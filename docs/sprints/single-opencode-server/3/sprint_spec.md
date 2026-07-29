# Single OpenCode Server Sprint 3 Specification: Standalone Lifecycle Command

## Document Status

| Field | Value |
| --- | --- |
| Status | Implementation-ready; active workspace selector `single-opencode-server/3` |
| Decision date | 2026-07-16 |
| Scope | `mkchad`, `msk_containers`, and compatibility verification against `opencode.nvim` |
| MkChad baseline | `origin/verification/optional-transport` at `512ab83` |
| msk_containers baseline | `main` at `01a7355` |
| opencode.nvim baseline | `builder/single-opencode-server` at `6cd689e` |
| Runtime choice | Minimal headless Neovim using the extracted reviewed Lua lifecycle |
| Predecessor | `docs/sprints/single-opencode-server/2/sprint_spec.md` |

This document defines the implementation scope and acceptance contract for
Sprint 3 of the Single OpenCode Server series. The parent workspace selects it
explicitly as `single-opencode-server/3`; selection remains per invocation and
does not make it a repository-wide default.

The following predecessor documents remain authoritative for transport,
process, state, credential, and audit policy unless this sprint explicitly
preserves a stricter requirement:

- `docs/sprints/single-opencode-server/2/sprint_spec.md`
- `docs/sprints/single-opencode-server/2/threat_model.md`
- `docs/sprints/single-opencode-server/2/audit_policy.md`

Sprint 3 changes the lifecycle entrypoint and ownership boundary. It does not
weaken the hardened TLS profile, trusted-host direct profile, schema-3 state,
kernel fence, pidfd signaling, endpoint proof, or credential-handling contract.
If implementation reveals that the new command boundary changes a threat
assumption, the Sprint 2 threat model and audit policy must be amended or
Sprint 3-specific replacements must be approved before production code proceeds.

## 1. Sprint Goal

Allow the managed detached OpenCode server to be started, inspected, and
stopped without launching MkChad, while making the same standalone command the
only detached-server lifecycle entrypoint used by MkChad.

The command must preserve the reviewed shared-server behavior from Sprint 2:

- One host-namespaced managed generation shared by all clients.
- Hardened TLS proxy mode by default.
- Explicit trusted-host direct HTTP mode when configured.
- Lazy startup and next-use recovery rather than a watchdog.
- Exact process, executable, argv, listener, boot, and generation validation.
- Kernel-fenced mutation and pidfd-only signaling.
- Protected configuration, state, logs, TLS material, and credentials.
- Bounded subprocesses, startup, stop, health, and recovery behavior.

## 2. Sprint Outcome

After Sprint 3, an installed user can run:

```bash
mkchad-opencode-server start
mkchad-opencode-server status
mkchad-opencode-server stop
```

`start` launches or reuses only the detached managed server. It does not load
the MkChad configuration, initialize plugins, open a Neovim UI, or create an
attached OpenCode TUI.

MkChad uses the same command through opencode.nvim's asynchronous
`server.ensure(callback)` hook. The editor remains responsible for attached TUI
presentation, terminal placement, notifications, and scoped directory reload,
but no longer directly launches or stops detached server processes.

## 3. Accepted Decisions

The following decisions were approved on 2026-07-16:

1. Reuse the reviewed Lua lifecycle through minimal headless Neovim rather than
   porting the lifecycle to Python.
2. Use `origin/verification/optional-transport` as the MkChad source baseline.
3. Name the external command `mkchad-opencode-server`.
4. Expose `start`, `status`, and `stop` subcommands.
5. Make `start` idempotent: it reuses a fully validated matching generation or
   performs the existing bounded cleanup and startup flow.
6. Keep server startup separate from attached TUI creation.
7. Use a stable bounded JSON interface for MkChad integration.
8. Preserve all existing config paths, transport modes, and safe downgrade
   behavior. Schema-4 is approved only for brokered TLS generations; direct HTTP
   remains schema 3 and schema-2/3 TLS remains its existing exact-proof path.
9. Keep the lifecycle implementation in MkChad and the host/container launcher
   integration in `msk_containers`.
10. Make no production opencode.nvim change unless focused integration tests
    identify a missing lifecycle-hook behavior.

### 3.1 Approved Schema-4 TLS Lifecycle Amendment

