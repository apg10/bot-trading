"""Fase 2 — Primer camino completo y atómico de fill totalmente ejecutado.

Cubre:
  - Fill MARKET completo dentro del max_quote_amount.
  - Fill LIMIT completo válido.
  - Rechazo por saldo de fee insuficiente con rollback.
  - Parciales rechazados sin efectos (no soportados en esta fase).
  - Idempotencia de aplicación repetida.

Gate: desde backend
  PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -B -m pytest -q -p no:cacheprovider --capture=sys tests/test_sqlite_store_paper_buy_fills.py
"""

from __future__ import annotations

import asyncio
import sqlite3
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from pydantic import TypeAdapter

from src.models.paper_contracts import (
    AssetBalance,
    Commission,
    PaperFill,
    PaperOrder,
    PaperOrderSide,
    PaperOrderStatus,
    PaperOrderType,
    PaperReservationRef,
    ReservationAmount,
)
from src.persistence.sqlite_store import (
    PaperFillIntegrityError,
    PaperOrderAcceptanceFundsError,
    PaperOrderAcceptanceStateError,
    SQLiteStore,
    _PAPER_ORDER_ADAPTER,
    _asset_balance_json_to_model,
)


# ── helpers ────────────────────────────────────────────────────────────────────

def _run(coro: Any) -> Any:
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _make_store(db: Path) -> SQLiteStore:
    store = SQLiteStore(db)
    _run(store.initialize())
    return store


def _open_account(
    store: SQLiteStore,
    account_id: str = "acc-1",
    session_id: str = "sess-1",
) -> None:
    """Open a paper account with QUOTE=10000 and BASE=0."""
    from src.models.paper_contracts import PaperAccountOpening, _PaperAccountOpeningAssetBalance

    quote_bal = _PaperAccountOpeningAssetBalance(
        asset="QUOTE", total=Decimal("10000"), available=Decimal("10000"), reserved=Decimal("0")
    )
    base_bal = _PaperAccountOpeningAssetBalance(
        asset="BASE", total=Decimal("0"), available=Decimal("0"), reserved=Decimal("0")
    )
    opening = PaperAccountOpening(
        environment="paper",
        account_id=account_id,
        balances=(quote_bal, base_bal),
    )
    _run(store.open_paper_account(opening))


def _make_order(
    order_id: str = "order-1",
    side: PaperOrderSide = PaperOrderSide.BUY,
    order_type: PaperOrderType = PaperOrderType.MARKET,
    requested_qty: Decimal | str = Decimal("2.0"),
    price: Decimal | str | None = None,
    max_quote_amount: Decimal | str | None = Decimal("300.0"),
    symbol: str = "BASE/QUOTE",
    position_id: str | None = None,
) -> PaperOrder:
    return _PAPER_ORDER_ADAPTER.validate_python({
        "order_id": order_id,
        "client_order_id": f"cli-{order_id}",
        "paper_account_id": "acc-1",
        "paper_session_id": "sess-1",
        "environment": "paper",
        "symbol": symbol,
        "side": side.value,
        "order_type": order_type.value,
        "requested_qty": Decimal(str(requested_qty)),
        "fills": [],
        "lot_allocations": [],
        "price": Decimal(str(price)) if price is not None else None,
        "trigger_price": None,
        "max_quote_amount": Decimal(str(max_quote_amount)) if max_quote_amount is not None else None,
        "strategy_ref": {"strategy_id": "strat-1", "strategy_version": "v1"},
        "decision_id": None,
        "status": PaperOrderStatus.PREPARED.value,
        "timestamp_ms": 1_700_000_000_000,
        "position_id": position_id,
        "protection_group_id": None,
    })


def _accept_order(store: SQLiteStore, order: PaperOrder) -> PaperOrder:
    """Accept an order (reserves QUOTE)."""
    return _run(store.accept_paper_order(order.order_id))


def _make_fill(
    fill_id: str = "fill-1",
    order_id: str = "order-1",
    qty: Decimal | str = Decimal("2.0"),
    price: Decimal | str = Decimal("100.0"),
    timestamp_ms: int = 1_700_000_001_000,
    commission: Commission | None = Commission(amount=Decimal("0.5"), asset="QUOTE"),
) -> PaperFill:
    return PaperFill(
        fill_id=fill_id,
        order_id=order_id,
        qty=Decimal(str(qty)),
        price=Decimal(str(price)),
        timestamp_ms=timestamp_ms,
        commission=commission,
    )


# ── Fase 2 tests ───────────────────────────────────────────────────────────────

def test_market_fill_complete_within_max_quote(tmp_path: Path) -> None:
    """MARKET fill completo dentro del max_quote_amount se aplica correctamente."""
    db = tmp_path / "market-complete.db"
    store = _make_store(db)
    _open_account(store)

    order = _make_order(
        order_type=PaperOrderType.MARKET,
        requested_qty="2.0",
        max_quote_amount="300.0",
    )
    _run(store.register_paper_order(order))
    _accept_order(store, order)

    fill = _make_fill(qty="2.0", price="100.0")
    _run(store.save_paper_fill(fill))

    applied = _run(store.apply_paper_fill("fill-1"))
    assert applied.fill_id == "fill-1"
    assert applied.qty == Decimal("2.0")

    # Principal is consumed from the reservation; the unused 100 is released.
    conn = sqlite3.connect(str(db))
    quote_bal = AssetBalance.model_validate_json(
        conn.execute("SELECT balance_json FROM paper_account_balances WHERE asset='QUOTE'").fetchone()[0]
    )
    base_bal = AssetBalance.model_validate_json(
        conn.execute("SELECT balance_json FROM paper_account_balances WHERE asset='BASE'").fetchone()[0]
    )
    # principal=200, commission=0.5, and the full reservation is consumed/released.
    # total = available = 10000 - 200.5 = 9799.5; reserved = 0.
    assert quote_bal.total == Decimal("9799.5")
    assert quote_bal.available == Decimal("9799.5")
    assert quote_bal.reserved == Decimal("0")
    assert conn.execute(
        "SELECT COUNT(*) FROM paper_reservations WHERE owner_order_id='order-1'"
    ).fetchone()[0] == 0
    assert base_bal.total == Decimal("2.0")
    assert base_bal.available == Decimal("2.0")

    # Verify order status
    order_row = conn.execute("SELECT order_json FROM paper_orders WHERE order_id='order-1'").fetchone()
    loaded_order = PaperOrder.model_validate_json(order_row[0])
    assert loaded_order.status == PaperOrderStatus.FILLED

    # Verify position_lots
    lot_row = conn.execute("SELECT lot_json FROM position_lots WHERE order_id='order-1'").fetchone()
    assert lot_row is not None
    from src.persistence.sqlite_store import PositionLot
    lot = PositionLot.model_validate_json(lot_row[0])
    assert lot.total_qty == Decimal("2.0")
    assert lot.total_cost == Decimal("200.0")

    # Verify durable application mark
    app_row = conn.execute("SELECT fill_id FROM paper_fill_applications WHERE fill_id='fill-1'").fetchone()
    assert app_row is not None

    conn.close()
    _run(store.close())


def test_limit_fill_complete_valid(tmp_path: Path) -> None:
    """Fill LIMIT completo válido se aplica correctamente."""
    db = tmp_path / "limit-complete.db"
    store = _make_store(db)
    _open_account(store)

    order = _make_order(
        order_type=PaperOrderType.LIMIT,
        requested_qty="1.0",
        price="50.0",
        max_quote_amount="100.0",
    )
    _run(store.register_paper_order(order))
    _accept_order(store, order)

    fill = _make_fill(fill_id="fill-limit", qty="1.0", price="50.0")
    _run(store.save_paper_fill(fill))

    applied = _run(store.apply_paper_fill("fill-limit"))
    assert applied.fill_id == "fill-limit"

    conn = sqlite3.connect(str(db))
    quote_bal = AssetBalance.model_validate_json(
        conn.execute("SELECT balance_json FROM paper_account_balances WHERE asset='QUOTE'").fetchone()[0]
    )
    # principal=50, commission=0.5; the full reservation is consumed/released.
    # total = available = 10000 - 50.5 = 9949.5; reserved = 0.
    assert quote_bal.total == Decimal("9949.5")
    assert quote_bal.available == Decimal("9949.5")
    assert quote_bal.reserved == Decimal("0")
    assert conn.execute(
        "SELECT COUNT(*) FROM paper_reservations WHERE owner_order_id='order-1'"
    ).fetchone()[0] == 0

    order_row = conn.execute("SELECT order_json FROM paper_orders WHERE order_id='order-1'").fetchone()
    loaded_order = PaperOrder.model_validate_json(order_row[0])
    assert loaded_order.status == PaperOrderStatus.FILLED
    conn.close()
    _run(store.close())


def test_insufficient_fee_funds_rejected_with_rollback(tmp_path: Path) -> None:
    """Rechazo por saldo de fee insuficiente deja todo intacto."""
    db = tmp_path / "insufficient-fee.db"
    store = _make_store(db)
    _open_account(store)

    order = _make_order(
        order_type=PaperOrderType.MARKET,
        requested_qty="2.0",
        max_quote_amount="300.0",
    )
    _run(store.register_paper_order(order))
    _accept_order(store, order)

    # The principal is reserved (300); commission must fit unreserved available=9700.
    fill = _make_fill(
        qty="2.0",
        price="100.0",
        commission=Commission(amount=Decimal("9701.0"), asset="QUOTE"),
    )
    _run(store.save_paper_fill(fill))

    with pytest.raises(PaperOrderAcceptanceFundsError, match="insufficient"):
        _run(store.apply_paper_fill("fill-1"))

    # Verify no changes: balances intact (available was already 9700 after acceptance)
    conn = sqlite3.connect(str(db))
    quote_bal = AssetBalance.model_validate_json(
        conn.execute("SELECT balance_json FROM paper_account_balances WHERE asset='QUOTE'").fetchone()[0]
    )
    assert quote_bal.total == Decimal("10000.0")  # Unchanged
    assert quote_bal.available == Decimal("9700.0")  # Unchanged (was 10000 - 300 reserved)
    assert quote_bal.reserved == Decimal("300.0")

    # No application mark created
    app_row = conn.execute("SELECT fill_id FROM paper_fill_applications WHERE fill_id='fill-1'").fetchone()
    assert app_row is None

    # Order still ACCEPTED
    order_row = conn.execute("SELECT order_json FROM paper_orders WHERE order_id='order-1'").fetchone()
    loaded_order = PaperOrder.model_validate_json(order_row[0])
    assert loaded_order.status == PaperOrderStatus.ACCEPTED

    conn.close()
    _run(store.close())


