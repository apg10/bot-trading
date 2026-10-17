"""Tests for atomic PAPER order acceptance and principal reservation (schema v9)."""

from __future__ import annotations

import asyncio
import json
import sqlite3
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from src.models.paper_contracts import (
    AssetBalance,
    PaperAccountOpening,
    PaperFill,
    LotAllocation,
    PaperOrder,
    PaperOrderSide,
    PaperOrderStatus,
    PaperOrderType,
    PaperReservationRef,
    ReservationAmount,
    StrategyRef,
)
from src.persistence import sqlite_store as sqlite_store_module
from src.persistence.sqlite_store import (
    PaperOrderContextConflictError,
    PaperOrderAcceptanceConflictError,
    PaperOrderAcceptanceFundsError,
    PaperOrderAcceptanceScopeError,
    PaperOrderAcceptanceStateError,
    SQLiteStore,
    _SCHEMA_V8_TABLES,
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
    client_order_id: str = "client-1",
    order_type: PaperOrderType = PaperOrderType.LIMIT,
    qty: Decimal = Decimal("2.00"),
    price: Decimal | None = Decimal("10.00"),
    max_quote: Decimal | None = None,
    account_id: str = "account-1",
    session_id: str = "session-1",
    symbol: str = "BTC/USD",
) -> PaperOrder:
    return PaperOrder(
        order_id=order_id,
        client_order_id=client_order_id,
        paper_account_id=account_id,
        paper_session_id=session_id,
        symbol=symbol,
        side=PaperOrderSide.BUY,
        order_type=order_type,
        requested_qty=qty,
        price=price,
        max_quote_amount=max_quote,
        strategy_ref=StrategyRef(strategy_id="strategy-1", strategy_version="v1"),
        status=PaperOrderStatus.PREPARED,
        timestamp_ms=100,
    )


def _run_with_open_account(db: Path, quote_available: Decimal = Decimal("100.00")) -> SQLiteStore:
    store = SQLiteStore(db)
    _run(store.initialize())
    opening = PaperAccountOpening(
        account_id="account-1",
        balances=(
            AssetBalance(
                asset="USD",
                total=quote_available,
                available=quote_available,
                reserved=Decimal("0"),
            ),
        ),
    )
    _run(store.open_paper_account(opening))
    return store


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


def test_limit_buy_acceptance_reserves_exact_quote_principal(tmp_path: Path) -> None:
    db = tmp_path / "limit-buy-acceptance.db"
    store = _run_with_open_account(db)
    order = _order()
    _run(store.register_paper_order(order))

    accepted = _run(store.accept_paper_order(order.order_id))
    assert accepted.status == PaperOrderStatus.ACCEPTED
    assert not accepted.fills
    reservation = _run(store.load_paper_reservation(order.order_id))
    assert reservation is not None
    assert reservation.reservation_id == order.order_id
    assert reservation.owner_order_id == order.order_id
    assert reservation.amount == ReservationAmount(asset="USD", amount=Decimal("20.0000"))
    balance = _run(store.load_paper_balance_view("account-1"))
    assert balance is not None
    assert balance.balances[0].total == Decimal("100.00")
    assert balance.balances[0].available == Decimal("80.0000")
    assert balance.balances[0].reserved == Decimal("20.0000")
    _run(store.close())


def test_market_buy_uses_explicit_max_quote_amount_without_price_estimate(
    tmp_path: Path,
) -> None:
    db = tmp_path / "market-buy-acceptance.db"
    store = _run_with_open_account(db, Decimal("250.00"))
    order = _order(
        order_id="market-order",
        client_order_id="market-client",
        order_type=PaperOrderType.MARKET,
        qty=Decimal("50"),
        price=None,
        max_quote=Decimal("175.25"),
    )
    _run(store.register_paper_order(order))

    accepted = _run(store.accept_paper_order(order.order_id))
    assert accepted.status == PaperOrderStatus.ACCEPTED
    reservation = _run(store.load_paper_reservation(order.order_id))
    assert reservation is not None
    assert reservation.amount == ReservationAmount(
        asset="USD", amount=Decimal("175.25")
    )
    _run(store.close())


def test_buy_reservation_rejects_ambiguous_quote_symbol_without_writes(
    tmp_path: Path,
) -> None:
    db = tmp_path / "ambiguous-quote-symbol.db"
    store = _run_with_open_account(db)
    order = _order(symbol="BTC/ USD")
    _run(store.register_paper_order(order))

    with pytest.raises(PaperOrderAcceptanceStateError, match="canonical BASE/QUOTE"):
        _run(store.accept_paper_order(order.order_id))

    assert _run(store.load_paper_order(order.order_id)) == order
    assert _run(store.load_paper_reservation(order.order_id)) is None
    balance = _run(store.load_paper_balance_view("account-1"))
    assert balance is not None
    assert balance.balances[0].available == Decimal("100.00")
    conn = sqlite3.connect(str(db))
    assert conn.execute("SELECT COUNT(*) FROM paper_order_acceptances").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM paper_reservations").fetchone()[0] == 0
    conn.close()
    _run(store.close())


