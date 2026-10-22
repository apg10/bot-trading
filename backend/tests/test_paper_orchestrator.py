"""Focal tests for the bounded TEST_ONLY PAPER orchestrator."""

from __future__ import annotations

import asyncio
import json
import sqlite3
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.models.paper_contracts import AssetBalance
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
from src.models.paper_contracts import PaperAccountOpening, PaperOrderStatus
from src.market.paper_snapshot import PaperMarketExecutionSnapshot as EngineSnapshot
from src.persistence.sqlite_store import SQLiteStore
from src.paper_orchestrator import _validate_snapshot, run_paper_cycle, run_paper_cycle_sell
from src.strategies.test_only_keltner import TEST_ONLY_OHLCV_FIXTURE


_BASE_MS = (1_700_000_000_000 // 60_000) * 60_000  # 1699999980000 -> 2023-11-14T22:13:00Z


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
                    AssetBalance(asset="BTC", total=Decimal("0"), available=Decimal("0"), reserved=Decimal("0")),
                    AssetBalance(asset="USDT", total=Decimal("500"), available=Decimal("500"), reserved=Decimal("0")),
                ),
            )
        )
    )
    _run(store.initialize_lifecycle_state("acct-1", "session-1", 1))
    return store


def _risk(max_notional: str = "1000") -> PaperRiskPolicy:
    return PaperRiskPolicy(
        policy_id="test-risk", policy_version="v1", account_id="acct-1", quote_asset="USDT",
        max_order_notional=Decimal(max_notional), max_total_exposure=Decimal("1000"),
        max_daily_loss=Decimal("500"),
    )


def _risk_snapshot() -> PaperRiskSnapshot:
    return PaperRiskSnapshot(
        account_id="acct-1", quote_asset="USDT", market_symbol="BTC/USDT",
        market_mark_price=Decimal("104"), market_reference="TEST_ONLY", market_timestamp_ms=1,
        equity_reference="TEST_ONLY", equity_timestamp_ms=1,
        equity_utc_day_start=Decimal("500"), equity_current_marked=Decimal("500"),
        exposure_reference="TEST_ONLY", exposure_timestamp_ms=1,
        marked_open_inventory=Decimal("0"), remaining_buy_reservations=Decimal("0"),
    )


def _filters() -> SymbolFilterSnapshot:
    return SymbolFilterSnapshot(
        snapshot_id="test-filters", filter_set_id="test-set", filter_set_version="v1",
        source_id="TEST_ONLY", source_version="v1", capture_timestamp_ms=1,
        symbol="BTC/USDT", base_asset="BTC", quote_asset="USDT",
        price_filter=PriceFilter(min_price=Decimal("0"), max_price=Decimal("0"), tick_size=Decimal("0")),
        lot_size=LotSizeFilter(min_qty=Decimal("1"), max_qty=Decimal("10"), step_size=Decimal("1")),
        market_lot_size=MarketLotSizeFilter(min_qty=Decimal("1"), max_qty=Decimal("10"), step_size=Decimal("1")),
        min_notional=MinNotionalFilter(min_notional=Decimal("10"), applies_to_market=False, avg_price_mins=0),
        notional=None, unhandled_filter_types=(),
    )


def _market(bid=None, ask=None) -> PaperMarketExecutionSnapshot:
    return PaperMarketExecutionSnapshot(
        symbol="BTC/USDT",
        bid=Decimal(bid) if bid is not None else Decimal("103"),
        ask=Decimal(ask) if ask is not None else Decimal("104"),
        bar_open_time_ms=2000, prior_bar_open_time_ms=1000, interval_ms=60000,
        provenance="TEST_ONLY", version="v1", snapshot_timestamp_ms=2000,
    )


def _capacity() -> PaperMarketCapacitySnapshot:
    return PaperMarketCapacitySnapshot(
        source_id="TEST_ONLY", source_version="v1", capacity=Decimal("1"),
        symbol="BTC/USDT", capture_bar_open_time_ms=2000, capture_snapshot_timestamp_ms=2000,
    )


