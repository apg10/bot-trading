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
from src.persistence import sqlite_store as sqlite_store_module
from src.persistence.sqlite_store import (
    SQLiteStore,
    _SCHEMA_V9_TABLES,
    _SCHEMA_V10_TABLES,
    _SCHEMA_V15_TABLES,
)

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
        assert version == 15


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
    assert version == 15

    # Tables should exist exactly once
    tables = {
        row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }
    assert tables == _SCHEMA_V15_TABLES
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


def _seed_v13_lifecycle_tables(db_path: Path) -> None:
    """Create an old lifecycle schema after creating the cumulative v13 DB."""
    store = sqlite_store_module.SQLiteStore(db_path)
    _run(store.initialize())
    _run(store.close())
    conn = sqlite3.connect(str(db_path))
    conn.executescript(
        """
        DROP TABLE paper_lifecycle_events;
        DROP TABLE paper_lifecycle_states;
        CREATE TABLE paper_lifecycle_states (
            account_id TEXT NOT NULL,
            paper_session_id TEXT NOT NULL,
            state TEXT NOT NULL CHECK (state IN ('running', 'paused')),
            last_command_id TEXT,
            last_updated_ms INTEGER NOT NULL,
            PRIMARY KEY (account_id, paper_session_id)
        );
        CREATE TABLE paper_lifecycle_events (
            event_id INTEGER PRIMARY KEY,
            command_id TEXT NOT NULL UNIQUE,
            account_id TEXT NOT NULL,
            paper_session_id TEXT NOT NULL,
            action TEXT NOT NULL CHECK (action IN ('pause', 'resume')),
            previous_state TEXT NOT NULL CHECK (previous_state IN ('running', 'paused')),
            resulting_state TEXT NOT NULL CHECK (resulting_state IN ('running', 'paused')),
            transitioned INTEGER NOT NULL CHECK (transitioned IN (0, 1)),
            timestamp_ms INTEGER NOT NULL
        );
        """
    )
    conn.execute(
        "INSERT INTO paper_lifecycle_states VALUES (?, ?, ?, ?, ?)",
        ("acct-migrate", "session-migrate", "paused", "pause-1", 10),
    )
    conn.execute(
        "INSERT INTO paper_lifecycle_events VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (1, "pause-1", "acct-migrate", "session-migrate", "pause", "running", "paused", 1, 10),
    )
    conn.execute("PRAGMA user_version = 13")
    conn.commit()
    conn.close()


def _seed_v14_lifecycle_tables(db_path: Path) -> None:
    """Create a v14 database with the pre-v15 event CHECK constraint."""
    store = SQLiteStore(db_path)
    _run(store.initialize())
    _run(store.close())
    conn = sqlite3.connect(str(db_path))
    conn.execute("DROP TABLE paper_lifecycle_reconciliations")
    conn.execute("ALTER TABLE paper_lifecycle_events RENAME TO paper_lifecycle_events_old")
    conn.execute(
        """CREATE TABLE paper_lifecycle_events (
            event_id INTEGER PRIMARY KEY,
            command_id TEXT NOT NULL UNIQUE,
            account_id TEXT NOT NULL,
            paper_session_id TEXT NOT NULL,
            action TEXT NOT NULL CHECK (action IN ('pause', 'resume', 'stop', 'close')),
            previous_state TEXT NOT NULL CHECK (previous_state IN ('running', 'paused', 'stopped')),
            resulting_state TEXT NOT NULL CHECK (resulting_state IN ('running', 'paused', 'stopped')),
            transitioned INTEGER NOT NULL CHECK (transitioned IN (0, 1)),
            timestamp_ms INTEGER NOT NULL
        )"""
    )
    conn.execute(
        "INSERT INTO paper_lifecycle_events SELECT * FROM paper_lifecycle_events_old"
    )
    conn.execute("DROP TABLE paper_lifecycle_events_old")
    conn.execute(
        "INSERT INTO paper_lifecycle_states VALUES (?, ?, ?, ?, ?)",
        ("acct-mig", "sess-mig", "running", None, 1000),
    )
    conn.execute(
        "INSERT INTO paper_lifecycle_events VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (1, "pause-mig", "acct-mig", "sess-mig", "pause", "running", "paused", 1, 1001),
    )
    conn.execute("PRAGMA user_version = 14")
    conn.commit()
    conn.close()


def test_v14_to_v15_migration_adds_reconciliation_table_and_rebuilds_event_check(tmp_path: Path) -> None:
    """Migration v14→v15 adds reconciliation table and rebuilds event CHECK."""
    db = tmp_path / "v14-to-v15.db"
    _seed_v14_lifecycle_tables(db)
    store = SQLiteStore(db)
    _run(store.initialize())
    assert {
        row[0]
        for row in sqlite3.connect(str(db)).execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    } == _SCHEMA_V15_TABLES
    conn = sqlite3.connect(str(db))
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 15
    event_sql = conn.execute(
        "SELECT sql FROM sqlite_master WHERE name='paper_lifecycle_events'"
    ).fetchone()[0]
    conn.close()
    assert "'reconcile'" in event_sql


