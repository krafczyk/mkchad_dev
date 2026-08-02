"""Focused behavioral tests for the parent deployment engine."""

import copy
import hashlib
import importlib.util
import io
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

    def test_config_files_is_workspace_only(self):
        release = self.deploy.load_release_json(ROOT / "deploy" / "release.json")
        self.deploy.validate_release_document(release, list(self.deploy.COMPONENT_DISPOSITIONS))
        self.assertNotIn("config_files", [component["name"] for component in release["components"]])
        schema = json.loads((ROOT / "deploy" / "release-v1.schema.json").read_text(encoding="utf-8"))
        component_schema = schema["properties"]["components"]
        self.assertEqual((component_schema["minItems"], component_schema["maxItems"]), (7, 7))
        self.assertNotIn("config_files", component_schema["items"]["properties"]["name"]["enum"])
        self.assertEqual(
            subprocess.check_output(
                ["git", "config", "-f", str(ROOT / ".gitmodules"), "--get", "submodule.config_files.url"],
                text=True,
            ).strip(),
            "git@github.com:krafczyk/config_files.git",
        )
        self.assertEqual(
            subprocess.check_output(
                ["git", "config", "-f", str(ROOT / ".gitmodules"), "--get", "submodule.config_files.branch"],
                text=True,
            ).strip(),
            "schumann",
        )
        index_entry = subprocess.check_output(["git", "ls-files", "--stage", "config_files"], cwd=ROOT, text=True).split()
        self.assertEqual(index_entry[0], "160000")

    def test_release_gitlink_derivation_skips_workspace_only_metadata(self):
        modules = {
            "config_files": "not-a-release-repository",
            "mkchad": "git@github.com:krafczyk/mkchad",
        }
        tree = subprocess.CompletedProcess([], 0, "160000 commit " + "b" * 40 + "\tmkchad\n", "")
        with mock.patch.object(self.deploy, "_gitmodules_at", return_value=modules), mock.patch.object(
            self.deploy, "run_git", return_value=tree
        ) as run_git:
            gitlinks = self.deploy._derive_release_gitlinks(self.root, "a" * 40)
        self.assertEqual(set(gitlinks), {"mkchad"})
        run_git.assert_called_once_with(["ls-tree", "a" * 40, "--", "mkchad"], self.root)

    def test_validate_candidate_uses_release_only_gitlinks(self):
        gitlinks = {
            name: {"repository": f"github.com/krafczyk/{name}", "url": f"git@github.com:krafczyk/{name}", "commit": format(index, "040x")}
            for index, name in enumerate(self.deploy.COMPONENT_DISPOSITIONS, 1)
        }
        with mock.patch.object(self.deploy, "_derive_release_gitlinks", return_value=gitlinks), mock.patch.object(
            self.deploy, "_release_file", return_value=self.root / "release.json"
        ), mock.patch.object(self.deploy, "load_release_json", return_value=self.release()
        ):
            release, release_gitlinks = self.deploy.validate_candidate(self.root, "a" * 40)
        self.assertNotIn("config_files", release_gitlinks)
        self.assertNotIn("config_files", [component["name"] for component in release["components"]])

    def test_configuration_handoff_uses_only_gitlink_derived_pins(self):
        gitlinks = {
            name: {"repository": f"github.com/krafczyk/{name}", "commit": format(index, "040x")}
            for index, name in enumerate(self.deploy.COMPONENT_DISPOSITIONS, 1)
        }
        self.deploy.validate_release_document(self.release(), list(self.deploy.COMPONENT_DISPOSITIONS))
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
            result = self.deploy.main(
                [
                    "deploy",
                    "--verified-release",
                    str(evidence),
                    "--verified-parent-bundle",
                    str(bundle),
                    "--image",
                    str(image),
                    "--trust-group-writable-roots",
                ]
            )
        self.assertEqual(result, 0)
        command = supervisor.call_args.args[0]
        self.assertEqual(command[command.index("--verified-parent-bundle") + 1], str(bundle))
        self.assertIn("--trust-group-writable-roots", command)
        with mock.patch.object(self.deploy.sys, "stderr"):
            with self.assertRaises(SystemExit):
                self.deploy.main(
                    [
                        "deploy",
                        "--verified-release",
                        str(evidence),
                        "--verified-parent-bundle",
                        str(bundle),
                        "--image",
                        str(image),
                        "--trust",
                    ]
                )

    def test_managed_root_requires_explicit_group_write_trust(self):
        path = pathlib.Path("/managed/home")
        system = mock.Mock(st_uid=0, st_mode=self.deploy.stat.S_IFDIR | 0o755)
        managed = mock.Mock(st_uid=0, st_mode=self.deploy.stat.S_IFDIR | 0o770)
        with mock.patch.object(self.deploy.os, "lstat", side_effect=[system, managed]):
            with self.assertRaisesRegex(self.deploy.DeploymentError, "ownership or mode"):
                self.deploy._safe_owned_directory(path)
        with self.deploy._trusted_group_writable_roots([path]), mock.patch.object(
            self.deploy.os, "lstat", side_effect=[system, managed]
        ):
            self.deploy._safe_owned_directory(path)
        descendant = path / ".local"
        private_descendant = mock.Mock(st_uid=os.getuid(), st_mode=self.deploy.stat.S_IFDIR | 0o700)
        with self.deploy._trusted_group_writable_roots([path]), mock.patch.object(
            self.deploy.os, "lstat", side_effect=[system, managed, private_descendant]
        ):
            self.deploy._safe_owned_directory(descendant)
        writable_descendant = mock.Mock(st_uid=os.getuid(), st_mode=self.deploy.stat.S_IFDIR | 0o770)
        with self.deploy._trusted_group_writable_roots([path]), mock.patch.object(
            self.deploy.os, "lstat", side_effect=[system, managed, writable_descendant]
        ):
            with self.assertRaisesRegex(self.deploy.DeploymentError, "ownership or mode"):
                self.deploy._safe_owned_directory(descendant)
        world_writable = mock.Mock(st_uid=0, st_mode=self.deploy.stat.S_IFDIR | 0o777)
        with self.deploy._trusted_group_writable_roots([path]), mock.patch.object(
            self.deploy.os, "lstat", side_effect=[system, world_writable]
        ):
            with self.assertRaisesRegex(self.deploy.DeploymentError, "ownership or mode"):
                self.deploy._safe_owned_directory(path)
        foreign = mock.Mock(st_uid=os.getuid() + 1, st_mode=self.deploy.stat.S_IFDIR | 0o755)
        with self.deploy._trusted_group_writable_roots([path]), mock.patch.object(
            self.deploy.os, "lstat", side_effect=[foreign, managed]
        ):
            with self.assertRaisesRegex(self.deploy.DeploymentError, "ownership or mode"):
                self.deploy._safe_owned_directory(path)

    def test_supervisor_applies_group_writable_root_trust(self):
        repo = self.root / "repo"
        evidence = self.root / "evidence.json"
        bundle = self.root / "parent.bundle"
        image = self.root / "image.sif"
        roots = {"home": pathlib.Path("/managed/home"), "xdg_config_home": pathlib.Path("/managed/home/.config")}
        observed = []

        def record_trust(*_args):
            observed.append(self.deploy._TRUSTED_GROUP_WRITABLE_ROOTS.get())
            return {"state": "succeeded"}

        with mock.patch.object(self.deploy, "_roots", return_value=roots), mock.patch.object(
            self.deploy, "deploy", side_effect=record_trust
        ), mock.patch.object(self.deploy, "cleanup_bootstrap_root"), mock.patch("builtins.print"):
            result = self.deploy.main(
                [
                    "supervise",
                    "--repo",
                    str(repo),
                    "--verified-release",
                    str(evidence),
                    "--verified-parent-bundle",
                    str(bundle),
                    "--image",
                    str(image),
                    "--trust-group-writable-roots",
                ]
            )
        self.assertEqual(result, 0)
        self.assertEqual(observed, [frozenset(roots.values())])
        with mock.patch.object(self.deploy, "_roots", return_value=roots), mock.patch.object(
            self.deploy, "deploy", side_effect=record_trust
        ), mock.patch.object(self.deploy, "cleanup_bootstrap_root"), mock.patch("builtins.print"):
            result = self.deploy.main(
                [
                    "supervise",
                    "--repo",
                    str(repo),
                    "--verified-release",
                    str(evidence),
                    "--verified-parent-bundle",
                    str(bundle),
                    "--image",
                    str(image),
                ]
            )
        self.assertEqual(result, 0)
        self.assertEqual(observed[-1], frozenset())

    def test_installer_actions_receive_group_writable_root_trust(self):
        destination = self.root / "msk"
        destination.mkdir(mode=0o700)
        installer = destination / "installer"
        installer.write_text("#!/bin/sh\nexit 0\n", encoding="ascii")
        installer.chmod(0o700)
        bundle = self.root / "bundle"
        bundle.mkdir(mode=0o700)
        journal = mock.Mock(bundle=bundle)
        scope = mock.Mock()
        scope.run.return_value = subprocess.CompletedProcess([str(installer)], 0, "", "")
        component = {"name": "msk_containers", "actions": [{"id": "host-tools-check", "path": "installer", "args": []}]}
        with self.deploy._trusted_group_writable_roots([self.root]):
            self.deploy.run_installer_actions(
                scope,
                component,
                destination,
                self.root / "image.sif",
                "a" * 64,
                "managed.sif",
                "b" * 40,
                journal,
            )
        scope.run.assert_called_once_with(
            [str(installer)],
            extra_env={"MKCHAD_TRUST_GROUP_WRITABLE_ROOTS": "1"},
        )
        default_bundle = self.root / "default-bundle"
        default_bundle.mkdir(mode=0o700)
        default_scope = mock.Mock()
        default_scope.run.return_value = subprocess.CompletedProcess([str(installer)], 0, "", "")
        self.deploy.run_installer_actions(
            default_scope,
            component,
            destination,
            self.root / "image.sif",
            "a" * 64,
            "managed.sif",
            "b" * 40,
            mock.Mock(bundle=default_bundle),
        )
        default_scope.run.assert_called_once_with([str(installer)], extra_env=None)

    def test_complete_host_tool_recovery_receives_root_trust(self):
        bundle = self.root / "recovery-bundle"
        bundle.mkdir(mode=0o700)
        destination = self.root / "msk"
        destination.mkdir(mode=0o700)
        installer = destination / "installer"
        installer.write_text("#!/bin/sh\nexit 0\n", encoding="ascii")
        installer.chmod(0o700)
        component = {
            "name": "msk_containers",
            "destination": {"root": "xdg_data_home", "path": "mkchad/components/msk_containers"},
            "actions": [{"id": "host-tools-install", "path": "installer", "args": []}],
        }
        scope = mock.Mock()
        scope.run.return_value = subprocess.CompletedProcess([], 0, "", "")
        with self.deploy._trusted_group_writable_roots([self.root]), mock.patch.object(
            self.deploy, "resolve_named_destination", return_value=destination
        ), mock.patch.object(self.deploy, "verify_host_tools"):
            self.deploy.recover_host_tools({}, "complete", component, bundle, {"home": self.root}, scope)
        self.assertEqual(scope.run.call_args.kwargs["extra_env"], {"MKCHAD_TRUST_GROUP_WRITABLE_ROOTS": "1"})

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

    def test_image_runtime_uses_contained_writable_home(self):
        runtime = self.root / "singularity"
        runtime.write_text("#!/bin/sh\nexit 0\n", encoding="ascii")
        runtime.chmod(0o755)
        manifest_text = json.dumps(
            {
                "schema": 1,
                "relationships": [
                    {"id": "ships-opencode", "contract": {"version": "1.18.9-mkchad.2"}},
                ],
            }
        ) + "\n"
        artifact = {
            "architecture": "x86_64",
            "component_manifest_sha256": hashlib.sha256(manifest_text.encode()).hexdigest(),
        }

        class RecordingScope:
            def __init__(self):
                self.commands = []

            def run(self, argv, **_kwargs):
                self.commands.append(argv)
                if argv[-2:] == ["uname", "-m"]:
                    return subprocess.CompletedProcess(argv, 0, "x86_64\n", "")
                if argv[-2:] == ["cat", "/usr/share/mkchad/component-manifest.json"]:
                    return subprocess.CompletedProcess(argv, 0, manifest_text, "")
                if argv[-2:] == ["opencode", "--version"]:
                    return subprocess.CompletedProcess(argv, 0, "1.18.9-mkchad.2\n", "")
                raise AssertionError(f"unexpected command: {argv}")

        scope = RecordingScope()
        git_results = [
            subprocess.CompletedProcess([], 0, manifest_text, ""),
            subprocess.CompletedProcess([], 0, "v1.18.9-mkchad.2\n", ""),
        ]
        with mock.patch.dict(os.environ, {"MKCHAD_CONTAINER_RUNTIME": str(runtime)}), mock.patch.object(
            self.deploy, "_contained_git"
        ) as contained_git, mock.patch.object(self.deploy, "run_git", side_effect=git_results):
            self.deploy.validate_image_runtime(scope, self.root / "image.sif", artifact, self.root / "msk", "a" * 40, self.root / "opencode", {"url": "remote", "commit": "b" * 40})

        self.assertTrue(all("--contain" in command for command in scope.commands))
        self.assertEqual(scope.commands[-1][-4:], ["env", "HOME=/tmp", "opencode", "--version"])
        self.assertEqual(contained_git.call_args.args[1][-1], "refs/tags/v*:refs/tags/v*")

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

    def test_absent_checkout_fetches_unreferenced_staged_commit(self):
        source = self.root / "source"
        commit = self.make_checkout(source, "msk_containers")
        staged = self.root / "staged.git"
        subprocess.run(["git", "init", "-q", "--bare", str(staged)], check=True)
        subprocess.run(["git", "-C", str(staged), "fetch", "--no-tags", str(source), commit], check=True, stdout=subprocess.DEVNULL)
        self.assertNotEqual(subprocess.run(["git", "-C", str(staged), "show-ref", "--quiet"]).returncode, 0)

        parent = self.root / "components"
        parent.mkdir(mode=0o700)
        destination = parent / "msk_containers"
        component = {"name": "msk_containers"}
        authority = {"repository": "github.com/krafczyk/msk_containers", "url": "git@github.com:krafczyk/msk_containers", "commit": commit}
        preflight = self.deploy.preflight_component_destination(destination, component, authority, staged)
        state = self.root / "state"
        state.mkdir(mode=0o700)
        journal = self.deploy.DeploymentJournal(state, {"kind": "development-sha", "parent_commit": "a" * 40}, [])
        with self.deploy.SessionScope() as scope:
            result = self.deploy.install_or_update_component(destination, staged, component, authority, journal, scope, preflight)

        self.assertEqual(result["result"], "installed")
        self.assertEqual(subprocess.check_output(["git", "-C", str(destination), "rev-parse", "HEAD"], text=True).strip(), commit)
        self.assertEqual(subprocess.check_output(["git", "-C", str(destination), "remote", "get-url", "origin"], text=True).strip(), authority["url"])

    def test_preserved_mkchad_path_accepts_only_expected_link_or_private_file(self):
        checkout = self.root / "preserved"
        commit = self.make_checkout(checkout, "mkchad", {"tracked": "content\n"})
        preserved_paths = {"opencode-server.json", "lazy-lock.json"}
        preserved = checkout / "opencode-server.json"
        preserved.symlink_to("../../config_files/.config/mkchad/opencode-server.json")
        lazy_lock = checkout / "lazy-lock.json"
        lazy_lock.write_text("{}\n", encoding="ascii")
        lazy_lock.chmod(0o644)
        self.deploy.preflight_existing_checkout(checkout, "github.com/krafczyk/mkchad", commit, preserved_paths)
        preserved.unlink()
        preserved.symlink_to("/tmp/unexpected")
        with self.assertRaisesRegex(self.deploy.DeploymentError, "unexpected legacy link"):
            self.deploy.preflight_existing_checkout(checkout, "github.com/krafczyk/mkchad", commit, preserved_paths)
        preserved.unlink()
        preserved.write_text("{}\n", encoding="ascii")
        preserved.chmod(0o600)
        lazy_lock.chmod(0o666)
        with self.assertRaisesRegex(self.deploy.DeploymentError, "generated lock"):
            self.deploy.preflight_existing_checkout(checkout, "github.com/krafczyk/mkchad", commit, preserved_paths)
        with self.assertRaisesRegex(self.deploy.DeploymentError, "generated lock"):
            self.deploy.classify_checkout_mixture(checkout, "github.com/krafczyk/mkchad", commit, commit, preserved_paths)

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

    def test_session_scope_reports_exit_and_stderr_digest(self):
        stderr = b"expected failure\n"
        with self.assertRaises(self.deploy.DeploymentError) as raised:
            self.deploy.SessionScope().run([sys.executable, "-c", "import sys; sys.stderr.write('expected failure\\n'); sys.exit(7)"])
        self.assertEqual(str(raised.exception), f"supervised subprocess failed with exit 7; stderr-sha256={hashlib.sha256(stderr).hexdigest()}")

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
            self.assertEqual(
                self.deploy.main(
                    [
                        "recover",
                        "--deployment-id",
                        deployment_id,
                        "--mode",
                        "restore",
                        "--state-root",
                        str(state),
                        "--confirm-processes-quiesced",
                        "--trust-group-writable-roots",
                    ]
                ),
                0,
            )
        command = supervisor.call_args.args[0]
        self.assertIn("recover-supervise", command)
        self.assertIn("--confirm-processes-quiesced", command)
        self.assertIn("--trust-group-writable-roots", command)

    def test_recovery_supervisor_applies_group_writable_root_trust(self):
        deployment_id = "123e4567-e89b-12d3-a456-426614174000"
        state = self.root / "state"
        roots = {"home": pathlib.Path("/managed/home"), "xdg_state_home": pathlib.Path("/managed/home/.local/state")}
        observed = []

        def record_trust(*_args):
            observed.append(self.deploy._TRUSTED_GROUP_WRITABLE_ROOTS.get())
            return {"state": "recovered_restore"}

        with mock.patch.object(self.deploy, "_roots", return_value=roots), mock.patch.object(
            self.deploy, "recover", side_effect=record_trust
        ), mock.patch("builtins.print"):
            result = self.deploy.main(
                [
                    "recover-supervise",
                    "--deployment-id",
                    deployment_id,
                    "--mode",
                    "restore",
                    "--state-root",
                    str(state),
                    "--trust-group-writable-roots",
                ]
            )
        self.assertEqual(result, 0)
        self.assertEqual(observed, [frozenset(roots.values())])

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

    def test_tree_entries_allow_dotfiles_but_reject_git_metadata(self):
        object_id = "a" * 40
        valid = subprocess.CompletedProcess([], 0, f"100644 blob {object_id}\t.gitignore\0", "")
        with mock.patch.object(self.deploy, "run_git", return_value=valid):
            self.assertEqual(self.deploy._tree_entries(self.root, "b" * 40), {".gitignore": ("100644", "blob", object_id)})

        for path in (".git/config", "nested/.GIT/config", "../outside", "/absolute"):
            invalid = subprocess.CompletedProcess([], 0, f"100644 blob {object_id}\t{path}\0", "")
            with mock.patch.object(self.deploy, "run_git", return_value=invalid):
                with self.assertRaisesRegex(self.deploy.DeploymentError, "unsupported path"):
                    self.deploy._tree_entries(self.root, "b" * 40)

    def test_complete_accepts_exact_recreated_checkout_but_restore_requires_identity(self):
        data = self.root / "data"
        destination = data / "mkchad" / "components" / "msk_containers"
        intended = self.make_checkout(destination, "msk_containers")
        component = self.release()["components"][0]
        authority = {
            "commit": intended,
            "repository": "github.com/krafczyk/msk_containers",
            "url": "git@github.com:krafczyk/msk_containers",
        }
        recorded_identity = list(self.deploy._identity(destination))
        recorded_identity[1] += 1
        resource = {
            "kind": "checkout",
            "name": "msk_containers",
            "previous": None,
            "intended": intended,
            "identity": recorded_identity,
            "state": "installed",
        }
        components = {"msk_containers": component}
        authorities = {"msk_containers": authority}
        roots = {"xdg_data_home": data}

        self.deploy.classify_recovery_resources([resource], components, authorities, roots, "complete")
        with self.assertRaisesRegex(self.deploy.DeploymentError, "identity changed"):
            self.deploy.classify_recovery_resources([resource], components, authorities, roots, "restore")
        (destination / "tracked").write_text("later edit\n", encoding="ascii")
        with self.assertRaisesRegex(self.deploy.DeploymentError, "identity changed"):
            self.deploy.classify_recovery_resources([resource], components, authorities, roots, "complete")

    def test_checkout_recovery_completes_and_restores_from_offline_bundle(self):
        data = self.root / "data"
        destination = data / "mkchad" / "components" / "msk_containers"
        previous = self.make_checkout(
            destination,
            "msk_containers",
            {".gitignore": "generated\n", ".stylua.toml": "indent_type = 'Spaces'\n", "tracked": "old\n", "other": "old other\n"},
        )
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
        self.assertEqual((destination / ".gitignore").read_text(encoding="ascii"), "generated\n")
        self.assertEqual((destination / ".stylua.toml").read_text(encoding="ascii"), "indent_type = 'Spaces'\n")
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
        for name in [*self.deploy.COMPONENT_DISPOSITIONS, "config_files"]:
            modules.extend([f'[submodule "{name}"]', f"\tpath = {name}", f"\turl = git@github.com:krafczyk/{name}"])
        (parent / ".gitmodules").write_text("\n".join(modules) + "\n", encoding="utf-8")
        subprocess.run(["git", "init", "-q", str(parent)], check=True)
        subprocess.run(["git", "-C", str(parent), "add", "."], check=True)
        for name, commit in child_commits.items():
            subprocess.run(["git", "-C", str(parent), "update-index", "--add", "--cacheinfo", f"160000,{commit},{name}"], check=True)
        subprocess.run(
            ["git", "-C", str(parent), "update-index", "--add", "--cacheinfo", f"160000,{'f' * 40},config_files"],
            check=True,
        )
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


