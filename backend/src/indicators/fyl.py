# FYL (First Yellow Line) indicator — Detección de pivotes y zonas de consolidación.
"""
AVISO IMPORTANTE: La detección incremental (default) solo mira hacia atrás, por lo que
los pivotes confirmados pueden refinarse en barras posteriores. Las señales basadas en
pivotes retrospectivos (modo batch) son TEST_ONLY y NO deben usarse para operaciones en vivo.

Según el plan (sección 3, requisito #6): los pivotes solo pueden confirmarse sin usar datos futuros.
"""

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
    """Un punto de pivote detectado.

    Si confirmada=True, el pivote fue verificado con datos retrospectivos
    (modo batch TEST_ONLY). Si confirmada=False, es provisional y puede refinarse.
    """
    time_ms: int
    price: float
    pivot_type: PivotType
    strength: float  # Fuerza del pivote (0-1)
    confirmed: bool = False  # True si fue verificado retrospectivamente


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
    mode: str = "incremental"  # "incremental" | "batch_test_only"


def _detect_pivots_incremental(
    highs: List[float],
    lows: List[float],
    lookback: int = 5,
    min_strength: float = 0.01,
    *,
    open_times: Optional[List[int]] = None,
) -> List[PivotPoint]:
    """Detecta pivotes de forma INCREMENTAL (seguro para tiempo real).

    Un punto se considera máximo/mínimo local solo mirando hacia ATRÁS:
    es mayor/menor que los `lookback` puntos anteriores. No mira al futuro.

    Los pivotes detectados son PROVISIONALES y pueden ser refinados en barras posteriores.
    """
    pivots: List[PivotPoint] = []
    times = open_times if open_times is not None else [i * 60_000 for i in range(len(highs))]

    for i in range(lookback, len(highs)):
        # ── Verificar si es un máximo local (solo mirando atrás) ──
        is_high = True
        for j in range(i - lookback, i):
            if highs[j] >= highs[i]:
                is_high = False
                break

        if is_high:
            # Calcular fuerza basada en los vecinos anteriores
            left_range = max(highs[max(0, i - lookback):i]) - highs[i]
            strength = left_range / highs[i] if highs[i] > 0 else 0.0
            if strength >= min_strength:
                pivots.append(PivotPoint(
                    time_ms=times[i],
                    price=highs[i],
                    pivot_type=PivotType.HIGH,
                    strength=round(strength, 4),
                    confirmed=False,
                ))

        # ── Verificar si es un mínimo local (solo mirando atrás) ──
        is_low = True
        for j in range(i - lookback, i):
            if lows[j] <= lows[i]:
                is_low = False
                break

        if is_low:
            left_range = lows[i] - min(lows[max(0, i - lookback):i])
            strength = left_range / abs(lows[i]) if lows[i] != 0 else 0.0
            if strength >= min_strength:
                pivots.append(PivotPoint(
                    time_ms=times[i],
                    price=lows[i],
                    pivot_type=PivotType.LOW,
                    strength=round(strength, 4),
                    confirmed=False,
                ))

    return pivots


def _detect_pivots_batch(
    highs: List[float],
    lows: List[float],
    lookback: int = 5,
    min_strength: float = 0.01,
    *,
    open_times: Optional[List[int]] = None,
) -> List[PivotPoint]:
    """Detecta pivotes retrospectivamente (TEST_ONLY — usa datos futuros).

    UNO SOLO debe usar este modo para backtesting por lotes con datos históricos.
    Los pivotes marcados como confirmados pueden ser revisados en tiempo real.

    WARNING: lookahead bias — no usar para señales en vivo ni replay.
    """
    pivots: List[PivotPoint] = []
    times = open_times if open_times is not None else [i * 60_000 for i in range(len(highs))]

    for i in range(lookback, len(highs) - lookback):
        # Verificar si es un máximo local (ambos lados)
        is_high = True
        for j in range(i - lookback, i + lookback + 1):
            if j != i and highs[j] >= highs[i]:
                is_high = False
                break

        if is_high:
            strength = max(
                (highs[i] - min(highs[max(0, i - lookback):i])) / highs[i],
                (highs[i] - min(highs[i + 1:min(len(highs), i + lookback + 1)])) / highs[i],
            )
            if strength >= min_strength:
                pivots.append(PivotPoint(
                    time_ms=times[i],
                    price=highs[i],
                    pivot_type=PivotType.HIGH,
                    strength=round(strength, 4),
                    confirmed=True,
                ))

        # Verificar si es un mínimo local (ambos lados)
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
                    time_ms=times[i],
                    price=lows[i],
                    pivot_type=PivotType.LOW,
                    strength=round(strength, 4),
                    confirmed=True,
                ))

    return pivots


