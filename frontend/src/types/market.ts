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
  // Optional while old development responses are still accepted by the UI.
  schema_version?: string
  stream_id?: string
  sequence?: number
  symbol: string
  interval: string
  data_source: "TEST_ONLY" | "market_engine"
  candles: CandleData[]
  complete?: boolean
}

export interface MarketTransportEnvelope {
  schema_version: string
  stream_id: string
  sequence: number
}

export interface MarketSnapshot extends MarketTransportEnvelope {
  type: "snapshot"
  candles: CandleData[]
  complete: boolean
}

export interface MarketResyncRequired extends MarketTransportEnvelope {
  type: "resync_required"
  reason: "gap" | "overflow"
  last_sequence: number
  recovery_required: true
}

export interface MarketCandleEvent {
  type: "bar_updated" | "bar_closed"
  schema_version?: string
  stream_id?: string
  sequence?: number
  symbol: string
  interval: string
  data_source: "market_engine"
  candle: CandleData
}

export interface MarketStatus {
  schema_version?: string
  stream_id?: string
  sequence?: number
  symbol: string
  connected: boolean
  reconnecting: boolean
  closed_candles_count: number
  last_bar_open_time: number | null
  interval: string | null
  data_source: "market_engine" | "unavailable"
  // Estado completo emitido por el motor.  Son opcionales únicamente para que
  // fixtures antiguas del websocket sigan siendo representables; N02 exige
  // todos estos campos antes de construir un snapshot.
  recovering?: boolean
  history_loaded?: boolean
  complete?: boolean
  stale?: boolean
  pending_gaps?: number
  last_closed_close_time?: number | null
  execution_available?: false
}