@pytest.mark.parametrize(
    "commission",
    [
        None,
        Commission(amount=Decimal("0.5"), asset="BASE"),
        Commission(amount=Decimal("0.5"), asset="BTC"),
    ],
    ids=("unknown", "base-asset", "third-asset"),
)
def test_unknown_or_non_quote_commission_rejected_without_effects(
    tmp_path: Path, commission: Commission | None
) -> None:
    db = tmp_path / f"unsupported-commission-{commission.asset if commission else 'unknown'}.db"
    store = _make_store(db)
    _open_account(store)

    order = _make_order(requested_qty="2.0", max_quote_amount="300.0")
    _run(store.register_paper_order(order))
    _accept_order(store, order)
    _run(store.save_paper_fill(_make_fill(qty="2.0", price="100.0", commission=commission)))

    with pytest.raises(PaperOrderAcceptanceStateError, match="known QUOTE|must match"):
        _run(store.apply_paper_fill("fill-1"))

    conn = sqlite3.connect(str(db))
    balance = AssetBalance.model_validate_json(
        conn.execute(
            "SELECT balance_json FROM paper_account_balances WHERE asset='QUOTE'"
        ).fetchone()[0]
    )
    assert balance.total == Decimal("10000.0")
    assert balance.available == Decimal("9700.0")
    assert balance.reserved == Decimal("300.0")
    assert conn.execute(
        "SELECT COUNT(*) FROM paper_fill_applications WHERE fill_id='fill-1'"
    ).fetchone()[0] == 0
    assert conn.execute(
        "SELECT COUNT(*) FROM paper_reservations WHERE owner_order_id='order-1'"
    ).fetchone()[0] == 1
    stored_order = PaperOrder.model_validate_json(
        conn.execute(
            "SELECT order_json FROM paper_orders WHERE order_id='order-1'"
        ).fetchone()[0]
    )
    assert stored_order.status == PaperOrderStatus.ACCEPTED
    assert stored_order.fills == []
    assert conn.execute(
        "SELECT COUNT(*) FROM position_lots WHERE order_id='order-1'"
    ).fetchone()[0] == 0
    conn.close()
    _run(store.close())


def test_two_partial_limit_fills_progress_reservation_across_reopen(tmp_path: Path) -> None:
    """Two partials consume current reserve, preserve acceptance, and complete after reopen."""
    db = tmp_path / "partial-progress.db"
    store = _make_store(db)
    _open_account(store)

    order = _make_order(
        order_type=PaperOrderType.LIMIT,
        requested_qty="2.0",
        price="50.0",
        max_quote_amount="150.0",
    )
    _run(store.register_paper_order(order))
    _accept_order(store, order)

    conn = sqlite3.connect(str(db))
    snapshot_before = conn.execute(
        "SELECT reservation_json FROM paper_order_acceptances WHERE order_id='order-1'"
    ).fetchone()[0]
    conn.close()

    first_fill = _make_fill(fill_id="fill-partial-1", qty="1.0", price="40.0")
    _run(store.save_paper_fill(first_fill))
    _run(store.apply_paper_fill(first_fill.fill_id))

    order_after_first = _run(store.load_paper_order(order.order_id))
    assert order_after_first is not None
    assert order_after_first.status == PaperOrderStatus.PARTIALLY_FILLED
    assert [fill.fill_id for fill in order_after_first.fills] == [first_fill.fill_id]
    reservation_after_first = _run(store.load_paper_reservation(order.order_id))
    assert reservation_after_first is not None
    assert reservation_after_first.amount.amount == Decimal("50.0")
    conn = sqlite3.connect(str(db))
    quote_after_first = AssetBalance.model_validate_json(
        conn.execute(
            "SELECT balance_json FROM paper_account_balances WHERE asset='QUOTE'"
        ).fetchone()[0]
    )
    assert quote_after_first.total == Decimal("9959.5")
    assert quote_after_first.available == Decimal("9909.5")
    assert quote_after_first.reserved == Decimal("50.0")
    conn.close()

    # Reapplying this same fill is a no-op even after the order has partial state.
    _run(store.apply_paper_fill(first_fill.fill_id))
    assert _run(store.load_paper_reservation(order.order_id)) == reservation_after_first

    _run(store.close())
    store = _make_store(db)
    second_fill = _make_fill(fill_id="fill-partial-2", qty="1.0", price="50.0")
    _run(store.save_paper_fill(second_fill))
    _run(store.apply_paper_fill(second_fill.fill_id))

    stored_order = _run(store.load_paper_order(order.order_id))
    assert stored_order is not None
    assert stored_order.status == PaperOrderStatus.FILLED
    assert [fill.fill_id for fill in stored_order.fills] == [
        first_fill.fill_id,
        second_fill.fill_id,
    ]
    assert _run(store.load_paper_reservation(order.order_id)) is None

    conn = sqlite3.connect(str(db))
    quote_bal = AssetBalance.model_validate_json(
        conn.execute("SELECT balance_json FROM paper_account_balances WHERE asset='QUOTE'").fetchone()[0]
    )
    assert quote_bal.total == Decimal("9909.0")
    assert quote_bal.available == Decimal("9909.0")
    assert quote_bal.reserved == Decimal("0")
    assert conn.execute(
        "SELECT COUNT(*) FROM paper_fill_applications WHERE order_id='order-1'"
    ).fetchone()[0] == 2
    snapshot_after = conn.execute(
        "SELECT reservation_json FROM paper_order_acceptances WHERE order_id='order-1'"
    ).fetchone()[0]
    assert snapshot_after == snapshot_before
    lot_row = conn.execute(
        "SELECT lot_json FROM position_lots WHERE order_id='order-1'"
    ).fetchone()
    assert lot_row is not None
    from src.persistence.sqlite_store import PositionLot
    lot = PositionLot.model_validate_json(lot_row[0])
    assert lot.total_qty == Decimal("2.0")
    assert lot.total_cost == Decimal("90.0")
    conn.close()
    _run(store.close())


def test_partial_overfill_rejects_journaled_fill_without_accounting_effects(
    tmp_path: Path,
) -> None:
    db = tmp_path / "partial-overfill.db"
    store = _make_store(db)
    _open_account(store)
    order = _make_order(
        order_type=PaperOrderType.LIMIT,
        requested_qty="2.0",
        price="50.0",
        max_quote_amount="100.0",
    )
    _run(store.register_paper_order(order))
    _accept_order(store, order)

    first = _make_fill(fill_id="overfill-first", qty="1.0", price="40.0")
    _run(store.save_paper_fill(first))
    _run(store.apply_paper_fill(first.fill_id))

    # Create and save the overfill fill
    overfill = _make_fill(fill_id="overfill-excess", qty="1.01", price="50.0")
    _run(store.save_paper_fill(overfill))

    conn = sqlite3.connect(str(db))

    # --- Snapshot AFTER save, BEFORE apply: complete rows ordered by keys ---
    balances_snapshot = [
        tuple(row)
        for row in conn.execute("SELECT * FROM paper_account_balances ORDER BY asset").fetchall()
    ]
    reservation_snapshot = [
        tuple(row)
        for row in conn.execute(
            "SELECT * FROM paper_reservations WHERE reservation_id=? ORDER BY reservation_id",
            (order.order_id,),
        ).fetchall()
    ]
    lot_snapshot = [
        tuple(row)
        for row in conn.execute(
            "SELECT * FROM position_lots WHERE order_id=? ORDER BY order_id",
            (order.order_id,),
        ).fetchall()
    ]
    order_snapshot = [
        tuple(row)
        for row in conn.execute(
            "SELECT * FROM paper_orders WHERE order_id=? ORDER BY order_id",
            (order.order_id,),
        ).fetchall()
    ]
    apps_snapshot = [
        tuple(row)
        for row in conn.execute(
            "SELECT * FROM paper_fill_applications WHERE order_id=? ORDER BY fill_id",
            (order.order_id,),
        ).fetchall()
    ]
    acceptance_snapshot = [
        tuple(row)
        for row in conn.execute(
            "SELECT * FROM paper_order_acceptances WHERE order_id=? ORDER BY order_id",
            (order.order_id,),
        ).fetchall()
    ]
    journal_overfill_snapshot = [
        tuple(row)
        for row in conn.execute(
            "SELECT * FROM paper_fills WHERE fill_id=? ORDER BY fill_id",
            (overfill.fill_id,),
        ).fetchall()
    ]
    conn.close()

    with pytest.raises(PaperOrderAcceptanceStateError, match="exceed requested quantity"):
        _run(store.apply_paper_fill(overfill.fill_id))

    # --- Post-rejection: compare exactly each snapshot with persisted state ---
    conn = sqlite3.connect(str(db))

    balances_post = [
        tuple(row)
        for row in conn.execute("SELECT * FROM paper_account_balances ORDER BY asset").fetchall()
    ]
    assert balances_post == balances_snapshot

    reservation_post = [
        tuple(row)
        for row in conn.execute(
            "SELECT * FROM paper_reservations WHERE reservation_id=? ORDER BY reservation_id",
            (order.order_id,),
        ).fetchall()
    ]
    assert reservation_post == reservation_snapshot

    lot_post = [
        tuple(row)
        for row in conn.execute(
            "SELECT * FROM position_lots WHERE order_id=? ORDER BY order_id",
            (order.order_id,),
        ).fetchall()
    ]
    assert lot_post == lot_snapshot

    order_post = [
        tuple(row)
        for row in conn.execute(
            "SELECT * FROM paper_orders WHERE order_id=? ORDER BY order_id",
            (order.order_id,),
        ).fetchall()
    ]
    assert order_post == order_snapshot

    apps_post = [
        tuple(row)
        for row in conn.execute(
            "SELECT * FROM paper_fill_applications WHERE order_id=? ORDER BY fill_id",
            (order.order_id,),
        ).fetchall()
    ]
    assert apps_post == apps_snapshot

    acceptance_post = [
        tuple(row)
        for row in conn.execute(
            "SELECT * FROM paper_order_acceptances WHERE order_id=? ORDER BY order_id",
            (order.order_id,),
        ).fetchall()
    ]
    assert acceptance_post == acceptance_snapshot

    # Journaled fill preserves same content and has no application mark
    journal_overfill_post = [
        tuple(row)
        for row in conn.execute(
            "SELECT * FROM paper_fills WHERE fill_id=? ORDER BY fill_id",
            (overfill.fill_id,),
        ).fetchall()
    ]
    assert journal_overfill_post == journal_overfill_snapshot
    # No application row for the overfill
    apps_for_overfill = conn.execute(
        "SELECT COUNT(*) FROM paper_fill_applications WHERE fill_id=?",
        (overfill.fill_id,),
    ).fetchone()[0]
    assert apps_for_overfill == 0

    conn.close()


def test_concurrent_distinct_partial_fills_apply_once_for_same_order(tmp_path: Path) -> None:
    db = tmp_path / "concurrent-partial-fills.db"
    store_a = _make_store(db)
    _open_account(store_a)
    order = _make_order(
        order_type=PaperOrderType.LIMIT,
        requested_qty="2.0",
        price="50.0",
        max_quote_amount="150.0",
    )
    _run(store_a.register_paper_order(order))
    _accept_order(store_a, order)
    fills = (
        _make_fill(fill_id="concurrent-partial-1", qty="1.0", price="40.0"),
        _make_fill(fill_id="concurrent-partial-2", qty="1.0", price="50.0"),
    )
    for fill in fills:
        _run(store_a.save_paper_fill(fill))

    store_b = SQLiteStore(db)
    _run(store_b.initialize())

    async def _apply_both():
        return await asyncio.gather(
            store_a.apply_paper_fill(fills[0].fill_id),
            store_b.apply_paper_fill(fills[1].fill_id),
            return_exceptions=True,
        )

    results = _run(_apply_both())
    assert all(isinstance(result, PaperFill) for result in results)

    conn = sqlite3.connect(str(db))
    stored_order = PaperOrder.model_validate_json(
        conn.execute(
            "SELECT order_json FROM paper_orders WHERE order_id=?", (order.order_id,)
        ).fetchone()[0]
    )
    assert stored_order.status == PaperOrderStatus.FILLED
    assert {fill.fill_id for fill in stored_order.fills} == {
        fills[0].fill_id,
        fills[1].fill_id,
    }
    assert conn.execute(
        "SELECT COUNT(*) FROM paper_fill_applications WHERE order_id=?", (order.order_id,)
    ).fetchone()[0] == 2
    quote = AssetBalance.model_validate_json(
        conn.execute(
            "SELECT balance_json FROM paper_account_balances WHERE asset='QUOTE'"
        ).fetchone()[0]
    )
    assert quote.total == Decimal("9909.0")
    assert quote.available == Decimal("9909.0")
    assert quote.reserved == Decimal("0")
    assert conn.execute(
        "SELECT COUNT(*) FROM paper_reservations WHERE owner_order_id=?", (order.order_id,)
    ).fetchone()[0] == 0
    conn.close()
    _run(store_a.close())
    _run(store_b.close())


