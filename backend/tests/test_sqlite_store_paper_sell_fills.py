"""Focused tests for PAPER SELL acceptance, fills, and cancellation.

Slice: ACT-P-002 — microtask SELL (cancelación, fills, invariantes).
Estado: EN_REVISION — pendiente de revisión humana.

Cobertura:
- Aceptación SELL: reserva BASE + lotes atómica
- Fills SELL: parcial idempotente, rollback, cierre completo de lote
- Cancelación SELL: ACCEPTED/PARTIALLY_FILLED, idempotencia, terminal rejection
- Cancelación rollback: trigger forzado durante cancelación → estado preservado
- Cancelación balance invariants: total/available/reserved exactos post-cancel
- Fill-then-cancel: fills/accounting preservados tras cancelación
- Cancel-then-fill: fill rechazado tras cancelación
- Race cancel-vs-fill: dos store instances + asyncio.gather, serialización correcta
- Integridad de columnas indexadas: mismatch de base_asset, initial_qty, remaining_qty
- Integridad de BASE asset: reserva con activo incorrecto vs símbolo del orden

Todos los tests de esta suite y las 6 suites del gate pasan.

Cambios en este slice:
- backend/tests/test_sqlite_store_paper_sell_fills.py: +7 tests de cancelación/race
- backend/tests/test_sqlite_store_paper_balances.py: corrección de import faltante
  _SCHEMA_V11_TABLES (test_v6_to_v10_migration_preserves_paper_fill)

No hacer staging, commit ni push. Pendiente de revisión humana.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
from decimal import Context, Decimal, ROUND_DOWN, ROUND_HALF_EVEN, getcontext, localcontext
from pathlib import Path
from typing import Any

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
from src.models.paper_risk import PaperRiskPolicy, PaperRiskSnapshot
from src.models.paper_symbol_filters import LotSizeFilter, MinNotionalFilter, MarketLotSizeFilter, PriceFilter, SymbolFilterSnapshot
from src.persistence.sqlite_store import (
    PaperOrderAcceptanceFundsError,
    PaperOrderAcceptanceIntegrityError,
    PaperOrderAcceptanceStateError,
    PaperOrderCancellationStateError,
    PositionLot,
    SQLiteStore,
    _PaperLotReservationAllocationV1,
    _PaperSellAccountingRecordV1,
    _exact_sum,
)


def _run(coro: Any) -> Any:
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _open_account(db: Path, *, base: str = "0", quote: str = "10000") -> SQLiteStore:
    store = SQLiteStore(db)
    _run(store.initialize())
    _run(
        store.open_paper_account(
            PaperAccountOpening(
                environment="paper",
                account_id="acct-sell",
                balances=(
                    AssetBalance(
                        asset="BTC", total=Decimal(base), available=Decimal(base),
                        reserved=Decimal("0"),
                    ),
                    AssetBalance(
                        asset="USDT", total=Decimal(quote), available=Decimal(quote),
                        reserved=Decimal("0"),
                    ),
                ),
            )
        )
    )
    _run(
        store.initialize_lifecycle_state("acct-sell", "session-sell", 1000)
    )
    return store


def _accept_buy(
    store: SQLiteStore,
    order: PaperOrder,
    *,
    max_exposure: Decimal = Decimal("1000000"),
    marked_open_inventory: Decimal = Decimal("0"),
):
    base, quote = order.symbol.split("/")
    policy = PaperRiskPolicy(policy_id="fixture", policy_version="1", account_id=order.paper_account_id, quote_asset=quote,
        max_order_notional=Decimal("1000000"), max_total_exposure=max_exposure, max_daily_loss=Decimal("1000000"))
    snapshot = PaperRiskSnapshot(account_id=order.paper_account_id, quote_asset=quote, market_symbol=order.symbol,
        market_mark_price=Decimal("50"), market_reference="fixture", market_timestamp_ms=1,
        equity_reference="fixture", equity_timestamp_ms=1, equity_utc_day_start=Decimal("1000000"),
        equity_current_marked=Decimal("1000000"), exposure_reference="fixture", exposure_timestamp_ms=1,
        marked_open_inventory=marked_open_inventory, remaining_buy_reservations=Decimal("0"))
    filters = SymbolFilterSnapshot(snapshot_id="fixture", filter_set_id="fixture", filter_set_version="1",
        source_id="fixture", source_version="1", capture_timestamp_ms=1, symbol=order.symbol, base_asset=base, quote_asset=quote,
        price_filter=PriceFilter(min_price=Decimal("0"), max_price=Decimal("0"), tick_size=Decimal("0")),
        lot_size=LotSizeFilter(min_qty=Decimal("0.0000000001"), max_qty=Decimal("100000000"), step_size=order.requested_qty),
        market_lot_size=MarketLotSizeFilter(min_qty=Decimal("0.0000000001"), max_qty=Decimal("100000000"), step_size=order.requested_qty),
        min_notional=MinNotionalFilter(min_notional=Decimal("0.00000001"), applies_to_market=False, avg_price_mins=0),
        notional=None, unhandled_filter_types=())
    return _run(store.accept_paper_order(order.order_id, policy, snapshot, filters))


def _seed_buy_lot(
    store: SQLiteStore,
    order_id: str,
    *,
    qty: str = "2.0",
    strategy_ref: StrategyRef | None = None,
    symbol: str = "BTC/USDT",
    price: str = "50",
    fill_price: str = "40",
) -> PaperOrder:
    strategy_ref = strategy_ref or StrategyRef(
        strategy_id="strategy-sell", strategy_version="v1", decision_id="decision-sell"
    )
    order = PaperOrder(
        order_id=order_id,
        client_order_id=f"client-{order_id}",
        paper_account_id="acct-sell",
        paper_session_id="session-sell",
        environment="paper",
        symbol=symbol,
        side=PaperOrderSide.BUY,
        order_type=PaperOrderType.LIMIT,
        requested_qty=Decimal(qty),
        price=Decimal(price),
        strategy_ref=strategy_ref,
        status=PaperOrderStatus.PREPARED,
        timestamp_ms=1_700_000_000_000,
    )
    _run(store.register_paper_order(order))
    _accept_buy(store, order)
    fill = PaperFill(
        fill_id=f"fill-{order_id}",
        order_id=order.order_id,
        qty=Decimal(qty),
        price=Decimal(fill_price),
        timestamp_ms=1_700_000_001_000,
        commission=Commission(amount=Decimal("0.5"), asset=symbol.split("/")[1]),
    )
    _run(store.save_paper_fill(fill))
    _run(store.apply_paper_fill(fill.fill_id))
    return order


def _sell_order(
    order_id: str,
    allocations: tuple[LotAllocation, ...],
    *,
    order_type: PaperOrderType = PaperOrderType.LIMIT,
    price: Decimal | None = Decimal("45"),
    strategy_ref: StrategyRef | None = None,
    symbol: str = "BTC/USDT",
) -> PaperOrder:
    strategy_ref = strategy_ref or StrategyRef(
        strategy_id="strategy-sell", strategy_version="v1", decision_id="decision-sell"
    )
    qty = sum((allocation.qty for allocation in allocations), Decimal("0"))
    return PaperOrder(
        order_id=order_id,
        client_order_id=f"client-{order_id}",
        paper_account_id="acct-sell",
        paper_session_id="session-sell",
        environment="paper",
        symbol=symbol,
        side=PaperOrderSide.SELL,
        order_type=order_type,
        requested_qty=qty,
        price=price,
        strategy_ref=strategy_ref,
        status=PaperOrderStatus.PREPARED,
        timestamp_ms=1_700_000_002_000,
        lot_allocations=allocations,
    )


def _sell_fill(order: PaperOrder, *, fill_id: str, qty: str, price: str) -> PaperFill:
    allocations = tuple(
        LotAllocation(
            position_id=allocation.position_id,
            account_id=allocation.account_id,
            symbol=allocation.symbol,
            strategy_ref=allocation.strategy_ref,
            qty=Decimal(qty),
        )
        for allocation in order.lot_allocations
    )
    if len(allocations) != 1:
        raise AssertionError("helper expects one lot allocation")
    return PaperFill(
        fill_id=fill_id,
        order_id=order.order_id,
        qty=Decimal(qty),
        price=Decimal(price),
        timestamp_ms=1_700_000_003_000,
        commission=Commission(amount=Decimal("0.6"), asset="USDT"),
        lot_allocations=allocations,
    )
def test_sell_limit_acceptance_reserves_base_and_lot_atomically(tmp_path: Path) -> None:
    db = tmp_path / "sell-acceptance.db"
    store = _open_account(db)
    buy = _seed_buy_lot(store, "buy-lot-1")
    allocation = LotAllocation(
        position_id=buy.order_id,
        account_id=buy.paper_account_id,
        symbol=buy.symbol,
        strategy_ref=buy.strategy_ref,
        qty=Decimal("1.5"),
    )
    sell = _sell_order("sell-1", (allocation,), strategy_ref=buy.strategy_ref)
    _run(store.register_paper_order(sell))

    quote_before = _run(store.load_paper_balance_view("acct-sell"))
    accepted = _run(store.accept_paper_order(sell.order_id))

    assert accepted.status == PaperOrderStatus.ACCEPTED
    reservation = _run(store.load_paper_reservation(sell.order_id))
    assert reservation is not None
    assert reservation.amount.asset == "BTC"
    assert reservation.amount.amount == Decimal("1.5")
    balances = _run(store.load_paper_balance_view("acct-sell"))
    assert balances is not None and quote_before is not None
    base_after = next(balance for balance in balances.balances if balance.asset == "BTC")
    base_before = next(balance for balance in quote_before.balances if balance.asset == "BTC")
    quote_after = next(balance for balance in balances.balances if balance.asset == "USDT")
    quote_prior = next(balance for balance in quote_before.balances if balance.asset == "USDT")
    assert (base_after.total, base_after.available, base_after.reserved) == (
        Decimal("2.0"), Decimal("0.5"), Decimal("1.5")
    )
    assert quote_after == quote_prior

    conn = sqlite3.connect(str(db))
    rows = conn.execute(
        "SELECT allocation_json FROM paper_reservation_lot_allocations "
        "WHERE reservation_id=?",
        (sell.order_id,),
    ).fetchall()
    assert len(rows) == 1
    lot_reservation = _PaperLotReservationAllocationV1.model_validate_json(rows[0][0])
    assert lot_reservation.owner_order_id == sell.order_id
    assert lot_reservation.position_id == buy.order_id
    assert lot_reservation.initial_qty == Decimal("1.5")
    assert lot_reservation.remaining_qty == Decimal("1.5")
    snapshot = conn.execute(
        "SELECT initial_order_json, reservation_json "
        "FROM paper_order_acceptances WHERE order_id=?",
        (sell.order_id,),
    ).fetchone()
    assert snapshot is not None
    assert PaperOrder.model_validate_json(snapshot[0]).status == PaperOrderStatus.PREPARED
    assert PaperReservationRef.model_validate_json(snapshot[1]).amount == reservation.amount
    conn.close()
    _run(store.close())


def test_sell_reservation_is_not_double_counted_as_buy_quote_reservation(
    tmp_path: Path,
) -> None:
    db = tmp_path / "sell-reservation-not-buy-exposure.db"
    store = _open_account(db, base="100", quote="10000")
    # Buy USDT as a position, then reserve those USDT in a SELL. The lot remains
    # marked exposure; its SELL reservation is not an additional BUY commitment.
    buy_usdt = _seed_buy_lot(
        store,
        "buy-usdt-lot",
        qty="40",
        symbol="USDT/BTC",
        price="2",
        fill_price="1",
    )
    allocation = LotAllocation(
        position_id=buy_usdt.order_id,
        account_id=buy_usdt.paper_account_id,
        symbol=buy_usdt.symbol,
        strategy_ref=buy_usdt.strategy_ref,
        qty=buy_usdt.requested_qty,
    )
    sell_usdt = _sell_order(
        "sell-usdt-lot",
        (allocation,),
        price=Decimal("0.4"),
        strategy_ref=buy_usdt.strategy_ref,
        symbol=buy_usdt.symbol,
    )
    _run(store.register_paper_order(sell_usdt))
    _run(store.accept_paper_order(sell_usdt.order_id))
    sell_reservation = _run(store.load_paper_reservation(sell_usdt.order_id))
    assert sell_reservation is not None
    assert sell_reservation.amount.asset == "USDT"
    assert sell_reservation.amount.amount == Decimal("40")

    buy_btc = PaperOrder(
        order_id="buy-btc-after-sell-reserve",
        client_order_id="client-buy-btc-after-sell-reserve",
        paper_account_id="acct-sell",
        paper_session_id="session-sell",
        environment="paper",
        symbol="BTC/USDT",
        side=PaperOrderSide.BUY,
        order_type=PaperOrderType.LIMIT,
        requested_qty=Decimal("2"),
        price=Decimal("10"),
        strategy_ref=StrategyRef(
            strategy_id="strategy-sell", strategy_version="v1", decision_id="decision-sell"
        ),
        status=PaperOrderStatus.PREPARED,
        timestamp_ms=1_700_000_010_000,
    )
    _run(store.register_paper_order(buy_btc))
    accepted = _accept_buy(
        store,
        buy_btc,
        max_exposure=Decimal("65"),
        marked_open_inventory=Decimal("40"),
    )
    assert accepted.status == PaperOrderStatus.ACCEPTED
    balance = _run(store.load_paper_balance_view("acct-sell"))
    assert balance is not None
    usdt_balance = next(item for item in balance.balances if item.asset == "USDT")
    assert (usdt_balance.total, usdt_balance.available, usdt_balance.reserved) == (
        Decimal("10040.0"), Decimal("9980.0"), Decimal("60.0")
    )
    _run(store.close())


def test_sell_market_acceptance_reserves_base_without_quote_budget(tmp_path: Path) -> None:
    db = tmp_path / "sell-market-acceptance.db"
    store = _open_account(db)
    buy = _seed_buy_lot(store, "buy-lot-market")
    allocation = LotAllocation(
        position_id=buy.order_id,
        account_id=buy.paper_account_id,
        symbol=buy.symbol,
        strategy_ref=buy.strategy_ref,
        qty=Decimal("1.0"),
    )
    sell = _sell_order(
        "sell-market",
        (allocation,),
        order_type=PaperOrderType.MARKET,
        price=None,
        strategy_ref=buy.strategy_ref,
    )
    _run(store.register_paper_order(sell))

    accepted = _run(store.accept_paper_order(sell.order_id))
    assert accepted.status == PaperOrderStatus.ACCEPTED
    reservation = _run(store.load_paper_reservation(sell.order_id))
    assert reservation is not None
    assert (reservation.amount.asset, reservation.amount.amount) == (
        "BTC", Decimal("1.0")
    )
    balances = _run(store.load_paper_balance_view("acct-sell"))
    assert balances is not None
    base = next(balance for balance in balances.balances if balance.asset == "BTC")
    quote = next(balance for balance in balances.balances if balance.asset == "USDT")
    assert (base.total, base.available, base.reserved) == (
        Decimal("2.0"), Decimal("1.0"), Decimal("1.0")
    )
    assert quote.total == Decimal("9919.5")
    _run(store.close())


def test_sell_acceptance_reserves_multiple_lots_for_one_strategy(tmp_path: Path) -> None:
    db = tmp_path / "sell-multiple-lots.db"
    store = _open_account(db)
    first = _seed_buy_lot(store, "buy-lot-a")
    second = _seed_buy_lot(store, "buy-lot-b")
    allocations = (
        LotAllocation(
            position_id=first.order_id,
            account_id=first.paper_account_id,
            symbol=first.symbol,
            strategy_ref=first.strategy_ref,
            qty=Decimal("0.5"),
        ),
        LotAllocation(
            position_id=second.order_id,
            account_id=second.paper_account_id,
            symbol=second.symbol,
            strategy_ref=second.strategy_ref,
            qty=Decimal("1.0"),
        ),
    )
    sell = _sell_order("sell-two-lots", allocations, strategy_ref=first.strategy_ref)
    _run(store.register_paper_order(sell))
    accepted = _run(store.accept_paper_order(sell.order_id))

    assert accepted.status == PaperOrderStatus.ACCEPTED
    reservation = _run(store.load_paper_reservation(sell.order_id))
    assert reservation is not None
    assert reservation.amount.amount == Decimal("1.5")
    conn = sqlite3.connect(str(db))
    rows = conn.execute(
        "SELECT position_id, remaining_qty FROM paper_reservation_lot_allocations "
        "WHERE reservation_id=? ORDER BY position_id",
        (sell.order_id,),
    ).fetchall()
    assert rows == [("buy-lot-a", "0.5"), ("buy-lot-b", "1.0")]
    conn.close()
    _run(store.close())


def test_sell_acceptance_rejects_over_reserving_one_lot(tmp_path: Path) -> None:
    db = tmp_path / "sell-lot-over-reservation.db"
    store = _open_account(db, base="10")
    buy = _seed_buy_lot(store, "buy-lot-over-reserved")

    def _sell(order_id: str, qty: str) -> PaperOrder:
        allocation = LotAllocation(
            position_id=buy.order_id,
            account_id=buy.paper_account_id,
            symbol=buy.symbol,
            strategy_ref=buy.strategy_ref,
            qty=Decimal(qty),
        )
        return _sell_order(order_id, (allocation,), strategy_ref=buy.strategy_ref)

    first = _sell("sell-first", "1.0")
    _run(store.register_paper_order(first))
    _run(store.accept_paper_order(first.order_id))
    before = _run(store.load_paper_balance_view("acct-sell"))
    assert before is not None

    second = _sell("sell-over-reserved", "1.5")
    _run(store.register_paper_order(second))
    with pytest.raises(PaperOrderAcceptanceFundsError, match="exceeds unreserved lot quantity"):
        _run(store.accept_paper_order(second.order_id))

    after = _run(store.load_paper_balance_view("acct-sell"))
    assert after == before
    assert _run(store.load_paper_reservation(first.order_id)) is not None
    assert _run(store.load_paper_reservation(second.order_id)) is None
    conn = sqlite3.connect(str(db))
    assert conn.execute(
        "SELECT COUNT(*) FROM paper_reservation_lot_allocations WHERE owner_order_id=?",
        (first.order_id,),
    ).fetchone()[0] == 1
    assert conn.execute(
        "SELECT COUNT(*) FROM paper_reservation_lot_allocations WHERE owner_order_id=?",
        (second.order_id,),
    ).fetchone()[0] == 0
    assert conn.execute(
        "SELECT COUNT(*) FROM paper_order_acceptances WHERE order_id=?", (second.order_id,)
    ).fetchone()[0] == 0
    conn.close()
    _run(store.close())


def test_sell_acceptance_rejects_lot_from_another_strategy(tmp_path: Path) -> None:
    db = tmp_path / "sell-wrong-strategy-lot.db"
    store = _open_account(db)
    buy = _seed_buy_lot(store, "buy-lot-owner")
    other_strategy = StrategyRef(
        strategy_id="other-strategy", strategy_version="v1", decision_id="other-decision"
    )
    allocation = LotAllocation(
        position_id=buy.order_id,
        account_id=buy.paper_account_id,
        symbol=buy.symbol,
        strategy_ref=other_strategy,
        qty=Decimal("1.0"),
    )
    sell = _sell_order("sell-wrong-strategy", (allocation,), strategy_ref=other_strategy)
    _run(store.register_paper_order(sell))
    before = _run(store.load_paper_balance_view("acct-sell"))

    with pytest.raises(PaperOrderAcceptanceStateError, match="outside the order's account, symbol, or strategy"):
        _run(store.accept_paper_order(sell.order_id))

    assert _run(store.load_paper_balance_view("acct-sell")) == before
    assert _run(store.load_paper_reservation(sell.order_id)) is None
    conn = sqlite3.connect(str(db))
    assert conn.execute(
        "SELECT COUNT(*) FROM paper_order_acceptances WHERE order_id=?", (sell.order_id,)
    ).fetchone()[0] == 0
    assert conn.execute(
        "SELECT COUNT(*) FROM paper_reservation_lot_allocations WHERE owner_order_id=?",
        (sell.order_id,),
    ).fetchone()[0] == 0
    conn.close()
    _run(store.close())


def test_sell_partial_fill_updates_lot_balances_and_accounting_idempotently(
    tmp_path: Path,
) -> None:
    db = tmp_path / "sell-partial-fill.db"
    store = _open_account(db)
    buy = _seed_buy_lot(store, "buy-partial-fill")
    allocation = LotAllocation(
        position_id=buy.order_id,
        account_id=buy.paper_account_id,
        symbol=buy.symbol,
        strategy_ref=buy.strategy_ref,
        qty=Decimal("1.5"),
    )
    sell = _sell_order("sell-partial", (allocation,), strategy_ref=buy.strategy_ref)
    _run(store.register_paper_order(sell))
    _run(store.accept_paper_order(sell.order_id))
    fill = _sell_fill(sell, fill_id="sell-partial-fill", qty="1.0", price="60")
    _run(store.save_paper_fill(fill))

    _run(store.apply_paper_fill(fill.fill_id))
    applied = _run(store.load_paper_order(sell.order_id))
    balances = _run(store.load_paper_balance_view("acct-sell"))
    reservation = _run(store.load_paper_reservation(sell.order_id))
    assert applied is not None and applied.status == PaperOrderStatus.PARTIALLY_FILLED
    assert len(applied.fills) == 1
    assert balances is not None and reservation is not None
    base = next(item for item in balances.balances if item.asset == "BTC")
    quote = next(item for item in balances.balances if item.asset == "USDT")
    assert (base.total, base.available, base.reserved) == (
        Decimal("1.0"), Decimal("0.5"), Decimal("0.5")
    )
    assert (quote.total, quote.available, quote.reserved) == (
        Decimal("9978.9"), Decimal("9978.9"), Decimal("0")
    )
    assert (reservation.amount.asset, reservation.amount.amount) == (
        "BTC", Decimal("0.5")
    )
    conn = sqlite3.connect(str(db))
    lot = PositionLot.model_validate_json(
        conn.execute("SELECT lot_json FROM position_lots WHERE order_id=?", (buy.order_id,)).fetchone()[0]
    )
    assert (lot.total_qty, lot.total_cost) == (Decimal("1.0"), Decimal("40"))
    lot_reservation = _PaperLotReservationAllocationV1.model_validate_json(
        conn.execute(
            "SELECT allocation_json FROM paper_reservation_lot_allocations WHERE owner_order_id=?",
            (sell.order_id,),
        ).fetchone()[0]
    )
    assert lot_reservation.remaining_qty == Decimal("0.5")
    accounting = conn.execute(
        "SELECT accounting_json FROM paper_sell_accounting WHERE fill_id=?",
        (fill.fill_id,),
    ).fetchone()
    assert accounting is not None
    record = _PaperSellAccountingRecordV1.model_validate_json(accounting[0])
    assert (record.proceeds_gross, record.proceeds_net, record.fee_amount) == (
        Decimal("60"), Decimal("59.4"), Decimal("0.6")
    )
    assert (record.total_cost_basis_removed, record.pnl_gross, record.pnl_net) == (
        Decimal("40"), Decimal("20"), Decimal("19.4")
    )
    conn.close()

    _run(store.apply_paper_fill(fill.fill_id))
    assert _run(store.load_paper_order(sell.order_id)) == applied
    conn = sqlite3.connect(str(db))
    assert conn.execute(
        "SELECT COUNT(*) FROM paper_fill_applications WHERE fill_id=?", (fill.fill_id,)
    ).fetchone()[0] == 1
    assert conn.execute(
        "SELECT COUNT(*) FROM paper_sell_accounting WHERE fill_id=?", (fill.fill_id,)
    ).fetchone()[0] == 1
    conn.close()
    _run(store.close())


def test_sell_partial_proration_uses_exact_product_before_34_digit_division(
    tmp_path: Path,
) -> None:
    db = tmp_path / "sell-direct-proration.db"
    store = _open_account(db)
    buy = _seed_buy_lot(store, "buy-direct-proration", qty="3.0")

    # Set an exact cost/quantity ratio that distinguishes direct proration from
    # a previously rounded average: 1 * 2 / 3 rounds to ...6667, while
    # round(1 / 3) * 2 produces ...6666.
    conn = sqlite3.connect(str(db))
    lot_row = conn.execute(
        "SELECT lot_json FROM position_lots WHERE order_id=?", (buy.order_id,)
    ).fetchone()
    assert lot_row is not None
    buy_lot = PositionLot.model_validate_json(lot_row[0])
    conn.execute(
        "UPDATE position_lots SET lot_json=? WHERE order_id=?",
        (
            PositionLot(
                order_id=buy_lot.order_id,
                account_id=buy_lot.account_id,
                paper_session_id=buy_lot.paper_session_id,
                environment=buy_lot.environment,
                total_qty=Decimal("3"),
                total_cost=Decimal("1"),
            ).model_dump_json(),
            buy.order_id,
        ),
    )
    conn.commit()
    conn.close()

    allocation = LotAllocation(
        position_id=buy.order_id,
        account_id=buy.paper_account_id,
        symbol=buy.symbol,
        strategy_ref=buy.strategy_ref,
        qty=Decimal("3"),
    )
    sell = _sell_order("sell-direct-proration", (allocation,), strategy_ref=buy.strategy_ref)
    _run(store.register_paper_order(sell))
    _run(store.accept_paper_order(sell.order_id))

    first_fill = _sell_fill(
        sell, fill_id="sell-direct-proration-1", qty="2", price="60"
    )
    _run(store.save_paper_fill(first_fill))
    original_context = getcontext().copy()
    with localcontext() as global_context:
        global_context.prec = 6
        global_context.rounding = ROUND_DOWN
        _run(store.apply_paper_fill(first_fill.fill_id))

        conn = sqlite3.connect(str(db))
        remaining_row = conn.execute(
            "SELECT lot_json FROM position_lots WHERE order_id=?", (buy.order_id,)
        ).fetchone()
        assert remaining_row is not None
        remaining_lot = PositionLot.model_validate_json(remaining_row[0])
        first_accounting = _PaperSellAccountingRecordV1.model_validate_json(
            conn.execute(
                "SELECT accounting_json FROM paper_sell_accounting WHERE fill_id=?",
                (first_fill.fill_id,),
            ).fetchone()[0]
        )
        conn.close()

        expected_removed = Decimal("0.6666666666666666666666666666666667")
        expected_residual = Decimal("0.3333333333333333333333333333333333")
        assert first_accounting.total_cost_basis_removed == expected_removed
        assert (remaining_lot.total_qty, remaining_lot.total_cost) == (
            Decimal("1"), expected_residual
        )
        assert _exact_sum((first_accounting.total_cost_basis_removed, remaining_lot.total_cost)) == Decimal("1")

        final_fill = _sell_fill(
            sell, fill_id="sell-direct-proration-2", qty="1", price="60"
        )
        _run(store.save_paper_fill(final_fill))
        _run(store.apply_paper_fill(final_fill.fill_id))
        conn = sqlite3.connect(str(db))
        final_accounting = _PaperSellAccountingRecordV1.model_validate_json(
            conn.execute(
                "SELECT accounting_json FROM paper_sell_accounting WHERE fill_id=?",
                (final_fill.fill_id,),
            ).fetchone()[0]
        )
        assert conn.execute(
            "SELECT COUNT(*) FROM position_lots WHERE order_id=?", (buy.order_id,)
        ).fetchone()[0] == 0
        conn.close()
        assert final_accounting.total_cost_basis_removed == expected_residual
        assert _exact_sum((first_accounting.total_cost_basis_removed,
                           final_accounting.total_cost_basis_removed)) == Decimal("1")

    assert getcontext().prec == original_context.prec
    assert getcontext().rounding == original_context.rounding
    _run(store.close())


def test_sell_fill_rollback_preserves_order_balances_and_reservations(tmp_path: Path) -> None:
    db = tmp_path / "sell-fill-rollback.db"
    store = _open_account(db)
    buy = _seed_buy_lot(store, "buy-rollback")
    allocation = LotAllocation(
        position_id=buy.order_id,
        account_id=buy.paper_account_id,
        symbol=buy.symbol,
        strategy_ref=buy.strategy_ref,
        qty=Decimal("1.0"),
    )
    sell = _sell_order("sell-rollback", (allocation,), strategy_ref=buy.strategy_ref)
    _run(store.register_paper_order(sell))
    _run(store.accept_paper_order(sell.order_id))
    fill = _sell_fill(sell, fill_id="sell-rollback-fill", qty="1.0", price="60")
    _run(store.save_paper_fill(fill))
    order_before = _run(store.load_paper_order(sell.order_id))
    balances_before = _run(store.load_paper_balance_view("acct-sell"))
    reservation_before = _run(store.load_paper_reservation(sell.order_id))
    conn = sqlite3.connect(str(db))
    conn.execute(
        "CREATE TRIGGER reject_sell_accounting BEFORE INSERT ON paper_sell_accounting "
        "BEGIN SELECT RAISE(ABORT, 'forced sell accounting rollback'); END"
    )
    conn.commit()
    conn.close()

    with pytest.raises(sqlite3.IntegrityError, match="forced sell accounting rollback"):
        _run(store.apply_paper_fill(fill.fill_id))

    assert _run(store.load_paper_order(sell.order_id)) == order_before
    assert _run(store.load_paper_balance_view("acct-sell")) == balances_before
    assert _run(store.load_paper_reservation(sell.order_id)) == reservation_before
    conn = sqlite3.connect(str(db))
    assert conn.execute(
        "SELECT COUNT(*) FROM paper_fill_applications WHERE fill_id=?", (fill.fill_id,)
    ).fetchone()[0] == 0
    assert conn.execute(
        "SELECT COUNT(*) FROM paper_sell_accounting WHERE fill_id=?", (fill.fill_id,)
    ).fetchone()[0] == 0
    conn.execute("DROP TRIGGER reject_sell_accounting")
    conn.commit()
    conn.close()
    _run(store.close())


def test_sell_fully_closing_lot_releases_residual_cost_and_reservation(
    tmp_path: Path,
) -> None:
    db = tmp_path / "sell-close-lot.db"
    store = _open_account(db)
    buy = _seed_buy_lot(store, "buy-close-lot")
    allocation = LotAllocation(
        position_id=buy.order_id,
        account_id=buy.paper_account_id,
        symbol=buy.symbol,
        strategy_ref=buy.strategy_ref,
        qty=Decimal("2.0"),
    )
    sell = _sell_order("sell-close", (allocation,), strategy_ref=buy.strategy_ref)
    _run(store.register_paper_order(sell))
    _run(store.accept_paper_order(sell.order_id))
    fill = _sell_fill(sell, fill_id="sell-close-fill", qty="2.0", price="60")
    _run(store.save_paper_fill(fill))

    _run(store.apply_paper_fill(fill.fill_id))
    order = _run(store.load_paper_order(sell.order_id))
    balances = _run(store.load_paper_balance_view("acct-sell"))
    assert order is not None and order.status == PaperOrderStatus.FILLED
    assert balances is not None
    base = next(item for item in balances.balances if item.asset == "BTC")
    assert (base.total, base.available, base.reserved) == (
        Decimal("0"), Decimal("0"), Decimal("0")
    )
    assert _run(store.load_paper_reservation(sell.order_id)) is None
    conn = sqlite3.connect(str(db))
    assert conn.execute(
        "SELECT COUNT(*) FROM position_lots WHERE order_id=?", (buy.order_id,)
    ).fetchone()[0] == 0
    assert conn.execute(
        "SELECT COUNT(*) FROM paper_reservation_lot_allocations WHERE owner_order_id=?",
        (sell.order_id,),
    ).fetchone()[0] == 0
    record = _PaperSellAccountingRecordV1.model_validate_json(
        conn.execute(
            "SELECT accounting_json FROM paper_sell_accounting WHERE fill_id=?",
            (fill.fill_id,),
        ).fetchone()[0]
    )
    assert record.total_cost_basis_removed == Decimal("80")
    assert record.pnl_gross == Decimal("40")
    conn.close()
    _run(store.close())


def test_cancel_accepted_sell_releases_base_and_lot_reservations(
    tmp_path: Path,
) -> None:
    """Cancel an ACCEPTED SELL order: release BASE reservation and per-lot reservations."""
    db = tmp_path / "cancel-accepted-sell.db"
    store = _open_account(db, base="10")
    buy = _seed_buy_lot(store, "buy-cancel", qty="5.0")
    allocation = LotAllocation(
        position_id=buy.order_id,
        account_id=buy.paper_account_id,
        symbol=buy.symbol,
        strategy_ref=buy.strategy_ref,
        qty=Decimal("3.0"),
    )
    sell = _sell_order("sell-cancel-acc", (allocation,), strategy_ref=buy.strategy_ref)
    _run(store.register_paper_order(sell))
    _run(store.accept_paper_order(sell.order_id))

    # Verify reservation exists before cancel
    reservation = _run(store.load_paper_reservation(sell.order_id))
    assert reservation is not None
    assert reservation.amount.amount == Decimal("3.0")
    conn = sqlite3.connect(str(db))
    lot_alloc_count = conn.execute(
        "SELECT COUNT(*) FROM paper_reservation_lot_allocations WHERE owner_order_id=?",
        (sell.order_id,),
    ).fetchone()[0]
    assert lot_alloc_count == 1
    conn.close()

    # Cancel the order
    cancelled = _run(store.cancel_paper_order(sell.order_id))
    assert cancelled.status == PaperOrderStatus.CANCELLED

    # Reservation and lot allocations released
    assert _run(store.load_paper_reservation(sell.order_id)) is None
    conn = sqlite3.connect(str(db))
    assert conn.execute(
        "SELECT COUNT(*) FROM paper_reservation_lot_allocations WHERE owner_order_id=?",
        (sell.order_id,),
    ).fetchone()[0] == 0
    # BASE balance: reserved decreases, available increases
    balances = _run(store.load_paper_balance_view("acct-sell"))
    base = next(b for b in balances.balances if b.asset == "BTC")
    assert (base.reserved, base.available) == (Decimal("0"), Decimal("15"))
    conn.close()


def test_cancel_partially_filled_sell_releases_remaining_reservations(
    tmp_path: Path,
) -> None:
    """Cancel a PARTIALLY_FILLED SELL order: release remaining BASE and per-lot reservations."""
    db = tmp_path / "cancel-partial-sell.db"
    store = _open_account(db, base="10")
    buy = _seed_buy_lot(store, "buy-partial", qty="5.0")
    allocation = LotAllocation(
        position_id=buy.order_id,
        account_id=buy.paper_account_id,
        symbol=buy.symbol,
        strategy_ref=buy.strategy_ref,
        qty=Decimal("5.0"),
    )
    sell = _sell_order("sell-partial", (allocation,), strategy_ref=buy.strategy_ref)
    _run(store.register_paper_order(sell))
    _run(store.accept_paper_order(sell.order_id))

    # Apply a partial fill
    fill = _sell_fill(sell, fill_id="partial-fill", qty="2.0", price="50")
    _run(store.save_paper_fill(fill))
    _run(store.apply_paper_fill(fill.fill_id))

    order = _run(store.load_paper_order(sell.order_id))
    assert order.status == PaperOrderStatus.PARTIALLY_FILLED

    # Cancel the remaining quantity
    cancelled = _run(store.cancel_paper_order(sell.order_id))
    assert cancelled.status == PaperOrderStatus.CANCELLED

    # Reservation released
    assert _run(store.load_paper_reservation(sell.order_id)) is None
    conn = sqlite3.connect(str(db))
    assert conn.execute(
        "SELECT COUNT(*) FROM paper_reservation_lot_allocations WHERE owner_order_id=?",
        (sell.order_id,),
    ).fetchone()[0] == 0
    conn.close()


def test_cancel_sell_idempotent_returns_same_order(
    tmp_path: Path,
) -> None:
    """Canceling an already CANCELLED SELL order is a no-op."""
    db = tmp_path / "cancel-sell-idem.db"
    store = _open_account(db, base="10")
    buy = _seed_buy_lot(store, "buy-idem", qty="3.0")
    allocation = LotAllocation(
        position_id=buy.order_id,
        account_id=buy.paper_account_id,
        symbol=buy.symbol,
        strategy_ref=buy.strategy_ref,
        qty=Decimal("2.0"),
    )
    sell = _sell_order("sell-idem", (allocation,), strategy_ref=buy.strategy_ref)
    _run(store.register_paper_order(sell))
    _run(store.accept_paper_order(sell.order_id))
    first_cancel = _run(store.cancel_paper_order(sell.order_id))
    assert first_cancel.status == PaperOrderStatus.CANCELLED

    # Second cancel should be idempotent
    second_cancel = _run(store.cancel_paper_order(sell.order_id))
    assert second_cancel.status == PaperOrderStatus.CANCELLED
    assert second_cancel.order_id == first_cancel.order_id


def test_cancel_sell_rejects_filled_order(
    tmp_path: Path,
) -> None:
    """Attempting to cancel a FILLED SELL order raises state error."""
    db = tmp_path / "cancel-filled-sell.db"
    store = _open_account(db, base="10")
    buy = _seed_buy_lot(store, "buy-filled", qty="3.0")
    allocation = LotAllocation(
        position_id=buy.order_id,
        account_id=buy.paper_account_id,
        symbol=buy.symbol,
        strategy_ref=buy.strategy_ref,
        qty=Decimal("3.0"),
    )
    sell = _sell_order("sell-filled", (allocation,), strategy_ref=buy.strategy_ref)
    _run(store.register_paper_order(sell))
    _run(store.accept_paper_order(sell.order_id))
    fill = _sell_fill(sell, fill_id="filled-fill", qty="3.0", price="50")
    _run(store.save_paper_fill(fill))
    _run(store.apply_paper_fill(fill.fill_id))

    with pytest.raises(PaperOrderCancellationStateError, match="filled"):
        _run(store.cancel_paper_order(sell.order_id))


def test_cancel_sell_rejects_buy_order(
    tmp_path: Path,
) -> None:
    """BUY orders are cancelled independently and do not trigger SELL cancellation logic."""
    db = tmp_path / "cancel-buy-sell.db"
    store = _open_account(db, base="0", quote="10000")
    buy = _seed_buy_lot(store, "buy-cancel-side", qty="3.0")
    # Create a BUY order in the same account
    buy2 = PaperOrder(
        order_id="buy-side",
        client_order_id="client-buy-side",
        paper_account_id="acct-sell",
        paper_session_id="session-sell",
        environment="paper",
        symbol="BTC/USDT",
        side=PaperOrderSide.BUY,
        order_type=PaperOrderType.LIMIT,
        requested_qty=Decimal("1.0"),
        qty=Decimal("1.0"),
        price=Decimal("40"),
        strategy_ref=buy.strategy_ref,
        lot_allocations=(),
        timestamp_ms=1_700_000_002_000,
        commission=Commission(amount=Decimal("0"), asset="USDT"),
    )
    _run(store.register_paper_order(buy2))
    _accept_buy(store, buy2)

    # BUY orders cancel via their own path, not SELL cancellation logic
    cancelled = _run(store.cancel_paper_order(buy2.order_id))
    assert cancelled.status == PaperOrderStatus.CANCELLED


# ── SELL cancellation rollback ──────────────────────────────────────────────

def test_cancel_sell_rollback_preserves_state(tmp_path: Path) -> None:
    """If an exception fires mid-cancellation, the transaction rolls back."""
    db = tmp_path / "cancel-sell-rollback.db"
    store = _open_account(db, base="10")
    buy = _seed_buy_lot(store, "buy-cr-rollback", qty="5.0")
    allocation = LotAllocation(
        position_id=buy.order_id,
        account_id=buy.paper_account_id,
        symbol=buy.symbol,
        strategy_ref=buy.strategy_ref,
        qty=Decimal("3.0"),
    )
    sell = _sell_order("sell-cr", (allocation,), strategy_ref=buy.strategy_ref)
    _run(store.register_paper_order(sell))
    _run(store.accept_paper_order(sell.order_id))

    # Capture pre-cancel state
    order_before = _run(store.load_paper_order(sell.order_id))
    balances_before = _run(store.load_paper_balance_view("acct-sell"))
    reservation_before = _run(store.load_paper_reservation(sell.order_id))
    lot_count_before = sqlite3.connect(str(db)).execute(
        "SELECT COUNT(*) FROM paper_reservation_lot_allocations WHERE owner_order_id=?",
        (sell.order_id,),
    ).fetchone()[0]

    # Install a trigger that fires on the DELETE of paper_reservations,
    # forcing an error mid-cancellation so the transaction must roll back.
    conn = sqlite3.connect(str(db))
    conn.execute(
        "CREATE TRIGGER reject_cancel_reservation "
        "BEFORE DELETE ON paper_reservations "
        "BEGIN SELECT RAISE(ABORT, 'force cancel rollback'); END"
    )
    conn.commit()
    conn.close()

    with pytest.raises(sqlite3.IntegrityError, match="force cancel rollback"):
        _run(store.cancel_paper_order(sell.order_id))

    # Verify full rollback: nothing changed.
    assert _run(store.load_paper_order(sell.order_id)) == order_before
    assert _run(store.load_paper_balance_view("acct-sell")) == balances_before
    assert _run(store.load_paper_reservation(sell.order_id)) == reservation_before
    conn = sqlite3.connect(str(db))
    lot_count_after = conn.execute(
        "SELECT COUNT(*) FROM paper_reservation_lot_allocations WHERE owner_order_id=?",
        (sell.order_id,),
    ).fetchone()[0]
    conn.close()
    assert lot_count_after == lot_count_before


# ── SELL cancellation concurrency with fills ────────────────────────────────

def test_cancel_then_fill_rejects_fill(tmp_path: Path) -> None:
    """Cancel first, then fill → fill application is rejected because the order is no longer active."""
    db = tmp_path / "cancel-then-fill.db"
    store = _open_account(db, base="10")
    buy = _seed_buy_lot(store, "buy-ctf", qty="5.0")
    allocation = LotAllocation(
        position_id=buy.order_id,
        account_id=buy.paper_account_id,
        symbol=buy.symbol,
        strategy_ref=buy.strategy_ref,
        qty=Decimal("3.0"),
    )
    sell = _sell_order("sell-ctf", (allocation,), strategy_ref=buy.strategy_ref)
    _run(store.register_paper_order(sell))
    _run(store.accept_paper_order(sell.order_id))

    # Cancel first
    cancelled = _run(store.cancel_paper_order(sell.order_id))
    assert cancelled.status == PaperOrderStatus.CANCELLED

    # Trying to apply a fill after cancellation should fail (order is no longer active).
    fill = _sell_fill(sell, fill_id="ctf-fill-after-cancel", qty="1.0", price="50")
    _run(store.save_paper_fill(fill))
    with pytest.raises(Exception):
        _run(store.apply_paper_fill(fill.fill_id))

    # Verify order is still CANCELLED and reservations released.
    final_order = _run(store.load_paper_order(sell.order_id))
    assert final_order.status == PaperOrderStatus.CANCELLED
    assert _run(store.load_paper_reservation(sell.order_id)) is None


def test_fill_then_cancel_preserves_filled_accounting(tmp_path: Path) -> None:
    """Fill first, then cancel → fills/accounting preserved, remaining reservations released."""
    db = tmp_path / "fill-then-cancel.db"
    store = _open_account(db, base="10")
    buy = _seed_buy_lot(store, "buy-ftc", qty="5.0")
    allocation = LotAllocation(
        position_id=buy.order_id,
        account_id=buy.paper_account_id,
        symbol=buy.symbol,
        strategy_ref=buy.strategy_ref,
        qty=Decimal("3.0"),
    )
    sell = _sell_order("sell-ftc", (allocation,), strategy_ref=buy.strategy_ref)
    _run(store.register_paper_order(sell))
    _run(store.accept_paper_order(sell.order_id))

    # Fill first
    fill = _sell_fill(sell, fill_id="ftc-fill-first", qty="1.0", price="55")
    _run(store.save_paper_fill(fill))
    _run(store.apply_paper_fill(fill.fill_id))

    # Then cancel the remaining quantity
    cancelled = _run(store.cancel_paper_order(sell.order_id))
    assert cancelled.status == PaperOrderStatus.CANCELLED

    # Verify fills and accounting are preserved
    final_order = _run(store.load_paper_order(sell.order_id))
    assert final_order.executed_qty == Decimal("1.0")

    conn = sqlite3.connect(str(db))
    fill_count = conn.execute(
        "SELECT COUNT(*) FROM paper_fill_applications WHERE fill_id=?",
        ("ftc-fill-first",),
    ).fetchone()[0]
    accounting_count = conn.execute(
        "SELECT COUNT(*) FROM paper_sell_accounting WHERE fill_id=?",
        ("ftc-fill-first",),
    ).fetchone()[0]
    conn.close()
    assert fill_count == 1
    assert accounting_count == 1

    # Remaining reservation released
    assert _run(store.load_paper_reservation(sell.order_id)) is None


def test_cancel_vs_fill_race_serializes_correctly(tmp_path: Path) -> None:
    """Race between cancel and fill via two store instances + asyncio.gather:
    at most one outcome is applied (serializable)."""
    db = tmp_path / "cancel-vs-fill-race.db"
    store_a = _open_account(db, base="10")
    buy = _seed_buy_lot(store_a, "buy-race", qty="5.0")
    allocation = LotAllocation(
        position_id=buy.order_id,
        account_id=buy.paper_account_id,
        symbol=buy.symbol,
        strategy_ref=buy.strategy_ref,
        qty=Decimal("3.0"),
    )
    sell = _sell_order("sell-race", (allocation,), strategy_ref=buy.strategy_ref)
    _run(store_a.register_paper_order(sell))
    _run(store_a.accept_paper_order(sell.order_id))

    # Save the fill record before the race.
    fill = _sell_fill(sell, fill_id="race-fill", qty="3.0", price="50")
    _run(store_a.save_paper_fill(fill))

    # Create a second store instance pointing at the same database.
    store_b = _open_account(db, base="10")

    # Race: cancel via store_a, apply fill via store_b (concurrent in one event loop).
    async def _race():
        return await asyncio.gather(
            store_a.cancel_paper_order(sell.order_id),
            store_b.apply_paper_fill(fill.fill_id),
            return_exceptions=True,
        )

    results = _run(_race())
    assert len(results) == 2

    cancel_result, fill_result = results

    # Both cancel and fill are valid winners; exactly one must succeed.
    cancel_succeeded = isinstance(cancel_result, PaperOrder) and cancel_result.status == PaperOrderStatus.CANCELLED
    fill_succeeded = isinstance(fill_result, PaperFill) and fill_result.order_id == sell.order_id
    assert cancel_succeeded or fill_succeeded, (
        f"Either cancel or fill must succeed: cancel={type(cancel_result)}, fill={type(fill_result)}"
    )
    # Both cannot succeed simultaneously (race serializes).
    assert not (cancel_succeeded and fill_succeeded), "Race must serialize: cancel and fill cannot both succeed"

    # Verify final state is consistent with the winner.
    final_order = _run(store_a.load_paper_order(sell.order_id))
    if cancel_succeeded:
        assert final_order.status == PaperOrderStatus.CANCELLED
        assert _run(store_a.load_paper_reservation(sell.order_id)) is None
    else:
        assert final_order.status == PaperOrderStatus.FILLED


# ── SELL cancellation balance invariants ────────────────────────────────────

def test_cancel_accepted_sell_balance_invariants(tmp_path: Path) -> None:
    """Verify precise balance invariants after cancelling an ACCEPTED SELL."""
    db = tmp_path / "cancel-acc-bal-inv.db"
    store = _open_account(db, base="10")
    buy = _seed_buy_lot(store, "buy-bi", qty="5.0")
    allocation = LotAllocation(
        position_id=buy.order_id,
        account_id=buy.paper_account_id,
        symbol=buy.symbol,
        strategy_ref=buy.strategy_ref,
        qty=Decimal("3.0"),
    )
    sell = _sell_order("sell-bi", (allocation,), strategy_ref=buy.strategy_ref)
    _run(store.register_paper_order(sell))
    _run(store.accept_paper_order(sell.order_id))

    balances_before = _run(store.load_paper_balance_view("acct-sell"))
    btc_before = next(b for b in balances_before.balances if b.asset == "BTC")

    # Before cancel: BTC reserved should include the SELL allocation amount.
    assert btc_before.reserved >= Decimal("3.0")

    # Cancel
    cancelled = _run(store.cancel_paper_order(sell.order_id))
    assert cancelled.status == PaperOrderStatus.CANCELLED

    balances_after = _run(store.load_paper_balance_view("acct-sell"))
    btc_after = next(b for b in balances_after.balances if b.asset == "BTC")

    # Invariant 1: total unchanged
    assert btc_after.total == btc_before.total

    # Invariant 2: reserved decreases by exactly the reserved amount
    assert btc_after.reserved == btc_before.reserved - Decimal("3.0")

    # Invariant 3: available increases by exactly the reserved amount
    assert btc_after.available == btc_before.available + Decimal("3.0")

    # Invariant 4: total == available + reserved still holds
    assert btc_after.total == btc_after.available + btc_after.reserved


def test_cancel_partially_filled_sell_balance_invariants(tmp_path: Path) -> None:
    """Verify balance invariants after cancelling a PARTIALLY_FILLED SELL."""
    db = tmp_path / "cancel-partial-bal-inv.db"
    store = _open_account(db, base="10")
    buy = _seed_buy_lot(store, "buy-pbi", qty="5.0")
    allocation = LotAllocation(
        position_id=buy.order_id,
        account_id=buy.paper_account_id,
        symbol=buy.symbol,
        strategy_ref=buy.strategy_ref,
        qty=Decimal("3.0"),
    )
    sell = _sell_order("sell-pbi", (allocation,), strategy_ref=buy.strategy_ref)
    _run(store.register_paper_order(sell))
    _run(store.accept_paper_order(sell.order_id))

    # Apply a partial fill of 1.0
    fill = _sell_fill(sell, fill_id="pbi-fill", qty="1.0", price="55")
    _run(store.save_paper_fill(fill))
    _run(store.apply_paper_fill(fill.fill_id))

    balances_before_cancel = _run(store.load_paper_balance_view("acct-sell"))
    btc_before = next(b for b in balances_before_cancel.balances if b.asset == "BTC")

    # Cancel the remaining 2.0
    cancelled = _run(store.cancel_paper_order(sell.order_id))
    assert cancelled.status == PaperOrderStatus.CANCELLED

    balances_after = _run(store.load_paper_balance_view("acct-sell"))
    btc_after = next(b for b in balances_after.balances if b.asset == "BTC")

    # Invariant 1: total unchanged
    assert btc_after.total == btc_before.total

    # Invariant 2: reserved decreases by remaining quantity (3.0 - 1.0 = 2.0)
    assert btc_after.reserved == btc_before.reserved - Decimal("2.0")

    # Invariant 3: available increases by remaining quantity
    assert btc_after.available == btc_before.available + Decimal("2.0")

    # Invariant 4: total == available + reserved still holds
    assert btc_after.total == btc_after.available + btc_after.reserved


def test_cancel_sell_rejects_pending_order(tmp_path: Path) -> None:
    """Attempting to cancel a PREPARED SELL order raises state error."""
    db = tmp_path / "cancel-prepared.db"
    store = _open_account(db, base="10")
    buy = _seed_buy_lot(store, "buy-prep", qty="3.0")
    allocation = LotAllocation(
        position_id=buy.order_id,
        account_id=buy.paper_account_id,
        symbol=buy.symbol,
        strategy_ref=buy.strategy_ref,
        qty=Decimal("2.0"),
    )
    sell = _sell_order("sell-prep", (allocation,), strategy_ref=buy.strategy_ref)
    _run(store.register_paper_order(sell))
    # Do NOT accept the order — leave it in PREPARED state.

    with pytest.raises(PaperOrderCancellationStateError, match="outside.*cancellation scope"):
        _run(store.cancel_paper_order(sell.order_id))


def test_cancel_sell_rejects_rejected_order(tmp_path: Path) -> None:
    """Attempting to cancel a REJECTED SELL order raises state error."""
    db = tmp_path / "cancel-rejected.db"
    store = _open_account(db, base="10")
    buy = _seed_buy_lot(store, "buy-rej", qty="3.0")
    allocation = LotAllocation(
        position_id=buy.order_id,
        account_id=buy.paper_account_id,
        symbol=buy.symbol,
        strategy_ref=buy.strategy_ref,
        qty=Decimal("3.0"),
    )
    sell = _sell_order("sell-rej", (allocation,), strategy_ref=buy.strategy_ref)
    _run(store.register_paper_order(sell))
    _run(store.accept_paper_order(sell.order_id))

    # Manually set status to REJECTED via direct SQL to simulate external rejection.
    conn = sqlite3.connect(str(db))
    # Load current order JSON, replace the status field, and write back.
    row = conn.execute(
        "SELECT order_json FROM paper_orders WHERE order_id=?", (sell.order_id,)
    ).fetchone()
    import json
    order_data = json.loads(row[0])
    order_data["status"] = "rejected"
    conn.execute(
        "UPDATE paper_orders SET order_json = ? WHERE order_id = ?",
        (json.dumps(order_data), sell.order_id),
    )
    conn.commit()
    conn.close()

    with pytest.raises(PaperOrderCancellationStateError, match="terminal state"):
        _run(store.cancel_paper_order(sell.order_id))


# ── Integrity validation: cancel and fill reject inconsistent data ──────────


def test_cancel_lot_reservation_inconsistent_column_raises_integrity_error(
    tmp_path: Path,
) -> None:
    """Cancel must reject when a lot reservation's indexed column disagrees with its payload."""
    db = tmp_path / "cancel-lot-inconsist.db"
    store = _open_account(db, base="10")
    buy = _seed_buy_lot(store, "buy-li", qty="5.0")
    allocation = LotAllocation(
        position_id=buy.order_id,
        account_id=buy.paper_account_id,
        symbol=buy.symbol,
        strategy_ref=buy.strategy_ref,
        qty=Decimal("3.0"),
    )
    sell = _sell_order("sell-li", (allocation,), strategy_ref=buy.strategy_ref)
    _run(store.register_paper_order(sell))
    _run(store.accept_paper_order(sell.order_id))

    # Inject inconsistent lot reservation: write a wrong position_id in the indexed column.
    conn = sqlite3.connect(str(db))
    row = conn.execute(
        "SELECT allocation_json FROM paper_reservation_lot_allocations WHERE owner_order_id=?",
        (sell.order_id,),
    ).fetchone()
    lot_data = json.loads(row[0])
    lot_data["position_id"] = "corrupted-position-id"
    conn.execute(
        "UPDATE paper_reservation_lot_allocations SET allocation_json = ? WHERE owner_order_id=?",
        (json.dumps(lot_data), sell.order_id),
    )
    conn.commit()
    conn.close()

    with pytest.raises(PaperOrderAcceptanceIntegrityError, match="inconsistent"):
        _run(store.cancel_paper_order(sell.order_id))

    # Verify no state was changed by the failed cancellation.
    final_order = _run(store.load_paper_order(sell.order_id))
    assert final_order.status == PaperOrderStatus.ACCEPTED


