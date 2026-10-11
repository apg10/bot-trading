# ACT-C-001 — Revisión de la síntesis de versiones/catálogo

DEC-6/DEC-7 se aceptan como inventarios de solo lectura, no como contrato implementado ni
como cierre de ACT-C-001. El global permanece `PENDIENTE`; ACT-S-007 sigue `PAUSADO` y
ACT-E-001 `BLOQUEADO`.

## Decisiones del maestro

1. **Capacidades no es el catálogo.** `CapabilitiesContract`/`backend_capabilities.v1` queda
   como inventario estático de soporte. El catálogo versionado de estrategia/datos es un
   contrato separado; no se amplía el endpoint actual de capabilities.
2. **Estrategia:** la recomendación es una única referencia conceptual `{strategy_id, version}`
   opaca e inmutable, sin defaults ni formato fijado. No duplicar campos planos y `StrategyRef`
   en paralelo. `state.AnalysisResult.strategy_version` identifica solo una versión; no es
   equivalente a una huella `id@version`. Un campo obligatorio en `TradingStrategy` rompería
   constructores existentes: la migración/adapter se decide en un task futuro, no ahora.
3. **Datos:** `schema_version` (forma del contrato) y `dataset_ref` (identidad de una instancia
   concreta de datos) son conceptos separados. No añadirlos ahora a respuestas wire existentes
   ni fijar valores. `data_source="provided"` indica que el cliente envió las velas, no un
   proveedor certificado; `market_engine`/`TEST_ONLY` conservan sus significados actuales.
4. **Reproducibilidad:** un `dataset_ref` por sí solo no reconstruye datos. Replay exacto exige
   referencia a datos inmutables persistidos por ACT-P-001. ACT-P-010 sigue siendo PAPER con
   precios reales y ejecución simulada, no EXCHANGE-DEMO.
5. **Hueco que sigue abierto:** el catálogo común ACT-C-001 incluye indicadores, parámetros,
   unidades, límites, calentamiento y causalidad. DEC-6/7 no los inventarían; requieren un
   microtask de inventario independiente antes de cerrar el catálogo.

## Correcciones al informe Qwen

- No aprobar los campos planos `strategy_ref_id`/`strategy_ref_version` junto con otro
  `StrategyRef` en la señal como si fueran una única forma ya resuelta.
- No comparar `fingerprint=id@version` con `AnalysisResult.strategy_version` sin ID.
- No afirmar replay exacto sin persistencia ACT-P-001.
- No considerar que la síntesis del catálogo está completa mientras falten indicadores.

Los informes no modificaron código, tests ni plan. No se ejecutaron tests. El recuento de
baseline del informe Qwen (13/25) difiere del snapshot posterior observado (13/33 untracked);
sin la salida pre/post íntegra del run no se concluye alteración de archivos.
