"""Tests for explicit PAPER pause/resume state and the BUY admission gate."""

from __future__ import annotations

import asyncio
import sqlite3
from decimal import Decimal
from pathlib import Path

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
    StrategyRef,
)
from src.models.paper_execution import PaperExecutionPolicy
from src.models.paper_lifecycle import (
    PaperLifecycleCommand,
    PaperLifecycleEvent,
    PaperLifecycleReconciliationReport,
    PaperLifecycleState,
)
from src.models.paper_risk import PaperRiskPolicy, PaperRiskSnapshot
from src.models.paper_symbol_filters import (
    LotSizeFilter,
    MarketLotSizeFilter,
    MinNotionalFilter,
    PriceFilter,
    SymbolFilterSnapshot,
)
from src.persistence.sqlite_store import (
    PaperLifecycleCommandConflictError,
    PaperLifecycleStateError,
    PaperOrderAcceptanceStateError,
    SQLiteStore,
)


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _store(db: Path, *, lifecycle: bool = True) -> SQLiteStore:
    store = SQLiteStore(db)
    _run(store.initialize())
    _run(
        store.open_paper_account(
            PaperAccountOpening(
                environment="paper",
                account_id="acct-lifecycle",
                balances=(
                    AssetBalance(asset="BTC", total=Decimal("0"), available=Decimal("0"), reserved=Decimal("0")),
                    AssetBalance(asset="USDT", total=Decimal("500"), available=Decimal("500"), reserved=Decimal("0")),
                ),
            )
        )
    )
    if lifecycle:
        _run(store.initialize_lifecycle_state("acct-lifecycle", "session-lifecycle", 1000))
    return store


def _command(command_id: str, action: str, timestamp: int = 2000) -> PaperLifecycleCommand:
    return PaperLifecycleCommand(
        command_id=command_id,
        account_id="acct-lifecycle",
        paper_session_id="session-lifecycle",
        action=action,
        issued_at_ms=timestamp,
    )


def _order(order_id: str = "buy-lifecycle") -> PaperOrder:
    return PaperOrder(
        order_id=order_id,
        client_order_id=f"client-{order_id}",
        paper_account_id="acct-lifecycle",
        paper_session_id="session-lifecycle",
        environment="paper",
        symbol="BTC/USDT",
        side=PaperOrderSide.BUY,
        order_type=PaperOrderType.LIMIT,
        requested_qty=Decimal("1"),
        price=Decimal("100"),
        strategy_ref=StrategyRef(strategy_id="lifecycle-strategy", strategy_version="v1"),
        status=PaperOrderStatus.PREPARED,
        timestamp_ms=2000,
    )


def _risk() -> tuple[PaperRiskPolicy, PaperRiskSnapshot]:
    return (
        PaperRiskPolicy(
            policy_id="lifecycle-risk", policy_version="v1", account_id="acct-lifecycle", quote_asset="USDT",
            max_order_notional=Decimal("1000"), max_total_exposure=Decimal("1000"), max_daily_loss=Decimal("500"),
        ),
        PaperRiskSnapshot(
            account_id="acct-lifecycle", quote_asset="USDT", market_symbol="BTC/USDT",
            market_mark_price=Decimal("100"), market_reference="TEST_ONLY", market_timestamp_ms=1,
            equity_reference="TEST_ONLY", equity_timestamp_ms=1,
            equity_utc_day_start=Decimal("500"), equity_current_marked=Decimal("500"),
            exposure_reference="TEST_ONLY", exposure_timestamp_ms=1,
            marked_open_inventory=Decimal("0"), remaining_buy_reservations=Decimal("0"),
        ),
    )


def _filters() -> SymbolFilterSnapshot:
    return SymbolFilterSnapshot(
        snapshot_id="lifecycle-filters", filter_set_id="lifecycle-set", filter_set_version="v1",
        source_id="TEST_ONLY", source_version="v1", capture_timestamp_ms=1,
        symbol="BTC/USDT", base_asset="BTC", quote_asset="USDT",
        price_filter=PriceFilter(min_price=Decimal("0"), max_price=Decimal("0"), tick_size=Decimal("0")),
        lot_size=LotSizeFilter(min_qty=Decimal("1"), max_qty=Decimal("10"), step_size=Decimal("1")),
        market_lot_size=MarketLotSizeFilter(min_qty=Decimal("1"), max_qty=Decimal("10"), step_size=Decimal("1")),
        min_notional=MinNotionalFilter(min_notional=Decimal("1"), applies_to_market=False, avg_price_mins=0),
        notional=None, unhandled_filter_types=(),
    )


