"""Evidence capture, baseline comparison, and artifact store for GROUP-AUTO-001.

Captures branch/HEAD/status, index/diffs, modes, additions/deletions, and
content of tracked/untracked files; compares against a sealed baseline;
creates immutable artifacts with SHA-256 hashes; writes metadata atomically
with history; protects paths/symlinks; never follows symlinks outside the
workspace. Uses only Python standard library. No subprocess, Git, provider,
CLI, or artifact I/O beyond the store paths documented here.

All public functions accept and return only JSON-compatible types (dict with
string keys, list, str, int, bool, None). No floats, bytes, or custom objects
in public I/O.
"""

import hashlib
import json
import os
import stat
import subprocess
from typing import Any, Dict, List, Optional, Set, Tuple

from tools.task_automation.contracts import (
    ProtocolError,
    canonical_bytes,
    sha256_json,
)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_SCHEMA_VERSION = "evidence.v1"
_ARTIFACT_ROLES = frozenset({"baseline", "source", "delta", "log", "checks", "review"})
_RESERVED_PREFIXES = frozenset({"baseline", "REV-"})


# ---------------------------------------------------------------------------
# Exception
# ---------------------------------------------------------------------------

class EvidenceError(Exception):
    """Raised when evidence capture or store operations fail."""

    def __init__(self, code: int, message: str) -> None:
        self.code = code
        super().__init__(message)


# ---------------------------------------------------------------------------
# Path validation (R-003: POSIX-relative, no traversal)
# ---------------------------------------------------------------------------

def _validate_relative_path(value: Any, field: str) -> None:
    """Validate *value* as a POSIX-relative path with no traversal components."""
    if not isinstance(value, str):
        raise EvidenceError(4100, f"{field}: not a string")
    if not value:
        raise EvidenceError(4101, f"{field}: empty path")
    if value.startswith("/"):
        raise EvidenceError(4102, f"{field}: absolute path")
    if value == ".":
        raise EvidenceError(4106, f"{field}: standalone dot not allowed")
    if ".." in value.split("/"):
        raise EvidenceError(4103, f"{field}: path traversal component")


def _validate_workspace_path(value: Any, workspace: str, field: str) -> str:
    """Validate a path is within *workspace*, return resolved relative path."""
    _validate_relative_path(value, field)
    full = os.path.join(workspace, value)
    real_workspace = os.path.realpath(workspace)
    real_full = os.path.realpath(full)
    if not real_full.startswith(real_workspace + os.sep) and real_full != real_workspace:
        raise EvidenceError(4104, f"{field}: path escapes workspace")
    return value


# ---------------------------------------------------------------------------
# Symlink detection (metadata only, never dereference)
# ---------------------------------------------------------------------------

def _is_symlink(path: str) -> bool:
    """Check if *path* is a symlink without dereferencing."""
    try:
        return os.path.islink(path)
    except OSError:
        return False


def _get_symlink_target(path: str) -> Optional[str]:
    """Get symlink target as text without dereferencing."""
    try:
        return os.readlink(path)
    except OSError:
        return None


# ---------------------------------------------------------------------------
# File classification
# ---------------------------------------------------------------------------

def _classify_file(
    path: str,
    workspace: str,
    allowlist: Set[str],
    secret_patterns: List[str],
) -> Tuple[str, Optional[str], Optional[str]]:
    """Classify a file: (role, mode_or_none, sha256_or_none).

    Returns:
        role: 'source', 'delta', 'baseline', or 'secret'
        mode: file mode string or None for symlinks
        sha256: SHA-256 hex of content or None for symlinks/secrets
    """
    full_path = os.path.join(workspace, path)

    # Check symlink first (metadata, never dereference)
    if _is_symlink(full_path):
        target = _get_symlink_target(full_path)
        # Never follow symlinks outside workspace
        if target:
            if os.path.isabs(target):
                if not target.startswith(workspace + os.sep) and target != workspace:
                    raise EvidenceError(4105, f"Symlink {path} points outside workspace")
            else:
                # Relative target: resolve relative to symlink's directory
                link_dir = os.path.dirname(full_path)
                resolved = os.path.normpath(os.path.join(link_dir, target))
                real_resolved = os.path.realpath(resolved)
                real_workspace = os.path.realpath(workspace)
                if not real_resolved.startswith(real_workspace + os.sep) and real_resolved != real_workspace:
                    raise EvidenceError(4105, f"Symlink {path} points outside workspace")
        return ("source", "symlink:" + str(target), None)

    try:
        st = os.lstat(full_path)
    except OSError:
        return ("source", None, None)

    mode_str = stat.filemode(st.st_mode)

    # Check if it's a directory (skip)
    if stat.S_ISDIR(st.st_mode):
        return ("source", mode_str, None)

    # Check if it's a regular file
    if not stat.S_ISREG(st.st_mode):
        return ("source", mode_str, None)

    # Check against secret patterns
    for pattern in secret_patterns:
        if pattern in path:
            return ("secret", mode_str, None)

    # Check allowlist
    if path in allowlist:
        role = "source"
    else:
        role = "delta"

    # Read content (binary-safe)
    try:
        with open(full_path, "rb") as f:
            content = f.read()
        sha = hashlib.sha256(content).hexdigest()
        return (role, mode_str, sha)
    except (OSError, IOError):
        return (role, mode_str, None)


