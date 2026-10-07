"""Protocol validators for GROUP-AUTO-001 handoff envelopes (ACT-AUTO-001).

Validates the five handoff types: assignment, implementation, checks, review, close.
Uses only Python standard library. No subprocess, Git, provider, CLI, or artifact I/O.
"""

import hashlib
import json
import re
from typing import Any, Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# Custom exception
# ---------------------------------------------------------------------------

class ProtocolError(Exception):
    """Raised when a document violates the protocol."""

    def __init__(self, code: int) -> None:
        self.code = code
        super().__init__(f"Protocol error {code}")


# ---------------------------------------------------------------------------
# ID / hash / date patterns (section 13.6)
# ---------------------------------------------------------------------------

_ID_RE = re.compile(r"^[A-Z][A-Z0-9-]{0,63}$")
_REV_RE = re.compile(r"^REV-[0-9]{3}$")
_HASH_RE = re.compile(r"^[a-f0-9]{64}$")
_UTC_RE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$")

# R-005: allowed keys for expected_identity validation (whitelist exacta)
_EXPECTED_IDENTITY_KEYS = frozenset({
    "group_id",
    "task_id",
    "run_id",
    "revision_id",
    "baseline_id",
    "contract_hash",
    "evidence_hash",
    "producer",
})


def _check_id(value: Any, field: str) -> None:
    if not isinstance(value, str) or not value or not _ID_RE.fullmatch(value):
        raise ProtocolError(1001)


def _check_rev(value: Any, field: str) -> None:
    if not isinstance(value, str) or not _REV_RE.fullmatch(value):
        raise ProtocolError(1002)


# Revision semantics (section 13.6): assignment → REV-000; others → REV-001..REV-999
def _check_rev_kind(value: Any, kind: str, field: str) -> None:
    """Validate revision_id against kind-specific allowed range."""
    if not isinstance(value, str) or not _REV_RE.fullmatch(value):
        raise ProtocolError(1002)
    rev_num = int(value.split("-")[1])
    if kind == "assignment" and rev_num != 0:
        raise ProtocolError(9060)
    if kind != "assignment" and rev_num < 1 or rev_num > 999:
        raise ProtocolError(9061)


def _check_hash(value: Any, field: str, required: bool = False) -> None:
    """Validate a hash field.

    *required=True*  – value must be a non-null 64-hex string.
    *required=False* (default) – null is accepted; otherwise must be a valid hash.
    Uses fullmatch to reject trailing newlines and other extra characters.
    """
    if value is None:
        if required:
            raise ProtocolError(1003)
        return
    if not isinstance(value, str) or not _HASH_RE.fullmatch(value):
        raise ProtocolError(1003)


