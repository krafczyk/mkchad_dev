# MkChad Development Workspace

## Purpose

This repository is the parent development workspace for MkChad and its related
OpenCode tooling. Its child repositories are Git submodules. Before changing a
child repository, read and follow the nearest `AGENTS.md` and any authoritative
documents it identifies.

## Child Repositories

- `mkchad/` (`/data0/matthew/Projects/mkchad/mkchad`): the MkChad NvChad-based
  Neovim configuration.
- `msk_containers/` (`/data0/matthew/Projects/mkchad/msk_containers`): container
  images, launch scripts, and runtime integration used by MkChad.
- `opencode.nvim/` (`/data0/matthew/Projects/mkchad/opencode.nvim`): the Neovim
  plugin that connects the editor to an OpenCode server.
- `opencode_sprint_loop/`
  (`/data0/matthew/Projects/mkchad/opencode_sprint_loop`): the Python workflow
  controller for implementation, audit, and CI sprint loops. It includes its
  own Neovim client as a nested Git submodule.
- `opencode-project-reload/`
  (`/data0/matthew/Projects/mkchad/opencode-project-reload`): the standalone
  OpenCode TUI plugin that safely reloads one project instance.

Treat each child as an independent repository. Run Git operations, tests, and
formatters from the child repository they apply to, and do not mix unrelated
changes across children.
When a change spans `opencode-project-reload/` and this workspace, commit the
plugin first and update the parent gitlink last.

## Sprint Documentation

Cross-repository sprint plans, specifications, checklists, and sprint-specific
policies live under `docs/sprints/<sprint-name>/`. Each series keeps its plan at
the series root and its sprint artifacts in numbered subdirectories. Durable
product and repository documentation remains in the child repository it
describes.

## Sprint Selection

This parent repository is the coordination root for development sprints. A
current sprint is selected for one task or sprint-loop invocation; it is not a
repository-wide singleton and must not be inferred from directory order,
numeric order, modification time, or unchecked checklist rows.

Resolve sprint work in this order:

1. Use an explicit selector or narrower sprint scope supplied by the user.
2. Otherwise use a default declared by this file only when exactly one default
   is present.
3. If no default exists and more than one active sprint is listed, ask the user
   to select one before starting sprint implementation or audit work.

Resolve a selector to the exact plan, specification, checklist, policy, and
participating repositories listed below. Keep that resolution fixed for the
invocation and pass it to every Builder, Auditor, CI Fixer, and closeout agent.
Normal Build, Plan, documentation, and review requests do not require a sprint
selection unless the user makes sprint scope relevant.

### Active Sprints

#### `controller-v1/3`

- Plan: `docs/sprints/controller-v1/sprint_plan.md`
- Specification: `docs/sprints/controller-v1/3/sprint_spec.md`
- Checklist: `docs/sprints/controller-v1/3/sprint_checklist.md`
- Durable controller specification and policies:
  `opencode_sprint_loop/docs/v1_final_software_specification.md`,
  `opencode_sprint_loop/docs/threat_model.md`, and
  `opencode_sprint_loop/docs/audit_policy.md`
- Participating repositories: `opencode_sprint_loop/` and, when plugin work is
  in scope, `opencode_sprint_loop/opencode_sprint_loop.lua/`
- Commit order: nested plugin first, controller second, parent workspace last

#### `single-opencode-server/2`

- Plan: `docs/sprints/single-opencode-server/sprint_plan.md`
- Specification: `docs/sprints/single-opencode-server/2/sprint_spec.md`
- Checklist: `docs/sprints/single-opencode-server/2/sprint_checklist.md`
- Sprint policies: `docs/sprints/single-opencode-server/2/threat_model.md` and
  `docs/sprints/single-opencode-server/2/audit_policy.md`
- Participating repositories: `mkchad/`, `msk_containers/`, and
  `opencode.nvim/`
- Commit order: each changed child before the parent workspace

#### `single-opencode-server/3`

- Plan: `docs/sprints/single-opencode-server/sprint_plan.md`
- Specification: `docs/sprints/single-opencode-server/3/sprint_spec.md`
- Checklist: `docs/sprints/single-opencode-server/3/sprint_checklist.md`
- Inherited policies: `docs/sprints/single-opencode-server/2/threat_model.md` and
  `docs/sprints/single-opencode-server/2/audit_policy.md`
- Participating repositories: `mkchad/`, `msk_containers/`, and
  `opencode.nvim/` when focused compatibility tests require plugin work
- Commit order: each changed child before the parent workspace

All three selectors are active, so this workspace has no default sprint. Sprint
1 of `single-opencode-server` is archived historical evidence and is never
selected from its unchecked gates. Select Sprint 2 or Sprint 3 explicitly; their
shared series and repositories do not make either one implicit. Run at most one
sprint loop in this coordination worktree at a time.

## Temporary Files

Anything requiring a temporary directory must use a task-specific directory in
the tree rooted at `/tmp/opencode-mkchad`, such as
`/tmp/opencode-mkchad/<purpose>`. Do not create temporary directories directly
under `/tmp`, under `/var/tmp`, or by using an unscoped `mktemp` default.
