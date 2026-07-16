# Single OpenCode Server Sprint 3 Checklist: Standalone Lifecycle Command

## Usage

This checklist tracks implementation of
`docs/sprints/single-opencode-server/3/sprint_spec.md`. It does not replace the
specification or the inherited Sprint 2 transport and security policies.

An item may be checked only when its implementation, required tests, and
documentation are complete. Fixture-only evidence must not close a real
SingularityCE, Apptainer, browser, architecture, or deployment gate.

The active selector is `single-opencode-server/3`. Keep that selector fixed for
an implementation, audit, or closeout invocation and pass the exact Sprint 3
plan, specification, checklist, inherited threat model, inherited audit policy,
and participating repositories to every agent.

## 1. Scope and Governing Inputs

- [x] **S3-SCOPE-001** Read the parent and all participating child `AGENTS.md`
      files before changing behavior.
- [x] **S3-SCOPE-002** Read the complete Sprint 3 specification, this checklist,
      Sprint 2 specification, Sprint 2 threat model, and Sprint 2 audit policy.
- [x] **S3-SCOPE-003** Confirm the invocation explicitly selected
      `single-opencode-server/3`; do not infer selection from unchecked rows or
      directory order.
- [x] **S3-SCOPE-004** Record exact starting commits and worktree status in
      `mkchad`, `msk_containers`, `opencode.nvim`, and the parent workspace.
- [x] **S3-SCOPE-005** Base MkChad implementation on
      `origin/verification/optional-transport` at or after reviewed baseline
      `512ab83`, not the lifecycle-free checked-out main history.
- [x] **S3-SCOPE-006** Preserve unrelated user changes, including MkChad's
      untracked `lazy-lock.json` and unrelated container artifacts.
- [x] **S3-SCOPE-007** Keep systemd, watchdogs, login startup, remote lifecycle,
      host-native fallback, Python rewrite, mandatory credential generation,
      non-loopback listeners, and multiple managed generations out of scope.
- [x] **S3-SCOPE-008** Treat the Sprint 2 hardened TLS and trusted-host direct
      profiles as binding; do not reinterpret direct mode as multi-user
      hardening.
- [x] **S3-SCOPE-009** Review whether the command boundary changes any inherited
      threat assumption and amend or replace policy documents before production
      implementation if it does.
- [x] **S3-SCOPE-010** Use only task-specific paths beneath
      `/tmp/opencode-mkchad` for temporary fixtures.

## 2. Live Environment and Credential Safety

- [x] **S3-SAFE-001** Treat `/home/matthew/.config/mkchad` as read-only unless
      the user explicitly authorizes a focused live deployment action.
- [x] **S3-SAFE-002** Do not run development lifecycle tests against live XDG
      config, state, data, cache, runtime, server, TLS, log, or lock paths.
- [x] **S3-SAFE-003** Use isolated `HOME`, `XDG_CONFIG_HOME`, `XDG_STATE_HOME`,
      `XDG_DATA_HOME`, `XDG_CACHE_HOME`, and `XDG_RUNTIME_DIR` where applicable.
- [x] **S3-SAFE-004** Do not inspect or expose real OpenCode provider tokens,
      server passwords, private transcripts, unrelated sessions, SSH material,
      CA private keys, or key-store passwords.
- [x] **S3-SAFE-005** Use synthetic credentials and marker values for every
      disclosure and argv/log/state test.
- [ ] **S3-SAFE-006** Recheck the live checkout observationally only when required
      by the sprint and confirm it was not changed by verification.

## 3. Repository and Publication Boundaries

- [x] **S3-REPO-001** Keep lifecycle implementation, command entrypoint, config
      parser, editor adapter, and lifecycle tests in `mkchad`.
- [x] **S3-REPO-002** Keep host/container wrapper, installation, and wrapper tests
      in `msk_containers`.
- [x] **S3-REPO-003** Make no production `opencode.nvim` change unless a focused
      test proves the existing `server.ensure`, `server.url`, or
      `server.ca_cert` contract is insufficient.
- [x] **S3-REPO-004** Update the Sprint 3 specification before introducing a new
      opencode.nvim requirement or changing the command's public contract.
