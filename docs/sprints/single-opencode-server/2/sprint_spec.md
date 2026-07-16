# Optional OpenCode Transport Sprint Specification

## Document Status

| Field | Value |
| --- | --- |
| Status | Implementation-ready with zero P0/P1 findings; publication and deployment-only browser/container evidence remain open |
| Decision date | 2026-07-15 |
| Scope | `msk_containers`, MkChad configuration, and verification against `krafczyk/opencode.nvim` |
| Baseline | `msk_containers` `ec5a5be`; MkChad `938c325`; `opencode.nvim` `a383638` |
| Default transport | Existing CA-pinned Java TLS proxy |
| Optional transport | Direct loopback HTTP for an explicitly trusted host only |
| New state schema | Schema 3 with an explicit transport discriminator |
| Archived sprint | `docs/sprints/single-opencode-server/1/` |

## Summary

MkChad currently manages one detached OpenCode backend behind one detached Java
TLS proxy. This sprint adds a protected `tls_proxy` Boolean to
`opencode-server.json`. The default remains `true`, preserving the reviewed
TLS-proxy architecture and all of its endpoint-identity and replacement-listener
controls.

When a user explicitly sets `"tls_proxy": false`, MkChad instead manages one
OpenCode backend directly on the selected public loopback port and publishes an
HTTP URL. This direct mode is a trusted-host opt-out. It is not an alternative
multi-user security control, does not authenticate the server to clients, and
does not protect stale clients from a replacement listener after backend death.

This sprint also repairs explicit stop behavior after selective proxy death.
There remains no watchdog: recovery and cleanup occur only on a subsequent
eligible lifecycle action.

## Accepted Decisions

The decision-maker accepted the following on 2026-07-15:

1. Direct loopback HTTP may be enabled only as an explicit trusted-host opt-out.
2. The config key is `tls_proxy`, is a strict JSON Boolean, and defaults to
   `true` when absent.
3. No environment variable overrides `tls_proxy`; the protected file is the
   authority for this security-sensitive choice.
4. Changing transport while a managed generation exists does not automatically
   replace it. The user must explicitly stop the shared server and restart
   Neovim before starting the other transport.
5. `:OpenCodeStop` will be repaired to stop a still-verifiable backend when its
   proxy is already dead.
6. Proxy and backend recovery remains next-use rather than watchdog-driven.
7. Existing TLS material is retained while direct mode is active so a later
   return to TLS can preserve browser trust when the material remains valid.

## Implementation Gate

No production implementation may begin while the governing threat model and
audit policy prohibit the accepted trusted-host direct profile. The first sprint
mutation after these planning documents must define and review both deployment
profiles in `docs/sprints/single-opencode-server/2/threat_model.md` and
`docs/sprints/single-opencode-server/2/audit_policy.md`.

Those policy changes must state precisely which direct-HTTP threats are excluded
by the trusted-host assumptions, which controls remain mandatory in direct mode,
and that direct HTTP remains a policy-reportable P0 in the hardened multi-user
profile. If that prerequisite cannot be approved, implementation stops and TLS
remains mandatory.

## Security Profiles

### Hardened TLS Profile

`"tls_proxy": true` is the default and the required profile for a host with
untrusted local users or processes.

This profile preserves the archived sprint contract:

- HTTPS with a stable host-specific CA and `127.0.0.1` leaf.
- A distinct automatic internal backend port.
- CA-pinned MkChad, opencode.nvim, attach, and browser clients.
- Fixed unauthenticated backend preflight before client HTTP bytes are read.
- Exact established-tuple and backend socket-inode proof before forwarding.
- No reconnect of a client stream to a replacement backend.
- Basic Auth on the public and directly discoverable internal endpoints when a
  user supplies a password.

The proxy still is not client access control. A strong user-supplied OpenCode
password remains required on a multi-user host because local users can discover
and connect directly to the backend HTTP port.

### Trusted-Host Direct Profile

