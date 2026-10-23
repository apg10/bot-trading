"""Immutable causal audit DTOs — structural and validation tests."""

import json
from decimal import Decimal

import pytest
from pydantic import ValidationError

from src.models.audit_contract import CandleSnapshot, CausalDecision, N02AuditRecord


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


# ── N02AuditRecord ──────────────────────────────────────────────────────────────


def _n02_record(**overrides):
    base = {
        "snapshot_id": "snapshot-1",
        "result_id": "result-1",
        "model_name": "n02",
        "model_version": "2026.01",
        "prompt_version": "prompt-1",
        "prompt_hash": "a" * 64,
        "safe_params": {"symbol": "BTC/USDT", "interval": "1m"},
        "success": True,
        "response_json": '{"decision":"BUY"}',
        "error_code": None,
        "timing_ms": 42,
        "valid_until_ms": 1700000005000,
        "captured_at_ms": 1700000000000,
    }
    base.update(overrides)
    return base


def test_n02_audit_record_success_roundtrip():
    record = N02AuditRecord(**_n02_record())
    restored = N02AuditRecord.model_validate_json(record.model_dump_json())
    assert restored == record


def test_n02_audit_record_failure_roundtrip():
    record = N02AuditRecord(
        **_n02_record(success=False, response_json=None, error_code="N02_TIMEOUT")
    )
    restored = N02AuditRecord.model_validate_json(record.model_dump_json())
    assert restored == record


def test_n02_audit_record_success_error_exclusive():
    with pytest.raises(ValidationError):
        N02AuditRecord(**_n02_record(error_code="N02_FAILED"))


def test_n02_audit_record_immutable():
    record = N02AuditRecord(**_n02_record())
    with pytest.raises(TypeError, match="cannot assign to field"):
        record.result_id = "result-2"


def test_n02_audit_record_extra_forbidden():
    with pytest.raises(ValidationError, match="extra_forbidden"):
        N02AuditRecord(**_n02_record(extra_field="not allowed"))


def test_n02_audit_record_no_raw_prompt():
    with pytest.raises(ValidationError, match="extra_forbidden"):
        N02AuditRecord(**_n02_record(prompt="raw prompt text"))


def test_n02_audit_record_unsafe_param_key_rejected():
    with pytest.raises(ValidationError, match="unsafe param key: temperature"):
        N02AuditRecord(**_n02_record(safe_params={"temperature": "0"}))


def test_n02_audit_record_safe_params_immutable():
    """After construction, safe_params accepts only the allowlisted keys and
    the record cannot be mutated in any field.
    """
    record = N02AuditRecord(**_n02_record())
    with pytest.raises(TypeError, match="cannot assign to field"):
        record.safe_params = {"symbol": "ETH/USDT"}


def test_n02_audit_record_secret_value_filtered():
    with pytest.raises(ValidationError, match="unsafe param key: api_key"):
        N02AuditRecord(**_n02_record(safe_params={"api_key": "secret-value"}))


def test_n02_audit_record_safe_params_is_frozenset():
    """safe_params is stored as frozenset-of-tuples; direct mutation raises."""
    record = N02AuditRecord(**_n02_record())
    assert isinstance(record.safe_params, frozenset)
    with pytest.raises(AttributeError):
        record.safe_params.add(("symbol", "ETH/USDT"))


def test_n02_audit_record_safe_params_serializes_to_dict():
    """JSON round-trip produces a dict-compatible representation."""
    record = N02AuditRecord(**_n02_record())
    json_str = record.model_dump_json()
    restored = N02AuditRecord.model_validate_json(json_str)
    assert restored.safe_params == record.safe_params
