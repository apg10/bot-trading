// ── Entorno ──────────────────────────────────────────────────────────────────

export type Environment = "paper" | "exchange_demo" | "live"

// ── Motor / Estado ───────────────────────────────────────────────────────────

export type EngineState = "stopped" | "paused" | "running"
export type MarketStatus = "connected" | "disconnected" | "reconnecting"

export interface EngineInfo {
  state: EngineState
  environment: Environment
  market_status: MarketStatus
  uptime_seconds: number
  version: string
}

// ── Mercado / Snapshot ───────────────────────────────────────────────────────

export interface Candle {
  timestamp_ms: number
  open: number
  high: number
  low: number
  close: number
  volume: number
}

export interface MarketSnapshot {
  symbol: string
  environment: Environment
  candle: Candle
  bid: number
  ask: number
  timestamp_ms: number
}

// ── Zonas / Pivotes ──────────────────────────────────────────────────────────

export type ZoneOrigin = "support" | "resistance" | "breakout" | "retracement"
export type ZoneStatus =
  | "observing"
  | "candidate"
  | "pending_analysis"
  | "approved"
  | "rejected"
  | "expired"

export interface Zone {
  id: string
  origin: ZoneOrigin
  status: ZoneStatus
  low: number
  high: number
  timestamp_ms: number
  contacts: number
  confirmed_at_ms?: number
  invalidated_at_ms?: number
}

export type PivotType = "swing_high" | "swing_low" | "equal_high" | "equal_low"

export interface Pivot {
  id: string
  pivot_type: PivotType
  price: number
  timestamp_ms: number
  strength: number
}

// ── Anotaciones ───────────────────────────────────────────────────────────────

export type AnnotationType =
  | "entry_proposal"
  | "stop_loss"
  | "take_profit"
  | "zone"
  | "pivot"
  | "note"

export interface Annotation {
  id: string
  annotation_type: AnnotationType
  symbol: string
  content: string
  x_px?: number
  y_px?: number
  zone_id?: string
  timestamp_ms: number
}

// ── Posiciones / Órdenes ─────────────────────────────────────────────────────

export type OrderSide = "buy" | "sell"
export type OrderType = "market" | "limit" | "stop_loss" | "take_profit"
export type OrderStatus =
  | "prepared"
  | "pending_send"
  | "unknown_state"
  | "accepted"
  | "partially_filled"
  | "filled"
  | "cancelled"
  | "rejected"
  | "expired"

export interface Order {
  id: string
  symbol: string
  side: OrderSide
  order_type: OrderType
  status: OrderStatus
  quantity: number
  price?: number
  stop_price?: number
  environment: Environment
  timestamp_ms: number
}

export interface Position {
  symbol: string
  side: OrderSide
  quantity: number
  entry_price: number
  unrealized_pnl: number
  environment: Environment
}

// ── Decisión / Análisis IA ───────────────────────────────────────────────────

export type Action =
  | "enter_long"
  | "enter_short"
  | "hold"
  | "exit_long"
  | "exit_short"
  | "abstain"

export interface AnalysisResult {
  strategy_version: string
  action: Action
  confidence: number
  evidence: string[]
  scenarios: Record<string, unknown>
  invalidation_conditions: string[]
  snapshot_ref?: string
}

export interface DecisionRecord {
  id: string
  symbol: string
  zone_id?: string
  analysis: AnalysisResult
  risk_check_passed: boolean
  order_ref?: string
  timestamp_ms: number
}

// ── Configuración de riesgo ──────────────────────────────────────────────────

export interface RiskLimits {
  max_position_size: number
  max_total_exposure: number
  max_concurrent_positions: number
  max_daily_loss: number
}

// ── Fixture ───────────────────────────────────────────────────────────────────

export interface FixtureData {
  snapshot: MarketSnapshot
  candles: Candle[]
  zones: Zone[]
  pivots: Pivot[]
  annotations: Annotation[]
}
