"""FastAPI application — skeleton para microtask TRADING-FOUNDATION-001 + Fase 1."""

from __future__ import annotations

from contextlib import asynccontextmanager
from enum import StrEnum
from typing import Any, AsyncGenerator

from fastapi import FastAPI, HTTPException, Body
from pydantic import BaseModel

from src.config import config as app_config
from src.models.state import EngineInfo, EngineState, MarketStatus, Environment
from .market import router as market_router, set_market_engine
from .analysis import router as analysis_router
from .bot import router as bot_router


# ── Enum de acciones válidas ───────────────────────────────────────────────────

class EngineAction(StrEnum):
    """Acciones válidas para el motor de trading."""
    PAUSE = "pause"
    RESUME = "resume"
    STOP = "stop"


class _EngineActionRequest(BaseModel):
    action: EngineAction


# ── Transiciones válidas de estado ─────────────────────────────────────────────

_VALID_TRANSITIONS: dict[EngineState, set[EngineAction]] = {
    EngineState.STOPPED: {EngineAction.RESUME},
    EngineState.PAUSED:  {EngineAction.RESUME, EngineAction.STOP},
    EngineState.RUNNING: {EngineAction.PAUSE, EngineAction.STOP},
}


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

# Incluir routers
app.include_router(market_router)
app.include_router(analysis_router)
app.include_router(bot_router)


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
async def engine_action(request: _EngineActionRequest) -> dict[str, str]:
    """Endpoint para comandos del motor (pausar, reanudar, detener).

    Valida que la acción esté en el enum EngineAction y que sea legal
    según el estado actual del motor. En fases posteriores se ejecutará
    la transición real.
    """
    current_state = EngineState.STOPPED  # Placeholder: leer de MarketEngine real
    allowed = _VALID_TRANSITIONS.get(current_state, set())

    if request.action not in allowed:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Acción '{request.action}' no permitida en estado '{current_state}'. "
                f"Acciones válidas: {', '.join(sorted(a.value for a in allowed)) or 'ninguna'}."
            ),
        )

    # Placeholder: ejecutar la transición real cuando el motor esté disponible
    return {"status": "accepted", "action": request.action}
