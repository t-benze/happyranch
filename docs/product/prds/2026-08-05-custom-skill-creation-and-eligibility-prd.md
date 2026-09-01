# Custom skill creation and eligibility PRD

> **Current-state addition (TASK-6143/TASK-6159/TASK-6168/TASK-6423):** Current-v2 custom skills support a founder-only, exact-slug-confirmed logical purge after retirement. It is irreversible HappyRanch denial with permanent ID/slug reservation, not physical erasure. Historical/evidentiary rows, cached content, artifacts, canonical packages, and historical links remain retained. The ordinary custom catalog omits tombstones; its explicit Removed view lists only tombstones, visibly labels them `Permanently removed`, and links to the retained direct receipt. Recreating a tombstoned slug produces a reservation-specific conflict. Current eligibility rules are superseded rather than deleted, so preserved-old resolution remains fail-closed with `no_eligibility_policy`. A narrow per-org barrier at the unified canonical publication seam spans the final authoritative purge/current-version re-read and both provider-root repairs; candidate construction and launch remain outside. There is no launch fence or running-session revocation. Retained `retired_at` means downgrade alone cannot resurrect a record; the deliberate limitation requires both operator downgrade and explicit restore of that exact skill, and purge-aware restore always refuses it.

| Field | Value |
| --- | --- |
| Status | Authoritative — Founder-authorized implementation contract (THR-055 seq 187, 200, 205), dated 2026-08-05 |
| Owner | Product Lead |
| Date | 2026-08-05 |
| Source Links | THR-055 seq. 187–190; PR #555 `2026-08-04-skills-governance-prd.md`; TASK-3380; TASK-3436 / PR #507; TASK-3509 / PR #511; THR-092 Skill Management web-module brief |
| Commitment Boundary | Implementation contract — this is an authoritative product contract; implementation proceeds under THR-055 seq 187, 200, and 205 without requiring further Founder decisions. |
| Founder Decisions | All required decisions for v1 are closed below (Section 12). Ruled: (a) humans who create, edit, validate, version, and retire custom skills and write eligibility are Founder-only via the pre-existing bearer-authenticated path; no named org/team administrators exist in v1; (b) agents create and update only their originated custom skills using verified active task/session identity; (c) default eligibility is hidden; (d) v1 is standard_operational only; (e) B2 retains its version, provenance, validation, and materialization evidence while legacy proposal UI, content, records, history, routes, adapters, and compatibility are deleted. No implementation is blocked on further Founder rulings. |

## Implementation contract

Make **custom-skill creation** the primary Skills workflow. A custom skill may originate in either of two ways:

1. An agent uses an injected, session-bound `create-skill` skill to author and submit a validated custom-skill record from a task.
2. A human creates or edits the same kind of custom-skill record directly in the Skills web console.

Creation produces reusable, editable guidance; it does **not** expose that guidance to any agent. A separate Eligibility surface then determines visibility by organization, team, or individual agent, with an effective-skills explanation and impact preview. This is the intentional activation step.

This is the current B2 path for custom skills. It retains provenance, deterministic validation, immutable version history, audit, rollback, and next-session materialization. Legacy proposal UI, content, records, history, routes, adapters, and compatibility are deleted.

The trade-off is deliberate: ordinary authoring becomes fast enough to capture useful workflow knowledge, while control of who sees it remains explicit and auditable. We give up universal pre-publication review; we do **not** give up validation, history, eligibility control, or the firewall between guidance and permissions.

## Problem and outcome

The B2 model gives teams a practical way to author reusable guidance, whether the initial author is an agent that discovered a repeatable workflow or a human who already knows the needed practice.

At the same time, letting creation automatically expose guidance would recreate the behavioral-governance risk that Skills was intended to manage. A skill can shape decisions and use of already-granted tools even though it cannot grant permissions.

The desired outcome is a single custom-skill model with two creation entry points and one explicit activation/configuration step:

```text
Agent create-skill  ─┐
                     ├─> Custom skill + validated version history ─> Eligibility policy ─> Next-session visibility
Human web editor ────┘                                     │
                                                          Audit / rollback
```

