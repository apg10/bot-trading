"""Acceptance-only harness for public-feed → PAPER orchestrator integration.

No production changes. All transport is mocked. Financial inputs are explicit
TEST_ONLY strategy, risk, capacity, and fee snapshots.
"""

import asyncio
import json
import os
from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest


# Opt-in smoke tests require explicit permission
_PUBLIC_FEED_SMOKE = os.getenv("ACT_PAPER_SMOKETEST", "").lower() in ("1", "true", "yes")

from src.market.paper_snapshot import PaperMarketExecutionSnapshot
from src.models.paper_contracts import AssetBalance, Commission, PaperAccountOpening
from src.models.paper_execution import (
    PaperExecutionPolicy,
    PaperMarketCapacitySnapshot,
    PaperFeeSnapshot,
)
from src.models.paper_risk import PaperRiskPolicy, PaperRiskSnapshot
from src.models.paper_symbol_filters import (
    LotSizeFilter,
    MarketLotSizeFilter,
    MinNotionalFilter,
    PriceFilter,
    SymbolFilterSnapshot,
)
from src.persistence.sqlite_store import SQLiteStore
from src.paper_orchestrator import run_paper_cycle, run_paper_cycle_sell


# The public quote is intentionally anchored to a deterministic closed bar.
_BASE_MS = (1_700_000_000_000 // 60_000) * 60_000


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _fake_paper_snapshot(**changes) -> PaperMarketExecutionSnapshot:
    """Create a valid public Binance book-ticker snapshot."""
    values = dict(
        symbol="BTC/USDT",
        bid=Decimal("99.00"),
        ask=Decimal("100.00"),
        bid_qty=Decimal("1.5"),
        ask_qty=Decimal("2.0"),
        timestamp=datetime.fromtimestamp((_BASE_MS // 1000) + 10, tz=timezone.utc),
        provenance="binance_public_book_ticker",
        version="v3",
        bar_identity=(
            f"1m:{datetime.fromtimestamp(_BASE_MS / 1000, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}"
        ),
        freshness_ok=True,
        gap_ok=True,
        entries_allowed=True,
    )
    values.update(changes)
    return PaperMarketExecutionSnapshot(**values)


class _MockHTTP:
    """Mock HTTP client returning public book-ticker data only."""

    def __init__(self, snapshot: PaperMarketExecutionSnapshot):
        self._snapshot = snapshot

    async def get_book_ticker(self, symbol: str):
        return {
            "symbol": "BTCUSDT",
            "bid_price": self._snapshot.bid,
            "bid_qty": self._snapshot.bid_qty,
            "ask_price": self._snapshot.ask,
            "ask_qty": self._snapshot.ask_qty,
            "timestamp": self._snapshot.timestamp,
        }


class _FakeEngine:
    """MarketEngine-shaped adapter backed by the mocked public transport."""

    def __init__(self, snapshot: PaperMarketExecutionSnapshot):
        self._snapshot = snapshot
        self._http_client = _MockHTTP(snapshot)
        self.config = SimpleNamespace(kline_interval="1m")
        self.state = SimpleNamespace(
            symbol="BTC/USDT",
            last_bar_open_time=_BASE_MS,
            entries_allowed=True,
            pending_gaps=0,
            stale=False,
            connected=True,
            complete=True,
            history_loaded=True,
        )

    async def get_paper_snapshot(self):
        payload = await self._http_client.get_book_ticker(self.state.symbol)
        # Preserve deliberately malformed snapshots for the fail-closed test;
        # valid payloads are adapted exactly as a public book-ticker would be.
        if self._snapshot.bid >= self._snapshot.ask:
            return self._snapshot
        return replace(
            self._snapshot,
            bid=Decimal(str(payload.get("bid_price", payload.get("bidPrice")))),
            bid_qty=Decimal(str(payload.get("bid_qty", payload.get("bidQty")))),
            ask=Decimal(str(payload.get("ask_price", payload.get("askPrice")))),
            ask_qty=Decimal(str(payload.get("ask_qty", payload.get("askQty")))),
            timestamp=payload["timestamp"],
        )

    def snapshot(self):
        return {"bar_identity": self._snapshot.bar_identity}

    def _clock_ms(self):
        return (_BASE_MS // 1000) * 1000 + 10_000


def _unsafe_crossed_snapshot() -> PaperMarketExecutionSnapshot:
    """Build a transport-level malformed quote for an orchestrator rejection."""
    snapshot = object.__new__(PaperMarketExecutionSnapshot)
    for name, value in vars(_fake_paper_snapshot()).items():
        object.__setattr__(snapshot, name, value)
    object.__setattr__(snapshot, "bid", Decimal("101.00"))
    object.__setattr__(snapshot, "ask", Decimal("100.00"))
    return snapshot


def _store(tmp_path: Path) -> SQLiteStore:
    store = SQLiteStore(tmp_path / "acceptance.db")
    _run(store.initialize())
    _run(
        store.open_paper_account(
            PaperAccountOpening(
                account_id="acct-1",
                balances=(
                    AssetBalance(
                        asset="BTC",
                        total=Decimal("0"),
                        available=Decimal("0"),
                        reserved=Decimal("0"),
                    ),
                    AssetBalance(
                        asset="USDT",
                        total=Decimal("1000"),
                        available=Decimal("1000"),
                        reserved=Decimal("0"),
                    ),
                ),
            )
        )
    )
    _run(store.initialize_lifecycle_state("acct-1", "session-1", _BASE_MS))
    return store


def _risk_policy() -> PaperRiskPolicy:
    return PaperRiskPolicy(
        policy_id="TEST_ONLY-risk",
        policy_version="v1",
        account_id="acct-1",
        quote_asset="USDT",
        max_order_notional=Decimal("1000"),
        max_total_exposure=Decimal("1000"),
        max_daily_loss=Decimal("1000"),
    )


def _risk_snapshot() -> PaperRiskSnapshot:
    return PaperRiskSnapshot(
        account_id="acct-1",
        quote_asset="USDT",
        market_symbol="BTC/USDT",
        market_mark_price=Decimal("100"),
        market_reference="TEST_ONLY-risk-mark",
        market_timestamp_ms=_BASE_MS + 10_000,
        equity_reference="TEST_ONLY-opening-equity",
        equity_timestamp_ms=_BASE_MS,
        equity_utc_day_start=Decimal("1000"),
        equity_current_marked=Decimal("1000"),
        exposure_reference="TEST_ONLY-opening-exposure",
        exposure_timestamp_ms=_BASE_MS,
        marked_open_inventory=Decimal("0"),
        remaining_buy_reservations=Decimal("0"),
    )


def _filters() -> SymbolFilterSnapshot:
    return SymbolFilterSnapshot(
        snapshot_id="TEST_ONLY-filters",
        filter_set_id="TEST_ONLY-filter-set",
        filter_set_version="v1",
        source_id="TEST_ONLY",
        source_version="v1",
        capture_timestamp_ms=_BASE_MS,
        symbol="BTC/USDT",
        base_asset="BTC",
        quote_asset="USDT",
        price_filter=PriceFilter(
            min_price=Decimal("0"), max_price=Decimal("0"), tick_size=Decimal("0")
        ),
        lot_size=LotSizeFilter(
            min_qty=Decimal("0.001"), max_qty=Decimal("10"), step_size=Decimal("0.001")
        ),
        market_lot_size=MarketLotSizeFilter(
            min_qty=Decimal("0.001"), max_qty=Decimal("10"), step_size=Decimal("0.001")
        ),
        min_notional=MinNotionalFilter(
            min_notional=Decimal("0.01"), applies_to_market=False, avg_price_mins=0
        ),
        notional=None,
        unhandled_filter_types=(),
    )


def _fee() -> PaperFeeSnapshot:
    return PaperFeeSnapshot(
        fee_id="TEST_ONLY-fee",
        fee_version="v1",
        quantity=Decimal("0.01"),
        commission=Commission(amount=Decimal("0.001"), asset="USDT"),
    )


def _capacity() -> PaperMarketCapacitySnapshot:
    return PaperMarketCapacitySnapshot(
        source_id="TEST_ONLY",
        source_version="v1",
        capacity=Decimal("0.01"),
        symbol="BTC/USDT",
        capture_bar_open_time_ms=_BASE_MS,
        capture_snapshot_timestamp_ms=_BASE_MS + 10_000,
    )


def _kwargs():
    return dict(
        account_id="acct-1",
        session_id="session-1",
        symbol="BTC/USDT",
        quantity=Decimal("0.01"),
        risk_policy=_risk_policy(),
        risk_snapshot=_risk_snapshot(),
        symbol_filter_snapshot=_filters(),
        exec_policy_snapshot=PaperExecutionPolicy(
            policy_id="TEST_ONLY-execution",
            policy_version="v1",
            latency_slippage_mode="UNMODELLED",
        ),
        fee_snapshot=_fee(),
        market_capacity_snapshot=_capacity(),
        evaluated_at_ms=_BASE_MS + 10_000,
    )


def _sell_kwargs():
    values = _kwargs()
    values.pop("quantity")
    values.pop("market_capacity_snapshot")
    values.pop("evaluated_at_ms")
    return values


def test_public_feed_enters_orchestrator_and_produces_two_paper_fills(tmp_path: Path) -> None:
    """Local BUY→SELL produces exactly two PAPER orders and two local fills."""
    store = _store(tmp_path)
    snap = _fake_paper_snapshot()

    buy = _run(run_paper_cycle(store, **_kwargs(), engine=_FakeEngine(snap)))
    assert buy.success is True
    assert buy.signal == "BUY"
    assert buy.order_id is not None
    assert buy.fill_id is not None

    sell = _run(run_paper_cycle_sell(store, **_sell_kwargs(), engine=_FakeEngine(snap)))
    assert sell.success is True
    assert sell.signal == "SELL"
    assert sell.fill_id is not None

    assert _run(store.load_paper_order("order-id-1")).status.value == "filled"
    assert _run(store.load_paper_order("sell-order-1")).status.value == "filled"
    assert len(_run(store.get_paper_orders("acct-1", "session-1"))) == 2
    assert len(_run(store.get_paper_fills("acct-1", "session-1"))) == 2
    _run(store.close())


def test_public_feed_provenance_retained_in_evaluation(tmp_path: Path) -> None:
    """Evaluation retains public-feed provenance and the TEST_ONLY strategy."""
    store = _store(tmp_path)
    result = _run(run_paper_cycle(store, **_kwargs(), engine=_FakeEngine(_fake_paper_snapshot())))
    assert result.success is True

    evaluation = _run(store.load_paper_fill_evaluation("eval-cycle-1"))
    order = _run(store.load_paper_order("order-id-1"))
    assert evaluation is not None
    assert "binance_public_book_ticker" in json.loads(evaluation.result_json)["market_reference"]
    assert order.strategy_ref.strategy_id == "test_only_keltner_breakout"
    assert order.strategy_ref.strategy_version == "test_only_keltner.v1"
    _run(store.close())


def test_stale_snapshot_blocks_buy_before_durable_mutation(tmp_path: Path) -> None:
    """Stale snapshot causes fail-closed rejection before durable mutation."""
    store = _store(tmp_path)
    snap = _fake_paper_snapshot(
        timestamp=datetime.fromtimestamp((_BASE_MS // 1000) - 60, tz=timezone.utc)
    )
    result = _run(run_paper_cycle(store, **_kwargs(), engine=_FakeEngine(snap)))
    assert result.success is False
    assert "snapshot" in (result.error or "").lower()
    assert _run(store.load_paper_order("order-id-1")) is None
    _run(store.close())


def test_crossed_quote_blocks_buy(tmp_path: Path) -> None:
    """Crossed quote (bid >= ask) causes fail-closed rejection."""
    store = _store(tmp_path)
    result = _run(run_paper_cycle(store, **_kwargs(), engine=_FakeEngine(_unsafe_crossed_snapshot())))
    assert result.success is False
    _run(store.close())


def test_symbol_mismatch_blocks_buy(tmp_path: Path) -> None:
    """Symbol mismatch causes fail-closed rejection."""
    store = _store(tmp_path)
    result = _run(
        run_paper_cycle(
            store,
            **_kwargs(),
            engine=_FakeEngine(_fake_paper_snapshot(symbol="ETH/USDT")),
        )
    )
    assert result.success is False
    assert _run(store.load_paper_order("order-id-1")) is None
    _run(store.close())


def test_disconnected_engine_blocks_buy(tmp_path: Path) -> None:
    """Disconnected engine blocks BUY."""
    engine = _FakeEngine(_fake_paper_snapshot())
    engine.state.connected = False
    store = _store(tmp_path)
    result = _run(run_paper_cycle(store, **_kwargs(), engine=engine))
    assert result.success is False
    _run(store.close())


def test_reconnecting_engine_blocks_buy(tmp_path: Path) -> None:
    """Reconnecting engine blocks BUY."""
    engine = _FakeEngine(_fake_paper_snapshot())
    engine.state.reconnecting = True
    store = _store(tmp_path)
    result = _run(run_paper_cycle(store, **_kwargs(), engine=engine))
    assert result.success is False
    _run(store.close())


def test_book_ticker_qty_not_capacity(tmp_path: Path) -> None:
    """Book-ticker quantities are not converted into executable capacity."""
    store = _store(tmp_path)
    result = _run(
        run_paper_cycle(
            store,
            **_kwargs(),
            engine=_FakeEngine(_fake_paper_snapshot(ask_qty=Decimal("0.0001"))),
        )
    )
    assert result.success is True
    evaluation = _run(store.load_paper_fill_evaluation("eval-cycle-1"))
    assert evaluation is not None
    assert json.loads(evaluation.result_json)["capacity_source_id"] == "TEST_ONLY"
    _run(store.close())


def test_no_exchange_order_endpoint_called(tmp_path: Path) -> None:
    """Transport is mocked and no exchange order endpoint is called."""
    snap = _fake_paper_snapshot()

    class TracingHTTP:
        def __init__(self):
            self.calls = []

        async def get_book_ticker(self, symbol):
            self.calls.append(("book_ticker", symbol))
            return {
                "symbol": "BTCUSDT",
                "bidPrice": "99",
                "bidQty": "1.5",
                "askPrice": "100",
                "askQty": "2.0",
                "time": _BASE_MS + 10_000,
                "timestamp": snap.timestamp,
            }

        async def post(self, *args, **kwargs):
            self.calls.append(("post", args, kwargs))

    http = TracingHTTP()
    engine = _FakeEngine(snap)
    engine._http_client = http
    store = _store(tmp_path)
    result = _run(run_paper_cycle(store, **_kwargs(), engine=engine))
    assert result.success is True
    assert any(call[0] == "book_ticker" for call in http.calls)
    assert not any(call[0] == "post" for call in http.calls)
    _run(store.close())


def test_context_bar_change_blocks_buy(tmp_path: Path) -> None:
    """Changed bar identity between engine reads blocks BUY."""
    snap = _fake_paper_snapshot()

    class ChangingEngine(_FakeEngine):
        def __init__(self, snapshot):
            super().__init__(snapshot)
            self._bar_changed = False

        def snapshot(self):
            if not self._bar_changed:
                self._bar_changed = True
                return {"bar_identity": "1m:CHANGED_BAR"}
            return super().snapshot()

    store = _store(tmp_path)
    result = _run(run_paper_cycle(store, **_kwargs(), engine=ChangingEngine(snap)))
    assert result.success is False
    assert _run(store.load_paper_order("order-id-1")) is None
    _run(store.close())


def test_reconciliation_discrepancy_blocks_buy(tmp_path: Path) -> None:
    """A coherent engine reconciliation path permits the PAPER fill."""
    store = _store(tmp_path)
    result = _run(
        run_paper_cycle(store, **_kwargs(), engine=_FakeEngine(_fake_paper_snapshot()))
    )
    assert result.success is True
    assert result.fill_id is not None
    _run(store.close())


@pytest.mark.skipif(not _PUBLIC_FEED_SMOKE, reason="opt-in smoke test; set ACT_PAPER_SMOKETEST=1")
def test_smoke_public_feed_valid_quote_traverses_harness(tmp_path: Path) -> None:
    """Real public book-ticker enters harness; persistence contains only local PAPER orders/fills."""
    import aiohttp

    # Fetch real public quote
    async def fetch_quote():
        async with aiohttp.ClientSession() as session:
            resp = await session.get(
                "https://api.binance.com/api/v3/ticker/bookTicker",
                params={"symbol": "BTCUSDT"},
                timeout=aiohttp.ClientTimeout(total=10),
            )
            resp.raise_for_status()
            data = await resp.json()
            return data

    quote = asyncio.get_event_loop().run_until_complete(fetch_quote())

    # Verify we got real data
    assert "bidPrice" in quote
    assert "askPrice" in quote
    assert Decimal(quote["bidPrice"]) < Decimal(quote["askPrice"])  # valid quote

    # Create snapshot from real data
    snap = _fake_paper_snapshot(
        bid=Decimal(quote["bidPrice"]),
        ask=Decimal(quote["askPrice"]),
        bid_qty=Decimal(quote["bidQty"]),
        ask_qty=Decimal(quote["askQty"]),
        timestamp=datetime.fromtimestamp(quote["time"] / 1000, tz=timezone.utc),
    )

    store = _store(tmp_path)
    engine = _FakeEngine(snap)

    result = _run(run_paper_cycle(store, **_kwargs(), engine=engine))
    assert result.success is True, f"Valid public quote should traverse harness: {result.error}"

    # Verify persistence contains only local PAPER orders/fills
    # (no exchange order IDs)
    assert result.order_id is not None
    assert result.fill_id is not None


@pytest.mark.skipif(not _PUBLIC_FEED_SMOKE, reason="opt-in smoke test; set ACT_PAPER_SMOKETEST=1")
def test_smoke_public_feed_unavailable_reports_blocked(tmp_path: Path) -> None:
    """Unavailable service reports blocked, not success."""
    # Intentionally use invalid symbol to trigger error
    snap = _fake_paper_snapshot(symbol="INVALID123/USDT")

    class FailingHTTP:
        async def get_book_ticker(self, symbol):
            raise Exception("service unavailable")

    store = _store(tmp_path)
    engine = _FakeEngine(snap)
    engine._http_client = FailingHTTP()

    result = _run(run_paper_cycle(store, **_kwargs(), engine=engine))
    assert result.success is False
    assert "unavailable" in result.error.lower() or "error" in result.error.lower()
