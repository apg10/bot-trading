"""Pruebas del CandleBuilder: duplicados, huecos, ordenamiento, reconexión."""

import pytest
from src.market.candle_builder import CandleBuilder, BarEventType


def _make_kline(
    symbol: str = "BTC/USDT",
    open_time: int = 1700000000000,
    open_p: float = 67500.0,
    high: float = 68000.0,
    low: float = 67000.0,
    close: float = 67800.0,
    volume: float = 100.0,
    is_closed: bool = False,
    close_time: int | None = None,
) -> dict:
    return {
        "symbol": symbol,
        "open_time": open_time,
        "close_time": close_time if close_time is not None else open_time + 59_999,
        "open": open_p,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
        "is_closed": is_closed,
    }


class TestDuplicateDetection:
    def test_close_of_live_bar_is_not_duplicate(self):
        """El cierre de la vela abierta es válido, no un duplicado."""
        builder = CandleBuilder("BTC/USDT")

        # Abrir barra en vivo
        builder.process_kline(_make_kline(open_time=1000, is_closed=False))
        # El cierre confirma la misma vela que estaba en formación.
        events = builder.process_kline(_make_kline(open_time=1000, is_closed=True))

        assert [e.event_type for e in events] == [BarEventType.BAR_CLOSED]
        assert len(builder.closed_candles) == 1
        assert builder.current_bar is None

    def test_identical_live_snapshot_is_duplicate(self):
        builder = CandleBuilder("BTC/USDT")
        message = _make_kline()
        builder.process_kline(message)
        events = builder.process_kline(dict(message))

        assert [e.event_type for e in events] == [BarEventType.DUPLICATE]
        assert builder.current_bar.volume == message["volume"]
        assert builder.closed_candles == []

    def test_repeated_closed_snapshot_is_duplicate(self):
        builder = CandleBuilder("BTC/USDT")
        message = _make_kline(is_closed=True)
        builder.process_kline(message)
        events = builder.process_kline(dict(message))

        assert [e.event_type for e in events] == [BarEventType.DUPLICATE]
        assert len(builder.closed_candles) == 1
        assert builder.current_bar is None

    def test_live_update_cannot_reopen_closed_bar(self):
        builder = CandleBuilder("BTC/USDT")
        builder.process_kline(_make_kline(is_closed=True))
        events = builder.process_kline(_make_kline(volume=150.0))

        assert [e.event_type for e in events] == [BarEventType.DUPLICATE]
        assert builder.current_bar is None
        assert builder.closed_candles[0]["volume"] == 100.0

    def test_no_duplicate_on_new_bar(self):
        builder = CandleBuilder("BTC/USDT")
        # Primera barra abierta
        builder.process_kline(_make_kline(open_time=1000, is_closed=False))
        # Segunda barra (nueva open_time)
        events = builder.process_kline(_make_kline(open_time=61_000, is_closed=False))
        new_bar_events = [e for e in events if e.event_type == BarEventType.NEW_BAR_STARTED]
        assert len(new_bar_events) == 1


