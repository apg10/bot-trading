import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { createElement } from "react"
import { renderToStaticMarkup } from "react-dom/server"
import {
  getComparison,
  getCapitalCurve,
  getCostBreakdown,
  getNormalizedResult,
  LaboratoryApiError,
  type CapitalCurveReport,
  type CostBreakdownReport,
  type ExperimentComparisonReport,
  type NormalizedExperimentResult,
} from "../src/api/laboratory"
import { LaboratoryCapitalCurve, LaboratoryComparisonResult, LaboratoryCostBreakdown } from "../src/components/LaboratoryView"

const result = (variantId: string, status: "available" | "undefined" | "insufficient_data", value: string | null): NormalizedExperimentResult => ({
  schema_version: "normalized_experiment_result.v1",
  result_id: `result-${variantId}`,
  run_id: `run-${variantId}`,
  origin_id: `origin-${variantId}`,
  result_version: "v1",
  experiment_ref: { experiment_id: "exp/one", experiment_version: "v 1" },
  variant_id: variantId,
  dataset_ref: "dataset/lab",
  strategy_config: {},
  code_ref: "code/lab",
  engine_ref: "engine/lab",
  risk_ref: "risk/lab",
  costs_ref: "costs/lab",
  exposure_ref: "exposure/lab",
  effective_periods: [{ purpose: "test", start_ms: 1, end_ms: 2 }],
  seed: 7,
  metrics: [{
    metric_id: "return", unit: "percent", status, value,
    reason: status === "available" ? null : `reason-${status}`,
  }],
})

const report: ExperimentComparisonReport = {
  comparison: {
    schema_version: "experiment_comparison.v1",
    comparison_id: "comparison/one",
    comparison_version: "v 1",
    hypothesis: "A hypothesis",
    normalized_results: [result("A", "available", "0.00000000000000000001"), result("B", "undefined", null), result("C", "insufficient_data", null)],
  },
  aggregates: [{
    metric_id: "return", unit: "percent", available_count: 1, undefined_count: 1,
    insufficient_data_count: 1, mean: "0.00000000000000000001", minimum: "0.00000000000000000001", maximum: "0.00000000000000000001",
  }],
}

const curveReport: CapitalCurveReport = {
  schema_version: "capital_curve_report.v1",
  account_id: "account/one",
  session_id: "session/one",
  quote_asset: "USDT",
  balance_kind: "cash",
  status: "available",
  initial_balance: "100.00000000000000000001",
  reason: null,
  points: [
    { timestamp_ms: 1, fill_id: "fill-a", balance: "89.90000000000000000001", peak_balance: "100.00000000000000000001", drawdown: "10.10000000000000000000" },
    { timestamp_ms: 2, fill_id: "fill-b", balance: "108.90000000000000000001", peak_balance: "108.90000000000000000001", drawdown: "0" },
    { timestamp_ms: 3, fill_id: "fill-c", balance: "108.80000000000000000001", peak_balance: "108.90000000000000000001", drawdown: "0.10000000000000000000" },
  ],
}

const costReport: CostBreakdownReport = {
  schema_version: "cost_breakdown_report.v1",
  account_id: "account/one",
  session_id: "session/one",
  quote_asset: "USDT",
  status: "available",
  reason: null,
  fills: [
    {
      fill_id: "fill-a", order_id: "order-a", timestamp_ms: 1, side: "BUY",
      qty: "0.12345678901234567890", price: "10.00000000000000000001", notional: "1.23456789012345678901",
      commission: { amount: "0.01000000000000000001", status: "available", reason: null },
      slippage: { amount: "0.02000000000000000002", status: "available", reason: null },
      total_cost: { amount: "0.03000000000000000003", status: "available", reason: null }, cost_model: "MODELLED",
    },
    {
      fill_id: "fill-b", order_id: "order-b", timestamp_ms: 2, side: "SELL",
      qty: "1.00", price: "20.00", notional: "20.00",
      commission: null,
      slippage: { amount: null, status: "insufficient_data", reason: "reference price is unavailable" },
      total_cost: null, cost_model: "UNKNOWN",
    },
  ],
}

function response(body: unknown, status = 200): Response {
  return { ok: status >= 200 && status < 300, status, json: async () => body } as Response
}

beforeEach(() => {
  vi.stubGlobal("window", { location: { origin: "http://frontend.test" } })
  vi.stubGlobal("fetch", vi.fn())
})

afterEach(() => {
  vi.unstubAllGlobals()
})

