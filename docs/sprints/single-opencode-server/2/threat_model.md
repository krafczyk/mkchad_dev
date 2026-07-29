# Threat Model

## Scope

This model covers schema-2 compatibility state, schema-3 persistent OpenCode
service state, and the schema-4 brokered TLS state approved for Sprint 3 in
`docs/sprints/single-opencode-server/3/sprint_spec.md`:

```text
MkChad/Neovim
  -> hardened TLS: detached Java TLS proxy on stable public loopback port
     -> detached OpenCode backend on automatic internal loopback port
  -> trusted-host direct: detached OpenCode backend on the public loopback port
  -> directory-scoped opencode attach TUI
```

The deployment is one intended user on a multi-user Linux host. Other processes
owned by that user are cooperative unless they are stale or accidentally reuse
an identity. An administrator is adversarial for availability and may
selectively kill processes.

Other unprivileged local users are not trusted. Loopback is host-wide, not
user-private. TLS authenticates the server to clients but does not authenticate
clients, and possession of the public CA certificate is not an access-control
mechanism. Without `OPENCODE_SERVER_PASSWORD`, other local users can access both
the public proxy and a discovered internal backend port.

## Security Boundary

Root is outside the enforceable boundary. Root can read/replace the CA key,
keystore password, state, process memory, procfs, fds, executable, environment,
container image, and loopback traffic; forge PID/start/boot/tuple evidence; or
deny execution. The design does not claim confidentiality, integrity, or
availability against root.

The practical boundary protects against accidental PID reuse, stale state,
ordinary unprivileged local endpoint replacement, bind races, and selective
process death while Linux procfs and user file permissions remain trustworthy.

## Deployment Profiles

### Hardened TLS Profile

`"tls_proxy": true`, including an absent setting, is the default and required
profile where any local user or process is untrusted. It retains the Java TLS
proxy, distinct internal backend port, stable host CA and `127.0.0.1` leaf,
CA-pinned clients, fixed unauthenticated preflight, exact tuple/inode proof
before client bytes, and no reconnect to a replacement backend. TLS authenticates
the server but not clients: a strong user-supplied Basic Auth password remains
required on a multi-user host because the internal backend port is discoverable
and directly reachable.

Direct HTTP, plaintext Basic Auth, absent CA-pinned server identity, or stale
client delivery to a replacement listener is a policy-reportable P0 in this
profile. A protected config file selects the mode; it does not make direct HTTP
safe against an untrusted local user or process.

### Trusted-Host Direct Profile

`"tls_proxy": false` is an explicit trusted-host opt-out, permitted only when
all relevant same-host users and processes are trusted or the owner explicitly
accepts their capabilities. The excluded threats are their ability to inspect
plaintext loopback traffic (including a Basic Auth header), send requests to the
backend, bind the public port after backend death, and receive traffic from stale
HTTP clients. Direct mode deliberately has no CA-pinned server identity, TLS
encryption, relay tuple/inode proof, or replacement-listener protection. An SSH
tunnel does not restore local endpoint identity after it terminates on the host.

The protected, current-user-owned mode-`0600` config controls who selects the
profile, not transport security. The following controls remain mandatory:
loopback-only binding; strict config and schema-3 transport validation;
mode-mismatch refusal; exact backend executable/argv/start/boot/listener proof
before authenticated health; configured-port conflict refusal; kernel fence,
generation-conditional cleanup, and pidfd-only signaling; bounded recovery;
private metadata; and no credential disclosure in argv, state, logs,
notifications, or repository history. Basic Auth can reject ordinary
unauthenticated use but is not direct-HTTP transport encryption or server
authentication.

Reevaluate this profile before adding an untrusted user/process, packet-capture
capability, non-loopback exposure, or a different tunneling assumption. Stop
direct mode and use hardened TLS until the model is reviewed after any trigger.

## Assets

### Confidentiality

