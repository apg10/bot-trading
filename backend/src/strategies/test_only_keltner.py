"""Pure TEST_ONLY Keltner breakout strategy and deterministic OHLCV fixture."""

from __future__ import annotations

from typing import Literal, Sequence

from pydantic import BaseModel, ConfigDict, Field

from src.indicators.keltner import KeltnerPoint, calculate_keltner
from src.models.paper_contracts import StrategyRef
from src.models.state import Candle
from src.models.strategy_config import IndicatorInstance, ParameterEntry, StrategyConfig


TEST_ONLY_DATA_SOURCE = "TEST_ONLY"
TEST_ONLY_SYMBOL = "TEST_ONLY/BTCUSDT"

TEST_ONLY_KELTNER_CONFIG = StrategyConfig(
    strategy_ref=StrategyRef(
        strategy_id="test_only_keltner_breakout",
        strategy_version="test_only_keltner.v1",
    ),
    indicator_instances=(
        IndicatorInstance(
            instance_id="keltner_breakout",
            indicator_id="keltner",
            parameters=(
                ParameterEntry(name="ema_period", value=3),
                ParameterEntry(name="atr_period", value=3),
                ParameterEntry(name="multiplier", value=0.5),
            ),
        ),
    ),
)


# Six stable one-minute bars warm the configured indicator.  Bar six crosses
# the upper band; bar seven has a high that reaches the deterministic target.
TEST_ONLY_OHLCV_FIXTURE: tuple[Candle, ...] = (
    Candle(timestamp_ms=1_699_999_980_000, open=100.0, high=101.0, low=99.0, close=100.0, volume=100.0),
    Candle(timestamp_ms=1_700_000_040_000, open=100.0, high=101.0, low=99.0, close=100.0, volume=100.0),
    Candle(timestamp_ms=1_700_000_100_000, open=100.0, high=101.0, low=99.0, close=100.0, volume=100.0),
    Candle(timestamp_ms=1_700_000_160_000, open=100.0, high=101.0, low=99.0, close=100.0, volume=100.0),
    Candle(timestamp_ms=1_700_000_220_000, open=100.0, high=101.0, low=99.0, close=100.0, volume=100.0),
    Candle(timestamp_ms=1_700_000_280_000, open=100.0, high=101.0, low=99.0, close=100.0, volume=100.0),
    Candle(timestamp_ms=1_700_000_340_000, open=100.0, high=105.0, low=99.0, close=104.0, volume=150.0),
    Candle(timestamp_ms=1_700_000_400_000, open=104.0, high=111.0, low=103.0, close=106.0, volume=150.0),
)


class TestOnlyKeltnerDecision(BaseModel):
    """Typed, non-executable decision produced by the fixture strategy."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    data_source: Literal["TEST_ONLY"] = TEST_ONLY_DATA_SOURCE
    strategy_config: StrategyConfig
    symbol: str = TEST_ONLY_SYMBOL
    timestamp_ms: int = Field(..., ge=0)
    signal: Literal["BUY", "HOLD"]
    reason: str
    entry_price: float | None = None
    stop_loss_price: float | None = None
    take_profit_price: float | None = None
    keltner_upper: float | None = None
    keltner_lower: float | None = None
    keltner_atr: float | None = None


def _indicator_candles(candles: Sequence[Candle]) -> list[dict[str, float | int]]:
    """Adapt immutable Candle DTOs without mutating the caller's sequence."""
    return [
        {
            "open_time": candle.timestamp_ms,
            "open": candle.open,
            "high": candle.high,
            "low": candle.low,
            "close": candle.close,
            "volume": candle.volume,
        }
        for candle in candles
    ]


def _hold_decision(candle: Candle, reason: str) -> TestOnlyKeltnerDecision:
    return TestOnlyKeltnerDecision(
        strategy_config=TEST_ONLY_KELTNER_CONFIG,
        timestamp_ms=candle.timestamp_ms,
        signal="HOLD",
        reason=reason,
    )


def _breakout_decision(candle: Candle, point: KeltnerPoint) -> TestOnlyKeltnerDecision:
    entry = candle.close
    stop = point.lower
    take_profit = entry + point.atr * 2.0
    return TestOnlyKeltnerDecision(
        strategy_config=TEST_ONLY_KELTNER_CONFIG,
        timestamp_ms=candle.timestamp_ms,
        signal="BUY",
        reason="TEST_ONLY Keltner upper-band breakout",
        entry_price=entry,
        stop_loss_price=stop,
        take_profit_price=take_profit,
        keltner_upper=point.upper,
        keltner_lower=point.lower,
        keltner_atr=point.atr,
    )


def evaluate_test_only_keltner(
    candles: Sequence[Candle],
) -> TestOnlyKeltnerDecision:
    """Evaluate only the latest fixture bar, with no I/O or runtime state."""
    if not candles:
        raise ValueError("at least one Candle is required")
    indicator_candles = _indicator_candles(candles)
    points = calculate_keltner(
        indicator_candles,
        ema_period=3,
        atr_period=3,
        multiplier=0.5,
    )
    latest = candles[-1]
    if len(points) < 2:
        return _hold_decision(latest, "insufficient Keltner warmup")

    previous_point, latest_point = points[-2], points[-1]
    previous_close = candles[-2].close
    if previous_close <= previous_point.upper and latest.close > latest_point.upper:
        return _breakout_decision(latest, latest_point)
    return _hold_decision(latest, "no Keltner upper-band cross")


__all__ = [
    "TEST_ONLY_DATA_SOURCE",
    "TEST_ONLY_SYMBOL",
    "TEST_ONLY_KELTNER_CONFIG",
    "TEST_ONLY_OHLCV_FIXTURE",
    "TestOnlyKeltnerDecision",
    "evaluate_test_only_keltner",
]
