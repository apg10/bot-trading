# Brief visual — Consola privada de trading con IA

Fecha: 2026-10-03. Complemento visual del plan de desarrollo. Diseñado para OpenCode y el MCP de Google Stitch disponible en el entorno del usuario. Este brief no presupone que el repositorio haya sido revisado ni que las funciones estén implementadas.

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

Velas verdes/rojas con alternativa accesible. FYL amarilla y bandas diferenciadas cuando estén definidas. Zonas como rangos translúcidos con límites y etiquetas; no tapar velas. No depender solo del color: añadir texto, iconos y patrones.

## 3. Navegación: tres vistas

### Terminal

Desktop de referencia: 1440×900 y 1920×1080.
- Barra superior de 56 px: marca discreta, Terminal/Laboratorio/Bot, entorno PAPER o EXCHANGE_DEMO, conexión y acceso a configuración.
- Barra del gráfico: símbolo, precio, bid/ask y spread cuando existan, temporalidad, capas y botón Analizar. Mostrar antigüedad del dato; diferenciar recepción local de latencia total.
- Watchlist izquierda opcional de 180–220 px; colapsable. Mostrar símbolos disponibles, precio y variación con período explícito. Sin feed de noticias inicial.
- Gráfico central flexible: ocupa la mayoría del ancho. Velas y volumen; panel de MACD BB sincronizado si está implementado. Herramientas mínimas: cursor, desplazamiento, zoom, ajuste de vista y capas.
- Panel derecho de 300–360 px: escenario de Qwen, hora y vigencia, evidencias, condiciones de entrada e invalidación. Una conclusión breve antes del detalle. No presentar confianza del LLM como probabilidad de ganar.
- Panel inferior de 200–260 px, plegable: Posiciones / Órdenes / Actividad. Tablas compactas y columnas relevantes. Arrastrar separadores dentro de límites; guardar preferencias.

Anotaciones: soporte/resistencia como áreas; propuesta como trazo discontinuo; orden pendiente con etiqueta propia; fill confirmado como marcador sólido. Entrada, stop y objetivos legibles. Al seleccionar una zona: rango, origen, fecha de confirmación y estado. Nunca convertir una propuesta en una ejecución visualmente.

Ficha de oportunidad: estrategia y versión, dirección permitida por el producto, entrada propuesta, invalidación, objetivos, tamaño/riesgo estimado y vigencia. Botón Ejecutar en demo únicamente si el backend lo permite. Al caducar, deshabilitar acción y ofrecer nuevo análisis. En Spot, no mostrar Abrir corto si no está soportado.

### Laboratorio

Orientado a comparar y entender estrategias, no a acumular tarjetas.
- Crear experimento: estrategia, par, barras, períodos de desarrollo/validación/prueba y costes asumidos.
- Estado de ejecución: pendiente/en curso/completado/fallido/cancelado; progreso solo si se puede medir.
- Tabla de candidatos: versión, beneficio neto, drawdown, operaciones, exposición y resultados por período. Mostrar datos insuficientes sin ocultarlos tras una puntuación.
- Detalle: curva de patrimonio, drawdown, operaciones sobre gráfico y supuestos del simulador. Resultados de desarrollo separados de evaluación reservada.
- Comparación de dos candidatos y referencia pertinente, con intervalos y costes iguales.
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

## 4. Móvil y tablet

Móvil de referencia: 390×844. Navegación inferior Terminal/Laboratorio/Bot. Terminal abre el gráfico; Análisis y Posiciones se acceden mediante pestañas o un panel inferior, evitando tres columnas comprimidas. Selector de símbolo sustituye watchlist. Laboratorio muestra lista resumida con detalle; no comprimir tablas completas.

Controles táctiles de al menos 44 px. Respetar safe areas, zoom y orientación horizontal. Sin interacción exclusiva por hover ni gestos que ejecuten órdenes accidentalmente. Estado DEMO y pausa accesibles. PWA opcional; offline permite consulta marcada, nunca órdenes en cola.

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

Preferencias persistidas: layout, capas, temporalidad y densidad. Densidad compacta/cómoda opcional después del diseño base. Atajos documentados para navegar, analizar y ajustar gráfico; ningún atajo de un toque envía órdenes.

Verificar contraste AA, focus visible, navegación por teclado, etiquetas de controles y lectura de estados. Comprobar números largos, traducciones, errores y 200% de zoom.

## 7. Cómo usar Google Stitch desde OpenCode

1. Inspeccionar repo y tomar capturas del frontend actual. Identificar funciones reales y pendientes.
2. Usar el MCP de Stitch disponible allí para generar dos direcciones de Terminal desktop: A, terminal densa y sobria; B, consola de supervisión con más espacio. Ambas comparten navegación y estados.
3. Comparar claridad del gráfico, lectura de datos, controles y facilidad de adaptación al frontend. Elegir una; no mezclar estilos sin criterio.
4. Desarrollar Terminal móvil y vistas Laboratorio/Bot con el sistema elegido.
5. Pedir vistas de error, desconexión, propuesta caducada y orden parcial.
6. Extraer tokens, componentes y referencias visuales. Adaptar al React existente; revisar dependencias y contratos antes de reutilizar código generado.
7. Implementar por pantallas y verificar contra referencias con datos reales o fixtures etiquetadas. Mantener historial de decisiones visuales.

Prompt base para Stitch:

Diseña una consola financiera privada de trading con IA local, en español. Estética de software financiero sofisticado: fondo oscuro, superficies discretas, números tabulares, bordes finos y color contenido. El gráfico de velas domina la pantalla, con zonas de soporte/resistencia y propuestas de entrada, stop y objetivos. Watchlist colapsable a la izquierda, análisis de escenarios de IA a la derecha y posiciones/órdenes abajo. Navegación Terminal, Laboratorio y Bot. Entorno DEMO siempre visible. Diferencia propuestas, órdenes pendientes y ejecuciones confirmadas mediante estilos y texto. No uses neón, glassmorphism, fondos animados ni promesas de rentabilidad. Mantén acciones simples y estados claros. Genera primero dos alternativas desktop 1440×900; después adapta la elegida a móvil 390×844. Usa datos ilustrativos etiquetados. Este diseño se adaptará a un frontend React/TypeScript existente.

## 8. Primera tarea de diseño para el cloud

Inspeccionar frontend y contratos. Entregar inventario de componentes reutilizables, mapa de vistas, funciones faltantes y cambios visuales mínimos. Usar Stitch para propuestas sin reescribir el proyecto. Después implementar primero el shell y la Terminal manteniendo gráfico y conexiones existentes. Laboratorio y Bot se integran conforme existan sus contratos.

Criterios de aceptación: precio y gráfico fluidos mientras Ollama trabaja; identificación inequívoca de demo y datos atrasados; propuesta distinta de fill; controles de pausa/cierre con semántica correcta; navegación por teclado; móvil sin desbordamientos; build y checks relevantes pasan; ningún botón simula una operación no implementada.

## 9. Alcance del documento

Este brief define diseño objetivo, no certifica funciones actuales. No modifica el producto financiero ni habilita dinero real. El plan original sigue siendo la base técnica; actualizar sus apartados obsoletos después de inspeccionar el repo. Qwen analiza y propone; el backend valida y ejecuta. No es una interfaz de chat con un gráfico añadido: la conversación y los logs son secundarios frente al mercado, la investigación y la supervisión.
