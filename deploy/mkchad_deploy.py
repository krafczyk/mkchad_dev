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
import uuid


SHA1 = re.compile(r"^[0-9a-f]{40}$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")
TAG = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]*$")
COMPONENTS = {
    "msk_containers": "deployed",
    "container_tools": "deployed",
    "mkchad": "deployed",
    "opencode": "embedded",
    "opencode.nvim": "configuration-owned",
    "opencode-project-reload": "configuration-owned",
    "compound-engineering": "configuration-owned",
    "opencode_sprint_loop": "disabled",
}
CONFIGURATION_COMPONENTS = frozenset({"opencode.nvim", "opencode-project-reload", "compound-engineering"})
PHASES = ("preflight", "apply", "verify")
TOKENS = frozenset({"recovery_dir", "staged_image", "component_revision"})
ARCHITECTURES = frozenset({"x86_64", "aarch64", "ppc64le"})
REDACT = re.compile(r"(?i)\b([A-Za-z0-9_-]*(?:password|token|secret|api[_-]?key)[A-Za-z0-9_-]*)=[^\s]+")


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
    """Validate the closed schema-2 manifest without reading Git links or targets."""
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
                    elif not isinstance(argument, dict) or set(argument) != {"token"} or argument.get("token") not in TOKENS:
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
    if not isinstance(artifacts, dict) or set(artifacts) != {"nvim-image"} or not isinstance(artifacts["nvim-image"], list) or not artifacts["nvim-image"]:
        raise DeploymentError("malformed artifacts")
    architectures = set()
    for artifact in artifacts["nvim-image"]:
        if not isinstance(artifact, dict) or set(artifact) != {"architecture", "sha256", "source_filename_class"} or artifact.get("architecture") not in ARCHITECTURES or artifact["architecture"] in architectures or not SHA256.fullmatch(artifact.get("sha256", "")) or artifact.get("source_filename_class") != "raw-nvim-container":
            raise DeploymentError("invalid image artifact")
        architectures.add(artifact["architecture"])
    configuration = document.get("configuration")
    if not isinstance(configuration, dict) or set(configuration) != {"contract_version", "guidance"} or configuration.get("contract_version") != 1:
        raise DeploymentError("malformed configuration handoff")
    _relative(configuration.get("guidance"), "configuration guidance")
    return document


def validate_result_v1_document(document: dict) -> dict:
    """Validate the closed terminal-result shape published for one completed run."""
    fields = {"schema", "run_id", "release", "started_at", "completed_at", "outcome", "phase", "components", "artifact", "actions", "configuration_handoff", "failed_step", "diagnostic", "log_path"}
    if not isinstance(document, dict) or set(document) != fields or document.get("schema") != 1 or not UUID.fullmatch(document.get("run_id", "")):
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
    actions = document.get("actions")
    if not isinstance(actions, list):
        raise DeploymentError("malformed terminal result")
    action_ids = set()
    for action in actions:
        if not isinstance(action, dict) or set(action) != {"component", "id", "phase", "status"}:
            raise DeploymentError("malformed terminal result action")
        key = (action.get("component"), action.get("id"))
        if action.get("component") not in COMPONENTS or not isinstance(action.get("id"), str) or not re.fullmatch(r"[a-z][a-z0-9-]*", action["id"]) or action.get("phase") not in PHASES or action.get("status") not in {"pending", "succeeded", "skipped-compliant", "failed"} or key in action_ids:
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
    """Terminate one live subprocess group and wait for its leader."""
    with contextlib.suppress(ProcessLookupError):
        os.killpg(process.pid, signal.SIGTERM)
    try:
        process.wait(timeout=5)
        return
    except subprocess.TimeoutExpired:
        pass
    with contextlib.suppress(ProcessLookupError):
        os.killpg(process.pid, signal.SIGKILL)
    process.wait(timeout=5)