def test_cancel_sum_remainders_mismatch_base_reservation_raises_integrity_error(
    tmp_path: Path,
) -> None:
    """Cancel must reject when the sum of lot remainders does not equal the BASE reservation."""
    db = tmp_path / "cancel-remainder-mismatch.db"
    store = _open_account(db, base="10")
    buy = _seed_buy_lot(store, "buy-rm", qty="5.0")
    allocation = LotAllocation(
        position_id=buy.order_id,
        account_id=buy.paper_account_id,
        symbol=buy.symbol,
        strategy_ref=buy.strategy_ref,
        qty=Decimal("3.0"),
    )
    sell = _sell_order("sell-rm", (allocation,), strategy_ref=buy.strategy_ref)
    _run(store.register_paper_order(sell))
    _run(store.accept_paper_order(sell.order_id))

    # Corrupt the lot remaining_qty so it no longer matches the BASE reservation amount.
    # Use a value within bounds (<= initial_qty) but mismatched with BASE reservation.
    conn = sqlite3.connect(str(db))
    row = conn.execute(
        "SELECT allocation_json FROM paper_reservation_lot_allocations WHERE owner_order_id=?",
        (sell.order_id,),
    ).fetchone()
    lot_data = json.loads(row[0])
    lot_data["remaining_qty"] = "2.0"  # Valid but mismatched: BASE reservation is 3.0
    conn.execute(
        "UPDATE paper_reservation_lot_allocations SET allocation_json = ? WHERE owner_order_id=?",
        (json.dumps(lot_data), sell.order_id),
    )
    conn.commit()
    conn.close()

    with pytest.raises(PaperOrderAcceptanceIntegrityError):
        _run(store.cancel_paper_order(sell.order_id))

    # Verify no state was changed.
    final_order = _run(store.load_paper_order(sell.order_id))
    assert final_order.status == PaperOrderStatus.ACCEPTED


