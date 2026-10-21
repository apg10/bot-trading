from decimal import Decimal, ROUND_DOWN, ROUND_UP, localcontext

import pytest
from pydantic import ValidationError

from src.models.paper_contracts import PaperOrder, PaperOrderSide, PaperOrderType, StrategyRef
from src.models.paper_risk import (
    PaperRiskPolicy,
    PaperRiskSnapshot,
    evaluate_paper_risk,
)


def policy(**overrides):
    values = dict(
        policy_id="risk-1", policy_version="v1", account_id="acct-1",
        quote_asset="USDT", max_order_notional=Decimal("100"),
        max_total_exposure=Decimal("500"), max_daily_loss=Decimal("20"),
    )
    values.update(overrides)
    return PaperRiskPolicy(**values)


def snapshot(**overrides):
    values = dict(
        account_id="acct-1", quote_asset="USDT", market_reference="mark:BTC/USDT:42",
        market_symbol="BTC/USDT", market_mark_price=Decimal("25"),
        market_timestamp_ms=42, equity_reference="ledger:acct-1:today",
        equity_timestamp_ms=42, equity_utc_day_start=Decimal("1000"),
        equity_current_marked=Decimal("990"), exposure_reference="exposure:acct-1:42",
        exposure_timestamp_ms=42, marked_open_inventory=Decimal("100"),
        remaining_buy_reservations=Decimal("20"),
    )
    values.update(overrides)
    return PaperRiskSnapshot(**values)


def order(*, kind=PaperOrderType.LIMIT, qty="2", price="25", max_quote=None, side=PaperOrderSide.BUY):
    return PaperOrder(
        order_id="o1", client_order_id="c1", paper_account_id="acct-1",
        paper_session_id="session-1", symbol="BTC/USDT", side=side,
        order_type=kind, requested_qty=Decimal(qty),
        price=Decimal(price) if kind == PaperOrderType.LIMIT else None,
        max_quote_amount=Decimal(max_quote) if max_quote is not None else None,
        strategy_ref=StrategyRef(strategy_id="s1", strategy_version="v1"),
        timestamp_ms=1,
    )


def test_policy_requires_all_explicit_fields_and_strict_finite_nonnegative_decimals():
    with pytest.raises(ValidationError):
        PaperRiskPolicy(policy_id="p")
    for bad in (1, True, "1", Decimal("NaN"), Decimal("Infinity"), Decimal("-0.01")):
        with pytest.raises(ValidationError):
            policy(max_order_notional=bad)
    assert policy(max_order_notional=Decimal("0")).max_order_notional == 0
    with pytest.raises(ValidationError):
        policy().policy_id = "changed"


@pytest.mark.parametrize(
    ("missing", "expected"),
    [(None, "risk_snapshot_insufficient"),
     ("account", "risk_snapshot_invalid"),
     ("quote", "risk_snapshot_invalid"),
     ("invalid", "risk_snapshot_invalid")],
)
def test_snapshot_missing_inconsistent_and_invalid_fail_closed(missing, expected):
    if missing is None:
        snap = None
    elif missing == "invalid":
        fields = snapshot().model_dump()
        fields["equity_current_marked"] = Decimal("NaN")
        snap = PaperRiskSnapshot.model_construct(**fields)
    else:
        snap = snapshot(**({"account_id": "other"} if missing == "account" else {"quote_asset": "BTC"}))
    assert evaluate_paper_risk(order(), policy(), snap).code == expected


def test_order_size_limit_market_limit_and_zero_limits():
    assert evaluate_paper_risk(order(), policy(max_order_notional=Decimal("50")), snapshot()).approved
    too_large = evaluate_paper_risk(order(qty="2.01"), policy(max_order_notional=Decimal("50")), snapshot())
    assert too_large.code == "risk_order_size_limit" and too_large.order_notional == Decimal("50.25")
    market = order(kind=PaperOrderType.MARKET, max_quote="100")
    assert evaluate_paper_risk(market, policy(max_order_notional=Decimal("100")), snapshot()).approved
    assert evaluate_paper_risk(market, policy(max_order_notional=Decimal("0")), snapshot()).code == "risk_order_size_limit"


def test_daily_loss_limit_is_inclusive_and_precedes_exposure():
    result = evaluate_paper_risk(
        order(), policy(max_daily_loss=Decimal("10"), max_total_exposure=Decimal("0")),
        snapshot(equity_current_marked=Decimal("990")),
    )
    assert result.code == "risk_daily_loss_limit"
    assert result.daily_loss == Decimal("10")


def test_exposure_equality_is_accepted_and_excess_rejected():
    equal = evaluate_paper_risk(
        order(), policy(max_total_exposure=Decimal("170")), snapshot()
    )
    assert equal.approved and equal.projected_exposure == Decimal("170")
    too_much = evaluate_paper_risk(
        order(), policy(max_total_exposure=Decimal("169.99")), snapshot()
    )
    assert too_much.code == "risk_exposure_limit"


def test_invalid_order_size_and_account_or_quote_mismatch():
    assert evaluate_paper_risk(order(), policy(account_id="another"), snapshot()).code == "risk_snapshot_invalid"
    assert evaluate_paper_risk(order(), policy(quote_asset="USD"), snapshot()).code == "risk_snapshot_invalid"
    with pytest.raises(ValidationError):
        order(qty="0")
    with pytest.raises(ValidationError):
        order(kind=PaperOrderType.MARKET, max_quote="0")
    with pytest.raises(ValidationError):
        snapshot(market_mark_price=Decimal("0"))
    assert evaluate_paper_risk(order(), policy(), snapshot(market_symbol="ETH/USDT")).code == "risk_snapshot_invalid"


def test_evaluation_is_pure_stable_and_independent_of_global_decimal_context():
    candidate = order()
    snap = snapshot()
    before_order, before_snapshot = candidate.model_dump(), snap.model_dump()
    expected = evaluate_paper_risk(candidate, policy(), snap)
    outcomes = []
    for precision, rounding in ((2, ROUND_DOWN), (50, ROUND_UP)):
        with localcontext() as context:
            context.prec, context.rounding = precision, rounding
            outcomes.append(evaluate_paper_risk(candidate, policy(), snap))
    assert outcomes == [expected, expected]
    assert candidate.model_dump() == before_order
    assert snap.model_dump() == before_snapshot
    assert expected.policy_id == "risk-1" and expected.policy_version == "v1"


def test_non_buy_is_not_rejected_by_entry_risk_policy():
    candidate = order().model_copy(update={"side": PaperOrderSide.SELL})
    for policy_arg, snapshot_arg in (
        (policy(), snapshot()),
        (None, None),
        (policy(), None),
        (None, snapshot()),
    ):
        result = evaluate_paper_risk(candidate, policy_arg, snapshot_arg)
        assert result.approved and result.code == "risk_not_applicable"


def test_non_entry_order_is_not_rejected_by_missing_risk_inputs():
    candidate = order().model_copy(
        update={
            "order_type": PaperOrderType.STOP_LOSS,
            "price": None,
            "trigger_price": Decimal("20"),
        }
    )
    result = evaluate_paper_risk(candidate, None, None)
    assert result.approved and result.code == "risk_not_applicable"
