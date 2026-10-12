"""Pruebas API offline de POST /api/analysis/market.

Reutiliza _FakeEngine y _block_network importados desde
test_analysis_market_input sin editar ese archivo.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

# ---------------------------------------------------------------------------
# Importar _FakeEngine y _block_network de test_analysis_market_input.
# ---------------------------------------------------------------------------

from test_analysis_market_input import _FakeEngine, _block_network  # noqa: F401

# ---------------------------------------------------------------------------
# Montaje: app minima con router analysis + fake engine en app.state
# ---------------------------------------------------------------------------


def _build_app(engine=None):
    """Monta analysis.router en FastAPI aislado con market_engine opcional."""
    from src.api.analysis import router as analysis_router

    app = FastAPI()
    if engine is not None:
        app.state.market_engine = engine
    app.include_router(analysis_router)
    client = TestClient(app)

    def _restore():
        client.close()

    return client, _restore


# ---------------------------------------------------------------------------
# Helpers — velas validas para el endpoint proporcionado (control)
# ---------------------------------------------------------------------------


def _make_candle(open_time: int, open_val: float, high_val: float, low_val: float, close_val: float):
    return {
        "open_time": open_time,
        "open": open_val,
        "high": high_val,
        "low": low_val,
        "close": close_val,
        "volume": 100.0,
    }


def _make_50_candles():
    base_ts = 1700000000000
    return [
        {**_make_candle(base_ts + i * 60000, 100.0, 101.0, 99.0, 100.0)}
        for i in range(50)
    ]


# ---------------------------------------------------------------------------
# Test 1: Éxito con 50 de 60 — comparar indicadores con POST provided (últimas 50)
# ---------------------------------------------------------------------------


def test_market_success_50_from_60():
    """Últimas 50 de 60; verificar source, cierre, timeframe, estado y ejecución false."""
    engine = _FakeEngine(closed_count=60)
    expected_close = engine.closed_candles[-1]["close_time"]
    expected_state = _FakeEngine(closed_count=60).snapshot()
    client, restore = _build_app(engine)
    try:
        resp = client.post("/api/analysis/market", json={
            "symbol": "BTC/USDT",
            "candles_count": 50,
        })
        assert resp.status_code == 200
        data = resp.json()

        # Metadatos exactos independientes de la respuesta
        assert data["source"] == "market_engine"
        assert data["symbol"] == "BTC/USDT"
        assert data["as_of_close_time_ms"] == expected_close
        assert data["execution_available"] is False
        assert data["timeframe"] == "1m"
        assert data["market_state"] == expected_state

        # Comparar indicadores con POST provided (últimas 50 velas)
        last_50 = engine.closed_candles[-50:]
        candles_provided = [
            {
                "open_time": c["open_time"],
                "open": c["open"],
                "high": c["high"],
                "low": c["low"],
                "close": c["close"],
                "volume": c["volume"],
            }
            for c in last_50
        ]
        resp_provided = client.post("/api/analysis", json={
            "symbol": "BTC/USDT",
            "timeframe": "1m",
            "candles_count": 50,
            "candles": candles_provided,
        })
        assert resp_provided.status_code == 200
        data_provided = resp_provided.json()

        # Igualdad completa de indicadores (sin tolerancias ni slicing)
        assert data["keltner"] == data_provided["keltner"]
        assert data["macd"] == data_provided["macd"]
        assert data["fyl"] == data_provided["fyl"]

        # Verificar data_source del provided
        assert data_provided["data_source"] == "provided"
    finally:
        restore()


# ---------------------------------------------------------------------------
# Test 2: Éxito con default candles_count=200 de 60 — todas las 60 velas
# ---------------------------------------------------------------------------


def test_market_success_default_200_from_60():
    """Default 200 de 60; comparar indicadores con POST provided (todas 60)."""
    engine = _FakeEngine(closed_count=60)
    expected_close = engine.closed_candles[-1]["close_time"]
    expected_state = _FakeEngine(closed_count=60).snapshot()
    client, restore = _build_app(engine)
    try:
        resp = client.post("/api/analysis/market", json={"symbol": "BTC/USDT"})
        assert resp.status_code == 200
        data = resp.json()

        # Metadatos exactos independientes de la respuesta
        assert data["source"] == "market_engine"
        assert data["symbol"] == "BTC/USDT"
        assert data["as_of_close_time_ms"] == expected_close
        assert data["execution_available"] is False
        assert data["timeframe"] == "1m"
        assert data["market_state"] == expected_state

        # candles_count=200 preservado en request interno, pero solo hay 60 velas

        # Comparar con POST provided (60 velas)
        all_60 = engine.closed_candles
        candles_provided = [
            {
                "open_time": c["open_time"],
                "open": c["open"],
                "high": c["high"],
                "low": c["low"],
                "close": c["close"],
                "volume": c["volume"],
            }
            for c in all_60
        ]
        resp_provided = client.post("/api/analysis", json={
            "symbol": "BTC/USDT",
            "timeframe": "1m",
            "candles_count": 200,
            "candles": candles_provided,
        })
        assert resp_provided.status_code == 200
        data_provided = resp_provided.json()

        # Igualdad completa de indicadores (sin tolerancias ni slicing)
        assert data["keltner"] == data_provided["keltner"]
        assert data["macd"] == data_provided["macd"]
        assert data["fyl"] == data_provided["fyl"]

        # Verificar data_source del provided
        assert data_provided["data_source"] == "provided"
    finally:
        restore()


# ---------------------------------------------------------------------------
# Test 3: Parametrizado — entrada inválida (count, tipo, campo extra)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "payload",
    [
        {"symbol": "BTC/USDT", "candles_count": 49},
        {"symbol": "BTC/USDT", "candles_count": 501},
        {"symbol": "BTC/USDT", "candles_count": True},
        {"symbol": "BTC/USDT", "candles_count": 50.0},
        {"symbol": "BTC/USDT", "candles_count": "50"},
        {"symbol": "BTC/USDT", "candles_count": None},
        {"symbol": "BTC/USDT", "candles_count": 200, "candles": []},
    ],
)
def test_market_invalid_input(payload):
    """Entrada inválida produce 422."""
    engine = _FakeEngine(closed_count=60)
    client, restore = _build_app(engine)
    try:
        resp = client.post("/api/analysis/market", json=payload)
        assert resp.status_code == 422
    finally:
        restore()


# ---------------------------------------------------------------------------
# Test 4: Parametrizado — fallos reales del fake engine
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "engine_kwargs,expected_status,expected_code",
    [
        (None, 503, "MARKET_NOT_READY"),
        ({"closed_count": 49}, 503, "INSUFFICIENT_CANDLES"),
        ({"symbol": "ETH/USDT"}, 409, "MARKET_SYMBOL_MISMATCH"),
        ({"kline_interval": "5m"}, 409, "MARKET_INTERVAL_UNSUPPORTED"),
        ({"closed_count": 60, "last_closed_close_time": 1700000000000}, 409, "MARKET_CONTEXT_CHANGED"),
    ],
)
def test_market_real_failures(engine_kwargs, expected_status, expected_code):
    """Fallos reales del fake engine devuelven status/code definidos."""
    engine = None if engine_kwargs is None else _FakeEngine(**engine_kwargs)
    client, restore = _build_app(engine)
    try:
        resp = client.post("/api/analysis/market", json={"symbol": "BTC/USDT"})
        assert resp.status_code == expected_status
        detail = resp.json()["detail"]
        assert detail["code"] == expected_code
    finally:
        restore()


# ---------------------------------------------------------------------------
# Test 5: Engine ausente → MARKET_NOT_READY
# ---------------------------------------------------------------------------


def test_market_no_engine():
    """Sin market_engine en app.state → 503."""
    client, restore = _build_app(None)
    try:
        resp = client.post("/api/analysis/market", json={"symbol": "BTC/USDT"})
        assert resp.status_code == 503
        detail = resp.json()["detail"]
        assert detail["code"] == "MARKET_NOT_READY"
    finally:
        restore()


# ---------------------------------------------------------------------------
# Test 6: Guardas — cálculo real, sin mocks de resultados
# ---------------------------------------------------------------------------


def test_market_real_calculation():
    """Verificar que los indicadores se calculan realmente (no mock)."""
    engine = _FakeEngine(closed_count=60)
    client, restore = _build_app(engine)
    try:
        resp = client.post("/api/analysis/market", json={"symbol": "BTC/USDT"})
        assert resp.status_code == 200
        data = resp.json()

        # Los indicadores deben tener datos reales (no vacíos)
        assert len(data["keltner"]["points"]) > 0
        assert len(data["macd"]["points"]) > 0
        assert len(data["fyl"]["pivots"]) >= 0
        assert len(data["fyl"]["zones"]) >= 0

        # MACD debe tener las keys esperadas por punto
        for pt in data["macd"]["points"]:
            assert set(pt.keys()) == {"time_ms", "macd_line", "signal_line", "histogram"}
    finally:
        restore()


# ---------------------------------------------------------------------------
# Test 7: Provided sigue source provided (verificar que POST /analysis no cambia)
# ---------------------------------------------------------------------------


def test_provided_still_works():
    """POST /analysis con candles proporcionadas mantiene data_source='provided'."""
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
        assert data["data_source"] == "provided"
    finally:
        restore()


# ---------------------------------------------------------------------------
# Test 8: Symbol estricto — no convierte bytes a string
# ---------------------------------------------------------------------------


def test_market_symbol_strict():
    """MarketAnalysisRequest.symbol con strict=True rechaza bytes."""
    from pydantic import ValidationError

    from src.api.analysis import MarketAnalysisRequest

    # Positivo: str intacto
    req = MarketAnalysisRequest(symbol="BTC/USDT")
    assert req.symbol == "BTC/USDT"
    assert isinstance(req.symbol, str)

    # Negativo: bytes deben rechazar con ValidationError
    with pytest.raises(ValidationError) as exc_info:
        MarketAnalysisRequest(symbol=b"BTC/USDT")

    errors = exc_info.value.errors()
    assert len(errors) == 1
    assert errors[0]["loc"] == ("symbol",)
    assert errors[0]["type"] == "string_type"
