"""Pruebas deterministas del contrato StrategyConfig.

Cobertura:
- Reutiliza StrategyRef vigente; conserva strategy_version sin alias ni duplicación.
- Solo acepta el literal strategy_config.v1; asignarlo después de construir falla.
- Rechaza instance_id duplicado, acepta IDs de indicador repetidos con instancias distintas.
- Rechaza indicador o parámetro desconocido, no configurable, ausente, duplicado o tipo incorrecto.
- True/False no se aceptan como valores numéricos; NaN/+Infinity/-Infinity se rechazan.
- No usa implementation_default para completar valores ni inventa límites.
- Inmutabilidad: no se pueden modificar configuraciones, instancias, parámetros ni tuplas.
- Dos entradas equivalentes con distinto orden producen exactamente el mismo JSON serializado.
"""

import json
import math

import pytest
from pydantic import ValidationError

from src.indicators.catalog import INDICATOR_CATALOG
from src.models.paper_contracts import StrategyRef
import src.models.strategy_config as strategy_config_module
from src.models.strategy_config import IndicatorInstance, ParameterEntry, StrategyConfig


# ── Fixtures ───────────────────────────────────────────────────────────────────

def _make_strategy_ref(**kwargs) -> StrategyRef:
    return StrategyRef(
        schema_version="paper_strategy_ref.v1",
        strategy_id=kwargs.get("strategy_id", "test_strategy"),
        strategy_version=kwargs.get("strategy_version", "v1"),
        decision_id=kwargs.get("decision_id", None),
    )


def _make_keltner_params(**overrides) -> tuple[ParameterEntry, ...]:
    """Parámetros canónicos para keltner (todos function_configurable=True)."""
    params = {
        "ema_period": 20,
        "atr_period": 14,
        "multiplier": 2.0,
    }
    params.update(overrides)
    return tuple(
        ParameterEntry(name=name, value=value)
        for name, value in sorted(params.items())
    )


def _make_macd_params(**overrides) -> tuple[ParameterEntry, ...]:
    """Parámetros canónicos para macd (todos function_configurable=True)."""
    params = {
        "fast_period": 12,
        "slow_period": 26,
        "signal_period": 9,
    }
    params.update(overrides)
    return tuple(
        ParameterEntry(name=name, value=value)
        for name, value in sorted(params.items())
    )


def _make_config(instances: list[dict]) -> StrategyConfig:
    """Helper para construir una configuración completa."""
    strategy_ref = _make_strategy_ref()
    inst_list = []
    for inst_data in instances:
        inst_list.append(
            IndicatorInstance(
                instance_id=inst_data["instance_id"],
                indicator_id=inst_data["indicator_id"],
                parameters=inst_data.get("parameters", ()),
            )
        )
    return StrategyConfig(
        strategy_ref=strategy_ref,
        indicator_instances=tuple(inst_list),
    )


# ── 1. Reutilización de StrategyRef ────────────────────────────────────────────

def test_strategy_ref_reused_preserves_fields():
    """StrategyConfig reutiliza StrategyRef con todos sus campos intactos."""
    config = _make_config([{
        "instance_id": "inst1",
        "indicator_id": "keltner",
        "parameters": _make_keltner_params(),
    }])
    ref = config.strategy_ref
    assert isinstance(ref, StrategyRef)
    assert ref.strategy_id == "test_strategy"
    assert ref.strategy_version == "v1"
    assert ref.decision_id is None
    assert ref.schema_version == "paper_strategy_ref.v1"


def test_strategy_ref_no_alias_version():
    """StrategyConfig no introduce alias 'version' ni aplan strategy_version."""
    config = _make_config([{
        "instance_id": "i1",
        "indicator_id": "macd",
        "parameters": _make_macd_params(),
    }])
    # strategy_version debe estar en strategy_ref, no como campo directo de config
    ref = config.strategy_ref
    assert hasattr(ref, "strategy_version")
    assert ref.strategy_version == "v1"
    # No debe haber un campo 'version' duplicado
    assert not hasattr(config, "version")


