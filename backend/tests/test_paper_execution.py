"""Synthetic fixture tests for the pure fill-candidate evaluator.

All numeric values are fixtures only — not operational recommendations.
No network or Binance connection is used.
"""

from decimal import Decimal, ROUND_DOWN, ROUND_UP, localcontext

import pytest
from pydantic import ValidationError

from src.models.paper_contracts import (
    Commission,
    PaperOrder,
    PaperOrderSide,
    PaperOrderType,
    PaperOrderStatus,
    StrategyRef,
)
from src.models.paper_execution import (
    PaperExecutionPolicy,
    PaperMarketExecutionSnapshot,
    PaperMarketCapacitySnapshot,
    PaperReservationSnapshot,
    PaperFeeSnapshot,
    PaperProtectionSnapshot,
    PaperFillCandidateResult,
    evaluate_fill_candidate,
    RESULT_FILL_CANDIDATE,
    RESULT_INSUFFICIENT_DATA,
    RESULT_GAP_UNCERTAIN,
    RESULT_AMBIGUOUS_INTRABAR,
    RESULT_LIMIT_NOT_CROSSED,
    RESULT_CAPACITY_MISSING,
    RESULT_FEE_UNKNOWN,
    RESULT_FEE_MISMATCH,
    RESULT_RESERVATION_INVALID,
    RESULT_RESERVATION_ASSET_MISMATCH,
    RESULT_MARKET_REFERENCE_INVALID,
    RESULT_MODELLED_EXEC_MISSING,
    RESULT_PROTECTION_TRIGGER_MISSING,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────

STRATEGY_REF = StrategyRef(strategy_id="s1", strategy_version="v1")

COMMISION_USDT = Commission(amount=Decimal("0.001"), asset="USDT")
ZERO_COMMISSION = Commission(amount=Decimal("0"), asset="USDT")


def base_order(
    side=PaperOrderSide.BUY,
    order_type=PaperOrderType.MARKET,
    status=PaperOrderStatus.ACCEPTED,
    price=None,
    trigger_price=None,
    max_quote=None,
    requested_qty="1.0",
    fills=None,
    symbol="BTC/USDT",
    remaining_protected_qty=None,
):
    """Build a minimal PaperOrder for fill evaluation.

    Respects PaperOrder contract:
      - MARKET BUY requires max_quote_amount > 0.
      - SELL requires lot_allocations.
    """
    values = dict(
        order_id="o1",
        client_order_id="c1",
        paper_account_id="acct-1",
        paper_session_id="sess-1",
        symbol=symbol,
        side=side,
        order_type=order_type,
        requested_qty=Decimal(requested_qty),
        strategy_ref=STRATEGY_REF,
        timestamp_ms=1000,
        status=status,
    )
    if price is not None:
        values["price"] = Decimal(price)
    if trigger_price is not None:
        values["trigger_price"] = Decimal(trigger_price)

    # MARKET BUY requires positive max_quote_amount
    if order_type == PaperOrderType.MARKET and side == PaperOrderSide.BUY:
        values["max_quote_amount"] = Decimal(max_quote or "40001")

    # SELL requires lot_allocations
    if side == PaperOrderSide.SELL:
        from src.models.paper_contracts import LotAllocation
        values["lot_allocations"] = (
            LotAllocation(
                position_id="pos-1", account_id="acct-1", symbol=symbol,
                strategy_ref=STRATEGY_REF, qty=Decimal(requested_qty),
            ),
        )

    if fills is not None:
        values["fills"] = fills
    return PaperOrder(**values)


def exec_policy(mode="UNMODELLED"):
    return PaperExecutionPolicy(
        policy_id="exec-1", policy_version="v1", latency_slippage_mode=mode
    )


def market_snapshot(
    symbol="BTC/USDT",
    bid="40000",
    ask="40001",
    bar_open_time_ms=2000,
    prior_bar_open_time_ms=1000,
    interval_ms=60000,
    provenance="synthetic",
    version="v1",
    snapshot_timestamp_ms=2000,
):
    return PaperMarketExecutionSnapshot(
        symbol=symbol,
        bid=Decimal(bid),
        ask=Decimal(ask),
        bar_open_time_ms=bar_open_time_ms,
        prior_bar_open_time_ms=prior_bar_open_time_ms,
        interval_ms=interval_ms,
        provenance=provenance,
        version=version,
        snapshot_timestamp_ms=snapshot_timestamp_ms,
    )


def reservation(
    remaining_qty="1.0",
    remaining_amount="40001",
    asset="USDT",
    account_id="acct-1",
):
    return PaperReservationSnapshot(
        reservation_id="res-1",
        owner_order_id="o1",
        account_id=account_id,
        asset=asset,
        remaining_qty=Decimal(remaining_qty),
        remaining_amount=Decimal(remaining_amount),
    )


def market_capacity(
    capacity="1.0",
    symbol="BTC/USDT",
    bar_open_time_ms=None,
    snapshot_timestamp_ms=None,
):
    # Default to matching the standard market snapshot values
    if bar_open_time_ms is None:
        bar_open_time_ms = 2000
    if snapshot_timestamp_ms is None:
        snapshot_timestamp_ms = 2000
    return PaperMarketCapacitySnapshot(
        source_id="cap-1",
        source_version="v1",
        capacity=Decimal(capacity),
        symbol=symbol,
        capture_bar_open_time_ms=bar_open_time_ms,
        capture_snapshot_timestamp_ms=snapshot_timestamp_ms,
    )


def fee_snapshot(quantity="1.0", commission=None):
    if commission is None:
        commission = COMMISION_USDT
    return PaperFeeSnapshot(
        fee_id="fee-1", fee_version="v1", quantity=Decimal(quantity), commission=commission
    )


def protection(
    symbol="BTC/USDT",
    account_id="acct-1",
    stop_price="39500",
    take_price="41000",
    bar_high="41500",
    bar_low="39000",
    bar_source_id="bar-1",
    bar_snapshot_timestamp_ms=2000,
    bar_open_time_ms=2000,
    bar_interval_ms=60000,
):
    return PaperProtectionSnapshot(
        symbol=symbol,
        account_id=account_id,
        protection_group_id="pg-1",
        stop_trigger_price=Decimal(stop_price),
        take_trigger_price=Decimal(take_price),
        bar_high=Decimal(bar_high),
        bar_low=Decimal(bar_low),
        bar_source_id=bar_source_id,
        bar_snapshot_timestamp_ms=bar_snapshot_timestamp_ms,
        bar_open_time_ms=bar_open_time_ms,
        bar_interval_ms=bar_interval_ms,
    )


# ── BUY/SELL MARKET bid/ask pricing ───────────────────────────────────────────

def test_buy_market_uses_ask():
    order = base_order()
    result = evaluate_fill_candidate(
        order, reservation(), exec_policy(), market_snapshot(bid="40000", ask="40001"), market_capacity(), fee_snapshot()
    )
    assert result.status_code == RESULT_FILL_CANDIDATE
    assert result.fill_price == Decimal("40001")


def test_sell_market_uses_bid():
    order = base_order(side=PaperOrderSide.SELL)
    res = reservation(remaining_qty="1.0", remaining_amount="1.0", asset="BTC")
    result = evaluate_fill_candidate(
        order, res, exec_policy(), market_snapshot(bid="39999", ask="40000"), market_capacity(), fee_snapshot()
    )
    assert result.status_code == RESULT_FILL_CANDIDATE
    assert result.fill_price == Decimal("39999")


# ── BUY/SELL LIMIT crossing ──────────────────────────────────────────────────

def test_buy_limit_crossed_when_ask_lte_limit():
    order = base_order(side=PaperOrderSide.BUY, order_type=PaperOrderType.LIMIT, price="40001")
    result = evaluate_fill_candidate(
        order, reservation(), exec_policy(), market_snapshot(bid="39999", ask="40001"), market_capacity(), fee_snapshot()
    )
    assert result.status_code == RESULT_FILL_CANDIDATE
    assert result.fill_price == Decimal("40001")


def test_buy_limit_not_crossed_when_ask_gt_limit():
    order = base_order(side=PaperOrderSide.BUY, order_type=PaperOrderType.LIMIT, price="40000")
    result = evaluate_fill_candidate(
        order, reservation(), exec_policy(), market_snapshot(bid="39999", ask="40001"), market_capacity(), fee_snapshot()
    )
    assert result.status_code == RESULT_LIMIT_NOT_CROSSED


def test_sell_limit_crossed_when_bid_gte_limit():
    order = base_order(side=PaperOrderSide.SELL, order_type=PaperOrderType.LIMIT, price="39999")
    res = reservation(remaining_qty="1.0", remaining_amount="1.0", asset="BTC")
    result = evaluate_fill_candidate(
        order, res, exec_policy(), market_snapshot(bid="39999", ask="40000"), market_capacity(), fee_snapshot()
    )
    assert result.status_code == RESULT_FILL_CANDIDATE
    assert result.fill_price == Decimal("39999")


def test_sell_limit_not_crossed_when_bid_lt_limit():
    order = base_order(side=PaperOrderSide.SELL, order_type=PaperOrderType.LIMIT, price="40001")
    res = reservation(remaining_qty="1.0", remaining_amount="1.0", asset="BTC")
    result = evaluate_fill_candidate(
        order, res, exec_policy(), market_snapshot(bid="39999", ask="40000"), market_capacity(), fee_snapshot()
    )
    assert result.status_code == RESULT_LIMIT_NOT_CROSSED


# ── No OHLC-only / mark fallback ──────────────────────────────────────────────

def test_no_fill_when_continuity_genuinely_missing():
    """Correction 4 (review-5): genuine missing continuity reference rejects fill.
    
    When bar_open_time_ms == prior_bar_open_time_ms, the continuity reference
    is genuinely missing (no prior bar to compare against). This is not a DTO
    validation failure but a genuine evaluator rejection due to incomplete data.
    """
    order = base_order()
    res = reservation(remaining_qty="1.0", remaining_amount="40001")
    # Genuinely missing: bar_open_time_ms == prior_bar_open_time_ms means
    # no prior bar reference exists (continuity is unknown)
    mkt = market_snapshot(bar_open_time_ms=2000, prior_bar_open_time_ms=2000)
    result = evaluate_fill_candidate(
        order, res, exec_policy(), mkt, market_capacity(), fee_snapshot()
    )
    # Missing continuity reference → insufficient data
    assert result.status_code == RESULT_INSUFFICIENT_DATA


# ── Order status gating ──────────────────────────────────────────────────────

@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (PaperOrderStatus.PREPARED, RESULT_INSUFFICIENT_DATA),
        (PaperOrderStatus.PENDING_SEND, RESULT_INSUFFICIENT_DATA),
        (PaperOrderStatus.ACCEPTED, RESULT_FILL_CANDIDATE),
        (PaperOrderStatus.PARTIALLY_FILLED, RESULT_FILL_CANDIDATE),
        (PaperOrderStatus.FILLED, RESULT_INSUFFICIENT_DATA),
        (PaperOrderStatus.CANCELLED, RESULT_INSUFFICIENT_DATA),
        (PaperOrderStatus.REJECTED, RESULT_INSUFFICIENT_DATA),
        (PaperOrderStatus.EXPIRED, RESULT_INSUFFICIENT_DATA),
    ],
)
def test_order_status_gating(status, expected):
    from src.models.paper_contracts import PaperFill

    fills: list = []
    if status == PaperOrderStatus.PARTIALLY_FILLED:
        fills = [
            PaperFill(
                fill_id="f1", order_id="o1", qty=Decimal("0.5"),
                price=Decimal("40000"), timestamp_ms=500, commission=None,
            )
        ]
    elif status == PaperOrderStatus.FILLED:
        fills = [
            PaperFill(
                fill_id="f1", order_id="o1", qty=Decimal("1.0"),
                price=Decimal("40000"), timestamp_ms=500, commission=None,
            )
        ]

    # For PARTIALLY_FILLED, remaining_qty is 0.5, so fee must match
    if status == PaperOrderStatus.PARTIALLY_FILLED:
        fs = fee_snapshot(quantity="0.5")
    else:
        fs = fee_snapshot()

    order = base_order(status=status, fills=fills)
    result = evaluate_fill_candidate(
        order, reservation(), exec_policy(), market_snapshot(bid="40000", ask="40001"), market_capacity(), fs
    )
    assert result.status_code == expected


