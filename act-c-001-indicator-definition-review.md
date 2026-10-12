# ACT-C-001 — Revisión maestra del diseño IndicatorDefinition

Estado: revisión conceptual; **no autoriza implementación**. ACT-C-001 global permanece
`PENDIENTE`; ACT-S-007 sigue `PAUSADO` y ACT-E-001 `BLOQUEADO`.

## Hechos aceptados

- Keltner y MACD exponen los parámetros/defaults descritos por Qwen como propiedades de la
  implementación actual; no son defaults canónicos de estrategia.
- Warmup actual: Keltner `ema_period + atr_period`; MACD `max(fast, slow) + signal - 1`.
- `time_ms` de velas con timestamp es epoch-ms; el fallback `i*60_000` es eje relativo de test.
- La precisión redondeada por API no representa la precisión matemática del indicador.
- FYL no forma parte de esta revisión y no se reabre.

## Correcciones de diseño

1. **Exposición de parámetros:** el informe dice que Keltner/MACD son “configurables por
   estrategia”, pero el código solo muestra parámetros en las funciones. `AnalysisRequest` y
   `TradingStrategy` no exponen esos campos. El futuro catálogo debe distinguir configurable en
   llamada de configurable por estrategia/API; para la segunda dimensión el estado actual es
   “no expuesto”, no `true`.
2. **Identidad/versión:** mantener `indicator_id`, `schema_version` e
   `implementation_version` como conceptos separados; no aprobar slug, UUID, `ind.v1`, fecha,
   hash ni otros valores/formats en este documento.
3. **Unidades:** precio hereda la unidad/quote asset del instrumento. No fijar USDT. El eje de
   tiempo relativo de fallback se marca como test-only.
4. **Distribución:** un módulo Python estático puede ser fuente backend, pero no se importa
   directamente desde una UI TypeScript. La entrega a UI/IA requiere una decisión posterior
   entre JSON generado y endpoint de solo lectura. No crear endpoint ni export en este paso.
5. **Catálogo:** permanecer separado de `CapabilitiesContract`; outputs matemáticos y redondeo
   API siguen siendo conceptos distintos. Rangos desconocidos permanecen
   `NO ESPECIFICADO`, nunca se interpretan como ilimitados.

## Baseline

El informe Qwen entrega recuentos que varían entre 2/16 y 13/25. El snapshot completo de esta
sesión fue 13 tracked modificados y 33 untracked. No se toma ninguno de esos resúmenes como
prueba pre/post si no viene acompañado de las salidas completas. No se cambió código, tests ni
plan; ACT-C-001 global sigue abierto.
