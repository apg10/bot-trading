"""Lifecycle tests for SQLiteStore integration in FastAPI lifespan (ACT-P-001 microtask).

All tests use tmp_path and a fake/monkeypatched MarketEngine; zero external services.
No test touches ``backend/data/trading_bot.sqlite3``.
"""

from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from src.persistence.sqlite_store import _SCHEMA_V9_TABLES, _SCHEMA_V10_TABLES, _SCHEMA_V12_TABLES

# ── Fake MarketEngine that mimics the real interface without external calls ─────


class _FakeMarketEngine:
    """Minimal fake matching the public surface used by the lifespan."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self.started = asyncio.Event()
        self._stopped = False

    async def start(self, symbol: str) -> None:
        self.started.set()

    async def stop(self) -> None:
        self._stopped = True

    def snapshot(self) -> dict[str, Any] | None:
        return {"connected": True}


# ── Helpers ─────────────────────────────────────────────────────────────────────


def _run(coro: Any) -> Any:
    """Run an async coroutine synchronously."""
    return asyncio.run(coro)


def _make_app(monkeypatch: pytest.MonkeyPatch, fake_engine_cls: type[_FakeMarketEngine]) -> FastAPI:
    """Build the real FastAPI app with MarketEngine monkeypatched."""
    # Patch before any import that caches MarketEngine
    import src.api.main as main_module

    monkeypatch.setattr(main_module, "MarketEngine", fake_engine_cls)

    # Re-import to pick up the patch (app is module-level singleton)
    # We rely on the fact that TestClient re-enters lifespan each time;
    # the module-level `app` object is reused but lifespan runs fresh.
    from src.api.main import app as real_app

    return real_app


# ── 1. Store initialized inside lifespan, temp DB has current user_version=10 ───


def test_lifespan_store_initialized_with_user_version_10(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Inside the lifespan the store is initialized at current schema version 10."""
    db_path = tmp_path / "lifecycle.db"
    monkeypatch.setenv("TRADING_DB_PATH", str(db_path))

    app = _make_app(monkeypatch, _FakeMarketEngine)

    with TestClient(app) as client:
        # Health confirms lifespan ran and engine is up
        resp = client.get("/health")
        assert resp.status_code == 200

        # Verify the store's DB has the current schema version.
        conn = sqlite3.connect(str(db_path))
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        conn.close()
        assert version == 12


# ── 2. Normal shutdown: store closed and reference removed ──────────────────────


def test_normal_shutdown_closes_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """On normal shutdown the store is closed and app.state._sqlite_store is cleared."""
    db_path = tmp_path / "shutdown.db"
    monkeypatch.setenv("TRADING_DB_PATH", str(db_path))

    # Track whether close was called
    _close_called = []

    class _TrackingFakeEngine(_FakeMarketEngine):
        pass

    app = _make_app(monkeypatch, _TrackingFakeEngine)

    # Patch the SQLiteStore.close to track calls
    from src.persistence import sqlite_store as store_module

    original_close = store_module.SQLiteStore.close

    async def _tracked_close(self: Any) -> None:
        _close_called.append(True)
        return await original_close(self)

    monkeypatch.setattr(store_module.SQLiteStore, "close", _tracked_close)

    with TestClient(app):
        pass  # lifespan enters and exits normally

    assert len(_close_called) == 1, "SQLiteStore.close should be called exactly once on shutdown"


def test_lifespan_removes_store_reference_after_shutdown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """After normal shutdown the _sqlite_store reference is removed from app.state."""
    db_path = tmp_path / "ref_clear.db"
    monkeypatch.setenv("TRADING_DB_PATH", str(db_path))

    app = _make_app(monkeypatch, _FakeMarketEngine)

    with TestClient(app):
        # During lifespan the store should be present
        assert hasattr(app.state, "_sqlite_store")
        assert getattr(app.state, "_sqlite_store") is not None

    # After exiting the client context (lifespan fully exited)
    assert getattr(app.state, "_sqlite_store", "MISSING") is None


# ── 3. Startup failure after opening store: cleanup closes it ───────────────────


