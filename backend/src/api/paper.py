"""Read-only PAPER account, order, fill, and activity projections."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request

from src.persistence.sqlite_store import SQLiteStore


router = APIRouter(
    prefix="/api/paper/accounts/{account_id}",
    tags=["paper-read"],
)


def _store_for(request: Request) -> SQLiteStore:
    store = getattr(request.app.state, "_sqlite_store", None)
    if not isinstance(store, SQLiteStore):
        raise HTTPException(
            status_code=503,
            detail={
                "code": "PAPER_STORE_UNAVAILABLE",
                "available": False,
            },
        )
    return store


async def _require_account(store: SQLiteStore, account_id: str) -> None:
    if not account_id.strip() or await store.load_paper_account_opening(account_id) is None:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "PAPER_ACCOUNT_NOT_FOUND",
                "account_id": account_id,
                "available": False,
            },
        )


async def _require_session(
    store: SQLiteStore, account_id: str, session_id: str | None
) -> None:
    if session_id is None or not session_id.strip():
        raise HTTPException(
            status_code=404,
            detail={
                "code": "PAPER_SESSION_REQUIRED",
                "account_id": account_id,
                "available": False,
            },
        )
    if await store.get_paper_lifecycle_snapshot(account_id, session_id) is not None:
        return
    if await store.get_paper_orders(account_id, session_id):
        return
    if await store.get_paper_fills(account_id, session_id):
        return
    if any(
        lot["paper_session_id"] == session_id
        for lot in await store.get_paper_position_lots(account_id)
    ):
        return
    raise HTTPException(
        status_code=404,
        detail={
            "code": "PAPER_SESSION_NOT_FOUND",
            "account_id": account_id,
            "session_id": session_id,
            "available": False,
        },
    )


@router.get("/balances")
async def get_balances(account_id: str, request: Request) -> dict[str, Any]:
    store = _store_for(request)
    await _require_account(store, account_id)
    balances = await store.get_paper_account_balances(account_id)
    # The opening check above makes this defensive branch unreachable unless a
    # concurrent external process removes/corrupts the account.
    if balances is None:
        raise HTTPException(status_code=404, detail={"code": "PAPER_ACCOUNT_NOT_FOUND"})
    return {
        "account_id": account_id,
        "environment": "paper",
        "balances": balances,
    }


@router.get("/positions")
async def get_positions(account_id: str, request: Request) -> dict[str, Any]:
    store = _store_for(request)
    await _require_account(store, account_id)
    return {
        "account_id": account_id,
        "environment": "paper",
        "positions": await store.get_paper_position_lots(account_id),
    }


@router.get("/orders")
async def get_orders(
    account_id: str,
    request: Request,
    session_id: str | None = Query(default=None),
) -> dict[str, Any]:
    store = _store_for(request)
    await _require_account(store, account_id)
    if session_id is not None:
        await _require_session(store, account_id, session_id)
    return {
        "account_id": account_id,
        "environment": "paper",
        "session_id": session_id,
        "orders": await store.get_paper_orders(account_id, session_id),
    }


@router.get("/fills")
async def get_fills(
    account_id: str,
    request: Request,
    session_id: str | None = Query(default=None),
) -> dict[str, Any]:
    store = _store_for(request)
    await _require_account(store, account_id)
    if session_id is not None:
        await _require_session(store, account_id, session_id)
    return {
        "account_id": account_id,
        "environment": "paper",
        "session_id": session_id,
        "fills": await store.get_paper_fills(account_id, session_id),
    }


@router.get("/lifecycle")
async def get_lifecycle(
    account_id: str,
    request: Request,
    session_id: str | None = Query(default=None),
) -> dict[str, Any]:
    store = _store_for(request)
    await _require_account(store, account_id)
    await _require_session(store, account_id, session_id)
    # _require_session guarantees a non-None lifecycle only for lifecycle
    # records; lifecycle itself must still be explicit for this endpoint.
    assert session_id is not None
    snapshot = await store.get_paper_lifecycle_snapshot(account_id, session_id)
    if snapshot is None:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "PAPER_LIFECYCLE_NOT_FOUND",
                "account_id": account_id,
                "session_id": session_id,
                "available": False,
            },
        )
    return snapshot


@router.get("/activity")
async def get_activity(
    account_id: str,
    request: Request,
    session_id: str | None = Query(default=None),
) -> dict[str, Any]:
    store = _store_for(request)
    await _require_account(store, account_id)
    if session_id is not None:
        await _require_session(store, account_id, session_id)
    return {
        "account_id": account_id,
        "environment": "paper",
        "session_id": session_id,
        "activity": await store.get_paper_activity(account_id, session_id),
    }
