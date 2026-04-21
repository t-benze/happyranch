# Talk Flow — Design Spec

**Date:** 2026-04-21
**Status:** Draft, pending implementation plan.
**Relates to:** `protocol/05d-feishu.md` (future founder↔agent channel), `protocol/06-knowledge-base.md` (reused at talk end), agent memory design `docs/superpowers/specs/2026-04-18-agent-memory-design.md` (reads `learnings.md`, `task_history.md`, `scorecard.md`).

## 1. Goal

Give the founder a first-class way to have a conversation with any individual agent — to review recent work, surface friction, and capture what came out of the discussion. The conversation itself happens inside the agent's existing workspace via the `claude` / `codex` interactive CLI. The Talk Flow is the structured wrapper around that conversation: a briefing report at the start, a summary + learnings capture at the end, and a persistent record the dashboard can later surface.

Specifically:

- The founder runs `claude` (or `codex`) inside an agent's workspace directory and activates the flow with `/talk start`.
- The agent compiles a structured report covering tasks, learnings, and self-reflections since the last closed talk.
- Conversation proceeds naturally; the founder picks topics from the report or goes off-script.
- The founder ends the flow with `/talk end`; the agent summarizes, extracts learnings (and optional KB entries), and persists everything through the orchestrator daemon.

## 2. Non-goals

The following are explicitly out of scope and must not creep in during implementation:

- **Multi-agent panel talks.** One founder, one agent, per talk.
- **Non-founder initiation.** Managers do not use this flow to "talk" to their workers. Existing task/escalation channels cover that.
- **Web or Feishu UI.** Transport is CLI-only. The future dashboard consumes `GET /talks`; Feishu integration is a later phase.
- **Real-time streaming via SSE.** Talks are synchronous terminal sessions. No event bus for talk events.
- **Auto-abandon timers / background reapers.** Orphaned talks are resolved interactively at the next `/talk start`.
- **Standing `reflections.md` artifact.** Reflections are generated just-in-time from existing artifacts (`task_history.md`, `learnings.md`, audit log, scorecard). This is a reversible choice — a standing log can be added later if the just-in-time version feels thin.
- **Action-item pipeline.** Talk end produces learnings (per-agent) and optional KB entries (shared). It does NOT spawn tasks. If a talk surfaces work that needs doing, the founder submits `opc run ...` separately.
- **Talk deletion.** Talks are append-only. If content must be removed, do it at the file level; no `opc talk delete` is provided.

## 3. Data model

### 3.1 New SQLite table

```sql
CREATE TABLE talks (
    id TEXT PRIMARY KEY,                  -- TALK-NNN, monotonic sequence like TASK-
    agent_name TEXT NOT NULL,
    started_at TEXT NOT NULL,             -- ISO-8601 UTC
    ended_at TEXT,                        -- ISO-8601 UTC, NULL while open
    status TEXT NOT NULL DEFAULT 'open',  -- open | closed | abandoned
    summary TEXT,                         -- agent's end-of-talk writeup, inline (≤16 KiB recommended)
    topic_list_json TEXT,                 -- JSON array of topic strings from the summary payload
    new_learnings_count INTEGER DEFAULT 0,
    new_kb_slugs_json TEXT,               -- JSON array of KB slugs created during this talk
    transcript_path TEXT                  -- <runtime>/talks/<id>.md, NULL until closed
);
CREATE INDEX idx_talks_agent_status ON talks(agent_name, status);
CREATE INDEX idx_talks_started ON talks(started_at);
```

`summary` is inlined (not file-only) so the future dashboard can render lists without opening every transcript file. `new_learnings_count` and `new_kb_slugs_json` are snapshots of what the talk contributed, useful for "recent productive talks" queries.

### 3.2 ID format

`TALK-NNN`, monotonic, matches `TASK-NNN` convention. Sequence maintained in a new `talk_sequence` row or equivalent (details left to implementation plan; reuse whatever pattern tasks use).

### 3.3 Status transitions

```
(nothing) --start--> open --end-------> closed     (normal)
                     open --abandon---> abandoned  (orphan resolution)
                     open --resume----> open       (no-op status-wise; logs a talk_resumed audit event)
```

`closed` and `abandoned` are terminal. A new talk is always a new `id`; resuming does NOT create a new row.

