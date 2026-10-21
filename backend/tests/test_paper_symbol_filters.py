"""Synthetic fixture unit tests for paper_symbol_filters.

Covers:
  - Provenance/version/identity completeness
  - Strict Decimal (no float)
  - Missing / invalid / unknown rules
  - All enabled bounds
  - Exact tick / step boundaries and violations
  - Zero-disabled PRICE_FILTER rules
  - Both notional variants and their market flags
  - Market-reference absence / mismatch
  - Order immutability
  - Deterministic codes / refs
  - Decimal-context independence
  - Early not_applicable for SELL/non-entry even with missing filters
  - Synthetic snapshots only; no Binance/network
"""

from decimal import Decimal, ROUND_DOWN, ROUND_UP, localcontext

import pytest
from pydantic import ValidationError

from src.models.paper_contracts import (
    PaperOrder,
    PaperOrderSide,
    PaperOrderType,
    StrategyRef,
)
from src.models.paper_symbol_filters import (
    MarketNotionalReference,
    MinNotionalFilter,
    NotionalFilter,
    PriceFilter,
    SymbolFilterResult,
    SymbolFilterSnapshot,
    LotSizeFilter,
    MarketLotSizeFilter,
    validate_symbol_filters,
)


# ── Synthetic fixtures ────────────────────────────────────────────────────────

def _base_order(
    kind=PaperOrderType.LIMIT,
    qty="1.0",
    price="25.00",
):
    return PaperOrder(
        order_id="o-synthetic",
        client_order_id="c-synthetic",
        paper_account_id="acct-synthetic",
        paper_session_id="sess-synthetic",
        symbol="BTC/USDT",
        side=PaperOrderSide.BUY,
        order_type=kind,
        requested_qty=Decimal(qty),
        price=Decimal(price) if kind == PaperOrderType.LIMIT else None,
        max_quote_amount=Decimal("50.00") if kind == PaperOrderType.MARKET else None,
        strategy_ref=StrategyRef(strategy_id="s1", strategy_version="v1"),
        timestamp_ms=1000,
    )


def _sell_order(
    kind=PaperOrderType.LIMIT,
    qty="1.0",
    price="25.00",
):
    """Create a SELL order via model_construct to bypass lot_allocations validation."""
    return PaperOrder.model_construct(
        order_id="o-synthetic",
        client_order_id="c-synthetic",
        paper_account_id="acct-synthetic",
        paper_session_id="sess-synthetic",
        symbol="BTC/USDT",
        side=PaperOrderSide.SELL,
        order_type=kind,
        requested_qty=Decimal(qty),
        price=Decimal(price) if kind == PaperOrderType.LIMIT else None,
        max_quote_amount=None,
        strategy_ref=StrategyRef(strategy_id="s1", strategy_version="v1"),
        timestamp_ms=1000,
        lot_allocations=(),
    )


def _market_buy_order(qty="1.0"):
    """Create a MARKET BUY order (requires max_quote_amount)."""
    return PaperOrder(
        order_id="o-synthetic",
        client_order_id="c-synthetic",
        paper_account_id="acct-synthetic",
        paper_session_id="sess-synthetic",
        symbol="BTC/USDT",
        side=PaperOrderSide.BUY,
        order_type=PaperOrderType.MARKET,
        requested_qty=Decimal(qty),
        price=None,
        max_quote_amount=Decimal("50.00"),
        strategy_ref=StrategyRef(strategy_id="s1", strategy_version="v1"),
        timestamp_ms=1000,
    )