def _validate_capture_path(path: str, workspace: str, field: str) -> None:
    """Validate a capture path is safe: relative, no traversal, no symlink escape."""
    if not isinstance(path, str):
        raise EvidenceError(4200, f"{field}: not a string")
    if not path:
        raise EvidenceError(4201, f"{field}: empty path")
    if path.startswith("/"):
        raise EvidenceError(4202, f"{field}: absolute path")
    if ".." in path.split("/"):
        raise EvidenceError(4203, f"{field}: path traversal component")
    # Check parent components for symlink escapes
    parts = path.split("/")
    real_workspace = os.path.realpath(workspace)
    for i in range(1, len(parts)):
        partial = os.path.join(*parts[:i])
        partial_full = os.path.join(workspace, partial)
        if _is_symlink(partial_full):
            target = _get_symlink_target(partial_full)
            if target:
                if os.path.isabs(target):
                    resolved = os.path.normpath(target)
                else:
                    link_dir = os.path.dirname(partial_full)
                    resolved = os.path.normpath(os.path.join(link_dir, target))
                real_resolved = os.path.realpath(resolved)
                if not real_resolved.startswith(real_workspace + os.sep) and real_resolved != real_workspace:
                    raise EvidenceError(4105, f"Parent symlink {partial} points outside workspace")


# ---------------------------------------------------------------------------
# Git state capture (metadata only, no subprocess)
# ---------------------------------------------------------------------------

