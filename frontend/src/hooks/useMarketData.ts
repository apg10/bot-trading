// Hook para datos de mercado: carga histórico por HTTP y recibe streaming por WebSocket.
// HTTP y WS resuelven su origen mediante el cliente común de mercado.

import { useEffect, useRef, useState } from "react"
import type { CandleData, MarketCandleEvent, MarketDataSource, MarketStatus } from "../types/market"
import type { MarketWSMessage } from "../api/market"
import { getApiUrl, getCandles, MarketWebSocket } from "../api/market"
import { MAX_CANDLES, mergeHistoricalCandles, upsertCandle } from "../market/candle_store"

const MARKET_TRANSPORT_VERSION = "market_transport.v1"

type SequencedMessage = { stream_id?: unknown; sequence?: unknown; schema_version?: unknown }

function transportMetadata(msg: MarketWSMessage): { streamId: string; sequence: number } | null {
  const value = msg as SequencedMessage
  const hasMetadata = value.schema_version !== undefined
    || value.stream_id !== undefined || value.sequence !== undefined
  if (!hasMetadata) return null // Legacy messages predate the envelope.
  if (
    (value.schema_version !== undefined && value.schema_version !== MARKET_TRANSPORT_VERSION)
    || typeof value.stream_id !== "string" || !value.stream_id
    || typeof value.sequence !== "number" || !Number.isSafeInteger(value.sequence)
    || value.sequence < 0
  ) return null
  return { streamId: value.stream_id, sequence: value.sequence }
}

function hasKnownSchema(msg: MarketWSMessage): boolean {
  return msg.schema_version === undefined || msg.schema_version === MARKET_TRANSPORT_VERSION
}