- OpenCode Basic Auth credentials.
- Provider credentials inherited by OpenCode.
- Prompts, request bodies, source, paths, session data, and SSE events.
- CA private key and its random PKCS12 password.

### Integrity

- Stable CA/public certificate identity and HTTPS URL.
- Exact association of public proxy, internal backend, generation, host boot,
  PID start times, executable/argv, listeners, and established socket inode.
- Current-directory routing and scoped reload isolation.
- Renewable lock ownership and generation-conditional cleanup.

### Availability

- Lazy MkChad startup independent of OpenCode.
- Bounded next-use recovery after proxy/backend/TUI/Neovim death.
- One pair and one generation under concurrent startup.
- Stable public URL and browser/client trust across ordinary recovery.

## Trust Assumptions

- Linux exposes readable `/proc/<pid>/stat`, `/proc/<pid>/fd`,
  `/proc/net/tcp{,6}`, and boot ID for the intended user's processes.
- Linux provides working advisory `flock(2)` and pidfds; Python 3 provides
  `os.pidfd_open` and `signal.pidfd_send_signal` in every target image.
- Other unprivileged users cannot modify mode-`0700` state directories or read
  the mode-`0600` server config, state, logs, password, or keystores.
- A schema-4 TLS proxy remains alive in its original process context while it
  brokers lifecycle work, can inspect its backend's required procfs evidence,
  and shares its protected host-specific state root and Unix socket inode with
  later invocations. A fresh invocation need not be able to dereference that
  procfs evidence.
- Java 21/keytool, curl, OpenCode, and the selected executables are trusted.
- SingularityCE/Apptainer uses compatible host network/PID visibility.
- Hostname namespaces shared-home state but is not a credential.
- A CA-authenticated public TLS endpoint plus exact same-connection backend
  proof is required before managed clients send secrets.
- Client authorization requires the user-supplied OpenCode Basic Auth password;
  CA trust alone provides no client authorization.

## Required Controls

- Bind public and internal listeners only to loopback.
- In the hardened TLS profile, require explicit state CA for every
  lifecycle/plugin curl and `NODE_EXTRA_CA_CERTS` for attach. Direct-profile
  clients use HTTP and receive no active CA setting.
- Keep server config, CA key, password, stores, state, and logs private.
- Keep password content out of argv/state/logs/notifications/repository.
- Prominently warn and diagnose when no user-supplied Basic Auth password is
  present; do not invent or force a password.
- Validate process executable, exact argv, PID start time, boot ID, and listener
  inode before reuse or signal. For interpreted launches, also require the
  current launch-path inode; independently require the current Java proxy source
  inode.
- Never signal a live schema-1 PID: that format lacks the persisted boot/start/
  immutable-executable evidence required to distinguish PID reuse. Preserve its
  state for trusted manual accounting and remove it automatically only when the
  PID is dead.
- On every TLS connection, prove the exact reverse ESTABLISHED tuple and inode
  under the immutable expected backend PID before client reads/forwarding.
- For schema-2/3 TLS, stop the verified proxy first, then the backend; direct
  mode retains its existing exact backend path. For schema-4 TLS, the persistent
  proxy is the lifecycle broker: after a fully validated mutating control request
  commits, it closes public admission, its public acceptor, and every registered
  relay before broker-context pidfd signaling. It retains the accepted private
  control connection only for the final receipt.
- Hold the process-owned kernel fence across logical-lock validation and every
  shared-state mutation or control-path reconciliation. Direct and legacy paths
  retain the fence-through-signal rule. A schema-4 activation or stop commits
  once under that fence, then transfers process-lifecycle completion to the
  broker's single-writer state machine; caller loss cannot cancel it.
- Open a pidfd before final managed-process identity validation and signal only
  through that pidfd; unsupported/mismatched evidence fails closed.
- Before any schema-4 authority-bearing socket operation, no-follow validate the
  state root and control path type, current-user ownership, restrictive mode,
  and stable device/inode identity. Bind control before launching a backend;
  accept only bounded, one-request/one-response, generation-matched control
  exchanges with bounded connections and queued work. The nonce correlates a
  response and is not authorization.
