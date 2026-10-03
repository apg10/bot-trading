// Cliente de mercado para el frontend (HTTP + WebSocket).

import type { CandleData, MarketStatus } from "../types/market"

const API_BASE = import.meta.env.VITE_API_URL || "http://localhost:8000"

// ── HTTP ─────────────────────────────────────────────────────────────────────

export async function getMarketStatus(symbol: string = "BTC/USDT"): Promise<MarketStatus> {
  const resp = await fetch(`${API_BASE}/api/market/status?symbol=${encodeURIComponent(symbol)}`)
  if (!resp.ok) throw new Error(`HTTP ${resp.status}`)
  return resp.json() as Promise<MarketStatus>
}

export async function getCandles(
  symbol: string = "BTC/USDT",
  limit: number = 100,
): Promise<CandleData[]> {
  const resp = await fetch(`${API_BASE}/api/market/candles?symbol=${encodeURIComponent(symbol)}&limit=${limit}`)
  if (!resp.ok) throw new Error(`HTTP ${resp.status}`)
  return resp.json() as Promise<CandleData[]>
}

// ── WebSocket multiplexado ───────────────────────────────────────────────────

export interface MarketWSMessage {
  type: string
  [key: string]: unknown
}

export interface CandleUpdate {
  type: "bar_closed" | "bar_updated"
  candle: CandleData
}

export interface GapEvent {
  type: "gap"
  gap_ms: number
  previous_close_time: number | null
}

export interface StatusEvent {
  type: "status"
  symbol: string
  connected: boolean
  reconnecting: boolean
  closed_candles_count: number
  last_bar_open_time: number | null
}

export class MarketWebSocket {
  private ws: WebSocket | null = null
  private url: string
  private reconnectInterval: ReturnType<typeof setInterval> | null = null
  private reconnectDelay = 1000
  private maxReconnectDelay = 30000
  private _onMessage: ((msg: MarketWSMessage) => void) | null = null

  constructor(url?: string) {
    const proto = window.location.protocol === "https:" ? "wss://" : "ws://"
    this.url = url || `${proto}${window.location.host}/api/market/ws`
  }

  onMessage(handler: (msg: MarketWSMessage) => void): void {
    this._onMessage = handler
  }

  connect(): void {
    if (this.ws?.readyState === WebSocket.OPEN) return

    this.ws = new WebSocket(this.url)

    this.ws.onopen = () => {
      this.reconnectDelay = 1000 // Reset delay on success
    }

    this.ws.onmessage = (event) => {
      const msg = JSON.parse(event.data) as MarketWSMessage
      if (this._onMessage) this._onMessage(msg)
    }

    this.ws.onerror = (err) => {
      console.error("Market WS error:", err)
    }

    this.ws.onclose = () => {
      // Auto-reconnect with exponential backoff
      this.reconnectInterval = setInterval(() => {
        this.connect()
      }, this.reconnectDelay)
      this.reconnectDelay = Math.min(this.reconnectDelay * 2, this.maxReconnectDelay)
    }
  }

  subscribe(channel: string): void {
    if (this.ws?.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify({ type: "subscribe", channel }))
    }
  }

  unsubscribe(channel: string): void {
    if (this.ws?.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify({ type: "unsubscribe", channel }))
    }
  }

  ping(): void {
    if (this.ws?.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify({ type: "ping" }))
    }
  }

  disconnect(): void {
    if (this.reconnectInterval) {
      clearInterval(this.reconnectInterval)
      this.reconnectInterval = null
    }
    if (this.ws) {
      this.ws.close()
      this.ws = null
    }
  }
}
