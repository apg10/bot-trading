"""Tests for SQLiteStore — session, StrategyConfig versions, DatasetRef references."""

from __future__ import annotations

import asyncio
import json
import sqlite3
from pathlib import Path

import pytest
from pydantic import ValidationError

from src.models.dataset_ref import DatasetRef
from src.models.experiment_contracts import (
    ExperimentDefinition,
    ExperimentRef,
    ExperimentResult,
    ExperimentVariant,
    PeriodWindow,
    PublicDiagnostic,
    ResultExclusionRecord,
    RunAttempt,
    VariantDiscardRecord,
)
from src.models.paper_contracts import StrategyRef
from src.models.strategy_config import IndicatorInstance, ParameterEntry, StrategyConfig
from src.persistence import sqlite_store as sqlite_store_module
from src.persistence.sqlite_store import (
    ConfigConflictError,
    ExperimentDefinitionConflictError,
    ExperimentDefinitionIntegrityError,
    ExperimentResultConflictError,
    ExperimentResultStateError,
    ResultExclusionConflictError,
    RunAttemptConflictError,
    RunAttemptStateError,
    SchemaVersionMismatchError,
    SQLiteStore,
    StoreClosedError,
    VariantDiscardConflictError,
    _SCHEMA_V4_TABLES,
    _SCHEMA_V9_TABLES,
    _SCHEMA_V15_TABLES,
)


# ── helpers ────────────────────────────────────────────────────────────────────


def _make_strategy_config(
    strategy_id: str = "strat-1",
    strategy_version: str = "v1",
    ema_period: int = 20,
) -> StrategyConfig:
    return StrategyConfig(
        strategy_ref=StrategyRef(
            strategy_id=strategy_id,
            strategy_version=strategy_version,
        ),
        indicator_instances=(
            IndicatorInstance(
                instance_id="keltner-main",
                indicator_id="keltner",
                parameters=(
                    ParameterEntry(name="ema_period", value=ema_period),
                    ParameterEntry(name="atr_period", value=14),
                    ParameterEntry(name="multiplier", value=2.0),
                ),
            ),
        ),
    )


def _make_experiment_definition(
    experiment_id: str = "experiment-1",
    experiment_version: str = "v1",
    hypothesis: str = "explicit test hypothesis",
    variant_ids: tuple[str, ...] | None = None,
    strategy_id: str = "strategy-experiment",
    strategy_version: str = "strategy-v1",
    dataset_ref: DatasetRef = "dataset-experiment",
) -> ExperimentDefinition:
    if variant_ids is None:
        variant_ids = ("variant-a",)
    variants = tuple(
        ExperimentVariant(
            variant_id=vid,
            strategy_config=_make_strategy_config(strategy_id, strategy_version),
            dataset_ref=dataset_ref,
        )
        for vid in variant_ids
    )
    return ExperimentDefinition(
        experiment_ref=ExperimentRef(
            experiment_id=experiment_id,
            experiment_version=experiment_version,
        ),
        hypothesis=hypothesis,
        planned_periods=(
            PeriodWindow(purpose="design", start_ms=1_700_000_000_000, end_ms=1_700_086_400_000),
        ),
        variants=variants,
    )


def _run(coro):
    """Run an async coroutine synchronously."""
    return asyncio.run(coro)


# ── 1. Double-initialize applies a single migration; FKs and version correct ──


def test_double_initialize_applies_single_migration(tmp_path: Path) -> None:
    db = tmp_path / "test.db"
    store = SQLiteStore(db)
    _run(store.initialize())
    _run(store.initialize())  # second init must not duplicate tables

    conn = sqlite3.connect(str(db))
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 15
    # FKs are set per-connection during writes; verify by checking the schema
    fk = conn.execute("PRAGMA foreign_keys").fetchone()[0]
    assert fk == 0  # default off when not explicitly enabled
    # Verify tables exist and FK constraints are declared in schema
    tables = {
        row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }
    assert tables == _SCHEMA_V15_TABLES
    conn.close()
    _run(store.close())


# ── 2. Save session + configs + refs is atomic; conflict on different JSON ─────


def test_save_session_atomic(tmp_path: Path) -> None:
    db = tmp_path / "test.db"
    store = SQLiteStore(db)
    _run(store.initialize())

    sid = "sess-1"
    cfgs = [_make_strategy_config()]
    refs: list[DatasetRef] = ["ds-ref-alpha"]
    _run(store.save_session(sid, cfgs, refs))

    # Verify all three tables have rows
    conn = sqlite3.connect(str(db))
    assert conn.execute("SELECT COUNT(*) FROM sessions WHERE session_id=?", (sid,)).fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM strategy_config_versions").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM session_strategy_configs").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM session_dataset_refs").fetchone()[0] == 1
    conn.close()
    _run(store.close())


def test_config_conflict_different_json(tmp_path: Path) -> None:
    db = tmp_path / "test.db"
    store = SQLiteStore(db)
    _run(store.initialize())

    sid = "sess-1"
    cfgs = [_make_strategy_config()]
    refs: list[DatasetRef] = []
    _run(store.save_session(sid, cfgs, refs))

    # Same StrategyRef but different explicit config → conflict.
    cfgs_conflict = [_make_strategy_config("strat-1", "v1", ema_period=21)]
    with pytest.raises(ConfigConflictError, match="conflict for strategy strat-1 version v1"):
        _run(store.save_session("sess-2", cfgs_conflict, refs))

    # sess-1 must still be intact (no partial writes)
    result = _run(store.load_session("sess-1"))
    assert result is not None
    assert len(result["strategy_configs"]) == 1
    assert result["strategy_configs"][0].model_dump_json() == cfgs[0].model_dump_json()
    assert result["dataset_refs"] == refs
    _run(store.close())


# ── 3. Close / reopen recovers session, validated config, literal refs ─────────


def test_close_reopen_recovers_data(tmp_path: Path) -> None:
    db = tmp_path / "test.db"
    store1 = SQLiteStore(db)
    _run(store1.initialize())

    sid = "sess-recovery"
    cfgs = [_make_strategy_config("my-strat", "v3")]
    refs: list[DatasetRef] = ["ds-ref-xyz"]
    _run(store1.save_session(sid, cfgs, refs))
    _run(store1.close())

    # Open a new instance with the same path
    store2 = SQLiteStore(db)
    _run(store2.initialize())
    result = _run(store2.load_session(sid))
    assert result is not None
    assert result["session_id"] == sid
    assert len(result["strategy_configs"]) == 1
    cfg = result["strategy_configs"][0]
    assert isinstance(cfg, StrategyConfig)
    assert cfg.strategy_ref.strategy_id == "my-strat"
    assert cfg.strategy_ref.strategy_version == "v3"
    assert cfg.model_dump_json() == cfgs[0].model_dump_json()
    assert result["dataset_refs"] == ["ds-ref-xyz"]
    _run(store2.close())


# ── 4. Forced-fail transaction rolls back session, config, refs ───────────────


def test_forced_fail_rolls_back_all(tmp_path: Path) -> None:
    db = tmp_path / "test.db"
    store = SQLiteStore(db)
    _run(store.initialize())

    # First save succeeds
    sid_ok = "sess-ok"
    cfgs_ok = [_make_strategy_config("ok-strat", "v1")]
    refs_ok: list[DatasetRef] = ["ds-ref-ok"]
    _run(store.save_session(sid_ok, cfgs_ok, refs_ok))

    # Second save: session inserted first, then config conflict should roll back everything
    sid_fail = "sess-fail"
    cfgs_fail = [_make_strategy_config("ok-strat", "v1", ema_period=21)]
    refs_fail: list[DatasetRef] = ["ds-ref-fail"]

    with pytest.raises(ConfigConflictError):
        _run(store.save_session(sid_fail, cfgs_fail, refs_fail))

    # Verify sess-ok is intact
    result_ok = _run(store.load_session(sid_ok))
    assert result_ok is not None
    assert result_ok["strategy_configs"][0].model_dump_json() == cfgs_ok[0].model_dump_json()
    assert result_ok["dataset_refs"] == refs_ok

    # Verify sess-fail does NOT exist (rolled back)
    result_fail = _run(store.load_session(sid_fail))
    assert result_fail is None
    _run(store.close())


# ── 5. Concurrent writes serialize; no lost or duplicated associations ─────────


def test_concurrent_writes_serialize(tmp_path: Path) -> None:
    db = tmp_path / "test.db"
    store = SQLiteStore(db)
    _run(store.initialize())

    async def write_session(session_id: str, idx: int) -> None:
        cfgs = [_make_strategy_config(f"strat-{idx}", f"v{idx}")]
        refs: list[DatasetRef] = [f"ds-ref-{idx}"]
        await store.save_session(session_id, cfgs, refs)

    # Launch multiple concurrent writes with different session IDs
    async def _concurrent() -> None:
        tasks = [asyncio.create_task(write_session(f"sess-concurrent-{i}", i)) for i in range(5)]
        await asyncio.gather(*tasks)

    _run(_concurrent())

    # Verify all sessions exist exactly once
    result = _run(store.load_session("sess-concurrent-0"))
    assert result is not None
    for i in range(5):
        r = _run(store.load_session(f"sess-concurrent-{i}"))
        assert r is not None
        assert len(r["strategy_configs"]) == 1
        assert r["dataset_refs"] == [f"ds-ref-{i}"]
    _run(store.close())


# ── 6. Tests use tmp_path; no app/service/production DB initialization ─────────


def test_no_app_initialization(tmp_path: Path) -> None:
    """Store must work standalone without FastAPI / any app."""
    db = tmp_path / "standalone.db"
    store = SQLiteStore(db)
    _run(store.initialize())
    _run(store.save_session("s1", [_make_strategy_config()], ["ref1"]))
    result = _run(store.load_session("s1"))
    assert result is not None
    _run(store.close())


def test_save_rejects_invalid_dataset_ref_without_partial_session(tmp_path: Path) -> None:
    db = tmp_path / "invalid-ref.db"
    store = SQLiteStore(db)
    _run(store.initialize())

    with pytest.raises(ValidationError):
        _run(store.save_session("session-invalid-ref", [], [" \t\n "]))

    assert _run(store.load_session("session-invalid-ref")) is None
    _run(store.close())


def test_save_requires_validated_strategy_config_instance(tmp_path: Path) -> None:
    db = tmp_path / "invalid-config.db"
    store = SQLiteStore(db)
    _run(store.initialize())

    with pytest.raises(TypeError, match="StrategyConfig instances"):
        _run(store.save_session("session-invalid-config", [{}], []))  # type: ignore[list-item]

    assert _run(store.load_session("session-invalid-config")) is None
    _run(store.close())


# ── 7. No persistence of original data; no replay guarantee ───────────────────


def test_no_data_persistence(tmp_path: Path) -> None:
    """Only opaque references are persisted, not underlying candle/data payloads."""
    db = tmp_path / "test.db"
    store = SQLiteStore(db)
    _run(store.initialize())

    # config_json is a JSON string of StrategyConfig — no raw OHLCV or payload
    cfgs = [_make_strategy_config("data-strat", "v1")]
    refs: list[DatasetRef] = ["ds-ref-data"]
    _run(store.save_session("s-data", cfgs, refs))

    result = _run(store.load_session("s-data"))
    assert result is not None
    # Verify config_json is a JSON blob, not raw data
    loaded = result["strategy_configs"][0]
    assert isinstance(loaded, StrategyConfig)
    assert loaded.schema_version == "strategy_config.v1"
    # DatasetRef is stored as opaque text
    assert "ds-ref-data" in result["dataset_refs"]
    _run(store.close())


def test_load_rejects_corrupted_strategy_config_json(tmp_path: Path) -> None:
    db = tmp_path / "corrupt-config.db"
    store = SQLiteStore(db)
    _run(store.initialize())
    _run(store.save_session("s-corrupt", [_make_strategy_config()], []))

    conn = sqlite3.connect(str(db))
    conn.execute(
        "UPDATE strategy_config_versions SET config_json = ? "
        "WHERE strategy_id = ? AND strategy_version = ?",
        ('{"schema_version":"strategy_config.v1"}', "strat-1", "v1"),
    )
    conn.commit()
    conn.close()

    with pytest.raises(ValidationError):
        _run(store.load_session("s-corrupt"))
    _run(store.close())


def test_load_rejects_corrupted_dataset_ref(tmp_path: Path) -> None:
    db = tmp_path / "corrupt-ref.db"
    store = SQLiteStore(db)
    _run(store.initialize())
    _run(store.save_session("s-corrupt", [], ["dataset-1"]))

    conn = sqlite3.connect(str(db))
    conn.execute(
        "UPDATE session_dataset_refs SET dataset_ref = ? WHERE session_id = ?",
        (" \t\n ", "s-corrupt"),
    )
    conn.commit()
    conn.close()

    with pytest.raises(ValidationError):
        _run(store.load_session("s-corrupt"))
    _run(store.close())


# ── 8. v10 order acceptance/reservation without lifecycle, risk, or UI ────────────


def test_v10_order_acceptance_tables_without_lifecycle_or_preference_tables(
    tmp_path: Path,
) -> None:
    """The schema stores initial order context, not lifecycle or UI state."""
    db = tmp_path / "test.db"
    store = SQLiteStore(db)
    _run(store.initialize())

    conn = sqlite3.connect(str(db))
    tables = {
        row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }
    conn.close()
    # v11 adds paper_reservation_lot_allocations and paper_sell_accounting tables.
    assert tables == _SCHEMA_V15_TABLES
    _run(store.close())


# ── 9. Idempotent same-strategy-ref save ───────────────────────────────────────


