# Plan de desarrollo — Terminal privada con bot de trading e IA local

Fecha de actualización: 2026-10-07. Estado: motor público Spot integrado y análisis parcialmente integrado; todavía no hay un bot operativo ni simulador PAPER.

Este plan distingue el estado observado de los requisitos objetivo. Complemento de diseño: [trading-console-visual-brief.md](trading-console-visual-brief.md). La actualización de este documento no implementa funcionalidades ni autoriza iniciar la siguiente tarea, conectarse a cuentas o operar.

## 0. Estado del repositorio y punto de partida

Inventario reconciliado documentalmente con el contexto verificado y las aceptaciones del plan de acción raíz, sobre la baseline `master`, commit `4383372b846ca019cd8008f96b464dd82aa3e49e`. Esta actualización no constituye una nueva auditoría de código ni una ejecución de pruebas. Antes de cada microtask se debe revalidar el estado y preservar el trabajo ajeno.

El [plan de acción raíz](trading-bot-action-plan.md) es la única autoridad de estados, aceptaciones, dependencias y siguiente task. Este plan conserva arquitectura y requisitos objetivo; el brief define diseño. Los documentos de `trading-plans-2026-10-07/` son propuestas fechadas sin autoridad operativa, no sustituyen la raíz y se conservan intactos.

| Área | Estado observado | Pendiente para el producto objetivo |
|---|---|---|
| Frontend | React 19, TypeScript, Vite y CSS propio con tokens. Una única Terminal de desarrollo; símbolo activo BTC/USDT y watchlist estática BTC/ETH. | Navegación Terminal/Laboratorio/Bot, selección de símbolo/intervalo, controles funcionales y QA responsive/accesible. |
| Gráfico | Lightweight Charts 5.2.1: velas, zoom, desplazamiento y resize. Cada cambio usa `setData` completo. | Deltas, volumen, capas, zonas y paneles sincronizados; propuesta/orden/fill diferenciados. |
| Mercado backend | Clientes públicos Binance Spot HTTP/WS, `CandleBuilder` y `MarketEngine` integrados desde FastAPI, con histórico, recuperación de huecos y control de frescura. | Conservar propietario único y garantías existentes; histórico persistido/versionado para laboratorio pendiente. Aptitud de datos no implica ejecución. |
| Mercado frontend | Histórico con envelope de símbolo, intervalo y procedencia; filtros de eventos y avisos `TEST_ONLY`. | Revalidar apertura/canales, resuscripción, limpieza, buffer y reconciliación HTTP/WS según ACT-M-001/002/005; no inferir ausencia ni repetir trabajo por esta lista. |
| Indicadores | MACD convencional y Keltner por lotes; FYL experimental. B3 acepta validaciones de insuficiencia, finitud, rango OHLC y orden temporal. Frontend usa identidad de snapshot/request tracker. B4.1 prepara últimas velas cerradas propias. | Referencia numérica de Keltner: convención aprobada para la implementación actual (TR[0]=0; EMA con SMA inicial + alpha=2/(n+1); ATR con promedio simple inicial + recurrencia Wilder; bandas = EMA ± multiplier*ATR; warmup = ema_period+atr_period). Defaults 20/14/2.0 son configurables, no mandato por estrategia. Defectos FYL por resolver o aislar; método/MACD BB sin definir. B4.2 aceptado (COMPLETADO en su alcance, §5 plan de acción). B4.1 no calcula ni construye contexto N02. |
| Reglas e IA | Reglas con indicadores fijos `TEST_ONLY`; cliente N02/Ollama de escenarios estructurados con Qwen explícito, vigencia, límites y validación semántica. `AIAnalysisPanel` no está montado y conserva el contrato antiguo. | Contexto propio confiable, integración UI y auditoría persistente pendientes. Cruces/incrementos sin comparación temporal no son triggers operativos. |
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
| Backend | Conservar FastAPI y asyncio, arranque integrado y propietario único del motor. Mantener un proceso/worker; no presumir propiedad única entre múltiples workers. |
| Cálculos | Python y funciones puras; NumPy solo cuando sea útil. Los cálculos actuales son por lotes; incrementalidad y equivalencia quedan pendientes. |
| Persistencia | Objetivo pendiente: SQLite, transacciones, migraciones simples y escritura serializada. No guardar cada tick indefinidamente en tablas de decisiones. |
| Histórico bruto | Diseño pendiente: archivos segmentados y acotados; elegir formato tras medir volumen. SQLite conservará metadatos y referencias. |
| IA | Conservar N02/Ollama asíncrono, contratos estructurados, concurrencia limitada, vigencia y validación semántica existentes. Contexto propio y auditoría persistente pendientes. Modelo explícito; validar evidencias no acredita calidad predictiva. |
| Transporte UI | Conservar HTTP y WS multiplexado de mercado. Extender por contrato a snapshot, secuencia y eventos operativos cuando existan; hoy no transporta órdenes/fills/cartera. |
| Operación | Entorno Python y npm local. `httpx` ya está declarado en runtime (ACT-S-001 aceptado); instalación limpia no certificada. Contenedor opcional, no obligatorio. |

