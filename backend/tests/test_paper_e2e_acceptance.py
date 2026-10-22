"""Deterministic end-to-end acceptance for the TEST_ONLY PAPER lifecycle."""

from __future__ import annotations

import asyncio
import sqlite3
from decimal import Decimal
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.paper import router
from src.models.paper_contracts import AssetBalance, Commission, PaperAccountOpening
from src.models.paper_execution import (
    PaperExecutionPolicy,
    PaperFeeSnapshot,
    PaperMarketCapacitySnapshot,
    PaperMarketExecutionSnapshot,
)
from src.models.paper_lifecycle import PaperLifecycleCommand
from src.models.paper_risk import PaperRiskPolicy, PaperRiskSnapshot
from src.models.paper_symbol_filters import (
    LotSizeFilter,
    MarketLotSizeFilter,
    MinNotionalFilter,
    PriceFilter,
    SymbolFilterSnapshot,
)
from src.paper_orchestrator import run_paper_cycle, run_paper_cycle_sell
from src.persistence.sqlite_store import SQLiteStore
from src.strategies.test_only_keltner import TEST_ONLY_OHLCV_FIXTURE


ACCOUNT_ID = "paper-e2e-account"
SESSION_ID = "paper-e2e-session"
SYMBOL = "BTC/USDT"


def _run(coro: Any) -> Any:
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _counts(db: Path) -> dict[str, int]:
    conn = sqlite3.connect(str(db))
    try:
        tables = (
            "paper_orders",
            "paper_order_acceptances",
            "paper_fill_evaluations",
            "paper_fills",
            "paper_fill_applications",
            "paper_reservations",
            "position_lots",
            "paper_sell_accounting",
        )
        return {
            table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in tables
        }
    finally:
        conn.close()


def _risk_policy() -> PaperRiskPolicy:
    return PaperRiskPolicy(
        policy_id="TEST_ONLY-risk",
        policy_version="v1",
        account_id=ACCOUNT_ID,
        quote_asset="USDT",
        max_order_notional=Decimal("1000"),
        max_total_exposure=Decimal("2000"),
        max_daily_loss=Decimal("1000"),
    )


def _risk_snapshot() -> PaperRiskSnapshot:
    return PaperRiskSnapshot(
        account_id=ACCOUNT_ID,
        quote_asset="USDT",
        market_symbol=SYMBOL,
        market_mark_price=Decimal("104"),
        market_reference="TEST_ONLY",
        market_timestamp_ms=TEST_ONLY_OHLCV_FIXTURE[6].timestamp_ms,
        equity_reference="TEST_ONLY-opening-equity",
        equity_timestamp_ms=TEST_ONLY_OHLCV_FIXTURE[6].timestamp_ms,
        equity_utc_day_start=Decimal("1000"),
        equity_current_marked=Decimal("1000"),
        exposure_reference="TEST_ONLY-opening-exposure",
        exposure_timestamp_ms=TEST_ONLY_OHLCV_FIXTURE[6].timestamp_ms,
        marked_open_inventory=Decimal("0"),
        remaining_buy_reservations=Decimal("0"),
    )


def _filters() -> SymbolFilterSnapshot:
    return SymbolFilterSnapshot(
        snapshot_id="TEST_ONLY-filters",
        filter_set_id="TEST_ONLY-filter-set",
        filter_set_version="v1",
        source_id="TEST_ONLY",
        source_version="v1",
        capture_timestamp_ms=TEST_ONLY_OHLCV_FIXTURE[6].timestamp_ms,
        symbol=SYMBOL,
        base_asset="BTC",
        quote_asset="USDT",
        price_filter=PriceFilter(
            min_price=Decimal("0"), max_price=Decimal("0"), tick_size=Decimal("0")
        ),
        lot_size=LotSizeFilter(
            min_qty=Decimal("1"), max_qty=Decimal("10"), step_size=Decimal("1")
        ),
        market_lot_size=MarketLotSizeFilter(
            min_qty=Decimal("1"), max_qty=Decimal("10"), step_size=Decimal("1")
        ),
        min_notional=MinNotionalFilter(
            min_notional=Decimal("1"), applies_to_market=False, avg_price_mins=0
        ),
        notional=None,
        unhandled_filter_types=(),
    )


def _execution_policy() -> PaperExecutionPolicy:
    return PaperExecutionPolicy(
        policy_id="TEST_ONLY-execution",
        policy_version="v1",
        latency_slippage_mode="UNMODELLED",
    )


def _market_snapshot() -> PaperMarketExecutionSnapshot:
    bar = TEST_ONLY_OHLCV_FIXTURE[6]
    prior = TEST_ONLY_OHLCV_FIXTURE[5]
    return PaperMarketExecutionSnapshot(
        symbol=SYMBOL,
        bid=Decimal("103"),
        ask=Decimal("104"),
        bar_open_time_ms=bar.timestamp_ms,
        prior_bar_open_time_ms=prior.timestamp_ms,
        interval_ms=bar.timestamp_ms - prior.timestamp_ms,
        provenance="TEST_ONLY",
        version="v1",
        snapshot_timestamp_ms=bar.timestamp_ms,
    )


