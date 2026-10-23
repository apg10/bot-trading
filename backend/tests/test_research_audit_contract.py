from dataclasses import FrozenInstanceError

import pytest

from src.models.research_audit import ResearchRequestAuditRecord
from src.api.research_allowlist import ALLOWED


def _record(**overrides):
    values = dict(
        request_id="request-1",
        tool_id="research",
        method="GET",
        canonical_route="/health",
        allowlist_decision=ALLOWED,
        sanitized_params={"symbol": "BTC/USDT"},
        provenance={"schema_version": "research_gateway.v1"},
        outcome="success",
        public_result_metadata={"status_code": 200},
        timing_ms=1.0,
        captured_at_ms=1,
    )
    values.update(overrides)
    return ResearchRequestAuditRecord(**values)


def test_immutable_record():
    record = _record()
    with pytest.raises((FrozenInstanceError, TypeError)):
        record.outcome = "error"


def test_mutually_exclusive_metadata_error():
    with pytest.raises(ValueError):
        _record(error_code="NOPE")


def test_no_secrets_in_sanitized_params():
    with pytest.raises(ValueError):
        _record(sanitized_params={"api_token": "secret"})


def test_default_schema_version():
    assert _record().schema_version == "research_request_audit.v1"


def test_minimal_record_creation():
    record = ResearchRequestAuditRecord(
        request_id="r", tool_id="t", method="GET", canonical_route="/health",
        allowlist_decision=ALLOWED,
    )
    assert record.outcome == "error"


def test_nested_list_value_rejected():
    with pytest.raises(ValueError, match="scalar"):
        _record(sanitized_params={"key": [1, 2, 3]})


def test_nested_dict_value_rejected():
    with pytest.raises(ValueError, match="scalar"):
        _record(sanitized_params={"key": {"nested": "val"}})


def test_tuple_value_rejected():
    with pytest.raises(ValueError, match="scalar"):
        _record(sanitized_params={"key": (1, 2)})
