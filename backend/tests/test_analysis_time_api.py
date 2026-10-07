"""Pruebas aisladas del guard de open_time estrictamente creciente en POST /api/analysis.

FastAPI aislado solo analysis.router, TestClient context manager y guardas
HTTP/socket aceptadas (AF_UNIX permitido). No main.app/lifespan ni servicios.
"""

from __future__ import annotations

import httpx
import socket
from unittest import mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Autouse fixture: bloqueo de red para toda la suite
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _block_network(monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError("Real network is forbidden in these tests")

    async def blocked_async(*args, **kwargs):
        blocked()

    original_connect = socket.socket.connect
    original_connect_ex = socket.socket.connect_ex

    def guarded_connect(sock, address):
        if sock.family != socket.AF_UNIX:
            blocked()
        return original_connect(sock, address)

    def guarded_connect_ex(sock, address):
        if sock.family != socket.AF_UNIX:
            blocked()
        return original_connect_ex(sock, address)

    monkeypatch.setattr(socket, "getaddrinfo", blocked)
    monkeypatch.setattr(socket, "create_connection", blocked)
    monkeypatch.setattr(socket.socket, "connect", guarded_connect)
    monkeypatch.setattr(socket.socket, "connect_ex", guarded_connect_ex)
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", blocked)
    monkeypatch.setattr(
        httpx.AsyncHTTPTransport, "handle_async_request", blocked_async
    )


# ---------------------------------------------------------------------------
# Montaje: app minima con solo el router de análisis
# ---------------------------------------------------------------------------


def _build_app():
    """Monta analysis.router en un FastAPI aislado."""
    from src.api.analysis import router as analysis_router  # noqa: F401

    app = FastAPI()
    app.include_router(analysis_router)
    client = TestClient(app)

    def _restore():
        client.close()

    return client, _restore


# ---------------------------------------------------------------------------
# Helpers para generar velas validas de test
# ---------------------------------------------------------------------------


def _make_50_candles(open_times=None):
    """Genera 50 velas con OHLC100/101/99/100, volume0.

    Si open_times es None usa timestamps estrictamente crecientes uniformes.
    """
    base_ms = 1700000000000
    if open_times is None:
        open_times = [base_ms + i * 60000 for i in range(50)]
    candles = []
    for i in range(50):
        candles.append({
            "open_time": open_times[i],
            "open": 100.0,
            "high": 101.0,
            "low": 99.0,
            "close": 100.0,
            "volume": 0.0,
        })
    return candles


def _make_60_candles(open_times=None):
    """Genera 60 velas con OHLC100/101/99/100, volume0."""
    base_ms = 1700000000000
    if open_times is None:
        open_times = [base_ms + i * 60000 for i in range(60)]
    candles = []
    for i in range(60):
        candles.append({
            "open_time": open_times[i],
            "open": 100.0,
            "high": 101.0,
            "low": 99.0,
            "close": 100.0,
            "volume": 0.0,
        })
    return candles


# ---------------------------------------------------------------------------
# Test 1: Cuatro negativos parametrizados — open_time duplicado o descendente
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("dup_index,dup_type", [
    (1, "duplicate"),   # índice1 igual al anterior
    (10, "duplicate"),  # índice10 igual al anterior
    (1, "descend"),     # índice1 menor que el anterior
    (10, "descend"),    # índice10 menor que el anterior
])
def test_non_increasing_time_parametrized(dup_index, dup_type):
    """open_time no creciente en índice1 o 10 → 422 body EXACTO."""
    client, restore = _build_app()
    try:
        base_ms = 1700000000000
        open_times = [base_ms + i * 60000 for i in range(50)]

        if dup_type == "duplicate":
            open_times[dup_index] = open_times[dup_index - 1]
        else:
            open_times[dup_index] = open_times[dup_index - 1] - 1

        candles = []
        for i in range(50):
            candles.append({
                "open_time": open_times[i],
                "open": 100.0,
                "high": 101.0,
                "low": 99.0,
                "close": 100.0,
                "volume": 0.0,
            })

        resp = client.post("/api/analysis", json={
            "symbol": "BTC/USDT",
            "timeframe": "1m",
            "candles_count": 50,
            "candles": candles,
        })

        assert resp.status_code == 422
        detail = resp.json()["detail"]
        expected_detail = {"code": "NON_INCREASING_CANDLE_TIME", "index": dup_index}
        assert detail == expected_detail

        # Ningún indicador debe calcularse
        with mock.patch("src.api.analysis.calculate_keltner") as mock_k:
            with mock.patch("src.api.analysis.calculate_macd") as mock_m:
                with mock.patch("src.api.analysis.calculate_fyl") as mock_f:
                    resp2 = client.post("/api/analysis", json={
                        "symbol": "BTC/USDT",
                        "timeframe": "1m",
                        "candles_count": 50,
                        "candles": candles,
                    })
                    assert resp2.status_code == 422
                    mock_k.assert_not_called()
                    mock_m.assert_not_called()
                    mock_f.assert_not_called()
    finally:
        restore()


# ---------------------------------------------------------------------------
# Test 2: Positivo con 50 tiempos estrictamente crecientes pero IRREGULARES
# ---------------------------------------------------------------------------


def test_positive_irregular_timestamps():
    """Tiempos irregulares pero crecientes → HTTP200, indicadores REALES."""
    client, restore = _build_app()
    try:
        base_ms = 1700000000000
        open_times = [base_ms + i * 60000 + i * i for i in range(50)]

        candles = []
        for i in range(50):
            candles.append({
                "open_time": open_times[i],
                "open": 100.0,
                "high": 101.0,
                "low": 99.0,
                "close": 100.0,
                "volume": 0.0,
            })

        resp = client.post("/api/analysis", json={
            "symbol": "BTC/USDT",
            "timeframe": "1m",
            "candles_count": 50,
            "candles": candles,
        })

        assert resp.status_code == 200
        data = resp.json()

        expected_keys = {"symbol", "timeframe", "data_source", "keltner", "macd", "fyl"}
        assert set(data.keys()) == expected_keys
        assert data["symbol"] == "BTC/USDT"
        assert data["timeframe"] == "1m"
        assert data["data_source"] == "provided"

        # MACD: 17 puntos con TODA la secuencia timestamps[33:50] y mismas keys
        macd_points = data["macd"]["points"]
        assert len(macd_points) == 17

        expected_keys_per_point = {"time_ms", "macd_line", "signal_line", "histogram"}
        for pt in macd_points:
            assert set(pt.keys()) == expected_keys_per_point

        # Verificar TODOS los timestamps MACD con la secuencia irregular
        expected_timestamps = [base_ms + i * 60000 + i * i for i in range(33, 50)]
        actual_timestamps = [pt["time_ms"] for pt in macd_points]
        assert actual_timestamps == expected_timestamps
    finally:
        restore()


# ---------------------------------------------------------------------------
# Test 3: Cola no seleccionada — 60 velas, candles_count=50, duplicado en índice55
# ---------------------------------------------------------------------------


def test_unselected_tail_duplicate():
    """Duplicado en índice55 de 60 velas → 422 con índice55, no ignorar la cola."""
    client, restore = _build_app()
    try:
        base_ms = 1700000000000
        open_times = [base_ms + i * 60000 for i in range(60)]
        # Duplicar el tiempo en índice55 (igual al anterior)
        open_times[55] = open_times[54]

        candles = []
        for i in range(60):
            candles.append({
                "open_time": open_times[i],
                "open": 100.0,
                "high": 101.0,
                "low": 99.0,
                "close": 100.0,
                "volume": 0.0,
            })

        resp = client.post("/api/analysis", json={
            "symbol": "BTC/USDT",
            "timeframe": "1m",
            "candles_count": 50,
            "candles": candles,
        })

        assert resp.status_code == 422
        detail = resp.json()["detail"]
        expected_detail = {"code": "NON_INCREASING_CANDLE_TIME", "index": 55}
        assert detail == expected_detail
    finally:
        restore()
