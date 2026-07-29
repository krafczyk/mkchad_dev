# Audit Policy

## Purpose

This policy defines how implementation audits are performed and how findings
are prioritized for the single persistent OpenCode server sprint.

The primary remediation objective is to identify and resolve all P0 and P1
issues. Lower-priority findings should be recorded and addressed when they are
low risk to fix or directly support the sprint's acceptance criteria.

## Scope

Audits cover sprint-related changes in:

- `/data1/matthew/Projects/msk_containers`
- `/home/matthew/.config/mkchad`
- `/data1/matthew/Projects/opencode.nvim`

The audit should consider the combined behavior of all three repositories, not
only each repository in isolation.

The threat assumptions and deployment profiles in
`docs/sprints/single-opencode-server/2/threat_model.md` are
normative for security and availability findings.

## Deployment Profile Rules

### Hardened TLS Profile

For `"tls_proxy": true` (including an absent key), direct HTTP is a P0. Audits
must require the TLS proxy, distinct backend port, protected CA, pinned clients,
relay tuple/inode proof, and all process/fence/pidfd controls. Basic Auth remains
required on a multi-user host because the discoverable internal backend is
directly reachable and TLS does not authorize clients.

### Trusted-Host Direct Profile

For `"tls_proxy": false`, audits must verify that direct HTTP is presented only
as an explicit trusted-host opt-out. The protected mode-`0600` config selects
the mode; it is not a transport-security control. The owner must trust or accept
same-host plaintext, Basic Auth header, and replacement-listener capability. An
SSH tunnel does not restore identity after it terminates on the host, and Basic
Auth over direct HTTP does not encrypt the header or authenticate the server.

Direct mode must still satisfy loopback-only binding, strict config/state
transport validation, mode-mismatch refusal, exact backend identity/listener
proof before managed authenticated health, configured-port conflict refusal,
private files, credential non-disclosure, kernel fencing,
generation-conditional cleanup, pidfd-only signaling, and bounded recovery. The
trusted-host assumption excludes only direct plaintext and stale replacement
listener threats; it does not excuse another P0/P1 failure. Record a P0 if
direct mode is presented as multi-user hardening or used outside this profile.

Reevaluate before untrusted users/processes, packet-capture capability,
non-loopback exposure, or changed tunnel assumptions are added.

## Audit Principles

- Findings take priority over summaries or stylistic commentary.
- Findings must describe observable impact, not only code preference.
- Every finding should include a concrete file and line reference when possible.
- Every finding should include a plausible trigger or reproduction path.
- Severity is based on impact and likelihood in the documented deployment.
- Root-equivalent administrator capabilities are not application-fixable by
  themselves; findings should instead focus on unsafe behavior after selective
  process disruption.
- Audits must not recommend evading system administration controls.
- Audits must preserve laziness: ordinary MkChad startup and status inspection
  must not start OpenCode.
- Audits must treat pinned HTTPS health as liveness truth only after exact TLS
  proxy/backend identity, listener, CA, and same-connection controls validate.
  In the trusted-host direct profile, HTTP health is meaningful only after exact
  schema-3 direct backend identity and public-listener ownership validate;
  persistent state remains coordination metadata.
- Audits must account for shared homes, multiple hosts, multiple MkChad
  processes, PID reuse, and port races.
- Audits must not modify unrelated user changes.

## Priority Definitions

### P0: Critical

A P0 issue can cause catastrophic or broadly unsafe behavior under expected or
adversarial conditions.

Examples:

- Signaling or killing an unrelated process without reliable ownership checks.
- Disclosing provider credentials or server passwords in committed files,
  diagnostics, process arguments, or world-readable state.
- Corrupting or deleting unrelated user data.
- Executing attacker-controlled state as code or shell input.
- Entering an unbounded process-spawn or restart loop.
- Making ordinary MkChad startup consistently unusable across supported hosts.
- Connecting project commands to an untrusted endpoint while treating it as the
  managed server.
- In the hardened TLS profile, sending credentials, bodies, prompts, or SSE
  bytes over direct HTTP or before exact established-backend socket proof.
- Reconnecting an already accepted client stream to a replacement backend.
- Exposing the CA private key or keytool password through argv, state, logs, or
  permissions available to other users.

Policy:

- P0 findings block sprint completion.
- Begin remediation immediately.
- Add a regression test or explicit verification whenever feasible.
- Re-audit the fix and adjacent code paths before proceeding.
- Do not accept a P0 risk as a normal limitation.

