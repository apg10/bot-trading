"""Tests for lifecycle reconciliation reports and discrepancy gates."""

from __future__ import annotations

import asyncio
import sqlite3
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from src.models.paper_contracts import (
    AssetBalance,
    PaperAccountOpening,
    PaperOrder,
    PaperOrderSide,
    PaperOrderStatus,
    PaperOrderType,
    PaperReservationRef,
    ReservationAmount,
    StrategyRef,
)
from src.models.paper_lifecycle import (
    PaperLifecycleCommand,
    PaperLifecycleReconciliationReport,
)
from src.models.paper_risk import PaperRiskPolicy, PaperRiskSnapshot
from src.models.paper_symbol_filters import (
    LotSizeFilter,
    MarketLotSizeFilter,
    MinNotionalFilter,
    PriceFilter,
    SymbolFilterSnapshot,
)
from src.persistence import sqlite_store as sqlite_store_module
from src.persistence.sqlite_store import (
    PaperLifecycleReconciliationError,
    SQLiteStore,
)


def _run(coro: Any) -> Any:
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _make_store_with_lifecycle(
    tmp_path: Path, account_id: str = "acct-rec", session_id: str = "sess-rec"
) -> SQLiteStore:
    """Create a fresh store with lifecycle initialized to RUNNING."""
    db = tmp_path / "rec.db"
    store = SQLiteStore(db)
    _run(store.initialize())
    opening = PaperAccountOpening(
        environment="paper",
        account_id=account_id,
        balances=(
            AssetBalance(
                asset="BTC", total=Decimal("0"), available=Decimal("0"), reserved=Decimal("0")
            ),
            AssetBalance(
                asset="USDT",
                total=Decimal("10000"),
                available=Decimal("10000"),
                reserved=Decimal("0"),
            ),
        ),
    )
    _run(store.open_paper_account(opening))
    _run(store.initialize_lifecycle_state(account_id, session_id, 10000))
    return store


def _command(action: str, command_id: str, account: str = "acct-rec", session: str = "sess-rec") -> PaperLifecycleCommand:
    return PaperLifecycleCommand(
        command_id=command_id,
        account_id=account,
        paper_session_id=session,
        action=action,
        issued_at_ms=20000,
    )


def _order(order_id: str = "reconciliation-order", status: PaperOrderStatus = PaperOrderStatus.PREPARED) -> PaperOrder:
    return PaperOrder(
        order_id=order_id,
        client_order_id=f"client-{order_id}",
        paper_account_id="acct-rec",
        paper_session_id="sess-rec",
        environment="paper",
        symbol="BTC/USDT",
        side=PaperOrderSide.BUY,
        order_type=PaperOrderType.LIMIT,
        requested_qty=Decimal("1"),
        price=Decimal("100"),
        strategy_ref=StrategyRef(strategy_id="reconciliation", strategy_version="v1"),
        status=status,
        timestamp_ms=10000,
    )


