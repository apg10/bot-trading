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