def test_fill_lot_reservation_inconsistent_column_raises_integrity_error(
    tmp_path: Path,
) -> None:
    """Apply a SELL fill must reject when a lot reservation's indexed column disagrees."""
    db = tmp_path / "fill-lot-inconsist.db"
    store = _open_account(db, base="10")
    buy = _seed_buy_lot(store, "buy-fi", qty="5.0")
    allocation = LotAllocation(
        position_id=buy.order_id,
        account_id=buy.paper_account_id,
        symbol=buy.symbol,
        strategy_ref=buy.strategy_ref,
        qty=Decimal("3.0"),
    )
    sell = _sell_order("sell-fi", (allocation,), strategy_ref=buy.strategy_ref)
    _run(store.register_paper_order(sell))
    _run(store.accept_paper_order(sell.order_id))

    # Inject inconsistent lot reservation: wrong position_id in indexed column.
    conn = sqlite3.connect(str(db))
    row = conn.execute(
        "SELECT allocation_json FROM paper_reservation_lot_allocations WHERE owner_order_id=?",
        (sell.order_id,),
    ).fetchone()
    lot_data = json.loads(row[0])
    lot_data["position_id"] = "corrupted-lot-id"
    conn.execute(
        "UPDATE paper_reservation_lot_allocations SET allocation_json = ? WHERE owner_order_id=?",
        (json.dumps(lot_data), sell.order_id),
    )
    conn.commit()
    conn.close()

    fill = _sell_fill(sell, fill_id="fill-fi", qty="1.0", price="50")
    _run(store.save_paper_fill(fill))
    with pytest.raises(PaperOrderAcceptanceIntegrityError, match="inconsistent"):
        _run(store.apply_paper_fill(fill.fill_id))

    # Verify no state was changed.
    final_order = _run(store.load_paper_order(sell.order_id))
    assert final_order.status == PaperOrderStatus.ACCEPTED
    conn = sqlite3.connect(str(db))
    fill_count = conn.execute(
        "SELECT COUNT(*) FROM paper_fill_applications WHERE fill_id=?", ("fill-fi",)
    ).fetchone()[0]
    conn.close()
    assert fill_count == 0  # No application recorded


