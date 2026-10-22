"""Tests for atomic PAPER order acceptance and principal reservation (schema v9)."""

from __future__ import annotations

import asyncio
import json
import sqlite3
from decimal import Decimal
from pathlib import Path
from typing import Any
from unittest import mock

import aiosqlite
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
from src.models.paper_risk import PaperRiskPolicy, PaperRiskSnapshot
from src.models.paper_symbol_filters import (
    LotSizeFilter, MinNotionalFilter, MarketLotSizeFilter, PriceFilter, MarketNotionalReference,
    SymbolFilterSnapshot,
)
from src.persistence import sqlite_store as sqlite_store_module
from src.persistence.sqlite_store import (
    PaperOrderContextConflictError,
    PaperOrderAcceptanceConflictError,
    PaperOrderAcceptanceFundsError,
    PaperOrderAcceptanceGateError,
    PaperOrderAcceptanceIntegrityError,
    PaperOrderAcceptanceScopeError,
    PaperOrderAcceptanceStateError,
    SQLiteStore,
    _SCHEMA_V12_TABLES,
    _SCHEMA_V12_TABLES,
    _SCHEMA_V8_TABLES,
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


def _gate_inputs(order: PaperOrder, max_exposure: Decimal = Decimal("1000000")):
    risk_policy = PaperRiskPolicy(
        policy_id="fixture", policy_version="1", account_id=order.paper_account_id,
        quote_asset=order.symbol.split("/")[1], max_order_notional=Decimal("1000000"),
        max_total_exposure=max_exposure, max_daily_loss=Decimal("1000000"),
    )
    risk_snapshot = PaperRiskSnapshot(
        account_id=order.paper_account_id, quote_asset=order.symbol.split("/")[1],
        market_symbol=order.symbol, market_mark_price=Decimal("10"), market_reference="fixture-market",
        market_timestamp_ms=1, equity_reference="fixture-equity", equity_timestamp_ms=1,
        equity_utc_day_start=Decimal("1000000"), equity_current_marked=Decimal("1000000"),
        exposure_reference="fixture-exposure", exposure_timestamp_ms=1,
        marked_open_inventory=Decimal("0"), remaining_buy_reservations=Decimal("999999"),
    )
    base, quote = order.symbol.split("/")
    filters = SymbolFilterSnapshot(
        snapshot_id="fixture-snapshot", filter_set_id="fixture-set", filter_set_version="1",
        source_id="fixture-source", source_version="1", capture_timestamp_ms=1,
        symbol=order.symbol, base_asset=base, quote_asset=quote,
        price_filter=PriceFilter(min_price=Decimal("0"), max_price=Decimal("0"), tick_size=Decimal("0")),
        lot_size=LotSizeFilter(min_qty=Decimal("0.0000000001"), max_qty=Decimal("100000000"), step_size=order.requested_qty),
        market_lot_size=MarketLotSizeFilter(min_qty=Decimal("0.0000000001"), max_qty=Decimal("100000000"), step_size=order.requested_qty),
        min_notional=MinNotionalFilter(min_notional=Decimal("0.00000001"), applies_to_market=order.order_type == PaperOrderType.MARKET, avg_price_mins=0),
        notional=None, unhandled_filter_types=(),
    )
    market_refs = (MarketNotionalReference(
        symbol=order.symbol, price=Decimal("10"), source="fixture-market",
        source_version="1", timestamp_ms=1, avg_price_mins=0,
    ) if order.order_type == PaperOrderType.MARKET else None)
    return risk_policy, risk_snapshot, filters, market_refs


def _accept(store: SQLiteStore, order_id: str):
    order = _run(store.load_paper_order(order_id))
    return _run(store.accept_paper_order(order_id, *_gate_inputs(order)))


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


def _assert_no_unknown_state(db: Path) -> None:
    """The local acceptance slice never persists an indeterminate state."""
    conn = sqlite3.connect(str(db))
    try:
        for (table_name,) in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ):
            rows = conn.execute(f"SELECT * FROM {table_name}").fetchall()
            assert not any(
                "unknown_state" in str(value).lower()
                for row in rows
                for value in row
            ), f"UNKNOWN_STATE persisted in {table_name}"
    finally:
        conn.close()


