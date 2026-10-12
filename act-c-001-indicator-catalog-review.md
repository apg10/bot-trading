# ACT-C-001 — Revisión del inventario Keltner/MACD

DEC de solo lectura aceptada como **inventario de la implementación actual**, no como
especificación canónica ni cierre del catálogo. ACT-C-001 permanece `PENDIENTE`.

## Hechos aceptados

- Keltner expone `ema_period=20`, `atr_period=14`, `multiplier=2.0`; son defaults de la
  función, no valores canónicos. Su warmup actual es `ema_period + atr_period` (34 con esos
  defaults). La convención numérica aceptada por ACT-S-006 solo aplica a la implementación
  actual y no acredita el Keltner original.
- MACD expone `fast_period=12`, `slow_period=26`, `signal_period=9`; son defaults actuales,
  no versión o estándar aprobado. Su warmup actual es `max(fast, slow) + signal - 1` (34 para
  esos defaults); periodos deben ser enteros positivos y timestamps, si se entregan, deben
  corresponder uno a uno con los cierres.
- Los valores de precio/indicadores usan las unidades de los precios de entrada; `time_ms` es
  milisegundos. Si MACD no recibe timestamps, usa `i * 60_000` desde t=0, no epoch Unix.
- Las recurrencias observadas usan datos actuales/anteriores; esto es evidencia de causalidad
  de esta implementación, no una garantía formal/versionada ni equivalencia con un estándar
  externo. La precisión redondeada por API (2 decimales Keltner, 4 MACD) es serialización de
  salida actual, no precisión matemática del indicador.
- Rangos mínimos/máximos de los parámetros Keltner, schema versions, formato de IDs/versiones
  y compatibilidad entre versiones quedan `NO ESPECIFICADOS`.

## Alcance que sigue abierto

Antes de declarar el catálogo ACT-C-001 cerrado, hace falta acordar un `IndicatorDefinition`
versionable que distinga identificador, versión, parámetros configurables, defaults de
implementación, límites conocidos/desconocidos, unidades, warmup, outputs y causalidad. No
convertir los defaults actuales en estrategia aprobada ni fusionar este catálogo con
`CapabilitiesContract`.

## Calidad de la evidencia de baseline

El informe Qwen declara 2 archivos tracked modificados y 16 untracked. El snapshot completo
observado en esta sesión muestra 13 tracked modificados y 33 untracked. Por tanto, no acepto
el recuento del informe como evidencia pre/post comparable. En el status local no aparecen
cambios nuevos atribuibles a este informe; no se editaron código, tests ni plan y no se
ejecutaron tests.

ACT-S-007 permanece `PAUSADO`; ACT-E-001 permanece `BLOQUEADO`. El microtask read-only no
reabre ninguno de esos trabajos.

## Revisión del informe de diseño IndicatorDefinition

Se acepta la matriz Keltner/MACD como inventario de la implementación actual, no como catálogo
canónico. El informe queda corregido así:

- FYL (`lookback`, `min_strength`) y cualquier decisión sobre su inclusión se excluyen; ACT-S-007
  sigue `PAUSADO` y ACT-E-001 `BLOQUEADO`.
- No se aprueban formatos de ID ni valores como `ind.v1`, fechas de implementación, UUID, slug
  o SemVer. `indicator_id`, `schema_version` e `implementation_version` quedan como conceptos
  separados pendientes de decisión, sin defaults ni literales.
- Parámetros y warmup son defaults/reglas de la implementación actual, no valores canónicos de
  estrategia. Los rangos no documentados son `NO ESPECIFICADO`; no equivalen a rango ilimitado.
- Precio hereda las unidades del instrumento/quote asset; no se fija USDT. `time_ms` con fuente
  de mercado es epoch ms; `i*60_000` sin timestamps es eje relativo de pruebas, no epoch.
- La causalidad se describe como evidencia del flujo actual de índices; no como garantía formal.
  El redondeo API (2/4 decimales) no define la precisión matemática.
- Si se prepara una implementación futura, la recomendación es un módulo Python estático
  separado de `CapabilitiesContract`; no añadir endpoint ni modificar schemas existentes en
  este microtask.

## Baseline / seguimiento

Los informes Qwen mostraron recuentos distintos (2/16 y 13/25 tracked/untracked). El snapshot
completo observado por el maestro es 13/33. No se puede confirmar el pre/post del run a partir
de esos resúmenes; el status local no muestra cambios atribuibles a esta lectura. No hubo
ediciones ni tests. ACT-C-001 global permanece `PENDIENTE`; `qwen-task-act-c-001-indicator-definition-design.txt`
queda como propuesta de diseño pendiente, no autorización de implementación.