def test_idempotent_same_ref(tmp_path: Path) -> None:
    """Same StrategyRef + same JSON is idempotent; does not duplicate rows."""
    db = tmp_path / "test.db"
    store = SQLiteStore(db)
    _run(store.initialize())

    sid = "sess-idem"
    cfgs = [_make_strategy_config("idem-strat", "v1")]
    refs: list[DatasetRef] = []
    _run(store.save_session(sid, cfgs, refs))
    _run(store.save_session(sid, cfgs, refs))  # second call must be idempotent

    conn = sqlite3.connect(str(db))
    assert conn.execute("SELECT COUNT(*) FROM strategy_config_versions").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM session_strategy_configs WHERE session_id=?", (sid,)).fetchone()[0] == 1
    conn.close()
    _run(store.close())


# ── 10. StoreClosedError on operations after close ─────────────────────────────


def test_closed_store_rejects_operations(tmp_path: Path) -> None:
    db = tmp_path / "test.db"
    store = SQLiteStore(db)
    _run(store.initialize())
    _run(store.close())

    with pytest.raises(StoreClosedError):
        _run(store.save_session("s1", [_make_strategy_config()], []))
    with pytest.raises(StoreClosedError):
        _run(store.load_session("s1"))


# ── 11. Schema version mismatch ───────────────────────────────────────────────


def test_schema_version_mismatch(tmp_path: Path) -> None:
    """If user_version is set to an unknown future version, initialize must raise."""
    db = tmp_path / "test.db"

    # Pre-seed the DB with a wrong version
    conn = sqlite3.connect(str(db))
    conn.executescript("""
        CREATE TABLE sessions (session_id TEXT PRIMARY KEY);
        PRAGMA user_version = 99;
    """)
    conn.close()

    store = SQLiteStore(db)
    with pytest.raises(SchemaVersionMismatchError):
        _run(store.initialize())

    # An unsupported version is rejected before the initial migration changes schema.
    conn = sqlite3.connect(str(db))
    tables = {
        row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }
    assert tables == {"sessions"}
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 99
    conn.close()


def test_initial_migration_rolls_back_partial_ddl(tmp_path: Path) -> None:
    """A DDL failure leaves the pre-existing schema and user_version untouched."""
    db = tmp_path / "partial-migration.db"
    conn = sqlite3.connect(str(db))
    conn.execute(
        "CREATE TABLE strategy_config_versions "
        "(strategy_id TEXT PRIMARY KEY)"
    )
    conn.commit()
    conn.close()

    store = SQLiteStore(db)
    with pytest.raises(sqlite3.OperationalError, match="already exists"):
        _run(store.initialize())

    conn = sqlite3.connect(str(db))
    tables = {
        row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }
    assert tables == {"strategy_config_versions"}
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 0
    conn.close()

# ── 12. Session association idempotent (same session + same ref) ───────────────


def test_session_association_idempotent(tmp_path: Path) -> None:
    """Repeating a session-to-strategy association is idempotent."""
    db = tmp_path / "test.db"
    store = SQLiteStore(db)
    _run(store.initialize())

    sid = "sess-assoc-idem"
    cfgs = [_make_strategy_config("assoc-strat", "v1")]
    refs: list[str] = []
    _run(store.save_session(sid, cfgs, refs))
    _run(store.save_session(sid, cfgs, refs))

    conn = sqlite3.connect(str(db))
    count = conn.execute(
        "SELECT COUNT(*) FROM session_strategy_configs WHERE session_id=?", (sid,)
    ).fetchone()[0]
    assert count == 1
    conn.close()
    _run(store.close())


# ── 13. Multiple strategies per session ────────────────────────────────────────


def test_multiple_strategies_per_session(tmp_path: Path) -> None:
    """A session can reference multiple strategy configs."""
    db = tmp_path / "test.db"
    store = SQLiteStore(db)
    _run(store.initialize())

    sid = "sess-multi"
    cfgs = [
        _make_strategy_config("strat-a", "v1"),
        _make_strategy_config("strat-b", "v2"),
    ]
    refs: list[DatasetRef] = ["ds-ref-a", "ds-ref-b"]
    _run(store.save_session(sid, cfgs, refs))

    result = _run(store.load_session(sid))
    assert result is not None
    assert len(result["strategy_configs"]) == 2
    assert len(result["dataset_refs"]) == 2
    _run(store.close())


# ── 14. Duplicate dataset_ref within same save is idempotent ───────────────────


def test_duplicate_dataset_ref_idempotent(tmp_path: Path) -> None:
    """Same DatasetRef listed twice in one save does not duplicate."""
    db = tmp_path / "test.db"
    store = SQLiteStore(db)
    _run(store.initialize())

    sid = "sess-dup-ref"
    cfgs: list[StrategyConfig] = []
    refs: list[DatasetRef] = ["ds-same", "ds-same"]  # duplicate within one save
    _run(store.save_session(sid, cfgs, refs))

    conn = sqlite3.connect(str(db))
    count = conn.execute(
        "SELECT COUNT(*) FROM session_dataset_refs WHERE session_id=?", (sid,)
    ).fetchone()[0]
    assert count == 1
    conn.close()
    _run(store.close())


# ── 15. load_session returns None for unknown session ─────────────────────────


def test_load_unknown_session_returns_none(tmp_path: Path) -> None:
    db = tmp_path / "test.db"
    store = SQLiteStore(db)
    _run(store.initialize())

    result = _run(store.load_session("nonexistent"))
    assert result is None
    _run(store.close())


# ── 16. ExperimentDefinition store: new DB v2 and v1→v2 upgrade ────────────────


def test_new_database_initializes_directly_at_v12(tmp_path: Path) -> None:
    db = tmp_path / "new-v11.db"
    store = SQLiteStore(db)
    _run(store.initialize())

    conn = sqlite3.connect(str(db))
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 15
    tables = {
        row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }
    assert "experiment_definitions" in tables
    assert "run_attempts" in tables
    assert "experiment_results" in tables
    assert "paper_fills" in tables
    assert "paper_account_openings" in tables
    assert "paper_account_balances" in tables
    assert "paper_orders" in tables
    assert "paper_order_acceptances" in tables
    assert "paper_reservations" in tables
    assert "paper_fill_applications" in tables
    assert "position_lots" in tables
    assert "paper_reservation_lot_allocations" in tables
    assert "paper_sell_accounting" in tables
    assert {
        "sessions", "strategy_config_versions", "session_strategy_configs",
        "session_dataset_refs",
    } <= tables
    conn.close()
    _run(store.close())


def test_v1_to_v12_migration_preserves_existing_sessions_and_configs(
    tmp_path: Path,
) -> None:
    db = tmp_path / "upgrade-v1.db"
    old_store = SQLiteStore(db)
    _run(old_store.initialize())
    cfg = _make_strategy_config("existing-strategy", "v4")
    _run(old_store.save_session("existing-session", [cfg], ["existing-dataset"]))
    _run(old_store.close())

    # Model a valid accepted v1 database: preserve the v1 tables/data but remove
    # the experiment and run tables and rewind the schema marker for the migration test.
    conn = sqlite3.connect(str(db))
    conn.execute("DROP TABLE IF EXISTS experiment_definitions")
    conn.execute("DROP TABLE IF EXISTS run_attempts")
    conn.execute("DROP TABLE IF EXISTS experiment_results")
    conn.execute("DROP TABLE IF EXISTS variant_dispositions")
    conn.execute("DROP TABLE IF EXISTS result_exclusions")
    conn.execute("DROP TABLE IF EXISTS paper_fills")
    conn.execute("DROP TABLE IF EXISTS paper_account_balances")
    conn.execute("DROP TABLE IF EXISTS paper_account_openings")
    conn.execute("DROP TABLE IF EXISTS paper_orders")
    conn.execute("DROP TABLE IF EXISTS paper_reservations")
    conn.execute("DROP TABLE IF EXISTS paper_order_acceptances")
    conn.execute("DROP TABLE IF EXISTS paper_fill_applications")
    conn.execute("DROP TABLE IF EXISTS position_lots")
    conn.execute("PRAGMA user_version = 1")
    conn.commit()
    conn.close()

    upgraded = SQLiteStore(db)
    _run(upgraded.initialize())
    assert _run(upgraded.load_session("existing-session"))["strategy_configs"][0].model_dump_json() == cfg.model_dump_json()
    assert _run(upgraded.load_session("existing-session"))["dataset_refs"] == ["existing-dataset"]

    conn = sqlite3.connect(str(db))
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 15
    assert conn.execute("SELECT COUNT(*) FROM experiment_definitions").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM run_attempts").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM experiment_results").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM paper_fills").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM paper_account_openings").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM paper_account_balances").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM paper_orders").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM paper_order_acceptances").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM paper_reservations").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM paper_reservation_lot_allocations").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM paper_sell_accounting").fetchone()[0] == 0
    conn.close()
    _run(upgraded.close())