- Never send credentials to legacy HTTP, malformed/future state, unknown public
  listeners, or an unproved backend connection.
- Bound all locks, TLS handshakes, preflights, parsing, retries, connection
  handlers, pumps, process stops, and recovery attempts.
- Run every wait-for-result lifecycle utility through one argv-only async
  subprocess boundary with protected stdin, bounded output, an event-loop
  deadline, TERM/KILL escalation, reaping, and exactly-once cleanup.

## Threats And Controls

### T1: Public Proxy Replacement

Threat: an unrelated listener binds the persisted public port after proxy death
or during startup and attempts to collect Basic Auth or request bodies.

Controls:

- Clients trust only the protected host CA and verify the `127.0.0.1` leaf.
- MkChad validates exact proxy PID/start/executable/argv and listening inode
  before an authenticated health request.
- Unknown explicit conflicts fail; automatic conflicts choose a bounded
  fallback and are never adopted.
- The CA private key remains in a mode-`0600` store with random mode-`0600`
  password.

Residual risk: root or another process with access to the CA private key can
mint/serve a trusted replacement.

### T1A: Unauthenticated Local-User Access

Threat: when no password is supplied through protected MkChad config or
`OPENCODE_SERVER_PASSWORD`, another user on the same host connects to the public
TLS proxy or discovers and directly connects to the internal loopback backend.
TLS server authentication and relay tuple proof do not deny that client.

Controls: MkChad accepts a user-supplied credential from a strict, ignored,
current-user-owned mode-`0600` config or the process environment, preserves
OpenCode Basic Auth on both endpoints, warns at first use and in diagnostics,
and documents the strong-password stop/restart workaround. It does not generate
a credential.

Residual risk: unauthenticated local access remains a P1 under explicit
exception `SPOS-AUD-P1-004`; owner, workaround, follow-up, and reevaluation
trigger are recorded in `docs/sprints/single-opencode-server/2/audit_policy.md`.

### T2: Internal-Port Takeover Before Proof

Threat: the backend dies or loses its listener and another process binds the
recorded internal port between validation and connection.

Controls:

- The proxy pins backend PID, start time, boot ID, and internal port at launch.
- It revalidates process identity before connecting.
- It sends only the fixed non-secret preflight before proof.
- It maps the exact reverse established 4-tuple and requires that socket inode
  under the expected backend PID's fd directory.
- No decrypted client request byte is read/forwarded before proof.
- Failure closes both sockets and never reconnects the stream.

Result: a replacement may see only the fixed preflight if takeover occurs after
the pre-connect identity check. It receives no Authorization/body/client bytes.
If the expected process is already dead, it receives zero bytes.

### T3: Backend Dies After Proof

Threat: the expected backend dies while a long HTTP/SSE stream is active and a
replacement binds the internal port.

Controls:

- The client stream remains tied to the already proved socket; the proxy never
  reconnects it.
- Pump monitoring revalidates PID/start/boot and inode ownership.
- Death/EOF/ownership loss closes the TLS client connection.
- The next operation replaces proxy and backend together under lock.

Residual risk: in-flight work is lost and the client must retry through the new
generation.

### T4: Tuple Ambiguity Or Unsupported Evidence

Threat: procfs is malformed/unavailable, multiple entries match, address-family
translation is unexpected, or the tuple disappears between preflight and proof.

Controls:

- Require exactly one reverse ESTABLISHED tuple across tcp/tcp6.
- Stream both tables under line, entry, byte, time, and concurrent-scan bounds;
  require exactly one valid family-specific header as each table's first line,
  fully validate every state-dependent data-row column and address width, and
  let any structural or exceeded-bound failure invalidate the whole proof.
- Apply the same complete bounded two-table grammar and exact cross-table
  uniqueness to every Lua lifecycle listener ownership check before fd inode
  ownership is accepted; do not rely on a procfs read prefix.
