# MkChad Deployment

`mkchad-release` is the release deployment command. It supports one trusted
operator on a trusted host. Provision the release-owned launcher at
`~/.local/bin/mkchad-release` before deploying the first schema-2 release.

```bash
install -Dm755 deploy/mkchad_release.py ~/.local/bin/mkchad-release
```

```bash
mkchad-release --release TAG --image /absolute/path/to/nvim_container_x86.sif \
  --container-tools-host-package /absolute/path/to/container-tools-host.tar.gz
```

The caller-owned image may be stored directly under the managed container
directory. The deployer excludes that exact source path when detecting an
existing target, installs it under its canonical managed name, and removes the
original only after the active image digest verifies. Other managed images in
the directory still require lifecycle validation, and a failed installation
preserves the caller image for repair and rerun.

The launcher fetches exactly `TAG` from
`git@github.com:krafczyk/mkchad_dev.git`, checks out that release without
submodules, and invokes only the deployer in that checkout. Its only optional
override is `--replace-partial`; it accepts no remote override, parent SHA,
development, validation, supervisor, or recovery option.

## Release Authority

`deploy/release.json` is schema 2 and is the deployment authority. It records
every component repository, full commit, disposition, destination, ordered
actions, preserved paths, image digest, role-keyed `container-tools-package`
identities, and configuration handoff pin. Each identity has a role,
architecture, semantic version/product major, source commit, SHA-256, libc
family, and filename class. These
explicit pins are independent of parent or nested Git links; a manifest pin may
intentionally differ from a development checkout's gitlink.

Schema-1 manifests, bootstrap evidence, receipts, bundles, journals, and
recovery records are unsupported history. The schema-2 deployer neither reads,
migrates, writes, nor blocks on them.

The trusted deployment profile allows the exact `HOME` root used by host-tool
actions to be root- or current-user-owned and group-writable, as required by
ACL-managed HPC homes. World-write remains rejected, and `.local`, `.local/bin`,
installed tools, locks, and recovery state retain their stricter ownership and
mode checks. This fixed profile requires no public trust option.

## Developer Deployment

Run the repository-local developer command only from the canonical parent Git
top level:

```bash
./deploy/mkchad_develop.py [--image PATH] --container-tools-host-package PATH \
  --container-tools-host-sha256 SHA256 --container-tools-host-version VERSION \
  --container-tools-host-source-commit COMMIT \
  --container-tools-host-architecture ARCH --container-tools-host-libc musl|glibc \
  [--replace-partial]
```

It is a developer workflow, not a release. The source authority is the exact
clean committed `HEAD` of the checked-out `msk_containers`, its nested
`container_tools`, `mkchad`, and `opencode-db` repositories. The parent repository may be
dirty. Child origins may be forked or missing, and unpublished local commits
work without network access. Parent and nested gitlink revisions are not
authority. Any dirty, staged, untracked, unmerged, renamed, or type-changing
child bytes are rejected before target mutation. The supplied archive must hash
to the supplied identity and its source commit must equal the admitted pinned
`container_tools` child; the nested gitlink is never used as a fallback.

The command stages those four local commits and then follows the regular
deployment procedure. Destinations still require their canonical origins and
are protected when dirty. Checkouts converge exactly to the selected commits;
host-tool actions, lifecycle inactivity for required mutation, partial-target
handling, shared locks, logs, and result publication all remain in effect.
Failures stop at the first failed step while retaining earlier successful work.
Repair the reported issue and rerun the same command; `--replace-partial`
asserts inactivity only when replacing a target that has managed paths but no
lifecycle launcher.

`--image` is optional. When it is omitted, the command preserves active image
state without making a compatibility judgment and performs no image validation,
installation, activation, rotation, pruning, or image verification. When a
supplied image has the same digest as the valid active image, every image action
is skipped and the existing image topology is unchanged. When the active image
is different or absent, the supplied image receives intrinsic validation and
the regular installer path. An unsafe active selector blocks supplied-image
mutation. Only the active selected image qualifies for digest reuse; previous
or other inactive images do not. A developer caller-supplied SIF is always
preserved, including after a changed-image installation or failure.

Developer runs atomically publish a mode-tagged result at the shared
`latest-result.json` path with local workspace, selected-commit, action, image,
failure, and log evidence. That result is diagnostic evidence, not release
authority. The release manifest, release CLI and launcher handoff, and
schema-2 release result remain unchanged.

## Deployment Flow

Both commands perform complete preflight before mutating a final checkout:

1. They validate the selected release manifest or local commits, any selected
   image, action files, destinations, and lifecycle status.
2. It applies exact detached component checkouts and component-owned installer
   actions. Dirty, unexpected, or wrong-origin targets stop the run without
   reset, stash, merge, overwrite, or rollback.
3. It verifies exact checkout commits, host tools, and any image selected for
   installation. Release success then reports configuration pending; developer
   success reports only the selected local deployment state.

An inactive service is required for mutation. When every checkout, host tool,
and image already complies, an active service is allowed for a verify-only
rerun. The omitted apply actions are recorded as `skipped-compliant`.

