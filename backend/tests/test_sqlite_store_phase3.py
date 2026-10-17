"""Phase 3 tests for VariantDiscardRecord and ResultExclusionRecord stateful invariants."""

from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path

import pytest

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
from src.persistence.sqlite_store import (
    ExperimentDefinitionConflictError,
    ExperimentResultStateError,
    ResultExclusionConflictError,
    RunAttemptStateError,
    SQLiteStore,
    VariantDiscardConflictError,
)


# ── helpers ────────────────────────────────────────────────────────────────────

def _run(coro):
    """Run an async coroutine synchronously."""
    return asyncio.run(coro)


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


# ── 21. VariantDiscardRecord — stateful invariants (Phase 3) ─────────────────────

def test_record_variant_discard_round_trip(tmp_path: Path) -> None:
    """record_variant_discard + load_variant_discard round-trips correctly."""
    db = tmp_path / "discard.db"
    store = SQLiteStore(db)
    _run(store.initialize())

    definition = _make_experiment_definition()
    _run(store.register_experiment(definition))

    discard = VariantDiscardRecord(
        experiment_ref=definition.experiment_ref,
        variant_id="variant-a",
        reason="early_stop",
        recorded_at_ms=9_000,
    )
    _run(store.record_variant_discard(discard))

    loaded = _run(store.load_variant_discard(definition.experiment_ref, "variant-a"))
    assert loaded is not None
    assert loaded.experiment_ref == discard.experiment_ref
    assert loaded.variant_id == discard.variant_id
    assert loaded.reason == discard.reason
    assert loaded.recorded_at_ms == discard.recorded_at_ms
    _run(store.close())


def test_record_variant_discard_idempotent(tmp_path: Path) -> None:
    """Identical record_variant_discard calls are idempotent."""
    db = tmp_path / "discard-idem.db"
    store = SQLiteStore(db)
    _run(store.initialize())

    definition = _make_experiment_definition()
    _run(store.register_experiment(definition))

    discard = VariantDiscardRecord(
        experiment_ref=definition.experiment_ref,
        variant_id="variant-a",
        reason="early_stop",
        recorded_at_ms=9_000,
    )
    _run(store.record_variant_discard(discard))
    # Second call with identical payload must not raise.
    _run(store.record_variant_discard(discard))

    loaded = _run(store.load_variant_discard(definition.experiment_ref, "variant-a"))
    assert loaded is not None
    conn = sqlite3.connect(str(db))
    assert conn.execute("SELECT COUNT(*) FROM variant_dispositions").fetchone()[0] == 1
    conn.close()
    _run(store.close())


def test_record_variant_discard_conflict_different_metadata(tmp_path: Path) -> None:
    """Different metadata (reason/recorded_at_ms) on same identity raises."""
    db = tmp_path / "discard-conflict.db"
    store = SQLiteStore(db)
    _run(store.initialize())

    definition = _make_experiment_definition()
    _run(store.register_experiment(definition))

    discard1 = VariantDiscardRecord(
        experiment_ref=definition.experiment_ref,
        variant_id="variant-a",
        reason="early_stop",
        recorded_at_ms=9_000,
    )
    _run(store.record_variant_discard(discard1))

    discard2 = VariantDiscardRecord(
        experiment_ref=definition.experiment_ref,
        variant_id="variant-a",
        reason="manual_override",  # different reason
        recorded_at_ms=9_000,
    )
    with pytest.raises(VariantDiscardConflictError):
        _run(store.record_variant_discard(discard2))

    # Original unchanged.
    loaded = _run(store.load_variant_discard(definition.experiment_ref, "variant-a"))
    assert loaded.reason == "early_stop"
    _run(store.close())


def test_record_variant_discard_unknown_experiment_rejected(tmp_path: Path) -> None:
    """Discarding a variant for a non-existent experiment raises early."""
    db = tmp_path / "discard-no-exp.db"
    store = SQLiteStore(db)
    _run(store.initialize())

    discard = VariantDiscardRecord(
        experiment_ref=ExperimentRef(experiment_id="no-such", experiment_version="v1"),
        variant_id="variant-x",
        reason="test",
        recorded_at_ms=1_000,
    )
    with pytest.raises(ExperimentDefinitionConflictError):
        _run(store.record_variant_discard(discard))


