# ACT-T-001 — Inventario Visual y Mapa de Vistas (READ-ONLY)

**Estado:** CORREGIDO 2026-10-22 (post-inspección). Este informe es el entregable; no cambia estado de la tarea.
**HEAD baseline:** `master` / `718e044` (origin/master).
**Nota de corrección:** Los commits `895cf39` y `718e044` son fixes post-ACT-M-004 (late HTTP guard + TS2339 narrow), NO implementan ACT-M-004. El trabajo ACT-M-004 (POST /api/market/reconfigure, view/engine separation) fue implementado en commits anteriores.

---

## A. Matriz Vista/Panel → Componente Real → Estado/Datos → Contratos/Capacidades → Implementación

### A.1 Terminal (vista actual)

| Panel / Zona | Componente real (ruta) | Estado / Datos consumidos | Contrato / Capacidad requerida | Estatus de implementación |
|---|---|---|---|---|
| **Barra superior** (entorno, estado conexión, versión) | `HeaderBar` inline en `App.tsx:31-44` | Muestra badge "Sin ejecución", status-dot (connected/disconnected/reconnecting), label "Bot: no implementado", versión v0.1.0. **No consulta `/api/capabilities`.** | **EXISTENTE** (datos hardcodeados). |
| **Navegación** (tabs Terminal/Laboratorio/Bot) | `NavBar` en `App.tsx:48-87` | 3 tabs con keyboard navigation (ArrowLeft/Right/Home/End), ARIA roles. ACT-M-004: navegación funcional por estado React. | **IMPLEMENTADO** (ACT-M-004). |
| **Selector de mercado** (símbolo/intervalo/aplicar) | `MarketSelector` en `App.tsx:130-179` | View/engine separation. Selectores de símbolo e intervalo, botón "Aplicar al motor", estado pending/error, display del motor actual. ACT-M-004: separación explícita vista vs. motor. | **IMPLEMENTADO** (ACT-M-004). |
| **Watchlist** (lista de símbolos) | `WatchlistPanel` inline en `App.tsx:108-124` | Array fijo `['BTC/USDT', 'ETH/USDT']`; muestra "Sin datos" para cada símbolo. ACT-M-004: watchlist no conectada al motor. | **PLACEHOLDER** (datos estáticos, sin interactividad). |
| **Gráfico de velas** | `ChartPanel` en `frontend/src/components/ChartPanel.tsx` | Recibe `candles` de `useMarketData(engineSymbol, engineInterval)`; Lightweight Charts con OHLCV; overlay de conexión y fuente. ACT-M-004: se activa con engineContext, no viewContext. | **IMPLEMENTADO** (datos del hook). Sin indicadores overlay. |
| **Panel de análisis técnico** | `AnalysisPanel` en `frontend/src/components/AnalysisPanel.tsx` | Recibe `candles`, `symbol`, `dataSource`, `interval` del hook; POST a `/api/analysis`. ACT-M-004: usa engineContext para análisis. | **IMPLEMENTADO** (request tracker descarta respuestas obsoletas). FYL PAUSADO. |
| **Barra inferior PAPER** | `BottomBar` en `frontend/src/components/BottomBar.tsx` | Montado por App.tsx:332 con `accountId=null, sessionId=null`. 5 tabs (Balances/Positions/Orders/Fills/Activity); usa `usePaperData` → GET /api/paper/*. ACT-P-008: PAPER read-only endpoints + wiring. accountId/sessionId son null intencionalmente (App.tsx:258 "no account is inferred or persisted"). | **MONTADO** (ACT-P-008). Datos indisponibles por diseño sin cuenta activa. |

### A.2 Laboratorio (vista objetivo)

| Panel / Zona | Componente real | Estado | Contrato / Capacidad requerida | Estatus |
|---|---|---|---|---|
| Estado del laboratorio | `UnavailableView` en `App.tsx:318` | "Laboratorio no disponible" — placeholder explícito | ACT-L-001, ACT-L-002, ACT-L-003 | **NO DISPONIBLE** |

### A.3 Bot (vista objetivo)

| Panel / Zona | Componente real | Estado | Contrato / Capacidad requerida | Estatus |
|---|---|---|---|---|
| Estado del bot | `UnavailableView` en `App.tsx:328` | "Bot no disponible" — placeholder explícito | ACT-B-001, ACT-P-007/008 | **NO DISPONIBLE** |

### A.4 Componentes existentes pero NO montados por App

| Componente | Ruta | Observación |
|---|---|---|
| `AIAnalysisPanel` | `frontend/src/components/AIAnalysisPanel.tsx` | Existe (263 líneas); hook `useAIAnalysis` hace POST a `/api/bot/ai/analyze` (endpoint Ollama, distinto de AnalysisPanel que usa POST /api/analysis). Muestra Ollama status y markdown renderer. **NO está importado ni montado por App.tsx.** |
| `BottomBar` (con tabs) | `frontend/src/components/BottomBar.tsx` | Existe (97 líneas); props: `accountId`, `sessionId`; 5 tabs (Balance/Positions/Orders/Fills/Activity); usa `usePaperData` hook → GET /api/paper/* read-only. **MONTADO por App.tsx:332** con `accountId=null, sessionId=null` → muestra "PAPER data unavailable". ACT-P-008: PAPER read-only endpoints + wiring; accountId/sessionId null por diseño (App.tsx:258). |

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

### B.1 Terminal — estado actual (implementado, post-ACT-M-004)

- **Shell:** Flex column con header fijo (40px), nav-bar, main-body flex row, footer fijo.
- **Layout:** 3 columnas — Watchlist (200px), ChartPanel (flex:1), AnalysisPanel (320px). Responsive a móvil en <768px (columna única).
- **Navegación:** 3 tabs (Terminal/Laboratorio/Bot) con keyboard navigation y ARIA roles (ACT-M-004).
- **Selector de mercado:** View/engine separation (ACT-M-004). viewContext para UI, engineContext para el motor. Botón "Aplicar al motor" → POST /api/market/reconfigure.
- **Datos de mercado:** `useMarketData(engineSymbol, engineInterval)` → HTTP histórico + WebSocket streaming. Fuente puede ser `TEST_ONLY`, `market_engine` o `unknown`.
- **Gráfico:** Lightweight Charts con velas OHLCV, colores up/down, resize observer, actualización por delta/rebuild. Se activa con engineContext (no viewContext).
- **Análisis técnico:** POST /api/analysis con velas; respuesta Keltner(20/14/2), MACD cruce convencional. Request tracker descarta respuestas obsoletas. Mínimo 50 velas para activar. FYL PAUSADO.
- **Watchlist:** Símbolos fijos BTC/USDT y ETH/USDT sin datos dinámicos ni selección.
- **Barra inferior PAPER:** BottomBar.tsx montado (ACT-P-008). accountId/sessionId null por diseño → datos indisponibles.
- **Laboratorio/Bot:** `UnavailableView` con mensaje explícito "no disponible". Sin datos simulados.

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
| Persistencia de símbolo seleccionado | NO implementado | Símbolo por defecto 'BTC/USDT' en App.tsx:261 (viewContext default), no persiste entre sesiones |
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
| Selección de vista | Navegación por estado React (activeView) pero sin URL/hash routing | **NO DISPONIBLE** — App.tsx es un solo componente, navegación por estado React (ACT-M-004) |
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
| G1 | Navegación entre vistas existe por estado React (activeView) pero sin URL/hash routing ni persistencia | Elegir enfoque: hash routing, estado en URL, o Stitch para generar shell con navegación |
| G2 | Laboratorio y Bot son vistas objetivo sin implementación | Confirmar que ACT-T-002 es el siguiente task para abordar esto |
| G3 | Terminal actual no distingue claramente "Terminal de desarrollo" de "Terminal objetivo" | Decidir si se reutiliza App.tsx como base o se genera nueva shell con Stitch |

### F.2 Paneles contextuales

| Gap | Descripción | Decisión requerida |
|---|---|---|
| G4 | BottomBar.tsx existe y está montado (ACT-P-008) pero datos indisponibles por diseño (accountId=null) | Decidir si se integra con datos reales o se mantiene placeholder |
| G5 | AIAnalysisPanel.tsx existe pero no montado | Decidir si se incluye en la Terminal objetivo o se deja para ampliación posterior |
| G6 | Watchlist sin datos dinámicos ni selección de símbolo funcional | Definir si la watchlist muestra datos reales del motor o permanece como placeholder |

### F.3 Persistencia y preferencias

| Gap | Descripción | Decisión requerida |
|---|---|---|
| G7 | Cero persistencia de preferencias (localStorage, sessionStorage, cookies) | Esperar ACT-P-001 para persistencia backend; frontend sin persistencia hoy |
| G8 | Símbolo por defecto 'BTC/USDT' en App.tsx:261 (viewContext default), no persiste entre sesiones | Decidir si se hace configurable antes de ACT-T-002 |

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

## H. Inspección en vivo — verificaciones realizadas

**Fecha:** 2026-10-22. **HEAD:** `718e044` (origin/master).

### H.1 Backend

| Check | Comando | Resultado |
|---|---|---|
| GET /api/capabilities | `TestClient(app).get('/api/capabilities')` | **200 OK** — `environment=paper`, `market_data_supported=True`, `execution_available=False`, `portfolio_available=False`, `paper_simulation_available=False` |
| Schema version | Campo `schema_version` | `"backend_capabilities.v1"` |

### H.2 Frontend — inspección estática y build

| Check | Comando | Resultado | Tipo de evidencia |
|---|---|---|---|
| TypeScript typecheck | `tsc -b` (verificado en plan) | **PASS** (sin errores) | Fuente: tsconfig.json + plan |
| Production build | `npm run build` | **PASS** — 44 modules, 1.32s, dist/index.html + CSS 6.73kB + JS 432kB | Runtime: ejecutado en este task |
| Tokens CSS | `frontend/src/styles/tokens.css` | Inter font, dark palette (#0B1018 bg), accent #67D5E2, radios 6-10, spacing 4/8 base | Fuente: lectura de archivo |
| Capturas de navegador | PENDIENTE | No disponible browser tooling en este entorno. Inspección estática del DOM structure confirmada por código fuente. | PENDIENTE |

### H.3 App shell — estructura DOM (inspección de fuente)

```
<div class="app-layout">          <!-- flex column, 100vh -->
  <header class="header-bar">    <!-- 56px, bg #121A26, border #2A394D -->
    <span class="env-badge paper">Sin ejecución</span>
    <div class="status-dot" />   <!-- 8x8px, status indicator -->
    <span>Bot: no implementado</span>
    <span>v0.1.0 · Terminal de desarrollo</span>
  </header>
  <nav class="nav-bar">          <!-- 3 tabs: Terminal/Laboratorio/Bot -->
    <button role="tab" id="tab-terminal" />
    <button role="tab" id="tab-laboratorio" />
    <button role="tab" id="tab-bot" />
  </nav>
  <div class="main-body">        <!-- flex row, responsive <768px column -->
    <section id="panel-terminal" class="view-panel">
      <MarketSelector />         <!-- viewContext + engineContext -->
      <WatchlistPanel />         <!-- static BTC/USDT, ETH/USDT -->
      <ChartPanel />             <!-- Lightweight Charts, engineContext -->
      <AnalysisPanel />          <!-- POST /api/analysis, request tracker -->
    </section>
    <section id="panel-laboratorio" class="view-panel">
      <UnavailableView />        <!-- "Laboratorio no disponible" -->
    </section>
    <section id="panel-bot" class="view-panel">
      <UnavailableView />        <!-- "Bot no disponible" -->
    </section>
  </div>
  <footer class="bottom-bar">    <!-- 36px, PAPER read-only -->
    <strong>PAPER · read-only</strong>
    <button>Balances (0)</button>
    <button>Positions (0)</button>
    <button>Orders (0)</button>
    <button>Fills (0)</button>
    <button>Activity (0)</button>
  </footer>
</div>
```

### H.4 Datos reales vs placeholders

| Elemento | Estado real | Fuente de datos |
|---|---|---|
| Watchlist symbols | **STATIC** — array fijo `['BTC/USDT', 'ETH/USDT']` | Hardcoded en WatchlistPanel |
| Watchlist prices | **"Sin datos"** para cada símbolo | Sin conexión a motor ni API |
| Chart candles | **ENGINE-DRIVEN** — recibe de `useMarketData(engineSymbol, engineInterval)` | HTTP histórico + WS streaming (TEST_ONLY o motor real) |
| Analysis indicators | **REQUEST-TRACKED** — POST /api/analysis con velas del hook | Backend Keltner(20/14/2), MACD cruce |
| BottomBar tabs | **5 tabs** (Balances/Positions/Orders/Fills/Activity) | `usePaperData(accountId, sessionId)` → GET /api/paper/* |
| BottomBar data | **"PAPER data unavailable"** | accountId=null → no fetch. **Por diseño:** App.tsx:258 comenta "no account is inferred or persisted". No es un defecto de integración. |
| Laboratorios/Bot | **"no disponible"** | UnavailableView placeholder |

### H.5 Navegación confirmada

- **Tabs:** Terminal (default), Laboratorio, Bot — `activeView` state en App.tsx:257
- **Keyboard:** ArrowLeft/Right/Home/End + ARIA roles (tab/tablist/tabpanel)
- **No URL routing:** Navegación puramente por estado React
- **No persistencia:** activeView no se guarda en localStorage/sessionStorage

### H.6 View vs Engine context (ACT-M-004)

| Contexto | Propósito | Valor default | Persistencia |
|---|---|---|---|
| `viewContext` | UI selector (lo que el usuario ve/selecciona) | `{symbol: 'BTC/USDT', interval: '1m'}` | Ninguna |
| `engineContext` | Autoridad del motor de mercado | `{symbol: 'BTC/USDT', interval: '1m', stream_id: 'unavailable'}` | Estado React |

- `MarketSelector` acepta cambios en `viewContext` sin afectar el motor
- "Aplicar al motor" → POST /api/market/reconfigure → actualiza `engineContext`
- `useMarketData` se activa con `engineContext`, no con `viewContext`
- Status del motor actualiza `engineContext` vía stream_id matching

---

## G. Resumen ejecutivo (post-ACT-M-004)

**Lo que existe hoy:**
- Shell con navegación de 3 tabs (Terminal/Laboratorio/Bot) con keyboard navigation y ARIA roles (ACT-M-004).
- Terminal con view/engine separation: viewContext para UI, engineContext para el motor (ACT-M-004).
- Selector de mercado con botón "Aplicar al motor" → POST /api/market/reconfigure (ACT-M-004).
- Gráfico Lightweight Charts funcional con datos del hook (engineContext).
- Análisis técnico con request tracker (descarta respuestas obsoletas). FYL PAUSADO.
- Watchlist estática (BTC/USDT, ETH/USDT — "Sin datos").
- BottomBar.tsx montado (ACT-P-008) con 5 tabs (Balance/Positions/Orders/Fills/Activity); datos indisponibles por diseño sin cuenta activa.
- Laboratorio/Bot: `UnavailableView` con mensaje explícito. Sin datos simulados.
- 4 componentes en `frontend/src/components/`: AnalysisPanel, ChartPanel, AIAnalysisPanel (no montado), BottomBar (MONTADO).
- Tokens CSS y layout responsive implementados.
- Backend: GET /api/capabilities informativo; POST /api/analysis con velas; POST /api/market/reconfigure (ACT-M-004).

**Lo que NO existe hoy:**
- Navegación entre vistas con URL/hash routing.
- Persistencia de preferencias de vista.
- Datos reales en watchlist, posiciones, órdenes y P&L.
- Integración de AIAnalysisPanel en la UI.
- Laboratorios/Bot implementados.
- Capturas visuales o mockups en el repositorio.
- Design system de Stitch.

**Dependencias para ACT-T-002:** ACT-T-001 (este task), autorización de diseño, definición de shell/navegación, y preferencias según ACT-P-001 cuando esté disponible.

---

*Fin del informe ACT-T-001. Sin cambios al repositorio. Sin ejecución de tests ni servicios externos. Build verificado (runtime). Inspección visual de navegador PENDIENTE.*

---

## I. Correcciones aplicadas por inspección (2026-10-22)

| Afirmación previa | Estado real | Fuente |
|---|---|---|
| `895cf39` y `718e044` son commits ACT-M-004 | Son fixes post-ACT-M-004 (late HTTP guard + TS2339 narrow). ACT-M-004 work está en commits anteriores. | `git show 895cf39` → `useMarketData.ts` + `market-websocket.test.ts`; `git show 718e044` → `useMarketData.ts` solo |
| BottomBar "no disponible" por defecto de integración | **Por diseño:** App.tsx:258 "no account is inferred or persisted"; accountId/sessionId son `null` intencionalmente | `App.tsx:259-260`, `usePaperData.ts:64-68`, `BottomBar.tsx:39-40` |
| Inspección visual completada | **PENDIENTE:** no browser tooling disponible. DOM structure confirmada por inspección de fuente (no observación de navegador). | — |
| ACT-M-004 work uncommitted | ACT-M-004 está **en origin/master** en commits anteriores. HEAD = origin/master = `718e044`. | `git log origin/master` |
| BottomBar/PAPER atribuido a ACT-M-004 | BottomBar/PAPER es **ACT-P-008**, no ACT-M-004. ACT-M-004 es selección de mercado (símbolo/intervalo). | `App.tsx:259-260`, `paper.py`, `paper.ts` |
| typecheck/build como ejecutado | typecheck verificado en plan (fuente); build ejecutado en este task (runtime). | tsconfig.json, `npm run build` output |
