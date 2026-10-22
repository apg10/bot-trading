import { normalizePreferences, DEFAULT_PREFERENCES, UiPreferences } from './uiPreferences';

const API_BASE = '/api/preferences';

let saveAbort: AbortController | null = null;
let saveTimer: ReturnType<typeof setTimeout> | null = null;
let currentRevision = 0;
const MAX_SAVE_RETRIES = 3;

export interface PreferencesLoadResult {
  preferences: string;
  revision: number;
  version: number;
}

/**
 * Load UI preferences from the backend.
 * Returns the normalized preferences (or defaults if unavailable/invalid).
 * Invalid stored evidence is preserved in memory for diagnostics.
 */
export async function loadPreferences(): Promise<{
  normalized: UiPreferences;
  rawText: string | null;
  parseError: string | null;
  revision: number;
}> {
  try {
    const response = await fetch(`${API_BASE}/ui`, { method: 'GET' });

    if (!response.ok) {
      if (response.status === 404) {
        // No preferences stored — return defaults with no raw text
        currentRevision = 0;
        return { normalized: DEFAULT_PREFERENCES, rawText: null, parseError: null, revision: 0 };
      }
      // Other HTTP error — try to get raw text for diagnostics
      const raw = await response.text().catch(() => null);
      return { normalized: DEFAULT_PREFERENCES, rawText: raw, parseError: `HTTP ${response.status}`, revision: currentRevision };
    }

    const data = await response.json() as PreferencesLoadResult;
    currentRevision = Number.isInteger(data.revision) && data.revision >= 0 ? data.revision : 0;
    const rawText = data.preferences ?? null;

    // Parse and normalize
    let parsed: unknown;
    let parseError: string | null = null;
    try {
      parsed = JSON.parse(rawText as string);
    } catch (e) {
      parseError = (e as Error).message;
      // Preserve invalid evidence — don't silently overwrite
      return { normalized: DEFAULT_PREFERENCES, rawText, parseError, revision: currentRevision };
    }

    const normalized = normalizePreferences(parsed);
    return { normalized, rawText, parseError: null, revision: currentRevision };
  } catch (e) {
    // Network error — preserve defaults
    return { normalized: DEFAULT_PREFERENCES, rawText: null, parseError: (e as Error).message, revision: currentRevision };
  }
}

/**
 * Save UI preferences to the backend.
 * Validates against the C1 contract before sending.
 */
export async function savePreferences(
  request: { preferences: UiPreferences; revision: number },
  signal?: AbortSignal,
): Promise<{
  ok: boolean;
  error: string | null;
  conflict: boolean;
  revision?: number;
}> {
  try {
    const requestInit: RequestInit = {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(request),
    };
    if (signal) requestInit.signal = signal;
    const response = await fetch(`${API_BASE}/ui`, requestInit);

    if (!response.ok) {
      const detail = await response.text().catch(() => null);
      return {
        ok: false,
        error: detail ?? `HTTP ${response.status}`,
        conflict: response.status === 409,
      };
    }

    const data = await response.json().catch(() => null) as { revision?: number } | null;
    const revision = data && Number.isInteger(data.revision) ? data.revision : request.revision + 1;
    return { ok: true, error: null, conflict: false, revision };
  } catch (e) {
    return { ok: false, error: (e as Error).message, conflict: false };
  }
}

/**
 * Debounce preference writes and cancel an in-flight older write when a newer
 * snapshot supersedes it.  The returned cleanup is safe to call after another
 * snapshot has already been scheduled.
 */
export function saveDebounced(preferences: UiPreferences): () => void {
  if (saveTimer !== null) {
    clearTimeout(saveTimer);
  }
  saveAbort?.abort();

  let activeController = new AbortController();
  saveAbort = activeController;
  const timer = setTimeout(async () => {
    if (saveAbort !== activeController) return;
    saveTimer = null;

    let result = await savePreferences(
      { preferences, revision: currentRevision },
      activeController.signal,
    );
    let retries = 0;
    while (result.conflict && retries < MAX_SAVE_RETRIES) {
      // The conflicted request is no longer useful.  Refresh the authoritative
      // revision, then use a fresh controller for the retry request.
      activeController.abort();
      if (saveAbort !== activeController) return;
      await loadPreferences();
      if (saveAbort !== activeController) return;
      retries += 1;
      activeController = new AbortController();
      saveAbort = activeController;
      result = await savePreferences(
        { preferences, revision: currentRevision },
        activeController.signal,
      );
    }

    if (saveAbort !== activeController) return;
    saveAbort = null;
    if (result.ok && result.revision !== undefined) {
      currentRevision = result.revision;
    }
    if (!result.ok && !activeController.signal.aborted) {
      console.error('Failed to save UI preferences:', result.error);
    }
  }, 500);
  saveTimer = timer;

  return () => {
    clearTimeout(timer);
    if (saveTimer === timer) saveTimer = null;
    if (saveAbort === activeController) {
      activeController.abort();
      saveAbort = null;
    }
  };
}
