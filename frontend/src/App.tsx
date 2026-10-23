// Terminal de desarrollo: shell con navegación honesta.
// La vista Bot mantiene indisponibilidad explícita; Laboratorio consulta datos reales.
// El hook useMarketData solo se activa en la vista Terminal.

import { useMarketData } from './hooks/useMarketData'
import { ENGINE_INTERVALS, reconfigure, getCapabilities, type EngineContext, type CapabilitiesResponse } from './api/market'
import { ChartPanel } from './components/ChartPanel'
import { AnalysisPanel } from './components/AnalysisPanel'
import { BottomBar } from './components/BottomBar'
import { LaboratoryView } from './components/LaboratoryView'
import { useEffect, useRef, useState } from 'react'
import type { KeyboardEvent } from 'react'
import { loadPreferences, saveDebounced } from './preferences'
import type { ActiveView, BottomTab, Interval, UiPreferences } from './preferences'

// ── Tipos de vista ────────────────────────────────────────────────────────────

type ViewId = ActiveView

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

function HeaderBar({ capabilities }: { capabilities: CapabilitiesResponse | null }) {
  const envLabel = capabilities
    ? (capabilities.environment === 'paper' ? 'PAPER' : 'DEMO')
    : null

  const envBadgeClass = capabilities
    ? (capabilities.environment === 'paper' ? 'env-badge paper' : 'env-badge demo')
    : 'env-badge'

  const executionLabel = capabilities
    ? (capabilities.execution_available ? 'Ejecución disponible' : 'Sin ejecución')
    : 'Entorno desconocido'

  // Distinguish: null=pending (desconocido), false=no disponible, true=disponible
  const capabilityItems = capabilities ? [
    { label: 'Mercado', available: capabilities.market_data_supported },
    { label: 'Análisis', available: capabilities.technical_analysis_supported },
    { label: 'IA', available: capabilities.ai_scenarios_supported },
    { label: 'Cartera', available: capabilities.portfolio_available },
    { label: 'PAPER', available: capabilities.paper_simulation_available },
  ] : []

  return (
    <header className="header-bar">
      {envLabel ? (
        <span className={envBadgeClass}>{envLabel}</span>
      ) : null}
      <span style={{ fontSize: '11px', color: capabilities ? 'var(--color-text-muted)' : 'var(--color-warning)', marginLeft: '8px' }}>
        {executionLabel}
      </span>
      <div
        className="status-dot"
        title={
          capabilities
            ? (capabilities.market_data_supported ? 'Datos de mercado soportados' : 'Datos de mercado no disponibles')
            : 'Estado del backend desconocido'
        }
      />
      <span style={{ fontSize: '13px', color: 'var(--color-text-muted)' }}>
        Bot: no implementado
      </span>
      {capabilityItems.length > 0 ? (
        <span style={{ marginLeft: '8px', fontSize: '11px', color: 'var(--color-text-muted)' }}>
          {capabilityItems.map((item, index) => (
            <span key={item.label}>
              {index > 0 && ' · '}
              <span style={{ opacity: item.available ? 1 : 0.55 }}>
                {item.label}: {item.available ? '✓' : '—'}
              </span>
            </span>
          ))}
        </span>
      ) : capabilities ? (
        <span style={{ marginLeft: '8px', fontSize: '11px', color: 'var(--color-warning)' }}>
          Sin capacidades disponibles
        </span>
      ) : (
        <span style={{ marginLeft: '8px', fontSize: '11px', color: 'var(--color-text-muted)' }}>
          Capacidades: desconocido
        </span>
      )}
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

// ── Componente: Vista "No disponible" (Bot y futuras vistas) ─────────────────

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

type MarketContext = { symbol: string; interval: Interval }

function MarketSelector({
  viewContext,
  engineContext,
  pending,
  error,
  onChange,
  onApply,
}: {
  viewContext: MarketContext
  engineContext: EngineContext
  pending: boolean
  error: string | null
  onChange: (context: MarketContext) => void
  onApply: () => void
}) {
  return (
    <div className="market-selector" role="group" aria-label="Selección de mercado" style={{ padding: '12px 16px', display: 'flex', gap: '8px', alignItems: 'end' }}>
      <label>
        Símbolo
        <select
          aria-label="Símbolo"
          value={viewContext.symbol}
          disabled={pending}
          onChange={event => onChange({ ...viewContext, symbol: event.target.value })}
        >
          <option value="BTC/USDT">BTC/USDT</option>
          <option value="ETH/USDT">ETH/USDT</option>
        </select>
      </label>
      <label>
        Intervalo
        <select
          aria-label="Intervalo"
          value={viewContext.interval}
          disabled={pending}
          onChange={event => onChange({ ...viewContext, interval: event.target.value as Interval })}
        >
          {ENGINE_INTERVALS.map(value => <option key={value} value={value}>{value}</option>)}
        </select>
      </label>
      <button type="button" onClick={onApply} disabled={pending}>
        {pending ? 'Aplicando…' : 'Aplicar al motor'}
      </button>
      <span aria-live="polite" style={{ color: 'var(--color-text-muted)', fontSize: '11px' }}>
        Motor: {engineContext.symbol} · {engineContext.interval}
      </span>
      {error && <span role="alert" style={{ color: 'var(--color-negative)' }}>{error}</span>}
    </div>
  )
}

function TerminalView({
  engineContext,
  viewContext,
  pending,
  reconfigureError,
  onChange,
  onApply,
  onEngineContext,
}: {
  engineContext: EngineContext
  viewContext: MarketContext
  pending: boolean
  reconfigureError: string | null
  onChange: (context: MarketContext) => void
  onApply: () => void
  onEngineContext: (context: EngineContext) => void
}) {
  const { candles, status, streamId, wsConnected, loading: marketLoading, error, dataSource, interval } = useMarketData(
    engineContext.symbol,
    engineContext.interval,
    pending,
  )
  const loading = marketLoading || pending

  useEffect(() => {
    const currentStreamId = streamId ?? status?.stream_id
    // Do not let a status from the previous stream overwrite the context
    // announced by a newer transport envelope.  Status from the current
    // stream is authoritative for tabs that did not initiate reconfigure.
    const currentStatus = status && (!streamId || !status.stream_id || status.stream_id === streamId)
      ? status : null
    const currentSymbol = currentStatus?.symbol ?? engineContext.symbol
    const currentInterval = currentStatus?.interval ?? engineContext.interval
    if (currentStreamId) {
      onEngineContext({ symbol: currentSymbol, interval: currentInterval, stream_id: currentStreamId })
    }
  }, [streamId, status?.stream_id, status?.symbol, status?.interval, engineContext.symbol, engineContext.interval])

  return (
    <>
      <MarketSelector
        viewContext={viewContext}
        engineContext={engineContext}
        pending={pending}
        error={reconfigureError}
        onChange={onChange}
        onApply={onApply}
      />
      {error && (
        <div role="alert" style={{ padding: '8px 16px', color: 'var(--color-negative)' }}>
          Error de datos de mercado: {error}
        </div>
      )}
      <WatchlistPanel />
      <ChartPanel
        candles={candles}
        symbol={engineContext.symbol}
        loading={loading}
        error={error}
        wsConnected={wsConnected}
        dataSource={dataSource}
        interval={interval}
      />
      <AnalysisPanel
        candles={candles}
        symbol={engineContext.symbol}
        loading={loading}
        dataSource={dataSource}
        interval={interval}
        status={status}
      />
    </>
  )
}

// ── App principal ────────────────────────────────────────────────────────────

export default function App() {
  const [activeView, setActiveView] = useState<ViewId>('terminal')
  // PAPER identifiers are session state only; no account is inferred or persisted.
  const [paperAccountId] = useState<string | null>(null)
  const [paperSessionId] = useState<string | null>(null)
  const [viewContext, setViewContext] = useState<MarketContext>({ symbol: 'BTC/USDT', interval: '1m' })
  const [bottomTab, setBottomTab] = useState<BottomTab>('balances')
  const [preferencesLoaded, setPreferencesLoaded] = useState(false)
  const skipNextPreferencesSave = useRef(false)
  const [engineContext, setEngineContext] = useState<EngineContext>({
    symbol: 'BTC/USDT', interval: '1m', stream_id: 'unavailable',
  })
  const [reconfiguring, setReconfiguring] = useState(false)
  const [reconfigureError, setReconfigureError] = useState<string | null>(null)
  const [capabilities, setCapabilities] = useState<CapabilitiesResponse | null>(null)
  const [capabilitiesError, setCapabilitiesError] = useState<string | null>(null)

  // Restore presentation state only. In particular, the saved interval updates
  // the view context and never calls the market engine reconfiguration API.
  useEffect(() => {
    let cancelled = false
    loadPreferences().then(result => {
      if (cancelled) return
      skipNextPreferencesSave.current = true
      setActiveView(result.normalized.view.activeView)
      setBottomTab(result.normalized.view.bottomTab)
      setViewContext(context => ({ ...context, interval: result.normalized.view.interval }))
      setPreferencesLoaded(true)
    })
    return () => { cancelled = true }
  }, [])

  // Persist only user changes after the initial restoration has completed.
  useEffect(() => {
    if (!preferencesLoaded) return
    if (skipNextPreferencesSave.current) {
      skipNextPreferencesSave.current = false
      return
    }

    const preferences: UiPreferences = {
      schemaVersion: '1',
      view: {
        activeView,
        bottomTab,
        interval: viewContext.interval,
      },
      metadata: {
        lastSaved: new Date().toISOString(),
        schemaVersion: '1',
      },
    }

    return saveDebounced(preferences)
  }, [activeView, bottomTab, viewContext.interval, preferencesLoaded])

  // Fetch capabilities on mount — honest state representation per brief §5.
  useEffect(() => {
    let cancelled = false
    getCapabilities()
      .then(caps => { if (!cancelled) setCapabilities(caps) })
      .catch(err => { if (!cancelled) setCapabilitiesError(err.message) })
    return () => { cancelled = true }
  }, [])

  const applyToEngine = async () => {
    setReconfiguring(true)
    setReconfigureError(null)
    try {
      const next = await reconfigure(viewContext.symbol, viewContext.interval)
      setEngineContext({ symbol: next.symbol, interval: next.interval, stream_id: next.stream_id })
    } catch (error: unknown) {
      setReconfigureError(error instanceof Error ? error.message : 'No se pudo reconfigurar el motor')
    } finally {
      setReconfiguring(false)
    }
  }

  return (
    <div className="app-layout">
      <HeaderBar capabilities={capabilities} />

      {/* Navegación */}
      <NavBar activeView={activeView} onChange={setActiveView} />

      {/* Error de capacidades — no bloquea la app, solo informa */}
      {capabilitiesError && (
        <div role="alert" style={{ padding: '8px 16px', color: 'var(--color-warning)', fontSize: '12px' }}>
          No se pudo obtener el estado del backend: {capabilitiesError}
        </div>
      )}

      {/* Cuerpo principal — solo Terminal activa useMarketData */}
      <main className="main-body">
        <section
          className="view-panel"
          role="tabpanel"
          id="panel-terminal"
          aria-labelledby="tab-terminal"
          tabIndex={0}
          hidden={activeView !== 'terminal'}
        >
          {activeView === 'terminal' && (
            <TerminalView
              engineContext={engineContext}
              viewContext={viewContext}
              pending={reconfiguring}
              reconfigureError={reconfigureError}
              onChange={setViewContext}
              onApply={applyToEngine}
              onEngineContext={context => setEngineContext(context)}
            />
          )}
        </section>
        <section
          className="view-panel"
          role="tabpanel"
          id="panel-laboratorio"
          aria-labelledby="tab-laboratorio"
          tabIndex={0}
          hidden={activeView !== 'laboratorio'}
        >
          {activeView === 'laboratorio' && <LaboratoryView />}
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
      </main>

      <BottomBar
        accountId={paperAccountId}
        sessionId={paperSessionId}
        paperAvailable={capabilities?.paper_simulation_available}
        activeTab={bottomTab}
        onActiveTabChange={setBottomTab}
      />
    </div>
  )
}
