# Plan de desarrollo — Terminal privada con bot de trading e IA local

Fecha de actualización: 2026-10-03. Estado: base backend/frontend existente, con implementación parcial y estabilización pendiente; todavía no hay un bot operativo ni simulador PAPER.

Este plan distingue el estado observado de los requisitos objetivo. Complemento de diseño: [trading-console-visual-brief.md](trading-console-visual-brief.md). La actualización de este documento no implementa funcionalidades ni autoriza iniciar la siguiente tarea, conectarse a cuentas o operar.

## 0. Estado del repositorio y punto de partida

Inventario contrastado con el árbol de trabajo el 2026-10-03. Hay cambios sin confirmar; esta descripción no corresponde a una versión congelada en un commit. Antes de cada microtask se debe revalidar el estado y preservar el trabajo ajeno.

| Área | Estado observado | Pendiente para el producto objetivo |
|---|---|---|
| Frontend | React 19, TypeScript, Vite y CSS propio con tokens. Una única Terminal de desarrollo; símbolo activo BTC/USDT y watchlist estática BTC/ETH. | Navegación Terminal/Laboratorio/Bot, selección de símbolo/intervalo, controles funcionales y QA responsive/accesible. |
| Gráfico | Lightweight Charts 5.2.1: velas, zoom, desplazamiento y resize. Cada cambio usa `setData` completo. | Deltas, volumen, capas, zonas y paneles sincronizados; propuesta/orden/fill diferenciados. |
| Mercado backend | Clientes públicos Binance Spot HTTP/WS, `CandleBuilder` y `MarketEngine`, con pruebas offline de transporte y ciclo de vida. | Arranque integrado en FastAPI, histórico inicial, recuperación de huecos y frescura verificable. El `lifespan` actual no arranca el motor. |
| Mercado frontend | Histórico con envelope de símbolo, intervalo y procedencia; filtros básicos de eventos y avisos `TEST_ONLY`. | Suscripción al abrir, canal `updates`, resuscripción, limpieza de timers, buffer acotado y reconciliación HTTP/WS. |
| Indicadores | MACD convencional y Keltner calculados por lotes; módulo experimental denominado FYL. El panel conserva procedencia e intervalo. | MACD BB y método exacto no definidos; FYL presenta defectos de fuerza/timestamps. Refresco del análisis aún depende de la longitud del histórico. |
| Reglas e IA | Evaluador de reglas con indicadores fijos `TEST_ONLY`; Ollama asíncrono bajo demanda, respuesta textual asesora y modelo por defecto `llama3`. `AIAnalysisPanel` existe, pero no está montado. | Escenarios estructurados, selección explícita de modelo, límites de concurrencia, caducidad, validación semántica y auditoría. Qwen es objetivo del brief, no configuración actual. |
| Ejecución y cartera | Contratos declarativos; API niega ejecución/cartera y comandos del motor devuelven `501`. UI indica «Sin ejecución» y métricas no disponibles. | PAPER, órdenes/fills, libro, riesgo aplicado, pausa/cierre verificables y recuperación. No hay adaptador demo ni live. |
| Persistencia y laboratorio | Estado en memoria; `aiosqlite` declarado sin implementación de almacenamiento. No hay vista ni motor de experimentos. | SQLite, sesiones/versiones, replay causal, simulación, métricas y comparación de candidatos. |

Referencias principales: `frontend/src/App.tsx`, `frontend/src/components/ChartPanel.tsx`, `frontend/src/components/AnalysisPanel.tsx`, `frontend/src/hooks/useMarketData.ts`, `frontend/src/api/market.ts`, `backend/src/api/{main,market,analysis,bot}.py`, `backend/src/market/`, `backend/src/indicators/` y `backend/src/ai/ollama_client.py`.

No confundir modelos, componentes aislados o nombres de commits/fases con capacidades integradas. Los endpoints de mercado pueden devolver fixtures; una conexión WS abierta no acredita feed activo, frescura ni capacidad de ejecución.

## 1. Objetivo y alcance