def _fee() -> PaperFeeSnapshot:
    from src.models.paper_contracts import Commission

    return PaperFeeSnapshot(
        fee_id="TEST_ONLY-fee", fee_version="v1", quantity=Decimal("1"),
        commission=Commission(amount=Decimal("1"), asset="USDT"),
    )


def _kwargs(candles=TEST_ONLY_OHLCV_FIXTURE[:7], market=None):
    return dict(
        account_id="acct-1",
        session_id="session-1",
        symbol="BTC/USDT",
        quantity=Decimal("1"),
        risk_policy=_risk(),
        risk_snapshot=_risk_snapshot(),
        symbol_filter_snapshot=_filters(),
        exec_policy_snapshot=PaperExecutionPolicy(
            policy_id="TEST_ONLY-exec", policy_version="v1", latency_slippage_mode="UNMODELLED"
        ),
        market_exec_snapshot=_market() if market is None else market,
        market_capacity_snapshot=_capacity(),
        fee_snapshot=_fee(),
        candles=candles,
        evaluated_at_ms=3000,
    )


def _sell_kwargs():
    return dict(
        account_id="acct-1",
        session_id="session-1",
        symbol="BTC/USDT",
        risk_policy=_risk(),
        risk_snapshot=_risk_snapshot(),
        symbol_filter_snapshot=_filters(),
        exec_policy_snapshot=PaperExecutionPolicy(
            policy_id="TEST_ONLY-exec", policy_version="v1", latency_slippage_mode="UNMODELLED"
        ),
        fee_snapshot=_fee(),
    )


class _FakeEngine:
    def __init__(self, snapshot: EngineSnapshot | None, *, engine_bar: str | None = None):
        self._paper_snapshot = snapshot
        self.config = SimpleNamespace(kline_interval="1m")
        self.state = SimpleNamespace(
            symbol="BTC/USDT",
            last_bar_open_time=_BASE_MS,
            entries_allowed=True,
            pending_gaps=0,
            stale=False,
            connected=True,
            complete=True,
            history_loaded=True,
        )
        self._engine_bar = engine_bar

    async def get_paper_snapshot(self):
        return self._paper_snapshot

    def snapshot(self):
        return {"bar_identity": self._engine_bar or self._paper_snapshot.bar_identity}

    def _clock_ms(self):
        return _BASE_MS + 30_000


def _engine_snapshot(**changes) -> EngineSnapshot:
    values = dict(
        symbol="BTC/USDT",
        bid=Decimal("99"),
        ask=Decimal("100"),
        bid_qty=Decimal("999"),
        ask_qty=Decimal("999"),
        timestamp=datetime.fromtimestamp(_BASE_MS / 1000 + 30, tz=timezone.utc),
        provenance="binance_public_book_ticker",
        version="v3",
        bar_identity=f"1m:{datetime.fromtimestamp(_BASE_MS / 1000, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}",
        freshness_ok=True,
        gap_ok=True,
        entries_allowed=True,
    )
    values.update(changes)
    return EngineSnapshot(**values)


def test_buy_success_wires_risk_filters_reservation_and_accounting(tmp_path: Path) -> None:
    db = tmp_path / "orchestrator.db"
    store = _store(db)
    result = _run(run_paper_cycle(store, **_kwargs()))
    assert result.success is True
    assert result.signal == "BUY"
    assert result.order_id == "order-id-1"
    assert result.fill_id is not None
    assert result.evaluation_id == "eval-cycle-1"
    assert result.risk_result == "risk_approved"
    assert result.filter_result == "symbol_filter_approved"
    assert _run(store.load_paper_order(result.order_id)).status == PaperOrderStatus.FILLED
    assert _run(store.load_paper_reservation(result.order_id)) is None
    balance_view = _run(store.load_paper_balance_view("acct-1"))
    assert balance_view is not None
    balances = {balance.asset: balance for balance in balance_view.balances}
    assert (balances["USDT"].total, balances["USDT"].available, balances["USDT"].reserved) == (
        Decimal("395"), Decimal("395"), Decimal("0")
    )
    assert (balances["BTC"].total, balances["BTC"].available, balances["BTC"].reserved) == (
        Decimal("1"), Decimal("1"), Decimal("0")
    )
    assert all(balance.total >= 0 for balance in balances.values())
    conn = sqlite3.connect(str(db))
    assert conn.execute("SELECT COUNT(*) FROM paper_orders").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM paper_order_acceptances").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM paper_fill_evaluations").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM paper_fills").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM paper_fill_applications").fetchone()[0] == 1
    conn.close()
    _run(store.close())