def _accept(store: SQLiteStore, order: PaperOrder) -> PaperOrder:
    _run(store.register_paper_order(order))
    risk, snapshot = _risk()
    return _run(store.accept_paper_order(order.order_id, risk, snapshot, _filters()))


def test_lifecycle_initialization_pause_resume_survives_reopen(tmp_path: Path) -> None:
    db = tmp_path / "lifecycle.db"
    store = _store(db)
    paused = _run(store.apply_lifecycle_command(_command("pause-1", "pause")))
    assert paused.state == "paused"
    _run(store.close())
    reopened = SQLiteStore(db)
    _run(reopened.initialize())
    assert _run(reopened.get_lifecycle_state("acct-lifecycle", "session-lifecycle")).state == "paused"
    resumed = _run(reopened.apply_lifecycle_command(_command("resume-1", "resume", 3000)))
    assert resumed.state == "running"
    assert _run(reopened.is_buy_allowed("acct-lifecycle", "session-lifecycle")) is True
    _run(reopened.close())


def test_same_command_retry_is_idempotent_and_old_pause_does_not_reapply(tmp_path: Path) -> None:
    store = _store(tmp_path / "idempotent.db")
    command = _command("pause-idempotent", "pause")
    first = _run(store.apply_lifecycle_command(command))
    _run(store.apply_lifecycle_command(_command("resume-idempotent", "resume", 3000)))
    retry = _run(store.apply_lifecycle_command(command))
    assert first.state == "paused"
    assert retry.state == "running"
    conn = sqlite3.connect(str(tmp_path / "idempotent.db"))
    assert conn.execute("SELECT COUNT(*) FROM paper_lifecycle_events WHERE command_id=?", (command.command_id,)).fetchone()[0] == 1
    conn.close()
    _run(store.close())


def test_conflicting_command_payload_is_rejected(tmp_path: Path) -> None:
    store = _store(tmp_path / "conflict.db")
    _run(store.apply_lifecycle_command(_command("same-command", "pause")))
    with pytest.raises(PaperLifecycleCommandConflictError):
        _run(store.apply_lifecycle_command(_command("same-command", "resume")))
    _run(store.close())


def test_missing_state_blocks_buy_without_inference(tmp_path: Path) -> None:
    store = _store(tmp_path / "missing.db", lifecycle=False)
    with pytest.raises(PaperOrderAcceptanceStateError, match="initialized lifecycle"):
        _accept(store, _order())
    assert _run(store.is_buy_allowed("acct-lifecycle", "session-lifecycle")) is False
    assert _run(store.load_paper_order("buy-lifecycle")).status == PaperOrderStatus.PREPARED
    _run(store.close())


def test_paused_blocks_buy_without_acceptance_or_reservation(tmp_path: Path) -> None:
    db = tmp_path / "paused.db"
    store = _store(db)
    _run(store.apply_lifecycle_command(_command("pause", "pause")))
    with pytest.raises(PaperOrderAcceptanceStateError, match="paused"):
        _accept(store, _order())
    assert _run(store.load_paper_reservation("buy-lifecycle")) is None
    conn = sqlite3.connect(str(db))
    assert conn.execute("SELECT COUNT(*) FROM paper_order_acceptances").fetchone()[0] == 0
    conn.close()
    _run(store.close())


def test_accepted_buy_remains_manageable_after_pause(tmp_path: Path) -> None:
    store = _store(tmp_path / "accepted.db")
    accepted = _accept(store, _order())
    _run(store.apply_lifecycle_command(_command("pause", "pause")))
    cancelled = _run(store.cancel_paper_order(accepted.order_id))
    assert cancelled.status == PaperOrderStatus.CANCELLED
    _run(store.close())


