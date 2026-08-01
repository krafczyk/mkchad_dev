#!/usr/bin/env python3
"""Independently provisioned trust bootstrap for MkChad parent releases.

The bootstrap validates its installed digest and private current-user trust
root before fetching one annotated SSH-signed tag.  It records descriptor-safe
verification evidence for candidate deployment code, or labels an explicitly
requested full development SHA.  ``recover`` is intentionally state-bound: it
accepts a UUID, authenticates the retained record, and invokes no network path.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import re
import shutil
import stat
import subprocess
import sys
import tempfile


SCHEMA = 1
DEPLOYER_ID = "mkchad-deploy-v1"
DEFAULT_REMOTE = "git@github.com:krafczyk/mkchad_dev.git"
SHA1 = re.compile(r"^[0-9a-f]{40}$")
TAG = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]*$")
DEPLOYMENT_ID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")


class BootstrapError(RuntimeError):
    """Raised when bootstrap trust, tag identity, or recovery state is unsafe."""


def _safe_directory(path: pathlib.Path):
    if not path.is_absolute():
        raise BootstrapError("trust root must be absolute")
    current = pathlib.Path("/")
    for part in path.parts[1:]:
        current /= part
        ancestor = os.lstat(current)
        if stat.S_ISLNK(ancestor.st_mode) or not stat.S_ISDIR(ancestor.st_mode):
            raise BootstrapError(f"trust path contains a symlink or non-directory: {current}")
    entry = os.lstat(path)
    if not stat.S_ISDIR(entry.st_mode) or stat.S_ISLNK(entry.st_mode) or entry.st_uid != os.getuid() or stat.S_IMODE(entry.st_mode) != 0o700:
        raise BootstrapError("trust root must be current-user-owned mode 0700")


def _read_private(path: pathlib.Path) -> bytes:
    entry = os.lstat(path)
    if not stat.S_ISREG(entry.st_mode) or stat.S_ISLNK(entry.st_mode) or entry.st_uid != os.getuid() or entry.st_mode & 0o022:
        raise BootstrapError(f"unsafe trust input: {path}")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        before = os.fstat(fd)
        if (entry.st_dev, entry.st_ino) != (before.st_dev, before.st_ino):
            raise BootstrapError("trust input changed before open")
        data = os.read(fd, 1024 * 1024)
        if os.read(fd, 1):
            raise BootstrapError("trust input is too large")
        after = os.fstat(fd)
        if (before.st_dev, before.st_ino, before.st_size) != (after.st_dev, after.st_ino, after.st_size):
            raise BootstrapError("trust input changed while reading")
        final = os.lstat(path)
        if (before.st_dev, before.st_ino) != (final.st_dev, final.st_ino):
            raise BootstrapError("trust input changed after open")
        return data
    finally:
        os.close(fd)


def verify_self_digest(script: pathlib.Path, digest_file: pathlib.Path):
    """Verify the bootstrap bytes against its separately provisioned SHA-256 file.

    Both parameters must be protected regular files.  A mismatch raises
    ``BootstrapError`` before a Git or candidate command can execute.
    """
    expected = _read_private(digest_file).decode("ascii").strip().split()[0]
    actual = hashlib.sha256(_read_private(script)).hexdigest()
    if not re.fullmatch(r"[0-9a-f]{64}", expected) or actual != expected:
        raise BootstrapError("bootstrap self digest mismatch")


def snapshot_trust_root(trust_root: pathlib.Path, snapshot_root: pathlib.Path) -> dict:
    """Snapshot exactly one authorized signer and a revocation file by descriptor.

    Returns descriptor-safe snapshot paths and the required
    ``mkchad-release`` principal.  Unknown, duplicate, or missing principals
    raise ``BootstrapError`` without executing candidate code.
    """
    _safe_directory(trust_root)
    allowed = _read_private(trust_root / "allowed_signers")
    revoked = _read_private(trust_root / "revoked_signers")
    lines = [line for line in allowed.decode("utf-8").splitlines() if line.strip() and not line.lstrip().startswith("#")]
    principals = [line.split(None, 1)[0] for line in lines if line.split()]
    if len(lines) != 1 or principals != ["mkchad-release"]:
        raise BootstrapError("allowed_signers must authorize exactly one mkchad-release principal")
    snapshot_root.mkdir(mode=0o700, parents=True, exist_ok=False)
    os.chmod(snapshot_root, 0o700)
    paths = {}
    for name, content in (("allowed_signers", allowed), ("revoked_signers", revoked)):
        path = snapshot_root / name
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        try:
            os.write(fd, content)
            os.fsync(fd)
        finally:
            os.close(fd)
        paths[name] = str(path)
    return {"principal": "mkchad-release", **paths}


def _git(args: list[str], cwd: pathlib.Path | None = None, check: bool = True):
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    env.update({"GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull, "GIT_TERMINAL_PROMPT": "0"})
    result = subprocess.run(["git", "-c", "core.hooksPath=/dev/null", "-c", "init.templateDir=", *args], cwd=cwd, env=env, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if check and result.returncode:
        digest = hashlib.sha256(result.stderr.encode()).hexdigest()
        raise BootstrapError(f"git command failed with exit {result.returncode}; stderr-sha256={digest}")
    return result


def verify_signed_tag(remote: str, tag: str, snapshot: dict, work: pathlib.Path) -> dict:
    """Fetch and authenticate one annotated tag without branch fallback.

    The tag must remain the same tag object from fetch through verification and
    must peel to one full parent commit.  Git performs SSH cryptographic and
    revocation validation using the retained signer snapshots.
    """
    if not TAG.fullmatch(tag) or tag.startswith("-") or ".." in tag:
        raise BootstrapError("invalid release tag")
    _git(["init", str(work)])
    reference = f"refs/tags/{tag}:refs/tags/{tag}"
    _git(["fetch", "--no-tags", remote, reference], work)
    if _git(["cat-file", "-t", f"refs/tags/{tag}"], work).stdout.strip() != "tag":
        raise BootstrapError("release must be an annotated tag")
    tag_object = _git(["rev-parse", f"refs/tags/{tag}^{{tag}}"], work).stdout.strip()
    if not SHA1.fullmatch(tag_object):
        raise BootstrapError("release must be an annotated tag")
    result = _git(["-c", "gpg.format=ssh", "-c", f"gpg.ssh.allowedSignersFile={snapshot['allowed_signers']}", "-c", f"gpg.ssh.revocationFile={snapshot['revoked_signers']}", "verify-tag", "--raw", tag_object], work, check=False)
    output = result.stdout + result.stderr
    if result.returncode or "mkchad-release" not in output:
        raise BootstrapError("tag signature is not authorized for mkchad-release")
    if _git(["rev-parse", f"refs/tags/{tag}^{{tag}}"], work).stdout.strip() != tag_object:
        raise BootstrapError("release tag moved during verification")
    peeled = _git(["rev-parse", f"{tag_object}^{{commit}}"], work).stdout.strip()
    if not SHA1.fullmatch(peeled):
        raise BootstrapError("tag did not peel to a parent commit")
    fingerprint_match = re.search(r"\bkey (SHA256:[A-Za-z0-9+/=]+)", output)
    if not fingerprint_match:
        raise BootstrapError("tag verification did not report the authorized key fingerprint")
    return {"schema": SCHEMA, "deployer": DEPLOYER_ID, "trust": "ssh-signed-tag", "principal": "mkchad-release", "key_fingerprint": fingerprint_match.group(1), "tag": tag, "tag_object": tag_object, "parent_commit": peeled, "verification_output_sha256": hashlib.sha256(output.encode()).hexdigest()}


def development_evidence(commit: str) -> dict:
    """Return conspicuously unadvertised development authority for one full SHA."""
    if not SHA1.fullmatch(commit):
        raise BootstrapError("development commit must be a full SHA")
    return {"schema": SCHEMA, "deployer": DEPLOYER_ID, "trust": "development-sha", "parent_commit": commit}


def prepare_verified_checkout(remote: str, evidence: dict, work: pathlib.Path) -> pathlib.Path:
    """Materialize and bind the exact verified parent commit for execution.

    The signed-tag path reuses the repository populated by ``verify_signed_tag``;
    development mode fetches only the explicit full commit. The returned script
    is always read from that detached checkout, never from a caller path.
    """
    if not (work / ".git").is_dir():
        _git(["init", str(work)])
        _git(["fetch", "--no-tags", remote, evidence["parent_commit"]], work)
    _git(["checkout", "--detach", "--no-recurse-submodules", evidence["parent_commit"]], work)
    head = _git(["rev-parse", "HEAD"], work).stdout.strip()
    if head != evidence["parent_commit"]:
        raise BootstrapError("verified parent checkout does not match release authority")
    candidate = work / "deploy" / "mkchad_deploy.py"
    entry = os.lstat(candidate)
    if not stat.S_ISREG(entry.st_mode) or stat.S_ISLNK(entry.st_mode) or entry.st_uid != os.getuid():
        raise BootstrapError("verified parent has no safe deployment engine")
    evidence["candidate_sha256"] = hashlib.sha256(_read_private(candidate)).hexdigest()
    evidence["checkout_commit"] = head
    return candidate


def _write_evidence(directory: pathlib.Path, evidence: dict) -> pathlib.Path:
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(directory, 0o700)
    path = directory / "verified-release.json"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        os.write(fd, (json.dumps(evidence, sort_keys=True) + "\n").encode())
        os.fsync(fd)
    finally:
        os.close(fd)
    return path


def authenticated_recovery_deployer(state_root: pathlib.Path, deployment_id: str) -> pathlib.Path:
    """Validate a canonical recovery record and its retained deployment engine."""
    _safe_directory(state_root)
    if not DEPLOYMENT_ID.fullmatch(deployment_id):
        raise BootstrapError("invalid deployment UUID")
    record_path = state_root / f"{deployment_id}.json"
    try:
        record = json.loads(_read_private(record_path), object_pairs_hook=lambda pairs: _unique_json(pairs))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BootstrapError("malformed deployment record") from exc
    if record.get("schema") != SCHEMA or record.get("id") != deployment_id or record.get("deployer") != DEPLOYER_ID:
        raise BootstrapError("deployment record identity is invalid")
    if any(key.startswith("cgroup") for key in record):
        raise BootstrapError("legacy cgroup deployment record requires recovery with the prior implementation")
    if record.get("state") not in {"failed", "in_progress"} or record.get("state_root") != str(state_root):
        raise BootstrapError("deployment record is not recoverable at this state root")
    bundle_name = record.get("recovery_bundle")
    if not isinstance(bundle_name, str) or not re.fullmatch(r"[0-9a-f-]+\.bundle", bundle_name):
        raise BootstrapError("deployment recovery bundle name is invalid")
    bundle = state_root / bundle_name
    bundle_entry = os.lstat(bundle)
    if not stat.S_ISDIR(bundle_entry.st_mode) or stat.S_ISLNK(bundle_entry.st_mode) or bundle_entry.st_uid != os.getuid() or stat.S_IMODE(bundle_entry.st_mode) != 0o700:
        raise BootstrapError("deployment recovery bundle is unsafe")
    digests = record.get("bundle_digests")
    if not isinstance(digests, dict) or "deployer.py" not in digests:
        raise BootstrapError("deployment recovery bundle is incomplete")
    for name, expected in digests.items():
        if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9_.-]+", name) or not isinstance(expected, str) or not re.fullmatch(r"[0-9a-f]{64}", expected):
            raise BootstrapError("deployment recovery bundle digest table is malformed")
        if hashlib.sha256(_read_private(bundle / name)).hexdigest() != expected:
            raise BootstrapError("deployment recovery bundle authentication failed")
    actual_files = set()
    for child in bundle.iterdir():
        entry = os.lstat(child)
        if stat.S_ISREG(entry.st_mode):
            actual_files.add(child.name)
        elif stat.S_ISDIR(entry.st_mode) and (child.name in {"host-tools", "staging"} or child.name.startswith("host-tools-complete-") or child.name.startswith("removed-")):
            if entry.st_uid != os.getuid() or stat.S_IMODE(entry.st_mode) != 0o700:
                raise BootstrapError("deployment recovery bundle has an unsafe recovery directory")
        else:
            raise BootstrapError("deployment recovery bundle has an undeclared entry")
    if actual_files != set(digests):
        raise BootstrapError("deployment recovery bundle inventory does not match its digest table")
    mandatory = {"deployer.py", "verified-release.json", "release.json", "release-v1.schema.json", "receipt-v1.schema.json"}
    if record.get("final_mutation_started", False):
        mandatory.add("parent.bundle")
    if not mandatory <= actual_files:
        raise BootstrapError("deployment recovery bundle is incomplete")
    evidence = json.loads(_read_private(bundle / "verified-release.json"), object_pairs_hook=lambda pairs: _unique_json(pairs))
    if evidence.get("schema") != SCHEMA or evidence.get("deployer") != DEPLOYER_ID or not SHA1.fullmatch(evidence.get("parent_commit", "")):
        raise BootstrapError("retained release evidence is malformed")
    if evidence.get("trust") == "ssh-signed-tag" and record.get("final_mutation_started", False):
        if not {"allowed_signers", "revoked_signers"} <= actual_files or not SHA1.fullmatch(evidence.get("tag_object", "")):
            raise BootstrapError("signed release recovery evidence is incomplete")
        verification = pathlib.Path(tempfile.mkdtemp(prefix="mkchad-recovery-verify-"))
        try:
            repository = verification / "parent.git"
            _git(["clone", "--mirror", str(bundle / "parent.bundle"), str(repository)])
            result = _git(
                ["-c", "gpg.format=ssh", "-c", f"gpg.ssh.allowedSignersFile={bundle / 'allowed_signers'}", "-c", f"gpg.ssh.revocationFile={bundle / 'revoked_signers'}", "verify-tag", "--raw", evidence["tag_object"]],
                repository,
                check=False,
            )
            output = result.stdout + result.stderr
            if result.returncode or "mkchad-release" not in output:
                raise BootstrapError("retained signed release cannot be reauthenticated")
            peeled = _git(["rev-parse", f"{evidence['tag_object']}^{{commit}}"], repository).stdout.strip()
            if peeled != evidence["parent_commit"]:
                raise BootstrapError("retained signed release identity changed")
        finally:
            shutil.rmtree(verification, ignore_errors=True)
    elif evidence.get("trust") != "development-sha":
        raise BootstrapError("retained release trust kind is unsupported")
    return bundle / "deployer.py"


def _unique_json(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise BootstrapError("duplicate deployment record key")
        result[key] = value
    return result


def main(argv: list[str] | None = None) -> int:
    """Run bootstrap release verification or its state-bound recovery handoff."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    subcommands = parser.add_subparsers(dest="command")
    recover = subcommands.add_parser("recover")
    recover.add_argument("--deployment-id", required=True)
    recover.add_argument("--mode", choices=("complete", "restore"), required=True)
    recover.add_argument("--trust-root", type=pathlib.Path, required=True)
    recover.add_argument("--state-root", type=pathlib.Path, required=True)
    recover.add_argument("--confirm-processes-quiesced", action="store_true")
    parser.add_argument("--release")
    parser.add_argument("--development-commit")
    parser.add_argument("--remote", default=DEFAULT_REMOTE)
    parser.add_argument("--trust-root", type=pathlib.Path)
    parser.add_argument("--image", type=pathlib.Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "recover":
            _safe_directory(args.trust_root)
            verify_self_digest(pathlib.Path(__file__), args.trust_root / "bootstrap.sha256")
            deployer = authenticated_recovery_deployer(args.state_root, args.deployment_id)
            command = [sys.executable, str(deployer), "recover", "--deployment-id", args.deployment_id, "--mode", args.mode, "--state-root", str(args.state_root)]
            if args.confirm_processes_quiesced:
                command.append("--confirm-processes-quiesced")
            return subprocess.run(command, check=False).returncode
        if bool(args.release) == bool(args.development_commit) or not args.trust_root:
            raise BootstrapError("provide exactly one of --release or --development-commit and --trust-root")
        verify_self_digest(pathlib.Path(__file__), args.trust_root / "bootstrap.sha256")
        private = pathlib.Path(tempfile.mkdtemp(prefix="mkchad-bootstrap-"))
        os.chmod(private, 0o700)
        handed_off = False
        try:
            if args.release:
                snapshot = snapshot_trust_root(args.trust_root, private / "trust")
                evidence = verify_signed_tag(args.remote, args.release, snapshot, private / "parent")
            else:
                evidence = development_evidence(args.development_commit)
            candidate = prepare_verified_checkout(args.remote, evidence, private / "parent")
            evidence_path = _write_evidence(private / "evidence", evidence)
            if not args.image:
                raise BootstrapError("--image is required")
            command = [sys.executable, str(candidate), "deploy", "--repo", str(private / "parent"), "--verified-release", str(evidence_path), "--image", str(args.image), "--bootstrap-root", str(private)]
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
