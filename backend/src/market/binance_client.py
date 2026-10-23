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
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Callable, Optional
from urllib.parse import urlsplit, urlunsplit

import httpx

logger = logging.getLogger(__name__)

# Stable provenance identity.  This client intentionally implements only the
# unauthenticated Spot market-data API; credentials are never read by the
# transport and no signed/private endpoint is exposed.
BINANCE_SPOT_PUBLIC = "BINANCE_SPOT_PUBLIC"


class BinanceRateLimitError(ValueError):
    """Binance rejected a public request because its rate budget was exceeded."""

    def __init__(self, status_code: int, message: str = "Binance rate limit"):
        super().__init__(message)
        self.status_code = status_code


class BinanceWSProtocolError(RuntimeError):
    """A Binance WebSocket protocol error which requires a fresh connection."""

    def __init__(self, code: int, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


_BINANCE_RATE_LIMIT_CODES = frozenset({-1003, -1008, -1015})


def _decimal_field(value: Any, field: str, *, positive: bool = False) -> Decimal:
    if isinstance(value, bool):
        raise ValueError(f"Invalid Binance {field}")
    try:
        parsed = Decimal(value)
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(f"Invalid Binance {field}") from exc
    if not parsed.is_finite() or (positive and parsed <= 0) or (not positive and parsed < 0):
        raise ValueError(f"Invalid Binance {field}")
    return parsed


def _raise_for_public_response(resp: httpx.Response) -> None:
    """Translate Binance rate-limit responses without exposing response data."""
    if resp.status_code in (418, 429):
        raise BinanceRateLimitError(resp.status_code)
    try:
        payload = resp.json()
    except (ValueError, json.JSONDecodeError):
        payload = None
    if isinstance(payload, dict) and payload.get("code") in _BINANCE_RATE_LIMIT_CODES:
        raise BinanceRateLimitError(resp.status_code or 429)
    resp.raise_for_status()


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

    source_identity = BINANCE_SPOT_PUBLIC
    source = BINANCE_SPOT_PUBLIC

    def __init__(self, config: BinanceConfig):
        self.config = config
        base = urlsplit(config.base_url)
        if (base.scheme not in ("http", "https") or not base.hostname
                or base.username is not None or base.password is not None
                or base.query or base.fragment):
            raise ValueError("base_url debe ser una base HTTP(S) pública sin credenciales")
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
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise ValueError("Binance kline limit must be an integer between 1 and 1000")
        if start_ts is not None and (type(start_ts) is not int or start_ts < 0):
            raise ValueError("Invalid Binance kline start timestamp")
        if end_ts is not None and (type(end_ts) is not int or end_ts < 0):
            raise ValueError("Invalid Binance kline end timestamp")
        if start_ts is not None and end_ts is not None and start_ts > end_ts:
            raise ValueError("Binance kline start must not exceed end")
        if not isinstance(interval, str) or not interval or not interval.isascii():
            raise ValueError("Invalid Binance kline interval")

        params: dict[str, Any] = {
            "symbol": _normalize_symbol(symbol),
            "interval": interval,
            "limit": limit,
        }
        if start_ts is not None:
            params["startTime"] = start_ts
        if end_ts is not None:
            params["endTime"] = end_ts

        resp = await self._client.get("/api/v3/klines", params=params)
        _raise_for_public_response(resp)
        try:
            raw = resp.json()
        except (ValueError, json.JSONDecodeError) as exc:
            raise ValueError("Invalid Binance klines response") from exc
        if not isinstance(raw, list) or len(raw) > limit:
            raise ValueError("Invalid Binance klines response")

        # Normalizar a dicts con keys claras
        normalized: list[dict[str, Any]] = []
        previous_open: int | None = None
        for row in raw:
            if not isinstance(row, (list, tuple)) or len(row) < 9:
                raise ValueError("Invalid Binance kline row")
            if any(isinstance(row[index], bool) for index in (0, 6, 8)):
                raise ValueError("Invalid Binance kline row")
            try:
                open_time = int(row[0])
                close_time = int(row[6])
                trades = int(row[8])
            except (TypeError, ValueError) as exc:
                raise ValueError("Invalid Binance kline row") from exc
            if open_time < 0 or close_time < open_time or trades < 0:
                raise ValueError("Invalid Binance kline row")
            values = [_decimal_field(row[index], name, positive=index in (1, 2, 3, 4))
                      for index, name in ((1, "open"), (2, "high"), (3, "low"),
                                          (4, "close"), (5, "volume"), (7, "quote volume"))]
            open_price, high, low, close, volume, quote_volume = values
            if low > min(open_price, close) or high < max(open_price, close) or low > high:
                raise ValueError("Invalid Binance OHLC values")
            if previous_open is not None and open_time <= previous_open:
                raise ValueError("Binance klines are not strictly ordered")
            previous_open = open_time
            normalized.append({
                "open_time": open_time,
                "open": float(open_price),
                "high": float(high),
                "low": float(low),
                "close": float(close),
                "volume": float(volume),
                "close_time": close_time,
                "quote_volume": float(quote_volume),
                "trades": trades,
            })
            if not all(math.isfinite(normalized[-1][field])
                       for field in ("open", "high", "low", "close", "volume", "quote_volume")):
                raise ValueError("Invalid Binance numeric payload")
        return normalized

    async def get_symbol_info(self, symbol: str) -> dict[str, Any]:
        """Obtener información del símbolo (tickSize, stepSize, etc.)."""
        native_symbol = _normalize_symbol(symbol)
        resp = await self._client.get("/api/v3/exchangeInfo", params={"symbol": native_symbol})
        _raise_for_public_response(resp)
        try:
            data = resp.json()
        except (ValueError, json.JSONDecodeError) as exc:
            raise ValueError("Invalid Binance exchange info response") from exc
        if not isinstance(data, dict) or not isinstance(data.get("symbols"), list):
            raise ValueError("Invalid Binance exchange info response")
        for s in data["symbols"]:
            if not isinstance(s, dict):
                raise ValueError("Invalid Binance exchange info symbol")
            if s.get("symbol") == native_symbol:
                if not all(isinstance(s.get(key), str) and s[key].strip()
                           for key in ("symbol", "baseAsset", "quoteAsset")):
                    raise ValueError("Invalid Binance exchange info symbol")
                raw_filters = s.get("filters", [])
                if not isinstance(raw_filters, list):
                    raise ValueError("Invalid Binance exchange info filters")
                filters: dict[str, dict[str, Any]] = {}
                for item in raw_filters:
                    if not isinstance(item, dict) or not isinstance(item.get("filterType"), str):
                        raise ValueError("Invalid Binance exchange info filter")
                    filters[item["filterType"]] = item
                # Some Binance-compatible public fixtures omit filters.  Keep
                # the historical defaults in that case, but reject malformed
                # values whenever a filter is supplied.
                def filter_decimal(filter_type: str, key: str, default: str) -> float:
                    item = filters.get(filter_type, {})
                    if filter_type in filters and key not in item:
                        raise ValueError("Invalid Binance exchange info filter")
                    value = item.get(key, default)
                    return float(_decimal_field(value, key, positive=True))

                return {
                    "symbol": symbol,
                    "base_asset": s.get("baseAsset"),
                    "quote_asset": s.get("quoteAsset"),
                    "tick_size": filter_decimal("PRICE_FILTER", "tickSize", "0.01"),
                    "step_size": filter_decimal("LOT_SIZE", "stepSize", "0.001"),
                    "min_notional": filter_decimal(
                        "NOTIONAL" if "NOTIONAL" in filters else "MIN_NOTIONAL",
                        "minNotional", "5.0",
                    ),
                }
        return {}

    async def get_book_ticker(self, symbol: str) -> dict[str, Any]:
        """Fetch Binance's public best bid/ask quote; no credentials are used."""
        native_symbol = _normalize_symbol(symbol)
        resp = await self._client.get(
            "/api/v3/ticker/bookTicker", params={"symbol": native_symbol},
        )
        _raise_for_public_response(resp)
        try:
            data = resp.json()
        except (ValueError, json.JSONDecodeError) as exc:
            raise ValueError("Invalid Binance book ticker response") from exc
        if not isinstance(data, dict):
            raise ValueError("Invalid Binance book ticker response")
        try:
            if any(isinstance(data.get(key), bool) for key in
                   ("bidPrice", "bidQty", "askPrice", "askQty")):
                raise ValueError("boolean quote")
            quote_symbol = data["symbol"]
            bid_price = Decimal(data["bidPrice"])
            bid_qty = Decimal(data["bidQty"])
            ask_price = Decimal(data["askPrice"])
            ask_qty = Decimal(data["askQty"])
        except (KeyError, TypeError, ValueError, InvalidOperation) as exc:
            raise ValueError("Invalid Binance book ticker response") from exc
        if (not isinstance(quote_symbol, str) or not quote_symbol.strip()
                or _normalize_symbol(quote_symbol) != native_symbol):
            raise ValueError("Invalid Binance book ticker response")
        if (any(not value.is_finite() or value <= 0
                for value in (bid_price, bid_qty, ask_price, ask_qty))
                or bid_price > ask_price):
            raise ValueError("Invalid Binance book ticker response")

        return {
            "symbol": quote_symbol,
            "bid_price": bid_price,
            "bid_qty": bid_qty,
            "ask_price": ask_price,
            "ask_qty": ask_qty,
            # bookTicker has no exchange timestamp; record local capture time.
            "timestamp": datetime.now(timezone.utc),
        }

    async def close(self):
        await self._client.aclose()


# ── Cliente WebSocket ─────────────────────────────────────────────────────────

class BinanceWSClient:
    """Cliente WebSocket para streaming de velas en tiempo real.

    Maneja reconexión con backoff exponencial automático.
    """

    source_identity = BINANCE_SPOT_PUBLIC
    source = BINANCE_SPOT_PUBLIC

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
                            if not isinstance(msg, dict):
                                raise ValueError("Invalid Binance WebSocket frame")
                            if "code" in msg:
                                code = msg.get("code")
                                if type(code) is int:
                                    raise BinanceWSProtocolError(
                                        code, "Binance WebSocket protocol error"
                                    )
                            if "k" not in msg:
                                continue
                            kline = msg["k"]
                            if not isinstance(kline, dict):
                                raise ValueError("Invalid Binance kline payload")
                            if _normalize_symbol(kline.get("s", native_symbol)) != native_symbol:
                                continue
                            required = ("t", "T", "o", "h", "l", "c", "v", "x")
                            if any(key not in kline for key in required) or type(kline["x"]) is not bool:
                                raise ValueError("Invalid Binance kline payload")
                            try:
                                open_time = int(kline["t"])
                                close_time = int(kline["T"])
                            except (TypeError, ValueError) as exc:
                                raise ValueError("Invalid Binance kline payload") from exc
                            if open_time < 0 or close_time < open_time:
                                raise ValueError("Invalid Binance kline timestamps")
                            values = {
                                name: _decimal_field(kline[key], name, positive=name != "volume")
                                for name, key in (("open", "o"), ("high", "h"),
                                                  ("low", "l"), ("close", "c"),
                                                  ("volume", "v"))
                            }
                            if (values["low"] > min(values["open"], values["close"])
                                    or values["high"] < max(values["open"], values["close"])
                                    or values["low"] > values["high"]):
                                raise ValueError("Invalid Binance OHLC values")
                            normalized = {
                                "symbol": symbol,
                                "open_time": open_time,
                                "close_time": close_time,
                                "open": float(values["open"]),
                                "high": float(values["high"]),
                                "low": float(values["low"]),
                                "close": float(values["close"]),
                                "volume": float(values["volume"]),
                                "is_closed": kline["x"],
                            }
                            if not all(math.isfinite(normalized[field])
                                       for field in ("open", "high", "low", "close", "volume")):
                                raise ValueError("Invalid Binance numeric payload")
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