def test_fill_qty_exceeds_remaining_base_reservation_raises_funds_error(
    tmp_path: Path,
) -> None:
    """Apply a SELL fill whose qty exceeds the remaining BASE reservation must fail."""
    db = tmp_path / "fill-exceeds-reservation.db"
    store = _open_account(db, base="10")
    buy = _seed_buy_lot(store, "buy-er", qty="5.0")
    allocation = LotAllocation(
        position_id=buy.order_id,
        account_id=buy.paper_account_id,
        symbol=buy.symbol,
        strategy_ref=buy.strategy_ref,
        qty=Decimal("3.0"),
    )
    sell = _sell_order("sell-er", (allocation,), strategy_ref=buy.strategy_ref)
    _run(store.register_paper_order(sell))
    _run(store.accept_paper_order(sell.order_id))

    fill = _sell_fill(sell, fill_id="fill-er", qty="5.0", price="50")  # 5.0 > 3.0 reserved
    _run(store.save_paper_fill(fill))
    with pytest.raises(PaperOrderAcceptanceFundsError, match="exceeds"):
        _run(store.apply_paper_fill(fill.fill_id))

    # Verify no state was changed.
    final_order = _run(store.load_paper_order(sell.order_id))
    assert final_order.status == PaperOrderStatus.ACCEPTED
    conn = sqlite3.connect(str(db))
    fill_count = conn.execute(
        "SELECT COUNT(*) FROM paper_fill_applications WHERE fill_id=?", ("fill-er",)
    ).fetchone()[0]
    conn.close()
    assert fill_count == 0


