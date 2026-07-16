# Single Persistent OpenCode Server Sprint Checklist

## Decisions

- [x] Use one lazy detached proxy/backend pair per user and host.
- [x] Use stable host-CA-authenticated HTTPS on the public loopback port.
- [x] Keep OpenCode on a distinct automatic high internal loopback port.
- [x] Preserve exact protected-config/`OPENCODE_PORT` and preferred `4096` as
      public-port policy; configured conflicts never fall back.
- [x] Prove backend ownership on the exact established connection before client
      bytes are read/forwarded.
- [x] Use Java 21 `SSLServerSocket`, PKCS12, keytool, and virtual threads; do not
      add OpenSSL.
- [x] Preserve Basic Auth rather than generating OpenCode credentials.
- [x] Warn that TLS authenticates the server, not clients, and that both
      loopback endpoints are locally accessible without a user-supplied
      config-file or `OPENCODE_SERVER_PASSWORD` credential.
- [x] Preserve renewable lifecycle locking from MkChad `cdf5499`.
- [x] Add a persistent mode-`0600` Linux `flock(2)` fence held by the Neovim
      process across logical validation and every shared lifecycle side effect.
- [x] Preserve bounded initial SSE connection behavior from opencode.nvim
      `e04b7a7`.
- [x] Treat schema 1 as deployed legacy state; never probe or signal a live PID,
      and migrate dead matching state under lock.

## Worktree Safety

- [x] Read all in-scope `AGENTS.md` and governing documents before edits.
- [x] Restrict implementation to the three sprint repositories.
- [x] Preserve untracked MkChad `lazy-lock.json`.
- [x] Preserve untracked msk_containers JDT archive.
- [x] Use only `/tmp/opencode/...` for runtime fixtures.
- [x] Do not inspect real credentials or session data.
- [x] Do not change Dockerfiles; existing Java/keytool support was sufficient.
- [x] Commit and push only with explicit user authorization.

## Certificate Material

- [x] Generate stable EC CA and `127.0.0.1` leaf with keytool/PKCS12.
- [x] Generate a random mode-`0600` password file.
- [x] Use keytool `-storepass:file`; keep password content out of argv.
- [x] Keep state root and TLS directory `0700`.
- [x] Keep CA PEM, stores, leaf evidence, password, state, and logs `0600`.
- [x] Validate/reuse material under lock and regenerate only absent/invalid
      material.
- [x] Atomically isolate invalid material and publish a complete replacement.
- [x] Keep certificate identity stable across ordinary process recovery.
- [x] Persist CA path and certificate identity, not password/key content.

## Java TLS Proxy

- [x] Load PKCS12 directly into `SSLContext`/`SSLServerSocket`.
- [x] Bind loopback only and permit TLS 1.2/1.3.
- [x] Disable ALPN/HTTP/2 negotiation.
- [x] Bound concurrent virtual-thread handlers.
- [x] Stream proc TCP tables with line/entry/byte/time bounds and separately
      bound concurrent proof scans; require one valid family-specific first-line
      header per table, fully validate all state-dependent row columns and exact
      family address widths, and reject partial or structurally incomplete scans.
- [x] Complete TLS before backend connection and application reads.
- [x] Revalidate backend PID start and boot identity before connecting.
- [x] Send only fixed unauthenticated keep-alive `GET /global/health`.
- [x] Bound preflight timeout, headers, and body.
- [x] Accept only bounded `200`/`401` with explicit `Content-Length`.
- [x] Reject malformed, oversized, transfer-encoded/chunked, close-delimited,
      or timed-out preflight responses.
- [x] Map the exact reverse ESTABLISHED tuple in procfs.
- [x] Require exactly one tuple and its inode under expected backend PID fds.
- [x] Pump raw bytes bidirectionally only on that same socket after proof.
- [x] Never reconnect a client stream.
- [x] Monitor process/start/boot/inode ownership while pumping and fail closed.
- [x] Fail closed when Linux procfs evidence is unavailable.

## Schema 2 And Lifecycle

- [x] Record one generation with stable HTTPS URL/CA/certificate identity.
- [x] Record exact proxy PID/start/executable/argv/public port/log.
- [x] Record exact backend PID/start/executable/argv/internal port/version/log.
- [x] Record immutable runtime and launch executable device/inode identities for
      both roles, plus Java source identity for the proxy.
