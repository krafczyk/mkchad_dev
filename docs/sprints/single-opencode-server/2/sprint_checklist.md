# Optional OpenCode Transport Sprint Checklist

## Decisions

- [x] Keep the Java TLS proxy enabled by default.
- [x] Add strict protected-config Boolean `tls_proxy`; absence means `true`.
- [x] Do not add an environment override for `tls_proxy`.
- [x] Permit `false` only as an explicit trusted-host direct HTTP profile.
- [x] Require explicit shared stop and Neovim restart for transport changes.
- [x] Refuse automatic replacement when requested and active transports differ.
- [x] Repair explicit stop for a dead proxy with a verified live backend.
- [x] Keep recovery next-use; do not add a watchdog.
- [x] Retain protected TLS material while direct mode is active.
- [x] Use schema 3 with an explicit transport discriminator.

## Sprint Preparation

- [x] Archive the superseded TLS-only specification and checklist under
      `docs/sprints/single-opencode-server/1/`.
- [x] Preserve active governing paths for the new sprint.
- [x] Record implementation baselines and the archived sprint location.
- [x] Read all in-scope `AGENTS.md` files before implementation.
- [x] Re-read the active sprint spec, threat model, and audit policy before each
       Builder/Auditor cycle.
- [x] Preserve MkChad's unrelated untracked `lazy-lock.json`.
- [x] Preserve the unrelated `msk_containers` JDT archive.
- [x] Use only `/tmp/opencode/**` for temporary fixtures.
- [x] Do not inspect or expose real credentials or unrelated session data.
- [x] Commit or push only with explicit user authorization.

## Policy Profiles

- [x] Complete and review this section before any production implementation.
- [x] Amend `docs/sprints/single-opencode-server/2/threat_model.md` with hardened
      TLS and trusted-host direct
       profiles.
- [x] Amend `docs/sprints/single-opencode-server/2/audit_policy.md` so direct
      HTTP remains P0 in the hardened
       multi-user profile and is an explicit opt-out in the trusted-host profile.
- [x] State that the protected config controls mode selection, not transport
       security.
- [x] State that SSH tunneling does not restore local endpoint identity.
- [x] State that Basic Auth remains required for multi-user TLS mode because the
       internal backend port is discoverable and directly reachable.
- [x] State that Basic Auth over direct HTTP is not transport encryption.
- [x] Enumerate the direct-mode threats excluded by trusted-host assumptions and
       the controls that remain mandatory.
- [x] Stop the sprint without implementation if the two-profile policy cannot be
       approved.
- [x] Add a reevaluation trigger if the trusted-host deployment gains untrusted
       users, packet-capture capability, non-loopback exposure, or different
       tunneling assumptions.

## Configuration

- [x] Add `tls_proxy` to the supported config keys.
- [x] Accept only JSON Boolean values.
- [x] Default to enabled when absent.
- [x] Reject string, number, null, array, and object values.
- [x] Preserve current-user ownership, regular-file, mode-`0600`, size, and
       bounded JSON validation.
- [x] Preserve environment precedence only for port, username, and password.
- [x] Keep config and password content out of errors and diagnostics.
- [x] Update `opencode-server.example.json` and MkChad README.
- [x] Document process-cached loading and required Neovim restart.

## Schema 3

- [x] Define `transport` as exactly `tls-proxy` or `loopback-http`.
- [x] Retain common top-level `hostname`, `generation`, `host`, `port`, `url`,
       `port_source`, `started_at`, `cwd`, `boot_id`, and `backend` fields.
- [x] Preserve all common backend PID/start/boot/argv/executable/listener fields.
- [x] Require HTTPS, proxy, distinct backend port, CA path, and certificate
       identity for TLS state.
- [x] Require HTTP, absent proxy/active-CA fields, and backend/public port
       equality for direct state.
- [x] Add transport to pending metadata.
- [x] Distinguish TLS backend-only pending state from complete direct pending
      state.