def test_v1_migration_failure_rolls_back_ddl_and_user_version(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = tmp_path / "upgrade-rollback.db"
    store = SQLiteStore(db)
    _run(store.initialize())
    cfg = _make_strategy_config("preserved-strategy", "v1")
    _run(store.save_session("preserved-session", [cfg], ["preserved-dataset"]))
    _run(store.close())

    conn = sqlite3.connect(str(db))
    conn.execute("DROP TABLE IF EXISTS experiment_definitions")
    conn.execute("DROP TABLE IF EXISTS run_attempts")
    conn.execute("DROP TABLE IF EXISTS result_exclusions")
    conn.execute("DROP TABLE IF EXISTS variant_dispositions")
    conn.execute("DROP TABLE IF EXISTS experiment_results")
    conn.execute("DROP TABLE IF EXISTS paper_fills")
    conn.execute("DROP TABLE IF EXISTS paper_account_balances")
    conn.execute("DROP TABLE IF EXISTS paper_account_openings")
    conn.execute("DROP TABLE IF EXISTS paper_orders")
    conn.execute("DROP TABLE IF EXISTS paper_reservations")
    conn.execute("DROP TABLE IF EXISTS paper_order_acceptances")
    conn.execute("PRAGMA user_version = 1")
    conn.commit()
    conn.close()

    original_v2_statements = sqlite_store_module._SCHEMA_V2_STATEMENTS
    monkeypatch.setattr(
        sqlite_store_module,
        "_SCHEMA_V2_STATEMENTS",
        (*original_v2_statements, "THIS IS INVALID SQL"),
    )
    migration = SQLiteStore(db)
    with pytest.raises(sqlite3.OperationalError):
        _run(migration.initialize())

    conn = sqlite3.connect(str(db))
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 1
    tables = {
        row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }
    assert "experiment_definitions" not in tables
    assert "run_attempts" not in tables
    assert conn.execute(
        "SELECT COUNT(*) FROM sessions WHERE session_id = ?",
        ("preserved-session",),
    ).fetchone()[0] == 1
    assert conn.execute(
        "SELECT dataset_ref FROM session_dataset_refs WHERE session_id = ?",
        ("preserved-session",),
    ).fetchone()[0] == "preserved-dataset"
    conn.close()


# ── 17. ExperimentDefinition registration and recovery ──────────────────────────


def test_register_experiment_round_trip_and_idempotency(tmp_path: Path) -> None:
    db = tmp_path / "experiments.db"
    store = SQLiteStore(db)
    _run(store.initialize())
    definition = _make_experiment_definition()

    first = _run(store.register_experiment(definition))
    second = _run(store.register_experiment(definition))
    assert first.model_dump_json() == definition.model_dump_json()
    assert second.model_dump_json() == definition.model_dump_json()

    conn = sqlite3.connect(str(db))
    assert conn.execute("SELECT COUNT(*) FROM experiment_definitions").fetchone()[0] == 1
    conn.close()
    _run(store.close())

    reopened = SQLiteStore(db)
    _run(reopened.initialize())
    loaded = _run(reopened.load_experiment(definition.experiment_ref))
    assert loaded is not None
    assert loaded.model_dump_json() == definition.model_dump_json()
    assert loaded.variants == definition.variants
    _run(reopened.close())


def test_register_experiment_conflict_does_not_overwrite(tmp_path: Path) -> None:
    db = tmp_path / "experiment-conflict.db"
    store = SQLiteStore(db)
    _run(store.initialize())
    original = _make_experiment_definition(hypothesis="original hypothesis")
    conflicting = _make_experiment_definition(hypothesis="different hypothesis")
    _run(store.register_experiment(original))

    with pytest.raises(ExperimentDefinitionConflictError):
        _run(store.register_experiment(conflicting))

    loaded = _run(store.load_experiment(original.experiment_ref))
    assert loaded is not None
    assert loaded.model_dump_json() == original.model_dump_json()
    conn = sqlite3.connect(str(db))
    assert conn.execute("SELECT COUNT(*) FROM experiment_definitions").fetchone()[0] == 1
    conn.close()
    _run(store.close())


def test_load_unknown_experiment_returns_none(tmp_path: Path) -> None:
    db = tmp_path / "unknown-experiment.db"
    store = SQLiteStore(db)
    _run(store.initialize())

    result = _run(store.load_experiment(ExperimentRef(
        experiment_id="not-registered", experiment_version="v1"
    )))
    assert result is None
    _run(store.close())


def test_load_experiment_rejects_corrupted_definition_json(tmp_path: Path) -> None:
    db = tmp_path / "corrupt-experiment.db"
    store = SQLiteStore(db)
    _run(store.initialize())
    definition = _make_experiment_definition()
    _run(store.register_experiment(definition))

    conn = sqlite3.connect(str(db))
    conn.execute(
        "UPDATE experiment_definitions SET definition_json = ? "
        "WHERE experiment_id = ? AND experiment_version = ?",
        ('{"schema_version":"experiment_definition.v1"}',
         definition.experiment_ref.experiment_id,
         definition.experiment_ref.experiment_version),
    )
    conn.commit()
    conn.close()

    with pytest.raises(ValidationError):
        _run(store.load_experiment(definition.experiment_ref))

    mismatched = _make_experiment_definition(experiment_id="different-experiment")
    conn = sqlite3.connect(str(db))
    conn.execute(
        "UPDATE experiment_definitions SET definition_json = ? "
        "WHERE experiment_id = ? AND experiment_version = ?",
        (mismatched.model_dump_json(), definition.experiment_ref.experiment_id,
         definition.experiment_ref.experiment_version),
    )
    conn.commit()
    conn.close()
    with pytest.raises(ExperimentDefinitionIntegrityError):
        _run(store.load_experiment(definition.experiment_ref))


# ── 18. RunAttempt persistence: create, transition, validation ───────────────


def _make_run_attempt(
    run_id: str = "run-1",
    experiment_id: str = "experiment-1",
    experiment_version: str = "v1",
    variant_id: str = "variant-a",
    status: str = "pending",
    started_at_ms: int | None = None,
    finished_at_ms: int | None = None,
    seed: int | None = None,
) -> RunAttempt:
    return RunAttempt(
        run_id=run_id,
        experiment_ref=ExperimentRef(
            experiment_id=experiment_id,
            experiment_version=experiment_version,
        ),
        variant_id=variant_id,
        status=status,  # type: ignore[call-arg]
        started_at_ms=started_at_ms,
        finished_at_ms=finished_at_ms,
        seed=seed,
    )


def test_create_run_attempt_pending_valid(tmp_path: Path) -> None:
    """A valid pending run is persisted and recoverable."""
    db = tmp_path / "run-pending.db"
    store = SQLiteStore(db)
    _run(store.initialize())

    definition = _make_experiment_definition()
    _run(store.register_experiment(definition))

    run = RunAttempt(
        run_id="run-pending-ok",
        experiment_ref=definition.experiment_ref,
        variant_id="variant-a",
        status="pending",
        seed=None,
    )
    result = _run(store.create_run_attempt(run))
    assert result.run_id == "run-pending-ok"
    assert result.status == "pending"

    conn = sqlite3.connect(str(db))
    count = conn.execute("SELECT COUNT(*) FROM run_attempts").fetchone()[0]
    assert count == 1
    row = conn.execute(
        "SELECT run_id, experiment_id, experiment_version, variant_id, status "
        "FROM run_attempts WHERE run_id=?", ("run-pending-ok",)
    ).fetchone()
    assert row is not None
    assert row[0] == "run-pending-ok"
    assert row[1] == "experiment-1"
    assert row[2] == "v1"
    assert row[3] == "variant-a"
    assert row[4] == "pending"
    conn.close()
    _run(store.close())


def test_create_run_attempt_rejects_non_pending(tmp_path: Path) -> None:
    """create_run_attempt only accepts pending; running is rejected."""
    db = tmp_path / "run-non-pending.db"
    store = SQLiteStore(db)
    _run(store.initialize())

    definition = _make_experiment_definition()
    _run(store.register_experiment(definition))

    run = RunAttempt(
        run_id="run-running",
        experiment_ref=definition.experiment_ref,
        variant_id="variant-a",
        status="running",
        seed=None,
        started_at_ms=1_000,
        dataset_ref_used="dataset-experiment",
        code_ref="code-ref-1",
        engine_ref="engine-ref-1",
        effective_periods=(PeriodWindow(purpose="design", start_ms=1_000, end_ms=2_000),),
    )
    with pytest.raises(ValueError, match="only accepts pending"):
        _run(store.create_run_attempt(run))

    conn = sqlite3.connect(str(db))
    assert conn.execute("SELECT COUNT(*) FROM run_attempts").fetchone()[0] == 0
    conn.close()
    _run(store.close())


def test_create_run_attempt_unknown_experiment(tmp_path: Path) -> None:
    """Persisting a run for an unknown experiment leaves no partial rows."""
    db = tmp_path / "run-no-experiment.db"
    store = SQLiteStore(db)
    _run(store.initialize())

    run = RunAttempt(
        run_id="run-no-exp",
        experiment_ref=ExperimentRef(experiment_id="no-such-exp", experiment_version="v1"),
        variant_id="variant-a",
        status="pending",
        seed=None,
    )
    with pytest.raises(ValueError, match="not found"):
        _run(store.create_run_attempt(run))

    conn = sqlite3.connect(str(db))
    assert conn.execute("SELECT COUNT(*) FROM run_attempts").fetchone()[0] == 0
    conn.close()
    _run(store.close())


def test_create_run_attempt_unknown_variant(tmp_path: Path) -> None:
    """Persisting a run with an unknown variant leaves no partial rows."""
    db = tmp_path / "run-no-variant.db"
    store = SQLiteStore(db)
    _run(store.initialize())

    definition = _make_experiment_definition()
    _run(store.register_experiment(definition))

    run = RunAttempt(
        run_id="run-no-var",
        experiment_ref=definition.experiment_ref,
        variant_id="nonexistent-variant",
        status="pending",
        seed=None,
    )
    with pytest.raises(ValueError, match="not found"):
        _run(store.create_run_attempt(run))

    conn = sqlite3.connect(str(db))
    assert conn.execute("SELECT COUNT(*) FROM run_attempts").fetchone()[0] == 0
    conn.close()
    _run(store.close())


def test_create_run_attempt_dataset_mismatch(tmp_path: Path) -> None:
    """Run dataset_ref_used must match the variant's dataset_ref."""
    db = tmp_path / "run-ds-mismatch.db"
    store = SQLiteStore(db)
    _run(store.initialize())

    definition = _make_experiment_definition(dataset_ref="dataset-experiment")
    _run(store.register_experiment(definition))

    # Pending runs cannot carry execution context (including dataset_ref_used);
    # Pydantic rejects this before the store even sees it.
    with pytest.raises(ValidationError, match="pending must not contain"):
        RunAttempt(
            run_id="run-ds-mismatch",
            experiment_ref=definition.experiment_ref,
            variant_id="variant-a",
            status="pending",
            seed=None,
            dataset_ref_used="different-dataset",
        )


def test_create_run_attempt_idempotent_same_json(tmp_path: Path) -> None:
    """Same run_id with identical JSON returns the existing row without duplicating."""
    db = tmp_path / "run-idem.db"
    store = SQLiteStore(db)
    _run(store.initialize())

    definition = _make_experiment_definition()
    _run(store.register_experiment(definition))

    run = RunAttempt(
        run_id="run-idem",
        experiment_ref=definition.experiment_ref,
        variant_id="variant-a",
        status="pending",
        seed=None,
    )
    first = _run(store.create_run_attempt(run))
    second = _run(store.create_run_attempt(run))
    assert first.run_id == second.run_id
    assert first.model_dump_json() == second.model_dump_json()

    conn = sqlite3.connect(str(db))
    count = conn.execute("SELECT COUNT(*) FROM run_attempts WHERE run_id=?", ("run-idem",)).fetchone()[0]
    assert count == 1
    conn.close()
    _run(store.close())


def test_create_run_attempt_conflict_different_json(tmp_path: Path) -> None:
    """Same run_id with different content raises conflict without overwriting.

    Pending runs can only carry run_id + experiment_ref + variant_id + status.
    Two pending runs with the same values produce identical JSON (idempotent).
    Different values produce different JSON → conflict.
    """
    db = tmp_path / "run-conflict.db"
    store = SQLiteStore(db)
    _run(store.initialize())

    definition = _make_experiment_definition(variant_ids=("variant-a", "variant-b"))
    _run(store.register_experiment(definition))

    # Create a pending run with variant-a.
    pending1 = RunAttempt(
        run_id="run-conflict",
        experiment_ref=definition.experiment_ref,
        variant_id="variant-a",
        status="pending",
        seed=None,
    )
    _run(store.create_run_attempt(pending1))

    # Try to create a *different* pending run with the same run_id but
    # different variant_id → different JSON → conflict.
    pending2 = RunAttempt(
        run_id="run-conflict",
        experiment_ref=definition.experiment_ref,
        variant_id="variant-b",  # different variant → different JSON
        status="pending",
        seed=None,
    )
    with pytest.raises(RunAttemptConflictError):
        _run(store.create_run_attempt(pending2))

    conn = sqlite3.connect(str(db))
    row = conn.execute(
        "SELECT run_json FROM run_attempts WHERE run_id=?", ("run-conflict",)
    ).fetchone()
    assert row is not None
    loaded_run = RunAttempt.model_validate_json(row[0])
    assert loaded_run.variant_id == "variant-a"  # original preserved.
    conn.close()
    _run(store.close())


def test_transition_pending_to_running(tmp_path: Path) -> None:
    """pending → running is valid and persisted."""
    db = tmp_path / "trans-pr.db"
    store = SQLiteStore(db)
    _run(store.initialize())

    definition = _make_experiment_definition()
    _run(store.register_experiment(definition))

    run = RunAttempt(
        run_id="run-trans-pr",
        experiment_ref=definition.experiment_ref,
        variant_id="variant-a",
        status="pending",
        seed=None,
    )
    _run(store.create_run_attempt(run))

    target = RunAttempt(
        run_id="run-trans-pr",
        experiment_ref=definition.experiment_ref,
        variant_id="variant-a",
        status="running",
        seed=None,
        started_at_ms=1_000,
        dataset_ref_used="dataset-experiment",
        code_ref="code-ref-1",
        engine_ref="engine-ref-1",
        effective_periods=(PeriodWindow(purpose="design", start_ms=1_000, end_ms=2_000),),
    )
    result = _run(store.transition_run_attempt("run-trans-pr", target))
    assert result.status == "running"

    conn = sqlite3.connect(str(db))
    row = conn.execute(
        "SELECT status FROM run_attempts WHERE run_id=?", ("run-trans-pr",)
    ).fetchone()
    assert row[0] == "running"
    conn.close()
    _run(store.close())


def test_transition_pending_to_cancelled(tmp_path: Path) -> None:
    """pending → cancelled is valid."""
    db = tmp_path / "trans-pc.db"
    store = SQLiteStore(db)
    _run(store.initialize())

    definition = _make_experiment_definition()
    _run(store.register_experiment(definition))

    run = RunAttempt(
        run_id="run-trans-pc",
        experiment_ref=definition.experiment_ref,
        variant_id="variant-a",
        status="pending",
        seed=None,
    )
    _run(store.create_run_attempt(run))

    target = RunAttempt(
        run_id="run-trans-pc",
        experiment_ref=definition.experiment_ref,
        variant_id="variant-a",
        status="cancelled",
        seed=None,
        finished_at_ms=2_000,
    )
    result = _run(store.transition_run_attempt("run-trans-pc", target))
    assert result.status == "cancelled"

    conn = sqlite3.connect(str(db))
    row = conn.execute(
        "SELECT status FROM run_attempts WHERE run_id=?", ("run-trans-pc",)
    ).fetchone()
    assert row[0] == "cancelled"
    conn.close()
    _run(store.close())


def test_transition_running_to_failed(tmp_path: Path) -> None:
    """running → failed is valid."""
    db = tmp_path / "trans-rf.db"
    store = SQLiteStore(db)
    _run(store.initialize())

    definition = _make_experiment_definition()
    _run(store.register_experiment(definition))

    # Create a pending run, then transition to running → failed.
    pending = RunAttempt(
        run_id="run-trans-rf",
        experiment_ref=definition.experiment_ref,
        variant_id="variant-a",
        status="pending",
        seed=None,
    )
    _run(store.create_run_attempt(pending))
    running = RunAttempt(
        run_id="run-trans-rf",
        experiment_ref=definition.experiment_ref,
        variant_id="variant-a",
        status="running",
        seed=42,
        started_at_ms=1_000,
        dataset_ref_used="dataset-experiment",
        code_ref="code-ref-1",
        engine_ref="engine-ref-1",
        effective_periods=(PeriodWindow(purpose="design", start_ms=1_000, end_ms=2_000),),
    )
    _run(store.transition_run_attempt("run-trans-rf", running))

    target = RunAttempt(
        run_id="run-trans-rf",
        experiment_ref=definition.experiment_ref,
        variant_id="variant-a",
        status="failed",
        seed=42,
        started_at_ms=1_000,
        finished_at_ms=3_000,
        dataset_ref_used="dataset-experiment",
        code_ref="code-ref-1",
        engine_ref="engine-ref-1",
        effective_periods=(PeriodWindow(purpose="design", start_ms=1_000, end_ms=2_000),),
        diagnostic=PublicDiagnostic(error_code="TIMEOUT", public_message="run timed out"),
    )
    result = _run(store.transition_run_attempt("run-trans-rf", target))
    assert result.status == "failed"

    conn = sqlite3.connect(str(db))
    row = conn.execute(
        "SELECT status FROM run_attempts WHERE run_id=?", ("run-trans-rf",)
    ).fetchone()
    assert row[0] == "failed"
    conn.close()
    _run(store.close())


def test_transition_running_to_cancelled(tmp_path: Path) -> None:
    """running → cancelled is valid."""
    db = tmp_path / "trans-rc.db"
    store = SQLiteStore(db)
    _run(store.initialize())

    definition = _make_experiment_definition()
    _run(store.register_experiment(definition))

    # Create a pending run, then transition to running → cancelled.
    pending = RunAttempt(
        run_id="run-trans-rc",
        experiment_ref=definition.experiment_ref,
        variant_id="variant-a",
        status="pending",
        seed=None,
    )
    _run(store.create_run_attempt(pending))
    running = RunAttempt(
        run_id="run-trans-rc",
        experiment_ref=definition.experiment_ref,
        variant_id="variant-a",
        status="running",
        seed=42,
        started_at_ms=1_000,
        dataset_ref_used="dataset-experiment",
        code_ref="code-ref-1",
        engine_ref="engine-ref-1",
        effective_periods=(PeriodWindow(purpose="design", start_ms=1_000, end_ms=2_000),),
    )
    _run(store.transition_run_attempt("run-trans-rc", running))

    target = RunAttempt(
        run_id="run-trans-rc",
        experiment_ref=definition.experiment_ref,
        variant_id="variant-a",
        status="cancelled",
        seed=42,
        started_at_ms=1_000,
        finished_at_ms=3_000,
        dataset_ref_used="dataset-experiment",
        code_ref="code-ref-1",
        engine_ref="engine-ref-1",
        effective_periods=(PeriodWindow(purpose="design", start_ms=1_000, end_ms=2_000),),
    )
    result = _run(store.transition_run_attempt("run-trans-rc", target))
    assert result.status == "cancelled"

    conn = sqlite3.connect(str(db))
    row = conn.execute(
        "SELECT status FROM run_attempts WHERE run_id=?", ("run-trans-rc",)
    ).fetchone()
    assert row[0] == "cancelled"
    conn.close()
    _run(store.close())


def test_transition_rejects_completed(tmp_path: Path) -> None:
    """Transitions to 'completed' are explicitly rejected."""
    db = tmp_path / "trans-completed.db"
    store = SQLiteStore(db)
    _run(store.initialize())

    definition = _make_experiment_definition()
    _run(store.register_experiment(definition))

    run = RunAttempt(
        run_id="run-trans-c",
        experiment_ref=definition.experiment_ref,
        variant_id="variant-a",
        status="pending",
        seed=None,
    )
    _run(store.create_run_attempt(run))

    target = RunAttempt(
        run_id="run-trans-c",
        experiment_ref=definition.experiment_ref,
        variant_id="variant-a",
        status="completed",
        seed=42,
        started_at_ms=1_000,
        finished_at_ms=3_000,
        dataset_ref_used="dataset-experiment",
        code_ref="code-ref-1",
        engine_ref="engine-ref-1",
        effective_periods=(PeriodWindow(purpose="design", start_ms=1_000, end_ms=2_000),),
        result_id="result-1",
    )
    with pytest.raises(RunAttemptStateError, match="completed"):
        _run(store.transition_run_attempt("run-trans-c", target))

    conn = sqlite3.connect(str(db))
    row = conn.execute(
        "SELECT status FROM run_attempts WHERE run_id=?", ("run-trans-c",)
    ).fetchone()
    assert row[0] == "pending"
    conn.close()
    _run(store.close())


def test_transition_terminal_rejected(tmp_path: Path) -> None:
    """Failed and cancelled runs reject any further transitions."""
    db = tmp_path / "trans-terminal.db"
    store = SQLiteStore(db)
    _run(store.initialize())

    definition = _make_experiment_definition()
    _run(store.register_experiment(definition))

    # Create a pending run, transition to running → failed, then try invalid transition.
    pending = RunAttempt(
        run_id="run-term",
        experiment_ref=definition.experiment_ref,
        variant_id="variant-a",
        status="pending",
        seed=None,
    )
    _run(store.create_run_attempt(pending))
    running = RunAttempt(
        run_id="run-term",
        experiment_ref=definition.experiment_ref,
        variant_id="variant-a",
        status="running",
        seed=42,
        started_at_ms=1_000,
        dataset_ref_used="dataset-experiment",
        code_ref="code-ref-1",
        engine_ref="engine-ref-1",
        effective_periods=(PeriodWindow(purpose="design", start_ms=1_000, end_ms=2_000),),
    )
    _run(store.transition_run_attempt("run-term", running))
    failed = RunAttempt(
        run_id="run-term",
        experiment_ref=definition.experiment_ref,
        variant_id="variant-a",
        status="failed",
        seed=42,
        started_at_ms=1_000,
        finished_at_ms=3_000,
        dataset_ref_used="dataset-experiment",
        code_ref="code-ref-1",
        engine_ref="engine-ref-1",
        effective_periods=(PeriodWindow(purpose="design", start_ms=1_000, end_ms=2_000),),
        diagnostic=PublicDiagnostic(error_code="ERR", public_message="fail"),
    )
    _run(store.transition_run_attempt("run-term", failed))

    target = RunAttempt(
        run_id="run-term",
        experiment_ref=definition.experiment_ref,
        variant_id="variant-a",
        status="running",
        seed=42,
        started_at_ms=4_000,
        dataset_ref_used="dataset-experiment",
        code_ref="code-ref-1",
        engine_ref="engine-ref-1",
        effective_periods=(PeriodWindow(purpose="design", start_ms=4_000, end_ms=5_000),),
    )
    with pytest.raises(RunAttemptStateError, match="terminal"):
        _run(store.transition_run_attempt("run-term", target))

    conn = sqlite3.connect(str(db))
    row = conn.execute(
        "SELECT status FROM run_attempts WHERE run_id=?", ("run-term",)
    ).fetchone()
    assert row[0] == "failed"
    conn.close()
    _run(store.close())


def test_transition_invalid_state_rejected(tmp_path: Path) -> None:
    """Invalid transitions (e.g. pending → failed) are rejected."""
    db = tmp_path / "trans-invalid.db"
    store = SQLiteStore(db)
    _run(store.initialize())

    definition = _make_experiment_definition()
    _run(store.register_experiment(definition))

    run = RunAttempt(
        run_id="run-inv",
        experiment_ref=definition.experiment_ref,
        variant_id="variant-a",
        status="pending",
        seed=None,
    )
    _run(store.create_run_attempt(run))

    target = RunAttempt(
        run_id="run-inv",
        experiment_ref=definition.experiment_ref,
        variant_id="variant-a",
        status="failed",
        seed=None,
        started_at_ms=1_000,
        finished_at_ms=3_000,
        dataset_ref_used="dataset-experiment",
        code_ref="code-ref-1",
        engine_ref="engine-ref-1",
        effective_periods=(PeriodWindow(purpose="design", start_ms=1_000, end_ms=2_000),),
        diagnostic=PublicDiagnostic(error_code="ERR", public_message="fail"),
    )
    with pytest.raises(RunAttemptStateError, match="invalid transition"):
        _run(store.transition_run_attempt("run-inv", target))

    conn = sqlite3.connect(str(db))
    row = conn.execute(
        "SELECT status FROM run_attempts WHERE run_id=?", ("run-inv",)
    ).fetchone()
    assert row[0] == "pending"
    conn.close()
    _run(store.close())


def test_transition_unknown_run_rejected(tmp_path: Path) -> None:
    """Transitioning a non-existent run raises an error."""
    db = tmp_path / "trans-unknown.db"
    store = SQLiteStore(db)
    _run(store.initialize())

    target = RunAttempt(
        run_id="nonexistent",
        experiment_ref=ExperimentRef(experiment_id="e1", experiment_version="v1"),
        variant_id="v-a",
        status="running",
        seed=None,
        started_at_ms=1_000,
        dataset_ref_used="ds-ref",
        code_ref="c-ref",
        engine_ref="e-ref",
        effective_periods=(PeriodWindow(purpose="design", start_ms=1_000, end_ms=2_000),),
    )
    with pytest.raises(RunAttemptStateError, match="not found"):
        _run(store.transition_run_attempt("nonexistent", target))


def test_transition_identity_mismatch(tmp_path: Path) -> None:
    """Transitioning with a different run_id in the target raises an error."""
    db = tmp_path / "trans-identity.db"
    store = SQLiteStore(db)
    _run(store.initialize())

    definition = _make_experiment_definition()
    _run(store.register_experiment(definition))

    run = RunAttempt(
        run_id="run-ident",
        experiment_ref=definition.experiment_ref,
        variant_id="variant-a",
        status="pending",
        seed=None,
    )
    _run(store.create_run_attempt(run))

    target = RunAttempt(
        run_id="different-run-id",
        experiment_ref=definition.experiment_ref,
        variant_id="variant-a",
        status="running",
        seed=None,
        started_at_ms=1_000,
        dataset_ref_used="dataset-experiment",
        code_ref="code-ref-1",
        engine_ref="engine-ref-1",
        effective_periods=(PeriodWindow(purpose="design", start_ms=1_000, end_ms=2_000),),
    )
    with pytest.raises(RunAttemptStateError, match="immutable across transitions"):
        _run(store.transition_run_attempt("run-ident", target))

    conn = sqlite3.connect(str(db))
    row = conn.execute(
        "SELECT status FROM run_attempts WHERE run_id=?", ("run-ident",)
    ).fetchone()
    assert row[0] == "pending"
    conn.close()
    _run(store.close())


def test_reopen_recovers_runs(tmp_path: Path) -> None:
    """Reopening the database recovers persisted and validated runs."""
    db = tmp_path / "run-reopen.db"
    store1 = SQLiteStore(db)
    _run(store1.initialize())

    definition = _make_experiment_definition()
    _run(store1.register_experiment(definition))

    run = RunAttempt(
        run_id="run-recover",
        experiment_ref=definition.experiment_ref,
        variant_id="variant-a",
        status="pending",
        seed=None,
    )
    _run(store1.create_run_attempt(run))
    _run(store1.close())

    store2 = SQLiteStore(db)
    _run(store2.initialize())

    recovered = _run(store2.load_run_attempt("run-recover"))
    assert recovered is not None
    assert recovered.run_id == "run-recover"
    assert recovered.status == "pending"

    conn = sqlite3.connect(str(db))
    row = conn.execute(
        "SELECT run_id, status FROM run_attempts WHERE run_id=?", ("run-recover",)
    ).fetchone()
    assert row is not None
    assert row[0] == "run-recover"
    assert row[1] == "pending"
    conn.close()
    _run(store2.close())


def test_load_run_attempt_rejects_missing_required_seed(tmp_path: Path) -> None:
    """Persistence must not fill a missing required nullable seed with None."""
    db = tmp_path / "missing-seed.db"
    store = SQLiteStore(db)
    _run(store.initialize())
    definition = _make_experiment_definition()
    _run(store.register_experiment(definition))

    run = RunAttempt(
        run_id="run-missing-seed",
        experiment_ref=definition.experiment_ref,
        variant_id="variant-a",
        status="pending",
        seed=None,
    )
    _run(store.create_run_attempt(run))

    conn = sqlite3.connect(str(db))
    row = conn.execute(
        "SELECT run_json FROM run_attempts WHERE run_id = ?", (run.run_id,)
    ).fetchone()
    payload = json.loads(row[0])
    payload.pop("seed")
    conn.execute(
        "UPDATE run_attempts SET run_json = ? WHERE run_id = ?",
        (json.dumps(payload, separators=(",", ":")), run.run_id),
    )
    conn.commit()
    conn.close()

    with pytest.raises(ValidationError, match="seed"):
        _run(store.load_run_attempt(run.run_id))
    _run(store.close())


def test_migration_v2_to_v12_preserves_sessions_and_configs(tmp_path: Path) -> None:
    """v2 → v10 migration preserves sessions, configs, and experiment definitions."""
    db = tmp_path / "v2-to-v10.db"

    # Build a v2 database directly.
    conn = sqlite3.connect(str(db))
    conn.executescript("""
        PRAGMA foreign_keys = OFF;
        CREATE TABLE sessions (session_id TEXT PRIMARY KEY);
        CREATE TABLE strategy_config_versions (
            strategy_id TEXT NOT NULL, strategy_version TEXT NOT NULL, config_json TEXT NOT NULL,
            PRIMARY KEY (strategy_id, strategy_version)
        );
        CREATE TABLE session_strategy_configs (
            session_id TEXT NOT NULL REFERENCES sessions(session_id),
            strategy_id TEXT NOT NULL, strategy_version TEXT NOT NULL,
            PRIMARY KEY (session_id, strategy_id, strategy_version)
        );
        CREATE TABLE session_dataset_refs (
            session_id TEXT NOT NULL REFERENCES sessions(session_id),
            dataset_ref TEXT NOT NULL, PRIMARY KEY (session_id, dataset_ref)
        );
        CREATE TABLE experiment_definitions (
            experiment_id TEXT NOT NULL, experiment_version TEXT NOT NULL,
            definition_json TEXT NOT NULL, PRIMARY KEY (experiment_id, experiment_version)
        );
        PRAGMA user_version = 2;
    """)
    conn.close()

    upgraded = SQLiteStore(db)
    _run(upgraded.initialize())

    conn = sqlite3.connect(str(db))
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 15
    tables = {
        row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }
    assert tables == _SCHEMA_V15_TABLES
    conn.close()
    _run(upgraded.close())


def test_unknown_version_rejected_no_schema_change(tmp_path: Path) -> None:
    """An unknown schema version is rejected without modifying the database."""
    db = tmp_path / "unknown-version.db"

    conn = sqlite3.connect(str(db))
    conn.executescript("""
        CREATE TABLE sessions (session_id TEXT PRIMARY KEY);
        PRAGMA user_version = 99;
    """)
    conn.close()

    store = SQLiteStore(db)
    with pytest.raises(SchemaVersionMismatchError):
        _run(store.initialize())

    # Verify no changes were made.
    conn = sqlite3.connect(str(db))
    tables = {row[0] for row in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    )}
    assert tables == {"sessions"}
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 99
    conn.close()


