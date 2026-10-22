"""Minimal deterministic PAPER orchestration for TEST_ONLY and public quotes."""

from __future__ import annotations

import asyncio
import inspect
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Protocol, Sequence

from pydantic import BaseModel, ConfigDict

from src.models.paper_contracts import (
    Commission,
    LotAllocation,
    PaperFill,
    PaperOrder,
    PaperOrderSide,
    PaperOrderStatus,
    PaperOrderType,
    StrategyRef,
)
from src.models.paper_execution import (
    PaperExecutionPolicy,
    PaperFeeSnapshot,
    PaperMarketCapacitySnapshot,
    PaperMarketExecutionSnapshot,
    PaperReservationSnapshot,
)
from src.models.paper_risk import PaperRiskPolicy, PaperRiskSnapshot, evaluate_paper_risk
from src.models.paper_symbol_filters import SymbolFilterSnapshot, validate_symbol_filters
from src.models.state import Candle, EngineState as LifecycleState, MarketSnapshot
from src.market.engine import MarketEngine
from src.market.paper_snapshot import (
    PaperMarketExecutionSnapshot as EnginePaperMarketExecutionSnapshot,
)
from src.persistence.sqlite_store import SQLiteStore
from src.strategies.test_only_keltner import (
    TEST_ONLY_OHLCV_FIXTURE,
    evaluate_test_only_keltner,
)
from src.paper_execution_integration import evaluate_and_apply_fill
from src.replay import DataSource, DecisionSide, PaperSignal, ReplaySegment


# These aliases keep the entry adapter's public vocabulary independent from the
# more specific DTO names used by the existing PAPER implementation.
RiskSnapshot = PaperRiskSnapshot
FilterSnapshot = SymbolFilterSnapshot
ExecutionSnapshot = PaperExecutionPolicy


class AbstractPaperPersistence(Protocol):
    """Small persistence surface accepted by the replay signal adapter.

    The protocol is intentionally structural: the adapter can be used with the
    lightweight persistence fixture used by replay tests as well as with the
    durable store's ``register_paper_order``/``save_paper_fill`` methods.
    """


class PaperResult(BaseModel):
    """Stable result returned by a replay signal entry attempt."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    success: bool
    signal: str
    order_id: str | None = None
    fill_id: str | None = None
    error: str | None = None


class OrchestratorResult(BaseModel):
    """Frozen, non-operational summary of one local PAPER cycle."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    success: bool
    signal: str
    order_id: str | None = None
    fill_id: str | None = None
    evaluation_id: str | None = None
    risk_result: str | None = None
    filter_result: str | None = None
    error: str | None = None
    buy_status: str | None = None
    sell_status: str | None = None


def _build_order(
    account_id: str,
    session_id: str,
    symbol: str,
    quantity: Decimal,
    entry_price: Decimal,
    strategy_ref,
    timestamp_ms: int,
) -> PaperOrder:
    return PaperOrder(
        order_id="order-id-1",
        client_order_id="client-order-1",
        paper_account_id=account_id,
        paper_session_id=session_id,
        environment="paper",
        symbol=symbol,
        side=PaperOrderSide.BUY,
        order_type=PaperOrderType.LIMIT,
        requested_qty=quantity,
        price=entry_price,
        strategy_ref=strategy_ref,
        status=PaperOrderStatus.PREPARED,
        timestamp_ms=timestamp_ms,
    )


def _reservation_snapshot(order: PaperOrder, reservation) -> PaperReservationSnapshot:
    return PaperReservationSnapshot(
        reservation_id=reservation.reservation_id,
        owner_order_id=reservation.owner_order_id,
        owner_group_id=reservation.owner_group_id,
        account_id=order.paper_account_id,
        asset=reservation.amount.asset,
        remaining_qty=order.remaining_qty,
        remaining_amount=reservation.amount.amount,
    )


