// Read-only client for the laboratory projections.
// Decimal values intentionally remain strings at this boundary.

import { getApiUrl } from "./market"

export type LaboratoryMetricStatus = "available" | "undefined" | "insufficient_data"

export interface LaboratoryPeriod {
  readonly purpose: "design" | "validation" | "test"
  readonly start_ms: number
  readonly end_ms: number
}

export interface LaboratoryExperimentRef {
  readonly experiment_id: string
  readonly experiment_version: string
}

export interface NormalizedMetric {
  readonly metric_id: string
  readonly unit: string | null
  readonly status: LaboratoryMetricStatus
  readonly value?: string | null
  readonly reason: string | null
}

export interface NormalizedExperimentResult {
  readonly schema_version: "normalized_experiment_result.v1"
  readonly result_id: string
  readonly run_id: string
  readonly origin_id: string
  readonly result_version: string
  readonly experiment_ref: LaboratoryExperimentRef
  readonly variant_id: string
  readonly dataset_ref: string
  readonly strategy_config: Record<string, unknown>
  readonly code_ref: string
  readonly engine_ref: string
  readonly risk_ref: string
  readonly costs_ref: string
  readonly exposure_ref: string
  readonly effective_periods: readonly LaboratoryPeriod[]
  readonly seed: number | null
  readonly metrics: readonly NormalizedMetric[]
}

export interface NormalizedMetricAggregate {
  readonly metric_id: string
  readonly unit: string | null
  readonly available_count: number
  readonly undefined_count: number
  readonly insufficient_data_count: number
  readonly mean: string | null
  readonly minimum: string | null
  readonly maximum: string | null
}

export interface ExperimentComparison {
  readonly schema_version: "experiment_comparison.v1"
  readonly comparison_id: string
  readonly comparison_version: string
  readonly hypothesis: string
  readonly normalized_results: readonly NormalizedExperimentResult[]
}

export interface ExperimentComparisonReport {
  readonly comparison: ExperimentComparison
  readonly aggregates: readonly NormalizedMetricAggregate[]
}

export type LaboratoryErrorCode = "http" | "unavailable" | "invalid_response"

/** Public errors are bounded and never contain an HTTP response body. */
export class LaboratoryApiError extends Error {
  readonly code: LaboratoryErrorCode
  readonly status: number | undefined

  constructor(code: LaboratoryErrorCode, message: string, status?: number) {
    super(message)
    this.name = "LaboratoryApiError"
    this.code = code
    this.status = status
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value)
}

function nonEmptyString(value: unknown): value is string {
  return typeof value === "string" && value.length > 0
}

function integer(value: unknown): value is number {
  return typeof value === "number" && Number.isSafeInteger(value)
}

function invalidResponse(): never {
  throw new LaboratoryApiError("invalid_response", "Invalid laboratory response")
}

function period(value: unknown): value is LaboratoryPeriod {
  if (!isRecord(value) || !["design", "validation", "test"].includes(String(value.purpose))) return false
  return integer(value.start_ms) && value.start_ms >= 0
    && integer(value.end_ms) && value.end_ms > value.start_ms
}

function experimentRef(value: unknown): value is LaboratoryExperimentRef {
  return isRecord(value) && nonEmptyString(value.experiment_id)
    && nonEmptyString(value.experiment_version)
}

function metric(value: unknown): value is NormalizedMetric {
  if (!isRecord(value) || !nonEmptyString(value.metric_id)
    || (value.unit !== null && !nonEmptyString(value.unit))
    || !["available", "undefined", "insufficient_data"].includes(String(value.status))) return false

  if (value.status === "available") {
    return nonEmptyString(value.unit) && typeof value.value === "string"
      && nonEmptyString(value.value)
      && (value.reason === null || value.reason === undefined || nonEmptyString(value.reason))
  }
  return nonEmptyString(value.reason)
    && (value.value === null || value.value === undefined)
}

function normalizedResult(value: unknown): value is NormalizedExperimentResult {
  if (!isRecord(value) || value.schema_version !== "normalized_experiment_result.v1"
    || !nonEmptyString(value.result_id) || !nonEmptyString(value.run_id)
    || !nonEmptyString(value.origin_id) || !nonEmptyString(value.result_version)
    || !experimentRef(value.experiment_ref) || !nonEmptyString(value.variant_id)
    || !nonEmptyString(value.dataset_ref) || !isRecord(value.strategy_config)
    || !nonEmptyString(value.code_ref) || !nonEmptyString(value.engine_ref)
    || !nonEmptyString(value.risk_ref) || !nonEmptyString(value.costs_ref)
    || !nonEmptyString(value.exposure_ref) || !Array.isArray(value.effective_periods)
    || value.effective_periods.length === 0 || !value.effective_periods.every(period)
    || (value.seed !== null && value.seed !== undefined && !integer(value.seed))
    || !Array.isArray(value.metrics) || value.metrics.length === 0
    || !value.metrics.every(metric)) return false

  const typedMetrics = value.metrics as NormalizedMetric[]
  const metricIds = typedMetrics.map(item => item.metric_id)
  return new Set(metricIds).size === metricIds.length
}