Sin Redis, Kubernetes, microservicios, múltiples usuarios, plataforma de plugins ni abstracciones para muchos exchanges. No elegir Rust/C++ sin un cuello de botella medido.

## 3. Bloqueos del método original

Antes de afirmar que reproducimos T1/V1/V2 necesitamos:
1. Fórmula y parámetros exactos de FYL original, MACD BB; significado preciso de BBs y niveles «30». La convención Keltner de la implementación actual ya está aprobada y documentada en §19 (no afirma equivalencia con otro método ni hace obligatorios los parámetros para todas las estrategias).
2. Tipo de barras, construcción, sesiones y calentamiento. La captura de 610 ticks no implica que 610 operaciones de BTC sea equivalente.
3. Reglas numéricas de impulso, retroceso, consolidación, pendiente y tolerancia de zonas.
4. Resolver exactamente tres barras de FYL plana y pequeñas consolidaciones admitidas en T1.
5. Fórmulas de entrada, caducidad, stop, objetivo, salida y tamaño.
6. Momento en que pivotes/áreas pueden confirmarse sin usar el futuro.

Se puede desarrollar infraestructura y probar el ciclo completo con una estrategia fixture determinista sobre datos sintéticos, etiquetada TEST_ONLY. No inventar FYL ni presentar otra estrategia como el método original. T1/V1/V2 permanecen deshabilitados para operar mientras falten reglas.

El módulo actual `backend/src/indicators/fyl.py` ya tiene strength genérica corregida para umbral 0.01 y timestamps incrementales basados en open_time de origen (verificados localmente, no committed). El método FYL original sigue sin especificación; batch continúa TEST_ONLY con futuro, no válido para replay causal ni señales en vivo.

MACD convencional no es MACD BB. La estrategia predeterminada y sus puntuaciones son de desarrollo, no T1/V1/V2 validados ni probabilidades de ganar. Conservar la restricción Spot de N02 y de cualquier herramienta: largos/salida, sin introducir cortos. La convención Keltner aprobada para la implementación actual (§19) no afirma equivalencia con otro método ni hace obligatorios los parámetros defaults (20/14/2.0) para todas las estrategias.

## 4. Backend

Los apartados siguientes describen garantías a conservar y requisitos objetivo pendientes; no son una lista de implementaciones por repetir ni de funciones disponibles. El inventario y las aceptaciones raíz distinguen ambos casos. No implementar órdenes para completar un mockup.

### Mercado
- Una conexión compartida a Binance; historial por HTTP y eventos en vivo por WebSocket.
- Normalizar símbolo, timestamps UTC, identificador de operación y procedencia.
- Detectar duplicados, huecos, eventos fuera de orden, cierre de barra y antigüedad.
- Conservar reconexión con backoff, histórico inicial y recuperación de huecos integrados; datos incompletos deberán bloquear entradas cuando exista ejecución. Detectar un gap no equivale a repararlo.
- Para barras por ticks, definir qué evento cuenta como tick. No sustituir operaciones individuales por operaciones agregadas sin justificar el cambio.
- Separar barras cerradas de barra en formación y declarar cuál utiliza cada regla.
- Mantener buffers acotados y cargar histórico adicional bajo demanda.
- Conservar timestamps de mercado y añadir recepción local y edad. `close_time` es cierre del intervalo, no hora de recepción; no llamarlo latencia ni «última actualización».
- Conservar arranque/parada de una única instancia desde el ciclo de vida de la app, con configuración explícita y apagado limpio. Pruebas automáticas mediante clientes simulados, sin conexiones externas implícitas.

