# Indicators package for technical analysis.
"""Utilidades compartidas entre indicadores técnicos."""

from typing import List


def ema(series: List[float], period: int) -> List[float]:
    """Calcula EMA (Exponential Moving Average).

    Args:
        series: Lista de valores numéricos.
        period: Período de suavizado.

    Returns:
        Lista del mismo tamaño con valores EMA. Los primeros `period-1` elementos son 0.0.
    """
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