- [x] Record host boot ID and public port source.
- [x] Keep secrets and sensitive file content out of state.
- [x] Use fsynced same-directory temporary writes and atomic rename.
- [x] Use mode-`0600` transient pending metadata for interrupted startup cleanup.
- [x] Require renewed current lock ownership before every pending write and
      generation-matched removal.
- [x] Publish complete state only after both listeners and pinned health pass.
- [x] Preserve lock lease renewal, owner/inode checks, stale reclaim, and bounded
      waiting.
- [x] Select public and internal ports under lock with bounded attempts.
- [x] Launch backend first and prove exact process/listener ownership.
- [x] Launch proxy with immutable backend PID/start/boot/port and prove listener.
- [x] Prove every Lua lifecycle listener with a bounded streaming, exact-header,
      full-row, complete-two-table scanner and exact cross-table uniqueness
      before checking `/proc/<pid>/fd` inode ownership.
- [x] Require pinned authenticated HTTPS health before publication.
- [x] Stop proxy first, then exact verified backend.
- [x] Replace both when either process/listener/identity/health fails.
- [x] Keep CA and valid public port across normal recovery.
- [x] Retain no `ExitPre` shared-pair stop.
- [x] Recycle TUI on process, directory, URL, generation, or CA identity change.
- [x] Keep all retries, waits, cleanup, and escalation bounded.
- [x] Route every wait-for-result subprocess reachable under the lifecycle
      fence through one argv-only async runner with protected stdin, independent
      64 KiB stdout/stderr caps, a five-second/per-operation deadline, 250 ms
      TERM/KILL escalation, reaping, exactly-once completion, and complete
      pipe/timer/handle cleanup.
- [x] Run TUI creation and opencode.nvim reconnection outside the reload fence,
      then reacquire it for the final generation comparison.
- [x] Create/reclaim/release the logical metadata lock only under the kernel
      fence; lease expiry cannot bypass a live or suspended fence holder.
- [x] Use bounded stdin-driven Linux pidfd signaling with final identity
      validation after `pidfd_open`; retain proxy-first/backend and
      SIGTERM/SIGKILL ordering and fail closed without pidfd support.
- [x] Keep the persistent fence file `0600` in the `0700` host state root and
      close every acquired fd on success, refusal, timeout, release, and death.

## Legacy And Unsafe State

- [x] Label schema 1 as legacy in diagnostics.
- [x] Never probe a schema-1 HTTP URL or give it to opencode.nvim.
- [x] Never signal a live schema-1 PID, even when current executable path and
      argv exactly match; preserve state with trusted manual-accounting guidance.
- [x] Allow migration and explicit stop to remove matching legacy state only
      when its PID is already dead, without HTTP.
- [x] Treat missing state as inactive.
- [x] Diagnose malformed state without probing it; replace only under lock.
- [x] Refuse to overwrite unsupported future schema.
- [x] Refuse to signal live unverifiable PIDs.
- [x] Treat pre-inode schema-2/pending metadata as malformed and require bounded
      replacement or trusted manual process accounting.
- [x] Re-read valid pending metadata under renewed lock before cleanup, signal
      only exact identities for the target generation, and unlink only when the
      current generation still matches.
- [x] Fence every pending/state publication and removal across final logical
      validation and mutation; `cleanup_failed_pair` ignores caller identities
      and signals only freshly re-read validated pending identities.

## Clients

- [x] Supply MkChad curl `cacert` through protected stdin config.
- [x] Keep MkChad Basic Auth and JSON bodies in protected stdin config.
- [x] Preserve request-time current-directory routing and HTTP/1.1.
- [x] Add opencode.nvim optional `server.ca_cert` path/resolver.
- [x] Resolve and supply CA for every REST and SSE request.
- [x] Keep opencode.nvim CA, credentials, and bodies out of curl argv.
- [x] Preserve opencode.nvim request-time directory headers.
- [x] Give local attach `NODE_EXTRA_CA_CERTS=<state ca.pem>` in child options.
- [x] Preserve attach HTTPS URL, `--dir`, auth inheritance, and terminal layout.
- [x] Preserve scoped reload and validate both PIDs, generation, URL/port, and CA
      identity across reload.
