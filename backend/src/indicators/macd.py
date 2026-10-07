# MACD indicator — Moving Average Convergence Divergence + signal line + histograma.
"""
MACD convencional (sin bandas BB) calculado sobre series de cierre.
Los timestamps se proporcionan opcionalmente;
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

    Convención de calentamiento:
        Cada EMA de precios usa la SMA de sus primeros `period` cierres como
        semilla, conservando la implementación EMA compartida. La primera línea
        MACD válida está en el índice max(fast_period, slow_period) - 1.
        La señal usa como semilla la SMA de las primeras `signal_period` líneas
        MACD válidas; nunca incluye los ceros de calentamiento de las EMA.
        Después de la semilla, el suavizado EMA usa alfa = 2 / (period + 1).
        La primera salida completa está en max(fast_period, slow_period)
        + signal_period - 2 (índice base cero): se requieren max(fast, slow)
        + signal - 1 cierres, 34 para los períodos 12/26/9.

    Raises:
        ValueError: Si los períodos no son enteros positivos o los timestamps
                    no corresponden uno a uno con los cierres.
    """
    for name, period in (
        ("fast_period", fast_period),
        ("slow_period", slow_period),
        ("signal_period", signal_period),
    ):
        if isinstance(period, bool) or not isinstance(period, int) or period <= 0:
            raise ValueError(f"{name} debe ser un entero positivo")
    if timestamps is not None and len(timestamps) != len(closes):
        raise ValueError("timestamps y closes deben tener la misma longitud")

    macd_start = max(fast_period, slow_period) - 1
    if len(closes) < macd_start + signal_period:
        return []

    ema_fast = ema(closes, fast_period)
    ema_slow = ema(closes, slow_period)

    # Recortar las posiciones en las que alguna EMA todavía no es válida.
    macd_lines = [ema_fast[i] - ema_slow[i] for i in range(macd_start, len(closes))]

    # Señal sembrada exclusivamente con líneas MACD válidas.
    signal_line = ema(macd_lines, signal_period)

    result = []
    for offset in range(signal_period - 1, len(macd_lines)):
        i = macd_start + offset
        time_ms = timestamps[i] if timestamps is not None else i * 60_000
        result.append(MACDPoint(
            time_ms=time_ms,
            macd_line=macd_lines[offset],
            signal_line=signal_line[offset],
            histogram=macd_lines[offset] - signal_line[offset],
        ))

    return result