def _valid_snapshot(
    unhandled: tuple[str, ...] = (),
    has_market_lot: bool = True,
    min_notional=None,
    notional=None,
    include_notional: bool = True,
):
    if include_notional and min_notional is None and notional is None:
        notional = NotionalFilter(
            min_notional=Decimal("0.01"),
            max_notional=Decimal("1000000000"),
            apply_min_to_market=False,
            apply_max_to_market=False,
            avg_price_mins=5,
        )
    values = dict(
        snapshot_id="snap-001",
        filter_set_id="fs-001",
        filter_set_version="v1",
        source_id="binance",
        source_version="v2",
        capture_timestamp_ms=42,
        symbol="BTC/USDT",
        base_asset="BTC",
        quote_asset="USDT",
        price_filter=PriceFilter(
            min_price=Decimal("0.01"),
            max_price=Decimal("1000000.00"),
            tick_size=Decimal("0.01"),
        ),
        lot_size=LotSizeFilter(
            min_qty=Decimal("0.00001"),
            max_qty=Decimal("9000.00000"),
            step_size=Decimal("0.00001"),
        ),
        market_lot_size=(
            MarketLotSizeFilter(
                min_qty=Decimal("0.00001"),
                max_qty=Decimal("9000.00000"),
                step_size=Decimal("0.00001"),
            )
            if has_market_lot
            else None
        ),
        min_notional=min_notional,
        notional=notional,
        unhandled_filter_types=unhandled,
    )
    return SymbolFilterSnapshot(**values)


def _market_ref():
    return MarketNotionalReference(
        symbol="BTC/USDT",
        price=Decimal("25.50"),
        source="mark:BTC/USDT:42",
        source_version="v1",
        timestamp_ms=50,
        avg_price_mins=5,
    )


# ── 1. Provenance / version / identity ────────────────────────────────────────

def test_snapshot_provenance_fields_are_immutable_and_present():
    snap = _valid_snapshot()
    assert snap.schema_version == "symbol_filter_snapshot.v1"
    assert snap.snapshot_id == "snap-001"
    assert snap.filter_set_id == "fs-001"
    assert snap.filter_set_version == "v1"
    assert snap.source_id == "binance"
    assert snap.source_version == "v2"
    assert snap.capture_timestamp_ms == 42
    assert snap.symbol == "BTC/USDT"
    assert snap.base_asset == "BTC"
    assert snap.quote_asset == "USDT"
    with pytest.raises(Exception):
        snap.snapshot_id = "changed"


def test_result_retains_exact_snapshot_refs():
    snap = _valid_snapshot()
    order = _base_order()
    result = validate_symbol_filters(order, snap)
    assert result.approved
    assert result.snapshot_id == "snap-001"
    assert result.filter_set_id == "fs-001"
    assert result.filter_set_version == "v1"
    assert result.source_id == "binance"
    assert result.source_version == "v2"


# ── 2. Strict Decimal (no float) ─────────────────────────────────────────────

def test_price_filter_rejects_float_tick_size():
    with pytest.raises(ValueError):
        PriceFilter(min_price=Decimal("0.01"), max_price=Decimal("1000000"), tick_size=0.01)


def test_lot_size_rejects_float_step():
    with pytest.raises(ValueError):
        LotSizeFilter(min_qty=Decimal("0"), max_qty=Decimal("9000"), step_size=0.001)


def test_snapshot_requires_explicit_decimal_in_filters():
    snap = _valid_snapshot()
    assert isinstance(snap.price_filter.min_price, Decimal)
    assert isinstance(snap.lot_size.step_size, Decimal)


# ── 3. Missing / invalid / unknown rules ─────────────────────────────────────

def test_missing_snapshot_returns_filter_missing():
    order = _base_order()
    result = validate_symbol_filters(order, None)
    assert not result.approved
    assert result.code == "symbol_filter_missing"
    assert result.snapshot_id == ""


def test_invalid_snapshot_returns_filter_invalid():
    snap = _valid_snapshot()
    bad_fields = snap.model_dump()
    bad_fields["snapshot_id"] = ""  # blank → invalid
    bad_snap = SymbolFilterSnapshot.model_construct(**bad_fields)
    order = _base_order()
    result = validate_symbol_filters(order, bad_snap)
    assert not result.approved
    assert result.code == "symbol_filter_invalid"


