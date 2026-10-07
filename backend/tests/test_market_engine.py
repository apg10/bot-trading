"""Motor de mercado con clientes falsos; sin red ni plugin pytest-asyncio."""

import asyncio

import pytest

import src.market.engine as engine_module
from src.market.binance_client import BinanceConfig
from src.market.engine import MarketEngine


@pytest.fixture
def fake_clients(monkeypatch):
    class FakeHTTP:
        instances = []

        def __init__(self, config):
            self.config = config
            self.close_count = 0
            self.instances.append(self)

        async def close(self):
            self.close_count += 1

    class FakeWS:
        instances = []
        start_error = None

        def __init__(self, config):
            self.config = config
            self.handlers = {}
            self.started = asyncio.Event()
            self.finished = asyncio.Event()
            self.stop_count = 0
            self.instances.append(self)

        def on_message(self, handler):
            self.handlers["message"] = handler

        def on_connect(self, handler):
            self.handlers["connect"] = handler

        def on_disconnect(self, handler):
            self.handlers["disconnect"] = handler

        def on_error(self, handler):
            self.handlers["error"] = handler

        async def start(self, symbol):
            self.symbol = symbol
            self.started.set()
            if self.start_error is not None:
                raise self.start_error
            await self.finished.wait()

        async def stop(self):
            self.stop_count += 1
            callback = self.handlers.get("disconnect")
            if callback:
                callback()
            self.finished.set()

        def message(self, message):
            self.handlers["message"](message)

    monkeypatch.setattr(engine_module, "BinanceHTTPClient", FakeHTTP)
    monkeypatch.setattr(engine_module, "BinanceWSClient", FakeWS)
    return FakeHTTP, FakeWS


async def _until(predicate):
    async def wait():
        while not predicate():
            await asyncio.sleep(0)
    await asyncio.wait_for(wait(), timeout=1)


async def _start(engine, fake_ws, symbol="BTC/USDT"):
    previous_count = len(fake_ws.instances)
    task = asyncio.create_task(engine.start(symbol))
    await _until(lambda: len(fake_ws.instances) > previous_count)
    client = fake_ws.instances[-1]
    await asyncio.wait_for(client.started.wait(), timeout=1)
    return task, client


async def _stop(engine, task):
    await asyncio.wait_for(engine.stop(), timeout=1)
    await asyncio.wait_for(task, timeout=1)


def _kline(open_time=1_700_000_000_000, volume=10.0, is_closed=False,
           interval_ms=60_000, symbol="BTC/USDT"):
    return {
        "symbol": symbol,
        "open_time": open_time,
        "close_time": open_time + interval_ms - 1,
        "open": 100.0,
        "high": 102.0,
        "low": 99.0,
        "close": 101.0,
        "volume": volume,
        "is_closed": is_closed,
    }


@pytest.mark.parametrize("event", ["bar_closed", "bar_updated", "gap", "state_change"])
def test_handler_registration_and_removal_are_idempotent(event):
    engine = MarketEngine(BinanceConfig())

    async def handler(_):
        pass

    register = getattr(engine, f"on_{event}")
    remove = getattr(engine, f"off_{event}")
    handlers = getattr(engine, f"_on_{event}")
    unregister = register(handler)
    register(handler)
    assert handlers == [handler]
    unregister()
    unregister()
    remove(handler)
    assert handlers == []


