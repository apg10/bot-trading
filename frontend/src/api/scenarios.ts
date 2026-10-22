// Cliente del contrato N02.  Este módulo solo transporta escenarios advisory;
// no contiene reglas, señales ni ninguna capacidad de ejecución.

import { getApiUrl } from "./market"
import type { CandleData, MarketDataSource, MarketStatus } from "../types/market"

export const N02_MISSING_CONTEXT = ["zones", "checklist", "portfolio"] as const

export type ScenarioErrorCode =
  | "incompatible_interval"
  | "missing_context"
  | "market_incomplete"
  | "snapshot_expired"
  | "busy"
  | "unavailable"
  | "invalid_structure"
  | "snapshot_mismatch"
  | "invalid_evidence"
  | "snapshot_id_conflict"
  | "model_missing"
  | "timeout"
  | "client_closed"
  | "invalid_json"
  | "false_observation"
  | "invalid_invalidation"
  | "invalid_condition"
  | "model_mismatch"
  | "incomplete_generation"
  | "unexpected_reasoning"
  | "request_too_large"
  | "cancelled"

export class ScenarioApiError extends Error {
  readonly code: ScenarioErrorCode

  constructor(code: ScenarioErrorCode) {
    super(code)
    this.name = "ScenarioApiError"
    this.code = code
  }
}

export interface ScenarioMarketState {
  connected: boolean
  history_loaded: boolean
  complete: boolean
  recovering: boolean
  stale: boolean
  pending_gaps: number
}

export interface ScenarioEvidence {
  value: number
  dimension: "price" | "volume" | "oscillator"
  unit: string
  observed_at_ms: number
}

export interface ScenarioSnapshot {
  schema_version: "market_snapshot.v1"
  snapshot_id: string
  symbol: string
  timeframe: "1m"
  environment: "paper"
  data_source: "market_engine"
  captured_at_ms: number
  as_of_close_time_ms: number
  valid_until_ms: number
  market_state: ScenarioMarketState
  evidence: Record<string, ScenarioEvidence>
  missing_data: string[]
}

type ScenarioReference = string
type ScenarioRelation = "gt" | "gte" | "lt" | "lte" | "eq" | "ne"

export interface ScenarioEvidenceCitation {
  evidence_id: ScenarioReference
  observed_value: number
}

export interface ScenarioComparison {
  left_ref: ScenarioReference
  operator: ScenarioRelation
  right_ref: ScenarioReference
}

export interface ScenarioCondition {
  applies_to: "future_closed_snapshot"
  metric_ref: ScenarioReference
  operator: ScenarioRelation
  threshold_ref: ScenarioReference
}

export interface ScenarioAdvisory {
  evidence: ScenarioEvidenceCitation[]
  observations: ScenarioComparison[]
  condition_logic: "all"
  conditions: ScenarioCondition[]
  invalidation_logic: "any"
  invalidations: ScenarioCondition[]
}

export interface ScenarioOutput {
  schema_version: "scenario_analysis.v1"
  snapshot_id: string
  symbol: string
  timeframe: "1m"
  as_of_close_time_ms: number
  valid_until_ms: number
  data_source: "TEST_ONLY" | "market_engine" | "unverified"
  provenance_verified: false
  result_type: "scenario_advisory"
  executable: false
  scenarios: {
    continuation: ScenarioAdvisory
    failure: ScenarioAdvisory
    sideways: ScenarioAdvisory
  }
  missing_data: string[]
}

export interface ScenarioAnalysisResult {
  analysis: ScenarioOutput
  available: true
  result_type: "scenario_advisory"
  requested_model: string
  model_used: string
  duration_ms: number
  total_duration_ms?: number | null
  load_duration_ms?: number | null
  prompt_tokens?: number | null
  generated_tokens?: number | null
  generation_tokens_per_second?: number | null
  data_source: "TEST_ONLY" | "unverified"
  cache_hit?: boolean
  deduplicated?: boolean
  executable: false
  execution_available: false
  summaries: {
    continuation: string
    failure: string
    sideways: string
  }
}

export interface ScenarioSnapshotInput {
  candles: readonly CandleData[]
  status: MarketStatus | null | undefined
  symbol: string
  interval: string | null | undefined
  dataSource: MarketDataSource
  nowMs?: number
}

function record(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value)
}

function finite(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value)
}

function integer(value: unknown): value is number {
  return finite(value) && Number.isSafeInteger(value)
}

function isCandleList(
  value: ScenarioSnapshotInput | readonly CandleData[],
): value is readonly CandleData[] {
  return Array.isArray(value)
}