def test_limit_buy_acceptance_reserves_exact_quote_principal(tmp_path: Path) -> None:
    db = tmp_path / "limit-buy-acceptance.db"
    store = _run_with_open_account(db)
    order = _order()
    _run(store.register_paper_order(order))

    accepted = _accept(store, order.order_id)
    assert accepted.status == PaperOrderStatus.ACCEPTED
    assert not accepted.fills
    reservation = _run(store.load_paper_reservation(order.order_id))
    assert reservation is not None
    assert reservation.reservation_id == order.order_id
    assert reservation.owner_order_id == order.order_id
    assert reservation.amount == ReservationAmount(asset="USD", amount=Decimal("20.0000"))
    conn = sqlite3.connect(str(db))
    payload = json.loads(conn.execute(
        "SELECT acceptance_payload_json FROM paper_order_acceptances WHERE order_id = ?",
        (order.order_id,),
    ).fetchone()[0])
    conn.close()
    assert payload["schema_version"] == "paper_order_acceptance.v2"
    receipt = payload["gate_receipt"]
    assert receipt["risk_approved"] == "risk_approved"
    assert receipt["risk_policy_id"] == "fixture"
    assert receipt["symbol_filter_approved"] == "symbol_filter_approved"
    assert receipt["filter_snapshot_id"] == "fixture-snapshot"
    assert "max_order_notional" not in json.dumps(receipt)
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

    accepted = _accept(store, order.order_id)
    assert accepted.status == PaperOrderStatus.ACCEPTED
    reservation = _run(store.load_paper_reservation(order.order_id))
    assert reservation is not None
    assert reservation.amount == ReservationAmount(
        asset="USD", amount=Decimal("175.25")
    )
    conn = sqlite3.connect(str(db))
    receipt = json.loads(conn.execute(
        "SELECT acceptance_payload_json FROM paper_order_acceptances WHERE order_id = ?",
        (order.order_id,),
    ).fetchone()[0])["gate_receipt"]
    conn.close()
    assert receipt["market_references"] == [{
        "symbol": "BTC/USD", "source": "fixture-market", "source_version": "1",
        "timestamp_ms": 1, "avg_price_mins": 0,
    }]
    assert "10" not in json.dumps(receipt["market_references"])
    _run(store.close())


def test_v2_gate_receipt_rejects_persisted_limit_values(tmp_path: Path) -> None:
    db = tmp_path / "acceptance-v2-receipt-corrupt.db"
    store = _run_with_open_account(db)
    order = _order(order_id="receipt-integrity", client_order_id="receipt-integrity-client")
    _run(store.register_paper_order(order))
    _accept(store, order.order_id)

    conn = sqlite3.connect(str(db))
    payload_text = conn.execute(
        "SELECT acceptance_payload_json FROM paper_order_acceptances WHERE order_id = ?",
        (order.order_id,),
    ).fetchone()[0]
    payload = json.loads(payload_text)
    payload["gate_receipt"]["max_total_exposure"] = "1000000"
    conn.execute(
        "UPDATE paper_order_acceptances SET acceptance_payload_json = ? WHERE order_id = ?",
        (json.dumps(payload, sort_keys=True, separators=(",", ":")), order.order_id),
    )
    conn.commit()
    conn.close()

    with pytest.raises(PaperOrderAcceptanceIntegrityError, match="unsupported fields"):
        _run(store.load_paper_reservation(order.order_id))
    _run(store.close())


@pytest.mark.parametrize("failure", ["missing", "risk", "filter"])
def test_gate_rejection_is_typed_and_leaves_prepared_state_unchanged(
    tmp_path: Path, failure: str,
) -> None:
    db = tmp_path / f"gate-rejection-{failure}.db"
    store = _run_with_open_account(db)
    order = _order(order_id=f"gate-{failure}", client_order_id=f"gate-client-{failure}")
    _run(store.register_paper_order(order))
    if failure == "missing":
        args = ()
        expected = "risk_policy_missing"
    else:
        policy, snapshot, filters, market_refs = _gate_inputs(order)
        if failure == "risk":
            policy = policy.model_copy(update={"max_order_notional": Decimal("1")})
            expected = "risk_order_size_limit"
        else:
            filters = filters.model_copy(update={
                "lot_size": LotSizeFilter(min_qty=Decimal("3"), max_qty=Decimal("100"), step_size=Decimal("1"))
            })
            expected = "symbol_filter_violation"
        args = (policy, snapshot, filters, market_refs)
    with pytest.raises(PaperOrderAcceptanceGateError) as captured:
        _run(store.accept_paper_order(order.order_id, *args))
    error = captured.value
    assert error.code == expected
    if failure == "missing":
        assert error.risk_result.code == "risk_policy_missing"
        assert error.filter_result.code == "symbol_filter_missing"
    elif failure == "risk":
        assert error.risk_result.code == expected
        assert error.filter_result.code == "symbol_filter_approved"
    else:
        assert error.risk_result.code == "risk_approved"
        assert error.filter_result.code == expected
    assert _run(store.load_paper_order(order.order_id)) == order
    assert _run(store.load_paper_reservation(order.order_id)) is None
    balance = _run(store.load_paper_balance_view("account-1"))
    assert balance is not None
    assert (balance.balances[0].available, balance.balances[0].reserved) == (Decimal("100.00"), Decimal("0"))
    conn = sqlite3.connect(str(db))
    assert conn.execute("SELECT COUNT(*) FROM paper_order_acceptances").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM paper_reservations").fetchone()[0] == 0
    conn.close()
    _run(store.close())


