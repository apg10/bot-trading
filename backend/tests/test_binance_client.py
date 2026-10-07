"""Clientes públicos Spot con HTTP MockTransport y WebSocket falso, sin red."""

import asyncio
import json
import sys
from collections import deque
from types import SimpleNamespace

import httpx
import pytest

from src.market.binance_client import BinanceConfig, BinanceHTTPClient, BinanceWSClient


class FakeConnectionClosed(Exception):
    pass


class FakeConnectionClosedError(FakeConnectionClosed):
    pass


class FakeSocket:
    def __init__(self, messages=()):
        self.messages = deque(messages)
        self.closed = asyncio.Event()
        self.close_count = 0

    async def recv(self):
        if self.messages:
            message = self.messages.popleft()
            if isinstance(message, Exception):
                raise message
            return message
        await self.closed.wait()
        raise FakeConnectionClosed()

    async def close(self):
        self.close_count += 1
        self.closed.set()


class PendingHandshake:
    def __init__(self):
        self.entered = asyncio.Event()
        self.cancelled = False


@pytest.fixture
def transport(monkeypatch):
    class FakeTransport:
        def __init__(self):
            self.plans = deque()
            self.uris = []

        def connect(self, uri):
            self.uris.append(uri)
            assert self.plans, "Reintento no previsto por la prueba"
            plan = self.plans.popleft()

            class Connection:
                async def __aenter__(self):
                    if isinstance(plan, Exception):
                        raise plan
                    if isinstance(plan, PendingHandshake):
                        plan.entered.set()
                        try:
                            await asyncio.Event().wait()
                        except asyncio.CancelledError:
                            plan.cancelled = True
                            raise
                    return plan

                async def __aexit__(self, *_):
                    await plan.close()

            return Connection()

    fake = FakeTransport()
    module = SimpleNamespace(
        connect=fake.connect,
        exceptions=SimpleNamespace(
            ConnectionClosed=FakeConnectionClosed,
            ConnectionClosedError=FakeConnectionClosedError,
        ),
    )
    monkeypatch.setitem(sys.modules, "websockets", module)
    return fake


async def _until(predicate):
    async def wait():
        while not predicate():
            await asyncio.sleep(0)
    await asyncio.wait_for(wait(), timeout=1)


async def _stop(client, task):
    await asyncio.wait_for(client.stop(), timeout=1)
    await asyncio.wait_for(task, timeout=1)


def _frame(symbol="BTCUSDT", is_closed=False):
    return json.dumps({"k": {
        "s": symbol, "t": 1_700_000_000_000, "T": 1_700_000_059_999,
        "o": "100.0", "h": "102.0", "l": "99.0", "c": "101.0",
        "v": "15.0", "x": is_closed,
    }})


@pytest.mark.parametrize("symbol", ["BTC/USDT", "btcusdt", " btc/usdt "])
def test_http_queries_normalize_symbol_and_remain_public_gets(symbol):
    async def scenario():
        requests = []

        def respond(request):
            requests.append(request)
            assert request.method == "GET"
            assert request.url.params["symbol"] == "BTCUSDT"
            assert "signature" not in request.url.params
            assert "x-mbx-apikey" not in request.headers
            if request.url.path == "/api/v3/klines":
                return httpx.Response(200, json=[[
                    1_700_000_000_000, "100", "102", "99", "101", "15",
                    1_700_000_059_999, "1515", 5,
                ]])
            assert request.url.path == "/api/v3/exchangeInfo"
            return httpx.Response(200, json={"symbols": [{
                "symbol": "BTCUSDT", "baseAsset": "BTC", "quoteAsset": "USDT",
                "filters": [],
            }]})

        config = BinanceConfig(base_url="https://spot.invalid", api_key="unused-key",
                               api_secret="unused-secret")
        client = BinanceHTTPClient(config)
        await client._client.aclose()
        client._client = httpx.AsyncClient(
            base_url=config.base_url, transport=httpx.MockTransport(respond),
        )
        try:
            candles = await client.get_klines(symbol, interval="15m", limit=200,
                                             start_ts=1000, end_ts=2000)
            info = await client.get_symbol_info(symbol)
            assert candles[0]["close_time"] == 1_700_000_059_999
            assert info["base_asset"] == "BTC"
            assert requests[0].url.params["interval"] == "15m"
            assert requests[0].url.params["startTime"] == "1000"
            assert requests[0].url.params["endTime"] == "2000"
            assert all(request.url.host == "spot.invalid" for request in requests)
        finally:
            await client.close()
    asyncio.run(scenario())


