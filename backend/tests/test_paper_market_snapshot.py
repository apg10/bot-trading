"""Validation contract for the public PAPER market snapshot."""

from dataclasses import FrozenInstanceError
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from src.market.paper_snapshot import PaperMarketExecutionSnapshot


def _snapshot(**changes):
    values = {
        "symbol": "BTC/USDT",
        "bid": Decimal("100"),
        "ask": Decimal("101"),
        "bid_qty": Decimal("1"),
        "ask_qty": Decimal("2"),
        "timestamp": datetime(2024, 1, 1, tzinfo=timezone.utc),
        "provenance": "binance_public_book_ticker",
        "version": "v3",
        "bar_identity": "15m:2024-01-01T00:00:00Z",
        "freshness_ok": True,
        "gap_ok": True,
        "entries_allowed": True,
    }
    values.update(changes)
    return PaperMarketExecutionSnapshot(**values)


def test_valid_public_snapshot_preserves_strict_market_values():
    snapshot = _snapshot()
    assert snapshot.bid == Decimal("100")
    assert snapshot.provenance == "binance_public_book_ticker"
    assert snapshot.version == "v3"


def test_snapshot_is_immutable():
    snapshot = _snapshot()
    with pytest.raises(FrozenInstanceError):
        snapshot.bid = Decimal("99")


@pytest.mark.parametrize("field", ["freshness_ok", "gap_ok", "entries_allowed"])
def test_snapshot_rejects_blocking_engine_state(field):
    with pytest.raises(ValueError):
        _snapshot(**{field: False})


@pytest.mark.parametrize("field", ["bid", "ask"])
@pytest.mark.parametrize("value", [Decimal("0"), Decimal("-1")])
def test_snapshot_rejects_non_positive_quotes(field, value):
    with pytest.raises(ValueError):
        _snapshot(**{field: value})


def test_snapshot_rejects_crossed_quotes():
    with pytest.raises(ValueError, match="crossed quote"):
        _snapshot(bid=Decimal("101"), ask=Decimal("100"))


@pytest.mark.parametrize("field", ["bid", "ask", "bid_qty", "ask_qty"])
def test_snapshot_rejects_non_decimal_market_values(field):
    with pytest.raises(ValueError):
        _snapshot(**{field: "100"})


def test_snapshot_rejects_test_only_provenance():
    with pytest.raises(ValueError):
        _snapshot(provenance="TEST_ONLY")
