"""Pruebas de los tres endpoints de mercado con FastAPI TestClient en memoria."""

from collections.abc import Iterator
from contextlib import contextmanager
from types import SimpleNamespace

import httpx
import pytest
import socket as _socket_module
from fastapi import FastAPI
from fastapi.testclient import TestClient

import src.api.market as market_api


@pytest.fixture(autouse=True)
def _isolate_and_block_network(monkeypatch: pytest.MonkeyPatch):
    """Bloquea toda red real y protege el módulo market_api durante la suite."""

    def forbid_network(*args, **kwargs):
        raise AssertionError("Esta suite prohíbe HTTP, conexiones y DNS reales")

    async def forbid_async_http(*args, **kwargs):
        forbid_network()

    # Conservar AF_UNIX para los sockets locales usados por asyncio/AnyIO/TestClient.
    _real_connect = _socket_module.socket.connect
    _real_connect_ex = _socket_module.socket.connect_ex

    def guarded_connect(sock, address):
        if sock.family != _socket_module.AF_UNIX:
            forbid_network()
        return _real_connect(sock, address)

    def guarded_connect_ex(sock, address):
        if sock.family != _socket_module.AF_UNIX:
            forbid_network()
        return _real_connect_ex(sock, address)

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", forbid_network)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", forbid_async_http)
    monkeypatch.setattr(_socket_module, "getaddrinfo", forbid_network)
    monkeypatch.setattr(_socket_module, "gethostbyname", forbid_network)
    monkeypatch.setattr(_socket_module, "gethostbyname_ex", forbid_network)
    monkeypatch.setattr(_socket_module, "gethostbyaddr", forbid_network)
    monkeypatch.setattr(_socket_module, "create_connection", forbid_network)
    monkeypatch.setattr(_socket_module.socket, "connect", guarded_connect)
    monkeypatch.setattr(_socket_module.socket, "connect_ex", guarded_connect_ex)
    # Esta protección externa restaura el estado previo al test después del
    # teardown del cliente y de cualquier monkeypatch aplicado por la prueba.
    monkeypatch.setattr(market_api, "_market_engine", None)


@contextmanager
def _isolated_market_client() -> Iterator[TestClient]:
    """Protección compartida por la fixture y sus regresiones de restauración."""
    # Asegurar que el módulo de market comienza sin motor.
    _previous = market_api._market_engine
    market_api._market_engine = None
    try:
        # App aislada: solo las rutas de mercado, nunca el lifespan productivo.
        isolated_app = FastAPI()
        isolated_app.include_router(market_api.router)
        with TestClient(isolated_app) as test_client:
            yield test_client
    finally:
        # Restaurar estado global previo incluso si una assertion falló.
        market_api._market_engine = _previous


@pytest.fixture
def client() -> Iterator[TestClient]:
    with _isolated_market_client() as test_client:
        yield test_client


@pytest.mark.parametrize("path", ["/market/status", "/market/candles"])
def test_legacy_market_routes_not_registered(client: TestClient, path: str):
    assert client.get(path).status_code == 404


# ── /api/market/status ───────────────────────────────────────────────────────

class TestMarketStatus:
    def test_status_returns_200(self, client: TestClient):
        resp = client.get("/api/market/status")
        assert resp.status_code == 200

    def test_status_default_symbol(self, client: TestClient):
        resp = client.get("/api/market/status")
        data = resp.json()
        assert data["symbol"] == "BTC/USDT"
        assert data["connected"] is False
        assert data["reconnecting"] is False
        assert data["data_source"] == "unavailable"
        assert data["interval"] is None

    def test_status_custom_symbol(self, client: TestClient):
        resp = client.get("/api/market/status?symbol=ETH/USDT")
        data = resp.json()
        assert data["symbol"] == "ETH/USDT"


# ── /api/market/candles ─────────────────────────────────────────────────────