- Require the exact inode under expected PID fds.
- Fail closed on zero/multiple matches, parsing error, permission error, timeout,
  or unsupported platform evidence.

Residual risk: root can forge all evidence. Non-Linux platforms are unsupported
for this relay rather than silently weakened.

### T5: Preflight Smuggling Or Resource Exhaustion

Threat: a replacement or compromised backend returns chunked, close-delimited,
oversized, malformed, or slow data to confuse response boundaries or consume
resources before proof.

Controls:

- The request is a fixed unauthenticated keep-alive health GET.
- Accept only HTTP/1.0/1.1 status `200` or `401` with explicit bounded
  `Content-Length`.
- Reject transfer encoding (including chunked), `Connection: close`, duplicate
  or invalid length, oversized header/body, EOF, and timeout.
- Bound active handlers and use no unbounded executor queue.

Residual risk: repeated clients can consume the configured bounded concurrency
and cause temporary denial of service.

### T6: CA Key Or Password Disclosure

Threat: another local user reads the CA signing key/password and impersonates
the public endpoint.

Controls:

- State/TLS directories are `0700`; stores/password/CA PEM are `0600`.
- Password is random, passed to keytool with `-storepass:file`, read directly by
  Java, wiped from its temporary character array, and omitted from state/argv.
- Logs and diagnostics do not print file content or inherited environment.
- Invalid material is isolated and replaced under lock.

Residual risk: the signing key is software-protected, not hardware-backed; the
intended user and root can read it. The CA PEM is non-secret but remains `0600`
to keep all generated material consistently private.

### T7: Browser Trust Confusion

Threat: users assume HTTPS works automatically, trust the wrong CA, or mistake a
certificate warning for an acceptable loopback condition.

Controls:

- Documentation gives the exact host CA path and stable URL source.
- Browser trust is explicitly manual and never bypassed/disabled.
- CA/leaf stay stable across normal recovery.
- Diagnostics expose CA path and certificate identity.

Residual risk: browser trust stores and import UI differ by platform; an
incorrect manual trust decision is outside application enforcement.

### T8: Schema-1 Credential Leak

Threat: deployed schema-1 state points at an attacker-controlled HTTP listener,
and diagnostics/recovery send Basic Auth before migration.

Controls:

- Schema 1 is labeled legacy and never probed.
- The plugin URL/CA resolvers expose only validated schema-2 TLS or schema-3
  transport-discriminated state.
- Migration and explicit stop never signal a live schema-1 PID, even if current
  executable path and argv match the record.
- Matching legacy state may be removed under lock only when its PID is dead.
- Live legacy state is preserved with guidance to use trusted operating-system
  process accounting, terminate the verified process manually, and retry.

Operational limitation: every live legacy process is intentionally left running
rather than risk signaling a reused PID; manual intervention is required.

### T9: Selective Proxy Or Backend Death

Threat: an administrator kills one TLS member and leaves stale state/the other
member alive, or kills the direct backend while its state remains.

Controls:

- TLS reuse requires both exact process identities/listeners, certificate
  identity, and pinned health. Schema-2/3 TLS retains proxy-first cleanup of a
  surviving verified member. For schema-4 TLS, the four broker/backend liveness
  outcomes are explicit: live/live is reusable only with matching broker and
  endpoint proof; broker-live/backend-dead is `unhealthy` and only a locked
  broker cleanup may recover it; broker-dead/backend-live is blocked for manual
  operating-system accounting; both-dead is reconciled only under the fence
  after recorded control-path checks. No outcome adopts an ambiguous role.
- Direct reuse requires the exact backend identity/listener and HTTP health. A
  dead direct backend is replaced as one new generation without adopting a
  replacement listener.
- The public port is reused when available, and valid certificate material is
  retained across TLS recovery and direct operation.
- Local TUI recycles on generation/CA/process mismatch.
- There is no unbounded background watchdog.