def test_hold_is_side_effect_free(tmp_path: Path) -> None:
    store = _store(tmp_path / "hold.db")
    result = _run(run_paper_cycle(store, **_kwargs(TEST_ONLY_OHLCV_FIXTURE[:6])))
    assert result.success is True
    assert result.signal == "HOLD"
    assert result.order_id is None
    assert _run(store.load_paper_order("order-id-1")) is None
    assert _run(store.load_paper_fill_evaluation("eval-cycle-1")) is None
    _run(store.close())


def test_risk_rejection_precedes_registration_and_balance_mutation(tmp_path: Path) -> None:
    store = _store(tmp_path / "risk-rejection.db")
    result = _run(run_paper_cycle(store, **{**_kwargs(), "risk_policy": _risk("10")}))
    assert result.success is False
    assert result.risk_result == "risk_order_size_limit"
    assert _run(store.load_paper_order("order-id-1")) is None
    balance_view = _run(store.load_paper_balance_view("acct-1"))
    assert balance_view is not None
    assert next(item for item in balance_view.balances if item.asset == "USDT").total == Decimal("500")
    _run(store.close())


def test_filter_rejection_precedes_registration_and_balance_mutation(tmp_path: Path) -> None:
    store = _store(tmp_path / "filter-rejection.db")
    result = _run(run_paper_cycle(store, **{**_kwargs(), "symbol_filter_snapshot": None}))
    assert result.success is False
    assert result.filter_result == "symbol_filter_missing"
    assert _run(store.load_paper_order("order-id-1")) is None
    balance_view = _run(store.load_paper_balance_view("acct-1"))
    assert balance_view is not None
    assert next(item for item in balance_view.balances if item.asset == "USDT").total == Decimal("500")
    _run(store.close())


def test_registration_acceptance_recovery_is_idempotent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db = tmp_path / "recovery.db"
    store = _store(db)
    original = store.accept_paper_order
    failed = False

    async def fail_once_after_registration(*args, **kwargs):
        nonlocal failed
        if not failed:
            failed = True
            raise OSError("synthetic failure after registration")
        return await original(*args, **kwargs)

    monkeypatch.setattr(store, "accept_paper_order", fail_once_after_registration)
    first = _run(run_paper_cycle(store, **_kwargs()))
    assert first.success is False
    assert _run(store.load_paper_order("order-id-1")) is not None
    monkeypatch.undo()
    second = _run(run_paper_cycle(store, **_kwargs()))
    assert second.success is True
    conn = sqlite3.connect(str(db))
    assert conn.execute("SELECT COUNT(*) FROM paper_orders").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM paper_order_acceptances").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM paper_reservations").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM paper_fills").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM paper_fill_applications").fetchone()[0] == 1
    conn.close()
    _run(store.close())


def test_paused_lifecycle_blocks_orchestrator_buy_preflight(tmp_path: Path) -> None:
    store = _store(tmp_path / "orchestrator-paused.db")
    _run(
        store.apply_lifecycle_command(
            PaperLifecycleCommand(
                command_id="orchestrator-pause",
                account_id="acct-1",
                paper_session_id="session-1",
                action="pause",
                issued_at_ms=2000,
            )
        )
    )
    result = _run(run_paper_cycle(store, **_kwargs()))
    assert result.success is False
    assert "lifecycle" in (result.error or "")
    assert _run(store.load_paper_order("order-id-1")) is None
    _run(store.close())


