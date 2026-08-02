"""Focused schema-2 deployment contract and CLI tests."""

import copy
import fcntl
import hashlib
import importlib.util
import json
import os
import pathlib
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock


ROOT = pathlib.Path(__file__).resolve().parents[1]


def load_deployer():
    """Load the release-owned deployer directly from the parent checkout."""
    spec = importlib.util.spec_from_file_location("mkchad_deploy", ROOT / "deploy/mkchad_deploy.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class DeploymentContractTests(unittest.TestCase):
    """Prove schema-2 is the only accepted release authority."""

    def setUp(self):
        self.deploy = load_deployer()
        self.release = json.loads((ROOT / "deploy" / "release.json").read_text(encoding="utf-8"))

    def test_checked_in_manifest_is_schema_2_and_explicit(self):
        self.assertEqual(self.deploy.validate_release_v2_document(self.release), self.release)
        self.assertEqual({component["name"] for component in self.release["components"]}, set(self.deploy.COMPONENTS))
        self.assertNotIn("config_files", {component["name"] for component in self.release["components"]})
        for component in self.release["components"]:
            self.assertIn("repository", component)
            self.assertIn("commit", component)

    def test_schema_1_and_unknown_fields_are_rejected(self):
        schema_1 = copy.deepcopy(self.release)
        schema_1["schema"] = 1
        with self.assertRaisesRegex(self.deploy.DeploymentError, "schema"):
            self.deploy.validate_release_v2_document(schema_1)
        unknown = copy.deepcopy(self.release)
        unknown["components"][0]["authority"] = "legacy"
        with self.assertRaisesRegex(self.deploy.DeploymentError, "unknown"):
            self.deploy.validate_release_v2_document(unknown)

    def test_manifest_pins_do_not_depend_on_gitlinks(self):
        different_pin = copy.deepcopy(self.release)
        different_pin["components"][0]["commit"] = "f" * 40
        self.assertEqual(self.deploy.validate_release_v2_document(different_pin), different_pin)

    def test_only_internal_handoff_is_parsed(self):
        handoff = ["deploy", "--release", "v2", "--image", "/tmp/image.sif", "--log-path", "/tmp/run.log", "--result-path", "/tmp/result.json", "--run-id", "123e4567-e89b-12d3-a456-426614174000"]
        with mock.patch.object(self.deploy, "execute_deployment", return_value=0) as execute:
            self.assertEqual(self.deploy.main(handoff), 0)
        self.assertEqual(execute.call_args.kwargs["release_tag"], "v2")
        for old in ("validate-release", "recover", "supervise", "deploy --verified-release x"):
            with self.subTest(old=old), self.assertRaises(SystemExit):
                self.deploy.main(old.split())

    def test_terminal_result_validation_is_closed(self):
        result = {
            "schema": 1,
            "run_id": "123e4567-e89b-12d3-a456-426614174000",
            "release": "v2",
            "started_at": "2026-08-02T00:00:00Z",
            "completed_at": "2026-08-02T00:00:01Z",
            "outcome": "succeeded",
            "phase": "verify",
            "components": [
                {
                    "name": component["name"],
                    "disposition": component["disposition"],
                    "requested_commit": component["commit"],
                    "observed_commit": component["commit"] if component["disposition"] == "deployed" else None,
                }
                for component in self.release["components"]
            ],
            "artifact": {"architecture": "x86_64", "expected_sha256": "a" * 64, "observed_sha256": "a" * 64},
            "actions": [],
            "configuration_handoff": {
                "state": "configuration-pending",
                "contract_version": 1,
                "guidance": self.release["configuration"]["guidance"],
                "components": [
                    {"name": component["name"], "repository": component["repository"], "commit": component["commit"]}
                    for component in self.release["components"]
                    if component["disposition"] == "configuration-owned"
                ],
            },
            "failed_step": None,
            "diagnostic": "deployment complete",
            "log_path": "/tmp/mkchad-v1/result.log",
        }
        self.assertEqual(self.deploy.validate_result_v1_document(result), result)
        for mutate in (
            lambda value: value["artifact"].__setitem__("environment", {}),
            lambda value: value["components"][0].__setitem__("requested_commit", "bad"),
            lambda value: value["configuration_handoff"]["components"].pop(),
        ):
            malformed = copy.deepcopy(result)
            mutate(malformed)
            with self.assertRaises(self.deploy.DeploymentError):
                self.deploy.validate_result_v1_document(malformed)


class DeploymentExecutionTests(unittest.TestCase):
    """Exercise the real schema-2 flow against isolated local fixture repositories."""

    def setUp(self):
        self.deploy = load_deployer()
        self.root = pathlib.Path(tempfile.mkdtemp(prefix="simplified-deploy-", dir="/tmp/mkchad-v1"))
        self.addCleanup(shutil.rmtree, self.root, True)
        self.home = self.root / "home"
        self.config = self.root / "config"
        self.data = self.root / "data"
        self.state = self.root / "state"
        self.runtime = self.root / "runtime"
        self.bin = self.root / "bin"
        for path in (self.home, self.config, self.data, self.state, self.runtime, self.bin):
            path.mkdir(mode=0o700)
        self.environment = mock.patch.dict(os.environ, {
            "HOME": str(self.home),
            "XDG_CONFIG_HOME": str(self.config),
            "XDG_DATA_HOME": str(self.data),
            "XDG_STATE_HOME": str(self.state),
            "XDG_RUNTIME_DIR": str(self.runtime),
            "NVIM_CONTAINER_DIR": str(self.home / "containers"),
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": str(self.home / ".gitconfig"),
            "TMPDIR": str(self.root),
            "PATH": str(self.bin) + os.pathsep + os.environ["PATH"],
        })
        self.environment.start()
        self.addCleanup(self.environment.stop)
        previous_tempdir = tempfile.tempdir
        tempfile.tempdir = str(self.root)
        self.addCleanup(setattr, tempfile, "tempdir", previous_tempdir)
        runtime = self.bin / "apptainer"
        runtime.write_text("#!/bin/sh\nprintf '%s\\n' \"${FAKE_IMAGE_ARCH:-x86_64}\"\n", encoding="ascii")
        runtime.chmod(0o755)
        self.commits = self._sources()
        (self.home / ".gitconfig").write_text(
            f'[url "file://{self.root}/sources/"]\n\tinsteadOf = git@github.com:krafczyk/\n',
            encoding="ascii",
        )
        self.image = self.root / "image.sif"
        self.image.write_bytes(b"fixture image\n")
        self.repo = self.root / "release"
        (self.repo / "deploy").mkdir(parents=True)
        self.manifest = json.loads((ROOT / "deploy" / "release.json").read_text(encoding="utf-8"))
        for component in self.manifest["components"]:
            if component["name"] in self.commits:
                component["commit"] = self.commits[component["name"]]
        self.manifest["artifacts"]["nvim-image"][0]["sha256"] = hashlib.sha256(self.image.read_bytes()).hexdigest()
        self._write_manifest()

    def _repo(self, name, files, executable=()):
        repository = self.root / "sources" / name
        repository.mkdir(parents=True)
        for relative, contents in files.items():
            path = repository / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(contents, encoding="ascii")
            if relative in executable:
                path.chmod(0o755)
        subprocess.run(["git", "init", "-q", str(repository)], check=True)
        subprocess.run(["git", "-C", str(repository), "add", "."], check=True)
        subprocess.run(["git", "-C", str(repository), "-c", "user.name=test", "-c", "user.email=test@example.invalid", "commit", "-q", "-m", name], check=True)
        return subprocess.check_output(["git", "-C", str(repository), "rev-parse", "HEAD"], text=True).strip()

    def _sources(self):
        tools = self._repo("container_tools", {"tool.sh": "#!/bin/sh\nexit 0\n"}, {"tool.sh"})
        msk = self._repo(
            "msk_containers",
            {
                "bin/install_nvim.sh": "#!/bin/sh\nset -eu\nif [ \"${1:-}\" = --check ]; then\n  { [ \"$#\" -eq 1 ] || { [ \"$#\" -eq 3 ] && [ \"$2\" = --recovery-dir ] && [ -d \"$3\" ]; }; } || exit 2\n  exit \"${HOST_PREFLIGHT_STATUS:-0}\"\nfi\n[ \"$#\" -eq 2 ] && [ \"$1\" = --recovery-dir ] && [ -d \"$2\" ] || exit 2\nmkdir -p \"$HOME/.local/bin\"\ncp container_tools/*.sh nvim/bin/* \"$HOME/.local/bin/\"\nchmod 755 \"$HOME/.local/bin\"/*\n",
                "nvim/bin/nvim": "#!/bin/sh\nexit 0\n",
                "nvim/bin/nvim_library.sh": "#!/bin/sh\nreturn 0\n",
                "nvim/bin/mkchad-opencode-server": "#!/bin/sh\nprintf '{\"schema\":1,\"ok\":true,\"command\":\"status\",\"status\":\"%s\"}\\n' \"${LIFECYCLE_STATUS:-inactive}\"\n",
                "nvim/bin/install_nvim_container": "#!/bin/sh\nset -eu\ndry_run=0\nif [ \"${1:-}\" = --dry-run ]; then dry_run=1; shift; fi\n[ \"$#\" -eq 3 ] && [ \"$1\" = --source-revision ] && [ \"${#2}\" -eq 40 ] && [ \"${3##*/}\" = nvim_container_x86.sif ] || exit 2\n[ \"$dry_run\" -eq 0 ] || exit \"${IMAGE_PREFLIGHT_STATUS:-0}\"\n[ \"${IMAGE_INSTALL_STATUS:-0}\" = 0 ] || exit \"$IMAGE_INSTALL_STATUS\"\nmkdir -p \"$NVIM_CONTAINER_DIR\"\ncp \"$3\" \"$NVIM_CONTAINER_DIR/neovim_fixture.sif\"\nln -sfn neovim_fixture.sif \"$NVIM_CONTAINER_DIR/neovim.sif\"\n",
            },
            {"bin/install_nvim.sh", "nvim/bin/nvim", "nvim/bin/nvim_library.sh", "nvim/bin/mkchad-opencode-server", "nvim/bin/install_nvim_container"},
        )
        old_mkchad = self._repo("mkchad", {".gitignore": "opencode-server.json\n", "init.lua": "return {}\n"})
        mkchad_source = self.root / "sources" / "mkchad"
        (mkchad_source / ".gitignore").write_text("opencode-server.json\nlazy-lock.json\n", encoding="ascii")
        subprocess.run(["git", "-C", str(mkchad_source), "add", ".gitignore"], check=True)
        subprocess.run(["git", "-C", str(mkchad_source), "-c", "user.name=test", "-c", "user.email=test@example.invalid", "commit", "-q", "-m", "ignore generated lock"], check=True)
        mkchad = subprocess.check_output(["git", "-C", str(mkchad_source), "rev-parse", "HEAD"], text=True).strip()
        return {"msk_containers": msk, "container_tools": tools, "mkchad": mkchad, "old_mkchad": old_mkchad}

    def _write_manifest(self):
        (self.repo / "deploy" / "release.json").write_text(json.dumps(self.manifest), encoding="utf-8")

    def _execute(self, run_id="123e4567-e89b-12d3-a456-426614174000"):
        self.result = self.root / "result.json"
        return self.deploy.execute_deployment(
            self.repo,
            self.image,
            release_tag="v2-fixture",
            log_path=self.root / "run.log",
            result_path=self.result,
            run_id=run_id,
        )

    def _result(self):
        return json.loads(self.result.read_text(encoding="utf-8"))

    def _destination(self, name):
        if name == "mkchad":
            return self.config / "mkchad"
        base = self.data / "mkchad" / "components" / "msk_containers"
        return base / "container_tools" if name == "container_tools" else base

    def test_fresh_update_and_active_compliant_rerun(self):
        self.assertEqual(self._execute(), 0)
        first = self._result()
        self.assertEqual(first["outcome"], "succeeded")
        self.assertEqual([action["status"] for action in first["actions"]], ["succeeded"] * 5)
        for name in ("msk_containers", "container_tools", "mkchad"):
            destination = self._destination(name)
            self.assertEqual(subprocess.check_output(["git", "-C", str(destination), "rev-parse", "HEAD"], text=True).strip(), self.commits[name])
            self.assertEqual(subprocess.check_output(["git", "-C", str(destination), "config", "--get", "remote.origin.url"], text=True).strip().removesuffix(".git"), f"git@github.com:krafczyk/{name}")
        installed_image = self.home / "containers" / os.readlink(self.home / "containers" / "neovim.sif")
        self.assertEqual(hashlib.sha256(installed_image.read_bytes()).hexdigest(), self.manifest["artifacts"]["nvim-image"][0]["sha256"])
        self.assertEqual((self.home / ".local" / "bin" / "nvim_library.sh").stat().st_mode & 0o777, 0o755)
        preserved = {"opencode-server.json": "private\n", "lazy-lock.json": "{}\n"}
        for name, contents in preserved.items():
            (self._destination("mkchad") / name).write_text(contents, encoding="ascii")
        source = self.root / "sources" / "mkchad"
        (source / "init.lua").write_text("return { updated = true }\n", encoding="ascii")
        subprocess.run(["git", "-C", str(source), "add", "init.lua"], check=True)
        subprocess.run(["git", "-C", str(source), "-c", "user.name=test", "-c", "user.email=test@example.invalid", "commit", "-q", "-m", "update"], check=True)
        next(component for component in self.manifest["components"] if component["name"] == "mkchad")["commit"] = subprocess.check_output(["git", "-C", str(source), "rev-parse", "HEAD"], text=True).strip()
        self._write_manifest()
        self.assertEqual(self._execute("223e4567-e89b-12d3-a456-426614174000"), 0)
        self.assertEqual({name: (self._destination("mkchad") / name).read_text(encoding="ascii") for name in preserved}, preserved)
        os.environ["LIFECYCLE_STATUS"] = "healthy"
        self.assertEqual(self._execute("323e4567-e89b-12d3-a456-426614174000"), 0)
        self.assertEqual([action["status"] for action in self._result()["actions"]], ["succeeded", "succeeded", "skipped-compliant", "skipped-compliant", "succeeded"])

    def test_dirty_wrong_image_and_preflight_failure_do_not_mutate(self):
        wrong = self.manifest["artifacts"]["nvim-image"][0]["sha256"]
        self.manifest["artifacts"]["nvim-image"][0]["sha256"] = "0" * 64
        self._write_manifest()
        self.assertEqual(self._execute(), 2)
        self.assertFalse(self._destination("msk_containers").exists())
        self.manifest["artifacts"]["nvim-image"][0]["sha256"] = wrong
        self._write_manifest()
        os.environ["IMAGE_PREFLIGHT_STATUS"] = "7"
        self.assertEqual(self._execute("223e4567-e89b-12d3-a456-426614174000"), 2)
        self.assertFalse(self._destination("msk_containers").exists())
        os.environ.pop("IMAGE_PREFLIGHT_STATUS")
        self.assertEqual(self._execute("323e4567-e89b-12d3-a456-426614174000"), 0)
        (self._destination("mkchad") / "dirty").write_text("user work\n", encoding="ascii")
        self.assertEqual(self._execute("423e4567-e89b-12d3-a456-426614174000"), 2)
        self.assertEqual((self._destination("mkchad") / "dirty").read_text(encoding="ascii"), "user work\n")

    def test_apply_failure_is_repairable_without_recovery_state(self):
        os.environ["IMAGE_INSTALL_STATUS"] = "7"
        self.assertEqual(self._execute(), 2)
        failed = self._result()
        self.assertEqual(failed["failed_step"], "msk_containers:image-install")
        self.assertEqual([action["status"] for action in failed["actions"]], ["succeeded", "succeeded", "succeeded", "failed", "pending"])
        self.assertFalse(list(self.state.rglob("*journal*")))
        self.assertFalse(list(self.state.rglob("*recovery*")))
        os.environ.pop("IMAGE_INSTALL_STATUS")
        self.assertEqual(self._execute("223e4567-e89b-12d3-a456-426614174000"), 0)

    def test_preflight_refuses_unavailable_inputs_partial_targets_and_lock_contention(self):
        msk_component = next(component for component in self.manifest["components"] if component["name"] == "msk_containers")
        valid_commit = msk_component["commit"]
        msk_component["commit"] = "f" * 40
        self._write_manifest()
        self.assertEqual(self._execute(), 2)
        self.assertFalse(self._destination("msk_containers").exists())
        msk_component["commit"] = valid_commit
        self._write_manifest()

        partial = self.home / ".local" / "bin" / "nvim_library.sh"
        partial.parent.mkdir(parents=True)
        partial.write_text("partial\n", encoding="ascii")
        self.assertEqual(self._execute("223e4567-e89b-12d3-a456-426614174000"), 2)
        self.assertFalse(self._destination("msk_containers").exists())
        partial.unlink()

        lock = self.state / "mkchad" / "deployment.lock"
        lock.parent.mkdir(parents=True, exist_ok=True)
        with lock.open("w") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.assertEqual(self._execute("323e4567-e89b-12d3-a456-426614174000"), 2)
        self.assertFalse(self._destination("msk_containers").exists())

        os.environ["FAKE_IMAGE_ARCH"] = "aarch64"
        self.assertEqual(self._execute("423e4567-e89b-12d3-a456-426614174000"), 2)
        self.assertFalse(self._destination("msk_containers").exists())

    def test_wrong_origin_and_tracked_work_are_preserved(self):
        self.assertEqual(self._execute(), 0)
        mkchad = self._destination("mkchad")
        subprocess.run(["git", "-C", str(mkchad), "remote", "set-url", "origin", "git@github.com:krafczyk/other"], check=True)
        self.assertEqual(self._execute("223e4567-e89b-12d3-a456-426614174000"), 2)
        subprocess.run(["git", "-C", str(mkchad), "remote", "set-url", "origin", "git@github.com:krafczyk/mkchad"], check=True)
        (mkchad / "init.lua").write_text("user change\n", encoding="ascii")
        self.assertEqual(self._execute("323e4567-e89b-12d3-a456-426614174000"), 2)
        self.assertEqual((mkchad / "init.lua").read_text(encoding="ascii"), "user change\n")

    def test_caller_image_inside_managed_directory_is_not_a_partial_target(self):
        container_dir = self.home / "containers"
        container_dir.mkdir()
        source_image = container_dir / "neovim_x86_g1_fixture_build.sif"
        self.image = self.image.replace(source_image)

        self.assertEqual(self._execute(), 0)
        self.assertFalse(source_image.exists())
        self.assertTrue((container_dir / os.readlink(container_dir / "neovim.sif")).is_file())

    def test_failed_install_preserves_caller_image_inside_managed_directory(self):
        container_dir = self.home / "containers"
        container_dir.mkdir()
        source_image = container_dir / "neovim_x86_g1_fixture_build.sif"
        self.image = self.image.replace(source_image)
        os.environ["IMAGE_INSTALL_STATUS"] = "7"

        self.assertEqual(self._execute(), 2)
        self.assertTrue(source_image.is_file())

    def test_active_canonical_caller_image_is_not_removed(self):
        self.assertEqual(self._execute(), 0)
        container_dir = self.home / "containers"
        active_image = container_dir / os.readlink(container_dir / "neovim.sif")
        self.image = active_image

        self.assertEqual(self._execute("223e4567-e89b-12d3-a456-426614174000"), 0)
        self.assertTrue(active_image.is_file())

    def test_other_image_inside_managed_directory_remains_a_partial_target(self):
        container_dir = self.home / "containers"
        container_dir.mkdir()
        (container_dir / "neovim_x86_g1_existing_build.sif").write_bytes(b"existing image\n")

        self.assertEqual(self._execute(), 2)

    def test_existing_config_only_target_bootstraps_lifecycle_and_preserves_lazy_lock(self):
        mkchad = self._destination("mkchad")
        mkchad.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "clone", "-q", str(self.root / "sources" / "mkchad"), str(mkchad)], check=True)
        subprocess.run(["git", "-C", str(mkchad), "checkout", "-q", "--detach", self.commits["old_mkchad"]], check=True)
        subprocess.run(["git", "-C", str(mkchad), "remote", "set-url", "origin", "git@github.com:krafczyk/mkchad"], check=True)
        lazy_lock = mkchad / "lazy-lock.json"
        lazy_lock.write_text('{"plugins": {}}\n', encoding="ascii")

        self.assertEqual(self._execute(), 0)

        self.assertEqual(lazy_lock.read_text(encoding="ascii"), '{"plugins": {}}\n')
        self.assertTrue((self.home / ".local" / "bin" / "mkchad-opencode-server").is_file())


if __name__ == "__main__":
    unittest.main()
