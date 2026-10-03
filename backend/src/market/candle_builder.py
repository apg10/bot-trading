"""CandleBuilder — construye barras a partir de ticks/klines.

Maneja:
- Detección de duplicados
- Huecos en timestamps
- Eventos fuera de orden
- Cierre de barra y nueva barra
- Separar barra cerrada vs barra en formación
"""

from dataclasses import dataclass as enum_dataclass
import enum
import logging
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)


# ── Tipos ─────────────────────────────────────────────────────────────────────

class BarEventType(enum.StrEnum):
    BAR_CLOSED = "bar_closed"
    BAR_UPDATED = "bar_updated"
    NEW_BAR_STARTED = "new_bar_started"
    GAP_DETECTED = "gap_detected"
    DUPLICATE = "duplicate"


@enum_dataclass
class BarEvent:
    """Evento emitido por CandleBuilder."""

    event_type: BarEventType
    symbol: str
    candle: Optional[dict[str, Any]] = None
    gap_ms: Optional[int] = None
    previous_close_time: Optional[int] = None
    raw_message: Optional[dict[str, Any]] = None


@enum_dataclass
class CurrentBar:
    """Estado de la barra en formación."""

    symbol: str
    open_time: int
    open: float
    high: float = 0.0
    low: float = float("inf")
    close: float = 0.0
    volume: float = 0.0
    is_closed: bool = False


# ── Builder ───────────────────────────────────────────────────────────────────