`"tls_proxy": false` is permitted only when all relevant same-host users and
processes are trusted, or the deployment owner explicitly accepts their access
and endpoint-replacement capabilities. An SSH tunnel may protect traffic between
the remote workstation and host, but it does not restore local endpoint identity
after the tunnel terminates.

Direct mode deliberately gives up:

- TLS encryption on the host loopback hop.
- CA-pinned server identity.
- The proxy's backend tuple/inode proof before client bytes are forwarded.
- Protection against a replacement process that captures the public port after
  backend death and receives traffic from stale HTTP clients.

Basic Auth may still be configured in direct mode, but its header travels over
loopback HTTP. It prevents an ordinary unauthenticated connection from using the
backend; it does not provide transport confidentiality or server authentication.

The protected config file controls who can select the mode. It does not make
direct HTTP safe against an untrusted local user.

## Goals

- Preserve the existing TLS profile as the secure default with no weakened
  validation or test coverage.
- Permit an explicit direct HTTP profile without requiring Java, keytool, CA
  generation, or a second managed process.
- Persist transport mode unambiguously and make downgrade behavior fail closed.
- Prevent multiple Neovim instances with stale opposite settings from flipping
  the shared generation automatically.
- Keep exact process, listener, lock, generation, pending-state, and pidfd
  controls in both profiles.
- Make every managed client and diagnostic transport-aware.
- Repair safe explicit cleanup after proxy-only death.
- Keep all credentials out of state, argv, logs, notifications, repository
  history, and test output.

## Non-Goals

- Making direct HTTP suitable for untrusted multi-user hosts.
- Hiding either port from local process inspection or scanning.
- Preventing privileged loopback packet capture.
- Adding a watchdog, systemd unit, container instance, or automatic background
  restart.
- Generating or forcing an OpenCode password.
- Adding plaintext support to `MkChadTlsProxy.java`.
- Deleting valid TLS material merely because direct mode is selected.
- Changing OpenCode's same-directory multi-TUI behavior.

## Configuration Contract

The protected config remains:

```text
${XDG_CONFIG_HOME:-$HOME/.config}/mkchad/opencode-server.json
```

Example:

```json
{
  "port": 4096,
  "username": "opencode",
  "password": "a-strong-existing-password",
  "tls_proxy": true
}
```

Rules:

- `tls_proxy` accepts only JSON `true` or `false`.
- Absence means `true`.
- Strings, numbers, null, arrays, and objects are invalid.
- The existing regular-file, owner, mode-`0600`, size, JSON-object, supported-key,
  port, username, and password checks remain mandatory.
- Existing non-empty OpenCode environment variables continue to override port,
  username, and password only.
- The setting is loaded when the plugin configuration loads and is process
  cached. Editing the file requires a Neovim restart.
- The live password and config contents never enter lifecycle metadata.

## Topologies

### TLS Proxy Enabled

```text
browser (trusted CA) --------\
opencode.nvim (curl CA) ------+--> HTTPS 127.0.0.1:<public port>
opencode attach (NODE CA) ---/                 |
                                           Java TLS proxy
                                      fixed preflight + tuple proof
                                                  |
                                      HTTP 127.0.0.1:<internal port>
                                                  |
                                           opencode serve
```

### TLS Proxy Disabled

```text
browser ---------------------\
opencode.nvim ----------------+--> HTTP 127.0.0.1:<public port>
opencode attach -------------/                 |
                                           opencode serve
```

In direct mode the backend owns the public port. There is no managed internal
port, Java process, proxy listener, CA requirement, or TLS readiness phase.

## State Schema 3

Schema 3 adds an explicit `transport` value:

```text
tls-proxy
loopback-http
```

### Common Invariants

Schema 3 retains every schema-2 common top-level field: `hostname`, `generation`,
`host`, `port`, `url`, `port_source`, `started_at`, `cwd`, `boot_id`, and
`backend`. It adds `transport`. Meanings and validation remain unchanged except
where the transport-specific rules below explicitly alter URL scheme, backend
port relationship, proxy presence, CA path, or certificate identity.

