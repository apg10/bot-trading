"""Contrato N01: ciclo de vida y aptitud del mercado, sin red ni pytest-asyncio.

REST devuelve klines normalizadas, sin flag de cierre; WS sí incluye is_closed.
Los tiempos son UTC Unix ms y close_time es inclusivo (open_time + 59_999).
Bootstrap verifica la ventana explícita de 500 cierres; las fases no tienen
nombres concretos fijados por estas pruebas.
"""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from dataclasses import dataclass
from types import SimpleNamespace

import pytest
import anyio
from fastapi import WebSocketDisconnect
from fastapi.testclient import TestClient

import src.api.main as main
import src.api.market as market_api
import src.market.engine as engine_module
from src.config import Config
from src.market.binance_client import BinanceConfig
from src.market.engine import MarketEngine


MINUTE_MS = 60_000
HISTORY_DEPTH = 500
HISTORY_START_INDEX = 5 - HISTORY_DEPTH
BASE_MS = (1_700_000_000_000 // MINUTE_MS) * MINUTE_MS
SYMBOL = "BTC/USDT"
EVENTS = ("bar_closed", "bar_updated", "gap", "state_change")
STATE_FIELDS = {
    "connected", "reconnecting", "recovering", "history_loaded", "complete",
    "stale", "entries_allowed", "phase", "last_closed_close_time",
    "pending_gaps", "max_candle_age_ms",
}


@dataclass
class Clock:
    now: int = BASE_MS + 5 * MINUTE_MS + 10_000

    def __call__(self):
        return self.now


def _rest_candle(index):
    open_time = BASE_MS + index * MINUTE_MS
    return {
        "open_time": open_time,
        "close_time": open_time + MINUTE_MS - 1,
        "open": 100.0 + index * 0.01,
        "high": 102.0 + index * 0.01,
        "low": 99.0 + index * 0.01,
        "close": 101.0 + index * 0.01,
        "volume": 10.0 + index * 0.001,
        "quote_volume": 1_000.0,
        "trades": 10,
    }


def _kline(index, *, closed=True, volume=None):
    candle = _rest_candle(index)
    candle.update(symbol=SYMBOL, is_closed=closed)
    if volume is not None:
        candle["volume"] = volume
    return candle


async def _spin(predicate, *, timeout=1):
    async def wait():
        while not predicate():
            await asyncio.sleep(0)

    await asyncio.wait_for(wait(), timeout=timeout)


class DelayedHTTP:
    """Una respuesta REST explícitamente retenida, incluso al cancelar shutdown."""

    def __init__(self, rig, *, ranged_only=False):
        self.rig = rig
        self.ranged_only = ranged_only
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.cancelled = asyncio.Event()

    async def __call__(self, request):
        if not self.ranged_only or request["start_ts"] is not None:
            self.entered.set()
            try:
                await self.release.wait()
            except asyncio.CancelledError:
                self.cancelled.set()
                raise
        return self.rig.select(request)


@pytest.fixture
def fake_clients(monkeypatch):
    """Ambos transportes se reemplazan donde MarketEngine los instancia."""
    for variable in ("TRADING_ENV", "MARKET_SYMBOL", "MARKET_INTERVAL",
                     "MARKET_FRESHNESS_MARGIN_SECONDS"):
        monkeypatch.delenv(variable, raising=False)
    rig = SimpleNamespace(
        clock=Clock(),
        rows=[_rest_candle(i) for i in range(HISTORY_START_INDEX, 5)],
        history=None,
        responder=None,
        auto_connect=False,
        close_release=None,
        http_instances=[],
        ws_instances=[],
        requests=[],
        responses=[],
    )

    def select(request):
        start, end = request["start_ts"], request["end_ts"]
        bootstrap = start is None and request["limit"] == HISTORY_DEPTH
        source = rig.history if bootstrap and rig.history is not None else rig.rows
        rows = sorted(
            (row for row in source
             if (start is None or row["open_time"] >= start)
             and (end is None or row["open_time"] <= end)),
            key=lambda row: row["open_time"],
        )
        # Binance: sin startTime se obtiene la cola, con startTime la primera página.
        rows = rows[-request["limit"]:] if start is None else rows[:request["limit"]]
        return [dict(row) for row in rows]

    rig.select = select

    class FakeHTTP:
        def __init__(self, config):
            self.config = config
            self.close_count = 0
            self.close_entered = asyncio.Event()
            self.close_finished = asyncio.Event()
            self.close_cancelled = asyncio.Event()
            rig.http_instances.append(self)

        async def get_klines(
            self, symbol, interval="1m", limit=500, start_ts=None, end_ts=None,
        ):
            assert symbol == SYMBOL
            assert interval == "1m"
            assert 1 <= limit <= 1000
            request = dict(
                symbol=symbol, interval=interval, limit=limit,
                start_ts=start_ts, end_ts=end_ts,
            )
            rig.requests.append(request)
            rows = await rig.responder(request) if rig.responder else select(request)
            rows = [dict(row) for row in rows]
            rig.responses.append((request, rows))
            return rows

        async def close(self):
            self.close_count += 1
            self.close_entered.set()
            if rig.close_release is not None:
                try:
                    await rig.close_release.wait()
                except asyncio.CancelledError:
                    self.close_cancelled.set()
                    raise
            self.close_finished.set()

    class FakeWS:
        def __init__(self, config):
            self.config = config
            self.handlers = {}
            self.started = asyncio.Event()
            self.finished = asyncio.Event()
            self.start_count = 0
            self.stop_count = 0
            rig.ws_instances.append(self)

        def on_message(self, handler):
            self.handlers["message"] = handler

        def on_connect(self, handler):
            self.handlers["connect"] = handler

        def on_disconnect(self, handler):
            self.handlers["disconnect"] = handler

        def on_error(self, handler):
            self.handlers["error"] = handler

        async def start(self, symbol):
            assert symbol == SYMBOL
            self.start_count += 1
            self.started.set()
            if rig.auto_connect:
                self.connect()
            await self.finished.wait()

        async def stop(self):
            self.stop_count += 1
            self.finished.set()

        def connect(self):
            self.handlers["connect"]()

        def disconnect(self):
            self.handlers["disconnect"]()

        def message(self, candle):
            self.handlers["message"](dict(candle))

    monkeypatch.setattr(engine_module, "BinanceHTTPClient", FakeHTTP)
    monkeypatch.setattr(engine_module, "BinanceWSClient", FakeWS)
    return rig


def _engine(rig, *, margin=30.0):
    return MarketEngine(
        BinanceConfig(), freshness_margin_seconds=margin, clock_ms=rig.clock,
    )


@asynccontextmanager
async def _running(engine, rig):
    previous = len(rig.ws_instances)
    task = asyncio.create_task(engine.start(SYMBOL))
    try:
        assert isinstance(engine.started, asyncio.Event)
        await _spin(lambda: engine.started.is_set() or task.done())
        if task.done():
            task.result()
            pytest.fail("start debe seguir esperando hasta stop")
        assert len(rig.ws_instances) == previous + 1
        ws = rig.ws_instances[-1]
        await asyncio.wait_for(ws.started.wait(), timeout=1)
        yield task, ws
    finally:
        try:
            await asyncio.wait_for(engine.stop(), timeout=1)
            await asyncio.wait_for(task, timeout=1)
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)


def _snapshot(engine):
    snapshot = engine.snapshot()
    assert isinstance(snapshot, dict)
    assert STATE_FIELDS <= snapshot.keys()
    assert isinstance(snapshot["pending_gaps"], int)
    assert not isinstance(snapshot["pending_gaps"], bool)
    assert snapshot["pending_gaps"] >= 0
    assert isinstance(snapshot["phase"], str) and snapshot["phase"]
    for field in STATE_FIELDS:
        assert getattr(engine.state, field) == snapshot[field]
    return snapshot