def _capture(command: list[str], *, cwd: pathlib.Path | None = None, env: dict[str, str] | None = None, timeout: float = 300) -> subprocess.CompletedProcess:
    """Run one bounded command in a new process group and capture its output."""
    try:
        process = subprocess.Popen(command, cwd=cwd, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, start_new_session=True, close_fds=True)
        stdout, stderr = process.communicate(timeout=timeout)
    except KeyboardInterrupt:
        if "process" in locals():
            _terminate_group(process)
        raise
    except subprocess.TimeoutExpired as exc:
        _terminate_group(process)
        raise DeploymentError(f"command timed out after {timeout:g} seconds: {command[0]}") from exc
    except OSError as exc:
        raise DeploymentError(f"command could not start: {command[0]}") from exc
    return subprocess.CompletedProcess(command, process.returncode, stdout, stderr)


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


def _stage(component: dict, staging: pathlib.Path) -> tuple[pathlib.Path, pathlib.Path]:
    """Fetch one manifest-pinned deployed component into disposable staging."""
    bare, checkout = staging / f"{component['name']}.git", staging / component["name"]
    _git(["init", "--bare", str(bare)])
    _git(["-C", str(bare), "fetch", "--no-tags", component["repository"], component["commit"]])
    if _git(["-C", str(bare), "cat-file", "-e", component["commit"] + "^{commit}"], check=False).returncode:
        raise DeploymentError(f"staged component commit is unavailable: {component['name']}")
    _git(["-C", str(bare), "worktree", "add", "--detach", str(checkout), component["commit"]])
    return bare, checkout


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
        if not match or match.group(3) != action["path"] or not stat.S_ISREG(entry.st_mode) or stat.S_ISLNK(entry.st_mode) or not entry.st_mode & stat.S_IXUSR or not int(match.group(1), 8) & stat.S_IXUSR:
            raise DeploymentError(f"action is not an executable regular file: {action['id']}")
        if _git(["-C", str(checkout), "hash-object", "--no-filters", str(path)]).stdout.strip() != match.group(2):
            raise DeploymentError(f"action does not match its pinned blob: {action['id']}")


def _stage_image(source: pathlib.Path, staging: pathlib.Path, artifact: dict, revision: str) -> pathlib.Path:
    """Copy a matching caller image to the installer-required raw-image name."""
    raw_arch = {"x86_64": "x86", "aarch64": "aarch64", "ppc64le": "ppc64le"}[artifact["architecture"]]
    target = staging / f"neovim_0.0.0_g{revision}_{artifact['sha256']}_{raw_arch}.sif"
    if not source.is_file() or source.is_symlink() or _digest(source) != artifact["sha256"]:
        raise DeploymentError("caller image digest does not match the release artifact")
    shutil.copyfile(source, target)
    return target


def _runtime_image(image: pathlib.Path, architecture: str):
    """Verify the staged image architecture with Apptainer or Singularity."""
    runtime = shutil.which("apptainer") or shutil.which("singularity")
    if not runtime:
        raise DeploymentError("no Apptainer or Singularity runtime is available")
    result = _capture([runtime, "exec", str(image), "uname", "-m"], timeout=40)
    observed = {"amd64": "x86_64", "x86": "x86_64", "arm64": "aarch64"}.get(result.stdout.strip().lower(), result.stdout.strip().lower())
    if result.returncode or observed != architecture:
        raise DeploymentError("staged image architecture does not match the release artifact")


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
            if path not in preserved or _git(["check-ignore", "--quiet", "--", path], destination, check=False).returncode:
                raise DeploymentError(f"existing checkout has local work: {path}")
            if _git(["-C", str(staged), "cat-file", "-e", f"{component['commit']}:{path}"], check=False).returncode == 0:
                raise DeploymentError(f"desired component tree owns preserved path: {path}")
    head = _git(["rev-parse", "HEAD"], destination).stdout.strip()
    return {"kind": "existing", "head": head, "compliant": head == component["commit"]}


