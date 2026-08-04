# MkChad v2 Guidestar

## Purpose

MkChad v2 is a distributed programming-work system for environments where
interactive access, compute capacity, and long-running processes are temporary.
It should make a remote work environment feel durable even when login sessions,
compute allocations, network paths, clients, or entire nodes disappear.

This document is the guidestar for the sequence of plans that will build MkChad
v2. Individual plans should deliver narrow, useful increments while preserving
a credible path toward this complete product. Aspirational capabilities here are
not acceptance criteria for the first increment.

## Why MkChad v2 Exists

MkChad v1 proved the value of a portable, containerized editor environment, but
its surrounding operating model remains manual and fragile:

- Deployment is brittle and requires coordinated setup across several
  components.
- Starting work requires manual SSH connections, tunnels, tmux sessions, and
  services.
- Login-node limits, compute-node time limits, crashes, and network loss destroy
  operational state and require manual reconstruction.
- Editor configuration, host dotfiles, and encrypted secrets have separate and
  sometimes conflicting management models.
- A user must understand the topology and perform the recovery instead of the
  system accounting for it.

MkChad v2 should turn those procedures into an explicit, inspectable, and
recoverable system without replacing the Unix and HPC tools that already work.

## Product Vision

A user starts a commander client on any suitable personal device, selects a
known computing environment, and sees the managers, workers, projects, services,
and scheduler capacity available through it. The client establishes whatever
SSH hops, terminal attachments, and port forwards are necessary. The user
chooses the work they want rather than manually constructing the route to it.

When no remote management process is available, the client can authenticate to
the site and bootstrap one. A site manager can discover existing workers, start
new workers, cooperate with managers on other reachable nodes, and use Slurm to
obtain compute capacity. A compute allocation can host its own manager and one
or more workers.

Each worker represents a durable work-set rather than merely a process. It owns
the intended environment, projects, tmux topology, declared services, and
connection metadata for that work. Multiple clients can connect to the same
worker for different purposes, such as a terminal from a workstation and an
OpenCode web interface from a phone.

Before temporary resources disappear, workers checkpoint enough declarative
state to recreate the useful workspace. After a loss, the user can select the
previous work-set, allocate replacement capacity, recover its workspace, and
reconnect without manually repeating the original setup.

## System Roles

### Commander Client

The client connects personal-device resources to remote MkChad resources. It is
responsible for user interaction, authentication handoff, SSH path construction,
terminal attachment, and local presentation of forwarded services.

The same core client should support command-line and terminal interfaces and a
local web API or GUI. It should be able to run on a workstation or in a Linux
environment such as Termux. Browser access is mediated by a trusted client; a
browser is not expected to establish the SSH control path itself.

### Commander Manager

A manager accounts for MkChad services reachable from a particular node. It can
discover and start workers, communicate with other managers, broker client
access, reconcile stale state, synchronize configuration, and expose scheduler
operations appropriate to the host.

Managers are reconstructible coordinators, not irreplaceable authorities. They
may remain available for efficient discovery and event delivery, but the system
must tolerate their routine termination. A client must be able to perform a
bounded one-shot operation over SSH to inspect, validate, restart, or replace a
manager.

### Commander Worker

A worker owns one work-set and its live runtime. It manages the work-set's
environment, tmux sessions, projects, services, web portals, and recoverable
workspace description. It reports observed state separately from desired state
and never treats stale process identifiers as sufficient proof of ownership.

Workers can request or initiate additional workers through managers when a task
needs more resources. A worker tied to a Slurm allocation remains subject to the
allocation's lifetime; its durable identity and recovery state must outlive that
particular execution.

## Guiding Principles

### Build On Existing Tools

MkChad commander is primarily a state-accounting, orchestration, and
communication layer. SSH, OpenSSH forwarding, tmux, mosh, Slurm, containers,
and ordinary Unix process controls should continue to perform the jobs they are
good at. Commander should coordinate them through narrow, machine-readable
interfaces rather than reimplementing them.

### Bootstrap From SSH

A newly encountered HPC host may provide only SSH and basic Unix tools. The
core system must not require administrator installation, a preinstalled language
runtime, a container runtime, a public relay, or an overlay network. A client
should be able to upload one self-contained commander executable and begin a
versioned control exchange over SSH.

SSH is the required cross-host trust and transport foundation. Site-specific
capabilities may permit more efficient paths, but they must not become universal
prerequisites.

### Prefer Lazy, Recoverable Authorities

Persistent managers and workers are useful for concurrent clients, events, and
long-running operations, but persistence is an optimization rather than an
assumption. Every authority needs an explicit identity, bounded lifecycle, and
reconciliation path. Losing a manager should reduce availability temporarily,
not destroy the work-set or make its state unknowable.

### Recover Intent, Not Process Images

MkChad v2 does not pursue transparent restoration of arbitrary process memory,
file descriptors, or TTY kernel state. Its portable recovery unit is workspace
topology and intent:

- tmux sessions, windows, and panes;
- project and pane working directories;
- environment and configuration selection;
- declared services and their startup commands;
- selected command or task restart instructions; and
- enough operation history to identify completed, interrupted, and uncertain
  effects.

Later capabilities may preserve shell history, terminal scrollback, editor
sessions, and service-specific checkpoints, but those are enhancements to
declarative recovery rather than a return to CRIU-style process restoration.

### Separate Desired, Observed, And Historical State

Persisted state is evidence, not live authority. The system must distinguish
what the user requested, what a manager or worker currently observes, and what
previous operations recorded. Recovery must handle process death, PID reuse,
concurrent startup, stale state, malformed or future state versions, partial
operations, network interruption, and outcomes that remain unknown.

Operations that affect user work or process ownership must be bounded,
idempotent where possible, and explicit when safe reconciliation is impossible.
The system must never guess in a way that could corrupt unrelated work, signal
an unrelated process, or connect a client to an untrusted endpoint.

### Treat HPC Variability As A Capability Model

HPC sites differ in login policy, network reachability, filesystems, scheduler
configuration, containers, process limits, and compute-node access. Commander
should discover capabilities and apply host configuration instead of assuming
one topology. Shared filesystems may be NFS, Lustre, or GPFS and must not be
treated as though they have local ext4 semantics.

Slurm integration should expose useful scheduler state and operations without
hiding queueing or allocation lifetime. The user should be able to request a
commander allocation, observe its queue and startup progress, select the
resulting compute manager, and then create or recover workers there.

### Make Configuration Layered And Deployable

Worker behavior is configured through a shared base plus site-, host-, role-,
and work-set-specific layers. Configuration source control and remote deployment
are separate concerns: host-specific Git branches should not be the runtime
merge mechanism.

The client should render selected layers and authorized secrets into an
immutable, identifiable deployment bundle. Managers and workers consume and
report that bundle rather than independently inventing merges. Secret material
must remain encrypted outside its authorized use boundary and must not leak into
logs, state snapshots, command lines, or repository history.

### Keep Interfaces Replaceable

The client, manager, and worker need stable, versioned control contracts that do
not depend on a particular UI. CLI, TUI, editor, local web, and future clients
should consume the same product model. Human-readable status is useful, but
automation must use structured interfaces rather than parse presentation text.

## Current Foundational Direction

The leading implementation direction is one Rust multi-call binary that can act
as client, manager, or worker. Rust offers native Unix process and terminal
control, practical self-contained deployment, and stronger memory and
concurrency safety than a C++ kernel for lifecycle code where cancellation and
ownership errors have high consequences.

Cross-host control should initially use a framed, versioned protocol over SSH
standard input and output. Local peers may use private Unix-domain sockets.
OpenSSH remains responsible for terminal transport, jump hosts, and port
forwarding. Remote HTTP is not part of the required control plane; web and TUI
frontends attach to a local commander client API.

This direction is a constraint for the first plans, not an assertion that every
technology choice is permanently closed. A later plan may revise it when real
host evidence demonstrates that it cannot meet a supported environment.

## Delivery Strategy

MkChad v2 will be built as a sequence of independently useful vertical slices.
Each plan should state which part of this guidestar it advances, preserve stable
contracts already in use, and avoid pretending that deferred capabilities are
implemented.

The first end-to-end slice should connect a client to an existing worker. The
user selects a configured host and worker; commander discovers and validates the
worker, establishes the terminal path, and creates the requested service
forwards. Worker creation may remain manual for this slice.

Likely later slices include:

1. Remote manager bootstrap and version negotiation.
2. Worker creation and lifecycle management.
3. Layered configuration bundle rendering and synchronization.
4. Slurm discovery, job submission, queue presentation, and compute-manager
   startup.
5. Durable workspace snapshots and recovery on replacement capacity.
6. Multi-client access and local browser gateways.
7. Manager-to-manager coordination and worker-initiated expansion.

This sequence is directional rather than a fixed roadmap. Plans should be
ordered by demonstrated value, operational learning, and the dependencies
needed to preserve safe recovery.

## Long-Term Success

MkChad v2 succeeds when a user can move among personal devices and temporary
HPC resources while treating a work-set as durable:

- A new site can be reached and bootstrapped from a client without manual
  component installation.
- Available workers and scheduler capacity are visible without reconstructing
  topology by hand.
- Terminal and web-service connections are established from user intent rather
  than manual tunnel commands.
- Multiple clients can safely reach the same worker for different interfaces.
- Loss of a client, manager, worker process, compute allocation, or node has a
  bounded and understandable recovery path.
- A replacement worker can recreate the useful workspace topology from durable
  state.
- Shared and host-specific configuration behaves as one coherent system while
  secrets retain appropriate boundaries.
- Failures are reported as observable state, not hidden behind indefinite
  retries or optimistic guesses.

The desired experience is not that remote systems never fail. It is that
failure no longer forces the user to remember and manually replay the machinery
of their programming environment.