class TestMarketCandles:
    def test_candles_returns_200(self, client: TestClient):
        resp = client.get("/api/market/candles")
        assert resp.status_code == 200

    def test_candles_returns_envelope_with_explicit_test_source(self, client: TestClient):
        resp = client.get("/api/market/candles")
        data = resp.json()
        assert data["symbol"] == "BTC/USDT"
        assert data["interval"] == "1m"
        assert data["data_source"] == "TEST_ONLY"
        assert isinstance(data["candles"], list)
        assert len(data["candles"]) > 0

    def test_candle_fields(self, client: TestClient):
        resp = client.get("/api/market/candles")
        data = resp.json()
        candle = data["candles"][0]
        assert "open_time" in candle
        assert "close_time" in candle
        assert "open" in candle
        assert "high" in candle
        assert "low" in candle
        assert "close" in candle
        assert "volume" in candle
        assert "is_closed" in candle

    def test_candles_limit(self, client: TestClient):
        resp = client.get("/api/market/candles?limit=10")
        data = resp.json()
        assert len(data["candles"]) <= 10

    def test_custom_symbol_fixture_is_still_test_only(self, client: TestClient):
        data = client.get("/api/market/candles?symbol=ETH/USDT&limit=10").json()
        assert data["symbol"] == "ETH/USDT"
        assert data["data_source"] == "TEST_ONLY"
        assert data["interval"] == "1m"


class FakeMarketEngine:
    """Buffer explícito de prueba que representa datos recibidos por el motor."""

    def __init__(self):
        self.state = SimpleNamespace(
            symbol="BTC/USDT", connected=True, reconnecting=False,
            closed_candles_count=1, last_bar_open_time=1_700_000_000_000,
        )
        self.config = SimpleNamespace(kline_interval="15m")
        self.closed_candles = [{
            "open_time": 1_700_000_000_000, "close_time": 1_700_000_899_999,
            "open": 100.0, "high": 102.0, "low": 99.0, "close": 101.0,
            "volume": 10.0, "is_closed": True,
        }]
        self.handlers = {}

    def on_bar_closed(self, handler):
        self.handlers["closed"] = handler

    def on_bar_updated(self, handler):
        self.handlers["updated"] = handler

    def on_gap(self, handler):
        self.handlers["gap"] = handler

    def on_state_change(self, handler):
        self.handlers["state"] = handler


def test_engine_history_preserves_symbol_interval_and_source(client, monkeypatch):
    engine = FakeMarketEngine()
    monkeypatch.setattr(market_api, "_market_engine", engine)
    data = client.get("/api/market/candles").json()
    assert data == {
        "symbol": "BTC/USDT", "interval": "15m", "data_source": "market_engine",
        "candles": engine.closed_candles,
    }
    status = client.get("/api/market/status").json()
    assert status["connected"] is True
    assert status["data_source"] == "market_engine"
    assert status["interval"] == "15m"


def test_another_symbol_never_receives_current_engine_history(client, monkeypatch):
    monkeypatch.setattr(market_api, "_market_engine", FakeMarketEngine())
    data = client.get("/api/market/candles?symbol=ETH/USDT").json()
    assert data["symbol"] == "ETH/USDT"
    assert data["data_source"] == "TEST_ONLY"
    status = client.get("/api/market/status?symbol=ETH/USDT").json()
    assert status["symbol"] == "ETH/USDT"
    assert status["connected"] is False
    assert status["closed_candles_count"] == 0
    assert status["data_source"] == "unavailable"


def test_empty_engine_fallback_keeps_fixture_interval(client, monkeypatch):
    engine = FakeMarketEngine()
    engine.closed_candles = []
    monkeypatch.setattr(market_api, "_market_engine", engine)
    data = client.get("/api/market/candles").json()
    assert data["data_source"] == "TEST_ONLY"
    assert data["interval"] == "1m"  # no presentar la fixture 1m como 15m


# ── /api/market/ws ──────────────────────────────────────────────────────────

