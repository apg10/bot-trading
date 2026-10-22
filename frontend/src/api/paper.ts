// Read-only HTTP adapter for the PAPER projections.

import { getApiUrl } from "./market"

export type PaperDecimal = string

export interface PaperReadItem {
  readonly [key: string]: unknown
}

export interface PaperBalance extends PaperReadItem {
  readonly asset: string
  readonly total: PaperDecimal
  readonly available: PaperDecimal
  readonly reserved: PaperDecimal
}

export interface PaperCollectionResponse<T extends PaperReadItem = PaperReadItem> {
  readonly account_id: string
  readonly environment: "paper"
  readonly session_id?: string | null
  readonly [key: string]: unknown
  readonly items: readonly T[]
}

export interface PaperBalancesResponse extends Omit<PaperCollectionResponse<PaperBalance>, "items"> {
  readonly balances: readonly PaperBalance[]
}

export interface PaperPositionsResponse extends Omit<PaperCollectionResponse, "items"> {
  readonly positions: readonly PaperReadItem[]
}

export interface PaperOrdersResponse extends Omit<PaperCollectionResponse, "items"> {
  readonly orders: readonly PaperReadItem[]
}

export interface PaperFillsResponse extends Omit<PaperCollectionResponse, "items"> {
  readonly fills: readonly PaperReadItem[]
}

export interface PaperActivityResponse extends Omit<PaperCollectionResponse, "items"> {
  readonly activity: readonly PaperReadItem[]
}

export interface PaperLifecycleResponse {
  readonly account_id: string
  readonly environment: "paper"
  readonly session_id: string
  readonly paper_session_id: string
  readonly state: PaperReadItem
  readonly reconciliation: PaperReadItem | null
}

/** Public error intentionally contains no server response body. */
export class PaperApiError extends Error {
  readonly status: number | undefined

  constructor(message: string, status?: number) {
    super(message)
    this.name = "PaperApiError"
    this.status = status
  }
}

const DECIMAL_KEYS = new Set([
  "total", "available", "reserved", "quantity", "cost", "total_qty", "total_cost",
  "requested_qty", "price", "trigger_price", "max_quote_amount", "qty", "amount",
  "executed_qty", "remaining_qty", "avg_entry_price", "mark_price", "unrealized_pnl",
  "open_position_qty", "remaining_protected_qty",
])

function isRecord(value: unknown): value is PaperReadItem {
  return typeof value === "object" && value !== null && !Array.isArray(value)
}

function invalidResponse(): never {
  throw new PaperApiError("Invalid PAPER response")
}

function assertRecord(value: unknown): asserts value is PaperReadItem {
  if (!isRecord(value)) invalidResponse()
}

function assertDecimalStrings(value: unknown): void {
  if (Array.isArray(value)) {
    value.forEach(assertDecimalStrings)
    return
  }
  if (!isRecord(value)) return
  for (const [key, child] of Object.entries(value)) {
    if (DECIMAL_KEYS.has(key) && child !== null && typeof child !== "string") {
      invalidResponse()
    }
    assertDecimalStrings(child)
  }
}

type ItemIdentityMode = "strict" | "projection" | "optional"

function assertItemIdentity(
  item: PaperReadItem,
  accountId: string,
  sessionId: string | undefined,
  mode: ItemIdentityMode,
): void {
  const hasAccountId = "account_id" in item
  const hasEnvironment = "environment" in item
  const hasPaperAccountId = "paper_account_id" in item

  if (mode === "strict" || mode === "projection") {
    if (!hasAccountId || !hasEnvironment) invalidResponse()
  }
  if (hasAccountId && item.account_id !== accountId) invalidResponse()
  if (hasEnvironment && item.environment !== "paper") invalidResponse()
  if (mode === "strict" && !hasPaperAccountId) invalidResponse()
  if (hasPaperAccountId && item.paper_account_id !== accountId) invalidResponse()

  const hasSessionId = "session_id" in item
  const hasPaperSessionId = "paper_session_id" in item
  if (mode === "strict") {
    if (hasSessionId && hasPaperSessionId) {
      if (sessionId !== undefined && item.session_id !== sessionId) invalidResponse()
      if (item.session_id !== item.paper_session_id) invalidResponse()
    } else if (sessionId !== undefined) {
      invalidResponse()
    }
  } else {
    if (hasSessionId && hasPaperSessionId && item.session_id !== item.paper_session_id) {
      invalidResponse()
    }
    if (sessionId !== undefined) {
      if (!hasSessionId || item.session_id !== sessionId) invalidResponse()
      if (hasPaperSessionId && item.paper_session_id !== sessionId) invalidResponse()
    }
  }
}

