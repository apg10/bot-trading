# Verificación de tareas ACT-T-001 a ACT-T-004

| Task | Status | Build | Tests | Reviewer |
|---|---|---|---|---|
| ACT-T-001 | CORREGIDO | Read-only; runtime evidence clasificada | No se ejecutó suite; no se presenta como evidencia de build | Corrección revisada |
| ACT-T-002 | COMPLETE | `npm run build`: exit 0 (`tsc -b` + Vite production build) | Frontend: 348/348 passed | PASS |
| ACT-T-003-S1 | COMPLETE; preferencias bloqueadas | `npm run build`: exit 0 | Frontend: 348/348 passed | PASS |
| ACT-T-004 | COMPLETE | `npm run build`: exit 0 | Frontend: 348/348 passed; focal N02 incluido | PASS |

## ACT-T-001 — Inventario visual corregido

**Status:** CORREGIDO. Se corrigió el inventario para reflejar los componentes y el wiring realmente existentes: `App`, `HeaderBar`, `NavBar`, `ChartPanel`, `AnalysisPanel` y `BottomBar`, además de las vistas Laboratorio/Bot no disponibles. También se separó lo montado, lo placeholder y lo existente pero no montado (`AIAnalysisPanel`).

**Evidence classification:** inspección de source y estado/runtime documentado. El inventario no afirma una inspección visual de navegador que no esté evidenciada; por eso su evidencia es de código/estado, no de screenshots o browser QA.

## ACT-T-002 — Shell/Terminal adaptation

**Status:** COMPLETE. Se añadió el consumo de `getCapabilities()` al mount, representación honesta de environment/capabilities en `HeaderBar` y estados de disponibilidad PAPER en `BottomBar`. Se preservaron la navegación keyboard/ARIA, las vistas no disponibles y la separación view/engine. El layout quedó alineado con el brief: header de 56 px, watchlist de 180–220 px, chart flexible y `AnalysisPanel` de 300–360 px.

**Evidence:** `npm run build` terminó con exit 0 y la suite frontend registró 348/348 tests passed. El trabajo pasó el ciclo de implementación/revisión y la revisión final fue PASS. No se añadió `AIAnalysisPanel`, ejecución ficticia ni persistencia de preferencias.

## ACT-T-003-S1 — Volume series + delta updates

**Status:** COMPLETE para el slice visual; la persistencia de preferencias queda bloqueada por contrato pendiente (ver abajo). `ChartPanel` usa `HistogramSeries`, mapea OHLCV a una serie de velas y una serie de volumen, y conserva un buffer máximo de 200 velas. Los cambios compatibles usan `update()` para la última barra y append; cambios históricos, cambio de contexto o recorte reconstruyen con `setData()`, preservando el zoom cuando corresponde.

**Evidence:** build limpio (`npm run build`, exit 0) y 348/348 frontend tests passed, incluidos los casos de volumen, delta, rolling window y reconstrucción. Reviewer PASS. No se implementó persistencia fuera del contrato UI.

## ACT-T-004 — N02 advisory scenarios

**Status:** COMPLETE. `frontend/src/api/scenarios.ts` construye el snapshot N02 desde la última vela cerrada y el `MarketStatus`, valida identidad, vigencia y estructura, y oculta detalles de errores internos. `AnalysisPanel` integra estados `pending`, `busy`, `error`, `expired` y contexto ausente. `App.tsx` conecta el `status` real al panel.

**Evidence:** build con exit 0 y 348/348 frontend tests passed, con focal N02 incluido. Reviewer PASS. La integración es **advisory-only**: `executable=false`, sin órdenes, fills ni capacidad de ejecución; no se fabrica evidencia para zonas, checklist o portfolio.

## ACT-T-003 Preferences dependency

La dirección real de dependencia es: **UI → contract → ACT-P-001**. La semántica de la UI debe definir primero el contrato de preferencias; ACT-P-001 puede persistirlo después. No es un bloqueo circular.

Decisiones aún faltantes:

- Qué preferencias persistir: layout/panels, layers, interval y density.
- Dónde vive el contrato: componente UI responsable o dedicated store.
- Cómo se dispara la persistencia: únicamente después de que la UI defina el contrato.

## Authorization notes

Las actualizaciones de documentación están autorizadas. **Commit/push no están autorizados.**

---

## ACT-T-005: QA responsive/accesible de Terminal y paneles

**Estado:** PASS | **Build:** ✅ | **Tests:** 354/354 (7 files) | **Reviewer:** PASS (2 cycles)

### Cambios implementados
1. **Responsive (ACT-T-005-B):**
   - `.view-panel` mobile `flex-direction: column` bajo 768px + `min-height:0` + `overflow-y:auto`
   - Touch targets ≥44px (min-height + min-width)
   - `prefers-reduced-motion` media query en tokens.css
   - Focus visible global `:focus-visible`
   - Header reflow en pantallas pequeñas

2. **Accesibilidad (ACT-T-005-C):**
   - BottomBar tabs: `role="tab"`, `aria-controls`, `aria-selected`, `role="tablist"`, `role="tabpanel"`
   - Roving tab index: activo `0`, inactivo `-1`, Arrow/Home/End navigation
   - ChartPanel: `<figure>`/`<figcaption>`, `aria-label` en canvas
   - AnalysisPanel: `role="status"` para loading/empty, `role="alert"` solo para errores reales
   - `<main>` landmark reemplaza `<div className="main-body">`

### Issues encontrados y corregidos
- **High:** 390px layout clip → `min-height:0` + `overflow-y:auto` en `.view-panel`
- **Medium:** Touch width no garantizado → `min-width:44px` en controles interactivos
- **Medium:** Roving tab index faltante → `tabIndex={activeTab ? 0 : -1}`
- **Medium:** Main landmark faltante → `<main>` reemplaza `<div className="main-body">`

### Limitaciones
- No tooling de navegador (axe/Lighthouse/Puppeteer) disponible para auditoría runtime
- Contraste verificado por inspección de tokens; no medido con herramienta
- Viewport 390×844 verificado por CSS inspection; no screenshot
