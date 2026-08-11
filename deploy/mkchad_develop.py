#!/usr/bin/env python3
"""Deploy clean committed child checkouts from this MkChad workspace."""

from __future__ import annotations

import argparse
import contextlib
import datetime
import fcntl
import json
import os
import pathlib
import shutil
import stat
import sys
import tempfile
import uuid

import mkchad_deploy as deploy
import mkchad_release as release


BUILD_ROOT = pathlib.Path("/tmp/mkchad-v1/container-tools-runtime-boundary")
BUILD_TIMEOUT = 300


class _StreamLog:
    """Send direct deployer output through the release redaction and log writer."""

    def __init__(self, stream, log):
        self.stream = stream
        self.log = log

    def write(self, text: str) -> int:
        """Write one text chunk to its terminal stream and the current run log."""
        if text:
            release._write_stream(self.stream, self.log, text.encode("utf-8", errors="replace"))
        return len(text)

    def flush(self):
        """Flush the terminal stream and persistent run log."""
        self.stream.flush()
        self.log.flush()


def _diagnostic(operation: str, category: str, action: str, log_path: pathlib.Path | None = None) -> str:
    """Render one bounded developer diagnostic using the shared redaction policy."""
    return release._diagnostic(operation, category, action, log_path)


def _develop_failure_diagnostic(error: BaseException, log_path: pathlib.Path) -> str:
    """Classify local build and deployment errors without publishing exception text."""
    if isinstance(error, deploy.DeploymentError) and error.category is not None and error.action is not None:
        return deploy._deployment_diagnostic("developer deployment", error, log_path)
    text = str(error)
    if "CMake" in text:
        category = "cmake-install"
    elif "installed container-tools" in text:
        category = "installed-version"
    elif "developer build root" in text:
        category = "build-root"
    elif "another developer build" in text:
        category = "build-contention"
    elif "cleanup" in text:
        category = "command-cleanup"
    elif "timed out" in text:
        category = "command-timeout"
    elif "output exceeded" in text:
        category = "command-output-limit"
    elif "source" in text or "child" in text or "workspace" in text:
        category = "source-admission"
    else:
        category = "deployment-failure"
    return _diagnostic("developer deployment", category, "repair the reported prerequisite and rerun", log_path)


def _fallback_result(workspace: pathlib.Path, run_id: str, started_at: str, log_path: pathlib.Path, diagnostic: str, image: pathlib.Path | None) -> dict:
    """Build a bounded developer failure result before source admission completes."""
    return {
        "mode": "developer", "run_id": run_id,
        "workspace": {"path": str(workspace), "revision": None, "dirty": None},
        "started_at": started_at, "completed_at": release._timestamp(), "outcome": "failed", "phase": "preflight",
        "components": [], "artifact": {"architecture": os.uname().machine, "requested_sha256": None, "observed_sha256": None},
        "image": {"choice": "omitted" if image is None else "install", "supplied_sha256": None, "active_sha256": None, "decision": "preserved" if image is None else "unavailable"},
        "actions": [], "failed_step": "developer-admission", "diagnostic": release._redact(diagnostic)[:4096], "log_path": str(log_path),
    }


def _load_result(path: pathlib.Path, run_id: str, log_path: pathlib.Path, status: int) -> tuple[dict | None, str | None]:
    """Read one developer result and return its document or safe rejection category."""
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None, "missing"
    except (OSError, json.JSONDecodeError):
        return None, "unreadable"
    if not isinstance(document, dict) or document.get("mode") != "developer":
        return None, "schema-invalid"
    if document.get("run_id") != run_id:
        return None, "run-binding-mismatch"
    if document.get("log_path") != str(log_path):
        return None, "log-binding-mismatch"
    if (status == 0) != (document.get("outcome") == "succeeded"):
        return None, "status-outcome-mismatch"
    return document, None


def _child_result_issue(path: pathlib.Path, run_id: str, log_path: pathlib.Path, status: int) -> str | None:
    """Return a safe rejection category for a direct deployer result."""
    return _load_result(path, run_id, log_path, status)[1]


