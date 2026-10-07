"""MACD convencional con semillas SMA; referencia Decimal independiente."""

from decimal import Decimal, localcontext

import pytest
from fastapi.testclient import TestClient

import src.indicators.macd as macd_module
from src.api.main import app
from src.indicators.macd import calculate_macd


def _reference_macd(closes, fast, slow, signal):
    """Recurrencia independiente, sin llamar a calculate_macd ni a la EMA compartida."""
    with localcontext() as context:
        context.prec = 50
        values = [Decimal(str(value)) for value in closes]
        fast_value = slow_value = signal_value = None
        fast_alpha = Decimal(2) / (fast + 1)
        slow_alpha = Decimal(2) / (slow + 1)
        signal_alpha = Decimal(2) / (signal + 1)
        valid_macd, result = [], []

        for index, value in enumerate(values):
            if index + 1 == fast:
                fast_value = sum(values[:fast]) / fast
            elif index + 1 > fast:
                fast_value += fast_alpha * (value - fast_value)

            if index + 1 == slow:
                slow_value = sum(values[:slow]) / slow
            elif index + 1 > slow:
                slow_value += slow_alpha * (value - slow_value)

            if fast_value is None or slow_value is None:
                continue
            line = fast_value - slow_value
            valid_macd.append(line)
            if signal_value is None:
                if len(valid_macd) < signal:
                    continue
                signal_value = sum(valid_macd) / signal
            else:
                signal_value += signal_alpha * (line - signal_value)
            result.append((index, float(line), float(signal_value), float(line - signal_value)))
        return result


@pytest.mark.parametrize("price", [0.0, 100.0, 65_000.0])
def test_constant_prices_have_zero_macd_signal_and_histogram(price):
    points = calculate_macd([price] * 40)
    assert len(points) == 7
    assert points[0].time_ms == 33 * 60_000
    for point in points:
        assert point.macd_line == pytest.approx(0.0, abs=1e-9)
        assert point.signal_line == pytest.approx(0.0, abs=1e-9)
        assert point.histogram == pytest.approx(0.0, abs=1e-9)


@pytest.mark.parametrize("length", [0, 1, 12, 25, 26, 32, 33])
def test_default_periods_do_not_publish_before_warmup(length):
    assert calculate_macd([100.0] * length) == []


@pytest.mark.parametrize("length, count", [(34, 1), (35, 2), (50, 17)])
def test_exact_minimum_length_and_output_count(length, count):
    points = calculate_macd([100.0] * length)
    assert len(points) == count
    assert points[0].time_ms == 33 * 60_000
    assert points[-1].time_ms == (length - 1) * 60_000


def test_signal_ema_receives_only_valid_macd_values(monkeypatch):
    calls = []
    original_ema = macd_module.ema

    def record(series, period):
        calls.append((list(series), period))
        return original_ema(series, period)

    monkeypatch.setattr(macd_module, "ema", record)
    calculate_macd([float(i) for i in range(1, 41)])
    assert len(calls) == 3
    assert calls[2][1] == 9
    assert len(calls[2][0]) == 15  # índices originales 25..39, no 0..39
    assert calls[2][0] == pytest.approx([7.0] * 15, abs=1e-12)


def test_hand_calculated_small_periods():
    # EMA rápida: SMA de los primeros 2 cierres; lenta: SMA de los primeros 3.
    # MACD válido en índice 2 = 5/6 y en índice 3 = 11/9.
    # Primera señal en índice 3 = (5/6 + 11/9)/2 = 37/36.
    points = calculate_macd([1.0, 2.0, 4.0, 8.0, 16.0, 32.0],
                            fast_period=2, slow_period=3, signal_period=2)
    expected = [
        (3, 11 / 9, 37 / 36, 7 / 36),
        (4, 239 / 108, 589 / 324, 32 / 81),
        (5, 2791 / 648, 845 / 243, 1613 / 1944),
    ]
    assert len(points) == len(expected)
    for point, (index, line, signal, histogram) in zip(points, expected):
        assert point.time_ms == index * 60_000
        assert point.macd_line == pytest.approx(line, abs=1e-12)
        assert point.signal_line == pytest.approx(signal, abs=1e-12)
        assert point.histogram == pytest.approx(histogram, abs=1e-12)