The backend record retains exact argv, PID, start time, launch and runtime
executable device/inode identity, listener port, version, and protected log
path. Automatic-port persistence continues to use `port_source`; diagnostics
continue to use `cwd` and the common identity fields.

No state contains Basic Auth credentials, provider credentials, config contents,
CA private material, or keystore passwords.

### TLS State

A `tls-proxy` state requires:

- An `https://127.0.0.1:<public-port>` URL.
- A proxy process record whose listener equals the public port.
- A backend port distinct from the public port.
- The existing exact Java argv, runtime/source identity, listener, CA path, and
  certificate identity invariants.

### Direct State

A `loopback-http` state requires:

- An `http://127.0.0.1:<public-port>` URL.
- No proxy process record.
- No active CA path or certificate identity fields.
- `backend.port == state.port`.
- Backend argv that binds exactly `127.0.0.1:<public-port>`.

### Pending State

New pending metadata is schema 3 and includes `transport`. Proxy absence is no
longer ambiguous: in `tls-proxy` pending state it means startup has not reached
the proxy phase; in `loopback-http` it is the only valid role layout.

Pending writes, removals, cleanup, and process signaling retain the existing
generation-conditional kernel-fence and pidfd requirements.

Before each role spawn, `launch.json` records a generation, boot, role, port,
and, once known, PID. A later startup remains blocked while the intent's role is
not covered by matching validated pending metadata. Under the fence, exact
same-generation/boot/role/port/PID coverage transfers authority to pending
metadata and permits intent removal; unmatched intent requires trusted manual
process accounting.

### Compatibility And Downgrade

- Existing valid schema-2 state is interpreted as `tls-proxy`.
- A healthy schema-2 TLS generation may remain unchanged while the setting is
  enabled.
- New generations use schema 3.
- Schema-2 state is never reinterpreted as direct mode.
- Existing schema-1 state remains legacy and non-signalable while live.
- Older MkChad versions reject schema 3 as a future schema and must not remove or
  signal it. A user must explicitly stop the new generation and confirm no
  launch intent before downgrade.

## Lifecycle Contract

### Mode Agreement

Before reuse, ensure compares the process-cached config mode with trusted state:

- Matching healthy mode may be reused.
- Opposite mode fails with instructions to run `:OpenCodeStop`, restart Neovim,
  and start again.
- An ensuring action never changes transport automatically.
- `:OpenCodeStop` follows trusted state rather than the current requested mode,
  so it remains able to stop the existing generation during a transition.
- A stale Neovim with the opposite cached mode cannot replace a newer generation.

### Common Startup

Under the existing kernel fence and logical lock, startup:

1. Validates or cleans existing state, matching pending metadata, and any
   exactly covered launch intent.
2. Selects the exact configured public port or the existing automatic policy.
3. Resolves the OpenCode executable through the bounded subprocess runner.
4. Publishes a role-specific launch intent before spawning the process.
5. Launches the backend, captures immutable identity, and transfers authority to
   pending metadata before proving its listener.
6. Repeats the launch-intent transfer for the TLS proxy when enabled.
7. Requires transport-appropriate authenticated health.
8. Publishes complete state only after all role, listener, health, and lock checks
   succeed.

### TLS Startup

TLS startup preserves the archived sequence: certificate validation/generation,
distinct internal port, backend launch and proof, Java launch and proof, pinned
HTTPS health, then state publication.

### Direct Startup

Direct startup:

- Skips Java and proxy-source validation.
- Skips keytool and certificate validation/generation.
- Launches OpenCode directly on the selected public port.
- Proves that exact backend PID owns the public listener before any health check
  or state publication.
- Uses loopback HTTP health only for a fully validated direct pending generation.
- Fails without fallback when an exact configured port is occupied.

Direct startup must not adopt a helper or replacement process that binds the
assigned port.

## Death, Stop, And Recovery

There is no watchdog in either mode. State changes only during an explicit or
client-triggered lifecycle operation.

