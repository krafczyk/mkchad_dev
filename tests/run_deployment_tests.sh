#!/usr/bin/env bash
set -euo pipefail
umask 077

root=${1:?usage: run_deployment_tests.sh TASK_DIRECTORY}
project_root=$(pwd -P)
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
export MKCHAD_TRUST_GROUP_WRITABLE_ROOTS=caller-value

python3 -m py_compile deploy/mkchad_release.py deploy/mkchad_develop.py deploy/mkchad_deploy.py
python3 -m unittest -v tests.test_mkchad_release tests.test_mkchad_develop tests.test_mkchad_deploy

cat > "$canonical/bin/apptainer" <<'SH'
#!/usr/bin/env sh
set -eu
[ "$1" = exec ] || exit 2
if [ "$3" = uname ] && [ "$4" = -m ]; then
  printf '%s\n' x86_64
  exit 0
fi
[ "$3" = cat ] && [ "$4" = /etc/mkchad/container-tools-package.json ] || exit 2
cat "$2"
SH
chmod 755 "$canonical/bin/apptainer"

mkdir -p "$canonical/components/container_tools"
cat > "$canonical/components/container_tools/tool.sh" <<'SH'
#!/usr/bin/env sh
exit 0
SH
chmod 755 "$canonical/components/container_tools/tool.sh"
mkdir -p "$canonical/components/container_tools/scripts"
cat > "$canonical/components/container_tools/scripts/install-package.sh" <<'SH'
#!/usr/bin/env sh
set -eu
case "${1:-}" in --check|--apply|--verify) ;; *) exit 2 ;; esac
test -f "${3:-}" || exit 2
SH
chmod 755 "$canonical/components/container_tools/scripts/install-package.sh"

mkdir -p "$canonical/components/msk_containers/bin" "$canonical/components/msk_containers/nvim/bin"
cat > "$canonical/components/msk_containers/bin/install_nvim.sh" <<'SH'
#!/usr/bin/env sh
set -eu
[ "${MKCHAD_TRUST_GROUP_WRITABLE_ROOTS:-}" = 1 ] || exit 2
if [ "${1:-}" = --check ]; then
  exit 0
fi
[ "$1" = --recovery-dir ] && [ -d "$2" ] || exit 2
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
[ -z "${MKCHAD_TRUST_GROUP_WRITABLE_ROOTS:-}" ] || exit 2
dry_run=0
if [ "${1:-}" = --dry-run ]; then dry_run=1; shift; fi
[ "$#" -eq 3 ] || exit 2
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

git clone -q "$project_root/opencode-db" "$canonical/components/opencode-db"
git -C "$canonical/components/opencode-db" checkout -q --detach fd8782cf56c0823bf850f36c5967ade4da896d00

for component in container_tools msk_containers mkchad; do
  git init -q "$canonical/components/$component"
  git -C "$canonical/components/$component" add .
  git -C "$canonical/components/$component" -c user.name=test -c user.email=test@example.invalid commit -q -m "$component"
done

image="$canonical/image.sif"
package="$canonical/container-tools-host.tar.gz"
cp deploy/mkchad_deploy.py deploy/release.json "$canonical/source/deploy/"
python3 - "$canonical/source/deploy/release.json" "$canonical/components" "$image" "$package" <<'PY'
import hashlib
import io
import json
import pathlib
import subprocess
import sys
import tarfile

manifest_path = pathlib.Path(sys.argv[1])
components = pathlib.Path(sys.argv[2])
image = pathlib.Path(sys.argv[3])
package_path = pathlib.Path(sys.argv[4])
manifest = json.loads(manifest_path.read_text())
for component in manifest["components"]:
    source = components / component["name"]
    if source.is_dir():
        component["commit"] = subprocess.check_output(["git", "-C", str(source), "rev-parse", "HEAD"], text=True).strip()
packages = {entry["role"]: entry for entry in manifest["artifacts"]["container-tools-package"]}
tools_commit = next(component["commit"] for component in manifest["components"] if component["name"] == "container_tools")
for entry in packages.values():
    entry["source_commit"] = tools_commit
host_metadata = {key: value for key, value in packages["host"].items() if key != "sha256"}
payload = json.dumps(host_metadata, sort_keys=True, separators=(",", ":")).encode()
with tarfile.open(package_path, "w:gz", format=tarfile.PAX_FORMAT) as archive:
    member = tarfile.TarInfo("container-tools-package.json")
    member.size, member.mode, member.mtime = len(payload), 0o644, 0
    archive.addfile(member, io.BytesIO(payload))