function assertEnvelope(value: unknown, accountId: string): asserts value is PaperReadItem {
  assertRecord(value)
  if (value.environment !== "paper" || value.account_id !== accountId) invalidResponse()
  assertDecimalStrings(value)
}

function assertCollection(
  value: unknown,
  key: string,
  accountId: string,
  sessionId?: string,
  itemIdentityMode: ItemIdentityMode = "strict",
): PaperReadItem[] {
  assertEnvelope(value, accountId)
  const items = value[key]
  if (!Array.isArray(items)) invalidResponse()
  if (sessionId !== undefined && value.session_id !== sessionId) invalidResponse()
  for (const item of items) {
    assertRecord(item)
    assertItemIdentity(item, accountId, sessionId, itemIdentityMode)
  }
  return items
}

function withSession(path: string, sessionId?: string): string {
  return sessionId === undefined ? path : `${path}?session_id=${encodeURIComponent(sessionId)}`
}

async function request(path: string): Promise<unknown> {
  let response: Response
  try {
    response = await fetch(getApiUrl(path), { method: "GET" })
  } catch {
    throw new PaperApiError("PAPER request failed")
  }
  if (!response.ok) throw new PaperApiError(`PAPER request failed (HTTP ${response.status})`, response.status)
  try {
    return await response.json() as unknown
  } catch {
    throw new PaperApiError("Invalid PAPER response")
  }
}

function accountPath(accountId: string, endpoint: string): string {
  return `/api/paper/accounts/${encodeURIComponent(accountId)}/${endpoint}`
}

export async function getBalances(accountId: string): Promise<PaperBalancesResponse> {
  const data = await request(accountPath(accountId, "balances"))
  const balances = assertCollection(data, "balances", accountId, undefined, "optional")
  for (const balance of balances) {
    if (
      typeof balance.asset !== "string"
      || typeof balance.total !== "string"
      || typeof balance.available !== "string"
      || typeof balance.reserved !== "string"
    ) invalidResponse()
  }
  return data as PaperBalancesResponse
}

export async function getPositions(accountId: string): Promise<PaperPositionsResponse> {
  const data = await request(accountPath(accountId, "positions"))
  assertCollection(data, "positions", accountId, undefined, "projection")
  return data as PaperPositionsResponse
}

export async function getOrders(accountId: string, sessionId?: string): Promise<PaperOrdersResponse> {
  const data = await request(withSession(accountPath(accountId, "orders"), sessionId))
  assertCollection(data, "orders", accountId, sessionId)
  return data as PaperOrdersResponse
}

export async function getFills(accountId: string, sessionId?: string): Promise<PaperFillsResponse> {
  const data = await request(withSession(accountPath(accountId, "fills"), sessionId))
  assertCollection(data, "fills", accountId, sessionId, "projection")
  return data as PaperFillsResponse
}

export async function getLifecycle(accountId: string, sessionId: string): Promise<PaperLifecycleResponse> {
  const data = await request(withSession(accountPath(accountId, "lifecycle"), sessionId))
  assertEnvelope(data, accountId)
  if (
    data.session_id !== sessionId
    || data.paper_session_id !== sessionId
    || typeof data.state !== "object" || data.state === null || Array.isArray(data.state)
    || (data.reconciliation !== null && (typeof data.reconciliation !== "object" || data.reconciliation === null || Array.isArray(data.reconciliation)))
  ) invalidResponse()
  const state = data.state as Record<string, unknown>
  if (typeof state.account_id !== "string" || typeof state.paper_session_id !== "string") invalidResponse()
  if (typeof state.state !== "string") invalidResponse()
  if (typeof state.last_updated_ms === "string") {
    const parsedLastUpdatedMs = Number(state.last_updated_ms)
    if (state.last_updated_ms.trim() === "" || !Number.isFinite(parsedLastUpdatedMs)) invalidResponse()
    state.last_updated_ms = parsedLastUpdatedMs
  } else if (typeof state.last_updated_ms !== "number") {
    invalidResponse()
  }
  if (state.account_id !== accountId || state.paper_session_id !== sessionId) invalidResponse()
  if (data.reconciliation !== null) {
    const rec = data.reconciliation as Record<string, unknown>
    if (typeof rec.consistent !== "boolean") invalidResponse()
  }
  return data as unknown as PaperLifecycleResponse
}

export async function getActivity(accountId: string, sessionId?: string): Promise<PaperActivityResponse> {
  const data = await request(withSession(accountPath(accountId, "activity"), sessionId))
  assertCollection(data, "activity", accountId, sessionId, "projection")
  return data as PaperActivityResponse
}