class DeploymentPreflightTests(unittest.TestCase):
    """Exercise schema-2 staging and preflight without final-path mutation."""

    def setUp(self):
        self.deploy = load_module("mkchad_deploy_preflight", "deploy/mkchad_deploy.py")
        self.root = pathlib.Path(tempfile.mkdtemp(prefix="mkchad-v2-preflight-", dir="/tmp/mkchad-v1"))
        self.addCleanup(shutil.rmtree, self.root, True)
        self.home = self.root / "home"
        self.config = self.root / "config"
        self.data = self.root / "data"
        self.state = self.root / "state"
        for path in (self.home, self.config, self.data, self.state):
            path.mkdir(mode=0o700)
        self.roots = {"home": self.home, "xdg_config_home": self.config, "xdg_data_home": self.data, "xdg_state_home": self.state}
        self.binary = self.root / "bin"
        self.binary.mkdir(mode=0o700)
        environment = {
            "HOME": str(self.home),
            "XDG_CONFIG_HOME": str(self.config),
            "XDG_DATA_HOME": str(self.data),
            "XDG_STATE_HOME": str(self.state),
            "PATH": str(self.binary) + os.pathsep + os.environ["PATH"],
        }
        patcher = mock.patch.dict(os.environ, environment)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.commits = self._sources()

    def _repo(self, name, files, executable=()):
        repository = self.root / "sources" / name
        repository.mkdir(parents=True)
        for relative, content in files.items():
            path = repository / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="ascii")
            if relative in executable:
                path.chmod(0o755)
        subprocess.run(["git", "init", "-q", str(repository)], check=True)
        subprocess.run(["git", "-C", str(repository), "add", "."], check=True)
        subprocess.run(["git", "-C", str(repository), "-c", "user.name=test", "-c", "user.email=test@example.invalid", "commit", "-q", "-m", name], check=True)
        return subprocess.check_output(["git", "-C", str(repository), "rev-parse", "HEAD"], text=True).strip()

    def _sources(self):
        msk = self._repo(
            "msk_containers",
            {
                "bin/install_nvim.sh": "#!/bin/sh\n[ \"$1\" = --check ] || exit 2\nexit \"${HOST_PREFLIGHT_STATUS:-0}\"\n",
                "nvim/bin/install_nvim_container": "#!/bin/sh\n[ \"$1\" = --dry-run ] || exit 2\nexit \"${IMAGE_PREFLIGHT_STATUS:-0}\"\n",
                "nvim/bin/nvim": "#!/bin/sh\nexit 0\n",
                "plain.sh": "not executable\n",
            },
            {"bin/install_nvim.sh", "nvim/bin/install_nvim_container", "nvim/bin/nvim"},
        )
        tools = self._repo("container_tools", {"tool.sh": "#!/bin/sh\nexit 0\n"}, {"tool.sh"})
        mkchad = self._repo("mkchad", {".gitignore": "opencode-server.json\nlazy-lock.json\n", "init.lua": "return {}\n"})
        (self.home / ".gitconfig").write_text("[url \"file://" + str(self.root / "sources") + "/\"]\n\tinsteadOf = git@github.com:krafczyk/\n", encoding="ascii")
        return {"msk_containers": msk, "container_tools": tools, "mkchad": mkchad}

    def _manifest(self, *, mutate=None):
        manifest = json.loads((ROOT / "tests" / "fixtures" / "deployment" / "release-v2.json").read_text(encoding="utf-8"))
        for component in manifest["components"]:
            if component["name"] in self.commits:
                component["commit"] = self.commits[component["name"]]
        image = self.root / "image.sif"
        image.write_bytes(b"fixture sif\n")
        manifest["artifacts"]["nvim-image"][0]["sha256"] = hashlib.sha256(image.read_bytes()).hexdigest()
        if mutate:
            mutate(manifest, image)
        path = self.root / "release-v2.json"
        path.write_text(json.dumps(manifest), encoding="utf-8")
        return path, image, manifest

    def _runtime(self, name="apptainer", architecture="x86_64"):
        runtime = self.binary / name
        runtime.write_text("#!/bin/sh\nprintf '%s\\n' \"$FAKE_IMAGE_ARCH\"\n", encoding="ascii")
        runtime.chmod(0o755)
        os.environ["FAKE_IMAGE_ARCH"] = architecture

    def _lifecycle(self, payload, status=0):
        launcher = self.home / ".local" / "bin" / "mkchad-opencode-server"
        launcher.parent.mkdir(parents=True, exist_ok=True)
        launcher.write_text("#!/bin/sh\nprintf '%s\\n' '" + json.dumps(payload) + "'\nexit " + str(status) + "\n", encoding="ascii")
        launcher.chmod(0o755)
        return launcher

    def _checkout(self, name):
        destination = self.data / "mkchad" / "components" / "msk_containers" / "container_tools" if name == "container_tools" else (self.data / "mkchad" / "components" / "msk_containers" if name == "msk_containers" else self.config / "mkchad")
        subprocess.run(["git", "clone", "-q", str(self.root / "sources" / name), str(destination)], check=True)
        subprocess.run(["git", "-C", str(destination), "remote", "set-url", "origin", "git@github.com:krafczyk/" + name], check=True)
        return destination

    def _preflight(self, **kwargs):
        manifest, image, _ = self._manifest(**kwargs)
        return self.deploy.preflight_release(self.root, image, manifest_path=manifest, roots=self.roots, staging_root=self.root)

    def test_schema_2_preflight_is_available_without_checkout_mutation(self):
        self._runtime()
        result = self._preflight()
        self.assertEqual(result["state"], "preflighted")
        self.assertEqual(result["lifecycle"], "missing")
        self.assertFalse((self.data / "mkchad" / "components" / "msk_containers").exists())
        self.assertFalse((self.config / "mkchad").exists())

    def test_staging_rejects_unavailable_commit_and_bad_action_entries(self):
        self._runtime()
        with self.assertRaisesRegex(self.deploy.DeploymentError, "unavailable"):
            self._preflight(mutate=lambda manifest, image: manifest["components"][0].__setitem__("commit", "f" * 40))
        with self.assertRaisesRegex(self.deploy.DeploymentError, "action path"):
            self._preflight(mutate=lambda manifest, image: manifest["components"][0]["actions"][0].__setitem__("path", "../outside"))
        for path, mode, message in [
            ("missing.sh", None, "absent"),
            ("plain.sh", None, "executable"),
            ("link.sh", "symlink", "regular"),
        ]:
            with self.subTest(path=path):
                source = self.root / "sources" / "msk_containers"
                if mode == "symlink":
                    target = source / path
                    target.symlink_to("bin/install_nvim.sh")
                    subprocess.run(["git", "-C", str(source), "add", path], check=True)
                    subprocess.run(["git", "-C", str(source), "-c", "user.name=test", "-c", "user.email=test@example.invalid", "commit", "-q", "-m", "link"], check=True)
                    self.commits["msk_containers"] = subprocess.check_output(["git", "-C", str(source), "rev-parse", "HEAD"], text=True).strip()
                with self.assertRaisesRegex(self.deploy.DeploymentError, message):
                    self._preflight(mutate=lambda manifest, image, path=path: manifest["components"][0]["actions"][0].__setitem__("path", path))

    def test_image_digest_architecture_runtime_and_dry_run_fail_before_targets(self):
        self._runtime(architecture="aarch64")
        with self.assertRaisesRegex(self.deploy.DeploymentError, "architecture"):
            self._preflight()
        self._runtime(architecture="x86_64")
        with self.assertRaisesRegex(self.deploy.DeploymentError, "digest"):
            self._preflight(mutate=lambda manifest, image: manifest["artifacts"]["nvim-image"][0].__setitem__("sha256", "0" * 64))
        os.environ["IMAGE_PREFLIGHT_STATUS"] = "7"
        with self.assertRaisesRegex(self.deploy.DeploymentError, "image-install-preflight"):
            self._preflight()
        self.assertFalse((self.data / "mkchad" / "components" / "msk_containers").exists())

    def test_runtime_prefers_apptainer_falls_back_to_singularity_and_requires_one(self):
        image = self.root / "runtime.sif"
        image.write_bytes(b"x")
        completed = subprocess.CompletedProcess([], 0, "x86_64\n", "")
        with mock.patch.object(self.deploy.shutil, "which", side_effect=["/bin/apptainer", "/bin/singularity"]), mock.patch.object(self.deploy, "_v2_capture", return_value=completed) as run:
            self.deploy._v2_validate_image_runtime(image, "x86_64")
        self.assertEqual(run.call_args.args[0][0], "/bin/apptainer")
        with mock.patch.object(self.deploy.shutil, "which", side_effect=[None, "/bin/singularity"]), mock.patch.object(self.deploy, "_v2_capture", return_value=completed) as run:
            self.deploy._v2_validate_image_runtime(image, "x86_64")
        self.assertEqual(run.call_args.args[0][0], "/bin/singularity")
        with mock.patch.object(self.deploy.shutil, "which", return_value=None):
            with self.assertRaisesRegex(self.deploy.DeploymentError, "no Apptainer"):
                self.deploy._v2_validate_image_runtime(image, "x86_64")

    def test_destinations_require_expected_clean_checkouts_and_narrow_preservation(self):
        self._runtime()
        self._lifecycle({"schema": 1, "ok": True, "command": "status", "status": "inactive"})
        checkout = self._checkout("mkchad")
        (checkout / "opencode-server.json").write_text("private\n", encoding="ascii")
        result = self._preflight()
        self.assertEqual(result["components"]["mkchad"]["kind"], "existing")
        (checkout / "untracked").write_text("dirty\n", encoding="ascii")
        with self.assertRaisesRegex(self.deploy.DeploymentError, "local work"):
            self._preflight()
        (checkout / "untracked").unlink()
        (checkout / "init.lua").write_text("changed\n", encoding="ascii")
        with self.assertRaisesRegex(self.deploy.DeploymentError, "local work"):
            self._preflight()
        subprocess.run(["git", "-C", str(checkout), "checkout", "--", "init.lua"], check=True)
        subprocess.run(["git", "-C", str(checkout), "remote", "set-url", "origin", "git@github.com:krafczyk/other"], check=True)
        with self.assertRaisesRegex(self.deploy.DeploymentError, "wrong origin"):
            self._preflight()

    def test_all_destinations_are_checked_before_preflight_actions(self):
        self._runtime()
        marker = self.root / "action-ran"
        source = self.root / "sources" / "msk_containers" / "bin" / "install_nvim.sh"
        source.write_text("#!/bin/sh\ntouch '" + str(marker) + "'\nexit 0\n", encoding="ascii")
        subprocess.run(["git", "-C", str(source.parent.parent), "add", "bin/install_nvim.sh"], check=True)
        subprocess.run(["git", "-C", str(source.parent.parent), "-c", "user.name=test", "-c", "user.email=test@example.invalid", "commit", "-q", "-m", "marker"], check=True)
        self.commits["msk_containers"] = subprocess.check_output(["git", "-C", str(source.parent.parent), "rev-parse", "HEAD"], text=True).strip()
        bad = self.config / "mkchad"
        bad.parent.mkdir(parents=True, exist_ok=True)
        bad.write_text("not a checkout\n", encoding="ascii")
        with self.assertRaisesRegex(self.deploy.DeploymentError, "expected Git checkout"):
            self._preflight()
        self.assertFalse(marker.exists())

    def test_lock_and_lifecycle_classification_cover_fresh_partial_and_active_targets(self):
        self._runtime()
        with self.deploy.UserLock(self.state / "mkchad" / "deployment.lock"):
            with self.assertRaisesRegex(self.deploy.DeploymentError, "another deployment"):
                self._preflight()
        tool = self.home / ".local" / "bin" / "nvim"
        tool.parent.mkdir(parents=True, exist_ok=True)
        tool.write_text("partial\n", encoding="ascii")
        with self.assertRaisesRegex(self.deploy.DeploymentError, "partial target"):
            self._preflight()
        tool.unlink()
        self._lifecycle({"schema": 1, "ok": True, "command": "status", "status": "inactive"})
        self.assertEqual(self._preflight()["lifecycle"], "inactive")
        self._lifecycle({"schema": 1, "ok": True, "command": "status", "status": "healthy"})
        with self.assertRaisesRegex(self.deploy.DeploymentError, "not completely compliant"):
            self._preflight()
        with mock.patch.object(self.deploy, "_v2_complete_compliance", return_value=True):
            self.assertTrue(self._preflight()["verify_only"])

    def test_lifecycle_rejects_malformed_failed_and_timed_out_status(self):
        self._runtime()
        for payload, status in [({"schema": 2, "ok": True, "command": "status", "status": "inactive"}, 0), ({"schema": 1, "ok": False, "command": "status", "status": "blocked"}, 1)]:
            self._lifecycle(payload, status)
            with self.subTest(payload=payload), self.assertRaisesRegex(self.deploy.DeploymentError, "status"):
                self._preflight()
        self._lifecycle({"schema": 1, "ok": True, "command": "status", "status": "inactive"})
        plans = {name: (self.root / name, {"kind": "absent", "compliant": False}) for name in self.commits}
        with mock.patch.object(self.deploy, "_v2_capture", side_effect=self.deploy.DeploymentError("command timed out")):
            with self.assertRaisesRegex(self.deploy.DeploymentError, "status"):
                self.deploy._v2_lifecycle(self.roots, plans, self.root / "sources" / "msk_containers", {"sha256": "0" * 64}, 40)



