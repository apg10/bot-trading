"""Cliente HTTP + WebSocket para Binance Spot.

Maneja:
- Historial de velas por REST API
- Streaming en vivo por WebSocket (kline)
- Reconexión con backoff exponencial
- Notificación de conexión/desconexión (las velas y gaps los valida CandleBuilder)
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
from contextlib import suppress
from typing import Any, Callable, Optional
from urllib.parse import urlsplit, urlunsplit

import httpx

logger = logging.getLogger(__name__)


def _normalize_symbol(symbol: str) -> str:
    """Símbolo Spot nativo para el transporte; no acepta notación de derivados."""
    if not isinstance(symbol, str):
        raise ValueError("Símbolo Spot inválido")
    parts = symbol.strip().split("/")
    if len(parts) not in (1, 2) or any(
        not part or not part.isascii() or not part.isalnum() for part in parts
    ):
        raise ValueError("Símbolo Spot inválido")
    return "".join(parts).upper()


# ── Configuración ─────────────────────────────────────────────────────────────

class BinanceConfig:
    """Configuración pública Spot.

    ws_url es una base WS/WSS, con /ws opcional. max_reconnect_attempts cuenta
    reintentos después del intento inicial; 0 los deshabilita. El presupuesto y
    el backoff se reinician al recibir una kline válida para el símbolo solicitado.
    """

    def __init__(
        self,
        base_url: str = "https://api.binance.com",
        ws_url: str = "wss://stream.binance.com:9443",
        api_key: str = "",
        api_secret: str = "",
        kline_interval: str = "1m",
        max_reconnect_attempts: int = 5,
        reconnect_base_delay: float = 1.0,
    ):
        self.base_url = base_url.rstrip("/")
        self.ws_url = ws_url.rstrip("/")
        self.api_key = api_key
        self.api_secret = api_secret
        self.kline_interval = kline_interval
        self.max_reconnect_attempts = max_reconnect_attempts
        self.reconnect_base_delay = reconnect_base_delay


# ── Cliente HTTP ──────────────────────────────────────────────────────────────

class BinanceHTTPClient:
    """Cliente ligero para llamadas REST a Binance."""

    def __init__(self, config: BinanceConfig):
        self.config = config
        self._client = httpx.AsyncClient(
            base_url=config.base_url,
            timeout=httpx.Timeout(30.0),
            headers={"User-Agent": "TradingBotTerminal/0.1"},
        )

    async def get_klines(
        self,
        symbol: str,
        interval: str = "1m",
        limit: int = 500,
        start_ts: Optional[int] = None,
        end_ts: Optional[int] = None,
    ) -> list[dict[str, Any]]:
        """Obtener velas históricas desde la API de Binance.

        Retorna lista de dicts con keys:
        open_time, open, high, low, close, volume, close_time, ...
        """
        params: dict[str, Any] = {
            "symbol": _normalize_symbol(symbol),
            "interval": interval,
            "limit": min(limit, 1000),  # Binance max = 1000
        }
        if start_ts is not None:
            params["startTime"] = start_ts
        if end_ts is not None:
            params["endTime"] = end_ts

        resp = await self._client.get("/api/v3/klines", params=params)
        resp.raise_for_status()
        raw = resp.json()

        # Normalizar a dicts con keys claras
        return [
            {
                "open_time": int(row[0]),
                "open": float(row[1]),
                "high": float(row[2]),
                "low": float(row[3]),
                "close": float(row[4]),
                "volume": float(row[5]),
                "close_time": int(row[6]),
                "quote_volume": float(row[7]),
                "trades": int(row[8]),
            }
            for row in raw
        ]

    async def get_symbol_info(self, symbol: str) -> dict[str, Any]:
        """Obtener información del símbolo (tickSize, stepSize, etc.)."""
        native_symbol = _normalize_symbol(symbol)
        resp = await self._client.get("/api/v3/exchangeInfo", params={"symbol": native_symbol})
        resp.raise_for_status()
        data = resp.json()
        for s in data.get("symbols", []):
            if s["symbol"] == native_symbol:
                filters = {f["filterType"]: f for f in s.get("filters", [])}
                return {
                    "symbol": symbol,
                    "base_asset": s.get("baseAsset"),
                    "quote_asset": s.get("quoteAsset"),
                    "tick_size": float(filters.get("PRICE_FILTER", {}).get("tickSize", 0.01)),
                    "step_size": float(filters.get("LOT_SIZE", {}).get("stepSize", 0.001)),
                    "min_notional": float(filters.get("NOTIONAL", {}).get("minNotional", 5.0)),
                }
        return {}

    async def close(self):
        await self._client.aclose()


# ── Cliente WebSocket ─────────────────────────────────────────────────────────

class BinanceWSClient:
    """Cliente WebSocket para streaming de velas en tiempo real.

    Maneja reconexión con backoff exponencial automático.
    """

    def __init__(self, config: BinanceConfig):
        self.config = config
        self._on_message: Optional[Callable[[dict[str, Any]], None]] = None
        self._on_error: Optional[Callable[[Exception], None]] = None
        self._on_connect: Optional[Callable[[], None]] = None
        self._on_disconnect: Optional[Callable[[], None]] = None
        self._running = False
        self._ws: Optional[Any] = None
        self._run_task: Optional[asyncio.Task[Any]] = None
        self._stop_event = asyncio.Event()
        self._stop_lock = asyncio.Lock()

    def on_message(self, handler: Callable[[dict[str, Any]], None]) -> None:
        """Registra handler para mensajes de kline."""
        self._on_message = handler

    def on_error(self, handler: Callable[[Exception], None]) -> None:
        """Registra handler para errores."""
        self._on_error = handler

    def on_connect(self, handler: Callable[[], None]) -> None:
        """Registra handler cuando se conecta."""
        self._on_connect = handler

    def on_disconnect(self, handler: Callable[[], None]) -> None:
        """Registra handler cuando se desconecta."""
        self._on_disconnect = handler

    @staticmethod
    def _notify(handler: Optional[Callable], *args: Any) -> None:
        if handler is not None:
            try:
                handler(*args)
            except Exception:
                # No imprimir el error de callbacks/transportes: puede incluir
                # URLs o credenciales. Tampoco romper el ciclo de recepción.
                logger.warning("Error en un callback del cliente Binance")

    def _disconnect(self, ws: Any) -> None:
        if ws is not None and self._ws is ws:
            self._ws = None
            self._notify(self._on_disconnect)

    def _stream_uri(self, native_symbol: str) -> str:
        base = urlsplit(self.config.ws_url.strip())
        if (
            base.scheme not in ("ws", "wss") or not base.hostname
            or base.username is not None or base.password is not None
            or base.query or base.fragment
        ):
            raise ValueError("ws_url debe ser una base WS/WSS pública sin credenciales")
        interval = self.config.kline_interval
        if not interval or not interval.isascii() or not interval.isalnum():
            raise ValueError("Intervalo kline inválido")
        path = base.path.rstrip("/")
        if not path.endswith("/ws"):
            path += "/ws"
        return urlunsplit((
            base.scheme, base.netloc,
            f"{path}/{native_symbol.lower()}@kline_{interval}", "", "",
        ))

    async def _wait_before_reconnect(self, delay: float) -> None:
        try:
            await asyncio.wait_for(self._stop_event.wait(), timeout=delay)
        except asyncio.TimeoutError:
            pass

    async def start(self, symbol: str):
        """Inicia el streaming de klines para un símbolo.

        Espera hasta stop(), cancelación externa o agotamiento de reintentos.
        Conserva la etiqueta del llamador en los mensajes normalizados para no
        cambiar el contrato con MarketEngine; solo el transporte usa BTCUSDT.
        """
        if self._run_task is not None:
            raise RuntimeError("El cliente WebSocket ya está iniciado")
        native_symbol = _normalize_symbol(symbol)
        uri = self._stream_uri(native_symbol)
        maximum = self.config.max_reconnect_attempts
        base_delay = self.config.reconnect_base_delay
        if not isinstance(maximum, int) or maximum < 0:
            raise ValueError("max_reconnect_attempts debe ser un entero no negativo")
        if not math.isfinite(base_delay) or base_delay < 0:
            raise ValueError("reconnect_base_delay debe ser finito y no negativo")
        import websockets as _websockets

        self._run_task = asyncio.current_task()
        self._running = True
        self._stop_event.clear()
        retries = 0
        delay = min(base_delay, 60.0)
        try:
            while self._running:
                ws = None
                try:
                    async with _websockets.connect(uri) as ws:
                        if not self._running:
                            break
                        self._ws = ws
                        self._notify(self._on_connect)
                        while self._running:
                            try:
                                raw = await asyncio.wait_for(ws.recv(), timeout=30.0)
                            except asyncio.TimeoutError:
                                # Timeout de recepción no equivale a precio fresco.
                                # La validación de antigüedad corresponde al motor.
                                continue
                            except _websockets.exceptions.ConnectionClosed as error:
                                abnormal_close = getattr(
                                    _websockets.exceptions, "ConnectionClosedError", (),
                                )
                                if self._running and isinstance(error, abnormal_close):
                                    self._notify(self._on_error, error)
                                break
                            if not self._running:
                                break
                            msg = json.loads(raw)
                            if "k" not in msg:
                                continue
                            kline = msg["k"]
                            if _normalize_symbol(kline.get("s", native_symbol)) != native_symbol:
                                continue
                            normalized = {
                                "symbol": symbol,
                                "open_time": int(kline["t"]),
                                "close_time": int(kline["T"]),
                                "open": float(kline["o"]),
                                "high": float(kline["h"]),
                                "low": float(kline["l"]),
                                "close": float(kline["c"]),
                                "volume": float(kline["v"]),
                                "is_closed": kline["x"],
                            }
                            retries = 0
                            delay = min(base_delay, 60.0)
                            self._notify(self._on_message, normalized)
                except Exception as error:
                    if self._running:
                        logger.warning("Fallo del transporte WebSocket (%s)", type(error).__name__)
                        self._notify(self._on_error, error)
                finally:
                    self._disconnect(ws)

                if not self._running:
                    break
                if retries >= maximum:
                    logger.warning("Presupuesto de reconexión WebSocket agotado")
                    break
                retries += 1
                logger.info("Reconectando en %.1fs (reintento %d/%d)", delay, retries, maximum)
                await self._wait_before_reconnect(delay)
                delay = min(delay * 2, 60.0)
        except asyncio.CancelledError:
            # stop() cancela handshake/recv/backoff para apagarse sin esperar al
            # timeout. Una cancelación externa se conserva para el llamador.
            if self._running:
                raise
        finally:
            self._running = False
            self._disconnect(self._ws)
            self._run_task = None

    async def stop(self):
        """Apagado idempotente, incluso durante handshake o backoff."""
        async with self._stop_lock:
            self._running = False
            self._stop_event.set()
            task = self._run_task
            if task is not None and task is not asyncio.current_task():
                if not task.done():
                    task.cancel()
                with suppress(asyncio.CancelledError):
                    await task
            # El context manager de websockets cierra la sesión en start().