def _interval_ms(engine: MarketEngine, snapshot: Any) -> int | None:
    """Return the engine interval without making a market-data assumption."""
    config = getattr(engine, "config", None)
    interval = getattr(config, "kline_interval", None)
    if interval is None:
        interval = getattr(snapshot, "bar_identity", "").split(":", 1)[0]
    fixed = {
        "1s": 1_000,
        "1m": 60_000,
        "3m": 180_000,
        "5m": 300_000,
        "15m": 900_000,
        "30m": 1_800_000,
        "1h": 3_600_000,
        "2h": 7_200_000,
        "4h": 14_400_000,
        "6h": 21_600_000,
        "8h": 28_800_000,
        "12h": 43_200_000,
        "1d": 86_400_000,
        "3d": 259_200_000,
        "1w": 604_800_000,
    }
    return fixed.get(interval)


async def _validate_snapshot(
    engine: MarketEngine, snapshot: EnginePaperMarketExecutionSnapshot
) -> bool:
    """Validate a public quote against the current engine context."""
    try:
        state = engine.state
        if snapshot.symbol != state.symbol:
            return False

        engine_snapshot = engine.snapshot()
        engine_bar = engine_snapshot.get("bar_identity")
        if engine_bar and snapshot.bar_identity != engine_bar:
            return False
        state_bar = getattr(state, "bar_identity", None)
        if state_bar and snapshot.bar_identity != state_bar:
            return False
        # Current MarketEngine exposes the bar open time rather than a materialized
        # identity in snapshot(). Reconstructing it here also detects interval
        # changes between the engine read and the quote read.
        interval = getattr(getattr(engine, "config", None), "kline_interval", None)
        interval = interval or getattr(state, "interval", None)
        if interval and not snapshot.bar_identity.startswith(f"{interval}:"):
            return False
        if state.last_bar_open_time is not None and interval:
            opened = datetime.fromtimestamp(
                state.last_bar_open_time / 1000, tz=timezone.utc
            )
            expected_bar = (
                f"{interval}:{opened.isoformat(timespec='seconds').replace('+00:00', 'Z')}"
            )
            if snapshot.bar_identity != expected_bar:
                return False

        if snapshot.provenance != "binance_public_book_ticker":
            return False
        if snapshot.version != "v3":
            return False
        now = engine._clock_ms()
        quote_age_ms = now - int(snapshot.timestamp.timestamp() * 1000)
        if quote_age_ms < 0 or quote_age_ms > 30_000:
            return False
        if not state.entries_allowed or state.pending_gaps or state.stale:
            return False
        # ``get_paper_snapshot`` already enforces these gates on the concrete
        # engine; retain them here for deterministic adapters/fake engines too.
        # Missing required state is incomplete and must fail closed.
        for gate in ("connected", "complete", "history_loaded"):
            val = getattr(state, gate, None)
            if val is None or not val:
                return False
        if getattr(state, "reconnecting", None) is True:
            return False
        if getattr(state, "recovering", None) is True:
            return False
        return True
    except (AttributeError, TypeError, ValueError, OverflowError):
        return False


def _model_market_snapshot(
    engine: MarketEngine, snapshot: EnginePaperMarketExecutionSnapshot
) -> PaperMarketExecutionSnapshot | None:
    """Adapt the engine dataclass to the durable evaluator DTO."""
    state = engine.state
    interval_ms = _interval_ms(engine, snapshot)
    if state.last_bar_open_time is None or interval_ms is None:
        return None
    try:
        return PaperMarketExecutionSnapshot(
            symbol=snapshot.symbol,
            bid=snapshot.bid,
            ask=snapshot.ask,
            bar_open_time_ms=state.last_bar_open_time,
            prior_bar_open_time_ms=state.last_bar_open_time - interval_ms,
            interval_ms=interval_ms,
            provenance=snapshot.provenance,
            version=snapshot.version,
            snapshot_timestamp_ms=int(snapshot.timestamp.timestamp() * 1000),
        )
    except (TypeError, ValueError):
        return None