Construir una terminal responsive privada para Binance Spot con análisis técnico, anotaciones sobre el gráfico, análisis de escenarios mediante Ollama y bot automático en simulación desde el primer MVP integrado. Reducir infraestructura y código propio, no controles de seguridad ni pruebas.

Requisito objetivo: la UI no será el motor del bot; cerrar el navegador no deberá detenerlo. Suspender la máquina sí puede detenerlo; al recuperarse deberá reconciliar estado y actualizar datos antes de abrir nuevas posiciones. Actualmente no existe ese ciclo autónomo.

Entornos separados:
- PAPER: datos reales, saldo y ejecuciones simulados propios. Entorno predeterminado.
- EXCHANGE_DEMO: integración oficial demo/testnet con credenciales y URLs independientes; valida órdenes y protocolo, no garantiza fidelidad de resultados económicos.
- LIVE: fuera del alcance inicial. No incluir un interruptor funcional que habilite dinero real.

Estos son entornos objetivo, no capacidades actuales. PAPER nominal no implica que exista un simulador. La procedencia `TEST_ONLY` describe datos sintéticos, no un entorno de exchange; los endpoints públicos de producción para precios tampoco habilitan operaciones con dinero real.

Spot sin margin ni derivados: largos y salida a moneda cotizada; vender requiere saldo disponible. BTC y ETH son activos iniciales propuestos, sujetos a disponibilidad de símbolos en la cuenta. No hay depósitos ni retiros automáticos.

## 2. Decisiones de arquitectura

| Área | Decisión y estado |
|---|---|
| Frontend | Conservar React, TypeScript y Vite existentes; CSS con tokens, sin framework adicional de estado al principio. Reutilizar componentes antes de sustituirlos. |
| Gráfico | Conservar Lightweight Charts 5.2.1 ya integrado. Verificar licencia, atribución, API de overlays y paneles sincronizados antes de ampliar; no reemplazarlo por código de Stitch sin justificación técnica. |
| Backend | Conservar FastAPI y asyncio. Objetivo: un proceso/worker y un único propietario del motor; los globales actuales no garantizan propiedad única entre workers. |
| Cálculos | Python y funciones puras; NumPy solo cuando sea útil. Los cálculos actuales son por lotes; incrementalidad y equivalencia quedan pendientes. |
| Persistencia | Objetivo pendiente: SQLite, transacciones, migraciones simples y escritura serializada. No guardar cada tick indefinidamente en tablas de decisiones. |
| Histórico bruto | Diseño pendiente: archivos segmentados y acotados; elegir formato tras medir volumen. SQLite conservará metadatos y referencias. |
| IA | Conservar cliente Ollama asíncrono con timeout. Añadir concurrencia limitada, salida estructurada y validación semántica; Pydantic valida hoy el envelope de Ollama, no la decisión financiera. Selección de modelo explícita, sin presentar `llama3` como Qwen. |
| Transporte UI | Conservar HTTP y WS multiplexado de mercado. Extender por contrato a snapshot, secuencia y eventos operativos cuando existan; hoy no transporta órdenes/fills/cartera. |
| Operación | Entorno Python y npm local. Revisar dependencias runtime: `httpx` se utiliza en aplicación, pero figura solo en el extra `test`. Contenedor opcional, no obligatorio. |

Sin Redis, Kubernetes, microservicios, múltiples usuarios, plataforma de plugins ni abstracciones para muchos exchanges. No elegir Rust/C++ sin un cuello de botella medido.

## 3. Bloqueos del método original

Antes de afirmar que reproducimos T1/V1/V2 necesitamos:
1. Fórmula y parámetros exactos de FYL, Keltner y MACD BB; significado preciso de BBs y niveles «30».
2. Tipo de barras, construcción, sesiones y calentamiento. La captura de 610 ticks no implica que 610 operaciones de BTC sea equivalente.
3. Reglas numéricas de impulso, retroceso, consolidación, pendiente y tolerancia de zonas.
4. Resolver exactamente tres barras de FYL plana y pequeñas consolidaciones admitidas en T1.
5. Fórmulas de entrada, caducidad, stop, objetivo, salida y tamaño.
6. Momento en que pivotes/áreas pueden confirmarse sin usar el futuro.

