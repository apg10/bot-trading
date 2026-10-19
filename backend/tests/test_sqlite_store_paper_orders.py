"""Tests for initial PAPER order-context persistence (current schema v10)."""

from __future__ import annotations

import asyncio
import sqlite3
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from src.models.paper_contracts import (
    PaperOrder,
    PaperOrderSide,
    PaperOrderStatus,
    PaperOrderType,
    PaperFill,
    PaperAccountOpening,
    PaperReservationRef,
    AssetBalance,
    StrategyRef,
)
from src.persistence import sqlite_store as sqlite_store_module
from src.persistence.sqlite_store import (
    PaperOrderContextConflictError,
    PaperOrderContextIntegrityError,
    PaperOrderContextStateError,
    PaperOrderCancellationStateError,
    PaperOrderAcceptanceFundsError,
    PaperOrderAcceptanceStateError,
    PositionLot,
    SQLiteStore,
    _SCHEMA_V7_TABLES,
    _SCHEMA_V9_TABLES,
    _SCHEMA_V10_TABLES,
    _SCHEMA_V11_TABLES,
)


def _run(coro: Any) -> Any:
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _order(
    order_id: str = "order-1",
    account_id: str = "account-1",
    session_id: str = "session-1",
    client_order_id: str = "client-1",
    price: Decimal | str = Decimal("10.00"),
) -> PaperOrder:
    return PaperOrder(
        order_id=order_id,
        client_order_id=client_order_id,
        paper_account_id=account_id,
        paper_session_id=session_id,
        symbol="BTC/USD",
        side=PaperOrderSide.BUY,
        order_type=PaperOrderType.LIMIT,
        requested_qty=Decimal("1.250"),
        price=Decimal(price) if isinstance(price, str) else price,
        strategy_ref=StrategyRef(strategy_id="strategy-1", strategy_version="v1"),
        status=PaperOrderStatus.PREPARED,
        timestamp_ms=10,
    )


def _table_names(db: Path) -> set[str]:
    conn = sqlite3.connect(str(db))
    try:
        return {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
    finally:
        conn.close()


def _make_store(db: Path) -> SQLiteStore:
    store = SQLiteStore(db)
    _run(store.initialize())
    return store


def test_register_and_load_initial_order_context(tmp_path: Path) -> None:
    db = tmp_path / "paper-order-context.db"
    store = SQLiteStore(db)
    _run(store.initialize())
    order = _order()

    assert _run(store.register_paper_order(order)) == order
    loaded = _run(store.load_paper_order(order.order_id))
    assert loaded == order
    assert loaded is not None
    assert (
        loaded.paper_account_id,
        loaded.paper_session_id,
        loaded.environment,
    ) == ("account-1", "session-1", "paper")
    assert _run(store.load_paper_order("missing-order")) is None
    conn = sqlite3.connect(str(db))
    assert conn.execute("SELECT COUNT(*) FROM paper_orders").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM paper_fills").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM paper_account_balances").fetchone()[0] == 0
    conn.close()
    _run(store.close())


def test_order_id_and_client_idempotency_key_conflicts_do_not_overwrite(
    tmp_path: Path,
) -> None:
    db = tmp_path / "paper-order-conflict.db"
    store = SQLiteStore(db)
    _run(store.initialize())
    original = _order()
    _run(store.register_paper_order(original))

    with pytest.raises(PaperOrderContextConflictError, match="order_id"):
        _run(store.register_paper_order(_order(price=Decimal("11.00"))))
    with pytest.raises(PaperOrderContextConflictError, match="order_id"):
        _run(
            store.register_paper_order(
                _order(
                    account_id="account-other",
                    session_id="session-other",
                    client_order_id="client-other",
                )
            )
        )
    with pytest.raises(PaperOrderContextConflictError, match="idempotency key"):
        _run(
            store.register_paper_order(
                _order(order_id="order-2", price=Decimal("11.00"))
            )
        )

    assert _run(store.load_paper_order("order-1")) == original
    assert _run(store.load_paper_order("order-2")) is None
    conn = sqlite3.connect(str(db))
    assert conn.execute("SELECT COUNT(*) FROM paper_orders").fetchone()[0] == 1
    conn.close()
    _run(store.close())


def test_same_client_order_id_in_different_context_is_independent(
    tmp_path: Path,
) -> None:
    db = tmp_path / "paper-order-context-key.db"
    store = SQLiteStore(db)
    _run(store.initialize())
    first = _order(client_order_id="same-client")
    second = _order(
        order_id="order-other-context",
        account_id="account-2",
        session_id="session-2",
        client_order_id="same-client",
    )

    _run(store.register_paper_order(first))
    _run(store.register_paper_order(second))
    assert _run(store.load_paper_order(first.order_id)) == first
    assert _run(store.load_paper_order(second.order_id)) == second
    conn = sqlite3.connect(str(db))
    assert conn.execute("SELECT COUNT(*) FROM paper_orders").fetchone()[0] == 2
    conn.close()
    _run(store.close())


def test_register_rejects_non_initial_order_state_before_write(tmp_path: Path) -> None:
    db = tmp_path / "paper-order-state.db"
    store = SQLiteStore(db)
    _run(store.initialize())
    non_initial = _order(order_id="order-not-initial").model_copy(
        update={"status": PaperOrderStatus.ACCEPTED}
    )

    with pytest.raises(PaperOrderContextStateError, match="PREPARED"):
        _run(store.register_paper_order(non_initial))

    conn = sqlite3.connect(str(db))
    assert conn.execute("SELECT COUNT(*) FROM paper_orders").fetchone()[0] == 0
    conn.close()
    _run(store.close())


def test_load_rejects_order_json_indexed_context_mismatch(tmp_path: Path) -> None:
    db = tmp_path / "paper-order-integrity.db"
    store = SQLiteStore(db)
    _run(store.initialize())
    order = _order()
    _run(store.register_paper_order(order))

    conn = sqlite3.connect(str(db))
    conn.execute(
        "UPDATE paper_orders SET paper_account_id = ? WHERE order_id = ?",
        ("different-account", order.order_id),
    )
    conn.commit()
    conn.close()
    with pytest.raises(PaperOrderContextIntegrityError, match="indexed identity/context"):
        _run(store.load_paper_order(order.order_id))
    _run(store.close())


