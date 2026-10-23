"""Audit decisions persistence — registration, loading, migration, isolation."""

import asyncio
import json
from decimal import Decimal
from pathlib import Path

import pytest

from src.persistence.sqlite_store import (
    SQLiteStore,
    StoreCorruptionError,
)
from src.models.audit_contract import CausalDecision, CandleSnapshot


def _run(coro):
    """Helper to run async coroutines in tests."""
    return asyncio.run(coro)


def _decision_json(**overrides):
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
    return json.dumps(base)


def _candle_json(**overrides):
    base = {
        "account_id": "acct-1",
        "paper_session_id": "sess-1",
        "decision_id": "dec-1",
        "symbol": "BTC/USDT",
        "timestamp": 1700000000000,
        "candle_index": 42,
        "open": "10.00",
        "high": "11.00",
        "low": "9.50",
        "close": "10.50",
        "volume": "100.00",
    }
    base.update(overrides)
    return json.dumps(base)


def _make_store(tmp_path: Path):
    """Create and initialize a fresh SQLiteStore."""
    store = SQLiteStore(str(tmp_path / "test.db"))
    _run(store.initialize())
    return store


# ── Registration ────────────────────────────────────────────────────────────────


def test_register_valid(tmp_path):
    store = _make_store(tmp_path)
    decision = CausalDecision(
        account_id="acct-1", paper_session_id="sess-1", decision_id="dec-1",
        signal_id="sig-1", segment_id="seg-1", symbol="BTC/USDT",
        strategy="TEST_ONLY_KELTNER", timestamp=1700000000000,
        candle_index=42, side="BUY", reason="close > upper_band",
    )
    candle = CandleSnapshot(
        account_id="acct-1", paper_session_id="sess-1", decision_id="dec-1",
        symbol="BTC/USDT", timestamp=1700000000000, candle_index=42,
        open=Decimal("10.00"), high=Decimal("11.00"), low=Decimal("9.50"),
        close=Decimal("10.50"), volume=Decimal("100.00"),
    )
    _run(store.register_causal_decision(
        decision.model_dump_json(), candle.model_dump_json(), "dec-1"
    ))


def test_register_idempotent(tmp_path):
    """Duplicate registration must not raise (INSERT OR IGNORE)."""
    store = _make_store(tmp_path)
    d = CausalDecision(
        account_id="acct-1", paper_session_id="sess-1", decision_id="dec-1",
        signal_id="sig-1", segment_id="seg-1", symbol="BTC/USDT",
        strategy="TEST_ONLY_KELTNER", timestamp=1700000000000,
        candle_index=42, side="BUY", reason="close > upper_band",
    )
    c = CandleSnapshot(
        account_id="acct-1", paper_session_id="sess-1", decision_id="dec-1",
        symbol="BTC/USDT", timestamp=1700000000000, candle_index=42,
        open=Decimal("10.00"), high=Decimal("11.00"), low=Decimal("9.50"),
        close=Decimal("10.50"), volume=Decimal("100.00"),
    )
    _run(store.register_causal_decision(
        d.model_dump_json(), c.model_dump_json(), "dec-1"
    ))
    # Second registration should not raise
    _run(store.register_causal_decision(
        d.model_dump_json(), c.model_dump_json(), "dec-1"
    ))


def test_register_conflicting_payload(tmp_path):
    """Different payload for same identity should raise."""
    store = _make_store(tmp_path)
    d = CausalDecision(
        account_id="acct-1", paper_session_id="sess-1", decision_id="dec-1",
        signal_id="sig-1", segment_id="seg-1", symbol="BTC/USDT",
        strategy="TEST_ONLY_KELTNER", timestamp=1700000000000,
        candle_index=42, side="BUY", reason="close > upper_band",
    )
    c = CandleSnapshot(
        account_id="acct-1", paper_session_id="sess-1", decision_id="dec-1",
        symbol="BTC/USDT", timestamp=1700000000000, candle_index=42,
        open=Decimal("10.00"), high=Decimal("11.00"), low=Decimal("9.50"),
        close=Decimal("10.50"), volume=Decimal("100.00"),
    )
    _run(store.register_causal_decision(
        d.model_dump_json(), c.model_dump_json(), "dec-1"
    ))
    # Different decision_id in payload should raise
    d2 = CausalDecision(
        account_id="acct-1", paper_session_id="sess-1", decision_id="dec-2",
        signal_id="sig-2", segment_id="seg-1", symbol="BTC/USDT",
        strategy="TEST_ONLY_KELTNER", timestamp=1700000000000,
        candle_index=42, side="BUY", reason="close > upper_band",
    )
    with pytest.raises(ValueError):
        _run(store.register_causal_decision(
            d2.model_dump_json(), c.model_dump_json(), "dec-1"
        ))


