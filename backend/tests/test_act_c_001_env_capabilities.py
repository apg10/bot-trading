"""Contrato estático de capacidades aprobado en ACT-C-001, slice entorno."""

import pytest
from pydantic import ValidationError

from src.models.state import CapabilitiesContract


def test_capabilities_contract_preserves_current_wire_values():
    contract = CapabilitiesContract(environment="paper")

    assert contract.model_dump() == {
        "schema_version": "backend_capabilities.v1",
        "environment": "paper",
        "runtime_health_checked": False,
        "market_data_supported": True,
        "technical_analysis_supported": True,
        "ai_scenarios_supported": True,
        "original_method_supported": False,
        "execution_available": False,
        "portfolio_available": False,
        "paper_simulation_available": False,
        "exchange_demo_execution_available": False,
        "live_execution_available": False,
    }


@pytest.mark.parametrize("environment", ["paper", "exchange_demo"])
def test_capabilities_contract_accepts_supported_environments(environment):
    assert CapabilitiesContract(environment=environment).environment == environment


@pytest.mark.parametrize("environment", ["live", "other"])
def test_capabilities_contract_rejects_other_environments(environment):
    with pytest.raises(ValidationError):
        CapabilitiesContract(environment=environment)


def test_capabilities_contract_health_checked_is_always_false():
    with pytest.raises(ValidationError):
        CapabilitiesContract(environment="paper", runtime_health_checked=True)


def test_capabilities_contract_all_capability_flags_may_be_false():
    flags = {
        "runtime_health_checked": False,
        "market_data_supported": False,
        "technical_analysis_supported": False,
        "ai_scenarios_supported": False,
        "original_method_supported": False,
        "execution_available": False,
        "portfolio_available": False,
        "paper_simulation_available": False,
        "exchange_demo_execution_available": False,
        "live_execution_available": False,
    }

    contract = CapabilitiesContract(environment="exchange_demo", **flags)

    assert all(value is False for key, value in contract.model_dump().items()
               if key not in {"schema_version", "environment"})


def test_capabilities_contract_schema_version_is_immutable():
    contract = CapabilitiesContract(environment="paper")

    with pytest.raises(ValidationError):
        contract.schema_version = "backend_capabilities.v2"
