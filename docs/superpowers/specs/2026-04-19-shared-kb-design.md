# Shared Knowledge Base — Design Spec

**Date:** 2026-04-19
**Status:** Draft, pending implementation plan.
**Relates to:** Implementation order step 8 in `CLAUDE.md`; blueprint references in `protocol/05a-teams.md`, `protocol/05b-agent-runtime.md`, `protocol/05e-dashboard.md`.

## 1. Goal

Add a shared, agent-contributed knowledge base so the org can accumulate two kinds of durable content:

1. **Precedents** — resolved escalations, founder decisions, incident post-mortems.
2. **Domain reference** — SOPs, regulatory rules, partner-API quirks, visa requirements, payment flow details, anything with a 12+ month expected useful lifespan that agents discover during work.

Any agent can read any entry. Any agent can write. Deletion is restricted to the Engineering Head.

## 2. Non-goals

The following are explicitly out of scope and must not creep in during implementation:

- **Agent-private operational learnings.** `learnings.md` stays per-agent and private; the KB is additive, not a replacement.
- **Task progress notes or drafts.** Those belong in `artifacts/<task_id>/` and are retrieved via the existing `opc recall`.
- **Mirrors of `protocol/` docs.** Authoritative org policy stays in `protocol/`; the KB complements, never duplicates.
- **Fast-changing state** (prices, current partner list, promotions). KB content has a 12-month expected lifespan.
- **RAG / embeddings / vector search.** Retrieval is deliberate CLI only (ripgrep + frontmatter tags). If corpus outgrows that, it's a future iteration.
- **Edit approval gate.** Collective contribution; no maker-checker on the KB itself.
- **Escalation-resolution automation (Feishu bot reply parsing).** That's blueprint step 10. This spec ships the founder-side CLI for writing precedents; the automated path hooks into the same code later.
- **Stale-content detection.** No automated rot alerts. Readers who find a wrong entry update it.
- **Git enforcement.** The KB lives inside the runtime dir. If the operator wants edit history, they `git init` the runtime themselves.

## 3. Storage

### 3.1 Layout

The KB lives at `<runtime>/kb/`, a peer of `workspaces/` and `opc.db`:

```
<runtime>/
├── opc.yaml
├── opc.db
├── workspaces/
└── kb/
    ├── _index.md
    ├── mainland-visa-tourist-90day.md
    ├── alipay-refund-endpoint.md
    └── precedent-refund-200usd-custom-itinerary.md
```

Flat folder. No subfolders. Filename equals slug. Switching runtimes via `opc use <path>` switches KBs; two runtimes = two KBs. Cross-runtime sync is out of scope.

### 3.2 Slug rules

- Regex: `^[a-z0-9][a-z0-9-]{0,63}$`.
- Kebab-case, ASCII only, max 64 chars.
- Filename = `<slug>.md`. Filename is the canonical identity; the `slug` frontmatter field must match the filename.
- Agents pick their own slug at creation. Exact-slug collisions are a hard 409 — use `update` instead.

### 3.3 Entry shape

YAML frontmatter + markdown body.

```markdown
---
slug: mainland-visa-tourist-90day
title: Mainland China tourist visa (90-day L-visa)
type: reference                       # reference | precedent
topic: visa                           # single kebab-case token, free-form; drives grouping in _index.md
tags: [mainland, tourist, 90-day]     # list[str], optional
authored_by: compliance_agent         # stamped server-side from --agent flag
authored_at: 2026-04-19T10:22:00Z     # stamped server-side
updated_by: compliance_agent          # stamped server-side
updated_at: 2026-04-19T10:22:00Z      # stamped server-side
source_task: TASK-037                 # optional; the task that produced the knowledge
supersedes: null                      # optional slug; explicit deprecation pointer
---

# Mainland China tourist visa (90-day L-visa)

<markdown body, ≤32 KiB>
```

**Server-owned fields** (`authored_at`, `authored_by`, `updated_at`, `updated_by`): any value the agent supplies is overwritten by the daemon. `authored_by` / `updated_by` are taken from the `--agent` CLI flag (same pattern as `opc learning --agent`).

