// App principal — Terminal con gráfico de velas real + datos en tiempo real.

import { useState, useEffect } from 'react'
import type { EngineInfo, FixtureData, Position, Order } from './types'
import { useMarketData } from './hooks/useMarketData'
import { ChartPanel } from './components/ChartPanel'
import { BottomBar } from './components/BottomBar'

// ── Componente: Barra superior ───────────────────────────────────────────────

function HeaderBar({ engineState }: { engineState: EngineInfo }) {
  const envClass = engineState.environment === 'paper' ? 'paper' : 'demo'
  const dotClass = engineState.market_status === 'connected' ? 'connected'
    : engineState.market_status === 'disconnected' ? 'disconnected'
    : 'reconnecting'

  return (
    <header className="header-bar">
      <span className={`env-badge ${envClass}`}>{engineState.environment}</span>
      <div className={`status-dot ${dotClass}`} title={engineState.market_status} />
      <span style={{ fontSize: '13px', color: 'var(--color-text-muted)' }}>
        {engineState.state.toUpperCase()}
      </span>
      <span style={{ marginLeft: 'auto', fontSize: '11px', color: 'var(--color-text-muted)' }}>
        v{engineState.version} · Uptime: {Math.round(engineState.uptime_seconds)}s
      </span>
    </header>
  )
}

// ── Componente: Watchlist ────────────────────────────────────────────────────

function WatchlistPanel() {
  const [assets, setAssets] = useState([
    { symbol: 'BTC/USDT', price: '—', change: '—', positive: true },
    { symbol: 'ETH/USDT', price: '—', change: '—', positive: false },
  ])

  // Simular precios de watchlist (en fase posterior se conecta a Binance)
  useEffect(() => {
    const interval = setInterval(() => {
      setAssets(prev => prev.map(a => ({
        ...a,
        price: (parseFloat(a.price.replace(',', '')) + (Math.random() - 0.5) * 100).toFixed(2).replace(/\B(?=(\d{3})+(?!\d))/g, ','),
        change: `${(Math.random() * 4 - 2).toFixed(2)}%`,
        positive: Math.random() > 0.5,
      })))
    }, 3000)

    return () => clearInterval(interval)
  }, [])

  return (
    <aside className="watchlist-panel">
      <div className="panel-section">
        <h3>Watchlist</h3>
        {assets.map(a => (
          <div key={a.symbol} className="asset-item">
            <span>{a.symbol}</span>
            <span className={a.positive ? 'text-green' : 'text-red'}>
              {a.change}
            </span>
          </div>
        ))}
      </div>
    </aside>
  )
}

// ── Componente: Panel de análisis ────────────────────────────────────────────

function AnalysisPanel({ fixture }: { fixture: FixtureData | null }) {
  return (
    <aside className="analysis-panel">
      {/* Reglas pendientes — siempre visibles */}
      <div className="panel-section">
        <div className="pending-rules">
          <h4>⚠️ Reglas de estrategia pendientes</h4>
          <ul>
            <li>Fórmula y parámetros exactos de FYL, Keltner y MACD BB</li>
            <li>Tipo de barras, construcción, sesiones y calentamiento</li>
            <li>Reglas numéricas de impulso, retroceso, consolidación</li>
            <li>Resolver 3 barras de FYL plana + consolidaciones admitidas</li>
            <li>Fórmulas de entrada, caducidad, stop, objetivo y tamaño</li>
            <li>Momento de confirmación de pivotes sin usar el futuro</li>
          </ul>
        </div>
      </div>

      {/* Zonas */}
      {fixture?.zones && (
        <div className="panel-section">
          <h3>Zonas ({fixture.zones.length})</h3>
          {fixture.zones.map(z => (
            <div key={z.id} className="card">
              <div className="card-title">{z.origin.toUpperCase()} — {z.status}</div>
              <div className="card-detail">
                {z.low.toLocaleString()} — {z.high.toLocaleString()} · {z.contacts} contactos
              </div>
            </div>
          ))}
        </div>
      )}

      {/* Pivotes */}
      {fixture?.pivots && (
        <div className="panel-section">
          <h3>Pivotes ({fixture.pivots.length})</h3>
          {fixture.pivots.map(p => (
            <div key={p.id} className="card">
              <div className="card-title">{p.pivot_type.replace('_', ' ').toUpperCase()}</div>
              <div className="card-detail">
                Precio: {p.price.toLocaleString()} · Fuerza: {p.strength.toFixed(4)}
              </div>
            </div>
          ))}
        </div>
      )}

      {/* Anotaciones */}
      {fixture?.annotations && (
        <div className="panel-section">
          <h3>Anotaciones ({fixture.annotations.length})</h3>
          {fixture.annotations.map(a => (
            <div key={a.id} className="card">
              <div className="card-title">{a.annotation_type.replace('_', ' ').toUpperCase()}</div>
              <div className="card-detail">{a.content}</div>
            </div>
          ))}
        </div>
      )}
    </aside>
  )
}

