# Thread "agent working on a reply" live indicator — Design

**Status:** Approved (brainstorm 2026-06-02)
**Scope:** Web UI + daemon. Threads only.

## Problem

When a message is posted to a thread, every non-speaker participant gets a REPLY
invocation and a `claude -p` subprocess is spawned. Today the web UI collapses
every non-terminal invocation into a single `pending…` label in
`ResponderStatusStrip`. The founder cannot tell the difference between "queued,
nothing running yet" and "an agent's subprocess is actively working on a reply
right now" — the thread view feels clueless about live activity.

The data to disambiguate already exists: `thread_invocations.started_at` is
stamped when the subprocess spawns (`stamp_invocation_started`). It is simply not
selected into the responder projection, and the strip has no state for it.

## Goal

Surface, in real time, which participants are **actively working** on a reply,
with elapsed time, distinct from those still **queued**. Update live as agents
start and finish.

## Non-goals (YAGNI)

- No stuck/stale escalation (amber/red threshold). Plain elapsed time only.
- No mid-turn progress heartbeat for thread invocations (they remain single-turn;
  elapsed-since-start is the only liveness cue).
- No new DB columns — `started_at` already exists.
- No new HTTP routes — the change rides the existing `GET /threads/{id}` response
  and the existing thread SSE tail.

## State model

The wire currently exposes responder statuses `pending | replied | declined |
failed` (DB `consumed`→`replied`, mapped by `_wire_status`). We **split
`pending`** into two states using `started_at`:

| Wire status | DB condition | Strip label |
|---|---|---|
| `queued` | invocation `pending` AND `started_at IS NULL` | `queued` |
| `working` | invocation `pending` AND `started_at IS NOT NULL` | `working <elapsed>` (e.g. `working 45s`) |
| `replied` | `consumed` | `replied` |
| `declined` | `declined` | `declined` |
| `failed` | `failed` OR `timeout` | `failed` |

`pending` no longer appears on the wire. Elapsed time is computed **client-side**
from `started_at` versus the browser clock — no server-side "now" and no server
clock dependency. Each responder entry therefore also carries `started_at`.

## Architecture / data flow

```
message posted
   → REPLY invocations minted (pending, started_at NULL)        [status: queued]
   → thread_runner spawns subprocess
       → stamp_invocation_started (started_at set)              [status: working]
       → publish SSE invocation_started  ──────────────┐
   → agent calls back reply / decline                   │
       → reply route: message event (existing)          │  thread SSE tail
       → decline route: publish invocation_settled  ────┤  (existing subscription)
   → or run_invocation auto-decline / fail / timeout     │
       → publish invocation_settled  ───────────────────┘
                                                          ▼
                                   web UI invalidates thread-detail query
                                                          ▼
                              refetch GET /threads/{id} → updated responder_status
                                                          ▼
                          ResponderStatusStrip (per message) + ThreadActivityFooter
```

## Backend changes

1. **`Database.list_invocations_for_thread_grouped_by_seq`** — add `started_at` to
   the SELECT and to each entry dict (alongside `agent_name`, `status`,
   `consumed_at`).

2. **`ResponderStatusEntry` model** — add `started_at: str | None = None`.

3. **`_msg_to_dict` responder builder** (`src/daemon/routes/threads.py`) — when an
   entry's DB status is `pending`, emit `working` if `started_at` is set else
   `queued`; pass `started_at` through. Terminal mappings stay in `_wire_status`.

3a. **`GET /threads/{id}/messages` parity (root-cause fix).** This endpoint —
   the *primary* source the strip renders from (`useThreadMessages`; the thread
   detail is only a fallback before it resolves) — currently calls
   `_msg_to_dict(m)` with **no `responders`**, so `responder_status` is always
   `[]` there. That is why the strip looks clueless even before the
   queued/working split. Build `responders_by_seq =
   list_invocations_for_thread_grouped_by_seq(thread_id)` and pass
   `responders=...` to `_msg_to_dict` for `kind == message`, exactly as
   `get_thread_endpoint` already does. Without this, invalidating
   `['thread-messages']` refreshes nothing.

4. **SSE events on the existing `thread_topic(thread_id)`.** Payload shape (note
   the `seq` field carries the **triggering message seq** — see why below):
   `{"thread_id": ..., "seq": <triggering_seq:int>, "kind":
   "invocation_started" | "invocation_settled", "agent_name": ..., "status":
   <wire status>}`. Published **directly** to `thread_topic` (NOT via
   `_publish_thread_event`, which also fans out to the inbox topic — we don't
   want invocation churn lighting up the threads-list badge).

   Publish sites — both in `run_invocation` (`src/daemon/thread_runner.py`),
   both guarded by `if getattr(org_state, "event_bus", None):` (the runner is
   `async def`, so `await org_state.event_bus.publish(...)` is direct; the test
   `FakeOrgState` has no `event_bus`, so the guard makes both silent no-ops):
   - **`invocation_started`** — immediately after `stamp_invocation_started`.
   - **`invocation_settled`** — in the auto-decline / fail / timeout tail (after
     `fail_invocation`).

   **No route changes.** The reply route already publishes a new-message event,
   and the decline route already publishes a `decline_status` event — both
   surface the terminal state on the next refetch. The two `run_invocation`
   publishes are the *only* new ones: they cover the subprocess-start transition
   (previously unpublished) and the no-callback fail/timeout terminal
   (previously unpublished).

   These events are NOT in `_TERMINAL_TYPES`, so they don't close the SSE stream.

   > **Pre-existing quirk (out of scope):** the decline route publishes with
   > `seq=None`, which the client tail consumer drops (`if (ev.seq == null)
   > return;`). So declines don't currently live-update either. We do not fix
   > that here; we sidestep it by giving the new events a non-null `seq`.