Residual risk: repeated selective killing defeats availability; recovery remains
bounded and user-triggered. Broker-dead/backend-live intentionally requires
manual accounting rather than a weaker cross-container signal path.

### T10: PID Reuse, Reboot, Or Forged State

Threat: a stale PID now names another process, boot changed, or state fields are
malformed/tampered.

Controls:

- Validate hostname, boot ID, PID type, liveness/non-zombie state, proc start
  ticks, immutable runtime executable device/inode, current interpreted launch
  and Java source device/inode, exact argv, role semantics, and listener inode.
- Keep native `/proc/<pid>/exe` device/inode authoritative across an on-disk
  replacement, including a ` (deleted)` readlink; refuse to signal a live
  interpreted/source process after its current recorded path is replaced.
- Signal only while the kernel fence is held, after renewing logical ownership,
  opening a pidfd, and revalidating boot/start/runtime inode/argv plus required
  launch/source identities. Dispatch only through that pidfd.
- Prefer leaking an unverifiable process over signaling an unrelated process.
- Missing/malformed/future state is not probed or executed.

Residual risk: same-user malicious state modification and root forgery are not
cryptographically prevented. Filesystem ownership is the cooperative-user trust
boundary.

### T11: Lock Owner Or State Writer Death

Threat: Neovim dies after lock acquisition or after one child starts but before
complete state publication.

Controls:

- Preserve boot-scoped monotonic renewable leases and lock inode ownership for
  diagnostics and dead-owner policy, but make a process-held kernel `flock(2)`
  the authoritative exclusion fence.
- Create, reclaim, publish, renew, and release logical lock metadata under that
  fence. A suspended holder cannot be reclaimed after lease expiry; holder
  death closes the fd and lets a contender proceed.
- Use mode-private fsynced atomic writes.
- Publish a generation-specific launch intent before each backend or proxy
  spawn. For schema-4 TLS, Lua records intent, starts the persistent proxy as a
  control-only broker, and publishes pending authority only after a matching
  receipt proves broker identity, control inode, generation, and backend launch
  intent; the broker then launches the backend. Starting, control-ready,
  activating, activation-failed, running, stopping, blocked, and terminated
  phases have bounded observational or fenced reconciliation outcomes.
- Record generation-specific pending process identities before final state.
- Require renewed current lock ownership under the continuously held fence
  before every pending/state write or removal.
- Before pending cleanup, renew ownership, re-read valid metadata, require the
  caller's generation, and signal only its exact process identities.
- Remove pending metadata only through a renewed-lock helper that re-reads it
  and unlinks only an equal target generation.
- Cleanup signals only identities from freshly re-read validated pending
  metadata, never caller state. A stale owner cannot coexist past reclaim:
  reclaim cannot occur until its kernel fence is released by action or death.
- Final schema-3 state appears only after transport-appropriate ownership and
  health validation; healthy existing schema-2 TLS state may be reused without
  mutation.

Residual risk: schema-2/3 launch cuts retain the existing narrow
authority-transfer window and manual-accounting limitation. Schema-4 removes
backend-only startup by making the already-persistent proxy the broker, but a
missing, mismatched, or unreachable broker/control path preserves state and may
require manual accounting; no new helper or external supervisor is introduced.

Unsupported procfs, flock, pidfd, Python pidfd APIs, or bounded helper startup
fails closed and may require manual recovery. No continuously running helper is
introduced.

### T15: Private Control-Path Replacement, Confusion, Or Exhaustion

Threat: a stale, substituted, symlinked, permissive, wrong-generation, partial,
malformed, replayed, or overloaded Unix control path causes a caller or broker
to authorize lifecycle work for another generation.

Controls:

- The control socket is private state-root IPC, not a public TLS or OpenCode
  route. State-root directories are current-user-owned `0700`; broker-created
  files and socket are current-user-owned `0600`.
