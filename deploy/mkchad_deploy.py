#!/usr/bin/env python3
"""Run one launcher-admitted schema-2 MkChad deployment."""

from __future__ import annotations

import argparse
import contextlib
import datetime
import fcntl
import hashlib
import json
import os
import pathlib
import re
import selectors
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time
import tarfile
import uuid


SHA1 = re.compile(r"^[0-9a-f]{40}$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")
TAG = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]*$")
COMPONENTS = {
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
CONFIGURATION_COMPONENTS = frozenset(name for name, disposition in COMPONENTS.items() if disposition == "configuration-owned")
PHASES = ("preflight", "apply", "verify")
TOKENS = frozenset({"recovery_dir", "staged_image", "component_revision"})
ARCHITECTURES = frozenset({"x86_64", "aarch64", "ppc64le"})
CAPTURE_LIMIT = 1024 * 1024
PACKAGE_TOKENS = frozenset({
    "staged_container_tools_host_package", "container_tools_host_sha256",
    "container_tools_host_version",
    "container_tools_host_source_commit", "container_tools_host_prefix",
    "container_tools_host_libc", "native_architecture",
})
PACKAGE_VERSION = re.compile(r"^(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)(?:-[0-9A-Za-z]+(?:[.-][0-9A-Za-z]+)*)?$")
REDACT = re.compile(r"(?i)\b([A-Za-z0-9_-]*(?:password|token|secret|api[_-]?key)[A-Za-z0-9_-]*)=[^\s]+")
MANAGED_IMAGE = re.compile(r"neovim_[0-9]+\.[0-9]+\.[0-9]+_g[0-9a-fA-F]{7,40}_[0-9a-fA-F]{8,64}_(?:x86|x86_64|aarch64|ppc64le)\.sif")
DEVELOPER_REPOSITORIES = {
    "msk_containers": "git@github.com:krafczyk/msk_containers",
    "container_tools": "git@github.com:krafczyk/container_tools",
    "mkchad": "git@github.com:krafczyk/mkchad",
    "opencode-db": "git@github.com:krafczyk/opencode-db.git",
}
HOST_PACKAGE_ARGS = ["--container-tools-archive", {"token": "staged_container_tools_host_package"}, "--container-tools-sha256", {"token": "container_tools_host_sha256"}, "--container-tools-version", {"token": "container_tools_host_version"}, "--container-tools-source-commit", {"token": "container_tools_host_source_commit"}, "--container-tools-architecture", {"token": "native_architecture"}, "--container-tools-libc", {"token": "container_tools_host_libc"}]
PACKAGE_ACTIONS = [
    {"id": "container-tools-package-preflight", "phase": "preflight", "path": "scripts/install-package.sh", "args": ["--check", "--archive", {"token": "staged_container_tools_host_package"}, "--sha256", {"token": "container_tools_host_sha256"}, "--version", {"token": "container_tools_host_version"}, "--source-commit", {"token": "container_tools_host_source_commit"}, "--architecture", {"token": "native_architecture"}, "--libc", {"token": "container_tools_host_libc"}, "--prefix", {"token": "container_tools_host_prefix"}]},
    {"id": "container-tools-package-install", "phase": "apply", "path": "scripts/install-package.sh", "args": ["--apply", "--archive", {"token": "staged_container_tools_host_package"}, "--sha256", {"token": "container_tools_host_sha256"}, "--version", {"token": "container_tools_host_version"}, "--source-commit", {"token": "container_tools_host_source_commit"}, "--architecture", {"token": "native_architecture"}, "--libc", {"token": "container_tools_host_libc"}, "--prefix", {"token": "container_tools_host_prefix"}, "--recovery-dir", {"token": "recovery_dir"}]},
    {"id": "container-tools-package-verify", "phase": "verify", "path": "scripts/install-package.sh", "args": ["--verify", "--archive", {"token": "staged_container_tools_host_package"}, "--sha256", {"token": "container_tools_host_sha256"}, "--version", {"token": "container_tools_host_version"}, "--source-commit", {"token": "container_tools_host_source_commit"}, "--architecture", {"token": "native_architecture"}, "--libc", {"token": "container_tools_host_libc"}, "--prefix", {"token": "container_tools_host_prefix"}]},
]
DEVELOPER_ACTIONS = [
    {"id": "host-tools-preflight", "phase": "preflight", "path": "bin/install_nvim.sh", "args": ["--check", "--recovery-dir", {"token": "recovery_dir"}, *HOST_PACKAGE_ARGS]},
    {"id": "image-install-preflight", "phase": "preflight", "path": "nvim/bin/install_nvim_container", "args": ["--dry-run", "--source-revision", {"token": "component_revision"}, {"token": "staged_image"}]},
    {"id": "host-tools-install", "phase": "apply", "path": "bin/install_nvim.sh", "args": ["--recovery-dir", {"token": "recovery_dir"}, *HOST_PACKAGE_ARGS]},
    {"id": "image-install", "phase": "apply", "path": "nvim/bin/install_nvim_container", "args": ["--source-revision", {"token": "component_revision"}, {"token": "staged_image"}]},
    {"id": "host-tools-verify", "phase": "verify", "path": "bin/install_nvim.sh", "args": ["--check", "--recovery-dir", {"token": "recovery_dir"}, *HOST_PACKAGE_ARGS]},
]
OPENCODE_DB_ACTIONS = [
    {"id": "opencode-db-preflight", "phase": "preflight", "path": "bin/install_opencode_db.sh", "args": ["--check", "--recovery-dir", {"token": "recovery_dir"}]},
    {"id": "opencode-db-install", "phase": "apply", "path": "bin/install_opencode_db.sh", "args": ["--recovery-dir", {"token": "recovery_dir"}]},
    {"id": "opencode-db-verify", "phase": "verify", "path": "bin/install_opencode_db.sh", "args": ["--check"]},
]
DEPLOYED_COMPONENTS = tuple(name for name, disposition in COMPONENTS.items() if disposition == "deployed")


class DeploymentError(RuntimeError):
    """Raised when preflight, apply, or final verification cannot complete."""


def _reject_duplicates(pairs):
    """Reject duplicate JSON keys while decoding a deployment document."""
    result = {}
    for key, value in pairs:
        if key in result:
            raise DeploymentError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _load(path: pathlib.Path, label: str) -> dict:
    """Load a bounded regular JSON document without accepting duplicate keys."""
    try:
        if not path.is_file() or path.is_symlink() or path.stat().st_size > 1024 * 1024:
            raise OSError
        return json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_reject_duplicates)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DeploymentError(f"malformed {label}") from exc


def _repository(value: object) -> str:
    """Validate one canonical GitHub SSH or HTTPS repository URL."""
    if not isinstance(value, str) or not re.fullmatch(r"(?:git@github\.com:|https://github\.com/)[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+(?:\.git)?", value):
        raise DeploymentError("invalid repository")
    return value


def _relative(value: object, label: str) -> str:
    """Validate one normalized non-absolute repository-relative path."""
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]*", value) or any(part in {".", ".."} for part in pathlib.PurePosixPath(value).parts):
        raise DeploymentError(f"invalid {label}")
    return value


