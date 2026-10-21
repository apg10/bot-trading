"""Immutable versioned paper risk policy and deterministic entry evaluation.

This module deliberately consumes only explicit DTO snapshots. It performs no
I/O, market lookup, global configuration read, filtering, or order mutation.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation, localcontext
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from src.models.paper_contracts import PaperOrder, PaperOrderSide, PaperOrderType


def _required_decimal(value: object) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, Decimal):
        raise ValueError("value must be an explicit Decimal")
    if not value.is_finite():
        raise ValueError("value must be finite")
    return value


def _exact_add(*values: Decimal) -> Decimal:
    """Add finite decimals without consulting or inheriting the global context."""
    if not values:
        return Decimal("0")
    min_exp = min(value.as_tuple().exponent for value in values)
    max_adjusted = max(value.adjusted() if value else 0 for value in values)
    precision = max(1, max_adjusted - min_exp + 1 + len(str(len(values))))
    with localcontext() as context:
        context.prec = precision
        context.Emin = min(context.Emin, min_exp - precision)
        context.Emax = max(context.Emax, max_adjusted + precision)
        result = Decimal("0")
        for value in values:
            result += value
        return result


def _exact_product(left: Decimal, right: Decimal) -> Decimal:
    precision = max(1, len(left.as_tuple().digits) + len(right.as_tuple().digits))
    with localcontext() as context:
        context.prec = precision
        context.Emin = min(context.Emin, left.as_tuple().exponent + right.as_tuple().exponent - precision)
        context.Emax = max(context.Emax, left.adjusted() + right.adjusted() + precision)
        return left * right


class _RiskDTO(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)


class PaperRiskPolicy(_RiskDTO):
    """Explicit account-scoped financial limits; no financial limit defaults."""

    schema_version: Literal["paper_risk_policy.v1"] = Field(
        default="paper_risk_policy.v1", frozen=True
    )
    policy_id: str = Field(..., min_length=1)
    policy_version: str = Field(..., min_length=1)
    account_id: str = Field(..., min_length=1)
    quote_asset: str = Field(..., min_length=1)
    max_order_notional: Decimal
    max_total_exposure: Decimal
    max_daily_loss: Decimal

    @field_validator("policy_id", "policy_version", "account_id", "quote_asset")
    @classmethod
    def nonblank_identity(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("identity fields must not be blank")
        return value

    @field_validator("max_order_notional", "max_total_exposure", "max_daily_loss", mode="before")
    @classmethod
    def decimal_only(cls, value: object) -> Decimal:
        return _required_decimal(value)

    @field_validator("max_order_notional", "max_total_exposure", "max_daily_loss")
    @classmethod
    def nonnegative_limits(cls, value: Decimal) -> Decimal:
        if value < 0:
            raise ValueError("limits must be nonnegative")
        return value


class PaperRiskSnapshot(_RiskDTO):
    """Explicit quote-valued marks, equity, and exposure with provenance times."""

    account_id: str = Field(..., min_length=1)
    quote_asset: str = Field(..., min_length=1)
    market_symbol: str = Field(..., min_length=3)
    market_mark_price: Decimal
    market_reference: str = Field(..., min_length=1)
    market_timestamp_ms: int = Field(..., ge=0)
    equity_reference: str = Field(..., min_length=1)
    equity_timestamp_ms: int = Field(..., ge=0)
    equity_utc_day_start: Decimal
    equity_current_marked: Decimal
    exposure_reference: str = Field(..., min_length=1)
    exposure_timestamp_ms: int = Field(..., ge=0)
    marked_open_inventory: Decimal
    remaining_buy_reservations: Decimal

    @field_validator(
        "account_id", "quote_asset", "market_symbol", "market_reference",
        "equity_reference", "exposure_reference",
    )
    @classmethod
    def nonblank_snapshot_identity(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("snapshot identities and references must not be blank")
        return value

    @field_validator(
        "market_mark_price", "equity_utc_day_start", "equity_current_marked", "marked_open_inventory",
        "remaining_buy_reservations", mode="before",
    )
    @classmethod
    def decimal_only(cls, value: object) -> Decimal:
        return _required_decimal(value)

    @field_validator(
        "equity_utc_day_start", "equity_current_marked", "marked_open_inventory",
        "remaining_buy_reservations",
    )
    @classmethod
    def nonnegative_values(cls, value: Decimal) -> Decimal:
        if value < 0:
            raise ValueError("snapshot values must be nonnegative")
        return value

    @field_validator("market_mark_price")
    @classmethod
    def positive_market_mark(cls, value: Decimal) -> Decimal:
        if value <= 0:
            raise ValueError("market mark must be positive")
        return value


class PaperRiskResult(_RiskDTO):
    """Stable pure-evaluation outcome retaining the exact policy reference."""

    approved: bool
    code: str
    policy_id: str
    policy_version: str
    order_notional: Decimal | None = None
    projected_exposure: Decimal | None = None
    daily_loss: Decimal | None = None


def _result(
    approved: bool,
    code: str,
    policy: PaperRiskPolicy | None,
    *,
    notional: Decimal | None = None,
    exposure: Decimal | None = None,
    loss: Decimal | None = None,
) -> PaperRiskResult:
    return PaperRiskResult(
        approved=approved,
        code=code,
        policy_id=policy.policy_id if policy else "",
        policy_version=policy.policy_version if policy else "",
        order_notional=notional,
        projected_exposure=exposure,
        daily_loss=loss,
    )


def evaluate_paper_risk(
    order: PaperOrder,
    policy: PaperRiskPolicy | None,
    snapshot: PaperRiskSnapshot | None,
) -> PaperRiskResult:
    """Evaluate BUY LIMIT/MARKET entries; leave other orders outside entry risk."""
    # Exit/non-entry operations must not be blocked by absent risk configuration
    # or marks. Keep this guard first so callers can safely route any order here.
    if order.side != PaperOrderSide.BUY or order.order_type not in (
        PaperOrderType.LIMIT,
        PaperOrderType.MARKET,
    ):
        return _result(True, "risk_not_applicable", policy)

    if policy is None:
        return _result(False, "risk_policy_missing", None)
    try:
        # Revalidate even model_construct-created DTOs; no malformed object passes.
        policy = PaperRiskPolicy.model_validate(policy.model_dump())
    except (ValidationError, AttributeError, TypeError, ValueError):
        return _result(False, "risk_policy_missing", None)

    if snapshot is None:
        return _result(False, "risk_snapshot_insufficient", policy)
    try:
        snapshot = PaperRiskSnapshot.model_validate(snapshot.model_dump())
    except (ValidationError, AttributeError, TypeError, ValueError):
        return _result(False, "risk_snapshot_invalid", policy)
    if (
        snapshot.account_id != policy.account_id
        or snapshot.quote_asset != policy.quote_asset
    ):
        return _result(False, "risk_snapshot_invalid", policy)

    symbol_parts = order.symbol.split("/")
    if (
        order.paper_account_id != policy.account_id
        or len(symbol_parts) != 2
        or symbol_parts[1] != policy.quote_asset
        or snapshot.market_symbol != order.symbol
    ):
        return _result(False, "risk_snapshot_invalid", policy)

    try:
        if order.order_type == PaperOrderType.LIMIT:
            notional = _exact_product(order.requested_qty, order.price)  # type: ignore[arg-type]
        elif order.order_type == PaperOrderType.MARKET:
            notional = _required_decimal(order.max_quote_amount)  # type: ignore[arg-type]
        else:  # guarded above; retain fail-closed behavior if enum grows
            return _result(False, "risk_snapshot_invalid", policy)
        if notional <= 0:
            return _result(False, "risk_snapshot_invalid", policy)
        current_exposure = _exact_add(
            snapshot.marked_open_inventory, snapshot.remaining_buy_reservations
        )
        projected = _exact_add(current_exposure, notional)
        loss = max(
            Decimal("0"),
            _exact_add(snapshot.equity_utc_day_start, snapshot.equity_current_marked.copy_negate()),
        )
    except (ArithmeticError, InvalidOperation, TypeError, ValueError):
        return _result(False, "risk_snapshot_invalid", policy)

    if notional > policy.max_order_notional:
        return _result(False, "risk_order_size_limit", policy, notional=notional, loss=loss)
    if loss >= policy.max_daily_loss:
        return _result(False, "risk_daily_loss_limit", policy, notional=notional, loss=loss)
    if projected > policy.max_total_exposure:
        return _result(
            False, "risk_exposure_limit", policy,
            notional=notional, exposure=projected, loss=loss,
        )
    return _result(True, "risk_approved", policy, notional=notional, exposure=projected, loss=loss)
