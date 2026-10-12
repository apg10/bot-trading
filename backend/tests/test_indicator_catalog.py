# ACT-C-001 — Tests de schema IndicatorDefinition
"""
Validaciones focales del DTO `IndicatorDefinition` y sus componentes:
- Requeridos / tipos
- Unknowns (None vs valores presentes)
- Rangos conocidos vs NOT_SPECIFIED
- Unidades heredadas
- Warmup / evidencia
- schema_version separado de implementation_version
- Serialización conserva strings/unidades sin fijar instrumento ni precision de API
"""

import pytest
from pydantic import ValidationError

from src.indicators.catalog import (
    IndicatorDefinition,
    ParameterSpec,
    RangeStatus,
    UnitOrigin,
    UnitSpec,
    WarmupEvidence,
    WarmupSpec,
    OutputSpec,
)


# ── 1. indicator_id: obligatorio, no vacío, sin regex/formato impuesto ──────


class TestIndicatorId:

    def test_accepts_non_empty_string(self):
        """Cualquier string no vacío pasa como indicator_id."""
        dto = IndicatorDefinition(
            indicator_id="klt-001", schema_version="v1", implementation_version=None
        )
        assert dto.indicator_id == "klt-001"

    def test_rejects_empty_string(self):
        """indicator_id vacío debe rechazar."""
        with pytest.raises(ValidationError):
            IndicatorDefinition(
                indicator_id="", schema_version="v1", implementation_version=None
            )

    def test_rejects_missing_indicator_id(self):
        """Falta indicator_id → ValidationError."""
        with pytest.raises(ValidationError):
            IndicatorDefinition(schema_version="v1", implementation_version=None)


# ── 2. schema_version: obligatorio, separado de implementation_version ───────


class TestSchemaVersion:

    def test_accepts_non_empty_string(self):
        dto = IndicatorDefinition(
            indicator_id="x", schema_version="draft-0", implementation_version=None
        )
        assert dto.schema_version == "draft-0"

    def test_rejects_empty_schema_version(self):
        with pytest.raises(ValidationError):
            IndicatorDefinition(
                indicator_id="x", schema_version="", implementation_version=None
            )

    def test_separate_from_implementation_version(self):
        """schema_version e implementation_version son independientes."""
        dto = IndicatorDefinition(
            indicator_id="x",
            schema_version="v1",
            implementation_version=None,
        )
        assert dto.schema_version == "v1"
        assert dto.implementation_version is None

    def test_can_have_both_different(self):
        dto = IndicatorDefinition(
            indicator_id="x",
            schema_version="schema-v2",
            implementation_version="impl-v3",
        )
        assert dto.schema_version == "schema-v2"
        assert dto.implementation_version == "impl-v3"


# ── 3. implementation_version: nullable, None = no asignada ─────────────────


class TestImplementationVersion:

    def test_none_means_not_assigned(self):
        dto = IndicatorDefinition(
            indicator_id="x", schema_version="v1", implementation_version=None
        )
        assert dto.implementation_version is None

    def test_accepts_string_value(self):
        dto = IndicatorDefinition(
            indicator_id="x", schema_version="v1", implementation_version="0.1.0"
        )
        assert dto.implementation_version == "0.1.0"

    def test_omitted_implementation_version_is_rejected(self):
        with pytest.raises(ValidationError):
            IndicatorDefinition(indicator_id="x", schema_version="v1")

    def test_empty_implementation_version_is_rejected(self):
        with pytest.raises(ValidationError):
            IndicatorDefinition(
                indicator_id="x", schema_version="v1", implementation_version=""
            )


# ── 4. ParameterSpec: rangos conocidos vs NOT_SPECIFIED ──────────────────────


