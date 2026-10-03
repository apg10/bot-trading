# Endpoints API para el motor de reglas de trading y análisis con IA.

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from typing import Optional

from src.bot.models import (
    TradingStrategy,
    TradingSignal,
    AnalysisResult,
    Position,
    Order,
)
from src.bot.rules import RulesEngine, create_default_strategy
from src.ai.ollama_client import OllamaClient, OllamaMessage, OllamaResponse

router = APIRouter(prefix="/api/bot", tags=["bot"])


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
    positions_count: int
    orders_count: int
    ollama_available: Optional[bool] = None


class AIRequest(BaseModel):
    """Solicitud para análisis con IA."""
    symbol: str = Field(..., description="Símbolo a analizar")
    timeframe: str = Field("15m", description="Temporalidad")
    market_data: dict = Field(..., description="Datos de mercado para análisis")


class AIAnalysisResponse(BaseModel):
    """Respuesta del análisis con IA."""
    analysis: str
    model_used: str
    duration_ms: Optional[int] = None
    available: bool


@router.get("/status", response_model=BotStatusResponse)
async def get_bot_status():
    """Obtiene el estado actual del bot de trading."""
    # Inicializar engine lazy
    if _bot_state["engine"] is None:
        _bot_state["engine"] = RulesEngine(_bot_state["strategy"])

    # Verificar disponibilidad de Ollama
    ollama_available = None
    if _bot_state["ollama_client"]:
        ollama_available = await _bot_state["ollama_client"].health_check()

    return BotStatusResponse(
        is_running=True,  # Simplificado
        strategy=_bot_state["strategy"],
        positions_count=len(_bot_state["positions"]),
        orders_count=len(_bot_state["orders"]),
        ollama_available=ollama_available,
    )


@router.post("/analyze", response_model=dict)
async def analyze_with_rules(request: BotRequest):
    """Evalúa las reglas de trading para un símbolo dado."""
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
        "signal": signal.model_dump(),
        "strategy": _bot_state["strategy"].model_dump(),
        "analysis": analysis.model_dump(),
    }


@router.post("/ai/analyze", response_model=AIAnalysisResponse)
async def analyze_with_ai(request: AIRequest):
    """Analiza el mercado con IA (Ollama)."""
    # Inicializar cliente Ollama lazy
    if _bot_state["ollama_client"] is None:
        _bot_state["ollama_client"] = OllamaClient()

    # Verificar disponibilidad de Ollama
    available = await _bot_state["ollama_client"].health_check()
    if not available:
        return AIAnalysisResponse(
            analysis="⚠️ Ollama no está disponible. Asegúrate de tener Ollama corriendo en localhost:11434.",
            model_used=_bot_state["ollama_client"].model,
            available=False,
        )

    try:
        # Analizar con IA
        analysis_text = await _bot_state["ollama_client"].analyze_market(request.market_data)

        return AIAnalysisResponse(
            analysis=analysis_text,
            model_used=_bot_state["ollama_client"].model,
            available=True,
        )
    except Exception as e:
        return AIAnalysisResponse(
            analysis=f"Error en el análisis con IA: {str(e)}",
            model_used=_bot_state["ollama_client"].model,
            available=False,
        )


@router.post("/ai/strategy", response_model=AIAnalysisResponse)
async def generate_strategy_with_ai(request: AIRequest):
    """Genera una estrategia de trading con IA."""
    # Inicializar cliente Ollama lazy
    if _bot_state["ollama_client"] is None:
        _bot_state["ollama_client"] = OllamaClient()

    available = await _bot_state["ollama_client"].health_check()
    if not available:
        return AIAnalysisResponse(
            analysis="⚠️ Ollama no está disponible.",
            model_used=_bot_state["ollama_client"].model,
            available=False,
        )

    try:
        strategy_text = await _bot_state["ollama_client"].generate_strategy(
            json.dumps(request.market_data, indent=2)
        )

        return AIAnalysisResponse(
            analysis=strategy_text,
            model_used=_bot_state["ollama_client"].model,
            available=True,
        )
    except Exception as e:
        return AIAnalysisResponse(
            analysis=f"Error al generar estrategia: {str(e)}",
            model_used=_bot_state["ollama_client"].model,
            available=False,
        )
