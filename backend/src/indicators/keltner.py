# Keltner Channels indicator — EMA + bandas basadas en ATR.

import numpy as np
from dataclasses import dataclass
from typing import List


@dataclass
class KeltnerPoint:
    time_ms: int
    ema: float
    upper: float
    lower: float
    atr: float


def _ema(series: List[float], period: int) -> List[float]:
    """Calcula EMA (Exponential Moving Average)."""
    if len(series) < period:
        return series[:]

    result = [0.0] * len(series)
    multiplier = 2.0 / (period + 1)

    # Primer valor: SMA
    sma = sum(series[:period]) / period
    result[period - 1] = sma

    # Resto: EMA
    for i in range(period, len(series)):
        result[i] = (series[i] * multiplier) + (result[i - 1] * (1 - multiplier))

    return result


def _atr(highs: List[float], lows: List[float], closes: List[float], period: int = 14) -> List[float]:
    """Calcula ATR (Average True Range)."""
    if len(highs) < 2:
        return [0.0] * len(highs)

    true_ranges = [0.0]
    for i in range(1, len(highs)):
        tr = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        )
        true_ranges.append(tr)

    atr = [0.0] * len(true_ranges)
    atr[period - 1] = sum(true_ranges[:period]) / period

    for i in range(period, len(true_ranges)):
        atr[i] = (atr[i - 1] * (period - 1) + true_ranges[i]) / period

    return atr


def calculate_keltner(
    candles: List[dict],
    ema_period: int = 20,
    atr_period: int = 14,
    multiplier: float = 2.0,
) -> List[KeltnerPoint]:
    """Calcula canales Keltner para las velas dadas.

    Args:
        candles: Lista de dicts con keys: open_time, high, low, close
        ema_period: Período para la EMA central
        atr_period: Período para el ATR
        multiplier: Multiplicador para las bandas (default 2.0)

    Returns:
        Lista de KeltnerPoint con time_ms, ema, upper, lower, atr
    """
    if len(candles) < ema_period + atr_period:
        return []

    closes = [c["close"] for c in candles]
    highs = [c["high"] for c in candles]
    lows = [c["low"] for c in candles]

    ema = _ema(closes, ema_period)
    atr = _atr(highs, lows, closes, atr_period)

    result = []
    for i in range(ema_period + atr_period - 1, len(candles)):
        result.append(KeltnerPoint(
            time_ms=candles[i]["open_time"],
            ema=ema[i],
            upper=ema[i] + multiplier * atr[i],
            lower=ema[i] - multiplier * atr[i],
            atr=atr[i],
        ))

    return result
