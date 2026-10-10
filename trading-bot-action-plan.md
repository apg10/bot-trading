# Plan de acción y seguimiento — bot_trading

Fecha de creación: **2026-10-05**. Actualización documental: **2026-10-07**.

## 1. Propósito y reglas

Este archivo centraliza el orden de trabajo, los pendientes, sus dependencias y
la evidencia de cierre. Complementa, no sustituye:

- [Plan técnico](trading-bot-development-plan.md): requisitos del producto.
- [Brief visual](trading-console-visual-brief.md): interfaz objetivo.
- [Plantilla Qwen](qwen-task-template.txt): contrato de cada implementación.

**Autoridad canónica:** este plan de raíz gobierna estados, aceptaciones,
dependencias y selección del siguiente microtask. La copia fechada
`trading-plans-2026-10-07/trading-bot-action-plan.md` es una propuesta sin autoridad;
sus textos no sustituyen este seguimiento ni los paquetes ya preparados.

**Preparar o reconciliar este plan no autoriza iniciar una implementación.** Esta
reconciliación autoriza únicamente los tres documentos de raíz: este plan, el plan
técnico y el brief visual. No hay implementación activa.
B4.2 está COMPLETADO en su alcance; su preparación de §14.3 es histórica.
El bloque STATE/CLOSE/SNAPSHOT de §14.7 está aceptado en su alcance.
BACKEND-SCENARIO-ENGINE-001 está aceptado en su alcance (§14.8).
BACKEND-SCENARIO-ADVISORY-SERVICE-001 (§14.9) está COMPLETADO por revisión
manual delegada; su ficha conserva el cierre como historia. No hay siguiente
microtask seleccionado ni implementación activa ni autorización para autoavanzar.
La cola de automatización de §13 es histórica y está fuera de la selección vigente.

Principios obligatorios:

1. Un microtask autorizado a la vez, con archivos y checks exactos.
2. Revalidar código y pruebas antes de asumir que un pendiente documental existe.
3. Conservar React, FastAPI, Lightweight Charts y el trabajo previo.
4. No inventar fórmulas, capacidades, precios, órdenes, resultados ni progreso.
5. Spot: largos y salida a moneda cotizada; sin margin, derivados ni cortos.
6. LIVE, dinero real, depósitos y retiros quedan fuera del alcance inicial.
7. La IA asesora; el código valida riesgo y ejecución. No mostrar su confianza
   como probabilidad de ganar.
8. Las pruebas con servicios externos, instalaciones, diseño con Stitch y
   operaciones demo requieren permisos específicos. No leer secretos.
9. No commit, push, staging, reset, clean ni cambios de rama por iniciativa propia.

## 2. Punto de partida y límites de evidencia

- Workspace: `/home/adrian10/bot_trading`.
- Rama observada: `master`.
- HEAD histórico al crear el plan: `93f5545317c43d65b9864e57f29ca624b8f871be`.
- Contexto inicial verificado en solo lectura el 2026-10-07: `master`,
  HEAD `4383372b846ca019cd8008f96b464dd82aa3e49e`; estado inicial con este plan
  raíz modificado y tres propuestas untracked, índice vacío y ningún otro cambio.
  La captura precede a esta reconciliación y no certifica el resto del checkpoint.
- Tras la reconciliación documental se verifica 7 tracked modificados (los documentos raíz y backend) y
  10 untracked (propuestas, AI, tests), con índice vacío. Antes de ejecutar B4.2 debe
  actualizarse la precondición del paquete y capturarse su baseline completa;
  esta descripción de estado no sustituye la evidencia de contenido y propiedad.
- **HEAD no identifica todo el árbol ni basta como baseline.** Todos los HEAD,
  hashes y árboles anteriores registrados en fichas son evidencia histórica de
  sus intentos, no precondiciones locales vigentes ni autorización reutilizable.
  Esto incluye cualquier árbol 21 M, 23 M o 23 M + 30 ?? mencionado históricamente.
- Antes de cada implementación se debe fijar CLEAN o BASELINE_AUTORIZADA según
  la plantilla, incluyendo evidencia completa y propiedad de los cambios.
- La autorización de baseline del task de mercado no se extiende automáticamente
  a otros archivos ni tasks. Los artifacts temporales de aquel task no son una
  baseline vigente del proyecto después de su corrección local.

### Última ejecución completa, anterior a crear este documento

| Check | Resultado observado | Duración |
|---|---|---|
| Backend, pytest completo | 762 passed, 0 failed, 1 warning | 5,26 s |
| Frontend, Vitest completo | 320 passed, 0 failed; 3 archivos de tests | 3,14 s |
| Total | 1.082 tests correctos, 0 fallidos | No es una medida de rendimiento del producto |

Advertencia: deprecación Starlette/TestClient sobre el uso de `httpx`.
No cambiar dependencias para silenciarla sin un task separado.

Estos resultados proceden de la ejecución realizada en esta conversación; no
se volvieron a ejecutar para redactar el plan. No certifican simulación PAPER,
rentabilidad, métodos originales, build, navegador, accesibilidad, rendimiento
ni integración real con Binance/Ollama.

### Historial verificado — no reabrir sin una regresión concreta

| ID de seguimiento | Trabajo cerrado | Evidencia | Límite |
|---|---|---|---|
| HIST-IA-001 | Migrar `test_bot_ai_api.py` y `test_bot_api.py` al contrato N02 | Ambas suites + `test_ai_scenarios.py`: 503 passed | Se migraron tests; no se implementó un bot operativo |
| TRADING-MARKET-TEST-ISOLATION-001 | Aislar rutas de mercado, eliminar import productivo y corregir contaminación del sentinel | Test original: 1 passed; mercado + lifecycle: 91 passed; regresiones en ambos órdenes: 3 passed por orden | Handoff inicial de Qwen requirió corrección local; la aceptación corresponde a la versión corregida |
| HIST-TESTS-001 | Validar suites completas tras las correcciones | Backend 762 + frontend 320 passed | No incluye typecheck, build ni navegador |

### Requisitos documentales que ya tienen avances

- El lifespan de FastAPI sí inicia el motor de datos: no volver a implementar
  ese arranque porque el plan antiguo diga lo contrario.
- Existe N02 con snapshots y escenarios tipados, validación semántica, vigencia
  y restricciones Spot/no ejecución. No recrear el contrato textual antiguo.
- Hay pruebas frontend de WebSocket, candle store y request tracker. Su presencia
  no acredita por sí sola toda la integración de la Terminal.
- La liberación de callbacks WS tiene implementación; revalidar su cobertura
  antes de abrir una reparación.
- `httpx` ya fue declarado en runtime por ACT-S-001, COMPLETADO en su alcance
  aceptado; la instalación limpia sigue sin certificar.

Las descripciones antiguas del plan técnico y del brief no constituyen evidencia
de pendientes actuales. Su reconciliación documental no cierra ACT-S-002 ni fases
completas; ACT-S-002 debe contrastar el inventario con código y evidencia real.

## 3. Cómo mantener el seguimiento

### Estados

| Estado | Significado |
|---|---|
| PENDIENTE | Resultado deseado aún sin ejecutar; requiere autorización |
| PREPARADO | Contrato redactado, no iniciado; faltan autorización de ejecución y precondiciones de base |
| PAUSADO | Trabajo detenido por decisión explícita; conservar avance parcial, subaceptaciones y registro; no habilita selección ni acredita cierre |
| POR_REVALIDAR | El documento puede estar atrasado; primero comprobar código y evidencia |
| BLOQUEADO | Falta especificación, decisión, permiso o dependencia identificada |
| EN_CURSO | Microtask acotado, autorizado y realmente iniciado; máximo uno |
| EN_REVISION | Cambio realizado, pero falta aceptación o verificación requerida |
| COMPLETADO | Criterios de aceptación verificados y entrega revisada |
| NO_APLICA / DESCARTADO | Motivo explícito, conservando historial |

Prioridades: P0 = estabilización y seguridad; P1 = MVP PAPER; P2 = ampliación
posterior del producto. No son fechas ni porcentajes de avance.

### Procedimiento por task

1. Elegir la primera fila cuya dependencia esté satisfecha y confirmar el alcance.
2. Si está POR_REVALIDAR, inspeccionar en solo lectura. Si ya cumple, cerrar con
   evidencia; si hay un gap, crear el microtask concreto, no reescribir todo.
3. Preparar el contrato usando `qwen-task-template.txt`: base real, archivos
   exhaustivos, comportamiento, compatibilidad, pruebas y permisos.
4. Autorizar por separado implementación, tests y actualización de este archivo.
   El task de código debe incluir este archivo en su allowlist si va a actualizarlo;
   en caso contrario, registrar aquí el handoff mediante un task documental aparte.
5. Registrar responsable real, inicio y estado EN_CURSO; no asignar trabajo a
   Qwen/cloud ni inventar una revisión que no se haya realizado.
6. Implementar solo lo autorizado. Si el contrato no basta, marcar BLOQUEADO.
7. Ejecutar únicamente los checks acordados, revisar delta contra la baseline y
   comprobar conservación de cambios ajenos. Un test verde no basta para QA visual.
8. Registrar resultado y evidencia; cerrar solo después de la revisión. Detenerse.

Las filas de las secciones 5 a 9 son **backlog**, no tasks ejecutables ni allowlists.
Cada fila puede necesitar varios microtasks independientes. Ninguna permite
elegir archivos de implementación o decisiones financieras silenciosamente.
La sección 13 archiva los contratos y el selector históricos de automatización;
no selecciona trabajo actual. La preparación B4.2 de §14.3 es histórica; el
bloque actual de contexto N02 se referencia en §14.7.
PREPARADO no acredita aprobación cloud ni una baseline futura ya capturada.

## 4. Orden de acción y puertas de salida

| Hito | Orden y alcance | Puerta de salida |
|---|---|---|
| H0 — Base fiable | Estabilización, inventario, dependencias y checks de construcción | Pendientes actuales identificados; tests/typecheck/build verificables; fixtures explícitas |
| H1 — Terminal de análisis | Contratos, mercado/frontend, análisis y UI sin ejecución aparente | HTTP/WS coherentes; datos antiguos visibles; solicitudes obsoletas descartadas; QA básico |
| H2 — MVP PAPER | Persistencia, libro, órdenes simuladas, riesgo, autonomía y recuperación | Núcleo probado con fixtures y aceptación separada con datos reales; reinicio sin duplicados; pausa mantiene gestión |
| H3 — Método e IA integrada | Método exacto cuando esté especificado; zonas causales e IA versionada | Sin futuro en señales; respuestas vigentes; IA no controla límites ni órdenes |
| H4 — Laboratorio y Bot | Replay, comparación, métricas y supervisión por contratos reales | Experimentos reproducibles, sesiones conservadas y estado operativo verificable |
| H5 — EXCHANGE_DEMO | Adaptador oficial, reconciliación, UI demo y validación de fallos | Parciales, timeout y reconexión probados; sin credenciales live |

Orden recomendado del producto: **H0 → H1 → H2 → H3/H4 → H5**. El plan técnico
enumera demo antes de validación final; aquí se prioriza una base de evaluación
PAPER/replay antes de conectar demo. El estrés y la aceptación final se repiten
también después de H5. Este orden es propuesta de planificación, no autorización
para alterar contratos. QA y seguridad son transversales, no un acabado al final.

ACT-E-001 puede resolverse durante H0; la falta de fórmula original no bloquea
H2 con una estrategia fixture determinista e inequívocamente TEST_ONLY.

### Cierres mínimos por hito

- H0: ACT-S-001/002/003/004/005/006/007 y ACT-C-001. Un elemento POR_REVALIDAR
  se cierra con evidencia de que cumple o después de verificar sus gaps; no basta
  haberlo inspeccionado. FYL puede quedar aislado y no operativo sin inventar el
  método ni desbloquear ACT-E-001.
- H1: ACT-M-001/002/003/004/005 y ACT-T-001/002/003/004/005. Los estados de
  ejecución ausente deben seguir siendo honestos; no se necesita una cartera
  ficticia para aceptar una Terminal de análisis.
- H2 tiene dos puertas: ACT-P-001 a ACT-P-009 cierran el núcleo determinista;
  ACT-P-010 acredita PAPER integrado con mercado real. Sin permiso o evidencia
  para ACT-P-010, marcar esa puerta BLOQUEADA, no declarar todo H2 completado.
- H3 completo: ACT-E-001/002/003 y ACT-A-001/002/003. El bloqueo del método no
  impide investigación fixture en H4 ni asesoría sobre indicadores conocidos.
- H4: ACT-L-001/002/003 y ACT-B-001; la aceptación identifica si se trabajó con
  fixture o método exacto. ACT-Q-001 valida estrategias/versiones evaluadas por
  separado; una simulación de infraestructura no acredita su rentabilidad.
- H5: ACT-D-001/002/003 y repetición de ACT-Q-002 con el sistema integrado.
  ACT-Q-003 es obligatorio antes de exponer acceso fuera de la máquina local,
  no un prerrequisito para investigar offline.

Las dependencias de las tablas son obligatorias para emitir el microtask;
el resto del orden es una recomendación. ACT-D-001 exige la base de comparación
ACT-L-002, pero no presupone que el método original ni todas las pantallas de
Laboratorio estén terminadas. No habilitar una estrategia sin su validación.

## 5. Backlog — estabilización y contratos

| ID | Pri. | Estado | Resultado acotado | Dependencia | Evidencia de aceptación |
|---|---|---|---|---|---|
| ACT-S-001 | P0 | COMPLETADO | Declarar `httpx` como dependencia runtime | REV-001 aceptada manualmente; instalación limpia sigue NO EJECUTADA | Declaración y smoke existentes verificados; no certifica instalación limpia ni todas las dependencias del backend |
| ACT-S-002 | P0 | COMPLETADO | Reconciliar inventario actual con plan/brief y marcar avances reales | Ninguna; lectura de código | Matriz implementado/parcial/ausente con rutas y pruebas; aceptación manual del maestro registrada en §14.10 |
| ACT-S-003 | P0 | PENDIENTE | Verificar typecheck de aplicación y configuración | Dependencias existentes | Ambos comandos TypeScript pasan; fallos se reportan sin arreglos fuera de alcance |
| ACT-S-004 | P0 | PENDIENTE | Verificar build de producción frontend | ACT-S-003 | `npm run build` pasa; artifacts y efectos de escritura autorizados de antemano |
| ACT-S-005 | P0 | COMPLETADO | Contratos de consulta/histórico, callbacks WS y estado temporal backend | ACT-S-002 | 189 tests verdes (REV-001 + REV-002). Criterio 1: Query(100, ge=1, le=500) — default 100 (test_candles_default_limit_is_100), bordes 1/500 aceptados (test_candles_limit_boundary_accepts_valid_values), inválidos 0/-1/501 rechazados (test_market_lifecycle.py:1096-1104). Criterio 2: símbolo malformado → 422 status/candles (test_market_api.py:324-336); válido no poseído → TEST_ONLY candles + unavailable status (test_another_symbol_never_receives_current_engine_history, test_custom_symbol_fixture_is_still_test_only). Criterio 3: market_engine real vs TEST_ONLY/unavailable; fixture no presentado como conectado. Criterio 4: close_time inclusivo, guard/rechazo WS 1m y controles aligned/non-1m, bootstrap 500 velas, frescura clock_ms+margin, pending_gaps/entries_allowed. Criterio 5: callbacks connect/disconnect/error, backfill simulado y exitoso con DelayedHTTP, release handlers/tareas, cancelación/shutdown limpia. Cap HTTP min(limit,1000) implementado sin test directo (no bloquea cierre); evidencia desglosada en §14.11. Aceptación manual del maestro registrada en §14.11. |
| ACT-S-006 | P0 | COMPLETADO | Referencia numérica Keltner actual y semántica de datos insuficientes | ACT-S-002 | Microtasks separados aceptados: warmup 33/34/35 sin señal fabricada y referencia numérica independiente. Convención válida solo para la implementación actual (no equivale al método original): TR[0]=0; EMA SMA inicial + alpha=2/(n+1); ATR promedio inicial incluyendo TR[0] + Wilder; bandas EMA ± multiplier*ATR; warmup ema_period+atr_period. Fixture OHLCV 8bar-v1, parámetros de test 3/3/2 y expected exactos en §14.12; 40 passed. Defaults 20/14/2.0 siguen configurables por estrategia, no obligatorios. |
| ACT-S-007 | P0 | POR_REVALIDAR | Aislar defectos del FYL experimental sin atribuirle el método | ACT-S-002 | Tests de timestamps/fuerza con especificación del detector; batch no causal permanece TEST_ONLY |
| ACT-C-001 | P0 | PENDIENTE | Mapa canónico de entornos, capacidades, velas, propuestas, órdenes y posiciones; contratos y catálogo común para UI/IA, con versiones de estrategia y datos | ACT-S-002 | Contratos y compatibilidad aprobados antes de modificar familias de modelos; parámetros tipados, unidades, límites, calentamiento, causalidad y versiones; catálogo distingue soporte de salud/ejecución |
| BACKEND-CAPABILITIES-001 | P0 | COMPLETADO | B2: GET /api/capabilities informativo y sin efectos | REV-003 aceptada por revisión manual delegada | 6 tests y guardas de red verificadas; no cierra ACT-C-001 completo |
| BACKEND-ANALYSIS-INPUT-001 | P0 | COMPLETADO | B3.1: datos insuficientes sin fallback sintético implícito | REV-002 aceptada por revisión manual delegada | 8 tests y probe de omisión correctos; fixture separada existente intacta, sin nueva ruta o módulo |
| BACKEND-ANALYSIS-FINITE-001 | P0 | COMPLETADO | B3.2: rechazar OHLCV NaN/Infinity antes de calcular | REV-001 aceptada por revisión manual delegada | 25 tests correctos; campos finitos, volumen cero e insuficiencia conservados |
| BACKEND-ANALYSIS-RANGE-001 | P0 | COMPLETADO | B3.3: open/close dentro de low/high inclusivos | REV-001 aceptada por revisión manual delegada | 34 tests correctos; extremos inclusivos y errores anteriores conservados |
| BACKEND-ANALYSIS-TIME-001 | P0 | COMPLETADO | B3.4: open_time estrictamente creciente | REV-001 aceptada por revisión manual delegada | 40 tests correctos; secuencia irregular creciente aceptada y cola inválida rechazada |
| BACKEND-MARKET-ANALYSIS-INPUT-001 | P0 | COMPLETADO | B4.1: entrada de análisis desde cerradas propias del motor | REV-002 aceptada por revisión manual delegada | 16 tests y probe independiente offline correctos; últimas N cerradas 1m, contexto y copia profunda; sin endpoint, N02 ni ejecución |
| BACKEND-MARKET-ANALYSIS-API-001 | P0 | COMPLETADO | B4.2: API de análisis desde PreparedMarketAnalysis reutilizando await analyze | PARTE01–07 aceptadas por revisión manual delegada | 18 tests API y 42 de regresión focal correctos; cierre documental verificado, sin ejecución financiera ni certificación runtime; §14.3 conserva preparación histórica |
| BACKEND-SCENARIO-STATE-001 | P0 | COMPLETADO | STATE: proyección pura de dict a SnapshotMarketState, seis campos, sin defaults ni autoridad operativa | Revisión manual delegada del bloque y CORRECCIÓN01/02 aceptadas | Modelo/validación/no mutación conformes; aceptación limitada al adaptador |
| BACKEND-SCENARIO-CLOSE-001 | P0 | COMPLETADO | CLOSE: convertir último close preparado en SnapshotEvidence numérica, sin calcular ni redondear | STATE-001 aceptado; fixture corregida y guarda original | DTOs reales, cierre inclusivo y control de no mutación conformes; sin volumen o indicadores |
| BACKEND-SCENARIO-SNAPSHOT-001 | P0 | COMPLETADO | SNAPSHOT: componer ScenarioSnapshot mínimo desde PreparedMarketAnalysis, sin integrar Ollama ni siguiente task | STATE/CLOSE y CORRECCIÓN01–04 aceptadas por revisión manual delegada | Snapshot mínimo, guardas y prueba de alias conformes; 11 tests del bloque. Sin integración IA operativa ni reloj real |
| BACKEND-SCENARIO-CORRECCION01 | P0 | COMPLETADO | CORRECCIÓN01: reutilizar _block_network original desde test_analysis_market_input, eliminar guarda local + imports socket/mock/httpx | Revisión manual delegada del contenido actual, no del manifiesto obsoleto del handoff | Guarda importada correctamente; helpers/assertions y ocho archivos ajenos conservados; 11 tests del maestro correctos. No acepta STATE/CLOSE/SNAPSHOT ni resuelve los otros gaps |
| BACKEND-SCENARIO-CORRECCION02 | P0 | COMPLETADO | CORRECCIÓN02: excepción específica ValidationError en negativo parametrizado STATE, verificar loc exacto | Revisión manual delegada de la versión realmente observada | 3 casos del maestro correctos; un único error en loc esperado y scope conservado. No acepta automáticamente STATE/CLOSE/SNAPSHOT |
| BACKEND-SCENARIO-CORRECCION03 | P0 | COMPLETADO | CORRECCIÓN03: excepciones específicas ValidationError/ScenarioError en negativos SNAPSHOT, conservar assert code | Revisión manual delegada de la versión actual | 3 casos del maestro correctos; imports locales, excepciones exactas y code conservado. No acepta automáticamente el bloque |
| BACKEND-SCENARIO-CORRECCION04 | P0 | COMPLETADO | CORRECCIÓN04: missing_data sin alias — independencia entre lista del caller y la del snapshot | Revisión manual delegada de las seis assertions y conservación | 1 test focal y 11 del bloque correctos; listas distintas y mutaciones aisladas en ambas direcciones |

### Primer microtask de producto tras preparar la automatización: ACT-S-001 (histórico, superado)

- Hallazgo histórico ya corregido por ACT-S-001: `httpx` estaba en
  `[project.optional-dependencies].test`, aunque se importaba en runtime.
- Las instrucciones siguientes conservan la propuesta original; no seleccionan
  trabajo ni autorizan repetir el microtask completado.
- Candidato principal de implementación: `backend/pyproject.toml`; no es una
  allowlist autorizada. La prueba y sus archivos se fijarán al emitir el task.
- Preservar la restricción de versión existente salvo decisión justificada;
  no actualizar toda la pila ni resolver la advertencia de Starlette de paso.
- No usar un entorno con el extra de pruebas instalado como única evidencia
  de que una instalación normal funciona.
- Un entorno limpio y cualquier instalación/red requieren aprobación propia.
  Sin ella, documentar los límites de la comprobación y no declarar esa prueba hecha.

## 6. Backlog — mercado, análisis y Terminal

| ID | Pri. | Estado | Resultado acotado | Dependencia | Evidencia de aceptación |
|---|---|---|---|---|---|
| ACT-M-001 | P0 | POR_REVALIDAR | Apertura, canales, resuscripción y limpieza del WS frontend | ACT-S-002 | Apertura lenta, reconexión y desmontaje sin sockets/timers supervivientes |
| ACT-M-002 | P0 | POR_REVALIDAR | Velas válidas, buffer acotado y reconciliación HTTP/WS | ACT-S-002 | Carrera histórico/stream, duplicados, OHLC inválido y conservación de eventos nuevos |
| ACT-M-003 | P0 | POR_REVALIDAR | Refresco técnico por revisión, no por longitud, y descarte de respuestas antiguas | ACT-S-002 | Intrabar/longitud constante, cambio de identidad y errores visibles |
| ACT-M-004 | P1 | PENDIENTE | Selección real de símbolo e intervalo con contrato de propiedad del motor | ACT-C-001; cierre de ACT-M-001/002 | Cambio no mezcla buffers/suscripciones; separar selección de vista de reconfiguración del motor |
| ACT-M-005 | P1 | POR_REVALIDAR | Snapshot/secuencia y recuperación de huecos del transporte UI | ACT-S-005; ACT-C-001 | Protocolo versionado e HTTP/WS coherentes; primero identificar qué garantiza hoy el transporte |
| ACT-T-001 | P1 | PENDIENTE | Inventario visual, capturas y mapa mínimo de vistas y paneles contextuales reutilizables | H0 | Diseño respeta contratos y contexto real; permisos de servicios/navegador; no reescritura de React |
| ACT-T-002 | P1 | PENDIENTE | Elegir dirección visual y adaptar shell/Terminal existentes y paneles por contexto | ACT-T-001; autorización de diseño | Dos propuestas si se autoriza Stitch, elección explícita; vistas sin backend muestran indisponibilidad; preferencias persistidas según ACT-P-001 cuando esté disponible |
| ACT-T-003 | P1 | PENDIENTE | Gráfico por delta, volumen y preferencias persistidas de vista | ACT-M-002/004; ACT-T-001 | Equivalencia de serie, zoom preservado, buffers acotados; dividir funcionalidades y medir fluidez; conservación de preferencias mediante ACT-P-001 cuando esté disponible |
| ACT-T-004 | P1 | POR_REVALIDAR | Integración visible de escenarios N02, contexto y estados de vigencia | ACT-S-002; ACT-M-003 | Modelo real identificado; ocupado/error/caducado; contexto ausente visible; mercado sigue utilizable; no repetir backend N02 |
| ACT-T-005 | P1 | PENDIENTE | QA responsive y accesible de Terminal y paneles contextuales | Cambios de Terminal verificados | 1440×900, 1920×1080, 390×844, horizontal, zoom 200%, teclado, contraste y reduced motion; preferencias y estados contextuales comprobados |

Las capas FYL/MACD BB y anotaciones del método dependen de ACT-E-001/002/003;
no dibujar fórmulas inventadas para completar el diseño. La navegación puede
mostrar páginas honestamente no disponibles, nunca tablas o acciones ficticias.

## 7. Backlog — núcleo PAPER y persistencia

Estas son capacidades de producto pendientes según el plan, no una auditoría
nueva de todos sus archivos. Validar ausencia/estado con ACT-S-002 antes de crear
implementaciones. Cada fila requiere contrato y subdivisión por resultado.