Se puede desarrollar infraestructura y probar el ciclo completo con una estrategia fixture determinista sobre datos sintéticos, etiquetada TEST_ONLY. No inventar FYL ni presentar otra estrategia como el método original. T1/V1/V2 permanecen deshabilitados para operar mientras falten reglas.

El módulo actual `backend/src/indicators/fyl.py` no acredita la fórmula original: sus pivotes incrementales calculan fuerza negativa para nuevos extremos con precios positivos y usan tiempos relativos `índice × 60.000`, que luego se comparan con timestamps de mercado. Corregir o aislar esos defectos requiere pruebas y una especificación explícita del detector; no resuelve las fórmulas desconocidas del método. El modo batch usa futuro y sigue siendo `TEST_ONLY`, no válido para replay causal ni señales en vivo.

MACD convencional no es MACD BB. La estrategia predeterminada y sus puntuaciones son de desarrollo, no T1/V1/V2 validados ni probabilidades de ganar. El prompt actual que propone `long/short` debe restringirse al alcance Spot, sin introducir cortos.

## 4. Backend

Los apartados siguientes son requisitos por completar sobre la base existente, no una lista de funciones disponibles. No implementar órdenes para completar un mockup.

### Mercado
- Una conexión compartida a Binance; historial por HTTP y eventos en vivo por WebSocket.
- Normalizar símbolo, timestamps UTC, identificador de operación y procedencia.
- Detectar duplicados, huecos, eventos fuera de orden, cierre de barra y antigüedad.
- Reconexión con backoff y recuperación de huecos; datos incompletos bloquean entradas. Integrar histórico inicial y backfill: detectar un gap no equivale a repararlo.
- Para barras por ticks, definir qué evento cuenta como tick. No sustituir operaciones individuales por operaciones agregadas sin justificar el cambio.
- Separar barras cerradas de barra en formación y declarar cuál utiliza cada regla.
- Mantener buffers acotados y cargar histórico adicional bajo demanda.
- Conservar timestamps de mercado y añadir recepción local y edad. `close_time` es cierre del intervalo, no hora de recepción; no llamarlo latencia ni «última actualización».
- Arrancar/parar una única instancia desde el ciclo de vida de la app, con configuración explícita y apagado limpio. Pruebas automáticas mediante clientes simulados, sin conexiones externas implícitas.

### Indicadores, zonas y estrategia
- Mismo motor numérico para replay y ejecución en vivo.
- Actualización incremental y pruebas de equivalencia contra cálculo por lotes.
- Zonas con ID, rango, contactos, origen, instante de detección, confirmación, revisión e invalidación.
- Conservar revisiones; no reescribir retrospectivamente cuándo una zona era conocida.
- Motor de estados: observando → candidato → pendiente de análisis → aprobado/rechazado/caducado → orden. Posiciones se gestionan en un ciclo aparte.
- Cada candidato guarda estrategia/versión, snapshot de mercado y condiciones verificadas.

### IA de escenarios
- Snapshot compacto con características numéricas, zonas confirmadas, checklist, cartera y reglas.
- Respuesta: evaluación, evidencias referenciadas, escenarios de continuación/fallo/lateralización, datos faltantes e invalidación.
- Precio y tamaño de órdenes se calculan/validan en código, no desde texto libre.
- JSON schema más validación semántica: referencias existentes, fechas válidas y acciones permitidas.
- Configurar timeout, máximo de salida, concurrencia y deduplicación. Revalidar mercado, estrategia y riesgo tras recibir respuesta.
- Abstención y errores bloquean entradas que dependan de IA; nunca bloquean salidas protectoras.
- Persistir versión del modelo, prompt, parámetros, duración, snapshot y respuesta.
- Herramientas opcionales inicialmente solo lectura; modelo sin secretos, shell, retiros ni envío directo de órdenes.
- Noticias fuera del primer sprint; al añadirlas guardar fuente, publicación y recepción, y tratarlas como contenido no confiable.
- Corregir y cubrir la ruta `/api/bot/ai/strategy`: actualmente utiliza `json.dumps` sin importar `json`. Verificar que el modelo configurado está disponible, no solo que `/api/tags` responde.

