"""Pruebas aisladas del guard de rango OHLC en POST /api/analysis.

FastAPI aislado solo analysis.router, TestClient context manager y guardas
HTTP/socket ya aprobadas (AF_UNIX permitido). No main.app/lifespan ni servicios.
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


def _make_50_candles(open_val: float = 100.0, high_val: float = 101.0,
                     low_val: float = 99.0, close_val: float = 100.0):
    """Genera 50 velas con OHLC constantes y timestamps incrementales."""
    base_ts = 1700000000000
    candles = []
    for i in range(50):
        candles.append({
            "open_time": base_ts + i * 60000,
            "open": open_val,
            "high": high_val,
            "low": low_val,
            "close": close_val,
            "volume": 0.0,
        })
    return candles


# ---------------------------------------------------------------------------
# Test 1: Cuatro negativos parametrizados — open/close fuera de [low,high]
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("field_name,value", [
    ("open", 98.0),   # por debajo de low=99
    ("open", 102.0),  # por encima de high=101
    ("close", 98.0),  # por debajo de low=99
    ("close", 102.0), # por encima de high=101
])
def test_price_out_of_range_parametrized(field_name, value):
    """open o close fuera de [low,high] en índice10 → 422 body EXACTO."""
    client, restore = _build_app()
    try:
        candles = _make_50_candles()
        candles[10][field_name] = value

        resp = client.post("/api/analysis", json={
            "symbol": "BTC/USDT",
            "timeframe": "1m",
            "candles_count": 50,
            "candles": candles,
        })

        assert resp.status_code == 422
        detail = resp.json()["detail"]
        expected_detail = {"code": "CANDLE_PRICE_OUT_OF_RANGE", "index": 10, "field": field_name}
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
# Test 2: Cuatro positivos — open/close igual a low o high (inclusivo)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("field_name,value", [
    ("open", 99.0),   # igual a low
    ("open", 101.0),  # igual a high
    ("close", 99.0),  # igual a low
    ("close", 101.0), # igual a high
])
def test_price_at_boundary_inclusive(field_name, value):
    """open/close en los límites inclusivos [low,high] → HTTP200 con indicadores REALES."""
    client, restore = _build_app()
    try:
        candles = _make_50_candles()
        candles[10][field_name] = value

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

        # MACD: 17 puntos con timestamps completos (índices33..49)
        macd_points = data["macd"]["points"]
        assert len(macd_points) == 17

        expected_keys_per_point = {"time_ms", "macd_line", "signal_line", "histogram"}
        for pt in macd_points:
            assert set(pt.keys()) == expected_keys_per_point

        expected_timestamps = [1700000000000 + i * 60000 for i in range(33, 50)]
        actual_timestamps = [pt["time_ms"] for pt in macd_points]
        assert actual_timestamps == expected_timestamps
    finally:
        restore()


# ---------------------------------------------------------------------------
# Test 3: Regresión high<low — se mantiene el error/string existente
# ---------------------------------------------------------------------------


def test_high_low_regression():
    """high<low en índice10 → 422 con detail existente, no NON_FINITE."""
    client, restore = _build_app()
    try:
        candles = _make_50_candles()
        candles[10]["high"] = 98.0
        candles[10]["low"] = 99.0

        resp = client.post("/api/analysis", json={
            "symbol": "BTC/USDT",
            "timeframe": "1m",
            "candles_count": 50,
            "candles": candles,
        })

        assert resp.status_code == 422
        detail = resp.json()["detail"]
        # Se mantiene el error/string existente de high<low
        assert detail == "Candle 10: high (98.0) debe ser >= low (99.0)"

        # No invocar indicadores
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
