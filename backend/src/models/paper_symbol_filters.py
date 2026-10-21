"""Immutable versioned symbol-filter snapshot and pure validator for BUY LIMIT/MARKET.

This module deliberately consumes only explicit DTO snapshots. It performs no
I/O, market lookup, global configuration read, filtering, order mutation, or
persistence.  It does not certify full exchange acceptance; this bounded v1
covers only the rules enumerated in ACT-P-004 policy section 14.23-14.25.

Supported filter types (Spot BUY LIMIT/MARKET):
  - PRICE_FILTER      : min_price, max_price, tick_size
  - LOT_SIZE          : min_qty, max_qty, step_size
  - MARKET_LOT_SIZE   : min_qty, max_qty, step_size
  - MIN_NOTIONAL      : min_notional (with market applicability flag)
  - NOTIONAL          : min_notional / max_notional (with market applicability flag)

Unsupported filter types cause the snapshot to be invalid (fail-closed).
"""

from __future__ import annotations

from decimal import Decimal, localcontext
from typing import Literal, Sequence

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)


# ── helpers ───────────────────────────────────────────────────────────────────

def _required_decimal(value: object) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, Decimal):
        raise ValueError("value must be an explicit Decimal")
    if not value.is_finite():
        raise ValueError("value must be finite")
    return value


def _is_step_multiple(qty: Decimal, step: Decimal) -> bool:
    """Return True when *qty* is an exact multiple of *step*.

    A zero step is allowed only for PRICE_FILTER's explicitly disabled tick;
    LOT_SIZE and MARKET_LOT_SIZE reject zero steps during DTO validation.
    """
    if step == Decimal("0"):
        return True
    if qty < Decimal("0") or step <= Decimal("0"):
        return False
    with localcontext() as ctx:
        ctx.prec = max(1, len(qty.as_tuple().digits) + len(step.as_tuple().digits) + 4)
        ctx.Emin = min(ctx.Emin, qty.as_tuple().exponent - ctx.prec)
        ctx.Emax = max(ctx.Emax, qty.adjusted() + ctx.prec)
        remainder = qty % step
        return remainder == Decimal("0")


# ── DTOs ──────────────────────────────────────────────────────────────────────

class _FilterDTO(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)


class PriceFilter(_FilterDTO):
    """PRICE_FILTER snapshot values."""

    filter_type: Literal["PRICE_FILTER"] = "PRICE_FILTER"
    min_price: Decimal  # zero = disabled
    max_price: Decimal  # zero = disabled
    tick_size: Decimal  # zero = disabled

    @field_validator("min_price", "max_price", "tick_size", mode="before")
    @classmethod
    def decimal_only(cls, value: object) -> Decimal:
        return _required_decimal(value)

    @model_validator(mode="after")
    def valid_bounds(self) -> PriceFilter:
        if min(self.min_price, self.max_price, self.tick_size) < 0:
            raise ValueError("PRICE_FILTER values must be nonnegative")
        if self.min_price and self.max_price and self.min_price > self.max_price:
            raise ValueError("PRICE_FILTER min_price must not exceed max_price")
        return self


class LotSizeFilter(_FilterDTO):
    """LOT_SIZE snapshot values."""

    filter_type: Literal["LOT_SIZE"] = "LOT_SIZE"
    min_qty: Decimal
    max_qty: Decimal
    step_size: Decimal

    @field_validator("min_qty", "max_qty", "step_size", mode="before")
    @classmethod
    def decimal_only(cls, value: object) -> Decimal:
        return _required_decimal(value)

    @model_validator(mode="after")
    def valid_bounds(self) -> LotSizeFilter:
        if min(self.min_qty, self.max_qty, self.step_size) <= 0:
            raise ValueError("LOT_SIZE values must be positive")
        if self.min_qty > self.max_qty:
            raise ValueError("LOT_SIZE min_qty must not exceed max_qty")
        return self


