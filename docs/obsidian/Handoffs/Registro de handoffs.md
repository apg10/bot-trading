# Registro de handoffs

> Índice, no sustituto de la evidencia. El [plan de acción raíz](../../../trading-bot-action-plan.md) conserva el estado canónico de cada task, sus dependencias y las aceptaciones. Snapshot de navegación: 2026-10-21.

## Estado actual

- ACT-P-004 está `ACEPTADO` tras sus cuatro fases; commit publicado `2cbd689` y regresión backend 1,784 passed, 0 failed, 0 skipped, 1 warning.
- ACT-P-001/002/003 globales siguen `PENDIENTE`; la aceptación de P-004 no los cierra.
- ACT-P-005-DECISION-MAP-001 fue revisado y su semántica corregida quedó aprobada en §14.29. Siguiente task preparado, no ejecutado: [[ACT-P-005-fill-simulator-pure-task]] (solo DTO/evaluación pura, sin SQLite).
- No encontré un directorio de handoffs independientes con nombres/IDs de handoff en la raíz. El plan de acción y sus secciones de recepción son el registro operativo actual.

## Cómo interpretar los artefactos de raíz

| Artefacto | Qué es | Qué no es |
|---|---|---|
| `trading-bot-action-plan.md` | Estado, dependencias y evidencia de cierre vigente. | No se reemplaza por este índice. |
| `qwen-task-*.txt` | Prompt/contrato de trabajo; algunos conservan instrucciones o resultados históricos. | El nombre o texto del prompt no acredita por sí solo entrega o aceptación. |
| `qwen-batch-*.txt` | Archivo de ejecución por lotes con secuencia, baseline y checks descritos. | No es el selector de tareas vigente ni prueba actual de gates. |
| `act-*-review.md` | Revisión o evidencia temática, algunas con estado fechado. | No prevalece sobre una ficha posterior del plan raíz. |
| `trading-plans-2026-10-07/` | Copia fechada histórica. | No tiene autoridad sobre el plan de acción de raíz. |

## Índice de handoffs/evidencia vigente

| Task / área | Estado que muestra el plan | Límite para retomar |
|---|---|---|
| ACT-P-001 — persistencia backend | Núcleo aceptado; task global `PENDIENTE`. | Preferencias UI/metadata y replay siguen en ámbitos/contratos pendientes. |
| ACT-P-002 — PAPER ledger | Journal, balances, reservas y fills BUY/SELL tienen slices aceptados; task global `PENDIENTE`. | Ver criterios globales restantes en la fila raíz; no es un ciclo autónomo. |
| ACT-P-003 — lifecycle PAPER | Registro, aceptación/cancelación e idempotencia local aceptados por slices; task global `PENDIENTE`. | Criterio de reconciliación local aceptado sin introducir `UNKNOWN_STATE`; ver §14.23. |
| ACT-P-004 — riesgo/filtros/admisión | `ACEPTADO`, publicado en `2cbd689`; regresión backend 1,784 passed. | No matching, ejecución externa ni simulación de fills; esas políticas pasan a ACT-P-005/006. |
| ACT-P-005-DECISION-MAP-001 | Decisiones agrupadas aprobadas en §14.29. | El primer borrador Qwen con comisión/latencia cero, full fill sin volumen y salida intrabar ambigua fue corregido; ver matriz aprobada. |
| ACT-P-005-FILL-SIMULATOR-PURE-001 | Rework-003 revisado; gate 79 passed; `REQUIERE_CAMBIOS`. | Capacity con símbolo no correspondiente y reservation sin account_id aún pueden aprobarse; falta identidad bar/protection y test real de quote ausente. El plan raíz §14.35 registra el handoff; correcciones en [[ACT-P-005-fill-simulator-pure-review-5]], integración detenida. |
| ACT-C-001 — contratos | `COMPLETADO` según fila raíz. | Contratos no equivalen a integración API/UI/runtime ni ejecución. |
| ACT-S-007 / ACT-E-001 | `PAUSADO` / `BLOQUEADO`. | No afirmar equivalencia FYL original. |

Para las cifras de tests, hashes, allowlists y límites de cada slice, abrir la fila y sección citada en el [plan raíz](../../../trading-bot-action-plan.md); no copiar cifras aquí como si fueran gates recién ejecutados.

## Añadir un handoff nuevo

Usar [[Plantilla de handoff]]. Mantener un archivo por entrega revisable; enlazar el artefacto fuente y preservar sus hashes. Un handoff `EN_REVISION` no es aceptación. No completar automáticamente la sección “siguiente task”.

## Enlaces

- [[Inicio]] · [[Seguimiento]] · [[Decisiones/Registro de decisiones]]
- [Batch de integridad temporal](../../../qwen-batch-backend-timestamp-integrity-001.txt) — archivo histórico.
- [Batch FYL/reconexión](../../../qwen-batch-backend-fyl-reconnect-001.txt) — archivo histórico; no reabre FYL.
- [Plan de acción y evidencias](../../../trading-bot-action-plan.md)