- [x] Publish a generation-specific launch intent before every process spawn and
      block later startup while its role lacks persistent signal authority.
- [x] Remove launch intent automatically only when exact matching pending
      generation, boot, role, port, and optional PID authority exists.
- [x] Keep pending writes/removals and cleanup generation-conditional under the
      kernel fence.
- [x] Keep all managed signals behind fresh identity validation and pidfds.
- [x] Accept existing valid schema-2 state only as TLS mode.
- [x] Reuse healthy schema-2 TLS state without unsafe mutation.
- [x] Publish all new generations as schema 3.
- [x] Preserve live schema-1 non-signaling behavior.
- [x] Ensure older code treats schema 3 as unsupported and non-mutable.
- [x] Document stop-before-downgrade requirements.

## Mode Agreement And Transitions

- [x] Compare trusted active transport with process-cached requested mode before
      reuse.
- [x] Reuse only a matching healthy generation.
- [x] Refuse opposite-mode ensure with explicit stop/restart guidance.
- [x] Never auto-replace a healthy opposite-mode generation.
- [x] Let `:OpenCodeStop` follow trusted state independent of requested mode.
- [x] Prevent stale opposite-mode Neovim instances from generation flapping.
- [x] Recycle local TUI when transport changes.
- [x] Preserve valid TLS files across disable/re-enable.
- [x] Validate/reuse or safely regenerate retained material only when TLS is
      re-enabled.

## Common Lifecycle

- [x] Preserve lazy startup and observational info.
- [x] Preserve the process-held kernel fence and renewable logical lock.
- [x] Preserve bounded subprocess execution, output caps, TERM/KILL escalation,
      reaping, and callback cleanup.
- [x] Preserve exact configured-port refusal and bounded automatic selection.
- [x] Preserve exact backend executable, argv, start, boot, and listener proof.
- [x] Publish complete state only after mode-appropriate health succeeds.
- [x] Preserve generation-specific pending cleanup and state removal.
- [x] Keep Neovim exit from stopping the shared service.

## TLS Mode

- [x] Preserve separate public and internal ports.
- [x] Preserve stable CA and leaf validation/generation.
- [x] Preserve Java runtime and source identity validation.
- [x] Preserve TLS handshake, fixed unauthenticated preflight, tuple/inode proof,
      complete proc scanning, and no-reconnect behavior.
- [x] Preserve backend-first launch and proof followed by proxy launch and proof.
- [x] Preserve pinned authenticated HTTPS health before state publication.
- [x] Preserve proxy-first explicit stop for a complete pair.
- [x] Keep every archived Java/TLS security test passing unchanged.

## Direct Mode

- [x] Launch only OpenCode on the selected public port.
- [x] Publish `http://127.0.0.1:<port>`.
- [x] Require `backend.port == state.port`.
- [x] Skip internal-port selection.
- [x] Skip Java and proxy-source discovery/validation.
- [x] Skip keytool and certificate validation/generation.
- [x] Create no new TLS material on a fresh direct-only deployment.
- [x] Prove the exact backend owns the public listener before health or state
       publication.
- [x] Reject helper-listener adoption and public-port replacement races.
- [x] Use HTTP health only for a fully validated direct pending generation.
- [x] Preserve Basic Auth when supplied without claiming transport encryption.
- [x] Fail exact configured-port conflicts without fallback.

## Death And Stop

- [x] Keep proxy/backend recovery next-use rather than watchdog-driven.
- [x] Prove proxy-only death leaves backend/state unchanged before an ensuring
      action.
- [x] Keep `:OpenCodeInfo` observational after proxy death.
- [x] Keep reload from starting an unhealthy service.
- [x] On ensure after proxy death, stop the verified backend and replace the full
      TLS generation.
- [x] Repair `:OpenCodeStop` to clean a verified surviving backend after proxy
      death without launching a replacement.