def test_v14_to_v15_migration_preserves_lifecycle_rows(tmp_path: Path) -> None:
    """Migration v14→v15 preserves all lifecycle state and event rows."""
    db = tmp_path / "v14-preserve.db"
    _seed_v14_lifecycle_tables(db)
    _run(SQLiteStore(db).initialize())
    conn = sqlite3.connect(str(db))
    state = conn.execute(
        "SELECT account_id, paper_session_id, state, last_updated_ms "
        "FROM paper_lifecycle_states WHERE account_id='acct-mig'"
    ).fetchone()
    event = conn.execute(
        "SELECT command_id, action, resulting_state FROM paper_lifecycle_events "
        "WHERE command_id='pause-mig'"
    ).fetchone()
    conn.close()
    assert state == ("acct-mig", "sess-mig", "running", 1000)
    assert event == ("pause-mig", "pause", "paused")


def test_v14_to_v15_migration_rolls_back_atomically(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Failed v14→v15 migration rolls back tables, rows, and user_version."""
    db = tmp_path / "v14-to-v15-rollback.db"
    _seed_v14_lifecycle_tables(db)
    original_execute = sqlite_store_module.aiosqlite.Connection.execute

    async def fail_event_rename(self: Any, sql: Any, *args: Any, **kwargs: Any) -> Any:
        if "ALTER TABLE paper_lifecycle_events_v15_new" in str(sql):
            raise RuntimeError("synthetic v15 lifecycle rebuild failure")
        return await original_execute(self, sql, *args, **kwargs)

    monkeypatch.setattr(sqlite_store_module.aiosqlite.Connection, "execute", fail_event_rename)
    with pytest.raises(RuntimeError, match="synthetic v15 lifecycle rebuild failure"):
        _run(SQLiteStore(db).initialize())

    conn = sqlite3.connect(str(db))
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 14
    assert conn.execute(
        "SELECT state FROM paper_lifecycle_states WHERE account_id='acct-mig'"
    ).fetchone()[0] == "running"
    assert conn.execute(
        "SELECT action FROM paper_lifecycle_events WHERE command_id='pause-mig'"
    ).fetchone()[0] == "pause"
    assert conn.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE name LIKE '%_v15_new'"
    ).fetchone()[0] == 0
    conn.close()


def test_v13_to_v14_migration_rebuilds_lifecycle_checks_and_preserves_rows(tmp_path: Path) -> None:
    db = tmp_path / "v13-to-v14.db"
    _seed_v13_lifecycle_tables(db)
    store = sqlite_store_module.SQLiteStore(db)
    _run(store.initialize())
    conn = sqlite3.connect(str(db))
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 15
    assert conn.execute(
        "SELECT account_id, paper_session_id, state, last_command_id, last_updated_ms "
        "FROM paper_lifecycle_states"
    ).fetchall() == [("acct-migrate", "session-migrate", "paused", "pause-1", 10)]
    assert conn.execute(
        "SELECT event_id, command_id, action, previous_state, resulting_state, transitioned, timestamp_ms "
        "FROM paper_lifecycle_events"
    ).fetchall() == [(1, "pause-1", "pause", "running", "paused", 1, 10)]
    state_sql = conn.execute(
        "SELECT sql FROM sqlite_master WHERE name='paper_lifecycle_states'"
    ).fetchone()[0]
    event_sql = conn.execute(
        "SELECT sql FROM sqlite_master WHERE name='paper_lifecycle_events'"
    ).fetchone()[0]
    assert "'stopped'" in state_sql
    assert "'stop'" in event_sql and "'close'" in event_sql
    conn.close()
    _run(store.close())


def test_v13_to_v14_migration_rolls_back_atomically(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = tmp_path / "v13-to-v14-rollback.db"
    _seed_v13_lifecycle_tables(db)
    original_execute = sqlite_store_module.aiosqlite.Connection.execute

    async def fail_event_rename(self, sql, *args, **kwargs):
        if "ALTER TABLE paper_lifecycle_events_v14_new" in str(sql):
            raise RuntimeError("synthetic lifecycle rebuild failure")
        return await original_execute(self, sql, *args, **kwargs)

    monkeypatch.setattr(sqlite_store_module.aiosqlite.Connection, "execute", fail_event_rename)
    with pytest.raises(RuntimeError, match="synthetic lifecycle rebuild failure"):
        _run(sqlite_store_module.SQLiteStore(db).initialize())

    conn = sqlite3.connect(str(db))
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 13
    assert conn.execute(
        "SELECT state FROM paper_lifecycle_states WHERE account_id='acct-migrate'"
    ).fetchone()[0] == "paused"
    assert conn.execute(
        "SELECT action FROM paper_lifecycle_events WHERE command_id='pause-1'"
    ).fetchone()[0] == "pause"
    assert conn.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE name LIKE '%_v14_new'"
    ).fetchone()[0] == 0
    conn.close()


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
    assert version == 15


# ── 10. Store is not accessible outside lifespan ───────────────────────────────


def test_store_not_accessible_outside_lifespan(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Outside the lifespan context, app.state._sqlite_store is None."""
    db_path = tmp_path / "outside.db"
    monkeypatch.setenv("TRADING_DB_PATH", str(db_path))

    app = _make_app(monkeypatch, _FakeMarketEngine)

    # Before any client enters
    assert getattr(app.state, "_sqlite_store", "MISSING") is None
