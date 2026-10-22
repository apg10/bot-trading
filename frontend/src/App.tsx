// Terminal de desarrollo: shell con navegación honesta.
// Vistas Laboratorio y Bot muestran indisponibilidad explícita sin datos simulados.
// El hook useMarketData solo se activa en la vista Terminal.

import type { MarketDataSource, MarketStatus } from './types/market'
import { useMarketData } from './hooks/useMarketData'
import { ChartPanel } from './components/ChartPanel'
import { AnalysisPanel } from './components/AnalysisPanel'
import { useRef, useState } from 'react'
import type { KeyboardEvent } from 'react'

// ── Tipos de vista ────────────────────────────────────────────────────────────

type ViewId = 'terminal' | 'laboratorio' | 'bot'

interface NavTabDef {
  id: ViewId
  label: string
  ariaLabel: string
}

const NAV_TABS: NavTabDef[] = [
  { id: 'terminal', label: 'Terminal', ariaLabel: 'Ir a Terminal' },
  { id: 'laboratorio', label: 'Laboratorio', ariaLabel: 'Ir a Laboratorio' },
  { id: 'bot', label: 'Bot', ariaLabel: 'Ir a Bot' },
]

// ── Componente: Barra superior ───────────────────────────────────────────────

function HeaderBar() {
  return (
    <header className="header-bar">
      <span className="env-badge paper">Sin ejecución</span>
      <div className="status-dot" title="Conexión de mercado no verificada" />
      <span style={{ fontSize: '13px', color: 'var(--color-text-muted)' }}>
        Bot: no implementado
      </span>
      <span style={{ marginLeft: 'auto', fontSize: '11px', color: 'var(--color-text-muted)' }}>
        v0.1.0 · Terminal de desarrollo
      </span>
    </header>
  )
}

// ── Componente: Barra de navegación ──────────────────────────────────────────

function NavBar({ activeView, onChange }: {
  activeView: ViewId
  onChange: (view: ViewId) => void
}) {
  const tabRefs = useRef<Array<HTMLButtonElement | null>>([])

  const handleKeyDown = (event: KeyboardEvent<HTMLButtonElement>, index: number) => {
    let nextIndex: number | null = null
    if (event.key === 'ArrowRight') nextIndex = (index + 1) % NAV_TABS.length
    if (event.key === 'ArrowLeft') nextIndex = (index - 1 + NAV_TABS.length) % NAV_TABS.length
    if (event.key === 'Home') nextIndex = 0
    if (event.key === 'End') nextIndex = NAV_TABS.length - 1
    if (nextIndex === null) return

    event.preventDefault()
    onChange(NAV_TABS[nextIndex].id)
    tabRefs.current[nextIndex]?.focus()
  }

  return (
    <nav className="nav-bar" role="tablist" aria-label="Navegación principal">
      {NAV_TABS.map((tab, index) => (
        <button
          key={tab.id}
          ref={element => { tabRefs.current[index] = element }}
          role="tab"
          aria-selected={activeView === tab.id}
          aria-controls={`panel-${tab.id}`}
          id={`tab-${tab.id}`}
          className="nav-tab"
          onClick={() => onChange(tab.id)}
          tabIndex={activeView === tab.id ? 0 : -1}
          onKeyDown={event => handleKeyDown(event, index)}
        >
          {tab.label}
        </button>
      ))}
    </nav>
  )
}

// ── Componente: Vista "No disponible" ────────────────────────────────────────

function UnavailableView({ viewId }: { viewId: ViewId }) {
  const labels: Record<ViewId, string> = {
    terminal: 'Terminal',
    laboratorio: 'Laboratorio',
    bot: 'Bot',
  }
  return (
    <div className="unavailable-view">
      <span className="icon">📋</span>
      <h2>{labels[viewId]} no disponible</h2>
      <p>Esta vista está planificada pero aún no implementada. No se muestran datos, métricas ni estados ficticios.</p>
    </div>
  )
}

// ── Componente: Watchlist ────────────────────────────────────────────────────

function WatchlistPanel() {
  const symbols = ['BTC/USDT', 'ETH/USDT']

  return (
    <aside className="watchlist-panel">
      <div className="panel-section">
        <h3>Watchlist</h3>
        {symbols.map(symbol => (
          <div key={symbol} className="asset-item">
            <span>{symbol}</span>
            <span style={{ color: 'var(--color-text-muted)' }}>Sin datos</span>
          </div>
        ))}
      </div>
    </aside>
  )
}

// ── Componente: Terminal (solo esta vista activa el hook de mercado) ─────────

function TerminalView() {
  const { candles, status, wsConnected, loading: marketLoading, error, dataSource, interval } = useMarketData('BTC/USDT')

  return (
    <>
      {error && (
        <div role="alert" style={{ padding: '8px 16px', color: 'var(--color-negative)' }}>
          Error de datos de mercado: {error}
        </div>
      )}
      <WatchlistPanel />
      <ChartPanel
        candles={candles}
        symbol="BTC/USDT"
        loading={marketLoading}
        wsConnected={wsConnected}
        dataSource={dataSource}
        interval={interval}
      />
      <AnalysisPanel
        candles={candles}
        symbol="BTC/USDT"
        loading={marketLoading}
        dataSource={dataSource}
        interval={interval}
      />
    </>
  )
}

// ── App principal ────────────────────────────────────────────────────────────

export default function App() {
  const [activeView, setActiveView] = useState<ViewId>('terminal')

  return (
    <div className="app-layout">
      <HeaderBar />

      {/* Navegación */}
      <NavBar activeView={activeView} onChange={setActiveView} />

      {/* Cuerpo principal — solo Terminal activa useMarketData */}
      <div className="main-body">
        <section
          className="view-panel"
          role="tabpanel"
          id="panel-terminal"
          aria-labelledby="tab-terminal"
          tabIndex={0}
          hidden={activeView !== 'terminal'}
        >
          {activeView === 'terminal' && <TerminalView />}
        </section>
        <section
          className="view-panel"
          role="tabpanel"
          id="panel-laboratorio"
          aria-labelledby="tab-laboratorio"
          tabIndex={0}
          hidden={activeView !== 'laboratorio'}
        >
          {activeView === 'laboratorio' && <UnavailableView viewId="laboratorio" />}
        </section>
        <section
          className="view-panel"
          role="tabpanel"
          id="panel-bot"
          aria-labelledby="tab-bot"
          tabIndex={0}
          hidden={activeView !== 'bot'}
        >
          {activeView === 'bot' && <UnavailableView viewId="bot" />}
        </section>
      </div>

      {/* Footer honesto — sin datos ficticios */}
      <footer className="bottom-bar" style={{ flexWrap: 'wrap' }}>
        <span>Posiciones: no consultadas</span>
        <span>Órdenes: no consultadas</span>
        <span>P&amp;L: no disponible</span>
        <span style={{ marginLeft: 'auto', color: 'var(--color-text-muted)' }}>
          Cartera y simulador PAPER: no implementados
        </span>
      </footer>
    </div>
  )
}