- [x] Never signal a surviving backend if its identity is unverifiable.
- [x] Preserve matching state unless all exact managed roles are confirmed dead.
- [x] Preserve bounded TERM, exit wait, KILL escalation, and final death check.
- [x] Preserve state on identity, helper, signal, timeout, escalation, or final
      death-confirmation failure.
- [x] Keep public-port reuse conditional on availability.
- [x] Keep valid CA identity stable across ordinary TLS recovery.
- [x] Recover a dead direct backend on the next ensuring action.
- [x] Never adopt a surviving TLS backend as direct mode.
- [x] Never reconnect a stale client stream to a replacement process.

## Clients

- [x] Return HTTPS and CA only for validated TLS state.
- [x] Return HTTP and nil CA only for validated direct state.
- [x] Inject `NODE_EXTRA_CA_CERTS` only in TLS mode.
- [x] Include transport in TUI reuse/recycle identity.
- [x] Preserve Basic Auth and body delivery through protected curl stdin.
- [x] Preserve request-time directory routing for REST and SSE.
- [x] Verify existing opencode.nvim HTTP/no-CA behavior.
- [x] Verify existing opencode.nvim HTTPS/pinned-CA behavior.
- [x] Avoid an opencode.nvim production change unless focused tests demonstrate
      a missing transport-neutral behavior.

## Reload And Diagnostics

- [x] Make reload invariants transport-aware.
- [x] Always preserve backend PID, generation, URL, port, and transport.
- [x] Require proxy PID and certificate identity only in TLS mode.
- [x] Report requested and active transport.
- [x] Report mode mismatch and exact stop/restart remediation.
- [x] Report active HTTP or HTTPS URL accurately.
- [x] Omit active proxy/CA claims in direct mode.
- [x] Label retained TLS material inactive while direct mode runs.
- [x] Send health only after transport-appropriate ownership/listener validation.

## Focused Tests

- [x] Add table-driven `tls_proxy` config parsing tests.
- [x] Add schema-2/schema-3 TLS/direct/pending validation matrices.
- [x] Add malformed cross-transport and future-schema cases.
- [x] Add direct startup without Java/keytool/certificate dependencies.
- [x] Add direct authenticated and unauthenticated HTTP tests.
- [x] Add exact and automatic port-conflict tests in both modes.
- [x] Add TLS-to-direct and direct-to-TLS explicit transition tests.
- [x] Add stale opposite-mode Neovim refusal tests.
- [x] Expand proxy-death tests for the no-watchdog interval, info, reload,
      ensuring recovery, port/CA stability, and explicit partial stop.
- [x] Test partial stop with TERM resistance, KILL escalation, helper failure,
      timeout, identity mismatch, and unconfirmed death; require state retention
      on every incomplete cleanup.
- [x] Add direct backend death and replacement-listener tests.
- [x] Add direct listener-adoption race tests.
- [x] Parameterize concurrent startup for both modes.
- [x] Parameterize reload and TUI tests for both modes.
- [x] Verify direct mode creates no TLS files from an empty fixture.
- [x] Verify retained TLS files survive direct mode unchanged.
- [x] Verify credentials/config content stay out of state, argv, logs,
      notifications, Git diffs, and test output.

## Regression Verification

- [x] Run the full MkChad serial lifecycle suite.
- [x] Run pending-generation, state-validation, lock-lease, fence-race, startup-
      race, executable-identity, proc-scanner, reload, and concurrent-startup
      suites.
- [x] Run bounded-subprocess timeout/overflow/reaping matrices.
- [x] Run pidfd signaling tests.
- [x] Run Java `--release 21 -Xlint:all -Werror`.
- [x] Run Java tuple, malformed proc-table, constrained-heap, concurrency, HTTP,
      SSE, replacement, and credential-forwarding tests.
- [x] Run opencode.nvim protected-curl, TLS, HTTP, and SSE tests.
- [x] Run Python compile checks.
- [x] Run `git diff --check` in every changed repository.
- [x] Run available Lua formatting/static checks or record unavailable tooling:
      `stylua` and `luacheck` are unavailable; LuaLS ran but the checkout's CI
      library configuration produced 844 existing unresolved-library warnings.

