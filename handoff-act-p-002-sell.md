# Handoff — ACT-P-002 PAPER SELL

**Estado del slice:** ACEPTADO funcionalmente por revisión humana en esta conversación
**ACT-P-002 global:** PENDIENTE
**Fecha de reconciliación:** 2026-10-20
**Schema:** v11
**Alcance de esta reconciliación:** documentación y verificación; sin cambios de producción

## Origen y dictamen

No existía un task SELL definitivo previo: `qwen-task-act-p-002-buy-fill-application.txt` es BUY-only y excluye SELL; `handoff-fase2.md` es la historia del slice BUY y también excluye SELL/cancelaciones. Este handoff reconstruye el alcance y la evidencia de la implementación ya presente; no afirma que se ejecutara un task SELL histórico.

El dictamen funcional previo sí está en esta conversación: el revisor aceptó el microtask SELL después de revisar la implementación y verificar la suite backend completa. Esta reconciliación conserva ese dictamen, precisa su alcance y deja ACT-P-002 global PENDIENTE.

## Fases reconstruidas del trabajo existente

| Fase | Alcance | Estado y evidencia |
|---|---|---|
| 1. Schema, modelos y migraciones v11 | Modelos internos, `paper_reservation_lot_allocations`, `paper_sell_accounting`; upgrades atómicos y rollback, preservando datos. | Implementado. Cubierto por suites v11 de modelos/migración e integración en el gate completo. |
| 2. Aceptación y reservas | SELL LIMIT/MARKET reserva BASE y asignaciones explícitas por lote; validar cuenta, sesión, símbolo, estrategia, cantidad y saldo libre; mutación atómica. | Implementado y cubierto por la suite SELL, acceptance, orders y balances del gate completo. |
| 3. Aplicación de fills y accounting | Parciales/completos; balances, reservas y lotes atómicos; comisión de salida conocida solo en QUOTE; accounting por fill. | Implementado y cubierto por pruebas SELL y regresión BUY/fills del gate completo. |
| 4. Cancelación, integridad y cierre | Cancelar órdenes ACCEPTED/PARTIALLY_FILLED, liberar solo el remanente; preservar fills/accounting; idempotencia, terminales, concurrencia y rollback. | Implementado. Gate backend completo: **1,680 passed, 1 warning, 0 failed**. También `git diff --check` limpio. |

## Decisiones aprobadas que delimitan el slice

- No cambiar DTOs públicos ni la semántica BUY aceptada. Una orden SELL requiere lot allocations explícitas y exactas; no puede reservar cantidades ya reservadas o ajenas al contexto de cuenta/sesión/símbolo/estrategia.
- El activo de reserva SELL es BASE. LIMIT SELL no acepta fills por debajo del límite. MARKET SELL no usa presupuesto/cap QUOTE: reserva BASE por cantidad.
- `PositionLot.total_qty` y `total_cost` son autoridades. En una venta parcial, se calcula primero el producto exacto `coste_vigente × qty_vendida`, después se divide por `qty_vigente` bajo el contexto explícito aprobado de 34 cifras `ROUND_HALF_EVEN`; no se multiplica por un promedio previamente redondeado. En el cierre se retira exactamente el coste residual y se elimina el lote agotado.
- **Corrección de prorrateo ACEPTADA:** regresión integrada con coste 1, cantidad vigente 3 y venta parcial 2. Bajo contexto global `prec=6`/`ROUND_DOWN`, el coste retirado es exactamente `0.6666666666666666666666666666666667`, el remanente `0.3333333333333333333333333333333333`, y la venta final consume ese residual; ambos costes suman exactamente 1. La regresión falló antes de la corrección con `...6666` y pasa después. La rama de cierre y los guards de coste existentes se conservaron.
- **Guard de coste residual SELL ACEPTADO:** parciales que dejan `remaining_qty > 0` y `remaining_cost <= 0` se rechazan sin mutaciones; el fill continúa en `paper_fills`, sin application ni accounting. La regresión reproduce qty 1/coste 40/vendido `0.9999999999999999999999999999999999999999`, comprueba lote, reservas, balance y orden intactos. El cierre total mantiene el residual exacto. Pruebas focales: 30 passed; regresión SQLite SELL/fills/orders: 82 passed.
- La comisión de salida debe ser conocida y estar en QUOTE. Se registran proceeds brutos y proceeds después de esa comisión, además del resultado después de la comisión. Ese resultado **no es neto completo**: no incorpora spread, slippage ni otros costes no modelados.
- Cancelar una orden SELL aceptada/parcial libera únicamente la reserva BASE y por lote remanente; no deshace fills, costes ni accounting ya aplicado. `FILLED`, `REJECTED` y `EXPIRED` no se cancelan; repetir una cancelación efectiva es idempotente.
- La marca durable de aplicación por `fill_id` se consulta antes de modificar lotes. Comprobación temporal: tras cerrar por SELL un lote BUY, reintentar el mismo fill BUY ya aplicado retorna sin recrear `position_lots` (**PASS**; smoke check no persistente).
- Excluido: OCO, `UNKNOWN_STATE`, colas, expiración, matching, ejecución externa, riesgo y comisión en otro activo.

## Gate final reproducido

Desde `backend/`:

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONDONTWRITEBYTECODE=1 \
  .venv/bin/python -B -m pytest -q -p no:cacheprovider --capture=sys tests/
```

**Resultado:** `1,680 passed, 1 warning` en 10.16s. Único warning: `StarletteDeprecationWarning` de `starlette.testclient`/httpx; no relacionado con SELL. `git diff --check`: limpio.

## Allowlist reconstruida del slice

- Implementación: `backend/src/persistence/sqlite_store.py`.
- Tests focales/versionados: `backend/tests/test_sqlite_store_paper_sell_fills.py`, `backend/tests/test_sqlite_store_v11_models.py`, `backend/tests/test_sqlite_store_v11_migration.py`.
- Compatibilidad/regresión: `backend/tests/test_sqlite_store_paper_acceptance.py`, `backend/tests/test_sqlite_store_paper_balances.py`, `backend/tests/test_sqlite_store_paper_fills.py`, `backend/tests/test_sqlite_store_paper_orders.py`, `backend/tests/test_sqlite_store.py`, `backend/tests/test_persistence_lifecycle.py`; suite BUY como regresión, preservando su comportamiento aceptado.
- Registro: solo la fila ACT-P-002 de `trading-bot-action-plan.md` y este handoff. No editar modelos/DTOs públicos, API ni frontend.

Sin staging, commit ni push. Este handoff no cierra ACT-P-002 global ni selecciona una tarea posterior.
