#!/usr/bin/env python3
"""Fetch one MkChad release tag and synchronously run its deployer."""

from __future__ import annotations

import argparse
import contextlib
import datetime
import fcntl
import json
import os
import pathlib
import re
import selectors
import shutil
import subprocess
import sys
import tempfile
import time
import uuid


DEFAULT_REMOTE = "git@github.com:krafczyk/mkchad_dev.git"
TAG = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]*$")
RESULT_FIELDS = {
    "schema",
    "run_id",
    "release",
    "started_at",
    "completed_at",
    "outcome",
    "phase",
    "components",
    "artifact",
    "actions",
    "configuration_handoff",
    "failed_step",
    "diagnostic",
    "log_path",
}
REDACT_ASSIGNMENT = re.compile(r"(?i)\b(password|token|secret|api[_-]?key)=[^\s]+")
COMMAND_TIMEOUT = 300


class ReleaseError(RuntimeError):
    """Raised when release acquisition or synchronous handoff cannot complete."""


class InvocationBusy(RuntimeError):
    """Raised when another public release invocation owns the diagnostic lock."""


def _timestamp() -> str:
    """Return the current UTC timestamp in the result-schema representation."""
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _validate_tag(tag: str):
    """Reject tag syntax that could select a revision other than one exact tag."""
    if not TAG.fullmatch(tag) or tag.startswith("-") or ".." in tag or tag.endswith("/"):
        raise ReleaseError("invalid release tag")


def _state_root() -> pathlib.Path:
    """Return the owner-controlled XDG diagnostic directory for release invocations."""
    home = pathlib.Path(os.environ.get("HOME", "~")).expanduser()
    root = pathlib.Path(os.environ.get("XDG_STATE_HOME", home / ".local/state"))
    return (root / "mkchad" / "deploy").absolute()


@contextlib.contextmanager
def _invocation_lock(state_root: pathlib.Path):
    """Hold the nonblocking public-invocation lock for one admitted run."""
    state_root.mkdir(parents=True, exist_ok=True)
    handle = (state_root / "invocation.lock").open("a+")
    try:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise InvocationBusy from exc
        yield
    finally:
        with contextlib.suppress(OSError):
            fcntl.flock(handle, fcntl.LOCK_UN)
        handle.close()


def _redact(text: str) -> str:
    """Remove recognizable credential assignments before terminal or log output."""
    return REDACT_ASSIGNMENT.sub(lambda match: match.group(1) + "=<redacted>", text)


def _write_stream(stream, log, data: bytes):
    """Write one filtered subprocess chunk to its terminal channel and the run log."""
    text = _redact(data.decode("utf-8", errors="replace"))
    stream.write(text)
    stream.flush()
    log.write(text)
    log.flush()


def _run(command: list[str], *, cwd: pathlib.Path, log, timeout: float = COMMAND_TIMEOUT) -> int:
    """Run one bounded command while preserving stdout/stderr channels in the log."""
    try:
        process = subprocess.Popen(
            command,
            cwd=cwd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            close_fds=True,
        )
    except OSError as exc:
        raise ReleaseError(f"cannot start {command[0]}: {exc}") from exc
    assert process.stdout is not None and process.stderr is not None
    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ, sys.stdout)
    selector.register(process.stderr, selectors.EVENT_READ, sys.stderr)
    deadline = time.monotonic() + timeout
    try:
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                process.kill()
                process.wait()
                raise ReleaseError(f"command timed out after {timeout:g} seconds: {command[0]}")
            for key, _ in selector.select(min(0.1, remaining)):
                data = os.read(key.fileobj.fileno(), 65536)
                if not data:
                    selector.unregister(key.fileobj)
                    continue
                _write_stream(key.data, log, data)
        return process.wait(timeout=max(0.1, deadline - time.monotonic()))
    except subprocess.TimeoutExpired as exc:
        process.kill()
        process.wait()
        raise ReleaseError(f"command timed out after {timeout:g} seconds: {command[0]}") from exc
    finally:
        selector.close()
        process.stdout.close()
        process.stderr.close()