def _check_utc(value: Any, field: str) -> None:
    if not isinstance(value, str) or not _UTC_RE.fullmatch(value):
        raise ProtocolError(1004)
    # R-005: Reject impossible dates via pure calendar validation (no clock access)
    try:
        year = int(value[0:4])
        month = int(value[5:7])
        day = int(value[8:10])
        hour = int(value[11:13])
        minute = int(value[14:16])
        second = int(value[17:19])
    except (ValueError, IndexError):
        raise ProtocolError(1004)
    # Pure calendar validation
    if month < 1 or month > 12:
        raise ProtocolError(1052)
    days_in_month = [0, 31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
    # Leap year check
    if (year % 4 == 0 and year % 100 != 0) or (year % 400 == 0):
        days_in_month[2] = 29
    if day < 1 or day > days_in_month[month]:
        raise ProtocolError(1053)
    if hour > 23 or minute > 59 or second > 59:
        raise ProtocolError(1054)


# ---------------------------------------------------------------------------
# Canonicalisation helpers
# ---------------------------------------------------------------------------

def _validate_json_types_strict(obj: Any) -> None:
    """Validate that *obj* contains only JSON-compatible types.

    Supported: dict (string keys), list, str, int, bool, None.
    Rejected: float (all floats including finite), bytes, set, tuple, other objects.
    Raises ProtocolError with stable code on first violation.
    """
    # An ancestor stack distinguishes actual cycles from repeated references.
    # Iteration avoids introducing a protocol-specific nesting limit.
    ancestors = set()
    stack = [(obj, False)]
    while stack:
        value, leaving = stack.pop()
        if leaving:
            ancestors.remove(id(value))
            continue
        if isinstance(value, (dict, list)):
            if id(value) in ancestors:
                raise ProtocolError(1049)
            ancestors.add(id(value))
            stack.append((value, True))
            if isinstance(value, dict):
                for key in value:
                    if not isinstance(key, str):
                        raise ProtocolError(1043)
                    _check_surrogates_in_str(key)
                children = value.values()
            else:
                children = value
            stack.extend((child, False) for child in children)
        elif isinstance(value, str):
            _check_surrogates_in_str(value)
        elif value is None or isinstance(value, (bool, int)):
            continue
        elif isinstance(value, float):
            raise ProtocolError(1047)
        elif isinstance(value, tuple):
            raise ProtocolError(1044)
        elif isinstance(value, set):
            raise ProtocolError(1045)
        elif isinstance(value, bytes):
            raise ProtocolError(1046)
        else:
            raise ProtocolError(1048)


def canonical_bytes(value: Any) -> bytes:
    """Produce canonical JSON bytes for *value*.

    Rules from 13.6:
    - UTF-8, sorted keys, no spaces after separators, ensure_ascii=False,
      no trailing newline.
    - Reject NaN / Infinity before serialisation.
    - Apply strict JSON type validation: floats, tuples, sets, bytes values,
      and non-string dict keys are all rejected via ProtocolError.
    - Reject cycles (shared references that form back-edges) via ProtocolError.
    """
    _validate_json_types_strict(value)
    try:
        return json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    except (ValueError, TypeError, UnicodeError, RecursionError):
        # Standard encoder/resource failures must not expose the input.
        raise ProtocolError(1048) from None


def sha256_json(value: Any) -> str:
    """SHA-256 hex of canonical bytes for *value*."""
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


# ---------------------------------------------------------------------------
# NaN / Infinity pre-check (recursive)
# ---------------------------------------------------------------------------

def _reject_nan_inf(obj: Any) -> None:
    if isinstance(obj, float):
        if obj != obj or obj == float("inf") or obj == float("-inf"):
            raise ProtocolError(1010)
    elif isinstance(obj, dict):
        for v in obj.values():
            _reject_nan_inf(v)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            _reject_nan_inf(v)


# ---------------------------------------------------------------------------
# Relative-path validation (R-003: POSIX-relative, no traversal)
# ---------------------------------------------------------------------------

def _validate_relative_path(value: Any, field: str, *, allow_dot: bool = False) -> None:
    """Validate *value* as a POSIX-relative path with no traversal components.

    Rules:
    - Must be a non-empty string.
    - Must not be absolute (no leading ``/``).
    - Must not contain an exact ``..`` component in any position.
      ``../x``, ``safe/../x``, ``safe/../../x`` are rejected; ``..notes/x`` is
      accepted because ``..notes`` is a normal name, not the traversal token ``..``.
    - ``.`` as the full value is only allowed when *allow_dot=True* (e.g. cwd).
    - No filesystem access: no ``Path.resolve``, no symlink checks, no existence tests.
    """
    if not isinstance(value, str):
        raise ProtocolError(4100)
    if not value:
        raise ProtocolError(4101)
    if value.startswith("/"):
        raise ProtocolError(4102)
    if ".." in value.split("/"):
        raise ProtocolError(4103)
    # Reject standalone "." unless explicitly allowed (e.g. cwd)
    if not allow_dot and value == ".":
        raise ProtocolError(4104)


# ---------------------------------------------------------------------------
# Common envelope fields
# ---------------------------------------------------------------------------

_REQUIRED_ENVELOPE = {
    "schema_version", "kind", "group_id", "task_id", "run_id",
    "revision_id", "producer", "status", "baseline_id",
    "contract_hash", "evidence_hash", "previous_handoff_id",
    "created_at", "artifacts", "body",
}

_VALID_KINDS = {"assignment", "implementation", "checks", "review", "close"}

# producer restrictions per kind (section 13.6)
_PRODUCER_MAP = {
    "assignment": "controller",
    "implementation": "implementer",
    "checks": "controller",
    "review": "reviewer",
    "close": "controller",
}

_VALID_STATUSES: Dict[str, List[str]] = {
    "assignment": ["ASSIGNED"],
    "implementation": ["READY_FOR_REVIEW"],
    "checks": [None],  # dynamic
    "review": [None],  # dynamic
    "close": [None],   # dynamic
}


def _validate_envelope(doc: Dict[str, Any]) -> None:
    """Validate common envelope fields. Raises ProtocolError on violation."""
    if not isinstance(doc, dict):
        raise ProtocolError(1000)

    # Check for unexpected keys
    extra = set(doc.keys()) - _REQUIRED_ENVELOPE
    if extra:
        raise ProtocolError(2001)

    # Missing required keys
    missing = _REQUIRED_ENVELOPE - set(doc.keys())
    if missing:
        raise ProtocolError(2002)

    # schema_version
    if doc["schema_version"] != "task_automation.v1":
        raise ProtocolError(2003)

    # kind
    kind = doc["kind"]
    if not isinstance(kind, str) or kind not in _VALID_KINDS:
        raise ProtocolError(2004)

    # group_id / task_id / run_id
    for field in ("group_id", "task_id", "run_id"):
        _check_id(doc[field], field)

    # revision_id — kind-specific semantics (assignment → REV-000, others → REV-001..REV-999)
    _check_rev_kind(doc["revision_id"], kind, "revision_id")

    # producer
    expected_producer = _PRODUCER_MAP[kind]
    if doc["producer"] != expected_producer:
        raise ProtocolError(2005)

    # baseline_id / contract_hash / evidence_hash (baseline and contract are always required)
    _check_hash(doc["baseline_id"], "baseline_id", required=True)
    _check_hash(doc["contract_hash"], "contract_hash", required=True)

    # evidence_hash: null allowed only for assignment; all other kinds require non-null valid hash
    _check_hash(
        doc["evidence_hash"],
        "evidence_hash",
        required=(kind != "assignment"),
    )

    # previous_handoff_id: null only for assignment
    if kind != "assignment" and doc["previous_handoff_id"] is None:
        raise ProtocolError(2006)
    _check_hash(doc["previous_handoff_id"], "previous_handoff_id")

    # created_at
    _check_utc(doc["created_at"], "created_at")

    # artifacts
    _validate_artifacts(doc["artifacts"])


def _validate_artifacts(artifacts: Any) -> None:
    if not isinstance(artifacts, list):
        raise ProtocolError(2010)
    for item in artifacts:
        _require_object(item, {"path", "sha256", "role"}, 2012)
        # path: must be relative string, no traversal, no absolute
        _validate_relative_path(item["path"], "path")
        _check_hash(item["sha256"], "sha256", required=True)
        # role: enum validation (section 13.6)
        if not isinstance(item["role"], str) or item["role"] not in _VALID_ARTIFACT_ROLES:
            raise ProtocolError(2014)


# ---------------------------------------------------------------------------
# Body validation helpers
# ---------------------------------------------------------------------------

_VALID_CHECK_STATUSES = {"PASSED", "FAILED", "TIMED_OUT"}
_VALID_REVIEW_VERDICTS = {"APPROVED", "REQUIRES_CHANGES", "BLOCKED"}
_VALID_CLOSE_REASONS = None  # any string
_ALLOWED_WHOLESALE = True  # placeholder for future


def _check_status_allowed(value: Any, allowed: List[str]) -> bool:
    """Check if value is in allowed list."""
    return value in allowed


# ---------------------------------------------------------------------------
# Assignment body validation
# ---------------------------------------------------------------------------

_ASSIGNMENT_BODY_KEYS = {
    "goal", "source_contract", "allowlist", "checks", "acceptance",
    "dependencies", "permissions", "review_policy", "limits", "profile_hash",
}

_VALID_REVIEW_POLICIES = {"MANUAL", "CLOUD"}

# Artifact role enum (section 13.6)
_VALID_ARTIFACT_ROLES = {"baseline", "source", "delta", "log", "checks", "review"}


def _reject_non_string_keys(doc: Dict[str, Any], context: str) -> None:
    """Reject dict keys that are not strings."""
    for key in doc:
        if not isinstance(key, str):
            raise ProtocolError(1040)


def _reject_float_types(obj: Any, context: str) -> None:
    """Reject any float values in a document structure."""
    if isinstance(obj, float):
        # Reject all floats including finite ones (protocol uses ints for numbers)
        raise ProtocolError(1041)
    elif isinstance(obj, dict):
        for v in obj.values():
            _reject_float_types(v, context)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            _reject_float_types(v, context)


def _unique_json_object(pairs: List[Tuple[str, Any]]) -> Dict[str, Any]:
    """Reject duplicate decoded keys before a JSON object becomes a dict."""
    result = {}
    for key, value in pairs:
        if key in result:
            raise ProtocolError(1050)
        result[key] = value
    return result


def _has_duplicate_keys(raw: bytes) -> bool:
    """Compatibility helper; the public parser also uses the same object hook."""
    try:
        json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_json_object)
    except ProtocolError as error:
        return error.code == 1050
    except (ValueError, UnicodeError, RecursionError):
        return False  # Malformed JSON is not evidence of a duplicate key.
    return False