The existing persistent Java TLS proxy is approved to become the private
lifecycle broker for every new schema-4 TLS generation. This adds no helper,
host-native verifier, watchdog, or public lifecycle endpoint. Lua retains the
kernel fence, logical lock, durable shared-state publication/removal, and
control-path reconciliation. The broker retains process-context authority and,
after a complete authorized `activate` or `stop` request commits, exclusively
completes backend launch or stop even if the caller exits.

The broker binds and proves one private Unix socket under the protected
host-specific state root before launching a backend. Root directories are
current-user-owned `0700`; broker-created socket/files are current-user-owned
`0600`. Before authority-bearing operations, root and socket identity require
no-follow type, ownership, mode, and stable device/inode checks. Control uses a
strict, bounded, one-request/one-response protocol with generation matching;
the nonce correlates responses and is not authorization. Connections and queued
work are bounded, and malformed, partial, trailing, duplicate, mismatched,
timed-out, or overloaded exchanges are non-mutating.

Schema-4 is valid only for `tls-proxy`. It records the broker protocol, exact
control path, and control inode in pending/complete metadata. Current schema-3
readers preserve schema-4 metadata as unsupported future state without startup,
replacement, signaling, or deletion. Existing schema-2/3 TLS may use their
legacy route only when every current direct proof succeeds; direct HTTP and its
credential policy are unchanged.

Schema-4 startup is broker-first: Lua writes durable intent, starts a
control-only broker, verifies matching broker/control evidence, then commits
broker-owned backend activation. Crash cuts are reconciled only from matching
intent, broker receipt, and control identity; contact failure or possible live
unproved authority blocks and preserves. Activation failure is terminal for the
generation unless exact cleanup proves no backend remains; it never retries or
adopts a child.

Schema-4 stop is public-quiesce-first. After a complete authorized stop commit,
the broker closes public admission, its accept loop, and every registered relay,
proves they are quiesced, then performs existing post-validation pidfd TERM/
bounded-wait/KILL signaling. It closes control acceptance and conditionally
unlinks the recorded control inode after backend death, returns one matching
receipt on the already accepted stop connection, and exits. Lua may remove state
only after that receipt, observed broker termination, and fenced control-path
absence. A lost receipt preserves state until a later fenced reconciliation proves
both recorded roles and the control path absent or safely reconciled.

The schema-4 broker/backend liveness matrix is: live/live is reusable only with
matching broker evidence and independent CA-pinned endpoint proof;
broker-live/backend-dead is unhealthy and recoverable only by locked broker
cleanup; broker-dead/backend-live is blocked for manual operating-system
accounting; both-dead may be reconciled under the fence after control-path
checks. These rules replace proxy-process-first cleanup and fence-through-signal
only for committed schema-4 operations. Direct HTTP and schema-2/3 TLS retain
their existing stop order and exact proof paths.

### 3.2 Approved Persistent Runtime Instance Amendment

Real SingularityCE evidence on 2026-07-29 showed that a detached broker process
can outlive a foreground `exec` manager while the runtime tears down its
squashfuse image mount. The surviving broker then has a disconnected image root
and cannot execute the approved one-shot Python pidfd helper during a later
stop. The deployment owner approved persistent SingularityCE/Apptainer instances
as runtime mount authority for supported host launches.

The installed MkChad launcher and standalone lifecycle launcher must create or
reuse one current-user runtime instance keyed by runtime, host, active image
identity, bootstrap identity, and fixed bind profile. Instance creation is
serialized separately from the lifecycle fence. Both launchers execute through
their matching instance, and an in-image detached `start` is permitted only when
the managed instance marker is present. This prevents a foreground container
from publishing a service whose image mount will disappear with its manager.

The instance owns only the container mount lifetime. It is not lifecycle
authority, a watchdog, a restart policy, or permission to bypass Lua state,
broker control, process proof, or pidfd signaling. `stop` terminates only the
verified managed server roles and deliberately retains the idle instance:
mapping lifecycle success to `instance stop` would race another external or
in-image caller and could kill a newly started generation or editor. An exact
idle instance may be stopped only as an explicit operational action after all
callers and managed roles are accounted for. Image or bootstrap rotation selects
a new profile rather than silently executing through stale mounts.

## 4. Meaning of Standalone

For this sprint, standalone means the user can manage the detached server from
an ordinary shell without starting the MkChad configuration or an interactive
Neovim process.

The command may still depend on:

- The installed MkChad lifecycle assets.
- The active MkChad Neovim SIF and its host launcher integration.
- Neovim 0.12 inside that image.
- OpenCode, curl, Java 21, keytool, Python pidfd support, and Linux procfs as
  already required by the selected transport profile.

The command runs a minimal headless Neovim process with no user configuration
and no plugin initialization. Using Neovim as the reviewed Lua/libuv runtime is
not considered launching MkChad.

Sprint 3 does not produce a host-native OpenCode distribution independent of
the SIF, replace the managed lifecycle with systemd, or make the command usable
without installed MkChad lifecycle assets.

## 5. Repository Ownership

### 5.1 MkChad

The `mkchad` repository owns:

- The reusable server lifecycle module.
- Protected server-config parsing shared by the command and editor adapter.
- The minimal headless command entrypoint.
- Human and JSON result construction.
- State, process, TLS, direct-mode, recovery, and stop behavior.
- The thin editor adapter for opencode.nvim and the local attached TUI.
- Lifecycle and command-level tests.
- User-facing server command documentation.

The current monolithic `lua/configs/opencode.lua` must be split so detached
server behavior is not coupled to plugin registration, notifications, terminal
windows, or attached TUI state. Equivalent file names are allowed, but server
lifecycle and editor presentation must have an explicit module boundary.

### 5.2 msk_containers

The `msk_containers` repository owns:

- The installed `mkchad-opencode-server` executable wrapper.
- Host-versus-container execution selection.
- Entry into the active versioned Neovim SIF from a host shell.
- Reuse of the architecture-specific mounted npm global runtime.
- Required `OPENCODE_CONFIG`, XDG runtime/cache, bind, and container environment
  setup consistent with the `mkchad` launcher.
- Installation through `bin/install_nvim.sh`.
- Wrapper and installation tests.
- Package-selection documentation only if a selected package or version changes.

### 5.3 opencode.nvim

The `opencode.nvim` repository already provides the intended integration
boundary:

- `server.ensure(callback)` performs asynchronous external preparation before
  every discovery attempt.
- `server.url` may resolve the prepared endpoint.
- `server.ca_cert` may resolve the active private CA path at request time.
- `server.start = false` disables the plugin's terminal fallback.

Sprint 3 expects compatibility tests, not a production plugin change. If a
plugin change becomes necessary, its requirement and compatibility impact must
be added to this specification before implementation.

## 6. Architecture

### 6.1 External Invocation

```text
shell
  -> mkchad-opencode-server start
     -> active Neovim SIF
        -> nvim --headless -u NONE
           -> standalone lifecycle entrypoint
               -> reusable lifecycle module
                  -> detached opencode serve (direct or legacy TLS)
                  -> persistent Java TLS proxy/broker (schema-4 TLS)
```

### 6.2 MkChad Invocation

```text
opencode.nvim discovery
  -> server.ensure(callback)
     -> mkchad-opencode-server start --json
        -> reusable lifecycle module in minimal headless Neovim
           -> validated endpoint result
  -> server.url / server.ca_cert
  -> opencode.nvim REST and SSE connection
  -> optional editor-owned opencode attach TUI
```

### 6.3 Container Reentry

When called from the host, the wrapper enters the active Neovim SIF with the
same relevant binds and environment as the MkChad launcher. When called from an
already running MkChad container, it invokes the in-container Neovim directly
and must not recursively launch SingularityCE or Apptainer.

Host and in-container paths must execute the same command entrypoint and produce
the same machine contract.

## 7. Command Contract

### 7.1 Syntax

The supported interface is:

```text
mkchad-opencode-server start [--json]
mkchad-opencode-server status [--json]
mkchad-opencode-server stop [--json]
mkchad-opencode-server --help
```

Unknown commands, unknown options, duplicate options, and positional arguments
must fail without lifecycle mutation. Process invocations must use argv arrays;
no path may concatenate user-controlled values into `sh -c`, `bash -c`, an Ex
command, or another shell command string.

### 7.2 Start

`start` performs the existing ensure operation without creating a TUI:

1. Load and validate protected configuration.
2. Inspect complete, pending, launch-intent, lock, and fence state.
3. Reuse only a matching fully validated healthy generation.
4. Refuse mode mismatch, unverifiable live processes, unresolved launch intent,
   unsupported state, or an exact configured-port conflict.
5. Recover an owned unhealthy generation through the existing bounded cleanup.
6. For direct and legacy TLS, retain the existing launch-intent and pending
   authority transfer. For schema-4 TLS, record intent, establish and validate
   the control-only broker, then commit broker-owned backend launch.