### P1: High

A P1 issue can cause serious data exposure, persistent availability failure, or
incorrect cross-process behavior in a realistic deployment scenario.

Examples:

- Failure to recover after the managed server is selectively killed.
- A startup race that regularly creates multiple managed servers.
- Stale-lock handling that can block all future OpenCode use.
- Explicit `OPENCODE_PORT` silently falling back to another port.
- Adopting an unknown OpenCode endpoint without matching managed state.
- Reporting a dead server as healthy based only on cached state.
- Sending TUI commands to the wrong project directory.
- Failing to namespace process state by host on a shared home directory.
- Writing lifecycle state or logs with permissions readable by other users.
- A healthy server becoming dependent on the originating Neovim process because
  detachment was implemented incorrectly.
- A failed lifecycle hook preventing all future plugin actions without a bounded
  recovery path.
- A scoped configuration reload disposing the wrong directory, interrupting an
  active operation, or permanently disconnecting current-directory clients.

Policy:

- The team should resolve every P1 finding during the sprint.
- A P1 remains a release blocker unless it is proven unreachable in the scoped
  deployment or explicitly deferred with a documented owner, rationale, and
  bounded operational workaround.
- Re-audit the fix and its concurrency/failure paths.
- Add regression coverage or a repeatable manual verification procedure.

### P2: Medium

A P2 issue causes degraded behavior, confusing diagnostics, or a recoverable
failure that does not threaten unrelated processes or sensitive data.

Examples:

- `:OpenCodeInfo` omits useful but nonessential metadata.
- An error message lacks the server log path.
- TUI recreation requires a second user action after a rare race.
- A malformed optional state field produces a warning that is not actionable.
- The statusline takes longer than expected to clear after disconnect while live
  info remains correct.
- A bounded retry policy is unnecessarily slow but eventually succeeds or
  fails safely.

Policy:

- Record all P2 findings.
- Resolve P2 findings that are low-risk or necessary for acceptance criteria.
- Unresolved P2 findings require a concise residual-risk note.
- P2 findings do not automatically block sprint completion.

### P3: Low

A P3 issue is a minor maintainability, documentation, consistency, or user
experience concern with limited operational impact.

Examples:

- Inconsistent naming that does not change behavior.
- Missing comments around a non-obvious but safe branch.
- Minor duplicated code.
- Documentation wording or formatting issues.
- A diagnostic field could be ordered more clearly.

Policy:

- Record P3 findings when useful.
- Fix opportunistically.
- Do not delay P0/P1 work to address P3 findings.

## Required Audit Areas

### Process Safety

- Validate PID ownership before any signal.
- Require managed dispatch through a pidfd opened before final identity
  validation; reject any PID-number signal path, helper timeout, unsupported
  pidfd API, or post-open identity mismatch.
- Validate process generation before state cleanup.
- Verify negative or reused PIDs cannot target unrelated processes or groups.
- Verify stop escalation remains scoped to the managed process.
- Verify detached process handles and file descriptors are closed safely.
- Verify supported host lifecycle and MkChad launches use a profile-bound
  current-user runtime instance, concurrent first callers create one instance,
  and foreground containers cannot publish a detached generation.
- Validate proxy and backend independently by PID start time, boot ID, exact
  executable/argv, role semantics, and owned listener inode.
- Verify every schema-2/3 TLS stop/recovery path stops proxy first and never
  signals a live unverifiable process. For schema-4 TLS, verify that a
  complete authorized control request transfers process completion to the
  persistent proxy broker, which quiesces public admission, accept, and relays
  before its post-validation pidfd dispatch; direct mode is unchanged.
- Treat every live schema-1 PID as unverifiable regardless of current path/argv;
  never signal it during migration or explicit stop, and preserve its state with
  actionable trusted manual-accounting guidance.

### Concurrency

- Audit startup lock acquisition, ownership, expiry, and removal.
- Verify a persistent private kernel fence is held by the lifecycle Neovim
  process across logical lock create/reclaim/release and every shared mutation
  and signal dispatch. Lease expiry must not bypass a suspended holder.
- Audit concurrent first-use startup from separate Neovim processes.
- Audit state replacement while another process is reading.
- Audit recovery when the lock owner or state writer is killed.
- Verify every pending write renews current lock ownership, and that a stale
  reclaimed owner cannot overwrite, signal from, or remove newer pending data.