def _detect_duplicate_keys_in_obj(obj: Any, seen: Optional[set] = None, depth: int = 0) -> bool:
    """Recursively detect cycles (shared references) in decoded Python dicts.

    Uses ancestor-based tracking per branch so that shared (non-cyclic)
    references across siblings are accepted; only true back-edges produce
    a cycle detection.
    Returns True if a cycle is detected.
    Uses depth limit to prevent RecursionError on deeply nested structures.
    """
    if depth > 100:
        raise ProtocolError(1051)
    if seen is None:
        seen = set()
    if isinstance(obj, dict):
        obj_id = id(obj)
        if obj_id in seen:
            return True  # cycle (back-edge)
        seen.add(obj_id)
        for v in obj.values():
            if _detect_duplicate_keys_in_obj(v, seen, depth + 1):
                return True
        seen.discard(obj_id)  # allow re-visiting from other branches
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            if _detect_duplicate_keys_in_obj(v, seen, depth + 1):
                return True
    return False
    """Validate that *obj* contains only JSON-compatible types.

    Supported: dict (string keys), list, str, int, bool, None.
    Rejected: float (all floats including finite), bytes, set, tuple, other objects.
    """
    if isinstance(obj, dict):
        for k in obj:
            if not isinstance(k, str):
                raise ProtocolError(1043)
        for v in obj.values():
            _validate_json_types(v)
    elif isinstance(obj, list):
        for v in obj:
            _validate_json_types(v)
    elif isinstance(obj, tuple):
        raise ProtocolError(1044)
    elif isinstance(obj, set):
        raise ProtocolError(1045)
    elif isinstance(obj, bytes):
        raise ProtocolError(1046)
    elif isinstance(obj, float):
        # Floats are not allowed in this protocol (numbers must be int)
        raise ProtocolError(1047)
    elif isinstance(obj, bool):
        # bool is valid JSON boolean - ok (checked before int because bool is subclass of int)
        pass
    elif isinstance(obj, int):
        # int is valid JSON number - ok
        pass
    elif obj is None:
        pass
    elif isinstance(obj, str):
        # Check for surrogate characters in string values/keys
        _check_surrogates_in_str(obj)
    else:
        raise ProtocolError(1048)


