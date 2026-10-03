"""MarketEngine — orquesta la conexión a Binance, candle builder y eventos."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, Optional

from .binance_client import BinanceHTTPClient, BinanceWSClient, BinanceConfig
from .candle_builder import CandleBuilder, BarEvent, BarEventType

logger = logging.getLogger(__name__)


@dataclass
class MarketState:
    """Estado actual del mercado."""

    symbol: str
    connected: bool = False
    reconnecting: bool = False
    closed_candles_count: int = 0
    last_bar_open_time: Optional[int] = None
    current_bar_closed: Optional[dict[str, Any]] = None


class MarketEngine:
    """Motor que orquesta Binance HTTP + WS + CandleBuilder.

    Responsabilidades:
    - Iniciar/detener conexión a Binance (HTTP para histórico, WS para streaming)
    - Gestionar reconexión con backoff
    - Orquestar eventos del builder y propagarlos
    - Mantener estado actualizado
    """

    def __init__(self, config: BinanceConfig):
        self.config = config
        # Separar HTTP (para histórico) y WS (para streaming en vivo).
        self._http_client: Optional[BinanceHTTPClient] = None
        self._ws_client: Optional[BinanceWSClient] = None
        self._builder: Optional[CandleBuilder] = None
        self._state = MarketState(symbol="")
        self._running = False

        # Handlers de eventos del motor
        self._on_bar_closed: list[Any] = []
        self._on_bar_updated: list[Any] = []
        self._on_gap: list[Any] = []
        self._on_state_change: list[Any] = []

    def on_bar_closed(self, handler) -> None:
        self._on_bar_closed.append(handler)

    def on_bar_updated(self, handler) -> None:
        self._on_bar_updated.append(handler)

    def on_gap(self, handler) -> None:
        self._on_gap.append(handler)

    def on_state_change(self, handler) -> None:
        self._on_state_change.append(handler)

    async def start(self, symbol: str):
        """Inicia el motor para un símbolo dado.

        1. Crea cliente HTTP (para histórico).
        2. Crea cliente WS y conecta streaming a Binance.
        3. Conecta builder al client WS para recibir eventos.
        """
        if self._running:
            await self.stop()

        self._state = MarketState(symbol=symbol)
        self._running = True

        # ── Cliente HTTP (solo para consultas de histórico, no streaming) ──
        self._http_client = BinanceHTTPClient(self.config)

        # ── Cliente WS (streaming en vivo) ──
        self._ws_client = BinanceWSClient(self.config)

        # Conectar builder al client WS para recibir mensajes normalizados
        self._builder = CandleBuilder(symbol, interval_ms=60_000)

        def _on_ws_message(msg: dict[str, Any]) -> None:
            """Middleware: convierte mensaje WS en kline y lo pasa al builder."""
            if "k" in msg:
                kline = msg["k"]
                normalized = {
                    "symbol": symbol,
                    "open_time": kline["t"],
                    "close_time": kline["T"],
                    "open": float(kline["o"]),
                    "high": float(kline["h"]),
                    "low": float(kline["l"]),
                    "close": float(kline["c"]),
                    "volume": float(kline["v"]),
                    "is_closed": kline["x"],
                }
                self._builder.process_kline(normalized)  # type: ignore[arg-type]

        self._ws_client.on_message(_on_ws_message)

        # Iniciar streaming WebSocket (maneja reconexión interna)
        await self._ws_client.start(symbol)
        self._state.connected = True
        self._emit_state_change()

    async def stop(self):
        """Detiene el motor y libera recursos."""
        self._running = False
        if self._ws_client:
            await self._ws_client.stop()
        if self._http_client:
            await self._http_client.close()
        self._state.connected = False
        self._state.reconnecting = False
        self._emit_state_change()

    async def _handle_bar_event(self, event: BarEvent):
        """Maneja eventos del CandleBuilder."""
        if event.event_type == BarEventType.BAR_CLOSED and event.candle:
            self._state.current_bar_closed = event.candle
            self._state.closed_candles_count += 1
            self._state.last_bar_open_time = event.candle["open_time"]
            for h in self._on_bar_closed:
                await h(event.candle)

        elif event.event_type == BarEventType.BAR_UPDATED and event.candle:
            for h in self._on_bar_updated:
                await h(event.candle)

        elif event.event_type == BarEventType.GAP_DETECTED:
            for h in self._on_gap:
                await h(event)

    def _emit_state_change(self):
        """Propaga cambio de estado."""
        state_snapshot = {
            "symbol": self._state.symbol,
            "connected": self._state.connected,
            "reconnecting": self._state.reconnecting,
            "closed_candles_count": self._state.closed_candles_count,
            "last_bar_open_time": self._state.last_bar_open_time,
        }
        for h in self._on_state_change:
            asyncio.create_task(h(state_snapshot))

    @property
    def state(self) -> MarketState:
        return self._state

    @property
    def closed_candles(self) -> list[dict[str, Any]]:
        if self._builder:
            return self._builder.closed_candles
        return []