def test_normalized_messages_propagate_in_order_and_count_only_closes(fake_clients):
    async def scenario():
        engine = MarketEngine(BinanceConfig())
        received, states, gaps = [], [], []

        async def updated(candle):
            received.append(("updated", candle["volume"]))

        async def closed(candle):
            received.append(("closed", candle["volume"]))

        async def gap(event):
            received.append(("gap", event.gap_ms))
            gaps.append(event)

        async def state(snapshot):
            states.append(snapshot)

        engine.on_bar_updated(updated)
        engine.on_bar_closed(closed)
        engine.on_gap(gap)
        engine.on_state_change(state)
        task, ws = await _start(engine, fake_clients[1])
        try:
            ws.handlers["connect"]()
            ws.message(_kline(volume=10.0))
            ws.message(_kline(volume=15.0))
            final = _kline(volume=20.0, is_closed=True)
            ws.message(final)
            ws.message(final)
            ws.message(_kline(open_time=final["open_time"] + 180_000,
                              volume=25.0, is_closed=True))
            await _until(lambda: len(received) == 5)
            assert received == [
                ("updated", 10.0), ("updated", 15.0), ("closed", 20.0),
                ("gap", 120_000), ("closed", 25.0),
            ]
            assert engine.state.closed_candles_count == 2
            assert engine.state.last_bar_open_time == final["open_time"] + 180_000
            assert engine.state.current_bar_closed == engine.closed_candles[-1]
            assert len(engine.closed_candles) == 2
            assert gaps[0].gap_end_time - gaps[0].gap_start_time == gaps[0].gap_ms
            await _until(lambda: states and states[-1]["closed_candles_count"] == 2)
        finally:
            await _stop(engine, task)
    asyncio.run(scenario())


def test_missing_close_propagates_gap_without_confirming_partial(fake_clients):
    async def scenario():
        engine = MarketEngine(BinanceConfig())
        received = []

        async def gap(event):
            received.append(("gap", event.gap_ms))

        async def closed(candle):
            received.append(("closed", candle["open_time"]))

        engine.on_gap(gap)
        engine.on_bar_closed(closed)
        task, ws = await _start(engine, fake_clients[1])
        try:
            first = _kline()
            ws.message(first)
            ws.message(_kline(open_time=first["open_time"] + 60_000, is_closed=True))
            await _until(lambda: len(received) == 2)
            assert received == [("gap", 60_000), ("closed", first["open_time"] + 60_000)]
            assert engine.state.closed_candles_count == 1
        finally:
            await _stop(engine, task)
    asyncio.run(scenario())


def test_connection_error_reconnect_and_stop_use_transport_callbacks(fake_clients):
    async def scenario():
        engine = MarketEngine(BinanceConfig())
        states = []

        async def state(snapshot):
            states.append((snapshot["connected"], snapshot["reconnecting"]))

        engine.on_state_change(state)
        task, ws = await _start(engine, fake_clients[1])
        try:
            assert set(ws.handlers) == {"message", "connect", "disconnect", "error"}
            assert engine.state.connected is False
            ws.handlers["connect"]()
            assert engine.state.connected is True
            assert engine.state.reconnecting is False
            await _until(lambda: states and states[-1] == (True, False))
            ws.handlers["disconnect"]()
            assert engine.state.connected is False
            assert engine.state.reconnecting is True
            await _until(lambda: states[-1] == (False, True))
            ws.handlers["connect"]()
            await _until(lambda: states[-1] == (True, False))
            ws.handlers["error"](RuntimeError("mock transport failure"))
            assert engine.state.connected is False
            assert engine.state.reconnecting is True
            await _until(lambda: states[-1] == (False, True))
        finally:
            await _stop(engine, task)
        assert states[-1] == (False, False)
        ws.handlers["connect"]()
        ws.handlers["error"](RuntimeError("late error"))
        assert engine.state.connected is False
        assert engine.state.reconnecting is False
        assert fake_clients[0].instances[0].close_count == 1
    asyncio.run(scenario())


@pytest.mark.parametrize("interval, milliseconds", [
    ("1s", 1_000), ("1m", 60_000), ("15m", 900_000),
    ("1h", 3_600_000), ("1d", 86_400_000), ("1w", 604_800_000),
])
def test_builder_uses_configured_fixed_interval(fake_clients, interval, milliseconds):
    async def scenario():
        engine = MarketEngine(BinanceConfig(kline_interval=interval))
        gaps, closes = [], []

        async def gap(event):
            gaps.append(event)

        async def closed(candle):
            closes.append(candle)

        engine.on_gap(gap)
        engine.on_bar_closed(closed)
        task, ws = await _start(engine, fake_clients[1])
        try:
            first = _kline(interval_ms=milliseconds, is_closed=True)
            ws.message(first)
            ws.message(_kline(open_time=first["open_time"] + milliseconds,
                              interval_ms=milliseconds, is_closed=True))
            await _until(lambda: len(closes) == 2)
            assert gaps == []
            assert closes[0]["close_time"] == first["close_time"]
        finally:
            await _stop(engine, task)
    asyncio.run(scenario())


