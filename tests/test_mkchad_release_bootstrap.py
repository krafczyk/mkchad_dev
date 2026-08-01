"""Focused tests for bootstrap trust-root checks."""

import hashlib
import importlib.util
import json
import os
import pathlib
import tempfile
import unittest
import subprocess


ROOT = pathlib.Path(__file__).resolve().parents[1]


def load_bootstrap():
    """Load the bootstrap directly from the parent checkout."""
    spec = importlib.util.spec_from_file_location("mkchad_bootstrap", ROOT / "deploy/mkchad_release_bootstrap.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class BootstrapTrustTests(unittest.TestCase):
    """Ensure bootstrap trust material is owned, private, and unambiguous."""

    def setUp(self):
        self.bootstrap = load_bootstrap()
        self.root = pathlib.Path(tempfile.mkdtemp(prefix="mkchad-bootstrap-test-"))
        os.chmod(self.root, 0o700)

    def test_snapshots_exactly_one_principal_and_revocation_file(self):
        (self.root / "allowed_signers").write_text("mkchad-release ssh-ed25519 AAAATEST\n", encoding="ascii")
        (self.root / "revoked_signers").write_text("", encoding="ascii")
        snapshot = self.bootstrap.snapshot_trust_root(self.root, self.root / "snapshot")
        self.assertEqual(snapshot["principal"], "mkchad-release")
        self.assertTrue(pathlib.Path(snapshot["allowed_signers"]).is_file())
        (self.root / "allowed_signers").write_text("mkchad-release ssh-ed25519 AAAA\nmkchad-release ssh-ed25519 BBBB\n", encoding="ascii")
        with self.assertRaisesRegex(self.bootstrap.BootstrapError, "exactly one"):
            self.bootstrap.snapshot_trust_root(self.root, self.root / "other")

    def test_self_digest_fails_closed(self):
        script = self.root / "bootstrap.py"
        script.write_text("print('bootstrap')\n", encoding="ascii")
        digest = hashlib.sha256(script.read_bytes()).hexdigest()
        (self.root / "bootstrap.sha256").write_text(digest + "\n", encoding="ascii")
        self.bootstrap.verify_self_digest(script, self.root / "bootstrap.sha256")
        (self.root / "bootstrap.sha256").write_text("0" * 64 + "\n", encoding="ascii")
        with self.assertRaisesRegex(self.bootstrap.BootstrapError, "digest"):
            self.bootstrap.verify_self_digest(script, self.root / "bootstrap.sha256")

    def test_trust_root_rejects_symlinked_ancestor(self):
        real_parent = self.root / "real"
        trust = real_parent / "trust"
        trust.mkdir(parents=True, mode=0o700)
        alias = self.root / "alias"
        alias.symlink_to(real_parent, target_is_directory=True)
        with self.assertRaisesRegex(self.bootstrap.BootstrapError, "symlink"):
            self.bootstrap.snapshot_trust_root(alias / "trust", self.root / "snapshot")

    def test_development_execution_uses_only_verified_checkout(self):
        source = self.root / "source"
        remote = self.root / "remote.git"
        checkout = self.root / "checkout"
        (source / "deploy").mkdir(parents=True)
        candidate_source = source / "deploy" / "mkchad_deploy.py"
        candidate_source.write_text("print('verified candidate')\n", encoding="ascii")
        subprocess.run(["git", "init", "-q", str(source)], check=True)
        subprocess.run(["git", "-C", str(source), "add", "deploy/mkchad_deploy.py"], check=True)
        subprocess.run(
            ["git", "-C", str(source), "-c", "user.name=test", "-c", "user.email=test@example.invalid", "commit", "-q", "-m", "candidate"],
            check=True,
        )
        commit = subprocess.check_output(["git", "-C", str(source), "rev-parse", "HEAD"], text=True).strip()
        subprocess.run(["git", "clone", "-q", "--bare", str(source), str(remote)], check=True)
        candidate_source.write_text("print('unverified worktree')\n", encoding="ascii")

        evidence = self.bootstrap.development_evidence(commit)
        candidate = self.bootstrap.prepare_verified_checkout(str(remote), evidence, checkout)

        self.assertEqual(candidate.read_text(encoding="ascii"), "print('verified candidate')\n")
        self.assertEqual(evidence["checkout_commit"], commit)
        self.assertEqual(evidence["candidate_sha256"], hashlib.sha256(candidate.read_bytes()).hexdigest())

    def test_recovery_executes_only_digest_bound_retained_deployer(self):
        deployment_id = "123e4567-e89b-12d3-a456-426614174000"
        state = self.root / "state"
        state.mkdir(mode=0o700)
        bundle = state / f"{deployment_id}.bundle"
        bundle.mkdir(mode=0o700)
        retained = {
            "deployer.py": b"print('retained')\n",
            "verified-release.json": json.dumps({"schema": 1, "deployer": "mkchad-deploy-v1", "trust": "development-sha", "parent_commit": "1" * 40}).encode(),
            "release.json": b"{}\n",
            "release-v1.schema.json": b"{}\n",
            "receipt-v1.schema.json": b"{}\n",
            "parent.bundle": b"fixture bundle\n",
        }
        for name, content in retained.items():
            path = bundle / name
            path.write_bytes(content)
            path.chmod(0o600)
        staging = bundle / "staging"
        staging.mkdir(mode=0o700)
        (staging / "partial-input").write_bytes(b"private staging")
        deployer = bundle / "deployer.py"
        record = {
            "schema": 1,
            "id": deployment_id,
            "deployer": "mkchad-deploy-v1",
            "state": "failed",
            "state_root": str(state),
            "recovery_bundle": bundle.name,
            "bundle_digests": {name: hashlib.sha256(content).hexdigest() for name, content in retained.items()},
        }
        record_path = state / f"{deployment_id}.json"
        record_path.write_text(json.dumps(record), encoding="utf-8")
        record_path.chmod(0o600)
        self.assertEqual(self.bootstrap.authenticated_recovery_deployer(state, deployment_id), deployer)
        deployer.write_text("print('tampered')\n", encoding="ascii")
        with self.assertRaisesRegex(self.bootstrap.BootstrapError, "authentication"):
            self.bootstrap.authenticated_recovery_deployer(state, deployment_id)

    def test_development_evidence_is_explicit_and_lightweight_tag_is_rejected(self):
        commit = "a" * 40
        self.assertEqual(self.bootstrap.development_evidence(commit)["trust"], "development-sha")
        with self.assertRaises(self.bootstrap.BootstrapError):
            self.bootstrap.development_evidence("main")
        source = self.root / "source"
        remote = self.root / "remote.git"
        work = self.root / "work"
        subprocess.run(["git", "init", str(source)], check=True, stdout=subprocess.DEVNULL)
        subprocess.run(["git", "-C", str(source), "-c", "user.name=test", "-c", "user.email=test@example.invalid", "commit", "--allow-empty", "-m", "initial"], check=True, stdout=subprocess.DEVNULL)
        subprocess.run(["git", "-C", str(source), "tag", "v1"], check=True)
        subprocess.run(["git", "clone", "--bare", str(source), str(remote)], check=True, stdout=subprocess.DEVNULL)
        snapshot = {"allowed_signers": str(self.root / "allowed_signers"), "revoked_signers": str(self.root / "revoked_signers")}
        (self.root / "allowed_signers").write_text("mkchad-release ssh-ed25519 AAAATEST\n", encoding="ascii")
        (self.root / "revoked_signers").write_text("", encoding="ascii")
        with self.assertRaisesRegex(self.bootstrap.BootstrapError, "annotated"):
            self.bootstrap.verify_signed_tag(str(remote), "v1", snapshot, work)

    def test_authorized_and_revoked_ssh_signed_tags(self):
        source = self.root / "source"
        remote = self.root / "remote.git"
        key = self.root / "release-key"
        subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(key)], check=True)
        public = key.with_suffix(".pub").read_text(encoding="ascii").strip()
        subprocess.run(["git", "init", "-q", str(source)], check=True)
        subprocess.run(["git", "-C", str(source), "-c", "user.name=test", "-c", "user.email=test@example.invalid", "commit", "--allow-empty", "-m", "initial"], check=True, stdout=subprocess.DEVNULL)
        subprocess.run(["git", "-C", str(source), "-c", "user.name=test", "-c", "user.email=test@example.invalid", "-c", "gpg.format=ssh", "-c", "user.signingkey=" + str(key), "tag", "-s", "-m", "release", "v1"], check=True)
        subprocess.run(["git", "clone", "-q", "--bare", str(source), str(remote)], check=True)
        allowed = self.root / "allowed_signers"
        revoked = self.root / "revoked_signers"
        allowed.write_text("mkchad-release " + public + "\n", encoding="ascii")
        revoked.write_text("", encoding="ascii")
        snapshot = {"allowed_signers": str(allowed), "revoked_signers": str(revoked)}
        evidence = self.bootstrap.verify_signed_tag(str(remote), "v1", snapshot, self.root / "verified")
        self.assertEqual(evidence["trust"], "ssh-signed-tag")
        subprocess.run(["ssh-keygen", "-k", "-f", str(revoked), str(key.with_suffix(".pub"))], check=True, stdout=subprocess.DEVNULL)
        with self.assertRaisesRegex(self.bootstrap.BootstrapError, "authorized"):
            self.bootstrap.verify_signed_tag(str(remote), "v1", snapshot, self.root / "revoked-verified")


if __name__ == "__main__":
    unittest.main()