@pytest.mark.parametrize("base, expected", [
    ("wss://spot.invalid:9443", "wss://spot.invalid:9443/ws/btcusdt@kline_15m"),
    ("wss://spot.invalid:9443/", "wss://spot.invalid:9443/ws/btcusdt@kline_15m"),
    ("wss://spot.invalid/ws", "wss://spot.invalid/ws/btcusdt@kline_15m"),
    ("ws://spot.invalid/proxy/", "ws://spot.invalid/proxy/ws/btcusdt@kline_15m"),
])
def test_ws_uses_configured_url_interval_and_normalized_symbol(transport, base, expected):
    async def scenario():
        socket = FakeSocket([_frame()])
        transport.plans.append(socket)
        client = BinanceWSClient(BinanceConfig(ws_url=base, kline_interval="15m"))
        received = []
        client.on_message(received.append)
        task = asyncio.create_task(client.start("BTC/USDT"))
        try:
            await _until(lambda: bool(received))
            assert transport.uris == [expected]
            assert received[0] == {
                "symbol": "BTC/USDT", "open_time": 1_700_000_000_000,
                "close_time": 1_700_000_059_999, "open": 100.0, "high": 102.0,
                "low": 99.0, "close": 101.0, "volume": 15.0, "is_closed": False,
            }
        finally:
            await _stop(client, task)
        assert socket.close_count == 1
    asyncio.run(scenario())


@pytest.mark.parametrize("maximum", [0, 1, 3])
def test_retry_budget_includes_initial_attempt_plus_configured_retries(transport, monkeypatch, maximum):
    async def scenario():
        transport.plans.extend(ConnectionError("mock failure") for _ in range(maximum + 1))
        client = BinanceWSClient(BinanceConfig(max_reconnect_attempts=maximum,
                                               reconnect_base_delay=2.0))
        delays, errors, disconnects = [], [], []

        async def delay(seconds):
            delays.append(seconds)
            await asyncio.sleep(0)

        monkeypatch.setattr(client, "_wait_before_reconnect", delay)
        client.on_error(errors.append)
        client.on_disconnect(lambda: disconnects.append(True))
        await asyncio.wait_for(client.start("BTCUSDT"), timeout=1)
        assert len(transport.uris) == maximum + 1
        assert delays == [2.0 * 2 ** i for i in range(maximum)]
        assert len(errors) == maximum + 1
        assert disconnects == []  # nunca llegó a conectar
        assert client._running is False
        assert client._ws is None
        assert client._run_task is None
    asyncio.run(scenario())


def test_retry_delay_is_capped_at_sixty_seconds(transport, monkeypatch):
    async def scenario():
        transport.plans.extend(ConnectionError() for _ in range(4))
        client = BinanceWSClient(BinanceConfig(max_reconnect_attempts=3,
                                               reconnect_base_delay=40.0))
        delays = []

        async def delay(seconds):
            delays.append(seconds)

        monkeypatch.setattr(client, "_wait_before_reconnect", delay)
        await client.start("BTCUSDT")
        assert delays == [40.0, 60.0, 60.0]
    asyncio.run(scenario())


def test_opening_without_data_does_not_reset_retry_budget(transport, monkeypatch):
    async def scenario():
        transport.plans.extend(FakeSocket([FakeConnectionClosed()]) for _ in range(3))
        client = BinanceWSClient(BinanceConfig(max_reconnect_attempts=2))
        delays, lifecycle = [], []

        async def delay(seconds):
            delays.append(seconds)

        monkeypatch.setattr(client, "_wait_before_reconnect", delay)
        client.on_connect(lambda: lifecycle.append("connect"))
        client.on_disconnect(lambda: lifecycle.append("disconnect"))
        await client.start("BTCUSDT")
        assert len(transport.uris) == 3
        assert delays == [1.0, 2.0]
        assert lifecycle == ["connect", "disconnect"] * 3
    asyncio.run(scenario())