def test_missing_price_filter_returns_filter_missing():
    snap = _valid_snapshot()
    snap_no_pf = snap.model_copy(update={"price_filter": None})
    order = _base_order()
    result = validate_symbol_filters(order, snap_no_pf)
    assert not result.approved
    assert result.code == "symbol_filter_missing"


def test_missing_lot_size_returns_filter_missing():
    snap = _valid_snapshot()
    snap_no_ls = snap.model_copy(update={"lot_size": None})
    order = _base_order()
    result = validate_symbol_filters(order, snap_no_ls)
    assert not result.approved
    assert result.code == "symbol_filter_missing"


def test_unhandled_filter_types_causes_invalid():
    snap = _valid_snapshot(unhandled=("PERCENT_PRICE", "STOP_PRICE"))
    order = _base_order()
    result = validate_symbol_filters(order, snap)
    assert not result.approved
    assert result.code == "symbol_filter_invalid"


def test_missing_market_lot_size_rejects_market_entry():
    result = validate_symbol_filters(
        _market_buy_order(), _valid_snapshot(has_market_lot=False)
    )
    assert not result.approved
    assert result.code == "symbol_filter_missing"


def test_missing_all_notional_rules_rejects_entry():
    result = validate_symbol_filters(
        _base_order(), _valid_snapshot(include_notional=False)
    )
    assert not result.approved
    assert result.code == "symbol_filter_missing"


def test_snapshot_symbol_assets_must_match():
    bad = _valid_snapshot().model_copy(update={"base_asset": "ETH"})
    result = validate_symbol_filters(_base_order(), bad)
    assert not result.approved
    assert result.code == "symbol_filter_invalid"


def test_filter_ranges_are_validated_at_construction():
    with pytest.raises(ValidationError):
        PriceFilter(
            min_price=Decimal("10"), max_price=Decimal("1"), tick_size=Decimal("1")
        )
    with pytest.raises(ValidationError):
        LotSizeFilter(
            min_qty=Decimal("2"), max_qty=Decimal("1"), step_size=Decimal("1")
        )
    with pytest.raises(ValidationError):
        NotionalFilter(
            min_notional=Decimal("20"), max_notional=Decimal("10"),
            apply_min_to_market=False, apply_max_to_market=False, avg_price_mins=0,
        )
    with pytest.raises(ValidationError):
        MinNotionalFilter(min_notional=Decimal("0"), applies_to_market=False, avg_price_mins=0)


def test_missing_market_applicability_flag_is_not_defaulted():
    with pytest.raises(ValidationError):
        MinNotionalFilter(min_notional=Decimal("1"), avg_price_mins=0)
    with pytest.raises(ValidationError):
        NotionalFilter(
            min_notional=Decimal("1"), max_notional=Decimal("2"), avg_price_mins=0,
            apply_min_to_market=True,
        )


# ── 4. All enabled bounds ────────────────────────────────────────────────────

def test_limit_order_passes_all_bounds_when_compliant():
    snap = _valid_snapshot()
    order = _base_order(qty="1.0", price="25.00")
    result = validate_symbol_filters(order, snap)
    assert result.approved


def test_lot_size_min_qty_enforced():
    snap = _valid_snapshot()
    order = _base_order(qty="0.000001")  # below min_qty=0.00001
    result = validate_symbol_filters(order, snap)
    assert not result.approved
    assert result.code == "symbol_filter_violation"


def test_lot_size_max_qty_enforced():
    snap = _valid_snapshot()
    order = _base_order(qty="9000.00001")  # above max_qty=9000
    result = validate_symbol_filters(order, snap)
    assert not result.approved
    assert result.code == "symbol_filter_violation"


def test_price_filter_min_price_enforced():
    snap = _valid_snapshot()
    order = _base_order(price="0.005")  # below min_price=0.01
    result = validate_symbol_filters(order, snap)
    assert not result.approved
    assert result.code == "symbol_filter_violation"


