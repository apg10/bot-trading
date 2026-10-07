"""Pruebas aisladas del contrato de POST /api/analysis — insuficiencia de velas.

No abren TestClient(main.app) ni inician el motor de mercado.
Montan un FastAPI minimo con solo el router de análisis.
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
    """Monta analysis.router en un FastAPI aislado.

    No crea ni modifica app_config.  El restore cierra el TestClient.
    """
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


def _make_candle(open_time: int, open_val: float, high_val: float, low_val: float, close_val: float) -> dict:
    return {
        "open_time": open_time,
        "open": open_val,
        "high": high_val,
        "low": low_val,
        "close": close_val,
        "volume": 100.0,
    }


def _make_50_candles():
    """Genera 50 velas validas con OHLC constantes y timestamps incrementales."""
    base_ts = 1700000000000
    candles = []
    for i in range(50):
        candles.append({
            "open_time": base_ts + i * 60000,
            "open": 100.0,
            "high": 101.0,
            "low": 99.0,
            "close": 100.0,
            "volume": 100.0,
        })
    return candles


# ---------------------------------------------------------------------------
# Test 1: Parametrizado — insuficiencia de velas (3 casos)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("candles_value", [
    None,       # null explícito
    [],         # lista vacia
])
def test_insufficient_candles_parametrized(candles_value):
    """Candles null o [] recibe 422 con detalle exacto."""
    client, restore = _build_app()
    try:
        resp = client.post("/api/analysis", json={
            "symbol": "BTC/USDT",
            "timeframe": "15m",
            "candles_count": 200,
            "candles": candles_value,
        })

        assert resp.status_code == 422
        detail = resp.json()["detail"]
        assert detail == {"code": "INSUFFICIENT_CANDLES", "received": 0, "required": 50}
    finally:
        restore()


def test_insufficient_candles_null_exact_detail():
    """Candles=null comprueba el detalle exacto (no se apoya en omitido)."""
    client, restore = _build_app()
    try:
        resp = client.post("/api/analysis", json={
            "symbol": "BTC/USDT",
            "timeframe": "15m",
            "candles_count": 200,
            "candles": None,
        })

        assert resp.status_code == 422
        detail = resp.json()["detail"]
        assert detail == {"code": "INSUFFICIENT_CANDLES", "received": 0, "required": 50}
    finally:
        restore()


def test_insufficient_candles_49():
    """49 velas validas recibe 422 con received=49."""
    client, restore = _build_app()
    try:
        candles = _make_50_candles()[:49]
        resp = client.post("/api/analysis", json={
            "symbol": "BTC/USDT",
            "timeframe": "15m",
            "candles_count": 200,
            "candles": candles,
        })

        assert resp.status_code == 422
        detail = resp.json()["detail"]
        assert detail["code"] == "INSUFFICIENT_CANDLES"
        assert detail["received"] == 49
        assert detail["required"] == 50
    finally:
        restore()


# ---------------------------------------------------------------------------
# Test 2: Los calculate_* fallan si se invocan en casos de insuficiencia
# ---------------------------------------------------------------------------


def test_calculate_functions_not_called_on_insufficient():
    """En casos de insuficiencia, los calculadores no deben ser invocados."""
    client, restore = _build_app()
    try:
        # Patch los calculadores para que fallen si se invocan
        with mock.patch("src.api.analysis.calculate_keltner") as mock_keltner:
            with mock.patch("src.api.analysis.calculate_macd") as mock_macd:
                with mock.patch("src.api.analysis.calculate_fyl") as mock_fyl:
                    resp = client.post("/api/analysis", json={
                        "symbol": "BTC/USDT",
                        "timeframe": "15m",
                        "candles_count": 200,
                        "candles": [],
                    })

                    assert resp.status_code == 422
                    # Ningun calculador debe haber sido invocado
                    mock_keltner.assert_not_called()
                    mock_macd.assert_not_called()
                    mock_fyl.assert_not_called()
    finally:
        restore()


def test_generate_candles_not_called_on_insufficient():
    """generate_candles no debe ser llamado en casos de insuficiencia."""
    client, restore = _build_app()
    try:
        with mock.patch("src.fixtures.synthetic.generate_candles") as mock_gen:
            resp = client.post("/api/analysis", json={
                "symbol": "BTC/USDT",
                "timeframe": "15m",
                "candles_count": 200,
                "candles": None,
            })

            assert resp.status_code == 422
            mock_gen.assert_not_called()
    finally:
        restore()


# ---------------------------------------------------------------------------
# Test 3: Positivo con 50 velas validas
# ---------------------------------------------------------------------------


def test_analysis_with_50_candles():
    """50 velas validas genera HTTP200 con seis keys del exito actual."""
    client, restore = _build_app()
    try:
        candles = _make_50_candles()
        resp = client.post("/api/analysis", json={
            "symbol": "BTC/USDT",
            "timeframe": "1m",
            "candles_count": 50,
            "candles": candles,
        })

        assert resp.status_code == 200
        data = resp.json()

        # Keys del exito actual
        expected_keys = {"symbol", "timeframe", "data_source", "keltner", "macd", "fyl"}
        assert set(data.keys()) == expected_keys

        # Valores
        assert data["symbol"] == "BTC/USDT"
        assert data["timeframe"] == "1m"
        assert data["data_source"] == "provided"

        # MACD: 17 puntos con timestamps desde indice33 y mismas keys por punto
        macd_points = data["macd"]["points"]
        assert len(macd_points) == 17

        expected_keys_per_point = {"time_ms", "macd_line", "signal_line", "histogram"}
        for pt in macd_points:
            assert set(pt.keys()) == expected_keys_per_point

        # Comparar TODOS los timestamps MACD
        expected_timestamps = [1700000000000 + i * 60000 for i in range(33, 50)]
        actual_timestamps = [pt["time_ms"] for pt in macd_points]
        assert actual_timestamps == expected_timestamps
    finally:
        restore()


# ---------------------------------------------------------------------------
# Test 4: Positivo con 60 velas y candles_count=50 — truncacion
# ---------------------------------------------------------------------------


def test_analysis_with_60_candles_truncation():
    """60 velas con candles_count=50 respeta el limite/truncacion."""
    client, restore = _build_app()
    try:
        candles = _make_50_candles() + [_make_candle(1700000000000 + i * 60000, 100.0, 101.0, 99.0, 100.0) for i in range(50, 60)]
        resp = client.post("/api/analysis", json={
            "symbol": "BTC/USDT",
            "timeframe": "1m",
            "candles_count": 50,
            "candles": candles,
        })

        assert resp.status_code == 200
        data = resp.json()
        assert data["data_source"] == "provided"

        # MACD debe tener 17 puntos (desde indice33 hasta el final de las primeras 50)
        macd_points = data["macd"]["points"]
        assert len(macd_points) == 17

        expected_keys_per_point = {"time_ms", "macd_line", "signal_line", "histogram"}
        for pt in macd_points:
            assert set(pt.keys()) == expected_keys_per_point

        # Comparar TODOS los timestamps MACD — deben ser de las primeras 50 velas
        expected_timestamps = [1700000000000 + i * 60000 for i in range(33, 50)]
        actual_timestamps = [pt["time_ms"] for pt in macd_points]
        assert actual_timestamps == expected_timestamps
    finally:
        restore()
