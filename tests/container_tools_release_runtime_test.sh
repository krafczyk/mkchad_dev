#!/usr/bin/env bash
# Release-level binding gate for the finalized container-tools runtime evidence.
# Fixture evidence validates only the release admission boundary, never native parity or operability.
set -euo pipefail
umask 077

readonly evidence_schema='container-tools.runtime-evidence/v1'
readonly binding_schema='mkchad.container-tools-release-evidence/v1'
readonly final_parent_commit='c954465f9ce10b0552686ee811d3fec48993c80f'
readonly final_child_commit='22c803fb981a61db4cdeb5310df0301f238458ba'
readonly work_root='/tmp/mkchad-v1/container-tools-release-runtime'

script_dir=$(dirname "$(realpath "$0")")
root=$(realpath "$script_dir/..")
parent_root="$root/msk_containers"
child_root="$parent_root/container_tools"
selected_root_runner="$parent_root/tests/container_tools_selected_root_runtime_test.sh"

usage() {
  printf '%s\n' 'usage: container_tools_release_runtime_test.sh --self-test'
  printf '%s\n' '       container_tools_release_runtime_test.sh --validate-report REPORT --manifest MANIFEST --package ARCHIVE --image-metadata JSON --root-commit COMMIT'
  printf '%s\n' '       container_tools_release_runtime_test.sh --native'
}

unavailable() {
  printf 'container-tools release runtime evidence unavailable: %s\n' "$1" >&2
  return 77
}

sha256() {
  sha256sum -- "$1" | cut -d ' ' -f1
}

current_commits_match() {
  [[ $(git -C "$parent_root" rev-parse HEAD) == "$final_parent_commit" ]] || return 1
  [[ $(git -C "$parent_root" rev-parse HEAD:container_tools) == "$final_child_commit" ]] || return 1
  [[ $(git -C "$child_root" rev-parse HEAD) == "$final_child_commit" ]] || return 1
}

manifest_identities_match() {
  local manifest=$1

  jq -e --arg parent "$final_parent_commit" --arg child "$final_child_commit" '
    .schema == 2 and
    ([.components[] | select(.name == "msk_containers")] | length == 1) and
    ([.components[] | select(.name == "msk_containers")][0].commit == $parent) and
    ([.components[] | select(.name == "container_tools")] | length == 1) and
    ([.components[] | select(.name == "container_tools")][0].commit == $child) and
    ([.artifacts["container-tools-package"][] | select(.role == "host")] | length == 1) and
    ([.artifacts["container-tools-package"][] | select(.role == "container")] | length == 1) and
    ([.artifacts["container-tools-package"][] | .source_commit] | all(. == $child)) and
    ([.artifacts["container-tools-package"][] | .product_major] | unique | length == 1)
  ' "$manifest" >/dev/null
}

release_inputs_match() {
  local manifest=$1 package=$2 image_metadata=$3 root_commit=$4
  local package_digest image_digest

  [[ $root_commit =~ ^[0-9a-f]{40}$ && $(git -C "$root" rev-parse HEAD) == "$root_commit" ]] || return 2
  current_commits_match || { unavailable 'the selected parent or nested child commit is not finalized'; return $?; }
  manifest_identities_match "$manifest" || { unavailable 'the release manifest does not bind the finalized parent and package source commits'; return $?; }
  [[ -f $package && -f $image_metadata ]] || { unavailable 'the exact package archive or image metadata candidate is absent'; return $?; }

  package_digest=$(sha256 "$package")
  image_digest=$(sha256 "$image_metadata")
  jq -e --arg package_digest "$package_digest" --arg image_digest "$image_digest" '
    ([.artifacts["container-tools-package"][] | select(.role == "host")][0]) as $host |
    ([.artifacts["container-tools-package"][] | select(.role == "container")][0]) as $container |
    ([.artifacts["nvim-image"][] | select(.architecture == "x86_64")][0]) as $image |
    $host.sha256 == $package_digest and $container.sha256 == $package_digest and
    $image.sha256 == $image_digest
  ' "$manifest" >/dev/null || { unavailable 'the package or image digest does not match the release manifest'; return $?; }

  tar -xOzf "$package" container-tools-package.json |
    jq -e --slurpfile manifest "$manifest" '
      . == ([ $manifest[0].artifacts["container-tools-package"][] | select(.role == "host") ][0] | del(.sha256))
    ' >/dev/null || return 1
  jq -e --slurpfile manifest "$manifest" '
    . == ([ $manifest[0].artifacts["container-tools-package"][] | select(.role == "container") ][0])
  ' "$image_metadata" >/dev/null || return 1
}

