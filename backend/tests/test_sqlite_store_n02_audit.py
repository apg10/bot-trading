"""N02 audit-record persistence and v19-to-v20 migration tests."""

from __future__ import annotations

import asyncio
import sqlite3

import pytest

from src.models.audit_contract import N02AuditRecord
from src.persistence.sqlite_store import (
    N02AuditRecordConflictError,
    N02AuditRecordIntegrityError,
    SQLiteStore,
    SchemaVersionMismatchError,
)


def _run(awaitable):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(awaitable)
    finally:
        loop.close()


def _record(result_id: str = "result-1", snapshot_id: str = "snapshot-1", captured_at_ms: int = 10):
    return N02AuditRecord(
        result_id=result_id,
        snapshot_id=snapshot_id,
        model_name="n02",
        model_version="test",
        prompt_version="prompt-1",
        prompt_hash="a" * 64,
        safe_params={"symbol": "BTC/USDT", "interval": "1m"},
        success=True,
        response_json='{"approved":true}',
        timing_ms=1,
        valid_until_ms=100,
        captured_at_ms=captured_at_ms,
    )


def _store(path):
    store = SQLiteStore(path)
    _run(store.initialize())
    return store


def test_save_load_n02_audit_record_round_trip(tmp_path):
    store = _store(tmp_path / "round-trip.db")
    record = _record()
    _run(store.save_n02_audit_record(record))
    assert _run(store.load_n02_audit_record(record.result_id)) == record
    _run(store.close())


def test_save_n02_audit_record_is_idempotent(tmp_path):
    path = tmp_path / "idempotent.db"
    store = _store(path)
    record = _record()
    _run(store.save_n02_audit_record(record))
    _run(store.save_n02_audit_record(record))
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT count(*) FROM n02_audit_records").fetchone()[0] == 1
    _run(store.close())


def test_save_n02_audit_record_conflict_does_not_overwrite(tmp_path):
    store = _store(tmp_path / "conflict.db")
    record = _record()
    _run(store.save_n02_audit_record(record))
    with pytest.raises(N02AuditRecordConflictError):
        _run(store.save_n02_audit_record(_record(captured_at_ms=11)))
    assert _run(store.load_n02_audit_record(record.result_id)) == record
    _run(store.close())


def test_load_n02_audit_records_filters_by_snapshot(tmp_path):
    store = _store(tmp_path / "filter.db")
    first = _record("result-2", captured_at_ms=20)
    second = _record("result-1", captured_at_ms=10)
    other = _record("result-3", snapshot_id="snapshot-2", captured_at_ms=1)
    for record in (first, second, other):
        _run(store.save_n02_audit_record(record))
    assert _run(store.load_n02_audit_records("snapshot-1")) == [second, first]
    _run(store.close())


def test_n02_audit_record_corruption_is_rejected(tmp_path):
    path = tmp_path / "corrupt.db"
    store = _store(path)
    record = _record()
    _run(store.save_n02_audit_record(record))
    _run(store.close())
    with sqlite3.connect(path) as conn:
        conn.execute(
            "UPDATE n02_audit_records SET record_json = ? WHERE result_id = ?",
            ("not-json", record.result_id),
        )
    reopened = _store(path)
    with pytest.raises(N02AuditRecordIntegrityError):
        _run(reopened.load_n02_audit_record(record.result_id))
    _run(reopened.close())


def test_migration_v18_to_v20_adds_n02_table_and_preserves_data(tmp_path):
    path = tmp_path / "migration.db"
    store = _store(path)
    with sqlite3.connect(path) as conn:
        conn.execute("INSERT INTO sessions(session_id) VALUES ('legacy-session')")
        conn.execute("PRAGMA user_version = 18")
    _run(store.close())
    reopened = _store(path)
    with sqlite3.connect(path) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 20
        assert conn.execute("SELECT session_id FROM sessions").fetchone()[0] == "legacy-session"
        assert conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='n02_audit_records'"
        ).fetchone() is not None
    _run(reopened.close())


def test_migration_v18_to_v20_is_idempotent(tmp_path):
    path = tmp_path / "migration-idempotent.db"
    store = _store(path)
    with sqlite3.connect(path) as conn:
        conn.execute("PRAGMA user_version = 18")
    _run(store.close())
    reopened = _store(path)
    _run(reopened.close())
    reopened = _store(path)
    with sqlite3.connect(path) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 20
        assert conn.execute(
            "SELECT count(*) FROM sqlite_master WHERE type='table' AND name='n02_audit_records'"
        ).fetchone()[0] == 1
        assert conn.execute(
            "SELECT count(*) FROM sqlite_master WHERE type='index' AND name='idx_n02_audit_records_snapshot'"
        ).fetchone()[0] == 1
    _run(reopened.close())


def test_migration_v19_to_v20_rolls_back_on_invalid_legacy_schema(tmp_path):
    path = tmp_path / "migration-rollback.db"
    with sqlite3.connect(path) as conn:
        # Keep the candle table valid so the migration can reject the
        # incomplete v19 schema with SchemaVersionMismatchError.
        conn.execute(
            """CREATE TABLE market_candles (
                symbol TEXT NOT NULL,
                interval TEXT NOT NULL,
                open_time INTEGER NOT NULL,
                timestamp INTEGER NOT NULL,
                open TEXT NOT NULL,
                high TEXT NOT NULL,
                low TEXT NOT NULL,
                close TEXT NOT NULL,
                volume TEXT NOT NULL,
                quote_volume TEXT NOT NULL,
                trades INTEGER NOT NULL,
                source TEXT NOT NULL,
                PRIMARY KEY (symbol, interval, open_time)
            )"""
        )
        conn.execute(
            "CREATE INDEX idx_market_candles_series "
            "ON market_candles (symbol, interval, open_time)"
        )
        conn.execute("PRAGMA user_version = 19")
        legacy_schema = {
            (row[0], row[1], row[2])
            for row in conn.execute(
                "SELECT type, name, sql FROM sqlite_master "
                "WHERE type IN ('table', 'index') AND name NOT LIKE 'sqlite_%'"
            )
        }
    store = SQLiteStore(path)
    with pytest.raises(SchemaVersionMismatchError):
        _run(store.initialize())
    with sqlite3.connect(path) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 19
        schema_after_failure = {
            (row[0], row[1], row[2])
            for row in conn.execute(
                "SELECT type, name, sql FROM sqlite_master "
                "WHERE type IN ('table', 'index') AND name NOT LIKE 'sqlite_%'"
            )
        }
        assert schema_after_failure == legacy_schema


def test_migration_v18_to_v20_rejects_incomplete_v19(tmp_path):
    """A v18 database without market_candles must not silently upgrade."""
    path = tmp_path / "incomplete-v19.db"
    with sqlite3.connect(path) as conn:
        # Create only v18 tables (audit_decisions), skip market_candles.
        conn.execute(
            """CREATE TABLE audit_decisions (
                account_id TEXT NOT NULL,
                session_id TEXT NOT NULL,
                decision_id TEXT NOT NULL,
                symbol TEXT NOT NULL,
                signal TEXT NOT NULL,
                candle_index INTEGER NOT NULL,
                decision_json TEXT NOT NULL,
                PRIMARY KEY (account_id, session_id, decision_id)
            )"""
        )
        conn.execute("PRAGMA user_version = 18")
    store = SQLiteStore(path)
    with pytest.raises(SchemaVersionMismatchError):
        _run(store.initialize())
    with sqlite3.connect(path) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 18
