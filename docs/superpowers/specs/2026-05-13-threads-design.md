# Threads — Design Spec

> **Addressing model superseded** by `2026-05-30-thread-broadcast-only-design.md`.
> The `addressed_to` field, the `@all` / `@founder` tokens, the `_verify_addressed`
> reply-eligibility check, and the in-thread Feishu founder push are removed.
> Threads now broadcast every message to all participants; declines are silent.
> See the new spec for the current routing model.

> **Superseded in part by `2026-06-01-thread-close-out-removal-and-resume-design.md`.**
> §5.10 close-out (Phase B), §5.10.1 close-out callback, §5.12 abandon,
> and the `archiving` state are no longer accurate. Archive is now
> synchronous; abandoned + archiving states are collapsed into `archived`;
> close-out invocations are removed; founder can resume an archived thread.

**Date:** 2026-05-13
**Status:** Draft, pending implementation plan.
**Relates to:** `docs/superpowers/specs/2026-04-21-talk-flow-design.md` (sibling primitive — 1:1 reflective ritual), `docs/superpowers/specs/2026-04-26-talk-dispatch-design.md` (agent task-dispatch precedent; thread dispatch mirrors this), `docs/superpowers/specs/2026-05-08-feishu-notification-design.md` (no Feishu surface for threads yet), `protocol/06-knowledge-base.md` (reused at archive).

## 1. Goal

Give the founder an email-style written workchannel for multi-agent collaboration. The founder composes a message to one or more agents, addressed agents process it headlessly and reply (or decline), the thread grows, and the founder can loop more agents in along the way. Every current participant sees the full prior history. The primary surface is a Textual TUI (`opc threads`); CLI subcommands underneath cover scripting and the agent callback.

Threads replace two previously-imagined features by collapsing them into one primitive:

- **Forwarding** = compose a new thread seeded with a quoted excerpt from another thread (or talk).
- **Meetings** = a thread with 3+ participants.

Threads coexist with talks. Talks remain the 1:1 reflective ritual (scorecard report, learnings extraction over a time window). Threads are utilitarian written exchange — closer to email than to a structured 1:1.

## 2. Non-goals

The following are explicitly out of scope and must not creep in during implementation:

- **Agent-initiated threads.** Only the founder composes a new thread or invites participants. An agent in a thread may reply, decline, or dispatch a task — they cannot start a new thread or invite a new participant.
- **Agent-to-agent reply chains without founder involvement.** Every message is either composed by the founder or is a reply to a founder-driven turn. Agents cannot send messages to each other unprompted.
- **Inline body `@mention` routing.** The To: field is the source of truth for addressing. `@text` in the body is visual only; the daemon does not parse it.
- **Quoting specific prior messages.** Replies are flat. The TUI may show ordering and speaker, but there is no tree structure.
- **Thread full-text search.** History is browsable but not indexed for query. The founder can `grep` archived transcript files.
- **Per-agent thread mute / snooze.** All current participants stay subscribed for as long as they're listed.
- **Real-time token streaming of agent replies.** Replies arrive whole, like the existing executor model.
- **`opc manage-agent` extended to threads.** Enrollment / update / termination via talks only (talk is a focused 1:1 ritual; thread participants change too dynamically to be safe authority bearers for this).
- **Mid-thread `opc learning` callbacks.** Learnings are collected at archive close-out, not interleaved with the conversation.
- **Founder participating from inside an agent's workspace CLI.** Founder drives threads from their own terminal via `opc threads`. Founder is never a Claude/Codex subprocess.
- **Feishu surface for threads.** Feishu integration today is escalation-only; deferred.

## 3. Data model

### 3.1 New SQLite tables

```sql
CREATE TABLE threads (
    id TEXT PRIMARY KEY,                       -- THR-NNN, monotonic
    subject TEXT NOT NULL,
    started_at TEXT NOT NULL,                  -- ISO-8601 UTC
    archived_at TEXT,
    status TEXT NOT NULL DEFAULT 'open',       -- open | archived | abandoned
    forwarded_from_id TEXT,                    -- nullable; "THR-NNN" or "TALK-NNN"
    forwarded_from_kind TEXT,                  -- 'thread' | 'talk'; null iff forwarded_from_id is null
    turn_cap INTEGER NOT NULL DEFAULT 500,     -- max total agent invocations across the thread
    turns_used INTEGER NOT NULL DEFAULT 0,
    summary TEXT,                              -- founder-written archive summary (≤16 KiB)
    new_kb_slugs_json TEXT,                    -- rollup of KB slugs created during this thread
    transcript_path TEXT                       -- <runtime>/orgs/<slug>/threads/<id>.md, populated at archive
);
CREATE INDEX idx_threads_status ON threads(status);
CREATE INDEX idx_threads_started ON threads(started_at);

CREATE TABLE thread_participants (
    thread_id TEXT NOT NULL,
    agent_name TEXT NOT NULL,                  -- agent_name; "founder" is implicit and not stored here
    added_at TEXT NOT NULL,
    added_by TEXT NOT NULL,                    -- "founder" (in v1 always)
    PRIMARY KEY (thread_id, agent_name),
    FOREIGN KEY (thread_id) REFERENCES threads(id)
);
CREATE INDEX idx_thread_participants_agent ON thread_participants(agent_name);

CREATE TABLE thread_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    thread_id TEXT NOT NULL,
    seq INTEGER NOT NULL,                      -- monotonic per-thread ordering
    speaker TEXT NOT NULL,                     -- "founder" | <agent_name>
    kind TEXT NOT NULL,                        -- 'message' | 'decline' | 'system'
    body_markdown TEXT,                        -- null for kind='decline' (use decline_reason)
    addressed_to_json TEXT,                    -- ["@all"] | ["agent_a","agent_b"]; null for replies, declines, system
    decline_reason TEXT,                       -- non-null iff kind='decline'
    system_payload_json TEXT,                  -- non-null iff kind='system'; structured details
    created_at TEXT NOT NULL,
    FOREIGN KEY (thread_id) REFERENCES threads(id)
);
CREATE UNIQUE INDEX idx_thread_messages_thread_seq ON thread_messages(thread_id, seq);

CREATE TABLE thread_invocations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    thread_id TEXT NOT NULL,
    agent_name TEXT NOT NULL,
    invocation_token TEXT NOT NULL UNIQUE,     -- opaque, daemon-minted at enqueue time
    triggering_seq INTEGER NOT NULL,           -- the message that caused this invocation
    purpose TEXT NOT NULL,                     -- 'reply' | 'bootstrap' | 'close_out'
    status TEXT NOT NULL DEFAULT 'pending',    -- pending | consumed | timeout | failed
    enqueued_at TEXT NOT NULL,
    started_at TEXT,                           -- set when runner launches subprocess
    consumed_at TEXT,                          -- set on terminal status transition
    session_id TEXT,                           -- executor's session_id, recorded for audit
    dispatched_task_id TEXT,                   -- non-null iff a dispatch was issued on this token
    decline_reason TEXT,                       -- runner-recorded reason on timeout/failure
    FOREIGN KEY (thread_id) REFERENCES threads(id)
);
CREATE INDEX idx_thread_invocations_token ON thread_invocations(invocation_token);
CREATE INDEX idx_thread_invocations_thread ON thread_invocations(thread_id);
CREATE INDEX idx_thread_invocations_pending ON thread_invocations(status) WHERE status = 'pending';
```