def test_record_variant_discard_unknown_variant_rejected(tmp_path: Path) -> None:
    """Discarding a variant not in the experiment definition raises."""
    db = tmp_path / "discard-no-variant.db"
    store = SQLiteStore(db)
    _run(store.initialize())

    definition = _make_experiment_definition()
    _run(store.register_experiment(definition))

    discard = VariantDiscardRecord(
        experiment_ref=definition.experiment_ref,
        variant_id="nonexistent-variant",
        reason="test",
        recorded_at_ms=1_000,
    )
    with pytest.raises(VariantDiscardConflictError):
        _run(store.record_variant_discard(discard))


def test_record_variant_discard_post_run_rejected(tmp_path: Path) -> None:
    """Discarding after a RunAttempt exists for the variant raises."""
    db = tmp_path / "discard-post-run.db"
    store = SQLiteStore(db)
    _run(store.initialize())

    definition = _make_experiment_definition()
    _run(store.register_experiment(definition))

    run = RunAttempt(
        run_id="run-1",
        experiment_ref=definition.experiment_ref,
        variant_id="variant-a",
        status="pending",
        seed=None,
    )
    _run(store.create_run_attempt(run))

    discard = VariantDiscardRecord(
        experiment_ref=definition.experiment_ref,
        variant_id="variant-a",
        reason="test",
        recorded_at_ms=1_000,
    )
    with pytest.raises(VariantDiscardConflictError):
        _run(store.record_variant_discard(discard))


def test_variant_discard_then_run_rejected(tmp_path: Path) -> None:
    """A persisted pre-run discard prevents later creation of a run."""
    db = tmp_path / "discard-before-run.db"
    store = SQLiteStore(db)
    _run(store.initialize())

    definition = _make_experiment_definition()
    _run(store.register_experiment(definition))
    discard = VariantDiscardRecord(
        experiment_ref=definition.experiment_ref,
        variant_id="variant-a",
        reason="early_stop",
        recorded_at_ms=9_000,
    )
    _run(store.record_variant_discard(discard))

    run = RunAttempt(
        run_id="run-after-discard",
        experiment_ref=definition.experiment_ref,
        variant_id="variant-a",
        status="pending",
        seed=None,
    )
    with pytest.raises(RunAttemptStateError, match="discarded before run"):
        _run(store.create_run_attempt(run))

    assert _run(store.load_run_attempt(run.run_id)) is None
    assert _run(store.load_variant_discard(definition.experiment_ref, "variant-a")) == discard
    _run(store.close())


def test_record_variant_discard_different_variant_ok(tmp_path: Path) -> None:
    """Different variant_id for same experiment is allowed."""
    db = tmp_path / "discard-diff-variant.db"
    store = SQLiteStore(db)
    _run(store.initialize())

    definition = _make_experiment_definition(variant_ids=("variant-a", "variant-b"))
    _run(store.register_experiment(definition))

    discard_a = VariantDiscardRecord(
        experiment_ref=definition.experiment_ref,
        variant_id="variant-a",
        reason="early_stop",
        recorded_at_ms=9_000,
    )
    _run(store.record_variant_discard(discard_a))

    discard_b = VariantDiscardRecord(
        experiment_ref=definition.experiment_ref,
        variant_id="variant-b",
        reason="manual_override",
        recorded_at_ms=10_000,
    )
    _run(store.record_variant_discard(discard_b))

    loaded_a = _run(store.load_variant_discard(definition.experiment_ref, "variant-a"))
    loaded_b = _run(store.load_variant_discard(definition.experiment_ref, "variant-b"))
    assert loaded_a is not None and loaded_b is not None
    assert loaded_a.reason == "early_stop"
    assert loaded_b.reason == "manual_override"
    _run(store.close())


# ── 22. ResultExclusionRecord — stateful invariants (Phase 3) ─────────────────────