### Indicadores, zonas y estrategia
- Mismo motor numérico para replay y ejecución en vivo.
- Actualización incremental y pruebas de equivalencia contra cálculo por lotes.
- Zonas con ID, rango, contactos, origen, instante de detección, confirmación, revisión e invalidación.
- Conservar revisiones; no reescribir retrospectivamente cuándo una zona era conocida.
- Motor de estados: observando → candidato → pendiente de análisis → aprobado/rechazado/caducado → orden. Posiciones se gestionan en un ciclo aparte.
- Cada candidato guarda estrategia/versión, snapshot de mercado y condiciones verificadas.

### Contratos y catálogos versionados (objetivo, ACT-C-001)
- Contrato común de estrategia, indicadores y datos para backend, UI e IA: IDs/versiones, parámetros tipados, límites, unidades, calentamiento, barras admitidas, procedencia y restricciones de causalidad.
- Estrategia: hipótesis, alcance Spot long/salida, filtros, trigger temporal, caducidad, invalidación, salidas, sizing y bloqueos. Un cruce requiere estado anterior y actual; una condición falsa no genera señal contraria.
- El catálogo dinámico completo es una ampliación; no bloquea B4.2 ni habilita métodos desconocidos. Registrar una implementación no equivale a aprobar una estrategia ni a autorizar ejecución. Esta reconciliación reutiliza ACT-C-001, sin añadir IDs duplicados.

### IA de escenarios
- Conservar el cliente N02 existente; construcción de contexto propio y auditoría/integración pendientes se asignan a ACT-A-002/001/003, sin recrear el cliente ni atribuirle una aceptación global nueva.
- Snapshot compacto con características numéricas, zonas confirmadas, checklist, cartera y reglas.
- Respuesta: evaluación, evidencias referenciadas, escenarios de continuación/fallo/lateralización, datos faltantes e invalidación.
- Precio y tamaño de órdenes se calculan/validan en código, no desde texto libre.
- JSON schema más validación semántica: referencias existentes, fechas válidas y acciones permitidas.
- Conservar timeout, máximo de salida, concurrencia y vigencia existentes; deduplicación según contrato. Revalidar mercado, estrategia y riesgo tras recibir respuesta cuando se integre al ciclo PAPER.
- Abstención y errores bloquean entradas que dependan de IA; nunca bloquean salidas protectoras.
- Persistir versión del modelo, prompt, parámetros, duración, snapshot y respuesta.
- Herramientas opcionales inicialmente solo lectura; modelo sin secretos, shell, retiros ni envío directo de órdenes.
- Noticias fuera del primer sprint; al añadirlas guardar fuente, publicación y recepción, y tratarlas como contenido no confiable.
- `/api/bot/ai/strategy` es un alias deprecated de escenarios N02; el antiguo defecto de importación es histórico y no debe reabrirse ni recrearse la generación textual. La disponibilidad del modelo concreto requiere integración autorizada, no solo una respuesta de `/api/tags`.

