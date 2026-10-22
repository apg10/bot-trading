---
tipo: decision
decision_id: ACT-P-005-FILL-POLICY
estado: aceptada
fecha: 2026-10-21
owner: maestro
fuente_canonica: "../../../trading-bot-action-plan.md"
---

# ACT-P-005 — política reproducible de fills PAPER

> Semántica aprobada por el maestro el 2026-10-21; el task global ACT-P-005 sigue PENDIENTE hasta implementar y verificar sus slices. El plan raíz conserva estado y dependencias canónicos.

## Hechos verificados

- `PaperFill` registra un fill ya determinado (`qty`, `price`, timestamp y comisión nullable); no simula cómo se genera. Su aplicación contable es aceptada (§14.22–14.28): journal inmutable, reserva atómica, balances, lotes, commission known-in-QUOTE.
- La aplicación contable BUY/SELL, los balances, reservas y lotes tienen slices aceptados; consultar §14.22–14.28 del plan raíz.
- El coste de comisión conocido se contabiliza según las reglas ya aceptadas; los resultados actuales no se presentan como netos de spread/slippage no registrados.
- ACT-P-004 está ACEPTADO (fases 1–4, §14.24–14.28): validador de riesgo con política inmutable/versionada, filtros de símbolo explícitos, aceptación atómica con reservas dinámicas y regresión 1,784 passed.
- ACT-P-002 global permanece PENDIENTE; ACT-P-003 global permanece PENDIENTE. Los slices aceptados de P-002/003 satisfacen criterios específicos relevantes para ACT-P-005 (transacciones atómicas, reconciliación ante incertidumbre local), pero no cierran filas globales.
- `paper_reservations` es autoridad del remanente; snapshot de aceptación conserva términos originales (§14.22).
- El validador ACT-P-004 exige `risk_snapshot` con marks válidos y procedencia (§14.23); un snapshot sin precio válido para un activo requerido rechaza la entrada.
- La marca durable de aplicación por `fill_id` se consulta antes de modificar lotes; reintentar un fill ya aplicado no recrea lotes.
- `PaperFill.commission` es obligatorio pero nullable: `None` = coste desconocido; `Commission(amount=Decimal("0"), asset=...)` = fee cero conocido. La aplicación de fills exige commission conocida en QUOTE (§14.22, `sqlite_store.py` línea ~3066).
- Las transiciones válidas de PaperOrder están definidas en `TRANSITION_MATRIX` de `paper_contracts.py`; los estados terminales son `filled`, `cancelled`, `rejected`, `expired`.
- El gate de reconciliación local (ACT-P-003, §14.23) confirma que SQLite atestigua efectos contables compañeros vía transacción atómica; no se persiste `UNKNOWN_STATE` en el flujo PAPER local actual.

## Revisión del planeador

El primer borrador de Qwen no fue aceptado tal cual: incluía comisión cero y latencia cero por defecto, full fill sin volumen, fallback ask/mark y resultados intrabar contradictorios. El maestro aprobó la semántica corregida listada abajo. La matriz original se conserva solo como historial, no como contrato.

## Semántica aprobada por el maestro (2026-10-21)

1. **Elegibilidad y precio:** BUY usa ask; SELL usa bid. LIMIT solo es elegible si cruza la cotización (`ask <= limit` en BUY, `bid >= limit` en SELL); cruce más reserva activa y capacidad explícita son necesarios, pero el toque OHLC no garantiza fill. Sin referencia requerida, no fill; no fallback a mark/OHLC.
2. **Spread:** queda implícito en el precio bid/ask del fill y puede reportarse como `ask - bid`; no deducir un segundo cargo de spread al P&L.
3. **Comisión:** cada fill consume `Commission` conocida en QUOTE, explícita y suministrada por un snapshot/policy versionado para la cantidad exacta. Desconocida/ausente no produce fill; cero es válido solo si viene explícito como fee cero conocido. No derivar tasa ni tomarla de `PaperRiskPolicy`.
4. **Latencia/slippage:** el modo debe ser explícito. `UNMODELLED` permite usar bid/ask y etiqueta los resultados como no netos de latencia/slippage; no asigna cero. `MODELLED` solo con parámetros/snapshots versionados explícitos; sin ellos no afirma un coste cero.
5. **Gap:** cualquier barra esperada ausente según el intervalo explícito del snapshot (`next_open_time > prior_open_time + interval`) devuelve `FILL_GAP_UNCERTAIN` y no genera fill automático.
6. **Parciales:** cantidad de fill limitada por `min(remaining_qty, executable_capacity)` explícita. Sin capacidad/volumen, no fill; no completar implícitamente la orden.
7. **Colisión intrabar:** si stop y objetivo son alcanzables dentro de la misma barra y no hay orden temporal, devolver `FILL_AMBIGUOUS_INTRABAR` sin contabilizar ningún fill.

