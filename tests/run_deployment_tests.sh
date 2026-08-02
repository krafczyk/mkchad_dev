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
mkdir -p "$HOME" "$XDG_CONFIG_HOME" "$XDG_DATA_HOME" "$XDG_STATE_HOME" "$XDG_RUNTIME_DIR" "$TMPDIR" "$canonical/source/deploy"
chmod 700 "$HOME" "$XDG_CONFIG_HOME" "$XDG_DATA_HOME" "$XDG_STATE_HOME" "$XDG_RUNTIME_DIR" "$TMPDIR"

python3 -m py_compile deploy/mkchad_release.py deploy/mkchad_deploy.py
python3 -m unittest -v tests.test_mkchad_release tests.test_mkchad_deploy

cat > "$canonical/source/deploy/mkchad_deploy.py" <<'PY'
import json
import pathlib
import sys

def value(name):
    return sys.argv[sys.argv.index(name) + 1]

result = pathlib.Path(value("--result-path"))
result.write_text(json.dumps({
    "schema": 1, "run_id": value("--run-id"), "release": value("--release"),
    "started_at": "2026-08-02T00:00:00Z", "completed_at": "2026-08-02T00:00:01Z",
    "outcome": "succeeded", "phase": "verify", "components": [], "artifact": {},
    "actions": [], "configuration_handoff": {}, "failed_step": None,
    "diagnostic": "configuration pending", "log_path": value("--log-path"),
}) + "\n", encoding="utf-8")
print("fixture deployer: configuration pending")
PY
git init -q "$canonical/source"
git -C "$canonical/source" add deploy/mkchad_deploy.py
git -C "$canonical/source" -c user.name=test -c user.email=test@example.invalid commit -q -m fixture
git -C "$canonical/source" tag v2-fixture
git clone -q --bare "$canonical/source" "$canonical/release.git"
git config --global url."file://$canonical/release.git".insteadOf git@github.com:krafczyk/mkchad_dev.git
image="$canonical/image.sif"
printf fixture > "$image"
for _ in 1 2 3 4; do python3 deploy/mkchad_release.py --release v2-fixture --image "$image"; done
test "$(printf '%s\n' "$XDG_STATE_HOME"/mkchad/deploy/logs/*.log | wc -l)" -eq 3
python3 - "$XDG_STATE_HOME/mkchad/deploy/latest-result.json" <<'PY'
import json
import pathlib
import sys
value = json.loads(pathlib.Path(sys.argv[1]).read_text())
assert value["release"] == "v2-fixture"
assert value["diagnostic"] == "configuration pending"
PY
if python3 deploy/mkchad_deploy.py recover --deployment-id 123e4567-e89b-12d3-a456-426614174000; then exit 1; fi
if python3 deploy/mkchad_release.py --release v2-fixture --image "$image" --expected-parent-commit "$(printf a%.0s {1..40})"; then exit 1; fi