def _assert_ready(engine):
    state = _snapshot(engine)
    assert state["connected"] is True
    assert state["reconnecting"] is False
    assert state["recovering"] is False
    assert state["history_loaded"] is True
    assert state["complete"] is True
    assert state["pending_gaps"] == 0
    assert state["stale"] is False
    assert state["entries_allowed"] is True
    return state


async def _connect_ready(engine, ws):
    ws.connect()
    await _spin(lambda: engine.snapshot()["entries_allowed"])
    return _assert_ready(engine)


def _handler_counts(engine):
    return tuple(len(getattr(engine, f"_on_{event}")) for event in EVENTS)


def test_config_defaults_to_paper_one_minute_and_thirty_second_margin(fake_clients):
    config = Config()
    assert config.env == "paper"
    assert config.is_live is False
    assert config.market_interval == "1m"
    assert config.market_freshness_margin_seconds == 30.0
    assert config.market_max_candle_age_seconds == 90.0


@pytest.mark.parametrize("margin", [0.0, 15.5, 45.0])
def test_config_market_maximum_age_includes_configured_margin(fake_clients, margin):
    config = Config(market_freshness_margin_seconds=margin)
    assert config.market_interval == "1m"
    assert config.market_freshness_margin_seconds == margin
    assert config.market_max_candle_age_seconds == 60.0 + margin


@pytest.mark.parametrize("environment", ["LIVE", "live"])
def test_config_live_is_disabled(fake_clients, environment):
    with pytest.raises(ValueError):
        Config(environment)


@pytest.mark.parametrize("margin", ["0", "15.5", "30.25"])
def test_config_reads_float_margin_from_environment(fake_clients, monkeypatch, margin):
    monkeypatch.setenv("MARKET_FRESHNESS_MARGIN_SECONDS", margin)
    config = Config()
    assert config.market_freshness_margin_seconds == float(margin)
    assert config.market_max_candle_age_seconds == 60.0 + float(margin)
    assert config.market_interval == "1m"
    assert config.env == "paper" and not config.is_live


@pytest.mark.parametrize("margin", ["nan", "inf", "-inf", "-0.001", "invalid", ""])
def test_config_rejects_invalid_nonfinite_or_negative_environment_margin(
    fake_clients, monkeypatch, margin,
):
    monkeypatch.setenv("MARKET_FRESHNESS_MARGIN_SECONDS", margin)
    with pytest.raises(ValueError):
        Config()


@pytest.mark.parametrize("margin", [float("nan"), float("inf"), float("-inf"), -0.001])
def test_config_rejects_nonfinite_or_negative_explicit_margin(fake_clients, margin):
    with pytest.raises(ValueError):
        Config(market_freshness_margin_seconds=margin)


@pytest.mark.parametrize("environment", ["LIVE", "live"])
def test_config_rejects_live_from_trading_environment(fake_clients, monkeypatch, environment):
    monkeypatch.setenv("TRADING_ENV", environment)
    with pytest.raises(ValueError):
        Config()


def test_started_announces_clients_but_start_blocks_until_idempotent_stop(fake_clients):
    async def scenario():
        rig = fake_clients
        engine = _engine(rig)
        async with _running(engine, rig) as (task, ws):
            assert len(rig.http_instances) == len(rig.ws_instances) == 1
            assert ws.start_count == 1
            assert not task.done()
            assert rig.requests == []  # bootstrap depende de la conexión real.
            state = _snapshot(engine)
            assert not state["connected"]
            assert not state["history_loaded"]
            assert not state["complete"]
            assert not state["entries_allowed"]
            assert state["last_closed_close_time"] is None
            assert state["stale"]
        await asyncio.wait_for(engine.stop(), timeout=1)
        assert rig.http_instances[0].close_count == 1
        assert ws.stop_count == 1
        ws.connect()
        ws.message(_kline(4))
        state = _snapshot(engine)
        assert not state["connected"]
        assert not state["entries_allowed"]
        assert asyncio.all_tasks() == {asyncio.current_task()}

    asyncio.run(scenario())


@pytest.mark.parametrize("event", EVENTS)
def test_register_returns_idempotent_off_function(fake_clients, event):
    engine = _engine(fake_clients)

    async def handler(_payload):
        pass

    register = getattr(engine, f"on_{event}")
    off = register(handler)
    assert callable(off)
    register(handler)
    assert getattr(engine, f"_on_{event}") == [handler]
    off()
    off()
    assert getattr(engine, f"_on_{event}") == []