class TestMarketWebSocket:
    @pytest.mark.parametrize("channel, handler, event_type", [
        ("candles", "closed", "bar_closed"), ("updates", "updated", "bar_updated"),
    ])
    def test_candle_events_include_source_and_market_identity(
        self, client, monkeypatch, channel, handler, event_type,
    ):
        engine = FakeMarketEngine()
        monkeypatch.setattr(market_api, "_market_engine", engine)
        with client.websocket_connect("/api/market/ws") as websocket:
            websocket.send_json({"type": "subscribe", "channel": channel})
            assert websocket.receive_json()["type"] == "subscribed"
            client.portal.call(engine.handlers[handler], engine.closed_candles[0])
            data = websocket.receive_json()
            assert data["type"] == event_type
            assert data["symbol"] == "BTC/USDT"
            assert data["interval"] == "15m"
            assert data["data_source"] == "market_engine"
            assert data["candle"] == engine.closed_candles[0]

    def test_status_events_include_source_without_claiming_execution(self, client, monkeypatch):
        engine = FakeMarketEngine()
        monkeypatch.setattr(market_api, "_market_engine", engine)
        with client.websocket_connect("/api/market/ws") as websocket:
            websocket.send_json({"type": "subscribe", "channel": "status"})
            assert websocket.receive_json()["type"] == "subscribed"
            client.portal.call(engine.handlers["state"], vars(engine.state))
            data = websocket.receive_json()
            assert data["type"] == "status"
            assert data["data_source"] == "market_engine"
            assert data["interval"] == "15m"
            assert "is_running" not in data

    def test_ws_accepts_connection(self, client: TestClient):
        """El endpoint acepta la conexión y responde al primer ping."""
        with client.websocket_connect("/api/market/ws") as websocket:
            # El protocolo no incluye un mensaje de bienvenida espontáneo.
            websocket.send_json({"type": "ping"})
            data = websocket.receive_json()
            assert data == {"type": "pong"}

    def test_ws_subscribe_candles(self, client: TestClient):
        """Suscribirse al canal candles devuelve acknowledged."""
        with client.websocket_connect("/api/market/ws") as websocket:
            websocket.send_json({"type": "subscribe", "channel": "candles"})
            data = websocket.receive_json()
            assert data["type"] == "subscribed"
            assert data["channel"] == "candles"

    def test_ws_subscribe_status(self, client: TestClient):
        """Suscribirse al canal status devuelve acknowledged."""
        with client.websocket_connect("/api/market/ws") as websocket:
            websocket.send_json({"type": "subscribe", "channel": "status"})
            data = websocket.receive_json()
            assert data["type"] == "subscribed"
            assert data["channel"] == "status"

    def test_ws_subscribe_updates(self, client: TestClient):
        """El canal de actualizaciones intrabar está en la misma ruta WS."""
        with client.websocket_connect("/api/market/ws") as websocket:
            websocket.send_json({"type": "subscribe", "channel": "updates"})
            assert websocket.receive_json() == {
                "type": "subscribed",
                "channel": "updates",
            }

    def test_ws_subscribe_gaps(self, client: TestClient):
        """Suscribirse al canal gaps devuelve acknowledged."""
        with client.websocket_connect("/api/market/ws") as websocket:
            websocket.send_json({"type": "subscribe", "channel": "gaps"})
            data = websocket.receive_json()
            assert data["type"] == "subscribed"
            assert data["channel"] == "gaps"

    def test_ws_ping_pong(self, client: TestClient):
        """El servidor responde pong ante un ping."""
        with client.websocket_connect("/api/market/ws") as websocket:
            websocket.send_json({"type": "ping"})
            data = websocket.receive_json()
            assert data["type"] == "pong"

    def test_ws_unsubscribe(self, client: TestClient):
        """Unsubscribe no genera error."""
        with client.websocket_connect("/api/market/ws") as websocket:
            websocket.send_json({"type": "subscribe", "channel": "candles"})
            data = websocket.receive_json()
            assert data["type"] == "subscribed"

            # Enviar unsubscribe — no se espera respuesta
            websocket.send_json({"type": "unsubscribe", "channel": "candles"})
            websocket.send_json({"type": "ping"})
            assert websocket.receive_json() == {"type": "pong"}


# ── Validación de sintaxis de símbolo (REF-BASE master/4383372) ─────────────

_INVALID_SYMBOLS = [
    # Componentes vacíos / separadores extra
    "BTC//USDT",
    "BTC/",
    "/USDT",
    "BTC/USDT/ETH",
    # Puntuación en componentes
    "BTC.USDT",
    "BTC-USDT",
    "BTC@USDT",
    # Caracteres no ASCII
    "BTC/US\u00c1DT",
    "\u0411\u0422\u0426/USDT",
]


@pytest.mark.parametrize("invalid_symbol", _INVALID_SYMBOLS)
def test_status_rejects_invalid_symbols_422(invalid_symbol: str):
    """Ambos endpoints deben rechazar símbolos con sintaxis inválida con 422."""
    with _isolated_market_client() as tc:
        resp = tc.get(f"/api/market/status?symbol={invalid_symbol}")
        assert resp.status_code == 422


