// Panel de gráfico con velas (Lightweight Charts) + indicadores overlay.

import { useEffect, useRef } from "react"
import { createChart, ColorType, CandlestickSeries, type IChartApi } from "lightweight-charts"
import type { CandlestickData, ISeriesApi, UTCTimestamp } from "lightweight-charts"
import { MAX_CANDLES } from "../market/candle_store"
import type { CandleData, MarketDataSource } from "../types/market"

interface ChartPanelProps {
  candles: CandleData[]
  symbol: string
  loading: boolean
  wsConnected: boolean
  dataSource: MarketDataSource
  interval: string | null
}

type ChartBar = CandlestickData<UTCTimestamp>

function sameBar(a: ChartBar, b: ChartBar): boolean {
  return a.time === b.time && a.open === b.open && a.high === b.high
    && a.low === b.low && a.close === b.close
}

export function ChartPanel({ candles, symbol, loading, wsConnected, dataSource, interval }: ChartPanelProps) {
  const chartContainerRef = useRef<HTMLDivElement>(null)
  const chartRef = useRef<IChartApi | null>(null)
  const candleSeriesRef = useRef<ISeriesApi<"Candlestick"> | null>(null)
  const plottedRef = useRef<{ context: string | null; data: ChartBar[] }>({ context: null, data: [] })

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
      plottedRef.current = { context: null, data: [] }
    }
  }, [])

  // Actualizar datos del gráfico
  useEffect(() => {
    const series = candleSeriesRef.current
    if (!series) return

    const context = JSON.stringify([symbol, interval, dataSource])
    const data: ChartBar[] = candles.slice(-MAX_CANDLES).map(c => ({
      time: (c.open_time / 1000) as UTCTimestamp, // Lightweight Charts usa segundos
      open: c.open,
      high: c.high,
      low: c.low,
      close: c.close,
    }))

    const previous = plottedRef.current
    const lastIndex = previous.data.length - 1
    // update() solo modifica el último punto o añade puntos posteriores. Si se
    // recorta la izquierda o cambia un punto histórico, reconstruir para no
    // acumular barras fuera de la ventana acotada de C10.
    const compatible = previous.context === context && lastIndex >= 0
      && data.length >= previous.data.length
      && data[lastIndex]?.time === previous.data[lastIndex].time
      && previous.data.slice(0, lastIndex).every((bar, index) => sameBar(bar, data[index]))

    let rebuilt = false
    if (compatible) {
      if (!sameBar(previous.data[lastIndex], data[lastIndex])) series.update(data[lastIndex])
      for (let index = previous.data.length; index < data.length; index++) series.update(data[index])
    } else if (
      previous.context !== context || previous.data.length !== data.length
      || previous.data.some((bar, index) => !sameBar(bar, data[index]))
    ) {
      series.setData(data)
      rebuilt = true
    }
    plottedRef.current = { context, data }

    // Ajustar vista a los datos (timeScale es del chart, no de la serie)
    const chart = chartRef.current
    if (chart && rebuilt && data.length > 0) {
      const range = chart.timeScale().getVisibleLogicalRange()
      if (!range) {
        chart.timeScale().fitContent()
      }
    }
  }, [candles, symbol, interval, dataSource])

  // Indicador de conexión
  const marketConnected = dataSource === "market_engine" && wsConnected
  const connectionIndicator = marketConnected ? "●" : "○"
  const connectionColor = dataSource === "TEST_ONLY" ? "#f0883e"
    : marketConnected ? "#3fb950" : "#8b949e"
  const sourceLabel = dataSource === "TEST_ONLY" ? "TEST_ONLY · datos sintéticos · no operativo"
    : dataSource === "market_engine"
      ? marketConnected ? "Motor conectado · frescura no validada" : "Datos del motor · desconectado"
      : "Procedencia sin verificar"
  const intervalLabel = interval && interval !== "unknown" ? interval : "intervalo sin verificar"
  const hasData = !loading && candles.length > 0

  return (
    <div className="chart-panel" style={{ position: "relative" }}>
      {/* Overlay: título + estado de conexión — siempre visible */}
      <div style={{
        position: "absolute",
        top: 8,
        left: 16,
        zIndex: 10,
        display: "flex",
        alignItems: "center",
        flexWrap: "wrap",
        gap: 8,
        fontSize: 14,
        fontWeight: 600,
        color: "#e6edf3",
      }}>
        <span>{symbol} · {intervalLabel}</span>
        <span style={{
          display: "inline-flex",
          alignItems: "center",
          gap: 4,
          fontSize: 12,
          color: connectionColor,
        }}>
          {connectionIndicator}
          {sourceLabel}
        </span>
      </div>

      {/* Área del gráfico — siempre montado para evitar recrear el gráfico */}
      <div className="chart-area">
        <div ref={chartContainerRef} style={{ width: "100%", height: "100%" }} />
        {!hasData && (
          <div className="chart-placeholder" style={{
            position: "absolute",
            inset: 0,
            display: "flex",
            flexDirection: "column",
            alignItems: "center",
            justifyContent: "center",
            gap: 8,
          }}>
            <span className="icon">{loading ? "⏳" : "📊"}</span>
            <span>{loading ? "Cargando velas..." : "Sin datos"}</span>
            {!wsConnected && (
              <span style={{ color: "#f85149", fontSize: 12 }}>
                Conexión de mercado no verificada
              </span>
            )}
          </div>
        )}
      </div>

      {/* Leyenda inferior: cantidad de velas + última actualización */}
      {hasData && (
        <div style={{
          position: "absolute",
          bottom: 8,
          right: 16,
          fontSize: 11,
          color: "#8b949e",
          zIndex: 10,
        }}>
          {Math.min(candles.length, MAX_CANDLES)} velas · {candles[candles.length - 1].is_closed ? "Cierre" : "Fin previsto"}: {new Date(candles[candles.length - 1].close_time).toLocaleTimeString()}
        </div>
      )}
    </div>
  )
}
