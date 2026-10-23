import asyncio
import json
import sqlite3

import pytest

from src.api.research_allowlist import ALLOWED
from src.models.research_audit import ResearchRequestAuditRecord
from src.persistence.sqlite_store import (
    ResearchRequestAuditRecordConflictError,
    ResearchRequestAuditRecordIntegrityError,
    SQLiteStore,
)


def _run(awaitable):
    return asyncio.run(awaitable)


def _store(path):
    store = SQLiteStore(path)
    _run(store.initialize())
    return store


def _record(request_id="r-1", tool_id="tool", route="/health"):
    return ResearchRequestAuditRecord(
        request_id=request_id, tool_id=tool_id, method="GET", canonical_route=route,
        allowlist_decision=ALLOWED, sanitized_params={"symbol": "BTC/USDT"},
        provenance={"schema_version": "research_gateway.v1"}, outcome="success",
        public_result_metadata={"status_code": 200}, timing_ms=1.0, captured_at_ms=1,
    )


def test_schema_v21_migration(tmp_path):
    path = tmp_path / "migration.db"
    store = _store(path)
    _run(store.close())
    with sqlite3.connect(path) as conn:
        conn.execute("PRAGMA user_version = 20")
    reopened = _store(path)
    with sqlite3.connect(path) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 21
        assert conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='research_request_audit_records'").fetchone()
    _run(reopened.close())


def test_save_and_load_record(tmp_path):
    store = _store(tmp_path / "roundtrip.db")
    record = _record()
    _run(store.save_research_request_audit_record(record))
    assert _run(store.load_research_request_audit_record(record.request_id)) == record


def test_idempotent_insert(tmp_path):
    store = _store(tmp_path / "idempotent.db")
    record = _record()
    _run(store.save_research_request_audit_record(record))
    _run(store.save_research_request_audit_record(record))


def test_conflict_detection(tmp_path):
    store = _store(tmp_path / "conflict.db")
    _run(store.save_research_request_audit_record(_record()))
    with pytest.raises(ResearchRequestAuditRecordConflictError):
        _run(store.save_research_request_audit_record(_record(route="/other")))


def test_integrity_check(tmp_path):
    path = tmp_path / "corrupt.db"
    store = _store(path)
    _run(store.save_research_request_audit_record(_record()))
    _run(store.close())
    with sqlite3.connect(path) as conn:
        conn.execute("UPDATE research_request_audit_records SET sanitized_params = 'bad' WHERE request_id = 'r-1'")
    reopened = _store(path)
    with pytest.raises(ResearchRequestAuditRecordIntegrityError):
        _run(reopened.load_research_request_audit_record("r-1"))


def test_query_by_tool_id(tmp_path):
    store = _store(tmp_path / "tool.db")
    _run(store.save_research_request_audit_record(_record("r-1", "a")))
    _run(store.save_research_request_audit_record(_record("r-2", "b")))
    assert [r.request_id for r in _run(store.load_research_request_audit_records("a", None, 10))] == ["r-1"]


def test_query_by_route(tmp_path):
    store = _store(tmp_path / "route.db")
    _run(store.save_research_request_audit_record(_record("r-1", route="/health")))
    _run(store.save_research_request_audit_record(_record("r-2", route="/other")))
    assert [r.request_id for r in _run(store.load_research_request_audit_records(None, "/other", 10))] == ["r-2"]


def test_load_nonexistent(tmp_path):
    store = _store(tmp_path / "none.db")
    assert _run(store.load_research_request_audit_record("missing")) is None


