"""Tests for normalized experiment result registration and loading."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import asyncio
import json
import sqlite3
import pytest

from src.models.experiment_contracts import (
    ExperimentDefinition,
    ExperimentRef,
    ExperimentVariant,
    PeriodWindow,
    RunAttempt,
)
from src.models.normalized_experiment import (
    NormalizedExperimentResult,
    NormalizedMetric,
)
from src.models.paper_contracts import StrategyRef
from src.models.strategy_config import IndicatorInstance, ParameterEntry, StrategyConfig
from src.persistence.sqlite_store import (
    SQLiteStore,
    NormalizedExperimentResultConflictError,
    NormalizedExperimentResultStateError,
)


def _run(coro):
    """Run an async coroutine synchronously."""
    return asyncio.run(coro)


def _make_strategy() -> StrategyConfig:
    return StrategyConfig(
        strategy_ref=StrategyRef(strategy_id="s", strategy_version="v1", decision_id=None),
        indicator_instances=(
            IndicatorInstance(
                instance_id="ema",
                indicator_id="keltner",
                parameters=(
                    ParameterEntry(name="ema_period", value=20),
                    ParameterEntry(name="atr_period", value=14),
                    ParameterEntry(name="multiplier", value=2.0),
                ),
            ),
        ),
    )


def _make_experiment(
    experiment_id: str = "exp-1",
    variant_ids: tuple[str, ...] = ("A", "B", "C"),
) -> ExperimentDefinition:
    variants = tuple(
        ExperimentVariant(
            variant_id=vid,
            strategy_config=_make_strategy(),
            dataset_ref="data/v1",
        )
        for vid in variant_ids
    )
    return ExperimentDefinition(
        experiment_ref=ExperimentRef(experiment_id=experiment_id, experiment_version="v1"),
        hypothesis="test hypothesis",
        planned_periods=(PeriodWindow(purpose="design", start_ms=1, end_ms=100),),
        variants=variants,
    )


def _make_completed_run(
    run_id: str = "run-A",
    experiment_id: str = "exp-1",
    variant_id: str = "A",
) -> RunAttempt:
    return RunAttempt(
        run_id=run_id,
        experiment_ref=ExperimentRef(experiment_id=experiment_id, experiment_version="v1"),
        variant_id=variant_id,
        status="completed",
        started_at_ms=1,
        finished_at_ms=100,
        seed=42,
        dataset_ref_used="data/v1",
        code_ref="code-ref",
        engine_ref="engine-ref",
        effective_periods=(PeriodWindow(purpose="design", start_ms=1, end_ms=100),),
        result_id=f"result-{variant_id}",
    )


def _make_result(variant_id: str = "A", result_id: str = "res-A") -> NormalizedExperimentResult:
    return NormalizedExperimentResult(
        result_id=result_id,
        run_id=f"run-{variant_id}",
        origin_id=f"origin-{variant_id}",
        result_version="v1",
        experiment_ref=ExperimentRef(experiment_id="exp-1", experiment_version="v1"),
        variant_id=variant_id,
        dataset_ref="data/v1",
        strategy_config=_make_strategy(),
        code_ref="code/literal",
        engine_ref="engine/literal",
        risk_ref="risk/literal",
        costs_ref="costs/literal",
        exposure_ref="exposure/literal",
        effective_periods=(PeriodWindow(purpose="design", start_ms=1, end_ms=100),),
        seed=42,
        metrics=(
            NormalizedMetric(metric_id="a-score", unit="ratio", status="available", value=Decimal("1.5")),
            NormalizedMetric(metric_id="z-score", unit="ratio", status="available", value=Decimal("2.0")),
        ),
    )


def _setup_store_with_completed_run(tmp_path: Path, run_id: str = "run-A", variant_id: str = "A") -> tuple[SQLiteStore, ExperimentDefinition]:
    """Create an initialized store with a completed run and experiment.

    Uses direct DB insert for the completed run because transition_run_attempt
    explicitly rejects completed state.
    """
    db = tmp_path / "test.db"
    store = SQLiteStore(db)
    _run(store.initialize())
    exp = _make_experiment()
    _run(store.register_experiment(exp))

    # Insert a pending run via the store (required for experiment FK)
    pending = RunAttempt(
        run_id=run_id,
        experiment_ref=ExperimentRef(experiment_id="exp-1", experiment_version="v1"),
        variant_id=variant_id,
        status="pending",
        seed=None,
    )
    _run(store.create_run_attempt(pending))

    # Directly update the run to completed in the DB (bypassing transition_run_attempt)
    completed = _make_completed_run(run_id=run_id, variant_id=variant_id)
    conn = sqlite3.connect(str(db))
    conn.execute(
        "UPDATE run_attempts SET status=?, run_json=? WHERE run_id=?",
        (
            completed.status,
            json.dumps(completed.model_dump(mode="python")),
            run_id,
        ),
    )
    conn.commit()
    conn.close()

    return store, exp


def _setup_store_with_pending_run(tmp_path: Path, run_id: str = "run-C", variant_id: str = "C") -> tuple[SQLiteStore, ExperimentDefinition]:
    """Create an initialized store with a pending (incomplete) run."""
    db = tmp_path / "test-pending.db"
    store = SQLiteStore(db)
    _run(store.initialize())
    exp = _make_experiment()
    _run(store.register_experiment(exp))

    pending = RunAttempt(
        run_id=run_id,
        experiment_ref=ExperimentRef(experiment_id="exp-1", experiment_version="v1"),
        variant_id=variant_id,
        status="pending",
        seed=None,
    )
    _run(store.create_run_attempt(pending))
    return store, exp


# ── Tests ──────────────────────────────────────────────────────────────────────


def test_register_and_load_valid_result(tmp_path: Path) -> None:
    store, _ = _setup_store_with_completed_run(tmp_path)
    result = _make_result()
    registered = _run(store.register_normalized_experiment_result(result))
    assert registered.result_id == "res-A"

    loaded = _run(store.load_normalized_experiment_result("res-A"))
    assert loaded is not None
    assert loaded.result_id == "res-A"
    assert loaded.variant_id == "A"
    assert loaded.metrics[0].metric_id == "a-score"  # canonical ordering


def test_duplicate_identical_registration_is_noop(tmp_path: Path) -> None:
    store, _ = _setup_store_with_completed_run(tmp_path)
    result = _make_result()
    r1 = _run(store.register_normalized_experiment_result(result))
    r2 = _run(store.register_normalized_experiment_result(result))
    assert r1.result_id == r2.result_id


def test_conflicting_payload_raises(tmp_path: Path) -> None:
    store, _ = _setup_store_with_completed_run(tmp_path)
    _run(store.register_normalized_experiment_result(_make_result()))

    conflicting = NormalizedExperimentResult(
        result_id="res-A",
        run_id="run-A",
        origin_id="origin-X",
        result_version="v2",
        experiment_ref=ExperimentRef(experiment_id="exp-1", experiment_version="v1"),
        variant_id="A",
        dataset_ref="data/v1",
        strategy_config=_make_strategy(),
        code_ref="code/literal",
        engine_ref="engine/literal",
        risk_ref="risk/literal",
        costs_ref="costs/literal",
        exposure_ref="exposure/literal",
        effective_periods=(PeriodWindow(purpose="design", start_ms=1, end_ms=100),),
        seed=99,
        metrics=(NormalizedMetric(metric_id="a-score", unit="ratio", status="available", value=Decimal("9.9")),),
    )
    with pytest.raises(NormalizedExperimentResultConflictError):
        _run(store.register_normalized_experiment_result(conflicting))


def test_missing_run_raises_state_error(tmp_path: Path) -> None:
    db = tmp_path / "test.db"
    store = SQLiteStore(db)
    _run(store.initialize())
    exp = _make_experiment()
    _run(store.register_experiment(exp))

    result = _make_result()
    with pytest.raises(NormalizedExperimentResultStateError):
        _run(store.register_normalized_experiment_result(result))


def test_incomplete_run_raises_state_error(tmp_path: Path) -> None:
    store, _ = _setup_store_with_pending_run(tmp_path)
    result = NormalizedExperimentResult(
        result_id="res-C",
        run_id="run-C",
        origin_id="origin-C",
        result_version="v1",
        experiment_ref=ExperimentRef(experiment_id="exp-1", experiment_version="v1"),
        variant_id="C",
        dataset_ref="data/v1",
        strategy_config=_make_strategy(),
        code_ref="code/literal",
        engine_ref="engine/literal",
        risk_ref="risk/literal",
        costs_ref="costs/literal",
        exposure_ref="exposure/literal",
        effective_periods=(PeriodWindow(purpose="design", start_ms=1, end_ms=100),),
        seed=42,
        metrics=(NormalizedMetric(metric_id="x", unit="ratio", status="available", value=Decimal("1")),),
    )
    with pytest.raises(NormalizedExperimentResultStateError):
        _run(store.register_normalized_experiment_result(result))


def test_load_nonexistent_returns_none(tmp_path: Path) -> None:
    db = tmp_path / "test.db"
    store = SQLiteStore(db)
    _run(store.initialize())

    loaded = _run(store.load_normalized_experiment_result("nonexistent"))
    assert loaded is None


def test_schema_bumps_to_17(tmp_path: Path) -> None:
    db = tmp_path / "schema.db"
    store = SQLiteStore(db)
    _run(store.initialize())

    conn = sqlite3.connect(str(db))
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    conn.close()
    assert version == 17


def test_run_uniqueness_enforced(tmp_path: Path) -> None:
    store, _ = _setup_store_with_completed_run(tmp_path)
    _run(store.register_normalized_experiment_result(_make_result(variant_id="A", result_id="res-A")))

    second = NormalizedExperimentResult(
        result_id="res-B",
        run_id="run-A",  # same run
        origin_id="origin-B",
        result_version="v1",
        experiment_ref=ExperimentRef(experiment_id="exp-1", experiment_version="v1"),
        variant_id="B",
        dataset_ref="data/v1",
        strategy_config=_make_strategy(),
        code_ref="code/literal",
        engine_ref="engine/literal",
        risk_ref="risk/literal",
        costs_ref="costs/literal",
        exposure_ref="exposure/literal",
        effective_periods=(PeriodWindow(purpose="design", start_ms=1, end_ms=100),),
        seed=42,
        metrics=(NormalizedMetric(metric_id="x", unit="ratio", status="available", value=Decimal("1")),),
    )
    with pytest.raises(NormalizedExperimentResultConflictError):
        _run(store.register_normalized_experiment_result(second))


def test_load_experiment_comparison_aggregates_sorted_abc_results(tmp_path: Path) -> None:
    store, _ = _setup_store_with_completed_run(tmp_path)
    for variant in ("B", "C"):
        pending = RunAttempt(
            run_id=f"run-{variant}",
            experiment_ref=ExperimentRef(experiment_id="exp-1", experiment_version="v1"),
            variant_id=variant,
            status="pending",
            seed=None,
        )
        _run(store.create_run_attempt(pending))
        completed = _make_completed_run(run_id=f"run-{variant}", variant_id=variant)
        conn = sqlite3.connect(str(tmp_path / "test.db"))
        conn.execute(
            "UPDATE run_attempts SET status=?, run_json=? WHERE run_id=?",
            (completed.status, completed.model_dump_json(), completed.run_id),
        )
        conn.commit()
        conn.close()

    for variant, value in (("A", "1"), ("B", "3"), ("C", "5")):
        result = _make_result(variant_id=variant, result_id=f"res-{variant}").model_copy(
            update={
                "metrics": (
                    NormalizedMetric(
                        metric_id="z-score",
                        unit="ratio",
                        status="available",
                        value=Decimal("2"),
                    ),
                    NormalizedMetric(
                        metric_id="a-score",
                        unit="ratio",
                        status="available",
                        value=Decimal(value),
                    ),
                )
            }
        )
        _run(store.register_normalized_experiment_result(result))

    report = _run(
        store.load_normalized_experiment_comparison(
            ExperimentRef(experiment_id="exp-1", experiment_version="v1"),
            "comparison-1",
            "v1",
        )
    )
    assert report is not None
    assert [result.variant_id for result in report.results] == ["A", "B", "C"]
    assert [aggregate.metric_id for aggregate in report.aggregates] == [
        "a-score",
        "z-score",
    ]
    aggregate = report.aggregates[0]
    assert aggregate.mean == Decimal("3")
    assert aggregate.minimum == Decimal("1")
    assert aggregate.maximum == Decimal("5")