def test_buy_reservation_rejects_ambiguous_quote_symbol_without_writes(
    tmp_path: Path,
) -> None:
    db = tmp_path / "ambiguous-quote-symbol.db"
    store = _run_with_open_account(db)
    order = _order(symbol="BTC/ USD")
    _run(store.register_paper_order(order))

    with pytest.raises(PaperOrderAcceptanceStateError, match="canonical BASE/QUOTE"):
        _accept(store, order.order_id)

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
    _accept(store, original.order_id)

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

    retried_acceptance = _accept(store, original.order_id)
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


def test_legacy_v1_acceptance_payload_remains_loadable_and_idempotent(tmp_path: Path) -> None:
    db = tmp_path / "acceptance-v1-legacy.db"
    store = _run_with_open_account(db)
    order = _order(order_id="legacy-v1", client_order_id="legacy-v1-client")
    _run(store.register_paper_order(order))
    accepted = _accept(store, order.order_id)
    conn = sqlite3.connect(str(db))
    row = conn.execute(
        "SELECT acceptance_payload_json FROM paper_order_acceptances WHERE order_id = ?",
        (order.order_id,),
    ).fetchone()
    payload = json.loads(row[0])
    payload["schema_version"] = "paper_order_acceptance.v1"
    payload.pop("gate_receipt")
    conn.execute(
        "UPDATE paper_order_acceptances SET acceptance_payload_json = ? WHERE order_id = ?",
        (json.dumps(payload, sort_keys=True, separators=(",", ":")), order.order_id),
    )
    conn.commit()
    conn.close()
    # Idempotent retry recognizes the unchanged historical v1 receipt without inputs.
    assert _run(store.accept_paper_order(order.order_id)) == accepted
    _run(store.close())


def test_conflicting_initial_order_after_acceptance_does_not_overwrite(
    tmp_path: Path,
) -> None:
    db = tmp_path / "acceptance-order-conflict.db"
    store = _run_with_open_account(db)
    original = _order()
    _run(store.register_paper_order(original))
    accepted = _accept(store, original.order_id)

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
        _accept(store, order.order_id)

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


def test_protected_buy_orders_are_outside_atomic_acceptance_slice(
    tmp_path: Path,
) -> None:
    db = tmp_path / "acceptance-scope.db"
    store = _run_with_open_account(db)
    protected_buy = _order(
        order_id="protected-buy",
        client_order_id="protected-client",
    ).model_copy(update={"protection_group_id": "oco-1"})
    _run(store.register_paper_order(protected_buy))
    with pytest.raises(PaperOrderAcceptanceScopeError, match="protection-group"):
        _accept(store, protected_buy.order_id)
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
            store_a.accept_paper_order(order.order_id, *_gate_inputs(order)),
            store_b.accept_paper_order(order.order_id, *_gate_inputs(order)),
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
            store_a.accept_paper_order(order_a.order_id, *_gate_inputs(order_a)),
            store_b.accept_paper_order(order_b.order_id, *_gate_inputs(order_b)),
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