describe("laboratory read client", () => {
  it("uses encoded paths/queries and GET only", async () => {
    vi.mocked(fetch)
      .mockResolvedValueOnce(response(result("A", "available", "1.23000000000000000001")))
      .mockResolvedValueOnce(response(report))

    await getNormalizedResult("result/id")
    await getComparison("exp/one", "v 1", "comparison/one", "v 1")

    expect(vi.mocked(fetch).mock.calls.map(([url]) => url)).toEqual([
      "http://frontend.test/api/laboratory/results/result%2Fid",
      "http://frontend.test/api/laboratory/comparisons/exp%2Fone/v%201?comparison_id=comparison%2Fone&comparison_version=v%201",
    ])
    expect(vi.mocked(fetch).mock.calls.every(([, init]) => init?.method === "GET")).toBe(true)
  })

  it("preserves Decimal strings and explicit unavailable states", async () => {
    vi.mocked(fetch).mockResolvedValueOnce(response(report))
    const loaded = await getComparison("exp", "v1", "cmp", "v1")
    expect(loaded.comparison.normalized_results[0].metrics[0].value).toBe("0.00000000000000000001")
    expect(typeof loaded.comparison.normalized_results[0].metrics[0].value).toBe("string")
    expect(loaded.comparison.normalized_results[1].metrics[0].status).toBe("undefined")
    expect(loaded.comparison.normalized_results[2].metrics[0].status).toBe("insufficient_data")
  })

  it("rejects non-2xx and malformed responses without response bodies", async () => {
    const secret = "private server response"
    vi.mocked(fetch)
      .mockResolvedValueOnce(response({ detail: secret }, 502))
      .mockResolvedValueOnce(response({ detail: secret }))
    const httpError = await getNormalizedResult("result").catch(error => error as LaboratoryApiError)
    expect(httpError).toBeInstanceOf(LaboratoryApiError)
    expect(httpError.message).not.toContain(secret)
    await expect(getComparison("exp", "v1", "cmp", "v1")).rejects.toThrow("Invalid laboratory response")
  })

  it("keeps capital and cost decimals as strings at the API boundary", async () => {
    vi.mocked(fetch)
      .mockResolvedValueOnce(response(curveReport))
      .mockResolvedValueOnce(response(costReport))
    const curve = await getCapitalCurve("account/one", "session/one")
    const costs = await getCostBreakdown("account/one", "session/one")
    expect(typeof curve.points[0]?.balance).toBe("string")
    expect(curve.points[0]?.balance).toBe("89.90000000000000000001")
    expect(typeof costs.fills[0]?.qty).toBe("string")
    expect(costs.fills[0]?.qty).toBe("0.12345678901234567890")
  })
})

describe("laboratory comparison rendering", () => {
  it("shows available, undefined, insufficient_data, and zero-safe aggregates without winner/progress text", () => {
    const html = renderToStaticMarkup(createElement(LaboratoryComparisonResult, { report }))
    expect(html).toContain("0.00000000000000000001")
    expect(html).toContain("undefined")
    expect(html).toContain("insufficient_data")
    expect(html).toContain("Disponibles")
    expect(html).not.toMatch(/winner|ranking|recommendation|progress|%/i)
  })

  it("renders an em dash when no aggregate observation is available", () => {
    const emptyAggregateReport: ExperimentComparisonReport = {
      ...report,
      aggregates: [{ ...report.aggregates[0], available_count: 0, undefined_count: 2, insufficient_data_count: 1, mean: null, minimum: null, maximum: null }],
    }
    expect(renderToStaticMarkup(createElement(LaboratoryComparisonResult, { report: emptyAggregateReport }))).toContain("—")
  })
})

describe("laboratory capital and cost rendering", () => {
  it("renders the capital curve SVG with deterministic point positions and drawdown", () => {
    const html = renderToStaticMarkup(createElement(LaboratoryCapitalCurve, { report: curveReport }))
    expect(html).toContain('aria-label="Capital curve"')
    expect(html).toContain('cx="10"')
    expect(html).toContain('cx="160"')
    expect(html).toContain('cx="310"')
    expect(html).toContain('data-balance="89.90000000000000000001"')
    expect(html).toContain("10.10000000000000000000")
    expect(html).toContain("Drawdown")
  })

  it("renders commission, slippage, and total per fill without coercing decimals", () => {
    const html = renderToStaticMarkup(createElement(LaboratoryCostBreakdown, { report: costReport }))
    expect(html).toContain("Commission")
    expect(html).toContain("Slippage")
    expect(html).toContain("Total")
    expect(html).toContain("0.01000000000000000001")
    expect(html).toContain("0.02000000000000000002")
    expect(html).toContain("0.03000000000000000003")
    expect(html).toContain("insufficient_data")
    expect(html).toContain("reference price is unavailable")
    expect(html).toContain("0.12345678901234567890")
    expect(html).not.toContain("0.12345678901234568")
  })

  it("displays insufficient projection states rather than converting them to zero", () => {
    const insufficientCurve: CapitalCurveReport = { ...curveReport, status: "insufficient_data", points: [], reason: "no applied fills are available" }
    const insufficientCosts: CostBreakdownReport = { ...costReport, status: "insufficient_data", fills: [], reason: "no applied fills are available" }
    const html = renderToStaticMarkup(createElement("div", null,
      createElement(LaboratoryCapitalCurve, { report: insufficientCurve }),
      createElement(LaboratoryCostBreakdown, { report: insufficientCosts }),
    ))
    expect(html).toContain("insufficient_data: no applied fills are available")
    expect(html).not.toContain("slippage>0<")
  })
})
