"""Endpoints de mercado: historial, estado y WebSocket multiplexado."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, AsyncGenerator

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field

from src.market.binance_client import BinanceConfig
from src.market.candle_builder import CandleBuilder, BarEvent, BarEventType
from src.models.state import Environment

logger = logging.getLogger(__name__)


# ── Schemas de request/response ───────────────────────────────────────────────

class StartMarketRequest(BaseModel):
    symbol: str = Field(..., min_length=1, max_length=20)
    interval: str = "1m"
    environment: Environment = Environment.PAPER


class MarketStatusResponse(BaseModel):
    symbol: str
    connected: bool
    reconnecting: bool
    closed_candles_count: int
    last_bar_open_time: int | None = None


class CandleData(BaseModel):
    open_time: int
    close_time: int
    open: float
    high: float
    low: float
    close: float
    volume: float
    is_closed: bool


# ── Router ────────────────────────────────────────────────────────────────────

router = APIRouter(prefix="/market", tags=["mercado"])

# Estado global del motor (se inicializa al arranque de la app)
_market_engine: Any = None


def set_market_engine(engine: Any):
    """Set the market engine instance (called from main.py lifespan)."""
    global _market_engine
    _market_engine = engine


@router.get("/status", response_model=MarketStatusResponse)
async def get_market_status(symbol: str = "BTC/USDT") -> MarketStatusResponse:
    """Devuelve el estado actual del mercado para un símbolo."""
    if not _market_engine or not _market_engine.state:
        return MarketStatusResponse(
            symbol=symbol,
            connected=False,
            reconnecting=False,
            closed_candles_count=0,
        )

    s = _market_engine.state
    return MarketStatusResponse(
        symbol=s.symbol,
        connected=s.connected,
        reconnecting=s.reconnecting,
        closed_candles_count=s.closed_candles_count,
        last_bar_open_time=s.last_bar_open_time,
    )


@router.get("/candles", response_model=list[CandleData])
async def get_candles(
    symbol: str = "BTC/USDT",
    limit: int = 100,
):
    """Devuelve las últimas N barras cerradas del motor (o datos de prueba)."""
    candles = []

    if _market_engine and hasattr(_market_engine, "closed_candles"):
        all_candles = _market_engine.closed_candles
        candles = all_candles[-limit:] if all_candles else []

    # Si no hay datos reales, devolver fixture sintética
    if not candles:
        from src.fixtures.synthetic import generate_candles
        raw = generate_candles(limit)
        candles = [
            {
                "open_time": c.timestamp_ms,
                "close_time": c.timestamp_ms + 60_000,
                "open": c.open,
                "high": c.high,
                "low": c.low,
                "close": c.close,
                "volume": c.volume,
                "is_closed": True,
            }
            for c in raw
        ]

    return [CandleData(**c) for c in candles]


@router.websocket("/ws")
async def market_websocket(websocket: WebSocket):
    """WebSocket multiplexado para eventos de mercado en tiempo real.

    Protocolo (mensajes JSON con campo "type"):

    Suscripciones:
      - {"type": "subscribe", "channel": "candles"} — recibir barras cerradas
      - {"type": "subscribe", "channel": "updates"}  — recibir actualizaciones de barra en formación
      - {"type": "subscribe", "channel": "status"}   — recibir cambios de estado
      - {"type": "subscribe", "channel": "gaps"}     — recibir eventos de hueco

    Publicación:
      - {"type": "bar_closed", "candle": {...}}
      - {"type": "bar_updated", "candle": {...}}
      - {"type": "status", ...}
      - {"type": "gap", "gap_ms": N, ...}
    """
    await websocket.accept()
    subscribers: dict[str, set] = {
        "candles": set(),
        "updates": set(),
        "status": set(),
        "gaps": set(),
    }

    async def _on_bar_closed(candle: dict[str, Any]):
        msg = {"type": "bar_closed", "candle": candle}
        for ws in list(subscribers["candles"]):
            try:
                await ws.send_json(msg)
            except Exception:
                pass

    async def _on_bar_updated(candle: dict[str, Any]):
        msg = {"type": "bar_updated", "candle": candle}
        for ws in list(subscribers["updates"]):
            try:
                await ws.send_json(msg)
            except Exception:
                pass

    async def _on_gap(event: BarEvent):
        msg = {
            "type": "gap",
            "gap_ms": event.gap_ms,
            "previous_close_time": event.previous_close_time,
        }
        for ws in list(subscribers["gaps"]):
            try:
                await ws.send_json(msg)
            except Exception:
                pass

    async def _on_state_change(state_data: dict[str, Any]):
        msg = {"type": "status", **state_data}
        for ws in list(subscribers["status"]):
            try:
                await ws.send_json(msg)
            except Exception:
                pass

    # Suscribirse al motor
    if _market_engine:
        _market_engine.on_bar_closed(_on_bar_closed)
        _market_engine.on_bar_updated(_on_bar_updated)
        _market_engine.on_gap(_on_gap)
        _market_engine.on_state_change(_on_state_change)

    try:
        while True:
            data = await websocket.receive_text()
            msg = json.loads(data)
            msg_type = msg.get("type")

            if msg_type == "subscribe":
                channel = msg.get("channel", "")
                subscribers[channel].add(websocket)
                await websocket.send_json({
                    "type": "subscribed",
                    "channel": channel,
                })

            elif msg_type == "unsubscribe":
                channel = msg.get("channel", "")
                subscribers[channel].discard(websocket)

            elif msg_type == "ping":
                await websocket.send_json({"type": "pong"})

    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.error("WebSocket error: %s", e)
    finally:
        # Limpiar suscripciones
        for ch in subscribers:
            subscribers[ch].discard(websocket)
