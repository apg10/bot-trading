"""Fixture sintética determinista para pruebas y desarrollo.

Datos generados con semilla fija para reproducibilidad.
Marcados como TEST_ONLY según el plan (sección 3).
"""

from __future__ import annotations

import random
from typing import List

from ..models.state import Candle, MarketSnapshot, Zone, Pivot, Annotation, Environment


def _seeded_rng(seed: int = 42) -> random.Random:
    """Devuelve un RNG con semilla fija para reproducibilidad."""
    return random.Random(seed)


def generate_candles(count: int = 100, base_price: float = 67500.0) -> List[Candle]:
    """Genera `count` barras sintéticas deterministas.

    Precio base inicial ~67500 (BTC-like). Rango diario ±2%.
    """
    rng = _seeded_rng(42)
    candles: List[Candle] = []
    price = base_price
    start_ts = 1700000000000  # 2023-11-14 approx, ms

    for i in range(count):
        ts = start_ts + i * 60_000  # 1-min bars
        change_pct = rng.gauss(0, 0.005)  # drift ±0.5% por barra
        high_pct = abs(rng.gauss(0, 0.003))
        low_pct = abs(rng.gauss(0, 0.003))

        open_p = price
        close_p = price * (1 + change_pct)
        high_p = max(open_p, close_p) * (1 + high_pct)
        low_p = min(open_p, close_p) * (1 - low_pct)
        volume = rng.uniform(50, 500)

        candles.append(Candle(
            timestamp_ms=ts,
            open=round(open_p, 2),
            high=round(high_p, 2),
            low=round(low_p, 2),
            close=round(close_p, 2),
            volume=round(volume, 4),
        ))
        price = close_p

    return candles


def generate_market_snapshot(candles: List[Candle], idx: int = -1) -> MarketSnapshot:
    """Genera un snapshot a partir de una barra existente."""
    c = candles[idx]
    rng = _seeded_rng(99)
    spread = abs(c.close - c.open) * 0.1
    bid = c.close - spread / 2
    ask = c.close + spread / 2
    return MarketSnapshot(
        symbol="BTC/USDT",
        environment=Environment.PAPER,
        candle=c,
        bid=round(bid, 2),
        ask=round(ask, 2),
        timestamp_ms=c.timestamp_ms,
    )


def generate_zones(count: int = 5) -> List[Zone]:
    """Genera zonas sintéticas deterministas."""
    rng = _seeded_rng(77)
    origins = ["support", "resistance", "breakout", "retracement"]
    statuses = ["observing", "candidate", "pending_analysis", "approved", "rejected"]
    zones: List[Zone] = []

    for i in range(count):
        low = 67000 + rng.uniform(0, 1000)
        high = low + rng.uniform(50, 300)
        zones.append(Zone(
            id=f"zone-{i:03d}",
            origin=origins[i % len(origins)],
            status=statuses[i % len(statuses)],
            low=round(low, 2),
            high=round(high, 2),
            timestamp_ms=1700000000000 + i * 60_000,
            contacts=rng.randint(2, 15),
        ))

    return zones


def generate_pivots(count: int = 4) -> List[Pivot]:
    """Genera pivotes sintéticos deterministas."""
    rng = _seeded_rng(88)
    types_list = ["swing_high", "swing_low", "equal_high", "equal_low"]
    pivots: List[Pivot] = []

    for i in range(count):
        price = 67500 + rng.uniform(-500, 500)
        pivots.append(Pivot(
            id=f"pivot-{i:03d}",
            pivot_type=types_list[i % len(types_list)],
            price=round(price, 2),
            timestamp_ms=1700000000000 + i * 60_000,
            strength=round(rng.uniform(0.3, 1.0), 4),
        ))

    return pivots


def generate_annotations(count: int = 3) -> List[Annotation]:
    """Genera anotaciones sintéticas deterministas."""
    rng = _seeded_rng(55)
    types_list = ["entry_proposal", "stop_loss", "take_profit", "zone", "note"]
    annotations: List[Annotation] = []

    for i in range(count):
        annotations.append(Annotation(
            id=f"ann-{i:03d}",
            annotation_type=types_list[i % len(types_list)],
            symbol="BTC/USDT",
            content=f"Anotación de prueba {i+1}",
            x_px=rng.uniform(100, 800),
            y_px=rng.uniform(100, 500),
            timestamp_ms=1700000000000 + i * 60_000,
        ))

    return annotations


# Exportación de fixture completa
def get_full_fixture() -> dict:
    """Devuelve un diccionario con toda la fixture sintética."""
    candles = generate_candles(100)
    snapshot = generate_market_snapshot(candles)
    zones = generate_zones(5)
    pivots = generate_pivots(4)
    annotations = generate_annotations(3)

    return {
        "snapshot": snapshot,
        "candles": candles[-10:],  # últimas 10 barras
        "zones": zones,
        "pivots": pivots,
        "annotations": annotations,
    }
