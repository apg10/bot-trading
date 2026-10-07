"""N02: snapshots identificados y escenarios advisory, nunca reglas ejecutables.

Los datos enviados por un cliente no se certifican como datos de Binance. Las
afirmaciones comprobables son referencias/comparaciones, no prosa del modelo.
Condiciones e invalidaciones describen un futuro snapshot CERRADO: este módulo
no las monitoriza, no evalúa estrategias y no tiene autoridad sobre ejecución.
"""

from __future__ import annotations

import hashlib
import json
import math
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

QWEN_MODEL = "qwen3.6:35b-a3b-q4_K_M-128k"
MAX_SNAPSHOT_AGE_MS = 90_000
Ref = Annotated[str, Field(min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9_]*$")]
FiniteNumber = Annotated[float, Field(allow_inf_nan=False, strict=True)]
Relation = Literal["gt", "gte", "lt", "lte", "eq", "ne"]
INVERSE = {"gt": "lte", "gte": "lt", "lt": "gte", "lte": "gt", "eq": "ne", "ne": "eq"}


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    @field_validator("executable", "execution_available", "provenance_verified", mode="before", check_fields=False)
    @classmethod
    def never_executable(cls, value):
        if type(value) is not bool or value is not False:
            raise ValueError("Se exige false booleano, nunca autorización de ejecución")
        return value


class SnapshotMarketState(Contract):
    connected: bool
    history_loaded: bool
    complete: bool
    recovering: bool
    stale: bool
    pending_gaps: int = Field(ge=0)


class SnapshotEvidence(Contract):
    value: FiniteNumber
    dimension: Literal["price", "volume", "oscillator"]
    unit: str = Field(min_length=1, max_length=16, pattern=r"^[A-Z0-9]+$")
    observed_at_ms: int = Field(ge=0)


class ScenarioSnapshot(Contract):
    """Contexto PAPER declarado por el solicitante, no certificado por N01.

    T = close_time inclusivo Binance de la última vela cerrada 1m. Se exige
    T <= captured_at_ms <= valid_until_ms <= T + 90000 y UTC actual dentro
    de ese intervalo. Cada evidencia identifica su propio cierre y su unidad.
    La API no acepta balances, órdenes, parámetros de riesgo ni texto de prompts.
    """
    schema_version: Literal["market_snapshot.v1"]
    snapshot_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_.:-]+$")
    symbol: str = Field(pattern=r"^[A-Z0-9]{2,12}/[A-Z0-9]{2,12}$")
    timeframe: Literal["1m"]
    environment: Literal["paper"]
    data_source: Literal["TEST_ONLY", "market_engine", "unverified"]
    captured_at_ms: int = Field(ge=0)
    as_of_close_time_ms: int = Field(ge=0)
    valid_until_ms: int = Field(ge=0)
    market_state: SnapshotMarketState
    evidence: dict[Ref, SnapshotEvidence] = Field(min_length=1, max_length=64)
    missing_data: list[Ref] = Field(max_length=32)

    @model_validator(mode="after")
    def coherent(self):
        if self.as_of_close_time_ms % 60_000 != 59_999:
            raise ValueError("Se exige el cierre inclusivo Binance de una vela 1m")
        if not (self.as_of_close_time_ms <= self.captured_at_ms <= self.valid_until_ms
                <= self.as_of_close_time_ms + MAX_SNAPSHOT_AGE_MS):
            raise ValueError("Los timestamps no pueden extender la vigencia de N01")
        if len(set(self.missing_data)) != len(self.missing_data):
            raise ValueError("Datos faltantes duplicados")
        base, quote = self.symbol.split("/")
        for fact in self.evidence.values():
            if fact.observed_at_ms > self.as_of_close_time_ms:
                raise ValueError("No se admiten evidencias de velas en formación/futuras")
            if fact.observed_at_ms % 60_000 != 59_999:
                raise ValueError("La evidencia debe identificar un cierre Binance 1m")
            if fact.unit != (base if fact.dimension == "volume" else quote):
                raise ValueError("Unidad de evidencia incompatible con el símbolo")
            if fact.dimension == "price" and fact.value <= 0:
                raise ValueError("Precio no positivo")
            if fact.dimension == "volume" and fact.value < 0:
                raise ValueError("Volumen negativo")
        return self

    def assert_current(self, now_ms: int) -> None:
        if now_ms < self.captured_at_ms or now_ms > self.valid_until_ms:
            raise ScenarioError("snapshot_expired")
        state = self.market_state
        if not (state.connected and state.history_loaded and state.complete
                and not state.recovering and not state.stale and state.pending_gaps == 0):
            raise ScenarioError("market_incomplete")

    def fingerprint(self) -> str:
        body = json.dumps(self.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(body.encode()).hexdigest()


class EvidenceCitation(Contract):
    evidence_id: Ref
    observed_value: FiniteNumber


class Comparison(Contract):
    left_ref: Ref
    operator: Relation
    right_ref: Ref


class FutureCondition(Contract):
    applies_to: Literal["future_closed_snapshot"]
    metric_ref: Ref
    operator: Relation
    threshold_ref: Ref

    def key(self) -> tuple[str, str, str]:
        return self.metric_ref, self.operator, self.threshold_ref


class ModelScenario(Contract):
    # No campos summary/interpretation libres: el benchmark demostró que pueden
    # contradecir los valores aunque el JSON y observed_value sean correctos.
    evidence: list[EvidenceCitation] = Field(min_length=1, max_length=4)
    observations: list[Comparison] = Field(max_length=3)
    condition_logic: Literal["all"]
    conditions: list[FutureCondition] = Field(min_length=1, max_length=3)
    invalidation_logic: Literal["any"]
    invalidations: list[FutureCondition] = Field(min_length=1, max_length=3)


class ModelScenarios(Contract):
    continuation: ModelScenario
    failure: ModelScenario
    sideways: ModelScenario


class ScenarioOutput(Contract):
    """Hipótesis no ejecutables, nunca predicciones certificadas ni reglas del bot.

    Observaciones: comparaciones verdaderas de evidencias citadas del snapshot.
    Conditions: futuras métricas cerradas contra umbrales del snapshot actual.
    Invalidations: complementos exactos, ANY, de esas condiciones ALL.
    Los resúmenes se añaden fuera de este objeto por el servidor, no por Qwen.
    """
    schema_version: Literal["scenario_analysis.v1"]
    snapshot_id: str
    symbol: str
    timeframe: Literal["1m"]
    as_of_close_time_ms: int
    valid_until_ms: int
    data_source: Literal["TEST_ONLY", "market_engine", "unverified"]
    # La etiqueta preserva la declaración del snapshot, nunca una certificación.
    provenance_verified: Literal[False]
    result_type: Literal["scenario_advisory"]
    executable: Literal[False]
    scenarios: ModelScenarios
    missing_data: list[Ref] = Field(max_length=32)

    def validate_for(self, snapshot: ScenarioSnapshot, now_ms: int) -> None:
        snapshot.assert_current(now_ms)
        for name in ("snapshot_id", "symbol", "timeframe", "as_of_close_time_ms",
                     "valid_until_ms", "data_source"):
            if getattr(self, name) != getattr(snapshot, name):
                raise ScenarioError("snapshot_mismatch")
        if sorted(self.missing_data) != sorted(snapshot.missing_data):
            raise ScenarioError("invalid_evidence")
        for name in ("continuation", "failure", "sideways"):
            scenario = getattr(self.scenarios, name)
            cited = {item.evidence_id for item in scenario.evidence}
            if len(cited) != len(scenario.evidence):
                raise ScenarioError("invalid_evidence")
            for citation in scenario.evidence:
                fact = snapshot.evidence.get(citation.evidence_id)
                if fact is None or citation.observed_value != fact.value:
                    raise ScenarioError("invalid_evidence")
            for observation in scenario.observations:
                left, right = _compatible(snapshot, observation.left_ref, observation.right_ref, cited)
                if not _compare(left.value, observation.operator, right.value):
                    raise ScenarioError("false_observation")
            expected = set()
            for condition in scenario.conditions:
                _compatible(snapshot, condition.metric_ref, condition.threshold_ref, cited)
                expected.add((condition.metric_ref, INVERSE[condition.operator], condition.threshold_ref))
            actual = {condition.key() for condition in scenario.invalidations}
            if (len(expected) != len(scenario.conditions) or actual != expected
                    or len(actual) != len(scenario.invalidations)):
                raise ScenarioError("invalid_invalidation")
            _feasible(scenario.conditions, snapshot)


def _compatible(snapshot, left_ref, right_ref, cited):
    if left_ref not in cited or right_ref not in cited:
        raise ScenarioError("invalid_evidence")
    left, right = snapshot.evidence[left_ref], snapshot.evidence[right_ref]
    if (left.dimension, left.unit) != (right.dimension, right.unit):
        raise ScenarioError("invalid_evidence")
    return left, right


def _compare(left: float, operator: str, right: float) -> bool:
    return {"gt": left > right, "gte": left >= right, "lt": left < right,
            "lte": left <= right, "eq": left == right, "ne": left != right}[operator]


def _feasible(conditions, snapshot):
    """Rechaza conjunciones numéricamente imposibles, sin evaluar ninguna regla."""
    for metric in {condition.metric_ref for condition in conditions}:
        fact = snapshot.evidence[metric]
        low = -math.inf if fact.dimension == "oscillator" else 0.0
        low_closed = fact.dimension != "price"
        high, high_closed = math.inf, False
        equals, excluded = set(), set()
        for condition in conditions:
            if condition.metric_ref != metric:
                continue
            value, op = snapshot.evidence[condition.threshold_ref].value, condition.operator
            if op in ("gt", "gte"):
                if value > low:
                    low, low_closed = value, op == "gte"
                elif value == low:
                    low_closed = low_closed and op == "gte"
            elif op in ("lt", "lte"):
                if value < high:
                    high, high_closed = value, op == "lte"
                elif value == high:
                    high_closed = high_closed and op == "lte"
            elif op == "eq":
                equals.add(value)
            else:
                excluded.add(value)
        if (low > high or (low == high and (not low_closed or not high_closed or low in excluded))
                or len(equals) > 1):
            raise ScenarioError("invalid_condition")
        if equals:
            value = next(iter(equals))
            if (value < low or value > high or value in excluded
                    or (value == low and not low_closed) or (value == high and not high_closed)):
                raise ScenarioError("invalid_condition")


class ScenarioAnalysisRequest(Contract):
    """Cuerpo de /ai/analyze y /ai/scenarios: {snapshot: {...}}.

    Migración incompatible a propósito: ya no se acepta
    {symbol, timeframe, market_data}. /ai/strategy es alias deprecated del
    MISMO asesor de escenarios, no un generador de borradores operativos.
    Todos los campos del snapshot son obligatorios; el esquema OpenAPI y
    test_ai_scenarios.snapshot_dict documentan un ejemplo TEST_ONLY completo.
    """
    snapshot: ScenarioSnapshot


class ScenarioAnalysisResult(Contract):
    analysis: ScenarioOutput
    available: Literal[True] = True
    result_type: Literal["scenario_advisory"] = "scenario_advisory"
    requested_model: Literal[QWEN_MODEL] = QWEN_MODEL
    model_used: Literal[QWEN_MODEL]
    duration_ms: int = Field(ge=0)
    total_duration_ms: float | None = None
    load_duration_ms: float | None = None
    prompt_tokens: int | None = None
    generated_tokens: int | None = None
    generation_tokens_per_second: float | None = None
    data_source: Literal["TEST_ONLY", "unverified"]
    cache_hit: bool = False
    deduplicated: bool = False
    executable: Literal[False] = False
    execution_available: Literal[False] = False
    summaries: dict[Literal["continuation", "failure", "sideways"],
                    Annotated[str, Field(min_length=1, max_length=256)]] = Field(min_length=3, max_length=3)


ERROR_STATUS = {
    "snapshot_expired": 409, "market_incomplete": 409, "snapshot_mismatch": 502,
    "snapshot_id_conflict": 409, "invalid_snapshot": 422, "model_missing": 503,
    "unavailable": 503, "timeout": 504, "busy": 429, "client_closed": 503,
    "invalid_json": 502, "invalid_structure": 502, "invalid_evidence": 502,
    "false_observation": 502, "invalid_invalidation": 502, "invalid_condition": 502,
    "model_mismatch": 502, "incomplete_generation": 502, "unexpected_reasoning": 502,
    "request_too_large": 413,
}


class ScenarioError(Exception):
    """Código público estable; no exponer prompts, respuestas crudas ni errores HTTP."""
    def __init__(self, code: str, *, duration_ms: int = 0, effective_model: str | None = None):
        super().__init__(code)
        self.code = code
        self.status_code = ERROR_STATUS[code]
        self.duration_ms = duration_ms
        self.effective_model = effective_model