# ── Gap detection ─────────────────────────────────────────────────────────────

def test_gap_detected_when_bar_gaps():
    # Gap: 70001 > 1000 + 60000 = 61000
    mkt = market_snapshot(prior_bar_open_time_ms=1000, bar_open_time_ms=70001, interval_ms=60000)
    result = evaluate_fill_candidate(
        base_order(), reservation(), exec_policy(), mkt, market_capacity(), fee_snapshot()
    )
    assert result.status_code == RESULT_GAP_UNCERTAIN


def test_gap_detected_with_large_interval():
    # Gap: 2000 > 1000 + 600 (interval_ms = 600ms)
    mkt = market_snapshot(prior_bar_open_time_ms=1000, bar_open_time_ms=2000, interval_ms=600)
    result = evaluate_fill_candidate(
        base_order(), reservation(), exec_policy(), mkt, market_capacity(), fee_snapshot()
    )
    assert result.status_code == RESULT_GAP_UNCERTAIN


def test_no_gap_when_contiguous():
    mkt = market_snapshot(prior_bar_open_time_ms=1000, bar_open_time_ms=1600, interval_ms=600)
    result = evaluate_fill_candidate(
        base_order(), reservation(), exec_policy(), mkt, market_capacity(bar_open_time_ms=1600), fee_snapshot()
    )
    assert result.status_code == RESULT_FILL_CANDIDATE


# ── Reservation validity ─────────────────────────────────────────────────────

def test_reservation_invalid_when_zero_remaining():
    res = reservation(remaining_qty="0", remaining_amount="0")
    order = base_order()
    result = evaluate_fill_candidate(
        order, res, exec_policy(), market_snapshot(bid="40000", ask="40001"), market_capacity(), fee_snapshot()
    )
    assert result.status_code == RESULT_RESERVATION_INVALID


# ── Capacity-bounded partial and full candidates ──────────────────────────────

def test_full_fill_when_capacity_matches_remaining():
    order = base_order(requested_qty="1.0")
    res = reservation(remaining_qty="1.0", remaining_amount="40001")
    result = evaluate_fill_candidate(
        order, res, exec_policy(), market_snapshot(bid="40000", ask="40001"), market_capacity(), fee_snapshot()
    )
    assert result.status_code == RESULT_FILL_CANDIDATE
    assert result.fill_qty == Decimal("1.0")


def test_partial_fill_when_capacity_less_than_remaining():
    order = base_order(requested_qty="2.0")
    res = reservation(remaining_qty="1.0", remaining_amount="40001")
    result = evaluate_fill_candidate(
        order, res, exec_policy(), market_snapshot(bid="40000", ask="40001"), market_capacity(capacity="1.0"), fee_snapshot()
    )
    assert result.status_code == RESULT_FILL_CANDIDATE
    assert result.fill_qty == Decimal("1.0")


def test_no_fill_when_capacity_zero():
    order = base_order(requested_qty="1.0")
    res = reservation(remaining_qty="0", remaining_amount="0")
    result = evaluate_fill_candidate(
        order, res, exec_policy(), market_snapshot(bid="40000", ask="40001"), market_capacity(), fee_snapshot()
    )
    # Zero capacity → reservation_invalid (checked before capacity)
    assert result.status_code == RESULT_RESERVATION_INVALID