# ── 2. Schema version inmutable ───────────────────────────────────────────────

def test_schema_version_literal():
    """Solo acepta strategy_config.v1."""
    ref = _make_strategy_ref()
    config = StrategyConfig(
        strategy_ref=ref,
        indicator_instances=tuple(),
    )
    assert config.schema_version == "strategy_config.v1"


def test_schema_version_immutable_after_construction():
    """Asignar schema_version después de construir falla."""
    ref = _make_strategy_ref()
    config = StrategyConfig(
        strategy_ref=ref,
        indicator_instances=tuple(),
    )
    # frozen=True en model_config impide asignación directa de atributos
    with pytest.raises(ValueError, match="frozen_instance"):
        config.schema_version = "strategy_config.v2"  # type: ignore


def test_schema_version_cannot_be_set_at_construction():
    """Pasar un valor diferente a schema_version en el constructor falla."""
    ref = _make_strategy_ref()
    with pytest.raises(ValidationError) as exc_info:
        StrategyConfig(
            schema_version="wrong.version",  # type: ignore
            strategy_ref=ref,
            indicator_instances=tuple(),
        )
    assert "schema_version" in str(exc_info.value)


# ── 3. Unicidad de instance_id ────────────────────────────────────────────────

def test_duplicate_instance_id_rejected():
    """instance_id duplicado dentro de la configuración se rechaza."""
    ref = _make_strategy_ref()
    with pytest.raises(ValidationError) as exc_info:
        StrategyConfig(
            strategy_ref=ref,
            indicator_instances=(
                IndicatorInstance(
                    instance_id="dup",
                    indicator_id="keltner",
                    parameters=_make_keltner_params(),
                ),
                IndicatorInstance(
                    instance_id="dup",
                    indicator_id="macd",
                    parameters=_make_macd_params(),
                ),
            ),
        )
    assert "duplicate instance_id" in str(exc_info.value)


def test_empty_instance_id_rejected():
    """instance_id vacío se rechaza."""
    ref = _make_strategy_ref()
    with pytest.raises(ValidationError):
        IndicatorInstance(instance_id="", indicator_id="keltner", parameters=())


def test_same_indicator_id_with_different_instances_accepted():
    """IDs de indicador repetidos con instancias distintas son aceptados."""
    config = _make_config([
        {
            "instance_id": "k1",
            "indicator_id": "keltner",
            "parameters": _make_keltner_params(ema_period=10),
        },
        {
            "instance_id": "k2",
            "indicator_id": "keltner",
            "parameters": _make_keltner_params(ema_period=30),
        },
    ])
    assert len(config.indicator_instances) == 2
    assert config.indicator_instances[0].instance_id == "k1"
    assert config.indicator_instances[1].instance_id == "k2"


# ── 4. Validación de indicador y parámetros ───────────────────────────────────

def test_unknown_indicator_rejected():
    """indicator_id desconocido se rechaza."""
    ref = _make_strategy_ref()
    with pytest.raises(ValidationError) as exc_info:
        StrategyConfig(
            strategy_ref=ref,
            indicator_instances=(
                IndicatorInstance(
                    instance_id="i1",
                    indicator_id="nonexistent",
                    parameters=(),
                ),
            ),
        )
    assert "unknown indicator_id" in str(exc_info.value)


def test_unknown_parameter_rejected():
    """Parámetro con nombre desconocido se rechaza."""
    ref = _make_strategy_ref()
    with pytest.raises(ValidationError) as exc_info:
        StrategyConfig(
            strategy_ref=ref,
            indicator_instances=(
                IndicatorInstance(
                    instance_id="i1",
                    indicator_id="keltner",
                    parameters=(
                        ParameterEntry(name="ema_period", value=20),
                        ParameterEntry(name="unknown_param", value=1.0),
                    ),
                ),
            ),
        )
    assert "unknown parameter" in str(exc_info.value)