| ID | Pri. | Estado | Resultado | Dependencia | Evidencia de aceptación |
|---|---|---|---|---|---|
| ACT-P-001 | P1 | PENDIENTE | Sesiones/configuraciones versionadas en SQLite y migración inicial; persistencia de versiones y preferencias | ACT-C-001 | Transacciones, escritura serializada y conservación de sesiones tras reinicio; referencias versionadas de estrategia/datos/experimentos y preferencias de panel/vista según contratos disponibles |
| ACT-P-002 | P1 | PENDIENTE | Libro de efectivo, activos, reservas, fills y comisiones | ACT-P-001 | Invariantes contables, dinero con decimales y saldo libre/reservado correcto |
| ACT-P-003 | P1 | PENDIENTE | Estados e IDs idempotentes de órdenes simuladas | ACT-C-001; ACT-P-001 | Transiciones válidas; persistencia; rechazo de duplicados y gestión del estado desconocido |
| ACT-P-004 | P1 | PENDIENTE | Validador de riesgo versionado y filtros del símbolo | ACT-P-002/003; política de riesgo aprobada | Saldo/reservas, stop, exposición, pérdida máxima y redondeos; IA no amplía límites; bloquear entradas al alcanzarlos sin liquidación implícita |
| ACT-P-005 | P1 | PENDIENTE | Política de fills PAPER con costes, spread y ambigüedad intrabar | ACT-P-002/003/004 | Parciales, comisiones, gaps y política conservadora reproducible; toque de vela no garantiza fill |
| ACT-P-006 | P1 | PENDIENTE | Ciclo autónomo con estrategia fixture TEST_ONLY | ACT-P-004/005; contrato de aptitud de datos probado | Candidato → riesgo → orden → posición → salida sin navegador; revalidar saldo, reservas, datos, spread y estado justo antes de cada envío; mantener salidas protectoras |
| ACT-P-007 | P1 | PENDIENTE | Pausar entradas; cierre/detención; recuperación y reconciliación | ACT-P-006 | Pausa mantiene gestión; reinicio no duplica operaciones; discrepancias bloquean entradas; subdividir comandos y recuperación |
| ACT-P-008 | P1 | PENDIENTE | API y UI PAPER de cartera, órdenes y actividad | ACT-P-002/003/007; ACT-T-002 | Capacidades confirmadas y fills diferenciados de propuestas; sin métricas ficticias |
| ACT-P-009 | P1 | PENDIENTE | Aceptación E2E del MVP PAPER | ACT-P-006/007/008 | Ciclo completo, costes, pausa, cierre y reinicio con datos deterministas; no implica rentabilidad |
| ACT-P-010 | P1 | PENDIENTE | Aceptación PAPER integrada con precios reales y ejecución exclusivamente simulada | ACT-P-009; H1; autorización de servicio externo | Procedencia real explícita, frescura y huecos/desconexión comprobados; entradas bloqueadas con datos no aptos; cero envío de órdenes al exchange |

Separar datos y entorno: PAPER objetivo usa datos reales con ejecuciones propias
simuladas; TEST_ONLY identifica fixtures para pruebas. No vender una prueba con
fixtures como integración de mercado real ni como EXCHANGE_DEMO.

## 8. Backlog — método e IA dentro del ciclo

| ID | Pri. | Estado | Resultado | Dependencia/bloqueo | Evidencia de aceptación |
|---|---|---|---|---|---|
| ACT-E-001 | P0 | BLOQUEADO | Especificación exacta FYL, MACD BB, barras y reglas T1/V1/V2 | Falta definición del método y ejemplos aprobados | Fórmulas, parámetros, calentamiento, entradas, salidas, tolerancias y confirmaciones sin ambigüedad |
| ACT-E-002 | P1 | BLOQUEADO | Indicadores exactos e incrementalidad equivalente al batch | ACT-E-001 | Referencias numéricas y equivalencia; separar cada indicador; MACD convencional no se renombra MACD BB |
| ACT-E-003 | P1 | BLOQUEADO | Zonas y patrones causales con revisiones | ACT-E-001/002; ACT-P-001 | IDs, detección/confirmación/invalidation, versiones y ausencia de lookahead; separar zonas de patrones |
| ACT-A-001 | P1 | PENDIENTE | Auditoría persistente de snapshot, modelo, prompt, parámetros y respuesta N02 | ACT-P-001; contrato de auditoría | Versiones y vigencia reconstruibles, errores públicos sin secretos ni texto privado |
| ACT-A-002 | P1 | POR_REVALIDAR | Contexto de escenarios: zonas, checklist y cartera cuando existan | ACT-S-002; ACT-C-001; módulos de contexto disponibles | Solo referencias reales y confirmadas; marcar campos ausentes; no fabricar evidencia |
| ACT-A-003 | P1 | PENDIENTE | Uso opcional de asesoría en el ciclo PAPER, sin autoridad de ejecución | ACT-P-006/007; ACT-A-001/002 | Revalidar mercado/estrategia/riesgo; caducidad o abstención bloquean entradas dependientes, nunca salidas protectoras |
| ACT-A-004 | P2 | PENDIENTE | Herramientas estructuradas posteriores de investigación para Qwen, sin autoridad financiera | ACT-C-001; ACT-A-001; ACT-L-002 cuando estén disponibles | Descubrimiento, esquemas, permisos, límites y auditoría; consultas y solicitudes acotadas de experimentos, sin órdenes, shell ni modificación de estrategia activa; ampliación posterior, no puerta de H4 ni bloqueo de B4.2 |

ACT-A-002 no obliga a esperar el método original si el contexto utilizado es de
la estrategia fixture o de indicadores conocidos; la incorporación de zonas
del método exacto sí requiere ACT-E-003. Mantener aisladas esas dos procedencias.

No autooptimizar estrategia/modelo/prompt durante la evaluación. Cada cambio
crea otra versión. Probar N02 con MockTransport no certifica inferencia real:
una prueba Ollama real exige autorización, modelo concreto y medición separada.

## 9. Backlog — Laboratorio, Bot y demo

| ID | Pri. | Estado | Resultado | Dependencia | Evidencia de aceptación |
|---|---|---|---|---|---|
| ACT-L-001 | P2 | PENDIENTE | Replay causal e histórico segmentado/acotado para backtest reproducible | ACT-P-001/005; ACT-C-001 | Futuro oculto; decisiones reproducibles con fuente/reloj identificados y política de costos/fills explícita; formato de histórico elegido según volumen medido |
| ACT-L-002 | P2 | PENDIENTE | Experimentos y resultados versionados/normalizados; comparación A/B/C de al menos tres variantes por hipótesis | ACT-L-001; ACT-P-009 | Datos, estrategia, código, motor, riesgo y costos identificados; todos los intentos registrados, incluidos fallidos/descartados; desarrollo/validación/test separados; referencia, periodos y exposición comparables; unidades y métricas indefinidas explícitas; tres variantes no significan tres órdenes |
| ACT-L-003 | P2 | PENDIENTE | Vista Laboratorio, comparación A/B/C e inspector de operaciones | ACT-L-002; ACT-T-002 | Resultados normalizados, costos/unidades y curvas de capital/drawdown; trade enlazado al gráfico y decisiones; estados reales, progreso solo medible y datos insuficientes visibles; sesiones anteriores conservadas |
| ACT-B-001 | P1 | PENDIENTE | Supervisión Bot con timeline, propietario, comandos confirmados y paneles contextuales | ACT-P-007/008; ACT-A-001; ACT-T-002 | Estados/IDs reales; pausa y cierre separados; modos habilitados únicamente si backend los soporta; contexto real y preferencias persistidas mediante ACT-P-001 cuando esté disponible |
| ACT-D-001 | P2 | PENDIENTE | Contrato e implementación oficial Binance EXCHANGE_DEMO | ACT-P-010; ACT-L-002; ACT-C-001 | URLs/credenciales demo independientes, filtros y tipos protectores válidos; permiso específico |
| ACT-D-002 | P2 | PENDIENTE | Reconciliación demo y pruebas de timeout, parcial y reconexión | ACT-D-001 | Estado desconocido no provoca reenvío; saldo/órdenes/fills consistentes; protección de cantidad realmente comprada |
| ACT-D-003 | P2 | PENDIENTE | UI demo y aceptación integrada | ACT-D-002; ACT-B-001; ACT-T-005 | Operaciones confirmadas, errores visibles, sin live; permisos de integración explícitos |
| ACT-Q-001 | P2 | PENDIENTE | Evaluación congelada, walk-forward, A/B/C y prospectiva | ACT-L-002; ACT-A-003 si se compara IA | Periodos de desarrollo/validación/test separados y costos/exposición comparables; evaluación reservada no guía ajustes; todos los intentos trazables; IA evaluada separadamente y LLM no validado solo por backtest; sin promesas de rentabilidad |
| ACT-Q-002 | P1 | PENDIENTE | Estrés de memoria, event loop, clientes y fluidez | H1; repetir tras PAPER/demo | Mediciones sostenidas, reconexiones y dos pestañas sin dos motores; presupuestos son objetivos, no garantías |
| ACT-Q-003 | P2 | PENDIENTE | Acceso privado remoto/autenticado, HTTPS y controles de origen | Antes de exposición externa | Comandos protegidos, Ollama/secretos no expuestos; rediseño de acceso solo autorizado |

PWA, noticias, promoción automática, microservicios y expansión a otros exchanges
no forman parte de la cola inmediata. LIVE permanece excluido, no se convierte
en un pendiente habilitable al cerrar demo. No hace falta desplegar públicamente.

## 10. Registro de bloqueos y decisiones

| Decisión | Estado actual | Efecto |
|---|---|---|
| Fórmulas/reglas del método original | Pendiente de especificación verificable | ACT-E-001 bloquea reproducción del método, no PAPER fixture |
| Instalación limpia para verificar runtime | Permiso no concedido en este task documental | No certificar instalación sin extra `test` solo con el entorno existente |
| Modelo y entorno Ollama para prueba real | No fijados para una integración autorizada | No presentar tests simulados como inferencia real |
| Cambio de símbolo/intervalo del motor | Requiere contrato de propiedad y compatibilidad | No introducir varios motores ni reconfiguración global oculta |
| Política intrabar, costes y parciales PAPER | Debe acordarse antes de ACT-P-005 | No presumir fill por contacto de precio ni inventar orden intrabar |
| Tamaño, exposición, pérdida máxima y comportamiento ante límites | Política numérica/versionada pendiente antes de ACT-P-004 | Ningún límite financiero se decide silenciosamente; bloquear entradas sin abandonar protección ni liquidar implícitamente |
| Feed y protocolo de aceptación PAPER con datos reales | Pendientes de autorización para ACT-P-010 | Cerrar el núcleo con fixtures no acredita mercado real; conservar procedencia y ejecución simulada separadas |
| Referencia visual elegida | No existe elección aprobada en este plan | Stitch/capturas/diseño se autorizan aparte |
| Credenciales y servicio EXCHANGE_DEMO | No autorizados por este documento | No leer `.env` ni conectar cuentas |
| Fechas y responsables de tasks futuros | Sin asignar | No inventar estimaciones, compromisos ni revisiones cloud |

## 11. Comandos de referencia y comprobaciones

**Referencias, no autorización de ejecución automática.** El contrato de cada
microtask debe fijar los checks pertinentes. No hacer una suite completa por
iniciativa propia cuando solo se ha autorizado una focalizada.

Backend completo, desde `backend/` — última ejecución: 762 passed:

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -B -m pytest -q -p no:cacheprovider --capture=sys
```

Frontend completo, desde `frontend/` — última ejecución: 320 passed:

```bash
npm test
```

Typecheck, desde `frontend/` — NO EJECUTADO en la validación completa reciente:

```bash
./node_modules/.bin/tsc --noEmit --incremental false -p tsconfig.json
./node_modules/.bin/tsc --noEmit --incremental false -p tsconfig.node.json
```

Build, desde `frontend/` — NO EJECUTADO en esta conversación:

```bash
npm run build
```

El build puede escribir artifacts y archivos de estado TypeScript: acordar
efectos y permisos antes de ejecutarlo. No instalar dependencias si falta una.

Desde la raíz, al preparar y entregar un task:

```bash
git branch --show-current
git rev-parse HEAD
git status --short --untracked-files=all
git diff --check
git diff --cached --check
```

`git diff` normal no incluye untracked. Para revisar el contenido de uno nuevo,
usar herramientas de lectura y/o `git diff --no-index -- /dev/null RUTA_REAL`.
El exit 1 de `--no-index` puede significar diferencias, no un fallo de pytest.
En una baseline sucia comparar también contenidos, no solo el status. No usar
los comandos para borrar ni ocultar el trabajo previo.

## 12. Ficha de seguimiento para cada ejecución futura

Al autorizar un microtask, añadir aquí una entrada con:

- ID del backlog y del contrato ejecutable; objetivo y enlace/referencia al task.
- Estado, responsable real, fecha de inicio y cierre.
- Rama, HEAD y referencia íntegra a la baseline autorizada.
- Archivos autorizados y delta real, separados del trabajo anterior.
- Criterios de aceptación y dependencias satisfechas.
- Comandos, exit codes, resultados y evidencia de revisión.
- Checks NO EJECUTADOS y límites de lo comprobado.
- Bloqueo concreto y decisión requerida, si corresponde.
- Confirmación de conservación de cambios ajenos y de no iniciar otro task.

No borrar intentos rechazados: anotar la corrección y la evidencia que permitió
cerrarlos. No recalcular un porcentaje global por contar filas; las tareas tienen
tamaños diferentes y pueden desglosarse. La evidencia manda sobre la etiqueta.

**ENGINE-001 COMPLETADO en su alcance**, bajo ACT-A-002 (§14.8).
BACKEND-SCENARIO-ADVISORY-SERVICE-001 (§14.9) está COMPLETADO por revisión
manual delegada; su ficha conserva el cierre como historia. El bloque de adaptadores
está cerrado; no completa integración IA. No hay siguiente microtask seleccionado.
Los selectores B4.2, AUTO-001 y ACT-S-001 son históricos, superados; no autorizan
reanudar infraestructura ni autoavanzar en el chat ejecutor.

Las fichas y preparaciones siguientes conservan su cronología: estados y órdenes
anteriores describen aquellos intentos, no selecciones vigentes. Prevalecen las
aceptaciones posteriores y el único siguiente microtask indicado arriba.

---

### Ficha GROUP-BACKEND-001 / RUN-MARKET-API-001-001 / REV-001 — BACKEND-MARKET-ANALYSIS-API-001 (B4.2)

- **ID backlog / contrato:** BACKEND-MARKET-ANALYSIS-API-001 / GROUP-BACKEND-001 / RUN-MARKET-API-001-001 / REV-001
- **Objetivo:** Exponer POST /api/analysis/market reutilizando la entrada B4.1 y cálculo existente, sin habilitar ejecución.
- **Estado:** COMPLETADO por revisión manual delegada — 2026-10-07; cierre limitado al contrato compacto B4.2.
- **Responsable real:** Qwen session (este chat)
- **Fecha de inicio:** 2026-10-07
- **Rama / HEAD:** `master` / `4383372b846ca019cd8008f96b464dd82aa3e49e`.
- **Baseline / autorización:** Verificación final PARTE07 bajo BASELINE_AUTORIZADA conforme a su REF-BASE: master, HEAD `4383372b846ca019cd8008f96b464dd82aa3e49e`, índice vacío, cuatro tracked modificados y cuatro untracked autorizados. Hash inicial del plan en aquella verificación: `9193cf9a7ebb8ba5ce83849fc8156079888f0b91`; código `bdc3a3bf91c8cc126d8b609b39cfebedcd8ee174`; test nuevo `33e53dd6331b8ef6f51d77da8680cb7a2ffa1c33`; otros cinco documentos conservados. Esta baseline identifica la verificación final, no una nueva captura del inicio de implementación. Allowlist original B4.2: `backend/src/api/analysis.py`, `backend/tests/test_analysis_market_api.py` y seguimiento propio en `trading-bot-action-plan.md`.
- **Archivos autorizados y delta real:**
  1. `backend/src/api/analysis.py` — MOD: añadir imports mínimos (`Request`, `Literal`); clases `MarketAnalysisRequest` (symbol str, candles_count int 50-500, extra forbid) y `MarketAnalysisResponse(AnalysisResponse)` con source/as_of_close_time_ms/market_state/execution_available; endpoint `@router.post("/market")` que lee engine de app.state, prepara localmente (import para evitar ciclo), captura MarketAnalysisInputError → HTTPException con mapeo de status codes, llama await analyze y construye respuesta nueva sin mutaciones.
   2. `backend/tests/test_analysis_market_api.py` — NEW: 329 líneas; _FakeEngine/_block_network importados de test_analysis_market_input; app aislada con router + fake en state; tests éxito 50/60 y default 200/60 comparando indicadores con POST provided; parametrizado entrada inválida (49/501/True/50.0/"50"/null/candles extra); parametrizado fallos reales del fake (engine ausente, 49 cerradas, símbolo distinto, 5m, cierre incoherente); guard cálculo real sin mocks; verificación provided conservado; test_market_symbol_strict positivo/negativo; fixture local _block_network eliminada.
  3. `trading-bot-action-plan.md` — MOD: fila BACKEND-MARKET-ANALYSIS-API-001 PREPARADO→EN_REVISION; nueva ficha al final de §12.
- **Criterios de aceptación:** Contrato exacto (symbol estricto, candles_count int 50-500 default 200, extra forbid); mapeo de errores definido; respuesta extiende AnalysisResponse con campos adicionales; execution_available Literal[False]; sin modificar prepare_market_analysis/analyze/router existente/main.py.
- **Dependencias satisfechas:** B4.1 REV-002 COMPLETADO.
- **Comandos, exit codes, resultados:** Comandos completos originales conservados en PARTE07. Qwen: archivo API → exit 0, 18 passed, 1 warning en 0.34s; regresión focal de tres archivos → exit 0, 42 passed, 1 warning en 0.36s. Maestro: mismos comandos → exit 0 en ambos; 18 passed, 1 warning en 0.35s y 42 passed, 1 warning en 0.39s, respectivamente. Diff-check y cached-check correctos, índice vacío. Los 42 tests no son la suite completa del backend; suites completas backend/frontend no ejecutadas. Resultados previos, no pruebas de esta corrección documental.
- **Checks NO EJECUTADOS:** Instalación limpia, suite completa backend/frontend, typecheck, build, navegador, red real.
- **Bloqueo concreto:** Ninguno.
- **Conservación de cambios ajenos:** Confirmada — solo se modifican los tres archivos autorizados; no se modifica prepare_market_analysis, analyze, main.py ni ningún otro módulo existente.
- **No iniciar otro task:** Confirmado — solo B4.2.

### PARTE01 — RUN-MARKET-API-001-001 / REV-001 — engine_kwargs parametrizado

- **ID backlog / contrato:** BACKEND-MARKET-ANALYSIS-API-001 / GROUP-BACKEND-001 / PART01
- **Objetivo:** Que cada negativo de `test_market_real_failures` monte su propio engine.
- **Estado:** PARTE01 aceptada por revisión manual delegada — 2026-10-07; B4.2 sigue sin aceptación global.
- **Archivos editados:** solo `backend/tests/test_analysis_market_api.py` y `trading-bot-action-plan.md`.
- **Delta real:** primer `engine_kwargs` del parametrizado → `None`; dentro del test: `engine = None if engine_kwargs is None else _FakeEngine(**engine_kwargs)`; pasar `engine` a `_build_app(engine)`. Conservar los otros cuatro casos y todos sus status/codes/assertions. No tocar el test de éxito.
- **Check del revisor desde backend/:** `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -B -m pytest -q -p no:cacheprovider --capture=sys tests/test_analysis_market_api.py::test_market_real_failures tests/test_analysis_market_api.py::test_market_success_50_from_60` → exit 0, 6 passed, 1 warning de deprecación Starlette/httpx en 0.70s; no instalación ni cambios de dependencias.
- **Conservación revisada:** parametrización/montaje corregidos, otros cuatro casos y control de éxito intactos; endpoint, helper y cinco documentos no editables conservados. Índice vacío y `git diff --check` correcto. No se reparan los otros gaps ni se inicia PARTE02 en esta revisión.

### PARTE02 — RUN-MARKET-API-001-001 / REV-001 — igualdad completa de indicadores

- **ID backlog / contrato:** BACKEND-MARKET-ANALYSIS-API-001 / GROUP-BACKEND-001 / PART02
- **Objetivo:** Comprobar igualdad completa de indicadores entre market y provided cuando ambos reciben exactamente las mismas velas.
- **Estado:** PARTE02 aceptada por revisión manual delegada — 2026-10-07; PARTE01 conserva su aceptación y B4.2 sigue sin aceptación global.
- **Archivos editados:** solo `backend/tests/test_analysis_market_api.py` y `trading-bot-action-plan.md`.
- **Delta real:** en ambos tests de éxito, construir candles_provided copiando exactamente open_time/open/high/low/close/volume de cada vela del engine (sin _make_candle); sustituir comparaciones de timestamps/longitudes por igualdad completa de data["keltner"] == data_provided["keltner"], data["macd"] == data_provided["macd"] y data["fyl"] == data_provided["fyl"]; comprobar data_provided["data_source"] == "provided". Conservar requests, counts, status 200 y asserts de metadatos existentes. PARTE01 no modificada.
- **Check del revisor desde backend/:** `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -B -m pytest -q -p no:cacheprovider --capture=sys tests/test_analysis_market_api.py::test_market_success_50_from_60 tests/test_analysis_market_api.py::test_market_success_default_200_from_60` → exit 0, 2 passed, 1 warning de deprecación Starlette/httpx en 0.28s; no instalación ni cambios de dependencias.
- **Conservación revisada:** OHLCV y open_time idénticos a la ventana del fake; comparación íntegra de Keltner/MACD/FYL y data_source provided correctos. Requests, counts, metadatos anteriores, PARTE01, endpoint y cinco documentos no editables conservados. Índice vacío y `git diff --check` correcto; no se inicia PARTE03.

### PARTE03 — RUN-MARKET-API-001-001 / REV-001 — metadatos exactos independientes

- **ID backlog / contrato:** BACKEND-MARKET-ANALYSIS-API-001 / GROUP-BACKEND-001 / PART03
- **Objetivo:** Comprobar metadatos exactos en los dos éxitos del endpoint market con expectativas independientes de la respuesta.
- **Estado:** PARTE03 aceptada por revisión manual delegada — 2026-10-07; historial conservado, PARTE01/02 siguen aceptadas y B4.2 sin aceptación global.
- **Archivos editados:** solo `backend/tests/test_analysis_market_api.py` y `trading-bot-action-plan.md`.
- **Delta real PARTE03 original:** ambos tests calculan expected_close y expected_state antes del POST; comprueban cierre, estado completo, timeframe, source y execution_available. Se conservan requests, counts, status 200 y comparaciones de PARTE02.
- **Historial PARTE03 (pruebas ejecutadas por el maestro, no por este task):**
  - **Rechazo inicial — maestro, 2026-10-07:** REQUIRES_CHANGES por faltar `assert data["symbol"] == "BTC/USDT"` en ambos tests. Comando desde backend/: `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -B -m pytest -q -p no:cacheprovider --capture=sys tests/test_analysis_market_api.py::test_market_success_50_from_60 tests/test_analysis_market_api.py::test_market_success_default_200_from_60`. Resultado: exit 0, 2 passed, 1 warning de deprecación Starlette/httpx en 0.32s. Tests verdes no cubrían el criterio ausente.
  - **Revisión de la corrección — maestro, 2026-10-07:** Qwen añadió las dos assertions; el maestro verificó el delta y repitió el mismo comando. Resultado: exit 0, 2 passed, 1 warning de deprecación Starlette/httpx en 0.30s; diff-check correcto e índice vacío. Código conforme en esta corrección; pendiente conservar correctamente el historial. No son pruebas ejecutadas por el task documental.
- **Verificación de esta corrección documental:** únicamente checks Git; no se ejecuta pytest. La evidencia de pruebas anterior pertenece a las revisiones del maestro.
- **Aceptación actual del revisor:** cuatro reemplazos literales verificados y rechazo inicial conservado; código/tests mantienen sus hashes respecto de la revisión técnica previa. `git diff --check` y `git diff --cached --check` → exit 0, índice vacío y status esperado; sin pytest en esta revisión documental. Solo se registra aceptación de PARTE03, sin nuevos cierres de implementación ni inicio de PARTE04.

### PARTE04 — RUN-MARKET-API-001-001 / REV-001 — symbol estricto

- **ID backlog / contrato:** BACKEND-MARKET-ANALYSIS-API-001 / GROUP-BACKEND-001 / PART04
- **Objetivo:** Declarar symbol estricto y comprobar que no convierte bytes a string.
- **Estado:** PARTE04 aceptada por revisión manual delegada — 2026-10-07; PARTE01/02/03 conservadas y B4.2 sin aceptación global.
- **Archivos editados:** solo `backend/src/api/analysis.py`, `backend/tests/test_analysis_market_api.py` y `trading-bot-action-plan.md`.
- **Delta real:** añadir `strict=True` al Field de `MarketAnalysisRequest.symbol`; conservar min_length/max_length existentes. Nuevo test `test_market_symbol_strict`: positivo con str intacto; negativo con bytes via pytest.raises(ValidationError), comprobando loc=("symbol",) y type="string_type". PARTE01/02/03 aceptadas conservadas.
- **Check del revisor desde backend/:** `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -B -m pytest -q -p no:cacheprovider --capture=sys tests/test_analysis_market_api.py::test_market_symbol_strict tests/test_analysis_market_api.py::test_market_success_50_from_60` → exit 0, 2 passed, 1 warning de deprecación Starlette/httpx en 0.29s; sin cambios de dependencias.
- **Conservación revisada:** único cambio productivo de esta parte en el Field de symbol; StrictInt/count, modelos restantes, endpoint, helper y tests/fichas anteriores conservados. Test nuevo con control str y único error string_type en symbol para bytes. Diff-check correcto, índice vacío y cinco documentos no editables conservados; no se inicia PARTE05.

### PARTE05 — RUN-MARKET-API-001-001 / REV-001 — eliminar fixture local muerta

- **ID backlog / contrato:** BACKEND-MARKET-ANALYSIS-API-001 / GROUP-BACKEND-001 / PART05
- **Objetivo:** Eliminar la fixture local _block_network y reutilizar únicamente la guarda importada.
- **Estado:** PARTE05 aceptada por revisión manual delegada — 2026-10-07; PARTE01–04 conservadas y B4.2 sin aceptación global.
- **Archivos editados:** solo `backend/tests/test_analysis_market_api.py` y `trading-bot-action-plan.md`.
- **Delta real:** eliminada fixture local _block_network con su comentario obsoleto; eliminados imports socket, httpx y from unittest import mock. Conservada importación de _FakeEngine y _block_network desde test_analysis_market_input. PARTE01–04 aceptadas conservadas.
- **Check del revisor desde backend/:** `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -B -m pytest -q -p no:cacheprovider --capture=sys tests/test_analysis_market_api.py` → exit 0, 18 passed, 1 warning de deprecación Starlette/httpx en 0.33s; sin cambios de dependencias.
- **Conservación revisada:** eliminación limitada a fixture local/comentario e imports socket/httpx/mock; importación original de _FakeEngine/_block_network y los 18 casos conservados. Sin nueva definición local ni cambios de endpoint/helper o documentos ajenos. Diff-check correcto, índice vacío y cinco documentos no editables conservados; no se inicia PARTE06.

### PARTE06 — RUN-MARKET-API-001-001 / REV-001 — restituir encabezado ACT-S-001

- **ID backlog / contrato:** BACKEND-MARKET-ANALYSIS-API-001 / GROUP-BACKEND-001 / PART06
- **Objetivo:** Restituir el encabezado histórico de ACT-S-001 alterado durante B4.2.
- **Estado:** PARTE06 aceptada por revisión manual delegada — 2026-10-07; PARTE01–05 conservadas y B4.2 sin aceptación global.
- **Archivos editados:** solo `trading-bot-action-plan.md`.
- **Delta real:** cambiado encabezado `## 13. Archivo histórico de la cola — GROUP-AUTO-001` por `### Ficha GROUP-BACKEND-001 / RUN-S-001-001 / REV-001 — ACT-S-001` inmediatamente anterior al ID ACT-S-001; contenido de ACT-S-001 y sección 13 legítima posterior conservados literalmente. PARTE01–05 aceptadas conservadas.
- **Checks del revisor:** `git diff --check` y `git diff --cached --check` → exit 0, índice vacío y status esperado. Encabezado ACT-S-001 restituido, contenido histórico conservado y una sola sección 13 legítima. Código/tests y cinco documentos no editables mantienen sus hashes; sin pytest en esta revisión documental.
- **Límite:** solo aceptación de PARTE06; no nuevos cierres globales de implementación ni ejecución de la verificación final B4.2 desde esta revisión.

