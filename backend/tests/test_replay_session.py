"""Bounded replay session acceptance tests for ACT-L-001-T10."""

from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
import asyncio

from src.models.paper_contracts import AssetBalance, Commission, PaperAccountOpening, StrategyRef
from src.models.paper_execution import (
    PaperExecutionPolicy,
    PaperFeeSnapshot,
    PaperMarketCapacitySnapshot,
    PaperMarketExecutionSnapshot,
)
from src.models.paper_lifecycle import PaperLifecycleCommand
from src.models.paper_risk import PaperRiskPolicy, PaperRiskSnapshot
from src.models.paper_symbol_filters import (
    LotSizeFilter,
    MarketLotSizeFilter,
    MinNotionalFilter,
    PriceFilter,
    SymbolFilterSnapshot,
)
from src.persistence.sqlite_store import SQLiteStore
from src.paper_orchestrator import apply_signal_to_paper
from src.replay import Candle, DataSource, DecisionSide, ReplaySegment


_BASE_MS = int(datetime(2025, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
_STEP_S = 60


def _candle(
    ts_offset_s: int,
    o: float,
    h: float,
    l: float,
    c: float,
    v: float = Decimal("100"),
) -> Candle:
    return Candle(
        timestamp_ms=_BASE_MS + ts_offset_s * _STEP_S * 1000,
        open=Decimal(str(o)),
        high=Decimal(str(h)),
        low=Decimal(str(l)),
        close=Decimal(str(c)),
        volume=Decimal(str(v)),
    )


def _segment(segment_id: str, candles: tuple[Candle, ...]) -> ReplaySegment:
    return ReplaySegment(
        segment_id=segment_id,
        symbol="BTC/USDT",
        strategy="TEST_ONLY",
        data_source=DataSource.TEST_ONLY,
        source_ref="ref://test-source",
        clock_ref="ref://test-clock",
        cost_policy_ref="ref://test-cost",
        fill_policy_ref="ref://test-fill",
        start_ms=_BASE_MS,
        end_ms=_BASE_MS + 3 * _STEP_S * 1000,
        candles=candles,
    )


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _store(db: Path) -> SQLiteStore:
    store = SQLiteStore(db)
    _run(store.initialize())
    _run(store.open_paper_account(PaperAccountOpening(
        environment="paper", account_id="acct-test",
        balances=(
            AssetBalance(asset="BTC", total=Decimal("0"), available=Decimal("0"), reserved=Decimal("0")),
            AssetBalance(asset="USDT", total=Decimal("500"), available=Decimal("500"), reserved=Decimal("0")),
        ),
    )))
    _run(store.initialize_lifecycle_state("acct-test", "session-test", 1))
    return store


def _reopen(db: Path) -> SQLiteStore:
    store = SQLiteStore(db)
    _run(store.initialize())
    return store


def _context(*, lifecycle: str = "running", snapshot_ms: int = _BASE_MS):
    from src.models.paper_lifecycle import PaperLifecycleState
    from src.replay_session import SessionContext

    lifecycle_obj = PaperLifecycleState(
        account_id="acct-test", paper_session_id="session-test",
        state=lifecycle, last_command_id=None, last_updated_ms=snapshot_ms,
    )

    market = PaperMarketExecutionSnapshot(
        symbol="BTC/USDT", bid=Decimal("100"), ask=Decimal("105"),
        bar_open_time_ms=snapshot_ms, prior_bar_open_time_ms=snapshot_ms - 60_000,
        interval_ms=60_000, provenance="TEST_ONLY", version="v1",
        snapshot_timestamp_ms=snapshot_ms,
    )
    return SessionContext(
        account_id="acct-test", session_id="session-test", quantity=Decimal("1"),
        entry_price=Decimal("105"), exit_price=Decimal("100"),
        strategy_ref={"source_id": "TEST_ONLY", "source_version": "v1"},
        risk_policy=PaperRiskPolicy(
            policy_id="test-risk", policy_version="v1", account_id="acct-test",
            quote_asset="USDT", max_order_notional=Decimal("1000"),
            max_total_exposure=Decimal("1000"), max_daily_loss=Decimal("500"),
        ),
        risk_snapshot=PaperRiskSnapshot(
            account_id="acct-test", quote_asset="USDT", market_symbol="BTC/USDT",
            market_mark_price=Decimal("105"), market_reference="TEST_ONLY",
            market_timestamp_ms=snapshot_ms, equity_reference="TEST_ONLY",
            equity_timestamp_ms=snapshot_ms, equity_utc_day_start=Decimal("500"),
            equity_current_marked=Decimal("500"), exposure_reference="TEST_ONLY",
            exposure_timestamp_ms=snapshot_ms, marked_open_inventory=Decimal("0"),
            remaining_buy_reservations=Decimal("0"),
        ),
        filter_snapshot=SymbolFilterSnapshot(
            snapshot_id="test-filters", filter_set_id="test-set", filter_set_version="v1",
            source_id="TEST_ONLY", source_version="v1", capture_timestamp_ms=snapshot_ms,
            symbol="BTC/USDT", base_asset="BTC", quote_asset="USDT",
            price_filter=PriceFilter(min_price=Decimal("0"), max_price=Decimal("0"), tick_size=Decimal("0")),
            lot_size=LotSizeFilter(min_qty=Decimal("1"), max_qty=Decimal("10"), step_size=Decimal("1")),
            market_lot_size=MarketLotSizeFilter(min_qty=Decimal("1"), max_qty=Decimal("10"), step_size=Decimal("1")),
            min_notional=MinNotionalFilter(min_notional=Decimal("10"), applies_to_market=False, avg_price_mins=0),
            notional=None, unhandled_filter_types=(),
        ),
        execution_snapshot=PaperExecutionPolicy(
            policy_id="TEST_ONLY-exec", policy_version="v1", latency_slippage_mode="UNMODELLED",
        ),
        market_snapshot=market,
        capacity_snapshot=PaperMarketCapacitySnapshot(
            source_id="TEST_ONLY", source_version="v1", capacity=Decimal("1"),
            symbol="BTC/USDT", capture_bar_open_time_ms=snapshot_ms,
            capture_snapshot_timestamp_ms=snapshot_ms,
        ),
        fee_snapshot=PaperFeeSnapshot(
            fee_id="TEST_ONLY-fee", fee_version="v1", quantity=Decimal("1"),
            commission=Commission(amount=Decimal("1"), asset="USDT"),
        ),
        lifecycle_state=lifecycle_obj,
    )


class TestReplaySession:
    """Tests for the bounded replay session."""

    def test_session_produces_buy_and_sell(self):
        """Fixture produces exactly one BUY and one SELL."""
        from src.replay_session import SessionContext, run_replay_session

        segment = _segment(
            "seg-session-001",
            (
                _candle(0, 100, 110, 90, 105),
                _candle(1, 105, 112, 95, 108),
                _candle(2, 108, 130, 100, 125),
            ),
        )

        result = run_replay_session(
            segment,
            _context(),
            None,
        )

        assert result.signal_count == 3
        assert result.buy_count == 1
        assert result.sell_count == 1
        assert result.hold_count == 1
        assert result.success_count == 3
        assert result.failure_count == 0

    def test_hold_does_not_mutate(self, tmp_path: Path):
        """HOLD does not mutate SQLite (structural)."""
        from src.replay_session import run_replay_session

        store = _store(tmp_path / "replay-hold.db")
        try:
            result = run_replay_session(
                _segment("hold-session", (_candle(0, 100, 110, 90, 105),)),
                _context(), store,
            )
            assert result.success_count == 1
            assert _run(store.get_paper_orders("acct-test")) == []
            assert _run(store.get_paper_fills("acct-test")) == []
        finally:
            _run(store.close())

    def test_paused_blocks_buy_allows_sell(self, tmp_path: Path):
        """Paused lifecycle blocks BUY but allows SELL."""
        from src.replay import replay_to_signals
        from src.replay_session import run_replay_session

        db = tmp_path / "replay-paused.db"
        store = _store(db)
        segment = _segment("paused-session", (_candle(0, 100, 110, 90, 105), _candle(1, 105, 112, 95, 108), _candle(2, 108, 130, 100, 125)))
        ctx = _context()
        signals = replay_to_signals(segment, lambda _prefix, index: (DecisionSide.BUY, "seed") if index == 0 else (DecisionSide.HOLD, "hold"))
        seed = apply_signal_to_paper(
            signal=signals[0], segment=segment, quantity=Decimal("1"), entry_price=Decimal("105"),
            strategy_ref=StrategyRef(strategy_id="TEST_ONLY", strategy_version="v1"),
            risk_snapshot=ctx.risk_snapshot, lifecycle=ctx.lifecycle_state, persistence=store,
            filter_snapshot=ctx.filter_snapshot, execution_snapshot=ctx.execution_snapshot,
            market_snapshot=ctx.market_snapshot, fee_snapshot=ctx.fee_snapshot,
            risk_policy=ctx.risk_policy, market_capacity_snapshot=ctx.capacity_snapshot,
        )
        assert seed.success is True
        _run(store.apply_lifecycle_command(PaperLifecycleCommand(
            command_id="pause-replay", account_id="acct-test", paper_session_id="session-test",
            action="pause", issued_at_ms=2,
        )))
        try:
            result = run_replay_session(segment, _context(lifecycle="paused"), store)
            assert result.buy_count == 1 and result.sell_count == 1
            assert result.failure_count == 1
            assert any("lifecycle" in error for error in result.errors)
            assert _run(store.get_paper_orders("acct-test")) and len(_run(store.get_paper_fills("acct-test"))) == 2
        finally:
            _run(store.close())

    def test_stopped_blocks_new_operations(self, tmp_path: Path):
        """STOPPED lifecycle blocks new operations."""
        from src.replay_session import run_replay_session

        store = _store(tmp_path / "replay-stopped.db")
        try:
            _run(store.apply_lifecycle_command(PaperLifecycleCommand(
                command_id="stop-replay", account_id="acct-test",
                paper_session_id="session-test", action="stop", issued_at_ms=2,
            )))
            result = run_replay_session(
                _segment("stopped-session", (_candle(0, 100, 110, 90, 105), _candle(1, 105, 112, 95, 108), _candle(2, 108, 130, 100, 125))),
                _context(lifecycle="stopped"), store,
            )
            assert result.failure_count == 1
            assert result.errors == ["lifecycle is STOPPED"]
            assert _run(store.get_paper_orders("acct-test")) == []
        finally:
            _run(store.close())

    def test_future_snapshot_rejected(self, tmp_path: Path):
        """Future snapshots are rejected without mutation."""
        from src.replay_session import run_replay_session

        store = _store(tmp_path / "replay-future.db")
        try:
            result = run_replay_session(
                _segment("future-session", (_candle(0, 100, 110, 90, 105), _candle(1, 105, 112, 95, 108), _candle(2, 108, 130, 100, 125))),
                _context(snapshot_ms=_BASE_MS + 60_000), store,
            )
            assert result.failure_count == 1
            assert any("after replay operation" in error for error in result.errors)
            assert _run(store.get_paper_orders("acct-test")) == []
        finally:
            _run(store.close())

    def test_idempotent_after_reopen(self, tmp_path: Path):
        """Re-execution after reopen is idempotent."""
        from src.replay_session import run_replay_session

        db = tmp_path / "replay-reopen.db"
        store = _store(db)
        segment = _segment("reopen-session", (_candle(0, 100, 110, 90, 105), _candle(1, 105, 112, 95, 108), _candle(2, 108, 130, 100, 125)))
        first = run_replay_session(segment, _context(), store)
        assert first.success_count == 3 and first.failure_count == 0
        _run(store.close())
        reopened = _reopen(db)
        try:
            second = run_replay_session(segment, _context(), reopened)
            assert second.success_count == 3 and second.failure_count == 0
            assert len(_run(reopened.get_paper_orders("acct-test"))) == 2
            assert len(_run(reopened.get_paper_fills("acct-test"))) == 2
            assert _run(reopened.get_paper_position_lots("acct-test")) == []
        finally:
            _run(reopened.close())

    def test_invalid_sequence_rejected(self):
        """Invalid sequences are explicitly rejected."""
        from src import replay_session
        from src.replay import PaperSignal

        segment = _segment("invalid-sequence", (_candle(0, 100, 110, 90, 105), _candle(1, 105, 112, 95, 108)))
        invalid = [
            PaperSignal("invalid-0", segment.segment_id, segment.symbol, segment.strategy, DataSource.TEST_ONLY, segment.candles[0].timestamp_ms, 0, DecisionSide.SELL, "invalid"),
            PaperSignal("invalid-1", segment.segment_id, segment.symbol, segment.strategy, DataSource.TEST_ONLY, segment.candles[1].timestamp_ms, 1, DecisionSide.HOLD, "hold"),
        ]
        original = replay_session.replay_to_signals
        replay_session.replay_to_signals = lambda _segment, _strategy: invalid
        try:
            result = replay_session.run_replay_session(segment, _context(), None)
            assert result.failure_count == 1
            assert "SELL without" in result.errors[0]
        finally:
            replay_session.replay_to_signals = original

    def test_multiple_positions_rejected(self, tmp_path: Path):
        """Multiple positions are rejected explicitly."""
        from src.replay import replay_to_signals
        from src.replay_session import run_replay_session
        from src.models.paper_contracts import StrategyRef

        store = _store(tmp_path / "replay-multiple.db")
        segment = _segment("multiple-session", (_candle(0, 100, 110, 90, 105), _candle(1, 105, 112, 95, 108), _candle(2, 108, 130, 100, 125)))
        context = _context()
        for index in (0, 1):
            seed_segment = _segment(f"seed-{index}", segment.candles)
            seed_signal = replay_to_signals(seed_segment, lambda _prefix, i: (DecisionSide.BUY, "seed") if i == 0 else (DecisionSide.HOLD, "hold"))[0]
            seeded = apply_signal_to_paper(
                signal=seed_signal, segment=seed_segment, quantity=Decimal("1"), entry_price=Decimal("105"),
                strategy_ref=StrategyRef(strategy_id="TEST_ONLY", strategy_version="v1"),
                risk_snapshot=context.risk_snapshot, lifecycle=context.lifecycle_state, persistence=store,
                filter_snapshot=context.filter_snapshot, execution_snapshot=context.execution_snapshot,
                market_snapshot=context.market_snapshot, fee_snapshot=context.fee_snapshot,
                risk_policy=context.risk_policy, market_capacity_snapshot=context.capacity_snapshot,
            )
            assert seeded.success is True
        try:
            result = run_replay_session(segment, context, store)
            assert result.failure_count == 1
            assert "multiple open lots" in result.errors[0]
            assert len(_run(store.get_paper_orders("acct-test"))) == 2
        finally:
            _run(store.close())

    def test_manifest_contains_provenance(self):
        """Manifest contains segment provenance."""
        segment = _segment("seg-manifest-001", (_candle(0, 100, 110, 90, 105),))
        manifest = segment.to_manifest()

        assert manifest["segment_id"] == "seg-manifest-001"
        assert manifest["data_source"] == "TEST_ONLY"
        assert manifest["source_ref"] == "ref://test-source"
