"""Pruebas de los contratos Pydantic y fixture sintética."""

import pytest
from pydantic import ValidationError

from src.models.state import (
    Candle, MarketSnapshot, Zone, Pivot, Annotation, Order,
    EngineInfo, EngineState, MarketStatus, Environment, AnalysisResult,
    validate_snapshot,
)


class TestCandle:
    def test_valid_candle(self):
        c = Candle(
            timestamp_ms=1700000000000,
            open=67500.0,
            high=68000.0,
            low=67000.0,
            close=67800.0,
            volume=123.45,
        )
        assert c.open == 67500.0
        assert c.high == 68000.0

    def test_invalid_candle_volume(self):
        with pytest.raises(ValidationError):
            Candle(
                timestamp_ms=1700000000000,
                open=67500.0,
                high=68000.0,
                low=67000.0,
                close=67800.0,
                volume=-1.0,  # type: ignore
            )


class TestMarketSnapshot:
    def test_valid_snapshot(self):
        c = Candle(
            timestamp_ms=1700000000000,
            open=67500.0,
            high=68000.0,
            low=67000.0,
            close=67800.0,
            volume=123.45,
        )
        snap = MarketSnapshot(
            symbol="BTC/USDT",
            environment=Environment.PAPER,
            candle=c,
            bid=67790.0,
            ask=67810.0,
            timestamp_ms=1700000000000,
        )
        assert snap.symbol == "BTC/USDT"

    def test_invalid_bid_ask(self):
        c = Candle(
            timestamp_ms=1700000000000,
            open=67500.0,
            high=68000.0,
            low=67000.0,
            close=67800.0,
            volume=123.45,
        )
        with pytest.raises(ValidationError):
            snap = MarketSnapshot(
                symbol="BTC/USDT",
                environment=Environment.PAPER,
                candle=c,
                bid=67810.0,  # > ask
                ask=67790.0,
                timestamp_ms=1700000000000,
            )


class TestValidateSnapshot:
    def test_valid_snapshot_validation(self):
        c = Candle(
            timestamp_ms=1700000000000,
            open=67500.0,
            high=68000.0,
            low=67000.0,
            close=67800.0,
            volume=123.45,
        )
        snap = MarketSnapshot(
            symbol="BTC/USDT",
            environment=Environment.PAPER,
            candle=c,
            bid=67790.0,
            ask=67810.0,
            timestamp_ms=1700000000000,
        )
        # No debe levantar excepción
        validate_snapshot(snap)

    def test_invalid_high_low(self):
        """high < low ya es inválido al crear Candle (Pydantic valida)."""
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            Candle(
                timestamp_ms=1700000000000,
                open=67500.0,
                high=67000.0,  # < low!
                low=68000.0,
                close=67800.0,
                volume=123.45,
            )


class TestFixture:
    def test_candles_count(self):
        from src.fixtures.synthetic import generate_candles
        candles = generate_candles(100)
        assert len(candles) == 100

    def test_deterministic(self):
        """Dos llamadas con la misma semilla producen el mismo resultado."""
        from src.fixtures.synthetic import generate_candles
        a = generate_candles(50)
        b = generate_candles(50)
        assert len(a) == len(b)
        for ca, cb in zip(a, b):
            assert ca.open == cb.open
            assert ca.high == cb.high
            assert ca.low == cb.low
            assert ca.close == cb.close

    def test_full_fixture(self):
        from src.fixtures.synthetic import get_full_fixture
        data = get_full_fixture()
        assert "snapshot" in data
        assert "candles" in data
        assert len(data["candles"]) == 10
        assert "zones" in data
        assert "pivots" in data
        assert "annotations" in data


class TestEngineInfo:
    def test_default_values(self):
        info = EngineInfo(
            state=EngineState.STOPPED,
            environment=Environment.PAPER,
            market_status=MarketStatus.DISCONNECTED,
        )
        assert info.uptime_seconds == 0.0
        assert info.version == "0.1.0"


class TestAnalysisResult:
    def test_valid_analysis(self):
        result = AnalysisResult(
            strategy_version="test-v1",
            action="enter_long",
            confidence=0.85,
            evidence=["zone support confirmed"],
            invalidation_conditions=["break below 67000"],
        )
        assert result.action == "enter_long"
        assert len(result.evidence) == 1
