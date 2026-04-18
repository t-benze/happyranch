# Agent Memory — Design

**Date:** 2026-04-18
**Status:** Approved, ready for implementation plan

## Problem

Agents today have fragmented persistence:

- `learnings.md` — agent-authored insights (unbounded, unstructured).
- `recent_tasks.md` — global log written to *every* workspace (misleading; not per-agent memory).
- `scorecard.md` — rolling performance stats.
- `audit_logs` in SQLite — full history of sessions, completion reports, verdicts.

Raw history exists in the database, but nothing surfaces it back into the next task's prompt. An agent cannot recall what it produced in a prior task, and there is no addressable per-agent work unit — multiple agents share a single `task_id` for the whole orchestration, so the dev_agent cannot point at "the specific thing I did yesterday."

Concrete failure case: the founder asks the Engineering Head to review project status and deliver a report. Next day, they ask the EH "what actions should I take to follow up?" Today the EH has no mechanism to retrieve yesterday's report.

## Goals

1. Every agent-level unit of work is addressable by its own `task_id`.
2. The orchestrator persists the brief *and* the output of every task.
3. Each agent keeps a lightweight per-agent history of the tasks *it* performed.
4. Agents can retrieve the full details of any task on demand via a CLI.
5. The new task flow nudges the agent to consult its history when the brief references prior work.

## Non-goals

- Semantic / vector-based retrieval (defer until enough history accumulates to need it).
- Automatic prompt injection of unrelated past tasks (keeps prompts clean).
- Cross-agent shared memory (each agent sees only its own history; cross-agent access goes through `opc recall`).
- Rewriting the learnings / scorecard mechanisms — they stay as-is.

## Design

### 1. Artifact storage

Agents producing standalone content (reports, plans, analyses) write files under:

```
<agent_workspace>/artifacts/<task_id>/
```

This directory sits at the top of the workspace. It is **not** inside `repos/` and is not affected by `make-worktree` (worktrees live under `repos/<name>/.claude/worktrees/`).

`CompletionReport` gains one optional field:

```python
artifact_dir: str | None = None  # relative to agent workspace, e.g. "artifacts/TASK-042"
```

Routine tasks (edited 3 files, nothing standalone to archive) leave it `None`.

**Schema change — `task_results`:** add `artifact_dir TEXT NULL`.

### 2. Sub-tasks with parent links

Today, one `task_id` flows through the whole orchestration. When EH delegates to dev_agent, both run under the same id. This conflates the founder's request with agent-level work units.

**New model:** delegation spawns a child task.

- Root task `TASK-042`: `assigned_agent = engineering_head`, `parent_task_id = None`, `brief` = the founder's brief.
- EH's decision loop produces a `delegate` step → orchestrator calls `create_task` with `parent_task_id = TASK-042`, `assigned_agent = dev_agent`, `brief` = EH's prompt to dev_agent. Result: `TASK-043`.
- Dev_agent runs its session under `TASK-043` and writes `artifacts/TASK-043/` if applicable.
- Recursion is natural: if dev_agent's orchestration itself spawns a sub-delegate, that's `TASK-044` with `parent_task_id = TASK-043`.

**Schema change — `tasks`:** add `parent_task_id TEXT NULL`, indexed for parent→children lookup.

**Orchestration state:** the EH decision loop still needs to see sub-task results on its next prompt. `prior_steps` keeps functioning, but each `StepRecord` now references a real persisted sub-task by id; the orchestrator loads the sub-task's `output_summary` and `artifact_dir` from the DB when building the next EH prompt.

**Review verdicts / performance scoring:** unchanged in spirit. When a sub-task completes, the EH's next decision effectively judges it, and `_log_review_verdicts` fires with `reviewed_agent` = the sub-task's `assigned_agent`. The verdict now references the sub-task's id.

### 3. Per-agent task history

Replace the global `recent_tasks.md` with a per-agent `task_history.md`.

- Written only to the workspace of the task's `assigned_agent`.
- Newest entries at the top.
- Capped at ~50 entries; older entries roll off. Nothing is lost — DB keeps everything.

**Entry format:**

```markdown
- **TASK-042** (2026-04-17, approved) — Review Q1 project status and deliver report
  - Outcome: Analyzed 4 team dashboards; delivered Q1 report with 3 risks and 5 actions.
  - Artifact: `artifacts/TASK-042/`
```

Fields:
- **Header line:** `task_id`, completion date, final status, brief (one line, truncated to ~120 chars).
- **Outcome:** one line, truncated to ~160 chars. Source depends on status:
  - `approved` — `CompletionReport.output_summary` (see "Storing EH output" below for the root-task caveat).
  - `escalated` — the `reason` from the escalation audit log entry.
  - `rejected` — the session's failure message if available, else `"Agent session failed"`.
- **Artifact:** present only if `artifact_dir` is set.