## Verification Evidence (2026-07-15)

- Final MkChad verification ran from the fresh isolated clone
  `/tmp/opencode/spos-final-verification/mkchad`; the live MkChad checkout
  remained read-only.
- Fresh `/tmp/opencode/**` state roots passed TLS and direct lifecycle, both-mode
  reload, both-mode concurrent startup, config, certificate, pending, schema
  matrix, lock, fence, startup-race, executable-identity, proc-scanner,
  bounded-subprocess, and pidfd suites.
- `javac --release 21 -Xlint:all -Werror`, Java tuple/large-table tests, the full
  TLS proxy integration, and opencode.nvim protected-curl/HTTP/TLS/SSE tests
  passed.
- Isolated runtime-equivalent fixtures passed dead-proxy client
  disconnect/no-watchdog/info/reload/recovery, free/occupied public-port
  recovery, topology-specific partial-stop failure matrices, Basic Auth,
  generated disclosure canaries, schema-3 baseline refusal, and full
  schema-3-to-schema-2 rollback. Audit policy keeps the corresponding intended-
  deployment runtime boxes open.
- opencode.nvim response errors are redacted in local commit `59ad0b8`; MkChad
  pins that exact revision, and a fresh pinned clone passed REST, SSE, and TLS
  integration.
- `git diff --check` passed in every changed repository. A fresh final Auditor
  reported zero P0 and zero P1 findings.
- No browser, SSH server, Docker, Podman, SingularityCE, or Apptainer executable
  is available in this environment. Interactive browser/SSH and container
  persistence checks remain deployment-only and are not claimed here.

## Documentation

- [x] Update MkChad README with both topology diagrams and security profiles.
- [x] Update config examples and stop/restart ordering.
- [x] Update threat model and audit policy before claiming implementation
       readiness or changing production code.
- [x] Keep archived TLS-only documents unchanged.
- [x] Distinguish archived TLS evidence from new direct-mode evidence.
- [x] Document direct-mode browser HTTP behavior and scheme-changing bookmarks.
- [x] Document killed-proxy immediate effects and exact recovery triggers.
- [x] Document that `:OpenCodeStop` affects all shared clients in both modes.

## Runtime Evidence

- [ ] Verify a real TLS-mode browser session with trusted CA.
- [ ] Verify a real direct-mode browser session through the intended SSH tunnel.
- [ ] Verify TLS-mode Basic Auth on public and direct internal endpoints.
- [ ] Verify direct-mode Basic Auth and record the accepted plaintext-loopback
      limitation.
- [ ] Verify killed-proxy client disconnect and next-use recovery.
- [ ] Verify killed-proxy explicit partial stop.
- [ ] Verify disable/re-enable preserves valid CA identity.
- [ ] Verify rollback removes `tls_proxy`, stops the final schema-3 TLS
      generation, and leaves no launch intent or complete/pending schema-3
      metadata before an old MkChad baseline starts.
- [ ] Verify detached persistence in each supported container runtime and mode.

## Audit And Completion Gate

- [x] A fresh Auditor confirms schema migration and downgrade behavior cannot
      orphan or multiply managed generations.
- [x] A fresh Auditor confirms direct mode is constrained to the declared
      trusted-host profile and is never presented as multi-user hardening.
- [x] A fresh Auditor confirms enabled mode retains every archived P0/P1 control.
- [x] A fresh Auditor confirms partial-pair stop signals only freshly verified
      identities through pidfd under the fence.
- [x] No policy-reportable P0/P1 findings remain within either profile's stated
      assumptions.
- [x] The explicit no-password local-user exception remains accurately scoped.
- [x] No secrets or unrelated untracked files appear in any diff or commit.
- [ ] Required implementation, tests, docs, and runtime evidence are complete.
- [ ] Changed revisions are committed and pushed only after explicit user
      authorization.
