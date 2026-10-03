// ── Fixture sintética determinista (TypeScript).

import type { Candle, MarketSnapshot, Zone, Pivot, Annotation, Environment } from "../types"

function seededRandom(seed: number): () => number {
  let s = seed
  return () => {
    s = (s * 16807 + 0) % 2147483647
    return s / 2147483647
  }
}

export function generateCandles(count: number = 100, basePrice: number = 67500): Candle[] {
  const rng = seededRandom(42)
  const candles: Candle[] = []
  let price = basePrice
  const startTs = 1700000000000

  for (let i = 0; i < count; i++) {
    const changePct = (rng() - 0.5) * 0.01 // ±0.5%
    const highPct = rng() * 0.006
    const lowPct = rng() * 0.006

    const openP = price
    const closeP = price * (1 + changePct)
    const highP = Math.max(openP, closeP) * (1 + highPct)
    const lowP = Math.min(openP, closeP) * (1 - lowPct)

    candles.push({
      timestamp_ms: startTs + i * 60_000,
      open: Math.round(openP * 100) / 100,
      high: Math.round(highP * 100) / 100,
      low: Math.round(lowP * 100) / 100,
      close: Math.round(closeP * 100) / 100,
      volume: Math.round(rng() * 450 + 50),
    })

    price = closeP
  }

  return candles
}

export function generateMarketSnapshot(candles: Candle[], idx?: number): MarketSnapshot {
  const resolvedIdx = idx !== undefined ? idx : candles.length - 1
  const c = candles[resolvedIdx]
  if (!c) throw new Error("generateMarketSnapshot: candle not found")
  const rng = seededRandom(99)
  const spread = Math.abs(c.close - c.open) * 0.1
  const bid = c.close - spread / 2
  const ask = c.close + spread / 2

  return {
    symbol: "BTC/USDT",
    environment: "paper",
    candle: c,
    bid: Math.round(bid * 100) / 100,
    ask: Math.round(ask * 100) / 100,
    timestamp_ms: c.timestamp_ms,
  }
}

export function generateZones(count: number = 5): Zone[] {
  const rng = seededRandom(77)
  const origins: Zone["origin"][] = ["support", "resistance", "breakout", "retracement"]
  const statuses: Zone["status"][] = [
    "observing", "candidate", "pending_analysis", "approved", "rejected",
  ]
  const zones: Zone[] = []

  for (let i = 0; i < count; i++) {
    const low = 67000 + rng() * 1000
    const high = low + rng() * 250 + 50
    zones.push({
      id: `zone-${String(i).padStart(3, "0")}`,
      origin: origins[i % origins.length],
      status: statuses[i % statuses.length],
      low: Math.round(low * 100) / 100,
      high: Math.round(high * 100) / 100,
      timestamp_ms: 1700000000000 + i * 60_000,
      contacts: Math.floor(rng() * 14 + 2),
    })
  }

  return zones
}

export function generatePivots(count: number = 4): Pivot[] {
  const rng = seededRandom(88)
  const types: Pivot["pivot_type"][] = ["swing_high", "swing_low", "equal_high", "equal_low"]
  const pivots: Pivot[] = []

  for (let i = 0; i < count; i++) {
    const price = 67500 + (rng() - 0.5) * 1000
    pivots.push({
      id: `pivot-${String(i).padStart(3, "0")}`,
      pivot_type: types[i % types.length],
      price: Math.round(price * 100) / 100,
      timestamp_ms: 1700000000000 + i * 60_000,
      strength: Math.round(rng() * 0.7 + 0.3),
    })
  }

  return pivots
}

export function generateAnnotations(count: number = 3): Annotation[] {
  const rng = seededRandom(55)
  const types: Annotation["annotation_type"][] = [
    "entry_proposal", "stop_loss", "take_profit", "zone", "note",
  ]
  const annotations: Annotation[] = []

  for (let i = 0; i < count; i++) {
    annotations.push({
      id: `ann-${String(i).padStart(3, "0")}`,
      annotation_type: types[i % types.length],
      symbol: "BTC/USDT",
      content: `Anotación de prueba ${i + 1}`,
      x_px: Math.round(rng() * 700 + 100),
      y_px: Math.round(rng() * 400 + 100),
      timestamp_ms: 1700000000000 + i * 60_000,
    })
  }

  return annotations
}

export function getFullFixture(): {
  snapshot: MarketSnapshot
  candles: Candle[]
  zones: Zone[]
  pivots: Pivot[]
  annotations: Annotation[]
} {
  const candles = generateCandles(100)
  return {
    snapshot: generateMarketSnapshot(candles),
    candles: candles.slice(-10),
    zones: generateZones(5),
    pivots: generatePivots(4),
    annotations: generateAnnotations(3),
  }
}
