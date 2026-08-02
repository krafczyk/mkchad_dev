"""Focused tests for keyless MkChad release bootstrap authority."""

import hashlib
import importlib.util
import json
import os
import pathlib
import subprocess
import tempfile
import unittest
from unittest import mock


ROOT = pathlib.Path(__file__).resolve().parents[1]


def load_bootstrap():
    """Load the bootstrap directly from the parent checkout."""
    spec = importlib.util.spec_from_file_location("mkchad_bootstrap", ROOT / "deploy/mkchad_release_bootstrap.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class BootstrapAuthorityTests(unittest.TestCase):
    """Ensure bootstrap accepts only closed keyless release authority."""

    def setUp(self):
        self.bootstrap = load_bootstrap()
        self.root = pathlib.Path(tempfile.mkdtemp(prefix="mkchad-bootstrap-test-"))
        os.chmod(self.root, 0o700)

    def remote_with_tag(self, annotated=True):
        """Create a local parent remote with one commit and optional annotated tag."""
        source = self.root / "source"
        remote = self.root / "remote.git"
        (source / "deploy").mkdir(parents=True)
        (source / "deploy" / "mkchad_deploy.py").write_text("print('candidate')\n", encoding="ascii")
        subprocess.run(["git", "init", "-q", str(source)], check=True)
        subprocess.run(["git", "-C", str(source), "add", "."], check=True)
        subprocess.run(["git", "-C", str(source), "-c", "user.name=test", "-c", "user.email=test@example.invalid", "commit", "-q", "-m", "candidate"], check=True)
        commit = subprocess.check_output(["git", "-C", str(source), "rev-parse", "HEAD"], text=True).strip()
        tag_args = ["git", "-C", str(source), "tag", "v1"]
        if annotated:
            tag_args = ["git", "-C", str(source), "-c", "user.name=test", "-c", "user.email=test@example.invalid", "tag", "-a", "-m", "unsigned release", "v1"]
        subprocess.run(tag_args, check=True)
        subprocess.run(["git", "clone", "-q", "--bare", str(source), str(remote)], check=True)
        return source, remote, commit

    def test_unsigned_annotated_tag_requires_matching_full_sha_and_closed_authority(self):
        _, remote, commit = self.remote_with_tag()
        evidence = self.bootstrap.verify_annotated_tag(str(remote), "v1", commit, self.root / "work")

        self.assertEqual(evidence, {"kind": "annotated-tag-plus-sha", "tag": "v1", "tag_object": evidence["tag_object"], "parent_commit": commit})
        with self.assertRaisesRegex(self.bootstrap.BootstrapError, "expected parent"):
            self.bootstrap.verify_annotated_tag(str(remote), "v1", "0" * 40, self.root / "mismatch")

    def test_lightweight_and_malformed_tags_are_rejected_without_signing_inputs(self):
        _, remote, commit = self.remote_with_tag(annotated=False)
        with self.assertRaisesRegex(self.bootstrap.BootstrapError, "annotated"):
            self.bootstrap.verify_annotated_tag(str(remote), "v1", commit, self.root / "lightweight")
        with self.assertRaisesRegex(self.bootstrap.BootstrapError, "invalid"):
            self.bootstrap.verify_annotated_tag(str(remote), "../main", commit, self.root / "malformed")

    def test_tag_movement_between_private_observations_is_rejected(self):
        commit = "a" * 40
        first = "b" * 40
        second = "c" * 40

        fetches = []

        def git(argv, cwd=None, check=True):
            if argv and argv[0] == "fetch":
                fetches.append(argv)
            if argv[:2] == ["cat-file", "-t"]:
                return subprocess.CompletedProcess(argv, 0, "tag\n", "")
            if argv[:2] == ["rev-parse", "refs/mkchad-bootstrap/first^{tag}"]:
                return subprocess.CompletedProcess(argv, 0, first + "\n", "")
            if argv[:2] == ["rev-parse", "refs/mkchad-bootstrap/second^{tag}"]:
                return subprocess.CompletedProcess(argv, 0, second + "\n", "")
            if argv[:2] == ["rev-parse", first + "^{commit}"] or argv[:2] == ["rev-parse", second + "^{commit}"]:
                return subprocess.CompletedProcess(argv, 0, commit + "\n", "")
            return subprocess.CompletedProcess(argv, 0, "", "")

        with mock.patch.object(self.bootstrap, "_git", side_effect=git):
            with self.assertRaisesRegex(self.bootstrap.BootstrapError, "moved"):
                self.bootstrap.verify_annotated_tag("remote", "v1", commit, self.root / "moved")
        self.assertEqual(
            fetches,
            [
                ["fetch", "--no-tags", "remote", "refs/tags/v1:refs/mkchad-bootstrap/first"],
                ["fetch", "--no-tags", "remote", "refs/tags/v1:refs/mkchad-bootstrap/second"],
            ],
        )

    def test_development_authority_is_closed_and_unfetchable_commit_fails(self):
        _, remote, commit = self.remote_with_tag()
        self.assertEqual(self.bootstrap.development_evidence(commit), {"kind": "development-sha", "parent_commit": commit})
        with self.assertRaisesRegex(self.bootstrap.BootstrapError, "unavailable"):
            self.bootstrap.prepare_verified_checkout(str(remote), self.bootstrap.development_evidence("0" * 40), self.root / "missing")

    def test_installed_bootstrap_rejects_linked_or_writable_path_without_digest_file(self):
        script = self.root / "bootstrap.py"
        script.write_text("pass\n", encoding="ascii")
        self.bootstrap.validate_installed_bootstrap(script)
        script.chmod(0o666)
        with self.assertRaisesRegex(self.bootstrap.BootstrapError, "safe regular"):
            self.bootstrap.validate_installed_bootstrap(script)
        unsafe = self.root / "unsafe"
        unsafe.mkdir()
        unsafe.chmod(0o777)
        nested_script = unsafe / "bootstrap.py"
        nested_script.write_text("pass\n", encoding="ascii")
        with self.assertRaisesRegex(self.bootstrap.BootstrapError, "ancestor"):
            self.bootstrap.validate_installed_bootstrap(nested_script)
        foreign = mock.Mock(st_uid=os.getuid() + 1, st_mode=0o040755)
        self.assertTrue(self.bootstrap._unsafe_writable_ancestor(foreign))

    def test_git_timeout_terminates_the_process_group(self):
        binary = self.root / "bin"
        binary.mkdir()
        git = binary / "git"
        git.write_text("#!/bin/sh\ntrap '' TERM\nsleep 30\n", encoding="ascii")
        git.chmod(0o755)
        with mock.patch.dict(os.environ, {"PATH": str(binary) + os.pathsep + os.environ["PATH"]}):
            with self.assertRaisesRegex(self.bootstrap.BootstrapError, "timed out"):
                self.bootstrap._git(["version"], timeout=0.05, term_grace=0.05, kill_grace=0.2)

    def test_git_rejects_diagnostic_output_overflow(self):
        binary = self.root / "overflow-bin"
        binary.mkdir()
        git = binary / "git"
        git.write_text("#!/bin/sh\nyes x | head -c 1100000 >&2\n", encoding="ascii")
        git.chmod(0o755)
        with mock.patch.dict(os.environ, {"PATH": str(binary) + os.pathsep + os.environ["PATH"]}):
            with self.assertRaisesRegex(self.bootstrap.BootstrapError, "output limit"):
                self.bootstrap._git(["version"], timeout=2, term_grace=0.05, kill_grace=0.2)

    def test_git_rejects_and_kills_a_surviving_group_helper(self):
        binary = self.root / "helper-bin"
        binary.mkdir()
        git = binary / "git"
        git.write_text("#!/bin/sh\n(trap '' TERM; exec sleep 30) </dev/null >/dev/null 2>&1 &\n", encoding="ascii")
        git.chmod(0o755)
        with mock.patch.dict(os.environ, {"PATH": str(binary) + os.pathsep + os.environ["PATH"]}):
            with self.assertRaisesRegex(self.bootstrap.BootstrapError, "live git process-group member"):
                self.bootstrap._git(["version"], timeout=2, term_grace=0.05, kill_grace=0.2)

    def test_private_output_retries_short_writes(self):
        writes = []

        def short_write(_fd, value):
            writes.append(bytes(value))
            return min(2, len(value))

        with mock.patch.object(self.bootstrap.os, "write", side_effect=short_write):
            self.bootstrap._write_all(7, b"abcdef")
        self.assertEqual([len(value) for value in writes], [6, 4, 2])

    def test_parent_bundle_digest_and_evidence_are_private_and_bound(self):
        _, remote, commit = self.remote_with_tag()
        work = self.root / "work"
        authority = self.bootstrap.verify_annotated_tag(str(remote), "v1", commit, work)
        candidate = self.bootstrap.prepare_verified_checkout(str(remote), authority, work)
        bundle = self.bootstrap.write_parent_bundle(work, authority, self.root / "parent.bundle")
        evidence = self.bootstrap.write_evidence(self.root / "evidence", authority, bundle)

        parsed = json.loads(evidence.read_text(encoding="utf-8"))
        self.assertEqual(parsed["authority"], authority)
        self.assertEqual(parsed["parent_bundle_sha256"], hashlib.sha256(bundle.read_bytes()).hexdigest())
        self.assertEqual(stat_mode(bundle), 0o600)
        self.assertEqual(stat_mode(evidence), 0o600)
        self.assertTrue(candidate.is_file())

    def test_recovery_accepts_retained_keyless_evidence_and_rejects_tampering(self):
        deployment_id = "123e4567-e89b-12d3-a456-426614174000"
        _, remote, commit = self.remote_with_tag()
        work = self.root / "work"
        authority = self.bootstrap.verify_annotated_tag(str(remote), "v1", commit, work)
        self.bootstrap.prepare_verified_checkout(str(remote), authority, work)
        state = self.root / "state"
        state.mkdir(mode=0o700)
        bundle = state / f"{deployment_id}.bundle"
        bundle.mkdir(mode=0o700)
        parent_bundle = self.bootstrap.write_parent_bundle(work, authority, bundle / "parent.bundle")
        evidence = self.bootstrap.write_evidence(bundle, authority, parent_bundle)
        retained = {
            "deployer.py": (work / "deploy" / "mkchad_deploy.py").read_bytes(),
            "verified-release.json": evidence.read_bytes(),
            "release.json": b"{}\n",
            "release-v1.schema.json": b"{}\n",
            "receipt-v1.schema.json": b"{}\n",
            "parent.bundle": parent_bundle.read_bytes(),
        }
        for name, content in retained.items():
            path = bundle / name
            if not path.exists():
                path.write_bytes(content)
            path.chmod(0o600)
        large_bundle = bundle / "large.bundle"
        with large_bundle.open("wb") as handle:
            handle.truncate(16 * 1024 * 1024 + 1)
        large_bundle.chmod(0o600)
        large_digest = hashlib.sha256()
        for _ in range(16):
            large_digest.update(b"\0" * (1024 * 1024))
        large_digest.update(b"\0")
        record = {
            "schema": 1, "id": deployment_id, "deployer": "mkchad-deploy-v1", "state": "failed",
            "state_root": str(state), "recovery_bundle": bundle.name,
            "authority": authority,
            "bundle_digests": {name: hashlib.sha256(content).hexdigest() for name, content in retained.items()},
        }
        record["bundle_digests"][large_bundle.name] = large_digest.hexdigest()
        (state / f"{deployment_id}.json").write_text(json.dumps(record), encoding="utf-8")
        (state / f"{deployment_id}.json").chmod(0o600)
        self.assertEqual(self.bootstrap.authenticated_recovery_deployer(state, deployment_id), bundle / "deployer.py")
        (bundle / "parent.bundle").write_bytes(b"tampered")
        record["bundle_digests"]["parent.bundle"] = hashlib.sha256(b"tampered").hexdigest()
        (state / f"{deployment_id}.json").write_text(json.dumps(record), encoding="utf-8")
        with self.assertRaisesRegex(self.bootstrap.BootstrapError, "integrity"):
            self.bootstrap.authenticated_recovery_deployer(state, deployment_id)

    def test_recovery_rejects_release_selection_arguments(self):
        deployment_id = "123e4567-e89b-12d3-a456-426614174000"
        with mock.patch.object(self.bootstrap, "validate_installed_bootstrap"), mock.patch.object(self.bootstrap, "authenticated_recovery_deployer") as recovery:
            result = self.bootstrap.main(["--release", "v1", "recover", "--deployment-id", deployment_id, "--mode", "restore", "--state-root", str(self.root)])
        self.assertEqual(result, 2)
        recovery.assert_not_called()

    def test_public_release_entrypoint_forwards_both_authority_files(self):
        private = self.root / "main-private"
        private.mkdir(mode=0o700)
        candidate = self.root / "candidate.py"
        candidate.write_text("pass\n", encoding="ascii")
        authority = {"kind": "annotated-tag-plus-sha", "tag": "v1", "tag_object": "b" * 40, "parent_commit": "a" * 40}

        def write_bundle(_work, _authority, path):
            path.write_bytes(b"bundle")
            path.chmod(0o600)
            return path

        def write_evidence(directory, _authority, _bundle):
            path = directory / "verified-release.json"
            path.write_text("{}\n", encoding="ascii")
            path.chmod(0o600)
            return path

        process = mock.Mock()
        process.wait.return_value = 0
        with mock.patch.object(self.bootstrap, "validate_installed_bootstrap"), mock.patch.object(self.bootstrap.tempfile, "mkdtemp", return_value=str(private)), mock.patch.object(
            self.bootstrap, "verify_annotated_tag", return_value=authority
        ), mock.patch.object(self.bootstrap, "prepare_verified_checkout", return_value=candidate), mock.patch.object(
            self.bootstrap, "write_parent_bundle", side_effect=write_bundle
        ), mock.patch.object(self.bootstrap, "write_evidence", side_effect=write_evidence), mock.patch.object(
            self.bootstrap.subprocess, "Popen", return_value=process
        ) as popen:
            self.assertEqual(self.bootstrap.main(["--release", "v1", "--expected-parent-commit", "a" * 40, "--image", str(self.root / "image.sif")]), 0)
        command = popen.call_args.args[0]
        self.assertIn("--verified-release", command)
        self.assertIn("--verified-parent-bundle", command)
        self.assertIn("--bootstrap-root", command)

    def test_prehandoff_failure_removes_private_bootstrap_root(self):
        private = self.root / "failed-private"
        private.mkdir(mode=0o700)
        with mock.patch.object(self.bootstrap, "validate_installed_bootstrap"), mock.patch.object(self.bootstrap.tempfile, "mkdtemp", return_value=str(private)), mock.patch.object(
            self.bootstrap, "verify_annotated_tag", side_effect=self.bootstrap.BootstrapError("expected failure")
        ):
            self.assertEqual(self.bootstrap.main(["--release", "v1", "--expected-parent-commit", "a" * 40, "--image", str(self.root / "image.sif")]), 2)
        self.assertFalse(private.exists())


def stat_mode(path):
    """Return only the Unix permission bits for one test fixture path."""
    return path.stat().st_mode & 0o777


if __name__ == "__main__":
    unittest.main()