validate_binding() {
  local binding=$1 report=$2 manifest=$3 package=$4 image_metadata=$5 root_commit=$6
  local report_digest manifest_digest package_digest image_digest

  release_inputs_match "$manifest" "$package" "$image_metadata" "$root_commit" || return $?
  report_digest=$(sha256 "$report")
  manifest_digest=$(sha256 "$manifest")
  package_digest=$(sha256 "$package")
  image_digest=$(sha256 "$image_metadata")
  jq -e --arg schema "$binding_schema" --arg root "$root_commit" --arg parent "$final_parent_commit" \
    --arg child "$final_child_commit" --arg report "$report_digest" --arg manifest "$manifest_digest" \
    --arg package "$package_digest" --arg image "$image_digest" '
      type == "object" and
      (keys | sort) == ["child_commit", "evidence_kind", "image_sha256", "manifest_sha256", "package_sha256", "parent_commit", "report_sha256", "result_schema", "root_commit", "schema"] and
      .schema == $schema and .evidence_kind == "fixture" and .root_commit == $root and
      .parent_commit == $parent and .child_commit == $child and .report_sha256 == $report and
      .manifest_sha256 == $manifest and .package_sha256 == $package and .image_sha256 == $image and
      .result_schema == 2 and ((tostring | test("(?i)(token|secret|password|authorization|/private/)")) | not)
    ' "$binding" >/dev/null
}

validate_report() {
  local report=$1 manifest=$2 package=$3 image_metadata=$4 root_commit=$5
  local package_digest image_digest work host_release container_release

  release_inputs_match "$manifest" "$package" "$image_metadata" "$root_commit" || return $?
  package_digest=$(sha256 "$package")
  image_digest=$(sha256 "$image_metadata")
  mkdir -p "$work_root"
  work=$(mktemp -d "$work_root/validate.XXXXXX")
  host_release="$work/host-release.json"
  container_release="$work/container-release.json"
  trap 'rm -rf -- "$work"' RETURN
  jq '[.artifacts["container-tools-package"][] | select(.role == "host")][0] | {digest:.sha256, product_major, source_commit}' "$manifest" > "$host_release"
  jq '[.artifacts["container-tools-package"][] | select(.role == "container")][0] | {digest:.sha256, product_major, source_commit}' "$manifest" > "$container_release"
  bash "$selected_root_runner" --validate-report "$report" --host-release "$host_release" \
    --container-release "$container_release" --image-digest "$image_digest"
  jq -e --arg schema "$evidence_schema" --arg package "$package_digest" --arg image "$image_digest" \
    --arg child "$final_child_commit" '
      .schema == $schema and .evidence_kind == "fixture" and .overall == "passed" and
      .source_commit == $child and .image == {digest:$image, id:("sha256:" + $image), role:"fixture"} and
      .package.archive_digest == $package and .operations == {outer_launch:1, manifest_publish:1, nested_dispatch:1, payload:1} and
      .cleanup == {status:"exact", owned_residue:0} and
      ((tostring | test("(?i)(token|secret|password|authorization|/private/)")) | not)
    ' "$report" >/dev/null
}

validate_result() {
  local result=$1 manifest=$2 image_metadata=$3
  local image_digest

  image_digest=$(sha256 "$image_metadata")
  jq -e --slurpfile manifest "$manifest" --arg image "$image_digest" '
    .schema == 2 and .artifact == {architecture:"x86_64", expected_sha256:$image, observed_sha256:$image} and
    .packages.expected == $manifest[0].artifacts["container-tools-package"] and
    .packages.observed == $manifest[0].artifacts["container-tools-package"]
  ' "$result" >/dev/null
  python3 - "$root/deploy/mkchad_deploy.py" "$result" <<'PY'
import importlib.util
import json
import pathlib
import sys

spec = importlib.util.spec_from_file_location("mkchad_deploy", sys.argv[1])
module = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(module)
module.validate_result_v1_document(json.loads(pathlib.Path(sys.argv[2]).read_text(encoding="utf-8")))
PY
}

make_fixture_archive() {
  local archive=$1 metadata=$2

  python3 - "$archive" "$metadata" <<'PY'
import gzip
import io
import pathlib
import tarfile
import sys

archive = pathlib.Path(sys.argv[1])
payload = pathlib.Path(sys.argv[2]).read_bytes()
with archive.open("wb") as raw:
    with gzip.GzipFile(fileobj=raw, mode="wb", mtime=0) as compressed:
        with tarfile.open(fileobj=compressed, mode="w", format=tarfile.PAX_FORMAT) as package:
            member = tarfile.TarInfo("container-tools-package.json")
            member.mode = 0o644
            member.mtime = 0
            member.size = len(payload)
            package.addfile(member, io.BytesIO(payload))
PY
}

