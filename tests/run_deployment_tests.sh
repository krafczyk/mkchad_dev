#!/usr/bin/env bash
set -euo pipefail
umask 077

root=${1:?usage: run_deployment_tests.sh TASK_DIRECTORY}
canonical=$(realpath -m -- "$root")
case "$root" in
  /tmp/mkchad-v1/*) ;;
  *) printf '%s\n' 'test root must be below /tmp/mkchad-v1' >&2; exit 2 ;;
esac
case "$canonical" in
  /tmp/mkchad-v1/*) ;;
  *) printf '%s\n' 'test root resolves outside /tmp/mkchad-v1' >&2; exit 2 ;;
esac
[[ ! -e $canonical && ! -L $canonical ]] || { printf '%s\n' 'test root must not already exist' >&2; exit 2; }
root=$canonical
mkdir -m 700 "$root"
export HOME="$root/home"
export XDG_CONFIG_HOME="$root/config"
export XDG_DATA_HOME="$root/data"
export XDG_STATE_HOME="$root/state"
export XDG_RUNTIME_DIR="$root/runtime"
export TMPDIR="$root/tmp"
export GIT_CONFIG_NOSYSTEM=1
export GIT_CONFIG_GLOBAL=/dev/null
export GIT_CONFIG_SYSTEM=/dev/null
export GIT_TEMPLATE_DIR=
mkdir -p "$HOME" "$XDG_CONFIG_HOME" "$XDG_DATA_HOME" "$XDG_STATE_HOME" "$XDG_RUNTIME_DIR" "$TMPDIR"
chmod 700 "$HOME" "$XDG_CONFIG_HOME" "$XDG_DATA_HOME" "$XDG_STATE_HOME" "$XDG_RUNTIME_DIR" "$TMPDIR"
python3 -m unittest -v tests.test_mkchad_release_bootstrap tests.test_mkchad_deploy
