# IndicatorDefinition schema — DTOs puros (sin catálogo poblado ni cálculo).
"""
ACT-C-001 microtask: solo contratos Pydantic para describir indicadores.

No se agregan entradas de catálogo, IDs canónicos, versiones de producción,
endpoints, exportación JSON, CapabilitiesContract, frontend, ejecución,
dependencias nuevas ni lógica de cálculo.

Los fixtures sintéticos (Keltner/MACD) solo existen en test_indicator_catalog.py.
"""

from __future__ import annotations

from enum import Enum
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictFloat, StrictInt, field_validator, model_validator


# ── Enums ────────────────────────────────────────────────────────────────────


class RangeStatus(str, Enum):
    """Estado de un rango de parámetro."""
    SPECIFIED = "specified"
    NOT_SPECIFIED = "not_specified"


class UnitOrigin(str, Enum):
    """Origen de una unidad para input/output."""
    INSTRUMENT = "instrument"
    FIXED = "fixed"
    NOT_SPECIFIED = "not_specified"


class WarmupEvidence(str, Enum):
    """Nivel de evidencia de la regla de warmup."""
    DOCUMENTED = "documented"
    DEDUCED = "deduced"
    NOT_SPECIFIED = "not_specified"


# ── ParameterSpec ────────────────────────────────────────────────────────────


class ParameterSpec(BaseModel):
    """Descripción de un parámetro escalar de un indicador."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1)
    scalar_type: Literal["int", "float", "bool"]
    function_configurable: bool = False
    consumer_exposed: Optional[bool] = None  # None = no determinado
    implementation_default: Optional[StrictInt | StrictFloat | StrictBool] = None
    min_value: Optional[StrictInt | StrictFloat] = None
    max_value: Optional[StrictInt | StrictFloat] = None
    range_status: RangeStatus = RangeStatus.NOT_SPECIFIED

    @model_validator(mode="after")
    def validate_parameter_spec(self) -> "ParameterSpec":
        default = self.implementation_default
        if default is not None:
            valid_default = {
                "int": type(default) is int,
                "float": type(default) is float,
                "bool": type(default) is bool,
            }[self.scalar_type]
            if not valid_default:
                raise ValueError(f"implementation_default must match scalar_type={self.scalar_type}")

        if self.range_status == RangeStatus.SPECIFIED:
            if self.min_value is None or self.max_value is None:
                raise ValueError(
                    "range_status='specified' requiere min_value y max_value"
                )
            if self.scalar_type == "bool":
                raise ValueError("boolean parameters cannot declare numeric ranges")
            if self.scalar_type == "int" and (
                type(self.min_value) is not int or type(self.max_value) is not int
            ):
                raise ValueError("int parameters require integer range bounds")
            if self.scalar_type == "float" and (
                type(self.min_value) is not float or type(self.max_value) is not float
            ):
                raise ValueError("float parameters require float range bounds")
            if self.min_value > self.max_value:
                raise ValueError("min_value cannot exceed max_value")
            if default is not None and not (self.min_value <= default <= self.max_value):
                raise ValueError("implementation_default must be inside the specified range")
        else:
            # NOT_SPECIFIED → min/max deben ser None
            if self.min_value is not None or self.max_value is not None:
                raise ValueError(
                    "range_status='not_specified' no puede tener min_value/max_value"
                )
        return self


# ── UnitSpec ─────────────────────────────────────────────────────────────────


class UnitSpec(BaseModel):
    """Unidad para input o output de un indicador."""

    model_config = ConfigDict(extra="forbid")

    unit: Optional[str] = None  # None = heredada del instrumento / no especificada
    origin: UnitOrigin = UnitOrigin.NOT_SPECIFIED

    @model_validator(mode="after")
    def unit_matches_origin(self) -> "UnitSpec":
        if self.origin == UnitOrigin.FIXED:
            if self.unit is None or not self.unit.strip():
                raise ValueError("fixed unit origin requires a non-empty unit")
        elif self.unit is not None:
            raise ValueError("instrument/not_specified unit origins must not fix a unit value")
        return self


# ── WarmupSpec ───────────────────────────────────────────────────────────────


class WarmupSpec(BaseModel):
    """Regla de calentamiento dependiente de parámetros."""

    model_config = ConfigDict(extra="forbid")

    rule: Optional[str] = None  # None = unknown / no determinado
    bars: Optional[int] = None  # None = no especificado
    evidence: WarmupEvidence = WarmupEvidence.NOT_SPECIFIED

    @model_validator(mode="after")
    def warmup_evidence_matches_fields(self) -> "WarmupSpec":
        if self.bars is not None and self.bars < 0:
            raise ValueError("warmup bars cannot be negative")
        if self.evidence == WarmupEvidence.NOT_SPECIFIED:
            if self.rule is not None or self.bars is not None:
                raise ValueError("unspecified warmup cannot claim a rule or bar count")
        elif self.rule is None or not self.rule.strip():
            raise ValueError("documented/deduced warmup requires a non-empty rule")
        return self


# ── OutputSpec ───────────────────────────────────────────────────────────────


class OutputSpec(BaseModel):
    """Descripción de un campo de salida de un indicador."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1)
    output_type: str = Field(..., description="tipo escalar o compuesto")
    unit: UnitSpec = Field(default_factory=UnitSpec)
    api_rounding: Optional[str] = None  # ej. "2dp"; no define precisión matemática


# ── IndicatorDefinition ──────────────────────────────────────────────────────