self_test() {
  local work root_commit package metadata manifest image report binding result mutated source_manifest

  command -v jq >/dev/null 2>&1 || { unavailable 'jq is required for deterministic evidence validation'; return $?; }
  [[ -f $selected_root_runner ]] || { unavailable 'the finalized parent selected-root harness is missing'; return $?; }
  current_commits_match || { unavailable 'the selected parent or nested child commit is not finalized'; return $?; }
  mkdir -p "$work_root"
  work=$(mktemp -d "$work_root/self-test.XXXXXX")
  trap 'rm -rf -- "$work"' RETURN
  root_commit=$(git -C "$root" rev-parse HEAD)
  package="$work/container-tools-host.tar.gz"
  metadata="$work/container-tools-package.json"
  manifest="$work/release.json"
  image="$work/final-image-metadata.json"
  report="$work/runtime-report.json"
  binding="$work/release-binding.json"
  result="$work/result.json"
  mutated="$work/mutated.json"

  # Consume the parent harness first; it in turn consumes the child report validator.
  bash "$selected_root_runner" --self-test
  jq -n --arg child "$final_child_commit" '
    {role:"host", architecture:"x86_64", version:"1.0.0-fixture", product_major:1,
     source_commit:$child, libc:"musl", source_filename_class:"container-tools-package"}
  ' > "$metadata"
  make_fixture_archive "$package" "$metadata"
  jq --arg parent "$final_parent_commit" --arg child "$final_child_commit" --arg package "$(sha256 "$package")" '
    .components |= map(if .name == "msk_containers" then .commit = $parent elif .name == "container_tools" then .commit = $child else . end) |
    .artifacts["container-tools-package"] = [
      {role:"host", architecture:"x86_64", version:"1.0.0-fixture", product_major:1, source_commit:$child, sha256:$package, libc:"musl", source_filename_class:"container-tools-package"},
      {role:"container", architecture:"x86_64", version:"1.0.0-fixture", product_major:1, source_commit:$child, sha256:$package, libc:"musl", source_filename_class:"container-tools-package"}
    ] |
    .artifacts["nvim-image"] = [{architecture:"x86_64", sha256:("0" * 64), source_filename_class:"raw-nvim-container"}]
  ' "$root/deploy/release.json" > "$manifest"
  jq '[.artifacts["container-tools-package"][] | select(.role == "container")][0]' "$manifest" > "$image"
  jq --arg image "$(sha256 "$image")" '.artifacts["nvim-image"][0].sha256 = $image' "$manifest" > "$mutated"
  mv "$mutated" "$manifest"
  source_manifest=$(bash "$child_root/tests/host_projection_runtime_test.sh" --source-manifest)
  jq -n --arg schema "$evidence_schema" --arg child "$final_child_commit" --arg source_manifest "$source_manifest" \
    --arg package "$(sha256 "$package")" --arg image "$(sha256 "$image")" --arg manifest "$(sha256 "$manifest")" '
    {schema:$schema, evidence_kind:"fixture", overall:"passed", backend:"docker", architecture:"x86_64",
     source_commit:$child, source_manifest:$source_manifest,
     image:{digest:$image, id:("sha256:" + $image), role:"fixture"},
     runtime:{executable_digest:("b" * 64), version:"fixture"},
     package:{archive_digest:$package, host:{digest:$package, product_major:1, source_commit:$child}, container:{digest:$package, product_major:1, source_commit:$child}},
     baseline:{status:"not-applicable", report_digest:"unavailable", image_digest:"unavailable"}, manifest_digest:$manifest,
     operations:{outer_launch:1, manifest_publish:1, nested_dispatch:1, payload:1},
     semantic_outcomes:{outer:"pass", manifest:"pass", nested:"pass", persistent:"pass", selected_root:"pass", status:"pass"},
     selected_root:{default:"pass", alternate:"pass"}, signals:{payload_exit:0, signal:"none"}, cleanup:{status:"exact", owned_residue:0}}
  ' > "$report"
  jq -n --arg schema "$binding_schema" --arg root "$root_commit" --arg parent "$final_parent_commit" \
    --arg child "$final_child_commit" --arg report "$(sha256 "$report")" --arg manifest "$(sha256 "$manifest")" \
    --arg package "$(sha256 "$package")" --arg image "$(sha256 "$image")" '
    {schema:$schema, evidence_kind:"fixture", root_commit:$root, parent_commit:$parent, child_commit:$child,
     report_sha256:$report, manifest_sha256:$manifest, package_sha256:$package, image_sha256:$image, result_schema:2}
  ' > "$binding"
  jq -n --slurpfile manifest "$manifest" --arg image "$(sha256 "$image")" '
    {schema:2, run_id:"123e4567-e89b-12d3-a456-426614174000", release:"fixture", started_at:"2026-08-08T00:00:00Z", completed_at:"2026-08-08T00:00:01Z", outcome:"succeeded", phase:"verify",
     components:[$manifest[0].components[] | {name, disposition, requested_commit:.commit, observed_commit:(if .disposition == "deployed" then .commit else null end)}],
     artifact:{architecture:"x86_64", expected_sha256:$image, observed_sha256:$image}, packages:{expected:$manifest[0].artifacts["container-tools-package"], observed:$manifest[0].artifacts["container-tools-package"]}, actions:[],
     configuration_handoff:{state:"configuration-pending", contract_version:1, guidance:$manifest[0].configuration.guidance, components:[$manifest[0].components[] | select(.disposition == "configuration-owned") | {name, repository, commit}]},
     failed_step:null, diagnostic:"fixture deployment evidence", log_path:"/tmp/mkchad-v1/container-tools-release-runtime/fixture.log"}
  ' > "$result"

  validate_report "$report" "$manifest" "$package" "$image" "$root_commit"
  validate_binding "$binding" "$report" "$manifest" "$package" "$image" "$root_commit"
  validate_result "$result" "$manifest" "$image"
  jq '.operations.nested_dispatch = 2' "$report" > "$mutated"
  if validate_report "$mutated" "$manifest" "$package" "$image" "$root_commit"; then return 1; fi
  jq '.leak = "/private/fixture-secret"' "$report" > "$mutated"
  if validate_report "$mutated" "$manifest" "$package" "$image" "$root_commit"; then return 1; fi
  jq '.cleanup.owned_residue = 1' "$report" > "$mutated"
  if validate_report "$mutated" "$manifest" "$package" "$image" "$root_commit"; then return 1; fi
  jq '.root_commit = "0000000000000000000000000000000000000000"' "$binding" > "$mutated"
  if validate_binding "$mutated" "$report" "$manifest" "$package" "$image" "$root_commit"; then return 1; fi
  jq '.packages.observed[0].source_commit = "0000000000000000000000000000000000000000"' "$result" > "$mutated"
  if validate_result "$mutated" "$manifest" "$image" >/dev/null 2>&1; then return 1; fi
  if grep -Fq '/private/fixture-secret' "$report"; then return 1; fi

  set +e
  manifest_identities_match "$root/deploy/release.json"
  local checked_manifest_status=$?
  set -e
  [[ $checked_manifest_status != 0 ]] || return 1
  rm -rf -- "$work"
  trap - RETURN
  [[ ! -e $work ]] || return 1
  printf '%s\n' 'container-tools release runtime fixture tests passed; native release evidence remains unavailable without final immutable package and image candidates'
}

