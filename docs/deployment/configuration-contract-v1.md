# MkChad Configuration Contract v1

Component deployment and host configuration are separate phases. A successful
deployment receipt with `configuration_handoff.contract_version` equal to `1`
means the release components and image were verified and installed, but host
configuration may still require review and application. Deployment does not
open or modify `config_files`, and it leaves the managed OpenCode service
stopped.

## Release Handoff

The receipt's `configuration_handoff` is the machine-readable handoff. Its
`components` entries contain the repository and gitlink-derived revision for
each configuration-owned release component. The release dispositions have
these meanings:

- `msk_containers` and `mkchad` are deployed to their declared named-root
  destinations.
- `opencode` is supplied by the verified SIF rather than a host checkout.
- `opencode.nvim`, `opencode-project-reload`, and `compound-engineering` are
  configuration-owned. Use their receipt revisions when reviewing the host's
  plugin and package state; deployment does not install or update them directly.
- `opencode_sprint_loop` is frozen and disabled. Do not install or migrate it as
  part of this release.

The `mkchad` checkout preserves `opencode-server.json` as a configuration-owned
path and `lazy-lock.json` as generated plugin state. The server configuration
may be absent, the expected legacy link, or a current-user-owned mode-`0600`
regular file. The generated lock may be absent or a current-user-owned regular
file with mode `0600` or `0644`. Deployment reads neither file and requires the
selected MkChad tree to ignore rather than own both paths.

## Configuration Phase

The development workspace may include `config_files` as a host-branch
submodule for inspection and independent maintenance. It is not a release
component and is excluded from release validation, deployment, receipts, and
the configuration handoff.

Use the canonical `github.com/krafczyk/config_files` repository on the branch
matching the target host's short hostname. Its `config_files_scripts` gitlink
must be checked out at the exact revision recorded by that repository. Review
both repositories before applying changes; do not use the release deployment
command to switch branches, update the scripts gitlink, or modify host
configuration.

From the verified host branch, run:

```bash
config_files_scripts/create_links.sh
config_files_scripts/materialize_config_files.sh apply --identity "$AGE_IDENTITY"
config_files_scripts/materialize_config_files.sh verify --identity "$AGE_IDENTITY"
```

The link command owns ordinary dotfiles and excludes every manifest target. The
materialization commands apply exceptional regular files and require a clean
verification result. Then:

1. Compare MkChad's observational component status with the deployed receipt,
   including the configuration-owned revisions.
2. Start the managed service explicitly only after configuration and component
   status agree with the selected release.

`AGE_IDENTITY` is an externally provisioned current-user-owned mode-`0600`
identity file. Do not place the identity or recipient values in the release
manifest, receipt, command output, or tracked configuration. Capture back into
the host branch is always deliberate and additionally requires both host and
recovery recipients as documented by `config_files_scripts`.

## Interrupted Materialization

Routine `apply`, `verify`, and `capture` calls must not assert process
quiescence. If an interrupted materialization journal remains after supervisor
or host loss, independently inspect same-user host and scheduler/session
evidence, terminate or await matching work, and verify that no related action
remains. Only then may `apply` or `capture` be retried with
`--confirm-processes-quiesced`. The resulting recovery receipt records that as
an operator assertion rather than machine-observed cleanup. If quiescence cannot
be established, leave the operation blocked and preserve its evidence.