- [x] **S3-REPO-005** Add no generated state, logs, TLS files, launch intent,
      pending metadata, test runtime, SIF artifact, credential, or transcript to
      source control.
- [ ] **S3-REPO-006** Commit and publish each changed child repository before the
      parent workspace records its gitlink.
- [x] **S3-REPO-007** Keep child commits independently reviewable and avoid
      mixing unrelated changes across repositories.
- [x] **S3-REPO-008** Inspect status, diff, and exact revision in every changed
      repository before publication and closeout.

## 4. Lifecycle Module Extraction

- [ ] **S3-MOD-001** Extract detached server lifecycle behavior from
      `lua/configs/opencode.lua` into an explicit reusable module boundary.
- [ ] **S3-MOD-002** Keep protected server-config parsing reusable by both the
      standalone entrypoint and the editor adapter.
- [ ] **S3-MOD-003** Keep state validation, process identity, procfs scanning,
      listener ownership, fencing, logical locks, pidfd signaling, subprocesses,
      TLS material, health, startup, recovery, and stop in the lifecycle layer.
- [ ] **S3-MOD-004** Keep attached TUI state, Snacks terminal presentation,
      terminal placement, notifications, user commands, and plugin connection
      state outside the detached lifecycle layer.
- [x] **S3-MOD-005** Ensure loading the lifecycle module alone registers no
      plugin, command, autocmd, mapping, window, buffer, or terminal.
- [ ] **S3-MOD-006** Preserve narrow deterministic test seams without exporting
      unsafe production mutation APIs.
- [ ] **S3-MOD-007** Preserve callback exactly-once behavior and release every
      timer, pipe, process handle, fence fd, and logical lock on all completions.
- [ ] **S3-MOD-008** Preserve the five-second utility-child deadline, 64 KiB
      per-stream caps, TERM then 250 ms KILL escalation, reaping, and lifecycle
      deadline cap.
- [ ] **S3-MOD-009** Keep all lifecycle subprocess invocations argv-only and
      asynchronous with protected stdin where currently required.
- [ ] **S3-MOD-010** Prove the refactor changes no existing state or process
      authority merely because code moved files.

## 5. Standalone Entrypoint and CLI Parsing

- [x] **S3-CLI-001** Add the minimal headless lifecycle entrypoint without
      loading user init files or plugins.
- [x] **S3-CLI-002** Implement exact command name `mkchad-opencode-server`.
- [x] **S3-CLI-003** Implement `start [--json]`.
- [x] **S3-CLI-004** Implement `status [--json]`.
- [x] **S3-CLI-005** Implement `stop [--json]`.
- [x] **S3-CLI-006** Implement `--help` without lifecycle mutation.
- [ ] **S3-CLI-007** Reject missing, unknown, duplicated, or misplaced commands
      and options with exit status `2` and no lifecycle mutation.
- [ ] **S3-CLI-008** Reject extra positional arguments and control- or NUL-bearing
      values before lifecycle mutation.
- [ ] **S3-CLI-009** Avoid Ex-command construction, shell interpolation, manual
      quoting, and `sh -c` or `bash -c` execution.
- [x] **S3-CLI-010** Preserve lifecycle exit status through the Lua entrypoint and
      installed wrapper.
- [ ] **S3-CLI-011** Produce concise human output that identifies safe log and
      recovery paths without exposing protected content.
- [x] **S3-CLI-012** Ensure command startup itself does not create a TUI, plugin
      connection, editor buffer, or MkChad UI state.

## 6. JSON Interface

- [x] **S3-JSON-001** Emit exactly one UTF-8 JSON object and one newline on
      stdout in `--json` mode.
- [x] **S3-JSON-002** Keep wrapper, runtime-discovery, Neovim, and progress
      messages off machine-mode stdout.
- [x] **S3-JSON-003** Emit schema version `1`, `ok`, `command`, and `status` with
      the exact documented types.
- [x] **S3-JSON-004** Emit healthy state fields `url`, `transport`, `generation`,
      and `ca_cert` with direct-mode CA represented as JSON null.
