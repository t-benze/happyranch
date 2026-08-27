---
name: create-skill
description: >
  Verified-agent custom-skill authoring from an active task session.
  Creates a standard_operational custom skill with server-verified
  task/session/agent/org provenance. The B2 record is created immediately
  and hidden by default until founder-configured eligibility makes it visible.
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
- The created skill is a default-hidden B2 custom-skill record. The response
  includes `skill_id`, `version_id`, `content_hash`, `validation_state`, and
  `hidden_reason` (`no_eligibility_policy`).
- It becomes visible to the assignee only after a founder configures
  eligibility through the B2 web UI or API. Creation has no separate
  lifecycle step after the record is saved.

## What create-skill does NOT do

This skill grants no additional authority. It does not:

- Grant tools, credentials, network access, filesystem scope, sandbox changes,
  allow rules, executor configuration, or command authority.
- Change permissions, auth, or the agent execution model.
- Set eligibility, assign, or make the skill visible to any agent.
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
  "skill_md": "---\nname: My Workflow\ndescription: Optional one-line summary\n---\n\n# My Workflow\n\nGuidance content here...",
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
- `skill_md` — the SKILL.md body as a string. It must be one of the two
  supported authoring shapes: (a) **heading-first** — the body starts at
  column zero with a Markdown ATX heading (1–6 `#` markers followed by
  whitespace or end of line) — or
  (b) **YAML-frontmatter-first** — a valid opening `---` YAML fence at
  column zero containing a YAML mapping, a closing `---` fence, then a
  Markdown heading (the same ATX boundary). Leading BOM/whitespace before
  either shape is not accepted (no silent healing). Example below.

### Optional fields

- `version` — defaults to `"0.1.0"`.
- `policy_class` — must be `"standard_operational"` (the only supported class).
- `description` — one-line summary.
- `references` — map of filename → content for reference files.
- `assets` — map of filename → content for asset files.

## Validation

The server runs deterministic validation before persistence. Validation checks:

1. `skill_md` matches the supported authoring grammar (THR-210 PR 2): either
   a column-zero Markdown ATX heading (1–6 `#` markers followed by whitespace
   or end of line; heading-first) or a valid opening `---`
   fence, a YAML mapping inside, a closing `---` fence, then a Markdown
   heading (the same ATX boundary). Malformed, unclosed, non-mapping, or
   missing-heading bodies — and bodies with neither a column-zero heading nor
   frontmatter, including hash-prefixed lines that are not ATX headings — are
   classified invalid under the authoring contract and persisted as
   immutable validation/provenance evidence; they are not accepted as valid
   or materializable versions.
2. Required metadata (`slug`, `name`, `skill_md`) is present and non-empty.
3. `slug` does not collide with any protected slug (system contracts +
   release-managed skills, loaded from the canonical release registry).
4. `policy_class` is `standard_operational` (no `system_contract` or
   `high_impact_policy` without B2 authority).
5. References and assets filenames are safe (no path traversal, no
   absolute paths).

On document-contract failure (THR-210 PR 1), the candidate is NOT silently
discarded: it is appended as immutable validation/provenance evidence — one
invalid version row with deterministic findings, your task/session provenance,
and its content-addressed artifact — and the skill (or first-version creation)
is returned as created with `validation_state: invalid`. The invalid candidate
never displaces an existing valid current version and is never eligible or
materialized; an initial invalid creation darkens the skill as
`current_version_invalid` until a valid successor advances it. Requests
rejected before persistence (missing metadata, protected-slug collision,
unknown/cross-org session, identity spoofing) leave zero artifact, package,
ledger-event, materialization, or operational-session residue.

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
- Missing/empty required metadata (`slug`, `name`, `skill_md`) → HTTP 422 with structured error codes.
- Document-contract validation failure → the candidate is appended as immutable invalid-version evidence (HTTP 201, `validation_state: invalid`); it never becomes the current version when a valid one exists.
- Server error → HTTP 500 with detail.

## Visibility after creation

Creation saves the B2 custom-skill record immediately, but the default
`hidden_reason` is `no_eligibility_policy`. A founder must configure an
eligibility rule for the intended assignee through the B2 web UI or API before
the skill can be materialized for that agent. This authoring path does not set
that rule and does not change permissions, tools, credentials, or auth.

`high_impact_policy` custom skills remain unsupported.
