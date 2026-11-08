# Knowledge Distill and Instill

| Field | Value |
| --- | --- |
| Status | draft |
| Owner | Product Lead (`product_lead`) |
| Date | 2026-11-08 |
| Source Links | THR-296 seq289, 294, 297–299; TASK-14394 recommendation; TASK-14491. Source baseline: [main b4e9518e](https://github.com/t-benze/happyranch/tree/b4e9518e2f5b328a50117b8c9b0257084bb286f6). |
| Commitment Boundary | analysis-only; no build, roadmap, release, timeline or operational authorization |
| Founder Decisions | **Ruled:** abandon agent moves; two independent Founder actions: Distill on a retired agent’s page, Instill on another agent’s page with available-archive selection. **Required:** ratify recommended v1 corpus/retention and import defaults below before scope becomes build-ready. |

## Problem

Useful knowledge should outlive an agent’s execution and be reusable without moving its identity. Existing memory tools support manual curation, not this complete feature. The earlier recommendation to prepare a handoff before retirement (seq293–294) is not a prerequisite for the new feature: seq297 requires post-retirement Distill and an independent Instill action.

**Everything below the Founder-ruled core is Product’s recommended v1 contract, not a delivery commitment.** Distillation is lossy curation, not complete knowledge, identity or conversation transfer.

## Users and Workflow

The Founder opens retained retired-agent detail → **Distill knowledge** → reviews source coverage → starts processing → inspects a durable Ready archive. No recipient need exist. Separately, on any eligible active agent’s detail: **Instill knowledge** → choose a same-org Ready archive → preview and select items → explicitly confirm → inspect the target receipt. Existing agents, different teams and different provider types are supported; no successor designation, enrollment or retirement is coupled to either action.

## Goals

- Preserve attributable, useful knowledge independently of the source’s execution and any recipient.
- Add chosen knowledge without changing existing recipient notes, identity, role, team or authority.
- Make omissions, failures and actual import effects visible and retrievable.

## Non-Goals and No-List

No agent move/refactor; automatic enrollment/retirement; task/thread ownership transfer; identity/history rewriting; cross-org transfer; raw folder/session cloning; all-history summarization by default; cross-archive semantic deduplication engine; or automatic shared-KB publication. No lifecycle redesign, cleanup, operation/key/custody transfer or recovery of abandoned moves follows. Former task approvals, manager directives and open-work references never become current assignments or permissions.

## Success Signal

The Founder completes the actual retired-source-page → durable archive → separate active-recipient-page selection → confirmed import journey. A fresh recipient session retrieves selected facts, a sourced Founder decision and an open-work reference with correct attribution and current owners/holds. All selected items have exact outcomes and retrievable memory mappings; zero duplicate writes on repeat import and zero unintended changes to originals, existing notes or role. Distill success and Instill success are separate; neither promises recall of every item in every response.

## Phase Scope

**v1 cutline:** Founder-only independent actions, retained structured runtime memories, explicit coverage, immutable Ready versions, selective additive import, durable receipts and safe partial retry. Required access/storage gaps must be resolved before this is operable.

**Later:** provider-local text ingestion, additional source categories, archive editing/deletion policy, cancellation if not supported, and richer conflict assistance. These are deferred, not scheduled.

## Functional Requirements

### Distill

1. Start only from an actually retired agent’s retained detail. Show retirement, source permanent ID, available/unavailable categories and coverage exclusions before confirmation. Missing required data blocks with a reason; never offer “run the old agent first.” A separately owned, currently authorized processing operation reads preserved data. It must not launch/revive the source, resume its sessions, inherit its permissions, modify originals, select/create a successor, or write recipient memory/KB.
2. Runtime-managed structured memory is required. Inventory every preserved entry, including promoted pointers, superseded and evicted entries, rather than just the index/digest. Curate durable facts, lessons, procedures, attributable Founder preferences/decisions and open-work **references**. Keep known stale/superseded material reference-only and unselectable, with its reason; show unresolved uncertainty/conflicts without asserting current truth. Current Founder rulings prevail over historical instructions. Provenance-only task summaries/references may support selected entries; no unrestricted history ingestion.
3. Show **Queued / Processing / Ready / Failed**, operation ID, observable progress, last update and actionable failures; do not invent percentages. Closing/reopening the page preserves access. Cancellation is omitted unless a supported safe cancellation path exists. Required read/curation/publication failures produce Failed, never a selectable partial archive. A verified empty corpus may produce an explicitly empty Ready archive with zero eligible items; unreadable data is not “empty.”
4. Publish availability only after the complete declared corpus has been processed and archive/manifest durably saved. Each archive has an ID and immutable identifiable Ready version. A fresh Distill or explicit retry after a confirmed Failed attempt creates a new attempt/version, preserving failure history and any prior Ready version. While pending or after uncertain submission, reopen the original operation before starting another; no automatic duplicate attempt. The available list and archive contents outlive source execution, retirement and job completion, independent of provider folders or task scratch. No automatic v1 deletion.

### Instill

5. On an active agent’s page, list all same-org Ready archives across source agents/teams/providers, with source ID/name, version, date, coverage, item count and prior imports. Failed/partial archives are unavailable; no archives has an explanatory empty state. One archive can serve many recipients; one recipient can receive many archives, **one confirmed operation at a time**. Reject source and recipient with the same permanent ID; this never enables reactivation. Re-importing an archive into a prior recipient is allowed under the idempotence rules.
6. Preview item text, provenance, coverage, warnings and this recipient’s already-imported mappings. Default-select all eligible, not-yet-imported items; allow deselection. Known superseded/stale items, forbidden content and unavailable bodies are ineligible and visibly explained. Uncertain/conflicting eligible knowledge carries explicit warnings. Confirmation binds the exact recipient, archive version and selected item IDs; zero selected items writes nothing. No import on preview/navigation. A changed selection requires fresh confirmation.
7. Eligibility requires an active approved recipient, initialized writable runtime memory and current authorization. Pending/retired recipients cannot import. V1 requires the recipient idle at write admission: active task, thread, dream or other supported memory writer produces **Busy** with actual blocker references and “finish/wait, then retry.” This writes nothing; no global shutdown, team move or automatic cancellation. Revalidate target/authority before writes and pause remaining work if eligibility is lost. A resumed old conversation is not rewritten. Imported items are guaranteed retrievable through the normal memory interface in a fresh session after completion; the UI explicitly states that boundary.
8. Add each item under a new recipient-local MEM ID, with visible inherited-source attribution. Preserve existing bodies, IDs, lifecycle and provenance: no replacement, deletion, superseding links or import-triggered compaction of existing notes. Current target charter, permissions and identity remain authoritative. Flag observed conflicts; do not claim exhaustive conflict detection or that imported content is newer authority. A later archive version is separately selectable and cannot silently update earlier imports; show related prior imports for Founder review.
9. Identity is `(org, archive, version, item, recipient permanent ID)`. The same item can create at most one target note. Repeated confirmation/retry returns its existing receipt/mapping; already imported items are skipped, never duplicated. An operation retains its fixed confirmed selection. Successful item writes and their exact receipts must be durably reconciled before retry; uncertainty holds that item for reconciliation, never a blind second write. Partial failure retains successes without rollback/deletion; **Resume missing items** retries only failed/unwritten selected items under the same operation. A new selection is a new confirmation, still respecting prior item identity. No abandoned move machinery is required by this contract.
10. Show operation ID, archive/version/source, target, status (**Queued / Processing / Succeeded / Partially failed / Failed**), timestamps and item results. While running, show pending counts; at termination, selected = imported + skipped + failed (unattempted items are failed with that reason). Record skipped reasons and mappings for already imported items. Every success maps archive item → retrievable target MEM ID. Succeeded means no selected items remain failed/pending and all mappings are durable. Lost response/page reload must expose the original receipt. Target detail persistently lists learned sources, versions, counts and receipt links.

## Data and Provenance Requirements

Archive manifest: org/archive/version/operation IDs, source permanent ID and captured display name/team/provider, source observation cutoff, processing date/owner, category coverage with counts or explicit unknowns, exclusions/errors and item inventory. Each curated item retains stable item ID, original qualified MEM/task/thread references, source/observed dates (unknown explicitly), original attribution and uncertainty/conflict/supersession flags. Open-work references preserve observed owner, status/date, next action and holds; they neither assert current status nor assign work.

Imported notes retain this provenance in readable content and persistent receipt links, with the actual import date separate from source dates. Current `memory add` stamps the recipient as author and defaults to experiential provenance; this must never imply the recipient experienced inherited material. Read-only reference access does not grant new permissions. Archives contain curated knowledge, not credentials, generated instructions/permissions, skills, provider resume/cache state or session identity. No automatic KB promotion: `memory promote` only replaces a local note with a pointer to an **existing** KB entry; it creates/copies no KB content.

## Acceptance Criteria

The appendix defines required future evidence, **all NOT RUN**. This task authorizes zero tests, new cases/fixtures/campaigns, provider calls or live product writes. Existing no-new-unit/integration-case and execution holds persist until a concrete later Founder ruling. A source review, mock, intercepted save, capability response or manual-memory demonstration cannot establish shipped end-to-end success.

## Open Questions and Risks

**Founder decision:** ratify the recommended runtime-memory-only v1 corpus, explicit provider-text exclusions, durable archives with no automatic deletion, and additive/idle-recipient import defaults. Including provider-local text now increases retention/access scope and delays feasibility closure; recommending exclusion accepts visibly incomplete capture. This ruling alone would not authorize build, verification spend or delivery commitments.

**Dependencies, not extra lifecycle scope:** retained retired-detail navigation/read access, preserved source data, durable archive/receipt ownership, and reliable additive/idempotent writes need Engineering feasibility. Current manager/empty-team retirement has no supported Founder path identified; worker-first retirement does not fix it. That separately owned lifecycle decision must not require a retired source to execute. Product owns scope, Engineering later owns bounded feasibility/build planning, Founder owns final rulings and commitments.

## Appendix: Coverage, Dependencies and Future Acceptance

### v1 source coverage decision

| Category | Recommended v1 treatment and feasibility boundary |
| --- | --- |
| Preserved structured runtime MEM entries | Required read-only corpus, all lifecycles inventoried. Valid bodies feed curation; stale/superseded/evicted items and promoted KB pointers retain provenance but are not automatically revived/copied. Required unreadable/malformed/missing entries fail the attempt. Retired-path reader is missing from ordinary memory routes. |
| Legacy flat `learnings.md` / `learnings/` | Unsupported in v1 until nonmutating read feasibility is established; if the source uses only these, Distill is unavailable. No lazy migration/reindex of retired originals. Show this category explicitly. |
| Saved task summaries, cited tasks/threads | Optional provenance for selected memories; record unavailable references. No raw full outputs, attachments or conversation ingestion by default; no task/thread assignment changes. |
| Claude provider-local memory text | Known candidate from TASK-14394: notes bound to the prior workspace path, potentially outside the archived workspace. Excluded in v1; no verified retained reader/content inventory. Show “not captured; availability unverified,” not zero memories or complete capture. |
| Codex / OpenCode / Pi / custom-provider local memory text | No normalized retained text reader identified in inspected executor/workspace paths. Excluded in v1; existence/location/retention unverified per provider. This is not evidence that providers have no memory. |
| KB bodies; raw sessions/folders; credentials, caches, generated authority, skills, identity | KB links retained without importing shared bodies. Other categories excluded and never cloned; no shared-KB writes. |

### Source evidence and dependencies

Pinned read-only source `b4e9518e2f5b328a50117b8c9b0257084bb286f6`; installed/loaded behavior is not attested. TASK-14394 inspected installed `54b7146c` and exercised no transfer. Current source shows:

- `runtime/daemon/routes/agents.py`: `terminate` archives quiescent worker definitions/workspaces under `_terminated`; manager termination refuses. `list_enrollments(status=terminated)` exposes summaries, but active `/agents` excludes them. `AgentDetailPane.tsx` resolves from that active list; `AgentsPage.tsx` terminated view exposes names, not the required knowledge detail. Retained detail is a feature dependency, not delivered evidence.
- `_workspace_memory_store` resolves only active workspace paths and calls migration. It is not a read-only retired-source API. Provider-local external notes have no established retirement retention guarantee.
- `MemoryStore.write_entry` can auto-compact; plain `memory add` lacks inherited provenance and archive-item receipts. Reuse needs bounded feasibility, not a claim that looping existing commands satisfies Instill.

### Required future acceptance evidence — NOT RUN / HELD

| Boundary | Required observation after explicit later authorization |
| --- | --- |
| Full journey | Actual retained retired-page start without source launch → complete durable archive, visible after job completion/reopen → independently selected on another active page → selected-item confirmation → new target MEM mappings → fresh real recipient retrieval with correct attribution/role/holds. Include an existing target and cross-team/provider coverage before claiming that support. |
| Failed Distill | Required-source/processing failure produces Failed, unselectable output; explicit retry/new version leaves earlier Ready archive selectable and source retired/unchanged. |
| Repeat and partial import | Repeat same-version selection produces zero duplicates; partial success retains exact mappings/counts after reopen, and resume writes only missing items. New version never overwrites old imports. |
| Preservation and admission | Predecessor originals/history and target preexisting notes/lifecycle/role/team/permissions unchanged; new IDs and provenance readable. Busy/ineligible target, non-Ready/foreign-org archive and no selection cause no writes; same-source-ID import refuses. |

Fallback evidence may include pinned source tracing, authorized isolated demonstrations or substituted boundaries, each explicitly naming what did not execute. It can support feasibility/source acceptance only; no synthetic/source-only/intercepted-save result substitutes for this complete journey. No verification or lifecycle work is commissioned by this PRD.

**Prior holds unchanged:** earlier preview 1/1 spent; newer 1/2 charged, zero successes, remaining maximum 1 WITHHELD; both old move implementation/verification allowances unapproved. Preserve original saved move keys/bodies/custody and unknown TASK-14014/JOB-5867 effects/handler settlement. No new allowance, send, cleanup, recovery or transfer follows.