- [x] **S3-JSON-005** Emit `state: null` for inactive status.
- [ ] **S3-JSON-006** Emit bounded stable error codes and bounded presentation
      messages for lifecycle failures.
- [x] **S3-JSON-007** Return exit zero for completed status observation even when
      its result is inactive, unhealthy, or blocked.
- [ ] **S3-JSON-008** Return exit one for failed or refused start/stop lifecycle
      operations.
- [ ] **S3-JSON-009** Include no password, config body, environment dump,
      provider token, request body, prompt, SSE payload, CA private material, or
      key-store password in JSON, stderr, or human output.
- [ ] **S3-JSON-010** Bound output before retention and decoding in the MkChad
      caller.
- [ ] **S3-JSON-011** Reject empty, malformed, oversized, unsupported, trailing,
      duplicate-key where detectable, wrong-type, and inconsistent result data.
- [ ] **S3-JSON-012** Ignore unknown compatible fields while requiring every
      documented client-consumed field.
- [ ] **S3-JSON-013** Require TLS URL/transport/non-null CA and direct
      URL/transport/null CA consistency before endpoint caching.

## 7. Configuration and Path Compatibility

- [x] **S3-CONFIG-001** Preserve protected config path
      `${XDG_CONFIG_HOME:-$HOME/.config}/mkchad/opencode-server.json`.
- [ ] **S3-CONFIG-002** Preserve regular-file, owner, mode-`0600`, size, JSON
      object, supported-key, port, username, password, and strict Boolean
      `tls_proxy` validation.
- [ ] **S3-CONFIG-003** Preserve non-empty environment precedence only for
      `OPENCODE_PORT`, `OPENCODE_SERVER_USERNAME`, and
      `OPENCODE_SERVER_PASSWORD`.
- [ ] **S3-CONFIG-004** Preserve absence of an environment override for
      `tls_proxy` and default it to TLS when absent.
- [x] **S3-CONFIG-005** Preserve MkChad OpenCode application config path through
      `OPENCODE_CONFIG` in external and editor invocation paths.
- [x] **S3-CONFIG-006** Preserve state root
      `${XDG_STATE_HOME:-$HOME/.local/state}/mkchad/opencode/<hostname>` under
      minimal `-u NONE` execution.
- [x] **S3-CONFIG-007** Prevent accidental use of default `nvim` config or state
      roots when `NVIM_APPNAME` is absent or different.
- [ ] **S3-CONFIG-008** Resolve proxy source and pidfd helper to the same trusted
      MkChad assets in host and in-container invocation paths.
- [ ] **S3-CONFIG-009** Preserve mode-`0700` directories and mode-`0600` config,
      state, log, TLS, and password files.
- [ ] **S3-CONFIG-010** Document that MkChad clients still require shared stop
      and Neovim restart after relevant protected-config changes.

## 8. State and Schema Compatibility

- [ ] **S3-STATE-001** Make no state-schema change unless the specification,
      policy, downgrade plan, and tests are revised first.
- [ ] **S3-STATE-002** Reuse healthy valid schema-2 state only as TLS mode.
- [ ] **S3-STATE-003** Reuse healthy valid schema-3 TLS and direct state without
      migration.
- [ ] **S3-STATE-004** Preserve schema-1 legacy non-probing and non-signaling
      behavior.
- [ ] **S3-STATE-005** Preserve malformed and future-schema fail-closed behavior.
- [ ] **S3-STATE-006** Preserve exact launch-intent publication before each role
      spawn and exact pending authority transfer after identity capture.
- [ ] **S3-STATE-007** Preserve generation-conditional pending, intent, and
      complete-state writes and removals under the kernel fence.
- [ ] **S3-STATE-008** Preserve atomic private writes and fsync/publication
      behavior.
- [ ] **S3-STATE-009** Keep credentials and private key material out of complete,
      pending, intent, lock-owner, and diagnostic state.
- [ ] **S3-STATE-010** Prove prior lifecycle code can interpret a stopped or
      explicitly compatible command-managed state during rollback.

## 9. Start and Recovery Behavior

- [ ] **S3-START-001** Make `start` idempotently reuse only a matching fully
      validated healthy generation.
