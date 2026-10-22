"""Integration tests for durable PAPER fill-candidate orchestration."""

from __future__ import annotations

import asyncio
import sqlite3
from decimal import Decimal
from pathlib import Path
from unittest.mock import Mock

import pytest

from src.models.paper_contracts import (
    AssetBalance,
    Commission,
    LotAllocation,
    PaperAccountOpening,
    PaperFill,
    PaperOrder,
    PaperOrderSide,
    PaperOrderStatus,
    PaperOrderType,
    PaperReservationRef,
    StrategyRef,
)
from src.models.paper_execution import (
    PaperExecutionPolicy,
    PaperFeeSnapshot,
    PaperMarketCapacitySnapshot,
    PaperMarketExecutionSnapshot,
    PaperReservationSnapshot,
    RESULT_FILL_CANDIDATE,
    RESULT_LIMIT_NOT_CROSSED,
)
import src.paper_execution_integration as integration_module
from src.paper_execution_integration import (
    PaperFillIntegrationConflictError,
    _deterministic_fill_id,
    evaluate_and_apply_fill,
)
from src.persistence.sqlite_store import SQLiteStore


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _store(db: Path) -> SQLiteStore:
    store = SQLiteStore(db)
    _run(store.initialize())
    _run(
        store.open_paper_account(
            PaperAccountOpening(
                environment="paper",
                account_id="acct-1",
                balances=(
                    AssetBalance(
                        asset="BTC", total=Decimal("0"), available=Decimal("0"), reserved=Decimal("0")
                    ),
                    AssetBalance(
                        asset="USDT", total=Decimal("1000"), available=Decimal("1000"), reserved=Decimal("0")
                    ),
                ),
            )
        )
    )
    _run(store.initialize_lifecycle_state("acct-1", "sess-1", 1000))
    return store


def _buy(order_id: str = "buy-1", qty: str = "1", price: str = "50") -> PaperOrder:
    return PaperOrder(
        order_id=order_id,
        client_order_id=f"client-{order_id}",
        paper_account_id="acct-1",
        paper_session_id="sess-1",
        environment="paper",
        symbol="BTC/USDT",
        side=PaperOrderSide.BUY,
        order_type=PaperOrderType.LIMIT,
        requested_qty=Decimal(qty),
        price=Decimal(price),
        strategy_ref=StrategyRef(strategy_id="strategy-1", strategy_version="v1"),
        status=PaperOrderStatus.PREPARED,
        timestamp_ms=1000,
    )


def _accept_buy(store: SQLiteStore, order: PaperOrder) -> PaperOrder:
    from src.models.paper_risk import PaperRiskPolicy, PaperRiskSnapshot
    from src.models.paper_symbol_filters import (
        LotSizeFilter,
        MarketLotSizeFilter,
        MinNotionalFilter,
        PriceFilter,
        SymbolFilterSnapshot,
    )

    policy = PaperRiskPolicy(
        policy_id="policy-1", policy_version="v1", account_id="acct-1", quote_asset="USDT",
        max_order_notional=Decimal("1000000"), max_total_exposure=Decimal("1000000"),
        max_daily_loss=Decimal("1000000"),
    )
    risk = PaperRiskSnapshot(
        account_id="acct-1", quote_asset="USDT", market_symbol="BTC/USDT",
        market_mark_price=Decimal("50"), market_reference="test", market_timestamp_ms=1,
        equity_reference="test", equity_timestamp_ms=1,
        equity_utc_day_start=Decimal("1000"), equity_current_marked=Decimal("1000"),
        exposure_reference="test", exposure_timestamp_ms=1,
        marked_open_inventory=Decimal("0"), remaining_buy_reservations=Decimal("0"),
    )
    filters = SymbolFilterSnapshot(
        snapshot_id="filters-1", filter_set_id="set-1", filter_set_version="v1",
        source_id="source-1", source_version="v1", capture_timestamp_ms=1,
        symbol="BTC/USDT", base_asset="BTC", quote_asset="USDT",
        price_filter=PriceFilter(min_price=Decimal("0"), max_price=Decimal("0"), tick_size=Decimal("0")),
        lot_size=LotSizeFilter(min_qty=Decimal("0.0001"), max_qty=Decimal("100"), step_size=order.requested_qty),
        market_lot_size=MarketLotSizeFilter(min_qty=Decimal("0.0001"), max_qty=Decimal("100"), step_size=order.requested_qty),
        min_notional=MinNotionalFilter(min_notional=Decimal("0.01"), applies_to_market=False, avg_price_mins=0),
        notional=None, unhandled_filter_types=(),
    )
    return _run(store.accept_paper_order(order.order_id, policy, risk, filters))