def test_concurrent_order_registration_is_idempotent_or_conflicts_without_overwrite(
    tmp_path: Path,
) -> None:
    db = tmp_path / "paper-order-concurrent.db"
    store_a = SQLiteStore(db)
    store_b = SQLiteStore(db)
    _run(store_a.initialize())
    _run(store_b.initialize())
    identical = _order(order_id="order-identical-race")

    async def _register_identical():
        return await asyncio.gather(
            store_a.register_paper_order(identical),
            store_b.register_paper_order(identical),
            return_exceptions=True,
        )

    identical_results = _run(_register_identical())
    assert identical_results == [identical, identical]

    conflict_a = _order(
        order_id="order-conflict-race",
        client_order_id="client-conflict",
        price=Decimal("10.00"),
    )
    conflict_b = _order(
        order_id="order-conflict-race",
        client_order_id="client-conflict",
        price=Decimal("12.00"),
    )

    async def _register_conflicting():
        return await asyncio.gather(
            store_a.register_paper_order(conflict_a),
            store_b.register_paper_order(conflict_b),
            return_exceptions=True,
        )

    conflicting_results = _run(_register_conflicting())
    assert sum(isinstance(result, PaperOrder) for result in conflicting_results) == 1
    assert sum(
        isinstance(result, PaperOrderContextConflictError)
        for result in conflicting_results
    ) == 1
    persisted = _run(store_a.load_paper_order("order-conflict-race"))
    assert persisted in (conflict_a, conflict_b)

    conn = sqlite3.connect(str(db))
    assert conn.execute("SELECT COUNT(*) FROM paper_orders").fetchone()[0] == 2
    conn.close()
    _run(store_a.close())
    _run(store_b.close())


def _make_v7_database(db: Path) -> None:
    """Create the exact historical v7 schema, preserving accepted PAPER data."""
    store = SQLiteStore(db)
    _run(store.initialize())
    opening = PaperAccountOpening(
        account_id="account-before-v8",
        balances=(
            AssetBalance(
                asset="USD",
                total=Decimal("50.00"),
                available=Decimal("50.00"),
                reserved=Decimal("0"),
            ),
        ),
    )
    fill = PaperFill(
        fill_id="fill-before-v8",
        order_id="opaque-order-before-v8",
        qty=Decimal("1.0"),
        price=Decimal("10.00"),
        timestamp_ms=2,
        commission=None,
    )
    _run(store.open_paper_account(opening))
    _run(store.save_paper_fill(fill))
    _run(store.close())

    conn = sqlite3.connect(str(db))
    conn.execute("DROP TABLE paper_reservations")
    conn.execute("DROP TABLE paper_order_acceptances")
    conn.execute("DROP TABLE paper_orders")
    conn.execute("DROP TABLE paper_fill_applications")
    conn.execute("DROP TABLE position_lots")
    conn.execute("DROP TABLE IF EXISTS paper_reservation_lot_allocations")
    conn.execute("DROP TABLE IF EXISTS paper_sell_accounting")
    conn.execute("PRAGMA user_version = 7")
    conn.commit()
    conn.close()
    assert _table_names(db) == _SCHEMA_V7_TABLES


def test_direct_v7_to_v9_migration_preserves_accepted_paper_data(
    tmp_path: Path,
) -> None:
    db = tmp_path / "v7-to-v9.db"
    _make_v7_database(db)

    store = SQLiteStore(db)
    _run(store.initialize())
    assert _table_names(db) == _SCHEMA_V11_TABLES
    assert _run(store.load_paper_fill("fill-before-v8")) is not None
    assert _run(store.load_paper_account_opening("account-before-v8")) is not None
    assert _run(store.load_paper_order("order-not-registered")) is None
    conn = sqlite3.connect(str(db))
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 11
    conn.close()
    _run(store.close())