packages["host"]["sha256"] = hashlib.sha256(package_path.read_bytes()).hexdigest()
image.write_text(json.dumps(packages["container"], sort_keys=True, separators=(",", ":")))
manifest["artifacts"]["nvim-image"][0]["sha256"] = hashlib.sha256(image.read_bytes()).hexdigest()
manifest_path.write_text(json.dumps(manifest) + "\n")
PY
git init -q "$canonical/source"
git -C "$canonical/source" add deploy/mkchad_deploy.py deploy/release.json
git -C "$canonical/source" -c user.name=test -c user.email=test@example.invalid commit -q -m fixture
git -C "$canonical/source" tag v2-fixture
git clone -q --bare "$canonical/source" "$canonical/release.git"
git config --global url."file://$canonical/components/".insteadOf git@github.com:krafczyk/
git config --global url."file://$canonical/components/opencode-db".insteadOf git@github.com:krafczyk/opencode-db.git
git config --global url."file://$canonical/release.git".insteadOf git@github.com:krafczyk/mkchad_dev.git

if IMAGE_PREFLIGHT_STATUS=7 python3 deploy/mkchad_release.py --release v2-fixture --image "$image" --container-tools-host-package "$package"; then exit 1; fi
if IMAGE_INSTALL_STATUS=7 python3 deploy/mkchad_release.py --release v2-fixture --image "$image" --container-tools-host-package "$package"; then exit 1; fi
rm "$HOME/.local/bin/mkchad-opencode-server"
printf '%s\n' legacy > "$HOME/.local/bin/nvim_library.sh"
if python3 deploy/mkchad_release.py --release v2-fixture --image "$image" --container-tools-host-package "$package"; then exit 1; fi
python3 deploy/mkchad_release.py --release v2-fixture --image "$image" --container-tools-host-package "$package" --replace-partial

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
python3 deploy/mkchad_release.py --release v2-fixture-update --image "$image" --container-tools-host-package "$package"

printf dirty > "$XDG_CONFIG_HOME/mkchad/untracked-user-work"
if python3 deploy/mkchad_release.py --release v2-fixture-update --image "$image" --container-tools-host-package "$package"; then exit 1; fi
test "$(cat "$XDG_CONFIG_HOME/mkchad/untracked-user-work")" = dirty
rm "$XDG_CONFIG_HOME/mkchad/untracked-user-work"
printf wrong > "$canonical/wrong-image.sif"
if python3 deploy/mkchad_release.py --release v2-fixture-update --image "$canonical/wrong-image.sif" --container-tools-host-package "$package"; then exit 1; fi
LIFECYCLE_STATUS=healthy python3 deploy/mkchad_release.py --release v2-fixture-update --image "$image" --container-tools-host-package "$package"
test "$(printf '%s\n' "$XDG_STATE_HOME"/mkchad/deploy/logs/*.log | wc -l)" -eq 3
python3 - "$XDG_STATE_HOME/mkchad/deploy/latest-result.json" <<'PY'
import json
import pathlib
import sys
value = json.loads(pathlib.Path(sys.argv[1]).read_text())
assert value["release"] == "v2-fixture-update"
assert value["outcome"] == "succeeded"
assert value["diagnostic"] == "deployment verified; configuration is pending"
assert [action["status"] for action in value["actions"]] == ["succeeded", "succeeded", "succeeded", "succeeded", "skipped-compliant", "skipped-compliant", "skipped-compliant", "succeeded", "succeeded", "skipped-compliant", "succeeded"]
assert value["packages"]["observed"] == value["packages"]["expected"]
assert {component["name"] for component in value["components"]} >= {"opencode-db"}
launcher = pathlib.Path.home() / ".local/bin/opencode-db"
assert launcher.is_file() and launcher.stat().st_mode & 0o777 == 0o755
PY
test -z "$(git -C "$XDG_DATA_HOME/mkchad/components/opencode-db" status --porcelain=v1 --untracked-files=all)"
if python3 deploy/mkchad_deploy.py recover --deployment-id 123e4567-e89b-12d3-a456-426614174000; then exit 1; fi
if python3 deploy/mkchad_release.py --release v2-fixture --image "$image" --container-tools-host-package "$package" --expected-parent-commit "$(printf a%.0s {1..40})"; then exit 1; fi