def _check_surrogates_in_str(s: str) -> None:
    """Python strings must contain Unicode scalars, not UTF-16 code units.

    A valid pair escaped in JSON is decoded by json.loads to one scalar and
    therefore passes this check without normalizing the caller's string.
    """
    if any(0xD800 <= ord(char) <= 0xDFFF for char in s):
        raise ProtocolError(1042)


def _reject_invalid_surrogates(text: str) -> None:
    """Check actual codepoints; literal JSON escape sequences are not repaired."""
    _check_surrogates_in_str(text)


def _validate_json_types(obj: Any) -> None:
    """Validate that *obj* contains only JSON-compatible types.

    Supported: dict (string keys), list, str, int, bool, None.
    Rejected: float (all floats including finite), bytes, set, tuple, other objects.
    """
    if isinstance(obj, dict):
        for k in obj:
            if not isinstance(k, str):
                raise ProtocolError(1043)
        for v in obj.values():
            _validate_json_types(v)
    elif isinstance(obj, list):
        for v in obj:
            _validate_json_types(v)
    elif isinstance(obj, tuple):
        raise ProtocolError(1044)
    elif isinstance(obj, set):
        raise ProtocolError(1045)
    elif isinstance(obj, bytes):
        raise ProtocolError(1046)
    elif isinstance(obj, float):
        # Floats are not allowed in this protocol (numbers must be int)
        raise ProtocolError(1047)
    elif isinstance(obj, bool):
        # bool is valid JSON boolean - ok (checked before int because bool is subclass of int)
        pass
    elif isinstance(obj, int):
        # int is valid JSON number - ok
        pass
    elif obj is None:
        pass
    elif isinstance(obj, str):
        # Check for surrogate characters in string values/keys
        _check_surrogates_in_str(obj)
    else:
        raise ProtocolError(1048)


def _validate_document_types(document: Dict[str, Any]) -> None:
    """Pre-validate document structure before deep processing.

    Checks JSON-compatible types, duplicate keys (for dicts passed directly),
    and cycles.  This runs before envelope/body validation to fail fast.
    """
    _validate_json_types_strict(document)


def _require_object(value: Any, keys: set, code: int) -> None:
    """Check the complete object shape before accessing any required field."""
    if not isinstance(value, dict) or set(value) != keys:
        raise ProtocolError(code)


def _require_string_list(value: Any, code: int) -> None:
    if not isinstance(value, list):
        raise ProtocolError(code)
    for item in value:
        if not isinstance(item, str) or not item:
            raise ProtocolError(code)


def _validate_required_fields(body: Any, fields: List[Tuple[str, str]], context: str) -> None:
    """Validate that *body* is a dict and all required fields exist with correct types.

    *fields* is a list of (field_name, type_name) pairs.  *type_name* must be one of:
    ``str``, ``int``, ``bool``, ``list``, ``dict``, ``hash`` (64-hex or null),
    ``utc`` (UTC string), ``id`` (ID string).

    Raises ProtocolError with stable codes on first violation.
    """
    if not isinstance(body, dict):
        raise ProtocolError(9100)
    for field_name, type_name in fields:
        if field_name not in body:
            raise ProtocolError(9101)
        value = body[field_name]
        if type_name == "str":
            if not isinstance(value, str):
                raise ProtocolError(9102)
        elif type_name == "int":
            if isinstance(value, bool) or not isinstance(value, int):
                raise ProtocolError(9103)
        elif type_name == "bool":
            if not isinstance(value, bool):
                raise ProtocolError(9104)
        elif type_name == "list":
            if not isinstance(value, list):
                raise ProtocolError(9105)
        elif type_name == "dict":
            if not isinstance(value, dict):
                raise ProtocolError(9106)
        elif type_name == "hash":
            # hash or null
            if value is not None:
                if not isinstance(value, str) or not _HASH_RE.fullmatch(value):
                    raise ProtocolError(9107)
        elif type_name == "utc":
            if not isinstance(value, str) or not _UTC_RE.fullmatch(value):
                raise ProtocolError(9108)
        elif type_name == "id":
            if not isinstance(value, str) or not _ID_RE.fullmatch(value):
                raise ProtocolError(9109)


