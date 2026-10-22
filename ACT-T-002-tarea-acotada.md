# ACT-T-002 — Adaptar shell/Terminal existentes conforme al brief visual

**Prioridad:** P1
**Estado:** PENDIENTE (awaiting authorization)
**Dependencia:** ACT-T-001 (inventario corregido), H0 satisfied
**Autorización:** Requiere autorización explícita del usuario para cambios de código

---

## Alcance acotado

Adaptar el shell existente (`App.tsx`) y la Terminal conforme al brief visual (`trading-console-visual-brief.md`), **reutilizando componentes y contratos existentes**. No Stitch, no nuevo design system, no preference persistence.

### T1: Consumir /api/capabilities y representar estados honestos (scope mínimo)

**Archivo:** `frontend/src/App.tsx`
**Cambios:**
1. Importar `getCapabilities` desde `frontend/src/api/market.ts` (o crear si no existe — verificar primer).
2. Agregar estado de capabilities en `App()`: `useEffect` que llama `getCapabilities()` al mount.
3. Actualizar `HeaderBar` para mostrar:
   - `environment` real de backend (paper/exchange_demo) en badge
   - `execution_available`, `portfolio_available`, `paper_simulation_available` como indicadores de capacidad
   - Mantener "Sin ejecución" cuando `execution_available === false`
4. Actualizar `BottomBar` para:
   - Mostrar "PAPER read-only" solo cuando `paper_simulation_available === true`
   - Mostrar "No disponible" cuando `paper_simulation_available === false`

**Límites:** No crear cuenta PAPER, no auto-seleccionar accountId. BottomBar permanece con accountId=null por diseño.

### T2: Adaptar Terminal al brief visual (mínimo)

**Archivos:** `frontend/src/App.tsx`, `frontend/src/styles/tokens.css`
**Cambios:**
1. Verificar que tokens CSS (`tokens.css`) coinciden con brief (§2): colores, tipografía Inter, radios 6-10, spacing 4/8.
2. Ajustar `HeaderBar`:
   - Mantener 56px height
   - Badge de entorno con color correcto (paper=verde, demo=azul)
   - Separar indicador de conexión, fuente/frescura y capacidad
3. Ajustar `NavBar`:
   - Tres tabs: Terminal/Laboratorio/Bot (ya existentes)
   - Mantener keyboard navigation y ARIA roles
4. Ajustar `main-body` layout:
   - Watchlist izquierda 180-220px (ya existe ~200px)
   - ChartPanel central flexible (ya existe flex:1)
   - AnalysisPanel derecho 300-360px (verificar ancho actual)
5. Mantener `UnavailableView` para Laboratorio/Bot (ya existe)

**Límites:** No agregar Qwen/AIAnalysisPanel. No agregar indicadores overlay en gráfico. No agregar persistencia de preferencias.

### T3: Verificar y ajustar componentes existentes

**Archivos verificados:**
- `ChartPanel.tsx`: Lightweight Charts con velas OHLCV, overlay de conexión y fuente
- `AnalysisPanel.tsx`: POST /api/analysis, Keltner(20/14/2), MACD cruce, request tracker
- `BottomBar.tsx`: 5 tabs, usePaperData, PAPER read-only
- `WatchlistPanel`: array fijo ['BTC/USDT', 'ETH/USDT'], "Sin datos"

**Cambios mínimos:**
- Verificar que AnalysisPanel ancho está dentro de 300-360px del brief
- Verificar que WatchlistPanel ancho está dentro de 180-220px del brief
- Ajustar si es necesario

---

## Decisiones restantes (requieren autorización)

| Decisión | Opciones | Responsable |
|---|---|---|
| D1: ¿Consumir /api/capabilities? | Sí: mostrar estado real del backend | Qwen (autorizado por este task) |
| D2: BottomBar muestra "PAPER read-only" o "No disponible" según capacidad? | Sí: usar paper_simulation_available del capabilities | Qwen (autorizado por este task) |
| D3: ¿Ajustar tokens CSS para coincidir con brief? | Verificar y ajustar si difieren | Qwen (autorizado por este task) |
| D4: ¿Agregar Qwen/AIAnalysisPanel a Terminal? | NO (fuera de alcance) | — |
| D5: ¿Agregar persistencia de preferencias? | NO (fuera de alcance, ACT-P-001) | — |
| D6: ¿Usar Stitch para dos propuestas visuales? | NO (instrucción: no Stitch dependency) | — |

---

## Restricciones explícitas

1. **No reescribir** React, FastAPI, Lightweight Charts ni la estructura por código de Stitch.
2. **No inventar** FYL/MACD BB ni métricas operativas.
3. **No agregar** persistencia de preferencias (ACT-P-001).
4. **No integrar** AIAnalysisPanel (fuera de alcance).
5. **No auto-seleccionar** accountId para BottomBar (por diseño es null).
6. **No abrir** Stitch, no crear design system, no generar mockups.
7. **No reabrir** FYL (ACT-S-007 PAUSADO) ni accepted backend slices.
8. **No tratar** ausencia de URL routing como defecto (el brief permite estado React).

---

## Criterios de cierre

1. `npm run build` → exit 0 (typecheck + production build)
2. `cd frontend && npm test` → todos los tests existentes pasan
3. Backend GET /api/capabilities consumido y representado honestamente
4. BottomBar muestra estado correcto según paper_simulation_available
5. Layout respeta brechas del brief: header 56px, watchlist 180-220px, chart central, analysis 300-360px
6. Estados honestos: "Sin ejecución" cuando execution_available=false
7. Sin botones que simulen operaciones no implementadas
8. Keyboard navigation y ARIA roles preservados

---

## Archivos de referencia

- `trading-bot-action-plan.md` — autoridad canónica de estados y dependencias
- `trading-console-visual-brief.md` — diseño objetivo (149 líneas)
- `ACT-T-001-inventario-visual-mapa-vistas.md` — inventario corregido (316 líneas)
- `frontend/src/App.tsx` — shell existente (335 líneas)
- `frontend/src/styles/tokens.css` — tokens CSS actuales (78 líneas)
- `frontend/src/api/paper.ts` — cliente PAPER (ya aceptado ACT-P-008)
- `frontend/src/hooks/usePaperData.ts` — hook PAPER (ya aceptado ACT-P-008)
- `backend/src/api/paper.py` — endpoints PAPER read-only (6 endpoints, ya aceptados)

---

*Fin de ACT-T-002. Pendiente de autorización para implementación.*