def test_v7_to_v10_migration_rolls_back_order_ddl_and_version(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = tmp_path / "v7-to-v10-rollback.db"
    _make_v7_database(db)
    original_ddl = sqlite_store_module._PAPER_ORDER_DDL_STATEMENTS
    monkeypatch.setattr(
        sqlite_store_module,
        "_PAPER_ORDER_DDL_STATEMENTS",
        (*original_ddl, "THIS IS INVALID SQL"),
    )

    store = SQLiteStore(db)
    with pytest.raises(sqlite3.OperationalError):
        _run(store.initialize())

    assert _table_names(db) == _SCHEMA_V7_TABLES
    conn = sqlite3.connect(str(db))
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 7
    assert conn.execute(
        "SELECT COUNT(*) FROM paper_fills WHERE fill_id = ?",
        ("fill-before-v8",),
    ).fetchone()[0] == 1
    assert conn.execute(
        "SELECT COUNT(*) FROM paper_account_openings WHERE account_id = ?",
        ("account-before-v8",),
    ).fetchone()[0] == 1
    conn.close()
    _run(store.close())


def test_registered_order_context_survives_close_and_reopen(tmp_path: Path) -> None:
    db = tmp_path / "paper-order-reopen.db"
    order = _order()
    first = SQLiteStore(db)
    _run(first.initialize())
    _run(first.register_paper_order(order))
    _run(first.close())

    reopened = SQLiteStore(db)
    _run(reopened.initialize())
    assert _run(reopened.load_paper_order(order.order_id)) == order
    assert _run(reopened.register_paper_order(order)) == order
    _run(reopened.close())


# ── ACT-P-003: cancel_paper_order focal tests ─────────────────────────────

from src.models.paper_contracts import (
    Commission,
    PaperAccountOpening,
    _PaperAccountOpeningAssetBalance,
)


def _open_quote_base(store: SQLiteStore, account_id: str = "acc-1") -> None:
    quote_bal = _PaperAccountOpeningAssetBalance(
        asset="QUOTE", total=Decimal("10000"), available=Decimal("10000"), reserved=Decimal("0")
    )
    base_bal = _PaperAccountOpeningAssetBalance(
        asset="BASE", total=Decimal("0"), available=Decimal("0"), reserved=Decimal("0")
    )
    from src.models.paper_contracts import PaperAccountOpening
    opening = PaperAccountOpening(
        environment="paper", account_id=account_id, balances=(quote_bal, base_bal),
    )
    _run(store.open_paper_account(opening))


def _make_buy_order(
    order_id: str = "order-1",
    side=PaperOrderSide.BUY,
    order_type=PaperOrderType.LIMIT,
    requested_qty=Decimal("2.0"),
    price="50.0",
    max_quote_amount=None,
    symbol="BASE/QUOTE",
    account_id="acc-1",
) -> PaperOrder:
    return PaperOrder(
        order_id=order_id,
        client_order_id=f"cli-{order_id}",
        paper_account_id=account_id,
        paper_session_id="sess-1",
        environment="paper",
        symbol=symbol,
        side=side,
        order_type=order_type,
        requested_qty=Decimal(str(requested_qty)),
        price=Decimal(str(price)) if price is not None else None,
        strategy_ref=StrategyRef(strategy_id="strat-1", strategy_version="v1"),
        status=PaperOrderStatus.PREPARED,
        timestamp_ms=1_700_000_000_000,
    )


def _make_fill(
    fill_id: str = "fill-1",
    order_id: str = "order-1",
    qty="1.0",
    price="40.0",
    timestamp_ms=1_700_000_001_000,
) -> PaperFill:
    return PaperFill(
        fill_id=fill_id, order_id=order_id, qty=Decimal(str(qty)),
        price=Decimal(str(price)), timestamp_ms=timestamp_ms,
        commission=Commission(amount=Decimal("0.5"), asset="QUOTE"),
    )


def test_real_reopen_cancel_repeat_compare_full_state(tmp_path: Path) -> None:
    """Par 1a. Reapertura real: cancelar, cerrar store, crear otro sobre la misma DB y repetir cancelación; comparar estado completo."""
    db = tmp_path / "reopen-cancel.db"
    store = _make_store(db)
    _open_quote_base(store)

    order = _make_buy_order(order_id="ord-reopen", price="50.0")
    _run(store.register_paper_order(order))
    _run(store.accept_paper_order("ord-reopen"))

    # Snapshot before first cancel
    conn = sqlite3.connect(str(db))
    snap_pre1 = {
        "order": PaperOrder.model_validate_json(
            conn.execute("SELECT * FROM paper_orders WHERE order_id='ord-reopen'").fetchone()[5]
        ),
        "reservation": conn.execute("SELECT * FROM paper_reservations WHERE owner_order_id='ord-reopen'").fetchone()[7],
        "quote_bal": AssetBalance.model_validate_json(
            conn.execute("SELECT * FROM paper_account_balances WHERE account_id='acc-1' AND asset='QUOTE'").fetchone()[2]
        ),
        "acceptance": conn.execute("SELECT * FROM paper_order_acceptances WHERE order_id='ord-reopen'").fetchall(),
    }
    conn.close()

    # First cancel
    c1 = _run(store.cancel_paper_order("ord-reopen"))
    assert c1.status == PaperOrderStatus.CANCELLED

    # Close store, reopen on same DB
    _run(store.close())
    reopened = _make_store(db)

    # Snapshot after reopen before second cancel
    conn = sqlite3.connect(str(db))
    snap_post_open = {
        "order": PaperOrder.model_validate_json(
            conn.execute("SELECT * FROM paper_orders WHERE order_id='ord-reopen'").fetchone()[5]
        ),
        "reservation_count": conn.execute("SELECT COUNT(*) FROM paper_reservations WHERE owner_order_id='ord-reopen'").fetchone()[0],
        "quote_bal": AssetBalance.model_validate_json(
            conn.execute("SELECT * FROM paper_account_balances WHERE account_id='acc-1' AND asset='QUOTE'").fetchone()[2]
        ),
    }
    conn.close()

    # Second cancel (idempotent)
    c2 = _run(reopened.cancel_paper_order("ord-reopen"))
    assert c2.status == PaperOrderStatus.CANCELLED

    # Compare full state: both cancels return identical DTOs
    assert c1 == c2

    # Verify order state unchanged between first and second cancel
    conn = sqlite3.connect(str(db))
    snap_pre2 = {
        "order": PaperOrder.model_validate_json(
            conn.execute("SELECT * FROM paper_orders WHERE order_id='ord-reopen'").fetchone()[5]
        ),
        "reservation_count": conn.execute("SELECT COUNT(*) FROM paper_reservations WHERE owner_order_id='ord-reopen'").fetchone()[0],
        "quote_bal": AssetBalance.model_validate_json(
            conn.execute("SELECT * FROM paper_account_balances WHERE account_id='acc-1' AND asset='QUOTE'").fetchone()[2]
        ),
    }
    conn.close()

    # State after second cancel should match state after first (idempotent)
    conn = sqlite3.connect(str(db))
    snap_post2 = {
        "order": PaperOrder.model_validate_json(
            conn.execute("SELECT * FROM paper_orders WHERE order_id='ord-reopen'").fetchone()[5]
        ),
        "reservation_count": conn.execute("SELECT COUNT(*) FROM paper_reservations WHERE owner_order_id='ord-reopen'").fetchone()[0],
        "quote_bal": AssetBalance.model_validate_json(
            conn.execute("SELECT * FROM paper_account_balances WHERE account_id='acc-1' AND asset='QUOTE'").fetchone()[2]
        ),
    }
    conn.close()

    assert snap_pre2 == snap_post2  # no changes from idempotent cancel
    assert snap_post_open["reservation_count"] == 0  # reservation already released by first cancel
    _run(reopened.close())


def test_cancel_partially_filled_preserves_fills_lot_cost_inventory_commissions(tmp_path: Path) -> None:
    """Par 1b. Cancelación parcial: snapshot completo antes/después de balances BASE/QUOTE, lote, orden/fills, reserva activa, aceptación y journal; comprobar que solo cambian estado y liberación del remanente."""
    db = tmp_path / "partial-cancel-snapshot.db"
    store = _make_store(db)
    _open_quote_base(store)

    order = _make_buy_order(order_id="ord-partial", price="50.0")
    _run(store.register_paper_order(order))
    _run(store.accept_paper_order("ord-partial"))

    # Apply one partial fill
    fill1 = _make_fill(fill_id="fill-p2", order_id="ord-partial", qty="1.0", price="40.0")
    _run(store.save_paper_fill(fill1))
    _run(store.apply_paper_fill("fill-p2"))

    # Full snapshot before cancel
    conn = sqlite3.connect(str(db))
    snap_before = {
        "order": PaperOrder.model_validate_json(
            conn.execute("SELECT order_json FROM paper_orders WHERE order_id='ord-partial'").fetchone()[0]
        ),
        "quote_bal": AssetBalance.model_validate_json(
            conn.execute("SELECT balance_json FROM paper_account_balances WHERE account_id='acc-1' AND asset='QUOTE'").fetchone()[0]
        ),
        "base_bal": AssetBalance.model_validate_json(
            conn.execute("SELECT balance_json FROM paper_account_balances WHERE account_id='acc-1' AND asset='BASE'").fetchone()[0]
        ),
        "reservation": PaperReservationRef.model_validate_json(
            conn.execute("SELECT reservation_json FROM paper_reservations WHERE owner_order_id='ord-partial'").fetchone()[0]
        ),
        "acceptance": conn.execute("SELECT reservation_json FROM paper_order_acceptances WHERE order_id='ord-partial'").fetchone()[0],
        "fills": [PaperFill.model_validate_json(r[0]) for r in conn.execute("SELECT record_json FROM paper_fills ORDER BY fill_id").fetchall() if PaperFill.model_validate_json(r[0]).order_id == "ord-partial"],
        "position_lots": conn.execute("SELECT lot_json FROM position_lots WHERE order_id='ord-partial' ORDER BY rowid").fetchall(),
    }
    conn.close()

    # Cancel
    cancelled = _run(store.cancel_paper_order("ord-partial"))
    assert cancelled.status == PaperOrderStatus.CANCELLED

    # Full snapshot after cancel
    conn = sqlite3.connect(str(db))
    snap_after = {
        "order": PaperOrder.model_validate_json(
            conn.execute("SELECT order_json FROM paper_orders WHERE order_id='ord-partial'").fetchone()[0]
        ),
        "quote_bal": AssetBalance.model_validate_json(
            conn.execute("SELECT balance_json FROM paper_account_balances WHERE account_id='acc-1' AND asset='QUOTE'").fetchone()[0]
        ),
        "base_bal": AssetBalance.model_validate_json(
            conn.execute("SELECT balance_json FROM paper_account_balances WHERE account_id='acc-1' AND asset='BASE'").fetchone()[0]
        ),
        "reservation_count": conn.execute("SELECT COUNT(*) FROM paper_reservations WHERE owner_order_id='ord-partial'").fetchone()[0],
        "acceptance": conn.execute("SELECT reservation_json FROM paper_order_acceptances WHERE order_id='ord-partial'").fetchone()[0],
        "fills": [PaperFill.model_validate_json(r[0]) for r in conn.execute("SELECT record_json FROM paper_fills ORDER BY fill_id").fetchall() if PaperFill.model_validate_json(r[0]).order_id == "ord-partial"],
        "position_lots": conn.execute("SELECT lot_json FROM position_lots WHERE order_id='ord-partial' ORDER BY rowid").fetchall(),
    }
    conn.close()

    # Verify only state and reservation changed; fills/cost/inventory/commission preserved
    assert snap_before["order"].status != snap_after["order"].status  # status changed
    assert snap_before["order"].fills == snap_after["order"].fills  # fills preserved
    assert snap_before["order"].executed_qty == snap_after["order"].executed_qty  # executed_qty preserved
    assert snap_before["order"].lot_allocations == snap_after["order"].lot_allocations  # lot preserved

    # Fills list unchanged
    assert snap_before["fills"] == snap_after["fills"]

    # Commission preserved
    assert sum(f.commission.amount for f in snap_before["fills"]) == sum(f.commission.amount for f in snap_after["fills"])

    # Position lots unchanged
    assert snap_before["position_lots"] == snap_after["position_lots"]

    # Acceptance snapshot unchanged
    assert snap_before["acceptance"] == snap_after["acceptance"]

    # BASE balance unchanged
    assert snap_before["base_bal"] == snap_after["base_bal"]

    # QUOTE reserved decreased by remainder, available increased by same amount
    remainder = snap_before["reservation"].amount.amount
    expected_reserved = snap_before["quote_bal"].reserved - remainder
    expected_available = snap_before["quote_bal"].available + remainder
    assert snap_after["quote_bal"].reserved == expected_reserved
    assert snap_after["quote_bal"].available == expected_available
    assert snap_after["quote_bal"].total == snap_before["quote_bal"].total  # total unchanged

    # Reservation released (row gone)
    assert snap_after["reservation_count"] == 0
    _run(store.close())


def test_another_order_of_same_asset_preserves_reservation(tmp_path: Path) -> None:
    """3. Otra orden del mismo activo conserva su reserva, disponible y estado."""
    db = tmp_path / "cancel-same-asset.db"
    store = _make_store(db)
    _open_quote_base(store)

    # Order A to be cancelled
    order_a = _make_buy_order(order_id="ord-a", price="50.0")
    _run(store.register_paper_order(order_a))
    _run(store.accept_paper_order("ord-a"))

    # Order B on the same account to verify isolation
    order_b = _make_buy_order(order_id="ord-b", account_id="acc-1", price="60.0")
    _run(store.register_paper_order(order_b))
    _run(store.accept_paper_order("ord-b"))

    # Cancel order A
    _run(store.cancel_paper_order("ord-a"))

    # Order B should be unaffected
    res_b = _run(store.load_paper_reservation("ord-b"))
    assert res_b is not None
    assert res_b.amount.amount == Decimal("120.0")  # 2.0 * 60.0
    loaded_b = _run(store.load_paper_order("ord-b"))
    assert loaded_b.status == PaperOrderStatus.ACCEPTED

    conn = sqlite3.connect(str(db))
    quote_bal = AssetBalance.model_validate_json(
        conn.execute("SELECT balance_json FROM paper_account_balances WHERE account_id='acc-1' AND asset='QUOTE'").fetchone()[0]
    )
    assert quote_bal.reserved == Decimal("120.0")  # only ord-b reserved
    conn.close()
    _run(store.close())


def test_repeated_cancel_after_reopen_is_idempotent(tmp_path: Path) -> None:
    """Par 1. Repetir cancelación tras reapertura es idempotente; snapshot de aceptación no cambia."""
    db = tmp_path / "cancel-idempotent.db"
    store = _make_store(db)
    _open_quote_base(store)

    order = _make_buy_order(order_id="ord-idem", price="50.0")
    _run(store.register_paper_order(order))
    _run(store.accept_paper_order("ord-idem"))

    # Capture acceptance snapshot before cancel
    conn = sqlite3.connect(str(db))
    snap_before = conn.execute(
        "SELECT reservation_json FROM paper_order_acceptances WHERE order_id='ord-idem'"
    ).fetchone()[0]
    conn.close()

    # First cancel
    c1 = _run(store.cancel_paper_order("ord-idem"))
    assert c1.status == PaperOrderStatus.CANCELLED

    # Close store and reopen on the same DB before repeating
    _run(store.close())
    store2 = _make_store(db)

    # Capture complete snapshots before second cancel
    conn = sqlite3.connect(str(db))
    snap_before_2nd = {
        "order": PaperOrder.model_validate_json(
            conn.execute("SELECT order_json FROM paper_orders WHERE order_id='ord-idem'").fetchone()[0]
        ),
        "quote_bal": AssetBalance.model_validate_json(
            conn.execute("SELECT balance_json FROM paper_account_balances WHERE account_id='acc-1' AND asset='QUOTE'").fetchone()[0]
        ),
        "base_bal": AssetBalance.model_validate_json(
            conn.execute("SELECT balance_json FROM paper_account_balances WHERE account_id='acc-1' AND asset='BASE'").fetchone()[0]
        ),
        "reservation_count": conn.execute("SELECT COUNT(*) FROM paper_reservations WHERE owner_order_id='ord-idem'").fetchone()[0],
        "reservation": PaperReservationRef.model_validate_json(
            conn.execute("SELECT reservation_json FROM paper_reservations WHERE owner_order_id='ord-idem'").fetchone()[0]
        ) if conn.execute("SELECT COUNT(*) FROM paper_reservations WHERE owner_order_id='ord-idem'").fetchone()[0] > 0 else None,
        "acceptance": conn.execute("SELECT reservation_json FROM paper_order_acceptances WHERE order_id='ord-idem'").fetchone()[0],
        "fills": [PaperFill.model_validate_json(r[0]) for r in conn.execute("SELECT record_json FROM paper_fills ORDER BY fill_id").fetchall() if PaperFill.model_validate_json(r[0]).order_id == "ord-idem"],
        "position_lots": conn.execute("SELECT lot_json FROM position_lots WHERE order_id='ord-idem' ORDER BY rowid").fetchall(),
    }
    conn.close()

    # Second cancel (idempotent) via new store instance
    c2 = _run(store2.cancel_paper_order("ord-idem"))
    assert c2.status == PaperOrderStatus.CANCELLED
    assert c1 == c2  # same DTO returned

    # Capture complete snapshots after second cancel
    conn = sqlite3.connect(str(db))
    snap_after_2nd = {
        "order": PaperOrder.model_validate_json(
            conn.execute("SELECT order_json FROM paper_orders WHERE order_id='ord-idem'").fetchone()[0]
        ),
        "quote_bal": AssetBalance.model_validate_json(
            conn.execute("SELECT balance_json FROM paper_account_balances WHERE account_id='acc-1' AND asset='QUOTE'").fetchone()[0]
        ),
        "base_bal": AssetBalance.model_validate_json(
            conn.execute("SELECT balance_json FROM paper_account_balances WHERE account_id='acc-1' AND asset='BASE'").fetchone()[0]
        ),
        "reservation_count": conn.execute("SELECT COUNT(*) FROM paper_reservations WHERE owner_order_id='ord-idem'").fetchone()[0],
        "reservation": PaperReservationRef.model_validate_json(
            conn.execute("SELECT reservation_json FROM paper_reservations WHERE owner_order_id='ord-idem'").fetchone()[0]
        ) if conn.execute("SELECT COUNT(*) FROM paper_reservations WHERE owner_order_id='ord-idem'").fetchone()[0] > 0 else None,
        "acceptance": conn.execute("SELECT reservation_json FROM paper_order_acceptances WHERE order_id='ord-idem'").fetchone()[0],
        "fills": [PaperFill.model_validate_json(r[0]) for r in conn.execute("SELECT record_json FROM paper_fills ORDER BY fill_id").fetchall() if PaperFill.model_validate_json(r[0]).order_id == "ord-idem"],
        "position_lots": conn.execute("SELECT lot_json FROM position_lots WHERE order_id='ord-idem' ORDER BY rowid").fetchall(),
    }
    conn.close()

    # Verify idempotency: all snapshots identical after second cancel
    assert snap_before_2nd["order"] == snap_after_2nd["order"]
    assert snap_before_2nd["quote_bal"] == snap_after_2nd["quote_bal"]
    assert snap_before_2nd["base_bal"] == snap_after_2nd["base_bal"]
    assert snap_before_2nd["reservation_count"] == snap_after_2nd["reservation_count"]
    assert snap_before_2nd["reservation"] == snap_after_2nd["reservation"]
    assert snap_before_2nd["acceptance"] == snap_after_2nd["acceptance"]
    assert snap_before_2nd["fills"] == snap_after_2nd["fills"]
    assert snap_before_2nd["position_lots"] == snap_after_2nd["position_lots"]

    # Acceptance snapshot unchanged (original check)
    conn = sqlite3.connect(str(db))
    snap_after = conn.execute(
        "SELECT reservation_json FROM paper_order_acceptances WHERE order_id='ord-idem'"
    ).fetchone()[0]
    assert snap_after == snap_before
    conn.close()
    _run(store2.close())


def test_terminal_states_rejected_and_prepared_out_of_scope(tmp_path: Path) -> None:
    """Par 3. Estados terminales: tras cada rechazo, comparar snapshots completos antes/después para demostrar que no hubo cambios."""

    def _full_snap(conn, order_id):
        return {
            "order": PaperOrder.model_validate_json(
                conn.execute(f"SELECT * FROM paper_orders WHERE order_id='{order_id}'").fetchone()[5]
            ),
            "quote_bal": AssetBalance.model_validate_json(
                conn.execute("SELECT * FROM paper_account_balances WHERE account_id='acc-1' AND asset='QUOTE'").fetchone()[2]
            ),
            "base_bal": AssetBalance.model_validate_json(
                conn.execute("SELECT * FROM paper_account_balances WHERE account_id='acc-1' AND asset='BASE'").fetchone()[2]
            ),
            "reservations": conn.execute(f"SELECT * FROM paper_reservations WHERE owner_order_id='{order_id}' ORDER BY rowid").fetchall(),
            "position_lots": conn.execute(f"SELECT * FROM position_lots WHERE order_id='{order_id}' ORDER BY rowid").fetchall(),
            "acceptances": conn.execute(f"SELECT * FROM paper_order_acceptances WHERE order_id='{order_id}' ORDER BY rowid").fetchall(),
            "fill_applications": conn.execute(f"SELECT * FROM paper_fill_applications WHERE order_id='{order_id}' ORDER BY fill_id").fetchall(),
            "fills": [PaperFill.model_validate_json(r[1]) for r in conn.execute("SELECT * FROM paper_fills ORDER BY fill_id").fetchall() if PaperFill.model_validate_json(r[1]).order_id == order_id],
        }

    db = tmp_path / "cancel-terminal-snap.db"
    store = _make_store(db)
    _open_quote_base(store)

    # ── FILLED ────────────────────────────────────────────────────────
    order_filled = _make_buy_order(order_id="ord-filled2", price="50.0")
    _run(store.register_paper_order(order_filled))
    _run(store.accept_paper_order("ord-filled2"))
    fill_f = _make_fill(fill_id="fill-f2", order_id="ord-filled2", qty="2.0", price="50.0")
    _run(store.save_paper_fill(fill_f))
    _run(store.apply_paper_fill("fill-f2"))

    conn = sqlite3.connect(str(db))
    snap_filled_pre = _full_snap(conn, "ord-filled2")
    conn.close()

    with pytest.raises(PaperOrderCancellationStateError, match="terminal state"):
        _run(store.cancel_paper_order("ord-filled2"))

    conn = sqlite3.connect(str(db))
    snap_filled_post = _full_snap(conn, "ord-filled2")
    conn.close()
    assert snap_filled_pre == snap_filled_post  # no changes from rejection

    # ── REJECTED ──────────────────────────────────────────────────────
    order_rej = _make_buy_order(order_id="ord-rej2", price="50.0")
    _run(store.register_paper_order(order_rej))
    conn = sqlite3.connect(str(db))
    rej_json = PaperOrder.model_validate_json(
        conn.execute("SELECT order_json FROM paper_orders WHERE order_id='ord-rej2'").fetchone()[0]
    ).model_dump(mode="python")
    rej_json["status"] = PaperOrderStatus.REJECTED.value
    conn.execute(
        "UPDATE paper_orders SET order_json = ? WHERE order_id='ord-rej2'",
        (PaperOrder(**rej_json).model_dump_json(),),
    )
    conn.commit()
    conn.close()

    conn = sqlite3.connect(str(db))
    snap_rej_pre = _full_snap(conn, "ord-rej2")
    conn.close()

    with pytest.raises(PaperOrderCancellationStateError, match="terminal state"):
        _run(store.cancel_paper_order("ord-rej2"))

    conn = sqlite3.connect(str(db))
    snap_rej_post = _full_snap(conn, "ord-rej2")
    conn.close()
    assert snap_rej_pre == snap_rej_post  # no changes from rejection

    # ── EXPIRED ───────────────────────────────────────────────────────
    order_exp = _make_buy_order(order_id="ord-exp2", price="50.0")
    _run(store.register_paper_order(order_exp))
    conn = sqlite3.connect(str(db))
    exp_json = PaperOrder.model_validate_json(
        conn.execute("SELECT order_json FROM paper_orders WHERE order_id='ord-exp2'").fetchone()[0]
    ).model_dump(mode="python")
    exp_json["status"] = PaperOrderStatus.EXPIRED.value
    conn.execute(
        "UPDATE paper_orders SET order_json = ? WHERE order_id='ord-exp2'",
        (PaperOrder(**exp_json).model_dump_json(),),
    )
    conn.commit()
    conn.close()

    conn = sqlite3.connect(str(db))
    snap_exp_pre = _full_snap(conn, "ord-exp2")
    conn.close()

    with pytest.raises(PaperOrderCancellationStateError, match="terminal state"):
        _run(store.cancel_paper_order("ord-exp2"))

    conn = sqlite3.connect(str(db))
    snap_exp_post = _full_snap(conn, "ord-exp2")
    conn.close()
    assert snap_exp_pre == snap_exp_post  # no changes from rejection

    # ── PREPARED (out of scope) ───────────────────────────────────────
    order_prep = _make_buy_order(order_id="ord-prep2", price="50.0")
    _run(store.register_paper_order(order_prep))

    conn = sqlite3.connect(str(db))
    snap_prep_pre = _full_snap(conn, "ord-prep2")
    conn.close()

    with pytest.raises(PaperOrderCancellationStateError, match="outside.*cancellation scope"):
        _run(store.cancel_paper_order("ord-prep2"))

    conn = sqlite3.connect(str(db))
    snap_prep_post = _full_snap(conn, "ord-prep2")
    conn.close()
    assert snap_prep_pre == snap_prep_post  # no changes from rejection

    _run(store.close())


def test_cancel_vs_fill_race_accepts_serialized_results(tmp_path: Path) -> None:
    """Par 2. Carrera fill parcial/cancelación: captura estado completo tras guardar fill y antes de carrera. Si fill devuelve PaperFill, exige exactamente una marca, el fill una sola vez en stored_order.fills, lote total_qty=1.0/total_cost=40.0, contabilidad (total=available=9959.5, reserved=0). Si cancelación ganó, exige stored_order.fills==[], cero aplicaciones/lotes, BASE sin acreditar, QUOTE total=available=10000.0 y reserva cero. En ambos casos verifica que el record_json journalizado antes de la carrera permanece idéntico; no usa paper_fills como prueba de aplicación. Rechaza cualquier otra excepción."""
    db = tmp_path / "cancel-fill-race-strict.db"
    store_a = _make_store(db)
    store_b = _make_store(db)
    _open_quote_base(store_a)

    order = _make_buy_order(order_id="ord-race2", price="50.0")
    _run(store_a.register_paper_order(order))
    _run(store_a.accept_paper_order("ord-race2"))

    fill_partial = _make_fill(fill_id="fill-partial-race2", order_id="ord-race2", qty="1.0", price="40.0")
    _run(store_a.save_paper_fill(fill_partial))

    # Capture complete state after saving fill, before race
    conn = sqlite3.connect(str(db))
    snap_before_race = {
        "order": PaperOrder.model_validate_json(
            conn.execute("SELECT * FROM paper_orders WHERE order_id='ord-race2'").fetchone()[5]
        ),
        "quote_bal": AssetBalance.model_validate_json(
            conn.execute("SELECT * FROM paper_account_balances WHERE account_id='acc-1' AND asset='QUOTE'").fetchone()[2]
        ),
        "base_bal": AssetBalance.model_validate_json(
            conn.execute("SELECT * FROM paper_account_balances WHERE account_id='acc-1' AND asset='BASE'").fetchone()[2]
        ),
        "reservation_count": conn.execute("SELECT COUNT(*) FROM paper_reservations WHERE owner_order_id='ord-race2'").fetchone()[0],
        "fill_applications": conn.execute("SELECT * FROM paper_fill_applications WHERE order_id='ord-race2' ORDER BY fill_id").fetchall(),
        "position_lots": conn.execute("SELECT * FROM position_lots WHERE order_id='ord-race2' ORDER BY rowid").fetchall(),
        "acceptances": conn.execute("SELECT * FROM paper_order_acceptances WHERE order_id='ord-race2' ORDER BY rowid").fetchall(),
        "journal_record": conn.execute("SELECT * FROM paper_fills WHERE fill_id='fill-partial-race2'").fetchone() if conn.execute("SELECT 1 FROM paper_fills WHERE fill_id='fill-partial-race2'").fetchone() else None,
    }
    conn.close()

    # Both stores race: one cancels, one applies fill
    async def _race():
        return await asyncio.gather(
            store_a.cancel_paper_order("ord-race2"),
            store_b.apply_paper_fill("fill-partial-race2"),
            return_exceptions=True,
        )

    results = _run(_race())
    assert len(results) == 2

    # Cancel MUST succeed with CANCELLED
    cancel_result = results[0]
    assert isinstance(cancel_result, PaperOrder), f"Cancel must succeed with CANCELLED, got {type(cancel_result)}: {cancel_result}"
    assert cancel_result.status == PaperOrderStatus.CANCELLED

    # Fill result: can only be PaperFill or fail with expected terminal state error
    fill_result = results[1]
    fill_applied = False
    if isinstance(fill_result, PaperFill):
        assert fill_result.order_id == "ord-race2"
        fill_applied = True
    elif isinstance(fill_result, PaperOrderAcceptanceStateError):
        # Expected: terminal state error when cancel won first
        pass
    else:
        pytest.fail(f"Fill result must be PaperFill or PaperOrderAcceptanceStateError, got {type(fill_result)}: {fill_result}")

    conn = sqlite3.connect(str(db))
    order_row = conn.execute("SELECT order_json FROM paper_orders WHERE order_id='ord-race2'").fetchone()
    stored_order = PaperOrder.model_validate_json(order_row[0])

    # In both cases, order must be CANCELLED and reservation released
    assert stored_order.status == PaperOrderStatus.CANCELLED, f"Order must be CANCELLED, got {stored_order.status}"
    res_count = conn.execute(
        "SELECT COUNT(*) FROM paper_reservations WHERE owner_order_id='ord-race2'"
    ).fetchone()[0]
    assert res_count == 0

    if fill_applied:
        # Exactly that fill in stored_order.fills
        assert len(stored_order.fills) == 1, f"Expected exactly 1 fill in stored_order.fills, got {len(stored_order.fills)}"
        assert stored_order.fills[0].fill_id == "fill-partial-race2"

        # One application row matching expected values
        marks_post = conn.execute("SELECT * FROM paper_fill_applications WHERE order_id='ord-race2' ORDER BY fill_id").fetchall()
        assert len(marks_post) == 1, f"Expected exactly 1 mark, got {len(marks_post)}"
        exp_row = ("fill-partial-race2", "ord-race2", "acc-1", "sess-1", "paper", 1700000001000)
        assert marks_post[0][:6] == exp_row, f"Application row mismatch: {marks_post[0][:6]} != {exp_row}"

        # Lot: total_qty=1.0, total_cost=40.0
        lots_post = conn.execute("SELECT * FROM position_lots WHERE order_id='ord-race2' ORDER BY rowid").fetchall()
        assert len(lots_post) == 1, f"Expected exactly 1 lot, got {len(lots_post)}"
        lot_data = PositionLot.model_validate_json(lots_post[0][4])
        assert lot_data.total_qty == Decimal("1.0")
        assert lot_data.total_cost == Decimal("40.0")

        # Expected accounting: total=available=9959.5, reserved=0
        base_bal = AssetBalance.model_validate_json(
            conn.execute("SELECT * FROM paper_account_balances WHERE account_id='acc-1' AND asset='BASE'").fetchone()[2]
        )
        assert base_bal.available == snap_before_race["base_bal"].available + Decimal("1.0")

        quote_bal = AssetBalance.model_validate_json(
            conn.execute("SELECT * FROM paper_account_balances WHERE account_id='acc-1' AND asset='QUOTE'").fetchone()[2]
        )
        assert quote_bal.total == Decimal("9959.5")
        assert quote_bal.available == Decimal("9959.5")
        assert quote_bal.reserved == Decimal("0")

        # Journal byte-by-byte: record must remain identical to pre-race snapshot
        journal_post = conn.execute("SELECT * FROM paper_fills WHERE fill_id='fill-partial-race2'").fetchone()
        assert journal_post is not None, "Journalized record must still exist"
        assert journal_post == snap_before_race["journal_record"], "Journal row must remain byte-identical to pre-race snapshot"

    else:
        # Cancel won first: stored_order.fills == []
        assert stored_order.fills == [], f"Expected no fills when cancel won, got {len(stored_order.fills)}"

        # Zero applications/lots
        marks_post = conn.execute("SELECT * FROM paper_fill_applications WHERE order_id='ord-race2' ORDER BY fill_id").fetchall()
        assert len(marks_post) == 0, f"Expected zero marks when cancel won, got {len(marks_post)}"

        lots_post = conn.execute("SELECT * FROM position_lots WHERE order_id='ord-race2' ORDER BY rowid").fetchall()
        assert len(lots_post) == 0, f"Expected zero lots when cancel won, got {len(lots_post)}"

        # BASE not credited
        base_bal = AssetBalance.model_validate_json(
            conn.execute("SELECT * FROM paper_account_balances WHERE account_id='acc-1' AND asset='BASE'").fetchone()[2]
        )
        assert base_bal.available == snap_before_race["base_bal"].available, "BASE must not be credited when cancel won"

        # QUOTE total=available=10000.0 and reservation zero
        quote_bal = AssetBalance.model_validate_json(
            conn.execute("SELECT * FROM paper_account_balances WHERE account_id='acc-1' AND asset='QUOTE'").fetchone()[2]
        )
        assert quote_bal.total == Decimal("10000.0")
        assert quote_bal.available == Decimal("10000.0")
        assert quote_bal.reserved == Decimal("0")

        # Journal byte-by-byte: record must remain identical to pre-race snapshot (no fill applied)
        journal_post = conn.execute("SELECT * FROM paper_fills WHERE fill_id='fill-partial-race2'").fetchone()
        assert journal_post is not None, "Journalized record must still exist"
        assert journal_post == snap_before_race["journal_record"], "Journal row must remain byte-identical to pre-race snapshot"

    conn.close()
    _run(store_a.close())
    _run(store_b.close())


def test_cancel_rollback_on_quote_balance_trigger(tmp_path: Path) -> None:
    """7a. Inyectar fallo tras UPDATE balance con trigger AFTER; comprobar rollback completo."""
    db = tmp_path / "cancel-trigger-balance.db"
    store = _make_store(db)
    _open_quote_base(store)

    order = _make_buy_order(order_id="ord-trig", price="50.0")
    _run(store.register_paper_order(order))
    _run(store.accept_paper_order("ord-trig"))

    # Inject trigger after balance update
    conn = sqlite3.connect(str(db))
    conn.execute(
        "CREATE TABLE IF NOT EXISTS _trigger_control (action TEXT PRIMARY KEY)"
    )
    trigger = """
    CREATE TRIGGER IF NOT EXISTS fail_after_cancel_balance
    AFTER UPDATE ON paper_account_balances
    WHEN NEW.asset = 'QUOTE' AND (SELECT COUNT(*) FROM _trigger_control WHERE action='enabled') > 0
    BEGIN
        SELECT RAISE(ABORT, 'cancel-trigger-fail');
    END;
    """
    conn.executescript(trigger)
    conn.execute("INSERT OR REPLACE INTO _trigger_control (action) VALUES ('enabled')")
    conn.commit()

    # Snapshot before cancel
    balances_pre = [tuple(r) for r in conn.execute("SELECT * FROM paper_account_balances ORDER BY asset").fetchall()]
    reservation_pre = [tuple(r) for r in conn.execute("SELECT * FROM paper_reservations WHERE owner_order_id='ord-trig'").fetchall()]
    order_pre = [tuple(r) for r in conn.execute("SELECT * FROM paper_orders WHERE order_id='ord-trig'").fetchall()]
    acceptance_pre = [tuple(r) for r in conn.execute("SELECT * FROM paper_order_acceptances WHERE order_id='ord-trig'").fetchall()]
    conn.close()

    with pytest.raises(sqlite3.IntegrityError, match="cancel-trigger-fail"):
        _run(store.cancel_paper_order("ord-trig"))

    # Verify complete rollback
    conn = sqlite3.connect(str(db))
    assert [tuple(r) for r in conn.execute("SELECT * FROM paper_account_balances ORDER BY asset").fetchall()] == balances_pre
    assert [tuple(r) for r in conn.execute("SELECT * FROM paper_reservations WHERE owner_order_id='ord-trig'").fetchall()] == reservation_pre
    assert [tuple(r) for r in conn.execute("SELECT * FROM paper_orders WHERE order_id='ord-trig'").fetchall()] == order_pre
    assert [tuple(r) for r in conn.execute("SELECT * FROM paper_order_acceptances WHERE order_id='ord-trig'").fetchall()] == acceptance_pre

    # Order should still be ACCEPTED (not CANCELLED)
    stored = PaperOrder.model_validate_json(
        conn.execute("SELECT order_json FROM paper_orders WHERE order_id='ord-trig'").fetchone()[0]
    )
    assert stored.status == PaperOrderStatus.ACCEPTED
    conn.close()
    _run(store.close())


def test_cancel_rollback_on_reservation_delete_trigger(tmp_path: Path) -> None:
    """7b. Inyectar fallo tras DELETE reserva con trigger AFTER; comprobar rollback completo."""
    db = tmp_path / "cancel-trigger-reservation.db"
    store = _make_store(db)
    _open_quote_base(store)

    order = _make_buy_order(order_id="ord-trig2", price="50.0")
    _run(store.register_paper_order(order))
    _run(store.accept_paper_order("ord-trig2"))

    conn = sqlite3.connect(str(db))
    conn.execute(
        "CREATE TABLE IF NOT EXISTS _trigger_control (action TEXT PRIMARY KEY)"
    )
    trigger = """
    CREATE TRIGGER IF NOT EXISTS fail_after_cancel_reservation_delete
    AFTER DELETE ON paper_reservations
    WHEN (SELECT COUNT(*) FROM _trigger_control WHERE action='enabled') > 0
    BEGIN
        SELECT RAISE(ABORT, 'cancel-reservation-trigger-fail');
    END;
    """
    conn.executescript(trigger)
    conn.execute("INSERT OR REPLACE INTO _trigger_control (action) VALUES ('enabled')")
    conn.commit()

    # Snapshot before cancel
    reservation_pre = [tuple(r) for r in conn.execute("SELECT * FROM paper_reservations WHERE owner_order_id='ord-trig2'").fetchall()]
    order_pre = [tuple(r) for r in conn.execute("SELECT * FROM paper_orders WHERE order_id='ord-trig2'").fetchall()]
    balances_pre = [tuple(r) for r in conn.execute("SELECT * FROM paper_account_balances ORDER BY asset").fetchall()]
    acceptance_pre = [tuple(r) for r in conn.execute("SELECT * FROM paper_order_acceptances WHERE order_id='ord-trig2'").fetchall()]
    conn.close()

    with pytest.raises(sqlite3.IntegrityError, match="cancel-reservation-trigger-fail"):
        _run(store.cancel_paper_order("ord-trig2"))

    # Verify complete rollback
    conn = sqlite3.connect(str(db))
    assert [tuple(r) for r in conn.execute("SELECT * FROM paper_account_balances ORDER BY asset").fetchall()] == balances_pre
    assert [tuple(r) for r in conn.execute("SELECT * FROM paper_reservations WHERE owner_order_id='ord-trig2'").fetchall()] == reservation_pre
    assert [tuple(r) for r in conn.execute("SELECT * FROM paper_orders WHERE order_id='ord-trig2'").fetchall()] == order_pre
    assert [tuple(r) for r in conn.execute("SELECT * FROM paper_order_acceptances WHERE order_id='ord-trig2'").fetchall()] == acceptance_pre

    stored = PaperOrder.model_validate_json(
        conn.execute("SELECT order_json FROM paper_orders WHERE order_id='ord-trig2'").fetchone()[0]
    )
    assert stored.status == PaperOrderStatus.ACCEPTED
    conn.close()
    _run(store.close())


def test_cancel_rollback_on_order_update_trigger(tmp_path: Path) -> None:
    """7c. Inyectar fallo tras UPDATE orden con trigger AFTER; comprobar rollback completo."""
    db = tmp_path / "cancel-trigger-order.db"
    store = _make_store(db)
    _open_quote_base(store)

    order = _make_buy_order(order_id="ord-trig3", price="50.0")
    _run(store.register_paper_order(order))
    _run(store.accept_paper_order("ord-trig3"))

    conn = sqlite3.connect(str(db))
    conn.execute(
        "CREATE TABLE IF NOT EXISTS _trigger_control (action TEXT PRIMARY KEY)"
    )
    trigger = """
    CREATE TRIGGER IF NOT EXISTS fail_after_cancel_order_update
    AFTER UPDATE ON paper_orders
    WHEN (SELECT COUNT(*) FROM _trigger_control WHERE action='enabled') > 0
         AND NEW.order_json LIKE '%"status":"cancelled"%'
    BEGIN
        SELECT RAISE(ABORT, 'cancel-order-trigger-fail');
    END;
    """
    conn.executescript(trigger)
    conn.execute("INSERT OR REPLACE INTO _trigger_control (action) VALUES ('enabled')")
    conn.commit()

    # Snapshot before cancel
    order_pre = [tuple(r) for r in conn.execute("SELECT * FROM paper_orders WHERE order_id='ord-trig3'").fetchall()]
    reservation_pre = [tuple(r) for r in conn.execute("SELECT * FROM paper_reservations WHERE owner_order_id='ord-trig3'").fetchall()]
    balances_pre = [tuple(r) for r in conn.execute("SELECT * FROM paper_account_balances ORDER BY asset").fetchall()]
    acceptance_pre = [tuple(r) for r in conn.execute("SELECT * FROM paper_order_acceptances WHERE order_id='ord-trig3'").fetchall()]
    conn.close()

    with pytest.raises(sqlite3.IntegrityError, match="cancel-order-trigger-fail"):
        _run(store.cancel_paper_order("ord-trig3"))

    # Verify complete rollback
    conn = sqlite3.connect(str(db))
    assert [tuple(r) for r in conn.execute("SELECT * FROM paper_orders WHERE order_id='ord-trig3'").fetchall()] == order_pre
    assert [tuple(r) for r in conn.execute("SELECT * FROM paper_reservations WHERE owner_order_id='ord-trig3'").fetchall()] == reservation_pre
    assert [tuple(r) for r in conn.execute("SELECT * FROM paper_account_balances ORDER BY asset").fetchall()] == balances_pre
    assert [tuple(r) for r in conn.execute("SELECT * FROM paper_order_acceptances WHERE order_id='ord-trig3'").fetchall()] == acceptance_pre

    stored = PaperOrder.model_validate_json(
        conn.execute("SELECT order_json FROM paper_orders WHERE order_id='ord-trig3'").fetchone()[0]
    )
    assert stored.status == PaperOrderStatus.ACCEPTED
    conn.close()
    _run(store.close())


def test_journal_fill_preserved_after_cancel_rollback(tmp_path: Path) -> None:
    """7d. Si el fill ya estaba journalizado, su registro debe conservarse exactamente tras rollback de cancelación."""
    db = tmp_path / "cancel-trigger-journal.db"
    store = _make_store(db)
    _open_quote_base(store)

    order = _make_buy_order(order_id="ord-journal", price="50.0")
    _run(store.register_paper_order(order))
    _run(store.accept_paper_order("ord-journal"))

    # Journalize a fill (separate from cancel)
    journal_fill = _make_fill(fill_id="fill-journal", order_id="ord-journal", qty="1.0", price="40.0")
    _run(store.save_paper_fill(journal_fill))

    conn = sqlite3.connect(str(db))
    journal_pre = [tuple(r) for r in conn.execute("SELECT * FROM paper_fills WHERE fill_id='fill-journal'").fetchall()]
    conn.close()

    # Inject trigger to cause cancel rollback
    conn = sqlite3.connect(str(db))
    conn.execute(
        "CREATE TABLE IF NOT EXISTS _trigger_control (action TEXT PRIMARY KEY)"
    )
    trigger = """
    CREATE TRIGGER IF NOT EXISTS fail_after_cancel_journal
    AFTER UPDATE ON paper_account_balances
    WHEN NEW.asset = 'QUOTE' AND (SELECT COUNT(*) FROM _trigger_control WHERE action='enabled') > 0
    BEGIN
        SELECT RAISE(ABORT, 'cancel-journal-trigger-fail');
    END;
    """
    conn.executescript(trigger)
    conn.execute("INSERT OR REPLACE INTO _trigger_control (action) VALUES ('enabled')")
    conn.commit()
    conn.close()

    with pytest.raises(sqlite3.IntegrityError, match="cancel-journal-trigger-fail"):
        _run(store.cancel_paper_order("ord-journal"))

    # Verify journal fill preserved
    conn = sqlite3.connect(str(db))
    journal_post = [tuple(r) for r in conn.execute("SELECT * FROM paper_fills WHERE fill_id='fill-journal'").fetchall()]
    assert journal_post == journal_pre
    conn.close()
    _run(store.close())