@pytest.mark.parametrize("interval", ["", "7m", "bad", "1M"])
def test_unsupported_or_calendar_interval_fails_before_creating_clients(fake_clients, interval):
    async def scenario():
        engine = MarketEngine(BinanceConfig(kline_interval=interval))
        with pytest.raises(ValueError):
            await asyncio.wait_for(engine.start("BTC/USDT"), timeout=1)
        assert fake_clients[0].instances == []
        assert fake_clients[1].instances == []
    asyncio.run(scenario())


def test_subscriber_failure_does_not_stop_other_subscribers_or_messages(fake_clients):
    async def scenario():
        engine = MarketEngine(BinanceConfig())
        received = []

        async def failing(_):
            raise RuntimeError("mock subscriber failure")

        async def healthy(candle):
            received.append(candle["open_time"])

        engine.on_bar_closed(failing)
        engine.on_bar_closed(healthy)
        engine.on_state_change(failing)
        task, ws = await _start(engine, fake_clients[1])
        try:
            first = _kline(is_closed=True)
            ws.message(first)
            ws.message(_kline(open_time=first["open_time"] + 60_000, is_closed=True))
            await _until(lambda: len(received) == 2)
            assert engine.state.closed_candles_count == 2
            assert not task.done()
        finally:
            await _stop(engine, task)
    asyncio.run(scenario())


def test_async_subscribers_do_not_reorder_messages(fake_clients):
    async def scenario():
        engine = MarketEngine(BinanceConfig())
        entered, release = asyncio.Event(), asyncio.Event()
        received = []

        async def updated(candle):
            entered.set()
            await release.wait()
            received.append(("updated", candle["volume"]))

        async def closed(candle):
            received.append(("closed", candle["volume"]))

        engine.on_bar_updated(updated)
        engine.on_bar_closed(closed)
        task, ws = await _start(engine, fake_clients[1])
        try:
            ws.message(_kline())
            await asyncio.wait_for(entered.wait(), timeout=1)
            ws.message(_kline(volume=20.0, is_closed=True))
            assert len(engine.closed_candles) == 1  # recepción no espera al suscriptor
            release.set()
            await _until(lambda: len(received) == 2)
            assert received == [("updated", 10.0), ("closed", 20.0)]
        finally:
            await _stop(engine, task)
    asyncio.run(scenario())


def test_handler_can_remove_itself_without_skipping_other_subscribers(fake_clients):
    async def scenario():
        engine = MarketEngine(BinanceConfig())
        first, second = [], []

        async def once(candle):
            first.append(candle["open_time"])
            engine.off_bar_closed(once)

        async def always(candle):
            second.append(candle["open_time"])

        engine.on_bar_closed(once)
        engine.on_bar_closed(always)
        task, ws = await _start(engine, fake_clients[1])
        try:
            initial = _kline(is_closed=True)
            ws.message(initial)
            ws.message(_kline(open_time=initial["open_time"] + 60_000, is_closed=True))
            await _until(lambda: len(second) == 2)
            assert first == [initial["open_time"]]
            assert second == [initial["open_time"], initial["open_time"] + 60_000]
        finally:
            await _stop(engine, task)
    asyncio.run(scenario())


def test_queued_transport_states_do_not_regress_candle_count(fake_clients):
    async def scenario():
        engine = MarketEngine(BinanceConfig())
        states = []

        async def state(snapshot):
            states.append(snapshot)

        engine.on_state_change(state)
        task, ws = await _start(engine, fake_clients[1])
        try:
            ws.handlers["connect"]()
            ws.message(_kline(is_closed=True))
            ws.handlers["disconnect"]()
            ws.handlers["connect"]()
            await _until(lambda: len(states) >= 4 and states[-1]["connected"])
            counts = [snapshot["closed_candles_count"] for snapshot in states]
            assert counts == sorted(counts)
            assert counts[-1] == 1
        finally:
            await _stop(engine, task)
    asyncio.run(scenario())


