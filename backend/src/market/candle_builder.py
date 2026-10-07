"""CandleBuilder — procesa snapshots kline acumulados de Binance.

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
    """Evento del builder. Los gaps indican un intervalo [inicio, fin).

    previous_close_time es el cierre recibido de la última vela confirmada,
    o None si aún no existe ninguna. No confirma una vela parcial.
    """

    event_type: BarEventType
    symbol: str
    candle: Optional[dict[str, Any]] = None
    gap_ms: Optional[int] = None
    previous_close_time: Optional[int] = None
    raw_message: Optional[dict[str, Any]] = None
    gap_start_time: Optional[int] = None
    gap_end_time: Optional[int] = None


@enum_dataclass
class CurrentBar:
    """Estado de la barra en formación."""

    symbol: str
    open_time: int
    open: float
    close_time: int
    high: float = 0.0
    low: float = float("inf")
    close: float = 0.0
    volume: float = 0.0
    is_closed: bool = False


# ── Builder ───────────────────────────────────────────────────────────────────

class CandleBuilder:
    """Construye barras (velas) a partir de mensajes de Binance kline/streaming.

    Reglas:
    - Una kline es un snapshot con volumen acumulado, no un tick incremental
    - Ignora snapshots idénticos y barras ya cerradas o fuera de orden
    - Acepta el cierre definitivo de la misma barra en formación
    - Detecta huecos entre barras consecutivas
    - Solo emite BAR_CLOSED cuando recibe is_closed=True
    - Un cierre perdido requiere recuperación; nunca se fabrica una vela cerrada
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
            gap_start_time=kwargs.get("gap_start_time"),
            gap_end_time=kwargs.get("gap_end_time"),
        )
        if self._on_event:
            self._on_event(event)

    def _start_new_bar(self, open_time: int, open_p: float, close_time: int) -> CurrentBar:
        bar = CurrentBar(
            symbol=self.symbol,
            open_time=open_time,
            open=open_p,
            close_time=close_time,
            high=open_p,
            low=open_p,
            close=open_p,
            volume=0.0,
        )
        self._current_bar = bar
        return bar

    def _current_candle(self) -> dict[str, Any]:
        """Snapshot independiente de la barra provisional, sin confirmar cierre."""
        bar = self._current_bar
        assert bar is not None
        return {
            "open_time": bar.open_time,
            "close_time": bar.close_time,
            "open": bar.open,
            "high": bar.high,
            "low": bar.low,
            "close": bar.close,
            "volume": bar.volume,
            "is_closed": False,
        }

    def _close_current_bar(self) -> Optional[dict[str, Any]]:
        if not self._current_bar or self._current_bar.is_closed:
            return None

        # Solo se llama tras recibir y aplicar el mensaje definitivo de Binance.
        closed = self._current_candle()
        closed["is_closed"] = True

        self._closed_candles.append(closed)
        if len(self._closed_candles) > self._max_closed_buffer:
            self._closed_candles.pop(0)

        self._current_bar = None
        return closed

    def process_kline(self, message: dict[str, Any]) -> list[BarEvent]:
        """Procesa un mensaje kline de Binance y retorna lista de eventos.

        Los gaps requieren backfill separado: un cierre de un intervalo anterior
        a la barra vigente no se inserta por este flujo de streaming.

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
        same_bar = (
            self._current_bar is not None
            and open_time == self._current_bar.open_time
        )

        # La vela actual es la excepción: sus actualizaciones/cierre son válidos
        # aunque su open_time sea anterior al siguiente intervalo esperado.
        if (
            not same_bar
            and self._expected_next_open_time is not None
            and open_time < self._expected_next_open_time
        ):
            logger.warning(
                "Evento fuera de orden: expected >= %d, got %d",
                self._expected_next_open_time,
                open_time,
            )
            events.append(BarEvent(
                event_type=BarEventType.DUPLICATE,
                symbol=self.symbol,
                raw_message=dict(message),
            ))
            return events

        candle = {
            "open_time": open_time,
            "close_time": message["close_time"],
            "open": message["open"],
            "high": message["high"],
            "low": message["low"],
            "close": message["close"],
            "volume": message["volume"],
            "is_closed": is_closed,
        }
        if same_bar and candle == self._current_candle():
            events.append(BarEvent(
                event_type=BarEventType.DUPLICATE,
                symbol=self.symbol,
                raw_message=dict(message),
            ))
            return events

        if not same_bar:
            # Si la barra anterior sigue abierta, incluirla entera en el gap:
            # cambiar de intervalo no constituye una confirmación de cierre.
            gap_start = (
                self._current_bar.open_time
                if self._current_bar is not None
                else self._expected_next_open_time
            )
            if gap_start is not None and open_time > gap_start:
                events.append(BarEvent(
                    event_type=BarEventType.GAP_DETECTED,
                    symbol=self.symbol,
                    gap_ms=open_time - gap_start,
                    previous_close_time=(
                        self._closed_candles[-1]["close_time"]
                        if self._closed_candles else None
                    ),
                    gap_start_time=gap_start,
                    gap_end_time=open_time,
                    raw_message=dict(message),
                ))
            self._start_new_bar(open_time, candle["open"], candle["close_time"])
            self._expected_next_open_time = open_time + self.interval_ms

        # Cada mensaje contiene OHLCV acumulado. El cierre definitivo es
        # autoritativo incluso si corrige un snapshot provisional anterior.
        bar = self._current_bar
        assert bar is not None
        bar.open = candle["open"]
        bar.close_time = candle["close_time"]
        bar.high = candle["high"]
        bar.low = candle["low"]
        bar.close = candle["close"]
        bar.volume = candle["volume"]

        if is_closed:
            closed_candle = self._close_current_bar()
            assert closed_candle is not None
            events.append(BarEvent(
                event_type=BarEventType.BAR_CLOSED,
                symbol=self.symbol,
                candle=closed_candle,
            ))
        else:
            if not same_bar:
                events.append(BarEvent(
                    event_type=BarEventType.NEW_BAR_STARTED,
                    symbol=self.symbol,
                    candle=dict(candle),
                ))
            events.append(BarEvent(
                event_type=BarEventType.BAR_UPDATED,
                symbol=self.symbol,
                candle=dict(candle),
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
