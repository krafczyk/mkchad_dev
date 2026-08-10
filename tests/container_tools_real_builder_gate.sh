#!/usr/bin/env bash
# Build and validate the exact nested container-tools source selected by msk_containers.
set -euo pipefail
umask 077

[[ $# -eq 0 ]] || { printf '%s\n' 'usage: container_tools_real_builder_gate.sh' >&2; exit 2; }

script_dir=$(dirname "$(realpath "$0")")
root=$(realpath "$script_dir/..")
parent_root="$root/msk_containers"
child_root="$parent_root/container_tools"
work_root='/tmp/mkchad-v1/container-tools-runtime-boundary'

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

architecture=$(uname -m)
case $architecture in
  amd64|x86_64) architecture=x86_64; compiler=musl-gcc ;;
  arm64|aarch64) architecture=aarch64; compiler=musl-gcc ;;
  ppc64le) compiler=gcc ;;
  *) printf 'container-tools real builder gate has unsupported architecture: %s\n' "$architecture" >&2; exit 1 ;;
esac
command -v "$compiler" >/dev/null 2>&1 || {
  printf 'container-tools real builder gate unavailable: %s is required\n' "$compiler" >&2
  exit 77
}

mkdir -p -- "$work_root"
work=$(mktemp -d "$work_root/real-builder.XXXXXX")
trap 'rm -rf -- "$work"' EXIT
cmake -S "$child_root" -B "$work/build" \
  -DCMAKE_BUILD_TYPE=Release \
  -DCONTAINER_TOOLS_STATIC=ON \
  -DCMAKE_C_COMPILER="$compiler" \
  -DCMAKE_INSTALL_PREFIX="$work/prefix"
cmake --build "$work/build"
cmake --install "$work/build"

tool="$work/prefix/bin/container-tools"
"$tool" --version
"$tool" --version --json > "$work/version.json"
python3 - "$work/version.json" "$selected_commit" "$architecture" <<'PY'
import json
import pathlib
import sys

identity = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
assert set(identity) == {"product_version", "source_commit", "architecture", "mount_plan_grammar"}
assert identity["source_commit"] == sys.argv[2]
assert identity["architecture"] == sys.argv[3]
assert identity["mount_plan_grammar"] == "ct-mount-plan-v1"
assert isinstance(identity["product_version"], str) and identity["product_version"]
PY

if readelf -lW "$tool" | grep -Fq ' INTERP '; then
  printf '%s\n' 'container-tools real builder gate produced a dynamic interpreter' >&2
  exit 1
fi
[[ ! -e $work/prefix/share/container-tools/release.json ]]
[[ ! -e $work/prefix/etc/mkchad/container-tools-package.json ]]
printf '%s\n' 'container-tools real builder gate passed'
