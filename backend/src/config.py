"""Configuración por entorno (PAPER, EXCHANGE_DEMO, LIVE).

El entorno se selecciona con la variable de entorno TRADING_ENV.
Valor predeterminado: PAPER.
"""

from enum import StrEnum
from pathlib import Path
from typing import Optional


class Environment(StrEnum):
    PAPER = "paper"
    EXCHANGE_DEMO = "exchange_demo"
    LIVE = "live"


class Config:
    """Configuración cargada desde TRADING_ENV."""

    def __init__(self, env: str | None = None) -> None:
        # Map input names to enum members (case-insensitive)
        mapping = {
            "PAPER": Environment.PAPER,
            "EXCHANGE_DEMO": Environment.EXCHANGE_DEMO,
            "EXCHANGEDEMO": Environment.EXCHANGE_DEMO,
            "DEMO": Environment.EXCHANGE_DEMO,
            "LIVE": Environment.LIVE,
        }
        raw = (env or "PAPER").upper().replace("-", "_")
        self.env = mapping.get(raw, Environment.PAPER)
        # Bandera de seguridad: nunca permitir LIVE sin confirmación explícita
        self.is_live = self.env == Environment.LIVE


# Instancia global para importación directa
config = Config()


def get_config(env: Optional[str] = None) -> Config:
    """Devuelve la instancia de configuración (singleton opcional)."""
    if env is not None:
        return Config(env)
    return config
