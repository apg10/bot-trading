# Plan de desarrollo — Terminal privada con bot de trading e IA local

Fecha de actualización: 2026-10-07. Estado: base backend/frontend existente, con implementación parcial y estabilización pendiente; todavía no hay un bot operativo ni simulador PAPER.

Este plan distingue el estado observado de los requisitos objetivo. Complemento de diseño: [trading-console-visual-brief.md](trading-console-visual-brief.md). La actualización de este documento no implementa funcionalidades ni autoriza iniciar la siguiente tarea, conectarse a cuentas o operar.

## 0. Estado del repositorio y punto de partida

Inventario documental contrastado con la rama publicada `master`, commit `4383372b846ca019cd8008f96b464dd82aa3e49e`, el 2026-10-07. No representa cambios locales sin publicar ni una nueva ejecución completa de tests. Antes de cada microtask revalidar HEAD, árbol local, permisos y propiedad del trabajo existente. El plan de acción conserva el historial de pruebas y revisiones.

| Área | Estado observado | Pendiente para el producto objetivo |
|---|---|---|
| Frontend | React 19, TypeScript, Vite y CSS propio con tokens. Una única Terminal de desarrollo; símbolo activo BTC/USDT y watchlist estática BTC/ETH. | Navegación Terminal/Laboratorio/Bot, selección de símbolo/intervalo, controles funcionales y QA responsive/accesible. |
| Gráfico | Lightweight Charts 5.2.1: velas, zoom, desplazamiento y resize. Cada cambio usa `setData` completo. | Deltas, volumen, capas, zonas y paneles sincronizados; propuesta/orden/fill diferenciados. |
| Mercado backend | Clientes públicos Binance Spot HTTP/WS, `CandleBuilder`, `MarketEngine` y arranque único desde FastAPI; histórico, recuperación y control de frescura implementados. | Revalidar contratos y cobertura; persistir/versionar histórico para laboratorio y conservar propietario único. Aptitud de datos no implica ejecución. |
| Mercado frontend | Histórico con envelope de símbolo, intervalo y procedencia; filtros básicos de eventos y avisos `TEST_ONLY`. | Suscripción al abrir, canal `updates`, resuscripción, limpieza de timers, buffer acotado y reconciliación HTTP/WS. |
| Indicadores | MACD convencional y Keltner por lotes; FYL experimental. API valida insuficiencia, finitud, rango OHLC y orden temporal. Frontend utiliza identidad de snapshot/request tracker. B4.1 prepara las últimas velas cerradas propias. | Verificar referencia numérica, resolver o aislar FYL y conectar preparación con cálculo técnico; B4.1 no calcula indicadores ni construye contexto N02. MACD BB/método exacto siguen sin definir. |
| Reglas e IA | Reglas con indicadores fijos `TEST_ONLY`; N02 recibe snapshots tipados y devuelve escenarios estructurados, con modelo Qwen explícito, vigencia, límites y validación semántica. `AIAnalysisPanel` no está montado y conserva el contrato antiguo. | Contexto confiable construido desde backend, auditoría persistente y adaptación de UI. Reglas de cruce/incremento aún no comparan estado anterior; no promoverlas como triggers operativos. |
| Ejecución y cartera | Contratos declarativos; API niega ejecución/cartera y comandos del motor devuelven `501`. UI indica «Sin ejecución» y métricas no disponibles. | PAPER, órdenes/fills, libro, riesgo aplicado, pausa/cierre verificables y recuperación. No hay adaptador demo ni live. |
| Persistencia y laboratorio | Estado en memoria; `aiosqlite` declarado sin implementación de almacenamiento. No hay vista ni motor de experimentos. | SQLite, sesiones/versiones, replay causal, simulación, métricas y comparación de candidatos. |

Referencias principales: `frontend/src/App.tsx`, `frontend/src/components/ChartPanel.tsx`, `frontend/src/components/AnalysisPanel.tsx`, `frontend/src/hooks/useMarketData.ts`, `frontend/src/api/market.ts`, `backend/src/api/{main,market,analysis,bot}.py`, `backend/src/market/`, `backend/src/indicators/` y `backend/src/ai/ollama_client.py`.

No confundir modelos, componentes aislados o nombres de commits/fases con capacidades integradas. Los endpoints de mercado pueden devolver fixtures; una conexión WS abierta no acredita feed activo, frescura ni capacidad de ejecución.

## 1. Objetivo y alcance