An operator can answer four questions without reconstructing history manually:

- Who created or changed this version, and from which task when an agent authored it?
- Did this exact version pass the deterministic package validation?
- Why is it visible—or hidden—for this agent/session?
- Which version was actually materialized in a session?

## Users and authority baseline

| User | Primary need | Allowed product action |
| --- | --- | --- |
| Agent author | Capture reusable guidance discovered in a task. | Create or update its own custom-skill content through `create-skill`; submit for validation; view its own result. |
| Founder (human author/editor/eligibility administrator) | Create, edit, validate, inspect history, retire, and set eligibility for custom guidance. | Use the web editor via the pre-existing bearer-authenticated human path. No separate org-admin or team-admin roles exist in v1; all human custom-skill writes are Founder-only. |
| Recipient agent | Use relevant guidance. | Receive only the version resolved for a new session; cannot alter the skill or its eligibility merely because it can see it. |
| Engineering/support | Diagnose bad or missing materialization. | Read provenance, policy explanation, validation, version, and materialization evidence. |

System contracts (`start-task`, `thread`, `jobs`, `make-worktree`, `dream`) remain runtime-owned, context-gated, and non-toggleable. First-party shipped skills remain release-managed. Global CLI/plugin skills remain outside HappyRanch custom-skill management. A custom skill never grants tools, credentials, network access, filesystem scope, sandbox changes, allow rules, executor configuration, or command authority.

The human authority model is closed for v1: the pre-existing bearer-authenticated path is Founder-only for all human custom-skill writes (create, edit, validate, version, retire, restore) and eligibility writes (org/team/agent scope). No named organization administrators, team administrators, or delegated human roles exist in this version. The server enforces this via the existing bearer-authentication channel; no new auth, RBAC, membership storage, or permission model is introduced.

## Goals

- Give agents a discoverable, safe `create-skill` workflow for authoring reusable custom guidance from a task.
- Give humans a first-class web editor to create, edit, validate, version, inspect, retire, and restore custom skills.
- Make eligibility an explicit, separate configuration surface with organization, team, and agent scope; additive inheritance and explicit deny; and clear provenance.
- Preserve version/hash history, technical-validation results, task/session/agent provenance, policy changes, and per-session materialization evidence.
- Make the next-session boundary and “guidance visibility, not permissions” distinction unmistakable in every write and preview flow.
- Preserve collision protection: custom skills cannot shadow system contracts, first-party shipped skills, or protected namespaces.

## Non-goals and no-list

- No marketplace, cross-organization sharing, public distribution, billing, ratings, or customer skill storefront.
- No automatic assignment or visibility because an agent created a skill.
- No agent authority to modify eligibility, publish permissions, assign itself or others, edit system/first-party skills, change agent/team settings, or alter any permission control.
- No executable hooks, plugin installation, dependency installation, background jobs, secret input, or code-execution surface inside a custom skill package.
- No change to the daemon's existing permission, authentication, sandbox, credential, allow-rule, executor-selection, or system-contract model without a separate Founder-approved proposal.
- No generic organization-wide propagation based on similarity, role title, or AI inference; every eligibility effect must follow an explicit policy rule.
- No Proposals queue, proposal history, legacy route, or compatibility layer for custom skills.

## Product model and workflow

### One custom-skill record, two creation paths

Every custom skill has a stable organization-scoped slug, metadata, a current editable version, immutable historical versions/hashes, validation evidence, author provenance, eligibility policy, and materialization history.

| Path | Initiator | Required outcome | Not permitted |
| --- | --- | --- | --- |
| Agent `create-skill` | Verified active agent task/session | Create a new custom skill or a new version with task/session/agent provenance; run deterministic validation; surface errors. | Eligibility writes, assignment, permission changes, protected-slug edits, system/first-party changes. |
| Human web editor | Founder (bearer-authenticated) in Skills console | Create/import, edit, validate, save a new version, inspect diff/history, retire/restore. | System/first-party changes, unsupported package contents, eligibility or publication writes from an agent path. |