def test_stop_cancels_blocked_dispatcher_without_leaking_tasks(fake_clients):
    async def scenario():
        engine = MarketEngine(BinanceConfig())
        entered = asyncio.Event()

        async def blocked(_):
            entered.set()
            await asyncio.Event().wait()

        engine.on_bar_updated(blocked)
        task, ws = await _start(engine, fake_clients[1])
        ws.message(_kline())
        await asyncio.wait_for(entered.wait(), timeout=1)
        await _stop(engine, task)
        await engine.stop()  # idempotente
        assert not engine.state.connected
        assert not engine.state.reconnecting
        assert fake_clients[0].instances[0].close_count == 1
        assert ws.stop_count == 1
        assert asyncio.all_tasks() == {asyncio.current_task()}
    asyncio.run(scenario())


def test_state_subscriber_can_request_stop_without_deadlock(fake_clients):
    async def scenario():
        engine = MarketEngine(BinanceConfig())
        halted = asyncio.Event()

        async def stop_on_connect(snapshot):
            if snapshot["connected"]:
                await engine.stop()
                halted.set()

        engine.on_state_change(stop_on_connect)
        task, ws = await _start(engine, fake_clients[1])
        ws.handlers["connect"]()
        await asyncio.wait_for(halted.wait(), timeout=1)
        await asyncio.wait_for(task, timeout=1)
        assert asyncio.all_tasks() == {asyncio.current_task()}
        assert not engine.state.connected
        assert fake_clients[0].instances[0].close_count == 1
    asyncio.run(scenario())


def test_restart_ignores_callbacks_from_previous_client(fake_clients):
    async def scenario():
        engine = MarketEngine(BinanceConfig())
        old_task, old_ws = await _start(engine, fake_clients[1])
        new_task, new_ws = await _start(engine, fake_clients[1], symbol="ETH/USDT")
        try:
            await asyncio.wait_for(old_task, timeout=1)
            old_ws.message(_kline(is_closed=True))
            old_ws.handlers["connect"]()
            assert engine.state.symbol == "ETH/USDT"
            assert engine.state.connected is False
            assert engine.closed_candles == []
            new_ws.handlers["connect"]()
            new_ws.message(_kline(symbol="ETH/USDT", is_closed=True))
            await _until(lambda: engine.state.closed_candles_count == 1)
            assert engine.state.connected is True
        finally:
            await _stop(engine, new_task)
    asyncio.run(scenario())


def test_start_failure_releases_clients_and_tasks(fake_clients):
    async def scenario():
        fake_clients[1].start_error = RuntimeError("mock start failure")
        engine = MarketEngine(BinanceConfig())
        with pytest.raises(RuntimeError, match="mock start failure"):
            await asyncio.wait_for(engine.start("BTC/USDT"), timeout=1)
        assert not engine.state.connected
        assert not engine.state.reconnecting
        assert fake_clients[0].instances[0].close_count == 1
        assert asyncio.all_tasks() == {asyncio.current_task()}
    asyncio.run(scenario())


def test_cancelling_start_cleans_up_and_preserves_cancellation(fake_clients):
    async def scenario():
        engine = MarketEngine(BinanceConfig())
        task, _ = await _start(engine, fake_clients[1])
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert fake_clients[0].instances[0].close_count == 1
        assert not engine.state.connected
        assert asyncio.all_tasks() == {asyncio.current_task()}
    asyncio.run(scenario())


def test_normal_stream_end_does_not_claim_connected(fake_clients):
    async def scenario():
        engine = MarketEngine(BinanceConfig())
        task, ws = await _start(engine, fake_clients[1])
        ws.finished.set()
        await asyncio.wait_for(task, timeout=1)
        assert not engine.state.connected
        assert not engine.state.reconnecting
        assert fake_clients[0].instances[0].close_count == 1
        assert asyncio.all_tasks() == {asyncio.current_task()}
    asyncio.run(scenario())