### Riesgo
- Límites configurados y versionados: tamaño, exposición total/por activo, posiciones simultáneas y pérdida máxima.
- Validar stop, saldo libre/reservado, órdenes pendientes, spread, datos y estado del bot.
- Aplicar filtros del símbolo: tickSize, stepSize e importe mínimo; utilizar decimales en dinero y órdenes.
- Definir qué sucede al alcanzar límites: bloquear entradas y mantener gestión; no liquidar implícitamente.
- Revalidar justo antes de enviar. La IA no cambia límites ni elimina stops.

### Ejecución y simulador
- Interfaz pequeña con dos implementaciones: paper y demo Binance.
- Estados de orden: preparada, envío pendiente, estado desconocido, aceptada, parcialmente ejecutada, ejecutada, cancelada, rechazada o expirada.
- Identificadores persistentes; un timeout no implica rechazo. Consultar/reconciliar antes de repetir una acción.
- Libro de efectivo, activos, reservas, comisiones y fills; posición derivada del libro.
- Paper con bid/ask, costes configurables, latencia, ejecuciones parciales y política conservadora para límites. Un toque en una vela no garantiza fill.
- Si entrada, stop y objetivo caben en una misma barra y no conocemos el orden intrabar, marcar ambigüedad o aplicar una política conservadora documentada.
- Registrar fills y comisiones reales del entorno demo; manejar comisiones en otro activo.
- Proteger solo la cantidad efectivamente comprada. Validar tipos de órdenes protectoras disponibles en cada símbolo/entorno.
- Reconciliar saldo, órdenes y fills en arranque y reconexión; detectar discrepancias y bloquear nuevas entradas.

### Persistencia y API

#### Contratos existentes

| Ruta | Semántica actual |
|---|---|
| `GET /health` | Vida de la API; no certifica mercado, IA ni ejecución. |
| `GET /engine/state` | Diagnóstico `not_implemented`, ejecución/cartera no disponibles. |
| `GET /fixture/data` | Datos sintéticos deterministas `TEST_ONLY/1m`. |
| `POST /engine/action` | Valida `pause/resume/stop`, pero devuelve `501` y `executed:false`. No es pausa/cierre operativo. |
| `GET /api/market/status` | Estado del motor para el símbolo o `unavailable`. |
| `GET /api/market/candles` | Envelope `{symbol, interval, data_source, candles}`; fuente `market_engine` o fallback `TEST_ONLY/1m`. |
| `WS /api/market/ws` | Canales `candles`, `updates`, `status`, `gaps`; sin snapshot inicial ni secuencia. |
| `POST /api/analysis` | Indicadores sobre velas proporcionadas o fallback `synthetic_test`; `provided` no acredita fuente real ni frescura. |
| `GET /api/bot/status` | Bot no operativo, estrategia `TEST_ONLY`, cartera/ejecución no disponibles. |
| `POST /api/bot/analyze` | Propuesta sobre indicadores fijos; `TEST_ONLY`, `executable:false`. |
| `POST /api/bot/ai/analyze`, `POST /api/bot/ai/strategy` | Asesoría textual, fuente `TEST_ONLY/unverified`, no ejecutable; generación de estrategia tiene el defecto citado. |

El frontend ya consume el envelope de histórico y conserva procedencia/intervalo; no queda pendiente volver a migrarlo desde un array. Sí falta validar cada vela y las respuestas/eventos, límites de consultas, orden temporal y coherencia OHLC. El fallback técnico actual con menos de 50 velas debe sustituirse por un estado de datos insuficientes fuera de un modo fixture explícito, sin fabricar una señal operativa.

