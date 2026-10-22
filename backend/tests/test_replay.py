"""Causal replay acceptance tests for ACT-L-001-T1."""

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from src.replay import (
    Candle,
    DataSource,
    Decision,
    DecisionSide,
    ReplayError,
    ReplaySegment,
    replay_decisions,
    replay_deterministic,
    replay_segment,
    validate_candles,
)


_BASE_MS = int(datetime(2025, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
_STEP_S = 60  # 1-minute candles


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


class TestValidation:
    def test_empty_rejected(self):
        with pytest.raises(ReplayError, match="empty"):
            validate_candles([])

    def test_duplicate_timestamp_rejected(self):
        c1 = _candle(0, 100, 110, 90, 105)
        c2 = _candle(0, 100, 110, 90, 105)  # same timestamp
        with pytest.raises(ReplayError, match="duplicate"):
            validate_candles([c1, c2])

    def test_non_increasing_timestamp_rejected(self):
        c1 = _candle(1, 100, 110, 90, 105)
        c2 = _candle(0, 100, 110, 90, 105)  # earlier than c1
        with pytest.raises(ReplayError, match="non-increasing"):
            validate_candles([c1, c2])

    def test_low_gt_high_rejected(self):
        c = _candle(0, 100, 90, 110, 105)  # high=90 < low=110
        with pytest.raises(ReplayError, match="low > high"):
            validate_candles([c])

    def test_negative_value_rejected(self):
        c = _candle(0, -1, 110, 90, 105)
        with pytest.raises(ReplayError, match="negative"):
            validate_candles([c])


class TestCausalReplay:
    def test_initial_candle_is_hold(self):
        candles = [_candle(0, 100, 110, 90, 105), _candle(1, 100, 110, 90, 105)]
        decisions = replay_decisions(candles, "BTC/USDT", "TEST_ONLY")
        assert decisions[0].side == DecisionSide.HOLD
        assert decisions[0].candle_index == 0

    def test_breakout_produces_buy(self):
        """8 candles: first 7 range-bound, 8th breaks above previous high."""
        candles = [
            _candle(0, 100, 110, 90, 105),
            _candle(1, 105, 112, 95, 108),
            _candle(2, 108, 115, 100, 110),
            _candle(3, 110, 118, 102, 106),
            _candle(4, 106, 112, 98, 104),
            _candle(5, 104, 110, 96, 102),
            _candle(6, 102, 108, 94, 100),
            _candle(7, 100, 130, 95, 125),  # breakout: close=125 > max(prev highs)
        ]
        decisions = replay_decisions(candles, "BTC/USDT", "TEST_ONLY")

        # Only the last candle should be BUY (breakout)
        buy_decisions = [d for d in decisions if d.side == DecisionSide.BUY]
        assert len(buy_decisions) == 1
        assert buy_decisions[0].candle_index == 7
        assert "breakout" in buy_decisions[0].reason

    def test_all_decisions_have_provenance(self):
        candles = [
            _candle(0, 100, 110, 90, 105),
            _candle(1, 105, 112, 95, 108),
        ]
        decisions = replay_decisions(candles, "BTC/USDT", "TEST_ONLY")

        for d in decisions:
            assert d.symbol == "BTC/USDT"
            assert d.strategy == "TEST_ONLY"
            assert d.data_source == DataSource.TEST_ONLY
            assert d.candle_index >= 0
            assert d.timestamp_ms > 0


class TestDeterminism:
    def test_same_input_produces_same_output(self):
        candles = [
            _candle(0, 100, 110, 90, 105),
            _candle(1, 105, 112, 95, 108),
            _candle(2, 108, 130, 100, 125),
        ]
        result = replay_deterministic(candles, "BTC/USDT", "TEST_ONLY")
        assert len(result) == 3

    def test_truncation_does_not_change_past_decisions(self):
        """Truncating or modifying future candles does not change already emitted decisions."""
        candles_full = [
            _candle(0, 100, 110, 90, 105),
            _candle(1, 105, 112, 95, 108),
            _candle(2, 108, 130, 100, 125),
        ]
        decisions_full = replay_decisions(candles_full, "BTC/USDT", "TEST_ONLY")

        # Truncate to 2 candles
        candles_short = candles_full[:2]
        decisions_short = replay_decisions(candles_short, "BTC/USDT", "TEST_ONLY")

        # Past decisions must match exactly
        assert decisions_short == decisions_full[:2]


class TestFrozenDTOs:
    def test_candle_is_frozen(self):
        c = _candle(0, 100, 110, 90, 105)
        with pytest.raises(Exception):
            c.open = Decimal("999")

    def test_decision_is_frozen(self):
        d = Decision(
            timestamp_ms=_BASE_MS,
            symbol="BTC/USDT",
            side=DecisionSide.BUY,
            strategy="TEST_ONLY",
            data_source=DataSource.TEST_ONLY,
            candle_index=0,
            reason="test",
        )
        with pytest.raises(Exception):
            d.side = DecisionSide.SELL

    def test_decision_to_dict(self):
        d = Decision(
            timestamp_ms=_BASE_MS,
            symbol="BTC/USDT",
            side=DecisionSide.BUY,
            strategy="TEST_ONLY",
            data_source=DataSource.TEST_ONLY,
            candle_index=0,
            reason="test",
        )
        d_dict = d.to_dict()
        assert d_dict["side"] == "BUY"
        assert d_dict["data_source"] == "TEST_ONLY"
        assert d_dict["strategy"] == "TEST_ONLY"


class TestReplaySegment:
    """Tests for the reproducible replay segment DTO."""

    _VALID_CANDLES = (
        _candle(0, 100, 110, 90, 105),
        _candle(1, 105, 112, 95, 108),
        _candle(2, 108, 130, 100, 125),
    )

    def _make_segment(self, **overrides) -> ReplaySegment:
        kwargs = dict(
            segment_id="seg-test-001",
            symbol="BTC/USDT",
            strategy="TEST_ONLY",
            data_source=DataSource.TEST_ONLY,
            source_ref="ref://test-source",
            clock_ref="ref://test-clock",
            cost_policy_ref="ref://test-cost",
            fill_policy_ref="ref://test-fill",
            start_ms=_BASE_MS,
            end_ms=_BASE_MS + 3 * _STEP_S * 1000,
            candles=self._VALID_CANDLES,
        )
        kwargs.update(overrides)
        return ReplaySegment(**kwargs)

    def test_valid_segment_created(self):
        seg = self._make_segment()
        assert seg.segment_id == "seg-test-001"
        assert seg.data_source == DataSource.TEST_ONLY
        assert len(seg.candles) == 3

    def test_empty_segment_id_rejected(self):
        with pytest.raises(ReplayError, match="segment_id"):
            self._make_segment(segment_id="")

    def test_empty_symbol_rejected(self):
        with pytest.raises(ReplayError, match="symbol"):
            self._make_segment(symbol="")

    def test_non_test_only_source_rejected(self):
        with pytest.raises(ReplayError, match="TEST_ONLY"):
            self._make_segment(data_source="LIVE")

    def test_empty_source_ref_rejected(self):
        with pytest.raises(ReplayError, match="source_ref"):
            self._make_segment(source_ref="")

    def test_start_gte_end_rejected(self):
        with pytest.raises(ReplayError, match="start_ms"):
            self._make_segment(start_ms=1000, end_ms=500)

    def test_candle_outside_window_rejected(self):
        candles = (_candle(0, 100, 110, 90, 105),)
        with pytest.raises(ReplayError, match="outside window"):
            self._make_segment(
                start_ms=_BASE_MS + 1000,
                end_ms=_BASE_MS + 5000,
                candles=candles,
            )

    def test_non_increasing_candle_timestamps_rejected(self):
        candles = (
            _candle(1, 100, 110, 90, 105),
            _candle(0, 105, 112, 95, 108),
        )
        with pytest.raises(ReplayError, match="not strictly increasing"):
            self._make_segment(candles=candles)

    def test_manifest_contains_provenance(self):
        manifest = self._make_segment().to_manifest()

        assert manifest["segment_id"] == "seg-test-001"
        assert manifest["data_source"] == "TEST_ONLY"
        assert manifest["source_ref"] == "ref://test-source"
        assert manifest["clock_ref"] == "ref://test-clock"
        assert manifest["cost_policy_ref"] == "ref://test-cost"
        assert manifest["fill_policy_ref"] == "ref://test-fill"
        assert manifest["candle_count"] == 3

    def test_manifest_serializes_decimals_as_text(self):
        candle = self._make_segment().to_manifest()["candles"][0]

        assert isinstance(candle["open"], str)
        assert isinstance(candle["close"], str)
        assert isinstance(candle["volume"], str)

    def test_reproducible_decisions(self):
        seg = self._make_segment()
        assert replay_segment(seg) == replay_segment(seg)

    def test_truncation_does_not_change_past_decisions(self):
        seg_full = self._make_segment()
        decisions_full = replay_segment(seg_full)
        truncated = self._make_segment(candles=self._VALID_CANDLES[:2])

        assert replay_segment(truncated) == decisions_full[:2]

    def test_frozen_candles_cannot_be_modified(self):
        seg = self._make_segment()
        with pytest.raises(Exception):
            seg.candles[0].open = Decimal("999")

    def test_frozen_segment_cannot_be_modified(self):
        seg = self._make_segment()
        with pytest.raises(Exception):
            seg.symbol = "ETH/USDT"
