# ACT-T-001 — Inventario Visual y Mapa de Vistas (READ-ONLY)

**Estado:** PENDIENTE (este informe es el entregable; no cambia estado de la tarea).
**HEAD baseline:** `master` / `819ef896d1ed04f79a0c5dbd749fe6dceb5abdd8`.
**Estado del repo antes/después:** 7 tracked modificados, 12 untracked — **sin cambios durante este task**.

---

## A. Matriz Vista/Panel → Componente Real → Estado/Datos → Contratos/Capacidades → Implementación

### A.1 Terminal (vista actual)

| Panel / Zona | Componente real (ruta) | Estado / Datos consumidos | Contrato / Capacidad requerida | Estatus de implementación |
|---|---|---|---|---|
| **Barra superior** (entorno, estado conexión, versión) | `HeaderBar` inline en `App.tsx:10-33` | Muestra badge "Sin ejecución", status-dot (connected/disconnected/reconnecting), label de motor, versión v0.1.0 | GET /api/capabilities → `environment`, `execution_available`, `paper_simulation_available` | **EXISTENTE** (datos hardcodeados: "Bot: no implementado", badge paper fijo). No consulta `/api/capabilities`. |
| **Watchlist** (lista de símbolos) | `WatchlistPanel` inline en `App.tsx:37-53` | Array fijo `['BTC/USDT', 'ETH/USDT']`; muestra "Sin datos" para cada símbolo | Ninguno; sin API de watchlist, sin selección de símbolo | **PLACEHOLDER** (datos estáticos, sin interactividad). |
| **Gráfico de velas** | `ChartPanel` en `frontend/src/components/ChartPanel.tsx` | Recibe `candles` de `useMarketData('BTC/USDT')`; Lightweight Charts con OHLCV; overlay de conexión y fuente | Hook `useMarketData` (HTTP histórico + WS streaming); tipos `CandleData`, `MarketDataSource` | **IMPLEMENTADO** (gráfico funcional con datos del hook). Sin indicadores overlay (Keltner/MACD se muestran en AnalysisPanel, no en el gráfico). |
| **Panel de análisis técnico** | `AnalysisPanel` en `frontend/src/components/AnalysisPanel.tsx` | Recibe `candles`, `symbol`, `dataSource`, `interval` del hook; hace POST a `/api/analysis` con velas; muestra Keltner, MACD cruce y el output FYL experimental actual (pivotes/zonas/anotaciones) | POST /api/analysis; contrato `AnalysisResponse`; tipos `KeltnerPoint`, `MACDPoint`, `PivotPoint`, `ConsolidationZone` | **IMPLEMENTADO** (componente + hook `useAnalysis` con request tracker). FYL/ACT-S-007 sigue PAUSADO: sin equivalencia con FYL original ni uso operativo. |
| **Barra inferior** (posiciones/órdenes/P&L) | Footer inline en `App.tsx:88-95` | Texto fijo: "Posiciones: no consultadas", "Órdenes: no consultadas", "P&L: no disponible" | GET /api/portfolio, GET /api/orders (no existen) | **PLACEHOLDER** (texto estático). El componente `BottomBar.tsx` existe pero NO está montado por App. |
| **Navegación entre vistas** | No implementado | App.tsx es un único componente sin routing | ACT-T-002 (selección de vista) | **NO DISPONIBLE** (sin router, sin vistas Laboratorio/Bot). |

### A.2 Laboratorio (vista objetivo)

| Panel / Zona | Componente real | Estado | Contrato / Capacidad requerida | Estatus |
|---|---|---|---|---|
| Crear experimento | No existe | — | ACT-L-001 (replay), ACT-L-002 (experimentos) | **NO DISPONIBLE** |
| Tabla de candidatos A/B/C | No existe | — | ACT-L-002, contratos `ExperimentDefinition`, `RunAttempt` | **NO DISPONIBLE** |
| Detalle: curva patrimonio/drawdown | No existe | — | ACT-L-002, métricas normalizadas | **NO DISPONIBLE** |
| Inspector de operaciones vinculado al gráfico | No existe | — | ACT-L-003 | **NO DISPONIBLE** |

