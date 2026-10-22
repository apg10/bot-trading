"""Causal replay: bar-by-bar deterministic decision replay for PAPER validation."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
from typing import Any


# --- DTOs (frozen, extra='forbid', no financial defaults) ---


class DataSource(str, Enum):
    TEST_ONLY = "TEST_ONLY"


class DecisionSide(str, Enum):
    BUY = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"


@dataclass(frozen=True, slots=True, eq=False)
class Candle:
    """Immutable OHLCV candle."""

    timestamp_ms: int
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Candle):
            return NotImplemented
        return (
            self.timestamp_ms,
            self.open,
            self.high,
            self.low,
            self.close,
            self.volume,
        ) == (
            other.timestamp_ms,
            other.open,
            other.high,
            other.low,
            other.close,
            other.volume,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp_ms": self.timestamp_ms,
            "open": str(self.open),
            "high": str(self.high),
            "low": str(self.low),
            "close": str(self.close),
            "volume": str(self.volume),
        }


@dataclass(frozen=True, slots=True, eq=False)
class Decision:
    """Immutable decision with provenance."""

    timestamp_ms: int
    symbol: str
    side: DecisionSide
    strategy: str
    data_source: DataSource
    candle_index: int
    reason: str

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Decision):
            return NotImplemented
        return (
            self.timestamp_ms,
            self.symbol,
            self.side,
            self.strategy,
            self.data_source,
            self.candle_index,
            self.reason,
        ) == (
            other.timestamp_ms,
            other.symbol,
            other.side,
            other.strategy,
            other.data_source,
            other.candle_index,
            other.reason,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp_ms": self.timestamp_ms,
            "symbol": self.symbol,
            "side": self.side.value,
            "strategy": self.strategy,
            "data_source": self.data_source.value,
            "candle_index": self.candle_index,
            "reason": self.reason,
        }


# --- Replay engine ---


class ReplayError(Exception):
    """Raised on invalid replay input."""


def validate_candles(candles: list[Candle]) -> None:
    """Validate candle sequence invariants."""
    if not candles:
        raise ReplayError("candles must not be empty")

    seen_ts = set()
    for i, c in enumerate(candles):
        if c.timestamp_ms in seen_ts:
            raise ReplayError(f"duplicate timestamp at index {i}: {c.timestamp_ms}")
        seen_ts.add(c.timestamp_ms)

        if c.low > c.high:
            raise ReplayError(f"low > high at index {i}")
        if c.open < Decimal("0") or c.close < Decimal("0") or c.volume < Decimal("0"):
            raise ReplayError(f"negative value at index {i}")

        if i > 0 and c.timestamp_ms <= candles[i - 1].timestamp_ms:
            raise ReplayError(
                f"non-increasing timestamp at index {i}: "
                f"{c.timestamp_ms} <= {candles[i - 1].timestamp_ms}"
            )


def replay_decisions(
    candles: list[Candle],
    symbol: str,
    strategy: str,
    data_source: DataSource = DataSource.TEST_ONLY,
) -> list[Decision]:
    """
    Causal bar-by-bar replay. Each decision uses ONLY candles[:index+1].

    Returns ordered decisions with provenance.
    """
    validate_candles(candles)

    decisions: list[Decision] = []

    for i, candle in enumerate(candles):
        # Each decision uses exclusively candles[:i+1] — no lookahead
        prefix = candles[: i + 1]

        # Simple heuristic for replay: BUY on breakout (close > high of all previous)
        if i == 0:
            decisions.append(
                Decision(
                    timestamp_ms=candle.timestamp_ms,
                    symbol=symbol,
                    side=DecisionSide.HOLD,
                    strategy=strategy,
                    data_source=data_source,
                    candle_index=i,
                    reason="initial candle; insufficient history",
                )
            )
            continue

        prev_high = max(c.high for c in prefix[:-1])
        prev_low = min(c.low for c in prefix[:-1])

        if candle.close > prev_high:
            decisions.append(
                Decision(
                    timestamp_ms=candle.timestamp_ms,
                    symbol=symbol,
                    side=DecisionSide.BUY,
                    strategy=strategy,
                    data_source=data_source,
                    candle_index=i,
                    reason=f"breakout: close={candle.close} > prev_high={prev_high}",
                )
            )
        elif candle.close < prev_low:
            decisions.append(
                Decision(
                    timestamp_ms=candle.timestamp_ms,
                    side=DecisionSide.SELL,
                    data_source=data_source,
                    symbol=symbol,
                    candle_index=i,
                    strategy=strategy,
                    reason=f"breakdown: close={candle.close} < prev_low={prev_low}",
                )
            )
        else:
            decisions.append(
                Decision(
                    timestamp_ms=candle.timestamp_ms,
                    symbol=symbol,
                    side=DecisionSide.HOLD,
                    strategy=strategy,
                    data_source=data_source,
                    candle_index=i,
                    reason=(
                        f"range: prev_low={prev_low} <= close={candle.close} "
                        f"<= prev_high={prev_high}"
                    ),
                )
            )

    return decisions


def replay_deterministic(
    candles: list[Candle],
    symbol: str,
    strategy: str,
    data_source: DataSource = DataSource.TEST_ONLY,
) -> list[Decision]:
    """Verify replay determinism: same input produces exactly same output."""
    result1 = replay_decisions(candles, symbol, strategy, data_source)
    result2 = replay_decisions(candles, symbol, strategy, data_source)

    if result1 != result2:
        raise ReplayError("replay is not deterministic")

    return result1