### Proxy Death

When a TLS proxy dies:

- Existing TLS, browser, TUI, REST, and SSE connections fail.
- The backend may remain alive and state remains on disk.
- `:OpenCodeInfo` remains observational and does not restart anything.
- `:OpenCodeReload` does not start an unhealthy service.
- The next ensuring operation validates the partial generation, stops the
  surviving backend through pidfd under the fence, removes matching state, and
  launches a new TLS generation.
- `:OpenCodeStop` performs the same verified partial cleanup without launching a
  replacement.
- Recovery never adopts the surviving backend as direct mode.

Public-port reuse remains conditional on availability. Valid certificate
material remains stable across ordinary recovery.

### Backend Death

- In TLS mode, the next ensuring action replaces both roles.
- In direct mode, the next ensuring action replaces the backend generation.
- A configured public-port takeover fails without fallback.
- Automatic mode may use its bounded fallback policy where already permitted.
- No client stream is reconnected by MkChad.

### Explicit Stop

Stop remains proxy-first for a complete TLS generation. For a dead proxy with a
live backend, it verifies the backend independently against trusted state and
signals only through pidfd. Direct mode stops only the verified backend. State
is removed only after every matching role is confirmed dead.

Termination preserves the existing bounded sequence: pidfd-open, fresh identity
validation, SIGTERM, bounded exit wait, SIGKILL when still live, and a final
bounded exit check. If identity validation, helper execution, signal dispatch,
TERM/KILL escalation, or final death confirmation fails, explicit stop reports
the failure and preserves matching state for trusted later recovery. It must not
orphan a live process by removing signal authority. An already-dead exact role is
a terminal success and requires no signal.

## Certificate Behavior

Direct mode neither validates nor creates TLS material during startup. Existing
protected material is retained untouched. Re-enabling TLS validates and reuses
it when valid or regenerates it through the existing atomic process when
invalid.

No direct-mode diagnostic may imply that retained material is active.

## Client Integration

MkChad exposes only a fully validated state URL:

- TLS state returns HTTPS and its CA path.
- Direct state returns HTTP and a nil CA path.

`opencode attach` receives `NODE_EXTRA_CA_CERTS` only in TLS mode. TUI identity
and recycling include transport, URL, generation, and active certificate
identity where applicable.

The existing opencode.nvim fork already permits HTTP URLs and an optional
request-time CA resolver. No production fork change is expected unless tests
identify a missing transport-neutral behavior. REST, SSE, credentials, payloads,
and directory headers remain protected from argv exposure in both modes.

## Reload And Diagnostics

Reload remains scoped to the current directory and never changes transport.
Invariant checks always require backend PID, generation, URL, port, and
transport; proxy PID and certificate identity are additionally required only in
TLS mode.

`:OpenCodeInfo` reports:

- Requested and active transport.
- Mode mismatch and required stop/restart action.
- HTTP or HTTPS URL.
- Active backend PID/port/log.
- Proxy PID/port/log and CA identity only when active.
- Retained-but-inactive TLS material without probing it in direct mode.
- Fresh health only after transport-appropriate state and listener validation.

## Documentation And Policy Work

Before production implementation begins,
`docs/sprints/single-opencode-server/2/threat_model.md` and
`docs/sprints/single-opencode-server/2/audit_policy.md` must define two
deployment profiles. Existing no-direct-
HTTP P0 rules remain mandatory for the hardened multi-user profile. The trusted-
host profile must state that direct HTTP is an explicit opt-out from those
controls, not a closure or mitigation of the associated threats.

The archived TLS-only documents remain immutable historical evidence. Current
documents and README diagrams must describe both topologies without rewriting
the meaning of archived test evidence.

## Verification Plan

Focused development begins with strict config and state-validation tests, then
expands through lifecycle and cross-client integration.

Required coverage includes:

- `tls_proxy` absent/true/false and rejection of every non-Boolean JSON type.
- Existing schema-2 TLS state, schema-3 TLS state, schema-3 direct state, pending
  state, malformed cross-mode fields, future schemas, and rollback behavior.