- [ ] **S3-START-002** Validate requested transport against active trusted state
      and refuse automatic mode flipping.
- [ ] **S3-START-003** Refuse unresolved or malformed launch intent until trusted
      authority permits exact reconciliation.
- [ ] **S3-START-004** Refuse live unverifiable managed processes rather than
      replacing or signaling them.
- [ ] **S3-START-005** Preserve exact configured-port conflict refusal without
      fallback.
- [ ] **S3-START-006** Preserve bounded automatic-port selection, retry, and
      persistence behavior.
- [ ] **S3-START-007** Preserve exact OpenCode executable/version resolution and
      interpreted-launch identity handling.
- [ ] **S3-START-008** Preserve loopback-only backend and proxy binding.
- [ ] **S3-START-009** Preserve TLS certificate validation/generation, distinct
      internal port, backend proof, proxy proof, pinned health, and complete
      publication order.
- [ ] **S3-START-010** Preserve direct-mode omission of Java, keytool, TLS
      validation, proxy state, and active CA while proving the backend owns the
      public listener before health.
- [ ] **S3-START-011** Publish complete state only after transport-appropriate
      identity, listener, certificate, and health checks succeed.
- [ ] **S3-START-012** Preserve proxy-only and backend-only next-use recovery.
- [ ] **S3-START-013** Treat command-result loss after complete publication as a
      discoverable generation, not permission to launch a duplicate.
- [ ] **S3-START-014** Ensure a caller timeout does not automatically stop or
      replace an ambiguously completed shared generation.

## 10. Observational Status

- [ ] **S3-STATUS-001** Distinguish `healthy`, `inactive`, `unhealthy`, and
      `blocked` status.
- [ ] **S3-STATUS-002** Require complete process, listener, transport, and
      transport-specific certificate identity before health is authoritative.
- [ ] **S3-STATUS-003** Keep status free of process start/stop, state repair,
      state removal, port selection, and transport change.
- [ ] **S3-STATUS-004** Do not create state, pending, intent, lock, TLS, or log
      files during inactive status.
- [ ] **S3-STATUS-005** Do not validate or rotate retained inactive TLS material
      in direct mode.
- [ ] **S3-STATUS-006** Never probe legacy, malformed, future, certificate-
      mismatched, process-invalid, or replacement endpoints with credentials.
- [ ] **S3-STATUS-007** Report active and requested transport mismatch with the
      documented stop/restart action.
- [ ] **S3-STATUS-008** Report local/server version mismatch without mutating the
      active generation.
- [ ] **S3-STATUS-009** Keep human diagnostics useful without exposing config or
      inherited environment values.

## 11. Safe Stop

- [ ] **S3-STOP-001** Make already-inactive stop an idempotent success.
- [ ] **S3-STOP-002** Follow trusted active state independently of requested
      config transport.
- [ ] **S3-STOP-003** Stop a complete TLS generation proxy first, then backend.
- [ ] **S3-STOP-004** Stop a verified surviving backend after proxy-only death.
- [ ] **S3-STOP-005** Stop a verified direct generation backend without proxy
      assumptions.
- [ ] **S3-STOP-006** Open a pidfd before final identity validation and dispatch
      every signal only through that pidfd.
- [ ] **S3-STOP-007** Preserve bounded TERM, wait, KILL, final-wait behavior.
- [ ] **S3-STOP-008** Never signal a live schema-1 process or another live
      unverifiable process.
- [ ] **S3-STOP-009** Preserve matching state when signal authorization,
      dispatch, escalation, or death confirmation fails.
- [ ] **S3-STOP-010** Remove state only after every authoritative matching role
      is confirmed dead.
- [ ] **S3-STOP-011** Keep stop and state removal generation-conditional under a
      continuously held kernel fence.

## 12. Installed Wrapper and Container Routing

- [x] **S3-WRAP-001** Add executable wrapper
      `msk_containers/nvim/bin/mkchad-opencode-server`.
- [x] **S3-WRAP-002** Install it to `$HOME/.local/bin` through
      `bin/install_nvim.sh` with mode `0755`.
