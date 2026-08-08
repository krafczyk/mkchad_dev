#!/usr/bin/env bash
# Build and validate the exact nested container-tools source selected by msk_containers.
set -euo pipefail
umask 077

script_dir=$(dirname "$(realpath "$0")")
root=$(realpath "$script_dir/..")
parent_root="$root/msk_containers"
child_root="$parent_root/container_tools"
work_root='/tmp/mkchad-v1/container-tools-c11'

compiler=$(command -v musl-gcc || true)
if [[ -z $compiler ]]; then
  printf '%s\n' 'container-tools real builder gate unavailable: musl-gcc is required' >&2
  exit 77
fi

[[ -z $(git -C "$parent_root" status --porcelain=v1 --untracked-files=all) ]] || {
  printf '%s\n' 'container-tools real builder gate requires a clean msk_containers checkout' >&2
  exit 1
}
[[ -z $(git -C "$child_root" status --porcelain=v1 --untracked-files=all) ]] || {
  printf '%s\n' 'container-tools real builder gate requires a clean container_tools checkout' >&2
  exit 1
}

selected_commit=$(git -C "$parent_root" rev-parse HEAD:container_tools)
child_commit=$(git -C "$child_root" rev-parse HEAD)
[[ $child_commit == "$selected_commit" ]] || {
  printf '%s\n' 'container-tools real builder gate requires the gitlink-selected child commit' >&2
  exit 1
}

[[ $# -eq 0 ]] || { printf '%s\n' 'usage: container_tools_real_builder_gate.sh' >&2; exit 2; }
mkdir -p -- "$work_root"
work=$(mktemp -d "$work_root/real-builder.XXXXXX")
trap 'rm -rf -- "$work"' EXIT
CT_MUSL_CC="$compiler" "$child_root/scripts/build-package.sh" \
  --build-root "$work/build" --source-commit "$selected_commit" \
  --output-dir "$work/output" --libc musl >/dev/null
shopt -s nullglob
archives=("$work/output"/*.tar.gz)
[[ ${#archives[@]} -eq 1 && -f ${archives[0]} ]] || {
  printf '%s\n' 'container-tools real builder gate did not produce one archive' >&2
  exit 1
}
archive=${archives[0]}
digest=$(sha256sum "$archive" | awk '{print $1}')
native_architecture=$(uname -m)
case $native_architecture in
  amd64|x86_64) native_architecture=x86_64 ;;
  arm64|aarch64) native_architecture=aarch64 ;;
  ppc64le) ;;
  *) printf 'container-tools real builder gate has unsupported architecture: %s\n' "$native_architecture" >&2; exit 1 ;;
esac
IFS=$'\t' read -r version architecture libc source_commit < <(
  python3 - "$archive" <<'PY'
import json
import pathlib
import re
import sys
import tarfile

archive = pathlib.Path(sys.argv[1])
with tarfile.open(archive, "r:*") as handle:
    members = [member for member in handle.getmembers() if member.name.endswith("/archive.json")]
    if len(members) != 1 or not members[0].isfile():
        raise SystemExit("archive must contain exactly one regular archive.json")
    source = handle.extractfile(members[0])
    if source is None:
        raise SystemExit("archive metadata is unavailable")
    metadata = json.loads(source.read().decode("utf-8"))

expected = {"schema", "version", "architecture", "libc", "source_commit"}
if set(metadata) != expected or metadata["schema"] != "container-tools.archive/v1":
    raise SystemExit("archive metadata has an unexpected schema")
patterns = {
    "version": r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)(?:-[0-9A-Za-z]+(?:[.-][0-9A-Za-z]+)*)?",
    "architecture": r"[a-z0-9_.-]+",
    "libc": r"(?:musl|glibc)",
    "source_commit": r"[0-9a-f]{40}",
}
if any(not isinstance(metadata[key], str) or not re.fullmatch(pattern, metadata[key]) for key, pattern in patterns.items()):
    raise SystemExit("archive metadata is malformed")
print("\t".join(metadata[key] for key in ("version", "architecture", "libc", "source_commit")))
PY
)
[[ $architecture == "$native_architecture" ]] || {
  printf 'container-tools real builder gate archive architecture mismatch: %s\n' "$architecture" >&2
  exit 1
}
[[ $source_commit == "$selected_commit" && $libc == musl ]] || {
  printf '%s\n' 'container-tools real builder gate archive identity does not match its selected musl source' >&2
  exit 1
}
"$child_root/scripts/verify-package.sh" \
  --archive "$archive" --sha256 "$digest" --version "$version" \
  --source-commit "$source_commit" --architecture "$architecture" --libc "$libc" \
  --work-root "$work/verify" >/dev/null

python3 - "$root/deploy/mkchad_deploy.py" "$archive" "$selected_commit" "$digest" "$version" "$architecture" "$libc" <<'PY'
import hashlib
import importlib.util
import pathlib
import sys

deployer_path = pathlib.Path(sys.argv[1])
archive_path = pathlib.Path(sys.argv[2])
selected_commit, digest, version, architecture, libc = sys.argv[3:]
spec = importlib.util.spec_from_file_location("mkchad_deploy", deployer_path)
module = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(module)
archive = archive_path.resolve(strict=True)
identity = module._archive_package_identity(archive, digest)
assert hashlib.sha256(archive.read_bytes()).hexdigest() == digest
assert identity["source_commit"] == selected_commit
assert identity["version"] == version
assert identity["architecture"] == architecture == module._native_architecture()
assert identity["libc"] == libc == "musl"
assert archive.with_name(archive.name + ".sha256").read_text(encoding="ascii") == f"{identity['sha256']}  {archive.name}\n"
PY

printf '%s\n' 'container-tools real builder gate passed'