def _detect_consolidation_zones(
    pivots: List[PivotPoint],
    candles: List[dict],
) -> List[ConsolidationZone]:
    """Detecta zonas de consolidación entre pivotes consecutivos del mismo tipo.

    Los contactos se cuentan con timestamps, no índices, para evitar desalineación.
    """
    zones: List[ConsolidationZone] = []
    zone_id = 0

    for i in range(len(pivots) - 1):
        pivot_a = pivots[i]
        pivot_b = pivots[i + 1]

        # Solo considerar pares del mismo tipo (high-high o low-low)
        if pivot_a.pivot_type != pivot_b.pivot_type:
            continue

        # Calcular zona de consolidación
        zone_low = min(pivot_a.price, pivot_b.price)
        zone_high = max(pivot_a.price, pivot_b.price)

        # Contar contactos con los bordes usando timestamps (no índices)
        contacts = 0
        for candle in candles:
            ot = candle["open_time"]
            if ot <= pivot_a.time_ms:
                continue
            if ot >= pivot_b.time_ms:
                break
            if (candle["high"] >= zone_high - 0.01 or candle["low"] <= zone_low + 0.01):
                contacts += 1

        zones.append(ConsolidationZone(
            id=f"zone_{zone_id}",
            start_time_ms=pivot_a.time_ms,
            end_time_ms=pivot_b.time_ms,
            origin=pivot_a.pivot_type,
            low=round(zone_low, 2),
            high=round(zone_high, 2),
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

    Por defecto usa detección INCREMENTAL (solo mirando hacia atrás), segura para señales en vivo.
    Los pivotes incrementales son PROVISIONALES y pueden refinarse en barras posteriores.

    Para backtesting retrospectivo, usar `calculate_fyl_batch` (TEST_ONLY).

    Args:
        candles: Lista de dicts con keys: open_time, high, low, close
        lookback: Número de barras para buscar pivotes
        min_strength: Fuerza mínima para considerar un pivote

    Returns:
        FYLResult con pivots, zones y annotations. mode indica el modo usado.
    """
    if len(candles) < lookback + 1:
        return FYLResult(pivots=[], zones=[], annotations=[], mode="incremental")

    highs = [c["high"] for c in candles]
    lows = [c["low"] for c in candles]
    open_times = [c["open_time"] for c in candles]

    # ── Modo incremental (default, seguro para tiempo real) ──
    pivots = _detect_pivots_incremental(highs, lows, lookback, min_strength, open_times=open_times)
    zones = _detect_consolidation_zones(pivots, candles)

    annotations = []
    for zone in zones:
        if zone.contacts >= 2:
            annotations.append({
                "id": f"ann_{zone.id}",
                "annotation_type": "consolidation_zone",
                "content": f"Zona de consolidación {zone.origin} con {zone.contacts} contactos",
                "zone_id": zone.id,
            })

    return FYLResult(
        pivots=pivots,
        zones=zones,
        annotations=annotations,
        mode="incremental",
    )


def calculate_fyl_batch(
    candles: List[dict],
    lookback: int = 5,
    min_strength: float = 0.01,
) -> FYLResult:
    """Versión retrospectiva de calculate_fyl — TEST_ONLY para backtesting por lotes.

    Usa lookahead bias (mira hacia adelante), por lo que los pivotes confirmados
    NO pueden replicarse en tiempo real. Marcados con confirmed=True.

    WARNING: lookahead bias — no usar para señales en vivo ni replay.
    """
    if len(candles) < lookback * 2 + 2:
        return FYLResult(pivots=[], zones=[], annotations=[], mode="batch_test_only")

    highs = [c["high"] for c in candles]
    lows = [c["low"] for c in candles]
    open_times = [c["open_time"] for c in candles]

    pivots = _detect_pivots_batch(highs, lows, lookback, min_strength, open_times=open_times)
    zones = _detect_consolidation_zones(pivots, candles)

    annotations = []
    for zone in zones:
        if zone.contacts >= 2:
            annotations.append({
                "id": f"ann_{zone.id}",
                "annotation_type": "consolidation_zone",
                "content": f"Zona de consolidación {zone.origin} con {zone.contacts} contactos",
                "zone_id": zone.id,
            })

    return FYLResult(
        pivots=pivots,
        zones=zones,
        annotations=annotations,
        mode="batch_test_only",
    )
