# Registro de decisiones

> Índice revisado el 2026-10-21. No es una fuente de autoridad paralela: el [plan de acción raíz](../../../trading-bot-action-plan.md) decide estados y aceptaciones. Las notas de revisión enlazadas pueden conservar el estado que tenían cuando se escribieron.

## Decisiones e inventarios

| Tema / ID | Estado resumido | Consecuencia vigente | Fuente principal |
|---|---|---|---|
| DEC-3 — identidad temporal/procedencia de velas | Aceptada en su alcance de velas/procedencia | `open_time` identifica la vela; `timestamp_ms` legacy se conserva mediante adaptación. Esto no define una identidad reproducible del dataset ni garantiza replay. | [Plan raíz](../../../trading-bot-action-plan.md), [mapa de versión de datos](../../../qwen-task-act-c-001-data-version-map.txt) |
| DEC-4 — Proposal/Signal | Límite aceptado | La propuesta actual es TEST_ONLY/no ejecutable y permanece separada de N02; no crear órdenes a partir de ella. | [Plan raíz](../../../trading-bot-action-plan.md), [task de contrato Proposal/Signal](../../../qwen-task-act-c-001-proposal-contract-implementation.txt) |
| DEC-5 — contratos PAPER | Cerrada como inventario de contratos | Los DTOs PAPER son contract-only; no seleccionan una familia legacy ni implican store, lifecycle o ejecución. | [Plan raíz §14.17](../../../trading-bot-action-plan.md), [contrato PAPER Spot](../../../qwen-task-act-c-001-paper-orders-positions-contracts.txt) |
| DEC-6/DEC-7 — referencias estrategia/datos | Inventario aceptado y dirección conceptual acotada | `StrategyRef` es conceptualmente `{id, version}`; `schema_version` y `dataset_ref` son distintos. No se fijan formatos/hashes ni se garantiza replay sin datos inmutables. | [Revisión DEC-6/7](../../../act-c-001-strategy-data-version-review.md), [síntesis](../../../act-c-001-catalog-synthesis-review.md) |
| FYL / ACT-S-007 | Equivalencia original diferida; task pausado | Conservar el detector/evidencia con su alcance; no afirmar equivalencia con FYL original ni desbloquear ACT-E-001. | [Plan raíz §14.13](../../../trading-bot-action-plan.md), [revisión DEC-6/7](../../../act-c-001-strategy-data-version-review.md) |
| IndicatorDefinition | La ficha de decisión es histórica; consultar el plan actual | No usar esa nota para deducir el estado actual de ACT-C-001: su encabezado dice PENDIENTE, mientras la fila vigente del plan raíz registra ACT-C-001 COMPLETADO y el schema/catálogo existente. | [Fila vigente ACT-C-001](../../../trading-bot-action-plan.md), [ficha de decisión original](../../../act-c-001-indicator-definition-decision.md) |
| Apertura PAPER por cuenta | Decisión de alcance aceptada dentro de ACT-P-002; no tiene DEC-ID asignado | `PaperBalanceView.account_id` y `PaperOrder.paper_account_id` identifican la misma cuenta; balances account-scoped, no por sesión. La aceptación del slice no cierra ACT-P-002 global. | [Plan raíz, fila ACT-P-002](../../../trading-bot-action-plan.md) |
| ACT-P-003 — incertidumbre de persistencia local | Criterio aceptado; sin `UNKNOWN_STATE` | Para el flujo PAPER local actual, transacciones SQLite, journal, marcas durables, órdenes y reservas permiten reconocer/aplicar/fallar cerrado. ACT-P-003 global sigue PENDIENTE por sus criterios restantes. | [Plan raíz §14.23](../../../trading-bot-action-plan.md) |
| ACT-P-004 — riesgo y filtros | Política aprobada y task global ACEPTADO | Límites explícitos sin defaults, snapshots versionados, tamaño notional quote, exposición cuenta+quote y pérdida UTC; reject/fail-closed sin mutación/redondeo. Filtros y aceptación BUY son atómicos; no implica matching ni envío al exchange. | [Plan raíz §14.23-14.28](../../../trading-bot-action-plan.md), commit `2cbd689` |
| ACT-P-005 — semántica de fills | **TECHNICALLY_ACCEPTED** — semántica aprobada por el maestro 2026-10-21; evaluador puro aceptado (92 tests). Task global **PENDIENTE** hasta integración autorizada. | Side-quote bid/ask; spread no doble-contado; fee conocido en QUOTE por fill; `UNMODELLED` explícito no implica cero; capacidad parcial explícita; gap/intrabar ambiguo no produce fill. Sin tarifas/slippage numéricos. | [Plan raíz §14.29](../../../trading-bot-action-plan.md), [decisión](ACT-P-005-fill-policy-draft.md) |

## Decisiones pendientes / no inferibles

- La semántica ACT-P-005 está aprobada y el evaluador puro es **TECHNICALLY_ACCEPTED** (92 tests, commit `98c86b9`). **Globalmente PENDIENTE** hasta integración autorizada con allowlist y regression checks. No iniciar integración sin autorización explícita.
- ACT-P-002 y ACT-P-003 continúan globalmente `PENDIENTE`; consultar sus filas del plan raíz para los criterios aún no cerrados, sin invalidar los slices aceptados.
- El formato/versionado de datasets y su replay requieren datos subyacentes persistidos (ACT-L-001).
- La especificación del método FYL original permanece pendiente; ver la decisión de pausa en el plan raíz.
- Preferencias UI y metadata adicional solo se concretan en los contratos de sus tasks responsables.

## Regla de mantenimiento

Registrar solo decisiones aprobadas, con fecha, alcance y fuente. Las recomendaciones, prompts de Qwen y borradores se enlazan como evidencia/propuesta, no se elevan a decisión. Si una nota histórica contradice el plan raíz, conservarla como historia y corregir este índice; no reescribir la decisión histórica.

## Enlaces

- [[Inicio]] · [[Seguimiento]] · [[Mapa del proyecto]]
- [[Plantilla de decisión]]
