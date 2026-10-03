// Panel de análisis técnico con señales de Keltner, MACD y FYL.

import { useEffect, useState } from "react"
import type { CandleData } from "../types/market"

interface AnalysisPanelProps {
  candles: CandleData[]
  symbol: string
  loading: boolean
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
  keltner: { points: KeltnerPoint[] }
  macd: { points: MACDPoint[] }
  fyl: {
    pivots: PivotPoint[]
    zones: ConsolidationZone[]
    annotations: Array<{ id: string; annotation_type: string; content: string }>
  }
}

// Hook para obtener datos de análisis desde el backend
function useAnalysis(symbol: string, candles: CandleData[]) {
  const [analysis, setAnalysis] = useState<AnalysisData | null>(null)
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    if (candles.length < 50) return

    const fetchAnalysis = async () => {
      try {
        setLoading(true)
        const resp = await fetch(
          `${import.meta.env.VITE_API_URL || "http://localhost:8000"}/api/analysis`,
          {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              symbol,
              timeframe: "15m",
              candles_count: Math.min(candles.length, 200),
            }),
          },
        )

        if (!resp.ok) throw new Error(`HTTP ${resp.status}`)
        const data = await resp.json()
        setAnalysis(data as AnalysisData)
      } catch (e) {
        console.error("Error fetching analysis:", e)
      } finally {
        setLoading(false)
      }
    }

    fetchAnalysis()
  }, [symbol, candles.length])

  return { analysis, loading }
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
        {bullishCrossover ? "📈 SEÑAL COMPRA" : "📉 SEÑAL VENTA"}
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
export function AnalysisPanel({ candles, symbol, loading }: AnalysisPanelProps) {
  const { analysis, loading: analysisLoading } = useAnalysis(symbol, candles)

  return (
    <aside className="analysis-panel">
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
          </ul>
        </div>
      </div>

      {/* Análisis técnico */}
      {loading || analysisLoading ? (
        <div style={{ padding: 12, fontSize: 12, color: "#8b949e" }}>
          Calculando indicadores...
        </div>
      ) : analysis ? (
        <>
          {/* Señales MACD */}
          <div className="panel-section">
            <h3>Señales MACD</h3>
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
