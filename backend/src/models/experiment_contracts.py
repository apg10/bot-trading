"""Experiment DTOs — contract-only definitions.

Este m\u00f3dulo define los contratos de datos para experimentos, ejecuciones y
resultados.  No incluye persistencia, tablas, backtesting, m\u00e9tricas ni replay.

DTOs:
    - ExperimentRef: identificador inmutable de un experimento.
    - PeriodWindow: ventana temporal con prop\u00f3sito design/validation/test.
    - ExperimentVariant: variante con StrategyConfig y DatasetRef.
    - ExperimentDefinition: definici\u00f3n fija de un experimento.
    - PublicDiagnostic: diagn\u00f3stico cerrado, sin excepciones crudas.
    - RunAttempt: intento de ejecuci\u00f3n con ciclo de vida estricto.
    - ExperimentResult: resultado vinculado a run completado.

Invariantes de estado de RunAttempt:
    pending  -> sin inicio, fin ni resultado; sin contexto efectivo, seed ni evidencia.
    running  -> inicio, datos/code/engine refs y periodos efectivos no vac\u00edos; sin fin,
                diagn\u00f3stico ni resultado.
    completed-> inicio y fin con finish >= start, contexto efectivo completo y result_id;
                sin diagn\u00f3stico. Resultado validado/publicado.
    failed   -> requiere inicio/fin, contexto efectivo, diagnostic obligatorio y
                result_id ausente; evidencia parcial opcional.
    cancelled-> requiere fin; puede carecer de inicio si se cancela desde pending.
                Sin inicio no admite contexto, seed ni evidencia. Con inicio requiere
                contexto efectivo; diagn\u00f3stico/evidencia parcial opcionales.
                Sin resultado final.

Transiciones v\u00e1lidas:
    pending  -> running | cancelled
    running  -> completed | failed | cancelled
    terminales (completed, failed) sin transici\u00f3n.

Helper puro ``validate_transition`` disponible para verificar transiciones.
"""

from __future__ import annotations

from types import MappingProxyType
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    AfterValidator,
    ConfigDict,
    Field,
    StrictInt,
    StrictStr,
    model_validator,
)
from src.models.dataset_ref import DatasetRef
from src.models.strategy_config import StrategyConfig

# ── Reutilizaci\u00f3n de tipos existentes ────────────────────────────────────────

__all__ = [
    "ExperimentDefinition",
    "ExperimentRef",
    "ExperimentResult",
    "PeriodWindow",
    "PublicDiagnostic",
    "RunAttempt",
    "ResultExclusionRecord",
    "VariantDiscardRecord",
    "VALID_TRANSITIONS",
    "TERMINAL_STATES",
    "validate_transition",
    "is_terminal",
    "resolve_run_variant",
    "run_matches_definition",
    "result_matches_completed_run",
]

# ── Validadores de string estricto ──────────────────────────────────────────────


def _validate_non_empty_literal(value: str) -> str:
    """Return *value* unchanged; raise if blank (after stripping)."""
    if not value.strip():
        raise ValueError("field must be a non-empty string")
    return value


StrictNonEmpty = Annotated[
    StrictStr,
    AfterValidator(_validate_non_empty_literal),
]

# Opaque identifiers/references are validated for shape only. These aliases do
# not assert that a referenced object exists or represents a real artifact.
OpaqueRef = StrictNonEmpty
OpaqueId = StrictNonEmpty
ArtifactRef = StrictNonEmpty

# ── ExperimentRef ───────────────────────────────────────────────────────────────


class ExperimentRef(BaseModel):
    """Identificador inmutable de un experimento.

    ``experiment_id`` es estable y se preserva literalmente.
    ``experiment_version`` es opaco e inmutable tras la creaci\u00f3n.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    experiment_id: OpaqueId
    experiment_version: OpaqueRef


# ── PeriodWindow ────────────────────────────────────────────────────────────────


class PeriodWindow(BaseModel):
    """Ventana temporal con prop\u00f3sito design | validation | test.

    ``start_ms`` y ``end_ms`` son enteros estrictos UTC (no negativos).
    Inicio inclusivo, fin exclusivo: start_ms < end_ms.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    purpose: Literal["design", "validation", "test"]
    start_ms: StrictInt = Field(..., ge=0)
    end_ms: StrictInt = Field(..., gt=0)

    @model_validator(mode="after")
    def _validate_order(self) -> "PeriodWindow":
        if self.end_ms <= self.start_ms:
            raise ValueError("end_ms must be strictly greater than start_ms")
        return self


