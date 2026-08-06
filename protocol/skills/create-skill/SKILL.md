---
name: create-skill
description: Create a custom skill from an active task session. Documents the package format, validation rules, proven
ance requirements, and the submission command. This skill grants no tools, credentials, or permissions.
---

# create-skill

Use this skill to create a custom `standard_operational` skill from an active task session.

## What this skill does

Documents the canonical format for authoring a custom-skill package (metadata + `SKILL.md`), the deterministic
validation rules, the immutable version/hash model, the required task/session/agent/org provenance, and the exact
CLI command to submit the package. It grants no tools, credentials, network access, filesystem scope, sandbox
changes, allow rules, executor configuration, eligibility writes, publishing, assignment, instruction-changing,
or system/release editing authority.

## When to use

Use when you have discovered a repeatable workflow or reusable guidance worth capturing as a custom skill. The
created skill will have `standard_operational` policy class and will NOT be automatically visible to any agent
— the founder must separately configure eligibility before any agent receives it.

## Package format

A custom-skill package consists of a JSON payload with these required fields:

- `slug` — unique organization-scoped identifier (e.g., `my-custom-workflow`)
- `name` — human-readable short name
- `description` — one-line purpose
- `skill_md` — the full `SKILL.md` body (Markdown string)

Optional fields:

- `version` — semantic version string (default `0.1.0`)
- `purpose` — free-text explanation of intent
- `target_agent_suggestion` — suggested recipient agent (informational only)
- `references` — dict of relative path → content for supplementary files
- `assets` — dict of relative path → content for asset files

## Validation

Every submission undergoes deterministic validation BEFORE any artifact or ledger write:

1. **Metadata completeness** — `slug`, `name`, `description`, and `skill_md` must be non-empty.
2. **Policy class** — only `standard_operational` is accepted.
3. **Protected slug check** — the slug must not collide with any system contract, release-managed,
   or first-party shipped skill. The live release catalog and `SYSTEM_CONTRACTS` are consulted;
   the check fails closed if the registry is unavailable.
4. **Path safety** — all reference and asset paths must be relative (no `..` traversal, no absolute paths).
5. **Content hash** — an immutable canonical SHA-256 hash binds the full package content.

Validation findings record the validator version and are tied to the exact content hash. A validation
pass is technical evidence, not a claim that the guidance is good.

## Provenance

Every agent-created skill records:

- **Verified org slug** — derived from the server's SessionTracker context, never from body/path/flag claims.
- **Verified task ID** — the active task from which the skill is authored.
- **Verified session ID** — the active opaque session binding.
- **Verified agent name** — the agent that authored the skill.
- **Task brief digest** — a SHA-256 hash of the task's brief text, providing non-repudiable evidence of
  the context in which the skill was created.

All four identity dimensions (org, task, agent, session) are derived server-side from the opaque session
capability — never from body, query, path, environment, or client claims. Any attempt to supply identity
in the request body (`task_id`, `agent`, `org`, etc.) is rejected before parsing.

## Submission command

```
happyranch skills create --from-file <path> --session-id <session-id> [--org <slug>]
```

- `--from-file` — path to the JSON package file (metadata and content only; no identity fields)
- `--session-id` — your active session ID (available in your task context)
- `--org` — optional org slug (auto-detected when omitted; the server cross-checks against session context)

The CLI builds a token-free transport — no bearer token is read or sent. The server derives all identity
from the verified session context.

## What this skill does NOT grant

- ❌ Tools, credentials, or capabilities
- ❌ Network access or filesystem scope changes
- ❌ Sandbox policy, allow rules, or executor configuration
- ❌ Permission, eligibility, or publishing authority
- ❌ Ability to assign skills to agents or teams
- ❌ Ability to edit system contracts, release-managed skills, or shipped skills
- ❌ Ability to change agent instructions or system prompts

## Result

On success the CLI prints:

```
Skill created successfully.
  skill_id:  hr:<slug>
  version_id: <id>
  version:    <version>
  status:     proposed
  content_hash: <sha256>
```

The skill is created in `proposed` status with default hidden eligibility. It will NOT be visible to any
agent until the founder explicitly configures eligibility through the web console.

## Errors

Every denial is stable and actionable:

- `unknown_session` — the session ID is invalid, inactive, or expired
- `cross_org_session` — the session belongs to a different org than the URL path
- `bearer_not_accepted` — an Authorization header was sent (this route is agent-only)
- `body_identity_rejected` — the request body contains a prohibited identity field
- `protected_slug` — the slug collides with a system contract or release-managed skill
- `missing_session_binding` — the session lacks org/task/agent context
- `validation_failed` — the package failed deterministic validation

Every denial leaves zero artifact, package, lifecycle event, ledger, materialization, or
operational-session residue.

## Boundaries

- **v1 only:** `standard_operational` policy class only.
- **No eligibility writes:** agents cannot set or modify eligibility. Default is hidden.
- **No system/release editing:** custom skills cannot shadow protected slugs.
- **No executable content:** packages contain only Markdown and reference/asset files.
- **No assignment:** the skill is NOT automatically assigned to the creating agent.
- **No materialization:** the skill is NOT materialized into any workspace until founder eligibility.
