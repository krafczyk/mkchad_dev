#!/usr/bin/env python3
"""Independently provisioned keyless bootstrap for MkChad parent releases.

The bootstrap validates its installed path safety, observes an exact annotated
tag twice against an operator-supplied parent SHA, and hands closed authority
evidence plus a private parent bundle to candidate deployment code. ``recover``
is state-bound and uses only retained evidence; it never contacts a remote.
"""

from __future__ import annotations

import argparse
import contextlib
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


SCHEMA = 1
DEPLOYER_ID = "mkchad-deploy-v1"
DEFAULT_REMOTE = "git@github.com:krafczyk/mkchad_dev.git"
SHA1 = re.compile(r"^[0-9a-f]{40}$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
TAG = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]*$")
DEPLOYMENT_ID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")
FORBIDDEN_AUTHORITY_ALIASES = {"trust", "signer", "signature", "fingerprint", "verified_release"}
GIT_OUTPUT_LIMIT = 1024 * 1024


class BootstrapError(RuntimeError):
    """Raised when bootstrap authority, installed paths, or recovery state is unsafe."""


def _safe_directory(path: pathlib.Path):
    """Require an absolute current-user-owned private directory without links."""
    if not path.is_absolute():
        raise BootstrapError("directory must be absolute")
    current = pathlib.Path("/")
    for part in path.parts[1:]:
        current /= part
        entry = os.lstat(current)
        if stat.S_ISLNK(entry.st_mode) or not stat.S_ISDIR(entry.st_mode):
            raise BootstrapError(f"path contains a symlink or non-directory: {current}")
        if _unsafe_writable_ancestor(entry):
            raise BootstrapError(f"path is group or world writable: {current}")
    entry = os.lstat(path)
    if entry.st_uid != os.getuid() or stat.S_IMODE(entry.st_mode) != 0o700:
        raise BootstrapError("directory must be current-user-owned mode 0700")


def _unsafe_writable_ancestor(entry: os.stat_result) -> bool:
    """Return whether another local user can replace a path through an ancestor."""
    if entry.st_uid not in {0, os.getuid()}:
        return True
    writable = bool(entry.st_mode & 0o022)
    protected_shared_root = entry.st_uid == 0 and bool(entry.st_mode & stat.S_ISVTX)
    return writable and not protected_shared_root


def _fsync_directory(path: pathlib.Path):
    """Flush one already-validated directory entry set."""
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def validate_installed_bootstrap(script: pathlib.Path):
    """Validate the separately provisioned bootstrap path without a digest sidecar.

    Args:
        script: Installed bootstrap path that must be absolute, current-user-owned,
            regular, and non-writable by group or other users.

    Raises:
        BootstrapError: If an ancestor is linked, a user-owned ancestor is writable,
            or the script is not a safe current-user-owned regular file.
    """
    if not script.is_absolute():
        raise BootstrapError("installed bootstrap path must be absolute")
    current = pathlib.Path("/")
    for part in script.parts[1:-1]:
        current /= part
        entry = os.lstat(current)
        if stat.S_ISLNK(entry.st_mode) or not stat.S_ISDIR(entry.st_mode):
            raise BootstrapError(f"bootstrap path contains a symlink or non-directory: {current}")
        if _unsafe_writable_ancestor(entry):
            raise BootstrapError(f"bootstrap ancestor is group or world writable: {current}")
    entry = os.lstat(script)
    if not stat.S_ISREG(entry.st_mode) or stat.S_ISLNK(entry.st_mode) or entry.st_uid != os.getuid() or entry.st_mode & 0o022:
        raise BootstrapError("installed bootstrap must be a current-user-owned safe regular file")


