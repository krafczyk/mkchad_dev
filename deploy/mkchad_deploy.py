#!/usr/bin/env python3
"""Fail-closed deployment engine for a verified MkChad parent release.

The public ``validate-release`` command checks a candidate checkout and image
without changing deployment destinations.  ``deploy`` accepts only a
bootstrap-created verification record, stages all inputs before final-path
mutation, and writes a durable receipt.  ``recover`` accepts a deployment UUID
and reconciles a retained, authenticated journal without consulting a remote.
All commands use explicit argv subprocesses and intentionally reject unsafe or
ambiguous state rather than attempting repair.
"""

from __future__ import annotations

import argparse
import configparser
import contextlib
import contextvars
import ctypes
import errno
import fcntl
import hashlib
import json
import os
import pathlib
import re
import selectors
import signal
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import uuid
import socket


SCHEMA = 1
DEPLOYER_ID = "mkchad-deploy-v1"
SHA256 = re.compile(r"^[0-9a-f]{64}$")
SHA1 = re.compile(r"^[0-9a-f]{40}$")
UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")
SAFE_RELATIVE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]*$")
SAFE_TREE_RELATIVE = re.compile(r"^[A-Za-z0-9.][A-Za-z0-9._/-]*$")
COMPONENT_DISPOSITIONS = {
    "msk_containers": "deployed",
    "mkchad": "deployed",
    "opencode": "embedded",
    "opencode.nvim": "configuration-owned",
    "opencode-project-reload": "configuration-owned",
    "compound-engineering": "configuration-owned",
    "opencode_sprint_loop": "disabled",
}
RELEASE_V2_COMPONENT_DISPOSITIONS = {
    "msk_containers": "deployed",
    "container_tools": "deployed",
    "mkchad": "deployed",
    "opencode": "embedded",
    "opencode.nvim": "configuration-owned",
    "opencode-project-reload": "configuration-owned",
    "compound-engineering": "configuration-owned",
    "opencode_sprint_loop": "disabled",
}
V2_DESTINATION_ROOTS = frozenset({"home", "xdg_config_home", "xdg_data_home", "xdg_state_home"})
V2_ACTION_PHASES = ("preflight", "apply", "verify")
V2_ARCHITECTURES = frozenset({"x86_64", "aarch64", "ppc64le"})
V2_PRESERVED_PATHS = frozenset({"opencode-server.json", "lazy-lock.json"})
V2_CONFIGURATION_COMPONENTS = frozenset({"opencode.nvim", "opencode-project-reload", "compound-engineering"})
WORKSPACE_ONLY_SUBMODULES = frozenset({"config_files"})
DEPLOYED_DESTINATIONS = {
    "msk_containers": ("xdg_data_home", "mkchad/components/msk_containers"),
    "mkchad": ("xdg_config_home", "mkchad"),
}
ACTION_TOKENS = {"recovery_dir", "staged_image", "component_revision"}
MSK_ACTION_IDS = ["host-tools-check", "host-tools-install", "image-install"]
MSK_ACTION_CONTRACT = [
    {"id": "host-tools-check", "path": "bin/install_nvim.sh", "args": ["--check"]},
    {"id": "host-tools-install", "path": "bin/install_nvim.sh", "args": ["--recovery-dir", {"token": "recovery_dir"}]},
    {"id": "image-install", "path": "nvim/bin/install_nvim_container", "args": ["--source-revision", {"token": "component_revision"}, {"token": "staged_image"}]},
]
LEGACY_MKCHAD_REVISION = "e91cbc39224d0637d9da59e1500cce6a355191c2"
FORBIDDEN_AUTHORITY_ALIASES = {"trust", "signer", "signature", "fingerprint", "verified_release"}
MKCHAD_PRESERVED_PATHS = frozenset({"opencode-server.json", "lazy-lock.json"})


class DeploymentError(RuntimeError):
    """Raised when deployment safety or release validation cannot be proven."""


