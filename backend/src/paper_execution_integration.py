"""Durable orchestration for pure PAPER fill-candidate evaluation."""

from __future__ import annotations

import hashlib
import json
from typing import Optional

from src.models.paper_contracts import (
    Commission,
    LotAllocation,
    PaperFill,
    PaperOrder,
    PaperOrderSide,
)
from src.models.paper_execution import (
    PaperExecutionPolicy,
    PaperFeeSnapshot,
    PaperFillCandidateResult,
    PaperFillEvaluationRecord,
    PaperMarketCapacitySnapshot,
    PaperMarketExecutionSnapshot,
    PaperModelledExecution,
    PaperProtectionSnapshot,
    PaperReservationSnapshot,
    RESULT_FILL_CANDIDATE,
    evaluate_fill_candidate,
    _ExecutionDTO,
)
from src.persistence.sqlite_store import SQLiteStore


def _deterministic_fill_id(evaluation_id: str) -> str:
    return "eval-" + hashlib.sha256(
        f"paper-fill-evaluation.v1:{evaluation_id}".encode()
    ).hexdigest()


class PaperFillIntegrationResult(_ExecutionDTO):
    evaluation_id: str
    status_code: str
    fill_id: Optional[str] = None
    applied: bool = False
    evaluation_persisted: bool = False
    conflict: Optional[str] = None


class PaperFillIntegrationConflictError(Exception):
    """Raised when a different payload uses an existing evaluation identity."""


def _record_from_result(
    evaluation_id: str,
    order: PaperOrder,
    result: PaperFillCandidateResult,
    evaluated_at_ms: int,
) -> PaperFillEvaluationRecord:
    is_candidate = result.status_code == RESULT_FILL_CANDIDATE
    if is_candidate and (
        result.fill_qty is None
        or result.fill_price is None
        or result.commission is None
    ):
        raise ValueError("fill_candidate result requires quantity, price, and commission")
    return PaperFillEvaluationRecord(
        evaluation_id=evaluation_id,
        order_id=order.order_id,
        account_id=order.paper_account_id,
        paper_session_id=order.paper_session_id,
        environment=order.environment,
        status_code=result.status_code,
        result_json=result.model_dump_json(),
        evaluated_at_ms=evaluated_at_ms,
        fill_candidate_fill_id=(
            _deterministic_fill_id(evaluation_id) if is_candidate else None
        ),
        fill_candidate_fill_qty=(str(result.fill_qty) if is_candidate else None),
        fill_candidate_fill_price=(str(result.fill_price) if is_candidate else None),
        fill_candidate_commission_json=(
            result.commission.model_dump_json()
            if is_candidate and result.commission is not None
            else None
        ),
    )


def _fill_allocations(order: PaperOrder, fill_qty) -> tuple[LotAllocation, ...]:
    if order.side != PaperOrderSide.SELL:
        return ()
    if not order.lot_allocations:
        raise ValueError("SELL fill candidate requires explicit lot allocations")
    remaining = fill_qty
    allocations: list[LotAllocation] = []
    for allocation in order.lot_allocations:
        if remaining <= 0:
            break
        qty = min(allocation.qty, remaining)
        if qty > 0:
            allocations.append(allocation.model_copy(update={"qty": qty}))
            remaining -= qty
    if remaining != 0:
        raise ValueError("SELL fill candidate exceeds explicit lot allocations")
    return tuple(allocations)


def _fill_from_result(
    order: PaperOrder,
    result: PaperFillCandidateResult,
    fill_id: str,
    evaluated_at_ms: int,
) -> PaperFill:
    if result.fill_qty is None or result.fill_price is None:
        raise ValueError("fill_candidate result requires quantity and price")
    commission: Commission | None = result.commission
    if commission is None:
        raise ValueError("fill_candidate result requires a known commission")
    return PaperFill(
        fill_id=fill_id,
        order_id=order.order_id,
        qty=result.fill_qty,
        price=result.fill_price,
        timestamp_ms=evaluated_at_ms,
        commission=commission,
        lot_allocations=_fill_allocations(order, result.fill_qty),
    )


async def _resolve_candidate(
    store: SQLiteStore,
    result: PaperFillCandidateResult,
    record: PaperFillEvaluationRecord,
    evaluated_at_ms: int,
) -> PaperFillIntegrationResult:
    fill_id = record.fill_candidate_fill_id
    if fill_id != _deterministic_fill_id(record.evaluation_id):
        raise PaperFillIntegrationConflictError(
            f"evaluation {record.evaluation_id} has a non-deterministic fill_id"
        )
    durable_order = await store.load_paper_order(record.order_id)
    if durable_order is None:
        raise PaperFillIntegrationConflictError(
            f"evaluation {record.evaluation_id} references missing order "
            f"{record.order_id}"
        )
    if (
        durable_order.paper_account_id != record.account_id
        or durable_order.paper_session_id != record.paper_session_id
        or durable_order.environment != record.environment
    ):
        raise PaperFillIntegrationConflictError(
            f"evaluation {record.evaluation_id} order identity does not match "
            "the durable order"
        )
    fill = _fill_from_result(durable_order, result, fill_id, evaluated_at_ms)

    stored_fill = await store.load_paper_fill(fill_id)
    if stored_fill is not None and stored_fill != fill:
        raise PaperFillIntegrationConflictError(
            f"fill {fill_id} already exists with different content"
        )
    current_order = await store.load_paper_order(record.order_id)
    if current_order is not None and any(
        applied_fill.fill_id == fill_id for applied_fill in current_order.fills
    ):
        return PaperFillIntegrationResult(
            evaluation_id=record.evaluation_id,
            status_code=record.status_code,
            fill_id=fill_id,
            applied=True,
            evaluation_persisted=True,
        )

    if stored_fill is None:
        await store.save_paper_fill(fill)

    try:
        await store.apply_paper_fill(fill_id)
    except BaseException as original_error:
        # A commit acknowledgement can be lost. Inspect durable order state;
        # retry only when the applied fill is not present there.
        try:
            recovered_order = await store.load_paper_order(record.order_id)
        except BaseException:
            raise original_error
        if recovered_order is not None and any(
            applied_fill.fill_id == fill_id for applied_fill in recovered_order.fills
        ):
            return PaperFillIntegrationResult(
                evaluation_id=record.evaluation_id,
                status_code=record.status_code,
                fill_id=fill_id,
                applied=True,
                evaluation_persisted=True,
            )
        raise

    return PaperFillIntegrationResult(
        evaluation_id=record.evaluation_id,
        status_code=record.status_code,
        fill_id=fill_id,
        applied=True,
        evaluation_persisted=True,
    )


