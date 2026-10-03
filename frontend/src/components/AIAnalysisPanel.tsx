// Panel de análisis con IA — visualización y control para Ollama.

import { useState, useEffect } from "react"

interface AIAnalysisPanelProps {
  symbol: string
  marketData?: Record<string, any>
}

interface AIResponse {
  analysis: string
  model_used: string
  duration_ms?: number
  available: boolean
}

// Hook para interactuar con la API de IA
function useAIAnalysis(symbol: string) {
  const [analysis, setAnalysis] = useState<AIResponse | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const analyze = async (marketData?: Record<string, any>) => {
    try {
      setLoading(true)
      setError(null)

      const resp = await fetch(
        `${import.meta.env.VITE_API_URL || "http://localhost:8000"}/api/bot/ai/analyze`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            symbol,
            timeframe: "15m",
            market_data: marketData || {},
          }),
        },
      )

      if (!resp.ok) throw new Error(`HTTP ${resp.status}`)
      const data = await resp.json()
      setAnalysis(data as AIResponse)
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Error desconocido")
    } finally {
      setLoading(false)
    }
  }

  return { analysis, loading, error, analyze }
}

// Componente: Estado de conexión con Ollama
function OllamaStatus({ available }: { available?: boolean }) {
  if (available === undefined) return null

  return (
    <div style={{
      display: "flex",
      alignItems: "center",
      gap: 8,
      padding: "6px 10px",
      borderRadius: 4,
      fontSize: 12,
      background: available ? "rgba(63, 185, 80, 0.1)" : "rgba(248, 81, 73, 0.1)",
      color: available ? "#3fb950" : "#f85149",
    }}>
      <span style={{ fontSize: 16 }}>{available ? "●" : "○"}</span>
      <span>{available ? "Ollama Conectado" : "Ollama Desconectado"}</span>
    </div>
  )
}

// Componente: Análisis con IA renderizado como markdown simple
function AnalysisContent({ text }: { text: string }) {
  // Renderizar texto con formato básico (negrita, listas, código)
  const lines = text.split("\n")
  let inList = false

  return (
    <div style={{ fontSize: 12, lineHeight: 1.6 }}>
      {lines.map((line, i) => {
        // Detectar encabezados
        if (line.startsWith("# ")) {
          return (
            <h4 key={i} style={{ margin: "8px 0 4px", fontSize: 14, fontWeight: 700 }}>
              {line.slice(2)}
            </h4>
          )
        }

        // Detectar sub-encabezados
        if (line.startsWith("## ")) {
          return (
            <h5 key={i} style={{ margin: "6px 0 3px", fontSize: 13, fontWeight: 600 }}>
              {line.slice(3)}
            </h5>
          )
        }

        // Detectar listas
        if (line.startsWith("- ") || line.startsWith("* ")) {
          if (!inList) {
            inList = true
            return (
              <ul key={i} style={{ margin: "4px 0", paddingLeft: 20 }}>
                <li>{line.slice(2)}</li>
              </ul>
            )
          }
          return (
            <li key={i} style={{ margin: "2px 0" }}>
              {line.slice(2)}
            </li>
          )
        }

        // Detectar fin de lista
        if (inList && line.trim() === "") {
          inList = false
          return <br key={i} />
        }

        // Detectar código inline
        if (line.includes("`")) {
          const parts = line.split("`")
          return (
            <div key={i} style={{ margin: "2px 0" }}>
              {parts.map((part, j) =>
                j % 2 === 1 ? (
                  <code key={j} style={{
                    background: "#161b22",
                    padding: "2px 4px",
                    borderRadius: 3,
                    fontSize: 11,
                    fontFamily: "monospace",
                  }}>
                    {part}
                  </code>
                ) : (
                  <span key={j}>{part}</span>
                ),
              )}
            </div>
          )
        }

        // Línea normal
        return (
          <div key={i} style={{ margin: "2px 0" }}>
            {line || "\u00A0"}
          </div>
        )
      })}
    </div>
  )
}

// Componente principal del panel de análisis con IA
export function AIAnalysisPanel({ symbol, marketData }: AIAnalysisPanelProps) {
  const { analysis, loading, error, analyze } = useAIAnalysis(symbol)

  // Auto-analizar cuando cambian los datos de mercado
  useEffect(() => {
    if (marketData) {
      analyze(marketData)
    }
  }, [symbol, marketData])

  return (
    <aside className="ai-analysis-panel">
      {/* Header con estado de Ollama */}
      <div style={{
        display: "flex",
        justifyContent: "space-between",
        alignItems: "center",
        marginBottom: 12,
      }}>
        <h3 style={{ fontSize: 14, fontWeight: 700, color: "#e6edf3" }}>
          🤖 Análisis IA (Ollama)
        </h3>
        <OllamaStatus available={analysis?.available} />
      </div>

      {/* Botón de análisis manual */}
      <button
        onClick={() => analyze(marketData)}
        disabled={loading}
        style={{
          width: "100%",
          padding: "8px 12px",
          marginBottom: 12,
          background: loading ? "#30363d" : "#238636",
          color: "white",
          border: "none",
          borderRadius: 6,
          cursor: loading ? "not-allowed" : "pointer",
          fontSize: 12,
          fontWeight: 600,
        }}
      >
        {loading ? "Analizando..." : "🔄 Analizar con IA"}
      </button>

      {/* Error */}
      {error && (
        <div style={{
          padding: "8px 12px",
          marginBottom: 12,
          background: "rgba(248, 81, 73, 0.1)",
          borderLeft: "3px solid #f85149",
          borderRadius: 4,
          fontSize: 11,
          color: "#f85149",
        }}>
          Error: {error}
        </div>
      )}

      {/* Análisis */}
      {analysis && (
        <div style={{
          padding: 12,
          background: "rgba(22, 27, 34, 0.5)",
          borderRadius: 6,
          border: "1px solid #30363d",
        }}>
          {/* Info del modelo */}
          <div style={{
            fontSize: 11,
            color: "#8b949e",
            marginBottom: 8,
            display: "flex",
            justifyContent: "space-between",
          }}>
            <span>Modelo: {analysis.model_used}</span>
            {analysis.duration_ms && (
              <span>Duración: {analysis.duration_ms}ms</span>
            )}
          </div>

          {/* Contenido del análisis */}
          <AnalysisContent text={analysis.analysis} />
        </div>
      )}

      {/* Placeholder cuando no hay análisis */}
      {!analysis && !loading && !error && (
        <div style={{
          padding: 20,
          textAlign: "center",
          color: "#8b949e",
          fontSize: 12,
        }}>
          <div style={{ fontSize: 32, marginBottom: 8 }}>🤖</div>
          <div>Haz clic en "Analizar con IA" para obtener análisis del mercado</div>
        </div>
      )}
    </aside>
  )
}