function decimalOrNull(value: unknown): value is string | null {
  return value === null || nonEmptyString(value)
}

function aggregate(value: unknown): value is NormalizedMetricAggregate {
  if (!isRecord(value) || !nonEmptyString(value.metric_id)
    || (value.unit !== null && !nonEmptyString(value.unit))
    || !integer(value.available_count) || value.available_count < 0
    || !integer(value.undefined_count) || value.undefined_count < 0
    || !integer(value.insufficient_data_count) || value.insufficient_data_count < 0
    || !decimalOrNull(value.mean) || !decimalOrNull(value.minimum)
    || !decimalOrNull(value.maximum)) return false
  if (value.available_count === 0) {
    return value.mean === null && value.minimum === null && value.maximum === null
  }
  return value.mean !== null && value.minimum !== null && value.maximum !== null
}

function validateNormalizedResult(value: unknown): NormalizedExperimentResult {
  if (!normalizedResult(value)) invalidResponse()
  return value
}

function validateComparison(value: unknown): ExperimentComparisonReport {
  if (!isRecord(value) || !isRecord(value.comparison) || !Array.isArray(value.aggregates)) {
    invalidResponse()
  }
  const comparison = value.comparison
  if (comparison.schema_version !== "experiment_comparison.v1"
    || !nonEmptyString(comparison.comparison_id)
    || !nonEmptyString(comparison.comparison_version)
    || !nonEmptyString(comparison.hypothesis)
    || !Array.isArray(comparison.normalized_results)
    || comparison.normalized_results.length < 3
    || !comparison.normalized_results.every(normalizedResult)
    || !value.aggregates.every(aggregate)) invalidResponse()

  const results = comparison.normalized_results as NormalizedExperimentResult[]
  const aggregates = value.aggregates as NormalizedMetricAggregate[]
  const first = results[0]
  const resultIds = results.map(result => result.variant_id)
  if (!first || new Set(resultIds).size !== resultIds.length) invalidResponse()
  const metricIds = first.metrics.map(item => item.metric_id)
  const metricUnits = new Map(first.metrics.map(item => [item.metric_id, item.unit]))
  if (results.some(result => result.experiment_ref.experiment_id !== first.experiment_ref.experiment_id
    || result.experiment_ref.experiment_version !== first.experiment_ref.experiment_version
    || result.effective_periods.length !== first.effective_periods.length
    || result.metrics.length !== first.metrics.length
    || result.metrics.some(item => !metricUnits.has(item.metric_id)
      || metricUnits.get(item.metric_id) !== item.unit))) invalidResponse()

  if (aggregates.length !== metricIds.length
    || new Set(aggregates.map(item => item.metric_id)).size !== aggregates.length
    || aggregates.some(item => !metricUnits.has(item.metric_id)
      || metricUnits.get(item.metric_id) !== item.unit
      || item.available_count + item.undefined_count + item.insufficient_data_count !== results.length)) {
    invalidResponse()
  }
  return value as unknown as ExperimentComparisonReport
}

async function request(path: string): Promise<unknown> {
  let response: Response
  try {
    response = await fetch(getApiUrl(path), { method: "GET" })
  } catch {
    throw new LaboratoryApiError("unavailable", "Laboratory request failed")
  }
  if (!response.ok) {
    throw new LaboratoryApiError("http", `Laboratory request failed (HTTP ${response.status})`, response.status)
  }
  try {
    return await response.json() as unknown
  } catch {
    throw new LaboratoryApiError("invalid_response", "Invalid laboratory response")
  }
}

export async function getNormalizedResult(resultId: string): Promise<NormalizedExperimentResult> {
  return validateNormalizedResult(await request(`/api/laboratory/results/${encodeURIComponent(resultId)}`))
}

export async function getComparison(
  experimentId: string,
  experimentVersion: string,
  comparisonId: string,
  comparisonVersion: string,
): Promise<ExperimentComparisonReport> {
  const path = `/api/laboratory/comparisons/${encodeURIComponent(experimentId)}/${encodeURIComponent(experimentVersion)}`
    + `?comparison_id=${encodeURIComponent(comparisonId)}&comparison_version=${encodeURIComponent(comparisonVersion)}`
  return validateComparison(await request(path))
}