Construir una terminal responsive privada para Binance Spot con análisis técnico, anotaciones sobre el gráfico, escenarios mediante Ollama y bot automático en simulación desde el primer MVP integrado. La utilidad competitiva objetivo es investigar hipótesis reproducibles, descartar variantes débiles y ejecutar reglas con riesgo y trazabilidad; la rentabilidad debe evaluarse por separado. Reducir infraestructura y código propio, no controles ni pruebas.

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
| IA | Conservar N02/Ollama asíncrono, contratos estructurados, concurrencia, vigencia y validación semántica existentes. Persistir auditoría y conectar contexto propio. Modelo explícito; validación de evidencias no demuestra calidad predictiva. |
| Transporte UI | Conservar HTTP y WS multiplexado de mercado. Extender por contrato a snapshot, secuencia y eventos operativos cuando existan; hoy no transporta órdenes/fills/cartera. |
| Operación | Entorno Python y npm local. `httpx` ya está declarado en runtime (ACT-S-001); instalación limpia sigue sin certificar. Contenedor opcional, no obligatorio. |

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

MACD convencional no es MACD BB. La estrategia predeterminada y sus puntuaciones son de desarrollo, no T1/V1/V2 validados ni probabilidades de ganar. Conservar la restricción Spot de N02; cualquier herramienta nueva admite largos/salida y no introduce cortos.

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

### Catálogos y contratos de estrategia (objetivo)
- Un catálogo backend versionado de indicadores: ID, fórmula/implementación, parámetros tipados, límites, unidades, calentamiento, barras admitidas y restricciones de causalidad. UI e IA consumen el mismo catálogo.
- Estrategia versionada: hipótesis, Spot long/salida, activos/temporalidad, filtros, trigger temporal, caducidad, invalidación, salidas, sizing, ejecución y bloqueos. Un cruce exige estado anterior y actual; una condición falsa no genera automáticamente señal contraria.
- Cada hipótesis investigada incluye al menos tres variantes predefinidas. Cambiar un aspecto por comparación; conservar datos, costos y presupuesto de riesgo equivalentes. No implica tres órdenes simultáneas ni que T1/V1/V2 estén especificados.
- Catálogo/estrategia no autoriza ejecución. Fixtures y métodos desconocidos conservan restricciones actuales. No registrar silenciosamente EMA/RSI/ADX/Donchian u otros parámetros sugeridos como una estrategia aprobada.

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
- `/api/bot/ai/strategy` ya es un alias de migración de escenarios, marcado deprecated; no recrear generación textual ni reabrir el defecto antiguo de importación. Verificar disponibilidad del modelo concreto en integración autorizada.

### Herramientas de investigación para IA (ampliación posterior)
- Contratos descubiertos por API con nombre, esquema/versiones, permisos, límites y respuestas estructuradas. MCP es un adaptador opcional futuro, no dependencia del MVP ni plataforma de plugins.
- Lectura de catálogos, cobertura/calidad de datos, resultados y trazas; después, creación acotada de experimentos y solicitud/cancelación de jobs de investigación. Estas escrituras no modifican estrategia activa, riesgo ni órdenes.
- Reutilizar servicios de dominio usados por UI/API; no dar shell, secretos ni autoridad directa de ejecución a Qwen. La ejecución financiera conserva sus contratos independientes.
- Consultas resumidas/paginadas con detalle bajo demanda; guardar IDs de herramientas, entradas, resultados, modelo/prompt y versiones. No almacenar razonamiento interno del modelo.
- Cupos de pruebas, concurrencia, tiempo, memoria y rangos autorizados; registrar fallos y todas las variantes intentadas. No conectar optimización automática a activación o promoción.

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
| `POST /api/analysis` | Indicadores sobre velas proporcionadas; rechaza menos de 50 velas y valores/rangos/tiempos inválidos. Sin fallback sintético implícito; `provided` no acredita procedencia ni frescura. |
| `GET /api/bot/status` | Bot no operativo, estrategia `TEST_ONLY`, cartera/ejecución no disponibles. |
| `POST /api/bot/analyze` | Propuesta sobre indicadores fijos; `TEST_ONLY`, `executable:false`. |
| `POST /api/bot/ai/scenarios`, `POST /api/bot/ai/analyze` | Reciben `{snapshot: ...}` y devuelven escenarios advisory N02 estructurados, no ejecutables. Procedencia declarada no certifica origen. |
| `POST /api/bot/ai/strategy` | Alias deprecated de N02; no genera/aplica una estrategia. |
| `GET /api/capabilities` | Inventario estático versionado; distingue soporte de código y ejecución ausente. No comprueba salud runtime. |

El frontend ya consume el envelope de histórico y conserva procedencia/intervalo; no queda pendiente volver a migrarlo desde un array. Sí falta validar cada vela y las respuestas/eventos, límites de consultas, orden temporal y coherencia OHLC. El fallback técnico implícito ya fue retirado; conservar el error de insuficiencia y la fixture explícita sin fabricar señales operativas. B4.1 es un helper interno, no un endpoint nuevo.