def test_valid_data_resets_budget_and_delay(transport, monkeypatch):
    async def scenario():
        final = FakeSocket()
        transport.plans.extend([
            ConnectionError(), FakeSocket([FakeConnectionClosed()]),
            FakeSocket([_frame(), FakeConnectionClosed()]), final,
        ])
        client = BinanceWSClient(BinanceConfig(max_reconnect_attempts=2))
        delays, connects = [], []

        async def delay(seconds):
            delays.append(seconds)
            await asyncio.sleep(0)

        monkeypatch.setattr(client, "_wait_before_reconnect", delay)
        client.on_connect(lambda: connects.append(True))
        task = asyncio.create_task(client.start("BTCUSDT"))
        try:
            await _until(lambda: len(connects) == 3)
            assert len(transport.uris) == 4
            assert delays == [1.0, 2.0, 1.0]
        finally:
            await _stop(client, task)
    asyncio.run(scenario())


def test_stop_interrupts_backoff_without_another_attempt(transport):
    async def scenario():
        transport.plans.append(ConnectionError())
        client = BinanceWSClient(BinanceConfig(reconnect_base_delay=60.0))
        failed = asyncio.Event()
        client.on_error(lambda _: failed.set())
        task = asyncio.create_task(client.start("BTCUSDT"))
        await asyncio.wait_for(failed.wait(), timeout=1)
        await _stop(client, task)
        await asyncio.sleep(0)
        assert len(transport.uris) == 1
        assert client._running is False
        assert asyncio.all_tasks() == {asyncio.current_task()}
    asyncio.run(scenario())


def test_stop_interrupts_pending_handshake(transport):
    async def scenario():
        handshake = PendingHandshake()
        transport.plans.append(handshake)
        client = BinanceWSClient(BinanceConfig())
        disconnects = []
        client.on_disconnect(lambda: disconnects.append(True))
        task = asyncio.create_task(client.start("BTCUSDT"))
        await asyncio.wait_for(handshake.entered.wait(), timeout=1)
        await _stop(client, task)
        assert handshake.cancelled
        assert disconnects == []
        assert client._run_task is None
        assert asyncio.all_tasks() == {asyncio.current_task()}
    asyncio.run(scenario())


def test_stop_is_idempotent_and_disconnect_fires_once(transport):
    async def scenario():
        socket = FakeSocket()
        transport.plans.append(socket)
        client = BinanceWSClient(BinanceConfig())
        connected, disconnects = asyncio.Event(), []
        client.on_connect(connected.set)
        client.on_disconnect(lambda: disconnects.append(True))
        task = asyncio.create_task(client.start("BTCUSDT"))
        await asyncio.wait_for(connected.wait(), timeout=1)
        await asyncio.gather(client.stop(), client.stop())
        await asyncio.wait_for(task, timeout=1)
        await client.stop()
        assert disconnects == [True]
        assert socket.close_count == 1
        assert len(transport.uris) == 1
        assert client._ws is None
        assert asyncio.all_tasks() == {asyncio.current_task()}
    asyncio.run(scenario())


