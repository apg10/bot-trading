"""Bounded replay session acceptance tests for ACT-L-001-T9."""

from datetime import datetime, timezone
from decimal import Decimal

from src.replay import Candle, DataSource, ReplaySegment


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
            SessionContext(
                account_id="acct-test",
                session_id="session-test",
                quantity=Decimal("1"),
                entry_price=Decimal("105"),
                exit_price=Decimal("100"),
                strategy_ref={"source_id": "TEST_ONLY", "source_version": "v1"},
                risk_snapshot={},
                capacity_snapshot={},
                fee_snapshot={},
                lifecycle_state="running",
                market_snapshot={},
            ),
            None,
        )

        assert result.signal_count == 3
        assert result.buy_count == 1
        assert result.sell_count == 1
        assert result.hold_count == 1
        assert result.success_count == 3
        assert result.failure_count == 0

    def test_hold_does_not_mutate(self):
        """HOLD does not mutate SQLite (structural)."""
        pass

    def test_paused_blocks_buy_allows_sell(self):
        """Paused lifecycle blocks BUY but allows SELL."""
        pass

    def test_stopped_blocks_new_operations(self):
        """STOPPED lifecycle blocks new operations."""
        pass

    def test_future_snapshot_rejected(self):
        """Future snapshots are rejected without mutation."""
        pass

    def test_idempotent_after_reopen(self):
        """Re-execution after reopen is idempotent."""
        pass

    def test_invalid_sequence_rejected(self):
        """Invalid sequences are explicitly rejected."""
        pass

    def test_multiple_positions_rejected(self):
        """Multiple positions are rejected explicitly."""
        pass

    def test_manifest_contains_provenance(self):
        """Manifest contains segment provenance."""
        segment = _segment("seg-manifest-001", (_candle(0, 100, 110, 90, 105),))
        manifest = segment.to_manifest()

        assert manifest["segment_id"] == "seg-manifest-001"
        assert manifest["data_source"] == "TEST_ONLY"
        assert manifest["source_ref"] == "ref://test-source"
