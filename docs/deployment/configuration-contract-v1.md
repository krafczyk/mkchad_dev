# MkChad Configuration Contract v1

Schema-2 component deployment and host configuration are separate phases. A
successful `latest-result.json` has `configuration_handoff.state` set to
`configuration-pending`. Its configuration-owned component entries are the
explicit repository and commit pins from `deploy/release.json`, not derived
from parent or nested Gitlinks.

`msk_containers`, `container_tools`, `mkchad`, and `opencode-db` are deployed checkouts.
`opencode` is embedded in the verified SIF. `opencode.nvim`,
`opencode-project-reload`, and `compound-engineering` remain
configuration-owned; the deployer does not install them.

The deployer preserves only MkChad's ignored `opencode-server.json` and
`lazy-lock.json`, and it never reads either file. It never reads or mutates
`config_files`. Review and apply the target host's configuration independently,
then explicitly activate the managed service only after configuration and the
reported release pins agree.

Historical schema-1 deployment receipts and recovery records do not participate
in this configuration contract.

Schema-2 results report the selected host source commit through component
evidence and the independently validated image digest. They contain no package
archive or host/container compatibility role. Configuration must not infer a
package path, archive location, image-internal identity, or token from them.