- [ ] **S3-WRAP-003** Resolve support tools relative to the installed wrapper and
      never depend on caller cwd.
- [ ] **S3-WRAP-004** Use the active managed `neovim.sif` contract and preserve
      supported `NVIM_CONT_LOCATION`/container-directory overrides.
- [x] **S3-WRAP-005** Enter the active SIF exactly once from host mode.
- [x] **S3-WRAP-006** Detect in-container mode through a reliable explicit image
      signal and invoke image Neovim directly.
- [x] **S3-WRAP-007** Never recursively invoke SingularityCE or Apptainer from
      inside the MkChad image.
- [x] **S3-WRAP-008** Preserve MkChad's XDG runtime/cache setup,
      architecture-neutral npm host bind, architecture-specific npm selection,
      and `OPENCODE_CONFIG` environment.
- [x] **S3-WRAP-009** Invoke minimal headless Neovim with `-u NONE` and no plugin
      loading.
- [ ] **S3-WRAP-010** Forward only documented arguments and preserve signals and
      exit status.
- [x] **S3-WRAP-011** Keep runtime discovery and errors off JSON stdout.
- [ ] **S3-WRAP-012** Fail clearly for missing runtime, active image, lifecycle
      assets, support tool, or image Neovim.
- [ ] **S3-WRAP-013** Never silently select another SIF or launch host OpenCode
      outside the active image.

## 13. opencode.nvim and MkChad Adapter

- [x] **S3-ADAPT-001** Configure opencode.nvim with asynchronous
      `server.ensure(callback)` that invokes
      `mkchad-opencode-server start --json`.
- [x] **S3-ADAPT-002** Keep `server.start = false` and provide no terminal launch
      fallback after command failure.
- [x] **S3-ADAPT-003** Invoke the command through an argv array without blocking
      Neovim's main loop.
- [x] **S3-ADAPT-004** Bound command duration, stdout, and stderr and invoke the
      ensure callback exactly once.
- [x] **S3-ADAPT-005** Require exit zero and a valid success object before
      exposing an endpoint.
- [x] **S3-ADAPT-006** Cache URL, transport, generation, and CA only from the
      current successful ensure result.
- [x] **S3-ADAPT-007** Make `server.url` resolve only the current validated cached
      endpoint after ensure.
- [ ] **S3-ADAPT-008** Make `server.ca_cert` return the current TLS CA and nil for
      direct mode.
- [x] **S3-ADAPT-009** Reject stale command completion after a newer invocation
      or generation has won.
- [x] **S3-ADAPT-010** Clear or supersede stale endpoint cache after stop,
      command failure, or validated generation change.
- [ ] **S3-ADAPT-011** Preserve protected config/environment authentication in
      opencode.nvim without returning credentials in command JSON.
- [ ] **S3-ADAPT-012** Preserve request-time cwd routing, protected curl config,
      REST, SSE, HTTP/no-CA, and HTTPS/pinned-CA behavior.

## 14. Editor-Only TUI, Commands, and Reload

- [ ] **S3-UI-001** Keep local `opencode attach` creation and recycling in the
      editor adapter.
- [ ] **S3-UI-002** Make external standalone start create no attached TUI.
- [x] **S3-UI-003** Make `:OpenCodeStart` ensure the server through the command
      before creating or showing the local TUI.
- [ ] **S3-UI-004** Preserve attached TUI URL, `--dir`, generation, transport,
      and active CA identity recycling rules.
- [ ] **S3-UI-005** Supply `NODE_EXTRA_CA_CERTS` only for TLS attach.
- [ ] **S3-UI-006** Preserve terminal toggle, show, close, and placement behavior.
- [x] **S3-UI-007** Make `:OpenCodeStop` delegate to the command and close only
      the invoking local TUI after successful or inactive stop.
- [ ] **S3-UI-008** Make `:OpenCodeInfo` combine command status with editor-local
      SSE/TUI state without lifecycle mutation.
- [ ] **S3-RELOAD-001** Preserve current absolute-directory targeting.
- [ ] **S3-RELOAD-002** Preserve inactive reload without server startup.
- [ ] **S3-RELOAD-003** Preserve busy session, pending permission, and pending
      question refusal with no force bypass.