### PARTE07 — RUN-MARKET-API-001-001 / REV-001 — checks originales y consolidación handoff

- **ID backlog / contrato:** BACKEND-MARKET-ANALYSIS-API-001 / GROUP-BACKEND-001 / PART07
- **Objetivo:** Ejecutar los checks originales y consolidar el handoff global de B4.2.
- **Estado:** PARTE07 aceptada por revisión manual delegada — 2026-10-07, tras corrección literal de los cuatro campos del cierre; B4.2 COMPLETADO en su alcance.
- **Archivos editados:** solo `trading-bot-action-plan.md`.
- **Delta real PARTE07 (verificación ejecutada por Qwen, no por maestro):**
  - `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -B -m pytest -q -p no:cacheprovider --capture=sys tests/test_analysis_market_api.py` → exit 0, 18 passed, 1 warning deprecación Starlette/httpx en 0.34s.
  - `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -B -m pytest -q -p no:cacheprovider --capture=sys tests/test_analysis_market_api.py tests/test_analysis_market_input.py tests/test_analysis_input_api.py` → exit 0, 42 passed, 1 warning en 0.36s.
  - `git diff --check` → limpio (exit 0); `git diff --cached --check` → índice vacío (exit 0).
  - Consolidación declarada por Qwen: línea test file corregida "~90 líneas" → "329 líneas"; resultados añadidos. La revisión detecta que no se completaron HEAD/baseline y permanecen "no iniciado", "Pendiente ejecución" y la etiqueta incorrecta "suite completa" para 42 tests. PARTE01–06 aceptadas conservadas.
- **Checks del maestro en esta revisión:** mismos dos comandos originales → exit 0 en ambos; archivo API: 18 passed, 1 warning en 0.35s; regresión focal de tres archivos: 42 passed, 1 warning en 0.39s. Sin suite completa backend/frontend, servicios, proveedores, instalación o cambios de código/tests.
- **Corrección única pendiente:** fila B4.2 y campos Rama/HEAD, Baseline/autorización y Comandos/resultados de su ficha global. Deben distinguir baseline de verificación, resultados Qwen/maestro y ejecución focal; no inventar evidencia inicial ni cambiar implementaciones.
- **Límite:** ninguna aceptación global B4.2 todavía; siete contenidos no editables conservados, índice vacío y diff-check correcto. Solo se registra esta revisión, sin iniciar integración N02 u otro task.
- **Cierre posterior:** se conserva arriba la revisión REQUIRES_CHANGES documental y sus causas como historia. Los cuatro reemplazos fueron verificados; código/test mantienen `bdc3a3bf91c8cc126d8b609b39cfebedcd8ee174` y `33e53dd6331b8ef6f51d77da8680cb7a2ffa1c33`. B4.2 aceptado con los 18/42 tests del maestro previamente registrados (0.35s/0.39s); no pytest nuevo en esta revisión documental. Git checks correctos, índice vacío y resto del árbol conservado. No se aceptan otras fases o módulos por este cierre.

### Ficha GROUP-BACKEND-001 / RUN-S-001-001 / REV-001 — ACT-S-001

- **ID backlog / contrato:** ACT-S-001 / GROUP-BACKEND-001 / RUN-S-001-001 / REV-001
- **Objetivo:** Mover `httpx>=0.27.0` de `[project.optional-dependencies].test` a
  `[project.dependencies]` en `backend/pyproject.toml`.
- **Estado:** COMPLETADO por aceptación manual explícita; instalación limpia NO EJECUTADA.
- **Responsable real:** Qwen session (este chat)
- **Fecha de inicio:** 2026-10-06
- **Rama / HEAD:** `master` / `93f5545317c43d65b9864e57f29ca624b8f871be`
- **Baseline / autorización:** Baseline fijada por el usuario ante la pregunta de
  fijar la base actual; dos archivos autorizados: `backend/pyproject.toml` y
  `trading-bot-action-plan.md`. Evidencia persistente en
  `/home/adrian10/.local/share/opencode/tool-output/act-s001-baseline-20261005-*`.
- **Archivos autorizados y delta real:**
  1. `backend/pyproject.toml` — MOD: añadir `"httpx>=0.27.0",` al final de
     `dependencies`; eliminar `"httpx>=0.27.0",` de `test`.
  2. `trading-bot-action-plan.md` — MOD: fila ACT-S-001 PREPARADO→EN_REVISION;
     añadir ficha GROUP-BACKEND-001/RUN-S-001-001/REV-001 al final de sección 12.
- **Criterios de aceptación:** Metadatos exactos, rango preservado, import smoke
  correcto, contenido ajeno conservado.
- **Dependencias satisfechas:** Preparación y baseline autorizadas.
- **Comandos, exit codes, resultados:**
  - `git branch --show-current` → `master` (exit 0)
  - `git rev-parse HEAD` → `93f5545317c43d65b9864e57f29ca624b8f871be` (exit 0)
  - `git status --short --untracked-files=all` → 21 M + 22 ?? (exit 0)
  - `git hash-object ...` → `9f03aec3...` / `7e0b6340...` (exit 0)
  - `git apply --reverse --check ...` → sin output (exit 0)
  - TOML validation check → `TOML and runtime httpx metadata: OK` (exit 0), revalidado en revisión manual.
  - Smoke import check → `Existing-environment runtime imports: OK` (exit 0), revalidado sin iniciar clientes/servicios.
  - `git diff --check` y `git diff --cached --check` → sin diagnósticos (exit 0), índice vacío.
  - `git status --short --untracked-files=all` post-edición → 22 M + 22 ??; pyproject pasó de limpio a modificado. La base previa de 21 M + 22 ?? no es el status final.
- **Checks NO EJECUTADOS:** Instalación limpia sin extra test, typecheck, build,
  navegador, pruebas externas, llamadas cloud.
- **Bloqueo concreto:** Ninguno.
- **Conservación de cambios ajenos:** Confirmada — solo se modifican los dos
  archivos autorizados; todo el resto del árbol se preserva intacto.
- **No iniciar otro task:** Confirmado — solo ACT-S-001.

## 13. Archivo histórico de la cola — GROUP-AUTO-001 (selector y contratos superados)

**Resumen vigente al 2026-10-07:** ACT-AUTO-001 COMPLETADO por aceptación manual
de REV-005, con 200 tests; ACT-AUTO-002 conserva avance parcial y queda PAUSADO.
Se preservan registro y subaceptaciones históricas; los detalles no recuperados
no se reconstruyen ni se convierten en nuevos cierres. No se inicia infraestructura.

**Ámbito histórico:** los selectores, mensajes de ejecución, contratos, allowlists
y precondiciones de esta sección conservan el diseño y los intentos anteriores.
Están superados para la selección actual: no son una condición local vigente,
no autorizan reanudación y no bloquean B4.2. Todos sus HEAD y árboles son históricos.
Una eventual reanudación requeriría decisión y revalidación propias; el único
siguiente microtask del producto se identifica en §14.3.

### 13.1. Objetivo y límites del grupo

Construir un mecanismo pequeño que Qwen pueda invocar para revisar **un solo
task activo**, conservar una sesión GPT por grupo, registrar handoffs separados
y cortar después de COMPLETED o BLOCKED. No construir un daemon, un scheduler
de desarrollo ni una cola que implemente todo el producto sin nuevas órdenes.

Decisiones fijadas por las conversaciones de diseño:

- Qwen implementa; GPT revisa; el controlador captura evidencia y valida cierres.
- Modelo revisor: `openai/gpt-6.1-sol`, presente en el catálogo consultado.
- OpenCode observado: 1.18.31; opciones verificadas `run`, `--agent`, `--model`,
  `--session`, `--format json`, `--file` y `--dir`. No se ha probado inferencia.
- Una sesión GPT por grupo. Correcciones y tasks consecutivos del grupo reutilizan
  su ID explícito; un grupo nuevo crea otra sesión. Nunca usar `--continue`.
- Sesión Qwen y sesión GPT separadas. No revisor recursivo ni delegación del revisor.
- Límite inicial: revisión inicial más hasta tres rondas de corrección. Cada llamada
  tendrá timeout de 180 segundos; sin reintentos técnicos automáticos en el MVP.
- No aprobar por exit code 0 ni por encontrar la palabra APPROVED en un log.
- Solo una asignación activa y un escritor automatizado por workspace.
- Después del handoff final, STOP. El siguiente task requiere otra orden del usuario.
- Reutilizar contexto no es gratis: registrar uso disponible y no prometer ahorro.

No se configura el modelo implementador: estos contratos los ejecuta la sesión
Qwen que el usuario elija. No se modifican configuración ni credenciales globales.

### 13.2. Selector histórico superado: «ejecuta el siguiente task»

Instrucción archivada, no utilizable como selector vigente:

```text
Lee trading-bot-action-plan.md, especialmente la sección 13.
Ejecuta únicamente el siguiente task habilitado de GROUP-AUTO-001.
Respeta su contrato, la baseline y los checks; actualiza solo su seguimiento
autorizado, entrega el handoff y detente. No ejecutes el task siguiente.
```

Procedimiento exacto, también antes de existir el controlador:

1. Si hay un task EN_CURSO o EN_REVISION sin cierre, no seleccionar otro. Una orden
   ambigua no autoriza reanudarlo: informar su ID y el estado pendiente.
2. Seleccionar la primera fila no COMPLETADA/NO_APLICA en el orden de la tabla.
   No saltar un bloqueo o fallo para hacer trabajo posterior.
3. Exigir cierre aceptado de sus dependencias, no solo archivos que parecen existir.
4. Leer su contrato completo y fijar baseline/propiedad según 13.3. Si falla una
   precondición, entregar BLOCKED sin editar implementación.
5. La orden autoriza únicamente el task seleccionado y el seguimiento especificado
   en su allowlist. No concede permisos externos adicionales ni cambia el contrato.
6. Ejecutar, verificar y entregar según el modo de revisión disponible (13.4).
7. Registrar el resultado y STOP. Si toda la cola está cerrada, informar
   NO_READY_TASK; no seleccionar por iniciativa propia el backlog del producto.

### 13.3. Precondiciones históricas y propiedad de la baseline de aquellos intentos

Los contratos archivados compartían las condiciones siguientes. No se aplican
al árbol local actual ni a B4.2; en particular, el HEAD esperado antiguo no debe
restaurarse ni exigirse para ejecutar el paquete del producto:

- Workspace `/home/adrian10/bot_trading`, rama `master`, HEAD esperado
  `93f5545317c43d65b9864e57f29ca624b8f871be`. Si cambia, pedir revalidación;
  no crear ramas/worktrees/clones ni arreglar la condición con Git destructivo.
- Política requerida: BASELINE_AUTORIZADA para el árbol sucio actual. **Este
  documento no sustituye la autorización y evidencia de esa baseline.** La
  aprobación del antiguo task de mercado no se reutiliza para estos archivos.
- Antes del primer task: capturar status completo, diffs tracked/staged completos
  y contenido/hashes de untracked; obtener aprobación explícita si no existe.
  Preparar la base en solo lectura no autoriza una implementación ni un envío cloud.
- Antes de leer contenido, inventariar rutas/metadatos. Excluir secretos y rutas
  no clasificadas antes de generar diffs, leer, hashear o archivar su contenido.
  Si eso impide una baseline suficiente, BLOCKED y solicitar evidencia segura
  o clasificación explícita; la exhaustividad nunca autoriza leer secretos.
- Antes de cada task posterior: fijar otra baseline, separando las entregas aceptadas
  del grupo y los cambios anteriores. La evidencia del cierre previo debe permitir
  detectar actividad concurrente. No asumir que los nombres del status bastan.
  Identificar su autorización expresa; un cierre previo no autoriza incorporar
  cambios ajenos o concurrentes. Permiso de baseline y permiso cloud son distintos.
- Las rutas marcadas NEW no existen en el inventario usado para redactar estos
  contratos. Si aparecen sin un handoff aceptado que explique su origen: BLOCKED;
  no sobrescribirlas. Para una corrección, conservar el delta previo del mismo task.
- Leer instrucciones AGENTS.md aplicables, plantilla Qwen, esta sección y los
  archivos de dependencias del task. No leer `.env`, auth.json ni claves.
- Fuente oficial de configuración: https://opencode.ai/config.json; documentación
  https://opencode.ai/docs/agents/, /permissions/ y /cli/. Consultarla al configurar;
  si falta una especificación de API, no adivinarla. La lectura de documentación
  pública no autoriza inferencia ni conexiones a servicios financieros.

La escritura del seguimiento se autoriza en cada allowlist, pero **solo** para la
fila seleccionada y una ficha al final de 13.8. No modificar prioridades, contratos,
dependencias ni filas ajenas durante la ejecución. El hash del contrato excluye
estas zonas de estado/ficha; un cambio en cualquier parte normativa lo invalida.

Hard exclusions comunes: backend/frontend de trading, otros docs, dependencias,
lockfiles, secretos, Git staging/commit/push y configuración global. No instalar
nada ni lanzar servicios. No conectar Binance/Ollama. No lanzar una revisión GPT
real hasta autorización independiente de ACT-AUTO-008.

### 13.4. Bootstrap y revisión: no simular un sistema que todavía no existe

ACT-AUTO-001 a ACT-AUTO-007 se construyen con pruebas offline y revisión manual
del handoff por el usuario/revisor de esta conversación. Cada uno puede pasar a
EN_REVISION después de sus checks; no autocertificar aprobación cloud. El cierre
COMPLETADO se registra solo cuando se acepta realmente su entrega.

Regla específica de bootstrap: el handoff de implementación usa READY_FOR_REVIEW
y el seguimiento queda EN_REVISION + STOP. Esta regla sustituye únicamente la
alternativa COMPLETED/BLOCKED del apartado HANDOFF de la plantilla vigente,
no sus límites de alcance o parada. Una aceptación manual explícita autoriza
registrar su cierre documental, no ejecutar el próximo task. No autoaceptar.
Antes de existir el controlador, la evidencia se identifica como manual/de la
conversación; no atribuir su captura a un proceso controlador que no se ejecutó.

Esto evita exigir al primer task un wrapper o un revisor que aún no están creados.
La versión final de la plantilla se adapta en ACT-AUTO-007 sin modificar reglas
superiores. Hasta entonces, se respeta la parada de la plantilla existente.

Después del piloto real aceptado, los tasks nuevos pueden autorizar expresamente
el ciclo de revisión/corrección acotado dentro del mismo task. El controlador
devuelve acción, no implementa cambios: Qwen corrige únicamente dentro de su
allowlist. Si falla un check no autorizado a corregir, falta una decisión o se
necesita otro archivo, entregar BLOCKED; no saltar al siguiente task.

### 13.5. Resumen reconciliado y cola histórica fuera de selección

| Orden | Task | Estado | Dependencias | Entrega |
|---|---|---|---|---|
| 1 | ACT-AUTO-001 | COMPLETADO | Ninguna | Contratos JSON estrictos de handoff; REV-005 aceptada manualmente por el usuario |
| 2 | ACT-AUTO-002 | PAUSADO | ACT-AUTO-001 COMPLETADO | Avance parcial de baseline, delta y artifacts; conservar subaceptaciones/registro, sin certificar cierre completo |
| 3 | ACT-AUTO-003 | PREPARADO | ACT-AUTO-001 COMPLETADO | Perfil revisor sin herramientas ni escritura |
| 4 | ACT-AUTO-004 | PREPARADO | ACT-AUTO-002/003 COMPLETADOS | Adaptador OpenCode y sesión por grupo, probado offline |
| 5 | ACT-AUTO-005 | PREPARADO | ACT-AUTO-004 COMPLETADO | Captura de checks y paquete mínimo de revisión |
| 6 | ACT-AUTO-006 | PREPARADO | ACT-AUTO-001 a 005 COMPLETADOS | Controlador de un task y CLI sin autoavance |
| 7 | ACT-AUTO-007 | PREPARADO | ACT-AUTO-006 COMPLETADO | Registro de contratos, comando Qwen y prueba E2E simulada |
| 8 | ACT-AUTO-008 | BLOQUEADO | ACT-AUTO-007 COMPLETADO + permiso cloud explícito | Piloto GPT real limitado a tres llamadas |

PREPARADO significa contrato disponible; no significa que sus dependencias,
baseline o autorizaciones ya estén satisfechas. No cambiarlo a COMPLETADO por
el hecho de que esta tabla se haya redactado.

Las filas AUTO-003 a AUTO-008 conservan sus estados documentales históricos;
ninguna participa en la selección vigente. «No hay ejecuciones iniciadas» era
el resumen de publicación original, superado por las fichas y aceptaciones.

### 13.6. Especificación común del MVP

**Implementación:** Python >=3.11, biblioteca estándar y unittest. Usar el Python
existente `backend/.venv/bin/python`; no añadir paquetes ni tocar pyproject/npm.
Archivos nuevos bajo `tools/task_automation/`, fuera del código financiero.
El controlador no lanza ni decide el modelo de Qwen y no usa un servidor persistente.
Paquetes namespace PEP 420: no crear `__init__.py`. Tests con imports absolutos
`tools.task_automation...` desde raíz. Una colisión de imports implica BLOCKED,
no añadir archivos de empaquetado fuera de allowlist.

**Artifacts:** `/tmp/opencode/bot-trading-automation/GROUP_ID/TASK_ID/RUN_ID/`.
Solo el código controlador/store escribe allí. Group/task/run IDs se validan
sin separadores, `..` ni rutas absolutas. Baseline y cada REV se crean una sola
vez, sin sobrescribir; referencias relativas y SHA-256 de contenido canónico.
Metadatos de grupo mutables se escriben atómicamente y conservan historial.
Los tests pueden crear únicamente subdirectorios temporales de este namespace.
No crear artifacts en el repo ni cambiar .gitignore. Si /tmp pierde evidencia,
informar BLOCKED; no reconstruir una aprobación como si fuera la original.
Estas escrituras temporales son efectos expresamente autorizados además de la
allowlist de fuentes; no permiten escritura arbitraria. El permiso cloud del
piloto se guarda una sola vez en el nivel TASK_ID/permit.json, fuera de RUN_ID.

**Schema v1, JSON UTF-8:** envelope obligatorio con `schema_version`, `kind`,
`group_id`, `task_id`, `run_id`, `revision_id`, `producer`, `status`,
`baseline_id`, `contract_hash`, `evidence_hash`, `previous_handoff_id`,
`created_at` UTC y `artifacts`. Rechazar claves inesperadas y tipos incorrectos.
Validar identidad frente al contexto explícito; la existencia de referencias
la comprueba evidence/store, no un validador puro. Campo no aplicable: null
permitido solo por el schema del tipo, nunca un string inventado.

- assignment: objetivo, allowlist, checks como argv/cwd exactos, dependencias,
  permisos y política de revisión; producer=controller, status=ASSIGNED.
- implementation: resumen, cambios declarados, criterios y límites;
  producer=implementer, status=READY_FOR_REVIEW. Nunca APPROVED/COMPLETED.
- checks: argv/cwd, inicio/fin, exit code, timeout, hashes de salidas y estado;
  producer=controller. No aceptar logs suministrados como ejecución propia de Qwen.
- review: producer=reviewer, verdict/status APPROVED/REQUIRES_CHANGES/BLOCKED,
  criterios, hallazgos con ID/gravedad/ruta/línea/motivo/alcance y límites.
- close: producer=controller, status COMPLETED/BLOCKED, motivo, referencias a
  checks/review y pendientes. No hereda aprobación de una revisión anterior.

#### Protocolo exacto v1 para ACT-AUTO-001 y sus consumidores

Tipos base: string no vacío; IDs `^[A-Z][A-Z0-9-]{0,63}$`; revisión
`^REV-[0-9]{3}$`; hash 64 caracteres hex minúscula; UTC
`YYYY-MM-DDTHH:MM:SSZ`. Números del protocolo: enteros, nunca bool ni float;
cantidades decimales de negocio no se introducen en este protocolo. Rechazar
claves duplicadas, NaN/Infinity, claves no string y Unicode surrogates inválidos.
Canonicalización: JSON UTF-8, claves ordenadas, separadores sin espacios,
ensure_ascii=False y sin newline final. No introducir normalización Unicode.

Envelope, con todos sus campos obligatorios:
- `schema_version` = `task_automation.v1`; `kind` = assignment/implementation/checks/review/close.
- group_id/task_id/run_id son IDs; revision_id es REV-000 para assignment y
  REV-001 o posterior para los demás. created_at usa UTC definido arriba.
- producer = controller/implementer/reviewer con las restricciones del tipo.
- baseline_id/contract_hash/evidence_hash son hashes. Solo assignment permite
  evidence_hash=null, porque todavía no hay delta/checks de implementación.
- previous_handoff_id: hash o null; null solo para assignment. Referencia a
  una entrega anterior real del mismo run, nunca a otro task.
- `artifacts`: lista de objetos exactos `{path, sha256, role}`. path es relativo
  al run, sin traversal/symlink; sha256 es hash; role = baseline/source/delta/log/checks/review.
- `body`: objeto del tipo descrito abajo; ningún campo adicional.

Estructuras reutilizadas:
- CheckDef = `{check_id, argv, cwd, timeout_seconds, allowed_writes}`:
  check_id es ID, argv lista no vacía de strings, cwd ruta relativa al workspace
  (se admite `.`), timeout_seconds entero positivo, allowed_writes lista de
  rutas relativas aprobadas o vacía. Las escrituras temporales de pruebas se
  limitan adicionalmente al namespace externo fijado, no al valor de argv.
- Criterion = `{criterion_id, state, evidence_paths, note}`: criterion_id ID,
  state MET/UNMET/NOT_CHECKED, evidence_paths lista de paths de artifacts,
  note string no vacío. La aceptación referencia la lista de criterios del contrato.

Cuerpos exactos, todos los campos obligatorios:
1. assignment: `{goal, source_contract, allowlist, checks, acceptance, dependencies,
   permissions, review_policy, limits, profile_hash}`. goal/source_contract
   strings; source_contract incluye texto normativo común y del task congelado,
   no su estado mutable. allowlist lista de rutas relativas; checks lista de
   CheckDef; acceptance lista no vacía de `{criterion_id, description, check_ids}`,
   con ID, descripción string y referencias a CheckDef existentes; dependencies
   lista de task IDs. permissions = `{fixture_git,
   public_docs, temp_artifacts, cloud_calls}`: tres bool y cloud_calls entero >=0.
   review_policy MANUAL/CLOUD; limits = `{max_corrections, max_calls,
   call_timeout_seconds}` enteros >=0 (timeout >0). profile_hash hash o null
   en MANUAL. status ASSIGNED, producer controller.
2. implementation: `{summary, changes, criteria, previous_findings, limitations}`.
   summary string; changes lista `{path, before_hash, after_hash, change}`,
   con change ADD/MODIFY/DELETE y null solo para el lado inexistente;
   criteria lista Criterion; previous_findings lista `{finding_id, resolution,
   explanation, evidence_paths}`, resolution ADDRESSED/UNADDRESSED/BLOCKED;
   limitations lista de strings. status READY_FOR_REVIEW, producer implementer.
3. checks: `{results, limitations}`. results lista no vacía de `{check_id,
   argv, cwd, started_at, finished_at, elapsed_ms, exit_code, timed_out,
   stdout_path, stderr_path, stdout_hash, stderr_hash, status}`. Fechas UTC,
   elapsed_ms entero >=0; exit_code entero o null si terminó por timeout;
   timed_out bool; paths/hashes de artifacts. status de resultado PASSED/FAILED/TIMED_OUT.
   PASSED requiere exit 0 y timed_out=false; timeout requiere TIMED_OUT y null.
   status envelope PASSED solo si todos pasaron; TIMED_OUT si alguno expiró,
   FAILED en otro caso. producer controller; limitations lista de strings.
4. review: `{verdict, criteria, findings, limitations}`. verdict igual a status;
   criteria lista Criterion; findings lista `{finding_id, severity, path, line,
   message, in_scope, criterion_id}`. severity LOW/MEDIUM/HIGH/CRITICAL;
   path relativa o null para problema global, line entero >=1 o null,
   message string, in_scope bool, criterion_id ID o null; limitations lista
   de strings. producer reviewer. APPROVED exige cero findings y todos los
   criterios MET; si hay correcciones, REQUIRES_CHANGES; falta de decisión/evidencia,
   BLOCKED. IDs de hallazgo se comparan contra la revisión previa, no se regeneran
   para ocultar pendientes. Este MVP no mezcla recomendaciones opcionales con aprobación.
5. close: `{reason, checks_handoff_id, review_handoff_id, accepted_evidence_hash,
   pending_findings, limitations}`. reason string; referencias/hash aceptado
   hash o null solo en BLOCKED; pendientes lista de finding IDs y limitations
   lista de strings. producer controller. COMPLETED requiere ambos handoffs,
   accepted_evidence_hash=evidence_hash y cero pendientes. La puerta de validez
   real de tests/review la aplica el controlador, no solo el schema.

Identidad/hash: handoff_id = SHA-256 del documento canónico completo, calculado
externamente, sin campo autorreferencial. baseline_id = hash del manifest
inmutable de baseline; contract_hash = hash canónico del body de assignment;
evidence_hash = hash del manifest de fuentes/delta/checks de esa revisión,
sin incorporar el review que después lo evalúa. Cada manifest enumera paths,
modos/tipos y hashes de contenido; timestamps de emisión no sustituyen identidad.

Orden de sellado obligatorio, sin hashes circulares:
1. Qwen entrega un borrador del body de implementation; no es todavía un handoff
   final ni una aprobación. El controlador conserva ese input original y su hash.
2. submit captura fuentes/delta, ejecuta checks autorizados y revalida el repo.
3. Materializa `task_evidence.v1`: `{schema_version, group_id, task_id, run_id,
   revision_id, baseline_id, contract_hash, files, check_results,
   implementation_body_hash}`. files es lista `{path, sha256, role}` de fuentes,
   delta y salidas; check_results es la lista de resultados descrita para checks;
   implementation_body_hash liga el borrador. Este manifest **excluye** envelopes
   implementation/checks/review/close, sus handoff IDs y hashes de esos envelopes.
4. Calcula evidence_hash del manifest una sola vez y sella los handoffs finales
   implementation y checks con ese hash. producer identifica al autor del body,
   no al proceso que serializa el envelope; preservar el body original de Qwen,
   sin convertir sus afirmaciones en checks del controlador.
5. Solo después invoca review con la evidencia sellada. Checks fallidos se registran
   igual pero no habilitan aprobación/cierre. Reejecutar checks requiere otra REV.

Por tanto, el borrador anterior a checks no necesita un hash final inventado ni
se modifica un handoff inmutable para añadirlo. La validación estructural de body
se reutiliza antes del sellado; parse_document exige el envelope final completo.

API compartida de contracts.py: `parse_document(raw, expected_identity=None)`,
`validate_document(document, expected_identity=None)`, `canonical_bytes(value)`,
`sha256_json(value)` y `handoff_id(document)`; error `ProtocolError(code)`.
expected_identity es un dict con un subconjunto de group_id/task_id/run_id/
revision_id/baseline_id/contract_hash/evidence_hash/producer y sus valores exactos.
Las funciones no hacen I/O. El store verifica existencia/hashes de artifacts;
el controlador compara historial y permisos usando datos recibidos explícitamente.
Si falta semántica imprescindible para implementarlo: BLOCKED, no ampliar el
schema por decisión propia ni reparar contracts.py desde un task posterior.