class TestParameterSpec:

    def test_minimal_valid(self):
        p = ParameterSpec(name="ema_period", scalar_type="int")
        assert p.name == "ema_period"
        assert p.function_configurable is False
        assert p.consumer_exposed is None
        assert p.implementation_default is None
        assert p.min_value is None
        assert p.max_value is None
        assert p.range_status == RangeStatus.NOT_SPECIFIED

    def test_specified_range_requires_min_max(self):
        p = ParameterSpec(
            name="multiplier",
            scalar_type="float",
            range_status=RangeStatus.SPECIFIED,
            min_value=0.5,
            max_value=5.0,
        )
        assert p.range_status == RangeStatus.SPECIFIED
        assert p.min_value == 0.5
        assert p.max_value == 5.0

    def test_specified_range_rejects_missing_min(self):
        with pytest.raises(ValidationError):
            ParameterSpec(
                name="x",
                scalar_type="float",
                range_status=RangeStatus.SPECIFIED,
                max_value=1.0,
            )

    def test_specified_range_rejects_missing_max(self):
        with pytest.raises(ValidationError):
            ParameterSpec(
                name="x",
                scalar_type="float",
                range_status=RangeStatus.SPECIFIED,
                min_value=0.0,
            )

    def test_specified_range_rejects_min_greater_than_max(self):
        with pytest.raises(ValidationError, match="min_value cannot exceed max_value"):
            ParameterSpec(
                name="period", scalar_type="int", range_status=RangeStatus.SPECIFIED,
                min_value=10, max_value=1,
            )

    def test_default_must_match_scalar_type(self):
        with pytest.raises(ValidationError, match="must match scalar_type"):
            ParameterSpec(name="period", scalar_type="int", implementation_default=1.5)

    def test_default_must_be_within_specified_range(self):
        with pytest.raises(ValidationError, match="inside the specified range"):
            ParameterSpec(
                name="period", scalar_type="int", implementation_default=20,
                range_status=RangeStatus.SPECIFIED, min_value=1, max_value=10,
            )

    def test_not_specified_rejects_present_min_max(self):
        """NOT_SPECIFIED no puede tener min/max."""
        with pytest.raises(ValidationError):
            ParameterSpec(
                name="x",
                scalar_type="float",
                range_status=RangeStatus.NOT_SPECIFIED,
                min_value=0.0,
                max_value=1.0,
            )

    def test_consumer_exposed_default_is_none(self):
        """consumer_exposed default es None (no determinado), no False."""
        p = ParameterSpec(name="x", scalar_type="int")
        assert p.consumer_exposed is None


# ── 5. WarmupSpec: rule/bars opcionales, evidencia ──────────────────────────


class TestWarmupSpec:

    def test_default_none_values(self):
        w = WarmupSpec()
        assert w.rule is None
        assert w.bars is None
        assert w.evidence == WarmupEvidence.NOT_SPECIFIED

    def test_documented_evidence(self):
        w = WarmupSpec(rule="ema+atr warmup", bars=34, evidence=WarmupEvidence.DOCUMENTED)
        assert w.rule == "ema+atr warmup"
        assert w.bars == 34
        assert w.evidence == WarmupEvidence.DOCUMENTED

    def test_deduced_evidence(self):
        w = WarmupSpec(rule="inferred", evidence=WarmupEvidence.DEDUCED)
        assert w.evidence == WarmupEvidence.DEDUCED

    def test_none_is_unknown_not_zero(self):
        """None representa unknown, no cero."""
        w = WarmupSpec()
        assert w.bars is None  # no 0

    def test_unknown_evidence_rejects_rule_or_bars(self):
        with pytest.raises(ValidationError, match="unspecified warmup"):
            WarmupSpec(rule="ema+atr", evidence=WarmupEvidence.NOT_SPECIFIED)

    def test_documented_evidence_requires_rule(self):
        with pytest.raises(ValidationError, match="requires a non-empty rule"):
            WarmupSpec(bars=34, evidence=WarmupEvidence.DOCUMENTED)

    def test_negative_bars_rejected(self):
        with pytest.raises(ValidationError, match="cannot be negative"):
            WarmupSpec(rule="warmup", bars=-1, evidence=WarmupEvidence.DEDUCED)


