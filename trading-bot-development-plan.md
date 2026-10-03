# Plan de desarrollo — Terminal privada con bot de trading e IA local

Fecha: 2026-10-03. Estado: propuesta para implementar con OpenCode; no se ha creado la aplicación.

## 1. Objetivo y alcance

Construir una terminal responsive privada para Binance Spot con análisis técnico, anotaciones sobre el gráfico, análisis de escenarios mediante Ollama y bot automático en simulación desde el primer MVP integrado. Reducir infraestructura y código propio, no controles de seguridad ni pruebas.

La UI no es el motor del bot: cerrar el navegador no lo detiene. Suspender la máquina sí puede detenerlo; al recuperarse debe reconciliar estado y actualizar datos antes de abrir nuevas posiciones.

Entornos separados:
- PAPER: datos reales, saldo y ejecuciones simulados propios. Entorno predeterminado.
- EXCHANGE_DEMO: integración oficial demo/testnet con credenciales y URLs independientes; valida órdenes y protocolo, no garantiza fidelidad de resultados económicos.
- LIVE: fuera del alcance inicial. No incluir un interruptor funcional que habilite dinero real.

Spot sin margin ni derivados: largos y salida a moneda cotizada; vender requiere saldo disponible. BTC y ETH son activos iniciales propuestos, sujetos a disponibilidad de símbolos en la cuenta. No hay depósitos ni retiros automáticos.

## 2. Decisiones de arquitectura

| Área | Decisión |
|---|---|
| Frontend | React, TypeScript y Vite; CSS con tokens, sin framework adicional de estado al principio. |
| Gráfico | Librería especializada canvas, con soporte de zonas y paneles sincronizados. Candidata: Lightweight Charts; confirmar API vigente, licencia, atribución y extensibilidad mediante una prueba técnica antes de fijarla. |
| Backend | FastAPI y asyncio en un proceso/worker. Un único propietario del motor de trading. |
| Cálculos | Python/NumPy cuando sea útil; indicadores incrementales y funciones puras. |
| Persistencia | SQLite, transacciones, migraciones simples y escritura serializada. No guardar cada tick indefinidamente en tablas de decisiones. |
| Histórico bruto | Archivos segmentados y acotados; elegir formato tras medir volumen. SQLite conserva metadatos y referencias. |
| IA | Ollama; llamadas asíncronas con timeout, concurrencia limitada y validación Pydantic. |
| Transporte UI | HTTP para comandos/consultas; un WebSocket multiplexado para eventos. |
| Operación | Entorno Python y npm local. Contenedor opcional, no obligatorio. |

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

## 4. Backend

### Mercado
- Una conexión compartida a Binance; historial por HTTP y eventos en vivo por WebSocket.
- Normalizar símbolo, timestamps UTC, identificador de operación y procedencia.
- Detectar duplicados, huecos, eventos fuera de orden, cierre de barra y antigüedad.
- Reconexion con backoff y recuperación de huecos; datos incompletos bloquean entradas.
- Para barras por ticks, definir qué evento cuenta como tick. No sustituir operaciones individuales por operaciones agregadas sin justificar el cambio.
- Separar barras cerradas de barra en formación y declarar cuál utiliza cada regla.
- Mantener buffers acotados y cargar histórico adicional bajo demanda.

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
Tablas mínimas: configuración/versiones, sesiones de simulación, decisiones/analisis, órdenes/fills, revisiones de zonas y eventos de auditoría. Evitar duplicar balances sin mecanismo de reconciliación.

Endpoints orientativos: GET estado; GET histórico paginado; GET zonas; GET decisiones; GET cartera/órdenes; POST solicitar análisis; POST pausar/reanudar entradas; POST solicitar cierre y detención; GET/PATCH configuración validada.

Comandos con ID para prevenir duplicados. Cierre y detención es un proceso verificable: cancelar entradas pendientes, solicitar cierres, confirmar resultado y señalar fallos. El botón no garantiza un cierre instantáneo.

## 5. Frontend

### Terminal
- Barra superior: PAPER/EXCHANGE_DEMO, estado del motor, datos y modelo.
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

### Responsive y acceso
- Desktop de tres columnas; tablet reduce paneles; móvil usa Gráfico/Análisis/Operación y navegación inferior.
- Gráfico táctil, etiquetas legibles, controles de al menos 44 px y estados no dependientes solo del color.
- PWA para shell instalable; offline es solo consulta cacheada marcada como tal. Nunca encolar órdenes offline ni cachear secretos.
- Acceso local primero. Desde fuera: acceso privado/autenticado, HTTPS y controles de origen/sesión. No exponer Ollama ni credenciales al teléfono.

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