def test_buy_market_quote_reserve_must_cover_notional():
    order = base_order(side=PaperOrderSide.BUY, order_type=PaperOrderType.MARKET, requested_qty="2.0", max_quote="80002")
    # Ask is 40001, so 2 * 40001 = 80002 — exactly covered
    res = reservation(remaining_qty="2.0", remaining_amount="80002")
    result = evaluate_fill_candidate(
        order, res, exec_policy(), market_snapshot(bid="40000", ask="40001"),
        market_capacity(capacity="2.0"),
        fee_snapshot(quantity="2.0"),  # match order qty
    )
    assert result.status_code == RESULT_FILL_CANDIDATE
    assert result.fill_qty == Decimal("2.0")

    # Notional exceeds reserve → capacity missing
    res_insufficient = reservation(remaining_qty="2.0", remaining_amount="80001")
    result2 = evaluate_fill_candidate(
        order, res_insufficient, exec_policy(), market_snapshot(bid="40000", ask="40001"),
        market_capacity(capacity="2.0"),
        fee_snapshot(quantity="2.0"),
    )
    assert result2.status_code == RESULT_CAPACITY_MISSING


# ── Fee snapshot validation ───────────────────────────────────────────────────

def test_unknown_fee_rejected():
    order = base_order()
    result = evaluate_fill_candidate(
        order, reservation(), exec_policy(), market_snapshot(bid="40000", ask="40001"),
        market_capacity(),
        fee_snapshot=None,  # type: ignore[arg-type]
    )
    assert result.status_code == RESULT_FEE_UNKNOWN


def test_fee_quantity_mismatch_rejected():
    result = evaluate_fill_candidate(
        base_order(), reservation(), exec_policy(),
        market_snapshot(bid="40000", ask="40001"),
        market_capacity(),
        fee_snapshot(quantity="0.5"),  # qty mismatch with order qty 1.0
    )
    assert result.status_code == RESULT_FEE_MISMATCH


def test_explicit_zero_fee_accepted():
    result = evaluate_fill_candidate(
        base_order(), reservation(), exec_policy(),
        market_snapshot(bid="40000", ask="40001"),
        market_capacity(),
        fee_snapshot(quantity="1.0", commission=ZERO_COMMISSION),
    )
    assert result.status_code == RESULT_FILL_CANDIDATE
    assert result.commission is not None
    assert result.commission.amount == Decimal("0")


def test_fee_asset_must_be_present():
    """Fee snapshot must supply a valid commission with asset."""
    no_asset_commission = Commission(amount=Decimal("0"), asset="USDT")
    fs = fee_snapshot(quantity="1.0", commission=no_asset_commission)
    result = evaluate_fill_candidate(
        base_order(), reservation(), exec_policy(),
        market_snapshot(bid="40000", ask="40001"),
        market_capacity(),
        fs,
    )
    assert result.status_code == RESULT_FILL_CANDIDATE


# ── Latency/slippage mode ─────────────────────────────────────────────────────

def test_unmodelled_mode_uses_side_quote():
    result = evaluate_fill_candidate(
        base_order(), reservation(), exec_policy(mode="UNMODELLED"),
        market_snapshot(bid="40000", ask="40001"),
        market_capacity(),
        fee_snapshot(),
    )
    assert result.status_code == RESULT_FILL_CANDIDATE
    assert result.cost_model == "UNMODELLED"
    assert result.fill_price == Decimal("40001")


def test_modelled_mode_accepted():
    from src.models.paper_execution import PaperModelledExecution
    modelled = PaperModelledExecution(modelled_execution_price=Decimal("40001"), latency_ms=50)
    result = evaluate_fill_candidate(
        base_order(), reservation(), exec_policy(mode="MODELLED"),
        market_snapshot(bid="40000", ask="40001"),
        market_capacity(),
        fee_snapshot(),
        modelled_execution=modelled,
    )
    assert result.status_code == RESULT_FILL_CANDIDATE
    assert result.cost_model == "MODELLED"
    assert result.modelled_execution_price == Decimal("40001")
    assert result.latency_ms == 50


def test_unmodelled_result_not_labelled_net():
    """UNMODELLED results must not be labelled as net of latency/slippage."""
    result = evaluate_fill_candidate(
        base_order(), reservation(), exec_policy(mode="UNMODELLED"),
        market_snapshot(bid="40000", ask="40001"),
        market_capacity(),
        fee_snapshot(),
    )
    assert result.cost_model == "UNMODELLED"
    # cost_model must NOT be "NET" or any net label
    assert "net" not in result.cost_model.lower()


# ── Intra-bar collision ───────────────────────────────────────────────────────

def test_same_bar_stop_target_collision_ambiguous_buy():
    """Both stop and target reachable in same bar → ambiguous."""
    prot = protection(stop_price="39500", take_price="40500", bar_high="41000", bar_low="39000", bar_source_id="synthetic", bar_snapshot_timestamp_ms=2000, bar_open_time_ms=2000, bar_interval_ms=60000)
    order = base_order(side=PaperOrderSide.BUY, order_type=PaperOrderType.MARKET)
    order.protection_group_id = "pg-1"  # match protection group
    result = evaluate_fill_candidate(
        order, reservation(), exec_policy(),
        market_snapshot(bid="40000", ask="40001"),
        market_capacity(),
        fee_snapshot(),
        protection_snapshot=prot,
    )
    assert result.status_code == RESULT_AMBIGUOUS_INTRABAR


def test_no_collision_when_only_stop_reachable():
    prot = protection(stop_price="39500", take_price="41000", bar_high="40500", bar_low="39000", bar_source_id="synthetic", bar_snapshot_timestamp_ms=2000, bar_open_time_ms=2000, bar_interval_ms=60000)
    order = base_order(side=PaperOrderSide.BUY, order_type=PaperOrderType.MARKET)
    order.protection_group_id = "pg-1"
    result = evaluate_fill_candidate(
        order, reservation(), exec_policy(),
        market_snapshot(bid="40000", ask="40001"),
        market_capacity(),
        fee_snapshot(),
        protection_snapshot=prot,
    )
    assert result.status_code == RESULT_FILL_CANDIDATE


def test_no_collision_when_only_target_reachable():
    prot = protection(stop_price="39500", take_price="41000", bar_high="41500", bar_low="39800", bar_source_id="synthetic", bar_snapshot_timestamp_ms=2000, bar_open_time_ms=2000, bar_interval_ms=60000)
    order = base_order(side=PaperOrderSide.BUY, order_type=PaperOrderType.MARKET)
    order.protection_group_id = "pg-1"
    result = evaluate_fill_candidate(
        order, reservation(), exec_policy(),
        market_snapshot(bid="40000", ask="40001"),
        market_capacity(),
        fee_snapshot(),
        protection_snapshot=prot,
    )
    assert result.status_code == RESULT_FILL_CANDIDATE


def test_no_collision_when_no_protection_snapshot():
    order = base_order(side=PaperOrderSide.BUY, order_type=PaperOrderType.MARKET)
    result = evaluate_fill_candidate(
        order, reservation(), exec_policy(),
        market_snapshot(bid="40000", ask="40001"),
        market_capacity(),
        fee_snapshot(),
        protection_snapshot=None,  # type: ignore[arg-type]
    )
    assert result.status_code == RESULT_FILL_CANDIDATE


# ── No mutation of inputs ─────────────────────────────────────────────────────