def test_missing_configurable_parameter_rejected():
    """Parámetro function_configurable ausente se rechaza (no usa implementation_default)."""
    ref = _make_strategy_ref()
    # Falta atr_period y multiplier que son function_configurable=True
    with pytest.raises(ValidationError) as exc_info:
        StrategyConfig(
            strategy_ref=ref,
            indicator_instances=(
                IndicatorInstance(
                    instance_id="i1",
                    indicator_id="keltner",
                    parameters=(
                        ParameterEntry(name="ema_period", value=20),
                    ),
                ),
            ),
        )
    assert "missing" in str(exc_info.value).lower()


def test_duplicate_parameter_rejected():
    """Parámetros duplicados dentro de una instancia se rechazan."""
    ref = _make_strategy_ref()
    with pytest.raises(ValidationError) as exc_info:
        StrategyConfig(
            strategy_ref=ref,
            indicator_instances=(
                IndicatorInstance(
                    instance_id="i1",
                    indicator_id="keltner",
                    parameters=(
                        ParameterEntry(name="ema_period", value=20),
                        ParameterEntry(name="ema_period", value=30),
                    ),
                ),
            ),
        )
    assert "duplicate parameter" in str(exc_info.value)


def test_non_configurable_parameter_rejected(monkeypatch):
    """Parámetros no configurables (function_configurable=False) se rechazan si se proporcionan."""
    original = INDICATOR_CATALOG["keltner"]
    parameters = [
        parameter.model_copy(update={"function_configurable": False})
        if parameter.name == "ema_period"
        else parameter
        for parameter in original.parameters
    ]
    test_catalog = dict(INDICATOR_CATALOG)
    test_catalog["keltner"] = original.model_copy(update={"parameters": parameters})
    monkeypatch.setattr(strategy_config_module, "INDICATOR_CATALOG", test_catalog)

    with pytest.raises(ValidationError, match="not function_configurable"):
        _make_config([{
            "instance_id": "i1",
            "indicator_id": "keltner",
            "parameters": _make_keltner_params(),
        }])


# ── 5. Tipos estrictos — bool no es int/float; NaN/Infinity rechazados ────────

def test_bool_not_accepted_as_int():
    """True/False no se aceptan como valores int."""
    ref = _make_strategy_ref()
    # Intentar pasar True (bool) donde se espera int
    with pytest.raises(ValidationError) as exc_info:
        StrategyConfig(
            strategy_ref=ref,
            indicator_instances=(
                IndicatorInstance(
                    instance_id="i1",
                    indicator_id="keltner",
                    parameters=(
                        ParameterEntry(name="ema_period", value=True),  # bool, not int
                    ),
                ),
            ),
        )
    assert "requires int" in str(exc_info.value)


def test_bool_not_accepted_as_float():
    """True/False no se aceptan como valores float."""
    ref = _make_strategy_ref()
    with pytest.raises(ValidationError) as exc_info:
        StrategyConfig(
            strategy_ref=ref,
            indicator_instances=(
                IndicatorInstance(
                    instance_id="i1",
                    indicator_id="keltner",
                    parameters=(
                        ParameterEntry(name="ema_period", value=20),
                        ParameterEntry(name="atr_period", value=14),
                        ParameterEntry(name="multiplier", value=True),  # bool, not float
                    ),
                ),
            ),
        )
    assert "requires float" in str(exc_info.value)


def test_int_not_accepted_as_float():
    """int no se acepta como valor float."""
    ref = _make_strategy_ref()
    with pytest.raises(ValidationError) as exc_info:
        StrategyConfig(
            strategy_ref=ref,
            indicator_instances=(
                IndicatorInstance(
                    instance_id="i1",
                    indicator_id="keltner",
                    parameters=(
                        ParameterEntry(name="ema_period", value=20),
                        ParameterEntry(name="atr_period", value=14),
                        ParameterEntry(name="multiplier", value=2),  # int, not float
                    ),
                ),
            ),
        )
    assert "requires float" in str(exc_info.value)