No se aprobaron valores operativos, tasas, costes o límites numéricos. Los fixtures de pruebas no son recomendaciones.

## Matriz original del handoff — referencias históricas, no aprobadas

| # | Decisión | Definición/unidad/ámbito que falta fijar | Datos/procedencia requeridos | Recomendación para aprobación (no decisión) |
|---|---|---|---|---|
| 1 | **Momento/condición de fill** — qué hace elegible a una orden LIMIT/MARKET para generar un fill. | Definir qué evento desencadea la generación de un fill simulado. Una condición de precio (toque de vela) no es suficiente por sí sola. Unidades: booleano `can_fill` por orden+barra. Ámbito: motor de simulación PAPER, separado de `StrategyConfig`. | Snapshot de mercado con OHLCV completo; estado de orden (`ACCEPTED`/`PARTIALLY_FILLED`); referencia de precio del snapshot. | **Recomendación:** definir un predicado explícito `fill_eligible(order, snapshot) → bool` que combine: (a) estado de orden `ACCEPTED` o `PARTIALLY_FILLED`, (b) precio de orden dentro del rango de la vela con dirección correcta para el side, (c) reserva activa disponible. Si falta cualquier dato del snapshot → resultado `NO_FILL_INSUFFICIENT_DATA`. No inventar condiciones de fill basadas solo en `OHLC[close]` touching `order.price`. |
| 2 | **Precio LIMIT/MARKET, bid/ask y spread** — qué referencia de precio determina un fill simulado, por side y tipo de orden; cómo se representa el spread. | Definir para cada combinación (side × order_type) qué campo del snapshot se usa como precio de referencia: `bid`, `ask`, `mark`, `last`, `open`, `high`, `low`, `close`. El spread debe tener una representación explícita en el snapshot o ser declarado ausente. | Snapshot de mercado con campos bid/ask/mark/trade explícitos o declaración de ausencia. | **Recomendación:** para BUY LIMIT usar `ask` (precio de contra-partida); para BUY MARKET usar `ask` o `mark` según disponibilidad; para SELL LIMIT usar `bid`; para SELL MARKET usar `bid` o `mark`. Si bid/ask/mark/trade están ausentes en el snapshot → resultado `NO_FILL_INSUFFICIENT_DATA`. El spread se representa como `ask − bid` cuando ambos están presentes; si no, se declara `SPREAD_UNKNOWN` y no se aplica a P&L. No estimar spread ni usar un valor fijo. |
| 3 | **Gaps, latencia y slippage** — tratamiento de huecos entre velas, latencia de simulación y slippage, sin proponer tasas numéricas. | Definir comportamiento cuando hay gaps (velas ausentes entre ticks), latencia entre generación y ejecución, y slippage entre precio de referencia y precio de fill. | Historial de velas con detección de gaps; ventana de latencia; precio de referencia vs. precio de ejecución. | **Recomendación:** cuando haya un gap detectable entre dos velas consecutivas (hueco > intervalo del timeframe), declarar `FILL_GAP_UNCERTAIN` y no generar fill automático. La latencia se modela como un delay fijo configurable (valor por defecto: 0 ticks, es decir, fill en la misma barra) — no se propone un valor específico. El slippage se declara `SLIPPAGE_UNKNOWN` por defecto; no se aplica a P&L hasta que se decida explícitamente. Si se decide modelar slippage futuro, debe ser un parámetro explícito de `StrategyConfig`, no un cálculo implícito. |
| 4 | **Fuente/tipo de comisión y unknown vs. zero** — origen de la comisión generada para fills simulados, activo y tratamiento de unknown vs. zero. | Definir qué commission se asigna a fills generados por la simulación PAPER. ¿Se usa una tasa fija? ¿Se deriva del snapshot? ¿Se deja como `None`? El activo debe ser QUOTE (ya exigido por aplicación). | Tasa/comisión del exchange o política configurable; activo resultante (debe ser QUOTE). | **Recomendación:** fills generados por simulación PAPER deben asignar `Commission(amount=Decimal("0"), asset=QUOTE)` como default explícito (fee cero conocido), no `None` (coste desconocido). Si la política de riesgo (ACT-P-004) incluye una tasa de comisión en la política, usarla. En ambos casos, el campo debe estar presente y no omitido. El activo DEBE ser QUOTE del símbolo, consistente con la aplicación aceptada. No proponer tasas numéricas; dejar como parámetro de política. |
| 5 | **Capacidad y cantidad de fills parciales** — capacidad de llenado parcial, cantidad máxima por partial y timing. | Definir cuándo un fill es parcial vs. completo, cuánto puede llenar un partial, y si hay límites de frecuencia/cantidad. | Estado de orden, cantidad restante (`remaining_qty`), snapshot de mercado. | **Recomendación:** un fill es parcial cuando `executed_qty < requested_qty` tras la generación y `executed_qty > 0`. La cantidad máxima de un partial es `min(remaining_qty, qty_from_snapshot)` donde `qty_from_snapshot` se deriva del volumen disponible en el snapshot (si presente) o se deja como `remaining_qty` (fill completo). No inventar volumen implícito. Si el snapshot no tiene volumen → solo se permite fill completo (no parcial). Los partials se acumulan en `fills` hasta `requested_qty`; `remaining_qty` es autoridad. |
| 6 | **Colisión intrabar stop/objetivo** — cuando OHLC permite que tanto stop como target se disparen en una misma barra pero no su orden, si el resultado es explícitamente ambiguo o sigue otra regla conservadora documentada. | Definir comportamiento cuando un mismo candle contiene precios que activan tanto stop_loss como take_profit. El orden de evaluación no está definido por OHLC. | Snapshot OHLC con `high >= trigger_take` y `low <= trigger_stop` (o viceversa según side). | **Recomendación:** cuando ambos stop y target son activables en la misma barra y el snapshot no especifica orden de ejecución intrabar → resultado `FILL_AMBIGUOUS_INTRABAR`. Registrar ambos eventos como fills parciales si la cantidad lo permite, o rechazar ambos si no se puede determinar prioridad. No inventar una regla de precedencia (stop-first, target-first, etc.) sin autorización del maestro. Esta es la decisión más crítica para evitar fills fabricados. |

