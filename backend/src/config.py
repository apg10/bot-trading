"""Configuración por entorno (PAPER, EXCHANGE_DEMO, LIVE).

El entorno se selecciona con la variable de entorno TRADING_ENV.
Valor predeterminado: PAPER.
LIVE está deshabilitado. N01 usa MARKET_INTERVAL=1m y
MARKET_FRESHNESS_MARGIN_SECONDS=30: edad máxima = 60s + margen.
La referencia es UTC actual menos close_time Binance de la última vela cerrada.
"""

from enum import StrEnum
import math
import os
from typing import Optional


class Environment(StrEnum):
    PAPER = "paper"
    EXCHANGE_DEMO = "exchange_demo"
    LIVE = "live"


class Config:
    """Configuración PAPER/DEMO; datos públicos Spot, sin ejecución externa."""

    def __init__(
        self, env: str | None = None, *, market_freshness_margin_seconds: float | None = None
    ) -> None:
        # Map input names to enum members (case-insensitive)
        mapping = {
            "PAPER": Environment.PAPER,
            "EXCHANGE_DEMO": Environment.EXCHANGE_DEMO,
            "EXCHANGEDEMO": Environment.EXCHANGE_DEMO,
            "DEMO": Environment.EXCHANGE_DEMO,
            "LIVE": Environment.LIVE,
        }
        raw = (env or os.getenv("TRADING_ENV", "PAPER")).upper().replace("-", "_")
        self.env = mapping.get(raw, Environment.PAPER)
        if self.env == Environment.LIVE:
            raise ValueError("LIVE está deshabilitado; no existe ejecución externa")
        self.is_live = False
        self.execution_available = False
        self.market_symbol = os.getenv("MARKET_SYMBOL", "BTC/USDT")
        self.market_interval = os.getenv("MARKET_INTERVAL", "1m")
        if self.market_interval != "1m":
            raise ValueError("N01 solo admite el intervalo acordado 1m")
        margin = (
            market_freshness_margin_seconds if market_freshness_margin_seconds is not None
            else float(os.getenv("MARKET_FRESHNESS_MARGIN_SECONDS", "30"))
        )
        if not math.isfinite(margin) or margin < 0:
            raise ValueError("El margen de frescura debe ser finito y no negativo")
        self.market_freshness_margin_seconds = float(margin)
        self.market_max_candle_age_seconds = 60.0 + self.market_freshness_margin_seconds


# Instancia global para importación directa
config = Config()


def get_config(env: Optional[str] = None) -> Config:
    """Devuelve la instancia de configuración (singleton opcional)."""
    if env is not None:
        return Config(env)
    return config
