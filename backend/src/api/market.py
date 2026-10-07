"""Endpoints de mercado: historial, estado y WebSocket multiplexado."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, AsyncGenerator, Literal

import anyio
from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect
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
    interval: str | None = None
    data_source: Literal["market_engine", "unavailable"] = "unavailable"
    recovering: bool = False
    history_loaded: bool = False
    complete: bool = False
    stale: bool = True
    entries_allowed: bool = Field(False, description="Aptitud de datos; no autoriza ejecución")
    pending_gaps: int = 0
    last_closed_close_time: int | None = None
    max_candle_age_ms: int | None = None
    phase: str = "disconnected"
    execution_available: Literal[False] = False


class CandleData(BaseModel):
    open_time: int
    close_time: int
    open: float
    high: float
    low: float
    close: float
    volume: float
    is_closed: bool


class CandleHistoryResponse(BaseModel):
    """Procedencia del histórico, sin implicar frescura ni autorización para operar."""

    symbol: str
    interval: str
    data_source: Literal["TEST_ONLY", "market_engine"]
    candles: list[CandleData]


# ── Router ────────────────────────────────────────────────────────────────────

router = APIRouter(prefix="/api/market", tags=["mercado"])

# Estado global del motor (se inicializa al arranque de la app)
_market_engine: Any = None


def set_market_engine(engine: Any):
    """Set the market engine instance (called from main.py lifespan)."""
    global _market_engine
    _market_engine = engine


def _engine_for_symbol(symbol: str) -> Any:
    state = getattr(_market_engine, "state", None)
    return _market_engine if state is not None and state.symbol == symbol else None


def _engine_metadata(engine: Any) -> dict[str, Any]:
    """Identifica el dueño del buffer; no certifica mercado fresco ni ejecución."""
    return {
        "symbol": engine.state.symbol,
        "interval": getattr(getattr(engine, "config", None), "kline_interval", "unknown"),
        "data_source": "market_engine",
    }


@router.get("/status", response_model=MarketStatusResponse)
async def get_market_status(symbol: str = "BTC/USDT") -> MarketStatusResponse:
    """Devuelve el estado actual del mercado para un símbolo."""
    engine = _engine_for_symbol(symbol)
    if engine is None:
        return MarketStatusResponse(
            symbol=symbol,
            connected=False,
            reconnecting=False,
            closed_candles_count=0,
        )

    if hasattr(engine, "snapshot"):
        return MarketStatusResponse(**{**engine.snapshot(), **_engine_metadata(engine)})
    # Compatibilidad con adaptadores antiguos: nunca certificar su aptitud.
    s = engine.state
    return MarketStatusResponse(symbol=s.symbol, connected=s.connected,
                                reconnecting=s.reconnecting,
                                closed_candles_count=s.closed_candles_count,
                                last_bar_open_time=s.last_bar_open_time,
                                **{k: v for k, v in _engine_metadata(engine).items() if k != "symbol"})


@router.get("/candles", response_model=CandleHistoryResponse)
async def get_candles(
    symbol: str = "BTC/USDT",
    limit: int = Query(100, ge=1, le=500),
):
    """Devuelve un histórico identificado; el fallback siempre es TEST_ONLY/1m."""
    candles = []
    engine = _engine_for_symbol(symbol)
    metadata = {"symbol": symbol, "interval": "1m", "data_source": "TEST_ONLY"}

    if engine is not None and hasattr(engine, "closed_candles"):
        all_candles = engine.closed_candles
        candles = all_candles[-limit:] if all_candles else []
        if candles:
            metadata = _engine_metadata(engine)

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

    return CandleHistoryResponse(**metadata, candles=[CandleData(**c) for c in candles])


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
    engine = _market_engine
    await websocket.accept()
    valid_channels = {"candles", "updates", "status", "gaps"}
    subscriptions: set[str] = set()
    # Un único escritor por pestaña. Saturación obliga a releer REST; no se
    # descartan velas silenciosamente ni se frena la recepción de Binance.
    outgoing: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=128)
    overflow = asyncio.Event()
    removers = []

    def enqueue(message: dict[str, Any]) -> None:
        if outgoing.full():
            overflow.set()
        elif not overflow.is_set():
            outgoing.put_nowait(message)

    async def _on_bar_closed(candle: dict[str, Any]):
        msg = {"type": "bar_closed", **_engine_metadata(engine), "candle": candle}
        if "candles" in subscriptions:
            enqueue(msg)

    async def _on_bar_updated(candle: dict[str, Any]):
        msg = {"type": "bar_updated", **_engine_metadata(engine), "candle": candle}
        if "updates" in subscriptions:
            enqueue(msg)

    async def _on_gap(event: BarEvent):
        msg = {
            "type": "gap",
            **_engine_metadata(engine),
            "gap_ms": event.gap_ms,
            "previous_close_time": event.previous_close_time,
            "gap_start_time": event.gap_start_time,
            "gap_end_time": event.gap_end_time,
        }
        if "gaps" in subscriptions:
            enqueue(msg)

    async def _on_state_change(state_data: dict[str, Any]):
        msg = {"type": "status", **_engine_metadata(engine), **state_data}
        if state_data.get("resync_required"):
            overflow.set()
        elif "status" in subscriptions:
            enqueue(msg)

    # Suscribirse al motor
    async def receive():
        while True:
            data = await websocket.receive_text()
            msg = json.loads(data)
            msg_type = msg.get("type")

            if msg_type == "subscribe":
                channel = msg.get("channel", "")
                if channel not in valid_channels:
                    enqueue({"type": "error", "error": "unknown_channel"})
                    continue
                subscriptions.add(channel)
                enqueue({
                    "type": "subscribed",
                    "channel": channel,
                })

            elif msg_type == "unsubscribe":
                channel = msg.get("channel", "")
                subscriptions.discard(channel)

            elif msg_type == "ping":
                enqueue({"type": "pong"})

    async def send():
        while True:
            message = await outgoing.get()
            try:
                await websocket.send_json(message)
            finally:
                outgoing.task_done()

    tasks = []
    close_for_resync = False
    try:
        if engine:
            for register, remove, callback in (
                ("on_bar_closed", "off_bar_closed", _on_bar_closed),
                ("on_bar_updated", "off_bar_updated", _on_bar_updated),
                ("on_gap", "off_gap", _on_gap),
                ("on_state_change", "off_state_change", _on_state_change),
            ):
                unregister = getattr(engine, register)(callback)
                if callable(unregister):
                    removers.append(unregister)
                elif hasattr(engine, remove):
                    removers.append(lambda name=remove, cb=callback: getattr(engine, name)(cb))
        tasks = [asyncio.create_task(receive()), asyncio.create_task(send()),
                 asyncio.create_task(overflow.wait())]
        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            task.result()
        close_for_resync = overflow.is_set()

    except WebSocketDisconnect:
        pass
    except asyncio.CancelledError:
        # ASGI/TestClient pueden cancelar la sesión al desconectar el navegador.
        # La sesión está terminando: recoger los hijos antes de devolver control.
        pass
    except Exception as e:
        logger.warning("WebSocket de mercado terminado (%s)", type(e).__name__)
    finally:
        with anyio.CancelScope(shield=True):
            subscriptions.clear()
            for unregister in removers:
                unregister()
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
    if close_for_resync:
        # Limpiar antes del cierre y no escribir en paralelo con send_json.
        # El límite protege el apagado; no cambia el umbral de frescura acordado.
        try:
            await asyncio.wait_for(
                websocket.close(code=1013, reason="Resincronizar histórico REST"), timeout=1.0,
            )
        except (asyncio.TimeoutError, WebSocketDisconnect, RuntimeError):
            logger.warning("Cierre de consumidor saturado no completado")