`opencode-db` is a deployed child at
`${XDG_DATA_HOME:-$HOME/.local/share}/mkchad/components/opencode-db`. Its
child-owned installer provides `$HOME/.local/bin/opencode-db`; Python 3.11 or
newer is required. This managed launcher installation builds no wheel, contacts
no package index, and adds no dependency. The child repository's standalone
wheel and virtual-environment installation remains independently supported.

## Container Tools Packages

`container_tools` is one product, not a host-bridge component. Release and
developer deployment require one explicit host archive. The deployer privately
stages its exact bytes, reads `archive.json` from the versioned package root,
binds the outer archive digest as the closed host identity, and resolves action
arguments only from the selected release/developer identity; callers cannot
supply internal action tokens. The pinned package verifier then checks the full
archive before mutation. A supplied or reused image must expose the separately
verified container identity at `/etc/mkchad/container-tools-package.json`.
Both metadata documents must exactly match their selected identities.

Host and in-container package identities may use different exact versions and
source commits only when their `product_major` values match. A cross-major pair
is rejected during preflight, before checkouts, host tools, or images mutate.
An architecture without both closed role entries is unavailable rather than
falling back to another archive.

The ordered package actions are `container-tools-package-preflight`,
`container-tools-package-install`, and `container-tools-package-verify`.
Preflight runs before host-tool and image mutation, package install precedes
host-tool installation, and package verification completes before terminal
success. Host-tool actions receive the archive, SHA-256, version, source commit,
architecture, libc, and private recovery directory as a fixed
argv. Generated compatibility scripts remain package-owned and are verified as
one configured package identity rather than compared with source templates. The
image installer receives the corresponding in-container identity. The package
installer records interrupted replacement state only in a private recovery
directory; repair the reported condition and rerun with the same archive
identity. Terminal schema-2 results report expected and observed host/container
identities without archive contents, environment values, or prefix inventories.

An existing clean component checkout does not require the lifecycle launcher to
be preinstalled when no managed host tool or image exists yet. Deployment treats
that configuration-only state as inactive, installs the pinned host tools, and
then verifies the lifecycle and remaining release state normally.

An older deployment may have managed host tools or images but no lifecycle
launcher. After independently confirming that no MkChad/OpenCode service is
running, pass `--replace-partial` to assert that the service is inactive and
replace that legacy partial state through the normal verified installers:

```bash
mkchad-release --release TAG --image /absolute/path/to/image.sif \
  --container-tools-host-package /absolute/path/to/container-tools-host.tar.gz \
  --replace-partial
```

The option bypasses only the missing-lifecycle guard. A lifecycle launcher that
reports an active noncompliant service still blocks, as do dirty or wrong-origin
checkouts, unsafe installer outputs, and invalid image inputs.

If an action fails, prior successful changes remain. Repair the reported step
and rerun the same command; no rollback or recovery command exists.

## Runtime Evidence

The workspace-level release binding gate consumes the finalized child
`container-tools.runtime-evidence/v1` validator and the parent selected-root
harness. Run its deterministic, credential-free fixture gate from the workspace
root:

```bash
bash tests/container_tools_release_runtime_test.sh --self-test
```

The fixture creates a temporary package archive, image metadata document,
runtime report, terminal schema-2 result, and root binding document beneath
`/tmp/mkchad-v1`. It verifies the selected workspace `HEAD`, finalized
`msk_containers` commit `cb834dbea46ded29ba812085902102dd79a5371f`, and nested
`container_tools` commit `0c806b4c3fafdc11d9ee16e66d4ba4805953241c`. It then
binds the release manifest's host and container identities to the archive
digest and metadata, the image digest and metadata, the report source and
manifest digests, schema-2 expected/observed package identities, exact cleanup,
redaction, and exactly one outer launch, manifest publication, nested dispatch,
and payload. The synthetic report proves this admission boundary only; it is not
Bash parity, image operability, or a native backend claim.

The checked-in release authority does not currently select final immutable
package and image candidates for those finalized commits. Consequently
`bash tests/container_tools_release_runtime_test.sh --native` returns `77` and
does not substitute prior package bytes, assign an image digest, or upgrade
fixture evidence to a runtime claim. Native parity remains unavailable without
the retained matching Bash baseline and unchanged parity image. Final-image
operability remains unavailable without a final immutable image, exact host and
in-container package identities, and a release-specific selected-root runner.
These are distinct claims; a final image can establish operability but never
historical Bash parity.

## Diagnostics And Configuration

The launcher streams Git and deployer output to the terminal and writes the
same redacted output to `${XDG_STATE_HOME:-$HOME/.local/state}/mkchad/deploy/`.
It keeps the current log and two previous logs. `latest-result.json` is
atomically replaced only by the admitted run's matching run ID and log path.
Partial-target diagnostics report the expected lifecycle command and every
pre-existing managed path that requires lifecycle validation.

The release result distinguishes deployed, embedded, configuration-owned, and
disabled components. A successful release result is `configuration-pending`:
deployment does not read, modify, or apply `config_files`, and it does not
activate the managed service. Continue with
[the configuration contract](configuration-contract-v1.md).
