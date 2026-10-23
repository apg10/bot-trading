"""FastAPI: ciclo único de datos Spot, PAPER por defecto y ejecución deshabilitada."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager, suppress
from enum import StrEnum
from typing import Any, AsyncGenerator

import anyio
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from src.config import config as app_config
from src.models.state import EngineInfo, EngineState, MarketStatus, Environment
from src.market.binance_client import BinanceConfig
from src.market.engine import MarketEngine
from src.persistence.sqlite_store import SQLiteStore
from .market import router as market_router, set_market_engine
from .analysis import router as analysis_router
from .bot import router as bot_router
from .capabilities import router as capabilities_router
from .paper import router as paper_router
from .preferences import router as preferences_router
from .laboratory import router as laboratory_router


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
    """Un motor de datos público por aplicación; nunca por pestaña/suscripción.

    Ciclo de vida SQLiteStore (ACT-P-001 microtask):
    1. Resolver ``TRADING_DB_PATH`` y crear directorio padre.
    2. Inicializar el store antes del MarketEngine.
    3. Publicar store en ``app.state`` mientras el lifespan esté activo.
    4. Si apertura/migración o startup del motor falla, propagar el error.
    5. En shutdown o startup fallido, cerrar store en cleanup blindado.
    """
    if app_config.is_live or app_config.env == "live":
        raise RuntimeError("LIVE está deshabilitado")
    if getattr(app.state, "market_engine", None) is not None:
        raise RuntimeError("El ciclo de vida del mercado ya está iniciado")

    db_path = app_config.database_path
    store = SQLiteStore(db_path)
    engine: MarketEngine | None = None
    task: asyncio.Task[None] | None = None
    ready: asyncio.Task[bool] | None = None
    try:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        await store.initialize()
        app.state._sqlite_store = store

        # Open/migrate persistence before starting the market engine.
        engine = MarketEngine(
            BinanceConfig(kline_interval=app_config.market_interval),
            freshness_margin_seconds=app_config.market_freshness_margin_seconds,
        )
        app.state.market_engine = engine
        set_market_engine(engine)
        task = asyncio.create_task(engine.start(app_config.market_symbol))
        ready = asyncio.create_task(engine.started.wait())

        done, _ = await asyncio.wait((task, ready), return_when=asyncio.FIRST_COMPLETED)
        if task in done:
            # Preserve the existing startup contract: failed engine startup fails
            # the lifespan, while the finally block still closes SQLite safely.
            task.result()
        # Histórico y conexión no bloquean la apertura de la API: el estado
        # permanecerá no apto hasta terminar bootstrap/recuperación.
        yield
    finally:
        with anyio.CancelScope(shield=True):
            try:
                if ready is not None:
                    ready.cancel()
                    with suppress(asyncio.CancelledError):
                        await ready

                if engine is not None:
                    try:
                        await engine.stop()
                    finally:
                        if task is not None:
                            if not task.done():
                                task.cancel()
                            with suppress(asyncio.CancelledError):
                                await task
            finally:
                set_market_engine(None)
                app.state.market_engine = None
                try:
                    await store.close()
                finally:
                    app.state._sqlite_store = None


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
app.include_router(capabilities_router)
app.include_router(paper_router)
app.include_router(preferences_router)
app.include_router(laboratory_router)


@app.get("/health")
async def health() -> dict[str, Any]:
    """Health check — confirma que el backend está vivo."""
    return {
        "status": "ok",
        "environment": app_config.env,
        "is_live": app_config.is_live,
    }


@app.get("/engine/state")
async def engine_state() -> dict[str, Any]:
    """Estado diagnóstico; el motor de ejecución y la cartera no están implementados."""
    market_engine = getattr(app.state, "market_engine", None)
    market = market_engine.snapshot() if market_engine is not None else None
    transport_status = (
        MarketStatus.CONNECTED if market and market["connected"]
        else MarketStatus.RECONNECTING if market and market["reconnecting"]
        else MarketStatus.DISCONNECTED
    )
    info = EngineInfo(
        state=EngineState.STOPPED,
        environment=Environment(app_config.env),
        market_status=transport_status,
        uptime_seconds=0.0,
    )
    return {
        **info.model_dump(mode="json"),
        "implementation_status": "not_implemented",
        "execution_available": False,
        "portfolio_available": False,
        "market": market,
    }


@app.get("/fixture/data")
async def fixture_data() -> dict[str, Any]:
    """Devuelve datos sintéticos deterministas para desarrollo y pruebas."""
    from src.fixtures.synthetic import get_full_fixture
    return {**get_full_fixture(), "data_source": "TEST_ONLY", "interval": "1m"}


@app.post("/engine/action")
async def engine_action(request: _EngineActionRequest) -> dict[str, Any]:
    """Valida el comando, pero nunca acepta una transición que no puede ejecutar."""
    raise HTTPException(
        status_code=501,
        detail={
            "code": "ENGINE_NOT_IMPLEMENTED",
            "action": request.action.value,
            "executed": False,
            "message": "Los comandos del motor de trading aún no están implementados.",
        },
    )