def _capacity_snapshot() -> PaperMarketCapacitySnapshot:
    bar = TEST_ONLY_OHLCV_FIXTURE[6]
    return PaperMarketCapacitySnapshot(
        source_id="TEST_ONLY",
        source_version="v1",
        symbol=SYMBOL,
        capacity=Decimal("1"),
        capture_bar_open_time_ms=bar.timestamp_ms,
        capture_snapshot_timestamp_ms=bar.timestamp_ms,
    )


def _fee_snapshot() -> PaperFeeSnapshot:
    return PaperFeeSnapshot(
        fee_id="TEST_ONLY-fee",
        fee_version="v1",
        quantity=Decimal("1"),
        commission=Commission(amount=Decimal("1"), asset="USDT"),
    )


def _buy_kwargs() -> dict[str, Any]:
    return {
        "account_id": ACCOUNT_ID,
        "session_id": SESSION_ID,
        "symbol": SYMBOL,
        "quantity": Decimal("1"),
        "risk_policy": _risk_policy(),
        "risk_snapshot": _risk_snapshot(),
        "symbol_filter_snapshot": _filters(),
        "exec_policy_snapshot": _execution_policy(),
        "market_exec_snapshot": _market_snapshot(),
        "market_capacity_snapshot": _capacity_snapshot(),
        "fee_snapshot": _fee_snapshot(),
        "candles": TEST_ONLY_OHLCV_FIXTURE[:7],
        "evaluated_at_ms": TEST_ONLY_OHLCV_FIXTURE[6].timestamp_ms,
    }


def _sell_kwargs() -> dict[str, Any]:
    return {
        "account_id": ACCOUNT_ID,
        "session_id": SESSION_ID,
        "symbol": SYMBOL,
        "risk_policy": _risk_policy(),
        "risk_snapshot": _risk_snapshot(),
        "symbol_filter_snapshot": _filters(),
        "exec_policy_snapshot": _execution_policy(),
        "fee_snapshot": _fee_snapshot(),
    }


def _command(command_id: str, action: str, issued_at_ms: int) -> PaperLifecycleCommand:
    return PaperLifecycleCommand(
        command_id=command_id,
        account_id=ACCOUNT_ID,
        paper_session_id=SESSION_ID,
        action=action,
        issued_at_ms=issued_at_ms,
    )


