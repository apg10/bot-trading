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
    # These are deliberately required, rather than being looked up from a
    # persistence adapter.  A replay is a historical operation and must use
    # the exact immutable inputs with which it was evaluated.
    risk_policy: Any
    risk_snapshot: Any
    filter_snapshot: Any
    execution_snapshot: Any
    market_snapshot: Any
    capacity_snapshot: Any
    fee_snapshot: Any
    lifecycle_state: str


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
    def resolve(value: Any) -> Any:
        import asyncio
        import inspect

        if not inspect.isawaitable(value):
            return value
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
        if loop.is_closed():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
        return loop.run_until_complete(value)

    for method_name in ("load_paper_position", "get_paper_position"):
        method = getattr(persistence, method_name, None)
        if method is not None:
            # Persistence adapters in this repository are synchronous at this
            # boundary, while SQLiteStore's readers are awaitables.
            position = resolve(method(account_id, session_id))
            if position is not None:
                return position

    lots_reader = getattr(persistence, "get_paper_position_lots", None)
    if lots_reader is None:
        lots_reader = getattr(persistence, "load_paper_position_lots", None)
    if lots_reader is not None:
        lots = resolve(lots_reader(account_id))
        # apply_exit_signal_to_paper only needs identity and lots; using a
        # small projection also keeps this coordinator independent of storage.
        from types import SimpleNamespace

        return SimpleNamespace(account_id=account_id, paper_session_id=session_id, lots=list(lots or ()))

    return getattr(persistence, "position", None)