Desregistrar callbacks WS al desconectar, validar canales y añadir controles de origen/autenticación antes de acceso externo. Fijar contratos canónicos para velas, zonas, propuestas, órdenes y posiciones: existen familias duplicadas entre `models/state.py` y `bot/models.py`. Procedencia, entorno, conexión y frescura son dimensiones distintas.

#### Persistencia y endpoints objetivo

Tablas mínimas: configuración/versiones, sesiones de simulación, decisiones/análisis, órdenes/fills, revisiones de zonas y auditoría. Para laboratorio añadir versiones de datasets, estrategias, experimentos/variantes, runs/jobs y referencias a resultados/capital. Guardar fuente, cobertura, calidad, hash/identificador de datos, versión de código/motor, reloj, costos, riesgo y semilla cuando aplique. Runs inmutables; ajustes crean otra versión. Evitar duplicar balances sin reconciliación.

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

### Resultados, comparación y contexto compartido
- Laboratorio: configurar experimento → ejecutar jobs → comparar al menos tres variantes. Separar resultados de desarrollo, validación y prueba reservada, sin declarar ganadora por una sola métrica.
- Resultado normalizado con unidades: beneficio neto, costos, drawdown y duración, expectativa, profit factor, operaciones, exposición y métricas de retorno con frecuencia/supuestos explícitos. Datos insuficientes o métricas indefinidas no se sustituyen por cero.
- Curvas de capital y drawdown, referencia pertinente y rendimientos por período; mapa mensual solo con cobertura suficiente. Mostrar procedencia y cobertura, sintéticos, truncamiento/paginación y supuestos.
- Seleccionar trade abre gráfico temporal y detalle trigger → riesgo → orden → fills → salida, con versiones e IDs. Comparar también oportunidades donde una variante se abstuvo; no alinear trades distintos como si fueran iguales.
- Panel IA contextual: analizar resultado, comparar variantes o explicar una decisión solo cuando existan herramientas. Propuestas muestran cambios de parámetros y crean nuevas versiones; nunca activan por sí mismas.
- Paneles plegables/redimensionables y preferencias persistidas; selección de activo/run/trade compartida sin convertir un cambio de vista en reconfiguración oculta del motor. Mantener Terminal/Laboratorio/Bot; no agregar otra navegación incompatible.

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
| 1. Mercado + terminal | Motor integrado; Terminal parcial, cobertura por revalidar | Conservar arranque único, histórico/streaming y recuperación; verificar contratos y persistencia del histórico. | Suscripciones/reconexión, selector real, deltas, estados y móvil. | Reconexión sin duplicados ni fugas; HTTP/WS coherentes; datos atrasados visibles. |
| 2. Núcleo paper | No implementado | Órdenes, fills, libro, riesgo, persistencia y recuperación; estrategia fixture TEST_ONLY. | Cartera, órdenes, pausa/cierre y actividad por capacidades reales. | Ciclo automático completo y reinicio sin operaciones duplicadas; pausa mantiene gestión de posiciones. |
| 3. Método | Indicadores genéricos/experimentales; especificación bloqueada | Indicadores exactos disponibles, zonas causales, patrones y replay. | Capas, checklist y anotación manual. | Equivalencia numérica y concordancia con ejemplos; desconocidos bloqueados. |
| 4. Ollama | N02 estructurado presente; contexto propio/UI/persistencia pendientes | Conservar snapshots, escenarios, validación, límites y vigencia; conectar contexto propio y auditoría versionada. | Integrar análisis asíncrono y escenarios con estados de indisponibilidad/caducidad. | Mercado fluido durante inferencia; respuesta obsoleta no provoca entrada; modelo real identificado. |
| 5. Integración demo | No implementada | Adaptador Binance oficial, filtros, reconciliación y fallos de órdenes. | Estado confirmado y fills del entorno demo. | Timeout, parcial y reconexión probados; sin credenciales live. |
| 6. Validación | No implementada como producto | Simulador causal, walk-forward, comparación A/B y estrés. | Laboratorio, resultados por versión/sesión, supervisión Bot, QA responsive y PWA opcional. | MVP demo integrado, reproducible y auditable; métricas con supuestos explícitos. |

Las fases conservan sus IDs históricos. El orden operativo canónico es el del plan de acción: H0 → H1 → H2 → H3/H4 → H5; laboratorio PAPER/replay básico antes de EXCHANGE_DEMO y evaluación/estrés final después. El número de fase no autoriza saltar dependencias.

Orden secuencial para OpenCode: cada fase se divide en microtasks pequeñas y autorizadas. Estabilizar mercado y análisis antes del acabado visual de Terminal; integrar shell/Terminal sin inventar cartera. El núcleo PAPER puede validarse con fixture determinista sin esperar fórmulas desconocidas. Laboratorio y Bot se integran conforme existan sus contratos. No hace falta desplegar públicamente.

