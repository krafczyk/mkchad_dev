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
actions, preserved paths, image digest, and configuration handoff pin. These
explicit pins are independent of parent or nested Git links; a manifest pin may
intentionally differ from a development checkout's gitlink.

Schema-1 manifests, bootstrap evidence, receipts, bundles, journals, and
recovery records are unsupported history. The schema-2 deployer neither reads,
migrates, writes, nor blocks on them.

## Developer Deployment

Run the repository-local developer command only from the canonical parent Git
top level:

```bash
./deploy/mkchad_develop.py [--image PATH] [--replace-partial]
```

It is a developer workflow, not a release. The source authority is the exact
clean committed `HEAD` of the checked-out `msk_containers`, its nested
`container_tools`, and `mkchad` repositories. The parent repository may be
dirty. Child origins may be forked or missing, and unpublished local commits
work without network access. Parent and nested gitlink revisions are not
authority. Any dirty, staged, untracked, unmerged, renamed, or type-changing
child bytes are rejected before target mutation.

The command stages those three local commits and then follows the regular
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
schema-1 release result remain unchanged.

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

An existing clean component checkout does not require the lifecycle launcher to
be preinstalled when no managed host tool or image exists yet. Deployment treats
that configuration-only state as inactive, installs the pinned host tools, and
then verifies the lifecycle and remaining release state normally.

An older deployment may have managed host tools or images but no lifecycle
launcher. After independently confirming that no MkChad/OpenCode service is
running, pass `--replace-partial` to assert that the service is inactive and
replace that legacy partial state through the normal verified installers:

```bash
mkchad-release --release TAG --image /absolute/path/to/image.sif --replace-partial
```

The option bypasses only the missing-lifecycle guard. A lifecycle launcher that
reports an active noncompliant service still blocks, as do dirty or wrong-origin
checkouts, unsafe installer outputs, and invalid image inputs.

If an action fails, prior successful changes remain. Repair the reported step
and rerun the same command; no rollback or recovery command exists.

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