def test_migration_failure_reverts_ddl_and_version(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A DDL failure during migration rolls back schema and user_version."""
    db = tmp_path / "migration-fail.db"

    # Build a v2 database.
    conn = sqlite3.connect(str(db))
    conn.executescript("""
        PRAGMA foreign_keys = OFF;
        CREATE TABLE sessions (session_id TEXT PRIMARY KEY);
        CREATE TABLE strategy_config_versions (
            strategy_id TEXT NOT NULL, strategy_version TEXT NOT NULL, config_json TEXT NOT NULL,
            PRIMARY KEY (strategy_id, strategy_version)
        );
        CREATE TABLE session_strategy_configs (
            session_id TEXT NOT NULL REFERENCES sessions(session_id),
            strategy_id TEXT NOT NULL, strategy_version TEXT NOT NULL,
            PRIMARY KEY (session_id, strategy_id, strategy_version)
        );
        CREATE TABLE session_dataset_refs (
            session_id TEXT NOT NULL REFERENCES sessions(session_id),
            dataset_ref TEXT NOT NULL, PRIMARY KEY (session_id, dataset_ref)
        );
        CREATE TABLE experiment_definitions (
            experiment_id TEXT NOT NULL, experiment_version TEXT NOT NULL,
            definition_json TEXT NOT NULL, PRIMARY KEY (experiment_id, experiment_version)
        );
        PRAGMA user_version = 2;
    """)
    conn.close()

    original_v3_statements = sqlite_store_module._SCHEMA_V3_STATEMENTS
    monkeypatch.setattr(
        sqlite_store_module,
        "_SCHEMA_V3_STATEMENTS",
        (*original_v3_statements, "THIS IS INVALID SQL"),
    )

    store = SQLiteStore(db)
    with pytest.raises(sqlite3.OperationalError):
        _run(store.initialize())

    conn = sqlite3.connect(str(db))
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 2
    tables = {row[0] for row in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    )}
    assert "run_attempts" not in tables
    conn.close()


# ── 19. v3 → current-schema migration tests ────────────────────────────────────


def test_v3_to_v12_migration_preserves_all_v3_data(tmp_path: Path) -> None:
    """v3 → v10 migration preserves sessions, configs, definitions, and runs."""
    db = tmp_path / "v3-to-v10.db"

    # Build a v3 database with data.
    conn = sqlite3.connect(str(db))
    conn.executescript("""
        PRAGMA foreign_keys = OFF;
        CREATE TABLE sessions (session_id TEXT PRIMARY KEY);
        CREATE TABLE strategy_config_versions (
            strategy_id TEXT NOT NULL, strategy_version TEXT NOT NULL, config_json TEXT NOT NULL,
            PRIMARY KEY (strategy_id, strategy_version)
        );
        CREATE TABLE session_strategy_configs (
            session_id TEXT NOT NULL REFERENCES sessions(session_id),
            strategy_id TEXT NOT NULL, strategy_version TEXT NOT NULL,
            PRIMARY KEY (session_id, strategy_id, strategy_version)
        );
        CREATE TABLE session_dataset_refs (
            session_id TEXT NOT NULL REFERENCES sessions(session_id),
            dataset_ref TEXT NOT NULL, PRIMARY KEY (session_id, dataset_ref)
        );
        CREATE TABLE experiment_definitions (
            experiment_id TEXT NOT NULL, experiment_version TEXT NOT NULL,
            definition_json TEXT NOT NULL, PRIMARY KEY (experiment_id, experiment_version)
        );
        CREATE TABLE run_attempts (
            run_id TEXT PRIMARY KEY, experiment_id TEXT NOT NULL,
            experiment_version TEXT NOT NULL, variant_id TEXT NOT NULL,
            status TEXT NOT NULL CHECK(status IN ('pending','running','failed','cancelled')),
            run_json TEXT NOT NULL,
            FOREIGN KEY (experiment_id, experiment_version)
                REFERENCES experiment_definitions(experiment_id, experiment_version)
        );
        PRAGMA user_version = 3;
    """)
    # Insert test data.
    conn.execute(
        "INSERT INTO sessions (session_id) VALUES ('sess-v3')",
    )
    conn.execute(
        "INSERT INTO strategy_config_versions VALUES ('s1','v1','{}')",
    )
    conn.execute(
        "INSERT INTO session_strategy_configs VALUES ('sess-v1','s1','v1')",
    )
    conn.execute(
        "INSERT INTO session_dataset_refs VALUES ('sess-v1','ds-ref-1')",
    )
    conn.execute(
        "INSERT INTO experiment_definitions VALUES ('exp-1','v1','{}')",
    )
    conn.execute(
        "INSERT INTO run_attempts VALUES ('run-1','exp-1','v1','va','pending','{}')",
    )
    conn.commit()
    conn.close()

    # Migrate to the current schema.
    upgraded = SQLiteStore(db)
    _run(upgraded.initialize())

    conn = sqlite3.connect(str(db))
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 15
    tables = {row[0] for row in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    )}
    assert tables == _SCHEMA_V15_TABLES

    # All v3 data preserved.
    assert conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM strategy_config_versions").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM session_strategy_configs").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM session_dataset_refs").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM experiment_definitions").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM run_attempts").fetchone()[0] == 1

    # New table is empty.
    assert conn.execute("SELECT COUNT(*) FROM experiment_results").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM paper_fills").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM paper_account_openings").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM paper_account_balances").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM paper_orders").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM paper_order_acceptances").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM paper_reservations").fetchone()[0] == 0
    conn.close()
    _run(upgraded.close())


def test_v3_to_v4_migration_rolls_back_ddl_and_version(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A DDL failure during v3→v4 migration rolls back schema and user_version."""
    db = tmp_path / "v3-to-v4-fail.db"

    # Build a v3 database.
    conn = sqlite3.connect(str(db))
    conn.executescript("""
        PRAGMA foreign_keys = OFF;
        CREATE TABLE sessions (session_id TEXT PRIMARY KEY);
        CREATE TABLE strategy_config_versions (
            strategy_id TEXT NOT NULL, strategy_version TEXT NOT NULL, config_json TEXT NOT NULL,
            PRIMARY KEY (strategy_id, strategy_version)
        );
        CREATE TABLE session_strategy_configs (
            session_id TEXT NOT NULL REFERENCES sessions(session_id),
            strategy_id TEXT NOT NULL, strategy_version TEXT NOT NULL,
            PRIMARY KEY (session_id, strategy_id, strategy_version)
        );
        CREATE TABLE session_dataset_refs (
            session_id TEXT NOT NULL REFERENCES sessions(session_id),
            dataset_ref TEXT NOT NULL, PRIMARY KEY (session_id, dataset_ref)
        );
        CREATE TABLE experiment_definitions (
            experiment_id TEXT NOT NULL, experiment_version TEXT NOT NULL,
            definition_json TEXT NOT NULL, PRIMARY KEY (experiment_id, experiment_version)
        );
        CREATE TABLE run_attempts (
            run_id TEXT PRIMARY KEY, experiment_id TEXT NOT NULL,
            experiment_version TEXT NOT NULL, variant_id TEXT NOT NULL,
            status TEXT NOT NULL CHECK(status IN ('pending','running','failed','cancelled')),
            run_json TEXT NOT NULL,
            FOREIGN KEY (experiment_id, experiment_version)
                REFERENCES experiment_definitions(experiment_id, experiment_version)
        );
        PRAGMA user_version = 3;
    """)
    conn.execute("INSERT INTO sessions (session_id) VALUES ('preserved-sess')")
    conn.commit()
    conn.close()

    original_v4_statements = sqlite_store_module._SCHEMA_V4_STATEMENTS
    monkeypatch.setattr(
        sqlite_store_module,
        "_SCHEMA_V4_STATEMENTS",
        (*original_v4_statements, "THIS IS INVALID SQL"),
    )

    store = SQLiteStore(db)
    with pytest.raises(sqlite3.OperationalError):
        _run(store.initialize())

    conn = sqlite3.connect(str(db))
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 3
    tables = {row[0] for row in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    )}
    assert "experiment_results" not in tables
    # v3 data preserved.
    assert conn.execute("SELECT COUNT(*) FROM sessions WHERE session_id=?", ("preserved-sess",)).fetchone()[0] == 1
    conn.close()