Both paths converge on the same editable custom-skill record. An agent-created skill is not second-class and does not require an artificial publish transition. It remains non-effective until eligibility exposes it.

### Creation and update flow

1. The agent invokes `create-skill` for a reusable behavior, or a human chooses **New custom skill** in the console.
2. The author supplies metadata and `SKILL.md` guidance. The system derives identity/task/session/org for the agent path; a human path records authenticated actor and organization scope.
3. The service validates the exact candidate version before any persistence. Validation checks the supported SKILL.md authoring contract (THR-210 PR 2 grammar: either a column-zero Markdown ATX heading — 1–6 `#` markers followed by whitespace or end of line, heading-first — or a valid opening/closing `---` fence with a YAML mapping, then a Markdown body heading under the same ATX boundary), package structure, protected slug/path collision, supported reference/asset limits, and prohibited executable/permission-bearing content. A pass is technical evidence, not a claim that the guidance is good.
4. A valid version is saved and becomes the current editable custom-skill version. An INVALID candidate is also saved — as immutable validation/provenance evidence with its deterministic findings, author provenance, and content-addressed artifact — but it never displaces an existing valid current version (THR-210 PR 1): the current pointer is retained, and a malformed edit cannot darken a skill whose last valid version still exists. The invalid candidate stays inspectable via version history but is never eligible or materializable. Initial creation with no prior version sets the first version as the current pointer regardless of validity (a NULL pointer is unreadable by list/detail consumers and uneditable), so a skill whose first version is invalid is visible as `current_version_invalid` until a valid successor advances the pointer. Leading BOM/whitespace before either accepted opening shape is not tolerated (column-zero contract, no silent healing); pre-PR-2 records — including heading-first versions stored valid and PR-1-era heading-first invalid evidence — keep reading exactly as persisted.
5. A later edit always creates a new immutable version/hash; it never mutates historic content or sessions that previously received another version. Every version's parent is the current version it was authored against; version rows and events are never rewritten.
6. The author or authorized editor may retire a custom skill. Retirement blocks future visibility but preserves history and materialization evidence. Restoring a prior version is an explicit, audited operation.

### Eligibility and runtime flow

1. The Founder opens a custom skill's **Eligibility** surface (bearer-authenticated).
2. They set scoped policy rules: organization baseline, team additions/restrictions, and agent-specific overrides. Effective policy is additive; explicit deny wins. A rule can be saved only after impact preview and server validation.
3. The resolver evaluates the current valid, non-retired version plus eligibility. It returns visible/hidden status and the winning provenance for an agent.
4. A recipient agent receives a compact `hr:` skill index only when the policy resolves the skill as visible. Full body materialization occurs at its next session spawn; no existing session changes mid-run.
5. The runtime records the version/hash actually materialized. If materialization fails, the session follows the existing fail-closed, recoverable materialization behavior.

Eligibility is therefore the sole runtime visibility gate for a valid custom skill. It is not access control for the underlying capability, and a hidden skill does not revoke permissions an agent already has.

## Functional requirements