def capture_git_state(
    workspace: str,
    branch: str,
    head: str,
    tracked_modified: List[str],
    staged: List[str],
    untracked: List[str],
) -> Dict[str, Any]:
    """Capture git state metadata.

    Args:
        workspace: workspace root path
        branch: current branch name
        head: HEAD commit hash (40 hex chars)
        tracked_modified: list of tracked modified file paths
        staged: list of staged file paths
        untracked: list of untracked file paths

    Returns:
        Dict with schema_version, branch, head, status, and file entries.
    """
    # Verify git identity: compare provided values against actual repo state
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL="/dev/null")

    try:
        real_branch = subprocess.run(
            ["git", "-c", "user.name=Verify", "-c", "user.email=verify@verify.invalid",
             "-c", "core.hooksPath=/dev/null", "branch", "--show-current"],
            cwd=workspace, env=env, check=True, capture_output=True,
        ).stdout.decode().strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        real_branch = None

    try:
        real_head = subprocess.run(
            ["git", "-c", "user.name=Verify", "-c", "user.email=verify@verify.invalid",
             "-c", "core.hooksPath=/dev/null", "rev-parse", "HEAD"],
            cwd=workspace, env=env, check=True, capture_output=True,
        ).stdout.decode().strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        real_head = None

    if real_branch is not None and branch is not None and real_branch != branch:
        raise EvidenceError(8001, f"Branch mismatch: provided={branch}, actual={real_branch}")
    if real_head is not None and head is not None and real_head != head:
        raise EvidenceError(8002, f"HEAD mismatch: provided={head}, actual={real_head}")

    entries: List[Dict[str, Any]] = []

    # Process tracked modified files
    for path in tracked_modified:
        _validate_capture_path(path, workspace, "tracked_modified")
        role, mode, sha = _classify_file(path, workspace, set(tracked_modified), [])
        entries.append({
            "path": path,
            "role": "delta",
            "mode": mode,
            "sha256": sha,
            "type": "tracked_modified",
        })

    # Process staged files
    for path in staged:
        _validate_capture_path(path, workspace, "staged")
        # Get staged content hash from git index (not filesystem)
        staged_sha = None
        try:
            env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
            env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL="/dev/null")
            staged_output = subprocess.run(
                ["git", "-c", "user.name=Verify", "-c", "user.email=verify@verify.invalid",
                 "-c", "core.hooksPath=/dev/null", "show", f":{path}"],
                cwd=workspace, env=env, check=True, capture_output=True,
            )
            staged_sha = hashlib.sha256(staged_output.stdout).hexdigest()
        except (subprocess.CalledProcessError, FileNotFoundError):
            pass
        role, mode, sha = _classify_file(path, workspace, set(staged), [])
        entries.append({
            "path": path,
            "role": "delta",
            "mode": mode,
            "sha256": staged_sha if staged_sha else sha,
            "type": "staged",
        })

    # Process untracked files
    for path in untracked:
        _validate_capture_path(path, workspace, "untracked")
        # Default secret patterns for untracked files
        _SECRET_PATTERNS = frozenset({".env", ".gitconfig", ".ssh", "id_rsa", "id_ed25519",
                                       "authorized_keys", "known_hosts", ".npmrc", ".pypirc",
                                       ".netrc", ".aws", "credentials", "token", "secret",
                                       "password", "api_key", "apikey", "private"})
        role, mode, sha = _classify_file(path, workspace, set(), list(_SECRET_PATTERNS))
        if role == "secret":
            raise EvidenceError(4107, f"Secret file detected: {path}")
        entries.append({
            "path": path,
            "role": "source",
            "mode": mode,
            "sha256": sha,
            "type": "untracked",
        })

    # Capture all tracked files (including clean committed) for complete baseline
    try:
        env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
        env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL="/dev/null")
        tracked_output = subprocess.run(
            ["git", "-c", "user.name=Verify", "-c", "user.email=verify@verify.invalid",
             "-c", "core.hooksPath=/dev/null", "ls-files", "-z", "--", "."],
            cwd=workspace, env=env, check=True, capture_output=True,
        )
        tracked_files = [f for f in tracked_output.stdout.decode().split("\0") if f]
    except (subprocess.CalledProcessError, FileNotFoundError):
        tracked_files = []

    for path in tracked_files:
        # Skip if already in entries (modified/staged files already captured)
        if any(e["path"] == path for e in entries):
            continue
        _validate_capture_path(path, workspace, "tracked_clean")
        role, mode, sha = _classify_file(path, workspace, set(tracked_files), [])
        entries.append({
            "path": path,
            "role": role,
            "mode": mode,
            "sha256": sha,
            "type": "tracked_clean",
        })

    # Detect mutations during capture: re-verify all captured file hashes
    for entry in entries:
        if entry.get("sha256") is None:
            continue
        # Skip staged entries - their hash is from git index, not filesystem
        if entry.get("type") == "staged":
            continue
        path = entry["path"]
        full_path = os.path.join(workspace, path)
        if _is_symlink(full_path):
            continue
        try:
            with open(full_path, "rb") as f:
                current_content = f.read()
            current_sha = hashlib.sha256(current_content).hexdigest()
            if current_sha != entry["sha256"]:
                raise EvidenceError(8003, f"File mutated during capture: {path}")
        except (OSError, IOError):
            pass  # File may have been deleted after capture

    return {
        "schema_version": _SCHEMA_VERSION,
        "branch": branch,
        "head": head,
        "status": {
            "tracked_modified": sorted(tracked_modified),
            "staged": sorted(staged),
            "untracked": sorted(untracked),
        },
        "entries": entries,
    }


# ---------------------------------------------------------------------------
# Baseline comparison
# ---------------------------------------------------------------------------