def test_stopped_lifecycle_blocks_orchestrator_buy_preflight(tmp_path: Path) -> None:
    store = _store(tmp_path / "orchestrator-stopped.db")
    _run(
        store.apply_lifecycle_command(
            PaperLifecycleCommand(
                command_id="orchestrator-stop", account_id="acct-1",
                paper_session_id="session-1", action="stop", issued_at_ms=2000,
            )
        )
    )
    _run(
        store.apply_lifecycle_command(
            PaperLifecycleCommand(
                command_id="orchestrator-close", account_id="acct-1",
                paper_session_id="session-1", action="close", issued_at_ms=3000,
            )
        )
    )
    result = _run(run_paper_cycle(store, **_kwargs()))
    assert result.success is False
    assert "lifecycle" in (result.error or "")
    assert _run(store.load_paper_order("order-id-1")) is None
    _run(store.close())


def test_complete_buy_to_sell_cycle_closes_position_and_accounts_proceeds(tmp_path: Path) -> None:
    db = tmp_path / "buy-sell.db"
    store = _store(db)
    buy_result = _run(
        run_paper_cycle(store, **_kwargs(market=_market(bid="99", ask="100")))
    )
    assert buy_result.success is True
    assert _run(store.load_paper_order("order-id-1")).status == PaperOrderStatus.FILLED
    conn = sqlite3.connect(str(db))
    assert conn.execute("SELECT COUNT(*) FROM position_lots").fetchone()[0] == 1
    lot_json = conn.execute("SELECT lot_json FROM position_lots").fetchone()[0]
    assert Decimal(json.loads(lot_json)["total_qty"]) == Decimal("1")
    assert Decimal(json.loads(lot_json)["total_cost"]) == Decimal("100")
    conn.close()

    sell_result = _run(run_paper_cycle_sell(store, **_sell_kwargs()))
    assert sell_result.success is True
    assert sell_result.signal == "SELL"
    assert sell_result.order_id == "sell-order-1"
    assert sell_result.evaluation_id == "eval-cycle-sell-1"
    assert sell_result.buy_status == "filled"
    assert sell_result.sell_status == "filled"
    assert _run(store.load_paper_order("sell-order-1")).status == PaperOrderStatus.FILLED
    assert _run(store.load_paper_reservation("sell-order-1")) is None

    balance_view = _run(store.load_paper_balance_view("acct-1"))
    assert balance_view is not None
    balances = {balance.asset: balance for balance in balance_view.balances}
    assert (balances["BTC"].total, balances["BTC"].available, balances["BTC"].reserved) == (
        Decimal("0"), Decimal("0"), Decimal("0")
    )
    assert (balances["USDT"].total, balances["USDT"].available, balances["USDT"].reserved) == (
        Decimal("502"), Decimal("502"), Decimal("0")
    )
    assert all(balance.total >= 0 for balance in balances.values())
    conn = sqlite3.connect(str(db))
    assert conn.execute("SELECT COUNT(*) FROM position_lots").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM paper_reservations").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM paper_orders").fetchone()[0] == 2
    assert conn.execute("SELECT COUNT(*) FROM paper_fills").fetchone()[0] == 2
    assert conn.execute("SELECT COUNT(*) FROM paper_fill_applications").fetchone()[0] == 2
    assert conn.execute("SELECT COUNT(*) FROM paper_fill_evaluations").fetchone()[0] == 2
    assert conn.execute("SELECT COUNT(*) FROM paper_sell_accounting").fetchone()[0] == 1
    conn.close()
    _run(store.close())


