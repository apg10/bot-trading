"""Pruebas del CandleBuilder: duplicados, huecos, ordenamiento, reconexión."""

import pytest
from src.market.candle_builder import CandleBuilder, BarEvent, BarEventType


def _make_kline(
    symbol: str = "BTC/USDT",
    open_time: int = 1700000000000,
    open_p: float = 67500.0,
    high: float = 68000.0,
    low: float = 67000.0,
    close: float = 67800.0,
    volume: float = 100.0,
    is_closed: bool = False,
) -> dict:
    return {
        "symbol": symbol,
        "open_time": open_time,
        "close_time": open_time + 60_000,
        "open": open_p,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
        "is_closed": is_closed,
    }


class TestDuplicateDetection:
    def test_ignores_duplicate(self):
        """Un segundo evento con el mismo open_time (is_closed=True) se ignora."""
        builder = CandleBuilder("BTC/USDT")

        # Abrir barra en vivo
        builder.process_kline(_make_kline(open_time=1000, is_closed=False))
        # Duplicado: misma open_time con is_closed=True (ya fue procesada)
        events = builder.process_kline(_make_kline(open_time=1000, is_closed=True))

        dup_events = [e for e in events if e.event_type == BarEventType.DUPLICATE]
        assert len(dup_events) == 1

    def test_no_duplicate_on_new_bar(self):
        builder = CandleBuilder("BTC/USDT")
        # Primera barra abierta
        builder.process_kline(_make_kline(open_time=1000, is_closed=False))
        # Segunda barra (nueva open_time)
        events = builder.process_kline(_make_kline(open_time=1060_000, is_closed=False))
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


class TestOutOfOrder:
    def test_out_of_order_treated_as_duplicate(self):
        builder = CandleBuilder("BTC/USDT")

        # Abrir barra en t=1000
        builder.process_kline(_make_kline(open_time=1000, is_closed=False))
        # Evento con open_time < expected (1060_000)
        events = builder.process_kline(_make_kline(open_time=500, is_closed=True))

        dup_events = [e for e in events if e.event_type == BarEventType.DUPLICATE]
        assert len(dup_events) == 1


class TestBarClosed:
    def test_bar_closed_event(self):
        builder = CandleBuilder("BTC/USDT")
        events = builder.process_kline(_make_kline(open_time=1000, is_closed=True))

        closed_events = [e for e in events if e.event_type == BarEventType.BAR_CLOSED]
        assert len(closed_events) == 1
        assert closed_events[0].candle["is_closed"] is True
        assert closed_events[0].candle["open_time"] == 1000


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