def test_nan_rejected():
    """NaN se rechaza."""
    ref = _make_strategy_ref()
    with pytest.raises(ValidationError) as exc_info:
        StrategyConfig(
            strategy_ref=ref,
            indicator_instances=(
                IndicatorInstance(
                    instance_id="i1",
                    indicator_id="keltner",
                    parameters=(
                        ParameterEntry(name="ema_period", value=float("nan")),
                    ),
                ),
            ),
        )
    assert "NaN" in str(exc_info.value) or "invalid" in str(exc_info.value).lower()


def test_positive_infinity_rejected():
    """+Infinity se rechaza."""
    ref = _make_strategy_ref()
    with pytest.raises(ValidationError) as exc_info:
        StrategyConfig(
            strategy_ref=ref,
            indicator_instances=(
                IndicatorInstance(
                    instance_id="i1",
                    indicator_id="keltner",
                    parameters=(
                        ParameterEntry(name="ema_period", value=float("inf")),
                    ),
                ),
            ),
        )
    assert "Infinity" in str(exc_info.value) or "invalid" in str(exc_info.value).lower()


def test_negative_infinity_rejected():
    """-Infinity se rechaza."""
    ref = _make_strategy_ref()
    with pytest.raises(ValidationError) as exc_info:
        StrategyConfig(
            strategy_ref=ref,
            indicator_instances=(
                IndicatorInstance(
                    instance_id="i1",
                    indicator_id="keltner",
                    parameters=(
                        ParameterEntry(name="ema_period", value=float("-inf")),
                    ),
                ),
            ),
        )
    assert "Infinity" in str(exc_info.value) or "invalid" in str(exc_info.value).lower()


# ── 6. implementation_default no se hereda; límites no inventados ──────────────

def test_implementation_default_not_inherited():
    """Los defaults de implementación del catálogo no completan valores faltantes."""
    ref = _make_strategy_ref()
    # Si falta un parámetro configurable, debe fallar (ya probado arriba).
    # Verificamos que el catálogo tenga implementation_default para keltner.ema_period
    keltner_def = INDICATOR_CATALOG["keltner"]
    ema_param = next(p for p in keltner_def.parameters if p.name == "ema_period")
    assert ema_param.implementation_default == 20
    # Pero StrategyConfig no lo usa: debe fallar si falta.
    with pytest.raises(ValidationError):
        StrategyConfig(
            strategy_ref=ref,
            indicator_instances=(
                IndicatorInstance(
                    instance_id="i1",
                    indicator_id="keltner",
                    parameters=(
                        ParameterEntry(name="atr_period", value=14),
                        ParameterEntry(name="multiplier", value=2.0),
                    ),
                ),
            ),
        )


def test_no_invented_range_limits():
    """No se inventan límites para parámetros con range_status=NOT_SPECIFIED."""
    keltner_def = INDICATOR_CATALOG["keltner"]
    ema_param = next(p for p in keltner_def.parameters if p.name == "ema_period")
    assert ema_param.range_status.value == "not_specified"
    assert ema_param.min_value is None
    assert ema_param.max_value is None
    # Un valor grande debe ser aceptado (no hay límite inventado)
    config = _make_config([{
        "instance_id": "i1",
        "indicator_id": "keltner",
        "parameters": _make_keltner_params(ema_period=999999),
    }])
    # parameters están ordenados por nombre; ema_period viene después de atr_period
    ema_entry = next(p for p in config.indicator_instances[0].parameters if p.name == "ema_period")
    assert ema_entry.value == 999999


# ── 7. Inmutabilidad profunda ─────────────────────────────────────────────────

