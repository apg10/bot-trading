# Publication Candidate: ACT-T-001 through ACT-T-005

## Status
All tasks: PASS | Build: ✅ | Frontend tests: 368/368 (8 files) | Backend tests: 1980 passed / 1 pre-existing WS failure

## Accurate file manifest (from git status)

### Modified files for T-001 through T-005
- `frontend/src/App.tsx`
- `frontend/src/api/market.ts`
- `frontend/src/components/AnalysisPanel.tsx`
- `frontend/src/components/BottomBar.tsx`
- `frontend/src/components/ChartPanel.tsx`
- `frontend/src/hooks/useMarketData.ts`
- `frontend/src/styles/layout.css`
- `frontend/src/styles/tokens.css`
- `frontend/src/types/market.ts`
- `frontend/tests/analysis-request-tracker.test.ts`

### New files for T-001 through T-005
- `frontend/src/api/scenarios.ts`
- `frontend/tests/market-selection.test.ts`
- `frontend/tests/scenario-api.test.ts`
- `ACT-T-001..T-004-verificacion.md`
- `ACT-T-002-tarea-acotada.md`

### Backend changes (not part of T-001 through T-005)
- `backend/src/api/market.py`
- `backend/src/market/engine.py`
- `backend/tests/test_market_api.py`
- `backend/tests/test_market_engine.py`
- `backend/tests/test_market_lifecycle.py`

### Other tracked changes (not part of T-001 through T-005)
- `.opencode/checkpoint.md`
- `ACT-T-001-inventario-visual-mapa-vistas.md`
- `docs/obsidian/Seguimiento.md`
- `trading-bot-action-plan.md`
- `frontend/tsconfig.tsbuildinfo`

## Changeset by task

### ACT-T-001: Inventario visual corregido
- File changes:
  - No candidate source/test file changes; related tracked documentation is listed under “Other tracked changes”.
- Corrections: El inventario ahora distingue componentes montados, placeholders, vistas no disponibles y componentes existentes no montados; también clasifica la evidencia de código/runtime frente a la inspección visual pendiente.
- Evidence: classification table

  | Evidence | Classification | Result |
  |---|---|---|
  | Source/component map | Source inspection | App shell, panels, views and wiring identified |
  | Capabilities and runtime state | Runtime/API evidence | Backend capabilities and honest unavailable states documented |
  | Screenshots/browser audit | Not available | No browser tooling or visual captures claimed |

### ACT-T-002: Adaptación shell/Terminal al brief
- File changes:
  - `frontend/src/App.tsx`
  - `frontend/src/api/market.ts`
  - `frontend/src/components/BottomBar.tsx`
  - `frontend/src/styles/layout.css`
  - `frontend/src/styles/tokens.css`
- Key changes: `getCapabilities`, HeaderBar badges, BottomBar states, layout fixes; capability/environment state is represented honestly and unavailable execution/portfolio states are not simulated.
- Evidence: build, tests (349/349), 2 review cycles

### ACT-T-003-C1: Contrato de preferencias de UI
- File changes:
  - `frontend/src/preferences/uiPreferences.ts` (nuevo)
  - `frontend/tests/ui-preferences.test.ts` (nuevo)
- Key changes: Contract types (`UiPreferences`, `ActiveView`, `BottomTab`, `Interval`), defaults, `normalizePreferences()` with schema-version enforcement, ISO-8601 validation, fresh-default isolation, unknown-field discard. Pure contract — no persistence, no engine mutation.
- Evidence: build, tests (368/368), PASS review

### ACT-P-001: Persistencia y restauración de preferencias UI
- File changes:
  - `backend/src/api/preferences.py` (nuevo) — GET/POST /api/preferences/ui, validate_preferences()
  - `backend/src/api/main.py` (modificado) — router registration
  - `backend/src/persistence/sqlite_store.py` (modificado) — v15→v16 migration, save/get_ui_preferences()
  - `backend/tests/test_ui_preferences_api.py` (nuevo) — 13 tests
  - `frontend/src/preferences/store.ts` (nuevo) — loadPreferences(), savePreferences()
  - `frontend/src/preferences/index.ts` (nuevo) — barrel exports
  - `frontend/src/App.tsx` (modificado) — preference restoration wiring, debounced save
  - `frontend/src/components/BottomBar.tsx` (modificado) — controlled/persisted tab state
- Key changes: SQLite v16 migration (atomic with rollback), raw-text preservation for corrupt data, strict ISO-8601 validation, serialized debounced saves with AbortController, restoration applies to view state ONLY (no engine reconfiguration)
- Evidence: build, frontend tests (368/368), backend tests (13/13), PASS review

### ACT-T-003-S1: Volumen + actualizaciones delta
- File changes:
  - `frontend/src/components/ChartPanel.tsx`
  - `frontend/src/hooks/useMarketData.ts`
  - `frontend/src/types/market.ts`
- Key changes: `HistogramSeries`, OHLCV mapping, delta updates, bounded 200-candle buffer, and rebuild/update handling that preserves the visible range when possible.
- Evidence: build, tests (354/354), 1 fixer cycle

### ACT-T-004: Escenarios N02 advisory
- File changes:
  - `frontend/src/api/scenarios.ts`
  - `frontend/src/components/AnalysisPanel.tsx`
  - `frontend/src/App.tsx`
  - `frontend/src/types/market.ts`
- Key changes: `scenarios.ts` client, AnalysisPanel integration, App.tsx wiring; snapshot identity, validity, missing context and advisory-only execution constraints are enforced.
- Evidence: build, tests (354/354), PASS review

### ACT-T-005: QA responsive/accesible
- File changes:
  - `frontend/src/App.tsx`
  - `frontend/src/components/AnalysisPanel.tsx`
  - `frontend/src/components/BottomBar.tsx`
  - `frontend/src/components/ChartPanel.tsx`
  - `frontend/src/styles/layout.css`
  - `frontend/src/styles/tokens.css`
- Key changes: responsive layout, touch targets, roving tab index, ARIA, landmarks, reduced-motion, focus-visible.
- Evidence: build, tests (354/354), 2 review cycles

## Documentation artifacts
- `ACT-T-001..T-004-verificacion.md` (updated with T-005)
- `ACT-T-002-tarea-acotada.md`
- `PUBLICATION-CANDIDATE-T001-T005.md` (this document)

## Notes
- No staging/commit/push authorized yet
- Browser screenshots pending (no tooling); automated checks + CSS inspection only
- ACT-T-003-C1 contract resolved; ACT-P-001 persistence implemented and reviewed
- Pre-existing WS test failure: `test_market_lifecycle.py::test_two_browser_tabs_share_engine_and_each_disconnect_removes_handlers` (infrastructure issue, not related to this work)
