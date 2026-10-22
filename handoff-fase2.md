# Handoff — Fases 1–5: Slice BUY (Papel LIMIT/MARKET)

**Estado:** EN_REVISION — gate ampliado en verde; esperando revisión  
**Fecha:** 2026-10-16  
**Worktree:** conservado, sin staging/commit/push

---

## Tabla de Fases (Reordenada Conforme al Task Secuencial Real)

| Fase | Alcance Real | Estado | Evidencia / Gate |
|------|-------------|--------|-----------------|
| **Fase 1** | Schema v10 / migración v9→v10 | ✅ ACEPTADA | Gate focal de 6 tests: 6 passed, 0 failed, 1 warning |
| **Fase 2** | Aplicación atómica de un fill completo ya journalizado | ✅ ACEPTADA | Suite focal BUY: 16/16; gate de 5 archivos: 80 passed, 0 failed |
| **Fase 3** | Aplicación de parciales, reserva LIMIT/MARKET y overfill | ✅ ACEPTADA | Gate final de 5 archivos de esa fase: 85 passed, 0 failed |
| **Fase 4** | Mismo `fill_id` concurrente y rollback tras escrituras | ✅ ACEPTADA | Suite focal: 31/31; gate de 7 archivos: 181 passed, 1 warning; checkpoint focal Fase 4 indicado abajo |
| **Fase 5** | Cierre y gate | ✅ CERRADA | Gate final (ver abajo): 332 passed, 1 warning |

> **Nota:** Los recuentos históricos (Fases 1–4) son evidencia reutilizada por fase. No se suman ni presentan como resultados independientes del gate final. El checkpoint de Fase 4 es evidencia únicamente del archivo focal de esa fase; no se cita como baseline o entregable de Fase 1.

> **Prerrequisitos históricos:** El registro de órdenes y el contrato/journal de fills son prerrequisitos previos a las fases, no entregables de Fase 1 ni de Fase 2.

---

## Gates Históricos (Evidencia Reutilizada por Fase)

| Fuente | Comando / Suite | Resultado |
|--------|----------------|-----------|
| **Fase 2 — Gate Ampliado** | `tests/test_sqlite_store_paper_buy_fills.py` + 4 archivos adicionales (5 total) | 80 passed, 0 failed |
| **Fase 2 — Suite Focal BUY** | 16 tests focales de fills | 16/16 ✅ |
| **Fase 3 — Regresión** | 5 archivos: BUY fills, fills, acceptance, balances, orders | 85 passed, 0 failed |
| **Fase 4 — Gate Focal** | `tests/test_sqlite_store_paper_buy_fills.py` | 31/31 ✅ |
| **Fase 4 — Regresión** | 7 archivos: suites PAPER, store y lifecycle | 181 passed, 1 warning |

---

## Gate Final — Ejecución Única sobre Contenido Definitivo

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONDONTWRITEBYTECODE=1 \
  .venv/bin/python -B -m pytest -q -p no:cacheprovider --capture=sys \
  tests/test_sqlite_store_paper_buy_fills.py \
  tests/test_sqlite_store_paper_fills.py \
  tests/test_sqlite_store_paper_acceptance.py \
  tests/test_sqlite_store_paper_balances.py \
  tests/test_sqlite_store_paper_orders.py \
  tests/test_sqlite_store.py \
  tests/test_persistence_lifecycle.py \
  tests/test_paper_contracts.py \
  tests/test_models.py \
  tests/test_bot_api.py
```

**Resultado: 332 passed, 1 warning** (StarletteDeprecationWarning — no relacionado con el slice)

---

## Checkpoints y alcance del worktree

Estos checkpoints son recuperables y cubren solo los archivos incluidos en cada archivo; no deben interpretarse como snapshots completos del repositorio:

| Checkpoint | SHA-256 | Alcance |
|------------|---------|---------|
| `/tmp/opencode/actp002-schema10-pending-prepartial-checkpoint.tar.gz` | `f5d037f51b6a8c76042ca80738b274f496b94ea2f90e1adef099c646c1a6192f` | `sqlite_store.py`, tests de BUY fills, store, acceptance y lifecycle; estado previo a parciales |
| `/tmp/opencode/actp002-phase3-overfill-precheckpoint.tar.gz` | `a59421879e2f77afdd0033fa1a2ef108d9d7a11ae9d2f0a0b17bf90f14d80262` | Los mismos cinco archivos; estado previo a la regresión de overfill |
| `/tmp/opencode/fase4-checkpoint-v2/test_sqlite_store_paper_buy_fills.py` | `6a9d5f016396e231ce3d45d475e8e0824804e686bf48a430f33c621cc8804af6` | Solo el test focal, previo a las adiciones de Fase 4 |

Los archivos de store y tests mencionados por el task son el alcance lógico de las fases; salvo el contenido incluido en un checkpoint concreto, este handoff no afirma que exista un diff histórico exacto para cada archivo. El resto de rutas mostradas por `git status` es inventario del worktree y no se atribuye al slice BUY sin comparación de baseline específica.

---

## Límites del Slice BUY

- **Tipos:** PAPER BUY LIMIT / MARKET
- **Comisión:** conocida solo en QUOTE y debitada de `available`. LIMIT conserva `remaining_qty × limit_price`; MARKET conserva `max_quote_amount − notional acumulado` (las comisiones quedan fuera del límite).
- **Autoridades:** `paper_reservations` (remanente), cantidad/coste bruto (promedio derivado)
- **Cobertura:** fills, balances/lotes/reservas/órdenes, rollback atómico, reintentos, concurrencia, idempotencia (`fill_id` único)
- **Excluido explícitamente:** SELL, matching engine, cancelaciones, ejecución fuera de papel, comisión en otro asset

---

## Estado Final

| Elemento | Estado |
|----------|--------|
| **Slice BUY (Fases 1–5)** | `EN_REVISION` |
| **ACT-P-002 global** | `PENDIENTE` |

STOP tras el handoff. Sin staging, commit ni push.
