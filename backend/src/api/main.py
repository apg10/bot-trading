"""FastAPI application — skeleton para microtask TRADING-FOUNDATION-001 + Fase 1."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any, AsyncGenerator

from fastapi import FastAPI, Body

from src.config import config as app_config
from src.models.state import EngineInfo, EngineState, MarketStatus, Environment
from .market import router as market_router, set_market_engine


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Ciclo de vida: arranque y apagado del motor."""
    # Fase 1: Inicializar motor de mercado (pendiente de conexión real)
    yield
    # Apagado: guardar estado, cerrar conexiones


app = FastAPI(
    title="Trading Bot Terminal",
    version="0.1.0",
    description="Terminal privada con bot de trading e IA local",
    lifespan=lifespan,
)

# Incluir router de mercado
app.include_router(market_router)


@app.get("/health")
async def health() -> dict[str, Any]:
    """Health check — confirma que el backend está vivo."""
    return {
        "status": "ok",
        "environment": app_config.env,
        "is_live": app_config.is_live,
    }


@app.get("/engine/state")
async def engine_state() -> EngineInfo:
    """Devuelve el estado actual del motor de trading."""
    return EngineInfo(
        state=EngineState.STOPPED,
        environment=Environment.PAPER,
        market_status=MarketStatus.DISCONNECTED,
        uptime_seconds=0.0,
    )


@app.get("/fixture/data")
async def fixture_data() -> dict[str, Any]:
    """Devuelve datos sintéticos deterministas para desarrollo y pruebas."""
    from src.fixtures.synthetic import get_full_fixture
    return get_full_fixture()


@app.post("/engine/action")
async def engine_action(action: str = Body(..., embed=True)) -> dict[str, str]:
    """Endpoint placeholder para comandos del motor (pausar, reanudar, detener)."""
    # En fases posteriores se validará el action y actualizará EngineState
    return {"status": "accepted", "action": action}