def test_acceptance_retry_uses_original_payload_after_balance_and_order_change(
    tmp_path: Path,
) -> None:
    db = tmp_path / "stable-acceptance-retry.db"
    store = _run_with_open_account(db)
    original = _order()
    _run(store.register_paper_order(original))
    _run(store.accept_paper_order(original.order_id))

    # Simulate later fill processing: current order and active reservation change,
    # but the immutable acceptance snapshot remains the retry identity.
    fill = PaperFill(
        fill_id="fill-after-acceptance",
        order_id=original.order_id,
        qty=Decimal("0.50"),
        price=Decimal("10.00"),
        timestamp_ms=101,
        commission=None,
    )
    current_order = PaperOrder.model_validate(
        {
            **original.model_dump(mode="python"),
            "status": PaperOrderStatus.PARTIALLY_FILLED,
            "fills": (fill,),
        }
    )
    current_balance = AssetBalance(
        asset="USD",
        total=Decimal("95.00"),
        available=Decimal("80.00"),
        reserved=Decimal("15.00"),
    )
    current_reservation = PaperReservationRef(
        reservation_id=original.order_id,
        owner_order_id=original.order_id,
        amount=ReservationAmount(asset="USD", amount=Decimal("15.00")),
    )
    conn = sqlite3.connect(str(db))
    conn.execute(
        "UPDATE paper_orders SET order_json = ? WHERE order_id = ?",
        (current_order.model_dump_json(), original.order_id),
    )
    conn.execute(
        "UPDATE paper_account_balances SET balance_json = ? "
        "WHERE account_id = ? AND asset = ?",
        (current_balance.model_dump_json(), "account-1", "USD"),
    )
    conn.execute(
        "UPDATE paper_reservations SET amount_json = ?, reservation_json = ? "
        "WHERE reservation_id = ?",
        (
            current_reservation.amount.model_dump_json(),
            current_reservation.model_dump_json(),
            original.order_id,
        ),
    )
    conn.commit()
    acceptance_payload_json = conn.execute(
        "SELECT acceptance_payload_json FROM paper_order_acceptances WHERE order_id = ?",
        (original.order_id,),
    ).fetchone()[0]
    conn.close()
    acceptance_payload = json.loads(acceptance_payload_json)
    assert acceptance_payload["initial_order"]["status"] == "prepared"
    assert "available_before" not in acceptance_payload

    retried_acceptance = _run(store.accept_paper_order(original.order_id))
    retried_registration = _run(store.register_paper_order(original))
    assert retried_acceptance.model_dump_json() == current_order.model_dump_json()
    assert retried_registration.model_dump_json() == current_order.model_dump_json()
    loaded_balance = _run(store.load_paper_balance_view("account-1"))
    loaded_reservation = _run(store.load_paper_reservation(original.order_id))
    assert loaded_balance is not None
    assert (loaded_balance.balances[0].total, loaded_balance.balances[0].reserved) == (
        Decimal("95.00"), Decimal("15.00")
    )
    assert loaded_reservation == current_reservation
    _run(store.close())


def test_conflicting_initial_order_after_acceptance_does_not_overwrite(
    tmp_path: Path,
) -> None:
    db = tmp_path / "acceptance-order-conflict.db"
    store = _run_with_open_account(db)
    original = _order()
    _run(store.register_paper_order(original))
    accepted = _run(store.accept_paper_order(original.order_id))

    conflicting = _order(price=Decimal("11.00"))
    with pytest.raises(PaperOrderContextConflictError):
        _run(store.register_paper_order(conflicting))
    assert _run(store.load_paper_order(original.order_id)) == accepted
    balance = _run(store.load_paper_balance_view("account-1"))
    assert balance is not None
    assert balance.balances[0].reserved == Decimal("20.0000")
    _run(store.close())


