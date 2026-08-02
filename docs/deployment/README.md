# MkChad Release Deployment

`mkchad_release_bootstrap.py` is separately provisioned through the operator's
trusted channel. It validates its installed path, ancestors, owner, type, and
mode safety, fetches exactly one ordinary annotated tag without consulting any
signature, requires it to peel to the operator-supplied full parent SHA, then
fetches that exact remote tag again into a second private ref immediately before
candidate execution. Both tag
objects and peeled commits must agree. The bootstrap writes mode-`0600` closed
authority evidence and a digest-bound minimal parent bundle. An explicit full
SHA development invocation is recorded as `development-sha`; branches and
caller-selected candidate scripts are never release authority.

The authority-bound candidate exposes these commands:

```text
python3 ~/.local/libexec/mkchad-release-bootstrap [--trust-bootstrap-group-writable-ancestors] [--trust-group-writable-deployment-roots] --release TAG --expected-parent-commit FULL_SHA --image IMAGE
python3 ~/.local/libexec/mkchad-release-bootstrap [--trust-bootstrap-group-writable-ancestors] [--trust-group-writable-deployment-roots] --development-commit FULL_SHA --image IMAGE
python3 deploy/mkchad_deploy.py validate-release --release TAG --expected-parent-commit FULL_SHA --image IMAGE
python3 deploy/mkchad_deploy.py deploy --verified-release EVIDENCE --verified-parent-bundle BUNDLE --image IMAGE
python3 ~/.local/libexec/mkchad-release-bootstrap [--trust-bootstrap-group-writable-ancestors] [--trust-group-writable-deployment-roots] recover --deployment-id UUID --mode complete|restore --state-root DIR [--confirm-processes-quiesced]
```

`--state-root` names the canonical mode-`0700` deployment-record directory,
normally `~/.local/state/mkchad/deployments`.

The bootstrap rejects group- or world-writable path ancestors by default,
except for root-owned sticky shared roots such as `/tmp`. On a host where a
root- or current-user-owned bootstrap ancestor is necessarily group-writable,
the operator may pass `--trust-bootstrap-group-writable-ancestors` only when
every principal with group-class or ACL write access to those ancestors is
trusted to supply the deployment bootstrap.

`--trust-group-writable-deployment-roots` separately trusts group-class and ACL
writers on root- or current-user-owned named deployment and recovery roots. It
passes that decision to the verified candidate and retained recovery deployer.
Each option emits its own warning and changes only its named trust boundary.
Foreign ownership, ordinary world-write access, symlinks, non-directories, and
a group- or world-writable bootstrap file remain rejected. Deployment-created
directories and final component destinations remain current-user-owned. An
ACL-managed home that contains both the bootstrap and deployment roots requires
both options.

No delegated cgroup or service-manager scope is required. Every Git, runtime,
and installer child starts in a new POSIX session with closed standard input,
minimal environment, separate 1 MiB stdout/stderr bounds, and a fixed timeout.
The supervisor retains an unreaped leader as its PID/process-group anchor,
revalidates owner, start time, session, group, and zombie state through procfs,
and scans current-user members before reaping. Failures send TERM for five
seconds then KILL for five seconds. A group still live at the final deadline
leaves the journal `in_progress` with cleanup-unconfirmed evidence.

`validate-release` validates release-tag syntax and requires the candidate HEAD
to equal `--expected-parent-commit`, but does not require the planned tag to be
published. It fetches every required gitlink into private staging, verifies the
exact installer action contract, checks the MkChad `opencode.nvim` pin, copies
the architecture-specific raw image with a checksum sidecar, and compares its
embedded component manifest and OpenCode release tag. It never changes a
deployment destination.

`deploy` derives child commits only from parent and nested gitlinks. It
preflights every destination before the first final-path mutation, acquires the
legacy first-upgrade fence before the shared deployment lock, proves the managed
service inactive, and then runs in a detached supervisor. The supervisor owns
locks, journal updates, and integrity-bound recovery inputs even if the
user-facing controller exits. Unknown pre-interlock MkChad revisions require the
explicit `--maintenance-quiesced` operator assertion.

Before its first candidate-controlled supervised subprocess, deployment safely
reopens and validates the evidence and parent-bundle digest, exact candidate
HEAD, and closed authority. It then copies those exact bytes into its durable
recovery stub before placing private staging inside that journal's recovery
bundle. A pre-mutation interruption accepts only `restore`; recovery finalizes
the record before deleting the bundle and its staging tree.

The release manifest may declare only these ordered `msk_containers` actions:

1. `bin/install_nvim.sh --check`
2. `bin/install_nvim.sh --recovery-dir <recovery_dir>`
3. `nvim/bin/install_nvim_container --source-revision <component_revision> <staged_image>`

The deployer also installs the exact nested `container_tools` gitlink before
running those actions. It verifies final checkout revisions, host tools, active
image digest, image manifest, and stopped-service lock state before writing a
successful receipt.

A failed or in-progress journal blocks subsequent deployment. `recover` accepts
only its UUID through the installed bootstrap, revalidates the installed path,
the exact retained bundle inventory, digest-bound annotated-tag-plus-SHA or
development-SHA evidence, parent and child Git bundles, and every resource's old
or intended state before changing anything. `complete` and `restore` use no
network. Unknown or
later-edited bytes remain untouched and the recovery bundle is retained.

Receipts include closed authority, component, action, artifact, recovery, and
configuration-handoff identities. Raw subprocess diagnostics, configuration
contents, credentials, and secret-bearing values are never stored.
Follow [configuration contract v1](configuration-contract-v1.md) after a
successful component receipt. The independent configuration phase reviews the
gitlink-derived configuration-owned revisions, applies and verifies the correct
`config_files` host branch, and starts the managed service only after status
agrees with the selected release.
Machine-observed process-group cleanup and an operator's explicit quiescence
assertion are recorded separately. Recovery never signals journal PIDs. It
requires `--confirm-processes-quiesced` only for an `in_progress` record and
rejects that flag for finalized `failed` records. A pre-mutation interruption
accepts only `restore`, which removes integrity-bound private staging without
changing final deployment paths. Descendants that escape a session/process group
remain an accepted residual and require operator inspection.

For that inspection, use the journal's redacted action kind, executable identity,
and start window to check same-user host and scheduler/session process evidence.
Terminate or await matching work before confirming quiescence. Journal PIDs are
hints only, not signaling authority. If the host cannot provide enough evidence,
leave the record blocked. Records containing legacy cgroup fields must be
recovered with the prior implementation and are never reinterpreted.