### 3.4 Audit-log actions (additions)

Added to the existing `audit_log` action vocabulary:

| Action | Payload (JSON fields) |
|---|---|
| `talk_started` | `talk_id`, `agent_name`, `resumed_from` (nullable) |
| `talk_resumed` | `talk_id`, `agent_name` |
| `talk_abandoned` | `talk_id`, `agent_name`, `reason` (`"orphan_at_new_start"` today; extensible) |
| `talk_ended` | `talk_id`, `agent_name`, `new_learnings_count`, `new_kb_slugs` |

### 3.5 Filesystem

```
<runtime>/
├── opc.yaml
├── opc.db
├── workspaces/
├── kb/
└── talks/
    ├── TALK-001.md
    ├── TALK-002.md
    └── ...
```

Flat folder. One file per closed talk. Abandoned talks do NOT write a transcript file (they produced no summary, so there's nothing to persist beyond the row). The transcript file is written atomically during the `/talks/{id}/end` transaction — same pattern as `kb_store.write_entry`.

### 3.6 Transcript file shape

```markdown
---
talk_id: TALK-001
agent_name: dev_agent
started_at: 2026-04-21T09:00:00Z
ended_at: 2026-04-21T09:42:00Z
topic_list: [payment refund edge case, QA gate flakiness]
new_learnings_count: 2
new_kb_slugs: [alipay-refund-timeout-retry]
---

# Summary

<agent's summary from the end-of-talk payload, verbatim>

# Transcript (agent's perspective)

<transcript_markdown from the end-of-talk payload, verbatim>
```

The transcript is what the agent chose to record — the CLI's own session log (`~/.claude/projects/...`) remains authoritative for full fidelity, but OPC's copy is self-contained and greppable without reaching into CLI internals.

## 4. Daemon routes — `src/daemon/routes/talks.py`

All routes use the existing bearer-token dependency. No new auth model.

| Method | Path | Purpose | Request body | Response |
|---|---|---|---|---|
| POST | `/talks` | Start a new talk | `{agent_name}` | Happy path: `{talk_id, started_at}`. On conflict: 409 with `{prior_open_talk_id, prior_started_at}` — no new row created. |
| POST | `/talks/{id}/resume` | Resume an open talk | `{}` | `{talk_id, started_at}` |
| POST | `/talks/{id}/end` | End + persist | full end payload (§5.2) | `{talk_id, status: "closed", transcript_path, new_learnings_count}` |
| POST | `/talks/{id}/abandon` | Mark abandoned | `{reason}` | `{talk_id, status: "abandoned"}` |
| GET | `/talks` | List | query: `agent`, `status`, `limit` (default 50, max 500) | `{talks: [...row_summary]}` |
| GET | `/talks/{id}` | Detail | — | row + inlined transcript content |

Collision rule: `POST /talks` with an already-open talk for the same agent returns HTTP 409 with `{prior_open_talk_id}`, and the skill handles the interactive choice (resume vs abandon).

Idempotency / error rules:

- `POST /talks/{id}/end` on a non-`open` talk → 400.
- `POST /talks/{id}/resume` on a non-`open` talk → 400.
- Transcript-file write failure inside `/end` → transaction rolls back; row stays `open`; returns 500. Caller can retry.

No SSE endpoints. Talks are point-in-time; no event stream to subscribe to.

## 5. CLI surface — `opc talk ...`

### 5.1 Human-facing commands

```
opc talk start  [--agent <name>]                 # --agent optional if $PWD is inside a workspace
opc talk resume --talk-id <id>
opc talk status [--agent <name>]                 # list OPEN talks
opc talk list   [--agent <name>] [--limit N]     # history (closed + abandoned + open)
opc talk show <talk-id>                          # metadata + transcript
opc talk abandon --talk-id <id>                  # explicit cleanup; rare
```

`opc talk start` without `--agent` infers the agent from `$PWD` if it is `<runtime>/workspaces/<name>/...`. Outside a workspace, `--agent` is required. This mirrors how other workspace-local callbacks accept their agent context.

### 5.2 Skill-facing (callback) command

```
opc talk end --talk-id <id> --from-file <path>
```

`--from-file` payload (JSON):

```json
{
  "summary": "≤16 KiB markdown; what we talked about, decisions, open threads",
  "topic_list": ["payment refund edge case", "QA gate flakiness"],
  "transcript_markdown": "full transcript as the agent recorded it, markdown",
  "learnings": [
    {"text": "Alipay refund endpoint returns 504 under load; retry twice with 2s backoff."},
    {"text": "Founder wants Codex-backed workers on infra-heavy tasks."}
  ],
  "kb_slugs": ["alipay-refund-timeout-retry"]
}
```

Rationale for `--from-file` + single-line invocation: matches `opc report-completion` / `opc manage-agent` / `opc kb add`. Claude's headless-mode permission matcher treats newlines and shell separators as command boundaries, so any callback with multi-line content must stage to a temp file and invoke the CLI once. Codex has no equivalent constraint, but using the same pattern across executors keeps the skill portable.

`learnings[].text` is the full learning text; the daemon appends each entry to the agent's `learnings.md`. Talk-originated learnings have no `task_id` — the daemon stamps them with `source_talk: <talk_id>` provenance instead (existing learnings carry `source_task: <task_id>`). The `opc learning` path is reused for the append + dedup logic; the daemon's internal write helper accepts either `task_id` or `talk_id`, exactly one. `kb_slugs` is a list of slugs that the skill ALREADY wrote via `opc kb add` earlier in its body — `/talk end` only records which ones came from this talk, it does NOT re-write them.

## 6. Skill: `protocol/skills/talk/`

New skill, copied into every workspace by the context builder (same mechanism as `start-task`, `make-worktree`, `manage-repo`, `manage-agent`). Instruction-first, not code-first — the agent reasons from the skill body each time.

### 6.1 `/talk start` procedure

1. Identify self: read agent name from `agent.yaml` in the workspace root.
2. Check for open talk: `opc talk status --agent <self>`. If one exists, prompt the founder in-session:
   > "An open talk exists: TALK-042 started 2026-04-20T15:10:00Z. Resume, or abandon and start fresh?"
   - **Resume** → `opc talk resume --talk-id TALK-042`; skip to step 7 (skip the report; prior context is already in founder's head).
   - **Abandon** → `opc talk abandon --talk-id TALK-042 --reason orphan_at_new_start`, then continue to step 3.
3. Start: `opc talk start --agent <self>` → receive `talk_id`.
4. Query the last-closed talk's `ended_at` (from `opc talk list --agent <self> --limit 1 --status closed`) to get the report window. If none exists, window is "all-time" capped at 30 days.
5. Gather inputs:
   - `task_history.md` in the workspace.
   - `learnings.md` in the workspace (delta since window start).
   - `scorecard.md` in the workspace.
   - `opc audit --agent <self> --since <window_start>`.
6. Emit report. Exact sections, in order:
   1. **Since last talk** — window dates, task counts by terminal status.
   2. **Notable tasks** — 3–5 items, agent-selected for significance, one-line takeaway each.
   3. **New learnings** — learnings added in the window.
   4. **Open questions / frictions** — agent's just-in-time reflection. Can be empty.
   5. **Scorecard delta** — tier change if any; acceptance-rate shift.
   6. **Suggested topics** — 2–3 the agent wants to discuss.
7. Await founder's next message. Normal conversation proceeds.

### 6.2 `/talk end` procedure

1. Summarize the conversation. Structured: what was discussed, what decisions were reached, what remains unresolved.
2. Extract candidate learnings. Rule: a learning is a durable, non-obvious operational fact the agent will want to recall on future tasks. Rely on the daemon's existing `opc learning` dedup for correctness; the skill may pre-filter obvious restatements of entries already in `learnings.md` to keep the payload small.
3. Identify KB-worthy material. Apply rules from `protocol/06-knowledge-base.md` §2 (12-month expected lifespan; not per-agent private). For each, call `opc kb add --agent <self> --from-file /tmp/kb-<slug>.md`. Collect the slugs.
4. Assemble the end payload at `/tmp/talk-end-<talk_id>.json` (schema in §5.2).
5. Single-line call: `opc talk end --talk-id <id> --from-file /tmp/talk-end-<talk_id>.json`.
6. Confirm to the founder: transcript path, count of new learnings, count of new KB entries.

### 6.3 Skill file layout

```
protocol/skills/talk/
├── SKILL.md             # skill body + frontmatter (description triggers on /talk start, /talk end)
└── examples/
    ├── opening-report.md
    └── end-payload.json
```

Copied verbatim into each workspace under `.claude/skills/talk/` during `init-agent` (Claude workspaces) or the Codex equivalent path — whichever the `workspace_adapters.py` already targets.

## 7. Permissions

No allowlist changes required. Both `.claude/settings.json` and `ClaudeExecutor.run --allowedTools` already declare `Bash(opc *)`, which transparently covers all `opc talk ...` subcommands. Codex workspaces have no per-command allowlist. Explicit statement here only so implementers don't accidentally re-add a narrower rule.

## 8. "Since last talk" semantics

```sql
SELECT ended_at FROM talks
WHERE agent_name = ? AND status = 'closed'
ORDER BY ended_at DESC LIMIT 1;
```

Abandoned talks do NOT count as a "last talk" — they produced no summary, so there is nothing for the founder to have caught up on. Window start = that `ended_at`, or "all-time capped at 30 days" if no prior closed talk exists.

## 9. Error handling

| Condition | Behavior |
|---|---|
| `POST /talks` while another is open | 409 with `prior_open_talk_id`; skill prompts resume/abandon. |
| `POST /talks/{id}/end` on `closed` or `abandoned` | 400. |
| Transcript file write fails during `/end` | Row stays `open`; 500; caller retries. |
| `learnings` list contains a duplicate of existing text | Daemon skips it silently (idempotent, matches `opc learning`). |
| `kb_slugs` references a slug that does not exist | Daemon returns 400 before persisting — protects against typos recording phantom KB contributions. |
| Founder closes terminal mid-talk | No end callback fires. Row stays `open`. Resolved at next `/talk start` interactively. |
| `opc talk show` on non-existent `talk_id` | 404. |

## 10. Testing

### 10.1 Unit tests

- DB CRUD: start → open; end → closed + ended_at set; abandon → abandoned + ended_at set; resume is a no-op on the row.
- `new_learnings_count` equals `len(learnings)` after dedup.
- `topic_list_json` and `new_kb_slugs_json` round-trip intact.
- Transcript file write is atomic (rename pattern).
- Audit log entries match table 3.4 for every transition.
- `GET /talks` filters: agent, status, limit cap at 500.
- `from-file` payload: malformed JSON → 400; unknown `kb_slugs` → 400.

### 10.2 Integration tests (`tests/integration/`)

End-to-end: spawn the real daemon against a tmp runtime, run a fake-Claude binary that:
1. Calls `opc talk start --agent dev_agent`.
2. Prints a canned opening report.
3. Prints a canned conversation.
4. Writes a canned end-payload file and calls `opc talk end --from-file ...`.
5. Assert: row is `closed`, transcript file present on disk, `learnings.md` appended, audit log contains `talk_started` + `talk_ended`, `opc talk show` returns the correct metadata + transcript.

Second scenario: orphan resolution — start a talk, kill the fake-Claude, start another talk for the same agent, confirm the skill's interactive-choice path by asserting `opc talk abandon` was called and a new `talk_id` was issued.

## 11. Open questions for the implementation plan

These are design-visible but implementation-specific; the plan should settle them explicitly, not re-litigate the design:

- Exact sequence-generation approach for `TALK-NNN` — probably the same helper as `TASK-NNN`.
- Whether `/talks/{id}` inlines transcript content or returns a path pointer for large transcripts. Default: inline unless >256 KiB.
- Whether the skill prints the report to stdout or writes it to a file the CLI then cats. Default: stdout; the interactive CLI already captures it in scrollback.
- Whether `opc talk show` defaults to human-formatted or JSON output. Default: human, with `--json` flag matching other `opc` commands.

## 12. Explicitly deferred

Captured here so they are remembered but kept out of v1:

- **Standing `reflections.md` log** — upgrade path if just-in-time reflection proves thin.
- **Cross-agent cross-references** — e.g., "dev_agent mentioned qa_engineer's flakiness" becoming a surface on qa_engineer's next report.
- **Founder-initiated talks via Feishu** — reuses the same daemon routes; skill layer needs a Feishu-side sibling.
- **Rolling summaries** — multi-talk retrospectives (e.g., "summarize my last five talks with dev_agent").
- **Dashboard widget** — consume `GET /talks` after the dashboard phase lands.