def test_partial_fill_transaction_rolls_back_if_order_write_fails(tmp_path: Path) -> None:
    db = tmp_path / "partial-fill-rollback.db"
    store = _make_store(db)
    _open_account(store)
    order = _make_order(
        order_type=PaperOrderType.LIMIT,
        requested_qty="2.0",
        price="50.0",
        max_quote_amount="150.0",
    )
    _run(store.register_paper_order(order))
    _accept_order(store, order)
    fill = _make_fill(fill_id="partial-rollback", qty="1.0", price="40.0")
    _run(store.save_paper_fill(fill))

    conn = sqlite3.connect(str(db))
    snapshot_before = conn.execute(
        "SELECT reservation_json FROM paper_order_acceptances WHERE order_id=?",
        (order.order_id,),
    ).fetchone()[0]
    conn.execute(
        "CREATE TRIGGER reject_order_update BEFORE UPDATE OF order_json ON paper_orders "
        "BEGIN SELECT RAISE(ABORT, 'injected order update failure'); END"
    )
    conn.commit()
    conn.close()

    with pytest.raises(sqlite3.IntegrityError, match="injected order update failure"):
        _run(store.apply_paper_fill(fill.fill_id))

    conn = sqlite3.connect(str(db))
    quote = AssetBalance.model_validate_json(
        conn.execute(
            "SELECT balance_json FROM paper_account_balances WHERE asset='QUOTE'"
        ).fetchone()[0]
    )
    assert quote.total == Decimal("10000.0")
    assert quote.available == Decimal("9900.0")
    assert quote.reserved == Decimal("100.0")
    assert conn.execute(
        "SELECT COUNT(*) FROM paper_fill_applications WHERE fill_id=?", (fill.fill_id,)
    ).fetchone()[0] == 0
    assert conn.execute(
        "SELECT COUNT(*) FROM position_lots WHERE order_id=?", (order.order_id,)
    ).fetchone()[0] == 0
    active_reservation = _run(store.load_paper_reservation(order.order_id))
    assert active_reservation is not None
    assert active_reservation.amount.amount == Decimal("100.0")
    acceptance_after = conn.execute(
        "SELECT reservation_json FROM paper_order_acceptances WHERE order_id=?",
        (order.order_id,),
    ).fetchone()[0]
    assert acceptance_after == snapshot_before
    stored_order = PaperOrder.model_validate_json(
        conn.execute(
            "SELECT order_json FROM paper_orders WHERE order_id=?", (order.order_id,)
        ).fetchone()[0]
    )
    assert stored_order.status == PaperOrderStatus.ACCEPTED
    assert stored_order.fills == []
    conn.execute("DROP TRIGGER reject_order_update")
    conn.close()
    _run(store.close())


@pytest.mark.parametrize(
    ("order_kwargs", "fill_price", "error_message"),
    [
        (
            {"order_type": PaperOrderType.LIMIT, "price": "50.0"},
            "51.0",
            "LIMIT BUY fill price",
        ),
        (
            {"position_id": "another-position"},
            "100.0",
            "different position_id",
        ),
    ],
    ids=("above-limit", "conflicting-position-id"),
)
def test_limit_price_or_position_id_conflict_rejected_without_effects(
    tmp_path: Path,
    order_kwargs: dict[str, object],
    fill_price: str,
    error_message: str,
) -> None:
    db = tmp_path / "invalid-order-fill.db"
    store = _make_store(db)
    _open_account(store)

    order = _make_order(**order_kwargs)
    _run(store.register_paper_order(order))
    _accept_order(store, order)
    fill = _make_fill(qty="2.0", price=fill_price)
    _run(store.save_paper_fill(fill))

    conn = sqlite3.connect(str(db))
    balance_before = AssetBalance.model_validate_json(
        conn.execute(
            "SELECT balance_json FROM paper_account_balances WHERE asset='QUOTE'"
        ).fetchone()[0]
    )
    conn.close()

    with pytest.raises(PaperOrderAcceptanceStateError, match=error_message):
        _run(store.apply_paper_fill(fill.fill_id))

    conn = sqlite3.connect(str(db))
    balance_after = AssetBalance.model_validate_json(
        conn.execute(
            "SELECT balance_json FROM paper_account_balances WHERE asset='QUOTE'"
        ).fetchone()[0]
    )
    assert balance_after == balance_before
    assert conn.execute(
        "SELECT COUNT(*) FROM paper_fill_applications WHERE fill_id=?", (fill.fill_id,)
    ).fetchone()[0] == 0
    assert conn.execute(
        "SELECT COUNT(*) FROM paper_reservations WHERE owner_order_id=?", (order.order_id,)
    ).fetchone()[0] == 1
    assert conn.execute(
        "SELECT COUNT(*) FROM position_lots WHERE order_id=?", (order.order_id,)
    ).fetchone()[0] == 0
    stored_order = PaperOrder.model_validate_json(
        conn.execute(
            "SELECT order_json FROM paper_orders WHERE order_id=?", (order.order_id,)
        ).fetchone()[0]
    )
    assert stored_order.status == PaperOrderStatus.ACCEPTED
    assert stored_order.fills == []
    conn.close()
    _run(store.close())


def test_apply_fill_idempotent_no_side_effects(tmp_path: Path) -> None:
    """Repetición idéntica de fill_id no cambia balance/reserva/lote/orden."""
    db = tmp_path / "idempotent.db"
    store = _make_store(db)
    _open_account(store)

    order = _make_order(
        order_type=PaperOrderType.MARKET,
        requested_qty="2.0",
        max_quote_amount="300.0",
    )
    _run(store.register_paper_order(order))
    _accept_order(store, order)

    fill = _make_fill(qty="2.0", price="100.0")
    _run(store.save_paper_fill(fill))

    # First apply
    applied1 = _run(store.apply_paper_fill("fill-1"))
    assert applied1.fill_id == "fill-1"

    # Second apply (idempotent)
    applied2 = _run(store.apply_paper_fill("fill-1"))
    assert applied2.fill_id == "fill-1"

    conn = sqlite3.connect(str(db))
    quote_bal = AssetBalance.model_validate_json(
        conn.execute("SELECT balance_json FROM paper_account_balances WHERE asset='QUOTE'").fetchone()[0]
    )
    # Should be same after second apply (no double debit)
    assert quote_bal.total == Decimal("9799.5")

    app_count = conn.execute("SELECT COUNT(*) FROM paper_fill_applications WHERE fill_id='fill-1'").fetchone()[0]
    assert app_count == 1  # Only one application mark
    conn.close()
    _run(store.close())


def test_fill_previously_journalized_then_applied(tmp_path: Path) -> None:
    """Fill previamente guardado: aplicación efectiva."""
    db = tmp_path / "journal-then-apply.db"
    store = _make_store(db)
    _open_account(store)

    order = _make_order(
        order_type=PaperOrderType.MARKET,
        requested_qty="1.0",
        max_quote_amount="200.0",
    )
    _run(store.register_paper_order(order))
    _accept_order(store, order)

    fill = _make_fill(qty="1.0", price="150.0")
    _run(store.save_paper_fill(fill))

    # Load the fill (journalized but not yet applied)
    loaded = _run(store.load_paper_fill("fill-1"))
    assert loaded is not None
    assert loaded.qty == Decimal("1.0")

    # Apply it
    applied = _run(store.apply_paper_fill("fill-1"))
    assert applied.fill_id == "fill-1"

    conn = sqlite3.connect(str(db))
    order_row = conn.execute("SELECT order_json FROM paper_orders WHERE order_id='order-1'").fetchone()
    loaded_order = PaperOrder.model_validate_json(order_row[0])
    assert loaded_order.status == PaperOrderStatus.FILLED
    conn.close()
    _run(store.close())


def test_fill_not_journalized_rejected(tmp_path: Path) -> None:
    """Fill no registrado rechazado."""
    db = tmp_path / "not-journalized.db"
    store = _make_store(db)
    _open_account(store)

    order = _make_order(
        order_type=PaperOrderType.MARKET,
        requested_qty="1.0",
        max_quote_amount="200.0",
    )
    _run(store.register_paper_order(order))
    _accept_order(store, order)

    # Try to apply a fill that was never saved
    with pytest.raises(PaperFillIntegrityError, match="not found"):
        _run(store.apply_paper_fill("fill-nonexistent"))


def test_order_not_accepted_rejected(tmp_path: Path) -> None:
    """Fill de orden no aceptada se rechaza."""
    db = tmp_path / "order-not-accepted.db"
    store = _make_store(db)
    _open_account(store)

    order = _make_order(
        order_type=PaperOrderType.MARKET,
        requested_qty="1.0",
        max_quote_amount="200.0",
    )
    _run(store.register_paper_order(order))
    # Don't accept the order

    fill = _make_fill()
    _run(store.save_paper_fill(fill))

    with pytest.raises(PaperOrderAcceptanceStateError, match="not open for fills"):
        _run(store.apply_paper_fill("fill-1"))


def test_market_notional_exact_max_accepted(tmp_path: Path) -> None:
    """MARKET con notional acumulado igual al máximo acepta."""
    db = tmp_path / "market-exact-max.db"
    store = _make_store(db)
    _open_account(store)

    order = _make_order(
        order_type=PaperOrderType.MARKET,
        requested_qty="3.0",
        max_quote_amount="300.0",
    )
    _run(store.register_paper_order(order))
    _accept_order(store, order)

    # Fill exactly at max
    fill = _make_fill(qty="3.0", price="100.0")
    _run(store.save_paper_fill(fill))

    applied = _run(store.apply_paper_fill("fill-1"))
    assert applied.fill_id == "fill-1"

    conn = sqlite3.connect(str(db))
    order_row = conn.execute("SELECT order_json FROM paper_orders WHERE order_id='order-1'").fetchone()
    loaded_order = PaperOrder.model_validate_json(order_row[0])
    assert loaded_order.status == PaperOrderStatus.FILLED
    conn.close()
    _run(store.close())


