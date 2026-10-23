"""Bounded replay session: ReplaySegment -> PaperSignals -> PAPER BUY->SELL."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Mapping

from src.replay import (
    Candle,
    DataSource,
    DecisionSide,
    PaperSignal,
    ReplayError,
    ReplaySegment,
    replay_to_signals,
)


@dataclass(frozen=True, slots=True, eq=False)
class SessionContext:
    """Immutable explicit session context with all required snapshots."""

    account_id: str
    session_id: str
    quantity: Decimal
    entry_price: Decimal
    exit_price: Decimal
    strategy_ref: dict[str, str]
    risk_snapshot: dict[str, Any]
    capacity_snapshot: dict[str, Any]
    fee_snapshot: dict[str, Any]
    lifecycle_state: str
    market_snapshot: dict[str, Any]


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


def _value(context: Any, name: str, default: Any = None) -> Any:
    """Read both the immutable context and the mapping used by old callers."""
    if isinstance(context, Mapping):
        return context.get(name, default)
    return getattr(context, name, default)


def _paper_result_value(result: Any, name: str, default: Any = None) -> Any:
    if isinstance(result, Mapping):
        return result.get(name, default)
    return getattr(result, name, default)


def _strategy_ref(value: Any, segment: ReplaySegment) -> Any:
    """Adapt the public context's stable mapping to the PAPER DTO when needed."""
    if value is None:
        value = {"source_id": segment.strategy, "source_version": "v1"}
    if hasattr(value, "strategy_id") and hasattr(value, "strategy_version"):
        return value
    if isinstance(value, Mapping):
        from src.models.paper_contracts import StrategyRef

        source_id = value.get("source_id", value.get("strategy_id", segment.strategy))
        source_version = value.get("source_version", value.get("strategy_version", "v1"))
        return StrategyRef(strategy_id=str(source_id), strategy_version=str(source_version))
    return value


def _position_for_exit(persistence: Any, account_id: str, session_id: str) -> Any:
    """Load the durable open lot projection without inventing a financial value."""
    for method_name in ("load_paper_position", "get_paper_position"):
        method = getattr(persistence, method_name, None)
        if method is not None:
            position = method(account_id, session_id)
            # Persistence adapters in this repository are synchronous at this
            # boundary, while SQLiteStore's readers are awaitables.
            import inspect
            import asyncio

            if inspect.isawaitable(position):
                try:
                    loop = asyncio.get_event_loop()
                except RuntimeError:
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
                position = loop.run_until_complete(position)
            if position is not None:
                return position

    lots_reader = getattr(persistence, "get_paper_position_lots", None)
    if lots_reader is None:
        lots_reader = getattr(persistence, "load_paper_position_lots", None)
    if lots_reader is not None:
        lots = lots_reader(account_id)
        import inspect
        import asyncio

        if inspect.isawaitable(lots):
            try:
                loop = asyncio.get_event_loop()
            except RuntimeError:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
            lots = loop.run_until_complete(lots)
        # apply_exit_signal_to_paper only needs identity and lots; using a
        # small projection also keeps this coordinator independent of storage.
        from types import SimpleNamespace

        return SimpleNamespace(account_id=account_id, paper_session_id=session_id, lots=list(lots or ()))

    return getattr(persistence, "position", None)


def _result(
    segment: ReplaySegment,
    *,
    signal_count: int = 0,
    buy_count: int = 0,
    sell_count: int = 0,
    hold_count: int = 0,
    success_count: int = 0,
    failure_count: int = 0,
    errors: list[str] | None = None,
) -> SessionResult:
    return SessionResult(
        segment_id=segment.segment_id,
        signal_count=signal_count,
        buy_count=buy_count,
        sell_count=sell_count,
        hold_count=hold_count,
        success_count=success_count,
        failure_count=failure_count,
        errors=[] if errors is None else errors,
        manifest=segment.to_manifest(),
    )