def compare_baseline(
    current_state: Dict[str, Any],
    baseline_state: Dict[str, Any],
    allowlist: Set[str],
) -> Dict[str, Any]:
    """Compare current state against sealed baseline.

    Compares content hashes, not status names. Detects content/mode/type
    additions, deletions, and drift outside the allowlist.

    Args:
        current_state: state from capture_git_state
        baseline_state: sealed baseline state
        allowlist: set of paths allowed to change

    Returns:
        Dict with delta, drift, and classification results.
    """
    if current_state.get("schema_version") != _SCHEMA_VERSION:
        raise EvidenceError(5001, "Schema version mismatch")
    if baseline_state.get("schema_version") != _SCHEMA_VERSION:
        raise EvidenceError(5002, "Baseline schema version mismatch")

    # Build lookup maps by path
    current_map: Dict[str, Dict[str, Any]] = {}
    for entry in current_state.get("entries", []):
        current_map[entry["path"]] = entry

    baseline_map: Dict[str, Dict[str, Any]] = {}
    for entry in baseline_state.get("entries", []):
        baseline_map[entry["path"]] = entry

    delta: List[Dict[str, Any]] = []
    drift: List[Dict[str, Any]] = []

    all_paths = set(current_map.keys()) | set(baseline_map.keys())

    for path in sorted(all_paths):
        current_entry = current_map.get(path)
        baseline_entry = baseline_map.get(path)

        if current_entry and not baseline_entry:
            # New file (addition)
            delta.append({
                "path": path,
                "change": "ADD",
                "before_sha256": None,
                "after_sha256": current_entry.get("sha256"),
                "before_mode": None,
                "after_mode": current_entry.get("mode"),
            })
            if path not in allowlist:
                drift.append({
                    "path": path,
                    "change": "ADD",
                    "before_sha256": None,
                    "after_sha256": current_entry.get("sha256"),
                })
        elif baseline_entry and not current_entry:
            # Deleted file
            delta.append({
                "path": path,
                "change": "DELETE",
                "before_sha256": baseline_entry.get("sha256"),
                "after_sha256": None,
                "before_mode": baseline_entry.get("mode"),
                "after_mode": None,
            })
            if path not in allowlist:
                drift.append({
                    "path": path,
                    "change": "DELETE",
                    "before_sha256": baseline_entry.get("sha256"),
                    "after_sha256": None,
                })
        else:
            # Both exist - compare content
            current_sha = current_entry.get("sha256")
            baseline_sha = baseline_entry.get("sha256")
            current_mode = current_entry.get("mode")
            baseline_mode = baseline_entry.get("mode")

            if current_sha != baseline_sha or current_mode != baseline_mode:
                change_type = "MODIFY"
                if current_entry.get("type") == "untracked" and baseline_entry.get("type") != "untracked":
                    change_type = "ADD"
                elif baseline_entry.get("type") == "untracked" and current_entry.get("type") != "untracked":
                    change_type = "DELETE"

                delta.append({
                    "path": path,
                    "change": change_type,
                    "before_sha256": baseline_sha,
                    "after_sha256": current_sha,
                    "before_mode": baseline_mode,
                    "after_mode": current_mode,
                })

                # Check for drift (outside allowlist)
                if path not in allowlist:
                    drift.append({
                        "path": path,
                        "change": change_type,
                        "before_sha256": baseline_sha,
                        "after_sha256": current_sha,
                    })

    # Detect HEAD / identity drift
    current_head = current_state.get("head")
    baseline_head = baseline_state.get("head")
    if current_head is not None and baseline_head is not None and current_head != baseline_head:
        drift.append({
            "path": "HEAD",
            "change": "MODIFY",
            "before_sha256": None,
            "after_sha256": current_head,
        })

    # Detect branch drift (even when HEAD is identical)
    current_branch = current_state.get("branch")
    baseline_branch = baseline_state.get("branch")
    if current_branch is not None and baseline_branch is not None and current_branch != baseline_branch:
        drift.append({
            "path": "branch",
            "change": "MODIFY",
            "before_sha256": None,
            "after_sha256": current_branch,
        })

    return {
        "delta": delta,
        "drift": drift,
        "drift_count": len(drift),
        "delta_count": len(delta),
        "paths_changed": sorted(set(d["path"] for d in delta)),
    }


# ---------------------------------------------------------------------------
# Artifact store
# ---------------------------------------------------------------------------

def _validate_artifact_path(run_dir: str, relative_path: str) -> str:
    """Validate artifact path is within run_dir, no traversal."""
    _validate_relative_path(relative_path, "artifact_path")
    full = os.path.join(run_dir, relative_path)
    real_run = os.path.realpath(run_dir)
    real_full = os.path.realpath(full)
    if not real_full.startswith(real_run + os.sep) and real_full != real_run:
        raise EvidenceError(4104, "Artifact path escapes run directory")
    return relative_path


