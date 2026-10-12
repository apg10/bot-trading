# ACT-C-001 — Decisión del maestro: IndicatorDefinition

Estado: dirección conceptual aceptada para continuar el diseño; **sin autorización de código**.
ACT-C-001 global permanece `PENDIENTE`; FYL/ACT-S-007 no se reabre.

## Aceptado del informe

- Keltner/MACD: los parámetros, defaults y warmup citados describen la implementación actual,
  no defaults canónicos de estrategia.
- `schema_version` y `implementation_version` son conceptos distintos. No se asignan valores,
  formatos ni literales todavía.
- El warmup se expresa como regla derivada de parámetros; el redondeo de la API no es precisión
  matemática. La causalidad observada en los bucles no constituye garantía formal.
- `CapabilitiesContract` permanece separado del catálogo de indicadores. No se añade endpoint
  ni se amplían respuestas existentes en esta etapa.

## Correcciones/precisiones del maestro

- No escoger aquí slug/UUID/SemVer ni valores de ID/version. El informe puede recomendar un ID
  semántico estable, pero la sintaxis y los valores siguen pendientes.
- Un único `configurable: bool` sería ambiguo: la función acepta parámetros, pero
  `AnalysisRequest` y `TradingStrategy` no los exponen actualmente. El diseño debe distinguir
  parámetros de llamada de parámetros expuestos a estrategia/API/UI.
- Un módulo Python puede ser la fuente backend, pero no lo importa directamente TypeScript. La
  distribución a UI/IA queda como decisión posterior (JSON generado o API de solo lectura).
- FYL, rangos no documentados, unidades fijas como USDT y precisión matemática formal quedan
  fuera o `NO ESPECIFICADO`.

## Evidencia/baseline

Qwen reportó 13 tracked modificados/25 untracked; el snapshot de este workspace observado por
el maestro fue 13/33. No se puede reconciliar ese recuento con una salida pre/post completa.
El microtask fue de solo lectura; el status actual no muestra cambios atribuibles a él.

## Siguiente paso

Microtask de decisión limitado a la semántica `function_configurable` vs
`strategy/API_exposed` y a la distribución futura del catálogo. No implementar todavía
`IndicatorDefinition`, no añadir defaults/versiones, y no reabrir N02/FYL.
