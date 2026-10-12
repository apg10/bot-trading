# Brief visual — Consola privada de trading con IA

Fecha de actualización: 2026-10-07. Complemento visual del plan de desarrollo, reconciliado documentalmente con el contexto verificado. Diseñado para OpenCode y el MCP de Google Stitch disponible en el entorno del usuario. Esta actualización no es una nueva auditoría de código, pruebas ni QA visual.

El [plan de acción raíz](trading-bot-action-plan.md) es la única autoridad de estados, aceptaciones, dependencias y siguiente task. El plan de desarrollo conserva arquitectura y alcance; este brief define diseño objetivo. Los documentos de `trading-plans-2026-10-07/` son propuestas fechadas sin autoridad operativa y permanecen intactos.

Contexto actual: una única Terminal de desarrollo, motor público Binance Spot integrado, N02 estructurado con integración propia pendiente y B2/B3/B4.1/B4.2 aceptados según raíz. PAPER, ejecución, cartera, persistencia y replay no están implementados. La navegación descrita es objetivo, no disponibilidad actual.

## 1. Dirección del producto

Crear una web app financiera sofisticada, personal y rápida. Debe transmitir precisión mediante gráficos claros, composición disciplinada y estados verificables. El gráfico y la supervisión del bot son protagonistas. La investigación de estrategias tiene su propio laboratorio. La complejidad aparece bajo demanda.

Conservar el frontend existente: inspeccionar componentes, rutas, estilos, biblioteca del gráfico y contratos antes de diseñar cambios. Reutilizar lo que funciona. No reemplazar frameworks, motor de gráficos ni estructura por el código exportado de Stitch sin una justificación concreta.

Nombre provisional en los mockups: PRIVATE TERMINAL. No implica nombre definitivo ni marca comercial. Interfaz en español; símbolos, abreviaturas financieras y timestamps mantienen convenciones explícitas.

## 2. Lenguaje visual

Estética: terminal financiera contemporánea, sobria y de alta calidad. Superficies oscuras ligeramente diferenciadas, bordes finos, datos alineados y color contenido. Evitar estética de casino, grandes tarjetas de ganancias, neón, glassmorphism, gradientes decorativos, fondos animados y mensajes de rentabilidad garantizada.

Tokens iniciales, sujetos a verificación de contraste:

| Token | Valor | Uso |
|---|---|---|
| Fondo | #0B1018 | Lienzo general |
| Superficie | #121A26 | Paneles |
| Superficie elevada | #1A2535 | Menús y selección |
| Borde | #2A394D | Separadores |
| Texto principal | #E8EEF7 | Datos y títulos |
| Texto secundario | #A3B1C6 | Etiquetas |
| Acento | #67D5E2 | Selección y herramientas |
| Positivo | #63D6A4 | Compra y resultado positivo |
| Negativo | #FF8792 | Venta, pérdida y stop |
| Advertencia | #F2C56D | Datos atrasados y pendientes |
| IA | #B6A3F5 | Análisis y propuestas |

Tipografía: reutilizar la fuente actual si encaja; candidata Inter para interfaz y una mono para IDs/logs. Números tabulares en precios, balances y tablas. Texto habitual 14–16 px; secundario nunca diminuto para conseguir densidad. Espaciado base 4/8 px. Radios 6–10 px; separadores antes que sombras.

Velas verdes/rojas con alternativa accesible. MACD convencional identificado como tal; FYL experimental y bandas según capacidad/causalidad. MACD BB y T1/V1/V2 siguen bloqueados por especificación: no inventar fórmulas para completar un diseño. Zonas como rangos translúcidos con límites y etiquetas; no tapar velas. No depender solo del color: añadir texto, iconos y patrones.

## 3. Navegación: tres vistas previstas

### Terminal