@pytest.mark.parametrize(
    ("max_exposure", "accepted_count", "available", "reserved"),
    [
        (Decimal("100"), 1, Decimal("120"), Decimal("80")),
        (Decimal("160"), 2, Decimal("40"), Decimal("160")),
    ],
)
def test_concurrent_buy_risk_uses_transaction_local_reservations(
    tmp_path: Path, max_exposure: Decimal, accepted_count: int,
    available: Decimal, reserved: Decimal,
) -> None:
    db = tmp_path / "acceptance-risk-race.db"
    store_a = _run_with_open_account(db, Decimal("200"))
    store_b = SQLiteStore(db)
    _run(store_b.initialize())
    orders = (
        _order(order_id="risk-a", client_order_id="risk-client-a", qty=Decimal("8")),
        _order(order_id="risk-b", client_order_id="risk-client-b", qty=Decimal("8")),
    )
    for order in orders:
        _run(store_a.register_paper_order(order))

    async def accept_both():
        return await asyncio.gather(*(
            store.accept_paper_order(order.order_id, *_gate_inputs(order, max_exposure))
            for store, order in zip((store_a, store_b), orders)
        ), return_exceptions=True)

    results = _run(accept_both())
    assert sum(isinstance(result, PaperOrder) for result in results) == accepted_count
    assert sum(isinstance(result, PaperOrderAcceptanceGateError) for result in results) == 2 - accepted_count
    balance = _run(store_a.load_paper_balance_view("account-1"))
    assert balance is not None
    assert (balance.balances[0].available, balance.balances[0].reserved) == (available, reserved)
    statuses = [_run(store_a.load_paper_order(order.order_id)).status for order in orders]
    assert statuses.count(PaperOrderStatus.ACCEPTED) == accepted_count
    assert statuses.count(PaperOrderStatus.PREPARED) == 2 - accepted_count
    conn = sqlite3.connect(str(db))
    assert conn.execute("SELECT COUNT(*) FROM paper_order_acceptances").fetchone()[0] == accepted_count
    assert conn.execute("SELECT COUNT(*) FROM paper_reservations").fetchone()[0] == accepted_count
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
        _accept(store, order.order_id)

    assert _run(store.load_paper_order(order.order_id)) == order
    assert _run(store.load_paper_reservation(order.order_id)) is None
    balance = _run(store.load_paper_balance_view("account-1"))
    assert balance is not None
    assert (balance.balances[0].available, balance.balances[0].reserved) == (
        Decimal("100.00"), Decimal("0")
    )
    _assert_no_unknown_state(db)
    conn = sqlite3.connect(str(db))
    assert conn.execute("SELECT COUNT(*) FROM paper_order_acceptances").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM paper_reservations").fetchone()[0] == 0
    conn.close()
    _run(store.close())


def test_acceptance_precommit_failure_rolls_back_and_retry_reserves_once(
    tmp_path: Path,
) -> None:
    """A lost-before-commit acceptance is rolled back and safely retried."""
    db = tmp_path / "acceptance-precommit-uncertainty.db"
    store = _run_with_open_account(db)
    order = _order(order_id="accept-precommit", client_order_id="accept-precommit-client")
    _run(store.register_paper_order(order))

    real_commit = aiosqlite.Connection.commit
    commit_calls = 0

    async def fail_first_commit(self, *args, **kwargs):
        nonlocal commit_calls
        commit_calls += 1
        if commit_calls == 1:
            raise OSError("synthetic acceptance pre-commit failure")
        return await real_commit(self, *args, **kwargs)

    with mock.patch.object(aiosqlite.Connection, "commit", new=fail_first_commit):
        with pytest.raises(OSError, match="acceptance pre-commit failure"):
            _accept(store, order.order_id)
    assert commit_calls == 1

    # The failed transaction left no partial acceptance side effects.
    assert _run(store.load_paper_order(order.order_id)) == order
    assert _run(store.load_paper_reservation(order.order_id)) is None
    balance = _run(store.load_paper_balance_view("account-1"))
    assert balance is not None
    assert (balance.balances[0].available, balance.balances[0].reserved) == (
        Decimal("100.00"), Decimal("0")
    )

    accepted = _accept(store, order.order_id)
    assert accepted.status == PaperOrderStatus.ACCEPTED
    assert _accept(store, order.order_id) == accepted
    conn = sqlite3.connect(str(db))
    assert conn.execute(
        "SELECT COUNT(*) FROM paper_order_acceptances WHERE order_id = ?",
        (order.order_id,),
    ).fetchone()[0] == 1
    assert conn.execute(
        "SELECT COUNT(*) FROM paper_reservations WHERE owner_order_id = ?",
        (order.order_id,),
    ).fetchone()[0] == 1
    assert conn.execute(
        "SELECT COUNT(*) FROM paper_account_balances "
        "WHERE account_id = 'account-1' AND asset = 'USD'"
    ).fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM paper_fills").fetchone()[0] == 0
    conn.close()
    _assert_no_unknown_state(db)
    _run(store.close())


