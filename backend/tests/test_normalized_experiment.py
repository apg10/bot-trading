"""Contract tests for the immutable normalized experiment manifest."""

from __future__ import annotations

from decimal import Decimal
import json

import pytest
from pydantic import ValidationError

from src.models.experiment_contracts import ExperimentRef, PeriodWindow
from src.models.normalized_experiment import (
    ExperimentComparison,
    ExperimentComparisonReport,
    NormalizedExperimentResult,
    NormalizedMetric,
    NormalizedMetricAggregate,
)
from src.models.paper_contracts import StrategyRef
from src.models.strategy_config import IndicatorInstance, ParameterEntry, StrategyConfig


def _strategy() -> StrategyConfig:
    return StrategyConfig(
        strategy_ref=StrategyRef(
            strategy_id="strategy",
            strategy_version="v1",
            decision_id=None,
        ),
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


def _metric(metric_id: str, value: str = "1") -> NormalizedMetric:
    return NormalizedMetric(
        metric_id=metric_id,
        unit="ratio",
        status="available",
        value=Decimal(value),
    )


def _result(variant_id: str = "A", **changes: object) -> NormalizedExperimentResult:
    payload: dict[str, object] = {
        "result_id": f"result-{variant_id}",
        "run_id": f"run-{variant_id}",
        "origin_id": f"origin-{variant_id}",
        "result_version": "v1",
        "experiment_ref": ExperimentRef(experiment_id="exp", experiment_version="v1"),
        "variant_id": variant_id,
        "dataset_ref": "dataset/literal",
        "strategy_config": _strategy(),
        "code_ref": "code/literal",
        "engine_ref": "engine/literal",
        "risk_ref": "risk/literal",
        "costs_ref": "costs/literal",
        "exposure_ref": "exposure/literal",
        "effective_periods": (
            PeriodWindow(purpose="design", start_ms=1, end_ms=2),
        ),
        "seed": 42,
        "metrics": (_metric("z-score", "2"), _metric("a-score", "1")),
    }
    payload.update(changes)
    return NormalizedExperimentResult(**payload)


def _comparison(*results: NormalizedExperimentResult) -> ExperimentComparison:
    return ExperimentComparison(
        comparison_id="comparison-1",
        comparison_version="v1",
        hypothesis="the variants are compared without selecting a winner",
        normalized_results=results,
    )


def test_valid_abc_manifest_sorts_metrics_and_is_stable() -> None:
    comparison = _comparison(_result("A"), _result("B"), _result("C"))
    assert [metric.metric_id for metric in comparison.normalized_results[0].metrics] == [
        "a-score",
        "z-score",
    ]
    assert comparison.schema_version == "experiment_comparison.v1"
    assert comparison.normalized_results[0].schema_version == (
        "normalized_experiment_result.v1"
    )
    dumped = comparison.model_dump_json()
    assert dumped == comparison.model_dump_json()
    assert json.loads(dumped)["comparison_version"] == "v1"


def test_metric_aggregates_are_frozen_sorted_and_explicit_about_no_values() -> None:
    comparison = _comparison(_result("A"), _result("B"), _result("C"))
    report = ExperimentComparisonReport(
        comparison=comparison,
        aggregates=(
            NormalizedMetricAggregate(
                metric_id="z-score",
                unit="ratio",
                available_count=3,
                mean=Decimal("2"),
                minimum=Decimal("2"),
                maximum=Decimal("2"),
            ),
            NormalizedMetricAggregate(
                metric_id="a-score",
                unit="ratio",
                undefined_count=3,
            ),
        ),
    )
    assert [aggregate.metric_id for aggregate in report.aggregates] == [
        "a-score",
        "z-score",
    ]
    assert report.aggregates[0].mean is None
    assert report.aggregates[1].mean == Decimal("2")
    with pytest.raises(ValidationError):
        report.aggregates[0].mean = Decimal("1")


def test_comparison_requires_three_distinct_variants() -> None:
    with pytest.raises(ValidationError, match="at least three"):
        _comparison(_result("A"), _result("B"))
    with pytest.raises(ValidationError, match="duplicate variant_id"):
        _comparison(_result("A"), _result("A"), _result("C"))


@pytest.mark.parametrize("field", ["experiment_ref", "dataset_ref", "effective_periods", "risk_ref", "costs_ref", "exposure_ref"])
def test_comparison_rejects_incompatible_references(field: str) -> None:
    changed: object
    if field == "experiment_ref":
        changed = ExperimentRef(experiment_id="other", experiment_version="v1")
    elif field == "effective_periods":
        changed = (PeriodWindow(purpose="test", start_ms=3, end_ms=4),)
    else:
        changed = f"different-{field}"
    with pytest.raises(ValidationError):
        _comparison(_result("A"), _result("B", **{field: changed}), _result("C"))


def test_metric_states_are_explicit_and_never_use_zero_as_fallback() -> None:
    available = NormalizedMetric(
        metric_id="count", unit="items", status="available", value=Decimal("0")
    )
    assert available.value == Decimal("0")
    undefined = NormalizedMetric(
        metric_id="return", unit=None, status="undefined", reason="not applicable"
    )
    assert undefined.value is None
    with pytest.raises(ValidationError):
        NormalizedMetric(metric_id="return", unit=None, status="undefined")
    with pytest.raises(ValidationError):
        NormalizedMetric(
            metric_id="return", unit=None, status="insufficient_data", value=Decimal("0"), reason="too few observations"
        )
    with pytest.raises(ValidationError):
        NormalizedMetric(metric_id="return", unit=None, status="available", value=Decimal("1"))


def test_rejects_duplicates_non_finite_empty_extra_and_mutation() -> None:
    with pytest.raises(ValidationError):
        NormalizedExperimentResult(**{**_result("A").model_dump(), "metrics": (_metric("x"), _metric("x"))})
    with pytest.raises(ValidationError):
        _metric("x", "NaN")
    with pytest.raises(ValidationError):
        NormalizedMetric(metric_id="", unit="x", status="available", value=Decimal("1"))
    with pytest.raises(ValidationError):
        NormalizedMetric(metric_id="x", unit="x", status="available", value=Decimal("1"), extra="bad")
    result = _result()
    with pytest.raises(ValidationError):
        result.variant_id = "changed"
    with pytest.raises(ValidationError):
        result.metrics[0].metric_id = "changed"
