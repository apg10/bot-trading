"""Immutable, normalized experiment-result and comparison contracts.

This module deliberately contains only DTOs and relationship validation.  It
does not persist, replay, calculate metrics, or select a comparison winner.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Annotated, Literal

from pydantic import (
    AliasChoices,
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    StrictInt,
    StrictStr,
    field_validator,
    model_validator,
)

from src.models.dataset_ref import DatasetRef
from src.models.experiment_contracts import ExperimentRef, PeriodWindow
from src.models.strategy_config import StrategyConfig


def _non_empty(value: str) -> str:
    """Validate without normalizing: references are opaque and literal."""
    if not value.strip():
        raise ValueError("field must be a non-empty string")
    return value


NonEmpty = Annotated[StrictStr, AfterValidator(_non_empty)]


class NormalizedMetric(BaseModel):
    """One metric with an explicit availability state.

    An unavailable metric has no value at all.  In particular, zero is a
    legitimate available value but is never used as a sentinel for missing
    data.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    metric_id: NonEmpty
    unit: NonEmpty | None = None
    status: Literal["available", "undefined", "insufficient_data"]
    value: Decimal | None = None
    reason: NonEmpty | None = None

    @field_validator("value")
    @classmethod
    def _finite_value(cls, value: Decimal | None) -> Decimal | None:
        if value is not None and not value.is_finite():
            raise ValueError("metric value must be finite")
        return value

    @model_validator(mode="after")
    def _validate_state(self) -> "NormalizedMetric":
        if self.status == "available":
            if self.value is None or self.unit is None:
                raise ValueError("available metric requires a finite value and unit")
        else:
            if self.reason is None:
                raise ValueError(f"{self.status} metric requires a reason")
            if self.value is not None:
                raise ValueError("unavailable metric must not contain a value")
        return self


class NormalizedExperimentResult(BaseModel):
    """A self-contained normalized result for one immutable experiment run."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["normalized_experiment_result.v1"] = Field(
        default="normalized_experiment_result.v1", frozen=True
    )
    result_id: NonEmpty
    run_id: NonEmpty
    origin_id: NonEmpty
    result_version: NonEmpty
    experiment_ref: ExperimentRef
    variant_id: NonEmpty
    dataset_ref: DatasetRef
    strategy_config: StrategyConfig
    code_ref: NonEmpty
    engine_ref: NonEmpty
    risk_ref: NonEmpty
    costs_ref: NonEmpty
    exposure_ref: NonEmpty
    effective_periods: tuple[PeriodWindow, ...]
    seed: StrictInt | None = None
    metrics: tuple[NormalizedMetric, ...]

    @model_validator(mode="after")
    def _validate_collections(self) -> "NormalizedExperimentResult":
        if not self.effective_periods:
            raise ValueError("effective_periods must contain at least one period")
        if not self.metrics:
            raise ValueError("metrics must contain at least one metric")
        metric_ids = tuple(metric.metric_id for metric in self.metrics)
        if len(metric_ids) != len(set(metric_ids)):
            raise ValueError("metrics must not contain duplicate metric_id")
        # Ordering is part of the wire contract, while all other values are
        # retained literally (including opaque references and metric strings).
        object.__setattr__(self, "metrics", tuple(sorted(self.metrics, key=lambda m: m.metric_id)))
        return self


class ExperimentComparison(BaseModel):
    """A/B/C comparison manifest without a winner or derived profitability."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["experiment_comparison.v1"] = Field(
        default="experiment_comparison.v1", frozen=True
    )
    comparison_id: NonEmpty
    comparison_version: NonEmpty
    hypothesis: NonEmpty
    normalized_results: tuple[NormalizedExperimentResult, ...] = Field(
        validation_alias=AliasChoices("normalized_results", "results")
    )

    @property
    def results(self) -> tuple[NormalizedExperimentResult, ...]:
        """Short read-only view for callers that call the manifest's entries results."""
        return self.normalized_results

    @model_validator(mode="after")
    def _validate_comparison(self) -> "ExperimentComparison":
        if len(self.normalized_results) < 3:
            raise ValueError("comparison requires at least three normalized results")

        variants = tuple(result.variant_id for result in self.normalized_results)
        if len(variants) != len(set(variants)):
            raise ValueError("comparison must not contain duplicate variant_id")

        first = self.normalized_results[0]
        metric_units = {
            metric.metric_id: metric.unit for metric in first.metrics
        }
        for result in self.normalized_results[1:]:
            if result.experiment_ref != first.experiment_ref:
                raise ValueError("comparison results must use the same experiment")
            if result.dataset_ref != first.dataset_ref:
                raise ValueError("comparison results must use the same dataset")
            if result.effective_periods != first.effective_periods:
                raise ValueError("comparison results must use the same periods")
            if result.risk_ref != first.risk_ref:
                raise ValueError("comparison results must use the same risk reference")
            if result.costs_ref != first.costs_ref:
                raise ValueError("comparison results must use the same costs reference")
            if result.exposure_ref != first.exposure_ref:
                raise ValueError("comparison results must use the same exposure reference")
            candidate_units = {
                metric.metric_id: metric.unit for metric in result.metrics
            }
            if candidate_units != metric_units:
                raise ValueError("comparison results must use the same metric IDs and units")
        return self


__all__ = [
    "ExperimentComparison",
    "NormalizedExperimentResult",
    "NormalizedMetric",
]
