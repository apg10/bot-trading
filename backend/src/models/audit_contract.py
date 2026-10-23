"""Immutable causal replay audit records."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, StrictInt, StrictStr, field_validator


def _exact_decimal(value: object) -> Decimal:
    """Accept exact Decimal/string inputs, never binary numeric coercion."""
    if isinstance(value, (float, int, bool)):
        raise ValueError("OHLCV values must be Decimal or decimal strings")
    if isinstance(value, Decimal):
        result = value
    elif isinstance(value, str):
        try:
            result = Decimal(value)
        except InvalidOperation as exc:
            raise ValueError("invalid decimal string") from exc
    else:
        raise ValueError("OHLCV values must be Decimal or decimal strings")
    if not result.is_finite():
        raise ValueError("OHLCV values must be finite")
    return result


StrictDecimal = Annotated[Decimal, BeforeValidator(_exact_decimal)]


class CausalDecision(BaseModel):
    """The decision which caused (or declined) one PAPER operation."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    account_id: StrictStr = Field(..., min_length=1)
    paper_session_id: StrictStr = Field(..., min_length=1)
    decision_id: StrictStr = Field(..., min_length=1)
    signal_id: StrictStr = Field(..., min_length=1)
    segment_id: StrictStr = Field(..., min_length=1)
    symbol: StrictStr = Field(..., min_length=1)
    strategy: StrictStr = Field(..., min_length=1)
    is_test_only: Literal[True] = True
    timestamp: StrictInt | datetime
    candle_index: StrictInt = Field(..., ge=0)
    side: Literal["BUY", "SELL", "HOLD"]
    reason: StrictStr

    @field_validator("timestamp", mode="before")
    @classmethod
    def non_negative_timestamp(cls, value):
        if isinstance(value, (float, bool)):
            raise ValueError("timestamp must be an integer or datetime")
        if isinstance(value, int) and value < 0:
            raise ValueError("timestamp must be non-negative")
        return value


class CandleSnapshot(BaseModel):
    """Exact OHLCV input used by a causal decision."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    account_id: StrictStr = Field(..., min_length=1)
    paper_session_id: StrictStr = Field(..., min_length=1)
    decision_id: StrictStr = Field(..., min_length=1)
    symbol: StrictStr = Field(..., min_length=1)
    timestamp: StrictInt | datetime
    candle_index: StrictInt = Field(..., ge=0)
    open: StrictDecimal
    high: StrictDecimal
    low: StrictDecimal
    close: StrictDecimal
    volume: StrictDecimal = Field(..., ge=Decimal("0"))

    @field_validator("timestamp", mode="before")
    @classmethod
    def non_negative_timestamp(cls, value):
        if isinstance(value, (float, bool)):
            raise ValueError("timestamp must be an integer or datetime")
        if isinstance(value, int) and value < 0:
            raise ValueError("timestamp must be non-negative")
        return value