### Laboratorio backend y aceptación de investigación
- Reutilizar estrategia, indicadores, libro y riesgo del núcleo PAPER. Replay cambia reloj/fuente/adaptador de ejecución; no crear una lógica financiera paralela.
- Un contrato pequeño para el motor de backtesting y resultados. Primer motor único; adaptadores adicionales solo si una necesidad medida los justifica. Un motor vectorizado futuro no se presume equivalente al simulador operativo.
- Jobs asíncronos con IDs, estados, cancelación, límites y errores. Retomar tras reinicio o marcar interrupción explícita según contrato; ningún trabajo desaparece silenciosamente ni se duplica al reintentar.
- Señal de cierre ejecutable solo después de conocerse el cierre. Gaps, parcialidad, intrabar y costos reutilizan política PAPER documentada.
- Desarrollo/validación/test cronológicos, calentamiento sin contaminar resultados y control de ventanas solapadas cuando aplique. Registrar todos los intentos; usar el test final para ajustar lo convierte en datos de desarrollo.
- Robustez por períodos/activos/regímenes, sensibilidad a parámetros/costos y referencia pertinente. Umbrales de aceptación se fijan antes de evaluar, no son números financieros decididos por este plan.
- Evaluación de IA separada: datos disponibles entonces, auditoría de modelo/prompt/respuesta y validación prospectiva congelada. Backtest de reglas no acredita ventaja de Qwen.

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

La lista anterior es histórica: ACT-S-001 y las validaciones B3 ya tienen cierres registrados; no emitirlos otra vez por esta lista. B4.1 REV-002 fue aceptada manualmente en conversación, con 16 passed reportados y sin repetición local de pytest. Siguiente candidato de backend: cálculo técnico desde `PreparedMarketAnalysis`, con procedencia/as_of y cálculos existentes; definir contrato/allowlist/checks antes de emitirlo. No incluye FYL, N02, endpoint, persistencia ni UI salvo autorización explícita de otro microtask.

Antes de ejecutar cualquiera, acordar un microtask con objetivo, archivos permitidos, contrato, pruebas y aceptación explícitos. Quedan fuera de estabilización: órdenes, live, promoción automática, reescritura de frontend y generación de diseños. La integración de mercado y el shell visual se planifican después de cerrar sus prerrequisitos, sin empezar desde esta actualización del plan.

## 11. Incorporación de referencias Fincept (2026-10-07)

Fincept es referencia de patrones, no dependencia ni código importado. Se conservarán React/FastAPI/SQLite/Lightweight Charts y Binance Spot; no C++/Qt, Redis, múltiples brokers, noticias, constructor visual de nodos, agentes adicionales ni promoción automática en la cola inmediata. Si se propone reutilizar código, revisar licencia y compatibilidad en un task aparte.

| Patrón | Dueño en el plan de acción | Aceptación / límite |
|---|---|---|
| Catálogo backend y estrategia/versiones | ACT-C-001/002/003 | UI/IA comparten contrato; soporte separado de salud/ejecución |
| Datos compartidos | ACT-S-005; ACT-M-005; ACT-Q-002 | Reutilizar MarketEngine y transporte; dos consumidores no crean dos productores |
| Órdenes/fills y trazabilidad | ACT-P-002/003/005/007; ACT-B-001 | Libro, estados e IDs reales; propuesta no es fill |
| Backtesting y resultados normalizados | ACT-L-001/002/004; ACT-Q-001 | Un motor inicial, causalidad, jobs reproducibles y tres variantes |
| IA consume herramientas | ACT-A-001/004 | Investigación acotada, auditoría y sin envío de órdenes |
| Reportes, trade inspector y paneles | ACT-T-001/002/003/004/005; ACT-L-003; ACT-B-001 | UI conforme a backend, detalle vinculado y datos insuficientes visibles |

Los nuevos IDs son backlog documental, no tasks iniciados ni un cambio de prioridades de la cola activa. Catálogos preceden investigación; persistencia/núcleo PAPER preceden laboratorio; herramientas IA de investigación pueden llegar después del laboratorio mínimo sin bloquearlo.

## Referencias consultadas

- Fincept Terminal (referencia de arquitectura, catálogos, backtesting y paneles; no auditado integralmente): https://github.com/Fincept-Corporation/FinceptTerminal
- Binance Spot: https://github.com/binance/binance-spot-api-docs
- Ollama structured outputs: https://docs.ollama.com/capabilities/structured-outputs
- Limitaciones de backtesting: https://www.freqtrade.io/en/stable/backtesting/
- Lookahead: https://www.freqtrade.io/en/stable/lookahead-analysis/
- Memoria histórica en LLM: https://arxiv.org/abs/2504.14765

Las elecciones y presupuestos de este plan son propuestas de ingeniería, no afirmaciones de rentabilidad. Comprobar condiciones, APIs y disponibilidad de cuenta al implementar.