Desregistrar callbacks WS al desconectar, validar canales y añadir controles de origen/autenticación antes de acceso externo. Fijar contratos canónicos para velas, zonas, propuestas, órdenes y posiciones: existen familias duplicadas entre `models/state.py` y `bot/models.py`. Procedencia, entorno, conexión y frescura son dimensiones distintas.

#### Persistencia y endpoints objetivo

Tablas mínimas: configuración/versiones, sesiones de simulación, decisiones/analisis, órdenes/fills, revisiones de zonas y eventos de auditoría. Evitar duplicar balances sin mecanismo de reconciliación.

Endpoints orientativos: GET estado; GET histórico paginado; GET zonas; GET decisiones; GET cartera/órdenes; POST solicitar análisis; POST pausar/reanudar entradas; POST solicitar cierre y detención; GET/PATCH configuración validada.

Comandos con ID para prevenir duplicados. Cierre y detención es un proceso verificable: cancelar entradas pendientes, solicitar cierres, confirmar resultado y señalar fallos. El botón no garantiza un cierre instantáneo.

## 5. Frontend

Conservar `ChartPanel`, `AnalysisPanel`, el hook de mercado y los tokens existentes. Integrar `AIAnalysisPanel` solo al completar su contrato y política de solicitud. `BottomBar` no está montado y conserva métricas fijas y pestañas aparentes: corregirlo antes de reutilizarlo. Mientras no exista cartera, mantener «no consultada/no disponible», no ceros ficticios.

La aplicación activa ya identifica fixtures, informa ausencia de ejecución y conserva el intervalo real en análisis. No reintroducir porcentajes aleatorios, estado de bot simulado o acciones aparentes. El análisis debe refrescar por identidad/revisión de barra y política definida, no solo por `candles.length`.

### Terminal
- Barra superior: navegación Terminal/Laboratorio/Bot y PAPER/EXCHANGE_DEMO solo si el backend confirma ese entorno y su capacidad. Mientras no exista ejecución, conservar la advertencia explícita de desarrollo; mostrar estado de datos y modelo sin fingir disponibilidad.
- Lista de activos a la izquierda; gráfico central; análisis y checklist a la derecha.
- Precio/Keltner/FYL/zonas arriba; MACD BB sincronizado abajo.
- Anotaciones exactas: zonas, pivotes, propuesta de entrada, stop y objetivos; capas activables.
- Seleccionar zona muestra origen, rango, contactos y fecha de confirmación.
- Panel IA con conclusión, evidencias, escenarios e invalidación; respuestas caducadas se distinguen.
- Franja inferior: posiciones, órdenes y actividad. Nunca confundir propuesta con fill.
- Controles separados: pausar nuevas entradas; cerrar posiciones y detener, con confirmación y seguimiento.

### Cartera e historial
- Patrimonio, efectivo, reservas, exposición, realizado/no realizado y drawdown, todos con moneda/periodo.
- Curva de patrimonio y tabla de posiciones.
- Historial filtrable; detalle de propuesta → riesgo → orden → fills → cierre.
- Preservar tesis original; revisión posterior aparece aparte.

### Replay y laboratorio mínimo
- Reproducción barra a barra con futuro oculto.
- Etiquetas manuales válidas/inválidas y discrepancias con el detector.
- Comparar estrategia sin IA, con IA y referencia pasiva; registrar exposición.
- Versiones congeladas; reiniciar demo crea sesión nueva, no borra la anterior.
- Vista Laboratorio según el brief: experimentos identificables, estados reales de ejecución, costes/supuestos, candidatos comparables y datos insuficientes explícitos. No añadir promoción automática solo para completar la interfaz.

### Supervisión del bot
- Vista Bot según contratos disponibles: estado confirmado, estrategia/versión, modelo/prompt y sesión.
- Modos Manual/Con aprobación/Automático solo si el backend los soporta y confirma.
- Timeline con IDs: oportunidad → análisis → validación → orden → ejecución → salida, sin razonamiento interno del modelo.
- Propietario visible de posiciones manuales/del bot e informe con métricas calculadas separadas de explicaciones IA. Registros externos manuales no se presentan como sincronizados.

