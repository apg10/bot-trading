// Cliente de mercado para el frontend (HTTP + WebSocket).
// HTTP y WS comparten VITE_API_URL o, por defecto, el origen de la página.

import type { CandleData, CandleHistoryResponse, MarketStatus } from "../types/market"

// ── Resolución común de URLs ──────────────────────────────────────────────────

export function getApiUrl(path: string): string {
  const base = new URL(
    import.meta.env.VITE_API_URL?.trim() || window.location.origin,
    window.location.origin,
  )
  if (base.protocol !== "http:" && base.protocol !== "https:") {
    throw new Error("VITE_API_URL debe usar HTTP o HTTPS")
  }

  // Conservar un posible prefijo de despliegue sin duplicar barras.
  base.pathname = `${base.pathname.replace(/\/+$/, "")}/`
  base.search = ""
  base.hash = ""
  return new URL(path.replace(/^\/+/, ""), base).href
}

export function getWebSocketUrl(path: string): string {
  const url = new URL(getApiUrl(path))
  url.protocol = url.protocol === "https:" ? "wss:" : "ws:"
  return url.href
}

// ── HTTP ─────────────────────────────────────────────────────────────────────

export async function getMarketStatus(symbol: string = "BTC/USDT"): Promise<MarketStatus> {
  const resp = await fetch(getApiUrl(`/api/market/status?symbol=${encodeURIComponent(symbol)}`))
  if (!resp.ok) throw new Error(`HTTP ${resp.status}`)
  return resp.json() as Promise<MarketStatus>
}