7. Publish complete state only after transport-appropriate process, listener,
   certificate, and health validation.
8. Return only after a complete healthy state is available or the operation has
   failed safely.

Concurrent external and MkChad callers must converge on one generation through
the existing kernel fence and logical-lock protocol.

### 7.3 Status

`status` is observational. It may validate configuration, state, process
identity, listeners, certificates, local executable version, and endpoint
health, but it must not:

- Start or stop a process.
- Acquire mutation authority merely to repair state.
- Create or remove state, pending, launch-intent, lock, TLS, or log files.
- Rotate certificate material.
- Change transport or select a new port.
- Create an attached TUI or connect opencode.nvim.

The status result must distinguish at least `healthy`, `inactive`, `unhealthy`,
and `blocked`. Persistent state alone is never sufficient for `healthy`.

### 7.4 Stop

`stop` follows trusted active state rather than requested configuration. It
preserves Sprint 2 behavior for schema-2/3 TLS generations, direct generations,
already-dead roles, live schema-1 state, unverifiable processes, and failed
termination. Schema-4 TLS follows the approved broker liveness matrix and
public-quiesce-first committed stop contract in section 3.1.

An already inactive managed service is an idempotent success. A live process
that cannot be proved and signaled through the approved pidfd path is a refusal,
not a successful stop. Matching state is removed only after every authoritative
managed role is confirmed dead.

## 8. Machine-Readable Result

### 8.1 Framing

With `--json`, stdout contains exactly one UTF-8 JSON object followed by one
newline. The lifecycle entrypoint must not emit startup messages, Neovim UI
messages, container-runtime discovery notices, logs, or progress text on
stdout.

The command bounds stdout and stderr retained by its caller. Expected failures
produce a bounded structured result without a Lua traceback. Launcher failure
before the lifecycle entrypoint starts may use bounded stderr and a non-zero
exit because no JSON producer ran.

### 8.2 Success Shape

The JSON schema version begins at `1`. A representative healthy result is:

```json
{
  "schema": 1,
  "ok": true,
  "command": "start",
  "status": "healthy",
  "state": {
    "url": "https://127.0.0.1:4096",
    "transport": "tls-proxy",
    "generation": "example-generation",
    "ca_cert": "/home/user/.local/state/mkchad/opencode/host/tls/ca.pem"
  }
}
```

For direct mode, `transport` is `loopback-http`, the URL uses `http`, and
`ca_cert` is JSON `null`. An inactive status has `state: null`.

The stable client-consumed state fields are:

| Field | Type | Meaning |
| --- | --- | --- |
| `url` | string | Fully validated active loopback endpoint. |
| `transport` | string | `tls-proxy` or `loopback-http`. |
| `generation` | string | Active managed generation identity. |
| `ca_cert` | string or null | Active TLS CA path, or null for direct mode. |

Human diagnostics may include additional validated PID, port, version, log,
certificate, config-source, and health information. Additional JSON fields may
be added compatibly, but consumers must ignore unknown fields.

### 8.3 Failure Shape

A lifecycle failure in JSON mode returns:

```json
{
  "schema": 1,
  "ok": false,
  "command": "start",
  "status": "blocked",
  "error": {
    "code": "mode_mismatch",
    "message": "bounded actionable diagnostic"
  }
}
```

Error codes are stable machine identifiers. Messages are bounded presentation
text and must not be parsed for decisions. Neither form may contain a password,
config contents, inherited environment dump, provider credential, request body,
prompt, SSE payload, CA private material, or key-store password.

### 8.4 Exit Status

- `0`: the requested operation completed successfully. `status` may return
  `inactive`, `unhealthy`, or `blocked` with exit zero when observation itself
  completed successfully.
- `1`: lifecycle operation failed or was refused.
- `2`: command-line usage error.

MkChad must require both exit zero and a structurally valid success object from
`start --json` before exposing an endpoint to opencode.nvim.

## 9. Configuration and State Compatibility

The command uses the existing protected configuration path:

```text
${XDG_CONFIG_HOME:-$HOME/.config}/mkchad/opencode-server.json
```

It preserves strict ownership, regular-file, mode-`0600`, size, JSON, supported
key, port, username, password, and `tls_proxy` validation. Non-empty
`OPENCODE_PORT`, `OPENCODE_SERVER_USERNAME`, and
`OPENCODE_SERVER_PASSWORD` retain their existing precedence. No environment
variable overrides `tls_proxy`.

