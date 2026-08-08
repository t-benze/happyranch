---
name: create-skill
description: >
  Verified-agent custom-skill authoring from an active task session.
  Creates a standard_operational custom skill with server-verified
  task/session/agent/org provenance. This is the ONLY supported custom-skill
  authoring path; the skill is hidden by default. Direct human authoring,
  editing, validation, assignment, eligibility, and the legacy proposal-review
  workflow are retired.
---

# create-skill

Use this skill when you have reusable guidance to capture as a custom skill
from your current active task session. This is a system-contract skill
injected only for TASK sessions with repo access.

## What create-skill does

- Accepts a package with valid `slug`, `name`, `skill_md` (the SKILL.md body),
  and optional `references` (filename→content) and `assets` (filename→content).
- The server deterministically validates the package: it checks metadata
  completeness, protected-slug collision, `standard_operational` policy class
  only, and rejects executable/credential/permission/sandbox/allow-rule/
  executor/eligibility-bearing content.
- The server derives your org, task, agent, and active session identity from
  its verified SessionTracker context — never from body, CLI flags, or
  request-path claims.
- On success, the server records an immutable version with a canonical
  content hash, validator version, findings, task/session/agent/org provenance,
  and a nonempty task-brief digest.
- The created skill is **hidden by default**. No human authoring, editing,
  validation, assignment, or eligibility remediation surface is currently
  supported.

## What create-skill does NOT do

This skill grants no additional authority. It does not:

- Grant tools, credentials, network access, filesystem scope, sandbox changes,
  allow rules, executor configuration, or command authority.
- Change permissions, auth, or the agent execution model.
- Set eligibility, publish, assign, or make the skill visible to any agent.
- Edit system contracts, first-party shipped skills, or release-managed skills.
- Override developer, system, or user instructions.
- Support `high_impact_policy`, `system_contract`, executable hooks, plugin
  installation, or dependency installation.

## Submission form

```bash
happyranch skills create --from-file <path> --session-id <session-id> [--org <slug>]
```

This is a token-free transport. The CLI builds a plain HTTP POST with no
Authorization header. The server derives identity from the session binding.

## Package file format

Write a JSON file containing only package metadata and content. Do NOT include
trusted identity fields (`org`, `agent`, `agent_name`, `task_id`, `session_id`,
`proposer_agent`, `actor`, `eligibility`, `permission`, `permissions`,
`org_slug`). The server rejects the presence of any of these keys before
persistence.

```json
{
  "slug": "my-workflow",
  "name": "My Workflow",
  "skill_md": "# My Workflow\n\nGuidance content here...",
  "version": "0.1.0",
  "policy_class": "standard_operational",
  "description": "Optional one-line summary",
  "references": {},
  "assets": {}
}
```

### Required fields

- `slug` — unique within the org; must not collide with any system contract
  (`start-task`, `jobs`, `make-worktree`, `thread`, `dream`, `todos`,
  `create-skill`) or release-managed skill (`hr:reflection`,
  `hr:manage-agent`, `hr:manage-repo`, `hr:frontend-development`,
  `hr:product-manager-prd`).
- `name` — human-readable name.
- `skill_md` — the SKILL.md body as a string. Must be non-empty and start
  with a Markdown heading.

### Optional fields

- `version` — defaults to `"0.1.0"`.
- `policy_class` — must be `"standard_operational"` (only supported class in B1).
- `description` — one-line summary.
- `references` — map of filename → content for reference files.
- `assets` — map of filename → content for asset files.

## Validation

The server runs deterministic validation before persistence. Validation checks:

1. `skill_md` is non-empty and starts with a Markdown heading.
2. Required metadata (`slug`, `name`, `skill_md`) is present and non-empty.
3. `slug` does not collide with any protected slug (system contracts +
   release-managed skills, loaded from the canonical release registry).
4. `policy_class` is `standard_operational` (no `system_contract` or
   `high_impact_policy` without B2 authority).
5. References and assets filenames are safe (no path traversal, no
   absolute paths).

On failure, the response includes actionable error codes and leaves zero
artifact, package, ledger event, materialization, or operational-session
residue.

## Provenance and atomicity

The server records, in the SAME durable transaction as the package:

- Verified `org_slug`, `task_id`, `agent_name`, and `session_id` (from
  SessionTracker context, never from body claims).
- Nonempty task-brief digest (the task's current brief at submission time).
- Canonical content hash (SHA-256 of the assembled package manifest).
- Validator version (e.g. `"THR-055/1.0.0"`) and structured findings.

If any provenance field cannot be populated (e.g., the task has no brief),
the entire transaction rolls back with zero residue.

## Error handling

- Missing `--from-file` or `--session-id` → CLI error, exit code 1.
- Unknown/inactive session → HTTP 403 `unknown_session`.
- Cross-org session → HTTP 403 `cross_org_session`.
- Authorization header present → HTTP 401 `bearer_not_accepted`.
- Body contains trusted identity key → HTTP 403 `body_identity_rejected`.
- Protected slug collision → HTTP 409 `protected_slug`.
- Validation failure → HTTP 422 with structured error codes.
- Server error → HTTP 500 with detail.

## Not part of this agent path

- Eligibility configuration or any visibility write.
- Founder or other human web editing, direct validation, or assignment.
- `high_impact_policy` custom skills.
- `happyranch skills propose` or any proposal/review/publish action; those
  legacy surfaces are retired and intentionally absent.