1. **FR-1 — Create-skill system skill.** A dedicated `create-skill` system skill is provided to verified active agent sessions whose actor identity is derived server-side from active task/session context; it is not authorized by Eligibility and is not a human/admin/RBAC grant. It explains package shape, validation, provenance, hard boundaries, and the supported submission command/API. It must never instruct the agent to change eligibility, publish itself, or bypass system/developer/user instructions.
2. **FR-2 — Agent-bound creation.** The agent path derives organization, agent, task, and session from verified active context; caller-supplied trusted identity fields are rejected. The service records source task/session/agent and task brief digest with the created version.
3. **FR-3 — Human authoring/editor.** The web console provides Create, Edit, Validate, Save version, History/Diff, Retire, Restore, loading/empty/error/denied states, and explicit validation findings. Editing a current version must produce a new version/hash, not mutate an existing historical/effective version.
4. **FR-4 — Package validity and safety.** A candidate must have valid metadata and `SKILL.md` conforming to the supported authoring contract (THR-210 PR 2 grammar: either a column-zero Markdown ATX heading — 1–6 `#` markers followed by whitespace or end of line, heading-first — or valid opening/closing `---` fence with a YAML mapping, then a Markdown body heading under the same ATX boundary; leading BOM/whitespace is not tolerated); cannot use protected/shipped/system slugs or namespace; cannot declare system contracts; and cannot include executable, credential, permission, sandbox, allow-rule, executor, or eligibility-writing behavior. Validation completes before any persistence and the deterministic validator records its rule/validator version and findings by package hash. An invalid candidate is appended as immutable validation/provenance evidence — one version row with deterministic findings, author/task/session provenance, and its content-addressed artifact — but never displaces an existing valid `current_version_id`, never becomes eligible (`current_version_invalid`), and is never materialized. Only initial creation with no prior version sets the first (even invalid) version as the current pointer. Protected-slug candidates are hard-rejected (a policy gate, not evidence); missing-body/missing-metadata requests are rejected with no residue. Append-only `(skill_id, content_hash)` uniqueness is never relaxed; stored pre-PR-2 records are never rewritten or silently healed. A byte-identical replay of any stored version (current or historical, valid or invalid evidence) conflicts as HTTP 409 `version_content_exists` (THR-210 PR 3) with zero residue — no artifact write or rewrite, no new version row, no new event/audit row, no `current_version_id` change; the 409 translation applies only to the version INSERT, while any later persistence failure stays a 500 with full artifact compensation.
5. **FR-5 — Custom-skill state.** At minimum, distinguish editable valid/invalid current version, retired status, and historical versions. Do not overload lifecycle with assignment or materialization state. A version's validation result is deterministic for its exact hash plus validator version.
6. **FR-6 — Eligibility editor.** Each valid, non-retired custom skill has a dedicated policy editor supporting organization, team, and agent allow/deny rules. The rule model is additive inheritance with explicit deny winning. The UI must distinguish target suggestion from actual rule target.
7. **FR-7 — Eligibility authority and preview.** Every eligibility write is server-authorized, validates referenced org/team/agent targets, is atomic, creates an audit event, and shows a dry-run impact preview: agents newly visible, newly hidden, unchanged, and the rule/provenance causing each outcome.
8. **FR-8 — Effective Skills explanation.** Catalog/detail/agent inspector APIs and UI show the exact custom version, validation state, eligibility provenance, hidden reason, assignment-free visibility result, and last materialized version/hash/session where applicable. System contracts appear read-only and separately.
9. **FR-9 — Next-session effect.** Saving content or eligibility never changes a running session. The UI must state whether a change is available to future sessions, and materialization evidence determines actual effect.
10. **FR-10 — Audit and provenance.** Append-only product audit records cover creation source, agent task/session provenance, human edits, validation, version changes, eligibility rule changes, retirement/restoration, preview/request outcome, and materialization. An edit or unassignment never erases prior evidence.
11. **FR-11 — Protection boundaries.** API and UI must deny agent eligibility/config writes and all custom edits to system or first-party shipped skills. No custom package can shadow an existing protected slug; no save may change permissions.
12. **FR-12 — B2 cutover.** B2 custom-skill versions, provenance, validation events, and materialization history are retained. Legacy proposal and direct-write paths must be deleted rather than preserved as competing or compatibility paths.

## Data and provenance requirements

| User-visible claim | Required authoritative data |
| --- | --- |
| “Created by agent from this task” | Verified agent ID, org, task ID, session ID, creation timestamp, brief digest; not request-body claims. |
| “This is version X / hash Y” | Immutable version record, canonical content hash, parent version where applicable. |
| “Validation passed” | Validation run tied to exact hash and validator-rule version, with findings. |
| “Visible to this agent” | Resolver result plus organization/team/agent policy provenance and any deny rule. |
| “Will take effect next session” | Current valid version + saved policy result; no claim of current-session mutation. |
| “Effective in a session” | Successful materialization record with session ID, version, hash, and timestamp. |
| “Retired/restored” | Append-only action actor, rationale, affected version, prior/new current pointer, and future-visibility result. |