### A.3 Bot (vista objetivo)

| Panel / Zona | Componente real | Estado | Contrato / Capacidad requerida | Estatus |
|---|---|---|---|---|
| Estado del bot (observando/analizando/operando) | No existe | — | ACT-B-001, EngineInfo, contratos de estado | **NO DISPONIBLE** |
| Línea de actividad (oportunidad→análisis→orden→ejecución→salida) | No existe | — | ACT-B-001, DecisionRecord | **NO DISPONIBLE** |
| Pausar/Cerrar/Detener | No existe | — | ACT-P-007, comandos de control | **NO DISPONIBLE** |

### A.4 Componentes existentes pero NO montados por App

| Componente | Ruta | Observación |
|---|---|---|
| `AIAnalysisPanel` | `frontend/src/components/AIAnalysisPanel.tsx` | Existe (263 líneas); hook `useAIAnalysis` hace POST a `/api/bot/ai/analyze` (endpoint Ollama, distinto de AnalysisPanel que usa POST /api/analysis). Muestra Ollama status y markdown renderer. **NO está importado ni montado por App.tsx.** |
| `BottomBar` (con tabs) | `frontend/src/components/BottomBar.tsx` | Existe (105 líneas); props: `positions`, `orders`, `activityLog`; tabs de Posiciones/Órdenes/Actividad. **NO está importado ni montado por App.tsx.** El footer en App.tsx es código inline diferente con texto fijo. |

### A.5 Capacidades backend (GET /api/capabilities)

| Campo | Valor actual (hardcoded en CapabilitiesResponse) | Observación |
|---|---|---|
| `schema_version` | `"backend_capabilities.v1"` | Contrato versionado |
| `environment` | `app_config.env` (paper/exchange_demo) | Dinámico desde config |
| `runtime_health_checked` | `False` siempre | Endpoint no diagnostica |
| `market_data_supported` | `True` | HTTP + WS implementados |
| `technical_analysis_supported` | `True` | POST /api/analysis; muestra Keltner, MACD cruce convencional, FYL pivotes/zonas/anotaciones (output experimental actual) |
| `ai_scenarios_supported` | `True` | Componente existe pero no montado |
| `original_method_supported` | `False` | ACT-E-001 bloqueado |
| `execution_available` | `False` | Sin ejecución |
| `portfolio_available` | `False` | Sin cartera |
| `paper_simulation_available` | `False` | Sin simulador PAPER |
| `exchange_demo_execution_available` | `False` | Sin demo |
| `live_execution_available` | `False` | Sin live |

---

## B. Terminal actualmente existente vs. Vistas objetivo

### B.1 Terminal — estado actual (implementado)

- **Shell:** Flex column con header fijo (40px), main-body flex row, footer fijo (36px).
- **Layout:** 3 columnas — Watchlist (200px), ChartPanel (flex:1), AnalysisPanel (320px). Responsive a móvil en <768px (columna única).
- **Datos de mercado:** `useMarketData('BTC/USDT')` → HTTP histórico + WebSocket streaming. Fuente puede ser `TEST_ONLY`, `market_engine` o `unknown`.
- **Gráfico:** Lightweight Charts con velas OHLCV, colores up/down, resize observer, actualización por delta/rebuild.
- **Análisis técnico:** POST /api/analysis con velas; respuesta Keltner(20/14/2), MACD cruce y output FYL experimental actual (pivotes/zonas/anotaciones; ACT-S-007 PAUSADO, sin equivalencia ni uso operativo). Request tracker descarta respuestas obsoletas. Mínimo 50 velas para activar.
- **Watchlist:** Símbolos fijos BTC/USDT y ETH/USDT sin datos dinámicos ni selección.
- **Barra inferior:** Texto fijo "no consultado"/"no disponible". BottomBar.tsx existe pero no montado.
- **Navegación:** No existe. Un solo componente App.

### B.2 Laboratorio — estado objetivo (NO implementado)

- Diseño descrito en `trading-console-visual-brief.md:58-66`.
- Depende de ACT-L-001 (replay), ACT-L-002 (experimentos/resultados), ACT-L-003 (vista inspector).
- Sin componentes, sin rutas, sin datos.

