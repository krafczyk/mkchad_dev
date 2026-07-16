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
   [threat model](2/threat_model.md), and [audit policy](2/audit_policy.md) are
   the active sprint documents.

Sprint 2 supersedes Sprint 1 for current implementation decisions. Sprint 1
remains historical evidence and must not be updated to describe later optional
transport behavior.