| Fase | Backend | Frontend | Puerta de salida |
|---|---|---|---|
| 0. Contratos | Resolver reglas conocidas/pendientes, esquemas, fixtures y entornos. | Congelar tokens, navegación y prueba técnica del gráfico. | Sin fórmulas inventadas; selección de gráfico justificada. |
| 1. Mercado + terminal | Historial, streaming, buffers, salud y construcción de barras. | Gráfico vivo, watchlist, indicadores de conexión y móvil. | Reconexión sin duplicados; datos atrasados visibles. |
| 2. Núcleo paper | Órdenes, fills, libro, riesgo, persistencia y recuperación; fixture TEST_ONLY. | Cartera, órdenes, pausa/cierre y actividad. | Ciclo automático completo y reinicio sin operaciones duplicadas. |
| 3. Método | Indicadores exactos disponibles, zonas, patrones y replay. | Capas, checklist y anotación manual. | Equivalencia numérica y concordancia con ejemplos; desconocidos bloqueados. |
| 4. Ollama | Snapshots, escenarios, validación, timeouts y registro. | Análisis asincrónico y visualización de escenarios. | Sin llamadas IA bloqueantes; respuesta obsoleta no provoca entrada. |
| 5. Integración demo | Adaptador Binance, filtros, reconciliación y fallos de órdenes. | Estado real de conexión y fills del entorno demo. | Timeout, parcial y reconexión probados; sin credenciales live. |
| 6. Validación | Walk-forward, comparación A/B y pruebas de estrés. | Resultados por versión y sesión; PWA y QA responsive. | MVP demo integrado, reproducible y auditable. |

Orden secuencial para OpenCode: cada fase se divide en microtasks pequeñas. Integrar backend/frontend por contrato, no construir todas las pantallas ficticias antes del motor. No hace falta desplegar públicamente.

## 8. Pruebas obligatorias

- Unitarias: indicadores, pivotes confirmados, zonas, redondeos, tamaños, contabilidad y estados.
- Simulación: comisiones, spread, gap, stop/objetivo intrabar, parciales y saldo insuficiente.
- Integración: desconexión, duplicados, timeout de orden, rate limits, reinicio y reconciliación.
- IA: JSON incorrecto, evidencia inventada, timeout, propuesta caducada e intento de superar riesgo.
- Seguridad: credenciales ausentes en logs/UI, comandos no autorizados y contenido externo sin autoridad.
- E2E: arranque paper → candidato → análisis → riesgo → orden → posición → salida; pausa no desactiva gestión.
- Evaluación: datos disponibles solo hasta el instante de decisión; ajustes separados del conjunto final de prueba. Backtests del LLM no bastan por posible memoria de datos históricos; incorporar evaluación prospectiva congelada.

## 9. Reglas de trabajo para OpenCode

1. Este documento es un plan, no autorización para operar con dinero real.
2. Inspeccionar repo/AGENTS.md y preservar cambios existentes antes de implementar.
3. Un microtask con alcance, archivos, contrato, pruebas y aceptación explícitos.
4. No inventar indicadores ni resolver ambigüedades del método silenciosamente.
5. Usar dependencias probadas y pocas capas; no imponer límite artificial de líneas que elimine pruebas.
6. Elegir versiones y APIs contra documentación oficial al implementar; no copiar endpoints antiguos.
7. Entregar resumen de cambios, pruebas ejecutadas y pendientes; detenerse ante permisos/bloqueos.
8. No autooptimizar ni cambiar estrategia/prompt/modelo de la sesión evaluada. Toda mejora crea una nueva versión.

## 10. Primer microtask sugerido

TRADING-FOUNDATION-001: crear esqueleto backend/frontend, contratos de estado/snapshot/anotaciones y fixture sintética; mostrar terminal responsive sin conexiones privadas ni órdenes externas. Añadir tests de esquema y estados. Lista visible de reglas de estrategia pendientes. Sin implementar aún fórmulas desconocidas.

## Referencias consultadas

- Binance Spot: https://github.com/binance/binance-spot-api-docs
- Ollama structured outputs: https://docs.ollama.com/capabilities/structured-outputs
- Limitaciones de backtesting: https://www.freqtrade.io/en/stable/backtesting/
- Lookahead: https://www.freqtrade.io/en/stable/lookahead-analysis/
- Memoria histórica en LLM: https://arxiv.org/abs/2504.14765

Las elecciones y presupuestos de este plan son propuestas de ingeniería, no afirmaciones de rentabilidad. Comprobar condiciones, APIs y disponibilidad de cuenta al implementar.
