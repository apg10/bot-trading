"""GET /api/capabilities — inventario informativo de capacidades del backend.

Este endpoint NO activa ningun flag financiero, NO llama a servicios externos,
NO instancia clientes ni motores, y NO ejecuta diagnosticos.  Solo devuelve
metadatos estaticos derivados de la configuracion actual.
"""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict

from src.config import config as app_config

router = APIRouter()


class CapabilitiesResponse(BaseModel):
    """Respuesta exacta del endpoint de capacidades."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
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
        }
    )

    schema_version: str = "backend_capabilities.v1"
    environment: str
    runtime_health_checked: bool = False
    market_data_supported: bool = True
    technical_analysis_supported: bool = True
    ai_scenarios_supported: bool = True
    original_method_supported: bool = False
    execution_available: bool = False
    portfolio_available: bool = False
    paper_simulation_available: bool = False
    exchange_demo_execution_available: bool = False
    live_execution_available: bool = False


@router.get("/api/capabilities")
async def get_capabilities() -> CapabilitiesResponse:
    """Inventario informativo de capacidades.

    - `environment` proviene de `config.env` (paper | exchange_demo).
    - `runtime_health_checked` es siempre False: este endpoint no diagnostica.
    - Los campos `*_supported` indican si el codigo/API existe, no si esta
      conectado, fresco ni apto para operar.
    - Ejecucion, cartera y simulador son False porque no estan implementados.
    """
    return CapabilitiesResponse(environment=app_config.env)