# ── 6. UnitSpec: origen y valor ─────────────────────────────────────────────


class TestUnitSpec:

    def test_instrument_origin(self):
        u = UnitSpec(unit=None, origin=UnitOrigin.INSTRUMENT)
        assert u.unit is None
        assert u.origin == UnitOrigin.INSTRUMENT

    def test_fixed_origin_with_value(self):
        u = UnitSpec(unit="TEST_UNIT", origin=UnitOrigin.FIXED)
        assert u.unit == "TEST_UNIT"
        assert u.origin == UnitOrigin.FIXED

    def test_not_specified_origin(self):
        u = UnitSpec(origin=UnitOrigin.NOT_SPECIFIED)
        assert u.unit is None
        assert u.origin == UnitOrigin.NOT_SPECIFIED

    def test_fixed_origin_requires_unit(self):
        with pytest.raises(ValidationError, match="fixed unit origin requires"):
            UnitSpec(origin=UnitOrigin.FIXED)

    def test_instrument_origin_cannot_set_fixed_unit(self):
        with pytest.raises(ValidationError, match="must not fix a unit value"):
            UnitSpec(unit="TEST_UNIT", origin=UnitOrigin.INSTRUMENT)

    def test_not_specified_origin_cannot_set_unit(self):
        with pytest.raises(ValidationError, match="must not fix a unit value"):
            UnitSpec(unit="TEST_UNIT", origin=UnitOrigin.NOT_SPECIFIED)


# ── 7. OutputSpec: serialización conserva strings/unidades ───────────────────


class TestOutputSpec:

    def test_accepts_api_rounding(self):
        o = OutputSpec(name="ema", output_type="float", api_rounding="2dp")
        assert o.api_rounding == "2dp"

    def test_serialization_preserves_unit_string(self):
        o = OutputSpec(
            name="upper_band",
            output_type="float",
            unit=UnitSpec(unit="TEST_UNIT", origin=UnitOrigin.FIXED),
        )
        dumped = o.model_dump()
        assert dumped["unit"]["unit"] == "TEST_UNIT"
        assert dumped["unit"]["origin"] == "fixed"

    def test_serialization_preserves_api_rounding(self):
        o = OutputSpec(name="histogram", output_type="float", api_rounding="4dp")
        dumped = o.model_dump()
        assert dumped["api_rounding"] == "4dp"


# ── 8. IndicatorDefinition: estructura completa y serialización ──────────────