def _market(*, bid: str = "48", ask: str = "49") -> PaperMarketExecutionSnapshot:
    return PaperMarketExecutionSnapshot(
        symbol="BTC/USDT", bid=Decimal(bid), ask=Decimal(ask),
        bar_open_time_ms=2000, prior_bar_open_time_ms=1000, interval_ms=60000,
        provenance="synthetic", version="v1", snapshot_timestamp_ms=2000,
    )


def _capacity() -> PaperMarketCapacitySnapshot:
    return PaperMarketCapacitySnapshot(
        source_id="capacity-1", source_version="v1", capacity=Decimal("1"),
        symbol="BTC/USDT", capture_bar_open_time_ms=2000,
        capture_snapshot_timestamp_ms=2000,
    )


def _policy() -> PaperExecutionPolicy:
    return PaperExecutionPolicy(
        policy_id="execution-1", policy_version="v1", latency_slippage_mode="UNMODELLED"
    )


def _fee(amount: str = "0.5") -> PaperFeeSnapshot:
    return PaperFeeSnapshot(
        fee_id="fee-1", fee_version="v1", quantity=Decimal("1"),
        commission=Commission(amount=Decimal(amount), asset="USDT"),
    )


def _reservation(order: PaperOrder, *, asset: str = "USDT", amount: str = "50") -> PaperReservationSnapshot:
    return PaperReservationSnapshot(
        reservation_id=order.order_id,
        owner_order_id=order.order_id,
        account_id=order.paper_account_id,
        asset=asset,
        remaining_qty=order.requested_qty,
        remaining_amount=Decimal(amount),
    )


def _args(order: PaperOrder, *, market: PaperMarketExecutionSnapshot | None = None):
    return dict(
        order=order,
        reservation_snapshot=_reservation(
            order,
            asset="BTC" if order.side == PaperOrderSide.SELL else "USDT",
            amount="1" if order.side == PaperOrderSide.SELL else "50",
        ),
        exec_policy_snapshot=_policy(),
        market_exec_snapshot=market or _market(),
        market_capacity_snapshot=_capacity(),
        fee_snapshot=_fee(),
        evaluated_at_ms=3000,
    )


def _setup_buy(tmp_path: Path) -> tuple[SQLiteStore, PaperOrder]:
    store = _store(tmp_path / "integration.db")
    order = _buy()
    _run(store.register_paper_order(order))
    order = _accept_buy(store, order)
    return store, order


def _ledger_state(db: Path, order_id: str) -> tuple:
    conn = sqlite3.connect(str(db))
    try:
        order = conn.execute("SELECT order_json FROM paper_orders WHERE order_id=?", (order_id,)).fetchone()[0]
        balances = tuple(conn.execute("SELECT asset, balance_json FROM paper_account_balances ORDER BY asset").fetchall())
        reservation = conn.execute("SELECT * FROM paper_reservations WHERE owner_order_id=?", (order_id,)).fetchall()
        fills = tuple(conn.execute("SELECT * FROM paper_fills ORDER BY fill_id").fetchall())
        applications = tuple(conn.execute("SELECT * FROM paper_fill_applications ORDER BY fill_id").fetchall())
        return order, balances, tuple(reservation), fills, applications
    finally:
        conn.close()


def test_buy_candidate_end_to_end(tmp_path: Path) -> None:
    store, order = _setup_buy(tmp_path)
    result = _run(evaluate_and_apply_fill(store, "evaluation-1", **_args(order)))
    assert result.status_code == RESULT_FILL_CANDIDATE
    assert result.fill_id == _deterministic_fill_id("evaluation-1")
    assert result.applied is True
    assert result.evaluation_persisted is True
    assert _run(store.load_paper_fill(result.fill_id)) is not None
    assert _run(store.load_paper_order(order.order_id)).status == PaperOrderStatus.FILLED
    _run(store.close())