# ── ExperimentVariant ───────────────────────────────────────────────────────────


class ExperimentVariant(BaseModel):
    """Variante de experimento con StrategyConfig aceptado y DatasetRef aceptado."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    variant_id: StrictNonEmpty
    strategy_config: StrategyConfig
    dataset_ref: DatasetRef


# ── ExperimentDefinition ────────────────────────────────────────────────────────


class ExperimentDefinition(BaseModel):
    """Definici\u00f3n fija de un experimento.

    Schema: ``experiment_definition.v1``.
    hypothesis: texto estricto no vac\u00edo.
    planned_periods: lista no vac\u00eda de PeriodWindow.
    variants: una o m\u00e1s variantes con variant_id \u00fanico dentro de la versi\u00f3n.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["experiment_definition.v1"] = Field(
        default="experiment_definition.v1", frozen=True
    )
    experiment_ref: ExperimentRef
    hypothesis: StrictNonEmpty
    planned_periods: tuple[PeriodWindow, ...]
    variants: tuple[ExperimentVariant, ...]

    @model_validator(mode="after")
    def _validate_planned_periods_non_empty(self) -> "ExperimentDefinition":
        if not self.planned_periods:
            raise ValueError("planned_periods must contain at least one PeriodWindow")
        return self

    @model_validator(mode="after")
    def _validate_variants_non_empty(self) -> "ExperimentDefinition":
        if not self.variants:
            raise ValueError("variants must contain at least one ExperimentVariant")
        return self

    @model_validator(mode="after")
    def _validate_variant_ids_unique(self) -> "ExperimentDefinition":
        variant_ids = [v.variant_id for v in self.variants]
        if len(variant_ids) != len(set(variant_ids)):
            raise ValueError("variant_id must be unique within the experiment version")
        return self

    @model_validator(mode="after")
    def _validate_strategy_config_versions_immutable(self) -> "ExperimentDefinition":
        """A StrategyRef version cannot identify different configurations."""
        configs_by_strategy_version: dict[tuple[str, str], str] = {}
        for variant in self.variants:
            strategy_ref = variant.strategy_config.strategy_ref
            key = (strategy_ref.strategy_id, strategy_ref.strategy_version)
            config_json = variant.strategy_config.model_dump_json()
            existing = configs_by_strategy_version.get(key)
            if existing is not None and existing != config_json:
                raise ValueError(
                    "different StrategyConfig content cannot reuse the same StrategyRef version"
                )
            configs_by_strategy_version[key] = config_json
        return self


# ── PublicDiagnostic ────────────────────────────────────────────────────────────


