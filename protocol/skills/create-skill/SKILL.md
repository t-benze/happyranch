---
name: create-skill
description: Author and submit a validated custom skill package from an active task session. Task-facing system contract: derives identity from verified session context only; never exposes tools, permissions, or eligibility.
---

# create-skill

A TASK-facing system contract. It authorizes a verified active agent session to
create a new custom skill or append a new version to the agent's own originated
skill using the server's verified SessionTracker identity — never from caller-
supplied trusted fields.

## What create-skill does

- Explains the valid custom-skill package shape: required metadata (`slug`,
  `name`, `description`, `version`), required `SKILL.md` guidance, and
  optional `references` and `assets` (flat, text-only; no binaries or
  executables).
- Describes deterministic package validation: non-empty members, safe paths
  (no traversal or absolute), text-only content, no executable/credential/
  permission/sandbox/allow-rule/executor/eligibility content.
- Documents that every accepted version is immutable and content-addressed
  (canonical SHA-256 hash over the complete manifest — SKILL.md plus every
  reference and asset).
- States that a successful create produces a validated custom-skill record
  that is **default-hidden** — it does NOT automatically become visible,
  assign itself, materialize, or grant any tool/credential/permission/
  sandbox/allow-rule/executor/eligibility authority.
- Provides the exact supported submission form.

## Hard boundaries (never offered, never implied)

- create-skill grants NO tools, credentials, network access, filesystem scope,
  sandbox changes, allow rules, executor configuration, or command authority.
- It cannot change eligibility, assign a skill, publish a skill, modify
  system/first-party skills, or alter any agent/team/org permission.
- It cannot shadow protected system-contract or first-party shipped skill
  slugs.
- It cannot create `protected`, `system`, `shipped`, `high_impact_policy`, or
  any non-`standard_operational` policy class.
- It cannot include executable content, binary assets, credential files,
  permission declarations, sandbox directives, allow-rule fragments,
  executor-selection hints, or eligibility-rule fragments in any package
  member.
- It cannot assert caller-supplied identity, authority, eligibility,
  permission, or configuration fields — the server rejects every such field
  before persistence.

## Package shape

A valid custom-skill package submitted via `--from-file <path>` is a JSON
object:

```json
{
  "slug": "my-workflow",
  "name": "My Workflow",
  "description": "Reusable guidance for…",
  "version": "0.1.0",
  "skill_md": "# My Workflow\n\nGuidance text…",
  "references": {},
  "assets": {}
}
```

| Field | Required | Constraints |
| --- | --- | --- |
| `slug` | Yes | `[a-z][a-z0-9-]*`; ≤64 chars; must not shadow a protected system/first-party slug |
| `name` | Yes | Non-empty; ≤128 chars |
| `description` | Yes | Non-empty; ≤512 chars |
| `version` | Yes | SemVer-like (`N.N.N`); ≤32 chars |
| `skill_md` | Yes | Non-empty; ≤128 KiB; valid Markdown/plain text |
| `references` | No | `{<path>: "<text>"}`; each ≤128 KiB; safe paths only; ≤32 entries |
| `assets` | No | `{<path>: "<text>"}`; each ≤256 KiB; safe paths only; ≤16 entries |

The body MUST NOT contain any of these prohibited identity/authority/config keys:
`org`, `org_slug`, `agent`, `agent_name`, `task_id`, `task`, `session_id`,
`session`, `proposer_agent`, `proposer`, `actor`, `eligibility`, `permission`,
`permissions`, `allow_rules`, `allow_rule`, `sandbox_mode`, `sandbox`,
`executor`, `executor_config`, `credential`, `credentials`,
`network_access`, `filesystem_scope`, `command_authority`,
`config`, `configuration`, `settings`, `owner`.
Their presence (including empty values) is rejected with HTTP 403 before
any persistence. ANY unknown field not listed above is also strictly
rejected — the body shape is exact.

Additionally, all text members (SKILL.md, every reference, every asset)
are scanned for executable, credential, permission, sandbox, allow-rule,
executor, and eligibility-indicating content BEFORE any artifact write or
ledger row. Matches are rejected with HTTP 403 and produce zero residue.

## Submission result

On success (HTTP 201), the server returns:

```json
{
  "skill_id": "hr:my-workflow",
  "version_id": 42,
  "version": "0.1.0",
  "status": "proposed",
  "content_hash": "<64-hex-chars>",
  "content_artifact_key": "skill-lifecycle/my-workflow/<hash>/manifest.json",
  "proposal_task_id": "TASK-NNN"
}
```

The skill is now a validated custom-skill record. It remains **hidden**
until a separate human eligibility write makes it visible. It does NOT
appear in any agent's effective skills, catalog, or materialization until
explicitly made eligible by a Founder.

On failure, the server returns an actionable error with a machine-readable
`code` and human-readable `detail`. No partial artifact, ledger row, event,
or session state is produced.

## Validation

Deterministic validation runs on the exact submitted package before any
persistence. Validation checks:

1. **Package shape**: all required fields present; no unknown top-level keys.
2. **Metadata limits**: slug/safe characters, name/description length.
3. **Protected namespace**: slug does not collide with any runtime system
   contract or first-party shipped skill slug.
4. **Policy class**: must be `standard_operational` (explicitly set; not
   defaulted).
5. **Text-member safety**: every text member (SKILL.md, each reference,
   each asset) is scanned for executable/credential/permission/sandbox/
   allow-rule/executor/eligibility-indicating content.
6. **Path safety**: no `..` traversal, no absolute paths, no empty names
   in reference/asset keys.
7. **Content addressing**: the canonical SHA-256 content hash is computed
   over the complete manifest (all members, normalized paths, sorted).

Validation records the rule/validator version and deterministic findings
for the exact package hash. A pass is technical evidence — not a claim
that the guidance is correct or useful.

## Provenance

Every accepted version records, immutably and server-derived:

- Verified organization slug
- Verified task ID and session ID (from SessionTracker context)
- Verified agent name
- Task brief digest (SHA-256 of the task's brief at creation time)
- Creation timestamp (UTC)
- Canonical content hash (SHA-256 of the complete manifest)
- Validator/rule version and deterministic findings

None of these are caller-supplied. The server rejects any body field that
claims to set them.

## Lifecycle

After creation, a custom skill is in `proposed` status:
- It is visible in the founder's lifecycle queue.
- It is NOT visible to any agent (including its creator).
- It does NOT materialize in any workspace.
- Subsequent versions by the same originating agent append to the same skill record
  (same `skill_id`, new `version_id` and `content_hash`). Originator-only
  enforcement: the server rejects version appends from any agent other than
  the verified creator.
- The agent cannot edit, validate, claim, publish, assign, retire, or
  rollback the skill — those are human/founder-only actions.