**Agent-supplied required fields**: `slug`, `title`, `type`, `topic`. `tags`, `source_task`, `supersedes` optional.

### 3.4 `_index.md`

Machine-generated. Regenerated inside the write lock after every successful `add`/`update`/`delete`. Groups entries by `topic`, alphabetized within each group:

```markdown
# Knowledge Base Index

## visa

- `mainland-visa-tourist-90day` — Mainland China tourist visa (90-day L-visa)
- `macau-visa-transit` — Macau transit visa rules

## payment

- `alipay-refund-endpoint` — Alipay v3 refund endpoint quirks
...
```

Not a source of truth — deleting `_index.md` is safe; the daemon rebuilds it on next write or on explicit `opc kb reindex`.

## 4. CLI surface

All under `opc kb`. Agents use the sanctioned `Bash(opc:*)` allow rule — no new permission surface in agent workspaces.

```
opc kb list [--topic <t>] [--type reference|precedent]
opc kb get <slug>
opc kb search <query> [--limit N] [--json]
opc kb add      --agent <name> --from-file <path> [--force-new-sibling]
opc kb update <slug> --agent <name> --from-file <path>
opc kb delete <slug> --agent <name> --confirm [--as-founder]
opc kb precedent --task-id <id> --decision approve|reject --rationale "..." [--slug <s>] --as-founder
opc kb reindex

opc resolve-escalation --task-id <id> --decision approve|reject --rationale "..."
```

### 4.1 `--from-file` mandatory for `add`/`update`

Same reason `report-completion`, `manage-agent`, and `manage-repo` use it: Claude Code's `Bash(opc:*)` matcher rejects multi-line payloads. Agent writes `/tmp/kb-<slug>.md` with the full markdown (frontmatter + body), then invokes the single-line `opc kb add --agent <name> --from-file /tmp/kb-<slug>.md`.

### 4.2 Collision check on `add`

Inside `kb_lock`:

1. **Hard 409 `slug_exists`** if `<slug>.md` exists. Response includes existing title so the agent can decide: `update` that entry, or pick a different slug.
2. **Soft 409 `near_duplicate`** if any existing entry has
   - normalized-Levenshtein title similarity > 70%, **or**
   - ≥2 shared tags.

   Response payload:

   ```json
   {"code": "near_duplicate",
    "candidates": [{"slug": "...", "title": "...", "similarity": 0.82}],
    "suggestion": "update"}
   ```

   Agent bypasses with `--force-new-sibling` (documented in the guideline: use only when the topic is genuinely distinct).

No embeddings. Cheap title similarity + tag-set intersection only.

### 4.3 `search`

Thin wrapper over ripgrep across `<runtime>/kb/*.md`. Ranks:

1. Exact query phrase in `title`.
2. Phrase in body.
3. Term hits in `tags` / `topic`.

Returns slug + title + matched snippet. Plain text by default; `--json` for structured.

### 4.4 `delete` — EH-only

- Non-EH agents: 403 `delete_forbidden`.
- Missing `--confirm`: 400 `confirm_required`.
- `--as-founder` bypasses the EH restriction (same bearer token today; the flag is intent, not identity).
- Irreversible unless the runtime is git-initialized. The guideline says this bluntly.

### 4.5 `resolve-escalation` — founder-only, state transition

Separate command; one job: move an escalated task to its terminal status and log the founder's rationale. Does **not** touch the KB.

Behavior:

1. Resolve `TASK-<id>`. Require `status = ESCALATED` — else 409 `task_not_escalated`.
2. Transition `ESCALATED` → `APPROVED` (if `--decision approve`) or `REJECTED` (if `--decision reject`).
3. Append a new audit row (`log_escalation_resolved`) with founder decision + rationale so the precedent-writer downstream has a clean record to read.

`--rationale` is required — the audit trail's value is the *why*, not the verdict.

### 4.6 `kb precedent` — founder-only, KB write

Second command; one job: produce a precedent entry from an escalation audit trail. Does **not** touch task state.

Behavior:

1. Resolve `TASK-<id>`; read its brief, `assigned_agent`, and the most-recent `log_escalation` audit row (the escalation reason).
2. Require at least one escalation audit row exists for the task — else 400 `no_escalation_record`. Task status is *not* checked; a precedent can be captured after-the-fact regardless of whether the task was resolved via `resolve-escalation`, manual DB update, or a future Feishu handler.
3. Build a precedent entry:

   ```yaml
   slug: precedent-<task_id>-<approve|reject>   # or --slug override
   title: "<task brief> — <decision>"
   type: precedent
   topic: <inferred>                  # payment_change → payment; bug_fix/implement_feature → engineering; else → general
   tags: [precedent, <decision>]
   authored_by: founder
   source_task: TASK-<id>
   escalation_reason: "<from audit row>"
   founder_decision: approve | reject
   founder_rationale: "<from --rationale>"
   ```

   Body is a templated markdown with context / ask / decision / rationale sections.

4. Write via the same `_kb_write` helper as `opc kb add` — uniform collision rules and `_index.md` regeneration.

### 4.7 Typical founder flow

Two commands, explicit intent at each step:

```
opc resolve-escalation --task-id TASK-037 --decision approve \
    --rationale "Refund justified: vendor error per partner-log, <$250 risk"
opc kb precedent --task-id TASK-037 --decision approve \
    --rationale "Refund justified: vendor error per partner-log, <$250 risk" \
    --as-founder
```

The founder can skip `kb precedent` for trivial resolutions that don't warrant a precedent. The founder can also call `kb precedent` alone on an already-resolved task (post-hoc precedent capture).

**Hook point for future automation.** Feishu-driven escalation resolution (blueprint step 10) calls both commands in sequence — same two code paths, no third implementation.

## 5. Agent integration

Two touch points: the `start-task` skill (procedural) and generated `CLAUDE.md` (ambient reminder).

### 5.1 `start-task` skill — new step: Consult KB

Slots in after the existing "Consult memory" step and before "Plan and execute":

> **Consult the knowledge base.**
>
> Run `opc kb list --topic <guess>` or `opc kb search "<terms from brief>"` to see whether durable knowledge relevant to this task already exists. Fetch full entries with `opc kb get <slug>`.
>
> **Consult triggers** — scan the KB whenever your brief touches:
> - regulatory / compliance rules (visa, PCI-DSS, PIPL, PDPO, PDPA);
> - partner APIs, integration quirks, rate limits;
> - payment flows, refund policies;
> - any topic where a past escalation likely set precedent.
>
> If nothing matches, proceed. If something matches, treat it as authoritative unless the brief explicitly contradicts it — in which case escalate rather than silently override.

### 5.2 `start-task` skill — new step: Contribute to KB

Slots in after the existing "Report mid-task learnings (optional)" step and before "Report completion". All existing step numbers after this point shift by one; the plan will handle the renumbering mechanics.

> **Contribute to the KB (optional).**
>
> Before reporting completion, ask yourself: did I discover or confirm durable, cross-agent-relevant knowledge that isn't already in the KB?
>
> **Contribute YES if any are true:**
> - You found a factual rule other agents would need (API rate limit, regulatory deadline, partner contract term).
> - You consulted the KB and an entry was wrong or outdated — update it.
> - You made a non-trivial procedural decision worth preserving as a mini-SOP (not a one-off workaround).
>
> **Contribute NO if:**
> - The info is specific to this task (→ task artifact).
> - It's your own operational preference (→ `opc learning`).
> - It's already in `protocol/` docs.
> - The info has a <12-month useful lifespan.
>
> To contribute: write `/tmp/kb-<slug>.md` with the entry shape, then run `opc kb add --agent <you> --from-file /tmp/kb-<slug>.md` (or `opc kb update <slug> …`). Resolve collision 409s by updating the existing entry instead of forcing a sibling.

### 5.3 Generated `CLAUDE.md` nudge

`context_builder.py` injects, just after the `learnings.md` pointer:

```markdown
## Knowledge Base

The org maintains a shared, agent-contributed knowledge base at `<runtime>/kb/` covering precedents and domain reference. Consult it at task start for anything regulatory, partner-API, payment, or precedent-shaped (see start-task skill Step 2.5). Contribute back when you discover durable cross-agent knowledge (Step 5.5).

Everyone reads everything. Only Engineering Head deletes.
```