def test_sell_path_remains_operational_while_paused(tmp_path: Path) -> None:
    store = _store(tmp_path / "sell-paused.db")
    buy = _accept(store, _order())
    fill = PaperFill(
        fill_id="buy-fill", order_id=buy.order_id, qty=Decimal("1"), price=Decimal("100"),
        timestamp_ms=3000, commission=Commission(amount=Decimal("0"), asset="USDT"),
    )
    _run(store.save_paper_fill(fill))
    _run(store.apply_paper_fill(fill.fill_id))
    _run(store.apply_lifecycle_command(_command("pause", "pause")))
    sell = PaperOrder(
        order_id="sell-lifecycle", client_order_id="client-sell-lifecycle",
        paper_account_id="acct-lifecycle", paper_session_id="session-lifecycle",
        environment="paper", symbol="BTC/USDT", side=PaperOrderSide.SELL,
        order_type=PaperOrderType.LIMIT, requested_qty=Decimal("1"), price=Decimal("101"),
        strategy_ref=buy.strategy_ref, status=PaperOrderStatus.PREPARED, timestamp_ms=4000,
        lot_allocations=(LotAllocation(position_id=buy.order_id, account_id=buy.paper_account_id, symbol=buy.symbol, strategy_ref=buy.strategy_ref, qty=Decimal("1")),),
    )
    _run(store.register_paper_order(sell))
    assert _run(store.accept_paper_order(sell.order_id)).status == PaperOrderStatus.ACCEPTED
    _run(store.close())


def test_buy_accepted_before_pause_remains_fillable(tmp_path: Path) -> None:
    store = _store(tmp_path / "fill-after-pause.db")
    buy = _accept(store, _order())
    _run(store.apply_lifecycle_command(_command("pause", "pause")))
    fill = PaperFill(
        fill_id="fill-after-pause", order_id=buy.order_id, qty=Decimal("1"), price=Decimal("100"),
        timestamp_ms=3000, commission=Commission(amount=Decimal("0"), asset="USDT"),
    )
    _run(store.save_paper_fill(fill))
    assert _run(store.apply_paper_fill(fill.fill_id)).fill_id == fill.fill_id
    _run(store.close())


def test_pause_then_buy_acceptance_is_serially_rejected(tmp_path: Path) -> None:
    store = _store(tmp_path / "serialized.db")
    _run(store.apply_lifecycle_command(_command("pause", "pause")))
    with pytest.raises(PaperOrderAcceptanceStateError):
        _accept(store, _order())
    assert _run(store.is_buy_allowed("acct-lifecycle", "session-lifecycle")) is False
    _run(store.close())


def test_v12_to_v14_migration_adds_empty_lifecycle_tables(tmp_path: Path) -> None:
    db = tmp_path / "migration.db"
    store = SQLiteStore(db)
    _run(store.initialize())
    _run(store.close())
    conn = sqlite3.connect(str(db))
    conn.execute("PRAGMA user_version = 12")
    conn.commit()
    conn.close()
    reopened = SQLiteStore(db)
    _run(reopened.initialize())
    conn = sqlite3.connect(str(db))
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 15
    assert conn.execute("SELECT COUNT(*) FROM paper_lifecycle_states").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM paper_lifecycle_events").fetchone()[0] == 0
    conn.close()
    assert _run(reopened.get_lifecycle_state("acct-lifecycle", "session-lifecycle")) is None
    _run(reopened.close())


def test_lifecycle_dtos_are_frozen_and_validated() -> None:
    state = PaperLifecycleState(
        account_id="a", paper_session_id="s", state="running",
        last_command_id=None, last_updated_ms=0,
    )
    with pytest.raises((TypeError, ValueError)):
        state.state = "paused"
    with pytest.raises(Exception):
        PaperLifecycleCommand(command_id="c", account_id="a", paper_session_id="s", action="bogus", issued_at_ms=0)


def test_stop_transitions_running_to_paused_and_is_idempotent(tmp_path: Path) -> None:
    store = _store(tmp_path / "stop.db")
    first = _run(store.apply_lifecycle_command(_command("stop-1", "stop")))
    second = _run(store.apply_lifecycle_command(_command("stop-2", "stop", 3000)))
    assert first.state == second.state == "paused"
    conn = sqlite3.connect(str(tmp_path / "stop.db"))
    rows = conn.execute(
        "SELECT action, transitioned FROM paper_lifecycle_events ORDER BY event_id"
    ).fetchall()
    conn.close()
    assert rows == [("stop", 1), ("stop", 0)]
    _run(store.close())