### 3.2 ID format and sequencing

`THR-NNN` — three-char prefix matching `TASK-`, `TALK-`. Sequence allocated via `MAX(suffix)` query against `threads.id` (same pattern as `next_talk_id` per the recent fix in commit 86499ff).

### 3.3 Status transitions

```
(nothing) --compose--> open --archive-request--> archiving --close-outs-done--> archived
                       open --abandon---------------------------------------> abandoned
```

Four states: `open`, `archiving`, `archived`, `abandoned`. `archived` and `abandoned` are terminal. `archiving` is a transitional state entered when the founder hits `/archive`; close-out callbacks (§5.12) accept this state; conversational callbacks (reply/decline/dispatch) do not. The daemon transitions `archiving → archived` once close-outs complete (or the wait times out) and the transcript file is written. A thread may stay `open` indefinitely; there is no auto-archive timer.

### 3.4 System message kinds

`thread_messages.kind='system'` rows carry structured payloads (`system_payload_json`) describing in-thread events that aren't conversational turns. Each kind has a fixed renderer in the TUI and transcript:

| `kind_tag` (inside `system_payload_json`) | Trigger | Payload fields |
|---|---|---|
| `participant_added` | Founder invites an agent | `{kind_tag, agent_name, added_by, prior_history_visible: true}` |
| `task_dispatched` | Agent dispatches a task from the thread (§7) | `{kind_tag, task_id, dispatcher, target_agent, team, brief_preview}` |
| `turn_cap_extended` | Founder bumps `turn_cap` | `{kind_tag, new_cap, prior_cap}` |
| `archived` | Founder archives the thread | `{kind_tag, new_kb_slugs, new_learnings_total}` |

System messages always have `speaker='founder'` for `participant_added`, `turn_cap_extended`, `archived`; `speaker=<dispatcher>` for `task_dispatched`. They occupy normal `seq` slots in the message stream so the TUI and transcript render them inline chronologically.

### 3.5 Tasks-table extension

```sql
ALTER TABLE tasks ADD COLUMN dispatched_from_thread_id TEXT;
CREATE INDEX IF NOT EXISTS idx_tasks_dispatched_from_thread_id
    ON tasks(dispatched_from_thread_id)
    WHERE dispatched_from_thread_id IS NOT NULL;
```

Idempotent ALTER on startup (mirrors `dispatched_from_talk_id`). Sibling to the existing column — at most one of `dispatched_from_talk_id` / `dispatched_from_thread_id` is non-NULL per task; the daemon enforces this at insert time. Like the talk column, it is a sideways ref — `walk_ancestors` MUST NOT follow it.

### 3.6 TaskRecord change

Add `dispatched_from_thread_id: str | None = None` to `src/models.py:TaskRecord`. Both `dispatched_from_*` fields surface in `GET /tasks/{id}` and in `opc details`.

### 3.6.1 Invocation tokens

Every agent-side callback (`reply`, `decline`, `dispatch`, `close-out`) MUST present a valid `invocation_token` minted by the daemon when the corresponding invocation was enqueued. This is the second authentication layer on top of the bearer token: the bearer proves the caller can talk to the daemon at all; the invocation token proves the call is part of a live, daemon-triggered turn — closing the loophole where an agent could call `opc threads reply` out-of-band from their workspace at any time.

Token lifecycle:

1. Daemon enqueues a `ThreadInvocation` → inserts a `thread_invocations` row with a fresh random token (UUID4) and `status='pending'`.
2. Invocation runner injects the token into the agent's prompt (see §6.2).
3. Agent reads the token from the prompt and includes it in every callback payload.
4. Each callback validates: `invocation_token` exists, `status='pending'`, `(thread_id, agent_name)` match the row, thread is in a permitted state.
5. **Reply / decline / close-out** transition the row to `status='consumed'` (terminal — token cannot be reused).
6. **Dispatch** records `dispatched_task_id` but does NOT consume the token — the agent must still issue a reply or decline to terminate the turn cleanly.
7. On subprocess exit without a terminal callback: runner marks `status='failed'` (or `'timeout'`) and inserts an auto-decline (§6.3).

A pending row is invalidated by one of:
- Callback consumption (reply/decline/close-out) → `status='consumed'`.
- Runner timeout (`session_timeout_seconds` elapsed) → `status='timeout'`.
- Runner failure (subprocess exit without terminal callback) → `status='failed'`.
- The thread transitioning to `archiving` (Phase A of archive) → conversational invocations reaped to `status='failed'` with `decline_reason='archive_started'`. Close-out invocations are minted AFTER this reap, so they're unaffected.
- The thread transitioning to `abandoned` → ALL still-pending invocations reaped to `status='failed'` with `decline_reason='thread_abandoned'`.

### 3.7 Audit-log actions (additions)

| Action | Scope | Payload (JSON fields) |
|---|---|---|
| `thread_started` | thread_id | `subject`, `initial_recipients`, `forwarded_from_id` (nullable) |
| `thread_message_sent` | thread_id | `seq`, `speaker`, `addressed_to`, `kind` (`message` or `decline`) |
| `thread_participant_added` | thread_id | `agent_name`, `added_by` |
| `thread_dispatch` | new task_id | `thread_id`, `dispatcher`, `target_agent`, `team` |
| `thread_archived` | thread_id | `new_learnings_total`, `new_kb_slugs`, `turns_used` |
| `thread_abandoned` | thread_id | `reason` |

Existing `task_dispatched` audit action's payload gains an optional `thread_id` alongside the existing `talk_id`; mutually exclusive.

### 3.8 Filesystem

```
<runtime>/orgs/<slug>/
├── opc.db
├── talks/                    # existing
└── threads/                  # new
    ├── THR-001.md            # written at archive
    ├── THR-002.md
    └── ...
```

Abandoned threads do NOT write a transcript file. Transcript file is written atomically inside the `/archive` transaction.

### 3.9 Transcript file shape

```markdown
---
thread_id: THR-014
subject: Refund policy ≥30 days
started_at: 2026-05-13T10:42:00Z
archived_at: 2026-05-13T14:10:00Z
participants: [engineering_head, payment_agt]
forwarded_from_id: null
turns_used: 7
new_learnings_total: 3
new_kb_slugs: [refund-window-policy]
---

# Summary

<founder's archive summary, verbatim>

# Transcript

## Message 1 — founder · 2026-05-13T10:42:00Z
> To: @engineering_head, @payment_agt

should we cap refunds at 30 days?

## Message 2 — payment_agt · 2026-05-13T10:43:12Z (reply)

Alipay allows up to 60d; Stripe 120d. We'd lose ~$2k/mo to early-refusals.

## Message 3 — engineering_head · 2026-05-13T10:44:01Z (👁 declined: "payment_agt covered it")

## Message 4 — engineering_head · 2026-05-13T10:51:33Z (system: dispatched TASK-091 to dev_agent)
> brief: Implement 45-day refund window with grace period audit

...
```

The transcript is the authoritative human-readable archive. `opc threads show THR-014 --transcript` reads this file.

## 4. Authority and addressing

### 4.1 Founder authority

The founder has unconditional authority over the thread: compose, send any message, invite any agent (cross-team OK), archive, abandon, dispatch tasks (via the existing `opc run` path — no special thread mechanism needed for the founder).

### 4.2 Agent authority

