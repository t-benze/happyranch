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

- Uses `slug`, `name`, `skill_md` (the SKILL.md body), and optional
  `description`. The agent route ignores `references`, `assets`, `version`
  and `policy_class`; reference and asset files are not saved. Validation uses
  fixed version `"1"` and policy class `standard_operational`.
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
- The created skill is a default-hidden B2 custom-skill record. The CLI prints
  labelled `skill_id`, `version_id`, `content_hash`, `validation_state` and
  `hidden_reason` lines. Their JSON paths are `skill.id`, `version.id`,
  `version.content_hash`, `version.validation_state` and `hidden_reason`
  (`no_eligibility_policy`).
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

Write a JSON file containing only package metadata and content. The CLI refuses
these identity/authority keys locally (exit 1, no request): `org`, `org_slug`,
`agent`, `agent_name`, `task_id`, `task`, `session_id`, `session`,
`proposer_agent`, `proposer`, `actor`, `eligibility`, `permission`,
`permissions`, `identity`. Do not include them; identity comes from the verified
session. The server also rejects its trusted identity keys before persistence.

```json
{
  "slug": "my-workflow",
  "name": "My Workflow",
  "skill_md": "---\nname: my-workflow\ndescription: Summarize the workflow and when to use it\n---\n\nGuidance content here...",
  "description": "Summarize the workflow and when to use it"
}
```

### Required fields

- `slug` — unique within the org; must not collide with any system-contract
  slug in [system_contracts.py](../../system_contracts.py) or release-managed
  slug loaded by the [release registry](../../registry.py). Current shipped
  bare slugs are `start-task`, `jobs`, `make-worktree`, `thread`, `dream`,
  `todos`, `workspace-cleanup`, `create-skill`, `manage-agent`, `manage-repo`
  and `reflection`.
- `name` — human-readable catalog label.
- `skill_md` — the SKILL.md document as a string. It MUST start with a
  column-zero `---` YAML frontmatter fence, contain a YAML mapping with the
  required `name` and `description` fields, and close the fence with `---`.
  The body after the fence is optional. `slug` and frontmatter `name` must both
  be 1–64 characters of
  ASCII lower-case letters (`a-z`), ASCII digits (`0-9`) and single hyphens,
  with no leading, trailing or consecutive hyphen — a literal full-string match
  with no Unicode normalization, case folding or transliteration — and `name`
  must equal `slug`. This ASCII-only rule is a HappyRanch admission restriction
  (the Agent Skills standard permits Unicode names); a non-conforming `slug` is
  refused with HTTP 422 `invalid_slug` before any validation or write, while a
  non-conforming frontmatter `name` under an admitted ASCII `slug` is ordinary
  invalid-evidence. `description`
  must be a non-empty line of at most 1024 characters. The only permitted
  top-level frontmatter keys are `name`, `description`, `license`,
  `compatibility` and `metadata`; any other key (including `allowed-tools` and
  `hooks`) is rejected by local admission policy, whatever its value.

### Optional fields

- `description` — optional one-line catalog summary. When omitted (or JSON
  `null`) the catalog description is derived from the validated frontmatter
  `description`; when supplied it must equal that value or the request is
  rejected with 422 `divergent_description`.

## Validation

The server runs deterministic validation before persistence. Validation checks:

1. `skill_md` matches the authoring contract: a column-zero opening
   `---` fence, a YAML mapping, a closing `---` fence, and only the five
   permitted top-level keys. Structural problems (empty document, missing or
   unclosed fence, malformed YAML, non-mapping frontmatter, duplicate
   top-level key) are evaluated first, then admission, then the required and
   optional field checks, in that order. Any finding marks the candidate
   invalid and persists it as immutable validation/provenance evidence; it is
   never accepted as a valid or materializable version.
2. Required metadata (`slug`, `name`, `skill_md`) is present and non-empty, and
   the frontmatter `name` equals `slug`.
3. `slug` does not collide with any protected slug (system contracts +
   release-managed skills, loaded from the canonical release registry).

Invalid candidates return HTTP 201 with `version.validation_state: invalid` and
are saved as immutable validation/provenance evidence. They never displace an
existing valid current version or become eligible/materialized; an initial
invalid version stays hidden as `current_version_invalid` until a valid successor.
A valid candidate also updates the catalog description from its frontmatter.

## Updating your skill

Use the same `skills create` command and slug with changed `skill_md`. Only the
originating agent may append a new immutable version; another agent or a
human-originated skill returns 403 `not_origin_owner`. A valid version becomes
current; an invalid version is retained only as evidence when a current version
already exists. Byte-identical current or historical content returns 409
`version_content_exists`.

## Provenance and atomicity

The server records, in the SAME durable transaction as the package:

- Verified `org_slug`, `task_id`, `agent_name`, and `session_id` (from
  SessionTracker context, never from body claims).
- Nonempty task-brief digest (the task's current brief at submission time).
- Content hash (SHA-256 of `skill_md`).
- Validator version and structured findings.

An empty, falsey or whitespace-only task brief returns HTTP 422
`task_brief_empty` before package validation or persistence. Nothing is saved:
no custom-skill row, version or artifact. The server rechecks the brief after
validation, so a brief emptied during validation is also refused.

## Error handling

- Missing `--from-file` or `--session-id` → argparse exit 2; supply both flags.
- `not_origin_owner` (403) → append only to your own originated skill, or use a new slug.
- `session_not_current` (403) → stop; use only the injected current session, never borrow another SID.
- `recovery_purpose_forbidden` (403) → author only from an ordinary active task session.
- `task_not_found` (422) → report the missing bound task; do not invent task identity.
- `invalid_request` (422) → supply nonempty `slug`, `name` and `skill_md`.
- `skill_purged` (410) → the tombstone cannot be edited; use a new permitted slug.
- `invalid_artifact_name` (400) → report the artifact-name diagnostic; do not bypass store checks.
- `task_brief_empty` (422) → report the missing task brief to the task owner; nothing was saved.
- Unknown/inactive session → HTTP 403 `unknown_session`.
- Cross-org session → HTTP 403 `cross_org_session`.
- Authorization header present → HTTP 401 `bearer_not_accepted`.
- Body contains trusted identity key → HTTP 403 `body_identity_rejected`.
- Protected slug collision → HTTP 409 `protected_slug`.
- Byte-identical content replay (a version with this exact body already
  exists for this skill, current or historical) → HTTP 409
  `version_content_exists` with zero residue: no artifact write or rewrite,
  no new version row, no new event/audit row, no current-version change.
- Missing/empty required metadata (`slug`, `name`, `skill_md`) → HTTP 422 with structured error codes.
- Non-conforming logical `slug` (non-ASCII, lookalike, fullwidth, non-ASCII
  digit, uppercase ASCII, over-length, leading/trailing/consecutive hyphen, or
  a synthetic non-conforming stored slug on an append) → HTTP 422
  `invalid_slug` before validation, artifact-key construction or any durable
  write, with zero residue. This is a HappyRanch admission rule, not a
  SKILL.md syntax error.
- Document-contract validation failure → the candidate is appended as immutable invalid-version evidence (HTTP 201, `validation_state: invalid`); it never becomes the current version when a valid one exists.
- Server error → HTTP 500 with detail.

## Visibility after creation

Creation saves the B2 custom-skill record immediately, but the default
`hidden_reason` is `no_eligibility_policy`. A founder must configure an
eligibility rule for the intended assignee through the B2 web UI or API before
the skill can be materialized for that agent. This authoring path does not set
that rule and does not change permissions, tools, credentials, or auth.

`high_impact_policy` custom skills remain unsupported.