class TestGapDetection:
    def test_detects_gap_between_bars(self):
        builder = CandleBuilder("BTC/USDT")

        # Primera barra cerrada (ts base)
        ts_base = 1700000000000
        builder.process_kline(_make_kline(open_time=ts_base, is_closed=True))
        # expected_next_open_time = ts_base + 60_000 = 1700000060000

        # Segunda barra con hueco de 2 minutos (expected: 1700000060000, got: 1700000180000)
        events = builder.process_kline(_make_kline(open_time=ts_base + 180_000, is_closed=True))

        gap_events = [e for e in events if e.event_type == BarEventType.GAP_DETECTED]
        assert len(gap_events) == 1
        assert gap_events[0].gap_ms == 120_000  # 2 minutos de hueco
        assert gap_events[0].gap_start_time == ts_base + 60_000
        assert gap_events[0].gap_end_time == ts_base + 180_000
        assert gap_events[0].previous_close_time == ts_base + 59_999

    @pytest.mark.parametrize("next_is_closed", [False, True])
    def test_missing_final_close_does_not_confirm_partial_bar(self, next_is_closed):
        builder = CandleBuilder("BTC/USDT")
        ts_base = 1700000000000
        builder.process_kline(_make_kline(open_time=ts_base))
        events = builder.process_kline(_make_kline(
            open_time=ts_base + 60_000, is_closed=next_is_closed,
        ))

        gaps = [e for e in events if e.event_type == BarEventType.GAP_DETECTED]
        assert len(gaps) == 1
        assert gaps[0].gap_ms == 60_000
        assert gaps[0].gap_start_time == ts_base
        assert gaps[0].gap_end_time == ts_base + 60_000
        assert gaps[0].previous_close_time is None
        assert all(c["open_time"] != ts_base for c in builder.closed_candles)
        closed = [e for e in events if e.event_type == BarEventType.BAR_CLOSED]
        assert len(closed) == int(next_is_closed)
        if next_is_closed:
            assert closed[0].candle["open_time"] == ts_base + 60_000

    def test_missing_close_and_skipped_bars_form_one_recovery_interval(self):
        builder = CandleBuilder("BTC/USDT")
        ts_base = 1700000000000
        builder.process_kline(_make_kline(open_time=ts_base, is_closed=True))
        builder.process_kline(_make_kline(open_time=ts_base + 60_000))
        events = builder.process_kline(_make_kline(open_time=ts_base + 240_000))

        gap = [e for e in events if e.event_type == BarEventType.GAP_DETECTED]
        assert len(gap) == 1
        assert gap[0].gap_start_time == ts_base + 60_000
        assert gap[0].gap_end_time == ts_base + 240_000
        assert gap[0].gap_ms == 180_000
        assert gap[0].previous_close_time == ts_base + 59_999
        assert [c["open_time"] for c in builder.closed_candles] == [ts_base]

    def test_confirmed_consecutive_bars_have_no_gap(self):
        builder = CandleBuilder("BTC/USDT")
        ts_base = 1700000000000
        builder.process_kline(_make_kline(open_time=ts_base))
        builder.process_kline(_make_kline(open_time=ts_base, is_closed=True))
        events = builder.process_kline(_make_kline(open_time=ts_base + 60_000))

        assert all(e.event_type != BarEventType.GAP_DETECTED for e in events)
        assert len(builder.closed_candles) == 1

    def test_repeating_first_snapshot_after_gap_does_not_repeat_gap(self):
        builder = CandleBuilder("BTC/USDT")
        ts_base = 1700000000000
        builder.process_kline(_make_kline(open_time=ts_base))
        message = _make_kline(open_time=ts_base + 180_000)
        builder.process_kline(message)
        events = builder.process_kline(message)

        assert [e.event_type for e in events] == [BarEventType.DUPLICATE]


class TestOutOfOrder:
    def test_out_of_order_treated_as_duplicate(self):
        builder = CandleBuilder("BTC/USDT")

        # Abrir barra en t=1000
        builder.process_kline(_make_kline(open_time=1000, is_closed=False))
        previous = vars(builder.current_bar).copy()
        # Evento con open_time anterior a la vela abierta.
        events = builder.process_kline(_make_kline(open_time=500, is_closed=True))

        dup_events = [e for e in events if e.event_type == BarEventType.DUPLICATE]
        assert len(dup_events) == 1
        assert vars(builder.current_bar) == previous
        assert builder.expected_next_open_time == 61_000
        assert builder.closed_candles == []

    def test_late_close_does_not_replace_newer_live_bar(self):
        builder = CandleBuilder("BTC/USDT")
        ts_base = 1700000000000
        builder.process_kline(_make_kline(open_time=ts_base))
        builder.process_kline(_make_kline(open_time=ts_base + 60_000))
        events = builder.process_kline(_make_kline(open_time=ts_base, is_closed=True))

        assert [e.event_type for e in events] == [BarEventType.DUPLICATE]
        assert builder.current_bar.open_time == ts_base + 60_000
        assert builder.closed_candles == []