def test_bootstrap_uses_utc_closed_cutoff_and_preserves_binance_timestamps(fake_clients):
    async def scenario():
        rig = fake_clients
        # REST también puede devolver la vela actual sin is_closed.
        async def including_forming(_request):
            return [dict(row) for row in rig.rows] + [_rest_candle(5)]

        rig.responder = including_forming
        engine = _engine(rig)
        async with _running(engine, rig) as (_task, ws):
            state = await _connect_ready(engine, ws)
            cutoff = (rig.clock.now // MINUTE_MS) * MINUTE_MS - 1
            assert rig.requests[0]["limit"] == HISTORY_DEPTH
            assert rig.requests[0]["end_ts"] == cutoff
            assert state["last_closed_close_time"] == cutoff
            assert state["max_candle_age_ms"] == 90_000
            assert [row["open_time"] for row in engine.closed_candles] == [
                _rest_candle(i)["open_time"] for i in range(HISTORY_START_INDEX, 5)
            ]
            assert engine.closed_candles[0]["open_time"] == cutoff + 1 - HISTORY_DEPTH * MINUTE_MS
            for candle in engine.closed_candles:
                assert candle["is_closed"] is True
                assert candle["close_time"] == candle["open_time"] + 59_999
                original = next(row for row in rig.rows
                                if row["open_time"] == candle["open_time"])
                for field in ("open", "high", "low", "close", "volume"):
                    assert candle[field] == original[field]
            assert all(row["close_time"] <= cutoff for row in engine.closed_candles)

    asyncio.run(scenario())


@pytest.mark.parametrize("available", [True, False], ids=["recoverable", "missing_prefix"])
def test_left_truncated_bootstrap_requires_explicit_window_prefix(fake_clients, available):
    async def scenario():
        rig = fake_clients
        # La primera fila recibida no puede redefinir la ventana solicitada.
        rig.history = [dict(row) for row in rig.rows[100:]]
        delay = DelayedHTTP(rig, ranged_only=True)

        async def truncated(request):
            rows = await delay(request)
            return [] if request["start_ts"] is not None and not available else rows

        rig.responder = truncated
        engine = _engine(rig)
        async with _running(engine, rig) as (_task, ws):
            ws.connect()
            await asyncio.wait_for(delay.entered.wait(), timeout=1)
            cutoff_exclusive = rig.clock.now // MINUTE_MS * MINUTE_MS
            expected_start = cutoff_exclusive - HISTORY_DEPTH * MINUTE_MS
            first_received = rig.history[0]["open_time"]
            request = rig.requests[-1]
            assert request["start_ts"] == expected_start
            assert first_received - MINUTE_MS <= request["end_ts"] < first_received
            state = _snapshot(engine)
            assert state["connected"] and state["history_loaded"]
            assert state["recovering"] and state["pending_gaps"] > 0
            assert not state["complete"] and not state["entries_allowed"]
            assert not state["stale"]
            delay.release.set()
            await _spin(lambda: not engine.snapshot()["recovering"])
            if available:
                _assert_ready(engine)
                assert len(engine.closed_candles) == HISTORY_DEPTH
                assert engine.closed_candles[0]["open_time"] == expected_start
                assert [row["open_time"] for row in engine.closed_candles] == [
                    row["open_time"] for row in rig.rows
                ]
            else:
                state = _snapshot(engine)
                assert state["pending_gaps"] > 0
                assert not state["complete"] and not state["entries_allowed"]
                assert expected_start not in {row["open_time"] for row in engine.closed_candles}

    asyncio.run(scenario())


@pytest.mark.parametrize("margin", [0.0, 15.5, 30.0])
def test_snapshot_refreshes_age_at_inclusive_boundary_and_one_ms_later(fake_clients, margin):
    async def scenario():
        rig = fake_clients
        engine = _engine(rig, margin=margin)
        async with _running(engine, rig) as (_task, ws):
            state = await _connect_ready(engine, ws)
            last_close = state["last_closed_close_time"]
            maximum = int((60.0 + margin) * 1000)
            assert state["max_candle_age_ms"] == maximum
            rig.clock.now = last_close + maximum
            assert _assert_ready(engine)["last_closed_close_time"] == last_close
            rig.clock.now += 1
            state = _snapshot(engine)  # Sin mensajes ni sleeps de reloj real.
            assert state["stale"] is True
            assert state["entries_allowed"] is False
            assert state["connected"] and state["history_loaded"]
            assert state["complete"] and state["pending_gaps"] == 0
            assert state["last_closed_close_time"] == last_close

    asyncio.run(scenario())


def test_forming_messages_and_old_close_reception_never_refresh_freshness(fake_clients):
    async def scenario():
        rig = fake_clients
        engine = _engine(rig)
        updates = []

        async def updated(candle):
            updates.append(candle)

        engine.on_bar_updated(updated)
        async with _running(engine, rig) as (_task, ws):
            state = await _connect_ready(engine, ws)
            last_close = state["last_closed_close_time"]
            ws.message(_kline(5, closed=False))
            await _spin(lambda: len(updates) == 1)
            assert _assert_ready(engine)["last_closed_close_time"] == last_close
            rig.clock.now = last_close + 90_001
            ws.message(_kline(5, closed=False, volume=999.0))
            ws.message(_kline(4))  # Una recepción reciente no cambia el cierre viejo.
            await _spin(lambda: len(updates) == 2)
            state = _snapshot(engine)
            assert state["stale"] and not state["entries_allowed"]
            assert state["last_closed_close_time"] == last_close
            assert all(candle["is_closed"] is False for candle in updates)
            assert _rest_candle(5)["open_time"] not in {
                candle["open_time"] for candle in engine.closed_candles
            }

    asyncio.run(scenario())


def test_bootstrap_history_is_required_even_with_fresh_streamed_close(fake_clients):
    async def scenario():
        rig = fake_clients
        delay = DelayedHTTP(rig)
        rig.responder = delay
        engine = _engine(rig)
        async with _running(engine, rig) as (_task, ws):
            ws.connect()
            await asyncio.wait_for(delay.entered.wait(), timeout=1)
            ws.message(_kline(4))
            await _spin(lambda: engine.snapshot()["last_closed_close_time"]
                        == _rest_candle(4)["close_time"])
            state = _snapshot(engine)
            assert state["connected"] and state["recovering"]
            assert not state["history_loaded"]
            assert not state["stale"]
            assert not state["entries_allowed"]
            delay.release.set()
            await _spin(lambda: engine.snapshot()["entries_allowed"])
            _assert_ready(engine)

    asyncio.run(scenario())


def test_delayed_bootstrap_recovers_gap_before_first_streamed_close(fake_clients):
    async def scenario():
        rig = fake_clients
        bootstrap_rows = [dict(row) for row in rig.rows]
        bootstrap = DelayedHTTP(rig)
        backfill = DelayedHTTP(rig, ranged_only=True)

        async def delayed(request):
            if request["start_ts"] is None:
                await bootstrap(request)
                return bootstrap_rows
            return await backfill(request)

        rig.responder = delayed
        engine = _engine(rig)
        async with _running(engine, rig) as (_task, ws):
            ws.connect()
            await asyncio.wait_for(bootstrap.entered.wait(), timeout=1)
            assert rig.requests[0]["end_ts"] == BASE_MS + 5 * MINUTE_MS - 1
            rig.clock.now = BASE_MS + 7 * MINUTE_MS + 10_000
            rig.rows.extend(_rest_candle(i) for i in (5, 6))
            ws.message(_kline(6))  # Primer mensaje: el builder no tiene un cierre anterior.
            await _spin(lambda: engine.snapshot()["last_closed_close_time"]
                        == _rest_candle(6)["close_time"])
            assert not _snapshot(engine)["entries_allowed"]
            bootstrap.release.set()
            await asyncio.wait_for(backfill.entered.wait(), timeout=1)
            request = rig.requests[-1]
            assert request["start_ts"] == _rest_candle(5)["open_time"]
            assert _rest_candle(5)["open_time"] <= request["end_ts"] < _rest_candle(6)["open_time"]
            state = _snapshot(engine)
            assert state["history_loaded"] and state["recovering"]
            assert state["pending_gaps"] > 0
            assert not state["complete"] and not state["entries_allowed"]
            assert state["last_closed_close_time"] == _rest_candle(6)["close_time"]
            assert _rest_candle(5)["open_time"] not in {
                row["open_time"] for row in engine.closed_candles
            }
            backfill.release.set()
            await _spin(lambda: engine.snapshot()["entries_allowed"])
            _assert_ready(engine)
            assert _rest_candle(5)["open_time"] in {
                row["open_time"] for row in engine.closed_candles
            }
            assert engine.closed_candles[-1]["open_time"] == _rest_candle(6)["open_time"]

    asyncio.run(scenario())


def test_streamed_closed_candle_wins_conflict_with_pending_bootstrap(fake_clients):
    async def scenario():
        rig = fake_clients
        rig.rows[-1]["volume"] = 10.0
        delay = DelayedHTTP(rig)
        rig.responder = delay
        engine = _engine(rig)
        async with _running(engine, rig) as (_task, ws):
            ws.connect()
            await asyncio.wait_for(delay.entered.wait(), timeout=1)
            streamed = _kline(4, volume=777.0)
            expected = {field: streamed[field] for field in (
                "open_time", "close_time", "open", "high", "low", "close", "volume", "is_closed",
            )}
            ws.message(streamed)
            await _spin(lambda: engine.snapshot()["last_closed_close_time"]
                        == streamed["close_time"])
            assert engine.closed_candles[-1] == expected
            assert not _snapshot(engine)["entries_allowed"]
            delay.release.set()
            await _spin(lambda: engine.snapshot()["entries_allowed"])
            _assert_ready(engine)
            assert any(row["open_time"] == streamed["open_time"] and row["volume"] == 10.0
                       for _request, rows in rig.responses for row in rows)
            assert engine.closed_candles[-1] == expected
            assert isinstance(engine.state.current_bar_closed, dict)
            assert engine.state.current_bar_closed == expected

    asyncio.run(scenario())


@pytest.mark.parametrize("result", ["empty", "error"])
def test_unsuccessful_bootstrap_never_allows_entries(fake_clients, result):
    async def scenario():
        rig = fake_clients
        finished = asyncio.Event()

        async def unsuccessful(_request):
            finished.set()
            if result == "error":
                raise RuntimeError("REST de prueba no disponible")
            return []

        rig.responder = unsuccessful
        engine = _engine(rig)
        async with _running(engine, rig) as (_task, ws):
            ws.connect()
            await asyncio.wait_for(finished.wait(), timeout=1)
            await _spin(lambda: not engine.snapshot()["recovering"])
            state = _snapshot(engine)
            assert state["connected"]
            assert not state["complete"]
            assert state["stale"]
            assert not state["entries_allowed"]
            if result == "error":
                assert not state["history_loaded"]

    asyncio.run(scenario())


def test_reconnect_blocks_until_delayed_http_finishes_despite_new_closes(fake_clients):
    async def scenario():
        rig = fake_clients
        engine = _engine(rig)
        async with _running(engine, rig) as (task, ws):
            await _connect_ready(engine, ws)
            rig.clock.now += MINUTE_MS
            rig.rows.append(_rest_candle(5))
            ws.disconnect()
            state = _snapshot(engine)
            assert not state["connected"] and state["reconnecting"]
            assert not state["stale"] and not state["entries_allowed"]
            delay = DelayedHTTP(rig)
            rig.responder = delay
            ws.connect()
            await asyncio.wait_for(delay.entered.wait(), timeout=1)
            state = _snapshot(engine)
            assert state["connected"] and state["recovering"]
            assert not state["entries_allowed"]
            ws.message(_kline(5))
            rig.clock.now += MINUTE_MS
            rig.rows.append(_rest_candle(6))
            ws.message(_kline(6))
            await _spin(lambda: engine.snapshot()["last_closed_close_time"]
                        == _rest_candle(6)["close_time"])
            state = _snapshot(engine)
            assert state["recovering"] and not state["stale"]
            assert not state["entries_allowed"]
            assert not task.done()
            assert len(rig.http_instances) == len(rig.ws_instances) == 1
            delay.release.set()
            await _spin(lambda: engine.snapshot()["entries_allowed"])
            assert _assert_ready(engine)["last_closed_close_time"] == _rest_candle(6)["close_time"]

    asyncio.run(scenario())


@pytest.mark.parametrize("missing_index", [2, 4], ids=["interior", "latest_closed"])
def test_gap_inside_or_at_end_of_bootstrap_is_recovered_before_ready(fake_clients, missing_index):
    async def scenario():
        rig = fake_clients
        rig.history = [dict(row) for row in rig.rows
                       if row["open_time"] != _rest_candle(missing_index)["open_time"]]
        delay = DelayedHTTP(rig, ranged_only=True)
        rig.responder = delay
        engine = _engine(rig)
        async with _running(engine, rig) as (_task, ws):
            ws.connect()
            await asyncio.wait_for(delay.entered.wait(), timeout=1)
            state = _snapshot(engine)
            assert state["recovering"]
            assert state["pending_gaps"] > 0
            assert not state["complete"] and not state["entries_allowed"]
            assert not state["stale"]
            request = rig.requests[-1]
            missing = _rest_candle(missing_index)
            assert request["start_ts"] == missing["open_time"]
            assert missing["open_time"] <= request["end_ts"] <= missing["close_time"]
            delay.release.set()
            await _spin(lambda: engine.snapshot()["entries_allowed"])
            _assert_ready(engine)
            assert [row["open_time"] for row in engine.closed_candles] == [
                _rest_candle(i)["open_time"] for i in range(HISTORY_START_INDEX, 5)
            ]

    asyncio.run(scenario())


@pytest.mark.parametrize("result", ["partial", "error"])
def test_partial_or_failed_gap_recovery_keeps_market_incomplete(fake_clients, result):
    async def scenario():
        rig = fake_clients
        engine = _engine(rig)
        async with _running(engine, rig) as (_task, ws):
            await _connect_ready(engine, ws)
            fetched = asyncio.Event()

            async def incomplete(request):
                assert request["start_ts"] is not None
                assert request["end_ts"] is not None
                fetched.set()
                if result == "error":
                    raise RuntimeError("backfill de prueba fallido")
                missing = _rest_candle(5)
                if request["start_ts"] <= missing["open_time"] <= request["end_ts"]:
                    return [missing]
                return []

            rig.responder = incomplete
            rig.clock.now = BASE_MS + 8 * MINUTE_MS + 10_000
            ws.message(_kline(7))  # Faltan las velas 5 y 6.
            await asyncio.wait_for(fetched.wait(), timeout=1)
            await _spin(lambda: not engine.snapshot()["recovering"])
            state = _snapshot(engine)
            assert state["connected"] and state["history_loaded"]
            assert state["pending_gaps"] > 0
            assert not state["complete"] and not state["entries_allowed"]
            assert not state["stale"]
            assert state["last_closed_close_time"] == _rest_candle(7)["close_time"]
            assert _rest_candle(6)["open_time"] not in {
                candle["open_time"] for candle in engine.closed_candles
            }
            # Un cierre aún más reciente no borra el hueco que quedó sin recuperar.
            rig.clock.now += MINUTE_MS
            ws.message(_kline(8))
            await _spin(lambda: engine.snapshot()["last_closed_close_time"]
                        == _rest_candle(8)["close_time"])
            state = _snapshot(engine)
            assert state["pending_gaps"] > 0
            assert not state["complete"] and not state["entries_allowed"]

    asyncio.run(scenario())


def test_gap_recovery_pages_more_than_one_thousand_missing_candles(fake_clients):
    async def scenario():
        rig = fake_clients
        engine = _engine(rig)
        async with _running(engine, rig) as (_task, ws):
            await _connect_ready(engine, ws)
            last_index = 1207
            rig.rows = [_rest_candle(i) for i in range(HISTORY_START_INDEX, last_index + 1)]
            rig.clock.now = BASE_MS + (last_index + 1) * MINUTE_MS + 10_000
            previous_calls = len(rig.requests)
            ws.message(_kline(last_index))
            await _spin(lambda: len(rig.requests) > previous_calls)
            await _spin(lambda: engine.snapshot()["entries_allowed"], timeout=2)
            state = _assert_ready(engine)
            assert state["last_closed_close_time"] == _rest_candle(last_index)["close_time"]
            pages = rig.requests[previous_calls:]
            assert len(pages) >= 2
            assert pages[0]["start_ts"] == _rest_candle(5)["open_time"]
            starts = [request["start_ts"] for request in pages]
            assert all(start is not None for start in starts)
            assert starts == sorted(set(starts))
            assert all(request["end_ts"] is not None for request in pages)
            delivered = {
                row["open_time"]
                for request, rows in rig.responses
                if request["start_ts"] is not None
                for row in rows
            }
            assert {_rest_candle(i)["open_time"] for i in range(5, last_index)} <= delivered
            retained = [row["open_time"] for row in engine.closed_candles]
            assert retained == sorted(set(retained))
            assert retained[-1] == _rest_candle(last_index)["open_time"]

    asyncio.run(scenario())


def test_recovery_runs_independently_of_blocked_event_dispatch(fake_clients):
    async def scenario():
        rig = fake_clients
        engine = _engine(rig)
        async with _running(engine, rig) as (_task, ws):
            await _connect_ready(engine, ws)
            blocked = asyncio.Event()

            async def slow_update(_candle):
                blocked.set()
                await asyncio.Event().wait()

            off = engine.on_bar_updated(slow_update)
            ws.message(_kline(5, closed=False))
            await asyncio.wait_for(blocked.wait(), timeout=1)
            delay = DelayedHTTP(rig, ranged_only=True)
            rig.responder = delay
            rig.rows.extend(_rest_candle(i) for i in range(5, 8))
            rig.clock.now = BASE_MS + 8 * MINUTE_MS + 10_000
            ws.message(_kline(7))
            await asyncio.wait_for(delay.entered.wait(), timeout=1)
            state = _snapshot(engine)
            assert state["recovering"] and state["pending_gaps"] > 0
            assert not state["entries_allowed"]
            assert _rest_candle(5)["open_time"] not in {
                candle["open_time"] for candle in engine.closed_candles
            }  # Cambiar de intervalo no confirma la vela provisional anterior.
            delay.release.set()
            await _spin(lambda: engine.snapshot()["entries_allowed"])
            _assert_ready(engine)
            off()
        assert asyncio.all_tasks() == {asyncio.current_task()}

    asyncio.run(scenario())


@pytest.mark.parametrize("criterion", ["disconnected", "recovering", "gap"])
def test_stale_combines_with_other_blocking_criteria(fake_clients, criterion):
    async def scenario():
        rig = fake_clients
        engine = _engine(rig)
        async with _running(engine, rig) as (_task, ws):
            state = await _connect_ready(engine, ws)
            last_close = state["last_closed_close_time"]
            if criterion in ("disconnected", "recovering"):
                ws.disconnect()
            if criterion in ("recovering", "gap"):
                delay = DelayedHTTP(rig)
                rig.responder = delay
                if criterion == "recovering":
                    ws.connect()
                else:
                    rig.clock.now = BASE_MS + 8 * MINUTE_MS + 10_000
                    ws.message(_kline(7))
                    last_close = _rest_candle(7)["close_time"]
                await asyncio.wait_for(delay.entered.wait(), timeout=1)
            rig.clock.now = last_close + 90_001
            state = _snapshot(engine)
            assert state["stale"] and not state["entries_allowed"]
            if criterion == "disconnected":
                assert not state["connected"] and state["reconnecting"]
            elif criterion == "recovering":
                assert state["connected"] and state["recovering"]
            else:
                assert state["pending_gaps"] > 0 and not state["complete"]

    asyncio.run(scenario())


@pytest.mark.parametrize("stage", ["bootstrap", "gap"])
def test_shutdown_cancels_delayed_recovery_and_releases_all_resources(fake_clients, stage):
    async def scenario():
        rig = fake_clients
        engine = _engine(rig)
        async with _running(engine, rig) as (task, ws):
            if stage == "gap":
                await _connect_ready(engine, ws)
            delay = DelayedHTTP(rig)
            rig.responder = delay
            if stage == "bootstrap":
                ws.connect()
            else:
                rig.clock.now = BASE_MS + 8 * MINUTE_MS + 10_000
                ws.message(_kline(7))
            await asyncio.wait_for(delay.entered.wait(), timeout=1)
            assert _snapshot(engine)["recovering"]
            await asyncio.wait_for(engine.stop(), timeout=1)
            await asyncio.wait_for(task, timeout=1)
            await asyncio.wait_for(delay.cancelled.wait(), timeout=1)
            state = _snapshot(engine)
            assert not state["connected"] and not state["reconnecting"]
            assert not state["recovering"] and not state["entries_allowed"]
            assert rig.http_instances[0].close_count == 1
            assert ws.stop_count == 1
            delay.release.set()
            ws.connect()
            ws.message(_kline(8))
            assert not _snapshot(engine)["entries_allowed"]
        assert asyncio.all_tasks() == {asyncio.current_task()}

    asyncio.run(scenario())


def test_bar_closed_handler_stop_prevents_later_blocked_handler_and_orphan_tasks(fake_clients):
    async def scenario():
        rig = fake_clients
        engine = _engine(rig)
        halted = asyncio.Event()
        second_invoked = asyncio.Event()

        async def stop_on_close(_candle):
            await engine.stop()
            halted.set()

        async def blocked_after_stop(_candle):
            second_invoked.set()
            await asyncio.Event().wait()

        async with _running(engine, rig) as (task, ws):
            await _connect_ready(engine, ws)
            off_first = engine.on_bar_closed(stop_on_close)
            off_second = engine.on_bar_closed(blocked_after_stop)
            rig.clock.now += MINUTE_MS
            ws.message(_kline(5))
            await asyncio.wait_for(halted.wait(), timeout=1)
            await asyncio.wait_for(task, timeout=1)
            assert not second_invoked.is_set()
            assert not _snapshot(engine)["entries_allowed"]
            off_first()
            off_second()
        assert rig.http_instances[0].close_count == 1
        assert rig.ws_instances[0].stop_count == 1
        assert asyncio.all_tasks() == {asyncio.current_task()}

    asyncio.run(scenario())


def test_cancelled_stop_preserves_delayed_http_cleanup_for_second_stop(fake_clients):
    async def scenario():
        rig = fake_clients
        engine = _engine(rig)
        stop_tasks = []
        async with _running(engine, rig) as (task, ws):
            await _connect_ready(engine, ws)
            http = rig.http_instances[0]
            rig.close_release = asyncio.Event()
            try:
                first_stop = asyncio.create_task(engine.stop())
                stop_tasks.append(first_stop)
                await asyncio.wait_for(http.close_entered.wait(), timeout=1)
                first_stop.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await asyncio.wait_for(first_stop, timeout=1)
                assert not http.close_cancelled.is_set()
                assert not http.close_finished.is_set()
                second_entered = asyncio.Event()

                async def stop_again():
                    second_entered.set()
                    await engine.stop()

                second_stop = asyncio.create_task(stop_again())
                stop_tasks.append(second_stop)
                await asyncio.wait_for(second_entered.wait(), timeout=1)
                assert not second_stop.done()  # Debe esperar al cleanup ya iniciado.
                rig.close_release.set()
                await asyncio.wait_for(second_stop, timeout=1)
                await asyncio.wait_for(task, timeout=1)
                assert http.close_finished.is_set()
                assert not http.close_cancelled.is_set()
                assert http.close_count == 1 and ws.stop_count == 1
                assert not _snapshot(engine)["entries_allowed"]
            finally:
                rig.close_release.set()
                await asyncio.wait_for(
                    asyncio.gather(*stop_tasks, return_exceptions=True), timeout=1,
                )
        assert http.close_count == 1 and ws.stop_count == 1
        assert asyncio.all_tasks() == {asyncio.current_task()}

    asyncio.run(scenario())


@pytest.fixture
def lifecycle_api(monkeypatch, fake_clients):
    rig = fake_clients
    rig.auto_connect = True
    engines, starts, stops = [], [], []
    margin = 37.5
    monkeypatch.setattr(main, "app_config", Config(market_freshness_margin_seconds=margin))
    monkeypatch.setattr(market_api, "_market_engine", None)

    def factory(config, **kwargs):
        engine = MarketEngine(config, clock_ms=rig.clock, **kwargs)
        engines.append(engine)
        original_start, original_stop = engine.start, engine.stop

        async def start(symbol):
            starts.append(symbol)
            await original_start(symbol)

        async def stop():
            stops.append(engine)
            await original_stop()

        monkeypatch.setattr(engine, "start", start)
        monkeypatch.setattr(engine, "stop", stop)
        return engine

    # El símbolo de main forma parte del contrato nuevo, aunque aún no esté importado.
    monkeypatch.setattr(main, "MarketEngine", factory, raising=False)
    return SimpleNamespace(
        rig=rig, engines=engines, starts=starts, stops=stops, margin=margin,
    )


def _api_ready(client, engine):
    async def wait():
        await _spin(lambda: engine.snapshot()["entries_allowed"])

    client.portal.call(wait)


def _api_handlers(client, engine):
    async def counts():
        return _handler_counts(engine)

    return client.portal.call(counts)


def _api_wait_handlers(client, engine, expected):
    async def wait():
        await _spin(lambda: _handler_counts(engine) == expected)

    client.portal.call(wait)


def _receive_json(browser):
    # La cola ASGI de TestClient permite limitar también las esperas de mensajes.
    async def receive():
        message = await asyncio.wait_for(browser._send_rx.receive(), timeout=1)
        assert message["type"] == "websocket.send", message
        return json.loads(message["text"])

    return browser.portal.call(receive)


def test_lifespan_starts_one_configured_engine_and_cleans_global_on_exit(lifecycle_api):
    api = lifecycle_api
    with TestClient(main.app) as client:
        assert len(api.engines) == 1
        engine = api.engines[0]
        assert main.app.state.market_engine is engine
        assert market_api._market_engine is engine
        _api_ready(client, engine)
        assert api.starts == [SYMBOL]
        assert len(api.rig.http_instances) == len(api.rig.ws_instances) == 1
        assert engine.snapshot()["max_candle_age_ms"] == int((60 + api.margin) * 1000)
        status = client.get("/api/market/status")
        assert status.status_code == 200
        assert status.json()["entries_allowed"] is True
        assert status.json()["interval"] == "1m"
        health = client.get("/health").json()
        assert health["environment"] == "paper" and health["is_live"] is False
    assert api.stops == [engine]
    assert api.rig.http_instances[0].close_count == 1
    assert api.rig.ws_instances[0].stop_count == 1
    assert market_api._market_engine is None
    assert not engine.snapshot()["entries_allowed"]


def test_status_endpoint_recalculates_freshness_with_configured_clock(lifecycle_api):
    api = lifecycle_api
    with TestClient(main.app) as client:
        assert len(api.engines) == 1
        engine = api.engines[0]
        _api_ready(client, engine)
        last_close = engine.snapshot()["last_closed_close_time"]
        api.rig.clock.now = last_close + int((60 + api.margin) * 1000)
        response = client.get("/api/market/status")
        assert response.status_code == 200
        assert response.json()["stale"] is False
        assert response.json()["entries_allowed"] is True
        api.rig.clock.now += 1
        response = client.get("/api/market/status")
        assert response.status_code == 200
        assert response.json()["stale"] is True
        assert response.json()["entries_allowed"] is False
        assert response.json()["last_closed_close_time"] == last_close
    assert market_api._market_engine is None


@pytest.mark.parametrize("limit", [0, -1, 501])
def test_candles_endpoint_rejects_limits_outside_one_to_five_hundred(lifecycle_api, limit):
    api = lifecycle_api
    with TestClient(main.app) as client:
        assert len(api.engines) == 1
        _api_ready(client, api.engines[0])
        response = client.get("/api/market/candles", params={"limit": limit})
        assert response.status_code == 422
    assert market_api._market_engine is None


def test_two_browser_tabs_share_engine_and_each_disconnect_removes_handlers(lifecycle_api):
    api = lifecycle_api
    with TestClient(main.app) as client:
        assert len(api.engines) == 1
        engine = api.engines[0]
        _api_ready(client, engine)
        baseline = _api_handlers(client, engine)
        one_tab = tuple(count + 1 for count in baseline)
        two_tabs = tuple(count + 2 for count in baseline)
        with client.websocket_connect("/api/market/ws") as first:
            first.send_json({"type": "subscribe", "channel": "candles"})
            assert _receive_json(first) == {"type": "subscribed", "channel": "candles"}
            _api_wait_handlers(client, engine, one_tab)
            with client.websocket_connect("/api/market/ws") as second:
                second.send_json({"type": "subscribe", "channel": "candles"})
                assert _receive_json(second) == {"type": "subscribed", "channel": "candles"}
                _api_wait_handlers(client, engine, two_tabs)

                async def emit():
                    api.rig.clock.now += MINUTE_MS
                    api.rig.ws_instances[0].message(_kline(5))

                client.portal.call(emit)
                for browser in (first, second):
                    event = _receive_json(browser)
                    assert event["type"] == "bar_closed"
                    assert event["candle"]["close_time"] == _rest_candle(5)["close_time"]
                assert main.app.state.market_engine is engine
                assert market_api._market_engine is engine
                assert api.starts == [SYMBOL] and api.stops == []
                assert len(api.rig.ws_instances) == 1
                # Desconexión ASGI ordenada antes de que TestClient cancele su sesión.
                second.close()
                _api_wait_handlers(client, engine, one_tab)
            _api_wait_handlers(client, engine, one_tab)
            first.send_json({"type": "ping"})
            assert _receive_json(first) == {"type": "pong"}
            first.close()
            _api_wait_handlers(client, engine, baseline)
        _api_wait_handlers(client, engine, baseline)
        # Una nueva pestaña no acumula callbacks de las dos anteriores.
        with client.websocket_connect("/api/market/ws") as third:
            third.send_json({"type": "ping"})
            assert _receive_json(third) == {"type": "pong"}
            _api_wait_handlers(client, engine, one_tab)
            third.close()
            _api_wait_handlers(client, engine, baseline)
        _api_wait_handlers(client, engine, baseline)
    assert api.stops == [engine]
    assert market_api._market_engine is None


class FakeBrowser:
    """Endpoint directo: bloqueo de send_json controlado, sin sockets reales."""

    def __init__(self, *, slow=False, block_close=False):
        self.slow = slow
        self.block_close = block_close
        self.incoming = asyncio.Queue()
        self.sent = []
        self.accepted = asyncio.Event()
        self.blocked = asyncio.Event()
        self.release = asyncio.Event()
        self.send_cancelled = asyncio.Event()
        self.close_entered = asyncio.Event()
        self.close_release = asyncio.Event()
        self.close_cancelled = asyncio.Event()
        self.close_finished = asyncio.Event()
        self.close_calls = []
        self.incoming.put_nowait({"type": "subscribe", "channel": "candles"})

    async def accept(self):
        self.accepted.set()

    async def receive_text(self):
        message = await self.incoming.get()
        if message is None:
            raise WebSocketDisconnect(code=1000)
        return json.dumps(message)

    async def send_json(self, message):
        if self.slow and message["type"] == "bar_closed":
            self.blocked.set()
            try:
                await self.release.wait()
            except asyncio.CancelledError:
                self.send_cancelled.set()
                raise
        self.sent.append(dict(message))

    async def close(self, code=1000, reason=None):
        self.close_calls.append({"code": code, "reason": reason})
        self.close_entered.set()
        if self.block_close:
            try:
                await self.close_release.wait()
            except asyncio.CancelledError:
                self.close_cancelled.set()
                raise
        self.close_finished.set()
        self.disconnect()

    def disconnect(self):
        self.incoming.put_nowait(None)


@pytest.mark.parametrize("shutdown_first", [False, True], ids=["disconnect", "shutdown"])
def test_slow_browser_does_not_block_other_consumers_or_shutdown(
    fake_clients, monkeypatch, shutdown_first,
):
    async def scenario():
        rig = fake_clients
        engine = _engine(rig)
        monkeypatch.setattr(market_api, "_market_engine", engine)
        browsers = [FakeBrowser(slow=True), FakeBrowser()]
        endpoints = []
        try:
            async with _running(engine, rig) as (task, ws):
                await _connect_ready(engine, ws)
                baseline = _handler_counts(engine)
                endpoints = [asyncio.create_task(market_api.market_websocket(browser))
                             for browser in browsers]
                await _spin(lambda: all(any(message["type"] == "subscribed"
                                           for message in browser.sent)
                                       for browser in browsers))
                assert _handler_counts(engine) == tuple(count + 2 for count in baseline)
                rig.clock.now += MINUTE_MS
                ws.message(_kline(5))
                await asyncio.wait_for(browsers[0].blocked.wait(), timeout=1)
                rig.clock.now += MINUTE_MS
                ws.message(_kline(6))
                await _spin(lambda: len([message for message in browsers[1].sent
                                        if message["type"] == "bar_closed"]) == 2)
                _assert_ready(engine)
                if shutdown_first:
                    # El envío lento sigue bloqueado al detener el mercado.
                    assert not browsers[0].release.is_set()
                    await asyncio.wait_for(engine.stop(), timeout=1)
                    await asyncio.wait_for(task, timeout=1)
                browsers[0].disconnect()
                await asyncio.wait_for(endpoints[0], timeout=1)
                await asyncio.wait_for(browsers[0].send_cancelled.wait(), timeout=1)
                assert _handler_counts(engine) == tuple(count + 1 for count in baseline)
                await asyncio.wait_for(engine.stop(), timeout=1)
                await asyncio.wait_for(task, timeout=1)
                browsers[1].disconnect()
                await asyncio.wait_for(endpoints[1], timeout=1)
                assert _handler_counts(engine) == baseline
            assert rig.http_instances[0].close_count == 1
            assert rig.ws_instances[0].stop_count == 1
            assert asyncio.all_tasks() == {asyncio.current_task()}
        finally:
            for endpoint in endpoints:
                if not endpoint.done():
                    endpoint.cancel()
            if endpoints:
                await asyncio.wait_for(
                    asyncio.gather(*endpoints, return_exceptions=True), timeout=1,
                )

    asyncio.run(scenario())


def test_slow_browser_queue_overflow_cleans_handlers_before_bounded_blocked_close(
    fake_clients, monkeypatch,
):
    async def scenario():
        rig = fake_clients
        engine = _engine(rig)
        monkeypatch.setattr(market_api, "_market_engine", engine)
        browser = FakeBrowser(slow=True, block_close=True)
        endpoint = None
        try:
            async with _running(engine, rig) as (_task, ws):
                await _connect_ready(engine, ws)
                baseline = _handler_counts(engine)
                endpoint = asyncio.create_task(market_api.market_websocket(browser))
                await _spin(lambda: any(message["type"] == "subscribed"
                                       for message in browser.sent))
                assert _handler_counts(engine) == tuple(count + 1 for count in baseline)
                rig.clock.now += MINUTE_MS
                ws.message(_kline(5))
                await asyncio.wait_for(browser.blocked.wait(), timeout=1)
                # 130 cierres: uno en send_json, 128 en cola y uno provoca overflow.
                rig.clock.now = BASE_MS + 135 * MINUTE_MS + 10_000
                for index in range(6, 135):
                    ws.message(_kline(index))
                await asyncio.wait_for(browser.close_entered.wait(), timeout=1)
                assert not browser.close_release.is_set()
                await _spin(lambda: browser.send_cancelled.is_set()
                            and _handler_counts(engine) == baseline)
                assert len(browser.close_calls) == 1
                assert browser.close_calls[0]["code"] == 1013
                # close tiene presupuesto de 1 s; el test permite hasta 2 s.
                await asyncio.wait_for(asyncio.shield(endpoint), timeout=2)
                assert browser.close_cancelled.is_set()
                assert not browser.close_finished.is_set()
                assert not any(message["type"] == "bar_closed" for message in browser.sent)
                assert _handler_counts(engine) == baseline
                _assert_ready(engine)
            assert rig.http_instances[0].close_count == 1
            assert rig.ws_instances[0].stop_count == 1
            assert asyncio.all_tasks() == {asyncio.current_task()}
        finally:
            browser.close_release.set()
            browser.release.set()
            browser.disconnect()
            if endpoint is not None:
                if not endpoint.done():
                    endpoint.cancel()
                await asyncio.wait_for(
                    asyncio.gather(endpoint, return_exceptions=True), timeout=1,
                )

    asyncio.run(scenario())


@pytest.mark.parametrize("cancellation", ["asyncio", "anyio"])
def test_cancelled_lifespan_finishes_cleanup(lifecycle_api, cancellation):
    """Tanto cancelación de tarea como de scope ASGI liberan el motor único."""
    async def scenario():
        entered = asyncio.Event()

        async def serve():
            async with main.lifespan(main.app):
                entered.set()
                await asyncio.Event().wait()

        if cancellation == "asyncio":
            task = asyncio.create_task(serve())
            await asyncio.wait_for(entered.wait(), timeout=1)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(task, timeout=1)
        else:
            async with anyio.create_task_group() as group:
                group.start_soon(serve)
                await asyncio.wait_for(entered.wait(), timeout=1)
                group.cancel_scope.cancel()
        assert main.app.state.market_engine is None
        assert market_api._market_engine is None
        assert len(lifecycle_api.engines) == 1
        assert lifecycle_api.rig.http_instances[0].close_count == 1
        assert lifecycle_api.rig.ws_instances[0].stop_count == 1
        assert asyncio.all_tasks() == {asyncio.current_task()}

    asyncio.run(scenario())


@pytest.mark.parametrize("cancellation", ["asyncio", "anyio"])
def test_cancelled_browser_removes_handlers_and_collects_writer(fake_clients, monkeypatch, cancellation):
    """Cancelar la sesión ASGI con send_json pendiente no deja tareas ni callbacks."""
    async def scenario():
        rig = fake_clients
        engine = _engine(rig)
        browser = FakeBrowser(slow=True)
        monkeypatch.setattr(market_api, "_market_engine", engine)
        async with _running(engine, rig) as (_task, ws):
            await _connect_ready(engine, ws)
            baseline = _handler_counts(engine)

            async def wait_for_writer():
                await _spin(lambda: any(message["type"] == "subscribed" for message in browser.sent))
                rig.clock.now += MINUTE_MS
                ws.message(_kline(5))
                await asyncio.wait_for(browser.blocked.wait(), timeout=1)

            if cancellation == "asyncio":
                task = asyncio.create_task(market_api.market_websocket(browser))
                await wait_for_writer()
                task.cancel()
                await asyncio.wait_for(task, timeout=1)
            else:
                async with anyio.create_task_group() as group:
                    group.start_soon(market_api.market_websocket, browser)
                    await wait_for_writer()
                    group.cancel_scope.cancel()
            assert browser.send_cancelled.is_set()
            assert _handler_counts(engine) == baseline
        assert asyncio.all_tasks() == {asyncio.current_task()}

    asyncio.run(scenario())


# ============================================================
# TASK 01 — WS 1m GRID VALIDATION
# ============================================================

def test_ws_misaligned_1m_candle_is_rejected(fake_clients):
    engine = _engine(fake_clients)
    row = {
        "open_time": BASE_MS + 30_000,
        "close_time": BASE_MS + 30_000 + MINUTE_MS - 1,
        "open": 100.0, "high": 102.0, "low": 99.0,
        "close": 101.0, "volume": 10.0, "is_closed": True,
    }
    assert engine._validated_candle(row, streaming=True) is None


def test_ws_aligned_1m_candle_behaves_normally(fake_clients):
    engine = _engine(fake_clients)
    row = {
        "open_time": BASE_MS,
        "close_time": BASE_MS + MINUTE_MS - 1,
        "open": 100.0, "high": 102.0, "low": 99.0,
        "close": 101.0, "volume": 10.0, "is_closed": True,
    }
    result = engine._validated_candle(row, streaming=True)
    assert result is not None
    assert result["open_time"] == BASE_MS


def test_non_1m_ws_intervals_are_unaffected_by_grid_check(fake_clients):
    engine = _engine(fake_clients)
    engine.config.kline_interval = "5m"
    open_time = BASE_MS + MINUTE_MS
    assert open_time % (5 * MINUTE_MS) != 0
    row = {
        "open_time": open_time,
        "close_time": open_time + 5 * MINUTE_MS - 1,
        "open": 100.0, "high": 102.0, "low": 99.0,
        "close": 101.0, "volume": 10.0, "is_closed": False,
    }
    assert engine._validated_candle(row, streaming=True) is not None


# ============================================================
# TASK 02 — WS EVENT STATE REGRESSION (MKT-WSTG-002)
# ============================================================

def test_ws_misaligned_closed_candle_does_not_affect_engine_state(fake_clients):
    """Vela WS cerrada off-grid: no se añade a closed_candles, no cambia estado global."""
    async def scenario():
        rig = fake_clients
        engine = _engine(rig, margin=120.0)
        bar_events = []

        async def on_closed(payload):
            bar_events.append(("closed", payload["open_time"]))

        async def on_updated(payload):
            bar_events.append(("updated", payload["open_time"]))

        off_closed = engine.on_bar_closed(on_closed)
        off_updated = engine.on_bar_updated(on_updated)
        async with _running(engine, rig) as (task, ws):
            # Llegar a estado listo.
            state = await _connect_ready(engine, ws)
            assert state["complete"] and state["entries_allowed"]

            baseline_closed = list(engine.closed_candles)
            baseline_last_close = engine.state.last_closed_close_time
            baseline_pending = engine.snapshot()["pending_gaps"]
            baseline_entries = engine.snapshot()["entries_allowed"]

            # Avanzar el reloj al siguiente intervalo esperado +30_000.
            rig.clock.now = BASE_MS + 6 * MINUTE_MS + 31_000

            # Inyectar una vela WS cerrada off-grid con duración correcta.
            misaligned_open = BASE_MS + 5 * MINUTE_MS + 30_000
            ws.message({
                "open_time": misaligned_open,
                "close_time": misaligned_open + MINUTE_MS - 1,
                "open": 200.0, "high": 202.0, "low": 199.0,
                "close": 201.0, "volume": 50.0, "is_closed": True,
            })

            await asyncio.sleep(0)

            # No debe añadirse a closed_candles.
            assert engine.closed_candles == baseline_closed

            # last_closed_close_time no cambia.
            assert engine.state.last_closed_close_time == baseline_last_close

            # pending_gaps y entries_allowed sin cambios.
            assert engine.snapshot()["pending_gaps"] == baseline_pending
            assert engine.snapshot()["entries_allowed"] == baseline_entries
            assert bar_events == []

        off_closed()
        off_updated()

    asyncio.run(scenario())


def test_ws_aligned_closed_candle_preserves_normal_flow(fake_clients):
    """Vela WS cerrada aligned: se añade a closed_candles y avanza last_closed_close_time."""
    async def scenario():
        rig = fake_clients
        engine = _engine(rig)
        closed_events = []

        async def on_closed(payload):
            closed_events.append(payload["open_time"])

        off_closed = engine.on_bar_closed(on_closed)
        async with _running(engine, rig) as (task, ws):
            state = await _connect_ready(engine, ws)
            assert state["complete"] and state["entries_allowed"]

            baseline_closed = list(engine.closed_candles)
            baseline_last_close = engine.state.last_closed_close_time
            baseline_pending = engine.snapshot()["pending_gaps"]

            # Avanzar el reloj al siguiente intervalo esperado.
            rig.clock.now = BASE_MS + 6 * MINUTE_MS

            # Enviar la vela siguiente, con open_time aligned.
            aligned_open = BASE_MS + 5 * MINUTE_MS
            ws.message({
                "open_time": aligned_open,
                "close_time": aligned_open + MINUTE_MS - 1,
                "open": 300.0, "high": 302.0, "low": 299.0,
                "close": 301.0, "volume": 60.0, "is_closed": True,
            })

            await asyncio.sleep(0)

            # El buffer de 500 velas rueda; la nueva vela queda al final.
            assert len(engine.closed_candles) == len(baseline_closed)
            assert engine.closed_candles[-1]["open_time"] == aligned_open
            assert engine.closed_candles[0]["open_time"] > baseline_closed[0]["open_time"]

            # last_closed_close_time se actualiza.
            assert engine.state.last_closed_close_time == aligned_open + MINUTE_MS - 1

            # No se crean gaps.
            assert engine.snapshot()["pending_gaps"] == baseline_pending
            assert closed_events == [aligned_open]

        off_closed()

    asyncio.run(scenario())


# ============================================================
# TASK 03 — REST BOOTSTRAP GRID (MKT-RESTG-003)
# ============================================================

def test_bootstrap_with_misaligned_1m_candle_does_not_merge_or_mark_history_loaded(fake_clients):
    """Bootstrap REST con vela desalineada: no se fusiona, history_loaded/entries_allowed permanecen false."""
    async def scenario():
        rig = fake_clients
        # Inyectar una vela desalineada en el bootstrap.
        misaligned_open = BASE_MS + 30_000  # Fuera de la rejilla 1m
        misaligned = {
            "open_time": misaligned_open,
            "close_time": misaligned_open + MINUTE_MS - 1,
            "open": 200.0, "high": 202.0, "low": 199.0,
            "close": 201.0, "volume": 50.0,
        }
        # Reemplazar una vela alineada por la desalineada en el bootstrap.
        rig.history = [dict(row) for row in rig.rows]
        for i, row in enumerate(rig.history):
            if row["open_time"] == _rest_candle(2)["open_time"]:
                rig.history[i] = misaligned
                break
        delay = DelayedHTTP(rig)
        rig.responder = delay

        engine = _engine(rig)
        async with _running(engine, rig) as (_task, ws):
            ws.connect()
            await asyncio.wait_for(delay.entered.wait(), timeout=1)
            # Antes de liberar: bootstrap pendiente.
            state = _snapshot(engine)
            assert state["connected"] and not state["history_loaded"] and state["recovering"]
            delay.release.set()
            await _spin(lambda: not engine.snapshot()["recovering"])
            state = _snapshot(engine)
            assert state["pending_gaps"] > 0
            assert not state["history_loaded"]
            assert not state["complete"] and not state["entries_allowed"]
            assert not state["recovering"]
            # La vela desalineada no se fusiona.
            assert misaligned_open not in {row["open_time"] for row in engine.closed_candles}

    asyncio.run(scenario())


# ============================================================
# TASK 04 — REST GAP-RANGE GRID (MKT-RESTG-004)
# ============================================================

def test_gap_recovery_with_misaligned_1m_candle_does_not_clear_gap_or_advance_readiness(fake_clients):
    """Gap recovery REST con vela desalineada: gap persiste, readiness bloqueada."""
    async def scenario():
        rig = fake_clients
        # Eliminar una vela del histórico para forzar un gap.
        missing_index = 3
        rig.history = [dict(row) for row in rig.rows
                       if row["open_time"] != _rest_candle(missing_index)["open_time"]]
        delay_gap = DelayedHTTP(rig, ranged_only=True)

        async def responder(request):
            if request["start_ts"] is None:
                # Bootstrap normal sin delay.
                return rig.history
            return await delay_gap(request)

        rig.responder = responder
        engine = _engine(rig)
        async with _running(engine, rig) as (_task, ws):
            ws.connect()
            # Esperar a que se detecte el gap durante bootstrap y se inicie la recuperación ranged.
            await _spin(lambda: len(rig.requests) > 1)
            await asyncio.wait_for(delay_gap.entered.wait(), timeout=1)
            state = _snapshot(engine)
            assert state["recovering"] and state["pending_gaps"] > 0
            assert not state["complete"] and not state["entries_allowed"]
            # Inyectar una vela desalineada en la respuesta del gap (rig.rows se usa para ranged).
            misaligned_open = _rest_candle(missing_index)["open_time"] + 30_000
            misaligned = {
                "open_time": misaligned_open,
                "close_time": misaligned_open + MINUTE_MS - 1,
                "open": 200.0, "high": 202.0, "low": 199.0,
                "close": 201.0, "volume": 50.0,
            }
            # Reemplazar la vela faltante en rig.rows para que el ranged responder la devuelva.
            for i, row in enumerate(rig.rows):
                if row["open_time"] == _rest_candle(missing_index)["open_time"]:
                    rig.rows[i] = misaligned
                    break

            delay_gap.release.set()
            await _spin(lambda: not engine.snapshot()["recovering"])
            state = _snapshot(engine)
            # El gap persiste porque la vela desalineada no se fusiona.
            assert state["pending_gaps"] > 0
            assert not state["complete"] and not state["entries_allowed"]
            # La respuesta ranged sí contiene la vela desalineada, pero no aparece en closed_candles.
            ranged_rows = [row for req, rows in rig.responses if req.get("start_ts") is not None for row in (rows if isinstance(rows, list) else [rows])]
            assert any(r["open_time"] == misaligned_open for r in ranged_rows)
            assert misaligned_open not in {row["open_time"] for row in engine.closed_candles}

    asyncio.run(scenario())