def _durable_context_errors(
    ctx: Any, segment: ReplaySegment, signals: list[PaperSignal]
) -> list[str]:
    """Validate the complete durable input before the first write.

    The low-level adapters also validate their inputs, but doing this here is
    important: a later bad snapshot must not leave an earlier BUY from the
    same replay persisted.  In particular, none of the values below may be
    recovered from ``persistence`` or inferred from a candle.
    """
    # An observational replay has no durable execution boundary.  It must not
    # be forced to manufacture financial inputs merely to count a HOLD.
    operation_times = [signal.timestamp_ms for signal in signals if signal.side != DecisionSide.HOLD]
    if not operation_times:
        return []

    try:
        from src.models.paper_execution import (
            PaperExecutionPolicy,
            PaperFeeSnapshot,
            PaperMarketCapacitySnapshot,
            PaperMarketExecutionSnapshot,
        )
        from src.models.paper_risk import PaperRiskPolicy, PaperRiskSnapshot
        from src.models.paper_symbol_filters import SymbolFilterSnapshot
    except ImportError as exc:  # pragma: no cover - package installation error
        return [f"durable PAPER contracts unavailable: {exc}"]

    required = (
        "risk_policy",
        "risk_snapshot",
        "filter_snapshot",
        "execution_snapshot",
        "market_snapshot",
        "capacity_snapshot",
        "fee_snapshot",
    )
    missing = [name for name in required if _value(ctx, name) is None]
    if missing:
        return ["missing explicit durable snapshots: " + ", ".join(missing)]
    strategy_ref = _value(ctx, "strategy_ref")
    if strategy_ref is None:
        return ["explicit strategy provenance is required"]
    if isinstance(strategy_ref, Mapping):
        if not strategy_ref.get("source_id", strategy_ref.get("strategy_id")):
            return ["strategy provenance source_id is required"]
        if not strategy_ref.get("source_version", strategy_ref.get("strategy_version")):
            return ["strategy provenance source_version is required"]

    expected_types = {
        "risk_policy": PaperRiskPolicy,
        "risk_snapshot": PaperRiskSnapshot,
        "filter_snapshot": SymbolFilterSnapshot,
        "execution_snapshot": PaperExecutionPolicy,
        "market_snapshot": PaperMarketExecutionSnapshot,
        "capacity_snapshot": PaperMarketCapacitySnapshot,
        "fee_snapshot": PaperFeeSnapshot,
    }
    errors: list[str] = []
    values: dict[str, Any] = {}
    for name, expected in expected_types.items():
        value = _value(ctx, name)
        values[name] = value
        if not isinstance(value, expected):
            errors.append(f"{name} must be an explicit {expected.__name__}")
    if errors:
        return errors

    account_id = _value(ctx, "account_id")
    quantity = _value(ctx, "quantity")
    if not isinstance(account_id, str) or not account_id.strip():
        errors.append("explicit account identity is required")
    if not isinstance(_value(ctx, "session_id"), str) or not _value(ctx, "session_id").strip():
        errors.append("explicit paper session identity is required")
    if not isinstance(quantity, Decimal) or not quantity.is_finite() or quantity <= 0:
        errors.append("quantity must be a positive Decimal")

    lifecycle = _value(ctx, "lifecycle_state")
    # Accept plain strings, Pydantic models (.state), and legacy enums (.value).
    lifecycle_value = getattr(lifecycle, "state", None)
    if lifecycle_value is None:
        lifecycle_value = getattr(lifecycle, "value", lifecycle)
    if str(lifecycle_value).lower() not in {"running", "paused", "stopped"}:
        errors.append("lifecycle must be RUNNING, PAUSED, or STOPPED")

    risk_policy = values["risk_policy"]
    risk = values["risk_snapshot"]
    filters = values["filter_snapshot"]
    market = values["market_snapshot"]
    capacity = values["capacity_snapshot"]
    fee = values["fee_snapshot"]
    if risk_policy.account_id != account_id:
        errors.append("risk policy account does not match session account")
    if risk.account_id != account_id:
        errors.append("risk snapshot account does not match session account")
    if risk.market_symbol != segment.symbol:
        errors.append("risk snapshot symbol mismatch")
    if filters.symbol != segment.symbol:
        errors.append("filter snapshot symbol mismatch")
    if market.symbol != segment.symbol:
        errors.append("market snapshot symbol mismatch")
    if capacity.symbol != segment.symbol:
        errors.append("capacity snapshot symbol mismatch")
    if market.provenance != DataSource.TEST_ONLY.value:
        errors.append("market snapshot provenance must be TEST_ONLY")
    if capacity.source_id != DataSource.TEST_ONLY.value:
        errors.append("capacity snapshot provenance must be TEST_ONLY")
    if filters.source_id != DataSource.TEST_ONLY.value:
        errors.append("filter snapshot provenance must be TEST_ONLY")
    if fee.quantity != quantity:
        errors.append("fee snapshot quantity mismatch")
    # Capacity is not a guess derived from a quote: it must be bound to the
    # exact market capture consumed by the fill evaluator.
    if capacity.capture_bar_open_time_ms != market.bar_open_time_ms:
        errors.append("capacity snapshot bar does not match market snapshot")
    if capacity.capture_snapshot_timestamp_ms != market.snapshot_timestamp_ms:
        errors.append("capacity snapshot timestamp does not match market snapshot")

    first_operation = min(operation_times)
    timestamp_values = (
        ("market snapshot", market.snapshot_timestamp_ms),
        ("market bar", market.bar_open_time_ms),
        ("capacity snapshot", capacity.capture_snapshot_timestamp_ms),
        ("capacity bar", capacity.capture_bar_open_time_ms),
        ("filter snapshot", filters.capture_timestamp_ms),
        ("risk market snapshot", risk.market_timestamp_ms),
        ("risk equity snapshot", risk.equity_timestamp_ms),
        ("risk exposure snapshot", risk.exposure_timestamp_ms),
    )
    for name, timestamp in timestamp_values:
        if timestamp > first_operation:
            errors.append(f"{name} is after replay operation")
    return errors


def _validate_signal_sequence(signals: list[PaperSignal]) -> str | None:
    """Reject non-causal position transitions before durable mutation."""
    open_position = False
    for signal in signals:
        if signal.side == DecisionSide.HOLD:
            continue
        if signal.side == DecisionSide.BUY:
            if open_position:
                return f"invalid replay sequence: BUY while position is open at index {signal.candle_index}"
            open_position = True
        elif signal.side == DecisionSide.SELL:
            if not open_position:
                return f"invalid replay sequence: SELL without a causal BUY at index {signal.candle_index}"
            open_position = False
    if open_position:
        return "invalid replay sequence: replay ends with an open BUY position"
    return None


