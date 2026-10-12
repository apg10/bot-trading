# ACT-C-001 — Revisión corregida de DEC-6/DEC-7

Estado: revisión documental, sin cambios de implementación. ACT-C-001 global permanece
`PENDIENTE`; ACT-S-007 permanece `PAUSADO` y ACT-E-001 `BLOQUEADO`.

## DEC-6 — Identidad y versión de estrategia

### Hechos verificados
- `backend/src/bot/models.py::TradingStrategy` tiene `name`, descripción, reglas y parámetros;
  no tiene `strategy_id` ni versión. `name` es presentación, no ID estable.
- `TradeRule` sí tiene `id` y `name`.
- `create_default_strategy()` produce una fixture por nombre. Sus valores actuales son código
  existente, no defaults aprobados para ejecución ni una estrategia canónica.
- Hay dos `AnalysisResult` diferentes: el de `bot/models.py` describe indicadores técnicos; el
  de `models/state.py` contiene `strategy_version: str`, acción, evidencia y snapshot ref.
  `DecisionRecord` referencia el segundo. No son tipos intercambiables.

### Recomendación corregida
Usar conceptualmente **una sola referencia compuesta** `{strategy_id, version}`: ID opaco
estable separado del nombre visible y versión opaca inmutable asociada a una configuración
persistida por ACT-P-001. No asignar valores, formatos, hashes ni defaults todavía.

No proponer simultáneamente campos planos en `TradingStrategy` y una segunda `StrategyRef` en
`TradingSignal`. Una única referencia debe propagarse de forma coherente desde la definición
persistida hasta la decisión. `state.AnalysisResult.strategy_version` solo contiene un string
de versión; no puede compararse con `id@version` sin migración o alias definido. Hacer
obligatoria esa referencia rompería `create_default_strategy()` y otros constructores; no se
debe llamar “aditivo/compatible” sin plan de migración. Mantener los modelos actuales intactos
hasta aprobar un contrato y adapters.

Decisión pendiente: dónde vive el `StrategyRef` serializable y cómo se conserva histórico de
versiones tras cambios. No declarar un hash, UUID o SemVer como estándar sin aprobación.

## DEC-7 — Identidad y versión de datos

### Hechos verificados
- `state.Candle.timestamp_ms` es el campo legacy; el flujo de mercado usa `open_time` y
  `close_time`. El slice DEC-3 aceptó `open_time` como identidad temporal de mercado y preservó
  `timestamp_ms` mediante adaptación.
- `MarketSnapshot` contiene símbolo, entorno y candle, sin identidad/version de dataset.
- `CandleHistoryResponse.data_source` distingue el buffer del motor y fixture `TEST_ONLY`.
  `AnalysisResponse.data_source="provided"` indica que el request aportó las velas, no certifica
  proveedor. `MarketAnalysisResponse.source` identifica el origen del análisis. Son dimensiones
  distintas y se conservan.
- El código no registra aún una revisión inmutable del dataset que permita reconstruir datos
  históricos. El tamaño/cobertura observados no bastan para identificar una versión reproducible.

### Recomendación corregida
Separar conceptualmente:
- `schema_version`: estructura/semántica del contrato de datos.
- `dataset_ref`: referencia a un conjunto concreto de datos (fuente, mercado/símbolo, intervalo,
  ventana y corte temporal, según lo que ACT-P-001 llegue a persistir).

No añadir esos campos ahora a respuestas wire existentes ni asignar valores de versión, hashes
o ventanas de ejemplo. Primero aprobar compatibilidad/adaptación y definir cómo se persiste el
dataset; un `dataset_ref` sin datos guardados o identidad inmutable **no garantiza replay**.
El catálogo de estrategia/datos debe ser independiente del `CapabilitiesContract` estático.

Corrección de dependencia: ACT-P-010 es PAPER con precios reales y ejecución simulada; no es
EXCHANGE-DEMO.

## Evidencia de baseline

No se alteraron código, tests ni plan durante los informes. El status completo observado en
esta revisión fue 13 tracked modificados, 33 untracked y 0 staged; los informes originales
indicaron 13/25, por lo que su recuento de untracked no queda reconciliado. Preservar todos los
cambios locales; no limpiar ni revertir.

## Pendiente

Aceptar estas recomendaciones y definir referencias/compatibilidad antes de microtasks de
implementación. El contrato común y su catálogo siguen abiertos; no se cierra ACT-C-001 global.

## Decisión de revisión del maestro

- Se aceptan DEC-6/DEC-7 como inventarios y se aprueba solo la dirección conceptual: `StrategyRef`
  es un objeto único `{id, version}` sin defaults; `schema_version` y `dataset_ref` son distintos.
- No se acepta la duplicación de `strategy_ref_id`/`strategy_ref_version` en `TradingStrategy`
  junto con otro objeto en `TradingSignal`. `state.AnalysisResult.strategy_version` representa
  solo la versión, no `id@version`; los dos `AnalysisResult` siguen separados.
- `CapabilitiesContract` permanece inventario estático de capabilities, no es el catálogo
  común. No se añaden campos a respuestas wire existentes en este paso.
- `dataset_ref` no promete replay hasta que ACT-P-001 persista un conjunto inmutable. No se fijan
  literal de versión, formato de ID, hashes ni rangos.
- El inventario de indicadores no se cubrió en DEC-6/DEC-7 y queda pendiente; se preparó por
  separado `qwen-task-act-c-001-indicator-catalog-map.txt`.
- ACT-C-001 global sigue `PENDIENTE`; sin implementación, tests ni cambios al plan por esta
  revisión.