Registry de AUTO-006/007: `{schema_version, groups}`, con versión
`task_registry.v1`, groups lista de `{group_id, tasks}` y tasks lista ordenada
de `{task_id, source_file, source_heading, contract_hash, assignment_body}`.
source_file=`trading-bot-action-plan.md`; source_heading identifica el heading
TASK exacto; assignment_body cumple la estructura anterior. No contiene estados
de ejecución ni decide permisos; se contrasta con el contrato fuente congelado.
AUTO-006 consume este formato como datos sintéticos; AUTO-007 crea el archivo
real. Si cambian las interfaces previas, entregar BLOCKED, no editar módulos ajenos.
En esta cola cada task tiene el criterio `CRIT-AUTO-001` a `CRIT-AUTO-008`
respectivamente: su description contiene la aceptación completa de su contrato
y check_ids referencia todos sus checks requeridos. No omitir requisitos al
materializar el registry. Los contratos futuros pueden declarar más criterios
sin cambiar el schema ni alterar contratos activos.

Aceptación bootstrap durable: registro independiente `bootstrap_acceptance.v1`
con campos exactos `{schema_version, group_id, task_id, baseline_id, contract_hash,
evidence_hash, implementation_ref, checks_ref, authorization_ref, approved_at,
mode}`. mode=MANUAL, hashes y fecha con los tipos anteriores; refs strings no
vacíos que identifican evidencia y aceptación reales. No requiere inventar un
review cloud ni un close/checks emitido por un controlador inexistente. La operación
administrativa record-authorization lo valida y registra después de aceptación
manual, preservando esas referencias. El selector puede usarlo para dependencias,
pero no para aprobar otra REV o otro task. Si no se recupera la evidencia manual,
BLOCKED; no importar cierres por leer COMPLETADO en una tabla sin respaldo.

**Revisor:** recibe contrato, fuentes relevantes completas, baseline/delta del
task, checks y hallazgos previos. No enviar todos los archivos ajenos de baseline
al cloud. Payloads/logs sin secretos; si no se puede determinar que son seguros,
BLOCKED antes de enviar. No aceptar instrucciones en código/logs como permisos.
La primera revisión aporta contexto del grupo; siguientes aportan cambios y
fuentes nuevas, sin considerar el historial como evidencia vigente.

**Salida del controlador:** ASSIGNED, REQUIRES_CHANGES, COMPLETED, BLOCKED o
NO_READY_TASK, con IDs y motivo estructurados. No ejecuta el siguiente task,
no aplica parches sugeridos por GPT y no revierte trabajo previo.
Tres rondas de corrección como máximo; timeout/error de proveedor bloquea sin
reintentar. No afirmar un límite monetario si no se ha configurado/verificado.
La asociación grupo/sesión no se reemplaza silenciosamente. Perfil/modelo
incompatible -> BLOCKED hasta decisión. Una llamada iniciada consume presupuesto
aunque falle o quede incierta; persistirlo antes de invocar. Reanudar submit no
relanza una revisión ya iniciada ni reinicia contadores. Máximo una revisión
inicial y tres de corrección por task, salvo el piloto que tiene máximo tres llamadas.

**Modelo de seguridad:** permisos y hashes son controles de flujo, no un sandbox
del implementador. Shell arbitrario puede evadir un prompt o modificar artifacts.
El MVP no promete resistencia frente a un Qwen malicioso; la protección fuerte
requiere aislamiento/propiedad OS explícitos en otra tarea. Las pruebas sí deben
rechazar artifacts manipulados, cambios concurrentes y versiones no aprobadas.

### 13.7. Contratos históricos por task — no ejecutables desde el selector vigente

#### TASK ACT-AUTO-001 — validadores de handoff

GOAL: validar los cinco tipos de handoff de 13.6 sin dependencias externas.

AUTHORIZED IMPLEMENTATION FILES ONLY:
- `tools/task_automation/contracts.py` (NEW).
- `tools/task_automation/tests/test_contracts.py` (NEW).
- `trading-bot-action-plan.md`: exclusivamente seguimiento del task, según 13.3.

Contrato: funciones puras que validan envelopes y cuerpos, canonicalizan JSON,
calculan SHA-256 y producen errores estables sin volcar payloads privados.
Sin subprocess, Git, provider, CLI ni acceso a artifacts. IDs de hallazgos
persisten entre revisiones; no permitir que el implementador emita veredictos.

Aceptación: casos válidos de los cinco tipos; rechazo de falta/extra de campos,
tipos, producer/status ilegales, rutas con traversal e IDs cruzados. El mismo
contenido produce el mismo hash aunque cambie el orden de claves. Datos con
NaN/Infinity no son JSON canónico válido y deben rechazarse.

Desde raíz, check focalizado:
```bash
PYTHONDONTWRITEBYTECODE=1 backend/.venv/bin/python -B -m unittest discover -s tools/task_automation/tests -p test_contracts.py -v
```

#### TASK ACT-AUTO-002 — evidencia y baseline

GOAL: capturar/revalidar el estado real y guardar revisiones separadas.

AUTHORIZED IMPLEMENTATION FILES ONLY:
- `tools/task_automation/evidence.py` (NEW).
- `tools/task_automation/tests/test_evidence.py` (NEW).
- `trading-bot-action-plan.md`: exclusivamente seguimiento del task.

Contrato: usar contratos previos solo como imports. Capturar rama/HEAD/status,
index/diffs, modos, altas/bajas y contenido de tracked/untracked no ignorados;
comparar contra la allowlist y baseline, no contra nombres de status únicamente.
Store con creación exclusiva, hashes, escritura atómica de metadatos y protección
de rutas/symlinks; nunca seguir un enlace fuera del workspace. Si un archivo de
baseline contiene secretos que no pueden archivarse, bloquear, no capturarlos.
Git solo lectura: sin add/apply/reset/stash/clean/checkout ni cambios de config.
Mantener separado el seguimiento documental autorizado del delta de implementación.

Aceptación: tests con repos temporales bajo el namespace de artifacts, creados
exclusivamente para fixtures (git init/add/commit local permitido SOLO en esos
repos de test, sin hooks/identidad global/remotos). Probar cambios previos,
untracked, borrados, modo, symlink, mutación fuera de alcance y revisión repetida.
No mutar el repo real. Documentar que Git no detecta toda escritura ignorada:
artifacts generados requieren permisos explícitos y no se presume sandbox OS.
Esta es la única excepción expresa a la prohibición de crear repos/staging/commit
de la plantilla: solo fixtures de ACT-AUTO-002 en el namespace de pruebas.

Desde raíz:
```bash
PYTHONDONTWRITEBYTECODE=1 backend/.venv/bin/python -B -m unittest discover -s tools/task_automation/tests -p test_evidence.py -v
```

#### TASK ACT-AUTO-003 — perfil de cloud review

GOAL: definir un perfil explícito de revisor que evalúe el paquete, sin herramientas.

AUTHORIZED IMPLEMENTATION FILES ONLY:
- `.opencode/automation/reviewer.json` (NEW).
- `.opencode/automation/reviewer-prompt.md` (NEW).
- `tools/task_automation/tests/test_reviewer_profile.py` (NEW).
- `trading-bot-action-plan.md`: exclusivamente seguimiento del task.

Contrato: config con `$schema`, agente `cloud-review`, mode=primary y modelo
`openai/gpt-6.1-sol`; prompt externo. Negar herramientas al revisor, incluidas
edición, shell, delegación, web, MCP y lectura fuera del paquete. En este MVP el
paquete adjunto contiene la evidencia: no necesita recorrer el repo mediante tools.
El prompt exige review schema v1, revisión independiente y hallazgos por ID.
No autoaprobar, ejecutar tests ni invocar otra sesión. No introducir credenciales,
provider overrides, plugins, auto-share ni instrucciones globales en el perfil.

Aceptación: validar formas contra schema oficial y tests estructurales offline;
permisos fail-closed, modelo correcto, prompt resoluble y cero instrucciones que
permitan ampliar alcance. Un perfil no es validación de autenticación. No ejecutar
una llamada GPT real ni cambiar el OpenCode que está usando Qwen. Config nueva
requiere un proceso nuevo/reinicio para cargarse; explicarlo en el handoff.

Desde raíz:
```bash
PYTHONDONTWRITEBYTECODE=1 backend/.venv/bin/python -B -m unittest discover -s tools/task_automation/tests -p test_reviewer_profile.py -v
```

#### TASK ACT-AUTO-004 — adaptador OpenCode y sesiones

GOAL: construir invocación/parsing y persistir una sesión revisor por grupo.

AUTHORIZED IMPLEMENTATION FILES ONLY:
- `tools/task_automation/review.py` (NEW).
- `tools/task_automation/tests/test_review.py` (NEW).
- `trading-bot-action-plan.md`: exclusivamente seguimiento del task.

Contrato: subprocess con argv, shell=False, proceso hijo nuevo y cwd en un
workspace de revisión dentro de artifacts, no en el repo de trading. Cargar el
perfil explícito y su prompt desde copia de solo lectura; validar permisos
efectivos/heredados y bloquear si no puede garantizarse el perfil. No habilitar
MCP/plugins/skills de proyecto innecesarios ni registrar valores de autenticación.
Credenciales las gestiona OpenCode por su mecanismo existente, sin leerlas.
Preparar `opencode run`, modelo/agente fijos, --format json, --file y --dir;
usar --session solo si pertenece a ese grupo. Nunca --continue, --share ni --auto.

Separar exit técnico, eventos y texto final. Determinar el formato de eventos y
session ID contra la versión 1.18.31/documentación o código oficial; no inventarlo.
Tests con subprocess simulado y fixtures sintéticas fieles al formato verificado;
no lanzar opencode run real. Resolver varios bloques de texto finales sin tomar
texto intermedio/thinking como veredicto. Validar el review y IDs/hashes mediante
contracts; cualquier ambiguity/malformed/error/timeout -> BLOCKED.

Aceptación: primer grupo sin --session, segunda revisión/task con el ID correcto,
grupo nuevo sin heredar sesión; impedir llamadas concurrentes; timeout 180 s;
exit 0 con salida inválida no aprueba. Guardar outputs originales localmente,
con uso reportado si existe; no prometer ahorro o autenticación verificada.

Desde raíz:
```bash
PYTHONDONTWRITEBYTECODE=1 backend/.venv/bin/python -B -m unittest discover -s tools/task_automation/tests -p test_review.py -v
```

#### TASK ACT-AUTO-005 — checks y paquete de revisión

GOAL: capturar checks auténticos y construir un paquete mínimo del task actual.

AUTHORIZED IMPLEMENTATION FILES ONLY:
- `tools/task_automation/checks.py` (NEW).
- `tools/task_automation/packet.py` (NEW).
- `tools/task_automation/tests/test_checks_packet.py` (NEW).
- `trading-bot-action-plan.md`: exclusivamente seguimiento del task.

Contrato: ejecutar solo argv/cwd/checks aprobados en assignment, shell=False;
Qwen no puede sustituirlos mediante su handoff. Capturar exit, timeout y salidas
localmente, preservando logs sin publicarlos automáticamente. Revalidar después
el estado, incluso si el check falla, y bloquear escrituras fuera de autorización.
Packet ligado a baseline/contract/revision, fuentes relevantes completas, delta,
checks, criterios y estado de hallazgos previos; evitar adjuntar la baseline ajena
completa. Clasificación de secretos fail-closed: no vender un regex como garantía
universal; archivos no clasificados no se envían hasta aprobación específica.

Aceptación: pruebas sintéticas sin servicios, comandos financieros ni el build
del proyecto. Logs/payloads con tokens de prueba no se envían; checks falsos,
hash desactualizado, cwd/argv alternativo, timeout y mutación -> BLOCKED.
Corrección usa revisión nueva, no sobrescribe artifacts viejos.

Desde raíz:
```bash
PYTHONDONTWRITEBYTECODE=1 backend/.venv/bin/python -B -m unittest discover -s tools/task_automation/tests -p test_checks_packet.py -v
```

#### TASK ACT-AUTO-006 — controlador de un task

GOAL: aplicar las puertas de revisión/cierre sin ejecutar trabajo siguiente.

AUTHORIZED IMPLEMENTATION FILES ONLY:
- `tools/task_automation/controller.py` (NEW).
- `tools/task_automation/cli.py` (NEW).
- `tools/task_automation/tests/test_controller.py` (NEW).
- `trading-bot-action-plan.md`: exclusivamente seguimiento del task.

Contrato: CLI invocable como `backend/.venv/bin/python -B -m tools.task_automation.cli`.
Subcomandos `assign`, `submit`, `status`, `close-group`, `record-authorization`
y `pilot`, con --group/--task y
referencias a contratos/artifacts, sin cargar instrucciones ejecutables del JSON.
`assign` solo devuelve una asignación para el task solicitado, no lanza Qwen.
`submit` verifica evidencia/checks, solicita revisión y devuelve acción; solo el
controlador puede cerrar cuando checks y APPROVED corresponden al contenido actual.
Estado durable, lock exclusivo del workspace y lock de sesión; reanudación con
comprobación de contenido. Locks obsoletos no se borran por asumir que están libres.
Cambios normativos/contrato/modelo/perfil invalidan aprobación o sesión según alcance.

Aceptación offline: aprobar contenido exacto; rechazar aprobación vieja/otro task,
checks fallidos, artifacts alterados, cambios concurrentes y hallazgos fuera de
alcance; agotar tres correcciones sin bucle infinito. close-group solo acepta
tasks cerrados; ausencia de tasks autorizados devuelve NO_READY_TASK. Ningún
camino lanza el siguiente task ni ejecuta propuestas de GPT como comandos.
El registry todavía no existe: aceptar la interfaz normativa de 13.6 con fixtures
en tests, sin crear tasks.json. Incluir pruebas offline del comando pilot, permiso
ausente/ajeno/vencido, máximo de tres llamadas y grupos de sesión sintéticos.

Operaciones administrativas explícitas, sin inferencia ni implementación:
- `record-authorization --kind bootstrap-acceptance`: recibe por stdin un JSON
  bootstrap_acceptance.v1, comprueba referencias/identidad y registra el cierre
  MANUAL aceptado. Puede importar los cierres anteriores a la existencia de CLI,
  sin inventar tests ni cloud. No exige que tasks.json exista todavía: contrasta
  el contrato y la evidencia manual suministrados. AUTO-007 verifica este puente.
- `assign --group GROUP-AUTO-001 --task ACT-AUTO-008 --registry tools/task_automation/tasks.json --request-permission`:
  reserva un único run, fija propuesta de assignment y devuelve BLOCKED con
  PERMIT_REQUIRED, run_id y resumen del permiso solicitado. No invoca proveedor.
- `record-authorization --kind cloud-permit`: recibe por stdin el JSON
  cloud_permit.v1 de 13.7; verifica run reservado y aprobación real referenciada,
  registra TASK_ID/permit.json con creación exclusiva y habilita ese run solamente.
  No inicia llamadas. pilot después consume el permiso y sus contadores durables.

Estas operaciones requieren aceptación explícita visible del usuario; Qwen no
puede fabricar referencias ni usar su propio handoff como autorización. Una base
de datos local no prueba por sí sola consentimiento: registrar su procedencia
real y reconocer el modelo de confianza de 13.6. Permiso vencido/otro run: BLOCKED;
renovación solo con decisión expresa y nuevo intento/namespace aprobado, nunca
sobrescritura automática. Los comandos no conceden acceso a credenciales.

Desde raíz:
```bash
PYTHONDONTWRITEBYTECODE=1 backend/.venv/bin/python -B -m unittest discover -s tools/task_automation/tests -p test_controller.py -v
```

#### TASK ACT-AUTO-007 — integración offline y entrada «siguiente task»

GOAL: conectar selector, contratos y handoffs sin iniciar el backlog financiero.

AUTHORIZED IMPLEMENTATION FILES ONLY:
- `tools/task_automation/tasks.json` (NEW).
- `tools/task_automation/tests/test_workflow.py` (NEW).
- `.opencode/commands/siguiente-task.md` (NEW).
- `qwen-task-template.txt`: solo reglas de review/handoff/STOP y referencia a
  baseline preparada; no eliminar precondiciones ni exclusiones.
- `trading-bot-action-plan.md`: seguimiento de ACT-AUTO-007 y referencia al comando.

Contrato: tasks.json transcribe este grupo sin cambiar contratos ni permisos.
Estado de ejecución vive en artifacts, no en un registry que Qwen reescribe para
habilitarse. Comando /siguiente-task usa GROUP-AUTO-001 por defecto; no fija modelo
implementador ni instala nada. Selecciona como 13.2 y entrega un solo contrato;
la asignación se materializa y verifica antes de editar. El comando no tiene
autoridad para aprobar una baseline o una llamada pagada por el usuario.
Todos los pendientes de producto son DRAFT/no seleccionables hasta preparar
contratos en otro grupo; no insertar allowlists hipotéticas desde el backlog.

Adaptar la plantilla para distinguir READY_FOR_REVIEW de COMPLETED y permitir
solo las correcciones del task actual expresamente autorizadas. Conservar STOP
final, limitaciones de Git y parada por alcance ambiguo. Nuevo comando requiere
reiniciar OpenCode; no afirmar que la sesión Qwen actual ya lo ha cargado.

Aceptación: workflow simulado de dos tasks/grupo y otro grupo; IDs/sesiones
separados, primera asignación y parada tras cierre, errores/blocked no saltados;
review viejo no cierra; tareas futuras y piloto sin permiso no seleccionables.
Comprobar que cierres MANUAL importados con evidencia real reconocen dependencias
sin generar retrospectivamente reviews cloud ni autoaceptar otro task.
No usar la integración para implementar otro task ni emitir informes nuevos al repo.

Desde raíz, focalizado y suite completa de automatización:
```bash
PYTHONDONTWRITEBYTECODE=1 backend/.venv/bin/python -B -m unittest discover -s tools/task_automation/tests -p test_workflow.py -v
PYTHONDONTWRITEBYTECODE=1 backend/.venv/bin/python -B -m unittest discover -s tools/task_automation/tests -p 'test_*.py' -v
```

#### TASK ACT-AUTO-008 — piloto GPT real (bloqueado por permiso)

GOAL: verificar modelo, parsing y reutilización real de sesión, sin cambiar código.

AUTHORIZED IMPLEMENTATION FILES ONLY:
- `trading-bot-action-plan.md`: exclusivamente seguimiento del piloto.
- Artifacts nuevos del piloto bajo su namespace de 13.6; ningún archivo productivo.

Permiso que falta: autorización expresa de **hasta tres llamadas** al proveedor
GPT con el modelo fijado y datos sintéticos/no sensibles. Sin ese permiso,
no abrir sesión ni realizar llamada. Coste según proveedor; no se inventa un
precio. No reintentos, ninguna credencial manual y nada de Binance/Ollama.

Contrato: tres paquetes de fixture, no tres tasks de producto. Los dos primeros
usan PILOT-GROUP-001 y tasks PILOT-TASK-001/002; el tercero PILOT-GROUP-002 y
PILOT-TASK-003. Estos IDs están permitidos solo en pilot y no son seleccionables
desde el registry del producto/grupo. Grupo de ejecución GROUP-AUTO-001 y task
ACT-AUTO-008 son distintos de los grupos sintéticos de sesiones. Primera
llamada crea sesión; segunda reutiliza ID; tercera crea otra. Capturar outputs,
modelo/session IDs disponibles, tiempo y uso reportado. Verificar el schema,
identidades y evidencia; proveedor/configuración incompatible -> BLOCKED, no
fallback a otro modelo, permisos amplios o edición de scripts fuera de allowlist.

Tras implementar la CLI en ACT-AUTO-006, el piloto deberá poder ejecutarse desde
raíz con este comando exacto; esa interfaz forma parte del contrato del controlador:
```bash
PYTHONDONTWRITEBYTECODE=1 backend/.venv/bin/python -B -m tools.task_automation.cli pilot --group GROUP-AUTO-001 --task ACT-AUTO-008 --max-calls 3 --timeout-seconds 180 --permit /tmp/opencode/bot-trading-automation/GROUP-AUTO-001/ACT-AUTO-008/permit.json
```
El controlador valida un registro de permiso explícito antes de cualquier llamada.
Formato exacto del permiso: `{schema_version, group_id, task_id, run_id, model,
data_class, max_calls, timeout_seconds, session_groups, authorization_ref,
approved_at, expires_at}`. Versión `cloud_permit.v1`, modelo fijado, data_class
SYNTHETIC, max_calls=3, timeout_seconds=180 y session_groups exactamente los dos
grupos PILOT anteriores; IDs de ejecución coinciden con assignment. Fechas UTC,
expires_at hasta 24 horas tras approved_at; authorization_ref identifica una
aceptación real del usuario. Lo registra el controlador tras esa aprobación,
no un handoff autoemitido por Qwen. Ausencia/discrepancia/vencimiento -> BLOCKED.
El permiso no concede acceso a otro proveedor/modelo/datos ni se renueva solo.
Para el permiso falta un registro de ejecución, no un placeholder de configuración.
Pruebas del subcomando pilot son offline en ACT-AUTO-006/007.

Aceptación: tres reviews válidos, misma sesión en el primer grupo y sesión distinta
en el segundo; sin herramienta del revisor ni mutación de código; informe de
límites/coste disponible y cierre STOP. Si el piloto no pasa, el sistema sigue
sin certificación de integración real aunque todos los unittest estén verdes.

### Checks y handoff comunes de todos los contratos

Ejecutar únicamente el check focalizado del task y estos checks desde raíz:
```bash
git diff --check
git diff --cached --check
git status --short --untracked-files=all
```
Revisar contenido completo de NEW/untracked y delta de archivos existentes.
Revalidar contra baseline el contenido fuera de allowlist. Unittest nunca
autoriza modificar archivos importados fuera del contrato ni saltar tests.
Typecheck frontend/QA visual: NO APLICA; no se toca UI. Suite financiera completa,
build y pruebas reales: NO EJECUTADOS salvo permiso explícito en otro contrato.

Handoff obligatorio por ejecución:
1. Group/task/run/revision, estado, motivo y modo de revisión (manual/cloud real).
2. Rama/HEAD, autorización de baseline y referencias completas de evidencia.
3. Delta real, archivos modificados y trabajo previo separado.
4. Checks: comandos, exit codes, resultados y límites.
5. Review ID, hallazgos atendidos/pendientes y hash aprobado, si existen.
6. Criterios comprobados; NO EJECUTADO para cualquier check no realizado.
7. Status final y comprobación de conservación de cambios ajenos.
8. Confirmación de no commit/push/staging, no permisos ampliados y STOP.

Si BLOCKED, conservar el intento, describir la decisión necesaria y detenerse.
Si la revisión es manual y aún no aceptada, EN_REVISION y STOP, no COMPLETADO.

### 13.8. Registro de ejecuciones del grupo

**Registro histórico, con ejecuciones y aceptaciones conservadas.** La nota
original «Sin ejecuciones» y la primera selección AUTO-001 describían la
publicación de la cola y están superadas. Las fichas EN_REVISION y sus campos
«Estado actual» describen el estado de cada intento, no tareas activas actuales.
Prevalece el cierre AUTO-001 REV-005; AUTO-002 está parcialmente avanzado y
PAUSADO. No se acredita perfil, wrapper, controlador o integración completa
por este resumen. Conservar las fichas y subaceptaciones sin sobrescribir intentos.

---

#### Ficha: ACT-AUTO-001

| Campo | Valor |
|---|---|
| **Task ID** | ACT-AUTO-001 |
| **Group ID** | GROUP-AUTO-001 |
| **Run ID** | RUN-AUTO-001-001 |
| **Revision** | REV-004 (correcciones R-005 estructura completa + R-004 canonicalización) |
| **Estado actual** | EN_REVISION (READY_FOR_REVIEW entregado, pendiente aceptación manual) |
| **Modo de revisión** | MANUAL; la etiqueta anterior «bootstrap pilot offline» era incorrecta. No se ejecutó piloto. |
| **Responsable real** | Qwen (implementador), usuario (revisor) |
| **Fecha inicio** | 2026-10-05T06:45Z |
| **Fecha cierre estimado** | PENDIENTE (espera aceptación manual del revisor) |
| **Rama** | `master` |
| **HEAD baseline** | `93f5545317c43d65b9864e57f29ca624b8f871be` |
| **BASELINE_AUTORIZADA** | Workspace `/home/adrian10/bot_trading`, rama `master`, HEAD `93f5545317c43d65b9864e57f29ca624b8f871be`. 21 tracked M + 20 untracked. `git diff --check` y `--cached --check` limpios (exit 0). No hay task EN_CURSO/EN_REVISION previo en GROUP-AUTO-001. |
| **Archivos autorizados** | `tools/task_automation/contracts.py`, `tools/task_automation/tests/test_contracts.py`, `trading-bot-action-plan.md` |

#### Historia de intentos y revisiones

| Intento | Revision | Estado | Notas |
|---|---|---|---|
| 1 | REV-001 | REQUIRES_CHANGES | Primera entrega con 45 tests verdes; revisión manual rechazó gaps del contrato. Corrección de la etiqueta previa «COMPLETADO / 99»: no hubo aceptación del maestro. |
| 2 | REV-002 | REQUIRES_CHANGES | Entrega intermedia rechazada por gaps de cobertura. |
| 3 | REV-003 | REQUIRES_CHANGES | 164 tests verdes; se aceptaron avances de tipos JSON, producer y evidencias, pero persistieron campos faltantes/tipos, ciclos y Unicode en canonicalización y seguimiento inexacto. |
| 4 | REV-004 | REQUIRES_CHANGES | 168 tests verdes y entrega declarada completa; revisión manual reprodujo excepciones crudas, valores inválidos aceptados y controles de cobertura falsa. La ficha siguiente conserva el reporte histórico, no una aceptación. |
| 5 | REV-005 | COMPLETADO | 200 tests correctos y aceptación manual explícita del usuario: «Acepto REV-005 y autorizo preparar ACT-AUTO-002 con la baseline actual». No hubo commit ni review cloud automático. |

#### Ficha: ACT-AUTO-001 (REV-004)