def _test_only_capacity(
    capacity: PaperMarketCapacitySnapshot | None,
    market: PaperMarketExecutionSnapshot,
) -> PaperMarketCapacitySnapshot | None:
    """Bind explicit TEST_ONLY capacity without using book-ticker quantities."""
    if capacity is None:
        return None
    try:
        return PaperMarketCapacitySnapshot(
            source_id="TEST_ONLY",
            source_version="v1",
            capacity=capacity.capacity,
            symbol=market.symbol,
            capture_bar_open_time_ms=market.bar_open_time_ms,
            capture_snapshot_timestamp_ms=market.snapshot_timestamp_ms,
        )
    except (TypeError, ValueError):
        return None


async def run_paper_cycle(
    store: SQLiteStore,
    *,
    account_id: str,
    session_id: str,
    symbol: str,
    quantity: Decimal,
    risk_policy: PaperRiskPolicy,
    risk_snapshot: PaperRiskSnapshot,
    symbol_filter_snapshot: SymbolFilterSnapshot | None,
    exec_policy_snapshot: PaperExecutionPolicy,
    market_exec_snapshot: PaperMarketExecutionSnapshot | None = None,
    market_capacity_snapshot: PaperMarketCapacitySnapshot | None = None,
    fee_snapshot: PaperFeeSnapshot | None = None,
    candles: Sequence[Candle] = TEST_ONLY_OHLCV_FIXTURE[:7],
    evaluated_at_ms: int = 0,
    engine: MarketEngine | None = None,
) -> OrchestratorResult:
    """Run one bounded TEST_ONLY BUY cycle through the PAPER infrastructure.

    Account opening and all market/risk/fee snapshots are explicit caller-owned
    state.  This function creates no defaults for money or market data.
    """
    decision = evaluate_test_only_keltner(candles)
    if decision.signal == "HOLD":
        return OrchestratorResult(success=True, signal="HOLD")

    order = _build_order(
        account_id,
        session_id,
        symbol,
        quantity,
        Decimal(str(decision.entry_price)),
        decision.strategy_config.strategy_ref,
        evaluated_at_ms,
    )

    if not await store.is_buy_allowed(account_id, session_id):
        return OrchestratorResult(
            success=False,
            signal="BUY",
            error="PAPER BUY admission requires an explicit RUNNING lifecycle state",
        )

    risk_result = evaluate_paper_risk(order, risk_policy, risk_snapshot)
    if not risk_result.approved:
        return OrchestratorResult(
            success=False,
            signal="BUY",
            risk_result=risk_result.code,
            error="risk rejected PAPER order",
        )

    filter_result = validate_symbol_filters(order, symbol_filter_snapshot)
    if not filter_result.approved:
        return OrchestratorResult(
            success=False,
            signal="BUY",
            risk_result=risk_result.code,
            filter_result=filter_result.code,
            error="symbol filters rejected PAPER order",
        )

    evaluation_id = "eval-cycle-1"
    try:
        # Check durable idempotency before reading the engine.  A completed
        # fill must remain retryable even when a later engine snapshot is
        # unavailable.
        durable_order = await store.load_paper_order(order.order_id)
        if (
            durable_order is not None
            and durable_order.status == PaperOrderStatus.FILLED
            and durable_order.fills
        ):
            return OrchestratorResult(
                success=True,
                signal="BUY",
                order_id=durable_order.order_id,
                fill_id=durable_order.fills[0].fill_id,
                evaluation_id=evaluation_id,
                risk_result=risk_result.code,
                filter_result=filter_result.code,
            )

        # TEST_ONLY remains an explicit caller-supplied path.  Once an engine
        # is supplied, however, stale/disconnected/context-changed market data
        # must fail closed before registration or any durable mutation.
        effective_market_snapshot = market_exec_snapshot
        effective_capacity_snapshot = market_capacity_snapshot
        if engine is not None:
            try:
                engine_snapshot = await engine.get_paper_snapshot()
            except Exception:
                engine_snapshot = None
            if engine_snapshot is None or not await _validate_snapshot(engine, engine_snapshot):
                return OrchestratorResult(
                    success=False,
                    signal="BUY",
                    risk_result=risk_result.code,
                    filter_result=filter_result.code,
                    error="PAPER BUY requires a fresh, coherent market snapshot",
                )
            effective_market_snapshot = _model_market_snapshot(engine, engine_snapshot)
            if effective_market_snapshot is None:
                return OrchestratorResult(
                    success=False,
                    signal="BUY",
                    risk_result=risk_result.code,
                    filter_result=filter_result.code,
                    error="PAPER BUY market snapshot could not be bound to engine state",
                )
            effective_capacity_snapshot = _test_only_capacity(
                market_capacity_snapshot, effective_market_snapshot
            )

        if effective_market_snapshot is None or effective_capacity_snapshot is None or fee_snapshot is None:
            return OrchestratorResult(
                success=False,
                signal="BUY",
                risk_result=risk_result.code,
                filter_result=filter_result.code,
                error="PAPER BUY requires explicit market, capacity, and fee snapshots",
            )

        if durable_order is None:
            durable_order = await store.register_paper_order(order)
        elif durable_order.order_id != order.order_id:
            raise ValueError("durable order identity mismatch")

        accepted_order = await store.accept_paper_order(
            durable_order.order_id,
            risk_policy,
            risk_snapshot,
            symbol_filter_snapshot,
        )
        active_reservation = await store.load_paper_reservation(accepted_order.order_id)
        if active_reservation is None:
            raise RuntimeError("accepted BUY order has no active reservation")
        reservation_snapshot = _reservation_snapshot(accepted_order, active_reservation)
        integration = await evaluate_and_apply_fill(
            store,
            evaluation_id,
            accepted_order,
            reservation_snapshot,
            exec_policy_snapshot,
            effective_market_snapshot,
            effective_capacity_snapshot,
            fee_snapshot,
            evaluated_at_ms=evaluated_at_ms,
        )
        return OrchestratorResult(
            success=integration.applied,
            signal="BUY",
            order_id=accepted_order.order_id,
            fill_id=integration.fill_id,
            evaluation_id=integration.evaluation_id,
            risk_result=risk_result.code,
            filter_result=filter_result.code,
            error=None if integration.applied else "PAPER fill was not applied",
        )
    except Exception as exc:
        return OrchestratorResult(
            success=False,
            signal="BUY",
            order_id=order.order_id,
            evaluation_id=evaluation_id,
            risk_result=risk_result.code,
            filter_result=filter_result.code,
            error=str(exc),
        )