def validate_release_v2_document(document: dict) -> dict:
    """Validate and return a closed schema-2 manifest.

    Raises:
        DeploymentError: If component, artifact, action, destination, or
            configuration metadata violates the release contract.
    """
    if not isinstance(document, dict) or set(document) != {"schema", "components", "artifacts", "configuration"} or document.get("schema") != 2:
        raise DeploymentError("unsupported release schema")
    components = document.get("components")
    if not isinstance(components, list):
        raise DeploymentError("malformed components")
    names, destinations, action_ids = set(), [], set()
    for component in components:
        if not isinstance(component, dict) or component.get("name") not in COMPONENTS or component.get("name") in names:
            raise DeploymentError("components must be complete and unique")
        name = component["name"]
        if component.get("disposition") != COMPONENTS[name] or not SHA1.fullmatch(component.get("commit", "")):
            raise DeploymentError(f"invalid component identity: {name}")
        _repository(component.get("repository"))
        allowed = {"name", "disposition", "repository", "commit"}
        if component["disposition"] == "deployed":
            allowed |= {"destination", "actions"}
            destination = component.get("destination")
            if not isinstance(destination, dict) or set(destination) != {"root", "path"} or destination["root"] not in {"home", "xdg_config_home", "xdg_data_home", "xdg_state_home"}:
                raise DeploymentError(f"invalid destination: {name}")
            destinations.append((name, destination["root"], _relative(destination["path"], "destination")))
            if not isinstance(component.get("actions"), list):
                raise DeploymentError(f"invalid actions: {name}")
            last_phase = 0
            for action in component["actions"]:
                if not isinstance(action, dict) or set(action) != {"id", "phase", "path", "args"} or not isinstance(action.get("id"), str) or not re.fullmatch(r"[a-z][a-z0-9-]*", action["id"]):
                    raise DeploymentError(f"invalid action: {name}")
                if action["id"] in action_ids or action.get("phase") not in PHASES:
                    raise DeploymentError("duplicate action ID or invalid phase")
                phase = PHASES.index(action["phase"])
                if phase < last_phase:
                    raise DeploymentError("action phases are out of order")
                last_phase = phase
                action_ids.add(action["id"])
                _relative(action.get("path"), "action path")
                if not isinstance(action.get("args"), list):
                    raise DeploymentError("invalid action arguments")
                for argument in action["args"]:
                    if isinstance(argument, str):
                        if not argument or argument.startswith("/") or "\x00" in argument or "{" in argument or "}" in argument:
                            raise DeploymentError("invalid action argument")
                    elif not isinstance(argument, dict) or set(argument) != {"token"} or argument.get("token") not in TOKENS | PACKAGE_TOKENS:
                        raise DeploymentError("unknown action token")
            if "preserved_paths" in component:
                if name != "mkchad" or component["preserved_paths"] != ["opencode-server.json", "lazy-lock.json"]:
                    raise DeploymentError("invalid preserved paths")
                allowed.add("preserved_paths")
        elif {"destination", "actions", "preserved_paths"} & set(component):
            raise DeploymentError("non-deployed component has deployment fields")
        if set(component) != allowed:
            raise DeploymentError(f"unknown component fields: {name}")
        names.add(name)
    if names != set(COMPONENTS):
        raise DeploymentError("components must be complete and unique")
    for index, (name, root, path) in enumerate(destinations):
        for other_name, other_root, other_path in destinations[index + 1:]:
            allowed_nested = {name, other_name} == {"msk_containers", "container_tools"} and root == other_root == "xdg_data_home" and {path, other_path} == {"mkchad/components/msk_containers", "mkchad/components/msk_containers/container_tools"}
            if not allowed_nested and root == other_root and (path == other_path or path.startswith(other_path + "/") or other_path.startswith(path + "/")):
                raise DeploymentError("components have overlapping destinations")
    artifacts = document.get("artifacts")
    if not isinstance(artifacts, dict) or set(artifacts) != {"nvim-image", "container-tools-package"} or not isinstance(artifacts["nvim-image"], list) or not artifacts["nvim-image"] or not isinstance(artifacts["container-tools-package"], list):
        raise DeploymentError("malformed artifacts")
    architectures = set()
    for artifact in artifacts["nvim-image"]:
        if not isinstance(artifact, dict) or set(artifact) != {"architecture", "sha256", "source_filename_class"} or artifact.get("architecture") not in ARCHITECTURES or artifact["architecture"] in architectures or not SHA256.fullmatch(artifact.get("sha256", "")) or artifact.get("source_filename_class") != "raw-nvim-container":
            raise DeploymentError("invalid image artifact")
        architectures.add(artifact["architecture"])
    packages = artifacts["container-tools-package"]
    package_keys = set()
    package_roles: dict[str, set[str]] = {}
    package_majors: dict[str, set[int]] = {}
    for package in packages:
        key = (package.get("architecture"), package.get("role")) if isinstance(package, dict) else None
        if (
            not isinstance(package, dict)
            or set(package) != {"role", "architecture", "version", "product_major", "source_commit", "sha256", "libc", "source_filename_class"}
            or package.get("role") not in {"host", "container"}
            or not isinstance(package.get("architecture"), str)
            or package["architecture"] not in ARCHITECTURES
            or key in package_keys
            or not isinstance(package.get("version"), str) or not PACKAGE_VERSION.fullmatch(package["version"])
            or not isinstance(package.get("product_major"), int) or package["product_major"] < 0
            or not SHA1.fullmatch(package.get("source_commit", ""))
            or not SHA256.fullmatch(package.get("sha256", ""))
            or package["sha256"] == "0" * 64
            or package.get("libc") not in {"musl", "glibc"}
            or package.get("source_filename_class") != "container-tools-package"
        ):
            raise DeploymentError("invalid container-tools package artifact")
        package_keys.add(key)
        package_roles.setdefault(package["architecture"], set()).add(package["role"])
        package_majors.setdefault(package["architecture"], set()).add(package["product_major"])
    if set(package_roles) != architectures or any(roles != {"host", "container"} for roles in package_roles.values()):
        raise DeploymentError("each image architecture requires one complete container-tools package pair")
    if any(len(majors) != 1 for majors in package_majors.values()):
        raise DeploymentError("container-tools host and container package majors differ")
    container_actions = next(component["actions"] for component in components if component["name"] == "container_tools")
    if container_actions != PACKAGE_ACTIONS:
        raise DeploymentError("container-tools package actions are incomplete or out of order")
    msk_actions = next(component["actions"] for component in components if component["name"] == "msk_containers")
    if msk_actions != DEVELOPER_ACTIONS:
        raise DeploymentError("msk container-tools actions are incomplete or out of order")
    serialized = json.dumps(document, sort_keys=True)
    if "host-bridge" in serialized or "host_bridge" in serialized:
        raise DeploymentError("legacy host-bridge deployment surface is forbidden")
    configuration = document.get("configuration")
    if not isinstance(configuration, dict) or set(configuration) != {"contract_version", "guidance"} or configuration.get("contract_version") != 1:
        raise DeploymentError("malformed configuration handoff")
    _relative(configuration.get("guidance"), "configuration guidance")
    return document


def validate_result_v2_document(document: dict) -> dict:
    """Validate and return one closed terminal deployment result.

    Raises:
        DeploymentError: If the document is incomplete, future-versioned, or
            contains malformed component, action, artifact, or handoff data.
    """
    fields = {"schema", "run_id", "release", "started_at", "completed_at", "outcome", "phase", "components", "artifact", "packages", "actions", "configuration_handoff", "failed_step", "diagnostic", "log_path"}
    if not isinstance(document, dict) or set(document) != fields or document.get("schema") != 2 or not UUID.fullmatch(document.get("run_id", "")):
        raise DeploymentError("malformed terminal result")
    timestamp = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z")
    if (
        not isinstance(document.get("release"), str)
        or not TAG.fullmatch(document["release"])
        or document["release"].startswith("-")
        or ".." in document["release"]
        or not all(isinstance(document.get(key), str) and timestamp.fullmatch(document[key]) for key in ("started_at", "completed_at"))
        or document.get("outcome") not in {"succeeded", "failed", "interrupted"}
        or document.get("phase") not in PHASES
    ):
        raise DeploymentError("malformed terminal result")
    components = document.get("components")
    if not isinstance(components, list) or len(components) != len(COMPONENTS):
        raise DeploymentError("malformed terminal result components")
    names = set()
    for component in components:
        if not isinstance(component, dict) or set(component) != {"name", "disposition", "requested_commit", "observed_commit"}:
            raise DeploymentError("malformed terminal result components")
        name = component.get("name")
        if name not in COMPONENTS or name in names or component.get("disposition") != COMPONENTS[name] or not SHA1.fullmatch(component.get("requested_commit", "")):
            raise DeploymentError("malformed terminal result components")
        if component.get("observed_commit") is not None and not SHA1.fullmatch(component["observed_commit"]):
            raise DeploymentError("malformed terminal result components")
        names.add(name)
    if names != set(COMPONENTS):
        raise DeploymentError("malformed terminal result components")
    artifact = document.get("artifact")
    if (
        not isinstance(artifact, dict)
        or set(artifact) != {"architecture", "expected_sha256", "observed_sha256"}
        or artifact.get("architecture") not in ARCHITECTURES
        or not SHA256.fullmatch(artifact.get("expected_sha256", ""))
        or artifact.get("observed_sha256") is not None and not SHA256.fullmatch(artifact["observed_sha256"])
    ):
        raise DeploymentError("malformed terminal result artifact")
    packages = document.get("packages")
    if not isinstance(packages, dict) or set(packages) != {"expected", "observed"}:
        raise DeploymentError("malformed terminal result packages")
    expected, observed = packages["expected"], packages["observed"]
    if not isinstance(expected, list) or not isinstance(observed, list) or len(expected) != 2 or len(observed) != 2:
        raise DeploymentError("malformed terminal result packages")
    package_roles = set()
    for requested, actual in zip(expected, observed, strict=True):
        if not isinstance(requested, dict) or set(requested) != {"role", "architecture", "version", "product_major", "source_commit", "sha256", "libc", "source_filename_class"} or requested.get("role") not in {"host", "container"} or requested["role"] in package_roles or requested.get("architecture") not in ARCHITECTURES or not isinstance(requested.get("version"), str) or not PACKAGE_VERSION.fullmatch(requested["version"]) or not isinstance(requested.get("product_major"), int) or requested["product_major"] < 0 or not SHA1.fullmatch(requested.get("source_commit", "")) or not SHA256.fullmatch(requested.get("sha256", "")) or requested.get("libc") not in {"musl", "glibc"} or requested.get("source_filename_class") != "container-tools-package":
            raise DeploymentError("malformed terminal result packages")
        if actual is not None and actual != requested:
            raise DeploymentError("malformed terminal result packages")
        package_roles.add(requested["role"])
    if package_roles != {"host", "container"} or expected[0]["product_major"] != expected[1]["product_major"]:
        raise DeploymentError("malformed terminal result packages")
    actions = document.get("actions")
    if not isinstance(actions, list):
        raise DeploymentError("malformed terminal result")
    action_ids = set()
    for action in actions:
        if not isinstance(action, dict) or set(action) != {"component", "id", "phase", "status"}:
            raise DeploymentError("malformed terminal result action")
        key = (action.get("component"), action.get("id"))
        if COMPONENTS.get(action.get("component")) != "deployed" or not isinstance(action.get("id"), str) or not re.fullmatch(r"[a-z][a-z0-9-]*", action["id"]) or action.get("phase") not in PHASES or action.get("status") not in {"pending", "succeeded", "skipped-compliant", "failed"} or key in action_ids:
            raise DeploymentError("malformed terminal result action")
        action_ids.add(key)
    handoff = document.get("configuration_handoff")
    if not isinstance(handoff, dict) or set(handoff) != {"state", "contract_version", "guidance", "components"} or handoff.get("state") != "configuration-pending" or handoff.get("contract_version") != 1:
        raise DeploymentError("malformed terminal result handoff")
    _relative(handoff.get("guidance"), "configuration guidance")
    handoff_components = handoff.get("components")
    if not isinstance(handoff_components, list) or len(handoff_components) != len(CONFIGURATION_COMPONENTS):
        raise DeploymentError("malformed terminal result handoff")
    handoff_names = set()
    for component in handoff_components:
        if not isinstance(component, dict) or set(component) != {"name", "repository", "commit"} or component.get("name") not in CONFIGURATION_COMPONENTS or component["name"] in handoff_names or not SHA1.fullmatch(component.get("commit", "")):
            raise DeploymentError("malformed terminal result handoff")
        _repository(component.get("repository"))
        handoff_names.add(component["name"])
    if handoff_names != CONFIGURATION_COMPONENTS:
        raise DeploymentError("malformed terminal result handoff")
    if document.get("failed_step") is not None and (not isinstance(document["failed_step"], str) or not document["failed_step"] or len(document["failed_step"]) > 256):
        raise DeploymentError("malformed terminal result failure")
    if not isinstance(document.get("diagnostic"), str) or not document["diagnostic"] or len(document["diagnostic"]) > 4096:
        raise DeploymentError("malformed terminal result diagnostic")
    if not isinstance(document.get("log_path"), str) or not document["log_path"].startswith("/") or any(part in {"", ".", ".."} for part in pathlib.PurePosixPath(document["log_path"]).parts[1:]):
        raise DeploymentError("malformed terminal result log path")
    return document


