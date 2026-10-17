"""StrategyConfig — contrato puro e inmutable para vincular estrategia con indicadores.

Este módulo es un DTO contract-only: no se conecta a API, TradingStrategy, UI ni
runtime; no define persistencia, dataset_ref, implementación de indicador ni una
promesa de replay exacto.
"""

from __future__ import annotations

import math
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictFloat, StrictInt, field_validator, model_validator

from src.indicators.catalog import INDICATOR_CATALOG, IndicatorDefinition, ParameterSpec
from src.models.paper_contracts import StrategyRef


# ── Types ──────────────────────────────────────────────────────────────────────

class ParameterEntry(BaseModel):
    """Un parámetro explícito con nombre y valor estricto."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str = Field(..., min_length=1)
    value: StrictInt | StrictFloat | StrictBool

    @field_validator("value")
    @classmethod
    def _reject_nan_inf(cls, v: Any) -> Any:
        if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
            raise ValueError("NaN and Infinity values are not allowed")
        return v


class IndicatorInstance(BaseModel):
    """Una instancia de un indicador con parámetros explícitos."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    instance_id: str = Field(..., min_length=1)
    indicator_id: str = Field(..., min_length=1)
    parameters: tuple[ParameterEntry, ...] = ()


class StrategyConfig(BaseModel):
    """Configuración inmutable que vincula una estrategia con instancias de indicadores."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["strategy_config.v1"] = Field(
        default="strategy_config.v1", frozen=True
    )
    strategy_ref: StrategyRef
    indicator_instances: tuple[IndicatorInstance, ...] = ()

    @model_validator(mode="after")
    def _validate_canonical_order(self) -> "StrategyConfig":
        """Normalizar orden canónico de instancias y parámetros."""
        # Ordenar instancias por instance_id
        sorted_instances = sorted(
            self.indicator_instances, key=lambda inst: inst.instance_id
        )
        new_instances: list[IndicatorInstance] = []
        for inst in sorted_instances:
            # Ordenar parámetros por nombre dentro de cada instancia
            sorted_params = tuple(
                sorted(inst.parameters, key=lambda p: p.name)
            )
            new_instances.append(
                IndicatorInstance(
                    instance_id=inst.instance_id,
                    indicator_id=inst.indicator_id,
                    parameters=sorted_params,
                )
            )
        # Reemplazar con tupla inmutable
        object.__setattr__(self, "indicator_instances", tuple(new_instances))
        return self

    @model_validator(mode="after")
    def _validate_uniqueness(self) -> "StrategyConfig":
        """instance_id debe ser único y no vacío dentro de la configuración."""
        instance_ids: list[str] = []
        for inst in self.indicator_instances:
            instance_ids.append(inst.instance_id)
        if len(instance_ids) != len(set(instance_ids)):
            raise ValueError("duplicate instance_id within configuration")
        return self

    @model_validator(mode="after")
    def _validate_indicator_catalog(self) -> "StrategyConfig":
        """Validar indicator_id contra el catálogo."""
        for inst in self.indicator_instances:
            if inst.indicator_id not in INDICATOR_CATALOG:
                raise ValueError(
                    f"unknown indicator_id: {inst.indicator_id}"
                )
        return self

    @model_validator(mode="after")
    def _validate_parameters(self) -> "StrategyConfig":
        """Validar nombres, tipos, completitud y rangos de parámetros."""
        for inst in self.indicator_instances:
            catalog_def: IndicatorDefinition = INDICATOR_CATALOG[inst.indicator_id]
            catalog_params: dict[str, ParameterSpec] = {
                p.name: p for p in catalog_def.parameters
            }

            # Construir mapa de parámetros proporcionados (ya ordenados)
            provided: dict[str, ParameterEntry] = {}
            for param in inst.parameters:
                if param.name not in catalog_params:
                    raise ValueError(
                        f"unknown parameter '{param.name}' for indicator '{inst.indicator_id}'"
                    )
                if param.name in provided:
                    raise ValueError(
                        f"duplicate parameter '{param.name}' in instance '{inst.instance_id}'"
                    )
                provided[param.name] = param

            # Validar tipos y valores de cada parámetro proporcionado
            for name, entry in provided.items():
                spec: ParameterSpec = catalog_params[name]

                if not spec.function_configurable:
                    raise ValueError(
                        f"parameter '{name}' is not function_configurable "
                        f"for indicator '{inst.indicator_id}'"
                    )

                # Verificar que el tipo del valor coincide con scalar_type
                # StrictBool en Pydantic v2 ya es bool, pero necesitamos rechazar
                # que un valor int se use donde se espera float y viceversa.
                if spec.scalar_type == "int":
                    # Solo int válido; bool NO es int aquí
                    if type(entry.value) is not int:
                        raise ValueError(
                            f"parameter '{name}' requires int, got {type(entry.value).__name__}"
                        )
                elif spec.scalar_type == "float":
                    # Solo float válido; int y bool no son float
                    if type(entry.value) is not float:
                        raise ValueError(
                            f"parameter '{name}' requires float, got {type(entry.value).__name__}"
                        )
                elif spec.scalar_type == "bool":
                    if type(entry.value) is not bool:
                        raise ValueError(
                            f"parameter '{name}' requires bool, got {type(entry.value).__name__}"
                        )

                # Aplicar min/max solo si range_status es SPECIFIED
                if spec.range_status.value == "specified":
                    if spec.min_value is not None and entry.value < spec.min_value:
                        raise ValueError(
                            f"parameter '{name}' value {entry.value} below min {spec.min_value}"
                        )
                    if spec.max_value is not None and entry.value > spec.max_value:
                        raise ValueError(
                            f"parameter '{name}' value {entry.value} above max {spec.max_value}"
                        )

            # Verificar que todos los function_configurable están presentes
            for name, spec in catalog_params.items():
                if spec.function_configurable and name not in provided:
                    raise ValueError(
                        f"required configurable parameter '{name}' missing "
                        f"for indicator '{inst.indicator_id}' in instance '{inst.instance_id}'"
                    )

        return self


__all__ = ["StrategyConfig", "IndicatorInstance", "ParameterEntry"]