def _reject_duplicates(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise DeploymentError("duplicate JSON key: " + key)
        result[key] = value
    return result


def load_json(path: pathlib.Path, label: str, maximum: int = 1024 * 1024):
    """Load one regular private JSON file, rejecting duplicate or oversized data.

    Args:
        path: File that must be a current-user-owned regular file.
        label: Human-readable object type for diagnostics.
        maximum: Maximum permitted byte count.

    Returns:
        Parsed JSON object.

    Raises:
        DeploymentError: If type, ownership, size, encoding, or JSON structure
            is unsafe.  The function never changes the input file.
    """
    before = os.lstat(path)
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError as exc:
        raise DeploymentError(f"unsafe {label} file: {path}") from exc
    try:
        file_stat = os.fstat(fd)
        if (before.st_dev, before.st_ino) != (file_stat.st_dev, file_stat.st_ino) or not stat.S_ISREG(file_stat.st_mode) or file_stat.st_uid != os.getuid():
            raise DeploymentError(f"unsafe {label} file: {path}")
        if file_stat.st_mode & 0o077:
            raise DeploymentError(f"{label} is not private: {path}")
        if file_stat.st_size > maximum:
            raise DeploymentError(f"{label} is too large: {path}")
        data = os.read(fd, maximum + 1)
        final = os.lstat(path)
        if (file_stat.st_dev, file_stat.st_ino) != (final.st_dev, final.st_ino):
            raise DeploymentError(f"{label} changed while reading: {path}")
        try:
            return json.loads(data, object_pairs_hook=_reject_duplicates)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise DeploymentError(f"malformed {label}: {exc}") from exc
    finally:
        os.close(fd)


def load_release_json(path: pathlib.Path) -> dict:
    """Load public signed-tree metadata without applying private receipt modes.

    ``release.json`` is intentionally readable from a candidate checkout.  It
    must still be a current-user-owned regular file without group/world write,
    and duplicate JSON keys remain a hard error.
    """
    try:
        before = os.lstat(path)
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError as exc:
        raise DeploymentError(f"unsafe release metadata file: {path}") from exc
    try:
        entry = os.fstat(fd)
        if (before.st_dev, before.st_ino) != (entry.st_dev, entry.st_ino) or not stat.S_ISREG(entry.st_mode) or entry.st_uid != os.getuid() or entry.st_mode & 0o022 or entry.st_size > 1024 * 1024:
            raise DeploymentError(f"unsafe release metadata file: {path}")
        data = os.read(fd, 1024 * 1024 + 1)
        final = os.lstat(path)
        if (entry.st_dev, entry.st_ino) != (final.st_dev, final.st_ino):
            raise DeploymentError("release metadata changed while reading")
        return json.loads(data, object_pairs_hook=_reject_duplicates)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DeploymentError(f"malformed release metadata: {exc}") from exc
    finally:
        os.close(fd)


def fsync_directory(directory: pathlib.Path):
    """Synchronize a directory entry after a security-sensitive mutation."""
    fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


_TRUSTED_GROUP_WRITABLE_ROOTS = contextvars.ContextVar("mkchad_trusted_group_writable_roots", default=frozenset())


@contextlib.contextmanager
def _trusted_group_writable_roots(roots):
    """Temporarily trust group-class writers on an exact set of named roots."""
    token = _TRUSTED_GROUP_WRITABLE_ROOTS.set(frozenset(roots))
    try:
        yield
    finally:
        _TRUSTED_GROUP_WRITABLE_ROOTS.reset(token)


def _safe_owned_directory(path: pathlib.Path, create: bool = False):
    """Validate an owned, non-symlink directory and its owned descendants.

    Args:
        path: Absolute directory to validate.
        create: Create missing path components as current-user-owned mode 0700.

    Raises:
        DeploymentError: If the directory path is missing, linked, foreign-owned,
            world-writable, or otherwise unsafe under the selected trust mode.
    """
    if create:
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
    if not path.is_absolute():
        raise DeploymentError(f"directory must be absolute: {path}")
    current = pathlib.Path("/")
    parts = path.parts[1:]
    for index, part in enumerate(parts):
        current /= part
        try:
            entry = os.lstat(current)
        except FileNotFoundError as exc:
            raise DeploymentError(f"missing directory: {current}") from exc
        if stat.S_ISLNK(entry.st_mode) or not stat.S_ISDIR(entry.st_mode):
            raise DeploymentError(f"unsafe directory type: {current}")
        trusted_root = current in _TRUSTED_GROUP_WRITABLE_ROOTS.get()
        if entry.st_uid not in {0, os.getuid()}:
            raise DeploymentError(f"unsafe directory ownership or mode: {current}")
        protected_shared_ancestor = (
            index < len(parts) - 1
            and entry.st_uid == 0
            and bool(entry.st_mode & stat.S_ISVTX)
            and bool(entry.st_mode & 0o002)
        )
        if entry.st_mode & 0o002:
            if not protected_shared_ancestor:
                raise DeploymentError(f"unsafe directory ownership or mode: {current}")
        if entry.st_mode & 0o020 and not trusted_root and not protected_shared_ancestor:
            raise DeploymentError(f"unsafe directory ownership or mode: {current}")
        if index == len(parts) - 1 and entry.st_uid != os.getuid() and not trusted_root:
            raise DeploymentError(f"unsafe directory ownership or mode: {current}")


def _regular_owned(path: pathlib.Path, mode: int | None = None):
    entry = os.lstat(path)
    if not stat.S_ISREG(entry.st_mode) or entry.st_uid != os.getuid():
        raise DeploymentError(f"unsafe regular file: {path}")
    if mode is not None and stat.S_IMODE(entry.st_mode) != mode:
        raise DeploymentError(f"unexpected file mode: {path}")
    return entry


def atomic_json(path: pathlib.Path, value: dict, no_replace: bool = False):
    """Durably write a private JSON record without following destination links.

    Args:
        path: New or replacement record path beneath an owned directory.
        value: JSON-serializable record.
        no_replace: Refuse an existing target when true.

    Raises:
        DeploymentError: If the parent or target is unsafe, changed, or already
            exists in no-replace mode.  A successful call changes only ``path``.
    """
    parent = path.parent
    _safe_owned_directory(parent)
    parent_before = os.stat(parent)
    expected_target = None
    if path.exists() or path.is_symlink():
        if no_replace:
            raise DeploymentError(f"receipt collision: {path}")
        expected_target = _identity(path)
        _regular_owned(path, 0o600)
    data = (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
    fd, temporary = tempfile.mkstemp(prefix=".record-", dir=parent)
    temporary_path = pathlib.Path(temporary)
    try:
        os.fchmod(fd, 0o600)
        os.write(fd, data)
        os.fsync(fd)
        os.close(fd)
        if os.stat(parent).st_dev != parent_before.st_dev or os.stat(parent).st_ino != parent_before.st_ino:
            raise DeploymentError(f"record parent changed: {parent}")
        if no_replace:
            try:
                os.link(temporary_path, path, follow_symlinks=False)
            except FileExistsError as exc:
                raise DeploymentError(f"receipt collision: {path}") from exc
            os.unlink(temporary_path)
        else:
            if expected_target is None:
                if path.exists() or path.is_symlink():
                    raise DeploymentError(f"record target appeared after preflight: {path}")
            elif _identity(path) != expected_target:
                raise DeploymentError(f"record target changed after preflight: {path}")
            os.replace(temporary_path, path)
        fsync_directory(parent)
    finally:
        with contextlib.suppress(OSError):
            os.close(fd)
        with contextlib.suppress(FileNotFoundError):
            temporary_path.unlink()


def canonical_github_repository(url: str) -> str:
    """Canonicalize a supported GitHub SSH or HTTPS URL to ``host/owner/repo``.

    Local paths, alternate hosts, query strings, and owner/repository traversal
    are rejected so release metadata cannot redirect component authority.
    """
    match = re.fullmatch(r"(?:git@github\.com:|https://github\.com/)([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+?)(?:\.git)?", url)
    if not match:
        raise DeploymentError(f"unsupported repository identity: {url}")
    return "github.com/" + match.group(1).lower() + "/" + match.group(2).lower()


def resolve_named_destination(roots: dict[str, pathlib.Path], root: str, relative: str) -> pathlib.Path:
    """Resolve a schema-defined relative destination under one named root.

    The result is lexical rather than ``resolve()`` based; no attacker-controlled
    symlink is followed during validation or construction.
    """
    if root not in roots or not isinstance(relative, str) or not SAFE_RELATIVE.fullmatch(relative):
        raise DeploymentError("invalid named destination")
    pieces = pathlib.PurePosixPath(relative).parts
    if not pieces or any(part in (".", "..") for part in pieces):
        raise DeploymentError("destination escapes its named root")
    base = pathlib.Path(roots[root])
    _safe_owned_directory(base)
    return base.joinpath(*pieces)


def _forbid_revisions(value, location: str = "release"):
    if isinstance(value, dict):
        for key, item in value.items():
            if key.lower() in {"revision", "commit", "git_revision", "git_commit"}:
                raise DeploymentError(f"{location} duplicates gitlink revision via {key}")
            _forbid_revisions(item, f"{location}.{key}")
    elif isinstance(value, list):
        for item in value:
            _forbid_revisions(item, location)


def validate_release_document(document: dict, submodules: list[str]) -> dict:
    """Validate strict schema-1 release metadata against parent submodules.

    Args:
        document: Parsed ``release.json`` object.
        submodules: Exact submodule paths from the selected parent commit.

    Returns:
        The validated document.

    Raises:
        DeploymentError: If schemas are future/malformed, a component has no
        single disposition, metadata repeats a git revision, or a destination
        would be ambiguous.  No filesystem state is changed.
    """
    if not isinstance(document, dict) or document.get("schema") != SCHEMA:
        raise DeploymentError("unsupported release schema")
    required = {"schema", "components", "artifacts", "configuration"}
    if set(document) - required or not required <= set(document):
        raise DeploymentError("release has unknown or missing fields")
    if not isinstance(document["components"], list) or not isinstance(document["artifacts"], dict):
        raise DeploymentError("release components or artifacts are malformed")
    _forbid_revisions(document)
    if set(submodules) != set(COMPONENT_DISPOSITIONS):
        raise DeploymentError("parent submodule inventory does not match the deployment contract")
    seen, destinations = set(), set()
    for component in document["components"]:
        if not isinstance(component, dict) or not isinstance(component.get("name"), str):
            raise DeploymentError("malformed component")
        name = component["name"]
        if name in seen or name not in submodules:
            raise DeploymentError("every parent submodule needs exactly one disposition")
        seen.add(name)
        disposition = component.get("disposition")
        if disposition != COMPONENT_DISPOSITIONS[name]:
            raise DeploymentError(f"invalid disposition for {name}")
        allowed = {"name", "disposition"}
        if disposition == "deployed":
            allowed |= {"destination", "actions"}
            destination = component.get("destination")
            if not isinstance(destination, dict) or set(destination) != {"root", "path"}:
                raise DeploymentError(f"deployed component lacks named destination: {name}")
            key = (destination["root"], destination["path"])
            if key in destinations:
                raise DeploymentError("components have conflicting destinations")
            destinations.add(key)
            if not isinstance(component.get("actions", []), list):
                raise DeploymentError(f"component actions are malformed: {name}")
            if key != DEPLOYED_DESTINATIONS[name]:
                raise DeploymentError(f"component has the wrong deployment destination: {name}")
            for action in component.get("actions", []):
                _validate_action(action, name)
            action_ids = [action["id"] for action in component.get("actions", [])]
            if name == "msk_containers" and action_ids != MSK_ACTION_IDS:
                raise DeploymentError("msk_containers actions do not match the installer-owned contract")
            if name == "msk_containers" and component.get("actions") != MSK_ACTION_CONTRACT:
                raise DeploymentError("msk_containers action paths or arguments do not match the installer-owned contract")
            if name == "mkchad" and action_ids:
                raise DeploymentError("mkchad must not declare deployment actions")
        if set(component) - allowed:
            raise DeploymentError(f"unknown component fields for {name}")
    if seen != set(submodules):
        raise DeploymentError("every parent submodule needs exactly one disposition")
    configuration = document["configuration"]
    if not isinstance(configuration, dict) or set(configuration) != {"contract_version", "guidance"}:
        raise DeploymentError("malformed configuration handoff")
    if configuration["contract_version"] != 1 or not isinstance(configuration["guidance"], str):
        raise DeploymentError("malformed configuration handoff")
    guidance = pathlib.PurePosixPath(configuration["guidance"])
    if guidance.is_absolute() or not guidance.parts or any(part in {"", ".", ".."} for part in guidance.parts):
        raise DeploymentError("configuration guidance must be a normalized repository path")
    variants = document["artifacts"].get("nvim-image")
    if not isinstance(variants, list) or not variants:
        raise DeploymentError("release has no nvim-image artifact")
    for artifact in variants:
        if not isinstance(artifact, dict) or set(artifact) != {"architecture", "sha256", "component_manifest_sha256", "source_filename_class"}:
            raise DeploymentError("malformed nvim-image artifact")
        if artifact["architecture"] not in {"x86_64", "aarch64", "ppc64le"} or artifact["source_filename_class"] != "raw-nvim-container" or not SHA256.fullmatch(artifact["sha256"]) or not SHA256.fullmatch(artifact["component_manifest_sha256"]):
            raise DeploymentError("invalid nvim-image integrity metadata")
    return document


def configuration_handoff(release: dict, gitlinks: dict[str, dict[str, str]]) -> dict:
    """Build the machine-readable host-configuration handoff from gitlinks.

    Args:
        release: Validated schema-1 release metadata.
        gitlinks: Child authorities derived from the selected parent commit.

    Returns:
        Contract version, guidance path, and sorted configuration-owned
        repository revisions. Revisions are never read from release metadata.

    Raises:
        DeploymentError: If a configuration-owned component lacks a complete
        gitlink-derived authority. The function has no side effects.
    """
    components = []
    for component in release["components"]:
        if component["disposition"] != "configuration-owned":
            continue
        authority = gitlinks.get(component["name"])
        if not isinstance(authority, dict) or not SHA1.fullmatch(authority.get("commit", "")) or not isinstance(authority.get("repository"), str):
            raise DeploymentError("configuration-owned component lacks gitlink authority")
        components.append({"name": component["name"], "repository": authority["repository"], "revision": authority["commit"]})
    components.sort(key=lambda item: item["name"])
    return {"contract_version": release["configuration"]["contract_version"], "guidance": release["configuration"]["guidance"], "components": components}


def _validate_action(action: object, component: str):
    """Validate one ordered component-owned action without interpolation."""
    if not isinstance(action, dict) or set(action) != {"id", "path", "args"}:
        raise DeploymentError(f"malformed action for {component}")
    if not isinstance(action["id"], str) or not re.fullmatch(r"[a-z][a-z0-9-]*", action["id"]):
        raise DeploymentError(f"invalid action id for {component}")
    path = action["path"]
    if not isinstance(path, str) or not SAFE_RELATIVE.fullmatch(path) or any(part in {".", ".."} for part in pathlib.PurePosixPath(path).parts):
        raise DeploymentError(f"invalid action path for {component}")
    if not isinstance(action["args"], list):
        raise DeploymentError(f"invalid action arguments for {component}")
    for argument in action["args"]:
        if isinstance(argument, str):
            if not argument or "\x00" in argument or argument.startswith("/") or "{" in argument or "}" in argument:
                raise DeploymentError(f"unsafe literal action argument for {component}")
        elif not isinstance(argument, dict) or set(argument) != {"token"} or argument["token"] not in ACTION_TOKENS:
            raise DeploymentError(f"unknown action token for {component}")


def _normalized_relative_path(value: object, label: str) -> str:
    """Return a normalized repository-relative path or raise ``DeploymentError``.

    Args:
        value: Candidate JSON string containing a relative path.
        label: Human-readable field name used in a validation diagnostic.

    Returns:
        The validated path unchanged.

    Raises:
        DeploymentError: If the path is empty, absolute, contains traversal, or
            contains characters outside the release metadata path alphabet.
    """
    if not isinstance(value, str) or not SAFE_RELATIVE.fullmatch(value):
        raise DeploymentError(f"invalid {label}")
    if any(part in {".", ".."} for part in pathlib.PurePosixPath(value).parts):
        raise DeploymentError(f"invalid {label}")
    return value


def _validate_v2_action(action: object, component: str, action_ids: set[str], last_phase: int) -> int:
    """Validate one schema-2 action and return its ordered phase index.

    Args:
        action: Candidate action object from a deployed component.
        component: Component name used in validation diagnostics.
        action_ids: Mutable manifest-wide set of previously declared action IDs.
        last_phase: Index of the preceding action phase for this component.

    Returns:
        The validated action phase index.

    Raises:
        DeploymentError: If the action has an unknown shape, duplicate ID,
            unnormalized executable path, out-of-order phase, unsafe literal, or
            unknown typed token. This function has no side effects beyond adding
            a successfully validated ID to ``action_ids``.
    """
    if not isinstance(action, dict) or set(action) != {"id", "phase", "path", "args"}:
        raise DeploymentError(f"malformed action for {component}")
    action_id = action.get("id")
    if not isinstance(action_id, str) or not re.fullmatch(r"[a-z][a-z0-9-]*", action_id):
        raise DeploymentError(f"invalid action id for {component}")
    if action_id in action_ids:
        raise DeploymentError("duplicate action id")
    phase = action.get("phase")
    if phase not in V2_ACTION_PHASES:
        raise DeploymentError(f"invalid action phase for {component}")
    phase_index = V2_ACTION_PHASES.index(phase)
    if phase_index < last_phase:
        raise DeploymentError(f"action phases are out of order for {component}")
    _normalized_relative_path(action.get("path"), "action path")
    arguments = action.get("args")
    if not isinstance(arguments, list):
        raise DeploymentError(f"invalid action arguments for {component}")
    for argument in arguments:
        if isinstance(argument, str):
            if not argument or "\x00" in argument or argument.startswith("/") or "{" in argument or "}" in argument:
                raise DeploymentError(f"unsafe action literal for {component}")
        elif not isinstance(argument, dict) or set(argument) != {"token"} or argument.get("token") not in ACTION_TOKENS:
            raise DeploymentError(f"unknown action token for {component}")
    action_ids.add(action_id)
    return phase_index


def validate_release_v2_document(document: dict) -> dict:
    """Validate a standalone schema-2 release manifest without Gitlink authority.

    Args:
        document: Parsed JSON release manifest that declares every release
            component, image artifact, and configuration handoff.

    Returns:
        The unchanged validated document.

    Raises:
        DeploymentError: If the document is not the exact closed schema-2
            shape, omits or repeats a known component, contains unsafe metadata,
            or has conflicting deployment destinations. Validation is pure and
            never reads Git state, destinations, or ``config_files``.
    """
    required = {"schema", "components", "artifacts", "configuration"}
    if not isinstance(document, dict) or set(document) != required or document.get("schema") != 2:
        raise DeploymentError("release has unknown or missing fields or an unsupported schema")
    components = document.get("components")
    if not isinstance(components, list):
        raise DeploymentError("release components are malformed")
    seen_components, destinations, action_ids = set(), [], set()
    for component in components:
        if not isinstance(component, dict):
            raise DeploymentError("malformed component")
        name = component.get("name")
        disposition = component.get("disposition")
        if name not in RELEASE_V2_COMPONENT_DISPOSITIONS or name in seen_components:
            raise DeploymentError("release must declare every known component exactly once")
        if disposition != RELEASE_V2_COMPONENT_DISPOSITIONS[name]:
            raise DeploymentError(f"invalid disposition for {name}")
        if not isinstance(component.get("repository"), str):
            raise DeploymentError(f"invalid repository for {name}")
        canonical_github_repository(component["repository"])
        if not isinstance(component.get("commit"), str) or not SHA1.fullmatch(component["commit"]):
            raise DeploymentError(f"invalid commit for {name}")
        allowed = {"name", "disposition", "repository", "commit"}
        if disposition == "deployed":
            allowed |= {"destination", "actions"}
            destination = component.get("destination")
            if not isinstance(destination, dict) or set(destination) != {"root", "path"} or destination.get("root") not in V2_DESTINATION_ROOTS:
                raise DeploymentError(f"invalid destination for {name}")
            destination_path = _normalized_relative_path(destination.get("path"), "destination")
            destinations.append((name, destination["root"], destination_path))
            actions = component.get("actions")
            if not isinstance(actions, list):
                raise DeploymentError(f"invalid actions for {name}")
            last_phase = 0
            for action in actions:
                last_phase = _validate_v2_action(action, name, action_ids, last_phase)
            if "preserved_paths" in component:
                allowed.add("preserved_paths")
                preserved_paths = component["preserved_paths"]
                if name != "mkchad" or not isinstance(preserved_paths, list) or not all(isinstance(path, str) for path in preserved_paths) or len(set(preserved_paths)) != len(preserved_paths):
                    raise DeploymentError(f"invalid preserved paths for {name}")
                for preserved_path in preserved_paths:
                    if preserved_path not in V2_PRESERVED_PATHS:
                        raise DeploymentError(f"invalid preserved path for {name}")
        elif {"destination", "actions", "preserved_paths"} & set(component):
            raise DeploymentError(f"non-deployed component has deployment-only fields: {name}")
        if set(component) != allowed:
            raise DeploymentError(f"unknown component fields for {name}")
        seen_components.add(name)
    if seen_components != set(RELEASE_V2_COMPONENT_DISPOSITIONS):
        raise DeploymentError("release must declare every known component exactly once")
    for index, (name, root, path) in enumerate(destinations):
        for other_name, other_root, other_path in destinations[index + 1:]:
            if root != other_root:
                continue
            nested_tools = {name, other_name} == {"msk_containers", "container_tools"}
            msk_path = path if name == "msk_containers" else other_path
            tools_path = path if name == "container_tools" else other_path
            if (
                nested_tools
                and root == "xdg_data_home"
                and msk_path == "mkchad/components/msk_containers"
                and tools_path == "mkchad/components/msk_containers/container_tools"
            ):
                continue
            if path == other_path or path.startswith(other_path + "/") or other_path.startswith(path + "/"):
                raise DeploymentError("components have overlapping destinations")
    artifacts = document.get("artifacts")
    if not isinstance(artifacts, dict) or set(artifacts) != {"nvim-image"} or not isinstance(artifacts["nvim-image"], list) or not artifacts["nvim-image"]:
        raise DeploymentError("release artifacts are malformed")
    architectures = set()
    for artifact in artifacts["nvim-image"]:
        if not isinstance(artifact, dict) or set(artifact) != {"architecture", "sha256", "source_filename_class"}:
            raise DeploymentError("malformed nvim-image artifact")
        architecture = artifact.get("architecture")
        if architecture not in V2_ARCHITECTURES or architecture in architectures:
            raise DeploymentError("invalid or duplicate image architecture")
        if not isinstance(artifact.get("sha256"), str) or not SHA256.fullmatch(artifact["sha256"]):
            raise DeploymentError("invalid image digest")
        if artifact.get("source_filename_class") != "raw-nvim-container":
            raise DeploymentError("invalid nvim-image source filename class")
        architectures.add(architecture)
    configuration = document.get("configuration")
    if not isinstance(configuration, dict) or set(configuration) != {"contract_version", "guidance"} or configuration.get("contract_version") != 1:
        raise DeploymentError("malformed configuration handoff")
    _normalized_relative_path(configuration.get("guidance"), "configuration guidance")
    return document


def _validate_result_commit(value: object, label: str, nullable: bool = False):
    """Validate one result commit or digest field without transforming it.

    Args:
        value: Candidate string, or ``None`` for a nullable observed value.
        label: Field category used in errors, either ``commit`` or ``digest``.
        nullable: Whether an unavailable observation may be represented as null.

    Raises:
        DeploymentError: If the value is not a lowercase full SHA-1 or SHA-256.
    """
    if nullable and value is None:
        return
    pattern = SHA1 if label == "commit" else SHA256
    if not isinstance(value, str) or not pattern.fullmatch(value):
        raise DeploymentError(f"invalid result {label}")


def validate_result_v1_document(document: dict) -> dict:
    """Validate one completed schema-1 deployment result without publishing it.

    Args:
        document: Parsed terminal result JSON produced by a deployment run.

    Returns:
        The unchanged validated document.

    Raises:
        DeploymentError: If a result is future, incomplete, contains unknown
            secret-bearing fields, or has malformed component, artifact, action,
            configuration-handoff, timestamp, or log metadata. This pure
            validator does not read files or alter persistent state.
    """
    required = {"schema", "release", "started_at", "completed_at", "outcome", "phase", "components", "artifact", "actions", "configuration_handoff", "failed_step", "diagnostic", "log_path"}
    if not isinstance(document, dict) or set(document) != required or document.get("schema") != 1:
        raise DeploymentError("result has unknown or missing fields or an unsupported schema")
    if not isinstance(document.get("release"), str) or not re.fullmatch(r"(?!-)(?!.*\.\.)[A-Za-z0-9][A-Za-z0-9._/-]*", document["release"]):
        raise DeploymentError("invalid result release")
    timestamp = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z")
    if not all(isinstance(document[key], str) and timestamp.fullmatch(document[key]) for key in ("started_at", "completed_at")):
        raise DeploymentError("invalid result timestamp")
    if document.get("outcome") not in {"succeeded", "failed", "interrupted"} or document.get("phase") not in V2_ACTION_PHASES:
        raise DeploymentError("invalid result outcome or phase")
    components = document.get("components")
    if not isinstance(components, list):
        raise DeploymentError("malformed result components")
    seen = set()
    for component in components:
        if not isinstance(component, dict) or set(component) != {"name", "disposition", "requested_commit", "observed_commit"}:
            raise DeploymentError("malformed result component")
        name = component.get("name")
        if name not in RELEASE_V2_COMPONENT_DISPOSITIONS or name in seen or component.get("disposition") != RELEASE_V2_COMPONENT_DISPOSITIONS[name]:
            raise DeploymentError("invalid result component")
        _validate_result_commit(component.get("requested_commit"), "commit")
        _validate_result_commit(component.get("observed_commit"), "commit", nullable=True)
        seen.add(name)
    if seen != set(RELEASE_V2_COMPONENT_DISPOSITIONS):
        raise DeploymentError("result components are incomplete")
    artifact = document.get("artifact")
    if not isinstance(artifact, dict) or set(artifact) != {"architecture", "expected_sha256", "observed_sha256"} or artifact.get("architecture") not in V2_ARCHITECTURES:
        raise DeploymentError("malformed result artifact")
    _validate_result_commit(artifact.get("expected_sha256"), "digest")
    _validate_result_commit(artifact.get("observed_sha256"), "digest", nullable=True)
    actions = document.get("actions")
    if not isinstance(actions, list):
        raise DeploymentError("malformed result actions")
    action_keys = set()
    for action in actions:
        if not isinstance(action, dict) or set(action) != {"component", "id", "phase", "status"}:
            raise DeploymentError("malformed result action")
        if action.get("component") not in RELEASE_V2_COMPONENT_DISPOSITIONS or not isinstance(action.get("id"), str) or not re.fullmatch(r"[a-z][a-z0-9-]*", action["id"]):
            raise DeploymentError("invalid result action")
        if action.get("phase") not in V2_ACTION_PHASES or action.get("status") not in {"pending", "succeeded", "skipped-compliant", "failed"}:
            raise DeploymentError("invalid result action status")
        key = (action["component"], action["id"])
        if key in action_keys:
            raise DeploymentError("duplicate result action")
        action_keys.add(key)
    handoff = document.get("configuration_handoff")
    if not isinstance(handoff, dict) or set(handoff) != {"state", "contract_version", "guidance", "components"} or handoff.get("state") != "configuration-pending" or handoff.get("contract_version") != 1:
        raise DeploymentError("malformed result configuration handoff")
    _normalized_relative_path(handoff.get("guidance"), "configuration guidance")
    if not isinstance(handoff.get("components"), list):
        raise DeploymentError("malformed result configuration components")
    handoff_names = set()
    for component in handoff["components"]:
        if not isinstance(component, dict) or set(component) != {"name", "repository", "commit"} or component.get("name") not in V2_CONFIGURATION_COMPONENTS or component["name"] in handoff_names or not isinstance(component.get("repository"), str):
            raise DeploymentError("malformed result configuration component")
        canonical_github_repository(component["repository"])
        _validate_result_commit(component.get("commit"), "commit")
        handoff_names.add(component["name"])
    if handoff_names != V2_CONFIGURATION_COMPONENTS:
        raise DeploymentError("result configuration components are incomplete")
    if document["failed_step"] is not None and (not isinstance(document["failed_step"], str) or not document["failed_step"] or len(document["failed_step"]) > 256):
        raise DeploymentError("invalid result failed step")
    if not isinstance(document["diagnostic"], str) or not document["diagnostic"] or len(document["diagnostic"]) > 4096:
        raise DeploymentError("invalid result diagnostic")
    log_path = document.get("log_path")
    if not isinstance(log_path, str) or not log_path.startswith("/") or any(part in {"", ".", ".."} for part in pathlib.PurePosixPath(log_path).parts[1:]):
        raise DeploymentError("invalid result log path")
    return document


def git_env() -> dict[str, str]:
    """Return a minimal Git environment that disables user config and helpers."""
    environment = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    environment.update({"GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull, "GIT_TERMINAL_PROMPT": "0"})
    return environment


def run_git(argv: list[str], cwd: pathlib.Path | None = None, check: bool = True) -> subprocess.CompletedProcess:
    """Run Git with config/templates/hooks disabled and no shell interpolation."""
    command = ["git", "-c", "core.hooksPath=/dev/null", "-c", "init.templateDir=", *argv]
    result = subprocess.run(command, cwd=cwd, env=git_env(), text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if check and result.returncode:
        digest = hashlib.sha256(result.stderr.encode()).hexdigest()
        raise DeploymentError(f"git command failed with exit {result.returncode}; stderr-sha256={digest}")
    return result


def _gitmodules_at(repo: pathlib.Path, commit: str) -> dict[str, str]:
    raw = run_git(["show", f"{commit}:.gitmodules"], repo).stdout
    parser = configparser.ConfigParser(interpolation=None)
    parser.read_string(raw)
    modules = {}
    for section in parser.sections():
        path = parser.get(section, "path", fallback=None)
        url = parser.get(section, "url", fallback=None)
        if not path or not url:
            raise DeploymentError("malformed .gitmodules")
        modules[path] = url
    return modules


def derive_gitlinks(
    repo: pathlib.Path,
    commit: str,
    *,
    excluded: frozenset[str] = frozenset(),
) -> dict[str, dict[str, str]]:
    """Derive exact child commits and canonical repositories from selected gitlinks.

    Args:
        repo: Git repository containing the selected commit.
        commit: Full commit SHA whose ``.gitmodules`` and tree are authoritative.
        excluded: Workspace-only submodule paths omitted before gitlink and
            repository validation.

    Returns:
        Mapping of selected submodule paths to commits, URLs, and canonical
        repository identities.

    Raises:
        DeploymentError: If the commit, selected metadata, gitlinks, or
            repository identities are malformed. The function does not fetch
            or modify any child repository.
    """
    if not SHA1.fullmatch(commit):
        raise DeploymentError("parent commit must be a full SHA")
    result = {}
    for path, url in _gitmodules_at(repo, commit).items():
        if path in excluded:
            continue
        line = run_git(["ls-tree", commit, "--", path], repo).stdout.strip()
        match = re.fullmatch(r"160000 commit ([0-9a-f]{40})\t(.+)", line)
        if not match or match.group(2) != path:
            raise DeploymentError(f"missing gitlink for {path}")
        result[path] = {"commit": match.group(1), "url": url, "repository": canonical_github_repository(url)}
    return result


def _derive_release_gitlinks(repo: pathlib.Path, commit: str) -> dict[str, dict[str, str]]:
    return derive_gitlinks(repo, commit, excluded=WORKSPACE_ONLY_SUBMODULES)


def inspect_git_config(destination: pathlib.Path):
    """Reject local Git configuration that can run helpers or rewrite transport."""
    result = run_git(["config", "--local", "--no-includes", "--null", "--list"], destination, check=False)
    if result.returncode:
        raise DeploymentError("cannot read local Git configuration")
    for item in result.stdout.split("\0"):
        if not item:
            continue
        key = item.split("\n", 1)[0].split("=", 1)[0].lower()
        dangerous = key.startswith(("filter.", "diff.", "alias.", "credential.", "url.")) or key in {"core.hookspath", "core.fsmonitor", "core.sshcommand", "core.gitproxy", "core.attributesfile"}
        dangerous = dangerous or bool(re.fullmatch(r"remote\.[^.]+\.(uploadpack|receivepack|vcs)", key))
        if dangerous:
            raise DeploymentError(f"dangerous local Git configuration: {key}")


def _identity(path: pathlib.Path):
    entry = os.lstat(path)
    return (entry.st_dev, entry.st_ino, entry.st_uid, stat.S_IFMT(entry.st_mode))


def _validate_preserved_path(destination: pathlib.Path, path: str):
    """Require one declared MkChad-owned exception to retain its safe shape."""
    candidate = destination / path
    entry = os.lstat(candidate)
    if entry.st_uid != os.getuid():
        raise DeploymentError(f"preserved path is unsafe: {path}")
    if path == "opencode-server.json":
        if stat.S_ISLNK(entry.st_mode):
            target = os.readlink(candidate)
            if not target.endswith("config_files/.config/mkchad/opencode-server.json"):
                raise DeploymentError(f"preserved path has an unexpected legacy link: {path}")
        elif not stat.S_ISREG(entry.st_mode) or stat.S_IMODE(entry.st_mode) != 0o600:
            raise DeploymentError(f"preserved path is not a private regular file: {path}")
        return
    if path == "lazy-lock.json":
        if not stat.S_ISREG(entry.st_mode) or stat.S_IMODE(entry.st_mode) not in {0o600, 0o644}:
            raise DeploymentError("preserved generated lock is not a safe regular file")
        return
    raise DeploymentError(f"unsupported preserved path: {path}")


def preflight_existing_checkout(destination: pathlib.Path, expected_repository: str, expected_commit: str, preserved: set[str] | None = None) -> dict:
    """Validate an existing checkout before an exact detached update or no-op.

    Args:
        destination: Existing persistent component checkout.
        expected_repository: Canonical repository identity required for origin.
        expected_commit: Intended full commit SHA.
        preserved: Declared ignored paths whose type, owner, and mode are
            validated without reading their contents.

    Returns:
        Immutable preflight identity, current HEAD, and compliance result.

    Raises:
        DeploymentError: If repository identity, Git state, local paths, or a
        preserved path cannot be proven safe. The function changes no files.

    Ignored and untracked paths block except explicitly declared MkChad
    configuration and generated-state paths. Linked worktrees, external Git
    dirs, wrong origins, unsafe local configuration, and tracked local changes
    all fail before the caller may mutate the checkout.
    """
    preserved = preserved or set()
    _safe_owned_directory(destination)
    before = _identity(destination)
    top = run_git(["rev-parse", "--show-toplevel"], destination).stdout.strip()
    if pathlib.Path(top) != destination or os.path.realpath(top) != str(destination):
        raise DeploymentError("destination is not its Git top level")
    git_dir = run_git(["rev-parse", "--git-dir"], destination).stdout.strip()
    try:
        git_entry = os.lstat(destination / ".git")
    except OSError as exc:
        raise DeploymentError("checkout has no safe in-tree Git directory") from exc
    if git_dir != ".git" or not stat.S_ISDIR(git_entry.st_mode) or stat.S_ISLNK(git_entry.st_mode) or git_entry.st_uid != os.getuid():
        raise DeploymentError("linked or external Git worktree is forbidden")
    if (destination / ".git" / "index.lock").exists() or (destination / ".git" / "index.lock").is_symlink():
        raise DeploymentError("checkout has a pre-existing Git index lock")
    inspect_git_config(destination)
    origin = run_git(["remote", "get-url", "origin"], destination).stdout.strip()
    if canonical_github_repository(origin) != expected_repository:
        raise DeploymentError("existing checkout has wrong origin")
    status = run_git(["status", "--porcelain=v1", "-z", "--ignored=matching", "--untracked-files=all"], destination).stdout.split("\0")
    for line in status:
        if not line:
            continue
        path = line[3:]
        if path not in preserved:
            raise DeploymentError(f"existing checkout has local work: {path}")
        _validate_preserved_path(destination, path)
    head = run_git(["rev-parse", "HEAD"], destination).stdout.strip()
    return {"kind": "existing", "identity": before, "head": head, "compliant": head == expected_commit}


def _tree_entries(repository: pathlib.Path, commit: str) -> dict[str, tuple[str, str, str]]:
    entries = {}
    for line in run_git(["ls-tree", "-r", "-z", commit], repository).stdout.split("\0"):
        if not line:
            continue
        match = re.fullmatch(r"([0-7]{6}) (blob|commit) ([0-9a-f]{40})\t(.+)", line)
        if not match:
            raise DeploymentError("pinned tree contains an unsupported path or entry")
        path = match.group(4)
        parts = pathlib.PurePosixPath(path).parts
        if not SAFE_TREE_RELATIVE.fullmatch(path) or any(part in {".", ".."} or part.casefold() == ".git" for part in parts):
            raise DeploymentError("pinned tree contains an unsupported path or entry")
        entries[path] = (match.group(1), match.group(2), match.group(3))
    return entries


def _index_entries(repository: pathlib.Path) -> dict[str, tuple[str, str, str]]:
    entries = {}
    for line in run_git(["ls-files", "--stage", "-z"], repository).stdout.split("\0"):
        if not line:
            continue
        match = re.fullmatch(r"([0-7]{6}) ([0-9a-f]{40}) ([0-3])\t(.+)", line)
        if not match or match.group(3) != "0":
            raise DeploymentError("checkout index contains an unsupported merge state")
        entry_type = "commit" if match.group(1) == "160000" else "blob"
        entries[match.group(4)] = (match.group(1), entry_type, match.group(2))
    return entries


def _git_blob_id(data: bytes) -> str:
    return hashlib.sha1(b"blob " + str(len(data)).encode("ascii") + b"\0" + data).hexdigest()


def _worktree_entry(repository: pathlib.Path, path: str, candidates: set[tuple[str, str, str] | None]) -> tuple[str, str, str] | None:
    target = repository / path
    if not target.exists() and not target.is_symlink():
        return None
    entry = os.lstat(target)
    if entry.st_uid != os.getuid():
        raise DeploymentError("checkout mixture contains a foreign-owned path")
    if stat.S_ISLNK(entry.st_mode):
        data = os.readlink(target).encode()
        return ("120000", "blob", _git_blob_id(data))
    if stat.S_ISREG(entry.st_mode):
        mode = "100755" if entry.st_mode & stat.S_IXUSR else "100644"
        return (mode, "blob", _git_blob_id(target.read_bytes()))
    if stat.S_ISDIR(entry.st_mode):
        result = run_git(["rev-parse", "HEAD"], target, check=False)
        if result.returncode == 0 and SHA1.fullmatch(result.stdout.strip()):
            return ("160000", "commit", result.stdout.strip())
    raise DeploymentError("checkout mixture contains an unsupported path type")


def classify_checkout_mixture(destination: pathlib.Path, expected_repository: str, previous: str | None, intended: str, preserved: set[str]) -> dict:
    """Classify a recovery checkout containing only old or intended tree state.

    Args:
        destination: Existing persistent component checkout.
        expected_repository: Canonical repository identity required for origin.
        previous: Recorded pre-deployment commit, when one existed.
        intended: Release commit selected by the recovery authority.
        preserved: Declared ignored paths validated independently of Git trees.

    Returns:
        Mixed-state checkout identity and current HEAD for recovery planning.

    Raises:
        DeploymentError: If the checkout contains third-state Git content,
        unrelated local work, or an unsafe preserved path. No files are changed.
    """
    if previous is None:
        raise DeploymentError("new checkout has unclassifiable local work")
    _safe_owned_directory(destination)
    top = run_git(["rev-parse", "--show-toplevel"], destination).stdout.strip()
    git_entry = os.lstat(destination / ".git")
    if pathlib.Path(top) != destination or not stat.S_ISDIR(git_entry.st_mode) or stat.S_ISLNK(git_entry.st_mode) or git_entry.st_uid != os.getuid():
        raise DeploymentError("checkout mixture has an unsafe Git layout")
    if canonical_github_repository(run_git(["remote", "get-url", "origin"], destination).stdout.strip()) != expected_repository:
        raise DeploymentError("existing checkout has wrong origin")
    inspect_git_config(destination)
    old_entries = _tree_entries(destination, previous)
    new_entries = _tree_entries(destination, intended)
    index_entries = _index_entries(destination)
    for path in set(old_entries) | set(new_entries) | set(index_entries):
        allowed = {old_entries.get(path), new_entries.get(path)}
        if index_entries.get(path) not in allowed:
            raise DeploymentError(f"checkout index has third-state content: {path}")
        if _worktree_entry(destination, path, allowed) not in allowed:
            raise DeploymentError(f"checkout worktree has third-state content: {path}")
    for line in run_git(["status", "--porcelain=v1", "-z", "--ignored=matching", "--untracked-files=all"], destination).stdout.split("\0"):
        if not line:
            continue
        path = line[3:]
        if path in preserved:
            _validate_preserved_path(destination, path)
        elif path not in set(old_entries) | set(new_entries):
            raise DeploymentError(f"checkout mixture contains unrelated local work: {path}")
    return {"kind": "mixed", "identity": _identity(destination), "head": run_git(["rev-parse", "HEAD"], destination).stdout.strip(), "compliant": False}


def _git_blob_bytes(repository: pathlib.Path, object_id: str) -> bytes:
    result = subprocess.run(
        ["git", "-c", "core.hooksPath=/dev/null", "cat-file", "blob", object_id],
        cwd=repository,
        env=git_env(),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if result.returncode:
        raise DeploymentError(f"cannot read retained tree blob; stderr-sha256={hashlib.sha256(result.stderr).hexdigest()}")
    return result.stdout


def _write_tree_entry(repository: pathlib.Path, path: str, entry: tuple[str, str, str]):
    mode, entry_type, object_id = entry
    if entry_type == "commit":
        return
    target = repository / path
    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    _safe_owned_directory(target.parent)
    expected = _identity(target) if target.exists() or target.is_symlink() else None
    data = _git_blob_bytes(repository, object_id)
    temporary = target.parent / (".mkchad-tree-" + uuid.uuid4().hex)
    try:
        if mode == "120000":
            os.symlink(data.decode(), temporary)
        elif mode in {"100644", "100755"}:
            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o755 if mode == "100755" else 0o644)
            try:
                view = memoryview(data)
                while view:
                    view = view[os.write(fd, view):]
                os.fsync(fd)
            finally:
                os.close(fd)
        else:
            raise DeploymentError("retained tree has an unsupported file mode")
        if expected is None:
            if target.exists() or target.is_symlink():
                raise DeploymentError("checkout path appeared during recovery")
        elif _identity(target) != expected:
            raise DeploymentError("checkout path changed during recovery")
        os.replace(temporary, target)
        fsync_directory(target.parent)
    finally:
        temporary.unlink(missing_ok=True)


def materialize_checkout_tree(destination: pathlib.Path, previous: str, intended: str, target_commit: str, scope: SessionScope):
    """Reconstruct a classified old/new mixture without reset, stash, or merge."""
    old_entries = _tree_entries(destination, previous)
    new_entries = _tree_entries(destination, intended)
    target_entries = old_entries if target_commit == previous else new_entries
    for path in sorted((set(old_entries) | set(new_entries)) - set(target_entries), key=lambda value: (value.count("/"), value), reverse=True):
        target = destination / path
        if not target.exists() and not target.is_symlink():
            continue
        entry = os.lstat(target)
        if stat.S_ISDIR(entry.st_mode):
            try:
                target.rmdir()
            except OSError as exc:
                raise DeploymentError("obsolete checkout directory is not empty") from exc
        elif stat.S_ISREG(entry.st_mode) or stat.S_ISLNK(entry.st_mode):
            target.unlink()
        else:
            raise DeploymentError("obsolete checkout path has an unsafe type")
        fsync_directory(target.parent)
    for path, entry in sorted(target_entries.items()):
        _write_tree_entry(destination, path, entry)
    install_checkout_index(destination, target_commit, scope, ".mkchad-recovery.index")


def install_checkout_index(destination: pathlib.Path, target_commit: str, scope: SessionScope, index_name: str):
    """Build an alternate index and atomically publish it without index.lock."""
    git_dir = destination / ".git"
    index = git_dir / "index"
    alternate = git_dir / index_name
    alternate_lock = pathlib.Path(str(alternate) + ".lock")
    if alternate.exists() or alternate.is_symlink() or alternate_lock.exists() or alternate_lock.is_symlink():
        raise DeploymentError("deployment-owned alternate Git index already exists")
    original_identity = _identity(index)
    try:
        _contained_git(scope, ["-C", str(destination), "read-tree", target_commit], {"GIT_INDEX_FILE": str(alternate)})
        _regular_owned(alternate)
        if _identity(index) != original_identity:
            raise DeploymentError("Git index changed during alternate-index checkout")
        os.replace(alternate, index)
        fsync_directory(git_dir)
        _contained_git(scope, ["-C", str(destination), "update-ref", "--no-deref", "HEAD", target_commit])
    finally:
        alternate.unlink(missing_ok=True)
        alternate_lock.unlink(missing_ok=True)


def transactional_git_checkout(destination: pathlib.Path, target_commit: str, scope: SessionScope, journal: DeploymentJournal, resource: dict):
    """Checkout through a journal-owned alternate index to avoid stale index.lock."""
    git_dir = destination / ".git"
    index = git_dir / "index"
    name = ".mkchad-deploy-" + journal.id + "-" + resource["name"].replace("/", "-").replace(".", "-") + ".index"
    alternate = git_dir / name
    if alternate.exists() or alternate.is_symlink() or pathlib.Path(str(alternate) + ".lock").exists():
        raise DeploymentError("deployment-owned alternate Git index already exists")
    source_fd = os.open(index, os.O_RDONLY | os.O_NOFOLLOW)
    target_fd = os.open(alternate, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        while True:
            block = os.read(source_fd, 1024 * 1024)
            if not block:
                break
            view = memoryview(block)
            while view:
                view = view[os.write(target_fd, view):]
        os.fsync(target_fd)
    finally:
        os.close(source_fd)
        os.close(target_fd)
    resource["alternate_index"] = name
    resource["git_dir_identity"] = _identity(git_dir)
    resource["index_identity"] = _identity(index)
    resource["state"] = "index-prepared"
    journal.persist()
    _contained_git(scope, ["-C", str(destination), "checkout", "--detach", "--no-recurse-submodules", target_commit], {"GIT_INDEX_FILE": str(alternate)})
    _regular_owned(alternate)
    if _identity(git_dir) != tuple(resource["git_dir_identity"]) or _identity(index) != tuple(resource["index_identity"]):
        raise DeploymentError("checkout Git metadata changed during alternate-index update")
    os.replace(alternate, index)
    fsync_directory(git_dir)
    resource["state"] = "applied"
    journal.persist()


def preflight_component_destination(destination: pathlib.Path, component: dict, authority: dict, staged: pathlib.Path) -> dict:
    """Build an immutable destination plan before any component is changed."""
    preserved = MKCHAD_PRESERVED_PATHS if component["name"] == "mkchad" else set()
    for path in preserved:
        tracked = run_git(["-C", str(staged), "cat-file", "-e", f"{authority['commit']}:{path}"], check=False)
        if tracked.returncode == 0:
            raise DeploymentError(f"desired component tree owns preserved path: {path}")
    if destination.exists() or destination.is_symlink():
        return preflight_existing_checkout(destination, authority["repository"], authority["commit"], preserved)
    missing = []
    existing_parent = destination.parent
    while not existing_parent.exists() and not existing_parent.is_symlink():
        missing.append(existing_parent)
        existing_parent = existing_parent.parent
    _safe_owned_directory(existing_parent)
    return {"kind": "absent", "existing_parent": str(existing_parent), "parent_identity": _identity(existing_parent), "missing_parents": [str(path) for path in reversed(missing)]}


def create_preflighted_parents(preflight: dict, component: dict, journal: DeploymentJournal):
    """Durably journal and create only the absent destination-parent chain."""
    missing = [pathlib.Path(path) for path in preflight.get("missing_parents", [])]
    existing_parent = pathlib.Path(preflight["existing_parent"])
    if _identity(existing_parent) != preflight["parent_identity"]:
        raise DeploymentError("destination ancestor changed after preflight")
    if not missing:
        return
    parent = existing_parent
    for index, path in enumerate(missing):
        resource = {"kind": "directory", "name": component["name"], "index": index, "count": len(missing), "state": "pending"}
        journal.record["resources"].append(resource)
        journal.persist()
        if path.parent != parent or path.exists() or path.is_symlink():
            raise DeploymentError("destination parent appeared after preflight")
        path.mkdir(mode=0o700)
        fsync_directory(parent)
        _safe_owned_directory(path)
        resource["state"] = "created"
        resource["identity"] = _identity(path)
        journal.persist()
        parent = path


def preflight_all_destinations(roots: dict[str, pathlib.Path], release: dict, gitlinks: dict, staged: dict) -> dict[str, tuple[pathlib.Path, dict]]:
    """Validate every final checkout and exclusive destination before mutation."""
    plans = {}
    destinations = []
    for component in release["components"]:
        if component["disposition"] != "deployed":
            continue
        destination = resolve_named_destination(roots, component["destination"]["root"], component["destination"]["path"])
        for other in destinations:
            if destination == other or destination in other.parents or other in destination.parents:
                raise DeploymentError("deployed component destinations overlap")
        destinations.append(destination)
        plans[component["name"]] = (
            destination,
            preflight_component_destination(destination, component, gitlinks[component["name"]], staged[component["name"]]),
        )
    return plans


def preflight_nested_checkout(parent_plan: tuple[pathlib.Path, dict], authority: dict) -> dict:
    """Preflight the installer-owned container_tools checkout without mutation."""
    parent_destination, parent_preflight = parent_plan
    if parent_preflight["kind"] == "absent":
        return {"kind": "parent-absent"}
    destination = parent_destination / "container_tools"
    if destination.exists() or destination.is_symlink():
        return preflight_existing_checkout(destination, authority["repository"], authority["commit"])
    return {"kind": "absent", "parent_identity": _identity(parent_destination)}


def install_or_update_nested_checkout(parent_destination: pathlib.Path, staged: pathlib.Path, authority: dict, preflight: dict, resource: dict | None, journal: DeploymentJournal, scope: SessionScope) -> dict:
    """Install the exact nested container_tools gitlink before host actions."""
    destination = parent_destination / "container_tools"
    if preflight["kind"] == "parent-absent":
        if destination.exists() or destination.is_symlink():
            _safe_owned_directory(destination)
            try:
                destination.rmdir()
            except OSError as exc:
                raise DeploymentError("nested checkout path is not the empty pinned gitlink directory") from exc
        preflight = {"kind": "absent", "parent_identity": _identity(parent_destination)}
    if preflight["kind"] == "existing":
        current = preflight_existing_checkout(destination, authority["repository"], authority["commit"])
        if current != preflight:
            raise DeploymentError("nested checkout changed after preflight")
        if current["compliant"]:
            return {"name": "container_tools", "repository": authority["repository"], "result": "no-op", "revision": authority["commit"]}
        if resource is None or resource.get("previous") != current["head"]:
            raise DeploymentError("nested checkout recovery state was not journaled before parent mutation")
        _contained_git(scope, ["-C", str(destination), "fetch", "--no-tags", str(staged), authority["commit"]])
        transactional_git_checkout(destination, authority["commit"], scope, journal, resource)
        return {"name": "container_tools", "repository": authority["repository"], "result": "updated", "revision": authority["commit"]}
    if destination.exists() or destination.is_symlink() or _identity(parent_destination) != preflight["parent_identity"]:
        raise DeploymentError("nested checkout destination changed after preflight")
    if resource is None or resource.get("previous") is not None:
        raise DeploymentError("nested checkout recovery state was not journaled before parent mutation")
    temporary = pathlib.Path(tempfile.mkdtemp(prefix=".container-tools-", dir=parent_destination))
    try:
        _prepare_exact_checkout(staged, temporary, authority["commit"], authority["url"], scope)
        _no_replace_rename(temporary, destination)
        resource["state"] = "installed"
        resource["identity"] = _identity(destination)
        journal.persist()
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)
    return {"name": "container_tools", "repository": authority["repository"], "result": "installed", "revision": authority["commit"]}


def snapshot_image(source: pathlib.Path, staging_root: pathlib.Path, expected_digest: str, architecture: str | None = None) -> pathlib.Path:
    """Copy a caller image through no-follow descriptors and verify SHA-256.

    The source must be a current-user-owned regular file.  The copied private
    snapshot is fsynced and hashed before any runtime command may receive it.
    """
    if not SHA256.fullmatch(expected_digest):
        raise DeploymentError("invalid expected image digest")
    _safe_owned_directory(staging_root, create=True)
    os.chmod(staging_root, 0o700)
    try:
        source_fd = os.open(source, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError as exc:
        raise DeploymentError("image source is not a safe regular file") from exc
    try:
        source_stat = os.fstat(source_fd)
        if not stat.S_ISREG(source_stat.st_mode) or source_stat.st_uid != os.getuid():
            raise DeploymentError("image source is not a current-user-owned regular file")
        architecture = architecture or _architecture()
        raw_architecture = {"x86_64": "x86", "aarch64": "aarch64", "ppc64le": "ppc64le"}.get(architecture)
        if raw_architecture is None:
            raise DeploymentError(f"unsupported raw image architecture: {architecture}")
        destination = staging_root / f"nvim_container_{raw_architecture}.sif"
        fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        digest = hashlib.sha256()
        try:
            while True:
                chunk = os.read(source_fd, 1024 * 1024)
                if not chunk:
                    break
                digest.update(chunk)
                os.write(fd, chunk)
            os.fsync(fd)
        finally:
            os.close(fd)
        after = os.fstat(source_fd)
        if (source_stat.st_dev, source_stat.st_ino, source_stat.st_size) != (after.st_dev, after.st_ino, after.st_size):
            raise DeploymentError("image source changed while staging")
        if digest.hexdigest() != expected_digest:
            destination.unlink(missing_ok=True)
            raise DeploymentError("staged image digest mismatch")
        checksum = pathlib.Path(str(destination) + ".sha256")
        checksum_fd = os.open(checksum, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        try:
            os.write(checksum_fd, (expected_digest + "  " + destination.name + "\n").encode("ascii"))
            os.fsync(checksum_fd)
        finally:
            os.close(checksum_fd)
        fsync_directory(staging_root)
        return destination
    finally:
        os.close(source_fd)


def _digest(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    try:
        before = os.lstat(path)
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError as exc:
        raise DeploymentError(f"cannot digest unsafe file: {path}") from exc
    try:
        entry = os.fstat(fd)
        if (before.st_dev, before.st_ino) != (entry.st_dev, entry.st_ino) or not stat.S_ISREG(entry.st_mode):
            raise DeploymentError(f"cannot digest non-regular file: {path}")
        while True:
            block = os.read(fd, 1024 * 1024)
            if not block:
                break
            digest.update(block)
    finally:
        os.close(fd)
    final = os.lstat(path)
    if (before.st_dev, before.st_ino, before.st_size) != (final.st_dev, final.st_ino, final.st_size):
        raise DeploymentError(f"file changed while digesting: {path}")
    return digest.hexdigest()


def verify_staged_image(path: pathlib.Path, expected_digest: str):
    """Revalidate a staged private image immediately before installation."""
    _regular_owned(path, 0o600)
    if _digest(path) != expected_digest:
        raise DeploymentError("staged image changed after validation")


def verify_installed_image(home: pathlib.Path, expected_digest: str) -> pathlib.Path:
    """Resolve and verify the active managed image without accepting link escape."""
    container_dir = pathlib.Path(os.environ.get("NVIM_CONTAINER_DIR", home / "containers"))
    _safe_owned_directory(container_dir)
    active = container_dir / "neovim.sif"
    entry = os.lstat(active)
    if not stat.S_ISLNK(entry.st_mode) or entry.st_uid != os.getuid():
        raise DeploymentError("active image is not a current-user-owned symbolic link")
    target_name = os.readlink(active)
    if not re.fullmatch(r"neovim_[A-Za-z0-9._-]+\.sif", target_name):
        raise DeploymentError("active image link has an unsafe target")
    target = container_dir / target_name
    _regular_owned(target)
    if _digest(target) != expected_digest:
        raise DeploymentError("installed image digest does not match the release artifact")
    return target


def load_recovery_record(state_root: pathlib.Path, deployment_id: str) -> dict:
    """Open the canonical state-bound failed or in-progress deployment record.

    Args:
        state_root: Validated private deployment state directory.
        deployment_id: UUID selecting one canonical record filename.

    Returns:
        Parsed schema-1 failed or in-progress deployment record.

    Raises:
        DeploymentError: If identity, schema, state, path binding, or authority
            fields are unsafe. The function does not delete or rewrite evidence.

    ``deployment_id`` is the only caller-controlled selector.  The function
    rejects copied/moved records by binding the stored state root and filename to
    the validated state directory; it does not delete evidence.
    """
    if not UUID.fullmatch(deployment_id):
        raise DeploymentError("invalid deployment UUID")
    _safe_owned_directory(state_root)
    path = state_root / (deployment_id + ".json")
    record = load_json(path, "deployment record")
    if any(key.startswith("cgroup") for key in record):
        raise DeploymentError("legacy cgroup deployment record requires recovery with the prior implementation")
    if FORBIDDEN_AUTHORITY_ALIASES & set(record):
        raise DeploymentError("deployment record contains obsolete release-authority fields")
    if record.get("schema") != SCHEMA:
        raise DeploymentError("unsupported deployment record schema")
    if record.get("id") != deployment_id or record.get("deployer") != DEPLOYER_ID:
        raise DeploymentError("malformed deployment record")
    if record.get("state") not in {"failed", "in_progress"} or not isinstance(record.get("resources"), list):
        raise DeploymentError("deployment record is not recoverable")
    if record.get("state_root") not in (None, str(state_root)):
        raise DeploymentError("deployment record was moved or copied")
    return record


class UserLock:
    """A current-user deployment lock held through status, mutation, and verify."""

    def __init__(self, path: pathlib.Path):
        self.path, self.fd = path, None

    def __enter__(self):
        _safe_owned_directory(self.path.parent, create=True)
        try:
            self.fd = os.open(self.path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        except OSError as exc:
            raise DeploymentError("deployment lock path or identity is unsafe") from exc
        os.fchmod(self.fd, 0o600)
        descriptor = os.fstat(self.fd)
        path_entry = os.lstat(self.path)
        if not stat.S_ISREG(descriptor.st_mode) or descriptor.st_uid != os.getuid() or (descriptor.st_dev, descriptor.st_ino) != (path_entry.st_dev, path_entry.st_ino):
            os.close(self.fd)
            self.fd = None
            raise DeploymentError("deployment lock path or identity is unsafe")
        try:
            fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise DeploymentError("another deployment or startup holds the interlock") from exc
        return self

    def __exit__(self, *_):
        if self.fd is not None:
            fcntl.flock(self.fd, fcntl.LOCK_UN)
            os.close(self.fd)


def _hostname() -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "_", socket.gethostname() or "unknown")


def classify_existing_interlock(destination: pathlib.Path, maintenance_quiesced: bool = False) -> str:
    """Classify the startup exclusion contract of an existing MkChad checkout."""
    head = run_git(["rev-parse", "HEAD"], destination).stdout.strip()
    if head == LEGACY_MKCHAD_REVISION:
        return "legacy-v1"
    contracts = destination / "lua" / "mkchad" / "opencode" / "contracts.lua"
    if contracts.is_file():
        source = contracts.read_text(encoding="utf-8", errors="strict")
        if re.search(r"deployment_interlock_schema\s*=\s*1\b", source):
            return "host-lock-v1"
    if maintenance_quiesced:
        return "operator-quiesced"
    raise DeploymentError("existing MkChad revision has no recognized deployment interlock; explicit maintenance quiescence is required")


class HostInterlock:
    """Hold legacy startup exclusion first, then the shared deployment lock."""

    def __init__(self, roots: dict[str, pathlib.Path], maintenance_quiesced: bool = False, allow_missing_lifecycle: bool = False):
        self.roots = roots
        self.maintenance_quiesced = maintenance_quiesced
        self.allow_missing_lifecycle = allow_missing_lifecycle
        self.legacy_fd = None
        self.deployment = None

    def __enter__(self):
        mkchad = self.roots["xdg_config_home"] / "mkchad"
        existing = mkchad.exists() or mkchad.is_symlink()
        kind = classify_existing_interlock(mkchad, self.maintenance_quiesced) if existing else "fresh"
        if kind == "legacy-v1":
            authority = self.roots["xdg_state_home"] / "mkchad" / "opencode" / _hostname()
            _safe_owned_directory(authority, create=True)
            startup_lock = authority / "startup.lock"
            if startup_lock.exists() or startup_lock.is_symlink():
                raise DeploymentError("legacy MkChad startup transition is active or ambiguous")
            fence = authority / "lifecycle.fence"
            self.legacy_fd = os.open(fence, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
            os.fchmod(self.legacy_fd, 0o600)
            descriptor, path_entry = os.fstat(self.legacy_fd), os.lstat(fence)
            if not stat.S_ISREG(path_entry.st_mode) or path_entry.st_uid != os.getuid() or (descriptor.st_dev, descriptor.st_ino) != (path_entry.st_dev, path_entry.st_ino):
                raise DeploymentError("legacy lifecycle fence identity is unsafe")
            fcntl.flock(self.legacy_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        self.deployment = UserLock(self.roots["xdg_state_home"] / "mkchad" / "deployment.lock")
        self.deployment.__enter__()
        self._prove_inactive(existing)
        return self

    def _prove_inactive(self, existing: bool):
        launcher = self.roots["home"] / ".local" / "bin" / "mkchad-opencode-server"
        if not launcher.exists():
            if existing and not self.allow_missing_lifecycle:
                raise DeploymentError("existing MkChad installation has no observational lifecycle command")
            return
        entry = os.lstat(launcher)
        if not stat.S_ISREG(entry.st_mode) or entry.st_uid != os.getuid() or not entry.st_mode & stat.S_IXUSR:
            raise DeploymentError("observational lifecycle command is unsafe")
        environment = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "HOME": str(self.roots["home"]),
            "XDG_CONFIG_HOME": str(self.roots["xdg_config_home"]),
            "XDG_DATA_HOME": str(self.roots["xdg_data_home"]),
            "XDG_STATE_HOME": str(self.roots["xdg_state_home"]),
            "XDG_CACHE_HOME": str(pathlib.Path(os.environ.get("XDG_CACHE_HOME", self.roots["home"] / ".cache"))),
        }
        try:
            result = subprocess.run([str(launcher), "status", "--json"], env=environment, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10)
            payload = json.loads(result.stdout) if result.returncode == 0 else None
        except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError) as exc:
            raise DeploymentError("managed service status is unavailable or ambiguous") from exc
        if not isinstance(payload, dict) or payload.get("status") != "inactive":
            raise DeploymentError("managed service is active or its status is ambiguous")

    def __exit__(self, *args):
        if self.deployment is not None:
            self.deployment.__exit__(*args)
        if self.legacy_fd is not None:
            fcntl.flock(self.legacy_fd, fcntl.LOCK_UN)
            os.close(self.legacy_fd)


class SessionScope:
    """Run direct actions in new sessions with bounded diagnostics and cleanup.

    The unreaped direct leader is the PID/process-group anchor until Linux
    procfs shows that no live current-user member remains in its session and
    process group. Descendants that escape that boundary are an accepted
    residual and are not claimed as absent.
    """

    diagnostic_limit = 1024 * 1024

    def __init__(self, timeout: float = 300, term_grace: float = 5, kill_grace: float = 5, activity_callback=None):
        self.timeout = timeout
        self.term_grace = term_grace
        self.kill_grace = kill_grace
        self.activity_callback = activity_callback
        self.active_pid = None
        self.cleanup_unconfirmed = False

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return None

    @staticmethod
    def _leader_identity(pid: int):
        try:
            entry = os.stat(f"/proc/{pid}")
            fields = pathlib.Path(f"/proc/{pid}/stat").read_text(encoding="ascii").rsplit(") ", 1)[1].split()
            return (entry.st_uid, int(fields[19]), int(fields[3]), int(fields[2]), fields[0] == "Z")
        except (FileNotFoundError, IndexError, ValueError, OSError):
            return None

    def _live_group_members(self, session: int, group: int) -> list[int]:
        members = []
        try:
            candidates = list(pathlib.Path("/proc").iterdir())
        except OSError as exc:
            raise DeploymentError("cannot inspect supervised process group") from exc
        for candidate in candidates:
            if candidate.name.isdecimal():
                identity = self._leader_identity(int(candidate.name))
                if identity is not None and identity[0] == os.getuid() and identity[2] == session and identity[3] == group and not identity[4]:
                    members.append(int(candidate.name))
        return members

    def _terminal_status(self, pid: int):
        try:
            return os.waitid(os.P_PID, pid, os.WEXITED | os.WNOHANG | os.WNOWAIT)
        except ChildProcessError as exc:
            raise DeploymentError("supervised leader was reaped unexpectedly") from exc

    def _report_activity(self, argv: list[str], environment: dict[str, str]):
        if self.activity_callback is None:
            return
        executable = argv[0] if os.path.isabs(argv[0]) else shutil.which(argv[0], path=environment["PATH"])
        if not executable:
            raise DeploymentError("supervised executable is unavailable")
        try:
            entry = os.stat(executable)
        except OSError as exc:
            raise DeploymentError("cannot identify supervised executable") from exc
        self.activity_callback({"kind": pathlib.Path(argv[0]).name, "executable": {"device": entry.st_dev, "inode": entry.st_ino, "size": entry.st_size, "mtime_ns": entry.st_mtime_ns}, "started": int(time.time())})

    def _clear_activity(self):
        if self.activity_callback is not None:
            self.activity_callback(None)

    def _cleanup(self, pid: int, identity, reason: str = "cleanup"):
        current = self._leader_identity(pid)
        if current is None or current[:4] != identity[:4]:
            self.cleanup_unconfirmed = True
            raise DeploymentError("supervised leader identity changed; refusing to signal")
        session, group = identity[2], identity[3]
        for signal_value, deadline in ((signal.SIGTERM, self.term_grace), (signal.SIGKILL, self.kill_grace)):
            try:
                os.killpg(group, signal_value)
            except ProcessLookupError:
                pass
            end = time.monotonic() + deadline
            while time.monotonic() < end:
                if not self._live_group_members(session, group):
                    return
                time.sleep(0.02)
        if self._live_group_members(session, group):
            self.cleanup_unconfirmed = True
            raise DeploymentError("cleanup-unconfirmed: supervised process group remains live")

    @staticmethod
    def _reap(pid: int):
        try:
            return os.waitid(os.P_PID, pid, os.WEXITED)
        except ChildProcessError as exc:
            raise DeploymentError("supervised leader was reaped unexpectedly") from exc

    def run(self, argv: list[str], timeout: int | float | None = None, extra_env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
        """Execute one argv action as a session leader and return bounded output.

        A timeout, failure, signal, diagnostic overflow, or surviving group
        member receives TERM for five seconds then KILL for five seconds. The
        leader is reaped only after the group is observed empty.
        """
        if not argv or any(not isinstance(item, str) or "\x00" in item for item in argv):
            raise DeploymentError("invalid supervised subprocess argv")
        environment = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": os.environ.get("HOME", "/nonexistent"), "LC_ALL": "C", "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull, "GIT_TERMINAL_PROMPT": "0"}
        environment.update(extra_env or {})
        self._report_activity(argv, environment)
        try:
            process = subprocess.Popen(argv, env=environment, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True, close_fds=True)
        except Exception:
            self._clear_activity()
            raise
        self.active_pid = process.pid
        self.cleanup_unconfirmed = False
        identity = self._leader_identity(process.pid)
        if identity is None or identity[0] != os.getuid() or identity[2] != process.pid or identity[3] != process.pid:
            process.kill()
            try:
                process.wait(timeout=self.kill_grace)
                self.active_pid = None
            except subprocess.TimeoutExpired:
                self.cleanup_unconfirmed = True
            if self.active_pid is None:
                self._clear_activity()
            raise DeploymentError("cannot establish supervised session identity")
        selector = selectors.DefaultSelector()
        stdout_fd = process.stdout.fileno()
        stderr_fd = process.stderr.fileno()
        streams = {stdout_fd: bytearray(), stderr_fd: bytearray()}
        selector.register(process.stdout, selectors.EVENT_READ)
        selector.register(process.stderr, selectors.EVENT_READ)
        failure = None
        deadline = time.monotonic() + (self.timeout if timeout is None else timeout)
        terminal = None
        try:
            while terminal is None and failure is None:
                remaining = max(0, deadline - time.monotonic())
                for key, _ in selector.select(min(0.05, remaining)):
                    chunk = os.read(key.fd, 65536)
                    if not chunk:
                        selector.unregister(key.fileobj)
                        continue
                    remaining_capacity = self.diagnostic_limit - len(streams[key.fd])
                    streams[key.fd].extend(chunk[:remaining_capacity])
                    if len(chunk) > remaining_capacity:
                        failure = "diagnostic output exceeded 1 MiB"
                        break
                terminal = self._terminal_status(process.pid)
                if terminal is None and time.monotonic() >= deadline:
                    failure = "supervised subprocess timed out"
            if terminal is not None and terminal.si_code != os.CLD_EXITED:
                failure = failure or "supervised subprocess exited by signal"
            if terminal is not None and terminal.si_code == os.CLD_EXITED and terminal.si_status != 0:
                failure = failure or "supervised subprocess failed"
            live = self._live_group_members(identity[2], identity[3])
            if live and (live != [process.pid] or terminal is not None):
                failure = failure or "supervised process group has an unexpected live member"
            if failure:
                self._cleanup(process.pid, identity, failure)

            drain_deadline = time.monotonic() + 1
            while selector.get_map() and time.monotonic() < drain_deadline:
                for key, _ in selector.select(min(0.05, max(0, drain_deadline - time.monotonic()))):
                    chunk = os.read(key.fd, 65536)
                    if chunk:
                        remaining_capacity = self.diagnostic_limit - len(streams[key.fd])
                        streams[key.fd].extend(chunk[:remaining_capacity])
                        if len(chunk) > remaining_capacity:
                            failure = failure or "diagnostic output exceeded 1 MiB"
                    else:
                        selector.unregister(key.fileobj)
            if selector.get_map():
                failure = failure or "diagnostic streams remained open after leader exit"
                self._cleanup(process.pid, identity, failure)

            stdout, stderr = bytes(streams[stdout_fd]), bytes(streams[stderr_fd])
            if terminal is not None and terminal.si_code == os.CLD_EXITED and terminal.si_status != 0:
                if failure == "supervised subprocess failed":
                    failure = f"supervised subprocess failed with exit {terminal.si_status}; stderr-sha256={hashlib.sha256(stderr).hexdigest()}"
            live = self._live_group_members(identity[2], identity[3])
            if live and (live != [process.pid] or terminal is not None):
                failure = failure or "supervised process group has an unexpected live member"
                self._cleanup(process.pid, identity, failure)
            if live and not failure:
                raise DeploymentError("supervised leader remains live without terminal status")
            reaped = self._reap(process.pid)
            self.active_pid = None
            process.returncode = reaped.si_status if reaped.si_code == os.CLD_EXITED else -reaped.si_status
            if failure:
                raise DeploymentError(failure)
            return subprocess.CompletedProcess(argv, 0, stdout.decode("utf-8", "replace"), stderr.decode("utf-8", "replace"))
        finally:
            selector.close()
            process.stdout.close()
            process.stderr.close()
            if self.active_pid is None:
                self._clear_activity()

    @property
    def machine_cleanup_observed(self) -> bool:
        """Return whether this supervisor owns no unresolved direct child."""
        return self.active_pid is None and not self.cleanup_unconfirmed


def validate_image_runtime(scope: SessionScope, staged_image: pathlib.Path, artifact: dict, staged_msk: pathlib.Path, msk_commit: str, staged_opencode: pathlib.Path, opencode_authority: dict) -> dict:
    """Validate architecture, embedded manifest, and unique OpenCode release tag.

    Runtime execution receives only the staged image under a clean environment,
    a contained temporary filesystem, and no host home or hostfs bind. OpenCode
    receives that contained ``/tmp`` as HOME so version inspection cannot write
    into the read-only image. The component manifest is compared to the pinned
    ``msk_containers`` tree before any final destination mutation.
    """
    runtime = os.environ.get("MKCHAD_CONTAINER_RUNTIME")
    if not runtime or not os.path.isabs(runtime) or not os.access(runtime, os.X_OK):
        raise DeploymentError("a trusted explicit container runtime is required")
    common = [runtime, "exec", "--cleanenv", "--contain", "--no-home", "--no-mount", "hostfs", str(staged_image)]
    architecture = scope.run([*common, "uname", "-m"]).stdout.strip().lower()
    normalized = {"amd64": "x86_64", "x86": "x86_64", "arm64": "aarch64", "ppc64el": "ppc64le"}.get(architecture, architecture)
    if normalized != artifact["architecture"]:
        raise DeploymentError("staged image architecture mismatch")
    manifest_text = scope.run([*common, "cat", "/usr/share/mkchad/component-manifest.json"]).stdout
    try:
        manifest = json.loads(manifest_text, object_pairs_hook=_reject_duplicates)
    except json.JSONDecodeError as exc:
        raise DeploymentError("image component manifest is malformed") from exc
    if hashlib.sha256(manifest_text.encode()).hexdigest() != artifact["component_manifest_sha256"]:
        raise DeploymentError("image component manifest digest mismatch")
    expected = run_git(["-C", str(staged_msk), "show", f"{msk_commit}:nvim/component-manifest.json"]).stdout
    if json.loads(expected) != manifest:
        raise DeploymentError("image component manifest does not match pinned msk_containers")
    _contained_git(scope, ["-C", str(staged_opencode), "fetch", "--force", opencode_authority["url"], "refs/tags/v*:refs/tags/v*"])
    tags = run_git(["-C", str(staged_opencode), "for-each-ref", "--format=%(refname:short)", "--points-at", opencode_authority["commit"], "refs/tags/v*-mkchad.*"]).stdout.splitlines()
    if len(tags) != 1:
        raise DeploymentError("OpenCode gitlink has no unique mkchad release tag")
    image_version = scope.run([*common, "env", "HOME=/tmp", "opencode", "--version"]).stdout.strip()
    relationships = manifest.get("relationships") if isinstance(manifest, dict) else None
    versions = [item.get("contract", {}).get("version") for item in relationships or [] if isinstance(item, dict) and item.get("id") == "ships-opencode"]
    expected_version = versions[0] if len(versions) == 1 else None
    reported_versions = re.findall(r"\b[0-9]+\.[0-9]+\.[0-9]+-mkchad\.[0-9]+\b", image_version)
    if not isinstance(expected_version, str) or reported_versions != [expected_version] or tags != ["v" + expected_version]:
        raise DeploymentError("embedded OpenCode identity does not match release tag")
    return manifest


def expected_managed_image_name(manifest: dict, revision: str, digest: str, architecture: str) -> str:
    """Derive the exact filename produced by the authoritative raw-image installer."""
    relationships = manifest.get("relationships") if isinstance(manifest, dict) else None
    versions = [item.get("contract", {}).get("version") for item in relationships or [] if isinstance(item, dict) and item.get("id") == "ships-neovim"]
    if len(versions) != 1 or not isinstance(versions[0], str) or not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", versions[0]):
        raise DeploymentError("image manifest has no unique Neovim version")
    return f"neovim_{versions[0]}_g{revision}_{digest}_{architecture}.sif"


class DeploymentJournal:
    """Schema-1 durable journal that retains digest-bound offline recovery input."""

    def __init__(self, state_root: pathlib.Path, verified_release: dict, resources: list[dict], deployment_id: str | None = None):
        _safe_owned_directory(state_root, create=True)
        self.state_root = state_root
        self.id = deployment_id or str(uuid.uuid4())
        self.bundle = state_root / (self.id + ".bundle")
        self.bundle.mkdir(mode=0o700)
        self.record = {"schema": SCHEMA, "id": self.id, "deployer": DEPLOYER_ID, "state": "in_progress", "phase": "journal", "final_mutation_started": False, "state_root": str(state_root), "authority": _closed_authority(verified_release), "recovery_bundle": self.bundle.name, "resources": resources, "bundle_digests": {}, "process_cleanup": {"machine_observed": False, "operator_asserted": False}}
        self.path = state_root / (self.id + ".json")

    def retain(self, name: str, source: pathlib.Path):
        """Copy and digest one safe source into the private recovery bundle.

        Args:
            name: New basename beneath this deployment's bundle.
            source: Current-user-owned regular source without group/world write.

        Raises:
            DeploymentError: If identity changes, the target exists, or a full
                durable mode-``0600`` copy cannot be made.

        Side effects:
            Adds the copied file and its SHA-256 to the in-memory journal.
        """
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", name):
            raise DeploymentError("invalid recovery bundle name")
        target = self.bundle / name
        before = _regular_owned(source)
        if before.st_mode & 0o022:
            raise DeploymentError(f"unsafe retained source: {source}")
        source_fd = os.open(source, os.O_RDONLY | os.O_NOFOLLOW)
        target_fd = None
        target_created = False
        try:
            opened = os.fstat(source_fd)
            if (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino):
                raise DeploymentError("retained source changed before open")
            target_fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            target_created = True
            os.fchmod(target_fd, 0o600)
            while True:
                block = os.read(source_fd, 1024 * 1024)
                if not block:
                    break
                view = memoryview(block)
                while view:
                    view = view[os.write(target_fd, view):]
            os.fsync(target_fd)
            after = os.fstat(source_fd)
            final = os.lstat(source)
            if (opened.st_dev, opened.st_ino, opened.st_size) != (after.st_dev, after.st_ino, after.st_size) or (opened.st_dev, opened.st_ino) != (final.st_dev, final.st_ino):
                raise DeploymentError("retained source changed while copying")
        except FileExistsError as exc:
            if target_created:
                target.unlink(missing_ok=True)
            raise DeploymentError("recovery bundle target collision") from exc
        except Exception:
            if target_created:
                target.unlink(missing_ok=True)
            raise
        finally:
            os.close(source_fd)
            if target_fd is not None:
                os.close(target_fd)
        self.record["bundle_digests"][name] = _digest(target)
        fsync_directory(self.bundle)

    def discard_unpublished(self):
        """Remove a provisional bundle before its journal becomes visible.

        Raises:
            DeploymentError: If a journal path already exists.

        Side effects:
            Deletes only this deployment's unpublished private bundle and
            flushes the state directory.
        """
        if self.path.exists() or self.path.is_symlink():
            raise DeploymentError("cannot discard a published deployment journal")
        shutil.rmtree(self.bundle)
        fsync_directory(self.state_root)

    def register_retained(self, name: str):
        """Authenticate a private file generated directly in the recovery bundle."""
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", name):
            raise DeploymentError("invalid recovery bundle name")
        path = self.bundle / name
        _regular_owned(path, 0o600)
        with open(path, "rb") as handle:
            os.fsync(handle.fileno())
        self.record["bundle_digests"][name] = _digest(path)
        fsync_directory(self.bundle)

    def persist(self):
        """Durably update the journal before the next final-path mutation."""
        atomic_json(self.path, self.record)

    def record_action(self, action: dict | None):
        """Persist redacted subprocess context for interruption diagnostics."""
        if action is None:
            self.record.pop("active_action", None)
        else:
            self.record["active_action"] = action
        self.persist()

    def failed(self, phase: str, reason: str, machine_cleanup_observed: bool = True):
        """Finalize a catchable failure while retaining all recovery evidence."""
        state = "failed" if machine_cleanup_observed else "in_progress"
        self.record.update({"state": state, "phase": phase, "process_cleanup": {"machine_observed": machine_cleanup_observed, "operator_asserted": False}, "diagnostic": redact(reason)})
        self.persist()

    def succeeded(self, receipt: dict):
        """Durably record success before removing private retained artifacts."""
        self.record.update({"state": "succeeded", "receipt": receipt})
        self.persist()
        shutil.rmtree(self.bundle)
        fsync_directory(self.state_root)


def redact(message: str) -> str:
    """Remove likely secret-bearing assignments from receipt diagnostics."""
    message = re.sub(r"(?i)(authorization\s*:\s*(?:bearer\s+)?)[^\s,;}]+", r"\1<redacted>", message)
    message = re.sub(r'(?i)(["\']?(?:token|password|secret)["\']?\s*[:=]\s*)["\']?[^\s,;}"\']+', r"\1<redacted>", message)
    return message[:1000]


def _release_file(repo: pathlib.Path) -> pathlib.Path:
    path = repo / "deploy" / "release.json"
    if not path.is_file():
        raise DeploymentError("candidate checkout has no deploy/release.json")
    return path


def validate_candidate(repo: pathlib.Path, parent_commit: str | None = None) -> tuple[dict, dict]:
    """Validate candidate metadata and derive authority only from parent gitlinks."""
    commit = parent_commit or run_git(["rev-parse", "HEAD"], repo).stdout.strip()
    gitlinks = _derive_release_gitlinks(repo, commit)
    release = validate_release_document(load_release_json(_release_file(repo)), list(gitlinks))
    return release, gitlinks


def _architecture() -> str:
    value = os.uname().machine.lower()
    return {"amd64": "x86_64", "x86": "x86_64", "arm64": "aarch64", "ppc64el": "ppc64le"}.get(value, value)


def _artifact_for(release: dict) -> dict:
    architecture = _architecture()
    found = [item for item in release["artifacts"]["nvim-image"] if item["architecture"] == architecture]
    if len(found) != 1:
        raise DeploymentError(f"release has no unique image for architecture {architecture}")
    return found[0]


def _contained_git(scope: SessionScope, argv: list[str], extra_env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    """Run one mutation-bearing Git operation in a supervised POSIX session."""
    return scope.run(["git", "-c", "core.hooksPath=/dev/null", "-c", "init.templateDir=", *argv], extra_env=extra_env)


def _prepare_exact_checkout(
    source: pathlib.Path, destination: pathlib.Path, commit: str, origin: str, scope: SessionScope
) -> None:
    """Initialize an empty checkout by fetching one exact authorized commit."""
    _contained_git(scope, ["init", str(destination)])
    _contained_git(scope, ["-C", str(destination), "fetch", "--no-tags", str(source), commit])
    _contained_git(scope, ["-C", str(destination), "remote", "add", "origin", origin])
    _contained_git(scope, ["-C", str(destination), "checkout", "--detach", "--no-recurse-submodules", commit])


def stage_child(url: str, commit: str, staging_root: pathlib.Path, name: str, scope: SessionScope) -> pathlib.Path:
    """Fetch one exact child object into an independent private bare repository."""
    bare = staging_root / (name.replace("/", "_") + ".git")
    _contained_git(scope, ["init", "--bare", str(bare)])
    _contained_git(scope, ["-C", str(bare), "fetch", "--no-tags", url, commit])
    if run_git(["-C", str(bare), "cat-file", "-e", commit + "^{commit}"], check=False).returncode:
        raise DeploymentError(f"staged child object is unavailable: {name}")
    return bare


def validate_action_files(staged: pathlib.Path, commit: str, component: dict):
    """Require every action to be an executable regular blob in the pinned tree."""
    for action in component.get("actions", []):
        line = run_git(["-C", str(staged), "ls-tree", commit, "--", action["path"]]).stdout.strip()
        match = re.fullmatch(r"(100[0-7]{3}) blob ([0-9a-f]{40,64})\t(.+)", line)
        if not match or match.group(3) != action["path"] or not (int(match.group(1), 8) & stat.S_IXUSR):
            raise DeploymentError(f"action is not an executable regular file in the pinned tree: {action['id']}")


def validate_configuration_pin(staged_mkchad: pathlib.Path, mkchad_commit: str, opencode_nvim_commit: str):
    """Bind MkChad's owner declaration to the parent opencode.nvim gitlink."""
    source = run_git(["-C", str(staged_mkchad), "show", f"{mkchad_commit}:lua/mkchad/opencode/contracts.lua"]).stdout
    matches = re.findall(r'opencode_nvim_revision\s*=\s*"([0-9a-f]{40})"', source)
    if matches != [opencode_nvim_commit]:
        raise DeploymentError("MkChad opencode_nvim_revision does not match the parent gitlink")


def retain_repository_bundle(journal: DeploymentJournal, name: str, repository: pathlib.Path, commits: list[str], scope: SessionScope):
    """Retain exact Git objects for network-independent complete or restore."""
    refs = []
    try:
        for index, commit in enumerate(dict.fromkeys(commits)):
            if not SHA1.fullmatch(commit):
                continue
            ref = f"refs/mkchad-recovery/{journal.id}/{index}"
            _contained_git(scope, ["-C", str(repository), "update-ref", ref, commit])
            refs.append(ref)
        if not refs:
            raise DeploymentError(f"no recoverable Git objects for {name}")
        target = journal.bundle / f"{name}.bundle"
        _contained_git(scope, ["-C", str(repository), "bundle", "create", str(target), *refs])
        os.chmod(target, 0o600)
        journal.register_retained(f"{name}.bundle")
    finally:
        for ref in refs:
            _contained_git(scope, ["-C", str(repository), "update-ref", "-d", ref])


def _no_replace_rename(source: pathlib.Path, destination: pathlib.Path):
    # Linux renameat2 is required because os.rename would overwrite a racing path.
    numbers = {"x86_64": 316, "aarch64": 276, "ppc64le": 357}
    number = numbers.get(os.uname().machine)
    if number is None:
        raise DeploymentError("Linux no-replace rename is unavailable on this architecture")
    result = ctypes.CDLL(None, use_errno=True).syscall(number, -100, os.fsencode(source), -100, os.fsencode(destination), 1)
    if result:
        err = ctypes.get_errno()
        if err in (errno.EEXIST, errno.ENOTEMPTY):
            raise DeploymentError("destination appeared after preflight")
        raise DeploymentError(f"renameat2(RENAME_NOREPLACE) failed: {os.strerror(err)}")


def install_or_update_component(destination: pathlib.Path, staged: pathlib.Path, component: dict, authority: dict, journal: DeploymentJournal, scope: SessionScope, preflight: dict):
    """Install an absent checkout or detached-update a preflighted clean checkout."""
    commit, repository = authority["commit"], authority["repository"]
    preserved = MKCHAD_PRESERVED_PATHS if component["name"] == "mkchad" else set()
    if preflight["kind"] == "existing":
        current = preflight_existing_checkout(destination, repository, commit, preserved)
        if current != preflight:
            raise DeploymentError("checkout changed after all-destination preflight")
        if current["compliant"]:
            return {"name": component["name"], "repository": repository, "destination": str(destination), "previous_revision": current["head"], "revision": commit, "result": "no-op"}
        resource = {"kind": "checkout", "name": component["name"], "previous": current["head"], "intended": commit, "identity": current["identity"], "state": "pending"}
        journal.record["resources"].append(resource)
        journal.persist()
        if _identity(destination) != current["identity"]:
            raise DeploymentError("checkout destination changed after preflight")
        _contained_git(scope, ["-C", str(destination), "fetch", "--no-tags", str(staged), commit])
        transactional_git_checkout(destination, commit, scope, journal, resource)
        return {"name": component["name"], "repository": repository, "destination": str(destination), "previous_revision": current["head"], "revision": commit, "result": "updated"}
    create_preflighted_parents(preflight, component, journal)
    if destination.exists() or destination.is_symlink():
        raise DeploymentError("absent destination changed after all-destination preflight")
    parent_identity = _identity(destination.parent)
    temporary = pathlib.Path(tempfile.mkdtemp(prefix=".mkchad-clone-", dir=destination.parent))
    try:
        _prepare_exact_checkout(staged, temporary, commit, authority["url"], scope)
        if _identity(destination.parent) != parent_identity:
            raise DeploymentError("destination parent changed after preflight")
        resource = {"kind": "checkout", "name": component["name"], "previous": None, "intended": commit, "state": "pending"}
        journal.record["resources"].append(resource)
        journal.persist()
        _no_replace_rename(temporary, destination)
        resource["state"] = "installed"
        resource["identity"] = _identity(destination)
        journal.persist()
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)
    return {"name": component["name"], "repository": repository, "destination": str(destination), "previous_revision": None, "revision": commit, "result": "installed"}


def run_installer_actions(
    scope: SessionScope,
    component: dict,
    msk_destination: pathlib.Path,
    staged_image: pathlib.Path,
    expected_digest: str,
    expected_target: str,
    revision: str,
    journal: DeploymentJournal,
) -> list[dict]:
    """Run the U1 installer-owned public actions from the pinned checkout.

    The host-tool check is deliberately separate from apply.  The recovery
    directory belongs to the deployment journal, and image installation receives
    the staged image plus the exact gitlink-derived source revision. Explicit
    group-write trust is passed only as a fixed Boolean environment value.
    """
    recovery = journal.bundle / "host-tools"
    recovery.mkdir(mode=0o700)
    tokens = {"recovery_dir": str(recovery), "staged_image": str(staged_image), "component_revision": revision}
    results = []
    for action in component["actions"]:
        script = msk_destination / action["path"]
        entry = os.lstat(script)
        if not stat.S_ISREG(entry.st_mode) or stat.S_ISLNK(entry.st_mode) or not entry.st_mode & stat.S_IXUSR:
            raise DeploymentError(f"pinned installer is not a regular executable: {script}")
        argv = [str(script)]
        for argument in action["args"]:
            argv.append(argument if isinstance(argument, str) else tokens[argument["token"]])
        resource = None
        if action["id"] == "host-tools-install":
            resource = snapshot_host_tools(msk_destination, pathlib.Path(os.environ["HOME"]), journal)
        if action["id"] == "image-install":
            verify_staged_image(staged_image, expected_digest)
            resource = snapshot_image_links(pathlib.Path(os.environ["HOME"]), expected_digest, expected_target, revision, staged_image.name, journal)
        extra_env = _installer_trust_environment() if action["id"] in {"host-tools-check", "host-tools-install"} else None
        scope.run(argv, extra_env=extra_env)
        if resource is not None:
            resource["state"] = "applied"
            if action["id"] == "host-tools-install":
                verify_host_tools(pathlib.Path(os.environ["HOME"]), resource)
            if action["id"] == "image-install":
                installed = verify_installed_image(pathlib.Path(os.environ["HOME"]), expected_digest)
                if installed.name != expected_target:
                    raise DeploymentError("image installer produced an unexpected managed filename")
                resource["intended_target"] = installed.name
                resource["intended_digest"] = expected_digest
            journal.persist()
        results.append({"component": component["name"], "id": action["id"], "result": "succeeded"})
    return results


def installer_owned_sources(msk_destination: pathlib.Path) -> list[pathlib.Path]:
    """Return the exact public host-installer source set without duplicating outputs."""
    sources = sorted((msk_destination / "container_tools").glob("*.sh"))
    bin_dir = msk_destination / "nvim" / "bin"
    sources.extend(sorted(bin_dir.glob("*.sh")))
    for name in ("nvim", "nvim_shell", "nvim_clear_data", "mkchad", "mkchad-container-bootstrap", "mkchad-status-host-evidence", "mkchad-opencode-server-image", "mkchad-opencode-server", "install_nvim_container"):
        path = bin_dir / name
        if path.exists():
            sources.append(path)
    by_name = {}
    for source in sources:
        if source.name in by_name:
            raise DeploymentError(f"installer sources have a duplicate output name: {source.name}")
        by_name[source.name] = source
    return list(by_name.values())


def snapshot_host_tools(msk_destination: pathlib.Path, home: pathlib.Path, journal: DeploymentJournal) -> dict:
    """Retain every noncompliant host-tool byte before installer mutation."""
    outputs = []
    target_dir = home / ".local" / "bin"
    for source in installer_owned_sources(msk_destination):
        intended = _digest(source)
        target = target_dir / source.name
        previous = None
        backup = None
        if target.exists() or target.is_symlink():
            entry = _regular_owned(target)
            previous = {"sha256": _digest(target), "mode": stat.S_IMODE(entry.st_mode)}
            if previous != {"sha256": intended, "mode": 0o755}:
                backup = "host-tool-" + source.name + ".before"
                journal.retain(backup, target)
        outputs.append({"name": source.name, "intended_sha256": intended, "previous": previous, "backup": backup})
    resource = {"kind": "host-tools", "name": "msk_containers", "state": "pending", "outputs": outputs}
    journal.record["resources"].append(resource)
    journal.persist()
    return resource


def verify_host_tools(home: pathlib.Path, resource: dict):
    """Verify the complete installer-owned output set after apply or recovery."""
    target_dir = home / ".local" / "bin"
    _safe_owned_directory(target_dir)
    for output in resource["outputs"]:
        target = target_dir / output["name"]
        entry = _regular_owned(target, 0o755)
        if stat.S_IMODE(entry.st_mode) != 0o755 or _digest(target) != output["intended_sha256"]:
            raise DeploymentError(f"host-tool verification failed: {output['name']}")


def _snapshot_managed_link(container_dir: pathlib.Path, link_name: str, retained_name: str, journal: DeploymentJournal) -> dict | None:
    link = container_dir / link_name
    if not link.exists() and not link.is_symlink():
        return None
    entry = os.lstat(link)
    if not stat.S_ISLNK(entry.st_mode) or entry.st_uid != os.getuid():
        raise DeploymentError(f"managed image link is unsafe: {link}")
    target_name = os.readlink(link)
    if not re.fullmatch(r"neovim_[A-Za-z0-9._-]+\.sif", target_name):
        raise DeploymentError(f"managed image link target is unsafe: {link}")
    target = container_dir / target_name
    target_entry = _regular_owned(target)
    journal.retain(retained_name, target)
    return {"target": target_name, "sha256": _digest(target), "mode": stat.S_IMODE(target_entry.st_mode), "retained": retained_name}


def snapshot_image_links(home: pathlib.Path, intended_digest: str, intended_target: str, revision: str, retained_image: str, journal: DeploymentJournal) -> dict:
    """Retain active and previous managed-image bytes before activation."""
    container_dir = pathlib.Path(os.environ.get("NVIM_CONTAINER_DIR", home / "containers"))
    if container_dir.exists() or container_dir.is_symlink():
        _safe_owned_directory(container_dir)
    resource = {
        "kind": "image",
        "name": "msk_containers",
        "state": "pending",
        "intended_digest": intended_digest,
        "intended_target": intended_target,
        "revision": revision,
        "retained_image": retained_image,
        "active_before": _snapshot_managed_link(container_dir, "neovim.sif", "image-active-before.sif", journal) if container_dir.exists() else None,
        "previous_before": _snapshot_managed_link(container_dir, "neovim.previous.sif", "image-previous-before.sif", journal) if container_dir.exists() else None,
    }
    journal.record["resources"].append(resource)
    journal.persist()
    return resource


def _atomic_restore_file(source: pathlib.Path, target: pathlib.Path, mode: int):
    parent = target.parent
    _safe_owned_directory(parent)
    expected = _identity(target) if target.exists() or target.is_symlink() else None
    if expected is not None:
        _regular_owned(target)
    fd, temporary = tempfile.mkstemp(prefix=".mkchad-restore-", dir=parent)
    temporary_path = pathlib.Path(temporary)
    try:
        source_fd = os.open(source, os.O_RDONLY | os.O_NOFOLLOW)
        try:
            while True:
                block = os.read(source_fd, 1024 * 1024)
                if not block:
                    break
                view = memoryview(block)
                while view:
                    written = os.write(fd, view)
                    view = view[written:]
        finally:
            os.close(source_fd)
        os.fchmod(fd, mode)
        os.fsync(fd)
        os.close(fd)
        if expected is None:
            if target.exists() or target.is_symlink():
                raise DeploymentError("restore target appeared after preflight")
        elif _identity(target) != expected:
            raise DeploymentError("restore target changed after preflight")
        os.replace(temporary_path, target)
        fsync_directory(parent)
    finally:
        with contextlib.suppress(OSError):
            os.close(fd)
        with contextlib.suppress(FileNotFoundError):
            temporary_path.unlink()


def recover_host_tools(resource: dict, mode: str, component: dict, bundle: pathlib.Path, roots: dict[str, pathlib.Path], scope: SessionScope) -> dict:
    """Complete through the pinned installer or restore retained host-tool bytes."""
    home = roots["home"]
    msk_destination = resolve_named_destination(roots, component["destination"]["root"], component["destination"]["path"])
    if mode == "complete":
        recovery_dir = pathlib.Path(tempfile.mkdtemp(prefix="host-tools-complete-", dir=bundle))
        os.chmod(recovery_dir, 0o700)
        action = next(action for action in component["actions"] if action["id"] == "host-tools-install")
        scope.run(
            [str(msk_destination / action["path"]), "--recovery-dir", str(recovery_dir)],
            extra_env=_installer_trust_environment(),
        )
        verify_host_tools(home, resource)
        return {"name": component["name"], "result": "host-tools-complete"}
    target_dir = home / ".local" / "bin"
    for output in resource.get("outputs", []):
        target = target_dir / output["name"]
        current = None
        if target.exists() or target.is_symlink():
            entry = _regular_owned(target)
            current = {"sha256": _digest(target), "mode": stat.S_IMODE(entry.st_mode)}
        previous = output.get("previous")
        intended = {"sha256": output.get("intended_sha256"), "mode": 0o755}
        if current == previous:
            continue
        if current != intended:
            raise DeploymentError(f"host tool has later edits or unknown recovery state: {output['name']}")
        if previous is None:
            target.unlink()
            fsync_directory(target_dir)
        else:
            backup = output.get("backup")
            if not backup:
                raise DeploymentError(f"host tool recovery bytes are missing: {output['name']}")
            retained = bundle / backup
            _regular_owned(retained, 0o600)
            _atomic_restore_file(retained, target, previous["mode"])
    return {"name": component["name"], "result": "host-tools-restored"}


def _replace_managed_link(container_dir: pathlib.Path, link_name: str, target_name: str | None):
    link = container_dir / link_name
    expected = _identity(link) if link.exists() or link.is_symlink() else None
    if target_name is None:
        if expected is not None:
            entry = os.lstat(link)
            if not stat.S_ISLNK(entry.st_mode) or entry.st_uid != os.getuid():
                raise DeploymentError("managed image link changed during recovery")
            link.unlink()
            fsync_directory(container_dir)
        return
    temporary = container_dir / ("." + link_name + "." + uuid.uuid4().hex)
    os.symlink(target_name, temporary)
    try:
        if expected is None:
            if link.exists() or link.is_symlink():
                raise DeploymentError("managed image link appeared during recovery")
        elif _identity(link) != expected:
            raise DeploymentError("managed image link changed during recovery")
        os.replace(temporary, link)
        fsync_directory(container_dir)
    finally:
        temporary.unlink(missing_ok=True)


def recover_image(resource: dict, mode: str, component: dict, bundle: pathlib.Path, roots: dict[str, pathlib.Path], scope: SessionScope) -> dict:
    """Complete image installation or restore retained active/previous links."""
    classify_image_resource(resource, roots)
    msk_destination = resolve_named_destination(roots, component["destination"]["root"], component["destination"]["path"])
    image_action = next(action for action in component["actions"] if action["id"] == "image-install")
    if mode == "complete":
        argv = [str(msk_destination / image_action["path"]), "--source-revision", resource["revision"], str(bundle / resource["retained_image"])]
        scope.run(argv)
        installed = verify_installed_image(roots["home"], resource["intended_digest"])
        if installed.name != resource["intended_target"]:
            raise DeploymentError("image recovery produced an unexpected managed filename")
        return {"name": component["name"], "result": "image-complete"}
    container_dir = pathlib.Path(os.environ.get("NVIM_CONTAINER_DIR", roots["home"] / "containers"))
    _safe_owned_directory(container_dir)
    for link_name, before in (("neovim.sif", resource.get("active_before")), ("neovim.previous.sif", resource.get("previous_before"))):
        if before is None:
            _replace_managed_link(container_dir, link_name, None)
            continue
        retained = bundle / before["retained"]
        target = container_dir / before["target"]
        if target.exists() or target.is_symlink():
            _regular_owned(target)
            if _digest(target) != before["sha256"]:
                raise DeploymentError("retained image target collides with later bytes")
        else:
            _atomic_restore_file(retained, target, before["mode"])
        _replace_managed_link(container_dir, link_name, before["target"])
    intended_target = resource.get("intended_target")
    if intended_target and intended_target not in {item.get("target") for item in (resource.get("active_before"), resource.get("previous_before")) if item}:
        installed = container_dir / intended_target
        if installed.exists() and not installed.is_symlink() and _digest(installed) == resource["intended_digest"]:
            installed.unlink()
            fsync_directory(container_dir)
    return {"name": component["name"], "result": "image-restored"}


def _managed_link_state(container_dir: pathlib.Path, link_name: str) -> dict | None:
    link = container_dir / link_name
    if not link.exists() and not link.is_symlink():
        return None
    entry = os.lstat(link)
    if not stat.S_ISLNK(entry.st_mode) or entry.st_uid != os.getuid():
        raise DeploymentError("managed image link has an unsafe type or owner")
    target_name = os.readlink(link)
    if not re.fullmatch(r"neovim_[A-Za-z0-9._-]+\.sif", target_name):
        raise DeploymentError("managed image link has an unsafe target")
    target = container_dir / target_name
    _regular_owned(target)
    return {"target": target_name, "sha256": _digest(target)}


def classify_image_resource(resource: dict, roots: dict[str, pathlib.Path]):
    """Require image links to be entirely in recorded pre-state or intended state."""
    container_dir = pathlib.Path(os.environ.get("NVIM_CONTAINER_DIR", roots["home"] / "containers"))
    if not container_dir.exists() and not container_dir.is_symlink():
        if resource.get("active_before") is None and resource.get("previous_before") is None:
            return
        raise DeploymentError("managed image directory disappeared before recovery")
    _safe_owned_directory(container_dir)
    active = _managed_link_state(container_dir, "neovim.sif")
    previous = _managed_link_state(container_dir, "neovim.previous.sif")
    active_before = resource.get("active_before")
    previous_before = resource.get("previous_before")
    intended = {"target": resource.get("intended_target"), "sha256": resource.get("intended_digest")}
    allowed_active = [active_before, intended]
    allowed_previous = [previous_before, active_before]
    if active not in allowed_active or previous not in allowed_previous:
        raise DeploymentError("managed image has later edits or unknown recovery state")


def classify_recovery_resources(resources: list[dict], components: dict, authorities: dict, roots: dict[str, pathlib.Path], mode: str, nested_authority: dict | None = None):
    """Classify every resource before recovery mutates any final path."""
    for resource in resources:
        name = resource.get("name")
        if name not in components or name not in authorities:
            raise DeploymentError("unknown recovery component")
        kind = resource.get("kind")
        component = components[name]
        if kind in {"checkout", "nested-checkout"}:
            active_component = nested_component_contract(component) if kind == "nested-checkout" else component
            active_authority = nested_authority if kind == "nested-checkout" else authorities[name]
            if active_authority is None:
                raise DeploymentError("nested recovery authority is missing")
            destination = resolve_named_destination(roots, active_component["destination"]["root"], active_component["destination"]["path"])
            if not destination.exists() and not destination.is_symlink():
                if resource.get("previous") is not None:
                    raise DeploymentError("existing checkout disappeared before recovery")
                continue
            if kind == "nested-checkout" and resource.get("previous") is None and resource.get("state") == "pending":
                parent_destination = resolve_named_destination(roots, component["destination"]["root"], component["destination"]["path"])
                parent_resource = next((item for item in resources if item.get("kind") == "checkout" and item.get("name") == "msk_containers" and item.get("previous") is None), None)
                entry = os.lstat(destination)
                if (
                    parent_resource
                    and parent_resource.get("state") == "installed"
                    and _identity(parent_destination) == tuple(parent_resource.get("identity", ()))
                    and stat.S_ISDIR(entry.st_mode)
                    and not stat.S_ISLNK(entry.st_mode)
                    and entry.st_uid == os.getuid()
                    and not any(destination.iterdir())
                ):
                    continue
            identity_changed = (
                resource.get("previous") is None
                and resource.get("state") == "installed"
                and _identity(destination) != tuple(resource.get("identity", ()))
            )
            if identity_changed and mode != "complete":
                raise DeploymentError("new checkout identity changed after installation")
            if resource.get("previous") is None and resource.get("state") not in {"pending", "installed"}:
                raise DeploymentError("new checkout has no valid ownership state")
            if resource.get("previous") is None and resource.get("state") == "pending" and mode == "restore":
                raise DeploymentError("pending checkout ownership is ambiguous; restore cannot finalize")
            preserved = MKCHAD_PRESERVED_PATHS if name == "mkchad" else set()
            try:
                current = preflight_existing_checkout(destination, active_authority["repository"], resource["intended"], preserved)
            except DeploymentError:
                if identity_changed:
                    raise DeploymentError("new checkout identity changed after installation") from None
                current = classify_checkout_mixture(destination, active_authority["repository"], resource.get("previous"), resource["intended"], preserved)
            if identity_changed and not current["compliant"]:
                raise DeploymentError("new checkout identity changed after installation")
            if current["head"] not in {value for value in (resource.get("previous"), resource.get("intended")) if value}:
                raise DeploymentError("checkout has later edits or unknown recovery state")
        elif kind == "host-tools":
            target_dir = roots["home"] / ".local" / "bin"
            for output in resource.get("outputs", []):
                target = target_dir / output["name"]
                current = None
                if target.exists() or target.is_symlink():
                    entry = _regular_owned(target)
                    current = {"sha256": _digest(target), "mode": stat.S_IMODE(entry.st_mode)}
                intended = {"sha256": output.get("intended_sha256"), "mode": 0o755}
                if current not in (output.get("previous"), intended):
                    raise DeploymentError(f"host tool has later edits or unknown recovery state: {output['name']}")
        elif kind == "image":
            classify_image_resource(resource, roots)
        elif kind == "directory":
            if resource.get("state") == "pending":
                count, index = resource.get("count"), resource.get("index")
                destination = resolve_named_destination(roots, component["destination"]["root"], component["destination"]["path"])
                path = destination.parent
                for _ in range(count - index - 1):
                    path = path.parent
                if mode == "restore" and (path.exists() or path.is_symlink()):
                    raise DeploymentError("pending directory ownership is ambiguous; restore cannot finalize")
                continue
            if resource.get("state") == "created":
                count, index = resource.get("count"), resource.get("index")
                destination = resolve_named_destination(roots, component["destination"]["root"], component["destination"]["path"])
                path = destination.parent
                for _ in range(count - index - 1):
                    path = path.parent
                if not path.exists() or path.is_symlink() or _identity(path) != tuple(resource.get("identity", ())):
                    raise DeploymentError("deployment-created directory identity changed")
        else:
            raise DeploymentError("unknown recovery resource")


def _closed_authority(value: object) -> dict:
    """Validate schema-1's closed discriminated release-authority object."""
    if not isinstance(value, dict) or not isinstance(value.get("kind"), str):
        raise DeploymentError("release authority is malformed")
    if value["kind"] == "annotated-tag-plus-sha":
        if set(value) != {"kind", "tag", "tag_object", "parent_commit"}:
            raise DeploymentError("annotated release authority has unknown or missing fields")
        if not isinstance(value["tag"], str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]*", value["tag"]) or value["tag"].startswith("-") or ".." in value["tag"]:
            raise DeploymentError("annotated release authority has an invalid tag")
        if not SHA1.fullmatch(value["tag_object"]) or not SHA1.fullmatch(value["parent_commit"]):
            raise DeploymentError("annotated release authority has invalid object IDs")
    elif value["kind"] == "development-sha":
        if set(value) != {"kind", "parent_commit"} or not SHA1.fullmatch(value["parent_commit"]):
            raise DeploymentError("development release authority has unknown or invalid fields")
    else:
        raise DeploymentError("release authority kind is unsupported")
    return value


def _bootstrap_evidence(path: pathlib.Path, parent_bundle: pathlib.Path) -> dict:
    """Safely load bootstrap evidence and bind it to exact private bundle bytes."""
    evidence = load_json(path, "bootstrap verification record")
    if not isinstance(evidence, dict) or set(evidence) != {"schema", "deployer", "authority", "parent_bundle_sha256"} or evidence.get("schema") != SCHEMA or evidence.get("deployer") != DEPLOYER_ID or not SHA256.fullmatch(evidence.get("parent_bundle_sha256", "")):
        raise DeploymentError("invalid bootstrap verification evidence")
    _regular_owned(parent_bundle, 0o600)
    if _digest(parent_bundle) != evidence["parent_bundle_sha256"]:
        raise DeploymentError("parent bundle digest does not match bootstrap evidence")
    return _closed_authority(evidence["authority"])


def _verify_authority_bundle(authority: dict, parent_bundle: pathlib.Path):
    """Prove the retained parent bundle contains exactly the closed authority."""
    temporary = pathlib.Path(tempfile.mkdtemp(prefix="mkchad-authority-bundle-"))
    try:
        repository = temporary / "parent.git"
        run_git(["clone", "--mirror", str(parent_bundle), str(repository)])
        if authority["kind"] == "annotated-tag-plus-sha":
            if run_git(["cat-file", "-t", authority["tag_object"]], repository).stdout.strip() != "tag":
                raise DeploymentError("parent bundle lacks the annotated release tag object")
            if run_git(["rev-parse", authority["tag_object"] + "^{commit}"], repository).stdout.strip() != authority["parent_commit"]:
                raise DeploymentError("parent bundle annotated tag does not match release authority")
        elif run_git(["cat-file", "-e", authority["parent_commit"] + "^{commit}"], repository, check=False).returncode:
            raise DeploymentError("parent bundle lacks the development parent commit")
    finally:
        shutil.rmtree(temporary, ignore_errors=True)


def _verify_deployer_blob(repo: pathlib.Path, parent_commit: str, candidate: pathlib.Path):
    """Require deployment-engine bytes to match the selected parent tree."""
    expected_blob = run_git(["rev-parse", parent_commit + ":deploy/mkchad_deploy.py"], repo).stdout.strip()
    actual_blob = run_git(["hash-object", "--no-filters", str(candidate)], repo).stdout.strip()
    if actual_blob != expected_blob:
        raise DeploymentError("deployment engine bytes do not match release authority")


def _verified_evidence(path: pathlib.Path, parent_bundle: pathlib.Path, repo: pathlib.Path) -> dict:
    """Validate evidence, parent bundle, and candidate HEAD before deployment."""
    authority = _bootstrap_evidence(path, parent_bundle)
    _verify_authority_bundle(authority, parent_bundle)
    head = run_git(["rev-parse", "HEAD"], repo).stdout.strip()
    if head != authority["parent_commit"]:
        raise DeploymentError("candidate checkout is not bound to bootstrap evidence")
    candidate = pathlib.Path(__file__).resolve()
    expected_candidate = (repo / "deploy" / "mkchad_deploy.py").resolve()
    if candidate != expected_candidate:
        raise DeploymentError("deployment engine is outside the verified checkout")
    _regular_owned(candidate)
    _verify_deployer_blob(repo, authority["parent_commit"], candidate)
    return authority


def _roots() -> dict[str, pathlib.Path]:
    home = pathlib.Path(os.environ["HOME"])
    return {"home": home, "xdg_config_home": pathlib.Path(os.environ.get("XDG_CONFIG_HOME", home / ".config")), "xdg_data_home": pathlib.Path(os.environ.get("XDG_DATA_HOME", home / ".local/share")), "xdg_state_home": pathlib.Path(os.environ.get("XDG_STATE_HOME", home / ".local/state"))}


def _installer_trust_environment() -> dict[str, str] | None:
    """Return the fixed installer trust environment for a scoped deployment."""
    return {"MKCHAD_TRUST_GROUP_WRITABLE_ROOTS": "1"} if _TRUSTED_GROUP_WRITABLE_ROOTS.get() else None


def deploy(
    repo: pathlib.Path,
    verified_release: pathlib.Path,
    verified_parent_bundle: pathlib.Path,
    image: pathlib.Path,
    state_root: pathlib.Path | None = None,
    maintenance_quiesced: bool = False,
) -> dict:
    """Deploy a bootstrap-authorized release and return its public receipt.

    Args:
        repo: Private detached parent checkout at the authorized commit.
        verified_release: Private closed authority evidence from the bootstrap.
        verified_parent_bundle: Digest-bound parent Git object bundle.
        image: Caller-supplied local SIF to snapshot and validate.
        state_root: Optional private deployment-state override for tests/tools.
        maintenance_quiesced: Operator assertion for the documented unknown
            pre-interlock maintenance path.
    Returns:
        The succeeded schema-1 deployment receipt.

    Raises:
        DeploymentError: On invalid authority, unavailable inputs, unsafe paths,
            local work, supervision failure, or action failure.

    Side effects:
        Stages Git/image inputs, updates declared final paths under lock, writes
        durable deployment state, and leaves managed services stopped.
    """
    authority = _verified_evidence(verified_release, verified_parent_bundle, repo)
    release, gitlinks = validate_candidate(repo, authority["parent_commit"])
    roots = _roots()
    for root in roots.values():
        _safe_owned_directory(root)
    state_root = state_root or roots["xdg_state_home"] / "mkchad" / "deployments"
    _safe_owned_directory(state_root, create=True)
    for candidate in state_root.glob("*.json"):
        try:
            record = load_json(candidate, "deployment record")
        except DeploymentError:
            raise DeploymentError("malformed deployment state blocks new deployment")
        if record.get("state") in {"failed", "in_progress"}:
            raise DeploymentError("unreconciled deployment requires bootstrap recover")
    artifact = _artifact_for(release)
    with HostInterlock(roots, maintenance_quiesced):
        deployment_id = str(uuid.uuid4())
        journal = DeploymentJournal(state_root, authority, [], deployment_id)
        journal_published = False
        staging = journal.bundle / "staging"
        staging.mkdir(mode=0o700)
        fsync_directory(journal.bundle)
        scope = None
        phase = "stage"
        try:
            # Persist the integrity-bound minimum before candidate-controlled code.
            journal.record["component_authority"] = gitlinks
            journal.retain("release.json", _release_file(repo))
            journal.retain("verified-release.json", verified_release)
            journal.retain("parent.bundle", verified_parent_bundle)
            journal.retain("deployer.py", pathlib.Path(__file__).resolve())
            journal.retain("release-v1.schema.json", repo / "deploy" / "release-v1.schema.json")
            journal.retain("receipt-v1.schema.json", repo / "deploy" / "receipt-v1.schema.json")
            if _bootstrap_evidence(journal.bundle / "verified-release.json", journal.bundle / "parent.bundle") != authority:
                raise DeploymentError("retained bootstrap authority does not match candidate authority")
            journal.persist()
            journal_published = True
            with SessionScope(activity_callback=journal.record_action) as scope:
                staged_image = snapshot_image(image, staging / "image", artifact["sha256"], artifact["architecture"])
                staged = {}
                for component in release["components"]:
                    if component["disposition"] != "disabled":
                        staged[component["name"]] = stage_child(gitlinks[component["name"]]["url"], gitlinks[component["name"]]["commit"], staging, component["name"], scope)
                nested = derive_gitlinks(staged["msk_containers"], gitlinks["msk_containers"]["commit"])
                if set(nested) != {"container_tools"}:
                    raise DeploymentError("msk_containers must pin exactly one container_tools gitlink")
                nested_authority = nested["container_tools"]
                staged_container_tools = stage_child(nested_authority["url"], nested_authority["commit"], staging, "msk_containers-container_tools", scope)
                for component in release["components"]:
                    if component["disposition"] == "deployed":
                        validate_action_files(staged[component["name"]], gitlinks[component["name"]]["commit"], component)
                validate_configuration_pin(staged["mkchad"], gitlinks["mkchad"]["commit"], gitlinks["opencode.nvim"]["commit"])
                image_manifest = validate_image_runtime(scope, staged_image, artifact, staged["msk_containers"], gitlinks["msk_containers"]["commit"], staged["opencode"], gitlinks["opencode"])
                phase = "preflight"
                destination_plans = preflight_all_destinations(roots, release, gitlinks, staged)
                nested_preflight = preflight_nested_checkout(destination_plans["msk_containers"], nested_authority)
                for name, (destination, preflight) in destination_plans.items():
                    if preflight["kind"] == "existing" and preflight["head"] != gitlinks[name]["commit"]:
                        _contained_git(scope, ["-C", str(staged[name]), "fetch", "--no-tags", str(destination), preflight["head"]])
                nested_resource = None
                if nested_preflight["kind"] == "existing" and not nested_preflight["compliant"]:
                    nested_destination = destination_plans["msk_containers"][0] / "container_tools"
                    _contained_git(scope, ["-C", str(staged_container_tools), "fetch", "--no-tags", str(nested_destination), nested_preflight["head"]])
                    nested_resource = {"kind": "nested-checkout", "name": "msk_containers", "previous": nested_preflight["head"], "intended": nested_authority["commit"], "state": "pending"}
                elif nested_preflight["kind"] in {"absent", "parent-absent"}:
                    nested_resource = {"kind": "nested-checkout", "name": "msk_containers", "previous": None, "intended": nested_authority["commit"], "state": "pending"}
                phase = "journal"
                journal.record["nested_authority"] = nested_authority
                if nested_resource is not None:
                    journal.record["resources"].append(nested_resource)
                journal.retain(staged_image.name, staged_image)
                journal.retain(staged_image.name + ".sha256", pathlib.Path(str(staged_image) + ".sha256"))
                for name, staged_repository in staged.items():
                    commits = [gitlinks[name]["commit"]]
                    plan = destination_plans.get(name)
                    if plan and plan[1]["kind"] == "existing":
                        commits.append(plan[1]["head"])
                    retain_repository_bundle(journal, name.replace(".", "-"), staged_repository, commits, scope)
                nested_commits = [nested_authority["commit"]]
                if nested_resource and nested_resource["previous"]:
                    nested_commits.append(nested_resource["previous"])
                retain_repository_bundle(journal, "container-tools", staged_container_tools, nested_commits, scope)
                journal.record["final_mutation_started"] = True
                journal.persist()
                phase = "components"
                results = []
                destinations = {}
                for component in release["components"]:
                    if component["disposition"] != "deployed":
                        continue
                    destination, preflight = destination_plans[component["name"]]
                    destinations[component["name"]] = destination
                    results.append(install_or_update_component(destination, staged[component["name"]], component, gitlinks[component["name"]], journal, scope, preflight))
                    if component["name"] == "msk_containers":
                        results.append(install_or_update_nested_checkout(destination, staged_container_tools, nested_authority, nested_preflight, nested_resource, journal, scope))
                phase = "actions"
                verify_staged_image(staged_image, artifact["sha256"])
                msk_component = next(component for component in release["components"] if component["name"] == "msk_containers")
                expected_image = expected_managed_image_name(image_manifest, gitlinks["msk_containers"]["commit"], artifact["sha256"], artifact["architecture"])
                actions = run_installer_actions(
                    scope,
                    msk_component,
                    destinations["msk_containers"],
                    staged_image,
                    artifact["sha256"],
                    expected_image,
                    gitlinks["msk_containers"]["commit"],
                    journal,
                )
                phase = "verify"
                host_check = next(action for action in msk_component["actions"] if action["id"] == "host-tools-check")
                scope.run(
                    [str(destinations["msk_containers"] / host_check["path"]), *host_check["args"]],
                    extra_env=_installer_trust_environment(),
                )
                installed_image = verify_installed_image(roots["home"], artifact["sha256"])
                validate_image_runtime(scope, installed_image, artifact, staged["msk_containers"], gitlinks["msk_containers"]["commit"], staged["opencode"], gitlinks["opencode"])
                for component in release["components"]:
                    if component["disposition"] != "deployed":
                        continue
                    destination = destinations[component["name"]]
                    preserved = MKCHAD_PRESERVED_PATHS if component["name"] == "mkchad" else set()
                    final_checkout = preflight_existing_checkout(destination, gitlinks[component["name"]]["repository"], gitlinks[component["name"]]["commit"], preserved)
                    if not final_checkout["compliant"]:
                        raise DeploymentError(f"component verification failed: {component['name']}")
                phase = "finalize"
                receipt = {"schema": SCHEMA, "id": journal.id, "deployer": DEPLOYER_ID, "state": "succeeded", "phase": "finalized", "authority": authority, "components": results, "artifact": {"sha256": artifact["sha256"], "architecture": artifact["architecture"]}, "actions": actions, "configuration_handoff": configuration_handoff(release, gitlinks), "recovery": {"available": False}, "process_cleanup": {"machine_observed": True, "operator_asserted": False}, "diagnostic": "deployment completed without reading configuration-owned content"}
                receipt_path = state_root / (journal.id + ".receipt.json")
                atomic_json(receipt_path, receipt, no_replace=True)
                journal.succeeded(receipt)
                return receipt
        except Exception as exc:
            if journal is not None:
                if journal_published or journal.path.exists() or journal.path.is_symlink():
                    machine_cleanup_observed = scope.machine_cleanup_observed if scope is not None else True
                    journal.failed(phase, str(exc), machine_cleanup_observed)
                else:
                    journal.discard_unpublished()
            if isinstance(exc, DeploymentError):
                raise
            raise DeploymentError(str(exc)) from exc
        finally:
            if journal.record.get("state") != "in_progress":
                shutil.rmtree(staging, ignore_errors=True)
                if journal.bundle.exists():
                    fsync_directory(journal.bundle)


def recover(
    state_root: pathlib.Path,
    deployment_id: str,
    mode: str,
    confirm_processes_quiesced: bool = False,
) -> dict:
    """Classify an authenticated journal for explicit offline complete/restore.

    Args:
        state_root: Validated deployment state directory.
        deployment_id: UUID naming exactly one failed/in-progress record.
        mode: ``complete`` accepts only resources already at intended state;
            ``restore`` accepts only resources still at recorded pre-state.
        confirm_processes_quiesced: Required only for ``in_progress`` records
            after the operator has independently established quiescence.
    Returns:
        A durable recovery receipt.

    Raises:
        DeploymentError: For unknown/copy/move/tampered state.  Evidence is
            deliberately retained on every failure.
    """
    if mode not in {"complete", "restore"}:
        raise DeploymentError("recovery mode must be complete or restore")
    record = load_recovery_record(state_root, deployment_id)
    if record["state"] == "in_progress" and not confirm_processes_quiesced:
        raise DeploymentError("in-progress recovery requires --confirm-processes-quiesced after independent operator inspection")
    if record["state"] == "failed" and confirm_processes_quiesced:
        raise DeploymentError("--confirm-processes-quiesced is rejected for finalized failed records")
    if not record.get("final_mutation_started", False) and mode != "restore":
        raise DeploymentError("pre-mutation deployment records permit restore only")
    bundle = state_root / record.get("recovery_bundle", "")
    if bundle.parent != state_root or not bundle.is_dir():
        raise DeploymentError("missing recovery bundle")
    _safe_owned_directory(bundle)
    for name, expected in record.get("bundle_digests", {}).items():
        path = bundle / name
        if path.parent != bundle or not SHA256.fullmatch(expected):
            raise DeploymentError("tampered recovery bundle")
        _regular_owned(path, 0o600)
        if _digest(path) != expected:
            raise DeploymentError("tampered recovery bundle")
    record["state"] = "in_progress"
    record["phase"] = "recovering"
    record["process_cleanup"] = {"machine_observed": False, "operator_asserted": confirm_processes_quiesced}
    record["recovery_attempt"] = {"id": str(uuid.uuid4()), "mode": mode, "started": int(time.time()), "results": []}
    atomic_json(state_root / (deployment_id + ".json"), record)
    scope = None
    try:
        if not record.get("final_mutation_started", False):
            record["recovery_attempt"]["results"].append({"result": "pre-mutation-private-staging-removed"})
            record["state"] = "recovered_restore"
            record["process_cleanup"] = {"machine_observed": True, "operator_asserted": confirm_processes_quiesced}
            record["recovery"] = {"mode": "restore", "time": int(time.time()), "final_paths_changed": False}
            atomic_json(state_root / (deployment_id + ".json"), record)
            shutil.rmtree(bundle)
            fsync_directory(state_root)
            return record
        release = load_json(bundle / "release.json", "retained release metadata")
        authority = _bootstrap_evidence(bundle / "verified-release.json", bundle / "parent.bundle")
        _verify_authority_bundle(authority, bundle / "parent.bundle")
        if _closed_authority(record.get("authority")) != authority:
            raise DeploymentError("recovery record authority does not match retained release evidence")
        recorded_authorities = record.get("component_authority")
        if not isinstance(recorded_authorities, dict):
            raise DeploymentError("recovery record lacks component authority")
        retained_parent = pathlib.Path(tempfile.mkdtemp(prefix="mkchad-recovery-parent-"))
        try:
            repository = retained_parent / "parent.git"
            run_git(["clone", "--mirror", str(bundle / "parent.bundle"), str(repository)])
            authorities = _derive_release_gitlinks(repository, authority["parent_commit"])
        finally:
            shutil.rmtree(retained_parent, ignore_errors=True)
        if authorities != recorded_authorities:
            raise DeploymentError("recorded component authority does not match the retained parent release")
        retained_msk = pathlib.Path(tempfile.mkdtemp(prefix="mkchad-recovery-msk-"))
        try:
            repository = retained_msk / "msk.git"
            run_git(["clone", "--mirror", str(bundle / "msk_containers.bundle"), str(repository)])
            nested_authorities = derive_gitlinks(repository, authorities["msk_containers"]["commit"])
        finally:
            shutil.rmtree(retained_msk, ignore_errors=True)
        if set(nested_authorities) != {"container_tools"} or nested_authorities["container_tools"] != record.get("nested_authority"):
            raise DeploymentError("nested component authority does not match the retained msk_containers release")
        nested_authority = nested_authorities["container_tools"]
        validate_release_document(release, list(authorities))
        roots = _roots()
        for root in roots.values():
            _safe_owned_directory(root)
        components = {component["name"]: component for component in release["components"]}
        fresh_recovery = any(resource.get("kind") == "checkout" and resource.get("name") == "mkchad" and resource.get("previous") is None for resource in record["resources"])
        with HostInterlock(roots, allow_missing_lifecycle=fresh_recovery):
            cleanup_journal_owned_indexes(record, components, roots)
            classify_recovery_resources(record["resources"], components, authorities, roots, mode, nested_authority)
            def record_recovery_action(action):
                if action is None:
                    record.pop("active_action", None)
                else:
                    record["active_action"] = action
                atomic_json(state_root / (deployment_id + ".json"), record)

            with SessionScope(activity_callback=record_recovery_action) as scope:
                priorities = (
                    {"directory": 0, "checkout": 1, "nested-checkout": 2, "host-tools": 3, "image": 4}
                    if mode == "complete"
                    else {"image": 0, "host-tools": 1, "nested-checkout": 2, "checkout": 3, "directory": 4}
                )
                ordered_resources = sorted(enumerate(record["resources"]), key=lambda item: (priorities.get(item[1].get("kind"), 99), item[0] if mode == "complete" else -item[0]))
                ordered_resources = [resource for _, resource in ordered_resources]
                for resource in ordered_resources:
                    name = resource.get("name")
                    if name not in components or name not in authorities:
                        raise DeploymentError("unknown recovery component")
                    if resource.get("kind") == "checkout":
                        result = recover_checkout_resource(resource, mode, components[name], authorities[name], bundle, roots, scope)
                    elif resource.get("kind") == "nested-checkout":
                        result = recover_checkout_resource(resource, mode, nested_component_contract(components["msk_containers"]), nested_authority, bundle, roots, scope, "container-tools")
                    elif resource.get("kind") == "directory":
                        result = recover_directory_resource(resource, mode, components[name], roots)
                    elif resource.get("kind") == "host-tools":
                        result = recover_host_tools(resource, mode, components[name], bundle, roots, scope)
                    elif resource.get("kind") == "image":
                        result = recover_image(resource, mode, components[name], bundle, roots, scope)
                    else:
                        raise DeploymentError("unknown recovery resource")
                    record["recovery_attempt"]["results"].append(result)
                    atomic_json(state_root / (deployment_id + ".json"), record)
                if mode == "complete":
                    synthetic = {"kind": "nested-checkout", "name": "msk_containers", "previous": None, "intended": nested_authority["commit"]}
                    result = recover_checkout_resource(synthetic, mode, nested_component_contract(components["msk_containers"]), nested_authority, bundle, roots, scope, "container-tools")
                    record["recovery_attempt"]["results"].append(result)
                    atomic_json(state_root / (deployment_id + ".json"), record)
        record["state"] = "recovered_" + mode
        record["process_cleanup"] = {"machine_observed": True, "operator_asserted": confirm_processes_quiesced}
        record["recovery"] = {"mode": mode, "time": int(time.time())}
        atomic_json(state_root / (deployment_id + ".json"), record)
        shutil.rmtree(bundle)
        fsync_directory(state_root)
        return record
    except Exception as exc:
        machine_cleanup_observed = scope.machine_cleanup_observed if scope is not None else True
        record["state"] = "failed" if machine_cleanup_observed else "in_progress"
        record["process_cleanup"] = {"machine_observed": machine_cleanup_observed, "operator_asserted": confirm_processes_quiesced}
        record["diagnostic"] = redact(str(exc))
        atomic_json(state_root / (deployment_id + ".json"), record)
        raise


def spawn_supervisor(argv: list[str]) -> int:
    """Run the lock/journal owner in a separate session and wait for its result.

    The child inherits only standard streams and the explicitly minimized
    deployment environment. If this user-facing controller dies, the supervisor
    remains alive and continues owning the interlocks and journal.
    """
    process = subprocess.Popen(argv, close_fds=True, start_new_session=True)
    return process.wait()


def cleanup_bootstrap_root(root: pathlib.Path | None, repo: pathlib.Path):
    """Remove only the authenticated bootstrap checkout handed to a supervisor."""
    if root is None:
        return
    root = root.absolute()
    repo = repo.absolute()
    entry = os.lstat(root)
    if repo.parent != root or repo.name != "parent" or not root.name.startswith("mkchad-bootstrap-") or not stat.S_ISDIR(entry.st_mode) or stat.S_ISLNK(entry.st_mode) or entry.st_uid != os.getuid() or stat.S_IMODE(entry.st_mode) != 0o700:
        raise DeploymentError("bootstrap cleanup root is not the authenticated checkout parent")
    shutil.rmtree(root)
    fsync_directory(root.parent)


def validate_release_identity(repo: pathlib.Path, release_tag: str, expected_parent_commit: str):
    """Validate planned tag syntax and candidate HEAD without resolving the tag.

    Args:
        repo: Candidate parent checkout.
        release_tag: Planned annotated release tag name, which may not yet exist.
        expected_parent_commit: Full SHA that must equal the candidate HEAD.

    Raises:
        DeploymentError: If syntax is invalid or candidate HEAD differs. This
            intentionally does not look up ``release_tag`` in any repository.
    """
    if not isinstance(release_tag, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]*", release_tag) or release_tag.startswith("-") or ".." in release_tag:
        raise DeploymentError("invalid release tag")
    if not SHA1.fullmatch(expected_parent_commit):
        raise DeploymentError("expected parent commit must be a full SHA")
    head = run_git(["rev-parse", "HEAD"], repo).stdout.strip()
    if head != expected_parent_commit:
        raise DeploymentError("candidate checkout does not match expected parent commit")


def prepare_validation_checkout(repo: pathlib.Path, expected_parent_commit: str, root: pathlib.Path) -> pathlib.Path:
    """Create a detached private checkout of the exact candidate commit.

    Args:
        repo: Source parent repository; worktree changes are ignored.
        expected_parent_commit: Full commit SHA to materialize.
        root: Existing private temporary root.

    Returns:
        Path to the detached exact candidate checkout.

    Raises:
        DeploymentError: If cloning or checkout fails. The caller owns cleanup.
    """
    candidate = root / "parent"
    run_git(["clone", "--no-checkout", "--no-local", str(repo), str(candidate)])
    run_git(["-C", str(candidate), "checkout", "--detach", "--no-recurse-submodules", expected_parent_commit])
    return candidate


def validate_release_inputs(repo: pathlib.Path, release_tag: str, expected_parent_commit: str, image: pathlib.Path) -> dict:
    """Validate an exact committed release candidate and local image.

    Args:
        repo: Parent checkout whose HEAD must equal ``expected_parent_commit``.
        release_tag: Planned annotated tag name; it need not exist yet.
        expected_parent_commit: Full candidate commit SHA.
        image: Local SIF path validated against committed release metadata.

    Returns:
        A machine-readable validation summary for the exact candidate commit.

    Raises:
        DeploymentError: If identity, metadata, child reachability, image, or
            configuration consistency cannot be proven.

    Side effects:
        Creates and removes private temporary checkouts/staging and performs
        bounded network/runtime validation without changing deployment targets.
    """
    validate_release_identity(repo, release_tag, expected_parent_commit)
    root = pathlib.Path(tempfile.mkdtemp(prefix="mkchad-validate-"))
    os.chmod(root, 0o700)
    try:
        candidate = prepare_validation_checkout(repo, expected_parent_commit, root)
        release, gitlinks = validate_candidate(candidate, expected_parent_commit)
        guidance = candidate / release["configuration"]["guidance"]
        if not guidance.is_file():
            raise DeploymentError("configuration guidance is missing from the candidate")
        artifact = _artifact_for(release)
        staging = root / "staging"
        staging.mkdir(mode=0o700)
        with SessionScope() as scope:
            staged_image = snapshot_image(image, staging / "image", artifact["sha256"], artifact["architecture"])
            staged = {}
            for component in release["components"]:
                if component["disposition"] != "disabled":
                    staged[component["name"]] = stage_child(gitlinks[component["name"]]["url"], gitlinks[component["name"]]["commit"], staging, component["name"], scope)
            nested = derive_gitlinks(staged["msk_containers"], gitlinks["msk_containers"]["commit"])
            if set(nested) != {"container_tools"}:
                raise DeploymentError("msk_containers must pin exactly one container_tools gitlink")
            stage_child(nested["container_tools"]["url"], nested["container_tools"]["commit"], staging, "msk_containers-container_tools", scope)
            for component in release["components"]:
                if component["disposition"] == "deployed":
                    validate_action_files(staged[component["name"]], gitlinks[component["name"]]["commit"], component)
            validate_configuration_pin(staged["mkchad"], gitlinks["mkchad"]["commit"], gitlinks["opencode.nvim"]["commit"])
            manifest = validate_image_runtime(scope, staged_image, artifact, staged["msk_containers"], gitlinks["msk_containers"]["commit"], staged["opencode"], gitlinks["opencode"])
            return {"state": "validated", "parent_commit": expected_parent_commit, "components": sorted(gitlinks), "artifact": {"sha256": artifact["sha256"], "architecture": artifact["architecture"]}, "component_manifest": manifest, "configuration_handoff": configuration_handoff(release, gitlinks)}
    finally:
        shutil.rmtree(root, ignore_errors=True)


def cleanup_journal_owned_indexes(record: dict, components: dict, roots: dict[str, pathlib.Path]):
    """Remove only alternate indexes named and identity-bound by this journal."""
    for resource in record.get("resources", []):
        if resource.get("kind") not in {"checkout", "nested-checkout"}:
            continue
        component = components.get(resource.get("name"))
        if not component:
            continue
        if resource["kind"] == "nested-checkout":
            component = nested_component_contract(component)
        destination = resolve_named_destination(roots, component["destination"]["root"], component["destination"]["path"])
        git_dir = destination / ".git"
        if not git_dir.is_dir() or git_dir.is_symlink():
            continue
        recorded_identity = resource.get("git_dir_identity")
        if recorded_identity and _identity(git_dir) != tuple(recorded_identity):
            raise DeploymentError("checkout Git directory changed before index recovery")
        names = []
        if resource.get("alternate_index"):
            names.append(resource["alternate_index"])
        if record.get("recovery_attempt"):
            names.append(".mkchad-recovery.index")
        for name in names:
            if pathlib.Path(name).name != name or not name.startswith(".mkchad-") or not name.endswith(".index"):
                raise DeploymentError("journal contains an unsafe alternate-index name")
            for path in (git_dir / name, git_dir / (name + ".lock")):
                if not path.exists() and not path.is_symlink():
                    continue
                entry = os.lstat(path)
                if not stat.S_ISREG(entry.st_mode) or entry.st_uid != os.getuid():
                    raise DeploymentError("journal-owned alternate index has an unsafe identity")
                path.unlink()
                fsync_directory(git_dir)


def recover_checkout_resource(resource: dict, mode: str, component: dict, authority: dict, bundle: pathlib.Path, roots: dict[str, pathlib.Path], scope: SessionScope, bundle_name: str | None = None) -> dict:
    """Complete or restore one checkout using only its authenticated bundle."""
    name = resource.get("name")
    if name != component.get("name") or resource.get("intended") != authority.get("commit"):
        raise DeploymentError("recovery resource does not match retained release authority")
    destination = resolve_named_destination(roots, component["destination"]["root"], component["destination"]["path"])
    target = resource.get("intended") if mode == "complete" else resource.get("previous")
    other = resource.get("previous") if mode == "complete" else resource.get("intended")
    git_bundle = bundle / ((bundle_name or name.replace(".", "-")) + ".bundle")
    _regular_owned(git_bundle, 0o600)
    preserved = MKCHAD_PRESERVED_PATHS if name == "mkchad" else set()
    if bundle_name == "container-tools" and resource.get("previous") is None and resource.get("state") == "pending" and destination.is_dir() and not destination.is_symlink() and not any(destination.iterdir()):
        if mode == "restore":
            return {"name": name, "result": "parent-owned-placeholder-preserved", "revision": None}
        destination.rmdir()
        fsync_directory(destination.parent)
    if destination.exists() or destination.is_symlink():
        try:
            current = preflight_existing_checkout(destination, authority["repository"], target or other, preserved)
        except DeploymentError:
            current = classify_checkout_mixture(destination, authority["repository"], resource.get("previous"), resource["intended"], preserved)
        if current["head"] not in {value for value in (target, other) if value}:
            raise DeploymentError("checkout has later edits or unknown recovery state")
        if target is None:
            if resource.get("state") == "pending":
                raise DeploymentError("pending checkout ownership is ambiguous; restore cannot finalize")
            if resource.get("state") != "installed" or _identity(destination) != tuple(resource.get("identity", ())):
                raise DeploymentError("new checkout ownership cannot be proven for restore")
            if current["head"] != other:
                raise DeploymentError("new checkout is not in its deployment-owned state")
            quarantine = bundle / ("removed-" + name.replace(".", "-"))
            if quarantine.exists() or quarantine.is_symlink():
                raise DeploymentError("recovery checkout quarantine already exists")
            os.rename(destination, quarantine)
            os.chmod(quarantine, 0o700)
            fsync_directory(destination.parent)
            return {"name": name, "result": "removed", "revision": None}
        if current["kind"] == "mixed" or current["head"] != target:
            _contained_git(scope, ["-C", str(destination), "fetch", "--no-tags", str(git_bundle), target])
            if current["kind"] == "mixed":
                materialize_checkout_tree(destination, resource["previous"], resource["intended"], target, scope)
            else:
                materialize_checkout_tree(destination, resource["previous"], resource["intended"], target, scope)
        final = preflight_existing_checkout(destination, authority["repository"], target, preserved)
        if not final["compliant"]:
            raise DeploymentError("checkout recovery did not reach the requested revision")
        return {"name": name, "result": "checked-out", "revision": target}
    if target is None:
        return {"name": name, "result": "absent", "revision": None}
    _safe_owned_directory(destination.parent)
    temporary = pathlib.Path(tempfile.mkdtemp(prefix=".mkchad-recover-", dir=destination.parent))
    try:
        _prepare_exact_checkout(git_bundle, temporary, target, authority["url"], scope)
        _no_replace_rename(temporary, destination)
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)
    return {"name": name, "result": "installed", "revision": target}


def recover_directory_resource(resource: dict, mode: str, component: dict, roots: dict[str, pathlib.Path]) -> dict:
    """Recreate or remove only the destination parents journaled by deployment."""
    count = resource.get("count")
    index = resource.get("index")
    if resource.get("name") != component.get("name") or not isinstance(count, int) or count < 1 or not isinstance(index, int) or not 0 <= index < count or resource.get("state") not in {"pending", "created"}:
        raise DeploymentError("invalid recovery directory resource")
    destination = resolve_named_destination(roots, component["destination"]["root"], component["destination"]["path"])
    created = []
    current = destination.parent
    for _ in range(count):
        created.append(current)
        current = current.parent
    path = list(reversed(created))[index]
    if mode == "complete":
        if not path.exists() and not path.is_symlink():
            _safe_owned_directory(path.parent)
            path.mkdir(mode=0o700)
            fsync_directory(path.parent)
        _safe_owned_directory(path)
        if resource["state"] == "created" and _identity(path) != tuple(resource.get("identity", ())):
            raise DeploymentError("deployment-created directory identity changed")
        return {"name": component["name"], "result": "parent-present", "index": index}
    if resource["state"] == "pending":
        if path.exists() or path.is_symlink():
            raise DeploymentError("pending directory ownership is ambiguous; restore cannot finalize")
        return {"name": component["name"], "result": "pending-parent-absent", "index": index}
    if not path.exists() and not path.is_symlink():
        raise DeploymentError("deployment-created directory disappeared before restore")
    _safe_owned_directory(path)
    if _identity(path) != tuple(resource.get("identity", ())):
        raise DeploymentError("deployment-created directory identity changed")
    try:
        path.rmdir()
    except OSError as exc:
        raise DeploymentError("deployment-created directory is not empty during restore") from exc
    fsync_directory(path.parent)
    return {"name": component["name"], "result": "parent-removed", "index": index}


def nested_component_contract(msk_component: dict) -> dict:
    """Derive the fixed nested checkout destination from the deployed owner."""
    component = dict(msk_component)
    component["destination"] = dict(msk_component["destination"])
    component["destination"]["path"] = component["destination"]["path"] + "/container_tools"
    return component


def main(argv: list[str] | None = None) -> int:
    """Run the public validation, deployment, or recovery command-line API.

    Args:
        argv: Optional command-line arguments; process arguments are used when
            omitted.

    Returns:
        Zero on success, child supervisor status for detached operations, or
        ``2`` for a deployment validation failure.

    Side effects:
        Validation uses private temporary staging; deployment and recovery
        start detached supervisors that own their documented state mutations.
    """
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    validate = commands.add_parser("validate-release", allow_abbrev=False)
    validate.add_argument("--repo", type=pathlib.Path, default=pathlib.Path.cwd())
    validate.add_argument("--release", required=True)
    validate.add_argument("--expected-parent-commit", required=True)
    validate.add_argument("--image", type=pathlib.Path, required=True)
    deploy_parser = commands.add_parser("deploy", allow_abbrev=False)
    deploy_parser.add_argument("--repo", type=pathlib.Path, default=pathlib.Path.cwd())
    deploy_parser.add_argument("--verified-release", type=pathlib.Path, required=True)
    deploy_parser.add_argument("--verified-parent-bundle", type=pathlib.Path, required=True)
    deploy_parser.add_argument("--image", type=pathlib.Path, required=True)
    deploy_parser.add_argument("--state-root", type=pathlib.Path)
    deploy_parser.add_argument("--maintenance-quiesced", action="store_true")
    deploy_parser.add_argument("--trust-group-writable-roots", action="store_true", help=argparse.SUPPRESS)
    deploy_parser.add_argument("--bootstrap-root", type=pathlib.Path, help=argparse.SUPPRESS)
    supervisor = commands.add_parser("supervise", help=argparse.SUPPRESS, allow_abbrev=False)
    supervisor.add_argument("--repo", type=pathlib.Path, required=True)
    supervisor.add_argument("--verified-release", type=pathlib.Path, required=True)
    supervisor.add_argument("--verified-parent-bundle", type=pathlib.Path, required=True)
    supervisor.add_argument("--image", type=pathlib.Path, required=True)
    supervisor.add_argument("--state-root", type=pathlib.Path)
    supervisor.add_argument("--maintenance-quiesced", action="store_true")
    supervisor.add_argument("--trust-group-writable-roots", action="store_true", help=argparse.SUPPRESS)
    supervisor.add_argument("--bootstrap-root", type=pathlib.Path, help=argparse.SUPPRESS)
    recovery = commands.add_parser("recover", allow_abbrev=False)
    recovery.add_argument("--deployment-id", required=True)
    recovery.add_argument("--mode", required=True, choices=("complete", "restore"))
    recovery.add_argument("--state-root", type=pathlib.Path, required=True)
    recovery.add_argument("--confirm-processes-quiesced", action="store_true")
    recovery.add_argument("--trust-group-writable-roots", action="store_true", help=argparse.SUPPRESS)
    recovery_supervisor = commands.add_parser("recover-supervise", help=argparse.SUPPRESS, allow_abbrev=False)
    recovery_supervisor.add_argument("--deployment-id", required=True)
    recovery_supervisor.add_argument("--mode", required=True, choices=("complete", "restore"))
    recovery_supervisor.add_argument("--state-root", type=pathlib.Path, required=True)
    recovery_supervisor.add_argument("--confirm-processes-quiesced", action="store_true")
    recovery_supervisor.add_argument("--trust-group-writable-roots", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    try:
        if args.command == "validate-release":
            print(json.dumps(validate_release_inputs(args.repo, args.release, args.expected_parent_commit, args.image), sort_keys=True))
        elif args.command == "deploy":
            command = [sys.executable, str(pathlib.Path(__file__).resolve()), "supervise", "--repo", str(args.repo), "--verified-release", str(args.verified_release), "--verified-parent-bundle", str(args.verified_parent_bundle), "--image", str(args.image)]
            if args.state_root:
                command.extend(["--state-root", str(args.state_root)])
            if args.maintenance_quiesced:
                command.append("--maintenance-quiesced")
            if args.trust_group_writable_roots:
                command.append("--trust-group-writable-roots")
            if args.bootstrap_root:
                command.extend(["--bootstrap-root", str(args.bootstrap_root)])
            return spawn_supervisor(command)
        elif args.command == "supervise":
            try:
                roots = _roots()
                trusted_roots = roots.values() if args.trust_group_writable_roots else ()
                with _trusted_group_writable_roots(trusted_roots):
                    print(
                        json.dumps(
                            deploy(
                                args.repo,
                                args.verified_release,
                                args.verified_parent_bundle,
                                args.image,
                                args.state_root,
                                args.maintenance_quiesced,
                            ),
                            sort_keys=True,
                        )
                    )
            finally:
                cleanup_bootstrap_root(args.bootstrap_root, args.repo)
        elif args.command == "recover":
            command = [sys.executable, str(pathlib.Path(__file__).resolve()), "recover-supervise", "--deployment-id", args.deployment_id, "--mode", args.mode, "--state-root", str(args.state_root)]
            if args.confirm_processes_quiesced:
                command.append("--confirm-processes-quiesced")
            if args.trust_group_writable_roots:
                command.append("--trust-group-writable-roots")
            return spawn_supervisor(command)
        else:
            roots = _roots()
            trusted_roots = roots.values() if args.trust_group_writable_roots else ()
            with _trusted_group_writable_roots(trusted_roots):
                print(
                    json.dumps(
                        recover(
                            args.state_root,
                            args.deployment_id,
                            args.mode,
                            args.confirm_processes_quiesced,
                        ),
                        sort_keys=True,
                    )
                )
        return 0
    except DeploymentError as exc:
        print("mkchad-deploy: " + redact(str(exc)), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