def _roots() -> dict[str, pathlib.Path]:
    """Return target roots from the process HOME and XDG environment."""
    home = pathlib.Path(os.environ["HOME"])
    return {"home": home, "xdg_config_home": pathlib.Path(os.environ.get("XDG_CONFIG_HOME", home / ".config")), "xdg_data_home": pathlib.Path(os.environ.get("XDG_DATA_HOME", home / ".local/share")), "xdg_state_home": pathlib.Path(os.environ.get("XDG_STATE_HOME", home / ".local/state"))}


def _terminate_group(process: subprocess.Popen):
    """Terminate one subprocess group while retaining its leader as the PGID anchor."""
    leader_unreaped = process.returncode is None
    try:
        if not leader_unreaped:
            return
        with contextlib.suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGTERM)
        deadline = time.monotonic() + 5
        while True:
            try:
                if os.waitid(os.P_PID, process.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT) is not None:
                    break
            except ChildProcessError:
                # Another observer reaped the leader, so its old PGID is unsafe to signal.
                leader_unreaped = False
                return
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            time.sleep(min(0.1, remaining))
    finally:
        if leader_unreaped:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGKILL)
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired as exc:
                raise DeploymentError("process-group cleanup did not complete") from exc


def _close_capture_pipes(process: subprocess.Popen):
    """Close captured pipes after cleanup so escaped descendants cannot retain them."""
    for pipe in (process.stdout, process.stderr):
        if pipe is not None:
            with contextlib.suppress(OSError):
                pipe.close()


def _capture(command: list[str], *, cwd: pathlib.Path | None = None, env: dict[str, str] | None = None, timeout: float = 300) -> subprocess.CompletedProcess:
    """Run one bounded command with concurrent, size-limited UTF-8 output capture.

    Raises:
        DeploymentError: If startup fails, either retained output channel exceeds
            :data:`CAPTURE_LIMIT`, the command times out, or group cleanup is
            uncertain.
    """
    try:
        process = subprocess.Popen(command, cwd=cwd, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True, close_fds=True)
    except OSError as exc:
        raise DeploymentError(f"command could not start: {command[0]}") from exc
    assert process.stdout is not None and process.stderr is not None
    selector = selectors.DefaultSelector()
    captured = {"stdout": bytearray(), "stderr": bytearray()}
    selector.register(process.stdout, selectors.EVENT_READ, "stdout")
    selector.register(process.stderr, selectors.EVENT_READ, "stderr")
    deadline = time.monotonic() + timeout
    try:
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise subprocess.TimeoutExpired(command, timeout)
            for key, _ in selector.select(min(0.1, remaining)):
                data = os.read(key.fileobj.fileno(), 65536)
                if not data:
                    selector.unregister(key.fileobj)
                    continue
                channel = key.data
                if len(captured[channel]) + len(data) > CAPTURE_LIMIT:
                    raise DeploymentError(f"command {channel} output exceeded {CAPTURE_LIMIT} byte capture limit: {command[0]}")
                captured[channel].extend(data)
        process.wait(timeout=max(0.1, deadline - time.monotonic()))
    except KeyboardInterrupt:
        try:
            _terminate_group(process)
        finally:
            _close_capture_pipes(process)
        raise
    except subprocess.TimeoutExpired as exc:
        try:
            _terminate_group(process)
        finally:
            _close_capture_pipes(process)
        raise DeploymentError(f"command timed out after {timeout:g} seconds: {command[0]}") from exc
    except DeploymentError:
        try:
            _terminate_group(process)
        finally:
            _close_capture_pipes(process)
        raise
    finally:
        selector.close()
        _close_capture_pipes(process)
    return subprocess.CompletedProcess(
        command,
        process.returncode,
        bytes(captured["stdout"]).decode("utf-8", errors="replace"),
        bytes(captured["stderr"]).decode("utf-8", errors="replace"),
    )


def _git(arguments: list[str], cwd: pathlib.Path | None = None, check: bool = True) -> subprocess.CompletedProcess:
    """Run a bounded noninteractive Git command without hooks or templates."""
    result = _capture(["git", "-c", "core.hooksPath=/dev/null", "-c", "init.templateDir=", *arguments], cwd=cwd, env={**os.environ, "GIT_TERMINAL_PROMPT": "0"})
    if check and result.returncode:
        raise DeploymentError(f"git command failed: {arguments[0]}")
    return result


def _digest(path: pathlib.Path) -> str:
    """Return the SHA-256 digest of one regular file."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


@contextlib.contextmanager
def _lock(path: pathlib.Path):
    """Hold the ordinary deployment interlock for this synchronous run."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise DeploymentError("another deployment or startup holds the interlock") from exc
        yield


def _destination(roots: dict[str, pathlib.Path], component: dict) -> pathlib.Path:
    """Resolve one validated named-root destination without dereferencing it."""
    return roots[component["destination"]["root"]].joinpath(*pathlib.PurePosixPath(component["destination"]["path"]).parts)


def _stage(component: dict, staging: pathlib.Path, source: pathlib.Path | None = None) -> tuple[pathlib.Path, pathlib.Path]:
    """Fetch one selected component into disposable staging from its declared source."""
    bare, checkout = staging / f"{component['name']}.git", staging / component["name"]
    _git(["init", "--bare", str(bare)])
    _git(["-C", str(bare), "fetch", "--no-tags", str(source or component["repository"]), component["commit"]])
    if _git(["-C", str(bare), "cat-file", "-e", component["commit"] + "^{commit}"], check=False).returncode:
        raise DeploymentError(f"staged component commit is unavailable: {component['name']}")
    _git(["-C", str(bare), "worktree", "add", "--detach", str(checkout), component["commit"]])
    return bare, checkout


def _overlay_container_tools(staged: dict[str, tuple[pathlib.Path, pathlib.Path]], components: dict[str, dict]):
    """Replace the staged msk nested checkout with the independent tools snapshot."""
    nested = staged["msk_containers"][1] / "container_tools"
    if nested.is_symlink():
        nested.unlink()
    elif nested.exists():
        shutil.rmtree(nested)
    _git(["-C", str(staged["container_tools"][0]), "worktree", "add", "--detach", str(nested), components["container_tools"]["commit"]])


def _stage_release_sources(components: dict[str, dict], staging: pathlib.Path) -> dict[str, tuple[pathlib.Path, pathlib.Path]]:
    """Stage manifest-pinned components from their release repositories."""
    staged = {name: _stage(component, staging) for name, component in components.items()}
    _overlay_container_tools(staged, components)
    return staged


def _developer_components(commits: dict[str, str]) -> dict[str, dict]:
    """Build the fixed developer deployment profile for selected local commits."""
    return {
        "msk_containers": {
            "name": "msk_containers", "disposition": "deployed", "repository": DEVELOPER_REPOSITORIES["msk_containers"], "commit": commits["msk_containers"],
            "destination": {"root": "xdg_data_home", "path": "mkchad/components/msk_containers"}, "actions": [dict(action) for action in DEVELOPER_ACTIONS],
        },
        "container_tools": {
            "name": "container_tools", "disposition": "deployed", "repository": DEVELOPER_REPOSITORIES["container_tools"], "commit": commits["container_tools"],
            "destination": {"root": "xdg_data_home", "path": "mkchad/components/msk_containers/container_tools"}, "actions": [],
        },
        "mkchad": {
            "name": "mkchad", "disposition": "deployed", "repository": DEVELOPER_REPOSITORIES["mkchad"], "commit": commits["mkchad"],
            "destination": {"root": "xdg_config_home", "path": "mkchad"}, "actions": [], "preserved_paths": ["opencode-server.json", "lazy-lock.json"],
        },
        "opencode-db": {
            "name": "opencode-db", "disposition": "deployed", "repository": DEVELOPER_REPOSITORIES["opencode-db"], "commit": commits["opencode-db"],
            "destination": {"root": "xdg_data_home", "path": "mkchad/components/opencode-db"}, "actions": [dict(action) for action in OPENCODE_DB_ACTIONS],
        },
    }