- [x] Keep info observational and refuse network probes for untrusted state.

## Focused Verification Completed

- [x] Java source compiles with `javac --release 21`.
- [x] Valid certificate material remains byte-stable and corrupted CA material
      regenerates as a new private mode-correct identity.
- [x] Java proxy passes pinned authenticated HTTP.
- [x] Java proxy passes long-lived SSE.
- [x] Curl fails without CA and succeeds with CA.
- [x] Installed OpenCode 1.17.18 attach reaches the fixture only with
      `NODE_EXTRA_CA_CERTS` supplied.
- [x] Installed OpenCode 1.17.18 starts with fixture Basic Auth, accepts the
      proxy's unauthenticated `401` keep-alive preflight, and passes pinned
      authenticated health.
- [x] Replacement-before-proof receives only fixed preflight and no client
      Authorization/body.
- [x] Replacement-after-backend-death receives zero bytes.
- [x] Inode/expected-PID mismatch fails before forwarding client bytes.
- [x] PID-start and boot-ID mismatch fail before listening.
- [x] Concurrent proxy clients complete through bounded handlers.
- [x] Concurrent startup converges on one generation/proxy/backend pair.
- [x] Originating Neovim workers exit while detached pinned health remains live.
- [x] Proxy-only death replaces both and preserves CA identity.
- [x] Backend-only death replaces both and preserves CA identity.
- [x] TUI death recreates the local attach.
- [x] Automatic public conflict selects one persisted fallback.
- [x] Explicit public conflict fails without fallback.
- [x] Separate internal bind/listener takeover is rejected before proxy/client
      traffic.
- [x] Legacy migration and diagnostics pass without HTTP probing.
- [x] Malformed state info/recovery remains bounded.
- [x] Auth `401` preserves the existing generation.
- [x] Info remains lazy.
- [x] Reload success, refusal, and reconnect rejection remain bounded while
      preserving the pair.
- [x] Renewable lease survives beyond the original lease and dead owner reclaim
      remains bounded.
- [x] Existing opencode.nvim curl-security and SSE-connect tests pass.
- [x] New opencode.nvim pinned REST/SSE test verifies request-time CA,
      credentials, body, and directory behavior.
- [x] Wrong and missing private CA fail closed; REST/SSE CA, credentials, and
      body stay out of argv.
- [x] Table-driven complete/pending malformed-state info/ensure/reload/stop
      checks remain bounded and do not signal or request.
- [x] Atomic native executable replacement preserves original-process ownership
      through authoritative `/proc/<pid>/exe`, including ` (deleted)`, while a
      same-argv native process on another inode is refused.
- [x] `SPOS-AUD-P1-006` interpreted-launch and Java-source identities are
      compared at every generic process identity gate; original interpreted
      identity passes, same-argv/same-runtime replacement and Java source
      replacement fail closed, and stop does not signal unverifiable processes.
- [x] Synthetic large proc tables pass under a constrained heap; malformed,
      truncated, oversized, and overlong tables fail closed, and proof scans
      remain separately concurrency-bounded.
- [x] `SPOS-AUD-P1-005` constrained-heap empty, headerless, repeated-header,
      malformed-header, data-before-header, and match-plus-incomplete-other-table
      cases fail closed; production IPv4/IPv6 proc headers pass integration.
- [x] Auditor pass 6 fully validates Java and Lua proc row fields, exact
      family widths and state-dependent column counts; Java malformed fields
      4-16, wrong-family, missing/extra, truncation, ambiguity, complete-other-
      table, and constrained-heap cases pass, while Lua passes >8 KiB,
      malformed ignored-field, wrong-family, header/empty/truncation/duplicate,
      incomplete-other-table, and controlled production-listener cases.
- [x] No-password startup/diagnostic warning and public/direct-internal invalid
      credential `401` checks pass.
- [x] Protected mode-`0600` MkChad server config supplies port/username/password,
      environment values override it, malformed JSON, invalid fields,
      unsupported keys, insecure modes, empty/oversized files, and symlinks fail
      closed, and an occupied configured port selects no fallback.