## Dependencias de ACT-P-002/003 verificadas para ACT-P-005

| Criterio ACT-P-002/003 | Estado para ACT-P-005 | Observación |
|---|---|---|
| Transacciones atómicas SQLite | Satisfecho | `_apply_paper_fill_inner` y `_apply_paper_sell_fill_inner` operan dentro de transacciones (§14.22, §14.23). |
| Reconciliación ante incertidumbre local | Satisfecho para flujo actual | §14.23 confirma que la reconciliación es viable; no se necesita `UNKNOWN_STATE`. |
| Journal inmutable de PaperFill | Satisfecho | Schema v6+; gate 212 passed. El journal precede a la aplicación. |
| Aplicación atómica de fills | Satisfecho por slices | Schema v10 (BUY) y v11 (SELL); gate 332+1,680 passed. |
| Transiciones válidas de orden | Satisfecho | `TRANSITION_MATRIX` en `paper_contracts.py` define estados válidos. |
| ACT-P-002 global | **NO satisfecho** | Pendiente para resultados contables globales; no bloquea los criterios específicos arriba. |
| ACT-P-003 global | **NO satisfecho** | Pendiente para criterios globales; no bloquea los criterios específicos arriba. |
| ACT-P-004 (risk validator) | **ACEPTADO** | Política inmutable/versionada, filtros de símbolo, aceptación atómica, regresión 1,784 passed. |

## Recomendaciones generales (no decisiones)

- Mantener la política de fills como un módulo separada de `StrategyConfig` y de los contratos DTO.
- Usar snapshots de mercado inmutables y versionados como única fuente de precios para fills.
- Si falta cualquier dato requerido (bid/ask, volumen, marks), el resultado debe ser determinista: `NO_FILL_INSUFFICIENT_DATA` o `FILL_AMBIGUOUS_INTRABAR`, nunca estimación ni zero.
- No proponer valores numéricos (tasas, spread, slippage, thresholds); dejarlos como parámetros de política o fixtures de test.
- Las decisiones del maestro deben aprobarse todas juntas antes de definir el microtask de implementación.

## Límites

- No añadir porcentajes, tarifas, spread, slippage ni reglas de precedencia por iniciativa propia.
- Valores en tests son fixtures, no recomendaciones operativas.
- Si falta un dato requerido, proponer resultado explícito (no fill / incompleto / ambiguo) para aprobación; no estimarlo ni sustituirlo por cero.
- No cambiar DTOs, `StrategyConfig`, SQLite, fills, risk gates ni UI en esta nota.

## Próximo paso

Semántica aprobada el 2026-10-21. El evaluador puro es **TECHNICAMENTE_ACEPTADO** (92 tests, commit `98c86b9`). **Globalmente PENDIENTE** hasta integración autorizada con allowlist y regression checks. El siguiente microtask preparado es `ACT-P-005-FILL-SIMULATOR-INTEGRATION-001` — conectar el evaluador puro con PAPER accounting/persistence. Requiere autorización explícita del maestro.