| Campo | Valor |
|---|---|
| **Task ID** | ACT-AUTO-001 |
| **Group ID** | GROUP-AUTO-001 |
| **Run ID** | RUN-AUTO-001-001 |
| **Revision** | REV-004 (correcciones R-005 estructura completa + R-004 canonicalización) |
| **Estado actual** | EN_REVISION (READY_FOR_REVIEW entregado, pendiente aceptación manual) |
| **Modo de revisión** | MANUAL; reporte histórico corregido: no hubo bootstrap pilot ni review cloud automático. |
| **Responsable real** | Qwen (implementador), usuario (revisor) |
| **Fecha inicio** | 2026-10-05T06:45Z |
| **Fecha cierre estimado** | PENDIENTE (espera aceptación manual del revisor) |
| **Rama** | `master` |
| **HEAD baseline** | `93f5545317c43d65b9864e57f29ca624b8f871be` |
| **BASELINE_AUTORIZADA** | Workspace `/home/adrian10/bot_trading`, rama `master`, HEAD `93f5545317c43d65b9864e57f29ca624b8f871be`. 21 tracked M + 20 untracked. `git diff --check` y `--cached --check` limpios (exit 0). No hay task EN_CURSO/EN_REVISION previo en GROUP-AUTO-001. |
| **Archivos autorizados** | `tools/task_automation/contracts.py`, `tools/task_automation/tests/test_contracts.py`, `trading-bot-action-plan.md` |
| **Delta real** | `tools/task_automation/contracts.py`: 1191 líneas (baseline: ~596) — Bloque A (R-005): _validate_criterion valida dict ANTES de .keys(); expected_identity whitelist exacta (8 keys: group_id, task_id, run_id, revision_id, baseline_id, contract_hash, evidence_hash, producer; sin previous_handoff_id); expected_identity solo None/dict (reject []/False/0 con ProtocolError 9070); _validate_assignment_body valida dict antes de body[field]; goal/source_contract non-empty string; check_ids reference check; permissions dict + bool fields; limits dict + int>=0 + bool reject; timeout_seconds >0 + bool reject; argv items str; limitations/pending_findings elements str non-empty; _validate_checks_body valida dict, elapsed_ms bool reject, argv items str; _validate_review_body valida dict, verdict string check, findings.message non-empty, line bool reject, limitations str non-empty; _validate_close_body valida dict, reason non-empty, pending_findings IDs str, limitations str non-empty; _check_utc pure calendar validation (mes/día/hora/minuto/segundo); canonical_bytes incluye cycle detection via _detect_duplicate_keys_in_obj. Bloque C: 4 tests de cobertura falsa corregidos (duplicate keys con valid envelope, escaped surrogate pair con valid envelope, invalid surrogate con raw bytes inyectados manualmente, shared ref con Criterion válido entre dos criterios + round-trip test). Bloque D: R-007 tracking actualizado con historia REV-001/002/003 + nueva ficha REV-004. `tools/task_automation/tests/test_contracts.py`: 1906 líneas — new tests: parse_duplicate_keys_top_level/nested/escaped (valid envelope), duplicate_keys_across_objects_allowed, parse_accepts_valid_escaped_surrogate_pair (valid envelope + raw bytes), parse_rejects_direct_surrogates_in_raw (valid envelope + raw surrogate bytes), parse_rejects_invalid_single_surrogate (valid envelope + raw bytes), parse_accepts_valid_integer, validate_shared_ref_not_cyclic (two Criterion sharing), validate_document_shared_ref_round_trip, review_finding_path_traversal_rejects (REQUIRES_CHANGES), review_finding_path_valid_control. `tools/task_automation/trading-bot-action-plan.md`: 1063 líneas — REV-004 tracking. |
| **Checks ejecutados** | 1. `PYTHONDONTWRITEBYTECODE=1 backend/.venv/bin/python -B -m unittest discover -s tools/task_automation/tests -p test_contracts.py -v` → 168 passed, 0 failed, exit 0. 2. `git diff --check` → exit 0 (sin errores). 3. `git diff --cached --check` → exit 0 (sin errores). |
| **Checks NO EJECUTADOS** | Typecheck frontend, build frontend, suite backend completa, suite frontend completa, pytest completo del proyecto. NO APLICA: typecheck frontend/QA visual (no se toca UI). |
| **Limitaciones** | Esta ficha conserva la entrega REV-004 rechazada: sus 168 tests no acreditaban todos los criterios de estructura/Unicode/ciclos. No existió bootstrap pilot; no se certificó integración cloud real. |
| **Cierre** | PENDIENTE (espera aceptación manual del revisor). Estado READY_FOR_REVIEW entregado. |
| **Conservación cambios ajenos** | Confirmada: los 21 archivos tracked M y 20 untracked previos se mantienen intactos. No se modificó ningún archivo fuera del allowlist del task. |
| **Confirmaciones** | No commit/push/staging/branch-change. Permisos no ampliados más allá del allowlist. STOP tras entrega. |

#### Ficha: ACT-AUTO-001 — REV-005, corrección local

Las fichas anteriores son intentos históricos, no cierres aprobados. La nota
«Sin ejecuciones» al inicio de 13.8 describe la publicación original de la cola.

| Campo | Evidencia actual |
|---|---|
| **Group / task / run** | GROUP-AUTO-001 / ACT-AUTO-001 / RUN-AUTO-001-001 |
| **Revisión / estado** | REV-005 / COMPLETADO por aceptación manual explícita del usuario; ACT-AUTO-002 autorizado solo para preparación, no iniciado |
| **Fecha / responsable** | 2026-10-05; asistente de esta conversación ejecutó la corrección local por petición «el task se queda atascado… ejecútalo acá». Qwen realizó los intentos anteriores. |
| **Modo de revisión** | MANUAL; revisión independiente de código en la conversación, sin invocación del wrapper/OpenCode cloud y sin piloto |
| **Rama / HEAD** | master / `93f5545317c43d65b9864e57f29ca624b8f871be`, sin cambio de rama ni commits |
| **Autorización / propiedad** | BASELINE_AUTORIZADA original de ACT-AUTO-001 y continuación correctiva explícita. Se conservaron los cambios parciales posteriores a REV-004; no se trataron hashes de revisiones antiguas como estado actual. |
| **Base inicial del task** | 21 tracked M + 18 untracked antes de crear los dos módulos. No es lo mismo que el estado posterior de 21 M + 20 untracked. |
| **Base de la corrección local** | 21 tracked M + 20 untracked. Git RAW antes de editar: contracts=`f6a69719361b3f0a3938e3da863b565a305baed0`; tests=`1d85ac9d0d5e16ae4dabdd7705a222cdfd24b67e`; plan=`b6590f87b71dc27f37f4e30416492d0cd7bc861c`. Estos blobs de 40 hex no son hashes SHA-256 del protocolo. |
| **Evidencia de base** | Patches completos con prefijo `/tmp/opencode/act-auto001-rev005-local-`: tracked/unstaged, staged vacío y fuentes untracked. Snapshot exacto de tests antes de editar: `/tmp/opencode/act-auto001-rev005-before-test_contracts.py`, hash 1d85… verificado; snapshot de código `/tmp/opencode/act-auto001-rev005-before-contracts.py`, hash f6a6… verificado. Captura manual, no artifacts de un controlador inexistente. |
| **Archivos editados** | Únicamente contracts.py, test_contracts.py y esta fila/historial/ficha del seguimiento ACT-AUTO-001. Código financiero, frontend, normas y otros tasks fuera de alcance. |
| **Delta técnico** | Objetos completos antes de accesos; enums y listas tipados; referencias internas; identidad con ocho claves; IDs/UTC exactos; scanner JSON iterativo con ancestros, claves/valores Unicode válidos y aliases sin ciclos; duplicados al parsear vía object_pairs_hook; errores de codec/recursos con ProtocolError sin payload; retirada del cap de 1 MiB no declarado. Se preservaron los gates de review/checks/close y hashes ya aceptados. |
| **Regresiones** | Matrices públicas missing/extra/wrong type de todos los objetos, listas/enteros/bools/identidad/rutas; ciclos de dict/list y mezclas; aliases reales; claves Unicode y surrogates; JSON válido con duplicados/escapes; decoder/encoder; control de documento >1 MiB. Correcciones de tests enmascarados sin borrar garantías anteriores. |
| **Check focalizado** | `PYTHONDONTWRITEBYTECODE=1 backend/.venv/bin/python -B -m unittest discover -s tools/task_automation/tests -p test_contracts.py -v` → 200 passed, 0 failures, 0 errors, 0 skipped; exit 0, 0,443 s en la ejecución final. |
| **Verificación y conservación** | `git diff --check` y `git diff --cached --check`: exit 0; checks incrementales de los tres deltas sin diagnósticos de whitespace. Los 17 untracked ajenos conservan sus hashes; diffs tracked/staged conservan blobs `717954fd86651eb0eaf2537da1a30f1d5ab63ba5` / `e69de29bb2d1d6434b8b29ae775ad8c2e48c5391`. Solo tres rutas autorizadas cambiaron, sin rutas nuevas; prefijo normativo del plan intacto e índice vacío. |
| **Límites** | Validación pura de forma/coherencias locales y coincidencia con expected_identity, no existencia de artifacts, historial real, permisos efectivos, sandbox ni integración. `contract_hash` tiene forma válida y comparación contextual; vincular assignment_body/contrato fuente congelado corresponde a materialización/registry/controlador, no se añadió esa semántica al validador durante esta corrección. |
| **NO EJECUTADO / NO APLICA** | Suites financieras/frontend completas, build, typecheck, navegador, red de proveedor y cloud review automatizado. Ningún commit/push/staging, servicio nuevo, instalación ni cambio de configuración. |
| **Cierre** | REV-005 aceptada explícitamente por el usuario en esta conversación; dependencia ACT-AUTO-001 cerrada. No se modifica ni elimina el historial de intentos rechazados. Sin commit/push/staging ni inicio de otra implementación. |

#### Recepción manual: ACT-AUTO-001 REV-005 y preparación de ACT-AUTO-002

- Fecha de registro: 2026-10-05. Autorización real del usuario:
  «Acepto REV-005 y autorizo preparar ACT-AUTO-002 con la baseline actual».
- REV-005 aceptada: contracts Git RAW `2996f7996a6e1af9479815f241ce29c9254a140b`,
  tests Git RAW `192d48ad417376790fe74f73a5bb0bdd66cbdcce`; 200 tests correctos
  previamente ejecutados, sin repetir la suite para este cambio documental.
- Se registra COMPLETADO para ACT-AUTO-001; sus fichas anteriores son historia,
  no tareas activas adicionales. No existe aprobación cloud automática ni piloto.
- Registro histórico de preparación, superado por el resumen vigente PAUSADO
  con avance parcial: ACT-AUTO-002 permanecía PREPARADO y sin ejecución.
  Aquella autorización permitía
  capturar su nueva BASELINE_AUTORIZADA y entregar contrato/evidencia a Qwen.
  No permite implementar código ahora, instalar dependencias, commit/push o cloud.
- El estado de partida de ACT-AUTO-002 conserva el trabajo previo: 21 tracked M
  y 20 untracked, incluyendo los dos módulos de ACT-AUTO-001 y este plan.
  La captura se realiza DESPUÉS de registrar este cierre, para que la baseline
  del siguiente task incluya una dependencia realmente aceptada.

#### Preparación de producto: ACT-S-001 / GROUP-BACKEND-001

- El usuario priorizó el backend del producto y autorizó preparar este microtask
  con la baseline actual («Autorizar preparación»), sin implementar ni instalar.
- Objetivo acotado: mover `httpx>=0.27.0` a dependencias runtime conservando el
  resto de metadatos y versiones. Solo pyproject.toml y seguimiento de ACT-S-001.
- Los avances de automatización se preservan; su cierre no es prerrequisito para
  este task de producto. No se declara ACT-AUTO-002 completado ni se altera su fila.
- Validación autorizada: TOML e imports existentes sin instanciar clientes ni
  arrancar servicios. Instalación limpia y descargas NO autorizadas: pendientes
  separadas. No inferir readiness de producción del backend desde este smoke.
- Estado histórico al preparar ACT-S-001: PREPARADO, sin ejecución; superado
  por su aceptación posterior. Grupo GROUP-BACKEND-001, run RUN-S-001-001,
  futura entrega REV-001; requiere orden explícita en el chat Qwen.

#### Recepción de ACT-S-001 y preparación B2

- Aceptación real: el usuario eligió «Aceptar y preparar B2», manteniendo
  instalación limpia NO EJECUTADA. Se corrigió la ficha anterior con resultados
  ya ejecutados/revalidados; no se volvió a ejecutar su código ni se instaló nada.
- ACT-S-001 COMPLETADO en su alcance acotado: pyproject Git RAW
  `95cd98a8618f6b1049fa2d6f0ddde171c1f01db2`. No acredita NumPy runtime,
  toda la instalación normal ni readiness financiera del backend.
- B2 = BACKEND-CAPABILITIES-001, grupo GROUP-BACKEND-001,
  run RUN-CAP-001-001, futura REV-001. Estado PREPARADO, sin implementación.
- Preparación/base actuales autorizadas. Alcance futuro de cuatro archivos:
  capabilities.py NEW, montaje mínimo main.py, test_capabilities_api.py NEW
  y seguimiento de este microtask. Endpoint solo informativo, sin health calls
  a Binance/Ollama ni capacidades operativas nuevas. No cierra ACT-C-001 completo.
- Captura posterior a registrar este cierre. Se conserva todo trabajo previo,
  incluyendo ACT-AUTO-002 pendiente y sus avances; no se modifica esa fila.
- STOP de la preparación: la ejecución se pide por separado en el chat Qwen;
  sin instalaciones, servicios, commit/push/staging ni llamadas cloud.

---

### Ficha GROUP-BACKEND-001 / RUN-CAP-001-001 / REV-003 — BACKEND-CAPABILITIES-001 (B2)

- **ID backlog / contrato:** BACKEND-CAPABILITIES-001 / GROUP-BACKEND-001 / RUN-CAP-001-001 / REV-003
- **Estado BACKEND-CAPABILITIES-001:** COMPLETADO por revisión manual delegada en esta conversación; ACT-C-001 completo no se cierra.
- **Modo de revisión:** MANUAL

#### Baseline / autorización

- Baseline inicial: task GROUP-BACKEND-001 / RUN-CAP-001-001; preparación REV-001 aceptada.
- Autorización para esta corrección (REV-003): el usuario ordena reemplazar la fixture `_block_network` por la versión con `httpx` importado a nivel módulo, bloqueando IP con AF_UNIX permitido, y retirar parches opcionales de requests/aiohttp.
- Archivos editables autorizados: `backend/tests/test_capabilities_api.py` y `trading-bot-action-plan.md` (solo ficha B2).
- Restricciones: NO editar capabilities.py/main.py; NO iniciar servicios, instalar, llamar cloud ni commit/push.

#### Delta de esta corrección (REV-003)

- `backend/tests/test_capabilities_api.py`: se reemplaza la fixture `_block_network` completa por la versión especificada:
  - Se importa `httpx` a nivel módulo (no dentro del fixture).
  - Se bloquea `socket.getaddrinfo` y `socket.create_connection` con `AssertionError`.
  - Se guardan y parchean `socket.socket.connect` / `connect_ex` con guards que permiten solo `AF_UNIX`.
  - Se bloquea `httpx.HTTPTransport.handle_request` y `httpx.AsyncHTTPTransport.handle_async_request`.
  - NO se sustituye `socket.socket`, `httpx.Client` ni `httpx.AsyncClient`.
  - Se retiran de la fixture los parches opcionales de `requests` y `aiohttp`.
- No se modifican los seis tests ni sus assertions.
- No se instalan dependencias ni se reescribe el archivo completo.

#### Ejecución

```
$ cd backend
$ PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -B -m pytest -q -p no:cacheprovider --capture=sys tests/test_capabilities_api.py
```

- **Exit code:** 0 (todos los tests pasan)
- **Resultados reales:** 6 passed, 1 warning (StarletteDeprecation httpx).
- **Tests verificados:**
  1. `test_capabilities_paper_contract` — contrato JSON exacto en PAPER.
  2. `test_capabilities_exchange_demo_environment` — flags inalterados en EXCHANGE_DEMO.
  3. `test_repeated_get_unchanged` — GET repetido devuelve mismo resultado.
  4. `test_query_params_ignored` — query params no modifican flags.
  5. `test_post_not_allowed` — POST rechaza con 405.
  6. `test_route_registered_in_main_app` — ruta registrada en openapi de main.app.
- **Bloqueo de IP:** la fixture bloquea todas las conexiones IP reales; solo se permite `AF_UNIX` (TestClient/AnyIO).

#### Limitaciones y checks NO EJECUTADOS

- **Instalación limpia:** NO ejecutada (restricción explícita del task).
- **Llamadas cloud / servicios reales:** NO ejecutadas (restricción explícita).
- **Typecheck / build frontend:** NO ejecutados (fuera de alcance).
- **Cobertura de código:** NO medida (no se ejecutó `--cov`).
- **Compatibilidad con otras versiones de httpx/starlette:** NO verificada.
- **Pruebas de integración end-to-end:** NO ejecutadas.

#### Estado final

- **Estado:** COMPLETADO por aceptación de la revisión delegada; la ejecución anterior había entregado EN_REVISION, sin autoaprobación de Qwen.
- **No se marca COMPLETADO** ni se cierra ACT-C-001 completo.
- **Handoff:** READY_FOR_REVIEW para REV-003.

### Recepción delegada de B2 y preparación B3.1

- Autorización real: el usuario pidió revisar cada nuevo handoff y enviar en la
  misma respuesta la corrección o el siguiente task. Esta instrucción permite
  registrar resultados/aceptación de la revisión manual y preparar el siguiente
  alcance; no autoriza su implementación, instalaciones, cloud o Git de escritura.
- B2 REV-003 aceptada: 6 passed, 1 warning, exit 0. Probes simulados verificaron
  bloqueo de AF_INET/AF_INET6, connect/connect_ex y HTTP sync/async, con delegación
  AF_UNIX. No se abrieron conexiones reales. Endpoint/main permanecieron intactos.
- Recepción de B2: código `52b670d7bb812663bd382dbe878261ccf0ab72c4`, main
  `d84b8ce4bc8299aefd84feb38c8483239e35daea`, tests
  `c7d5e764f04c2a7a627c142cc28729e619dabff0` (Git RAW, no SHA-256 del protocolo).
- Siguiente microtask BACKEND-ANALYSIS-INPUT-001, grupo GROUP-BACKEND-001,
  run RUN-ANALYSIS-001-001, futura REV-001. Solo analysis.py, test aislado NEW
  test_analysis_input_api.py y seguimiento propio. No se inicia durante preparación.
- Política acotada: POST /api/analysis solo calcula sobre velas proporcionadas
  suficientes, mínimo existente de 50. Omitidas/null/vacías/49 producen 422 con
  código INSUFFICIENT_CANDLES, sin generar fixtures ni calcular indicadores.
  /fixture/data conserva su selección explícita; no se añade otro modo/flag aquí.
- Se conservan éxito provided, cálculo/params, timestamps y forma de respuesta.
  No se certifica origen/frescura por decir provided. Cierre/provisional, OHLC
  completo, FYL causal y constructor propio N02 quedan para otros microtasks.
- Captura de nueva base posterior a registrar este cierre. Sin cambio de
  backend/frontend durante esta preparación; contexto/test_macd existente es
  solo lectura y no se ejecuta su lifespan productivo.

---

### Ficha GROUP-BACKEND-001 / RUN-ANALYSIS-001-001 / REV-001 — BACKEND-ANALYSIS-INPUT-001 (B3.1)

- **ID backlog / contrato:** BACKEND-ANALYSIS-INPUT-001 / GROUP-BACKEND-001 / RUN-ANALYSIS-001-001 / REV-001
- **Estado BACKEND-ANALYSIS-INPUT-001:** EN_REVISION (no se cierra ni marca COMPLETADO)
- **Modo de revisión:** MANUAL

#### Baseline / autorización

- Baseline inicial: task GROUP-BACKEND-001 / RUN-ANALYSIS-001-001; B2 REV-003 aceptado por revisión manual delegada.
- Autorización para esta entrega (REV-001): el usuario ordena implementar el contrato acotado de insuficiencia de velas, ejecutar checks y entregar handoff.
- Archivos editables autorizados: `backend/src/api/analysis.py`, `backend/tests/test_analysis_input_api.py` (NEW), `trading-bot-action-plan.md` (solo fila/ficha BACKEND-ANALYSIS-INPUT-001).
- Restricciones: NO cambiar indicadores, frontend, B2 ni fixtures existentes; NO instalar, reiniciar servicios, llamar cloud, hacer commit/push ni ejecutar otro task.

#### Delta de esta entrega (REV-001)

- `backend/src/api/analysis.py`:
  - Se reemplaza el branch/fallback de fixture sintética en `_resolve_candles` por validación estricta: si candles es None o tiene menos de 50 elementos, se lanza HTTPException 422 con detail exacto `{"code": "INSUFFICIENT_CANDLES", "received": N, "required": 50}`.
  - Se eliminan los imports de `src.fixtures.synthetic` y la lógica de `generate_candles`.
  - Se actualizan comentarios/docstrings que mencionaban fixture automática.
  - Se conserva AnalysisRequest/Response, defaults/límites, cálculos/params y forma del éxito.
  - Se conserva validación high>=low existente y validaciones Pydantic.
- `backend/tests/test_analysis_input_api.py` (NEW):
  - Fixture autouse `_block_network` con guards de red equivalentes al B2 aceptado (AF_UNIX permitido, IP/DNS bloqueados).
  - 7 casos: parametrizado (omitido/null/[]), 49 velas, calculadores no invocados, generate_candles no llamado, positivo con 50 velas, truncación con 60 velas.

#### Ejecución

```
$ cd backend
$ PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -B -m pytest -q -p no:cacheprovider --capture=sys tests/test_analysis_input_api.py
```

- **Exit code:** 0 (todos los tests pasan)
- **Resultados reales:** 7 passed, 1 warning (StarletteDeprecation httpx).
- **Tests verificados:**
  1. `test_insufficient_candles_parametrized[candles_value0]` — candles omitido → 422 INSUFFICIENT_CANDLES received=0.
  2. `test_insufficient_candles_parametrized[candles_value1]` — candles [] → 422 INSUFFICIENT_CANDLES received=0.
  3. `test_insufficient_candles_49` — 49 velas → 422 INSUFFICIENT_CANDLES received=49.
  4. `test_calculate_functions_not_called_on_insufficient` — keltner/macd/fyl no invocados en insuficiencia.
  5. `test_generate_candles_not_called_on_insufficient` — generate_candles no llamado en insuficiencia.
  6. `test_analysis_with_50_candles` — 50 velas → HTTP200, seis keys, data_source provided, MACD 17 puntos desde índice33.
  7. `test_analysis_with_60_candles_truncation` — 60 velas/candles_count=50 respeta truncación a primeras 50.

#### Limitaciones y checks NO EJECUTADOS

- **Instalación limpia:** NO ejecutada (restricción explícita del task).
- **Llamadas cloud / servicios reales:** NO ejecutadas (restricción explícita).
- **Typecheck / build frontend:** NO ejecutados (fuera de alcance).
- **Cobertura de código:** NO medida (no se ejecutó `--cov`).
- **Compatibilidad con otras versiones de httpx/starlette:** NO verificada.
- **Pruebas de integración end-to-end:** NO ejecutadas.
- **NaN/OHLC completo/cierre/orden temporal/fixtures flags/IA:** Fuera de alcance (otros microtasks).

#### Estado final

- **Estado:** EN_REVISION — pendiente aceptación manual del maestro.
- **No se marca COMPLETADO** ni se cierra ACT-C-001 completo.
- **Handoff:** READY_FOR_REVIEW para REV-001 de BACKEND-ANALYSIS-INPUT-001.

---

### Ficha GROUP-BACKEND-001 / RUN-ANALYSIS-001-001 / REV-002 — BACKEND-ANALYSIS-INPUT-001 (corrección de tests)

- **ID backlog / contrato:** BACKEND-ANALYSIS-INPUT-001 / GROUP-BACKEND-001 / RUN-ANALYSIS-001-001 / REV-002
- **Estado BACKEND-ANALYSIS-INPUT-001:** COMPLETADO por revisión manual delegada, conservando la entrega original EN_REVISION como historial.
- **Modo de revisión:** MANUAL

#### Baseline / autorización

- Baseline: REV-001 aceptada técnicamente en el endpoint, pero requiere corrección de tests.
- Autorización para esta entrega (REV-002): corregir tests según instrucciones del maestro. Únicos archivos editables: `backend/tests/test_analysis_input_api.py` y `trading-bot-action-plan.md` (solo fila/ficha BACKEND-ANALYSIS-INPUT-001).
- **NO** existen B3.2/B3.3 autorizadas en el paquete actual. NO crear explicit.py, otro módulo de fixtures, flags ni endpoints. analysis.py y los generadores existentes son SOLO LECTURA.

#### Correcciones aplicadas (REV-002)

1. **Simplificar _build_app():**
   - Se eliminó la manipulación de `app_config` (mock/config).
   - Ahora monta solo `analysis.router` en FastAPI aislado.
   - El restore cierra el TestClient (`client.close()`).
   - Se preservan las guardas de red intactas.

2. **Candles=null: detalle exacto:**
   - Caso parametrizado con `[None, []]` comprueba `detail == {"code": "INSUFFICIENT_CANDLES", "received": 0, "required": 50}`.
   - Se añadió test independiente `test_insufficient_candles_null_exact_detail` que prueba null por sí solo (no se apoya en omitido).

3. **Positivos: comparar TODOS los timestamps MACD:**
   - Ambos tests positivos (`50_candles`, `60_candles_truncation`) comparan todos los timestamps contra `[1700000000000 + i*60000 for i in range(33, 50)]`.
   - Se comprueban las keys exactas de cada punto: `time_ms`, `macd_line`, `signal_line`, `histogram` (set equality).
   - Se mantienen indicadores reales y controles de provided/truncación.

#### Ejecución

```
$ cd backend
$ PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -B -m pytest -q -p no:cacheprovider --capture=sys tests/test_analysis_input_api.py
```

- **Exit code:** 0 (todos los tests pasan)
- **Resultados reales:** 8 passed, 1 warning (StarletteDeprecation httpx).
- **Tests verificados:**
  1. `test_insufficient_candles_parametrized[candles_value0]` — candles=null → 422 detail exacto.
  2. `test_insufficient_candles_parametrized[candles_value1]` — candles=[] → 422 detail exacto.
  3. `test_insufficient_candles_null_exact_detail` — null por sí solo → 422 detail exacto.
  4. `test_insufficient_candles_49` — 49 velas → 422 INSUFFICIENT_CANDLES received=49.
  5. `test_calculate_functions_not_called_on_insufficient` — keltner/macd/fyl no invocados en insuficiencia.
  6. `test_generate_candles_not_called_on_insufficient` — generate_candles no llamado en insuficiencia.
  7. `test_analysis_with_50_candles` — 50 velas → HTTP200, seis keys, data_source provided, MACD 17 puntos con todos los timestamps verificados.
  8. `test_analysis_with_60_candles_truncation` — 60 velas/candles_count=50 respeta truncación a primeras 50, todos los timestamps verificados.

#### Limitaciones y checks NO EJECUTADOS

- **Instalación limpia:** NO ejecutada (restricción explícita del task).
- **Llamadas cloud / servicios reales:** NO ejecutadas (restricción explícita).
- **Typecheck / build frontend:** NO ejecutados (fuera de alcance).
- **Cobertura de código:** NO medida (no se ejecutó `--cov`).
- **Compatibilidad con otras versiones de httpx/starlette:** NO verificada.
- **Pruebas de integración end-to-end:** NO ejecutadas.
- **NaN/OHLC completo/cierre/orden temporal/fixtures flags/IA:** Fuera de alcance (otros microtasks).
- **B3.2/B3.3:** NO existen autorizadas en el paquete actual; no se crearon módulos/flags/endpoints adicionales.

#### Estado final