@pytest.mark.parametrize(
    ("max_quote", "exceeds_after_second"),
    [("200.0", False), ("199.0", True)],
    ids=("cumulative-at-cap", "cumulative-over-cap"),
)
def test_market_partial_fills_enforce_cumulative_notional_cap(
    tmp_path: Path, max_quote: str, exceeds_after_second: bool
) -> None:
    db = tmp_path / f"market-partials-{max_quote}.db"
    store = _make_store(db)
    _open_account(store)
    order = _make_order(
        order_type=PaperOrderType.MARKET,
        requested_qty="2.0",
        max_quote_amount=max_quote,
    )
    _run(store.register_paper_order(order))
    _accept_order(store, order)

    first = _make_fill(fill_id="market-partial-1", qty="1.0", price="80.0")
    second = _make_fill(fill_id="market-partial-2", qty="1.0", price="120.0")
    _run(store.save_paper_fill(first))
    _run(store.apply_paper_fill(first.fill_id))
    reservation_after_first = _run(store.load_paper_reservation(order.order_id))
    assert reservation_after_first is not None
    assert reservation_after_first.amount.amount == Decimal(max_quote) - Decimal("80.0")

    conn = sqlite3.connect(str(db))
    before_second = AssetBalance.model_validate_json(
        conn.execute(
            "SELECT balance_json FROM paper_account_balances WHERE asset='QUOTE'"
        ).fetchone()[0]
    )
    conn.close()
    _run(store.save_paper_fill(second))

    if exceeds_after_second:
        with pytest.raises(PaperOrderAcceptanceFundsError, match="cumulative MARKET notional"):
            _run(store.apply_paper_fill(second.fill_id))
        assert _run(store.load_paper_reservation(order.order_id)) == reservation_after_first
    else:
        _run(store.apply_paper_fill(second.fill_id))
        assert _run(store.load_paper_reservation(order.order_id)) is None

    conn = sqlite3.connect(str(db))
    after_second = AssetBalance.model_validate_json(
        conn.execute(
            "SELECT balance_json FROM paper_account_balances WHERE asset='QUOTE'"
        ).fetchone()[0]
    )
    if exceeds_after_second:
        assert after_second == before_second
        assert conn.execute(
            "SELECT COUNT(*) FROM paper_fill_applications WHERE order_id=?",
            (order.order_id,),
        ).fetchone()[0] == 1
        stored_order = PaperOrder.model_validate_json(
            conn.execute(
                "SELECT order_json FROM paper_orders WHERE order_id=?", (order.order_id,)
            ).fetchone()[0]
        )
        assert stored_order.status == PaperOrderStatus.PARTIALLY_FILLED
        assert [fill.fill_id for fill in stored_order.fills] == [first.fill_id]
    else:
        assert after_second.total == Decimal("9799.0")
        assert after_second.available == Decimal("9799.0")
        assert after_second.reserved == Decimal("0")
        assert conn.execute(
            "SELECT COUNT(*) FROM paper_fill_applications WHERE order_id=?",
            (order.order_id,),
        ).fetchone()[0] == 2
        stored_order = PaperOrder.model_validate_json(
            conn.execute(
                "SELECT order_json FROM paper_orders WHERE order_id=?", (order.order_id,)
            ).fetchone()[0]
        )
        assert stored_order.status == PaperOrderStatus.FILLED
        assert len(stored_order.fills) == 2
    conn.close()
    _run(store.close())


def test_market_excess_notional_rejected_entirely(tmp_path: Path) -> None:
    """MARKET con notional excedido rechaza íntegramente."""
    db = tmp_path / "market-excess.db"
    store = _make_store(db)
    _open_account(store)

    order = _make_order(
        order_type=PaperOrderType.MARKET,
        requested_qty="3.0",
        max_quote_amount="299.0",
    )
    _run(store.register_paper_order(order))
    _accept_order(store, order)

    # Fill exceeds max_quote_amount
    fill = _make_fill(qty="3.0", price="100.0")
    _run(store.save_paper_fill(fill))

    with pytest.raises(PaperOrderAcceptanceFundsError):
        _run(store.apply_paper_fill("fill-1"))

    # Verify no effects
    conn = sqlite3.connect(str(db))
    quote_bal = AssetBalance.model_validate_json(
        conn.execute("SELECT balance_json FROM paper_account_balances WHERE asset='QUOTE'").fetchone()[0]
    )
    assert quote_bal.total == Decimal("10000.0")  # Unchanged
    conn.close()
    _run(store.close())


def test_fill_persisted_in_order_for_complete_and_partial(tmp_path: Path) -> None:
    """Persisted order includes both complete and partial applied fills."""
    db = tmp_path / "focal.db"
    store = _make_store(db)
    _open_account(store)

    # --- caso completo: orden contiene fill y queda FILLED ---
    order = _make_order(
        order_type=PaperOrderType.MARKET,
        requested_qty="2.0",
        max_quote_amount="300.0",
    )
    _run(store.register_paper_order(order))
    _accept_order(store, order)

    fill = _make_fill(qty="2.0", price="100.0")
    _run(store.save_paper_fill(fill))

    applied = _run(store.apply_paper_fill("fill-1"))
    assert applied.fill_id == "fill-1"
    assert applied.qty == Decimal("2.0")

    conn = sqlite3.connect(str(db))
    order_row = conn.execute("SELECT order_json FROM paper_orders WHERE order_id='order-1'").fetchone()
    loaded_order = PaperOrder.model_validate_json(order_row[0])
    assert loaded_order.status == PaperOrderStatus.FILLED
    assert len(loaded_order.fills) == 1
    assert loaded_order.fills[0].fill_id == "fill-1"
    assert loaded_order.fills[0].qty == Decimal("2.0")
    conn.close()

    # --- caso parcial: se rechaza sin efectos ---
    db2 = tmp_path / "focal-partial.db"
    store2 = _make_store(db2)
    _open_account(store2)

    order2 = _make_order(
        order_type=PaperOrderType.LIMIT,
        requested_qty="2.0",
        price="50.0",
        max_quote_amount="150.0",
    )
    _run(store2.register_paper_order(order2))
    _accept_order(store2, order2)

    fill_partial = _make_fill(fill_id="fill-partial", qty="1.0", price="50.0")
    _run(store2.save_paper_fill(fill_partial))

    _run(store2.apply_paper_fill("fill-partial"))

    partial_order = _run(store2.load_paper_order(order2.order_id))
    assert partial_order is not None
    assert partial_order.status == PaperOrderStatus.PARTIALLY_FILLED
    assert [fill.fill_id for fill in partial_order.fills] == [fill_partial.fill_id]
    active_reservation = _run(store2.load_paper_reservation(order2.order_id))
    assert active_reservation is not None
    assert active_reservation.amount.amount == Decimal("50.0")

    conn2 = sqlite3.connect(str(db2))
    order_row2 = conn2.execute("SELECT order_json FROM paper_orders WHERE order_id='order-1'").fetchone()
    loaded_order2 = PaperOrder.model_validate_json(order_row2[0])
    assert loaded_order2.status == PaperOrderStatus.PARTIALLY_FILLED
    assert [fill.fill_id for fill in loaded_order2.fills] == [fill_partial.fill_id]
    app_row = conn2.execute("SELECT fill_id FROM paper_fill_applications WHERE fill_id='fill-partial'").fetchone()
    assert app_row is not None


# ── Fase 4 — concurrencia del mismo fill_id y rollback exhaustivo ────────────────


def test_same_fill_id_concurrent_apply(tmp_path: Path) -> None:
    """Dos instancias de SQLiteStore aplican el mismo fill_id simultáneamente;
    el efecto ocurre una sola vez (idempotencia)."""
    db = tmp_path / "concurrent.db"
    store1 = _make_store(db)
    store2 = _make_store(db)

    order = _make_order(
        order_type=PaperOrderType.LIMIT,
        requested_qty="2.0",
        price="50.0",
        max_quote_amount="100.0",
    )
    _open_account(store1)
    _run(store1.register_paper_order(order))
    _accept_order(store1, order)

    fill = _make_fill(fill_id="concurrent-fill", qty="1.0", price="40.0")
    _run(store1.save_paper_fill(fill))

    # Ambos store instancias invocan apply_paper_fill para el mismo fill_id
    def _apply(s: SQLiteStore) -> PaperFill:
        return _run(s.apply_paper_fill(fill.fill_id))

    loop = asyncio.new_event_loop()
    try:
        t1 = loop.run_in_executor(None, _apply, store1)
        t2 = loop.run_in_executor(None, _apply, store2)
        applied_list = loop.run_until_complete(asyncio.gather(t1, t2))
    finally:
        loop.close()

    # Ambas llamadas terminan con el DTO esperado (idempotencia: segunda es no-op exitoso)
    assert len(applied_list) == 2
    for a in applied_list:
        assert a.fill_id == fill.fill_id

    # Efecto ocurre una sola vez
    conn = sqlite3.connect(str(db))
    app_rows = conn.execute(
        "SELECT COUNT(*) FROM paper_fill_applications WHERE fill_id=?",
        (fill.fill_id,),
    ).fetchone()[0]
    assert app_rows == 1

    order_loaded = _run(store1.load_paper_order(order.order_id))
    assert len(order_loaded.fills) == 1
    assert order_loaded.fills[0].fill_id == fill.fill_id

    # Un solo crédito BASE, un solo débito QUOTE, una sola actualización de lote
    quote_row = conn.execute(
        "SELECT balance_json FROM paper_account_balances WHERE asset='QUOTE'"
    ).fetchone()[0]
    base_row = conn.execute(
        "SELECT balance_json FROM paper_account_balances WHERE asset='BASE'"
    ).fetchone()[0]

    # Verificar que no hay doble contabilización: los valores coinciden con un solo fill
    qb = AssetBalance.model_validate_json(quote_row)
    bb = AssetBalance.model_validate_json(base_row)
    assert qb.total == Decimal("10000") - (Decimal("40.0") + Decimal("0.5"))  # un solo débito
    assert bb.total == Decimal("1.0")  # un solo crédito BASE

    # Estado de la orden: PARTIALLY_FILLED tras una sola aplicación
    stored_order = PaperOrder.model_validate_json(
        conn.execute(
            "SELECT order_json FROM paper_orders WHERE order_id=?", (order.order_id,)
        ).fetchone()[0]
    )
    assert stored_order.status == PaperOrderStatus.PARTIALLY_FILLED

    # Reserva restante tras una sola aplicación (deducción usa order price: 1.0 * 50 = 50)
    reservation = _run(store1.load_paper_reservation(order.order_id))
    assert reservation is not None
    assert reservation.amount.amount == Decimal("50.0")  # 100 - 50 (order price)

    # reserved/available tras una sola aplicación (reserved = reserva restante)
    assert qb.reserved == Decimal("50.0")
    assert qb.available == Decimal("9909.5")  # total(9959.5) - reserved(50)

    # Un solo registro en position_lots con qty=1.0
    lot_row = conn.execute(
        "SELECT lot_json FROM position_lots WHERE order_id=?", (order.order_id,)
    ).fetchone()
    assert lot_row is not None
    import json as _json
    lot_data = _json.loads(lot_row[0])
    assert float(lot_data["total_qty"]) == 1.0

    conn.close()


def _make_store_with_trigger(
    tmp_path: Path,
    trigger_sql: str,
) -> SQLiteStore:
    """Crea un store con un trigger SQL inyectado después de la inicialización.

    NOTE: triggers must be injected AFTER the account is opened (via a separate
    connection), because _make_store calls initialize() which may recreate tables.
    Triggers are stored in the DB file schema and persist across connections.
    """
    db = tmp_path / "trigger.db"
    store = _make_store(db)
    conn = sqlite3.connect(str(db))
    conn.executescript(trigger_sql)
    conn.commit()
    conn.close()
    return store


def _inject_trigger(db: Path, trigger_sql: str) -> None:
    """Inyecta un trigger en una DB ya inicializada y con cuenta abierta.

    Crea la tabla _trigger_control si no existe (vacía = triggers desactivados).
    El trigger SQL DEBE usar esta tabla para decidir si dispara.
    Ejemplo:
        CREATE TRIGGER ... BEGIN
            SELECT CASE (SELECT COUNT(*) FROM _trigger_control WHERE action='enabled')
                WHEN 0 THEN 0 ELSE RAISE(ABORT, 'trigger-fail') END;
        END;
    """
    conn = sqlite3.connect(str(db))
    conn.execute(
        "CREATE TABLE IF NOT EXISTS _trigger_control (action TEXT PRIMARY KEY)"
    )
    conn.executescript(trigger_sql)
    conn.commit()
    conn.close()


def _set_trigger_flag(db: Path, enabled: bool) -> None:
    """Activa/desactiva triggers de prueba mediante tabla _trigger_control."""
    conn = sqlite3.connect(str(db))
    if enabled:
        conn.execute("INSERT OR REPLACE INTO _trigger_control (action) VALUES ('enabled')")
    else:
        conn.execute("DELETE FROM _trigger_control")
    conn.commit()
    conn.close()