@pytest.mark.parametrize("series", [
    [100 + ((i * 17) % 13) * 0.31 + i * 0.07 for i in range(80)],
    [250 - i * 0.6 + (i % 5) * 0.13 for i in range(80)],
    [100 + (i // 8) * 2 - (i % 8) * 0.3 for i in range(80)],
], ids=["oscillating", "falling", "steps"])
@pytest.mark.parametrize("fast, slow, signal", [
    (12, 26, 9), (3, 5, 2), (5, 3, 2), (1, 4, 1), (3, 3, 2),
])
def test_matches_independent_decimal_reference(series, fast, slow, signal):
    timestamps = [1_700_000_000_000 + i * 900_000 + (i % 4) * 123
                  for i in range(len(series))]
    expected = _reference_macd(series, fast, slow, signal)
    points = calculate_macd(series, timestamps=timestamps, fast_period=fast,
                            slow_period=slow, signal_period=signal)

    assert len(points) == len(expected)
    for point, (index, line, signal_value, histogram) in zip(points, expected):
        assert point.time_ms == timestamps[index]
        assert point.macd_line == pytest.approx(line, rel=1e-10, abs=1e-10)
        assert point.signal_line == pytest.approx(signal_value, rel=1e-10, abs=1e-10)
        assert point.histogram == pytest.approx(histogram, rel=1e-10, abs=1e-10)


def test_signal_period_one_is_valid_from_first_common_ema():
    closes = [float(i) for i in range(1, 27)]
    points = calculate_macd(closes, signal_period=1)
    assert len(points) == 1
    assert points[0].time_ms == 25 * 60_000
    assert points[0].macd_line == pytest.approx(7.0)
    assert points[0].signal_line == pytest.approx(7.0)
    assert points[0].histogram == pytest.approx(0.0)


def test_all_periods_one_have_no_warmup():
    points = calculate_macd([100.0, 101.0], timestamps=[123, 456],
                            fast_period=1, slow_period=1, signal_period=1)
    assert [point.time_ms for point in points] == [123, 456]
    assert all(point.macd_line == point.signal_line == point.histogram == 0.0
               for point in points)


@pytest.mark.parametrize("name", ["fast_period", "slow_period", "signal_period"])
@pytest.mark.parametrize("value", [0, -1, 2.5, True])
def test_periods_must_be_positive_integers(name, value):
    with pytest.raises(ValueError):
        calculate_macd([100.0] * 40, **{name: value})


@pytest.mark.parametrize("length, timestamp_count", [(40, 39), (40, 41), (10, 9), (0, 1)])
def test_mismatched_timestamps_are_rejected_before_calculation(length, timestamp_count):
    with pytest.raises(ValueError):
        calculate_macd([100.0] * length, timestamps=list(range(timestamp_count)))


def test_explicit_empty_input_with_empty_timestamps_is_valid():
    assert calculate_macd([], timestamps=[]) == []


def test_inputs_are_not_mutated_and_timestamps_are_preserved():
    closes = [100 + i * 0.25 for i in range(40)]
    timestamps = [10_000_000 + i * 300_000 + i ** 2 for i in range(40)]
    original_closes, original_timestamps = closes[:], timestamps[:]
    points = calculate_macd(closes, timestamps=timestamps)
    assert closes == original_closes
    assert timestamps == original_timestamps
    assert [point.time_ms for point in points] == timestamps[33:]


def test_new_prices_do_not_change_already_valid_points():
    closes = [100 + ((i * 17) % 13) * 0.31 + i * 0.07 for i in range(70)]
    complete = calculate_macd(closes)
    for length in range(34, 70, 7):
        assert calculate_macd(closes[:length]) == complete[:length - 33]


def test_analysis_api_preserves_macd_schema_and_candle_timestamps():
    timestamps = [1_700_000_000_000 + i * 900_000 for i in range(50)]
    candles = [{"open_time": timestamp, "open": 100.0, "high": 101.0,
                "low": 99.0, "close": 100.0, "volume": 10.0}
               for timestamp in timestamps]
    with TestClient(app) as client:
        response = client.post("/api/analysis", json={
            "symbol": "BTC/USDT", "timeframe": "15m", "candles_count": 50,
            "candles": candles,
        })
    assert response.status_code == 200
    data = response.json()
    assert data["data_source"] == "provided"
    points = data["macd"]["points"]
    assert len(points) == 17
    assert [point["time_ms"] for point in points] == timestamps[33:]
    assert all(set(point) == {"time_ms", "macd_line", "signal_line", "histogram"}
               for point in points)
    assert all(point["macd_line"] == point["signal_line"] == point["histogram"] == 0.0
               for point in points)