def _workspace_status(path: pathlib.Path) -> list[tuple[str, str]]:
    """Return porcelain-v1 status records while preserving unusual path bytes."""
    raw = _git(["status", "--porcelain=v1", "-z", "--untracked-files=all", "--ignore-submodules=none"], path).stdout
    status = []
    records = iter(raw.split("\0"))
    for record in records:
        if not record:
            break
        code, name = record[:2], record[3:]
        if code[0] in "RC" or code[1] in "RC":
            next(records, None)
            status.append((code, ""))
        else:
            status.append((code, name))
    return status


def _developer_child(path: pathlib.Path, name: str, *, allow_nested: bool = False) -> str:
    """Require one expected non-symbolic child checkout to be clean at a commit."""
    if path.is_symlink() or not path.is_dir():
        raise DeploymentError(f"expected child is not a directory: {name}")
    try:
        top = pathlib.Path(_git(["rev-parse", "--show-toplevel"], path).stdout.strip()).resolve(strict=True)
    except (DeploymentError, OSError) as exc:
        raise DeploymentError(f"expected child is not a Git top level: {name}") from exc
    if top != path.resolve(strict=True):
        raise DeploymentError(f"expected child is not a Git top level: {name}")
    for code, changed in _workspace_status(path):
        nested_change = allow_nested and changed.rstrip("/") == "container_tools" and code == " M"
        if not nested_change:
            raise DeploymentError(f"developer child has local work: {name}:{changed or '<rename>'}")
    commit = _git(["rev-parse", "--verify", "HEAD^{commit}"], path, check=False)
    if commit.returncode or not SHA1.fullmatch(commit.stdout.strip()):
        raise DeploymentError(f"developer child HEAD is not a commit: {name}")
    return commit.stdout.strip()


def admit_developer_workspace(workspace: pathlib.Path) -> dict:
    """Select four clean child commits from one canonical developer workspace.

    Args:
        workspace: Parent MkChad Git top-level containing the expected children.

    Returns:
        Parent identity plus exact source paths and selected child commit IDs.

    Raises:
        DeploymentError: If the layout, child identity, commit, or cleanliness
            check fails.

    This function reads Git metadata but does not stage or mutate target paths.
    """
    try:
        workspace = workspace.resolve(strict=True)
        top = pathlib.Path(_git(["rev-parse", "--show-toplevel"], workspace).stdout.strip()).resolve(strict=True)
    except (DeploymentError, OSError) as exc:
        raise DeploymentError("current directory is not the parent Git top level") from exc
    if workspace != top:
        raise DeploymentError("current directory is not the parent Git top level")
    msk, tools, mkchad, opencode_db = workspace / "msk_containers", workspace / "msk_containers" / "container_tools", workspace / "mkchad", workspace / "opencode-db"
    commits = {
        "msk_containers": _developer_child(msk, "msk_containers", allow_nested=True),
        "container_tools": _developer_child(tools, "container_tools"),
        "mkchad": _developer_child(mkchad, "mkchad"),
        "opencode-db": _developer_child(opencode_db, "opencode-db"),
    }
    parent_head = _git(["rev-parse", "--verify", "HEAD^{commit}"], workspace, check=False).stdout.strip()
    return {
        "parent": {"path": str(workspace), "revision": parent_head if SHA1.fullmatch(parent_head) else None, "dirty": bool(_workspace_status(workspace))},
        "sources": {"msk_containers": msk, "container_tools": tools, "mkchad": mkchad, "opencode-db": opencode_db},
        "commits": commits,
    }


def recheck_developer_workspace(admission: dict):
    """Refuse target mutation when an admitted source changed during staging.

    Args:
        admission: Result from :func:`admit_developer_workspace` containing the
            four selected source paths and commit IDs.

    Returns:
        ``None`` when every source remains clean at its selected commit.

    Raises:
        DeploymentError: If a source changed identity, commit, or cleanliness.

    This function reads child Git metadata and does not mutate sources or
    deployment targets.
    """
    for name, source in admission["sources"].items():
        try:
            commit = _developer_child(source, name, allow_nested=name == "msk_containers")
        except DeploymentError as exc:
            raise DeploymentError(f"developer source changed while staging: {name}") from exc
        if commit != admission["commits"][name]:
            raise DeploymentError(f"developer source changed while staging: {name}")


def stage_developer_sources(admission: dict, staging: pathlib.Path) -> dict[str, tuple[pathlib.Path, pathlib.Path]]:
    """Stage each admitted local commit under a caller-owned temporary root.

    Args:
        admission: Result from :func:`admit_developer_workspace`.
        staging: Empty caller-owned directory for bare repositories/checkouts.

    Returns:
        Component names mapped to their staged bare repository and checkout.

    Raises:
        DeploymentError: If an exact commit cannot be staged.

    The nested container-tools checkout is overlaid in staged msk_containers;
    no final deployment destination is changed.
    """
    components = _developer_components(admission["commits"])
    staged = {name: _stage(component, staging, admission["sources"][name]) for name, component in components.items()}
    _overlay_container_tools(staged, components)
    return staged


def _validate_actions(component: dict, checkout: pathlib.Path):
    """Require each declared action to be an executable pinned regular file."""
    for action in component["actions"]:
        path = checkout / action["path"]
        tree_entry = _git(["-C", str(checkout), "ls-tree", component["commit"], "--", action["path"]]).stdout.strip()
        match = re.fullmatch(r"([0-7]{6}) blob ([0-9a-f]{40,64})\t(.+)", tree_entry)
        try:
            entry = os.lstat(path)
        except OSError as exc:
            raise DeploymentError(f"action is absent from the pinned tree: {action['id']}") from exc
        if not match or match.group(3) != action["path"] or not stat.S_ISREG(entry.st_mode) or not entry.st_mode & stat.S_IXUSR or not int(match.group(1), 8) & stat.S_IXUSR:
            raise DeploymentError(f"action is not an executable regular file: {action['id']}")
        if _git(["-C", str(checkout), "hash-object", "--no-filters", str(path)]).stdout.strip() != match.group(2):
            raise DeploymentError(f"action does not match its pinned blob: {action['id']}")


def _stat_identity(entry: os.stat_result) -> tuple[int, int, int, int, int, int]:
    """Return the file identity fields used for stable observation."""
    return entry.st_dev, entry.st_ino, entry.st_mode, entry.st_size, entry.st_mtime_ns, entry.st_ctime_ns


def _stable_regular_digest(path: pathlib.Path, label: str) -> str:
    """Hash one non-symbolic regular file while rejecting identity changes."""
    try:
        before = os.lstat(path)
        if not stat.S_ISREG(before.st_mode):
            raise OSError
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except OSError as exc:
        raise DeploymentError(f"{label} is not a regular file") from exc
    try:
        opened = os.fstat(descriptor)
        if (opened.st_dev, opened.st_ino, stat.S_IFMT(opened.st_mode)) != (before.st_dev, before.st_ino, stat.S_IFMT(before.st_mode)):
            raise DeploymentError(f"{label} changed while hashing")
        digest = hashlib.sha256()
        with os.fdopen(descriptor, "rb", closefd=False) as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(block)
        after = os.fstat(descriptor)
        final = os.lstat(path)
        if _stat_identity(before) != _stat_identity(opened) or _stat_identity(before) != _stat_identity(after) or _stat_identity(before) != _stat_identity(final):
            raise DeploymentError(f"{label} changed while hashing")
        return digest.hexdigest()
    except OSError as exc:
        raise DeploymentError(f"{label} is unavailable") from exc
    finally:
        os.close(descriptor)


