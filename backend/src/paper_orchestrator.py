"""Minimal deterministic PAPER orchestration for the TEST_ONLY strategy."""

from __future__ import annotations

from decimal import Decimal
from typing import Sequence

from pydantic import BaseModel, ConfigDict

from src.models.paper_contracts import (
    LotAllocation,
    PaperOrder,
    PaperOrderSide,
    PaperOrderStatus,
    PaperOrderType,
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
from src.models.state import Candle
from src.persistence.sqlite_store import SQLiteStore
from src.strategies.test_only_keltner import (
    TEST_ONLY_OHLCV_FIXTURE,
    evaluate_test_only_keltner,
)
from src.paper_execution_integration import (
    _deterministic_fill_id,
    evaluate_and_apply_fill,
)


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
    market_exec_snapshot: PaperMarketExecutionSnapshot,
    market_capacity_snapshot: PaperMarketCapacitySnapshot,
    fee_snapshot: PaperFeeSnapshot,
    candles: Sequence[Candle] = TEST_ONLY_OHLCV_FIXTURE[:7],
    evaluated_at_ms: int = 0,
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
        durable_order = await store.load_paper_order(order.order_id)
        if durable_order is None:
            durable_order = await store.register_paper_order(order)
        elif durable_order.order_id != order.order_id:
            raise ValueError("durable order identity mismatch")

        if durable_order.status == PaperOrderStatus.FILLED:
            evaluation = await store.load_paper_fill_evaluation(evaluation_id)
            if evaluation is None:
                raise RuntimeError("filled durable order has no evaluation record")
            return OrchestratorResult(
                success=True,
                signal="BUY",
                order_id=durable_order.order_id,
                fill_id=evaluation.fill_candidate_fill_id or _deterministic_fill_id(evaluation_id),
                evaluation_id=evaluation_id,
                risk_result=risk_result.code,
                filter_result=filter_result.code,
            )

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
            market_exec_snapshot,
            market_capacity_snapshot,
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
            symbol=symbol,
            capture_bar_open_time_ms=exit_timestamp_ms,
            capture_snapshot_timestamp_ms=exit_timestamp_ms,
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


__all__ = ["OrchestratorResult", "run_paper_cycle", "run_paper_cycle_sell"]