- [x] Auditor pass 8 repair: live schema-1 exact-argv migration and explicit stop
      preserve state, send no signal/request, and return trusted manual-accounting
      guidance; dead legacy state migrates under lock and malformed legacy stop
      remains non-signaling.
- [x] Auditor pass 8 repair: a controlled SIGSTOP, lease-expiry/reclaim, and
      resume test proves the former owner cannot write, signal from, or remove a
      newer pending generation; the current owner can update/remove it, while
      newer-generation, malformed, and lock-loss paths fail closed.
- [x] Pass-9 Builder repair: deterministic test-only pauses cover pending write/
      remove, state publish/remove, logical reclaim, and pidfd signal after final
      validation while the fence is held. Contenders cannot reclaim or mutate
      after forced lease expiry; release/death permits progress; distinct newer
      generations survive; repeated failed acquisition has no fd growth.
- [x] Pass-9 Builder repair: pidfd helper mismatch refuses a surrogate PID, the
      valid pidfd path preserves SIGTERM behavior, and pending failure cleanup
      signals the freshly re-read pending identity rather than caller state.
- [x] Pass-10 Builder repair: hanging and SIGTERM-resistant fake `opencode`,
      `java`, and `keytool` cover version, every certificate generation/import
      phase, both keytool list validations, and Java validation. Timeout,
      oversized stdout/stderr, nonzero, malformed output, start failure, and
      callback exception remain bounded, reap children, release the fence, let
      a separate contender progress, publish no partial state/pending/TLS, and
      show stable fd/process counts.
- [x] Focused MkChad Lua and Python tests pass.
- [x] `git diff --check` passes in all three repositories.

## Explicit P1 Exception

- [x] `SPOS-AUD-P1-004` is explicitly deferred under the exception in
      `docs/sprints/single-opencode-server/2/audit_policy.md`. No generated or
      forced password was introduced.
- [x] Decision-maker/user accepted the policy gate on 2026-07-14 for this
      deployment scope, acknowledging that untrusted same-host users can access
      no-password deployments. The risk remains unresolved/conditional and was
      not code-fixed; TLS/CA is not client access control.
- [x] The bounded operational workaround is to set a strong existing password
      in protected MkChad config or `OPENCODE_SERVER_PASSWORD` before first use,
      or set it and stop/restart an existing pair.
- [ ] `SPOS-FOLLOWUP-AUTH-001` resolves or re-accepts the local-user access risk
      at its documented reevaluation trigger.

## Runtime And Release Evidence Still Open

- [ ] Verify installed OpenCode 1.17.20 attach with `NODE_EXTRA_CA_CERTS` (local
      installed runtime is 1.17.18).
- [ ] Verify real OpenCode 1.17.20 authenticated API/SSE and web UI, beyond the
      exact fixtures and local 1.17.18 lifecycle check.
- [ ] Manually import the host CA into a supported browser and verify the stable
      HTTPS web UI.
- [ ] Verify detached pair persistence and recovery on a SingularityCE target.
- [ ] Verify detached pair persistence and recovery on an Apptainer target.
- [ ] Build/install x86_64 container baseline and verify 1.17.20.
- [ ] Build/install aarch64 container baseline and verify 1.17.20.
- [ ] Verify mounted npm runtime override in supported containers.
- [ ] Run LuaLS and StyLua in the normal CI toolchain when available locally/CI.
- [x] Publish opencode.nvim CA support as `a383638` and update the MkChad
      immutable pin to that exact revision.
- [x] Publish final MkChad `53a85e8` and the coordination-document branch after
      explicit push authorization.

## Evidence

