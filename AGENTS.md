# MkChad Development Workspace

## Environment

This is the MkChad development environment on host `schumann`. Its workspace
root is `/data0/matthew/Projects/mkchad`, development short name is
`mkchad-v1`, and its disposable build and temporary-file root is
`/tmp/mkchad-v1`.

This repository coordinates independent child repositories. Read the nearest
child `AGENTS.md` before changing a child. Implement cross-repository work
serially, and run no more than one resource-intensive build or test suite at a
time. Child-specific tools, tests, and formatting commands belong in the child
repository that they apply to.

## Child Repositories

- `mkchad/`: active MkChad NvChad-based Neovim configuration.
- `msk_containers/`: active container images, launch scripts, and runtime
  integration.
- `msk_containers/container_tools/`: active reusable container execution and
  persistent-instance helpers.
- `opencode.nvim/`: active Neovim plugin connecting the editor to an OpenCode
  server.
- `opencode/`: active OpenCode fork for MkChad-specific TUI integration.
- `opencode-db/`: active standalone OpenCode SQLite cleanup tool.
- `opencode_sprint_loop/`: frozen historical reference for the former Python
  controller.
- `opencode_sprint_loop/opencode_sprint_loop.lua/`: frozen historical Neovim
  client for that controller.
- `opencode-project-reload/`: active standalone OpenCode TUI plugin that
  safely reloads one project instance.
- `compound-engineering/`: active Compound Engineering fork for cross-harness
  persona routing and shared global settings.
- `config_files/`: visible host-specific configuration repository, applied
  independently of release deployment.

## Cross-Repository Ownership

Each child has independent Git history. The coordinating Compound Engineering
agent owns cross-repository integration, authoritative verification, and the
combined change boundary. Complete and verify each changed child before its
parent records the updated gitlink; do not mix unrelated child changes.

## Tooling And Verification

Work in `compound-engineering/`, `opencode/`, and `opencode-project-reload/`
must use the provisioned Bun executable directly:

```bash
export PATH="/tmp/mkchad-v1/tools/bun-runtime/node_modules/.bin:$PATH"
bun test
bun run release:validate
```

If that executable is unavailable, check `bun` on `PATH` once. If neither is
available, report the missing prerequisite rather than downloading or
substituting a runtime. `plugin:validate` also requires the repository-pinned
`claude` CLI; its absence is an unavailable verification gate.

Use task-specific directories beneath `/tmp/mkchad-v1`. Repository instructions
define focused verification and any required local toolchain details.

## Protected Live Paths

Treat `~/.config/mkchad`, `~/.local/state/mkchad`,
`~/.local/share/mkchad`, and `~/.cache/mkchad` as live user state. They are
read-only unless the user explicitly authorizes a live-state change for the
current task. Do not use them as a fallback when an isolated checkout or test
fixture fails.

Do not inspect, expose, stage, or commit user credentials or session data,
including `~/.config/openai.token`, SSH material, provider tokens, and
OpenCode session data outside the task's explicit scope.

## Environment Threat Model

Protected assets are user work in every checkout, child Git history and
gitlinks, live MkChad configuration and runtime state, provider and server
credentials, prompts and session data, process identity, and reproducible build
artifacts. The user, explicitly invoked local development tools, and cooperative
same-user processes are trusted. Other agents may edit the shared worktree
concurrently. Other local users, package registries, GitHub, OpenCode servers,
downloaded artifacts, and data returned by external services are untrusted.

Supported environments include HPC systems and network-attached filesystems
such as NFS, Lustre, and GPFS. The filesystem service, cluster administrators,
and platform-provisioned project and state directories are trusted. Inode
identity, ownership presentation, permission enforcement, atomicity, and other
local-filesystem semantics may vary across hosts and mounts; do not assume ext4
semantics unless a child repository explicitly requires them.

Trust boundaries exist between child repositories, isolated checkouts and live
user paths, containers and the host, local tools and external services, and
managed clients and loopback services. Loopback is host-wide rather than
user-private. TLS authenticates a server but does not authorize clients; a
strong user-supplied OpenCode password remains necessary on a multi-user host.
Direct HTTP is acceptable only under an explicit trusted-host profile that
accepts same-host plaintext and endpoint-replacement capability.

In scope are accidental wrong-repository edits, concurrent worktree changes,
dirty or incorrect gitlinks, credential disclosure, malformed external input,
dependency or network failure, process death and PID reuse, stale runtime state,
port and startup races, bounded recovery, and unsafe cleanup or signaling.
Privileged host compromise, malicious administrators, deliberate repository or
filesystem forgery, and denial of service by an administrator are out of scope;
stop and ask the user if evidence suggests them.

## Environment Audit Calibration

Assess findings against the environment threat model and distinguish impact,
likelihood, confidence, priority, evidence, and disposition. P0 and P1 findings
block completion by default. P2 and P3 findings are non-blocking unless the user
promotes them. Record accepted or deferred findings honestly, and state the
verification needed for any unresolved finding.

A filesystem security finding must identify the protected asset, an untrusted
actor with a reachable write capability, the supported execution path, and the
concrete impact. Do not require inode pinning, `lstat`/`fstat` identity
comparisons, permission repair, recursive ownership checks, or symlink defenses
merely as defense in depth. Require them only for credentials, authority-bearing
configuration, cross-user-writable state, privilege transitions, or a
demonstrated time-of-check/time-of-use boundary under this threat model. Treat
concerns that depend on unsupported local-filesystem semantics as portability
requirements or residual risks rather than exploitable vulnerabilities.

When reviewing or changing a child component, calibrate required controls and
findings to that component's officially supported security disposition as
established by its governing instructions, documentation, and tests. Tests that
intentionally encode trust assumptions, deployment profiles, portability
tradeoffs, or accepted residual risks are evidence of the supported contract;
do not demand a stricter posture or more pedantic controls than that contract
requires unless an explicit requirement changes it or a reachable scenario
violates it. Extra checks whose only value is defense in depth beyond the
supported disposition are not findings. Do not weaken tests merely to silence a
finding. If implementation and the supported disposition conflict, fix the
implementation; if the disposition intentionally changes, update its
documentation and tests together.

Treat signaling an unrelated process, exposing credentials or private session
data, corrupting unrelated user work, connecting managed clients to an
untrusted endpoint, or using direct HTTP under the hardened multi-user profile
as P0. Treat realistic persistent availability failures, duplicate managed
services, stale-state adoption, cross-project routing, insecure state
permissions, and unbounded recovery as at least P1. Root-only capabilities do
not raise application findings by themselves.

No-password loopback deployments remain accessible to other local users. This
is an accepted residual only for an explicitly trusted deployment; use a strong
password before first use on a multi-user host and reevaluate before any
non-loopback exposure or authentication change. Audits of lifecycle behavior
must cover selective process death, PID reuse, malformed and future state,
concurrent startup, credential transport, bounded subprocesses, and recovery.

## Release Deployment Exception

`mkchad-release` and the explicit repository-root developer command
`./deploy/mkchad_develop.py` support one trusted operator on a trusted host.
For these deployment commands only, malicious same-user writers, hostile local
repository substitution, remote compromise, and adversarial filesystem races
are outside the supported threat model. This exception does not change runtime
server, credential, or cross-user network boundaries.