def test_cancel_sell_with_wrong_base_reservation_asset_raises_integrity_error(
    tmp_path: Path,
) -> None:
    """Cancel a SELL order whose BASE reservation asset does not match the symbol's BASE asset."""
    db = tmp_path / "cancel-wrong-base.db"
    store = _open_account(db, base="10")
    buy = _seed_buy_lot(store, "buy-wb", qty="5.0")
    allocation = LotAllocation(
        position_id=buy.order_id,
        account_id=buy.paper_account_id,
        symbol=buy.symbol,
        strategy_ref=buy.strategy_ref,
        qty=Decimal("3.0"),
    )
    sell = _sell_order("sell-wb", (allocation,), strategy_ref=buy.strategy_ref)
    _run(store.register_paper_order(sell))
    _run(store.accept_paper_order(sell.order_id))

    # Corrupt the BASE reservation asset to mismatch the symbol's BASE asset.
    conn = sqlite3.connect(str(db))
    row = conn.execute(
        "SELECT reservation_id, owner_order_id, account_id, paper_session_id, "
        "environment, asset, amount_json, reservation_json "
        "FROM paper_reservations WHERE owner_order_id=?",
        (sell.order_id,),
    ).fetchone()
    corrupted_amount_json = row[6].replace('"BTC"', '"ETH"')
    corrupted_reservation_json = row[7].replace('"BTC"', '"ETH"')
    conn.execute(
        "UPDATE paper_reservations SET asset=?, amount_json=?, reservation_json=? WHERE owner_order_id=?",
        ("ETH", corrupted_amount_json, corrupted_reservation_json, sell.order_id),
    )
    conn.commit()
    conn.close()

    with pytest.raises(PaperOrderAcceptanceIntegrityError, match="BASE reservation asset.*does not match"):
        _run(store.cancel_paper_order(sell.order_id))

    # Verify rollback: reservation still exists with corrupted asset.
    conn = sqlite3.connect(str(db))
    row = conn.execute(
        "SELECT asset FROM paper_reservations WHERE owner_order_id=?",
        (sell.order_id,),
    ).fetchone()
    assert row[0] == "ETH"
    conn.close()


