// Panel de análisis técnico con señales de Keltner, MACD y FYL.

import { useEffect, useRef, useState } from "react"
import { getApiUrl } from "../api/market"
import { AnalysisRequestTracker, createAnalysisSnapshotKey, type AnalysisRequestTicket } from "../analysis/request_tracker"
import { MAX_CANDLES } from "../market/candle_store"
import type { CandleData, MarketDataSource } from "../types/market"

interface AnalysisPanelProps {
  candles: CandleData[]
  symbol: string
  loading: boolean
  dataSource: MarketDataSource
  interval: string | null
}

// Tipos para las respuestas de la API de análisis
interface KeltnerPoint {
  time_ms: number
  ema: number
  upper: number
  lower: number
  atr: number
}

interface MACDPoint {
  time_ms: number
  macd_line: number
  signal_line: number
  histogram: number
}

interface PivotPoint {
  id: string
  time_ms: number
  price: number
  pivot_type: "high" | "low"
  strength: number
}

interface ConsolidationZone {
  id: string
  start_time_ms: number
  end_time_ms: number
  origin: "high" | "low"
  low: number
  high: number
  contacts: number
  status: "forming" | "complete" | "invalidated"
}

interface AnalysisData {
  symbol: string
  timeframe: string
  data_source: "provided" | "synthetic_test"
  keltner: { points: KeltnerPoint[] }
  macd: { points: MACDPoint[] }
  fyl: {
    pivots: PivotPoint[]
    zones: ConsolidationZone[]
    annotations: Array<{ id: string; annotation_type: string; content: string }>
  }
}

function object(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value)
}

function numericFields(value: unknown, fields: string[]): boolean {
  return object(value) && fields.every(field => typeof value[field] === "number" && Number.isFinite(value[field]))
}

function stringFields(value: unknown, fields: string[]): boolean {
  return object(value) && fields.every(field => typeof value[field] === "string")
}

function checkedAnalysis(value: unknown, symbol: string, interval: string): AnalysisData {
  if (
    !object(value) || value.symbol !== symbol || value.timeframe !== interval
    || (value.data_source !== "provided" && value.data_source !== "synthetic_test")
    || !object(value.keltner) || !Array.isArray(value.keltner.points)
    || !object(value.macd) || !Array.isArray(value.macd.points)
    || !object(value.fyl) || !Array.isArray(value.fyl.pivots)
    || !Array.isArray(value.fyl.zones) || !Array.isArray(value.fyl.annotations)
  ) throw new Error("Respuesta de análisis inválida o incompatible con el snapshot solicitado")

  if (
    !value.keltner.points.every(p => numericFields(p, ["time_ms", "ema", "upper", "lower", "atr"]))
    || !value.macd.points.every(p => numericFields(p, ["time_ms", "macd_line", "signal_line", "histogram"]))
    || !value.fyl.pivots.every(p => numericFields(p, ["time_ms", "price", "strength"])
      && stringFields(p, ["id", "pivot_type"]) && (p.pivot_type === "high" || p.pivot_type === "low"))
    || !value.fyl.zones.every(z => numericFields(z, ["start_time_ms", "end_time_ms", "low", "high", "contacts"])
      && stringFields(z, ["id", "origin", "status"])
      && (z.origin === "high" || z.origin === "low")
      && ["forming", "complete", "invalidated"].includes(z.status))
    || !value.fyl.annotations.every(a => stringFields(a, ["id", "annotation_type", "content"]))
  ) throw new Error("Respuesta de análisis con puntos o anotaciones inválidos")
  return value as unknown as AnalysisData
}

