# Custom skill creation and eligibility PRD

| Field | Value |
| --- | --- |
| Status | draft — decision-ready for Founder and Engineering review |
| Owner | Product Lead |
| Date | 2026-08-05 |
| Source Links | THR-055 seq. 187–190; PR #555 `2026-08-04-skills-governance-prd.md`; TASK-3380; TASK-3436 / PR #507; TASK-3509 / PR #511; THR-092 Skill Management web-module brief |
| Commitment Boundary | analysis-only — this resets custom-skill product scope and authorizes neither implementation nor a delivery timeline. |
| Founder Decisions | Ruled: custom skills may be created by agents through `create-skill` and by humans through the web editor; eligibility is configured per agent, team, or organization; skills remain guidance-only. Required: editor/eligibility authority model and first release cutline (Section 12). |

## Recommendation

Make **custom-skill creation** the primary Skills workflow. A custom skill may originate in either of two ways:

1. An agent uses an injected, proposal-safe `create-skill` skill to author and submit a validated custom-skill record from a task.
2. A human creates or edits the same kind of custom-skill record directly in the Skills web console.

Creation produces reusable, editable guidance; it does **not** expose that guidance to any agent. A separate Eligibility surface then determines visibility by organization, team, or individual agent, with an effective-skills explanation and impact preview. This is the intentional activation step.

This replaces the current proposal-review workflow as the normal path for custom skills. Preserve its useful safeguards—provenance, deterministic validation, immutable version history, audit, rollback, and next-session materialization—but remove Founder claim/review/publish as a required ceremony for ordinary `standard_operational` custom skills. The previously designed Founder Proposals queue/detail is therefore not the next implementation target.

The trade-off is deliberate: ordinary authoring becomes fast enough to capture useful workflow knowledge, while control of who sees it remains explicit and auditable. We give up universal pre-publication review; we do **not** give up validation, history, eligibility control, or the firewall between guidance and permissions.

## Problem and outcome

The present lifecycle treats an agent-authored skill as an exceptional proposal that must wait for a Founder review/publish sequence before it can become a usable custom skill. That is misaligned with the need: teams need a practical way to author reusable guidance, whether the initial author is an agent that discovered a repeatable workflow or a human who already knows the needed practice.

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
| Human author/editor | Create, edit, validate, inspect history, and retire custom guidance. | Use the web editor within server-authorized organization scope. |
| Eligibility administrator | Decide where approved custom guidance is visible. | Set organization, team, and agent allow/deny policy; preview impact; roll back/unassign visibility within authorized scope. |
| Recipient agent | Use relevant guidance. | Receive only the version resolved for a new session; cannot alter the skill or its eligibility merely because it can see it. |
| Engineering/support | Diagnose bad or missing materialization. | Read provenance, policy explanation, validation, version, and materialization evidence. |

System contracts (`start-task`, `thread`, `jobs`, `make-worktree`, `dream`) remain runtime-owned, context-gated, and non-toggleable. First-party shipped skills remain release-managed. Global CLI/plugin skills remain outside HappyRanch custom-skill management. A custom skill never grants tools, credentials, network access, filesystem scope, sandbox changes, allow rules, executor configuration, or command authority.

The exact human authority model is intentionally open: the recommended default is Founder plus explicitly authorized organization administrators for custom-skill editing and organization-wide eligibility, with team administrators limited to their own team and its agents. The server—not button visibility—must enforce whichever model the Founder rules.

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
- No requirement to implement the previously approved Founder-only Proposals queue/detail as the normal custom-skill workflow. It may be repurposed later as history/audit evidence only after a separately scoped design decision.

## Product model and workflow

### One custom-skill record, two creation paths

Every custom skill has a stable organization-scoped slug, metadata, a current editable version, immutable historical versions/hashes, validation evidence, author provenance, eligibility policy, and materialization history.

| Path | Initiator | Required outcome | Not permitted |
| --- | --- | --- | --- |
| Agent `create-skill` | Verified active agent task/session | Create a new custom skill or a new version with task/session/agent provenance; run deterministic validation; surface errors. | Eligibility writes, assignment, permission changes, protected-slug edits, system/first-party changes. |
| Human web editor | Authorized human in Skills console | Create/import, edit, validate, save a new version, inspect diff/history, retire/restore. | System/first-party changes, unsupported package contents, authority outside server scope. |

Both paths converge on the same editable custom-skill record. An agent-created skill is not second-class and does not require an artificial publish transition. It remains non-effective until eligibility exposes it.

### Creation and update flow