### Herramientas estructuradas de investigación (ampliación posterior)
- ACT-A-004, dependencias ACT-C-001, ACT-A-001 y ACT-L-002: contratos con nombre, esquema/versiones, permisos, límites y resultados estructurados para Qwen. No bloquea el backend inmediato ni añade una puerta H4.
- Lectura de catálogos, calidad/cobertura de datos, resultados y trazas; solicitud/cancelación acotada de experimentos de investigación cuando exista soporte. Reutilizar servicios comunes con UI/API, con paginación, cuotas y registro de todos los intentos.
- Auditar entradas/resultados y versiones de modelo/prompt sin razonamiento interno. Sin shell, secretos, cambios de riesgo, envío de órdenes ni activación de estrategias. MCP es adaptador opcional, no plataforma requerida.

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
| `POST /api/analysis` | Indicadores sobre velas proporcionadas; B3 rechaza menos de 50 velas y valores/rangos/tiempos inválidos, sin fallback sintético implícito. `provided` no acredita fuente real ni frescura. |
| `GET /api/bot/status` | Bot no operativo, estrategia `TEST_ONLY`, cartera/ejecución no disponibles. |
| `POST /api/bot/analyze` | Propuesta sobre indicadores fijos; `TEST_ONLY`, `executable:false`. |
| `POST /api/bot/ai/scenarios`, `POST /api/bot/ai/analyze` | Reciben `{snapshot: ...}` y devuelven escenarios advisory N02 estructurados, no ejecutables; la procedencia declarada no certifica origen. |
| `POST /api/bot/ai/strategy` | Alias deprecated de N02, sin generación/aplicación de estrategia. |
| `GET /api/capabilities` | B2 aceptado: inventario estático versionado, informativo y sin efectos. No certifica salud runtime ni ejecución. |

El frontend ya consume el envelope de histórico y conserva procedencia/intervalo; no queda pendiente volver a migrarlo desde un array. La validación de respuestas/eventos y la reconciliación del transporte UI siguen sujetas al plan de acción. No reimplementar validaciones B3 aceptadas: conservar insuficiencia y fixture explícita separadas, sin fabricar señales operativas. B4.1 es un helper interno con `PreparedMarketAnalysis`, no un endpoint ni una integración N02.

Conservar la liberación de callbacks WS existente y revalidar cobertura antes de abrir una reparación; contratos/canales se revisan según ACT-S-005. Añadir controles de origen/autenticación antes de acceso externo. ACT-C-001 gobierna la unificación contractual de velas, zonas, propuestas, órdenes y posiciones; no modificar familias duplicadas por iniciativa documental. Procedencia, entorno, conexión y frescura son dimensiones distintas.

#### Persistencia y endpoints objetivo

Tablas mínimas: configuración/versiones, sesiones de simulación, decisiones/analisis, órdenes/fills, revisiones de zonas y eventos de auditoría. Evitar duplicar balances sin mecanismo de reconciliación.

ACT-P-001 conserva versiones/referencias de datasets, estrategias, experimentos y resultados: fuente, cobertura/calidad, identificador o hash de datos, código/motor, reloj, costes, riesgo y semilla cuando aplique. Los runs conservan sus supuestos; los ajustes crean otra versión. Es persistencia objetivo, todavía no implementada.

Endpoints orientativos: GET estado; GET histórico paginado; GET zonas; GET decisiones; GET cartera/órdenes; POST solicitar análisis; POST pausar/reanudar entradas; POST solicitar cierre y detención; GET/PATCH configuración validada.

Comandos con ID para prevenir duplicados. Cierre y detención es un proceso verificable: cancelar entradas pendientes, solicitar cierres, confirmar resultado y señalar fallos. El botón no garantiza un cierre instantáneo.

## 5. Frontend

Conservar `ChartPanel`, `AnalysisPanel`, el hook de mercado y los tokens existentes. Integrar `AIAnalysisPanel` solo al completar su contrato y política de solicitud. `BottomBar` no está montado y conserva métricas fijas y pestañas aparentes: corregirlo antes de reutilizarlo. Mientras no exista cartera, mantener «no consultada/no disponible», no ceros ficticios.

La aplicación activa es una única Terminal de desarrollo: identifica fixtures, informa ausencia de ejecución y conserva el intervalo real en análisis. Terminal/Laboratorio/Bot son tres vistas previstas, no tres vistas existentes. No reintroducir porcentajes aleatorios, estado de bot simulado o acciones aparentes. Conservar identidad de snapshot/request tracker; cualquier gap del refresco se delimita en el plan de acción, sin repetir implementaciones aceptadas.