def test_record_result_exclusion_round_trip(tmp_path: Path) -> None:
    """record_result_exclusion + load_result_exclusion round-trips correctly."""
    db = tmp_path / "exclusion.db"
    store = SQLiteStore(db)
    _run(store.initialize())

    definition = _make_experiment_definition()
    _run(store.register_experiment(definition))

    run = RunAttempt(
        run_id="run-excl",
        experiment_ref=definition.experiment_ref,
        variant_id="variant-a",
        status="pending",
        seed=None,
    )
    _run(store.create_run_attempt(run))

    running = RunAttempt(
        run_id="run-excl",
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
    _run(store.transition_run_attempt("run-excl", running))

    completed = RunAttempt(
        run_id="run-excl",
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
        result_id="result-excl-1",
    )
    result = ExperimentResult(
        result_id="result-excl-1",
        run_id="run-excl",
        artifact_refs=("art-1", "art-2"),
    )
    _run(store.publish_experiment_result(completed, result))

    exclusion = ResultExclusionRecord(
        result_id="result-excl-1",
        reason="data_contamination",
        recorded_at_ms=7_000,
    )
    _run(store.record_result_exclusion(exclusion))

    loaded = _run(store.load_result_exclusion("result-excl-1"))
    assert loaded is not None
    assert loaded.result_id == exclusion.result_id
    assert loaded.reason == exclusion.reason
    assert loaded.recorded_at_ms == exclusion.recorded_at_ms
    _run(store.close())


def test_record_result_exclusion_idempotent(tmp_path: Path) -> None:
    """Identical record_result_exclusion calls are idempotent."""
    db = tmp_path / "exclusion-idem.db"
    store = SQLiteStore(db)
    _run(store.initialize())

    definition = _make_experiment_definition()
    _run(store.register_experiment(definition))

    run = RunAttempt(
        run_id="run-excl-idem",
        experiment_ref=definition.experiment_ref,
        variant_id="variant-a",
        status="pending",
        seed=None,
    )
    _run(store.create_run_attempt(run))

    running = RunAttempt(
        run_id="run-excl-idem",
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
    _run(store.transition_run_attempt("run-excl-idem", running))

    completed = RunAttempt(
        run_id="run-excl-idem",
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
        result_id="result-excl-idem-1",
    )
    result = ExperimentResult(
        result_id="result-excl-idem-1",
        run_id="run-excl-idem",
        artifact_refs=("art-1", "art-2"),
    )
    _run(store.publish_experiment_result(completed, result))

    exclusion = ResultExclusionRecord(
        result_id="result-excl-idem-1",
        reason="data_contamination",
        recorded_at_ms=7_000,
    )
    _run(store.record_result_exclusion(exclusion))
    # Second call with identical payload must not raise.
    _run(store.record_result_exclusion(exclusion))

    loaded = _run(store.load_result_exclusion("result-excl-idem-1"))
    assert loaded is not None
    conn = sqlite3.connect(str(db))
    assert conn.execute("SELECT COUNT(*) FROM result_exclusions").fetchone()[0] == 1
    conn.close()
    _run(store.close())


def test_record_result_exclusion_conflict_different_metadata(tmp_path: Path) -> None:
    """Different metadata on same result_id raises."""
    db = tmp_path / "exclusion-conflict.db"
    store = SQLiteStore(db)
    _run(store.initialize())

    definition = _make_experiment_definition()
    _run(store.register_experiment(definition))

    run = RunAttempt(
        run_id="run-excl-conf",
        experiment_ref=definition.experiment_ref,
        variant_id="variant-a",
        status="pending",
        seed=None,
    )
    _run(store.create_run_attempt(run))

    running = RunAttempt(
        run_id="run-excl-conf",
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
    _run(store.transition_run_attempt("run-excl-conf", running))

    completed = RunAttempt(
        run_id="run-excl-conf",
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
        result_id="result-excl-conf-1",
    )
    result = ExperimentResult(
        result_id="result-excl-conf-1",
        run_id="run-excl-conf",
        artifact_refs=("art-1", "art-2"),
    )
    _run(store.publish_experiment_result(completed, result))

    exclusion1 = ResultExclusionRecord(
        result_id="result-excl-conf-1",
        reason="data_contamination",
        recorded_at_ms=7_000,
    )
    _run(store.record_result_exclusion(exclusion1))

    exclusion2 = ResultExclusionRecord(
        result_id="result-excl-conf-1",
        reason="manual_override",  # different reason
        recorded_at_ms=7_000,
    )
    with pytest.raises(ResultExclusionConflictError):
        _run(store.record_result_exclusion(exclusion2))

    # Original unchanged.
    loaded = _run(store.load_result_exclusion("result-excl-conf-1"))
    assert loaded.reason == "data_contamination"
    _run(store.close())


def test_record_result_exclusion_unknown_result_rejected(tmp_path: Path) -> None:
    """Excluding a non-existent result raises early."""
    db = tmp_path / "exclusion-no-result.db"
    store = SQLiteStore(db)
    _run(store.initialize())

    exclusion = ResultExclusionRecord(
        result_id="nonexistent-result",
        reason="test",
        recorded_at_ms=1_000,
    )
    with pytest.raises(ExperimentResultStateError):
        _run(store.record_result_exclusion(exclusion))


def test_load_nonexistent_variant_discard_returns_none(tmp_path: Path) -> None:
    """Loading a non-existent variant discard returns None."""
    db = tmp_path / "discard-none.db"
    store = SQLiteStore(db)
    _run(store.initialize())

    result = _run(store.load_variant_discard(
        ExperimentRef(experiment_id="no-such", experiment_version="v1"),
        "variant-x",
    ))
    assert result is None
    _run(store.close())


def test_load_nonexistent_result_exclusion_returns_none(tmp_path: Path) -> None:
    """Loading a non-existent result exclusion returns None."""
    db = tmp_path / "exclusion-none.db"
    store = SQLiteStore(db)
    _run(store.initialize())

    result = _run(store.load_result_exclusion("nonexistent-result"))
    assert result is None
    _run(store.close())


# ── 23. Concurrency isolation (Phase 3) ───────────────────────────────────────────

def test_concurrent_variant_discard_serialized_writes(tmp_path: Path) -> None:
    """Concurrent writes to variant_dispositions are serialized via lock."""
    db = tmp_path / "discard-concurrent.db"
    store = SQLiteStore(db)
    _run(store.initialize())

    definition = _make_experiment_definition(variant_ids=("variant-a", "variant-b"))
    _run(store.register_experiment(definition))

    # Two different variants can be discarded concurrently without error.
    async def discard_variant(variant_id: str, reason: str) -> None:
        discard = VariantDiscardRecord(
            experiment_ref=definition.experiment_ref,
            variant_id=variant_id,
            reason=reason,
            recorded_at_ms=1_000,
        )
        await store.record_variant_discard(discard)

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(
            asyncio.gather(
                discard_variant("variant-a", "early_stop"),
                discard_variant("variant-b", "manual_override"),
            )
        )
    finally:
        loop.close()

    loaded_a = _run(store.load_variant_discard(definition.experiment_ref, "variant-a"))
    loaded_b = _run(store.load_variant_discard(definition.experiment_ref, "variant-b"))
    assert loaded_a is not None and loaded_a.reason == "early_stop"
    assert loaded_b is not None and loaded_b.reason == "manual_override"
    _run(store.close())


def test_concurrent_discard_and_run_are_mutually_exclusive(tmp_path: Path) -> None:
    """Separate store instances serialize a discard/run race in SQLite."""
    db = tmp_path / "discard-run-race.db"
    discard_store = SQLiteStore(db)
    run_store = SQLiteStore(db)
    _run(discard_store.initialize())
    definition = _make_experiment_definition()
    _run(discard_store.register_experiment(definition))
    _run(run_store.initialize())

    discard = VariantDiscardRecord(
        experiment_ref=definition.experiment_ref,
        variant_id="variant-a",
        reason="early_stop",
        recorded_at_ms=9_000,
    )
    run = RunAttempt(
        run_id="run-raced-with-discard",
        experiment_ref=definition.experiment_ref,
        variant_id="variant-a",
        status="pending",
        seed=None,
    )

    async def _capture(coro):
        try:
            await coro
            return None
        except Exception as exc:  # Return the competing operation's domain error.
            return exc

    async def _race():
        return await asyncio.gather(
            _capture(discard_store.record_variant_discard(discard)),
            _capture(run_store.create_run_attempt(run)),
        )

    discard_outcome, run_outcome = _run(_race())
    assert (discard_outcome is None) != (run_outcome is None)

    persisted_discard = _run(
        discard_store.load_variant_discard(definition.experiment_ref, "variant-a")
    )
    persisted_run = _run(run_store.load_run_attempt(run.run_id))
    assert (persisted_discard is not None) != (persisted_run is not None)
    if persisted_discard is not None:
        assert isinstance(run_outcome, RunAttemptStateError)
        assert persisted_discard == discard
    else:
        assert isinstance(discard_outcome, VariantDiscardConflictError)
        assert persisted_run == run

    _run(discard_store.close())
    _run(run_store.close())


def test_concurrent_conflicting_discards_same_identity_have_one_winner(
    tmp_path: Path,
) -> None:
    """Concurrent differing dispositions for one identity cannot overwrite."""
    db = tmp_path / "same-discard-race.db"
    store_a = SQLiteStore(db)
    store_b = SQLiteStore(db)
    _run(store_a.initialize())
    definition = _make_experiment_definition()
    _run(store_a.register_experiment(definition))
    _run(store_b.initialize())

    discard_a = VariantDiscardRecord(
        experiment_ref=definition.experiment_ref,
        variant_id="variant-a",
        reason="reason-a",
        recorded_at_ms=9_000,
    )
    discard_b = VariantDiscardRecord(
        experiment_ref=definition.experiment_ref,
        variant_id="variant-a",
        reason="reason-b",
        recorded_at_ms=9_001,
    )

    async def _capture(coro):
        try:
            await coro
            return None
        except Exception as exc:
            return exc

    async def _race():
        return await asyncio.gather(
            _capture(store_a.record_variant_discard(discard_a)),
            _capture(store_b.record_variant_discard(discard_b)),
        )

    outcome_a, outcome_b = _run(_race())
    assert (outcome_a is None) != (outcome_b is None)
    assert isinstance(
        outcome_b if outcome_a is None else outcome_a,
        VariantDiscardConflictError,
    )
    stored = _run(store_a.load_variant_discard(definition.experiment_ref, "variant-a"))
    assert stored in (discard_a, discard_b)

    _run(store_a.close())
    _run(store_b.close())


def test_transaction_abort_leaves_no_partial_writes(tmp_path: Path) -> None:
    """Failed transaction rolls back completely — no partial writes."""
    db = tmp_path / "discard-rollback.db"
    store = SQLiteStore(db)
    _run(store.initialize())

    definition = _make_experiment_definition()
    _run(store.register_experiment(definition))

    discard1 = VariantDiscardRecord(
        experiment_ref=definition.experiment_ref,
        variant_id="variant-a",
        reason="early_stop",
        recorded_at_ms=9_000,
    )
    _run(store.record_variant_discard(discard1))

    # This should fail (different metadata on same identity).
    discard2 = VariantDiscardRecord(
        experiment_ref=definition.experiment_ref,
        variant_id="variant-a",
        reason="manual_override",
        recorded_at_ms=9_000,
    )
    with pytest.raises(VariantDiscardConflictError):
        _run(store.record_variant_discard(discard2))

    # Verify only the first record exists — no partial state.
    loaded = _run(store.load_variant_discard(definition.experiment_ref, "variant-a"))
    assert loaded is not None and loaded.reason == "early_stop"
    _run(store.close())


def test_pure_in_memory_test_isolation(tmp_path: Path) -> None:
    """Each test gets its own database file — no cross-test pollution."""
    db1 = tmp_path / "isolation-1.db"
    store1 = SQLiteStore(db1)
    _run(store1.initialize())

    definition1 = _make_experiment_definition()
    _run(store1.register_experiment(definition1))

    discard1 = VariantDiscardRecord(
        experiment_ref=definition1.experiment_ref,
        variant_id="variant-a",
        reason="test-isolation",
        recorded_at_ms=1_000,
    )
    _run(store1.record_variant_discard(discard1))

    db2 = tmp_path / "isolation-2.db"
    store2 = SQLiteStore(db2)
    _run(store2.initialize())

    # store2 should have no variant_dispositions.
    loaded = _run(store2.load_variant_discard(
        definition1.experiment_ref, "variant-a"
    ))
    assert loaded is None  # different database, no pollution
    _run(store1.close())
    _run(store2.close())


def test_pure_temp_file_test_isolation(tmp_path: Path) -> None:
    """Tests using temp files are fully isolated from each other."""
    db = tmp_path / "temp-isolation.db"
    store = SQLiteStore(db)
    _run(store.initialize())

    # Verify the database file exists and is a valid SQLite database.
    import sqlite3 as sync_sqlite
    conn = sync_sqlite.connect(str(db))
    tables = {row[0] for row in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    )}
    assert "variant_dispositions" in tables
    assert "result_exclusions" in tables
    conn.close()
    _run(store.close())


# ── 24. FK resolution without dropping original records ───────────────────────────

def test_variant_discard_fk_resolution(tmp_path: Path) -> None:
    """FK resolution works — variant_id must exist in experiment definition."""
    db = tmp_path / "discard-fk.db"
    store = SQLiteStore(db)
    _run(store.initialize())

    definition = _make_experiment_definition()
    _run(store.register_experiment(definition))

    # Valid variant_id from definition.
    discard = VariantDiscardRecord(
        experiment_ref=definition.experiment_ref,
        variant_id="variant-a",
        reason="test-fk",
        recorded_at_ms=1_000,
    )
    _run(store.record_variant_discard(discard))

    # Non-existent variant_id.
    discard_bad = VariantDiscardRecord(
        experiment_ref=definition.experiment_ref,
        variant_id="nonexistent",
        reason="test-fk",
        recorded_at_ms=1_000,
    )
    with pytest.raises(VariantDiscardConflictError):
        _run(store.record_variant_discard(discard_bad))

    # Original still intact.
    loaded = _run(store.load_variant_discard(definition.experiment_ref, "variant-a"))
    assert loaded is not None and loaded.reason == "test-fk"
    _run(store.close())


def test_result_exclusion_fk_resolution(tmp_path: Path) -> None:
    """FK resolution works — result_id must exist in experiment_results."""
    db = tmp_path / "exclusion-fk.db"
    store = SQLiteStore(db)
    _run(store.initialize())

    definition = _make_experiment_definition()
    _run(store.register_experiment(definition))

    run = RunAttempt(
        run_id="run-excl-fk",
        experiment_ref=definition.experiment_ref,
        variant_id="variant-a",
        status="pending",
        seed=None,
    )
    _run(store.create_run_attempt(run))

    running = RunAttempt(
        run_id="run-excl-fk",
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
    _run(store.transition_run_attempt("run-excl-fk", running))

    completed = RunAttempt(
        run_id="run-excl-fk",
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
        result_id="result-excl-fk-1",
    )
    result = ExperimentResult(
        result_id="result-excl-fk-1",
        run_id="run-excl-fk",
        artifact_refs=("art-1", "art-2"),
    )
    _run(store.publish_experiment_result(completed, result))

    # Valid result_id.
    exclusion = ResultExclusionRecord(
        result_id="result-excl-fk-1",
        reason="test-fk",
        recorded_at_ms=7_000,
    )
    _run(store.record_result_exclusion(exclusion))

    # Non-existent result_id.
    exclusion_bad = ResultExclusionRecord(
        result_id="nonexistent-result",
        reason="test-fk",
        recorded_at_ms=7_000,
    )
    with pytest.raises(ExperimentResultStateError):
        _run(store.record_result_exclusion(exclusion_bad))

    # Original still intact.
    loaded = _run(store.load_result_exclusion("result-excl-fk-1"))
    assert loaded is not None and loaded.reason == "test-fk"
    _run(store.close())
