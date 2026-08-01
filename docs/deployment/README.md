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
python3 ~/.local/libexec/mkchad-release-bootstrap recover --deployment-id UUID --mode complete|restore --trust-root DIR --state-root DIR
```

Set `MKCHAD_DEPLOY_CGROUP_ROOT` to a user-delegated cgroup-v2 parent before
validation, deployment, or recovery. Every mutating Git, runtime, and installer
subprocess is enrolled before exec. Missing delegation, an escaping descendant,
or a scope that remains populated blocks finalization.

`validate-release` fetches every required gitlink into private staging, verifies
the exact installer action contract, checks the MkChad `opencode.nvim` pin,
copies the architecture-specific raw image with a checksum sidecar, and compares
its embedded component manifest and OpenCode release tag. It never changes a
deployment destination.

`deploy` derives child commits only from parent and nested gitlinks. It
preflights every destination before the first final-path mutation, acquires the
legacy first-upgrade fence before the shared deployment lock, proves the managed
service inactive, and then runs in a detached supervisor. The supervisor owns
locks, cgroups, journal updates, and authenticated recovery inputs even if the
user-facing controller exits. Unknown pre-interlock MkChad revisions require the
explicit `--maintenance-quiesced` operator assertion.

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
and child Git bundles, cgroup emptiness, and every resource's old/intended state
before changing anything. `complete` and `restore` use no network. Unknown or
later-edited bytes remain untouched and the recovery bundle is retained.

Receipts include trust, component, action, artifact, recovery, and
configuration-handoff identities. Raw subprocess diagnostics, configuration
contents, credentials, and secret-bearing values are never stored.