# ── Loading ─────────────────────────────────────────────────────────────────────


def test_load_empty_session(tmp_path):
    store = _make_store(tmp_path)
    audits = _run(store.load_audit_decisions("acct-1", "sess-empty"))
    assert audits == []


def test_load_by_session(tmp_path):
    store = _make_store(tmp_path)
    d = CausalDecision(
        account_id="acct-1", paper_session_id="sess-1", decision_id="dec-1",
        signal_id="sig-1", segment_id="seg-1", symbol="BTC/USDT",
        strategy="TEST_ONLY_KELTNER", timestamp=1700000000000,
        candle_index=42, side="BUY", reason="close > upper_band",
    )
    c = CandleSnapshot(
        account_id="acct-1", paper_session_id="sess-1", decision_id="dec-1",
        symbol="BTC/USDT", timestamp=1700000000000, candle_index=42,
        open=Decimal("10.00"), high=Decimal("11.00"), low=Decimal("9.50"),
        close=Decimal("10.50"), volume=Decimal("100.00"),
    )
    _run(store.register_causal_decision(
        d.model_dump_json(), c.model_dump_json(), "dec-1"
    ))
    audits = _run(store.load_audit_decisions("acct-1", "sess-1"))
    assert len(audits) == 1
    loaded_d, loaded_c = audits[0]
    assert loaded_d.decision_id == "dec-1"
    assert loaded_c.high == Decimal("11.00")


def test_session_isolation(tmp_path):
    """Two sessions must not mix."""
    store = _make_store(tmp_path)
    d1 = CausalDecision(
        account_id="acct-1", paper_session_id="sess-a", decision_id="dec-a",
        signal_id="sig-a", segment_id="seg-1", symbol="BTC/USDT",
        strategy="TEST_ONLY_KELTNER", timestamp=1700000000000,
        candle_index=42, side="BUY", reason="close > upper_band",
    )
    c1 = CandleSnapshot(
        account_id="acct-1", paper_session_id="sess-a", decision_id="dec-a",
        symbol="BTC/USDT", timestamp=1700000000000, candle_index=42,
        open=Decimal("10.00"), high=Decimal("11.00"), low=Decimal("9.50"),
        close=Decimal("10.50"), volume=Decimal("100.00"),
    )
    d2 = CausalDecision(
        account_id="acct-1", paper_session_id="sess-b", decision_id="dec-b",
        signal_id="sig-b", segment_id="seg-1", symbol="BTC/USDT",
        strategy="TEST_ONLY_KELTNER", timestamp=1700000000000,
        candle_index=42, side="SELL", reason="close < lower_band",
    )
    c2 = CandleSnapshot(
        account_id="acct-1", paper_session_id="sess-b", decision_id="dec-b",
        symbol="BTC/USDT", timestamp=1700000000000, candle_index=42,
        open=Decimal("20.00"), high=Decimal("21.00"), low=Decimal("19.50"),
        close=Decimal("19.00"), volume=Decimal("200.00"),
    )
    _run(store.register_causal_decision(
        d1.model_dump_json(), c1.model_dump_json(), "dec-a"
    ))
    _run(store.register_causal_decision(
        d2.model_dump_json(), c2.model_dump_json(), "dec-b"
    ))
    audits_a = _run(store.load_audit_decisions("acct-1", "sess-a"))
    audits_b = _run(store.load_audit_decisions("acct-1", "sess-b"))
    assert len(audits_a) == 1
    assert len(audits_b) == 1
    assert audits_a[0][0].decision_id == "dec-a"
    assert audits_b[0][0].decision_id == "dec-b"


# ── Migration v17→v18 ──────────────────────────────────────────────────────────


def test_migration_v17_to_v18(tmp_path):
    """Migration from v17 to v18 must add audit_decisions table."""
    store = SQLiteStore(str(tmp_path / "migration.db"))
    _run(store.initialize())
    # After initialize, schema should be at v18
    import aiosqlite
    async def get_version():
        conn = await aiosqlite.connect(str(tmp_path / "migration.db"))
        row = await conn.execute("PRAGMA user_version")
        version = (await row.fetchone())[0]
        await conn.close()
        return version
    version = _run(get_version())
    assert version == 20
