# Seguimiento del proyecto

> Resumen de navegación revisado el 2026-10-21. Para cualquier decisión operativa, volver a consultar el [plan de acción canónico](../../trading-bot-action-plan.md).

## Semáforo

| Área | Estado global | Resumen y límite |
|---|---|---|
| ACT-P-001 | **PENDIENTE** | Núcleo backend de sesiones/configuraciones, referencias, experimentos, runs, resultados y disposiciones aceptado. Preferencias UI y metadata adicional siguen dependiendo de contratos; replay/datos subyacentes se difieren a ACT-L-001. |
| ACT-P-002 | **PENDIENTE** | Globalmente sigue pendiente. Slices aceptados incluyen journal, apertura de cuenta, aplicación contable de fills BUY/SELL, reservas y accounting; ver criterios restantes en el plan raíz. No equivale a un ciclo PAPER autónomo. |
| ACT-P-003 | **PENDIENTE** | Slices de registro/aceptación/cancelación e IDs idempotentes aceptados. El criterio de incertidumbre de persistencia local está satisfecho; no se añadió `UNKNOWN_STATE`. El estado global sigue pendiente por criterios restantes del plan. |
| ACT-P-004 | **ACEPTADO** | Riesgo puro, filtros versionados, aceptación atómica/concurrente y regresión backend aceptados. Commit publicado: `2cbd689` (`feat(paper): gate atomic acceptance with risk filters`). Backend: 1,784 passed, 0 failed, 0 skipped; 1 warning deprecado de Starlette/httpx2. PAPER sigue sin matching ni envío externo. |
| ACT-P-005 | **TECHNICALLY_ACCEPTED — globalmente PENDIENTE** | Evaluador puro aceptado técnicamente; gate 92 passed. Integración SQLite pendiente de autorización explícita. No iniciar trabajo de integración sin autorización. |
| ACT-S-007 / ACT-E-001 | **PAUSADO / BLOQUEADO** | No afirmar equivalencia con FYL original sin especificación verificable. |
| Selección de trabajo | **NINGUNA — en espera de autorización** | ACT-P-005 puramente aceptado; globalmente PENDING. ACT-P-002/P-003 pendientes. Proxima tarea autorizada: ACT-P-005 SQLite integration con allowlist explícita. |

## Snapshot Git

- `master` y `origin/master` apuntan a `98c86b9` (`feat: ACT-P-005 fill-simulator-pure review-5 complete + docs and frontend updates`).
- Commit 98c86b9 integra: evaluador puro (1063 líneas), tests (1466 líneas), vault Obsidian completo, templates y actualizaciones frontend.
- El worktree conserva cambios locales tracked/untracked ajenos al commit; revisar `git status` antes de cualquier task.

Esta tabla es un resumen con fecha, no una segunda fuente de estados. La tabla completa, las dependencias y la evidencia detallada están en el plan raíz.

## Límites vigentes

- No presentar PAPER como un ciclo operativo ni inferir ejecución a partir de contratos, fills guardados o saldos abiertos.
- El store asocia y aplica fills PAPER a órdenes aceptadas; eso no implementa un motor de matching ni prueba un ciclo autónomo de ejecución.
- ACT-P-004 aprueba nuevas BUY con snapshots explícitos de riesgo/filtros y aceptación atómica; no estima datos/costes ausentes ni envía órdenes a un exchange.
- No conectar LIVE, servicios externos ni credenciales sin autorización explícita.
- Los resultados de pruebas copiados a planes son históricos; solo un comando ejecutado en el task actual acredita un gate actual.

## Antes de retomar

1. Leer el plan de acción y localizar el task/estado exacto.
2. ACT-P-005 evaluador puro es **TECHNICALLY_ACCEPTED** (92 tests); globalmente **PENDIENTE** hasta integración autorizada.
3. Confirmar autorización, dependencias por criterio, propiedad de cambios y allowlist sobre el árbol actual.
4. Si falta una decisión o permiso, parar como `BLOCKED`; no inferir el siguiente paso.
5. Registrar gates reales, limitaciones y el handoff; detenerse al cerrar.

## Índices relacionados

- [[Inicio]] · [[Mapa del proyecto]]
- [[Decisiones/Registro de decisiones]]
- [[Handoffs/Registro de handoffs]]
- [Backlog y evidencias completos](../../trading-bot-action-plan.md)
- [Arquitectura y requisitos](../../trading-bot-development-plan.md)