- Audit port bind races in automatic and explicit modes.
- Verify all callback and promise paths resolve exactly once.
- Verify all fence fds unlock/close on normal completion, refusal, timeout,
  callback error, exception, and process death; repeat failure paths and check
  for fd growth.
- Inventory every wait-for-result subprocess reachable while the fence may be
  held. Require argv-only asynchronous execution, protected stdin where needed,
  independent 64 KiB stdout/stderr limits, a five-second per-child deadline
  capped by the remaining lifecycle deadline, TERM then 250 ms KILL escalation,
  child reaping, exactly-once completion, and complete pipe/timer/process-handle
  cleanup.
- Exercise hanging and SIGTERM-resistant OpenCode version, every keytool
  generation/export/request/sign/import/list phase, Java keystore validation,
  curl, and pidfd-helper paths. Require fence release, separate-contender
  progress, stable fd/process counts, and no partial state, pending, or
  certificate publication on timeout, overflow, start/nonzero/parse error, or
  callback exception.
- Verify Lua retains exclusive fenced shared-state mutation and control-path
  reconciliation, while only a committed schema-4 broker transition owns backend
  launch or stop completion. Direct and legacy paths must retain fence-through-
  signal behavior.
- Verify broker control load has fixed connection, frame, deadline, and queued
  work bounds; overload, partial frames, duplicate/unknown fields, trailing
  bytes, invalid UTF-8, peer loss, nonce/generation mismatch, and retries make
  no lifecycle mutation and cannot launch or signal twice.

### Liveness And Recovery

- Verify CA-pinned HTTPS `/global/health` is authoritative only after complete
  pair ownership/listener validation.
- Verify all retries and polls are bounded.
- Verify manager/editor exit leaves the runtime instance and its image mount
  usable for later broker-context pidfd stop. Verify server stop does not
  automatically stop the instance or race a new lifecycle caller.
- Verify server death clears stale connection presentation.
- Verify next-use recovery after server death.
- Verify TUI recreation after TUI death or generation change.
- Verify failure does not make ordinary MkChad startup unusable.
- Verify schema-2/3 proxy-only and backend-only death retain existing complete-
  pair recovery. For schema-4, exercise all four broker/backend outcomes:
  live/live requires broker plus endpoint proof; broker-live/backend-dead is
  locked broker cleanup only; broker-dead/backend-live is blocked/manual; and
  both-dead is fenced control-path reconciliation only.

### State Integrity

- Verify hostname namespacing.
- Verify atomic state writes.
- Verify malformed, absent, stale, and future-schema state handling.
- Verify cleanup is conditional on matching generation.
- Verify state and logs use restrictive permissions.
- Verify no secrets are persisted.
- Verify schema 2 contains both process start identities, boot ID, stable CA
  path/certificate identity, distinct public/internal ports, versions, and logs.
- Verify incomplete startup uses private generation-specific pending metadata
  and cannot be mistaken for complete state.
- Verify pending cleanup renews lock ownership, re-reads valid metadata, checks
  the caller's generation before signaling, and conditionally unlinks only the
  same generation.
- Verify pending cleanup takes signal identities only from that fresh re-read,
  and that pending/state check-and-act operations never release the kernel fence
  between final logical validation and write/unlink.
- Verify schema 1 is labeled legacy and is never probed or returned as a plugin
  URL; only dead legacy state is removed automatically, live legacy state is
  preserved without signaling, and future state is not overwritten.
- Verify schema-4 TLS is rejected by current schema-3 readers without byte
  mutation, replacement, or signaling. Verify schema-4 pending/complete state
  records broker protocol, generation, control path, and control inode; direct
  HTTP remains schema 3.
- Verify every authority-bearing schema-4 root/socket operation uses no-follow
  type, current-user DAC, restrictive-mode, and stable device/inode checks. A
  stale path is reclaimed only under the fence after bounded contact and proof
  of absent recorded authority; live or ambiguous paths remain preserved.

### TLS, Relay, And Port Safety

- Verify public clients authenticate the exact host CA and never disable TLS
  verification.
- Verify CA key/store/password permissions and keytool `-storepass:file`; search
  argv/state/logs/notifications for password content.
