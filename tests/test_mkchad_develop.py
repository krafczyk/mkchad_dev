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
        self._init(tools, {"tool.sh": "#!/bin/sh\nexit 0\n"})
        msk = self.root / "msk"
        self._init(msk, {"bin/install_nvim.sh": "#!/bin/sh\nexit 0\n", "nvim/bin/install_nvim_container": "#!/bin/sh\nexit 0\n"})
        (msk / "nvim/bin/install_nvim_container").chmod(0o755)
        self._commit(msk, "make image installer executable")
        self._run(["git", "-C", str(msk), "-c", "protocol.file.allow=always", "submodule", "add", "-q", str(tools), "container_tools"])
        self._commit(msk, "add tools")
        mkchad = self.root / "config"
        self._init(mkchad, {"init.lua": "return {}\n", ".gitignore": "opencode-server.json\nlazy-lock.json\n"})
        self._run(["git", "-C", str(self.workspace), "-c", "protocol.file.allow=always", "submodule", "add", "-q", str(msk), "msk_containers"])
        self._run(["git", "-C", str(self.workspace), "-c", "protocol.file.allow=always", "submodule", "add", "-q", str(mkchad), "mkchad"])
        self._commit(self.workspace, "add children")
        self._run(["git", "-C", str(self.workspace), "-c", "protocol.file.allow=always", "submodule", "update", "--init", "--recursive"])
        self.msk = self.workspace / "msk_containers"
        self.tools = self.msk / "container_tools"
        self.mkchad = self.workspace / "mkchad"

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

    def test_clean_unpublished_heads_are_staged_from_local_objects_with_dirty_parent_allowed(self):
        (self.workspace / "parent-untracked").write_text("allowed\n", encoding="ascii")
        (self.tools / "unpublished").write_text("snapshot\n", encoding="ascii")
        self._commit(self.tools, "unpublished tools")
        (self.mkchad / "init.lua").write_text("return { local_commit = true }\n", encoding="ascii")
        self._commit(self.mkchad, "unpublished config")
        for child in (self.msk, self.tools, self.mkchad):
            self._run(["git", "-C", str(child), "remote", "remove", "origin"])

        admission = self.deploy.admit_developer_workspace(self.workspace)
        self.assertTrue(admission["parent"]["dirty"])
        self.assertEqual(admission["commits"]["container_tools"], self._commit_value(self.tools))
        self.assertEqual(admission["commits"]["mkchad"], self._commit_value(self.mkchad))
        staging = pathlib.Path(tempfile.mkdtemp(prefix="mkchad-develop-stage-", dir="/tmp/mkchad-v1"))
        self.addCleanup(shutil.rmtree, staging, True)
        staged = self.deploy.stage_developer_sources(admission, staging)
        self.assertEqual(self._commit_value(staged["container_tools"][1]), admission["commits"]["container_tools"])
        self.assertEqual((staged["msk_containers"][1] / "container_tools" / "unpublished").read_text(encoding="ascii"), "snapshot\n")

    def test_dirty_children_and_workspace_layout_fail_before_staging(self):
        for child, relative in ((self.msk, "untracked"), (self.tools, "untracked"), (self.mkchad, "init.lua")):
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

    def test_source_changes_after_snapshot_recheck_are_rejected(self):
        admission = self.deploy.admit_developer_workspace(self.workspace)
        staging = pathlib.Path(tempfile.mkdtemp(prefix="mkchad-develop-stage-", dir="/tmp/mkchad-v1"))
        self.addCleanup(shutil.rmtree, staging, True)
        self.deploy.stage_developer_sources(admission, staging)
        (self.mkchad / "init.lua").write_text("changed\n", encoding="ascii")
        with self.assertRaisesRegex(self.deploy.DeploymentError, "source changed while staging"):
            self.deploy.recheck_developer_workspace(admission)

    def test_developer_result_is_mode_tagged_and_contains_workspace_and_component_evidence(self):
        admission = self.deploy.admit_developer_workspace(self.workspace)
        result = self.deploy.developer_result(
            admission,
            actions=[],
            artifact={"architecture": "x86_64", "sha256": "a" * 64},
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

    def test_local_snapshots_reach_the_regular_checkout_procedure(self):
        image = self.root / "image.sif"
        image.write_bytes(b"developer image")
        (self.mkchad / "init.lua").write_text("return { deployed = true }\n", encoding="ascii")
        self._commit(self.mkchad, "local deployment")
        for child in (self.msk, self.tools, self.mkchad):
            self._run(["git", "-C", str(child), "remote", "remove", "origin"])
        home = self.root / "home"
        environment = {
            "HOME": str(home), "XDG_CONFIG_HOME": str(self.root / "config"), "XDG_DATA_HOME": str(self.root / "data"),
            "XDG_STATE_HOME": str(self.root / "state"), "NVIM_CONTAINER_DIR": str(home / "containers"),
        }
        result_path = self.root / "developer-result.json"
        with mock.patch.dict(os.environ, environment, clear=False), mock.patch.object(self.deploy, "_runtime_image"), mock.patch.object(self.deploy, "_run_action"), mock.patch.object(self.deploy, "_host_tool_mismatch", return_value=None), mock.patch.object(self.deploy, "_active_image_digest", return_value=hashlib.sha256(image.read_bytes()).hexdigest()), mock.patch.object(self.deploy, "_consume_caller_image"):
            self.assertEqual(self.deploy.execute_developer_deployment(self.workspace, image, log_path=self.root / "developer.log", result_path=result_path, run_id="123e4567-e89b-12d3-a456-426614174000"), 0)
        result = json.loads(result_path.read_text(encoding="utf-8"))
        self.assertEqual(result["mode"], "developer")
        self.assertEqual(result["outcome"], "succeeded")
        destination = self.root / "config" / "mkchad"
        self.assertEqual(self._commit_value(destination), self._commit_value(self.mkchad))

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
                self.assertEqual(command.main(["--image", str(image)]), 2)
        self.assertIn("another invocation", terminal.getvalue())
        self.assertFalse((state / "mkchad" / "deploy" / "latest-result.json").exists())


if __name__ == "__main__":
    unittest.main()