### 5.4 Guideline doc — `protocol/06-knowledge-base.md`

New authoritative reference doc, author-maintained. Covers:

- What belongs in the KB (expanded version of §5.2 triggers).
- What does not belong (expanded version of §5.2 exclusions).
- Entry shape and required frontmatter fields.
- Collision semantics (when to `update` vs. `--force-new-sibling`).
- Edit etiquette (stamp `updated_by`, keep prior content unless factually wrong, use `supersedes` to deprecate rather than delete).
- Deletion rules and irreversibility.
- Agents read it on demand; it is not injected into every prompt. `CLAUDE.md` points at it.

Kept in `protocol/` because rules about the KB are org policy, even though KB content is runtime state.

## 6. Error handling

Validation is daemon-side. The CLI is a thin HTTP client.

| Condition | HTTP | Code |
|---|---|---|
| Slug fails `^[a-z0-9][a-z0-9-]{0,63}$` | 400 | `invalid_slug` |
| Slug already exists on `add` | 409 | `slug_exists` |
| Near-duplicate on `add` (no `--force-new-sibling`) | 409 | `near_duplicate` |
| `type` not in `{reference, precedent}` | 400 | `invalid_type` |
| Required frontmatter missing | 400 | `missing_frontmatter` |
| Body > 32 KiB | 413 | `entry_too_large` |
| `supersedes` points to non-existent slug | 400 | `invalid_supersedes` |
| `get` / `update` / `delete` on missing slug | 404 | `not_found` |
| `delete` by non-EH, non-founder | 403 | `delete_forbidden` |
| `delete` without `--confirm` | 400 | `confirm_required` |
| `resolve-escalation` on task not in `ESCALATED` | 409 | `task_not_escalated` |
| `resolve-escalation` missing `--rationale` | 400 | `rationale_required` |
| `kb precedent` with no escalation audit row | 400 | `no_escalation_record` |

### 6.1 Concurrency

`DaemonState.kb_lock` (`asyncio.Lock`), same pattern as `db_lock`. The sequence {slug-exists check → near-duplicate check → body write → frontmatter stamping → `_index.md` regeneration} runs inside the lock.

### 6.2 Atomic writes

File writes use `os.replace` to atomically rename `<slug>.md.tmp` → `<slug>.md`. A failure before rename leaves no partial file.

### 6.3 `_index.md` regeneration failures

Non-fatal. If regeneration throws, the entry file is already on disk — the write itself succeeded. The daemon logs the regen error and returns the write success to the caller. Every subsequent `add` / `update` / `delete` re-attempts regeneration, so the index self-heals on the next successful write. `opc kb reindex` forces an explicit rebuild.

### 6.4 Deletion recovery

Irreversible unless the runtime dir is git-initialized. Guideline says so explicitly. `--confirm` is mandatory to avoid accidental deletion.

## 7. Testing

Matches existing test patterns (unit + route + CLI + skill-text + one integration).

### 7.1 `tests/test_kb_store.py` (new module `src/infrastructure/kb_store.py`)

- Happy-path `write_entry`: frontmatter stamped server-side, file on disk.
- Rejects bad slug regex / missing required fields / oversized body / invalid `type` / dangling `supersedes`.
- Raises `SlugExists` when file present.
- `find_near_duplicates` — title-similarity threshold, tag-overlap threshold, top-N ranking.
- `read_entry` round-trips frontmatter + body.
- `list_entries` filters by `topic` and `type`.
- `search` ranks title hits above body hits.
- `regenerate_index` — groups by topic, deterministic ordering, handles empty folder.
- `delete_entry` removes file + regenerates index.
- Concurrency: parallel `write_entry` calls produce two distinct entries; same-slug race yields one success + one `SlugExists`.
- Atomic write: injected failure between tmp-write and rename leaves no partial file.

### 7.2 `tests/daemon/test_routes_kb.py` (new route module `src/daemon/routes/kb.py`)

