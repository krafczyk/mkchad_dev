"""Focused tests for the installed MkChad release launcher."""

import contextlib
import importlib.util
import io
import json
import os
import pathlib
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock


ROOT = pathlib.Path(__file__).resolve().parents[1]


def load_launcher():
    """Load the launcher directly from the parent checkout."""
    spec = importlib.util.spec_from_file_location("mkchad_release", ROOT / "deploy/mkchad_release.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def load_result_validator():
    """Load the deployer's result validator for launcher contract checks."""
    spec = importlib.util.spec_from_file_location("mkchad_deploy_result_contract", ROOT / "deploy/mkchad_deploy.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module.validate_result_v2_document


class ReleaseLauncherTests(unittest.TestCase):
    """Exercise exact-tag acquisition, synchronous handoff, and diagnostics."""

    def setUp(self):
        self.launcher = load_launcher()
        self.validate_result = load_result_validator()
        self.root = pathlib.Path(tempfile.mkdtemp(prefix="mkchad-release-test-", dir="/tmp/mkchad-v1"))
        self.home = self.root / "home"
        self.state = self.root / "state"
        self.temp = self.root / "temporary"
        for path in (self.home, self.state, self.temp):
            path.mkdir()
        self.temp_environment = mock.patch.dict(os.environ, {"TMPDIR": str(self.temp)}, clear=False)
        self.temp_environment.start()
        self.addCleanup(self.temp_environment.stop)
        previous_tempdir = tempfile.tempdir
        tempfile.tempdir = str(self.temp)
        self.addCleanup(setattr, tempfile, "tempdir", previous_tempdir)
        self.image = self.root / "image with spaces.sif"
        self.image.write_bytes(b"image")

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def remote_with_release(self, *, annotated=True, deployer=True, tag="v1"):
        """Create a local release remote with a configurable release deployer."""
        flavor = "annotated" if annotated else "lightweight"
        source = self.root / f"release source {tag} {flavor} {deployer}"
        remote = self.root / f"release remote {tag} {flavor} {deployer}.git"
        (source / "deploy").mkdir(parents=True)
        if deployer:
            (source / "deploy" / "mkchad_deploy.py").write_text(
                """import json
import os
import pathlib
import sys

def value(name):
    return sys.argv[sys.argv.index(name) + 1]

release = value('--release')
image = value('--image')
log_path = value('--log-path')
result_path = pathlib.Path(value('--result-path'))
run_id = value('--run-id')
capture = os.environ.get('MKCHAD_RELEASE_CAPTURE')
if capture:
    pathlib.Path(capture).write_text(json.dumps({'argv': sys.argv[1:], 'release': release, 'image': image, 'log_path': log_path, 'result_path': str(result_path), 'run_id': run_id, 'container_tools_c_compiler': os.environ.get('CONTAINER_TOOLS_C_COMPILER')}), encoding='utf-8')
print('child standard output')
print('child standard error', file=sys.stderr)
mode = os.environ.get('MKCHAD_RELEASE_CHILD_MODE', 'success')
child_exit = int(os.environ.get('MKCHAD_RELEASE_CHILD_EXIT', '0'))
if mode not in ('missing', 'stale'):
    document = {
        'schema': 2,
        'run_id': run_id if mode != 'wrong-run' else '123e4567-e89b-12d3-a456-426614174000',
        'release': release,
        'started_at': '2026-08-02T12:00:00Z',
        'completed_at': '2026-08-02T12:00:01Z',
        'outcome': 'unknown' if mode == 'malformed' else ('succeeded' if child_exit == 0 else 'failed'),
        'phase': 'verify' if child_exit == 0 else 'apply',
        'components': [
            {'name': name, 'disposition': disposition, 'requested_commit': '0' * 40, 'observed_commit': None}
            for name, disposition in [
                ('msk_containers', 'deployed'), ('container_tools', 'deployed'), ('mkchad', 'deployed'), ('opencode-db', 'deployed'),
                ('opencode', 'embedded'), ('opencode.nvim', 'configuration-owned'),
                ('opencode-project-reload', 'configuration-owned'), ('compound-engineering', 'configuration-owned'),
                ('opencode_sprint_loop', 'disabled'),
            ]
        ],
        'artifact': {'architecture': 'x86_64', 'expected_sha256': '0' * 64, 'observed_sha256': None},
        'actions': [],
        'configuration_handoff': {
            'state': 'configuration-pending', 'contract_version': 1, 'guidance': 'docs/deployment/README.md',
            'components': [
                {'name': 'opencode.nvim', 'repository': 'git@github.com:krafczyk/opencode.nvim', 'commit': '0' * 40},
                {'name': 'opencode-project-reload', 'repository': 'git@github.com:krafczyk/opencode-project-reload', 'commit': '0' * 40},
                {'name': 'compound-engineering', 'repository': 'git@github.com:krafczyk/compound-engineering-plugin', 'commit': '0' * 40},
            ],
        },
        'failed_step': None if child_exit == 0 else 'fixture-action',
        'diagnostic': 'child completed' if child_exit == 0 else 'fixture child failed',
        'log_path': log_path if mode != 'wrong-log' else '/tmp/wrong.log',
    }
    result_path.write_text(json.dumps(document), encoding='utf-8')
if mode == 'stale':
    pathlib.Path(os.environ['XDG_STATE_HOME']).joinpath('mkchad/deploy/latest-result.json').write_text('{"stale": true}', encoding='utf-8')
raise SystemExit(child_exit)
""",
                encoding="utf-8",
            )
        else:
            (source / "README").write_text("fixture release\n", encoding="utf-8")
        subprocess.run(["git", "init", "-q", str(source)], check=True)
        subprocess.run(["git", "-C", str(source), "add", "."], check=True)
        subprocess.run(["git", "-C", str(source), "-c", "user.name=test", "-c", "user.email=test@example.invalid", "commit", "-q", "-m", "release"], check=True)
        commit = subprocess.check_output(["git", "-C", str(source), "rev-parse", "HEAD"], text=True).strip()
        tag_command = ["git", "-C", str(source), "tag", tag]
        if annotated:
            tag_command = ["git", "-C", str(source), "-c", "user.name=test", "-c", "user.email=test@example.invalid", "tag", "-a", "-m", "release", tag]
        subprocess.run(tag_command, check=True)
        subprocess.run(["git", "clone", "-q", "--bare", str(source), str(remote)], check=True)
        return source, remote, commit

    def invoke(self, remote, *, release="v1", extra=(), child_mode="success", child_exit=0, capture=None):
        """Run the launcher against a fixture remote and capture terminal channels."""
        stdout = io.StringIO()
        stderr = io.StringIO()
        environment = {
            "HOME": str(self.home),
            "XDG_STATE_HOME": str(self.state),
            "TMPDIR": str(self.temp),
            "MKCHAD_RELEASE_CHILD_MODE": child_mode,
            "MKCHAD_RELEASE_CHILD_EXIT": str(child_exit),
        }
        if capture:
            environment["MKCHAD_RELEASE_CAPTURE"] = str(capture)
        with mock.patch.dict(os.environ, environment, clear=False), mock.patch.object(self.launcher, "DEFAULT_REMOTE", str(remote)), contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            result = self.launcher.main(["--release", release, "--image", str(self.image), *extra])
        return result, stdout.getvalue(), stderr.getvalue()

    @property
    def deploy_root(self):
        """Return the fixture diagnostic root after an admitted invocation."""
        return self.state / "mkchad" / "deploy"

    def latest_result(self):
        """Read the latest terminal result emitted by the launcher."""
        return json.loads((self.deploy_root / "latest-result.json").read_text(encoding="utf-8"))

    def test_annotated_and_lightweight_tags_resolve_to_the_exact_commit(self):
        for annotated in (True, False):
            with self.subTest(annotated=annotated):
                _, remote, commit = self.remote_with_release(annotated=annotated)
                capture = self.root / f"capture-{annotated}.json"
                status, _, _ = self.invoke(remote, capture=capture)
                self.assertEqual(status, 0)
                received = json.loads(capture.read_text(encoding="utf-8"))
                self.assertEqual(received["release"], "v1")
                self.assertEqual(subprocess.check_output(["git", "-C", str(remote), "rev-parse", "v1^{commit}"], text=True).strip(), commit)

    def test_malformed_missing_and_non_commit_tags_fail_without_branch_fallback(self):
        source, remote, _ = self.remote_with_release()
        status, _, _ = self.invoke(remote, release="../main")
        self.assertEqual(status, 2)
        self.assertFalse(self.deploy_root.exists())
        status, _, _ = self.invoke(remote, release="missing")
        self.assertEqual(status, 2)
        subprocess.run(["git", "-C", str(source), "branch", "branch-only"], check=True)
        subprocess.run(["git", "-C", str(remote), "fetch", "-q", str(source), "refs/heads/branch-only:refs/heads/branch-only"], check=True)
        status, _, _ = self.invoke(remote, release="branch-only")
        self.assertEqual(status, 2)
        tree = subprocess.check_output(["git", "-C", str(source), "write-tree"], text=True).strip()
        subprocess.run(["git", "-C", str(source), "tag", "tree-release", tree], check=True)
        subprocess.run(["git", "-C", str(remote), "fetch", "-q", str(source), "refs/tags/tree-release:refs/tags/tree-release"], check=True)
        status, _, _ = self.invoke(remote, release="tree-release")
        self.assertEqual(status, 2)

    def test_fetches_once_checks_out_without_submodules_and_uses_release_deployer(self):
        _, remote, _ = self.remote_with_release()
        binary = self.root / "bin"
        binary.mkdir()
        log = self.root / "git-commands.log"
        git = binary / "git"
        git.write_text(f"#!/bin/sh\nprintf '%s\\n' \"$*\" >> {log!s}\nexec {shutil.which('git')} \"$@\"\n", encoding="utf-8")
        git.chmod(0o755)
        capture = self.root / "capture.json"
        with mock.patch.dict(os.environ, {"PATH": str(binary) + os.pathsep + os.environ["PATH"]}, clear=False):
            status, _, _ = self.invoke(remote, capture=capture)
        self.assertEqual(status, 0)
        commands = log.read_text(encoding="utf-8").splitlines()
        self.assertEqual(sum("fetch" in command.split() for command in commands), 1)
        self.assertTrue(any("checkout --detach --no-recurse-submodules" in command for command in commands))
        received = json.loads(capture.read_text(encoding="utf-8"))
        self.assertIn("--run-id", received["argv"])
        self.assertIn("--result-path", received["argv"])
        self.assertIn(received["run_id"], pathlib.Path(received["result_path"]).name)

    def test_replace_partial_is_forwarded_to_the_release_deployer(self):
        _, remote, _ = self.remote_with_release()
        capture = self.root / "replace-partial.json"

        status, _, _ = self.invoke(remote, extra=("--replace-partial",), capture=capture)

        self.assertEqual(status, 0)
        self.assertIn("--replace-partial", json.loads(capture.read_text(encoding="utf-8"))["argv"])

    def test_compiler_override_is_forwarded_to_the_release_deployer(self):
        """Preserve the exact static compiler selection across release handoff."""
        _, remote, _ = self.remote_with_release()
        capture = self.root / "compiler-override.json"
        compiler = "/opt/toolchains/bin/musl-gcc"

        with mock.patch.dict(os.environ, {"CONTAINER_TOOLS_C_COMPILER": compiler}, clear=False):
            status, _, _ = self.invoke(remote, capture=capture)

        self.assertEqual(status, 0)
        self.assertEqual(json.loads(capture.read_text(encoding="utf-8"))["container_tools_c_compiler"], compiler)

    def test_only_public_options_are_accepted(self):
        _, remote, _ = self.remote_with_release()
        for extra in (("--remote", "elsewhere"), ("--expected-parent-commit", "a" * 40), ("recover",)):
            with self.subTest(extra=extra), mock.patch.object(self.launcher, "DEFAULT_REMOTE", str(remote)), self.assertRaises(SystemExit) as raised:
                self.launcher.main(["--release", "v1", "--image", str(self.image), *extra])
            self.assertEqual(raised.exception.code, 2)
        self.assertFalse(self.deploy_root.exists())

    def test_paths_with_spaces_and_terminal_channels_are_preserved_in_the_log(self):
        _, remote, _ = self.remote_with_release()
        capture = self.root / "capture path.json"
        status, stdout, stderr = self.invoke(remote, capture=capture)
        self.assertEqual(status, 0)
        received = json.loads(capture.read_text(encoding="utf-8"))
        self.assertEqual(received["image"], str(self.image))
        self.assertIn("child standard output", stdout)
        self.assertIn("child standard error", stderr)
        log = pathlib.Path(self.latest_result()["log_path"])
        contents = log.read_text(encoding="utf-8")
        self.assertIn("child standard output", contents)
        self.assertIn("child standard error", contents)

    def test_four_invocations_retain_the_current_log_and_two_prior_logs(self):
        _, remote, _ = self.remote_with_release()
        for _ in range(4):
            self.assertEqual(self.invoke(remote)[0], 0)
        logs = list((self.deploy_root / "logs").glob("*.log"))
        self.assertEqual(len(logs), 3)
        self.assertIn(pathlib.Path(self.latest_result()["log_path"]), logs)

    def test_prehandoff_failure_publishes_an_atomic_failed_result(self):
        _, remote, _ = self.remote_with_release(deployer=False)
        status, _, _ = self.invoke(remote)
        self.assertEqual(status, 2)
        result = self.latest_result()
        self.assertEqual(self.validate_result(result), result)
        self.assertEqual(result["outcome"], "failed")
        self.assertEqual(result["phase"], "preflight")
        self.assertIn("deployer", result["diagnostic"])
        self.assertFalse(list(self.deploy_root.glob(".latest-result-*")))

    def test_child_result_rejects_removed_package_fields(self):
        """Keep launcher-side result validation aligned with the reduced result."""
        _, remote, _ = self.remote_with_release()
        self.assertEqual(self.invoke(remote)[0], 0)
        result = self.latest_result()
        result["packages"] = {"expected": [], "observed": []}
        self.assertFalse(self.launcher._valid_result_document(result))

    def test_child_status_and_missing_or_mismatched_results_use_the_right_terminal_result(self):
        _, remote, _ = self.remote_with_release()
        self.assertEqual(self.invoke(remote, child_exit=7)[0], 7)
        self.assertEqual(self.latest_result()["outcome"], "failed")
        self.assertEqual(self.latest_result()["diagnostic"], "fixture child failed")
        self.assertEqual(self.invoke(remote, child_mode="missing", child_exit=9)[0], 9)
        self.assertIn("release deployer result: missing", self.latest_result()["diagnostic"])
        self.assertNotIn("child standard error", self.latest_result()["diagnostic"])
        self.assertEqual(self.invoke(remote, child_mode="wrong-log")[0], 2)
        self.assertIn("log-binding-mismatch", self.latest_result()["diagnostic"])
        self.assertEqual(self.invoke(remote, child_mode="wrong-run")[0], 2)
        self.assertIn("run-binding-mismatch", self.latest_result()["diagnostic"])
        self.assertEqual(self.invoke(remote, child_mode="malformed")[0], 2)
        self.assertIn("schema-invalid", self.latest_result()["diagnostic"])
        self.assertEqual(self.invoke(remote, child_mode="stale")[0], 2)
        self.assertIn("missing", self.latest_result()["diagnostic"])

    def test_interruption_promotes_the_run_bound_interrupted_result(self):
        checkout = self.root / "interrupted-checkout"
        deployer = checkout / "deploy" / "mkchad_deploy.py"
        deployer.parent.mkdir(parents=True)
        deployer.write_text("pass\n", encoding="ascii")

        def interrupt(command, **_kwargs):
            def value(option):
                return command[command.index(option) + 1]

            result = self.launcher._fallback_result(
                value("--run-id"),
                value("--release"),
                "2026-08-02T00:00:00Z",
                pathlib.Path(value("--log-path")),
                "deployment interrupted",
            )
            result["outcome"] = "interrupted"
            pathlib.Path(value("--result-path")).write_text(json.dumps(result), encoding="utf-8")
            raise self.launcher.ReleaseInterrupted

        with mock.patch.dict(os.environ, {"HOME": str(self.home), "XDG_STATE_HOME": str(self.state)}, clear=False), mock.patch.object(
            self.launcher, "_checkout_release", return_value=(checkout, deployer)
        ), mock.patch.object(self.launcher, "_run", side_effect=interrupt):
            self.assertEqual(self.launcher.main(["--release", "v1", "--image", str(self.image)]), 2)
        self.assertEqual(self.latest_result()["outcome"], "interrupted")

    def test_contention_does_not_rotate_logs_or_replace_the_result(self):
        import fcntl

        _, remote, _ = self.remote_with_release()
        logs = self.deploy_root / "logs"
        logs.mkdir(parents=True)
        old_log = logs / "old.log"
        old_log.write_text("old\n", encoding="utf-8")
        old_result = self.deploy_root / "latest-result.json"
        old_result.write_text('{"previous": true}\n', encoding="utf-8")
        lock = self.deploy_root / "invocation.lock"
        handle = lock.open("w")
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            status, _, stderr = self.invoke(remote)
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)
            handle.close()
        self.assertEqual(status, 2)
        self.assertIn("another invocation", stderr)
        self.assertEqual(list(logs.glob("*.log")), [old_log])
        self.assertEqual(old_result.read_text(encoding="utf-8"), '{"previous": true}\n')

    def test_temporary_checkout_is_removed_after_completion(self):
        _, remote, _ = self.remote_with_release()
        self.assertEqual(self.invoke(remote)[0], 0)
        self.assertFalse(list(self.temp.glob("mkchad-release-*")))

    def test_log_rotation_failure_is_controlled_without_replacing_a_prior_result(self):
        _, remote, _ = self.remote_with_release()
        self.deploy_root.mkdir(parents=True)
        prior = self.deploy_root / "latest-result.json"
        prior.write_text('{"previous":true}\n', encoding="utf-8")

        with mock.patch.object(self.launcher, "_rotate_logs", side_effect=OSError("TOKEN=secret")):
            status, _stdout, stderr = self.invoke(remote)

        self.assertEqual(status, 2)
        self.assertIn("mkchad-release: diagnostic state: filesystem", stderr)
        self.assertNotIn("inspect log:", stderr)
        self.assertNotIn("secret", stderr)
        self.assertNotIn("Traceback", stderr)
        self.assertEqual(prior.read_text(encoding="utf-8"), '{"previous":true}\n')


class ReleaseLoggingTests(unittest.TestCase):
    """Exercise launcher logging behavior shared with release-owned deployment output."""

    def test_streamed_output_is_redacted_before_terminal_and_log_writes(self):
        launcher = load_launcher()
        terminal = io.StringIO()
        log = io.StringIO()
        launcher._write_stream(terminal, log, b"API_TOKEN=DO-NOT-PUBLISH child output\n")
        self.assertEqual(terminal.getvalue(), "API_TOKEN=<redacted> child output\n")
        self.assertEqual(log.getvalue(), terminal.getvalue())

    def test_actionable_diagnostic_is_redacted_and_points_to_the_retained_log(self):
        launcher = load_launcher()

        diagnostic = launcher._diagnostic(
            "release acquisition", "git-operation", "check release access and retry",
            pathlib.Path("/tmp/mkchad-v1/run.log"),
        )

        self.assertEqual(
            diagnostic,
            "release acquisition: git-operation: check release access and retry; "
            "inspect log: /tmp/mkchad-v1/run.log",
        )
        self.assertNotIn("token=secret", launcher._diagnostic("x", "token=secret", "retry"))

        bounded = launcher._diagnostic(
            "release acquisition", "git-operation", "x" * 5000,
            pathlib.Path("/tmp/mkchad-v1/retained.log"),
        )
        self.assertEqual(len(bounded), 4096)
        self.assertTrue(bounded.endswith("; inspect log: /tmp/mkchad-v1/retained.log"))

    def test_child_result_issue_does_not_expose_child_document_contents(self):
        launcher = load_launcher()
        with tempfile.TemporaryDirectory(prefix="child-result-", dir="/tmp/mkchad-v1") as temporary:
            result = pathlib.Path(temporary) / "result.json"
            result.write_text('{"token":"secret"}', encoding="utf-8")

            issue = launcher._child_result_issue(
                result,
                run_id="123e4567-e89b-12d3-a456-426614174000",
                release="v1",
                log_path=pathlib.Path("/tmp/mkchad-v1/run.log"),
                child_status=0,
            )

        self.assertEqual(issue, "schema-invalid")

    def test_rejected_child_diagnostic_contains_no_subprocess_output(self):
        launcher = load_launcher()

        diagnostic = launcher._diagnostic(
            "release deployer result", "schema-invalid",
            "repair the release deployer result publication and rerun",
            pathlib.Path("/tmp/mkchad-v1/run.log"),
        )

        self.assertEqual(
            diagnostic,
            "release deployer result: schema-invalid: repair the release deployer result publication and rerun; "
            "inspect log: /tmp/mkchad-v1/run.log",
        )
        self.assertNotIn("child standard error", diagnostic)

    def test_promoted_child_result_is_redacted_without_changing_its_contract(self):
        launcher = load_launcher()
        with tempfile.TemporaryDirectory(prefix="child-promotion-", dir="/tmp/mkchad-v1") as temporary:
            root = pathlib.Path(temporary)
            source = root / "source.json"
            destination = root / "latest-result.json"
            log_path = root / "run.log"
            document = launcher._fallback_result(
                "123e4567-e89b-12d3-a456-426614174000",
                "v1",
                "2026-08-11T00:00:00Z",
                log_path,
                "API_TOKEN=secret deployment failed",
            )
            source.write_text(json.dumps(document), encoding="utf-8")

            launcher._promote_child_result(source, destination, document)

            promoted = json.loads(destination.read_text(encoding="utf-8"))
        self.assertTrue(launcher._valid_result_document(promoted))
        self.assertEqual(promoted["diagnostic"], "API_TOKEN=<redacted> deployment failed")


if __name__ == "__main__":
    unittest.main()