async def run_paper_cycle_sell(
    store: SQLiteStore,
    *,
    account_id: str,
    session_id: str,
    symbol: str,
    risk_policy: PaperRiskPolicy,
    risk_snapshot: PaperRiskSnapshot,
    symbol_filter_snapshot: SymbolFilterSnapshot | None,
    exec_policy_snapshot: PaperExecutionPolicy,
    fee_snapshot: PaperFeeSnapshot,
    evaluated_at_ms: int | None = None,
    engine: MarketEngine | None = None,
) -> OrchestratorResult:
    """Close the durable BUY lot through the bounded SELL PAPER path."""
    buy_order = await store.load_paper_order("order-id-1")
    if buy_order is None:
        return OrchestratorResult(
            success=False, signal="SELL", error="durable BUY order not found"
        )
    if buy_order.status != PaperOrderStatus.FILLED or not buy_order.fills:
        return OrchestratorResult(
            success=False,
            signal="SELL",
            buy_status=buy_order.status.value,
            error="durable BUY order is not FILLED",
        )

    buy_qty = sum(fill.qty for fill in buy_order.fills)
    if buy_qty <= 0:
        return OrchestratorResult(
            success=False,
            signal="SELL",
            buy_status=buy_order.status.value,
            error="durable BUY has no positive position quantity",
        )

    # The fixture's seventh one-minute bar is index six and closes at 104.
    exit_bar = TEST_ONLY_OHLCV_FIXTURE[6]
    prior_bar = TEST_ONLY_OHLCV_FIXTURE[5]
    exit_timestamp_ms = exit_bar.timestamp_ms
    sell_order_id = "sell-order-1"
    evaluation_id = "eval-cycle-sell-1"
    exit_price = Decimal(str(exit_bar.close))
    market_snapshot: PaperMarketExecutionSnapshot | None = None

    # Check durable idempotency before reading the engine.  A completed SELL
    # fill must remain retryable even when a later engine snapshot is
    # unavailable.
    try:
        durable_sell = await store.load_paper_order(sell_order_id)
    except Exception as exc:
        return OrchestratorResult(
            success=False,
            signal="SELL",
            buy_status=buy_order.status.value,
            error=str(exc),
        )
    if (
        durable_sell is not None
        and durable_sell.status == PaperOrderStatus.FILLED
        and durable_sell.fills
    ):
        risk_result = evaluate_paper_risk(durable_sell, risk_policy, risk_snapshot)
        filter_result = validate_symbol_filters(durable_sell, symbol_filter_snapshot)
        return OrchestratorResult(
            success=True,
            signal="SELL",
            order_id=durable_sell.order_id,
            fill_id=durable_sell.fills[0].fill_id,
            evaluation_id=evaluation_id,
            risk_result=risk_result.code,
            filter_result=filter_result.code,
            buy_status=buy_order.status.value,
            sell_status=durable_sell.status.value,
        )

    if engine is not None:
        try:
            engine_snapshot = await engine.get_paper_snapshot()
        except Exception:
            engine_snapshot = None
        if engine_snapshot is None or not await _validate_snapshot(engine, engine_snapshot):
            return OrchestratorResult(
                success=False,
                signal="SELL",
                buy_status=buy_order.status.value,
                error="PAPER SELL requires a fresh, coherent market snapshot",
            )
        market_snapshot = _model_market_snapshot(engine, engine_snapshot)
        if market_snapshot is None:
            return OrchestratorResult(
                success=False,
                signal="SELL",
                buy_status=buy_order.status.value,
                error="PAPER SELL market snapshot could not be bound to engine state",
            )
        # The execution policy selects bid for SELL.  Keep the order limit at
        # that same accepted reference so a public quote is not accidentally
        # converted into a different TEST_ONLY close price.
        exit_price = market_snapshot.bid
    sell_order = PaperOrder(
        order_id=sell_order_id,
        client_order_id="client-sell-order-1",
        paper_account_id=account_id,
        paper_session_id=session_id,
        environment="paper",
        symbol=symbol,
        side=PaperOrderSide.SELL,
        order_type=PaperOrderType.LIMIT,
        requested_qty=buy_qty,
        price=exit_price,
        strategy_ref=buy_order.strategy_ref,
        status=PaperOrderStatus.PREPARED,
        timestamp_ms=exit_timestamp_ms,
        lot_allocations=(
            LotAllocation(
                position_id=buy_order.order_id,
                account_id=account_id,
                symbol=symbol,
                strategy_ref=buy_order.strategy_ref,
                qty=buy_qty,
            ),
        ),
    )

    risk_result = evaluate_paper_risk(sell_order, risk_policy, risk_snapshot)
    filter_result = validate_symbol_filters(sell_order, symbol_filter_snapshot)
    if not risk_result.approved or not filter_result.approved:
        return OrchestratorResult(
            success=False,
            signal="SELL",
            buy_status=buy_order.status.value,
            risk_result=risk_result.code,
            filter_result=filter_result.code,
            error="SELL revalidation rejected PAPER order",
        )

    try:
        durable_sell = await store.load_paper_order(sell_order_id)
        if durable_sell is None:
            durable_sell = await store.register_paper_order(sell_order)

        if durable_sell.status == PaperOrderStatus.FILLED:
            evaluation = await store.load_paper_fill_evaluation(evaluation_id)
            if evaluation is None:
                raise RuntimeError("filled durable SELL has no evaluation record")
            return OrchestratorResult(
                success=True,
                signal="SELL",
                order_id=sell_order_id,
                fill_id=evaluation.fill_candidate_fill_id,
                evaluation_id=evaluation_id,
                risk_result=risk_result.code,
                filter_result=filter_result.code,
                buy_status=buy_order.status.value,
                sell_status=durable_sell.status.value,
            )

        accepted_sell = await store.accept_paper_order(durable_sell.order_id)
        active_reservation = await store.load_paper_reservation(accepted_sell.order_id)
        if active_reservation is None:
            raise RuntimeError("accepted SELL order has no active reservation")
        reservation_snapshot = _reservation_snapshot(accepted_sell, active_reservation)
        if market_snapshot is None:
            market_snapshot = PaperMarketExecutionSnapshot(
                symbol=symbol,
                bid=exit_price,
                ask=exit_price,
                bar_open_time_ms=exit_timestamp_ms,
                prior_bar_open_time_ms=prior_bar.timestamp_ms,
                interval_ms=exit_timestamp_ms - prior_bar.timestamp_ms,
                provenance="TEST_ONLY",
                version="v1",
                snapshot_timestamp_ms=exit_timestamp_ms,
            )
        capacity_snapshot = PaperMarketCapacitySnapshot(
            source_id="TEST_ONLY",
            source_version="v1",
            capacity=buy_qty,
            symbol=market_snapshot.symbol,
            capture_bar_open_time_ms=market_snapshot.bar_open_time_ms,
            capture_snapshot_timestamp_ms=market_snapshot.snapshot_timestamp_ms,
        )
        integration = await evaluate_and_apply_fill(
            store,
            evaluation_id,
            accepted_sell,
            reservation_snapshot,
            exec_policy_snapshot,
            market_snapshot,
            capacity_snapshot,
            fee_snapshot,
            evaluated_at_ms=(
                exit_timestamp_ms if evaluated_at_ms is None else evaluated_at_ms
            ),
        )
        return OrchestratorResult(
            success=integration.applied,
            signal="SELL",
            order_id=accepted_sell.order_id,
            fill_id=integration.fill_id,
            evaluation_id=integration.evaluation_id,
            risk_result=risk_result.code,
            filter_result=filter_result.code,
            error=None if integration.applied else "PAPER SELL fill was not applied",
            buy_status=buy_order.status.value,
            sell_status=("filled" if integration.applied else accepted_sell.status.value),
        )
    except Exception as exc:
        return OrchestratorResult(
            success=False,
            signal="SELL",
            order_id=sell_order_id,
            evaluation_id=evaluation_id,
            risk_result=risk_result.code,
            filter_result=filter_result.code,
            buy_status=buy_order.status.value,
            error=str(exc),
        )


