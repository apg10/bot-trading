import { afterEach, describe, expect, it, vi } from "vitest"
import {
  buildScenarioSnapshot,
  requestScenarioAnalysis,
  ScenarioApiError,
  type ScenarioSnapshot,
} from "../src/api/scenarios"
import type { CandleData, MarketStatus } from "../src/types/market"

const closeTime = 1_700_000_039_999

function context(): { candles: CandleData[]; status: MarketStatus } {
  return {
    candles: [{
      open_time: closeTime - 59_999, close_time: closeTime,
      open: 100, high: 110, low: 90, close: 105, volume: 2, is_closed: true,
    }],
    status: {
      symbol: "BTC/USDT", connected: true, reconnecting: false, closed_candles_count: 1,
      last_bar_open_time: closeTime - 59_999, interval: "1m", data_source: "market_engine",
      recovering: false, history_loaded: true, complete: true, stale: false,
      pending_gaps: 0, last_closed_close_time: closeTime, execution_available: false,
    },
  }
}

function snapshot(): ScenarioSnapshot {
  const input = context()
  return buildScenarioSnapshot({
    ...input, symbol: "BTC/USDT", interval: "1m", dataSource: "market_engine", nowMs: closeTime + 1_000,
  })
}

describe("N02 scenario client", () => {
  afterEach(() => {
    vi.restoreAllMocks()
    vi.unstubAllGlobals()
    vi.unstubAllEnvs()
  })

  it("builds only the last closed close and declares unavailable context", () => {
    const result = snapshot()
    expect(result.evidence).toEqual({ last_close: {
      value: 105, dimension: "price", unit: "USDT", observed_at_ms: closeTime,
    } })
    expect(result.missing_data).toEqual(["zones", "checklist", "portfolio"])
    expect(result.timeframe).toBe("1m")
  })

  it.each([
    ["interval", { interval: "5m" }],
    ["source", { dataSource: "TEST_ONLY" }],
  ])("does not fabricate a snapshot for incompatible %s", (_label, patch) => {
    const input = context()
    expect(() => buildScenarioSnapshot({
      ...input, symbol: "BTC/USDT", interval: "1m", dataSource: "market_engine",
      nowMs: closeTime + 1_000, ...patch,
    })).toThrow(ScenarioApiError)
  })

  it("maps a busy HTTP response to a public code without exposing the body", async () => {
    vi.stubEnv("VITE_API_URL", "")
    vi.stubGlobal("window", { location: { origin: "http://localhost" } })
    vi.stubGlobal("fetch", vi.fn(async () => ({
      ok: false, status: 429, json: async () => ({ detail: { code: "busy", prompt: "private" } }),
    })))
    let error: unknown
    try {
      await requestScenarioAnalysis(snapshot())
    } catch (reason) {
      error = reason
    }
    expect(error).toBeInstanceOf(ScenarioApiError)
    expect((error as ScenarioApiError).code).toBe("busy")
    expect((error as Error).message).not.toContain("private")
  })
})
