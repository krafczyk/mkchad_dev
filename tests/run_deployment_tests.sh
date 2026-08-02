#!/usr/bin/env bash
set -euo pipefail
umask 077

root=${1:?usage: run_deployment_tests.sh TASK_DIRECTORY}
canonical=$(realpath -m -- "$root")
case "$canonical" in /tmp/mkchad-v1/*) ;; *) printf '%s\n' 'test root must be below /tmp/mkchad-v1' >&2; exit 2 ;; esac
[[ ! -e $canonical && ! -L $canonical ]] || { printf '%s\n' 'test root must not already exist' >&2; exit 2; }
mkdir -m 700 "$canonical"
export HOME="$canonical/home"
export XDG_CONFIG_HOME="$canonical/config"
export XDG_DATA_HOME="$canonical/data"
export XDG_STATE_HOME="$canonical/state"
export XDG_RUNTIME_DIR="$canonical/runtime"
export TMPDIR="$canonical/tmp"
export GIT_CONFIG_NOSYSTEM=1 GIT_CONFIG_GLOBAL="$HOME/.gitconfig" GIT_CONFIG_SYSTEM=/dev/null GIT_TEMPLATE_DIR=
mkdir -p "$HOME" "$XDG_CONFIG_HOME" "$XDG_DATA_HOME" "$XDG_STATE_HOME" "$XDG_RUNTIME_DIR" "$TMPDIR" "$canonical/source/deploy" "$canonical/components" "$canonical/bin"
chmod 700 "$HOME" "$XDG_CONFIG_HOME" "$XDG_DATA_HOME" "$XDG_STATE_HOME" "$XDG_RUNTIME_DIR" "$TMPDIR"
export PATH="$canonical/bin:$PATH"
export NVIM_CONTAINER_DIR="$HOME/containers"

python3 -m py_compile deploy/mkchad_release.py deploy/mkchad_deploy.py
python3 -m unittest -v tests.test_mkchad_release tests.test_mkchad_deploy

cat > "$canonical/bin/apptainer" <<'SH'
#!/usr/bin/env sh
printf '%s\n' x86_64
SH
chmod 755 "$canonical/bin/apptainer"

mkdir -p "$canonical/components/container_tools"
cat > "$canonical/components/container_tools/tool.sh" <<'SH'
#!/usr/bin/env sh
exit 0
SH
chmod 755 "$canonical/components/container_tools/tool.sh"

mkdir -p "$canonical/components/msk_containers/bin" "$canonical/components/msk_containers/nvim/bin"
cat > "$canonical/components/msk_containers/bin/install_nvim.sh" <<'SH'
#!/usr/bin/env sh
set -eu
if [ "${1:-}" = --check ]; then
  { [ "$#" -eq 1 ] || { [ "$#" -eq 3 ] && [ "$2" = --recovery-dir ] && [ -d "$3" ]; }; } || exit 2
  exit 0
fi
[ "$#" -eq 2 ] && [ "$1" = --recovery-dir ] && [ -d "$2" ] || exit 2
mkdir -p "$HOME/.local/bin"
cp container_tools/*.sh nvim/bin/* "$HOME/.local/bin/"
chmod 755 "$HOME/.local/bin"/*
SH
cat > "$canonical/components/msk_containers/nvim/bin/nvim" <<'SH'
#!/usr/bin/env sh
exit 0
SH
cat > "$canonical/components/msk_containers/nvim/bin/nvim_library.sh" <<'SH'
#!/usr/bin/env sh
return 0
SH
cat > "$canonical/components/msk_containers/nvim/bin/mkchad-opencode-server" <<'SH'
#!/usr/bin/env sh
printf '{"schema":1,"ok":true,"command":"status","status":"%s"}\n' "${LIFECYCLE_STATUS:-inactive}"
SH
cat > "$canonical/components/msk_containers/nvim/bin/install_nvim_container" <<'SH'
#!/usr/bin/env sh
set -eu
dry_run=0
if [ "${1:-}" = --dry-run ]; then dry_run=1; shift; fi
[ "$#" -eq 3 ] && [ "$1" = --source-revision ] && [ "${#2}" -eq 40 ] && [ "${3##*/}" = nvim_container_x86.sif ] || exit 2
[ "$dry_run" -eq 0 ] || exit "${IMAGE_PREFLIGHT_STATUS:-0}"
[ "${IMAGE_INSTALL_STATUS:-0}" = 0 ] || exit "$IMAGE_INSTALL_STATUS"
mkdir -p "$NVIM_CONTAINER_DIR"
cp "$3" "$NVIM_CONTAINER_DIR/neovim_fixture.sif"
ln -sfn neovim_fixture.sif "$NVIM_CONTAINER_DIR/neovim.sif"
SH
chmod 755 "$canonical/components/msk_containers/bin/install_nvim.sh" "$canonical/components/msk_containers/nvim/bin/"*

mkdir -p "$canonical/components/mkchad"
printf '%s\n' 'opencode-server.json' 'lazy-lock.json' > "$canonical/components/mkchad/.gitignore"
printf '%s\n' 'return {}' > "$canonical/components/mkchad/init.lua"

