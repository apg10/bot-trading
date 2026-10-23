"""Immutable causal replay audit records."""

from __future__ import annotations

import json
from decimal import Decimal, InvalidOperation
from datetime import datetime
from typing import Annotated, ClassVar, Literal

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StrictInt,
    StrictStr,
    field_serializer,
    field_validator,
    model_validator,
)


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


class N02AuditRecord(BaseModel):
    """Immutable, versioned audit envelope for one N02 analysis result.

    The envelope deliberately carries only a prompt hash, sanitized parameters,
    and a public error code.  Raw prompts, credentials, and diagnostic details
    are not part of this contract.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    _ALLOWED_SAFE_KEYS: ClassVar[frozenset[str]] = frozenset(
        {
            "symbol",
            "interval",
            "candles_count",
            "snapshot_id",
            "bar_identity",
            "environment",
            "strategy_name",
            "policy_id",
            "filter_set_id",
        }
    )

    snapshot_id: StrictStr = Field(..., min_length=1)
    result_id: StrictStr = Field(..., min_length=1)
    model_name: StrictStr = Field(..., min_length=1)
    model_version: StrictStr = Field(..., min_length=1)
    prompt_version: StrictStr = Field(..., min_length=1)
    prompt_hash: StrictStr = Field(
        ...,
        min_length=64,
        max_length=64,
        pattern=r"^[0-9a-fA-F]{64}$",
    )
    safe_params: dict[str, str]
    success: bool
    response_json: StrictStr | None = None
    error_code: StrictStr | None = None
    timing_ms: StrictInt = Field(..., ge=0)
    valid_until_ms: StrictInt = Field(..., ge=0)
    captured_at_ms: StrictInt = Field(..., ge=0)

    @field_validator("safe_params")
    @classmethod
    def reject_unsanitized_parameter_names(cls, value: dict[str, str]) -> dict[str, str]:
        """Permit only the explicitly safe parameter names."""
        for key in value:
            if key not in cls._ALLOWED_SAFE_KEYS:
                raise ValueError(f"unsafe param key: {key}")
        return value

    def __setattr__(self, name: str, value: object) -> None:
        """Prevent post-construction mutation of any field."""
        if getattr(self, "_initialized", False):
            raise TypeError(
                f"cannot assign to field '{name}' of frozen N02AuditRecord"
            )
        super().__setattr__(name, value)

    def __post_init__(self) -> None:
        """Mark the record as fully constructed; all fields are now immutable."""
        object.__setattr__(self, "_initialized", True)

    def model_post_init(self, __context: object) -> None:
        self.__post_init__()
        # Convert safe_params to an immutable frozenset-of-tuples so that
        # direct key assignment (record.safe_params["k"] = "v") raises TypeError.
        # The field_serializer converts it back to dict for JSON output.
        raw = self.safe_params
        object.__setattr__(self, "safe_params", frozenset(raw.items()))

    @field_validator("response_json")
    @classmethod
    def require_json_response(cls, value: str | None) -> str | None:
        if value is not None:
            try:
                json.loads(value)
            except (TypeError, ValueError) as exc:
                raise ValueError("response_json must contain serialized JSON") from exc
        return value

    @field_serializer("safe_params")
    @classmethod
    def serialize_safe_params(cls, value: object) -> dict[str, str]:
        """Convert internal frozenset-of-tuples to dict for JSON serialisation."""
        if isinstance(value, frozenset):
            return dict(value)
        return value  # pragma: no cover — construction path

    @model_validator(mode="after")
    def validate_outcome(self) -> "N02AuditRecord":
        if self.success:
            if self.error_code is not None:
                raise ValueError("successful records cannot contain error_code")
            if self.response_json is None:
                raise ValueError("successful records require response_json")
        elif self.error_code is None:
            raise ValueError("failed records require error_code")

        if self.response_json is not None and self.error_code is not None:
            raise ValueError("response_json and error_code are mutually exclusive")
        return self


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
