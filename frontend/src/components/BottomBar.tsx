import { useState } from "react"
import type { ReactNode } from "react"
import { usePaperData } from "../hooks/usePaperData"

interface BottomBarProps {
  accountId?: string | null
  sessionId?: string | null
}

type PaperTab = "balances" | "positions" | "orders" | "fills" | "activity"

function displayItem(item: Record<string, unknown>): string {
  return JSON.stringify(item, (key, value) => {
    // PAPER may expose P&L fields in a projection; this surface must not
    // calculate or present them.
    return /(?:^|_)pnl$/i.test(key) ? undefined : value
  }) ?? ""
}

export function BottomBar({ accountId, sessionId }: BottomBarProps) {
  const paper = usePaperData(accountId, sessionId)
  const [activeTab, setActiveTab] = useState<PaperTab>("balances")
  const tabData: Record<PaperTab, readonly unknown[]> = {
    balances: paper.balances,
    positions: paper.positions,
    orders: paper.orders,
    fills: paper.fills,
    activity: paper.activity,
  }
  const tabs: Array<{ key: PaperTab; label: string }> = [
    { key: "balances", label: `Balance (${paper.balances.length})` },
    { key: "positions", label: `Positions (${paper.positions.length})` },
    { key: "orders", label: `Orders (${paper.orders.length})` },
    { key: "fills", label: `Fills (${paper.fills.length})` },
    { key: "activity", label: `Activity (${paper.activity.length})` },
  ]

  let content: ReactNode
  if (!accountId) {
    content = <span>PAPER data unavailable</span>
  } else if (paper.loading) {
    content = <span role="status" aria-label="Loading PAPER data">Loading PAPER data…</span>
  } else if (paper.error) {
    content = <span role="alert">PAPER data unavailable</span>
  } else if (tabData[activeTab].length === 0) {
    content = <span>No PAPER data available</span>
  } else {
    content = (
      <div style={{ display: "flex", gap: 12, overflow: "auto", whiteSpace: "nowrap" }}>
        {tabData[activeTab].map((item, index) => (
          <code key={index} style={{ fontSize: 11 }}>
            {displayItem(item as Record<string, unknown>)}
          </code>
        ))}
      </div>
    )
  }

  return (
    <footer className="bottom-bar" aria-label="PAPER read-only data">
      <div style={{ display: "flex", flexDirection: "column", gap: 4, minWidth: 0, flex: 1 }}>
        <div role="tablist" aria-label="PAPER data tabs" style={{ display: "flex", gap: 10, alignItems: "center", overflow: "auto" }}>
          <strong style={{ fontSize: 11 }}>PAPER · read-only</strong>
          {tabs.map(tab => (
            <button
              key={tab.key}
              type="button"
              role="tab"
              aria-selected={activeTab === tab.key}
              onClick={() => setActiveTab(tab.key)}
              style={{
                cursor: "pointer",
                border: 0,
                borderBottom: activeTab === tab.key ? "2px solid #4f8cff" : "2px solid transparent",
                padding: "0 0 2px",
                background: "transparent",
                color: "#e6edf3",
                fontSize: 11,
              }}
            >
              {tab.label}
            </button>
          ))}
          <span style={{ marginLeft: "auto", fontSize: 10, color: "#8b949e" }}>
            Lifecycle: {paper.lifecycle ? "available" : "unavailable"}
          </span>
        </div>
        <div style={{ minHeight: 14, overflow: "hidden" }}>{content}</div>
      </div>
      {accountId && (
        <button type="button" onClick={paper.refetch} style={{ fontSize: 11 }}>
          Refresh
        </button>
      )}
    </footer>
  )
}
