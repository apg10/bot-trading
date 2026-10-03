// Barra inferior funcional: posiciones, órdenes, actividad reciente.

import type { Position, Order } from "../types/state"

interface BottomBarProps {
  positions: Position[]
  orders: Order[]
  activityLog: ActivityEntry[]
}

interface ActivityEntry {
  id: string
  timestamp_ms: number
  type: "order_created" | "order_filled" | "order_cancelled" | "position_opened" | "position_closed" | "analysis"
  symbol: string
  description: string
}

export function BottomBar({ positions, orders, activityLog }: BottomBarProps) {
  const tabs = [
    { key: "positions", label: `Posiciones (${positions.length})` },
    { key: "orders", label: `Órdenes (${orders.length})` },
    { key: "activity", label: `Actividad (${activityLog.length})` },
  ]

  return (
    <footer className="bottom-bar">
      {/* Tabs de navegación */}
      <div style={{ display: "flex", gap: 16, alignItems: "center" }}>
        {tabs.map(tab => (
          <span
            key={tab.key}
            style={{
              cursor: "pointer",
              borderBottom: "2px solid transparent",
              paddingBottom: 4,
              fontSize: 12,
              fontWeight: 600,
              color: "#e6edf3",
            }}
            onMouseEnter={e => {
              e.currentTarget.style.borderBottomColor = "#4f8cff"
            }}
            onMouseLeave={e => {
              e.currentTarget.style.borderBottomColor = "transparent"
            }}
          >
            {tab.label}
          </span>
        ))}
      </div>

      {/* Resumen de posiciones */}
      {positions.length > 0 && (
        <div style={{ display: "flex", gap: 24, alignItems: "center" }}>
          {positions.map(p => (
            <div key={`${p.symbol}-${p.side}`} style={{ display: "flex", gap: 8, fontSize: 12 }}>
              <span>
                <strong>{p.symbol}</strong>{" "}
                <span className={p.side === "buy" ? "text-green" : "text-red"}>
                  {p.side.toUpperCase()}
                </span>
              </span>
              <span>Cant: {p.quantity.toFixed(4)}</span>
              <span>Entry: ${p.entry_price.toLocaleString()}</span>
              <span className={p.unrealized_pnl >= 0 ? "text-green" : "text-red"}>
                P&L: {p.unrealized_pnl >= 0 ? "+" : ""}${p.unrealized_pnl.toFixed(2)}
              </span>
            </div>
          ))}
        </div>
      )}

      {/* Resumen de órdenes pendientes */}
      {orders.length > 0 && (
        <div style={{ display: "flex", gap: 16, alignItems: "center" }}>
          {orders.slice(0, 3).map(o => (
            <span key={o.id} style={{ fontSize: 12 }}>
              <strong>{o.symbol}</strong>{" "}
              <span className={o.side === "buy" ? "text-green" : "text-red"}>
                {o.side.toUpperCase()}
              </span>{" "}
              · {o.order_type.toUpperCase()}{" "}
              · ${o.price?.toLocaleString() ?? "MKT"}
            </span>
          ))}
        </div>
      )}

      {/* Últimas actividades */}
      {activityLog.length > 0 && (
        <div style={{ marginLeft: "auto", fontSize: 11, color: "#8b949e" }}>
          Última actividad:{" "}
          {new Date(activityLog[activityLog.length - 1].timestamp_ms).toLocaleTimeString()}
        </div>
      )}

      {/* Estado general */}
      <div style={{ marginLeft: "auto", display: "flex", gap: 12, fontSize: 11, color: "#8b949e" }}>
        <span>P&L realizado hoy: <strong className="text-green">$0.00</strong></span>
        <span>P&L no realizado: <strong>$0.00</strong></span>
      </div>
    </footer>
  )
}