An agent's authority on a thread is **co-presence with the founder**, scoped to their own invocation turn. While running a subprocess for thread `THR-NNN` with a pending `thread_invocations` row, an agent may:

- Post a reply (`opc threads reply ...`).
- Decline to reply (`opc threads decline ...`).
- Dispatch a task (`opc threads dispatch ...`) — see §7.
- Submit a close-out (`opc threads close-out ...`) — only when `purpose='close_out'`.

Authority is enforced via the invocation token (§3.6.1). Each token is single-use for reply/decline/close-out (terminal) and at-most-once for dispatch (the dispatch leg consumes only the dispatch slot on the row). Without a valid pending token, every callback returns 401 `invocation_token_invalid` or 409 `invocation_token_consumed`. This guarantees: one daemon-triggered invocation → at most one terminal callback + at most one dispatch from that agent on that thread at that seq.

### 4.3 Addressing semantics

The `addressed_to_json` field on a message carries either `["@all"]` or a list of specific agent names. The To: field is set explicitly by the founder in the TUI compose pane (or by `--to` on the CLI).

| Addressing | Who is invoked | Skill guidance for the invoked agent |
|---|---|---|
| Specific agent names (e.g., `[engineering_head]`) | Just those agents | "You were addressed individually — reply unless you have a strong reason not to." |
| `["@all"]` | Every current participant except the speaker | "You were addressed via @all. Reply only if you have material to add the others haven't covered. Default to silence." |

Participants who are NOT addressed are NOT invoked for that message — but they will see it in their prompt the next time they ARE invoked. This bounds fan-out cost. A "broadcast to nobody" addressing is not allowed; the To: field is required.

### 4.4 Adding a new participant

The founder invites an agent via the TUI ("I" key) or `opc threads invite --thread-id THR-NNN --agent <name>`. Effect:

1. Insert row into `thread_participants`.
2. Insert a `kind='system', kind_tag='participant_added'` message at the next `seq`.
3. Invoke the new agent once with the **full prior thread** as context plus the system note "the founder has added you to this thread." The agent may post a brief intro reply or decline. This first-invocation reply is the same shape as any other reply / decline.

The new agent's `addressed_to_json` for that bootstrap invocation is logically `["@<new_agent>"]` — the founder addressed them by inviting them.

### 4.5 Forwarding

Forwarding is the TUI "F" action (or `opc threads forward --source THR-SRC|TALK-SRC --to <recipients> [--note "..."]`):

1. Compose pane opens prefilled with a markdown blockquote of the source's transcript / summary.
2. Founder edits the body (adds context, trims), picks recipients, sends.
3. The new thread record has `forwarded_from_id = "THR-SRC"` (or `"TALK-SRC"`) and `forwarded_from_kind = 'thread'|'talk'`.
4. From there, the new thread behaves identically to any composed thread.

The quoted material lives in the message body, not in a separate column. The `forwarded_from_id` column is purely provenance for the UI ("This thread was forwarded from THR-008") and lineage queries.

## 5. HTTP API — `src/daemon/routes/threads.py`

All routes use the existing bearer-token dependency. Per-org path prefix is `/api/v1/orgs/{slug}/threads/...`.