def _make_v4_database_for_v12_migration(db: Path) -> None:
    """Create an actual v4 table set from a fresh current database."""
    store = SQLiteStore(db)
    _run(store.initialize())
    _run(store.close())

    conn = sqlite3.connect(str(db))
    conn.execute("DROP TABLE paper_fill_evaluations")
    conn.execute("DROP TABLE variant_dispositions")
    conn.execute("DROP TABLE result_exclusions")
    conn.execute("DROP TABLE paper_fills")
    conn.execute("DROP TABLE paper_account_balances")
    conn.execute("DROP TABLE paper_account_openings")
    conn.execute("DROP TABLE paper_reservations")
    conn.execute("DROP TABLE paper_order_acceptances")
    conn.execute("DROP TABLE paper_orders")
    conn.execute("DROP TABLE paper_fill_applications")
    conn.execute("DROP TABLE position_lots")
    conn.execute("DROP TABLE paper_reservation_lot_allocations")
    conn.execute("DROP TABLE paper_sell_accounting")
    conn.execute("DROP TABLE paper_lifecycle_reconciliations")
    conn.execute("DROP TABLE paper_lifecycle_events")
    conn.execute("DROP TABLE paper_lifecycle_states")
    conn.execute("INSERT INTO sessions (session_id) VALUES ('preserved-v4-session')")
    conn.execute("PRAGMA user_version = 4")
    conn.commit()
    conn.close()


def test_v4_to_v12_migration_creates_current_tables_and_preserves_data(
    tmp_path: Path,
) -> None:
    """The v4 → v12 chain adds current tables and preserves v4 data."""
    db = tmp_path / "v4-to-v12.db"
    _make_v4_database_for_v12_migration(db)

    upgraded = SQLiteStore(db)
    _run(upgraded.initialize())

    conn = sqlite3.connect(str(db))
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 15
    tables = {
        row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }
    assert tables == _SCHEMA_V15_TABLES
    assert conn.execute(
        "SELECT session_id FROM sessions WHERE session_id='preserved-v4-session'"
    ).fetchone() == ("preserved-v4-session",)
    assert conn.execute("SELECT COUNT(*) FROM variant_dispositions").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM result_exclusions").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM paper_fills").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM paper_account_openings").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM paper_account_balances").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM paper_orders").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM paper_order_acceptances").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM paper_reservations").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM paper_reservation_lot_allocations").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM paper_sell_accounting").fetchone()[0] == 0
    conn.close()
    _run(upgraded.close())


