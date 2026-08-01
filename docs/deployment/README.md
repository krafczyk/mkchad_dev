# MkChad Release Deployment

`mkchad_release_bootstrap.py` is separately provisioned with a SHA-256 file in
a current-user-owned, mode-`0700` trust root. It snapshots exactly one
`mkchad-release` allowed signer and the revocation file, fetches one annotated
SSH-signed tag, checks out its peeled commit privately, and executes only the
deployer from that checkout. An explicit full SHA development invocation is
recorded as `development-sha`; branches and caller-selected candidate scripts
are never release authority.

The authenticated candidate exposes these commands:

```text
python3 deploy/mkchad_deploy.py validate-release --image IMAGE --trust-root DIR
python3 deploy/mkchad_deploy.py deploy --verified-release EVIDENCE --image IMAGE
python3 ~/.local/libexec/mkchad-release-bootstrap recover --deployment-id UUID --mode complete|restore --trust-root DIR --state-root DIR [--confirm-processes-quiesced]
```

No delegated cgroup or service-manager scope is required. Every Git, runtime,
and installer child starts in a new POSIX session with closed standard input,
minimal environment, separate 1 MiB stdout/stderr bounds, and a fixed timeout.
The supervisor retains an unreaped leader as its PID/process-group anchor,
revalidates owner, start time, session, group, and zombie state through procfs,
and scans current-user members before reaping. Failures send TERM for five
seconds then KILL for five seconds. A group still live at the final deadline
leaves the journal `in_progress` with cleanup-unconfirmed evidence.

`validate-release` fetches every required gitlink into private staging, verifies
the exact installer action contract, checks the MkChad `opencode.nvim` pin,
copies the architecture-specific raw image with a checksum sidecar, and compares
its embedded component manifest and OpenCode release tag. It never changes a
deployment destination.

`deploy` derives child commits only from parent and nested gitlinks. It
preflights every destination before the first final-path mutation, acquires the
legacy first-upgrade fence before the shared deployment lock, proves the managed
service inactive, and then runs in a detached supervisor. The supervisor owns
locks, journal updates, and authenticated recovery inputs even if the
user-facing controller exits. Unknown pre-interlock MkChad revisions require the
explicit `--maintenance-quiesced` operator assertion.

Before its first candidate-controlled subprocess, deployment persists a minimal
authenticated journal and places private staging inside that journal's recovery
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
only its UUID through the installed bootstrap, revalidates the bootstrap digest,
the exact retained bundle inventory, signed-tag or development evidence, parent
and child Git bundles and every resource's old/intended state
before changing anything. `complete` and `restore` use no network. Unknown or
later-edited bytes remain untouched and the recovery bundle is retained.

Receipts include trust, component, action, artifact, recovery, and
configuration-handoff identities. Raw subprocess diagnostics, configuration
contents, credentials, and secret-bearing values are never stored.
Machine-observed process-group cleanup and an operator's explicit quiescence
assertion are recorded separately. Recovery never signals journal PIDs. It
requires `--confirm-processes-quiesced` only for an `in_progress` record and
rejects that flag for finalized `failed` records. A pre-mutation interruption
accepts only `restore`, which removes authenticated private staging without
changing final deployment paths. Descendants that escape a session/process group
remain an accepted residual and require operator inspection.

For that inspection, use the journal's redacted action kind, executable identity,
and start window to check same-user host and scheduler/session process evidence.
Terminate or await matching work before confirming quiescence. Journal PIDs are
hints only, not signaling authority. If the host cannot provide enough evidence,
leave the record blocked. Records containing legacy cgroup fields must be
recovered with the prior implementation and are never reinterpreted.
