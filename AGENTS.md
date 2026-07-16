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

Treat each child as an independent repository. Run Git operations, tests, and
formatters from the child repository they apply to, and do not mix unrelated
changes across children.

## Sprint Documentation

Cross-repository sprint plans, specifications, checklists, and sprint-specific
policies live under `docs/sprints/<sprint-name>/`. Each series keeps its plan at
the series root and its sprint artifacts in numbered subdirectories. Durable
product and repository documentation remains in the child repository it
describes.

## Temporary Files

Anything requiring a temporary directory must use a task-specific directory in
the tree rooted at `/tmp/opencode-mkchad`, such as
`/tmp/opencode-mkchad/<purpose>`. Do not create temporary directories directly
under `/tmp`, under `/var/tmp`, or by using an unscoped `mktemp` default.