def test_test_only_paper_lifecycle_is_deterministic_end_to_end(tmp_path: Path) -> None:
    db = tmp_path / "paper-e2e.db"
    store = SQLiteStore(db)
    _run(store.initialize())
    opening = PaperAccountOpening(
        account_id=ACCOUNT_ID,
        balances=(
            AssetBalance(
                asset="BTC",
                total=Decimal("0"),
                available=Decimal("0"),
                reserved=Decimal("0"),
            ),
            AssetBalance(
                asset="USDT",
                total=Decimal("1000"),
                available=Decimal("1000"),
                reserved=Decimal("0"),
            ),
        ),
    )
    assert _run(store.open_paper_account(opening)) == opening
    assert _run(store.initialize_lifecycle_state(ACCOUNT_ID, SESSION_ID, 1)).state == "running"

    buy = _run(run_paper_cycle(store, **_buy_kwargs()))
    assert buy.success is True
    assert (buy.signal, buy.risk_result, buy.filter_result) == (
        "BUY",
        "risk_approved",
        "symbol_filter_approved",
    )
    assert (buy.order_id, buy.evaluation_id) == ("order-id-1", "eval-cycle-1")
    assert buy.fill_id is not None
    assert _run(store.load_paper_order("order-id-1")).status.value == "filled"
    assert _run(store.load_paper_fill_evaluation("eval-cycle-1")).status_code == "fill_candidate"
    buy_fill = _run(store.load_paper_fill(buy.fill_id))
    assert buy_fill is not None
    assert (buy_fill.qty, buy_fill.price, buy_fill.commission) == (
        Decimal("1"),
        Decimal("104"),
        Commission(amount=Decimal("1"), asset="USDT"),
    )
    balance = _run(store.load_paper_balance_view(ACCOUNT_ID))
    assert balance is not None
    balances = {item.asset: item for item in balance.balances}
    assert (balances["USDT"].total, balances["USDT"].available, balances["USDT"].reserved) == (
        Decimal("895"), Decimal("895"), Decimal("0")
    )
    assert (balances["BTC"].total, balances["BTC"].available, balances["BTC"].reserved) == (
        Decimal("1"), Decimal("1"), Decimal("0")
    )
    assert _run(store.load_paper_reservation("order-id-1")) is None
    positions = _run(store.get_paper_position_lots(ACCOUNT_ID))
    assert len(positions) == 1
    assert (positions[0]["total_qty"], positions[0]["total_cost"]) == ("1", "104")
    assert _counts(db) == {
        "paper_orders": 1,
        "paper_order_acceptances": 1,
        "paper_fill_evaluations": 1,
        "paper_fills": 1,
        "paper_fill_applications": 1,
        "paper_reservations": 0,
        "position_lots": 1,
        "paper_sell_accounting": 0,
    }

    assert _run(store.apply_lifecycle_command(_command("pause-1", "pause", 2))).state == "paused"
    before_paused_buy = _counts(db)
    rejected_buy = _run(run_paper_cycle(store, **_buy_kwargs()))
    assert rejected_buy.success is False
    assert rejected_buy.signal == "BUY"
    assert "RUNNING" in (rejected_buy.error or "")
    assert _counts(db) == before_paused_buy

    sell = _run(run_paper_cycle_sell(store, **_sell_kwargs()))
    assert sell.success is True
    assert (sell.signal, sell.buy_status, sell.sell_status) == ("SELL", "filled", "filled")
    assert (sell.order_id, sell.evaluation_id) == ("sell-order-1", "eval-cycle-sell-1")
    sell_fill = _run(store.load_paper_fill(sell.fill_id))
    assert sell_fill is not None
    assert (sell_fill.qty, sell_fill.price, sell_fill.commission) == (
        Decimal("1"),
        Decimal("104"),
        Commission(amount=Decimal("1"), asset="USDT"),
    )
    balance = _run(store.load_paper_balance_view(ACCOUNT_ID))
    assert balance is not None
    balances = {item.asset: item for item in balance.balances}
    assert (balances["USDT"].total, balances["USDT"].available, balances["USDT"].reserved) == (
        Decimal("998"), Decimal("998"), Decimal("0")
    )
    assert (balances["BTC"].total, balances["BTC"].available, balances["BTC"].reserved) == (
        Decimal("0"), Decimal("0"), Decimal("0")
    )
    assert _run(store.get_paper_position_lots(ACCOUNT_ID)) == []
    assert _run(store.load_paper_reservation("sell-order-1")) is None
    assert _counts(db) == {
        "paper_orders": 2,
        "paper_order_acceptances": 2,
        "paper_fill_evaluations": 2,
        "paper_fills": 2,
        "paper_fill_applications": 2,
        "paper_reservations": 0,
        "position_lots": 0,
        "paper_sell_accounting": 1,
    }

    assert _run(store.apply_lifecycle_command(_command("close-1", "close", 3))).state == "stopped"
    assert _counts(db)["paper_orders"] == 2
    assert _counts(db)["paper_fills"] == 2
    _run(store.close())

    reopened = SQLiteStore(db)
    _run(reopened.initialize())
    report = _run(reopened.reconcile(ACCOUNT_ID, SESSION_ID, "stopped"))
    assert report.consistent is True
    assert report.observed_state == "stopped"
    assert _run(reopened.get_lifecycle_state(ACCOUNT_ID, SESSION_ID)).state == "stopped"
    assert _run(reopened.apply_lifecycle_command(_command("close-1", "close", 3))).state == "stopped"

    retry_sell = _run(run_paper_cycle_sell(reopened, **_sell_kwargs()))
    assert retry_sell == sell
    retry_buy = _run(run_paper_cycle(reopened, **_buy_kwargs()))
    assert retry_buy.success is False
    assert _counts(db) == {
        "paper_orders": 2,
        "paper_order_acceptances": 2,
        "paper_fill_evaluations": 2,
        "paper_fills": 2,
        "paper_fill_applications": 2,
        "paper_reservations": 0,
        "position_lots": 0,
        "paper_sell_accounting": 1,
    }

    app = FastAPI()
    app.include_router(router)
    app.state._sqlite_store = reopened
    with TestClient(app) as client:
        base = f"/api/paper/accounts/{ACCOUNT_ID}"
        responses = {
            "balances": client.get(f"{base}/balances"),
            "positions": client.get(f"{base}/positions"),
            "orders": client.get(f"{base}/orders", params={"session_id": SESSION_ID}),
            "fills": client.get(f"{base}/fills", params={"session_id": SESSION_ID}),
            "lifecycle": client.get(f"{base}/lifecycle", params={"session_id": SESSION_ID}),
            "activity": client.get(f"{base}/activity", params={"session_id": SESSION_ID}),
        }
        assert all(response.status_code == 200 for response in responses.values())
        balances_json = responses["balances"].json()["balances"]
        balance_json = {item["asset"]: item for item in balances_json}
        assert balance_json["USDT"]["total"] == "998.0"
        assert balance_json["USDT"]["reserved"] == "0.0"
        assert responses["positions"].json()["positions"] == []
        assert [item["order_id"] for item in responses["orders"].json()["orders"]] == [
            "order-id-1",
            "sell-order-1",
        ]
        fills_json = responses["fills"].json()["fills"]
        assert len(fills_json) == 2
        assert all(item["commission"]["amount"] == "1" for item in fills_json)
        lifecycle_json = responses["lifecycle"].json()
        assert lifecycle_json["state"]["state"] == "stopped"
        assert lifecycle_json["reconciliation"]["consistent"] is True
        assert len(responses["activity"].json()["activity"]) == 4

    _run(reopened.close())
