"""Endpoints de mercado: historial, estado y WebSocket multiplexado."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, AsyncGenerator, Literal

import anyio
from fastapi import APIRouter, HTTPException, Query, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field

from src.market.binance_client import BinanceConfig, _normalize_symbol
from src.market.candle_builder import CandleBuilder, BarEvent, BarEventType
from src.models.state import Environment

logger = logging.getLogger(__name__)
MARKET_TRANSPORT_VERSION = "market_transport.v1"


# ── Schemas de request/response ───────────────────────────────────────────────

class StartMarketRequest(BaseModel):
    symbol: str = Field(..., min_length=1, max_length=20)
    interval: str = "1m"
    environment: Environment = Environment.PAPER


class MarketStatusResponse(BaseModel):
    schema_version: Literal["market_transport.v1"] = MARKET_TRANSPORT_VERSION
    stream_id: str
    sequence: int
    restart_count: int = 0
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

    schema_version: Literal["market_transport.v1"] = MARKET_TRANSPORT_VERSION
    stream_id: str
    sequence: int
    restart_count: int = 0
    symbol: str
    interval: str
    data_source: Literal["TEST_ONLY", "market_engine"]
    candles: list[CandleData]
    complete: bool


# ── Router ────────────────────────────────────────────────────────────────────

router = APIRouter(prefix="/api/market", tags=["mercado"])

# Estado global del motor (se inicializa al arranque de la app)
_market_engine: Any = None


def set_market_engine(engine: Any):
    """Set the market engine instance (called from main.py lifespan)."""
    global _market_engine
    _market_engine = engine


def _validate_symbol(symbol: str) -> None:
    """Validate symbol syntax using Spot rules; raise 422 on invalid input."""
    try:
        _normalize_symbol(symbol)
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid symbol syntax")


def _engine_for_symbol(symbol: str) -> Any:
    state = getattr(_market_engine, "state", None)
    return _market_engine if state is not None and state.symbol == symbol else None


def _engine_metadata(engine: Any) -> dict[str, Any]:
    """Identifica el dueño del buffer; no certifica mercado fresco ni ejecución."""
    return {
        "schema_version": MARKET_TRANSPORT_VERSION,
        "stream_id": str(getattr(engine, "stream_id", "unavailable")),
        "sequence": int(getattr(engine, "sequence", 0)),
        "restart_count": int(getattr(engine, "restart_count", 0)),
        "symbol": engine.state.symbol,
        "interval": getattr(getattr(engine, "config", None), "kline_interval", "unknown"),
        "data_source": "market_engine",
    }


def _transport_metadata(engine: Any) -> dict[str, Any]:
    """Transport identity for WS messages, including an unavailable engine."""
    if engine is not None and getattr(engine, "state", None) is not None:
        return _engine_metadata(engine)
    return {
        "schema_version": MARKET_TRANSPORT_VERSION,
        "stream_id": "unavailable",
        "sequence": 0,
        "restart_count": 0,
    }


def _interval_ms(interval: str) -> int:
    fixed = {
        "1s": 1_000, "1m": 60_000, "3m": 180_000, "5m": 300_000,
        "15m": 900_000, "30m": 1_800_000, "1h": 3_600_000,
        "2h": 7_200_000, "4h": 14_400_000, "6h": 21_600_000,
        "8h": 28_800_000, "12h": 43_200_000, "1d": 86_400_000,
        "3d": 259_200_000, "1w": 604_800_000,
    }
    return fixed.get(interval, 60_000)


def _validate_range(start_time: int | None, end_time: int | None, interval_ms: int) -> None:
    if start_time is not None and end_time is not None and start_time >= end_time:
        raise HTTPException(status_code=422, detail="start_time must be before end_time")


def _range_complete(
    candles: list[dict[str, Any]], start_time: int | None, end_time: int | None,
    interval_ms: int, engine: Any,
) -> bool:
    if start_time is not None and end_time is not None:
        first_aligned = ((start_time + interval_ms - 1) // interval_ms) * interval_ms
        expected = list(range(first_aligned, end_time, interval_ms))
        actual = [c["open_time"] for c in candles]
        return actual == expected
    if engine is None:
        return True
    return bool(getattr(getattr(engine, "state", None), "complete", False))


@router.get("/status", response_model=MarketStatusResponse)
async def get_market_status(symbol: str = "BTC/USDT") -> MarketStatusResponse:
    """Devuelve el estado actual del mercado para un símbolo."""
    _validate_symbol(symbol)
    engine = _engine_for_symbol(symbol)
    if engine is None:
        return MarketStatusResponse(
            schema_version=MARKET_TRANSPORT_VERSION,
            stream_id="unavailable",
            sequence=0,
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
    start_time: int | None = Query(None),
    end_time: int | None = Query(None),
):
    """Devuelve un histórico identificado; el fallback sin rango es TEST_ONLY/1m."""
    _validate_symbol(symbol)
    candles = []
    engine = _engine_for_symbol(symbol)
    metadata = {"symbol": symbol, "interval": "1m", "data_source": "TEST_ONLY"}
    interval_ms = _interval_ms("1m")

    if engine is not None and hasattr(engine, "closed_candles"):
        all_candles = engine.closed_candles
        interval = getattr(getattr(engine, "config", None), "kline_interval", "1m")
        interval_ms = _interval_ms(interval)
        candles = all_candles if all_candles else []
        if candles:
            metadata = _engine_metadata(engine)

    has_range = start_time is not None or end_time is not None
    engine_has_data = bool(candles)
    # A ranged request must never be filled with synthetic data.  Keep the
    # engine identity when it exists, even while its buffer is empty.
    if has_range and engine is not None:
        metadata = _engine_metadata(engine)

    # Sin rango, conservar el fallback explícitamente TEST_ONLY para los
    # consumidores legacy.  Una petición con rango sin datos queda vacía.
    if not candles and not has_range:
        from src.fixtures.synthetic import generate_candles
        # Generate the full transport-sized fixture before applying range and
        # limit, so a range is not accidentally truncated by the default limit.
        raw = generate_candles(500)
        candles = [
            {
                "open_time": c.timestamp_ms,
                "close_time": c.timestamp_ms + 59_999,
                "open": c.open,
                "high": c.high,
                "low": c.low,
                "close": c.close,
                "volume": c.volume,
                "is_closed": True,
            }
            for c in raw
        ]

    _validate_range(start_time, end_time, interval_ms)
    candles = [c for c in candles if (start_time is None or c["open_time"] >= start_time)
               and (end_time is None or c["open_time"] < end_time)]
    candles = candles[-limit:]
    complete = False if has_range and not engine_has_data else _range_complete(
        candles, start_time, end_time, interval_ms,
        engine if metadata["data_source"] == "market_engine" else None,
    )
    if "schema_version" not in metadata:
        metadata = {
            **metadata,
            "schema_version": MARKET_TRANSPORT_VERSION,
            "stream_id": "unavailable",
            "sequence": 0,
            "restart_count": 0,
        }
    return CandleHistoryResponse(
        **metadata, candles=[CandleData(**c) for c in candles], complete=complete,
    )


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
        if overflow.is_set():
            return
        if outgoing.full():
            # Mark the connection unusable before the resync signal can be
            # blocked behind a slow consumer.  The signal is the sole item
            # retained for delivery after overflow.
            overflow.set()
            while not outgoing.empty():
                outgoing.get_nowait()
                outgoing.task_done()
            metadata = _transport_metadata(engine)
            outgoing.put_nowait({
                "type": "resync_required",
                **metadata,
                "reason": "overflow",
                "last_sequence": metadata["sequence"],
                "recovery_required": True,
            })
        elif not overflow.is_set():
            outgoing.put_nowait(message)

    async def _on_bar_closed(candle: dict[str, Any]):
        metadata = _transport_metadata(engine)
        metadata["sequence"] = int(candle.get("sequence", metadata["sequence"]))
        msg = {"type": "bar_closed", **metadata, "candle": candle}
        if "candles" in subscriptions:
            enqueue(msg)

    async def _on_bar_updated(candle: dict[str, Any]):
        metadata = _transport_metadata(engine)
        metadata["sequence"] = int(candle.get("sequence", metadata["sequence"]))
        msg = {"type": "bar_updated", **metadata, "candle": candle}
        if "updates" in subscriptions:
            enqueue(msg)

    async def _on_gap(event: BarEvent):
        metadata = _transport_metadata(engine)
        sequence = int(getattr(event, "sequence", metadata["sequence"]))
        msg = {
            "type": "gap",
            **metadata,
            "sequence": sequence,
            "gap_ms": event.gap_ms,
            "previous_close_time": event.previous_close_time,
            "gap_start_time": event.gap_start_time,
            "gap_end_time": event.gap_end_time,
        }
        if "gaps" in subscriptions:
            enqueue(msg)
        enqueue({
            "type": "resync_required",
            **metadata,
            "sequence": sequence,
            "reason": "gap",
            "last_sequence": sequence,
            "recovery_required": True,
        })

    async def _on_state_change(state_data: dict[str, Any]):
        metadata = _transport_metadata(engine)
        msg = {"type": "status", **metadata, **state_data}
        if state_data.get("resync_required"):
            enqueue({
                "type": "resync_required",
                **metadata,
                "sequence": int(state_data.get("sequence", metadata["sequence"])),
                "reason": state_data.get("reason", "overflow"),
                "last_sequence": int(state_data.get("last_sequence", metadata["sequence"])),
                "recovery_required": True,
            })
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
                    enqueue({"type": "error", **_transport_metadata(engine), "error": "unknown_channel"})
                    continue
                subscriptions.add(channel)
                enqueue({
                    "type": "subscribed",
                    **_transport_metadata(engine),
                    "channel": channel,
                })
                if channel == "candles":
                    current = list(getattr(engine, "closed_candles", [])) if engine else []
                    enqueue({
                        "type": "snapshot",
                        **_transport_metadata(engine),
                        "candles": current[-500:],
                    })

            elif msg_type == "unsubscribe":
                channel = msg.get("channel", "")
                subscriptions.discard(channel)

            elif msg_type == "ping":
                enqueue({"type": "pong", **_transport_metadata(engine)})

    async def send():
        while True:
            message = await outgoing.get()
            try:
                if overflow.is_set() and not (
                    message.get("type") == "resync_required"
                    and message.get("reason") == "overflow"
                ):
                    continue
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