def test_insufficient_funds_leaves_order_balance_and_reservation_unchanged(
    tmp_path: Path,
) -> None:
    db = tmp_path / "acceptance-insufficient.db"
    store = _run_with_open_account(db, Decimal("10.00"))
    order = _order()
    _run(store.register_paper_order(order))

    with pytest.raises(PaperOrderAcceptanceFundsError):
        _run(store.accept_paper_order(order.order_id))

    assert _run(store.load_paper_order(order.order_id)) == order
    assert _run(store.load_paper_reservation(order.order_id)) is None
    balance = _run(store.load_paper_balance_view("account-1"))
    assert balance is not None
    assert balance.balances[0] == AssetBalance(
        asset="USD",
        total=Decimal("10.00"),
        available=Decimal("10.00"),
        reserved=Decimal("0"),
    )
    conn = sqlite3.connect(str(db))
    assert conn.execute("SELECT COUNT(*) FROM paper_order_acceptances").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM paper_reservations").fetchone()[0] == 0
    conn.close()
    _run(store.close())


def test_sell_and_protected_orders_are_outside_atomic_buy_acceptance_slice(
    tmp_path: Path,
) -> None:
    db = tmp_path / "acceptance-scope.db"
    store = _run_with_open_account(db)
    buy = _order()
    sell_allocation = LotAllocation(
        position_id="position-1",
        account_id="account-1",
        symbol="BTC/USD",
        strategy_ref=StrategyRef(strategy_id="strategy-1", strategy_version="v1"),
        qty=Decimal("2.00"),
    )
    sell = PaperOrder.model_validate(
        {
            **buy.model_dump(mode="python"),
            "order_id": "sell-order",
            "client_order_id": "sell-client",
            "side": PaperOrderSide.SELL,
            "lot_allocations": (sell_allocation,),
        }
    )
    _run(store.register_paper_order(sell))

    with pytest.raises(PaperOrderAcceptanceScopeError, match="BUY orders only"):
        _run(store.accept_paper_order(sell.order_id))
    loaded_sell = _run(store.load_paper_order(sell.order_id))
    assert loaded_sell is not None
    assert loaded_sell.model_dump_json() == sell.model_dump_json()
    assert _run(store.load_paper_reservation(sell.order_id)) is None

    protected_buy = _order(
        order_id="protected-buy",
        client_order_id="protected-client",
    ).model_copy(update={"protection_group_id": "oco-1"})
    _run(store.register_paper_order(protected_buy))
    with pytest.raises(PaperOrderAcceptanceScopeError, match="protection-group"):
        _run(store.accept_paper_order(protected_buy.order_id))
    assert _run(store.load_paper_reservation(protected_buy.order_id)) is None
    _run(store.close())


def test_concurrent_acceptance_of_same_order_reserves_once(tmp_path: Path) -> None:
    db = tmp_path / "acceptance-same-order-race.db"
    store_a = _run_with_open_account(db)
    order = _order()
    _run(store_a.register_paper_order(order))
    store_b = SQLiteStore(db)
    _run(store_b.initialize())

    async def _accept_both():
        return await asyncio.gather(
            store_a.accept_paper_order(order.order_id),
            store_b.accept_paper_order(order.order_id),
            return_exceptions=True,
        )

    results = _run(_accept_both())
    assert results == [results[0], results[0]]
    assert isinstance(results[0], PaperOrder)
    assert results[0].status == PaperOrderStatus.ACCEPTED
    assert _run(store_a.load_paper_reservation(order.order_id)) is not None
    balance = _run(store_a.load_paper_balance_view("account-1"))
    assert balance is not None
    assert (balance.balances[0].available, balance.balances[0].reserved) == (
        Decimal("80.0000"), Decimal("20.0000")
    )
    conn = sqlite3.connect(str(db))
    assert conn.execute("SELECT COUNT(*) FROM paper_order_acceptances").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM paper_reservations").fetchone()[0] == 1
    conn.close()
    _run(store_a.close())
    _run(store_b.close())


def test_concurrent_competing_orders_cannot_double_reserve(tmp_path: Path) -> None:
    db = tmp_path / "acceptance-competing-orders.db"
    store_a = _run_with_open_account(db, Decimal("100.00"))
    store_b = SQLiteStore(db)
    _run(store_b.initialize())
    order_a = _order(order_id="order-a", client_order_id="client-a", qty=Decimal("8"))
    order_b = _order(order_id="order-b", client_order_id="client-b", qty=Decimal("8"))
    _run(store_a.register_paper_order(order_a))
    _run(store_a.register_paper_order(order_b))

    async def _accept_competitors():
        return await asyncio.gather(
            store_a.accept_paper_order(order_a.order_id),
            store_b.accept_paper_order(order_b.order_id),
            return_exceptions=True,
        )

    results = _run(_accept_competitors())
    assert sum(isinstance(result, PaperOrder) for result in results) == 1
    assert sum(
        isinstance(result, PaperOrderAcceptanceFundsError) for result in results
    ) == 1
    balance = _run(store_a.load_paper_balance_view("account-1"))
    assert balance is not None
    assert (balance.balances[0].available, balance.balances[0].reserved) == (
        Decimal("20.00"), Decimal("80"),
    )
    conn = sqlite3.connect(str(db))
    assert conn.execute("SELECT COUNT(*) FROM paper_order_acceptances").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM paper_reservations").fetchone()[0] == 1
    conn.close()
    _run(store_a.close())
    _run(store_b.close())


