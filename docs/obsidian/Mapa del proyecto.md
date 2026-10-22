# Mapa del proyecto

> Resumen de orientación revisado el 2026-10-21, basado en el plan de acción canónico y el código publicado. No sustituye una revalidación del repositorio antes de implementar.

## Componentes

| Área | Ubicación principal | Función / límite actual |
|---|---|---|
| Frontend | `frontend/` | React, TypeScript y Vite; la Terminal es la vista funcional de desarrollo. Laboratorio y Bot no deben presentarse como operativos sin soporte backend. |
| API | `backend/src/api/` | FastAPI para mercado, análisis y endpoints de bot/capacidades. |
| Mercado | `backend/src/market/` | Cliente público Binance Spot, construcción de velas y motor de mercado. Datos y ejecución son capacidades distintas. |
| Indicadores | `backend/src/indicators/` | MACD convencional, Keltner y FYL experimental. No asumir que equivalen a métodos no especificados. |
| IA | `backend/src/ai/` | Cliente Ollama y escenarios estructurados/advisory; no tiene autoridad para emitir órdenes. |
| Persistencia | `backend/src/persistence/`, `backend/src/models/` | SQLite schema v11: balances, órdenes/reservas, journal/aplicación de fills, lotes y SELL accounting. ACT-P-004 añade evaluadores puros de riesgo/filtros y gatea BUY en aceptación atómica con receipt JSON v2 (legacy v1 legible). No equivale a matching ni ciclo PAPER autónomo. |
| Pruebas | `backend/tests/`, `frontend/` | Cobertura de backend y frontend. Los resultados documentados son históricos salvo ejecución explícita reciente. |

## Flujo de datos (vista conceptual)

Binance Spot público → motor de mercado → velas / análisis → UI y escenarios advisory.

La ruta anterior **no es una ruta de ejecución financiera**. Propuestas, escenarios, datos de mercado y fills son conceptos distintos. La aceptación PAPER BUY consulta snapshots explícitos de riesgo/filtros, pero no genera fills, no hace matching y no envía órdenes a Binance. No inferir operación a partir de una API disponible, conexión abierta o etiqueta PAPER.

## Límite actual de PAPER/persistencia

- ACT-P-002 y ACT-P-003 globales permanecen `PENDIENTE`, aunque sus slices de libro, fills y órdenes aceptados sustentan los criterios requeridos por P-004.
- ACT-P-004 global está `ACEPTADO`: policy/snapshot de riesgo independiente de `StrategyConfig`, filtros de símbolo versionados y aceptación BUY atómica dentro de SQLite; el total de reservas BUY se recompone desde registros durables y se cubre concurrencia.
- ACT-P-005 evaluador puro: **TECHNICALLY_ACCEPTED** (92 tests). **Globalmente PENDIENTE** — integración SQLite requiere autorización explícita con allowlist y regression checks. No iniciar integración sin autorización.
- El gate no crea un motor de fill ni decisión intrabar. Parciales simulados, comisiones/spread/slippage/gaps y matching siguen sujetos a ACT-P-005; ciclo autónomo a ACT-P-006.
- No inferir una cartera automatizada o reconciliación completa de la existencia de tablas; ver límites y estados globales en el plan raíz.
- Ver [Seguimiento](Seguimiento.md) para un resumen fechado y [plan raíz](../../trading-bot-action-plan.md) para el estado canónico.

## Estado y vocabulario

- **TEST_ONLY**: datos sintéticos/de prueba; no representan operaciones reales ni un entorno de exchange.
- **PAPER**: objetivo de simulación propia; que aparezca como entorno nominal no demuestra que el ciclo de simulación esté integrado.
- **EXCHANGE_DEMO**: integración demo/testnet oficial; no está habilitada por esta nota.
- **LIVE**: fuera del alcance inicial. No activar ni conectar dinero real.
- **FYL / MACD BB**: especificación original pendiente; no inventar fórmula ni validar estrategias T1/V1/V2 sin ella.

## Referencias de código

- API y ciclo de vida: `backend/src/api/main.py`, `market.py`, `analysis.py`, `bot.py`.
- Mercado: `backend/src/market/engine.py`, `binance_client.py`, `candle_builder.py`.
- Indicadores: `backend/src/indicators/`.
- Contratos: `backend/src/models/`.
- Plan y restricciones: [Plan técnico](../../trading-bot-development-plan.md) y [plan de acción](../../trading-bot-action-plan.md).