The server process continues to receive the MkChad OpenCode application config:

```text
${XDG_CONFIG_HOME:-$HOME/.config}/mkchad/opencode.jsonc
```

The state root remains:

```text
${XDG_STATE_HOME:-$HOME/.local/state}/mkchad/opencode/<hostname>
```

The extracted module must resolve these paths identically under full MkChad and
minimal `-u NONE` execution. It must not accidentally use a default `nvim`
application-name state directory.

Schema-4 is approved only for new brokered TLS pending/complete metadata and
records broker protocol plus exact private control-path identity. Existing valid
schema-2 TLS and schema-3 TLS or direct generations remain usable through their
existing exact-proof behavior. Existing malformed, future, legacy, pending, and
launch-intent behavior remains fail-closed; schema-3 readers preserve schema-4
bytes and do not replace or signal named processes.

The standalone command reads configuration in a fresh process. MkChad client
credentials may still be process-cached, so user documentation must continue to
require a shared stop and Neovim restart after changing port, username,
password, or transport before clients reconnect.

## 10. MkChad Integration

### 10.1 opencode.nvim Options

MkChad configures:

```lua
vim.g.opencode_opts = {
  server = {
    ensure = function(callback)
      -- Run: mkchad-opencode-server start --json
    end,
    url = function(callback)
      -- Return the endpoint from the latest validated ensure result.
    end,
    ca_cert = function()
      -- Return the active CA from the latest validated ensure result.
    end,
    start = false,
  },
}
```

The exact internal adapter API may vary, but these semantics are required:

- Execute the command asynchronously without blocking Neovim's main loop.
- Use an argv array and a bounded operation timeout greater than the command's
  own bounded lifecycle deadline.
- Bound captured stdout and stderr before JSON decoding or notification.
- Invoke the ensure callback exactly once.
- Reject empty, malformed, oversized, unsupported, inconsistent, or trailing
  command output.
- Cache endpoint data only from the current successful invocation.
- Clear or supersede stale cached endpoint data after command failure, stop, or
  a different validated generation.
- Never fall back to opencode.nvim's terminal `server.start` function.

### 10.2 Editor-Only Behavior

The editor retains:

- Attached `opencode attach` TUI creation and recycling.
- Terminal visibility, toggling, and placement.
- Notifications and user commands.
- opencode.nvim connection and disconnection.
- Scoped current-directory reload and routed instance validation.

`:OpenCodeStart` first ensures the detached server through the command, then
creates or shows the local TUI. External `mkchad-opencode-server start` never
creates that TUI.

`:OpenCodeStop` delegates server stop to the command and closes the invoking
editor's local TUI only after a successful or already-inactive result.

`:OpenCodeInfo` combines observational command status with editor-local SSE and
TUI presentation. It must remain side-effect free.

Scoped reload may use the extracted shared module for validated state and
request primitives, but it must not regain a direct detached-spawn path. Its
existing busy-work, permission, question, directory, generation, transport,
process, certificate, lock, and final-revalidation guarantees remain required.

## 11. Wrapper and Runtime Requirements

The installed wrapper must:

- Resolve its own installed support tools without depending on the caller's
  working directory.
- Use the active `${NVIM_CONTAINER_DIR:-$HOME/containers}/neovim.sif` contract,
  including the existing `NVIM_CONT_LOCATION` override where supported.
- Preserve the architecture-neutral host npm base and architecture-specific
  in-container runtime selection.
- Supply required XDG runtime/cache directories and `OPENCODE_CONFIG` exactly as
  the MkChad launcher does.
- Detect in-container execution through an explicit reliable image environment
  signal and avoid nested container launch.
- Execute minimal headless Neovim with `-u NONE` and no plugin loading.
- Forward only documented command arguments.
- Preserve signals and the lifecycle command's exit status.
- Create or reuse a profile-bound current-user runtime instance for both the
  MkChad launcher and host lifecycle command, and execute payloads through its
  `instance://` URI.
- Keep instance creation serialized, argv-only, and machine-output safe; reject
  detached start from an ordinary foreground container.
- Retain the idle instance after server stop. Never use runtime instance stop as
  a substitute for verified lifecycle stop or as automatic post-stop cleanup.
- Keep machine-mode stdout free of wrapper diagnostics.
- Fail clearly when no active image, supported runtime, MkChad command assets,
  or in-container Neovim is available.

