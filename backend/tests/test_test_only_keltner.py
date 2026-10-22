"""Contract tests for the deterministic TEST_ONLY Keltner strategy."""

from __future__ import annotations

from copy import deepcopy
from decimal import Decimal

from src.indicators.catalog import INDICATOR_CATALOG
from src.models.paper_contracts import StrategyRef
from src.models.strategy_config import StrategyConfig
from src.strategies.test_only_keltner import (
    TEST_ONLY_KELTNER_CONFIG,
    TEST_ONLY_OHLCV_FIXTURE,
    TestOnlyKeltnerDecision,
    evaluate_test_only_keltner,
)


def test_configuration_is_canonical_and_catalog_validated() -> None:
    config = TEST_ONLY_KELTNER_CONFIG
    assert isinstance(config, StrategyConfig)
    assert isinstance(config.strategy_ref, StrategyRef)
    assert config.strategy_ref.strategy_id == "test_only_keltner_breakout"
    assert config.strategy_ref.strategy_version == "test_only_keltner.v1"
    instance = config.indicator_instances[0]
    assert instance.indicator_id in INDICATOR_CATALOG
    assert instance.indicator_id == "keltner"
    assert {
        parameter.name: parameter.value for parameter in instance.parameters
    } == {"ema_period": 3, "atr_period": 3, "multiplier": 0.5}


def test_fixture_is_identical_and_marked_test_only() -> None:
    first = tuple(candle.model_dump() for candle in TEST_ONLY_OHLCV_FIXTURE)
    second = tuple(candle.model_dump() for candle in TEST_ONLY_OHLCV_FIXTURE)
    assert first == second
    assert len(first) == 8
    assert all(candle.timestamp_ms % 60_000 == 0 for candle in TEST_ONLY_OHLCV_FIXTURE)
    assert evaluate_test_only_keltner(TEST_ONLY_OHLCV_FIXTURE[-1:]).data_source == "TEST_ONLY"
    assert all(
        evaluate_test_only_keltner(TEST_ONLY_OHLCV_FIXTURE[:index]).data_source == "TEST_ONLY"
        for index in range(1, len(TEST_ONLY_OHLCV_FIXTURE) + 1)
    )


def test_only_breakout_bar_produces_one_buy_signal() -> None:
    decisions = [
        evaluate_test_only_keltner(TEST_ONLY_OHLCV_FIXTURE[:index])
        for index in range(1, len(TEST_ONLY_OHLCV_FIXTURE) + 1)
    ]
    buys = [decision for decision in decisions if decision.signal == "BUY"]
    assert len(buys) == 1
    assert buys[0].timestamp_ms == TEST_ONLY_OHLCV_FIXTURE[6].timestamp_ms
    assert buys[0].strategy_config == TEST_ONLY_KELTNER_CONFIG


def test_before_breakout_and_insufficient_warmup_hold() -> None:
    insufficient = evaluate_test_only_keltner(TEST_ONLY_OHLCV_FIXTURE[:5])
    before_breakout = evaluate_test_only_keltner(TEST_ONLY_OHLCV_FIXTURE[:6])
    after_breakout = evaluate_test_only_keltner(TEST_ONLY_OHLCV_FIXTURE[:8])
    assert insufficient.signal == "HOLD"
    assert before_breakout.signal == "HOLD"
    assert after_breakout.signal == "HOLD"
    assert insufficient.entry_price is None
    assert before_breakout.entry_price is None


def test_breakout_has_ordered_protective_prices_and_reachable_target() -> None:
    decision = evaluate_test_only_keltner(TEST_ONLY_OHLCV_FIXTURE[:7])
    assert isinstance(decision, TestOnlyKeltnerDecision)
    assert decision.signal == "BUY"
    assert decision.entry_price is not None
    assert decision.stop_loss_price is not None
    assert decision.take_profit_price is not None
    assert decision.stop_loss_price < decision.entry_price < decision.take_profit_price
    assert TEST_ONLY_OHLCV_FIXTURE[7].high >= decision.take_profit_price


def test_input_ohlcv_is_not_mutated() -> None:
    candles = list(TEST_ONLY_OHLCV_FIXTURE)
    before = deepcopy(candles)
    evaluate_test_only_keltner(candles)
    assert candles == before


def test_decision_is_typed_and_has_no_execution_side_effects() -> None:
    decision = evaluate_test_only_keltner(TEST_ONLY_OHLCV_FIXTURE[:7])
    assert isinstance(decision, TestOnlyKeltnerDecision)
    assert decision.data_source == "TEST_ONLY"
    assert decision.strategy_config.strategy_ref.strategy_id == "test_only_keltner_breakout"
    assert decision.model_config.get("frozen") is True
    assert not hasattr(decision, "order")
    assert not hasattr(decision, "fill")
    assert not hasattr(decision, "reservation")
    assert Decimal(str(decision.entry_price)) == Decimal("104.0")