function publicErrorCode(value: unknown): ScenarioErrorCode {
  const known: ScenarioErrorCode[] = [
    "busy", "snapshot_expired", "market_incomplete", "snapshot_mismatch", "invalid_evidence",
    "snapshot_id_conflict", "model_missing", "timeout", "client_closed", "invalid_json",
    "invalid_structure", "false_observation", "invalid_invalidation", "invalid_condition",
    "model_mismatch", "incomplete_generation", "unexpected_reasoning", "request_too_large",
    "unavailable",
  ]
  if (typeof value === "string" && known.includes(value as ScenarioErrorCode)) return value as ScenarioErrorCode
  return "unavailable"
}

function ensureSnapshotInput(input: ScenarioSnapshotInput): void {
  if (input.interval !== "1m") throw new ScenarioApiError("incompatible_interval")
  if (input.dataSource !== "market_engine") throw new ScenarioApiError("missing_context")
  const status = input.status
  if (!status || status.symbol !== input.symbol || status.interval !== "1m"
    || status.data_source !== "market_engine") throw new ScenarioApiError("missing_context")

  // These fields are emitted by the real market status endpoint.  Optional
  // typing keeps legacy websocket fixtures compilable, but N02 never fills a
  // missing field with a guessed value.
  if (typeof status.connected !== "boolean" || typeof status.history_loaded !== "boolean"
    || typeof status.complete !== "boolean" || typeof status.recovering !== "boolean"
    || typeof status.stale !== "boolean" || !integer(status.pending_gaps)
    || status.pending_gaps < 0 || status.execution_available !== undefined && status.execution_available !== false
    || !integer(status.closed_candles_count) || status.closed_candles_count < 1
    || !integer(status.last_closed_close_time)) throw new ScenarioApiError("missing_context")
  if (!status.connected || !status.history_loaded || !status.complete || status.recovering
    || status.stale || status.pending_gaps !== 0) throw new ScenarioApiError("market_incomplete")
  if (!Array.isArray(input.candles)) throw new ScenarioApiError("missing_context")

  const closed = input.candles.filter(candle => candle.is_closed)
  const last = closed.reduce<CandleData | null>((latest, candle) =>
    latest === null || candle.close_time > latest.close_time ? candle : latest, null)
  if (!last || last.close_time !== status.last_closed_close_time || !finite(last.close) || last.close <= 0
    || !integer(last.close_time) || last.close_time % 60_000 !== 59_999) {
    throw new ScenarioApiError("missing_context")
  }
}

/** Construye el mínimo snapshot permitido por N02 desde contexto ya recibido. */
export function buildScenarioSnapshot(input: ScenarioSnapshotInput): ScenarioSnapshot
export function buildScenarioSnapshot(
  candles: readonly CandleData[], status: MarketStatus | null | undefined,
  symbol: string, interval: string | null | undefined, dataSource: MarketDataSource,
  nowMs?: number,
): ScenarioSnapshot
export function buildScenarioSnapshot(
  inputOrCandles: ScenarioSnapshotInput | readonly CandleData[],
  status?: MarketStatus | null,
  symbol?: string,
  interval?: string | null,
  dataSource?: MarketDataSource,
  nowMs = Date.now(),
): ScenarioSnapshot {
  const input: ScenarioSnapshotInput = isCandleList(inputOrCandles)
    ? { candles: inputOrCandles, status, symbol: symbol ?? "", interval, dataSource: dataSource ?? "unknown", nowMs }
    : inputOrCandles
  const capturedAt = input.nowMs ?? Date.now()
  ensureSnapshotInput(input)
  if (!integer(capturedAt) || capturedAt < 0) throw new ScenarioApiError("missing_context")

  const last = input.candles.filter(candle => candle.is_closed)
    .reduce<CandleData | null>((latest, candle) => latest === null || candle.close_time > latest.close_time ? candle : latest, null)!
  const validUntil = last.close_time + 90_000
  if (capturedAt < last.close_time || capturedAt > validUntil) throw new ScenarioApiError("snapshot_expired")

  const safeSymbol = input.symbol.replace("/", "_")
  return {
    schema_version: "market_snapshot.v1",
    snapshot_id: `n02:${safeSymbol}:${last.close_time}`,
    symbol: input.symbol,
    timeframe: "1m",
    environment: "paper",
    data_source: "market_engine",
    captured_at_ms: capturedAt,
    as_of_close_time_ms: last.close_time,
    valid_until_ms: validUntil,
    market_state: {
      connected: input.status!.connected,
      history_loaded: input.status!.history_loaded!,
      complete: input.status!.complete!,
      recovering: input.status!.recovering!,
      stale: input.status!.stale!,
      pending_gaps: input.status!.pending_gaps!,
    },
    evidence: {
      last_close: {
        value: last.close,
        dimension: "price",
        unit: input.symbol.split("/")[1]!,
        observed_at_ms: last.close_time,
      },
    },
    // N02 has no zones, checklist or portfolio subsystem in this UI.  These
    // are declarations of absence, never synthetic evidence.
    missing_data: [...N02_MISSING_CONTEXT],
  }
}