Custom skill content is organization-scoped. It must not be silently copied across organizations. References/assets, if supported, remain local, validated package content with explicit size/type/path constraints. Retention of audit metadata remains durable; package content follows existing internal artifact/content retention until a separate retention policy changes it.

## Web module requirements

The Skills console becomes an authoring and policy module, not a proposal review console.

| Surface | Required behavior |
| --- | --- |
| **Catalog** | Shows first-party/read-only versus custom/editable source clearly. Displays name, description, current version/validation, retired state, and aggregate visibility; does not imply permission grants. |
| **New custom skill** | Lets an authorized human select safe metadata, author `SKILL.md`, validate, save, and see next steps. Protected namespaces and prohibited policy class/content have clear errors. |
| **Custom-skill detail/editor** | Full editable content for authorized humans; metadata, validation findings, version/diff history, agent/task provenance where available, retire/restore, and no confusing approval/publish ceremony. |
| **Agent creation result** | A task-created custom skill opens to the same detail/history; shows agent/task/session provenance and validation. It does not automatically expose the skill. |
| **Eligibility** | Dedicated tab/page per custom skill with rule editor, org/team/agent inheritance, explicit deny, impact preview, effective skills link, save confirmation, and audit history. |
| **Agent Effective Skills** | Explains visible/hidden result per agent, current eligible version, policy provenance, and last materialization. Context selector affects system-contract display only, not custom-skill eligibility. |
| **System contracts** | Always-on/read-only context view only; no create/edit/eligibility controls. |
| **Audit/validation** | Filters content, validation, policy, retirement, and materialization events without representing technical validation as human approval. |

Every mutating page must provide labeled loading, blank, validation-error, server-error/retry, success, stale-action, and server-403 states. Keyboard access, focus management, semantic labels, non-color status cues, destructive-action confirmation, and readable policy provenance are required.

## API and implementation dependencies

This PRD does not prescribe endpoint names, but it requires separate server-authorized resources for:

- custom skill create/read/update/version-history/retire/restore;
- agent task/session-bound create/update submission used by `create-skill`;
- deterministic validation run/result by immutable package hash;
- custom-skill eligibility policy read/write, dry-run impact preview, and explain;
- catalog/detail/effective-skills/materialization/audit read projections; and
- B2 cutover confirmation that legacy proposal and direct-write routes are deleted.

Engineering must validate the source-of-truth split before build: release-owned first-party catalog versus runtime-writable organization custom-skill content and eligibility configuration. The custom package store and eligibility store need atomic writes, rollback-safe history, and server-side scope checks. No new human identity/RBAC model, delegated administration, or expansion of agent mutation authority is authorized; the human path remains the existing bearer-authenticated Founder channel. Database schema changes and migration/cutover plans require normal Engineering design review.

## Success signal

The module is successful when a repeated workflow can become a reusable skill without a manual engineering change, while operators can prove exactly why it is or is not visible.

Initial measures:

- Time from a valid authoring task or human intent to a validated custom-skill version.
- Percent of custom-skill creations completed without operator/engineering repair.
- Eligibility change preview-to-save completion and server validation failure rate.
- Time from eligibility save to a matching next-session materialization.
- Materialization failure/mismatch rate and successful audit reconstruction rate.
- Count of denied attempts to change eligibility/permissions from an agent path (expected: denied, zero successful).
- Weekly qualitative review: whether each new skill eliminated a repeatable task failure or ambiguity, rather than merely increasing catalog size.

## Phase scope

### v1 cutline

- `create-skill` system skill and supported verified-session agent authoring path.
- Human web create/edit/validate/version-history/retire/restore for custom `standard_operational` skills.
- Protected slug/source enforcement and deterministic package validation.
- Per-custom-skill eligibility editor at organization, team, and agent scope; additive inheritance plus explicit deny; Founder-only atomic save; impact preview and effective-skills explanation.
- Source/task/session provenance, validation/version history, policy audit, next-session-only messaging, and materialization evidence.
- B2 as the only supported custom-skill authoring path, retaining B2 version/provenance/validation/materialization evidence.

### Later