def test_close_from_running_is_rejected_without_mutation(tmp_path: Path) -> None:
    db = tmp_path / "close-running.db"
    store = _store(db)
    with pytest.raises(PaperLifecycleStateError, match="running"):
        _run(store.apply_lifecycle_command(_command("close-1", "close")))
    assert _run(store.get_lifecycle_state("acct-lifecycle", "session-lifecycle")).state == "running"
    conn = sqlite3.connect(str(db))
    assert conn.execute("SELECT COUNT(*) FROM paper_lifecycle_events").fetchone()[0] == 0
    conn.close()
    _run(store.close())


def test_close_rejects_open_position_quantity(tmp_path: Path) -> None:
    store = _store(tmp_path / "close-position.db")
    buy = _accept(store, _order())
    fill = PaperFill(
        fill_id="close-position-fill", order_id=buy.order_id, qty=Decimal("1"),
        price=Decimal("100"), timestamp_ms=3000,
        commission=Commission(amount=Decimal("0"), asset="USDT"),
    )
    _run(store.save_paper_fill(fill))
    _run(store.apply_paper_fill(fill.fill_id))
    _run(store.apply_lifecycle_command(_command("close-position-pause", "pause")))
    with pytest.raises(PaperLifecycleStateError, match="position"):
        _run(store.apply_lifecycle_command(_command("close-position", "close")))
    assert _run(store.get_lifecycle_state("acct-lifecycle", "session-lifecycle")).state == "paused"
    _run(store.close())


def test_close_rejects_nonterminal_order(tmp_path: Path) -> None:
    store = _store(tmp_path / "close-order.db")
    _run(store.register_paper_order(_order()))
    _run(store.apply_lifecycle_command(_command("close-order-pause", "pause")))
    with pytest.raises(PaperLifecycleStateError, match="nonterminal"):
        _run(store.apply_lifecycle_command(_command("close-order", "close")))
    _run(store.close())


def test_close_rejects_active_order_reservation(tmp_path: Path) -> None:
    db = tmp_path / "close-reservation.db"
    store = _store(db)
    order = _accept(store, _order())
    # Leave the reservation active while making the indexed order terminal;
    # this isolates the reservation precondition from the order precondition.
    terminal = order.model_copy(update={"status": PaperOrderStatus.REJECTED})
    conn = sqlite3.connect(str(db))
    conn.execute(
        "UPDATE paper_orders SET order_json = ? WHERE order_id = ?",
        (terminal.model_dump_json(), order.order_id),
    )
    conn.commit()
    conn.close()
    _run(store.apply_lifecycle_command(_command("close-reservation-pause", "pause")))
    with pytest.raises(PaperLifecycleStateError, match="reservation"):
        _run(store.apply_lifecycle_command(_command("close-reservation", "close")))
    _run(store.close())


def test_close_transitions_paused_to_stopped_without_side_effects(tmp_path: Path) -> None:
    db = tmp_path / "close-clean.db"
    store = _store(db)
    _run(store.apply_lifecycle_command(_command("close-clean-pause", "pause")))
    before_conn = sqlite3.connect(str(db))
    before = before_conn.execute("SELECT COUNT(*) FROM paper_orders").fetchone()[0]
    before_conn.close()
    closed = _run(store.apply_lifecycle_command(_command("close-clean", "close")))
    assert closed.state == "stopped"
    conn = sqlite3.connect(str(db))
    assert conn.execute("SELECT COUNT(*) FROM paper_orders").fetchone()[0] == before
    assert conn.execute("SELECT COUNT(*) FROM paper_fills").fetchone()[0] == 0
    conn.close()
    _run(store.close())


def test_close_does_not_create_liquidation_orders_or_fills(tmp_path: Path) -> None:
    db = tmp_path / "close-no-liquidation.db"
    store = _store(db)
    _run(store.apply_lifecycle_command(_command("close-no-liquidation-pause", "pause")))
    _run(store.apply_lifecycle_command(_command("close-no-liquidation", "close")))
    conn = sqlite3.connect(str(db))
    assert conn.execute("SELECT COUNT(*) FROM paper_orders").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM paper_fills").fetchone()[0] == 0
    conn.close()
    _run(store.close())