developer="$canonical/developer-tree"
workspace="$developer/workspace"
mkdir -p "$developer" "$workspace/deploy"
git clone -q "$canonical/components/container_tools" "$developer/tools-source"
git clone -q "$canonical/components/msk_containers" "$developer/msk-source"
git -C "$developer/msk-source" -c protocol.file.allow=always submodule add -q "$developer/tools-source" container_tools
git -C "$developer/msk-source" -c user.name=test -c user.email=test@example.invalid commit -q -m nested-tools
git clone -q "$canonical/components/mkchad" "$developer/mkchad-source"
git clone -q "$canonical/components/opencode-db" "$developer/opencode-db-source"
cp deploy/mkchad_develop.py deploy/mkchad_deploy.py deploy/mkchad_release.py "$workspace/deploy/"
chmod 755 "$workspace/deploy/mkchad_develop.py" "$workspace/deploy/mkchad_deploy.py" "$workspace/deploy/mkchad_release.py"
git init -q "$workspace"
git -C "$workspace" -c protocol.file.allow=always submodule add -q "$developer/msk-source" msk_containers
git -C "$workspace" -c protocol.file.allow=always submodule add -q "$developer/mkchad-source" mkchad
git -C "$workspace" -c protocol.file.allow=always submodule add -q "$developer/opencode-db-source" opencode-db
git -C "$workspace" -c protocol.file.allow=always submodule update -q --init --recursive
git -C "$workspace" add .
git -C "$workspace" -c user.name=test -c user.email=test@example.invalid commit -q -m workspace

tools="$workspace/msk_containers/container_tools"
printf '%s\n' local-tools-one >> "$tools/tool.sh"
git -C "$tools" add tool.sh
git -C "$tools" -c user.name=test -c user.email=test@example.invalid commit -q -m local-tools-one
git -C "$workspace/msk_containers" add container_tools
git -C "$workspace/msk_containers" -c user.name=test -c user.email=test@example.invalid commit -q -m local-msk
printf '%s\n' local-tools-two >> "$tools/tool.sh"
git -C "$tools" add tool.sh
git -C "$tools" -c user.name=test -c user.email=test@example.invalid commit -q -m local-tools-two
printf '%s\n' 'return { developer = true }' >> "$workspace/mkchad/init.lua"
git -C "$workspace/mkchad" add init.lua
git -C "$workspace/mkchad" -c user.name=test -c user.email=test@example.invalid commit -q -m local-mkchad
printf '%s\n' '# local database child' >> "$workspace/opencode-db/bin/opencode-db"
git -C "$workspace/opencode-db" add bin/opencode-db
git -C "$workspace/opencode-db" -c user.name=test -c user.email=test@example.invalid commit -q -m local-opencode-db
for child in "$workspace/msk_containers" "$tools" "$workspace/mkchad" "$workspace/opencode-db"; do
  git -C "$child" remote remove origin
done
printf '%s\n' parent-dirty > "$workspace/parent-untracked"
msk_commit=$(git -C "$workspace/msk_containers" rev-parse HEAD)
tools_commit=$(git -C "$tools" rev-parse HEAD)
mkchad_commit=$(git -C "$workspace/mkchad" rev-parse HEAD)
opencode_db_commit=$(git -C "$workspace/opencode-db" rev-parse HEAD)

developer_home="$developer/home"
developer_config="$developer/config"
developer_data="$developer/data"
developer_state="$developer/state"
developer_runtime="$developer/runtime"
developer_tmp="$developer/tmp"
mkdir -p "$developer_home/containers" "$developer_config" "$developer_data" "$developer_state" "$developer_runtime" "$developer_tmp"
chmod 700 "$developer_home" "$developer_config" "$developer_data" "$developer_state" "$developer_runtime" "$developer_tmp"
developer_package="$developer/container-tools-host.tar.gz"
python3 - "$developer_package" "$developer_home/containers/active.sif" "$tools_commit" <<'PY'
import hashlib
import io
import json
import pathlib
import sys
import tarfile

package, image, commit = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2]), sys.argv[3]
identity = {"role": "host", "architecture": "x86_64", "version": "1.0.0-dev", "product_major": 1, "source_commit": commit, "libc": "glibc", "source_filename_class": "container-tools-package"}
payload = json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()
with tarfile.open(package, "w:gz", format=tarfile.PAX_FORMAT) as archive:
    member = tarfile.TarInfo("container-tools-package.json")
    member.size, member.mode, member.mtime = len(payload), 0o644, 0
    archive.addfile(member, io.BytesIO(payload))
identity["sha256"] = hashlib.sha256(package.read_bytes()).hexdigest()
image.write_text(json.dumps({**identity, "role": "container"}, sort_keys=True, separators=(",", ":")))
PY
ln -s active.sif "$developer_home/containers/neovim.sif"
active_target=$(readlink "$developer_home/containers/neovim.sif")
active_digest=$(sha256sum "$developer_home/containers/active.sif" | cut -d' ' -f1)
developer_package_sha=$(sha256sum "$developer_package" | cut -d' ' -f1)