// ── App principal ────────────────────────────────────────────────────────────

export default function App() {
  const [engineState, setEngineState] = useState<EngineInfo>({
    state: 'stopped',
    environment: 'paper',
    market_status: 'disconnected',
    uptime_seconds: 0,
    version: '0.1.0',
  })

  const [fixture, setFixture] = useState<FixtureData | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  // Posiciones y órdenes (simuladas — en fases posteriores se conectan al backend)
  const [positions] = useState<Position[]>([])
  const [orders] = useState<Order[]>([])
  const [activityLog] = useState<Array<{ id: string; timestamp_ms: number; type: "order_created" | "order_filled" | "order_cancelled" | "position_opened" | "position_closed" | "analysis"; symbol: string; description: string }>>([])

  // Hook de datos de mercado (HTTP histórico + WebSocket streaming)
  const { candles, status, wsConnected, loading: marketLoading } = useMarketData('BTC/USDT')

  useEffect(() => {
    // Fetch fixture data from backend
    fetch('/api/fixture/data')
      .then(res => {
        if (!res.ok) throw new Error(`HTTP ${res.status}`)
        return res.json()
      })
      .then(data => {
        setFixture(data as FixtureData)
        setLoading(false)
      })
      .catch(err => {
        setError(err.message)
        // Fallback to local fixture for dev
        import('./fixtures/synthetic').then(mod => {
          const f = mod.getFullFixture()
          setFixture(f as unknown as FixtureData)
          setLoading(false)
        }).catch(() => {
          setLoading(false)
        })
      })

    // Simulate engine state polling
    const interval = setInterval(() => {
      setEngineState(prev => ({
        ...prev,
        uptime_seconds: prev.uptime_seconds + 1,
      }))
    }, 1000)

    return () => clearInterval(interval)
  }, [])

  // Combinar loading states
  const isLoading = loading || marketLoading

  return (
    <div className="app-layout">
      <HeaderBar engineState={engineState} />

      {isLoading ? (
        <div style={{ display: 'flex', flex: 1, alignItems: 'center', justifyContent: 'center' }}>
          <span>Cargando terminal...</span>
        </div>
      ) : error ? (
        <div style={{ display: 'flex', flex: 1, alignItems: 'center', justifyContent: 'center', color: 'var(--color-red)' }}>
          Error: {error}
        </div>
      ) : (
        <>
          <div className="main-body">
            <WatchlistPanel />
            <ChartPanel
              candles={candles}
              symbol="BTC/USDT"
              loading={marketLoading}
              wsConnected={wsConnected}
            />
            <AnalysisPanel fixture={fixture} />
          </div>
          <BottomBar
            positions={positions}
            orders={orders}
            activityLog={activityLog}
          />
        </>
      )}
    </div>
  )
}
