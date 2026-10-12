# TASK 05 — FYL INCREMENTAL SOURCE TIMESTAMP
"""Verifica que los pivotes incrementales usan el open_time real de su vela, no i*60_000."""

import pytest
from src.indicators.fyl import calculate_fyl, calculate_fyl_batch


def test_incremental_pivots_preserve_real_open_time_with_nonzero_start_and_irregular_intervals():
    """Caso incremental con timestamp inicial no cero e intervalos irregulares.

    Cada pivote detectado conserva el open_time de su índice de origen.
    No se cambia selección, lookback, price, strength, confirmed ni mode.
    """
    # Datos con timestamps irregulares y offset no cero; incluye máximos/mínimos locales claros
    candles = [
        {"open_time": 1_700_000_100_000, "high": 100.0, "low": 50.0, "close": 98.0},
        {"open_time": 1_700_000_160_000, "high": 105.0, "low": 48.0, "close": 101.0},
        {"open_time": 1_700_000_230_000, "high": 110.0, "low": 46.0, "close": 104.0},
        {"open_time": 1_700_000_305_000, "high": 120.0, "low": 44.0, "close": 108.0},   # idx=3
        {"open_time": 1_700_000_390_000, "high": 108.0, "low": 42.0, "close": 95.0},
        {"open_time": 1_700_000_485_000, "high": 95.0, "low": 38.0, "close": 85.0},
        {"open_time": 1_700_000_590_000, "high": 82.0, "low": 35.0, "close": 78.0},
        {"open_time": 1_700_000_700_000, "high": 75.0, "low": 30.0, "close": 72.0},   # idx=7
        {"open_time": 1_700_000_815_000, "high": 80.0, "low": 33.0, "close": 76.0},
        {"open_time": 1_700_000_935_000, "high": 85.0, "low": 36.0, "close": 80.0},
    ]

    result = calculate_fyl(candles, lookback=3, min_strength=-1)

    # Verificar que el modo no cambió
    assert result.mode == "incremental"
    assert len(result.pivots) > 0

    # Comprobar correspondencia exacta: HIGH del índice 3 y LOW del índice 7
    high_pivot_idx3 = None
    low_pivot_idx7 = None
    for pivot in result.pivots:
        if pivot.pivot_type.value == "high" and pivot.time_ms == candles[3]["open_time"]:
            high_pivot_idx3 = pivot
        if pivot.pivot_type.value == "low" and pivot.time_ms == candles[7]["open_time"]:
            low_pivot_idx7 = pivot

    assert high_pivot_idx3 is not None, "No se encontró HIGH en candles[3]['open_time']"
    assert low_pivot_idx7 is not None, "No se encontró LOW en candles[7]['open_time']"
    assert high_pivot_idx3.time_ms == candles[3]["open_time"]
    assert low_pivot_idx7.time_ms == candles[7]["open_time"]

    # Todos los pivotes deben conservar su open_time real de la vela correspondiente
    for pivot in result.pivots:
        found = any(c["open_time"] == pivot.time_ms for c in candles)
        assert found, (
            f"Pivot time_ms={pivot.time_ms} no corresponde a ningún open_time de vela. "
            f"open_times={[c['open_time'] for c in candles]}"
        )


# TASK 06 — FYL BATCH SOURCE TIMESTAMP
"""Verifica que los pivotes batch TEST_ONLY usan el open_time real de su vela, no i*60_000."""