class MarketLotSizeFilter(_FilterDTO):
    """MARKET_LOT_SIZE snapshot values."""

    filter_type: Literal["MARKET_LOT_SIZE"] = "MARKET_LOT_SIZE"
    min_qty: Decimal
    max_qty: Decimal
    step_size: Decimal

    @field_validator("min_qty", "max_qty", "step_size", mode="before")
    @classmethod
    def decimal_only(cls, value: object) -> Decimal:
        return _required_decimal(value)

    @model_validator(mode="after")
    def valid_bounds(self) -> MarketLotSizeFilter:
        if min(self.min_qty, self.max_qty, self.step_size) <= 0:
            raise ValueError("MARKET_LOT_SIZE values must be positive")
        if self.min_qty > self.max_qty:
            raise ValueError("MARKET_LOT_SIZE min_qty must not exceed max_qty")
        return self


class MinNotionalFilter(_FilterDTO):
    """MIN_NOTIONAL snapshot values."""

    filter_type: Literal["MIN_NOTIONAL"] = "MIN_NOTIONAL"
    min_notional: Decimal
    applies_to_market: bool = Field(...)
    avg_price_mins: int = Field(..., ge=0)

    @field_validator("min_notional", mode="before")
    @classmethod
    def decimal_only(cls, value: object) -> Decimal:
        return _required_decimal(value)

    @field_validator("min_notional")
    @classmethod
    def positive_minimum(cls, value: Decimal) -> Decimal:
        if value <= 0:
            raise ValueError("MIN_NOTIONAL min_notional must be positive")
        return value


class NotionalFilter(_FilterDTO):
    """NOTIONAL snapshot values."""

    filter_type: Literal["NOTIONAL"] = "NOTIONAL"
    min_notional: Decimal
    max_notional: Decimal
    apply_min_to_market: bool = Field(...)
    apply_max_to_market: bool = Field(...)
    avg_price_mins: int = Field(..., ge=0)

    @field_validator("min_notional", "max_notional", mode="before")
    @classmethod
    def decimal_only(cls, value: object) -> Decimal:
        return _required_decimal(value)

    @field_validator("min_notional", "max_notional")
    @classmethod
    def positive_bounds(cls, value: Decimal) -> Decimal:
        if value <= 0:
            raise ValueError("NOTIONAL bounds must be positive")
        return value

    @model_validator(mode="after")
    def valid_bounds(self) -> NotionalFilter:
        if self.min_notional > self.max_notional:
            raise ValueError("NOTIONAL min_notional must not exceed max_notional")
        return self


