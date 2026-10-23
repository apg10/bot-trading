"""Causal audit persistence at the replay-to-PAPER boundary."""

from __future__ import annotations

from pathlib import Path

from src.replay import DecisionSide
from src.paper_orchestrator import apply_signal_to_paper

from test_paper_orchestrator import TestSignalEntryAdapter, _risk, _run


def _audit(adapter: TestSignalEntryAdapter, tmp_path: Path, *, side=DecisionSide.BUY, **overrides):
    signal = adapter._make_signal(side=side)
    store, args = adapter._args(tmp_path / f"{side.value.lower()}.db", signal=signal, **overrides)
    result = apply_signal_to_paper(**args)
    rows = _run(store.load_audit_decisions("acct-1", "session-1"))
    return store, args, result, rows


def test_buy_signal_persists_audit(tmp_path: Path) -> None:
    store, _args, result, rows = _audit(TestSignalEntryAdapter(), tmp_path)
    assert result.success is True
    assert len(rows) == 1
    assert rows[0][0].side == "BUY"
    _run(store.close())


def test_sell_signal_persists_audit(tmp_path: Path) -> None:
    store, _args, result, rows = _audit(TestSignalEntryAdapter(), tmp_path, side=DecisionSide.SELL)
    assert result.success is False
    assert result.signal == "SELL"
    assert len(rows) == 1
    assert rows[0][0].side == "SELL"
    _run(store.close())


def test_decision_id_uses_signal_id(tmp_path: Path) -> None:
    adapter = TestSignalEntryAdapter()
    store, _args, result, rows = _audit(adapter, tmp_path)
    order = _run(store.load_paper_order(result.order_id))
    assert order is not None
    assert order.decision_id == rows[0][0].signal_id == "sig-0000"
    _run(store.close())


def test_hold_signal_persists_audit(tmp_path: Path) -> None:
    store, _args, result, rows = _audit(TestSignalEntryAdapter(), tmp_path, side=DecisionSide.HOLD)
    assert result.success is True
    assert result.order_id is None
    assert len(rows) == 1
    assert rows[0][0].side == "HOLD"
    _run(store.close())


def test_rejected_signal_persists_audit(tmp_path: Path) -> None:
    store, _args, result, rows = _audit(TestSignalEntryAdapter(), tmp_path, risk_policy=_risk("10"))
    assert result.success is False
    assert "risk rejected" in (result.error or "")
    assert len(rows) == 1
    assert rows[0][0].side == "BUY"
    assert _run(store.get_paper_orders("acct-1", "session-1")) == []
    _run(store.close())


def test_retry_does_not_duplicate_audit(tmp_path: Path) -> None:
    adapter = TestSignalEntryAdapter()
    store, args = adapter._args(tmp_path / "retry.db")
    first = apply_signal_to_paper(**args)
    _run(store.close())
    reopened, retry_args = adapter._args(tmp_path / "retry.db", signal=args["signal"])
    second = apply_signal_to_paper(**retry_args)
    rows = _run(reopened.load_audit_decisions("acct-1", "session-1"))
    assert first.success is True and second.success is True
    assert second.order_id == first.order_id
    assert second.fill_id == first.fill_id
    assert len(rows) == 1
    assert len(_run(reopened.get_paper_orders("acct-1", "session-1"))) == 1
    assert len(_run(reopened.get_paper_fills("acct-1", "session-1"))) == 1
    _run(reopened.close())