def _git(arguments: list[str], *, cwd: pathlib.Path, log, check: bool = True) -> str:
    """Run Git and return its stdout-equivalent only for exact revision resolution."""
    # Git output is streamed by _run; capture revision resolution separately.
    command = ["git", *arguments]
    if arguments and arguments[0] == "rev-parse":
        try:
            completed = subprocess.run(command, cwd=cwd, stdin=subprocess.DEVNULL, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=COMMAND_TIMEOUT, check=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ReleaseError(f"cannot resolve release commit: {exc}") from exc
        _write_stream(sys.stdout, log, completed.stdout.encode())
        _write_stream(sys.stderr, log, completed.stderr.encode())
        if check and completed.returncode:
            raise ReleaseError("release tag does not resolve to a commit")
        return completed.stdout.strip()
    status = _run(command, cwd=cwd, log=log)
    if check and status:
        raise ReleaseError(f"git command failed with exit {status}")
    return ""


def _rotate_logs(logs: pathlib.Path):
    """Retain the two newest completed logs before creating the current run log."""
    logs.mkdir(parents=True, exist_ok=True)
    existing = sorted(logs.glob("*.log"), key=lambda path: (path.stat().st_mtime_ns, path.name), reverse=True)
    for path in existing[2:]:
        path.unlink()


def _fallback_result(run_id: str, release: str, started_at: str, log_path: pathlib.Path, diagnostic: str) -> dict:
    """Build a schema-1 failed result for acquisition or handoff failures."""
    components = [
        ("msk_containers", "deployed"),
        ("container_tools", "deployed"),
        ("mkchad", "deployed"),
        ("opencode", "embedded"),
        ("opencode.nvim", "configuration-owned"),
        ("opencode-project-reload", "configuration-owned"),
        ("compound-engineering", "configuration-owned"),
        ("opencode_sprint_loop", "disabled"),
    ]
    return {
        "schema": 1,
        "run_id": run_id,
        "release": release,
        "started_at": started_at,
        "completed_at": _timestamp(),
        "outcome": "failed",
        "phase": "preflight",
        "components": [{"name": name, "disposition": disposition, "requested_commit": "0" * 40, "observed_commit": None} for name, disposition in components],
        "artifact": {"architecture": "x86_64", "expected_sha256": "0" * 64, "observed_sha256": None},
        "actions": [],
        "configuration_handoff": {
            "state": "configuration-pending",
            "contract_version": 1,
            "guidance": "docs/deployment/README.md",
            "components": [
                {"name": "opencode.nvim", "repository": "git@github.com:krafczyk/opencode.nvim", "commit": "0" * 40},
                {"name": "opencode-project-reload", "repository": "git@github.com:krafczyk/opencode-project-reload", "commit": "0" * 40},
                {"name": "compound-engineering", "repository": "git@github.com:krafczyk/compound-engineering-plugin", "commit": "0" * 40},
            ],
        },
        "failed_step": "release-launcher",
        "diagnostic": diagnostic[:4096],
        "log_path": str(log_path),
    }


def _publish_result(path: pathlib.Path, document: dict):
    """Atomically replace the latest completed result with one schema-1 document."""
    temporary = path.with_name(f".{path.name}-{uuid.uuid4().hex}.tmp")
    temporary.write_text(json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def _matching_child_result(path: pathlib.Path, *, run_id: str, release: str, log_path: pathlib.Path) -> bool:
    """Return whether the child result is bound to this admitted run and log."""
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return (
        isinstance(document, dict)
        and set(document) == RESULT_FIELDS
        and document.get("schema") == 1
        and document.get("run_id") == run_id
        and document.get("release") == release
        and document.get("log_path") == str(log_path)
    )


def _checkout_release(release: str, log) -> tuple[pathlib.Path, pathlib.Path]:
    """Fetch one exact tag, peel it to one commit, and return its detached deployer."""
    _validate_tag(release)
    root = pathlib.Path(tempfile.mkdtemp(prefix="mkchad-release-"))
    checkout = root / "release"
    try:
        _git(["init", str(checkout)], cwd=root, log=log)
        reference = "refs/mkchad-release/tag"
        _git(["fetch", "--no-tags", DEFAULT_REMOTE, f"refs/tags/{release}:{reference}"], cwd=checkout, log=log)
        commit = _git(["rev-parse", f"{reference}^{{commit}}"], cwd=checkout, log=log)
        if not re.fullmatch(r"[0-9a-f]{40}", commit):
            raise ReleaseError("release tag does not resolve to a commit")
        _git(["checkout", "--detach", "--no-recurse-submodules", commit], cwd=checkout, log=log)
        deployer = checkout / "deploy" / "mkchad_deploy.py"
        if not deployer.is_file():
            raise ReleaseError("release checkout has no deployer")
        return root, deployer
    except BaseException:
        shutil.rmtree(root, ignore_errors=True)
        raise


def main(argv: list[str] | None = None) -> int:
    """Run the public two-argument release launcher.

    Args:
        argv: Optional public CLI arguments; process arguments are used when omitted.

    Returns:
        The synchronous child status, or ``2`` for launcher acquisition or handoff
        failures and invocation contention.

    Side effects:
        Acquires the public invocation lock, writes one run log and terminal result,
        fetches the selected release into temporary storage, and removes that storage
        after the release-owned deployer exits.
    """
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0], allow_abbrev=False)
    parser.add_argument("--release", required=True)
    parser.add_argument("--image", type=pathlib.Path, required=True)
    args = parser.parse_args(argv)
    state_root = _state_root()
    try:
        with _invocation_lock(state_root):
            logs = state_root / "logs"
            _rotate_logs(logs)
            run_id = str(uuid.uuid4())
            log_path = logs / f"run-{datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')}-{run_id}.log"
            result_path = state_root / f".result-{run_id}.json"
            latest_result = state_root / "latest-result.json"
            started_at = _timestamp()
            checkout_root = None
            with log_path.open("x", encoding="utf-8") as log:
                try:
                    checkout_root, deployer = _checkout_release(args.release, log)
                    child_status = _run(
                        [
                            sys.executable,
                            str(deployer),
                            "deploy",
                            "--release",
                            args.release,
                            "--image",
                            str(args.image.absolute()),
                            "--log-path",
                            str(log_path),
                            "--result-path",
                            str(result_path),
                            "--run-id",
                            run_id,
                        ],
                        cwd=deployer.parent.parent,
                        log=log,
                    )
                    if _matching_child_result(result_path, run_id=run_id, release=args.release, log_path=log_path):
                        os.replace(result_path, latest_result)
                        return child_status
                    diagnostic = "release deployer result does not match the admitted run" if result_path.exists() else "release deployer did not publish a matching terminal result"
                    _publish_result(latest_result, _fallback_result(run_id, args.release, started_at, log_path, diagnostic))
                    return child_status if child_status else 2
                except ReleaseError as exc:
                    _write_stream(sys.stderr, log, ("mkchad-release: " + str(exc) + "\n").encode())
                    _publish_result(latest_result, _fallback_result(run_id, args.release, started_at, log_path, str(exc)))
                    return 2
                finally:
                    result_path.unlink(missing_ok=True)
                    if checkout_root is not None:
                        shutil.rmtree(checkout_root, ignore_errors=True)
    except InvocationBusy:
        print("mkchad-release: another invocation is already running", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