- Verify CA and leaf identity remain stable across ordinary process recovery.
- Verify unknown endpoints are not adopted based only on health or a listener.
- Verify explicit port conflicts fail without fallback.
- Verify automatic fallback is bounded and persisted.
- Verify auth failures are distinguished from availability failures.
- Verify public proxy and internal backend bind only to loopback and use
  distinct ports.
- Verify every TLS connection sends only the fixed unauthenticated keep-alive
  health preflight before proof.
- Verify bounded parser rejection for malformed, oversized, chunked,
  transfer-encoded, close-delimited, EOF, and timeout responses.
- Verify exact reverse ESTABLISHED tuple uniqueness across tcp/tcp6 and socket
  inode ownership under the immutable expected backend PID.
- Verify no decrypted client HTTP byte is read/forwarded before proof and the
  same socket is used for pumping.
- Verify process/start/boot/inode ownership remains monitored during long-lived
  HTTP/SSE and ownership loss closes the stream.
- Verify no code reconnects an accepted client stream.
- Verify replacement listeners before proof and after backend death receive no
  Authorization/body/client bytes (at most the fixed preflight before proof).
- Verify unsupported platform evidence fails closed.
- Verify schema-4 broker startup binds and validates private control before it
  launches the backend and does not expose public TLS before backend/listener
  proof. On committed stop, require admission-gate closure, accept-loop exit,
  and an empty registered-relay set before backend pidfd signaling.
- Verify normal stop conditionally unlinks the recorded control inode after
  backend death, sends one matching receipt on the accepted stop connection,
  exits the broker, and permits caller cleanup only after fenced path-absence
  proof. Lost receipt, unlink failure, missing broker death, or replacement inode
  preserves state until both roles and the control path are safely reconciled.

### Directory Routing

- Verify every opencode.nvim request sends the current cwd header.
- Verify every attached TUI uses the corresponding `--dir`.
- Verify cwd changes recycle the local TUI.
- Verify distinct project directories do not receive each other's TUI commands.
- Record the same-directory multi-TUI limitation rather than presenting it as
  solved.

### Scoped Configuration Reload

- Verify `:OpenCodeReload` and `:Opencode reload` target only the absolute current
  Neovim directory.
- Verify inactive reload starts no server and creates no lifecycle state.
- Verify reload refuses while session work, permission requests, or questions
  are active, with no force/bang bypass.
- Verify the dispose request authenticates without credential exposure.
- Verify instance recreation validates routed `/path` before success.
- Verify the shared server PID, generation, URL, and port do not change.
- Verify another directory instance remains usable throughout reload.
- Verify opencode.nvim reconnects and only the invoking local TUI is recycled.
- Verify same-process duplicate and cross-process concurrent reload requests are
  bounded and converge safely.
- Verify errors identify the reload phase and directory without claiming that
  process-cached global configuration was refreshed.
- Verify reload curl supplies the state CA through protected stdin and preserves
  proxy PID, backend PID, generation, both ports, URL, and certificate identity.

### Laziness And Side Effects

- Verify MkChad startup starts no OpenCode process.
- Verify plugin load starts no OpenCode process.
- Verify `:OpenCodeInfo` starts no process and writes no lifecycle state.
- Verify statusline rendering has no lifecycle side effects.
- Verify first OpenCode use performs bounded preparation.

### Diagnostics

- Verify live info distinguishes HTTP health, PID state, plugin SSE, and local
  TUI state.
- Verify stale state is not reported as healthy.
- Verify errors identify URL, port, and log path where safe.
- Verify credentials and inherited environment values are not printed.
- Verify server/local version mismatch is reported accurately.
- Verify info labels schema 1 legacy and sends no request for legacy, malformed,
  future, missing, certificate-mismatched, or process-invalid state.
- Verify diagnostics report CA path/certificate identity and proxy/backend
  layers separately without printing key/password content.
- Verify schema-4 diagnostics distinguish control unavailable, stale/mismatched
  control state, broker identity loss, backend identity loss, unhealthy,
  stopping, and blocked/manual outcomes without exposing frames, credentials, or
  unbounded process data.

### Client Trust And Secret Transport

- Verify MkChad lifecycle/reload curl supplies `cacert`, auth, and bodies only
  through protected stdin configuration.
- Verify opencode.nvim resolves optional `server.ca_cert` for every REST and SSE
  request and keeps CA, credentials, and bodies out of argv.