mode=''
report=''
manifest=''
package=''
image_metadata=''
root_commit=''
while (($#)); do
  case $1 in
    --self-test|--native) [[ -z $mode ]] || { usage >&2; exit 2; }; mode=${1#--}; shift ;;
    --validate-report) [[ -z $mode && $# -ge 2 ]] || { usage >&2; exit 2; }; mode='validate'; report=$2; shift 2 ;;
    --manifest|--package|--image-metadata|--root-commit)
      (($# >= 2)) || { usage >&2; exit 2; }
      case $1 in
        --manifest) manifest=$2 ;;
        --package) package=$2 ;;
        --image-metadata) image_metadata=$2 ;;
        --root-commit) root_commit=$2 ;;
      esac
      shift 2
      ;;
    --help|-h) usage; exit 0 ;;
    *) usage >&2; exit 2 ;;
  esac
done

case $mode in
  self-test) [[ -z $report && -z $manifest && -z $package && -z $image_metadata && -z $root_commit ]] || { usage >&2; exit 2; }; self_test ;;
  validate) [[ -n $report && -n $manifest && -n $package && -n $image_metadata && -n $root_commit ]] || { usage >&2; exit 2; }; validate_report "$report" "$manifest" "$package" "$image_metadata" "$root_commit" ;;
  native) [[ -z $report && -z $manifest && -z $package && -z $image_metadata && -z $root_commit ]] || { usage >&2; exit 2; }; unavailable 'no final immutable package and image candidates are bound by the checked-in release manifest' ;;
  *) usage >&2; exit 2 ;;
esac