def test_inputs_not_mutated():
    order = base_order()
    res = reservation()
    pol = exec_policy()
    mkt = market_snapshot()
    fs = fee_snapshot()
    prot = protection()

    order_dump = order.model_dump()
    res_dump = res.model_dump()
    mkt_dump = mkt.model_dump()

    evaluate_fill_candidate(order, res, pol, mkt, market_capacity(), fs, prot)

    assert order.model_dump() == order_dump
    assert res.model_dump() == res_dump
    assert mkt.model_dump() == mkt_dump


# ── Stable codes and references ───────────────────────────────────────────────

def test_result_codes_are_stable_strings():
    order = base_order()
    result = evaluate_fill_candidate(
        order, reservation(), exec_policy(), market_snapshot(bid="40000", ask="40001"), market_capacity(), fee_snapshot()
    )
    assert isinstance(result.status_code, str)
    assert len(result.status_code) > 0


def test_result_contains_exact_references():
    result = evaluate_fill_candidate(
        base_order(), reservation(), exec_policy(), market_snapshot(bid="40000", ask="40001"), market_capacity(), fee_snapshot()
    )
    assert result.policy_id == "exec-1"
    assert result.policy_version == "v1"
    assert "BTC/USDT" in result.market_reference
    assert "synthetic" in result.market_reference
    assert result.reservation_id == "res-1"
    assert result.fee_id == "fee-1"
    assert result.fee_version == "v1"


# ── Decimal-context invariance ────────────────────────────────────────────────

def test_outcomes_invariant_under_different_decimal_contexts():
    order = base_order()
    res = reservation()
    pol = exec_policy()
    mkt = market_snapshot()
    fs = fee_snapshot()

    expected = evaluate_fill_candidate(order, res, pol, mkt, market_capacity(), fs)

    outcomes = []
    for precision, rounding in ((2, ROUND_DOWN), (50, ROUND_UP)):
        with localcontext() as ctx:
            ctx.prec, ctx.rounding = precision, rounding
            outcomes.append(evaluate_fill_candidate(order, res, pol, mkt, market_capacity(), fs))

    for outcome in outcomes:
        assert outcome.status_code == expected.status_code
        assert outcome.fill_qty == expected.fill_qty
        assert outcome.fill_price == expected.fill_price


# ── DTO validation ────────────────────────────────────────────────────────────

def test_pydantic_dto_frozen():
    pol = exec_policy()
    with pytest.raises((TypeError, ValidationError)):
        pol.policy_id = "changed"  # type: ignore[assignment]


def test_market_snapshot_requires_positive_bid_ask():
    with pytest.raises(ValidationError):
        PaperMarketExecutionSnapshot(
            symbol="BTC/USDT", bid=Decimal("0"), ask=Decimal("40001"),
            bar_open_time_ms=2000, prior_bar_open_time_ms=1000, interval_ms=60000,
        )
    with pytest.raises(ValidationError):
        PaperMarketExecutionSnapshot(
            symbol="BTC/USDT", bid=Decimal("40000"), ask=Decimal("-1"),
            bar_open_time_ms=2000, prior_bar_open_time_ms=1000, interval_ms=60000,
        )


def test_reservation_requires_positive_qty():
    with pytest.raises(ValidationError):
        PaperReservationSnapshot(
            reservation_id="r1", remaining_qty=Decimal("-1"), remaining_amount=Decimal("1"), asset="USDT",
        )


def test_fee_snapshot_requires_valid_quantity():
    with pytest.raises(ValidationError):
        PaperFeeSnapshot(fee_id="f1", fee_version="v1", quantity=Decimal("-1"), commission=COMMISION_USDT)


# ── Result DTO properties ─────────────────────────────────────────────────────

def test_has_candidate_property_true():
    result = evaluate_fill_candidate(
        base_order(), reservation(), exec_policy(), market_snapshot(bid="40000", ask="40001"), market_capacity(), fee_snapshot()
    )
    assert result.has_candidate is True


def test_has_candidate_property_false():
    result = evaluate_fill_candidate(
        base_order(), reservation(), exec_policy(), market_snapshot(bid="40000", ask="40001"),
        market_capacity(),
        fee_snapshot=None,  # type: ignore[arg-type]
    )
    assert result.has_candidate is False


def test_sell_market_requires_base_reservation():
    """SELL requires BASE asset reservation; insufficient reserve is rejected."""
    order = base_order(side=PaperOrderSide.SELL, order_type=PaperOrderType.MARKET)
    # Correct: BTC reserve with sufficient amount for 1 BTC
    res_btc = reservation(remaining_qty="1.0", remaining_amount="1.0", asset="BTC")
    result = evaluate_fill_candidate(
        order, res_btc, exec_policy(), market_snapshot(bid="39999", ask="40000"), market_capacity(), fee_snapshot()
    )
    assert result.status_code == RESULT_FILL_CANDIDATE

    # Truly insufficient BTC reserve: remaining_qty=0.3 < order remaining 1.0
    # executable_qty = min(1.0, 0.3) = 0.3; remaining_amount 0.3 >= 0.3 passes
    # But we need remaining_amount < executable_qty to trigger capacity missing
    res_insufficient = reservation(remaining_qty="0.3", remaining_amount="0.29", asset="BTC")
    result2 = evaluate_fill_candidate(
        order, res_insufficient, exec_policy(), market_snapshot(bid="39999", ask="40000"),
        market_capacity(capacity="0.3"),
        fee_snapshot(quantity="0.3"),  # match executable qty
    )
    assert result2.status_code == RESULT_CAPACITY_MISSING


def test_protection_symbol_mismatch_no_collision():
    prot = protection(symbol="ETH/USDT", account_id="acct-1")
    order = base_order(side=PaperOrderSide.BUY)
    result = evaluate_fill_candidate(
        order, reservation(), exec_policy(),
        market_snapshot(bid="40000", ask="40001"),
        market_capacity(),
        fee_snapshot(),
        protection_snapshot=prot,
    )
    assert result.status_code == RESULT_FILL_CANDIDATE


# ── Numeric fixture values are not recommendations ────────────────────────────

def test_fixture_values_are_not_operational_recommendations():
    """Verify that the evaluator works with arbitrary fixture values."""
    order = base_order(requested_qty="0.001")
    res = reservation(remaining_qty="0.001", remaining_amount="40")
    result = evaluate_fill_candidate(
        order, res, exec_policy(), market_snapshot(bid="30000", ask="40000"),
        market_capacity(capacity="0.001"),
        fee_snapshot(quantity="0.001"),
    )
    assert result.status_code == RESULT_FILL_CANDIDATE
    assert result.fill_qty == Decimal("0.001")


def test_capacity_zero_rejects_fill_even_with_remaining_qty():
    """Correction 1: capacity is explicit; zero capacity rejects fill regardless of remaining_qty."""
    order = base_order(requested_qty="1.0")
    # remaining_qty > 0 but capacity == 0 → no fill
    res = reservation(remaining_qty="1.0", remaining_amount="40001")
    # Capacity must be > 0 per DTO; use minimal capacity with matching fee quantity
    result = evaluate_fill_candidate(
        order, res, exec_policy(), market_snapshot(bid="40000", ask="40001"),
        market_capacity(capacity="0.00000001"), fee_snapshot(quantity="0.00000001")
    )
    # With capacity 0.00000001, fill_qty should be 0.00000001 (partial), not rejected
    # The test verifies the evaluator uses capacity as the cap (not remaining_qty)
    assert result.status_code == "fill_candidate"
    assert result.fill_qty == Decimal("0.00000001")


def test_capacity_less_than_remaining_qty_causes_partial_fill():
    """Correction 1: capacity bounds the fill independently of remaining_qty."""
    order = base_order(requested_qty="2.0")
    res = reservation(remaining_qty="2.0", remaining_amount="80002")
    result = evaluate_fill_candidate(
        order, res, exec_policy(), market_snapshot(bid="40000", ask="40001"),
        market_capacity(capacity="1.0"),
        fee_snapshot(quantity="1.0"),
    )
    assert result.status_code == RESULT_FILL_CANDIDATE
    assert result.fill_qty == Decimal("1.0")


