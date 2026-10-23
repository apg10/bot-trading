"""Contract tests for laboratory capital and execution-cost projections."""

from decimal import Decimal
from pathlib import Path
import asyncio

from src.models.laboratory_metrics import build_capital_curve, build_cost_breakdown
from src.models.paper_contracts import (
    Commission,
    AssetBalance,
    PaperAccountOpening,
    PaperFill,
    PaperOrder,
    PaperOrderSide,
    PaperOrderType,
    LotAllocation,
    StrategyRef,
)
from src.models.paper_execution import PaperFillCandidateResult, PaperFillEvaluationRecord
from src.persistence.sqlite_store import SQLiteStore


ACCOUNT, SESSION, QUOTE = "acct", "session", "USDT"
STRATEGY = StrategyRef(strategy_id="strategy", strategy_version="v1", decision_id=None)


def _run(coro):
    return asyncio.run(coro)


def _order(order_id: str, side: PaperOrderSide, session: str = SESSION, symbol: str = "BTC/USDT") -> PaperOrder:
    allocations = () if side == PaperOrderSide.BUY else (
        LotAllocation(
            position_id=f"position-{order_id}",
            account_id=ACCOUNT,
            symbol=symbol,
            strategy_ref=STRATEGY,
            qty=Decimal("1"),
        ),
    )
    return PaperOrder(
        order_id=order_id,
        client_order_id=f"client-{order_id}",
        paper_account_id=ACCOUNT,
        paper_session_id=session,
        symbol=symbol,
        side=side,
        order_type=PaperOrderType.LIMIT,
        requested_qty=Decimal("1"),
        lot_allocations=allocations,
        price=Decimal("10"),
        strategy_ref=STRATEGY,
        timestamp_ms=1,
    )


def _fill(fill_id: str, order_id: str, *, commission: Commission | None = Commission(amount=Decimal("1"), asset=QUOTE), price: str = "10", timestamp: int = 1) -> PaperFill:
    return PaperFill(
        fill_id=fill_id,
        order_id=order_id,
        qty=Decimal("1"),
        price=Decimal(price),
        timestamp_ms=timestamp,
        commission=commission,
    )


def _evaluation(fill_id: str, *, model: str, reference: str | None = None) -> PaperFillEvaluationRecord:
    result = PaperFillCandidateResult(
        status_code="fill_candidate",
        cost_model=model,
        reference_price=Decimal(reference) if reference is not None else None,
    )
    return PaperFillEvaluationRecord(
        evaluation_id=f"evaluation-{fill_id}",
        order_id=f"order-{fill_id}",
        account_id=ACCOUNT,
        paper_session_id=SESSION,
        environment="paper",
        status_code="fill_candidate",
        result_json=result.model_dump_json(),
        evaluated_at_ms=1,
        fill_candidate_fill_id=fill_id,
    )


def test_capital_curve_buy_sell_drawdown_is_deterministic() -> None:
    orders = {"buy": _order("buy", PaperOrderSide.BUY), "sell": _order("sell", PaperOrderSide.SELL)}
    fills = [_fill("buy-fill", "buy", price="10", timestamp=1), _fill("sell-fill", "sell", price="20", timestamp=2)]

    report = build_capital_curve(ACCOUNT, SESSION, QUOTE, Decimal("100"), fills, orders)

    assert report.status == "available"
    assert [(point.balance, point.peak_balance, point.drawdown) for point in report.points] == [
        (Decimal("89"), Decimal("100"), Decimal("11")),
        (Decimal("108"), Decimal("108"), Decimal("0")),
    ]


def test_unknown_commission_is_distinct_from_known_zero() -> None:
    order = _order("order-unknown", PaperOrderSide.BUY)
    unknown = build_cost_breakdown(
        ACCOUNT, SESSION, QUOTE, [_fill("unknown", order.order_id, commission=None)], {order.order_id: order}, []
    ).fills[0]
    zero = build_cost_breakdown(
        ACCOUNT, SESSION, QUOTE,
        [_fill("zero", order.order_id, commission=Commission(amount=Decimal("0"), asset=QUOTE))],
        {order.order_id: order}, [],
    ).fills[0]

    assert unknown.commission is None
    assert unknown.total_cost is None
    assert zero.commission is not None and zero.commission.amount == Decimal("0")
    # total_cost is None when slippage is insufficient_data (no evaluation record)
    assert zero.total_cost is None


def test_unmodelled_slippage_is_known_zero() -> None:
    order = _order("order-unmodelled", PaperOrderSide.BUY)
    row = build_cost_breakdown(
        ACCOUNT, SESSION, QUOTE, [_fill("unmodelled", order.order_id)], {order.order_id: order},
        [_evaluation("unmodelled", model="UNMODELLED")],
    ).fills[0]
    assert row.slippage.status == "available"
    assert row.slippage.amount == Decimal("0")


def test_modelled_slippage_is_exact() -> None:
    order = _order("order-modelled", PaperOrderSide.BUY)
    row = build_cost_breakdown(
        ACCOUNT, SESSION, QUOTE, [_fill("modelled", order.order_id, price="10.25")], {order.order_id: order},
        [_evaluation("modelled", model="MODELLED", reference="10.00")],
    ).fills[0]
    assert row.slippage.amount == Decimal("0.25")
    assert row.total_cost is not None and row.total_cost.amount == Decimal("1.25")


def test_old_modelled_evaluation_without_reference_is_insufficient_data() -> None:
    order = _order("order-old", PaperOrderSide.BUY)
    row = build_cost_breakdown(
        ACCOUNT, SESSION, QUOTE, [_fill("old", order.order_id)], {order.order_id: order},
        [_evaluation("old", model="MODELLED")],
    ).fills[0]
    assert row.slippage.status == "insufficient_data"
    assert row.slippage.amount is None
    assert row.total_cost is None


def test_sqlite_reconciliation_after_reopen_balance_mismatch_is_insufficient_data(tmp_path: Path) -> None:
    # SQLite's explicit application marker must not be interpreted as a zero
    # balance when the journal is not reconciled after reopening.
    order = _order("order-reopened", PaperOrderSide.BUY)
    fill = _fill("reopened", order.order_id)
    db = tmp_path / "metrics-reopen.db"
    store = SQLiteStore(db)
    _run(store.initialize())
    _run(store.open_paper_account(PaperAccountOpening(
        account_id=ACCOUNT,
        balances=(AssetBalance(asset=QUOTE, total=Decimal("100"), available=Decimal("100"), reserved=Decimal("0")),),
    )))
    _run(store.register_paper_order(order))
    _run(store.save_paper_fill(fill))
    _run(store.close())
    reopened = SQLiteStore(db)
    _run(reopened.initialize())
    opening = _run(reopened.load_paper_account_opening(ACCOUNT))
    assert opening is not None
    fills = _run(reopened.get_paper_fills(ACCOUNT, SESSION))
    orders = _run(reopened.get_paper_orders(ACCOUNT, SESSION))
    report = build_capital_curve(
        ACCOUNT, SESSION, QUOTE, opening.balances[0].total, fills, orders,
    )
    assert report.status == "insufficient_data"
    assert report.reason == "no applied fills are available"
    _run(reopened.close())