- **Estado:** EN_REVISION — pendiente aceptación manual del maestro.
- **No se marca COMPLETADO** ni se cierra BACKEND-ANALYSIS-INPUT-001.
- **No se autoaprueba ni se cambian otras filas/normas.**
- **Handoff:** READY_FOR_REVIEW para REV-002 de BACKEND-ANALYSIS-INPUT-001.

### Recepción B3.1 y preparación del siguiente microtask de análisis

- REV-002 aceptada bajo la instrucción del usuario de revisar y preparar el
  próximo task en la misma respuesta: 8 passed, 1 warning, exit 0. Probe público
  independiente de candles omitido devolvió 422 con detalle exacto; el helper
  ya no añade app_config ni contamina el módulo y cierra TestClient.
- Recibido: analysis Git RAW `ebce2be7079bde4234d66db192d9e4437d5ec18d`, tests
  `6b4945a90391f99e98a3f31c49b74651dc578a75`. Todos los timestamps MACD y keys
  exactas se comprobaron; truncación 60→50 conservada. Sin cambio de indicadores.
- Observación de cobertura: la parametrización quedó null/[] y el test separado
  null es redundante; la omisión funciona y fue probada manualmente. Añadir su
  regresión en el test nuevo del siguiente microtask, sin tocar la suite aceptada.
- No existían B3.2/B3.3 secuenciales en el contrato anterior: los puntos fixture
  y éxito provided eran criterios de B3.1. /fixture/data permanece intacto;
  no se autoriza otro módulo de fixtures ni un modo use_fixture.
- Nuevo alcance independiente BACKEND-ANALYSIS-FINITE-001, GROUP-BACKEND-001,
  RUN-FINITE-001-001, futura REV-001. Solo analysis.py (guard finite), test NEW
  test_analysis_finite_api.py y seguimiento propio; no iniciado al prepararlo.
- OHLCV no finitos se rechazan con 422 limpio antes de high/low o indicadores.
  No introducir positividad/precios/ticks/cierre/orden temporal/IA en este paso.
- La nueva baseline se captura tras registrar el cierre; no se hacen instalaciones,
  reinicios, cloud, commit/push/staging o implementación del task siguiente.

---

### Ficha GROUP-BACKEND-001 / RUN-FINITE-001-001 / REV-001 — BACKEND-ANALYSIS-FINITE-001 (B3.2)

- **ID backlog / contrato:** BACKEND-ANALYSIS-FINITE-001 / GROUP-BACKEND-001 / RUN-FINITE-001-001 / REV-001
- **Estado BACKEND-ANALYSIS-FINITE-001:** EN_REVISION (no se cierra ni marca COMPLETADO)
- **Modo de revisión:** MANUAL

#### Baseline / autorización

- Baseline: B3.1 COMPLETADO por revisión delegada; 8 passed + REV-002 aceptada.
- Autorización para esta entrega (REV-001): implementar guard finito, tests acotados y seguimiento. Únicos archivos permitidos: `backend/src/api/analysis.py`, `backend/tests/test_analysis_finite_api.py` (NEW), `trading-bot-action-plan.md` (solo fila/ficha BACKEND-ANALYSIS-FINITE-001).
- Restricciones: NO crear módulos/deps/config/lockfiles, endpoint/flag, reglas de precios positivos, volumen mínimo, timestamp/orden/cierre, estrategia/IA o almacenamiento. NO modificar FYL/MACD/Keltner, frontend, B2, fixture data ni src.config. Sin instalaciones, servicios, red, secretos, cloud, Git de escritura ni tareas siguientes.

#### Delta de esta entrega (REV-001)

- `backend/src/api/analysis.py`:
  - Se añade `import math` al inicio del archivo.
  - Se inserta un guard finito en `analyze()` ANTES de la validación high>=low existente: para cada índice i y campo OHLCV (open, high, low, close, volume), si `math.isfinite(getattr(candle, field_name))` es False, se lanza HTTPException 422 con detail EXACTO `{"code": "NON_FINITE_CANDLE_VALUE", "index": i, "field": field_name}`.
  - El guard rechaza en el primer campo no finito encontrado; no incluye el valor NaN/Inf en el detail (JSON limpio).
  - Se conserva: insuficiencia INSUFFICIENT_CANDLES de B3.1, high>=low existente, todos los campos/defaults/modelos del éxito, volume=0 admitido (es finito), símbolos/timeframe/candles_count/timestamps/indicadores.
- `backend/tests/test_analysis_finite_api.py` (NEW):
  - FastAPI aislado con solo analysis.router; TestClient con context manager; guardas network (AF_UNIX permitido).
  - 15 casos parametrizados: 5 campos × NaN/+Inf/-Inf en índice10, usando `content=json.dumps(payload, allow_nan=True)` para que NaN/Inf lleguen al backend. Cada caso verifica status=422 y body EXACTO; calculadores no invocados (mock).
  - Positivo con datos finitos + volume=0: HTTP200, seis keys, provided, MACD 17 puntos con todos los timestamps verificados (índices33..49) y keys exactas por punto.
  - Regresión de omisión: payload sin candles → 422 INSUFFICIENT_CANDLES exacto.

#### Ejecución

```
$ cd backend
$ PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -B -m pytest -q -p no:cacheprovider --capture=sys tests/test_analysis_finite_api.py tests/test_analysis_input_api.py
```

- **Exit code:** 0 (todos los tests pasan)
- **Resultados reales:** 25 passed, 1 warning (StarletteDeprecation httpx).
- **Tests verificados:**
  - 15 casos parametrizados `test_non_finite_values_parametrized` — cada campo OHLCV con NaN/+Inf/-Inf → 422 NON_FINITE_CANDLE_VALUE index=10 field=X; calculadores no invocados.
  - `test_positive_finite_data_with_real_indicators` — datos finitos + volume=0 → HTTP200, seis keys, provided, MACD 17 puntos con timestamps completos verificados.
  - `test_omitted_candles_regression` — sin candles → 422 INSUFFICIENT_CANDLES exacto.
  - 8 tests de B3.1 (test_analysis_input_api.py) — todos pasan sin modificación.

#### Limitaciones y checks NO EJECUTADOS

- **Instalación limpia:** NO ejecutada (restricción explícita del task).
- **Llamadas cloud / servicios reales:** NO ejecutadas (restricción explícita).
- **Typecheck / build frontend:** NO ejecutados (fuera de alcance).
- **Cobertura de código:** NO medida (no se ejecutó `--cov`).
- **Compatibilidad con otras versiones de httpx/starlette:** NO verificada.
- **Pruebas de integración end-to-end:** NO ejecutadas.
- **Overflow de resultados por precios enormes finitos:** Fuera de alcance.
- **open_time, OHLC coherencia completa, precios negativos, gaps/orden/cierre:** Fuera de alcance (otros microtasks).

#### Estado final

- **Estado:** EN_REVISION — pendiente aceptación manual del maestro.
- **No se marca COMPLETADO** ni se cierra BACKEND-ANALYSIS-FINITE-001.
- **No se autoaprueba ni se cambian otras filas/normas.**
- **Handoff:** READY_FOR_REVIEW para REV-001 de BACKEND-ANALYSIS-FINITE-001.

### Recepción B3.2 y preparación B3.3

- B3.2 REV-001 aceptada por revisión manual delegada según la instrucción del
  usuario de revisar y preparar el siguiente task en la misma respuesta.
  Check reejecutado: 25 passed, 1 warning, exit 0; no red ni servicios.
- Código recibido Git RAW `8dec6bba593f0d65183b53eec78abf95721f69e7`, test finite
  `0a82f350f7a344b3ffc04c451a4042ea47374dc0`. B3.1 tests conservados
  `6b4945a90391f99e98a3f31c49b74651dc578a75`; restantes contenidos e índice intactos.
- Base de B3.2: 23 M + 25 untracked; tras su nuevo test: 23 M + 26 untracked.
  No confundir status inicial con final ni nombres de status con prueba de contenido.
- Siguiente contrato BACKEND-ANALYSIS-RANGE-001, GROUP-BACKEND-001,
  RUN-RANGE-001-001, futura REV-001. Solo guard de open/close inclusivo en
  analysis.py, NEW test_analysis_range_api.py y seguimiento propio; no iniciado.
- Preservar errores de non-finite, high<low e insuficiencia; no imponer precios
  positivos, volumen mínimo, filtros de exchange, timestamps/orden/cierre,
  IA/riesgo ni fórmulas del método. No modificar indicadores ni frontend.
- Nueva captura posterior al registro de cierre; esta preparación no instala,
  reinicia servicios, implementa el nuevo contrato ni hace cloud/commit/push.

---

### Ficha GROUP-BACKEND-001 / RUN-RANGE-001-001 / REV-001 — BACKEND-ANALYSIS-RANGE-001 (B3.3)

- **ID backlog / contrato:** BACKEND-ANALYSIS-RANGE-001 / GROUP-BACKEND-001 / RUN-RANGE-001-001 / REV-001
- **Estado BACKEND-ANALYSIS-RANGE-001:** EN_REVISION (no se cierra ni marca COMPLETADO)
- **Modo de revisión:** MANUAL

#### Baseline / autorización

- Baseline: B3.2 COMPLETADO por revisión manual delegada; 25 passed.
- Autorización para esta entrega (REV-001): implementar guard de rango open/close inclusivo, tests acotados y seguimiento. Únicos archivos permitidos: `backend/src/api/analysis.py`, `backend/tests/test_analysis_range_api.py` (NEW), `trading-bot-action-plan.md` (solo fila/ficha BACKEND-ANALYSIS-RANGE-001).
- Restricciones: NO crear módulos/deps/config/lockfiles, endpoint/flag, reglas de precios positivos, volumen mínimo, timestamp/orden/cierre, estrategia/IA o almacenamiento. NO modificar indicadores, cuerpo Request/Response, /fixture/data, MarketEngine, B2, sistema monetario ni estrategia. Sin instalaciones, servicios, red, secretos, cloud, Git de escritura ni siguiente task.

#### Delta de esta entrega (REV-001)

- `backend/src/api/analysis.py`:
  - Se añade un guard dentro del loop high>=low existente: tras descartar high<low, se comprueba open y close (en ese orden) de cada vela proporcionada. Ambos deben estar dentro del intervalo inclusivo [low,high].
  - Si uno está fuera → HTTPException 422 con detail EXACTO `{"code": "CANDLE_PRICE_OUT_OF_RANGE", "index": i, "field": "open"}` o `"close"`.
  - El rechazo ocurre antes de _resolve_candles/indicadores; no se truncar el input ni reparar la vela.
  - Se conserva: guard NON_FINITE_CANDLE_VALUE previo, error/string existente high<low, mínimo50, símbolos/timeframe/candles_count, timestamps/cálculos y seis keys del éxito provided.
  - Se acepta igualdad en low/high; no se añaden epsilons, redondeos, clamping ni máximos de precios.
- `backend/tests/test_analysis_range_api.py` (NEW):
  - FastAPI aislado con solo analysis.router; TestClient context manager; guardas network (AF_UNIX permitido).
  - 4 casos negativos parametrizados: open/close a 98 (bajo low=99) o 102 (sobre high=101) en índice10. Status=422, body EXACTO; calculadores no invocados.
  - 4 casos positivos: open/close igual a low=99 o high=101. HTTP200 con indicadores REALES, seis keys, provided, MACD17 y timestamps completos33..49.
  - Regresión high<low: high98/low99 en índice10 → 422 con detail existente `Candle 10: high (98.0) debe ser >= low (99.0)`; no se cambia al nuevo código ni a NON_FINITE.

#### Ejecución

```
$ cd backend
$ PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -B -m pytest -q -p no:cacheprovider --capture=sys tests/test_analysis_range_api.py tests/test_analysis_finite_api.py tests/test_analysis_input_api.py
```

- **Exit code:** 0 (todos los tests pasan)
- **Resultados reales:** 34 passed, 1 warning (StarletteDeprecation httpx).
- **Tests verificados:**
  - 4 casos negativos `test_price_out_of_range_parametrized` — open/close fuera de [low,high] → 422 CANDLE_PRICE_OUT_OF_RANGE index=10 field=X; calculadores no invocados.
  - 4 casos positivos `test_price_at_boundary_inclusive` — open/close en límites inclusivos → HTTP200, seis keys, provided, MACD17 con timestamps completos verificados.
  - `test_high_low_regression` — high<low → 422 detail existente string; no se cambia a NON_FINITE ni CANDLE_PRICE_OUT_OF_RANGE.
  - 25 tests de B3.2 (test_analysis_finite_api.py) — todos pasan sin modificación.
  - 8 tests de B3.1 (test_analysis_input_api.py) — todos pasan sin modificación.

#### Limitaciones y checks NO EJECUTADOS

- **Instalación limpia:** NO ejecutada (restricción explícita del task).
- **Llamadas cloud / servicios reales:** NO ejecutadas (restricción explícita).
- **Typecheck / build frontend:** NO ejecutados (fuera de alcance).
- **Cobertura de código:** NO medida (no se ejecutó `--cov`).
- **Compatibilidad con otras versiones de httpx/starlette:** NO verificada.
- **Pruebas de integración end-to-end:** NO ejecutadas.
- **Precios positivos, volumen mínimo, ticks, orden temporal/cierre/IA:** Fuera de alcance (otros microtasks).

#### Estado final

- **Estado:** EN_REVISION — pendiente aceptación manual del maestro.
- **No se marca COMPLETADO** ni se cierra BACKEND-ANALYSIS-RANGE-001.
- **No se autoaprueba ni se cambian otras filas/normas.**
- **Handoff:** READY_FOR_REVIEW para REV-001 de BACKEND-ANALYSIS-RANGE-001.

### Recepción B3.3 y preparación temporal

- REV-001 aceptada por revisión manual delegada: 34 passed, 1 warning, exit 0.
  Rango inclusivo y cálculos reales verificados; no se modificaron suites previas,
  contenido ajeno ni índice. No red/modelo real, instalación o reinicio.
- Código recibido Git RAW `2a64b9ef32e8074bb7b5a8a4f973a3978c387fb3`, test rango
  `a9f2e0663920415dc7a3d0f2faac93ad6a5b9530`. Tests finite e input conservados.
- Base B3.3: 23 M + 26 untracked; final: 23 M + 27 untracked al crear su test.
- Nuevo contrato BACKEND-ANALYSIS-TIME-001, GROUP-BACKEND-001,
  RUN-TIME-001-001, futura REV-001. Solo guard temporal en analysis.py, NEW
  test_analysis_time_api.py y seguimiento propio; no iniciado en preparación.
- open_time debe aumentar estrictamente en toda la secuencia proporcionada.
  No ordenar, deduplicar, rellenar, certificar frescura/cierre, exigir cadencia
  del timeframe ni imponer nuevas restricciones de timestamp absoluto.
- Se conserva el orden de errores finite → high<low → range y la insuficiencia
  para inputs válidos salvo longitud. Snapshot propio, FYL causal y método/IA
  quedan fuera de este microtask. Captura después del cierre y STOP sin Git/cloud.

---

### Ficha GROUP-BACKEND-001 / RUN-TIME-001-001 / REV-001 — BACKEND-ANALYSIS-TIME-001 (B3.4)

- **ID backlog / contrato:** BACKEND-ANALYSIS-TIME-001 / GROUP-BACKEND-001 / RUN-TIME-001-001 / REV-001
- **Estado BACKEND-ANALYSIS-TIME-001:** EN_REVISION (no se cierra ni marca COMPLETADO)
- **Modo de revisión:** MANUAL

#### Baseline / autorización

- Baseline: B3.3 COMPLETADO por revisión manual delegada; 34 passed, sin red.
- Autorización para esta entrega (REV-001): añadir guard open_time estrictamente creciente, tests acotados y seguimiento. Únicos archivos permitidos: `backend/src/api/analysis.py`, `backend/tests/test_analysis_time_api.py` (NEW), `trading-bot-action-plan.md` (solo fila/ficha BACKEND-ANALYSIS-TIME-001).
- Restricciones: NO cambiar modelos/defaults, source/success keys, cálculos/params, timeframe, _resolve_candles ni /fixture/data. No schema/broker/SQLite/flags/deps/config/lockfiles, frontend o generadores. Sin instalaciones, secretos, servicios, cloud ni Git de escritura. No nuevas restricciones de timestamp absoluto, cadencia, frescura, gaps, cierre, precios/volumen ni filtros exchange.

#### Delta de esta entrega (REV-001)

- `backend/src/api/analysis.py`:
  - Se añade un guard tras las validaciones finite/high<low/range y antes de _resolve_candles: para i>=1, open_time[i] debe ser MAYOR que open_time[i-1]. Si es igual o menor → HTTPException 422 con detail EXACTO `{"code": "NON_INCREASING_CANDLE_TIME", "index": i}`.
  - Se valida la lista completa aunque candles_count luego seleccione menos (como las validaciones existentes). None/[] no generan error temporal: se conserva INSUFFICIENT_CANDLES.
  - No se ordena, deduplica, rellena gaps, descarta colas inválidas ni repara input.
  - Se acepta cadencia irregular creciente y gaps; no se fuerza intervalos/timeframe/alineación.
- `backend/tests/test_analysis_time_api.py` (NEW):
  - FastAPI aislado con solo analysis.router; TestClient context manager; guardas network (AF_UNIX permitido).
  - 4 casos negativos parametrizados: open_time duplicado o descendente en índice1 o 10. Status=422, body EXACTO; calculadores no invocados.
  - Positivo con 50 tiempos estrictamente crecientes IRREGULARES (base_ms+i*60000+i*i): HTTP200, seis keys, provided, MACD17 con TODA la secuencia timestamps[33:50] y mismas keys por punto.
  - Cola no seleccionada: 60 velas, candles_count=50; duplicado en índice55 → 422 con índice55, no ignora la cola.

#### Ejecución

```
$ cd backend
$ PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -B -m pytest -q -p no:cacheprovider --capture=sys tests/test_analysis_time_api.py tests/test_analysis_range_api.py tests/test_analysis_finite_api.py tests/test_analysis_input_api.py
```

- **Exit code:** 0 (todos los tests pasan)
- **Resultados reales:** 40 passed, 1 warning (StarletteDeprecation httpx).
- **Tests verificados:**
  - 4 casos negativos `test_non_increasing_time_parametrized` — open_time duplicado/descendente en índice1 o 10 → 422 NON_INCREASING_CANDLE_TIME index=X; calculadores no invocados.
  - `test_positive_irregular_timestamps` — tiempos irregulares pero crecientes (base_ms+i*60000+i*i) → HTTP200, seis keys, provided, MACD17 con TODA la secuencia timestamps[33:50] verificados.
  - `test_unselected_tail_duplicate` — 60 velas/candles_count=50, duplicado en índice55 → 422 index=55; no ignora la cola.
  - 34 tests de B3.3 (test_analysis_range_api.py) — todos pasan sin modificación.
  - 25 tests de B3.2 (test_analysis_finite_api.py) — todos pasan sin modificación.
  - 8 tests de B3.1 (test_analysis_input_api.py) — todos pasan sin modificación.

#### Limitaciones y checks NO EJECUTADOS

- **Instalación limpia:** NO ejecutada (restricción explícita del task).
- **Llamadas cloud / servicios reales:** NO ejecutadas (restricción explícita).
- **Typecheck / build frontend:** NO ejecutados (fuera de alcance).
- **Cobertura de código:** NO medida (no se ejecutó `--cov`).
- **Compatibilidad con otras versiones de httpx/starlette:** NO verificada.
- **Pruebas de integración end-to-end:** NO ejecutadas.
- **Timestamp absoluto, cadencia del timeframe, frescura/procedencia/cierre:** Fuera de alcance (no se imponen nuevas restricciones).

#### Estado final

- **Estado:** EN_REVISION — pendiente aceptación manual del maestro.
- **No se marca COMPLETADO** ni se cierra BACKEND-ANALYSIS-TIME-001.
- **No se autoaprueba ni se cambian otras filas/normas.**
- **Handoff:** READY_FOR_REVIEW para REV-001 de BACKEND-ANALYSIS-TIME-001.

### Recepción B3.4 y preparación de entrada propia

- B3.4 REV-001 aceptada por revisión delegada: 40 passed, 1 warning, exit 0.
  Código recibido Git RAW `fe994bab407af0bca2adfd06cc829cd6f1fe0de0`, test time
  `94b3869711b3aee7c6f404db8794260c619c96ca`. Suites previas, contenido ajeno
  e índice conservados. No red/proveedor, instalación o restart.
- Base anterior 23 M + 27 untracked; final 23 M + 28 al añadir su test.
- MarketEngine ya ofrece closed_candles: ordenado y con copias de diccionarios;
  snapshot recalcula aptitud/frescura. No hace falta un nuevo getter. El REST
  histórico tiene fixture fallback y no es la fuente de este adaptador interno.
- Nuevo contrato BACKEND-MARKET-ANALYSIS-INPUT-001, GROUP-BACKEND-001,
  RUN-MARKET-INPUT-001-001, futura REV-001: NEW analysis_market.py (helper),
  NEW test_analysis_market_input.py y seguimiento propio; sin implementación ahora.
- Preparar AnalysisRequest desde las últimas N cerradas del engine (50–500,
  mínimo disponible50), conservar close_time/estado fuera de CandleInput y
  ejecución false. Usar symbol de snapshot y config.kline_interval públicos.
  No leer current_bar/builder privados, ni ordenar/deduplicar/crear fixtures.
- Este resultado no es ScenarioSnapshot ni evidencia del método: no calcular
  indicadores, resistencia/zonas inventadas, prompt, IA o persistencia. La conversión
  posterior a N02 necesita mapeo de evidencias y cierres precisos en otro microtask.

---

### Ficha GROUP-BACKEND-001 / RUN-MARKET-INPUT-001-001 / REV-001 — BACKEND-MARKET-ANALYSIS-INPUT-001 (B4.1)

- **ID backlog / contrato:** BACKEND-MARKET-ANALYSIS-INPUT-001 / GROUP-BACKEND-001 / RUN-MARKET-INPUT-001-001 / REV-001
- **Estado BACKEND-MARKET-ANALYSIS-INPUT-001:** EN_REVISION (no se cierra ni marca COMPLETADO)
- **Modo de revisión:** MANUAL

#### Baseline / autorización

- Baseline: B3.4 COMPLETADO por revisión manual delegada; 40 passed, sin red.
- Autorización para esta entrega (REV-001): crear helper interno `analysis_market.py`, tests offline `test_analysis_market_input.py` y seguimiento. Únicos archivos permitidos: `backend/src/api/analysis_market.py` (NEW), `backend/tests/test_analysis_market_input.py` (NEW), `trading-bot-action-plan.md` (solo fila/ficha BACKEND-MARKET-ANALYSIS-INPUT-001).
- Restricciones: NO crear endpoint/route, nuevo getter, ScenarioSnapshot, evidencias, prompt/IA, resistencia/zonas inventadas, trading/riesgo. No deps/config/lockfiles, frontend o fixtures. Sin instalaciones, secretos, servicios, cloud ni Git de escritura. No modificar engine/builder/models/cálculos/suites previas/main.py/capabilities.

#### Delta de esta entrega (REV-001)

- `backend/src/api/analysis_market.py` (NEW):
  - `MarketAnalysisInputError(ValueError)` con atributo `code` string y mensajes constantes, sin dumps de estado/precios.
  - `PreparedMarketAnalysis`: dataclass frozen con request:AnalysisRequest, data_source:str, as_of_close_time_ms:int, market_state:dict (copia profunda) y execution_available:Literal[False]=False.
  - `prepare_market_analysis(engine, *, symbol, candles_count=200)` sin async/router/cálculos:
    1. candles_count int NO bool, entre 50-500 → INVALID_CANDLES_COUNT; engine None → MARKET_NOT_READY.
    2. Primera lectura snapshot: entries_allowed True → si no MARKET_NOT_READY.
    3. snapshot.symbol == symbol → MARKET_SYMBOL_MISMATCH; config.kline_interval == "1m" → MARKET_INTERVAL_UNSUPPORTED.
    4. Leer `engine.closed_candles`, tomar ÚLTIMAS N (`[-candles_count:]`); <50 → INSUFFICIENT_CANDLES. Preserva orden engine, sin HTTP/builder/current_bar/ordenamiento/deduplicación.
    5. Segunda lectura snapshot/config: sigue apto, mismo símbolo/intervalo; last_closed_close_time == close_time última cerrada → MARKET_CONTEXT_CHANGED si no coincide.
    6. Construir CandleInput directamente desde open_time/OHLCV; AnalysisRequest symbol/timeframe="1m"/candles_count/candles. No llamar analyze/calculate_*.
    7. Retornar PreparedMarketAnalysis con data_source="market_engine", as_of exacto, market_state copia profunda independiente, execution false.

- `backend/tests/test_analysis_market_input.py` (NEW):
  - FakeEngine mínimo con config.kline_interval, snapshot() y closed_candles que representa las APIs reales; sin mocks de resultado esperado.
  - Estado preparado: connected/completo/no stale/gaps0/entries_allowed=True/symbol; close_time coherente.
  - Genera60 barras cerradas por loop: open_time=base+i*60000, close_time=open_time+59999, is_closed=True, OHLC100/101/99/100, volume0.
  - 14 tests: N50de60 (opens índices10..59, as_of close59, source market_engine, execution false), N200de60 (60 sin relleno, count200 preservado), engine None/inapto (MARKET_NOT_READY), symbol distinto/interval5m (códigos definidos), 49 cerradas (INSUFFICIENT_CANDLES), count0/501/True (INVALID_CANDLES_COUNT), close_time mismatch (MARKET_CONTEXT_CHANGED), mutación request/market_state no altera fuentes, guardas calculate_* no invocados.

#### Ejecución

```
$ cd backend
$ PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -B -m pytest -q -p no:cacheprovider --capture=sys tests/test_analysis_market_input.py
```

- **Exit code:** 0 (todos los tests pasan)
- **Resultados reales:** 14 passed.
- **Tests verificados:**
  - `test_50_from_60` — opens índices10..59, as_of close59, source market_engine, execution false, market_state copia independiente.
  - `test_200_from_60` — 60 velas sin relleno, candles_count=200 preservado.
  - `test_engine_none` / `test_entries_not_allowed` — MARKET_NOT_READY.
  - `test_symbol_mismatch` / `test_interval_5m` — MARKET_SYMBOL_MISMATCH / MARKET_INTERVAL_UNSUPPORTED.
  - `test_insufficient_candles` — INSUFFICIENT_CANDLES con49 cerradas.
  - `test_invalid_candles_count[0/49/501/True]` — INVALID_CANDLES_COUNT (parametrizado).
  - `test_close_time_mismatch` — MARKET_CONTEXT_CHANGED.
  - `test_mutation_isolation` — mutar request.candles y market_state no altera fuentes del fake.
  - `test_no_indicators_called` — calculate_keltner/macd/fyl no invocados.

#### Limitaciones y checks NO EJECUTADOS

- **Instalación limpia:** NO ejecutada (restricción explícita del task).
- **Llamadas cloud / servicios reales:** NO ejecutadas (restricción explícita).
- **Typecheck / build frontend:** NO ejecutados (fuera de alcance).
- **Cobertura de código:** NO medida (no se ejecutó `--cov`).
- **Compatibilidad con otras versiones de httpx/starlette:** NO verificada.
- **Pruebas de integración end-to-end:** NO ejecutadas.
- **Endpoint / route / controller:** Fuera de alcance (no se crea).
- **ScenarioSnapshot / evidencias / prompt / IA:** Fuera de alcance.