- [ ] **S3-RELOAD-004** Preserve routed `/path` validation, local-only TUI
      recycle, and opencode.nvim reconnection.
- [ ] **S3-RELOAD-005** Preserve final generation, backend, transport, URL, port,
      proxy, and certificate revalidation as applicable.
- [ ] **S3-RELOAD-006** Ensure reload has no direct detached process spawn or
      stop path after extraction.

## 15. Concurrency, Interruption, and Recovery Tests

- [ ] **S3-RACE-001** Prove two external first starts converge on one generation.
- [ ] **S3-RACE-002** Prove external and MkChad first starts converge on one
      generation.
- [ ] **S3-RACE-003** Prove start racing stop serializes to a documented result or
      fails safely without duplicate or unrelated signaling.
- [ ] **S3-RACE-004** Kill the manager before spawn and verify no managed process
      or lifecycle state appears.
- [ ] **S3-RACE-005** Kill the manager after launch-intent publication and verify
      later startup remains safely blocked or exactly reconciled.
- [ ] **S3-RACE-006** Kill the manager after pending publication and verify
      generation-conditional cleanup.
- [ ] **S3-RACE-007** Kill the manager after complete publication and verify later
      callers reuse the detached generation.
- [ ] **S3-RACE-008** Suspend a fence holder beyond lease expiry and prove no
      contender bypasses the kernel fence.
- [ ] **S3-RACE-009** Kill a fence holder and prove the kernel releases authority
      for bounded contender recovery.
- [ ] **S3-RACE-010** Repeat timeout, overflow, callback failure, and start failure
      paths and prove stable fd, timer, and child counts.
- [x] **S3-RACE-011** Prove stale Lua command completion cannot overwrite a newer
      validated endpoint cache.
- [ ] **S3-RACE-012** Prove loss of command output after complete publication does
      not create a second generation on retry.

## 16. Automated Regression Suites

- [x] **S3-TEST-001** Run strict protected server-config tests.
- [ ] **S3-TEST-002** Run schema-1, schema-2, schema-3, pending, launch-intent,
      malformed, future, and downgrade state tests.
- [ ] **S3-TEST-003** Run TLS lifecycle, direct lifecycle, partial-stop,
      selective-death, executable-identity, and startup-race tests.
- [ ] **S3-TEST-004** Run kernel-fence, lock-lease, pending-generation,
      concurrent-startup, and pidfd signaling tests.
- [ ] **S3-TEST-005** Run bounded subprocess timeout, overflow, TERM resistance,
      KILL, reaping, callback failure, and contender-progress tests.
- [ ] **S3-TEST-006** Run certificate generation/validation and Java 21 compile,
      lint, tuple, parser, constrained-heap, and concurrent proxy tests.
- [ ] **S3-TEST-007** Run procfs scanner header, tuple, truncation, malformed,
      large-table, and cross-table uniqueness tests.
- [ ] **S3-TEST-008** Run authentication, config, state, argv, log, notification,
      command JSON, and stderr disclosure tests.
- [ ] **S3-TEST-009** Run scoped reload and local TUI reuse/recycle tests in TLS
      and direct modes.
- [ ] **S3-TEST-010** Run opencode.nvim protected-curl, HTTP/no-CA,
      HTTPS/pinned-CA, wrong/missing-CA, SSE, and response-redaction tests.
- [x] **S3-TEST-011** Add command parser, framing, result validation, exit status,
      and no-MkChad-load tests.
- [ ] **S3-TEST-012** Add wrapper fake-runtime tests for exact argv, environment,
      binds, host/container routing, errors, and status propagation.
- [x] **S3-TEST-013** Add isolated installer test proving the command is installed
      executable without changing unrelated files.
- [ ] **S3-TEST-014** Run StyLua, LuaLS, shell syntax, Python compile, Java checks,
      and repository-specific formatters applicable to changed files.
- [ ] **S3-TEST-015** Run `git diff --check` in every changed child and the parent
      workspace.

## 17. Real Runtime and Cross-Client Evidence

