# Endpoints API para el motor de reglas de trading y análisis con IA.

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

import anyio
from fastapi import APIRouter, HTTPException, Request
from fastapi.routing import APIRoute
from pydantic import BaseModel, Field
from typing import Literal, Optional

from src.bot.models import (
    TradingStrategy,
    TradingSignal,
    AnalysisResult,
    Position,
    Order,
)
from src.bot.rules import RulesEngine, create_default_strategy
from src.ai.ollama_client import OllamaClient, _strict_json
from src.ai.scenario_models import (
    QWEN_MODEL, ScenarioAnalysisRequest, ScenarioAnalysisResult, ScenarioError,
)

@asynccontextmanager
async def _bot_lifespan(_app):
    """Liberar jobs/HTTP desde el router, sin modificar el ciclo de mercado N01."""
    try:
        yield
    finally:
        client = _bot_state["ollama_client"]
        try:
            close = getattr(client, "close", None)
            if callable(close):
                with anyio.CancelScope(shield=True):
                    cleanup = asyncio.create_task(close())
                    try:
                        await asyncio.shield(cleanup)
                    except asyncio.CancelledError:
                        await cleanup
                        raise
        finally:
            if _bot_state["ollama_client"] is client:
                _bot_state["ollama_client"] = None


def _http_error(error: ScenarioError):
    return HTTPException(status_code=error.status_code, detail={
        "code": error.code, "available": False, "requested_model": QWEN_MODEL,
        "effective_model": error.effective_model, "duration_ms": error.duration_ms,
        "executable": False, "execution_available": False,
    })


class _ScenarioRoute(APIRoute):
    """Límites antes del parsing de FastAPI, solamente en las tres rutas IA."""
    def get_route_handler(self):
        handler = super().get_route_handler()

        async def bounded(request: Request):
            body = bytearray()
            async for chunk in request.stream():
                if len(body) + len(chunk) > 32_768:
                    raise _http_error(ScenarioError("request_too_large"))
                body.extend(chunk)
            try:
                parsed = _strict_json(bytes(body))
            except (ValueError, TypeError, UnicodeError, RecursionError):
                raise _http_error(ScenarioError("invalid_snapshot")) from None
            request._body = bytes(body)
            request._json = parsed
            return await handler(request)

        return bounded


router = APIRouter(prefix="/api/bot", tags=["bot"], lifespan=_bot_lifespan)
_ai_router = APIRouter(route_class=_ScenarioRoute)


# Estado global del bot (en producción usar base de datos)
_bot_state = {
    "strategy": create_default_strategy(),
    "engine": None,  # Inicializado lazy
    "positions": [],
    "orders": [],
    "ollama_client": None,  # Inicializado lazy
}


class BotRequest(BaseModel):
    """Solicitud para el bot de trading."""
    symbol: str = Field(..., description="Símbolo a operar")
    strategy_name: Optional[str] = Field(None, description="Nombre de la estrategia")


class BotStatusResponse(BaseModel):
    """Estado actual del bot."""
    is_running: bool
    strategy: TradingStrategy
    positions_count: Optional[int] = None
    orders_count: Optional[int] = None
    ollama_available: Optional[bool] = None
    implementation_status: Literal["not_implemented"] = "not_implemented"
    execution_available: Literal[False] = False
    portfolio_available: Literal[False] = False
    strategy_status: Literal["TEST_ONLY"] = "TEST_ONLY"


@router.get("/status", response_model=BotStatusResponse)
async def get_bot_status():
    """Estado no operativo, sin consultar cuenta ni esperar a Ollama."""
    return BotStatusResponse(
        is_running=False,
        strategy=_bot_state["strategy"],
    )


@router.post("/analyze", response_model=dict)
async def analyze_with_rules(request: BotRequest):
    """Propuesta TEST_ONLY sobre indicadores fijos; nunca crea una orden o un fill."""
    # Inicializar engine lazy
    if _bot_state["engine"] is None:
        _bot_state["engine"] = RulesEngine(_bot_state["strategy"])

    # Simular análisis técnico (en producción usar datos reales)
    analysis = AnalysisResult(
        symbol=request.symbol,
        timeframe="15m",
        keltner_ema=65000.0,  # Simulado
        keltner_upper=67000.0,
        keltner_lower=63000.0,
        macd_line=125.5,
        macd_signal=98.2,
        macd_histogram=27.3,
        pivot_highs=[{"price": 68000.0, "time_ms": 1700000000000}],
        pivot_lows=[{"price": 62000.0, "time_ms": 1700000000000}],
        consolidation_zones=[{
            "id": "zone_1",
            "low": 64000.0,
            "high": 66000.0,
            "contacts": 3,
        }],
    )

    # Evaluar reglas
    signal = _bot_state["engine"].evaluate(analysis)

    return {
        "data_source": "TEST_ONLY",
        "result_type": "proposal",
        "executable": False,
        "execution_available": False,
        "warning": "Indicadores fijos de desarrollo; reglas del método pendientes. No operativo.",
        "signal": {
            **signal.model_dump(), "data_source": "TEST_ONLY",
            "result_type": "proposal", "executable": False,
        },
        "strategy": _bot_state["strategy"].model_dump(),
        "analysis": {**analysis.model_dump(), "data_source": "TEST_ONLY"},
    }


@_ai_router.post("/ai/scenarios", response_model=ScenarioAnalysisResult)
@_ai_router.post("/ai/analyze", response_model=ScenarioAnalysisResult)
async def analyze_with_ai(request: ScenarioAnalysisRequest):
    """N02: snapshot identificado → escenarios advisory, sin motor/reglas/cartera.

    El cuerpo anterior {symbol,timeframe,market_data} se rechaza con 422.
    El cliente valida también vigencia al salir de cola y al entregar la respuesta.
    No se consulta Ollama en /status ni se acopla IA a recepción del mercado.
    """
    if _bot_state["ollama_client"] is None:
        _bot_state["ollama_client"] = OllamaClient(model=QWEN_MODEL)
    try:
        return await _bot_state["ollama_client"].analyze_snapshot(request.snapshot)
    except ScenarioError as error:
        raise _http_error(error) from None


@_ai_router.post("/ai/strategy", response_model=ScenarioAnalysisResult, deprecated=True)
async def generate_strategy_with_ai(request: ScenarioAnalysisRequest):
    """Alias de migración: no genera texto de estrategia ni aplica reglas/riesgo."""
    return await analyze_with_ai(request)


router.include_router(_ai_router)