def _validate_signal_provenance(
    signals: list[PaperSignal], segment: ReplaySegment
) -> str | None:
    """Ensure every durable operation retains its replay identity."""
    seen_ids: set[str] = set()
    if len(signals) != len(segment.candles):
        return "invalid replay sequence: signal count does not match candles"
    for index, signal in enumerate(signals):
        if not isinstance(signal.signal_id, str) or not signal.signal_id.strip():
            return f"signal identity missing at index {index}"
        if signal.signal_id in seen_ids:
            return f"duplicate signal identity at index {index}: {signal.signal_id}"
        seen_ids.add(signal.signal_id)
        if signal.segment_id != segment.segment_id:
            return f"signal segment provenance mismatch at index {index}"
        if signal.symbol != segment.symbol or signal.strategy != segment.strategy:
            return f"signal strategy provenance mismatch at index {index}"
        if signal.data_source != segment.data_source:
            return f"signal data provenance mismatch at index {index}"
        if signal.candle_index != index:
            return f"signal candle identity mismatch at index {index}"
        if signal.timestamp_ms != segment.candles[index].timestamp_ms:
            return f"signal timestamp provenance mismatch at index {index}"
    return None


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
            risk_policy=None,
            risk_snapshot=old_risk,
            filter_snapshot=None,
            execution_snapshot=None,
            market_snapshot=None,
            capacity_snapshot=old_capacity,
            fee_snapshot=old_fee,
            lifecycle_state="running" if old_lifecycle is None else str(old_lifecycle),
        )
        persistence = old_persistence

    if segment.data_source != DataSource.TEST_ONLY:
        return _result(segment, failure_count=1, errors=["only TEST_ONLY data_source is allowed"])

    lifecycle = _value(ctx, "lifecycle_state")
    # Accept plain strings, Pydantic models (.state), and legacy enums (.value).
    lifecycle_value = getattr(lifecycle, "state", None)
    if lifecycle_value is None:
        lifecycle_value = getattr(lifecycle, "value", lifecycle)
    if str(lifecycle_value).lower() == "stopped":
        return _result(segment, failure_count=1, errors=["lifecycle is STOPPED"])
    if lifecycle is None:
        return _result(segment, failure_count=1, errors=["explicit lifecycle state is required"])

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

    sequence_error = _validate_signal_sequence(signals)
    if sequence_error is not None:
        return _result(
            segment,
            signal_count=len(signals),
            failure_count=1,
            errors=[sequence_error],
        )
    provenance_error = _validate_signal_provenance(signals, segment)
    if provenance_error is not None:
        return _result(
            segment,
            signal_count=len(signals),
            failure_count=1,
            errors=[provenance_error],
        )

    # A durable session must be completely admissible before its first adapter
    # call.  The non-durable compatibility mode intentionally remains useful
    # for structural replay tests and does not claim to persist anything.
    if persistence is not None:
        context_errors = _durable_context_errors(ctx, segment, signals)
        if context_errors:
            return _result(
                segment,
                signal_count=len(signals),
                failure_count=1,
                errors=context_errors,
            )
        # An exit must never choose an arbitrary lot.  Check the durable
        # projection before any BUY/SELL adapter can register an order.
        try:
            existing_position = _position_for_exit(
                persistence, str(_value(ctx, "account_id")), str(_value(ctx, "session_id"))
            )
        except Exception as exc:
            return _result(
                segment,
                signal_count=len(signals),
                failure_count=1,
                errors=[f"position projection unavailable: {exc}"],
            )
        existing_lots = getattr(existing_position, "lots", None)
        if existing_lots is None and isinstance(existing_position, Mapping):
            existing_lots = existing_position.get("lots")
        if existing_lots is not None and len(list(existing_lots)) > 1:
            return _result(
                segment,
                signal_count=len(signals),
                failure_count=1,
                errors=["ambiguous position: multiple open lots"],
            )

    errors: list[str] = []
    buy_count = sell_count = hold_count = success_count = failure_count = 0

    # Import lazily so replay validation remains usable without initializing
    # the persistence layer, and so adapter ownership remains explicit here.
    from src.paper_orchestrator import apply_exit_signal_to_paper, apply_signal_to_paper

    account_id = _value(ctx, "account_id")
    session_id = _value(ctx, "session_id")
    risk_snapshot = _value(ctx, "risk_snapshot")
    risk_policy = _value(ctx, "risk_policy")
    filter_snapshot = _value(ctx, "filter_snapshot")
    execution_snapshot = _value(ctx, "execution_snapshot")
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
                    filter_snapshot=filter_snapshot,
                    execution_snapshot=execution_snapshot,
                    market_snapshot=market_snapshot,
                    fee_snapshot=fee_snapshot,
                    risk_policy=risk_policy,
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
                    raise ValueError("explicit market snapshot bid is required")
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
