import { afterEach, describe, expect, it, vi } from "vitest"
import { reconfigure } from "../src/api/market"
import { useMarketData } from "../src/hooks/useMarketData"

// A small hook runner keeps this contract test independent of a DOM renderer.
const reactHooks = vi.hoisted(() => {
  const states: unknown[] = []
  const refs: { current: unknown }[] = []
  const effects: { deps?: readonly unknown[]; cleanup?: () => void }[] = []
  const pending: (() => void)[] = []
  let stateIndex = 0
  let refIndex = 0
  let effectIndex = 0

  return {
    useState<T>(initial: T) {
      const index = stateIndex++
      if (index >= states.length) states[index] = initial
      const setState = (next: T | ((previous: T) => T)) => {
        states[index] = typeof next === "function"
          ? (next as (previous: T) => T)(states[index] as T) : next
      }
      return [states[index] as T, setState] as const
    },
    useRef<T>(initial: T) {
      const index = refIndex++
      if (index >= refs.length) refs[index] = { current: initial }
      return refs[index] as { current: T }
    },
    useEffect(effect: () => void | (() => void), deps?: readonly unknown[]) {
      const index = effectIndex++
      const previous = effects[index]
      const unchanged = previous && deps && previous.deps
        && deps.length === previous.deps.length
        && deps.every((value, i) => Object.is(value, previous.deps![i]))
      if (unchanged) return
      pending.push(() => {
        previous?.cleanup?.()
        const cleanup = effect()
        effects[index] = { deps, cleanup: typeof cleanup === "function" ? cleanup : undefined }
      })
    },
    render<T>(hook: () => T): T {
      stateIndex = 0
      refIndex = 0
      effectIndex = 0
      const result = hook()
      for (const run of pending.splice(0)) run()
      return result
    },
    reset() {
      for (const effect of [...effects].reverse()) effect.cleanup?.()
      states.length = 0
      refs.length = 0
      effects.length = 0
      pending.length = 0
      stateIndex = refIndex = effectIndex = 0
    },
  }
})

vi.mock("react", () => ({
  useState: reactHooks.useState,
  useRef: reactHooks.useRef,
  useEffect: reactHooks.useEffect,
}))

class SelectionWebSocket {
  static readonly CONNECTING = 0
  static readonly OPEN = 1
  static readonly CLOSING = 2
  static readonly CLOSED = 3
  static instances: SelectionWebSocket[] = []
  readyState = SelectionWebSocket.CONNECTING
  onopen: (() => void) | null = null
  onmessage: ((event: { data: string }) => void) | null = null
  onclose: (() => void) | null = null

  constructor() { SelectionWebSocket.instances.push(this) }
  send(): void {}
  close(): void {
    this.readyState = SelectionWebSocket.CLOSED
    this.onclose?.()
  }
  open(): void {
    this.readyState = SelectionWebSocket.OPEN
    this.onopen?.()
  }
  message(value: unknown): void {
    this.onmessage?.({ data: JSON.stringify(value) })
  }
}

describe("market selection and engine reconfiguration", () => {
  afterEach(() => {
    reactHooks.reset()
    SelectionWebSocket.instances = []
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  it("posts the selected context without changing it locally", async () => {
    vi.stubGlobal("window", { location: { origin: "http://localhost" } })
    const fetchMock = vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
      expect(init?.method).toBe("POST")
      expect(init?.body).toBe(JSON.stringify({ symbol: "ETH/USDT", interval: "15m" }))
      return {
        ok: true,
        json: async () => ({ symbol: "ETH/USDT", interval: "15m", stream_id: "stream-2", status: "reconfiguring" }),
      } as Response
    })
    vi.stubGlobal("fetch", fetchMock)

    const result = await reconfigure("ETH/USDT", "15m")
    expect(result).toEqual({ symbol: "ETH/USDT", interval: "15m", stream_id: "stream-2", status: "reconfiguring" })
    expect(fetchMock).toHaveBeenCalledTimes(1)
  })

  it("surfaces a failed request so the engine context can remain unchanged", async () => {
    vi.stubGlobal("window", { location: { origin: "http://localhost" } })
    vi.stubGlobal("fetch", vi.fn(async () => ({ ok: false, status: 422 }) as Response))

    await expect(reconfigure("BTC/USDT", "7m")).rejects.toThrow("HTTP 422")
  })

  it("propagates another tab's stream context and accepts its ETH events", async () => {
    vi.stubGlobal("window", { location: { origin: "http://localhost" } })
    vi.stubGlobal("WebSocket", SelectionWebSocket)
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
      const eth = String(input).includes("ETH%2FUSDT")
      return {
        ok: true,
        json: async () => ({
          symbol: eth ? "ETH/USDT" : "BTC/USDT",
          interval: eth ? "15m" : "1m",
          data_source: "TEST_ONLY",
          candles: [],
        }),
      } as Response
    }))

    const render = () => reactHooks.render(() => useMarketData("BTC/USDT", "1m"))
    render()
    await Promise.resolve()
    await Promise.resolve()
    const first = SelectionWebSocket.instances[0]
    first.open()
    first.message({
      type: "status", schema_version: "market_transport.v1", stream_id: "stream-eth",
      sequence: 1, symbol: "ETH/USDT", interval: "15m", connected: true,
      reconnecting: false, closed_candles_count: 0, last_bar_open_time: null,
      data_source: "market_engine",
    })

    // The status is accepted before React's parent render.  The subsequent
    // render reconnects the observer and uses the newly announced context.
    render()
    expect(SelectionWebSocket.instances.length).toBeGreaterThan(1)
    const second = SelectionWebSocket.instances[SelectionWebSocket.instances.length - 1]!
    second.open()
    second.message({
      type: "snapshot", schema_version: "market_transport.v1", stream_id: "stream-eth",
      sequence: 1, interval: "15m", candles: [], complete: true,
    })
    second.message({
      type: "bar_closed", schema_version: "market_transport.v1", stream_id: "stream-eth",
      sequence: 2, symbol: "ETH/USDT", interval: "15m", data_source: "market_engine",
      candle: {
        open_time: 2000, close_time: 2060, open: 100, high: 110, low: 90,
        close: 105, volume: 2, is_closed: true,
      },
    })

    const result = render()
    expect(result.candles).toHaveLength(1)
    expect(result.interval).toBe("15m")
    expect(result.degraded).toBe(false)
  })
})
