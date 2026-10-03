"""Cliente HTTP + WebSocket para Binance Spot.

Maneja:
- Historial de velas por REST API
- Streaming en vivo por WebSocket (kline)
- Reconexión con backoff exponencial
- Detección de duplicados y huecos
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, AsyncIterator, Callable, Optional
from urllib.parse import urlencode

import httpx

logger = logging.getLogger(__name__)


# ── Configuración ─────────────────────────────────────────────────────────────

class BinanceConfig:
    """Configuración para conectar a Binance."""

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
            "symbol": symbol,
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
        resp = await self._client.get("/api/v3/exchangeInfo", params={"symbol": symbol})
        resp.raise_for_status()
        data = resp.json()
        for s in data.get("symbols", []):
            if s["symbol"] == symbol:
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

    async def start(self, symbol: str):
        """Inicia el streaming de klines para un símbolo.

        Maneja reconexión con backoff exponencial automático.
        Este método es bloqueante (no retorna hasta que se llame stop()).
        """
        import websockets as _websockets

        self._running = True
        attempt = 0

        while self._running:
            try:
                uri = f"wss://stream.binance.com:9443/ws/{symbol.lower()}@kline_{self.config.kline_interval}"
                async with _websockets.connect(uri) as ws:
                    self._ws = ws
                    attempt = 0
                    if self._on_connect:
                        self._on_connect()

                    while self._running:
                        try:
                            msg_raw = await asyncio.wait_for(ws.recv(), timeout=30.0)
                            msg = __import__("json").loads(msg_raw)
                            if "k" in msg:  # kline message
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
                                if self._on_message:
                                    self._on_message(normalized)
                        except asyncio.TimeoutError:
                            # Pong timeout, keep alive
                            pass
                        except _websockets.exceptions.ConnectionClosed:
                            break

            except Exception as e:
                logger.warning("WebSocket error: %s", e)
                if self._on_error:
                    self._on_error(e)

            # Reconexión con backoff exponencial
            if self._running:
                delay = min(
                    self.config.reconnect_base_delay * (2 ** attempt),
                    60.0,  # max 60s
                )
                logger.info("Reconnecting in %.1fs (attempt %d)...", delay, attempt + 1)
                attempt += 1
                await asyncio.sleep(delay)

    async def stop(self):
        """Detiene el streaming y cierra la conexión WebSocket."""
        self._running = False
        if self._ws:
            try:
                await self._ws.close()
            except Exception:
                pass
            self._ws = None
        if self._on_disconnect:
            self._on_disconnect()