def test_non_candidate_no_ledger_mutation(tmp_path: Path) -> None:
    store, order = _setup_buy(tmp_path)
    before = _ledger_state(tmp_path / "integration.db", order.order_id)
    result = _run(evaluate_and_apply_fill(
        store, "evaluation-non-candidate", **_args(order, market=_market(ask="51"))
    ))
    assert result.status_code == RESULT_LIMIT_NOT_CROSSED
    assert result.applied is False
    assert _ledger_state(tmp_path / "integration.db", order.order_id) == before
    _run(store.close())


def test_same_evaluation_retry_idempotent(tmp_path: Path) -> None:
    store, order = _setup_buy(tmp_path)
    first = _run(evaluate_and_apply_fill(store, "evaluation-retry", **_args(order)))
    second = _run(evaluate_and_apply_fill(store, "evaluation-retry", **_args(order)))
    assert second == first
    conn = sqlite3.connect(str(tmp_path / "integration.db"))
    assert conn.execute("SELECT COUNT(*) FROM paper_fills").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM paper_fill_applications").fetchone()[0] == 1
    conn.close()
    _run(store.close())


def test_existing_mutable_candidate_retry_does_not_re_evaluate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, order = _setup_buy(tmp_path)

    async def skip_application(_fill_id: str) -> None:
        return None

    monkeypatch.setattr(store, "apply_paper_fill", skip_application)
    first = _run(evaluate_and_apply_fill(store, "evaluation-no-retry-eval", **_args(order)))
    assert first.status_code == RESULT_FILL_CANDIDATE
    monkeypatch.undo()

    evaluator = Mock(side_effect=AssertionError("retry must not re-evaluate"))
    monkeypatch.setattr(integration_module, "evaluate_fill_candidate", evaluator)
    second = _run(
        evaluate_and_apply_fill(store, "evaluation-no-retry-eval", **_args(order))
    )

    evaluator.assert_not_called()
    assert second.applied is True
    assert second.evaluation_persisted is True
    _run(store.close())


def test_failure_after_evaluation_persist_retry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store, order = _setup_buy(tmp_path)
    original = store.persist_fill_evaluation

    async def persist_then_fail(*args, **kwargs):
        await original(*args, **kwargs)
        raise OSError("failure after evaluation persist")

    monkeypatch.setattr(store, "persist_fill_evaluation", persist_then_fail)
    with pytest.raises(OSError, match="after evaluation persist"):
        _run(evaluate_and_apply_fill(store, "evaluation-persist-failure", **_args(order)))
    monkeypatch.undo()
    recovered = _run(evaluate_and_apply_fill(store, "evaluation-persist-failure", **_args(order)))
    assert recovered.applied is True
    assert recovered.evaluation_persisted is True
    _run(store.close())


def test_failure_after_journal_save_retry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store, order = _setup_buy(tmp_path)
    original = store.save_paper_fill

    async def save_then_fail(*args, **kwargs):
        await original(*args, **kwargs)
        raise OSError("failure after journal save")

    monkeypatch.setattr(store, "save_paper_fill", save_then_fail)
    with pytest.raises(OSError, match="after journal save"):
        _run(evaluate_and_apply_fill(store, "evaluation-journal-failure", **_args(order)))
    monkeypatch.undo()
    recovered = _run(evaluate_and_apply_fill(store, "evaluation-journal-failure", **_args(order)))
    assert recovered.applied is True
    conn = sqlite3.connect(str(tmp_path / "integration.db"))
    assert conn.execute("SELECT COUNT(*) FROM paper_fills").fetchone()[0] == 1
    conn.close()
    _run(store.close())


