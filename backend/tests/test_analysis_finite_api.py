"""Pruebas aisladas del guard de valores finitos en POST /api/analysis.

FastAPI aislado solo analysis.router, guardas network (AF_UNIX permitido).
No main.app/lifespan ni modelos/servicios reales.
"""

from __future__ import annotations

import httpx
import json
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
                     low_val: float = 99.0, close_val: float = 100.0,
                     volume_val: float = 0.0):
    """Genera 50 velas validas con OHLC constantes y timestamps incrementales."""
    base_ts = 1700000000000
    candles = []
    for i in range(50):
        candles.append({
            "open_time": base_ts + i * 60000,
            "open": open_val,
            "high": high_val,
            "low": low_val,
            "close": close_val,
            "volume": volume_val,
        })
    return candles


# ---------------------------------------------------------------------------
# Test 1: Parametrizado — cinco campos × NaN/+Inf/-Inf (15 casos)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("field_name,value", [
    ("open", float("nan")),
    ("open", float("inf")),
    ("open", float("-inf")),
    ("high", float("nan")),
    ("high", float("inf")),
    ("high", float("-inf")),
    ("low", float("nan")),
    ("low", float("inf")),
    ("low", float("-inf")),
    ("close", float("nan")),
    ("close", float("inf")),
    ("close", float("-inf")),
    ("volume", float("nan")),
    ("volume", float("inf")),
    ("volume", float("-inf")),
])
def test_non_finite_values_parametrized(field_name, value):
    """Cada campo OHLCV con NaN/+Inf/-Inf en índice10 produce 422 exacto."""
    client, restore = _build_app()
    try:
        candles = _make_50_candles()
        # Sobrescribir el campo especificado en la vela de índice 10
        candles[10][field_name] = value

        payload = {
            "symbol": "BTC/USDT",
            "timeframe": "1m",
            "candles_count": 50,
            "candles": candles,
        }

        # Usar content=json.dumps con allow_nan=True para serializar NaN/Inf
        resp = client.post(
            "/api/analysis",
            content=json.dumps(payload, allow_nan=True),
            headers={"Content-Type": "application/json"},
        )

        assert resp.status_code == 422
        detail = resp.json()["detail"]
        expected_detail = {"code": "NON_FINITE_CANDLE_VALUE", "index": 10, "field": field_name}
        assert detail == expected_detail

        # No debe devolver indicadores ni calcular
        with mock.patch("src.api.analysis.calculate_keltner") as mock_k:
            with mock.patch("src.api.analysis.calculate_macd") as mock_m:
                with mock.patch("src.api.analysis.calculate_fyl") as mock_f:
                    resp2 = client.post(
                        "/api/analysis",
                        content=json.dumps(payload, allow_nan=True),
                        headers={"Content-Type": "application/json"},
                    )
                    assert resp2.status_code == 422
                    mock_k.assert_not_called()
                    mock_m.assert_not_called()
                    mock_f.assert_not_called()
    finally:
        restore()


# ---------------------------------------------------------------------------
# Test 2: Positivo con datos finitos y volume=0, indicadores REALES
# ---------------------------------------------------------------------------


def test_positive_finite_data_with_real_indicators():
    """Datos OHLCV finitos + volume=0 → HTTP200, seis keys, provided, MACD completo."""
    client, restore = _build_app()
    try:
        candles = _make_50_candles(open_val=100.0, high_val=101.0,
                                    low_val=99.0, close_val=100.0,
                                    volume_val=0.0)
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
# Test 3: Regresión de omisión — payload sin candles → 422 INSUFFICIENT_CANDLES
# ---------------------------------------------------------------------------


def test_omitted_candles_regression():
    """Payload sin candles devuelve 422 exacto INSUFFICIENT_CANDLES."""
    client, restore = _build_app()
    try:
        resp = client.post("/api/analysis", json={
            "symbol": "BTC/USDT",
            "timeframe": "15m",
            "candles_count": 200,
        })

        assert resp.status_code == 422
        detail = resp.json()["detail"]
        assert detail == {"code": "INSUFFICIENT_CANDLES", "received": 0, "required": 50}
    finally:
        restore()