def _table_names(db: Path) -> set[str]:
    conn = sqlite3.connect(str(db))
    try:
        return {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    finally:
        conn.close()


def _insert_malformed_order(db: Path, order_id: str = "malformed-order") -> None:
    conn = sqlite3.connect(str(db))
    conn.execute(
        "INSERT INTO paper_orders "
        "(order_id, paper_account_id, paper_session_id, environment, client_order_id, order_json) "
        "VALUES (?, ?, ?, 'paper', ?, ?)",
        (order_id, "acct-rec", "sess-rec", f"client-{order_id}", "not-json"),
    )
    conn.commit()
    conn.close()


def test_clean_reconciliation_report_is_typed_immutable_and_persisted(tmp_path: Path) -> None:
    store = _make_store_with_lifecycle(tmp_path)
    report = _run(store.reconcile("acct-rec", "sess-rec", "running"))
    assert isinstance(report, PaperLifecycleReconciliationReport)
    assert report.consistent is True
    assert report.discrepancies == ()
    with pytest.raises((TypeError, ValueError)):
        report.consistent = False
    conn = sqlite3.connect(str(store._db_path))
    rows = conn.execute(
        "SELECT report_id, account_id, paper_session_id FROM paper_lifecycle_reconciliations"
    ).fetchall()
    conn.close()
    assert rows == [(report.report_id, "acct-rec", "sess-rec")]


def test_expected_state_mismatch_is_reported_as_discrepancy(tmp_path: Path) -> None:
    store = _make_store_with_lifecycle(tmp_path)
    report = _run(store.reconcile("acct-rec", "sess-rec", "paused"))
    assert report.consistent is False
    assert any("expected lifecycle state" in item for item in report.discrepancies)


def test_reconciliation_detects_order_scope_mismatch(tmp_path: Path) -> None:
    store = _make_store_with_lifecycle(tmp_path)
    order = _order()
    _run(store.register_paper_order(order))
    conn = sqlite3.connect(str(store._db_path))
    mismatched = order.model_copy(update={"paper_account_id": "other-account"})
    conn.execute("UPDATE paper_orders SET order_json=? WHERE order_id=?", (mismatched.model_dump_json(), order.order_id))
    conn.commit()
    conn.close()
    report = _run(store.check_lifecycle_reconciliation("acct-rec", "sess-rec"))
    assert report.consistent is False
    assert any("indexed scope" in item for item in report.discrepancies)


def test_reconciliation_detects_reservation_for_terminal_order(tmp_path: Path) -> None:
    store = _make_store_with_lifecycle(tmp_path)
    order = _order(status=PaperOrderStatus.PREPARED)
    _run(store.register_paper_order(order))
    # Manually mark it terminal so reconciliation detects the reservation issue
    conn = sqlite3.connect(str(store._db_path))
    row = conn.execute("SELECT order_json FROM paper_orders WHERE order_id=?", (order.order_id,)).fetchone()
    import json
    order_data = json.loads(row[0])
    order_data["status"] = "rejected"
    conn.execute("UPDATE paper_orders SET order_json=? WHERE order_id=?", (json.dumps(order_data), order.order_id))
    conn.commit()
    conn.close()
    reservation = PaperReservationRef(
        reservation_id="terminal-reservation",
        owner_order_id=order.order_id,
        amount=ReservationAmount(asset="USDT", amount=Decimal("100")),
    )
    conn = sqlite3.connect(str(store._db_path))
    conn.execute(
        "INSERT INTO paper_order_acceptances "
        "(order_id, reservation_id, paper_account_id, paper_session_id, environment, "
        "client_order_id, initial_order_json, acceptance_payload_json, reservation_json) "
        "VALUES (?, ?, ?, ?, 'paper', ?, ?, '{}', ?)",
        (order.order_id, reservation.reservation_id, order.paper_account_id, order.paper_session_id,
         order.client_order_id, order.model_dump_json(), reservation.model_dump_json()),
    )
    conn.execute(
        "INSERT INTO paper_reservations "
        "(reservation_id, owner_order_id, account_id, paper_session_id, environment, asset, amount_json, reservation_json) "
        "VALUES (?, ?, ?, ?, 'paper', ?, ?, ?)",
        (reservation.reservation_id, order.order_id, order.paper_account_id, order.paper_session_id,
         "USDT", reservation.amount.model_dump_json(), reservation.model_dump_json()),
    )
    conn.commit()
    conn.close()
    report = _run(store.check_lifecycle_reconciliation("acct-rec", "sess-rec"))
    assert report.consistent is False
    assert any("ACCEPTED/PARTIALLY_FILLED" in item for item in report.discrepancies)


def test_reconciliation_detects_invalid_position_identity_or_quantity(tmp_path: Path) -> None:
    store = _make_store_with_lifecycle(tmp_path)
    conn = sqlite3.connect(str(store._db_path))
    conn.execute(
        "INSERT INTO position_lots (order_id, account_id, paper_session_id, environment, lot_json) "
        "VALUES ('bad-lot', 'acct-rec', 'sess-rec', 'paper', ?)",
        ('{"order_id":"bad-lot","account_id":"acct-rec","paper_session_id":"sess-rec",'
         '"environment":"paper","total_qty":"-1","total_cost":"0"}',),
    )
    conn.commit()
    conn.close()
    report = _run(store.check_lifecycle_reconciliation("acct-rec", "sess-rec"))
    assert report.consistent is False
    assert any("position lot" in item for item in report.discrepancies)


def test_open_positions_are_normal_while_running_or_paused(tmp_path: Path) -> None:
    store = _make_store_with_lifecycle(tmp_path)
    report = _run(store.check_lifecycle_reconciliation("acct-rec", "sess-rec"))
    assert report.consistent is True
    _run(store.apply_lifecycle_command(_command("pause", "pause-open")))
    report = _run(store.check_lifecycle_reconciliation("acct-rec", "sess-rec"))
    assert report.consistent is True


def test_stopped_requires_no_positions_or_active_reservations(tmp_path: Path) -> None:
    store = _make_store_with_lifecycle(tmp_path)
    _run(store.apply_lifecycle_command(_command("stop", "stop-1")))
    _run(store.apply_lifecycle_command(_command("close", "close-1")))
    report = _run(store.check_lifecycle_reconciliation("acct-rec", "sess-rec"))
    assert report.observed_state == "stopped"
    assert report.consistent is True


def test_reconcile_does_not_repair_balances_or_fabricate_records(tmp_path: Path) -> None:
    store = _make_store_with_lifecycle(tmp_path)
    before = _table_names(Path(store._db_path))
    report = _run(store.apply_lifecycle_command(_command("reconcile", "reconcile-1")))
    assert report.state == "running"
    after = _table_names(Path(store._db_path))
    assert after == before
    conn = sqlite3.connect(str(store._db_path))
    assert conn.execute("SELECT COUNT(*) FROM paper_orders").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM paper_fills").fetchone()[0] == 0
    conn.close()


def test_old_clean_report_does_not_authorize_after_state_change(tmp_path: Path) -> None:
    store = _make_store_with_lifecycle(tmp_path)
    report1 = _run(store.check_lifecycle_reconciliation("acct-rec", "sess-rec"))
    _run(store.apply_lifecycle_command(_command("pause", "pause-fresh")))
    report2 = _run(store.check_lifecycle_reconciliation("acct-rec", "sess-rec"))
    assert report1.consistent is True
    assert report2.consistent is True
    assert report1.snapshot_digest != report2.snapshot_digest


def test_reconcile_command_is_idempotent(tmp_path: Path) -> None:
    store = _make_store_with_lifecycle(tmp_path)
    command = _command("reconcile", "reconcile-idempotent")
    first = _run(store.apply_lifecycle_command(command))
    second = _run(store.apply_lifecycle_command(command))
    assert first == second
    conn = sqlite3.connect(str(store._db_path))
    assert conn.execute("SELECT COUNT(*) FROM paper_lifecycle_events WHERE command_id=?", (command.command_id,)).fetchone()[0] == 1
    conn.close()


def test_discrepancy_blocks_buy_resume_and_close(tmp_path: Path) -> None:
    store = _make_store_with_lifecycle(tmp_path)
    _insert_malformed_order(Path(store._db_path))
    order = _order("buy-blocked")
    _run(store.register_paper_order(order))
    policy = PaperRiskPolicy(
        policy_id="reconciliation-risk",
        policy_version="v1",
        account_id="acct-rec",
        quote_asset="USDT",
        max_order_notional=Decimal("1000"),
        max_total_exposure=Decimal("1000"),
        max_daily_loss=Decimal("1000"),
    )
    snapshot = PaperRiskSnapshot(
        account_id="acct-rec",
        quote_asset="USDT",
        market_symbol="BTC/USDT",
        market_mark_price=Decimal("100"),
        market_reference="test",
        market_timestamp_ms=1,
        equity_reference="test",
        equity_timestamp_ms=1,
        equity_utc_day_start=Decimal("10000"),
        equity_current_marked=Decimal("10000"),
        exposure_reference="test",
        exposure_timestamp_ms=1,
        marked_open_inventory=Decimal("0"),
        remaining_buy_reservations=Decimal("1000"),
    )
    filters = SymbolFilterSnapshot(
        snapshot_id="reconciliation-filters",
        filter_set_id="reconciliation-set",
        filter_set_version="v1",
        source_id="test",
        source_version="v1",
        capture_timestamp_ms=1,
        symbol="BTC/USDT",
        base_asset="BTC",
        quote_asset="USDT",
        price_filter=PriceFilter(min_price=Decimal("0"), max_price=Decimal("0"), tick_size=Decimal("0")),
        lot_size=LotSizeFilter(min_qty=Decimal("1"), max_qty=Decimal("10"), step_size=Decimal("1")),
        market_lot_size=MarketLotSizeFilter(min_qty=Decimal("1"), max_qty=Decimal("10"), step_size=Decimal("1")),
        min_notional=MinNotionalFilter(min_notional=Decimal("1"), applies_to_market=False, avg_price_mins=0),
        notional=None,
        unhandled_filter_types=(),
    )
    with pytest.raises(PaperLifecycleReconciliationError):
        _run(store.accept_paper_order(order.order_id, policy, snapshot, filters))
    _run(store.apply_lifecycle_command(_command("pause", "pause-blocked")))
    with pytest.raises(PaperLifecycleReconciliationError):
        _run(store.apply_lifecycle_command(_command("resume", "resume-blocked")))
    with pytest.raises(PaperLifecycleReconciliationError):
        _run(store.apply_lifecycle_command(_command("close", "close-blocked")))


def test_discrepancy_does_not_block_pause_stop_cancel_or_sell(tmp_path: Path) -> None:
    store = _make_store_with_lifecycle(tmp_path)
    _insert_malformed_order(Path(store._db_path))
    _run(store.apply_lifecycle_command(_command("pause", "pause-permitted")))
    assert _run(store.get_lifecycle_state("acct-rec", "sess-rec")).state == "paused"


def test_close_checks_fresh_reconciliation_report(tmp_path: Path) -> None:
    store = _make_store_with_lifecycle(tmp_path)
    _run(store.apply_lifecycle_command(_command("pause", "pause-close")))
    result = _run(store.apply_lifecycle_command(_command("close", "close-clean")))
    assert result.state == "stopped"