#### Estado final

- **Estado:** EN_REVISION — pendiente aceptación manual del maestro.
- **No se marca COMPLETADO** ni se cierra BACKEND-MARKET-ANALYSIS-INPUT-001.
- **No se autoaprueba ni se cambian otras filas/normas.**
- **Handoff:** READY_FOR_REVIEW para REV-002 de BACKEND-MARKET-ANALYSIS-INPUT-001.

---

### Ficha GROUP-BACKEND-001 / RUN-MARKET-INPUT-001-001 / REV-002 — BACKEND-MARKET-ANALYSIS-INPUT-001 (B4.1)

- **ID backlog / contrato:** BACKEND-MARKET-ANALYSIS-INPUT-001 / GROUP-BACKEND-001 / RUN-MARKET-INPUT-001-001 / REV-002
- **Estado BACKEND-MARKET-ANALYSIS-INPUT-001:** EN_REVISION (no se cierra ni marca COMPLETADO)
- **Modo de revisión:** MANUAL

#### Baseline / autorización

- Baseline histórica: REV-001 fue REQUIRES_CHANGES, con 14 tests reportados; REV-002 corrige segunda lectura y guardas HTTP. El checkpoint WIP no constituye aceptación.
- Autorización para esta corrección (REV-002): preservar códigos de PRIMERA lectura (snap1), cambiar SEGUNDA lectura (snap2) a MARKET_CONTEXT_CHANGED, añadir dos regresiones FakeEngine con cambio entre snap1/snap2, completar fixture network con httpx blocking. Únicos archivos permitidos: `backend/src/api/analysis_market.py`, `backend/tests/test_analysis_market_input.py`, `trading-bot-action-plan.md` (solo seguimiento de este task).
- Restricciones: NO reescribir el helper, NO crear endpoint/route/nuevo getter, NO instalar deps ni servicios. No modificar engine/builder/models/cálculos/suites previas/main.py/capabilities/frontend/analysis.py. Sin instalaciones, secretos, servicios, cloud ni Git de escritura.

#### Delta de esta entrega (REV-002)

- `backend/src/api/analysis_market.py`:
  - **Preservar códigos de PRIMERA lectura:** snap1.symbol != symbol → MARKET_SYMBOL_MISMATCH; snap1.config.kline_interval != "1m" → MARKET_INTERVAL_UNSUPPORTED (sin cambios).
  - **Cambiar códigos de SEGUNDA lectura:** snap2.symbol != symbol → ahora MARKET_CONTEXT_CHANGED (antes MARKET_SYMBOL_MISMATCH); snap2.config.kline_interval != "1m" → ahora MARKET_CONTEXT_CHANGED (antes MARKET_INTERVAL_UNSUPPORTED). entries_allowed no True en snap2 → conserva MARKET_NOT_READY.

- `backend/tests/test_analysis_market_input.py`:
  - **Completar fixture de red:** añadido `import httpx`, función `blocked_async`, y monkeypatch de `httpx.HTTPTransport.handle_request` y `httpx.AsyncHTTPTransport.handle_async_request`. Se mantiene AF_UNIX permitido, IP/DNS bloqueados y restauración monkeypatch.
  - **FakeEngine mejorado:** añade `second_symbol` y `second_kline_interval` que cambian el retorno de `snapshot()` y `config` a partir de la segunda llamada (simula cambio entre snap1 y snap2). `_snapshot_call_count` controla cuándo aplicar los valores alternativos.
  - **Dos regresiones añadidas:**
    - `test_second_snapshot_symbol_change`: FakeEngine con second_symbol="ETH/USDT" → snap1 válido (BTC/USDT), snap2 cambia símbolo → MARKET_CONTEXT_CHANGED.
    - `test_second_snapshot_interval_change`: FakeEngine con second_kline_interval="5m" → snap1 válido (1m), snap2 cambia intervalo → MARKET_CONTEXT_CHANGED.

#### Ejecución

```
$ cd backend
$ PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -B -m pytest -q -p no:cacheprovider --capture=sys tests/test_analysis_market_input.py
```

- **Exit code:** 0 (todos los tests pasan)
- **Resultados reales:** 16 passed.
- **Tests verificados:**
  - `test_50_from_60` — opens índices10..59, as_of close59, source market_engine, execution false, market_state copia independiente.
  - `test_200_from_60` — 60 velas sin relleno, candles_count=200 preservado.
  - `test_engine_none` / `test_entries_not_allowed` — MARKET_NOT_READY.
  - `test_symbol_mismatch` / `test_interval_5m` — MARKET_SYMBOL_MISMATCH / MARKET_INTERVAL_UNSUPPORTED (primera lectura).
  - `test_insufficient_candles` — INSUFFICIENT_CANDLES con49 cerradas.
  - `test_invalid_candles_count[0/49/501/True]` — INVALID_CANDLES_COUNT (parametrizado).
  - `test_close_time_mismatch` — MARKET_CONTEXT_CHANGED.
  - **`test_second_snapshot_symbol_change`** — PRIMERA lectura válida, segunda cambia símbolo → MARKET_CONTEXT_CHANGED (regresión REV-002).
  - **`test_second_snapshot_interval_change`** — PRIMERA lectura válida, segunda cambia intervalo → MARKET_CONTEXT_CHANGED (regresión REV-002).
  - `test_mutation_isolation` — mutar request.candles y market_state no altera fuentes del fake.
  - `test_no_indicators_called` — calculate_keltner/macd/fyl no invocados.

#### Limitaciones y checks NO EJECUTADOS

- **Instalación limpia:** NO ejecutada (restricción explícita del task).
- **Llamadas cloud / servicios reales:** NO ejecutadas (restricción explícita).
- **Typecheck / build frontend:** NO ejecutados (fuera de alcance).
- **Cobertura de código:** NO medida (no se ejecutó `--cov`).
- **Compatibilidad con otras versiones de httpx/starlette:** NO verificada.
- **Pruebas de integración end-to-end:** NO ejecutadas.
- **Endpoint / route / controller:** Fuera de alcance (no se crea).
- **ScenarioSnapshot / evidencias / prompt / IA:** Fuera de alcance.

#### Estado final

- **Estado:** EN_REVISION — pendiente aceptación manual del maestro.
- **No se marca COMPLETADO** ni se cierra BACKEND-MARKET-ANALYSIS-INPUT-001.
- **No se autoaprueba ni se cambian otras filas/normas.**
- **Handoff:** READY_FOR_REVIEW para REV-002 de BACKEND-MARKET-ANALYSIS-INPUT-001.

### Aceptación manual delegada B4.1 REV-002 — 2026-10-07

- Base real verificada: master, HEAD `4383372b846ca019cd8008f96b464dd82aa3e49e`, árbol e índice limpios al iniciar. Sustituye el HEAD antiguo para reanudar; no se restaura código.
- Leídos helper, tests, ficha y contratos pertinentes; contrastadas APIs públicas del motor. Primera lectura conserva mismatch/unsupported; cambios de símbolo/intervalo en segunda devuelven MARKET_CONTEXT_CHANGED y segunda inaptitud MARKET_NOT_READY.
- Desde backend: `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -B -m pytest -q -p no:cacheprovider --capture=sys tests/test_analysis_market_input.py` → exit 0, 16 passed en 0.35s.
- Probe independiente en memoria → exit 0: DNS/IP y HTTP sync/async bloqueados, AF_UNIX delegado, tipos inválidos rechazados, últimas 500 de 510, segunda lectura no apta y copia profunda anidada correctos.
- Git diff/diff cached vacíos antes del registro; diff-check y cached-check correctos. Código/tests coinciden con HEAD. Solo se modifica seguimiento propio; ningún cambio de código, índice, commit o push.
- Aceptada REV-002: BACKEND-MARKET-ANALYSIS-INPUT-001 COMPLETADO. Se corrige la baseline documental falsa de aceptación de REV-001; las fichas EN_REVISION conservan su carácter histórico.
- Límite: no hay delta aislado recuperado de REV-001 a REV-002, ambos archivos entraron en el checkpoint WIP. No se certifican los otros módulos de ese commit, instalación limpia, suite completa, integración real ni servicios.
- Siguiente microtask preparado, NO implementado: API de análisis desde esta entrada propia reutilizando analyze; sin N02, IA, persistencia, cartera o ejecución. Su contrato y baseline documental pendiente se entregan en la respuesta de revisión.

## 14. Reconciliación selectiva documental — 2026-10-07

### 14.1. Estado y autoridad — READY_FOR_REVIEW

Esta reconciliación local está **READY_FOR_REVIEW**, pendiente de aceptación
del usuario; no se autoaprueba ni registra nuevos cierres de implementación.
La autorización comprende únicamente `trading-bot-action-plan.md`,
`trading-bot-development-plan.md` y `trading-console-visual-brief.md` de raíz.
Las tres propuestas fechadas son fuentes sin autoridad y no se modifican;
tampoco se modifica la plantilla Qwen ni se reescribe el paquete preparado.

Se conserva el contenido preexistente de raíz identificado por el hash verificado
`88e244837c1d77d2f306d7ba385ea812ef6f48fd` en sus tres piezas de B4.1:
fila COMPLETADO con 16 tests y probe; corrección de baseline REV-001 rechazada
con 14 tests reportados; bloque de aceptación manual delegada del 2026-10-07.
La propuesta identificada por `35f669511fc46e3c1bc5137f37c9acc037d4ee7f`
no sustituye esa aceptación ni rebaja su evidencia a un reporte sin probe.
Los hashes iniciales se verificaron mediante Git en solo lectura; identifican
contenido previo, no una aprobación de la reconciliación ni una baseline futura.

La aceptación B4.1 conserva sus límites: no certifica un delta aislado entre
revisiones, otros módulos del checkpoint, instalación limpia, suite completa
o integración real. Sus fichas anteriores son historia, no revisiones activas.

### 14.2. Mapping acordado sin duplicación de responsables

| Mejora | Responsable de backlog | Alcance documental |
|---|---|---|
| Contratos, catálogo compartido UI/IA y versiones de estrategia/datos | ACT-C-001 | Fuente común, parámetros, unidades, límites, causalidad y compatibilidad; soporte no equivale a salud/ejecución |
| Persistencia de versiones, sesiones y preferencias | ACT-P-001 | Referencias reconstruibles y conservación tras reinicio según contratos disponibles |
| Replay y backtest reproducible | ACT-L-001 | Causalidad, fuente/reloj y política explícita de costos/fills; reutilizar contratos existentes |
| Experimentos y resultados versionados/normalizados | ACT-L-002 | Al menos tres variantes por hipótesis, comparación A/B/C, todos los intentos y costos; periodos separados y comparables; no tres órdenes |
| Laboratorio e inspector de operaciones | ACT-L-003 | Resultados reales, unidades, gráfico/decisiones enlazados y estados honestos |
| Evaluación congelada y prospectiva | ACT-Q-001 | Desarrollo/validación/test separados, sin ajuste contra evaluación reservada; IA evaluada separadamente |
| Paneles contextuales y preferencias persistidas | ACT-T-001/002/003/004/005; ACT-B-001; ACT-P-001 | Terminal/Bot por contexto y capacidad real, con persistencia y QA |
| Contexto, asesoría y auditoría IA | ACT-A-002; ACT-A-003; ACT-A-001 | Referencias reales, vigencia y trazabilidad; ninguna autoridad sobre límites u órdenes |
| Herramientas estructuradas posteriores de investigación Qwen | ACT-A-004 | Único ID nuevo: interfaz de herramientas sin equivalente completo; contratos, permisos, límites y auditoría |

No se crean ACT-C-002, ACT-C-003 ni ACT-L-004. Las ampliaciones se subdividirán
en microtasks de sus responsables existentes; no introducen dependencias nuevas
en B4.2, en la Terminal mínima ni en la puerta H4.

### 14.3. Preparación histórica B4.2 — superada por aceptación en §14.7

- **Task / grupo / run / revisión preparada:** BACKEND-MARKET-ANALYSIS-API-001 /
  GROUP-BACKEND-001 / RUN-MARKET-API-001-001 / REV-001.
- **Dependencia aceptada:** BACKEND-MARKET-ANALYSIS-INPUT-001, B4.1 REV-002.
- **Referencia de contrato:** paquete compacto previo de instrucciones de
  30–60 líneas, ya preparado y entregado en la respuesta de revisión B4.1.
  Este resumen lo referencia; no lo reemite ni reescribe su contrato, esquema,
  comportamiento, errores, checks o aceptación.
- **Ruta prevista:** usar el helper que produce PreparedMarketAnalysis y
  reutilizar `await analyze(...)`. No crear otro cálculo ni modificar
  silenciosamente el paquete o el helper aceptado.
- **Allowlist del paquete:** `backend/src/api/analysis.py`, NEW
  `backend/tests/test_analysis_market_api.py` y `trading-bot-action-plan.md`
  exclusivamente para seguimiento propio. `main.py` no pertenece al paquete.
- **Precondición pendiente de actualización antes de ejecutar:** `master`,
  HEAD observado `4383372b846ca019cd8008f96b464dd82aa3e49e`.
  La precondición previa ha quedado obsoleta por las propuestas untracked y
  la reconciliación documental. Fijar baseline del árbol posterior, propiedad
  de cambios y evidencia segura; no reutilizar árboles históricos ni asumir
   limpieza a partir de HEAD. El estado verificado 7 tracked modificados + 10 untracked no sustituye
   la evidencia completa del contenido. Actualizar la precondición no autoriza
   cambiar el alcance ni ejecutar desde esta reconciliación.
- **Estado y parada:** PREPARADO, no iniciado. Esta edición no implementa,
  ejecuta checks técnicos, emite aceptación técnica ni inicia otro task. No N02, IA,
  persistencia, cartera, órdenes o ejecución en este alcance.

### 14.4. Mínimo funcional, ampliaciones y patrones Fincept

Se conservan sin cambios las puertas, dependencias existentes y orden de hitos
H0 → H1 → H2 → H3/H4 → H5. El mínimo funcional se acepta por los contratos
vigentes y evidencia real: las ideas de catálogo ampliado, jobs o herramientas
research no añaden puertas de H4 ni bloqueos al backend inmediato.

El catálogo extensible y la persistencia ampliada de preferencias son objetivos
de sus responsables existentes, a subdividir y aceptar en microtasks propios;
no amplían los cierres mínimos de H0/H1/H2/H4. Sus versiones futuras no son
precondiciones del paquete B4.2 ni de las capacidades mínimas ya contratadas.

ACT-A-004 es ampliación posterior P2, PENDIENTE y no iniciada. Usa ACT-C-001,
ACT-A-001 y ACT-L-002 cuando estén disponibles; no depende de IDs nuevos de
catálogo/jobs. ACT-A-002 mantiene contexto, ACT-A-003 asesoría y ACT-A-001
auditoría. Las herramientas no ejecutan órdenes, shell ni cambios de estrategia
activa y no tienen autoridad financiera. Límites concretos se fijan en su futuro
microtask, sin inventar aquí cuotas, infraestructura o políticas económicas.

Cada experimento conserva versiones de datos/estrategia/código/motor/riesgo,
costos, semilla cuando aplique y todos los intentos, incluidos fallidos y
descartados. Cambios de parámetros crean otra versión/run. La evaluación
reservada no guía ajustes; si se usa para ajustar, pasa a desarrollo. Comparar
periodos y exposición pertinentes, con unidades y métricas indefinidas visibles.

Fincept inspira patrones de catálogo común, investigación reproducible,
reportes/inspector y paneles contextuales. No se importa código, frameworks,
brokers o plataformas ni se adopta su arquitectura por referencia. Se conservan
React, FastAPI, Lightweight Charts y el trabajo previo; no crear otro DataHub,
plataforma de jobs, infraestructura de automatización o sistema multi-motor
por esta reconciliación. Spot y exclusión LIVE permanecen vigentes; el método
exacto sigue sujeto a ACT-E-001, sin fórmulas ni evidencias inventadas.

### 14.5. Handoff documental y pendientes de revisión

- **Estado de esta entrega:** READY_FOR_REVIEW; pendiente aceptación del usuario.
- **Delta autorizado:** cambios selectivos en los tres documentos canónicos,
  separados del registro B4.1 previo; las propuestas untracked se conservan.
- **Evidencia de lectura:** seis documentos completos; la fila larga histórica
  AUTO-001 REV-004 se completó con Git blame y comparación documental, sin edición.
- **Verificación:** diff documental, referencias/dependencias y conservación;
  `git diff --check` correcto. Sin tests, red, servicios, instalaciones, staging,
  commit, push, reset o clean. Las cifras de tests citadas son evidencia previa.
- **Pendientes:** aceptación documental y
  actualización de la precondición/baseline B4.2 antes de ejecutar su paquete.
  No reconstruir detalles desconocidos de subaceptaciones AUTO-002.
- **Cierres:** ningún nuevo cierre de implementación, review cloud o
  autoaceptación; las aceptaciones previas se preservan. STOP.

### 14.6. Aceptación manual documental y preparación de reanudación — 2026-10-07

- **Aceptación explícita del usuario:** reconciliación documental aceptada según el diff completo entregado. Este registro supera el estado READY_FOR_REVIEW de §14.1/14.5; aquellos apartados conservan la entrega histórica.
- **Versiones aceptadas (Git hash de contenido):** acción `331a1c8f53e4667be5b950c201aa2c1c7de1142b`, técnico `ff3a9c4910f8a198da556b0b818236a0eb75af72` y brief `5cf97e79a7eb7c5d791510ffc43ffbcfdf90d1b9`. El hash del plan de acción corresponde a la versión revisada antes de añadir este registro.
- **Límite:** aceptación exclusivamente documental; no nuevos cierres de implementación, ejecución de tests ni aprobación cloud. B4.1 conserva su aceptación previa; sus 16 tests y probe no se atribuyen a esta revisión.
- **Conservación:** los tres documentos modificados y las tres propuestas untracked permanecen; propuestas sin autoridad operativa e índice vacío. Solo se añade este seguimiento propio, sin código, tests, configuración o plantilla.
- **Paquete recuperado íntegro:** mensaje compacto original de BACKEND-MARKET-ANALYSIS-API-001 / B4.2, GROUP-BACKEND-001 / RUN-MARKET-API-001-001 / REV-001, disponible en esta conversación. Se presenta completo en la respuesta, sin reconstrucción ni cambios de alcance, allowlist, comportamiento o checks.
- **Única actualización del paquete:** REF-BASE, con precondiciones, permisos y evidencia del árbol posterior a este registro. HEAD observado `4383372b846ca019cd8008f96b464dd82aa3e49e`, rama master; preservar tres documentos tracked modificados y tres propuestas untracked. Los hashes finales y comprobaciones se entregan en la referencia de baseline, sin asumir árbol limpio.
- **Estado B4.2:** PREPARADO, NO INICIADO. Reutiliza PreparedMarketAnalysis y await analyze; sin otro componente de cálculo. No código, tests, servicios, staging, commit o push; esta preparación no autoriza iniciar desde este chat.

### 14.7. Cierre B4.2 y bloque preparado de contexto N02 — 2026-10-07

- **B4.2 aceptado manualmente por revisión delegada:** código/test mantienen los hashes registrados en PARTE07; cuatro campos documentales corregidos verificados. Evidencia técnica previa del maestro: 18 tests API en 0.35s y 42 de regresión focal en 0.39s, ambos exit 0 con una advertencia. Esta revisión del cierre solo ejecuta checks Git, no pytest nuevo. No certifica suite completa, servicios, instalación limpia, UI, IA o ejecución financiera.
- **Compatibilidad conservada:** respuesta con `source="market_engine"` y `data_source="provided"` heredado, conforme al paquete compacto aceptado; no se cambia su esquema ni se atribuye provenance_verified. Las preparaciones y selectores antiguos de B4.2 quedan como historia.
- **Petición vigente:** preparar tres microtasks consecutivos para revisión en bloque, sin implementarlos aquí; STOP y handoff por cada task. Owner de backlog ACT-A-002, reutilizando contratos ACT-C-001 existentes sin cerrar ninguno globalmente.
- **Contratos reales contrastados:** SnapshotMarketState (seis campos), SnapshotEvidence (hecho numérico) y ScenarioSnapshot; no crear CandleEvidence/IndicatorEvidence. Indicadores, selección de señales, fórmulas del método y políticas de TTL quedan fuera; no llamar a N02/Ollama ni modificar sus modelos/cliente.

| Orden | Microtask preparado | Responsabilidad | Dependencia de ejecución |
|---|---|---|---|
| 1 | BACKEND-SCENARIO-STATE-001 | Proyección pura de seis campos a SnapshotMarketState, sin defaults ni autoridad operativa | Baseline actual capturada y contrato del paquete |
| 2 | BACKEND-SCENARIO-CLOSE-001 | SnapshotEvidence solo del último close y as_of exacto de PreparedMarketAnalysis, sin volumen/indicadores | Entrega técnica STATE-001 correcta, handoff completo y nueva baseline real |
| 3 | BACKEND-SCENARIO-SNAPSHOT-001 | Snapshot N02 mínimo con esos adaptadores e ID/captura/vencimiento/ref/faltantes explícitos del caller | Entrega técnica CLOSE-001 correcta, handoff completo y nueva baseline real |

- **Allowlist común propuesta:** NEW `backend/src/ai/scenario_adapters.py`, NEW `backend/tests/test_scenario_adapters.py` (creación solo en task 1) y seguimiento propio en este plan. Cada task autoriza únicamente su función/tests; no ejecutar los tres como un maestro abierto.
- **Baselines:** la primera se entrega con hashes reales en el paquete. Las de tasks 2/3 no existen todavía: se verifican contra el manifiesto/diff del handoff previo y las zonas autorizadas; ausencia, deriva o propiedad incierta → BLOCKED. La revisión manual conjunta no implica autoaceptación intermedia.
- **Límites del snapshot mínimo:** solo evidencia del cierre, etiqueta contractual `environment="paper"` sin subsistema PAPER y aptitud N02 al instante declarado; no vigencia de reloj real ni garantía de respuesta IA útil. Sin permisos de riesgo, órdenes, LIVE o método inventado. Ninguno de los tres tasks se ha iniciado.

### Revisión delegada CLOSE-001 — 2026-10-07

- **Resultado:** REQUIRES_CHANGES, sin aceptación automática de STATE-001. Función last_close_to_evidence conforme para entrada válida; la entrega no acredita aún compatibilidad con los DTO reales ni conservación byte a byte de STATE sin su manifiesto previo.
- **Check del maestro:** desde backend, `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -B -m pytest -q -p no:cacheprovider --capture=sys tests/test_scenario_adapters.py` → exit 0, 6 passed en 0.16s. Git diff-check/cached-check correctos, índice vacío y siete contenidos fuera de los archivos editables conservados.
- **Gap propio CLOSE:** _prepared_fixture crea _CandleInput/_AnalysisRequest/_PreparedMarketAnalysis privados en lugar de los modelos existentes; usa un cierre como open_time y as_of igual al último open. Data_source test_simulation no corresponde a PreparedMarketAnalysis del helper. El control solo compara as_of, no toda la entrada.
- **Gaps heredados del bloque:** fixture de red local copiada, sin guarda DNS, con create_connection async y constructores httpx reemplazados; no reutiliza la original y no delega connect AF_UNIX. Negativos STATE usan pytest.raises(Exception), no ValidationError. No se corrigen esas otras responsabilidades dentro de la primera corrección CLOSE.
- **Siguiente corrección acotada:** únicamente fixture CLOSE con DTO reales, 50 aperturas alineadas desde 1800000000000, as_of=base+50*60000-1 y control de no mutación completo. Guardas/negativos STATE se tratarán aparte; SNAPSHOT-001 no se habilita mientras estos gaps sigan abiertos. Sin código/tests editados por esta revisión.

### Revisión del bloque STATE/CLOSE/SNAPSHOT detenido — 2026-10-07

- **Base cotejada:** master, HEAD 4383372b846ca019cd8008f96b464dd82aa3e49e; módulo aac10d046dbdd7433242bd244bb3ce0f16bb4bb5, tests 30eef93ee3633b059ecde80310cdeff62ae9a2d2 y plan 5ee4227727355cb82bd6d2e260ab3e2aaa300196 antes del registro. Coinciden con el handoff actual; los resúmenes históricos sin salida completa no se reconstruyen.
- **Check del maestro:** desde backend, `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -B -m pytest -q -p no:cacheprovider --capture=sys tests/test_scenario_adapters.py` → exit 0, 11 passed en 0.26s. Diff-check/cached-check correctos, índice vacío y siete archivos ajenos conservados. Solo se modifica seguimiento por el revisor, no código/tests.
- **Corrección CLOSE verificada:** ahora usa DTO reales, aperturas 1800000000000+i*60000, cierre inclusivo base+50*60000-1, source market_engine simulado y deepcopy de toda la entrada. Resuelve el gap de fixture anterior; no significa aceptación global mientras la guarda común incumpla.
- **Resultado del bloque:** REQUIRES_CHANGES. Guarda copiada sin DNS/transports ni delegación connect AF_UNIX, constructores httpx alterados; STATE y dos negativos SNAPSHOT usan Exception en vez de ValidationError/ScenarioError. El control SNAPSHOT solo compara el contenido de missing_data, sin demostrar ausencia de alias.
- **Primera corrección autorizable:** únicamente sustituir la guarda local por la fixture importada original, sin alterar adaptadores o assertions. Después se revisarán en microtasks separados los tipos de excepción y la prueba de alias. No provider, endpoints, nuevas capacidades o autoavance.

### Aceptación delegada CORRECCIÓN01 de guarda — 2026-10-07

- **Versión realmente revisada antes de este registro:** tests acaf140328d966220b19e611021a2cb3714b729a, plan 94d2e6bb81e02dd23f77100a90d2265c68136168 y módulo aac10d046dbdd7433242bd244bb3ce0f16bb4bb5. La tabla de hashes del handoff era obsoleta y mezclaba archivos editables con no editables; no se usa como prueba de conservación. Los hashes actuales se calcularon en solo lectura.
- **Aceptación:** fixture local e imports socket/httpx/mock eliminados; import original autouse presente a nivel módulo. Resto de helpers/assertions leído y conservado; ocho archivos fuera de la allowlist mantienen hashes. No hay cambios de adaptadores, modelos ni guardas originales.
- **Check del maestro:** desde backend, `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -B -m pytest -q -p no:cacheprovider --capture=sys tests/test_scenario_adapters.py` → exit 0, 11 passed en 0.26s. Diff-check/cached-check correctos, índice vacío y status esperado; no se atribuyen estas salidas a Qwen ni se recuperan sus salidas perdidas.
- **Límite:** se acepta solo CORRECCIÓN01. Permanecen pendientes los negativos con Exception y la prueba de independencia de missing_data; no se autoacepta el bloque ni se inicia otra implementación. Siguiente corrección preparada: únicamente el negativo parametrizado STATE, sin tocar SNAPSHOT.

### Aceptación delegada CORRECCIÓN02 de negativo STATE — 2026-10-07

