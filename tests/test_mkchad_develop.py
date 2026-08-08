"""Focused workspace-admission and local-snapshot tests for mkchad-develop."""

import contextlib
import fcntl
import importlib.util
import io
import hashlib
import json
import os
import pathlib
import shutil
import subprocess
import tempfile
import tarfile
import unittest
from unittest import mock
import sys


ROOT = pathlib.Path(__file__).resolve().parents[1]


def load_deployer():
    """Load the deployment engine directly from the parent checkout."""
    spec = importlib.util.spec_from_file_location("mkchad_deploy", ROOT / "deploy/mkchad_deploy.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def load_developer_command():
    """Load the repository-local command with its sibling deployment modules."""
    deploy_directory = str(ROOT / "deploy")
    sys.path.insert(0, deploy_directory)
    try:
        spec = importlib.util.spec_from_file_location("mkchad_develop", ROOT / "deploy/mkchad_develop.py")
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path.remove(deploy_directory)


class DeveloperWorkspaceTests(unittest.TestCase):
    """Prove developer snapshots admit only clean local committed child bytes."""

    def setUp(self):
        self.deploy = load_deployer()
        self.root = pathlib.Path(tempfile.mkdtemp(prefix="mkchad-develop-", dir="/tmp/mkchad-v1"))
        self.addCleanup(shutil.rmtree, self.root, True)
        self.workspace = self.root / "workspace"
        self._init(self.workspace, {"README": "parent\n"})
        tools = self.root / "tools"
        self._init(tools, {"tool.sh": "#!/bin/sh\nexit 0\n", "scripts/install-package.sh": "#!/bin/sh\nexit 0\n"})
        (tools / "scripts/install-package.sh").chmod(0o755)
        msk = self.root / "msk"
        self._init(msk, {"bin/install_nvim.sh": "#!/bin/sh\nexit 0\n", "nvim/bin/install_nvim_container": "#!/bin/sh\nexit 0\n"})
        (msk / "nvim/bin/install_nvim_container").chmod(0o755)
        self._commit(msk, "make image installer executable")
        self._run(["git", "-C", str(msk), "-c", "protocol.file.allow=always", "submodule", "add", "-q", str(tools), "container_tools"])
        self._commit(msk, "add tools")
        mkchad = self.root / "config"
        self._init(mkchad, {"init.lua": "return {}\n", ".gitignore": "opencode-server.json\nlazy-lock.json\n"})
        database = self.root / "database"
        self._init(database, {
            "bin/install_opencode_db.sh": "#!/bin/sh\nexit 0\n",
            "bin/opencode-db": "#!/bin/sh\n[ \"${1:-}\" = --help ] && exit 0\nexit 0\n",
        })
        (database / "bin/opencode-db").chmod(0o755)
        self._commit(database, "make database launcher executable")
        self._run(["git", "-C", str(self.workspace), "-c", "protocol.file.allow=always", "submodule", "add", "-q", str(msk), "msk_containers"])
        self._run(["git", "-C", str(self.workspace), "-c", "protocol.file.allow=always", "submodule", "add", "-q", str(mkchad), "mkchad"])
        self._run(["git", "-C", str(self.workspace), "-c", "protocol.file.allow=always", "submodule", "add", "-q", str(database), "opencode-db"])
        self._commit(self.workspace, "add children")
        self._run(["git", "-C", str(self.workspace), "-c", "protocol.file.allow=always", "submodule", "update", "--init", "--recursive"])
        self.msk = self.workspace / "msk_containers"
        self.tools = self.msk / "container_tools"
        self.mkchad = self.workspace / "mkchad"
        self.database = self.workspace / "opencode-db"
        self.package = self.root / "container-tools-host.tar.gz"
        archive_metadata = {"schema": "container-tools.archive/v1", "architecture": self.deploy._native_architecture(), "version": "1.0.0-dev", "source_commit": self._commit_value(self.tools), "libc": "glibc"}
        payload = json.dumps(archive_metadata, sort_keys=True, separators=(",", ":")).encode("ascii")
        package_root = f"container-tools-{archive_metadata['version']}-{archive_metadata['architecture']}-{archive_metadata['libc']}-{archive_metadata['source_commit']}"
        with tarfile.open(self.package, "w:gz", format=tarfile.PAX_FORMAT) as archive:
            directory = tarfile.TarInfo(package_root)
            directory.type = tarfile.DIRTYPE
            directory.mode = 0o755
            directory.mtime = 0
            archive.addfile(directory)
            member = tarfile.TarInfo(f"{package_root}/archive.json")
            member.size = len(payload)
            member.mode = 0o644
            member.mtime = 0
            archive.addfile(member, io.BytesIO(payload))

    def _run(self, command):
        return subprocess.run(command, check=True, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    def _init(self, path, files):
        path.mkdir()
        for name, contents in files.items():
            target = path / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(contents, encoding="ascii")
            if name.endswith(".sh"):
                target.chmod(0o755)
        self._run(["git", "init", "-q", str(path)])
        self._commit(path, "initial")

    def _commit(self, path, message):
        self._run(["git", "-C", str(path), "add", "."])
        self._run(["git", "-C", str(path), "-c", "user.name=test", "-c", "user.email=test@example.invalid", "commit", "-q", "-m", message])

    def _commit_value(self, path):
        return self._run(["git", "-C", str(path), "rev-parse", "HEAD"]).stdout.strip()

    def _package_kwargs(self):
        """Return the admitted archive identity for direct deployer tests."""
        return {"host_package": self.package, "host_package_identity": {"role": "host", "sha256": hashlib.sha256(self.package.read_bytes()).hexdigest(), "version": "1.0.0-dev", "source_commit": self._commit_value(self.tools), "architecture": {"amd64": "x86_64", "arm64": "aarch64"}.get(os.uname().machine.lower(), os.uname().machine.lower()), "libc": "glibc"}}

    def _write_image(self, path):
        """Write synthetic in-image container package metadata for one test SIF."""
        identity = dict(self._package_kwargs()["host_package_identity"])
        identity.update(role="container", product_major=1, source_filename_class="container-tools-package")
        path.write_text(json.dumps(identity, sort_keys=True, separators=(",", ":")), encoding="ascii")

    def _command_package_args(self):
        """Return the public CLI representation of the synthetic archive identity."""
        identity = self._package_kwargs()["host_package_identity"]
        return ["--container-tools-host-package", str(self.package), "--container-tools-host-sha256", identity["sha256"], "--container-tools-host-version", identity["version"], "--container-tools-host-source-commit", identity["source_commit"], "--container-tools-host-architecture", identity["architecture"], "--container-tools-host-libc", identity["libc"]]

    def test_clean_unpublished_heads_are_staged_from_local_objects_with_dirty_parent_allowed(self):
        (self.workspace / "parent-untracked").write_text("allowed\n", encoding="ascii")
        (self.tools / "unpublished").write_text("snapshot\n", encoding="ascii")
        self._commit(self.tools, "unpublished tools")
        (self.mkchad / "init.lua").write_text("return { local_commit = true }\n", encoding="ascii")
        self._commit(self.mkchad, "unpublished config")
        for child in (self.msk, self.tools, self.mkchad, self.database):
            self._run(["git", "-C", str(child), "remote", "remove", "origin"])

        admission = self.deploy.admit_developer_workspace(self.workspace)
        self.assertTrue(admission["parent"]["dirty"])
        self.assertEqual(admission["commits"]["container_tools"], self._commit_value(self.tools))
        self.assertEqual(admission["commits"]["mkchad"], self._commit_value(self.mkchad))
        self.assertEqual(admission["commits"]["opencode-db"], self._commit_value(self.database))
        staging = pathlib.Path(tempfile.mkdtemp(prefix="mkchad-develop-stage-", dir="/tmp/mkchad-v1"))
        self.addCleanup(shutil.rmtree, staging, True)
        staged = self.deploy.stage_developer_sources(admission, staging)
        self.assertEqual(self._commit_value(staged["container_tools"][1]), admission["commits"]["container_tools"])
        self.assertEqual((staged["msk_containers"][1] / "container_tools" / "unpublished").read_text(encoding="ascii"), "snapshot\n")
        self.assertEqual(self._commit_value(staged["opencode-db"][1]), admission["commits"]["opencode-db"])

    def test_dirty_children_and_workspace_layout_fail_before_staging(self):
        for child, relative in ((self.msk, "untracked"), (self.tools, "untracked"), (self.mkchad, "init.lua"), (self.database, "untracked")):
            with self.subTest(child=child):
                target = child / relative
                target.write_text("dirty\n", encoding="ascii")
                with self.assertRaisesRegex(self.deploy.DeploymentError, "local work"):
                    self.deploy.admit_developer_workspace(self.workspace)
                target.unlink()
        with self.assertRaises(self.deploy.DeploymentError):
            self.deploy.admit_developer_workspace(self.workspace / "mkchad")
        shutil.rmtree(self.mkchad)
        with self.assertRaisesRegex(self.deploy.DeploymentError, "expected child"):
            self.deploy.admit_developer_workspace(self.workspace)

    def test_staged_and_type_changing_child_work_is_rejected(self):
        (self.tools / "staged").write_text("staged\n", encoding="ascii")
        self._run(["git", "-C", str(self.tools), "add", "staged"])
        with self.assertRaisesRegex(self.deploy.DeploymentError, "local work"):
            self.deploy.admit_developer_workspace(self.workspace)

    def test_type_changing_child_work_is_rejected(self):
        action = self.msk / "bin" / "install_nvim.sh"
        action.unlink()
        action.mkdir()
        with self.assertRaisesRegex(self.deploy.DeploymentError, "local work"):
            self.deploy.admit_developer_workspace(self.workspace)

    def test_unmerged_child_work_is_rejected(self):
        blobs = [
            subprocess.check_output(["git", "-C", str(self.tools), "hash-object", "-w", "--stdin"], input=value, text=False).decode().strip()
            for value in (b"base\n", b"ours\n", b"theirs\n")
        ]
        index = "".join(f"100644 {blob} {stage}\ttool.sh\n" for stage, blob in enumerate(blobs, start=1))
        subprocess.run(["git", "-C", str(self.tools), "update-index", "--index-info"], input=index, text=True, check=True)
        with self.assertRaisesRegex(self.deploy.DeploymentError, "local work"):
            self.deploy.admit_developer_workspace(self.workspace)

    def test_unmerged_nested_gitlink_is_not_treated_as_an_independent_head(self):
        for code in ("AA", "DD"):
            with self.subTest(code=code), mock.patch.object(
                self.deploy,
                "_workspace_status",
                side_effect=lambda path, code=code: [(code, "container_tools")] if path == self.msk else [],
            ):
                with self.assertRaisesRegex(self.deploy.DeploymentError, "local work"):
                    self.deploy.admit_developer_workspace(self.workspace)

    def test_source_changes_after_snapshot_recheck_are_rejected(self):
        admission = self.deploy.admit_developer_workspace(self.workspace)
        staging = pathlib.Path(tempfile.mkdtemp(prefix="mkchad-develop-stage-", dir="/tmp/mkchad-v1"))
        self.addCleanup(shutil.rmtree, staging, True)
        self.deploy.stage_developer_sources(admission, staging)
        (self.mkchad / "init.lua").write_text("changed\n", encoding="ascii")
        with self.assertRaisesRegex(self.deploy.DeploymentError, "source changed while staging"):
            self.deploy.recheck_developer_workspace(admission)

    def test_clean_head_advancement_after_snapshot_recheck_is_rejected(self):
        admission = self.deploy.admit_developer_workspace(self.workspace)
        staging = pathlib.Path(tempfile.mkdtemp(prefix="mkchad-develop-stage-", dir="/tmp/mkchad-v1"))
        self.addCleanup(shutil.rmtree, staging, True)
        self.deploy.stage_developer_sources(admission, staging)
        (self.database / "bin/opencode-db").write_text("#!/bin/sh\nexit 1\n", encoding="ascii")
        self._commit(self.database, "advance clean database head")

        with self.assertRaisesRegex(self.deploy.DeploymentError, "source changed while staging"):
            self.deploy.recheck_developer_workspace(admission)

    def test_developer_result_is_mode_tagged_and_contains_workspace_and_component_evidence(self):
        admission = self.deploy.admit_developer_workspace(self.workspace)
        result = self.deploy.developer_result(
            admission,
            actions=[],
            artifact={"architecture": "x86_64", "sha256": "a" * 64},
            image={"choice": "reused", "supplied_sha256": "a" * 64, "active_sha256": "a" * 64, "decision": "reused-active"},
            packages={"host": {**self._package_kwargs()["host_package_identity"], "product_major": 1, "source_filename_class": "container-tools-package"}, "container": {**self._package_kwargs()["host_package_identity"], "role": "container", "product_major": 1, "source_filename_class": "container-tools-package"}},
            package_observations={"host": None, "container": None},
            run_id="123e4567-e89b-12d3-a456-426614174000",
            started_at="2026-08-03T00:00:00Z",
            log_path=pathlib.Path("/tmp/mkchad-v1/develop.log"),
            outcome="succeeded",
            phase="verify",
            failed_step=None,
            diagnostic="deployment complete",
        )
        self.assertEqual(result["mode"], "developer")
        self.assertEqual(result["workspace"], admission["parent"])
        self.assertEqual({item["name"]: item["requested_commit"] for item in result["components"]}, admission["commits"])
        self.assertEqual(result["artifact"]["requested_sha256"], "a" * 64)
        self.assertEqual(result["image"]["choice"], "reused")

    def test_local_snapshots_reach_the_regular_checkout_procedure(self):
        image = self.root / "image.sif"
        self._write_image(image)
        (self.mkchad / "init.lua").write_text("return { deployed = true }\n", encoding="ascii")
        self._commit(self.mkchad, "local deployment")
        for child in (self.msk, self.tools, self.mkchad, self.database):
            self._run(["git", "-C", str(child), "remote", "remove", "origin"])
        home = self.root / "home"
        environment = {
            "HOME": str(home), "XDG_CONFIG_HOME": str(self.root / "config"), "XDG_DATA_HOME": str(self.root / "data"),
            "XDG_STATE_HOME": str(self.root / "state"), "NVIM_CONTAINER_DIR": str(home / "containers"),
        }
        result_path = self.root / "developer-result.json"
        with mock.patch.dict(os.environ, environment, clear=False), mock.patch.object(self.deploy, "_runtime_image"), mock.patch.object(self.deploy, "_runtime_image_package_identity", return_value={}), mock.patch.object(self.deploy, "_run_action"), mock.patch.object(self.deploy, "_host_tool_mismatches", return_value={"msk_containers": None, "opencode-db": None}), mock.patch.object(self.deploy, "_active_image_digest", return_value=hashlib.sha256(image.read_bytes()).hexdigest()):
            self.assertEqual(self.deploy.execute_developer_deployment(self.workspace, image, log_path=self.root / "developer.log", result_path=result_path, run_id="123e4567-e89b-12d3-a456-426614174000", **self._package_kwargs()), 0)
        result = json.loads(result_path.read_text(encoding="utf-8"))
        self.assertEqual(result["mode"], "developer")
        self.assertEqual(result["outcome"], "succeeded")
        self.assertEqual(result["diagnostic"], "developer deployment verified")
        destination = self.root / "config" / "mkchad"
        self.assertEqual(self._commit_value(destination), self._commit_value(self.mkchad))
        self.assertEqual(self._commit_value(self.root / "data" / "mkchad" / "components" / "opencode-db"), self._commit_value(self.database))
        self.assertTrue(image.is_file())

    def test_omitted_image_preserves_active_state_and_skips_image_actions(self):
        home = self.root / "home"
        container_dir = home / "containers"
        container_dir.mkdir(parents=True)
        (container_dir / "neovim.sif").symlink_to("missing.sif")
        environment = {
            "HOME": str(home), "XDG_CONFIG_HOME": str(self.root / "config"), "XDG_DATA_HOME": str(self.root / "data"),
            "XDG_STATE_HOME": str(self.root / "state"), "NVIM_CONTAINER_DIR": str(container_dir),
        }
        result_path = self.root / "developer-result.json"
        with mock.patch.dict(os.environ, environment, clear=False), mock.patch.object(self.deploy, "_stage_image", side_effect=AssertionError("image staging")), mock.patch.object(
            self.deploy, "_runtime_image", side_effect=AssertionError("runtime validation")
        ), mock.patch.object(self.deploy, "_runtime_image_package_identity", return_value={}), mock.patch.object(self.deploy, "_active_image_digest", side_effect=AssertionError("image verification")), mock.patch.object(
            self.deploy, "_run_action", wraps=self.deploy._run_action
        ) as action, mock.patch.object(self.deploy, "_host_tool_mismatches", return_value={"msk_containers": None, "opencode-db": None}):
            self.assertEqual(self.deploy.execute_developer_deployment(self.workspace, None, log_path=self.root / "developer.log", result_path=result_path, run_id="123e4567-e89b-12d3-a456-426614174000", replace_partial=True, **self._package_kwargs()), 0)

        result = json.loads(result_path.read_text(encoding="utf-8"))
        self.assertEqual(result["image"], {"choice": "omitted", "supplied_sha256": None, "active_sha256": None, "decision": "preserved"})
        self.assertEqual([item["id"] for item in result["actions"]], ["host-tools-preflight", "host-tools-install", "host-tools-verify", "opencode-db-preflight", "opencode-db-install", "opencode-db-verify"])
        self.assertFalse(any(call.args[0]["id"].startswith("image-") for call in action.call_args_list))
        self.assertTrue((container_dir / "neovim.sif").is_symlink())

    def test_omitted_image_does_not_bypass_missing_lifecycle_partial_target_refusal(self):
        home = self.root / "home"
        container_dir = home / "containers"
        container_dir.mkdir(parents=True)
        (container_dir / "neovim.sif").symlink_to("missing.sif")
        environment = {
            "HOME": str(home), "XDG_CONFIG_HOME": str(self.root / "config"), "XDG_DATA_HOME": str(self.root / "data"),
            "XDG_STATE_HOME": str(self.root / "state"), "NVIM_CONTAINER_DIR": str(container_dir),
        }
        result_path = self.root / "developer-result.json"

        with mock.patch.dict(os.environ, environment, clear=False):
            self.assertEqual(self.deploy.execute_developer_deployment(self.workspace, None, log_path=self.root / "developer.log", result_path=result_path, run_id="123e4567-e89b-12d3-a456-426614174000", **self._package_kwargs()), 2)

        self.assertIn("partial target has no lifecycle command", json.loads(result_path.read_text(encoding="utf-8"))["diagnostic"])
        self.assertTrue((container_dir / "neovim.sif").is_symlink())

    def test_matching_developer_image_skips_image_work_and_preserves_caller(self):
        home = self.root / "home"
        container_dir = home / "containers"
        container_dir.mkdir(parents=True)
        image_bytes = json.dumps({**self._package_kwargs()["host_package_identity"], "role": "container", "product_major": 1, "source_filename_class": "container-tools-package"}, sort_keys=True, separators=(",", ":")).encode("ascii")
        digest = hashlib.sha256(image_bytes).hexdigest()
        architecture = {"amd64": "x86_64", "arm64": "aarch64"}.get(os.uname().machine.lower(), os.uname().machine.lower())
        image = container_dir / f"neovim_1.2.3_gabcdef0_{digest}_{architecture}.sif"
        image.write_bytes(image_bytes)
        (container_dir / "neovim.sif").symlink_to(image.name)
        environment = {
            "HOME": str(home), "XDG_CONFIG_HOME": str(self.root / "config"), "XDG_DATA_HOME": str(self.root / "data"),
            "XDG_STATE_HOME": str(self.root / "state"), "NVIM_CONTAINER_DIR": str(container_dir),
        }
        topology = sorted(path.name for path in container_dir.iterdir())
        with mock.patch.dict(os.environ, environment, clear=False), mock.patch.object(self.deploy, "_stage_image", side_effect=AssertionError("image staging")), mock.patch.object(
            self.deploy, "_runtime_image", side_effect=AssertionError("runtime validation")
        ), mock.patch.object(self.deploy, "_runtime_image_package_identity", return_value={}), mock.patch.object(self.deploy, "_active_image_digest", side_effect=AssertionError("image verification")), mock.patch.object(
            self.deploy, "_run_action", wraps=self.deploy._run_action
        ) as action, mock.patch.object(self.deploy, "_host_tool_mismatches", return_value={"msk_containers": None, "opencode-db": None}):
            self.assertEqual(self.deploy.execute_developer_deployment(self.workspace, image, log_path=self.root / "developer.log", result_path=self.root / "developer-result.json", run_id="123e4567-e89b-12d3-a456-426614174000", replace_partial=True, **self._package_kwargs()), 0)

        self.assertTrue(image.is_file())
        self.assertEqual(sorted(path.name for path in container_dir.iterdir()), topology)
        self.assertFalse(any(call.args[0]["id"].startswith("image-") for call in action.call_args_list))

    def test_install_rejects_a_developer_caller_the_installer_can_prune(self):
        home = self.root / "home"
        container_dir = home / "containers"
        container_dir.mkdir(parents=True)
        image_bytes = json.dumps({**self._package_kwargs()["host_package_identity"], "role": "container", "product_major": 1, "source_filename_class": "container-tools-package"}, sort_keys=True, separators=(",", ":")).encode("ascii")
        digest = hashlib.sha256(image_bytes).hexdigest()
        architecture = {"amd64": "x86_64", "arm64": "aarch64"}.get(os.uname().machine.lower(), os.uname().machine.lower())
        image = container_dir / f"neovim_1.2.3_gabcdef0_{digest}_{architecture}.sif"
        image.write_bytes(image_bytes)
        environment = {
            "HOME": str(home), "XDG_CONFIG_HOME": str(self.root / "config"), "XDG_DATA_HOME": str(self.root / "data"),
            "XDG_STATE_HOME": str(self.root / "state"), "NVIM_CONTAINER_DIR": str(container_dir),
        }
        result_path = self.root / "developer-result.json"

        with mock.patch.dict(os.environ, environment, clear=False), mock.patch.object(self.deploy, "_run_action") as action:
            self.assertEqual(self.deploy.execute_developer_deployment(self.workspace, image, log_path=self.root / "developer.log", result_path=result_path, run_id="123e4567-e89b-12d3-a456-426614174000", **self._package_kwargs()), 2)

        self.assertTrue(image.is_file())
        action.assert_not_called()
        self.assertIn("could prune the developer caller image", json.loads(result_path.read_text(encoding="utf-8"))["diagnostic"])

    def test_supplied_image_rejects_an_unsafe_active_selector_before_target_mutation(self):
        image = self.root / "image.sif"
        self._write_image(image)
        home = self.root / "home"
        container_dir = home / "containers"
        container_dir.mkdir(parents=True)
        (container_dir / "neovim.sif").symlink_to("../escaping.sif")
        environment = {
            "HOME": str(home), "XDG_CONFIG_HOME": str(self.root / "config"), "XDG_DATA_HOME": str(self.root / "data"),
            "XDG_STATE_HOME": str(self.root / "state"), "NVIM_CONTAINER_DIR": str(container_dir),
        }
        result_path = self.root / "developer-result.json"

        with mock.patch.dict(os.environ, environment, clear=False), mock.patch.object(self.deploy, "_stage_image", side_effect=AssertionError("image staging")):
            self.assertEqual(self.deploy.execute_developer_deployment(self.workspace, image, log_path=self.root / "developer.log", result_path=result_path, run_id="123e4567-e89b-12d3-a456-426614174000", **self._package_kwargs()), 2)

        self.assertFalse((self.root / "config" / "mkchad").exists())
        self.assertTrue(image.is_file())
        self.assertEqual(json.loads(result_path.read_text(encoding="utf-8"))["image"]["decision"], "blocked-unsafe-active")

    def test_public_command_shares_the_release_invocation_lock(self):
        command = load_developer_command()
        state = self.root / "state"
        image = self.root / "image.sif"
        image.write_bytes(b"image")
        lock = state / "mkchad" / "deploy" / "invocation.lock"
        lock.parent.mkdir(parents=True)
        terminal = io.StringIO()
        with lock.open("w") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with mock.patch.dict(os.environ, {"HOME": str(self.root / "home"), "XDG_STATE_HOME": str(state)}, clear=False), contextlib.redirect_stderr(terminal):
                self.assertEqual(command.main(["--image", str(image), *self._command_package_args()]), 2)
        self.assertIn("another invocation", terminal.getvalue())
        self.assertFalse((state / "mkchad" / "deploy" / "latest-result.json").exists())

    def test_public_command_accepts_an_omitted_image(self):
        command = load_developer_command()
        state = self.root / "state"

        with mock.patch.dict(os.environ, {"HOME": str(self.root / "home"), "XDG_STATE_HOME": str(state)}, clear=False), mock.patch.object(
            command.deploy, "execute_developer_deployment", return_value=2
        ) as execute:
            self.assertEqual(command.main(self._command_package_args()), 2)

        self.assertIsNone(execute.call_args.args[1])

    def test_public_command_publishes_a_field_complete_fallback_for_os_errors(self):
        command = load_developer_command()
        state = self.root / "state"

        with mock.patch.dict(os.environ, {"HOME": str(self.root / "home"), "XDG_STATE_HOME": str(state)}, clear=False), mock.patch.object(
            command.deploy, "execute_developer_deployment", side_effect=OSError("preflight unavailable")
        ):
            self.assertEqual(command.main(self._command_package_args()), 2)

        result = json.loads((state / "mkchad" / "deploy" / "latest-result.json").read_text(encoding="utf-8"))
        self.assertEqual(result["mode"], "developer")
        self.assertEqual(result["outcome"], "failed")
        self.assertEqual(result["phase"], "preflight")
        self.assertEqual(result["failed_step"], "developer-admission")
        self.assertEqual(result["image"], {"choice": "omitted", "supplied_sha256": None, "active_sha256": None, "decision": "preserved"})
        self.assertIn("preflight unavailable", result["diagnostic"])

    def test_public_command_publishes_an_interrupted_fallback(self):
        command = load_developer_command()
        state = self.root / "state"

        with mock.patch.dict(os.environ, {"HOME": str(self.root / "home"), "XDG_STATE_HOME": str(state)}, clear=False), mock.patch.object(
            command.deploy, "execute_developer_deployment", side_effect=KeyboardInterrupt
        ):
            self.assertEqual(command.main(self._command_package_args()), 2)

        result = json.loads((state / "mkchad" / "deploy" / "latest-result.json").read_text(encoding="utf-8"))
        self.assertEqual(result["outcome"], "interrupted")
        self.assertEqual(result["failed_step"], "developer-admission")
        self.assertIn("interrupted", result["diagnostic"])


if __name__ == "__main__":
    unittest.main()
