---
name: create-skill
description: Task-facing system contract for agent-authored custom-skill creation via verified session identity.
---

# create-skill

A system-contract skill that explains how an agent in an active task may
author and submit a validated custom-skill record. This skill is
runtime-injected for TASK sessions (with repo access) through the
canonical source/hash/injection/materialization pipeline. It grants no
tools, credentials, network access, filesystem scope, sandbox changes,
allow rules, executor configuration, eligibility-writing authority,
publishing authority, assignment authority, or instruction-changing
authority.

## When to use

Use this skill when the founder or product spec instructs you to create a
custom skill to capture reusable guidance discovered during a task.

## Package shape

A valid custom-skill package MUST include:

- **Metadata:** ``slug`` (organization-scoped, lowercase alphanumeric
  plus hyphens and underscores), ``name`` (1–128 chars), ``description``
  (1–512 chars), ``version`` (semantic, e.g. ``0.1.0``).

- **``SKILL.md``:** The skill body — plain Markdown guidance for agents.
  It must be non-empty.

- **Optional:** ``purpose`` (free-text why this skill is needed),
  ``target_agent_suggestion`` (informational only, not binding),
  ``references`` (dict of filename → content), and ``assets`` (dict of
  filename → content).

## Boundary — what this skill does NOT grant

A custom skill is a **guidance package only**. It never:

- Grants tools, credentials, network access, filesystem permissions, or
  sandbox policy.
- Changes executor selection, allow rules, or permission boundaries.
- Exposes the submitted package to any agent (it is **hidden by default**).
- Creates, modifies, or reads eligibility policies.
- Publishes, assigns, or retires skills.
- Edits system-contract, release-managed, or protected-namespace skills.
- Shadows a system-contract id (``start-task``, ``jobs``,
  ``make-worktree``, ``thread``, ``dream``, ``todos``, ``create-skill``)
  or a first-party shipped skill id.
- Contains executable hooks, credentials, dependency installation,
  sandbox declarations, or permission content.

An agent **cannot** set eligibility, assign itself or others, edit system
or first-party skills, alter permissions, or change organizational
settings through this path.

## Validation

On submission the server performs **deterministic validation**:

1. **Package structure:** ``slug``, ``name``, ``description``,
   ``skill_md`` are non-empty and bounded.
2. **Protected slugs:** The slug must not collide with any system-contract
   id or release-managed skill id.
3. **Policy class:** Only ``standard_operational`` is accepted. Custom
   ``high_impact_policy`` skills require a separate governance decision
   and are out of scope.
4. **Provenance:** The server independently derives verified organization,
   task ID, agent name, and session ID from the active SessionTracker
   binding. Client-supplied identity fields (``org``, ``agent``,
   ``task_id``, ``session_id``, ``proposer_agent``,
   ``eligibility``, ``permission``) are rejected before persistence.
5. **Immutable version:** On success the version is saved with a
   deterministic content hash and verified provenance (org, task ID,
   agent name, session ID, task-brief digest) derived exclusively from
   the server's SessionTracker binding. A deterministic validation
   runs before persistence; its result (pass/fail and reason codes)
   is recorded alongside the validator version ("B1-create-skill-route-v1")
   in a durable lifecycle event. The version is immutable; a later edit
   always creates a new version/hash.

## Visibility

A newly created custom skill is **hidden — no agent session receives it**.
Visibility is controlled exclusively by the separate Eligibility surface
(Fr-6, Fr-7 in the PRD), which is human/Founder-only. This skill cannot
change eligibility.

## Supported command

The single shipping CLI form is:

```bash
happyranch skills create --from-file <path> --session-id <session-id> [--org <slug>]
```

- ``--from-file <path>``: Path to a JSON file containing package metadata
  and content (slug, name, description, skill_md, version, policy_class,
  optional purpose, target_agent_suggestion, references, assets).
  Must NOT contain identity/authority fields (org, agent, task_id,
  session_id, proposer_agent, eligibility, permission).
- ``--session-id <session-id>``: The opaque active session ID from the
  task context. The server derives org, task, and agent from this.
- ``--org <slug>``: Optional org selector; defaults to auto-detect.

The command sends a **bearer-free HTTP POST** to
``POST /api/v1/orgs/{slug}/skills/agent``. No Authorization header is
sent — the route is agent-only and validates identity exclusively through
the server-side SessionTracker.

On success the CLI prints skill_id, version_id, version, content_hash,
and status. On failure it prints an actionable error and exits non-zero.

## Errors

| Condition | Response |
|---|---|
| Missing ``--from-file`` or ``--session-id`` | Local error, exit 1 |
| Malformed JSON package file | Actionable parse error, exit 1 |
| Unknown/inactive/expired session | HTTP 403 ``unknown_session`` |
| Cross-org session | HTTP 403 ``cross_org_session`` |
| Authorization header present | HTTP 401 ``bearer_not_accepted`` |
| Body contains identity field | HTTP 403 ``body_identity_rejected`` |
| Protected/release slug | HTTP 409 ``protected_slug`` |
| Missing required fields | HTTP 422 with field-level detail |
| Invalid policy class | HTTP 409 with actionable detail |
| Agent not recognized | HTTP 403 |
| Daemon not running | Local error, exit 1 |

## What happens after creation

1. The custom skill enters the lifecycle ledger with version and hash.
2. It remains **hidden** — no agent receives it in their session.
3. A human (Founder) must configure Eligibility to expose it.
4. Once eligible, the skill is materialized into qualifying agent
   workspaces at their **next session spawn**.
5. The skill enters the lifecycle ledger as a PROPOSED record. The B1
   route creates new proposals only; updating an existing custom skill
   (creating a new version of an originated skill) is deferred to B2.

## Deferred (B2 / v1 follow-on)

- Human web editor / authoring console (Fr-3).
- Eligibility editor at org, team, agent scope (Fr-6, Fr-7).
- Effective Skills explanation and impact preview (Fr-8).
- Custom ``high_impact_policy`` skills.
- A repurposed proposal-review queue.
- Migration/cutover from existing proposal-only workflows.
- Any frontend or web/src surface.