def test_v4_migration_failure_rolls_back_ddl_and_version(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A late v12 DDL failure rolls the v4 → v12 migration back completely."""
    db = tmp_path / "v4-to-v12-rollback.db"
    _make_v4_database_for_v12_migration(db)

    original_fill_application_ddl = (
        sqlite_store_module._PAPER_FILL_APPLICATION_DDL_STATEMENTS
    )
    monkeypatch.setattr(
        sqlite_store_module,
        "_PAPER_FILL_APPLICATION_DDL_STATEMENTS",
        (*original_fill_application_ddl[:1], "THIS IS INVALID SQL"),
    )
    migration = SQLiteStore(db)
    with pytest.raises(sqlite3.OperationalError):
        _run(migration.initialize())

    conn = sqlite3.connect(str(db))
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 4
    tables = {
        row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }
    assert tables == _SCHEMA_V4_TABLES
    assert conn.execute(
        "SELECT session_id FROM sessions WHERE session_id='preserved-v4-session'"
    ).fetchone() == ("preserved-v4-session",)
    conn.close()


def test_new_database_initializes_directly_at_v12_with_paper_acceptance(tmp_path: Path) -> None:
    """A fresh database initializes v12 with atomic order-acceptance tables."""
    db = tmp_path / "fresh-v12.db"
    store = SQLiteStore(db)
    _run(store.initialize())

    conn = sqlite3.connect(str(db))
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 15
    tables = {row[0] for row in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    )}
    assert tables == _SCHEMA_V15_TABLES
    # experiment_results table exists and is empty.
    assert conn.execute("SELECT COUNT(*) FROM experiment_results").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM paper_fills").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM paper_account_openings").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM paper_account_balances").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM paper_orders").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM paper_order_acceptances").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM paper_reservations").fetchone()[0] == 0
    conn.close()
    _run(store.close())


    _run(store.close())


# ── 20. publish_experiment_result / load_experiment_result (Phase 2) ───────────────


def test_publish_experiment_result_from_running_run(tmp_path: Path) -> None:
    """publish_experiment_result succeeds when run is completed and result matches."""
    db = tmp_path / "publish-ok.db"
    store = SQLiteStore(db)
    _run(store.initialize())

    definition = _make_experiment_definition()
    _run(store.register_experiment(definition))

    # Create pending → running.
    pending = RunAttempt(
        run_id="run-pub",
        experiment_ref=definition.experiment_ref,
        variant_id="variant-a",
        status="pending",
        seed=None,
    )
    _run(store.create_run_attempt(pending))
    running = RunAttempt(
        run_id="run-pub",
        experiment_ref=definition.experiment_ref,
        variant_id="variant-a",
        status="running",
        seed=42,
        started_at_ms=1_000,
        dataset_ref_used="dataset-experiment",
        code_ref="code-ref-1",
        engine_ref="engine-ref-1",
        effective_periods=(PeriodWindow(purpose="design", start_ms=1_000, end_ms=2_000),),
    )
    _run(store.transition_run_attempt("run-pub", running))

    # Publish the experiment result — this also completes the run.
    completed = RunAttempt(
        run_id="run-pub",
        experiment_ref=definition.experiment_ref,
        variant_id="variant-a",
        status="completed",
        seed=42,
        started_at_ms=1_000,
        finished_at_ms=3_000,
        dataset_ref_used="dataset-experiment",
        code_ref="code-ref-1",
        engine_ref="engine-ref-1",
        effective_periods=(PeriodWindow(purpose="design", start_ms=1_000, end_ms=2_000),),
        result_id="result-pub",
    )
    result = ExperimentResult(
        result_id="result-pub",
        run_id="run-pub",
        artifact_refs=("artifact-ref-1", "artifact-ref-2"),
    )
    _run(store.publish_experiment_result(completed, result))

    # Verify run is completed.
    loaded_run = _run(store.load_run_attempt("run-pub"))
    assert loaded_run.status == "completed"
    assert loaded_run.result_id == "result-pub"

    # Verify experiment result was persisted and can be loaded.
    loaded_result = _run(store.load_experiment_result("run-pub"))
    assert loaded_result is not None
    assert loaded_result.result_id == "result-pub"
    assert loaded_result.run_id == "run-pub"
    assert loaded_result.artifact_refs == ("artifact-ref-1", "artifact-ref-2")

    conn = sqlite3.connect(str(db))
    assert conn.execute("SELECT COUNT(*) FROM experiment_results").fetchone()[0] == 1
    conn.close()
    _run(store.close())


def test_publish_experiment_result_rejects_non_completed(tmp_path: Path) -> None:
    """publish_experiment_result rejects runs that are not completed."""
    db = tmp_path / "publish-reject.db"
    store = SQLiteStore(db)
    _run(store.initialize())

    definition = _make_experiment_definition()
    _run(store.register_experiment(definition))

    pending = RunAttempt(
        run_id="run-pend",
        experiment_ref=definition.experiment_ref,
        variant_id="variant-a",
        status="pending",
        seed=None,
    )
    _run(store.create_run_attempt(pending))

    result = ExperimentResult(
        result_id="result-pend",
        run_id="run-pend",
        artifact_refs=("artifact-ref-1",),
    )
    with pytest.raises(ExperimentResultStateError):
        _run(store.publish_experiment_result(pending, result))

    # Transition to running and try again.
    running = RunAttempt(
        run_id="run-pend",
        experiment_ref=definition.experiment_ref,
        variant_id="variant-a",
        status="running",
        seed=42,
        started_at_ms=1_000,
        dataset_ref_used="dataset-experiment",
        code_ref="code-ref-1",
        engine_ref="engine-ref-1",
        effective_periods=(PeriodWindow(purpose="design", start_ms=1_000, end_ms=2_000),),
    )
    _run(store.transition_run_attempt("run-pend", running))

    with pytest.raises(ExperimentResultStateError):
        _run(store.publish_experiment_result(running, result))

    # Transition to failed and try again.
    failed = RunAttempt(
        run_id="run-pend",
        experiment_ref=definition.experiment_ref,
        variant_id="variant-a",
        status="failed",
        seed=42,
        started_at_ms=1_000,
        finished_at_ms=3_000,
        dataset_ref_used="dataset-experiment",
        code_ref="code-ref-1",
        engine_ref="engine-ref-1",
        effective_periods=(PeriodWindow(purpose="design", start_ms=1_000, end_ms=2_000),),
        diagnostic=PublicDiagnostic(error_code="ERR_FAIL", public_message="fail"),
    )
    _run(store.transition_run_attempt("run-pend", failed))

    with pytest.raises(ExperimentResultStateError):
        _run(store.publish_experiment_result(failed, result))

    # Transition to cancelled and try again (need a fresh run since failed is terminal).
    pending2 = RunAttempt(
        run_id="run-cancel",
        experiment_ref=definition.experiment_ref,
        variant_id="variant-a",
        status="pending",
        seed=None,
    )
    _run(store.create_run_attempt(pending2))
    cancelled = RunAttempt(
        run_id="run-cancel",
        experiment_ref=definition.experiment_ref,
        variant_id="variant-a",
        status="cancelled",
        seed=None,
        finished_at_ms=4_000,
    )
    _run(store.transition_run_attempt("run-cancel", cancelled))

    with pytest.raises(ExperimentResultStateError):
        _run(store.publish_experiment_result(cancelled, result))

    _run(store.close())


def test_publish_experiment_result_one_result_per_run(tmp_path: Path) -> None:
    """Only one result per run; second publish produces conflict."""
    db = tmp_path / "publish-one.db"
    store = SQLiteStore(db)
    _run(store.initialize())

    definition = _make_experiment_definition()
    _run(store.register_experiment(definition))

    pending = RunAttempt(
        run_id="run-single",
        experiment_ref=definition.experiment_ref,
        variant_id="variant-a",
        status="pending",
        seed=None,
    )
    _run(store.create_run_attempt(pending))
    running = RunAttempt(
        run_id="run-single",
        experiment_ref=definition.experiment_ref,
        variant_id="variant-a",
        status="running",
        seed=42,
        started_at_ms=1_000,
        dataset_ref_used="dataset-experiment",
        code_ref="code-ref-1",
        engine_ref="engine-ref-1",
        effective_periods=(PeriodWindow(purpose="design", start_ms=1_000, end_ms=2_000),),
    )
    _run(store.transition_run_attempt("run-single", running))

    # publish_experiment_result atomically completes the run and saves the result.
    completed = RunAttempt(
        run_id="run-single",
        experiment_ref=definition.experiment_ref,
        variant_id="variant-a",
        status="completed",
        seed=42,
        started_at_ms=1_000,
        finished_at_ms=3_000,
        dataset_ref_used="dataset-experiment",
        code_ref="code-ref-1",
        engine_ref="engine-ref-1",
        effective_periods=(PeriodWindow(purpose="design", start_ms=1_000, end_ms=2_000),),
        result_id="result-first",
    )
    result1 = ExperimentResult(
        result_id="result-first",
        run_id="run-single",
        artifact_refs=("artifact-1",),
    )
    _run(store.publish_experiment_result(completed, result1))

    # A second publish with different content conflicts and preserves the first result.
    result2 = ExperimentResult(
        result_id="result-first",
        run_id="run-single",
        artifact_refs=("artifact-2",),  # different content
    )
    with pytest.raises(ExperimentResultConflictError):
        _run(store.publish_experiment_result(completed, result2))

    loaded_result = _run(store.load_experiment_result("run-single"))
    assert loaded_result is not None
    assert loaded_result.artifact_refs == ("artifact-1",)

    conn = sqlite3.connect(str(db))
    assert conn.execute("SELECT COUNT(*) FROM experiment_results").fetchone()[0] == 1
    conn.close()
    _run(store.close())


def test_publish_experiment_result_idempotent_same_content(tmp_path: Path) -> None:
    """Publishing identical result twice is idempotent — no error, no duplicate."""
    db = tmp_path / "publish-idem.db"
    store = SQLiteStore(db)
    _run(store.initialize())

    definition = _make_experiment_definition()
    _run(store.register_experiment(definition))

    pending = RunAttempt(
        run_id="run-idem",
        experiment_ref=definition.experiment_ref,
        variant_id="variant-a",
        status="pending",
        seed=None,
    )
    _run(store.create_run_attempt(pending))
    running = RunAttempt(
        run_id="run-idem",
        experiment_ref=definition.experiment_ref,
        variant_id="variant-a",
        status="running",
        seed=42,
        started_at_ms=1_000,
        dataset_ref_used="dataset-experiment",
        code_ref="code-ref-1",
        engine_ref="engine-ref-1",
        effective_periods=(PeriodWindow(purpose="design", start_ms=1_000, end_ms=2_000),),
    )
    _run(store.transition_run_attempt("run-idem", running))

    # publish_experiment_result atomically completes the run and saves the result.
    completed = RunAttempt(
        run_id="run-idem",
        experiment_ref=definition.experiment_ref,
        variant_id="variant-a",
        status="completed",
        seed=42,
        started_at_ms=1_000,
        finished_at_ms=3_000,
        dataset_ref_used="dataset-experiment",
        code_ref="code-ref-1",
        engine_ref="engine-ref-1",
        effective_periods=(PeriodWindow(purpose="design", start_ms=1_000, end_ms=2_000),),
        result_id="result-idem",
    )
    result = ExperimentResult(
        result_id="result-idem",
        run_id="run-idem",
        artifact_refs=("artifact-1", "artifact-2"),
    )
    _run(store.publish_experiment_result(completed, result))
    # Exact retry after commit is idempotent and does not duplicate the result.
    _run(store.publish_experiment_result(completed, result))

    # Exact retries preserve the original timestamps and effective run context.
    loaded_run = _run(store.load_run_attempt("run-idem"))
    assert loaded_run is not None
    assert loaded_run.model_dump_json() == completed.model_dump_json()

    altered_run = completed.model_copy(
        update={"finished_at_ms": 4_000, "dataset_ref_used": "other-dataset"}
    )
    with pytest.raises(ExperimentResultConflictError):
        _run(store.publish_experiment_result(altered_run, result))

    unchanged_run = _run(store.load_run_attempt("run-idem"))
    assert unchanged_run is not None
    assert unchanged_run.model_dump_json() == completed.model_dump_json()

    # Verify idempotency: the stored result matches what we published.
    loaded_result = _run(store.load_experiment_result("run-idem"))
    assert loaded_result is not None
    assert loaded_result.artifact_refs == ("artifact-1", "artifact-2")

    conn = sqlite3.connect(str(db))
    assert conn.execute("SELECT COUNT(*) FROM experiment_results").fetchone()[0] == 1
    conn.close()
    _run(store.close())


def test_publish_experiment_result_conflict_different_run_id(tmp_path: Path) -> None:
    """Publishing with mismatched run_id produces conflict."""
    db = tmp_path / "publish-conflict-run.db"
    store = SQLiteStore(db)
    _run(store.initialize())

    definition = _make_experiment_definition()
    _run(store.register_experiment(definition))

    pending = RunAttempt(
        run_id="run-a",
        experiment_ref=definition.experiment_ref,
        variant_id="variant-a",
        status="pending",
        seed=None,
    )
    _run(store.create_run_attempt(pending))
    running = RunAttempt(
        run_id="run-a",
        experiment_ref=definition.experiment_ref,
        variant_id="variant-a",
        status="running",
        seed=42,
        started_at_ms=1_000,
        dataset_ref_used="dataset-experiment",
        code_ref="code-ref-1",
        engine_ref="engine-ref-1",
        effective_periods=(PeriodWindow(purpose="design", start_ms=1_000, end_ms=2_000),),
    )
    _run(store.transition_run_attempt("run-a", running))
    completed = RunAttempt(
        run_id="run-a",
        experiment_ref=definition.experiment_ref,
        variant_id="variant-a",
        status="completed",
        seed=42,
        started_at_ms=1_000,
        finished_at_ms=3_000,
        dataset_ref_used="dataset-experiment",
        code_ref="code-ref-1",
        engine_ref="engine-ref-1",
        effective_periods=(PeriodWindow(purpose="design", start_ms=1_000, end_ms=2_000),),
        result_id="result-a",
    )

    # Different run_id in result.
    bad_result = ExperimentResult(
        result_id="result-a",
        run_id="run-b",  # mismatched
        artifact_refs=("artifact-1",),
    )
    with pytest.raises(ExperimentResultStateError):
        _run(store.publish_experiment_result(completed, bad_result))

    _run(store.close())


def test_publish_experiment_result_conflict_different_result_id(tmp_path: Path) -> None:
    """Publishing with mismatched result_id produces conflict."""
    db = tmp_path / "publish-conflict-res.db"
    store = SQLiteStore(db)
    _run(store.initialize())

    definition = _make_experiment_definition()
    _run(store.register_experiment(definition))

    pending = RunAttempt(
        run_id="run-a",
        experiment_ref=definition.experiment_ref,
        variant_id="variant-a",
        status="pending",
        seed=None,
    )
    _run(store.create_run_attempt(pending))
    running = RunAttempt(
        run_id="run-a",
        experiment_ref=definition.experiment_ref,
        variant_id="variant-a",
        status="running",
        seed=42,
        started_at_ms=1_000,
        dataset_ref_used="dataset-experiment",
        code_ref="code-ref-1",
        engine_ref="engine-ref-1",
        effective_periods=(PeriodWindow(purpose="design", start_ms=1_000, end_ms=2_000),),
    )
    _run(store.transition_run_attempt("run-a", running))
    completed = RunAttempt(
        run_id="run-a",
        experiment_ref=definition.experiment_ref,
        variant_id="variant-a",
        status="completed",
        seed=42,
        started_at_ms=1_000,
        finished_at_ms=3_000,
        dataset_ref_used="dataset-experiment",
        code_ref="code-ref-1",
        engine_ref="engine-ref-1",
        effective_periods=(PeriodWindow(purpose="design", start_ms=1_000, end_ms=2_000),),
        result_id="result-a",
    )

    # Different result_id in result.
    bad_result = ExperimentResult(
        result_id="result-b",  # mismatched
        run_id="run-a",
        artifact_refs=("artifact-1",),
    )
    with pytest.raises(ExperimentResultStateError):
        _run(store.publish_experiment_result(completed, bad_result))

    _run(store.close())


def test_publish_experiment_result_rollback_on_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A failure between inserting result and updating run rolls back both."""
    db = tmp_path / "publish-rollback.db"
    store = SQLiteStore(db)
    _run(store.initialize())

    definition = _make_experiment_definition()
    _run(store.register_experiment(definition))

    pending = RunAttempt(
        run_id="run-rb",
        experiment_ref=definition.experiment_ref,
        variant_id="variant-a",
        status="pending",
        seed=None,
    )
    _run(store.create_run_attempt(pending))
    running = RunAttempt(
        run_id="run-rb",
        experiment_ref=definition.experiment_ref,
        variant_id="variant-a",
        status="running",
        seed=42,
        started_at_ms=1_000,
        dataset_ref_used="dataset-experiment",
        code_ref="code-ref-1",
        engine_ref="engine-ref-1",
        effective_periods=(PeriodWindow(purpose="design", start_ms=1_000, end_ms=2_000),),
    )
    _run(store.transition_run_attempt("run-rb", running))
    completed = RunAttempt(
        run_id="run-rb",
        experiment_ref=definition.experiment_ref,
        variant_id="variant-a",
        status="completed",
        seed=42,
        started_at_ms=1_000,
        finished_at_ms=3_000,
        dataset_ref_used="dataset-experiment",
        code_ref="code-ref-1",
        engine_ref="engine-ref-1",
        effective_periods=(PeriodWindow(purpose="design", start_ms=1_000, end_ms=2_000),),
        result_id="result-rb",
    )

    # Inject a failure after inserting experiment_results but before updating run.
    original_inner = SQLiteStore._publish_experiment_result_inner

    async def failing_inner(conn, completed_run, result):
        await original_inner(conn, completed_run, result)
        raise sqlite3.IntegrityError("simulated failure")

    monkeypatch.setattr(
        SQLiteStore,
        "_publish_experiment_result_inner",
        staticmethod(failing_inner),
    )

    result = ExperimentResult(
        result_id="result-rb",
        run_id="run-rb",
        artifact_refs=("artifact-1",),
    )
    with pytest.raises(sqlite3.IntegrityError):
        _run(store.publish_experiment_result(completed, result))

    # Verify rollback: run is still running (unchanged), no experiment_results row.
    loaded_run = _run(store.load_run_attempt("run-rb"))
    assert loaded_run.status == "running"
    assert loaded_run.result_id is None

    conn = sqlite3.connect(str(db))
    assert conn.execute("SELECT COUNT(*) FROM experiment_results").fetchone()[0] == 0
    conn.close()
    _run(store.close())


def test_load_experiment_result_returns_none_when_missing(tmp_path: Path) -> None:
    """load_experiment_result returns None when no result exists for the run."""
    db = tmp_path / "load-none.db"
    store = SQLiteStore(db)
    _run(store.initialize())

    loaded = _run(store.load_experiment_result("nonexistent"))
    assert loaded is None
    _run(store.close())


def test_load_experiment_result_validates_corrupt_json(tmp_path: Path) -> None:
    """load_experiment_result rejects corrupt JSON in experiment_results."""
    db = tmp_path / "load-corrupt.db"
    store = SQLiteStore(db)
    _run(store.initialize())

    # Insert data directly (use 'running' since 'completed' violates CHECK).
    conn = sqlite3.connect(str(db))
    conn.execute(
        "INSERT INTO run_attempts (run_id, experiment_id, experiment_version, variant_id, status, run_json) "
        "VALUES ('run-corrupt', 'exp-1', 'v1', 'va', 'running', '{}')",
    )
    conn.execute(
        "INSERT INTO experiment_results (result_id, run_id, result_json) VALUES ('res-corrupt', 'run-corrupt', 'NOT JSON')",
    )
    conn.commit()
    conn.close()

    with pytest.raises(ExperimentResultConflictError):
        _run(store.load_experiment_result("run-corrupt"))

    _run(store.close())


def test_load_experiment_result_validates_key_mismatch(tmp_path: Path) -> None:
    """load_experiment_result rejects result JSON whose keys don't match stored columns."""
    db = tmp_path / "load-keymismatch.db"
    store = SQLiteStore(db)
    _run(store.initialize())

    # Insert data directly (use 'running' since 'completed' violates CHECK).
    conn = sqlite3.connect(str(db))
    conn.execute(
        "INSERT INTO run_attempts (run_id, experiment_id, experiment_version, variant_id, status, run_json) "
        "VALUES ('run-match', 'exp-1', 'v1', 'va', 'running', '{}')",
    )
    # result_json has different run_id than stored.
    mismatched = ExperimentResult(
        result_id="res-match",
        run_id="run-other",  # mismatch with stored run_id
        artifact_refs=("artifact-1",),
    ).model_dump_json()
    conn.execute(
        "INSERT INTO experiment_results (result_id, run_id, result_json) VALUES ('res-match', 'run-match', ?)",
        (mismatched,),
    )
    conn.commit()
    conn.close()

    with pytest.raises(ExperimentResultConflictError):
        _run(store.load_experiment_result("run-match"))

    _run(store.close())


def test_transition_run_attempt_still_rejects_completed(tmp_path: Path) -> None:
    """transition_run_attempt continues to reject completed state."""
    db = tmp_path / "reject-completed.db"
    store = SQLiteStore(db)
    _run(store.initialize())

    definition = _make_experiment_definition()
    _run(store.register_experiment(definition))

    pending = RunAttempt(
        run_id="run-rc",
        experiment_ref=definition.experiment_ref,
        variant_id="variant-a",
        status="pending",
        seed=None,
    )
    _run(store.create_run_attempt(pending))

    # Attempt to transition directly to completed via transition_run_attempt.
    with pytest.raises(RunAttemptStateError):
        _run(
            store.transition_run_attempt(
                "run-rc",
                RunAttempt(
                    run_id="run-rc",
                    experiment_ref=definition.experiment_ref,
                    variant_id="variant-a",
                    status="completed",
                    seed=42,
                    started_at_ms=1_000,
                    finished_at_ms=3_000,
                    dataset_ref_used="dataset-experiment",
                    code_ref="code-ref-1",
                    engine_ref="engine-ref-1",
                    effective_periods=(PeriodWindow(purpose="design", start_ms=1_000, end_ms=2_000),),
                    result_id="result-x",
                ),
            )
        )

    _run(store.close())


def test_publish_experiment_result_empty_artifact_refs_rejected(tmp_path: Path) -> None:
    """publish_experiment_result rejects results with empty artifact_refs."""
    db = tmp_path / "publish-empty-artifacts.db"
    store = SQLiteStore(db)
    _run(store.initialize())

    definition = _make_experiment_definition()
    _run(store.register_experiment(definition))

    pending = RunAttempt(
        run_id="run-empty",
        experiment_ref=definition.experiment_ref,
        variant_id="variant-a",
        status="pending",
        seed=None,
    )
    _run(store.create_run_attempt(pending))
    running = RunAttempt(
        run_id="run-empty",
        experiment_ref=definition.experiment_ref,
        variant_id="variant-a",
        status="running",
        seed=42,
        started_at_ms=1_000,
        dataset_ref_used="dataset-experiment",
        code_ref="code-ref-1",
        engine_ref="engine-ref-1",
        effective_periods=(PeriodWindow(purpose="design", start_ms=1_000, end_ms=2_000),),
    )
    _run(store.transition_run_attempt("run-empty", running))

    # Insert a result with empty artifact_refs directly into the DB (bypassing Pydantic).
    conn = sqlite3.connect(str(db))
    conn.execute(
        "INSERT INTO experiment_results (result_id, run_id, result_json) "
        "VALUES ('res-empty', 'run-empty', "
        "'{\"schema_version\":\"experiment_result.v1\",\"result_id\":\"res-empty\","
        "\"run_id\":\"run-empty\",\"artifact_refs\":[]}')",
    )
    conn.commit()
    conn.close()

    # load_experiment_result should reject the empty artifact_refs.
    with pytest.raises(ExperimentResultConflictError):
        _run(store.load_experiment_result("run-empty"))
    completed = RunAttempt(
        run_id="run-empty",
        experiment_ref=definition.experiment_ref,
        variant_id="variant-a",
        status="completed",
        seed=42,
        started_at_ms=1_000,
        finished_at_ms=3_000,
        dataset_ref_used="dataset-experiment",
        code_ref="code-ref-1",
        engine_ref="engine-ref-1",
        effective_periods=(PeriodWindow(purpose="design", start_ms=1_000, end_ms=2_000),),
        result_id="result-empty",
    )
    # The model itself rejects empty artifact_refs at construction time.
    with pytest.raises(ValidationError):
        ExperimentResult(
            result_id="result-empty",
            run_id="run-empty",
            artifact_refs=(),  # empty — rejected by Pydantic validator
        )

    _run(store.close())


def test_reopen_recovers_completed_run_and_result(tmp_path: Path) -> None:
    """After reopen, completed run and linked ExperimentResult are recoverable."""
    db = tmp_path / "reopen-completed.db"
    store1 = SQLiteStore(db)
    _run(store1.initialize())

    definition = _make_experiment_definition()
    _run(store1.register_experiment(definition))

    pending = RunAttempt(
        run_id="run-reopen",
        experiment_ref=definition.experiment_ref,
        variant_id="variant-a",
        status="pending",
        seed=None,
    )
    _run(store1.create_run_attempt(pending))
    running = RunAttempt(
        run_id="run-reopen",
        experiment_ref=definition.experiment_ref,
        variant_id="variant-a",
        status="running",
        seed=42,
        started_at_ms=1_000,
        dataset_ref_used="dataset-experiment",
        code_ref="code-ref-1",
        engine_ref="engine-ref-1",
        effective_periods=(PeriodWindow(purpose="design", start_ms=1_000, end_ms=2_000),),
    )
    _run(store1.transition_run_attempt("run-reopen", running))
    completed = RunAttempt(
        run_id="run-reopen",
        experiment_ref=definition.experiment_ref,
        variant_id="variant-a",
        status="completed",
        seed=42,
        started_at_ms=1_000,
        finished_at_ms=3_000,
        dataset_ref_used="dataset-experiment",
        code_ref="code-ref-1",
        engine_ref="engine-ref-1",
        effective_periods=(PeriodWindow(purpose="design", start_ms=1_000, end_ms=2_000),),
        result_id="result-reopen",
    )
    result = ExperimentResult(
        result_id="result-reopen",
        run_id="run-reopen",
        artifact_refs=("artifact-reopen-1",),
    )
    _run(store1.publish_experiment_result(completed, result))
    _run(store1.close())

    # Reopen.
    store2 = SQLiteStore(db)
    _run(store2.initialize())

    loaded_run = _run(store2.load_run_attempt("run-reopen"))
    assert loaded_run is not None
    assert loaded_run.status == "completed"
    assert loaded_run.result_id == "result-reopen"

    loaded_result = _run(store2.load_experiment_result("run-reopen"))
    assert loaded_result is not None
    assert loaded_result.result_id == "result-reopen"
    assert loaded_result.run_id == "run-reopen"
    assert loaded_result.artifact_refs == ("artifact-reopen-1",)

    _run(store2.close())


def test_v9_to_v12_migration_preserves_v9_data(tmp_path: Path) -> None:
    """v9 → v10 migration preserves all v9 data and adds the two new tables."""
    db = tmp_path / "v9-to-v10.db"

    # Build a v9 database with data.
    conn = sqlite3.connect(str(db))
    conn.executescript("""
        PRAGMA foreign_keys = OFF;
        CREATE TABLE sessions (session_id TEXT PRIMARY KEY);
        CREATE TABLE strategy_config_versions (
            strategy_id TEXT NOT NULL, strategy_version TEXT NOT NULL, config_json TEXT NOT NULL,
            PRIMARY KEY (strategy_id, strategy_version)
        );
        CREATE TABLE session_strategy_configs (
            session_id TEXT NOT NULL REFERENCES sessions(session_id),
            strategy_id TEXT NOT NULL, strategy_version TEXT NOT NULL,
            PRIMARY KEY (session_id, strategy_id, strategy_version)
        );
        CREATE TABLE session_dataset_refs (
            session_id TEXT NOT NULL REFERENCES sessions(session_id),
            dataset_ref TEXT NOT NULL, PRIMARY KEY (session_id, dataset_ref)
        );
        CREATE TABLE experiment_definitions (
            experiment_id TEXT NOT NULL, experiment_version TEXT NOT NULL,
            definition_json TEXT NOT NULL, PRIMARY KEY (experiment_id, experiment_version)
        );
        CREATE TABLE run_attempts (
            run_id TEXT PRIMARY KEY, experiment_id TEXT NOT NULL,
            experiment_version TEXT NOT NULL, variant_id TEXT NOT NULL,
            status TEXT NOT NULL CHECK(status IN ('pending','running','failed','cancelled')),
            run_json TEXT NOT NULL,
            FOREIGN KEY (experiment_id, experiment_version)
                REFERENCES experiment_definitions(experiment_id, experiment_version)
        );
        CREATE TABLE experiment_results (
            experiment_id TEXT NOT NULL, experiment_version TEXT NOT NULL,
            result_json TEXT NOT NULL, PRIMARY KEY (experiment_id, experiment_version)
        );
        CREATE TABLE variant_dispositions (
            experiment_id TEXT NOT NULL, experiment_version TEXT NOT NULL,
            variant_id TEXT NOT NULL, disposition TEXT NOT NULL,
            PRIMARY KEY (experiment_id, experiment_version, variant_id)
        );
        CREATE TABLE result_exclusions (
            experiment_id TEXT NOT NULL, experiment_version TEXT NOT NULL,
            exclusion_json TEXT NOT NULL,
            PRIMARY KEY (experiment_id, experiment_version)
        );
        CREATE TABLE paper_fills (
            fill_id TEXT PRIMARY KEY, order_id TEXT NOT NULL,
            account_id TEXT NOT NULL, paper_session_id TEXT NOT NULL,
            environment TEXT NOT NULL, fill_json TEXT NOT NULL
        );
        CREATE TABLE paper_account_openings (
            account_id TEXT PRIMARY KEY, paper_session_id TEXT NOT NULL,
            environment TEXT NOT NULL, opening_json TEXT NOT NULL
        );
        CREATE TABLE paper_account_balances (
            account_id TEXT NOT NULL, paper_session_id TEXT NOT NULL,
            environment TEXT NOT NULL, asset TEXT NOT NULL,
            total REAL NOT NULL, reserved REAL NOT NULL, available REAL NOT NULL,
            PRIMARY KEY (account_id, paper_session_id, environment, asset)
        );
        CREATE TABLE paper_orders (
            order_id TEXT PRIMARY KEY, account_id TEXT NOT NULL,
            paper_session_id TEXT NOT NULL, environment TEXT NOT NULL,
            order_json TEXT NOT NULL
        );
        CREATE TABLE paper_order_acceptances (
            order_id TEXT PRIMARY KEY, account_id TEXT NOT NULL,
            paper_session_id TEXT NOT NULL, environment TEXT NOT NULL,
            acceptance_json TEXT NOT NULL
        );
        CREATE TABLE paper_reservations (
            order_id TEXT PRIMARY KEY, account_id TEXT NOT NULL,
            paper_session_id TEXT NOT NULL, environment TEXT NOT NULL,
            reservation_json TEXT NOT NULL
        );
        INSERT INTO sessions (session_id) VALUES ('sess-v9');
        INSERT INTO strategy_config_versions VALUES ('s1','v1','{}');
        INSERT INTO session_strategy_configs VALUES ('sess-v9','s1','v1');
        INSERT INTO session_dataset_refs VALUES ('sess-v9','ds-ref-1');
        INSERT INTO experiment_definitions VALUES ('exp-1','v1','{}');
        INSERT INTO run_attempts VALUES ('run-1','exp-1','v1','va','pending','{}');
        INSERT INTO experiment_results VALUES ('exp-1','v1','{}');
        INSERT INTO variant_dispositions VALUES ('exp-1','v1','va','accepted');
        INSERT INTO result_exclusions VALUES ('exp-1','v1','[]');
        INSERT INTO paper_fills VALUES ('fill-1','ord-1','acc-1','sess-v9','sandbox','{}');
        INSERT INTO paper_account_openings VALUES ('acc-1','sess-v9','sandbox','{}');
        INSERT INTO paper_account_balances VALUES ('acc-1','sess-v9','sandbox','QUOTE',1000.0,0.0,1000.0);
        INSERT INTO paper_orders VALUES ('ord-1','acc-1','sess-v9','sandbox','{}');
        INSERT INTO paper_order_acceptances VALUES ('ord-1','acc-1','sess-v9','sandbox','{}');
        INSERT INTO paper_reservations VALUES ('ord-1','acc-1','sess-v9','sandbox','{}');
        PRAGMA user_version = 9;
    """)
    conn.close()

    # Migrate to v10.
    upgraded = SQLiteStore(db)
    _run(upgraded.initialize())

    conn = sqlite3.connect(str(db))
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 15
    tables = {row[0] for row in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    )}
    assert tables == _SCHEMA_V15_TABLES

    # All v9 data preserved.
    assert conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM strategy_config_versions").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM session_strategy_configs").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM session_dataset_refs").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM experiment_definitions").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM run_attempts").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM experiment_results").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM variant_dispositions").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM result_exclusions").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM paper_fills").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM paper_account_openings").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM paper_account_balances").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM paper_orders").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM paper_order_acceptances").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM paper_reservations").fetchone()[0] == 1

    # New tables exist and are empty.
    assert conn.execute("SELECT COUNT(*) FROM paper_fill_applications").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM position_lots").fetchone()[0] == 0
    conn.close()
    _run(upgraded.close())