### Terminal
- Barra superior: navegación Terminal/Laboratorio/Bot y PAPER/EXCHANGE_DEMO solo si el backend confirma ese entorno y su capacidad. Mientras no exista ejecución, conservar la advertencia explícita de desarrollo; mostrar estado de datos y modelo sin fingir disponibilidad.
- Lista de activos a la izquierda; gráfico central; análisis y checklist a la derecha.
- Precio/Keltner arriba; MACD convencional identificado como tal. FYL experimental y zonas según capacidad/causalidad; MACD BB sincronizado solo cuando tenga especificación e implementación aprobadas.
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

### Resultados, inspector y contexto compartido
- ACT-L-002: mínimo tres variantes predefinidas A/B/C o IDs estables por hipótesis, con datos, períodos, costes y presupuesto de riesgo comparables. Registrar todos los intentos, incluidos fallidos/descartados; no implica tres órdenes simultáneas ni define T1/V1/V2.
- Resultados normalizados con unidades/supuestos: beneficio neto, costes, drawdown, operaciones y exposición; métricas ampliadas bajo demanda. Separar desarrollo/validación/test reservado y referencia pertinente. Datos insuficientes o métricas indefinidas no se sustituyen por cero.
- ACT-L-003: inspector ligado al gráfico con trigger → riesgo → orden → fills → salida, IDs/versiones y señales rechazadas. Comparar abstenciones sin alinear trades distintos como si fueran iguales.
- ACT-A-002 aporta contexto verificable; ACT-A-003 asesoría sin autoridad de ejecución; ACT-A-001 auditoría. Acciones contextuales de investigación requieren ACT-A-004 y contratos disponibles; propuestas crean versiones, no activan estrategias.
- ACT-T-001…005/ACT-L-003/ACT-B-001 organizan paneles contextuales plegables/redimensionables y selección compartida de activo/run/trade según capacidad; ACT-P-001 cubre persistencia versionada. Las preferencias de vista no reconfiguran el motor ni aparentan persistencia inexistente.

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

Continuar desde las aceptaciones del plan de acción, sin recrear el esqueleto ni implementaciones cerradas. Las fases mantienen su numeración histórica; la tabla resume alcance y no sustituye estados, dependencias o aceptación de tasks.

| Fase | Estado actual | Backend / trabajo pendiente | Frontend / trabajo pendiente | Puerta de salida |
|---|---|---|---|---|
| S. Estabilización | Parcial; cierres en plan de acción | Conservar ACT-S-001, B2, B3 y B4.1 aceptados; pendientes acotados de contratos y aislamiento experimental. | WS, buffer y frescura según gaps registrados; conservar identidad de análisis. | Evidencia por task, ninguna métrica ficticia ni conexión optimista; errores visibles y fixtures explícitas. |
| 0. Contratos | Parcial | Unificar esquemas, capacidades, entornos y reglas conocidas/pendientes. | Consolidar tokens y shell usando el brief; validar extensibilidad del gráfico actual. | Contratos documentados y probados; reglas desconocidas bloqueadas, sin fórmulas inventadas. |
| 1. Mercado + terminal | Motor integrado; Terminal parcial | Conservar arranque único, histórico, streaming, recuperación y frescura; no repetir su integración. | Suscripciones/reconexión, selector real, deltas, estados y móvil según plan de acción. | Reconexión sin duplicados ni fugas; HTTP/WS coherentes; datos atrasados visibles. |
| 2. Núcleo paper | No implementado | Órdenes, fills, libro, riesgo, persistencia y recuperación; estrategia fixture TEST_ONLY. | Cartera, órdenes, pausa/cierre y actividad por capacidades reales. | Ciclo automático completo y reinicio sin operaciones duplicadas; pausa mantiene gestión de posiciones. |
| 3. Método | Indicadores genéricos/experimentales; especificación bloqueada | Indicadores exactos cuando se especifiquen, zonas causales, patrones y replay. | Capas, checklist y anotación manual según capacidad. | Equivalencia numérica y concordancia con ejemplos; desconocidos bloqueados. |
| 4. Ollama | N02 estructurado presente; integración propia pendiente | Conservar escenarios, validación, límites y vigencia; contexto propio y auditoría persistente pendientes. | Integrar escenarios con estados de indisponibilidad/caducidad. | Mercado fluido durante inferencia; respuesta obsoleta no provoca entrada; modelo real identificado. |
| 5. Integración demo | No implementada | Adaptador Binance oficial, filtros, reconciliación y fallos de órdenes. | Estado confirmado y fills del entorno demo. | Timeout, parcial y reconexión probados; sin credenciales live. |
| 6. Validación | No implementada como producto | Replay causal, comparación de mínimo tres variantes A/B/C, evaluación congelada y estrés. | Laboratorio, resultados por versión/sesión, supervisión Bot, QA responsive y PWA opcional. | MVP demo integrado, reproducible y auditable; métricas con supuestos explícitos, sin acreditar rentabilidad por existir simulación. |

