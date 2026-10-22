"""One deterministic cross-slice PAPER ledger reconciliation scenario."""

from __future__ import annotations

import asyncio
import sqlite3
from decimal import Decimal, localcontext
from pathlib import Path
from typing import Any

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
    _POSITION_AVERAGE_CONTEXT,
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
    PositionLot,
    SQLiteStore,
    _PaperSellAccountingRecordV1,
)


def _run(coro: Any) -> Any:
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _open_store(db: Path) -> SQLiteStore:
    store = SQLiteStore(db)
    _run(store.initialize())
    opening = PaperAccountOpening(
        environment="paper",
        account_id="acct-ledger",
        balances=(
            AssetBalance(
                asset="BTC",
                total=Decimal("0"),
                available=Decimal("0"),
                reserved=Decimal("0"),
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
    _run(store.initialize_lifecycle_state("acct-ledger", "session-ledger", 10000))
    return store


def _buy_order() -> PaperOrder:
    return PaperOrder(
        order_id="buy-ledger",
        client_order_id="client-buy-ledger",
        paper_account_id="acct-ledger",
        paper_session_id="session-ledger",
        environment="paper",
        symbol="BTC/USDT",
        side=PaperOrderSide.BUY,
        order_type=PaperOrderType.LIMIT,
        requested_qty=Decimal("4"),
        price=Decimal("50"),
        strategy_ref=StrategyRef(
            strategy_id="strategy-ledger", strategy_version="v1", decision_id="decision-ledger"
        ),
        status=PaperOrderStatus.PREPARED,
        timestamp_ms=1_700_000_000_000,
    )


def _sell_order(buy: PaperOrder) -> PaperOrder:
    allocation = LotAllocation(
        position_id=buy.order_id,
        account_id=buy.paper_account_id,
        symbol=buy.symbol,
        strategy_ref=buy.strategy_ref,
        qty=Decimal("4"),
    )
    return PaperOrder(
        order_id="sell-ledger",
        client_order_id="client-sell-ledger",
        paper_account_id=buy.paper_account_id,
        paper_session_id=buy.paper_session_id,
        environment="paper",
        symbol=buy.symbol,
        side=PaperOrderSide.SELL,
        order_type=PaperOrderType.LIMIT,
        requested_qty=Decimal("4"),
        price=Decimal("50"),
        strategy_ref=buy.strategy_ref,
        status=PaperOrderStatus.PREPARED,
        timestamp_ms=1_700_000_002_000,
        lot_allocations=(allocation,),
    )


def _accept_buy(store: SQLiteStore, order: PaperOrder) -> PaperOrder:
    base, quote = order.symbol.split("/")
    policy = PaperRiskPolicy(
        policy_id="ledger-policy",
        policy_version="1",
        account_id=order.paper_account_id,
        quote_asset=quote,
        max_order_notional=Decimal("1000000"),
        max_total_exposure=Decimal("1000000"),
        max_daily_loss=Decimal("1000000"),
    )
    snapshot = PaperRiskSnapshot(
        account_id=order.paper_account_id,
        quote_asset=quote,
        market_symbol=order.symbol,
        market_mark_price=Decimal("50"),
        market_reference="ledger-market",
        market_timestamp_ms=1,
        equity_reference="ledger-equity",
        equity_timestamp_ms=1,
        equity_utc_day_start=Decimal("1000000"),
        equity_current_marked=Decimal("1000000"),
        exposure_reference="ledger-exposure",
        exposure_timestamp_ms=1,
        marked_open_inventory=Decimal("0"),
        remaining_buy_reservations=Decimal("0"),
    )
    filters = SymbolFilterSnapshot(
        snapshot_id="ledger-filters",
        filter_set_id="ledger-filter-set",
        filter_set_version="1",
        source_id="ledger-source",
        source_version="1",
        capture_timestamp_ms=1,
        symbol=order.symbol,
        base_asset=base,
        quote_asset=quote,
        price_filter=PriceFilter(
            min_price=Decimal("0"), max_price=Decimal("0"), tick_size=Decimal("0")
        ),
        lot_size=LotSizeFilter(
            min_qty=Decimal("0.0000000001"),
            max_qty=Decimal("100000000"),
            step_size=order.requested_qty,
        ),
        market_lot_size=MarketLotSizeFilter(
            min_qty=Decimal("0.0000000001"),
            max_qty=Decimal("100000000"),
            step_size=order.requested_qty,
        ),
        min_notional=MinNotionalFilter(
            min_notional=Decimal("0.00000001"), applies_to_market=False, avg_price_mins=0
        ),
        notional=None,
        unhandled_filter_types=(),
    )
    return _run(
        store.accept_paper_order(order.order_id, policy, snapshot, filters)
    )


def _fill(
    fill_id: str,
    order_id: str,
    qty: str,
    price: str,
    timestamp_ms: int,
    commission_amount: str,
    *,
    lot_allocation: LotAllocation | None = None,
) -> PaperFill:
    return PaperFill(
        fill_id=fill_id,
        order_id=order_id,
        qty=Decimal(qty),
        price=Decimal(price),
        timestamp_ms=timestamp_ms,
        commission=Commission(amount=Decimal(commission_amount), asset="USDT"),
        lot_allocations=() if lot_allocation is None else (lot_allocation,),
    )


def test_ledger_reconciliation_across_all_slices(tmp_path: Path) -> None:
    db = tmp_path / "paper-ledger-reconciliation.db"
    store = _open_store(db)
    buy = _buy_order()
    fills: list[PaperFill] = []

    conn = sqlite3.connect(str(db))
    opening_row = conn.execute(
        "SELECT opening_json FROM paper_account_openings WHERE account_id=?",
        ("acct-ledger",),
    ).fetchone()
    assert opening_row is not None
    opening_json = opening_row[0]
    conn.close()

    def assert_opening_unchanged() -> None:
        conn = sqlite3.connect(str(db))
        try:
            row = conn.execute(
                "SELECT opening_json FROM paper_account_openings WHERE account_id=?",
                ("acct-ledger",),
            ).fetchone()
            assert row is not None
            assert row[0] == opening_json
            assert PaperAccountOpening.model_validate_json(row[0]).model_dump_json() == opening_json
        finally:
            conn.close()

    def assert_balances(
        expected: dict[str, tuple[Decimal, Decimal, Decimal]],
    ) -> None:
        conn = sqlite3.connect(str(db))
        try:
            rows = conn.execute(
                "SELECT asset, balance_json FROM paper_account_balances "
                "WHERE account_id=? ORDER BY asset",
                ("acct-ledger",),
            ).fetchall()
            actual = {asset: AssetBalance.model_validate_json(payload) for asset, payload in rows}
            assert set(actual) == set(expected)
            for asset, balance in actual.items():
                assert balance.total >= Decimal("0"), asset
                assert balance.available >= Decimal("0"), asset
                assert balance.reserved >= Decimal("0"), asset
                assert balance.total == balance.available + balance.reserved, asset
                assert (
                    balance.total,
                    balance.available,
                    balance.reserved,
                ) == expected[asset]
        finally:
            conn.close()

    def assert_reservations(expected: dict[str, tuple[str, Decimal]]) -> None:
        conn = sqlite3.connect(str(db))
        try:
            reservation_rows = conn.execute(
                "SELECT owner_order_id, asset, amount_json, reservation_json "
                "FROM paper_reservations ORDER BY owner_order_id"
            ).fetchall()
            owners = {row[0] for row in reservation_rows}
            mutable_orders = {
                PaperOrder.model_validate_json(row[0]).order_id
                for row in conn.execute("SELECT order_json FROM paper_orders").fetchall()
                if PaperOrder.model_validate_json(row[0]).status
                in (PaperOrderStatus.ACCEPTED, PaperOrderStatus.PARTIALLY_FILLED)
            }
            assert owners == set(expected)
            assert owners == mutable_orders
            assert len(reservation_rows) == len(expected)
            for owner_order_id, asset, amount_json, reservation_json in reservation_rows:
                expected_asset, expected_amount = expected[owner_order_id]
                reservation = PaperReservationRef.model_validate_json(reservation_json)
                assert asset == expected_asset
                assert reservation.amount.asset == expected_asset
                assert reservation.amount.amount == expected_amount
                assert reservation.amount.model_dump_json() == amount_json
        finally:
            conn.close()

    def assert_fill_journal(fill: PaperFill) -> None:
        conn = sqlite3.connect(str(db))
        try:
            journal_rows = conn.execute(
                "SELECT fill_id, record_json FROM paper_fills WHERE fill_id=?",
                (fill.fill_id,),
            ).fetchall()
            assert len(journal_rows) == 1
            journal_fill = PaperFill.model_validate_json(journal_rows[0][1])
            assert journal_fill == fill
            assert journal_fill.qty == fill.qty
            assert journal_fill.price == fill.price
            assert journal_fill.timestamp_ms == fill.timestamp_ms
            assert journal_fill.commission == fill.commission
            assert journal_fill.commission is not None
            assert journal_fill.commission.asset == "USDT"

            application_rows = conn.execute(
                "SELECT fill_id, order_id, application_json "
                "FROM paper_fill_applications WHERE fill_id=?",
                (fill.fill_id,),
            ).fetchall()
            assert len(application_rows) == 1
            assert application_rows[0][1] == fill.order_id
            assert PaperFill.model_validate_json(application_rows[0][2]) == fill

            order_row = conn.execute(
                "SELECT order_json FROM paper_orders WHERE order_id=?",
                (fill.order_id,),
            ).fetchone()
            assert order_row is not None
            stored_order = PaperOrder.model_validate_json(order_row[0])
            assert fill in stored_order.fills
        finally:
            conn.close()

    def assert_buy_lot(expected_qty: Decimal, expected_cost: Decimal) -> None:
        conn = sqlite3.connect(str(db))
        try:
            row = conn.execute(
                "SELECT lot_json FROM position_lots WHERE order_id=?",
                (buy.order_id,),
            ).fetchone()
            assert row is not None
            lot = PositionLot.model_validate_json(row[0])
            assert lot.total_qty == expected_qty
            assert lot.total_cost == expected_cost
            with localcontext(_POSITION_AVERAGE_CONTEXT):
                avg_entry_price = lot.total_cost / lot.total_qty
            assert avg_entry_price == expected_cost / expected_qty
        finally:
            conn.close()

    assert_opening_unchanged()
    assert_balances(
        {
            "BTC": (Decimal("0"), Decimal("0"), Decimal("0")),
            "USDT": (Decimal("10000"), Decimal("10000"), Decimal("0")),
        }
    )

    _run(store.register_paper_order(buy))
    assert_opening_unchanged()
    assert_balances(
        {
            "BTC": (Decimal("0"), Decimal("0"), Decimal("0")),
            "USDT": (Decimal("10000"), Decimal("10000"), Decimal("0")),
        }
    )
    assert_reservations({})

    accepted_buy = _accept_buy(store, buy)
    assert accepted_buy.status == PaperOrderStatus.ACCEPTED
    assert_opening_unchanged()
    assert_balances(
        {
            "BTC": (Decimal("0"), Decimal("0"), Decimal("0")),
            "USDT": (Decimal("10000"), Decimal("9800"), Decimal("200")),
        }
    )
    assert_reservations({buy.order_id: ("USDT", Decimal("200"))})

    buy_fill_one = _fill(
        "buy-ledger-fill-1",
        buy.order_id,
        "1.5",
        "40",
        1_700_000_001_000,
        "0.5",
    )
    fills.append(buy_fill_one)
    _run(store.save_paper_fill(buy_fill_one))
    assert_opening_unchanged()
    _run(store.apply_paper_fill(buy_fill_one.fill_id))
    assert_opening_unchanged()
    assert_fill_journal(buy_fill_one)
    assert_balances(
        {
            "BTC": (Decimal("1.5"), Decimal("1.5"), Decimal("0")),
            "USDT": (Decimal("9939.5"), Decimal("9814.5"), Decimal("125")),
        }
    )
    assert_reservations({buy.order_id: ("USDT", Decimal("125"))})
    assert_buy_lot(Decimal("1.5"), Decimal("60"))

    buy_fill_two = _fill(
        "buy-ledger-fill-2",
        buy.order_id,
        "2.5",
        "48",
        1_700_000_002_000,
        "0.25",
    )
    fills.append(buy_fill_two)
    _run(store.save_paper_fill(buy_fill_two))
    assert_opening_unchanged()
    _run(store.apply_paper_fill(buy_fill_two.fill_id))
    assert_opening_unchanged()
    assert_fill_journal(buy_fill_one)
    assert_fill_journal(buy_fill_two)
    assert_balances(
        {
            "BTC": (Decimal("4"), Decimal("4"), Decimal("0")),
            "USDT": (Decimal("9819.25"), Decimal("9819.25"), Decimal("0")),
        }
    )
    assert_reservations({})
    assert_buy_lot(Decimal("4"), Decimal("180"))

    sell = _sell_order(buy)
    _run(store.register_paper_order(sell))
    assert_opening_unchanged()
    assert_balances(
        {
            "BTC": (Decimal("4"), Decimal("4"), Decimal("0")),
            "USDT": (Decimal("9819.25"), Decimal("9819.25"), Decimal("0")),
        }
    )
    assert_reservations({})
    accepted_sell = _run(store.accept_paper_order(sell.order_id))
    assert accepted_sell.status == PaperOrderStatus.ACCEPTED
    assert_opening_unchanged()
    assert_balances(
        {
            "BTC": (Decimal("4"), Decimal("0"), Decimal("4")),
            "USDT": (Decimal("9819.25"), Decimal("9819.25"), Decimal("0")),
        }
    )
    assert_reservations({sell.order_id: ("BTC", Decimal("4"))})
    conn = sqlite3.connect(str(db))
    try:
        assert conn.execute(
            "SELECT COUNT(*) FROM paper_reservation_lot_allocations "
            "WHERE owner_order_id=?",
            (sell.order_id,),
        ).fetchone()[0] == 1
    finally:
        conn.close()

    sell_allocation_one = LotAllocation(
        position_id=buy.order_id,
        account_id=buy.paper_account_id,
        symbol=buy.symbol,
        strategy_ref=buy.strategy_ref,
        qty=Decimal("1.5"),
    )
    sell_fill_one = _fill(
        "sell-ledger-fill-1",
        sell.order_id,
        "1.5",
        "55",
        1_700_000_003_000,
        "0.3",
        lot_allocation=sell_allocation_one,
    )
    fills.append(sell_fill_one)
    _run(store.save_paper_fill(sell_fill_one))
    assert_opening_unchanged()
    _run(store.apply_paper_fill(sell_fill_one.fill_id))
    assert_opening_unchanged()
    assert_fill_journal(sell_fill_one)
    assert_balances(
        {
            "BTC": (Decimal("2.5"), Decimal("0"), Decimal("2.5")),
            "USDT": (Decimal("9901.45"), Decimal("9901.45"), Decimal("0")),
        }
    )
    assert_reservations({sell.order_id: ("BTC", Decimal("2.5"))})
    assert_buy_lot(Decimal("2.5"), Decimal("112.5"))
    conn = sqlite3.connect(str(db))
    try:
        lot_reservation = conn.execute(
            "SELECT initial_qty, remaining_qty "
            "FROM paper_reservation_lot_allocations WHERE owner_order_id=?",
            (sell.order_id,),
        ).fetchone()
        assert lot_reservation is not None
        assert Decimal(lot_reservation[0]) == Decimal("4")
        assert Decimal(lot_reservation[1]) == Decimal("2.5")
        accounting = conn.execute(
            "SELECT accounting_json FROM paper_sell_accounting WHERE fill_id=?",
            (sell_fill_one.fill_id,),
        ).fetchone()
        assert accounting is not None
        first_record = _PaperSellAccountingRecordV1.model_validate_json(accounting[0])
        assert first_record.proceeds_gross == Decimal("82.5")
        assert first_record.fee_asset == "USDT"
        assert first_record.fee_amount == Decimal("0.3")
        assert first_record.proceeds_net == Decimal("82.2")
        assert first_record.total_cost_basis_removed == Decimal("67.5")
    finally:
        conn.close()

    sell_allocation_two = sell_allocation_one.model_copy(update={"qty": Decimal("2.5")})
    sell_fill_two = _fill(
        "sell-ledger-fill-2",
        sell.order_id,
        "2.5",
        "52",
        1_700_000_004_000,
        "0.2",
        lot_allocation=sell_allocation_two,
    )
    fills.append(sell_fill_two)
    _run(store.save_paper_fill(sell_fill_two))
    assert_opening_unchanged()
    _run(store.apply_paper_fill(sell_fill_two.fill_id))
    assert_opening_unchanged()
    for applied_fill in fills:
        assert_fill_journal(applied_fill)
    assert_balances(
        {
            "BTC": (Decimal("0"), Decimal("0"), Decimal("0")),
            "USDT": (Decimal("10031.25"), Decimal("10031.25"), Decimal("0")),
        }
    )
    assert_reservations({})
    conn = sqlite3.connect(str(db))
    try:
        second_accounting = conn.execute(
            "SELECT accounting_json FROM paper_sell_accounting WHERE fill_id=?",
            (sell_fill_two.fill_id,),
        ).fetchone()
        assert second_accounting is not None
        second_record = _PaperSellAccountingRecordV1.model_validate_json(second_accounting[0])
        assert second_record.proceeds_gross == Decimal("130")
        assert second_record.fee_asset == "USDT"
        assert second_record.fee_amount == Decimal("0.2")
        assert second_record.proceeds_net == Decimal("129.8")
        assert second_record.total_cost_basis_removed == Decimal("112.5")
        assert second_record.pnl_gross == Decimal("17.5")
        assert second_record.pnl_net == Decimal("17.3")
        assert conn.execute("SELECT COUNT(*) FROM position_lots").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM paper_reservations").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM paper_reservation_lot_allocations").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM paper_order_acceptances").fetchone()[0] == 2
    finally:
        conn.close()

    assert _run(store.load_paper_order(buy.order_id)).status == PaperOrderStatus.FILLED
    assert _run(store.load_paper_order(sell.order_id)).status == PaperOrderStatus.FILLED
    _run(store.close())

    reopened = SQLiteStore(db)
    _run(reopened.initialize())
    assert_opening_unchanged()
    assert_balances(
        {
            "BTC": (Decimal("0"), Decimal("0"), Decimal("0")),
            "USDT": (Decimal("10031.25"), Decimal("10031.25"), Decimal("0")),
        }
    )
    assert_reservations({})
    for applied_fill in fills:
        assert_fill_journal(applied_fill)
    assert _run(reopened.load_paper_order(buy.order_id)).status == PaperOrderStatus.FILLED
    assert _run(reopened.load_paper_order(sell.order_id)).status == PaperOrderStatus.FILLED
    conn = sqlite3.connect(str(db))
    try:
        assert conn.execute("SELECT COUNT(*) FROM position_lots").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM paper_reservations").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM paper_reservation_lot_allocations").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM paper_fill_applications").fetchone()[0] == 4
        assert conn.execute("SELECT COUNT(*) FROM paper_fills").fetchone()[0] == 4
    finally:
        conn.close()
    _run(reopened.close())