The wrapper must not select an arbitrary discovered SIF, silently use an older
image, launch host OpenCode outside the selected runtime, or bypass the managed
state when a command fails.

## 12. Threat-Model Application

Sprint 2's deployment profiles remain unchanged. The new command boundary adds
the following audit focus:

- A short-lived headless manager must not make the detached backend or proxy
  dependent on its lifetime.
- A supported runtime must retain the image mount through the profile-bound
  instance after the manager or originating editor exits.
- External, MkChad, and concurrent mixed callers must converge under the same
  fence and state authority.
- A host invocation and an in-container invocation must resolve the same config,
  state, proxy source, pidfd helper, and active SIF assets.
- Wrapper stdout noise must not be accepted as endpoint metadata.
- Malformed or spoofed JSON must never cause MkChad to connect to an unvalidated
  endpoint.
- A command timeout or killed manager must release its fence through process
  death and leave only the existing bounded pending/launch-intent recovery
  states.
- Wrapper path, argument, and environment handling must not introduce shell
  interpolation or credential disclosure.
- `status` must remain observational even when state is stale or malformed.
- `stop` must retain pidfd-only signaling and preserve authority when cleanup
  cannot be proved.
- New schema-4 TLS generations must bind private broker control before backend
  launch; all control-path DAC/inode, bounded framing/load, crash-cut,
  activation-failure, liveness-matrix, receipt, and lost-receipt rules in
  section 3.1 are audit requirements.
- Direct HTTP remains prohibited and P0-reportable in the hardened TLS profile.
- No-password local-user access remains subject to the existing explicit
  `SPOS-AUD-P1-004` exception and operational warning.

The command is not a new trust boundary against the intended user or root. That
does not permit unsafe handling of same-user concurrency, stale state, PID
reuse, selective process death, or malformed machine output.

## 13. Failure and Recovery Contract

Required failure behavior includes:

- Missing command or SIF: fail before state mutation.
- Missing lifecycle assets: fail before lock or process spawn where possible.
- Manager killed before spawn: no managed process or state is created.
- Manager killed after creating an idle runtime instance but before lifecycle
  spawn: no managed role or lifecycle state is created; a later matching caller
  reuses the same profile instance.
- Runtime instance loss is not permission to weaken signaling or adopt a
  detached process from a disconnected mount. A later invocation may establish
  its current profile but must let existing lifecycle proof block or reconcile
  the old generation normally.
- Manager killed after schema-4 launch intent or before activation commit: later
  starts reconcile only matching broker/control evidence or preserve state for
  trusted manual accounting.
- Manager killed after schema-4 activation or stop commit: the broker completes
  its idempotent transition; later callers reconcile only under the fence and do
  not relaunch, signal, or remove state from a missing response.
- Schema-4 activation failure retains private control for status/stop only after
  exact child absence; uncertain cleanup is blocked and preserved.
- Manager killed after complete state publication: later callers validate and
  reuse the detached generation.
- Schema-2/3 proxy or backend selective death: the next start performs existing
  transport-specific recovery. Schema-4 uses the section 3.1 four-outcome
  broker/backend matrix; broker-dead/backend-live is manual and blocked.
- Explicit-port conflict: fail without fallback.
- Automatic-port race: retain bounded fallback and persistence behavior.
- Mode mismatch: refuse automatic transport change and direct the user to stop,
  restart Neovim when applicable, and start again.
- Command output failure after successful server publication: do not start a
  second generation; a later invocation discovers and reuses the published one.
- Lua adapter timeout: do not assume the server failed or issue an automatic
  stop; a later ensure revalidates shared state.

## 14. Required Tests

### 14.1 Refactor Regression

- Existing TLS and direct lifecycle suites pass against the extracted module.
- Existing config, state, pending, launch-intent, fence, pidfd, subprocess,
  certificate, proc-scanner, partial-stop, reload, disclosure, downgrade, and
  concurrency tests retain their meaning.
- Loading MkChad remains lazy and starts no server.
- `status` and `:OpenCodeInfo` remain observational.
- Existing schema-2 and schema-3 fixtures require no migration.
- Schema-4 tests cover broker-first intent/control/activation crash cuts,
  terminal activation failure, strict bounded control frames and aggregate load,
  state-root/control DAC and inode replacement, and preservation on ambiguity.

### 14.2 Command Contract