def test_resume_from_stopped_is_rejected(tmp_path: Path) -> None:
    store = _store(tmp_path / "resume-stopped.db")
    _run(store.apply_lifecycle_command(_command("resume-stopped-pause", "pause")))
    _run(store.apply_lifecycle_command(_command("resume-stopped-close", "close")))
    with pytest.raises(PaperLifecycleStateError, match="stopped"):
        _run(store.apply_lifecycle_command(_command("resume-stopped", "resume")))
    _run(store.close())


def test_reinitialize_does_not_reset_stopped(tmp_path: Path) -> None:
    store = _store(tmp_path / "reinitialize-stopped.db")
    _run(store.apply_lifecycle_command(_command("reinit-pause", "pause")))
    _run(store.apply_lifecycle_command(_command("reinit-close", "close")))
    state = _run(store.initialize_lifecycle_state("acct-lifecycle", "session-lifecycle", 9999))
    assert state.state == "stopped"
    _run(store.close())


def test_repeated_close_from_stopped_is_idempotent(tmp_path: Path) -> None:
    store = _store(tmp_path / "repeated-close.db")
    _run(store.apply_lifecycle_command(_command("repeat-pause", "pause")))
    first = _run(store.apply_lifecycle_command(_command("repeat-close-1", "close")))
    second = _run(store.apply_lifecycle_command(_command("repeat-close-2", "close", 3000)))
    assert first.state == second.state == "stopped"
    _run(store.close())


def test_stopped_preserves_cancellation_and_sell_manageability(tmp_path: Path) -> None:
    store = _store(tmp_path / "stopped-management.db")
    buy = _accept(store, _order())
    _run(store.apply_lifecycle_command(_command("management-stop", "stop")))
    cancelled = _run(store.cancel_paper_order(buy.order_id))
    assert cancelled.status == PaperOrderStatus.CANCELLED
    closed = _run(store.apply_lifecycle_command(_command("management-close", "close")))
    assert closed.state == "stopped"
    _run(store.close())


def test_lifecycle_dtos_accept_stop_close_and_stopped() -> None:
    command_stop = PaperLifecycleCommand(
        command_id="stop", account_id="a", paper_session_id="s",
        action="stop", issued_at_ms=0,
    )
    command_close = command_stop.model_copy(update={"command_id": "close", "action": "close"})
    state = PaperLifecycleState(
        account_id="a", paper_session_id="s", state="stopped",
        last_command_id="close", last_updated_ms=0,
    )
    event = PaperLifecycleEvent(
        event_id=None, command_id="close", account_id="a", paper_session_id="s",
        action="close", previous_state="paused", resulting_state="stopped",
        transitioned=True, timestamp_ms=0,
    )
    assert command_stop.action == "stop"
    assert command_close.action == "close"
    assert state.state == event.resulting_state == "stopped"


def test_reconciliation_gate_runs_inside_lifecycle_command_transaction(tmp_path: Path) -> None:
    """A reconcile command persists its report in the command transaction."""
    store = _store(tmp_path / "reconcile-command.db")
    command = _command("reconcile-command", "reconcile")
    result = _run(store.apply_lifecycle_command(command))
    assert result.state == "running"
    conn = sqlite3.connect(str(tmp_path / "reconcile-command.db"))
    report_json = conn.execute(
        "SELECT report_json FROM paper_lifecycle_reconciliations"
    ).fetchone()[0]
    conn.close()
    report = PaperLifecycleReconciliationReport.model_validate_json(report_json)
    assert report.consistent is True


def test_reconcile_does_not_change_lifecycle_state(tmp_path: Path) -> None:
    """Reconcile command does not change the lifecycle state."""
    store = _store(tmp_path / "reconcile-state.db")
    state_before = _run(store.get_lifecycle_state("acct-lifecycle", "session-lifecycle"))
    _run(store.apply_lifecycle_command(_command("reconcile-state", "reconcile")))
    state_after = _run(store.get_lifecycle_state("acct-lifecycle", "session-lifecycle"))
    assert state_before.state == state_after.state == "running"


def test_close_checks_fresh_reconciliation_report(tmp_path: Path) -> None:
    """CLOSE checks a fresh reconciliation report before proceeding."""
    store = _store(tmp_path / "close-fresh-reconciliation.db")
    _run(store.apply_lifecycle_command(_command("close-fresh-pause", "pause")))
    result = _run(store.apply_lifecycle_command(_command("close-fresh", "close")))
    assert result.state == "stopped"