def _stage_image(source: pathlib.Path, staging: pathlib.Path, artifact: dict, expected_digest: str) -> pathlib.Path:
    """Copy a previously admitted caller image to the installer-required raw name."""
    raw_arch = {"x86_64": "x86", "aarch64": "aarch64", "ppc64le": "ppc64le"}[artifact["architecture"]]
    target = staging / f"nvim_container_{raw_arch}.sif"
    digest = hashlib.sha256()
    try:
        descriptor = os.open(source, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except OSError as exc:
        raise DeploymentError("caller image is not a regular file") from exc
    try:
        with os.fdopen(descriptor, "rb", closefd=False) as input_file, target.open("xb") as output_file:
            for block in iter(lambda: input_file.read(1024 * 1024), b""):
                digest.update(block)
                output_file.write(block)
    finally:
        os.close(descriptor)
    if digest.hexdigest() != expected_digest:
        target.unlink(missing_ok=True)
        raise DeploymentError("caller image changed before staging")
    return target


def _native_architecture() -> str:
    """Return the release architecture name for the running host."""
    return {"amd64": "x86_64", "arm64": "aarch64"}.get(os.uname().machine.lower(), os.uname().machine.lower())


def _select_container_tools_packages(artifacts: dict, architecture: str) -> dict[str, dict]:
    """Select one complete same-major package pair for a native architecture.

    Raises:
        DeploymentError: If either closed role is unavailable or the selected
            host and in-container package majors differ.
    """
    selected = {item["role"]: item for item in artifacts["container-tools-package"] if item["architecture"] == architecture}
    if set(selected) != {"host", "container"}:
        raise DeploymentError("release has no complete container-tools package pair for this architecture")
    if selected["host"]["product_major"] != selected["container"]["product_major"]:
        raise DeploymentError("container-tools host and container package majors differ")
    return selected


def _package_identity(value: object, *, role: str) -> dict:
    """Validate one package metadata document for its fixed deployment role."""
    if not isinstance(value, dict) or set(value) != {"role", "architecture", "version", "product_major", "source_commit", "sha256", "libc", "source_filename_class"}:
        raise DeploymentError("malformed container-tools package metadata")
    if value.get("role") != role or value.get("architecture") not in ARCHITECTURES or not isinstance(value.get("version"), str) or not PACKAGE_VERSION.fullmatch(value["version"]) or not isinstance(value.get("product_major"), int) or value["product_major"] < 0 or not SHA1.fullmatch(value.get("source_commit", "")) or not SHA256.fullmatch(value.get("sha256", "")) or value.get("libc") not in {"musl", "glibc"} or value.get("source_filename_class") != "container-tools-package":
        raise DeploymentError("malformed container-tools package metadata")
    return value


def _archive_package_identity(path: pathlib.Path, expected_sha256: str) -> dict:
    """Read one real package archive identity and bind its outer digest."""
    try:
        with tarfile.open(path, "r:*") as archive:
            members = [
                member
                for member in archive.getmembers()
                if len(pathlib.PurePosixPath(member.name).parts) == 2
                and pathlib.PurePosixPath(member.name).name == "archive.json"
            ]
            if len(members) != 1 or not members[0].isfile() or members[0].size > 4096:
                raise DeploymentError("container-tools package archive has invalid metadata")
            handle = archive.extractfile(members[0])
            if handle is None:
                raise DeploymentError("container-tools package archive has invalid metadata")
            metadata = json.loads(handle.read().decode("utf-8"), object_pairs_hook=_reject_duplicates)
    except (OSError, tarfile.TarError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DeploymentError("container-tools package archive has invalid metadata") from exc
    if (
        not isinstance(metadata, dict)
        or set(metadata) != {"schema", "version", "architecture", "libc", "source_commit"}
        or metadata.get("schema") != "container-tools.archive/v1"
        or not isinstance(metadata.get("version"), str)
        or not PACKAGE_VERSION.fullmatch(metadata["version"])
        or metadata.get("architecture") not in ARCHITECTURES
        or metadata.get("libc") not in {"musl", "glibc"}
        or not SHA1.fullmatch(metadata.get("source_commit", ""))
    ):
        raise DeploymentError("container-tools package archive has invalid metadata")
    root = f"container-tools-{metadata['version']}-{metadata['architecture']}-{metadata['libc']}-{metadata['source_commit']}"
    if pathlib.PurePosixPath(members[0].name).parts[0] != root:
        raise DeploymentError("container-tools package archive has invalid metadata")
    return _package_identity(
        {
            "role": "host",
            "architecture": metadata["architecture"],
            "version": metadata["version"],
            "product_major": int(metadata["version"].split(".", 1)[0]),
            "source_commit": metadata["source_commit"],
            "sha256": expected_sha256,
            "libc": metadata["libc"],
            "source_filename_class": "container-tools-package",
        },
        role="host",
    )


def _stage_package(source: pathlib.Path, staging: pathlib.Path, package: dict) -> tuple[pathlib.Path, dict]:
    """Privately copy and independently verify a caller archive before mutation."""
    target = staging / "container-tools-host-package.tar.gz"
    if _stable_regular_digest(source, "caller container-tools package") != package["sha256"]:
        raise DeploymentError("caller container-tools package digest does not match the release artifact")
    try:
        shutil.copyfile(source, target)
    except OSError as exc:
        raise DeploymentError("caller container-tools package could not be staged") from exc
    if _stable_regular_digest(target, "staged container-tools package") != package["sha256"]:
        target.unlink(missing_ok=True)
        raise DeploymentError("caller container-tools package changed while staging")
    identity = _archive_package_identity(target, package["sha256"])
    if identity != package:
        raise DeploymentError("container-tools package metadata does not match the selected identity")
    return target, identity


def _runtime_image(image: pathlib.Path, architecture: str):
    """Verify the staged image architecture with Apptainer or Singularity."""
    runtime = shutil.which("apptainer") or shutil.which("singularity")
    if not runtime:
        raise DeploymentError("no Apptainer or Singularity runtime is available")
    result = _capture([runtime, "exec", str(image), "uname", "-m"], timeout=40)
    observed = {"amd64": "x86_64", "x86": "x86_64", "arm64": "aarch64"}.get(result.stdout.strip().lower(), result.stdout.strip().lower())
    if result.returncode or observed != architecture:
        raise DeploymentError("staged image architecture does not match the release artifact")


def _runtime_image_package_identity(image: pathlib.Path, package: dict):
    """Read and require the exact closed package metadata from one image."""
    runtime = shutil.which("apptainer") or shutil.which("singularity")
    if not runtime:
        raise DeploymentError("no Apptainer or Singularity runtime is available")
    result = _capture([runtime, "exec", str(image), "cat", "/etc/mkchad/container-tools-package.json"], timeout=40)
    if result.returncode or len(result.stdout) > 4096:
        raise DeploymentError("image container-tools metadata is unavailable")
    try:
        metadata = json.loads(result.stdout, object_pairs_hook=_reject_duplicates)
    except (json.JSONDecodeError, DeploymentError) as exc:
        raise DeploymentError("image container-tools metadata is malformed") from exc
    try:
        identity = _package_identity(metadata, role="container")
    except DeploymentError as exc:
        raise DeploymentError("image container-tools metadata is malformed") from exc
    if identity != package:
        raise DeploymentError("image container-tools metadata does not match the selected identity")
    return identity


def _existing(destination: pathlib.Path, component: dict, staged: pathlib.Path) -> dict:
    """Refuse dirty or wrong-origin targets and classify their manifest compliance."""
    if not destination.is_dir() or destination.is_symlink():
        raise DeploymentError(f"destination is not the expected Git checkout: {destination}")
    if pathlib.Path(_git(["rev-parse", "--show-toplevel"], destination).stdout.strip()) != destination:
        raise DeploymentError(f"destination is not its Git top level: {destination}")
    origin = _git(["config", "--get", "remote.origin.url"], destination).stdout.strip()
    if origin.rstrip("/").removesuffix(".git") != component["repository"].rstrip("/").removesuffix(".git"):
        raise DeploymentError("existing checkout has wrong origin")
    preserved = set(component.get("preserved_paths", ()))
    for entry in _git(["status", "--porcelain=v1", "-z", "--ignored=matching", "--untracked-files=all"], destination).stdout.split("\0"):
        if entry:
            path = entry[3:]
            if component["name"] == "msk_containers" and path.rstrip("/") == "container_tools":
                continue
            if path not in preserved or _git(["check-ignore", "--quiet", "--", path], staged, check=False).returncode:
                raise DeploymentError(f"existing checkout has local work: {path}")
            if _git(["-C", str(staged), "cat-file", "-e", f"{component['commit']}:{path}"], check=False).returncode == 0:
                raise DeploymentError(f"desired component tree owns preserved path: {path}")
    head = _git(["rev-parse", "HEAD"], destination).stdout.strip()
    return {"kind": "existing", "head": head, "compliant": head == component["commit"]}


def _run_action(action: dict, component: dict, image: pathlib.Path | None, recovery: pathlib.Path, checkout: pathlib.Path, packages: dict | None = None, archive: pathlib.Path | None = None):
    """Run one fixed action argv from its pinned checkout with a finite timeout."""
    values = {"recovery_dir": str(recovery), "staged_image": str(image) if image is not None else "", "component_revision": component["commit"]}
    if packages is not None and archive is not None:
        host = packages["host"]
        values.update({
            "staged_container_tools_host_package": str(archive),
            "container_tools_host_sha256": host["sha256"],
            "container_tools_host_version": host["version"],
            "container_tools_host_source_commit": host["source_commit"],
            "container_tools_host_prefix": str(_roots()["home"] / ".local"),
            "container_tools_host_libc": host["libc"],
            "native_architecture": host["architecture"],
        })
    argv = [str(checkout / action["path"]), *(item if isinstance(item, str) else values[item["token"]] for item in action["args"])]
    environment = os.environ.copy()
    environment.pop("MKCHAD_TRUST_GROUP_WRITABLE_ROOTS", None)
    if action["id"] in {"host-tools-preflight", "host-tools-install", "host-tools-verify", "opencode-db-preflight", "opencode-db-install", "opencode-db-verify"}:
        environment["MKCHAD_TRUST_GROUP_WRITABLE_ROOTS"] = "1"
    try:
        process = subprocess.Popen(argv, cwd=checkout, env=environment, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True, close_fds=True)
    except OSError as exc:
        raise DeploymentError(f"action could not start: {component['name']}:{action['id']}") from exc
    assert process.stdout is not None and process.stderr is not None
    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ, sys.stdout)
    selector.register(process.stderr, selectors.EVENT_READ, sys.stderr)
    deadline = time.monotonic() + 300
    try:
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                _terminate_group(process)
                raise DeploymentError(f"action timed out: {action['id']}")
            for key, _ in selector.select(min(0.1, remaining)):
                data = os.read(key.fileobj.fileno(), 65536)
                if not data:
                    selector.unregister(key.fileobj)
                    continue
                key.data.write(REDACT.sub(r"\1=<redacted>", data.decode("utf-8", errors="replace")))
                key.data.flush()
        process.wait(timeout=max(0.1, deadline - time.monotonic()))
    except KeyboardInterrupt:
        _terminate_group(process)
        raise
    except subprocess.TimeoutExpired as exc:
        _terminate_group(process)
        raise DeploymentError(f"action timed out: {action['id']}") from exc
    finally:
        selector.close()
        process.stdout.close()
        process.stderr.close()
    if process.returncode:
        raise DeploymentError(f"action failed: {component['name']}:{action['id']}")


def _installer_sources(msk_checkout: pathlib.Path) -> list[pathlib.Path]:
    """Return the exact host-tool files owned by the pinned installer checkout."""
    bin_dir = msk_checkout / "nvim" / "bin"
    paths = set(bin_dir.glob("*.sh"))
    paths.update(path for path in (bin_dir / name for name in ("nvim", "nvim_shell", "nvim_clear_data", "nvim_clear_npm_global", "mkchad", "mkchad-container-bootstrap", "mkchad-status-host-evidence", "mkchad-opencode-server-image", "mkchad-opencode-server", "install_nvim_container")) if path.is_file())
    return sorted(paths, key=lambda path: path.name)


def _managed_paths(roots: dict[str, pathlib.Path], msk_checkout: pathlib.Path, caller_image: pathlib.Path | None) -> list[pathlib.Path]:
    """Return pre-existing installer-owned paths, excluding the caller artifact."""
    paths = [roots["home"] / ".local" / "bin" / source.name for source in _installer_sources(msk_checkout)]
    paths.append(roots["home"] / ".local" / "bin" / "opencode-db")
    container_dir = pathlib.Path(os.environ.get("NVIM_CONTAINER_DIR", roots["home"] / "containers"))
    paths.extend((container_dir / "neovim.sif", container_dir / "neovim.previous.sif"))
    if container_dir.exists():
        paths.extend(container_dir.glob("neovim_*_g*_*_*.sif"))
    if caller_image is not None:
        caller_image = pathlib.Path(os.path.abspath(caller_image))
        paths = [path for path in paths if pathlib.Path(os.path.abspath(path)) != caller_image]
    return paths


def _host_tool_mismatch(msk_checkout: pathlib.Path, home: pathlib.Path) -> str | None:
    """Return the first installer-owned host tool that differs from its source."""
    for source in _installer_sources(msk_checkout):
        target = home / ".local" / "bin" / source.name
        if not target.is_file() or stat.S_IMODE(target.stat().st_mode) != 0o755 or _digest(target) != _digest(source):
            return source.name
    return None


def _host_tool_mismatches(staged: dict[str, tuple[pathlib.Path, pathlib.Path]], home: pathlib.Path) -> dict[str, str | None]:
    """Return independent managed-launcher compliance observations by component."""
    launcher = home / ".local" / "bin" / "opencode-db"
    source = staged["opencode-db"][1] / "bin" / "opencode-db"
    opencode_db_mismatch = "opencode-db" if launcher.is_symlink() or not launcher.is_file() or stat.S_IMODE(launcher.stat().st_mode) != 0o755 or _digest(launcher) != _digest(source) else None
    return {"msk_containers": _host_tool_mismatch(staged["msk_containers"][1], home), "opencode-db": opencode_db_mismatch}


def _active_image_observation(home: pathlib.Path) -> dict:
    """Classify the active selector without following unsafe image paths."""
    container_dir = pathlib.Path(os.environ.get("NVIM_CONTAINER_DIR", home / "containers"))
    selector = container_dir / "neovim.sif"
    try:
        os.lstat(selector)
    except FileNotFoundError:
        return {"state": "absent", "digest": None, "path": None}
    except OSError:
        return {"state": "unsafe", "digest": None, "path": None}
    try:
        selector_before = os.lstat(selector)
        if not stat.S_ISLNK(selector_before.st_mode) or not stat.S_ISDIR(os.lstat(container_dir).st_mode):
            raise OSError
        target_value = os.readlink(selector)
        target = pathlib.PurePosixPath(target_value)
        if target.is_absolute() or not target.parts or any(part in {".", ".."} for part in target.parts):
            raise OSError
        candidate = container_dir.joinpath(*target.parts)
        for parent in (container_dir, *candidate.parents):
            if parent == container_dir.parent:
                break
            entry = os.lstat(parent)
            if parent != candidate and (not stat.S_ISDIR(entry.st_mode) or stat.S_ISLNK(entry.st_mode)):
                raise OSError
        if not stat.S_ISREG(os.lstat(candidate).st_mode):
            raise OSError
        digest = _stable_regular_digest(candidate, "active image")
        selector_after = os.lstat(selector)
        if _stat_identity(selector_before) != _stat_identity(selector_after) or os.readlink(selector) != target_value:
            raise OSError
        return {"state": "valid", "digest": digest, "path": candidate}
    except (OSError, DeploymentError):
        return {"state": "unsafe", "digest": None, "path": None}


def _active_image_digest(home: pathlib.Path) -> str:
    """Return the digest of a safely selected active image or raise OSError."""
    observation = _active_image_observation(home)
    if observation["state"] != "valid":
        raise OSError("active image is unavailable")
    return observation["digest"]


def _consume_caller_image(source: pathlib.Path, roots: dict[str, pathlib.Path], expected_digest: str):
    """Remove a co-located caller artifact after its managed image is verified."""
    source = pathlib.Path(os.path.abspath(source))
    container_dir = pathlib.Path(os.environ.get("NVIM_CONTAINER_DIR", roots["home"] / "containers"))
    try:
        container_dir = container_dir.resolve(strict=True)
        if source.parent.resolve(strict=True) != container_dir:
            return
        if source.is_symlink() or not source.is_file() or _digest(source) != expected_digest:
            raise DeploymentError("caller image changed before it could be consumed")
        active = (container_dir / os.readlink(container_dir / "neovim.sif")).resolve(strict=True)
        if source.resolve(strict=True) != active:
            source.unlink()
    except OSError as exc:
        raise DeploymentError("caller image could not be consumed after verified installation") from exc


def _result(release: dict, roots: dict[str, pathlib.Path], artifact: dict, packages: dict[str, dict], package_observations: dict[str, dict | None], run_id: str, tag: str, started_at: str, log_path: pathlib.Path, actions: list[dict], outcome: str, phase: str, failed_step: str | None, diagnostic: str, observed_image: str | None) -> dict:
    """Build the complete, run-bound terminal result without command data."""
    components = []
    for component in release["components"]:
        observed = None
        if component["disposition"] == "deployed":
            with contextlib.suppress(Exception):
                observed = _git(["rev-parse", "HEAD"], _destination(roots, component)).stdout.strip()
        components.append({"name": component["name"], "disposition": component["disposition"], "requested_commit": component["commit"], "observed_commit": observed})
    expected = [packages["host"], packages["container"]]
    observed = [package_observations["host"], package_observations["container"]]
    return {"schema": 2, "run_id": run_id, "release": tag, "started_at": started_at, "completed_at": _timestamp(), "outcome": outcome, "phase": phase, "components": components, "artifact": {"architecture": artifact["architecture"], "expected_sha256": artifact["sha256"], "observed_sha256": observed_image}, "packages": {"expected": expected, "observed": observed}, "actions": actions, "configuration_handoff": {"state": "configuration-pending", "contract_version": 1, "guidance": release["configuration"]["guidance"], "components": [{"name": item["name"], "repository": item["repository"], "commit": item["commit"]} for item in release["components"] if item["disposition"] == "configuration-owned"]}, "failed_step": failed_step, "diagnostic": REDACT.sub(r"\1=<redacted>", diagnostic)[:4096], "log_path": str(log_path)}


def developer_result(admission: dict, *, actions: list[dict], artifact: dict, image: dict, packages: dict[str, dict], package_observations: dict[str, dict | None], run_id: str, started_at: str, log_path: pathlib.Path, outcome: str, phase: str, failed_step: str | None, diagnostic: str) -> dict:
    """Build the mode-tagged developer result from local-workspace evidence.

    Args:
        admission: Selected parent and four child source identities.
        actions: Current deployment action-result records.
        artifact: Selected architecture metadata.
        image: Current image decision and digest observations.
        run_id: Invocation UUID.
        started_at: UTC start timestamp.
        log_path: Absolute run-log path.
        outcome: Terminal deployment outcome.
        phase: Last deployment phase reached.
        failed_step: Failed action/checkout identifier, when any.
        diagnostic: Bounded terminal diagnostic.

    Returns:
        A mode-tagged developer result with selected/observed component commits.

    The result intentionally has no release schema. Building it reads deployed
    checkout HEADs when available and does not mutate deployment state.
    """
    roots, profile = _roots(), _developer_components(admission["commits"])
    components = []
    for name in DEPLOYED_COMPONENTS:
        observed = None
        with contextlib.suppress(Exception):
            observed = _git(["rev-parse", "HEAD"], _destination(roots, profile[name])).stdout.strip()
        components.append({"name": name, "requested_commit": admission["commits"][name], "observed_commit": observed})
    return {
        "mode": "developer", "run_id": run_id, "workspace": admission["parent"], "started_at": started_at, "completed_at": _timestamp(),
        "outcome": outcome, "phase": phase, "components": components,
        "artifact": {"architecture": artifact["architecture"], "requested_sha256": image["supplied_sha256"], "observed_sha256": image["active_sha256"]},
        "image": image, "packages": {"expected": [packages["host"], packages["container"]], "observed": [package_observations["host"], package_observations["container"]]},
        "actions": actions, "failed_step": failed_step, "diagnostic": REDACT.sub(r"\1=<redacted>", diagnostic)[:4096], "log_path": str(log_path),
    }


def _timestamp() -> str:
    """Return a result-schema UTC timestamp."""
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _publish(path: pathlib.Path, document: dict, *, validate=True):
    """Atomically replace the launcher-owned result path for this admitted run."""
    if validate:
        validate_result_v2_document(document)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}-{uuid.uuid4().hex}")
    temporary.write_text(json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def _execute_components(deployed: dict[str, dict], artifact: dict, image: pathlib.Path | None, packages: dict[str, dict], host_package: pathlib.Path, *, developer: bool, log_path: pathlib.Path, result_path: pathlib.Path, run_id: str, replace_partial: bool, stage_sources, recheck_sources, result_builder) -> int:
    """Run the shared target procedure from already-selected component sources.

    Source selection differs between release and developer modes; destination
    validation, lifecycle checks, apply order, and failure handling do not.
    """
    started_at, roots, current, phase, failure_step = _timestamp(), _roots(), None, "preflight", "preflight"
    action_component_order = ("container_tools", "msk_containers", "opencode-db", "mkchad")
    action_specs = {
        (component["name"], action["id"]): action
        for name in action_component_order if name in deployed
        for component in (deployed[name],)
        for action in component["actions"]
    }
    image_action_keys = {
        key
        for key, action in action_specs.items()
        if any(isinstance(argument, dict) and argument.get("token") == "staged_image" for argument in action["args"])
    }
    actions = [
        {"component": key[0], "id": key[1], "phase": action["phase"], "status": "pending"}
        for key, action in action_specs.items()
        if image is not None or key not in image_action_keys
    ]
    action_results = {(item["component"], item["id"]): item for item in actions}
    image_info = {"choice": "omitted" if image is None else "install", "supplied_sha256": None, "active_sha256": None, "decision": "preserved" if image is None else "pending"}
    package_observations = {"host": None, "container": None}
    staging = pathlib.Path(tempfile.mkdtemp(prefix="mkchad-deploy-"))
    try:
        staged_package, package_observations["host"] = _stage_package(host_package, staging, packages["host"])
        if image is not None:
            image_info["supplied_sha256"] = _stable_regular_digest(image, "caller image")
            if not developer and image_info["supplied_sha256"] != artifact["sha256"]:
                raise DeploymentError("caller image digest does not match the release artifact")
        staged = stage_sources(deployed, staging)
        for name, component in deployed.items():
            _validate_actions(component, staged[name][1])
        recheck_sources()
        plans = {
            name: (
                _destination(roots, component),
                _existing(_destination(roots, component), component, staged[name][1])
                if _destination(roots, component).exists() or _destination(roots, component).is_symlink()
                else {"kind": "absent", "compliant": False},
            )
            for name, component in deployed.items()
        }
        with _lock(roots["xdg_state_home"] / "mkchad" / "deployment.lock"):
            active_image = _active_image_observation(roots["home"])
            image_info["active_sha256"] = active_image["digest"]
            if image is not None:
                if active_image["state"] == "unsafe":
                    image_info["decision"] = "blocked-unsafe-active"
                    raise DeploymentError("active image selector is unsafe or unavailable")
                if active_image["state"] == "valid" and active_image["digest"] == image_info["supplied_sha256"]:
                    image_info.update(choice="reused", decision="reused-active")
                else:
                    image_info["decision"] = "install"
            if developer and image is not None and image_info["choice"] == "install":
                container_dir = pathlib.Path(os.environ.get("NVIM_CONTAINER_DIR", roots["home"] / "containers"))
                if container_dir.exists() and pathlib.Path(os.path.abspath(image)).parent.resolve(strict=True) == container_dir.resolve(strict=True) and MANAGED_IMAGE.fullmatch(image.name):
                    raise DeploymentError("the image installer could prune the developer caller image; move it outside NVIM_CONTAINER_DIR or activate the identical image first")
            lifecycle = roots["home"] / ".local" / "bin" / "mkchad-opencode-server"
            active = False
            if lifecycle.exists():
                status = _capture([str(lifecycle), "status", "--json"], env={**os.environ, "HOME": str(roots["home"]), "XDG_CONFIG_HOME": str(roots["xdg_config_home"]), "XDG_DATA_HOME": str(roots["xdg_data_home"]), "XDG_STATE_HOME": str(roots["xdg_state_home"])}, timeout=40)
                payload = json.loads(status.stdout) if not status.returncode else None
                if not isinstance(payload, dict) or payload.get("schema") != 1 or payload.get("ok") is not True or payload.get("command") != "status" or payload.get("status") not in {"inactive", "healthy", "degraded", "blocked", "starting", "stopping", "failed"}:
                    raise DeploymentError("managed service status is unavailable or malformed")
                active = payload["status"] != "inactive"
            else:
                partial_paths = sorted(
                    (path for path in _managed_paths(roots, staged["msk_containers"][1], image) if path.exists() or path.is_symlink()),
                    key=str,
                )
                if partial_paths:
                    detail = ", ".join(map(str, partial_paths))
                    if not replace_partial:
                        raise DeploymentError(
                            f"partial target has no lifecycle command at {lifecycle}; detected pre-existing managed paths: {detail}; "
                            "rerun with --replace-partial only after confirming the managed service is inactive"
                        )
                    print(f"mkchad-deploy: --replace-partial asserted the managed service is inactive; replacing: {detail}", file=sys.stderr)
            recovery = staging / "recovery"
            recovery.mkdir(mode=0o700)
            host_mismatches = _host_tool_mismatches(staged, roots["home"])
            checkout_mutation = any(not plan["compliant"] for _, plan in plans.values())
            mutation_required = checkout_mutation or any(mismatch is not None for mismatch in host_mismatches.values()) or image_info["choice"] == "install"
            if active and mutation_required:
                raise DeploymentError("managed service is active and target is not completely compliant")
            staged_image = None
            if image_info["choice"] == "install":
                assert image is not None and image_info["supplied_sha256"] is not None
                staged_image = _stage_image(image, staging, artifact, image_info["supplied_sha256"])
                _runtime_image(staged_image, artifact["architecture"])
                package_observations["container"] = _runtime_image_package_identity(staged_image, packages["container"])
            elif image_info["choice"] == "reused":
                package_observations["container"] = _runtime_image_package_identity(active_image["path"], packages["container"])
            if image_info["choice"] == "reused":
                for item in actions:
                    if (item["component"], item["id"]) in image_action_keys:
                        item["status"] = "skipped-compliant"
            for name in action_component_order:
                if name not in deployed:
                    continue
                component = deployed[name]
                for action in component["actions"]:
                    key = component["name"], action["id"]
                    if action["phase"] == "preflight" and (key not in image_action_keys or image_info["choice"] == "install"):
                        current = action_results[key]
                        _run_action(action, component, staged_image, recovery, staged[component["name"]][1], packages, staged_package)
                        current["status"] = "succeeded"
                        current = None
            phase = "apply"
            for name in DEPLOYED_COMPONENTS:
                failure_step = f"checkout:{name}"
                component, (destination, plan) = deployed[name], plans[name]
                if plan["kind"] == "existing":
                    current_plan = _existing(destination, component, staged[name][1])
                    if current_plan != plan:
                        raise DeploymentError(f"checkout changed after complete preflight: {name}")
                    if plan["compliant"]:
                        continue
                    _git(["fetch", "--no-tags", str(staged[name][0]), component["commit"]], destination)
                    _git(["checkout", "--detach", "--no-recurse-submodules", component["commit"]], destination)
                else:
                    if destination.exists() or destination.is_symlink():
                        if name == "container_tools" and destination.is_dir() and not destination.is_symlink() and not any(destination.iterdir()):
                            destination.rmdir()
                        else:
                            raise DeploymentError(f"destination appeared after complete preflight: {destination}")
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    temporary = pathlib.Path(tempfile.mkdtemp(prefix=f".{name}-", dir=destination.parent))
                    try:
                        _git(["init", str(temporary)])
                        _git(["-C", str(temporary), "fetch", "--no-tags", str(staged[name][0]), component["commit"]])
                        _git(["-C", str(temporary), "remote", "add", "origin", component["repository"]])
                        _git(["-C", str(temporary), "checkout", "--detach", "--no-recurse-submodules", component["commit"]])
                        if destination.exists() or destination.is_symlink():
                            raise DeploymentError(f"destination appeared before final rename: {destination}")
                        os.rename(temporary, destination)
                    finally:
                        if temporary.exists():
                            shutil.rmtree(temporary)
            for item in actions:
                if item["phase"] == "apply":
                    key = item["component"], item["id"]
                    component = deployed[item["component"]]
                    action = action_specs[key]
                    if item["status"] == "skipped-compliant":
                        continue
                    if key in image_action_keys and image_info["choice"] != "install":
                        item["status"] = "skipped-compliant"
                    elif key not in image_action_keys and item["id"] != "container-tools-package-install" and host_mismatches.get(item["component"]) is None:
                        item["status"] = "skipped-compliant"
                    else:
                        current = item
                        failure_step = f"{item['component']}:{item['id']}"
                        _run_action(action, component, staged_image, recovery, plans[item["component"]][0], packages, staged_package)
                        item["status"] = "succeeded"
                        current = None
            phase = "verify"
            for item in actions:
                if item["phase"] == "verify":
                    current = item
                    failure_step = f"{item['component']}:{item['id']}"
                    component = deployed[item["component"]]
                    action = action_specs[(item["component"], item["id"])]
                    _run_action(action, component, staged_image, recovery, plans[item["component"]][0], packages, staged_package)
                    item["status"] = "succeeded"
                    current = None
            for name, component in deployed.items():
                failure_step = f"checkout-verification:{name}"
                if _existing(plans[name][0], component, staged[name][1])["head"] != component["commit"]:
                    raise DeploymentError(f"deployed checkout is not at its selected commit: {name}")
            failure_step = "host-tool-verification"
            for name, mismatch in _host_tool_mismatches(staged, roots["home"]).items():
                if mismatch is not None:
                    raise DeploymentError(f"host-tool verification failed: {name}:{mismatch}")
            if image_info["choice"] == "install":
                failure_step = "installed-image-verification"
                if _active_image_digest(roots["home"]) != image_info["supplied_sha256"]:
                    raise DeploymentError("installed image digest does not match the selected caller image")
                image_info["active_sha256"] = image_info["supplied_sha256"]
                if not developer:
                    failure_step = "caller-image-consumption"
                    _consume_caller_image(image, roots, artifact["sha256"])
            diagnostic = "developer deployment verified" if developer else "deployment verified; configuration is pending"
            _publish(result_path, result_builder(roots, actions, "succeeded", phase, None, diagnostic, started_at, image_info, package_observations), validate=not developer)
            return 0
    except KeyboardInterrupt:
        image_info["active_sha256"] = _active_image_observation(roots["home"])["digest"]
        _publish(result_path, result_builder(roots, actions, "interrupted", phase, f"{current['component']}:{current['id']}" if current else failure_step, "deployment interrupted; repair or inspect and rerun", started_at, image_info, package_observations), validate=not developer)
        return 2
    except (DeploymentError, OSError, subprocess.SubprocessError, json.JSONDecodeError) as exc:
        if current is not None:
            current["status"] = "failed"
        image_info["active_sha256"] = _active_image_observation(roots["home"])["digest"]
        _publish(result_path, result_builder(roots, actions, "failed", phase, f"{current['component']}:{current['id']}" if current else failure_step, str(exc), started_at, image_info, package_observations), validate=not developer)
        print("mkchad-deploy: " + REDACT.sub(r"\1=<redacted>", str(exc)), file=sys.stderr)
        return 2
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def execute_deployment(repo: pathlib.Path, image: pathlib.Path, host_package: pathlib.Path, *, release_tag: str, log_path: pathlib.Path, result_path: pathlib.Path, run_id: str, replace_partial: bool = False) -> int:
    """Run one manifest-authorized release deployment synchronously.

    Args:
        repo: Checked-out release repository containing ``deploy/release.json``.
        image: Caller-supplied image artifact.
        host_package: Caller-supplied host container-tools package archive.
        release_tag: Validated release identity for the result.
        log_path: Absolute launcher-owned log path.
        result_path: Path receiving the terminal schema-2 result.
        run_id: Invocation UUID.
        replace_partial: Whether the operator asserted a legacy partial target
            without lifecycle tooling is inactive.

    Returns:
        Zero on verified success, otherwise two after publishing failure state.

    Raises:
        DeploymentError: If launcher handoff or manifest selection is invalid.

    The function stages exact repositories, may update deployed checkouts and
    host tools, and atomically publishes a terminal result.
    """
    if not TAG.fullmatch(release_tag) or ".." in release_tag or not UUID.fullmatch(run_id):
        raise DeploymentError("invalid launcher handoff")
    manifest = validate_release_v2_document(_load(repo / "deploy" / "release.json", "release manifest"))
    architecture = _native_architecture()
    artifact = next((item for item in manifest["artifacts"]["nvim-image"] if item["architecture"] == architecture), None)
    if artifact is None:
        raise DeploymentError("release has no image for this architecture")
    deployed = {item["name"]: dict(item) for item in manifest["components"] if item["disposition"] == "deployed"}
    packages = _select_container_tools_packages(manifest["artifacts"], architecture)
    return _execute_components(
        deployed, artifact, image, packages, host_package, developer=False, log_path=log_path, result_path=result_path, run_id=run_id, replace_partial=replace_partial,
        stage_sources=_stage_release_sources, recheck_sources=lambda: None,
        result_builder=lambda roots, actions, outcome, phase, failed_step, diagnostic, started_at, image_info, package_observations: _result(manifest, roots, artifact, packages, package_observations, run_id, release_tag, started_at, log_path, actions, outcome, phase, failed_step, diagnostic, image_info["active_sha256"]),
    )


def execute_developer_deployment(workspace: pathlib.Path, image: pathlib.Path | None, host_package: pathlib.Path, *, host_package_identity: dict, log_path: pathlib.Path, result_path: pathlib.Path, run_id: str, replace_partial: bool = False) -> int:
    """Deploy clean local child commits through the regular target procedure.

    Args:
        workspace: Canonical parent workspace with four clean child checkouts.
        image: Optional caller-owned image; omission preserves active image state.
        host_package: Caller-owned package archive selected for host installation.
        host_package_identity: Independently verified closed identity for the archive.
        log_path: Absolute launcher-owned log path.
        result_path: Path receiving the terminal developer result.
        run_id: Invocation UUID.
        replace_partial: Whether the operator asserted a legacy partial target
            without lifecycle tooling is inactive.

    Returns:
        Zero on verified success, otherwise two after publishing failure state.

    Raises:
        DeploymentError: If source admission, architecture, or handoff fails.

    Local commits are staged without consulting origins or release metadata.
    The procedure may update deployed checkouts and host tools; a supplied image
    is never consumed from its caller path.
    """
    if not UUID.fullmatch(run_id):
        raise DeploymentError("invalid developer handoff")
    admission = admit_developer_workspace(workspace)
    architecture = _native_architecture()
    if architecture not in ARCHITECTURES:
        raise DeploymentError("developer deployment has no image for this architecture")
    artifact = {"architecture": architecture, "sha256": None}
    package = dict(host_package_identity)
    if package.get("role") != "host" or package.get("architecture") != architecture or package.get("source_commit") != admission["commits"]["container_tools"]:
        raise DeploymentError("developer container-tools package does not match the pinned child")
    if not PACKAGE_VERSION.fullmatch(package.get("version", "")) or not SHA256.fullmatch(package.get("sha256", "")) or package.get("libc") not in {"musl", "glibc"}:
        raise DeploymentError("invalid developer container-tools package identity")
    package.update(product_major=int(package["version"].split(".", 1)[0]), source_filename_class="container-tools-package")
    packages = {"host": package, "container": {**package, "role": "container"}}
    deployed = _developer_components(admission["commits"])
    return _execute_components(
        deployed, artifact, image, packages, host_package, developer=True, log_path=log_path, result_path=result_path, run_id=run_id, replace_partial=replace_partial,
        stage_sources=lambda _components, staging: stage_developer_sources(admission, staging), recheck_sources=lambda: recheck_developer_workspace(admission),
        result_builder=lambda _roots, actions, outcome, phase, failed_step, diagnostic, started, image_info, package_observations: developer_result(admission, actions=actions, artifact=artifact, image=image_info, packages=packages, package_observations=package_observations, run_id=run_id, started_at=started, log_path=log_path, outcome=outcome, phase=phase, failed_step=failed_step, diagnostic=diagnostic),
    )


def main(argv: list[str] | None = None) -> int:
    """Run the internal launcher handoff CLI.

    Args:
        argv: Optional CLI arguments; process arguments are used when omitted.

    Returns:
        The synchronous deployment status, or two for invalid deployment input.

    Side effects:
        Performs the deployment and writes the launcher-selected result path.
    """
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0], allow_abbrev=False)
    parser.add_argument("deploy")
    parser.add_argument("--release", required=True)
    parser.add_argument("--image", type=pathlib.Path, required=True)
    parser.add_argument("--container-tools-host-package", type=pathlib.Path, required=True)
    parser.add_argument("--replace-partial", action="store_true")
    parser.add_argument("--log-path", type=pathlib.Path, required=True)
    parser.add_argument("--result-path", type=pathlib.Path, required=True)
    parser.add_argument("--run-id", required=True)
    args = parser.parse_args(argv)
    try:
        if args.deploy != "deploy":
            parser.error("only the deploy handoff is supported")
        return execute_deployment(pathlib.Path.cwd(), args.image, args.container_tools_host_package, release_tag=args.release, log_path=args.log_path, result_path=args.result_path, run_id=args.run_id, replace_partial=args.replace_partial)
    except DeploymentError as exc:
        print("mkchad-deploy: " + str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