1. The agent invokes `create-skill` for a reusable behavior, or a human chooses **New custom skill** in the console.
2. The author supplies metadata and `SKILL.md` guidance. The system derives identity/task/session/org for the agent path; a human path records authenticated actor and organization scope.
3. The service validates the exact candidate version. Validation checks package structure, protected slug/path collision, supported reference/asset limits, and prohibited executable/permission-bearing content. A pass is technical evidence, not a claim that the guidance is good.
4. On success, the version is saved and becomes the current editable custom-skill version. On failure, it remains editable with actionable findings but cannot be made eligible.
5. A later edit always creates a new immutable version/hash; it never mutates historic content or sessions that previously received another version.
6. The author or authorized editor may retire a custom skill. Retirement blocks future visibility but preserves history and materialization evidence. Restoring a prior version is an explicit, audited operation.

### Eligibility and runtime flow

1. An authorized human opens a custom skill's **Eligibility** surface.
2. They set scoped policy rules: organization baseline, team additions/restrictions, and agent-specific overrides. Effective policy is additive; explicit deny wins. A rule can be saved only after impact preview and server validation.
3. The resolver evaluates the current valid, non-retired version plus eligibility. It returns visible/hidden status and the winning provenance for an agent.
4. A recipient agent receives a compact `hr:` skill index only when the policy resolves the skill as visible. Full body materialization occurs at its next session spawn; no existing session changes mid-run.
5. The runtime records the version/hash actually materialized. If materialization fails, the session follows the existing fail-closed, recoverable materialization behavior.

Eligibility is therefore the sole runtime visibility gate for a valid custom skill. It is not access control for the underlying capability, and a hidden skill does not revoke permissions an agent already has.

## Functional requirements

1. **FR-1 — Create-skill system skill.** A dedicated `create-skill` skill is available only to authoring-eligible agent sessions. It explains package shape, validation, provenance, hard boundaries, and the supported submission command/API. It must never instruct the agent to change eligibility, publish itself, or bypass system/developer/user instructions.
2. **FR-2 — Agent-bound creation.** The agent path derives organization, agent, task, and session from verified active context; caller-supplied trusted identity fields are rejected. The service records source task/session/agent and task brief digest with the created version.
3. **FR-3 — Human authoring/editor.** The web console provides Create, Edit, Validate, Save version, History/Diff, Retire, Restore, loading/empty/error/denied states, and explicit validation findings. Editing a current version must produce a new version/hash, not mutate an existing historical/effective version.
4. **FR-4 — Package validity and safety.** A candidate must have valid metadata and `SKILL.md`; cannot use protected/shipped/system slugs or namespace; cannot declare system contracts; and cannot include executable, credential, permission, sandbox, allow-rule, executor, or eligibility-writing behavior. The deterministic validator records its rule/validator version and findings by package hash.
5. **FR-5 — Custom-skill state.** At minimum, distinguish editable valid/invalid current version, retired status, and historical versions. Do not overload lifecycle with assignment or materialization state. A version's validation result is deterministic for its exact hash plus validator version.
6. **FR-6 — Eligibility editor.** Each valid, non-retired custom skill has a dedicated policy editor supporting organization, team, and agent allow/deny rules. The rule model is additive inheritance with explicit deny winning. The UI must distinguish target suggestion from actual rule target.
7. **FR-7 — Eligibility authority and preview.** Every eligibility write is server-authorized, validates referenced org/team/agent targets, is atomic, creates an audit event, and shows a dry-run impact preview: agents newly visible, newly hidden, unchanged, and the rule/provenance causing each outcome.
8. **FR-8 — Effective Skills explanation.** Catalog/detail/agent inspector APIs and UI show the exact custom version, validation state, eligibility provenance, hidden reason, assignment-free visibility result, and last materialized version/hash/session where applicable. System contracts appear read-only and separately.
9. **FR-9 — Next-session effect.** Saving content or eligibility never changes a running session. The UI must state whether a change is available to future sessions, and materialization evidence determines actual effect.
10. **FR-10 — Audit and provenance.** Append-only product audit records cover creation source, agent task/session provenance, human edits, validation, version changes, eligibility rule changes, retirement/restoration, preview/request outcome, and materialization. An edit or unassignment never erases prior evidence.
11. **FR-11 — Protection boundaries.** API and UI must deny agent eligibility/config writes and all custom edits to system or first-party shipped skills. No custom package can shadow an existing protected slug; no save may change permissions.
12. **FR-12 — Migration.** Existing proposal/immutable records, current custom skills, validation events, and materialization history are preserved and mapped into the new custom-skill/version history. Existing direct write endpoints and proposal-only transitions must not remain competing, hidden paths after cutover; Engineering must present an explicit compatibility/cutover plan.

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
- migration/cutover from current proposal-only and retired legacy custom-skill routes.