def _validate_assignment_body(body: Dict[str, Any]) -> None:
    _require_object(body, _ASSIGNMENT_BODY_KEYS, 3001)

    # goal / source_contract: non-empty strings
    for field in ("goal", "source_contract"):
        val = body[field]
        if not isinstance(val, str) or not val:
            raise ProtocolError(3002)

    # allowlist: list of relative paths
    if not isinstance(body["allowlist"], list):
        raise ProtocolError(3003)
    for p in body["allowlist"]:
        _validate_relative_path(p, "allowlist")

    # checks: list of CheckDef (dict objects)
    if not isinstance(body["checks"], list):
        raise ProtocolError(3005)
    for c in body["checks"]:
        _validate_check_def(c)

    # acceptance: non-empty list of dicts
    if not isinstance(body["acceptance"], list) or len(body["acceptance"]) == 0:
        raise ProtocolError(3006)
    all_check_ids = {c["check_id"] for c in body["checks"]}
    for a in body["acceptance"]:
        _require_object(a, {"criterion_id", "description", "check_ids"}, 3007)
        _check_id(a["criterion_id"], "criterion_id")
        if not isinstance(a["description"], str) or not a["description"]:
            raise ProtocolError(3008)
        if not isinstance(a["check_ids"], list):
            raise ProtocolError(3009)
        # R-005: check_ids must reference declared checks
        for cid in a["check_ids"]:
            _check_id(cid, "check_ids")
            if cid not in all_check_ids:
                raise ProtocolError(3017)

    # dependencies: list of task IDs
    if not isinstance(body["dependencies"], list):
        raise ProtocolError(3010)
    for d in body["dependencies"]:
        _check_id(d, "dependency")

    # permissions: dict with specific bool fields
    perms = body["permissions"]
    _require_object(perms, {"fixture_git", "public_docs", "temp_artifacts", "cloud_calls"}, 3011)
    for field in ("fixture_git", "public_docs", "temp_artifacts"):
        if not isinstance(perms[field], bool):
            raise ProtocolError(3012)
    cc = perms["cloud_calls"]
    if isinstance(cc, bool) or not isinstance(cc, int) or cc < 0:
        raise ProtocolError(3013)

    # review_policy
    if not isinstance(body["review_policy"], str) or body["review_policy"] not in _VALID_REVIEW_POLICIES:
        raise ProtocolError(3014)

    # limits: dict with specific int fields >= 0
    limits = body["limits"]
    _require_object(limits, {"max_corrections", "max_calls", "call_timeout_seconds"}, 3015)
    for field in ("max_corrections", "max_calls"):
        if isinstance(limits[field], bool) or not isinstance(limits[field], int) or limits[field] < 0:
            raise ProtocolError(3016)
    ct = limits["call_timeout_seconds"]
    if isinstance(ct, bool) or not isinstance(ct, int) or ct <= 0:
        raise ProtocolError(3016)

    # profile_hash: hash or null (MANUAL -> null, CLOUD requires hash)
    if body["review_policy"] == "CLOUD":
        _check_hash(body["profile_hash"], "profile_hash", required=True)
    else:
        _check_hash(body["profile_hash"], "profile_hash")


def _validate_check_def(c: Any) -> None:
    _require_object(c, {"check_id", "argv", "cwd", "timeout_seconds", "allowed_writes"}, 3020)
    _check_id(c["check_id"], "check_id")
    if not isinstance(c["argv"], list) or len(c["argv"]) == 0:
        raise ProtocolError(3021)
    for a in c["argv"]:
        if not isinstance(a, str) or not a:
            raise ProtocolError(3022)
    # cwd: relative path (allows ".")
    if not isinstance(c["cwd"], str):
        raise ProtocolError(3023)
    _validate_relative_path(c["cwd"], "checkdef.cwd", allow_dot=True)
    if isinstance(c["timeout_seconds"], bool) or not isinstance(c["timeout_seconds"], int) or c["timeout_seconds"] <= 0:
        raise ProtocolError(3024)
    # allowed_writes: list of relative paths
    if not isinstance(c["allowed_writes"], list):
        raise ProtocolError(3025)
    for w in c["allowed_writes"]:
        _validate_relative_path(w, "allowed_writes")


# ---------------------------------------------------------------------------
# Implementation body validation
# ---------------------------------------------------------------------------