Desktop de referencia: 1440×900 y 1920×1080.
- Barra superior de 56 px: marca discreta y navegación prevista Terminal/Laboratorio/Bot. PAPER o EXCHANGE_DEMO solo si el backend confirma entorno y capacidad; mientras no exista ejecución, desarrollo y «Sin ejecución» explícitos. Separar conexión, fuente/frescura y capacidad.
- Barra del gráfico: símbolo, precio, bid/ask y spread cuando existan, temporalidad, capas y botón Analizar. Mostrar antigüedad del dato; diferenciar recepción local de latencia total.
- Watchlist izquierda opcional de 180–220 px; colapsable. Mostrar símbolos disponibles, precio y variación con período explícito. Sin feed de noticias inicial.
- Gráfico central flexible: ocupa la mayoría del ancho. Velas y volumen según soporte; panel de MACD convencional identificado, MACD BB solo tras especificación e implementación aprobadas. Herramientas mínimas: cursor, desplazamiento, zoom, ajuste de vista y capas.
- Panel derecho de 300–360 px: escenario de Qwen, hora y vigencia, evidencias, condiciones de entrada e invalidación. Una conclusión breve antes del detalle. No presentar confianza del LLM como probabilidad de ganar.
- Panel inferior de 200–260 px, plegable: Posiciones / Órdenes / Actividad según contratos disponibles; ausencia explícita mientras no exista cartera/ejecución. Tablas compactas y columnas relevantes. Separadores y preferencias persistidas según capacidad, sin aparentar almacenamiento existente.

Anotaciones: soporte/resistencia como áreas; propuesta como trazo discontinuo; orden pendiente con etiqueta propia; fill confirmado como marcador sólido. Entrada, stop y objetivos legibles. Al seleccionar una zona: rango, origen, fecha de confirmación y estado. Nunca convertir una propuesta en una ejecución visualmente.

Ficha de oportunidad: estrategia y versión, dirección Spot long/salida, entrada propuesta, invalidación, objetivos, tamaño/riesgo estimado y vigencia según datos disponibles. Botón Ejecutar en demo únicamente con capacidad confirmada; hoy no existe ejecución. Al caducar, deshabilitar acción y ofrecer nuevo análisis. Sin cortos, margin, derivados ni interruptor LIVE funcional.

### Laboratorio

Orientado a comparar y entender estrategias, no a acumular tarjetas.
- Crear experimento: estrategia, par, barras, períodos de desarrollo/validación/prueba y costes asumidos.
- Estado de ejecución: pendiente/en curso/completado/fallido/cancelado; progreso solo si se puede medir.
- Tabla de candidatos: versión, beneficio neto, drawdown, operaciones, exposición y resultados por período. Mostrar datos insuficientes sin ocultarlos tras una puntuación.
- Detalle: curva de patrimonio, drawdown, operaciones sobre gráfico y supuestos del simulador. Resultados de desarrollo separados de evaluación reservada.
- Comparación de mínimo tres variantes predefinidas A/B/C o IDs estables por hipótesis y referencia pertinente, con datos, períodos, costes y presupuesto de riesgo comparables. Conservar todos los intentos, incluidos fallidos/descartados; no implica tres órdenes simultáneas ni define T1/V1/V2. Comparación por pares opcional.
- Botón Probar en demo crea una sesión identificable. Mostrar qué validaciones faltan antes de promover una estrategia.
- Si existe promoción automática, mostrar criterios, resultado de cada criterio y registro de promociones. No implementar promoción automática solo para completar un mockup.

### Bot

Supervisión del ciclo autónomo:
- Estado confirmado: observando / analizando / operando / entradas pausadas / recuperando / error.
- Estrategia y versión activas, modelo y versión del prompt, sesión de demo.
- Modo Manual / Con aprobación / Automático, solo donde los contratos actuales lo soporten. Cambio explícito y confirmado por backend.
- Patrimonio, efectivo, exposición y resultado realizado/no realizado con moneda y período.
- Línea de actividad: oportunidad → análisis → validación → orden → ejecución → salida. Eventos vinculados a IDs, sin mostrar razonamiento interno del modelo.
- Pausar nuevas entradas: mantiene gestión de posiciones. Cerrar posiciones y detener: acción separada con confirmación y estados parciales/fallidos visibles.
- Informe diario: resumen de operaciones, costes, fallos y propuestas de investigación. Distinguir explicación de IA de métricas calculadas.
- Posiciones manuales y del bot con propietario visible. Operaciones externas de Quantfury son registros manuales si no hay integración oficial; nunca mostrarlas como sincronizadas sin evidencia.

### Patrones contextuales inspirados en Fincept

