import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { createElement } from "react"
import { renderToStaticMarkup } from "react-dom/server"
import {
  getComparison,
  getNormalizedResult,
  LaboratoryApiError,
  type ExperimentComparisonReport,
  type NormalizedExperimentResult,
} from "../src/api/laboratory"
import { LaboratoryComparisonResult } from "../src/components/LaboratoryView"

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
