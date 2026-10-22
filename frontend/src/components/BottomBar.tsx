import { useRef, useState } from "react"
import type { ReactNode } from "react"
import type { KeyboardEvent } from "react"
import { usePaperData } from "../hooks/usePaperData"
import type { BottomTab } from "../preferences/uiPreferences"

interface BottomBarProps {
  accountId?: string | null
  sessionId?: string | null
  paperAvailable?: boolean | null
  activeTab?: BottomTab
  onActiveTabChange?: (tab: BottomTab) => void
}

type PaperTab = BottomTab

function displayItem(item: Record<string, unknown>): string {
  return JSON.stringify(item, (key, value) => {
    // PAPER may expose P&L fields in a projection; this surface must not
    // calculate or present them.
    return /(?:^|_)pnl$/i.test(key) ? undefined : value
  }) ?? ""
}

export function BottomBar({ accountId, sessionId, paperAvailable, activeTab: controlledActiveTab, onActiveTabChange }: BottomBarProps) {
  const paper = usePaperData(accountId, sessionId)
  const [internalActiveTab, setInternalActiveTab] = useState<PaperTab>("balances")
  const activeTab = controlledActiveTab ?? internalActiveTab
  const tabRefs = useRef<Array<HTMLButtonElement | null>>([])
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

  const selectTab = (tab: PaperTab) => {
    if (controlledActiveTab === undefined) setInternalActiveTab(tab)
    onActiveTabChange?.(tab)
  }

  const paperAvailabilityUnknown = paperAvailable == null
  const renderContent = (tab: PaperTab): ReactNode => {
    if (paperAvailabilityUnknown) return <span>PAPER desconocido</span>
    if (!paperAvailable) return <span>PAPER no disponible</span>
    if (!accountId) return <span>PAPER read-only · sin cuenta activa</span>
    if (paper.loading) return <span role="status" aria-label="Loading PAPER data">Loading PAPER data…</span>
    if (paper.error) return <span role="alert">PAPER data unavailable</span>
    if (tabData[tab].length === 0) return <span>No PAPER data available</span>
    return (
      <div style={{ display: "flex", gap: 12, overflow: "auto", whiteSpace: "nowrap" }}>
        {tabData[tab].map((item, index) => (
          <code key={index} style={{ fontSize: 11 }}>
            {displayItem(item as Record<string, unknown>)}
          </code>
        ))}
      </div>
    )
  }

  const handleTabKeyDown = (event: KeyboardEvent<HTMLButtonElement>, index: number) => {
    let nextIndex: number | null = null
    if (event.key === "ArrowRight") nextIndex = (index + 1) % tabs.length
    if (event.key === "ArrowLeft") nextIndex = (index - 1 + tabs.length) % tabs.length
    if (event.key === "Home") nextIndex = 0
    if (event.key === "End") nextIndex = tabs.length - 1
    if (nextIndex === null) return

    event.preventDefault()
    selectTab(tabs[nextIndex].key)
    tabRefs.current[nextIndex]?.focus()
  }

  return (
    <footer className="bottom-bar" aria-label={
      paperAvailabilityUnknown ? "PAPER desconocido" : paperAvailable ? "PAPER read-only data" : "PAPER no disponible"
    }>
      <div style={{ display: "flex", flexDirection: "column", gap: 4, minWidth: 0, flex: 1 }}>
        <div role="tablist" aria-label="PAPER data tabs" style={{ display: "flex", gap: 10, alignItems: "center", overflow: "auto" }}>
          <strong style={{ fontSize: 11 }}>
            {paperAvailabilityUnknown ? "PAPER · desconocido" : paperAvailable ? "PAPER · read-only" : "PAPER · no disponible"}
          </strong>
          {tabs.map((tab, index) => {
            const tabIndex = activeTab === tab.key ? 0 : -1
            return (
              <button
                key={tab.key}
                type="button"
                role="tab"
                id={`paper-tab-${tab.key}`}
                aria-selected={activeTab === tab.key}
                aria-controls={`tabpanel-${tab.key}`}
                tabIndex={tabIndex}
                ref={element => { tabRefs.current[index] = element }}
                onClick={() => selectTab(tab.key)}
                onKeyDown={event => handleTabKeyDown(event, index)}
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
            )
          })}
          <span style={{ marginLeft: "auto", fontSize: 10, color: "#8b949e" }}>
            Lifecycle: {paper.lifecycle ? "available" : "unavailable"}
          </span>
        </div>
        <div style={{ minHeight: 14, overflow: "hidden" }}>
          {tabs.map(tab => (
            <div
              key={tab.key}
              id={`tabpanel-${tab.key}`}
              role="tabpanel"
              aria-labelledby={`paper-tab-${tab.key}`}
              hidden={activeTab !== tab.key}
              tabIndex={0}
            >
              {renderContent(tab.key)}
            </div>
          ))}
        </div>
      </div>
      {accountId && (
        <button type="button" onClick={paper.refetch} style={{ fontSize: 11 }}>
          Refresh
        </button>
      )}
    </footer>
  )
}