class DeploymentCheckoutTests(unittest.TestCase):
    """Exercise exact schema-2 checkout application from held staging inputs."""

    def setUp(self):
        self.deploy = load_module("mkchad_deploy_checkouts", "deploy/mkchad_deploy.py")
        self.root = pathlib.Path(tempfile.mkdtemp(prefix="mkchad-v2-checkouts-", dir="/tmp/mkchad-v1"))
        self.addCleanup(shutil.rmtree, self.root, True)
        self.home = self.root / "home"
        self.config = self.root / "config"
        self.data = self.root / "data"
        self.state = self.root / "state"
        for path in (self.home, self.config, self.data, self.state):
            path.mkdir(mode=0o700)
        self.roots = {"home": self.home, "xdg_config_home": self.config, "xdg_data_home": self.data, "xdg_state_home": self.state}
        self.binary = self.root / "bin"
        self.binary.mkdir(mode=0o700)
        patcher = mock.patch.dict(os.environ, {
            "HOME": str(self.home), "XDG_CONFIG_HOME": str(self.config), "XDG_DATA_HOME": str(self.data),
            "XDG_STATE_HOME": str(self.state), "PATH": str(self.binary) + os.pathsep + os.environ["PATH"],
        })
        patcher.start()
        self.addCleanup(patcher.stop)
        self.commits = self._sources()
        self._runtime()
        self._lifecycle()

    def _commit(self, repository, message, stage=True):
        if stage:
            subprocess.run(["git", "-C", str(repository), "add", "."], check=True)
        subprocess.run(["git", "-C", str(repository), "-c", "user.name=test", "-c", "user.email=test@example.invalid", "commit", "-q", "-m", message], check=True)
        return subprocess.check_output(["git", "-C", str(repository), "rev-parse", "HEAD"], text=True).strip()

    def _repo(self, name, files, executable=()):
        repository = self.root / "sources" / name
        repository.mkdir(parents=True)
        for relative, content in files.items():
            path = repository / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="ascii")
            if relative in executable:
                path.chmod(0o755)
        subprocess.run(["git", "init", "-q", str(repository)], check=True)
        return repository, self._commit(repository, "initial")

    def _sources(self):
        tools, old_tools = self._repo("container_tools", {"tool.sh": "#!/bin/sh\necho old\n"}, {"tool.sh"})
        (tools / "tool.sh").write_text("#!/bin/sh\necho manifest\n", encoding="ascii")
        tools_commit = self._commit(tools, "manifest tools")
        msk, old_msk = self._repo("msk_containers", {
            "bin/install_nvim.sh": "#!/bin/sh\nexit 0\n", "nvim/bin/install_nvim_container": "#!/bin/sh\nexit 0\n",
            ".gitmodules": '[submodule "container_tools"]\n\tpath = container_tools\n\turl = git@github.com:krafczyk/container_tools\n',
        }, {"bin/install_nvim.sh", "nvim/bin/install_nvim_container"})
        subprocess.run(["git", "-C", str(msk), "update-index", "--add", "--cacheinfo", f"160000,{old_tools},container_tools"], check=True)
        msk_commit = self._commit(msk, "parent gitlink differs from manifest", stage=False)
        mkchad, old_mkchad = self._repo("mkchad", {".gitignore": "opencode-server.json\nlazy-lock.json\n", "init.lua": "return { old = true }\n"})
        (mkchad / "init.lua").write_text("return { manifest = true }\n", encoding="ascii")
        mkchad_commit = self._commit(mkchad, "manifest mkchad")
        (self.home / ".gitconfig").write_text("[url \"file://" + str(self.root / "sources") + "/\"]\n\tinsteadOf = git@github.com:krafczyk/\n", encoding="ascii")
        return {"container_tools": tools_commit, "msk_containers": msk_commit, "mkchad": mkchad_commit,
                "old_container_tools": old_tools, "old_msk_containers": old_msk, "old_mkchad": old_mkchad}

    def _runtime(self):
        runtime = self.binary / "apptainer"
        runtime.write_text("#!/bin/sh\nprintf '%s\\n' x86_64\n", encoding="ascii")
        runtime.chmod(0o755)

    def _lifecycle(self):
        launcher = self.home / ".local" / "bin" / "mkchad-opencode-server"
        launcher.parent.mkdir(parents=True, exist_ok=True)
        launcher.write_text("#!/bin/sh\nprintf '%s\\n' '{\"schema\": 1, \"ok\": true, \"command\": \"status\", \"status\": \"inactive\"}'\n", encoding="ascii")
        launcher.chmod(0o755)

    def _manifest(self):
        manifest = json.loads((ROOT / "tests" / "fixtures" / "deployment" / "release-v2.json").read_text(encoding="utf-8"))
        for component in manifest["components"]:
            if component["name"] in self.commits:
                component["commit"] = self.commits[component["name"]]
        image = self.root / "image.sif"
        image.write_bytes(b"fixture sif\n")
        manifest["artifacts"]["nvim-image"][0]["sha256"] = hashlib.sha256(image.read_bytes()).hexdigest()
        path = self.root / "release-v2.json"
        path.write_text(json.dumps(manifest), encoding="utf-8")
        return path, image

    def _destination(self, name):
        if name == "mkchad":
            return self.config / "mkchad"
        base = self.data / "mkchad" / "components" / "msk_containers"
        return base / "container_tools" if name == "container_tools" else base

    def _checkout(self, name, commit):
        destination = self._destination(name)
        destination.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "clone", "-q", str(self.root / "sources" / name), str(destination)], check=True)
        subprocess.run(["git", "-C", str(destination), "checkout", "-q", "--detach", commit], check=True)
        subprocess.run(["git", "-C", str(destination), "remote", "set-url", "origin", "git@github.com:krafczyk/" + name], check=True)
        return destination

    def _prepared(self):
        manifest, image = self._manifest()
        return self.deploy.preflight_release_inputs(self.root, image, manifest_path=manifest, roots=self.roots, staging_root=self.root)

    def _head(self, name):
        return subprocess.check_output(["git", "-C", str(self._destination(name)), "rev-parse", "HEAD"], text=True).strip()

    def test_fresh_apply_installs_manifest_pins_and_verifies_nested_divergence(self):
        with self._prepared() as prepared:
            result = self.deploy.apply_preflighted_checkouts(prepared)
        self.assertEqual([item["name"] for item in result], ["msk_containers", "container_tools", "mkchad"])
        self.assertEqual([item["result"] for item in result], ["installed", "installed", "installed"])
        for name in ("msk_containers", "container_tools", "mkchad"):
            self.assertEqual(self._head(name), self.commits[name])
            self.assertEqual(subprocess.check_output(["git", "-C", str(self._destination(name)), "config", "--get", "remote.origin.url"], text=True).strip(), "git@github.com:krafczyk/" + name)
        self.assertNotEqual(self.commits["container_tools"], self.commits["old_container_tools"])

    def test_clean_updates_preserve_declared_mkchad_files_and_rerun_is_noop(self):
        for name in ("msk_containers", "container_tools", "mkchad"):
            self._checkout(name, self.commits["old_" + name])
        preserved = {"opencode-server.json": "private\n", "lazy-lock.json": "{\"lock\": true}\n"}
        for name, content in preserved.items():
            (self._destination("mkchad") / name).write_text(content, encoding="ascii")
        with self._prepared() as prepared:
            updated = self.deploy.apply_preflighted_checkouts(prepared)
        self.assertEqual([item["result"] for item in updated], ["updated", "updated", "updated"])
        self.assertEqual({name: (self._destination("mkchad") / name).read_text(encoding="ascii") for name in preserved}, preserved)
        with self._prepared() as prepared:
            repeated = self.deploy.apply_preflighted_checkouts(prepared)
        self.assertEqual([item["result"] for item in repeated], ["skipped-compliant"] * 3)

    def test_manifest_cannot_overwrite_a_declared_preserved_path(self):
        source = self.root / "sources" / "mkchad"
        (source / "opencode-server.json").write_text("release owned\n", encoding="ascii")
        subprocess.run(["git", "-C", str(source), "add", "-f", "opencode-server.json"], check=True)
        self.commits["mkchad"] = self._commit(source, "incorrectly own preserved path", stage=False)
        checkout = self._checkout("mkchad", self.commits["old_mkchad"])
        preserved = checkout / "opencode-server.json"
        preserved.write_text("private\n", encoding="ascii")
        with self.assertRaisesRegex(self.deploy.DeploymentError, "owns preserved path"):
            with self._prepared():
                pass
        self.assertEqual(preserved.read_text(encoding="ascii"), "private\n")

    def test_nested_checkout_is_independently_refused_when_dirty_or_wrong_origin(self):
        self._checkout("msk_containers", self.commits["msk_containers"])
        tools = self._checkout("container_tools", self.commits["container_tools"])
        (tools / "dirty").write_text("no\n", encoding="ascii")
        with self.assertRaisesRegex(self.deploy.DeploymentError, "local work"):
            with self._prepared():
                pass
        (tools / "dirty").unlink()
        subprocess.run(["git", "-C", str(tools), "remote", "set-url", "origin", "git@github.com:krafczyk/other"], check=True)
        with self.assertRaisesRegex(self.deploy.DeploymentError, "wrong origin"):
            with self._prepared():
                pass

    def test_failure_retains_earlier_success_and_rerun_completes(self):
        original = self.deploy._v2_apply_checkout

        def fail_mkchad(prepared, name):
            if name == "mkchad":
                raise self.deploy.DeploymentError("forced mkchad failure")
            return original(prepared, name)

        with self.assertRaisesRegex(self.deploy.DeploymentError, "forced mkchad"):
            with self._prepared() as prepared, mock.patch.object(self.deploy, "_v2_apply_checkout", side_effect=fail_mkchad):
                self.deploy.apply_preflighted_checkouts(prepared)
        self.assertEqual(self._head("msk_containers"), self.commits["msk_containers"])
        self.assertEqual(self._head("container_tools"), self.commits["container_tools"])
        self.assertFalse(self._destination("mkchad").exists())
        with self._prepared() as prepared:
            result = self.deploy.apply_preflighted_checkouts(prepared)
        self.assertEqual([item["result"] for item in result], ["skipped-compliant", "skipped-compliant", "installed"])

    def test_destination_appearance_and_interruption_leave_ordinary_git_state(self):
        original = self.deploy._v2_prepare_checkout

        def appear_then_prepare(staged, temporary, component):
            original(staged, temporary, component)
            self._destination(component["name"]).mkdir()

        with self.assertRaisesRegex(self.deploy.DeploymentError, "destination appeared"):
            with self._prepared() as prepared, mock.patch.object(self.deploy, "_v2_prepare_checkout", side_effect=appear_then_prepare):
                self.deploy.apply_preflighted_checkouts(prepared)
        self.assertTrue(self._destination("msk_containers").is_dir())
        shutil.rmtree(self._destination("msk_containers"))
        with self.assertRaises(KeyboardInterrupt):
            with self._prepared() as prepared, mock.patch.object(self.deploy, "_v2_prepare_checkout", side_effect=KeyboardInterrupt):
                self.deploy.apply_preflighted_checkouts(prepared)
        self.assertFalse((self.state / "mkchad" / "deploy").exists())