class MarketNotionalReference(_FilterDTO):
    """Explicit market notional reference price required for MARKET notional checks."""

    symbol: str = Field(..., min_length=3)
    price: Decimal
    source: str = Field(..., min_length=1)  # provenance identifier
    source_version: str = Field(..., min_length=1)
    timestamp_ms: int = Field(..., ge=0)
    avg_price_mins: int = Field(..., ge=0)  # window identifier

    @field_validator("price", mode="before")
    @classmethod
    def decimal_only(cls, value: object) -> Decimal:
        return _required_decimal(value)

    @field_validator("symbol", "source", "source_version")
    @classmethod
    def nonblank_reference(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("market reference identity must not be blank")
        return value

    @field_validator("price")
    @classmethod
    def positive_price(cls, value: Decimal) -> Decimal:
        if value <= 0:
            raise ValueError("market reference price must be positive")
        return value


class SymbolFilterSnapshot(_FilterDTO):
    """Immutable, versioned symbol-filter snapshot with provenance.

    Every numeric value is an explicit finite Decimal; no float conversion
    or defaults.  Zero bounds/tick explicitly disable that individual rule
    per Binance Spot docs.
    """

    schema_version: Literal["symbol_filter_snapshot.v1"] = "symbol_filter_snapshot.v1"
    snapshot_id: str = Field(..., min_length=1)
    filter_set_id: str = Field(..., min_length=1)
    filter_set_version: str = Field(..., min_length=1)
    source_id: str = Field(..., min_length=1)
    source_version: str = Field(..., min_length=1)
    capture_timestamp_ms: int = Field(..., ge=0)
    symbol: str = Field(..., min_length=3)
    base_asset: str = Field(..., min_length=1)
    quote_asset: str = Field(..., min_length=1)

    # Presence of each nullable slot is explicit; None means source omits the rule.
    price_filter: PriceFilter | None = Field(...)
    lot_size: LotSizeFilter | None = Field(...)
    market_lot_size: MarketLotSizeFilter | None = Field(...)
    min_notional: MinNotionalFilter | None = Field(...)
    notional: NotionalFilter | None = Field(...)

    # Unhandled relevant source filter types (must be reported if present)
    unhandled_filter_types: tuple[str, ...] = Field(...)

    @field_validator("snapshot_id", "filter_set_id", "filter_set_version",
                     "source_id", "source_version", "symbol", "base_asset", "quote_asset")
    @classmethod
    def nonblank_identity(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("identity fields must not be blank")
        return value

    @field_validator("unhandled_filter_types")
    @classmethod
    def unique_nonblank_unhandled_types(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(not item.strip() for item in value) or len(set(value)) != len(value):
            raise ValueError("unhandled filter types must be unique and nonblank")
        return value

    @model_validator(mode="after")
    def symbol_assets_match(self) -> SymbolFilterSnapshot:
        parts = self.symbol.split("/")
        if len(parts) != 2 or (parts[0], parts[1]) != (self.base_asset, self.quote_asset):
            raise ValueError("snapshot symbol must match its base/quote assets")
        return self


# ── Validation outcome ────────────────────────────────────────────────────────

class SymbolFilterResult(_FilterDTO):
    """Stable pure-evaluation outcome retaining exact snapshot/source refs."""

    approved: bool
    code: str  # symbol_filter_missing | symbol_filter_invalid |
               # symbol_filter_violation | symbol_filter_not_applicable
    snapshot_id: str
    filter_set_id: str
    filter_set_version: str
    source_id: str
    source_version: str


# ── Validator ─────────────────────────────────────────────────────────────────

def validate_symbol_filters(
    order,  # PaperOrder with side/order_type/symbol/requested_qty/price/max_quote_amount
    snapshot: SymbolFilterSnapshot | None,
    market_ref: MarketNotionalReference | Sequence[MarketNotionalReference] | None = None,
) -> SymbolFilterResult:
    """Pure BUY LIMIT/MARKET entry filter validator.

    Parameters
    ----------
    order : PaperOrder-like
        Must expose ``side``, ``order_type``, ``symbol``, ``requested_qty``,
        ``price`` (for LIMIT), and ``max_quote_amount`` (for MARKET BUY).
    snapshot : SymbolFilterSnapshot | None
        Immutable, versioned filter snapshot.
    market_ref : MarketNotionalReference | Sequence[MarketNotionalReference] | None
        Explicit market reference(s), keyed by ``avg_price_mins``; required for
        every notional rule that applies to MARKET orders.

    Returns
    -------
    SymbolFilterResult with stable codes and exact snapshot/source refs.
    """
    # ── 1. Exit / non-entry → not_applicable (no filter data required) ──
    from src.models.paper_contracts import PaperOrderSide, PaperOrderType

    if order.side != PaperOrderSide.BUY or order.order_type not in (
        PaperOrderType.LIMIT,
        PaperOrderType.MARKET,
    ):
        snap_id = snapshot.snapshot_id if snapshot else ""
        return SymbolFilterResult(
            approved=True,
            code="symbol_filter_not_applicable",
            snapshot_id=snap_id,
            filter_set_id=snapshot.filter_set_id if snapshot else "",
            filter_set_version=snapshot.filter_set_version if snapshot else "",
            source_id=snapshot.source_id if snapshot else "",
            source_version=snapshot.source_version if snapshot else "",
        )

    # ── 2. Snapshot identity / presence ──
    if snapshot is None:
        return SymbolFilterResult(
            approved=False, code="symbol_filter_missing",
            snapshot_id="", filter_set_id="", filter_set_version="",
            source_id="", source_version="",
        )

    # Revalidate even model_construct-created DTOs
    try:
        snapshot = SymbolFilterSnapshot.model_validate(snapshot.model_dump(warnings=False))
    except (ValidationError, AttributeError, TypeError, ValueError):
        return SymbolFilterResult(
            approved=False, code="symbol_filter_invalid",
            snapshot_id=snapshot.snapshot_id if hasattr(snapshot, "snapshot_id") else "",
            filter_set_id=snapshot.filter_set_id if hasattr(snapshot, "filter_set_id") else "",
            filter_set_version=snapshot.filter_set_version if hasattr(snapshot, "filter_set_version") else "",
            source_id=snapshot.source_id if hasattr(snapshot, "source_id") else "",
            source_version=snapshot.source_version if hasattr(snapshot, "source_version") else "",
        )

    # ── 3. Unsupported filter types → invalid ──
    if snapshot.unhandled_filter_types:
        return SymbolFilterResult(
            approved=False, code="symbol_filter_invalid",
            snapshot_id=snapshot.snapshot_id,
            filter_set_id=snapshot.filter_set_id,
            filter_set_version=snapshot.filter_set_version,
            source_id=snapshot.source_id,
            source_version=snapshot.source_version,
        )

    # ── 4. Required filters must be present ──
    if snapshot.price_filter is None or snapshot.lot_size is None:
        return SymbolFilterResult(
            approved=False, code="symbol_filter_missing",
            snapshot_id=snapshot.snapshot_id,
            filter_set_id=snapshot.filter_set_id,
            filter_set_version=snapshot.filter_set_version,
            source_id=snapshot.source_id,
            source_version=snapshot.source_version,
        )
    is_market = order.order_type == PaperOrderType.MARKET
    if is_market:
        if snapshot.market_lot_size is None:
            return SymbolFilterResult(
                approved=False, code="symbol_filter_missing",
                snapshot_id=snapshot.snapshot_id,
                filter_set_id=snapshot.filter_set_id,
                filter_set_version=snapshot.filter_set_version,
                source_id=snapshot.source_id,
                source_version=snapshot.source_version,
            )
    if snapshot.min_notional is None and snapshot.notional is None:
        return SymbolFilterResult(
            approved=False, code="symbol_filter_missing",
            snapshot_id=snapshot.snapshot_id,
            filter_set_id=snapshot.filter_set_id,
            filter_set_version=snapshot.filter_set_version,
            source_id=snapshot.source_id,
            source_version=snapshot.source_version,
        )

    symbol_parts = order.symbol.split("/")
    if (
        len(symbol_parts) != 2
        or order.symbol != snapshot.symbol
        or (symbol_parts[0], symbol_parts[1])
        != (snapshot.base_asset, snapshot.quote_asset)
    ):
        return SymbolFilterResult(
            approved=False, code="symbol_filter_invalid",
            snapshot_id=snapshot.snapshot_id,
            filter_set_id=snapshot.filter_set_id,
            filter_set_version=snapshot.filter_set_version,
            source_id=snapshot.source_id,
            source_version=snapshot.source_version,
        )

    # ── 5. Validate order fields ──
    try:
        qty = _required_decimal(order.requested_qty)
    except (ValueError, TypeError):
        return SymbolFilterResult(
            approved=False, code="symbol_filter_invalid",
            snapshot_id=snapshot.snapshot_id,
            filter_set_id=snapshot.filter_set_id,
            filter_set_version=snapshot.filter_set_version,
            source_id=snapshot.source_id,
            source_version=snapshot.source_version,
        )
    if qty <= 0:
        return SymbolFilterResult(
            approved=False, code="symbol_filter_invalid",
            snapshot_id=snapshot.snapshot_id,
            filter_set_id=snapshot.filter_set_id,
            filter_set_version=snapshot.filter_set_version,
            source_id=snapshot.source_id,
            source_version=snapshot.source_version,
        )

    is_limit = order.order_type == PaperOrderType.LIMIT
    is_market = order.order_type == PaperOrderType.MARKET
    limit_price: Decimal | None = None
    if is_limit:
        try:
            limit_price = _required_decimal(order.price)
        except (ValueError, TypeError):
            return SymbolFilterResult(
                approved=False, code="symbol_filter_invalid",
                snapshot_id=snapshot.snapshot_id,
                filter_set_id=snapshot.filter_set_id,
                filter_set_version=snapshot.filter_set_version,
                source_id=snapshot.source_id,
                source_version=snapshot.source_version,
            )
        if limit_price <= 0:
            return SymbolFilterResult(
                approved=False, code="symbol_filter_invalid",
                snapshot_id=snapshot.snapshot_id,
                filter_set_id=snapshot.filter_set_id,
                filter_set_version=snapshot.filter_set_version,
                source_id=snapshot.source_id,
                source_version=snapshot.source_version,
            )

    # ── 6. PRICE_FILTER checks (LIMIT only) ──
    pf = snapshot.price_filter
    violations: list[str] = []

    if is_limit and limit_price is not None:
        # Zero tick_size = disabled; zero min/max = disabled bound
        if pf.tick_size != Decimal("0"):
            if not _is_step_multiple(limit_price, pf.tick_size):
                violations.append("price_not_tick_multiple")
        if pf.min_price != Decimal("0"):
            if limit_price < pf.min_price:
                violations.append(f"price_below_min:{pf.min_price}")
        if pf.max_price != Decimal("0"):
            if limit_price > pf.max_price:
                violations.append(f"price_above_max:{pf.max_price}")

    # ── 7. LOT_SIZE checks (LIMIT + MARKET) ──
    ls = snapshot.lot_size
    if ls.step_size != Decimal("0"):
        if not _is_step_multiple(qty, ls.step_size):
            violations.append("qty_not_step_multiple")
    if ls.min_qty != Decimal("0"):
        if qty < ls.min_qty:
            violations.append(f"qty_below_min:{ls.min_qty}")
    if ls.max_qty != Decimal("0"):
        if qty > ls.max_qty:
            violations.append(f"qty_above_max:{ls.max_qty}")

    # ── 8. MARKET_LOT_SIZE checks (MARKET only) ──
    if is_market and snapshot.market_lot_size is not None:
        mls = snapshot.market_lot_size
        if mls.step_size != Decimal("0"):
            if not _is_step_multiple(qty, mls.step_size):
                violations.append("market_qty_not_step_multiple")
        if mls.min_qty != Decimal("0"):
            if qty < mls.min_qty:
                violations.append(f"market_qty_below_min:{mls.min_qty}")
        if mls.max_qty != Decimal("0"):
            if qty > mls.max_qty:
                violations.append(f"market_qty_above_max:{mls.max_qty}")

    # ── 9. NOTIONAL / MIN_NOTIONAL checks ──
    notional_rules: list[MinNotionalFilter | NotionalFilter] = []
    if snapshot.min_notional is not None:
        notional_rules.append(snapshot.min_notional)
    if snapshot.notional is not None:
        notional_rules.append(snapshot.notional)

    if is_limit:
        try:
            order_notional = _exact_product(qty, limit_price)
        except (ValueError, TypeError, ArithmeticError):
            order_notional = Decimal("0")
        if order_notional <= 0:
            return SymbolFilterResult(
                approved=False, code="symbol_filter_invalid",
                snapshot_id=snapshot.snapshot_id,
                filter_set_id=snapshot.filter_set_id,
                filter_set_version=snapshot.filter_set_version,
                source_id=snapshot.source_id,
                source_version=snapshot.source_version,
            )
        for rule in notional_rules:
            if order_notional < rule.min_notional:
                violations.append(f"notional_below_min:{rule.min_notional}")
            if isinstance(rule, NotionalFilter) and order_notional > rule.max_notional:
                violations.append(f"notional_above_max:{rule.max_notional}")

    if is_market:
        required_windows: set[int] = set()
        for rule in notional_rules:
            if isinstance(rule, MinNotionalFilter):
                if rule.applies_to_market:
                    required_windows.add(rule.avg_price_mins)
            elif rule.apply_min_to_market or rule.apply_max_to_market:
                required_windows.add(rule.avg_price_mins)

        refs_by_window: dict[int, MarketNotionalReference] = {}
        if required_windows and market_ref is None:
            return SymbolFilterResult(
                approved=False, code="symbol_filter_missing",
                snapshot_id=snapshot.snapshot_id,
                filter_set_id=snapshot.filter_set_id,
                filter_set_version=snapshot.filter_set_version,
                source_id=snapshot.source_id,
                source_version=snapshot.source_version,
            )
        if market_ref is not None:
            raw_refs = (
                (market_ref,)
                if isinstance(market_ref, MarketNotionalReference)
                else tuple(market_ref)
                if isinstance(market_ref, Sequence)
                else ()
            )
            if not raw_refs:
                return SymbolFilterResult(
                    approved=False,
                    code=(
                        "symbol_filter_missing"
                        if required_windows
                        else "symbol_filter_invalid"
                    ),
                    snapshot_id=snapshot.snapshot_id,
                    filter_set_id=snapshot.filter_set_id,
                    filter_set_version=snapshot.filter_set_version,
                    source_id=snapshot.source_id,
                    source_version=snapshot.source_version,
                )
            try:
                for raw_ref in raw_refs:
                    ref = MarketNotionalReference.model_validate(
                        raw_ref.model_dump(warnings=False)
                    )
                    if ref.symbol != order.symbol or ref.avg_price_mins not in required_windows:
                        raise ValueError("market reference does not match symbol/rule window")
                    if ref.avg_price_mins in refs_by_window:
                        raise ValueError("duplicate market reference window")
                    refs_by_window[ref.avg_price_mins] = ref
            except (ValidationError, AttributeError, TypeError, ValueError):
                return SymbolFilterResult(
                    approved=False, code="symbol_filter_invalid",
                    snapshot_id=snapshot.snapshot_id,
                    filter_set_id=snapshot.filter_set_id,
                    filter_set_version=snapshot.filter_set_version,
                    source_id=snapshot.source_id,
                    source_version=snapshot.source_version,
                )
            if set(refs_by_window) != required_windows:
                return SymbolFilterResult(
                    approved=False, code="symbol_filter_missing",
                    snapshot_id=snapshot.snapshot_id,
                    filter_set_id=snapshot.filter_set_id,
                    filter_set_version=snapshot.filter_set_version,
                    source_id=snapshot.source_id,
                    source_version=snapshot.source_version,
                )

        for rule in notional_rules:
            if isinstance(rule, MinNotionalFilter):
                if not rule.applies_to_market:
                    continue
                ref = refs_by_window[rule.avg_price_mins]
                market_notional = _exact_product(qty, ref.price)
                if market_notional < rule.min_notional:
                    violations.append(f"market_notional_below_min:{rule.min_notional}")
            else:
                if not (rule.apply_min_to_market or rule.apply_max_to_market):
                    continue
                ref = refs_by_window[rule.avg_price_mins]
                market_notional = _exact_product(qty, ref.price)
                if rule.apply_min_to_market and market_notional < rule.min_notional:
                    violations.append(f"market_notional_below_min:{rule.min_notional}")
                if rule.apply_max_to_market and market_notional > rule.max_notional:
                    violations.append(f"market_notional_above_max:{rule.max_notional}")

    # ── 10. Outcome ──
    if violations:
        return SymbolFilterResult(
            approved=False, code="symbol_filter_violation",
            snapshot_id=snapshot.snapshot_id,
            filter_set_id=snapshot.filter_set_id,
            filter_set_version=snapshot.filter_set_version,
            source_id=snapshot.source_id,
            source_version=snapshot.source_version,
        )

    return SymbolFilterResult(
        approved=True, code="symbol_filter_approved",
        snapshot_id=snapshot.snapshot_id,
        filter_set_id=snapshot.filter_set_id,
        filter_set_version=snapshot.filter_set_version,
        source_id=snapshot.source_id,
        source_version=snapshot.source_version,
    )


def _exact_product(left: Decimal, right: Decimal) -> Decimal:
    """Multiply finite Decimals exactly with operand-derived local precision."""
    precision = max(1, len(left.as_tuple().digits) + len(right.as_tuple().digits))
    with localcontext() as ctx:
        ctx.prec = precision
        ctx.Emin = min(ctx.Emin, left.as_tuple().exponent + right.as_tuple().exponent - precision)
        ctx.Emax = max(ctx.Emax, left.adjusted() + right.adjusted() + precision)
        return left * right
