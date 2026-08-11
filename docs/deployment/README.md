# MkChad Deployment

`mkchad-release` is the release deployment command. It supports one trusted
operator on a trusted host. Provision the release-owned launcher at
`~/.local/bin/mkchad-release` before deploying the first schema-2 release.

```bash
install -Dm755 deploy/mkchad_release.py ~/.local/bin/mkchad-release
```

```bash
mkchad-release --release TAG --image /absolute/path/to/nvim_container_x86.sif
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
actions, preserved paths, image digest, and configuration handoff pin. The
`container_tools` component commit selects the host source independently of the
image's Dockerfile pin. These explicit pins are independent of parent or nested
Git links; a manifest pin may intentionally differ from a development
checkout's gitlink.

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
./deploy/mkchad_develop.py --image PATH [--replace-partial]
```

It is a developer workflow, not a release. The source authority is the exact
clean committed `HEAD` of the checked-out `msk_containers`, its nested
`container_tools`, `mkchad`, and `opencode-db` repositories. The parent
repository may be dirty. Child origins may be forked or missing, and unpublished
local commits work without network access. The admitted nested
`msk_containers/container_tools` checkout is the only developer host-package
source and must match the admitted nested gitlink. Any dirty, staged, untracked,
unmerged, renamed, or type-changing child bytes are rejected before target
mutation.

The command stages all admitted exact commits below its invocation-private
`run-*` directory in `/tmp/mkchad-v1/container-tools-runtime-boundary`, then
builds and installs the static executable from that staged source with normal
CMake commands while holding both the shared invocation lock and a fixed
build-root-global nonblocking lock. The latter spans stale-run reclamation,
staging, building, deployment, and result publication, so commands using
different XDG state roots cannot reclaim each other's active build directories.
When that build lock is busy, the command publishes a controlled developer
failure result in its own state root. X86_64 and AArch64 builds select
`musl-gcc`; PPC64LE selects GCC. The installed executable's JSON version must
report the admitted source commit, native architecture, product version, and
`ct-mount-plan-v1` grammar. Callers cannot select another source, archive,
identity, compiler family, or install prefix.
After it publishes a normal, controlled-failure, or interrupted terminal result,
the command immediately makes one best-effort cleanup attempt for its `run-*`
directory while still holding the build lock. Cleanup errors and interruption
are suppressed so they cannot replace that result. The next command invocation
reclaims only `run-*` residue left by an uncatchable interruption or crash. Build
and Git/runtime command output is captured concurrently, decoded as UTF-8 with
replacement, and limited to 1 MiB per channel; output beyond that limit fails
the run with a controlled diagnostic.

The command stages those four local commits and then follows the regular
deployment procedure. Destinations still require their canonical origins and
are protected when dirty. Checkouts converge exactly to the selected commits;
host-tool actions, lifecycle inactivity for required mutation, partial-target
handling, shared locks, logs, and result publication all remain in effect.
Failures stop at the first failed step while retaining earlier successful work.
Repair the reported issue and rerun the same command; `--replace-partial`
asserts inactivity only when replacing a target that has managed paths but no
lifecycle launcher.

`--image` is required. When its digest matches the valid active image, every
image action is skipped and the existing image topology is unchanged. When the
active image is different or absent, the supplied image receives intrinsic
validation and the regular installer path. An unsafe active selector blocks
supplied-image mutation. Only the active selected image qualifies for digest
reuse; previous or other inactive images do not. A developer caller-supplied
SIF is always preserved, including after a changed-image installation or
failure.

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

## Container Tools Host Selection

`container_tools` is one product, not a host-bridge component. Release
deployment checks out the release-pinned component commit; developer deployment
uses the admitted nested child. Both build and install that exact checkout with
CMake and verify compile-time JSON identity. Neither command accepts an archive
or source selector, reads package metadata from the image, compares host and
image versions, or records a container-package result role. The independently
pinned image remains digest-validated. Runtime compatibility is enforced when
an in-container host operation reads the mounted mount-plan grammar.

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
  --replace-partial
```

The option bypasses only the missing-lifecycle guard. A lifecycle launcher that
reports an active noncompliant service still blocks, as do dirty or wrong-origin
checkouts, unsafe installer outputs, and invalid image inputs.

If an action fails, prior successful changes remain. Repair the reported step
and rerun the same command; no rollback or recovery command exists.

## Runtime Evidence

The workspace-level release source-selection gate validates the reduced manifest
and terminal result contracts. Run its deterministic, credential-free fixture
gate from the workspace root:

```bash
bash tests/container_tools_release_runtime_test.sh --self-test
```

The fixture proves that release authority contains only image artifacts, selects
host source through the `container_tools` component commit, gives that component
no archive actions, emits no package result field, and rejects both removed CLI
selectors. Native image operability remains a separate image-build gate.

## Diagnostics And Configuration

The launcher streams Git and deployer output to the terminal and writes the
same redacted output to `${XDG_STATE_HOME:-$HOME/.local/state}/mkchad/deploy/`.
It keeps the current log and two previous logs. `latest-result.json` is
atomically replaced only by the admitted run's matching run ID and log path.
Every controlled failure uses `mkchad-release:`, `mkchad-develop:`, or
`mkchad-deploy:` followed by an operation, safe category, and repair action.
When a run log was created, the diagnostic includes `inspect log: PATH` for
that retained log; contention and pre-log state failures instead state the
prerequisite to repair or wait for. Child result rejection reports only a safe
category such as `missing`, `schema-invalid`, or a run, release, log, or status
binding mismatch. Exception text, command arguments, environment values, and
untrusted result contents are not published. Allowlisted operational details,
such as the affected component or detected managed paths, remain available when
they are needed to perform the repair. Partial-target diagnostics identify those
paths and direct the operator to confirm inactivity before using
`--replace-partial`.

The release result distinguishes deployed, embedded, configuration-owned, and
disabled components. A successful release result is `configuration-pending`:
deployment does not read, modify, or apply `config_files`, and it does not
activate the managed service. Continue with
[the configuration contract](configuration-contract-v1.md).
