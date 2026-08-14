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
    """Describe a release acquisition failure and optional operator guidance."""

    def __init__(self, message: str, *, category: str | None = None, action: str | None = None):
        super().__init__(message)
        self.category = category
        self.action = action


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


def _diagnostic(operation: str, category: str, action: str, log_path: pathlib.Path | None = None) -> str:
    """Render one bounded, redacted operator diagnostic without exception detail."""
    message = _redact(f"{operation}: {category}: {action}")
    if log_path is None:
        return message[:4096]
    suffix = _redact(f"; inspect log: {log_path}")
    if len(suffix) >= 4096:
        return _redact(f"{operation}: {category}: {action}; inspect the deployment state logs; the log path is too long to report")[:4096]
    return message[:4096 - len(suffix)] + suffix


def _release_failure_diagnostic(error: ReleaseError, log_path: pathlib.Path) -> str:
    """Classify release acquisition and handoff failures for safe publication."""
    if error.category is not None and error.action is not None:
        return _diagnostic("release acquisition", error.category, error.action, log_path)
    text = str(error)
    if "does not resolve" in text:
        return _diagnostic("release selection", "tag-resolution", "confirm the published tag names a commit and retry", log_path)
    if "no deployer" in text:
        return _diagnostic("release checkout", "deployer-missing", "use a release tag containing deploy/mkchad_deploy.py", log_path)
    if "timed out" in text:
        return _diagnostic("release acquisition", "timeout", "check release access and retry", log_path)
    if "cannot start" in text:
        return _diagnostic("release acquisition", "command-start", "repair the local Git or Python installation and retry", log_path)
    return _diagnostic("release acquisition", "git-operation", "check release access and retry", log_path)


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
        raise ReleaseError(
            f"cannot start {command[0]}",
            category="command-start",
            action="install or repair the required local tool, then retry",
        ) from exc
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
                raise ReleaseError(
                    f"command timed out after {timeout:g} seconds: {command[0]}",
                    category="command-timeout",
                    action="inspect the retained log, repair the stalled release operation, and retry",
                )
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
        raise ReleaseError(
            f"command timed out after {timeout:g} seconds: {command[0]}",
            category="command-timeout",
            action="inspect the retained log, repair the stalled release operation, and retry",
        ) from exc
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
            raise ReleaseError(
                "cannot resolve release commit",
                category="tag-resolution",
                action="confirm the published tag names a commit and retry",
            ) from exc
        _write_stream(sys.stdout, log, completed.stdout.encode())
        _write_stream(sys.stderr, log, completed.stderr.encode())
        if check and completed.returncode:
            raise ReleaseError(
                "release tag does not resolve to a commit",
                category="tag-resolution",
                action="confirm the published tag names a commit and retry",
            )
        return completed.stdout.strip()
    status = _run(command, cwd=cwd, log=log)
    if check and status:
        operation = arguments[0] if arguments else "operation"
        raise ReleaseError(
            f"git {operation} failed with status {status}",
            category=f"git-{operation}",
            action=f"repair Git {operation} access or state after status {status}, then retry",
        )
    return ""


def _rotate_logs(logs: pathlib.Path):
    """Retain the two newest completed logs before creating the current run log."""
    logs.mkdir(parents=True, exist_ok=True)
    existing = sorted(logs.glob("*.log"), key=lambda path: (path.stat().st_mtime_ns, path.name), reverse=True)
    for path in existing[2:]:
        path.unlink()