- Verify request-time directory routing remains present with TLS/auth/body.
- Verify local attach receives `NODE_EXTRA_CA_CERTS` in its child environment,
  the HTTPS URL, and `--dir`, while preserving inherited auth behavior.
- Verify curl fails without CA and succeeds with CA.
- Verify browser documentation requires manual import and does not claim trust
  automation; browser runtime evidence must name the tested trust store/browser.
- Verify documentation and diagnostics state that TLS authenticates the server,
  not clients, and CA possession does not control access.
- Verify no-password operation warns that public and discoverable internal
  loopback endpoints are accessible to local users; verify configured Basic Auth
  returns `401` through both endpoints for invalid credentials.

### Regression And Compatibility

- Verify existing mappings and terminal positions.
- Verify SingularityCE and Apptainer behavior where environments are available.
- Verify exact active image and bootstrap identities select the runtime instance
  profile, stale profiles are not silently reused after rotation, and
  machine-mode stdout contains no instance-management output.
- Verify mounted npm overrides still take precedence.
- Verify x86_64 and aarch64 baseline updates.
- Verify ppc64le remains untouched.
- Verify unrelated worktree changes remain untouched.
- Verify Java source compiles for release 21 and requires no OpenSSL dependency.
- Verify bounded concurrent TLS clients and concurrent first-use startup.
- Require evidence for each T15/T16 control in the threat model: private
  state-root/socket DAC and inode checks, bounded strict control protocol,
  broker-first crash-cut recovery, broker/backend liveness matrix,
  public-quiesce-first stop, and receipt/lost-receipt cleanup. Missing evidence
  is a finding, not a substitute for direct or legacy-path evidence.
- Verify the MkChad pin includes both opencode.nvim CA support and the `e04b7a7`
  SSE fix before release; a local uncommitted diff is not deployment evidence.

## Audit Workflow

1. Read `docs/sprints/single-opencode-server/2/sprint_spec.md`.
2. Read `docs/sprints/single-opencode-server/2/sprint_checklist.md`.
3. Read `docs/sprints/single-opencode-server/2/threat_model.md`.
4. Inspect worktree status in every in-scope repository.
5. Identify the exact sprint diff in every repository.
6. Review behavior across repository boundaries.
7. Run static checks and focused tests.
8. Exercise process-kill, race, state-corruption, and port-conflict scenarios.
9. Exercise TLS trust failure, same-connection proof, replacement listener,
   long-lived SSE, and preflight-parser rejection scenarios.
10. Report findings ordered by P0, P1, P2, then P3.
11. Include file/line references and reproduction details.
12. Remediate P0/P1 findings before lower-priority cleanup.
13. Re-run affected checks after each remediation.
14. Perform a final re-audit of the combined diff.
15. Record unresolved risks and verification gaps.

## Finding Format

Use this structure:

```text
[P1] Short finding title
Location: path/to/file.lua:123
Impact: What can go wrong and why it matters.
Trigger: The concrete sequence or condition that exposes the issue.
Evidence: Relevant code behavior, test result, or log observation.
Remediation: The smallest safe change that addresses the root cause.
Verification: How to prove the remediation works.
```

Do not inflate severity because a root administrator could bypass the system.
Assign severity based on whether application behavior introduces additional harm
or fails its documented recovery contract under selective disruption.

## P0/P1 Resolution Standard

A P0 or P1 finding is resolved only when:

- The root cause is understood.
- The implementation is corrected or the affected behavior is removed.
- Relevant tests or repeatable verification steps pass.
- Adjacent failure and concurrency paths are reviewed.
- The combined cross-repository behavior remains consistent.
- The fix does not introduce an equal or higher-severity regression.
- Documentation and checklist entries are updated when behavior changes.

Closing a finding because the happy path works is insufficient. Process death,
stale state, concurrent startup, and PID reuse must be considered where relevant.

Auditor pass 11 closed `SPOS-AUD-P1-007` and `SPOS-AUD-P1-008` after
independently verifying kernel fencing, pidfd signaling, bounded subprocess
termination/reaping, contender progress, and adjacent lifecycle regressions.

## Exceptions

P0 exceptions are not permitted for sprint completion.

A P1 may be deferred only when all of the following are recorded:

- Why the issue cannot reasonably be resolved in the sprint.
- Why the affected path is unreachable or acceptably bounded in the scoped
  deployment.
- The operational workaround.
- The responsible owner.
- A follow-up issue or sprint item.
- The condition that will trigger re-evaluation.