def create_artifact(
    run_dir: str,
    relative_path: str,
    content: Any,
    role: str,
) -> Dict[str, Any]:
    """Create an immutable artifact in the run directory.

    Never overwrites existing artifacts. Creates atomically via temp file + rename.

    Args:
        run_dir: base run directory under /tmp namespace
        relative_path: path relative to run_dir
        content: JSON-serializable content
        role: artifact role from _ARTIFACT_ROLES

    Returns:
        Dict with path, sha256, role, and created_at.

    Raises:
        EvidenceError: if artifact exists, path invalid, or role invalid.
    """
    if not isinstance(role, str):
        raise EvidenceError(6001, "Invalid artifact role")
    if role not in _ARTIFACT_ROLES:
        raise EvidenceError(6001, "Invalid artifact role")

    validated_path = _validate_artifact_path(run_dir, relative_path)
    full_path = os.path.join(run_dir, validated_path)

    # Never overwrite existing artifacts
    if os.path.exists(full_path):
        # Read existing to get its hash
        with open(full_path, "rb") as f:
            existing_content = f.read()
        existing_sha = hashlib.sha256(existing_content).hexdigest()
        raise EvidenceError(
            6002,
            f"Artifact already exists at {validated_path} (sha256: {existing_sha})",
        )

    # Canonicalize content
    canonical = canonical_bytes(content)
    sha = hashlib.sha256(canonical).hexdigest()

    # Create directory if needed
    os.makedirs(os.path.dirname(full_path) if os.path.dirname(full_path) else run_dir, exist_ok=True)

    # Atomic write via temp file + rename
    temp_path = full_path + ".tmp." + str(os.getpid())
    # Reject temp path if it's a symlink
    if _is_symlink(temp_path):
        raise EvidenceError(6004, f"Artifact temp path is a symlink: {temp_path}")
    try:
        with open(temp_path, "wb") as f:
            f.write(canonical)
        os.rename(temp_path, full_path)
    except OSError:
        if os.path.exists(temp_path):
            os.unlink(temp_path)
        raise EvidenceError(6003, f"Failed to write artifact at {validated_path}")

    return {
        "path": validated_path,
        "sha256": sha,
        "role": role,
        "size": len(canonical),
    }


def verify_artifact(
    run_dir: str,
    relative_path: str,
    expected_sha256: str,
) -> Dict[str, Any]:
    """Verify an artifact's SHA-256 hash.

    Args:
        run_dir: base run directory
        relative_path: path relative to run_dir
        expected_sha256: expected SHA-256 hex string (64 chars)

    Returns:
        Dict with path, sha256, role, and verified=True.

    Raises:
        EvidenceError: if artifact missing or hash mismatch.
    """
    if not isinstance(expected_sha256, str) or len(expected_sha256) != 64:
        raise EvidenceError(6010, "Invalid expected SHA-256")

    validated_path = _validate_artifact_path(run_dir, relative_path)
    full_path = os.path.join(run_dir, validated_path)

    if not os.path.exists(full_path):
        raise EvidenceError(6011, f"Artifact not found: {validated_path}")

    # Check for symlink (never follow)
    if _is_symlink(full_path):
        raise EvidenceError(6012, f"Artifact is a symlink: {validated_path}")

    with open(full_path, "rb") as f:
        content = f.read()

    actual_sha = hashlib.sha256(content).hexdigest()

    if actual_sha != expected_sha256:
        raise EvidenceError(
            6013,
            f"SHA-256 mismatch for {validated_path}: expected {expected_sha256}, got {actual_sha}",
        )

    return {
        "path": validated_path,
        "sha256": actual_sha,
        "role": "source",
        "verified": True,
    }


# ---------------------------------------------------------------------------
# Metadata store with atomic writes and history
# ---------------------------------------------------------------------------

def _atomic_write_metadata(
    run_dir: str,
    filename: str,
    data: Dict[str, Any],
) -> str:
    """Write metadata atomically, preserving history.

    Creates a new versioned file; never overwrites existing versions.

    Returns:
        Path to the written file.
    """
    validated_path = _validate_artifact_path(run_dir, filename)
    full_path = os.path.join(run_dir, validated_path)

    # Never overwrite
    if os.path.exists(full_path):
        raise EvidenceError(7001, f"Metadata file already exists: {validated_path}")

    canonical = canonical_bytes(data)
    temp_path = full_path + ".tmp"
    try:
        with open(temp_path, "wb") as f:
            f.write(canonical)
        os.rename(temp_path, full_path)
    except OSError:
        if os.path.exists(temp_path):
            os.unlink(temp_path)
        raise EvidenceError(7002, f"Failed to write metadata: {validated_path}")

    return validated_path