class PublicDiagnostic(BaseModel):
    """Diagn\u00f3stico congelado y cerrado.

    ``error_code`` y ``public_message`` son strings estrictos no vac\u00edos.
    No captura ni guarda excepciones crudas.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    error_code: StrictNonEmpty
    public_message: StrictNonEmpty


# ── RunAttempt ──────────────────────────────────────────────────────────────────


class RunAttempt(BaseModel):
    """Intento de ejecuci\u00f3n con ciclo de vida estricto.

    Schema: ``experiment_run.v1``.
    Estados: pending | running | completed | failed | cancelled.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["experiment_run.v1"] = Field(
        default="experiment_run.v1", frozen=True
    )
    run_id: OpaqueId
    experiment_ref: ExperimentRef
    variant_id: StrictNonEmpty
    status: Literal["pending", "running", "completed", "failed", "cancelled"]

    # Campos efectivos (solo algunos presentes seg\u00fan estado)
    started_at_ms: StrictInt | None = Field(default=None, ge=0)
    finished_at_ms: StrictInt | None = Field(default=None, ge=0)
    seed: StrictInt | None

    # Contexto efectivo no vac\u00edo para running/completed/failed/cancelled(con inicio)
    dataset_ref_used: DatasetRef | None = None
    code_ref: OpaqueRef | None = None
    engine_ref: OpaqueRef | None = None
    effective_periods: tuple[PeriodWindow, ...] = ()

    # Diagn\u00f3stico y evidencia
    diagnostic: PublicDiagnostic | None = None
    partial_artifact_refs: tuple[ArtifactRef, ...] = ()

    # Resultado vinculado
    result_id: OpaqueId | None = None

    @model_validator(mode="after")
    def _validate_finish_gte_start(self) -> "RunAttempt":
        if self.started_at_ms is not None and self.finished_at_ms is not None:
            if self.finished_at_ms < self.started_at_ms:
                raise ValueError("finished_at_ms must be >= started_at_ms")
        if len(self.partial_artifact_refs) != len(set(self.partial_artifact_refs)):
            raise ValueError("partial_artifact_refs must not contain duplicates")
        return self

    @model_validator(mode="after")
    def _validate_pending_invariants(self) -> "RunAttempt":
        if self.status != "pending":
            return self
        if any((self.started_at_ms is not None, self.finished_at_ms is not None,
                self.dataset_ref_used is not None, self.code_ref is not None,
                self.engine_ref is not None, self.effective_periods, self.seed is not None,
                self.diagnostic is not None, self.partial_artifact_refs, self.result_id is not None)):
            raise ValueError("pending must not contain execution context or evidence")
        return self

    @model_validator(mode="after")
    def _validate_running_invariants(self) -> "RunAttempt":
        """running: inicio, datos/code/engine refs y periodos efectivos no vac\u00edos; sin fin, diagn\u00f3stico ni resultado."""
        if self.status != "running":
            return self
        if self.started_at_ms is None:
            raise ValueError("running requires started_at_ms")
        if not all((self.dataset_ref_used, self.code_ref, self.engine_ref)) or not self.effective_periods:
            raise ValueError("running requires start and complete effective context")
        if self.finished_at_ms is not None or self.diagnostic is not None or self.result_id is not None:
            raise ValueError("running must not have finish, diagnostic or result_id")
        return self

    @model_validator(mode="after")
    def _validate_completed_invariants(self) -> "RunAttempt":
        """completed: inicio y fin con finish >= start, contexto efectivo completo y result_id; sin diagn\u00f3stico."""
        if self.status != "completed":
            return self
        if self.started_at_ms is None or self.finished_at_ms is None:
            raise ValueError("completed requires start and finish timestamps")
        if not all((self.dataset_ref_used, self.code_ref, self.engine_ref)) or not self.effective_periods or self.result_id is None:
            raise ValueError("completed requires context, periods and result_id")
        if self.diagnostic is not None or self.partial_artifact_refs:
            raise ValueError("completed must not have diagnostic or partial artifacts")
        return self

    @model_validator(mode="after")
    def _validate_failed_invariants(self) -> "RunAttempt":
        """failed: requiere inicio/fin, contexto efectivo, diagnostic obligatorio y result_id ausente."""
        if self.status != "failed":
            return self
        if self.started_at_ms is None or self.finished_at_ms is None:
            raise ValueError("failed requires start and finish timestamps")
        if not all((self.dataset_ref_used, self.code_ref, self.engine_ref)) or not self.effective_periods:
            raise ValueError("failed requires complete effective context and periods")
        if self.diagnostic is None:
            raise ValueError("failed requires diagnostic")
        if self.result_id is not None:
            raise ValueError("failed must not have result_id")
        return self

    @model_validator(mode="after")
    def _validate_cancelled_invariants(self) -> "RunAttempt":
        """cancelled: requiere fin; puede carecer de inicio si se cancela desde pending.
        Sin inicio no admite contexto, seed ni evidencia. Con inicio requiere contexto efectivo."""
        if self.status != "cancelled":
            return self
        if self.finished_at_ms is None:
            raise ValueError("cancelled requires finished_at_ms")
        has_start = self.started_at_ms is not None
        has_context = all((self.dataset_ref_used, self.code_ref, self.engine_ref))
        has_any_context = any((self.dataset_ref_used, self.code_ref, self.engine_ref))
        if has_start and (not has_context or not self.effective_periods):
            raise ValueError("cancelled with start requires context and periods")
        if not has_start and (has_any_context or self.effective_periods or self.seed is not None or self.partial_artifact_refs):
            raise ValueError("cancelled without start must not have execution context")
        # Sin resultado final en cualquier caso
        if self.result_id is not None:
            raise ValueError("cancelled must not have result_id")
        return self