def test_post_commit_application_uncertainty(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store, order = _setup_buy(tmp_path)
    original = store.apply_paper_fill

    async def apply_then_fail(*args, **kwargs):
        result = await original(*args, **kwargs)
        raise OSError("lost application acknowledgement")

    monkeypatch.setattr(store, "apply_paper_fill", apply_then_fail)
    result = _run(evaluate_and_apply_fill(store, "evaluation-apply-uncertain", **_args(order)))
    assert result.applied is True
    assert _run(store.load_paper_order(order.order_id)).status == PaperOrderStatus.FILLED
    _run(store.close())


def test_conflicting_evaluation_payload(tmp_path: Path) -> None:
    store, order = _setup_buy(tmp_path)
    _run(evaluate_and_apply_fill(
        store, "evaluation-conflict", **_args(order, market=_market(ask="51"))
    ))
    with pytest.raises(PaperFillIntegrationConflictError):
        _run(evaluate_and_apply_fill(store, "evaluation-conflict", **_args(order)))
    _run(store.close())


def test_existing_evaluation_rejects_order_identity_conflict(tmp_path: Path) -> None:
    store, order = _setup_buy(tmp_path)
    _run(evaluate_and_apply_fill(store, "evaluation-identity-conflict", **_args(order)))

    changed_order = order.model_copy(
        update={
            "order_id": "different-order",
            "client_order_id": "client-different-order",
            "paper_account_id": "different-account",
        }
    )
    with pytest.raises(PaperFillIntegrationConflictError, match="mismatch"):
        _run(
            evaluate_and_apply_fill(
                store, "evaluation-identity-conflict", **_args(changed_order)
            )
        )
    _run(store.close())


def test_sell_candidate_with_allocations(tmp_path: Path) -> None:
    store, buy = _setup_buy(tmp_path)
    buy_fill = PaperFill(
        fill_id="seed-buy-fill", order_id=buy.order_id, qty=Decimal("1"),
        price=Decimal("49"), timestamp_ms=2000,
        commission=Commission(amount=Decimal("0.5"), asset="USDT"),
    )
    _run(store.save_paper_fill(buy_fill))
    _run(store.apply_paper_fill(buy_fill.fill_id))
    allocation = LotAllocation(
        position_id=buy.order_id, account_id="acct-1", symbol="BTC/USDT",
        strategy_ref=buy.strategy_ref, qty=Decimal("1"),
    )
    sell = PaperOrder(
        order_id="sell-1", client_order_id="client-sell-1", paper_account_id="acct-1",
        paper_session_id="sess-1", environment="paper", symbol="BTC/USDT",
        side=PaperOrderSide.SELL, order_type=PaperOrderType.LIMIT,
        requested_qty=Decimal("1"), price=Decimal("50"), strategy_ref=buy.strategy_ref,
        status=PaperOrderStatus.PREPARED, timestamp_ms=2500, lot_allocations=(allocation,),
    )
    _run(store.register_paper_order(sell))
    sell = _run(store.accept_paper_order(sell.order_id))
    result = _run(evaluate_and_apply_fill(
        store, "evaluation-sell", **_args(sell, market=_market(bid="55", ask="56"))
    ))
    assert result.applied is True
    assert _run(store.load_paper_order(sell.order_id)).status == PaperOrderStatus.FILLED
    conn = sqlite3.connect(str(tmp_path / "integration.db"))
    assert conn.execute("SELECT COUNT(*) FROM paper_reservations").fetchone()[0] == 0
    conn.close()
    _run(store.close())


def test_deterministic_fill_id() -> None:
    assert _deterministic_fill_id("same") == _deterministic_fill_id("same")
    assert _deterministic_fill_id("same") != _deterministic_fill_id("different")
    assert _deterministic_fill_id("same").startswith("eval-")


def test_non_candidate_preserves_order_state(tmp_path: Path) -> None:
    store, order = _setup_buy(tmp_path)
    before = _ledger_state(tmp_path / "integration.db", order.order_id)
    result = _run(evaluate_and_apply_fill(
        store, "evaluation-state-preserved", **_args(order, market=_market(ask="60"))
    ))
    assert result.status_code == RESULT_LIMIT_NOT_CROSSED
    assert result.evaluation_persisted is True
    assert _ledger_state(tmp_path / "integration.db", order.order_id) == before
    evaluation = _run(store.load_paper_fill_evaluation("evaluation-state-preserved"))
    assert evaluation is not None
    assert evaluation.status_code == RESULT_LIMIT_NOT_CROSSED
    _run(store.close())