// Hook para obtener datos de análisis desde el backend.
// "provided" solo significa que el cliente envió velas, no que sean reales.
// Conservar la procedencia del input incluso cuando la API devuelve "provided".
function useAnalysis(symbol: string, candles: CandleData[], dataSource: MarketDataSource, interval: string | null, inputLoading: boolean) {
  const [record, setRecord] = useState<{
    data: AnalysisData
    symbol: string
    interval: string
    inputSource: MarketDataSource
    snapshotKey: string
    ticket: AnalysisRequestTicket
  } | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<{ snapshotKey: string; message: string } | null>(null)
  const trackerRef = useRef<AnalysisRequestTracker | null>(null)
  if (trackerRef.current === null) trackerRef.current = new AnalysisRequestTracker()
  const tracker = trackerRef.current
  const snapshotKey = createAnalysisSnapshotKey({ symbol, interval, dataSource }, candles)
  const eligible = !inputLoading && candles.length >= 50 && dataSource !== "unknown"
    && !!interval && interval !== "unknown"
  const latestRef = useRef({ snapshotKey, eligible })
  // Una respuesta entre render y cleanup tampoco puede publicar un snapshot viejo.
  latestRef.current = { snapshotKey, eligible }

  useEffect(() => {
    if (!eligible || !interval) {
      tracker.invalidate()
      setLoading(false)
      return
    }
    const timeframe = interval
    const ticket = tracker.begin(snapshotKey)
    const current = () => latestRef.current.eligible && tracker.isCurrent(ticket, latestRef.current.snapshotKey)

    const fetchAnalysis = async () => {
      try {
        setLoading(true)
        setError(null)
        // Enviar el intervalo real del histórico, no asumir 15m para una fixture 1m.
        const payload = {
          symbol,
          timeframe,
          candles_count: Math.min(candles.length, MAX_CANDLES),
          candles: candles.slice(-MAX_CANDLES).map(c => ({
            open_time: c.open_time,
            open: c.open,
            high: c.high,
            low: c.low,
            close: c.close,
            volume: c.volume,
          })),
        }

        const resp = await fetch(
          getApiUrl("/api/analysis"),
          {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload),
            signal: ticket.signal,
          },
        )

        if (!resp.ok) throw new Error(`HTTP ${resp.status}`)
        const data = await resp.json()
        if (current()) {
          setRecord({
            data: checkedAnalysis(data, symbol, timeframe), symbol, interval: timeframe,
            inputSource: dataSource, snapshotKey, ticket,
          })
        }
      } catch (e) {
        if (current()) {
          setError({ snapshotKey, message: e instanceof Error ? e.message : "Error de análisis técnico" })
        }
      } finally {
        if (current()) setLoading(false)
      }
    }

    fetchAnalysis()
    return () => { tracker.invalidate() }
  }, [snapshotKey, eligible])

  const currentError = eligible && error?.snapshotKey === snapshotKey ? error.message : null
  const visible = eligible && !currentError && record?.snapshotKey === snapshotKey
    && tracker.isCurrent(record.ticket, snapshotKey) ? record : null
  return {
    analysis: visible?.data ?? null,
    analysisSource: visible?.data.data_source === "synthetic_test" ? "TEST_ONLY" : visible?.inputSource,
    loading,
    error: currentError,
    stale: record !== null && visible === null,
    pending: eligible && visible === null && currentError === null,
    provisional: candles.slice(-MAX_CANDLES).some(c => !c.is_closed),
  }
}

// Componente: Señal de trading basada en cruce de MACD
function MACDSignal({ macdPoints }: { macdPoints: MACDPoint[] }) {
  if (macdPoints.length < 2) return null

  const last = macdPoints[macdPoints.length - 1]
  const prev = macdPoints[macdPoints.length - 2]

  // Detectar cruce: MACD cruza por encima o por debajo de signal line
  const bullishCrossover = prev.macd_line <= prev.signal_line && last.macd_line > last.signal_line
  const bearishCrossover = prev.macd_line >= prev.signal_line && last.macd_line < last.signal_line

  if (!bullishCrossover && !bearishCrossover) return null

  return (
    <div style={{
      padding: "8px 12px",
      borderRadius: 6,
      fontSize: 12,
      fontWeight: 600,
      marginBottom: 8,
      background: bullishCrossover ? "rgba(63, 185, 80, 0.1)" : "rgba(248, 81, 73, 0.1)",
      borderLeft: `3px solid ${bullishCrossover ? "#3fb950" : "#f85149"}`,
    }}>
      <span style={{ color: bullishCrossover ? "#3fb950" : "#f85149" }}>
        {bullishCrossover ? "📈 Cruce alcista — no ejecutable" : "📉 Cruce bajista — no ejecutable"}
      </span>
      <div style={{ fontSize: 11, color: "#8b949e", marginTop: 2 }}>
        MACD cruzó {bullishCrossover ? "por encima" : "por debajo"} de la línea de señal
      </div>
    </div>
  )
}

// Componente: Zona de consolidación FYL
function ConsolidationZones({ zones }: { zones: ConsolidationZone[] }) {
  if (zones.length === 0) return null

  return (
    <div style={{ marginBottom: 8 }}>
      <h4 style={{ fontSize: 12, fontWeight: 600, color: "#e6edf3", marginBottom: 4 }}>
        Zonas de Consolidación ({zones.length})
      </h4>
      {zones.slice(-3).map(zone => (
        <div key={zone.id} style={{
          padding: "6px 10px",
          fontSize: 11,
          color: "#8b949e",
          background: "rgba(22, 27, 34, 0.5)",
          borderRadius: 4,
          marginBottom: 2,
        }}>
          <span style={{ color: zone.origin === "high" ? "#f0883e" : "#3fb950" }}>
            {zone.origin.toUpperCase()}
          </span>
          {" · "}
          ${zone.low.toLocaleString()} — ${zone.high.toLocaleString()}
          {" · "}
          {zone.contacts} contactos
        </div>
      ))}
    </div>
  )
}

