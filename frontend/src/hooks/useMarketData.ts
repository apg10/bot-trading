// Hook para datos de mercado: carga histórico por HTTP y recibe streaming por WebSocket.

import { useEffect, useRef, useState, useCallback } from "react"
import type { CandleData, MarketStatus } from "../types/market"
import type { MarketWSMessage, CandleUpdate, GapEvent, StatusEvent } from "../api/market"
import { MarketWebSocket } from "../api/market"

const API_BASE = import.meta.env.VITE_API_URL || "http://localhost:8000"

interface UseMarketDataResult {
  candles: CandleData[]
  status: MarketStatus | null
  wsConnected: boolean
  loading: boolean
  error: string | null
}

export function useMarketData(
  symbol: string = "BTC/USDT",
): UseMarketDataResult {
  const [candles, setCandles] = useState<CandleData[]>([])
  const [status, setStatus] = useState<MarketStatus | null>(null)
  const [wsConnected, setWsConnected] = useState(false)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const wsRef = useRef<MarketWebSocket | null>(null)
  const candleHistoryRef = useRef<CandleData[]>([])

  // Cargar histórico por HTTP
  useEffect(() => {
    let cancelled = false

    async function loadHistorical() {
      try {
        setLoading(true)
        setError(null)

        const resp = await fetch(
          `${API_BASE}/api/market/candles?symbol=${encodeURIComponent(symbol)}&limit=200`,
        )
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`)

        const data: CandleData[] = await resp.json()

        if (!cancelled) {
          candleHistoryRef.current = data
          setCandles(data)
          setLoading(false)
        }
      } catch (e: unknown) {
        if (!cancelled) {
          setError(e instanceof Error ? e.message : "Unknown error")
          setLoading(false)
        }
      }
    }

    loadHistorical()

    return () => {
      cancelled = true
    }
  }, [symbol])

  // Conectar WebSocket para streaming en vivo
  useEffect(() => {
    const ws = new MarketWebSocket()
    wsRef.current = ws

    ws.onMessage((msg: MarketWSMessage) => {
      if (msg.type === "subscribed") return
      if (msg.type === "pong") return

      // Actualizar status
      if (msg.type === "status") {
        const s = msg as unknown as StatusEvent
        setStatus(s)
        setWsConnected(s.connected)
        return
      }

      // Actualizar candle actual o agregar nueva barra
      if (msg.type === "bar_updated" || msg.type === "bar_closed") {
        const update = msg as unknown as CandleUpdate
        const candle = update.candle

        setCandles(prev => {
          const hist = candleHistoryRef.current
          const lastCandle = hist[hist.length - 1]

          // Si es bar_closed y no coincide con la última vela del histórico, agregar nueva
          if (msg.type === "bar_closed" && (!lastCandle || lastCandle.open_time !== candle.open_time)) {
            const newHistory = [...hist, candle]
            candleHistoryRef.current = newHistory
            return newHistory.slice(-200) // Mantener últimas 200 velas
          }

          // Si es bar_updated o bar_closed con misma open_time, actualizar la última vela
          if (lastCandle && lastCandle.open_time === candle.open_time) {
            const updated = [...hist]
            updated[updated.length - 1] = candle
            return updated
          }

          // Si no hay coincidencia, agregar como nueva vela en formación
          const newHistory = [...hist, candle]
          candleHistoryRef.current = newHistory
          return newHistory.slice(-200)
        })
      }

      // Notificar gaps
      if (msg.type === "gap") {
        const gap = msg as unknown as GapEvent
        console.log(`[Market] Gap detected: ${gap.gap_ms}ms`)
      }
    })

    ws.connect()
    setWsConnected(true)

    // Suscribirse a canales
    setTimeout(() => {
      ws.subscribe("candles")
      ws.subscribe("status")
      ws.subscribe("gaps")
    }, 500)

    return () => {
      ws.disconnect()
      wsRef.current = null
      setWsConnected(false)
    }
  }, [symbol])

  // Ping cada 30s para mantener conexión
  useEffect(() => {
    const interval = setInterval(() => {
      wsRef.current?.ping()
    }, 30000)
    return () => clearInterval(interval)
  }, [])

  return { candles, status, wsConnected, loading, error }
}