def test_apply_fails_after_update_quote_balance(tmp_path: Path) -> None:
    """Trigger AFTER UPDATE ON paper_account_balances para QUOTE fuerza error;
    rollback completo."""
    db = tmp_path / "trigger.db"
    store = _make_store(db)
    _open_account(store)

    trigger = """
    CREATE TRIGGER IF NOT EXISTS fail_after_quote_update
    AFTER UPDATE ON paper_account_balances
    WHEN NEW.asset = 'QUOTE' AND (SELECT COUNT(*) FROM _trigger_control WHERE action='enabled') > 0
    BEGIN
        SELECT RAISE(ABORT, 'trigger-fail');
    END;
    """
    _inject_trigger(db, trigger)

    order = _make_order(
        order_type=PaperOrderType.LIMIT,
        requested_qty="2.0",
        price="50.0",
        max_quote_amount="100.0",
    )
    _run(store.register_paper_order(order))
    _accept_order(store, order)

    fill = _make_fill(fill_id="trigger-quote", qty="1.0", price="40.0")
    _run(store.save_paper_fill(fill))

    conn = sqlite3.connect(str(tmp_path / "trigger.db"))
    balances_pre = [tuple(r) for r in conn.execute("SELECT * FROM paper_account_balances ORDER BY asset").fetchall()]
    reservation_pre = [tuple(r) for r in conn.execute("SELECT * FROM paper_reservations WHERE owner_order_id='order-1' ORDER BY reservation_id").fetchall()]
    lot_pre = [tuple(r) for r in conn.execute("SELECT * FROM position_lots WHERE order_id='order-1' ORDER BY order_id").fetchall()]
    order_pre = [tuple(r) for r in conn.execute("SELECT * FROM paper_orders WHERE order_id='order-1' ORDER BY order_id").fetchall()]
    apps_pre = [tuple(r) for r in conn.execute("SELECT * FROM paper_fill_applications WHERE order_id='order-1' ORDER BY fill_id").fetchall()]
    journal_pre = [tuple(r) for r in conn.execute("SELECT * FROM paper_fills WHERE fill_id='trigger-quote' ORDER BY fill_id").fetchall()]
    acceptance_pre = [tuple(r) for r in conn.execute("SELECT * FROM paper_order_acceptances WHERE order_id='order-1' ORDER BY order_id").fetchall()]
    conn.close()

    _set_trigger_flag(tmp_path / "trigger.db", True)
    with pytest.raises(sqlite3.IntegrityError, match="trigger-fail"):
        _run(store.apply_paper_fill(fill.fill_id))

    conn = sqlite3.connect(str(tmp_path / "trigger.db"))
    balances_post = [tuple(r) for r in conn.execute("SELECT * FROM paper_account_balances ORDER BY asset").fetchall()]
    assert balances_post == balances_pre

    reservation_post = [tuple(r) for r in conn.execute("SELECT * FROM paper_reservations WHERE owner_order_id='order-1' ORDER BY reservation_id").fetchall()]
    assert reservation_post == reservation_pre

    lot_post = [tuple(r) for r in conn.execute("SELECT * FROM position_lots WHERE order_id='order-1' ORDER BY order_id").fetchall()]
    assert lot_post == lot_pre

    order_post = [tuple(r) for r in conn.execute("SELECT * FROM paper_orders WHERE order_id='order-1' ORDER BY order_id").fetchall()]
    assert order_post == order_pre

    apps_post = [tuple(r) for r in conn.execute("SELECT * FROM paper_fill_applications WHERE order_id='order-1' ORDER BY fill_id").fetchall()]
    assert apps_post == apps_pre

    journal_post = [tuple(r) for r in conn.execute("SELECT * FROM paper_fills WHERE fill_id='trigger-quote' ORDER BY fill_id").fetchall()]
    assert journal_post == journal_pre

    acceptance_post = [tuple(r) for r in conn.execute("SELECT * FROM paper_order_acceptances WHERE order_id='order-1' ORDER BY order_id").fetchall()]
    assert acceptance_post == acceptance_pre
    conn.close()


def test_apply_fails_after_update_reservation_limit(tmp_path: Path) -> None:
    """Trigger AFTER UPDATE ON paper_reservations (parcial LIMIT) fuerza error;
    rollback completo."""
    db = tmp_path / "trigger.db"
    store = _make_store(db)
    _open_account(store)

    trigger = """
    CREATE TRIGGER IF NOT EXISTS fail_after_reservation_update
    AFTER UPDATE ON paper_reservations
    WHEN (SELECT COUNT(*) FROM _trigger_control WHERE action='enabled') > 0
    BEGIN
        SELECT RAISE(ABORT, 'trigger-fail');
    END;
    """
    _inject_trigger(db, trigger)

    order = _make_order(
        order_type=PaperOrderType.LIMIT,
        requested_qty="2.0",
        price="50.0",
        max_quote_amount="100.0",
    )
    _run(store.register_paper_order(order))
    _accept_order(store, order)

    fill = _make_fill(fill_id="trigger-res-limit", qty="1.0", price="40.0")
    _run(store.save_paper_fill(fill))

    conn = sqlite3.connect(str(tmp_path / "trigger.db"))
    balances_pre = [tuple(r) for r in conn.execute("SELECT * FROM paper_account_balances ORDER BY asset").fetchall()]
    reservation_pre = [tuple(r) for r in conn.execute("SELECT * FROM paper_reservations WHERE owner_order_id='order-1' ORDER BY reservation_id").fetchall()]
    lot_pre = [tuple(r) for r in conn.execute("SELECT * FROM position_lots WHERE order_id='order-1' ORDER BY order_id").fetchall()]
    order_pre = [tuple(r) for r in conn.execute("SELECT * FROM paper_orders WHERE order_id='order-1' ORDER BY order_id").fetchall()]
    apps_pre = [tuple(r) for r in conn.execute("SELECT * FROM paper_fill_applications WHERE order_id='order-1' ORDER BY fill_id").fetchall()]
    journal_pre = [tuple(r) for r in conn.execute("SELECT * FROM paper_fills WHERE fill_id='trigger-res-limit' ORDER BY fill_id").fetchall()]
    acceptance_pre = [tuple(r) for r in conn.execute("SELECT * FROM paper_order_acceptances WHERE order_id='order-1' ORDER BY order_id").fetchall()]
    conn.close()

    _set_trigger_flag(tmp_path / "trigger.db", True)
    with pytest.raises(sqlite3.IntegrityError, match="trigger-fail"):
        _run(store.apply_paper_fill(fill.fill_id))

    conn = sqlite3.connect(str(tmp_path / "trigger.db"))
    balances_post = [tuple(r) for r in conn.execute("SELECT * FROM paper_account_balances ORDER BY asset").fetchall()]
    assert balances_post == balances_pre

    reservation_post = [tuple(r) for r in conn.execute("SELECT * FROM paper_reservations WHERE owner_order_id='order-1' ORDER BY reservation_id").fetchall()]
    assert reservation_post == reservation_pre

    lot_post = [tuple(r) for r in conn.execute("SELECT * FROM position_lots WHERE order_id='order-1' ORDER BY order_id").fetchall()]
    assert lot_post == lot_pre

    order_post = [tuple(r) for r in conn.execute("SELECT * FROM paper_orders WHERE order_id='order-1' ORDER BY order_id").fetchall()]
    assert order_post == order_pre

    apps_post = [tuple(r) for r in conn.execute("SELECT * FROM paper_fill_applications WHERE order_id='order-1' ORDER BY fill_id").fetchall()]
    assert apps_post == apps_pre

    journal_post = [tuple(r) for r in conn.execute("SELECT * FROM paper_fills WHERE fill_id='trigger-res-limit' ORDER BY fill_id").fetchall()]
    assert journal_post == journal_pre

    acceptance_post = [tuple(r) for r in conn.execute("SELECT * FROM paper_order_acceptances WHERE order_id='order-1' ORDER BY order_id").fetchall()]
    assert acceptance_post == acceptance_pre
    conn.close()


def test_apply_fails_after_delete_reservation_complete(tmp_path: Path) -> None:
    """Trigger AFTER DELETE ON paper_reservations (fill completo) fuerza error;
    rollback completo."""
    db = tmp_path / "trigger.db"
    store = _make_store(db)
    _open_account(store)

    trigger = """
    CREATE TRIGGER IF NOT EXISTS fail_after_reservation_delete
    AFTER DELETE ON paper_reservations
    WHEN (SELECT COUNT(*) FROM _trigger_control WHERE action='enabled') > 0
    BEGIN
        SELECT RAISE(ABORT, 'trigger-fail');
    END;
    """
    _inject_trigger(db, trigger)

    order = _make_order(
        order_type=PaperOrderType.LIMIT,
        requested_qty="1.0",
        price="50.0",
        max_quote_amount="100.0",
    )
    _run(store.register_paper_order(order))
    _accept_order(store, order)

    fill = _make_fill(fill_id="trigger-res-del", qty="1.0", price="40.0")
    _run(store.save_paper_fill(fill))

    conn = sqlite3.connect(str(tmp_path / "trigger.db"))
    balances_pre = [tuple(r) for r in conn.execute("SELECT * FROM paper_account_balances ORDER BY asset").fetchall()]
    reservation_pre = [tuple(r) for r in conn.execute("SELECT * FROM paper_reservations WHERE owner_order_id='order-1' ORDER BY reservation_id").fetchall()]
    lot_pre = [tuple(r) for r in conn.execute("SELECT * FROM position_lots WHERE order_id='order-1' ORDER BY order_id").fetchall()]
    order_pre = [tuple(r) for r in conn.execute("SELECT * FROM paper_orders WHERE order_id='order-1' ORDER BY order_id").fetchall()]
    apps_pre = [tuple(r) for r in conn.execute("SELECT * FROM paper_fill_applications WHERE order_id='order-1' ORDER BY fill_id").fetchall()]
    journal_pre = [tuple(r) for r in conn.execute("SELECT * FROM paper_fills WHERE fill_id='trigger-res-del' ORDER BY fill_id").fetchall()]
    acceptance_pre = [tuple(r) for r in conn.execute("SELECT * FROM paper_order_acceptances WHERE order_id='order-1' ORDER BY order_id").fetchall()]
    conn.close()

    _set_trigger_flag(tmp_path / "trigger.db", True)
    with pytest.raises(sqlite3.IntegrityError, match="trigger-fail"):
        _run(store.apply_paper_fill(fill.fill_id))

    conn = sqlite3.connect(str(tmp_path / "trigger.db"))
    balances_post = [tuple(r) for r in conn.execute("SELECT * FROM paper_account_balances ORDER BY asset").fetchall()]
    assert balances_post == balances_pre

    reservation_post = [tuple(r) for r in conn.execute("SELECT * FROM paper_reservations WHERE owner_order_id='order-1' ORDER BY reservation_id").fetchall()]
    assert reservation_post == reservation_pre

    lot_post = [tuple(r) for r in conn.execute("SELECT * FROM position_lots WHERE order_id='order-1' ORDER BY order_id").fetchall()]
    assert lot_post == lot_pre

    order_post = [tuple(r) for r in conn.execute("SELECT * FROM paper_orders WHERE order_id='order-1' ORDER BY order_id").fetchall()]
    assert order_post == order_pre

    apps_post = [tuple(r) for r in conn.execute("SELECT * FROM paper_fill_applications WHERE order_id='order-1' ORDER BY fill_id").fetchall()]
    assert apps_post == apps_pre

    journal_post = [tuple(r) for r in conn.execute("SELECT * FROM paper_fills WHERE fill_id='trigger-res-del' ORDER BY fill_id").fetchall()]
    assert journal_post == journal_pre

    acceptance_post = [tuple(r) for r in conn.execute("SELECT * FROM paper_order_acceptances WHERE order_id='order-1' ORDER BY order_id").fetchall()]
    assert acceptance_post == acceptance_pre
    conn.close()