## Frontend changes

1. **`ResponderStatusEntry` TS type** (`web/src/lib/api/types.ts`) — add
   `started_at: string | null`; widen `status` union to
   `'queued' | 'working' | 'replied' | 'declined' | 'failed'` (drop `'pending'`).

2. **`ResponderStatusStrip`** — add `queued` and `working` cases to `statusLabel`
   / `statusClass`. `working` renders a spinner glyph + live elapsed
   (`working 45s`), formatted from `started_at`.

3. **`ThreadActivityFooter`** (new component) — pinned below the transcript in
   `ThreadsPage`. Derives the set of currently-`working` responders across the
   thread's messages; renders one line listing them with elapsed
   (`● alice is working on a reply… (45s)`; multiple → `● alice, carol working…`).
   Renders nothing when no responder is `working`.

4. **Elapsed ticker** — a single `setInterval(1s)` in `ThreadsPage` (or a small
   `useElapsedTick` hook) that forces a re-render once per second **only while ≥1
   `working` entry exists**, and clears when none do. Drives both the strip timers
   and the footer. No per-entry timers.

5. **SSE handling — no consumer change required.** The existing
   `useThreadTailSSE` (`web/src/design-system/providers/_real-threads.ts`)
   already invalidates `['thread-messages', slug, threadId]` for any seq-bearing
   non-message event (the `else` branch). Because the new events carry
   `seq=<triggering_seq>` and have no `body_markdown`, they hit that branch and
   trigger a refetch of the messages (which embed `responder_status`). A provider
   test locks this behavior so a future consumer refactor can't silently break
   the working indicator.

## Error handling / edge cases

- **Clock skew:** client-computed elapsed can show a small negative or inflated
  value if the browser clock differs from the server. Clamp displayed elapsed to
  `>= 0`. Acceptable for a localhost single-machine deployment.
- **Missed `invocation_started` event** (UI not yet subscribed): the next refetch
  (any later event, or the SSE replay-on-subscribe) reconciles from
  `responder_status`, which is the source of truth. Events are an optimization;
  the projection is canonical.
- **`failed`/`timeout` collapse:** both render `failed`. The distinction is still
  in the invocation row + audit for forensics; the strip doesn't need it.
- **Stuck subprocess:** out of scope — elapsed keeps counting up; the founder reads
  the number. No auto-warning (per scope decision).

## Testing

**Backend**
- `list_invocations_for_thread_grouped_by_seq` returns `started_at` per entry.
- Route projection: `pending` + `started_at` set → `working`; `pending` + null →
  `queued`; `consumed` → `replied`; `declined`/`failed`/`timeout` unchanged.
- `started_at` present in each responder entry on the messages projection.
- `run_invocation` publishes `invocation_started` after stamping and
  `invocation_settled` on the fail/timeout tail; both are guarded no-ops when
  `org_state` has no `event_bus` (the existing `FakeOrgState` test path).

**Frontend**
- `ResponderStatusStrip`: renders `queued`, and `working 45s` given a
  `started_at` ~45s in the past.
- `ThreadActivityFooter`: shows working participants with elapsed; renders nothing
  when none are working.
- SSE `invocation_started`/`invocation_settled` event triggers a thread-detail
  refetch (mocked).

## Contract pinning (per CLAUDE.md "Web UI")

- Regenerate the OpenAPI snapshot for the new `started_at` field + widened status
  enum: `HAPPYRANCH_REGEN_OPENAPI=1 uv run pytest tests/contract/test_openapi_snapshot.py`.
- Update the TS `ResponderStatusEntry` mirror in `web/src/lib/api/`. No new path →
  `web/src/test/openapi-coverage.test.ts` `INCLUDED_PATHS` unchanged.

## Files touched

- `src/infrastructure/database.py` — `started_at` in the grouped query.
- `src/models.py` — `ResponderStatusEntry.started_at` + widened status enum.
- `src/daemon/routes/threads.py` — projection split (`pending`→`queued`/`working`) + `started_at` passthrough.
- `src/daemon/thread_runner.py` — `invocation_started` + tail `invocation_settled` publishes.
- `web/src/lib/api/types.ts` — `started_at` field + status union.
- `web/src/features/threads/ResponderStatusStrip.tsx` — `queued`/`working` rendering + elapsed.
- `web/src/features/threads/ThreadActivityFooter.tsx` — new.
- `web/src/features/threads/ThreadsPage.tsx` — footer mount + elapsed ticker.
- `web/src/design-system/providers/_real-threads.test.ts` (or existing provider test) — lock the invalidate-on-invocation-event behavior.
- Tests alongside each. No client SSE consumer code change.