def test_sell_cycle_retry_is_idempotent(tmp_path: Path) -> None:
    db = tmp_path / "buy-sell-retry.db"
    store = _store(db)
    assert _run(
        run_paper_cycle(store, **_kwargs(market=_market(bid="99", ask="100")))
    ).success is True
    first = _run(run_paper_cycle_sell(store, **_sell_kwargs()))
    second = _run(run_paper_cycle_sell(store, **_sell_kwargs()))
    assert first.success is True
    assert second == first
    conn = sqlite3.connect(str(db))
    assert conn.execute("SELECT COUNT(*) FROM paper_orders").fetchone()[0] == 2
    assert conn.execute("SELECT COUNT(*) FROM paper_fills").fetchone()[0] == 2
    assert conn.execute("SELECT COUNT(*) FROM paper_fill_evaluations").fetchone()[0] == 2
    assert conn.execute("SELECT COUNT(*) FROM paper_fill_applications").fetchone()[0] == 2
    assert conn.execute("SELECT COUNT(*) FROM paper_sell_accounting").fetchone()[0] == 1
    conn.close()
    _run(store.close())


def test_sell_journal_failure_reopens_and_retries_exactly_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = tmp_path / "buy-sell-recovery.db"
    store = _store(db)
    assert _run(
        run_paper_cycle(store, **_kwargs(market=_market(bid="99", ask="100")))
    ).success is True
    original = store.save_paper_fill
    failed = False

    async def save_sell_then_fail(*args, **kwargs):
        nonlocal failed
        await original(*args, **kwargs)
        if not failed:
            failed = True
            raise OSError("synthetic SELL failure after journal save")

    monkeypatch.setattr(store, "save_paper_fill", save_sell_then_fail)
    first = _run(run_paper_cycle_sell(store, **_sell_kwargs()))
    assert first.success is False
    assert _run(store.load_paper_order("sell-order-1")).status == PaperOrderStatus.ACCEPTED
    _run(store.close())
    monkeypatch.undo()

    reopened = SQLiteStore(db)
    _run(reopened.initialize())
    recovered = _run(run_paper_cycle_sell(reopened, **_sell_kwargs()))
    assert recovered.success is True
    assert recovered.sell_status == "filled"
    assert _run(reopened.load_paper_order("sell-order-1")).status == PaperOrderStatus.FILLED
    balance_view = _run(reopened.load_paper_balance_view("acct-1"))
    assert balance_view is not None
    balances = {balance.asset: balance for balance in balance_view.balances}
    assert balances["USDT"].total == Decimal("502")
    assert balances["BTC"].total == Decimal("0")
    assert all(balance.total >= 0 for balance in balances.values())
    conn = sqlite3.connect(str(db))
    assert conn.execute("SELECT COUNT(*) FROM paper_orders").fetchone()[0] == 2
    assert conn.execute("SELECT COUNT(*) FROM paper_fills").fetchone()[0] == 2
    assert conn.execute("SELECT COUNT(*) FROM paper_fill_applications").fetchone()[0] == 2
    assert conn.execute("SELECT COUNT(*) FROM paper_sell_accounting").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM paper_reservations").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM position_lots").fetchone()[0] == 0
    conn.close()
    _run(reopened.close())


def test_buy_rejected_when_snapshot_is_none(tmp_path: Path) -> None:
    store = _store(tmp_path / "engine-none.db")
    result = _run(run_paper_cycle(store, **_kwargs(), engine=_FakeEngine(None)))
    assert result.success is False
    assert _run(store.load_paper_order("order-id-1")) is None
    _run(store.close())


def test_buy_rejected_on_context_change(tmp_path: Path) -> None:
    store = _store(tmp_path / "engine-context.db")
    snap = _engine_snapshot()
    engine = _FakeEngine(
        snap,
        engine_bar=f"1m:{datetime.fromtimestamp((_BASE_MS + 60_000) / 1000, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}",
    )
    result = _run(run_paper_cycle(store, **_kwargs(), engine=engine))
    assert result.success is False
    assert _run(store.load_paper_order("order-id-1")) is None
    _run(store.close())


