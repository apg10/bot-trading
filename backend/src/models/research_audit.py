"""Public, non-N02 request audit contract for the research gateway."""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from typing import Any, Literal

from src.api.research_allowlist import ALLOWED, BLOCKED, DEFERRED, DENIED


_DECISIONS = frozenset((ALLOWED, BLOCKED, DEFERRED, DENIED))
_OUTCOMES = frozenset(("admitted", "success", "unavailable", "insufficient_data", "rejected", "error"))
_SENSITIVE_NAME = re.compile(
    r"(?:secret|password|passwd|token|credential|authorization|cookie|session|api[_-]?key|private[_-]?key|prompt|body|header)",
    re.IGNORECASE,
)


def _validate_public_mapping(name: str, value: object) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise TypeError(f"{name} must be a dict")
    for key, item in value.items():
        if not isinstance(key, str) or not key or _SENSITIVE_NAME.search(key):
            raise ValueError(f"unsafe {name} key")
        # Parameters and provenance are deliberately JSON-shaped public data,
        # never opaque request objects or raw bodies.
        try:
            json.dumps(item, allow_nan=False)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{name} contains non-public JSON data") from exc
        if isinstance(item, (dict, list, tuple)):
            # Nested or composite objects make it too easy to smuggle a body or
            # diagnostic payload into this generic envelope.
            raise ValueError(f"{name} values must be scalar JSON values")
    return dict(value)


@dataclass(frozen=True, slots=True)
class ResearchRequestAuditRecord:
    """Immutable public metadata for one request crossing the gateway."""

    request_id: str
    tool_id: str
    method: str
    canonical_route: str
    allowlist_decision: str
    sanitized_params: dict[str, Any] = field(default_factory=dict)
    provenance: dict[str, Any] = field(default_factory=dict)
    outcome: str = "error"
    public_result_metadata: dict[str, Any] | None = None
    error_code: str | None = None
    timing_ms: float = 0.0
    captured_at_ms: int = 0
    snapshot_id: str | None = None
    result_id: str | None = None
    schema_version: Literal["research_request_audit.v1"] = "research_request_audit.v1"

    def __post_init__(self) -> None:
        for name in ("request_id", "tool_id", "method", "canonical_route", "schema_version"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must be a non-empty string")
        if self.schema_version != "research_request_audit.v1":
            raise ValueError("unsupported research audit schema_version")
        if self.allowlist_decision not in _DECISIONS:
            raise ValueError("invalid allowlist decision")
        if self.outcome not in _OUTCOMES:
            raise ValueError("invalid audit outcome")
        if isinstance(self.timing_ms, bool) or not isinstance(self.timing_ms, (int, float)):
            raise TypeError("timing_ms must be numeric")
        if not math.isfinite(float(self.timing_ms)) or self.timing_ms < 0:
            raise ValueError("timing_ms must be finite and non-negative")
        if isinstance(self.captured_at_ms, bool) or not isinstance(self.captured_at_ms, int):
            raise TypeError("captured_at_ms must be an integer")
        if self.captured_at_ms < 0:
            raise ValueError("captured_at_ms must be non-negative")
        object.__setattr__(
            self, "sanitized_params", _validate_public_mapping("sanitized_params", self.sanitized_params)
        )
        object.__setattr__(self, "provenance", _validate_public_mapping("provenance", self.provenance))
        if self.public_result_metadata is not None:
            object.__setattr__(
                self,
                "public_result_metadata",
                _validate_public_mapping("public_result_metadata", self.public_result_metadata),
            )
        if self.public_result_metadata is not None and self.error_code is not None:
            raise ValueError("public_result_metadata and error_code are mutually exclusive")
        for name in ("snapshot_id", "result_id", "error_code"):
            value = getattr(self, name)
            if value is not None and (not isinstance(value, str) or not value.strip()):
                raise ValueError(f"{name} must be a non-empty string when supplied")


__all__ = ["ResearchRequestAuditRecord"]
