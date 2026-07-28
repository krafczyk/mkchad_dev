# Compound Engineering Threat Model

## Status

- Product stage: single-user proof of concept.
- Deployment: a normal local user account on a general-purpose workstation.
- Current users: the maintainer is the only known actual user.
- Primary harness: OpenCode.
- Primary model path: OpenAI-hosted models are used almost exclusively.
- Last reviewed: 2026-07-27.

This document governs security prioritization for the Compound Engineering fork
in `compound-engineering/`. It intentionally describes the system that is
actually deployed and tested, not a hypothetical multi-user or production
service.

## Scope

Compound Engineering is installed and run by one intended operating-system user
to assist with that user's local development work. It may run on an ordinary
user workstation and interact with local repositories, local OpenCode state,
configured model providers, and user-authorized command-line tools.

The supported and actively tested product path is the **OpenCode harness**. The
maintainer uses **OpenAI-hosted models almost exclusively**. Other model providers
may be exercised occasionally, but they are exploratory unless explicitly
promoted into the supported profile.

Claude Code, Cursor, Codex CLI, Gemini, Grok, Pi, and other converted harnesses
remain compatibility targets, not current security-assurance targets. Their
presence in the repository does not imply that their live authentication,
sandboxing, process supervision, provider identity, or platform-specific
behavior has been validated for this proof of concept.

## Security Objective

The objective is to keep the maintainer's normal OpenCode workflow safe from
realistic mistakes and untrusted task content without claiming isolation from
the maintainer's own account or machine.

The proof of concept should:

- avoid unintended destructive changes to the selected repository;
- avoid placing credentials in prompts, model output, logs, result artifacts,
  or repository history;
- keep model and recipient selection consistent with the maintainer's explicit
  OpenCode routing configuration;
- treat repository content, fetched content, and model output as untrusted data,
  not authority to expand scope, permissions, recipients, or mutation rights;
- fail clearly when the supported OpenCode path cannot preserve a required
  routing or authorization decision; and
- bound ordinary subprocess, retry, temporary-state, and cleanup failures so a
  failed run remains recoverable by the sole user.

## Assets

### Confidentiality

- Model-provider credentials and local authentication state.
- Prompts, source code, diffs, review artifacts, and private repository content.
- Local paths, configuration, session data, and model responses.

### Integrity

- The selected repository and its Git history.
- OpenCode routing intent, selected recipient, and model attribution.
- Compound Engineering plans, results, checkpoints, and review findings.
- User-owned Compound Engineering configuration.

### Availability

- The maintainer's ability to continue using OpenCode and the repository after a
  failed or interrupted Compound Engineering run.
- Bounded cleanup of processes and temporary artifacts started by the supported
  path.

## Trust Assumptions

- The maintainer controls and trusts the operating-system account, host, kernel,
  filesystem, OpenCode installation, Compound Engineering installation, and
  explicitly configured OpenAI provider endpoint.
- Processes running as the same operating-system user are cooperative. They may
  race, crash, or leave stale state accidentally, but they are not modeled as
  malicious local adversaries.
- Root, administrators, the kernel, and host-management software are trusted for
  confidentiality and integrity. They can bypass every local control.
- The maintainer may open repositories containing untrusted prose, prompts,
  scripts, configuration, or dependencies. Merely reading such a repository must
  not grant its content higher authority. Executing repository code remains an
  explicit user/workflow decision.
- Model output is untrusted until the owning workflow validates it. Model
  instructions cannot mint user authority.
- User-selected OpenCode and OpenAI configuration is trusted input. Protecting
  the user from deliberate same-account modification of their own configuration
  is not a security boundary.
- Mode `0700`/`0600` files and private temporary directories protect against
  accidental disclosure and other OS users where the platform enforces them;
  they do not protect against another malicious process running as the same
  user.

## In-Scope Threats

### T1: Unintended Repository Mutation

An agent, model response, interrupted workflow, or malformed task causes changes
outside the user-authorized repository scope or integrates unverified output.

Required posture: OpenCode workflows preserve explicit mutation boundaries,
inspect changes before integration, and fail without discarding unrelated user
work.

### T2: Prompt Or Repository Authority Injection

Repository text, fetched content, generated findings, or model output attempts to
change recipients, permissions, scope, routing policy, or workflow authority.

Required posture: treat these inputs as evidence or task data. Only the user,
active project instructions, and authenticated Compound Engineering/OpenCode
configuration can grant authority.

### T3: Credential Disclosure

Credentials or authentication files enter prompts, logs, result artifacts,
workspace changes, subprocess arguments, or model-visible files.

Required posture: minimize inherited environment and authentication material,
redact known secrets, and fail rather than deliberately stage readable
credentials for model-controlled code on the supported OpenCode path.