- `start`, `status`, `stop`, and `--help` accept only documented arguments.
- Human mode is actionable and JSON mode emits exactly one bounded object.
- Success, inactive status, lifecycle refusal, and usage exit codes match this
  specification.
- JSON parser tests cover empty output, malformed JSON, trailing data, duplicate
  keys where detectable, wrong types, unsupported schema, oversized output,
  control characters, and inconsistent transport/CA/URL fields.
- Failure output contains no synthetic password, token, config body, request
  body, prompt, private key, or key-store password marker.

### 14.3 Standalone Lifecycle

- A clean external start creates one healthy detached generation without
  loading MkChad or plugins and without creating a TUI.
- The detached generation survives exit of the headless manager process.
- Repeated starts reuse the same generation.
- External status reports fresh health and performs no mutation.
- External stop terminates only verified managed roles and removes state only
  after confirmed death.
- Schema-4 stop proves public admission, accept-loop, and relay quiescence before
  broker-context pidfd signaling; matching receipt, broker death, and fenced
  control-path absence gate normal state removal, while lost receipts preserve.
- Direct and TLS modes both work through the command.
- Configured and automatic port behavior remains unchanged.
- Existing and changed runtime version diagnostics remain accurate.

### 14.4 Mixed-Caller Concurrency

- Two external starts converge on one generation.
- External and MkChad starts converge on one generation.
- Start racing stop either serializes to a documented result or fails safely.
- A killed or suspended command holder does not permit fence bypass.
- Repeated command failure does not leak fds, timers, children, or lock state.
- A stale command result cannot replace a newer endpoint cache in MkChad.

### 14.5 Wrapper Integration

- A fake container runtime verifies exact argv, binds, environment, image path,
  and exit-status propagation.
- Host mode enters the active SIF once.
- In-container mode invokes the image Neovim directly and never nests the
  container runtime.
- Paths containing spaces remain literal argv values.
- Missing runtime, image, lifecycle entrypoint, and Neovim fail clearly.
- Machine mode contains no runtime-discovery chatter on stdout.
- `bin/install_nvim.sh` installs the executable with mode `0755`.

### 14.6 Client Integration

- opencode.nvim calls `server.ensure` before URL resolution and receives the
  command's validated URL and request-time CA.
- TLS REST and SSE succeed only with the returned CA.
- Direct REST and SSE use HTTP with no CA.
- Authentication continues to come from the protected config/environment and
  never from command JSON.
- Start command failure invokes the callback once and does not fall back to a
  terminal server.
- TUI startup occurs only for an editor action that requests it.
- External startup followed by the first MkChad action reuses the existing
  generation and creates only the local TUI.

### 14.7 Real Runtime Evidence

Real SingularityCE and Apptainer checks are opt-in but required before deployment
claims. They must verify detached lifetime after the manager and launching shell
exit, host/container path parity, mounted npm override selection, loopback
listeners, external browser/client connectivity where applicable, and safe stop.

Fixture-only tests must not be reported as real SIF persistence evidence.

## 15. Documentation Requirements

Implementation must update durable documentation to cover:

- Command installation and prerequisites.
- `start`, `status`, `stop`, `--json`, and exit statuses.
- The distinction between standalone server startup and attached TUI startup.
- Config and state paths.
- TLS default and trusted-host direct warning.
- Basic Auth and the existing no-password local-user warning.
- Browser CA trust in TLS mode.
- Config-change stop/restart procedure.
- Logs and actionable diagnostics.
- Missing SIF/runtime and unresolved launch-intent recovery.
- Upgrade and rollback behavior.
- The requirement that real runtime persistence depends on supported
  SingularityCE/Apptainer behavior.

Do not describe a systemd service, watchdog, automatic login startup,
host-native OpenCode fallback, or Python lifecycle rewrite as implemented.

## 16. Acceptance Criteria

Sprint 3 is accepted when:

1. `mkchad-opencode-server start` creates or reuses the reviewed detached shared
   server without loading MkChad or creating a TUI.
2. The command works from a host shell through the active SIF and from MkChad
   without nested container launch.
3. `status` is observational and reports fresh validated lifecycle truth.
4. `stop` retains exact process authority, direct and legacy TLS cleanup,
   schema-4 public-quiesce-first broker cleanup, pidfd-only signaling, and
   fail-closed state preservation.
5. The command preserves all Sprint 2 transport, config, state, fence, process,
   credential, certificate, and recovery requirements.