def _matching_result(path: pathlib.Path, run_id: str, log_path: pathlib.Path, status: int) -> bool:
    """Return whether the direct deployer published this invocation's result."""
    return _load_result(path, run_id, log_path, status)[1] is None


def _write_build_output(stream, log, text: str) -> None:
    """Write one build output channel through redaction exactly once."""
    if not text:
        return
    if log is None:
        stream.write(text)
        return
    target = stream.stream if isinstance(stream, _StreamLog) else stream
    release._write_stream(target, log, text.encode("utf-8", errors="replace"))


def _install_host_tools(source: pathlib.Path, source_commit: str, temporary: pathlib.Path, log=None) -> None:
    """Build and install the admitted container-tools source with CMake.

    Args:
        source: Clean container-tools Git top-level selected by the caller.
        source_commit: Exact admitted container-tools commit.
        temporary: Invocation-unique directory below :data:`BUILD_ROOT`.
        log: Optional open invocation log receiving redacted build output.

    Returns:
        ``None`` after the compiled executable reports the selected commit.

    Raises:
        deploy.DeploymentError: If CMake fails or the installed executable does
            not report the selected source commit.
    """
    build = temporary / "build"
    prefix = pathlib.Path(os.environ.get("HOME", "~")).expanduser() / ".local"
    architecture, commands = deploy._container_tools_build_commands(source, build, prefix)
    for phase, command in zip(("configure", "build", "install"), commands, strict=True):
        completed = deploy._capture(command, timeout=BUILD_TIMEOUT)
        _write_build_output(sys.stdout, log, completed.stdout)
        _write_build_output(sys.stderr, log, completed.stderr)
        if completed.returncode:
            raise deploy.DeploymentError(
                f"container-tools CMake {phase} failed",
                category=f"cmake-{phase}",
                action=f"inspect the retained CMake output, repair the {phase} prerequisites after status {completed.returncode}, and rerun",
            )
    completed = deploy._capture([str(prefix / "bin" / "container-tools"), "--version", "--json"], timeout=BUILD_TIMEOUT)
    if completed.returncode:
        raise deploy.DeploymentError(
            "installed container-tools version check failed",
            category="installed-version",
            action=f"repair the installed container-tools executable after status {completed.returncode}, then rerun",
        )
    deploy._container_tools_identity(completed.stdout, source_commit, architecture)


@contextlib.contextmanager
def _build_root_lock():
    """Hold the fixed build-root interlock for staging, building, and deployment.

    Raises:
        deploy.DeploymentError: If the trusted build root is not a directory, its
            lock is not regular, or another developer build owns the interlock.
    """
    BUILD_ROOT.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        if not stat.S_ISDIR(os.lstat(BUILD_ROOT).st_mode):
            raise OSError
        descriptor = os.open(
            BUILD_ROOT / "build.lock",
            os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
    except OSError as exc:
        raise deploy.DeploymentError("developer build root is unavailable") from exc
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise deploy.DeploymentError("developer build root lock is unsafe")
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "a+") as handle:
            descriptor = -1
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise deploy.DeploymentError("another developer build is already running") from exc
            yield
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _discard_build_run(path: pathlib.Path):
    """Best-effort discard one command-owned build directory."""
    with contextlib.suppress(OSError, KeyboardInterrupt):
        shutil.rmtree(path)


def _reclaim_stale_build_runs():
    """Best-effort remove stale command-owned runs while build-root locked."""
    for candidate in BUILD_ROOT.glob("run-*"):
        if not candidate.is_symlink() and candidate.is_dir():
            _discard_build_run(candidate)