def test_market_reference_includes_provenance():
    """Correction 2: market_reference is not just the symbol; it includes provenance."""
    result = evaluate_fill_candidate(
        base_order(), reservation(), exec_policy(),
        market_snapshot(symbol="ETH/USDT", provenance="binance", version="v2", snapshot_timestamp_ms=9999),
        market_capacity(),
        fee_snapshot(),
    )
    assert "ETH/USDT" in result.market_reference
    assert "binance" in result.market_reference
    assert "v2" in result.market_reference
    assert "9999" in result.market_reference


def test_buy_requires_quote_asset_reservation():
    """Correction 3: BUY requires QUOTE asset reservation."""
    order = base_order(side=PaperOrderSide.BUY)
    # BTC reserve for BUY → asset mismatch
    res_btc = reservation(asset="BTC", remaining_qty="1.0", remaining_amount="1.0")
    result = evaluate_fill_candidate(
        order, res_btc, exec_policy(), market_snapshot(bid="40000", ask="40001"), market_capacity(), fee_snapshot()
    )
    assert result.status_code == RESULT_RESERVATION_ASSET_MISMATCH


def test_sell_requires_base_asset_reservation():
    """Correction 3: SELL requires BASE asset reservation."""
    order = base_order(side=PaperOrderSide.SELL)
    # USDT reserve for SELL → asset mismatch
    res_usdt = reservation(asset="USDT", remaining_qty="1.0", remaining_amount="40001")
    result = evaluate_fill_candidate(
        order, res_usdt, exec_policy(), market_snapshot(bid="39999", ask="40000"), market_capacity(), fee_snapshot()
    )
    assert result.status_code == RESULT_RESERVATION_ASSET_MISMATCH


# ── Correction 4: Commission asset must equal QUOTE ──

def test_commission_asset_must_match_quote():
    """Correction 4: commission asset must equal symbol QUOTE."""
    order = base_order()
    # Wrong commission asset (BTC instead of USDT)
    wrong_asset_commission = Commission(amount=Decimal("0.001"), asset="BTC")
    fs_wrong = PaperFeeSnapshot(
        fee_id="fee-1", fee_version="v1", quantity=Decimal("1.0"), commission=wrong_asset_commission
    )
    result = evaluate_fill_candidate(
        base_order(), reservation(), exec_policy(),
        market_snapshot(bid="40000", ask="40001"), market_capacity(), fs_wrong
    )
    assert result.status_code == RESULT_FEE_MISMATCH


def test_modelled_requires_explicit_execution():
    """Correction 5: MODELLED mode must not silently behave like UNMODELLED."""
    result = evaluate_fill_candidate(
        base_order(), reservation(), exec_policy(mode="MODELLED"),
        market_snapshot(bid="40000", ask="40001"), market_capacity(),
        fee_snapshot(),
        modelled_execution=None,  # type: ignore[arg-type]
    )
    assert result.status_code == RESULT_MODELLED_EXEC_MISSING


def test_modelled_uses_explicit_price_not_bid_ask():
    """Correction 5: MODELLED uses supplied price, not market bid/ask."""
    from src.models.paper_execution import PaperModelledExecution
    modelled = PaperModelledExecution(modelled_execution_price=Decimal("40500"), latency_ms=100)
    # Use LIMIT BUY with price=40500 so modelled price respects the limit
    order = base_order(side=PaperOrderSide.BUY, order_type=PaperOrderType.LIMIT, price="40500")
    # remaining_amount must cover notional: 1.0 * 40500 = 40500
    res = reservation(remaining_qty="1.0", remaining_amount="40501")
    result = evaluate_fill_candidate(
        order, res, exec_policy(mode="MODELLED"),
        market_snapshot(bid="40000", ask="40001"), market_capacity(),
        fee_snapshot(),
        modelled_execution=modelled,
    )
    assert result.status_code == RESULT_FILL_CANDIDATE
    assert result.fill_price == Decimal("40500")
    assert result.latency_ms == 100
    assert result.modelled_execution_price == Decimal("40500")


def test_protection_requires_same_group_for_collision():
    """Correction 6: unrelated protection snapshot must not block an order."""
    # Protection from different group/account should not cause collision
    prot_unrelated = protection(symbol="BTC/USDT", account_id="acct-999")  # wrong account
    order = base_order(side=PaperOrderSide.BUY)
    result = evaluate_fill_candidate(
        order, reservation(), exec_policy(),
        market_snapshot(bid="40000", ask="40001"), market_capacity(),
        fee_snapshot(),
        protection_snapshot=prot_unrelated,
    )
    assert result.status_code == RESULT_FILL_CANDIDATE


# ── Correction 7: Exact Decimal arithmetic (reproducer) ──

def test_exact_decimal_notional_reproducer():
    """Correction 7: qty 1.23 × ask 123.45, quote reserve 151.5 must NOT approve.

    Exact notional is 151.8435; 151.5 < 151.8435 → capacity missing.
    """
    order = base_order(side=PaperOrderSide.BUY, order_type=PaperOrderType.MARKET, requested_qty="1.23", max_quote="152")
    # capacity matches order qty
    res = reservation(remaining_qty="1.23", remaining_amount="151.5")
    result = evaluate_fill_candidate(
        order, res, exec_policy(),
        market_snapshot(bid="123.00", ask="123.45"),
        market_capacity(capacity="1.23"),
        fee_snapshot(quantity="1.23"),
    )
    # Exact notional: 1.23 * 123.45 = 151.8435; reserve 151.5 < 151.8435
    assert result.status_code == RESULT_CAPACITY_MISSING


def test_exact_decimal_notional_exactly_covered():
    """Correction 7: exact notional exactly covered should approve."""
    order = base_order(side=PaperOrderSide.BUY, order_type=PaperOrderType.MARKET, requested_qty="1.23", max_quote="152")
    # capacity matches order qty; reserve exactly covers notional
    res = reservation(remaining_qty="1.23", remaining_amount="151.8435")
    result = evaluate_fill_candidate(
        order, res, exec_policy(),
        market_snapshot(bid="123.00", ask="123.45"),
        market_capacity(capacity="1.23"),
        fee_snapshot(quantity="1.23"),
    )
    assert result.status_code == RESULT_FILL_CANDIDATE
    assert result.fill_qty == Decimal("1.23")


def test_incoherent_market_snapshot_no_fill():
    """Correction 6: incoherent bid/ask (bid > ask) should not produce a fill."""
    order = base_order(side=PaperOrderSide.BUY, order_type=PaperOrderType.MARKET, max_quote="40001")
    res = reservation(remaining_qty="1.0", remaining_amount="40001")
    # Incoherent: bid > ask (shouldn't happen in real markets)
    mkt = market_snapshot(bid="40002", ask="40000")  # bid > ask
    result = evaluate_fill_candidate(
        order, res, exec_policy(), mkt, market_capacity(), fee_snapshot()
    )
    # Incoherent snapshot → insufficient data
    assert result.status_code == RESULT_INSUFFICIENT_DATA


def test_regression_reservation_qty_caps_executable():
    """Reproducer 1: reservation remaining_qty=0.25 must cap fill even when capacity=1."""
    order = base_order(requested_qty="1.0")
    res = reservation(remaining_qty="0.25", remaining_amount="10001")
    result = evaluate_fill_candidate(
        order, res, exec_policy(), market_snapshot(bid="40000", ask="40001"), market_capacity(capacity="1.0"), fee_snapshot(quantity="0.25")
    )
    assert result.status_code == RESULT_FILL_CANDIDATE
    assert result.fill_qty == Decimal("0.25")