def _fallback_result(run_id: str, release: str, started_at: str, log_path: pathlib.Path, diagnostic: str) -> dict:
    """Build a schema-2 failed result for acquisition or handoff failures."""
    components = list(COMPONENT_DISPOSITIONS.items())
    return {
        "schema": 2,
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
    """Atomically replace the latest completed result with one schema-2 document."""
    temporary = path.with_name(f".{path.name}-{uuid.uuid4().hex}.tmp")
    temporary.write_text(json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def _promote_child_result(source: pathlib.Path, destination: pathlib.Path, document: dict):
    """Atomically promote one parsed child result, rewriting only for redaction."""
    original = document.get("diagnostic")
    if not isinstance(original, str):
        os.replace(source, destination)
        return
    diagnostic = _redact(original)[:4096]
    if diagnostic == original:
        os.replace(source, destination)
        return
    sanitized = dict(document)
    sanitized["diagnostic"] = diagnostic
    _publish_result(destination, sanitized)


def _valid_result_document(document: object) -> bool:
    """Return whether a child document satisfies the closed result-v2 contract."""
    if not isinstance(document, dict) or set(document) != RESULT_FIELDS or document.get("schema") != 2:
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


def _load_child_result(path: pathlib.Path, *, run_id: str, release: str, log_path: pathlib.Path, child_status: int | None = None) -> tuple[dict | None, str | None]:
    """Read one child result and return its parsed document or safe rejection category."""
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None, "missing"
    except (OSError, json.JSONDecodeError):
        return None, "unreadable"
    if not _valid_result_document(document):
        return None, "schema-invalid"
    if document.get("run_id") != run_id:
        return None, "run-binding-mismatch"
    if document.get("release") != release:
        return None, "release-binding-mismatch"
    if document.get("log_path") != str(log_path):
        return None, "log-binding-mismatch"
    if child_status is not None and (child_status == 0) != (document.get("outcome") == "succeeded"):
        return None, "status-outcome-mismatch"
    return document, None


def _child_result_issue(path: pathlib.Path, *, run_id: str, release: str, log_path: pathlib.Path, child_status: int | None = None) -> str | None:
    """Return a safe rejection category for an untrusted child result document."""
    return _load_child_result(path, run_id=run_id, release=release, log_path=log_path, child_status=child_status)[1]


def _matching_child_result(path: pathlib.Path, *, run_id: str, release: str, log_path: pathlib.Path, child_status: int | None = None) -> bool:
    """Return whether the child result is bound to this admitted run and log."""
    return _load_child_result(path, run_id=run_id, release=release, log_path=log_path, child_status=child_status)[1] is None


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
            raise ReleaseError(
                "release tag does not resolve to a commit",
                category="tag-resolution",
                action="confirm the published tag names a commit and retry",
            )
        _git(["checkout", "--detach", "--no-recurse-submodules", commit], cwd=checkout, log=log)
        deployer = checkout / "deploy" / "mkchad_deploy.py"
        if not deployer.is_file():
            raise ReleaseError(
                "release checkout has no deployer",
                category="deployer-missing",
                action="use a release tag containing deploy/mkchad_deploy.py",
            )
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
        after the release-owned deployer exits. The inherited
        ``CONTAINER_TOOLS_C_COMPILER`` value, when nonempty, is passed unchanged to
        select the release deployer's exact static host-tool compiler.
    """
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0], allow_abbrev=False)
    parser.add_argument("--release", required=True)
    parser.add_argument("--image", type=pathlib.Path, required=True)
    parser.add_argument("--replace-partial", action="store_true", help="assert the managed service is inactive and replace legacy partial state")
    args = parser.parse_args(argv)
    try:
        _validate_tag(args.release)
    except ReleaseError as exc:
        print("mkchad-release: " + _diagnostic("release selection", "invalid-tag", "provide one published release tag and retry"), file=sys.stderr)
        return 2
    retained_log = None
    try:
        state_root = _state_root()
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
                retained_log = log_path
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
                    )
                    document, issue = _load_child_result(
                        result_path,
                        run_id=run_id,
                        release=args.release,
                        log_path=log_path,
                        child_status=child_status,
                    )
                    if issue is None:
                        assert document is not None
                        _promote_child_result(result_path, latest_result, document)
                        return child_status
                    diagnostic = _diagnostic("release deployer result", issue or "missing", "repair the release deployer result publication and rerun", log_path)
                    _publish_result(latest_result, _fallback_result(run_id, args.release, started_at, log_path, diagnostic))
                    return child_status if child_status else 2
                except ReleaseError as exc:
                    diagnostic = _release_failure_diagnostic(exc, log_path)
                    _write_stream(sys.stderr, log, ("mkchad-release: " + diagnostic + "\n").encode())
                    _publish_result(latest_result, _fallback_result(run_id, args.release, started_at, log_path, diagnostic))
                    return 2
                except ReleaseInterrupted:
                    document, issue = _load_child_result(
                        result_path,
                        run_id=run_id,
                        release=args.release,
                        log_path=log_path,
                    )
                    if issue is None:
                        assert document is not None
                        _promote_child_result(result_path, latest_result, document)
                    else:
                        result = _fallback_result(run_id, args.release, started_at, log_path, _diagnostic("release deployment", "interrupted", "inspect the retained log, repair any partial state, and rerun", log_path))
                        result["outcome"] = "interrupted"
                        _publish_result(latest_result, result)
                    return 2
                finally:
                    result_path.unlink(missing_ok=True)
                    if checkout_root is not None:
                        shutil.rmtree(checkout_root, ignore_errors=True)
    except InvocationBusy:
        print("mkchad-release: " + _diagnostic("invocation", "contention", "another invocation is active; wait for it to finish and retry"), file=sys.stderr)
        return 2
    except (OSError, ReleaseError):
        print("mkchad-release: " + _diagnostic("diagnostic state", "filesystem", "repair the deployment state directory and retry", retained_log), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
