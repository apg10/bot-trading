"""Deterministic tests for experiment DTOs (experiment_contracts).

Cobertura por secciones:
  1. Imports/helpers + IDs, schema, ExperimentDefinition, variantes y periodos.
  2. RunAttempt: estados, campos requeridos/prohibidos, transiciones y timestamps.
  3. ExperimentResult, diagnósticos, artifact refs y registros separados de descarte/exclusión.
  4. Inmutabilidad/extra-forbid y relaciones puras.

Gates:
    PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONDONTWRITEBYTECODE=1 \
        .venv/bin/python -B -m pytest -q -p no:cacheprovider --capture=sys \
        tests/test_experiment_contracts.py
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from pydantic import BaseModel, ValidationError

from src.models.dataset_ref import DatasetRef
from src.models.experiment_contracts import (
    ExperimentDefinition,
    ExperimentRef,
    ExperimentResult,
    ExperimentVariant,
    PeriodWindow,
    PublicDiagnostic,
    RunAttempt,
    VALID_TRANSITIONS,
    TERMINAL_STATES,
    validate_transition,
    is_terminal,
    resolve_run_variant,
    run_matches_definition,
    result_matches_completed_run,
)
from src.models.strategy_config import StrategyConfig
from src.models.paper_contracts import StrategyRef

# ── Fixtures helpers ───────────────────────────────────────────────────────────


def _make_exp_ref(**kw: Any) -> ExperimentRef:
    return ExperimentRef(
        experiment_id=kw.get("experiment_id", "exp-001"),
        experiment_version=kw.get("experiment_version", "v1"),
    )


def _make_strategy_ref(**kw: Any) -> StrategyRef:
    return StrategyRef(
        strategy_id=kw.get("strategy_id", "test_strat"),
        strategy_version=kw.get("strategy_version", "v2"),
        decision_id=kw.get("decision_id", None),
    )


def _make_variant(**kw: Any) -> Any:
    from src.models.strategy_config import StrategyConfig, IndicatorInstance, ParameterEntry
    strat_ref = kw.get("strategy_ref", _make_strategy_ref())
    ema_period = kw.get("ema_period", 20)
    inst = IndicatorInstance(
        instance_id="inst1",
        indicator_id="keltner",
        parameters=(
            ParameterEntry(name="ema_period", value=ema_period),
            ParameterEntry(name="atr_period", value=14),
            ParameterEntry(name="multiplier", value=2.0),
        ),
    )
    config = StrategyConfig(strategy_ref=strat_ref, indicator_instances=(inst,))
    dataset = kw.get("dataset_ref", "ds/opaque-ref-001")
    return ExperimentVariant(
        variant_id=kw.get("variant_id", "var-a"),
        strategy_config=config,
        dataset_ref=dataset,
    )


def _make_period(**kw: Any) -> PeriodWindow:
    return PeriodWindow(
        purpose=kw.get("purpose", "design"),
        start_ms=kw.get("start_ms", 1000),
        end_ms=kw.get("end_ms", 2000),
    )


def _make_definition(**kw: Any) -> ExperimentDefinition:
    exp_ref = kw.get("experiment_ref", _make_exp_ref())
    hypothesis = kw.get("hypothesis", "H0: signal improves win-rate")
    periods = kw.get("periods", (_make_period(),))
    variants = kw.get("variants", (_make_variant(),))
    return ExperimentDefinition(
        experiment_ref=exp_ref,
        hypothesis=hypothesis,
        planned_periods=periods,
        variants=variants,
    )


def _make_run(**kw: Any) -> RunAttempt:
    exp_ref = kw.get("experiment_ref", _make_exp_ref())
    return RunAttempt(
        schema_version=kw.get("schema_version", "experiment_run.v1"),
        run_id=kw.get("run_id", "run-001"),
        experiment_ref=exp_ref,
        variant_id=kw.get("variant_id", "var-a"),
        status=kw.get("status", "pending"),
        dataset_ref_used=kw.get("dataset_ref_used", None),
        code_ref=kw.get("code_ref", None),
        engine_ref=kw.get("engine_ref", None),
        started_at_ms=kw.get("started_at_ms", None),
        finished_at_ms=kw.get("finished_at_ms", None),
        seed=kw.get("seed", None),
        effective_periods=kw.get("effective_periods", ()),
        diagnostic=kw.get("diagnostic", None),
        partial_artifact_refs=kw.get("partial_artifact_refs", ()),
        result_id=kw.get("result_id", None),
    )


def _make_result(**kw: Any) -> ExperimentResult:
    return ExperimentResult(
        result_id=kw.get("result_id", "res-001"),
        run_id=kw.get("run_id", "run-001"),
        artifact_refs=kw.get("artifact_refs", ("art-1",)),
    )


def _make_diagnostic(**kw: Any) -> PublicDiagnostic:
    return PublicDiagnostic(
        error_code=kw.get("error_code", "ERR_TIMEOUT"),
        public_message=kw.get("public_message", "Run exceeded time budget"),
    )


# ═══════════════════════════════════════════════════════════════════════════════
#  Section 1: IDs, schema, ExperimentDefinition, variantes y periodos
# ═══════════════════════════════════════════════════════════════════════════════

class TestExperimentRef:
    def test_frozen_id_and_version(self):
        ref = _make_exp_ref()
        assert ref.experiment_id == "exp-001"
        assert ref.experiment_version == "v1"
        with pytest.raises(ValueError, match="frozen_instance"):
            ref.experiment_id = "changed"

    def test_reference_has_no_duplicate_schema_version(self):
        with pytest.raises(ValidationError):
            ExperimentRef(
                experiment_id="exp-001",
                experiment_version="v1",
                schema_version="experiment_ref.v1",
            )

    def test_empty_experiment_id_rejected(self):
        with pytest.raises(ValidationError, match="non-empty"):
            ExperimentRef(experiment_id="", experiment_version="v1")

    def test_whitespace_only_id_rejected(self):
        with pytest.raises(ValidationError, match="non-empty"):
            ExperimentRef(experiment_id="   ", experiment_version="v1")

    def test_non_string_id_rejected(self):
        with pytest.raises(ValidationError):
            ExperimentRef(experiment_id=123, experiment_version="v1")  # type: ignore

    def test_preserves_literal_spaces(self):
        ref = ExperimentRef(experiment_id=" exp-001 ", experiment_version=" v1 ")
        assert ref.experiment_id == " exp-001 "
        assert ref.experiment_version == " v1 "

    @pytest.mark.parametrize("field", ("experiment_id", "experiment_version"))
    def test_both_reference_fields_reject_non_strings(self, field):
        payload = {"experiment_id": "e1", "experiment_version": "v1"}
        payload[field] = 7
        with pytest.raises(ValidationError):
            ExperimentRef(**payload)  # type: ignore[arg-type]


class TestPeriodWindow:
    def test_valid_period(self):
        p = _make_period()
        assert p.purpose == "design"
        assert p.start_ms == 1000
        assert p.end_ms == 2000

    def test_purpose_values(self):
        for purpose in ("design", "validation", "test"):
            p = _make_period(purpose=purpose)
            assert p.purpose == purpose

    def test_invalid_purpose_rejected(self):
        with pytest.raises(ValidationError):
            PeriodWindow(purpose="backtest", start_ms=100, end_ms=200)  # type: ignore

    def test_start_equal_end_rejected(self):
        with pytest.raises(ValidationError, match="end_ms must be strictly greater"):
            PeriodWindow(purpose="design", start_ms=1000, end_ms=1000)

    def test_start_after_end_rejected(self):
        with pytest.raises(ValidationError, match="end_ms must be strictly greater"):
            PeriodWindow(purpose="design", start_ms=2000, end_ms=1000)

    def test_negative_start_rejected(self):
        with pytest.raises(ValidationError):
            PeriodWindow(purpose="design", start_ms=-1, end_ms=1000)  # type: ignore[arg-type]

    def test_zero_end_rejected(self):
        with pytest.raises(ValidationError):
            PeriodWindow(purpose="design", start_ms=0, end_ms=0)

    def test_start_zero_valid(self):
        p = PeriodWindow(purpose="test", start_ms=0, end_ms=1)
        assert p.start_ms == 0

    def test_bool_timestamps_rejected_as_integers(self):
        with pytest.raises(ValidationError):
            PeriodWindow(purpose="design", start_ms=True, end_ms=2)  # type: ignore[arg-type]
        with pytest.raises(ValidationError):
            PeriodWindow(purpose="design", start_ms=1, end_ms=False)  # type: ignore[arg-type]

    def test_large_timestamps(self):
        p = PeriodWindow(purpose="validation", start_ms=1700000000000, end_ms=1800000000000)
        assert p.start_ms == 1700000000000

    def test_utc_millisecond_window_is_start_inclusive_end_exclusive(self):
        p = PeriodWindow(purpose="test", start_ms=10, end_ms=20)
        assert p.start_ms <= 10 < p.end_ms
        assert not p.start_ms <= 20 < p.end_ms

    def test_frozen(self):
        p = _make_period()
        with pytest.raises(ValueError, match="frozen_instance"):
            p.purpose = "changed"


class TestExperimentVariant:
    def test_valid_variant(self):
        v = _make_variant()
        assert v.variant_id == "var-a"
        assert isinstance(v.strategy_config, StrategyConfig)

    def test_unique_variant_id_preserved(self):
        v = _make_variant(variant_id="unique-id")
        assert v.variant_id == "unique-id"

    def test_empty_variant_id_rejected(self):
        strat_ref = _make_strategy_ref()
        from src.models.strategy_config import StrategyConfig, IndicatorInstance, ParameterEntry
        inst = IndicatorInstance(
            instance_id="i1", indicator_id="keltner",
            parameters=(
                ParameterEntry(name="ema_period", value=20),
                ParameterEntry(name="atr_period", value=14),
                ParameterEntry(name="multiplier", value=2.0),
            ),
        )
        config = StrategyConfig(strategy_ref=strat_ref, indicator_instances=(inst,))
        with pytest.raises(ValidationError, match="non-empty"):
            ExperimentVariant(variant_id="", strategy_config=config, dataset_ref="ds/1")

    @pytest.mark.parametrize("variant_id", ("   ", "\t\n"))
    def test_whitespace_only_variant_id_rejected(self, variant_id):
        v = _make_variant()
        with pytest.raises(ValidationError, match="non-empty"):
            ExperimentVariant(
                variant_id=variant_id,
                strategy_config=v.strategy_config,
                dataset_ref=v.dataset_ref,
            )

    def test_variant_id_is_strict_and_preserved(self):
        v = _make_variant(variant_id="  variant-A  ")
        assert v.variant_id == "  variant-A  "
        with pytest.raises(ValidationError):
            ExperimentVariant(
                variant_id=42,  # type: ignore[arg-type]
                strategy_config=v.strategy_config,
                dataset_ref=v.dataset_ref,
            )

    def test_extra_forbidden(self):
        v = _make_variant()
        with pytest.raises(ValidationError):
            ExperimentVariant(
                variant_id=v.variant_id,
                strategy_config=v.strategy_config,
                dataset_ref=v.dataset_ref,
                extra_field="nope",  # type: ignore
            )


class TestExperimentDefinition:
    def test_valid_definition_single_variant(self):
        d = _make_definition()
        assert d.schema_version == "experiment_definition.v1"
        assert len(d.variants) == 1
        assert len(d.planned_periods) == 1

    def test_valid_definition_two_variants(self):
        v1 = _make_variant(variant_id="var-a")
        v2 = _make_variant(variant_id="var-b")
        d = _make_definition(variants=(v1, v2))
        assert len(d.variants) == 2

    def test_schema_version_literal(self):
        d = _make_definition()
        assert d.schema_version == "experiment_definition.v1"

    def test_schema_version_cannot_be_reassigned(self):
        d = _make_definition()
        with pytest.raises(ValueError, match="frozen_instance"):
            d.schema_version = "experiment_definition.v2"

    def test_schema_version_rejects_other_versions(self):
        d = _make_definition()
        with pytest.raises(ValidationError):
            ExperimentDefinition(
                schema_version="experiment_definition.v2",  # type: ignore[arg-type]
                experiment_ref=d.experiment_ref,
                hypothesis=d.hypothesis,
                planned_periods=d.planned_periods,
                variants=d.variants,
            )

    def test_empty_hypothesis_rejected(self):
        with pytest.raises(ValidationError, match="non-empty"):
            _make_definition(hypothesis="")

    def test_whitespace_hypothesis_rejected(self):
        with pytest.raises(ValidationError, match="non-empty"):
            _make_definition(hypothesis="   ")

    def test_empty_planned_periods_rejected(self):
        with pytest.raises(ValidationError, match="planned_periods must contain"):
            _make_definition(periods=())

    def test_empty_variants_rejected(self):
        with pytest.raises(ValidationError, match="variants must contain"):
            _make_definition(variants=())

    def test_duplicate_variant_id_rejected(self):
        v1 = _make_variant(variant_id="dup")
        v2 = _make_variant(variant_id="dup")
        with pytest.raises(ValidationError, match="variant_id must be unique"):
            _make_definition(variants=(v1, v2))

    def test_same_strategy_ref_version_requires_identical_config(self):
        shared_ref = _make_strategy_ref(strategy_id="s1", strategy_version="v1")
        v1 = _make_variant(variant_id="var-a", strategy_ref=shared_ref, ema_period=20)
        v2 = _make_variant(variant_id="var-b", strategy_ref=shared_ref, ema_period=21)
        with pytest.raises(ValidationError, match="cannot reuse the same StrategyRef version"):
            _make_definition(variants=(v1, v2))

    def test_distinct_strategy_ref_versions_allow_distinct_configs(self):
        v1 = _make_variant(
            variant_id="var-a",
            strategy_ref=_make_strategy_ref(strategy_id="s1", strategy_version="v1"),
            ema_period=20,
        )
        v2 = _make_variant(
            variant_id="var-b",
            strategy_ref=_make_strategy_ref(strategy_id="s1", strategy_version="v2"),
            ema_period=21,
        )
        definition = _make_definition(variants=(v1, v2))
        assert len(definition.variants) == 2

    def test_frozen(self):
        d = _make_definition()
        with pytest.raises(ValueError, match="frozen_instance"):
            d.hypothesis = "changed"

    def test_extra_forbidden(self):
        d = _make_definition()
        with pytest.raises(ValidationError):
            ExperimentDefinition(
                experiment_ref=d.experiment_ref,
                hypothesis=d.hypothesis,
                planned_periods=d.planned_periods,
                variants=d.variants,
                extra_field="nope",  # type: ignore
            )

    def test_serialization_contains_fields(self):
        d = _make_definition()
        data = json.loads(d.model_dump_json())
        assert data["schema_version"] == "experiment_definition.v1"
        assert data["hypothesis"] == "H0: signal improves win-rate"
        assert len(data["variants"]) == 1

    def test_three_variants_valid(self):
        """Tres variantes es válido; la comparación posterior es otro tema."""
        variants = tuple(_make_variant(variant_id=f"var-{i}") for i in range(3))
        d = _make_definition(variants=variants)
        assert len(d.variants) == 3


# ═══════════════════════════════════════════════════════════════════════════════
#  Section 2: RunAttempt — estados, campos requeridos/prohibidos, transiciones y timestamps
# ═══════════════════════════════════════════════════════════════════════════════

class TestRunAttemptPending:
    def test_valid_pending(self):
        r = _make_run(status="pending")
        assert r.status == "pending"
        assert r.started_at_ms is None
        assert r.finished_at_ms is None
        assert r.result_id is None
        assert r.seed is None

    @pytest.mark.parametrize("field_value", [
        {"started_at_ms": 1}, {"finished_at_ms": 1}, {"dataset_ref_used": "ds-1"},
        {"code_ref": "code-1"}, {"engine_ref": "engine-1"},
        {"effective_periods": (_make_period(),)}, {"seed": 7},
        {"diagnostic": _make_diagnostic()}, {"partial_artifact_refs": ("art-1",)},
        {"result_id": "res-1"},
    ])
    def test_pending_rejects_execution_context_and_evidence(self, field_value):
        with pytest.raises(ValidationError):
            _make_run(status="pending", **field_value)


class TestRunAttemptRunning:
    def test_valid_running(self):
        r = _make_run(status="running", started_at_ms=100, dataset_ref_used="ds-1",
                      code_ref="code-1", engine_ref="engine-1",
                      effective_periods=(_make_period(),))
        assert r.status == "running"
        assert r.started_at_ms == 100

    @pytest.mark.parametrize("overrides", [
        {}, {"dataset_ref_used": "ds-1"},
        {"dataset_ref_used": "ds-1", "code_ref": "c1"},
        {"dataset_ref_used": "ds-1", "code_ref": "c1", "engine_ref": "e1"},
    ])
    def test_running_requires_complete_context_and_periods(self, overrides):
        with pytest.raises(ValidationError):
            _make_run(status="running", started_at_ms=10, **overrides)

    @pytest.mark.parametrize("extra", [
        {"finished_at_ms": 20}, {"diagnostic": _make_diagnostic()}, {"result_id": "res-1"},
    ])
    def test_running_rejects_terminal_fields(self, extra):
        with pytest.raises(ValidationError):
            _make_run(status="running", started_at_ms=10, dataset_ref_used="ds-1",
                      code_ref="c1", engine_ref="e1", effective_periods=(_make_period(),), **extra)

    def test_running_requires_start(self):
        with pytest.raises(ValidationError, match="running requires started_at_ms"):
            _make_run(status="running")


class TestRunAttemptCompleted:
    def test_valid_completed(self):
        r = _make_run(status="completed", started_at_ms=100, finished_at_ms=200,
                      dataset_ref_used="ds-1", code_ref="c1", engine_ref="e1",
                      effective_periods=(_make_period(),), result_id="res-001")
        assert r.status == "completed"
        assert r.finished_at_ms >= r.started_at_ms

    @pytest.mark.parametrize("overrides", [
        {"finished_at_ms": 200}, {"started_at_ms": 100},
        {"started_at_ms": 100, "finished_at_ms": 200},
    ])
    def test_completed_requires_timestamps_context_and_result(self, overrides):
        with pytest.raises(ValidationError):
            _make_run(status="completed", **overrides)

    def test_completed_rejects_diagnostic_and_partial_artifacts(self):
        base = dict(started_at_ms=100, finished_at_ms=200, dataset_ref_used="ds-1",
                    code_ref="c1", engine_ref="e1", effective_periods=(_make_period(),),
                    result_id="r1")
        for extra in ({"diagnostic": _make_diagnostic()}, {"partial_artifact_refs": ("a1",)}):
            with pytest.raises(ValidationError):
                _make_run(status="completed", **base, **extra)

    def test_finish_lt_start_rejected(self):
        with pytest.raises(ValidationError, match="finished_at_ms must be >= started_at_ms"):
            _make_run(status="completed", started_at_ms=200, finished_at_ms=100,
                      dataset_ref_used="ds-1", code_ref="c1", engine_ref="e1",
                      effective_periods=(_make_period(),), result_id="res-1")

    def test_finish_eq_start_valid(self):
        r = _make_run(status="completed", started_at_ms=100, finished_at_ms=100,
                      dataset_ref_used="ds-1", code_ref="c1", engine_ref="e1",
                      effective_periods=(_make_period(),), result_id="res-1")
        assert r.finished_at_ms == r.started_at_ms


class TestRunAttemptFailed:
    def test_valid_failed(self):
        r = _make_run(status="failed", started_at_ms=100, finished_at_ms=200,
                      dataset_ref_used="ds-1", code_ref="c1", engine_ref="e1",
                      effective_periods=(_make_period(),), diagnostic=_make_diagnostic(),
                      partial_artifact_refs=("partial-1",))
        assert r.status == "failed"

    def test_failed_requires_diagnostic(self):
        with pytest.raises(ValidationError, match="failed requires diagnostic"):
            _make_run(status="failed", started_at_ms=100, finished_at_ms=200,
                      dataset_ref_used="ds-1", code_ref="c1", engine_ref="e1",
                      effective_periods=(_make_period(),))

    def test_failed_rejects_result_id(self):
        with pytest.raises(ValidationError, match="failed must not have result_id"):
            _make_run(status="failed", started_at_ms=100, finished_at_ms=200,
                      dataset_ref_used="ds-1", code_ref="c1", engine_ref="e1",
                      effective_periods=(_make_period(),), diagnostic=_make_diagnostic(),
                      result_id="res-1")

    def test_failed_partial_artifact_refs_must_be_unique(self):
        with pytest.raises(ValidationError, match="partial_artifact_refs must not contain duplicates"):
            _make_run(status="failed", started_at_ms=100, finished_at_ms=200,
                      dataset_ref_used="ds-1", code_ref="c1", engine_ref="e1",
                      effective_periods=(_make_period(),), diagnostic=_make_diagnostic(),
                      partial_artifact_refs=("a1", "a1"))

    def test_failed_requires_complete_context(self):
        with pytest.raises(ValidationError, match="failed requires complete effective context"):
            _make_run(status="failed", started_at_ms=100, finished_at_ms=200,
                      diagnostic=_make_diagnostic())


class TestRunAttemptCancelled:
    def test_cancelled_with_start(self):
        r = _make_run(status="cancelled", started_at_ms=100, finished_at_ms=200,
                      dataset_ref_used="ds-1", code_ref="c1", engine_ref="e1",
                      effective_periods=(_make_period(),), diagnostic=_make_diagnostic(),
                      partial_artifact_refs=("partial-1",))
        assert r.status == "cancelled"

    def test_cancelled_without_start(self):
        exp_ref = _make_exp_ref()
        r = RunAttempt(
            run_id="run-cancel-002",
            experiment_ref=exp_ref,
            variant_id="var-a",
            status="cancelled",
            seed=None,
            finished_at_ms=200,
        )
        assert r.status == "cancelled"

    def test_cancelled_requires_finish(self):
        with pytest.raises(ValidationError, match="cancelled requires finished_at_ms"):
            _make_run(status="cancelled")

    def test_cancelled_without_start_rejects_seed(self):
        """seed is rejected only when cancelled without start."""
        with pytest.raises(ValidationError, match="cancelled without start"):
            _make_run(status="cancelled", finished_at_ms=200, seed=42)

    def test_cancelled_without_start_rejects_data_refs(self):
        with pytest.raises(ValidationError, match="cancelled without start"):
            _make_run(status="cancelled", finished_at_ms=200, dataset_ref_used="ds-1")

    def test_cancelled_with_start_requires_context(self):
        with pytest.raises(ValidationError, match="cancelled with start requires context"):
            _make_run(status="cancelled", started_at_ms=100, finished_at_ms=200)

    def test_cancelled_rejects_result_id(self):
        with pytest.raises(ValidationError, match="cancelled must not have result_id"):
            _make_run(status="cancelled", started_at_ms=100, finished_at_ms=200,
                      dataset_ref_used="ds-1", code_ref="c1", engine_ref="e1",
                      effective_periods=(_make_period(),), result_id="res-1")


class TestRunAttemptTransitions:
    def test_pending_to_running_valid(self):
        assert validate_transition("pending", "running") is True

    def test_pending_to_cancelled_valid(self):
        assert validate_transition("pending", "cancelled") is True

    def test_pending_to_completed_invalid(self):
        assert validate_transition("pending", "completed") is False

    def test_running_to_completed_valid(self):
        assert validate_transition("running", "completed") is True

    def test_running_to_failed_valid(self):
        assert validate_transition("running", "failed") is True

    def test_running_to_cancelled_valid(self):
        assert validate_transition("running", "cancelled") is True

    def test_completed_is_terminal(self):
        assert is_terminal("completed") is True
        assert validate_transition("completed", "running") is False

    def test_failed_is_terminal(self):
        assert is_terminal("failed") is True
        assert validate_transition("failed", "running") is False

    def test_cancelled_not_in_transitions(self):
        assert is_terminal("cancelled") is True
        assert validate_transition("cancelled", "running") is False

    def test_unknown_state_returns_false(self):
        assert validate_transition("unknown", "running") is False


class TestRunAttemptTimestamps:
    def test_timestamps_and_seed_are_strict_ints(self):
        r = _make_run(status="running", started_at_ms=100, dataset_ref_used="ds-1",
                      code_ref="c1", engine_ref="e1", effective_periods=(_make_period(),),
                      seed=42)
        assert r.started_at_ms == 100 and r.seed == 42

    @pytest.mark.parametrize("field", ("started_at_ms", "finished_at_ms", "seed"))
    def test_bool_rejected_for_integer_fields(self, field):
        values = dict(status="pending", finished_at_ms=None)
        values[field] = True
        with pytest.raises(ValidationError):
            _make_run(**values)

    def test_negative_timestamp_rejected(self):
        with pytest.raises(ValidationError):
            _make_run(status="cancelled", finished_at_ms=-1)

    def test_seed_is_optional_value_but_required_field(self):
        """No seed is represented explicitly as None; the schema never fills it in."""
        with pytest.raises(ValidationError):
            RunAttempt(run_id="r", experiment_ref=_make_exp_ref(), variant_id="v",
                       status="pending")
        r = RunAttempt(run_id="r", experiment_ref=_make_exp_ref(), variant_id="v",
                       status="pending", seed=None)
        assert r.seed is None


class TestRunAttemptSchemaAndRefs:
    def test_schema_version_literal(self):
        r = _make_run(status="pending")
        assert r.schema_version == "experiment_run.v1"

    def test_schema_version_cannot_be_reassigned(self):
        r = _make_run(status="pending")
        with pytest.raises(ValueError, match="frozen_instance"):
            r.schema_version = "experiment_run.v2"

    def test_schema_version_is_fixed(self):
        with pytest.raises(ValidationError):
            _make_run(status="pending", schema_version="experiment_run.v2")

    def test_run_id_non_empty(self):
        with pytest.raises(ValidationError, match="non-empty"):
            RunAttempt(
                run_id="", experiment_ref=_make_exp_ref(), variant_id="var-a", status="pending",
            )

    def test_variant_id_non_empty(self):
        with pytest.raises(ValidationError, match="non-empty"):
            RunAttempt(
                run_id="r1", experiment_ref=_make_exp_ref(), variant_id="", status="pending",
            )

    def test_experiment_ref_reused(self):
        exp_ref = _make_exp_ref()
        r = _make_run(experiment_ref=exp_ref)
        assert r.experiment_ref.experiment_id == "exp-001"

    def test_approved_field_names_reject_legacy_aliases(self):
        with pytest.raises(ValidationError):
            RunAttempt(
                run_id="r1", experiment_ref=_make_exp_ref(), variant_id="v1",
                status="pending", seed=None, start_ms=1, finish_ms=2,
                effective_data_refs=("ds-1",), effective_code_refs=("c1",),
                effective_engine_refs=("e1",),
            )  # type: ignore[call-arg]

    def test_run_refs_must_be_non_empty_and_strict(self):
        with pytest.raises(ValidationError):
            _make_run(status="running", started_at_ms=1, dataset_ref_used=" ",
                      code_ref="c1", engine_ref="e1", effective_periods=(_make_period(),))
        with pytest.raises(ValidationError):
            _make_run(status="running", started_at_ms=1, dataset_ref_used="ds-1",
                      code_ref=2, engine_ref="e1", effective_periods=(_make_period(),))

    def test_result_id_is_strict_non_empty(self):
        with pytest.raises(ValidationError):
            _make_run(status="completed", started_at_ms=1, finished_at_ms=2,
                      dataset_ref_used="ds-1", code_ref="c1", engine_ref="e1",
                      effective_periods=(_make_period(),), result_id="  ")


# ═══════════════════════════════════════════════════════════════════════════════
#  Section 4: Inmutabilidad/extra-forbid y relaciones puras
# ═══════════════════════════════════════════════════════════════════════════════

class TestDeepImmutability:
    """Todos los modelos congelados; tuplas inmutables."""

    def test_experiment_ref_frozen(self):
        ref = _make_exp_ref()
        with pytest.raises(ValueError, match="frozen_instance"):
            ref.experiment_version = "v2"

    def test_period_window_frozen(self):
        p = _make_period()
        with pytest.raises(ValueError, match="frozen_instance"):
            p.start_ms = 9999

    def test_experiment_variant_frozen(self):
        v = _make_variant()
        with pytest.raises(ValueError, match="frozen_instance"):
            v.variant_id = "changed"

    def test_experiment_definition_frozen(self):
        d = _make_definition()
        with pytest.raises(ValueError, match="frozen_instance"):
            d.experiment_ref = _make_exp_ref(experiment_id="x")

    def test_run_attempt_frozen(self):
        r = _make_run(status="pending")
        with pytest.raises(ValueError, match="frozen_instance"):
            r.run_id = "changed"

    def test_experiment_result_frozen(self):
        r = _make_result()
        with pytest.raises(ValueError, match="frozen_instance"):
            r.result_id = "changed"

    def test_public_diagnostic_frozen(self):
        d = _make_diagnostic()
        with pytest.raises(ValueError, match="frozen_instance"):
            d.public_message = "changed"

    def test_experiment_ref_extra_forbidden(self):
        with pytest.raises(ValidationError):
            ExperimentRef(experiment_id="e1", experiment_version="v1", extra_field="x")  # type: ignore

    def test_period_window_extra_forbidden(self):
        with pytest.raises(ValidationError):
            PeriodWindow(purpose="design", start_ms=1, end_ms=2, extra_field="x")  # type: ignore

    def test_experiment_variant_extra_forbidden(self):
        v = _make_variant()
        with pytest.raises(ValidationError):
            ExperimentVariant(
                variant_id=v.variant_id,
                strategy_config=v.strategy_config,
                dataset_ref=v.dataset_ref,
                extra_field="x",  # type: ignore
            )

    def test_run_attempt_extra_forbidden(self):
        with pytest.raises(ValidationError):
            RunAttempt(
                run_id="r1", experiment_ref=_make_exp_ref(), variant_id="v1",
                status="pending", extra_field="x",  # type: ignore
            )

    def test_experiment_result_extra_forbidden(self):
        with pytest.raises(ValidationError):
            ExperimentResult(result_id="r1", run_id="r2", artifact_refs=("a1",), extra_field="x")  # type: ignore

    def test_public_diagnostic_extra_forbidden(self):
        with pytest.raises(ValidationError):
            PublicDiagnostic(error_code="E1", public_message="m", extra_field="x")  # type: ignore


class TestTuplesAreImmutable:
    """Las tuplas no permiten mutación."""

    def test_experiment_definition_variants_tuple(self):
        d = _make_definition()
        with pytest.raises(Exception):
            d.variants[0] = _make_variant(variant_id="x")  # type: ignore

    def test_experiment_definition_periods_tuple(self):
        d = _make_definition()
        with pytest.raises(Exception):
            d.planned_periods[0] = _make_period(end_ms=9999)  # type: ignore

    def test_run_attempt_partial_artifact_refs_tuple(self):
        r = _make_run(
            status="failed", started_at_ms=100, finished_at_ms=110,
            dataset_ref_used="ds-1", code_ref="c1", engine_ref="e1",
            effective_periods=(_make_period(),), diagnostic=_make_diagnostic(),
            partial_artifact_refs=("a1",),
        )
        with pytest.raises(Exception):
            r.partial_artifact_refs[0] = "x"  # type: ignore

    def test_experiment_result_artifact_refs_tuple(self):
        r = _make_result()
        with pytest.raises(Exception):
            r.artifact_refs[0] = "x"  # type: ignore


class TestStrategyRefReuse:
    """StrategyRef se reutiliza sin duplicación ni alias."""

    def test_strategy_ref_preserves_fields(self):
        v = _make_variant()
        ref = v.strategy_config.strategy_ref
        assert ref.strategy_id == "test_strat"
        assert ref.strategy_version == "v2"
        assert ref.decision_id is None
        assert ref.schema_version == "paper_strategy_ref.v1"

    def test_strategy_ref_frozen_in_variant(self):
        v = _make_variant()
        with pytest.raises(ValueError, match="frozen_instance"):
            v.strategy_config.strategy_ref.strategy_id = "changed"

    def test_strategy_version_field_preserved(self):
        v = _make_variant()
        ref = v.strategy_config.strategy_ref
        assert hasattr(ref, "strategy_version")
        assert ref.strategy_version == "v2"

    def test_strategy_config_is_deeply_immutable_inside_variant(self):
        v = _make_variant()
        with pytest.raises(ValueError, match="frozen_instance"):
            v.strategy_config.indicator_instances[0].parameters[0].value = 99


class TestDatasetRefReuse:
    """DatasetRef se reutiliza como string opaco."""

    def test_dataset_ref_opaque(self):
        v = _make_variant()
        assert isinstance(v.dataset_ref, str)
        assert v.dataset_ref.strip()  # non-empty after strip

    def test_dataset_ref_preserves_literal(self):
        v = _make_variant(dataset_ref="  opaque-ref  ")
        assert v.dataset_ref == "  opaque-ref  "


class TestPureRelationships:
    """Relaciones puras entre DTOs — sin persistencia."""

    def test_experiment_definition_resolves_variants(self):
        d = _make_definition()
        for v in d.variants:
            assert isinstance(v.strategy_config, StrategyConfig)
            assert isinstance(v.dataset_ref, str)

    def test_run_attempt_resolves_experiment_ref(self):
        exp_ref = _make_exp_ref()
        r = _make_run(experiment_ref=exp_ref)
        assert r.experiment_ref.experiment_id == exp_ref.experiment_id

    def test_result_links_to_run_by_ids(self):
        run_id = "run-link-test"
        result_id = "res-link-test"
        completed_run = _make_run(
            run_id=run_id, status="completed", started_at_ms=100, finished_at_ms=200,
            dataset_ref_used="ds-1", code_ref="code-1", engine_ref="engine-1",
            effective_periods=(_make_period(),), result_id=result_id,
        )
        result = ExperimentResult(
            result_id=result_id,
            run_id=run_id,
            artifact_refs=("art-1",),
        )
        assert result_matches_completed_run(completed_run, result)

    def test_result_link_rejects_non_completed_or_mismatched_ids(self):
        run = _make_run(status="pending")
        result = ExperimentResult(result_id="res-1", run_id="run-001", artifact_refs=("a1",))
        assert not result_matches_completed_run(run, result)

    def test_run_links_exact_definition_variant_and_dataset(self):
        variant = _make_variant(variant_id="v-a", dataset_ref=" opaque dataset ")
        definition = _make_definition(variants=(variant,))
        run = _make_run(
            status="running", experiment_ref=definition.experiment_ref, variant_id="v-a",
            started_at_ms=1, dataset_ref_used=" opaque dataset ", code_ref="code",
            engine_ref="engine", effective_periods=(_make_period(),),
        )
        assert run_matches_definition(run, definition)
        resolved = resolve_run_variant(run, definition)
        assert resolved is not None
        assert resolved.strategy_config == variant.strategy_config
        assert resolved.dataset_ref == run.dataset_ref_used
        assert not run_matches_definition(
            _make_run(
                status="running", experiment_ref=definition.experiment_ref, variant_id="v-a",
                started_at_ms=1, dataset_ref_used="other", code_ref="code", engine_ref="engine",
                effective_periods=(_make_period(),),
            ), definition,
        )
    def test_result_link_checks_both_ids_and_status(self):
        completed_run = RunAttempt(
            run_id="run-link-test",
            experiment_ref=_make_exp_ref(),
            variant_id="var-a",
            status="completed",
            seed=None,
            started_at_ms=100,
            finished_at_ms=200,
            dataset_ref_used="ds-1",
            code_ref="c1",
            engine_ref="e1",
            effective_periods=(_make_period(),),
            result_id="res-link-test",
        )
        assert not result_matches_completed_run(
            completed_run,
            ExperimentResult(result_id="different", run_id="run-link-test", artifact_refs=("a1",)),
        )

    def test_no_metrics_in_result(self):
        """ExperimentResult no debe contener métricas."""
        r = _make_result()
        data = r.model_dump()
        metric_keys = [k for k in data if "metric" in k.lower() or "score" in k.lower()]
        assert metric_keys == [], f"Unexpected metric fields: {metric_keys}"

    def test_no_cost_in_diagnostic(self):
        """PublicDiagnostic no contiene costos ni políticas."""
        d = _make_diagnostic()
        data = d.model_dump()
        cost_keys = [k for k in data if "cost" in k.lower() or "risk" in k.lower()]
        assert cost_keys == [], f"Unexpected fields: {cost_keys}"


class TestInvariantesValidations:
    """Verificaciones de invariantes que no requieren persistencia."""

    def test_pending_has_no_context(self):
        r = _make_run(status="pending")
        assert r.started_at_ms is None
        assert r.finished_at_ms is None
        assert r.result_id is None
        assert r.dataset_ref_used is None

    def test_running_has_effective_context(self):
        r = _make_run(
            status="running", started_at_ms=100, dataset_ref_used="d1",
            code_ref="c1", engine_ref="e1",
            effective_periods=(_make_period(),),
        )
        assert r.started_at_ms is not None
        assert r.dataset_ref_used == "d1"

    def test_completed_has_result(self):
        r = _make_run(status="completed", started_at_ms=100, finished_at_ms=200,
                      dataset_ref_used="d1", code_ref="c1", engine_ref="e1",
                      effective_periods=(_make_period(),), result_id="res-1")
        assert r.result_id == "res-1"
        assert r.diagnostic is None

    def test_failed_has_diagnostic(self):
        diag = _make_diagnostic()
        r = _make_run(status="failed", started_at_ms=100, finished_at_ms=200,
                      dataset_ref_used="d1", code_ref="c1", engine_ref="e1",
                      effective_periods=(_make_period(),), diagnostic=diag)
        assert r.diagnostic is not None
        assert r.diagnostic.error_code == "ERR_TIMEOUT"

    def test_cancelled_without_start_has_no_context(self):
        r = _make_run(status="cancelled", finished_at_ms=200)
        assert r.started_at_ms is None
        assert r.dataset_ref_used is None
        assert r.seed is None


class TestRejectionLog:
    """Registros de valores que deben ser rechazados — no persistidos ni presentados."""

    def test_empty_string_not_persisted_as_id(self):
        """Los strings vacíos se rechazan en validación, no se persisten."""
        with pytest.raises(ValidationError):
            ExperimentRef(experiment_id="", experiment_version="v1")

    def test_blank_dataset_ref_rejected(self):
        class _Holder(BaseModel):
            ref: DatasetRef
        with pytest.raises(ValidationError):
            _Holder(ref="   ")

    def test_non_string_not_coerced(self):
        """Los tipos no-string se rechazan sin coerción."""
        with pytest.raises(ValidationError):
            ExperimentRef(experiment_id=42, experiment_version="v1")  # type: ignore


class TestTransitionHelper:
    """El helper validate_transition es un puro function — sin estado."""

    def test_all_pending_targets(self):
        for target in VALID_TRANSITIONS["pending"]:
            assert validate_transition("pending", target) is True

    def test_all_running_targets(self):
        for target in VALID_TRANSITIONS["running"]:
            assert validate_transition("running", target) is True

    def test_no_self_loop_pending(self):
        assert validate_transition("pending", "pending") is False

    def test_no_self_loop_running(self):
        assert validate_transition("running", "running") is False

    def test_cross_state_invalid(self):
        assert validate_transition("pending", "failed") is False
        assert validate_transition("pending", "completed") is False
        assert validate_transition("running", "pending") is False

    def test_terminal_states_have_no_transitions(self):
        for state in TERMINAL_STATES:
            assert state not in VALID_TRANSITIONS



# ═══════════════════════════════════════════════════════════════════════════════
#  Section 3: ExperimentResult, diagnósticos, artifact refs y registros de prueba
# ═══════════════════════════════════════════════════════════════════════════════

class TestPublicDiagnostic:
    def test_valid_diagnostic(self):
        d = _make_diagnostic()
        assert d.error_code == "ERR_TIMEOUT"
        assert d.public_message == "Run exceeded time budget"

    def test_frozen(self):
        d = _make_diagnostic()
        with pytest.raises(ValueError, match="frozen_instance"):
            d.error_code = "changed"

    def test_empty_error_code_rejected(self):
        with pytest.raises(ValidationError, match="non-empty"):
            PublicDiagnostic(error_code="", public_message="msg")

    def test_whitespace_error_code_rejected(self):
        with pytest.raises(ValidationError, match="non-empty"):
            PublicDiagnostic(error_code="   ", public_message="msg")

    def test_empty_public_message_rejected(self):
        with pytest.raises(ValidationError, match="non-empty"):
            PublicDiagnostic(error_code="ERR_1", public_message="")

    def test_non_string_error_code_rejected(self):
        with pytest.raises(ValidationError):
            PublicDiagnostic(error_code=42, public_message="msg")  # type: ignore

    def test_public_message_is_strict_non_empty_text(self):
        for message in ("", " \t "):
            with pytest.raises(ValidationError):
                PublicDiagnostic(error_code="E1", public_message=message)
        with pytest.raises(ValidationError):
            PublicDiagnostic(error_code="E1", public_message=42)  # type: ignore[arg-type]

    def test_extra_forbidden(self):
        d = _make_diagnostic()
        with pytest.raises(ValidationError):
            PublicDiagnostic(
                error_code=d.error_code,
                public_message=d.public_message,
                extra_field="nope",  # type: ignore
            )


class TestExperimentResult:
    def test_valid_result(self):
        r = _make_result()
        assert r.schema_version == "experiment_result.v1"
        assert r.result_id == "res-001"
        assert r.run_id == "run-001"

    def test_schema_version_literal(self):
        r = _make_result()
        assert r.schema_version == "experiment_result.v1"

    def test_schema_version_cannot_be_reassigned(self):
        r = _make_result()
        with pytest.raises(ValueError, match="frozen_instance"):
            r.schema_version = "experiment_result.v2"

    def test_schema_version_is_fixed(self):
        with pytest.raises(ValidationError):
            ExperimentResult(
                schema_version="experiment_result.v2", result_id="r1", run_id="run1",
                artifact_refs=("a1",),
            )  # type: ignore[arg-type]

    def test_frozen(self):
        r = _make_result()
        with pytest.raises(ValueError, match="frozen_instance"):
            r.result_id = "changed"

    def test_empty_result_id_rejected(self):
        with pytest.raises(ValidationError, match="non-empty"):
            ExperimentResult(result_id="", run_id="r1", artifact_refs=("a1",))

    def test_empty_run_id_rejected(self):
        with pytest.raises(ValidationError, match="non-empty"):
            ExperimentResult(result_id="r1", run_id="", artifact_refs=("a1",))

    def test_empty_artifact_refs_rejected(self):
        with pytest.raises(ValidationError, match="artifact_refs must contain"):
            ExperimentResult(result_id="r1", run_id="r2", artifact_refs=())

    def test_duplicate_artifact_refs_rejected(self):
        with pytest.raises(ValidationError, match="artifact_refs must not contain duplicates"):
            ExperimentResult(result_id="r1", run_id="r2", artifact_refs=("a1", "a1"))

    def test_non_empty_artifact_refs_validated(self):
        """Cada artifact_ref debe ser string no vacío."""
        with pytest.raises(ValidationError, match="non-empty"):
            ExperimentResult(result_id="r1", run_id="r2", artifact_refs=("",))

    def test_strict_non_empty_rejects_int(self):
        with pytest.raises(ValidationError):
            ExperimentResult(result_id="r1", run_id="r2", artifact_refs=(1,))  # type: ignore

    def test_artifact_refs_tuple(self):
        r = _make_result()
        assert isinstance(r.artifact_refs, tuple)

    def test_multiple_unique_artifacts_valid(self):
        r = ExperimentResult(
            result_id="res-multi",
            run_id="run-001",
            artifact_refs=("art-a", "art-b", "art-c"),
        )
        assert len(r.artifact_refs) == 3

    def test_artifact_refs_preserve_literal_and_reject_whitespace(self):
        result = ExperimentResult(result_id=" r1 ", run_id=" run1 ", artifact_refs=(" a1 ",))
        assert result.result_id == " r1 " and result.artifact_refs == (" a1 ",)
        with pytest.raises(ValidationError):
            ExperimentResult(result_id="r1", run_id="run1", artifact_refs=("  ",))

    def test_extra_forbidden(self):
        r = _make_result()
        with pytest.raises(ValidationError):
            ExperimentResult(
                result_id=r.result_id,
                run_id=r.run_id,
                artifact_refs=r.artifact_refs,
                extra_field="nope",  # type: ignore
            )

    def test_serialization_contains_fields(self):
        r = _make_result()
        data = json.loads(r.model_dump_json())
        assert data["schema_version"] == "experiment_result.v1"
        assert data["result_id"] == "res-001"
        assert data["run_id"] == "run-001"

    def test_result_links_to_completed_run(self):
        """result_id debe coincidir con un run completed."""
        exp_ref = _make_exp_ref()
        completed_run = RunAttempt(
            run_id="run-comp-link",
            experiment_ref=exp_ref,
            variant_id="var-a",
            status="completed",
            started_at_ms=100,
            finished_at_ms=200,
            seed=None,
            dataset_ref_used="ds-1",
            code_ref="c1",
            engine_ref="e1",
            effective_periods=(_make_period(),),
            result_id="res-linked",
        )
        result = ExperimentResult(
            result_id="res-linked",
            run_id="run-comp-link",
            artifact_refs=("art-1",),
        )
        assert result.result_id == completed_run.result_id
        assert result.run_id == completed_run.run_id
        assert result_matches_completed_run(completed_run, result)


# ═══════════════════════════════════════════════════════════════════════════════
#  Section 5: Disposición contract-only — VariantDiscardRecord / ResultExclusionRecord
# ═══════════════════════════════════════════════════════════════════════════════


class TestVariantDiscardRecord:
    """VariantDiscardRecord: frozen, extra-forbid, literal v1 fijo."""

    def _make_discard(self, **overrides: Any) -> Any:
        from src.models.experiment_contracts import VariantDiscardRecord, ExperimentRef
        base = dict(
            experiment_ref=ExperimentRef(experiment_id="exp-d", experiment_version="v1"),
            variant_id="var-x",
            reason="noisy signal",
            recorded_at_ms=5000,
        )
        base.update(overrides)
        return VariantDiscardRecord(**base)

    def test_schema_v1_fijo(self):
        r = self._make_discard()
        assert r.schema_version == "variant_discard_record.v1"
        # inmutable: no se puede cambiar
        with pytest.raises(ValueError, match="frozen_instance"):
            r.schema_version = "v2"
        with pytest.raises(ValidationError):
            self._make_discard(schema_version="variant_discard_record.v2")

    def test_decision_fija(self):
        r = self._make_discard()
        assert r.decision == "discarded_before_run"
        with pytest.raises(ValidationError):
            self._make_discard(decision="discarded_after_run")

    def test_conserva_experiment_ref(self):
        exp_ref = ExperimentRef(experiment_id="exp-ref", experiment_version="v3")
        r = self._make_discard(experiment_ref=exp_ref)
        assert r.experiment_ref.experiment_id == "exp-ref"

    def test_conserva_variant_id(self):
        r = self._make_discard(variant_id="var-keep")
        assert r.variant_id == "var-keep"

    def test_record_does_not_mutate_definition(self):
        definition = _make_definition()
        ref_before = definition.experiment_ref
        variant_before = definition.variants[0]
        record = self._make_discard(
            experiment_ref=definition.experiment_ref,
            variant_id=definition.variants[0].variant_id,
        )
        assert record.experiment_ref is ref_before
        assert definition.experiment_ref is ref_before
        assert definition.variants[0] is variant_before

    def test_reason_no_empty(self):
        with pytest.raises(ValidationError, match="non-empty"):
            self._make_discard(reason="")

    def test_reason_no_whitespace_only(self):
        with pytest.raises(ValidationError, match="non-empty"):
            self._make_discard(reason="   ")

    def test_reason_must_be_strict_string(self):
        with pytest.raises(ValidationError):
            self._make_discard(reason=123)

    def test_recorded_at_ms_no_negativo(self):
        r = self._make_discard(recorded_at_ms=0)
        assert r.recorded_at_ms == 0
        with pytest.raises(ValidationError, match="greater than or equal to 0"):
            self._make_discard(recorded_at_ms=-1)

    def test_recorded_at_ms_strict_int(self):
        r = self._make_discard(recorded_at_ms=9999)
        assert isinstance(r.recorded_at_ms, int)
        # bool no es aceptado como StrictInt
        with pytest.raises(ValidationError):
            self._make_discard(recorded_at_ms=True)

    def test_extra_forbid(self):
        with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
            self._make_discard(extra_field="boom")

    def test_frozen(self):
        r = self._make_discard()
        with pytest.raises(ValueError, match="frozen_instance"):
            r.reason = "changed"


class TestResultExclusionRecord:
    """ResultExclusionRecord: frozen, extra-forbid, literal v1 fijo."""

    def _make_exclusion(self, **overrides: Any) -> Any:
        from src.models.experiment_contracts import ResultExclusionRecord
        base = dict(
            result_id="res-excl",
            reason="data contamination detected",
            recorded_at_ms=7000,
        )
        base.update(overrides)
        return ResultExclusionRecord(**base)

    def test_schema_v1_fijo(self):
        r = self._make_exclusion()
        assert r.schema_version == "result_exclusion_record.v1"
        with pytest.raises(ValueError, match="frozen_instance"):
            r.schema_version = "v2"
        with pytest.raises(ValidationError):
            self._make_exclusion(schema_version="result_exclusion_record.v2")

    def test_decision_fija(self):
        r = self._make_exclusion()
        assert r.decision == "excluded"
        with pytest.raises(ValidationError):
            self._make_exclusion(decision="included")

    def test_conserva_result_id(self):
        r = self._make_exclusion(result_id="res-keep")
        assert r.result_id == "res-keep"

    def test_record_does_not_mutate_result(self):
        result = _make_result(result_id="res-immutable")
        before = result.model_dump()
        record = self._make_exclusion(result_id=result.result_id)
        assert record.result_id == result.result_id
        assert result.model_dump() == before

    def test_reason_no_empty(self):
        with pytest.raises(ValidationError, match="non-empty"):
            self._make_exclusion(reason="")

    def test_reason_no_whitespace_only(self):
        with pytest.raises(ValidationError, match="non-empty"):
            self._make_exclusion(reason="\t\n  ")

    def test_reason_must_be_strict_string(self):
        with pytest.raises(ValidationError):
            self._make_exclusion(reason=42)

    def test_recorded_at_ms_no_negativo(self):
        r = self._make_exclusion(recorded_at_ms=0)
        assert r.recorded_at_ms == 0
        with pytest.raises(ValidationError, match="greater than or equal to 0"):
            self._make_exclusion(recorded_at_ms=-100)

    def test_recorded_at_ms_strict_int(self):
        r = self._make_exclusion(recorded_at_ms=8888)
        assert isinstance(r.recorded_at_ms, int)
        with pytest.raises(ValidationError):
            self._make_exclusion(recorded_at_ms=False)

    def test_extra_forbid(self):
        with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
            self._make_exclusion(unexpected=1)

    def test_frozen(self):
        r = self._make_exclusion()
        with pytest.raises(ValueError, match="frozen_instance"):
            r.reason = "new reason"


# ═══════════════════════════════════════════════════════════════════════════════