class TestBarClosed:
    def test_bar_closed_event(self):
        builder = CandleBuilder("BTC/USDT")
        events = builder.process_kline(_make_kline(open_time=1000, is_closed=True))

        closed_events = [e for e in events if e.event_type == BarEventType.BAR_CLOSED]
        assert len(closed_events) == 1
        assert closed_events[0].candle["is_closed"] is True
        assert closed_events[0].candle["open_time"] == 1000

    def test_final_ohlcv_is_authoritative(self):
        builder = CandleBuilder("BTC/USDT")
        builder.process_kline(_make_kline(high=69000.0, low=66000.0, volume=10.0))
        final = _make_kline(
            open_p=67400.0, high=68500.0, low=66800.0,
            close=68100.0, volume=25.0, is_closed=True,
        )
        events = builder.process_kline(final)
        expected = {key: value for key, value in final.items() if key != "symbol"}

        assert [e.event_type for e in events] == [BarEventType.BAR_CLOSED]
        assert events[0].candle == expected
        assert builder.closed_candles == [expected]
        assert builder.current_bar is None
        repeated = builder.process_kline(final)
        assert [e.event_type for e in repeated] == [BarEventType.DUPLICATE]
        assert builder.closed_candles == [expected]

    @pytest.mark.parametrize("initially_live", [False, True])
    def test_preserves_exchange_close_time(self, initially_live):
        builder = CandleBuilder("BTC/USDT")
        ts_base = 1700000000000
        close_time = ts_base + 59_999
        if initially_live:
            builder.process_kline(_make_kline(close_time=close_time))
        events = builder.process_kline(_make_kline(
            close_time=close_time, is_closed=True,
        ))

        assert events[0].candle["close_time"] == close_time
        assert builder.closed_candles[0]["close_time"] == close_time


class TestBarUpdate:
    def test_bar_updated_event(self):
        builder = CandleBuilder("BTC/USDT")

        # Abrir barra (no cerrada)
        events1 = builder.process_kline(_make_kline(open_time=1000, is_closed=False))
        started = [e for e in events1 if e.event_type == BarEventType.NEW_BAR_STARTED]
        assert len(started) == 1

        # Actualizar la barra con nuevo precio
        events2 = builder.process_kline(_make_kline(
            open_time=1000,
            high=68500.0,
            low=66800.0,
            close=67900.0,
            is_closed=False,
        ))

        updated = [e for e in events2 if e.event_type == BarEventType.BAR_UPDATED]
        assert len(updated) == 1
        # Verificar que high/low se actualizaron correctamente
        candle = updated[0].candle
        assert candle["high"] == 68500.0
        assert candle["low"] == 66800.0

    def test_volume_is_cumulative_not_additive(self):
        builder = CandleBuilder("BTC/USDT")
        for volume in (10.0, 15.0, 20.0):
            events = builder.process_kline(_make_kline(volume=volume))
            updated = [e for e in events if e.event_type == BarEventType.BAR_UPDATED]
            assert updated[0].candle["volume"] == volume
            assert builder.current_bar.volume == volume
        closed = builder.process_kline(_make_kline(volume=25.0, is_closed=True))
        assert closed[0].candle["volume"] == 25.0

    def test_first_snapshot_preserves_received_ohlcv_and_close_time(self):
        builder = CandleBuilder("BTC/USDT")
        message = _make_kline()
        events = builder.process_kline(message)
        expected = {key: value for key, value in message.items() if key != "symbol"}

        assert [e.event_type for e in events] == [
            BarEventType.NEW_BAR_STARTED, BarEventType.BAR_UPDATED,
        ]
        assert all(e.candle == expected for e in events)


class TestCurrentBarTracking:
    def test_current_bar_property(self):
        builder = CandleBuilder("BTC/USDT")
        assert builder.current_bar is None

        # Abrir barra
        builder.process_kline(_make_kline(open_time=1000, is_closed=False))
        bar = builder.current_bar
        assert bar is not None
        assert bar.open == 67500.0

    def test_current_bar_none_after_close(self):
        builder = CandleBuilder("BTC/USDT")
        builder.process_kline(_make_kline(open_time=1000, is_closed=True))
        assert builder.current_bar is None