Referencia de organización, sin importar código, frameworks, brokers o plataformas. Conservar React y Lightweight Charts y las tres vistas previstas; actualmente solo existe la Terminal de desarrollo.
- ACT-C-001: configuración basada en contratos/catálogo versionado común con backend e IA, con tipos, límites, unidades, calentamiento y causalidad. El catálogo dinámico completo es ampliación, no condición del backend inmediato.
- ACT-L-002: flujo configurar → ejecutar → comparar con resultados normalizados y unidades/supuestos; beneficio neto, costes, drawdown, operaciones y exposición. ACT-Q-001 separa desarrollo/validación/test reservado; métricas ampliadas y mapas mensuales solo con datos suficientes.
- ACT-L-003: tabla A/B/C y todos los intentos, inspector vinculado al gráfico con trigger → riesgo → orden → fills → salida, IDs/versiones y señales rechazadas con motivo. No alinear trades distintos como si fueran iguales.
- ACT-T-001…005/ACT-L-003/ACT-B-001: paneles contextuales plegables/redimensionables y selección compartida de activo/experimento/trade según capacidad; ACT-P-001 conserva versiones y preferencias persistidas. Cambiar vista no reconfigura el motor.
- ACT-A-002 aporta contexto verificable, ACT-A-003 asesoría y ACT-A-001 auditoría. Acciones Qwen de investigación requieren herramientas disponibles: evidencias, modelo, fecha/vigencia y propuestas de cambio, sin razonamiento interno ni activación automática.
- ACT-A-004 es la ampliación posterior de herramientas estructuradas research Qwen, con dependencias ACT-C-001, ACT-A-001 y ACT-L-002. Sin cambios de riesgo, órdenes o estrategia activa; no bloquea B4.2.
- Calidad/cobertura/fuente, costes asumidos, sintéticos, métricas indefinidas y límites de paginación visibles. No sustituir ausencia por cero ni fabricar progreso.

Mínimo funcional futuro: replay ACT-L-001, experimentos/resultados ACT-L-002 e inspector ACT-L-003 según contratos. Catálogos completos, research IA y jobs avanzados son ampliaciones; esta reconciliación no añade IDs duplicados, puertas H4 ni nuevas dependencias a tasks existentes. Los jobs solo muestran estados/errores/cancelación reales cuando haya soporte. Se conserva H0 → H1 → H2 → H3/H4 → H5.

## 4. Móvil y tablet

Móvil de referencia: 390×844. Navegación inferior Terminal/Laboratorio/Bot. Terminal abre el gráfico; Análisis y Posiciones se acceden mediante pestañas o un panel inferior, evitando tres columnas comprimidas. Selector de símbolo sustituye watchlist. Laboratorio muestra lista resumida con detalle; no comprimir tablas completas.

Controles táctiles de al menos 44 px. Respetar safe areas, zoom y orientación horizontal. Sin interacción exclusiva por hover ni gestos que ejecuten órdenes accidentalmente. Entorno/capacidad reales y pausa accesibles cuando exista soporte. PWA opcional; offline permite consulta marcada, nunca órdenes en cola.

## 5. Estados que el diseño debe resolver

Diseñar datos cargados, carga inicial, vacío, error y desconexión. Además:
- Datos atrasados: timestamp visible y nuevas entradas bloqueadas si corresponde.
- Qwen ocupado/timeout/no disponible: el mercado y las posiciones siguen visibles; no inventar análisis.
- Sin oportunidades: explicar la condición pendiente, sin forzar una señal.
- Orden enviada sin confirmación: estado desconocido/en reconciliación; no volver a habilitar envío por un timeout.
- Ejecución parcial: cantidad ejecutada y restante explícitas.
- Reinicio del backend: recuperando hasta confirmar estado.
- Estrategia sin evaluar: no presentarla como la mejor ni como validada.
- Sesión demo nueva: historial anterior conservado.

En mockups se permiten fixtures inequívocamente etiquetadas Datos ilustrativos. En la app, ninguna métrica, precio, orden o progreso ficticios para aparentar funcionalidad.

## 6. Fluidez y acabado

Actualizar series del gráfico por delta; evitar renderizar toda la app por tick. Agrupar refrescos de etiquetas. Virtualizar listas largas cuando haga falta. Mantener capacidad del backend independiente del render. Transiciones breves de 120–180 ms en paneles, sin animación decorativa continua; respetar reduced motion.

Preferencias objetivo: layout/paneles, capas, temporalidad y densidad, persistidas según capacidad con ACT-P-001 y los responsables UI existentes. Densidad compacta/cómoda opcional después del diseño base. La selección contextual no cambia el mercado del motor sin comando explícito. Atajos documentados para navegar, analizar y ajustar gráfico; ningún atajo de un toque envía órdenes.

Verificar contraste AA, focus visible, navegación por teclado, etiquetas de controles y lectura de estados. Comprobar números largos, traducciones, errores y 200% de zoom.

## 7. Cómo usar Google Stitch desde OpenCode