Engineering must validate the source-of-truth split before build: release-owned first-party catalog versus runtime-writable organization custom-skill content and eligibility configuration. The custom package store and eligibility store need atomic writes, rollback-safe history, and server-side scope checks. Any new human identity/RBAC model, delegated administration model, database schema change, or expansion of agent mutation authority needs Engineering design review and the Founder decision in Section 12 before implementation.

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
- Per-custom-skill eligibility editor at organization, team, and agent scope; additive inheritance plus explicit deny; server-authorized atomic save; impact preview and effective-skills explanation.
- Source/task/session provenance, validation/version history, policy audit, next-session-only messaging, and materialization evidence.
- A migration/cutover that makes this the only supported custom-skill authoring path and preserves existing evidence.

### Later

- Custom `high_impact_policy` skills and any separate human review/admission requirement for that class.
- Marketplace, cross-org sharing, skill templates/gallery, collaborative editing, comments, ratings, AI-generated eligibility recommendations, bulk matrix editor, import/export, customer self-service, or public distribution.
- Automatically generated authoring tasks, automatic assignment, automatic retirement, or any permission integration.
- A repurposed proposal-review queue, if later needed as an audit/inbox experience rather than a universal publication gate.

## Acceptance criteria

1. A permitted agent can use `create-skill` in an active task to create a valid custom `standard_operational` skill with server-derived task/session/agent/org provenance and clear validation output.
2. A permitted human can create, edit, validate, save a new version, view an immutable prior-version diff, retire, and restore a custom skill through the web console.
3. An agent-created and a human-created skill use the same custom-skill detail, version, validation, and eligibility model; neither becomes visible merely because it was created.
4. An agent cannot create/edit a system or first-party skill, shadow a protected slug, change eligibility, assign a skill, alter a permission, or spoof task/agent/org provenance; direct attempts receive server denials and leave no partial policy/config mutation.
5. A valid custom skill can be allowed at organization scope, restricted at team scope, and explicitly denied at agent scope; deny wins, resolver provenance names the winning rule, and a preview accurately lists the changed agents.
6. An authorized eligibility change is atomic, audit-recorded, and takes effect only for new sessions. Existing sessions retain their already materialized version/hash.
7. Agent Effective Skills truthfully distinguishes valid-but-hidden, visible-but-not-yet-materialized, and successfully materialized, with version/hash and policy explanation.
8. Invalid or retired custom versions cannot become newly visible or materialize; validation failures remain actionable and recorded against the exact hash/validator version.
9. The console presents loading, empty, populated, validation failure, conflict/stale-action, server error/retry, and server-denied states accessibly across editor and eligibility workflows.
10. Existing proposal, validation, version, assignment/eligibility, and materialization evidence is preserved through the migration, and no old proposal-only or legacy direct-write route remains a parallel mutation path.

## Open questions, risks, and Founder decisions

### Founder decisions required before engineering build planning

1. **Human authority model.** May the Founder alone edit custom skills and set eligibility, or may explicitly authorized organization/team administrators do so within scope? **Recommendation:** Founder + named organization administrators for org-wide content/policy; team administrators limited to their own team and direct agents. Server enforcement is mandatory.
2. **Agent update authority.** May an agent update only a custom skill it originally created, or may it propose a new version of any non-protected organization custom skill? **Recommendation:** v1 permits creation plus a new version only of the agent's own skill; human editors can update any authorized custom skill. This keeps provenance and ownership clear.
3. **Default eligibility.** What should happen immediately after creation? **Recommendation:** default hidden (no eligibility rules). The author or editor must make an explicit policy change to expose it.
4. **v1 content class.** Does v1 remain `standard_operational` only? **Recommendation:** yes. Custom high-impact doctrine requires a separate governance decision.
5. **Migration disposition.** Should the existing two pilot proposals be converted to ordinary custom skills and retained with provenance, or be archived as superseded evidence? **Recommendation:** migrate their submitted content/history into custom-skill records, default hidden, then retire the proposal-only workflow.

### Risks and mitigations

- **Behavioral drift from rapid authoring:** require validation, default-hidden eligibility, durable provenance, version history, and clear owner/action audit. Evaluate usefulness weekly, not catalog volume.
- **Eligibility becomes shadow permission control:** UI/API copy and tests must state and prove it controls guidance visibility only. Keep all permission systems outside this module.
- **Scope conflict between content and policy:** separate content editing from eligibility writes and make each server-authorized/audited. An agent cannot perform the policy half.
- **Migration leaves two write paths:** require one explicit cutover plan and compatibility behavior; do not keep proposal-only and direct legacy writes as undocumented alternatives.
- **Overbroad org rules:** require impact preview and explicit confirmation for organization scope; show deny precedence and affected-agent count before save.
- **False runtime claims:** show materialization evidence, not merely an assignment/policy save, before claiming a version is effective.

No implementation is authorized by this PRD until the Founder decisions above are ruled and Engineering validates the authority, data-model, migration, and API plan.