### B.3 Bot — estado objetivo (NO implementado)

- Diseño descrito en `trading-console-visual-brief.md:68-78`.
- Depende de ACT-B-001, ACT-P-007/008.
- Sin componentes, sin rutas, sin datos.

---

## C. Preferencias de vista — persistencia actual

**Resultado de la inspección:** No se encuentra **ninguna** persistencia de preferencias en el frontend.

| Aspecto | Resultado | Evidencia |
|---|---|---|
| `localStorage` | NO encontrado | grep en `frontend/src/`: 0 resultados |
| `sessionStorage` | NO encontrado | grep en `frontend/src/`: 0 resultados |
| Cookies | NO encontrado | grep en `frontend.src/`: 0 resultados |
| Persistencia de layout/paneles | NO implementado | App.tsx no guarda/restaura estado de vista |
| Persistencia de símbolo seleccionado | NO implementado | Símbolo hardcodeado `'BTC/USDT'` en App.tsx:59 |
| Persistencia de intervalo/temporalidad | NO implementado | Intervalo viene del hook, no se configura ni persiste |
| Persistencia de paneles colapsados | NO implementado | No hay estado de colapso en componentes |
| Backend: preferencias | PENDIENTE ACT-P-001 | Store/sesión existe (untracked), pero preferencias UI no mapeadas |

**Conclusión:** Las preferencias de vista **no existen hoy**. El diseño objetivo (brief §6) las planea para cuando ACT-P-001 esté disponible. No se puede inventar un esquema de persistencia en este task.

---

## D. Relación paneles contextuales ↔ capacidades actuales

| Panel contextual | Capacidad backend correspondiente | Estado actual del contrato |
|---|---|---|
| Watchlist → selección de símbolo | GET /api/candles (histórico), WS candles channel | **IMPLEMENTADO** para BTC/USDT; watchlist sin datos dinámicos |
| Gráfico → velas OHLCV | Motor de mercado (Binance Spot); candle_store frontend | **IMPLEMENTADO** (datos reales o TEST_ONLY según motor) |
| Análisis técnico → indicadores | POST /api/analysis; PreparedMarketAnalysis | **IMPLEMENTADO** (Keltner, MACD, FYL pivotes/zonas/anotaciones). La UI muestra pivotes/zonas/anotaciones del output FYL experimental actual; ACT-S-007 está PAUSADO: no implica equivalencia con FYL original, aprobación del método ni uso operativo. No reabrir fórmula ni reglas FYL. |
| Panel inferior → posiciones | GET /api/portfolio (no existe endpoint) | **NO DISPONIBLE** — texto fijo "no consultadas" |
| Panel inferior → órdenes | GET /api/orders (no existe endpoint) | **NO DISPONIBLE** — texto fijo "no consultadas" |
| Panel inferior → P&L | Sin cálculo de P&L en frontend ni backend | **NO DISPONIBLE** — texto fijo "no disponible" |
| Selección de vista | No hay router ni navegación | **NO DISPONIBLE** — App.tsx es un solo componente |
| AI Analysis (Ollama) | POST /api/bot/ai/analyze | **COMPONENTE EXISTENTE PERO NO MONTADO** |

---

## E. Capturas / Artefactos visuales existentes en el repositorio

**Resultado de la inspección:** No hay capturas de pantalla, mockups visuales ni artefactos gráficos dentro del repositorio.

| Búsqueda | Resultado |
|---|---|
| `*.{png,jpg,jpeg,gif,svg,webp}` | 0 archivos encontrados |
| `*screenshot*` | 0 archivos encontrados |
| `*capture*` | 0 archivos encontrados |
| `*visual*` | Solo `trading-console-visual-brief.md` (documento textual) |
| `*artifact*` | 0 archivos encontrados |

**Conclusión:** No existen capturas visuales en el repo. El único artefacto visual es el documento textual `trading-console-visual-brief.md` que describe el diseño objetivo sin incluir mockups ni imágenes.

---

## F. Gaps concretos dejados por ACT-T-001 (para diseño/decisión)

### F.1 Navegación y vistas