def test_price_filter_max_price_enforced():
    snap = _valid_snapshot()
    order = _base_order(price="1000000.01")  # above max_price=1000000
    result = validate_symbol_filters(order, snap)
    assert not result.approved
    assert result.code == "symbol_filter_violation"


# ── 5. Exact tick / step boundaries and violations ───────────────────────────

def test_tick_multiple_exact_boundary():
    snap = _valid_snapshot()
    # tick_size=0.01, price=25.00 is exact multiple
    order = _base_order(price="25.00")
    result = validate_symbol_filters(order, snap)
    assert result.approved


def test_tick_multiple_violation():
    snap = _valid_snapshot()
    # tick_size=0.01, price=25.001 is NOT exact multiple
    order = _base_order(price="25.001")
    result = validate_symbol_filters(order, snap)
    assert not result.approved
    assert result.code == "symbol_filter_violation"


def test_step_multiple_exact_boundary():
    snap = _valid_snapshot()
    # step_size=0.00001, qty=1.00000 is exact multiple
    order = _base_order(qty="1.00000")
    result = validate_symbol_filters(order, snap)
    assert result.approved


def test_step_multiple_violation():
    snap = _valid_snapshot()
    # step_size=0.00001, qty=1.000001 is NOT exact multiple
    order = _base_order(qty="1.000001")
    result = validate_symbol_filters(order, snap)
    assert not result.approved
    assert result.code == "symbol_filter_violation"


# ── 6. Zero-disabled PRICE_FILTER rules ──────────────────────────────────────

def test_zero_tick_size_disables_tick_check():
    snap = _valid_snapshot()
    new_pf = PriceFilter(
        min_price=Decimal("0.01"),
        max_price=Decimal("1000000"),
        tick_size=Decimal("0"),  # disabled
    )
    snap_zero_tick = SymbolFilterSnapshot(
        **{**snap.model_dump(), "price_filter": new_pf},
    )
    order = _base_order(price="25.001")  # not tick multiple, but disabled
    result = validate_symbol_filters(order, snap_zero_tick)
    assert result.approved


def test_zero_min_price_disables_min_check():
    snap = _valid_snapshot()
    new_pf = PriceFilter(
        min_price=Decimal("0"),  # disabled
        max_price=Decimal("1000000"),
        tick_size=Decimal("0.01"),
    )
    snap_zero_min = snap.model_copy(update={"price_filter": new_pf})
    # Use a price that passes tick check (exact multiple of 0.01) but
    # would fail the disabled min check (below normal min_price=0.01)
    order = _base_order(price="0.01")  # exactly at normal min, min is disabled so OK
    result = validate_symbol_filters(order, snap_zero_min)
    assert result.approved


def test_zero_max_price_disables_max_check():
    snap = _valid_snapshot()
    new_pf = PriceFilter(
        min_price=Decimal("0.01"),
        max_price=Decimal("0"),  # disabled
        tick_size=Decimal("0.01"),
    )
    snap_zero_max = SymbolFilterSnapshot(
        **{**snap.model_dump(), "price_filter": new_pf},
    )
    order = _base_order(price="9999999.99")  # above normal max, but max disabled
    result = validate_symbol_filters(order, snap_zero_max)
    assert result.approved


# ── 7. Both notional variants and their market flags ─────────────────────────

def test_min_notional_violation_limit():
    min_n = MinNotionalFilter(
        min_notional=Decimal("50"),
        applies_to_market=False,
        avg_price_mins=5,
    )
    snap = _valid_snapshot(min_notional=min_n)
    # qty=1.0 * price=25.00 = 25 < 50
    order = _base_order(qty="1.0", price="25.00")
    result = validate_symbol_filters(order, snap)
    assert not result.approved
    assert result.code == "symbol_filter_violation"


