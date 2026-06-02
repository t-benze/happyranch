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

4. **SSE events on the existing `thread_topic(thread_id)`** — payload shape:
   `{"type": "invocation_started" | "invocation_settled", "thread_id": ...,
   "agent_name": ..., "triggering_seq": int, "status": <wire status>}`.
   Publish sites:
   - **`invocation_started`** — in `run_invocation`
     (`src/daemon/thread_runner.py`) immediately after
     `stamp_invocation_started`. Guarded: only publish when
     `getattr(org_state, "event_bus", None)` is present and the runner has the
     main asyncio loop available (it is `async def`, so `await
     org_state.event_bus.publish(...)` is direct). Test `FakeOrgState` has no
     `event_bus` → guard makes it a silent no-op.
   - **`invocation_settled`** — two sites:
     - The **decline route** (`POST /threads/{id}/decline`) — currently the only
       transition with no SSE publish. Add a publish after `mark_invocation_declined`.
     - `run_invocation`'s auto-decline / fail / timeout tail (after
       `fail_invocation`), guarded the same way.
   - The **reply route** already publishes a new-message thread event, which the
     UI already refetches on; that refetch surfaces `replied`. No new publish on
     reply.

   `invocation_started` / `invocation_settled` are NOT in `_TERMINAL_TYPES`, so
   they don't close the SSE stream.

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

5. **SSE handling** — the existing thread-tail SSE consumer
   (`useThreadTailSSE`) invalidates / refetches the thread-detail query on
   `invocation_started` and `invocation_settled` event types (it already refetches
   on message events).

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
- `started_at` present in each responder entry on `GET /threads/{id}`.
- Decline route publishes an `invocation_settled` event.
- `run_invocation` publishes `invocation_started` after stamping; guarded no-op
  when `org_state` has no `event_bus` (the existing `FakeOrgState` test path).

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
- `src/models.py` — `ResponderStatusEntry.started_at`.
- `src/daemon/routes/threads.py` — projection split; decline-route publish.
- `src/daemon/thread_runner.py` — `invocation_started` + tail `invocation_settled` publishes.
- `web/src/lib/api/types.ts` — type + status union.
- `web/src/features/threads/ResponderStatusStrip.tsx` — `queued`/`working` rendering.
- `web/src/features/threads/ThreadActivityFooter.tsx` — new.
- `web/src/features/threads/ThreadsPage.tsx` — footer mount + elapsed ticker + SSE invalidate wiring.
- Tests alongside each.