def _run_adapter_io(operation: Any) -> Any:
    """Run one persistence awaitable from the adapter's synchronous API."""
    if not inspect.isawaitable(operation):
        return operation
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    return loop.run_until_complete(operation)


def _adapter_order_value(order: Any, key: str, default: Any = None) -> Any:
    """Read either a PAPER DTO or a projected persistence dictionary."""
    if isinstance(order, dict):
        return order.get(key, default)
    return getattr(order, key, default)


def _adapter_orders(persistence: Any) -> list[Any]:
    """Load orders through either the replay fixture or SQLiteStore API."""
    if hasattr(persistence, "load_paper_orders"):
        return list(_run_adapter_io(persistence.load_paper_orders("acct-1")))
    if hasattr(persistence, "get_paper_orders"):
        return list(_run_adapter_io(persistence.get_paper_orders("acct-1")))
    return []


def _adapter_commission(fee_snapshot: Any, quantity: Decimal, price: Decimal) -> Commission:
    """Adapt the entry task's fee-rate shape to the durable PAPER contract."""
    commission = getattr(fee_snapshot, "commission", None)
    if commission is not None:
        return commission
    fee_rate = getattr(fee_snapshot, "fee_rate", None)
    if fee_rate is None:
        raise ValueError("fee snapshot must provide commission or fee_rate")
    quote_asset = getattr(getattr(fee_snapshot, "quote_asset", None), "value", None)
    if quote_asset is None:
        quote_asset = getattr(fee_snapshot, "quote_asset", "USDT")
    return Commission(
        amount=fee_rate * quantity * price,
        asset=str(quote_asset),
    )