def _read_private(path: pathlib.Path) -> bytes:
    """Read one owner-only regular file through a no-follow descriptor."""
    entry = os.lstat(path)
    if not stat.S_ISREG(entry.st_mode) or stat.S_ISLNK(entry.st_mode) or entry.st_uid != os.getuid() or stat.S_IMODE(entry.st_mode) != 0o600:
        raise BootstrapError(f"unsafe private input: {path}")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        before = os.fstat(fd)
        if (entry.st_dev, entry.st_ino) != (before.st_dev, before.st_ino):
            raise BootstrapError("private input changed before open")
        data = os.read(fd, 16 * 1024 * 1024)
        if os.read(fd, 1):
            raise BootstrapError("private input is too large")
        after = os.fstat(fd)
        if (before.st_dev, before.st_ino, before.st_size) != (after.st_dev, after.st_ino, after.st_size):
            raise BootstrapError("private input changed while reading")
        final = os.lstat(path)
        if (before.st_dev, before.st_ino) != (final.st_dev, final.st_ino):
            raise BootstrapError("private input changed after open")
        return data
    finally:
        os.close(fd)


def _digest_private(path: pathlib.Path) -> str:
    """Hash one owner-only regular file without loading it into memory."""
    entry = os.lstat(path)
    if (
        not stat.S_ISREG(entry.st_mode)
        or stat.S_ISLNK(entry.st_mode)
        or entry.st_uid != os.getuid()
        or stat.S_IMODE(entry.st_mode) != 0o600
    ):
        raise BootstrapError(f"unsafe private input: {path}")
    identity = (
        entry.st_dev,
        entry.st_ino,
        entry.st_size,
        entry.st_mode,
        entry.st_uid,
        entry.st_mtime_ns,
        entry.st_ctime_ns,
    )
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        before = os.fstat(fd)
        if identity != (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mode,
            before.st_uid,
            before.st_mtime_ns,
            before.st_ctime_ns,
        ):
            raise BootstrapError("private input changed before open")
        digest = hashlib.sha256()
        remaining = before.st_size
        while remaining:
            chunk = os.read(fd, min(1024 * 1024, remaining))
            if not chunk:
                raise BootstrapError("private input changed while reading")
            digest.update(chunk)
            remaining -= len(chunk)
        if os.read(fd, 1):
            raise BootstrapError("private input changed while reading")
        after = os.fstat(fd)
        if identity != (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mode,
            after.st_uid,
            after.st_mtime_ns,
            after.st_ctime_ns,
        ):
            raise BootstrapError("private input changed while reading")
        final = os.lstat(path)
        if identity != (
            final.st_dev,
            final.st_ino,
            final.st_size,
            final.st_mode,
            final.st_uid,
            final.st_mtime_ns,
            final.st_ctime_ns,
        ):
            raise BootstrapError("private input changed after open")
        return digest.hexdigest()
    finally:
        os.close(fd)


def _write_all(fd: int, data: bytes):
    """Write all bytes or fail rather than accepting a short write."""
    view = memoryview(data)
    while view:
        written = os.write(fd, view)
        if written <= 0:
            raise BootstrapError("private output write made no progress")
        view = view[written:]


def _process_identity(pid: int):
    """Return the current-user process identity needed for safe group signaling."""
    try:
        entry = os.stat(f"/proc/{pid}")
        fields = pathlib.Path(f"/proc/{pid}/stat").read_text(encoding="ascii").rsplit(") ", 1)[1].split()
        return (entry.st_uid, int(fields[19]), int(fields[3]), int(fields[2]), fields[0] == "Z")
    except (FileNotFoundError, IndexError, ValueError, OSError):
        return None


def _live_group_members(session: int, group: int) -> list[int]:
    """List live current-user members of one supervised session and group."""
    try:
        candidates = list(pathlib.Path("/proc").iterdir())
    except OSError as exc:
        raise BootstrapError("cannot inspect git process group") from exc
    members = []
    for candidate in candidates:
        if candidate.name.isdecimal():
            identity = _process_identity(int(candidate.name))
            if identity is not None and identity[0] == os.getuid() and identity[2] == session and identity[3] == group and not identity[4]:
                members.append(int(candidate.name))
    return members


def _terminal_status(pid: int):
    """Observe a direct child terminal status without releasing its PID anchor."""
    try:
        return os.waitid(os.P_PID, pid, os.WEXITED | os.WNOHANG | os.WNOWAIT)
    except ChildProcessError as exc:
        raise BootstrapError("git process leader was reaped unexpectedly") from exc


