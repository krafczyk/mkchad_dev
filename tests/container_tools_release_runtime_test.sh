#!/usr/bin/env bash
# Validate release source selection without package or image identity admission.
set -euo pipefail
umask 077

script_dir=$(dirname "$(realpath "$0")")
root=$(realpath "$script_dir/..")
work_root='/tmp/mkchad-v1/container-tools-runtime-boundary'

usage() {
  printf '%s\n' 'usage: container_tools_release_runtime_test.sh [--self-test|--native]'
}

unavailable() {
  printf 'container-tools release runtime evidence unavailable: %s\n' "$1" >&2
  return 77
}

self_test() {
  local work manifest result

  command -v jq >/dev/null 2>&1 || { unavailable 'jq is required'; return $?; }
  mkdir -p "$work_root"
  work=$(mktemp -d "$work_root/release-runtime.XXXXXX")
  trap 'rm -rf -- "$work"' RETURN
  manifest="$work/release.json"
  result="$work/result.json"
  cp "$root/deploy/release.json" "$manifest"

  jq -e '
    .schema == 2 and
    (.artifacts | keys) == ["nvim-image"] and
    ([.components[] | select(.name == "container_tools")] | length == 1) and
    ([.components[] | select(.name == "container_tools")][0].actions == []) and
    ((tostring | contains("container-tools-package")) | not)
  ' "$manifest" >/dev/null

  python3 - "$root/deploy/mkchad_deploy.py" "$manifest" "$result" <<'PY'
import importlib.util
import json
import pathlib
import sys

spec = importlib.util.spec_from_file_location("mkchad_deploy", sys.argv[1])
module = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(module)
manifest = json.loads(pathlib.Path(sys.argv[2]).read_text(encoding="utf-8"))
module.validate_release_v2_document(manifest)
image = manifest["artifacts"]["nvim-image"][0]
result = {
    "schema": 2,
    "run_id": "123e4567-e89b-12d3-a456-426614174000",
    "release": "fixture",
    "started_at": "2026-08-10T00:00:00Z",
    "completed_at": "2026-08-10T00:00:01Z",
    "outcome": "succeeded",
    "phase": "verify",
    "components": [
        {
            "name": component["name"],
            "disposition": component["disposition"],
            "requested_commit": component["commit"],
            "observed_commit": component["commit"] if component["disposition"] == "deployed" else None,
        }
        for component in manifest["components"]
    ],
    "artifact": {"architecture": image["architecture"], "expected_sha256": image["sha256"], "observed_sha256": image["sha256"]},
    "actions": [],
    "configuration_handoff": {
        "state": "configuration-pending",
        "contract_version": 1,
        "guidance": manifest["configuration"]["guidance"],
        "components": [
            {"name": component["name"], "repository": component["repository"], "commit": component["commit"]}
            for component in manifest["components"] if component["disposition"] == "configuration-owned"
        ],
    },
    "failed_step": None,
    "diagnostic": "fixture deployment evidence",
    "log_path": "/tmp/mkchad-v1/container-tools-runtime-boundary/fixture.log",
}
module.validate_result_v2_document(result)
pathlib.Path(sys.argv[3]).write_text(json.dumps(result), encoding="utf-8")
assert "packages" not in result
PY

  if python3 "$root/deploy/mkchad_release.py" --release fixture --image /tmp/image.sif --container-tools-host-package /tmp/package.tar.gz >/dev/null 2>&1; then
    return 1
  fi
  if python3 "$root/deploy/mkchad_develop.py" --image /tmp/image.sif --container-tools-source /tmp/source >/dev/null 2>&1; then
    return 1
  fi
  rm -rf -- "$work"
  trap - RETURN
  printf '%s\n' 'container-tools release source-selection tests passed'
}

mode=${1:---self-test}
[[ $# -le 1 ]] || { usage >&2; exit 2; }
case $mode in
  --self-test) self_test ;;
  --native) unavailable 'native image evidence is owned by the image build gate' ;;
  --help|-h) usage ;;
  *) usage >&2; exit 2 ;;
esac
