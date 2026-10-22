# Seguimiento del proyecto

> Resumen de navegación revisado el 2026-10-21. Para cualquier decisión operativa, volver a consultar el [plan de acción canónico](../../trading-bot-action-plan.md).

## Semáforo

| Área | Estado global | Resumen y límite |
|---|---|---|
| ACT-P-001 | **PENDIENTE** | Núcleo backend de sesiones/configuraciones, referencias, experimentos, runs, resultados y disposiciones aceptado. Preferencias UI y metadata adicional siguen dependiendo de contratos; replay/datos subyacentes se difieren a ACT-L-001. |
| ACT-P-002 | **PENDIENTE** | Globalmente sigue pendiente. Slices aceptados incluyen journal, apertura de cuenta, aplicación contable de fills BUY/SELL, reservas y accounting; ver criterios restantes en el plan raíz. No equivale a un ciclo PAPER autónomo. |
| ACT-P-003 | **PENDIENTE** | Slices de registro/aceptación/cancelación e IDs idempotentes aceptados. El criterio de incertidumbre de persistencia local está satisfecho; no se añadió `UNKNOWN_STATE`. El estado global sigue pendiente por criterios restantes del plan. |
| ACT-P-004 | **ACEPTADO** | Riesgo puro, filtros versionados, aceptación atómica/concurrente y regresión backend aceptados. Commit publicado: `2cbd689` (`feat(paper): gate atomic acceptance with risk filters`). Backend: 1,784 passed, 0 failed, 0 skipped; 1 warning deprecado de Starlette/httpx2. PAPER sigue sin matching ni envío externo. |
| ACT-P-005 | **PENDIENTE — handoff pure requiere cambios** | Semántica aprobada en §14.29; gate 79 passed, pero una capacity de otro símbolo y una reservation sin account_id pueden autorizar fill. Rework documentado en §14.35 y [[Handoffs/ACT-P-005-fill-simulator-pure-review-5]]; no integrar SQLite todavía. |
| ACT-S-007 / ACT-E-001 | **PAUSADO / BLOQUEADO** | No afirmar equivalencia con FYL original sin especificación verificable. |
| Selección de trabajo | **ACT-P-005-FILL-SIMULATOR-PURE-001 — REQUIERE_CAMBIOS** | Gate 79 passed; revisión reprodujo capacity con symbol distinto y reservation sin cuenta aceptadas. Además falta identidad exacta de bar/protection y el test de quote ausente sigue usando quotes válidos. Ver [[Handoffs/ACT-P-005-fill-simulator-pure-review-5]] antes de integración. |

## Snapshot Git

- `master` y `origin/master` apuntan a `2cbd689117469d3fa7a1093c066b245336dc8653`.
- El worktree conserva cambios locales tracked/untracked ajenos al commit (plan raíz, pruebas, frontend y notas/tareas); no está limpio. Preservarlos y revisar `git status` antes de cualquier task.

Esta tabla es un resumen con fecha, no una segunda fuente de estados. La tabla completa, las dependencias y la evidencia detallada están en el plan raíz.

## Límites vigentes

- No presentar PAPER como un ciclo operativo ni inferir ejecución a partir de contratos, fills guardados o saldos abiertos.
- El store asocia y aplica fills PAPER a órdenes aceptadas; eso no implementa un motor de matching ni prueba un ciclo autónomo de ejecución.
- ACT-P-004 aprueba nuevas BUY con snapshots explícitos de riesgo/filtros y aceptación atómica; no estima datos/costes ausentes ni envía órdenes a un exchange.
- No conectar LIVE, servicios externos ni credenciales sin autorización explícita.
- Los resultados de pruebas copiados a planes son históricos; solo un comando ejecutado en el task actual acredita un gate actual.

## Antes de retomar

1. Leer el plan de acción y localizar el task/estado exacto.
2. El contrato semántico ACT-P-005 está aprobado en §14.29; consultar y ejecutar manualmente [[Handoffs/ACT-P-005-fill-simulator-pure-task]]. No solicitar valores operativos para DTOs/validadores; las cifras de tests son fixtures.
3. Confirmar autorización, dependencias por criterio, propiedad de cambios y allowlist sobre el árbol actual.
4. Si falta una decisión o permiso, parar como `BLOCKED`; no inferir el siguiente paso.
5. Registrar gates reales, limitaciones y el handoff; detenerse al cerrar.

## Índices relacionados

- [[Inicio]] · [[Mapa del proyecto]]
- [[Decisiones/Registro de decisiones]]
- [[Handoffs/Registro de handoffs]]
- [Backlog y evidencias completos](../../trading-bot-action-plan.md)
- [Arquitectura y requisitos](../../trading-bot-development-plan.md)