6. JSON mode is bounded, versioned, deterministic, and contains no credentials
   or private material.
7. MkChad's opencode.nvim adapter invokes the command asynchronously through
   `server.ensure`, uses its validated endpoint result, and keeps
   `server.start = false`.
8. External and editor callers converge on one generation under concurrent
   startup.
9. Editor startup remains lazy; an external start creates no editor UI; an
   editor OpenCode action creates only its directory-scoped attached TUI after
   server ensure succeeds.
10. Existing scoped reload, TUI recycling, HTTP/no-CA, HTTPS/pinned-CA, and SSE
    behavior remains correct.
11. Existing valid state remains compatible without migration and rollback does
    not require unsafe state deletion.
12. Default tests are deterministic, credential-free, network-free, and use
    isolated paths under `/tmp/opencode-mkchad`.
13. Real runtime evidence is recorded separately and names the tested SIF,
    architecture, runtime, and exact revisions.
14. No unresolved policy-reportable P0 or P1 finding remains outside an existing
    explicitly applicable exception.
15. Child repository changes are committed and published before the parent
    workspace records their gitlinks.

## 17. Exit Demonstration

The sprint review must demonstrate:

1. Install the command and an active versioned Neovim SIF in an isolated test
   home.
2. Confirm no MkChad or OpenCode process is active.
3. Run `mkchad-opencode-server status --json` and observe inactive state with no
   lifecycle files created.
4. Run `mkchad-opencode-server start --json` and obtain one validated endpoint.
5. Confirm no MkChad configuration, plugin, or attached TUI was loaded.
6. Exit the launching shell or manager and prove the backend and optional proxy
   remain alive and healthy.
7. Run start again and prove the generation and process identities are reused.
8. Launch MkChad in a project directory, perform one OpenCode action, and prove
   it reuses the external generation while creating only its local attached TUI.
9. Demonstrate pinned HTTPS REST/SSE in TLS mode and HTTP/no-CA behavior in an
   isolated direct-mode run.
10. Demonstrate concurrent shell and MkChad startup convergence.
11. Kill each schema-4 broker/backend liveness combination and demonstrate its
    documented reuse, locked cleanup, blocked/manual, or fenced reconciliation
    outcome; retain existing selective-death evidence for direct and legacy TLS.
12. Run external stop and prove only verified managed roles terminate and state
    is removed after confirmed death.
13. Run the complete relevant automated suite and `git diff --check` in every
    changed repository.

No exit demonstration may use the live `~/.config/mkchad` checkout, its managed
state, its credentials, or unrelated OpenCode session data.

## 18. Publication and Rollback

### 18.1 Publication

Publish changed child repositories before updating parent gitlinks. If
opencode.nvim remains unchanged, expected child publication is MkChad and
`msk_containers`, followed by the parent workspace. Deployment must not point
the live configuration at an unpublished command/module pair.

The MkChad adapter and installed wrapper are one compatibility unit. Deployment
must avoid an interval where MkChad requires a missing command or the installed
command references missing lifecycle assets.

### 18.2 Rollback

No state-schema downgrade is planned. Before rollback:

1. Use the current command to stop any verified active generation when feasible.
   For schema-4, require its matching final receipt, broker death, and fenced
   control-path absence, or preserve state for later safe reconciliation.
2. Confirm complete state, pending state, and launch intent are absent, or retain
   them for the older compatible lifecycle only when exact compatibility has
   been tested.
3. Restore the prior MkChad lifecycle and launcher integration together.
4. Start an isolated server and verify the prior in-editor lifecycle still
   validates process, listener, transport, CA, and health state.

Never delete state or signal a process merely to make rollback appear clean.

## 19. Explicit Deferrals

Sprint 3 does not add:

- An application-level supervisor or watchdog. The approved idle container
  runtime instance is only persistent image-mount authority and performs no
  lifecycle health monitoring or restart.
- systemd, OpenRC, launchd, or login-session units.
- Automatic server start at boot or shell login.
- A Python rewrite of the lifecycle.
- A host-native fallback that bypasses the active SIF.
- Remote-host lifecycle management.
- Non-loopback listeners.
- Generated or mandatory OpenCode credentials.
- Automatic browser CA installation.
- Multiple independently managed OpenCode server generations.
- A generic lifecycle API for unrelated Neovim distributions.
- Changes to OpenCode's same-directory multi-TUI behavior.
