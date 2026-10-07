// Vigencia local del análisis técnico; no ejecuta estrategias ni invoca modelos.

import { MAX_CANDLES } from "../market/candle_store"
import type { CandleData, MarketDataSource } from "../types/market"

export interface AnalysisSnapshotContext {
  symbol: string
  interval: string | null
  dataSource: MarketDataSource
}

/** Identidad del input completo, no una revisión inventada del proveedor. */
export function createAnalysisSnapshotKey(
  context: AnalysisSnapshotContext, candles: readonly CandleData[],
): string {
  return JSON.stringify([
    context.symbol, context.interval, context.dataSource,
    candles.slice(-MAX_CANDLES).map(c => [
      c.open_time, c.close_time, c.open, c.high, c.low, c.close, c.volume, c.is_closed,
    ]),
  ])
}

export interface AnalysisRequestTicket {
  readonly id: number
  readonly snapshotKey: string
  readonly signal: AbortSignal
}

export class AnalysisRequestTracker {
  private nextId = 0
  private active: { ticket: AnalysisRequestTicket; controller: AbortController } | null = null

  begin(snapshotKey: string): AnalysisRequestTicket {
    this.invalidate()
    const controller = new AbortController()
    const ticket = Object.freeze({ id: ++this.nextId, snapshotKey, signal: controller.signal })
    this.active = { ticket, controller }
    return ticket
  }

  isCurrent(ticket: AnalysisRequestTicket, snapshotKey: string = ticket.snapshotKey): boolean {
    const active = this.active?.ticket
    return active?.id === ticket.id && active.snapshotKey === ticket.snapshotKey
      && snapshotKey === ticket.snapshotKey && active.signal === ticket.signal
      && !active.signal.aborted && !ticket.signal.aborted
  }

  invalidate(): void {
    const previous = this.active
    this.active = null
    previous?.controller.abort()
  }
}
