# FYL (First Yellow Line) indicator — Detección de pivotes y zonas de consolidación.

from dataclasses import dataclass
from enum import Enum
from typing import List, Optional


class PivotType(str, Enum):
    """Tipos de pivote."""
    HIGH = "high"  # Máximo local
    LOW = "low"    # Mínimo local


class ConsolidationStatus(str, Enum):
    """Estado de consolidación."""
    FORMING = "forming"      # En formación
    COMPLETE = "complete"    # Completada
    INVALIDATED = "invalidated"  # Invalidada


@dataclass
class PivotPoint:
    """Un punto de pivote detectado."""
    time_ms: int
    price: float
    pivot_type: PivotType
    strength: float  # Fuerza del pivote (0-1)


@dataclass
class ConsolidationZone:
    """Una zona de consolidación entre dos pivotes."""
    id: str
    start_time_ms: int
    end_time_ms: int
    origin: PivotType  # De qué tipo partió la zona
    low: float
    high: float
    contacts: int  # Número de contactos con los bordes
    status: ConsolidationStatus = ConsolidationStatus.FORMING


@dataclass
class FYLResult:
    """Resultado del análisis FYL."""
    pivots: List[PivotPoint]
    zones: List[ConsolidationZone]
    annotations: List[dict]  # Anotaciones de análisis


def _detect_pivots(
    highs: List[float],
    lows: List[float],
    closes: List[float],
    lookback: int = 5,
    min_strength: float = 0.01,
) -> List[PivotPoint]:
    """Detecta pivotes (máximos y mínimos locales) en los datos."""
    pivots = []

    for i in range(lookback, len(highs) - lookback):
        # Verificar si es un máximo local
        is_high = True
        for j in range(i - lookback, i + lookback + 1):
            if j != i and highs[j] >= highs[i]:
                is_high = False
                break

        if is_high:
            # Calcular fuerza del pivote (basada en la diferencia con los vecinos)
            strength = max(
                (highs[i] - min(highs[max(0, i - lookback):i])) / highs[i],
                (highs[i] - min(highs[i + 1:min(len(highs), i + lookback + 1)])) / highs[i],
            )
            if strength >= min_strength:
                pivots.append(PivotPoint(
                    time_ms=i * 60_000,
                    price=highs[i],
                    pivot_type=PivotType.HIGH,
                    strength=strength,
                ))

        # Verificar si es un mínimo local
        is_low = True
        for j in range(i - lookback, i + lookback + 1):
            if j != i and lows[j] <= lows[i]:
                is_low = False
                break

        if is_low:
            strength = max(
                (max(lows[max(0, i - lookback):i]) - lows[i]) / abs(lows[i]),
                (max(lows[i + 1:min(len(lows), i + lookback + 1)]) - lows[i]) / abs(lows[i]),
            )
            if strength >= min_strength:
                pivots.append(PivotPoint(
                    time_ms=i * 60_000,
                    price=lows[i],
                    pivot_type=PivotType.LOW,
                    strength=strength,
                ))

    return pivots


def _detect_consolidation_zones(
    pivots: List[PivotPoint],
    candles: List[dict],
) -> List[ConsolidationZone]:
    """Detecta zonas de consolidación entre pivotes consecutivos."""
    zones = []
    zone_id = 0

    for i in range(len(pivots) - 1):
        pivot_a = pivots[i]
        pivot_b = pivots[i + 1]

        # Solo considerar pares del mismo tipo (high-high o low-low)
        if pivot_a.pivot_type != pivot_b.pivot_type:
            continue

        # Calcular zona de consolidación
        low = min(pivot_a.price, pivot_b.price)
        high = max(pivot_a.price, pivot_b.price)

        # Contar contactos con los bordes
        contacts = 0
        for j in range(i + 1, len(candles)):
            if candles[j]["open_time"] >= pivot_b.time_ms:
                break
            if (candles[j]["high"] >= high - 0.01 or candles[j]["low"] <= low + 0.01):
                contacts += 1

        zones.append(ConsolidationZone(
            id=f"zone_{zone_id}",
            start_time_ms=pivot_a.time_ms,
            end_time_ms=pivot_b.time_ms,
            origin=pivot_a.pivot_type,
            low=low,
            high=high,
            contacts=contacts,
        ))
        zone_id += 1

    return zones


def calculate_fyl(
    candles: List[dict],
    lookback: int = 5,
    min_strength: float = 0.01,
) -> FYLResult:
    """Calcula el indicador FYL (First Yellow Line).

    Args:
        candles: Lista de dicts con keys: open_time, high, low, close
        lookback: Número de barras para buscar pivotes
        min_strength: Fuerza mínima para considerar un pivote

    Returns:
        FYLResult con pivots, zones y annotations
    """
    if len(candles) < lookback * 2 + 2:
        return FYLResult(pivots=[], zones=[], annotations=[])

    highs = [c["high"] for c in candles]
    lows = [c["low"] for c in candles]
    closes = [c["close"] for c in candles]

    # Detectar pivotes
    pivots = _detect_pivots(highs, lows, closes, lookback, min_strength)

    # Detectar zonas de consolidación
    zones = _detect_consolidation_zones(pivots, candles)

    # Generar anotaciones de análisis
    annotations = []
    for zone in zones:
        if zone.contacts >= 2:
            annotations.append({
                "id": f"ann_{zone.id}",
                "annotation_type": "consolidation_zone",
                "content": f"Zona de consolidación {zone.origin} con {zone.contacts} contactos",
                "zone_id": zone.id,
            })

    return FYLResult(pivots=pivots, zones=zones, annotations=annotations)
