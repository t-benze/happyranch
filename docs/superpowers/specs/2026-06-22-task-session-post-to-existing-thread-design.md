# Task-session post into an existing thread (`post-as-agent`)

Status: implemented (THR-027, founder-approved design + rulings).
Date: 2026-06-22.

## Problem

A live task session can already *open* a new thread
(`compose_thread_as_agent`, `runtime/daemon/routes/threads.py:compose_thread_as_agent`)
and *reply* when it holds a thread invocation token
(`runtime/daemon/routes/threads.py` reply route). It cannot **post a new
message into an existing thread it already participates in** without being
handed an invocation token first. That gap forces an agent who learns
something mid-task to either open a redundant new thread or wait to be
re-invoked. THR-027 closes the gap with a task-session-authenticated append
endpoint, gated to current participants (founder ruling THR-027 seq=18).

## Endpoint contract

`POST /api/v1/orgs/{slug}/threads/{thread_id}/post-as-agent`

Request body (`PostAsAgentBody`, `runtime/daemon/routes/threads.py`):

| field | type | notes |
| --- | --- | --- |
| `composer` | `str` | the posting agent's name (the speaker) |
| `task_id` | `str \| None` | the live task's id; required (binding) |
| `session_id` | `str \| None` | the live session id; required (binding) |
| `body_markdown` | `str` | message body; may be empty if attachments present |
| `attachments` | `list[AttachmentRefBody]` | shared-artifact refs, normalized via `_normalize_attachments` |

Response `200`:

```json
{"thread_id": "THR-NNN", "seq": <int>, "pending_replies": ["other_agent", ...]}
```

`pending_replies` is the set of participants who received a `REPLY` invocation
— **every participant except the composer**.

It is the task-session analogue of `POST /threads/{thread_id}/send`
(`send_thread_endpoint` / `_send_thread_message_inprocess`,
`runtime/daemon/routes/threads.py`) and a sibling of
`POST /threads/compose-as-agent`. Like both, it is an agent callback and is
not exercised from the web UI (exempted in
`web/src/test/openapi-coverage.test.ts`).

## Authorization — participant-only

Two layers, both grounded in existing patterns:

1. **Task binding** — copied from `compose_thread_as_agent`: the
   `(task_id, session_id)` pair proves the caller is a live session that owns
   the named task. The active-session check uses
   `org.sessions.get_active(task_id, composer)`
   (`runtime/daemon/sessions.py:get_active`).
2. **Participant gate** (founder ruling THR-027 seq=18): the composer must
   already be a current participant of the thread, via
   `org.db.list_thread_participants(thread_id)`
   (`runtime/infrastructure/database.py:list_thread_participants`). A
   non-participant cannot post — they would have to be invited first. This is
   the deliberate doctrine: posting is for agents already on the thread, not a
   back door to reach an arbitrary thread.

The bearer-token / auth model is unchanged: the route inherits the router's
`require_token()` dependency exactly like `compose-as-agent` and `send`.

## Error taxonomy

Evaluated in this order (first failure wins):

| code | status | condition |
| --- | --- | --- |
| `binding_required` | 422 | `task_id` or `session_id` missing |
| `unknown_task` | 404 | `org.db.get_task(task_id)` is `None` |
| `composer_not_task_owner` | 403 | `task.assigned_agent != composer` |
| `session_mismatch` | 409 | `get_active(task_id, composer)` absent or ≠ `session_id` |
| `not_found` | 404 | thread does not exist |
| `not_a_participant` | 403 | composer not in the thread's participant list |
| `thread_not_open` | 400 | thread status is not `OPEN` |
| `turn_cap_exceeded` | 429 | `turns_used + 1 > turn_cap` |

Notes:

- The binding block deliberately omits `compose_thread_as_agent`'s
  `task_not_active` (400) status gate. The founder-approved design (and the
  parent brief THR-027) enumerate exactly the four binding errors above; the
  live-session guarantee comes from `session_mismatch` — a finished task whose
  session has been cleared (`SessionTracker.clear`) fails the active-session
  check. Keeping the taxonomy to four binding codes matches the settled
  design.
- `not_found` is checked before `not_a_participant`: participation can only be
  evaluated against a thread that exists, and a missing thread should report
  `not_found`, not `not_a_participant`. Participation is then checked before
  `thread_not_open`, so "are you allowed to touch this thread at all" precedes
  "is the thread still open".

## Effect

Mirrors `_send_thread_message_inprocess`, with two intentional differences
(speaker attribution + composer self-exclusion):

1. Append a `MESSAGE` attributed to the **agent** (`speaker=composer`) via
   `org.db.append_thread_message(..., sent_from_task_id=task_id)`.
2. `org.db.increment_thread_turns_used(thread_id, by=1)`.
3. Audit row via `AuditLogger(org.db).log_thread_message_sent`.
4. Mint a `REPLY` `ThreadInvocation` for **every other participant**
   (`org.db.mint_thread_invocation`), excluding the composer.
5. Enqueue a `ThreadJob` per minted token onto `org.thread_queue`.
6. Publish the SSE event via `_publish_thread_event`.

## Schema — additive provenance column

`runtime/infrastructure/database.py`:

- `thread_messages` gains `sent_from_task_id TEXT` (nullable) — added to the
  `CREATE TABLE` shape for fresh DBs and via an additive `ALTER TABLE ... ADD
  COLUMN` in the duplicate-column-swallowing migration block, mirroring the
  `threads.composed_from_dream_id` precedent. **Additive only** — no existing
  column is altered, dropped, or overloaded (the load-bearing schema
  invariant). No index (provenance is read by message, never queried by task).
- `append_thread_message` gains an optional `sent_from_task_id: str | None =
  None` kwarg and persists it. Existing callers (founder compose/send, agent
  compose, reply, decline, invite, system messages) leave it `NULL`.

The column is storage-only; it is not surfaced on the `ThreadMessage` model
(`runtime/models.py`) — no read path needs it yet, so adding it there would be
out of scope.

## Doctrine reconciliation

Three ways an agent contributes to a thread, now distinct:

- **compose-as-agent** — opens a **NEW** thread; the composer is added as a
  participant. Authz: task-session binding only.
- **post-as-agent** — appends to an **EXISTING** thread the agent **already
  participates in**. Authz: task-session binding **plus** participant-only
  gate. No invocation token needed.
- **reply** — responds within a thread turn the agent was **invoked** for;
  requires the single-use `invocation_token` from that invocation.