// An origin without an interval has not identified its temporalidad yet. It
// is compatible with the first interval received from the stream.
function compatibleInterval(known: string | null, received: string | null): boolean {
  return known === null || received === null || known === received
}

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
  const transportRef = useRef<{ streamId: string; sequence: number } | null>(null)
  const streamEpochRef = useRef(0)
  const snapshotReceivedRef = useRef(false)
  // Identifica también la petición de recuperación que está en vuelo.  El
  // epoch invalida recuperaciones de otro stream, mientras que este token
  // evita que una respuesta antigua de la misma identidad vuelva a publicar
  // datos después de una nueva recuperación.
  const recoveryTokenRef = useRef<symbol | null>(null)
  const awaitingResyncRef = useRef(false)
  const resyncRef = useRef<Set<string>>(new Set())
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
    const initialStreamEpoch = streamEpochRef.current

    async function loadHistorical() {
      try {
        setLoading(true)
        setError(null)
        publishCandles([])
        transportRef.current = null
        awaitingResyncRef.current = false
        resyncRef.current.clear()
        originRef.current = { dataSource: "unknown", interval: null }
        setDataSource("unknown")
        setCandleInterval(null)
        setStatus(null)
        setWsConnected(false)
        setDegraded(false)

        const data = await getCandles(symbol, MAX_CANDLES)

        if (!cancelled) {
          // El histórico inicial pertenece al stream que estaba activo al
          // iniciar la petición. Si el WS ya cambió de dueño, no mezclar su
          // respuesta tardía con el buffer actual.
          // A stream snapshot is authoritative even when the HTTP response
          // does not carry an epoch of its own.  In particular, do not let a
          // late initial response put the fixture back over a newer stream.
          const responseStream = data.stream_id ?? null
          if (streamEpochRef.current !== initialStreamEpoch
            || (responseStream !== null
              && responseStream !== transportRef.current?.streamId)
            || (snapshotReceivedRef.current && !awaitingResyncRef.current)) {
            // Una respuesta inicial obsoleta no modifica el buffer actual.
            setLoading(false)
            return
          }
          // Una fixture tardía nunca reemplaza datos ya recibidos del motor.
          if (!(data.data_source === "TEST_ONLY" && originRef.current.dataSource === "market_engine")) {
            const origin = originRef.current
            if (
              origin.dataSource === "market_engine" && data.data_source === "market_engine"
              && !compatibleInterval(origin.interval, data.interval)
            ) {
              markDegraded("Histórico de otro intervalo; recuperación de datos pendiente")
            } else {
              const sameOrigin = origin.dataSource === data.data_source
                && compatibleInterval(origin.interval, data.interval)
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

    snapshotReceivedRef.current = false
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

    function resetForStream(streamId: string): void {
      if (transportRef.current?.streamId === streamId) return
      streamEpochRef.current += 1
      recoveryTokenRef.current = null
      transportRef.current = { streamId, sequence: -1 }
      awaitingResyncRef.current = true
      // A stream id is the ownership boundary of the candle buffer.  Never
      // retain historical or live candles from the previous owner.
      publishCandles([])
      originRef.current = { dataSource: "unknown", interval: null }
      setDataSource("unknown")
      setCandleInterval(null)
      setDegraded(true)
    }

    function acceptSequence(
      msg: MarketWSMessage,
      kind: "snapshot" | "resync_required" | "bar" | "status",
    ): { metadata: { streamId: string; sequence: number } | null; duplicate: boolean; rejected: boolean } {
      const metadata = transportMetadata(msg)
      if (!metadata) {
        // An explicitly present but malformed envelope is not a legacy event.
        const hasEnvelope = msg.schema_version !== undefined
          || msg.stream_id !== undefined || msg.sequence !== undefined
        if (hasEnvelope) return { metadata: null, duplicate: false, rejected: true }
        return { metadata: null, duplicate: false, rejected: false }
      }
      if (transportRef.current?.streamId !== metadata.streamId) resetForStream(metadata.streamId)
      const previous = transportRef.current
      const wasAwaitingResync = awaitingResyncRef.current
      let sequenceGap = false
      if (previous && previous.sequence >= 0) {
        if (metadata.sequence < previous.sequence) {
          awaitingResyncRef.current = true
          markDegraded("Secuencia de mercado regresiva; recuperación de datos pendiente")
          return { metadata, duplicate: false, rejected: true }
        }
        if (metadata.sequence > previous.sequence + 1 && kind !== "snapshot") {
          sequenceGap = true
          awaitingResyncRef.current = true
          markDegraded("Hueco de secuencia en el mercado; recuperación de datos pendiente")
          // Una barra recibida durante la recuperación aún aporta datos
          // válidos al buffer. El hueco queda marcado para el backfill, pero
          // no se descarta la actualización por ello.
          if (kind !== "bar") return { metadata, duplicate: false, rejected: true }
        }
        if (metadata.sequence === previous.sequence && kind === "bar") {
          return { metadata, duplicate: true, rejected: false }
        }
      }
      // A snapshot is authoritative, and a resync signal is meaningful even
      // when its sequence repeats the last published value.
      transportRef.current = metadata
      if (kind !== "resync_required"
        && (kind === "snapshot" || (!wasAwaitingResync && !sequenceGap))) {
        awaitingResyncRef.current = false
      }
      return { metadata, duplicate: false, rejected: false }
    }

    async function recoverRange(
      signal: MarketWSMessage,
      streamId: string,
      streamEpoch: number,
    ): Promise<void> {
      const reason = signal.reason === "gap" || signal.reason === "overflow" ? signal.reason : "overflow"
      const lastSequence = typeof signal.last_sequence === "number" && Number.isSafeInteger(signal.last_sequence)
        ? signal.last_sequence : 0
      const key = `${streamId}:${reason}:${lastSequence}`
      if (resyncRef.current.has(key)) return
      resyncRef.current.add(key)

      const current = candleHistoryRef.current
      const start = current.length > 0 ? current[0].open_time : 0
      const end = current.length > 0
        ? current[current.length - 1].close_time + 1 : Math.max(Date.now(), start + 1)
      const params = new URLSearchParams({
        symbol, limit: String(MAX_CANDLES), start_time: String(start), end_time: String(end),
      })
      const recoveryToken = Symbol("market-recovery")
      recoveryTokenRef.current = recoveryToken
      try {
        const response = await fetch(getApiUrl(`/api/market/candles?${params.toString()}`))
        if (!response.ok) throw new Error(`HTTP ${response.status}`)
        const data = await response.json() as Record<string, unknown>
        // Recovery belongs to the stream that requested it, not merely to the
        // request's completion. A newer stream may have replaced the buffer
        // while this HTTP request was in flight.
        const responseStream = typeof data?.stream_id === "string" && data.stream_id
          ? data.stream_id : null
        if (cancelled || recoveryTokenRef.current !== recoveryToken
          || streamEpochRef.current !== streamEpoch
          || transportRef.current?.streamId !== streamId
          || responseStream !== streamId) return
        if (
          !data || Array.isArray(data)
          || (data.schema_version !== undefined && data.schema_version !== MARKET_TRANSPORT_VERSION)
          || data.symbol !== symbol || typeof data.interval !== "string" || !data.interval
          || (data.data_source !== "TEST_ONLY" && data.data_source !== "market_engine")
          || !Array.isArray(data.candles)
        ) throw new Error("Respuesta de recuperación incompatible")
        const next = mergeHistoricalCandles(candleHistoryRef.current, data.candles as CandleData[])
        publishCandles(next)
        // Recovery is requested by the live stream, so its result must not
        // downgrade ownership to TEST_ONLY.  Otherwise the next live bar
        // would see a different origin and replace the recovered buffer.
        originRef.current = { dataSource: "market_engine", interval: data.interval }
        setDataSource(originRef.current.dataSource)
        setCandleInterval(data.interval)
        if (typeof data.sequence === "number" && Number.isSafeInteger(data.sequence) && data.sequence >= 0
          && transportRef.current && data.sequence > transportRef.current.sequence) {
          transportRef.current = { streamId: transportRef.current.streamId, sequence: data.sequence }
        }
        if (data.complete === false) {
          awaitingResyncRef.current = true
          markDegraded("Recuperación de mercado incompleta")
        } else {
          awaitingResyncRef.current = false
          setDegraded(false)
          setError(null)
        }
      } catch (e: unknown) {
        if (!cancelled && recoveryTokenRef.current === recoveryToken
          && streamEpochRef.current === streamEpoch
          && transportRef.current?.streamId === streamId) {
          markDegraded(e instanceof Error ? e.message : "Recuperación de mercado no disponible")
        }
      }
    }

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
      if (!hasKnownSchema(msg)) {
        markDegraded("Versión de transporte de mercado desconocida")
        return
      }
      if (msg.type === "subscribed") return
      if (msg.type === "pong") return

      if (msg.type === "snapshot") {
        const snapshotInterval = msg.interval
        if (!Array.isArray(msg.candles) || typeof msg.complete !== "boolean"
          || (snapshotInterval !== undefined && snapshotInterval !== null
            && (typeof snapshotInterval !== "string" || !snapshotInterval))) {
          markDegraded("Snapshot de mercado inválido")
          return
        }
        let next: CandleData[]
        try {
          next = mergeHistoricalCandles([], msg.candles as CandleData[])
        } catch (e: unknown) {
          markDegraded(e instanceof Error ? e.message : "Snapshot de mercado inválido")
          return
        }
        const result = acceptSequence(msg, "snapshot")
        if (result.rejected || (msg.schema_version !== undefined && !result.metadata)) {
          markDegraded("Snapshot de mercado con envelope inválido")
          return
        }
        snapshotReceivedRef.current = true
        publishCandles(next)
        awaitingResyncRef.current = !msg.complete
        originRef.current = {
          dataSource: "market_engine",
          interval: typeof snapshotInterval === "string" ? snapshotInterval : null,
        }
        setDataSource("market_engine")
        setCandleInterval(typeof snapshotInterval === "string" ? snapshotInterval : null)
        setLoading(false)
        if (msg.complete) {
          setDegraded(false)
          setError(null)
        } else {
          markDegraded("Snapshot de mercado incompleto; recuperación pendiente")
        }
        return
      }

      if (msg.type === "resync_required") {
        const result = acceptSequence(msg, "resync_required")
        if (result.rejected || !result.metadata || msg.recovery_required !== true
          || (msg.reason !== "gap" && msg.reason !== "overflow")) {
          markDegraded("Señal de recuperación de mercado inválida")
          return
        }
        markDegraded("Recuperación de mercado requerida")
        void recoverRange(msg, result.metadata.streamId, streamEpochRef.current)
        return
      }

      const sequencing = acceptSequence(msg, msg.type === "status" ? "status" : "bar")
      if (sequencing.rejected || (msg.schema_version !== undefined && !sequencing.metadata)) {
        markDegraded("Mensaje de mercado con envelope inválido")
        return
      }
      if (sequencing.duplicate && (msg.type === "bar_updated" || msg.type === "bar_closed")) return

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
          && !compatibleInterval(originRef.current.interval, update.interval)
        ) {
          markDegraded("Evento de otro intervalo; recuperación de datos pendiente")
          return
        }
        const sameOrigin = originRef.current.dataSource === update.data_source
          && compatibleInterval(originRef.current.interval, update.interval)
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
        if (!sameOrigin || originRef.current.interval === null) {
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
        if (originRef.current.dataSource === "market_engine"
          && originRef.current.interval !== null
          && originRef.current.interval !== msg.interval) return
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
