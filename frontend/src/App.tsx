// Terminal de desarrollo: procedencia explícita y ejecución/cartera no implementadas.

import type { MarketDataSource, MarketStatus } from './types/market'
import { useMarketData } from './hooks/useMarketData'
import { ChartPanel } from './components/ChartPanel'
import { AnalysisPanel } from './components/AnalysisPanel'

// ── Componente: Barra superior ───────────────────────────────────────────────

function HeaderBar({ status, dataSource, wsConnected }: {
  status: MarketStatus | null
  dataSource: MarketDataSource
  wsConnected: boolean
}) {
  const marketConnected = dataSource === 'market_engine' && wsConnected
  const dotClass = marketConnected ? 'connected' : status?.reconnecting ? 'reconnecting' : ''
  const marketLabel = dataSource === 'TEST_ONLY' ? 'TEST_ONLY: datos sintéticos'
    : marketConnected ? 'Motor de mercado conectado; frescura no validada'
    : 'Conexión de mercado no verificada'

  return (
    <header className="header-bar">
      <span className="env-badge paper">Sin ejecución</span>
      <div className={`status-dot ${dotClass}`} title={marketLabel} />
      <span style={{ fontSize: '13px', color: 'var(--color-text-muted)' }}>
        Bot: no implementado
      </span>
      <span style={{ marginLeft: 'auto', fontSize: '11px', color: 'var(--color-text-muted)' }}>
        v0.1.0 · Terminal de desarrollo
      </span>
    </header>
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

// ── App principal ────────────────────────────────────────────────────────────

export default function App() {
  // Hook de datos de mercado (HTTP histórico + WebSocket streaming)
  const { candles, status, wsConnected, loading: marketLoading, error, dataSource, interval } = useMarketData('BTC/USDT')

  return (
    <div className="app-layout">
      <HeaderBar status={status} dataSource={dataSource} wsConnected={wsConnected} />

      {error && (
        <div role="alert" style={{ padding: '8px 16px', color: 'var(--color-red)' }}>
          Error de datos de mercado: {error}
        </div>
      )}
      <div className="main-body">
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
      </div>
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