def _terminate_process_group(process: subprocess.Popen, identity, term_grace: float, kill_grace: float):
    """Terminate a verified process group and reap its still-anchored leader."""
    current = _process_identity(process.pid)
    if current is None or current[:4] != identity[:4]:
        raise BootstrapError("git process identity changed; refusing to signal")
    session, group = identity[2], identity[3]
    for signal_value, grace in ((signal.SIGTERM, term_grace), (signal.SIGKILL, kill_grace)):
        with contextlib.suppress(ProcessLookupError):
            os.killpg(group, signal_value)
        deadline = time.monotonic() + grace
        while time.monotonic() < deadline:
            if not _live_group_members(session, group):
                process.wait()
                return
            time.sleep(0.02)
    if _live_group_members(session, group):
        raise BootstrapError("git process-group cleanup could not be confirmed")
    process.wait()


def _git(args: list[str], cwd: pathlib.Path | None = None, check: bool = True, timeout: float = 300, term_grace: float = 5, kill_grace: float = 5):
    """Run constrained Git with bounded output, timeout, and group cleanup."""
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    env.update({"GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull, "GIT_TERMINAL_PROMPT": "0"})
    command = ["git", "-c", "core.hooksPath=/dev/null", "-c", "init.templateDir=", *args]
    process = subprocess.Popen(command, cwd=cwd, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
    assert process.stdout is not None and process.stderr is not None
    identity = _process_identity(process.pid)
    if identity is None or identity[0] != os.getuid() or identity[2] != process.pid or identity[3] != process.pid:
        process.kill()
        process.wait(timeout=kill_grace)
        raise BootstrapError("cannot establish git process-group identity")
    streams = {process.stdout: bytearray(), process.stderr: bytearray()}
    selector = selectors.DefaultSelector()
    deadline = time.monotonic() + timeout
    failure = None
    terminal = None
    try:
        try:
            for stream in streams:
                os.set_blocking(stream.fileno(), False)
                selector.register(stream, selectors.EVENT_READ)
            while selector.get_map():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    failure = "timed out"
                    break
                for key, _ in selector.select(min(0.05, remaining)):
                    chunk = os.read(key.fileobj.fileno(), 65536)
                    if not chunk:
                        selector.unregister(key.fileobj)
                        continue
                    target = streams[key.fileobj]
                    if len(target) + len(chunk) > GIT_OUTPUT_LIMIT:
                        failure = "exceeded diagnostic output limit"
                        break
                    target.extend(chunk)
                if failure:
                    break
                terminal = _terminal_status(process.pid)
                if terminal is not None and _live_group_members(identity[2], identity[3]):
                    failure = "left a live git process-group member"
                    break
            if failure is None:
                while terminal is None and time.monotonic() < deadline:
                    terminal = _terminal_status(process.pid)
                    if terminal is None:
                        time.sleep(0.02)
                if terminal is None:
                    failure = "timed out"
                elif _live_group_members(identity[2], identity[3]):
                    failure = "left a live git process-group member"
        except BaseException:
            _terminate_process_group(process, identity, term_grace, kill_grace)
            raise
        if failure is not None:
            _terminate_process_group(process, identity, term_grace, kill_grace)
        else:
            process.wait()
    finally:
        selector.close()
        for stream in streams:
            stream.close()
    stdout = bytes(streams[process.stdout]).decode("utf-8", errors="replace")
    stderr = bytes(streams[process.stderr]).decode("utf-8", errors="replace")
    if failure is not None:
        raise BootstrapError(f"git command {failure}; stderr-sha256={hashlib.sha256(stderr.encode()).hexdigest()}")
    result = subprocess.CompletedProcess(command, process.returncode, stdout, stderr)
    if check and result.returncode:
        raise BootstrapError(f"git command failed with exit {result.returncode}; stderr-sha256={hashlib.sha256(result.stderr.encode()).hexdigest()}")
    return result


def _validate_tag(tag: str):
    if not isinstance(tag, str) or not TAG.fullmatch(tag) or tag.startswith("-") or ".." in tag:
        raise BootstrapError("invalid release tag")


def _validate_authority(authority: object) -> dict:
    """Validate the closed discriminated release-authority object."""
    if not isinstance(authority, dict) or not isinstance(authority.get("kind"), str):
        raise BootstrapError("release authority is malformed")
    kind = authority["kind"]
    if kind == "annotated-tag-plus-sha":
        if set(authority) != {"kind", "tag", "tag_object", "parent_commit"}:
            raise BootstrapError("annotated release authority has unknown or missing fields")
        _validate_tag(authority["tag"])
        if not SHA1.fullmatch(authority["tag_object"]) or not SHA1.fullmatch(authority["parent_commit"]):
            raise BootstrapError("annotated release authority has invalid object IDs")
    elif kind == "development-sha":
        if set(authority) != {"kind", "parent_commit"} or not SHA1.fullmatch(authority["parent_commit"]):
            raise BootstrapError("development release authority has unknown or invalid fields")
    else:
        raise BootstrapError("release authority kind is unsupported")
    return authority


def verify_annotated_tag(remote: str, tag: str, expected_parent_commit: str, work: pathlib.Path) -> dict:
    """Observe one exact annotated tag twice and bind it to a full SHA.

    Args:
        remote: Canonical parent remote passed explicitly to constrained Git.
        tag: Exact tag name to fetch, never a branch or wildcard.
        expected_parent_commit: Operator-supplied 40-character parent commit SHA.
        work: New private Git work directory used only for this bootstrap run.

    Returns:
        Closed ``annotated-tag-plus-sha`` authority.

    Raises:
        BootstrapError: If the tag is malformed, lightweight, moves between the
            two observations, or does not peel to ``expected_parent_commit``.
    """
    _validate_tag(tag)
    if not SHA1.fullmatch(expected_parent_commit):
        raise BootstrapError("expected parent commit must be a full SHA")
    _git(["init", str(work)])
    first_ref = "refs/mkchad-bootstrap/first"
    second_ref = "refs/mkchad-bootstrap/second"
    source = f"refs/tags/{tag}"
    _git(["fetch", "--no-tags", remote, f"{source}:{first_ref}"], work)
    if _git(["cat-file", "-t", first_ref], work).stdout.strip() != "tag":
        raise BootstrapError("release must be an annotated tag")
    tag_object = _git(["rev-parse", f"{first_ref}^{{tag}}"], work).stdout.strip()
    peeled = _git(["rev-parse", f"{tag_object}^{{commit}}"], work).stdout.strip()
    if not SHA1.fullmatch(tag_object) or not SHA1.fullmatch(peeled):
        raise BootstrapError("release tag did not peel to a parent commit")
    if peeled != expected_parent_commit:
        raise BootstrapError("release tag does not match expected parent commit")
    _git(["fetch", "--no-tags", remote, f"{source}:{second_ref}"], work)
    second_object = _git(["rev-parse", f"{second_ref}^{{tag}}"], work).stdout.strip()
    second_peeled = _git(["rev-parse", f"{second_object}^{{commit}}"], work).stdout.strip()
    if second_object != tag_object or second_peeled != peeled:
        raise BootstrapError("release tag moved during acquisition")
    return _validate_authority({"kind": "annotated-tag-plus-sha", "tag": tag, "tag_object": tag_object, "parent_commit": peeled})


def development_evidence(commit: str) -> dict:
    """Return closed development authority for a full parent commit SHA.

    Args:
        commit: Lowercase 40-character commit SHA selected by the operator.

    Returns:
        A closed ``development-sha`` authority object.

    Raises:
        BootstrapError: If ``commit`` is not a full SHA. No I/O occurs.
    """
    return _validate_authority({"kind": "development-sha", "parent_commit": commit})


def prepare_verified_checkout(remote: str, authority: dict, work: pathlib.Path) -> pathlib.Path:
    """Check out the authority-bound parent object and return its deployer.

    Args:
        remote: Explicit canonical parent Git remote.
        authority: Closed release or development authority object.
        work: Private bootstrap checkout directory.

    Returns:
        Path to the regular deployment engine in the detached exact checkout.

    Raises:
        BootstrapError: If the authority is invalid, unavailable, mismatched, or
            lacks a safe deployment engine. The function creates ``work``.
    """
    authority = _validate_authority(authority)
    if not (work / ".git").is_dir():
        _git(["init", str(work)])
    if authority["kind"] == "development-sha":
        if _git(["fetch", "--no-tags", remote, authority["parent_commit"]], work, check=False).returncode:
            raise BootstrapError("authorized parent commit is unavailable from canonical remote")
    if _git(["cat-file", "-e", authority["parent_commit"] + "^{commit}"], work, check=False).returncode:
        raise BootstrapError("authorized parent commit is unavailable from canonical remote")
    _git(["checkout", "--detach", "--no-recurse-submodules", authority["parent_commit"]], work)
    head = _git(["rev-parse", "HEAD"], work).stdout.strip()
    if head != authority["parent_commit"]:
        raise BootstrapError("verified parent checkout does not match release authority")
    candidate = work / "deploy" / "mkchad_deploy.py"
    entry = os.lstat(candidate)
    if not stat.S_ISREG(entry.st_mode) or stat.S_ISLNK(entry.st_mode) or entry.st_uid != os.getuid() or entry.st_mode & 0o022:
        raise BootstrapError("verified parent has no safe deployment engine")
    return candidate


def write_parent_bundle(work: pathlib.Path, authority: dict, path: pathlib.Path) -> pathlib.Path:
    """Create and flush a private Git bundle for the selected parent authority.

    Args:
        work: Verified parent checkout containing the authority objects.
        authority: Closed release or development authority object.
        path: New bundle destination beneath a private directory.

    Returns:
        ``path`` after exact mode and durability preparation.

    Raises:
        BootstrapError: If authority validation or Git bundle creation fails.
    """
    authority = _validate_authority(authority)
    ref = "refs/mkchad-bootstrap/authority"
    object_id = authority.get("tag_object", authority["parent_commit"])
    _git(["update-ref", ref, object_id], work)
    try:
        _git(["bundle", "create", str(path), ref], work)
    finally:
        _git(["update-ref", "-d", ref], work)
    os.chmod(path, 0o600)
    with open(path, "rb") as handle:
        os.fsync(handle.fileno())
    _fsync_directory(path.parent)
    return path


def write_evidence(directory: pathlib.Path, authority: dict, parent_bundle: pathlib.Path) -> pathlib.Path:
    """Write owner-only evidence binding authority to exact parent-bundle bytes.

    Args:
        directory: New or existing private evidence directory.
        authority: Closed release or development authority object.
        parent_bundle: Mode-``0600`` bundle whose SHA-256 is recorded.

    Returns:
        Path to the new flushed evidence JSON file.

    Raises:
        BootstrapError: If inputs are unsafe, evidence already exists, or a
            complete durable write cannot be made.
    """
    if not directory.exists() and not directory.is_symlink():
        directory.mkdir(mode=0o700)
    _safe_directory(directory)
    digest = _digest_private(parent_bundle)
    value = {"schema": SCHEMA, "deployer": DEPLOYER_ID, "authority": _validate_authority(authority), "parent_bundle_sha256": digest}
    path = directory / "verified-release.json"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        _write_all(fd, (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8"))
        os.fsync(fd)
    finally:
        os.close(fd)
    _fsync_directory(directory)
    return path


def _retained_evidence(path: pathlib.Path, bundle: pathlib.Path) -> dict:
    """Open closed evidence and prove its bundle digest and authority objects offline."""
    try:
        evidence = json.loads(_read_private(path), object_pairs_hook=_unique_json)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BootstrapError("malformed retained release evidence") from exc
    if not isinstance(evidence, dict) or set(evidence) != {"schema", "deployer", "authority", "parent_bundle_sha256"} or evidence["schema"] != SCHEMA or evidence["deployer"] != DEPLOYER_ID or not SHA256.fullmatch(evidence["parent_bundle_sha256"]):
        raise BootstrapError("retained release evidence is malformed")
    if _digest_private(bundle) != evidence["parent_bundle_sha256"]:
        raise BootstrapError("retained parent bundle integrity verification failed")
    authority = _validate_authority(evidence["authority"])
    temporary = pathlib.Path(tempfile.mkdtemp(prefix="mkchad-recovery-verify-"))
    try:
        repository = temporary / "parent.git"
        _git(["clone", "--mirror", str(bundle), str(repository)])
        if authority["kind"] == "annotated-tag-plus-sha":
            if _git(["cat-file", "-t", authority["tag_object"]], repository).stdout.strip() != "tag":
                raise BootstrapError("retained annotated release tag object is unavailable")
            peeled = _git(["rev-parse", authority["tag_object"] + "^{commit}"], repository).stdout.strip()
            if peeled != authority["parent_commit"]:
                raise BootstrapError("retained annotated release identity changed")
        elif _git(["cat-file", "-e", authority["parent_commit"] + "^{commit}"], repository, check=False).returncode:
            raise BootstrapError("retained development parent commit is unavailable")
        expected_deployer = _git(["rev-parse", authority["parent_commit"] + ":deploy/mkchad_deploy.py"], repository).stdout.strip()
        actual_deployer = _git(["hash-object", "--no-filters", str(path.parent / "deployer.py")], repository).stdout.strip()
        if actual_deployer != expected_deployer:
            raise BootstrapError("retained deployer does not match release authority")
    finally:
        shutil.rmtree(temporary, ignore_errors=True)
    return evidence


def authenticated_recovery_deployer(state_root: pathlib.Path, deployment_id: str) -> pathlib.Path:
    """Validate one canonical recovery record and return its retained deployer.

    Args:
        state_root: Current-user-owned mode-``0700`` deployment state directory.
        deployment_id: UUID selecting exactly one canonical record.

    Returns:
        Path to the digest- and authority-bound retained deployment engine.

    Raises:
        BootstrapError: If record identity, authority, inventory, bytes, or
            recovery state is unsafe. The function performs no network access
            and does not mutate retained evidence.
    """
    _safe_directory(state_root)
    if not DEPLOYMENT_ID.fullmatch(deployment_id):
        raise BootstrapError("invalid deployment UUID")
    record_path = state_root / f"{deployment_id}.json"
    try:
        record = json.loads(_read_private(record_path), object_pairs_hook=_unique_json)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BootstrapError("malformed deployment record") from exc
    if record.get("schema") != SCHEMA or record.get("id") != deployment_id or record.get("deployer") != DEPLOYER_ID:
        raise BootstrapError("deployment record identity is invalid")
    if any(key.startswith("cgroup") for key in record):
        raise BootstrapError("legacy cgroup deployment record requires recovery with the prior implementation")
    if FORBIDDEN_AUTHORITY_ALIASES & set(record):
        raise BootstrapError("deployment record contains obsolete release-authority fields")
    if record.get("state") not in {"failed", "in_progress"} or record.get("state_root") != str(state_root):
        raise BootstrapError("deployment record is not recoverable at this state root")
    bundle_name = record.get("recovery_bundle")
    if not isinstance(bundle_name, str) or not re.fullmatch(r"[0-9a-f-]+\.bundle", bundle_name):
        raise BootstrapError("deployment recovery bundle name is invalid")
    bundle = state_root / bundle_name
    entry = os.lstat(bundle)
    if not stat.S_ISDIR(entry.st_mode) or stat.S_ISLNK(entry.st_mode) or entry.st_uid != os.getuid() or stat.S_IMODE(entry.st_mode) != 0o700:
        raise BootstrapError("deployment recovery bundle is unsafe")
    digests = record.get("bundle_digests")
    if not isinstance(digests, dict):
        raise BootstrapError("deployment recovery bundle is incomplete")
    for name, expected in digests.items():
        if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9_.-]+", name) or not isinstance(expected, str) or not SHA256.fullmatch(expected):
            raise BootstrapError("deployment recovery bundle digest table is malformed")
        if _digest_private(bundle / name) != expected:
            raise BootstrapError("deployment recovery bundle integrity verification failed")
    mandatory = {"deployer.py", "verified-release.json", "release.json", "release-v1.schema.json", "receipt-v1.schema.json", "parent.bundle"}
    if not mandatory <= set(digests):
        raise BootstrapError("deployment recovery bundle is incomplete")
    evidence = _retained_evidence(bundle / "verified-release.json", bundle / "parent.bundle")
    if _validate_authority(record.get("authority")) != evidence["authority"]:
        raise BootstrapError("deployment record authority does not match retained release evidence")
    return bundle / "deployer.py"


def _unique_json(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise BootstrapError("duplicate deployment record key")
        result[key] = value
    return result


def main(argv: list[str] | None = None) -> int:
    """Run keyless deployment bootstrap or state-bound offline recovery.

    Args:
        argv: Optional command-line arguments; process arguments are used when
            omitted.

    Returns:
        Child command status, or ``2`` for a bootstrap validation failure.

    Side effects:
        Creates a private temporary checkout for deployment, or invokes the
        retained deployer for recovery. Recovery never contacts a remote.
    """
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    subcommands = parser.add_subparsers(dest="command")
    recover = subcommands.add_parser("recover")
    recover.add_argument("--deployment-id", required=True)
    recover.add_argument("--mode", choices=("complete", "restore"), required=True)
    recover.add_argument("--state-root", type=pathlib.Path, required=True)
    recover.add_argument("--confirm-processes-quiesced", action="store_true")
    parser.add_argument("--release")
    parser.add_argument("--expected-parent-commit")
    parser.add_argument("--development-commit")
    parser.add_argument("--remote", default=DEFAULT_REMOTE)
    parser.add_argument("--image", type=pathlib.Path)
    args = parser.parse_args(argv)
    try:
        validate_installed_bootstrap(pathlib.Path(__file__).absolute())
        if args.command == "recover":
            if args.release or args.expected_parent_commit or args.development_commit or args.image or args.remote != DEFAULT_REMOTE:
                raise BootstrapError("recover accepts only recovery-specific arguments")
            deployer = authenticated_recovery_deployer(args.state_root, args.deployment_id)
            command = [sys.executable, str(deployer), "recover", "--deployment-id", args.deployment_id, "--mode", args.mode, "--state-root", str(args.state_root)]
            if args.confirm_processes_quiesced:
                command.append("--confirm-processes-quiesced")
            return subprocess.run(command, check=False).returncode
        release_form = bool(args.release) or bool(args.expected_parent_commit)
        development_form = bool(args.development_commit)
        if release_form == development_form or not args.image:
            raise BootstrapError("provide --release and --expected-parent-commit together, or exactly one --development-commit, plus --image")
        private = pathlib.Path(tempfile.mkdtemp(prefix="mkchad-bootstrap-"))
        os.chmod(private, 0o700)
        handed_off = False
        try:
            if release_form:
                if not args.release or not args.expected_parent_commit:
                    raise BootstrapError("--release requires --expected-parent-commit")
                authority = verify_annotated_tag(args.remote, args.release, args.expected_parent_commit, private / "parent")
            else:
                authority = development_evidence(args.development_commit)
            candidate = prepare_verified_checkout(args.remote, authority, private / "parent")
            evidence_dir = private / "evidence"
            evidence_dir.mkdir(mode=0o700)
            parent_bundle = write_parent_bundle(private / "parent", authority, evidence_dir / "parent.bundle")
            evidence_path = write_evidence(evidence_dir, authority, parent_bundle)
            command = [sys.executable, str(candidate), "deploy", "--repo", str(private / "parent"), "--verified-release", str(evidence_path), "--verified-parent-bundle", str(parent_bundle), "--image", str(args.image), "--bootstrap-root", str(private)]
            process = subprocess.Popen(command, cwd=private / "parent", close_fds=True)
            handed_off = True
            return process.wait()
        finally:
            if not handed_off:
                shutil.rmtree(private, ignore_errors=True)
    except BootstrapError as exc:
        print("mkchad-release-bootstrap: " + str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