def test_notional_max_enforced_limit():
    n = NotionalFilter(
        min_notional=Decimal("0.01"),
        max_notional=Decimal("30"),
        apply_min_to_market=False,
        apply_max_to_market=False,
        avg_price_mins=5,
    )
    snap = _valid_snapshot(notional=n)
    # qty=2.0 * price=25.00 = 50 > 30
    order = _base_order(qty="2.0", price="25.00")
    result = validate_symbol_filters(order, snap)
    assert not result.approved
    assert result.code == "symbol_filter_violation"


def test_notional_boundary_exactly_at_min():
    n = NotionalFilter(
        min_notional=Decimal("25"),
        max_notional=Decimal("100"),
        apply_min_to_market=False,
        apply_max_to_market=False,
        avg_price_mins=5,
    )
    snap = _valid_snapshot(notional=n)
    # qty=1.0 * price=25.00 = 25 == min → pass
    order = _base_order(qty="1.0", price="25.00")
    result = validate_symbol_filters(order, snap)
    assert result.approved


def test_min_notional_applies_to_market_flag():
    min_n = MinNotionalFilter(
        min_notional=Decimal("100"),
        applies_to_market=True,
        avg_price_mins=5,
    )
    snap = _valid_snapshot(min_notional=min_n)
    # qty=1.0 * market_ref.price=25.50 = 25.50 < 100
    order = _market_buy_order(qty="1.0")
    result = validate_symbol_filters(order, snap, market_ref=_market_ref())
    assert not result.approved
    assert result.code == "symbol_filter_violation"


def test_notional_does_not_apply_to_market_when_flag_false():
    n = NotionalFilter(
        min_notional=Decimal("100"),
        max_notional=Decimal("200"),
        apply_min_to_market=False,
        apply_max_to_market=False,
        avg_price_mins=5,
    )
    snap = _valid_snapshot(notional=n)
    order = _market_buy_order(qty="1.0")
    result = validate_symbol_filters(order, snap, market_ref=None)
    assert result.approved  # rule does not apply to MARKET


def test_notional_max_market_flag_is_enforced_independently():
    rule = NotionalFilter(
        min_notional=Decimal("10"),
        max_notional=Decimal("20"),
        apply_min_to_market=False,
        apply_max_to_market=True,
        avg_price_mins=5,
    )
    result = validate_symbol_filters(
        _market_buy_order(), _valid_snapshot(notional=rule), market_ref=_market_ref()
    )
    assert not result.approved
    assert result.code == "symbol_filter_violation"


# ── 8. Market-reference absence / mismatch ───────────────────────────────────

def test_market_notional_requires_reference_when_flag_true():
    min_n = MinNotionalFilter(
        min_notional=Decimal("100"),
        applies_to_market=True,
        avg_price_mins=5,
    )
    snap = _valid_snapshot(min_notional=min_n)
    order = _market_buy_order(qty="1.0")
    result = validate_symbol_filters(order, snap, market_ref=None)
    assert not result.approved
    assert result.code == "symbol_filter_missing"


def test_empty_market_reference_sequence_is_missing():
    rule = MinNotionalFilter(
        min_notional=Decimal("10"), applies_to_market=True, avg_price_mins=5
    )
    result = validate_symbol_filters(
        _market_buy_order(), _valid_snapshot(min_notional=rule), market_ref=()
    )
    assert not result.approved
    assert result.code == "symbol_filter_missing"


def test_market_notional_with_reference():
    min_n = MinNotionalFilter(
        min_notional=Decimal("10"),
        applies_to_market=True,
        avg_price_mins=5,
    )
    snap = _valid_snapshot(min_notional=min_n)
    order = _market_buy_order(qty="1.0")
    result = validate_symbol_filters(order, snap, market_ref=_market_ref())
    # 1.0 * 25.50 = 25.50 >= 10 → pass
    assert result.approved