- [ ] **S3-REAL-001** Record fixture tests separately from real runtime evidence.
- [ ] **S3-REAL-002** Verify host-shell start through a real supported
      SingularityCE runtime where available.
- [ ] **S3-REAL-003** Verify host-shell start through a real supported Apptainer
      runtime where available.
- [ ] **S3-REAL-004** Prove backend and optional proxy survive headless manager and
      launching shell exit in the real runtime.
- [ ] **S3-REAL-005** Prove in-container invocation does not nest the runtime.
- [ ] **S3-REAL-006** Prove the mounted npm override is selected for the image
      architecture and Node major.
- [ ] **S3-REAL-007** Verify loopback-only listeners and expected process count in
      TLS and direct profiles.
- [ ] **S3-REAL-008** Verify external curl/client health and Basic Auth without
      exposing credentials.
- [ ] **S3-REAL-009** Verify browser HTTPS only after manual trust of the exact
      generated CA, if browser evidence is in scope.
- [ ] **S3-REAL-010** Name exact MkChad, msk_containers, opencode.nvim, SIF,
      architecture, OpenCode, SingularityCE/Apptainer, Java, and Neovim revisions
      in recorded evidence.
- [ ] **S3-REAL-011** Do not close unavailable runtime or browser gates from fake
      wrapper, listener, or TLS fixtures.

## 18. Documentation and Operations

- [x] **S3-DOC-001** Document installation, prerequisites, active SIF selection,
      and command discovery.
- [x] **S3-DOC-002** Document `start`, `status`, `stop`, `--json`, and exit status
      semantics.
- [x] **S3-DOC-003** Document that standalone start creates no MkChad instance or
      attached TUI.
- [ ] **S3-DOC-004** Document config, state, log, TLS, pending, and launch-intent
      paths without exposing contents.
- [ ] **S3-DOC-005** Preserve hardened TLS default and trusted-host direct-mode
      warning.
- [ ] **S3-DOC-006** Preserve Basic Auth and no-password local-user warnings.
- [ ] **S3-DOC-007** Document manual browser CA trust and direct-mode absence of
      CA identity.
- [ ] **S3-DOC-008** Document stop/restart behavior after config or runtime
      changes.
- [ ] **S3-DOC-009** Document missing runtime/image/assets, port conflict, mode
      mismatch, unresolved intent, partial death, and unverifiable process
      diagnostics.
- [ ] **S3-DOC-010** Document upgrade, publication compatibility unit, and safe
      rollback procedure.
- [ ] **S3-DOC-011** Do not claim system service, watchdog, boot startup,
      host-native fallback, or automatic browser trust.
- [ ] **S3-DOC-012** Update package-selection ADR only if a selected package,
       component, version, source, or architecture scope changes.

### Development tranche evidence (2026-07-16)

- MkChad commit `eba2b83` adds the minimal `-u NONE` lifecycle entrypoint,
  bounded JSON command contract, and an isolated direct-mode command test. The
  test covers inactive status without lifecycle state, start, generation reuse,
  stop, JSON framing, and a usage exit of `2` under isolated
  `/tmp/opencode-mkchad` XDG paths.
- `msk_containers` commit `23c4791` installs the executable wrapper. Shell
  syntax checks, an isolated installer check, and an in-container-mode JSON
  status check passed. No real SingularityCE or Apptainer runtime evidence is
  claimed.
- The lifecycle-only load assertion confirmed no `opencode_opts`, OpenCode user
  commands, plugin, TUI, or lifecycle state are created for inactive status.
- The broader existing lifecycle suites still need relocation from their legacy
  `/tmp/opencode` fixture paths before they can run under this sprint's required
  `/tmp/opencode-mkchad` temporary-path policy.
- Repair cycle 1 (2026-07-16) resolves `SPOS3-AUD-P1-001` with MkChad commit
  `a713a71`: the asynchronous argv-only adapter bounds process output and time,
  requires a current strict JSON result before caching an endpoint, rejects
  malformed, trailing, oversized, and nonzero output, and delegates editor
  start, stop, and status to the standalone command. Its isolated regression
  covers callback once, cache clearing, stale completion, command stop/status,
  and an editor attach to an externally returned generation.
