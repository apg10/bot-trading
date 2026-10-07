// Hook para datos de mercado: carga histórico por HTTP y recibe streaming por WebSocket.
// HTTP y WS resuelven su origen mediante el cliente común de mercado.

import { useEffect, useRef, useState } from "react"
import type { CandleData, MarketCandleEvent, MarketDataSource, MarketStatus } from "../types/market"
import type { MarketWSMessage } from "../api/market"
import { getCandles, MarketWebSocket } from "../api/market"
import { MAX_CANDLES, mergeHistoricalCandles, upsertCandle } from "../market/candle_store"

interface UseMarketDataResult {
  candles: CandleData[]
  status: MarketStatus | null
  wsConnected: boolean
  loading: boolean
  error: string | null
  dataSource: MarketDataSource
  interval: string | null
  degraded: boolean
}

export function useMarketData(
  symbol: string = "BTC/USDT",
): UseMarketDataResult {
  const [candles, setCandles] = useState<CandleData[]>([])
  const [status, setStatus] = useState<MarketStatus | null>(null)
  const [wsConnected, setWsConnected] = useState(false)
  const [socketOpen, setSocketOpen] = useState(false)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [dataSource, setDataSource] = useState<MarketDataSource>("unknown")
  const [candleInterval, setCandleInterval] = useState<string | null>(null)
  const [degraded, setDegraded] = useState(false)

  const wsRef = useRef<MarketWebSocket | null>(null)
  const candleHistoryRef = useRef<CandleData[]>([])
  const originRef = useRef<{ dataSource: MarketDataSource; interval: string | null }>({
    dataSource: "unknown", interval: null,
  })

  // Escribir la referencia antes de publicar, nunca dentro de un updater React.
  function publishCandles(next: CandleData[]): void {
    candleHistoryRef.current = next
    setCandles(next)
  }

  function markDegraded(message: string): void {
    // Latch por símbolo: ni transporte abierto, status ni barras prueban backfill.
    // La recuperación verificada corresponde a una tarea posterior.
    setDegraded(true)
    setError(message)
    setWsConnected(false)
  }

  // Cargar histórico por HTTP
  useEffect(() => {
    let cancelled = false

    async function loadHistorical() {
      try {
        setLoading(true)
        setError(null)
        publishCandles([])
        originRef.current = { dataSource: "unknown", interval: null }
        setDataSource("unknown")
        setCandleInterval(null)
        setStatus(null)
        setWsConnected(false)
        setDegraded(false)

        const data = await getCandles(symbol, MAX_CANDLES)

        if (!cancelled) {
          // Una fixture tardía nunca reemplaza datos ya recibidos del motor.
          if (!(data.data_source === "TEST_ONLY" && originRef.current.dataSource === "market_engine")) {
            const origin = originRef.current
            if (
              origin.dataSource === "market_engine" && data.data_source === "market_engine"
              && origin.interval !== data.interval
            ) {
              markDegraded("Histórico de otro intervalo; recuperación de datos pendiente")
            } else {
              const sameOrigin = origin.dataSource === data.data_source && origin.interval === data.interval
              const next = mergeHistoricalCandles(sameOrigin ? candleHistoryRef.current : [], data.candles)
              originRef.current = { dataSource: data.data_source, interval: data.interval }
              publishCandles(next)
              setDataSource(data.data_source)
              setCandleInterval(data.interval)
            }
          }
          setLoading(false)
        }
      } catch (e: unknown) {
        if (!cancelled) {
          markDegraded(e instanceof Error ? e.message : "Histórico inválido o no disponible")
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
    let cancelled = false
    const ws = new MarketWebSocket()
    wsRef.current = ws

    // Informar al hook cuando el socket se abre realmente (no solo conecta).
    ws.onOpen(() => {
      if (cancelled) return
      setSocketOpen(true)
      // Abrir el enlace con el backend no acredita la conexión de Binance.
      // Esperar un estado nuevo, no reutilizar el recibido antes del corte.
      setStatus(null)
      setWsConnected(false)
    })

    ws.onClose(() => {
      if (cancelled) return
      setSocketOpen(false)
      setStatus(null)
      setWsConnected(false)
    })

    ws.onMessage((msg: MarketWSMessage) => {
      if (cancelled) return
      if (msg.type === "subscribed") return
      if (msg.type === "pong") return

      // Actualizar status
      if (msg.type === "status") {
        const s = msg as unknown as MarketStatus
        if (s.symbol !== symbol) return
        if (s.data_source !== "market_engine" && s.data_source !== "unavailable") {
          setWsConnected(false)
          return
        }
        setStatus(s)
        setWsConnected(ws.isOpen && s.data_source === "market_engine" && s.connected === true)
        return
      }

      // Actualizar candle actual o agregar nueva barra
      if (msg.type === "bar_updated" || msg.type === "bar_closed") {
        const update = msg as unknown as MarketCandleEvent
        if (
          update.symbol !== symbol || update.data_source !== "market_engine"
          || typeof update.interval !== "string" || !update.interval
        ) return
        if (
          originRef.current.dataSource === "market_engine"
          && originRef.current.interval !== update.interval
        ) {
          markDegraded("Evento de otro intervalo; recuperación de datos pendiente")
          return
        }
        const sameOrigin = originRef.current.dataSource === update.data_source
          && originRef.current.interval === update.interval
        let next: CandleData[]
        try {
          if (typeof update.candle?.is_closed !== "boolean"
            || update.candle.is_closed !== (msg.type === "bar_closed")) {
            throw new Error("Evento de vela con confirmación de cierre incoherente")
          }
          next = upsertCandle(sameOrigin ? candleHistoryRef.current : [], update.candle)
        } catch (e: unknown) {
          markDegraded(e instanceof Error ? e.message : "Vela inválida recibida")
          return
        }
        if (!sameOrigin) {
          // No mezclar la fixture o una temporalidad distinta con el stream.
          originRef.current = { dataSource: update.data_source, interval: update.interval }
          setDataSource(update.data_source)
          setCandleInterval(update.interval)
        }
        publishCandles(next)
        return
      }

      // Notificar gaps
      if (msg.type === "gap") {
        if (
          msg.symbol !== symbol || msg.data_source !== "market_engine"
          || typeof msg.interval !== "string" || !msg.interval
          || typeof msg.gap_ms !== "number" || !Number.isFinite(msg.gap_ms) || msg.gap_ms <= 0
        ) return
        if (originRef.current.dataSource === "market_engine" && originRef.current.interval !== msg.interval) return
        markDegraded("Hueco detectado en el mercado; recuperación de datos pendiente")
      }
    })

    // El cliente conserva y restaura estas suscripciones una vez por sesión.
    for (const channel of ["candles", "updates", "status", "gaps"]) {
      ws.subscribe(channel)
    }
    ws.connect()

    return () => {
      cancelled = true
      ws.disconnect()
      wsRef.current = null
      setSocketOpen(false)
      setStatus(null)
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

  return {
    candles, status,
    wsConnected: dataSource === "market_engine" && socketOpen && wsConnected && !degraded,
    loading, error, dataSource, interval: candleInterval, degraded,
  }
}
