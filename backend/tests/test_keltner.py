"""Semántica de datos insuficientes / warmup de Keltner.

Sin validar fórmula ni referencia numérica del Keltner original.
Solo comprueba longitud de salida y mapeo temporal para 33/34/35 velas.
"""

import pytest

from src.indicators.keltner import calculate_keltner


def _make_candles(count: int, base_time_ms: int = 1_700_000_000_000) -> list[dict]:
    """Genera `count` velas con OHLC simples y open_time creciente de 60_000 ms."""
    return [
        {
            "open_time": base_time_ms + i * 60_000,
            "high": 100.0 + i,
            "low": 90.0 + i,
            "close": 95.0 + i,
        }
        for i in range(count)
    ]


@pytest.mark.parametrize("count, expected_len", [
    (33, 0),
    (34, 1),
    (35, 2),
])
def test_keltner_warmup_length(count: int, expected_len: int) -> None:
    """Con defaults (ema_period=20, atr_period=14), mínimo = 34 velas.

    - 33 velas → lista vacía (no ceros ni fallback sintético).
    - 34 velas → 1 punto; 35 velas → 2 puntos.
    """
    candles = _make_candles(count)
    result = calculate_keltner(candles)
    assert len(result) == expected_len


def test_keltner_warmup_timestamps_map_to_candles_34() -> None:
    """Con 34 velas, el único punto debe tener time_ms de candles[33]."""
    candles = _make_candles(34)
    result = calculate_keltner(candles)
    assert len(result) == 1
    assert result[0].time_ms == candles[33]["open_time"]


def test_keltner_warmup_timestamps_map_to_candles_35() -> None:
    """Con 35 velas, los dos puntos deben mapear a candles[33] y candles[34]."""
    candles = _make_candles(35)
    result = calculate_keltner(candles)
    assert len(result) == 2
    assert result[0].time_ms == candles[33]["open_time"]
    assert result[1].time_ms == candles[34]["open_time"]


# ---------------------------------------------------------------------------
# Referencia numérica aprobada para la implementación actual de Keltner
# (contrato REV-001 / ACT-S-006)
# ---------------------------------------------------------------------------

_KELTNER_REF_OHLCV = [
    {"open": 9.0, "high": 10.0, "low": 8.0, "close": 9.0},
    {"open": 10.0, "high": 12.0, "low": 9.0, "close": 11.0},
    {"open": 11.0, "high": 13.0, "low": 10.0, "close": 12.0},
    {"open": 12.0, "high": 14.0, "low": 11.0, "close": 13.0},
    {"open": 14.0, "high": 16.0, "low": 12.0, "close": 15.0},
    {"open": 14.0, "high": 15.0, "low": 12.0, "close": 13.0},
    {"open": 13.0, "high": 17.0, "low": 13.0, "close": 16.0},
    {"open": 16.0, "high": 18.0, "low": 14.0, "close": 17.0},
]

_KELTNER_REF_BASE_TIME = 1_699_999_980_000  # minuto-alineado


def _make_keltner_ref_candles() -> list[dict]:
    """Construye las 8 velas OHLCV de referencia (1m) con open_time creciente."""
    return [
        {
            "open_time": _KELTNER_REF_BASE_TIME + i * 60_000,
            "open": row["open"],
            "high": row["high"],
            "low": row["low"],
            "close": row["close"],
            "volume": 100.0 + i,
        }
        for i, row in enumerate(_KELTNER_REF_OHLCV)
    ]


def test_keltner_reference_numeric_contract() -> None:
    """Valida la referencia numérica aprobada para la implementación actual de Keltner.

    Fixture OHLCV (8 velas 1m): open_time[i] = 1_699_999_980_000 + i*60_000.
    Parámetros de test: ema_period=3, atr_period=3, multiplier=2.0.

    Expected (racionales exactos, verificados manualmente):
      i=5: EMA=317/24, ATR=79/27, upper=4117/216, lower=1589/216
      i=6: EMA=701/48, ATR=266/81, upper=27439/1296, lower=10415/1296
      i=7: EMA=1517/96, ATR=856/243, upper=177661/7776, lower=68093/7776

    Convenciones de la implementación actual (NO especificación aprobada del método
    Keltner original):
      - TR[0]=0; para i>0: max(H-L, abs(H-Cprev), abs(L-Cprev)).
      - EMA: SMA de los primeros n closes en índice n-1; alpha=2/(n+1).
      - ATR: promedio simple de primeros p true_ranges (incluye TR[0]=0);
        recurrencia Wilder: (ATR[i-1]*(p-1)+TR[i])/p.
      - Bandas: upper=EMA+multiplier*ATR; lower=EMA-multiplier*ATR.
      - Warmup: len < ema_period+atr_period → []; primer índice = ema_period+atr_period-1.

    Este test NO ejecuta estrategia alguna (strategy_version=N/A, risk_profile=N/A).
    Dataset: keltner-reference-ohlcv-8bar-v1.
    """
    candles = _make_keltner_ref_candles()
    result = calculate_keltner(candles, ema_period=3, atr_period=3, multiplier=2.0)

    # Deben generarse 3 puntos (índices 5, 6, 7)
    assert len(result) == 3

    # --- i=5 ---------------------------------------------------------------
    p5 = result[0]
    assert p5.time_ms == _KELTNER_REF_BASE_TIME + 5 * 60_000
    assert p5.ema_val == pytest.approx(317 / 24, rel=1e-12, abs=1e-12)
    assert p5.atr == pytest.approx(79 / 27, rel=1e-12, abs=1e-12)
    assert p5.upper == pytest.approx(4117 / 216, rel=1e-12, abs=1e-12)
    assert p5.lower == pytest.approx(1589 / 216, rel=1e-12, abs=1e-12)

    # --- i=6 ---------------------------------------------------------------
    p6 = result[1]
    assert p6.time_ms == _KELTNER_REF_BASE_TIME + 6 * 60_000
    assert p6.ema_val == pytest.approx(701 / 48, rel=1e-12, abs=1e-12)
    assert p6.atr == pytest.approx(266 / 81, rel=1e-12, abs=1e-12)
    assert p6.upper == pytest.approx(27439 / 1296, rel=1e-12, abs=1e-12)
    assert p6.lower == pytest.approx(10415 / 1296, rel=1e-12, abs=1e-12)

    # --- i=7 ---------------------------------------------------------------
    p7 = result[2]
    assert p7.time_ms == _KELTNER_REF_BASE_TIME + 7 * 60_000
    assert p7.ema_val == pytest.approx(1517 / 96, rel=1e-12, abs=1e-12)
    assert p7.atr == pytest.approx(856 / 243, rel=1e-12, abs=1e-12)
    assert p7.upper == pytest.approx(177661 / 7776, rel=1e-12, abs=1e-12)
    assert p7.lower == pytest.approx(68093 / 7776, rel=1e-12, abs=1e-12)