def _run_action(action: dict, component: dict, image: pathlib.Path, recovery: pathlib.Path, checkout: pathlib.Path):
    """Run one fixed action argv from its pinned checkout with a finite timeout."""
    values = {"recovery_dir": str(recovery), "staged_image": str(image), "component_revision": component["commit"]}
    argv = [str(checkout / action["path"]), *(item if isinstance(item, str) else values[item["token"]] for item in action["args"])]
    try:
        process = subprocess.Popen(argv, cwd=checkout, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True, close_fds=True)
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
    paths = sorted((msk_checkout / "container_tools").glob("*.sh"))
    bin_dir = msk_checkout / "nvim" / "bin"
    paths.extend(path for path in (bin_dir / name for name in ("nvim", "nvim_shell", "nvim_clear_data", "mkchad", "mkchad-container-bootstrap", "mkchad-status-host-evidence", "mkchad-opencode-server-image", "mkchad-opencode-server", "install_nvim_container")) if path.is_file())
    return paths


def _managed_paths(roots: dict[str, pathlib.Path], msk_checkout: pathlib.Path) -> list[pathlib.Path]:
    """Return installer-owned paths whose presence makes a target non-fresh."""
    paths = [roots["home"] / ".local" / "bin" / source.name for source in _installer_sources(msk_checkout)]
    container_dir = pathlib.Path(os.environ.get("NVIM_CONTAINER_DIR", roots["home"] / "containers"))
    paths.extend((container_dir / "neovim.sif", container_dir / "neovim.previous.sif"))
    if container_dir.exists():
        paths.extend(container_dir.glob("neovim_*_g*_*_*.sif"))
    return paths


def _complete_compliance(plans: dict[str, tuple[pathlib.Path, dict]], roots: dict[str, pathlib.Path], msk_checkout: pathlib.Path, artifact: dict) -> bool:
    """Require all checkout, host-tool, and active-image state before verify-only use."""
    if not all(plan["compliant"] for _, plan in plans.values()):
        return False
    for source in _installer_sources(msk_checkout):
        target = roots["home"] / ".local" / "bin" / source.name
        if not target.is_file() or stat.S_IMODE(target.stat().st_mode) != 0o755 or _digest(target) != _digest(source):
            return False
    try:
        container_dir = pathlib.Path(os.environ.get("NVIM_CONTAINER_DIR", roots["home"] / "containers"))
        return _digest(container_dir / os.readlink(container_dir / "neovim.sif")) == artifact["sha256"]
    except OSError:
        return False


def _result(release: dict, roots: dict[str, pathlib.Path], artifact: dict, run_id: str, tag: str, started_at: str, log_path: pathlib.Path, actions: list[dict], outcome: str, phase: str, failed_step: str | None, diagnostic: str) -> dict:
    """Build the complete, run-bound terminal result without command data."""
    observed_image = None
    with contextlib.suppress(OSError):
        observed_image = _digest((pathlib.Path(os.environ.get("NVIM_CONTAINER_DIR", roots["home"] / "containers")) / os.readlink(pathlib.Path(os.environ.get("NVIM_CONTAINER_DIR", roots["home"] / "containers")) / "neovim.sif")))
    components = []
    for component in release["components"]:
        observed = None
        if component["disposition"] == "deployed":
            with contextlib.suppress(Exception):
                observed = _git(["rev-parse", "HEAD"], _destination(roots, component)).stdout.strip()
        components.append({"name": component["name"], "disposition": component["disposition"], "requested_commit": component["commit"], "observed_commit": observed})
    return {"schema": 1, "run_id": run_id, "release": tag, "started_at": started_at, "completed_at": _timestamp(), "outcome": outcome, "phase": phase, "components": components, "artifact": {"architecture": artifact["architecture"], "expected_sha256": artifact["sha256"], "observed_sha256": observed_image}, "actions": actions, "configuration_handoff": {"state": "configuration-pending", "contract_version": 1, "guidance": release["configuration"]["guidance"], "components": [{"name": item["name"], "repository": item["repository"], "commit": item["commit"]} for item in release["components"] if item["disposition"] == "configuration-owned"]}, "failed_step": failed_step, "diagnostic": REDACT.sub(r"\1=<redacted>", diagnostic)[:4096], "log_path": str(log_path)}