def test_batch_pivots_preserve_real_open_time_with_nonzero_start_and_irregular_intervals():
    """Caso batch con timestamp inicial no cero e intervalos irregulares.

    Produce dos pivotes consecutivos del mismo tipo (LOW) dentro de los índices válidos
    [lookback, len-lookback-1] = [3, 8]. Afirma timestamps exactos y zonas derivadas.
    """
    # lookback=3 → rango válido del detector batch: [3, len-4] = [3, 8].
    # Para dos LOWs consecutivos en idx=3 y idx=7 (fuera del range de vista uno del otro):
    #   idx=3 necesita lows[0..2,4..6] > lows[3]
    #   idx=7 necesita lows[4..6,8..10] > lows[7]
    candles = [
        {"open_time": 1_700_000_100_000, "high": 100.0, "low": 50.0, "close": 98.0},
        {"open_time": 1_700_000_160_000, "high": 105.0, "low": 48.0, "close": 101.0},
        {"open_time": 1_700_000_230_000, "high": 110.0, "low": 46.0, "close": 104.0},
        {"open_time": 1_700_000_305_000, "high": 95.0, "low": 35.0, "close": 88.0},   # idx=3: LOW pivot
        {"open_time": 1_700_000_390_000, "high": 108.0, "low": 47.0, "close": 95.0},   # idx=4: sin pivote
        {"open_time": 1_700_000_485_000, "high": 92.0, "low": 49.0, "close": 85.0},    # idx=5: sin pivote
        {"open_time": 1_700_000_590_000, "high": 110.0, "low": 46.0, "close": 104.0},
        {"open_time": 1_700_000_700_000, "high": 90.0, "low": 30.0, "close": 82.0},    # idx=7: LOW pivot
        {"open_time": 1_700_000_815_000, "high": 110.0, "low": 42.0, "close": 104.0},
        {"open_time": 1_700_000_935_000, "high": 105.0, "low": 38.0, "close": 100.0},
        {"open_time": 1_700_001_060_000, "high": 110.0, "low": 46.0, "close": 104.0},
        {"open_time": 1_700_001_190_000, "high": 115.0, "low": 44.0, "close": 108.0},
    ]

    result = calculate_fyl_batch(candles, lookback=3, min_strength=-1)

    # Verificar que el modo batch se mantiene
    assert result.mode == "batch_test_only"
    assert len(result.pivots) > 0

    # Filtrar los dos LOWs consecutivos dentro del rango válido [3, 8]
    low_pivots = [p for p in result.pivots if p.pivot_type.value == "low"]
    assert len(low_pivots) >= 2

    low_idx3 = None
    low_idx7 = None
    for p in low_pivots:
        if p.time_ms == candles[3]["open_time"]:
            low_idx3 = p
        if p.time_ms == candles[7]["open_time"]:
            low_idx7 = p

    # Afirma los timestamps exactos de los pivotes LOW en idx=3 y idx=7
    assert low_idx3 is not None, "No se encontró LOW batch en candles[3]['open_time']"
    assert low_idx7 is not None, "No se encontró LOW batch en candles[7]['open_time']"
    assert low_idx3.time_ms == candles[3]["open_time"]
    assert low_idx7.time_ms == candles[7]["open_time"]

    # Todos los pivotes deben conservar su open_time real de la vela correspondiente
    for pivot in result.pivots:
        found = any(c["open_time"] == pivot.time_ms for c in candles)
        assert found, (
            f"Pivot time_ms={pivot.time_ms} no corresponde a ningún open_time de vela. "
            f"open_times={[c['open_time'] for c in candles]}"
        )

    # Verificar que confirmed=True para todos los pivotes batch
    for pivot in result.pivots:
        assert pivot.confirmed is True

    # len(result.zones) > 0 y start/end coinciden con open_time de origen
    assert len(result.zones) > 0
    for zone in result.zones:
        start_found = any(p.time_ms == zone.start_time_ms for p in result.pivots)
        end_found = any(p.time_ms == zone.end_time_ms for p in result.pivots)
        assert start_found, f"Zone start_time_ms={zone.start_time_ms} no coincide con ningún pivote"
        assert end_found, f"Zone end_time_ms={zone.end_time_ms} no coincide con ningún pivote"


# TASK 01 — FYL INCREMENTAL STRENGTH SIGN REGRESSION
"""Verifica que el signo de la fuerza en pivotes incrementales es correcto:
máximos y mínimos locales válidos superan el umbral predeterminado (0.01)."""


