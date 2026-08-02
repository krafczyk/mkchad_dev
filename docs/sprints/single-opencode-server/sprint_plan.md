# Single OpenCode Server Sprint Plan

## Purpose

This series develops MkChad's persistent OpenCode server integration across the
MkChad configuration, `msk_containers`, and `opencode.nvim` repositories.

## Sprints

1. [TLS-only baseline](1/sprint_spec.md): establish a persistent backend behind
   a CA-pinned Java TLS proxy. Its [checklist](1/sprint_checklist.md) preserves
   the implementation and verification record for this superseded baseline.
2. [Optional transport](2/sprint_spec.md): retain TLS proxying as the default
   while adding an explicit trusted-host direct HTTP mode and repairing
   selective proxy-death cleanup. Its [checklist](2/sprint_checklist.md),
   [threat model](2/threat_model.md), and [audit policy](2/audit_policy.md)
   preserve its completed 2026-07-17 implementation and verification record.
3. [Standalone lifecycle command](3/sprint_spec.md): extract the reviewed
   detached-server lifecycle behind an installed `mkchad-opencode-server`
   command that can run without launching MkChad and that MkChad invokes through
   opencode.nvim's external lifecycle hook. Its
   [checklist](3/sprint_checklist.md) tracks implementation, while Sprint 2's
   threat model and audit policy remain authoritative.

Sprint 2 supersedes Sprint 1 for current implementation decisions. Sprint 1
remains historical evidence and must not be updated to describe later optional
transport behavior.

Sprint 2 completed on 2026-07-17. Sprint 3 is the active next increment and
preserves Sprint 2's transport, state, process-safety, and security contracts;
Sprint 2's threat model and audit policy remain authoritative for that inherited
scope.