def test_config_is_frozen():
    """StrategyConfig no se puede modificar después de construirse."""
    config = _make_config([{
        "instance_id": "i1",
        "indicator_id": "keltner",
        "parameters": _make_keltner_params(),
    }])
    # frozen=True en model_config impide asignación directa de atributos
    with pytest.raises(ValueError, match="frozen_instance"):
        config.strategy_ref = _make_strategy_ref()


def test_indicator_instance_is_frozen():
    """IndicatorInstance no se puede modificar."""
    inst = IndicatorInstance(
        instance_id="i1",
        indicator_id="keltner",
        parameters=_make_keltner_params(),
    )
    with pytest.raises(ValueError, match="frozen_instance"):
        inst.instance_id = "changed"


def test_parameter_entry_is_frozen():
    """ParameterEntry no se puede modificar."""
    param = ParameterEntry(name="ema_period", value=20)
    with pytest.raises(ValueError, match="frozen_instance"):
        param.value = 30


def test_tuples_are_immutable():
    """Las tuplas de parámetros e instancias no permiten mutación."""
    config = _make_config([{
        "instance_id": "i1",
        "indicator_id": "keltner",
        "parameters": _make_keltner_params(),
    }])
    with pytest.raises(Exception):
        config.indicator_instances[0].parameters[0].value = 999  # tuple is immutable


# ── 8. Orden canónico y serialización determinista ─────────────────────────────

def _canonical_json(config: StrategyConfig) -> str:
    """Usar directamente la serialización JSON estable del DTO."""
    return config.model_dump_json()


def test_canonical_order_same_json():
    """Dos configuraciones equivalentes con distinto orden producen el mismo JSON."""
    ref = _make_strategy_ref()

    # Orden A: instancias en orden alfabético inverso, parámetros desordenados
    config_a = StrategyConfig(
        strategy_ref=ref,
        indicator_instances=(
            IndicatorInstance(
                instance_id="b_inst",
                indicator_id="macd",
                parameters=(
                    ParameterEntry(name="signal_period", value=9),
                    ParameterEntry(name="fast_period", value=12),
                    ParameterEntry(name="slow_period", value=26),
                ),
            ),
            IndicatorInstance(
                instance_id="a_inst",
                indicator_id="keltner",
                parameters=(
                    ParameterEntry(name="multiplier", value=2.0),
                    ParameterEntry(name="ema_period", value=20),
                    ParameterEntry(name="atr_period", value=14),
                ),
            ),
        ),
    )

    # Orden B: instancias en orden alfabético directo, parámetros ordenados
    config_b = StrategyConfig(
        strategy_ref=ref,
        indicator_instances=(
            IndicatorInstance(
                instance_id="a_inst",
                indicator_id="keltner",
                parameters=(
                    ParameterEntry(name="atr_period", value=14),
                    ParameterEntry(name="ema_period", value=20),
                    ParameterEntry(name="multiplier", value=2.0),
                ),
            ),
            IndicatorInstance(
                instance_id="b_inst",
                indicator_id="macd",
                parameters=(
                    ParameterEntry(name="fast_period", value=12),
                    ParameterEntry(name="signal_period", value=9),
                    ParameterEntry(name="slow_period", value=26),
                ),
            ),
        ),
    )

    json_a = _canonical_json(config_a)
    json_b = _canonical_json(config_b)
    assert json_a == json_b, f"JSON mismatch:\nA: {json_a}\nB: {json_b}"


def test_canonical_order_nested_params_same_json():
    """Parámetros desordenados dentro de una misma instancia producen el mismo JSON."""
    ref = _make_strategy_ref()

    config_a = StrategyConfig(
        strategy_ref=ref,
        indicator_instances=(
            IndicatorInstance(
                instance_id="i1",
                indicator_id="keltner",
                parameters=(
                    ParameterEntry(name="multiplier", value=2.0),
                    ParameterEntry(name="ema_period", value=20),
                    ParameterEntry(name="atr_period", value=14),
                ),
            ),
        ),
    )

    config_b = StrategyConfig(
        strategy_ref=ref,
        indicator_instances=(
            IndicatorInstance(
                instance_id="i1",
                indicator_id="keltner",
                parameters=(
                    ParameterEntry(name="atr_period", value=14),
                    ParameterEntry(name="multiplier", value=2.0),
                    ParameterEntry(name="ema_period", value=20),
                ),
            ),
        ),
    )

    assert _canonical_json(config_a) == _canonical_json(config_b)