def test_startup_failure_after_store_opens_closes_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If startup fails after the store opens, cleanup still closes the store."""
    db_path = tmp_path / "startup_fail.db"
    monkeypatch.setenv("TRADING_DB_PATH", str(db_path))

    _close_calls = []

    class _FakeEngineThatFailsStart(_FakeMarketEngine):
        async def start(self, symbol: str) -> None:
            # Simulate a startup failure after engine creation
            raise RuntimeError("simulated startup failure")

    app = _make_app(monkeypatch, _FakeEngineThatFailsStart)

    from src.persistence import sqlite_store as store_module

    original_close = store_module.SQLiteStore.close

    async def _track_close(self: Any) -> None:
        _close_calls.append(True)
        return await original_close(self)

    monkeypatch.setattr(store_module.SQLiteStore, "close", _track_close)

    # Preserve the existing startup contract: engine.start failure aborts startup.
    with pytest.raises(RuntimeError, match="simulated startup failure"):
        with TestClient(app):
            pass

    assert len(_close_calls) == 1, "Store must be closed even when startup fails"


def test_startup_error_is_preserved_when_engine_shutdown_also_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Cleanup failure must not replace the original MarketEngine startup error."""
    db_path = tmp_path / "startup_and_shutdown_fail.db"
    monkeypatch.setenv("TRADING_DB_PATH", str(db_path))

    close_calls: list[bool] = []

    class _FakeEngineFailsStartAndStop(_FakeMarketEngine):
        async def start(self, symbol: str) -> None:
            raise RuntimeError("original startup failure")

        async def stop(self) -> None:
            raise RuntimeError("secondary engine shutdown failure")

    app = _make_app(monkeypatch, _FakeEngineFailsStartAndStop)

    from src.persistence import sqlite_store as store_module

    original_close = store_module.SQLiteStore.close

    async def _tracked_close(self: Any) -> None:
        close_calls.append(True)
        await original_close(self)

    monkeypatch.setattr(store_module.SQLiteStore, "close", _tracked_close)

    with pytest.raises(RuntimeError, match="original startup failure"):
        with TestClient(app):
            pass

    assert close_calls == [True]
    assert getattr(app.state, "_sqlite_store", None) is None
    assert getattr(app.state, "market_engine", None) is None


# ── 4. Store initialization failure: MarketEngine never starts, no partial state ─