_IMPLEMENTATION_BODY_KEYS = {
    "summary", "changes", "criteria", "previous_findings", "limitations",
}


def _validate_implementation_body(body: Dict[str, Any]) -> None:
    _require_object(body, _IMPLEMENTATION_BODY_KEYS, 4001)

    # summary: non-empty string
    if not isinstance(body["summary"], str) or not body["summary"]:
        raise ProtocolError(4002)

    # changes: list of {path, before_hash, after_hash, change}
    if not isinstance(body["changes"], list):
        raise ProtocolError(4003)
    for ch in body["changes"]:
        _require_object(ch, {"path", "before_hash", "after_hash", "change"}, 4004)
        if not isinstance(ch["path"], str):
            raise ProtocolError(4005)
        # R-003: Validate relative path with traversal rejection
        _validate_relative_path(ch["path"], "changes.path")

        # Hash validation depends on change type (ADD: before=null, after=hash; DELETE: before=hash, after=null; MODIFY: both hash)
        if not isinstance(ch["change"], str) or ch["change"] not in ("ADD", "MODIFY", "DELETE"):
            raise ProtocolError(4006)

        if ch["change"] == "ADD":
            if ch["before_hash"] is not None:
                raise ProtocolError(4006)
            _check_hash(ch["after_hash"], "after_hash", required=True)
        elif ch["change"] == "DELETE":
            _check_hash(ch["before_hash"], "before_hash", required=True)
            if ch["after_hash"] is not None:
                raise ProtocolError(4006)
        else:  # MODIFY
            _check_hash(ch["before_hash"], "before_hash", required=True)
            _check_hash(ch["after_hash"], "after_hash", required=True)

    # criteria: list of Criterion
    if not isinstance(body["criteria"], list):
        raise ProtocolError(4007)
    for cr in body["criteria"]:
        _validate_criterion(cr)

    # previous_findings
    if not isinstance(body["previous_findings"], list):
        raise ProtocolError(4008)
    for pf in body["previous_findings"]:
        _require_object(pf, {"finding_id", "resolution", "explanation", "evidence_paths"}, 4009)
        _check_id(pf["finding_id"], "finding_id")
        if not isinstance(pf["resolution"], str) or pf["resolution"] not in ("ADDRESSED", "UNADDRESSED", "BLOCKED"):
            raise ProtocolError(4010)
        # explanation: non-empty string
        if not isinstance(pf["explanation"], str) or not pf["explanation"]:
            raise ProtocolError(4011)
        if not isinstance(pf["evidence_paths"], list):
            raise ProtocolError(4012)
        for ep in pf["evidence_paths"]:
            _validate_relative_path(ep, "previous_findings.evidence_paths")

    # limitations: list of non-empty strings
    _require_string_list(body["limitations"], 4014)


def _validate_criterion(cr: Any) -> None:
    _require_object(cr, {"criterion_id", "state", "evidence_paths", "note"}, 4020)
    _check_id(cr["criterion_id"], "criterion_id")
    # evidence_paths must be a list of paths, never string/dict (validated BEFORE state check)
    if not isinstance(cr["evidence_paths"], list):
        raise ProtocolError(4022)
    for ep in cr["evidence_paths"]:
        _validate_relative_path(ep, "criterion.evidence_paths")
    if not isinstance(cr["state"], str) or cr["state"] not in ("MET", "UNMET", "NOT_CHECKED"):
        raise ProtocolError(4021)
    if not isinstance(cr["note"], str) or len(cr["note"]) == 0:
        raise ProtocolError(4023)


# ---------------------------------------------------------------------------
# Checks body validation
# ---------------------------------------------------------------------------

_CHECKS_BODY_KEYS = {"results", "limitations"}