class DeploymentContractTests(unittest.TestCase):
    """Exercise the future release manifest and terminal result contracts."""

    def setUp(self):
        self.deploy = load_module("mkchad_deploy_contracts", "deploy/mkchad_deploy.py")

    def release(self):
        return {
            "schema": 2,
            "components": [
                {
                    "name": "msk_containers",
                    "disposition": "deployed",
                    "repository": "git@github.com:krafczyk/msk_containers",
                    "commit": "a084416bf54b113677f5227380916e9003591111",
                    "destination": {"root": "xdg_data_home", "path": "mkchad/components/msk_containers"},
                    "actions": [
                        {"id": "host-tools-preflight", "phase": "preflight", "path": "bin/install_nvim.sh", "args": ["--check", "--recovery-dir", {"token": "recovery_dir"}]},
                        {"id": "image-install-preflight", "phase": "preflight", "path": "nvim/bin/install_nvim_container", "args": ["--dry-run", "--source-revision", {"token": "component_revision"}, {"token": "staged_image"}]},
                        {"id": "host-tools-install", "phase": "apply", "path": "bin/install_nvim.sh", "args": ["--recovery-dir", {"token": "recovery_dir"}]},
                        {"id": "image-install", "phase": "apply", "path": "nvim/bin/install_nvim_container", "args": ["--source-revision", {"token": "component_revision"}, {"token": "staged_image"}]},
                        {"id": "host-tools-verify", "phase": "verify", "path": "bin/install_nvim.sh", "args": ["--check"]},
                    ],
                },
                {
                    "name": "container_tools",
                    "disposition": "deployed",
                    "repository": "git@github.com:krafczyk/container_tools",
                    "commit": "22ffd7f3e8a52ec5a40dd95cb7428a7128830bc7",
                    "destination": {"root": "xdg_data_home", "path": "mkchad/components/msk_containers/container_tools"},
                    "actions": [],
                },
                {
                    "name": "mkchad",
                    "disposition": "deployed",
                    "repository": "git@github.com:krafczyk/mkchad",
                    "commit": "fb089cf345b33c7fd87dcc1d56677766c5719708",
                    "destination": {"root": "xdg_config_home", "path": "mkchad"},
                    "actions": [],
                    "preserved_paths": ["opencode-server.json", "lazy-lock.json"],
                },
                {"name": "opencode", "disposition": "embedded", "repository": "git@github.com:krafczyk/opencode.git", "commit": "a8a797ab717289fe30308b1e3bcc8dfa865beb4a"},
                {"name": "opencode.nvim", "disposition": "configuration-owned", "repository": "git@github.com:krafczyk/opencode.nvim", "commit": "37033dc157ac4c05c1e1525fe2fc9e87ae83e2ac"},
                {"name": "opencode-project-reload", "disposition": "configuration-owned", "repository": "git@github.com:krafczyk/opencode-project-reload.git", "commit": "4548302d5b79ab079b2a9d503a4556c6119cbab2"},
                {"name": "compound-engineering", "disposition": "configuration-owned", "repository": "git@github.com:krafczyk/compound-engineering-plugin.git", "commit": "f07e820a4146963de4e3ae1197c82f6686507e6c"},
                {"name": "opencode_sprint_loop", "disposition": "disabled", "repository": "git@github.com:krafczyk/opencode_sprint_loop", "commit": "5e71e56e6fc471c7021a7e34a58485cb38c2ab49"},
            ],
            "artifacts": {"nvim-image": [{"architecture": "x86_64", "sha256": "a" * 64, "source_filename_class": "raw-nvim-container"}]},
            "configuration": {"contract_version": 1, "guidance": "docs/deployment/README.md"},
        }

    def result(self, outcome="succeeded"):
        release = self.release()
        result = {
            "schema": 1,
            "run_id": "123e4567-e89b-12d3-a456-426614174000",
            "release": "v2.0.0",
            "started_at": "2026-08-02T12:00:00Z",
            "completed_at": "2026-08-02T12:01:00Z",
            "outcome": outcome,
            "phase": "verify" if outcome == "succeeded" else "apply",
            "components": [
                {"name": component["name"], "disposition": component["disposition"], "requested_commit": component["commit"], "observed_commit": component["commit"] if component["disposition"] == "deployed" else None}
                for component in release["components"]
            ],
            "artifact": {"architecture": "x86_64", "expected_sha256": "a" * 64, "observed_sha256": "a" * 64},
            "actions": [
                {"component": "msk_containers", "id": action["id"], "phase": action["phase"], "status": "succeeded"}
                for action in release["components"][0]["actions"]
            ],
            "configuration_handoff": {
                "state": "configuration-pending",
                "contract_version": 1,
                "guidance": "docs/deployment/README.md",
                "components": [
                    {"name": component["name"], "repository": component["repository"], "commit": component["commit"]}
                    for component in release["components"] if component["disposition"] == "configuration-owned"
                ],
            },
            "failed_step": None,
            "diagnostic": "deployment completed",
            "log_path": "/tmp/mkchad-v1/deploy/logs/run.log",
        }
        if outcome == "failed":
            result["actions"][3]["status"] = "failed"
            result["failed_step"] = "msk_containers:image-install"
            result["diagnostic"] = "image installer failed"
        elif outcome == "interrupted":
            result["actions"][3]["status"] = "pending"
            result["failed_step"] = "deployment interrupted"
            result["diagnostic"] = "deployment interrupted"
        return result

    def test_schema_2_fixture_is_exact_and_gitlink_independent(self):
        fixture = json.loads((ROOT / "tests" / "fixtures" / "deployment" / "release-v2.json").read_text(encoding="utf-8"))
        self.assertEqual(self.deploy.validate_release_v2_document(fixture), fixture)
        disagreement = copy.deepcopy(fixture)
        disagreement["components"][0]["commit"] = "f" * 40
        self.assertEqual(self.deploy.validate_release_v2_document(disagreement), disagreement)

    def test_schema_2_rejects_component_fields_and_identity_errors(self):
        for mutate, message in [
            (lambda value: value.pop("configuration"), "unknown or missing"),
            (lambda value: value.__setitem__("unexpected", True), "unknown or missing"),
            (lambda value: value["components"].append(copy.deepcopy(value["components"][0])), "component"),
            (lambda value: value["components"].pop(1), "component"),
            (lambda value: value["components"][0].__setitem__("repository", "ssh://example.invalid/repo"), "repository"),
            (lambda value: value["components"][0].__setitem__("commit", "A" * 40), "commit"),
            (lambda value: value["components"][0].__setitem__("disposition", "installed"), "disposition"),
            (lambda value: value["components"][0].__setitem__("unexpected", True), "component fields"),
            (lambda value: value["components"][0].pop("repository"), "repository"),
            (lambda value: value["components"].append({"name": "config_files", "disposition": "disabled", "repository": "git@github.com:krafczyk/config_files", "commit": "0" * 40}), "component"),
        ]:
            release = self.release()
            mutate(release)
            with self.subTest(message=message), self.assertRaisesRegex(self.deploy.DeploymentError, message):
                self.deploy.validate_release_v2_document(release)

    def test_schema_2_rejects_deployment_fields_actions_artifacts_and_configuration_errors(self):
        cases = [
            (lambda value: value["components"][0]["destination"].__setitem__("path", "../outside"), "destination"),
            (lambda value: value["components"][0]["destination"].__setitem__("root", "temporary"), "destination"),
            (lambda value: value["components"][2]["preserved_paths"].__setitem__(0, "nested/../path"), "preserved"),
            (lambda value: value["components"][2].__setitem__("preserved_paths", ["other.json"]), "preserved"),
            (lambda value: value["components"][3].__setitem__("actions", []), "non-deployed"),
            (lambda value: value["components"][0]["actions"].append(copy.deepcopy(value["components"][0]["actions"][0])), "action id"),
            (lambda value: value["components"][0]["actions"][0].__setitem__("phase", "install"), "action phase"),
            (lambda value: value["components"][0]["actions"][0].__setitem__("path", "bin/../install_nvim.sh"), "action path"),
            (lambda value: value["components"][0]["actions"][0]["args"].__setitem__(2, {"token": "shell_command"}), "action token"),
            (lambda value: value["artifacts"]["nvim-image"][0].__setitem__("architecture", "sparc"), "architecture"),
            (lambda value: value["artifacts"]["nvim-image"][0].__setitem__("sha256", "A" * 64), "digest"),
            (lambda value: value["artifacts"]["nvim-image"][0].__setitem__("source_filename_class", "derived-container"), "source filename"),
            (lambda value: value["artifacts"]["nvim-image"][0].pop("sha256"), "artifact"),
            (lambda value: value["artifacts"]["nvim-image"].append(copy.deepcopy(value["artifacts"]["nvim-image"][0])), "architecture"),
            (lambda value: value["components"][2]["destination"].__setitem__("root", "xdg_data_home") or value["components"][2]["destination"].__setitem__("path", "mkchad/components/msk_containers"), "destinations"),
            (lambda value: value["configuration"].__setitem__("guidance", "../README.md"), "guidance"),
        ]
        for mutate, message in cases:
            release = self.release()
            mutate(release)
            with self.subTest(message=message), self.assertRaisesRegex(self.deploy.DeploymentError, message):
                self.deploy.validate_release_v2_document(release)

    def test_schema_1_terminal_results_accept_each_outcome_and_reject_malformed_data(self):
        for outcome in ("succeeded", "failed", "interrupted"):
            result = self.result(outcome)
            self.assertEqual(self.deploy.validate_result_v1_document(result), result)

        for mutate, message in [
            (lambda value: value.__setitem__("schema", 2), "schema"),
            (lambda value: value.__setitem__("run_id", "not-a-uuid"), "run id"),
            (lambda value: value.__setitem__("environment", {}), "unknown or missing"),
            (lambda value: value["components"].pop(), "components"),
            (lambda value: value["components"][0].__setitem__("requested_commit", "bad"), "commit"),
            (lambda value: value["artifact"].__setitem__("expected_sha256", "bad"), "digest"),
            (lambda value: value["actions"][0].__setitem__("status", "running"), "status"),
            (lambda value: value["actions"][0].__setitem__("args", []), "action"),
            (lambda value: value["configuration_handoff"].__setitem__("state", "installed"), "configuration"),
            (lambda value: value["configuration_handoff"]["components"].pop(), "configuration components"),
            (lambda value: value.__setitem__("log_path", "relative.log"), "log path"),
        ]:
            result = self.result()
            mutate(result)
            with self.subTest(message=message), self.assertRaisesRegex(self.deploy.DeploymentError, message):
                self.deploy.validate_result_v1_document(result)


