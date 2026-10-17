"""Opaque reference to a concrete dataset instance.

``DatasetRef`` is an opaque identifier — it does not certify a provider,
promise replay, or carry metadata beyond the raw string itself.  Persistence,
resolution, and reconstruction of the referenced data belong to ACT-P-001.
"""

from __future__ import annotations

from typing import Annotated

from pydantic import BeforeValidator, AfterValidator


def _reject_non_string(value: object) -> str:
    """Reject every non-string type without coercion."""
    if not isinstance(value, str):
        raise ValueError(
            "dataset_ref must be a string; "
            f"got {type(value).__name__}"
        )
    return value


def _validate_dataset_ref(value: str) -> str:
    """Return *value* unchanged; raise if it is blank (after stripping)."""
    if not value.strip():
        raise ValueError(
            "dataset_ref must be a non-empty string; "
            f"got {value!r}"
        )
    return value


DatasetRef = Annotated[
    str,
    BeforeValidator(_reject_non_string),
    AfterValidator(_validate_dataset_ref),
]