### Responsive y acceso
- Desktop de tres columnas; tablet reduce paneles. Móvil abre el gráfico, sustituye watchlist por selector y usa pestañas/panel inferior para Análisis y Posiciones; navegación inferior Terminal/Laboratorio/Bot.
- Gráfico táctil, etiquetas legibles, controles de al menos 44 px y estados no dependientes solo del color.
- PWA opcional después de la base responsive; offline es solo consulta cacheada marcada como tal. Nunca encolar órdenes offline ni cachear secretos.
- Acceso local primero. Desde fuera: acceso privado/autenticado, HTTPS y controles de origen/sesión. No exponer Ollama ni credenciales al teléfono.
- Verificar 1440×900, 1920×1080 y 390×844, landscape y zoom 200%. Corregir posibles recortes del layout actual `100vh` con paneles apilados; respetar safe areas, foco visible, contraste AA y reduced motion.

Stitch se utilizará únicamente para propuestas y referencias cuando se autorice la tarea de diseño: comparar dos direcciones desktop, elegir una y adaptar al frontend existente. Antes tomar capturas; no hay QA visual ni capturas certificadas en la revisión actual. No importar una reescritura ni diseñar pantallas operativas ficticias antes de sus contratos.

## 6. Rendimiento: presupuestos propuestos, no garantías

- Gráfico con 10.000 barras y 100 anotaciones: interacción objetivo próxima a 60 fps en laptop; medir dispositivo móvil real.
- Entrada a feedback visual local: objetivo p95 menor a 100 ms.
- Eventos de mercado a UI: objetivo p95 menor a 250 ms desde recepción local, sin contar red externa ni IA.
- IA fuera de la ruta crítica; medir p50/p95 con snapshot representativo antes de elegir frecuencia.
- React actualiza paneles, no cada vela por componente. Agrupar renderizados, actualizar series por delta y limitar buffers.
- Backend no procesa inferencia/cálculos largos bloqueando el event loop; medir y descargar trabajo pesado a hilo/proceso si hace falta.
- WebSocket con secuencia y snapshot inicial; cliente recupera estado después de huecos.
- Prueba sostenida de memoria, desconexiones y varios clientes; abrir dos pestañas no crea dos bots.

## 7. Orden de desarrollo y criterios de aceptación

Reanudar desde estabilización, no recrear el esqueleto. Las fases mantienen su numeración como referencia, pero ninguna se declara terminada por tener archivos o commits asociados.

| Fase | Estado actual | Backend / trabajo pendiente | Frontend / trabajo pendiente | Puerta de salida |
|---|---|---|---|---|
| S. Estabilización | Pendiente; prioridad inmediata | Dependencias runtime, contratos/validación, aislamiento de indicadores experimentales y defectos IA. | WS, buffer, frescura, refresco del análisis y pruebas de contratos. | Checks reproducibles, ninguna métrica ficticia ni conexión optimista; errores visibles y fixtures explícitas. |
| 0. Contratos | Parcial | Unificar esquemas, capacidades, entornos y reglas conocidas/pendientes. | Consolidar tokens y shell usando el brief; validar extensibilidad del gráfico actual. | Contratos documentados y probados; reglas desconocidas bloqueadas, sin fórmulas inventadas. |
| 1. Mercado + terminal | Componentes presentes; integración incompleta | Arranque único, histórico inicial, streaming, buffers, recuperación y salud temporal. | Suscripciones/reconexión, selector real, deltas, estados y móvil. | Reconexión sin duplicados ni fugas; HTTP/WS coherentes; datos atrasados visibles. |
| 2. Núcleo paper | No implementado | Órdenes, fills, libro, riesgo, persistencia y recuperación; estrategia fixture TEST_ONLY. | Cartera, órdenes, pausa/cierre y actividad por capacidades reales. | Ciclo automático completo y reinicio sin operaciones duplicadas; pausa mantiene gestión de posiciones. |
| 3. Método | Indicadores genéricos/experimentales; especificación bloqueada | Indicadores exactos disponibles, zonas causales, patrones y replay. | Capas, checklist y anotación manual. | Equivalencia numérica y concordancia con ejemplos; desconocidos bloqueados. |
| 4. Ollama | Cliente asesor presente; integración incompleta | Snapshots, escenarios, validación, límites, vigencia y registro versionado. | Integrar análisis asíncrono y escenarios con estados de indisponibilidad/caducidad. | Mercado fluido durante inferencia; respuesta obsoleta no provoca entrada; modelo real identificado. |
| 5. Integración demo | No implementada | Adaptador Binance oficial, filtros, reconciliación y fallos de órdenes. | Estado confirmado y fills del entorno demo. | Timeout, parcial y reconexión probados; sin credenciales live. |
| 6. Validación | No implementada como producto | Simulador causal, walk-forward, comparación A/B y estrés. | Laboratorio, resultados por versión/sesión, supervisión Bot, QA responsive y PWA opcional. | MVP demo integrado, reproducible y auditable; métricas con supuestos explícitos. |