- Authority requires no-follow type/ownership/mode/device/inode validation of
  the root and recorded socket, exact generation and protocol matching, and a
  fresh nonce correlation. A socket pathname, nonce, or successful connect alone
  is never authority.
- Each connection carries one strictly framed bounded request and response;
  duplicate/unknown fields, trailing bytes, invalid UTF-8, partial input,
  timeout, peer loss, excess connections, and queued-work overload are rejected
  without mutation.
- Stale-path reclamation occurs only under the fence after bounded contact and
  proof that recorded authority is absent. Live or ambiguous paths are preserved.

Residual risk: the cooperative same-user boundary is not cryptographic. Root can
replace all evidence; ambiguous same-user state remains blocked rather than
adopted or unlinked.

### T16: Broker Handoff, Crash Cut, Or Lost Stop Receipt

Threat: a caller dies around activation/stop commitment, a broker dies after
launching a backend, or the final stop receipt is lost, leaving Lua durable state
behind broker truth.

Controls:

- Lua exclusively owns the fence, lock, shared-state publication/removal, and
  control-path reconciliation. The broker exclusively owns committed schema-4
  backend launch, public service, and stop completion.
- Before commitment, incomplete input and caller loss cause no process mutation.
  After commitment, activation and stop are idempotent broker transitions; a
  caller cannot relaunch, signal, or delete state from a missing response.
- Normal stop closes control acceptance and conditionally unlinks the recorded
  socket inode only after backend death, returns one matching final receipt over
  the already accepted connection, then exits. Lost receipts preserve state until
  a fenced caller proves both recorded roles and the control path absent or
  safely reconciled.

Residual risk: broker-dead/backend-live and any unproved identity remain manual
accounting cases. The design favors a leak over an unsafe signal or cleanup.

### T12: Unsafe Scoped Reload

Threat: reload targets the wrong directory, leaks credentials, interrupts work,
or replaces the shared pair.

Controls:

- Revalidate complete schema-2 TLS or schema-3 transport state and
  transport-appropriate health under lock.
- Resolve current absolute cwd and route every request.
- Refuse busy sessions, permissions, or questions.
- Supply CA/auth/body through protected stdin curl config.
- Validate recreated `/path`, reconnect SSE, and recycle only local TUI.
- Require backend PID, transport, generation, and URL/port unchanged; require
  proxy PID and certificate identity unchanged only in TLS mode.

Residual risk: OpenCode lacks an atomic idle-and-dispose transaction and a TUI
readiness endpoint.

### T13: Utility Child Retains The Lifecycle Fence

Threat: OpenCode version discovery, keytool, Java keystore validation, curl, or
the pidfd helper hangs, resists SIGTERM, floods output, fails to start, or exits
while a callback throws. A blocked Neovim event loop or unreaped child can retain
the authoritative fence and prevent every contender from recovering.

Controls:

- No wait-for-result lifecycle command uses a shell or synchronous wait.
- Protected stdin carries curl configuration and pidfd identity; keytool uses
  `-storepass:file`. Commands, secret content, and full environments are not
  logged.
- Stdout and stderr are each capped at 64 KiB. A five-second child deadline is
  additionally capped by the 30-second lifecycle deadline.
- Timeout and overflow send SIGTERM, then SIGKILL after at most 250 ms. The exit
  callback reaps the child before exactly-once completion and closes every pipe,
  timer, and process handle.
- Certificate files validate in staging before atomic publication. Every error
  flows through async lifecycle cleanup, and callback failure or shutdown
  releases the fence.
- Reload creates the TUI and reconnects opencode.nvim only after releasing the
  fence, then reacquires it and repeats the final generation comparison.

Residual risk: kernel-level uninterruptible sleep can delay even SIGKILL and is
outside application control. The child remains unpublished and no contender is
allowed to bypass a still-live fence holder.

### T14: Direct Loopback HTTP Exposure And Replacement

Threat: in direct mode, a same-host process can observe plaintext loopback HTTP,
receive a Basic Auth header, or capture the public port after the expected
backend dies and receive traffic from stale HTTP clients.