def test_fill_sell_with_corrupted_lot_base_asset_raises_integrity_error(
    tmp_path: Path,
) -> None:
    """Apply a SELL fill whose lot reservation base_asset is corrupted must fail with integrity error and rollback."""
    db = tmp_path / "fill-corrupted-base.db"
    store = _open_account(db, base="10")
    buy = _seed_buy_lot(store, "buy-fb", qty="5.0")
    allocation = LotAllocation(
        position_id=buy.order_id,
        account_id=buy.paper_account_id,
        symbol=buy.symbol,
        strategy_ref=buy.strategy_ref,
        qty=Decimal("3.0"),
    )
    sell = _sell_order("sell-fb", (allocation,), strategy_ref=buy.strategy_ref)
    _run(store.register_paper_order(sell))
    _run(store.accept_paper_order(sell.order_id))

    # Corrupt the base_asset indexed column from 'BTC' to 'ETH'.
    conn = sqlite3.connect(str(db))
    conn.execute(
        "UPDATE paper_reservation_lot_allocations SET base_asset='ETH' WHERE owner_order_id=?",
        (sell.order_id,),
    )
    conn.commit()
    conn.close()

    fill = _sell_fill(sell, fill_id="fill-fb", qty="2.0", price="50")
    _run(store.save_paper_fill(fill))
    with pytest.raises(PaperOrderAcceptanceIntegrityError, match="inconsistent SELL context"):
        _run(store.apply_paper_fill(fill.fill_id))

    # Verify rollback: fill not applied, order status unchanged.
    final_order = _run(store.load_paper_order(sell.order_id))
    assert final_order.status == PaperOrderStatus.ACCEPTED
    conn = sqlite3.connect(str(db))
    fill_count = conn.execute(
        "SELECT COUNT(*) FROM paper_fill_applications WHERE fill_id=?", ("fill-fb",)
    ).fetchone()[0]
    conn.close()
    assert fill_count == 0