def test_acceptance_postcommit_failure_reloads_and_is_idempotent(
    tmp_path: Path,
) -> None:
    """A post-commit acknowledgement failure is resolved from durable state."""
    db = tmp_path / "acceptance-postcommit-uncertainty.db"
    store = _run_with_open_account(db)
    order = _order(order_id="accept-postcommit", client_order_id="accept-postcommit-client")
    _run(store.register_paper_order(order))

    real_commit = aiosqlite.Connection.commit
    commit_calls = 0

    async def commit_then_fail(self, *args, **kwargs):
        nonlocal commit_calls
        commit_calls += 1
        await real_commit(self, *args, **kwargs)
        if commit_calls == 1:
            raise OSError("synthetic acceptance post-commit failure")

    with mock.patch.object(aiosqlite.Connection, "commit", new=commit_then_fail):
        with pytest.raises(OSError, match="acceptance post-commit failure"):
            _accept(store, order.order_id)
    assert commit_calls == 1

    _assert_no_unknown_state(db)
    # Reopen before retrying: the durable acceptance, reservation and balance
    # mutation are the source of truth after the lost acknowledgement.
    _run(store.close())
    reopened = SQLiteStore(db)
    _run(reopened.initialize())
    accepted = _accept(reopened, order.order_id)
    assert accepted.status == PaperOrderStatus.ACCEPTED
    assert _accept(reopened, order.order_id) == accepted
    reservation = _run(reopened.load_paper_reservation(order.order_id))
    assert reservation is not None
    assert reservation.amount.amount == Decimal("20.0000")
    balance = _run(reopened.load_paper_balance_view("account-1"))
    assert balance is not None
    assert (balance.balances[0].available, balance.balances[0].reserved) == (
        Decimal("80.0000"), Decimal("20.0000")
    )
    conn = sqlite3.connect(str(db))
    assert conn.execute("SELECT COUNT(*) FROM paper_order_acceptances").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM paper_reservations").fetchone()[0] == 1
    assert conn.execute(
        "SELECT COUNT(*) FROM paper_account_balances "
        "WHERE account_id = 'account-1' AND asset = 'USD'"
    ).fetchone()[0] == 1
    conn.close()
    _assert_no_unknown_state(db)
    _run(reopened.close())


def _make_v8_database(db: Path) -> tuple[PaperOrder, PaperAccountOpening]:
    """Create the exact historical v8 schema with accepted prior PAPER data."""
    store = _run_with_open_account(db)
    order = _order(order_id="order-before-v9", client_order_id="client-before-v9")
    _run(store.register_paper_order(order))
    opening = _run(store.load_paper_account_opening("account-1"))
    assert opening is not None
    _run(store.close())

    conn = sqlite3.connect(str(db))
    conn.execute("DROP TABLE IF EXISTS paper_fill_evaluations")
    conn.execute("DROP TABLE IF EXISTS paper_fill_applications")
    conn.execute("DROP TABLE IF EXISTS position_lots")
    conn.execute("DROP TABLE IF EXISTS paper_reservations")
    conn.execute("DROP TABLE IF EXISTS paper_order_acceptances")
    conn.execute("DROP TABLE IF EXISTS paper_reservation_lot_allocations")
    conn.execute("DROP TABLE IF EXISTS paper_sell_accounting")
    conn.execute("PRAGMA user_version = 8")
    conn.commit()
    conn.close()
    # v8->v12 migration adds SELL tables on top of v8, so use subset check.
    assert _SCHEMA_V8_TABLES.issubset(_table_names(db))
    return order, opening


def test_direct_v8_to_v12_migration_preserves_previous_paper_slices(
    tmp_path: Path,
) -> None:
    db = tmp_path / "v8-to-v12.db"
    order, opening = _make_v8_database(db)
    store = SQLiteStore(db)

    _run(store.initialize())
    assert _table_names(db) == _SCHEMA_V12_TABLES
    assert _run(store.load_paper_order(order.order_id)) == order
    assert _run(store.load_paper_account_opening(opening.account_id)) == opening
    assert _run(store.load_paper_reservation(order.order_id)) is None
    conn = sqlite3.connect(str(db))
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 12
    assert conn.execute("SELECT COUNT(*) FROM paper_order_acceptances").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM paper_reservations").fetchone()[0] == 0
    conn.close()
    _run(store.close())


def test_v8_to_v12_migration_rolls_back_on_v9_ddl_failure_and_version(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = tmp_path / "v8-to-v12-rollback.db"
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
