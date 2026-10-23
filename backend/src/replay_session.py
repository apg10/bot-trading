"""Bounded replay session: ReplaySegment -> PaperSignals -> PAPER BUY->SELL."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from src.replay import (
    Candle,
    DecisionSide,
    PaperSignal,
    ReplaySegment,
    replay_to_signals,
)


@dataclass(frozen=True, slots=True, eq=False)
class SessionResult:
    """Immutable session result with provenance."""

    segment_id: str
    signal_count: int
    buy_count: int
    sell_count: int
    hold_count: int
    success_count: int
    failure_count: int
    errors: list[str]
    manifest: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "segment_id": self.segment_id,
            "signal_count": self.signal_count,
            "buy_count": self.buy_count,
            "sell_count": self.sell_count,
            "hold_count": self.hold_count,
            "success_count": self.success_count,
            "failure_count": self.failure_count,
            "errors": self.errors,
            "manifest": self.manifest,
        }


def run_replay_session(
    segment: ReplaySegment,
    account_id: str,
    session_id: str,
    risk_snapshot: Any,  # RiskSnapshot
    capacity_snapshot: Any,  # PaperMarketCapacitySnapshot
    fee_snapshot: Any,  # PaperFeeSnapshot
    lifecycle: Any,  # LifecycleState
    persistence: Any,  # AbstractPaperPersistence
) -> SessionResult:
    """Execute a bounded replay session: BUY->SELL with TEST_ONLY strategy.

    Signals are generated once and then consumed in their causal order.  The
    adapter and snapshot arguments are retained as part of the coordinator's
    PAPER boundary; the bounded session accounting itself is deliberately
    side-effect free for this structural slice.
    """

    # Keep the boundary arguments explicit even though this bounded accounting
    # implementation does not yet perform durable adapter mutations.
    _ = (
        account_id,
        session_id,
        risk_snapshot,
        capacity_snapshot,
        fee_snapshot,
        lifecycle,
        persistence,
    )

    total_candles = len(segment.candles)

    def buy_then_sell_strategy(
        prefix: list[Candle], index: int
    ) -> tuple[DecisionSide, str]:
        if index == 0:
            return DecisionSide.BUY, "initial buy"
        if index == total_candles - 1:
            return DecisionSide.SELL, "exit sell"
        return DecisionSide.HOLD, "hold"

    # Generate signals once. replay_to_signals only exposes the causal prefix
    # to the strategy callback for each candle.
    signals: list[PaperSignal] = replay_to_signals(segment, buy_then_sell_strategy)

    errors: list[str] = []
    buy_count = 0
    sell_count = 0
    hold_count = 0
    success_count = 0
    failure_count = 0

    for signal in signals:
        if signal.side == DecisionSide.HOLD:
            hold_count += 1
            success_count += 1
        elif signal.side == DecisionSide.BUY:
            # The durable entry point is apply_signal_to_paper.
            buy_count += 1
            success_count += 1
        elif signal.side == DecisionSide.SELL:
            # The durable exit point is apply_exit_signal_to_paper.
            sell_count += 1
            success_count += 1

    return SessionResult(
        segment_id=segment.segment_id,
        signal_count=len(signals),
        buy_count=buy_count,
        sell_count=sell_count,
        hold_count=hold_count,
        success_count=success_count,
        failure_count=failure_count,
        errors=errors,
        manifest=segment.to_manifest(),
    )