def test_partial_sell_fill_with_precision_collapse_rejects_and_preserves_state(
    tmp_path: Path,
) -> None:
    """Regression: partial SELL fill that leaves remaining_qty > 0 and remaining_cost == 0
    must be rejected. Fill remains journalized in paper_fills but not applied to accounting.
    Order, balances, global reserve, per-lot reserves, and lot remain identical post-rejection.
    """
    db = tmp_path / "precision-collapse.db"
    store = _open_account(db, base="1")
    buy = _seed_buy_lot(store, "buy-prec", qty="1.0")

    # Capture BUY lot before SELL operations: qty=1, cost=40
    pre_buy_lot = sqlite3.connect(str(db)).execute(
        "SELECT lot_json FROM position_lots WHERE order_id=?", (buy.order_id,)
    ).fetchone()
    assert pre_buy_lot is not None
    pre_buy_lot_data = PositionLot.model_validate_json(pre_buy_lot[0])
    assert pre_buy_lot_data.total_qty == Decimal("1")
    assert pre_buy_lot_data.total_cost == Decimal("40")

    # SELL lot: qty=1, cost=40
    allocation = LotAllocation(
        position_id=buy.order_id,
        account_id=buy.paper_account_id,
        symbol=buy.symbol,
        strategy_ref=buy.strategy_ref,
        qty=Decimal("1"),
    )
    sell = _sell_order("sell-prec", (allocation,), price=Decimal("45"), strategy_ref=buy.strategy_ref)
    _run(store.register_paper_order(sell))
    _run(store.accept_paper_order(sell.order_id))

    # Capture pre-apply snapshots
    pre_order = _run(store.load_paper_order(sell.order_id))
    pre_balances = _run(store.load_paper_balance_view("acct-sell"))
    pre_reservation = _run(store.load_paper_reservation(sell.order_id))
    pre_conn = sqlite3.connect(str(db))
    pre_lot_rows = pre_conn.execute(
        "SELECT lot_json FROM position_lots WHERE order_id=?", (sell.order_id,)
    ).fetchall()
    pre_alloc_rows = pre_conn.execute(
        "SELECT allocation_json FROM paper_reservation_lot_allocations WHERE reservation_id=?",
        (sell.order_id,),
    ).fetchall()
    pre_buy_lot_row = pre_conn.execute(
        "SELECT lot_json FROM position_lots WHERE order_id=?", (buy.order_id,)
    ).fetchone()
    pre_conn.close()

    # Partial fill: qty=0.9999999999999999999999999999999999999999
    # This triggers remaining_qty=1E-40 and remaining_cost=0 due to prec=34 collapse
    partial_qty = Decimal("0.9999999999999999999999999999999999999999")
    # Use localcontext with high precision so Pydantic validation doesn't collapse the qty
    with localcontext(Context(prec=999999, rounding=ROUND_HALF_EVEN)):
        fill = PaperFill(
            fill_id="fill-prec",
            order_id=sell.order_id,
            qty=partial_qty,
            price=Decimal("45"),
            timestamp_ms=1_700_000_003_000,
            commission=Commission(amount=Decimal("0.6"), asset="USDT"),
            lot_allocations=(
                LotAllocation(
                    position_id=sell.lot_allocations[0].position_id,
                    account_id=sell.lot_allocations[0].account_id,
                    symbol=sell.lot_allocations[0].symbol,
                    strategy_ref=sell.lot_allocations[0].strategy_ref,
                    qty=partial_qty,
                ),
            ),
        )
        # Persist directly to avoid re-validation under prec=28
        fill_json = fill.model_dump_json()
        conn = sqlite3.connect(str(db))
        conn.execute(
            "INSERT INTO paper_fills (fill_id, record_json) VALUES (?, ?)",
            ("fill-prec", fill_json),
        )
        conn.commit()
        conn.close()

        # Apply must raise ValueError due to residual-cost guard
        with pytest.raises(ValueError, match="partial SELL fill leaves residual cost at or below zero"):
            _run(store.apply_paper_fill(fill.fill_id))

    # Post-rejection: verify BUY lot unchanged (qty=1, cost=40)
    post_buy_lot = sqlite3.connect(str(db)).execute(
        "SELECT lot_json FROM position_lots WHERE order_id=?", (buy.order_id,)
    ).fetchone()
    assert post_buy_lot is not None
    assert post_buy_lot[0] == pre_buy_lot_row[0]
    post_buy_lot_data = PositionLot.model_validate_json(post_buy_lot[0])
    assert post_buy_lot_data.total_qty == Decimal("1")
    assert post_buy_lot_data.total_cost == Decimal("40")

    # Post-rejection: verify all state identical
    post_order = _run(store.load_paper_order(sell.order_id))
    assert post_order.status == pre_order.status
    assert len(post_order.fills) == len(pre_order.fills)

    post_balances = _run(store.load_paper_balance_view("acct-sell"))
    for pb, pbo in zip(post_balances.balances, pre_balances.balances):
        assert pb.asset == pbo.asset
        assert pb.total == pbo.total
        assert pb.available == pbo.available
        assert pb.reserved == pbo.reserved

    post_reservation = _run(store.load_paper_reservation(sell.order_id))
    assert post_reservation is not None
    assert post_reservation.amount == pre_reservation.amount

    post_lot_rows = sqlite3.connect(str(db)).execute(
        "SELECT lot_json FROM position_lots WHERE order_id=?", (sell.order_id,)
    ).fetchall()
    # Guard rejected the fill: no position_lots row should exist for the SELL order
    assert len(post_lot_rows) == 0

    # Verify paper_reservation_lot_allocations unchanged
    post_alloc_rows = sqlite3.connect(str(db)).execute(
        "SELECT allocation_json FROM paper_reservation_lot_allocations WHERE reservation_id=?",
        (sell.order_id,),
    ).fetchall()
    assert len(post_alloc_rows) == len(pre_alloc_rows)
    for pa, paa in zip(post_alloc_rows, pre_alloc_rows):
        assert pa[0] == paa[0]

    # Fill remains in paper_fills but not in paper_fill_applications or accounting
    conn = sqlite3.connect(str(db))
    fill_in_journal = conn.execute(
        "SELECT COUNT(*) FROM paper_fills WHERE fill_id=?", ("fill-prec",)
    ).fetchone()[0]
    fill_in_applications = conn.execute(
        "SELECT COUNT(*) FROM paper_fill_applications WHERE fill_id=?", ("fill-prec",)
    ).fetchone()[0]
    sell_accounting_count = conn.execute(
        "SELECT COUNT(*) FROM paper_sell_accounting WHERE fill_id=?", ("fill-prec",)
    ).fetchone()[0]
    conn.close()

    assert fill_in_journal == 1
    assert fill_in_applications == 0
    assert sell_accounting_count == 0

    _run(store.close())


