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

const ACCOUNT = "acct/encoded"
const SESSION = "session with spaces"

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

describe("PAPER read client", () => {
  it("calls all six read endpoints with encoded path/query and GET only", async () => {
    const fetchMock = vi.mocked(fetch)
    fetchMock
      .mockResolvedValueOnce(response(envelope("balances", [
        { account_id: ACCOUNT, paper_account_id: ACCOUNT, environment: "paper", asset: "USD", total: "123.4500", available: "100", reserved: "23.4500" },
      ])))
      .mockResolvedValueOnce(response(envelope("positions", [{ account_id: ACCOUNT, paper_account_id: ACCOUNT, environment: "paper" }])))
      .mockResolvedValueOnce(response(envelope("orders", [{ account_id: ACCOUNT, paper_account_id: ACCOUNT, paper_session_id: SESSION, session_id: SESSION, environment: "paper" }], SESSION)))
      .mockResolvedValueOnce(response(envelope("fills", [{ account_id: ACCOUNT, paper_account_id: ACCOUNT, paper_session_id: SESSION, session_id: SESSION, environment: "paper" }], SESSION)))
      .mockResolvedValueOnce(response({
        account_id: ACCOUNT,
        environment: "paper",
        session_id: SESSION,
        paper_session_id: SESSION,
        state: { account_id: ACCOUNT, paper_account_id: ACCOUNT, paper_session_id: SESSION, state: "running", last_updated_ms: 1 },
        reconciliation: null,
      }))
      .mockResolvedValueOnce(response(envelope("activity", [{ account_id: ACCOUNT, paper_account_id: ACCOUNT, paper_session_id: SESSION, session_id: SESSION, environment: "paper" }], SESSION)))

    await getBalances(ACCOUNT)
    await getPositions(ACCOUNT)
    await getOrders(ACCOUNT, SESSION)
    await getFills(ACCOUNT, SESSION)
    await getLifecycle(ACCOUNT, SESSION)
    await getActivity(ACCOUNT, SESSION)

    expect(fetchMock).toHaveBeenCalledTimes(6)
    expect(fetchMock.mock.calls.map(([url]) => url)).toEqual([
      "http://frontend.test/api/paper/accounts/acct%2Fencoded/balances",
      "http://frontend.test/api/paper/accounts/acct%2Fencoded/positions",
      "http://frontend.test/api/paper/accounts/acct%2Fencoded/orders?session_id=session%20with%20spaces",
      "http://frontend.test/api/paper/accounts/acct%2Fencoded/fills?session_id=session%20with%20spaces",
      "http://frontend.test/api/paper/accounts/acct%2Fencoded/lifecycle?session_id=session%20with%20spaces",
      "http://frontend.test/api/paper/accounts/acct%2Fencoded/activity?session_id=session%20with%20spaces",
    ])
    expect(fetchMock.mock.calls.every(([, init]) => init?.method === "GET")).toBe(true)
  })

  it("preserves Decimal strings without numeric coercion", async () => {
    vi.mocked(fetch).mockResolvedValueOnce(response(envelope("balances", [
      { account_id: ACCOUNT, paper_account_id: ACCOUNT, environment: "paper", asset: "USD", total: "000123.4500", available: "123.4500", reserved: "0.0000" },
    ])))
    const result = await getBalances(ACCOUNT)
    expect(result.balances[0].total).toBe("000123.4500")
    expect(typeof result.balances[0].total).toBe("string")
  })

  it("rejects malformed and identity-mismatched successful responses", async () => {
    vi.mocked(fetch)
      .mockResolvedValueOnce(response(envelope("balances", { total: "not-a-list" })))
      .mockResolvedValueOnce(response({ ...envelope("positions", []), account_id: "other-account" }))
    await expect(getBalances(ACCOUNT)).rejects.toBeInstanceOf(PaperApiError)
    await expect(getPositions(ACCOUNT)).rejects.toThrow("Invalid PAPER response")
  })

  it("rejects malformed lifecycle objects", async () => {
    vi.mocked(fetch).mockResolvedValueOnce(response({
      account_id: ACCOUNT,
      environment: "paper",
      session_id: SESSION,
      paper_session_id: SESSION,
      state: [],
      reconciliation: null,
    }))
    await expect(getLifecycle(ACCOUNT, SESSION)).rejects.toThrow("Invalid PAPER response")
  })

  it("rejects lifecycle with invalid nested types", async () => {
    vi.mocked(fetch).mockResolvedValueOnce(response({
      account_id: ACCOUNT,
      environment: "paper",
      session_id: SESSION,
      paper_session_id: SESSION,
      state: null,
      reconciliation: null,
    }))
    await expect(getLifecycle(ACCOUNT, SESSION)).rejects.toThrow("Invalid PAPER response")
    vi.mocked(fetch).mockResolvedValueOnce(response({
      account_id: ACCOUNT,
      environment: "paper",
      session_id: SESSION,
      paper_session_id: SESSION,
      state: { account_id: ACCOUNT, paper_session_id: SESSION, state: "running", last_updated_ms: null },
      reconciliation: null,
    }))
    await expect(getLifecycle(ACCOUNT, SESSION)).rejects.toThrow("Invalid PAPER response")
    vi.mocked(fetch).mockResolvedValueOnce(response({
      account_id: ACCOUNT,
      environment: "paper",
      session_id: SESSION,
      paper_session_id: SESSION,
      state: { account_id: ACCOUNT, paper_session_id: SESSION, state: "running", last_updated_ms: 1 },
      reconciliation: { consistent: null },
    }))
    await expect(getLifecycle(ACCOUNT, SESSION)).rejects.toThrow("Invalid PAPER response")
  })

  it("turns non-2xx responses into bounded errors without the response body", async () => {
    const secretBody = "server-secret-response-body"
    vi.mocked(fetch).mockResolvedValueOnce(response({ detail: secretBody }, 502))
    const error = await getBalances(ACCOUNT).catch(value => value as PaperApiError)
    expect(error).toBeInstanceOf(PaperApiError)
    expect(error.status).toBe(502)
    expect(error.message).not.toContain(secretBody)
  })
})