- Repair cycle 1 resolves `SPOS3-AUD-P1-002` with `msk_containers` commit
  `4d3a709`. This corrects the prior S3-WRAP-006/007 evidence: the legacy
  `MKCHAD_NVIM_CONTAINER` marker alone was not a reliable in-image signal.
  The image definitions and launcher now establish `MKCHAD_NVIM_IMAGE`; the
  wrapper validates it with the standard runtime signal and retains compatibility
  for an already running `neovim.sif` image. The isolated fake-runtime test
  proves one host entry, direct in-container Neovim execution, inherited XDG,
  npm, and `OPENCODE_CONFIG` values, and refusal to let the legacy host marker
  choose host-native execution.

### Closeout verification pass (2026-07-16)

- The final combined local tree was verified at parent `fd33d0e`, MkChad
  `a713a71`, `msk_containers` `4d3a709`, and `opencode.nvim` `b8ad804`.
  Isolated paths were created only below
  `/tmp/opencode-mkchad/spos3-closeout-20260716`.
- The focused standalone-command, MkChad command-adapter, wrapper fake-runtime,
  and isolated installer regressions passed. The strict protected server-config
  suite also passed under isolated XDG paths.
- Broader inherited verification is blocked by suites that create fixtures under
  the prohibited legacy `/tmp/opencode` root: `python3
  tests/opencode_downgrade_spec.py`, `python3 tests/opencode_fence_spec.py`,
  `python3 tests/opencode_disclosure_spec.py`, `python3
  tests/opencode_subprocess_spec.py`, and `python3 tests/concurrent_startup.py`.
  They were not run and were not modified. Consequently, no remaining broader
  suites, runtime/browser gates, publication gates, or acceptance rows are
  claimed complete by this pass.
- Auditor pass 2 reported no policy-reportable P0/P1 findings; prior
  `SPOS3-AUD-P1-001` and `SPOS3-AUD-P1-002` remain resolved. The pass report did
  not supply recordable identifiers, owners, or dispositions for its remaining
  P2 findings, so **S3-ACCEPT-011** remains open. Real SingularityCE,
  Apptainer, and browser evidence remains unclaimed.

## 19. Acceptance and Closeout

- [ ] **S3-ACCEPT-001** Demonstrate inactive status with no lifecycle files
      created.
- [ ] **S3-ACCEPT-002** Demonstrate standalone start with one validated endpoint,
      no MkChad/plugin load, and no TUI.
- [ ] **S3-ACCEPT-003** Demonstrate detached survival after manager and shell exit.
- [ ] **S3-ACCEPT-004** Demonstrate idempotent generation reuse.
- [ ] **S3-ACCEPT-005** Demonstrate first MkChad use reuses the external
      generation and creates only its local directory TUI.
- [ ] **S3-ACCEPT-006** Demonstrate TLS pinned REST/SSE and isolated direct
      HTTP/no-CA behavior.
- [ ] **S3-ACCEPT-007** Demonstrate mixed external/editor concurrent startup
      convergence.
- [ ] **S3-ACCEPT-008** Demonstrate selective role death and documented next-use
      recovery.
- [ ] **S3-ACCEPT-009** Demonstrate external safe stop and confirmed state
      removal.
- [ ] **S3-ACCEPT-010** Complete a fresh independent audit with no unresolved P0
      or P1 outside an explicitly applicable existing exception.
- [ ] **S3-ACCEPT-011** Record remaining P2/P3 findings, accepted limitations,
      unavailable real-runtime gates, and follow-up owners.
- [ ] **S3-ACCEPT-012** Verify no credential, private runtime artifact, generated
      state, or unrelated change appears in final diffs.
- [ ] **S3-ACCEPT-013** Commit and push changed child repositories in the required
      order before updating parent gitlinks.
- [ ] **S3-ACCEPT-014** Verify every published exact revision is remotely
      reachable and repeat critical integration checks from clean clones.
- [ ] **S3-ACCEPT-015** Update the sprint checklist only from recorded evidence;
      never check planned, partial, or fixture-only deployment work as complete.
