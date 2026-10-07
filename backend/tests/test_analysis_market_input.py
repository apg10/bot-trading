"""Pruebas aisladas de prepare_market_analysis desde velas CERRADAS del engine."""

from __future__ import annotations

import copy
import socket
from unittest import mock

import httpx
import pytest

# ---------------------------------------------------------------------------
# Guardas network: bloquear IP/DNS/HTTP reales; AF_UNIX permitido.
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
# FakeEngine mínimo: config.kline_interval, snapshot(), closed_candles.
# ---------------------------------------------------------------------------


class _FakeConfig:
    def __init__(self, kline_interval: str = "1m"):
        self.kline_interval = kline_interval


class _FakeEngine:
    """Motor fake con las APIs públicas reales: config, snapshot, closed_candles."""

    def __init__(
        self,
        *,
        symbol: str = "BTC/USDT",
        kline_interval: str = "1m",
        entries_allowed: bool = True,
        closed_count: int = 60,
        last_closed_close_time: int | None = None,
        second_symbol: str | None = None,
        second_kline_interval: str | None = None,
    ):
        self._config = _FakeConfig(kline_interval)
        self._symbol = symbol
        self._entries_allowed = entries_allowed
        self._closed: dict[int, dict] = {}
        self._snapshot_call_count = 0
        self._second_symbol = second_symbol
        self._second_kline_interval = second_kline_interval
        # Para cambio de intervalo en segunda lectura, crear config mutable.
        if second_kline_interval is not None:
            self._config_second = _FakeConfig(second_kline_interval)
        else:
            self._config_second = None
        base_ms = 1700000000000
        for i in range(closed_count):
            ot = base_ms + i * 60000
            ct = ot + 59999
            self._closed[ot] = {
                "open_time": ot,
                "close_time": ct,
                "is_closed": True,
                "open": 100.0,
                "high": 101.0,
                "low": 99.0,
                "close": 100.0,
                "volume": 0.0,
            }
        # last_closed_close_time por defecto = close_time de la última cerrada.
        if last_closed_close_time is None:
            self._last_closed_close_time = max(self._closed) + 59999
        else:
            self._last_closed_close_time = last_closed_close_time

    @property
    def config(self):
        if (
            self._config_second is not None
            and self._snapshot_call_count >= 2
        ):
            return self._config_second
        return self._config

    @property
    def closed_candles(self):
        return [dict(self._closed[ot]) for ot in sorted(self._closed)]

    def snapshot(self):
        self._snapshot_call_count += 1
        sym = (
            self._second_symbol
            if (self._snapshot_call_count >= 2 and self._second_symbol is not None)
            else self._symbol
        )
        interval = (
            self._second_kline_interval
            if (self._snapshot_call_count >= 2 and self._second_kline_interval is not None)
            else self._config.kline_interval
        )
        return {
            "symbol": sym,
            "connected": True,
            "reconnecting": False,
            "closed_candles_count": len(self._closed),
            "last_bar_open_time": max(self._closed) if self._closed else None,
            "last_closed_close_time": self._last_closed_close_time,
            "history_loaded": True,
            "recovering": False,
            "complete": True,
            "stale": False,
            "entries_allowed": self._entries_allowed,
            "pending_gaps": 0,
            "phase": "complete",
            "max_candle_age_ms": 90000,
            "execution_available": False,
        }


# ---------------------------------------------------------------------------
# Import del helper bajo prueba.
# ---------------------------------------------------------------------------

from src.api.analysis_market import (
    MarketAnalysisInputError,
    PreparedMarketAnalysis,
    prepare_market_analysis,
)


# ---------------------------------------------------------------------------
# Test 1: N=50 de 60 — request con opens índices 10..59, as_of close59.
# ---------------------------------------------------------------------------


def test_50_from_60():
    engine = _FakeEngine(closed_count=60)
    result = prepare_market_analysis(engine, symbol="BTC/USDT", candles_count=50)

    assert isinstance(result, PreparedMarketAnalysis)
    assert result.data_source == "market_engine"
    assert result.execution_available is False

    req = result.request
    assert req.symbol == "BTC/USDT"
    assert req.timeframe == "1m"
    assert req.candles_count == 50
    assert len(req.candles) == 50

    # Opens índices 10..59 (base_ms + i*60000 para i=10..59).
    base_ms = 1700000000000
    for j in range(50):
        expected_ot = base_ms + (j + 10) * 60000
        assert req.candles[j].open_time == expected_ot

    # as_of_close_time_ms = close_time de la última vela cerrada (índice59).
    expected_as_of = base_ms + 59 * 60000 + 59999
    assert result.as_of_close_time_ms == expected_as_of

    # market_state es copia independiente.
    snap_before = copy.deepcopy(result.market_state)
    result.market_state["symbol"] = "MODIFIED"
    assert result.market_state["symbol"] == "MODIFIED"
    # Verificar que la copia original del engine no se alteró.
    assert engine.snapshot()["symbol"] == "BTC/USDT"


# ---------------------------------------------------------------------------
# Test 2: N=200 de 60 — request tiene 60, count200 preservado.
# ---------------------------------------------------------------------------


def test_200_from_60():
    engine = _FakeEngine(closed_count=60)
    result = prepare_market_analysis(engine, symbol="BTC/USDT", candles_count=200)

    req = result.request
    assert req.candles_count == 200  # preservado para el resolver.
    assert len(req.candles) == 60   # sin relleno.


# ---------------------------------------------------------------------------
# Test 3: engine None / inapto → MARKET_NOT_READY.
# ---------------------------------------------------------------------------


def test_engine_none():
    with pytest.raises(MarketAnalysisInputError) as exc_info:
        prepare_market_analysis(None, symbol="BTC/USDT", candles_count=50)
    assert exc_info.value.code == "MARKET_NOT_READY"