def test_incremental_strength_sign_default_min_strength():
    """Caso incremental con min_strength predeterminado (0.01).

    - timestamps no cero e irregulares;
    - un máximo y un mínimo local que superen 0.01;
    - comprobar pivote, timestamp de origen y strength positiva/umbral;
    - ampliar la misma entrada con barras futuras y comprobar que los pivotes ya
      observables conservan tipo, tiempo, precio y strength.
    """
    # Datos con timestamps irregulares y offset no cero.
    # lookback=3 → el primer índice posible es i=3.
    # idx=3: high=150 > max(100,105,110)=110? Sí (150>110). Es HIGH local.
    #   strength = (150 - 110) / 150 = 40/150 ≈ 0.2667 > 0.01 ✓
    # idx=3: low=90 > min(30,28,26)=26? Sí → no es LOW local (no es mínimo).
    # idx=7: high=130 < max(140,135,145)=145 → no es HIGH local.
    #   low=80 < min(85,88,92)=85? Sí → es LOW local.
    #   strength = (85 - 80) / 80 = 5/80 = 0.0625 > 0.01 ✓
    candles = [
        {"open_time": 1_700_000_100_000, "high": 100.0, "low": 30.0, "close": 95.0},
        {"open_time": 1_700_000_160_000, "high": 105.0, "low": 28.0, "close": 101.0},
        {"open_time": 1_700_000_230_000, "high": 110.0, "low": 26.0, "close": 104.0},
        {"open_time": 1_700_000_305_000, "high": 150.0, "low": 90.0, "close": 140.0},   # idx=3: HIGH local
        {"open_time": 1_700_000_390_000, "high": 140.0, "low": 85.0, "close": 135.0},
        {"open_time": 1_700_000_485_000, "high": 135.0, "low": 88.0, "close": 130.0},
        {"open_time": 1_700_000_590_000, "high": 145.0, "low": 92.0, "close": 140.0},
        {"open_time": 1_700_000_700_000, "high": 130.0, "low": 80.0, "close": 125.0},   # idx=7: LOW local
    ]

    # ── Primera llamada con min_strength predeterminado (0.01) y lookback=3 ──
    result = calculate_fyl(candles, lookback=3)

    assert result.mode == "incremental"
    assert len(result.pivots) > 0, "Se esperaba al menos un pivote con min_strength=0.01"

    # Encontrar el HIGH en idx=3 y LOW en idx=7
    high_pivot = None
    low_pivot = None
    for p in result.pivots:
        if p.pivot_type.value == "high" and p.time_ms == candles[3]["open_time"]:
            high_pivot = p
        if p.pivot_type.value == "low" and p.time_ms == candles[7]["open_time"]:
            low_pivot = p

    assert high_pivot is not None, "No se encontró HIGH pivot en candles[3]['open_time']"
    assert low_pivot is not None, "No se encontró LOW pivot en candles[7]['open_time']"

    # Verificar timestamps exactos
    assert high_pivot.time_ms == candles[3]["open_time"]
    assert low_pivot.time_ms == candles[7]["open_time"]

    # Verificar precios exactos
    assert high_pivot.price == candles[3]["high"]
    assert low_pivot.price == candles[7]["low"]

    # Verificar strength positiva y sobre umbral predeterminado 0.01
    assert high_pivot.strength > 0.0, f"strength HIGH debe ser positiva, got {high_pivot.strength}"
    assert high_pivot.strength >= 0.01, f"strength HIGH {high_pivot.strength} < umbral 0.01"
    assert low_pivot.strength > 0.0, f"strength LOW debe ser positiva, got {low_pivot.strength}"
    assert low_pivot.strength >= 0.01, f"strength LOW {low_pivot.strength} < umbral 0.01"

    # Verificar que confirmed=False (incremental)
    assert high_pivot.confirmed is False
    assert low_pivot.confirmed is False

    # ── Ampliar con barras futuras y comprobar estabilidad de pivotes observados ──
    future_candles = [
        {"open_time": 1_700_000_815_000, "high": 132.0, "low": 82.0, "close": 128.0},
        {"open_time": 1_700_000_935_000, "high": 140.0, "low": 85.0, "close": 135.0},
        {"open_time": 1_700_001_060_000, "high": 145.0, "low": 88.0, "close": 140.0},
    ]
    extended_candles = candles + future_candles
    result_extended = calculate_fyl(extended_candles, lookback=3)

    assert result_extended.mode == "incremental"

    # Los pivotes ya observables deben conservar tipo, tiempo, precio y strength
    high_pivot_ext = None
    low_pivot_ext = None
    for p in result_extended.pivots:
        if p.pivot_type.value == "high" and p.time_ms == candles[3]["open_time"]:
            high_pivot_ext = p
        if p.pivot_type.value == "low" and p.time_ms == candles[7]["open_time"]:
            low_pivot_ext = p

    assert high_pivot_ext is not None, "HIGH pivot desapareció tras extender con barras futuras"
    assert low_pivot_ext is not None, "LOW pivot desapareció tras extender con barras futuras"

    assert high_pivot_ext.pivot_type == high_pivot.pivot_type
    assert high_pivot_ext.time_ms == high_pivot.time_ms
    assert high_pivot_ext.price == high_pivot.price
    assert high_pivot_ext.strength == high_pivot.strength

    assert low_pivot_ext.pivot_type == low_pivot.pivot_type
    assert low_pivot_ext.time_ms == low_pivot.time_ms
    assert low_pivot_ext.price == low_pivot.price
    assert low_pivot_ext.strength == low_pivot.strength