Controls: this is excluded only by the trusted-host direct-profile assumption.
The implementation still binds only to loopback, validates the protected mode
selection and exact backend listener before managed authenticated health, refuses
unknown configured-port conflicts, and never auto-adopts a replacement. It does
not claim CA-pinned identity, relay tuple/inode proof, or stale-client protection
in direct mode. Basic Auth remains available but does not encrypt the header.

Residual risk: this threat is a policy-reportable P0 in the hardened TLS profile
and cannot be accepted there. It is an explicit deployment-owner opt-out only
within the trusted-host assumptions; those assumptions must be reevaluated on
the triggers stated in the deployment-profile section.

## Kill/Replacement Matrix

| Event | Expected result |
| --- | --- |
| Schema-4 broker live, backend live | Reuse only after matching broker evidence and CA-pinned endpoint proof |
| Schema-4 broker live, backend dead | Status is unhealthy; only a fenced, locked broker cleanup may recover it |
| Schema-4 broker dead, backend live | Block and preserve for manual operating-system accounting |
| Schema-4 broker dead, backend dead | Reconcile only under the fence after matching control-path absence or safe reconciliation |
| Schema-2/3 proxy killed | Next operation stops verified backend and creates one new pair |
| Schema-2/3 backend killed | Existing proxy closes streams; next operation stops proxy first and creates one new pair |
| Replacement binds internal before proof | At most fixed preflight; zero client Authorization/body |
| Replacement binds after expected backend death | Zero bytes because PID/start check fails before connect |
| TUI killed | Next operation recreates attach with CA, URL, generation, and cwd |
| Origin Neovim exits | No intentional pair stop; detached pair remains where runtime permits |
| Lock owner suspended | Kernel fence remains held; contenders cannot reclaim or mutate after lease expiry |
| Lock owner killed | Kernel releases the fence; lease/pending recovery is bounded and generation-specific |
| All user processes killed | New MkChad remains lazy; first operation performs bounded recovery |

## Policy Profile Review

On 2026-07-15 the Builder reviewed the two profiles against the accepted
decision in `docs/sprints/single-opencode-server/2/sprint_spec.md`: direct HTTP
remains prohibited and P0-reportable
for hardened multi-user deployments, while trusted-host direct mode is a
documented opt-out with the exclusions, mandatory controls, and reevaluation
triggers above. This review authorizes only subsequent implementation that
preserves those profile boundaries.

## Audit Focus

Audits must prioritize CA/password exposure, public proxy impersonation,
internal takeover before proof, same-connection violations, reconnects after
proof, proc tuple ambiguity, process signaling, lock/pending races, legacy HTTP
credential paths, unbounded handlers/retries, and false healthy diagnostics.
For schema-4 TLS they must additionally trace T15 and T16 controls to evidence:
state-root/socket DAC and inode checks, strict bounded control framing/load,
broker-first launch cuts, the four liveness outcomes, public-quiesce-before-pidfd
ordering, and receipt/lost-receipt reconciliation.
They must also exercise every lifecycle utility-child phase for timeout,
SIGTERM resistance, output overflow, start/nonzero/parse failure, reaping,
callback failure, fence release, and contender progress.
Severity/completion rules are in
`docs/sprints/single-opencode-server/2/audit_policy.md`.

## Residual Risk Acceptance

The accepted residuals are root control, software-only CA-key protection,
manual browser trust, bounded denial of service, next-use rather than continuous
recovery, detached-runtime variability, loss of in-memory work, and the explicit
`SPOS-AUD-P1-004` local-user access exception. Live schema-1 state requiring
trusted manual accounting is a fail-safe operational limitation, not accepted
permission to signal it. Credential
forwarding before exact proof, reconnecting a client stream to a replacement,
probing legacy HTTP with auth, or treating missing schema-4 broker authority as
permission to signal or remove state are not accepted residual risks.
