"""MarketEngine — orquesta la conexión a Binance, candle builder y eventos."""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Optional

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
    """Motor que orquesta BinanceClient + CandleBuilder.

    Responsabilidades:
    - Iniciar/detener conexión a Binance
    - Gestionar reconexión con backoff
    - Orquestar eventos del builder y propagarlos
    - Mantener estado actualizado
    """

    def __init__(self, config: BinanceConfig):
        self.config = config
        self._client: Optional[BinanceHTTPClient] = None
        self._builder: Optional[CandleBuilder] = None
        self._state = MarketState(symbol="")
        self._running = False
        self._reconnect_task: Optional[asyncio.Task] = None

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
        """Inicia el motor para un símbolo dado."""
        if self._running:
            await self.stop()

        self._state = MarketState(symbol=symbol)
        self._running = True

        # Crear componentes
        self._client = BinanceHTTPClient(self.config)
        self._builder = CandleBuilder(symbol, interval_ms=60_000)

        # Conectar builder al client
        self._builder.on_event(self._handle_bar_event)

        # Iniciar streaming
        await self._client.start(symbol)
        self._state.connected = True
        self._emit_state_change()

    async def stop(self):
        """Detiene el motor."""
        self._running = False
        if self._client:
            await self._client.stop()
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