class DeploymentExecutionTests(DeploymentCheckoutTests):
    """Exercise schema-2 actions, image installation, and terminal results."""

    def _sources(self):
        commits = super()._sources()
        msk = self.root / "sources" / "msk_containers"
        (msk / "bin" / "install_nvim.sh").write_text(
            "#!/bin/sh\n"
            "set -eu\n"
            "printf 'host-tools %s\\n' \"$*\"\n"
            "[ \"${1:-}\" != --check ] || exit \"${HOST_PREFLIGHT_STATUS:-0}\"\n"
            "[ \"${HOST_TOOLS_SLEEP:-0}\" = 0 ] || sleep \"$HOST_TOOLS_SLEEP\"\n"
            "mkdir -p \"$HOME/.local/bin\"\n"
            "cp container_tools/*.sh \"$HOME/.local/bin/\"\n"
            "cp nvim/bin/nvim \"$HOME/.local/bin/nvim\"\n"
            "cp nvim/bin/install_nvim_container \"$HOME/.local/bin/install_nvim_container\"\n"
            "chmod 755 \"$HOME/.local/bin\"/*\n",
            encoding="ascii",
        )
        (msk / "nvim" / "bin" / "nvim").write_text("#!/bin/sh\nexit 0\n", encoding="ascii")
        (msk / "nvim" / "bin" / "nvim").chmod(0o755)
        (msk / "nvim" / "bin" / "install_nvim_container").write_text(
            "#!/bin/sh\n"
            "set -eu\n"
            "printf 'image-install %s\\n' \"$*\"\n"
            "[ \"${1:-}\" = --dry-run ] && exit 0\n"
            "[ \"${IMAGE_INSTALL_STATUS:-0}\" = 0 ] || exit \"$IMAGE_INSTALL_STATUS\"\n"
            "mkdir -p \"$HOME/containers\"\n"
            "for source; do :; done\n"
            "if [ \"${IMAGE_INSTALL_WRONG_DIGEST:-0}\" = 1 ]; then printf bad > \"$HOME/containers/neovim_fixture.sif\"; else cp \"$source\" \"$HOME/containers/neovim_fixture.sif\"; fi\n"
            "ln -sfn neovim_fixture.sif \"$HOME/containers/neovim.sif\"\n",
            encoding="ascii",
        )
        commits["msk_containers"] = self._commit(msk, "install fixture outputs")
        return commits

    def _execute(self, *, manifest=None, image=None, run_id="123e4567-e89b-12d3-a456-426614174000"):
        manifest, image = (manifest, image) if manifest else self._manifest()
        self.result_path = self.root / "result.json"
        return self.deploy.execute_v2_deployment(
            self.root,
            image,
            release="v2-fixture",
            log_path=self.root / "run.log",
            result_path=self.result_path,
            run_id=run_id,
            manifest_path=manifest,
            roots=self.roots,
            staging_root=self.root,
        )

    def _result(self):
        return json.loads(self.result_path.read_text(encoding="utf-8"))

    def test_actions_install_and_verify_the_pinned_outputs_and_publish_requested_state(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(self._execute(), 0)
        result = self._result()
        self.assertEqual(result["outcome"], "succeeded")
        self.assertEqual(result["phase"], "verify")
        self.assertEqual(result["artifact"]["observed_sha256"], result["artifact"]["expected_sha256"])
        self.assertEqual([action["status"] for action in result["actions"]], ["succeeded"] * 5)
        self.assertEqual({component["name"] for component in result["configuration_handoff"]["components"]}, self.deploy.V2_CONFIGURATION_COMPONENTS)
        self.assertIn("host-tools --check", output.getvalue())
        self.assertFalse(list(self.state.rglob("*journal*")))
        self.assertFalse(list(self.state.rglob("*recovery*")))

    def test_first_apply_failure_stops_later_actions_and_reports_a_redacted_terminal_result(self):
        os.environ["IMAGE_INSTALL_STATUS"] = "7"
        os.environ["API_TOKEN"] = "DO-NOT-PUBLISH"
        self.assertEqual(self._execute(), 2)
        result = self._result()
        self.assertEqual(result["outcome"], "failed")
        self.assertEqual(result["failed_step"], "msk_containers:image-install")
        self.assertEqual([action["status"] for action in result["actions"]], ["succeeded", "succeeded", "succeeded", "failed", "pending"])
        encoded = json.dumps(result)
        self.assertNotIn("DO-NOT-PUBLISH", encoded)
        self.assertNotIn("args", encoded)
        self.assertNotIn("environment", encoded)

    def test_preflight_action_failure_names_the_step_before_checkout_mutation(self):
        os.environ["HOST_PREFLIGHT_STATUS"] = "7"

        self.assertEqual(self._execute(), 2)

        result = self._result()
        self.assertEqual(result["phase"], "preflight")
        self.assertEqual(result["failed_step"], "msk_containers:host-tools-preflight")
        self.assertEqual([action["status"] for action in result["actions"]], ["failed", "pending", "pending", "pending", "pending"])
        self.assertFalse(self._destination("msk_containers").exists())

    def test_action_timeout_terminates_the_live_group_and_records_the_failed_step(self):
        os.environ["HOST_TOOLS_SLEEP"] = "10"
        original = self.deploy._v2_run_action

        def short_timeout(argv, cwd):
            return original(argv, cwd, timeout=0.05)

        with mock.patch.object(self.deploy, "_v2_run_action", side_effect=short_timeout):
            self.assertEqual(self._execute(), 2)
        result = self._result()
        self.assertEqual(result["failed_step"], "msk_containers:host-tools-install")
        self.assertEqual([action["status"] for action in result["actions"]], ["succeeded", "succeeded", "failed", "pending", "pending"])
        self.assertIn("timed out", result["diagnostic"])

    def test_wrong_installed_image_digest_fails_terminal_verification(self):
        os.environ["IMAGE_INSTALL_WRONG_DIGEST"] = "1"
        self.assertEqual(self._execute(), 2)
        result = self._result()
        self.assertEqual(result["outcome"], "failed")
        self.assertEqual(result["phase"], "verify")
        self.assertEqual(result["failed_step"], "installed-image-verification")
        self.assertNotEqual(result["artifact"]["observed_sha256"], result["artifact"]["expected_sha256"])

    def test_internal_cli_accepts_only_the_launcher_handoff_arguments(self):
        log_path = self.root / "run.log"
        result_path = self.root / "result.json"
        with mock.patch.object(self.deploy, "execute_v2_deployment", return_value=0) as execute:
            self.assertEqual(
                self.deploy.main(
                    [
                        "deploy", "--release", "v2-fixture", "--image", str(self.root / "image.sif"),
                        "--log-path", str(log_path), "--result-path", str(result_path),
                        "--run-id", "123e4567-e89b-12d3-a456-426614174000",
                    ]
                ),
                0,
            )
        self.assertEqual(execute.call_args.kwargs["release"], "v2-fixture")
        self.assertEqual(execute.call_args.kwargs["log_path"], log_path)
        self.assertEqual(execute.call_args.kwargs["result_path"], result_path)

    def test_uncaught_termination_leaves_the_previous_terminal_result(self):
        self.assertEqual(self._execute(), 0)
        prior = self.result_path.read_text(encoding="utf-8")
        with mock.patch.object(self.deploy, "_v2_run_action", side_effect=SystemExit):
            with self.assertRaises(SystemExit):
                self._execute(run_id="423e4567-e89b-12d3-a456-426614174000")
        self.assertEqual(self.result_path.read_text(encoding="utf-8"), prior)

    def test_active_compliant_run_verifies_without_apply_and_caught_interruption_publishes_result(self):
        self.assertEqual(self._execute(), 0)
        self._lifecycle()
        launcher = self.home / ".local" / "bin" / "mkchad-opencode-server"
        launcher.write_text("#!/bin/sh\nprintf '%s\\n' '{\"schema\": 1, \"ok\": true, \"command\": \"status\", \"status\": \"healthy\"}'\n", encoding="ascii")
        launcher.chmod(0o755)
        self.assertEqual(self._execute(run_id="223e4567-e89b-12d3-a456-426614174000"), 0)
        result = self._result()
        self.assertEqual([action["status"] for action in result["actions"]], ["succeeded", "succeeded", "skipped-compliant", "skipped-compliant", "succeeded"])
        with mock.patch.object(self.deploy, "_v2_run_action", side_effect=KeyboardInterrupt):
            self.assertEqual(self._execute(run_id="323e4567-e89b-12d3-a456-426614174000"), 2)
        self.assertEqual(self._result()["outcome"], "interrupted")


if __name__ == "__main__":
    unittest.main()
