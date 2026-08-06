---
name: create-skill
description: Use this skill to author and submit a validated custom skill package from an active task. Explains package shape, validation, provenance, hard boundaries, and the supported submission command. Custom skills are default-hidden; eligibility is a separate Founder-only operation.
---

# create-skill

You are in an active task session. You can capture reusable guidance as a custom skill
using the agent-only creation endpoint. This skill describes what a valid custom-skill
package looks like, what you must never try to do, and how to submit it.

## What a custom skill IS

A custom skill is **guidance** — reusable text the system delivers to future agent
sessions when a Founder explicitly grants eligibility. A custom skill:

- Is a single `SKILL.md` file with markdown frontmatter (`name`, `description`) and
  a markdown body.
- Lives in a named package under a stable **slug** (lowercase alphanumeric + hyphens,
  e.g. `triage-runbook`). The slug becomes the skill id `hr:<slug>`.
- Has a **version** (default `"0.1.0"`) that records an **immutable content hash**
  (SHA-256 of the canonical package tree).
- Belongs to your current organization; the server derives the org identity.
- Is `standard_operational` only. You cannot create `high_impact_policy` or
  `system_contract` custom skills.

## What a custom skill is NOT — hard boundaries

A custom skill **never** grants, and you must never claim it grants:

- **Tools, credentials, or network access.** A skill cannot add or remove the
  tools, APIs, or credentials an agent already has.
- **Filesystem or sandbox changes.** A skill cannot alter workspace paths,
  sandbox settings, or filesystem policy.
- **Allow-rule or permission authority.** A skill cannot add, remove, or
  modify `allow_rules`, tool policies, or permission gates.
- **Executor or model configuration.** A skill cannot select, switch, or
  configure the executor, provider, or model.
- **Eligibility, publishing, or assignment.** An agent-created custom skill is
  default-hidden. Only the Founder (via the web console) can write eligibility
  rules that make it visible to an org, team, or agent. You cannot assign the
  skill to yourself or anyone else.
- **System, developer, or user instruction changes.** A skill is guidance for a
  specific workflow; it cannot rewrite the agent's core system prompt, the
  developer's `CLAUDE.md`, or the founder's user-level instructions.

If you find yourself wanting any of these capabilities, the answer is: record
the workflow as a custom skill, and the Founder decides separately whether to
expose it.

## Package shape

Your submission payload must have:

| Field | Required | Notes |
| --- | --- | --- |
| `slug` | Yes | Lowercase alphanumeric + hyphens. Cannot shadow a protected/system/shipped slug. |
| `name` | Yes | Human-readable display name. |
| `description` | Yes | One-line summary of what the skill provides. |
| `skill_md` | Yes | Full `SKILL.md` body — the guidance itself. Must contain at least one markdown heading. |
| `version` | No (default `"0.1.0"`) | Semantic version string. Each edit creates a new immutable version. |
| `references` | No | Dict of filename→content. Reference files packaged alongside the skill. |
| `assets` | No | Dict of filename→content. Asset files (images, diagrams) packaged alongside. |
| `purpose` | No | Free-text explanation of why you're creating this skill (aids audit). |

All file paths inside references and assets must be safe: no `..` traversal, no
absolute paths, no path separators that escape the package root.

**You must NEVER include these fields in the body** — they are derived server-side
from your verified session identity and will be rejected if present:

- `task_id`, `session_id`, `proposer_agent`, `org`, `org_slug`, `agent`,
  `agent_name`, `actor`, `eligibility`, `permission`, `permissions`

Any field whose name suggests identity, authority, eligibility, or permissions
is forbidden. The server derives org, agent, task, and session from your active
context; caller-supplied identity claims are rejected before any persistence
occurs.

## Validation

Every submission runs deterministic validation:

1. **Package structure**: `skill_md` is non-empty and contains at least one
   markdown heading.
2. **Protected slug check**: The slug cannot match a system contract slug
   (`start-task`, `jobs`, `make-worktree`, `thread`, `dream`, `todos`,
   `create-skill`), a first-party shipped skill slug, or any other
   runtime-protected namespace.