def test_entries_not_allowed():
    engine = _FakeEngine(entries_allowed=False)
    with pytest.raises(MarketAnalysisInputError) as exc_info:
        prepare_market_analysis(engine, symbol="BTC/USDT", candles_count=50)
    assert exc_info.value.code == "MARKET_NOT_READY"


# ---------------------------------------------------------------------------
# Test 4: symbol distinto / intervalo 5m → códigos definidos.
# ---------------------------------------------------------------------------


def test_symbol_mismatch():
    engine = _FakeEngine(symbol="ETH/USDT")
    with pytest.raises(MarketAnalysisInputError) as exc_info:
        prepare_market_analysis(engine, symbol="BTC/USDT", candles_count=50)
    assert exc_info.value.code == "MARKET_SYMBOL_MISMATCH"


def test_interval_5m():
    engine = _FakeEngine(kline_interval="5m")
    with pytest.raises(MarketAnalysisInputError) as exc_info:
        prepare_market_analysis(engine, symbol="BTC/USDT", candles_count=50)
    assert exc_info.value.code == "MARKET_INTERVAL_UNSUPPORTED"


# ---------------------------------------------------------------------------
# Test 5: Solo 49 cerradas aunque estado apto → INSUFFICIENT_CANDLES.
# ---------------------------------------------------------------------------


def test_insufficient_candles():
    engine = _FakeEngine(closed_count=49)
    with pytest.raises(MarketAnalysisInputError) as exc_info:
        prepare_market_analysis(engine, symbol="BTC/USDT", candles_count=50)
    assert exc_info.value.code == "INSUFFICIENT_CANDLES"


# ---------------------------------------------------------------------------
# Test 6: count=0 / 501 / True → INVALID_CANDLES_COUNT.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bad_count", [0, 49, 501, True])
def test_invalid_candles_count(bad_count):
    engine = _FakeEngine(closed_count=60)
    with pytest.raises(MarketAnalysisInputError) as exc_info:
        prepare_market_analysis(engine, symbol="BTC/USDT", candles_count=bad_count)  # type: ignore[arg-type]
    assert exc_info.value.code == "INVALID_CANDLES_COUNT"


# ---------------------------------------------------------------------------
# Test 7: Último close_time no coincide con estado → MARKET_CONTEXT_CHANGED.
# ---------------------------------------------------------------------------


def test_close_time_mismatch():
    engine = _FakeEngine(closed_count=60, last_closed_close_time=1700000000000)
    with pytest.raises(MarketAnalysisInputError) as exc_info:
        prepare_market_analysis(engine, symbol="BTC/USDT", candles_count=50)
    assert exc_info.value.code == "MARKET_CONTEXT_CHANGED"


# ---------------------------------------------------------------------------
# Test 7b: Regresión — cambio de símbolo en la segunda lectura.
# ---------------------------------------------------------------------------


def test_second_snapshot_symbol_change():
    """Primera lectura válida, segundo snapshot cambia símbolo → MARKET_CONTEXT_CHANGED."""
    engine = _FakeEngine(
        closed_count=60,
        second_symbol="ETH/USDT",  # snap1=BTC/USDT (válido), snap2=ETH/USDT
    )
    with pytest.raises(MarketAnalysisInputError) as exc_info:
        prepare_market_analysis(engine, symbol="BTC/USDT", candles_count=50)
    assert exc_info.value.code == "MARKET_CONTEXT_CHANGED"


# ---------------------------------------------------------------------------
# Test 7c: Regresión — cambio de intervalo en la segunda lectura.
# ---------------------------------------------------------------------------


def test_second_snapshot_interval_change():
    """Primera lectura válida, segundo snapshot cambia intervalo → MARKET_CONTEXT_CHANGED."""
    engine = _FakeEngine(
        closed_count=60,
        second_kline_interval="5m",  # snap1=1m (válido), snap2=5m
    )
    with pytest.raises(MarketAnalysisInputError) as exc_info:
        prepare_market_analysis(engine, symbol="BTC/USDT", candles_count=50)
    assert exc_info.value.code == "MARKET_CONTEXT_CHANGED"


# ---------------------------------------------------------------------------
# Test 8: Mutar request.candles y market_state retornados no altera fuentes.
# ---------------------------------------------------------------------------


def test_mutation_isolation():
    engine = _FakeEngine(closed_count=60)
    result = prepare_market_analysis(engine, symbol="BTC/USDT", candles_count=50)

    # Mutar request.candles.
    original_ot = result.request.candles[0].open_time
    result.request.candles[0].open_time = 999999
    assert result.request.candles[0].open_time == 999999

    # Verificar que las velas del engine no se alteraron.
    engine_candles = engine.closed_candles
    assert engine_candles[-50]["open_time"] != 999999 or engine_candles[-50]["open_time"] == original_ot

    # Mutar market_state.
    result.market_state["symbol"] = "HACKED"
    assert engine.snapshot()["symbol"] == "BTC/USDT"


# ---------------------------------------------------------------------------
# Guardas: calcular_* fallan si se invocan (no ejecutamos indicadores).
# ---------------------------------------------------------------------------


def test_no_indicators_called():
    """Verificar que prepare_market_analysis no llama a indicadores."""
    with mock.patch("src.api.analysis.calculate_keltner") as mock_k:
        with mock.patch("src.api.analysis.calculate_macd") as mock_m:
            with mock.patch("src.api.analysis.calculate_fyl") as mock_f:
                engine = _FakeEngine(closed_count=60)
                result = prepare_market_analysis(engine, symbol="BTC/USDT", candles_count=50)
                assert result is not None
                mock_k.assert_not_called()
                mock_m.assert_not_called()
                mock_f.assert_not_called()