def test_external_cancellation_is_preserved_and_cleans_session(transport):
    async def scenario():
        socket = FakeSocket()
        transport.plans.append(socket)
        client = BinanceWSClient(BinanceConfig())
        connected, disconnects = asyncio.Event(), []
        client.on_connect(connected.set)
        client.on_disconnect(lambda: disconnects.append(True))
        task = asyncio.create_task(client.start("BTCUSDT"))
        await asyncio.wait_for(connected.wait(), timeout=1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert disconnects == [True]
        assert socket.close_count == 1
        assert client._run_task is None
        assert client._running is False
        assert asyncio.all_tasks() == {asyncio.current_task()}
    asyncio.run(scenario())


def test_concurrent_start_is_rejected_without_disrupting_existing_stream(transport):
    async def scenario():
        transport.plans.append(FakeSocket())
        client = BinanceWSClient(BinanceConfig())
        connected = asyncio.Event()
        client.on_connect(connected.set)
        task = asyncio.create_task(client.start("BTCUSDT"))
        try:
            await asyncio.wait_for(connected.wait(), timeout=1)
            with pytest.raises(RuntimeError):
                await client.start("ETHUSDT")
            assert len(transport.uris) == 1
            assert client._run_task is task
        finally:
            await _stop(client, task)
    asyncio.run(scenario())


def test_timeout_and_unrelated_messages_do_not_break_stream(transport):
    async def scenario():
        transport.plans.append(FakeSocket([
            asyncio.TimeoutError(), json.dumps({"result": None}),
            _frame(symbol="ETHUSDT"), _frame(is_closed=True),
        ]))
        client = BinanceWSClient(BinanceConfig())
        received, errors = [], []
        client.on_message(received.append)
        client.on_error(errors.append)
        task = asyncio.create_task(client.start("BTC/USDT"))
        try:
            await _until(lambda: bool(received))
            assert len(received) == 1
            assert received[0]["is_closed"] is True
            assert errors == []
            assert len(transport.uris) == 1
        finally:
            await _stop(client, task)
    asyncio.run(scenario())


def test_transport_errors_do_not_log_credentials(transport, caplog):
    async def scenario():
        secret = "fake-private-credential"
        transport.plans.append(ConnectionError(secret))
        client = BinanceWSClient(BinanceConfig(api_key=secret, api_secret=secret,
                                               max_reconnect_attempts=0))
        errors = []
        client.on_error(errors.append)
        await client.start("BTCUSDT")
        assert len(errors) == 1
        assert secret not in caplog.text
    asyncio.run(scenario())


def test_abnormal_close_notifies_error_disconnect_and_reconnect(transport, monkeypatch):
    async def scenario():
        transport.plans.extend([
            FakeSocket([FakeConnectionClosedError("mock abnormal close")]), FakeSocket(),
        ])
        client = BinanceWSClient(BinanceConfig())
        lifecycle, errors = [], []

        async def delay(_):
            await asyncio.sleep(0)

        monkeypatch.setattr(client, "_wait_before_reconnect", delay)
        client.on_connect(lambda: lifecycle.append("connect"))
        client.on_disconnect(lambda: lifecycle.append("disconnect"))
        client.on_error(errors.append)
        task = asyncio.create_task(client.start("BTCUSDT"))
        try:
            await _until(lambda: len(lifecycle) == 3)
            assert lifecycle == ["connect", "disconnect", "connect"]
            assert len(errors) == 1
            assert isinstance(errors[0], FakeConnectionClosedError)
        finally:
            await _stop(client, task)
        assert lifecycle[-1] == "disconnect"
    asyncio.run(scenario())


def test_malformed_frame_reconnects_without_forwarding_invalid_data(transport, monkeypatch):
    async def scenario():
        first, second = FakeSocket(["not json"]), FakeSocket([_frame()])
        transport.plans.extend([first, second])
        client = BinanceWSClient(BinanceConfig(max_reconnect_attempts=1))
        received, errors = [], []

        async def delay(_):
            await asyncio.sleep(0)

        monkeypatch.setattr(client, "_wait_before_reconnect", delay)
        client.on_message(received.append)
        client.on_error(errors.append)
        task = asyncio.create_task(client.start("BTCUSDT"))
        try:
            await _until(lambda: bool(received))
            assert len(received) == 1
            assert len(errors) == 1
            assert isinstance(errors[0], json.JSONDecodeError)
            assert len(transport.uris) == 2
        finally:
            await _stop(client, task)
        assert first.close_count == second.close_count == 1
    asyncio.run(scenario())


def test_callback_failures_do_not_break_transport_or_log_credentials(transport, caplog):
    async def scenario():
        transport.plans.append(FakeSocket([_frame()]))
        client = BinanceWSClient(BinanceConfig())
        observed = []

        def failing(*_):
            observed.append(True)
            raise RuntimeError("fake-callback-secret")

        client.on_connect(failing)
        client.on_message(failing)
        client.on_disconnect(failing)
        task = asyncio.create_task(client.start("BTCUSDT"))
        try:
            await _until(lambda: len(observed) == 2)
            assert len(transport.uris) == 1
            assert not task.done()
        finally:
            await _stop(client, task)
        assert len(observed) == 3
        assert "fake-callback-secret" not in caplog.text
    asyncio.run(scenario())


def test_explicit_restart_after_stop_uses_fresh_symbol_and_stop_event(transport):
    async def scenario():
        transport.plans.extend([FakeSocket([_frame()]), FakeSocket([_frame(symbol="ETHUSDT")])])
        client = BinanceWSClient(BinanceConfig())
        received = []
        client.on_message(received.append)
        first = asyncio.create_task(client.start("BTCUSDT"))
        await _until(lambda: len(received) == 1)
        await _stop(client, first)
        second = asyncio.create_task(client.start("ETH/USDT"))
        try:
            await _until(lambda: len(received) == 2)
            assert received[-1]["symbol"] == "ETH/USDT"
            assert transport.uris[-1].endswith("/ws/ethusdt@kline_1m")
        finally:
            await _stop(client, second)
        assert asyncio.all_tasks() == {asyncio.current_task()}
    asyncio.run(scenario())


@pytest.mark.parametrize("kwargs", [
    {"max_reconnect_attempts": -1}, {"max_reconnect_attempts": 1.5},
    {"reconnect_base_delay": -1.0}, {"reconnect_base_delay": float("inf")},
    {"reconnect_base_delay": float("nan")},
])
def test_invalid_retry_configuration_fails_before_connect(transport, kwargs):
    async def scenario():
        client = BinanceWSClient(BinanceConfig(**kwargs))
        with pytest.raises(ValueError):
            await client.start("BTCUSDT")
        assert transport.uris == []
    asyncio.run(scenario())


@pytest.mark.parametrize("symbol", ["", "BTC/USDT:USDT", "BTC//USDT", "../BTC", "éTHUSDT"])
def test_invalid_or_derivative_symbol_is_rejected_before_connect(transport, symbol):
    async def scenario():
        client = BinanceWSClient(BinanceConfig())
        with pytest.raises(ValueError):
            await client.start(symbol)
        assert transport.uris == []
    asyncio.run(scenario())


@pytest.mark.parametrize("url", [
    "https://spot.invalid", "wss:///ws", "wss://user:secret@spot.invalid",
    "wss://spot.invalid?token=secret",
])
def test_invalid_or_authenticated_ws_base_is_rejected(transport, url):
    async def scenario():
        client = BinanceWSClient(BinanceConfig(ws_url=url))
        with pytest.raises(ValueError):
            await client.start("BTCUSDT")
        assert transport.uris == []
    asyncio.run(scenario())


def test_engine_receives_real_client_disconnect_and_reconnect_callbacks(transport, monkeypatch):
    import src.market.engine as engine_module

    class FakeHTTP:
        def __init__(self, _config):
            self.closed = False

        async def close(self):
            self.closed = True

    monkeypatch.setattr(engine_module, "BinanceHTTPClient", FakeHTTP)

    async def scenario():
        transport.plans.extend([
            FakeSocket([_frame(is_closed=True), FakeConnectionClosed()]), FakeSocket(),
        ])
        engine = engine_module.MarketEngine(BinanceConfig(reconnect_base_delay=0.0))
        states, closes = [], []

        async def state(snapshot):
            states.append((snapshot["connected"], snapshot["reconnecting"]))

        async def closed(candle):
            closes.append(candle)

        engine.on_state_change(state)
        engine.on_bar_closed(closed)
        task = asyncio.create_task(engine.start("BTC/USDT"))
        try:
            await _until(lambda: len(transport.uris) == 2 and closes
                         and (False, True) in states and states[-1] == (True, False))
            assert engine.state.closed_candles_count == 1
            assert engine.state.connected is True
        finally:
            await asyncio.wait_for(engine.stop(), timeout=1)
            await asyncio.wait_for(task, timeout=1)
        assert states[-1] == (False, False)
        assert asyncio.all_tasks() == {asyncio.current_task()}
    asyncio.run(scenario())
