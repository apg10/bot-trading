"""ACT-AUTO-002: real isolated fixtures, public API, and fail-closed regressions.

No application workspace, credentials, services or remotes are touched. Red
regressions describe unfinished requirements; they are not skips or xfails.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import unittest
from unittest.mock import MagicMock, patch
import uuid

from tools.task_automation import evidence as ev
from tools.task_automation.contracts import canonical_bytes, sha256_json


ROOT = Path("/tmp/opencode/bot-trading-automation/GROUP-AUTO-001/ACT-AUTO-002")


class EvidenceFixture(unittest.TestCase):
    """Each test owns one valid run namespace, including its throwaway Git repo."""

    def setUp(self):
        self.run = ROOT / ("RUN-TEST-" + uuid.uuid4().hex.upper())
        self.run.mkdir(parents=True, exist_ok=False)
        self.addCleanup(shutil.rmtree, self.run)
        self.repo = self.run / "workspace"
        self.repo.mkdir()
        self.git("init", "--quiet", "--initial-branch=master", "--template=")
        self.write("tracked.txt", b"committed baseline\n")
        self.git("add", "--", "tracked.txt")
        self.git("commit", "--quiet", "-m", "Fixture baseline")

    def git(self, *args):
        env = dict(os.environ)
        # Do not inherit an alternate index, worktree, hooks or user identity.
        for key in list(env):
            if key.startswith("GIT_"):
                del env[key]
        env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL="/dev/null")
        command = [
            "git", "-c", "user.name=Fixture", "-c",
            "user.email=fixture@example.invalid", "-c", "core.hooksPath=/dev/null",
            *args,
        ]
        return subprocess.run(
            command, cwd=self.repo, env=env, check=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        ).stdout

    def write(self, name, data):
        target = self.repo / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        return target

    def capture(self):
        def paths(*args):
            return [os.fsdecode(p) for p in self.git(*args).split(b"\0") if p]

        return ev.capture_git_state(
            str(self.repo), self.git("branch", "--show-current").decode().strip(),
            self.git("rev-parse", "HEAD").decode().strip(),
            paths("diff", "--name-only", "-z", "--no-ext-diff", "--no-textconv"),
            paths("diff", "--cached", "--name-only", "-z", "--no-ext-diff", "--no-textconv"),
            paths("ls-files", "--others", "--exclude-standard", "-z"),
        )

    def entry(self, state, name):
        matches = [row for row in state["entries"] if row["path"] == name]
        self.assertTrue(matches, name)
        return matches[-1]

    def artifact(self, name="data.json", payload=None, role="source"):
        return ev.create_artifact(
            str(self.run), name, {"value": 1} if payload is None else payload, role,
        )

    def assert_drift(self, current, baseline, allowed=()):
        report = ev.compare_baseline(current, baseline, set(allowed))
        self.assertGreater(report["drift_count"], 0, report)
        return report


class TestCapture(EvidenceFixture):
    def test_real_git_identity(self):
        state = self.capture()
        self.assertEqual(state["branch"], "master")
        self.assertEqual(state["head"], self.git("rev-parse", "HEAD").decode().strip())
        self.assertEqual(len(state["head"]), 40)

    def test_modified_binary_and_space_named_files(self):
        self.write("tracked.txt", b"\x00\xff\x80\n")
        self.write("folder with spaces/new file.bin", b"\x00\xfe")
        state = self.capture()
        self.assertEqual(self.entry(state, "tracked.txt")["sha256"], hashlib.sha256(b"\x00\xff\x80\n").hexdigest())
        self.assertEqual(self.entry(state, "folder with spaces/new file.bin")["sha256"], hashlib.sha256(b"\x00\xfe").hexdigest())

    def test_clean_baseline_includes_committed_file(self):
        state = self.capture()
        self.assertEqual(state["status"]["tracked_modified"], [])
        self.assertEqual(self.entry(state, "tracked.txt")["sha256"], hashlib.sha256(b"committed baseline\n").hexdigest())

    def test_staged_and_worktree_versions_are_not_collapsed(self):
        self.write("tracked.txt", b"staged bytes")
        self.git("add", "--", "tracked.txt")
        self.write("tracked.txt", b"working bytes")
        state = self.capture()
        self.assertIn("tracked.txt", state["status"]["staged"])
        self.assertIn("tracked.txt", state["status"]["tracked_modified"])
        hashes = {row.get("sha256") for row in state["entries"] if row["path"] == "tracked.txt"}
        self.assertIn(hashlib.sha256(b"staged bytes").hexdigest(), hashes)
        self.assertIn(hashlib.sha256(b"working bytes").hexdigest(), hashes)

    def test_fabricated_git_identity_is_rejected(self):
        self.capture()  # A valid fixture is usable before changing identity.
        try:
            state = ev.capture_git_state(str(self.repo), "fabricated", "f" * 40, [], [], [])
        except ev.EvidenceError:
            return
        # Re-reading the real identity is also safe; echoing invented facts is not.
        self.assertEqual(state["branch"], "master")
        self.assertEqual(state["head"], self.git("rev-parse", "HEAD").decode().strip())

    def test_deleted_file_is_not_silently_omitted_from_manifest(self):
        (self.repo / "tracked.txt").unlink()
        state = self.capture()
        self.assertIn("tracked.txt", state["status"]["tracked_modified"])
        manifest = ev.create_baseline_manifest(state)
        self.assertIn("tracked.txt", {row["path"] for row in manifest["files"]})

    def test_relative_symlink_is_metadata_only(self):
        target = self.write("target.bin", b"not dereferenced")
        link = self.repo / "link.bin"
        link.symlink_to(target.name)
        state = self.capture()
        row = self.entry(state, "link.bin")
        self.assertIsNone(row["sha256"])
        self.assertIn("target.bin", row["mode"])
        manifest = ev.create_baseline_manifest(state)
        self.assertIn("link.bin", {item["path"] for item in manifest["files"]})

    def test_parent_symlink_cannot_escape_workspace(self):
        outside = self.run / "outside"
        outside.mkdir()
        (outside / "data.bin").write_bytes(b"outside workspace")
        (self.repo / "bridge").symlink_to(outside, target_is_directory=True)
        with self.assertRaises(ev.EvidenceError):
            ev.capture_git_state(
                str(self.repo), "master", self.git("rev-parse", "HEAD").decode().strip(),
                [], [], ["bridge/data.bin"],
            )

    def test_sensitive_inventory_blocks_before_content_read(self):
        secret = self.write(".env", b"SYNTHETIC_SECRET=not-a-real-credential")
        real_open = open

        def audited_open(file, *args, **kwargs):
            if isinstance(file, (str, bytes, os.PathLike)):
                self.assertNotEqual(os.fsdecode(file), str(secret), "Secret content was read")
            return real_open(file, *args, **kwargs)

        with patch("builtins.open", side_effect=audited_open):
            with self.assertRaises(ev.EvidenceError):
                self.capture()

    def test_mutation_during_capture_is_not_sealed(self):
        target = self.write("tracked.txt", b"first version")
        original_hash = hashlib.sha256
        changed = False

        def mutate_after_read(data=b"", *args, **kwargs):
            nonlocal changed
            if data == b"first version" and not changed:
                changed = True
                target.write_bytes(b"second version")
            return original_hash(data, *args, **kwargs)

        with patch.object(ev.hashlib, "sha256", side_effect=mutate_after_read):
            with self.assertRaises(ev.EvidenceError):
                self.capture()
        self.assertTrue(changed, "The real filesystem mutation must be injected")


class TestCompare(EvidenceFixture):
    def test_unchanged_state_has_no_delta(self):
        self.write("tracked.txt", b"initial dirty version")
        baseline = self.capture()
        report = ev.compare_baseline(copy.deepcopy(baseline), baseline, set())
        self.assertEqual(report["delta_count"], 0)
        self.assertEqual(report["drift_count"], 0)

    def test_allowed_modification_preserves_preexisting_change(self):
        self.write("tracked.txt", b"preexisting dirty version")
        baseline = self.capture()
        self.write("tracked.txt", b"task-owned version")
        report = ev.compare_baseline(self.capture(), baseline, {"tracked.txt"})
        self.assertEqual(report["delta_count"], 1)
        self.assertEqual(report["drift_count"], 0)
        self.assertEqual(report["delta"][0]["before_sha256"], hashlib.sha256(b"preexisting dirty version").hexdigest())

    def test_foreign_modification_is_drift(self):
        self.write("foreign.txt", b"old")
        baseline = self.capture()
        self.write("foreign.txt", b"new")
        report = self.assert_drift(self.capture(), baseline)
        self.assertEqual(report["drift"][0]["path"], "foreign.txt")

    def test_foreign_addition_is_drift(self):
        baseline = self.capture()
        self.write("foreign.txt", b"new")
        self.assert_drift(self.capture(), baseline)

    def test_foreign_deletion_is_drift(self):
        target = self.write("foreign.txt", b"old")
        baseline = self.capture()
        target.unlink()
        self.assert_drift(self.capture(), baseline)

    def test_rename_is_add_delete_and_drift(self):
        old = self.write("old name.txt", b"old")
        baseline = self.capture()
        old.rename(self.repo / "new name.txt")
        report = self.assert_drift(self.capture(), baseline)
        self.assertEqual(set(report["paths_changed"]), {"old name.txt", "new name.txt"})

    def test_mode_only_change_is_detected(self):
        target = self.write("executable.txt", b"same content")
        baseline = self.capture()
        target.chmod(target.stat().st_mode | stat.S_IXUSR)
        self.assert_drift(self.capture(), baseline)

    def test_git_identity_drift_is_not_ignored(self):
        baseline = self.capture()
        current = copy.deepcopy(baseline)
        current["head"] = "f" * 40
        try:
            report = ev.compare_baseline(current, baseline, set())
        except ev.EvidenceError:
            return
        self.assertGreater(report["drift_count"], 0, report)


class TestArtifacts(EvidenceFixture):
    def test_create_and_verify_canonical_content(self):
        payload = {"z": [True, None], "a": "café"}
        descriptor = self.artifact(payload=payload)
        self.assertEqual((self.run / "data.json").read_bytes(), canonical_bytes(payload))
        self.assertEqual(descriptor["sha256"], hashlib.sha256(canonical_bytes(payload)).hexdigest())
        result = ev.verify_artifact(str(self.run), "data.json", descriptor["sha256"])
        self.assertTrue(result["verified"])

    def test_existing_artifact_never_overwrites_even_same_content(self):
        descriptor = self.artifact()
        before = (self.run / "data.json").read_bytes()
        for payload in ({"value": 1}, {"value": 2}):
            with self.subTest(payload=payload):
                with self.assertRaises(ev.EvidenceError):
                    self.artifact(payload=payload)
                self.assertEqual((self.run / "data.json").read_bytes(), before)
        ev.verify_artifact(str(self.run), "data.json", descriptor["sha256"])

    def test_baseline_and_revision_paths_are_exclusive(self):
        for name in ("baseline/manifest.json", "REV-001/manifest.json", "REV-002/manifest.json"):
            with self.subTest(name=name):
                descriptor = self.artifact(name, role="baseline")
                with self.assertRaises(ev.EvidenceError):
                    self.artifact(name, role="baseline")
                self.assertTrue(ev.verify_artifact(str(self.run), name, descriptor["sha256"])["verified"])

    def test_tampering_is_detected(self):
        descriptor = self.artifact()
        (self.run / "data.json").write_bytes(b"tampered")
        with self.assertRaises(ev.EvidenceError):
            ev.verify_artifact(str(self.run), "data.json", descriptor["sha256"])

    def test_missing_artifact_is_error(self):
        with self.assertRaises(ev.EvidenceError):
            ev.verify_artifact(str(self.run), "missing.json", "a" * 64)

    def test_absolute_empty_and_traversal_paths_are_errors(self):
        for name in ("", ".", "/etc/blocked", "../escape", "nested/../../escape"):
            with self.subTest(name=name):
                with self.assertRaises(ev.EvidenceError):
                    self.artifact(name)

    def test_verify_rejects_symlink_without_reading_target(self):
        target = self.run / "target.json"
        target.write_bytes(b"fake sensitive content")
        (self.run / "alias.json").symlink_to(target.name)
        with self.assertRaises(ev.EvidenceError):
            ev.verify_artifact(str(self.run), "alias.json", hashlib.sha256(target.read_bytes()).hexdigest())

    def test_parent_symlink_escape_cannot_write(self):
        # Every path belongs to this test, but the target is outside its run.
        other = ROOT / ("RUN-OUTSIDE-" + uuid.uuid4().hex.upper())
        other.mkdir()
        self.addCleanup(shutil.rmtree, other)
        (self.run / "bridge").symlink_to(other, target_is_directory=True)
        with self.assertRaises(ev.EvidenceError):
            self.artifact("bridge/escaped.json")
        self.assertFalse((other / "escaped.json").exists())

    def test_temporary_symlink_cannot_modify_immutable_artifact(self):
        self.artifact("protected.json", {"protected": True})
        protected = self.run / "protected.json"
        before = protected.read_bytes()
        (self.run / ("new.json.tmp." + str(os.getpid()))).symlink_to(protected.name)
        try:
            self.artifact("new.json", {"replacement": True})
        except ev.EvidenceError:
            pass  # Rejection or a safe independent temp strategy is acceptable.
        self.assertEqual(protected.read_bytes(), before)

    def test_role_error_does_not_echo_private_value(self):
        marker = "SYNTHETIC-PRIVATE-MARKER"
        with self.assertRaises(ev.EvidenceError) as caught:
            self.artifact(role=marker)
        self.assertNotIn(marker, str(caught.exception))

    def test_invalid_roles_have_stable_errors(self):
        for role in ([], {}, None, 123):
            with self.subTest(role=role):
                with self.assertRaises(ev.EvidenceError):
                    self.artifact(role=role)

    def test_interrupted_publication_is_not_success(self):
        with patch.object(ev.os, "rename", side_effect=OSError("synthetic publication error")) as renamed:
            with self.assertRaises(ev.EvidenceError):
                self.artifact()
        self.assertTrue(renamed.called, "Failure must be injected in the publication operation")
        self.assertFalse((self.run / "data.json").exists())


class TestMetadata(EvidenceFixture):
    def test_history_preserves_prior_entries(self):
        first = {"revision": "REV-001", "state": "pending"}
        second = {"revision": "REV-002", "state": "pending"}
        self.assertEqual(ev.append_metadata_history(str(self.run), "history.json", first), [first])
        self.assertEqual(ev.append_metadata_history(str(self.run), "history.json", second), [first, second])
        self.assertEqual(json.loads((self.run / "history.json").read_bytes()), [first, second])

    def test_history_publication_error_keeps_previous_bytes(self):
        ev.append_metadata_history(str(self.run), "history.json", {"revision": "REV-001"})
        before = (self.run / "history.json").read_bytes()
        with patch.object(ev.os, "rename", side_effect=OSError("synthetic publication error")) as renamed:
            with self.assertRaises(ev.EvidenceError):
                ev.append_metadata_history(str(self.run), "history.json", {"revision": "REV-002"})
        self.assertTrue(renamed.called)
        self.assertEqual((self.run / "history.json").read_bytes(), before)

    def test_corrupt_history_has_stable_error(self):
        (self.run / "history.json").write_bytes(b"{malformed")
        with self.assertRaises(ev.EvidenceError):
            ev.append_metadata_history(str(self.run), "history.json", {"revision": "REV-001"})

    def test_non_list_history_is_error(self):
        (self.run / "history.json").write_bytes(b"{}")
        with self.assertRaises(ev.EvidenceError):
            ev.append_metadata_history(str(self.run), "history.json", {"revision": "REV-001"})

    def test_history_destination_symlink_is_rejected(self):
        self.artifact("protected.json", [{"protected": True}])
        (self.run / "history.json").symlink_to("protected.json")
        with self.assertRaises(ev.EvidenceError):
            ev.append_metadata_history(str(self.run), "history.json", {"revision": "REV-001"})

    def test_history_temp_symlink_does_not_overwrite_an_artifact(self):
        self.artifact("protected.json", {"protected": True})
        protected = self.run / "protected.json"
        before = protected.read_bytes()
        (self.run / "history.json.tmp").symlink_to(protected.name)
        try:
            ev.append_metadata_history(str(self.run), "history.json", {"revision": "REV-001"})
        except ev.EvidenceError:
            pass
        self.assertEqual(protected.read_bytes(), before)


class TestManifests(EvidenceFixture):
    def test_baseline_digest_matches_canonical_body(self):
        self.write("tracked.txt", b"dirty content")
        manifest = ev.create_baseline_manifest(self.capture())
        body = copy.deepcopy(manifest)
        declared = body.pop("sha256")
        self.assertEqual(declared, sha256_json(body))
        self.assertEqual(manifest["file_count"], len(manifest["files"]))
        self.assertIn("tracked.txt", {item["path"] for item in manifest["files"]})

    def test_mutation_changes_manifest_digest(self):
        self.write("tracked.txt", b"first")
        first = ev.create_baseline_manifest(self.capture())
        self.write("tracked.txt", b"second")
        second = ev.create_baseline_manifest(self.capture())
        self.assertNotEqual(first["sha256"], second["sha256"])

    def test_manifest_artifact_tampering_is_detected(self):
        manifest = ev.create_baseline_manifest(self.capture())
        descriptor = self.artifact("baseline.json", manifest, "baseline")
        ev.verify_artifact(str(self.run), "baseline.json", descriptor["sha256"])
        (self.run / "baseline.json").write_bytes(b"{}")
        with self.assertRaises(ev.EvidenceError):
            ev.verify_artifact(str(self.run), "baseline.json", descriptor["sha256"])

    def test_evidence_digest_has_no_self_reference(self):
        manifest = ev.create_evidence_manifest(
            [{"path": "a.txt", "after_sha256": "a" * 64, "before_sha256": None}],
            [{"path": "b.txt", "sha256": "b" * 64}], [], "c" * 64,
        )
        body = copy.deepcopy(manifest)
        declared = body.pop("sha256")
        self.assertEqual(declared, sha256_json(body))
        self.assertEqual({row["role"] for row in manifest["files"]}, {"delta", "source"})

    def test_reserved_name_helper(self):
        for name in ("baseline", "baseline.json", "REV-001"):
            self.assertTrue(ev.is_reserved_name(name))
        self.assertFalse(ev.is_reserved_name("source.json"))


class TestCapturePathBoundaries(EvidenceFixture):
    """S5A: validate capture paths reject traversal, absolute, and symlink escapes."""

    def test_traversal_in_tracked_modified_is_rejected(self):
        self.write("safe.txt", b"ok")
        with self.assertRaises(ev.EvidenceError) as ctx:
            ev.capture_git_state(
                str(self.repo), "master", self.git("rev-parse", "HEAD").decode().strip(),
                ["../outside.bin"], [], [],
            )
        self.assertIn("path traversal", str(ctx.exception))

    def test_traversal_in_staged_is_rejected(self):
        self.write("safe.txt", b"ok")
        with self.assertRaises(ev.EvidenceError) as ctx:
            ev.capture_git_state(
                str(self.repo), "master", self.git("rev-parse", "HEAD").decode().strip(),
                [], ["../outside.bin"], [],
            )
        self.assertIn("path traversal", str(ctx.exception))

    def test_traversal_in_untracked_is_rejected(self):
        self.write("safe.txt", b"ok")
        with self.assertRaises(ev.EvidenceError) as ctx:
            ev.capture_git_state(
                str(self.repo), "master", self.git("rev-parse", "HEAD").decode().strip(),
                [], [], ["../outside.bin"],
            )
        self.assertIn("path traversal", str(ctx.exception))

    def test_absolute_path_in_untracked_is_rejected(self):
        self.write("safe.txt", b"ok")
        with self.assertRaises(ev.EvidenceError) as ctx:
            ev.capture_git_state(
                str(self.repo), "master", self.git("rev-parse", "HEAD").decode().strip(),
                [], [], ["/etc/passwd"],
            )
        self.assertIn("absolute path", str(ctx.exception))

    def test_tracked_parent_symlink_escape_is_rejected(self):
        """Parent symlink pointing outside workspace must be rejected."""
        outside = self.run / "outside"
        outside.mkdir(exist_ok=True)
        (outside / "outside.bin").write_bytes(b"outside")
        (self.repo / "link").symlink_to(outside, target_is_directory=True)
        # Mock git branch/head to return valid values, then ls-files returns path through symlink
        head = self.git("rev-parse", "HEAD").decode().strip()
        with patch.object(ev.subprocess, "run") as mock_run:
            def side_effect(*args, **kwargs):
                cmd = args[0] if args else kwargs.get("args", [])
                if "branch" in cmd:
                    return MagicMock(stdout=b"master\n", returncode=0)
                if "rev-parse" in cmd:
                    return MagicMock(stdout=head.encode() + b"\n", returncode=0)
                if "ls-files" in cmd:
                    return MagicMock(stdout=b"link/escape.bin\0", returncode=0)
                return MagicMock(stdout=b"", returncode=0)
            mock_run.side_effect = side_effect
            with self.assertRaises(ev.EvidenceError) as ctx:
                ev.capture_git_state(
                    str(self.repo), "master", head,
                    [], [], [],
                )
            self.assertIn("Parent symlink", str(ctx.exception))

    def test_chain_symlink_with_absolute_target_escapes(self):
        """first → absolute path → second (outside workspace) must be rejected."""
        second = self.run / "second"
        second.mkdir(exist_ok=True)
        (second / "data.bin").write_bytes(b"outside")
        first = self.repo / "first"
        first.mkdir(exist_ok=True)
        # first/bridge is an absolute symlink pointing to second
        bridge = first / "bridge"
        bridge.symlink_to(str(second), target_is_directory=True)
        # Simulate git ls-files returning first/bridge/data.bin
        head = self.git("rev-parse", "HEAD").decode().strip()
        with patch.object(ev.subprocess, "run") as mock_run:
            def side_effect(*args, **kwargs):
                cmd = args[0] if args else kwargs.get("args", [])
                if "branch" in cmd:
                    return MagicMock(stdout=b"master\n", returncode=0)
                if "rev-parse" in cmd:
                    return MagicMock(stdout=head.encode() + b"\n", returncode=0)
                if "ls-files" in cmd:
                    return MagicMock(stdout=b"first/bridge/data.bin\0", returncode=0)
                return MagicMock(stdout=b"", returncode=0)
            mock_run.side_effect = side_effect
            with self.assertRaises(ev.EvidenceError) as ctx:
                ev.capture_git_state(
                    str(self.repo), "master", head,
                    [], [], [],
                )
            self.assertIn("Parent symlink", str(ctx.exception))

    def test_safe_relative_path_is_accepted(self):
        """Control: a safe relative path should work normally."""
        self.write("safe.txt", b"ok")
        state = ev.capture_git_state(
            str(self.repo), "master", self.git("rev-parse", "HEAD").decode().strip(),
            [], [], ["safe.txt"],
        )
        paths = {e["path"] for e in state["entries"]}
        self.assertIn("safe.txt", paths)


if __name__ == "__main__":
    unittest.main()