def test_apply_fails_after_update_base_balance(tmp_path: Path) -> None:
    """Trigger AFTER UPDATE ON paper_account_balances para BASE (fila existente) fuerza error;
    rollback completo."""
    # Primero crear la cuenta con un fill que ya tenga BASE balance existente
    db = tmp_path / "trigger-base.db"
    store = _make_store(db)
    _open_account(store)

    order = _make_order(
        order_type=PaperOrderType.LIMIT,
        requested_qty="2.0",
        price="50.0",
        max_quote_amount="100.0",
    )
    _run(store.register_paper_order(order))
    _accept_order(store, order)

    # Crear un fill previo para que exista BASE balance
    fill_prev = _make_fill(fill_id="base-prev-fill", qty="0.5", price="40.0")
    _run(store.save_paper_fill(fill_prev))
    _run(store.apply_paper_fill(fill_prev.fill_id))

    # Ahora inyectar trigger para fallar en UPDATE de BASE
    trigger = """
    CREATE TRIGGER IF NOT EXISTS fail_after_base_update
    AFTER UPDATE ON paper_account_balances
    WHEN NEW.asset = 'BASE' AND (SELECT COUNT(*) FROM _trigger_control WHERE action='enabled') > 0
    BEGIN
        SELECT RAISE(ABORT, 'trigger-fail');
    END;
    """
    _inject_trigger(db, trigger)

    order2 = _make_order(
        order_id="order-2",
        order_type=PaperOrderType.LIMIT,
        requested_qty="2.0",
        price="50.0",
        max_quote_amount="100.0",
    )
    _run(store.register_paper_order(order2))
    _accept_order(store, order2)

    fill = _make_fill(fill_id="trigger-base-upd", order_id=order2.order_id, qty="1.0", price="40.0")
    _run(store.save_paper_fill(fill))

    conn = sqlite3.connect(str(db))
    balances_pre = [tuple(r) for r in conn.execute("SELECT * FROM paper_account_balances ORDER BY asset").fetchall()]
    reservation_pre = [tuple(r) for r in conn.execute("SELECT * FROM paper_reservations WHERE owner_order_id='order-2' ORDER BY reservation_id").fetchall()]
    lot_pre = [tuple(r) for r in conn.execute("SELECT * FROM position_lots WHERE order_id='order-2' ORDER BY order_id").fetchall()]
    order_pre = [tuple(r) for r in conn.execute("SELECT * FROM paper_orders WHERE order_id='order-2' ORDER BY order_id").fetchall()]
    apps_pre = [tuple(r) for r in conn.execute("SELECT * FROM paper_fill_applications WHERE order_id='order-2' ORDER BY fill_id").fetchall()]
    journal_pre = [tuple(r) for r in conn.execute("SELECT * FROM paper_fills WHERE fill_id='trigger-base-upd' ORDER BY fill_id").fetchall()]
    acceptance_pre = [tuple(r) for r in conn.execute("SELECT * FROM paper_order_acceptances WHERE order_id='order-2' ORDER BY order_id").fetchall()]

    # Capturar/comparar el estado de order2 explícitamente (sin fills aún: status=accepted)
    stored_order_pre = PaperOrder.model_validate_json(
        conn.execute(
            "SELECT order_json FROM paper_orders WHERE order_id='order-2'"
        ).fetchone()[0]
    )
    assert stored_order_pre.status == PaperOrderStatus.ACCEPTED
    assert len(stored_order_pre.fills) == 0

    # reserved/available de QUOTE para order2: full reservation intacta
    reservation_pre_explicit = _run(store.load_paper_reservation("order-2"))
    assert reservation_pre_explicit is not None
    assert reservation_pre_explicit.amount.amount == Decimal("100.0")  # sin cambios (sin fills)

    conn.close()

    _set_trigger_flag(db, True)
    with pytest.raises(sqlite3.IntegrityError, match="trigger-fail"):
        _run(store.apply_paper_fill(fill.fill_id))

    # Post-trigger: comparar las filas completas afectadas de order2 y su fill
    conn = sqlite3.connect(str(db))
    balances_post = [tuple(r) for r in conn.execute("SELECT * FROM paper_account_balances ORDER BY asset").fetchall()]
    assert balances_post == balances_pre

    reservation_post = [tuple(r) for r in conn.execute("SELECT * FROM paper_reservations WHERE owner_order_id='order-2' ORDER BY reservation_id").fetchall()]
    assert reservation_post == reservation_pre

    lot_post = [tuple(r) for r in conn.execute("SELECT * FROM position_lots WHERE order_id='order-2' ORDER BY order_id").fetchall()]
    assert lot_post == lot_pre

    order_post = [tuple(r) for r in conn.execute("SELECT * FROM paper_orders WHERE order_id='order-2' ORDER BY order_id").fetchall()]
    assert order_post == order_pre

    apps_post = [tuple(r) for r in conn.execute("SELECT * FROM paper_fill_applications WHERE order_id='order-2' ORDER BY fill_id").fetchall()]
    assert apps_post == apps_pre

    journal_post = [tuple(r) for r in conn.execute("SELECT * FROM paper_fills WHERE fill_id='trigger-base-upd' ORDER BY fill_id").fetchall()]
    assert journal_post == journal_pre

    acceptance_post = [tuple(r) for r in conn.execute("SELECT * FROM paper_order_acceptances WHERE order_id='order-2' ORDER BY order_id").fetchall()]
    assert acceptance_post == acceptance_pre

    # Verificar estado de order2 post-trigger: debe permanecer sin cambios (accepted, sin fills)
    stored_order_post = PaperOrder.model_validate_json(
        conn.execute(
            "SELECT order_json FROM paper_orders WHERE order_id='order-2'"
        ).fetchone()[0]
    )
    assert stored_order_post.status == PaperOrderStatus.ACCEPTED
    assert len(stored_order_post.fills) == 0

    # reserved/available de QUOTE para order2 deben permanecer sin cambios
    reservation_post_explicit = _run(store.load_paper_reservation("order-2"))
    assert reservation_post_explicit is not None
    assert reservation_post_explicit.amount.amount == Decimal("100.0")  # Sin cambio

    conn.close()


def test_apply_fails_after_insert_base_balance(tmp_path: Path) -> None:
    """Trigger AFTER INSERT ON paper_account_balances para BASE (nuevo) fuerza error;
    rollback completo."""
    db = tmp_path / "trigger-base-ins.db"
    store = _make_store(db)
    _open_account(store)

    # Eliminar solo el saldo BASE para que apply_paper_fill haga INSERT de BASE (no UPDATE)
    conn = sqlite3.connect(str(db))
    conn.execute("DELETE FROM paper_account_balances WHERE asset='BASE'")
    conn.commit()
    conn.close()

    order = _make_order(
        order_type=PaperOrderType.LIMIT,
        requested_qty="2.0",
        price="50.0",
        max_quote_amount="100.0",
    )
    _run(store.register_paper_order(order))
    _accept_order(store, order)

    # No crear fill previo: BASE balance no existe aún
    fill = _make_fill(fill_id="trigger-base-ins", qty="1.0", price="40.0")
    _run(store.save_paper_fill(fill))

    # Inyectar trigger para fallar en INSERT de paper_account_balances
    trigger = """
    CREATE TRIGGER IF NOT EXISTS fail_after_base_insert
    AFTER INSERT ON paper_account_balances
    WHEN NEW.asset = 'BASE' AND (SELECT COUNT(*) FROM _trigger_control WHERE action='enabled') > 0
    BEGIN
        SELECT RAISE(ABORT, 'trigger-fail');
    END;
    """
    _inject_trigger(db, trigger)

    conn = sqlite3.connect(str(db))
    balances_pre = [tuple(r) for r in conn.execute("SELECT * FROM paper_account_balances ORDER BY asset").fetchall()]
    reservation_pre = [tuple(r) for r in conn.execute("SELECT * FROM paper_reservations WHERE owner_order_id='order-1' ORDER BY reservation_id").fetchall()]
    lot_pre = [tuple(r) for r in conn.execute("SELECT * FROM position_lots WHERE order_id='order-1' ORDER BY order_id").fetchall()]
    order_pre = [tuple(r) for r in conn.execute("SELECT * FROM paper_orders WHERE order_id='order-1' ORDER BY order_id").fetchall()]
    apps_pre = [tuple(r) for r in conn.execute("SELECT * FROM paper_fill_applications WHERE order_id='order-1' ORDER BY fill_id").fetchall()]
    journal_pre = [tuple(r) for r in conn.execute("SELECT * FROM paper_fills WHERE fill_id='trigger-base-ins' ORDER BY fill_id").fetchall()]
    acceptance_pre = [tuple(r) for r in conn.execute("SELECT * FROM paper_order_acceptances WHERE order_id='order-1' ORDER BY order_id").fetchall()]
    conn.close()

    _set_trigger_flag(db, True)
    with pytest.raises(sqlite3.IntegrityError, match="trigger-fail"):
        _run(store.apply_paper_fill(fill.fill_id))

    conn = sqlite3.connect(str(db))
    balances_post = [tuple(r) for r in conn.execute("SELECT * FROM paper_account_balances ORDER BY asset").fetchall()]
    assert balances_post == balances_pre

    # BASE debe seguir ausente
    base_count = conn.execute(
        "SELECT COUNT(*) FROM paper_account_balances WHERE asset='BASE'"
    ).fetchone()[0]
    assert base_count == 0

    reservation_post = [tuple(r) for r in conn.execute("SELECT * FROM paper_reservations WHERE owner_order_id='order-1' ORDER BY reservation_id").fetchall()]
    assert reservation_post == reservation_pre

    lot_post = [tuple(r) for r in conn.execute("SELECT * FROM position_lots WHERE order_id='order-1' ORDER BY order_id").fetchall()]
    assert lot_post == lot_pre

    order_post = [tuple(r) for r in conn.execute("SELECT * FROM paper_orders WHERE order_id='order-1' ORDER BY order_id").fetchall()]
    assert order_post == order_pre

    apps_post = [tuple(r) for r in conn.execute("SELECT * FROM paper_fill_applications WHERE order_id='order-1' ORDER BY fill_id").fetchall()]
    assert apps_post == apps_pre

    journal_post = [tuple(r) for r in conn.execute("SELECT * FROM paper_fills WHERE fill_id='trigger-base-ins' ORDER BY fill_id").fetchall()]
    assert journal_post == journal_pre

    acceptance_post = [tuple(r) for r in conn.execute("SELECT * FROM paper_order_acceptances WHERE order_id='order-1' ORDER BY order_id").fetchall()]
    assert acceptance_post == acceptance_pre
    conn.close()