def _validate_checks_body(body: Dict[str, Any]) -> None:
    _require_object(body, _CHECKS_BODY_KEYS, 5001)

    if not isinstance(body["results"], list) or len(body["results"]) == 0:
        raise ProtocolError(5002)

    for r in body["results"]:
        _require_object(r, {
            "check_id", "argv", "cwd", "started_at", "finished_at",
            "elapsed_ms", "exit_code", "timed_out", "stdout_path",
            "stderr_path", "stdout_hash", "stderr_hash", "status",
        }, 5003)
        _check_id(r["check_id"], "check_id")
        _require_string_list(r["argv"], 5016)
        if not isinstance(r["cwd"], str):
            raise ProtocolError(5005)
        _validate_relative_path(r["cwd"], "checks.cwd", allow_dot=True)
        _check_utc(r["started_at"], "started_at")
        _check_utc(r["finished_at"], "finished_at")
        # elapsed_ms: int >= 0, bool not accepted as int
        if isinstance(r["elapsed_ms"], bool) or not isinstance(r["elapsed_ms"], int) or r["elapsed_ms"] < 0:
            raise ProtocolError(5006)

        if not isinstance(r["timed_out"], bool):
            raise ProtocolError(5008)

        # exit_code: int or null (if timed_out); bool not accepted as int
        ec = r["exit_code"]
        if isinstance(ec, bool):
            raise ProtocolError(5007)
        if ec is not None and not isinstance(ec, int):
            raise ProtocolError(5007)
        if r["timed_out"]:
            if ec is not None:
                raise ProtocolError(5009)
            if r["status"] != "TIMED_OUT":
                raise ProtocolError(5010)
        else:
            if ec is None:
                raise ProtocolError(5011)
            # non-timed_out: status must reflect exit_code
            if ec == 0 and r["status"] != "PASSED":
                raise ProtocolError(5014)
            elif ec != 0 and r["status"] != "FAILED":
                raise ProtocolError(5015)

        if not isinstance(r["status"], str) or r["status"] not in _VALID_CHECK_STATUSES:
            raise ProtocolError(5012)

        # stdout_path / stderr_path: relative paths
        _validate_relative_path(r["stdout_path"], "checks.stdout_path")
        _validate_relative_path(r["stderr_path"], "checks.stderr_path")

        # stdout_hash / stderr_hash: must not be null
        _check_hash(r["stdout_hash"], "stdout_hash", required=True)
        _check_hash(r["stderr_hash"], "stderr_hash", required=True)

    _require_string_list(body["limitations"], 5013)


# ---------------------------------------------------------------------------
# Review body validation
# ---------------------------------------------------------------------------

_REVIEW_BODY_KEYS = {"verdict", "criteria", "findings", "limitations"}


def _validate_review_body(body: Dict[str, Any]) -> None:
    _require_object(body, _REVIEW_BODY_KEYS, 6001)

    # verdict must be string
    if not isinstance(body["verdict"], str) or body["verdict"] not in _VALID_REVIEW_VERDICTS:
        raise ProtocolError(6002)

    # criteria
    if not isinstance(body["criteria"], list):
        raise ProtocolError(6003)
    for cr in body["criteria"]:
        _validate_criterion(cr)

    # findings
    if not isinstance(body["findings"], list):
        raise ProtocolError(6004)
    for f in body["findings"]:
        _require_object(f, {
            "finding_id", "severity", "path", "line",
            "message", "in_scope", "criterion_id",
        }, 6005)
        _check_id(f["finding_id"], "finding_id")
        if not isinstance(f["severity"], str) or f["severity"] not in ("LOW", "MEDIUM", "HIGH", "CRITICAL"):
            raise ProtocolError(6006)
        # path: relative or null (R-003)
        if f["path"] is not None:
            _validate_relative_path(f["path"], "findings.path")
        elif not isinstance(f["path"], type(None)):
            raise ProtocolError(6007)
        # line: int >= 1 or null, bool not accepted as int
        if isinstance(f["line"], bool) or f["line"] is not None and (not isinstance(f["line"], int) or f["line"] < 1):
            raise ProtocolError(6008)
        # message: non-empty string
        if not isinstance(f["message"], str) or not f["message"]:
            raise ProtocolError(6009)
        if not isinstance(f["in_scope"], bool):
            raise ProtocolError(6010)
        if f["criterion_id"] is not None:
            _check_id(f["criterion_id"], "criterion_id")

    # limitations: list of non-empty strings
    _require_string_list(body["limitations"], 6012)


# ---------------------------------------------------------------------------
# Close body validation
# ---------------------------------------------------------------------------

_CLOSE_BODY_KEYS = {
    "reason", "checks_handoff_id", "review_handoff_id",
    "accepted_evidence_hash", "pending_findings", "limitations",
}


def _validate_close_body(body: Dict[str, Any]) -> None:
    _require_object(body, _CLOSE_BODY_KEYS, 7001)

    # reason: non-empty string
    if not isinstance(body["reason"], str) or not body["reason"]:
        raise ProtocolError(7002)

    # checks_handoff_id / review_handoff_id: hash or null (BLOCKED -> null OK)
    _check_hash(body["checks_handoff_id"], "checks_handoff_id", required=False)
    _check_hash(body["review_handoff_id"], "review_handoff_id", required=False)

    # accepted_evidence_hash: non-null for COMPLETED, null allowed for BLOCKED
    _check_hash(body["accepted_evidence_hash"], "accepted_evidence_hash", required=False)

    # pending_findings: list of finding IDs (valid ID strings)
    if not isinstance(body["pending_findings"], list):
        raise ProtocolError(7003)
    for pf in body["pending_findings"]:
        if not isinstance(pf, str) or not _ID_RE.fullmatch(pf):
            raise ProtocolError(7005)

    # limitations: list of non-empty strings
    _require_string_list(body["limitations"], 7006)


# ---------------------------------------------------------------------------
# handoff_id helper
# ---------------------------------------------------------------------------

