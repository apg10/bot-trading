# Seguimiento del proyecto

> Resumen de navegación revisado el 2026-10-22. Para cualquier decisión operativa, volver a consultar el [plan de acción canónico](../../trading-bot-action-plan.md).

## Semáforo

| Área | Estado global | Resumen y límite |
|---|---|---|
| ACT-P-001 | **PENDIENTE** | Núcleo backend de sesiones/configuraciones, referencias, experimentos, runs, resultados y disposiciones aceptado. Preferencias UI y metadata adicional siguen dependiendo de contratos; replay/datos subyacentes se difieren a ACT-L-001. |
| ACT-P-002 | **PENDIENTE** | Reconciliación local PASS: `ACT-P-002-LEDGER-RECON-001` probó en un escenario BUY→SELL apertura, aceptación, journal, fills, reservas, lots, comisiones, reopening e invariantes contables. El estado global sigue PENDIENTE; ver criterios restantes en el plan raíz. |
| ACT-P-003 | **PENDIENTE** | Reconciliación local PASS: `ACT-P-003-RECON-001` probó retry idempotente y reload durable ante incertidumbre de aceptación/cancelación, sin `UNKNOWN_STATE` persistido. El estado global sigue PENDIENTE; ver criterios restantes en el plan raíz. |
| ACT-P-004 | **ACEPTADO** | Riesgo puro, filtros versionados, aceptación atómica/concurrente y regresión backend aceptados. Commit publicado: `2cbd689` (`feat(paper): gate atomic acceptance with risk filters`). Backend: 1,784 passed, 0 failed, 0 skipped; 1 warning deprecado de Starlette/httpx2. PAPER sigue sin matching ni envío externo. |
| ACT-P-005 | **TECHNICAMENTE_ACEPTADO — globalmente ACEPTADO** | Evaluador puro (92 tests). Integración con persistencia y contabilidad PAPER aceptada técnicamente: `paper_execution_integration.py` (316 líneas, 4 revisiones cloud-reviewer PASS), 12 tests de integración, gate 1899 passed, 1 warning. DTO `PaperFillEvaluationRecord` y loader `load_paper_fill_evaluation()` incluidos en alcance PASS. Globalmente ACEPTADO tras cierre de ACT-P-006 (ciclo autónomo T1-T3 completo con REVIEWER PASS). |
 | ACT-P-006 | **COMPLETE — REVIEWER PASS** | T2: orquestador BUY (376 líneas, 5 tests). T3: ciclo BUY→SELL con LotAllocation explícita, revalidación y cierre posición (3 tests, 8 focales totales). Focal: 8 passed. Regresión: 1914 passed, 2 warnings. No implica rentabilidad ni preparación para trading en vivo. |
 | ACT-P-007-T1 | **COMPLETE — REVIEWER PASS** | DTOs lifecycle en `paper_lifecycle.py` (39 líneas). Migración v12→v13 con tablas `paper_lifecycle_states`/`paper_lifecycle_events`. Gate BUY en `_accept_paper_order_inner()` dentro de transacción `BEGIN IMMEDIATE`. 20 tests focales + 1926 regression passed, 0 failures. |
 | ACT-P-007-T2 | **COMPLETE — REVIEWER PASS** | DTOs con `stopped`/`stop`/`close`. Migración v13→v14 con rebuild atómico. Gate CLOSE con precondiciones transaccionales (no posiciones, no órdenes no terminales, no reservas). 29 tests focales + 1941 regression passed, 0 failures. |
| ACT-P-007-T3 | **COMPLETE — REVIEWER PASS** | DTO `PaperLifecycleReconciliationReport` inmutable. Schema v14→v15 con tabla `paper_lifecycle_reconciliations`. Detección de discrepancias: estado, órdenes, reservas, lotes. Persistencia append-only. 18 tests focales (12 en test_paper_reconciliation.py nuevo + 3 en lifecycle + 3 en persistence). Total: 68 tests T1+T2+T3 (13 nuevos en archivo nuevo, 55 en archivos existentes — algunos preexistentes). 1960 regression passed, 0 failures. P1 fix: report ID collision resuelto incluyendo scope en digest. **Migración:** v11→v15 fully supported — todos los caminos de upgrade verificados. |
| ACT-P-008 | **COMPLETE — T1+T2A+T2B REVIEWER PASS** | API read-only, cliente tipado y wiring UI PAPER para balances, posiciones, órdenes, fills, lifecycle y activity. 339/339 frontend tests, build clean y 1964/1964 backend regression. |
| ACT-P-009 | **COMPLETE — T1 REVIEWER PASS** | Aceptación E2E determinista del MVP PAPER; BUY→pause→SELL→close→reopen→reconcile→retry, sin duplicados. 1964/1964 backend regression. |
| ACT-M-003 | **COMPLETE — REVIEWER PASS** | Refresco técnico por revisión (no longitud) y descarte de respuestas antiguas. Gate focal: 133/133 passed. Regresión: 345/345 passed. Build: clean. |
| ACT-M-004 | **COMPLETE — REVIEWER PASS** | Selección de símbolo/intervalo con contrato de propiedad del motor. POST /api/market/reconfigure (serializado, atómico). Separación vista vs. motor. Cross-tab reconfiguration. Snapshot `complete` field. Backend: 165/165. Frontend: 348/348. Build: clean. |
| ACT-S-007 / ACT-E-001 | **PAUSADO / BLOQUEADO** | No afirmar equivalencia con FYL original sin especificación verificable. |
| Selección de trabajo | **NINGUNA — en espera de autorización** | No implementación seleccionada. Gate humano requerido antes de cambiar estado global. |

