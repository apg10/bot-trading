"""Focused HTTP coverage for the read-only PAPER router."""

from __future__ import annotations

import asyncio
from decimal import Decimal
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.paper import router
from src.models.paper_contracts import AssetBalance, PaperAccountOpening
from src.persistence.sqlite_store import SQLiteStore


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def test_paper_read_api_has_explicit_not_found_and_string_balances(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "api.db")
    _run(store.initialize())
    _run(store.open_paper_account(PaperAccountOpening(
        account_id="acct-api",
        balances=(AssetBalance(
            asset="USD", total=Decimal("10"), available=Decimal("10"), reserved=Decimal("0")
        ),),
    )))
    app = FastAPI()
    app.include_router(router)
    app.state._sqlite_store = store
    with TestClient(app) as client:
        response = client.get("/api/paper/accounts/acct-api/balances")
        assert response.status_code == 200
        assert response.json()["balances"][0]["total"] == "10"
        assert response.json()["balances"][0]["reserved"] == "0"
        assert client.get("/api/paper/accounts/unknown/balances").status_code == 404
        assert client.get("/api/paper/accounts/acct-api/lifecycle").status_code == 404
    _run(store.close())