export async function getCandles(
  symbol: string = "BTC/USDT",
  limit: number = 100,
): Promise<CandleHistoryResponse> {
  const resp = await fetch(getApiUrl(`/api/market/candles?symbol=${encodeURIComponent(symbol)}&limit=${limit}`))
  if (!resp.ok) throw new Error(`HTTP ${resp.status}`)
  const data = await resp.json() as CandleHistoryResponse
  if (
    !data || typeof data !== "object" || Array.isArray(data)
    || data.symbol !== symbol || typeof data.interval !== "string" || !data.interval
    || (data.data_source !== "TEST_ONLY" && data.data_source !== "market_engine")
    || !Array.isArray(data.candles)
  ) {
    throw new Error("Histórico sin procedencia válida o con contrato incompatible")
  }
  return data
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
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null
  private reconnectDelay = 1000
  private maxReconnectDelay = 30000
  private _onMessage: ((msg: MarketWSMessage) => void) | null = null
  private _onOpen: (() => void) | null = null
  private _onClose: (() => void) | null = null
  private subscriptions = new Set<string>()
  private activeSubscriptions = new Set<string>()
  private opened = false
  private reconnecting = false
  private intentionallyDisconnected = false

  constructor(url?: string) {
    this.url = url || getWebSocketUrl("/api/market/ws")
  }

  // ── Callbacks para informar al hook sobre el ciclo de vida real del WS ──

  onMessage(handler: (msg: MarketWSMessage) => void): void {
    this._onMessage = handler
  }

  onOpen(handler: () => void): void {
    this._onOpen = handler
  }

  onClose(handler: () => void): void {
    this._onClose = handler
  }

  // ── Conexión con protección CONNECTING y timer único cancelable ──

  connect(): void {
    // CONNECTING, OPEN y CLOSING siguen perteneciendo a la misma sesión.
    if (this.ws && this.ws.readyState !== WebSocket.CLOSED) return
    if (this.intentionallyDisconnected) return
    this.cancelReconnectTimer()

    if (this.ws) {
      this.detach(this.ws)
      this.ws = null
      this.opened = false
      this.activeSubscriptions.clear()
      this.notify(() => this._onClose?.())
      // Un callback puede conectar o destruir el cliente por su cuenta.
      if (this.ws || this.intentionallyDisconnected) return
    }

    this.reconnecting = true
    let socket: WebSocket
    try {
      socket = new WebSocket(this.url)
    } catch {
      console.warn("No se pudo crear el WebSocket de mercado")
      this.notify(() => this._onClose?.())
      this.scheduleReconnect()
      return
    }
    this.ws = socket
    this.opened = false
    this.activeSubscriptions.clear()
    const current = () => this.ws === socket && !this.intentionallyDisconnected

    socket.onopen = () => {
      if (!current() || this.opened || socket.readyState !== WebSocket.OPEN) return
      this.opened = true
      this.reconnecting = false
      this.reconnectDelay = 1000
      this.cancelReconnectTimer()
      this.notify(() => this._onOpen?.())
      for (const channel of this.subscriptions) {
        if (!current()) return
        this.sendSubscription(channel)
      }
    }

    socket.onmessage = (event) => {
      if (!current() || !this.isOpen || typeof event.data !== "string") return
      let parsed: unknown
      try {
        parsed = JSON.parse(event.data)
      } catch {
        return
      }
      if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) return
      const msg = parsed as MarketWSMessage
      if (typeof msg.type !== "string" || !msg.type.trim()) return
      if (msg.type === "subscribed" || msg.type === "pong") return
      this.notify(() => this._onMessage?.(msg))
    }

    socket.onerror = () => {
      if (current()) console.warn("Error del transporte WebSocket de mercado")
    }

    socket.onclose = () => {
      if (current()) this.loseSocket(socket, false)
    }
  }

  // ── Suscripciones recuperables ──

  subscribe(channel: string): void {
    this.subscriptions.add(channel)
    this.sendSubscription(channel)
  }

  unsubscribe(channel: string): void {
    const wanted = this.subscriptions.delete(channel)
    const active = this.activeSubscriptions.delete(channel)
    if (wanted || active) {
      this.send({ type: "unsubscribe", channel })
    }
  }

  // ── Mensaje protegido y send helper ──

  ping(): void {
    this.send({ type: "ping" })
  }

  private sendSubscription(channel: string): void {
    if (!this.activeSubscriptions.has(channel) && this.send({ type: "subscribe", channel })) {
      this.activeSubscriptions.add(channel)
    }
  }

  private send(payload: Record<string, unknown>): boolean {
    const socket = this.ws
    if (!socket || !this.isOpen) return false
    try {
      socket.send(JSON.stringify(payload))
      return true
    } catch {
      console.warn("No se pudo enviar un mensaje WebSocket de mercado")
      this.loseSocket(socket, true)
      return false
    }
  }

  private notify(callback: () => void): void {
    try {
      callback()
    } catch {
      console.warn("Error en un callback WebSocket de mercado")
    }
  }

  private detach(socket: WebSocket): void {
    socket.onopen = null
    socket.onmessage = null
    socket.onerror = null
    socket.onclose = null
  }

  private closeSocket(socket: WebSocket): void {
    if (socket.readyState === WebSocket.CLOSED) return
    try {
      socket.close()
    } catch {
      console.warn("No se pudo cerrar el WebSocket de mercado")
    }
  }

  private loseSocket(socket: WebSocket, closeTransport: boolean): void {
    if (this.ws !== socket) return
    this.ws = null
    this.opened = false
    this.activeSubscriptions.clear()
    this.detach(socket)
    if (closeTransport) this.closeSocket(socket)
    this.notify(() => this._onClose?.())
    this.scheduleReconnect()
  }

  private scheduleReconnect(): void {
    if (this.intentionallyDisconnected || this.ws || this.reconnectTimer !== null) return
    this.reconnecting = true
    const timer = setTimeout(() => {
      if (this.reconnectTimer !== timer) return
      this.reconnectTimer = null
      this.connect()
    }, this.reconnectDelay)
    this.reconnectTimer = timer
    this.reconnectDelay = Math.min(this.reconnectDelay * 2, this.maxReconnectDelay)
  }

  // ── Desmontaje definitivo: cero sockets/timers activos ──

  disconnect(): void {
    if (this.intentionallyDisconnected) return
    this.intentionallyDisconnected = true
    this.cancelReconnectTimer()
    const sock = this.ws
    this.ws = null
    this.opened = false
    this.activeSubscriptions.clear()
    this.subscriptions.clear()

    if (sock) {
      this.detach(sock)
      this.closeSocket(sock)
      this.notify(() => this._onClose?.())
    }

    this.reconnecting = false
    this._onMessage = null
    this._onOpen = null
    this._onClose = null
  }

  // Getter para tests
  get state(): number | null {
    return this.ws?.readyState ?? null
  }

  get isOpen(): boolean {
    return this.opened && !this.intentionallyDisconnected && this.ws?.readyState === WebSocket.OPEN
  }

  private cancelReconnectTimer(): void {
    if (this.reconnectTimer !== null) {
      clearTimeout(this.reconnectTimer)
      this.reconnectTimer = null
    }
  }
}
