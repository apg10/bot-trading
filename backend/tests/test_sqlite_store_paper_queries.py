"""Focused coverage for the ACT-P-008 read-only SQLite projections."""

from __future__ import annotations

import asyncio
from decimal import Decimal
from pathlib import Path
from typing import Any

from src.models.paper_contracts import (
    AssetBalance,
    PaperAccountOpening,
    PaperFill,
    PaperOrder,
    PaperOrderSide,
    PaperOrderType,
    StrategyRef,
)
from src.persistence.sqlite_store import SQLiteStore


def _run(coro: Any) -> Any:
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _order(order_id: str, timestamp_ms: int) -> PaperOrder:
    return PaperOrder(
        order_id=order_id,
        client_order_id=f"client-{order_id}",
        paper_account_id="acct-read",
        paper_session_id="session-read",
        symbol="BTC/USD",
        side=PaperOrderSide.BUY,
        order_type=PaperOrderType.LIMIT,
        requested_qty=Decimal("1.25"),
        price=Decimal("10"),
        strategy_ref=StrategyRef(strategy_id="read", strategy_version="v1"),
        timestamp_ms=timestamp_ms,
    )


def test_paper_queries_are_empty_and_unknown_accounts_are_not_inferred(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "queries.db")
    _run(store.initialize())
    assert _run(store.get_paper_account_balances("missing")) is None
    assert _run(store.get_paper_position_lots("missing")) == []
    assert _run(store.get_paper_orders("missing")) == []
    assert _run(store.get_paper_fills("missing")) == []
    assert _run(store.get_paper_activity("missing")) == []
    assert _run(store.get_paper_lifecycle_snapshot("missing", "session")) is None
    _run(store.close())


def test_paper_queries_preserve_scope_strings_and_kind_separation(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "queries-populated.db")
    _run(store.initialize())
    _run(store.open_paper_account(PaperAccountOpening(
        account_id="acct-read",
        balances=(AssetBalance(
            asset="USD",
            total=Decimal("100.00"),
            available=Decimal("100.00"),
            reserved=Decimal("0"),
        ),),
    )))
    order = _order("order-read", 20)
    _run(store.register_paper_order(order))
    fill = PaperFill(
        fill_id="fill-read",
        order_id=order.order_id,
        qty=Decimal("0.5"),
        price=Decimal("10"),
        timestamp_ms=30,
        commission=None,
    )
    _run(store.save_paper_fill(fill))
    _run(store.initialize_lifecycle_state("acct-read", "session-read", 10))

    balances = _run(store.get_paper_account_balances("acct-read"))
    assert balances == [{
        "asset": "USD", "total": "100.00", "available": "100.00", "reserved": "0",
    }]
    orders = _run(store.get_paper_orders("acct-read", "session-read"))
    fills = _run(store.get_paper_fills("acct-read", "session-read"))
    assert orders[0]["kind"] == "order"
    assert orders[0]["order_id"] == "order-read"
    assert orders[0]["timestamp_ms"] == "20"
    assert fills[0]["kind"] == "fill"
    assert fills[0]["fill_id"] == "fill-read"
    assert fills[0]["applied"] is False
    activity = _run(store.get_paper_activity("acct-read", "session-read"))
    assert [(item["kind"], item.get("order_id"), item.get("fill_id")) for item in activity] == [
        ("order", "order-read", None), ("fill", "order-read", "fill-read"),
    ]
    assert _run(store.get_paper_lifecycle_snapshot("acct-read", "session-read"))["state"]["state"] == "running"
    _run(store.close())
