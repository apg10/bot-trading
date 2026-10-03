// ── Tipos de mercado para el frontend (agregados al state.ts).

import type { Candle as BaseCandle } from "./state"

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

export interface MarketStatus {
  symbol: string
  connected: boolean
  reconnecting: boolean
  closed_candles_count: number
  last_bar_open_time: number | null
}