def test_apply_fails_after_insert_fill_application(tmp_path: Path) -> None:
    """Trigger AFTER INSERT ON paper_fill_applications fuerza error;
    rollback completo."""
    db = tmp_path / "trigger.db"
    store = _make_store(db)
    _open_account(store)

    order = _make_order(
        order_type=PaperOrderType.LIMIT,
        requested_qty="2.0",
        price="50.0",
        max_quote_amount="100.0",
    )
    _run(store.register_paper_order(order))
    _accept_order(store, order)

    fill = _make_fill(fill_id="trigger-app-ins", qty="1.0", price="40.0")
    _run(store.save_paper_fill(fill))

    trigger = """
    CREATE TRIGGER IF NOT EXISTS fail_after_app_insert
    AFTER INSERT ON paper_fill_applications
    WHEN (SELECT COUNT(*) FROM _trigger_control WHERE action='enabled') > 0
    BEGIN
        SELECT RAISE(ABORT, 'trigger-fail');
    END;
    """
    _inject_trigger(db, trigger)

    conn = sqlite3.connect(str(tmp_path / "trigger.db"))
    balances_pre = [tuple(r) for r in conn.execute("SELECT * FROM paper_account_balances ORDER BY asset").fetchall()]
    reservation_pre = [tuple(r) for r in conn.execute("SELECT * FROM paper_reservations WHERE owner_order_id='order-1' ORDER BY reservation_id").fetchall()]
    lot_pre = [tuple(r) for r in conn.execute("SELECT * FROM position_lots WHERE order_id='order-1' ORDER BY order_id").fetchall()]
    order_pre = [tuple(r) for r in conn.execute("SELECT * FROM paper_orders WHERE order_id='order-1' ORDER BY order_id").fetchall()]
    apps_pre = [tuple(r) for r in conn.execute("SELECT * FROM paper_fill_applications WHERE order_id='order-1' ORDER BY fill_id").fetchall()]
    journal_pre = [tuple(r) for r in conn.execute("SELECT * FROM paper_fills WHERE fill_id='trigger-app-ins' ORDER BY fill_id").fetchall()]
    acceptance_pre = [tuple(r) for r in conn.execute("SELECT * FROM paper_order_acceptances WHERE order_id='order-1' ORDER BY order_id").fetchall()]
    conn.close()

    _set_trigger_flag(tmp_path / "trigger.db", True)
    with pytest.raises(sqlite3.IntegrityError, match="trigger-fail"):
        _run(store.apply_paper_fill(fill.fill_id))

    conn = sqlite3.connect(str(tmp_path / "trigger.db"))
    balances_post = [tuple(r) for r in conn.execute("SELECT * FROM paper_account_balances ORDER BY asset").fetchall()]
    assert balances_post == balances_pre

    reservation_post = [tuple(r) for r in conn.execute("SELECT * FROM paper_reservations WHERE owner_order_id='order-1' ORDER BY reservation_id").fetchall()]
    assert reservation_post == reservation_pre

    lot_post = [tuple(r) for r in conn.execute("SELECT * FROM position_lots WHERE order_id='order-1' ORDER BY order_id").fetchall()]
    assert lot_post == lot_pre

    order_post = [tuple(r) for r in conn.execute("SELECT * FROM paper_orders WHERE order_id='order-1' ORDER BY order_id").fetchall()]
    assert order_post == order_pre

    apps_post = [tuple(r) for r in conn.execute("SELECT * FROM paper_fill_applications WHERE order_id='order-1' ORDER BY fill_id").fetchall()]
    assert apps_post == apps_pre

    journal_post = [tuple(r) for r in conn.execute("SELECT * FROM paper_fills WHERE fill_id='trigger-app-ins' ORDER BY fill_id").fetchall()]
    assert journal_post == journal_pre

    acceptance_post = [tuple(r) for r in conn.execute("SELECT * FROM paper_order_acceptances WHERE order_id='order-1' ORDER BY order_id").fetchall()]
    assert acceptance_post == acceptance_pre
    conn.close()


def test_apply_fails_after_insert_replace_position_lots(tmp_path: Path) -> None:
    """Trigger AFTER INSERT ON position_lots fuerza error;
    rollback completo. (SQLite no soporta 'AFTER INSERT OR REPLACE', usamos AFTER INSERT)."""
    db = tmp_path / "trigger.db"
    store = _make_store(db)
    _open_account(store)

    trigger = """
    CREATE TRIGGER IF NOT EXISTS fail_after_lot_upsert
    AFTER INSERT ON position_lots
    WHEN (SELECT COUNT(*) FROM _trigger_control WHERE action='enabled') > 0
    BEGIN
        SELECT RAISE(ABORT, 'trigger-fail');
    END;
    """
    _inject_trigger(db, trigger)

    order = _make_order(
        order_type=PaperOrderType.LIMIT,
        requested_qty="2.0",
        price="50.0",
        max_quote_amount="100.0",
    )
    _run(store.register_paper_order(order))
    _accept_order(store, order)

    fill = _make_fill(fill_id="trigger-lot", qty="1.0", price="40.0")
    _run(store.save_paper_fill(fill))

    conn = sqlite3.connect(str(tmp_path / "trigger.db"))
    balances_pre = [tuple(r) for r in conn.execute("SELECT * FROM paper_account_balances ORDER BY asset").fetchall()]
    reservation_pre = [tuple(r) for r in conn.execute("SELECT * FROM paper_reservations WHERE owner_order_id='order-1' ORDER BY reservation_id").fetchall()]
    lot_pre = [tuple(r) for r in conn.execute("SELECT * FROM position_lots WHERE order_id='order-1' ORDER BY order_id").fetchall()]
    order_pre = [tuple(r) for r in conn.execute("SELECT * FROM paper_orders WHERE order_id='order-1' ORDER BY order_id").fetchall()]
    apps_pre = [tuple(r) for r in conn.execute("SELECT * FROM paper_fill_applications WHERE order_id='order-1' ORDER BY fill_id").fetchall()]
    journal_pre = [tuple(r) for r in conn.execute("SELECT * FROM paper_fills WHERE fill_id='trigger-lot' ORDER BY fill_id").fetchall()]
    acceptance_pre = [tuple(r) for r in conn.execute("SELECT * FROM paper_order_acceptances WHERE order_id='order-1' ORDER BY order_id").fetchall()]
    conn.close()

    _set_trigger_flag(tmp_path / "trigger.db", True)
    with pytest.raises(sqlite3.IntegrityError, match="trigger-fail"):
        _run(store.apply_paper_fill(fill.fill_id))

    conn = sqlite3.connect(str(tmp_path / "trigger.db"))
    balances_post = [tuple(r) for r in conn.execute("SELECT * FROM paper_account_balances ORDER BY asset").fetchall()]
    assert balances_post == balances_pre

    reservation_post = [tuple(r) for r in conn.execute("SELECT * FROM paper_reservations WHERE owner_order_id='order-1' ORDER BY reservation_id").fetchall()]
    assert reservation_post == reservation_pre

    lot_post = [tuple(r) for r in conn.execute("SELECT * FROM position_lots WHERE order_id='order-1' ORDER BY order_id").fetchall()]
    assert lot_post == lot_pre

    order_post = [tuple(r) for r in conn.execute("SELECT * FROM paper_orders WHERE order_id='order-1' ORDER BY order_id").fetchall()]
    assert order_post == order_pre

    apps_post = [tuple(r) for r in conn.execute("SELECT * FROM paper_fill_applications WHERE order_id='order-1' ORDER BY fill_id").fetchall()]
    assert apps_post == apps_pre

    journal_post = [tuple(r) for r in conn.execute("SELECT * FROM paper_fills WHERE fill_id='trigger-lot' ORDER BY fill_id").fetchall()]
    assert journal_post == journal_pre

    acceptance_post = [tuple(r) for r in conn.execute("SELECT * FROM paper_order_acceptances WHERE order_id='order-1' ORDER BY order_id").fetchall()]
    assert acceptance_post == acceptance_pre
    conn.close()


def test_apply_fails_after_update_paper_orders(tmp_path: Path) -> None:
    """Trigger AFTER UPDATE ON paper_orders fuerza error;
    rollback completo."""
    db = tmp_path / "trigger.db"
    store = _make_store(db)
    _open_account(store)

    trigger = """
    CREATE TRIGGER IF NOT EXISTS fail_after_order_update
    AFTER UPDATE ON paper_orders
    WHEN (SELECT COUNT(*) FROM _trigger_control WHERE action='enabled') > 0
    BEGIN
        SELECT RAISE(ABORT, 'trigger-fail');
    END;
    """
    _inject_trigger(db, trigger)

    order = _make_order(
        order_type=PaperOrderType.LIMIT,
        requested_qty="2.0",
        price="50.0",
        max_quote_amount="100.0",
    )
    _run(store.register_paper_order(order))
    _accept_order(store, order)

    fill = _make_fill(fill_id="trigger-order", qty="1.0", price="40.0")
    _run(store.save_paper_fill(fill))

    conn = sqlite3.connect(str(tmp_path / "trigger.db"))
    balances_pre = [tuple(r) for r in conn.execute("SELECT * FROM paper_account_balances ORDER BY asset").fetchall()]
    reservation_pre = [tuple(r) for r in conn.execute("SELECT * FROM paper_reservations WHERE owner_order_id='order-1' ORDER BY reservation_id").fetchall()]
    lot_pre = [tuple(r) for r in conn.execute("SELECT * FROM position_lots WHERE order_id='order-1' ORDER BY order_id").fetchall()]
    order_pre = [tuple(r) for r in conn.execute("SELECT * FROM paper_orders WHERE order_id='order-1' ORDER BY order_id").fetchall()]
    apps_pre = [tuple(r) for r in conn.execute("SELECT * FROM paper_fill_applications WHERE order_id='order-1' ORDER BY fill_id").fetchall()]
    journal_pre = [tuple(r) for r in conn.execute("SELECT * FROM paper_fills WHERE fill_id='trigger-order' ORDER BY fill_id").fetchall()]
    acceptance_pre = [tuple(r) for r in conn.execute("SELECT * FROM paper_order_acceptances WHERE order_id='order-1' ORDER BY order_id").fetchall()]
    conn.close()

    _set_trigger_flag(tmp_path / "trigger.db", True)
    with pytest.raises(sqlite3.IntegrityError, match="trigger-fail"):
        _run(store.apply_paper_fill(fill.fill_id))

    conn = sqlite3.connect(str(tmp_path / "trigger.db"))
    balances_post = [tuple(r) for r in conn.execute("SELECT * FROM paper_account_balances ORDER BY asset").fetchall()]
    assert balances_post == balances_pre

    reservation_post = [tuple(r) for r in conn.execute("SELECT * FROM paper_reservations WHERE owner_order_id='order-1' ORDER BY reservation_id").fetchall()]
    assert reservation_post == reservation_pre

    lot_post = [tuple(r) for r in conn.execute("SELECT * FROM position_lots WHERE order_id='order-1' ORDER BY order_id").fetchall()]
    assert lot_post == lot_pre

    order_post = [tuple(r) for r in conn.execute("SELECT * FROM paper_orders WHERE order_id='order-1' ORDER BY order_id").fetchall()]
    assert order_post == order_pre

    apps_post = [tuple(r) for r in conn.execute("SELECT * FROM paper_fill_applications WHERE order_id='order-1' ORDER BY fill_id").fetchall()]
    assert apps_post == apps_pre

    journal_post = [tuple(r) for r in conn.execute("SELECT * FROM paper_fills WHERE fill_id='trigger-order' ORDER BY fill_id").fetchall()]
    assert journal_post == journal_pre

    acceptance_post = [tuple(r) for r in conn.execute("SELECT * FROM paper_order_acceptances WHERE order_id='order-1' ORDER BY order_id").fetchall()]
    assert acceptance_post == acceptance_pre
    conn.close()


