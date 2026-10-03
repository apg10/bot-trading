"""Contratos Pydantic para estado del motor, snapshot y anotaciones."""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Optional

from pydantic import BaseModel, Field, field_validator


# ── Entorno ──────────────────────────────────────────────────────────────────

class Environment(StrEnum):
    PAPER = "paper"
    EXCHANGE_DEMO = "exchange_demo"
    LIVE = "live"


# ── Motor / Estado ───────────────────────────────────────────────────────────

class EngineState(StrEnum):
    STOPPED = "stopped"
    PAUSED = "paused"
    RUNNING = "running"


class MarketStatus(StrEnum):
    CONNECTED = "connected"
    DISCONNECTED = "disconnected"
    RECONNECTING = "reconnecting"


class EngineInfo(BaseModel):
    """Estado actual del motor de trading."""

    state: EngineState
    environment: Environment
    market_status: MarketStatus
    uptime_seconds: float = 0.0
    version: str = "0.1.0"


# ── Mercado / Snapshot ───────────────────────────────────────────────────────

class Candle(BaseModel):
    """Una barra de precios (OHLCV)."""

    timestamp_ms: int = Field(..., ge=0)
    open: float
    high: float
    low: float
    close: float
    volume: float = Field(..., ge=0)

    @field_validator("high")
    @classmethod
    def high_gte_all(cls, v: float, info) -> float:
        if hasattr(info, "data"):
            data = info.data
            low = data.get("low", -float("inf"))
            open_p = data.get("open", -float("inf"))
            close = data.get("close", -float("inf"))
            assert v >= low and v >= open_p and v >= close, "high debe ser >= low/open/close"
        return v

    @field_validator("low")
    @classmethod
    def low_lte_all(cls, v: float, info) -> float:
        if hasattr(info, "data"):
            data = info.data
            high = data.get("high", float("inf"))
            open_p = data.get("open", float("inf"))
            close = data.get("close", float("inf"))
            assert v <= high and v <= open_p and v <= close, "low debe ser <= high/open/close"
        return v

    @field_validator("open")
    @classmethod
    def open_check(cls, v: float, info) -> float:
        if hasattr(info, "data"):
            data = info.data
            low = data.get("low", -float("inf"))
            high = data.get("high", float("inf"))
            assert v >= low and v <= high, "open debe estar entre low y high"
        return v

    @field_validator("close")
    @classmethod
    def close_check(cls, v: float, info) -> float:
        if hasattr(info, "data"):
            data = info.data
            low = data.get("low", -float("inf"))
            high = data.get("high", float("inf"))
            assert v >= low and v <= high, "close debe estar entre low y high"
        return v


class MarketSnapshot(BaseModel):
    """Captura compacta del mercado en un instante."""

    symbol: str
    environment: Environment
    candle: Candle
    bid: float = 0.0
    ask: float = 0.0
    timestamp_ms: int = 0

    @field_validator("ask")
    @classmethod
    def ask_gt_bid(cls, v: float, info) -> float:
        if hasattr(info, "data"):
            bid = info.data.get("bid", -float("inf"))
            assert v > bid, "ask debe ser > bid"
        return v


# ── Zonas / Pivotes ──────────────────────────────────────────────────────────

class ZoneOrigin(StrEnum):
    SUPPORT = "support"
    RESISTANCE = "resistance"
    BREAKOUT = "breakout"
    RETRACEMENT = "retracement"


class ZoneStatus(StrEnum):
    OBSERVING = "observing"
    CANDIDATE = "candidate"
    PENDING_ANALYSIS = "pending_analysis"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"


class PivotType(StrEnum):
    SWING_HIGH = "swing_high"
    SWING_LOW = "swing_low"
    EQUAL_HIGH = "equal_high"
    EQUAL_LOW = "equal_low"


class Zone(BaseModel):
    """Una zona de precio con metadata."""

    id: str
    origin: ZoneOrigin
    status: ZoneStatus = ZoneStatus.OBSERVING
    low: float
    high: float
    timestamp_ms: int
    contacts: int = 0
    confirmed_at_ms: Optional[int] = None
    invalidated_at_ms: Optional[int] = None