// Componente: Pivotes FYL
function PivotList({ pivots }: { pivots: PivotPoint[] }) {
  if (pivots.length === 0) return null

  return (
    <div style={{ marginBottom: 8 }}>
      <h4 style={{ fontSize: 12, fontWeight: 600, color: "#e6edf3", marginBottom: 4 }}>
        Pivotes Recientes ({pivots.length})
      </h4>
      {pivots.slice(-5).map(pivot => (
        <div key={pivot.id} style={{
          padding: "4px 10px",
          fontSize: 11,
          color: "#8b949e",
          display: "flex",
          justifyContent: "space-between",
        }}>
          <span>
            <strong style={{ color: pivot.pivot_type === "high" ? "#f0883e" : "#3fb950" }}>
              {pivot.pivot_type.toUpperCase()}
            </strong>
          </span>
          <span>${pivot.price.toLocaleString()}</span>
          <span>Fuerza: {(pivot.strength * 100).toFixed(1)}%</span>
        </div>
      ))}
    </div>
  )
}

// Componente principal del panel de análisis
export function AnalysisPanel({ candles, symbol, loading, dataSource, interval }: AnalysisPanelProps) {
  const { analysis, analysisSource, loading: analysisLoading, error, stale, pending, provisional } = useAnalysis(symbol, candles, dataSource, interval, loading)
  const testOnly = dataSource === "TEST_ONLY" || analysisSource === "TEST_ONLY"

  return (
    <aside className="analysis-panel">
      <div className="panel-section" style={{ fontSize: 12, color: testOnly ? "#f0883e" : "#8b949e" }}>
        <strong>{testOnly ? "TEST_ONLY · datos sintéticos · no operativo"
          : dataSource === "market_engine" ? "Datos del motor · frescura no validada"
          : "Procedencia sin verificar"}</strong>
        <div>Análisis descriptivo; no es una orden ni un fill.</div>
        <div>{provisional ? "Snapshot provisional: incluye velas en formación"
          : "Snapshot de velas cerradas; estrategia todavía no validada"}</div>
        <div>Intervalo: {interval && interval !== "unknown" ? interval : "sin verificar"}</div>
      </div>
      {/* Reglas pendientes */}
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
            <li>T1/V1/V2 deshabilitados: método pendiente de definición</li>
          </ul>
        </div>
      </div>

      {/* Análisis técnico */}
      {error ? (
        <div role="alert" style={{ padding: 12, fontSize: 12, color: "#f85149" }}>
          Error de análisis técnico: {error}. No hay un resultado vigente para este snapshot.
        </div>
      ) : loading || analysisLoading || pending || stale ? (
        <div style={{ padding: 12, fontSize: 12, color: "#8b949e" }}>
          {stale ? "Análisis anterior caducado; esperando el snapshot actual..."
            : "Calculando indicadores del snapshot actual..."}
        </div>
      ) : analysis ? (
        <>
          {/* Señales MACD */}
          <div className="panel-section">
            <h3>Cruces MACD convencionales · no es MACD BB</h3>
            <MACDSignal macdPoints={analysis.macd.points} />
          </div>

          {/* Canales Keltner */}
          <div className="panel-section">
            <h3>Keltner Channels</h3>
            {analysis.keltner.points.length > 0 && (
              <div style={{ fontSize: 11, color: "#8b949e" }}>
                <div>EMA(20): ${analysis.keltner.points[analysis.keltner.points.length - 1].ema.toLocaleString()}</div>
                <div>
                  Superior: ${analysis.keltner.points[analysis.keltner.points.length - 1].upper.toLocaleString()}
                </div>
                <div>
                  Inferior: ${analysis.keltner.points[analysis.keltner.points.length - 1].lower.toLocaleString()}
                </div>
                <div style={{ marginTop: 4, fontStyle: "italic" }}>
                  ATR(14): ${analysis.keltner.points[analysis.keltner.points.length - 1].atr.toLocaleString()}
                </div>
              </div>
            )}
          </div>

          {/* Zonas de consolidación */}
          <div className="panel-section">
            <ConsolidationZones zones={analysis.fyl.zones} />
          </div>

          {/* Pivotes */}
          <div className="panel-section">
            <PivotList pivots={analysis.fyl.pivots} />
          </div>

          {/* Anotaciones */}
          {analysis.fyl.annotations.length > 0 && (
            <div className="panel-section">
              <h3>Anotaciones ({analysis.fyl.annotations.length})</h3>
              {analysis.fyl.annotations.map(ann => (
                <div key={ann.id} className="card" style={{ marginBottom: 4 }}>
                  <div className="card-title">{ann.annotation_type.replace("_", " ").toUpperCase()}</div>
                  <div className="card-detail">{ann.content}</div>
                </div>
              ))}
            </div>
          )}
        </>
      ) : (
        <div style={{ padding: 12, fontSize: 12, color: "#8b949e" }}>
          Sin datos suficientes para análisis (mínimo 50 velas)
        </div>
      )}
    </aside>
  )
}
