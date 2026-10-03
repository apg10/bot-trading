# MACD indicator — Moving Average Convergence Divergence + signal line + histograma.
"""
MACD calculado sobre series de cierre. Los timestamps se proporcionan opcionalmente;
si no se proporcionan, se asumen candles de 1 minuto empezando en t=0 (solo para pruebas).
"""

from dataclasses import dataclass
from typing import List, Optional

from . import ema


@dataclass
class MACDPoint:
    time_ms: int
    macd_line: float
    signal_line: float
    histogram: float


def calculate_macd(
    closes: List[float],
    timestamps: Optional[List[int]] = None,
    fast_period: int = 12,
    slow_period: int = 26,
    signal_period: int = 9,
) -> List[MACDPoint]:
    """Calcula MACD para los precios de cierre dados.

    Args:
        closes: Lista de precios de cierre.
        timestamps: Lista opcional de timestamps (ms). Si se omite, se asumen
                    candles de 1 minuto empezando en t=0 — solo para pruebas unitarias.
        fast_period: Período rápido (default 12)
        slow_period: Período lento (default 26)
        signal_period: Período para la línea de señal (default 9)

    Returns:
        Lista de MACDPoint con time_ms, macd_line, signal_line, histogram.
    """
    if len(closes) < slow_period + signal_period:
        return []

    ema_fast = ema(closes, fast_period)
    ema_slow = ema(closes, slow_period)

    # MACD line = EMA_fast - EMA_slow
    macd_lines = [ema_fast[i] - ema_slow[i] for i in range(len(closes))]

    # Signal line = EMA del MACD
    signal_line = ema(macd_lines, signal_period)

    result = []
    start_idx = slow_period + signal_period - 1
    for i in range(start_idx, len(closes)):
        time_ms = timestamps[i] if timestamps is not None else i * 60_000
        result.append(MACDPoint(
            time_ms=time_ms,
            macd_line=macd_lines[i],
            signal_line=signal_line[i],
            histogram=macd_lines[i] - signal_line[i],
        ))

    return result