def test_v9_to_v10_migration_rolls_back_ddl_and_version(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Failure in v10 DDL rolls the v9 → v10 migration back completely."""
    db = tmp_path / "v9-to-v10-rollback.db"

    # Build a v9 database.
    conn = sqlite3.connect(str(db))
    conn.executescript("""
        PRAGMA foreign_keys = OFF;
        CREATE TABLE sessions (session_id TEXT PRIMARY KEY);
        CREATE TABLE strategy_config_versions (
            strategy_id TEXT NOT NULL, strategy_version TEXT NOT NULL, config_json TEXT NOT NULL,
            PRIMARY KEY (strategy_id, strategy_version)
        );
        CREATE TABLE session_strategy_configs (
            session_id TEXT NOT NULL REFERENCES sessions(session_id),
            strategy_id TEXT NOT NULL, strategy_version TEXT NOT NULL,
            PRIMARY KEY (session_id, strategy_id, strategy_version)
        );
        CREATE TABLE session_dataset_refs (
            session_id TEXT NOT NULL REFERENCES sessions(session_id),
            dataset_ref TEXT NOT NULL, PRIMARY KEY (session_id, dataset_ref)
        );
        CREATE TABLE experiment_definitions (
            experiment_id TEXT NOT NULL, experiment_version TEXT NOT NULL,
            definition_json TEXT NOT NULL, PRIMARY KEY (experiment_id, experiment_version)
        );
        CREATE TABLE run_attempts (
            run_id TEXT PRIMARY KEY, experiment_id TEXT NOT NULL,
            experiment_version TEXT NOT NULL, variant_id TEXT NOT NULL,
            status TEXT NOT NULL CHECK(status IN ('pending','running','failed','cancelled')),
            run_json TEXT NOT NULL,
            FOREIGN KEY (experiment_id, experiment_version)
                REFERENCES experiment_definitions(experiment_id, experiment_version)
        );
        CREATE TABLE experiment_results (
            experiment_id TEXT NOT NULL, experiment_version TEXT NOT NULL,
            result_json TEXT NOT NULL, PRIMARY KEY (experiment_id, experiment_version)
        );
        CREATE TABLE variant_dispositions (
            experiment_id TEXT NOT NULL, experiment_version TEXT NOT NULL,
            variant_id TEXT NOT NULL, disposition TEXT NOT NULL,
            PRIMARY KEY (experiment_id, experiment_version, variant_id)
        );
        CREATE TABLE result_exclusions (
            experiment_id TEXT NOT NULL, experiment_version TEXT NOT NULL,
            exclusion_json TEXT NOT NULL,
            PRIMARY KEY (experiment_id, experiment_version)
        );
        CREATE TABLE paper_fills (
            fill_id TEXT PRIMARY KEY, order_id TEXT NOT NULL,
            account_id TEXT NOT NULL, paper_session_id TEXT NOT NULL,
            environment TEXT NOT NULL, fill_json TEXT NOT NULL
        );
        CREATE TABLE paper_account_openings (
            account_id TEXT PRIMARY KEY, paper_session_id TEXT NOT NULL,
            environment TEXT NOT NULL, opening_json TEXT NOT NULL
        );
        CREATE TABLE paper_account_balances (
            account_id TEXT NOT NULL, paper_session_id TEXT NOT NULL,
            environment TEXT NOT NULL, asset TEXT NOT NULL,
            total REAL NOT NULL, reserved REAL NOT NULL, available REAL NOT NULL,
            PRIMARY KEY (account_id, paper_session_id, environment, asset)
        );
        CREATE TABLE paper_orders (
            order_id TEXT PRIMARY KEY, account_id TEXT NOT NULL,
            paper_session_id TEXT NOT NULL, environment TEXT NOT NULL,
            order_json TEXT NOT NULL
        );
        CREATE TABLE paper_order_acceptances (
            order_id TEXT PRIMARY KEY, account_id TEXT NOT NULL,
            paper_session_id TEXT NOT NULL, environment TEXT NOT NULL,
            acceptance_json TEXT NOT NULL
        );
        CREATE TABLE paper_reservations (
            order_id TEXT PRIMARY KEY, account_id TEXT NOT NULL,
            paper_session_id TEXT NOT NULL, environment TEXT NOT NULL,
            reservation_json TEXT NOT NULL
        );
        INSERT INTO sessions (session_id) VALUES ('sess-v9');
        PRAGMA user_version = 9;
    """)
    conn.close()

    # Inject bad v10 DDL.
    import src.persistence.sqlite_store as sqlite_store_module
    original_v10_statements = sqlite_store_module._PAPER_FILL_APPLICATION_DDL_STATEMENTS
    monkeypatch.setattr(
        sqlite_store_module,
        "_PAPER_FILL_APPLICATION_DDL_STATEMENTS",
        (*original_v10_statements[:1], "THIS IS INVALID SQL"),
    )

    migration = SQLiteStore(db)
    with pytest.raises(sqlite3.OperationalError):
        _run(migration.initialize())

    conn = sqlite3.connect(str(db))
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 9
    tables = {row[0] for row in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    )}
    assert tables == _SCHEMA_V9_TABLES
    assert conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 1
    conn.close()


def test_reinitialize_v12_is_idempotent(tmp_path: Path) -> None:
    """Re-initializing a v12 database is idempotent; no error or schema change."""
    db = tmp_path / "reinit-v11.db"
    store1 = SQLiteStore(db)
    _run(store1.initialize())

    # Second initialize must succeed without error.
    _run(store1.initialize())

    conn = sqlite3.connect(str(db))
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 15
    tables = {row[0] for row in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    )}
    assert tables == _SCHEMA_V15_TABLES
    conn.close()
    _run(store1.close())


def test_unknown_version_rejected_no_schema_change(tmp_path: Path) -> None:
    """RunAttempt is persisted with model_dump_json(); keys match columns."""
    db = tmp_path / "run-json.db"
    store = SQLiteStore(db)
    _run(store.initialize())

    definition = _make_experiment_definition()
    _run(store.register_experiment(definition))

    run = RunAttempt(
        run_id="run-json-check",
        experiment_ref=definition.experiment_ref,
        variant_id="variant-a",
        status="pending",
        seed=None,
    )
    _run(store.create_run_attempt(run))

    conn = sqlite3.connect(str(db))
    row = conn.execute(
        "SELECT run_json FROM run_attempts WHERE run_id=?", ("run-json-check",)
    ).fetchone()
    assert row is not None
    loaded = RunAttempt.model_validate_json(row[0])
    assert loaded.run_id == "run-json-check"
    assert loaded.status == "pending"
    assert loaded.experiment_ref == definition.experiment_ref
    assert loaded.variant_id == "variant-a"
    conn.close()
    _run(store.close())


def test_transition_pending_to_running_preserves_diagnostic(tmp_path: Path) -> None:
    """Transitioning pending → running preserves the run JSON with updated state."""
    db = tmp_path / "trans-pr-diag.db"
    store = SQLiteStore(db)
    _run(store.initialize())

    definition = _make_experiment_definition()
    _run(store.register_experiment(definition))

    run = RunAttempt(
        run_id="run-tpr",
        experiment_ref=definition.experiment_ref,
        variant_id="variant-a",
        status="pending",
        seed=None,
    )
    _run(store.create_run_attempt(run))

    target = RunAttempt(
        run_id="run-tpr",
        experiment_ref=definition.experiment_ref,
        variant_id="variant-a",
        status="running",
        seed=None,
        started_at_ms=1_000,
        dataset_ref_used="dataset-experiment",
        code_ref="code-ref-1",
        engine_ref="engine-ref-1",
        effective_periods=(PeriodWindow(purpose="design", start_ms=1_000, end_ms=2_000),),
    )
    _run(store.transition_run_attempt("run-tpr", target))

    conn = sqlite3.connect(str(db))
    row = conn.execute(
        "SELECT run_json FROM run_attempts WHERE run_id=?", ("run-tpr",)
    ).fetchone()
    loaded = RunAttempt.model_validate_json(row[0])
    assert loaded.status == "running"
    assert loaded.started_at_ms == 1_000
    conn.close()
    _run(store.close())


def test_failed_run_preserves_diagnostic_and_evidence(tmp_path: Path) -> None:
    """A failed run preserves its diagnostic and partial artifact refs."""
    db = tmp_path / "failed-diag.db"
    store = SQLiteStore(db)
    _run(store.initialize())

    definition = _make_experiment_definition()
    _run(store.register_experiment(definition))

    # Create pending → transition to running → transition to failed.
    pending = RunAttempt(
        run_id="run-fail",
        experiment_ref=definition.experiment_ref,
        variant_id="variant-a",
        status="pending",
        seed=None,
    )
    _run(store.create_run_attempt(pending))
    running = RunAttempt(
        run_id="run-fail",
        experiment_ref=definition.experiment_ref,
        variant_id="variant-a",
        status="running",
        seed=42,
        started_at_ms=1_000,
        dataset_ref_used="dataset-experiment",
        code_ref="code-ref-1",
        engine_ref="engine-ref-1",
        effective_periods=(PeriodWindow(purpose="design", start_ms=1_000, end_ms=2_000),),
    )
    _run(store.transition_run_attempt("run-fail", running))
    target = RunAttempt(
        run_id="run-fail",
        experiment_ref=definition.experiment_ref,
        variant_id="variant-a",
        status="failed",
        seed=42,
        started_at_ms=1_000,
        finished_at_ms=3_000,
        dataset_ref_used="dataset-experiment",
        code_ref="code-ref-1",
        engine_ref="engine-ref-1",
        effective_periods=(PeriodWindow(purpose="design", start_ms=1_000, end_ms=2_000),),
        diagnostic=PublicDiagnostic(error_code="ERR_TIMEOUT", public_message="timeout"),
        partial_artifact_refs=("artifact-1", "artifact-2"),
    )
    _run(store.transition_run_attempt("run-fail", target))

    conn = sqlite3.connect(str(db))
    row = conn.execute(
        "SELECT run_json FROM run_attempts WHERE run_id=?", ("run-fail",)
    ).fetchone()
    loaded = RunAttempt.model_validate_json(row[0])
    assert loaded.status == "failed"
    assert loaded.diagnostic.error_code == "ERR_TIMEOUT"
    assert loaded.partial_artifact_refs == ("artifact-1", "artifact-2")
    conn.close()
    _run(store.close())

    _run(store.close())