class CandleBuilder:
    """Construye barras (velas) a partir de mensajes de Binance kline/streaming.

    Reglas:
    - Ignora duplicados (misma open_time que la barra actual)
    - Detecta huecos entre barras consecutivas
    - Detecta eventos fuera de orden (open_time < expected_next_open_time)
    - Emite evento BAR_CLOSED cuando una barra se cierra
    - Emite BAR_UPDATED con la barra en formación
    """

    def __init__(self, symbol: str, interval_ms: int = 60_000):
        self.symbol = symbol
        self.interval_ms = interval_ms

        self._current_bar: Optional[CurrentBar] = None
        self._expected_next_open_time: Optional[int] = None
        self._closed_candles: list[dict[str, Any]] = []
        self._max_closed_buffer: int = 500

        self._on_event: Optional[Callable[[BarEvent], None]] = None

    def on_event(self, handler: Callable[[BarEvent], None]) -> None:
        """Registra un handler para eventos del builder."""
        self._on_event = handler

    def _emit(self, event_type: BarEventType, **kwargs) -> None:
        candle = kwargs.get("candle")
        gap_ms = kwargs.get("gap_ms")
        previous_close_time = kwargs.get("previous_close_time")
        raw_message = kwargs.get("raw_message")

        event = BarEvent(
            event_type=event_type,
            symbol=self.symbol,
            candle=candle,
            gap_ms=gap_ms,
            previous_close_time=previous_close_time,
            raw_message=raw_message,
        )
        if self._on_event:
            self._on_event(event)

    def _start_new_bar(self, open_time: int, open_p: float) -> CurrentBar:
        bar = CurrentBar(
            symbol=self.symbol,
            open_time=open_time,
            open=open_p,
            high=open_p,
            low=open_p,
            close=open_p,
            volume=0.0,
        )
        self._current_bar = bar
        return bar

    def _close_current_bar(self) -> Optional[dict[str, Any]]:
        if not self._current_bar or self._current_bar.is_closed:
            return None

        closed = {
            "open_time": self._current_bar.open_time,
            "close_time": self._current_bar.open_time + self.interval_ms,
            "open": self._current_bar.open,
            "high": self._current_bar.high,
            "low": self._current_bar.low,
            "close": self._current_bar.close,
            "volume": self._current_bar.volume,
            "is_closed": True,
        }

        self._closed_candles.append(closed)
        if len(self._closed_candles) > self._max_closed_buffer:
            self._closed_candles.pop(0)

        self._current_bar = None
        return closed

    def process_kline(self, message: dict[str, Any]) -> list[BarEvent]:
        """Procesa un mensaje kline de Binance y retorna lista de eventos.

        Formato esperado de `message`:
        {
            "symbol": str,
            "open_time": int (ms),
            "close_time": int (ms),
            "open": float,
            "high": float,
            "low": float,
            "close": float,
            "volume": float,
            "is_closed": bool,  # True = barra cerrada por API
        }
        """
        events: list[BarEvent] = []
        symbol = message.get("symbol", self.symbol)

        if symbol != self.symbol:
            return events

        open_time = message["open_time"]
        is_closed = message.get("is_closed", False)

        # ── Caso 1: Actualización de barra en formación (streaming, mismo open_time) ─
        if self._current_bar and not is_closed and open_time == self._current_bar.open_time:
            bar = self._current_bar
            bar.high = max(bar.high, message["high"])
            bar.low = min(bar.low, message["low"])
            bar.close = message["close"]
            bar.volume += message.get("volume", 0.0)

            events.append(BarEvent(
                event_type=BarEventType.BAR_UPDATED,
                symbol=self.symbol,
                candle={
                    "open_time": bar.open_time,
                    "close_time": bar.open_time + self.interval_ms,
                    "open": bar.open,
                    "high": bar.high,
                    "low": bar.low,
                    "close": bar.close,
                    "volume": bar.volume,
                    "is_closed": False,
                },
            ))
            return events

        # ── Caso 2: Duplicado (mismo open_time que barra en formación) ──
        if self._current_bar and open_time == self._current_bar.open_time:
            events.append(BarEvent(
                event_type=BarEventType.DUPLICATE,
                symbol=self.symbol,
                raw_message=message,
            ))
            return events

        # ── Caso 3: Fuera de orden (open_time < esperado) ──────────────
        if self._expected_next_open_time and open_time < self._expected_next_open_time:
            logger.warning(
                "Evento fuera de orden: expected >= %d, got %d",
                self._expected_next_open_time,
                open_time,
            )
            events.append(BarEvent(
                event_type=BarEventType.DUPLICATE,
                symbol=self.symbol,
                raw_message=message,
            ))
            return events

        # ── Caso 4: Hueco detectado ─────────────────────────────────────
        if self._expected_next_open_time and open_time > self._expected_next_open_time:
            gap = open_time - self._expected_next_open_time
            events.append(BarEvent(
                event_type=BarEventType.GAP_DETECTED,
                symbol=self.symbol,
                gap_ms=gap,
                previous_close_time=self._expected_next_open_time - self.interval_ms,
            ))

        # ── Caso 5: Cerrar barra anterior si existe ─────────────────────
        if self._current_bar and not self._current_bar.is_closed:
            closed = self._close_current_bar()
            if closed:
                events.append(BarEvent(
                    event_type=BarEventType.BAR_CLOSED,
                    symbol=self.symbol,
                    candle=closed,
                ))

        # ── Caso 6: Barra ya cerrada por la API ─────────────────────────
        if is_closed:
            closed_candle = {
                "open_time": open_time,
                "close_time": open_time + self.interval_ms,
                "open": message["open"],
                "high": message["high"],
                "low": message["low"],
                "close": message["close"],
                "volume": message["volume"],
                "is_closed": True,
            }
            events.append(BarEvent(
                event_type=BarEventType.BAR_CLOSED,
                symbol=self.symbol,
                candle=closed_candle,
            ))
            # Agregar al buffer de cerradas (también para barras API)
            self._closed_candles.append(closed_candle)
            if len(self._closed_candles) > self._max_closed_buffer:
                self._closed_candles.pop(0)
            self._expected_next_open_time = open_time + self.interval_ms

        # ── Caso 7: Nueva barra (streaming) ─────────────────────────────
        elif self._current_bar is None:
            bar = self._start_new_bar(open_time, message["open"])
            self._expected_next_open_time = open_time + self.interval_ms
            events.append(BarEvent(
                event_type=BarEventType.NEW_BAR_STARTED,
                symbol=self.symbol,
                candle={
                    "open_time": open_time,
                    "close_time": open_time + self.interval_ms,
                    "open": bar.open,
                    "high": bar.high,
                    "low": bar.low,
                    "close": bar.close,
                    "volume": bar.volume,
                    "is_closed": False,
                },
            ))

        # ── Caso 8: Actualizar barra en formación (después de cerrar anterior) ─
        if self._current_bar and not is_closed:
            bar = self._current_bar
            bar.high = max(bar.high, message["high"])
            bar.low = min(bar.low, message["low"])
            bar.close = message["close"]
            bar.volume += message.get("volume", 0.0)

            events.append(BarEvent(
                event_type=BarEventType.BAR_UPDATED,
                symbol=self.symbol,
                candle={
                    "open_time": bar.open_time,
                    "close_time": bar.open_time + self.interval_ms,
                    "open": bar.open,
                    "high": bar.high,
                    "low": bar.low,
                    "close": bar.close,
                    "volume": bar.volume,
                    "is_closed": False,
                },
            ))

        return events

    @property
    def current_bar(self) -> Optional[CurrentBar]:
        return self._current_bar

    @property
    def closed_candles(self) -> list[dict[str, Any]]:
        return list(self._closed_candles)

    @property
    def expected_next_open_time(self) -> Optional[int]:
        return self._expected_next_open_time

    def reset(self):
        """Resetea el builder (útil en reconexión)."""
        self._current_bar = None
        self._expected_next_open_time = None
        self._closed_candles.clear()