def test_buy_rejected_on_missing_state_gate(tmp_path: Path) -> None:
    store = _store(tmp_path / "engine-missing-gate.db")
    engine = _FakeEngine(_engine_snapshot())
    del engine.state.connected
    result = _run(run_paper_cycle(store, **_kwargs(), engine=engine))
    assert result.success is False
    assert _run(store.load_paper_order("order-id-1")) is None
    _run(store.close())


def test_buy_rejected_on_reconnecting(tmp_path: Path) -> None:
    store = _store(tmp_path / "engine-reconnecting.db")
    engine = _FakeEngine(_engine_snapshot())
    engine.state.reconnecting = True
    result = _run(run_paper_cycle(store, **_kwargs(), engine=engine))
    assert result.success is False
    assert _run(store.load_paper_order("order-id-1")) is None
    _run(store.close())


def test_buy_rejected_on_pause_reconciliation(tmp_path: Path) -> None:
    store = _store(tmp_path / "engine-pause.db")
    _run(store.apply_lifecycle_command(PaperLifecycleCommand(
        command_id="pause-engine", account_id="acct-1", paper_session_id="session-1",
        action="pause", issued_at_ms=2000,
    )))
    result = _run(run_paper_cycle(
        store, **_kwargs(), engine=_FakeEngine(_engine_snapshot())
    ))
    assert result.success is False
    assert _run(store.load_paper_order("order-id-1")) is None
    _run(store.close())


def test_buy_accepts_valid_snapshot(tmp_path: Path) -> None:
    db = tmp_path / "engine-buy.db"
    store = _store(db)
    result = _run(run_paper_cycle(
        store, **_kwargs(), engine=_FakeEngine(_engine_snapshot())
    ))
    assert result.success is True
    record = _run(store.load_paper_fill_evaluation("eval-cycle-1"))
    assert record is not None
    assert json.loads(record.result_json)["fill_price"] == "100"
    _run(store.close())


def test_sell_accepts_valid_snapshot(tmp_path: Path) -> None:
    store = _store(tmp_path / "engine-sell.db")
    assert _run(run_paper_cycle(store, **_kwargs())).success is True
    result = _run(run_paper_cycle_sell(
        store, **_sell_kwargs(), engine=_FakeEngine(_engine_snapshot())
    ))
    assert result.success is True
    record = _run(store.load_paper_fill_evaluation("eval-cycle-sell-1"))
    assert record is not None
    assert json.loads(record.result_json)["fill_price"] == "99"
    _run(store.close())


def test_quote_qty_not_capacity(tmp_path: Path) -> None:
    store = _store(tmp_path / "engine-capacity.db")
    result = _run(run_paper_cycle(
        store, **_kwargs(), engine=_FakeEngine(_engine_snapshot())
    ))
    assert result.success is True
    record = _run(store.load_paper_fill_evaluation("eval-cycle-1"))
    assert record is not None
    payload = json.loads(record.result_json)
    assert payload["capacity_source_id"] == "TEST_ONLY"
    assert payload["fill_qty"] == "1"
    _run(store.close())


def test_retry_preserves_fill_id(tmp_path: Path) -> None:
    store = _store(tmp_path / "engine-retry.db")
    first = _run(run_paper_cycle(store, **_kwargs()))
    second = _run(run_paper_cycle(store, **_kwargs()))
    assert first.success is True
    assert second.success is True
    assert second.fill_id == first.fill_id
    _run(store.close())


def test_provenance_retained_in_evaluation(tmp_path: Path) -> None:
    store = _store(tmp_path / "engine-provenance.db")
    assert _run(run_paper_cycle(
        store, **_kwargs(), engine=_FakeEngine(_engine_snapshot())
    )).success is True
    record = _run(store.load_paper_fill_evaluation("eval-cycle-1"))
    assert record is not None
    assert "binance_public_book_ticker" in json.loads(record.result_json)["market_reference"]
    _run(store.close())


def test_existing_test_only_still_works_without_engine(tmp_path: Path) -> None:
    store = _store(tmp_path / "engine-test-only.db")
    result = _run(run_paper_cycle(store, **_kwargs()))
    assert result.success is True
    _run(store.close())