Orden secuencial para OpenCode: cada fase se divide en microtasks pequeñas y autorizadas. Estabilizar mercado y análisis antes del acabado visual de Terminal; integrar shell/Terminal sin inventar cartera. El núcleo PAPER puede validarse con fixture determinista sin esperar fórmulas desconocidas. Laboratorio y Bot se integran conforme existan sus contratos. No hace falta desplegar públicamente.

Orden de hitos canónico: **H0 → H1 → H2 → H3/H4 → H5**, con dependencias exclusivamente en el plan de acción. PAPER/replay y comparación básicos preceden EXCHANGE_DEMO; la numeración histórica de fases no permite saltar hitos.

Mínimo funcional futuro: ACT-L-001 reutiliza indicadores, estrategia, libro, riesgo y política PAPER, cambiando reloj/fuente/adaptador sin lógica financiera paralela; ACT-L-002 aporta experimentos reproducibles y resultados; ACT-L-003 su consulta e inspector. ACT-Q-001 separa desarrollo/validación/test y evaluación prospectiva congelada de IA. Persistencia, PAPER y replay siguen pendientes.

Ampliaciones: catálogo dinámico completo, ACT-A-004 research Qwen, jobs avanzados/recuperación y visualizaciones enriquecidas. Los jobs muestran IDs/estados/errores reales y progreso solo medible cuando exista soporte. Esta reconciliación no añade ACT-L-004, puertas H4 ni nuevas dependencias a tasks existentes; estas ampliaciones no bloquean B4.2.

## 8. Pruebas obligatorias

### Línea base verificada en el análisis previo (2026-10-03)

Registro histórico de aquella revisión: no describe una suite ejecutada en 2026-10-07 ni sustituye aceptaciones posteriores. Los comandos siguientes son referencias históricas, no instrucciones de ejecución para esta reconciliación.

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
- Backend: referencia numérica de Keltner (convención aprobada para implementación actual: TR[0]=0, EMA con SMA inicial + alpha=2/(n+1), ATR con promedio simple inicial + recurrencia Wilder; fixture OHLCV 8bar-v1 test en `test_keltner.py::test_keltner_reference_numeric_contract`), aislamiento FYL y gaps de histórico/callbacks se delimitan según el plan de acción. Insuficiencia y validaciones B3 ya aceptadas no son implementaciones pendientes; N02 existente no debe recrearse. La lista de cobertura objetivo no selecciona el siguiente task.
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

## 10. Referencias históricas — sin paquetes pendientes

El antiguo `TRADING-FOUNDATION-001` ya no es el siguiente paso: existen esqueleto, fixtures y contratos parciales. No recrearlos ni afirmar que se cumplió toda su aceptación responsive.

Desglose histórico de la fase S, superseded por el seguimiento raíz. No es una cola activa ni permite reemitir cierres:

