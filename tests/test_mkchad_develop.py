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
import time
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
        self._init(tools, {
            "tool.sh": "#!/bin/sh\nexit 0\n",
            "VERSION": "1.0.0-dev\n",
            "scripts/build-package.sh": "#!/bin/sh\nexit 0\n",
            "scripts/install-package.sh": "#!/bin/sh\nexit 0\n",
        })
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
        self.built_package = self.root / "container-tools-host-musl.tar.gz"
        self._write_package(self.package, "glibc")
        self._write_package(self.built_package, "musl")

    def _write_package(self, path, libc):
        """Write one synthetic archive whose metadata matches the tools checkout."""
        archive_metadata = {"schema": "container-tools.archive/v1", "architecture": self.deploy._native_architecture(), "version": "1.0.0-dev", "source_commit": self._commit_value(self.tools), "libc": libc}
        payload = json.dumps(archive_metadata, sort_keys=True, separators=(",", ":")).encode("ascii")
        package_root = f"container-tools-{archive_metadata['version']}-{archive_metadata['architecture']}-{archive_metadata['libc']}-{archive_metadata['source_commit']}"
        with tarfile.open(path, "w:gz", format=tarfile.PAX_FORMAT) as archive:
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

    def _command_args(self, image=None, source=None):
        """Return the reduced public command interface for the fixture sources."""
        image = image or self.root / "image.sif"
        image.write_bytes(b"image")
        return ["--image", str(image), "--container-tools-source", str(source or self.tools)]

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
                self.assertEqual(command.main(self._command_args(image)), 2)
        self.assertIn("another invocation", terminal.getvalue())
        self.assertFalse((state / "mkchad" / "deploy" / "latest-result.json").exists())

    def test_public_command_has_only_the_reduced_required_interface(self):
        command = load_developer_command()
        state = self.root / "state"

        with mock.patch.object(command.pathlib.Path, "cwd", return_value=self.workspace), mock.patch.dict(os.environ, {"HOME": str(self.root / "home"), "XDG_STATE_HOME": str(state)}, clear=False), mock.patch.object(
            command, "_build_host_package", return_value=(self.built_package, {**self._package_kwargs()["host_package_identity"], "libc": "musl"})
        ), mock.patch.object(
            command.deploy, "execute_developer_deployment", return_value=2
        ) as execute:
            self.assertEqual(command.main(self._command_args()), 2)

        self.assertEqual(execute.call_args.args[1], (self.root / "image.sif").absolute())
        self.assertEqual(execute.call_args.args[2], self.built_package)
        self.assertEqual(execute.call_args.kwargs["host_package_identity"]["libc"], "musl")
        with self.assertRaises(SystemExit):
            command.main(["--container-tools-host-package", str(self.package)])
        with self.assertRaises(SystemExit):
            command.main([*self._command_args(), "--libc", "musl"])

    def test_build_invocation_uses_source_commit_musl_and_derived_identity(self):
        command = load_developer_command()
        command.BUILD_ROOT.mkdir(parents=True, exist_ok=True)
        build_root = pathlib.Path(tempfile.mkdtemp(prefix="mkchad-develop-build-", dir="/tmp/mkchad-v1/container-tools-c11"))
        self.addCleanup(shutil.rmtree, build_root, True)
        commands = []

        def build(argv, **kwargs):
            commands.append(argv)
            output = pathlib.Path(argv[argv.index("--output-dir") + 1])
            output.mkdir(parents=True)
            shutil.copyfile(self.built_package, output / "container-tools-host.tar.gz")
            digest = hashlib.sha256(self.built_package.read_bytes()).hexdigest()
            (output / "container-tools-host.tar.gz.sha256").write_text(f"{digest}  container-tools-host.tar.gz\n", encoding="ascii")
            return subprocess.CompletedProcess(argv, 0, stdout="built\n", stderr="")

        source_commit = self._commit_value(self.tools)
        with mock.patch.object(command.deploy, "_capture", side_effect=build) as capture:
            archive, identity = command._build_host_package(self.tools, source_commit, build_root)

        self.assertEqual(archive.name, "container-tools-host.tar.gz")
        self.assertEqual(identity["sha256"], hashlib.sha256(self.built_package.read_bytes()).hexdigest())
        self.assertEqual(identity["version"], "1.0.0-dev")
        self.assertEqual(identity["source_commit"], source_commit)
        self.assertEqual(identity["libc"], "musl")
        self.assertEqual(commands[0][0], str(self.tools / "scripts/build-package.sh"))
        self.assertEqual(commands[0][commands[0].index("--source-commit") + 1], source_commit)
        self.assertEqual(commands[0][commands[0].index("--libc") + 1], "musl")
        self.assertNotIn("--version", commands[0])
        self.assertEqual(capture.call_args.kwargs, {"timeout": command.BUILD_TIMEOUT})

    def test_public_command_builds_from_a_private_exact_commit_checkout(self):
        command = load_developer_command()
        state = self.root / "state"
        build_root = self.root / "container-tools-c11"
        observed = {}

        def build(source, source_commit, temporary, log):
            observed["source"] = source
            observed["commit"] = source_commit
            observed["temporary"] = temporary
            self.assertNotEqual(source, self.tools)
            self.assertEqual(self._commit_value(source), source_commit)
            self.assertEqual((source / "VERSION").read_text(encoding="ascii"), "1.0.0-dev\n")
            return self.built_package, {**self._package_kwargs()["host_package_identity"], "libc": "musl"}

        with mock.patch.object(command.pathlib.Path, "cwd", return_value=self.workspace), mock.patch.dict(
            os.environ, {"HOME": str(self.root / "home"), "XDG_STATE_HOME": str(state)}, clear=False
        ), mock.patch.object(command, "BUILD_ROOT", build_root), mock.patch.object(
            command, "_build_host_package", side_effect=build
        ), mock.patch.object(command.deploy, "execute_developer_deployment", return_value=2):
            self.assertEqual(command.main(self._command_args()), 2)

        self.assertEqual(observed["commit"], self._commit_value(self.tools))
        self.assertFalse(observed["temporary"].exists())

    def test_stale_build_reclamation_leaves_non_run_entries_untouched(self):
        command = load_developer_command()
        build_root = self.root / "container-tools-c11"
        stale = build_root / "run-old"
        retained = build_root / "keep"
        run_file = build_root / "run-output.txt"
        build_root.mkdir()
        stale.mkdir()
        retained.mkdir()
        run_file.write_text("retain\n", encoding="ascii")
        (build_root / "run-link").symlink_to(retained)

        with mock.patch.object(command, "BUILD_ROOT", build_root):
            command._reclaim_stale_build_runs()

        self.assertFalse(stale.exists())
        self.assertTrue(retained.is_dir())
        self.assertTrue(run_file.is_file())
        self.assertTrue((build_root / "run-link").is_symlink())

    def test_cleanup_error_after_verified_deploy_does_not_replace_its_result(self):
        command = load_developer_command()
        state = self.root / "state"
        build_root = self.root / "container-tools-c11"

        def deploy_success(*args, **kwargs):
            kwargs["result_path"].write_text(
                json.dumps(
                    {
                        "mode": "developer",
                        "run_id": kwargs["run_id"],
                        "log_path": str(kwargs["log_path"]),
                        "outcome": "succeeded",
                    }
                ),
                encoding="utf-8",
            )
            return 0

        with mock.patch.object(command.pathlib.Path, "cwd", return_value=self.workspace), mock.patch.dict(
            os.environ, {"HOME": str(self.root / "home"), "XDG_STATE_HOME": str(state)}, clear=False
        ), mock.patch.object(command, "BUILD_ROOT", build_root), mock.patch.object(
            command, "_build_host_package", return_value=(self.built_package, {**self._package_kwargs()["host_package_identity"], "libc": "musl"})
        ), mock.patch.object(command.deploy, "execute_developer_deployment", side_effect=deploy_success), mock.patch.object(
            command.shutil, "rmtree", side_effect=OSError("cleanup unavailable")
        ) as cleanup:
            self.assertEqual(command.main(self._command_args()), 0)

        cleanup.assert_called_once()
        result = json.loads((state / "mkchad" / "deploy" / "latest-result.json").read_text(encoding="utf-8"))
        self.assertEqual(result["outcome"], "succeeded")
        self.assertEqual(len(list(build_root.glob("run-*"))), 1)

    def test_public_command_cleans_run_after_verified_deploy(self):
        command = load_developer_command()
        state = self.root / "state"
        build_root = self.root / "container-tools-c11"

        def deploy_success(*args, **kwargs):
            kwargs["result_path"].write_text(
                json.dumps(
                    {
                        "mode": "developer",
                        "run_id": kwargs["run_id"],
                        "log_path": str(kwargs["log_path"]),
                        "outcome": "succeeded",
                    }
                ),
                encoding="utf-8",
            )
            return 0

        with mock.patch.object(command.pathlib.Path, "cwd", return_value=self.workspace), mock.patch.dict(
            os.environ, {"HOME": str(self.root / "home"), "XDG_STATE_HOME": str(state)}, clear=False
        ), mock.patch.object(command, "BUILD_ROOT", build_root), mock.patch.object(
            command, "_build_host_package", return_value=(self.built_package, {**self._package_kwargs()["host_package_identity"], "libc": "musl"})
        ), mock.patch.object(command.deploy, "execute_developer_deployment", side_effect=deploy_success):
            self.assertEqual(command.main(self._command_args()), 0)

        result = json.loads((state / "mkchad" / "deploy" / "latest-result.json").read_text(encoding="utf-8"))
        self.assertEqual(result["outcome"], "succeeded")
        self.assertEqual(list(build_root.glob("run-*")), [])

    def test_cleanup_interruption_after_verified_deploy_does_not_replace_its_result(self):
        command = load_developer_command()
        state = self.root / "state"
        build_root = self.root / "container-tools-c11"

        def deploy_success(*args, **kwargs):
            kwargs["result_path"].write_text(
                json.dumps(
                    {
                        "mode": "developer",
                        "run_id": kwargs["run_id"],
                        "log_path": str(kwargs["log_path"]),
                        "outcome": "succeeded",
                    }
                ),
                encoding="utf-8",
            )
            return 0

        with mock.patch.object(command.pathlib.Path, "cwd", return_value=self.workspace), mock.patch.dict(
            os.environ, {"HOME": str(self.root / "home"), "XDG_STATE_HOME": str(state)}, clear=False
        ), mock.patch.object(command, "BUILD_ROOT", build_root), mock.patch.object(
            command, "_build_host_package", return_value=(self.built_package, {**self._package_kwargs()["host_package_identity"], "libc": "musl"})
        ), mock.patch.object(command.deploy, "execute_developer_deployment", side_effect=deploy_success), mock.patch.object(
            command.shutil, "rmtree", side_effect=KeyboardInterrupt
        ) as cleanup:
            self.assertEqual(command.main(self._command_args()), 0)

        cleanup.assert_called_once()
        result = json.loads((state / "mkchad" / "deploy" / "latest-result.json").read_text(encoding="utf-8"))
        self.assertEqual(result["outcome"], "succeeded")
        self.assertEqual(len(list(build_root.glob("run-*"))), 1)

    def test_public_command_cleans_run_after_matching_deployment_failure(self):
        command = load_developer_command()
        state = self.root / "state"
        build_root = self.root / "container-tools-c11"

        def deploy_failure(*args, **kwargs):
            kwargs["result_path"].write_text(
                json.dumps(
                    {
                        "mode": "developer",
                        "run_id": kwargs["run_id"],
                        "log_path": str(kwargs["log_path"]),
                        "outcome": "failed",
                    }
                ),
                encoding="utf-8",
            )
            return 2

        with mock.patch.object(command.pathlib.Path, "cwd", return_value=self.workspace), mock.patch.dict(
            os.environ, {"HOME": str(self.root / "home"), "XDG_STATE_HOME": str(state)}, clear=False
        ), mock.patch.object(command, "BUILD_ROOT", build_root), mock.patch.object(
            command, "_build_host_package", return_value=(self.built_package, {**self._package_kwargs()["host_package_identity"], "libc": "musl"})
        ), mock.patch.object(command.deploy, "execute_developer_deployment", side_effect=deploy_failure):
            self.assertEqual(command.main(self._command_args()), 2)

        result = json.loads((state / "mkchad" / "deploy" / "latest-result.json").read_text(encoding="utf-8"))
        self.assertEqual(result["outcome"], "failed")
        self.assertEqual(list(build_root.glob("run-*")), [])

    def test_global_build_lock_blocks_distinct_state_roots_with_a_result(self):
        command = load_developer_command()
        build_root = self.root / "container-tools-c11"
        state = self.root / "other-state"

        with mock.patch.object(command.pathlib.Path, "cwd", return_value=self.workspace), mock.patch.object(
            command, "BUILD_ROOT", build_root
        ), command._build_root_lock(), mock.patch.dict(
            os.environ, {"HOME": str(self.root / "home"), "XDG_STATE_HOME": str(state)}, clear=False
        ):
            self.assertEqual(command.main(self._command_args()), 2)

        result = json.loads((state / "mkchad" / "deploy" / "latest-result.json").read_text(encoding="utf-8"))
        self.assertEqual(result["outcome"], "failed")
        self.assertIn("another developer build", result["diagnostic"])

    def test_public_command_publishes_final_wait_cleanup_failure(self):
        command = load_developer_command()
        state = self.root / "state"
        build_root = self.root / "container-tools-c11"

        with mock.patch.object(command.pathlib.Path, "cwd", return_value=self.workspace), mock.patch.dict(
            os.environ, {"HOME": str(self.root / "home"), "XDG_STATE_HOME": str(state)}, clear=False
        ), mock.patch.object(command, "BUILD_ROOT", build_root), mock.patch.object(
            command, "_build_host_package", side_effect=command.deploy.DeploymentError("process-group cleanup did not complete")
        ):
            self.assertEqual(command.main(self._command_args()), 2)

        result = json.loads((state / "mkchad" / "deploy" / "latest-result.json").read_text(encoding="utf-8"))
        self.assertEqual(result["outcome"], "failed")
        self.assertIn("process-group cleanup did not complete", result["diagnostic"])

    def test_capture_replaces_malformed_subprocess_output(self):
        result = self.deploy._capture(
            [sys.executable, "-c", "import sys; sys.stdout.buffer.write(b'\\xff'); sys.stderr.buffer.write(b'\\xfe')"]
        )

        self.assertEqual(result.stdout, "\ufffd")
        self.assertEqual(result.stderr, "\ufffd")

    def test_capture_retains_normal_stdout_and_stderr_channels(self):
        result = self.deploy._capture(
            [sys.executable, "-c", "import sys; print('standard'); print('error', file=sys.stderr)"]
        )

        self.assertEqual(result.stdout, "standard\n")
        self.assertEqual(result.stderr, "error\n")

    def test_capture_rejects_output_overflow(self):
        with self.assertRaisesRegex(self.deploy.DeploymentError, "stdout output exceeded"):
            self.deploy._capture(
                [sys.executable, "-c", f"import sys; sys.stdout.write('x' * {self.deploy.CAPTURE_LIMIT + 1})"]
            )

    def test_build_rejects_missing_or_multiple_archives(self):
        command = load_developer_command()
        command.BUILD_ROOT.mkdir(parents=True, exist_ok=True)
        source_commit = self._commit_value(self.tools)
        for name, copies in (("missing", 0), ("multiple", 2)):
            with self.subTest(output=name), tempfile.TemporaryDirectory(prefix="mkchad-develop-build-", dir="/tmp/mkchad-v1/container-tools-c11") as temporary:
                def build(argv, **kwargs):
                    output = pathlib.Path(argv[argv.index("--output-dir") + 1])
                    output.mkdir(parents=True)
                    for index in range(copies):
                        shutil.copyfile(self.built_package, output / f"package-{index}.tar.gz")
                    return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

                with mock.patch.object(command.deploy, "_capture", side_effect=build):
                    with self.assertRaisesRegex(command.deploy.DeploymentError, "malformed output"):
                        command._build_host_package(self.tools, source_commit, pathlib.Path(temporary))

    def test_build_rejects_malformed_checksum_content(self):
        command = load_developer_command()
        command.BUILD_ROOT.mkdir(parents=True, exist_ok=True)
        source_commit = self._commit_value(self.tools)
        digest = hashlib.sha256(self.built_package.read_bytes()).hexdigest()
        checksums = ("fixture\n", f"{digest}  wrong-name.tar.gz\n", f"{'0' * 64}  container-tools-host.tar.gz\n")

        for checksum in checksums:
            with self.subTest(checksum=checksum), tempfile.TemporaryDirectory(prefix="mkchad-develop-build-", dir="/tmp/mkchad-v1/container-tools-c11") as temporary:
                def build(argv, **kwargs):
                    output = pathlib.Path(argv[argv.index("--output-dir") + 1])
                    output.mkdir(parents=True)
                    archive = output / "container-tools-host.tar.gz"
                    shutil.copyfile(self.built_package, archive)
                    archive.with_name(archive.name + ".sha256").write_text(checksum, encoding="ascii")
                    return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

                with mock.patch.object(command.deploy, "_capture", side_effect=build):
                    with self.assertRaisesRegex(command.deploy.DeploymentError, "checksum does not match"):
                        command._build_host_package(self.tools, source_commit, pathlib.Path(temporary))

    def test_build_output_is_redacted_and_emitted_once_per_destination(self):
        command = load_developer_command()
        command.BUILD_ROOT.mkdir(parents=True, exist_ok=True)
        source_commit = self._commit_value(self.tools)
        terminal = io.StringIO()
        log = io.StringIO()

        def build(argv, **kwargs):
            output = pathlib.Path(argv[argv.index("--output-dir") + 1])
            output.mkdir(parents=True)
            archive = output / "container-tools-host.tar.gz"
            shutil.copyfile(self.built_package, archive)
            digest = hashlib.sha256(archive.read_bytes()).hexdigest()
            archive.with_name(archive.name + ".sha256").write_text(f"{digest}  {archive.name}\n", encoding="ascii")
            return subprocess.CompletedProcess(argv, 0, stdout="token=secret\n", stderr="password=secret\n")

        with tempfile.TemporaryDirectory(prefix="mkchad-develop-build-", dir="/tmp/mkchad-v1/container-tools-c11") as temporary, contextlib.redirect_stdout(command._StreamLog(terminal, log)), contextlib.redirect_stderr(command._StreamLog(terminal, log)), mock.patch.object(command.deploy, "_capture", side_effect=build):
            command._build_host_package(self.tools, source_commit, pathlib.Path(temporary), log)

        expected = "token=<redacted>\npassword=<redacted>\n"
        self.assertEqual(terminal.getvalue(), expected)
        self.assertEqual(log.getvalue(), expected)

    def test_bounded_capture_terminates_timeout_descendants(self):
        """Kill a SIGTERM-ignoring descendant after its leader exits on SIGTERM."""
        marker = self.root / "timeout-descendant-marker"
        started = self.root / "timeout-descendant-started"
        release = self.root / "timeout-descendant-release"
        descendant = self.root / "ignore-term.py"
        descendant.write_text(
            "import pathlib, signal, sys, time\n"
            "marker = pathlib.Path(sys.argv[1])\n"
            "started = pathlib.Path(sys.argv[2])\n"
            "release = pathlib.Path(sys.argv[3])\n"
            "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
            "started.write_text('started')\n"
            "while not release.exists():\n"
            "    time.sleep(0.01)\n"
            "marker.write_text('survived')\n",
            encoding="ascii",
        )
        script = self.root / "spawn-descendant.py"
        script.write_text(
            "import pathlib, subprocess, sys, time\n"
            "marker = pathlib.Path(sys.argv[1])\n"
            "started = pathlib.Path(sys.argv[2])\n"
            "release = pathlib.Path(sys.argv[3])\n"
            "descendant = pathlib.Path(sys.argv[4])\n"
            "subprocess.Popen([sys.executable, str(descendant), str(marker), str(started), str(release)])\n"
            "time.sleep(60)\n",
            encoding="ascii",
        )

        with self.assertRaisesRegex(self.deploy.DeploymentError, "timed out"):
            self.deploy._capture([sys.executable, str(script), str(marker), str(started), str(release), str(descendant)], timeout=0.5)
        self.assertTrue(started.exists())
        release.write_text("release", encoding="ascii")
        time.sleep(0.5)
        self.assertFalse(marker.exists())

    def test_timeout_closes_pipes_held_by_an_escaped_descendant(self):
        """Return promptly without signaling a detached descendant retaining stdout/stderr."""
        started = self.root / "escaped-descendant-started"
        release = self.root / "escaped-descendant-release"
        completed = self.root / "escaped-descendant-completed"
        descendant = self.root / "escaped-descendant.py"
        descendant.write_text(
            "import os, pathlib, sys, time\n"
            "started = pathlib.Path(sys.argv[1])\n"
            "release = pathlib.Path(sys.argv[2])\n"
            "completed = pathlib.Path(sys.argv[3])\n"
            "started.write_text(f'{os.getpid()} {os.getsid(0)}')\n"
            "while not release.exists():\n"
            "    time.sleep(0.01)\n"
            "completed.write_text('released')\n",
            encoding="ascii",
        )
        leader = self.root / "spawn-escaped-descendant.py"
        leader.write_text(
            "import pathlib, subprocess, sys, time\n"
            "started = pathlib.Path(sys.argv[1])\n"
            "subprocess.Popen([sys.executable, sys.argv[2], sys.argv[1], sys.argv[3], sys.argv[4]], start_new_session=True)\n"
            "while not started.exists():\n"
            "    time.sleep(0.01)\n"
            "time.sleep(60)\n",
            encoding="ascii",
        )
        probe = (
            "import importlib.util, pathlib, sys, time\n"
            "spec = importlib.util.spec_from_file_location('mkchad_deploy', sys.argv[1])\n"
            "module = importlib.util.module_from_spec(spec)\n"
            "spec.loader.exec_module(module)\n"
            "started, leader, descendant, release, completed = sys.argv[2:]\n"
            "began = time.monotonic()\n"
            "try:\n"
            "    module._capture([sys.executable, leader, started, descendant, release, completed], timeout=0.25)\n"
            "except module.DeploymentError:\n"
            "    print(time.monotonic() - began)\n"
            "else:\n"
            "    raise AssertionError('capture unexpectedly completed')\n"
        )
        try:
            result = subprocess.run(
                [
                    sys.executable, "-c", probe, str(ROOT / "deploy/mkchad_deploy.py"),
                    str(started), str(leader), str(descendant), str(release), str(completed),
                ],
                check=True,
                capture_output=True,
                text=True,
                timeout=3,
            )
            self.assertLess(float(result.stdout), 2)
            pid, session = started.read_text(encoding="ascii").split()
            self.assertEqual(pid, session)
            release.write_text("release", encoding="ascii")
            deadline = time.monotonic() + 2
            while not completed.exists() and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertTrue(completed.exists())
        finally:
            release.touch()

    def test_termination_keeps_the_exited_leader_unreaped_until_group_kill(self):
        """Retain the leader PID as the process-group identity anchor through SIGKILL."""
        process = mock.Mock(pid=12345, returncode=None)
        events = []

        def killpg(pid, signum):
            events.append(("killpg", pid, signum))

        def waitid(kind, pid, options):
            events.append(("waitid", kind, pid, options))
            return object()

        def wait(timeout):
            events.append(("wait", timeout))

        process.wait.side_effect = wait
        with mock.patch.object(self.deploy.os, "killpg", side_effect=killpg), mock.patch.object(
            self.deploy.os, "waitid", side_effect=waitid
        ):
            self.deploy._terminate_group(process)

        self.assertEqual(
            events,
            [
                ("killpg", process.pid, self.deploy.signal.SIGTERM),
                ("waitid", self.deploy.os.P_PID, process.pid, self.deploy.os.WEXITED | self.deploy.os.WNOHANG | self.deploy.os.WNOWAIT),
                ("killpg", process.pid, self.deploy.signal.SIGKILL),
                ("wait", 5),
            ],
        )

    def test_termination_reports_an_unreaped_group_without_resignaling(self):
        process = mock.Mock(pid=12345, returncode=None)
        process.wait.side_effect = subprocess.TimeoutExpired([], 5)
        events = []

        def killpg(pid, signum):
            events.append((pid, signum))

        with mock.patch.object(self.deploy.os, "killpg", side_effect=killpg), mock.patch.object(
            self.deploy.os, "waitid", return_value=object()
        ):
            with self.assertRaisesRegex(self.deploy.DeploymentError, "cleanup did not complete"):
                self.deploy._terminate_group(process)

        self.assertEqual(events, [(process.pid, self.deploy.signal.SIGTERM), (process.pid, self.deploy.signal.SIGKILL)])

    def test_termination_does_not_signal_when_the_leader_was_reaped_elsewhere(self):
        process = mock.Mock(pid=12345, returncode=None)

        with mock.patch.object(self.deploy.os, "killpg") as killpg, mock.patch.object(
            self.deploy.os, "waitid", side_effect=ChildProcessError
        ):
            self.deploy._terminate_group(process)

        killpg.assert_called_once_with(process.pid, self.deploy.signal.SIGTERM)

    def test_public_command_rejects_a_source_checkout_at_another_commit(self):
        command = load_developer_command()
        other = self.root / "other-container-tools"
        self._init(other, {"VERSION": "1.0.0-dev\n", "scripts/build-package.sh": "#!/bin/sh\nexit 0\n"})
        state = self.root / "state"

        with mock.patch.object(command.pathlib.Path, "cwd", return_value=self.workspace), mock.patch.dict(os.environ, {"HOME": str(self.root / "home"), "XDG_STATE_HOME": str(state)}, clear=False), mock.patch.object(
            command, "_build_host_package", side_effect=AssertionError("must not build")
        ):
            self.assertEqual(command.main(self._command_args(source=other)), 2)

        result = json.loads((state / "mkchad" / "deploy" / "latest-result.json").read_text(encoding="utf-8"))
        self.assertIn("does not match admitted", result["diagnostic"])

    def test_public_command_cleans_failed_or_timed_out_build_and_publishes_failure(self):
        command = load_developer_command()
        state = self.root / "state"
        build_parent = self.root / "container-tools-c11"

        for build, diagnostic in (
            (subprocess.CompletedProcess([], 78, stdout="", stderr="build failed\n"), "package build failed"),
            (command.deploy.DeploymentError("command timed out after 1 seconds: build-package.sh"), "timed out"),
        ):
            capture = command.deploy._capture

            def fail_build(argv, **kwargs):
                if pathlib.Path(argv[0]).name == "build-package.sh":
                    if isinstance(build, Exception):
                        raise build
                    return build
                return capture(argv, **kwargs)

            with self.subTest(build=type(build).__name__), mock.patch.object(command.pathlib.Path, "cwd", return_value=self.workspace), mock.patch.dict(os.environ, {"HOME": str(self.root / "home"), "XDG_STATE_HOME": str(state)}, clear=False), mock.patch.object(
                command, "BUILD_ROOT", build_parent
            ), mock.patch.object(command.deploy, "_capture", side_effect=fail_build):
                self.assertEqual(command.main(self._command_args()), 2)

            self.assertEqual([path.name for path in build_parent.iterdir()], ["build.lock"])
            result = json.loads((state / "mkchad" / "deploy" / "latest-result.json").read_text(encoding="utf-8"))
            self.assertEqual(result["outcome"], "failed")
            self.assertIn(diagnostic, result["diagnostic"])

    def test_public_command_cleans_interrupted_build_and_publishes_interruption(self):
        command = load_developer_command()
        state = self.root / "state"
        build_parent = self.root / "container-tools-c11"
        capture = command.deploy._capture

        def interrupt_build(argv, **kwargs):
            if pathlib.Path(argv[0]).name == "build-package.sh":
                raise KeyboardInterrupt
            return capture(argv, **kwargs)

        with mock.patch.object(command.pathlib.Path, "cwd", return_value=self.workspace), mock.patch.dict(os.environ, {"HOME": str(self.root / "home"), "XDG_STATE_HOME": str(state)}, clear=False), mock.patch.object(
            command, "BUILD_ROOT", build_parent
        ), mock.patch.object(command.deploy, "_capture", side_effect=interrupt_build):
            self.assertEqual(command.main(self._command_args()), 2)

        self.assertEqual([path.name for path in build_parent.iterdir()], ["build.lock"])
        result = json.loads((state / "mkchad" / "deploy" / "latest-result.json").read_text(encoding="utf-8"))
        self.assertEqual(result["outcome"], "interrupted")

    def test_public_command_publishes_a_field_complete_fallback_for_os_errors(self):
        command = load_developer_command()
        state = self.root / "state"

        with mock.patch.object(command.pathlib.Path, "cwd", return_value=self.workspace), mock.patch.dict(os.environ, {"HOME": str(self.root / "home"), "XDG_STATE_HOME": str(state)}, clear=False), mock.patch.object(
            command.deploy, "execute_developer_deployment", side_effect=OSError("preflight unavailable")
        ):
            with mock.patch.object(command, "_build_host_package", return_value=(self.built_package, {**self._package_kwargs()["host_package_identity"], "libc": "musl"})):
                self.assertEqual(command.main(self._command_args()), 2)

        result = json.loads((state / "mkchad" / "deploy" / "latest-result.json").read_text(encoding="utf-8"))
        self.assertEqual(result["mode"], "developer")
        self.assertEqual(result["outcome"], "failed")
        self.assertEqual(result["phase"], "preflight")
        self.assertEqual(result["failed_step"], "developer-admission")
        self.assertEqual(result["image"], {"choice": "install", "supplied_sha256": None, "active_sha256": None, "decision": "unavailable"})
        self.assertIn("preflight unavailable", result["diagnostic"])

    def test_public_command_publishes_an_interrupted_fallback(self):
        command = load_developer_command()
        state = self.root / "state"
        build_root = self.root / "container-tools-c11"

        with mock.patch.object(command.pathlib.Path, "cwd", return_value=self.workspace), mock.patch.dict(os.environ, {"HOME": str(self.root / "home"), "XDG_STATE_HOME": str(state)}, clear=False), mock.patch.object(
            command, "BUILD_ROOT", build_root
        ), mock.patch.object(
            command.deploy, "execute_developer_deployment", side_effect=KeyboardInterrupt
        ):
            with mock.patch.object(command, "_build_host_package", return_value=(self.built_package, {**self._package_kwargs()["host_package_identity"], "libc": "musl"})):
                self.assertEqual(command.main(self._command_args()), 2)

        result = json.loads((state / "mkchad" / "deploy" / "latest-result.json").read_text(encoding="utf-8"))
        self.assertEqual(result["outcome"], "interrupted")
        self.assertEqual(result["failed_step"], "developer-admission")
        self.assertIn("interrupted", result["diagnostic"])
        self.assertEqual(list(build_root.glob("run-*")), [])


if __name__ == "__main__":
    unittest.main()
