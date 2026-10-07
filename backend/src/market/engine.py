"""MarketEngine — orquesta la conexión a Binance, candle builder y eventos."""

from __future__ import annotations

import asyncio
import logging
import math
import time
from contextlib import suppress
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Optional

from .binance_client import BinanceHTTPClient, BinanceWSClient, BinanceConfig
from .candle_builder import CandleBuilder, BarEvent, BarEventType

logger = logging.getLogger(__name__)
Handler = Callable[[Any], Awaitable[None]]


@dataclass
class MarketState:
    """Estado actual del mercado."""

    symbol: str
    connected: bool = False
    reconnecting: bool = False
    closed_candles_count: int = 0
    last_bar_open_time: Optional[int] = None
    current_bar_closed: Optional[dict[str, Any]] = None
    last_closed_close_time: Optional[int] = None
    history_loaded: bool = False
    recovering: bool = False
    complete: bool = False
    stale: bool = True
    entries_allowed: bool = False  # Aptitud de datos, nunca autorización de ejecución.
    pending_gaps: int = 0
    phase: str = "disconnected"
    max_candle_age_ms: int = 90_000


class MarketEngine:
    """Motor que orquesta Binance HTTP + WS + CandleBuilder.

    Responsabilidades:
    - Iniciar/detener conexión a Binance (HTTP para histórico, WS para streaming)
    - Gestionar reconexión con backoff
    - Orquestar eventos del builder y propagarlos
    - Mantener estado actualizado
    """

    def __init__(
        self, config: BinanceConfig, *, freshness_margin_seconds: float = 30.0,
        clock_ms: Callable[[], int] | None = None,
    ):
        self.config = config
        if not math.isfinite(freshness_margin_seconds) or freshness_margin_seconds < 0:
            raise ValueError("El margen de frescura debe ser finito y no negativo")
        self._clock_ms = clock_ms or (lambda: time.time_ns() // 1_000_000)
        self._freshness_margin_ms = int(freshness_margin_seconds * 1000)
        # Mantener la validación de intervalo en start(), antes de crear clientes.
        self._max_age_ms = 60_000 + self._freshness_margin_ms
        self.started = asyncio.Event()
        self._closed: dict[int, dict[str, Any]] = {}
        self._stream_closed: set[int] = set()
        self._coverage_start: int | None = None
        self._coverage_end: int | None = None
        self._pending_gaps: list[tuple[int, int]] = []
        self._latest_message: dict[str, Any] | None = None
        self._recovery_event = asyncio.Event()
        self._recovery_task: Optional[asyncio.Task[None]] = None
        self._freshness_task: Optional[asyncio.Task[None]] = None
        self._connection_epoch = 0
        self._recovery_verified = False
        self._cleanup_task: Optional[asyncio.Task[None]] = None
        # Separar HTTP (para histórico) y WS (para streaming en vivo).
        self._http_client: Optional[BinanceHTTPClient] = None
        self._ws_client: Optional[BinanceWSClient] = None
        self._builder: Optional[CandleBuilder] = None
        self._state = MarketState(symbol="", max_candle_age_ms=self._max_age_ms)
        self._running = False
        self._lifecycle_lock = asyncio.Lock()
        self._event_queue: Optional[asyncio.Queue[BarEvent | dict[str, Any]]] = None
        self._dispatch_task: Optional[asyncio.Task[None]] = None
        self._stream_task: Optional[asyncio.Task[None]] = None

        # Handlers de eventos del motor
        self._on_bar_closed: list[Handler] = []
        self._on_bar_updated: list[Handler] = []
        self._on_gap: list[Handler] = []
        self._on_state_change: list[Handler] = []

    @staticmethod
    def _remove_handler(handlers: list[Handler], handler: Handler) -> None:
        if handler in handlers:
            handlers.remove(handler)

    def _register_handler(self, handlers: list[Handler], handler: Handler) -> Callable[[], None]:
        if handler not in handlers:
            handlers.append(handler)
        return lambda: self._remove_handler(handlers, handler)

    def on_bar_closed(self, handler: Handler) -> Callable[[], None]:
        return self._register_handler(self._on_bar_closed, handler)

    def off_bar_closed(self, handler: Handler) -> None:
        self._remove_handler(self._on_bar_closed, handler)

    def on_bar_updated(self, handler: Handler) -> Callable[[], None]:
        return self._register_handler(self._on_bar_updated, handler)

    def off_bar_updated(self, handler: Handler) -> None:
        self._remove_handler(self._on_bar_updated, handler)

    def on_gap(self, handler: Handler) -> Callable[[], None]:
        return self._register_handler(self._on_gap, handler)

    def off_gap(self, handler: Handler) -> None:
        self._remove_handler(self._on_gap, handler)

    def on_state_change(self, handler: Handler) -> Callable[[], None]:
        return self._register_handler(self._on_state_change, handler)

    def off_state_change(self, handler: Handler) -> None:
        self._remove_handler(self._on_state_change, handler)

    @staticmethod
    def _interval_ms(interval: str) -> int:
        # 1M es un mes de calendario; no se aproxima a una duración fija.
        fixed_intervals = {
            "1s": 1_000,
            "1m": 60_000, "3m": 180_000, "5m": 300_000,
            "15m": 900_000, "30m": 1_800_000,
            "1h": 3_600_000, "2h": 7_200_000, "4h": 14_400_000,
            "6h": 21_600_000, "8h": 28_800_000, "12h": 43_200_000,
            "1d": 86_400_000, "3d": 259_200_000, "1w": 604_800_000,
        }
        if interval not in fixed_intervals:
            raise ValueError("Intervalo no soportado por el constructor de duración fija")
        return fixed_intervals[interval]

    async def start(self, symbol: str):
        """Inicia y espera al streaming; stop() o cancelación liberan recursos.

        BinanceWSClient ya normaliza las klines. Una cola local serializa su
        publicación sin hacer esperar la recepción a los suscriptores async.
        El transporte sigue siendo responsable de reconectar y notificarlo.
        """
        interval_ms = self._interval_ms(self.config.kline_interval)
        self._max_age_ms = interval_ms + self._freshness_margin_ms
        async with self._lifecycle_lock:
            await self._stop()
            self.started.clear()
            self._state = MarketState(symbol=symbol, max_candle_age_ms=self._max_age_ms)
            self._closed.clear()
            self._stream_closed.clear()
            self._coverage_start = self._coverage_end = None
            self._pending_gaps.clear()
            self._latest_message = None
            self._recovery_verified = False
            self._recovery_event.clear()
            self._builder = CandleBuilder(symbol, interval_ms=interval_ms)
            self._event_queue = queue = asyncio.Queue(maxsize=1024)
            self._running = True
            try:
                self._http_client = BinanceHTTPClient(self.config)
                self._ws_client = ws_client = BinanceWSClient(self.config)

                def active() -> bool:
                    return self._running and self._ws_client is ws_client

                def on_message(msg: dict[str, Any]) -> None:
                    if not active():
                        return
                    try:
                        candle = self._validated_candle(msg, streaming=True)
                        if candle is None or msg.get("symbol", symbol) != symbol:
                            return
                        assert self._builder is not None
                        events = self._builder.process_kline({**candle, "symbol": symbol})
                    except (KeyError, TypeError, ValueError):
                        logger.warning("Mensaje kline normalizado inválido")
                        return
                    if not self._latest_message or candle["open_time"] >= self._latest_message["open_time"]:
                        self._latest_message = {**candle, "symbol": symbol}
                    for event in events:
                        if event.event_type == BarEventType.GAP_DETECTED:
                            self._add_gap(event.gap_start_time, event.gap_end_time)
                            self._request_recovery()
                        if event.event_type == BarEventType.BAR_CLOSED and event.candle:
                            self._extend_coverage(event.candle["open_time"] + interval_ms)
                            self._merge_candles([event.candle], streaming=True)
                        elif event.event_type == BarEventType.BAR_UPDATED and event.candle:
                            self._extend_coverage(event.candle["open_time"])
                        self._enqueue(event)
                    self._refresh_state()

                def connection_state(connected: bool, reconnecting: bool) -> None:
                    if active():
                        self._state.connected = connected
                        self._state.reconnecting = reconnecting
                        self._connection_epoch += 1
                        if connected:
                            # Bloqueo sincrónico antes de cualquier mensaje posterior.
                            self._request_recovery()
                        else:
                            self._state.complete = False
                            self._state.recovering = True
                        self._emit_state_change()

                def on_error(_error: Exception) -> None:
                    connection_state(False, True)

                ws_client.on_message(on_message)
                ws_client.on_connect(lambda: connection_state(True, False))
                ws_client.on_disconnect(lambda: connection_state(False, True))
                ws_client.on_error(on_error)
                self._dispatch_task = asyncio.create_task(self._dispatch_events(queue))
                self._recovery_task = asyncio.create_task(self._recover())
                self._freshness_task = asyncio.create_task(self._watch_freshness())
                self._emit_state_change()
                self._stream_task = stream_task = asyncio.create_task(ws_client.start(symbol))
                self.started.set()
            except BaseException:
                await self._stop()
                raise

        try:
            await stream_task
        except asyncio.CancelledError:
            # stop/reinicio cancelan el transporte, no al llamador. Una cancelación
            # externa de start sí debe propagarse después de la limpieza.
            if self._running and self._ws_client is ws_client:
                raise
        finally:
            stopped_snapshot = None
            async with self._lifecycle_lock:
                if self._ws_client is ws_client:
                    stopped_snapshot = await self._stop()
            if stopped_snapshot is not None:
                await self._notify_stopped(stopped_snapshot)

    async def stop(self):
        """Detiene el motor y libera recursos."""
        async with self._lifecycle_lock:
            stopped_snapshot = await self._stop()
        # No ejecutar código de suscriptores bajo el lock de ciclo de vida:
        # un handler puede solicitar stop() sin provocar un interbloqueo.
        if stopped_snapshot is not None:
            await self._notify_stopped(stopped_snapshot)

    async def _notify_stopped(self, snapshot: dict[str, Any]) -> None:
        """Una oportunidad de entregar el estado; jamás esperar al consumidor lento."""
        tasks = [asyncio.create_task(self._notify_handlers([handler], snapshot))
                 for handler in tuple(self._on_state_change)]
        try:
            await asyncio.sleep(0)
        finally:
            for task in tasks:
                await self._cancel_task(task)

    @staticmethod
    async def _cancel_task(task: Optional[asyncio.Task[None]]) -> None:
        if task is None or task is asyncio.current_task():
            return
        if not task.done():
            task.cancel()
        with suppress(asyncio.CancelledError, Exception):
            await task

    async def _stop(self) -> Optional[dict[str, Any]]:
        if self._cleanup_task is not None:
            await asyncio.shield(self._cleanup_task)
            self._cleanup_task = None
        if not self._running and self._ws_client is None and self._http_client is None:
            return None
        self._running = False
        self._state.connected = False
        self._state.reconnecting = False
        self._state.recovering = False
        self._state.complete = False
        self._connection_epoch += 1

        ws_client, http_client = self._ws_client, self._http_client
        dispatcher, stream = self._dispatch_task, self._stream_task
        recovery, freshness = self._recovery_task, self._freshness_task
        self._ws_client = self._http_client = None
        self._dispatch_task = self._stream_task = None
        self._recovery_task = self._freshness_task = None
        self._event_queue = None
        # El llamador puede cancelarse mientras cerramos HTTP. Mantener una tarea
        # seguida permite continuar/reintentar el apagado sin perder recursos.
        caller = asyncio.current_task()
        self._cleanup_task = asyncio.create_task(self._release_resources(
            ws_client, http_client,
            [task for task in (dispatcher, recovery, freshness, stream) if task is not caller],
        ))
        await asyncio.shield(self._cleanup_task)
        self._cleanup_task = None
        return self._state_snapshot()

    async def _release_resources(self, ws_client: Any, http_client: Any, tasks: list) -> None:
        for task in tasks:
            await self._cancel_task(task)
        for client, method in ((ws_client, "stop"), (http_client, "close")):
            if client is not None:
                try:
                    await getattr(client, method)()
                except Exception:
                    logger.exception("Error al liberar un cliente de mercado")

    async def _dispatch_events(self, queue: asyncio.Queue[BarEvent | dict[str, Any]]) -> None:
        while self._event_queue is queue:
            event = await queue.get()
            try:
                if isinstance(event, BarEvent):
                    await self._handle_bar_event(event)
                else:
                    # Conservar el orden de los estados de transporte, pero no
                    # publicar contadores antiguos capturados antes de recibir barras.
                    snapshot = self._state_snapshot()
                    snapshot.update({
                        "connected": event["connected"],
                        "reconnecting": event["reconnecting"],
                    })
                    # No combinar un estado viejo de desconexión con aptitud actual.
                    if not snapshot["connected"] or event.get("recovering"):
                        snapshot["entries_allowed"] = False
                        snapshot["complete"] = False
                        snapshot["recovering"] = event.get("recovering", False)
                        snapshot["phase"] = "disconnected" if not snapshot["connected"] else "recovering"
                    if event.get("resync_required"):
                        snapshot["resync_required"] = True
                    await self._notify_handlers(self._on_state_change, snapshot)
            finally:
                queue.task_done()

    async def _notify_handlers(self, handlers: list[Handler], payload: Any) -> None:
        generation = self._event_queue
        for handler in tuple(handlers):
            if generation is not None and self._event_queue is not generation:
                return
            try:
                await handler(dict(payload) if isinstance(payload, dict) else payload)
            except Exception:
                logger.exception("Error en un suscriptor de mercado")

    async def _handle_bar_event(self, event: BarEvent):
        """Maneja eventos del CandleBuilder."""
        generation = self._event_queue
        if event.event_type == BarEventType.BAR_CLOSED and event.candle:
            await self._notify_handlers(self._on_bar_closed, event.candle)
            if self._event_queue is generation:
                await self._notify_handlers(self._on_state_change, self._state_snapshot())

        elif event.event_type == BarEventType.BAR_UPDATED and event.candle:
            await self._notify_handlers(self._on_bar_updated, event.candle)

        elif event.event_type == BarEventType.GAP_DETECTED:
            await self._notify_handlers(self._on_gap, event)

    def _state_snapshot(self) -> dict[str, Any]:
        self._refresh_state()
        return {
            "symbol": self._state.symbol,
            "connected": self._state.connected,
            "reconnecting": self._state.reconnecting,
            "closed_candles_count": self._state.closed_candles_count,
            "last_bar_open_time": self._state.last_bar_open_time,
            "last_closed_close_time": self._state.last_closed_close_time,
            "history_loaded": self._state.history_loaded,
            "recovering": self._state.recovering,
            "complete": self._state.complete,
            "stale": self._state.stale,
            "entries_allowed": self._state.entries_allowed,
            "pending_gaps": self._state.pending_gaps,
            "phase": self._state.phase,
            "max_candle_age_ms": self._max_age_ms,
            "execution_available": False,
        }

    def snapshot(self) -> dict[str, Any]:
        """UTC - cierre Binance; consultar recalcula sin depender de nuevos ticks."""
        return self._state_snapshot()

    def _refresh_state(self) -> None:
        s = self._state
        s.pending_gaps = len(self._pending_gaps)
        s.closed_candles_count = len(self._closed)
        if self._closed:
            latest = self._closed[max(self._closed)]
            s.current_bar_closed = dict(latest)
            s.last_bar_open_time = latest["open_time"]
            s.last_closed_close_time = latest["close_time"]
        age = None if s.last_closed_close_time is None else self._clock_ms() - s.last_closed_close_time
        s.stale = age is None or age < 0 or age > self._max_age_ms
        s.complete = bool(self._running and self._recovery_verified and s.history_loaded
                          and not s.pending_gaps and not s.recovering)
        s.entries_allowed = bool(self._running and s.connected and not s.reconnecting
                                 and s.complete and not s.stale)
        s.phase = (
            "disconnected" if not s.connected else "recovering" if s.recovering
            else "incomplete" if not s.complete else "stale" if s.stale else "complete"
        )

    def _enqueue(self, event: BarEvent | dict[str, Any]) -> None:
        queue = self._event_queue
        if queue is None:
            return
        if queue.full():
            # La publicación no debe crecer sin límite ni bloquear la recepción.
            # Invalidar la entrega y expulsar consumidores: deben volver a leer REST.
            while not queue.empty():
                queue.get_nowait()
                queue.task_done()
            queue.put_nowait({**self._state_snapshot(), "resync_required": True})
            logger.warning("Cola de publicación saturada; suscriptores requieren resincronización")
        queue.put_nowait(event)

    def _emit_state_change(self) -> None:
        if self._event_queue is not None:
            self._enqueue(self._state_snapshot())

    async def _watch_freshness(self) -> None:
        previous = None
        while self._running:
            snapshot = self.snapshot()
            key = (snapshot["stale"], snapshot["entries_allowed"], snapshot["phase"])
            if key != previous:
                self._emit_state_change()
                previous = key
            if self._state.connected and not self._state.recovering and not self._state.complete:
                self._request_recovery()
            await asyncio.sleep(1)

    def _validated_candle(self, row: dict[str, Any], *, streaming: bool = False) -> dict[str, Any] | None:
        interval = self._interval_ms(self.config.kline_interval)
        start, end = row["open_time"], row["close_time"]
        if type(start) is not int or type(end) is not int or end != start + interval - 1:
            raise ValueError("Timestamps de vela inválidos")
        if streaming and type(row.get("is_closed")) is not bool:
            raise ValueError("Confirmación de cierre inválida")
        closed = row.get("is_closed", False) if streaming else end < self._clock_ms()
        if not streaming and row.get("is_closed") is False:
            return None
        if closed and end > self._clock_ms():
            return None
        if streaming and start > self._clock_ms():
            return None
        candle = {"open_time": start, "close_time": end, "is_closed": closed}
        for field in ("open", "high", "low", "close", "volume"):
            value = float(row[field])
            if not math.isfinite(value) or value < 0 or (field != "volume" and value == 0):
                raise ValueError("OHLCV inválido")
            candle[field] = value
        if candle["low"] > min(candle["open"], candle["close"]) or candle["high"] < max(
            candle["open"], candle["close"]
        ) or candle["low"] > candle["high"]:
            raise ValueError("OHLC inválido")
        return candle if streaming or closed else None

    def _add_gap(self, start: int | None, end: int | None) -> None:
        if start is None or end is None or start >= end:
            return
        ranges = sorted([*self._pending_gaps, (start, end)])
        merged: list[tuple[int, int]] = []
        for left, right in ranges:
            if merged and left <= merged[-1][1]:
                merged[-1] = (merged[-1][0], max(right, merged[-1][1]))
            else:
                merged.append((left, right))
        self._pending_gaps = merged
        # Velas ya recibidas durante REST también satisfacen el rango.
        for candle in self._closed.values():
            self._resolve_gap(candle["open_time"])

    def _resolve_gap(self, start: int) -> None:
        end = start + self._interval_ms(self.config.kline_interval)
        remaining = []
        for left, right in self._pending_gaps:
            if start >= right or end <= left:
                remaining.append((left, right))
            else:
                if left < start:
                    remaining.append((left, start))
                if end < right:
                    remaining.append((end, right))
        self._pending_gaps = remaining

    def _extend_coverage(self, end: int) -> None:
        if self._coverage_end is not None and end > self._coverage_end:
            self._add_gap(self._coverage_end, end)
            self._coverage_end = end

    def _merge_candles(self, candles: list[dict[str, Any]], *, streaming: bool = False) -> None:
        for candle in candles:
            # Un cierre WS confirmado mientras REST estaba pendiente prevalece
            # sobre ese snapshot REST: la respuesta tardía no deshace el evento.
            if streaming or candle["open_time"] not in self._stream_closed:
                self._closed[candle["open_time"]] = dict(candle)
            if streaming:
                self._stream_closed.add(candle["open_time"])
            self._resolve_gap(candle["open_time"])
        # Recuperación puede exceder el buffer; los huecos se conservan aparte.
        for start in sorted(self._closed)[:-500]:
            del self._closed[start]
            self._stream_closed.discard(start)
        self._refresh_state()

    def _request_recovery(self) -> None:
        self._recovery_verified = False
        self._state.recovering = True
        self._state.complete = False
        self._state.entries_allowed = False
        self._recovery_event.set()
        self._emit_state_change()

    def _seed_builder(self) -> None:
        builder = CandleBuilder(self._state.symbol, self._interval_ms(self.config.kline_interval))
        for candle in self.closed_candles:
            builder.process_kline(candle)
        if self._latest_message and (
            not self._closed or self._latest_message["open_time"] > max(self._closed)
        ):
            builder.process_kline(self._latest_message)
        self._builder = builder

    async def _recover(self) -> None:
        """REST independiente de recepción/publicación; fallo o respuesta parcial bloquean."""
        while self._running:
            await self._recovery_event.wait()
            self._recovery_event.clear()
            if not self._state.connected:
                continue
            epoch = self._connection_epoch
            verified = False
            try:
                verified = await self._recover_history(epoch)
            except Exception:
                logger.warning("Recuperación de mercado incompleta; entradas bloqueadas")
            if epoch == self._connection_epoch and self._state.connected:
                self._recovery_verified = verified and not self._recovery_event.is_set()
                self._state.recovering = self._recovery_event.is_set()
                self._seed_builder()
                self._emit_state_change()

    async def _recover_history(self, epoch: int) -> bool:
        assert self._http_client is not None
        interval = self._interval_ms(self.config.kline_interval)
        target = self._clock_ms() // interval * interval  # fin exclusivo del histórico cerrado
        if not self._state.history_loaded:
            # Profundidad existente del buffer: 500 velas. El inicio se fija
            # ANTES de REST; una respuesta corta no redefine "histórico completo".
            if self._coverage_start is None:
                self._coverage_start = target - 500 * interval
                self._coverage_end = target
                self._add_gap(self._coverage_start, target)
            else:
                self._extend_coverage(target)
            rows = await self._http_client.get_klines(
                self._state.symbol, interval=self.config.kline_interval, limit=500, end_ts=target - 1,
            )
            candles = [c for row in rows if (c := self._validated_candle(row)) is not None
                       and self._coverage_start <= c["open_time"] < target]
            if not candles:
                return False
            if any(c["open_time"] % interval for c in candles):
                raise ValueError("Histórico fuera de los intervalos Binance")
            self._merge_candles(candles)
            self._state.history_loaded = True
        elif self._closed:
            self._extend_coverage(target)
            if not self._pending_gaps:
                # Incluso una reconexión dentro de la misma vela debe completar
                # una comprobación REST antes de rehabilitar los datos.
                start = max(self._closed)
                rows = await self._http_client.get_klines(
                    self._state.symbol, interval=self.config.kline_interval, limit=1000,
                    start_ts=start, end_ts=target - 1,
                )
                candles = [c for row in rows if (c := self._validated_candle(row)) is not None
                           and start <= c["open_time"] < target]
                if not candles or any(c["open_time"] % interval for c in candles):
                    return False
                self._merge_candles(candles)

        # Una vuelta por cada rango pendiente. Respuestas parciales conservan los
        # huecos; no se permite un bucle REST ocupado ante datos ausentes.
        for left, right in tuple(self._pending_gaps):
            cursor = left
            while cursor < right and self._running and self._state.connected and epoch == self._connection_epoch:
                page_end = min(right, cursor + 1000 * interval)
                rows = await self._http_client.get_klines(
                    self._state.symbol, interval=self.config.kline_interval, limit=1000,
                    start_ts=cursor, end_ts=page_end - 1,
                )
                candles = [c for row in rows if (c := self._validated_candle(row)) is not None
                           and cursor <= c["open_time"] < page_end]
                if any(c["open_time"] % interval for c in candles):
                    raise ValueError("Recuperación fuera de los intervalos Binance")
                if not candles:
                    break
                self._merge_candles(candles)
                cursor = max(c["open_time"] for c in candles) + interval

        # Si el reloj avanzó durante REST, cubrir también los cierres de ese periodo.
        new_target = self._clock_ms() // interval * interval
        self._extend_coverage(new_target)
        return bool(not self._pending_gaps and self._state.history_loaded)

    @property
    def state(self) -> MarketState:
        self._refresh_state()
        return self._state

    @property
    def closed_candles(self) -> list[dict[str, Any]]:
        return [dict(self._closed[start]) for start in sorted(self._closed)]
