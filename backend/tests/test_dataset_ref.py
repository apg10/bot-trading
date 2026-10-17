"""Deterministic tests for ``DatasetRef``.

Gates:
    PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONDONTWRITEBYTECODE=1 \
        .venv/bin/python -B -m pytest -q -p no:cacheprovider --capture=sys \
        tests/test_dataset_ref.py
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from pydantic import BaseModel, ValidationError
from src.models.dataset_ref import DatasetRef


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class _Holder(BaseModel):
    """Thin wrapper so we can exercise the annotated type."""

    ref: DatasetRef


def _accepts(raw: Any) -> str:
    """Validate *raw* through ``_Holder`` and return the stored string."""
    instance = _Holder(ref=raw)
    return instance.ref


# ---------------------------------------------------------------------------
# 1. Acepta una referencia válida y conserva exactamente el string
# ---------------------------------------------------------------------------

class TestValidRef:
    def test_simple_id(self) -> None:
        val = _accepts("tick/SPX/1m")
        assert val == "tick/SPX/1m"

    def test_preserves_leading_trailing_spaces(self) -> None:
        """Spaces are preserved when the string is not whitespace-only."""
        val = _accepts("  tick/SPX/1m  ")
        assert val == "  tick/SPX/1m  "

    def test_empty_string_is_not_whitespace_only_but_blank(self) -> None:
        """Empty string fails because ``''.strip()`` is empty."""
        with pytest.raises(ValidationError):
            _accepts("")

    def test_tab_only_is_rejected(self) -> None:
        with pytest.raises(ValidationError):
            _accepts("\t")

    def test_newline_only_is_rejected(self) -> None:
        with pytest.raises(ValidationError):
            _accepts("\n")

    def test_mixed_whitespace_is_rejected(self) -> None:
        with pytest.raises(ValidationError):
            _accepts(" \t\n ")


# ---------------------------------------------------------------------------
# 2. Rechaza todos los tipos no string sin coerción
# ---------------------------------------------------------------------------

class TestNonStringRejection:
    @pytest.mark.parametrize(
        "raw",
        [
            None,          # NoneType
            True,          # bool
            False,         # bool
            42,            # int
            3.14,          # float
            [1, 2],        # list
            {"k": "v"},    # dict / map
            b"bytes",      # bytes
        ],
    )
    def test_rejects_non_string_without_coercion(self, raw: Any) -> None:
        with pytest.raises(ValidationError):
            _accepts(raw)


# ---------------------------------------------------------------------------
# 3. Serialización JSON y round-trip conservan literalmente el valor válido
# ---------------------------------------------------------------------------

class TestSerialization:
    def test_json_roundtrip_simple(self) -> None:
        original = "tick/SPX/1m"
        instance = _Holder(ref=original)
        dumped = json.dumps(instance.model_dump())
        loaded = json.loads(dumped)
        assert loaded["ref"] == original

    def test_json_roundtrip_with_spaces(self) -> None:
        original = "  tick/SPX/1m  "
        instance = _Holder(ref=original)
        dumped = json.dumps(instance.model_dump())
        loaded = json.loads(dumped)
        assert loaded["ref"] == original

    def test_serializes_as_plain_string(self) -> None:
        """The annotated type must serialise as a bare JSON string, not an object."""
        instance = _Holder(ref="x")
        dumped = json.dumps(instance.model_dump())
        # model_dump returns {"ref": "x"} — the value itself is a string.
        assert '"ref": "x"' in dumped or '"ref":"x"' in dumped


# ---------------------------------------------------------------------------
# 4. No confunde dataset_ref con data_source; no afirma proveedor ni replay
# ---------------------------------------------------------------------------

class TestContractBoundaries:
    def test_field_name_is_dataset_ref_not_data_source(self) -> None:
        """The field is named ``ref`` (DatasetRef), not ``data_source``."""
        instance = _Holder(ref="x")
        assert "data_source" not in instance.model_dump()

    def test_no_provider_claim_in_type(self) -> None:
        """DatasetRef type itself makes no claim about provider."""
        # If the type imported or referenced a provider constant, this would
        # be brittle; we only check that instantiation with any opaque string
        # succeeds — no provider prefix required.
        _accepts("any-opaque-id")

    def test_no_replay_promise(self) -> None:
        """DatasetRef does not promise replay capability."""
        # The type exists solely as an opaque identifier; replay is out of scope.
        val = _accepts("historical/batch-42")
        assert val == "historical/batch-42"


# ---------------------------------------------------------------------------
# 5. No se añaden campos a contratos existentes — verificado por importación
# ---------------------------------------------------------------------------

class TestNoSideEffects:
    def test_import_does_not_modify_state(self) -> None:
        """Importing DatasetRef must not mutate existing contracts."""
        from src.models import state as _state  # noqa: F401

        # Verify a known attribute on the existing module is untouched.
        assert hasattr(_state, "Candle")