def test_store_init_failure_prevents_engine_start(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """If store initialization/migration fails, MarketEngine is never created."""
    db_path = tmp_path / "init_fail.db"
    monkeypatch.setenv("TRADING_DB_PATH", str(db_path))

    # Pre-seed the DB with a wrong schema version so initialize() raises
    conn = sqlite3.connect(str(db_path))
    conn.executescript("""
        CREATE TABLE sessions (session_id TEXT PRIMARY KEY);
        PRAGMA user_version = 99;
    """)
    conn.close()

    engine_created = []

    class _TrackingEngine(_FakeMarketEngine):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            engine_created.append(True)
            super().__init__(*args, **kwargs)

    app = _make_app(monkeypatch, _TrackingEngine)

    with pytest.raises(Exception):  # initialize raises SchemaVersionMismatchError
        with TestClient(app):
            pass

    assert len(engine_created) == 0, "MarketEngine must not be created when store init fails"


def test_no_partial_state_on_store_init_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """When store init fails, no partial app.state._sqlite_store remains."""
    db_path = tmp_path / "partial.db"
    monkeypatch.setenv("TRADING_DB_PATH", str(db_path))

    # Pre-seed wrong version
    conn = sqlite3.connect(str(db_path))
    conn.executescript("""
        CREATE TABLE sessions (session_id TEXT PRIMARY KEY);
        PRAGMA user_version = 99;
    """)
    conn.close()

    app = _make_app(monkeypatch, _FakeMarketEngine)

    with pytest.raises(Exception):
        with TestClient(app):
            pass

    # After the exception, no partial store reference should remain
    assert getattr(app.state, "_sqlite_store", "MISSING") is None


# ── 5. Reopening same file preserves data, no duplicate migration ───────────────


def test_reopen_preserves_data_no_duplicate_migration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A second open of the same file preserves data and doesn't repeat migration."""
    db_path = tmp_path / "reopen.db"
    monkeypatch.setenv("TRADING_DB_PATH", str(db_path))

    from src.persistence.sqlite_store import SQLiteStore

    # First lifespan-like cycle
    app1 = _make_app(monkeypatch, _FakeMarketEngine)
    with TestClient(app1):
        pass

    # Open store directly (simulating a second process/lifespan)
    store = SQLiteStore(db_path)
    _run(store.initialize())

    # Verify the schema remains at current v12 after re-open.
    conn = sqlite3.connect(str(db_path))
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    assert version == 12

    # Tables should exist exactly once
    tables = {
        row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }
    assert tables == _SCHEMA_V12_TABLES
    conn.close()

    _run(store.close())


# ── 6. No test touches the default production path ──────────────────────────────


def test_no_default_path_accessed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Tests must use tmp_path; never open backend/data/trading_bot.sqlite3."""
    # Ensure TRADING_DB_PATH is set so the default path is never used
    db_path = tmp_path / "no_default.db"
    monkeypatch.setenv("TRADING_DB_PATH", str(db_path))

    from src.config import Config
    cfg = Config()
    # The config should resolve to our tmp_path, not the default
    assert "trading_bot.sqlite3" not in str(cfg.database_path) or db_path in cfg.database_path.parents


# ── 7. Config resolves TRADING_DB_PATH correctly ───────────────────────────────


def test_config_resolves_db_path_from_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Config.database_path reflects TRADING_DB_PATH when set."""
    db_path = tmp_path / "config_check.db"
    monkeypatch.setenv("TRADING_DB_PATH", str(db_path))

    from src.config import Config

    cfg = Config()
    assert cfg.database_path == db_path


def test_config_default_db_path_is_relative_to_backend_root(monkeypatch: pytest.MonkeyPatch) -> None:
    """When TRADING_DB_PATH is unset, default resolves relative to backend package root."""
    monkeypatch.delenv("TRADING_DB_PATH", raising=False)

    from src.config import Config

    cfg = Config()
    expected = Path(__file__).resolve().parents[1] / "data" / "trading_bot.sqlite3"
    assert cfg.database_path == expected


# ── 8. Directory creation on startup ────────────────────────────────────────────


def test_parent_directory_created(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The parent directory of the DB path is created if it doesn't exist."""
    db_path = tmp_path / "nested" / "deep" / "store.db"
    # Ensure the nested dir does NOT exist yet
    assert not db_path.parent.exists()

    monkeypatch.setenv("TRADING_DB_PATH", str(db_path))

    app = _make_app(monkeypatch, _FakeMarketEngine)

    with TestClient(app):
        pass  # lifespan should create parent dirs

    assert db_path.parent.exists()


# ── 9. Multiple lifespan cycles on same DB are safe ────────────────────────────


def test_multiple_lifespan_cycles_safe(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Multiple TestClient enters/exits on the same DB file are safe."""
    db_path = tmp_path / "multi_cycle.db"
    monkeypatch.setenv("TRADING_DB_PATH", str(db_path))

    app = _make_app(monkeypatch, _FakeMarketEngine)

    for _ in range(3):
        with TestClient(app) as c:
            resp = c.get("/health")
            assert resp.status_code == 200

    # Final verification: schema remains current v12.
    conn = sqlite3.connect(str(db_path))
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    conn.close()
    assert version == 12


# ── 10. Store is not accessible outside lifespan ───────────────────────────────


def test_store_not_accessible_outside_lifespan(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Outside the lifespan context, app.state._sqlite_store is None."""
    db_path = tmp_path / "outside.db"
    monkeypatch.setenv("TRADING_DB_PATH", str(db_path))

    app = _make_app(monkeypatch, _FakeMarketEngine)

    # Before any client enters
    assert getattr(app.state, "_sqlite_store", "MISSING") is None