**Storing EH output.** The Engineering Head's completion reports carry a JSON payload (`{"action": "done" | "delegate" | "escalate", "summary": "...", ...}`), parsed by `_parse_next_step`. When the root task finishes because EH returned `done`, the orchestrator stores the **parsed `summary` field** as the task's `output_summary` (not the raw JSON), so `task_history.md` and `opc recall` return readable text. Sub-tasks (run by worker agents) write plain summaries already, so no parsing is needed for them.

CLAUDE.md already points at `task_history.md` under "Persistent Files" (rename from `recent_tasks.md`).

### 4. Recall CLI

**Daemon route:** `GET /tasks/{task_id}/recall`

Response shape:

```json
{
  "task_id": "TASK-042",
  "parent_task_id": null,
  "assigned_agent": "engineering_head",
  "brief": "...",
  "status": "approved",
  "created_at": "...",
  "completed_at": "...",
  "output_summary": "...",
  "artifact_dir": "artifacts/TASK-042",
  "children": ["TASK-043"]
}
```

Query parameters:
- `include_artifact=true` — daemon resolves `artifact_dir` against the task's assigned-agent workspace and returns `[{ path, content }]` for each file. Total payload capped at ~200 KB; if exceeded, returns file listing only with a truncation flag.
- `tree=true` — recurse into `children` and return the same structure for each. Combinable with `include_artifact`.

**CLI commands:**

| Command | Behavior |
|---|---|
| `opc recall <task_id>` | One task: brief, outcome, artifact path. |
| `opc recall <task_id> --fetch-artifact` | Inlines artifact file contents. |
| `opc recall <task_id> --tree` | Root + descendants. |
| `opc recall <task_id> --tree --fetch-artifact` | Everything. |

**Permissions:** already covered by the existing `Bash(opc:*)` allow rule (both workspace `settings.json` and `--allowedTools` flag). No changes.

### 5. Start-task skill update

Insert a new step between "Parse parameters" and "Plan and execute":

> **Step 1.5 — Consult memory.**
> 1. Read `task_history.md` in your workspace root. It lists your recent tasks with briefs, outcomes, and artifact paths.
> 2. If the current brief references prior work — phrases like "follow up on", "continue", "the report from last week", a specific date, or an explicit `TASK-xxx` — identify the matching entry and fetch details:
>    ```bash
>    opc recall <task_id> --fetch-artifact
>    ```
> 3. If the brief does not reference prior work, skip this step. Do not pull history speculatively.

Amend "Plan and execute" with the artifact convention:

> If the task produces a standalone document (report, plan, analysis), write its files under `artifacts/<task_id>/` in your workspace root (not in any repo or worktree). Include the relative path in your completion payload as `artifact_dir`.

Update the completion-payload example to show the optional `artifact_dir` field.

## Affected components

| Area | Change |
|---|---|
| `src/models.py` | `CompletionReport.artifact_dir: str \| None`. `TaskRecord.parent_task_id: str \| None`. |
| `src/infrastructure/database.py` | Migrations: `tasks.parent_task_id`, `task_results.artifact_dir`. New queries: `get_children`, `get_recall_payload`. |
| `src/infrastructure/audit_logger.py` | Persist `artifact_dir` in `log_completion_report`. |
| `src/orchestrator/orchestrator.py` | Spawn sub-tasks on delegate. Rewrite `_update_recent_tasks` → per-agent `task_history.md`, called per-task (not globally). Rename file to `task_history.md`. |
| `src/orchestrator/context_builder.py` | Rename persistent file `recent_tasks.md` → `task_history.md`; update CLAUDE.md template. |
| `src/daemon/routes/tasks.py` | Add `GET /tasks/{id}/recall` with `include_artifact` and `tree` params. |
| `src/cli.py` | Add `opc recall <task_id>` with flags. |
| `src/client/client.py` | Add corresponding HTTP method. |
| `protocol/skills/start-task/SKILL.md` | Add Step 1.5; document artifact convention; update payload example. |
| `tests/` | New tests: sub-task creation, recall endpoint, artifact read with cap, per-agent history write, renamed file. |

## Migration notes

- Existing workspaces have `recent_tasks.md`; on next `opc init-agent` (or implicit regeneration in `ensure_workspace_ready`), rename to `task_history.md` if the old file exists and the new file does not. Otherwise leave in place.
- `artifact_dir` defaults to `NULL` in the DB, so existing rows are unaffected.
- `parent_task_id` is `NULL` for all existing tasks — they are treated as root tasks.

## Open questions / deferred

- **Retention:** `task_history.md` rolls off at 50 entries; the DB grows without bound. Acceptable for now; revisit once DB size becomes noticeable.
- **Semantic retrieval:** deferred. When history is large enough that linear scan of `task_history.md` misses relevant tasks, add a vector index keyed by brief+outcome.
- **Cross-agent recall authorization:** `opc recall` today returns any task to any caller. The orchestrator CLI is currently trusted inside the workspace; when tighter isolation is required, add an `--agent` argument and check the caller.