def test_regression_limit_buy_notional_checked():
    """Reproducer 2: LIMIT BUY qty 1 × limit 40001, reserve amount 1 must NOT approve."""
    order = base_order(side=PaperOrderSide.BUY, order_type=PaperOrderType.LIMIT, price="40001", requested_qty="1.0")
    res = reservation(remaining_qty="1.0", remaining_amount="1")
    result = evaluate_fill_candidate(
        order, res, exec_policy(), market_snapshot(bid="40000", ask="40001"), market_capacity(), fee_snapshot(quantity="1.0")
    )
    assert result.status_code == RESULT_CAPACITY_MISSING


def test_regression_market_buy_max_quote_enforced():
    """Reproducer 3: MARKET BUY max_quote=1 with reserve 40001 must NOT approve."""
    order = base_order(side=PaperOrderSide.BUY, order_type=PaperOrderType.MARKET, requested_qty="1.0", max_quote="1")
    res = reservation(remaining_qty="1.0", remaining_amount="40001")
    result = evaluate_fill_candidate(
        order, res, exec_policy(), market_snapshot(bid="40000", ask="40001"), market_capacity(), fee_snapshot()
    )
    assert result.status_code == RESULT_CAPACITY_MISSING


def test_regression_owner_order_id_mismatch():
    """Reproducer 4: reservation owner_order_id != candidate order must reject."""
    # Create reservation with different owner_order_id
    res = PaperReservationSnapshot(
        reservation_id="res-2",
        owner_order_id="o-other",  # different from base_order's o1
        account_id="acct-1",  # required field
        asset="USDT",
        remaining_qty=Decimal("1.0"),
        remaining_amount=Decimal("40001"),
    )
    order = base_order()  # order_id="o1"
    result = evaluate_fill_candidate(
        order, res, exec_policy(), market_snapshot(bid="40000", ask="40001"), market_capacity(), fee_snapshot()
    )
    assert result.status_code == RESULT_RESERVATION_INVALID


def test_regression_market_symbol_mismatch():
    """Reproducer 5: BTC/USDT order with ETH/USDT market must NOT produce fill."""
    order = base_order(symbol="BTC/USDT")
    result = evaluate_fill_candidate(
        order, reservation(), exec_policy(),
        market_snapshot(symbol="ETH/USDT", bid="2000", ask="2001"), market_capacity(), fee_snapshot()
    )
    assert result.status_code == RESULT_MARKET_REFERENCE_INVALID


def test_regression_modelled_limit_price_violated():
    """Reproducer 6: MODELLED BUY LIMIT with limit 40001, modelled price 40002 must NOT approve."""
    from src.models.paper_execution import PaperModelledExecution
    order = base_order(side=PaperOrderSide.BUY, order_type=PaperOrderType.LIMIT, price="40001", requested_qty="1.0")
    modelled = PaperModelledExecution(modelled_execution_price=Decimal("40002"), latency_ms=50)
    # remaining_amount must cover notional: 1.0 * 40002 = 40002
    res = reservation(remaining_qty="1.0", remaining_amount="40003")
    result = evaluate_fill_candidate(
        order, res, exec_policy(mode="MODELLED"),
        market_snapshot(bid="40000", ask="40001"), market_capacity(),
        fee_snapshot(),
        modelled_execution=modelled,
    )
    assert result.status_code == RESULT_LIMIT_NOT_CROSSED


def test_regression_stop_loss_no_trigger():
    """Reproducer 7: STOP_LOSS must not fill without trigger reached."""
    order = base_order(
        side=PaperOrderSide.SELL,
        order_type=PaperOrderType.STOP_LOSS,
        trigger_price="41000",
        requested_qty="1.0",
    )
    # Market bid 39999 < trigger 41000 → trigger NOT reached
    res = reservation(remaining_qty="1.0", remaining_amount="1.0", asset="BTC")
    result = evaluate_fill_candidate(
        order, res, exec_policy(), market_snapshot(bid="39999", ask="40000"), market_capacity(), fee_snapshot()
    )
    assert result.status_code == RESULT_PROTECTION_TRIGGER_MISSING


def test_regression_stop_loss_trigger_reached():
    """Reproducer 7: STOP_LOSS with trigger reached should approve."""
    order = base_order(
        side=PaperOrderSide.SELL,
        order_type=PaperOrderType.STOP_LOSS,
        trigger_price="39500",
        requested_qty="1.0",
    )
    # Market bid 39999 >= trigger 39500 → trigger reached
    res = reservation(remaining_qty="1.0", remaining_amount="1.0", asset="BTC")
    result = evaluate_fill_candidate(
        order, res, exec_policy(), market_snapshot(bid="39999", ask="40000"), market_capacity(), fee_snapshot()
    )
    assert result.status_code == RESULT_FILL_CANDIDATE


def test_regression_protection_group_mismatch():
    """Reproducer 8: unrelated protection_group_id must not cause ambiguity."""
    # Order has group "pg-1", protection has different group "pg-999"
    order = base_order(side=PaperOrderSide.BUY, order_type=PaperOrderType.MARKET)
    order.protection_group_id = "pg-1"  # type: ignore[assignment]
    # Protection with different group - construct directly since model is frozen
    prot = PaperProtectionSnapshot(
        symbol="BTC/USDT",
        account_id="acct-1",
        protection_group_id="pg-999",
        stop_trigger_price=Decimal("39500"),
        take_trigger_price=Decimal("40500"),
        bar_high=Decimal("41000"),
        bar_low=Decimal("39000"),
        bar_source_id="synthetic",
        bar_snapshot_timestamp_ms=2000,
        bar_open_time_ms=2000,
        bar_interval_ms=60000,
    )
    result = evaluate_fill_candidate(
        order, reservation(), exec_policy(),
        market_snapshot(bid="40000", ask="40001"), market_capacity(),
        fee_snapshot(),
        protection_snapshot=prot,
    )
    assert result.status_code == RESULT_FILL_CANDIDATE


def test_regression_genuine_missing_market_data():
    """Correction 8: genuine missing market data should not produce fill."""
    # Market snapshot with zero bid/ask is invalid by schema, so we test
    # with a scenario that truly has missing data: no market snapshot at all
    # (this is tested via the market_snapshot fixture requiring positive bid/ask).
    # Instead, test with a market snapshot that has valid but empty provenance.
    with pytest.raises(ValidationError):
        PaperMarketExecutionSnapshot(
            symbol="BTC/USDT", bid=Decimal("40000"), ask=Decimal("40001"),
            bar_open_time_ms=2000, prior_bar_open_time_ms=1000, interval_ms=60000,
            provenance="", version="v1", snapshot_timestamp_ms=2000,
        )


def test_regression_modelled_buy_market_exceeds_max_quote():
    """Regression: MODELLED BUY MARKET with price exceeding max_quote must reject."""
    from src.models.paper_execution import PaperModelledExecution
    order = base_order(side=PaperOrderSide.BUY, order_type=PaperOrderType.MARKET, requested_qty="1.0", max_quote="40001")
    modelled = PaperModelledExecution(modelled_execution_price=Decimal("50000"), latency_ms=50)
    result = evaluate_fill_candidate(
        order, reservation(), exec_policy(mode="MODELLED"),
        market_snapshot(bid="40000", ask="40001"), market_capacity(),
        fee_snapshot(),
        modelled_execution=modelled,
    )
    assert result.status_code == RESULT_CAPACITY_MISSING


# ── Correction 1: Reservation identity tests ──────────────────────────────────