## Snapshot Git

- `master` y `origin/master` apuntan a `98c86b9` (`feat: ACT-P-005 fill-simulator-pure review-5 complete + docs and frontend updates`).
- Commit 98c86b9 integra: evaluador puro (1063 líneas), tests (1466 líneas), vault Obsidian completo, templates y actualizaciones frontend.
- El worktree conserva cambios locales tracked/untracked ajenos al commit; revisar `git status` antes de cualquier task.

Esta tabla es un resumen con fecha, no una segunda fuente de estados. La tabla completa, las dependencias y la evidencia detallada están en el plan raíz.

## ACT-P-008-T2B — UI Wiring (PAPER data into Terminal)
**Status:** COMPLETE — REVIEWER PASS
**Date:** 2026-10-22
**Files:** `frontend/src/hooks/usePaperData.ts` (NEW), `frontend/src/components/BottomBar.tsx` (MODIFIED), `frontend/src/App.tsx` (MODIFIED), `frontend/tests/paper-ui.test.ts` (NEW, 13 tests)
**Evidence:** 339/339 frontend tests pass, build clean, 1964/1964 backend regression
**Notes:** Contract fixes applied: projection mode for balances/positions/fills/activity, string→number lifecycle conversion, PAPER labeling.

## Inventario consolidado de versionado — trabajo completo no publicado

El inventario siguiente reúne únicamente los slices completados de schema/API/client/E2E/UI. Los cambios siguen locales: **no se ha hecho commit ni push de nada de este inventario**.

| Slice | Estado | Archivos cambiados | Evidencia |
|---|---|---|---|
| **BACKEND / schema v11→v15** — ACT-P-007 T1–T3 | COMPLETE — REVIEWER PASS | `backend/src/models/paper_lifecycle.py`; `backend/src/persistence/sqlite_store.py`; `backend/tests/test_paper_lifecycle.py`; `backend/tests/test_paper_orchestrator.py`; `backend/tests/test_persistence_lifecycle.py`; `backend/tests/test_paper_reconciliation.py` (NEW) | 68 focales; 1960/1960 backend regression; migraciones v11→v15 y caminos de upgrade verificados. |
| **BACKEND / API** — ACT-P-008 T1 | COMPLETE — REVIEWER PASS | `backend/src/api/main.py`; `backend/src/api/paper.py`; `backend/src/persistence/sqlite_store.py`; `backend/tests/test_sqlite_store_paper_queries.py`; `backend/tests/test_paper_read_api.py` | 6 endpoints read-only; 3 focales + 1964/1964 backend regression. |
| **FRONTEND / client** — ACT-P-008 T2A | COMPLETE — REVIEWER PASS | `frontend/src/api/paper.ts`; `frontend/tests/paper-api.test.ts` | 6 focales; typecheck limpio; preservación Decimal y validación de envelope/identidad/shape. |
| **BACKEND / E2E** — ACT-P-009 T1 | COMPLETE — REVIEWER PASS | `backend/tests/test_paper_e2e_acceptance.py` | 1 flujo focal completo; 6 endpoints verificados; 1964/1964 backend regression. |
| **FRONTEND / UI** — ACT-P-008 T2B | COMPLETE — REVIEWER PASS | `frontend/src/hooks/usePaperData.ts` (NEW); `frontend/src/components/BottomBar.tsx`; `frontend/src/App.tsx`; `frontend/tests/paper-ui.test.ts` (NEW) | 13 tests del slice; 339/339 frontend tests; build limpio; 1964/1964 backend regression. |

## Límites vigentes

- No presentar PAPER como un ciclo operativo ni inferir ejecución a partir de contratos, fills guardados o saldos abiertos.
- El store asocia y aplica fills PAPER a órdenes aceptadas; eso no implementa un motor de matching ni prueba un ciclo autónomo de ejecución.
- ACT-P-004 aprueba nuevas BUY con snapshots explícitos de riesgo/filtros y aceptación atómica; no estima datos/costes ausentes ni envía órdenes a un exchange.
- No conectar LIVE, servicios externos ni credenciales sin autorización explícita.
- Los resultados de pruebas copiados a planes son históricos; solo un comando ejecutado en el task actual acredita un gate actual.

## Antes de retomar

1. Leer el plan de acción y localizar el task/estado exacto.
2. ACT-P-005 evaluador puro es **TECHNICALLY_ACCEPTED** (92 tests); integración con persistencia aceptada técnicamente (12 tests integración, 1899 total). Globalmente **ACEPTADO** tras el cierre de ACT-P-006 (REVIEWER PASS).
3. Confirmar autorización, dependencias por criterio, propiedad de cambios y allowlist sobre el árbol actual.
4. Si falta una decisión o permiso, parar como `BLOCKED`; no inferir el siguiente paso.
5. Registrar gates reales, limitaciones y el handoff; detenerse al cerrar.

## Índices relacionados

- [[Inicio]] · [[Mapa del proyecto]]
- [[Decisiones/Registro de decisiones]]
- [[Handoffs/Registro de handoffs]]
- [Backlog y evidencias completos](../../trading-bot-action-plan.md)
- [Arquitectura y requisitos](../../trading-bot-development-plan.md)