The inability to defend against root is not itself a P1 exception. The audit
must distinguish impossible root resistance from fixable unsafe application
behavior.

### SPOS-AUD-P1-004: Local-User Access Without A Supplied Password

- **Status:** Policy gate accepted for this deployment scope by the
  decision-maker/user on 2026-07-14. The risk remains unresolved and
  conditional, was not code-fixed, and remains release-blocking anywhere this
  exception is not accepted. This is not a closure claim.
- **Acceptance:** The user explicitly acknowledged that untrusted same-host
  users can access no-password deployments and accepted that residual for this
  deployment scope, subject to the documented strong-password-before-first-use
  or stop/set/restart workaround.
- **Owner:** Matthew Krafczyk, MkChad maintainer.
- **Rationale:** The selected contract preserves OpenCode Basic Auth when the
  user supplies a password through MkChad's protected config or
  `OPENCODE_SERVER_PASSWORD`, but neither generates nor forces a password. TLS
  authenticates server identity only. Changing that contract would require
  credential generation/distribution and recovery design beyond the approved
  sprint behavior.
- **Scoped bound:** Both listeners remain loopback-only, so exposure is limited
  to users/processes on the same host. This does not make the data exposure
  acceptable by itself. TLS/CA authenticates server identity and is not client
  access control; CA possession does not control access.
- **Operational workaround:** Set a strong existing password in MkChad's
  protected mode-`0600` `opencode-server.json` or
  `OPENCODE_SERVER_PASSWORD` before first use. If a pair already runs, update
  the setting, run `:OpenCodeStop`, restart Neovim when the file changed, and
  start OpenCode again. Confirm invalid credentials receive `401` from both the
  public proxy and direct internal endpoint.
- **Follow-up:** `SPOS-FOLLOWUP-AUTH-001` must design and approve either a
  mandatory user-supplied credential gate or an equivalent per-user endpoint
  access control without inventing a password.
- **Reevaluation trigger:** Reevaluate before deployment on a host with
  untrusted local users, before either listener becomes non-loopback, when
  OpenCode authentication semantics change, when a per-user transport becomes
  available, or when OpenCode v2 architecture is selected.

### Optional Transport Policy Review

- **Status:** Reviewed against the accepted 2026-07-15 optional-transport
  decision before production implementation. This does not close a direct-HTTP
  risk in the hardened profile.
- **Hardened rule:** Direct HTTP remains a policy-reportable P0 for a multi-user
  hardened deployment. The TLS proxy, pinned CA, distinct backend port, and
  relay proof controls remain mandatory.
- **Trusted-host rule:** Direct HTTP is an explicit opt-out only when the owner
  accepts or trusts same-host plaintext, credential, and endpoint-replacement
  capability. Protected config chooses mode but does not secure the hop; Basic
  Auth is not encryption; and SSH tunneling does not restore host-local endpoint
  identity.
- **Mandatory direct controls:** Loopback-only binding, strict config and
  transport state, exact backend ownership before health, port-conflict refusal,
  private metadata, no secret disclosure, fence/generation/pidfd process safety,
  and bounded recovery remain audit requirements.
- **Reevaluation trigger:** Reassess before any untrusted user/process,
  packet-capture capability, non-loopback exposure, or changed tunneling model.

## Completion Gate

The audit passes when:

- No P0 findings remain.
- Every P1 is resolved, or an explicit exception meeting this policy is
  documented.
- Required acceptance criteria have verification evidence.
- Remaining P2/P3 findings and test gaps are recorded.
- No secrets or unrelated changes are present in the final diffs.
- The implementation still fails safely when one or more process-chain members
  are killed.
- Schema-4 evidence covers broker-first startup, control-path authority, every
  broker/backend liveness combination, public-quiesce-before-pidfd ordering, and
  receipt-gated or lost-receipt state reconciliation; direct HTTP and schema-2/3
  TLS retain their existing independent proof evidence.
- CA-authenticated TLS, protected client configuration, fixed preflight, exact
  tuple/inode/PID-start/boot proof, and no-reconnect behavior have repeatable
  evidence.
- Release evidence identifies the exact MkChad and opencode.nvim revisions;
  opencode.nvim CA support must be in the revision pinned by MkChad.
- Runtime/browser/container items are not marked complete from fixture-only
  evidence.

The preferred outcome is zero unresolved P0 and P1 findings.
