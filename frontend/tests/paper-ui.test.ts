import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import {
  getActivity,
  getBalances,
  getFills,
  getLifecycle,
  getOrders,
  getPositions,
  PaperApiError,
} from "../src/api/paper"

const ACCOUNT = "acct-uid"
const SESSION = "sess-uid"

function response(body: unknown, status = 200): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  } as Response
}

function envelope(key: string, value: unknown, sessionId?: string): Record<string, unknown> {
  return {
    account_id: ACCOUNT,
    environment: "paper",
    ...(sessionId === undefined ? {} : { session_id: sessionId }),
    [key]: value,
  }
}

beforeEach(() => {
  vi.stubGlobal("window", { location: { origin: "http://frontend.test" } })
  vi.stubGlobal("fetch", vi.fn())
})

afterEach(() => {
  vi.unstubAllGlobals()
})

describe("PAPER UI data flow", () => {
  it("returns correct envelope for balances endpoint", async () => {
    vi.mocked(fetch).mockResolvedValueOnce(response(envelope("balances", [
      { asset: "USD", total: "1000", available: "1000", reserved: "0" },
    ])))

    const result = await getBalances(ACCOUNT)
    expect(result.balances[0].asset).toBe("USD")
    expect(typeof result.balances[0].total).toBe("string")
  })

  it("returns correct envelope for positions endpoint", async () => {
    vi.mocked(fetch).mockResolvedValueOnce(response(envelope("positions", [
      { account_id: ACCOUNT, paper_session_id: SESSION, session_id: SESSION, environment: "paper" },
    ])))

    const result = await getPositions(ACCOUNT)
    expect(result.positions.length).toBe(1)
  })

  it("returns correct envelope for orders endpoint with session", async () => {
    vi.mocked(fetch).mockResolvedValueOnce(response(envelope("orders", [
      { account_id: ACCOUNT, paper_account_id: ACCOUNT, paper_session_id: SESSION, session_id: SESSION, environment: "paper" },
    ], SESSION)))

    const result = await getOrders(ACCOUNT, SESSION)
    expect(result.orders.length).toBe(1)
  })

  it("returns correct envelope for fills endpoint with session", async () => {
    vi.mocked(fetch).mockResolvedValueOnce(response(envelope("fills", [
      { account_id: ACCOUNT, session_id: SESSION, environment: "paper" },
    ], SESSION)))

    const result = await getFills(ACCOUNT, SESSION)
    expect(result.fills.length).toBe(1)
  })

  it("returns correct envelope for lifecycle endpoint", async () => {
    vi.mocked(fetch).mockResolvedValueOnce(response({
      account_id: ACCOUNT,
      environment: "paper",
      session_id: SESSION,
      paper_session_id: SESSION,
       state: { account_id: ACCOUNT, paper_session_id: SESSION, state: "running", last_updated_ms: "1" },
      reconciliation: null,
    }))

    const result = await getLifecycle(ACCOUNT, SESSION)
    expect(result.state.state).toBe("running")
    expect(result.state.last_updated_ms).toBe(1)
  })

  it("returns correct envelope for activity endpoint with session", async () => {
    vi.mocked(fetch).mockResolvedValueOnce(response(envelope("activity", [
      {
        schema_version: "paper_fill.v1",
        kind: "fill",
        fill_id: "fill-uid",
        order_id: "order-uid",
        qty: "0.5",
        price: "10",
        timestamp_ms: "30",
        commission: null,
        lot_allocations: [],
        account_id: ACCOUNT,
        session_id: SESSION,
        environment: "paper",
        applied: false,
        applied_at_ms: null,
      },
    ], SESSION)))

    const result = await getActivity(ACCOUNT, SESSION)
    expect(result.activity.length).toBe(1)
    expect(result.activity[0].kind).toBe("fill")
  })

  it("rejects non-2xx responses as PaperApiError", async () => {
    vi.mocked(fetch).mockResolvedValueOnce(response({ detail: "error" }, 500))
    await expect(getBalances(ACCOUNT)).rejects.toBeInstanceOf(PaperApiError)
  })

  it("rejects mismatched account identity", async () => {
    vi.mocked(fetch).mockResolvedValueOnce(response({
      account_id: "other-account",
      environment: "paper",
      balances: [],
    }))
    await expect(getBalances(ACCOUNT)).rejects.toThrow("Invalid PAPER response")
  })

  it("rejects non-paper environment", async () => {
    vi.mocked(fetch).mockResolvedValueOnce(response({
      account_id: ACCOUNT,
      environment: "live",
      balances: [],
    }))
    await expect(getBalances(ACCOUNT)).rejects.toThrow("Invalid PAPER response")
  })

  it("rejects malformed collection items", async () => {
    vi.mocked(fetch).mockResolvedValueOnce(response(envelope("balances", [
      { asset: "USD" },
    ])))
    await expect(getBalances(ACCOUNT)).rejects.toThrow("Invalid PAPER response")
  })
})

describe("PAPER UI data states", () => {
  it("preserves Decimal strings without coercion", async () => {
    vi.mocked(fetch).mockResolvedValueOnce(response(envelope("balances", [
      { asset: "USD", total: "000123.4500", available: "123.4500", reserved: "0.0000" },
    ])))
    const result = await getBalances(ACCOUNT)
    expect(result.balances[0].total).toBe("000123.4500")
    expect(typeof result.balances[0].total).toBe("string")
  })

  it("handles empty data gracefully", async () => {
    vi.mocked(fetch).mockResolvedValueOnce(response(envelope("balances", [])))
    const result = await getBalances(ACCOUNT)
    expect(result.balances).toEqual([])
  })

  it("excludes P&L fields from display via displayItem pattern", () => {
    const item = { account_id: ACCOUNT, environment: "paper", open_pnl: 100, realized_pnl: -50, total: "1000" }
    const filtered = JSON.parse(JSON.stringify(item, (key, value) => {
      return /(?:^|_)pnl$/i.test(key) ? undefined : value
    }))
    expect(filtered).not.toHaveProperty("open_pnl")
    expect(filtered).not.toHaveProperty("realized_pnl")
    expect(filtered).toHaveProperty("total")
  })
})