3. **Policy class**: Must be `standard_operational`. Other classes are
   rejected.
4. **Path safety**: All reference/asset filenames pass traversal checks.
5. **Content safety**: The server records the validator version and a
   deterministically-computed content hash. The same content always produces
   the same hash.

A **pass** is technical evidence the package is well-formed — not a claim the
guidance is good. A **failure** returns actionable error details; the skill is
not persisted.

## Provenance

Every version records:

- **Agent ID**: derived from your verified active session.
- **Task ID**: the task you were working on when you submitted.
- **Session ID**: the exact session that submitted.
- **Organization**: derived server-side, never from the request.
- **Content hash**: SHA-256 of the canonical package tree (deterministic).
- **Validator version**: the version of the validation rules applied.
- **Timestamp**: server-assigned creation time.

This provenance is **immutable**. A later edit creates a new version with its
own provenance; it never mutates the original version record.

## Default-hidden

An agent-created custom skill is **never automatically visible** to any agent,
including the agent that created it. The Founder must explicitly configure
eligibility in the Skills web console before the skill appears in any agent's
session.

There is no "publish," "claim," or "review" step for the agent path. The skill
exists as a validated version record with full provenance. Eligibility is a
separate, Founder-only operation on a different surface.

## Submission

Submit from your active task using the agent-only endpoint. The endpoint
requires NO bearer token — it uses your opaque session capability:

```bash
happyranch skills create --from-file /tmp/skill-payload.json --session-id <your-session-id>
```

The `--from-file` JSON payload has this shape:

```json
{
  "slug": "triage-runbook",
  "name": "Triage Runbook",
  "description": "Steps to triage an incoming bug report.",
  "skill_md": "# Triage Runbook\n\n1. Check severity.\n2. Assign to team.\n",
  "version": "0.1.0",
  "purpose": "Captured from TASK-1234 after triaging the third identical issue."
}
```

The server:

1. Derives org, agent, task, and session from your active SessionTracker context.
2. Rejects any body field claiming identity, eligibility, or permissions.
3. Validates the package deterministically.
4. Creates or appends a version for your custom skill.
5. Returns `skill_id`, `version_id`, `version`, `content_hash`, and provenance.

## After submission

- The skill appears in the web console as a custom skill with your provenance.
- It is **not visible** to any agent session until the Founder configures
  eligibility.
- You can view your own submission result (id, hash, validation, provenance).
- You **cannot** edit eligibility, assign the skill, or make it visible. Those
  are Founder-only operations on the web console.

## Failure behavior

| Condition | Response |
| --- | --- |
| Invalid/missing session | 403 `unknown_session` |
| Session belongs to different org | 403 `cross_org_session` |
| Body contains identity/authority field | 403 `body_identity_rejected` |
| Bearer token present (agent path only) | 401 `bearer_not_accepted` |
| Slug collides with protected/system/shipped slug | 400 `protected_slug` |
| Empty or heading-free `skill_md` | 400 validation failure |
| Policy class not `standard_operational` | 400 `invalid_policy_class` |
| Unsafe reference/asset path | 400 validation failure |
| Internal persistence error | 500 with atomically-rolled-back state |

All errors leave zero residue: no partial version record, no orphaned artifact,
and no eligibility mutation.

## Scope and future

This is the **B1 slice** of custom-skill creation. What is NOT yet available
(and what you must not attempt):

- Editing an existing custom skill (appending a new version).
- Retiring or restoring a custom skill.
- Human authoring/editor via the web console (B2).
- Eligibility policy configuration of any kind (B2).
- Materialization into agent sessions (requires eligibility + B2 backend
  contract freeze).
- Any web console surface for custom skills beyond the read-only catalog.

These are deferred to backend slice B2 and subsequent frontend slices. The
current delivery gives you the ability to create a validated, provenance-tracked
custom skill from an active task, with full audit evidence and no visible effect
until a Founder configures eligibility.