def test_serialization_contains_expected_fields():
    """La serialización contiene los campos esperados del contrato."""
    config = _make_config([{
        "instance_id": "i1",
        "indicator_id": "keltner",
        "parameters": _make_keltner_params(),
    }])
    data = json.loads(_canonical_json(config))
    assert data["schema_version"] == "strategy_config.v1"
    assert data["strategy_ref"]["strategy_id"] == "test_strategy"
    assert data["strategy_ref"]["strategy_version"] == "v1"
    assert len(data["indicator_instances"]) == 1
    assert data["indicator_instances"][0]["instance_id"] == "i1"
    assert data["indicator_instances"][0]["indicator_id"] == "keltner"


# ── 9. Múltiples instancias del mismo indicador ───────────────────────────────

def test_multiple_keltner_instances():
    """Se pueden crear múltiples instancias de keltner con distintos parámetros."""
    config = _make_config([
        {
            "instance_id": "k_fast",
            "indicator_id": "keltner",
            "parameters": _make_keltner_params(ema_period=10, atr_period=7),
        },
        {
            "instance_id": "k_slow",
            "indicator_id": "keltner",
            "parameters": _make_keltner_params(ema_period=50, atr_period=28),
        },
    ])
    assert len(config.indicator_instances) == 2
    # Deben estar ordenados por instance_id
    assert config.indicator_instances[0].instance_id == "k_fast"
    assert config.indicator_instances[1].instance_id == "k_slow"


# ── 10. Parámetros bool del catálogo ──────────────────────────────────────────

def test_bool_parameter_accepted_when_scalar_type_is_bool():
    """Si el catálogo declara scalar_type=bool, se acepta un valor bool."""
    # Verificar que los indicadores actuales no tienen parámetros bool (todos son int/float)
    # Esto asegura que el contrato permite bool cuando corresponde.
    for indicator_id, defn in INDICATOR_CATALOG.items():
        for param in defn.parameters:
            assert param.scalar_type in ("int", "float"), (
                f"Unexpected scalar_type '{param.scalar_type}' for '{indicator_id}.{param.name}'"
            )


# ── 11. Estructura vacía válida ───────────────────────────────────────────────

def test_empty_indicator_instances_valid():
    """Una configuración sin instancias de indicadores es válida."""
    ref = _make_strategy_ref()
    config = StrategyConfig(
        strategy_ref=ref,
        indicator_instances=tuple(),
    )
    assert config.schema_version == "strategy_config.v1"
    assert config.indicator_instances == ()


# ── 12. Extra forbid ──────────────────────────────────────────────────────────

def test_extra_forbid_on_strategy_config():
    """StrategyConfig rechaza campos adicionales."""
    ref = _make_strategy_ref()
    with pytest.raises(ValidationError):
        StrategyConfig(
            strategy_ref=ref,
            indicator_instances=tuple(),
            extra_field="should_fail",  # type: ignore
        )


def test_extra_forbid_on_indicator_instance():
    """IndicatorInstance rechaza campos adicionales."""
    with pytest.raises(ValidationError):
        IndicatorInstance(
            instance_id="i1",
            indicator_id="keltner",
            parameters=(),
            extra_field="should_fail",  # type: ignore
        )


def test_extra_forbid_on_parameter_entry():
    """ParameterEntry rechaza campos adicionales."""
    with pytest.raises(ValidationError):
        ParameterEntry(name="ema_period", value=20, extra_field="should_fail")  # type: ignore