class Pivot(BaseModel):
    """Un pivote detectado en el gráfico."""

    id: str
    pivot_type: PivotType
    price: float
    timestamp_ms: int
    strength: float = 0.0


# ── Anotaciones ───────────────────────────────────────────────────────────────

class AnnotationType(StrEnum):
    ENTRY_PROPOSAL = "entry_proposal"
    STOP_LOSS = "stop_loss"
    TAKE_PROFIT = "take_profit"
    ZONE = "zone"
    PIVOT = "pivot"
    NOTE = "note"


class Annotation(BaseModel):
    """Anotación del usuario sobre el gráfico."""

    id: str
    annotation_type: AnnotationType
    symbol: str
    content: str
    x_px: Optional[float] = None
    y_px: Optional[float] = None
    zone_id: Optional[str] = None
    timestamp_ms: int = 0


# ── Posiciones / Órdenes ─────────────────────────────────────────────────────

class OrderSide(StrEnum):
    BUY = "buy"
    SELL = "sell"


class OrderType(StrEnum):
    MARKET = "market"
    LIMIT = "limit"
    STOP_LOSS = "stop_loss"
    TAKE_PROFIT = "take_profit"


class OrderStatus(StrEnum):
    PREPARED = "prepared"
    PENDING_SEND = "pending_send"
    UNKNOWN_STATE = "unknown_state"
    ACCEPTED = "accepted"
    PARTIALLY_FILLED = "partially_filled"
    FILLED = "filled"
    CANCELLED = "cancelled"
    REJECTED = "rejected"
    EXPIRED = "expired"


class Order(BaseModel):
    """Órden gestionada por el bot."""

    id: str
    symbol: str
    side: OrderSide
    order_type: OrderType
    status: OrderStatus = OrderStatus.PREPARED
    quantity: float
    price: Optional[float] = None
    stop_price: Optional[float] = None
    environment: Environment
    timestamp_ms: int = 0


class Position(BaseModel):
    """Posición abierta."""

    symbol: str
    side: OrderSide
    quantity: float
    entry_price: float
    unrealized_pnl: float = 0.0
    environment: Environment


# ── Decisión / Análisis IA ───────────────────────────────────────────────────

class Action(StrEnum):
    ENTER_LONG = "enter_long"
    ENTER_SHORT = "enter_short"
    HOLD = "hold"
    EXIT_LONG = "exit_long"
    EXIT_SHORT = "exit_short"
    ABSTAIN = "abstain"


class AnalysisResult(BaseModel):
    """Resultado de análisis IA o regla determinista."""

    strategy_version: str
    action: Action
    confidence: float = 0.0
    evidence: list[str] = []
    scenarios: dict[str, Any] = {}
    invalidation_conditions: list[str] = []
    snapshot_ref: Optional[str] = None


class DecisionRecord(BaseModel):
    """Registro completo de una decisión del bot."""

    id: str
    symbol: str
    zone_id: Optional[str] = None
    analysis: AnalysisResult
    risk_check_passed: bool
    order_ref: Optional[str] = None
    timestamp_ms: int = 0


# ── Configuración de riesgo ──────────────────────────────────────────────────

class RiskLimits(BaseModel):
    """Límites configurables de riesgo."""

    max_position_size: float = 1.0
    max_total_exposure: float = 10000.0
    max_concurrent_positions: int = 3
    max_daily_loss: float = 500.0


# ── Validación ────────────────────────────────────────────────────────────────

def validate_snapshot(snapshot: MarketSnapshot) -> None:
    """Valida que un snapshot sea coherente (high >= low, etc.)."""
    c = snapshot.candle
    assert c.high >= c.low, "high debe ser >= low"
    assert c.high >= c.open, "high debe ser >= open"
    assert c.high >= c.close, "high debe ser >= close"
    assert c.low <= c.open, "low debe ser <= open"
    assert c.low <= c.close, "low debe ser <= close"
    assert snapshot.bid < snapshot.ask, "bid debe ser < ask"