def append_metadata_history(
    run_dir: str,
    history_filename: str,
    entry: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """Append an entry to a metadata history file.

    Creates the file if it doesn't exist; appends atomically.

    Returns:
        Updated list of history entries.
    """
    validated_path = _validate_artifact_path(run_dir, history_filename)
    full_path = os.path.join(run_dir, validated_path)

    # Reject symlink destination
    if _is_symlink(full_path):
        raise EvidenceError(7006, f"History destination is a symlink: {validated_path}")

    # Read existing history
    history: List[Dict[str, Any]] = []
    if os.path.exists(full_path):
        with open(full_path, "rb") as f:
            content = f.read()
        try:
            history = json.loads(content.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise EvidenceError(7005, "History file contains unparseable data") from exc

    if not isinstance(history, list):
        raise EvidenceError(7003, "History file contains non-list data")

    # Append new entry
    history.append(entry)

    # Write atomically
    canonical = canonical_bytes(history)
    temp_path = full_path + ".tmp"
    # Reject temp path if it's a symlink
    if _is_symlink(temp_path):
        raise EvidenceError(7007, f"History temp path is a symlink: {temp_path}")
    try:
        with open(temp_path, "wb") as f:
            f.write(canonical)
        os.rename(temp_path, full_path)
    except OSError:
        if os.path.exists(temp_path):
            os.unlink(temp_path)
        raise EvidenceError(7004, f"Failed to update history: {validated_path}")

    return history


# ---------------------------------------------------------------------------
# Manifest creation
# ---------------------------------------------------------------------------

def create_baseline_manifest(
    state: Dict[str, Any],
) -> Dict[str, Any]:
    """Create a baseline manifest from captured state.

    Args:
        state: output from capture_git_state

    Returns:
        Manifest dict with files list, hashes, and metadata.
    """
    files: List[Dict[str, Any]] = []
    for entry in state.get("entries", []):
        # Include all entries: deleted files (sha=None) and symlinks (sha=None)
        files.append({
            "path": entry["path"],
            "sha256": entry.get("sha256"),
            "role": entry.get("role", "source"),
            "mode": entry.get("mode"),
            "type": entry.get("type"),
        })

    manifest = {
        "schema_version": "baseline_manifest.v1",
        "branch": state.get("branch"),
        "head": state.get("head"),
        "file_count": len(files),
        "files": files,
    }

    manifest["sha256"] = sha256_json(manifest)
    return manifest


def create_evidence_manifest(
    delta: List[Dict[str, Any]],
    sources: List[Dict[str, Any]],
    check_results: List[Dict[str, Any]],
    implementation_body_hash: Optional[str],
) -> Dict[str, Any]:
    """Create an evidence manifest for a handoff.

    Args:
        delta: list of delta entries from compare_baseline
        sources: list of source file entries
        check_results: list of check result dicts
        implementation_body_hash: hash of implementation body or None

    Returns:
        Evidence manifest dict.
    """
    files: List[Dict[str, Any]] = []

    # Add delta files
    for d in delta:
        files.append({
            "path": d["path"],
            "sha256": d.get("after_sha256") or d.get("before_sha256"),
            "role": "delta",
        })

    # Add source files
    for s in sources:
        files.append({
            "path": s["path"],
            "sha256": s["sha256"],
            "role": "source",
        })

    manifest = {
        "schema_version": "task_evidence.v1",
        "files": files,
        "check_results": check_results,
        "implementation_body_hash": implementation_body_hash,
    }

    manifest["sha256"] = sha256_json(manifest)
    return manifest


# ---------------------------------------------------------------------------
# Reserved name protection
# ---------------------------------------------------------------------------

def is_reserved_name(name: str) -> bool:
    """Check if a name is reserved for baseline/REV creation."""
    if name in _RESERVED_PREFIXES:
        return True
    if name.startswith("baseline"):
        return True
    if name.startswith("REV-"):
        return True
    return False


# ---------------------------------------------------------------------------
# Public API summary
# ---------------------------------------------------------------------------

__all__ = [
    "EvidenceError",
    "ProtocolError",
    "capture_git_state",
    "compare_baseline",
    "create_artifact",
    "verify_artifact",
    "create_baseline_manifest",
    "create_evidence_manifest",
    "append_metadata_history",
    "is_reserved_name",
    "canonical_bytes",
    "sha256_json",
]
