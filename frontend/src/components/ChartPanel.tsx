// Panel de gráfico con velas (Lightweight Charts) + indicadores overlay.

import { useEffect, useRef } from "react"
import { createChart, ColorType, CandlestickSeries, type IChartApi } from "lightweight-charts"
import type { CandleData } from "../types/market"

interface ChartPanelProps {
  candles: CandleData[]
  symbol: string
  loading: boolean
  wsConnected: boolean
}

export function ChartPanel({ candles, symbol, loading, wsConnected }: ChartPanelProps) {
  const chartContainerRef = useRef<HTMLDivElement>(null)
  const chartRef = useRef<IChartApi | null>(null)
  const candleSeriesRef = useRef<any>(null)

  // Inicializar gráfico
  useEffect(() => {
    if (!chartContainerRef.current) return

    const chart = createChart(chartContainerRef.current, {
      layout: {
        background: { type: ColorType.Solid, color: "#0d1117" },
        textColor: "#e6edf3",
        fontSize: 12,
      },
      grid: {
        vertLines: { color: "rgba(48, 54, 61, 0.4)" },
        horzLines: { color: "rgba(48, 54, 61, 0.4)" },
      },
      crosshair: {
        mode: 1, // CrosshairMode.Normal
        vertLine: {
          labelBackgroundColor: "#30363d",
        },
        horzLine: {
          labelBackgroundColor: "#30363d",
        },
      },
      rightPriceScale: {
        borderColor: "#30363d",
        scaleMargins: { top: 0.1, bottom: 0.1 },
      },
      timeScale: {
        borderColor: "#30363d",
        timeVisible: true,
        secondsVisible: false,
        rightOffset: 5,
        barSpacing: 8,
      },
      handleScroll: true,
      handleScale: true,
    })

    chartRef.current = chart

    // Crear serie de velas (candlestick)
    const candleSeries = chart.addSeries(CandlestickSeries, {
      upColor: "#3fb950",
      downColor: "#f85149",
      borderUpColor: "#3fb950",
      borderDownColor: "#f85149",
      wickUpColor: "#3fb950",
      wickDownColor: "#f85149",
    })

    candleSeriesRef.current = candleSeries

    // Resize automático
    const resizeObserver = new ResizeObserver(() => {
      chart.applyOptions({
        width: chartContainerRef.current?.clientWidth ?? 800,
        height: chartContainerRef.current?.clientHeight ?? 400,
      })
    })

    if (chartContainerRef.current) {
      resizeObserver.observe(chartContainerRef.current)
    }

    return () => {
      resizeObserver.disconnect()
      chart.remove()
      chartRef.current = null
      candleSeriesRef.current = null
    }
  }, [])

  // Actualizar datos del gráfico
  useEffect(() => {
    if (!candleSeriesRef.current || !candles.length) return

    const data = candles.map(c => ({
      time: c.open_time / 1000, // Lightweight Charts usa segundos
      open: c.open,
      high: c.high,
      low: c.low,
      close: c.close,
    }))

    candleSeriesRef.current.setData(data)

    // Ajustar vista a los datos
    if (chartRef.current) {
      const range = candleSeriesRef.current.timeScale().getVisibleLogicalRange()
      if (!range) {
        candleSeriesRef.current.timeScale().fitContent()
      }
    }
  }, [candles])

  // Indicador de conexión
  const connectionIndicator = wsConnected ? "●" : "○"
  const connectionColor = wsConnected ? "#3fb950" : "#f85149"

  return (
    <div className="chart-panel">
      {/* Overlay: título + estado de conexión */}
      <div style={{
        position: "absolute",
        top: 8,
        left: 16,
        zIndex: 10,
        display: "flex",
        alignItems: "center",
        gap: 8,
        fontSize: 14,
        fontWeight: 600,
        color: "#e6edf3",
      }}>
        <span>{symbol}</span>
        <span style={{
          display: "inline-flex",
          alignItems: "center",
          gap: 4,
          fontSize: 12,
          color: connectionColor,
        }}>
          {connectionIndicator}
          {wsConnected ? "Live" : "Offline"}
        </span>
      </div>

      {/* Área del gráfico */}
      <div className="chart-area">
        {!loading && candles.length > 0 ? (
          <div ref={chartContainerRef} style={{ width: "100%", height: "100%" }} />
        ) : (
          <div className="chart-placeholder">
            <span className="icon">{loading ? "⏳" : "📊"}</span>
            <span>{loading ? "Cargando velas..." : "Sin datos"}</span>
            {!wsConnected && (
              <span style={{ color: "#f85149", fontSize: 12 }}>
                Desconectado del servidor
              </span>
            )}
          </div>
        )}
      </div>

      {/* Leyenda inferior: cantidad de velas + última actualización */}
      {candles.length > 0 && (
        <div style={{
          position: "absolute",
          bottom: 8,
          right: 16,
          fontSize: 11,
          color: "#8b949e",
          zIndex: 10,
        }}>
          {candles.length} velas · Última: {new Date(candles[candles.length - 1].close_time).toLocaleTimeString()}
        </div>
      )}
    </div>
  )
}