1. **Dependencias runtime:** corregir la declaración de `httpx` y verificar importación/instalación sin depender del extra de pruebas.
2. **WebSocket frontend:** apertura confirmada, canales completos, resuscripción y limpieza sin sockets/timers supervivientes al desmontaje.
3. **Histórico y buffer:** validación de velas, límite efectivo, referencia sincronizada y reconciliación HTTP/WS sin sobrescribir datos más recientes.
4. **Análisis técnico:** refresco con longitud constante, errores visibles y política de barras explícita; conservar procedencia/intervalo y descarte de respuestas antiguas.
5. **Defectos backend acotados:** dividir validación de consultas, callbacks WS, detector experimental y ruta IA en microtasks separados con pruebas. No definir silenciosamente fórmulas del método.

ACT-S-001, B2 y B3 tienen aceptaciones registradas. B4.1: REV-001 fue REQUIRES_CHANGES con 14 tests reportados; REV-002 fue aceptada con 16 tests y probe independiente offline conforme al plan de acción raíz. Las entregas y baselines anteriores son históricas, no aceptaciones actuales adicionales.

B4.2: BACKEND-MARKET-ANALYSIS-API-001 / B4.2, GROUP-BACKEND-001, RUN-MARKET-API-001-001, REV-001 — **COMPLETADO en su alcance** (aceptación manual delegada, 18+42 tests). Expuso API desde `PreparedMarketAnalysis` reutilizando `await analyze(prepared.request)`, sin componente de cálculo nuevo. Esta referencia es historial; no modifica su paquete, alcance o aceptación; no incluyó N02, IA, persistencia, cartera, ejecución ni UI.

Automatización: ACT-AUTO-001 completo; ACT-AUTO-002 parcial y PAUSADO. Las instrucciones anteriores que los seleccionaban son históricas/superseded; no reactivar ni construir infraestructura desde este plan. El plan de acción es la única autoridad operativa.

## 11. Patrones Fincept y responsables compartidos

Fincept aporta patrones de organización, no código importado ni una dependencia. Conservar React/FastAPI/SQLite/Lightweight Charts y Binance Spot; no introducir frameworks, brokers, plataformas o motores adicionales por esta referencia.

| Patrón objetivo | Responsable existente/acordado | Límite |
|---|---|---|
| Contratos y catálogo versionado de estrategia/indicadores/datos | ACT-C-001 | Fuente común UI/IA; soporte no es salud ni ejecución |
| Persistencia de versiones y sesiones | ACT-P-001 | Conservar supuestos y referencias, sin declarar almacenamiento existente |
| Replay causal | ACT-L-001 | Reutilizar núcleo PAPER, sin futuro ni lógica paralela |
| Experimentos/resultados A/B/C | ACT-L-002; ACT-Q-001 | Mínimo tres variantes, todos los intentos, costes/períodos comparables y evaluación separada |
| Laboratorio e inspector | ACT-L-003 | Detalle ligado al gráfico, IDs y rechazos reales |
| Paneles, contexto y preferencias | ACT-T-001…005; ACT-B-001; ACT-P-001 | Según capacidad; selección visual no reconfigura motor |
| Contexto, asesoría y auditoría IA | ACT-A-002; ACT-A-003; ACT-A-001 | Evidencias verificables, sin autoridad de ejecución |
| Herramientas estructuradas research Qwen | ACT-A-004 | Ampliación posterior; dependencias ACT-C-001, ACT-A-001, ACT-L-002 |

Este mapping no cambia dependencias anteriores ni crea gates H4. Catálogos completos, research y jobs avanzados no bloquean el backend inmediato; las capacidades se incorporan conforme a contratos y autorización del plan de acción.

## Referencias consultadas

- Fincept Terminal (referencia documental de patrones, sin nueva consulta de red): https://github.com/Fincept-Corporation/FinceptTerminal
- Binance Spot: https://github.com/binance/binance-spot-api-docs
- Ollama structured outputs: https://docs.ollama.com/capabilities/structured-outputs
- Limitaciones de backtesting: https://www.freqtrade.io/en/stable/backtesting/
- Lookahead: https://www.freqtrade.io/en/stable/lookahead-analysis/
- Memoria histórica en LLM: https://arxiv.org/abs/2504.14765

Las elecciones y presupuestos de este plan son propuestas de ingeniería, no afirmaciones de rentabilidad. Comprobar condiciones, APIs y disponibilidad de cuenta al implementar.