def main(argv: list[str] | None = None) -> int:
    """Run one local developer deployment under the shared invocation lock.

    The command accepts no release selector. It validates the current parent
    workspace, stages local child commits through the deployer, and atomically
    promotes the direct developer result to the shared latest-result path.
    """
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0], allow_abbrev=False)
    parser.add_argument("--image", type=pathlib.Path, required=True)
    parser.add_argument("--replace-partial", action="store_true")
    args = parser.parse_args(argv)
    retained_log = None
    try:
        state_root = release._state_root()
        workspace = pathlib.Path.cwd().resolve()
        with release._invocation_lock(state_root):
            logs = state_root / "logs"
            release._rotate_logs(logs)
            run_id = str(uuid.uuid4())
            started_at = release._timestamp()
            log_path = logs / f"run-{datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')}-{run_id}.log"
            result_path = state_root / f".result-{run_id}.json"
            latest_result = state_root / "latest-result.json"
            with log_path.open("x", encoding="utf-8") as log, contextlib.redirect_stdout(_StreamLog(sys.stdout, log)), contextlib.redirect_stderr(_StreamLog(sys.stderr, log)):
                retained_log = log_path
                try:
                    with _build_root_lock():
                        _reclaim_stale_build_runs()
                        temporary = pathlib.Path(tempfile.mkdtemp(prefix=f"run-{run_id}-", dir=BUILD_ROOT))
                        try:
                            status = deploy.execute_developer_deployment(
                                workspace,
                                args.image.absolute(),
                                installer=lambda staged_source, source_commit, _staging: _install_host_tools(staged_source, source_commit, temporary, log),
                                staging_root=temporary,
                                log_path=log_path,
                                result_path=result_path,
                                run_id=run_id,
                                replace_partial=args.replace_partial,
                            )
                            document, issue = _load_result(result_path, run_id, log_path, status)
                            if issue is None:
                                assert document is not None
                                release._promote_child_result(result_path, latest_result, document)
                                return status
                            diagnostic = _diagnostic("developer deployer result", issue or "missing", "repair result publication and rerun", log_path)
                            release._publish_result(latest_result, _fallback_result(workspace, run_id, started_at, log_path, diagnostic, args.image))
                            return status if status else 2
                        except (deploy.DeploymentError, OSError) as exc:
                            diagnostic = _develop_failure_diagnostic(exc, log_path)
                            _write_build_output(sys.stderr, log, "mkchad-develop: " + diagnostic + "\n")
                            release._publish_result(latest_result, _fallback_result(workspace, run_id, started_at, log_path, diagnostic, args.image))
                            return 2
                        except KeyboardInterrupt:
                            result = _fallback_result(workspace, run_id, started_at, log_path, _diagnostic("developer deployment", "interrupted", "inspect the retained log, repair any partial state, and rerun", log_path), args.image)
                            result["outcome"] = "interrupted"
                            release._publish_result(latest_result, result)
                            return 2
                        finally:
                            # Publish the terminal result before cleanup. _discard_build_run
                            # suppresses cleanup failures and interruption, preserving it.
                            _discard_build_run(temporary)
                except (deploy.DeploymentError, OSError) as exc:
                    diagnostic = _develop_failure_diagnostic(exc, log_path)
                    _write_build_output(sys.stderr, log, "mkchad-develop: " + diagnostic + "\n")
                    release._publish_result(latest_result, _fallback_result(workspace, run_id, started_at, log_path, diagnostic, args.image))
                    return 2
                except KeyboardInterrupt:
                    result = _fallback_result(workspace, run_id, started_at, log_path, _diagnostic("developer deployment", "interrupted", "inspect the retained log, repair any partial state, and rerun", log_path), args.image)
                    result["outcome"] = "interrupted"
                    release._publish_result(latest_result, result)
                    return 2
                finally:
                    result_path.unlink(missing_ok=True)
    except release.InvocationBusy:
        print("mkchad-develop: " + _diagnostic("invocation", "contention", "another invocation is active; wait for it to finish and retry"), file=sys.stderr)
        return 2
    except (deploy.DeploymentError, OSError):
        print("mkchad-develop: " + _diagnostic("diagnostic state", "filesystem", "repair the deployment state directory and retry", retained_log), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
