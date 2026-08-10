#!/usr/bin/env bash
# Run deterministic deployment contracts in an isolated environment.
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
mkdir -m 700 "$HOME" "$XDG_CONFIG_HOME" "$XDG_DATA_HOME" "$XDG_STATE_HOME" "$XDG_RUNTIME_DIR" "$TMPDIR"

python3 -m py_compile deploy/mkchad_release.py deploy/mkchad_develop.py deploy/mkchad_deploy.py
python3 -m unittest -v tests.test_mkchad_release tests.test_mkchad_develop tests.test_mkchad_deploy
bash tests/container_tools_real_builder_gate.sh
bash tests/container_tools_release_runtime_test.sh --self-test

if python3 deploy/mkchad_release.py --release fixture --image /tmp/image.sif \
  --container-tools-host-package /tmp/package.tar.gz >/dev/null 2>&1; then
  printf '%s\n' 'release CLI accepted the removed package selector' >&2
  exit 1
fi
if python3 deploy/mkchad_develop.py --image /tmp/image.sif \
  --container-tools-source /tmp/source >/dev/null 2>&1; then
  printf '%s\n' 'developer CLI accepted the removed source selector' >&2
  exit 1
fi

printf '%s\n' 'deployment tests passed'
