"""REST and persistence coverage for terminal UI preferences."""

from contextlib import asynccontextmanager
import asyncio
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.preferences import router
from src.persistence import sqlite_store as sqlite_store_module
from src.persistence.sqlite_store import SQLiteStore


@pytest.fixture
def client(tmp_path):
    db_path = tmp_path / "preferences.db"

    @asynccontextmanager
    async def lifespan(app):
        store = SQLiteStore(db_path)
        await store.initialize()
        app.state._sqlite_store = store
        try:
            yield
        finally:
            await store.close()

    app = FastAPI(lifespan=lifespan)
    app.include_router(router)
    with TestClient(app) as test_client:
        yield test_client, db_path


def _payload() -> dict:
    return {
        "schemaVersion": "1",
        "view": {
            "activeView": "terminal",
            "bottomTab": "balances",
            "interval": "1m",
        },
        "metadata": {
            "lastSaved": "2026-10-23T12:34:56Z",
            "schemaVersion": "1",
        },
    }


def test_valid_save_and_reload_round_trip(client):
    test_client, _ = client
    payload = _payload()

    response = test_client.post(
        "/api/preferences/ui", json={"preferences": payload, "revision": 0}
    )
    assert response.status_code in (200, 201)
    assert response.json() == {"status": "saved", "revision": 1}

    response = test_client.get("/api/preferences/ui")
    assert response.status_code == 200
    assert json.loads(response.json()["preferences"]) == payload
    assert response.json()["version"] == 1
    assert response.json()["revision"] == 1


def test_stale_write_returns_conflict(client):
    test_client, _ = client
    first = _payload()
    assert test_client.post(
        "/api/preferences/ui", json={"preferences": first, "revision": 0}
    ).status_code == 200

    stale = {**first, "view": {**first["view"], "interval": "5m"}}
    response = test_client.post(
        "/api/preferences/ui", json={"preferences": stale, "revision": 0}
    )
    assert response.status_code == 409
    assert response.json()["detail"] == "UI_PREFERENCES_CONFLICT"


def test_concurrent_saves_only_one_succeeds(client):
    test_client, _ = client
    initial = _payload()
    assert test_client.post(
        "/api/preferences/ui", json={"preferences": initial, "revision": 0}
    ).status_code == 200
    payloads = [
        {**initial, "view": {**initial["view"], "interval": "5m"}},
        {**initial, "view": {**initial["view"], "interval": "15m"}},
    ]

    def save(payload):
        return test_client.post(
            "/api/preferences/ui", json={"preferences": payload, "revision": 1}
        ).status_code

    with ThreadPoolExecutor(max_workers=2) as executor:
        statuses = list(executor.map(save, payloads))
    assert sorted(statuses) == [200, 409]


def test_invalid_contract_returns_400_and_keeps_existing_row(client):
    test_client, _ = client
    payload = _payload()
    assert test_client.post(
        "/api/preferences/ui", json={"preferences": payload, "revision": 0}
    ).status_code in (200, 201)

    invalid = {**payload, "view": {**payload["view"], "interval": "10m"}}
    response = test_client.post(
        "/api/preferences/ui", json={"preferences": invalid, "revision": 1}
    )
    assert response.status_code == 400
    assert response.json()["detail"] == "UI_PREFERENCES_INVALID_INTERVAL"

    response = test_client.get("/api/preferences/ui")
    assert json.loads(response.json()["preferences"]) == payload


@pytest.mark.parametrize(
    "timestamp",
    [
        "2026-99-99T99:99:99",
        "2026-02-30T12:34:56",
        "1999-12-31T23:59:59",
        "2101-01-01T00:00:00",
    ],
)
def test_invalid_iso8601_timestamp_returns_400(client, timestamp):
    test_client, _ = client
    payload = _payload()
    payload["metadata"]["lastSaved"] = timestamp

    response = test_client.post(
        "/api/preferences/ui", json={"preferences": payload, "revision": 0}
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "UI_PREFERENCES_INVALID_TIMESTAMP"


@pytest.mark.parametrize("body", [[], "preferences", 42])
def test_non_object_body_returns_400(body, client):
    test_client, _ = client

    response = test_client.post("/api/preferences/ui", json=body)

    assert response.status_code == 400
    assert response.json()["detail"] == "UI_PREFERENCES_INVALID_TYPE"


def test_null_body_returns_400(client):
    """json=None sends empty body → invalid JSON → 400."""
    test_client, _ = client
    response = test_client.post("/api/preferences/ui", json=None)
    assert response.status_code == 400


def test_v15_to_v16_ddl_failure_rolls_back_tables_version_and_data(tmp_path, monkeypatch):
    """A failed v15→v16 DDL sequence leaves the database unchanged."""
    db_path = tmp_path / "v15-migration.db"
    connection = sqlite3.connect(db_path)
    for table_name in sqlite_store_module._SCHEMA_V15_TABLES:
        connection.execute(f'CREATE TABLE "{table_name}" (marker TEXT)')
    connection.execute(
        'INSERT INTO "paper_lifecycle_reconciliations" (marker) VALUES (?)',
        ("preserved",),
    )
    connection.execute("PRAGMA user_version = 15")
    connection.commit()
    connection.close()

    monkeypatch.setattr(
        sqlite_store_module,
        "_UI_PREFERENCES_DDL_STATEMENTS",
        (
            "CREATE TABLE created_before_failure (marker TEXT)",
            "CREATE TABLE another_created_before_failure (marker TEXT)",
            "CREATE TABLE invalid_syntax (",
        ),
    )

    store = SQLiteStore(db_path)
    with pytest.raises(sqlite3.OperationalError):
        asyncio.run(store.initialize())

    connection = sqlite3.connect(db_path)
    tables = {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        )
    }
    version = connection.execute("PRAGMA user_version").fetchone()[0]
    preserved = connection.execute(
        'SELECT marker FROM "paper_lifecycle_reconciliations"'
    ).fetchone()
    connection.close()

    assert "created_before_failure" not in tables
    assert "another_created_before_failure" not in tables
    assert version == 15
    assert preserved == ("preserved",)


def test_missing_preferences_returns_404(client):
    test_client, _ = client
    response = test_client.get("/api/preferences/ui")
    assert response.status_code == 404
    assert response.json()["detail"] == "UI_PREFERENCES_NOT_FOUND"


def test_corrupt_json_is_returned_as_raw_text(client):
    test_client, db_path = client
    connection = sqlite3.connect(db_path)
    connection.execute(
        "INSERT OR REPLACE INTO ui_preferences "
        "(id, preferences, revision, version, created_at, updated_at) "
        "VALUES (1, ?, 1, 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)",
        ("not-json",),
    )
    connection.commit()
    connection.close()

    response = test_client.get("/api/preferences/ui")
    assert response.status_code == 200
    assert response.json()["preferences"] == "not-json"
    assert response.json()["revision"] == 1
    assert response.json()["version"] == 1
