"""PAPER advisory gate and N02 audit-boundary regression tests."""

from __future__ import annotations

import pytest

from src.ai.paper_advisory import AdvisoryAuditEnvelope, AdvisoryResult
from src.market.paper_snapshot import bar_identity
from src.models.audit_contract import N02AuditRecord
from src.paper_orchestrator import run_paper_cycle, run_paper_cycle_sell

from test_paper_orchestrator import _run, _sell_kwargs, _store, _kwargs


_CAPTURED_AT_MS = 3_000
_BAR_IDENTITY = bar_identity("1m", 2_000)


def _advisory(**overrides) -> AdvisoryResult:
    values = {
        "approved": True,
        "reason": "advisory approved",
        "snapshot_id": _BAR_IDENTITY,
        "valid_until_ms": _CAPTURED_AT_MS,
    }
    values.update(overrides)
    return AdvisoryResult(**values)


def _envelope(*, approved: bool = True, result_id: str = "result-1") -> AdvisoryAuditEnvelope:
    advisory = _advisory(approved=approved)
    return AdvisoryAuditEnvelope(
        advisory=advisory,
        audit_record=N02AuditRecord(
            snapshot_id=advisory.snapshot_id,
            result_id=result_id,
            model_name="n02",
            model_version="test",
            prompt_version="prompt-1",
            prompt_hash="a" * 64,
            safe_params={"symbol": "BTC/USDT", "interval": "1m", "snapshot_id": advisory.snapshot_id},
            success=True,
            response_json='{"approved":true}',
            timing_ms=1,
            valid_until_ms=advisory.valid_until_ms,
            captured_at_ms=_CAPTURED_AT_MS,
        ),
    )


def test_advisory_approved_permits_buy_pipeline(tmp_path) -> None:
    store = _store(tmp_path / "approved.db")
    result = _run(
        run_paper_cycle(store, **_kwargs(), advisory_input=_advisory())
    )
    assert result.success is True
    assert result.order_id == "order-id-1"
    _run(store.close())


def test_advisory_absent_allows_buy(tmp_path) -> None:
    store = _store(tmp_path / "absent.db")
    result = _run(
        run_paper_cycle(store, **_kwargs(), advisory_input=None)
    )
    assert result.success is True
    assert result.order_id == "order-id-1"
    _run(store.close())


def test_advisory_expired_blocks_buy(tmp_path) -> None:
    store = _store(tmp_path / "expired.db")
    result = _run(
        run_paper_cycle(
            store,
            **_kwargs(),
            advisory_input=_advisory(valid_until_ms=_CAPTURED_AT_MS - 1),
        )
    )
    assert result.success is False
    assert result.error == "advisory_expired"
    _run(store.close())


def test_advisory_not_approved_blocks_buy(tmp_path) -> None:
    store = _store(tmp_path / "not-approved.db")
    result = _run(
        run_paper_cycle(
            store, **_kwargs(), advisory_input=_advisory(approved=False)
        )
    )
    assert result.success is False
    assert result.error == "advisory_not_approved"
    _run(store.close())


def test_advisory_context_mismatch_blocks_buy(tmp_path) -> None:
    store = _store(tmp_path / "mismatch.db")
    result = _run(
        run_paper_cycle(
            store,
            **_kwargs(),
            advisory_input=_advisory(snapshot_id="another-bar"),
        )
    )
    assert result.success is False
    assert result.error == "advisory_context_mismatch"
    _run(store.close())


def test_advisory_does_not_affect_sell_exits(tmp_path) -> None:
    store = _store(tmp_path / "sell.db")
    buy = _run(
        run_paper_cycle(store, **_kwargs(), advisory_input=_advisory())
    )
    sell = _run(run_paper_cycle_sell(store, **_sell_kwargs()))
    assert buy.success is True
    assert sell.success is True
    assert sell.signal == "SELL"
    _run(store.close())


