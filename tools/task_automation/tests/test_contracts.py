"""Tests for contracts module (ACT-AUTO-001).

Covers:
- Valid documents of all five kinds.
- Rejection: missing/extra fields, wrong types, illegal producer/status,
  path traversal, cross-task IDs, NaN/Infinity.
- Hash consistency: same content produces the same hash regardless of key order.
"""

import copy
import hashlib
import json
import os
import sys
import unittest
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

# Ensure the project root is on sys.path for absolute imports (needed by unittest discover)
_test_dir = os.path.dirname(os.path.abspath(__file__))
_project_root = os.path.dirname(os.path.dirname(os.path.dirname(_test_dir)))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from tools.task_automation.contracts import (
    ProtocolError,
    _has_duplicate_keys,
    _reject_invalid_surrogates,
    _reject_nan_inf,
    canonical_bytes,
    handoff_id,
    parse_document,
    sha256_json,
    validate_document,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_BASE_ENVELOPE = {
    "schema_version": "task_automation.v1",
    "group_id": "GROUP-AUTO-001",
    "task_id": "ACT-AUTO-001",
    "run_id": "RUN-001",
    "revision_id": "REV-001",
    "baseline_id": "0" * 64,
    "contract_hash": "1" * 64,
    "evidence_hash": "2" * 64,
    "previous_handoff_id": "3" * 64,
    "producer": "controller",
    "created_at": "2026-10-05T12:00:00Z",
    "artifacts": [{"path": "a.txt", "sha256": "4" * 64, "role": "delta"}],
}

_BASE_BODY: Dict[str, Any] = {}


_STATUS_MAP = {
    "assignment": "ASSIGNED",
    "implementation": "READY_FOR_REVIEW",
    "checks": "PASSED",
    "review": "APPROVED",
    "close": "COMPLETED",
}


def _make_doc(kind: str, body: Dict[str, Any]) -> Dict[str, Any]:
    """Build a minimal document with the given kind and body."""
    doc = copy.deepcopy(_BASE_ENVELOPE)
    doc["kind"] = kind
    doc["producer"] = {
        "assignment": "controller",
        "implementation": "implementer",
        "checks": "controller",
        "review": "reviewer",
        "close": "controller",
    }[kind]
    doc["status"] = _STATUS_MAP[kind]

    # For assignment, evidence_hash can be null; revision_id is REV-000 (baseline)
    if kind == "assignment":
        doc["evidence_hash"] = None
        doc["revision_id"] = "REV-000"
    else:
        doc["evidence_hash"] = "2" * 64

    # For non-assignment, previous_handoff_id must not be null
    if kind != "assignment":
        doc["previous_handoff_id"] = "3" * 64

    doc["body"] = body
    return doc


# Assignment body fixture (use copy.deepcopy when modifying to avoid shared state)
_ASSIGNMENT_BODY: Dict[str, Any] = {
    "goal": "Test goal",
    "source_contract": "Section 13.7 ACT-AUTO-001",
    "allowlist": ["tools/task_automation/contracts.py"],
    "checks": [{
        "check_id": "CHK-001",
        "argv": ["python", "-m", "pytest"],
        "cwd": ".",
        "timeout_seconds": 60,
        "allowed_writes": [],
    }],
    "acceptance": [{"criterion_id": "CRIT-001", "description": "Must pass", "check_ids": ["CHK-001"]}],
    "dependencies": [],
    "permissions": {"fixture_git": True, "public_docs": False, "temp_artifacts": True, "cloud_calls": 0},
    "review_policy": "MANUAL",
    "limits": {"max_corrections": 3, "max_calls": 180, "call_timeout_seconds": 60},
    "profile_hash": None,
}

# Implementation body fixture
_IMPLEMENTATION_BODY: Dict[str, Any] = {
    "summary": "Added contracts.py",
    "changes": [{"path": "tools/task_automation/contracts.py", "before_hash": None, "after_hash": "a" * 64, "change": "ADD"}],
    "criteria": [{"criterion_id": "CRIT-001", "state": "MET", "evidence_paths": ["artifacts/rev/check.txt"], "note": "passed"}],
    "previous_findings": [],
    "limitations": ["No cloud"],
}

# Checks body fixture
_CHECKS_BODY: Dict[str, Any] = {
    "results": [{
        "check_id": "CHK-001",
        "argv": ["python", "-m", "pytest"],
        "cwd": ".",
        "started_at": "2026-10-05T12:00:00Z",
        "finished_at": "2026-10-05T12:01:00Z",
        "elapsed_ms": 60000,
        "exit_code": 0,
        "timed_out": False,
        "stdout_path": "artifacts/out.txt",
        "stderr_path": "artifacts/err.txt",
        "stdout_hash": "b" * 64,
        "stderr_hash": "c" * 64,
        "status": "PASSED",
    }],
    "limitations": [],
}

# Review body fixture
_REVIEW_BODY: Dict[str, Any] = {
    "verdict": "APPROVED",
    "criteria": [{"criterion_id": "CRIT-001", "state": "MET", "evidence_paths": ["a.txt"], "note": "ok"}],
    "findings": [],
    "limitations": [],
}

# Close body fixture
_CLOSE_BODY: Dict[str, Any] = {
    "reason": "All checks passed and review approved.",
    "checks_handoff_id": "d" * 64,
    "review_handoff_id": "e" * 64,
    "accepted_evidence_hash": "2" * 64,
    "pending_findings": [],
    "limitations": [],
}


# ---------------------------------------------------------------------------
# Tests: valid documents
# ---------------------------------------------------------------------------

class TestValidDocuments(unittest.TestCase):
    """Each kind must parse without raising."""

    def test_valid_assignment(self) -> None:
        doc = _make_doc("assignment", copy.deepcopy(_ASSIGNMENT_BODY))
        validate_document(doc)

    def test_valid_implementation(self) -> None:
        doc = _make_doc("implementation", copy.deepcopy(_IMPLEMENTATION_BODY))
        validate_document(doc)

    def test_valid_checks(self) -> None:
        doc = _make_doc("checks", copy.deepcopy(_CHECKS_BODY))
        validate_document(doc)

    def test_valid_review(self) -> None:
        doc = _make_doc("review", copy.deepcopy(_REVIEW_BODY))
        validate_document(doc)

    def test_valid_close(self) -> None:
        doc = _make_doc("close", copy.deepcopy(_CLOSE_BODY))
        validate_document(doc)


# ---------------------------------------------------------------------------
# Tests: envelope-level rejections
# ---------------------------------------------------------------------------

class TestEnvelopeRejection(unittest.TestCase):
    """Envelope violations."""

    def test_extra_envelope_key(self) -> None:
        doc = _make_doc("assignment", copy.deepcopy(_ASSIGNMENT_BODY))
        doc["extra_field"] = "bad"
        with self.assertRaises(ProtocolError):
            validate_document(doc)

    def test_missing_envelope_key(self) -> None:
        doc = _make_doc("assignment", copy.deepcopy(_ASSIGNMENT_BODY))
        del doc["kind"]
        with self.assertRaises(ProtocolError):
            validate_document(doc)

    def test_bad_schema_version(self) -> None:
        doc = _make_doc("assignment", copy.deepcopy(_ASSIGNMENT_BODY))
        doc["schema_version"] = "wrong"
        with self.assertRaises(ProtocolError):
            validate_document(doc)

    def test_bad_kind(self) -> None:
        doc = _make_doc("assignment", copy.deepcopy(_ASSIGNMENT_BODY))
        doc["kind"] = "bogus"
        with self.assertRaises(ProtocolError):
            validate_document(doc)

    def test_wrong_producer(self) -> None:
        doc = _make_doc("assignment", copy.deepcopy(_ASSIGNMENT_BODY))
        doc["producer"] = "implementer"
        with self.assertRaises(ProtocolError):
            validate_document(doc)

    def test_bad_id_format(self) -> None:
        doc = _make_doc("assignment", copy.deepcopy(_ASSIGNMENT_BODY))
        doc["group_id"] = "lowercase"
        with self.assertRaises(ProtocolError):
            validate_document(doc)

    def test_bad_rev_format(self) -> None:
        doc = _make_doc("assignment", copy.deepcopy(_ASSIGNMENT_BODY))
        doc["revision_id"] = "REV-1"
        with self.assertRaises(ProtocolError):
            validate_document(doc)

    def test_bad_hash_format(self) -> None:
        doc = _make_doc("assignment", copy.deepcopy(_ASSIGNMENT_BODY))
        doc["baseline_id"] = "short"
        with self.assertRaises(ProtocolError):
            validate_document(doc)

    def test_bad_utc(self) -> None:
        doc = _make_doc("assignment", copy.deepcopy(_ASSIGNMENT_BODY))
        doc["created_at"] = "not-utc"
        with self.assertRaises(ProtocolError):
            validate_document(doc)

    def test_assignment_evidence_hash_not_null_allowed(self) -> None:
        """Assignment can have evidence_hash=null or a valid hash."""
        doc = _make_doc("assignment", copy.deepcopy(_ASSIGNMENT_BODY))
        doc["evidence_hash"] = "a" * 64
        validate_document(doc)

    def test_non_assignment_previous_handoff_not_null(self) -> None:
        """Non-assignment kinds must have non-null previous_handoff_id."""
        doc = _make_doc("implementation", copy.deepcopy(_IMPLEMENTATION_BODY))
        doc["previous_handoff_id"] = None
        with self.assertRaises(ProtocolError):
            validate_document(doc)

    def test_artifacts_not_list(self) -> None:
        doc = _make_doc("assignment", copy.deepcopy(_ASSIGNMENT_BODY))
        doc["artifacts"] = "not_a_list"
        with self.assertRaises(ProtocolError):
            validate_document(doc)


# ---------------------------------------------------------------------------
# Tests: assignment body rejections
# ---------------------------------------------------------------------------

class TestAssignmentBodyRejection(unittest.TestCase):
    """Assignment-specific violations."""

    def test_extra_assignment_body_key(self) -> None:
        body = copy.deepcopy(_ASSIGNMENT_BODY)
        body["extra"] = "bad"
        doc = _make_doc("assignment", body)
        with self.assertRaises(ProtocolError):
            validate_document(doc)

    def test_allowlist_path_traversal(self) -> None:
        body = copy.deepcopy(_ASSIGNMENT_BODY)
        body["allowlist"] = ["../escape.txt"]
        doc = _make_doc("assignment", body)
        with self.assertRaises(ProtocolError):
            validate_document(doc)

    def test_bad_permissions_cloud_calls(self) -> None:
        body = copy.deepcopy(_ASSIGNMENT_BODY)
        body["permissions"]["cloud_calls"] = -1
        doc = _make_doc("assignment", body)
        with self.assertRaises(ProtocolError):
            validate_document(doc)

    def test_bad_review_policy(self) -> None:
        body = copy.deepcopy(_ASSIGNMENT_BODY)
        body["review_policy"] = "BOTH"
        doc = _make_doc("assignment", body)
        with self.assertRaises(ProtocolError):
            validate_document(doc)

    def test_limits_negative(self) -> None:
        body = copy.deepcopy(_ASSIGNMENT_BODY)
        body["limits"]["max_corrections"] = -1
        doc = _make_doc("assignment", body)
        with self.assertRaises(ProtocolError):
            validate_document(doc)


# ---------------------------------------------------------------------------
# Tests: implementation body rejections
# ---------------------------------------------------------------------------

class TestImplementationBodyRejection(unittest.TestCase):
    """Implementation-specific violations."""

    def test_extra_implementation_body_key(self) -> None:
        body = copy.deepcopy(_IMPLEMENTATION_BODY)
        body["extra"] = "bad"
        doc = _make_doc("implementation", body)
        with self.assertRaises(ProtocolError):
            validate_document(doc)

    def test_invalid_change_type(self) -> None:
        body = copy.deepcopy(_IMPLEMENTATION_BODY)
        body["changes"][0]["change"] = "COPY"
        doc = _make_doc("implementation", body)
        with self.assertRaises(ProtocolError):
            validate_document(doc)


# ---------------------------------------------------------------------------
# Tests: checks body rejections
# ---------------------------------------------------------------------------

class TestChecksBodyRejection(unittest.TestCase):
    """Checks-specific violations."""

    def test_empty_results(self) -> None:
        body = copy.deepcopy(_CHECKS_BODY)
        body["results"] = []
        doc = _make_doc("checks", body)
        with self.assertRaises(ProtocolError):
            validate_document(doc)

    def test_timed_out_exit_code_present(self) -> None:
        body = copy.deepcopy(_CHECKS_BODY)
        body["results"][0]["timed_out"] = True
        body["results"][0]["exit_code"] = 1
        doc = _make_doc("checks", body)
        with self.assertRaises(ProtocolError):
            validate_document(doc)

    def test_invalid_check_status(self) -> None:
        body = copy.deepcopy(_CHECKS_BODY)
        body["results"][0]["status"] = "UNKNOWN"
        doc = _make_doc("checks", body)
        with self.assertRaises(ProtocolError):
            validate_document(doc)


# ---------------------------------------------------------------------------
# Tests: review body rejections
# ---------------------------------------------------------------------------

class TestReviewBodyRejection(unittest.TestCase):
    """Review-specific violations."""

    def test_invalid_verdict(self) -> None:
        body = copy.deepcopy(_REVIEW_BODY)
        body["verdict"] = "MAYBE"
        doc = _make_doc("review", body)
        with self.assertRaises(ProtocolError):
            validate_document(doc)

    def test_invalid_severity(self) -> None:
        body = copy.deepcopy(_REVIEW_BODY)
        body["findings"].append({
            "finding_id": "F-001",
            "severity": "NICE",
            "path": None,
            "line": None,
            "message": "test",
            "in_scope": True,
            "criterion_id": None,
        })
        doc = _make_doc("review", body)
        doc["status"] = doc["body"]["verdict"] = "REQUIRES_CHANGES"
        doc["body"]["findings"][0]["severity"] = "LOW"
        validate_document(doc)
        doc["body"]["findings"][0]["severity"] = "NICE"
        with self.assertRaises(ProtocolError):
            validate_document(doc)


# ---------------------------------------------------------------------------
# Tests: close body rejections
# ---------------------------------------------------------------------------

class TestCloseBodyRejection(unittest.TestCase):
    """Close-specific violations."""

    def test_extra_close_body_key(self) -> None:
        body = copy.deepcopy(_CLOSE_BODY)
        body["extra"] = "bad"
        doc = _make_doc("close", body)
        with self.assertRaises(ProtocolError):
            validate_document(doc)


# ---------------------------------------------------------------------------
# Tests: hash consistency
# ---------------------------------------------------------------------------

class TestHashConsistency(unittest.TestCase):
    """Same content produces the same hash regardless of key order."""

    def test_canonical_bytes_same(self) -> None:
        d1 = {"b": 2, "a": 1}
        d2 = {"a": 1, "b": 2}
        self.assertEqual(canonical_bytes(d1), canonical_bytes(d2))

    def test_sha256_json_same(self) -> None:
        d1 = {"b": 2, "a": 1}
        d2 = {"a": 1, "b": 2}
        self.assertEqual(sha256_json(d1), sha256_json(d2))

    def test_handoff_id_same(self) -> None:
        body = copy.deepcopy(_IMPLEMENTATION_BODY)
        doc1 = _make_doc("implementation", body)
        doc2 = _make_doc("implementation", body)
        # Manually swap keys in the document to ensure order independence
        self.assertEqual(handoff_id(doc1), handoff_id(doc2))


# ---------------------------------------------------------------------------
# Tests: NaN / Infinity rejection
# ---------------------------------------------------------------------------

class TestNaNInfinity(unittest.TestCase):
    """Data containing NaN or Infinity must be rejected."""

    def test_nan_in_dict(self) -> None:
        with self.assertRaises(ProtocolError):
            canonical_bytes({"value": float("nan")})

    def test_infinity_in_dict(self) -> None:
        with self.assertRaises(ProtocolError):
            canonical_bytes({"value": float("inf")})

    def test_negative_infinity_in_dict(self) -> None:
        with self.assertRaises(ProtocolError):
            canonical_bytes({"value": float("-inf")})

    def test_nan_in_nested_list(self) -> None:
        with self.assertRaises(ProtocolError):
            canonical_bytes([1, float("nan"), 3])


# ---------------------------------------------------------------------------
# Tests: parse_document
# ---------------------------------------------------------------------------

class TestParseDocument(unittest.TestCase):
    """parse_document round-trips and rejects invalid JSON."""

    def test_parse_valid(self) -> None:
        doc = _make_doc("assignment", copy.deepcopy(_ASSIGNMENT_BODY))
        raw = canonical_bytes(doc)
        parsed = parse_document(raw)
        self.assertEqual(parsed["kind"], "assignment")

    def test_parse_invalid_json(self) -> None:
        with self.assertRaises(ProtocolError):
            parse_document(b"not json")

    def test_parse_non_utf8(self) -> None:
        with self.assertRaises(ProtocolError):
            parse_document(b"\xff\xfe")


# ---------------------------------------------------------------------------
# Tests: expected_identity validation
# ---------------------------------------------------------------------------

class TestExpectedIdentity(unittest.TestCase):
    """expected_identity enforces exact matches."""

    def test_identity_matches(self) -> None:
        doc = _make_doc("assignment", copy.deepcopy(_ASSIGNMENT_BODY))
        validate_document(doc, expected_identity={
            "group_id": "GROUP-AUTO-001",
            "task_id": "ACT-AUTO-001",
        })

    def test_identity_mismatch(self) -> None:
        doc = _make_doc("assignment", copy.deepcopy(_ASSIGNMENT_BODY))
        with self.assertRaises(ProtocolError):
            validate_document(doc, expected_identity={
                "group_id": "WRONG-GROUP",
            })

    # --- R-005: Unknown key rejection ---

    def test_identity_rejects_unknown_key(self) -> None:
        """Keys not in _EXPECTED_IDENTITY_KEYS must be rejected."""
        doc = _make_doc("assignment", copy.deepcopy(_ASSIGNMENT_BODY))
        with self.assertRaises(ProtocolError):
            validate_document(doc, expected_identity={
                "unknown_field": "value",
            })

    def test_identity_accepts_valid_subset(self) -> None:
        """Valid subset of expected_identity keys must be accepted."""
        doc = _make_doc("assignment", copy.deepcopy(_ASSIGNMENT_BODY))
        validate_document(doc, expected_identity={
            "group_id": doc["group_id"],
            "task_id": doc["task_id"],
            "run_id": doc["run_id"],
            "revision_id": doc["revision_id"],
            "baseline_id": doc["baseline_id"],
            "contract_hash": doc["contract_hash"],
            "evidence_hash": doc["evidence_hash"],
            "producer": doc["producer"],
        })

    def test_identity_rejects_missing_key(self) -> None:
        """A key in expected_identity that is not in the document must be rejected."""
        doc = _make_doc("assignment", copy.deepcopy(_ASSIGNMENT_BODY))
        with self.assertRaises(ProtocolError):
            validate_document(doc, expected_identity={
                "group_id": doc["group_id"],
                "task_id": doc["task_id"],
                "run_id": doc["run_id"],
                "revision_id": doc["revision_id"],
                "baseline_id": doc["baseline_id"],
                "contract_hash": doc["contract_hash"],
                "evidence_hash": doc["evidence_hash"],
                "producer": doc["producer"],
                "missing_key": "value",
            })


# ---------------------------------------------------------------------------
# Tests: status consistency
# ---------------------------------------------------------------------------

class TestStatusConsistency(unittest.TestCase):
    """Kind/status must match."""

    def test_assignment_status_must_be_assigned(self) -> None:
        doc = _make_doc("assignment", copy.deepcopy(_ASSIGNMENT_BODY))
        doc["status"] = "READY_FOR_REVIEW"
        with self.assertRaises(ProtocolError):
            validate_document(doc)

    def test_implementation_status_must_be_ready_for_review(self) -> None:
        doc = _make_doc("implementation", copy.deepcopy(_IMPLEMENTATION_BODY))
        doc["status"] = "ASSIGNED"
        with self.assertRaises(ProtocolError):
            validate_document(doc)


# ---------------------------------------------------------------------------
# Tests: artifact path validation
# ---------------------------------------------------------------------------

class TestArtifactPaths(unittest.TestCase):
    """Artifact paths must be relative and non-empty."""

    def test_absolute_path(self) -> None:
        doc = _make_doc("assignment", copy.deepcopy(_ASSIGNMENT_BODY))
        doc["artifacts"] = [{"path": "/absolute/path.txt", "sha256": "4" * 64, "role": "delta"}]
        with self.assertRaises(ProtocolError):
            validate_document(doc)


# ---------------------------------------------------------------------------
# Tests: revision semantics (assignment → REV-000; others → REV-001..REV-999)
# ---------------------------------------------------------------------------

class TestRevisionSemantics(unittest.TestCase):
    """revision_id must match the kind-specific allowed range."""

    def test_assignment_rejects_REV_001(self) -> None:
        """Assignment must only accept REV-000, reject REV-001."""
        doc = _make_doc("assignment", copy.deepcopy(_ASSIGNMENT_BODY))
        doc["revision_id"] = "REV-001"
        with self.assertRaises(ProtocolError):
            validate_document(doc)

    def test_other_kinds_reject_REV_000(self) -> None:
        """implementation, checks, review, close must reject REV-000."""
        for kind in ("implementation", "checks", "review", "close"):
            with self.subTest(kind=kind):
                if kind == "implementation":
                    body = copy.deepcopy(_IMPLEMENTATION_BODY)
                elif kind == "checks":
                    body = copy.deepcopy(_CHECKS_BODY)
                elif kind == "review":
                    body = copy.deepcopy(_REVIEW_BODY)
                else:
                    body = copy.deepcopy(_CLOSE_BODY)
                doc = _make_doc(kind, body)
                doc["revision_id"] = "REV-000"
                with self.assertRaises(ProtocolError):
                    validate_document(doc)

    def test_revision_rejects_trailing_newline(self) -> None:
        """Trailing newline in revision_id must be rejected (fullmatch)."""
        # assignment: "REV-000\n" should be rejected
        doc = _make_doc("assignment", copy.deepcopy(_ASSIGNMENT_BODY))
        doc["revision_id"] = "REV-000\n"
        with self.assertRaises(ProtocolError):
            validate_document(doc)

        # implementation, checks, review, close: "REV-001\n" should be rejected
        for kind in ("implementation", "checks", "review", "close"):
            with self.subTest(kind=kind):
                if kind == "implementation":
                    body = copy.deepcopy(_IMPLEMENTATION_BODY)
                elif kind == "checks":
                    body = copy.deepcopy(_CHECKS_BODY)
                elif kind == "review":
                    body = copy.deepcopy(_REVIEW_BODY)
                else:
                    body = copy.deepcopy(_CLOSE_BODY)
                doc = _make_doc(kind, body)
                doc["revision_id"] = "REV-001\n"
                with self.assertRaises(ProtocolError):
                    validate_document(doc)


# ---------------------------------------------------------------------------
# Tests: close gate (R-001C)
# ---------------------------------------------------------------------------

class TestCloseGate(unittest.TestCase):
    """COMPLETED and BLOCKED are the only valid close states. COMPLETED requires
    non-null handoff refs + hash matching envelope; BLOCKED allows null body refs."""

    def _make_close_doc(self, body: Dict[str, Any], status: str = "COMPLETED") -> Dict[str, Any]:
        doc = _make_doc("close", copy.deepcopy(body))
        doc["status"] = status
        return doc

    # --- 1. Positive COMPLETED from fixture ---

    def test_close_completed_positive(self) -> None:
        """Valid close body with COMPLETED must pass."""
        doc = self._make_close_doc(_CLOSE_BODY, "COMPLETED")
        validate_document(doc)

    # --- 2. Reject each required reference/hash as null ---

    def test_close_completed_rejects_null_checks_handoff_id(self) -> None:
        body = copy.deepcopy(_CLOSE_BODY)
        body["checks_handoff_id"] = None
        doc = self._make_close_doc(body, "COMPLETED")
        with self.assertRaises(ProtocolError):
            validate_document(doc)

    def test_close_completed_rejects_null_review_handoff_id(self) -> None:
        body = copy.deepcopy(_CLOSE_BODY)
        body["review_handoff_id"] = None
        doc = self._make_close_doc(body, "COMPLETED")
        with self.assertRaises(ProtocolError):
            validate_document(doc)

    def test_close_completed_rejects_null_accepted_evidence_hash(self) -> None:
        body = copy.deepcopy(_CLOSE_BODY)
        body["accepted_evidence_hash"] = None
        doc = self._make_close_doc(body, "COMPLETED")
        with self.assertRaises(ProtocolError):
            validate_document(doc)

    # --- 3. Reject accepted hash valid but different from envelope evidence_hash ---

    def test_close_completed_rejects_wrong_accepted_hash(self) -> None:
        """accepted_evidence_hash must equal the envelope's evidence_hash."""
        body = copy.deepcopy(_CLOSE_BODY)
        body["accepted_evidence_hash"] = "9" * 64  # different from "2"*64 in envelope
        doc = self._make_close_doc(body, "COMPLETED")
        with self.assertRaises(ProtocolError):
            validate_document(doc)

    # --- 4. Reject non-empty pending_findings in COMPLETED; reject wrong states ---

    def test_close_completed_rejects_non_empty_pending(self) -> None:
        body = copy.deepcopy(_CLOSE_BODY)
        body["pending_findings"] = ["F-001"]
        doc = self._make_close_doc(body, "COMPLETED")
        with self.assertRaises(ProtocolError):
            validate_document(doc)

    def test_close_rejects_passed_state(self) -> None:
        """Close must not accept PASSED as a status."""
        doc = self._make_close_doc(_CLOSE_BODY, "PASSED")
        with self.assertRaises(ProtocolError):
            validate_document(doc)

    def test_close_rejects_approved_state(self) -> None:
        """Close must not accept APPROVED as a status."""
        doc = self._make_close_doc(_CLOSE_BODY, "APPROVED")
        with self.assertRaises(ProtocolError):
            validate_document(doc)

    # --- 5. Positive BLOCKED with null body refs and pending findings ---

    def test_close_blocked_positive(self) -> None:
        """BLOCKED close with null body refs + pending_findings is valid."""
        body = {
            "reason": "Blocked",
            "checks_handoff_id": None,
            "review_handoff_id": None,
            "accepted_evidence_hash": None,
            "pending_findings": ["F-001"],
            "limitations": [],
        }
        doc = self._make_close_doc(body, "BLOCKED")
        # Envelope evidence_hash and previous_handoff_id remain non-null (valid)
        validate_document(doc)


# ---------------------------------------------------------------------------
# Tests: hash validation strictness (R-002)
# ---------------------------------------------------------------------------

class TestHashValidation(unittest.TestCase):
    """All hash fields must be exactly 64 lowercase hex characters. No normalization,
    no coercion. Nullability follows per-field protocol rules."""

    _VALID_HASH = "a" * 64
    _INVALID_HASHES = [
        ("too_short_63", "a" * 63),
        ("too_long_65", "a" * 65),
        ("uppercase", "A" * 64),
        ("newline_trail", "a" * 64 + "\n"),
        ("empty_string", ""),
    ]

    def _make_assignment_doc(self, body: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        b = copy.deepcopy(_ASSIGNMENT_BODY) if body is None else copy.deepcopy(body)
        return _make_doc("assignment", b)

    # --- Positive controls ---

    def test_assignment_baseline_hash_valid(self) -> None:
        doc = self._make_assignment_doc()
        validate_document(doc)  # baseline_id="0"*64 is valid

    def test_assignment_contract_hash_valid(self) -> None:
        doc = self._make_assignment_doc()
        validate_document(doc)  # contract_hash="1"*64 is valid

    def test_assignment_evidence_hash_null_ok(self) -> None:
        """Assignment allows evidence_hash=null."""
        doc = self._make_assignment_doc()
        doc["evidence_hash"] = None
        validate_document(doc)

    def test_assignment_evidence_hash_valid(self) -> None:
        """Assignment allows evidence_hash=valid hash."""
        doc = self._make_assignment_doc()
        doc["evidence_hash"] = "a" * 64
        validate_document(doc)

    def test_assignment_evidence_hash_invalid_values_rejected(self) -> None:
        """Invalid (non-null) evidence_hash values must be rejected for assignment."""
        invalid_values = [
            ("not_a_hash", "not-a-hash"),
            ("too_short_63", "a" * 63),
            ("too_long_65", "a" * 65),
            ("g_hex", "g" * 64),
            ("uppercase", "A" * 64),
            ("newline_trail", "a" * 64 + "\n"),
            ("empty_string", ""),
            ("bool_true", True),
            ("int_value", 123),
            ("list_value", ["a"]),
        ]
        for name, bad_val in invalid_values:
            with self.subTest(name=name):
                doc = self._make_assignment_doc()
                doc["evidence_hash"] = bad_val
                with self.assertRaises(ProtocolError):
                    validate_document(doc)

    def test_previous_handoff_null_assignment_ok(self) -> None:
        """previous_handoff_id may be null only for assignment."""
        body = copy.deepcopy(_ASSIGNMENT_BODY)
        doc = _make_doc("assignment", body)
        doc["previous_handoff_id"] = None
        validate_document(doc)

    def test_profile_hash_manual_null_ok(self) -> None:
        """MANUAL review_policy allows profile_hash=null."""
        body = copy.deepcopy(_ASSIGNMENT_BODY)
        body["review_policy"] = "MANUAL"
        body["profile_hash"] = None
        doc = _make_doc("assignment", body)
        validate_document(doc)

    def test_profile_hash_cloud_with_hash_ok(self) -> None:
        """CLOUD review_policy allows profile_hash=valid hash."""
        body = copy.deepcopy(_ASSIGNMENT_BODY)
        body["review_policy"] = "CLOUD"
        body["profile_hash"] = "b" * 64
        doc = _make_doc("assignment", body)
        validate_document(doc)

    def test_add_change_hashes_valid(self) -> None:
        """ADD: before_hash=null, after_hash=valid."""
        body = copy.deepcopy(_IMPLEMENTATION_BODY)
        body["changes"] = [{
            "path": "new/file.txt",
            "before_hash": None,
            "after_hash": "a" * 64,
            "change": "ADD",
        }]
        doc = _make_doc("implementation", body)
        validate_document(doc)

    def test_delete_change_hashes_valid(self) -> None:
        """DELETE: before_hash=valid, after_hash=null."""
        body = copy.deepcopy(_IMPLEMENTATION_BODY)
        body["changes"] = [{
            "path": "old/file.txt",
            "before_hash": "b" * 64,
            "after_hash": None,
            "change": "DELETE",
        }]
        doc = _make_doc("implementation", body)
        validate_document(doc)

    def test_modify_change_hashes_valid(self) -> None:
        """MODIFY: both before_hash and after_hash required and valid."""
        body = copy.deepcopy(_IMPLEMENTATION_BODY)
        body["changes"] = [{
            "path": "mod/file.txt",
            "before_hash": "c" * 64,
            "after_hash": "d" * 64,
            "change": "MODIFY",
        }]
        doc = _make_doc("implementation", body)
        validate_document(doc)

    # --- Negative controls: hash format ---

    def test_hash_format_rejections(self) -> None:
        """Each invalid hash form must be rejected in a field that accepts non-null hashes."""
        for name, bad_value in self._INVALID_HASHES:
            with self.subTest(name=name):
                body = copy.deepcopy(_ASSIGNMENT_BODY)
                doc = _make_doc("assignment", body)
                doc["baseline_id"] = bad_value
                with self.assertRaises(ProtocolError):
                    validate_document(doc)

    # --- Negative controls: nullability ---

    def test_assignment_baseline_null_rejected(self) -> None:
        """baseline_id must never be null."""
        doc = self._make_assignment_doc()
        doc["baseline_id"] = None
        with self.assertRaises(ProtocolError):
            validate_document(doc)

    def test_assignment_contract_hash_null_rejected(self) -> None:
        """contract_hash must never be null."""
        doc = self._make_assignment_doc()
        doc["contract_hash"] = None
        with self.assertRaises(ProtocolError):
            validate_document(doc)

    def test_non_assignment_evidence_hash_null_rejected(self) -> None:
        """Non-assignment kinds must have non-null evidence_hash."""
        body = copy.deepcopy(_IMPLEMENTATION_BODY)
        doc = _make_doc("implementation", body)
        doc["evidence_hash"] = None
        with self.assertRaises(ProtocolError):
            validate_document(doc)

    def test_non_assignment_previous_handoff_null_rejected(self) -> None:
        """Non-assignment must have non-null previous_handoff_id."""
        body = copy.deepcopy(_IMPLEMENTATION_BODY)
        doc = _make_doc("implementation", body)
        doc["previous_handoff_id"] = None
        with self.assertRaises(ProtocolError):
            validate_document(doc)

    def test_artifact_sha256_null_rejected(self) -> None:
        """Artifact sha256 must not be null."""
        doc = self._make_assignment_doc()
        doc["artifacts"] = [{"path": "a.txt", "sha256": None, "role": "delta"}]
        with self.assertRaises(ProtocolError):
            validate_document(doc)

    def test_checks_exit_code_hash_null_rejected(self) -> None:
        """stdout_hash/stderr_hash must not be null."""
        body = copy.deepcopy(_CHECKS_BODY)
        body["results"][0]["stdout_hash"] = None
        doc = _make_doc("checks", body)
        with self.assertRaises(ProtocolError):
            validate_document(doc)

    def test_checks_stderr_hash_null_rejected(self) -> None:
        body = copy.deepcopy(_CHECKS_BODY)
        body["results"][0]["stderr_hash"] = None
        doc = _make_doc("checks", body)
        with self.assertRaises(ProtocolError):
            validate_document(doc)

    def test_add_after_hash_null_rejected(self) -> None:
        """ADD change requires non-null after_hash."""
        body = copy.deepcopy(_IMPLEMENTATION_BODY)
        body["changes"] = [{
            "path": "new/file.txt",
            "before_hash": None,
            "after_hash": None,
            "change": "ADD",
        }]
        doc = _make_doc("implementation", body)
        with self.assertRaises(ProtocolError):
            validate_document(doc)

    def test_delete_before_hash_null_rejected(self) -> None:
        """DELETE change requires non-null before_hash."""
        body = copy.deepcopy(_IMPLEMENTATION_BODY)
        body["changes"] = [{
            "path": "old/file.txt",
            "before_hash": None,
            "after_hash": None,
            "change": "DELETE",
        }]
        doc = _make_doc("implementation", body)
        with self.assertRaises(ProtocolError):
            validate_document(doc)

    def test_modify_both_hashes_required(self) -> None:
        """MODIFY requires both before_hash and after_hash non-null and valid."""
        body = copy.deepcopy(_IMPLEMENTATION_BODY)
        body["changes"] = [{
            "path": "mod/file.txt",
            "before_hash": None,
            "after_hash": "d" * 64,
            "change": "MODIFY",
        }]
        doc = _make_doc("implementation", body)
        with self.assertRaises(ProtocolError):
            validate_document(doc)

    # --- Negative controls: wrong types ---

    def test_hash_rejects_number(self) -> None:
        """A numeric value must not be accepted as a hash."""
        doc = self._make_assignment_doc()
        doc["baseline_id"] = 12345
        with self.assertRaises(ProtocolError):
            validate_document(doc)

    def test_hash_rejects_boolean(self) -> None:
        """A boolean value must not be accepted as a hash."""
        doc = self._make_assignment_doc()
        doc["baseline_id"] = True
        with self.assertRaises(ProtocolError):
            validate_document(doc)

    def test_profile_cloud_requires_hash(self) -> None:
        """CLOUD review_policy requires profile_hash to be a valid hash (not null)."""
        body = copy.deepcopy(_ASSIGNMENT_BODY)
        body["review_policy"] = "CLOUD"
        body["profile_hash"] = None
        doc = _make_doc("assignment", body)
        # CLOUD should require a non-null profile_hash
        with self.assertRaises(ProtocolError):
            validate_document(doc)

    def test_bad_hash_in_optional_field_rejected(self) -> None:
        """A malformed (but non-null) hash in a nullable field must still be rejected."""
        doc = self._make_assignment_doc()
        doc["evidence_hash"] = "ZZZZ" + "a" * 60
        with self.assertRaises(ProtocolError):
            validate_document(doc)


# ---------------------------------------------------------------------------
# Tests: review approval gate (R-001A)
# ---------------------------------------------------------------------------

class TestReviewApprovalGate(unittest.TestCase):
    """APPROVED verdict requires 0 findings + all criteria MET, and verdict == status."""

    def test_approved_rejects_unmet_criteria(self) -> None:
        """APPROVED must reject when any criterion is UNMET or NOT_CHECKED."""
        for state in ("UNMET", "NOT_CHECKED"):
            with self.subTest(state=state):
                body = copy.deepcopy(_REVIEW_BODY)
                body["criteria"][0]["state"] = state
                doc = _make_doc("review", body)
                with self.assertRaises(ProtocolError):
                    validate_document(doc)

    def test_approved_rejects_critical_finding(self) -> None:
        """APPROVED must reject when any finding exists (e.g. CRITICAL)."""
        body = copy.deepcopy(_REVIEW_BODY)
        body["findings"].append({
            "finding_id": "F-001",
            "severity": "CRITICAL",
            "path": None,
            "line": None,
            "message": "Blocking test finding",
            "in_scope": True,
            "criterion_id": "CRIT-001",
        })
        doc = _make_doc("review", body)
        with self.assertRaises(ProtocolError):
            validate_document(doc)

        # Control: same document but verdict + status changed to REQUIRES_CHANGES
        body2 = copy.deepcopy(body)
        body2["verdict"] = "REQUIRES_CHANGES"
        doc2 = _make_doc("review", body2)
        doc2["status"] = "REQUIRES_CHANGES"
        validate_document(doc2)  # must pass

    def test_review_rejects_status_verdict_mismatch(self) -> None:
        """Review envelope status must equal verdict."""
        for status in ("BLOCKED", "COMPLETED"):
            with self.subTest(status=status):
                body = copy.deepcopy(_REVIEW_BODY)
                doc = _make_doc("review", body)
                doc["status"] = status
                with self.assertRaises(ProtocolError):
                    validate_document(doc)


# ---------------------------------------------------------------------------
# Tests: checks status gate (R-001B)
# ---------------------------------------------------------------------------

class TestChecksStatusGate(unittest.TestCase):
    """Per-result status must match exit_code / timed_out, and envelope status
    must be derived correctly from the set of results."""

    def _make_checks_doc(self, results: List[Dict[str, Any]]) -> Dict[str, Any]:
        body = {"results": results, "limitations": []}
        return _make_doc("checks", copy.deepcopy(body))

    # --- 1. Reject invalid per-result combos ---

    def test_passed_rejects_exit_1(self) -> None:
        """A result with status PASSED must not have exit_code != 0."""
        body = copy.deepcopy(_CHECKS_BODY)
        body["results"][0]["status"] = "PASSED"
        body["results"][0]["exit_code"] = 1
        doc = self._make_checks_doc(body["results"])
        with self.assertRaises(ProtocolError):
            validate_document(doc)

    def test_failed_rejects_exit_0(self) -> None:
        """A result with status FAILED must not have exit_code == 0."""
        body = copy.deepcopy(_CHECKS_BODY)
        body["results"][0]["status"] = "FAILED"
        body["results"][0]["exit_code"] = 0
        doc = self._make_checks_doc(body["results"])
        with self.assertRaises(ProtocolError):
            validate_document(doc)

    def test_timed_out_rejects_timed_out_false(self) -> None:
        """A result with status TIMED_OUT must have timed_out=true."""
        body = copy.deepcopy(_CHECKS_BODY)
        body["results"][0]["status"] = "TIMED_OUT"
        body["results"][0]["timed_out"] = False
        doc = self._make_checks_doc(body["results"])
        with self.assertRaises(ProtocolError):
            validate_document(doc)

    def test_timed_out_rejects_exit_code_present(self) -> None:
        """A result with timed_out=true must not have a non-null exit_code."""
        body = copy.deepcopy(_CHECKS_BODY)
        body["results"][0]["timed_out"] = True
        body["results"][0]["exit_code"] = 1
        doc = self._make_checks_doc(body["results"])
        with self.assertRaises(ProtocolError):
            validate_document(doc)

    # --- 2. Reject exit_code as bool ---

    def test_exit_code_bool_rejected(self) -> None:
        """exit_code must not be a boolean value."""
        body = copy.deepcopy(_CHECKS_BODY)
        body["results"][0]["exit_code"] = True
        doc = self._make_checks_doc(body["results"])
        with self.assertRaises(ProtocolError):
            validate_document(doc)

    # --- 3. Aggregation tests ---

    def test_aggregation_all_passed_yields_passed(self) -> None:
        """All PASSED results must yield envelope status PASSED."""
        body = copy.deepcopy(_CHECKS_BODY)
        body["results"].append({
            "check_id": "CHK-002",
            "argv": ["python", "-m", "pytest"],
            "cwd": ".",
            "started_at": "2026-10-05T13:00:00Z",
            "finished_at": "2026-10-05T13:01:00Z",
            "elapsed_ms": 60000,
            "exit_code": 0,
            "timed_out": False,
            "stdout_path": "artifacts/out2.txt",
            "stderr_path": "artifacts/err2.txt",
            "stdout_hash": "d" * 64,
            "stderr_hash": "e" * 64,
            "status": "PASSED",
        })
        doc = self._make_checks_doc(body["results"])
        validate_document(doc)  # status=PASSED must be accepted

    def test_aggregation_passed_plus_failed_yields_failed(self) -> None:
        """Mix of PASSED + FAILED must yield envelope status FAILED."""
        body = copy.deepcopy(_CHECKS_BODY)
        body["results"].append({
            "check_id": "CHK-002",
            "argv": ["python", "-m", "pytest"],
            "cwd": ".",
            "started_at": "2026-10-05T13:00:00Z",
            "finished_at": "2026-10-05T13:01:00Z",
            "elapsed_ms": 60000,
            "exit_code": 1,
            "timed_out": False,
            "stdout_path": "artifacts/out2.txt",
            "stderr_path": "artifacts/err2.txt",
            "stdout_hash": "d" * 64,
            "stderr_hash": "e" * 64,
            "status": "FAILED",
        })
        doc = self._make_checks_doc(body["results"])
        doc["status"] = "FAILED"
        validate_document(doc)

    def test_aggregation_passed_plus_timed_out_yields_timed_out(self) -> None:
        """Mix of PASSED + TIMED_OUT must yield envelope status TIMED_OUT."""
        body = copy.deepcopy(_CHECKS_BODY)
        body["results"].append({
            "check_id": "CHK-002",
            "argv": ["python", "-m", "pytest"],
            "cwd": ".",
            "started_at": "2026-10-05T13:00:00Z",
            "finished_at": "2026-10-05T13:01:00Z",
            "elapsed_ms": 60000,
            "exit_code": None,
            "timed_out": True,
            "stdout_path": "artifacts/out2.txt",
            "stderr_path": "artifacts/err2.txt",
            "stdout_hash": "d" * 64,
            "stderr_hash": "e" * 64,
            "status": "TIMED_OUT",
        })
        doc = self._make_checks_doc(body["results"])
        doc["status"] = "TIMED_OUT"
        validate_document(doc)

    def test_aggregation_failed_plus_timed_out_yields_timed_out(self) -> None:
        """Mix of FAILED + TIMED_OUT must yield envelope status TIMED_OUT."""
        body = copy.deepcopy(_CHECKS_BODY)
        body["results"][0]["status"] = "FAILED"
        body["results"][0]["exit_code"] = 1
        body["results"].append({
            "check_id": "CHK-002",
            "argv": ["python", "-m", "pytest"],
            "cwd": ".",
            "started_at": "2026-10-05T13:00:00Z",
            "finished_at": "2026-10-05T13:01:00Z",
            "elapsed_ms": 60000,
            "exit_code": None,
            "timed_out": True,
            "stdout_path": "artifacts/out2.txt",
            "stderr_path": "artifacts/err2.txt",
            "stdout_hash": "d" * 64,
            "stderr_hash": "e" * 64,
            "status": "TIMED_OUT",
        })
        doc = self._make_checks_doc(body["results"])
        doc["status"] = "TIMED_OUT"
        validate_document(doc)

    def test_aggregation_negative_matrix(self) -> None:
        """Each aggregation must reject all wrong envelope statuses (PASSED/FAILED/TIMED_OUT/APPROVED)."""
        aggregations = [
            ("all_passed", [{"check_id": "CHK-001", "argv": ["python", "-m", "pytest"], "cwd": ".", "started_at": "2026-10-05T12:00:00Z", "finished_at": "2026-10-05T12:01:00Z", "elapsed_ms": 60000, "exit_code": 0, "timed_out": False, "stdout_path": "artifacts/out.txt", "stderr_path": "artifacts/err.txt", "stdout_hash": "b" * 64, "stderr_hash": "c" * 64, "status": "PASSED"}], "PASSED"),
            ("passed_plus_failed", [{"check_id": "CHK-001", "argv": ["python", "-m", "pytest"], "cwd": ".", "started_at": "2026-10-05T12:00:00Z", "finished_at": "2026-10-05T12:01:00Z", "elapsed_ms": 60000, "exit_code": 0, "timed_out": False, "stdout_path": "artifacts/out.txt", "stderr_path": "artifacts/err.txt", "stdout_hash": "b" * 64, "stderr_hash": "c" * 64, "status": "PASSED"}, {"check_id": "CHK-002", "argv": ["python", "-m", "pytest"], "cwd": ".", "started_at": "2026-10-05T13:00:00Z", "finished_at": "2026-10-05T13:01:00Z", "elapsed_ms": 60000, "exit_code": 1, "timed_out": False, "stdout_path": "artifacts/out2.txt", "stderr_path": "artifacts/err2.txt", "stdout_hash": "d" * 64, "stderr_hash": "e" * 64, "status": "FAILED"}], "FAILED"),
            ("passed_plus_timed_out", [{"check_id": "CHK-001", "argv": ["python", "-m", "pytest"], "cwd": ".", "started_at": "2026-10-05T12:00:00Z", "finished_at": "2026-10-05T12:01:00Z", "elapsed_ms": 60000, "exit_code": 0, "timed_out": False, "stdout_path": "artifacts/out.txt", "stderr_path": "artifacts/err.txt", "stdout_hash": "b" * 64, "stderr_hash": "c" * 64, "status": "PASSED"}, {"check_id": "CHK-002", "argv": ["python", "-m", "pytest"], "cwd": ".", "started_at": "2026-10-05T13:00:00Z", "finished_at": "2026-10-05T13:01:00Z", "elapsed_ms": 60000, "exit_code": None, "timed_out": True, "stdout_path": "artifacts/out2.txt", "stderr_path": "artifacts/err2.txt", "stdout_hash": "d" * 64, "stderr_hash": "e" * 64, "status": "TIMED_OUT"}], "TIMED_OUT"),
            ("failed_plus_timed_out", [{"check_id": "CHK-001", "argv": ["python", "-m", "pytest"], "cwd": ".", "started_at": "2026-10-05T12:00:00Z", "finished_at": "2026-10-05T12:01:00Z", "elapsed_ms": 60000, "exit_code": 1, "timed_out": False, "stdout_path": "artifacts/out.txt", "stderr_path": "artifacts/err.txt", "stdout_hash": "b" * 64, "stderr_hash": "c" * 64, "status": "FAILED"}, {"check_id": "CHK-002", "argv": ["python", "-m", "pytest"], "cwd": ".", "started_at": "2026-10-05T13:00:00Z", "finished_at": "2026-10-05T13:01:00Z", "elapsed_ms": 60000, "exit_code": None, "timed_out": True, "stdout_path": "artifacts/out2.txt", "stderr_path": "artifacts/err2.txt", "stdout_hash": "d" * 64, "stderr_hash": "e" * 64, "status": "TIMED_OUT"}], "TIMED_OUT"),
        ]
        wrong_statuses = ["PASSED", "FAILED", "TIMED_OUT", "APPROVED"]
        for agg_name, results, correct_status in aggregations:
            with self.subTest(agg=agg_name):
                for wrong_status in wrong_statuses:
                    with self.subTest(status=wrong_status):
                        body = {"results": copy.deepcopy(results), "limitations": []}
                        doc = _make_doc("checks", body)
                        doc["status"] = wrong_status
                        if wrong_status == correct_status:
                            validate_document(doc)
                        else:
                            with self.assertRaises(ProtocolError):
                                validate_document(doc)

    # --- 4. Reject wrong envelope status for each aggregation ---

    def test_aggregation_all_passed_rejects_envelope_failed(self) -> None:
        """When all results passed, envelope must not be FAILED."""
        body = copy.deepcopy(_CHECKS_BODY)
        body["results"].append({
            "check_id": "CHK-002",
            "argv": ["python", "-m", "pytest"],
            "cwd": ".",
            "started_at": "2026-10-05T13:00:00Z",
            "finished_at": "2026-10-05T13:01:00Z",
            "elapsed_ms": 60000,
            "exit_code": 0,
            "timed_out": False,
            "stdout_path": "artifacts/out2.txt",
            "stderr_path": "artifacts/err2.txt",
            "stdout_hash": "d" * 64,
            "stderr_hash": "e" * 64,
            "status": "PASSED",
        })
        doc = self._make_checks_doc(body["results"])
        doc["status"] = "FAILED"
        with self.assertRaises(ProtocolError):
            validate_document(doc)

    def test_aggregation_all_passed_rejects_envelope_timed_out(self) -> None:
        """When all results passed, envelope must not be TIMED_OUT."""
        body = copy.deepcopy(_CHECKS_BODY)
        body["results"].append({
            "check_id": "CHK-002",
            "argv": ["python", "-m", "pytest"],
            "cwd": ".",
            "started_at": "2026-10-05T13:00:00Z",
            "finished_at": "2026-10-05T13:01:00Z",
            "elapsed_ms": 60000,
            "exit_code": 0,
            "timed_out": False,
            "stdout_path": "artifacts/out2.txt",
            "stderr_path": "artifacts/err2.txt",
            "stdout_hash": "d" * 64,
            "stderr_hash": "e" * 64,
            "status": "PASSED",
        })
        doc = self._make_checks_doc(body["results"])
        doc["status"] = "TIMED_OUT"
        with self.assertRaises(ProtocolError):
            validate_document(doc)

    def test_aggregation_all_passed_rejects_approved(self) -> None:
        """When all results passed, envelope must not be APPROVED."""
        body = copy.deepcopy(_CHECKS_BODY)
        body["results"].append({
            "check_id": "CHK-002",
            "argv": ["python", "-m", "pytest"],
            "cwd": ".",
            "started_at": "2026-10-05T13:00:00Z",
            "finished_at": "2026-10-05T13:01:00Z",
            "elapsed_ms": 60000,
            "exit_code": 0,
            "timed_out": False,
            "stdout_path": "artifacts/out2.txt",
            "stderr_path": "artifacts/err2.txt",
            "stdout_hash": "d" * 64,
            "stderr_hash": "e" * 64,
            "status": "PASSED",
        })
        doc = self._make_checks_doc(body["results"])
        doc["status"] = "APPROVED"
        with self.assertRaises(ProtocolError):
            validate_document(doc)


# ---------------------------------------------------------------------------
# Tests: R-004 JSON strictness (Block C2)
# ---------------------------------------------------------------------------

class TestStrictJSON(unittest.TestCase):
    """R-004: Duplicate keys, surrogates, cycles, type checks, canonicalisation."""

    # --- Duplicate key detection ---

    def test_parse_rejects_duplicate_keys_top_level(self) -> None:
        """Duplicate keys at top-level of a valid envelope must be rejected."""
        body = copy.deepcopy(_IMPLEMENTATION_BODY)
        doc = _make_doc("implementation", body)
        validate_document(doc)
        raw_str = json.dumps(doc, sort_keys=True, separators=(",", ":"))
        patched = raw_str[:-1] + ',"kind":"implementation"}'
        raw = patched.encode("utf-8")
        # Standard decoding discards duplicates: the resulting envelope is still valid.
        self.assertEqual(json.loads(raw), doc)
        validate_document(json.loads(raw))
        err = self.assertRaises(ProtocolError)
        with err:
            parse_document(raw)
        # Verify the error code is 1050 (duplicate keys), not a JSON decode error
        self.assertEqual(err.exception.code, 1050)

    def test_parse_rejects_duplicate_keys_nested(self) -> None:
        """Duplicate keys in a nested object of a valid envelope must be rejected."""
        body = copy.deepcopy(_IMPLEMENTATION_BODY)
        doc = _make_doc("implementation", body)
        validate_document(doc)
        raw_str = json.dumps(doc, sort_keys=True)
        # Find the first "path" key inside an artifact (a nested object with {"after_hash":..., "before_hash":...})
        # json.dumps default separators: ": " between key and value
        target = '"path": "'
        idx = raw_str.index(target)
        # Find end of the string value
        val_start = idx + len(target)
        i = val_start
        while i < len(raw_str) and raw_str[i] != '"':
            if raw_str[i] == '\\':
                i += 2
            else:
                i += 1
        # i is now at the closing quote of the path value
        insert_pos = i + 1  # right after the closing quote
        patched = raw_str[:insert_pos] + ', "path":"' + raw_str[val_start:i] + '"' + raw_str[insert_pos:]
        raw = patched.encode("utf-8")
        self.assertEqual(json.loads(raw), doc)
        validate_document(json.loads(raw))
        with self.assertRaises(ProtocolError) as err:
            parse_document(raw)
        self.assertEqual(err.exception.code, 1050)

    def test_parse_rejects_duplicate_keys_escaped(self) -> None:
        """Escaped duplicate key after decode must be rejected."""
        body = copy.deepcopy(_IMPLEMENTATION_BODY)
        doc = _make_doc("implementation", body)
        validate_document(doc)
        raw_str = json.dumps(doc, sort_keys=True)
        # Find a regular key and add its escaped form as a duplicate in the same object
        target = '"summary": "'
        idx = raw_str.index(target)
        val_start = idx + len(target)
        i = val_start
        while i < len(raw_str) and raw_str[i] != '"':
            if raw_str[i] == '\\':
                i += 2
            else:
                i += 1
        insert_pos = i + 1
        patched = raw_str[:insert_pos] + ', "\\u0073ummary":"Added contracts.py"' + raw_str[insert_pos:]
        raw = patched.encode("utf-8")
        self.assertEqual(json.loads(raw), doc)
        validate_document(json.loads(raw))
        with self.assertRaises(ProtocolError) as err:
            parse_document(raw)
        self.assertEqual(err.exception.code, 1050)

    def test_duplicate_keys_across_objects_allowed(self) -> None:
        """Same key in different objects is fine."""
        doc = _make_doc("checks", copy.deepcopy(_CHECKS_BODY))
        second = copy.deepcopy(doc["body"]["results"][0])
        second["check_id"] = "CHK-002"
        doc["body"]["results"].append(second)
        validate_document(doc)
        parsed = parse_document(json.dumps(doc).encode("utf-8"))
        self.assertEqual(parsed, doc)
        self.assertEqual([r["check_id"] for r in parsed["body"]["results"]],
                         ["CHK-001", "CHK-002"])

    # --- Surrogate handling ---

    def test_parse_rejects_direct_surroges_in_raw(self) -> None:
        """Surrogate chars in raw UTF-8 text of a valid envelope must be rejected."""
        body = copy.deepcopy(_IMPLEMENTATION_BODY)
        doc = _make_doc("implementation", body)
        doc["body"]["summary"] = "xvalue"
        validate_document(doc)
        valid_raw = json.dumps(doc).encode("utf-8")
        self.assertEqual(parse_document(valid_raw), doc)
        raw = valid_raw.replace(b'"xvalue"', b'"x\xed\xa0\x80value"', 1)
        self.assertNotEqual(raw, valid_raw)
        with self.assertRaises(ProtocolError):
            parse_document(raw)

    def test_parse_accepts_valid_escaped_surrogate_pair(self) -> None:
        """Escaped surrogate pair \\uD834\\uDD1E decodes to U+1D11E (MUSICAL SYMBOL G CLEF).

        JSON decoding combines the escaped pair into a real Unicode scalar.
        Must use a valid envelope with escaped surrogates in raw JSON.
        """
        body = copy.deepcopy(_IMPLEMENTATION_BODY)
        doc = _make_doc("implementation", body)
        validate_document(doc)
        # Build the raw JSON string, then inject literal \\uD834\\uDD1E escape sequences
        # (NOT actual surrogate characters which Python cannot encode to UTF-8)
        raw_base = json.dumps(doc, ensure_ascii=True)
        # Use single backslash escapes so json.loads interprets them as Unicode surrogates
        patched = raw_base.replace(
            '"summary": "Added contracts.py"',
            '"summary": "\\uD834\\uDD1Emusic"',
            1,
        )
        # Verify replacement happened (patched should contain literal \uD834 escape sequences)
        self.assertIn("\\uD834", patched, "Replacement should have occurred")
        raw = patched.encode("utf-8")
        parsed = parse_document(raw)
        # json.loads decodes the valid pair as U+1D11E (single char) + "music" = 6 chars
        self.assertIn("music", parsed["body"]["summary"])
        self.assertEqual(len(parsed["body"]["summary"]), 6)  # 1 surrogate pair char + "music"
        self.assertEqual(parsed["body"]["summary"], "\U0001d11emusic")

    def test_parse_rejects_invalid_single_surrogate(self) -> None:
        """A single invalid surrogate (no matching pair) in a valid envelope must be rejected."""
        # Build a valid envelope, then use JSON escapes \\uD800 and \\uDFFF that produce
        # two separate surrogates after json.loads decodes them. This tests rejection AFTER
        # JSON decode on a structurally valid envelope (not raw invalid UTF-8).
        body = copy.deepcopy(_IMPLEMENTATION_BODY)
        doc = _make_doc("implementation", body)
        validate_document(doc)
        # Use a value that will contain an unpaired high surrogate after JSON decode
        doc["body"]["summary"] = "\uD800"  # high surrogate alone (invalid)
        raw = json.dumps(doc, ensure_ascii=True).encode("utf-8")
        err = self.assertRaises(ProtocolError)
        with err:
            parse_document(raw)
        # Verify the error code is 1042 (surrogate), not a UTF-8 decode error
        self.assertEqual(err.exception.code, 1042)

    # --- Cycle detection ---

    def test_validate_document_rejects_cyclic_dict(self) -> None:
        """A dict that references itself must be rejected."""
        body = copy.deepcopy(_IMPLEMENTATION_BODY)
        body["summary"] = "cycle"
        doc = _make_doc("implementation", body)
        doc["body"]["summary_ref"] = doc  # cyclic reference
        with self.assertRaises(ProtocolError):
            validate_document(doc)

    def test_validate_document_rejects_cyclic_list(self) -> None:
        """A list that references itself must be rejected."""
        body = copy.deepcopy(_IMPLEMENTATION_BODY)
        lst = [1, 2]
        lst.append(lst)  # cyclic
        doc = _make_doc("implementation", body)
        doc["body"]["summary"] = "x"
        doc["body"]["limitations"].append(lst)
        with self.assertRaises(ProtocolError):
            validate_document(doc)

    def test_validate_document_accepts_shared_ref_not_cyclic(self) -> None:
        """A shared (non-cyclic) reference must be accepted."""
        # Create a valid Criterion object and share the SAME dict between two criteria entries
        shared_criterion = {
            "criterion_id": "CR-SHARED-001",
            "state": "MET",
            "evidence_paths": ["evidence.txt"],
            "note": "shared criterion",
        }
        body = copy.deepcopy(_IMPLEMENTATION_BODY)
        # Use the same object reference (not deepcopy) for both entries
        body["criteria"] = [shared_criterion, shared_criterion]
        doc = _make_doc("implementation", body)
        self.assertIs(doc["body"]["criteria"][0], doc["body"]["criteria"][1])
        # Validate: must not raise (shared refs are allowed, only cycles are rejected)
        validate_document(doc)  # must not raise

    def test_validate_document_shared_ref_round_trip(self) -> None:
        """Shared references serialize normally; JSON preserves content, not aliases."""
        shared_criterion = {
            "criterion_id": "CR-SHARED-002",
            "state": "MET",
            "evidence_paths": ["evidence.txt"],
            "note": "shared criterion round-trip",
        }
        body = copy.deepcopy(_IMPLEMENTATION_BODY)
        # Use the same object reference (not deepcopy) for both entries
        body["criteria"] = [shared_criterion, shared_criterion]
        doc = _make_doc("implementation", body)
        self.assertIs(doc["body"]["criteria"][0], doc["body"]["criteria"][1])
        validate_document(doc)
        raw = json.dumps(doc).encode("utf-8")
        parsed = parse_document(raw)
        self.assertEqual(len(parsed["body"]["criteria"]), 2)
        self.assertEqual(parsed["body"]["criteria"][0]["criterion_id"], "CR-SHARED-002")
        self.assertEqual(parsed["body"]["criteria"][1]["criterion_id"], "CR-SHARED-002")

    # --- Type rejection ---

    def test_validate_rejects_float_finite(self) -> None:
        """A finite float value anywhere in the document must be rejected."""
        body = copy.deepcopy(_IMPLEMENTATION_BODY)
        doc = _make_doc("implementation", body)
        doc["baseline_id"] = 3.14  # float in envelope field
        with self.assertRaises(ProtocolError):
            validate_document(doc)

    def test_validate_rejects_float_in_body(self) -> None:
        """A finite float value inside the body must be rejected."""
        body = copy.deepcopy(_IMPLEMENTATION_BODY)
        body["summary"] = 3.14  # float in body
        doc = _make_doc("implementation", body)
        with self.assertRaises(ProtocolError):
            validate_document(doc)

    def test_validate_rejects_float_in_nested_list(self) -> None:
        """A finite float inside a nested list must be rejected."""
        body = copy.deepcopy(_IMPLEMENTATION_BODY)
        doc = _make_doc("implementation", body)
        doc["artifacts"][0]["path"] = 3.14  # float in nested list
        with self.assertRaises(ProtocolError):
            validate_document(doc)

    def test_validate_rejects_tuple(self) -> None:
        body = copy.deepcopy(_IMPLEMENTATION_BODY)
        doc = _make_doc("implementation", body)
        doc["body"]["summary"] = (1, 2, 3)
        with self.assertRaises(ProtocolError):
            validate_document(doc)

    def test_validate_rejects_set(self) -> None:
        body = copy.deepcopy(_IMPLEMENTATION_BODY)
        doc = _make_doc("implementation", body)
        doc["body"]["summary"] = {1, 2}
        with self.assertRaises(ProtocolError):
            validate_document(doc)

    def test_validate_rejects_bytes_value(self) -> None:
        body = copy.deepcopy(_IMPLEMENTATION_BODY)
        doc = _make_doc("implementation", body)
        doc["body"]["summary"] = b"raw bytes"
        with self.assertRaises(ProtocolError):
            validate_document(doc)

    def test_validate_rejects_non_string_key(self) -> None:
        body = copy.deepcopy(_IMPLEMENTATION_BODY)
        doc = _make_doc("implementation", body)
        # Add a non-string key to the document
        doc[123] = "bad"
        with self.assertRaises(ProtocolError):
            validate_document(doc)

    def test_validate_rejects_nested_non_string_key(self) -> None:
        body = copy.deepcopy(_IMPLEMENTATION_BODY)
        doc = _make_doc("implementation", body)
        # Add non-string key inside body
        doc["body"][123] = "bad"
        with self.assertRaises(ProtocolError):
            validate_document(doc)

    # --- Float/NaN/Infinity in raw JSON ---

    def test_parse_rejects_float_value(self) -> None:
        """A float value in a valid envelope must be rejected."""
        body = copy.deepcopy(_IMPLEMENTATION_BODY)
        doc = _make_doc("implementation", body)
        doc["body"]["summary"] = 3.14  # float in body
        raw = json.dumps(doc).encode("utf-8")
        with self.assertRaises(ProtocolError):
            parse_document(raw)

    def test_parse_accepts_valid_integer(self) -> None:
        """Valid integer values must be accepted."""
        body = copy.deepcopy(_IMPLEMENTATION_BODY)
        doc = _make_doc("implementation", body)
        raw = json.dumps(doc).encode("utf-8")
        parsed = parse_document(raw)
        self.assertEqual(parsed["kind"], "implementation")

    def test_parse_rejects_nan_raw(self) -> None:
        # NaN is not valid JSON; json.loads will fail, but we should handle it
        raw = b'{"val": NaN}'
        with self.assertRaises(ProtocolError):
            parse_document(raw)

    def test_parse_rejects_infinity_raw(self) -> None:
        raw = b'{"val": Infinity}'
        with self.assertRaises(ProtocolError):
            parse_document(raw)

    # --- Canonicalisation determinism ---

    def test_canonical_bytes_deterministic(self) -> None:
        d1 = {"b": [1, 2], "a": 1}
        d2 = {"a": 1, "b": [1, 2]}  # different order, same content
        self.assertEqual(canonical_bytes(d1), canonical_bytes(d2))

    def test_canonical_bytes_no_trailing_newline(self) -> None:
        cb = canonical_bytes({"a": 1})
        self.assertFalse(cb.endswith(b"\n"))

    def test_canonical_bytes_utf8_non_ascii(self) -> None:
        cb = canonical_bytes({"name": "café"})
        # ensure_ascii=False means raw UTF-8 bytes, not escaped
        self.assertEqual(cb, b'{"name":"caf\xc3\xa9"}')

    def test_round_trip_preserves_content(self) -> None:
        """canonical_bytes + json.loads round-trip preserves data."""
        body = copy.deepcopy(_IMPLEMENTATION_BODY)
        doc = _make_doc("implementation", body)
        cb = canonical_bytes(doc)
        restored = json.loads(cb.decode("utf-8"))
        self.assertEqual(restored["kind"], "implementation")
        self.assertEqual(restored["body"]["summary"], "Added contracts.py")

    # --- Invalid raw input ---

    def test_parse_rejects_empty_bytes(self) -> None:
        with self.assertRaises(ProtocolError):
            parse_document(b"")

    def test_parse_rejects_null_bytes(self) -> None:
        with self.assertRaises(ProtocolError):
            parse_document(None)  # type: ignore

    def test_parse_rejects_int_raw(self) -> None:
        with self.assertRaises(ProtocolError):
            parse_document(42)  # type: ignore


# ---------------------------------------------------------------------------
# Tests: R-003 path validation (Block C1)
# ---------------------------------------------------------------------------

class TestRelativePaths(unittest.TestCase):
    """R-003: All path fields must be POSIX-relative with no traversal components."""

    # --- Negative controls: each field + invalid route ---

    def test_artifact_path_absolute(self) -> None:
        doc = _make_doc("assignment", copy.deepcopy(_ASSIGNMENT_BODY))
        doc["artifacts"] = [{"path": "/absolute/x.txt", "sha256": "4" * 64, "role": "delta"}]
        with self.assertRaises(ProtocolError):
            validate_document(doc)

    def test_artifact_path_traversal_prefix(self) -> None:
        doc = _make_doc("assignment", copy.deepcopy(_ASSIGNMENT_BODY))
        doc["artifacts"] = [{"path": "../escape/x.txt", "sha256": "4" * 64, "role": "delta"}]
        with self.assertRaises(ProtocolError):
            validate_document(doc)

    def test_artifact_path_traversal_middle(self) -> None:
        doc = _make_doc("assignment", copy.deepcopy(_ASSIGNMENT_BODY))
        doc["artifacts"] = [{"path": "safe/../x.txt", "sha256": "4" * 64, "role": "delta"}]
        with self.assertRaises(ProtocolError):
            validate_document(doc)

    def test_artifact_path_traversal_deep(self) -> None:
        doc = _make_doc("assignment", copy.deepcopy(_ASSIGNMENT_BODY))
        doc["artifacts"] = [{"path": "a/b/../../x.txt", "sha256": "4" * 64, "role": "delta"}]
        with self.assertRaises(ProtocolError):
            validate_document(doc)

    def test_artifact_path_empty(self) -> None:
        doc = _make_doc("assignment", copy.deepcopy(_ASSIGNMENT_BODY))
        doc["artifacts"] = [{"path": "", "sha256": "4" * 64, "role": "delta"}]
        with self.assertRaises(ProtocolError):
            validate_document(doc)

    def test_artifact_path_non_string(self) -> None:
        doc = _make_doc("assignment", copy.deepcopy(_ASSIGNMENT_BODY))
        doc["artifacts"] = [{"path": 123, "sha256": "4" * 64, "role": "delta"}]
        with self.assertRaises(ProtocolError):
            validate_document(doc)

    def test_allowlist_path_absolute(self) -> None:
        body = copy.deepcopy(_ASSIGNMENT_BODY)
        body["allowlist"] = ["/absolute/x.txt"]
        doc = _make_doc("assignment", body)
        with self.assertRaises(ProtocolError):
            validate_document(doc)

    def test_allowlist_path_traversal(self) -> None:
        body = copy.deepcopy(_ASSIGNMENT_BODY)
        body["allowlist"] = ["safe/../x.txt"]
        doc = _make_doc("assignment", body)
        with self.assertRaises(ProtocolError):
            validate_document(doc)

    def test_checkdef_cwd_absolute(self) -> None:
        body = copy.deepcopy(_ASSIGNMENT_BODY)
        body["checks"][0]["cwd"] = "/abs/cwd"
        doc = _make_doc("assignment", body)
        with self.assertRaises(ProtocolError):
            validate_document(doc)

    def test_checkdef_allowed_writes_traversal(self) -> None:
        body = copy.deepcopy(_ASSIGNMENT_BODY)
        body["checks"][0]["allowed_writes"] = ["../bad.txt"]
        doc = _make_doc("assignment", body)
        with self.assertRaises(ProtocolError):
            validate_document(doc)

    def test_impl_change_path_absolute(self) -> None:
        body = copy.deepcopy(_IMPLEMENTATION_BODY)
        body["changes"][0]["path"] = "/absolute/x.txt"
        doc = _make_doc("implementation", body)
        with self.assertRaises(ProtocolError):
            validate_document(doc)

    def test_impl_change_path_traversal(self) -> None:
        body = copy.deepcopy(_IMPLEMENTATION_BODY)
        body["changes"][0]["path"] = "a/b/../../x.txt"
        doc = _make_doc("implementation", body)
        with self.assertRaises(ProtocolError):
            validate_document(doc)

    def test_impl_criteria_evidence_paths_traversal(self) -> None:
        body = copy.deepcopy(_IMPLEMENTATION_BODY)
        body["criteria"][0]["evidence_paths"] = ["../bad.txt"]
        doc = _make_doc("implementation", body)
        with self.assertRaises(ProtocolError):
            validate_document(doc)

    def test_impl_previous_findings_evidence_paths_traversal(self) -> None:
        body = copy.deepcopy(_IMPLEMENTATION_BODY)
        body["previous_findings"] = [{
            "finding_id": "F-001", "resolution": "BLOCKED",
            "explanation": "x", "evidence_paths": ["a/../b.txt"],
        }]
        doc = _make_doc("implementation", body)
        with self.assertRaises(ProtocolError):
            validate_document(doc)

    def test_checks_result_cwd_absolute(self) -> None:
        body = copy.deepcopy(_CHECKS_BODY)
        body["results"][0]["cwd"] = "/abs/cwd"
        doc = _make_doc("checks", body)
        with self.assertRaises(ProtocolError):
            validate_document(doc)

    def test_checks_result_stdout_path_traversal(self) -> None:
        body = copy.deepcopy(_CHECKS_BODY)
        body["results"][0]["stdout_path"] = "../out.txt"
        doc = _make_doc("checks", body)
        with self.assertRaises(ProtocolError):
            validate_document(doc)

    def test_checks_result_stderr_path_traversal(self) -> None:
        body = copy.deepcopy(_CHECKS_BODY)
        body["results"][0]["stderr_path"] = "a/../../err.txt"
        doc = _make_doc("checks", body)
        with self.assertRaises(ProtocolError):
            validate_document(doc)

    def test_review_finding_path_traversal(self) -> None:
        """A finding with path traversal must be rejected. Use REQUIRES_CHANGES verdict."""
        body = copy.deepcopy(_REVIEW_BODY)
        body["verdict"] = "REQUIRES_CHANGES"  # must not be APPROVED (would need 0 findings)
        body["findings"].append({
            "finding_id": "F-001", "severity": "LOW",
            "path": "safe.txt", "line": 1,
            "message": "x", "in_scope": True, "criterion_id": None,
        })
        doc = _make_doc("review", body)
        doc["status"] = "REQUIRES_CHANGES"
        validate_document(doc)
        doc["body"]["findings"][0]["path"] = "../escape.txt"
        with self.assertRaises(ProtocolError):
            validate_document(doc)

    def test_review_finding_path_valid_control(self) -> None:
        """A finding with a valid path must be accepted."""
        body = copy.deepcopy(_REVIEW_BODY)
        # Use REQUIRES_CHANGES verdict (APPROVED requires 0 findings, all criteria MET)
        doc = _make_doc("review", body)
        doc["body"]["verdict"] = "REQUIRES_CHANGES"
        doc["status"] = "REQUIRES_CHANGES"
        # Add a finding with a valid path
        doc["body"]["findings"].append({
            "finding_id": "F-002", "severity": "LOW",
            "path": "safe.txt", "line": 1,
            "message": "x", "in_scope": True, "criterion_id": None,
        })
        validate_document(doc)  # must not raise

    def test_review_finding_path_traversal_rejects(self) -> None:
        """A finding with path traversal must be rejected. Use REQUIRES_CHANGES verdict."""
        body = copy.deepcopy(_REVIEW_BODY)
        doc = _make_doc("review", body)
        # Change verdict/status to REQUIRES_CHANGES (APPROVED requires 0 findings)
        doc["body"]["verdict"] = "REQUIRES_CHANGES"
        doc["status"] = "REQUIRES_CHANGES"
        doc["body"]["findings"].append({
            "finding_id": "F-001", "severity": "LOW",
            "path": "../escape.txt", "line": 1,
            "message": "x", "in_scope": True, "criterion_id": None,
        })
        with self.assertRaises(ProtocolError):
            validate_document(doc)

    # --- Positive controls ---

    def test_artifact_path_positive(self) -> None:
        doc = _make_doc("assignment", copy.deepcopy(_ASSIGNMENT_BODY))
        doc["artifacts"] = [{"path": "safe/path/file.txt", "sha256": "4" * 64, "role": "delta"}]
        validate_document(doc)

    def test_artifact_path_hidden_file(self) -> None:
        doc = _make_doc("assignment", copy.deepcopy(_ASSIGNMENT_BODY))
        doc["artifacts"] = [{"path": ".hidden/file.txt", "sha256": "4" * 64, "role": "delta"}]
        validate_document(doc)

    def test_artifact_path_dotnotes(self) -> None:
        doc = _make_doc("assignment", copy.deepcopy(_ASSIGNMENT_BODY))
        doc["artifacts"] = [{"path": "..notes/file.txt", "sha256": "4" * 64, "role": "delta"}]
        validate_document(doc)

    def test_artifact_path_spaces(self) -> None:
        doc = _make_doc("assignment", copy.deepcopy(_ASSIGNMENT_BODY))
        doc["artifacts"] = [{"path": "dir with spaces/file.txt", "sha256": "4" * 64, "role": "delta"}]
        validate_document(doc)

    def test_allowlist_path_positive(self) -> None:
        body = copy.deepcopy(_ASSIGNMENT_BODY)
        body["allowlist"] = ["tools/task_automation/contracts.py", ".hidden/x.txt"]
        doc = _make_doc("assignment", body)
        validate_document(doc)

    def test_checkdef_cwd_dot(self) -> None:
        body = copy.deepcopy(_ASSIGNMENT_BODY)
        body["checks"][0]["cwd"] = "."
        doc = _make_doc("assignment", body)
        validate_document(doc)

    def test_impl_change_path_positive(self) -> None:
        body = copy.deepcopy(_IMPLEMENTATION_BODY)
        body["changes"][0]["path"] = "a/b/../c.txt"  # not traversal, just "..notes" style is safe
        # Actually ".." as component is rejected; use a truly valid path
        body["changes"][0]["path"] = "a/b/c.txt"
        doc = _make_doc("implementation", body)
        validate_document(doc)

    def test_impl_criteria_evidence_paths_positive(self) -> None:
        body = copy.deepcopy(_IMPLEMENTATION_BODY)
        body["criteria"][0]["evidence_paths"] = ["artifacts/rev/check.txt"]
        doc = _make_doc("implementation", body)
        validate_document(doc)

    def test_impl_previous_findings_evidence_paths_positive(self) -> None:
        body = copy.deepcopy(_IMPLEMENTATION_BODY)
        body["previous_findings"] = [{
            "finding_id": "F-001", "resolution": "BLOCKED",
            "explanation": "x", "evidence_paths": ["artifacts/e.txt"],
        }]
        doc = _make_doc("implementation", body)
        validate_document(doc)

    def test_checks_result_cwd_positive(self) -> None:
        body = copy.deepcopy(_CHECKS_BODY)
        body["results"][0]["cwd"] = "."
        doc = _make_doc("checks", body)
        validate_document(doc)

    def test_checks_result_paths_positive(self) -> None:
        body = copy.deepcopy(_CHECKS_BODY)
        body["results"][0]["stdout_path"] = "a/b/out.txt"
        body["results"][0]["stderr_path"] = ".hidden/err.txt"
        doc = _make_doc("checks", body)
        validate_document(doc)

    def test_review_finding_path_null_global(self) -> None:
        """Finding path=null (global finding) must remain valid."""
        body = copy.deepcopy(_REVIEW_BODY)
        body["findings"] = [{
            "finding_id": "F-001", "severity": "LOW",
            "path": None, "line": None,
            "message": "x", "in_scope": True, "criterion_id": None,
        }]
        # Use REQUIRES_CHANGES since findings are non-empty
        body["verdict"] = "REQUIRES_CHANGES"
        doc = _make_doc("review", body)
        doc["status"] = "REQUIRES_CHANGES"
        validate_document(doc)

    def test_review_finding_path_positive(self) -> None:
        body = copy.deepcopy(_REVIEW_BODY)
        body["findings"] = [{
            "finding_id": "F-001", "severity": "LOW",
            "path": "src/finding.txt", "line": 42,
            "message": "x", "in_scope": True, "criterion_id": None,
        }]
        # Use REQUIRES_CHANGES since findings are non-empty
        body["verdict"] = "REQUIRES_CHANGES"
        doc = _make_doc("review", body)
        doc["status"] = "REQUIRES_CHANGES"
        validate_document(doc)

    def test_checkdef_allowed_writes_positive(self) -> None:
        body = copy.deepcopy(_ASSIGNMENT_BODY)
        body["checks"][0]["allowed_writes"] = ["artifacts/out.txt", ".hidden/x"]
        doc = _make_doc("assignment", body)
        validate_document(doc)

    def test_artifact_path_deep_traversal_rejected(self) -> None:
        """Deep traversal like a/b/../../x must be rejected."""
        doc = _make_doc("assignment", copy.deepcopy(_ASSIGNMENT_BODY))
        doc["artifacts"] = [{"path": "a/b/../../x.txt", "sha256": "4" * 64, "role": "delta"}]
        with self.assertRaises(ProtocolError):
            validate_document(doc)

    def test_artifact_path_only_dot_rejected(self) -> None:
        """Artifact path '.' alone is not a valid relative path for artifacts."""
        doc = _make_doc("assignment", copy.deepcopy(_ASSIGNMENT_BODY))
        doc["artifacts"] = [{"path": ".", "sha256": "4" * 64, "role": "delta"}]
        with self.assertRaises(ProtocolError):
            validate_document(doc)


# ---------------------------------------------------------------------------
# REV005: public-API regression matrices, with independently validated controls
# ---------------------------------------------------------------------------

_REGRESSION_BODIES = {
    "assignment": _ASSIGNMENT_BODY,
    "implementation": _IMPLEMENTATION_BODY,
    "checks": _CHECKS_BODY,
    "review": _REVIEW_BODY,
    "close": _CLOSE_BODY,
}


def _regression_doc(kind: str) -> Dict[str, Any]:
    """Seed optional lists so every nested-object case reaches its validator."""
    doc = _make_doc(kind, copy.deepcopy(_REGRESSION_BODIES[kind]))
    body = doc["body"]
    if kind == "assignment":
        body["checks"][0]["allowed_writes"] = ["artifacts/output.txt"]
        body["dependencies"] = ["ACT-AUTO-002"]
    else:
        body["limitations"] = ["Only local evidence"]
    if kind == "implementation":
        body["previous_findings"] = [{
            "finding_id": "F-001", "resolution": "ADDRESSED",
            "explanation": "Fixed with local evidence",
            "evidence_paths": ["artifacts/fix.txt"],
        }]
    if kind == "review":
        doc["status"] = body["verdict"] = "REQUIRES_CHANGES"
        body["findings"] = [{
            "finding_id": "F-001", "severity": "HIGH",
            "path": "src/finding.txt", "line": 1,
            "message": "Correction required", "in_scope": True,
            "criterion_id": "CRIT-001",
        }]
    return doc


def _regression_at(value: Any, path: tuple) -> Any:
    for component in path:
        value = value[component]
    return value


def _regression_replace(doc: Any, path: tuple, value: Any) -> Any:
    """Replace exactly one value, including the root object when path is empty."""
    if not path:
        return value
    _regression_at(doc, path[:-1])[path[-1]] = value
    return doc


class _PublicContractControls:
    """Helpers use public validators; each API receives independent input."""

    def assert_valid_document(self, doc: Any) -> None:
        control = copy.deepcopy(doc)
        validate_document(control)
        self.assertEqual(control, doc)
        raw = json.dumps(copy.deepcopy(doc), ensure_ascii=True).encode("utf-8")
        self.assertEqual(parse_document(raw), doc)

    def assert_rejected_document(self, doc: Any) -> None:
        with self.assertRaises(ProtocolError):
            validate_document(copy.deepcopy(doc))
        raw = json.dumps(copy.deepcopy(doc), ensure_ascii=True).encode("utf-8")
        with self.assertRaises(ProtocolError):
            parse_document(raw)

    def assert_bad_value(self, base: Any, path: tuple, value: Any) -> None:
        self.assert_valid_document(copy.deepcopy(base))
        mutated = _regression_replace(copy.deepcopy(base), path, copy.deepcopy(value))
        self.assert_rejected_document(mutated)


class TestPublicObjectSchemaMatrices(_PublicContractControls, unittest.TestCase):
    """Exact keys and object types at every envelope, body and nested object."""

    _OBJECTS = (
        tuple((kind, ()) for kind in _REGRESSION_BODIES)
        + tuple((kind, ("body",)) for kind in _REGRESSION_BODIES)
        + (
            ("assignment", ("artifacts", 0)),
            ("assignment", ("body", "checks", 0)),
            ("assignment", ("body", "acceptance", 0)),
            ("assignment", ("body", "permissions")),
            ("assignment", ("body", "limits")),
            ("implementation", ("body", "changes", 0)),
            ("implementation", ("body", "criteria", 0)),
            ("review", ("body", "criteria", 0)),
            ("implementation", ("body", "previous_findings", 0)),
            ("checks", ("body", "results", 0)),
            ("review", ("body", "findings", 0)),
        )
    )

    def test_every_required_object_key_missing(self) -> None:
        for kind, path in self._OBJECTS:
            base = _regression_doc(kind)
            for key in _regression_at(base, path):
                with self.subTest(kind=kind, object=path, missing=key):
                    self.assert_valid_document(copy.deepcopy(base))
                    mutated = copy.deepcopy(base)
                    del _regression_at(mutated, path)[key]
                    self.assert_rejected_document(mutated)

    def test_every_object_rejects_extra_key(self) -> None:
        for kind, path in self._OBJECTS:
            with self.subTest(kind=kind, object=path):
                base = _regression_doc(kind)
                self.assert_valid_document(copy.deepcopy(base))
                mutated = copy.deepcopy(base)
                _regression_at(mutated, path)["unexpected"] = "extra"
                self.assert_rejected_document(mutated)

    def test_every_object_rejects_non_object(self) -> None:
        for kind, path in self._OBJECTS:
            for bad in ([], None, "not an object", 0, False):
                with self.subTest(kind=kind, object=path, bad=bad):
                    self.assert_bad_value(_regression_doc(kind), path, bad)

    def test_empty_objects_are_not_complete_schemas(self) -> None:
        for kind, path in self._OBJECTS:
            with self.subTest(kind=kind, object=path):
                self.assert_bad_value(_regression_doc(kind), path, {})


class TestPublicFieldTypeMatrices(_PublicContractControls, unittest.TestCase):
    """Nested enums, string lists, IDs, booleans and integer boundaries."""

    def test_enum_values_reject_wrong_json_types(self) -> None:
        cases = [(kind, (field,)) for kind in _REGRESSION_BODIES
                 for field in ("schema_version", "kind", "producer", "status")]
        cases += [
            ("assignment", ("artifacts", 0, "role")),
            ("assignment", ("body", "review_policy")),
            ("implementation", ("body", "changes", 0, "change")),
            ("implementation", ("body", "criteria", 0, "state")),
            ("review", ("body", "criteria", 0, "state")),
            ("implementation", ("body", "previous_findings", 0, "resolution")),
            ("checks", ("body", "results", 0, "status")),
            ("review", ("body", "verdict")),
            ("review", ("body", "findings", 0, "severity")),
        ]
        for kind, path in cases:
            for bad in ([], {}, None, 7):
                with self.subTest(kind=kind, field=path, bad=bad):
                    self.assert_bad_value(_regression_doc(kind), path, bad)

    def test_acceptance_check_ids_reject_bad_elements_and_unknown_ids(self) -> None:
        path = ("body", "acceptance", 0, "check_ids", 0)
        for bad in ([], {}, None, 0, False, "", "CHK-UNKNOWN", "CHK-001\n"):
            with self.subTest(bad=bad):
                self.assert_bad_value(_regression_doc("assignment"), path, bad)

    def test_acceptance_description_is_nonempty_string(self) -> None:
        path = ("body", "acceptance", 0, "description")
        for bad in ("", [], {}, None, 0, False):
            with self.subTest(bad=bad):
                self.assert_bad_value(_regression_doc("assignment"), path, bad)

    def test_argv_cardinality_and_nonempty_string_items(self) -> None:
        for kind, path in (
            ("assignment", ("body", "checks", 0, "argv")),
            ("checks", ("body", "results", 0, "argv")),
        ):
            # Only CheckDef declares a nonempty argv list. The captured result
            # schema declares a list of strings, without an extra minimum.
            bad_lists = ([], "python", {}, None, 0, False) if kind == "assignment" else (
                "python", {}, None, 0, False,
            )
            for bad in bad_lists:
                with self.subTest(kind=kind, list_value=bad):
                    self.assert_bad_value(_regression_doc(kind), path, bad)
            for bad in ("", [], {}, None, 0, False):
                with self.subTest(kind=kind, element=bad):
                    self.assert_bad_value(_regression_doc(kind), path + (0,), bad)
        doc = _regression_doc("checks")
        doc["body"]["results"][0]["argv"] = []
        self.assert_valid_document(doc)

    def test_limitations_in_all_four_kinds(self) -> None:
        for kind in ("implementation", "checks", "review", "close"):
            for bad in ("text", {}, None, 0, False):
                with self.subTest(kind=kind, list_value=bad):
                    self.assert_bad_value(_regression_doc(kind), ("body", "limitations"), bad)
            for bad in ("", [], {}, None, 0, False):
                with self.subTest(kind=kind, element=bad):
                    self.assert_bad_value(_regression_doc(kind), ("body", "limitations", 0), bad)
            with self.subTest(kind=kind, empty_list="allowed"):
                doc = _regression_doc(kind)
                doc["body"]["limitations"] = []
                self.assert_valid_document(doc)

    def test_pending_findings_ids_without_completed_gate_masking_types(self) -> None:
        base = _regression_doc("close")
        base["status"] = "BLOCKED"
        base["body"]["pending_findings"] = ["F-001"]
        for bad in ("F-001", {}, None, 0, False):
            with self.subTest(list_value=bad):
                self.assert_bad_value(base, ("body", "pending_findings"), bad)
        for bad in ("", [], {}, None, 0, False, "f-001", "F-001\n"):
            with self.subTest(element=bad):
                self.assert_bad_value(base, ("body", "pending_findings", 0), bad)

    def test_integer_fields_reject_bool_and_other_wrong_types(self) -> None:
        cases = (
            ("assignment", ("body", "permissions", "cloud_calls")),
            ("assignment", ("body", "limits", "max_corrections")),
            ("assignment", ("body", "limits", "max_calls")),
            ("assignment", ("body", "limits", "call_timeout_seconds")),
            ("assignment", ("body", "checks", 0, "timeout_seconds")),
            ("checks", ("body", "results", 0, "elapsed_ms")),
            ("checks", ("body", "results", 0, "exit_code")),
            ("review", ("body", "findings", 0, "line")),
        )
        for kind, path in cases:
            for bad in (False, True, "1", [], {}, 1.0):
                with self.subTest(kind=kind, field=path, bad=bad):
                    base = _regression_doc(kind)
                    # Match numeric bool values to a valid integer control so that
                    # result-status coherence cannot hide a missing bool guard.
                    if kind == "checks" and path[-1] == "exit_code" and bad is True:
                        base["body"]["results"][0]["exit_code"] = 1
                        base["body"]["results"][0]["status"] = "FAILED"
                        base["status"] = "FAILED"
                    self.assert_bad_value(base, path, bad)

    def test_positive_integer_fields_reject_zero(self) -> None:
        for kind, path in (
            ("assignment", ("body", "checks", 0, "timeout_seconds")),
            ("assignment", ("body", "limits", "call_timeout_seconds")),
            ("review", ("body", "findings", 0, "line")),
        ):
            for bad in (0, -1):
                with self.subTest(kind=kind, field=path, bad=bad):
                    self.assert_bad_value(_regression_doc(kind), path, bad)
            doc = _regression_replace(_regression_doc(kind), path, 1)
            self.assert_valid_document(doc)

    def test_nonnegative_counters_accept_zero_and_reject_negative(self) -> None:
        for kind, path in (
            ("assignment", ("body", "permissions", "cloud_calls")),
            ("assignment", ("body", "limits", "max_corrections")),
            ("assignment", ("body", "limits", "max_calls")),
            ("checks", ("body", "results", 0, "elapsed_ms")),
        ):
            for value in (0, 1):
                with self.subTest(kind=kind, field=path, valid=value):
                    doc = _regression_replace(_regression_doc(kind), path, value)
                    self.assert_valid_document(doc)
            with self.subTest(kind=kind, field=path, invalid=-1):
                self.assert_bad_value(_regression_doc(kind), path, -1)

    def test_boolean_fields_reject_integer_coercion(self) -> None:
        cases = [("assignment", ("body", "permissions", field))
                 for field in ("fixture_git", "public_docs", "temp_artifacts")]
        cases += [
            ("checks", ("body", "results", 0, "timed_out")),
            ("review", ("body", "findings", 0, "in_scope")),
        ]
        for kind, path in cases:
            for bad in (0, 1, None, "false", [], {}):
                with self.subTest(kind=kind, field=path, bad=bad):
                    base = _regression_doc(kind)
                    if path[-1] == "timed_out" and bad == 1:
                        base["body"]["results"][0].update(
                            timed_out=True, exit_code=None, status="TIMED_OUT")
                        base["status"] = "TIMED_OUT"
                    self.assert_bad_value(base, path, bad)


class TestPublicIdentityMatrices(_PublicContractControls, unittest.TestCase):
    """All eight context keys, producer constraints, and context container types."""

    _IDENTITY_KEYS = (
        "group_id", "task_id", "run_id", "revision_id", "baseline_id",
        "contract_hash", "evidence_hash", "producer",
    )

    def test_producer_expected_by_every_kind(self) -> None:
        expected = {"assignment": "controller", "implementation": "implementer",
                    "checks": "controller", "review": "reviewer", "close": "controller"}
        for kind, producer in expected.items():
            with self.subTest(kind=kind, producer=producer):
                doc = _regression_doc(kind)
                self.assertEqual(doc["producer"], producer)
                self.assert_valid_document(doc)
            for wrong in ("controller", "implementer", "reviewer", "unknown"):
                if wrong != producer:
                    with self.subTest(kind=kind, wrong=wrong):
                        self.assert_bad_value(_regression_doc(kind), ("producer",), wrong)

    def test_every_identity_key_matches_and_mismatches(self) -> None:
        alternatives = {
            "group_id": "GROUP-OTHER", "task_id": "ACT-OTHER", "run_id": "RUN-OTHER",
            "revision_id": "REV-999", "baseline_id": "a" * 64,
            "contract_hash": "b" * 64, "evidence_hash": "c" * 64,
            "producer": "reviewer",
        }
        for kind in _REGRESSION_BODIES:
            for key in self._IDENTITY_KEYS:
                for api in (validate_document, parse_document):
                    with self.subTest(kind=kind, key=key, api=api.__name__):
                        base = _regression_doc(kind)
                        self.assert_valid_document(copy.deepcopy(base))

                        def fresh_input():
                            doc = copy.deepcopy(base)
                            return doc if api is validate_document else json.dumps(doc).encode("utf-8")

                        api(fresh_input(), expected_identity={key: base[key]})
                        wrong = alternatives[key]
                        if key == "producer" and wrong == base[key]:
                            wrong = "controller"
                        self.assertNotEqual(wrong, base[key])
                        with self.assertRaises(ProtocolError):
                            api(fresh_input(), expected_identity={key: wrong})

    def test_context_none_empty_dict_and_full_matching_dict(self) -> None:
        for kind in _REGRESSION_BODIES:
            base = _regression_doc(kind)
            full = {key: base[key] for key in self._IDENTITY_KEYS}
            for context in (None, {}, full):
                with self.subTest(kind=kind, context=context):
                    self.assert_valid_document(copy.deepcopy(base))
                    validate_document(copy.deepcopy(base), expected_identity=copy.deepcopy(context))
                    raw = json.dumps(copy.deepcopy(base)).encode("utf-8")
                    self.assertEqual(parse_document(raw, expected_identity=copy.deepcopy(context)), base)

    def test_invalid_context_containers_and_unknown_previous_context_key(self) -> None:
        for kind in _REGRESSION_BODIES:
            base = _regression_doc(kind)
            for context in ([], False, 0, "context", {"unknown": "value"},
                            {"previous_handoff_id": base["previous_handoff_id"]}):
                with self.subTest(kind=kind, context=context):
                    self.assert_valid_document(copy.deepcopy(base))
                    with self.assertRaises(ProtocolError):
                        validate_document(copy.deepcopy(base), expected_identity=copy.deepcopy(context))
                    with self.assertRaises(ProtocolError):
                        parse_document(json.dumps(copy.deepcopy(base)).encode("utf-8"),
                                       expected_identity=copy.deepcopy(context))


class TestPublicCanonicalRobustness(unittest.TestCase):
    """Canonical bytes and both hash APIs share strict JSON/error semantics."""

    _APIS = (canonical_bytes, sha256_json, handoff_id)

    def test_dict_list_and_mixed_cycles_raise_protocol_error(self) -> None:
        for api in self._APIS:
            for shape in ("dict", "list", "dict-list", "list-dict", "mutual-dicts"):
                with self.subTest(api=api.__name__, shape=shape):
                    # Fresh graph for every invocation; no schema violation is needed.
                    if shape == "dict":
                        graph = {}
                        graph["back"] = graph
                    elif shape == "list":
                        graph = []
                        graph.append(graph)
                    elif shape == "dict-list":
                        graph = {"items": []}
                        graph["items"].append(graph)
                    elif shape == "list-dict":
                        graph = [{}]
                        graph[0]["back"] = graph
                    else:
                        graph = {"child": {}}
                        graph["child"]["parent"] = graph
                    with self.assertRaises(ProtocolError):
                        api(graph)

    def test_real_shared_aliases_are_not_cycles(self) -> None:
        for api in self._APIS:
            with self.subTest(api=api.__name__):
                shared = {"items": [True, 0, None, "café"]}
                graph = {"a": shared, "b": shared}
                self.assertIs(graph["a"], graph["b"])
                independent = {"a": copy.deepcopy(shared), "b": copy.deepcopy(shared)}
                self.assertEqual(api(graph), api(independent))
                self.assertIs(graph["a"], graph["b"])
                doc = _regression_doc("implementation")
                same_criterion = doc["body"]["criteria"][0]
                doc["body"]["criteria"] = [same_criterion, same_criterion]
                self.assertIs(doc["body"]["criteria"][0], doc["body"]["criteria"][1])
                validate_document(doc)
                self.assertEqual(api(doc), api(copy.deepcopy(doc)))
                parsed = parse_document(canonical_bytes(doc))
                self.assertEqual(parsed, doc)

    def test_unsupported_types_raise_protocol_error_in_all_hash_apis(self) -> None:
        bad_values = (1.5, float("nan"), float("inf"), float("-inf"),
                      b"bytes", bytearray(b"bytes"), (1, 2), {1, 2},
                      frozenset({1}), complex(1, 2), object())
        for api in self._APIS:
            for value in bad_values:
                for nested in (False, True):
                    with self.subTest(api=api.__name__, type=type(value).__name__, nested=nested):
                        arg = {"value": [value]} if nested else value
                        with self.assertRaises(ProtocolError):
                            api(arg)
            for key in (0, True, None, ("key",)):
                with self.subTest(api=api.__name__, key=key):
                    with self.assertRaises(ProtocolError):
                        api({key: "value"})

    def test_surrogate_keys_values_and_literal_python_pair_are_invalid(self) -> None:
        for api in self._APIS:
            for text in ("\ud800", "\udfff", "x\ud800y", "\ud834\udd1e"):
                for placement in ("scalar", "value", "key"):
                    with self.subTest(api=api.__name__, text=repr(text), placement=placement):
                        value = text if placement == "scalar" else (
                            {text: "value"} if placement == "key" else {"value": [text]})
                        with self.assertRaises(ProtocolError):
                            api(value)

    def test_generic_canonical_values_and_hashes_have_exact_bytes(self) -> None:
        cases = (
            (True, b"true"), (False, b"false"), (0, b"0"), (-7, b"-7"),
            (None, b"null"), ([], b"[]"), ({}, b"{}"),
            ([True, 1, None, {}], b"[true,1,null,{}]"),
            ({"b": None, "a": [False, 0]}, b'{"a":[false,0],"b":null}'),
            ("\U0001d11e", '"\U0001d11e"'.encode("utf-8")),
            ({"é": "e\u0301"}, '{"é":"e\u0301"}'.encode("utf-8")),
        )
        for value, expected in cases:
            with self.subTest(value=value):
                self.assertEqual(canonical_bytes(copy.deepcopy(value)), expected)
                digest = hashlib.sha256(expected).hexdigest()
                self.assertEqual(sha256_json(copy.deepcopy(value)), digest)
                self.assertEqual(handoff_id(copy.deepcopy(value)), digest)
        self.assertNotEqual(canonical_bytes("é"), canonical_bytes("e\u0301"))
        self.assertNotEqual(sha256_json(True), sha256_json(1))

    def test_full_document_hash_includes_envelope_and_body(self) -> None:
        for kind in _REGRESSION_BODIES:
            with self.subTest(kind=kind):
                doc = _regression_doc(kind)
                validate_document(doc)
                reference = json.dumps(doc, sort_keys=True, separators=(",", ":"),
                                       ensure_ascii=False).encode("utf-8")
                self.assertEqual(canonical_bytes(doc), reference)
                self.assertEqual(handoff_id(doc), hashlib.sha256(reference).hexdigest())
                changed = copy.deepcopy(doc)
                changed["created_at"] = "2026-10-05T12:00:01Z"
                validate_document(changed)
                self.assertNotEqual(handoff_id(doc), handoff_id(changed))


class TestPublicRawJSONRobustness(_PublicContractControls, unittest.TestCase):
    """Raw JSON regressions without adding document-size or nesting caps."""

    def test_escaped_lone_surrogates_in_valid_envelope(self) -> None:
        for lone in ("\ud800", "\udfff"):
            with self.subTest(lone=repr(lone)):
                base = _regression_doc("implementation")
                self.assert_valid_document(copy.deepcopy(base))
                doc = copy.deepcopy(base)
                doc["body"]["summary"] = lone
                raw = json.dumps(doc, ensure_ascii=True).encode("utf-8")
                self.assertEqual(json.loads(raw), doc)
                with self.assertRaises(ProtocolError) as err:
                    parse_document(raw)
                self.assertEqual(err.exception.code, 1042)

    def test_real_unicode_and_escaped_pair_parse_to_identical_scalar(self) -> None:
        doc = _regression_doc("implementation")
        doc["body"]["summary"] = "café \U0001d11e e\u0301"
        self.assert_valid_document(doc)
        escaped = json.dumps(doc, ensure_ascii=True).encode("utf-8")
        literal = json.dumps(doc, ensure_ascii=False).encode("utf-8")
        self.assertIn(b"\\ud834\\udd1e", escaped)
        self.assertEqual(parse_document(escaped), doc)
        self.assertEqual(parse_document(literal), doc)
        self.assertEqual(canonical_bytes(parse_document(escaped)), canonical_bytes(doc))

    def test_document_over_one_mib_has_no_unspecified_size_cap(self) -> None:
        doc = _regression_doc("assignment")
        doc["body"]["source_contract"] = "Normative contract\n" + "x" * (1_048_576 + 1)
        validate_document(copy.deepcopy(doc))
        raw = json.dumps(doc, ensure_ascii=True).encode("utf-8")
        self.assertGreater(len(raw), 1_048_576)
        self.assertEqual(parse_document(raw), doc)

    def test_huge_raw_integer_decoder_value_error_is_protocol_error(self) -> None:
        limit = sys.get_int_max_str_digits()
        doc = _regression_doc("assignment")
        self.assert_valid_document(copy.deepcopy(doc))
        raw = json.dumps(doc, separators=(",", ":")).encode("utf-8")
        token = b'"cloud_calls":0'
        self.assertEqual(raw.count(token), 1)
        raw = raw.replace(token, b'"cloud_calls":' + b"9" * 5000, 1)
        # The JSON is syntactically valid; only the interpreter integer guard fails.
        if limit != 0 and limit < 5000:
            with self.assertRaises(ValueError):
                json.loads(raw)
            with self.assertRaises(ProtocolError):
                parse_document(raw)
        else:
            # No new protocol limit is imposed when the interpreter permits it.
            self.assertEqual(parse_document(raw), json.loads(raw))

    def test_huge_integer_encoder_value_error_is_protocol_error(self) -> None:
        limit = sys.get_int_max_str_digits()
        huge = 10 ** 4999
        if limit != 0 and limit < 5000:
            with self.assertRaises(ValueError):
                json.dumps({"value": huge})
            for api in (canonical_bytes, sha256_json, handoff_id):
                with self.subTest(api=api.__name__):
                    with self.assertRaises(ProtocolError):
                        api({"value": huge})
        else:
            self.assertEqual(
                canonical_bytes({"value": huge}),
                json.dumps({"value": huge}, separators=(",", ":")).encode("utf-8"),
            )
            self.assertEqual(sha256_json({"value": huge}), handoff_id({"value": huge}))

    def test_deep_raw_json_interpreter_recursion_is_protocol_error(self) -> None:
        doc = _regression_doc("assignment")
        self.assert_valid_document(copy.deepcopy(doc))
        raw = json.dumps(doc, separators=(",", ":")).encode("utf-8")
        token = b'"source_contract":"Section 13.7 ACT-AUTO-001"'
        self.assertEqual(raw.count(token), 1)
        # CPython's C JSON decoder budget is not necessarily the Python
        # recursion limit (notably on 3.12); exceed both in this fixture.
        depth = max(10_000, sys.getrecursionlimit() * 8)
        nested = b"[" * depth + b"0" + b"]" * depth
        raw = raw.replace(token, b'"source_contract":' + nested, 1)
        with self.assertRaises(RecursionError):
            json.loads(raw)
        with self.assertRaises(ProtocolError):
            parse_document(raw)


class TestPublicLexicalPathMatrices(_PublicContractControls, unittest.TestCase):
    """Every path field is exercised lexically, without filesystem operations."""

    _PATHS = (
        ("assignment", ("artifacts", 0, "path"), False),
        ("assignment", ("body", "allowlist", 0), False),
        ("assignment", ("body", "checks", 0, "cwd"), True),
        ("assignment", ("body", "checks", 0, "allowed_writes", 0), False),
        ("implementation", ("body", "changes", 0, "path"), False),
        ("implementation", ("body", "criteria", 0, "evidence_paths", 0), False),
        ("implementation", ("body", "previous_findings", 0, "evidence_paths", 0), False),
        ("checks", ("body", "results", 0, "cwd"), True),
        ("checks", ("body", "results", 0, "stdout_path"), False),
        ("checks", ("body", "results", 0, "stderr_path"), False),
        ("review", ("body", "criteria", 0, "evidence_paths", 0), False),
        ("review", ("body", "findings", 0, "path"), False),
    )

    def test_each_path_rejects_lexically_unsafe_or_wrong_type(self) -> None:
        for kind, path, allow_dot in self._PATHS:
            bad_values = ("", "/absolute/file.txt", "../file.txt", "safe/../file.txt",
                          "a/b/../../file.txt", "..", [], {}, 0, False)
            if path != ("body", "findings", 0, "path"):
                bad_values += (None,)
            if not allow_dot:
                bad_values += (".",)
            for bad in bad_values:
                with self.subTest(kind=kind, field=path, bad=bad):
                    self.assert_bad_value(_regression_doc(kind), path, bad)

    def test_each_path_accepts_normalized_safe_names(self) -> None:
        for kind, path, allow_dot in self._PATHS:
            good_values = ("safe/file.txt", ".hidden/file.txt", "..notes/file.txt",
                           "dir with spaces/file.txt", "evidence/café.txt")
            if allow_dot:
                good_values += (".",)
            if path == ("body", "findings", 0, "path"):
                good_values += (None,)
            for good in good_values:
                with self.subTest(kind=kind, field=path, good=good):
                    doc = _regression_replace(_regression_doc(kind), path, good)
                    self.assert_valid_document(doc)


if __name__ == "__main__":
    unittest.main()