run_developer() {
  (
    cd "$workspace"
    env HOME="$developer_home" XDG_CONFIG_HOME="$developer_config" XDG_DATA_HOME="$developer_data" XDG_STATE_HOME="$developer_state" XDG_RUNTIME_DIR="$developer_runtime" TMPDIR="$developer_tmp" NVIM_CONTAINER_DIR="$developer_home/containers" ./deploy/mkchad_develop.py "$@" --container-tools-host-package "$developer_package" --container-tools-host-sha256 "$developer_package_sha" --container-tools-host-version 1.0.0-dev --container-tools-host-source-commit "$tools_commit" --container-tools-host-architecture x86_64 --container-tools-host-libc glibc
  )
}

run_developer --replace-partial
python3 - "$developer_state/mkchad/deploy/latest-result.json" "$msk_commit" "$tools_commit" "$mkchad_commit" "$opencode_db_commit" <<'PY'
import json
import pathlib
import sys

result = json.loads(pathlib.Path(sys.argv[1]).read_text())
assert result["mode"] == "developer"
assert result["outcome"] == "succeeded"
assert result["diagnostic"] == "developer deployment verified"
assert result["workspace"]["dirty"] is True
assert result["image"]["choice"] == "omitted"
assert all("image" not in action["id"] for action in result["actions"])
assert {item["name"]: item["observed_commit"] for item in result["components"]} == dict(zip(("msk_containers", "container_tools", "mkchad", "opencode-db"), sys.argv[2:]))
PY
test "$(git -C "$developer_data/mkchad/components/msk_containers" rev-parse HEAD)" = "$msk_commit"
test "$(git -C "$developer_data/mkchad/components/msk_containers/container_tools" rev-parse HEAD)" = "$tools_commit"
test "$(git -C "$developer_config/mkchad" rev-parse HEAD)" = "$mkchad_commit"
test "$(git -C "$developer_data/mkchad/components/opencode-db" rev-parse HEAD)" = "$opencode_db_commit"
test -x "$developer_home/.local/bin/opencode-db"
"$developer_home/.local/bin/opencode-db" --help >/dev/null
test -z "$(git -C "$developer_data/mkchad/components/opencode-db" status --porcelain=v1 --untracked-files=all)"
test "$(readlink "$developer_home/containers/neovim.sif")" = "$active_target"
test "$(sha256sum "$developer_home/containers/active.sif" | cut -d' ' -f1)" = "$active_digest"

same_image="$developer/caller-same.sif"
cp "$developer_home/containers/active.sif" "$same_image"
run_developer --image "$same_image"
python3 - "$developer_state/mkchad/deploy/latest-result.json" <<'PY'
import json
import pathlib
result = json.loads(pathlib.Path(__import__("sys").argv[1]).read_text())
assert result["image"]["choice"] == "reused"
assert {action["id"]: action["status"] for action in result["actions"] if "image" in action["id"]} == {"image-install-preflight": "skipped-compliant", "image-install": "skipped-compliant"}
PY
test -f "$same_image"
test "$(readlink "$developer_home/containers/neovim.sif")" = "$active_target"
test "$(sha256sum "$developer_home/containers/active.sif" | cut -d' ' -f1)" = "$active_digest"

changed_image="$developer/caller-changed.sif"
python3 - "$changed_image" "$tools_commit" "$developer_package_sha" <<'PY'
import json
import pathlib
import sys

path, commit, digest = pathlib.Path(sys.argv[1]), sys.argv[2], sys.argv[3]
path.write_text(json.dumps({"role": "container", "architecture": "x86_64", "version": "1.0.0-dev", "product_major": 1, "source_commit": commit, "sha256": digest, "libc": "glibc", "source_filename_class": "container-tools-package"}, sort_keys=True, separators=(",", ":")) + "\n")
PY
changed_digest=$(sha256sum "$changed_image" | cut -d' ' -f1)
run_developer --image "$changed_image"
python3 - "$developer_state/mkchad/deploy/latest-result.json" <<'PY'
import json
import pathlib
result = json.loads(pathlib.Path(__import__("sys").argv[1]).read_text())
assert result["image"]["choice"] == "install"
assert {action["id"]: action["status"] for action in result["actions"] if "image" in action["id"]} == {"image-install-preflight": "succeeded", "image-install": "succeeded"}
PY
test -f "$changed_image"
test "$(sha256sum "$changed_image" | cut -d' ' -f1)" = "$changed_digest"
test "$(sha256sum "$developer_home/containers/neovim_fixture.sif" | cut -d' ' -f1)" = "$changed_digest"
test "$(sha256sum "$developer_home/containers/$(readlink "$developer_home/containers/neovim.sif")" | cut -d' ' -f1)" = "$changed_digest"
