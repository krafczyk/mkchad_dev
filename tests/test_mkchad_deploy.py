"""Focused behavioral tests for the parent deployment engine."""

import hashlib
import importlib.util
import json
import os
import pathlib
import contextlib
import signal
import shutil
import tempfile
import unittest
from unittest import mock
import subprocess
import sys
import time


ROOT = pathlib.Path(__file__).resolve().parents[1]


def load_module(name: str, relative: str):
    """Load a deployment module directly from the parent checkout."""
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class DeploymentValidationTests(unittest.TestCase):
    """Exercise fail-closed release and filesystem primitives."""

    def setUp(self):
        self.deploy = load_module("mkchad_deploy", "deploy/mkchad_deploy.py")
        self.root = pathlib.Path(tempfile.mkdtemp(prefix="mkchad-deploy-test-"))

    def release(self):
        return {
            "schema": 1,
            "components": [
                {
                    "name": "msk_containers",
                    "disposition": "deployed",
                    "destination": {"root": "xdg_data_home", "path": "mkchad/components/msk_containers"},
                    "actions": [
                        {"id": "host-tools-check", "path": "bin/install_nvim.sh", "args": ["--check"]},
                        {"id": "host-tools-install", "path": "bin/install_nvim.sh", "args": ["--recovery-dir", {"token": "recovery_dir"}]},
                        {"id": "image-install", "path": "nvim/bin/install_nvim_container", "args": ["--source-revision", {"token": "component_revision"}, {"token": "staged_image"}]},
                    ],
                },
                {"name": "mkchad", "disposition": "deployed", "destination": {"root": "xdg_config_home", "path": "mkchad"}, "actions": []},
                {"name": "opencode", "disposition": "embedded"},
                {"name": "opencode.nvim", "disposition": "configuration-owned"},
                {"name": "opencode-project-reload", "disposition": "configuration-owned"},
                {"name": "compound-engineering", "disposition": "configuration-owned"},
                {"name": "opencode_sprint_loop", "disposition": "disabled"},
            ],
            "artifacts": {"nvim-image": [{"architecture": "x86_64", "sha256": "a" * 64, "component_manifest_sha256": "b" * 64, "source_filename_class": "raw-nvim-container"}]},
            "configuration": {"contract_version": 1, "guidance": "docs/deployment/README.md"},
        }

    def make_checkout(self, path, repository, files=None, executable=()):
        path.mkdir(parents=True)
        subprocess.run(["git", "init", "-q", str(path)], check=True)
        for name, content in (files or {"tracked": "content\n"}).items():
            target = path / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
            if name in executable:
                target.chmod(0o755)
        subprocess.run(["git", "-C", str(path), "add", "."], check=True)
        subprocess.run(
            ["git", "-C", str(path), "-c", "user.name=test", "-c", "user.email=test@example.invalid", "commit", "-q", "-m", "initial"],
            check=True,
        )
        subprocess.run(["git", "-C", str(path), "remote", "add", "origin", f"git@github.com:krafczyk/{repository}"], check=True)
        return subprocess.check_output(["git", "-C", str(path), "rev-parse", "HEAD"], text=True).strip()

    def test_release_rejects_duplicate_disposition_and_revision(self):
        release = self.release()
        release["components"].append({"name": "mkchad", "disposition": "disabled"})
        with self.assertRaisesRegex(self.deploy.DeploymentError, "exactly one"):
            self.deploy.validate_release_document(release, list(self.deploy.COMPONENT_DISPOSITIONS))
        release = self.release()
        release["components"][0]["revision"] = "0" * 40
        with self.assertRaisesRegex(self.deploy.DeploymentError, "revision"):
            self.deploy.validate_release_document(release, list(self.deploy.COMPONENT_DISPOSITIONS))

    def test_release_enforces_fixed_dispositions_destinations_and_typed_actions(self):
        release = self.release()
        release["components"][2]["disposition"] = "deployed"
        with self.assertRaisesRegex(self.deploy.DeploymentError, "disposition"):
            self.deploy.validate_release_document(release, list(self.deploy.COMPONENT_DISPOSITIONS))

        release = self.release()
        release["components"][0]["destination"]["path"] = "elsewhere"
        with self.assertRaisesRegex(self.deploy.DeploymentError, "destination"):
            self.deploy.validate_release_document(release, list(self.deploy.COMPONENT_DISPOSITIONS))

        release = self.release()
        self.deploy.validate_release_document(release, list(self.deploy.COMPONENT_DISPOSITIONS))
        release["components"][0]["actions"][2]["args"][1] = {"token": "shell_command"}
        with self.assertRaisesRegex(self.deploy.DeploymentError, "token"):
            self.deploy.validate_release_document(release, list(self.deploy.COMPONENT_DISPOSITIONS))

    def test_public_tree_metadata_does_not_require_private_receipt_mode(self):
        release_path = self.root / "release.json"
        release_path.write_text(json.dumps(self.release()), encoding="utf-8")
        os.chmod(release_path, 0o644)
        parsed = self.deploy.load_release_json(release_path)
        self.assertEqual(parsed["schema"], 1)

    def test_configuration_handoff_uses_only_gitlink_derived_pins(self):
        gitlinks = {
            name: {"repository": f"github.com/krafczyk/{name}", "commit": format(index, "040x")}
            for index, name in enumerate(self.deploy.COMPONENT_DISPOSITIONS, 1)
        }
        handoff = self.deploy.configuration_handoff(self.release(), gitlinks)
        self.assertEqual(handoff["contract_version"], 1)
        self.assertEqual(handoff["guidance"], "docs/deployment/README.md")
        self.assertEqual([component["name"] for component in handoff["components"]], ["compound-engineering", "opencode-project-reload", "opencode.nvim"])
        self.assertEqual(handoff["components"][0]["revision"], gitlinks["compound-engineering"]["commit"])
        del gitlinks["opencode.nvim"]
        with self.assertRaisesRegex(self.deploy.DeploymentError, "gitlink authority"):
            self.deploy.configuration_handoff(self.release(), gitlinks)

    def test_keyless_authority_evidence_and_parent_bundle_are_closed_and_bound(self):
        repo = self.root / "parent"
        subprocess.run(["git", "init", "-q", str(repo)], check=True)
        (repo / "deploy").mkdir()
        candidate = repo / "deploy" / "mkchad_deploy.py"
        candidate.write_text("# candidate\n", encoding="ascii")
        subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
        subprocess.run(["git", "-C", str(repo), "-c", "user.name=test", "-c", "user.email=test@example.invalid", "commit", "-q", "-m", "candidate"], check=True)
        commit = subprocess.check_output(["git", "-C", str(repo), "rev-parse", "HEAD"], text=True).strip()
        bundle = self.root / "parent.bundle"
        subprocess.run(["git", "-C", str(repo), "bundle", "create", str(bundle), "HEAD"], check=True)
        bundle.chmod(0o600)
        evidence = self.root / "verified-release.json"
        authority = {"kind": "development-sha", "parent_commit": commit}
        evidence.write_text(json.dumps({"schema": 1, "deployer": self.deploy.DEPLOYER_ID, "authority": authority, "parent_bundle_sha256": hashlib.sha256(bundle.read_bytes()).hexdigest()}), encoding="utf-8")
        evidence.chmod(0o600)
        self.assertEqual(self.deploy._bootstrap_evidence(evidence, bundle), authority)
        self.deploy._verify_authority_bundle(authority, bundle)
        tampered = json.loads(evidence.read_text(encoding="utf-8"))
        tampered["authority"]["signer"] = "legacy"
        evidence.write_text(json.dumps(tampered), encoding="utf-8")
        with self.assertRaisesRegex(self.deploy.DeploymentError, "authority"):
            self.deploy._bootstrap_evidence(evidence, bundle)
        evidence.write_text(json.dumps({"schema": 1, "deployer": self.deploy.DEPLOYER_ID, "authority": authority, "parent_bundle_sha256": "0" * 64}), encoding="utf-8")
        with self.assertRaisesRegex(self.deploy.DeploymentError, "bundle"):
            self.deploy._bootstrap_evidence(evidence, bundle)

    def test_deployer_bytes_must_match_the_selected_parent_tree(self):
        repo = self.root / "parent"
        candidate = repo / "deploy" / "mkchad_deploy.py"
        candidate.parent.mkdir(parents=True)
        candidate.write_text("# exact candidate\n", encoding="ascii")
        subprocess.run(["git", "init", "-q", str(repo)], check=True)
        subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
        subprocess.run(["git", "-C", str(repo), "-c", "user.name=test", "-c", "user.email=test@example.invalid", "commit", "-q", "-m", "candidate"], check=True)
        commit = subprocess.check_output(["git", "-C", str(repo), "rev-parse", "HEAD"], text=True).strip()
        self.deploy._verify_deployer_blob(repo, commit, candidate)
        candidate.write_text("# dirty candidate\n", encoding="ascii")
        with self.assertRaisesRegex(self.deploy.DeploymentError, "bytes"):
            self.deploy._verify_deployer_blob(repo, commit, candidate)

    def test_validation_checkout_uses_committed_bytes_not_dirty_worktree(self):
        repo = self.root / "source"
        commit = self.make_checkout(repo, "mkchad", {"deploy/release.json": "committed\n"})
        (repo / "deploy" / "release.json").write_text("dirty\n", encoding="ascii")
        root = self.root / "validation"
        root.mkdir(mode=0o700)
        candidate = self.deploy.prepare_validation_checkout(repo, commit, root)
        self.assertEqual((candidate / "deploy" / "release.json").read_text(encoding="ascii"), "committed\n")
        self.assertEqual(subprocess.check_output(["git", "-C", str(candidate), "rev-parse", "HEAD"], text=True).strip(), commit)

    def test_recovery_bundle_retention_preserves_collisions_and_exact_mode(self):
        state = self.root / "state"
        state.mkdir(mode=0o700)
        source = self.root / "source"
        source.write_bytes(b"source")
        source.chmod(0o600)
        journal = self.deploy.DeploymentJournal(state, {"kind": "development-sha", "parent_commit": "a" * 40}, [])
        collision = journal.bundle / "parent.bundle"
        collision.write_bytes(b"existing")
        collision.chmod(0o600)
        with self.assertRaisesRegex(self.deploy.DeploymentError, "collision"):
            journal.retain("parent.bundle", source)
        self.assertEqual(collision.read_bytes(), b"existing")
        old_umask = os.umask(0o777)
        try:
            journal.retain("evidence.json", source)
        finally:
            os.umask(old_umask)
        self.assertEqual((journal.bundle / "evidence.json").stat().st_mode & 0o777, 0o600)
        journal.discard_unpublished()
        self.assertFalse(journal.bundle.exists())

    def test_validate_release_identity_accepts_an_absent_planned_tag_and_rejects_old_cli_inputs(self):
        repo = self.root / "candidate"
        commit = self.make_checkout(repo, "mkchad")
        self.deploy.validate_release_identity(repo, "v9-not-published", commit)
        with self.assertRaisesRegex(self.deploy.DeploymentError, "expected parent"):
            self.deploy.validate_release_identity(repo, "v9-not-published", "0" * 40)
        with self.assertRaises(SystemExit):
            self.deploy.main(["validate-release", "--trust-root", str(self.root)])

    def test_public_deploy_entrypoint_forwards_parent_bundle(self):
        evidence = self.root / "evidence.json"
        bundle = self.root / "parent.bundle"
        image = self.root / "image.sif"
        with mock.patch.object(self.deploy, "spawn_supervisor", return_value=0) as supervisor:
            result = self.deploy.main(["deploy", "--verified-release", str(evidence), "--verified-parent-bundle", str(bundle), "--image", str(image)])
        self.assertEqual(result, 0)
        command = supervisor.call_args.args[0]
        self.assertEqual(command[command.index("--verified-parent-bundle") + 1], str(bundle))

    def test_repository_identity_and_destination_are_constrained(self):
        self.assertEqual(self.deploy.canonical_github_repository("git@github.com:krafczyk/mkchad.git"), "github.com/krafczyk/mkchad")
        self.assertEqual(self.deploy.canonical_github_repository("https://github.com/krafczyk/mkchad"), "github.com/krafczyk/mkchad")
        with self.assertRaises(self.deploy.DeploymentError):
            self.deploy.canonical_github_repository("/tmp/mkchad")
        with self.assertRaises(self.deploy.DeploymentError):
            self.deploy.resolve_named_destination({"xdg_config_home": self.root}, "xdg_config_home", "../escape")

    def test_image_snapshot_rejects_replacement_and_digest_mismatch_before_runtime(self):
        source = self.root / "input.sif"
        source.write_bytes(b"known image")
        staged = self.deploy.snapshot_image(source, self.root / "stage", hashlib.sha256(b"known image").hexdigest())
        self.assertEqual(staged.read_bytes(), b"known image")
        self.assertEqual(staged.name, "nvim_container_x86.sif")
        self.assertIn(hashlib.sha256(b"known image").hexdigest(), pathlib.Path(str(staged) + ".sha256").read_text(encoding="ascii"))
        with self.assertRaisesRegex(self.deploy.DeploymentError, "digest"):
            self.deploy.snapshot_image(source, self.root / "other", "0" * 64)
        source.unlink()
        source.symlink_to(staged)
        with self.assertRaisesRegex(self.deploy.DeploymentError, "regular"):
            self.deploy.snapshot_image(source, self.root / "symlink", hashlib.sha256(b"known image").hexdigest())

    def test_journal_rejects_future_and_moved_receipts(self):
        state = self.root / "state"
        state.mkdir(mode=0o700)
        record = {"schema": 1, "id": "123e4567-e89b-12d3-a456-426614174000", "state": "failed", "deployer": "mkchad-deploy-v1", "resources": []}
        path = state / (record["id"] + ".json")
        self.deploy.atomic_json(path, record)
        self.assertEqual(self.deploy.load_recovery_record(state, record["id"])["state"], "failed")
        record["schema"] = 2
        self.deploy.atomic_json(path, record)
        with self.assertRaisesRegex(self.deploy.DeploymentError, "schema"):
            self.deploy.load_recovery_record(state, record["id"])
        record["schema"] = 1
        record["trust"] = {"kind": "legacy"}
        self.deploy.atomic_json(path, record)
        with self.assertRaisesRegex(self.deploy.DeploymentError, "obsolete"):
            self.deploy.load_recovery_record(state, record["id"])

    def test_receipt_collision_and_moved_record_leave_evidence_untouched(self):
        state = self.root / "state"
        state.mkdir(mode=0o700)
        path = state / "receipt.json"
        self.deploy.atomic_json(path, {"schema": 1})
        with self.assertRaisesRegex(self.deploy.DeploymentError, "collision"):
            self.deploy.atomic_json(path, {"schema": 1}, no_replace=True)
        record = {"schema": 1, "id": "123e4567-e89b-12d3-a456-426614174000", "state": "failed", "deployer": "mkchad-deploy-v1", "state_root": "/elsewhere", "resources": []}
        self.deploy.atomic_json(state / (record["id"] + ".json"), record)
        with self.assertRaisesRegex(self.deploy.DeploymentError, "moved or copied"):
            self.deploy.load_recovery_record(state, record["id"])
        self.assertTrue((state / (record["id"] + ".json")).exists())

    def test_hostile_git_config_blocks_before_checkout_and_clean_checkout_is_noop(self):
        checkout = self.root / "checkout"
        subprocess.run(["git", "init", str(checkout)], check=True, stdout=subprocess.DEVNULL)
        subprocess.run(["git", "-C", str(checkout), "-c", "user.name=test", "-c", "user.email=test@example.invalid", "commit", "--allow-empty", "-m", "initial"], check=True, stdout=subprocess.DEVNULL)
        commit = subprocess.check_output(["git", "-C", str(checkout), "rev-parse", "HEAD"], text=True).strip()
        subprocess.run(["git", "-C", str(checkout), "remote", "add", "origin", "git@github.com:krafczyk/mkchad"], check=True)
        result = self.deploy.preflight_existing_checkout(checkout, "github.com/krafczyk/mkchad", commit)
        self.assertTrue(result["compliant"])
        subprocess.run(["git", "-C", str(checkout), "config", "filter.attack.clean", "/bin/false"], check=True)
        with self.assertRaisesRegex(self.deploy.DeploymentError, "dangerous"):
            self.deploy.preflight_existing_checkout(checkout, "github.com/krafczyk/mkchad", commit)
        subprocess.run(["git", "-C", str(checkout), "config", "--unset", "filter.attack.clean"], check=True)
        external_git = self.root / "external-git"
        (checkout / ".git").rename(external_git)
        (checkout / ".git").symlink_to(external_git, target_is_directory=True)
        with self.assertRaisesRegex(self.deploy.DeploymentError, "external Git"):
            self.deploy.preflight_existing_checkout(checkout, "github.com/krafczyk/mkchad", commit)

    def test_all_destinations_preflight_before_any_component_update(self):
        data = self.root / "data"
        config = self.root / "config"
        msk = data / "mkchad" / "components" / "msk_containers"
        mkchad = config / "mkchad"
        msk_commit = self.make_checkout(msk, "msk_containers")
        mkchad_commit = self.make_checkout(mkchad, "mkchad", {".gitignore": "opencode-server.json\n"})
        (mkchad / "unexpected").write_text("dirty\n", encoding="ascii")
        release = self.release()
        roots = {"xdg_data_home": data, "xdg_config_home": config}
        gitlinks = {
            "msk_containers": {"commit": msk_commit, "repository": "github.com/krafczyk/msk_containers"},
            "mkchad": {"commit": mkchad_commit, "repository": "github.com/krafczyk/mkchad"},
        }
        before = os.stat(msk)
        with self.assertRaisesRegex(self.deploy.DeploymentError, "local work"):
            self.deploy.preflight_all_destinations(roots, release, gitlinks, {"msk_containers": msk, "mkchad": mkchad})
        after = os.stat(msk)
        self.assertEqual((before.st_dev, before.st_ino), (after.st_dev, after.st_ino))
        self.assertEqual(subprocess.check_output(["git", "-C", str(msk), "rev-parse", "HEAD"], text=True).strip(), msk_commit)

    def test_preserved_mkchad_path_accepts_only_expected_link_or_private_file(self):
        checkout = self.root / "preserved"
        commit = self.make_checkout(checkout, "mkchad", {".gitignore": "opencode-server.json\n"})
        preserved = checkout / "opencode-server.json"
        preserved.symlink_to("../../config_files/.config/mkchad/opencode-server.json")
        self.deploy.preflight_existing_checkout(checkout, "github.com/krafczyk/mkchad", commit, {"opencode-server.json"})
        preserved.unlink()
        preserved.symlink_to("/tmp/unexpected")
        with self.assertRaisesRegex(self.deploy.DeploymentError, "unexpected legacy link"):
            self.deploy.preflight_existing_checkout(checkout, "github.com/krafczyk/mkchad", commit, {"opencode-server.json"})

    def test_pinned_tree_owns_executable_actions_and_configuration_pin(self):
        msk = self.root / "action-tree"
        action_path = "bin/install_nvim.sh"
        commit = self.make_checkout(msk, "msk_containers", {action_path: "#!/bin/sh\nexit 0\n"}, executable={action_path})
        component = {"actions": [{"id": "host-tools-check", "path": action_path, "args": ["--check"]}]}
        self.deploy.validate_action_files(msk, commit, component)
        (msk / action_path).chmod(0o644)
        subprocess.run(["git", "-C", str(msk), "add", action_path], check=True)
        subprocess.run(["git", "-C", str(msk), "-c", "user.name=test", "-c", "user.email=test@example.invalid", "commit", "-q", "-m", "non-executable"], check=True)
        non_executable = subprocess.check_output(["git", "-C", str(msk), "rev-parse", "HEAD"], text=True).strip()
        with self.assertRaisesRegex(self.deploy.DeploymentError, "executable"):
            self.deploy.validate_action_files(msk, non_executable, component)

        mkchad = self.root / "pin-tree"
        expected = "1" * 40
        mkchad_commit = self.make_checkout(
            mkchad,
            "mkchad",
            {"lua/mkchad/opencode/contracts.lua": f'local M = {{ opencode_nvim_revision = "{expected}" }}\nreturn M\n'},
        )
        self.deploy.validate_configuration_pin(mkchad, mkchad_commit, expected)
        with self.assertRaisesRegex(self.deploy.DeploymentError, "does not match"):
            self.deploy.validate_configuration_pin(mkchad, mkchad_commit, "2" * 40)

    def test_session_scope_needs_no_cgroup_environment_and_captures_output(self):
        activities = []
        with mock.patch.dict(os.environ, {}, clear=True):
            scope = self.deploy.SessionScope(activity_callback=activities.append)
            result = scope.run([sys.executable, "-c", "print('ok')", "secret-argument"])
        self.assertEqual(result.stdout, "ok\n")
        self.assertEqual(result.stderr, "")
        self.assertEqual(activities[-1], None)
        self.assertEqual(activities[0]["kind"], pathlib.Path(sys.executable).name)
        self.assertNotIn("secret-argument", json.dumps(activities[0]))

    def test_session_scope_bounds_diagnostics_and_escalates_term_then_kill(self):
        scope = self.deploy.SessionScope(timeout=0.01, term_grace=0.01, kill_grace=0.01)
        with self.assertRaisesRegex(self.deploy.DeploymentError, "timed out"):
            scope.run([sys.executable, "-c", "import time; time.sleep(60)"])
        scope = self.deploy.SessionScope(timeout=1, term_grace=0.01, kill_grace=0.01)
        with self.assertRaisesRegex(self.deploy.DeploymentError, "diagnostic output exceeded"):
            scope.run([sys.executable, "-c", "import sys; sys.stderr.write('x' * (1024 * 1024 + 1))"])

    def test_session_scope_reaps_only_after_group_cleanup_and_detects_reuse_fence(self):
        scope = self.deploy.SessionScope()
        process = subprocess.Popen([sys.executable, "-c", "pass"], start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        while scope._terminal_status(process.pid) is None:
            time.sleep(0.01)
        identity = scope._leader_identity(process.pid)
        self.assertTrue(identity[4])
        scope._cleanup(process.pid, identity)
        scope._reap(process.pid)
        process.returncode = 0
        with mock.patch.object(scope, "_leader_identity", return_value=(os.getuid(), 1, process.pid, process.pid, False)):
            with self.assertRaisesRegex(self.deploy.DeploymentError, "identity changed"):
                scope._cleanup(process.pid, (os.getuid(), 2, process.pid, process.pid, False))

    def test_session_scope_bounds_pipe_drain_when_escaped_descendant_holds_streams(self):
        marker = self.root / "escaped-pid"
        program = (
            "import os,pathlib,time; "
            "pid=os.fork(); "
            f"path=pathlib.Path({str(marker)!r}); "
            "(os.setsid(),path.write_text(str(os.getpid())),time.sleep(60)) if pid == 0 else None"
        )
        started = time.monotonic()
        try:
            with self.assertRaisesRegex(self.deploy.DeploymentError, "diagnostic streams remained open"):
                self.deploy.SessionScope(timeout=2, term_grace=0.01, kill_grace=0.01).run([sys.executable, "-c", program])
            self.assertLess(time.monotonic() - started, 3)
        finally:
            if marker.exists():
                escaped = int(marker.read_text())
                with contextlib.suppress(ProcessLookupError):
                    os.kill(escaped, signal.SIGKILL)

    def test_session_scope_records_cleanup_unconfirmed_after_term_and_kill_deadlines(self):
        scope = self.deploy.SessionScope(term_grace=0, kill_grace=0)
        identity = (os.getuid(), 1, 12345, 12345, False)
        with mock.patch.object(scope, "_leader_identity", return_value=identity), mock.patch.object(scope, "_live_group_members", return_value=[12345]), mock.patch.object(os, "killpg") as kill:
            with self.assertRaisesRegex(self.deploy.DeploymentError, "cleanup-unconfirmed"):
                scope._cleanup(12345, identity)
        self.assertEqual([call.args[1] for call in kill.call_args_list], [self.deploy.signal.SIGTERM, self.deploy.signal.SIGKILL])

    def test_legacy_cgroup_records_are_rejected_without_signaling(self):
        deployment_id = "123e4567-e89b-12d3-a456-426614174000"
        state = self.root / "state"
        state.mkdir(mode=0o700)
        record = {"schema": 1, "id": deployment_id, "state": "in_progress", "deployer": self.deploy.DEPLOYER_ID, "resources": [], "cgroup_name": "legacy"}
        self.deploy.atomic_json(state / (deployment_id + ".json"), record)
        with self.assertRaisesRegex(self.deploy.DeploymentError, "prior implementation"):
            self.deploy.load_recovery_record(state, deployment_id)

    def test_recovery_confirmation_is_gated_by_durable_state(self):
        deployment_id = "123e4567-e89b-12d3-a456-426614174000"
        state = self.root / "state"
        state.mkdir(mode=0o700)
        bundle = state / (deployment_id + ".bundle")
        bundle.mkdir(mode=0o700)
        retained = {"deployer.py": b"x", "verified-release.json": b"x", "release.json": b"x", "release-v1.schema.json": b"x", "receipt-v1.schema.json": b"x"}
        for name, contents in retained.items():
            path = bundle / name
            path.write_bytes(contents)
            path.chmod(0o600)
        staging = bundle / "staging"
        staging.mkdir(mode=0o700)
        (staging / "partial-image").write_bytes(b"partial")
        base = {"schema": 1, "id": deployment_id, "state": "in_progress", "deployer": self.deploy.DEPLOYER_ID, "state_root": str(state), "resources": [], "final_mutation_started": False, "recovery_bundle": bundle.name, "bundle_digests": {name: hashlib.sha256(contents).hexdigest() for name, contents in retained.items()}}
        self.deploy.atomic_json(state / (deployment_id + ".json"), base)
        with self.assertRaisesRegex(self.deploy.DeploymentError, "requires --confirm"):
            self.deploy.recover(state, deployment_id, "restore")
        restored = self.deploy.recover(state, deployment_id, "restore", True)
        self.assertEqual(restored["state"], "recovered_restore")
        self.assertFalse(bundle.exists())
        base["state"] = "failed"
        base["final_mutation_started"] = True
        self.deploy.atomic_json(state / (deployment_id + ".json"), base)
        with self.assertRaisesRegex(self.deploy.DeploymentError, "rejected"):
            self.deploy.recover(state, deployment_id, "restore", True)

    def test_catchable_recovery_failure_returns_to_failed_with_cleanup_evidence(self):
        deployment_id = "123e4567-e89b-12d3-a456-426614174000"
        state = self.root / "recovery-state"
        state.mkdir(mode=0o700)
        bundle = state / (deployment_id + ".bundle")
        bundle.mkdir(mode=0o700)
        retained = {"release.json": b"{}", "verified-release.json": b"{}"}
        for name, contents in retained.items():
            path = bundle / name
            path.write_bytes(contents)
            path.chmod(0o600)
        record = {
            "schema": 1,
            "id": deployment_id,
            "state": "failed",
            "deployer": self.deploy.DEPLOYER_ID,
            "state_root": str(state),
            "resources": [],
            "final_mutation_started": True,
            "recovery_bundle": bundle.name,
            "bundle_digests": {name: hashlib.sha256(contents).hexdigest() for name, contents in retained.items()},
        }
        self.deploy.atomic_json(state / (deployment_id + ".json"), record)
        original_load_json = self.deploy.load_json

        def fail_retained_release(path, label):
            if label == "deployment record":
                return original_load_json(path, label)
            raise self.deploy.DeploymentError("catchable recovery failure")

        with mock.patch.object(self.deploy, "load_json", side_effect=fail_retained_release):
            with self.assertRaisesRegex(self.deploy.DeploymentError, "catchable recovery failure"):
                self.deploy.recover(state, deployment_id, "restore")
        persisted = json.loads((state / (deployment_id + ".json")).read_text())
        self.assertEqual(persisted["state"], "failed")
        self.assertEqual(persisted["phase"], "recovering")
        self.assertEqual(persisted["process_cleanup"], {"machine_observed": True, "operator_asserted": False})

    def test_recovery_runs_through_detached_supervisor(self):
        deployment_id = "123e4567-e89b-12d3-a456-426614174000"
        state = self.root / "state"
        with mock.patch.object(self.deploy, "spawn_supervisor", return_value=0) as supervisor:
            self.assertEqual(self.deploy.main(["recover", "--deployment-id", deployment_id, "--mode", "restore", "--state-root", str(state), "--confirm-processes-quiesced"]), 0)
        command = supervisor.call_args.args[0]
        self.assertIn("recover-supervise", command)
        self.assertIn("--confirm-processes-quiesced", command)

    def test_subprocess_diagnostics_and_redaction_do_not_expose_secret_sentinels(self):
        sentinel = "DO-NOT-STORE-THIS"
        failed = subprocess.CompletedProcess([], 17, "", f'Authorization: Bearer {sentinel} password: {sentinel}')
        with mock.patch.object(subprocess, "run", return_value=failed):
            with self.assertRaises(self.deploy.DeploymentError) as raised:
                self.deploy.run_git(["status"])
        self.assertNotIn(sentinel, str(raised.exception))
        self.assertNotIn(sentinel, self.deploy.redact(f'{{"password":"{sentinel}"}} token={sentinel}'))

    def test_image_recovery_classification_rejects_later_bytes(self):
        home = self.root / "image-home"
        container_dir = home / "containers"
        container_dir.mkdir(parents=True, mode=0o700)
        target = container_dir / "neovim_expected.sif"
        target.write_bytes(b"expected")
        target.chmod(0o600)
        (container_dir / "neovim.sif").symlink_to(target.name)
        resource = {
            "active_before": None,
            "previous_before": None,
            "intended_target": target.name,
            "intended_digest": hashlib.sha256(b"expected").hexdigest(),
        }
        self.deploy.classify_image_resource(resource, {"home": home})
        target.write_bytes(b"later user bytes")
        with self.assertRaisesRegex(self.deploy.DeploymentError, "later edits"):
            self.deploy.classify_image_resource(resource, {"home": home})

    def test_detached_supervisor_survives_controller_death(self):
        started = self.root / "supervisor-started"
        completed = self.root / "supervisor-completed"
        controller = self.root / "controller.py"
        controller.write_text(
            "import importlib.util, pathlib, sys\n"
            f"spec=importlib.util.spec_from_file_location('deploy', {str(ROOT / 'deploy/mkchad_deploy.py')!r})\n"
            "module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module)\n"
            f"raise SystemExit(module.spawn_supervisor([sys.executable, '-c', {('import pathlib,time; pathlib.Path(' + repr(str(started)) + ').write_text(\"started\"); time.sleep(0.5); pathlib.Path(' + repr(str(completed)) + ').write_text(\"completed\")')!r}]))\n",
            encoding="utf-8",
        )
        process = subprocess.Popen([sys.executable, str(controller)])
        for _ in range(100):
            if started.exists():
                break
            time.sleep(0.02)
        self.assertTrue(started.exists(), "supervisor did not start")
        process.kill()
        process.wait()
        for _ in range(100):
            if completed.exists():
                break
            time.sleep(0.02)
        self.assertTrue(completed.exists(), "supervisor died with its controller")

    def test_interlock_classifies_legacy_current_and_unknown_revisions(self):
        checkout = self.root / "interlock-checkout"
        contracts = checkout / "lua" / "mkchad" / "opencode" / "contracts.lua"
        contracts.parent.mkdir(parents=True)
        contracts.write_text("local M = { deployment_interlock_schema = 1 }\n", encoding="ascii")
        with mock.patch.object(self.deploy, "run_git", return_value=subprocess.CompletedProcess([], 0, self.deploy.LEGACY_MKCHAD_REVISION + "\n", "")):
            self.assertEqual(self.deploy.classify_existing_interlock(checkout), "legacy-v1")
        with mock.patch.object(self.deploy, "run_git", return_value=subprocess.CompletedProcess([], 0, "1" * 40 + "\n", "")):
            self.assertEqual(self.deploy.classify_existing_interlock(checkout), "host-lock-v1")
            contracts.write_text("local M = {}\n", encoding="ascii")
            with self.assertRaisesRegex(self.deploy.DeploymentError, "maintenance quiescence"):
                self.deploy.classify_existing_interlock(checkout)
            self.assertEqual(self.deploy.classify_existing_interlock(checkout, True), "operator-quiesced")

    def test_fresh_host_interlock_uses_shared_state_lock_and_rejects_links(self):
        roots = {
            "home": self.root / "home",
            "xdg_config_home": self.root / "config",
            "xdg_data_home": self.root / "data",
            "xdg_state_home": self.root / "state",
        }
        for path in roots.values():
            path.mkdir(mode=0o700)
        lock_path = roots["xdg_state_home"] / "mkchad" / "deployment.lock"
        with self.deploy.HostInterlock(roots):
            with self.assertRaisesRegex(self.deploy.DeploymentError, "another deployment"):
                with self.deploy.UserLock(lock_path):
                    pass
        lock_path.unlink()
        target = self.root / "unrelated"
        target.write_text("keep\n", encoding="ascii")
        lock_path.symlink_to(target)
        with self.assertRaisesRegex(self.deploy.DeploymentError, "unsafe"):
            with self.deploy.HostInterlock(roots):
                pass
        self.assertEqual(target.read_text(encoding="ascii"), "keep\n")

    def test_checkout_recovery_completes_and_restores_from_offline_bundle(self):
        data = self.root / "data"
        destination = data / "mkchad" / "components" / "msk_containers"
        previous = self.make_checkout(destination, "msk_containers", {"tracked": "old\n", "other": "old other\n"})
        (destination / "tracked").write_text("new\n", encoding="ascii")
        (destination / "other").write_text("new other\n", encoding="ascii")
        subprocess.run(["git", "-C", str(destination), "add", "tracked", "other"], check=True)
        subprocess.run(["git", "-C", str(destination), "-c", "user.name=test", "-c", "user.email=test@example.invalid", "commit", "-q", "-m", "new"], check=True)
        intended = subprocess.check_output(["git", "-C", str(destination), "rev-parse", "HEAD"], text=True).strip()
        bundle_root = self.root / "bundle"
        bundle_root.mkdir(mode=0o700)
        git_bundle = bundle_root / "msk_containers.bundle"
        subprocess.run(["git", "-C", str(destination), "bundle", "create", str(git_bundle), "--all"], check=True)
        git_bundle.chmod(0o600)
        subprocess.run(["git", "-C", str(destination), "checkout", "-q", "--detach", previous], check=True)

        class DirectScope:
            def run(self, argv, timeout=300, extra_env=None):
                environment = os.environ.copy()
                environment.update(extra_env or {})
                return subprocess.run(argv, check=True, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout, env=environment)

        component = self.release()["components"][0]
        authority = {"commit": intended, "repository": "github.com/krafczyk/msk_containers", "url": "git@github.com:krafczyk/msk_containers"}
        resource = {"kind": "checkout", "name": "msk_containers", "previous": previous, "intended": intended}
        roots = {"xdg_data_home": data}
        completed = self.deploy.recover_checkout_resource(resource, "complete", component, authority, bundle_root, roots, DirectScope())
        self.assertEqual(completed["revision"], intended)
        subprocess.run(["git", "-C", str(destination), "checkout", previous, "--", "tracked"], check=True)
        restored = self.deploy.recover_checkout_resource(resource, "restore", component, authority, bundle_root, roots, DirectScope())
        self.assertEqual(restored["revision"], previous)
        self.assertEqual((destination / "tracked").read_text(encoding="ascii"), "old\n")
        self.assertEqual((destination / "other").read_text(encoding="ascii"), "old other\n")

    def test_isolated_home_deployment_rehearsal_is_repeatable(self):
        fixture = self.root / "rehearsal"
        home = fixture / "home"
        config = fixture / "config"
        data = fixture / "data"
        state = fixture / "state"
        runtime = fixture / "runtime"
        for path in (home, config, data, state, runtime):
            path.mkdir(parents=True, mode=0o700)
        image = fixture / "nvim_container_x86.sif"
        image.write_bytes(b"fixture image")
        image_digest = hashlib.sha256(image.read_bytes()).hexdigest()
        opencode_nvim_commit = "3" * 40

        children = {}
        child_commits = {}
        for name in self.deploy.COMPONENT_DISPOSITIONS:
            child = fixture / (name.replace(".", "-") + "-source")
            files = {"tracked": name + "\n"}
            executable = set()
            if name == "msk_containers":
                files = {
                    "bin/install_nvim.sh": """#!/usr/bin/env bash
set -euo pipefail
[[ ${1:-} == --check ]] && exit 0
mkdir -p "$HOME/.local/bin"
repo=$(realpath "$(dirname "$(realpath "$0")")/..")
for source in "$repo"/container_tools/*.sh "$repo"/nvim/bin/*; do
  [[ -f $source ]] || continue
  target="$HOME/.local/bin/${source##*/}"
  cmp -s "$source" "$target" 2>/dev/null || { cp "$source" "$target"; chmod 755 "$target"; }
done
""",
                    "nvim/bin/mkchad-opencode-server": "#!/usr/bin/env bash\nprintf '%s\\n' '{\"status\":\"inactive\"}'\n",
                    "nvim/bin/install_nvim_container": """#!/usr/bin/env bash
set -euo pipefail
image=${@: -1}
mkdir -p "$HOME/containers"
target="$HOME/containers/neovim_fixture.sif"
[[ -e $target ]] || cp "$image" "$target"
ln -sfn "${target##*/}" "$HOME/containers/neovim.sif"
""",
                    "nvim/component-manifest.json": "{}\n",
                }
                executable = {"bin/install_nvim.sh", "nvim/bin/install_nvim_container", "nvim/bin/mkchad-opencode-server"}
            elif name == "mkchad":
                files = {
                    ".gitignore": "opencode-server.json\n",
                    "lua/mkchad/opencode/contracts.lua": f'local M = {{ deployment_interlock_schema = 1, opencode_nvim_revision = "{opencode_nvim_commit}" }}\nreturn M\n',
                }
            child_commits[name] = self.make_checkout(child, name, files, executable)
            children[name] = child
        opencode_nvim_commit = child_commits["opencode.nvim"]
        contracts = children["mkchad"] / "lua" / "mkchad" / "opencode" / "contracts.lua"
        contracts.write_text(
            f'local M = {{ deployment_interlock_schema = 1, opencode_nvim_revision = "{opencode_nvim_commit}" }}\nreturn M\n',
            encoding="ascii",
        )
        subprocess.run(["git", "-C", str(children["mkchad"]), "add", str(contracts.relative_to(children["mkchad"]))], check=True)
        subprocess.run(
            ["git", "-C", str(children["mkchad"]), "-c", "user.name=test", "-c", "user.email=test@example.invalid", "commit", "-q", "-m", "pin"],
            check=True,
        )
        child_commits["mkchad"] = subprocess.check_output(["git", "-C", str(children["mkchad"]), "rev-parse", "HEAD"], text=True).strip()
        container_tools = fixture / "container-tools-source"
        container_tools_commit = self.make_checkout(
            container_tools,
            "container_tools",
            {"ct_exec.sh": "#!/usr/bin/env bash\nexit 0\n"},
            executable={"ct_exec.sh"},
        )
        msk_source = children["msk_containers"]
        (msk_source / ".gitmodules").write_text(
            '[submodule "container_tools"]\n\tpath = container_tools\n\turl = git@github.com:krafczyk/container_tools\n',
            encoding="ascii",
        )
        subprocess.run(["git", "-C", str(msk_source), "add", ".gitmodules"], check=True)
        subprocess.run(
            ["git", "-C", str(msk_source), "update-index", "--add", "--cacheinfo", f"160000,{container_tools_commit},container_tools"],
            check=True,
        )
        subprocess.run(
            ["git", "-C", str(msk_source), "-c", "user.name=test", "-c", "user.email=test@example.invalid", "commit", "-q", "-m", "pin tools"],
            check=True,
        )
        child_commits["msk_containers"] = subprocess.check_output(["git", "-C", str(msk_source), "rev-parse", "HEAD"], text=True).strip()

        parent = fixture / "parent"
        (parent / "deploy").mkdir(parents=True)
        (parent / "docs" / "deployment").mkdir(parents=True)
        release = self.release()
        release["artifacts"]["nvim-image"][0]["sha256"] = image_digest
        release["artifacts"]["nvim-image"][0]["component_manifest_sha256"] = hashlib.sha256(b"{}\n").hexdigest()
        (parent / "deploy" / "release.json").write_text(json.dumps(release), encoding="utf-8")
        shutil.copy(ROOT / "deploy" / "release-v1.schema.json", parent / "deploy" / "release-v1.schema.json")
        shutil.copy(ROOT / "deploy" / "receipt-v1.schema.json", parent / "deploy" / "receipt-v1.schema.json")
        (parent / "docs" / "deployment" / "README.md").write_text("fixture guidance\n", encoding="ascii")
        modules = []
        for name in self.deploy.COMPONENT_DISPOSITIONS:
            modules.extend([f'[submodule "{name}"]', f"\tpath = {name}", f"\turl = git@github.com:krafczyk/{name}"])
        (parent / ".gitmodules").write_text("\n".join(modules) + "\n", encoding="utf-8")
        subprocess.run(["git", "init", "-q", str(parent)], check=True)
        subprocess.run(["git", "-C", str(parent), "add", "."], check=True)
        for name, commit in child_commits.items():
            subprocess.run(["git", "-C", str(parent), "update-index", "--add", "--cacheinfo", f"160000,{commit},{name}"], check=True)
        subprocess.run(["git", "-C", str(parent), "-c", "user.name=test", "-c", "user.email=test@example.invalid", "commit", "-q", "-m", "release"], check=True)
        parent_commit = subprocess.check_output(["git", "-C", str(parent), "rev-parse", "HEAD"], text=True).strip()
        evidence = fixture / "verified-release.json"
        evidence.write_text(json.dumps({"schema": 1, "deployer": self.deploy.DEPLOYER_ID, "authority": {"kind": "development-sha", "parent_commit": parent_commit}, "parent_bundle_sha256": "0" * 64}), encoding="utf-8")
        evidence.chmod(0o600)
        parent_bundle = fixture / "parent.bundle"
        subprocess.run(["git", "-C", str(parent), "bundle", "create", str(parent_bundle), "HEAD"], check=True)
        parent_bundle.chmod(0o600)
        evidence.write_text(json.dumps({"schema": 1, "deployer": self.deploy.DEPLOYER_ID, "authority": {"kind": "development-sha", "parent_commit": parent_commit}, "parent_bundle_sha256": hashlib.sha256(parent_bundle.read_bytes()).hexdigest()}), encoding="utf-8")
        evidence.chmod(0o600)

        class DirectScope:
            def __init__(self, *args, **kwargs):
                self.path = fixture / "direct-session"

            def __enter__(self):
                self.path.mkdir(parents=True, exist_ok=True)
                return self

            def __exit__(self, *_):
                return None

            def run(self, argv, timeout=300, extra_env=None):
                environment = os.environ.copy()
                environment.update(extra_env or {})
                return subprocess.run(argv, check=True, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout, env=environment)

        environment = {
            "HOME": str(home),
            "XDG_CONFIG_HOME": str(config),
            "XDG_DATA_HOME": str(data),
            "XDG_STATE_HOME": str(state),
            "XDG_RUNTIME_DIR": str(runtime),
        }
        stage_map = {name: children[name] for name in children}
        stage_map["msk_containers-container_tools"] = container_tools
        with mock.patch.dict(os.environ, environment, clear=False), mock.patch.object(self.deploy, "SessionScope", DirectScope), mock.patch.object(
            self.deploy, "_verified_evidence", return_value={"kind": "development-sha", "parent_commit": parent_commit}
        ), mock.patch.object(self.deploy, "stage_child", side_effect=lambda url, commit, staging, name, scope: stage_map[name]), mock.patch.object(
            self.deploy, "validate_image_runtime", return_value={"schema": 1}
        ), mock.patch.object(
            self.deploy, "expected_managed_image_name", return_value="neovim_fixture.sif"
        ):
            first = self.deploy.deploy(parent, evidence, parent_bundle, image)
            launcher_before = os.stat(home / ".local" / "bin" / "mkchad-opencode-server")
            second = self.deploy.deploy(parent, evidence, parent_bundle, image)
            launcher_after = os.stat(home / ".local" / "bin" / "mkchad-opencode-server")
        self.assertEqual(first["state"], "succeeded")
        self.assertEqual(second["state"], "succeeded")
        self.assertEqual((launcher_before.st_ino, launcher_before.st_mtime_ns), (launcher_after.st_ino, launcher_after.st_mtime_ns))
        self.assertEqual(subprocess.check_output(["git", "-C", str(config / "mkchad"), "rev-parse", "HEAD"], text=True).strip(), child_commits["mkchad"])


if __name__ == "__main__":
    unittest.main()