| Gap | Descripción | Decisión requerida |
|---|---|---|
| G1 | No existe router ni mecanismo de navegación entre vistas | Elegir enfoque: hash routing, estado en URL, o Stitch para generar shell con navegación |
| G2 | Laboratorio y Bot son vistas objetivo sin implementación | Confirmar que ACT-T-002 es el siguiente task para abordar esto |
| G3 | Terminal actual no distingue claramente "Terminal de desarrollo" de "Terminal objetivo" | Decidir si se reutiliza App.tsx como base o se genera nueva shell con Stitch |

### F.2 Paneles contextuales

| Gap | Descripción | Decisión requerida |
|---|---|---|
| G4 | BottomBar.tsx existe pero no montado; footer en App.tsx es texto fijo | Decidir si se integra BottomBar.tsx con datos reales o se mantiene placeholder |
| G5 | AIAnalysisPanel.tsx existe pero no montado | Decidir si se incluye en la Terminal objetivo o se deja para ampliación posterior |
| G6 | Watchlist sin datos dinámicos ni selección de símbolo funcional | Definir si la watchlist muestra datos reales del motor o permanece como placeholder |

### F.3 Persistencia y preferencias

| Gap | Descripción | Decisión requerida |
|---|---|---|
| G7 | Cero persistencia de preferencias (localStorage, sessionStorage, cookies) | Esperar ACT-P-001 para persistencia backend; frontend sin persistencia hoy |
| G8 | Símbolo hardcodeado 'BTC/USDT' en App.tsx:59 | Decidir si se hace configurable antes de ACT-T-002 |

### F.4 Datos y estados

| Gap | Descripción | Decisión requerida |
|---|---|---|
| G9 | GET /api/capabilities no es consultado por el frontend | Decidir si App.tsx debe consumir este endpoint para mostrar estado real del backend |
| G10 | Sin endpoints de portfolio/órdenes/P&L implementados | Confirmar que los paneles inferiores permanecen como "no disponible" hasta ACT-P-008 |
| G11 | Análisis técnico usa POST /api/analysis pero no hay POST /api/analysis/market (B4.2) integrado en el frontend | B4.2 existe en backend pero no se consume desde el frontend actual |

### F.5 Diseño visual

| Gap | Descripción | Decisión requerida |
|---|---|---|
| G12 | Tokens CSS actuales (`tokens.css`) difieren del diseño objetivo del brief (colores, tipografía, radios) | Confirmar si se actualizan tokens antes de ACT-T-002 o se mantiene el diseño actual |
| G13 | No hay sistema de diseño unificado (Stitch design system no creado) | Decidir si se crea design system en Stitch para ACT-T-002 |

---

## G. Resumen ejecutivo

**Lo que existe hoy:**
- Una única Terminal de desarrollo con 3 paneles: Watchlist (placeholder), Gráfico (Lightweight Charts funcional), Análisis técnico (Keltner/MACD y output FYL experimental actual vía POST /api/analysis; ACT-S-007 PAUSADO, sin equivalencia ni uso operativo).
- Barra superior con estado hardcodeado "Sin ejecución".
- Barra inferior con texto fijo "no consultado"/"no disponible".
- 4 componentes en `frontend/src/components/`: AnalysisPanel, ChartPanel, AIAnalysisPanel (no montado), BottomBar (no montado).
- Tokens CSS y layout responsive implementados.
- Backend: GET /api/capabilities informativo; POST /api/analysis con velas (Keltner, MACD cruce convencional, FYL pivotes/zonas/anotaciones del output experimental actual); contratos de estado completos.

**Lo que NO existe hoy:**
- Navegación entre vistas (Laboratorio, Bot).
- Persistencia de preferencias de vista.
- Datos reales en watchlist, posiciones, órdenes y P&L.
- Integración de AIAnalysisPanel en la UI.
- Capturas visuales o mockups en el repositorio.
- Design system de Stitch.

**Dependencias para ACT-T-002:** ACT-T-001 (este task), autorización de diseño, definición de shell/navegación, y preferencias según ACT-P-001 cuando esté disponible.

---

*Fin del informe ACT-T-001. Sin cambios al repositorio. Sin ejecución de tests, build, navegador ni servicios externos.*