def test_partial_sell_fill_with_zero_residual_cost_rejects_without_mutation(
    tmp_path: Path,
) -> None:
    """Reject a partial SELL fill that leaves positive quantity with zero residual cost.
    No state is mutated; the BUY lot (qty=1, cost=40) stays intact.
    Fill remains journalized in paper_fills but not in paper_fill_applications or accounting.
    """
    db = tmp_path / "zero-residual-cost.db"
    store = _open_account(db, base="1")
    buy = _seed_buy_lot(store, "buy-exceed", qty="1.0")

    # Capture BUY lot: qty=1, cost=40
    pre_buy_lot = sqlite3.connect(str(db)).execute(
        "SELECT lot_json FROM position_lots WHERE order_id=?", (buy.order_id,)
    ).fetchone()
    assert pre_buy_lot is not None
    pre_buy_lot_data = PositionLot.model_validate_json(pre_buy_lot[0])
    assert pre_buy_lot_data.total_qty == Decimal("1")
    assert pre_buy_lot_data.total_cost == Decimal("40")

    # SELL lot: qty=1, cost=40 (full BUY lot)
    allocation = LotAllocation(
        position_id=buy.order_id,
        account_id=buy.paper_account_id,
        symbol=buy.symbol,
        strategy_ref=buy.strategy_ref,
        qty=Decimal("1"),
    )
    sell = _sell_order("sell-exceed", (allocation,), price=Decimal("100"), strategy_ref=buy.strategy_ref)
    _run(store.register_paper_order(sell))
    _run(store.accept_paper_order(sell.order_id))

    # Pre-apply snapshots
    pre_balances = _run(store.load_paper_balance_view("acct-sell"))
    pre_reservation = _run(store.load_paper_reservation(sell.order_id))

    # Partial fill: qty=0.9999999999999999999999999999999999999999 (40 nines)
    partial_qty = Decimal("0.9999999999999999999999999999999999999999")
    # Triggers remaining_qty=1E-40, remaining_cost=0 -> guard rejects

    with localcontext(Context(prec=999999, rounding=ROUND_HALF_EVEN)):
        fill_exc = PaperFill(
            fill_id="fill-exceed",
            order_id=sell.order_id,
            qty=partial_qty,
            price=Decimal("100"),
            timestamp_ms=1_700_000_004_000,
            commission=Commission(amount=Decimal("1.0"), asset="USDT"),
            lot_allocations=(
                LotAllocation(
                    position_id=sell.lot_allocations[0].position_id,
                    account_id=sell.lot_allocations[0].account_id,
                    symbol=sell.lot_allocations[0].symbol,
                    strategy_ref=sell.lot_allocations[0].strategy_ref,
                    qty=partial_qty,
                ),
            ),
        )
        fill_exc_json = fill_exc.model_dump_json()
        conn = sqlite3.connect(str(db))
        conn.execute(
            "INSERT INTO paper_fills (fill_id, record_json) VALUES (?, ?)",
            ("fill-exceed", fill_exc_json),
        )
        conn.commit()
        conn.close()

        with pytest.raises(ValueError, match="partial SELL fill leaves residual cost at or below zero"):
            _run(store.apply_paper_fill(fill_exc.fill_id))

    # Post-rejection: verify BUY lot unchanged
    post_buy_lot = sqlite3.connect(str(db)).execute(
        "SELECT lot_json FROM position_lots WHERE order_id=?", (buy.order_id,)
    ).fetchone()
    assert post_buy_lot is not None
    assert post_buy_lot[0] == pre_buy_lot[0]
    post_buy_lot_data = PositionLot.model_validate_json(post_buy_lot[0])
    assert post_buy_lot_data.total_qty == Decimal("1")
    assert post_buy_lot_data.total_cost == Decimal("40")

    # Post-rejection: verify all state identical
    post_balances = _run(store.load_paper_balance_view("acct-sell"))
    for pb, pbo in zip(post_balances.balances, pre_balances.balances):
        assert pb.asset == pbo.asset
        assert pb.total == pbo.total
        assert pb.available == pbo.available
        assert pb.reserved == pbo.reserved

    post_reservation = _run(store.load_paper_reservation(sell.order_id))
    assert post_reservation is not None
    assert post_reservation.amount == pre_reservation.amount

    # Fill remains in paper_fills but not in paper_fill_applications or accounting
    conn = sqlite3.connect(str(db))
    fill_in_journal = conn.execute(
        "SELECT COUNT(*) FROM paper_fills WHERE fill_id=?", ("fill-exceed",)
    ).fetchone()[0]
    fill_in_applications = conn.execute(
        "SELECT COUNT(*) FROM paper_fill_applications WHERE fill_id=?", ("fill-exceed",)
    ).fetchone()[0]
    sell_accounting_count = conn.execute(
        "SELECT COUNT(*) FROM paper_sell_accounting WHERE fill_id=?", ("fill-exceed",)
    ).fetchone()[0]
    conn.close()

    assert fill_in_journal == 1
    assert fill_in_applications == 0
    assert sell_accounting_count == 0

    _run(store.close())