def handoff_id(document: Dict[str, Any]) -> str:
    """Compute handoff ID as SHA-256 of the full canonical document."""
    return sha256_json(document)


# ---------------------------------------------------------------------------
# parse_document: raw bytes -> parsed dict
# ---------------------------------------------------------------------------

def parse_document(raw: bytes, expected_identity: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Parse raw JSON bytes into a document dict.

    Raises ProtocolError on unparseable content or invalid types.
    If *expected_identity* is given, validates matching fields.
    """
    if not isinstance(raw, (bytes, bytearray)):
        raise ProtocolError(8002)
    try:
        text = raw.decode("utf-8")
    except (UnicodeDecodeError, ValueError):
        raise ProtocolError(8001) from None

    try:
        doc = json.loads(text, object_pairs_hook=_unique_json_object)
    except (ValueError, RecursionError):
        # Includes malformed JSON and the interpreter's integer/decoder limits,
        # without inventing new protocol caps or disclosing the input.
        raise ProtocolError(8001) from None

    validate_document(doc, expected_identity)
    return doc


# ---------------------------------------------------------------------------
# validate_document: dict -> validates in place (raises on error)
# ---------------------------------------------------------------------------

def validate_document(document: Dict[str, Any], expected_identity: Optional[Dict[str, Any]] = None) -> None:
    """Validate a parsed document. Raises ProtocolError on violation.

    If *expected_identity* is given, each present key must match exactly.
    """
    if not isinstance(document, dict):
        raise ProtocolError(1000)
    if expected_identity is not None and not isinstance(expected_identity, dict):
        raise ProtocolError(9070)
    # R-004: Validate all types, Unicode and actual cycles with one iterative pass.
    _validate_document_types(document)

    _validate_envelope(document)

    kind = document["kind"]
    body = document["body"]

    if not isinstance(body, dict):
        raise ProtocolError(9001)

    # Each validator checks its complete object shape before any field access.
    body_validators = {
        "assignment": _validate_assignment_body,
        "implementation": _validate_implementation_body,
        "checks": _validate_checks_body,
        "review": _validate_review_body,
        "close": _validate_close_body,
    }

    validator = body_validators.get(kind)
    if validator is None:
        raise ProtocolError(9002)

    validator(body)

    # R-001: Status consistency with kind
    status = document["status"]
    if kind == "assignment" and status != "ASSIGNED":
        raise ProtocolError(9010)
    if kind == "implementation" and status != "READY_FOR_REVIEW":
        raise ProtocolError(9011)

    # R-001: Review verdict must match status; APPROVED requires 0 findings + all criteria MET
    if kind == "review":
        if body["verdict"] not in _VALID_REVIEW_VERDICTS:
            raise ProtocolError(6002)
        if body["verdict"] == "APPROVED":
            if len(body.get("findings", [])) != 0:
                raise ProtocolError(9030)
            for cr in body.get("criteria", []):
                if cr.get("state") != "MET":
                    raise ProtocolError(9031)
        # verdict must equal status for review
        if body["verdict"] != status:
            raise ProtocolError(9032)

    # R-001: Close status coherence
    if kind == "close":
        if status not in ("COMPLETED", "BLOCKED"):
            raise ProtocolError(9060)
        if status == "COMPLETED":
            # COMPLETED requires non-null handoff refs and hash, empty pending
            if body.get("checks_handoff_id") is None or body.get("review_handoff_id") is None:
                raise ProtocolError(9040)
            if body.get("accepted_evidence_hash") is None:
                raise ProtocolError(9041)
            # accepted_evidence_hash must match envelope evidence_hash
            if body.get("accepted_evidence_hash") != document.get("evidence_hash"):
                raise ProtocolError(9043)
            if len(body.get("pending_findings", [])) != 0:
                raise ProtocolError(9042)
        elif status == "BLOCKED":
            # BLOCKED preserves nullabilities
            pass

    # R-001: Checks envelope status from results
    if kind == "checks":
        results = body.get("results", [])
        has_timed_out = any(r.get("timed_out") for r in results)
        all_passed = all(
            r.get("exit_code") == 0 and not r.get("timed_out")
            for r in results
        )
        if has_timed_out:
            if status != "TIMED_OUT":
                raise ProtocolError(9050)
        elif not all_passed:
            if status != "FAILED":
                raise ProtocolError(9051)
        else:
            # All passed: envelope must be PASSED
            if status != "PASSED":
                raise ProtocolError(9052)

    # R-005: identity check - reject unknown keys and enforce exact match semantics
    if expected_identity:
        # Reject keys not in the allowed set
        for key in expected_identity:
            if key not in _EXPECTED_IDENTITY_KEYS:
                raise ProtocolError(9021)
        # The context is a subset, not a replacement for the envelope schema.
        for key, expected_val in expected_identity.items():
            actual_val = document.get(key)
            if actual_val != expected_val:
                raise ProtocolError(9020)