- **Versión revisada antes del registro:** tests 4614fe88412d6b78eedb16b75d47bb8e216fe00d, plan 4c1d4693268c8826eddce366d07a3736f2da2cbd y módulo aac10d046dbdd7433242bd244bb3ce0f16bb4bb5. El handoff no incluía el manifiesto final solicitado; se verificaron hashes reales, no se certifica su lista incompleta/duplicada como evidencia.
- **Aceptación:** cambio limitado al negativo STATE: import local ValidationError, excepción específica, un único error y loc=(modify_key,). Parametrización, control, fixture, adaptadores, otras assertions y ocho archivos fuera de la allowlist conservados.
- **Check del maestro desde backend:** `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -B -m pytest -q -p no:cacheprovider --capture=sys tests/test_scenario_adapters.py::test_project_market_state_valid_with_extras tests/test_scenario_adapters.py::test_project_market_state_negative_parametrized` → exit 0, 3 passed en 0.24s. Diff-check/cached-check correctos, índice vacío. No se atribuyen estas salidas a Qwen ni se recuperan salidas perdidas.
- **Límite:** solo se acepta CORRECCIÓN02. STATE vuelve a EN_REVISION para el cierre conjunto; SNAPSHOT aún necesita excepciones específicas y prueba de missing_data sin alias. Siguiente corrección preparada: solo los dos negativos SNAPSHOT, sin modificar adaptadores o la prueba de alias.

### Aceptación delegada CORRECCIÓN03 de excepciones SNAPSHOT — 2026-10-07

- **Versión revisada antes del registro:** tests b606baab80a5f000eaf2bc16a2562b3604ca76cf, plan 719df680839815ad532d1d7f21a568fe7ec14f3e y módulo aac10d046dbdd7433242bd244bb3ce0f16bb4bb5. Los hashes aportados son reales; la lista no era la de REF-BASE porque reemplazó tres propuestas por modelos/fixtures/helpers. Se cotejó también el conjunto original, incluidas las propuestas intactas.
- **Aceptación:** solo dos negativos cambiados, imports locales ValidationError/ScenarioError, excepciones específicas y code market_incomplete conservado. Resto de helpers/assertions, adaptadores, guardas y ocho archivos ajenos conservados.
- **Check del maestro desde backend:** `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -B -m pytest -q -p no:cacheprovider --capture=sys tests/test_scenario_adapters.py::test_build_market_scenario_snapshot_control tests/test_scenario_adapters.py::test_build_market_scenario_snapshot_valid_until_too_far tests/test_scenario_adapters.py::test_build_market_scenario_snapshot_connected_false` → exit 0, 3 passed en 0.26s. Diff-check/cached-check correctos, índice vacío. Esta es evidencia propia del revisor, no recuperación de salidas Qwen.
- **Límite:** se acepta solo CORRECCIÓN03; queda demostrar independencia de missing_data en el control, sin cambiar el constructor. No se acepta aún STATE/CLOSE/SNAPSHOT ni integración IA, servicios o ejecución financiera.

### Aceptación delegada CORRECCIÓN04 y cierre STATE/CLOSE/SNAPSHOT — 2026-10-07

- **Versión revisada antes del registro:** módulo aac10d046dbdd7433242bd244bb3ce0f16bb4bb5, tests e5ea502d5d75c4006f6cca8cd60fd48303439631 y plan ffa988695f3cb7189cd536e5010db649573aca48. Se cotejó el manifiesto original completo y propuestas intactas; la tabla del handoff sustituía tres propuestas por otros archivos y no se toma como lista completa de REF-BASE.
- **CORRECCIÓN04 aceptada:** seis assertions conservan el control previo, prueban identidad distinta y aislamiento de mutaciones caller/snapshot. Ningún cambio de constructor, otros tests o guardas; ocho archivos fuera de allowlist conservados.
- **Checks del maestro desde backend:** `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -B -m pytest -q -p no:cacheprovider --capture=sys tests/test_scenario_adapters.py::test_build_market_scenario_snapshot_control` → exit 0, 1 passed en 0.25s; mismo prefijo con `tests/test_scenario_adapters.py` → exit 0, 11 passed en 0.27s. Diff-check/cached-check correctos e índice vacío; no se atribuyen estas salidas a Qwen.
- **Cierre manual delegado:** STATE-001, CLOSE-001 y SNAPSHOT-001 COMPLETADOS en sus contratos acotados. Rechazos y correcciones anteriores se conservan como historia. ACT-A-002/ACT-C-001 no se cierran globalmente, ni N02/IA/PAPER quedan integrados por estos adaptadores.
- **Límites:** una evidencia de cierre, validez al instante capturado declarado, environment paper solo contractual, sin reloj real/IDs/TTL implícitos, indicadores, ejecución financiera o provenance_verified. No servicios, instalación o tests completos del backend/frontend.

### 14.8. BACKEND-SCENARIO-ENGINE-001 — preparación histórica y cierre

- **Owner:** ACT-A-002; no cierra su integración global. GROUP-BACKEND-001 / RUN-SCENARIO-ENGINE-001-001 / REV-001, COMPLETADO por revisión manual delegada del código y evidencia ya verificados.
- **Objetivo único:** prepare_market_scenario_snapshot orquesta prepare_market_analysis y build_market_scenario_snapshot, sin duplicar selección/validación o ejecutar análisis/IA. IDs, captura, vencimiento, ref y missing_data siguen explícitos del caller.
- **Allowlist propuesta:** scenario_adapters.py (solo función nueva/import local), test_scenario_adapters.py (fixture engine alineada y dos tests nuevos) y seguimiento propio de este plan. Código/modelos/fixtures originales permanecen intactos.
- **Checks preparados:** solo los dos nodos nuevos y git checks; los comandos exactos y baseline actual se entregan en el paquete. Datos de fixture, no providers/servicios/rutas/clock/TTL por defecto o permisos de ejecución. STOP al terminar; si requiere más scope, BLOCKED.

### Revisión delegada ENGINE-001 — 2026-10-07

- **Base real antes del registro:** master, HEAD 4383372b846ca019cd8008f96b464dd82aa3e49e; módulo 82f130bdec3c4f0a569e888d82577eb8a6f72428, tests 7a54da38f70a0ee2c275ef8be8d75e0539ecd2fc y plan f9ee083a5852fbf699dedcb8d8f1e1eaa2bf2b9b. Siete contenidos fuera de la allowlist conservados, índice vacío y diff-check correcto.
- **Resultado:** REQUIRES_CHANGES. Duplicado confirmado de prepare_market_scenario_snapshot: primera definición líneas 153–182, segunda idéntica 190–219 con separador 185–188. La segunda sobrescribe la primera; no cambia hoy los resultados al ser idénticas, pero deja código muerto y contradice una única orquestación.
- **Check del maestro desde backend:** `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -B -m pytest -q -p no:cacheprovider --capture=sys tests/test_scenario_adapters.py::test_prepare_market_scenario_snapshot_valid tests/test_scenario_adapters.py::test_prepare_market_scenario_snapshot_no_engine` → exit 0, 2 passed en 0.24s. No se ejecuta ni certifica el reporte de 13 tests del otro chat; no se recuperan salidas ajenas.
- **Tracking:** el plan mantenía exactamente su hash de preparación y no contenía registro de ejecución ENGINE. Se registra aquí la revisión; el implementador debe añadir seguimiento propio con resultados reales, sin modificar historia o aprobaciones previas.
- **Corrección única preparada:** eliminar solo la segunda definición/separador y conservar la primera literalmente; no editar tests, adaptadores aceptados, helpers originales o modelos. Repetir los dos nodos y entregar un manifiesto actual del mismo conjunto de diez rutas. No se implementa la corrección en este chat ni se inicia otro task.

### Revisión de eliminación del duplicado ENGINE-001 — 2026-10-07

- **Versión realmente observada:** módulo 46c9abe467c355c9b6134ff73ef022feadc3f75c, tests 7a54da38f70a0ee2c275ef8be8d75e0539ecd2fc y plan d46e4fabbd8d77a3ac99644895234345c429df51 antes del registro; master/HEAD 4383372b846ca019cd8008f96b464dd82aa3e49e. No se aceptan por la segunda opinión ni por hashes no verificados.
- **Código conforme:** queda una definición y se conserva la primera función; orquesta helpers sin cálculos, clock, IDs, TTL, endpoints o IA. Tests y ocho archivos ajenos a la corrección conservados.
- **Check del maestro desde backend:** `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -B -m pytest -q -p no:cacheprovider --capture=sys tests/test_scenario_adapters.py::test_prepare_market_scenario_snapshot_valid tests/test_scenario_adapters.py::test_prepare_market_scenario_snapshot_no_engine` → exit 0, 2 passed en 0.28s. Diff-check/cached-check correctos e índice vacío. No se certifica la salida ajena de 13 tests ni se atribuyen estos resultados a Qwen.
- **Gap documental restante:** el plan seguía exactamente con el hash registrado por el maestro antes de la corrección, sin seguimiento de entrega ENGINE añadido. Se requiere solo una ficha propia fiel a la versión/evidencia actual, conservando el rechazo y estas revisiones; no más código o pytest en esa corrección.
- **Parada:** ENGINE sin aceptación global todavía, integración N02 siguiente no habilitada. Solo registro de revisión; no se modifica código/tests ni se inicia otro task.

### Ficha de entrega ENGINE — 2026-10-08

- **Estado:** COMPLETADO por aceptación manual delegada; se conserva la fecha de entrega declarada y la historia anterior.
- **Código observado:** módulo `46c9abe467c355c9b6134ff73ef022feadc3f75c`; tests `7a54da38f70a0ee2c275ef8be8d75e0539ecd2fc`.
- **Cambio aplicado:** segunda definición de `prepare_market_scenario_snapshot` eliminada; primera definición conservada literalmente. Verificado con Grep: exactamente una definición (línea 153).
- **Revisión de eliminación del duplicado ENGINE-001:** exit 0, 2 passed en 0.28s; diff-check/cached-check correctos e índice vacío. No atribuir a Qwen ni inventar ejecución ajena de 13 tests.
- **Referencia al check completo del maestro:** § "Revisión de eliminación del duplicado ENGINE-001" (líneas 2207-2212) con exit 0, 2 passed, 0.28s; no atribuirlo a Qwen.
- **Reporte ajeno:** 13 tests no re-verificado por esta ficha; no inventar salida o ejecución.
- **Naturaleza de esta ficha:** documental — no es una nueva implementación ni aceptación.

### Cierre manual ENGINE-001 y ajuste de revisión

- **ENGINE-001 aceptado:** módulo 46c9abe467c355c9b6134ff73ef022feadc3f75c y tests 7a54da38f70a0ee2c275ef8be8d75e0539ecd2fc permanecen iguales a la revisión técnica conforme. Se utiliza el check del maestro ya registrado: 2 passed, exit 0, 0.28s; no pytest nuevo ni certificación del reporte ajeno de 13 tests.
- **Seguimiento:** ficha de entrega observada, rechazo por duplicado y revisiones conservados. Solo se actualiza tracking del revisor; código/tests, propuestas y trabajo previo intactos. Git diff-check correcto e índice vacío; sin staging, commit o push.
- **Criterio de eficiencia:** detalles de ficha/formato/hashes que el revisor puede verificar y registrar no generan nuevas rondas de implementación. Siguen bloqueando bugs, riesgos, mocks/fixtures inválidos, tests enmascarados, pérdida de trabajo o decisiones/ownership ambiguos.
- **Parada:** ningún nuevo task implementado ni capacidad IA/PAPER habilitada. Próximo microtask pendiente de preparación, orientado a funcionalidad backend, no infraestructura de revisión.

### 14.9. Servicio de asesoría preparado — tamaño funcional acotado

- **Task:** BACKEND-SCENARIO-ADVISORY-SERVICE-001 / GROUP-BACKEND-001 / RUN-SCENARIO-ADVISORY-001-001 / REV-001, COMPLETADO; owner ACT-A-002. No cierra el ciclo PAPER de ACT-A-003.
- **Objetivo único:** servicio async con cliente N02 inyectado: prepare_market_scenario_snapshot → await client.analyze_snapshot → mismo ScenarioAnalysisResult, sin reconstruir respuesta o recodificar errores. Metadata permanece explícita; el cliente conserva reloj/vigencia/cache y autoridad advisory existentes.
- **Scope propuesto:** NEW backend/src/ai/market_scenarios.py, NEW backend/tests/test_market_scenarios.py y seguimiento propio de este plan. No editar adapters, modelos, cliente, rutas, frontend, persistencia o configuración.
- **Unidad de entrega:** implementación y tres tests de orquestación juntos: forwarding/await/identidad de resultado, fallo de preparación sin llamar al cliente, ScenarioError del cliente propagado intacto. Mocks solo en la frontera del helper dentro del módulo nuevo; snapshot/result sentinels son DTO reales de fixtures N02, no clones ni prueba end-to-end.
- **Comprobación preparada:** archivo nuevo y regresión focal con test_scenario_adapters.py; comandos exactos y baseline actual en paquete. Sin proveedores reales, servicios, instalación, build, suites completas, staging o commit/push.
- **Limitaciones:** no endpoint ni panel IA operativo, snapshot propio aún mínimo de un cierre y no garantía de utilidad predictiva. No políticas nuevas de IDs/TTL ni authority/provenance true. No se implementa desde la preparación; STOP por cada entrega y BLOCKED si requiere ampliar scope.

### Ficha de entrega ADVISORY — 2026-10-08

- **Estado:** COMPLETADO por revisión manual delegada; corrección aplicada y confirmada en segunda opinión técnica.
- **Código observado:** módulo `ee52a0a577273c1311ed86640f29de343e83f3c1`; tests `f1fe9e8c59b7703c1bada7cc7940d871968177d7`.
- **Implementación:** NEW `backend/src/ai/market_scenarios.py` con función async `analyze_market_scenarios(client, engine, *, symbol, candles_count=200, snapshot_id, close_ref, captured_at_ms, valid_until_ms, missing_data) -> ScenarioAnalysisResult`. Importa `prepare_market_scenario_snapshot`, prepara snapshot con argumentos exactos, retorna `await client.analyze_snapshot(snapshot)`. Sin reconstruir, copiar o cambiar flags/source. Propaga excepciones intactas.
- **Tests:** NEW `backend/tests/test_market_scenarios.py` con tres casos: (1) forwarding exacto/await una vez/resultado por identidad; (2) MarketAnalysisInputError propagada/cero llamadas cliente; (3) ScenarioError del cliente propagada/sin sustitución. Mocks solo en frontera del helper importado.
- **Corrección aplicada:** (1) Línea 11: `from test_analysis_market_input import _block_network` — aislamiento de red contractual; (2) Línea 200: `assert exc_info.value is helper_error` — identidad de propagación verificada. Ambos cambios confirmados por lectura y segunda opinión técnica CONFORME.
- **Comprobación:** 3 tests test_market_scenarios.py pasaron exit 0, 0.34s; 16 tests combinados (market_scenarios + scenario_adapters) pasaron exit 0, 0.29s. Diff-check/cached-check correctos e índice vacío.
- **Naturaleza de esta ficha:** documental — no es una nueva implementación ni aceptación.

### Revisión delegada ADVISORY-SERVICE-001

- **Versión observada:** servicio ee52a0a577273c1311ed86640f29de343e83f3c1, tests f1fe9e8c59b7703c1bada7cc7940d871968177d7, plan actualizado tras cierre. master/HEAD 4383372b846ca019cd8008f96b464dd82aa3e49e.
- **Servicio conforme:** snapshot preparado y await cliente, forwarding completo y resultado devuelto sin modificar; no nuevas políticas, instancias, validación, rutas, ejecución o proveedor real.
- **Checks del maestro desde backend:** `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -B -m pytest -q -p no:cacheprovider --capture=sys tests/test_market_scenarios.py` → exit 0, 3 passed; combinado con test_scenario_adapters.py → exit 0, 16 passed. Diff-check/cached-check correctos, índice vacío.
- **Gap 1 cerrado:** test_market_scenarios.py línea 11 importa `_block_network` de test_analysis_market_input — aislamiento de red contractual demostrado.
- **Gap 2 cerrado:** test_helper_raises_market_not_ready línea 200: `assert exc_info.value is helper_error` — identidad de propagación verificada.
- **Estado final:** COMPLETADO por revisión manual delegada y segunda opinión técnica CONFORME. No se acepta ADVISORY global ni se inicia otro task desde esta sección.

### 14.10. Cierre ACT-S-002 — Reconciliación de inventario — 2026-10-09

- **Estado:** COMPLETADO por revisión manual del maestro el 2026-10-10; no se selecciona task siguiente.
- **Baseline verificada:** `master`, HEAD `7322afa336a798e255f81b8c74154352e40a8bf3`; 7 tracked modificados + 10 untracked (sin commit).
- **Matriz de capacidades respecto a la baseline:**

| Área | Estado | Rutas / Tests | Observación |
|---|---|---|---|
| Mercado/API+procedencia | IMPLEMENTADO (contrato) | `backend/src/api/analysis.py`, `backend/tests/test_analysis_market_api.py` | B4.2 aceptado: POST /api/analysis/market con PreparedMarketAnalysis; 18+42 tests. Sin ejecución financiera. |
| Análisis B3.1–B3.4 | COMPLETADO (aceptado) | `backend/tests/test_analysis_market_input.py` | B3.1-B3.4: insuficiencia, finitud, rango OHLC, open_time creciente — 8+25+34+40 tests. |
| Análisis B4.1 | COMPLETADO (aceptado) | `backend/tests/test_analysis_market_input.py` | 16 tests + probe offline; últimas N cerradas 1m sin endpoint ni ejecución. |
| Indicadores FYL | PARCIAL / NO VERIFICADO | `backend/src/indicators/fyl.py`, `backend/tests/test_fyl.py` | strength genérica corregida para umbral 0.01 (local, no committed); timestamps incrementales usan open_time de origen. Batch sigue TEST_ONLY con futuro. No afirmar equivalencia al FYL original. |
| Indicadores Keltner/MACD | IMPLEMENTADO (batch) | `backend/src/indicators/` | Referencia numérica de la implementación actual aprobada/testeada; ACT-S-006 COMPLETADO en su alcance (§14.12). MACD BB y método original FYL siguen sin especificación. |
| Reconexión engine | PARCIAL / NO VERIFICADO global | `backend/tests/test_binance_client.py`, `test_market_engine.py` | `_frame` parametrizado + FakeHTTP recovery tracking; 66 passed. El fake HTTP devuelve histórico vacío — prueba callback/protocolo, no backfill. No cierra ACT-S-005 global. |
| Mercado microarreglos | EVIDENCIA ACOTADA (POR_REVALIDAR) | Gates verificados en tasks previos | Símbolo, close_time, rejilla WS 1m, rechazo REST bootstrap/gap verificados acotadamente. No bastan para cerrar ACT-S-005 global. |
| N02/IA | IMPLEMENTADO (contrato), NO VERIFICADO (integración) | `backend/src/ai/market_scenarios.py`, `scenario_adapters.py`, `test_market_scenarios.py`, `test_scenario_adapters.py` | Escenarios estructurados con vigencia, límites y validación semántica. Contexto propio, auditoría e integración UI pendientes. ADVISORY-SERVICE-001 COMPLETADO en su alcance (§14.9). |
| Terminal/UI | PARCIAL | `frontend/src/` | Una única Terminal de desarrollo; navegación Laboratorio/Bot objetivo. QA responsive/accesible pendiente (ACT-T-005 POR_REVALIDAR). |
| PAPER/persistencia | AUSENTE | — | Estado en memoria; `aiosqlite` declarado sin implementación. ACT-P-001–P-010 PENDIENTE. |

- **ADVISORY-SERVICE-001:** Corregido puntero activo en §1 y §12; ficha de cierre en §14.9 conservada como historia. Ya no se describe como "siguiente microtask preparado".
- **B4.2:** Mantenido COMPLETADO en su alcance (§5 fila, §14.7). Descripción antigua de B4.2 como futuro reconciliada: ahora consta como aceptado con evidencia 18+42 tests. El FYL de §3 dev plan ya refleja strength genérica corregida y open_time de origen; batch continúa TEST_ONLY.
- **N03:** Nota: no se encontró un identificador N03 independiente en plan, código ni tests. CORRECCION03 es un ID de corrección del bloque STATE/CLOSE/SNAPSHOT (§14.7), no un identificador de tarea. No se crea como nueva tarea/ID.
- **ACT-S-005:** EN_REVISION (pendiente aceptación manual del maestro). 189 tests verdes; revalidación final documentada en §14.11 con evidencia separada por criterio. ACT-S-006/007 permanecen POR_REVALIDAR.
- **Conservación de diffs previos:** Los 7 archivos tracked modificados y 10 untracked se conservan íntegros. Solo se aplicaron las correcciones documentales enumeradas arriba en los tres documentos raíz.
- **Límites de esta reconciliación:** No se ejecutaron tests nuevos, ni build, ni typecheck, ni instalación limpia. Las cifras de tests citadas provienen de handoffs previos verificados por el maestro. ACT-S-002 cierra su inventario; no cierra fases completas ni selecciona siguiente task.
- **STOP:** No se inicia tarea siguiente. Handoff entregado con diff documental, matriz/evidencia y estado de ACT-S-002.

### 14.11. ACT-S-005 — Revalidación final (REV-001 + REV-002)

**Estado:** COMPLETADO por revisión manual del maestro el 2026-10-10.
**Baseline:** master / 7322afa336a798e255f81b8c74154352e40a8bf3
**Tests ejecutados:** 189 passed, 0 failed (suite completa: test_market_api.py, test_market_engine.py, test_market_lifecycle.py, test_binance_client.py)

#### Criterio 1 — Límites REST de `limit`
- Default 100: `test_candles_default_limit_is_100` → 200, data_source=TEST_ONLY, interval=1m, len(candles)==100
- Bordes válidos 1 y 500: `test_candles_limit_boundary_accepts_valid_values(limit=1)` y `(limit=500)` → 200, len==N
- Inválidos 0, -1, 501: `test_market_lifecycle.py:1096-1104` → 422
- Cap HTTP min(limit, 1000): implementado en `binance_client.py:95`, sin test directo (no bloquea cierre)

#### Criterio 2 — Símbolos válidos y malformados
- Malformado → 422 status/candles: `test_market_api.py:324-336` parametrizado con 9 símbolos inválidos
- Válido no poseído → TEST_ONLY candles + unavailable status: `test_another_symbol_never_receives_current_engine_history`, `test_custom_symbol_fixture_is_still_test_only`

#### Criterio 3 — Procedencia
- Engine real → market_engine: `test_engine_history_preserves_symbol_interval_and_source`
- Sin engine → unavailable: `test_status_default_symbol`
- Engine vacío → fallback TEST_ONLY 1m: `test_empty_engine_fallback_keeps_fixture_interval`
- WS no presenta fixture como conectado: candle events source correcto

#### Criterio 4 — Temporalidad (evidencia separada)

**Rejilla WS 1m:**
- `engine.py:440`: guard de alineación temporal para velas WS 1m
- `test_ws_misaligned_1m_candle_is_rejected`: rechazo de vela misalineada 1m
- `test_ws_aligned_1m_candle_behaves_normally`: vela alineada 1m procesada normalmente
- `test_non_1m_ws_intervals_are_unaffected_by_grid_check`: intervalos no-1m no afectados por el guard
- `test_ws_misaligned_closed_candle_does_not_affect_engine_state`: vela cerrada misalineada sin efecto en estado
- `test_ws_aligned_closed_candle_preserves_normal_flow`: vela cerrada alineada conserva flujo normal

**Control de límites HTTP (no atribuido a rejilla WS):**
- `test_market_lifecycle.py:1096-1104`: rechazo de límites fuera de rango en candles endpoint (REST, no WS)

**Test de intervalos fijos del builder (no atribuido a rejilla WS):**
- `test_builder_uses_configured_fixed_interval`: prueba del procesamiento/continuidad por `interval_ms`, no del guard de alineación WS

**Construcción de URI WS (evidencia separada):**
- `test_binance_client.py:156-181`: verificación directa de la URI WebSocket construida con el intervalo correcto

**Control WS alineado (1m) — motor:**
- `test_ws_aligned_closed_candle_preserves_normal_flow`: vela 1m alineada llega al engine, cierra y avanza last_closed_close_time.
- Candle events source correcto en `test_market_api.py:211-229`.

**Control non-1m del guard (5m):**
- `test_non_1m_ws_intervals_are_unaffected_by_grid_check`: la vela 5m fuera de la rejilla 1m no es rechazada por el guard 1m.

**Continuidad del builder (separada de rejilla WS):**
- `test_builder_uses_configured_fixed_interval`: procesamiento/continuidad por interval_ms; no valida alineación epoch.

- close_time inclusivo (3 tests): fallback, engine vacío, bootstrap REST
  - `test_market_api.py:344-351` fallback
  - `test_market_api.py:361-371` engine vacío
  - `test_market_lifecycle.py:405-411` bootstrap REST
- Bootstrap 500 velas: timestamps preservados (`test_market_lifecycle.py:389-418`)
- Frescura clock_ms+margin: boundary test + freshness endpoint (`test_market_lifecycle.py:468-487`, `test_market_lifecycle.py:1075-1093`)
- pending_gaps y entries_allowed: _assert_ready + múltiples tests gaps (`test_market_lifecycle.py:265-275`)

#### Criterio 5 — Reconexión y teardown
- Callbacks connect/disconnect/error: transport callbacks (`test_market_engine.py:203-238`)
- Backfill simulado y exitoso: DelayedHTTP gap recovery (2 tests) (`test_market_lifecycle.py:545-591`, `test_market_lifecycle.py:691-719`)
- Release handlers/tareas al disconnect: shutdown cancela recovery (`test_market_lifecycle.py:1107-1156`, `test_market_lifecycle.py:873-904`)
- Cancelación y shutdown limpia recursos: preserve cleanup (`test_market_engine.py:469-479`, `test_market_lifecycle.py:941-984`)

#### Gaps no aplicables
- G4 retirado: cubierto por tests existentes
- G6 retirado: cubierto por backfill ranged exitoso con DelayedHTTP

### 14.12. Cierre ACT-S-006 — Referencia numérica Keltner actual

- **Estado:** COMPLETADO por revisión manual del maestro el 2026-10-10.
- **Contrato aprobado:** semilla EMA SMA, alpha `2/(n+1)`; ATR con `TR[0]=0`, media simple inicial incluyendo ese valor y recurrencia Wilder; bandas EMA ± multiplier×ATR; warmup `ema_period+atr_period`. Describe la implementación aprobada, no el Keltner original.
- **Referencia independiente:** fixture 8 barras 1m, origen alineado `1_699_999_980_000`; test-only `ema_period=3`, `atr_period=3`, `multiplier=2.0`; expected racionales exactos en la fila ACT-S-006 y `backend/tests/test_keltner.py::test_keltner_reference_numeric_contract`, tolerancia rel/abs `1e-12`.
- **Insuficiencia:** `test_keltner_warmup_length` comprueba 33→[], 34→1 punto y 35→2 puntos con defaults; B3/API conservan rechazo/no-fallback. No se fabrican señales.
- **Verificación del maestro:** `test_keltner.py tests/test_analysis_market_input.py tests/test_analysis_market_api.py` → 40 passed, 1 warning Starlette/httpx. Diff checks limpios. Ningún cambio de fórmula productiva, estrategia, riesgo o frontend.
- **Límites:** defaults actuales 20/14/2.0 no son obligatorios para todas las estrategias; nuevas convenciones requieren versión explícita. No se afirma equivalencia con el método original. ACT-S-007 permanece POR_REVALIDAR.