@pytest.mark.parametrize("invalid_symbol", _INVALID_SYMBOLS)
def test_candles_rejects_invalid_symbols_422(invalid_symbol: str):
    """candles nunca devuelve envelope TEST_ONLY para entrada inválida."""
    with _isolated_market_client() as tc:
        resp = tc.get(f"/api/market/candles?symbol={invalid_symbol}")
        assert resp.status_code == 422


# ── Corrección close_time inclusivo (REF-BASE master/4383372) ─────────────

class TestFixtureCloseTimeInclusive:
    """Verificar que las velas del fallback TEST_ONLY usan close_time inclusivo."""

    def test_fallback_without_engine_close_time_inclusive(self, client: TestClient):
        """Sin motor: cada vela debe tener close_time = open_time + 59_999."""
        data = client.get("/api/market/candles").json()
        assert data["data_source"] == "TEST_ONLY"
        assert data["interval"] == "1m"
        for candle in data["candles"]:
            assert candle["is_closed"] is True
            assert candle["close_time"] == candle["open_time"] + 59_999

    def test_fallback_without_engine_all_fields(self, client: TestClient):
        """Sin motor: verificar is_closed=True y procedencia TEST_ONLY."""
        data = client.get("/api/market/candles").json()
        assert data["data_source"] == "TEST_ONLY"
        assert data["interval"] == "1m"
        for candle in data["candles"]:
            assert candle["is_closed"] is True

    def test_empty_engine_fallback_close_time_inclusive(self, client: TestClient, monkeypatch):
        """Con motor pero buffer vacío: close_time inclusivo en todas las velas."""
        engine = FakeMarketEngine()
        engine.closed_candles = []
        monkeypatch.setattr(market_api, "_market_engine", engine)
        data = client.get("/api/market/candles").json()
        assert data["data_source"] == "TEST_ONLY"
        assert data["interval"] == "1m"
        for candle in data["candles"]:
            assert candle["is_closed"] is True
            assert candle["close_time"] == candle["open_time"] + 59_999

    def test_empty_engine_fallback_all_fields(self, client: TestClient, monkeypatch):
        """Con motor vacío: verificar is_closed=True y procedencia TEST_ONLY."""
        engine = FakeMarketEngine()
        engine.closed_candles = []
        monkeypatch.setattr(market_api, "_market_engine", engine)
        data = client.get("/api/market/candles").json()
        assert data["data_source"] == "TEST_ONLY"
        assert data["interval"] == "1m"
        for candle in data["candles"]:
            assert candle["is_closed"] is True


# ── Regresión de aislamiento ────────────────────────────────────────────────

def test_isolation_no_engine_without_fake(client):
    """La fixture real no inicia un motor ni registra estado de producción."""
    assert market_api._market_engine is None
    assert not hasattr(client.app.state, "market_engine")
    response = client.get("/api/market/status")
    assert response.status_code == 200
    assert response.json()["data_source"] == "unavailable"
    assert market_api._market_engine is None


@pytest.mark.parametrize("fail_inside", [False, True])
def test_isolation_restores_sentinel_after_fixture(fail_inside):
    """Ejercitar la protección real de la fixture en salida normal y con error."""
    original_engine = market_api._market_engine
    sentinel = FakeMarketEngine()
    with pytest.MonkeyPatch.context() as state:
        state.setattr(market_api, "_market_engine", sentinel)

        def exercise():
            with _isolated_market_client() as test_client:
                assert market_api._market_engine is None
                assert not hasattr(test_client.app.state, "market_engine")
                response = test_client.get("/api/market/status")
                assert response.status_code == 200
                assert response.json()["data_source"] == "unavailable"
                # Una prueba con fake puede sobrescribir el estado durante la sesión.
                market_api._market_engine = FakeMarketEngine()
                assert market_api._market_engine is not sentinel
                if fail_inside:
                    raise AssertionError("simulated assertion failure")

        if fail_inside:
            with pytest.raises(AssertionError, match="simulated assertion failure"):
                exercise()
        else:
            exercise()
        assert market_api._market_engine is sentinel

    # El sentinel de esta regresión tampoco puede contaminar las siguientes pruebas.
    assert market_api._market_engine is original_engine