class TestIndicatorDefinition:

    @pytest.fixture
    def sample_definition(self):
        """Fixture sintético — NO es un registro de catálogo canónico."""
        return IndicatorDefinition(
            indicator_id="kltner-test-fixture",
            schema_version="draft-0",
            implementation_version=None,
            parameters=[
                ParameterSpec(
                    name="ema_period",
                    scalar_type="int",
                    function_configurable=True,
                    consumer_exposed=False,
                    implementation_default=20,
                    range_status=RangeStatus.SPECIFIED,
                    min_value=1,
                    max_value=200,
                ),
                ParameterSpec(
                    name="atr_period",
                    scalar_type="int",
                    function_configurable=True,
                    consumer_exposed=False,
                    implementation_default=14,
                    range_status=RangeStatus.SPECIFIED,
                    min_value=1,
                    max_value=100,
                ),
                ParameterSpec(
                    name="multiplier",
                    scalar_type="float",
                    function_configurable=True,
                    consumer_exposed=False,
                    implementation_default=2.0,
                    range_status=RangeStatus.SPECIFIED,
                    min_value=0.5,
                    max_value=5.0,
                ),
            ],
            inputs=[
                OutputSpec(
                    name="candles",
                    output_type="list[OHLCV]",
                    unit=UnitSpec(origin=UnitOrigin.INSTRUMENT),
                ),
            ],
            outputs=[
                OutputSpec(
                    name="keltner_points",
                    output_type="list[KeltnerPoint]",
                    unit=UnitSpec(unit=None, origin=UnitOrigin.NOT_SPECIFIED),
                ),
            ],
            warmup=WarmupSpec(rule="ema_period + atr_period", bars=34, evidence=WarmupEvidence.DEDUCED),
            causality_note="evidence: deduced from EMA seed convention",
        )

    def test_accepts_full_structure(self, sample_definition):
        assert sample_definition.indicator_id == "kltner-test-fixture"
        assert sample_definition.schema_version == "draft-0"
        assert sample_definition.implementation_version is None
        assert len(sample_definition.parameters) == 3
        assert len(sample_definition.inputs) == 1
        assert len(sample_definition.outputs) == 1

    def test_serialization_preserves_all_fields(self, sample_definition):
        dumped = sample_definition.model_dump()
        assert dumped["indicator_id"] == "kltner-test-fixture"
        assert dumped["schema_version"] == "draft-0"
        assert dumped["implementation_version"] is None
        assert len(dumped["parameters"]) == 3
        assert dumped["warmup"]["rule"] == "ema_period + atr_period"
        assert dumped["warmup"]["bars"] == 34
        assert dumped["warmup"]["evidence"] == "deduced"
        assert dumped["causality_note"] == "evidence: deduced from EMA seed convention"

    def test_serialization_preserves_units_without_instrument(self, sample_definition):
        """Unidades heredadas del instrumento se conservan sin fijar USDT."""
        dumped = sample_definition.model_dump()
        input_unit = dumped["inputs"][0]["unit"]
        assert input_unit["origin"] == "instrument"
        assert input_unit["unit"] is None

    def test_rejects_invalid_scalar_type(self):
        """scalar_type solo admite int, float o bool."""
        with pytest.raises(ValidationError):
            IndicatorDefinition(
                indicator_id="x",
                schema_version="v1",
                implementation_version=None,
                parameters=[ParameterSpec(name="x", scalar_type=123)],  # type: ignore
            )

    def test_rejects_unsupported_scalar_type_string(self):
        with pytest.raises(ValidationError):
            ParameterSpec(name="x", scalar_type="string")  # type: ignore[arg-type]

    def test_empty_parameters_list_is_valid(self):
        """Sin parámetros es válido (no se fuerza un catálogo poblado)."""
        dto = IndicatorDefinition(
            indicator_id="empty-test", schema_version="v0", implementation_version=None
        )
        assert dto.parameters == []


# ── 9. Validación de tipos inválidos ────────────────────────────────────────


class TestTypeValidation:

    def test_rejects_invalid_range_status_string(self):
        with pytest.raises(ValidationError):
            ParameterSpec(
                name="x",
                scalar_type="int",
                range_status="invalid_enum",  # type: ignore
            )

    def test_rejects_invalid_warmup_evidence(self):
        with pytest.raises(ValidationError):
            WarmupSpec(evidence="unknown_value")  # type: ignore

    def test_rejects_invalid_unit_origin(self):
        with pytest.raises(ValidationError):
            UnitSpec(origin="bad_origin")  # type: ignore

    @pytest.mark.parametrize(
        "model,payload",
        [
            (ParameterSpec, {"name": "x", "scalar_type": "int", "unexpected": True}),
            (UnitSpec, {"origin": UnitOrigin.INSTRUMENT, "unexpected": True}),
            (WarmupSpec, {"unexpected": True}),
            (OutputSpec, {"name": "x", "output_type": "float", "unexpected": True}),
            (IndicatorDefinition, {
                "indicator_id": "x", "schema_version": "test-schema",
                "implementation_version": None, "unexpected": True,
            }),
        ],
    )
    def test_contract_dtos_reject_unknown_fields(self, model, payload):
        with pytest.raises(ValidationError):
            model(**payload)


# ── Gate summary ─────────────────────────────────────────────────────────────
# Total tests in this file should pass when run from backend/:
#   PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONDONTWRITEBYTECODE=1 \
#     .venv/bin/python -B -m pytest -q -p no:cacheprovider \
#     --capture=sys tests/test_indicator_catalog.py