def test_save_paper_fill_rollback_on_journal_insert(tmp_path: Path) -> None:
    """Trigger AFTER INSERT ON paper_fills fuerza fallo de save_paper_fill;
    la fila no queda en el journal y el resto del estado no cambia."""
    db = tmp_path / "rollback-journal.db"
    store = _make_store(db)
    _open_account(store)

    order = _make_order(
        order_type=PaperOrderType.LIMIT,
        requested_qty="2.0",
        price="50.0",
        max_quote_amount="100.0",
    )
    _run(store.register_paper_order(order))
    _accept_order(store, order)

    # Snapshots antes del save
    conn = sqlite3.connect(str(db))
    balances_pre = [tuple(r) for r in conn.execute("SELECT * FROM paper_account_balances ORDER BY asset").fetchall()]
    reservation_pre = [tuple(r) for r in conn.execute("SELECT * FROM paper_reservations WHERE owner_order_id='order-1' ORDER BY reservation_id").fetchall()]
    lot_pre = [tuple(r) for r in conn.execute("SELECT * FROM position_lots WHERE order_id='order-1' ORDER BY order_id").fetchall()]
    order_pre = [tuple(r) for r in conn.execute("SELECT * FROM paper_orders WHERE order_id='order-1' ORDER BY order_id").fetchall()]
    journal_pre = [tuple(r) for r in conn.execute("SELECT * FROM paper_fills ORDER BY fill_id").fetchall()]
    acceptance_pre = [tuple(r) for r in conn.execute("SELECT * FROM paper_order_acceptances WHERE order_id='order-1' ORDER BY order_id").fetchall()]
    conn.close()

    # Inyectar trigger DESPUÉS de que la cuenta esté abierta
    trigger = """
    CREATE TRIGGER IF NOT EXISTS fail_after_fill_insert
    AFTER INSERT ON paper_fills
    WHEN (SELECT COUNT(*) FROM _trigger_control WHERE action='enabled') > 0
    BEGIN
        SELECT RAISE(ABORT, 'trigger-fail');
    END;
    """
    _inject_trigger(db, trigger)

    fill = _make_fill(fill_id="rollback-fill", qty="1.0", price="40.0")

    _set_trigger_flag(db, True)
    with pytest.raises(sqlite3.IntegrityError, match="trigger-fail"):
        _run(store.save_paper_fill(fill))

    # Post-fallo: comparar exactamente cada snapshot
    conn = sqlite3.connect(str(db))
    balances_post = [tuple(r) for r in conn.execute("SELECT * FROM paper_account_balances ORDER BY asset").fetchall()]
    assert balances_post == balances_pre

    reservation_post = [tuple(r) for r in conn.execute("SELECT * FROM paper_reservations WHERE owner_order_id='order-1' ORDER BY reservation_id").fetchall()]
    assert reservation_post == reservation_pre

    lot_post = [tuple(r) for r in conn.execute("SELECT * FROM position_lots WHERE order_id='order-1' ORDER BY order_id").fetchall()]
    assert lot_post == lot_pre

    order_post = [tuple(r) for r in conn.execute("SELECT * FROM paper_orders WHERE order_id='order-1' ORDER BY order_id").fetchall()]
    assert order_post == order_pre

    journal_post = [tuple(r) for r in conn.execute("SELECT * FROM paper_fills ORDER BY fill_id").fetchall()]
    assert journal_post == journal_pre

    acceptance_post = [tuple(r) for r in conn.execute("SELECT * FROM paper_order_acceptances WHERE order_id='order-1' ORDER BY order_id").fetchall()]
    assert acceptance_post == acceptance_pre

    # Verificar que la fila no quedó en el journal
    fill_count = conn.execute(
        "SELECT COUNT(*) FROM paper_fills WHERE fill_id='rollback-fill'"
    ).fetchone()[0]
    assert fill_count == 0
    conn.close()


# ── Precisión decimal en BUY (regresión pre-fix) ───────────────────────────────

def test_buy_fill_precision_under_restricted_global_context(tmp_path: Path) -> None:
    """BUY con qty=1, price=1.23456789 y commission=0 debe registrar coste exacto 1.23456789
    incluso cuando el contexto decimal global tiene prec=6 y ROUND_DOWN.

    El bug actual redondea el principal a 1.23456 en lugar de 1.23456789.
    Este test es una regresión de integración que debe FALLAR antes del fix y PASAR después.
    """
    import decimal as dec_mod

    # 1. Preparar y aceptar órdenes con contexto normal
    db = tmp_path / "buy-precision.db"
    store = _make_store(db)
    _open_account(store)

    order = _make_order(
        order_id="order-prec",
        order_type=PaperOrderType.MARKET,
        requested_qty="1.0",
        max_quote_amount="10.0",
    )
    _run(store.register_paper_order(order))
    _accept_order(store, order)

    fill = _make_fill(
        fill_id="fill-prec",
        order_id="order-prec",
        qty="1.0",
        price="1.23456789",
        commission=Commission(amount=Decimal("0"), asset="QUOTE"),
    )
    _run(store.save_paper_fill(fill))

    order2 = _make_order(
        order_id="order-prec-2",
        order_type=PaperOrderType.MARKET,
        requested_qty="1.0",
        max_quote_amount="10.0",
    )
    _run(store.register_paper_order(order2))
    _accept_order(store, order2)

    fill2 = _make_fill(
        fill_id="fill-prec-2",
        order_id="order-prec-2",
        qty="1.0",
        price="2.34567890",
        commission=Commission(amount=Decimal("0"), asset="QUOTE"),
    )
    _run(store.save_paper_fill(fill2))

    # 2. Aplicar ambos fills bajo prec=6, ROUND_DOWN en la misma cuenta
    old_ctx = dec_mod.getcontext().copy()
    try:
        dec_mod.getcontext().prec = 6
        dec_mod.getcontext().rounding = dec_mod.ROUND_DOWN

        # Guardar copia del contexto restringido (incluye flags) para comparar después
        ctx_restricted_copy = dec_mod.getcontext().copy()

        applied = _run(store.apply_paper_fill("fill-prec"))
        assert applied.fill_id == "fill-prec"

        # ── Releer balance modificado tras primer BUY (misma cuenta) ──
        conn = sqlite3.connect(str(db))
        quote_bal_1 = _asset_balance_json_to_model(
            conn.execute("SELECT balance_json FROM paper_account_balances WHERE account_id='acc-1' AND asset='QUOTE'").fetchone()[0]
        )
        # tras primer BUY: total = 9998.76543211, available = 9998.76543211 - 10.0 (reserved), reserved = 10.0
        assert quote_bal_1.total == Decimal("9998.76543211"), \
            f"QUOTE total tras primer BUY esperado 9998.76543211, obtenido {quote_bal_1.total}"
        assert quote_bal_1.available == Decimal("9988.76543211"), \
            f"QUOTE available esperado 9988.76543211, obtenido {quote_bal_1.available}"
        assert quote_bal_1.reserved == Decimal("10.0"), \
            f"QUOTE reserved esperado 10.0, obtenido {quote_bal_1.reserved}"

        # Este segundo apply_paper_fill lee balances existentes bajo prec=6;
        # con el fix debe pasar sin ValueError del validador.
        applied2 = _run(store.apply_paper_fill("fill-prec-2"))
        assert applied2.fill_id == "fill-prec-2"

        # 3. Comparar contexto actual con la copia restringida ANTES de restaurar old_ctx
        ctx_during = dec_mod.getcontext()
        assert ctx_during.prec == ctx_restricted_copy.prec, \
            f"prec alterado durante operaciones: {ctx_during.prec} != {ctx_restricted_copy.prec}"
        assert ctx_during.rounding == ctx_restricted_copy.rounding, \
            f"rounding alterado durante operaciones: {ctx_during.rounding} != {ctx_restricted_copy.rounding}"
        # Comparar flags explícitamente
        assert ctx_during.flags == ctx_restricted_copy.flags, \
            f"flags alterado durante operaciones: {ctx_during.flags} != {ctx_restricted_copy.flags}"
        # Comparar traps explícitamente
        for trap_name in ("traps",):
            assert getattr(ctx_during, trap_name) == getattr(ctx_restricted_copy, trap_name), \
                f"trap {trap_name} alterado: {getattr(ctx_during, trap_name)} != {getattr(ctx_restricted_copy, trap_name)}"

        # 4. Restaurar contexto original y calcular expectativas EXACTAS fuera del contexto reducido
        dec_mod.setcontext(old_ctx)

        # Verificar que el contexto global quedó intacto tras restaurar
        ctx_after = dec_mod.getcontext()
        assert ctx_after.prec == old_ctx.prec, "El prec global fue alterado tras restaurar"
        assert ctx_after.rounding == old_ctx.rounding, "El rounding global fue alterado tras restaurar"

        # expectativas exactas (fuera del contexto reducido)
        expected_principal = Decimal("1.23456789")
        expected_commission = Decimal("0")
        expected_total_debit = expected_principal + expected_commission  # 1.23456789

        # 5. Verificar balances y lote con valores exactos (cuenta 1)
        # QUOTE balance: total = available == 9996.41975321, reserved == 0
        quote_bal = AssetBalance.model_validate_json(
            conn.execute("SELECT balance_json FROM paper_account_balances WHERE account_id='acc-1' AND asset='QUOTE'").fetchone()[0]
        )
        assert quote_bal.total == Decimal("9996.41975321"), \
            f"QUOTE total esperado 9996.41975321, obtenido {quote_bal.total}"
        assert quote_bal.available == Decimal("9996.41975321"), \
            f"QUOTE available esperado 9996.41975321, obtenido {quote_bal.available}"
        assert quote_bal.reserved == Decimal("0"), \
            f"QUOTE reserved esperado 0, obtenido {quote_bal.reserved}"

        # BASE balance: total == available == 2.0
        base_bal = AssetBalance.model_validate_json(
            conn.execute("SELECT balance_json FROM paper_account_balances WHERE account_id='acc-1' AND asset='BASE'").fetchone()[0]
        )
        assert base_bal.total == Decimal("2.0"), f"BASE total esperado 2.0, obtenido {base_bal.total}"
        assert base_bal.available == Decimal("2.0"), f"BASE available esperado 2.0, obtenido {base_bal.available}"

        # position_lots: total_cost debe ser EXACTAMENTE 1.23456789
        lot_row = conn.execute("SELECT lot_json FROM position_lots WHERE order_id='order-prec'").fetchone()
        assert lot_row is not None, "position_lots no encontrado para order-prec"
        from src.persistence.sqlite_store import PositionLot
        lot = PositionLot.model_validate_json(lot_row[0])
        assert lot.total_cost == Decimal("1.23456789"), \
            f"Lot cost esperado 1.23456789, obtenido {lot.total_cost} (bug de precisión detectado)"
        assert lot.total_qty == Decimal("1.0")

        # position_lots segundo: total_cost debe ser EXACTAMENTE 2.34567890
        lot_row2 = conn.execute("SELECT lot_json FROM position_lots WHERE order_id='order-prec-2'").fetchone()
        assert lot_row2 is not None
        lot2 = PositionLot.model_validate_json(lot_row2[0])
        assert lot2.total_cost == Decimal("2.34567890"), \
            f"Segundo Lot cost esperado 2.34567890, obtenido {lot2.total_cost}"

        # durable mark
        app_row = conn.execute("SELECT fill_id FROM paper_fill_applications WHERE fill_id='fill-prec'").fetchone()
        assert app_row is not None
        app_row2 = conn.execute("SELECT fill_id FROM paper_fill_applications WHERE fill_id='fill-prec-2'").fetchone()
        assert app_row2 is not None

        # order status FILLED
        order_row = conn.execute("SELECT order_json FROM paper_orders WHERE order_id='order-prec'").fetchone()
        loaded_order = PaperOrder.model_validate_json(order_row[0])
        assert loaded_order.status == PaperOrderStatus.FILLED
        order_row2 = conn.execute("SELECT order_json FROM paper_orders WHERE order_id='order-prec-2'").fetchone()
        loaded_order2 = PaperOrder.model_validate_json(order_row2[0])
        assert loaded_order2.status == PaperOrderStatus.FILLED

        conn.close()
        _run(store.close())
    finally:
        dec_mod.setcontext(old_ctx)