| Date | Area | Evidence |
| --- | --- | --- |
| 2026-07-14 | Baseline | MkChad started this redesign at `cdf5499`; opencode.nvim at `e04b7a7`; msk_containers at `7cde376`. |
| 2026-07-14 | Proxy | `python3 tests/tls_proxy_integration.py` passed HTTP, SSE, curl CA rejection/success, installed attach CA, concurrent clients, replacement, preflight parser, inode/PID, start, and boot checks. `MkChadTlsProxyTupleTest` passed exact, missing, and ambiguous tuple cases. |
| 2026-07-14 | Local OpenCode | Installed 1.17.18 passed a real Basic-Auth lifecycle: unauthenticated proxy preflight returned reusable `401`, pinned authenticated health succeeded, and exact pair cleanup completed. |
| 2026-07-14 | Lifecycle | Isolated headless lifecycle passed schema 2, permissions, auth 401, TUI death, proxy/backend death, stable CA, explicit conflict, malformed recovery, and legacy migration. |
| 2026-07-14 | Concurrency | `python3 tests/concurrent_startup.py` passed two-process startup, one generation/pair, automatic fallback, origin exit persistence, pinned health, and cleanup. |
| 2026-07-14 | Reload | Isolated headless reload passed inactive laziness, routed preflights/dispose/path, pair preservation, reconnect rejection, and busy refusal. |
| 2026-07-14 | Lock | Existing cross-process lease test passed with renewal and dead-owner reclaim. |
| 2026-07-14 | Environment | Java/keytool 25 can compile/run Java 21 source; local OpenCode is 1.17.18. SingularityCE, Apptainer, container builders, and StyLua are unavailable. LuaLS ran without initialized CI library/runtime metadata and emitted repository-wide undefined-global/type warnings, so it is not clean static evidence. |
| 2026-07-14 | Auditor pass 4 repair | Serial MkChad lifecycle/race/lease/reload/certificate/state/executable/concurrent-startup/proxy tests passed. Java `--release 21 -Xlint:all -Werror`, tuple/truncation/malformed proof, and `-Xmx24m` large/concurrent table tests passed. opencode.nvim protected curl, request-time REST/SSE CA, wrong/missing CA, and SSE-connect tests passed. `SPOS-AUD-P1-004` remains the explicit exception above; publication/pin remains open. |
| 2026-07-14 | `SPOS-AUD-P1-004` acceptance | Decision-maker/user explicitly accepted the policy gate for this deployment scope, acknowledged untrusted same-host access when no password is supplied, and agreed to set a strong password before first use or stop/set/restart. Risk remains unresolved/conditional and not code-fixed; TLS/CA is not client access control. |
| 2026-07-14 | Auditor pass 5 repair | Proc proof requires complete family-specific headers and complete bounded scans of both tables before exact cross-table uniqueness. Generic process identity compares interpreted launch and Java source inodes while retaining native `/proc/<pid>/exe` authority. Constrained-heap Java proof, proxy integration, executable/lifecycle/state/reload/lease/startup, opencode.nvim curl/TLS/security/SSE, and diff checks were rerun as recorded in the repair report. |
| 2026-07-14 | Auditor pass 6 `SPOS-AUD-P1-005` repair | Java and Lua now validate exact family-specific headers, all required proc row fields, 17-column ordinary and 12-column `SYN_RECV`/`TIME_WAIT` forms, complete newline-terminated tcp/tcp6 scans, and exact cross-table uniqueness. Java lint/compile, tuple/header/truncation/malformed/large/concurrency, proxy integration, serial MkChad lifecycle/startup/reload/lease/state/identity/certificate/concurrent-startup, Lua >8 KiB and controlled-listener proof, and opencode.nvim TLS/security/SSE tests passed. Runtime/container/browser and publication/pin gates remain open. |
| 2026-07-14 | Auditor pass 7 | Reported no remaining code P0/P1. `SPOS-AUD-P1-004` remains the explicitly accepted conditional exception. The only code-release gate was immutable fork publication and pinning. |
| 2026-07-14 | Fork publication | Published opencode.nvim CA support as `a38363837c564a14f50a3a72f58f7df8dbeff3ad`; MkChad `99bf651` pins `a383638`. A fresh remote fork clone passed curl-security and SSE tests, and the cross-repository TLS integration passed with `OPENCODE_NVIM_ROOT` set to that clean clone. |
| 2026-07-14 | Auditor pass 8 findings | Reopened the prior no-P0/P1 claim: P0 schema-1 migration/stop could promote current path/argv into signalable identity despite missing boot/start/immutable proof; P1 pending writes and unlinks were not uniformly renewed-lock and generation conditional, allowing a reclaimed owner to race newer metadata. |
| 2026-07-14 | Auditor pass 8 Builder repair | Removed every live schema-1 signal path and added dead-only removal plus manual-accounting diagnostics. Pending publication now renews ownership before write/publication; cleanup re-reads valid matching-generation metadata before signaling and unlinks only through the renewed-lock helper. Lifecycle/state/startup-race/concurrent-startup/lease/pending-generation tests, full TLS proxy integration, Java lint/tuple/large-table tests, and opencode.nvim TLS/curl-security/SSE regressions passed. Fresh independent audit remains open; this is repair evidence, not closure. |
| 2026-07-14 | Auditor pass 9 reopening and Builder repair | Reopened pending-generation fencing because lease rechecks could not prevent a suspended actor from resuming between final validation and mutation, and PID-number signaling remained reusable after validation. Added a process-held persistent `flock(2)` fence around logical lock create/reclaim/release and all shared side effects, plus bounded stdin-driven post-`pidfd_open` identity validation and pidfd signaling. Deterministic fence/pidfd pauses, lease expiry, contender block/progress, holder death, distinct generations, fresh-pending cleanup, surrogate PID refusal, and repeated-failure fd counts passed. The full serial MkChad lifecycle suite, concurrent startup, TLS integration, Java lint/tuple/large-table tests, opencode.nvim TLS/security/SSE, Python compile, and diff checks were rerun. Fresh independent audit remains open; this Builder evidence does not close `SPOS-AUD-P1-007`. |
| 2026-07-14 | Auditor pass 10 reopening and Builder repair | `SPOS-AUD-P1-008` reopened the no-known-P0/P1 claim because synchronous or independently unbounded OpenCode version, keytool, Java validation, curl, and pidfd-helper subprocesses could retain the authoritative fence indefinitely. One libuv runner now enforces argv-only execution, protected stdin, 64 KiB stream caps, five-second/operation deadlines, TERM then 250 ms KILL, reaping, exactly-once completion, and handle cleanup. Certificate validation occurs before staged publication; reload TUI/plugin subprocesses run outside the fence with final fenced revalidation. The new subprocess matrix; serial lifecycle/certificate/state/identity/pending/startup-race/reload/lease/proc/fence tests; concurrent startup; pidfd helper; TLS integration; Java lint/tuple/constrained-heap tests; opencode.nvim TLS/security/SSE; Python compile; and three-repository diff checks passed. Failure-phase and real lifecycle regressions are Builder evidence only; fresh independent audit remains open. |
| 2026-07-14 | Auditor pass 11 | Reported no remaining code P0/P1. It closed `SPOS-AUD-P1-007` and `SPOS-AUD-P1-008` after independently verifying suspended-holder fencing, owner-death recovery, pidfd identity, bounded subprocess timeout/escalation/reaping, contender progress, and the full serial/integration suite. It recorded nonblocking P2 gaps for shutdown certificate-staging cleanup and fresh final reload health validation. `SPOS-AUD-P1-004` remains the accepted conditional exception. |
| 2026-07-14 | Final remote verification | Fresh remote branch clones resolved to opencode.nvim `a38363837c564a14f50a3a72f58f7df8dbeff3ad`, MkChad `53a85e88b3d662595f3f36fb88f3036d39018616`, and coordination evidence `2ff2c749e9e0ed409eaa270ad90845b08238c18f`. The MkChad clone pinned `a383638`; protected curl/SSE, full clean-pair TLS integration, kernel-fence races, bounded subprocesses, and pidfd signaling passed from those remote clones. |

## Completion Gate

- [x] `SPOS-AUD-P0-003` implementation and clean-fork fixture evidence are
      complete in committed revisions.
- [x] A fresh independent audit confirms the pass-8 P0/P1 repairs and restores
      the no-known-P0/P1 claim, including the pass-9 `SPOS-AUD-P1-007` kernel-
      fence/pidfd repair and pass-10 `SPOS-AUD-P1-008` bounded-subprocess repair.
      Builder repair evidence is recorded above but does not close the findings.
      `SPOS-AUD-P1-004` remains the accepted, unresolved
      conditional policy exception for this deployment scope.
- [x] No secrets or unrelated untracked files were added to diffs.
- [ ] Runtime/container/browser gates above are complete.
- [x] The reviewed opencode.nvim revision is published and the committed MkChad
      pin references it exactly.
- [x] MkChad and coordination-document branches are published; this final
      evidence-only commit advances the coordination branch once more.