# ── 10. Catálogo poblado — entradas estáticas Keltner/MACD ───────────────────


class TestCatalogPopulation:

    @pytest.fixture
    def catalog(self):
        from src.indicators.catalog import INDICATOR_CATALOG
        return INDICATOR_CATALOG

    # ── IDs y schema_version ──────────────────────────────────────────────

    def test_catalog_contains_keltner_and_macd(self, catalog):
        """El catálogo debe contener exactamente los IDs 'keltner' y 'macd'."""
        assert set(catalog.keys()) == {"keltner", "macd"}

    def test_keltner_schema_version(self, catalog):
        assert catalog["keltner"].schema_version == "indicator_definition.v1"

    def test_macd_schema_version(self, catalog):
        assert catalog["macd"].schema_version == "indicator_definition.v1"

    def test_implementation_version_none(self, catalog):
        """Ambos indicadores tienen implementation_version=None."""
        assert catalog["keltner"].implementation_version is None
        assert catalog["macd"].implementation_version is None

    # ── Parámetros y defaults ─────────────────────────────────────────────

    def test_keltner_parameters_count(self, catalog):
        k = catalog["keltner"]
        assert len(k.parameters) == 3

    def test_keltner_ema_period_default(self, catalog):
        k = catalog["keltner"]
        ema_p = next(p for p in k.parameters if p.name == "ema_period")
        assert ema_p.scalar_type == "int"
        assert ema_p.function_configurable is True
        assert ema_p.consumer_exposed is False
        assert ema_p.implementation_default == 20

    def test_keltner_atr_period_default(self, catalog):
        k = catalog["keltner"]
        atr_p = next(p for p in k.parameters if p.name == "atr_period")
        assert atr_p.scalar_type == "int"
        assert atr_p.implementation_default == 14

    def test_keltner_multiplier_default(self, catalog):
        k = catalog["keltner"]
        mul_p = next(p for p in k.parameters if p.name == "multiplier")
        assert mul_p.scalar_type == "float"
        assert mul_p.implementation_default == 2.0

    def test_macd_parameters_count(self, catalog):
        m = catalog["macd"]
        assert len(m.parameters) == 3

    def test_macd_fast_period_default(self, catalog):
        m = catalog["macd"]
        fast_p = next(p for p in m.parameters if p.name == "fast_period")
        assert fast_p.scalar_type == "int"
        assert fast_p.function_configurable is True
        assert fast_p.consumer_exposed is False
        assert fast_p.implementation_default == 12

    def test_macd_slow_period_default(self, catalog):
        m = catalog["macd"]
        slow_p = next(p for p in m.parameters if p.name == "slow_period")
        assert slow_p.scalar_type == "int"
        assert slow_p.implementation_default == 26

    def test_macd_signal_period_default(self, catalog):
        m = catalog["macd"]
        sig_p = next(p for p in m.parameters if p.name == "signal_period")
        assert sig_p.scalar_type == "int"
        assert sig_p.implementation_default == 9

    # ── Exposición (consumer_exposed) ─────────────────────────────────────

    def test_all_keltner_consumer_exposed_false(self, catalog):
        k = catalog["keltner"]
        for p in k.parameters:
            assert p.consumer_exposed is False

    def test_all_macd_consumer_exposed_false(self, catalog):
        m = catalog["macd"]
        for p in m.parameters:
            assert p.consumer_exposed is False

    # ── Rangos desconocidos (no inventados) ───────────────────────────────

    def test_keltner_no_ranges_specified(self, catalog):
        """Ningún parámetro de Keltner tiene rango especificado."""
        k = catalog["keltner"]
        for p in k.parameters:
            assert p.range_status == RangeStatus.NOT_SPECIFIED
            assert p.min_value is None
            assert p.max_value is None

    def test_macd_no_ranges_specified(self, catalog):
        """Ningún parámetro de MACD tiene rango especificado."""
        m = catalog["macd"]
        for p in m.parameters:
            assert p.range_status == RangeStatus.NOT_SPECIFIED
            assert p.min_value is None
            assert p.max_value is None

    # ── Unidades ──────────────────────────────────────────────────────────

    def test_keltner_input_unit_instrument(self, catalog):
        k = catalog["keltner"]
        assert len(k.inputs) == 1
        assert k.inputs[0].unit.origin == UnitOrigin.INSTRUMENT
        assert k.inputs[0].unit.unit is None

    def test_macd_input_unit_instrument(self, catalog):
        m = catalog["macd"]
        assert len(m.inputs) == 1
        assert m.inputs[0].unit.origin == UnitOrigin.INSTRUMENT
        assert m.inputs[0].unit.unit is None

    def test_keltner_output_units(self, catalog):
        k = catalog["keltner"]
        output_names = {o.name for o in k.outputs}
        assert output_names == {"time_ms", "ema_val", "upper", "lower", "atr"}
        time_out = next(o for o in k.outputs if o.name == "time_ms")
        assert time_out.unit.origin == UnitOrigin.FIXED
        assert time_out.unit.unit == "ms"

    def test_macd_output_units(self, catalog):
        m = catalog["macd"]
        output_names = {o.name for o in m.outputs}
        assert output_names == {"time_ms", "macd_line", "signal_line", "histogram"}
        time_out = next(o for o in m.outputs if o.name == "time_ms")
        assert time_out.unit.origin == UnitOrigin.FIXED
        assert time_out.unit.unit == "ms"

    # ── Warmup ────────────────────────────────────────────────────────────

    def test_keltner_warmup(self, catalog):
        k = catalog["keltner"]
        assert k.warmup.rule == "ema_period + atr_period"
        assert k.warmup.bars == 34
        assert k.warmup.evidence == WarmupEvidence.DOCUMENTED

    def test_macd_warmup(self, catalog):
        m = catalog["macd"]
        assert m.warmup.rule == "max(fast_period, slow_period) + signal_period - 1"
        assert m.warmup.bars == 34
        assert m.warmup.evidence == WarmupEvidence.DOCUMENTED

    # ── Outputs ───────────────────────────────────────────────────────────

    def test_keltner_outputs_count(self, catalog):
        k = catalog["keltner"]
        assert len(k.outputs) == 5

    def test_macd_outputs_count(self, catalog):
        m = catalog["macd"]
        assert len(m.outputs) == 4

    def test_keltner_api_rounding(self, catalog):
        k = catalog["keltner"]
        for o in k.outputs:
            if o.name != "time_ms":
                assert o.api_rounding == "2dp"

    def test_macd_api_rounding(self, catalog):
        m = catalog["macd"]
        for o in m.outputs:
            if o.name != "time_ms":
                assert o.api_rounding == "4dp"

    # ── Ausencia de FYL ───────────────────────────────────────────────────

    def test_catalog_does_not_contain_fyl(self, catalog):
        """FYL no debe estar en el catálogo poblado."""
        assert "fyl" not in catalog
        assert "FYL" not in catalog

    # ── Serialización ─────────────────────────────────────────────────────

    def test_keltner_serialization_roundtrip(self, catalog):
        k = catalog["keltner"]
        dumped = k.model_dump()
        assert dumped["indicator_id"] == "keltner"
        assert dumped["schema_version"] == "indicator_definition.v1"
        assert dumped["implementation_version"] is None
        assert len(dumped["parameters"]) == 3
        assert dumped["warmup"]["bars"] == 34

    def test_macd_serialization_roundtrip(self, catalog):
        m = catalog["macd"]
        dumped = m.model_dump()
        assert dumped["indicator_id"] == "macd"
        assert dumped["schema_version"] == "indicator_definition.v1"
        assert dumped["implementation_version"] is None
        assert len(dumped["parameters"]) == 3
        assert dumped["warmup"]["bars"] == 34