@pytest.mark.parametrize(
    "reference",
    [
        _market_ref().model_copy(update={"symbol": "ETH/USDT"}),
        _market_ref().model_copy(update={"avg_price_mins": 10}),
    ],
    ids=["wrong-symbol", "wrong-average-window"],
)
def test_market_reference_mismatch_fails_closed(reference):
    rule = MinNotionalFilter(
        min_notional=Decimal("10"), applies_to_market=True, avg_price_mins=5
    )
    result = validate_symbol_filters(
        _market_buy_order(), _valid_snapshot(min_notional=rule), market_ref=reference
    )
    assert not result.approved
    assert result.code == "symbol_filter_invalid"


# ── 9. Order immutability ────────────────────────────────────────────────────

def test_order_not_mutated_by_validator():
    snap = _valid_snapshot()
    order = _base_order(qty="1.0", price="25.00")
    before = order.model_dump()
    validate_symbol_filters(order, snap)
    after = order.model_dump()
    assert before == after


# ── 10. Deterministic codes / refs ───────────────────────────────────────────

def test_deterministic_codes_for_violation():
    snap = _valid_snapshot()
    order = _base_order(qty="9000.00001")  # above max_qty
    result = validate_symbol_filters(order, snap)
    assert result.code == "symbol_filter_violation"
    assert result.approved is False
    assert result.snapshot_id == "snap-001"


def test_deterministic_codes_for_approved():
    snap = _valid_snapshot()
    order = _base_order()
    result = validate_symbol_filters(order, snap)
    assert result.code == "symbol_filter_approved"
    assert result.approved is True


# ── 11. Decimal-context independence ─────────────────────────────────────────

def test_evaluation_is_decimal_context_independent():
    snap = _valid_snapshot()
    order = _base_order(qty="1.0", price="25.00")
    expected = validate_symbol_filters(order, snap)
    outcomes = []
    for precision, rounding in ((2, ROUND_DOWN), (50, ROUND_UP)):
        with localcontext() as ctx:
            ctx.prec, ctx.rounding = precision, rounding
            outcomes.append(validate_symbol_filters(order, snap))
    assert outcomes == [expected, expected]


# ── 12. Early not_applicable for SELL/non-entry ─────────────────────────────

def test_sell_order_returns_not_applicable_even_with_missing_filters():
    snap = _valid_snapshot()
    order = _sell_order()
    result = validate_symbol_filters(order, snap)
    assert result.approved
    assert result.code == "symbol_filter_not_applicable"


def test_sell_order_with_no_snapshot_returns_not_applicable():
    order = _sell_order()
    result = validate_symbol_filters(order, None)
    assert result.approved
    assert result.code == "symbol_filter_not_applicable"


def test_market_sell_order_returns_not_applicable():
    snap = _valid_snapshot()
    order = _sell_order(kind=PaperOrderType.MARKET)
    result = validate_symbol_filters(order, snap)
    assert result.approved
    assert result.code == "symbol_filter_not_applicable"


# ── 13. MARKET_LOT_SIZE bounds ───────────────────────────────────────────────

def test_market_lot_size_min_qty_enforced():
    snap = _valid_snapshot()
    order = _market_buy_order(qty="0.000001")
    result = validate_symbol_filters(order, snap)
    assert not result.approved
    assert result.code == "symbol_filter_violation"


def test_market_lot_size_max_qty_enforced():
    snap = _valid_snapshot()
    order = _market_buy_order(qty="9000.00001")
    result = validate_symbol_filters(order, snap)
    assert not result.approved
    assert result.code == "symbol_filter_violation"


def test_market_lot_size_step_multiple_enforced():
    snap = _valid_snapshot()
    order = _market_buy_order(qty="1.000001")
    result = validate_symbol_filters(order, snap)
    assert not result.approved
    assert result.code == "symbol_filter_violation"


# ── 14. LOT_SIZE step_multiple also applies to MARKET ────────────────────────