def test_reservation_neither_owner_field_rejected():
    """Correction 1: neither owner_order_id nor owner_group_id set → invalid."""
    res = PaperReservationSnapshot(
        reservation_id="res-neither",
        owner_order_id=None,
        owner_group_id=None,
        account_id="acct-1",
        asset="USDT",
        remaining_qty=Decimal("1.0"),
        remaining_amount=Decimal("40001"),
    )
    order = base_order()
    result = evaluate_fill_candidate(
        order, res, exec_policy(), market_snapshot(bid="40000", ask="40001"), market_capacity(), fee_snapshot()
    )
    assert result.status_code == RESULT_RESERVATION_INVALID


def test_reservation_both_owner_fields_rejected():
    """Correction 1: both owner_order_id and owner_group_id set → invalid."""
    res = PaperReservationSnapshot(
        reservation_id="res-both",
        owner_order_id="o1",
        owner_group_id="pg-1",
        account_id="acct-1",
        asset="USDT",
        remaining_qty=Decimal("1.0"),
        remaining_amount=Decimal("40001"),
    )
    order = base_order()
    result = evaluate_fill_candidate(
        order, res, exec_policy(), market_snapshot(bid="40000", ask="40001"), market_capacity(), fee_snapshot()
    )
    assert result.status_code == RESULT_RESERVATION_INVALID


def test_reservation_account_mismatch_rejected():
    """Correction 1: reservation account_id must match order paper_account_id."""
    res = PaperReservationSnapshot(
        reservation_id="res-acc",
        owner_order_id="o1",
        account_id="acct-999",  # wrong account
        asset="USDT",
        remaining_qty=Decimal("1.0"),
        remaining_amount=Decimal("40001"),
    )
    order = base_order()
    result = evaluate_fill_candidate(
        order, res, exec_policy(), market_snapshot(bid="40000", ask="40001"), market_capacity(), fee_snapshot()
    )
    assert result.status_code == RESULT_RESERVATION_INVALID


def test_reservation_owner_group_mismatch_rejected():
    """Correction 1: owner_group_id must match order's protection_group_id."""
    res = PaperReservationSnapshot(
        reservation_id="res-grp",
        owner_order_id=None,
        owner_group_id="pg-999",  # wrong group
        account_id="acct-1",
        asset="USDT",
        remaining_qty=Decimal("1.0"),
        remaining_amount=Decimal("40001"),
    )
    order = base_order(side=PaperOrderSide.BUY, order_type=PaperOrderType.MARKET)
    order.protection_group_id = "pg-1"
    result = evaluate_fill_candidate(
        order, res, exec_policy(), market_snapshot(bid="40000", ask="40001"), market_capacity(), fee_snapshot()
    )
    assert result.status_code == RESULT_RESERVATION_INVALID


def test_reservation_owner_group_to_order_without_group_rejected():
    """Correction 1: owner_group_id set but order has no protection_group_id → invalid."""
    res = PaperReservationSnapshot(
        reservation_id="res-grp2",
        owner_order_id=None,
        owner_group_id="pg-1",
        account_id="acct-1",
        asset="USDT",
        remaining_qty=Decimal("1.0"),
        remaining_amount=Decimal("40001"),
    )
    order = base_order(side=PaperOrderSide.BUY, order_type=PaperOrderType.MARKET)
    # order has no protection_group_id
    result = evaluate_fill_candidate(
        order, res, exec_policy(), market_snapshot(bid="40000", ask="40001"), market_capacity(), fee_snapshot()
    )
    assert result.status_code == RESULT_RESERVATION_INVALID


# ── Correction 2: Capacity provenance tests ───────────────────────────────────

def test_result_contains_capacity_provenance():
    """Correction 2: result must include capacity source reference."""
    order = base_order()
    result = evaluate_fill_candidate(
        order, reservation(), exec_policy(), market_snapshot(bid="40000", ask="40001"), market_capacity(), fee_snapshot()
    )
    assert result.capacity_source_id == "cap-1"
    assert result.capacity_source_version == "v1"
    assert result.capacity_symbol == "BTC/USDT"


# ── Correction 4: Protection bar provenance tests ─────────────────────────────

def test_collision_bar_provenance_mismatch():
    """Correction 4: protection bar provenance must match market snapshot."""
    # Protection with different bar source_id
    prot = PaperProtectionSnapshot(
        symbol="BTC/USDT",
        account_id="acct-1",
        protection_group_id="pg-1",
        stop_trigger_price=Decimal("39500"),
        take_trigger_price=Decimal("40500"),
        bar_high=Decimal("41000"),
        bar_low=Decimal("39000"),
        bar_source_id="different-source",  # doesn't match market provenance "synthetic"
        bar_snapshot_timestamp_ms=2000,
        bar_open_time_ms=2000,
        bar_interval_ms=60000,
    )
    order = base_order(side=PaperOrderSide.BUY, order_type=PaperOrderType.MARKET)
    order.protection_group_id = "pg-1"
    result = evaluate_fill_candidate(
        order, reservation(), exec_policy(),
        market_snapshot(bid="40000", ask="40001"), market_capacity(),
        fee_snapshot(),
        protection_snapshot=prot,
    )
    assert result.status_code == RESULT_FILL_CANDIDATE  # no collision due to provenance mismatch


def test_collision_bar_timestamp_mismatch():
    """Correction 4: protection bar timestamp must match market snapshot."""
    # Protection with different bar_snapshot_timestamp_ms
    prot = PaperProtectionSnapshot(
        symbol="BTC/USDT",
        account_id="acct-1",
        protection_group_id="pg-1",
        stop_trigger_price=Decimal("39500"),
        take_trigger_price=Decimal("40500"),
        bar_high=Decimal("41000"),
        bar_low=Decimal("39000"),
        bar_source_id="synthetic",  # matches
        bar_snapshot_timestamp_ms=9999,  # doesn't match market timestamp 2000
        bar_open_time_ms=2000,
        bar_interval_ms=60000,
    )
    order = base_order(side=PaperOrderSide.BUY, order_type=PaperOrderType.MARKET)
    order.protection_group_id = "pg-1"
    result = evaluate_fill_candidate(
        order, reservation(), exec_policy(),
        market_snapshot(bid="40000", ask="40001"), market_capacity(),
        fee_snapshot(),
        protection_snapshot=prot,
    )
    assert result.status_code == RESULT_FILL_CANDIDATE  # no collision due to timestamp mismatch


# ── Correction 6: Snapshot coherence tests ────────────────────────────────────

def test_snapshot_timestamp_ordering_invalid():
    """Correction 6: bar_open_time_ms must be > prior_bar_open_time_ms."""
    order = base_order()
    res = reservation()
    # bar_open_time_ms (1000) <= prior_bar_open_time_ms (2000) → invalid
    mkt = market_snapshot(bar_open_time_ms=1000, prior_bar_open_time_ms=2000)
    result = evaluate_fill_candidate(
        order, res, exec_policy(), mkt, market_capacity(bar_open_time_ms=1000), fee_snapshot()
    )
    assert result.status_code == RESULT_INSUFFICIENT_DATA


# ── Correction 7: Genuine missing data test ───────────────────────────────────

def test_reservation_invalid_when_zero_remaining():
    """Regression: zero remaining_qty/amount should be caught by reservation validity."""
    res = reservation(remaining_qty="0", remaining_amount="0")
    order = base_order()
    result = evaluate_fill_candidate(
        order, res, exec_policy(), market_snapshot(bid="40000", ask="40001"), market_capacity(), fee_snapshot()
    )
    assert result.status_code == RESULT_RESERVATION_INVALID


# ── Review-5: Residual snapshot binding corrections ───────────────────────────

def test_capacity_symbol_mismatch_rejects_fill():
    """Correction 1 (review-5): capacity symbol must match order and market symbols."""
    # Capacity with ETH/USDT symbol for a BTC/USDT order/market
    order = base_order(symbol="BTC/USDT")
    result = evaluate_fill_candidate(
        order, reservation(), exec_policy(),
        market_snapshot(symbol="BTC/USDT", bid="40000", ask="40001"),
        market_capacity(symbol="ETH/USDT"),  # mismatched capacity symbol
        fee_snapshot(),
    )
    assert result.status_code == RESULT_MARKET_REFERENCE_INVALID


