---
name: MkChad v1
last_updated: 2026-08-17
---

# MkChad v1 Strategy

## Target problem

Developers working across personal machines and HPC systems must manually reconstruct SSH routes, tunnels, tmux sessions, services, and work state when clients or temporary resources disappear. Site variability and short-lived compute make that reconstruction difficult to standardize.

## Our approach

Extend MkChad v1 through independently useful commander vertical slices that build on SSH, tmux, Slurm, and existing lifecycle behavior rather than requiring a big-bang v2 rewrite. Roll out only what proves useful, preserve bounded and safe failure behavior, and let each slice inform the next.

## Who it's for

**Primary:** Developers moving among personal machines and temporary HPC resources - they are hiring MkChad to keep remote work accessible and recoverable without manually reconstructing its topology.

## Tracks

### Connection and multi-client access

Make existing workers discoverable and connectable from personal devices, then support concurrent terminal and local browser clients.

_Why it serves the approach:_ It delivers immediate value before Commander owns remote lifecycle or recovery.

### Worker lifecycle and recovery

Add remote manager bootstrap, worker creation and lifecycle management, and durable workspace recovery on replacement capacity.

_Why it serves the approach:_ It progressively removes manual reconstruction while preserving independently useful stages.

### HPC orchestration and coordination

Add Slurm discovery and allocation workflows, compute-manager startup, manager-to-manager coordination, and worker-initiated expansion.

_Why it serves the approach:_ It extends proven single-node behavior into temporary and distributed HPC capacity only after the local control model works.

## Not working on

- Layered configuration bundles, configuration synchronization, and Commander-managed secret delivery.