- Custom `high_impact_policy` skills and any separate human review/admission requirement for that class.
- Marketplace, cross-org sharing, skill templates/gallery, collaborative editing, comments, ratings, AI-generated eligibility recommendations, bulk matrix editor, import/export, customer self-service, or public distribution.
- Automatically generated authoring tasks, automatic assignment, automatic retirement, or any permission integration.

## Acceptance criteria

1. A permitted agent can use `create-skill` in an active task to create a valid custom `standard_operational` skill with server-derived task/session/agent/org provenance and clear validation output.
2. A permitted human can create, edit, validate, save a new version, view an immutable prior-version diff, retire, and restore a custom skill through the web console.
3. An agent-created and a human-created skill use the same custom-skill detail, version, validation, and eligibility model; neither becomes visible merely because it was created.
4. An agent cannot create/edit a system or first-party skill, shadow a protected slug, write eligibility, assign a skill, alter a permission, or spoof task/agent/org provenance; direct attempts receive server denials and leave no partial policy/config mutation.
5. A valid custom skill can be allowed at organization scope, restricted at team scope, and explicitly denied at agent scope; deny wins, resolver provenance names the winning rule, and a preview accurately lists the changed agents.
6. An authorized eligibility change is atomic, audit-recorded, and takes effect only for new sessions. Existing sessions retain their already materialized version/hash.
7. Agent Effective Skills truthfully distinguishes valid-but-hidden, visible-but-not-yet-materialized, and successfully materialized, with version/hash and policy explanation. The `happyranch skills effective` diagnostic consumes the authenticated effective-skills projection for B2 custom output rather than a second CLI resolver; it retains release-managed local output and explicitly labels B2 status unavailable when the daemon cannot be read.
8. Invalid or retired custom versions cannot become newly visible or materialize; validation failures remain actionable and recorded against the exact hash/validator version.
9. The console presents loading, empty, populated, validation failure, conflict/stale-action, server error/retry, and forbidden states accessibly across editor and eligibility workflows.
10. B2 version, validation, eligibility, provenance, and materialization evidence is retained, and no legacy proposal or direct-write route remains.

## Closed decisions (v1 authority ruled)

All Founder decisions for v1 are closed by THR-055 seq 187, 200, and 205:

1. **Human authority model.** All human custom-skill writes (create, edit, validate, version, retire, restore) and eligibility writes (org/team/agent scope) are Founder-only via the pre-existing bearer-authenticated path. No named organization administrators, team administrators, or delegated human roles exist in v1. Server enforcement is mandatory via the existing bearer channel.
2. **Agent update authority.** Agents may create and update only their own originated custom skills using verified active task/session identity. Agents cannot update skills originated by other agents or humans.
3. **Default eligibility.** Default hidden (no eligibility rules). The Founder must make an explicit policy change to expose a custom skill.
4. **v1 content class.** `standard_operational` only. Custom high-impact doctrine requires a separate governance decision and is out of scope for v1.
5. **B2 cutover disposition.** Keep B2 custom-skill version/provenance/validation/materialization evidence and default hidden eligibility; delete the legacy proposal workflow and direct mutation routes without compatibility behavior.

### Risks and mitigations

- **Behavioral drift from rapid authoring:** require validation, default-hidden eligibility, durable provenance, version history, and clear owner/action audit. Evaluate usefulness weekly, not catalog volume.
- **Eligibility becomes shadow permission control:** UI/API copy and tests must state and prove it controls guidance visibility only. Keep all permission systems outside this module.
- **Scope conflict between content and policy:** separate content editing from eligibility writes and make each server-authorized/audited. An agent cannot perform the policy half.
- **Cutover leaves a legacy writer:** delete it; do not keep proposal-only or direct legacy writes as undocumented alternatives.
- **Overbroad org rules:** require impact preview and explicit confirmation for organization scope; show deny precedence and affected-agent count before save.
- **False runtime claims:** show materialization evidence, not merely an assignment/policy save, before claiming a version is effective.

Implementation is authorized under THR-055 seq 187, 200, and 205 per the closed v1 authority decisions above. No further Founder rulings are required before Engineering proceeds with the authority, data-model, migration, and API plan.