def run_replay_session(
    segment: ReplaySegment,
    ctx: SessionContext | Mapping[str, Any] | str,
    persistence: Any = None,
    *legacy: Any,
) -> SessionResult:
    """Execute a bounded replay session using the durable PAPER adapters.

    Signals are materialized exactly once and consumed in causal order.  HOLD
    is intentionally side-effect-free; BUY and SELL are delegated to the
    idempotent adapters, whose deterministic signal IDs are the operation keys.
    ``persistence=None`` is retained as a non-mutating compatibility mode for
    the structural replay tests; a real persistence object always uses the
    durable path.
    """
    # T8 exposed the individual context fields as positional arguments.  Keep
    # that call shape readable for existing clients while making SessionContext
    # the canonical API for T9.
    if legacy:
        old_account_id = ctx
        old_session_id = persistence
        old_risk, old_capacity, old_fee, old_lifecycle, old_persistence = legacy
        ctx = SessionContext(
            account_id=str(old_account_id),
            session_id=str(old_session_id),
            quantity=Decimal("0"),
            entry_price=Decimal("0"),
            exit_price=Decimal("0"),
            strategy_ref={"source_id": segment.strategy, "source_version": "v1"},
            risk_snapshot={} if old_risk is None else old_risk,
            capacity_snapshot={} if old_capacity is None else old_capacity,
            fee_snapshot={} if old_fee is None else old_fee,
            lifecycle_state="running" if old_lifecycle is None else str(old_lifecycle),
            market_snapshot={},
        )
        persistence = old_persistence

    if segment.data_source != DataSource.TEST_ONLY:
        return _result(segment, failure_count=1, errors=["only TEST_ONLY data_source is allowed"])

    lifecycle = _value(ctx, "lifecycle_state", "running")
    lifecycle_value = getattr(lifecycle, "value", lifecycle)
    if str(lifecycle_value).lower() == "stopped":
        return _result(segment, failure_count=1, errors=["lifecycle is STOPPED"])

    total_candles = len(segment.candles)

    def default_strategy(prefix: list[Candle], index: int) -> tuple[DecisionSide, str]:
        # A one-candle segment cannot contain a causal entry and exit pair;
        # keep it observational rather than manufacturing a position.
        if total_candles == 1:
            return DecisionSide.HOLD, "hold"
        if index == 0:
            return DecisionSide.BUY, "initial buy"
        if index == total_candles - 1:
            return DecisionSide.SELL, "exit sell"
        return DecisionSide.HOLD, "hold"

    try:
        signals: list[PaperSignal] = replay_to_signals(segment, default_strategy)
    except ReplayError as exc:
        return _result(segment, failure_count=1, errors=[str(exc)])

    errors: list[str] = []
    buy_count = sell_count = hold_count = success_count = failure_count = 0

    # Import lazily so replay validation remains usable without initializing
    # the persistence layer, and so adapter ownership remains explicit here.
    from src.paper_orchestrator import apply_exit_signal_to_paper, apply_signal_to_paper

    account_id = _value(ctx, "account_id")
    session_id = _value(ctx, "session_id")
    risk_snapshot = _value(ctx, "risk_snapshot")
    capacity_snapshot = _value(ctx, "capacity_snapshot")
    fee_snapshot = _value(ctx, "fee_snapshot")
    market_snapshot = _value(ctx, "market_snapshot")
    strategy_ref = _strategy_ref(_value(ctx, "strategy_ref"), segment)

    for signal in signals:
        if signal.data_source != DataSource.TEST_ONLY:
            errors.append(f"non-TEST_ONLY provenance at index {signal.candle_index}")
            failure_count += 1
            continue

        if signal.side == DecisionSide.HOLD:
            hold_count += 1
            success_count += 1
            continue

        if signal.side == DecisionSide.BUY:
            buy_count += 1
            if persistence is None:
                # Explicitly non-durable compatibility mode; never claim this
                # is a successful persistence mutation.
                success_count += 1
                continue
            try:
                result = apply_signal_to_paper(
                    signal=signal,
                    segment=segment,
                    quantity=_value(ctx, "quantity"),
                    entry_price=_value(ctx, "entry_price"),
                    strategy_ref=strategy_ref,
                    risk_snapshot=risk_snapshot,
                    lifecycle=lifecycle,
                    persistence=persistence,
                    filter_snapshot=_value(
                        ctx,
                        "filter_snapshot",
                        getattr(
                            persistence,
                            "filter_snapshot",
                            getattr(persistence, "symbol_filter_snapshot", None),
                        ),
                    ),
                    execution_snapshot=_value(
                        ctx,
                        "execution_snapshot",
                        getattr(
                            persistence,
                            "execution_snapshot",
                            getattr(persistence, "exec_policy_snapshot", None),
                        ),
                    ),
                    market_snapshot=market_snapshot,
                    fee_snapshot=fee_snapshot,
                    risk_policy=_value(
                        ctx, "risk_policy", getattr(persistence, "risk_policy", None)
                    ),
                    market_capacity_snapshot=capacity_snapshot,
                )
                if _paper_result_value(result, "success", False):
                    success_count += 1
                else:
                    failure_count += 1
                    errors.append(str(_paper_result_value(result, "error", "BUY failed")))
            except Exception as exc:
                failure_count += 1
                errors.append(str(exc))
            continue

        if signal.side == DecisionSide.SELL:
            sell_count += 1
            if persistence is None:
                success_count += 1
                continue
            try:
                bid_price = _value(market_snapshot, "bid")
                if bid_price is None:
                    bid_price = _value(ctx, "exit_price")
                position = _position_for_exit(persistence, str(account_id), str(session_id))
                result = apply_exit_signal_to_paper(
                    signal=signal,
                    segment=segment,
                    position=position,
                    bid_price=bid_price,
                    capacity_snapshot=capacity_snapshot,
                    fee_snapshot=fee_snapshot,
                    risk_snapshot=risk_snapshot,
                    lifecycle=lifecycle,
                    persistence=persistence,
                )
                if _paper_result_value(result, "success", False):
                    success_count += 1
                else:
                    failure_count += 1
                    errors.append(str(_paper_result_value(result, "error", "SELL failed")))
            except Exception as exc:
                failure_count += 1
                errors.append(str(exc))

    return _result(
        segment,
        signal_count=len(signals),
        buy_count=buy_count,
        sell_count=sell_count,
        hold_count=hold_count,
        success_count=success_count,
        failure_count=failure_count,
        errors=errors,
    )