def test_acceptance_rolls_back_reservation_balance_and_order_together(
    tmp_path: Path,
) -> None:
    db = tmp_path / "acceptance-rollback-three-writes.db"
    store = _run_with_open_account(db)
    order = _order()
    _run(store.register_paper_order(order))
    conn = sqlite3.connect(str(db))
    conn.executescript("""
        CREATE TRIGGER fail_order_acceptance_update
        BEFORE UPDATE OF order_json ON paper_orders
        WHEN NEW.order_id = 'order-1'
        BEGIN
            SELECT RAISE(ABORT, 'injected order update failure');
        END;
    """)
    conn.close()

    with pytest.raises(sqlite3.IntegrityError, match="injected order update failure"):
        _run(store.accept_paper_order(order.order_id))

    assert _run(store.load_paper_order(order.order_id)) == order
    assert _run(store.load_paper_reservation(order.order_id)) is None
    balance = _run(store.load_paper_balance_view("account-1"))
    assert balance is not None
    assert (balance.balances[0].available, balance.balances[0].reserved) == (
        Decimal("100.00"), Decimal("0")
    )
    conn = sqlite3.connect(str(db))
    assert conn.execute("SELECT COUNT(*) FROM paper_order_acceptances").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM paper_reservations").fetchone()[0] == 0
    conn.close()
    _run(store.close())


def _make_v8_database(db: Path) -> tuple[PaperOrder, PaperAccountOpening]:
    """Create the exact historical v8 schema with accepted prior PAPER data."""
    store = _run_with_open_account(db)
    order = _order(order_id="order-before-v9", client_order_id="client-before-v9")
    _run(store.register_paper_order(order))
    opening = _run(store.load_paper_account_opening("account-1"))
    assert opening is not None
    _run(store.close())

    conn = sqlite3.connect(str(db))
    conn.execute("DROP TABLE paper_fill_applications")
    conn.execute("DROP TABLE position_lots")
    conn.execute("DROP TABLE paper_reservations")
    conn.execute("DROP TABLE paper_order_acceptances")
    conn.execute("PRAGMA user_version = 8")
    conn.commit()
    conn.close()
    assert _table_names(db) == _SCHEMA_V8_TABLES
    return order, opening


def test_direct_v8_to_v10_migration_preserves_previous_paper_slices(
    tmp_path: Path,
) -> None:
    db = tmp_path / "v8-to-v10.db"
    order, opening = _make_v8_database(db)
    store = SQLiteStore(db)

    _run(store.initialize())
    assert _table_names(db) == _SCHEMA_V10_TABLES
    assert _run(store.load_paper_order(order.order_id)) == order
    assert _run(store.load_paper_account_opening(opening.account_id)) == opening
    assert _run(store.load_paper_reservation(order.order_id)) is None
    conn = sqlite3.connect(str(db))
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 10
    assert conn.execute("SELECT COUNT(*) FROM paper_order_acceptances").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM paper_reservations").fetchone()[0] == 0
    conn.close()
    _run(store.close())


def test_v8_to_v10_migration_rolls_back_on_v9_ddl_failure_and_version(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = tmp_path / "v8-to-v10-rollback.db"
    order, opening = _make_v8_database(db)
    original_ddl = sqlite_store_module._PAPER_ORDER_RESERVATION_DDL_STATEMENTS
    monkeypatch.setattr(
        sqlite_store_module,
        "_PAPER_ORDER_RESERVATION_DDL_STATEMENTS",
        (original_ddl[0], "THIS IS INVALID SQL"),
    )
    store = SQLiteStore(db)

    with pytest.raises(sqlite3.OperationalError):
        _run(store.initialize())

    assert _table_names(db) == _SCHEMA_V8_TABLES
    conn = sqlite3.connect(str(db))
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 8
    stored_order = conn.execute(
        "SELECT order_json FROM paper_orders WHERE order_id = ?",
        (order.order_id,),
    ).fetchone()
    stored_opening = conn.execute(
        "SELECT opening_json FROM paper_account_openings WHERE account_id = ?",
        (opening.account_id,),
    ).fetchone()
    assert stored_order is not None
    assert stored_opening is not None
    assert PaperOrder.model_validate_json(stored_order[0]) == order
    assert PaperAccountOpening.model_validate_json(stored_opening[0]) == opening
    conn.close()
    _run(store.close())
