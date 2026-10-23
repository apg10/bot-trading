"""HTTP contract tests for the read-only laboratory API."""

from __future__ import annotations

import asyncio
import json
import sqlite3
from decimal import Decimal
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.laboratory import router
from src.models.experiment_contracts import (
    ExperimentDefinition,
    ExperimentRef,
    ExperimentVariant,
    PeriodWindow,
    RunAttempt,
)
from src.models.normalized_experiment import NormalizedExperimentResult, NormalizedMetric
from src.models.paper_contracts import StrategyRef
from src.models.strategy_config import IndicatorInstance, ParameterEntry, StrategyConfig
from src.persistence.sqlite_store import SQLiteStore


def _run(coro):
    return asyncio.run(coro)


def _strategy() -> StrategyConfig:
    return StrategyConfig(
        strategy_ref=StrategyRef(strategy_id="strategy", strategy_version="v1", decision_id=None),
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


def _experiment(experiment_id: str, variants: tuple[str, ...]) -> ExperimentDefinition:
    return ExperimentDefinition(
        experiment_ref=ExperimentRef(experiment_id=experiment_id, experiment_version="v1"),
        hypothesis=f"hypothesis-{experiment_id}",
        planned_periods=(PeriodWindow(purpose="design", start_ms=1, end_ms=100),),
        variants=tuple(
            ExperimentVariant(
                variant_id=variant,
                strategy_config=_strategy(),
                dataset_ref="dataset/laboratory",
            )
            for variant in variants
        ),
    )


def _completed_run(experiment_id: str, variant: str) -> RunAttempt:
    return RunAttempt(
        run_id=f"run-{experiment_id}-{variant}",
        experiment_ref=ExperimentRef(experiment_id=experiment_id, experiment_version="v1"),
        variant_id=variant,
        status="completed",
        started_at_ms=1,
        finished_at_ms=100,
        seed=7,
        dataset_ref_used="dataset/laboratory",
        code_ref="code/laboratory",
        engine_ref="engine/laboratory",
        effective_periods=(PeriodWindow(purpose="design", start_ms=1, end_ms=100),),
        result_id=f"result-{experiment_id}-{variant}",
    )


def _result(experiment_id: str, variant: str, value: str) -> NormalizedExperimentResult:
    return NormalizedExperimentResult(
        result_id=f"result-{experiment_id}-{variant}",
        run_id=f"run-{experiment_id}-{variant}",
        origin_id=f"origin-{experiment_id}-{variant}",
        result_version="v1",
        experiment_ref=ExperimentRef(experiment_id=experiment_id, experiment_version="v1"),
        variant_id=variant,
        dataset_ref="dataset/laboratory",
        strategy_config=_strategy(),
        code_ref="code/laboratory",
        engine_ref="engine/laboratory",
        risk_ref="risk/opaque",
        costs_ref="costs/opaque",
        exposure_ref="exposure/opaque",
        effective_periods=(PeriodWindow(purpose="design", start_ms=1, end_ms=100),),
        seed=7,
        metrics=(
            NormalizedMetric(
                metric_id="return",
                unit="percent",
                status="available",
                value=Decimal(value),
            ),
            NormalizedMetric(
                metric_id="drawdown",
                unit="USD",
                status="undefined",
                reason="not provided",
            ),
        ),
    )


def _store_with_results(
    tmp_path: Path,
    experiment_id: str = "exp-lab",
    variants: tuple[str, ...] = ("A", "B", "C"),
) -> SQLiteStore:
    store = SQLiteStore(tmp_path / f"{experiment_id}.db")
    _run(store.initialize())
    _run(store.register_experiment(_experiment(experiment_id, variants)))
    for variant in variants:
        pending = RunAttempt(
            run_id=f"run-{experiment_id}-{variant}",
            experiment_ref=ExperimentRef(experiment_id=experiment_id, experiment_version="v1"),
            variant_id=variant,
            status="pending",
            seed=None,
        )
        _run(store.create_run_attempt(pending))
        completed = _completed_run(experiment_id, variant)
        conn = sqlite3.connect(str(tmp_path / f"{experiment_id}.db"))
        conn.execute(
            "UPDATE run_attempts SET status=?, run_json=? WHERE run_id=?",
            (completed.status, json.dumps(completed.model_dump(mode="python")), completed.run_id),
        )
        conn.commit()
        conn.close()
        value = str(len(variants)) + ".12345678901234567890"
        _run(store.register_normalized_experiment_result(_result(experiment_id, variant, value)))
    return store


def _client(store: SQLiteStore | None) -> TestClient:
    app = FastAPI()
    app.include_router(router)
    if store is not None:
        app.state._sqlite_store = store
    return TestClient(app)


def test_single_result_preserves_decimal_unit_and_opaque_fields(tmp_path: Path) -> None:
    store = _store_with_results(tmp_path, variants=("A",))
    try:
        with _client(store) as client:
            response = client.get("/api/laboratory/results/result-exp-lab-A")
        assert response.status_code == 200
        data = response.json()
        metric = next(item for item in data["metrics"] if item["metric_id"] == "return")
        assert metric["value"] == "1.12345678901234567890"
        assert metric["unit"] == "percent"
        assert data["costs_ref"] == "costs/opaque"
        assert data["exposure_ref"] == "exposure/opaque"
        assert data["effective_periods"][0]["purpose"] == "design"
    finally:
        _run(store.close())


def test_comparison_success_preserves_aggregates_and_unit(tmp_path: Path) -> None:
    store = _store_with_results(tmp_path)
    try:
        with _client(store) as client:
            response = client.get(
                "/api/laboratory/comparisons/exp-lab/v1",
                params={"comparison_id": "cmp-1", "comparison_version": "v1"},
            )
        assert response.status_code == 200
        data = response.json()
        assert len(data["comparison"]["normalized_results"]) == 3
        aggregate = next(item for item in data["aggregates"] if item["metric_id"] == "return")
        assert aggregate["unit"] == "percent"
        assert aggregate["mean"] == "3.12345678901234567890"
    finally:
        _run(store.close())


def test_missing_and_insufficient_comparisons_have_explicit_statuses(tmp_path: Path) -> None:
    store = _store_with_results(tmp_path, variants=("A",))
    try:
        with _client(store) as client:
            assert client.get("/api/laboratory/results/missing").status_code == 404
            assert client.get(
                "/api/laboratory/comparisons/no-results/v1",
                params={"comparison_id": "cmp", "comparison_version": "v1"},
            ).status_code == 404
            assert client.get(
                "/api/laboratory/comparisons/exp-lab/v1",
                params={"comparison_id": "cmp", "comparison_version": "v1"},
            ).status_code == 409
    finally:
        _run(store.close())


def test_store_unavailable_is_503() -> None:
    with _client(None) as client:
        response = client.get("/api/laboratory/results/anything")
    assert response.status_code == 503


def test_comparison_isolated_by_experiment_reference(tmp_path: Path) -> None:
    store = _store_with_results(tmp_path, experiment_id="exp-a")
    _run(store.register_experiment(_experiment("exp-b", ("A", "B", "C"))))
    try:
        with _client(store) as client:
            response = client.get(
                "/api/laboratory/comparisons/exp-b/v1",
                params={"comparison_id": "cmp", "comparison_version": "v1"},
            )
        assert response.status_code == 404
    finally:
        _run(store.close())
