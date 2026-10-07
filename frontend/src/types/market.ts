// ── Tipos de mercado para el frontend (agregados al state.ts).

export type MarketDataSource = "TEST_ONLY" | "market_engine" | "unknown"

export interface CandleData {
  open_time: number
  close_time: number
  open: number
  high: number
  low: number
  close: number
  volume: number
  is_closed: boolean
}

export interface CandleHistoryResponse {
  symbol: string
  interval: string
  data_source: "TEST_ONLY" | "market_engine"
  candles: CandleData[]
}

export interface MarketCandleEvent {
  type: "bar_updated" | "bar_closed"
  symbol: string
  interval: string
  data_source: "market_engine"
  candle: CandleData
}

export interface MarketStatus {
  symbol: string
  connected: boolean
  reconnecting: boolean
  closed_candles_count: number
  last_bar_open_time: number | null
  interval: string | null
  data_source: "market_engine" | "unavailable"
}