- Default TLS lifecycle regressions with no weakened Java, certificate, tuple,
  listener, lock, pidfd, or protected-curl checks.
- Direct startup with one backend, HTTP URL, no Java/keytool invocation, no new
  TLS material, no CA client setting, and exact public listener ownership.
- Direct and TLS Basic Auth behavior without exposing credentials.
- Configured and automatic port conflict behavior in both modes.
- Explicit TLS-to-direct and direct-to-TLS stop/restart transitions.
- Refusal by stale opposite-mode Neovim instances.
- Proxy-only death before recovery, ensuring-action recovery, observational
  info/reload, public-port and CA behavior, and repaired explicit stop.
- Direct backend death and replacement-port capture behavior.
- Concurrent startup convergence in both modes.
- Reload and TUI reuse/recycle invariants in both modes.
- opencode.nvim HTTP/no-CA and HTTPS/pinned-CA REST/SSE behavior.
- Full serial MkChad lifecycle, startup-race, pending-generation, fence, lock,
  certificate, subprocess, proc-scanner, Java proxy, and pidfd suites.

## Acceptance Criteria

- Omitting `tls_proxy` preserves the existing TLS behavior.
- Enabled mode retains all archived security controls and passes its full suite.
- Disabled mode starts no Java/keytool process and exposes exactly one validated
  OpenCode loopback HTTP listener.
- Direct mode is clearly diagnosed and documented as trusted-host-only.
- State and pending metadata unambiguously encode transport.
- Existing schema-2 TLS generations remain safe and usable.
- Old versions fail closed on schema 3.
- Mode mismatch never causes automatic generation flipping.
- Configured occupied ports never fall back.
- Direct clients receive no CA setting; TLS clients remain CA-pinned.
- Proxy-only death is recoverable on next ensure and explicitly stoppable without
  signaling an unverified process.
- Failed or incomplete partial-pair stop preserves matching state until every
  managed role is confirmed dead.
- No password, provider credential, prompt, body, SSE data, CA private material,
  or config content enters argv, state, logs, notifications, repository history,
  or test output.
- A fresh independent audit reports no unresolved policy-reportable P0/P1 issue
  within either profile's declared assumptions.

## Limitations

- Direct mode is insecure against an untrusted local replacement listener and
  must not be represented as hardened multi-user operation.
- Basic Auth does not encrypt direct-mode loopback traffic.
- Root, privileged packet capture, a compromised kernel, and root-modified state
  remain outside enforceable scope.
- Recovery is next-use and may lose in-memory work.
- Multiple Neovim instances must restart after config changes; mismatch refusal
  prevents automatic flapping but cannot update stale process memory.
- Browser bookmarks are scheme-specific; switching transport changes HTTPS to
  HTTP or vice versa even when the port remains stable.

## Publication Gate

1. Publish opencode.nvim `59ad0b8` and verify that exact commit is remotely
   reachable.
2. Only then publish the MkChad revision that pins `59ad0b8`; repeat the pinned
   REST, SSE, and TLS integration checks from clean remote clones.
3. Publish coordination evidence after both implementation revisions are
   reachable. Never update the live MkChad configuration to an unpublished pin.

## Rollback

1. Stop any verified schema-3 generation with the implementation that created
   it.
2. Remove `tls_proxy` from `opencode-server.json`; the archived parser rejects
   the key even when its value is `true`.
3. Restart Neovim. If rollback verification is required, start a TLS generation
   and verify HTTPS, CA identity, both process roles, and authenticated health.
4. Stop that verified TLS generation again with the schema-3 implementation.
5. Confirm no live managed role, unresolved `launch.json` intent, or complete or
   pending schema-3 metadata remains.
6. Only then downgrade MkChad. Older code must never be asked to adopt, remove,
   or coexist with live schema-3 state.
7. Preserve TLS material and logs for diagnosis; never expose config or password
   content.