def _timestamp() -> str:
    """Return a result-schema UTC timestamp."""
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _publish(path: pathlib.Path, document: dict):
    """Atomically replace the launcher-owned result path for this admitted run."""
    validate_result_v1_document(document)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}-{uuid.uuid4().hex}")
    temporary.write_text(json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def execute_deployment(repo: pathlib.Path, image: pathlib.Path, *, release_tag: str, log_path: pathlib.Path, result_path: pathlib.Path, run_id: str) -> int:
    """Preflight, apply, verify, and report one schema-2 release deployment."""
    if not TAG.fullmatch(release_tag) or release_tag.startswith("-") or ".." in release_tag or not UUID.fullmatch(run_id):
        raise DeploymentError("invalid launcher handoff")
    started_at, roots, current, phase, failure_step = _timestamp(), _roots(), None, "preflight", "preflight"
    manifest = validate_release_v2_document(_load(repo / "deploy" / "release.json", "release manifest"))
    artifact = next((item for item in manifest["artifacts"]["nvim-image"] if item["architecture"] == {"amd64": "x86_64", "arm64": "aarch64"}.get(os.uname().machine.lower(), os.uname().machine.lower())), None)
    if artifact is None:
        raise DeploymentError("release has no image for this architecture")
    actions = [{"component": component["name"], "id": action["id"], "phase": action["phase"], "status": "pending"} for component in manifest["components"] if component["disposition"] == "deployed" for action in component["actions"]]
    staging = pathlib.Path(tempfile.mkdtemp(prefix="mkchad-deploy-"))
    try:
        deployed = {item["name"]: dict(item) for item in manifest["components"] if item["disposition"] == "deployed"}
        staged = {name: _stage(component, staging) for name, component in deployed.items()}
        nested = staged["msk_containers"][1] / "container_tools"
        if nested.exists():
            shutil.rmtree(nested)
        _git(["clone", "--no-checkout", str(staged["container_tools"][0]), str(nested)])
        _git(["-C", str(nested), "checkout", "--detach", deployed["container_tools"]["commit"]])
        for name, component in deployed.items():
            _validate_actions(component, staged[name][1])
        staged_image = _stage_image(image, staging, artifact, deployed["msk_containers"]["commit"])
        _runtime_image(staged_image, artifact["architecture"])
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
            lifecycle = roots["home"] / ".local" / "bin" / "mkchad-opencode-server"
            active = False
            if lifecycle.exists():
                status = _capture([str(lifecycle), "status", "--json"], env={**os.environ, "HOME": str(roots["home"]), "XDG_CONFIG_HOME": str(roots["xdg_config_home"]), "XDG_DATA_HOME": str(roots["xdg_data_home"]), "XDG_STATE_HOME": str(roots["xdg_state_home"])}, timeout=40)
                payload = json.loads(status.stdout) if not status.returncode else None
                if not isinstance(payload, dict) or payload.get("schema") != 1 or payload.get("ok") is not True or payload.get("command") != "status" or payload.get("status") not in {"inactive", "healthy", "degraded", "blocked", "starting", "stopping", "failed"}:
                    raise DeploymentError("managed service status is unavailable or malformed")
                active = payload["status"] != "inactive"
            elif any(destination.exists() or destination.is_symlink() for destination, _ in plans.values()) or any(path.exists() or path.is_symlink() for path in _managed_paths(roots, staged["msk_containers"][1])):
                raise DeploymentError("partial target has no lifecycle command")
            recovery = staging / "recovery"
            recovery.mkdir(mode=0o700)
            for component in deployed.values():
                for action in component["actions"]:
                    if action["phase"] == "preflight":
                        current = next(item for item in actions if item["component"] == component["name"] and item["id"] == action["id"])
                        _run_action(action, component, staged_image, recovery, staged[component["name"]][1])
                        current["status"] = "succeeded"
                        current = None
            compliant = _complete_compliance(plans, roots, staged["msk_containers"][1], artifact)
            if active and not compliant:
                raise DeploymentError("managed service is active and target is not completely compliant")
            phase = "apply"
            for name in ("msk_containers", "container_tools", "mkchad"):
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
                    if active or compliant:
                        item["status"] = "skipped-compliant"
                    else:
                        current = item
                        failure_step = f"{item['component']}:{item['id']}"
                        component = deployed[item["component"]]
                        action = next(value for value in component["actions"] if value["id"] == item["id"])
                        _run_action(action, component, staged_image, recovery, plans[item["component"]][0])
                        item["status"] = "succeeded"
                        current = None
            phase = "verify"
            for item in actions:
                if item["phase"] == "verify":
                    current = item
                    failure_step = f"{item['component']}:{item['id']}"
                    component = deployed[item["component"]]
                    action = next(value for value in component["actions"] if value["id"] == item["id"])
                    _run_action(action, component, staged_image, recovery, plans[item["component"]][0])
                    item["status"] = "succeeded"
                    current = None
            for name, component in deployed.items():
                failure_step = f"checkout-verification:{name}"
                if _existing(plans[name][0], component, staged[name][1])["head"] != component["commit"]:
                    raise DeploymentError(f"deployed checkout is not at its manifest commit: {name}")
            failure_step = "host-tool-verification"
            for source in _installer_sources(plans["msk_containers"][0]):
                target = roots["home"] / ".local" / "bin" / source.name
                if not target.is_file() or stat.S_IMODE(target.stat().st_mode) != 0o755 or _digest(target) != _digest(source):
                    raise DeploymentError(f"host-tool verification failed: {source.name}")
            container_dir = pathlib.Path(os.environ.get("NVIM_CONTAINER_DIR", roots["home"] / "containers"))
            failure_step = "installed-image-verification"
            if _digest(container_dir / os.readlink(container_dir / "neovim.sif")) != artifact["sha256"]:
                raise DeploymentError("installed image digest does not match the release artifact")
            _publish(result_path, _result(manifest, roots, artifact, run_id, release_tag, started_at, log_path, actions, "succeeded", phase, None, "deployment verified; configuration is pending"))
            return 0
    except KeyboardInterrupt:
        _publish(result_path, _result(manifest, roots, artifact, run_id, release_tag, started_at, log_path, actions, "interrupted", phase, f"{current['component']}:{current['id']}" if current else failure_step, "deployment interrupted; repair or inspect and rerun"))
        return 2
    except (DeploymentError, OSError, subprocess.SubprocessError, json.JSONDecodeError) as exc:
        if current is not None:
            current["status"] = "failed"
        _publish(result_path, _result(manifest, roots, artifact, run_id, release_tag, started_at, log_path, actions, "failed", phase, f"{current['component']}:{current['id']}" if current else failure_step, str(exc)))
        print("mkchad-deploy: " + REDACT.sub(r"\1=<redacted>", str(exc)), file=sys.stderr)
        return 2
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def main(argv: list[str] | None = None) -> int:
    """Run only the internal launcher handoff CLI for one release deployment."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0], allow_abbrev=False)
    parser.add_argument("deploy")
    parser.add_argument("--release", required=True)
    parser.add_argument("--image", type=pathlib.Path, required=True)
    parser.add_argument("--log-path", type=pathlib.Path, required=True)
    parser.add_argument("--result-path", type=pathlib.Path, required=True)
    parser.add_argument("--run-id", required=True)
    args = parser.parse_args(argv)
    if args.deploy != "deploy":
        parser.error("only the deploy handoff is supported")
    try:
        return execute_deployment(pathlib.Path.cwd(), args.image, release_tag=args.release, log_path=args.log_path, result_path=args.result_path, run_id=args.run_id)
    except DeploymentError as exc:
        print("mkchad-deploy: " + str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
