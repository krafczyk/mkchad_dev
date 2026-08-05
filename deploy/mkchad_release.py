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
import signal
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
COMPONENT_DISPOSITIONS = {
    "msk_containers": "deployed",
    "container_tools": "deployed",
    "mkchad": "deployed",
    "opencode-db": "deployed",
    "opencode": "embedded",
    "opencode.nvim": "configuration-owned",
    "opencode-project-reload": "configuration-owned",
    "compound-engineering": "configuration-owned",
    "opencode_sprint_loop": "disabled",
}
REDACT_ASSIGNMENT = re.compile(r"(?i)\b(?P<key>[A-Za-z0-9_-]*(?:password|token|secret|api[_-]?key)[A-Za-z0-9_-]*)=[^\s]+")
COMMAND_TIMEOUT = 300


class ReleaseError(RuntimeError):
    """Raised when release acquisition or synchronous handoff cannot complete."""


class InvocationBusy(RuntimeError):
    """Raised when another public release invocation owns the diagnostic lock."""


class ReleaseInterrupted(RuntimeError):
    """Raised after an interrupted child has completed its own cleanup."""


def _timestamp() -> str:
    """Return the current UTC timestamp in the result-schema representation."""
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _validate_tag(tag: str):
    """Reject tag syntax that could select a revision other than one exact tag."""
    if not TAG.fullmatch(tag) or ".." in tag or tag.endswith("/"):
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
    return REDACT_ASSIGNMENT.sub(lambda match: match.group("key") + "=<redacted>", text)


def _write_stream(stream, log, data: bytes):
    """Write one filtered subprocess chunk to its terminal channel and the run log."""
    text = _redact(data.decode("utf-8", errors="replace"))
    stream.write(text)
    stream.flush()
    log.write(text)
    log.flush()


def _run(command: list[str], *, cwd: pathlib.Path, log, timeout: float | None = COMMAND_TIMEOUT, stderr_capture: list[str] | None = None) -> int:
    """Run one command while preserving stdout/stderr channels in the log."""
    try:
        process = subprocess.Popen(
            command,
            cwd=cwd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            close_fds=True,
            start_new_session=timeout is not None,
        )
    except OSError as exc:
        raise ReleaseError(f"cannot start {command[0]}: {exc}") from exc
    assert process.stdout is not None and process.stderr is not None
    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ, sys.stdout)
    selector.register(process.stderr, selectors.EVENT_READ, sys.stderr)
    deadline = time.monotonic() + timeout if timeout is not None else None
    try:
        while selector.get_map():
            remaining = deadline - time.monotonic() if deadline is not None else None
            if remaining is not None and remaining <= 0:
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(process.pid, signal.SIGTERM)
                process.wait(timeout=5)
                raise ReleaseError(f"command timed out after {timeout:g} seconds: {command[0]}")
            for key, _ in selector.select(0.1 if remaining is None else min(0.1, remaining)):
                data = os.read(key.fileobj.fileno(), 65536)
                if not data:
                    selector.unregister(key.fileobj)
                    continue
                _write_stream(key.data, log, data)
                if stderr_capture is not None and key.data is sys.stderr:
                    stderr_capture.append(_redact(data.decode("utf-8", errors="replace")))
                    if sum(map(len, stderr_capture)) > 8192:
                        stderr_capture[:] = ["".join(stderr_capture)[-4096:]]
        return process.wait() if deadline is None else process.wait(timeout=max(0.1, deadline - time.monotonic()))
    except KeyboardInterrupt:
        with contextlib.suppress(ProcessLookupError):
            process.send_signal(signal.SIGINT)
        try:
            stdout, stderr = process.communicate(timeout=15)
        except subprocess.TimeoutExpired:
            process.terminate()
            stdout, stderr = process.communicate(timeout=5)
        _write_stream(sys.stdout, log, stdout)
        _write_stream(sys.stderr, log, stderr)
        raise ReleaseInterrupted from None
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
        ("opencode-db", "deployed"),
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


