export type ActiveView = 'terminal' | 'laboratorio' | 'bot';
export type BottomTab = 'balances' | 'positions' | 'orders' | 'fills' | 'activity';
export type Interval = '1s' | '1m' | '3m' | '5m' | '15m' | '30m' | '1h' | '2h' | '4h' | '6h' | '8h' | '12h' | '1d' | '3d' | '1w';

export interface UiPreferencesMetadata {
  lastSaved: string;
  schemaVersion: '1';
}

export interface UiPreferencesView {
  activeView: ActiveView;
  bottomTab: BottomTab;
  interval: Interval;
}

export interface UiPreferences {
  schemaVersion: '1';
  view: UiPreferencesView;
  metadata: UiPreferencesMetadata;
}

export const DEFAULT_ACTIVE_VIEW: ActiveView = 'terminal';
export const DEFAULT_BOTTOM_TAB: BottomTab = 'balances';
export const DEFAULT_INTERVAL: Interval = '1m';

const freshDefaults = (): UiPreferences => ({
  schemaVersion: '1',
  view: {
    activeView: DEFAULT_ACTIVE_VIEW,
    bottomTab: DEFAULT_BOTTOM_TAB,
    interval: DEFAULT_INTERVAL,
  },
  metadata: {
    lastSaved: new Date().toISOString(),
    schemaVersion: '1',
  },
});

export const DEFAULT_PREFERENCES: UiPreferences = freshDefaults();

const ACTIVE_VIEWS: ActiveView[] = ['terminal', 'laboratorio', 'bot'];
const BOTTOM_TABS: BottomTab[] = ['balances', 'positions', 'orders', 'fills', 'activity'];
const INTERVALS: Interval[] = ['1s', '1m', '3m', '5m', '15m', '30m', '1h', '2h', '4h', '6h', '8h', '12h', '1d', '3d', '1w'];

function isValidActiveView(v: unknown): v is ActiveView {
  return typeof v === 'string' && ACTIVE_VIEWS.includes(v as ActiveView);
}

function isValidBottomTab(v: unknown): v is BottomTab {
  return typeof v === 'string' && BOTTOM_TABS.includes(v as BottomTab);
}

function isValidInterval(v: unknown): v is Interval {
  return typeof v === 'string' && INTERVALS.includes(v as Interval);
}

function isValidISO8601(s: unknown): boolean {
  if (typeof s !== 'string') return false;
  const d = new Date(s);
  return !isNaN(d.getTime()) && s.match(/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}/) !== null;
}

/**
 * Normalize a partial or raw preferences object into a complete UiPreferences.
 * Unknown fields are ignored. Invalid enum values fall back to defaults.
 * Missing schemaVersion or mismatched version causes full default fallback.
 */
export function normalizePreferences(raw: unknown): UiPreferences {
  if (raw == null || typeof raw !== 'object' || Array.isArray(raw)) {
    return freshDefaults();
  }

  const obj = raw as Record<string, unknown>;
  const sv = obj.schemaVersion;
  if (sv !== '1') {
    return freshDefaults();
  }

  const view = obj.view;
  if (view == null || typeof view !== 'object' || Array.isArray(view)) {
    return freshDefaults();
  }

  const viewObj = view as Record<string, unknown>;
  const activeView = isValidActiveView(viewObj.activeView)
    ? viewObj.activeView as ActiveView
    : DEFAULT_ACTIVE_VIEW;
  const bottomTab = isValidBottomTab(viewObj.bottomTab)
    ? viewObj.bottomTab as BottomTab
    : DEFAULT_BOTTOM_TAB;
  const interval = isValidInterval(viewObj.interval)
    ? viewObj.interval as Interval
    : DEFAULT_INTERVAL;

  const metadata = obj.metadata;
  const metadataObj = metadata != null && typeof metadata === 'object' && !Array.isArray(metadata)
    ? metadata as Record<string, unknown>
    : undefined;

  return {
    schemaVersion: '1',
    view: { activeView, bottomTab, interval },
    metadata: {
      lastSaved: metadataObj && isValidISO8601(metadataObj.lastSaved)
        ? (metadataObj.lastSaved as string)
        : new Date().toISOString(),
      schemaVersion: '1',
    },
  };
}