Procedimiento futuro condicionado a autorización de diseño y dependencias del plan de acción; no selecciona la siguiente tarea ni autoriza servicios, generación o implementación desde esta reconciliación.

1. Inspeccionar repo y tomar capturas del frontend actual. Identificar funciones reales y pendientes.
2. Usar el MCP de Stitch disponible allí para generar dos direcciones de Terminal desktop: A, terminal densa y sobria; B, consola de supervisión con más espacio. Ambas comparten navegación y estados.
3. Comparar claridad del gráfico, lectura de datos, controles y facilidad de adaptación al frontend. Elegir una; no mezclar estilos sin criterio.
4. Desarrollar Terminal móvil y vistas Laboratorio/Bot con el sistema elegido.
5. Pedir vistas de error, desconexión, propuesta caducada y orden parcial.
6. Extraer tokens, componentes y referencias visuales. Adaptar al React existente; revisar dependencias y contratos antes de reutilizar código generado.
7. Implementar por pantallas y verificar contra referencias con datos reales o fixtures etiquetadas. Mantener historial de decisiones visuales.

Prompt base para Stitch:

Diseña una consola financiera privada Binance Spot con IA local, en español. Estética de software financiero sofisticado: fondo oscuro, superficies discretas, números tabulares, bordes finos y color contenido. El gráfico de velas domina la pantalla, con zonas y propuestas de entrada, stop y objetivos según capacidades definidas. Watchlist colapsable a la izquierda, escenarios IA a la derecha y posiciones/órdenes abajo cuando existan contratos. Tres vistas previstas: Terminal, Laboratorio y Bot; actualmente solo Terminal de desarrollo. Entorno y capacidad reales visibles, con «Sin ejecución» mientras corresponda; sin LIVE ni cortos. Diferencia propuestas, órdenes pendientes y fills mediante estilos y texto. No inventes FYL/MACD BB ni métricas operativas. Evita neón, glassmorphism, animación decorativa y promesas de rentabilidad. Genera dos alternativas desktop 1440×900; adapta la elegida a móvil 390×844. Usa datos ilustrativos etiquetados. Adaptación al React/TypeScript existente, solo tras autorización de diseño.

## 8. Referencia de tarea de diseño futura (no activa)

Inspeccionar frontend y contratos. Entregar inventario de componentes reutilizables, mapa de vistas, funciones faltantes y cambios visuales mínimos. Usar Stitch para propuestas sin reescribir el proyecto. Después implementar primero el shell y la Terminal manteniendo gráfico y conexiones existentes. Laboratorio y Bot se integran conforme existan sus contratos.

Criterios objetivo, no aprobación actual: precio y gráfico fluidos mientras Ollama trabaja; identificación inequívoca del entorno/capacidad y datos atrasados; propuesta distinta de fill; pausa/cierre con semántica correcta cuando existan; navegación por teclado; móvil sin desbordamientos; build y checks relevantes autorizados pasan; ningún botón simula una operación no implementada. No se han ejecutado estos checks ni QA visual en esta actualización.

B4.2: BACKEND-MARKET-ANALYSIS-API-001 / B4.2, GROUP-BACKEND-001, RUN-MARKET-API-001-001, REV-001 — **COMPLETADO en su alcance** (aceptación manual delegada). Reutiliza `PreparedMarketAnalysis` y `await analyze(prepared.request)` para exponer API, sin nuevo cálculo ni tarea UI. Esta referencia es historial; no modifica su paquete, alcance o aceptación. El estado 7 tracked modificados + 10 untracked tras reconciliar no sustituye evidencia de contenido/propiedad.

B4.1 REV-001: REQUIRES_CHANGES, 14 tests reportados; REV-002: 16 tests y probe independiente offline aceptados conforme a raíz. Son registros previos, no pruebas ejecutadas aquí. ACT-AUTO-001 completo y ACT-AUTO-002 parcial/PAUSADO; las instrucciones anteriores de automatización son históricas/superseded, sin reactivación ni infraestructura nueva.

## 9. Alcance del documento

Este brief define diseño objetivo, no certifica funciones actuales ni nuevas aceptaciones. No modifica el producto financiero ni habilita dinero real. La raíz conserva la base técnica y el plan de acción manda estados, aceptaciones, dependencias y siguiente task; las propuestas fechadas no la sustituyen. Qwen analiza y propone; el backend valida y solo ejecutará cuando exista capacidad confirmada. La conversación y los logs son secundarios frente al mercado, la investigación y la supervisión.