def _valid_result_document(document: object) -> bool:
    """Return whether a child document satisfies the closed result-v1 contract."""
    if not isinstance(document, dict) or set(document) != RESULT_FIELDS or document.get("schema") != 1:
        return False
    if document.get("outcome") not in {"succeeded", "failed", "interrupted"} or document.get("phase") not in {"preflight", "apply", "verify"}:
        return False
    timestamp = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z")
    if not all(isinstance(document.get(key), str) and timestamp.fullmatch(document[key]) for key in ("started_at", "completed_at")):
        return False
    components = document.get("components")
    if not isinstance(components, list) or len(components) != len(COMPONENT_DISPOSITIONS):
        return False
    observed_names = set()
    for component in components:
        if not isinstance(component, dict) or set(component) != {"name", "disposition", "requested_commit", "observed_commit"}:
            return False
        name = component.get("name")
        if name not in COMPONENT_DISPOSITIONS or name in observed_names or component.get("disposition") != COMPONENT_DISPOSITIONS[name] or not re.fullmatch(r"[0-9a-f]{40}", component.get("requested_commit", "")):
            return False
        if component.get("observed_commit") is not None and not re.fullmatch(r"[0-9a-f]{40}", component["observed_commit"]):
            return False
        observed_names.add(name)
    artifact = document.get("artifact")
    if not isinstance(artifact, dict) or set(artifact) != {"architecture", "expected_sha256", "observed_sha256"} or artifact.get("architecture") not in {"x86_64", "aarch64", "ppc64le"} or not re.fullmatch(r"[0-9a-f]{64}", artifact.get("expected_sha256", "")):
        return False
    if artifact.get("observed_sha256") is not None and not re.fullmatch(r"[0-9a-f]{64}", artifact["observed_sha256"]):
        return False
    actions = document.get("actions")
    if not isinstance(actions, list):
        return False
    action_keys = set()
    for action in actions:
        if not isinstance(action, dict) or set(action) != {"component", "id", "phase", "status"}:
            return False
        key = (action.get("component"), action.get("id"))
        if COMPONENT_DISPOSITIONS.get(action.get("component")) != "deployed" or not isinstance(action.get("id"), str) or not re.fullmatch(r"[a-z][a-z0-9-]*", action["id"]) or action.get("phase") not in {"preflight", "apply", "verify"} or action.get("status") not in {"pending", "succeeded", "skipped-compliant", "failed"} or key in action_keys:
            return False
        action_keys.add(key)
    handoff = document.get("configuration_handoff")
    if not isinstance(handoff, dict) or set(handoff) != {"state", "contract_version", "guidance", "components"} or handoff.get("state") != "configuration-pending" or handoff.get("contract_version") != 1:
        return False
    handoff_components = handoff.get("components")
    expected_handoff = {name for name, disposition in COMPONENT_DISPOSITIONS.items() if disposition == "configuration-owned"}
    if not isinstance(handoff_components, list) or len(handoff_components) != len(expected_handoff) or {item.get("name") for item in handoff_components if isinstance(item, dict)} != expected_handoff:
        return False
    if not isinstance(document.get("diagnostic"), str) or not document["diagnostic"] or not isinstance(document.get("log_path"), str) or not document["log_path"].startswith("/"):
        return False
    return document.get("failed_step") is None or isinstance(document["failed_step"], str) and bool(document["failed_step"])


def _matching_child_result(path: pathlib.Path, *, run_id: str, release: str, log_path: pathlib.Path, child_status: int | None = None) -> bool:
    """Return whether the child result is bound to this admitted run and log."""
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    matches = (
        _valid_result_document(document)
        and document.get("run_id") == run_id
        and document.get("release") == release
        and document.get("log_path") == str(log_path)
    )
    if not matches:
        return False
    return child_status is None or (child_status == 0) == (document.get("outcome") == "succeeded")


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
    """Run the public release launcher.

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
    parser.add_argument("--replace-partial", action="store_true", help="assert the managed service is inactive and replace legacy partial state")
    args = parser.parse_args(argv)
    try:
        _validate_tag(args.release)
    except ReleaseError as exc:
        print("mkchad-release: " + str(exc), file=sys.stderr)
        return 2
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
                    child_diagnostics: list[str] = []
                    child_status = _run(
                        [
                            sys.executable,
                            str(deployer),
                            "deploy",
                            "--release",
                            args.release,
                            "--image",
                            str(args.image.absolute()),
                            *(["--replace-partial"] if args.replace_partial else []),
                            "--log-path",
                            str(log_path),
                            "--result-path",
                            str(result_path),
                            "--run-id",
                            run_id,
                        ],
                        cwd=deployer.parent.parent,
                        log=log,
                        timeout=None,
                        stderr_capture=child_diagnostics,
                    )
                    if _matching_child_result(result_path, run_id=run_id, release=args.release, log_path=log_path, child_status=child_status):
                        os.replace(result_path, latest_result)
                        return child_status
                    diagnostic = "release deployer result does not match the admitted run" if result_path.exists() else "release deployer did not publish a matching terminal result"
                    detail = "".join(child_diagnostics).strip()
                    if detail:
                        diagnostic += ": " + detail[-3072:]
                    _publish_result(latest_result, _fallback_result(run_id, args.release, started_at, log_path, diagnostic))
                    return child_status if child_status else 2
                except ReleaseError as exc:
                    _write_stream(sys.stderr, log, ("mkchad-release: " + str(exc) + "\n").encode())
                    _publish_result(latest_result, _fallback_result(run_id, args.release, started_at, log_path, str(exc)))
                    return 2
                except ReleaseInterrupted:
                    if result_path.exists() and _matching_child_result(result_path, run_id=run_id, release=args.release, log_path=log_path):
                        os.replace(result_path, latest_result)
                    else:
                        result = _fallback_result(run_id, args.release, started_at, log_path, "release deployment interrupted")
                        result["outcome"] = "interrupted"
                        _publish_result(latest_result, result)
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