Orden secuencial para OpenCode: cada fase se divide en microtasks pequeñas y autorizadas. Estabilizar mercado y análisis antes del acabado visual de Terminal; integrar shell/Terminal sin inventar cartera. El núcleo PAPER puede validarse con fixture determinista sin esperar fórmulas desconocidas. Laboratorio y Bot se integran conforme existan sus contratos. No hace falta desplegar públicamente.

## 8. Pruebas obligatorias

### Línea base verificada en el análisis previo (2026-10-03)

- Backend: ocho suites (`test_models`, `test_candle_builder`, `test_market_engine`, `test_binance_client`, `test_macd`, `test_app`, `test_market_api`, `test_bot_api`), **201 pruebas aprobadas** usando clientes en memoria/mocks. Una advertencia de deprecación Starlette/TestClient relacionada con `httpx`.
- Frontend: TypeScript de aplicación y configuración pasó con `--noEmit --incremental false`.
- No se ejecutaron build de producción, pruebas en navegador, capturas, integración real Binance/Ollama ni operaciones. No se encontraron tests frontend. Esta línea base no certifica FYL, riesgo, ejecución, rendimiento ni funcionalidades ausentes; tampoco equivale a una nueva ejecución durante esta actualización documental.

Comandos de referencia usados en esa revisión, desde `backend/` y `frontend/` respectivamente, sin instalar dependencias ni levantar servicios:

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -B -m pytest -q -p no:cacheprovider --capture=sys tests/test_models.py tests/test_candle_builder.py tests/test_market_engine.py tests/test_binance_client.py tests/test_macd.py tests/test_app.py tests/test_market_api.py tests/test_bot_api.py
```

```bash
./node_modules/.bin/tsc --noEmit --incremental false -p tsconfig.json
./node_modules/.bin/tsc --noEmit --incremental false -p tsconfig.node.json
```

Revalidar pruebas y código antes de repetirlos si el árbol cambia; los flags evitan caché/bytecode, pero no sustituyen aislamiento de red del sistema. Cualquier prueba con servicios externos requiere autorización y configuración explícitas.

### Cobertura pendiente y criterios obligatorios

- Unitarias: indicadores, pivotes confirmados, zonas, redondeos, tamaños, contabilidad y estados.
- Simulación: comisiones, spread, gap, stop/objetivo intrabar, parciales y saldo insuficiente.
- Integración: desconexión, duplicados, timeout de orden, rate limits, reinicio y reconciliación.
- IA: JSON incorrecto, evidencia inventada, timeout, propuesta caducada e intento de superar riesgo.
- Seguridad: credenciales ausentes en logs/UI, comandos no autorizados y contenido externo sin autoridad.
- E2E: arranque paper → candidato → análisis → riesgo → orden → posición → salida; pausa no desactiva gestión.
- Evaluación: datos disponibles solo hasta el instante de decisión; ajustes separados del conjunto final de prueba. Backtests del LLM no bastan por posible memoria de datos históricos; incorporar evaluación prospectiva congelada.
- Frontend: envelope/procedencia y velas inválidas; apertura lenta del WS, resuscripción, cierre intencional y desmontaje; intrabar, buffer acotado y carrera histórico/stream; refresco con longitud constante y errores de análisis.
- Backend pendiente inmediato: referencia numérica de Keltner, defectos FYL sin atribuirle el método, límites de histórico, datos insuficientes, liberación de callbacks WS y éxito/error IA con cliente falso.
- Visual/E2E: teclado, foco, contraste, reduced motion, números largos, safe areas, zoom 200%, desconexión y mercado visible mientras IA está ocupada. No marcarlo aprobado sin pruebas en navegador.

## 9. Reglas de trabajo para OpenCode

1. Este documento es un plan, no autorización para operar con dinero real.
2. Inspeccionar repo/AGENTS.md y preservar cambios existentes antes de implementar.
3. Un microtask con alcance, archivos, contrato, pruebas y aceptación explícitos.
4. No inventar indicadores ni resolver ambigüedades del método silenciosamente.
5. Usar dependencias probadas y pocas capas; no imponer límite artificial de líneas que elimine pruebas.
6. Elegir versiones y APIs contra documentación oficial al implementar; no copiar endpoints antiguos.
7. Entregar resumen de cambios, pruebas ejecutadas y pendientes; detenerse ante permisos/bloqueos.
8. No autooptimizar ni cambiar estrategia/prompt/modelo de la sesión evaluada. Toda mejora crea una nueva versión.
9. Una tarea activa cada vez; respetar archivos y alcance, verificar, documentar y detenerse. Si el alcance, propiedad de cambios o una decisión requerida es ambiguo, informar BLOCKED sin adivinar.
10. Distinguir datos ilustrativos, procedencia de mercado, entorno y capacidad operativa. Ni una respuesta HTTP exitosa ni un timeout autorizan a mostrar una ejecución confirmada.

## 10. Próximos microtasks sugeridos, no iniciados

El antiguo `TRADING-FOUNDATION-001` ya no es el siguiente paso: existen esqueleto, fixtures y contratos parciales. No recrearlos ni afirmar que se cumplió toda su aceptación responsive.

Propuesta de desglose para la fase S, cada elemento como tarea independiente:

1. **Dependencias runtime:** corregir la declaración de `httpx` y verificar importación/instalación sin depender del extra de pruebas.
2. **WebSocket frontend:** apertura confirmada, canales completos, resuscripción y limpieza sin sockets/timers supervivientes al desmontaje.
3. **Histórico y buffer:** validación de velas, límite efectivo, referencia sincronizada y reconciliación HTTP/WS sin sobrescribir datos más recientes.
4. **Análisis técnico:** refresco con longitud constante, errores visibles y política de barras explícita; conservar procedencia/intervalo y descarte de respuestas antiguas.
5. **Defectos backend acotados:** dividir validación de consultas, callbacks WS, detector experimental y ruta IA en microtasks separados con pruebas. No definir silenciosamente fórmulas del método.

Antes de ejecutar cualquiera, acordar un microtask con objetivo, archivos permitidos, contrato, pruebas y aceptación explícitos. Quedan fuera de estabilización: órdenes, live, promoción automática, reescritura de frontend y generación de diseños. La integración de mercado y el shell visual se planifican después de cerrar sus prerrequisitos, sin empezar desde esta actualización del plan.

## Referencias consultadas

- Binance Spot: https://github.com/binance/binance-spot-api-docs
- Ollama structured outputs: https://docs.ollama.com/capabilities/structured-outputs
- Limitaciones de backtesting: https://www.freqtrade.io/en/stable/backtesting/
- Lookahead: https://www.freqtrade.io/en/stable/lookahead-analysis/
- Memoria histórica en LLM: https://arxiv.org/abs/2504.14765

Las elecciones y presupuestos de este plan son propuestas de ingeniería, no afirmaciones de rentabilidad. Comprobar condiciones, APIs y disponibilidad de cuenta al implementar.
