# MACD indicator — Moving Average Convergence Divergence + signal line + histograma.

from dataclasses import dataclass
from typing import List


@dataclass
class MACDPoint:
    time_ms: int
    macd_line: float
    signal_line: float
    histogram: float


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


def calculate_macd(
    closes: List[float],
    fast_period: int = 12,
    slow_period: int = 26,
    signal_period: int = 9,
) -> List[MACDPoint]:
    """Calcula MACD para los precios de cierre dados.

    Args:
        closes: Lista de precios de cierre
        fast_period: Período rápido (default 12)
        slow_period: Período lento (default 26)
        signal_period: Período para la línea de señal (default 9)

    Returns:
        Lista de MACDPoint con time_ms, macd_line, signal_line, histogram
    """
    if len(closes) < slow_period + signal_period:
        return []

    ema_fast = _ema(closes, fast_period)
    ema_slow = _ema(closes, slow_period)

    # MACD line = EMA_fast - EMA_slow
    macd_lines = [ema_fast[i] - ema_slow[i] for i in range(len(closes))]

    # Signal line = EMA del MACD
    signal_line = _ema(macd_lines, signal_period)

    result = []
    for i in range(slow_period + signal_period - 1, len(closes)):
        result.append(MACDPoint(
            time_ms=i * 60_000,  # Asumiendo candles de 1 minuto (ajustar según necesidad)
            macd_line=macd_lines[i],
            signal_line=signal_line[i],
            histogram=macd_lines[i] - signal_line[i],
        ))

    return result