async def evaluate_and_apply_fill(
    store: SQLiteStore,
    evaluation_id: str,
    order: PaperOrder,
    reservation_snapshot: PaperReservationSnapshot,
    exec_policy_snapshot: PaperExecutionPolicy,
    market_exec_snapshot: PaperMarketExecutionSnapshot,
    market_capacity_snapshot: PaperMarketCapacitySnapshot,
    fee_snapshot: Optional[PaperFeeSnapshot] = None,
    protection_snapshot: Optional[PaperProtectionSnapshot] = None,
    modelled_execution: Optional[PaperModelledExecution] = None,
    evaluated_at_ms: int = 0,
) -> PaperFillIntegrationResult:
    """Evaluate, durably record, and (only for candidates) apply one fill."""
    if not isinstance(evaluation_id, str) or not evaluation_id.strip():
        raise ValueError("evaluation_id must be a non-empty string")

    existing = await store.load_paper_fill_evaluation(evaluation_id)

    if existing is not None and (
        order.order_id != existing.order_id
        or order.paper_account_id != existing.account_id
        or order.paper_session_id != existing.paper_session_id
    ):
        raise PaperFillIntegrationConflictError(
            f"evaluation {evaluation_id} order/account/session mismatch: "
            f"expected order={existing.order_id}, account={existing.account_id}, "
            f"session={existing.paper_session_id}; "
            f"got order={order.order_id}, account={order.paper_account_id}, "
            f"session={order.paper_session_id}"
        )

    # An already applied candidate is authoritative; do not re-evaluate a now
    # terminal order during an idempotent retry.
    if existing is not None and existing.status_code == RESULT_FILL_CANDIDATE:
        if existing.fill_candidate_fill_id is None:
            raise PaperFillIntegrationConflictError(
                f"evaluation {evaluation_id} is missing its deterministic fill_id"
            )
        # result_json stores Decimals as strings (SQLite TEXT columns);
        # convert numeric fields back to Decimal before validation.
        parsed = json.loads(existing.result_json)
        for _dec_key in ("fill_qty", "fill_price", "modelled_execution_price", "fee_quantity"):
            if parsed.get(_dec_key) is not None and isinstance(parsed[_dec_key], str):
                from decimal import Decimal
                parsed[_dec_key] = Decimal(parsed[_dec_key])
        persisted_result = PaperFillCandidateResult.model_validate(parsed)
        current_order = await store.load_paper_order(existing.order_id)
        if current_order is not None and any(
            applied_fill.fill_id == existing.fill_candidate_fill_id
            for applied_fill in current_order.fills
        ):
            return PaperFillIntegrationResult(
                evaluation_id=evaluation_id,
                status_code=existing.status_code,
                fill_id=existing.fill_candidate_fill_id,
                applied=True,
                evaluation_persisted=True,
            )
        # The evaluation is durable but its fill may only be journalized. The
        # persisted result is used as the retry authority.
        return await _resolve_candidate(
            store, persisted_result, existing, existing.evaluated_at_ms
        )

    result = evaluate_fill_candidate(
        order,
        reservation_snapshot,
        exec_policy_snapshot,
        market_exec_snapshot,
        market_capacity_snapshot,
        fee_snapshot,
        protection_snapshot,
        modelled_execution,
    )
    expected = _record_from_result(evaluation_id, order, result, evaluated_at_ms)

    if existing is not None:
        if existing != expected:
            raise PaperFillIntegrationConflictError(
                f"evaluation {evaluation_id} already exists with different content"
            )
        if result.status_code != RESULT_FILL_CANDIDATE:
            return PaperFillIntegrationResult(
                evaluation_id=evaluation_id,
                status_code=result.status_code,
                evaluation_persisted=True,
            )

    else:
        await store.persist_fill_evaluation(
            evaluation_id=expected.evaluation_id,
            order_id=expected.order_id,
            account_id=expected.account_id,
            paper_session_id=expected.paper_session_id,
            status_code=expected.status_code,
            result_json=expected.result_json,
            evaluated_at_ms=expected.evaluated_at_ms,
            fill_candidate_fill_id=expected.fill_candidate_fill_id,
            fill_candidate_fill_qty=expected.fill_candidate_fill_qty,
            fill_candidate_fill_price=expected.fill_candidate_fill_price,
            fill_candidate_commission_json=expected.fill_candidate_commission_json,
        )

    if result.status_code != RESULT_FILL_CANDIDATE:
        return PaperFillIntegrationResult(
            evaluation_id=evaluation_id,
            status_code=result.status_code,
            evaluation_persisted=True,
        )
    return await _resolve_candidate(store, result, expected, evaluated_at_ms)
