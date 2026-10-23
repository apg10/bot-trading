"""Immutable causal audit DTOs — structural and validation tests."""

import json
from decimal import Decimal

import pytest
from pydantic import ValidationError

from src.models.audit_contract import CandleSnapshot, CausalDecision


# ── CausalDecision ──────────────────────────────────────────────────────────────


def _decision(**overrides):
    base = {
        "account_id": "acct-1",
        "paper_session_id": "sess-1",
        "decision_id": "dec-1",
        "signal_id": "sig-1",
        "segment_id": "seg-1",
        "symbol": "BTC/USDT",
        "strategy": "TEST_ONLY_KELTNER",
        "is_test_only": True,
        "timestamp": 1700000000000,
        "candle_index": 42,
        "side": "BUY",
        "reason": "close > upper_band",
    }
    base.update(overrides)
    return base


def test_decision_valid():
    d = CausalDecision(**_decision())
    assert d.side == "BUY"
    assert d.is_test_only is True


def test_decision_frozen():
    d = CausalDecision(**_decision())
    with pytest.raises(ValidationError):
        d.side = "SELL"


def test_decision_extra_forbidden():
    with pytest.raises(ValidationError, match="extra_forbidden"):
        CausalDecision(**{**_decision(), "bogus": "field"})


def test_decision_side_rejected():
    for bad in ["BUY ", "buy", "LONG", ""]:
        with pytest.raises(ValidationError):
            CausalDecision(**{**_decision(), "side": bad})


def test_decision_timestamp_negative_rejected():
    with pytest.raises(ValidationError):
        CausalDecision(**{**_decision(), "timestamp": -1})


def test_decision_serialization_roundtrip():
    d = CausalDecision(**_decision())
    raw = d.model_dump(mode="json")
    assert raw["side"] == "BUY"
    restored = CausalDecision(**raw)
    assert restored == d


# ── CandleSnapshot ──────────────────────────────────────────────────────────────


def _candle(**overrides):
    base = {
        "account_id": "acct-1",
        "paper_session_id": "sess-1",
        "decision_id": "dec-1",
        "symbol": "BTC/USDT",
        "timestamp": 1700000000000,
        "candle_index": 42,
        "open": Decimal("10.00"),
        "high": Decimal("11.00"),
        "low": Decimal("9.50"),
        "close": Decimal("10.50"),
        "volume": Decimal("100.00"),
    }
    base.update(overrides)
    return base


def test_candle_valid():
    c = CandleSnapshot(**_candle())
    assert c.high == Decimal("11.00")


def test_candle_frozen():
    c = CandleSnapshot(**_candle())
    with pytest.raises(ValidationError):
        c.high = Decimal("12.00")


def test_candle_float_rejected():
    with pytest.raises(ValueError, match="must be Decimal or decimal strings"):
        CandleSnapshot(**{**_candle(), "open": 10.5})


def test_candle_serialization():
    c = CandleSnapshot(**_candle())
    raw = c.model_dump(mode="json")
    assert isinstance(raw["open"], str)
    restored = CandleSnapshot(**raw)
    assert restored == c