# ── ExperimentResult ────────────────────────────────────────────────────────────


class ExperimentResult(BaseModel):
    """Resultado vinculado a run completado.

    Schema: ``experiment_result.v1``.
    artifact_refs: opacas estrictas, no vac\u00edas ni duplicadas.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["experiment_result.v1"] = Field(
        default="experiment_result.v1", frozen=True
    )
    result_id: OpaqueId
    run_id: OpaqueId
    artifact_refs: tuple[ArtifactRef, ...]

    @model_validator(mode="after")
    def _validate_artifact_refs_non_empty(self) -> "ExperimentResult":
        if not self.artifact_refs:
            raise ValueError("artifact_refs must contain at least one entry")
        return self

    @model_validator(mode="after")
    def _validate_artifact_refs_unique(self) -> "ExperimentResult":
        if len(self.artifact_refs) != len(set(self.artifact_refs)):
            raise ValueError("artifact_refs must not contain duplicates")
        return self


# ── Helper puro de transiciones ─────────────────────────────────────────────────

VALID_TRANSITIONS = MappingProxyType({
    "pending": ("running", "cancelled"),
    "running": ("completed", "failed", "cancelled"),
})

TERMINAL_STATES: frozenset[str] = frozenset({"completed", "failed", "cancelled"})


def validate_transition(from_state: str, to_state: str) -> bool:
    """Verificar si una transici\u00f3n de estado es v\u00e1lida.

    Args:
        from_state: estado actual (pending | running | completed | failed | cancelled).
        to_state: estado destino.

    Returns:
        True si la transici\u00f3n es v\u00e1lida, False en caso contrario.
    """
    allowed = VALID_TRANSITIONS.get(from_state, ())
    return to_state in allowed


def is_terminal(state: str) -> bool:
    """Check whether a run state is terminal (no outgoing transitions)."""
    return state in TERMINAL_STATES


def resolve_run_variant(
    run: RunAttempt, definition: ExperimentDefinition
) -> ExperimentVariant | None:
    """Resolve the immutable definition variant linked by this run, if any.

    StrategyConfig is taken directly from the immutable definition variant; the
    run contract has no override field. This helper does not resolve storage.
    """
    if run.experiment_ref != definition.experiment_ref:
        return None
    variant = next((v for v in definition.variants if v.variant_id == run.variant_id), None)
    if variant is None or run.dataset_ref_used != variant.dataset_ref:
        return None
    return variant


def run_matches_definition(run: RunAttempt, definition: ExperimentDefinition) -> bool:
    """Return whether a run links to a defined variant and exact dataset ref."""
    return resolve_run_variant(run, definition) is not None


def result_matches_completed_run(run: RunAttempt, result: ExperimentResult) -> bool:
    """Return whether a result belongs to a completed run with matching IDs."""
    return (
        run.status == "completed"
        and run.result_id == result.result_id
        and run.run_id == result.run_id
    )


# ═══════════════════════════════════════════════════════════════════════════════
#  Disposición contract-only — registros separados, no mutan DTOs centrales
# ═══════════════════════════════════════════════════════════════════════════════


class VariantDiscardRecord(BaseModel):
    """Registro de descarte de una variante antes de cualquier ejecución.

    Cada decisión es un registro separado: no muta ``ExperimentDefinition`` ni
    ninguna otra estructura central.  El variant_id se conserva como referencia.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["variant_discard_record.v1"] = Field(
        default="variant_discard_record.v1", frozen=True
    )
    experiment_ref: ExperimentRef
    variant_id: StrictNonEmpty
    decision: Literal["discarded_before_run"] = "discarded_before_run"
    reason: StrictNonEmpty
    recorded_at_ms: StrictInt = Field(..., ge=0)


class ResultExclusionRecord(BaseModel):
    """Registro de exclusión de un resultado.

    Cada decisión es un registro separado: no muta ``ExperimentResult`` ni
    ninguna otra estructura central.  El result_id se conserva como referencia.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["result_exclusion_record.v1"] = Field(
        default="result_exclusion_record.v1", frozen=True
    )
    result_id: OpaqueId
    decision: Literal["excluded"] = "excluded"
    reason: StrictNonEmpty
    recorded_at_ms: StrictInt = Field(..., ge=0)