| Method | Path | Purpose | Caller |
|---|---|---|---|
| POST | `/threads` | Compose new thread | founder (CLI / TUI) |
| GET | `/threads` | List threads with filters | founder |
| GET | `/threads/{id}` | Detail + recent messages | founder (and any participant for read-only) |
| GET | `/threads/{id}/messages` | Paginated message list | founder |
| GET | `/threads/{id}/tail` | SSE stream of new messages | founder (TUI) |
| POST | `/threads/{id}/send` | Founder posts a message to existing thread | founder |
| POST | `/threads/{id}/invite` | Add a participant | founder |
| POST | `/threads/{id}/reply` | Agent posts a reply (consumes token) | agent (callback) |
| POST | `/threads/{id}/decline` | Agent declines to reply (consumes token) | agent (callback) |
| POST | `/threads/{id}/dispatch` | Agent dispatches a task from thread (doesn't consume token) | agent (callback) |
| POST | `/threads/{id}/close-out` | Agent close-out at archive (consumes token) | agent (callback) |
| POST | `/threads/{id}/archive` | Founder archives → enters `archiving` | founder |
| POST | `/threads/{id}/abandon` | Founder abandons | founder |
| POST | `/threads/{id}/extend` | Bump `turn_cap` | founder |

### 5.1 Compose — `POST /threads`

Request:
```json
{
  "subject": "Refund policy ≥30 days",
  "recipients": ["engineering_head", "payment_agt"],
  "body_markdown": "should we cap refunds at 30 days?",
  "addressed_to": ["@all"],
  "forwarded_from_id": "TALK-008",
  "forwarded_from_kind": "talk"
}
```

`addressed_to` defaults to `["@all"]` if omitted. `forwarded_from_id` / `forwarded_from_kind` are optional and must either both be set or both absent.

Validation order:
1. `subject` non-empty after `strip()`. Else 422.
2. `recipients` non-empty; each must be a registered approved agent in this org. Else 422 or 404 `unknown_agent` with the offending name.
3. `body_markdown` non-empty after `strip()`. Else 422.
4. `addressed_to` is either `["@all"]` or a subset of `recipients`. Else 422 `addressed_to_not_subset`.
5. If `forwarded_from_id` set, verify the source exists (thread or talk) in this org. Else 404 `forwarded_source_not_found`.
6. `turn_cap` honored from org config if set, else default 500.

Effect (single SQLite transaction under `state.db_lock`):
- Allocate `thread_id = state.db.next_thread_id()`.
- Insert `threads` row.
- Insert `thread_participants` rows for every recipient (`added_by="founder"`).
- Insert message at `seq=1`: `speaker="founder"`, `kind="message"`, `addressed_to_json` from body.
- Audit `thread_started`.
- Outside the lock, for each addressed agent, enqueue a `ThreadInvocation` (§6).

Response:
```json
{
  "thread_id": "THR-014",
  "started_at": "2026-05-13T10:42:00Z",
  "pending_replies": ["engineering_head", "payment_agt"]
}
```

### 5.2 Founder reply — `POST /threads/{id}/send`

Request: same shape as compose minus `subject` / `recipients` / forwarded fields. Validation: thread must be `open`; `addressed_to` must be subset of current participants OR `["@all"]`. Effect mirrors compose's message-insert + fan-out steps.

### 5.3 Agent reply — `POST /threads/{id}/reply`

Request (matches the JSON the skill writes to `/tmp/thread-reply-<id>-<seq>.json`):
```json
{
  "thread_id": "THR-014",
  "invocation_token": "8b3f...e91a",
  "speaker": "engineering_head",
  "body_markdown": "I'd lean toward 45 days as a compromise...",
  "in_response_to_seq": 1
}
```

Validation (in order; each step gates the next):
1. Thread is `open`. Else 400 `thread_not_open`.
2. `invocation_token` exists, `status='pending'`, and `(thread_id, agent_name=speaker)` matches the row. Else 401 `invocation_token_invalid` (missing/mismatched) or 409 `invocation_token_consumed` (terminal).
3. Invocation `purpose ∈ {'reply', 'bootstrap'}` (close_out tokens can't be used here). Else 400 `wrong_invocation_purpose`.
4. `speaker` is a current participant. Else 403 `not_participant`.
5. `in_response_to_seq` references a real message that addressed `speaker` (specific or `@all`); bootstrap invocations satisfy this via the `participant_added` system message that triggered them. Else 400 `not_addressed`.
6. `body_markdown` non-empty after strip. Else 422.

Effect (one transaction under `state.db_lock`): insert message at next `seq` with `kind="message"`, `addressed_to_json=null`; mark invocation `status='consumed'`, `consumed_at=now()`; increment `threads.turns_used`. Audit `thread_message_sent`.

### 5.4 Agent decline — `POST /threads/{id}/decline`

Request:
```json
{
  "thread_id": "THR-014",
  "invocation_token": "8b3f...e91a",
  "speaker": "engineering_head",
  "reason": "payment_agt covered the constraint",
  "in_response_to_seq": 1
}
```

Validation: same as §5.3 steps 1–5; step 6 requires `reason` non-empty.

Effect: insert `kind="decline"` message with `body_markdown=null`, `decline_reason=<reason>`; consume the invocation; counts against `turns_used`. Audit `thread_message_sent` with `kind=decline`.

### 5.5 Agent dispatch — `POST /threads/{id}/dispatch`

Request:
```json
{
  "thread_id": "THR-014",
  "invocation_token": "8b3f...e91a",
  "dispatcher": "engineering_head",
  "brief": "Implement 45-day refund window with grace period audit",
  "target_agent": "dev_agent",
  "team": "engineering"
}
```

Token validation: same as §5.3 steps 1–2 (with `agent_name=dispatcher`), plus `dispatched_task_id IS NULL` on the row (at most one dispatch per token). Else 409 `dispatch_already_used`. Invocation purpose may be `'reply'` or `'bootstrap'` (workers in a bootstrap turn can still dispatch). Close-out tokens may NOT dispatch (close-outs are about wrapping up, not creating new work) — purpose `'close_out'` returns 400 `wrong_invocation_purpose`.

After token validation, role/team validation mirrors `POST /talks/{id}/dispatch` (see §7).

Effect:
- Allocate `task_id`.
- Insert `tasks` row with `dispatched_from_thread_id=<thread_id>`, `assigned_agent=effective_target`, `team=effective_team`.
- Insert a `kind='system', kind_tag='task_dispatched'` message at next `seq` in the thread.
- Set `thread_invocations.dispatched_task_id = task_id` (token stays `status='pending'` — the agent must still reply or decline).
- Audit `task_dispatched` (scoped to the new task, payload includes `thread_id`) and `thread_dispatch` (scoped to the thread).
- Outside the lock, `enqueue_task(state, task_id)`.

Response:
```json
{
  "task_id": "TASK-091",
  "team": "engineering",
  "assigned_agent": "dev_agent",
  "dispatched_from_thread_id": "THR-014",
  "system_message_seq": 5
}
```

### 5.6 Invite — `POST /threads/{id}/invite`

Request: `{"agent_name": "qa_engineer"}`. Validation: thread is `open` (not `archiving`); agent is approved-and-registered in this org; agent is not already a participant. Effect: insert participant row, insert `kind='system', kind_tag='participant_added'` message, enqueue a bootstrap invocation for the new agent (mints a pending `thread_invocations` row with `purpose='bootstrap'`).

### 5.7 Archive — `POST /threads/{id}/archive`

Request (founder writes from CLI/TUI):
```json
{
  "summary": "Settled on 45-day window. Engineering owns implementation; QA will validate.",
  "request_close_outs": true
}
```

Effect (split into two phases so close-outs can land before terminal state):

**Phase A — transition to `archiving` (synchronous, returns 202 immediately):**
1. Validate: thread is `open` (or already `archiving` and the caller is retrying with the same summary — idempotent). Else 400 `thread_not_open` (if `archived`/`abandoned`).
2. Reap any still-`pending` conversational invocations (reply/bootstrap) → mark `status='failed'`, `decline_reason='archive_started'`. No auto-decline messages inserted; they'd just clutter the transcript.
3. Mark `threads.status='archiving'`, store `summary`, set `archive_requested_at=now()`.
4. If `request_close_outs=true`, for each current participant enqueue a `purpose='close_out'` invocation (mints a fresh pending token).
5. Audit `thread_archive_requested`.

Response: 202 `{thread_id, status: "archiving", close_out_count, transcript_path: null}`.

**Phase B — finalize to `archived` (background, daemon-driven):**
1. Wait up to `close_out_wait_seconds` (default 300, configurable per-org §11) for all close-out invocations to terminate (`consumed` | `timeout` | `failed`).
2. Mark `threads.status='archived'`, `archived_at=now()`.
3. Insert a `kind='system', kind_tag='archived'` message with the rollup payload.
4. Write `threads/THR-NNN.md` atomically (TalkStore-style pattern).
5. Audit `thread_archived`.

The founder's TUI subscribes to the SSE stream and shows close-out progress live; the founder may `Esc` (no daemon call) to walk away without changing the daemon's behavior — finalization proceeds either way. Late close-outs (after `archived` state) return 409 `thread_already_archived`; their learnings are NOT applied (idempotency would require deduping, and the value of catching a stray late callback isn't worth the complexity — close-outs are best-effort).

While in `archiving`:
- `reply`/`decline`/`dispatch` callbacks return 400 `thread_not_open` (the `open`-state check rejects `archiving`).
- `close-out` callbacks are accepted.
- `send`/`invite`/`extend` return 400 `thread_not_open`.

Retrying `POST /archive` while `archiving` returns 409 `archive_in_progress` with `{archive_requested_at, pending_close_outs}`. Retrying after `archived` returns 200 with the existing `transcript_path` (idempotent).

### 5.8 Abandon — `POST /threads/{id}/abandon`

Request: `{"reason": "..."}`. Effect:
1. Mark `status='abandoned'`, `archived_at=now()` (reused column).
2. Reap any still-`pending` invocations (all purposes) → `status='failed'`, `decline_reason='thread_abandoned'`.
3. No transcript written, no close-outs requested.
4. Audit `thread_abandoned` with the reason.

Valid from any non-terminal state (`open` or `archiving`). From `archiving` it forces termination — pending close-outs are reaped without finalization. Already-`archived` / already-`abandoned` returns 200 idempotent.

### 5.9 Extend — `POST /threads/{id}/extend`

Request: `{"new_cap": 1000}`. Validation: `new_cap > current turn_cap`. Inserts `kind='system', kind_tag='turn_cap_extended'` message. The `turns_used` / `turn_cap` counters are tracked for visibility but no longer enforce a hard gate — the turn-cap guard was removed per THR-046 msg86. `/extend` still bumps the counter (soft, vestigial).

### 5.10 SSE — live updates

Two SSE endpoints drive the TUI:

| Endpoint | Scope | Payload per event |
|---|---|---|
| `GET /threads/events` | Org-wide; inbox-list updates | `{thread_id, event_kind, seq?, pending_replies_count?, status?}` |
| `GET /threads/{id}/tail` | Per-thread; full message-append stream | `{thread_id, seq, speaker, kind, body_preview, addressed_to}` |

The org-wide endpoint fires on thread lifecycle events (created, archived, abandoned, new-reply-while-inbox-collapsed). The per-thread endpoint fires on every `thread_messages` insert. Both support `?since_seq=N` (per-thread) / `?since_ts=ISO` (org-wide) for catch-up replay. Same SSE pattern as task event streams today.

### 5.10.1 Close-out — `POST /threads/{id}/close-out`

Agent callback invoked during the `archiving` phase. Request (matches `/tmp/thread-closeout-<id>-<self>.json`):
```json
{
  "thread_id": "THR-014",
  "invocation_token": "f12c...a04b",
  "agent": "engineering_head",
  "learnings": [{"text": "Refunds beyond 30d hit Alipay's 60d window cleanly; Stripe's 120d is fine."}],
  "kb_slugs": ["refund-window-policy"]
}
```

Validation:
1. Thread is in `open` OR `archiving`. (We accept `open` defensively in case a race lets a close-out land before the state flip is observable — extremely unlikely but harmless.) Else 400 `thread_already_finalized`.
2. `invocation_token` exists, `status='pending'`, `(thread_id, agent_name=agent)` match, `purpose='close_out'`. Else 401/409 as in §5.3.
3. `agent` is a current participant. Else 403 `not_participant`.
4. Each `kb_slugs` entry references an existing KB row (the agent must call `opc kb add` before close-out — same rule as `opc talk end`). Else 400 `kb_slug_not_found`.

Effect:
- Append each `learnings[].text` to `<workspace>/<agent>/learnings.md` via the existing helper (same as talk-end).
- Add `kb_slugs` to `threads.new_kb_slugs_json` (set-union; idempotent on duplicates).
- Mark invocation `status='consumed'`, `consumed_at=now()`.
- Audit `thread_close_out_received` with `{agent, new_learnings_count, new_kb_slugs}`.

This callback does NOT count toward `turns_used` — close-outs are bookkeeping, not conversational turns, and the founder has already opted to archive at this point so the cap is moot.

### 5.11 Turn tracking

**Hard enforcement was removed per THR-046 msg86.** Before that change, each fan-out (compose, send, invite-bootstrap) projected `turns_used + addressed_count` against `turn_cap` and returned HTTP 429 `turn_cap_exceeded`. Today, `turns_used` is still incremented per agent invocation and displayed, but it no longer gates posting, replying, or invocation-minting. `/extend` still bumps `turn_cap` (soft, vestigial).

What counts toward `turns_used`: each **agent invocation** (reply, decline, bootstrap-on-invite, close-out). System messages (founder-archive, dispatch-system, turn-cap-extend, participant-added) do NOT count — they're zero-cost daemon-generated rows. Founder-sent messages themselves don't count either; only the resulting agent fan-out does. The cap measures agent-time spent, not message volume.

## 6. Agent invocation (server-side)

A thread invocation is a one-shot headless executor call, similar to `run_step` but with a different prompt template. It does NOT use the orchestrator's decision loop — there is no manager classification step, no NextStep schema. The agent reads context, decides reply/decline/dispatch, calls the callback, and exits.

### 6.1 Invocation queue

A new `ThreadQueue` (mirrors the existing `TaskQueue` in `src/daemon/queue.py`) holds pending invocations. Enqueuing an invocation is a two-step transaction:

1. Insert a `thread_invocations` row with a fresh `invocation_token` (UUID4 hex), `status='pending'`, `enqueued_at=now()`.
2. Push the row's primary key onto the `ThreadQueue` for a worker to pick up.

Each invocation queued by the daemon carries:
- `thread_id`
- `agent_name` (whom to invoke)
- `triggering_seq` (the message that addressed them; for `bootstrap`, the `participant_added` system message seq; for `close_out`, the `archive_requested` system event's logical seq, recorded on the row)
- `purpose` — one of: `reply` | `bootstrap` | `close_out`
- `invocation_token` — passed into the subprocess prompt; required on the agent's callback

The queue's worker pool processes invocations concurrently across agents but serially per-agent (one invocation at a time per agent workspace — there's no need for parallel concurrent thread turns on the same agent).

### 6.2 Prompt template

The executor receives a system prompt containing:
1. The agent's standard identity block (loaded from their `CLAUDE.md` / `AGENTS.md` — handled by the executor's existing context).
2. A **thread context block** appended to the user prompt:
   ```
   You are participating in thread {THR-NNN}: "{subject}".

   Participants: {list of agents}.
   Started: {started_at}. {if forwarded: "Forwarded from {source_id}."}

   Full message history follows. Most recent message is at the bottom.

   ---
   [Message {seq} — {speaker} · {created_at}]
   {To: {addressed_to} if addressed}
   {body or decline marker or system rendering}
   ---
   [Message {seq} — ...]
   ---

   You have been invoked because:
   {purpose-specific note: "Message {triggering_seq} addressed you individually" |
                          "Message {triggering_seq} addressed @all" |
                          "The founder has added you to this thread" |
                          "This thread is being archived; provide a close-out"}

   Your invocation_token for this turn is: {invocation_token}
   Include this token in every callback payload (reply, decline, dispatch,
   close-out). It authorizes this single turn and is single-use for the
   terminal callback (reply/decline/close-out).

   Consult `protocol/skills/thread/SKILL.md` and respond.
   ```

For large threads, the full history is sent verbatim — no condensation in v1. The `turns_used` counter and soft `/extend` remain for visibility, but hard enforcement was removed per THR-046 msg86.

### 6.3 Invocation execution

`ThreadInvocationRunner` (new — sibling of orchestrator step runners) marks the row `started_at=now()`, calls the executor subprocess with the prompt above (containing the `invocation_token`), sets `--allowedTools Bash(opc *)`, captures the executor session_id and stores it on the row, waits up to `session_timeout_seconds` (resolved per agent — same layered resolution as today's task runner), and observes outcomes through the database state of the invocation row:

- **`reply` callback consumed the token** → success. Subprocess can exit any time.
- **`decline` callback consumed the token** → success. Same.
- **`close_out` callback consumed the token** (only valid when `purpose='close_out'`) → success.
- **`dispatch` recorded `dispatched_task_id`** → not a terminal callback. Runner continues waiting for reply/decline. The subprocess is expected to make one of those before exit.
- **Subprocess exits before any terminal callback** (regardless of exit code) → runner marks token `status='failed'`, `decline_reason="no_callback: exit <code>"`. For `purpose ∈ {reply, bootstrap}`, also inserts an auto-decline message at the next thread `seq` with `kind='decline'` and that reason (so the founder sees the absence). For `purpose='close_out'`, no message is inserted — close-outs are silent. Audit `thread_invocation_failed`.
- **`session_timeout_seconds` elapsed without terminal callback** → runner sends SIGTERM, marks token `status='timeout'`, `decline_reason="invocation_timeout"`. Insert/audit handling same as the no-callback case above.

The runner does NOT enforce the agent's reply vs decline choice — the skill makes that call. The runner only enforces that *some* terminal callback fires within the timeout window, and turns absence into a recorded decline (or, for close-outs, a silent failure).

**Concurrency note**: token status transitions on the daemon side (consume/timeout/fail) run under `state.db_lock` to avoid double-consumption (e.g., a slow reply callback racing with the runner's timeout-handler).

### 6.4 Session tracking

Each thread invocation gets a `session_id` like any other agent session. `audit_log.session_id` lets us trace per-invocation activity. Token usage is recorded in `session_token_usage` with `purpose='thread'` (new value alongside existing `'task'` / `'talk'`).

## 7. Agent dispatch from threads (detail)

This is the agent-spawns-task case the founder explicitly confirmed (see "rethink" discussion 2026-05-13). Mirrors the talk-dispatch design exactly with thread substituted for talk.

### 7.1 Authority rules

| Dispatcher role | Can target | Cross-team |
|---|---|---|
| Worker | Themselves only | Forbidden |
| Team manager | Anyone on their team (incl. self) | Forbidden |

The dispatcher's role and team are resolved exactly as in `talks/dispatch`:
1. `state.teams.team_for_manager(dispatcher)` → manager's team if applicable.
2. Else `state.teams.team_for_agent(dispatcher)` → worker's team.
3. Both None → 403 `dispatcher_team_unknown`.

### 7.2 Validation order

`POST /threads/{thread_id}/dispatch` validates (each step gates the next):

1. Thread exists. Else 404.
2. Thread status is `open`. Else 400 `thread_not_open`.
3. `dispatcher` is a current participant. Else 403 `not_participant`.
4. `brief` non-empty after strip. Else 422.
5. Resolve `dispatcher_team` per §7.1. Else 403.
6. `effective_team = body.team or dispatcher_team`; reject if `≠ dispatcher_team`. Else 403 `cross_team_dispatch_forbidden`.
7. `effective_target = body.target_agent or dispatcher`.
8. **Worker rule**: not-manager AND `effective_target != dispatcher` → 403 `worker_must_self_dispatch`.
9. **Manager rule**: manager AND `effective_target` not in their team → 403 `target_not_in_team`.
10. Target agent is registered with `status='approved'` and a workspace exists. Else 404 `unknown_agent`.

### 7.3 Effect

Same single-transaction shape as talks/dispatch, with two additions:
- The new task's `dispatched_from_thread_id` = thread_id.
- A `kind='system', kind_tag='task_dispatched'` message is inserted into the thread at the next `seq` with `speaker=dispatcher`.

Audit: both `task_dispatched` (scoped to the new task, payload `{thread_id, dispatcher, target_agent, team}`) AND `thread_dispatch` (scoped to the thread, payload `{task_id, dispatcher, target_agent, team}`).

### 7.4 Visibility

Every participant sees the system message in the TUI thread view and in the archived transcript. The TUI renders it as:
```
─── engineering_head dispatched TASK-091 to dev_agent ───
   brief: Implement 45-day refund window with grace period audit
```

No special acknowledgement is required from other participants. The system message does NOT trigger fan-out invocations.

## 8. CLI surface

### 8.1 Founder commands

```
opc threads                                 # alias: opens TUI ('opc inbox' is NOT exposed)
opc threads list [--status open|archived|abandoned] [--limit N]
opc threads show THR-NNN [--transcript]
opc threads compose --org <slug> --from-file /tmp/thread-compose.json
opc threads send --org <slug> --thread-id THR-NNN --from-file /tmp/thread-send.json
opc threads invite --org <slug> --thread-id THR-NNN --agent <name>
opc threads forward --org <slug> --source THR-SRC|TALK-SRC --recipients a,b --note-file /tmp/note.md
opc threads archive --org <slug> --thread-id THR-NNN --from-file /tmp/thread-archive.json
opc threads abandon --org <slug> --thread-id THR-NNN --reason "..."
opc threads extend --org <slug> --thread-id THR-NNN --new-cap 1000
```

`opc threads` with no subcommand launches the Textual TUI. All other subcommands operate non-interactively. `compose`, `send`, `archive` use `--from-file` for the same reason existing callbacks do (single-line `opc` invocation discipline; multi-line content goes to a temp file).

### 8.2 Agent callback commands

```
opc threads reply --org <slug> --thread-id THR-NNN --from-file /tmp/thread-reply-THR-NNN-<seq>.json
opc threads decline --org <slug> --thread-id THR-NNN --from-file /tmp/thread-decline-...json
opc threads dispatch --org <slug> --thread-id THR-NNN --from-file /tmp/thread-dispatch-...json
opc threads close-out --org <slug> --thread-id THR-NNN --from-file /tmp/thread-closeout-...json
```

All four are agent-side callbacks invoked from inside a thread invocation subprocess. The baseline `Bash(opc *)` allow rule covers them. The `--from-file` discipline is mandatory.

## 9. TUI — `opc threads` (no subcommand)

Built with [Textual](https://textual.textualize.io/), which provides reactive widgets, async I/O, CSS-like styling, and SSE integration via httpx.

### 9.1 Layout

```
┌─ opc threads · org: hk-macau-tourism ─────────────────────────────────────┐
│ ┌─ Threads ─────────────┐ ┌─ THR-014  Refund policy ≥30 days ─────────┐  │
│ │ ● THR-014 (2 pending) │ │ Participants: engineering_head, payment_agt│  │
│ │ ○ THR-013 archived    │ │ ───────────────────────────────────────────│  │
│ │ ○ THR-012 archived    │ │ founder · 10:42  · To: @all                │  │
│ │ ● THR-011             │ │   should we cap refunds at 30 days?        │  │
│ │ ○ THR-010 archived    │ │                                            │  │
│ │ ...                   │ │ payment_agt · 10:43  · reply               │  │
│ │                       │ │   Alipay allows up to 60d; Stripe 120d.    │  │
│ │                       │ │                                            │  │
│ │                       │ │ engineering_head · 👁 read, no reply       │  │
│ │                       │ │   reason: payment_agt covered the constr.  │  │
│ │                       │ │                                            │  │
│ │                       │ │ ─── system · engineering_head dispatched   │  │
│ │                       │ │     TASK-091 to dev_agent ─────────────    │  │
│ │                       │ │                                            │  │
│ │                       │ └────────────────────────────────────────────┘  │
│ │                       │ ┌─ Reply ────────────────────────────────────┐  │
│ │                       │ │ To: [@all ▼ ]                              │  │
│ │                       │ │ ┌────────────────────────────────────────┐ │  │
│ │ [N]ew [F]orward       │ │ │ then let's compromise at 45 days...    │ │  │
│ │ [I]nvite [A]rchive    │ │ └────────────────────────────────────────┘ │  │
│ └───────────────────────┘ │              [Send: Ctrl+Enter]            │  │
│                           └────────────────────────────────────────────┘  │
└───────────────────────────────────────────────────────────────────────────┘
```

### 9.2 Panes

- **Inbox pane** (left): scrollable thread list. Filled dot ● = has pending agent invocations OR unread agent replies since founder's last view. Empty circle ○ = no pending. Bottom shows current archive/abandon state via color (green=open, grey=archived, red=abandoned).
- **Thread pane** (top-right): scrollable message log of the selected thread. Speaker-color-coded, timestamps shown in local time. System messages render as `─── ... ───` separators. Decline messages render as `👁 read, no reply` followed by the reason.
- **Compose pane** (bottom-right): To: dropdown (multi-select among current participants + `@all`) and message body (Textual `TextArea`). Visible when the founder hits `R` (reply), `N` (new — switches the whole right side to a full-screen compose), `F` (forward — prefills with quoted source), `I` (invite — prompts for an agent name from the org's roster).

### 9.3 Keybindings

| Key | Action |
|---|---|
| `↑` / `↓` | Navigate inbox list |
| `Tab` | Cycle between inbox / thread / compose panes |
| `Enter` | Open selected thread in main view |
| `N` | New thread (compose) |
| `R` | Reply to current thread (focuses compose pane) |
| `F` | Forward selected thread (compose prefilled with quoted body) |
| `I` | Invite participant (modal prompt with org agent picker) |
| `A` | Archive current thread (modal prompts for summary; checkbox to request close-outs) |
| `X` | Abandon current thread (modal prompts for reason) |
| `Ctrl+Enter` | Send the compose / reply |
| `Esc` | Cancel compose / dismiss modal |
| `Ctrl+R` | Refresh thread list |
| `?` | Help overlay |
| `Ctrl+C` | Quit TUI |

### 9.4 Live updates

The TUI subscribes via SSE to `/api/v1/orgs/{slug}/threads/events` (a fan-out of all per-thread events for inbox-list updates) and to `/threads/{id}/tail` for the active thread. Both run in async tasks alongside the Textual app loop. Message append events update the visible thread pane in place; new-thread events update the inbox list.

### 9.5 Forward action UX

`F` on a selected thread or talk opens a full-screen compose with:
- Body prefilled: a markdown blockquote of the source's transcript (for threads) or summary + transcript (for talks). Capped at 4 KiB; longer sources are truncated with `(... source truncated; full content at THR-008 ...)`.
- `forwarded_from_id` and `forwarded_from_kind` set in the eventual `POST /threads`.
- To: field empty, founder fills in.
- Subject field empty, founder fills in.

The founder edits and sends; from then on the new thread is normal.

### 9.6 Implementation notes

- TUI lives in `src/tui/threads_app.py`. Single-file Textual app.
- Talks to the daemon via the existing `src/client/client.py` (extended with thread methods).
- All HTTP calls async — no blocking the UI loop.
- Failed sends surface as Textual notifications (toast-style) with retry option.
- TUI exits cleanly on `Ctrl+C` and on daemon disconnect (with reconnect prompt).

## 10. Skill — `protocol/skills/thread/SKILL.md`

New skill, copied into every workspace by the workspace adapters (same mechanism as `start-task`, `talk`, `dispatch`, etc.). Instruction-first, not code-first.

### 10.1 Frontmatter

```yaml
---
name: thread
description: Use this skill when the orchestrator invokes you for thread participation. Decide whether to reply, decline, or dispatch a task — all based on the thread context provided in your prompt.
---
```

### 10.2 Body sketch

```
# thread

You've been invoked because something happened on a thread (THR-NNN). The full
prior history is in your prompt, along with a note explaining WHY you were
invoked AND an `invocation_token` that authorizes this single turn. Read the
history end-to-end, then decide one outcome.

## Your invocation_token

Look for this line in your prompt:

    Your invocation_token for this turn is: <opaque-string>

You MUST include this token in every callback payload (reply, decline,
dispatch, close-out). It proves the callback is part of this live turn. Without
it, the daemon will reject the call with 401 invocation_token_invalid. The
token is single-use for the terminal callback (reply/decline/close-out) — a
second terminal callback with the same token returns 409.

## Identify the trigger

The "You have been invoked because" line tells you which case applies:

- "Message N addressed you individually" — the founder (or another participant
  via the system) put your name in the To: field of message N.
- "Message N addressed @all" — message N targets every participant.
- "The founder has added you to this thread" — bootstrap. You may post a brief
  intro or decline. No further obligation.
- "This thread is being archived" — close-out. Different procedure: see §
  Close-out below.

## Reply, decline, or dispatch

For everything except close-out, pick exactly one terminal outcome (reply OR
decline). You MAY additionally dispatch a task before the terminal callback;
dispatch alone does not end the turn.

### Reply

Write `/tmp/thread-reply-<thread_id>-<seq>.json`:
{"thread_id": "<id>", "invocation_token": "<token>",
 "speaker": "<your name>", "body_markdown": "...", "in_response_to_seq": <N>}

Then single-line:
opc threads reply --org <slug> --thread-id <id> --from-file /tmp/thread-reply-<id>-<seq>.json

Reply when:
- You were addressed individually (default behavior).
- You were addressed via @all AND you have material to add the others haven't
  covered (correction, missing context, agreement with reasoning).

### Decline

Write `/tmp/thread-decline-<thread_id>-<seq>.json`:
{"thread_id": "<id>", "invocation_token": "<token>",
 "speaker": "<your name>", "reason": "...", "in_response_to_seq": <N>}

Then:
opc threads decline --org <slug> --thread-id <id> --from-file /tmp/thread-decline-<id>-<seq>.json

Decline when:
- You were addressed via @all AND another participant has already covered what
  you'd say — restating wastes founder attention.
- You don't have relevant expertise on the topic.

Keep the reason short and substantive ("payment_agt covered the constraint",
not "I have nothing to add").

### Dispatch a task (optional, before reply/decline)

If the thread has converged on a concrete action that fits your authority
(workers self-dispatch only; managers can dispatch to anyone on their team),
you may submit a task without ending the thread. Cross-team dispatch is
forbidden — if the action belongs to another team, surface it in your reply
and let the founder loop their manager in.

Write `/tmp/thread-dispatch-<thread_id>.json`:
{"thread_id": "<id>", "invocation_token": "<token>",
 "dispatcher": "<your name>", "brief": "...",
 "target_agent": "<name>" /* optional, defaults to yourself */,
 "team": "<team>" /* optional, defaults to your team */}

Then:
opc threads dispatch --org <slug> --thread-id <id> --from-file /tmp/thread-dispatch-<id>.json

Each dispatch posts a system message into the thread for transparency.

Dispatching does NOT end the turn — you MUST still issue a reply or decline
afterwards to release the invocation token. If you exit without either, the
daemon will auto-decline on your behalf with reason="no_callback".

## Close-out (archive)

When invoked with "This thread is being archived":

1. Review what was discussed.
2. Identify KB-worthy material (apply rules from protocol/06-knowledge-base.md
   §2). Write those with `opc kb add` BEFORE the close-out callback.
3. Identify durable learnings for yourself — write them to
   /tmp/thread-closeout-<thread_id>-<your_name>.json:
   {"thread_id": "<id>", "invocation_token": "<token>",
    "agent": "<your name>",
    "learnings": [{"text": "..."}],
    "kb_slugs": ["the-slugs-you-just-added"]}
4. Run:
   opc threads close-out --org <slug> --thread-id <id> --from-file /tmp/thread-closeout-<id>-<your_name>.json

Other participants will produce their own close-outs in parallel. Each
contributes to their own learnings.md; KB slugs are unioned.

Close-out tokens may NOT dispatch tasks. If something actionable surfaces in
the close-out, mention it in the learnings text instead.

## What NOT to do

- Do NOT spawn arbitrary side-effects (run repos, hit APIs) inside a thread
  invocation. Threads are conversation. Side-effects flow through tasks.
- Do NOT issue multiple terminal callbacks (reply AND decline) in one
  invocation. One terminal outcome per turn. Dispatch is the only non-terminal
  extra.
- Do NOT parse `@text` in message bodies as routing. The `addressed_to` list
  in the message is authoritative.
- Do NOT share or persist your `invocation_token` outside the current
  subprocess — it's single-use and turn-scoped.
```

### 10.3 Permissions

Baseline `Bash(opc *)` allow rule covers all four agent-side commands. No frontmatter `allow_rules` changes.

## 11. Configuration

Per-org `org/config.yaml` gains an optional `threads` section:

```yaml
threads:
  enabled: true                # default true; setting false disables compose endpoint
  default_turn_cap: 500        # default 500; per-thread override at compose time
  invocation_timeout_seconds:  # overrides session timeout for thread invocations only
  close_out_wait_seconds: 300  # how long /archive waits for close-outs before finalizing
```

All fields optional. Loaded via `OrgConfig` (extends today's structure). Unknown keys remain forward-compat-ignored.

No new top-level `OPC_` env vars in v1. Org-level config is the customization surface.

## 12. Error handling

| Condition | Response |
|---|---|
| Compose with unknown recipient | 404 `unknown_agent`, no thread row created. |
| Compose with `addressed_to` containing a name not in `recipients` | 422 `addressed_to_not_subset`. |
| Founder send when thread is not `open` (i.e., `archiving`/`archived`/`abandoned`) | 400 `thread_not_open`. |
| Agent reply/decline/dispatch when thread is not `open` | 400 `thread_not_open`. |
| Agent callback missing `invocation_token` or token doesn't exist / mismatched `(thread_id, agent_name)` | 401 `invocation_token_invalid`. |
| Agent callback presenting an already-consumed token | 409 `invocation_token_consumed`. |
| Agent callback presenting a token whose purpose is wrong for the call (e.g., close_out token used for reply, or reply token used for close-out) | 400 `wrong_invocation_purpose`. |
| Agent dispatch when the token already has `dispatched_task_id` set | 409 `dispatch_already_used`. |
| Agent reply when not a participant | 403 `not_participant`. |
| Agent reply with `in_response_to_seq` referencing a message that didn't address them | 400 `not_addressed`. |
| `addressed_to` contains an agent who is not a current participant | 422 `addressee_not_participant`. |
| Turn cap tracking is soft/display-only — no 429 gate remains after THR-046 msg86 removal. |
| Invocation subprocess exits without terminal callback within `session_timeout_seconds` | Daemon marks token `status='timeout'`; auto-decline inserted for `reply`/`bootstrap` purposes; close-out failures are silent. Audit `thread_invocation_timeout` / `thread_invocation_failed`. |
| `POST /archive` while thread is `archiving` (re-entry) | 409 `archive_in_progress` with `{archive_requested_at, pending_close_outs}`. |
| `POST /archive` while thread is `archived` | 200 idempotent with existing `transcript_path`. |
| `POST /close-out` while thread is `archived` (late callback) | 409 `thread_already_finalized`; learnings/KB are dropped. |
| `POST /close-out` while thread is `open` | 400 `thread_not_archiving`. |
| Invite an agent already a participant | 409 `already_participant`. |
| Invite while thread is `archiving` / `archived` / `abandoned` | 400 `thread_not_open`. |
| Forward source not found | 404 `forwarded_source_not_found`. |
| Two-column violation (both `dispatched_from_talk_id` and `dispatched_from_thread_id` non-NULL) | 500 — should be unreachable; daemon enforces mutual exclusion at insert. |

## 13. Migration / backward compatibility

Schema changes are idempotent ALTERs on daemon startup:
- Three new tables (`threads`, `thread_participants`, `thread_messages`).
- `tasks.dispatched_from_thread_id` column.
- `OrgConfig` `threads:` section optional, defaults preserve current behavior.

Existing runtimes with no thread activity see zero behavior change. No multi-org-style migration command needed — schema-only.

Talks remain unchanged. Their existing data model and lifecycle continue to work. The `dispatched_from_talk_id` column on tasks remains untouched.

## 14. Testing

### 14.1 Unit

- `next_thread_id` allocation via `MAX(suffix)` (mirrors `next_talk_id`).
- Compose validation: recipients existence, `addressed_to` subset, non-empty body/subject.
- Reply / decline validation: thread-open, participant-check, not-addressed.
- **Invocation-token validation**: missing → 401; wrong agent → 401; consumed → 409; wrong purpose → 400; reply-with-close_out token rejected; close-out-with-reply token rejected; dispatch twice on same token → 409.
- Dispatch authority gates: worker self-only, manager team-only, cross-team forbidden.
- Turn-cap accounting: replies and declines count; system messages and close-outs do NOT count; cap-exceeded returns 429.
- System-message renderers: `participant_added`, `task_dispatched`, `turn_cap_extended`, `archived`.
- Forward source resolution: thread and talk both produce valid quoted bodies.
- Mutual exclusion of `dispatched_from_*` columns on tasks.
- **State-machine guards**: reply/decline/dispatch/send/invite/extend reject when `status='archiving'`; close-out accepted when `status='archiving'`, rejected when `status='archived'`/`'open'`/`'abandoned'`.

### 14.2 Integration

- `compose → fake claude replies → SSE event fires → archive → transcript file written`.
- `compose to two agents → one replies, one declines → both events visible in tail`.
- `compose → invite → new agent bootstrap invocation happens with full prior history → reply lands`.
- `compose → agent dispatches a task → system message appears in thread → task lands on assignee → original thread still open`.
- `forward (talk → thread) → new thread carries quoted body → forwarded_from_id resolves`.
- `turn_cap exhaustion → 429 → extend → next send succeeds`.
- `archive with request_close_outs=true → status='archiving' immediately → all participants invoked → close-outs land while archiving → status flips to 'archived' → transcript written with rollup`.
- `archive → during 'archiving', reply/decline/dispatch from any agent return 400 thread_not_open; close-out from a non-participant is rejected; late close-out after 'archived' returns 409`.
- `abandon → no close-out invocations → no transcript file → still-pending invocation rows reaped to status='failed'`.
- `out-of-band callback attempt: agent runs opc threads reply WITHOUT an invocation_token (or with a fabricated one) → 401`.
- `replay-attack: agent saves their token, replies, then attempts a second reply with the same token → 409 invocation_token_consumed`.
- TUI smoke test: launches against a live daemon, lists threads, opens one, sends a reply, observes SSE update. (Textual has snapshot-test support; one snapshot per major pane.)

### 14.3 Token / cost tracking

`session_token_usage.purpose='thread'` rows assert on the `opc tokens` rollup. Per-thread cost rollup added as a separate sub-task (not gated for v1 — exists as a `GET /threads/{id}/cost` after Phase 1 if needed).

## 15. Out-of-scope reminders (DO NOT include)

(See §2 for the full list. Repeated here as a "do not slip into" pointer during implementation.)

- Agent-initiated threads.
- Agent invites without founder.
- Mid-thread `opc learning` callbacks.
- Inline body `@mention` routing.
- Reply-tree structure.
- Full-text search.
- Per-agent thread mute.
- Real-time streaming agent replies.
- `opc manage-agent` from threads.
- Founder participating from inside an agent's workspace CLI.
- Feishu surface.

## 16. Open implementation choices (not gating the design)

These are decisions deferrable to the implementation plan, not blocking spec approval:

- TUI library version pinning and dependency placement (Textual is the choice; version pin in the plan).
- SSE event payload shape — the spec fixes the keys, the plan finalizes JSON encoding details and whether body previews truncate at 80 or 160 chars.
- Whether `ThreadQueue` is a separate FIFO from `TaskQueue` or shares the worker pool with a tag discriminator. Recommendation: separate queue, separate worker pool, smaller worker count (e.g., 4).
- Transcript file format details (extension of the talk transcript format; same atomic-write pattern).
- Whether the SSE event includes the full message body or just metadata + seq (TUI fetches detail). Recommendation: metadata + seq for inbox events; full body for per-thread tail.