# --- Signal entry adapter ---


def apply_signal_to_paper(
    signal: PaperSignal,
    segment: ReplaySegment,
    quantity: Decimal,
    entry_price: Decimal,
    strategy_ref: StrategyRef,
    risk_snapshot: RiskSnapshot,
    lifecycle: LifecycleState,
    persistence: AbstractPaperPersistence,
    filter_snapshot: FilterSnapshot,
    execution_snapshot: ExecutionSnapshot,
    market_snapshot: MarketSnapshot,
    fee_snapshot: PaperFeeSnapshot,
) -> PaperResult:
    """Route one already-materialized replay signal into PAPER persistence.

    All signal/segment checks happen before touching persistence.  In
    particular, this adapter never invokes a strategy or derives a signal from
    candles; the replay signal is the sole decision input.
    """
    # Validate segment/signal identity before any mutation.
    if signal.segment_id != segment.segment_id:
        return PaperResult(
            success=False,
            error=f"segment_id mismatch: {signal.segment_id} != {segment.segment_id}",
            signal=signal.side.value,
        )

    if signal.symbol != segment.symbol:
        return PaperResult(
            success=False,
            error=f"symbol mismatch: {signal.symbol} != {segment.symbol}",
            signal=signal.side.value,
        )

    if signal.strategy != segment.strategy:
        return PaperResult(
            success=False,
            error=f"strategy mismatch: {signal.strategy} != {segment.strategy}",
            signal=signal.side.value,
        )

    if (
        signal.data_source != DataSource.TEST_ONLY
        or segment.data_source != DataSource.TEST_ONLY
        or signal.data_source != segment.data_source
    ):
        return PaperResult(
            success=False,
            error="only TEST_ONLY data_source is allowed",
            signal=signal.side.value,
        )

    if signal.candle_index < 0 or signal.candle_index >= len(segment.candles):
        return PaperResult(
            success=False,
            error=f"candle_index out of range: {signal.candle_index}",
            signal=signal.side.value,
        )

    candle = segment.candles[signal.candle_index]
    if signal.timestamp_ms != candle.timestamp_ms:
        return PaperResult(
            success=False,
            error=(
                f"timestamp mismatch: {signal.timestamp_ms} != "
                f"{candle.timestamp_ms}"
            ),
            signal=signal.side.value,
        )
    if signal.timestamp_ms < segment.start_ms or signal.timestamp_ms >= segment.end_ms:
        return PaperResult(
            success=False,
            error="signal timestamp outside replay segment",
            signal=signal.side.value,
        )

    # The durable lifecycle uses the same values as EngineState.  Accept a
    # PaperLifecycleState-shaped object too, which is useful to persistence
    # fixtures and keeps this adapter read-only with respect to lifecycle.
    lifecycle_value = getattr(lifecycle, "state", lifecycle)
    if lifecycle == LifecycleState.PAUSED or lifecycle_value == LifecycleState.PAUSED:
        return PaperResult(
            success=False,
            error="lifecycle is PAUSED",
            signal=signal.side.value,
        )

    # HOLD is deliberately side-effect-free.
    if signal.side == DecisionSide.HOLD:
        return PaperResult(success=True, signal="HOLD", order_id=None, fill_id=None)

    # This slice only supports entry BUYs; exits remain on the existing SELL
    # orchestrator path where position lots are explicitly allocated.
    if signal.side == DecisionSide.SELL:
        return PaperResult(
            success=False,
            error="SELL without position not supported in this slice",
            signal="SELL",
        )

    order_id = f"ord-{signal.signal_id}"
    evaluation_id = f"eval-{signal.signal_id}"

    # Idempotency is checked before registration.  Support both the compact
    # replay persistence contract and the projected SQLiteStore read API.
    for existing_order in _adapter_orders(persistence):
        existing_evaluation = _adapter_order_value(existing_order, "evaluation_id")
        if existing_evaluation is None:
            existing_evaluation = _adapter_order_value(existing_order, "decision_id")
        if existing_evaluation != evaluation_id:
            continue
        fills = _adapter_order_value(existing_order, "fills", ()) or ()
        first_fill = fills[0] if fills else None
        fill_id = _adapter_order_value(existing_order, "fill_id")
        if fill_id is None and first_fill is not None:
            fill_id = _adapter_order_value(first_fill, "fill_id")
        if fill_id is None:
            # The SQLite order projection intentionally excludes the separate
            # fill journal; the fill identity is nevertheless deterministic.
            fill_id = f"fill-{signal.signal_id}"
        return PaperResult(
            success=True,
            signal="BUY",
            order_id=_adapter_order_value(existing_order, "order_id", order_id),
            fill_id=fill_id,
        )

    # Existing durable PAPER DTOs need account/session.  SQLite registration
    # requires PREPARED; compact persistence adapters may accept directly.
    account_id = getattr(risk_snapshot, "account_id", "acct-1")
    session_id = getattr(lifecycle, "paper_session_id", "session-1")
    save_order = getattr(persistence, "save_paper_order", None)
    if save_order is None:
        save_order = getattr(persistence, "register_paper_order", None)
    if save_order is None:
        raise AttributeError("persistence does not support paper order registration")
    order_status = (
        PaperOrderStatus.ACCEPTED
        if hasattr(persistence, "save_paper_order")
        else PaperOrderStatus.PREPARED
    )
    order = PaperOrder(
        order_id=order_id,
        client_order_id=f"client-{signal.signal_id}",
        paper_account_id=account_id,
        paper_session_id=session_id,
        environment="paper",
        symbol=signal.symbol,
        side=PaperOrderSide.BUY,
        order_type=PaperOrderType.LIMIT,
        requested_qty=quantity,
        price=entry_price,
        status=order_status,
        strategy_ref=strategy_ref,
        decision_id=evaluation_id,
        timestamp_ms=signal.timestamp_ms,
    )

    _run_adapter_io(save_order(order))

    commission = _adapter_commission(fee_snapshot, quantity, entry_price)
    fill = PaperFill(
        fill_id=f"fill-{signal.signal_id}",
        order_id=order_id,
        qty=quantity,
        price=entry_price,
        commission=commission,
        timestamp_ms=signal.timestamp_ms,
    )
    save_fill = getattr(persistence, "save_paper_fill", None)
    if save_fill is None:
        raise AttributeError("persistence does not support paper fill registration")
    _run_adapter_io(save_fill(fill))

    return PaperResult(
        success=True,
        signal="BUY",
        order_id=order_id,
        fill_id=fill.fill_id,
    )


__all__ = [
    "AbstractPaperPersistence",
    "ExecutionSnapshot",
    "FilterSnapshot",
    "LifecycleState",
    "OrchestratorResult",
    "PaperResult",
    "RiskSnapshot",
    "apply_signal_to_paper",
    "run_paper_cycle",
    "run_paper_cycle_sell",
]
