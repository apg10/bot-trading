"""Causal replay: bar-by-bar deterministic decision replay for PAPER validation."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
from typing import Any, Callable, Tuple


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

# --- Replay segment DTO ---


@dataclass(frozen=True, slots=True, eq=False)
class ReplaySegment:
    """Immutable reproducible replay segment."""

    segment_id: str
    symbol: str
    strategy: str
    data_source: DataSource
    source_ref: str
    clock_ref: str
    cost_policy_ref: str
    fill_policy_ref: str
    start_ms: int
    end_ms: int
    candles: tuple[Candle, ...]

    def __post_init__(self) -> None:
        """Validate invariants."""
        if not self.segment_id.strip():
            raise ReplayError("segment_id must not be empty")
        if not self.symbol.strip():
            raise ReplayError("symbol must not be empty")
        if not self.strategy.strip():
            raise ReplayError("strategy must not be empty")
        if self.data_source != DataSource.TEST_ONLY:
            raise ReplayError("only TEST_ONLY data_source is allowed")
        if not self.source_ref.strip():
            raise ReplayError("source_ref must not be empty")
        if not self.clock_ref.strip():
            raise ReplayError("clock_ref must not be empty")
        if not self.cost_policy_ref.strip():
            raise ReplayError("cost_policy_ref must not be empty")
        if not self.fill_policy_ref.strip():
            raise ReplayError("fill_policy_ref must not be empty")
        if len(self.candles) == 0:
            raise ReplayError("candles must not be empty")
        if self.start_ms >= self.end_ms:
            raise ReplayError("start_ms must be < end_ms")

        for i, candle in enumerate(self.candles):
            if candle.timestamp_ms < self.start_ms or candle.timestamp_ms >= self.end_ms:
                raise ReplayError(
                    f"candle {i} timestamp {candle.timestamp_ms} outside window "
                    f"[{self.start_ms}, {self.end_ms})"
                )

        for i in range(1, len(self.candles)):
            if self.candles[i].timestamp_ms <= self.candles[i - 1].timestamp_ms:
                raise ReplayError(f"candle {i} timestamp not strictly increasing")

        validate_candles(list(self.candles))

    def to_manifest(self) -> dict[str, Any]:
        """Stable serialization with provenance."""
        return {
            "segment_id": self.segment_id,
            "symbol": self.symbol,
            "strategy": self.strategy,
            "data_source": self.data_source.value,
            "source_ref": self.source_ref,
            "clock_ref": self.clock_ref,
            "cost_policy_ref": self.cost_policy_ref,
            "fill_policy_ref": self.fill_policy_ref,
            "start_ms": self.start_ms,
            "end_ms": self.end_ms,
            "candle_count": len(self.candles),
            "candles": [candle.to_dict() for candle in self.candles],
        }


# --- PAPER signal DTO ---


@dataclass(frozen=True, slots=True, eq=False)
class PaperSignal:
    """Immutable PAPER signal with complete provenance."""

    signal_id: str
    segment_id: str
    symbol: str
    strategy: str
    data_source: DataSource
    timestamp_ms: int
    candle_index: int
    side: DecisionSide
    reason: str

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, PaperSignal):
            return NotImplemented
        return (
            self.signal_id,
            self.segment_id,
            self.symbol,
            self.strategy,
            self.data_source,
            self.timestamp_ms,
            self.candle_index,
            self.side,
            self.reason,
        ) == (
            other.signal_id,
            other.segment_id,
            other.symbol,
            other.strategy,
            other.data_source,
            other.timestamp_ms,
            other.candle_index,
            other.side,
            other.reason,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "signal_id": self.signal_id,
            "segment_id": self.segment_id,
            "symbol": self.symbol,
            "strategy": self.strategy,
            "data_source": self.data_source.value,
            "timestamp_ms": self.timestamp_ms,
            "candle_index": self.candle_index,
            "side": self.side.value,
            "reason": self.reason,
        }


# --- Strategy adapter ---


StrategyCallback = Callable[[list[Candle], int], Tuple[DecisionSide, str]]


def replay_with_strategy(
    segment: ReplaySegment,
    strategy: StrategyCallback,
) -> list[Decision]:
    """
    Replay causal decisions using an explicit strategy callback.

    The callback receives only the causal prefix candles[:index+1] and
    returns a decision side and reason for each candle.
    """
    if not callable(strategy):
        raise ReplayError("strategy must be callable")

    decisions: list[Decision] = []
    candles = list(segment.candles)

    for i, candle in enumerate(candles):
        # Only the causal prefix is visible to the strategy.
        prefix = candles[: i + 1]

        try:
            side, reason = strategy(prefix, i)
        except Exception as exc:
            raise ReplayError(f"strategy failed at index {i}: {exc}") from exc

        if not isinstance(side, DecisionSide):
            raise ReplayError(
                f"strategy must return DecisionSide, got {type(side).__name__}"
            )

        decisions.append(
            Decision(
                timestamp_ms=candle.timestamp_ms,
                symbol=segment.symbol,
                side=side,
                strategy=segment.strategy,
                data_source=segment.data_source,
                candle_index=i,
                reason=reason,
            )
        )

    # Verify order and length match segment.
    assert len(decisions) == len(candles), "decision count must match candle count"

    return decisions


def decisions_to_signals(
    decisions: list[Decision],
    segment: ReplaySegment,
) -> list[PaperSignal]:
    """
    Project replay decisions to PAPER signals.

    Validates provenance alignment between decisions and segment and returns
    exactly one signal per decision with preserved order.
    """
    if len(decisions) != len(segment.candles):
        raise ReplayError(
            f"decision count {len(decisions)} must match candle count {len(segment.candles)}"
        )

    signals: list[PaperSignal] = []

    for i, (decision, candle) in enumerate(zip(decisions, segment.candles)):
        if decision.symbol != segment.symbol:
            raise ReplayError(
                f"symbol mismatch at index {i}: {decision.symbol} != {segment.symbol}"
            )

        if decision.strategy != segment.strategy:
            raise ReplayError(
                f"strategy mismatch at index {i}: {decision.strategy} != {segment.strategy}"
            )

        if decision.data_source != segment.data_source:
            raise ReplayError(f"data_source mismatch at index {i}")

        if decision.candle_index != i:
            raise ReplayError(
                f"candle_index mismatch at index {i}: {decision.candle_index} != {i}"
            )

        if decision.timestamp_ms != candle.timestamp_ms:
            raise ReplayError(
                f"timestamp mismatch at index {i}: "
                f"{decision.timestamp_ms} != {candle.timestamp_ms}"
            )

        signal_id = f"{segment.segment_id}-sig-{i:04d}"
        signals.append(
            PaperSignal(
                signal_id=signal_id,
                segment_id=segment.segment_id,
                symbol=decision.symbol,
                strategy=decision.strategy,
                data_source=decision.data_source,
                timestamp_ms=decision.timestamp_ms,
                candle_index=decision.candle_index,
                side=decision.side,
                reason=decision.reason,
            )
        )

    return signals


def replay_to_signals(
    segment: ReplaySegment,
    strategy: StrategyCallback,
) -> list[PaperSignal]:
    """End-to-end: replay decisions to PAPER signals."""
    decisions = replay_with_strategy(segment, strategy)
    return decisions_to_signals(decisions, segment)


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


def replay_segment(segment: ReplaySegment) -> list[Decision]:
    """Reproduce decisions from a segment using causal prefix only."""
    candles = list(segment.candles)
    return replay_decisions(candles, segment.symbol, segment.strategy, segment.data_source)


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
