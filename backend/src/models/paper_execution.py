"""Pure, deterministic fill-candidate evaluator for PAPER orders.

This module deliberately consumes only explicit DTO snapshots. It performs no
I/O, market lookup, global configuration read, filtering, or order mutation.
It returns a candidate/result; it does not persist or mutate ``PaperOrder``,
reservations or balances.
"""

from __future__ import annotations

from decimal import Decimal, localcontext
from typing import Literal, Optional, Sequence

from pydantic import BaseModel, ConfigDict, Field, field_validator

from src.models.paper_contracts import (
    Commission,
    PaperOrder,
    PaperOrderSide,
    PaperOrderStatus,
    PaperOrderType,
    PaperReservationRef,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _required_decimal(value: object) -> Decimal:
    if isinstance(value, bool):
        raise ValueError("value must be an explicit Decimal")
    if isinstance(value, str):
        value = Decimal(value)
    if not isinstance(value, Decimal):
        raise ValueError("value must be an explicit Decimal")
    if not value.is_finite():
        raise ValueError("value must be finite")
    return value


def _exact_product(left: Decimal, right: Decimal) -> Decimal:
    """Multiply finite Decimals exactly with operand-derived local precision."""
    from decimal import Context, Clamped, DivisionByZero, Inexact, InvalidOperation, Overflow, Subnormal, ROUND_HALF_EVEN, MAX_EMAX, MIN_EMIN
    precision = max(1, len(left.as_tuple().digits) + len(right.as_tuple().digits))
    ctx = Context(
        prec=precision,
        rounding=ROUND_HALF_EVEN,
        Emin=MIN_EMIN,
        Emax=MAX_EMAX,
        clamp=0,
        traps=[Clamped, DivisionByZero, Inexact, InvalidOperation, Overflow, Subnormal],
    )
    try:
        with localcontext(ctx):
            return left * right
    except Exception as exc:
        raise ValueError(f"exact Decimal product failed: {exc}") from exc


# ── Result codes (stable, immutable strings) ──────────────────────────────────

RESULT_FILL_CANDIDATE = "fill_candidate"
RESULT_INSUFFICIENT_DATA = "no_fill_insufficient_data"
RESULT_GAP_UNCERTAIN = "fill_gap_uncertain"
RESULT_AMBIGUOUS_INTRABAR = "fill_ambiguous_intrabar"
RESULT_LIMIT_NOT_CROSSED = "fill_limit_not_crossed"
RESULT_CAPACITY_MISSING = "fill_capacity_missing"
RESULT_FEE_UNKNOWN = "fill_fee_unknown"
RESULT_FEE_MISMATCH = "fill_fee_snapshot_mismatch"
RESULT_RESERVATION_INVALID = "fill_reservation_invalid"
RESULT_RESERVATION_ASSET_MISMATCH = "fill_reservation_asset_mismatch"
RESULT_MARKET_REFERENCE_INVALID = "fill_market_reference_invalid"
RESULT_MODELLED_EXEC_MISSING = "fill_modelled_exec_missing"
RESULT_PROTECTION_TRIGGER_MISSING = "fill_protection_trigger_missing"


# ── DTOs ──────────────────────────────────────────────────────────────────────

class _ExecutionDTO(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)


class PaperExecutionPolicy(_ExecutionDTO):
    """Explicit execution-policy snapshot consumed by the evaluator."""

    schema_version: Literal["paper_execution_policy.v1"] = Field(
        default="paper_execution_policy.v1", frozen=True
    )
    policy_id: str = Field(..., min_length=1)
    policy_version: str = Field(..., min_length=1)
    latency_slippage_mode: Literal["UNMODELLED", "MODELLED"]

    @field_validator("policy_id", "policy_version")
    @classmethod
    def nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("identity fields must not be blank")
        return value


class PaperMarketExecutionSnapshot(_ExecutionDTO):
    """Explicit market snapshot providing bid/ask, bar continuity, and provenance."""

    schema_version: Literal["paper_market_exec_snapshot.v1"] = Field(
        default="paper_market_exec_snapshot.v1", frozen=True
    )
    symbol: str = Field(..., min_length=3)
    bid: Decimal
    ask: Decimal
    bar_open_time_ms: int = Field(..., ge=0)
    prior_bar_open_time_ms: int = Field(..., ge=0)
    interval_ms: int = Field(..., gt=0)

    # Correction 2: provenance fields
    provenance: str = Field(..., min_length=1)
    version: str = Field(..., min_length=1)
    snapshot_timestamp_ms: int = Field(..., ge=0)

    @field_validator("symbol", "provenance", "version")
    @classmethod
    def nonblank_identity(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("identity fields must not be blank")
        return value

    @field_validator("bid", "ask", mode="before")
    @classmethod
    def decimal_only(cls, value: object) -> Decimal:
        return _required_decimal(value)

    @field_validator("bid", "ask")
    @classmethod
    def positive_quotes(cls, value: Decimal) -> Decimal:
        if value <= 0:
            raise ValueError("bid and ask must be positive")
        return value

    def market_reference(self) -> str:
        """Return exact market reference including provenance, not just symbol."""
        return f"{self.symbol}@{self.provenance}:v{self.version}@{self.snapshot_timestamp_ms}"


class PaperMarketCapacitySnapshot(_ExecutionDTO):
    """Explicit market capacity source for executable fill quantity.

    Correction 1: executable capacity is tied to a market source with its own
    identity, not embedded in the reservation DTO.
    Correction 2: includes symbol for provenance binding.
    Correction (review-5): includes capture/bar identity so capacity can be
    bound to the exact market snapshot, not just the symbol.
    """

    schema_version: Literal["paper_market_capacity.v1"] = Field(
        default="paper_market_capacity.v1", frozen=True
    )
    source_id: str = Field(..., min_length=1)
    source_version: str = Field(..., min_length=1)
    capacity: Decimal = Field(..., gt=Decimal("0"))
    symbol: str = Field(..., min_length=3)
    # Capture/bar identity for exact market binding (review-5)
    capture_bar_open_time_ms: int = Field(..., ge=0)
    capture_snapshot_timestamp_ms: int = Field(..., ge=0)

    @field_validator("source_id", "source_version", "symbol")
    @classmethod
    def nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("identity fields must not be blank")
        return value

    @field_validator("capacity", mode="before")
    @classmethod
    def decimal_only(cls, value: object) -> Decimal:
        return _required_decimal(value)


class PaperReservationSnapshot(_ExecutionDTO):
    """Active reservation snapshot consumed by the evaluator.

    Correction 3: requires non-null owner identity (owner_order_id or owner_group_id).
    The executable capacity is supplied separately via ``PaperMarketCapacitySnapshot``.
    """

    schema_version: Literal["paper_reservation_snapshot.v1"] = Field(
        default="paper_reservation_snapshot.v1", frozen=True
    )
    reservation_id: str = Field(..., min_length=1)
    owner_order_id: Optional[str] = None
    owner_group_id: Optional[str] = None
    account_id: str = Field(..., min_length=1)
    asset: str = Field(..., min_length=1)
    remaining_qty: Decimal = Field(..., ge=Decimal("0"))
    remaining_amount: Decimal = Field(..., ge=Decimal("0"))

    @field_validator("reservation_id", "owner_order_id", "owner_group_id", "account_id", "asset")
    @classmethod
    def nonblank_identity(cls, value: Optional[str]) -> Optional[str]:
        if value is not None and not value.strip():
            raise ValueError("identity fields must not be blank")
        return value

    @field_validator("remaining_qty", "remaining_amount", mode="before")
    @classmethod
    def decimal_only(cls, value: object) -> Decimal:
        return _required_decimal(value)


class PaperFeeSnapshot(_ExecutionDTO):
    """Explicit, versioned fee snapshot for a specific quantity."""

    schema_version: Literal["paper_fee_snapshot.v1"] = Field(
        default="paper_fee_snapshot.v1", frozen=True
    )
    fee_id: str = Field(..., min_length=1)
    fee_version: str = Field(..., min_length=1)
    quantity: Decimal = Field(..., ge=Decimal("0"))
    commission: Commission

    @field_validator("fee_id", "fee_version")
    @classmethod
    def nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("identity fields must not be blank")
        return value

    @field_validator("quantity", mode="before")
    @classmethod
    def decimal_only(cls, value: object) -> Decimal:
        return _required_decimal(value)


class PaperModelledExecution(_ExecutionDTO):
    """Explicit modelled execution parameters consumed when mode is MODELLED.

    Correction 5: MODELLED must not silently behave like UNMODELLED.
    Must consume explicit price and latency inputs.
    """

    schema_version: Literal["paper_modelled_exec.v1"] = Field(
        default="paper_modelled_exec.v1", frozen=True
    )
    modelled_execution_price: Decimal
    latency_ms: int = Field(..., ge=0)

    @field_validator("modelled_execution_price", mode="before")
    @classmethod
    def decimal_only(cls, value: object) -> Decimal:
        return _required_decimal(value)


class PaperProtectionSnapshot(_ExecutionDTO):
    """Explicit protection-group snapshot for intra-bar ambiguity detection.

    Correction 4: includes bar source/snapshot identity to prove OHLC collision
    is from the same bar as the market execution snapshot.
    Correction 3 (review-5): also includes bar open time and interval so OHLC
    from a different bar cannot create or suppress ambiguity.
    """

    schema_version: Literal["paper_protection_snapshot.v1"] = Field(
        default="paper_protection_snapshot.v1", frozen=True
    )
    symbol: str = Field(..., min_length=3)
    account_id: str = Field(..., min_length=1)
    protection_group_id: Optional[str] = None
    stop_trigger_price: Optional[Decimal] = None
    take_trigger_price: Optional[Decimal] = None
    bar_high: Optional[Decimal] = None
    bar_low: Optional[Decimal] = None
    # Correction 4: bar provenance
    bar_source_id: str = Field(..., min_length=1)
    bar_snapshot_timestamp_ms: int = Field(..., ge=0)
    # Correction 3 (review-5): explicit bar identity
    bar_open_time_ms: int = Field(..., ge=0)
    bar_interval_ms: int = Field(..., gt=0)

    @field_validator("symbol", "account_id", "bar_source_id")
    @classmethod
    def nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("identity fields must not be blank")
        return value

    @field_validator("stop_trigger_price", "take_trigger_price", "bar_high", "bar_low", mode="before")
    @classmethod
    def decimal_only(cls, value: Optional[object]) -> Optional[Decimal]:
        if value is None:
            return None
        return _required_decimal(value)


# ── Result DTO ────────────────────────────────────────────────────────────────

class PaperFillCandidateResult(_ExecutionDTO):
    """Frozen result containing stable status/code, exact references, and optional candidate."""

    schema_version: Literal["paper_fill_candidate_result.v1"] = Field(
        default="paper_fill_candidate_result.v1", frozen=True
    )
    status_code: str = Field(..., min_length=1)
    # Exact policy / market / fee / reservation references
    policy_id: str = ""
    policy_version: str = ""
    market_reference: str = ""
    fee_id: str = ""
    fee_version: str = ""
    fee_quantity: Decimal = Decimal("0")
    reservation_id: str = ""
    # Capacity provenance (Correction 2)
    capacity_source_id: str = ""
    capacity_source_version: str = ""
    capacity_symbol: str = ""
    # Capture/bar identity for exact capacity binding (review-5)
    capacity_capture_bar_open_time_ms: int = 0
    capacity_capture_snapshot_timestamp_ms: int = 0
    # Cost-model status
    cost_model: Literal["UNMODELLED", "MODELLED", "UNKNOWN"] = "UNKNOWN"
    # Optional candidate fill details
    fill_qty: Optional[Decimal] = None
    fill_price: Optional[Decimal] = None
    commission: Optional[Commission] = None
    # When MODELLED, the modelled execution price is captured
    modelled_execution_price: Optional[Decimal] = None
    latency_ms: Optional[int] = None
    # Optional benchmark retained with the evaluation.  Older evaluations do
    # not have this information and therefore yield unknown MODELLED slippage.
    reference_price: Optional[Decimal] = None
    reference_price_kind: Optional[str] = None

    @field_validator(
        "fill_qty", "fill_price", "modelled_execution_price", "reference_price",
        mode="before",
    )
    @classmethod
    def decimal_only(cls, value: Optional[object]) -> Optional[Decimal]:
        if value is None:
            return None
        return _required_decimal(value)

    @property
    def has_candidate(self) -> bool:
        return self.status_code == RESULT_FILL_CANDIDATE and self.fill_qty is not None


class PaperFillEvaluationRecord(_ExecutionDTO):
    """Durable representation of one evaluated PAPER fill decision."""

    evaluation_id: str
    order_id: str
    account_id: str
    paper_session_id: str
    environment: str
    status_code: str
    result_json: str
    evaluated_at_ms: int
    fill_candidate_fill_id: Optional[str] = None
    fill_candidate_fill_qty: Optional[str] = None
    fill_candidate_fill_price: Optional[str] = None
    fill_candidate_commission_json: Optional[str] = None


# ── Internal result builder ───────────────────────────────────────────────────

def _build_result(
    status_code: str,
    policy_id: str = "",
    policy_version: str = "",
    market_reference: str = "",
    fee_id: str = "",
    fee_version: str = "",
    fee_quantity: Decimal = Decimal("0"),
    reservation_id: str = "",
    cost_model: str = "UNKNOWN",
    fill_qty: Optional[Decimal] = None,
    fill_price: Optional[Decimal] = None,
    commission: Optional[Commission] = None,
    modelled_execution_price: Optional[Decimal] = None,
    latency_ms: Optional[int] = None,
    reference_price: Optional[Decimal] = None,
    reference_price_kind: Optional[str] = None,
    capacity_source_id: str = "",
    capacity_source_version: str = "",
    capacity_symbol: str = "",
    capacity_capture_bar_open_time_ms: int = 0,
    capacity_capture_snapshot_timestamp_ms: int = 0,
) -> PaperFillCandidateResult:
    return PaperFillCandidateResult(
        status_code=status_code,
        policy_id=policy_id,
        policy_version=policy_version,
        market_reference=market_reference,
        fee_id=fee_id,
        fee_version=fee_version,
        fee_quantity=fee_quantity,
        reservation_id=reservation_id,
        cost_model=cost_model,
        fill_qty=fill_qty,
        fill_price=fill_price,
        commission=commission,
        modelled_execution_price=modelled_execution_price,
        latency_ms=latency_ms,
        reference_price=reference_price,
        reference_price_kind=reference_price_kind,
        capacity_source_id=capacity_source_id,
        capacity_source_version=capacity_source_version,
        capacity_symbol=capacity_symbol,
        capacity_capture_bar_open_time_ms=capacity_capture_bar_open_time_ms,
        capacity_capture_snapshot_timestamp_ms=capacity_capture_snapshot_timestamp_ms,
    )


def _cap_kwargs(capacity: PaperMarketCapacitySnapshot) -> dict:
    """Return capacity-related kwargs for _build_result."""
    return {
        "capacity_source_id": capacity.source_id,
        "capacity_source_version": capacity.source_version,
        "capacity_symbol": capacity.symbol,
        "capacity_capture_bar_open_time_ms": capacity.capture_bar_open_time_ms,
        "capacity_capture_snapshot_timestamp_ms": capacity.capture_snapshot_timestamp_ms,
    }


# ── Gap detection ─────────────────────────────────────────────────────────────

def _detect_gap(market: PaperMarketExecutionSnapshot) -> bool:
    """Return True when the explicit bar interval shows a gap."""
    return market.bar_open_time_ms > market.prior_bar_open_time_ms + market.interval_ms


# ── Price eligibility ─────────────────────────────────────────────────────────

def _check_price_eligibility(
    order: PaperOrder,
    market: PaperMarketExecutionSnapshot,
) -> Optional[str]:
    """Return a failure code when price eligibility is not met, or None when eligible."""
    if order.side == PaperOrderSide.BUY:
        if order.order_type == PaperOrderType.LIMIT:
            # LIMIT BUY eligible only when ask <= limit_price
            if market.ask > order.price:
                return RESULT_LIMIT_NOT_CROSSED
        # MARKET BUY always uses ask; eligible by side/market snapshot presence
        return None
    elif order.side == PaperOrderSide.SELL:
        if order.order_type == PaperOrderType.LIMIT:
            # LIMIT SELL eligible only when bid >= limit_price
            if market.bid < order.price:
                return RESULT_LIMIT_NOT_CROSSED
        # MARKET SELL always uses bid; eligible by side/market snapshot presence
        return None
    return None


# ── STOP_LOSS / TAKE_PROFIT trigger check ─────────────────────────────────────

def _check_protection_trigger(
    order: PaperOrder,
    market: PaperMarketExecutionSnapshot,
) -> Optional[str]:
    """Return a failure code when STOP_LOSS/TAKE_PROFIT trigger is not reached.

    Correction 7: STOP_LOSS and TAKE_PROFIT orders must have their trigger
    condition met before they can produce a fill candidate.
    """
    if order.order_type not in (PaperOrderType.STOP_LOSS, PaperOrderType.TAKE_PROFIT):
        return None

    trigger_price = order.trigger_price
    if trigger_price is None or trigger_price <= 0:
        return RESULT_PROTECTION_TRIGGER_MISSING

    if order.side == PaperOrderSide.BUY:
        # BUY STOP_LOSS: triggered when price falls to or below trigger (ask <= trigger)
        if order.order_type == PaperOrderType.STOP_LOSS:
            if market.ask <= trigger_price:
                return None  # trigger reached
            return RESULT_PROTECTION_TRIGGER_MISSING
        # BUY TAKE_PROFIT: triggered when price rises to or above trigger (ask >= trigger)
        else:
            if market.ask >= trigger_price:
                return None  # trigger reached
            return RESULT_PROTECTION_TRIGGER_MISSING
    elif order.side == PaperOrderSide.SELL:
        # SELL STOP_LOSS: triggered when price rises to or above trigger (bid >= trigger)
        if order.order_type == PaperOrderType.STOP_LOSS:
            if market.bid >= trigger_price:
                return None  # trigger reached
            return RESULT_PROTECTION_TRIGGER_MISSING
        # SELL TAKE_PROFIT: triggered when price falls to or below trigger (bid <= trigger)
        else:
            if market.bid <= trigger_price:
                return None  # trigger reached
            return RESULT_PROTECTION_TRIGGER_MISSING

    return RESULT_PROTECTION_TRIGGER_MISSING


# ── Capacity computation (Correction 1 & 7) ───────────────────────────────────

def _compute_capacity(
    order: PaperOrder,
    reservation: PaperReservationSnapshot,
    market: PaperMarketExecutionSnapshot,
    market_capacity: PaperMarketCapacitySnapshot,
    fill_price: Decimal,
) -> Optional[Decimal]:
    """Return the executable capacity or None when insufficient.

    Correction 1: executable capacity comes from a separate ``PaperMarketCapacitySnapshot``
    with explicit market source reference, NOT from the reservation DTO.
    Correction 3: Uses the actual fill_price (not market bid/ask) for notional checks.
    Correction 7: Uses exact Decimal arithmetic for notional checks.
    Also caps executable qty by reservation.remaining_qty.
    """
    remaining_qty = order.remaining_qty
    if remaining_qty <= 0:
        return None

    # Use explicit market capacity (must be positive)
    cap = market_capacity.capacity
    if cap <= 0:
        return None

    # Cap executable qty by reservation remaining_qty as well
    executable_qty = min(remaining_qty, cap, reservation.remaining_qty)
    if executable_qty <= 0:
        return None

    # For BUY LIMIT: check that quote reserve covers the exact notional at fill_price
    if order.side == PaperOrderSide.BUY and order.order_type == PaperOrderType.LIMIT:
        notional = _exact_product(executable_qty, fill_price)
        if reservation.remaining_amount < notional:
            return None

    # For BUY MARKET: check that quote reserve covers the exact notional at fill_price
    if order.side == PaperOrderSide.BUY and order.order_type == PaperOrderType.MARKET:
        notional = _exact_product(executable_qty, fill_price)
        if reservation.remaining_amount < notional:
            return None
        # Also enforce order.max_quote_amount for MARKET BUY
        if order.max_quote_amount is not None:
            if notional > order.max_quote_amount:
                return None

    # For SELL MARKET/LIMIT: check BASE reserve covers quantity
    if order.side == PaperOrderSide.SELL:
        if reservation.remaining_amount < executable_qty:
            return None

    return executable_qty


# ── Fee validation (Correction 4) ─────────────────────────────────────────────

def _validate_fee(
    fee_snapshot: Optional[PaperFeeSnapshot],
    candidate_qty: Decimal,
    symbol: str,
) -> tuple[Optional[Commission], str]:
    """Return (commission, failure_code). Failure code is empty string on success.

    Correction 4: Validates commission asset equals symbol QUOTE.
    """
    if fee_snapshot is None:
        return None, RESULT_FEE_UNKNOWN

    # Revalidate to catch model_construct-created DTOs
    try:
        fee_snapshot = PaperFeeSnapshot.model_validate(fee_snapshot.model_dump())
    except (Exception, ValueError):
        return None, RESULT_FEE_UNKNOWN

    # Quantity must match exactly
    if fee_snapshot.quantity != candidate_qty:
        return None, RESULT_FEE_MISMATCH

    commission = fee_snapshot.commission
    if commission is None:
        return None, RESULT_FEE_UNKNOWN

    # Correction 4: Validate commission asset equals QUOTE
    symbol_parts = symbol.split("/")
    if len(symbol_parts) != 2 or not all(symbol_parts):
        return None, RESULT_FEE_MISMATCH
    expected_quote_asset = symbol_parts[1]
    if commission.asset != expected_quote_asset:
        return None, RESULT_FEE_MISMATCH

    return commission, ""


# ── Reservation asset validation (Correction 3) ───────────────────────────────

def _validate_reservation_asset(
    reservation: PaperReservationSnapshot,
    order: PaperOrder,
) -> Optional[str]:
    """Validate reservation asset matches order side. Returns failure code or None."""
    symbol_parts = order.symbol.split("/")
    if len(symbol_parts) != 2 or not all(symbol_parts):
        return RESULT_RESERVATION_ASSET_MISMATCH

    base_asset = symbol_parts[0]
    quote_asset = symbol_parts[1]

    if order.side == PaperOrderSide.BUY:
        # BUY reservation asset must equal QUOTE
        if reservation.asset != quote_asset:
            return RESULT_RESERVATION_ASSET_MISMATCH
    elif order.side == PaperOrderSide.SELL:
        # SELL reservation asset must equal BASE
        if reservation.asset != base_asset:
            return RESULT_RESERVATION_ASSET_MISMATCH

    return None


# ── Intra-bar ambiguity detection (Correction 6) ──────────────────────────────

def _detect_intra_bar_collision(
    protection: Optional[PaperProtectionSnapshot],
    order: PaperOrder,
    market: PaperMarketExecutionSnapshot,
) -> bool:
    """Return True when both stop and target are reachable in one bar with no event order.

    Correction 6/5: Also requires matching, non-empty protection_group_id against the
    order's protection_group_id; unrelated protection data must not cause an ambiguous
    result. If the order has no protection group, a protection snapshot with a group
    must NOT trigger ambiguity.
    Correction 4: Also requires bar provenance to match (same bar source and timestamp).
    """
    if protection is None:
        return False

    # Protection must have a non-empty group to be relevant
    if not protection.protection_group_id:
        return False

    stop_price = protection.stop_trigger_price
    take_price = protection.take_trigger_price
    bar_high = protection.bar_high
    bar_low = protection.bar_low

    # Both stop and take must be present
    if stop_price is None or take_price is None:
        return False
    if bar_high is None or bar_low is None:
        return False

    # Symbol and account must match
    if protection.symbol != order.symbol or protection.account_id != order.paper_account_id:
        return False

    # Correction 6/5: protection_group_id must be non-empty AND match order's group
    # If order has no group, protection with a group must not apply
    if order.protection_group_id is None:
        return False
    if protection.protection_group_id != order.protection_group_id:
        return False

    # Correction 4: Bar provenance must match (same bar source and timestamp)
    if protection.bar_source_id != market.provenance:
        return False
    if protection.bar_snapshot_timestamp_ms != market.snapshot_timestamp_ms:
        return False

    # Correction 3 (review-5): Bar open time and interval must match
    if protection.bar_open_time_ms != market.bar_open_time_ms:
        return False
    if protection.bar_interval_ms != market.interval_ms:
        return False

    if order.side == PaperOrderSide.BUY:
        # For BUY: stop is below, target is above
        stop_reachable = bar_low <= stop_price
        target_reachable = bar_high >= take_price
    elif order.side == PaperOrderSide.SELL:
        # For SELL: stop is above, target is below
        stop_reachable = bar_high >= stop_price
        target_reachable = bar_low <= take_price
    else:
        return False

    return stop_reachable and target_reachable


# ── Main evaluator ────────────────────────────────────────────────────────────

def evaluate_fill_candidate(
    order: PaperOrder,
    reservation_snapshot: PaperReservationSnapshot,
    exec_policy_snapshot: PaperExecutionPolicy,
    market_exec_snapshot: PaperMarketExecutionSnapshot,
    market_capacity_snapshot: PaperMarketCapacitySnapshot,
    fee_snapshot: Optional[PaperFeeSnapshot] = None,
    protection_snapshot: Optional[PaperProtectionSnapshot] = None,
    modelled_execution: Optional[PaperModelledExecution] = None,
    reference_price: Optional[Decimal] = None,
    reference_price_kind: Optional[str] = None,
) -> PaperFillCandidateResult:
    """Pure, deterministic fill-candidate evaluator.

    Returns a frozen ``PaperFillCandidateResult`` with exact status code,
    policy/market/fee/reservation references, cost-model status, and optional
    candidate fill details. Does not persist or mutate any input.
    """
    # Guard 0: Order must be in an eligible state
    if order.status not in (PaperOrderStatus.ACCEPTED, PaperOrderStatus.PARTIALLY_FILLED):
        return _build_result(
            RESULT_INSUFFICIENT_DATA,
            policy_id=exec_policy_snapshot.policy_id,
            policy_version=exec_policy_snapshot.policy_version,
            market_reference=market_exec_snapshot.market_reference(),
            reservation_id=reservation_snapshot.reservation_id,
        )

    # Guard 1: Gap detection
    if _detect_gap(market_exec_snapshot):
        return _build_result(
            RESULT_GAP_UNCERTAIN,
            policy_id=exec_policy_snapshot.policy_id,
            policy_version=exec_policy_snapshot.policy_version,
            market_reference=market_exec_snapshot.market_reference(),
            reservation_id=reservation_snapshot.reservation_id,
            cost_model=exec_policy_snapshot.latency_slippage_mode,
        )

    # Guard 2b: Market symbol must match order symbol
    if market_exec_snapshot.symbol != order.symbol:
        return _build_result(
            RESULT_MARKET_REFERENCE_INVALID,
            policy_id=exec_policy_snapshot.policy_id,
            policy_version=exec_policy_snapshot.policy_version,
            market_reference=market_exec_snapshot.market_reference(),
            reservation_id=reservation_snapshot.reservation_id,
            cost_model=exec_policy_snapshot.latency_slippage_mode,
        )

    # Guard 2bb: Capacity capture/bar identity must match market snapshot (review-5)
    # Capacity from a different bar/time cannot be used for this market snapshot
    if market_capacity_snapshot.capture_bar_open_time_ms != market_exec_snapshot.bar_open_time_ms:
        return _build_result(
            RESULT_MARKET_REFERENCE_INVALID,
            policy_id=exec_policy_snapshot.policy_id,
            policy_version=exec_policy_snapshot.policy_version,
            market_reference=market_exec_snapshot.market_reference(),
            reservation_id=reservation_snapshot.reservation_id,
            cost_model=exec_policy_snapshot.latency_slippage_mode,
            **_cap_kwargs(market_capacity_snapshot),
        )
    if market_capacity_snapshot.capture_snapshot_timestamp_ms != market_exec_snapshot.snapshot_timestamp_ms:
        return _build_result(
            RESULT_MARKET_REFERENCE_INVALID,
            policy_id=exec_policy_snapshot.policy_id,
            policy_version=exec_policy_snapshot.policy_version,
            market_reference=market_exec_snapshot.market_reference(),
            reservation_id=reservation_snapshot.reservation_id,
            cost_model=exec_policy_snapshot.latency_slippage_mode,
            **_cap_kwargs(market_capacity_snapshot),
        )

    # Guard 2bc: Capacity symbol must match both order and market symbols (before price)
    if market_capacity_snapshot.symbol != order.symbol:
        return _build_result(
            RESULT_MARKET_REFERENCE_INVALID,
            policy_id=exec_policy_snapshot.policy_id,
            policy_version=exec_policy_snapshot.policy_version,
            market_reference=market_exec_snapshot.market_reference(),
            reservation_id=reservation_snapshot.reservation_id,
            cost_model=exec_policy_snapshot.latency_slippage_mode,
            **_cap_kwargs(market_capacity_snapshot),
        )
    if market_capacity_snapshot.symbol != market_exec_snapshot.symbol:
        return _build_result(
            RESULT_MARKET_REFERENCE_INVALID,
            policy_id=exec_policy_snapshot.policy_id,
            policy_version=exec_policy_snapshot.policy_version,
            market_reference=market_exec_snapshot.market_reference(),
            reservation_id=reservation_snapshot.reservation_id,
            cost_model=exec_policy_snapshot.latency_slippage_mode,
            **_cap_kwargs(market_capacity_snapshot),
        )

    # Guard 2: Price eligibility
    price_code = _check_price_eligibility(order, market_exec_snapshot)
    if price_code is not None:
        return _build_result(
            price_code,
            policy_id=exec_policy_snapshot.policy_id,
            policy_version=exec_policy_snapshot.policy_version,
            market_reference=market_exec_snapshot.market_reference(),
            reservation_id=reservation_snapshot.reservation_id,
            cost_model=exec_policy_snapshot.latency_slippage_mode,
        )

    # Guard 2d: Snapshot coherence (Correction 6) - validate bid <= ask and timestamp ordering
    if market_exec_snapshot.bid > market_exec_snapshot.ask:
        return _build_result(
            RESULT_INSUFFICIENT_DATA,
            policy_id=exec_policy_snapshot.policy_id,
            policy_version=exec_policy_snapshot.policy_version,
            market_reference=market_exec_snapshot.market_reference(),
            reservation_id=reservation_snapshot.reservation_id,
            cost_model=exec_policy_snapshot.latency_slippage_mode,
        )
    if market_exec_snapshot.bar_open_time_ms <= market_exec_snapshot.prior_bar_open_time_ms:
        return _build_result(
            RESULT_INSUFFICIENT_DATA,
            policy_id=exec_policy_snapshot.policy_id,
            policy_version=exec_policy_snapshot.policy_version,
            market_reference=market_exec_snapshot.market_reference(),
            reservation_id=reservation_snapshot.reservation_id,
            cost_model=exec_policy_snapshot.latency_slippage_mode,
        )

    # Guard 2c: Reservation validity (remaining_qty and remaining_amount must be positive)
    if reservation_snapshot.remaining_qty <= 0 or reservation_snapshot.remaining_amount <= 0:
        return _build_result(
            RESULT_RESERVATION_INVALID,
            policy_id=exec_policy_snapshot.policy_id,
            policy_version=exec_policy_snapshot.policy_version,
            market_reference=market_exec_snapshot.market_reference(),
            reservation_id=reservation_snapshot.reservation_id,
            cost_model=exec_policy_snapshot.latency_slippage_mode,
        )

    # Guard 3: Reservation asset validation (Correction 3)
    asset_code = _validate_reservation_asset(reservation_snapshot, order)
    if asset_code is not None:
        return _build_result(
            asset_code,
            policy_id=exec_policy_snapshot.policy_id,
            policy_version=exec_policy_snapshot.policy_version,
            market_reference=market_exec_snapshot.market_reference(),
            reservation_id=reservation_snapshot.reservation_id,
            cost_model=exec_policy_snapshot.latency_slippage_mode,
        )

    # Guard 3a: Reservation identity (Correction 1)
    # - Exactly one owner field must be set (not both, not neither)
    # - account_id must match order's paper_account_id (if set)
    # - owner_order_id must match order's order_id
    # - owner_group_id must match order's protection_group_id (non-empty)
    has_owner_order = reservation_snapshot.owner_order_id is not None
    has_owner_group = reservation_snapshot.owner_group_id is not None

    # Neither owner field set → invalid
    if not has_owner_order and not has_owner_group:
        return _build_result(
            RESULT_RESERVATION_INVALID,
            policy_id=exec_policy_snapshot.policy_id,
            policy_version=exec_policy_snapshot.policy_version,
            market_reference=market_exec_snapshot.market_reference(),
            reservation_id=reservation_snapshot.reservation_id,
            cost_model=exec_policy_snapshot.latency_slippage_mode,
        )

    # Both owner fields set → invalid (exactly one required)
    if has_owner_order and has_owner_group:
        return _build_result(
            RESULT_RESERVATION_INVALID,
            policy_id=exec_policy_snapshot.policy_id,
            policy_version=exec_policy_snapshot.policy_version,
            market_reference=market_exec_snapshot.market_reference(),
            reservation_id=reservation_snapshot.reservation_id,
            cost_model=exec_policy_snapshot.latency_slippage_mode,
        )

    # Account mismatch → invalid (account_id is now required and must match)
    if reservation_snapshot.account_id is None or not reservation_snapshot.account_id.strip():
        return _build_result(
            RESULT_RESERVATION_INVALID,
            policy_id=exec_policy_snapshot.policy_id,
            policy_version=exec_policy_snapshot.policy_version,
            market_reference=market_exec_snapshot.market_reference(),
            reservation_id=reservation_snapshot.reservation_id,
            cost_model=exec_policy_snapshot.latency_slippage_mode,
        )
    if reservation_snapshot.account_id != order.paper_account_id:
        return _build_result(
            RESULT_RESERVATION_INVALID,
            policy_id=exec_policy_snapshot.policy_id,
            policy_version=exec_policy_snapshot.policy_version,
            market_reference=market_exec_snapshot.market_reference(),
            reservation_id=reservation_snapshot.reservation_id,
            cost_model=exec_policy_snapshot.latency_slippage_mode,
        )

    # owner_order_id must match candidate order
    if has_owner_order:
        if reservation_snapshot.owner_order_id != order.order_id:
            return _build_result(
                RESULT_RESERVATION_INVALID,
                policy_id=exec_policy_snapshot.policy_id,
                policy_version=exec_policy_snapshot.policy_version,
                market_reference=market_exec_snapshot.market_reference(),
                reservation_id=reservation_snapshot.reservation_id,
                cost_model=exec_policy_snapshot.latency_slippage_mode,
            )

    # owner_group_id must match order's non-empty protection_group_id
    if has_owner_group:
        if order.protection_group_id is None:
            return _build_result(
                RESULT_RESERVATION_INVALID,
                policy_id=exec_policy_snapshot.policy_id,
                policy_version=exec_policy_snapshot.policy_version,
                market_reference=market_exec_snapshot.market_reference(),
                reservation_id=reservation_snapshot.reservation_id,
                cost_model=exec_policy_snapshot.latency_slippage_mode,
            )
        if reservation_snapshot.owner_group_id != order.protection_group_id:
            return _build_result(
                RESULT_RESERVATION_INVALID,
                policy_id=exec_policy_snapshot.policy_id,
                policy_version=exec_policy_snapshot.policy_version,
                market_reference=market_exec_snapshot.market_reference(),
                reservation_id=reservation_snapshot.reservation_id,
                cost_model=exec_policy_snapshot.latency_slippage_mode,
            )

    # Determine fill_price BEFORE capacity checks (Correction 3: MODELLED budget consistency)
    if exec_policy_snapshot.latency_slippage_mode == "MODELLED":
        if modelled_execution is None:
            return _build_result(
                RESULT_MODELLED_EXEC_MISSING,
                policy_id=exec_policy_snapshot.policy_id,
                policy_version=exec_policy_snapshot.policy_version,
                market_reference=market_exec_snapshot.market_reference(),
                reservation_id=reservation_snapshot.reservation_id,
                cost_model="MODELLED",
                **_cap_kwargs(market_capacity_snapshot),
            )
        fill_price = modelled_execution.modelled_execution_price
        latency_ms = modelled_execution.latency_ms
    else:
        # UNMODELLED: use side bid/ask
        if order.side == PaperOrderSide.BUY:
            fill_price = market_exec_snapshot.ask
        else:
            fill_price = market_exec_snapshot.bid
        latency_ms = None

    # Guard 3c: Capacity zero check (already enforced by DTO, but explicit here)
    if market_capacity_snapshot.capacity <= 0:
        return _build_result(
            RESULT_RESERVATION_INVALID,
            policy_id=exec_policy_snapshot.policy_id,
            policy_version=exec_policy_snapshot.policy_version,
            market_reference=market_exec_snapshot.market_reference(),
            reservation_id=reservation_snapshot.reservation_id,
            cost_model=exec_policy_snapshot.latency_slippage_mode,
            **_cap_kwargs(market_capacity_snapshot),
        )

    # Guard 4: Capacity computation (uses actual fill_price, not market bid/ask)
    executable_qty = _compute_capacity(order, reservation_snapshot, market_exec_snapshot, market_capacity_snapshot, fill_price)
    if executable_qty is None or executable_qty <= 0:
        return _build_result(
            RESULT_CAPACITY_MISSING,
            policy_id=exec_policy_snapshot.policy_id,
            policy_version=exec_policy_snapshot.policy_version,
            market_reference=market_exec_snapshot.market_reference(),
            reservation_id=reservation_snapshot.reservation_id,
            cost_model=exec_policy_snapshot.latency_slippage_mode,
            **_cap_kwargs(market_capacity_snapshot),
        )

    # Guard 5: Fee validation (Correction 4)
    commission, fee_code = _validate_fee(fee_snapshot, executable_qty, order.symbol)
    if fee_code:
        return _build_result(
            fee_code,
            policy_id=exec_policy_snapshot.policy_id,
            policy_version=exec_policy_snapshot.policy_version,
            market_reference=market_exec_snapshot.market_reference(),
            fee_id=fee_snapshot.fee_id if fee_snapshot else "",
            fee_version=fee_snapshot.fee_version if fee_snapshot else "",
            fee_quantity=fee_snapshot.quantity if fee_snapshot else Decimal("0"),
            reservation_id=reservation_snapshot.reservation_id,
            cost_model=exec_policy_snapshot.latency_slippage_mode,
            **_cap_kwargs(market_capacity_snapshot),
        )

    # Guard 5b: Intra-bar collision detection (Correction 5: before trigger check)
    if _detect_intra_bar_collision(protection_snapshot, order, market_exec_snapshot):
        return _build_result(
            RESULT_AMBIGUOUS_INTRABAR,
            policy_id=exec_policy_snapshot.policy_id,
            policy_version=exec_policy_snapshot.policy_version,
            market_reference=market_exec_snapshot.market_reference(),
            reservation_id=reservation_snapshot.reservation_id,
            cost_model=exec_policy_snapshot.latency_slippage_mode,
            **_cap_kwargs(market_capacity_snapshot),
        )

    # Guard 5c: STOP_LOSS / TAKE_PROFIT trigger check (after collision)
    trigger_code = _check_protection_trigger(order, market_exec_snapshot)
    if trigger_code is not None:
        return _build_result(
            trigger_code,
            policy_id=exec_policy_snapshot.policy_id,
            policy_version=exec_policy_snapshot.policy_version,
            market_reference=market_exec_snapshot.market_reference(),
            reservation_id=reservation_snapshot.reservation_id,
            cost_model=exec_policy_snapshot.latency_slippage_mode,
            **_cap_kwargs(market_capacity_snapshot),
        )

    # Guard 6: Recheck exact notional at actual fill_price for BUY orders
    if order.side == PaperOrderSide.BUY:
        fill_notional = _exact_product(executable_qty, fill_price)
        if reservation_snapshot.remaining_amount < fill_notional:
            return _build_result(
                RESULT_CAPACITY_MISSING,
                policy_id=exec_policy_snapshot.policy_id,
                policy_version=exec_policy_snapshot.policy_version,
                market_reference=market_exec_snapshot.market_reference(),
                reservation_id=reservation_snapshot.reservation_id,
                cost_model=exec_policy_snapshot.latency_slippage_mode,
                **_cap_kwargs(market_capacity_snapshot),
            )
        # For BUY LIMIT: fill_price must not exceed limit_price
        if order.order_type == PaperOrderType.LIMIT:
            if fill_price > order.price:
                return _build_result(
                    RESULT_LIMIT_NOT_CROSSED,
                    policy_id=exec_policy_snapshot.policy_id,
                    policy_version=exec_policy_snapshot.policy_version,
                    market_reference=market_exec_snapshot.market_reference(),
                    reservation_id=reservation_snapshot.reservation_id,
                    cost_model=exec_policy_snapshot.latency_slippage_mode,
                    **_cap_kwargs(market_capacity_snapshot),
                )
        # For BUY MARKET: fill notional must not exceed max_quote_amount
        if order.order_type == PaperOrderType.MARKET:
            if order.max_quote_amount is not None:
                if fill_notional > order.max_quote_amount:
                    return _build_result(
                        RESULT_CAPACITY_MISSING,
                        policy_id=exec_policy_snapshot.policy_id,
                        policy_version=exec_policy_snapshot.policy_version,
                        market_reference=market_exec_snapshot.market_reference(),
                        reservation_id=reservation_snapshot.reservation_id,
                        cost_model=exec_policy_snapshot.latency_slippage_mode,
                        **_cap_kwargs(market_capacity_snapshot),
                    )

    # Guard 6b: MODELLED SELL LIMIT: fill_price must not be below limit_price
    if exec_policy_snapshot.latency_slippage_mode == "MODELLED" and modelled_execution is not None:
        modelled_price = modelled_execution.modelled_execution_price
        if modelled_price <= 0:
            return _build_result(
                RESULT_CAPACITY_MISSING,
                policy_id=exec_policy_snapshot.policy_id,
                policy_version=exec_policy_snapshot.policy_version,
                market_reference=market_exec_snapshot.market_reference(),
                reservation_id=reservation_snapshot.reservation_id,
                cost_model="MODELLED",
                **_cap_kwargs(market_capacity_snapshot),
            )
        # For SELL LIMIT: fill_price must be >= limit_price
        if order.side == PaperOrderSide.SELL and order.order_type == PaperOrderType.LIMIT:
            if modelled_price < order.price:
                return _build_result(
                    RESULT_LIMIT_NOT_CROSSED,
                    policy_id=exec_policy_snapshot.policy_id,
                    policy_version=exec_policy_snapshot.policy_version,
                    market_reference=market_exec_snapshot.market_reference(),
                    reservation_id=reservation_snapshot.reservation_id,
                    cost_model="MODELLED",
                    **_cap_kwargs(market_capacity_snapshot),
                )

    # Guard 7: Intra-bar collision detection (Correction 6) - moved before trigger
    # (Already handled above in Guard 5b per Correction 5)

    # Build candidate result
    fill_qty = min(order.remaining_qty, executable_qty)

    return _build_result(
        RESULT_FILL_CANDIDATE,
        policy_id=exec_policy_snapshot.policy_id,
        policy_version=exec_policy_snapshot.policy_version,
        market_reference=market_exec_snapshot.market_reference(),
        fee_id=fee_snapshot.fee_id if fee_snapshot else "",
        fee_version=fee_snapshot.fee_version if fee_snapshot else "",
        fee_quantity=fee_snapshot.quantity if fee_snapshot else Decimal("0"),
        reservation_id=reservation_snapshot.reservation_id,
        cost_model=exec_policy_snapshot.latency_slippage_mode,
        fill_qty=fill_qty,
        fill_price=fill_price,
        commission=commission,
        modelled_execution_price=modelled_execution.modelled_execution_price if modelled_execution else None,
        latency_ms=latency_ms,
        reference_price=(
            reference_price
            if reference_price is not None
            else (
                market_exec_snapshot.ask
                if order.side == PaperOrderSide.BUY
                else market_exec_snapshot.bid
            )
        ) if exec_policy_snapshot.latency_slippage_mode == "MODELLED" else None,
        reference_price_kind=(
            reference_price_kind or "market_snapshot"
        ) if exec_policy_snapshot.latency_slippage_mode == "MODELLED" else None,
        **_cap_kwargs(market_capacity_snapshot),
    )