### T4: Wrong OpenCode Recipient Or Model Attribution

The supported OpenCode path sends material to a recipient other than the one the
maintainer selected, silently weakens a required route, or reports an
unverified model as verified.

Required posture: preserve explicit routing precedence, disclose meaningful
fallback, and do not claim receipt-supported identity without receipt evidence.

### T5: Stale Or Corrupt Workflow State

An interrupted or overlapping run leaves stale locks, result files, processes,
or checkpoints that a later OpenCode run mistakes for current successful work.

Required posture: bind state to the current run where practical, reject
ambiguous state, preserve recoverable user data, and provide a bounded manual
recovery path.

### T6: Unbounded Local Failure

A child process hangs, floods output, survives an ordinary timeout, or prevents
the maintainer from continuing work.

Required posture: apply bounded waits and output limits on the OpenCode path,
attempt TERM/KILL cleanup, and report residual processes rather than claiming
successful cleanup without evidence.

## Out Of Scope

The following are not security requirements for the current proof of concept:

- multiple mutually untrusted users or tenants;
- a shared hosted Compound Engineering service;
- a malicious process already running as the maintainer's user;
- deliberate same-user replacement of private state, executables, locks,
  snapshots, result files, or configuration;
- root, administrator, kernel, hypervisor, or filesystem compromise;
- physical power-loss durability beyond ordinary recoverable local state;
- confidentiality from the selected model provider after the user authorizes
  sending a prompt or repository material;
- enterprise compliance, regulated-data processing, or hostile multi-user CI;
- security guarantees for Claude Code, Cursor, Codex CLI, Gemini, Grok, Pi, or
  any other non-OpenCode harness;
- correctness or identity guarantees for non-OpenAI model routes that have not
  been explicitly promoted and tested; and
- theoretical attacks that require the sole trusted user to plant malicious
  files, alter their own protected configuration, or race their own workflow
  with a hostile same-UID process outside normal use.

These cases may still justify compatibility fixes or defense in depth, but they
do not automatically constitute P0/P1 defects under this threat model.

## Harness And Model Policy

### Supported Profile

The supported profile is:

```text
single local user
  -> OpenCode harness
     -> Compound Engineering skills and routing
        -> primarily OpenAI-hosted models
```

Security and release verification should prioritize this path. A finding in
shared code remains in scope when it can affect OpenCode, even if first observed
through another harness.

### Experimental Profiles

All other harness and provider combinations are experimental. Conversion and
deterministic contract tests may be maintained, but passing those tests is not
evidence of live sandbox, credential, provider, or process-isolation behavior.

A defect confined to an experimental harness is a compatibility or hardening
item unless it also affects the supported OpenCode profile. The project should
not claim those paths are secure merely because they are distributed.

## Severity Calibration

P0 and P1 require realistic impact and realistic occurrence in the supported
single-user OpenCode profile.

- **P0:** likely catastrophic loss or broad credential disclosure in the normal
  supported workflow, with no reasonable recovery.
- **P1:** a high-impact defect plausibly reached by the maintainer during normal
  OpenCode use, such as unintended destructive mutation, credential disclosure,
  or material sent to the wrong configured recipient.
- **P2/P3:** defense in depth, narrow races, compatibility defects, unsupported
  harness failures, or attacks requiring a malicious same-UID process or
  deliberate user tampering.

An audit must not promote a finding solely because its theoretical impact is
large. It must separately establish that the trigger is plausible for the sole
current user on the supported OpenCode/OpenAI-oriented path.

## Accepted Residual Risks

- OpenAI and any occasionally selected provider receive the material the user
  authorizes the workflow to send.
- Model output can be wrong, adversarial, or misleading; human/workflow
  validation remains necessary.
- Prompt-only read or path restrictions are cooperative unless an actual host
  sandbox enforces them.
- Same-user processes can inspect or alter same-user files and process state.
- Interrupted runs may require manual inspection or cleanup.
- Non-OpenCode harnesses may be broken, insecure, or behaviorally different.
- Availability and durability are proof-of-concept quality, not service-level
  guarantees.

## Reevaluation Triggers

Review and replace this threat model before any of the following:

- another person becomes an actual Compound Engineering user;
- the tool runs as a shared service, daemon, CI service, or multi-user system;
- a non-OpenCode harness becomes a supported path;
- non-OpenAI providers become routinely supported rather than exploratory;
- unattended automation can merge, push, deploy, or delete without the user's
  immediate supervision;
- regulated, production, customer, or third-party secret data enters scope; or
- the project makes public security, sandboxing, tenant-isolation, or
  enterprise-readiness claims.

Until one of these triggers occurs, reviews should optimize for a reliable,
recoverable single-user OpenCode proof of concept rather than multi-tenant or
hostile-local-user security.
