"""Read-only laboratory projections for normalized experiment results."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request

from src.models.experiment_contracts import ExperimentRef
from src.persistence.sqlite_store import (
    NormalizedExperimentResultIntegrityError,
    NormalizedExperimentResultStateError,
    SQLiteStore,
    StoreClosedError,
    StoreCorruptionError,
)
from src.models.laboratory_metrics import (
    MetricsInconsistencyError,
    MetricsInsufficientDataError,
    build_capital_curve,
    build_cost_breakdown,
)


router = APIRouter(prefix="/api/laboratory", tags=["laboratory-read"])


def _store_for(request: Request) -> SQLiteStore:
    """Return the lifespan-owned store or report an unavailable laboratory."""
    store = getattr(request.app.state, "_sqlite_store", None)
    if not isinstance(store, SQLiteStore):
        raise HTTPException(
            status_code=503,
            detail={
                "code": "LABORATORY_STORE_UNAVAILABLE",
                "available": False,
            },
        )
    return store


def _corruption(exc: Exception) -> HTTPException:
    """Expose corruption as an explicit API error without leaking raw details."""
    return HTTPException(
        status_code=500,
        detail={
            "code": "LABORATORY_DATA_CORRUPTION",
            "available": False,
            "message": str(exc),
        },
    )


def _store_unavailable(exc: Exception) -> HTTPException:
    return HTTPException(
        status_code=503,
        detail={
            "code": "LABORATORY_STORE_UNAVAILABLE",
            "available": False,
            "message": str(exc),
        },
    )


async def _metrics_inputs(
    store: SQLiteStore, account_id: str, session_id: str, quote_asset: str
):
    if not account_id.strip() or await store.load_paper_account_opening(account_id) is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "LABORATORY_ACCOUNT_NOT_FOUND", "account_id": account_id, "available": False},
        )
    if not session_id.strip():
        raise HTTPException(
            status_code=404,
            detail={"code": "LABORATORY_SESSION_NOT_FOUND", "account_id": account_id, "session_id": session_id, "available": False},
        )
    orders = await store.get_paper_orders(account_id, session_id)
    fills = await store.get_paper_fills(account_id, session_id)
    evaluations = await store.get_paper_fill_evaluations(account_id, session_id)
    if not orders and not fills and not evaluations:
        raise HTTPException(
            status_code=404,
            detail={"code": "LABORATORY_SESSION_NOT_FOUND", "account_id": account_id, "session_id": session_id, "available": False},
        )
    if not quote_asset.strip():
        raise HTTPException(
            status_code=409,
            detail={"code": "LABORATORY_INSUFFICIENT_DATA", "reason": "quote_asset is required", "available": False},
        )
    opening = await store.load_paper_account_opening(account_id)
    assert opening is not None
    initial = next((balance.total for balance in opening.balances if balance.asset == quote_asset), None)
    if initial is None:
        raise HTTPException(
            status_code=409,
            detail={"code": "LABORATORY_QUOTE_ASSET_INCONSISTENT", "quote_asset": quote_asset, "available": False},
        )
    return initial, fills, {item["order_id"]: item for item in orders}, evaluations


def _metrics_error(exc: Exception) -> HTTPException:
    return HTTPException(
        status_code=409,
        detail={
            "code": "LABORATORY_INSUFFICIENT_DATA"
            if isinstance(exc, MetricsInsufficientDataError)
            else "LABORATORY_DATA_INCONSISTENCY",
            "reason": str(exc),
            "available": False,
        },
    )


async def _run_metrics(request: Request, account_id: str, session_id: str, quote_asset: str):
    store = _store_for(request)
    try:
        return store, await _metrics_inputs(store, account_id, session_id, quote_asset)
    except HTTPException:
        raise
    except (StoreClosedError, RuntimeError) as exc:
        raise _store_unavailable(exc) from exc
    except (StoreCorruptionError, ValueError) as exc:
        raise _metrics_error(exc) from exc


@router.get("/results/{result_id}")
async def get_normalized_result(result_id: str, request: Request) -> dict:
    """Return one persisted normalized experiment result without transformation."""
    store = _store_for(request)
    try:
        result = await store.load_normalized_experiment_result(result_id)
    except (NormalizedExperimentResultIntegrityError, StoreCorruptionError) as exc:
        raise _corruption(exc) from exc
    except StoreClosedError as exc:
        raise _store_unavailable(exc) from exc
    except RuntimeError as exc:
        # Includes an uninitialized/closed persistence lifecycle.
        raise _store_unavailable(exc) from exc
    except ValueError as exc:
        # Validation failures while reading persisted data are corruption, not
        # a client-side request error.
        raise _corruption(exc) from exc

    if result is None:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "LABORATORY_RESULT_NOT_FOUND",
                "result_id": result_id,
                "available": False,
            },
        )
    return result.model_dump(mode="json")


@router.get("/comparisons/{experiment_id}/{experiment_version}")
async def get_experiment_comparison(
    experiment_id: str,
    experiment_version: str,
    request: Request,
    comparison_id: str = Query(...),
    comparison_version: str = Query(...),
) -> dict:
    """Return the persisted comparison projection for one exact experiment ref."""
    store = _store_for(request)
    experiment_ref = ExperimentRef(
        experiment_id=experiment_id,
        experiment_version=experiment_version,
    )
    try:
        report = await store.load_experiment_comparison(
            experiment_ref,
            comparison_id,
            comparison_version,
        )
    except NormalizedExperimentResultStateError as exc:
        if "at least three" in str(exc):
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "LABORATORY_COMPARISON_REQUIRES_THREE_VARIANTS",
                    "experiment_id": experiment_id,
                    "experiment_version": experiment_version,
                    "available": False,
                },
            ) from exc
        raise _corruption(exc) from exc
    except (NormalizedExperimentResultIntegrityError, StoreCorruptionError) as exc:
        raise _corruption(exc) from exc
    except StoreClosedError as exc:
        raise _store_unavailable(exc) from exc
    except RuntimeError as exc:
        raise _store_unavailable(exc) from exc
    except ValueError as exc:
        raise _corruption(exc) from exc

    if report is None:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "LABORATORY_COMPARISON_NOT_FOUND",
                "experiment_id": experiment_id,
                "experiment_version": experiment_version,
                "available": False,
            },
        )
    return report.model_dump(mode="json")


@router.get("/capital-curves/{account_id}/{session_id}")
async def get_capital_curve(
    account_id: str,
    session_id: str,
    request: Request,
    quote_asset: str = Query(default="USDT"),
) -> dict:
    """Return the cash-balance curve derived from applied PAPER fills."""
    store, (initial, fills, orders, _evaluations) = await _run_metrics(
        request, account_id, session_id, quote_asset
    )
    try:
        report = build_capital_curve(
            account_id, session_id, quote_asset, initial, fills, orders
        )
    except (MetricsInconsistencyError, MetricsInsufficientDataError) as exc:
        raise _metrics_error(exc) from exc
    if report.status != "available":
        raise HTTPException(
            status_code=409,
            detail={"code": "LABORATORY_INSUFFICIENT_DATA", "reason": report.reason, "available": False},
        )
    return report.model_dump(mode="json")


@router.get("/cost-breakdowns/{account_id}/{session_id}")
async def get_cost_breakdown(
    account_id: str,
    session_id: str,
    request: Request,
    quote_asset: str = Query(default="USDT"),
) -> dict:
    """Return per-fill commission and explicitly modelled slippage."""
    store, (_initial, fills, orders, evaluations) = await _run_metrics(
        request, account_id, session_id, quote_asset
    )
    try:
        report = build_cost_breakdown(
            account_id, session_id, quote_asset, fills, orders, evaluations
        )
    except (MetricsInconsistencyError, MetricsInsufficientDataError) as exc:
        raise _metrics_error(exc) from exc
    if report.status != "available":
        raise HTTPException(
            status_code=409,
            detail={"code": "LABORATORY_INSUFFICIENT_DATA", "reason": report.reason, "available": False},
        )
    return report.model_dump(mode="json")


@router.get("/inspector/{account_id}/{session_id}")
async def get_operation_inspector(
    account_id: str, session_id: str, request: Request
) -> dict:
    """Return the persisted causal decision → order → fill projection.

    Historical PAPER rows without the v18 audit journal are intentionally
    reported as insufficient data; this endpoint never reconstructs decisions
    from orders or fills.
    """
    store = _store_for(request)
    try:
        if not account_id.strip() or await store.load_paper_account_opening(account_id) is None:
            raise HTTPException(
                status_code=404,
                detail={"code": "LABORATORY_ACCOUNT_NOT_FOUND", "account_id": account_id, "available": False},
            )
        if not session_id.strip():
            raise HTTPException(
                status_code=404,
                detail={"code": "LABORATORY_SESSION_NOT_FOUND", "account_id": account_id, "session_id": session_id, "available": False},
            )
        audits = await store.load_audit_decisions(account_id, session_id)
        orders = await store.get_paper_orders(account_id, session_id)
        fills = await store.get_paper_fills(account_id, session_id)
        lifecycle = await store.get_paper_lifecycle_snapshot(account_id, session_id)
    except HTTPException:
        raise
    except (StoreClosedError, RuntimeError) as exc:
        raise _store_unavailable(exc) from exc
    except (StoreCorruptionError, ValueError) as exc:
        raise _corruption(exc) from exc

    if not audits and not orders and not fills and lifecycle is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "LABORATORY_SESSION_NOT_FOUND", "account_id": account_id, "session_id": session_id, "available": False},
        )

    order_by_decision: dict[str, list[dict]] = {}
    for order in orders:
        decision_id = order.get("decision_id")
        if isinstance(decision_id, str):
            order_by_decision.setdefault(decision_id, []).append(order)
    fills_by_order: dict[str, list[dict]] = {}
    for fill in fills:
        order_id = fill.get("order_id")
        if isinstance(order_id, str):
            fills_by_order.setdefault(order_id, []).append(fill)

    decisions: list[dict] = []
    candles: list[dict] = []
    for decision, candle in audits:
        decision_payload = decision.model_dump(mode="json")
        candle_payload = candle.model_dump(mode="json")
        linked_orders = order_by_decision.get(decision.decision_id, [])
        linked_fills = [
            fill
            for order in linked_orders
            for fill in fills_by_order.get(order.get("order_id"), [])
        ]
        decisions.append({
            **decision_payload,
            "candle": candle_payload,
            "orders": linked_orders,
            "fills": linked_fills,
        })
        candles.append(candle_payload)

    status = "available" if audits else "insufficient_data"
    return {
        "schema_version": "operation_inspector.v1",
        "account_id": account_id,
        "session_id": session_id,
        "status": status,
        "decisions": decisions,
        "candles": candles,
        "orders": orders,
        "fills": fills,
        "reason": None if audits else "causal audit data is not available for this historical session",
    }