def test_advisory_zero_persistence_mutation_on_rejection(tmp_path) -> None:
    store = _store(tmp_path / "no-mutation.db")
    result = _run(
        run_paper_cycle(
            store,
            **_kwargs(),
            advisory_input=_advisory(approved=False),
        )
    )
    assert result.success is False
    assert _run(store.get_paper_orders("acct-1", "session-1")) == []
    assert _run(store.get_paper_fills("acct-1", "session-1")) == []
    _run(store.close())


def test_advisory_audit_envelope_safe_params() -> None:
    record = N02AuditRecord(
        snapshot_id="snapshot-1",
        result_id="result-1",
        model_name="n02",
        model_version="test",
        prompt_version="prompt-1",
        prompt_hash="a" * 64,
        safe_params={
            "symbol": "BTC/USDT",
            "interval": "1m",
            "snapshot_id": "snapshot-1",
            "bar_identity": _BAR_IDENTITY,
            "environment": "paper",
        },
        success=True,
        response_json='{"approved":true}',
        timing_ms=1,
        valid_until_ms=_CAPTURED_AT_MS,
        captured_at_ms=_CAPTURED_AT_MS,
    )
    assert set(dict(record.safe_params)) <= N02AuditRecord._ALLOWED_SAFE_KEYS


def test_advisory_envelope_persists_n02_record_end_to_end(tmp_path) -> None:
    store = _store(tmp_path / "envelope-approved.db")
    envelope = _envelope()
    result = _run(run_paper_cycle(store, **_kwargs(), advisory_input=envelope))
    assert result.success is True
    assert _run(store.load_n02_audit_record("result-1")) == envelope.audit_record
    assert len(_run(store.load_n02_audit_records(_BAR_IDENTITY))) == 1
    _run(store.close())


def test_rejected_advisory_persists_audit_without_trading_mutation(tmp_path) -> None:
    store = _store(tmp_path / "envelope-rejected.db")
    envelope = _envelope(approved=False)
    result = _run(run_paper_cycle(store, **_kwargs(), advisory_input=envelope))
    assert result.success is False
    assert _run(store.load_n02_audit_record("result-1")) == envelope.audit_record
    assert _run(store.get_paper_orders("acct-1", "session-1")) == []
    assert _run(store.get_paper_fills("acct-1", "session-1")) == []
    _run(store.close())


def test_advisory_envelope_mismatch_rejected() -> None:
    """Envelope with mismatched snapshot_id or valid_until_ms must fail construction."""
    advisory = _advisory(snapshot_id="different-bar")
    record = N02AuditRecord(
        snapshot_id=_BAR_IDENTITY,
        result_id="result-2",
        model_name="n02",
        model_version="test",
        prompt_version="prompt-1",
        prompt_hash="a" * 64,
        safe_params={"symbol": "BTC/USDT"},
        success=True,
        response_json='{"approved":true}',
        timing_ms=1,
        valid_until_ms=_CAPTURED_AT_MS,
        captured_at_ms=_CAPTURED_AT_MS,
    )
    with pytest.raises(ValueError):
        AdvisoryAuditEnvelope(advisory=advisory, audit_record=record)

    # valid_until_ms mismatch
    advisory2 = _advisory(valid_until_ms=_CAPTURED_AT_MS + 999)
    record2 = N02AuditRecord(
        snapshot_id=_BAR_IDENTITY,
        result_id="result-3",
        model_name="n02",
        model_version="test",
        prompt_version="prompt-1",
        prompt_hash="a" * 64,
        safe_params={"symbol": "BTC/USDT"},
        success=True,
        response_json='{"approved":true}',
        timing_ms=1,
        valid_until_ms=_CAPTURED_AT_MS,
        captured_at_ms=_CAPTURED_AT_MS,
    )
    with pytest.raises(ValueError):
        AdvisoryAuditEnvelope(advisory=advisory2, audit_record=record2)