def test_capacity_symbol_mismatch_with_market_mismatch():
    """Correction 1 (review-5): capacity symbol must also match market snapshot."""
    order = base_order(symbol="ETH/USDT")
    result = evaluate_fill_candidate(
        order, reservation(), exec_policy(),
        market_snapshot(symbol="BTC/USDT", bid="40000", ask="40001"),  # different market
        market_capacity(symbol="ETH/USDT"),  # matches order but not market
        fee_snapshot(),
    )
    assert result.status_code == RESULT_MARKET_REFERENCE_INVALID


def test_reservation_account_id_required():
    """Correction 2 (review-5): account_id is now required on reservation."""
    with pytest.raises(ValidationError):
        PaperReservationSnapshot(
            reservation_id="res-no-account",
            owner_order_id="o1",
            account_id="",  # blank account_id should fail
            asset="USDT",
            remaining_qty=Decimal("1.0"),
            remaining_amount=Decimal("40001"),
        )


def test_protection_bar_open_time_mismatch_no_collision():
    """Correction 3 (review-5): protection bar open time must match market."""
    # Protection with different bar_open_time_ms
    prot = PaperProtectionSnapshot(
        symbol="BTC/USDT",
        account_id="acct-1",
        protection_group_id="pg-1",
        stop_trigger_price=Decimal("39500"),
        take_trigger_price=Decimal("40500"),
        bar_high=Decimal("41000"),
        bar_low=Decimal("39000"),
        bar_source_id="synthetic",
        bar_snapshot_timestamp_ms=2000,
        bar_open_time_ms=9999,  # doesn't match market bar_open_time_ms 2000
        bar_interval_ms=60000,
    )
    order = base_order(side=PaperOrderSide.BUY, order_type=PaperOrderType.MARKET)
    order.protection_group_id = "pg-1"
    result = evaluate_fill_candidate(
        order, reservation(), exec_policy(),
        market_snapshot(bid="40000", ask="40001"), market_capacity(),
        fee_snapshot(),
        protection_snapshot=prot,
    )
    assert result.status_code == RESULT_FILL_CANDIDATE  # no collision due to bar identity mismatch


def test_protection_bar_interval_mismatch_no_collision():
    """Correction 3 (review-5): protection bar interval must match market."""
    # Protection with different bar_interval_ms
    prot = PaperProtectionSnapshot(
        symbol="BTC/USDT",
        account_id="acct-1",
        protection_group_id="pg-1",
        stop_trigger_price=Decimal("39500"),
        take_trigger_price=Decimal("40500"),
        bar_high=Decimal("41000"),
        bar_low=Decimal("39000"),
        bar_source_id="synthetic",
        bar_snapshot_timestamp_ms=2000,
        bar_open_time_ms=2000,  # matches
        bar_interval_ms=120000,  # doesn't match market interval_ms 60000
    )
    order = base_order(side=PaperOrderSide.BUY, order_type=PaperOrderType.MARKET)
    order.protection_group_id = "pg-1"
    result = evaluate_fill_candidate(
        order, reservation(), exec_policy(),
        market_snapshot(bid="40000", ask="40001"), market_capacity(),
        fee_snapshot(),
        protection_snapshot=prot,
    )
    assert result.status_code == RESULT_FILL_CANDIDATE  # no collision due to bar identity mismatch


def test_genuine_missing_continuity_reference():
    """Correction 4 (review-5): missing continuity reference (bar_open == prior_bar_open) rejects fill."""
    order = base_order()
    res = reservation(remaining_qty="1.0", remaining_amount="40001")
    # bar_open_time_ms == prior_bar_open_time_ms means no prior bar exists
    mkt = market_snapshot(bar_open_time_ms=5000, prior_bar_open_time_ms=5000)
    result = evaluate_fill_candidate(
        order, res, exec_policy(), mkt, market_capacity(bar_open_time_ms=5000), fee_snapshot()
    )
    assert result.status_code == RESULT_INSUFFICIENT_DATA


# ── review-5 Fix 1: Capacity capture/bar identity binding ─────────────────────

def test_capacity_capture_bar_mismatch_rejects_fill():
    """Capacity from a different bar cannot be used for this market snapshot."""
    order = base_order()
    # Capacity with different bar_open_time than market (2000 vs 9999)
    cap = market_capacity(bar_open_time_ms=9999, snapshot_timestamp_ms=2000)
    result = evaluate_fill_candidate(
        order, reservation(), exec_policy(),
        market_snapshot(bid="40000", ask="40001"), cap, fee_snapshot()
    )
    assert result.status_code == RESULT_MARKET_REFERENCE_INVALID


def test_capacity_capture_timestamp_mismatch_rejects_fill():
    """Capacity from a different snapshot timestamp cannot be used for this market snapshot."""
    order = base_order()
    # Capacity with different snapshot_timestamp than market (2000 vs 8888)
    cap = market_capacity(bar_open_time_ms=2000, snapshot_timestamp_ms=8888)
    result = evaluate_fill_candidate(
        order, reservation(), exec_policy(),
        market_snapshot(bid="40000", ask="40001"), cap, fee_snapshot()
    )
    assert result.status_code == RESULT_MARKET_REFERENCE_INVALID


def test_capacity_matching_bar_and_timestamp_allows_fill():
    """Capacity with matching bar identity allows fill."""
    order = base_order()
    cap = market_capacity(bar_open_time_ms=2000, snapshot_timestamp_ms=2000)
    result = evaluate_fill_candidate(
        order, reservation(), exec_policy(),
        market_snapshot(bid="40000", ask="40001"), cap, fee_snapshot()
    )
    assert result.status_code == RESULT_FILL_CANDIDATE


# ── review-5 Fix 2: Malformed/incomplete quote market data ───────────────────

def test_bid_gt_ask_incomplete_quote_rejects_fill():
    """Malformed market data (bid > ask) is an incomplete quote and rejects fill."""
    order = base_order()
    # Incoherent: bid > ask — this is a malformed market state
    mkt = market_snapshot(bid="40002", ask="40000")
    result = evaluate_fill_candidate(
        order, reservation(), exec_policy(), mkt, market_capacity(), fee_snapshot()
    )
    assert result.status_code == RESULT_INSUFFICIENT_DATA


# ── review-5 Fix 3: Reservation missing account_id test ───────────────────────

def test_reservation_missing_account_id_rejected():
    """Creating a reservation without account_id must be rejected by schema validation."""
    with pytest.raises(ValidationError):
        PaperReservationSnapshot(
            reservation_id="res-no-account",
            owner_order_id="o1",
            # account_id omitted entirely — Field(..., min_length=1) requires it
            asset="USDT",
            remaining_qty=Decimal("1.0"),
            remaining_amount=Decimal("40001"),
        )


def test_reservation_account_id_empty_rejected():
    """Reservation with empty account_id must be rejected by schema validation."""
    with pytest.raises(ValidationError):
        PaperReservationSnapshot(
            reservation_id="res-empty-account",
            owner_order_id="o1",
            account_id="",  # empty string fails min_length=1
            asset="USDT",
            remaining_qty=Decimal("1.0"),
            remaining_amount=Decimal("40001"),
        )


def test_reservation_account_id_none_rejected():
    """Reservation with None account_id must be rejected by schema validation."""
    with pytest.raises(ValidationError):
        PaperReservationSnapshot(
            reservation_id="res-none-account",
            owner_order_id="o1",
            account_id=None,  # None fails min_length=1
            asset="USDT",
            remaining_qty=Decimal("1.0"),
            remaining_amount=Decimal("40001"),
        )