def test_v20_to_v21_migration_preserves_n02_data(tmp_path):
    """v20→v21 migration must preserve existing N02 audit records and create
    the new research table.  The migration is purely additive (CREATE TABLE IF
    NOT EXISTS + user_version bump) and must not touch any v20 tables."""
    db = tmp_path / "v20.db"

    # --- Build a pristine v20 database with N02 data ---
    with sqlite3.connect(str(db)) as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        # Create all v20 tables (minimal DDL matching sqlite_store.py schema)
        conn.execute(
            """CREATE TABLE sessions (
                session_id TEXT PRIMARY KEY NOT NULL
            )"""
        )
        conn.execute(
            """CREATE TABLE strategy_config_versions (
                strategy_id TEXT NOT NULL,
                strategy_version TEXT NOT NULL,
                config_json TEXT NOT NULL,
                PRIMARY KEY (strategy_id, strategy_version)
            )"""
        )
        conn.execute(
            """CREATE TABLE session_strategy_configs (
                session_id TEXT NOT NULL REFERENCES sessions(session_id),
                strategy_id TEXT NOT NULL,
                strategy_version TEXT NOT NULL,
                PRIMARY KEY (session_id, strategy_id, strategy_version),
                FOREIGN KEY (strategy_id, strategy_version)
                    REFERENCES strategy_config_versions(strategy_id, strategy_version)
            )"""
        )
        conn.execute(
            """CREATE TABLE session_dataset_refs (
                session_id TEXT NOT NULL REFERENCES sessions(session_id),
                dataset_ref TEXT NOT NULL,
                PRIMARY KEY (session_id, dataset_ref)
            )"""
        )
        conn.execute(
            """CREATE TABLE experiment_definitions (
                experiment_id TEXT NOT NULL,
                experiment_version TEXT NOT NULL,
                definition_json TEXT NOT NULL,
                PRIMARY KEY (experiment_id, experiment_version)
            )"""
        )
        conn.execute(
            """CREATE TABLE run_attempts (
                run_id TEXT PRIMARY KEY,
                experiment_id TEXT NOT NULL,
                experiment_version TEXT NOT NULL,
                variant_id TEXT NOT NULL,
                status TEXT NOT NULL CHECK(status IN ('pending','running','failed','cancelled','completed')),
                run_json TEXT NOT NULL,
                FOREIGN KEY (experiment_id, experiment_version)
                    REFERENCES experiment_definitions(experiment_id, experiment_version)
            )"""
        )
        conn.execute(
            """CREATE TABLE experiment_results (
                result_id TEXT PRIMARY KEY,
                run_id TEXT NOT NULL UNIQUE REFERENCES run_attempts(run_id),
                result_json TEXT NOT NULL
            )"""
        )
        conn.execute(
            """CREATE TABLE variant_dispositions (
                experiment_id TEXT NOT NULL,
                experiment_version TEXT NOT NULL,
                variant_id TEXT NOT NULL,
                record_json TEXT NOT NULL,
                PRIMARY KEY (experiment_id, experiment_version, variant_id),
                FOREIGN KEY (experiment_id, experiment_version)
                    REFERENCES experiment_definitions(experiment_id, experiment_version)
            )"""
        )
        conn.execute(
            """CREATE TABLE result_exclusions (
                result_id TEXT PRIMARY KEY,
                record_json TEXT NOT NULL,
                FOREIGN KEY (result_id)
                    REFERENCES experiment_results(result_id)
            )"""
        )
        conn.execute(
            """CREATE TABLE paper_fills (
                fill_id TEXT PRIMARY KEY,
                record_json TEXT NOT NULL
            )"""
        )
        conn.execute(
            """CREATE TABLE paper_account_openings (
                account_id TEXT PRIMARY KEY,
                opening_json TEXT NOT NULL
            )"""
        )
        conn.execute(
            """CREATE TABLE paper_account_balances (
                account_id TEXT NOT NULL,
                asset TEXT NOT NULL,
                balance_json TEXT NOT NULL,
                PRIMARY KEY (account_id, asset),
                FOREIGN KEY (account_id) REFERENCES paper_account_openings(account_id)
            )"""
        )
        conn.execute(
            """CREATE TABLE paper_orders (
                order_id TEXT PRIMARY KEY,
                paper_account_id TEXT NOT NULL,
                paper_session_id TEXT NOT NULL,
                environment TEXT NOT NULL CHECK (environment = 'paper'),
                client_order_id TEXT NOT NULL,
                order_json TEXT NOT NULL,
                UNIQUE (paper_account_id, paper_session_id, environment, client_order_id)
            )"""
        )
        conn.execute(
            """CREATE TABLE paper_order_acceptances (
                order_id TEXT PRIMARY KEY REFERENCES paper_orders(order_id),
                reservation_id TEXT NOT NULL UNIQUE,
                paper_account_id TEXT NOT NULL,
                paper_session_id TEXT NOT NULL,
                environment TEXT NOT NULL CHECK (environment = 'paper'),
                client_order_id TEXT NOT NULL,
                initial_order_json TEXT NOT NULL,
                acceptance_payload_json TEXT NOT NULL,
                reservation_json TEXT NOT NULL
            )"""
        )
        conn.execute(
            """CREATE TABLE paper_reservations (
                reservation_id TEXT PRIMARY KEY REFERENCES paper_order_acceptances(reservation_id),
                owner_order_id TEXT NOT NULL UNIQUE REFERENCES paper_orders(order_id),
                account_id TEXT NOT NULL,
                paper_session_id TEXT NOT NULL,
                environment TEXT NOT NULL CHECK (environment = 'paper'),
                asset TEXT NOT NULL,
                amount_json TEXT NOT NULL,
                reservation_json TEXT NOT NULL
            )"""
        )
        conn.execute(
            """CREATE TABLE paper_fill_applications (
                fill_id TEXT PRIMARY KEY,
                order_id TEXT NOT NULL REFERENCES paper_orders(order_id),
                account_id TEXT NOT NULL,
                paper_session_id TEXT NOT NULL,
                environment TEXT NOT NULL CHECK (environment = 'paper'),
                applied_at_ms INTEGER NOT NULL,
                application_json TEXT NOT NULL
            )"""
        )
        conn.execute(
            """CREATE TABLE position_lots (
                order_id TEXT PRIMARY KEY,
                account_id TEXT NOT NULL,
                paper_session_id TEXT NOT NULL,
                environment TEXT NOT NULL CHECK (environment = 'paper'),
                lot_json TEXT NOT NULL
            )"""
        )
        conn.execute(
            """CREATE TABLE paper_reservation_lot_allocations (
                reservation_id TEXT NOT NULL
                    REFERENCES paper_reservations(reservation_id) ON DELETE CASCADE,
                owner_order_id TEXT NOT NULL REFERENCES paper_orders(order_id),
                position_id TEXT NOT NULL REFERENCES position_lots(order_id),
                account_id TEXT NOT NULL REFERENCES paper_account_openings(account_id),
                paper_session_id TEXT NOT NULL,
                environment TEXT NOT NULL CHECK (environment = 'paper'),
                symbol TEXT NOT NULL,
                base_asset TEXT NOT NULL,
                strategy_ref_json TEXT NOT NULL,
                initial_qty TEXT NOT NULL,
                remaining_qty TEXT NOT NULL,
                allocation_json TEXT NOT NULL,
                PRIMARY KEY (reservation_id, position_id)
            )"""
        )
        conn.execute(
            """CREATE TABLE paper_sell_accounting (
                fill_id TEXT PRIMARY KEY REFERENCES paper_fill_applications(fill_id),
                order_id TEXT NOT NULL REFERENCES paper_orders(order_id),
                account_id TEXT NOT NULL REFERENCES paper_account_openings(account_id),
                paper_session_id TEXT NOT NULL,
                environment TEXT NOT NULL CHECK (environment = 'paper'),
                quote_asset TEXT NOT NULL,
                strategy_ref_json TEXT NOT NULL,
                total_qty_filled TEXT NOT NULL,
                proceeds_gross TEXT NOT NULL,
                fee_asset TEXT NOT NULL,
                fee_amount TEXT NOT NULL,
                proceeds_net TEXT NOT NULL,
                total_cost_basis_removed TEXT NOT NULL,
                pnl_gross TEXT NOT NULL,
                pnl_net TEXT NOT NULL,
                allocation_slots_json TEXT NOT NULL,
                accounting_json TEXT NOT NULL,
                CHECK (fee_asset = quote_asset)
            )"""
        )
        conn.execute(
            """CREATE TABLE paper_fill_evaluations (
                evaluation_id TEXT PRIMARY KEY,
                order_id TEXT NOT NULL REFERENCES paper_orders(order_id),
                account_id TEXT NOT NULL,
                paper_session_id TEXT NOT NULL,
                environment TEXT NOT NULL CHECK (environment = 'paper'),
                status_code TEXT NOT NULL,
                result_json TEXT NOT NULL,
                evaluated_at_ms INTEGER NOT NULL,
                fill_candidate_fill_id TEXT,
                fill_candidate_fill_qty TEXT,
                fill_candidate_fill_price TEXT,
                fill_candidate_commission_json TEXT,
                CHECK (fill_candidate_fill_id IS NULL OR status_code = 'fill_candidate'),
                CHECK (fill_candidate_fill_qty IS NULL OR status_code = 'fill_candidate'),
                CHECK (fill_candidate_fill_price IS NULL OR status_code = 'fill_candidate'),
                CHECK (fill_candidate_commission_json IS NULL OR status_code = 'fill_candidate')
            )"""
        )
        conn.execute(
            """CREATE TABLE paper_lifecycle_states (
                account_id TEXT NOT NULL,
                paper_session_id TEXT NOT NULL,
                state TEXT NOT NULL CHECK (state IN ('running', 'paused', 'stopped')),
                last_command_id TEXT,
                last_updated_ms INTEGER NOT NULL,
                PRIMARY KEY (account_id, paper_session_id)
            )"""
        )
        conn.execute(
            """CREATE TABLE paper_lifecycle_events (
                event_id INTEGER PRIMARY KEY,
                command_id TEXT NOT NULL UNIQUE,
                account_id TEXT NOT NULL,
                paper_session_id TEXT NOT NULL,
                action TEXT NOT NULL CHECK (action IN ('pause', 'resume', 'stop', 'close', 'reconcile')),
                previous_state TEXT NOT NULL CHECK (previous_state IN ('running', 'paused', 'stopped')),
                resulting_state TEXT NOT NULL CHECK (resulting_state IN ('running', 'paused', 'stopped')),
                transitioned INTEGER NOT NULL CHECK (transitioned IN (0, 1)),
                timestamp_ms INTEGER NOT NULL
            )"""
        )
        conn.execute(
            """CREATE TABLE paper_lifecycle_reconciliations (
                report_id TEXT PRIMARY KEY,
                account_id TEXT NOT NULL,
                paper_session_id TEXT NOT NULL,
                report_json TEXT NOT NULL
            )"""
        )
        conn.execute(
            """CREATE TABLE ui_preferences (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                preferences TEXT NOT NULL,
                revision INTEGER NOT NULL DEFAULT 1,
                version INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )"""
        )
        conn.execute(
            """CREATE TABLE normalized_experiment_results (
                result_id TEXT PRIMARY KEY NOT NULL,
                run_id TEXT NOT NULL UNIQUE,
                experiment_id TEXT NOT NULL,
                experiment_version TEXT NOT NULL,
                variant_id TEXT NOT NULL,
                result_json TEXT NOT NULL,
                FOREIGN KEY (run_id) REFERENCES run_attempts(run_id),
                FOREIGN KEY (experiment_id, experiment_version)
                    REFERENCES experiment_definitions(experiment_id, experiment_version)
            )"""
        )
        conn.execute(
            """CREATE TABLE audit_decisions (
                account_id TEXT NOT NULL,
                paper_session_id TEXT NOT NULL,
                decision_id TEXT NOT NULL,
                timestamp INTEGER NOT NULL,
                decision_json TEXT NOT NULL,
                candle_json TEXT NOT NULL,
                PRIMARY KEY (account_id, paper_session_id, decision_id)
            )"""
        )
        # Create n02_audit_records table and insert a row that must survive migration
        conn.execute(
            """CREATE TABLE n02_audit_records (
                result_id TEXT PRIMARY KEY,
                snapshot_id TEXT NOT NULL,
                captured_at_ms INTEGER NOT NULL CHECK (captured_at_ms >= 0),
                record_json TEXT NOT NULL
            )"""
        )
        conn.execute(
            "INSERT INTO n02_audit_records VALUES ('n02-1', 'snap-1', 1000, '{\"result\": 42}')"
        )
        # Set version to 20 (v21 tables must NOT exist yet)
        conn.execute("PRAGMA user_version = 20")

    # --- Verify pre-migration state ---
    with sqlite3.connect(str(db)) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 20
        n02_count = conn.execute("SELECT COUNT(*) FROM n02_audit_records").fetchone()[0]
        assert n02_count == 1, "v20 database must contain one N02 record"
        assert conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='research_request_audit_records'"
        ).fetchone() is None, "v20 must not have the v21 table"

    # --- Apply v20→v21 migration via store ---
    store = _store(db)
    with sqlite3.connect(str(db)) as conn:
        # user_version should have been bumped to 21 by initialize()
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 21
        # The new table must exist
        assert conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='research_request_audit_records'"
        ).fetchone() is not None
        # CRITICAL: N02 data must be untouched — same count, same content
        preserved_count = conn.execute("SELECT COUNT(*) FROM n02_audit_records").fetchone()[0]
        assert preserved_count == 1, (
            f"v20→v21 migration must preserve N02 records; found {preserved_count}"
        )
        preserved_row = conn.execute(
            "SELECT result_id, snapshot_id, captured_at_ms, record_json FROM n02_audit_records WHERE result_id='n02-1'"
        ).fetchone()
        assert preserved_row == ("n02-1", "snap-1", 1000, '{"result": 42}'), (
            "N02 row content must be byte-identical after migration"
        )

    _run(store.close())


def test_v21_migration_idempotent_reapply(tmp_path):
    """Re-initializing an already-migrated (v21) database must be a no-op:
    user_version stays 21, the table still exists, and no errors are raised."""
    db = tmp_path / "v21.db"

    # First initialization creates v21
    store1 = _store(db)
    with sqlite3.connect(str(db)) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 21
    _run(store1.close())

    # Second initialization (idempotent re-open)
    store2 = _store(db)
    with sqlite3.connect(str(db)) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 21
        assert conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='research_request_audit_records'"
        ).fetchone() is not None
    _run(store2.close())
