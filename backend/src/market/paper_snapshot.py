"""Fail-closed market data contract for PAPER execution.

This DTO contains only an observed public quote and the market-engine gates.
It deliberately does not contain capacity, fees, risk, or order information.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal


def _strict_decimal(value: object, field: str) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, Decimal):
        raise ValueError(f"{field} must be an explicit Decimal")
    if not value.is_finite():
        raise ValueError(f"{field} must be finite")
    return value


@dataclass(frozen=True)
class PaperMarketExecutionSnapshot:
    """A validated public Binance quote bound to a closed market bar."""

    symbol: str
    bid: Decimal
    ask: Decimal
    bid_qty: Decimal
    ask_qty: Decimal
    timestamp: datetime
    provenance: str
    version: str
    bar_identity: str
    freshness_ok: bool
    gap_ok: bool
    entries_allowed: bool

    def __post_init__(self) -> None:
        if not isinstance(self.symbol, str) or not self.symbol.strip():
            raise ValueError("symbol must be a non-empty string")
        if not isinstance(self.timestamp, datetime) or self.timestamp.tzinfo is None:
            raise ValueError("timestamp must be timezone-aware")
        if not isinstance(self.provenance, str) or self.provenance == "TEST_ONLY":
            raise ValueError("TEST_ONLY provenance is not permitted")
        if self.provenance != "binance_public_book_ticker":
            raise ValueError("unsupported market snapshot provenance")
        if self.version != "v3":
            raise ValueError("unsupported market snapshot version")
        if not isinstance(self.bar_identity, str) or not self.bar_identity.strip():
            raise ValueError("bar_identity must be a non-empty string")
        for field in ("freshness_ok", "gap_ok", "entries_allowed"):
            if type(getattr(self, field)) is not bool:
                raise ValueError(f"{field} must be a bool")
        if not self.freshness_ok:
            raise ValueError("market quote is stale")
        if not self.gap_ok:
            raise ValueError("market candles contain a gap")
        if not self.entries_allowed:
            raise ValueError("market engine does not allow entries")

        bid = _strict_decimal(self.bid, "bid")
        ask = _strict_decimal(self.ask, "ask")
        _strict_decimal(self.bid_qty, "bid_qty")
        _strict_decimal(self.ask_qty, "ask_qty")
        if bid <= 0 or ask <= 0:
            raise ValueError("bid and ask must be positive")
        if bid >= ask:
            raise ValueError("crossed quote: bid must be strictly less than ask")
        if self.bid_qty < 0 or self.ask_qty < 0:
            raise ValueError("quote quantities must not be negative")


def bar_identity(interval: str, open_time_ms: int) -> str:
    """Return the stable UTC identity for a closed Binance bar."""
    if not isinstance(interval, str) or not interval or type(open_time_ms) is not int:
        raise ValueError("invalid closed-bar identity")
    opened = datetime.fromtimestamp(open_time_ms / 1000, tz=timezone.utc)
    return f"{interval}:{opened.isoformat(timespec='seconds').replace('+00:00', 'Z')}"
