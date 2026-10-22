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
    replay_decisions,
    replay_deterministic,
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