class IndicatorDefinition(BaseModel):
    """Contrato completo para describir un indicador técnico."""

    model_config = ConfigDict(extra="forbid")

    indicator_id: str = Field(..., min_length=1)
    schema_version: str = Field(..., min_length=1)
    implementation_version: Optional[str] = Field(..., min_length=1)  # None = no asignada aún

    parameters: list[ParameterSpec] = Field(default_factory=list)
    inputs: list[OutputSpec] = Field(default_factory=list)
    outputs: list[OutputSpec] = Field(default_factory=list)
    units: dict[str, UnitSpec] = Field(default_factory=dict)
    warmup: WarmupSpec = Field(default_factory=WarmupSpec)
    causality_note: Optional[str] = None  # evidencia causal, no promesa formal


# ── Catálogo estático de indicadores ────────────────────────────────────────

INDICATOR_CATALOG: dict[str, IndicatorDefinition] = {
    "keltner": IndicatorDefinition(
        indicator_id="keltner",
        schema_version="indicator_definition.v1",
        implementation_version=None,
        parameters=[
            ParameterSpec(
                name="ema_period",
                scalar_type="int",
                function_configurable=True,
                consumer_exposed=False,
                implementation_default=20,
                range_status=RangeStatus.NOT_SPECIFIED,
            ),
            ParameterSpec(
                name="atr_period",
                scalar_type="int",
                function_configurable=True,
                consumer_exposed=False,
                implementation_default=14,
                range_status=RangeStatus.NOT_SPECIFIED,
            ),
            ParameterSpec(
                name="multiplier",
                scalar_type="float",
                function_configurable=True,
                consumer_exposed=False,
                implementation_default=2.0,
                range_status=RangeStatus.NOT_SPECIFIED,
            ),
        ],
        inputs=[
            OutputSpec(
                name="candles",
                output_type="list[OHLCV]",
                unit=UnitSpec(unit=None, origin=UnitOrigin.INSTRUMENT),
            ),
        ],
        outputs=[
            OutputSpec(
                name="time_ms",
                output_type="int",
                unit=UnitSpec(unit="ms", origin=UnitOrigin.FIXED),
            ),
            OutputSpec(
                name="ema_val",
                output_type="float",
                unit=UnitSpec(unit=None, origin=UnitOrigin.INSTRUMENT),
                api_rounding="2dp",
            ),
            OutputSpec(
                name="upper",
                output_type="float",
                unit=UnitSpec(unit=None, origin=UnitOrigin.INSTRUMENT),
                api_rounding="2dp",
            ),
            OutputSpec(
                name="lower",
                output_type="float",
                unit=UnitSpec(unit=None, origin=UnitOrigin.INSTRUMENT),
                api_rounding="2dp",
            ),
            OutputSpec(
                name="atr",
                output_type="float",
                unit=UnitSpec(unit=None, origin=UnitOrigin.INSTRUMENT),
                api_rounding="2dp",
            ),
        ],
        units={
            "price": UnitSpec(unit=None, origin=UnitOrigin.INSTRUMENT),
        },
        warmup=WarmupSpec(
            rule="ema_period + atr_period",
            bars=34,
            evidence=WarmupEvidence.DOCUMENTED,
        ),
        causality_note="evidence: documented in implementation and tests; not a guarantee of equivalence with the original method",
    ),
    "macd": IndicatorDefinition(
        indicator_id="macd",
        schema_version="indicator_definition.v1",
        implementation_version=None,
        parameters=[
            ParameterSpec(
                name="fast_period",
                scalar_type="int",
                function_configurable=True,
                consumer_exposed=False,
                implementation_default=12,
                range_status=RangeStatus.NOT_SPECIFIED,
            ),
            ParameterSpec(
                name="slow_period",
                scalar_type="int",
                function_configurable=True,
                consumer_exposed=False,
                implementation_default=26,
                range_status=RangeStatus.NOT_SPECIFIED,
            ),
            ParameterSpec(
                name="signal_period",
                scalar_type="int",
                function_configurable=True,
                consumer_exposed=False,
                implementation_default=9,
                range_status=RangeStatus.NOT_SPECIFIED,
            ),
        ],
        inputs=[
            OutputSpec(
                name="closes",
                output_type="list[float]",
                unit=UnitSpec(unit=None, origin=UnitOrigin.INSTRUMENT),
            ),
        ],
        outputs=[
            OutputSpec(
                name="time_ms",
                output_type="int",
                unit=UnitSpec(unit="ms", origin=UnitOrigin.FIXED),
            ),
            OutputSpec(
                name="macd_line",
                output_type="float",
                unit=UnitSpec(unit=None, origin=UnitOrigin.INSTRUMENT),
                api_rounding="4dp",
            ),
            OutputSpec(
                name="signal_line",
                output_type="float",
                unit=UnitSpec(unit=None, origin=UnitOrigin.INSTRUMENT),
                api_rounding="4dp",
            ),
            OutputSpec(
                name="histogram",
                output_type="float",
                unit=UnitSpec(unit=None, origin=UnitOrigin.INSTRUMENT),
                api_rounding="4dp",
            ),
        ],
        units={
            "price": UnitSpec(unit=None, origin=UnitOrigin.INSTRUMENT),
        },
        warmup=WarmupSpec(
            rule="max(fast_period, slow_period) + signal_period - 1",
            bars=34,
            evidence=WarmupEvidence.DOCUMENTED,
        ),
        causality_note="evidence: documented in docstring and tests; not a guarantee of equivalence with the original method",
    ),
}