Mirror pattern of `test_routes_tasks.py`. One test per status code branch in §6 table. Includes happy-path tests for `list`, `get`, `search`, `add`, `update`, `delete` (as EH), and `kb precedent` (entry written, task status unchanged). A separate happy-path test verifies `kb precedent` succeeds on an already-resolved task (post-hoc capture).

### 7.2b `tests/daemon/test_routes_tasks.py` additions (resolve-escalation)

- Happy path: `resolve-escalation` on `ESCALATED` task transitions to `APPROVED`/`REJECTED`, writes `log_escalation_resolved` audit row, does **not** write to `<runtime>/kb/`.
- 409 `task_not_escalated` when task is in any non-`ESCALATED` status.
- 400 `rationale_required` when `--rationale` missing.

### 7.3 `tests/test_cli.py` additions

- Every `opc kb <subcommand>` present in the parser.
- `--from-file` required on `add` / `update`.
- `--confirm` required on `delete`.
- `--as-founder` recognized on `precedent` / `delete`.

### 7.4 `tests/test_skills.py` additions

- `start-task` body contains: `"Consult the knowledge base"`, `"Contribute to KB"`, `opc kb list`, `opc kb search`, `opc kb add`, `opc kb get`.
- Existing `test_skill_cli_commands_exist` automatically catches any `opc kb *` reference that isn't a real subcommand.

### 7.5 `tests/test_context_builder.py` additions

- Generated `CLAUDE.md` contains the Knowledge Base section (heading + CLI pointers).

### 7.6 `tests/test_protocol_docs.py` (new or extend existing)

- `protocol/06-knowledge-base.md` exists.
- Covers: what belongs, what does not belong, write gates, deletion rules (string-presence assertions).

### 7.7 `tests/integration/test_kb_end_to_end.py`

One integration test: fake-agent workspace calls `opc kb add --from-file <tmp>`, daemon writes file; a second fake agent calls `opc kb search`, finds it; `opc kb get <slug>` returns the body. Mirrors the existing fake-claude-binary integration test shape.

### 7.8 Out of scope for tests (matches out-of-scope code)

- No RAG / embeddings / vector search tests.
- No escalation-resolution automation tests.
- No git-history tests (runtime is not required to be git-initialized).

## 8. File structure

New files:

- `src/infrastructure/kb_store.py` — slug validation, frontmatter parsing, file I/O, near-duplicate detection, index regeneration, atomic writes.
- `src/daemon/routes/kb.py` — FastAPI router for all `opc kb *` endpoints.
- `protocol/06-knowledge-base.md` — authoritative guideline doc.
- Test files listed in §7.

Modified files:

- `src/cli.py` — new `opc kb *` subcommands plus `opc resolve-escalation`.
- `src/client/client.py` — HTTP client methods for the new routes.
- `src/daemon/routes/tasks.py` — add `POST /tasks/{id}/resolve-escalation`.
- `src/daemon/app.py` — register the `kb` router.
- `src/daemon/state.py` — add `kb_lock`.
- `src/infrastructure/audit_logger.py` — add `log_escalation_resolved` helper.
- `src/orchestrator/context_builder.py` — inject Knowledge Base section into generated `CLAUDE.md`.
- `protocol/skills/start-task/SKILL.md` — add Step 2.5 (Consult KB) and Step 5.5 (Contribute to KB), renumber as needed.
- `CLAUDE.md` (project) — note KB under the directory layout; add operational section for founders.
- `README.md` — user-facing: new `opc kb *` commands under the usage section.

## 9. Open questions / deferred

- **Cross-runtime KB sync.** Two machines, two runtimes → diverging KBs. Not a problem for today's single-founder single-host setup. If it becomes one, the answer is "operator `git init`s the runtime dir and syncs via git." No code change needed.
- **Escalation-resolution automation.** Blueprint step 10. When shipped, it calls the same `_kb_write_precedent` helper as the CLI. No second implementation.
- **Stale-content detection.** Possible future addition: warn on entries with `updated_at` older than N months. Out of scope for v1 per the "readers fix what they find wrong" principle.
- **Permissions beyond EH-only delete.** Current design: everyone reads, everyone writes, EH deletes. If operational experience shows we need per-topic write scoping (e.g., only Compliance writes visa entries), that's a targeted follow-up — not baked in now.