class TestClosedCandlesBuffer:
    def test_closed_candles_stored(self):
        builder = CandleBuilder("BTC/USDT")
        ts_base = 1700000000000
        builder.process_kline(_make_kline(open_time=ts_base, is_closed=True))
        builder.process_kline(_make_kline(open_time=ts_base + 60_000, is_closed=True))

        closed = builder.closed_candles
        assert len(closed) == 2

    def test_expected_next_open_time(self):
        ts_base = 1700000000000
        builder = CandleBuilder("BTC/USDT")
        builder.process_kline(_make_kline(open_time=ts_base, is_closed=True))
        assert builder.expected_next_open_time == ts_base + 60_000

    def test_buffer_is_bounded_and_evicted_closes_still_cannot_duplicate(self):
        builder = CandleBuilder("BTC/USDT")
        ts_base = 1700000000000
        for i in range(550):
            builder.process_kline(_make_kline(
                open_time=ts_base + i * 60_000, is_closed=True,
            ))

        assert len(builder.closed_candles) == 500
        assert builder.closed_candles[0]["open_time"] == ts_base + 50 * 60_000
        events = builder.process_kline(_make_kline(open_time=ts_base, is_closed=True))
        assert [e.event_type for e in events] == [BarEventType.DUPLICATE]
        assert len(builder.closed_candles) == 500

    def test_configured_interval_controls_next_open_not_close_timestamp(self):
        interval_ms = 900_000
        ts_base = 1700000000000
        builder = CandleBuilder("BTC/USDT", interval_ms=interval_ms)
        builder.process_kline(_make_kline(
            open_time=ts_base, close_time=ts_base + interval_ms - 1, is_closed=True,
        ))
        events = builder.process_kline(_make_kline(
            open_time=ts_base + interval_ms,
            close_time=ts_base + 2 * interval_ms - 1,
            is_closed=True,
        ))

        assert [e.event_type for e in events] == [BarEventType.BAR_CLOSED]
        assert builder.closed_candles[0]["close_time"] == ts_base + interval_ms - 1
        assert builder.expected_next_open_time == ts_base + 2 * interval_ms


class TestReset:
    def test_reset_clears_state(self):
        ts_base = 1700000000000
        builder = CandleBuilder("BTC/USDT")
        builder.process_kline(_make_kline(open_time=ts_base, is_closed=False))
        builder.process_kline(_make_kline(open_time=ts_base + 60_000, is_closed=True))

        builder.reset()
        assert builder.current_bar is None
        assert builder.expected_next_open_time is None
        assert len(builder.closed_candles) == 0


class TestSymbolFiltering:
    def test_ignores_different_symbol(self):
        builder = CandleBuilder("BTC/USDT")
        events = builder.process_kline(_make_kline(symbol="ETH/USDT", open_time=1000))
        assert len(events) == 0


class TestMultipleClosedBarsInSequence:
    def test_sequential_closed_bars(self):
        """Probar múltiples barras cerradas consecutivas."""
        ts_base = 1700000000000
        builder = CandleBuilder("BTC/USDT")
        events_all = []

        for i in range(5):
            events = builder.process_kline(_make_kline(
                open_time=ts_base + i * 60_000,
                is_closed=True,
                open_p=67000 + i * 100,
            ))
            events_all.extend(events)

        closed_events = [e for e in events_all if e.event_type == BarEventType.BAR_CLOSED]
        assert len(closed_events) == 5
        assert len(builder.closed_candles) == 5

        # Verificar precios secuenciales
        for i, candle in enumerate(builder.closed_candles):
            assert candle["open"] == 67000 + i * 100


class TestMixedClosedAndLive:
    def test_updates_and_repeated_closes_yield_one_final_candle_per_interval(self):
        builder = CandleBuilder("BTC/USDT")
        ts_base = 1700000000000
        closed_events = []
        for i in range(5):
            open_time = ts_base + i * 60_000
            for volume in (10.0, 15.0, 20.0):
                events = builder.process_kline(_make_kline(open_time=open_time, volume=volume))
                assert all(e.event_type != BarEventType.GAP_DETECTED for e in events)
            final = _make_kline(open_time=open_time, volume=25.0, is_closed=True)
            closed_events.extend(builder.process_kline(final))
            assert builder.process_kline(final)[0].event_type == BarEventType.DUPLICATE

        assert len(closed_events) == 5
        assert all(e.event_type == BarEventType.BAR_CLOSED for e in closed_events)
        assert len(builder.closed_candles) == 5
        assert all(c["volume"] == 25.0 for c in builder.closed_candles)

    def test_mixed_closed_and_live_bars(self):
        """Combinar barras cerradas con barra en formación."""
        ts_base = 1700000000000
        builder = CandleBuilder("BTC/USDT")

        # Barras cerradas históricas
        for i in range(3):
            builder.process_kline(_make_kline(
                open_time=ts_base + i * 60_000,
                is_closed=True,
            ))

        # Barra en formación (streaming) — next expected: ts_base + 3*60_000
        events = builder.process_kline(_make_kline(
            open_time=ts_base + 3 * 60_000,
            is_closed=False,
        ))

        started = [e for e in events if e.event_type == BarEventType.NEW_BAR_STARTED]
        assert len(started) == 1
        assert len(builder.closed_candles) == 3