function advisory(value: unknown): value is ScenarioAdvisory {
  if (!record(value) || !Array.isArray(value.evidence) || !Array.isArray(value.observations)
    || value.condition_logic !== "all" || !Array.isArray(value.conditions)
    || value.invalidation_logic !== "any" || !Array.isArray(value.invalidations)) return false
  const citation = (item: unknown) => record(item) && typeof item.evidence_id === "string" && finite(item.observed_value)
  const relation = (item: unknown) => record(item) && typeof item.left_ref === "string"
    && typeof item.right_ref === "string" && ["gt", "gte", "lt", "lte", "eq", "ne"].includes(String(item.operator))
  const condition = (item: unknown) => record(item) && item.applies_to === "future_closed_snapshot"
    && typeof item.metric_ref === "string" && typeof item.threshold_ref === "string"
    && ["gt", "gte", "lt", "lte", "eq", "ne"].includes(String(item.operator))
  return value.evidence.length > 0 && value.evidence.length <= 4 && value.observations.length <= 3
    && value.conditions.length > 0 && value.conditions.length <= 3
    && value.invalidations.length > 0 && value.invalidations.length <= 3
    && value.evidence.every(citation) && value.observations.every(relation)
    && value.conditions.every(condition) && value.invalidations.every(condition)
}

/** Valida identidad, vigencia y contrato advisory sin exponer la respuesta cruda. */
export function validateScenarioAnalysisResult(
  value: unknown, snapshot: ScenarioSnapshot, nowMs = Date.now(),
): ScenarioAnalysisResult {
  if (!record(value) || value.available !== true || value.result_type !== "scenario_advisory"
    || value.executable !== false || value.execution_available !== false
    || !record(value.analysis) || !record(value.analysis.scenarios)
    || value.data_source !== "TEST_ONLY" && value.data_source !== "unverified"
    || typeof value.requested_model !== "string" || !value.requested_model
    || typeof value.model_used !== "string" || !value.model_used
    || !integer(value.duration_ms) || value.duration_ms < 0
    || !record(value.summaries)) throw new ScenarioApiError("invalid_structure")

  const analysis = value.analysis
  const scenarios = analysis.scenarios as ScenarioOutput["scenarios"]
  if (analysis.schema_version !== "scenario_analysis.v1" || analysis.snapshot_id !== snapshot.snapshot_id
    || analysis.symbol !== snapshot.symbol || analysis.timeframe !== snapshot.timeframe
    || analysis.as_of_close_time_ms !== snapshot.as_of_close_time_ms
    || analysis.valid_until_ms !== snapshot.valid_until_ms
    || (analysis.data_source !== "TEST_ONLY" && analysis.data_source !== "market_engine" && analysis.data_source !== "unverified")
    || analysis.provenance_verified !== false || analysis.result_type !== "scenario_advisory"
    || analysis.executable !== false || !Array.isArray(analysis.missing_data)
    || JSON.stringify([...analysis.missing_data].sort()) !== JSON.stringify([...snapshot.missing_data].sort())
     || !advisory(scenarios.continuation) || !advisory(scenarios.failure)
     || !advisory(scenarios.sideways)) throw new ScenarioApiError("snapshot_mismatch")

  const summaries = value.summaries
  if (typeof summaries.continuation !== "string" || !summaries.continuation
    || typeof summaries.failure !== "string" || !summaries.failure
    || typeof summaries.sideways !== "string" || !summaries.sideways) {
    throw new ScenarioApiError("invalid_structure")
  }
  if (!integer(nowMs) || nowMs < snapshot.captured_at_ms || nowMs > snapshot.valid_until_ms) {
    throw new ScenarioApiError("snapshot_expired")
  }
  return value as unknown as ScenarioAnalysisResult
}

export async function requestScenarioAnalysis(
  snapshot: ScenarioSnapshot, signal?: AbortSignal,
): Promise<ScenarioAnalysisResult> {
  try {
    const response = await fetch(getApiUrl("/api/bot/ai/scenarios"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ snapshot }),
      signal,
    })
    if (!response.ok) {
      let code: unknown
      try {
        const body = await response.json() as unknown
        if (record(body) && record(body.detail)) code = body.detail.code
      } catch {
        // El cuerpo HTTP no forma parte del contrato público del cliente.
      }
      throw new ScenarioApiError(publicErrorCode(code))
    }
    let body: unknown
    try {
      body = await response.json()
    } catch {
      throw new ScenarioApiError("invalid_structure")
    }
    return validateScenarioAnalysisResult(body, snapshot)
  } catch (error: unknown) {
    if (error instanceof ScenarioApiError) throw error
    if (error && typeof error === "object" && "name" in error
      && (error as { name?: unknown }).name === "AbortError") throw new ScenarioApiError("cancelled")
    throw new ScenarioApiError("unavailable")
  }
}

// Alias descriptivo para consumidores que prefieren el nombre de cliente.
export const fetchScenarioAnalysis = requestScenarioAnalysis