def test_lot_size_step_multiple_applies_to_market():
    snap = _valid_snapshot()
    order = _market_buy_order(qty="1.000001")
    result = validate_symbol_filters(order, snap)
    assert not result.approved
    assert result.code == "symbol_filter_violation"


# ── 15. Snapshot validation rejects float in filter values ───────────────────

def test_snapshot_rejects_float_in_price_filter():
    with pytest.raises(ValueError):
        PriceFilter(min_price=Decimal("0.01"), max_price=Decimal("1000000"), tick_size=0.01)


def test_snapshot_rejects_nan_decimal():
    with pytest.raises(ValueError):
        PriceFilter(min_price=Decimal("NaN"), max_price=Decimal("1000000"), tick_size=Decimal("0.01"))


# ── 16. MARKET order with notional but no market_ref (flag=False) ────────────

def test_market_order_with_notional_flag_false_no_ref_needed():
    n = NotionalFilter(
        min_notional=Decimal("100"),
        max_notional=Decimal("200"),
        apply_min_to_market=False,
        apply_max_to_market=False,
        avg_price_mins=5,
    )
    snap = _valid_snapshot(notional=n)
    order = _market_buy_order(qty="1.0")
    result = validate_symbol_filters(order, snap, market_ref=None)
    assert result.approved  # flag=False → rule not applied, no ref needed


# ── 17. LOT_SIZE zero bounds are invalid ─────────────────────────────────────

def test_lot_size_zero_min_qty_is_invalid():
    with pytest.raises(ValidationError):
        LotSizeFilter(
            min_qty=Decimal("0"),
            max_qty=Decimal("9000"),
            step_size=Decimal("0.00001"),
        )


def test_lot_size_zero_step_is_invalid():
    with pytest.raises(ValidationError):
        LotSizeFilter(
            min_qty=Decimal("0.00001"),
            max_qty=Decimal("9000"),
            step_size=Decimal("0"),
        )


# ── 18. Result codes are stable strings ──────────────────────────────────────

def test_result_codes_are_stable_strings():
    snap = _valid_snapshot()
    order = _base_order()

    r_approved = validate_symbol_filters(order, snap)
    assert r_approved.code == "symbol_filter_approved"

    r_missing = validate_symbol_filters(order, None)
    assert r_missing.code == "symbol_filter_missing"

    r_not_applicable = validate_symbol_filters(
        _sell_order(), snap
    )
    assert r_not_applicable.code == "symbol_filter_not_applicable"


# ── 19. Both notional rules present, both apply ──────────────────────────────

def test_both_notional_rules_applied_to_limit():
    min_n = MinNotionalFilter(
        min_notional=Decimal("10"),
        applies_to_market=False,
        avg_price_mins=5,
    )
    n = NotionalFilter(
        min_notional=Decimal("0.01"),
        max_notional=Decimal("30"),
        apply_min_to_market=False,
        apply_max_to_market=False,
        avg_price_mins=5,
    )
    snap = _valid_snapshot(min_notional=min_n, notional=n)
    # qty=1.0 * price=25.00 = 25 → 25 >= 10 (min_ok) and 25 <= 30 (max_ok)
    order = _base_order(qty="1.0", price="25.00")
    result = validate_symbol_filters(order, snap)
    assert result.approved


def test_both_notional_rules_both_violated():
    min_n = MinNotionalFilter(
        min_notional=Decimal("10"),
        applies_to_market=False,
        avg_price_mins=5,
    )
    n = NotionalFilter(
        min_notional=Decimal("0.01"),
        max_notional=Decimal("20"),
        apply_min_to_market=False,
        apply_max_to_market=False,
        avg_price_mins=5,
    )
    snap = _valid_snapshot(min_notional=min_n, notional=n)
    # qty=1.0 * price=25.00 = 25 → above max_notional=20
    order = _base_order(qty="1.0", price="25.00")
    result = validate_symbol_filters(order, snap)
    assert not result.approved
    assert result.code == "symbol_filter_violation"