for component in container_tools msk_containers mkchad; do
  git init -q "$canonical/components/$component"
  git -C "$canonical/components/$component" add .
  git -C "$canonical/components/$component" -c user.name=test -c user.email=test@example.invalid commit -q -m "$component"
done

image="$canonical/image.sif"
printf fixture > "$image"
cp deploy/mkchad_deploy.py deploy/release.json "$canonical/source/deploy/"
python3 - "$canonical/source/deploy/release.json" "$canonical/components" "$image" <<'PY'
import hashlib
import json
import pathlib
import subprocess
import sys

manifest_path = pathlib.Path(sys.argv[1])
components = pathlib.Path(sys.argv[2])
image = pathlib.Path(sys.argv[3])
manifest = json.loads(manifest_path.read_text())
for component in manifest["components"]:
    source = components / component["name"]
    if source.is_dir():
        component["commit"] = subprocess.check_output(["git", "-C", str(source), "rev-parse", "HEAD"], text=True).strip()
manifest["artifacts"]["nvim-image"][0]["sha256"] = hashlib.sha256(image.read_bytes()).hexdigest()
manifest_path.write_text(json.dumps(manifest) + "\n")
PY
git init -q "$canonical/source"
git -C "$canonical/source" add deploy/mkchad_deploy.py deploy/release.json
git -C "$canonical/source" -c user.name=test -c user.email=test@example.invalid commit -q -m fixture
git -C "$canonical/source" tag v2-fixture
git clone -q --bare "$canonical/source" "$canonical/release.git"
git config --global url."file://$canonical/components/".insteadOf git@github.com:krafczyk/
git config --global url."file://$canonical/release.git".insteadOf git@github.com:krafczyk/mkchad_dev.git

if IMAGE_PREFLIGHT_STATUS=7 python3 deploy/mkchad_release.py --release v2-fixture --image "$image"; then exit 1; fi
if IMAGE_INSTALL_STATUS=7 python3 deploy/mkchad_release.py --release v2-fixture --image "$image"; then exit 1; fi
python3 deploy/mkchad_release.py --release v2-fixture --image "$image"

printf '%s\n' 'return { updated = true }' > "$canonical/components/mkchad/init.lua"
git -C "$canonical/components/mkchad" add init.lua
git -C "$canonical/components/mkchad" -c user.name=test -c user.email=test@example.invalid commit -q -m update
python3 - "$canonical/source/deploy/release.json" "$canonical/components/mkchad" <<'PY'
import json
import pathlib
import subprocess
import sys

manifest_path = pathlib.Path(sys.argv[1])
source = pathlib.Path(sys.argv[2])
manifest = json.loads(manifest_path.read_text())
next(component for component in manifest["components"] if component["name"] == "mkchad")["commit"] = subprocess.check_output(["git", "-C", str(source), "rev-parse", "HEAD"], text=True).strip()
manifest_path.write_text(json.dumps(manifest) + "\n")
PY
git -C "$canonical/source" add deploy/release.json
git -C "$canonical/source" -c user.name=test -c user.email=test@example.invalid commit -q -m update
git -C "$canonical/source" tag v2-fixture-update
git --git-dir="$canonical/release.git" fetch -q "$canonical/source" refs/tags/v2-fixture-update:refs/tags/v2-fixture-update
python3 deploy/mkchad_release.py --release v2-fixture-update --image "$image"

printf dirty > "$XDG_CONFIG_HOME/mkchad/untracked-user-work"
if python3 deploy/mkchad_release.py --release v2-fixture-update --image "$image"; then exit 1; fi
test "$(cat "$XDG_CONFIG_HOME/mkchad/untracked-user-work")" = dirty
rm "$XDG_CONFIG_HOME/mkchad/untracked-user-work"
printf wrong > "$canonical/wrong-image.sif"
if python3 deploy/mkchad_release.py --release v2-fixture-update --image "$canonical/wrong-image.sif"; then exit 1; fi
LIFECYCLE_STATUS=healthy python3 deploy/mkchad_release.py --release v2-fixture-update --image "$image"
test "$(printf '%s\n' "$XDG_STATE_HOME"/mkchad/deploy/logs/*.log | wc -l)" -eq 3
python3 - "$XDG_STATE_HOME/mkchad/deploy/latest-result.json" <<'PY'
import json
import pathlib
import sys
value = json.loads(pathlib.Path(sys.argv[1]).read_text())
assert value["release"] == "v2-fixture-update"
assert value["outcome"] == "succeeded"
assert value["diagnostic"] == "deployment verified; configuration is pending"
assert [action["status"] for action in value["actions"]] == ["succeeded", "succeeded", "skipped-compliant", "skipped-compliant", "succeeded"]
PY
if python3 deploy/mkchad_deploy.py recover --deployment-id 123e4567-e89b-12d3-a456-426614174000; then exit 1; fi
if python3 deploy/mkchad_release.py --release v2-fixture --image "$image" --expected-parent-commit "$(printf a%.0s {1..40})"; then exit 1; fi
