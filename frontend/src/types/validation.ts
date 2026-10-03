// ── Contratos TypeScript equivalentes a los Pydantic del backend.

import {
  type Candle,
  type MarketSnapshot,
  type Zone,
  type Pivot,
  type Annotation,
  type Order,
  type Position,
  type EngineInfo,
  type AnalysisResult,
} from "./state"

// ── Validación de snapshot (bid < ask) ───────────────────────────────────────

export function validateSnapshot(snap: MarketSnapshot): void {
  if (snap.candle.high < snap.candle.low) {
    throw new Error("high debe ser >= low")
  }
  if (snap.bid >= snap.ask) {
    throw new Error("bid debe ser < ask")
  }
}

// ── Validación de vela ───────────────────────────────────────────────────────

export function validateCandle(c: Candle): void {
  if (c.high < c.low) throw new Error("high >= low")
  if (c.high < c.open) throw new Error("high >= open")
  if (c.high < c.close) throw new Error("high >= close")
  if (c.low > c.open) throw new Error("low <= open")
  if (c.low > c.close) throw new Error("low <= close")
}

// ── Validación de zona ───────────────────────────────────────────────────────

export function validateZone(z: Zone): void {
  if (z.high < z.low) throw new Error("zona: high >= low")
  if (z.contacts < 0) throw new Error("zona: contacts >= 0")
}

// ── Validación de orden ──────────────────────────────────────────────────────

export function validateOrder(o: Order): void {
  if (o.quantity <= 0) throw new Error("orden: quantity > 0")
  if (o.status === "filled" && !o.price) {
    throw new Error("orden fill requiere price")
  }
}
