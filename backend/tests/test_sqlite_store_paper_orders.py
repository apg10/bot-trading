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
    AssetBalance,
    StrategyRef,
)
from src.persistence import sqlite_store as sqlite_store_module
from src.persistence.sqlite_store import (
    PaperOrderContextConflictError,
    PaperOrderContextIntegrityError,
    PaperOrderContextStateError,
    SQLiteStore,
    _SCHEMA_V7_TABLES,
    _SCHEMA_V9_TABLES,
    _SCHEMA_V10_TABLES,
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
    assert _table_names(db) == _SCHEMA_V10_TABLES
    assert _run(store.load_paper_fill("fill-before-v8")) is not None
    assert _run(store.load_paper_account_opening("account-before-v8")) is not None
    assert _run(store.load_paper_order("order-not-registered")) is None
    conn = sqlite3.connect(str(db))
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 10
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
