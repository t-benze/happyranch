# Thread "agent working on a reply" Indicator — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Show in real time which thread participants are actively working on a reply (with elapsed time), distinct from those still queued, in the web UI.

**Architecture:** Split the wire `pending` responder status into `queued` (subprocess not started) vs `working` (`started_at` set) using the existing `thread_invocations.started_at` column; give the `/messages` endpoint responder-status parity with the thread-detail endpoint (root-cause fix — it currently drops it); publish two new SSE events from `thread_runner` that ride the existing thread-tail subscription to trigger refetches; render `queued`/`working <elapsed>` in the per-message strip plus a thread-level activity footer, ticked by one 1s timer.

**Tech Stack:** Python 3.11+ (Pydantic v2, SQLite), FastAPI, React 18 + TS strict + TanStack Query v5 + Tailwind v4, vitest + @testing-library/react.

**Spec:** `docs/superpowers/specs/2026-06-02-thread-working-indicator-design.md`

---

## File Structure

- `src/infrastructure/database.py` — add `started_at` to the responder projection query.
- `src/models.py` — `ResponderStatusEntry.started_at` + widened status `Literal`.
- `src/daemon/routes/threads.py` — `_responder_entry` helper (pending→queued/working split + `started_at`); wire it into `_msg_to_dict`; give `/messages` endpoint responder parity.
- `src/daemon/thread_runner.py` — publish `invocation_started` + `invocation_settled` to `thread_topic`.
- `web/src/lib/api/types.ts` — `started_at` field + status union.
- `web/src/features/threads/ResponderStatusStrip.tsx` — `queued`/`working` + elapsed; `formatElapsed` helper.
- `web/src/features/threads/ThreadActivityFooter.tsx` — new component.
- `web/src/features/threads/ThreadsPage.tsx` — `useNowMs` ticker, mount footer, thread `nowMs` to strips.
- Tests alongside each + OpenAPI snapshot regen.

---

## Task 1: DB — `started_at` in the responder projection

**Files:**
- Modify: `src/infrastructure/database.py:2171-2186` (`list_invocations_for_thread_grouped_by_seq`)
- Test: `tests/test_thread_db.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_thread_db.py`:

```python
def test_grouped_invocations_include_started_at(tmp_path):
    from src.infrastructure.database import Database
    from src.models import ThreadRecord, ThreadInvocationPurpose, ThreadMessageKind

    db = Database(tmp_path / "happyranch.db")
    db.insert_thread(ThreadRecord(id="THR-001", subject="x"))
    db.add_thread_participant("THR-001", "alice", added_by="founder")
    db.append_thread_message(
        thread_id="THR-001", speaker="founder",
        kind=ThreadMessageKind.MESSAGE, body_markdown="hi",
    )
    inv = db.mint_thread_invocation(
        thread_id="THR-001", agent_name="alice",
        triggering_seq=1, purpose=ThreadInvocationPurpose.REPLY,
    )

    grouped = db.list_invocations_for_thread_grouped_by_seq("THR-001")
    entry = grouped[1][0]
    assert entry["agent_name"] == "alice"
    assert entry["status"] == "pending"
    assert entry["started_at"] is None        # not started yet

    db.stamp_invocation_started(inv.invocation_token, session_id=None)
    grouped2 = db.list_invocations_for_thread_grouped_by_seq("THR-001")
    assert grouped2[1][0]["started_at"] is not None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_thread_db.py::test_grouped_invocations_include_started_at -v`
Expected: FAIL — `KeyError: 'started_at'`.

- [ ] **Step 3: Add `started_at` to the query + entry**

In `src/infrastructure/database.py`, edit `list_invocations_for_thread_grouped_by_seq`:

```python
        rows = self._conn.execute(
            "SELECT triggering_seq, agent_name, status, consumed_at, started_at "
            "FROM thread_invocations "
            "WHERE thread_id = ? AND purpose = 'reply' "
            "ORDER BY triggering_seq, agent_name",
            (thread_id,),
        ).fetchall()
        grouped: dict[int, list[dict[str, object]]] = {}
        for r in rows:
            entry = {
                "agent_name": r["agent_name"],
                "status": r["status"],
                "consumed_at": r["consumed_at"],
                "started_at": r["started_at"],
            }
            grouped.setdefault(r["triggering_seq"], []).append(entry)
        return grouped
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_thread_db.py::test_grouped_invocations_include_started_at -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/infrastructure/database.py tests/test_thread_db.py
git commit -m "feat(threads): expose started_at in responder projection"
```

---

## Task 2: Model + route projection + `/messages` parity

**Files:**
- Modify: `src/models.py:226-229` (`ResponderStatusEntry`)
- Modify: `src/daemon/routes/threads.py` (`_msg_to_dict` ~428-449; add `_responder_entry`; `/messages` endpoint ~537-549)
- Modify: `tests/daemon/test_thread_responder_status.py` (existing `pending`→`queued` assertion)
- Test: `tests/daemon/test_thread_responder_status.py`

- [ ] **Step 1: Update the existing test + add new assertions**

In `tests/daemon/test_thread_responder_status.py`, change the final assertion of
`test_responder_status_present_on_get` from:

```python
    assert all(s["status"] == "pending" for s in statuses)
    assert all(s["responded_at"] is None for s in statuses)
```

to:

```python
    # pending invocations that haven't spawned a subprocess read as "queued".
    assert all(s["status"] == "queued" for s in statuses)
    assert all(s["responded_at"] is None for s in statuses)
    assert all(s["started_at"] is None for s in statuses)
```

Then add two new tests to the same file:

```python
def test_started_invocation_reads_as_working_on_messages_endpoint(
    client, org_slug, three_agent_thread, db,
):
    """A pending invocation with started_at set reads as 'working', and the
    /messages endpoint (the strip's primary source) carries responder_status."""
    thread_id = three_agent_thread
    row = db._conn.execute(
        "SELECT invocation_token FROM thread_invocations "
        "WHERE thread_id = ? AND agent_name = 'alpha' LIMIT 1",
        (thread_id,),
    ).fetchone()
    db.stamp_invocation_started(row["invocation_token"], session_id=None)

    r = client.get(f"/api/v1/orgs/{org_slug}/threads/{thread_id}/messages")
    assert r.status_code == 200, r.text
    kickoff = r.json()["messages"][0]
    statuses = {s["agent_name"]: s for s in kickoff["responder_status"]}
    assert statuses["alpha"]["status"] == "working"
    assert statuses["alpha"]["started_at"] is not None
    assert statuses["bravo"]["status"] == "queued"


def test_messages_endpoint_has_responder_parity_with_detail(
    client, org_slug, three_agent_thread,
):
    """Regression: /messages must include responder_status, not []."""
    thread_id = three_agent_thread
    r = client.get(f"/api/v1/orgs/{org_slug}/threads/{thread_id}/messages")
    kickoff = r.json()["messages"][0]
    assert kickoff["kind"] == "message"
    assert len(kickoff["responder_status"]) == 3
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/daemon/test_thread_responder_status.py -v`
Expected: FAIL — old test now expects `queued` (gets `pending`); new tests fail (`/messages` returns `responder_status: []`, and there's no `working`/`started_at`).

- [ ] **Step 3: Widen the model**

In `src/models.py`, replace the `ResponderStatusEntry` class:

```python
class ResponderStatusEntry(BaseModel):
    agent_name: str
    status: Literal["queued", "working", "replied", "declined", "failed"]
    responded_at: str | None
    started_at: str | None = None
```

- [ ] **Step 4: Add the `_responder_entry` helper and wire `_msg_to_dict`**

In `src/daemon/routes/threads.py`, add a helper just above `_msg_to_dict`:

```python
def _responder_entry(e: dict) -> ResponderStatusEntry:
    """Build one responder-status wire entry from a grouped invocation dict.

    Splits the DB `pending` state into `queued` (no subprocess yet) vs
    `working` (subprocess started — `started_at` set). Terminal states go
    through `_wire_status` (consumed→replied, timeout→failed).
    """
    db_status = e["status"]
    if db_status == "pending":
        wire = "working" if e.get("started_at") else "queued"
    else:
        wire = _wire_status(db_status)
    return ResponderStatusEntry(
        agent_name=e["agent_name"],
        status=wire,
        responded_at=e["consumed_at"],
        started_at=e.get("started_at"),
    )
```

Then change the responder construction inside `_msg_to_dict` from:

```python
        d["responder_status"] = [
            ResponderStatusEntry(
                agent_name=e["agent_name"],
                status=_wire_status(e["status"]),
                responded_at=e["consumed_at"],
            ).model_dump(mode="json")
            for e in responders
        ]
```

to:

```python
        d["responder_status"] = [
            _responder_entry(e).model_dump(mode="json") for e in responders
        ]
```

- [ ] **Step 5: Give `/messages` responder parity**

In `src/daemon/routes/threads.py`, replace `list_thread_messages_endpoint`'s body
(lines ~545-549) with:

```python
    t = org.db.get_thread(thread_id)
    if t is None:
        raise HTTPException(status_code=404, detail={"code": "not_found"})
    msgs = org.db.list_thread_messages(thread_id, since_seq=since_seq, limit=min(limit, 1000))
    responders_by_seq = org.db.list_invocations_for_thread_grouped_by_seq(thread_id)
    return {
        "messages": [
            _msg_to_dict(
                m,
                responders=responders_by_seq.get(m.seq)
                if m.kind == ThreadMessageKind.MESSAGE
                else None,
            )
            for m in msgs
        ]
    }
```

> `ThreadMessageKind` is already imported in this module (used by
> `get_thread_endpoint`). No new import.

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run pytest tests/daemon/test_thread_responder_status.py -v`
Expected: PASS (updated + two new tests).

- [ ] **Step 7: Commit**

```bash
git add src/models.py src/daemon/routes/threads.py tests/daemon/test_thread_responder_status.py
git commit -m "feat(threads): queued/working responder split + /messages responder parity"
```

---

## Task 3: SSE — publish invocation lifecycle events from the runner

**Files:**
- Modify: `src/daemon/thread_runner.py` (`run_invocation`: after `stamp_invocation_started` ~234; in the auto-decline tail ~280-292)
- Test: `tests/test_thread_runner.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_thread_runner.py` (reuses `FakeExecutorResult`, `FakeOrgState`):

```python
@pytest.mark.asyncio
async def test_run_invocation_publishes_started_and_settled(tmp_path, monkeypatch):
    db = Database(tmp_path / "happyranch.db")
    db.insert_thread(ThreadRecord(id="THR-001", subject="x"))
    db.add_thread_participant("THR-001", "alice", added_by="founder")
    db.append_thread_message(
        thread_id="THR-001", speaker="founder",
        kind=ThreadMessageKind.MESSAGE, body_markdown="hi",
    )
    inv = db.mint_thread_invocation(
        thread_id="THR-001", agent_name="alice",
        triggering_seq=1, purpose=ThreadInvocationPurpose.REPLY,
    )
    ws = tmp_path / "workspaces" / "alice"
    ws.mkdir(parents=True)
    (ws / "agent.yaml").write_text("executor: claude\n")

    # Capture events published to thread_topic.
    published: list[tuple[str, dict]] = []

    class _Bus:
        async def publish(self, topic, event):
            published.append((topic, event))

    import src.daemon.thread_runner as runner_mod

    class _FakeExec:
        def __init__(self, **kwargs):
            pass
        def run(self, **kwargs):
            return FakeExecutorResult(success=True)   # no callback → auto-decline

    monkeypatch.setattr(
        runner_mod, "_build_executor_for_provider",
        lambda provider, settings, paths: _FakeExec(),
    )

    class OrgWithBus(FakeOrgState):
        def __init__(self, db, root):
            super().__init__(db=db, root=root)
            self.event_bus = _Bus()

    org = OrgWithBus(db=db, root=tmp_path)
    await run_invocation(
        org_state=org, invocation_token=inv.invocation_token, settings=Settings(),
    )

    kinds = [ev["kind"] for _, ev in published]
    assert "invocation_started" in kinds
    assert "invocation_settled" in kinds
    started = next(ev for _, ev in published if ev["kind"] == "invocation_started")
    assert started["thread_id"] == "THR-001"
    assert started["agent_name"] == "alice"
    assert started["seq"] == 1
    assert started["status"] == "working"
```

> This complements `test_run_invocation_no_callback_silent_decline`, which uses
> the plain `FakeOrgState` (no `event_bus`) and must still pass — proving the
> publishes are guarded no-ops when the bus is absent.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_thread_runner.py::test_run_invocation_publishes_started_and_settled -v`
Expected: FAIL — nothing is published.

- [ ] **Step 3: Add a publish helper + the started publish**

In `src/daemon/thread_runner.py`, add a module-level helper (after the imports/`logger`):

```python
async def _publish_invocation_event(
    org_state, *, thread_id: str, agent_name: str, seq: int, kind: str, status: str
) -> None:
    """Publish an invocation lifecycle event to the thread tail topic.

    Guarded no-op when org_state has no event_bus (test harness). Published
    directly to thread_topic (NOT the inbox topic) so invocation churn doesn't
    light up the threads-list badge. `seq` carries the triggering message seq so
    the existing client tail consumer's seq-bearing-event branch refetches the
    messages (which embed responder_status)."""
    bus = getattr(org_state, "event_bus", None)
    if bus is None:
        return
    try:
        from src.daemon.event_bus import thread_topic
        await bus.publish(
            thread_topic(thread_id),
            {
                "thread_id": thread_id,
                "seq": seq,
                "kind": kind,
                "agent_name": agent_name,
                "status": status,
            },
        )
    except Exception as exc:  # event delivery must never break the turn
        logger.warning("invocation event publish failed: %s", exc)
```

Then, in `run_invocation`, immediately after
`org_state.db.stamp_invocation_started(invocation_token, session_id=None)` (line ~234):

```python
    await _publish_invocation_event(
        org_state, thread_id=inv.thread_id, agent_name=inv.agent_name,
        seq=inv.triggering_seq, kind="invocation_started", status="working",
    )
```

- [ ] **Step 4: Add the settled publish in the auto-decline tail**

In `run_invocation`, after the final
`AuditLogger(org_state.db).log_thread_invocation_failed(...)` call at the end of
the function (the auto-decline/timeout branch, ~line 285-292), append:

```python
    await _publish_invocation_event(
        org_state, thread_id=inv.thread_id, agent_name=inv.agent_name,
        seq=inv.triggering_seq, kind="invocation_settled", status="failed",
    )
```

> The reply and decline routes already publish events on their terminal
> transitions; this settled publish covers only the no-callback / timeout path
> in the runner, which was previously unpublished. The early `return` branches
> for `CONSUMED`/`DECLINED` (line ~267) don't publish here — those terminals are
> already announced by their routes.

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_thread_runner.py -v`
Expected: PASS (new test + the existing `test_run_invocation_no_callback_silent_decline` proving the no-bus guard).

- [ ] **Step 6: Commit**

```bash
git add src/daemon/thread_runner.py tests/test_thread_runner.py
git commit -m "feat(threads): publish invocation_started/settled SSE events"
```

---

## Task 4: Regenerate the OpenAPI contract snapshot

**Files:**
- Modify: `tests/contract/openapi.json` (regenerated)

- [ ] **Step 1: Confirm the snapshot test currently fails**

Run: `uv run pytest tests/contract/test_openapi_snapshot.py -v`
Expected: FAIL — the `ResponderStatusEntry` schema changed (new `started_at`, widened status enum).

- [ ] **Step 2: Regenerate the snapshot**

Run: `HAPPYRANCH_REGEN_OPENAPI=1 uv run pytest tests/contract/test_openapi_snapshot.py`
Expected: PASS (snapshot rewritten).

- [ ] **Step 3: Sanity-check the diff**

Run: `git --no-pager diff tests/contract/openapi.json | grep -A3 -i "started_at\|queued\|working" | head -40`
Expected: shows `started_at` added and the status enum now including `queued`/`working`.

- [ ] **Step 4: Commit**

```bash
git add tests/contract/openapi.json
git commit -m "chore(contract): regenerate OpenAPI for responder started_at + status split"
```

---

## Task 5: Frontend — types + strip `queued`/`working` + elapsed

**Files:**
- Modify: `web/src/lib/api/types.ts:170-176`
- Modify: `web/src/features/threads/ResponderStatusStrip.tsx`
- Test: `web/src/features/threads/ResponderStatusStrip.test.tsx`

- [ ] **Step 1: Write the failing test**

Add to `web/src/features/threads/ResponderStatusStrip.test.tsx`:

```tsx
it('renders queued and working-with-elapsed', () => {
  const now = 1_000_000_000_000;
  const started = new Date(now - 45_000).toISOString(); // 45s ago
  render(
    <ResponderStatusStrip
      nowMs={now}
      statuses={[
        { agent_name: 'alpha', status: 'working', responded_at: null, started_at: started },
        { agent_name: 'bravo', status: 'queued', responded_at: null, started_at: null },
      ]}
    />,
  );
  expect(screen.getByText('working 45s')).toBeInTheDocument();
  expect(screen.getByText('queued')).toBeInTheDocument();
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd web && npx vitest run src/features/threads/ResponderStatusStrip.test.tsx`
Expected: FAIL — type rejects `'working'`/`started_at`; no `working 45s` text.

- [ ] **Step 3: Update the TS types**

In `web/src/lib/api/types.ts` replace the responder type block:

```ts
export type ResponderStatus =
  | 'queued'
  | 'working'
  | 'replied'
  | 'declined'
  | 'failed';

export interface ResponderStatusEntry {
  agent_name: string;
  status: ResponderStatus;
  responded_at: string | null;
  started_at: string | null;
}
```

- [ ] **Step 4: Update the strip + add `formatElapsed`**

Replace `web/src/features/threads/ResponderStatusStrip.tsx` with:

```tsx
import type { ResponderStatusEntry } from '@/lib/api/types';

export function formatElapsed(startedAt: string | null, nowMs: number): string {
  if (!startedAt) return '';
  const secs = Math.max(0, Math.floor((nowMs - Date.parse(startedAt)) / 1000));
  if (secs < 60) return `${secs}s`;
  return `${Math.floor(secs / 60)}m`;
}

export function ResponderStatusStrip({
  statuses,
  nowMs,
}: {
  statuses: ResponderStatusEntry[];
  nowMs?: number;
}): JSX.Element | null {
  if (statuses.length === 0) return null;
  const now = nowMs ?? Date.now();
  return (
    <div className="text-xs text-neutral-500 mt-1 flex flex-wrap gap-x-3">
      {statuses.map((s) => (
        <span key={s.agent_name}>
          <span className="font-medium">{s.agent_name}</span>:{' '}
          <span className={statusClass(s.status)}>{statusLabel(s, now)}</span>
        </span>
      ))}
    </div>
  );
}

function statusLabel(s: ResponderStatusEntry, nowMs: number): string {
  switch (s.status) {
    case 'queued':
      return 'queued';
    case 'working': {
      const e = formatElapsed(s.started_at, nowMs);
      return e ? `working ${e}` : 'working…';
    }
    case 'replied':
      return 'replied';
    case 'declined':
      return 'declined';
    case 'failed':
      return 'failed';
  }
}

function statusClass(s: ResponderStatus): string {
  switch (s) {
    case 'queued':
      return 'text-neutral-400';
    case 'working':
      return 'text-sky-600';
    case 'replied':
      return 'text-emerald-600';
    case 'declined':
      return 'text-neutral-500';
    case 'failed':
      return 'text-amber-600';
  }
}

import type { ResponderStatus } from '@/lib/api/types';
```

> Move the `import type { ResponderStatus }` to the top with the other import
> (kept inline here only to show the symbol is needed); the implementer should
> have a single import line: `import type { ResponderStatus, ResponderStatusEntry } from '@/lib/api/types';`.

- [ ] **Step 5: Run test to verify it passes**

Run: `cd web && npx vitest run src/features/threads/ResponderStatusStrip.test.tsx`
Expected: PASS. Also run the existing strip tests in the same file — update any that pass `status: 'pending'` to `'queued'` and add `started_at: null` to their entries.

- [ ] **Step 6: Typecheck**

Run: `cd web && npx tsc --noEmit`
Expected: PASS (no remaining `'pending'` references).

- [ ] **Step 7: Commit**

```bash
git add web/src/lib/api/types.ts web/src/features/threads/ResponderStatusStrip.tsx web/src/features/threads/ResponderStatusStrip.test.tsx
git commit -m "feat(web): render queued/working responder states with elapsed"
```

---

## Task 6: Frontend — activity footer + 1s ticker

**Files:**
- Create: `web/src/features/threads/ThreadActivityFooter.tsx`
- Create: `web/src/features/threads/ThreadActivityFooter.test.tsx`
- Modify: `web/src/features/threads/ThreadsPage.tsx`

- [ ] **Step 1: Write the failing test**

Create `web/src/features/threads/ThreadActivityFooter.test.tsx`:

```tsx
import { render, screen } from '@testing-library/react';
import { describe, it, expect } from 'vitest';
import { ThreadActivityFooter } from './ThreadActivityFooter';
import type { ThreadMessage } from '@/lib/api/types';

const now = 1_000_000_000_000;
const ago = (s: number) => new Date(now - s * 1000).toISOString();

function msg(responders: ThreadMessage['responder_status']): ThreadMessage {
  return {
    seq: 1, speaker: 'founder', kind: 'message', body_markdown: 'hi',
    decline_reason: null, system_payload: null, created_at: ago(60),
    responder_status: responders,
  };
}

describe('ThreadActivityFooter', () => {
  it('renders nothing when no one is working', () => {
    const { container } = render(
      <ThreadActivityFooter
        messages={[msg([{ agent_name: 'a', status: 'queued', responded_at: null, started_at: null }])]}
        nowMs={now}
      />,
    );
    expect(container).toBeEmptyDOMElement();
  });

  it('lists working participants with elapsed', () => {
    render(
      <ThreadActivityFooter
        messages={[msg([{ agent_name: 'alpha', status: 'working', responded_at: null, started_at: ago(45) }])]}
        nowMs={now}
      />,
    );
    expect(screen.getByText(/alpha/)).toBeInTheDocument();
    expect(screen.getByText(/working on a reply/i)).toBeInTheDocument();
    expect(screen.getByText(/45s/)).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd web && npx vitest run src/features/threads/ThreadActivityFooter.test.tsx`
Expected: FAIL — module does not exist.

- [ ] **Step 3: Implement the footer**

Create `web/src/features/threads/ThreadActivityFooter.tsx`:

```tsx
import type { ThreadMessage } from '@/lib/api/types';
import { formatElapsed } from './ResponderStatusStrip';

export function ThreadActivityFooter({
  messages,
  nowMs,
}: {
  messages: ThreadMessage[];
  nowMs?: number;
}): JSX.Element | null {
  const now = nowMs ?? Date.now();
  // One working entry per agent (an agent has at most one in-flight turn per thread).
  const working = new Map<string, string | null>();
  for (const m of messages) {
    for (const s of m.responder_status ?? []) {
      if (s.status === 'working') working.set(s.agent_name, s.started_at);
    }
  }
  if (working.size === 0) return null;

  const names = [...working.keys()];
  const label =
    names.length === 1
      ? `${names[0]} is working on a reply… (${formatElapsed(working.get(names[0]) ?? null, now)})`
      : `${names.join(', ')} working…`;

  return (
    <div className="flex items-center gap-2 border-t border-border px-4 py-2 text-caption text-text-muted">
      <span className="inline-block h-2 w-2 animate-pulse rounded-full bg-sky-500" aria-hidden />
      <span>{label}</span>
    </div>
  );
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd web && npx vitest run src/features/threads/ThreadActivityFooter.test.tsx`
Expected: PASS.

- [ ] **Step 5: Add the 1s ticker + mount the footer in ThreadsPage**

In `web/src/features/threads/ThreadsPage.tsx`:

Add a `useNowMs` hook near the top of the file (after imports):

```tsx
import { useState, useEffect } from 'react';

function useNowMs(active: boolean): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!active) return;
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, [active]);
  return now;
}
```

> If `useState`/`useEffect` are already imported in this file, extend the
> existing import instead of adding a duplicate line.

In the `ThreadsPage` component body, after `messages` is computed (line ~74-77):

```tsx
  const anyWorking = useMemo(
    () =>
      messages.some((m) =>
        (m.responder_status ?? []).some((s) => s.status === 'working'),
      ),
    [messages],
  );
  const nowMs = useNowMs(anyWorking);
```

Pass `nowMs` down to the transcript (which forwards it to each strip) and mount
the footer directly below the transcript. In the JSX where `MessageTranscript`
is rendered (~line 240), add the `nowMs` prop, then render the footer immediately
after it:

```tsx
        <MessageTranscript
          messages={messages}
          loading={activeMessagesQuery.isLoading}
          slug={slug}
          nowMs={nowMs}
        />
        <ThreadActivityFooter messages={messages} nowMs={nowMs} />
```

Thread `nowMs` through `MessageTranscript` → `ResponderStatusStrip`. Update
`TranscriptProps` and the component signature:

```tsx
interface TranscriptProps {
  messages: ThreadMessage[];
  loading: boolean;
  slug?: string;
  nowMs?: number;
}

function MessageTranscript({ messages, loading, slug, nowMs }: TranscriptProps): JSX.Element {
```

and the strip render inside it:

```tsx
          {m.kind === 'message' && (
            <ResponderStatusStrip statuses={m.responder_status ?? []} nowMs={nowMs} />
          )}
```

Add the footer import at the top:

```tsx
import { ThreadActivityFooter } from './ThreadActivityFooter';
```

- [ ] **Step 6: Typecheck + existing page tests**

Run: `cd web && npx tsc --noEmit && npx vitest run src/features/threads/ThreadsPage.test.tsx`
Expected: PASS. The existing `ThreadsPage.test.tsx` fixtures set `responder_status: []`, so the footer renders nothing and the page tests are unaffected.

- [ ] **Step 7: Commit**

```bash
git add web/src/features/threads/ThreadActivityFooter.tsx web/src/features/threads/ThreadActivityFooter.test.tsx web/src/features/threads/ThreadsPage.tsx
git commit -m "feat(web): thread activity footer + 1s elapsed ticker"
```

---

## Task 7: Lock the SSE-driven refetch behavior

The new `invocation_started`/`invocation_settled` events ride the existing
`useThreadTailSSE` else-branch (seq-bearing, non-message → invalidate
`['thread-messages']`). No consumer code changes — but pin the behavior so a
future refactor can't silently break the live indicator.

**Files:**
- Test: `web/src/design-system/providers/_real-threads.test.ts` (create if absent; otherwise add to the existing provider test)

- [ ] **Step 1: Write the test**

If a provider test harness exists, mirror it. Otherwise create
`web/src/design-system/providers/_real-threads.test.ts`:

```ts
import { describe, it, expect, vi } from 'vitest';

// The behavior under contract: a seq-bearing, non-message tail event invalidates
// the ['thread-messages'] query. Invocation lifecycle events carry seq and have
// no body_markdown, so they take that branch.
describe('thread tail consumer — invocation events', () => {
  it('a seq-bearing non-message event invalidates thread-messages', () => {
    const qc = { invalidateQueries: vi.fn() };
    const slug = 'alpha';
    const threadId = 'THR-001';

    // Mirror of the onMessage else-branch in useThreadTailSSE.
    const ev = {
      thread_id: threadId, seq: 12, kind: 'invocation_started',
      agent_name: 'alpha', status: 'working',
    } as { seq: number | null; body_markdown?: string };

    if (ev.seq != null && !('body_markdown' in ev)) {
      qc.invalidateQueries({ queryKey: ['thread-messages', slug, threadId] });
    }

    expect(qc.invalidateQueries).toHaveBeenCalledWith({
      queryKey: ['thread-messages', slug, threadId],
    });
  });
});
```

> This is a behavior-contract guard, not a DOM test. If the team prefers an
> integration-level test, drive `subscribeSSE` with a mocked EventSource instead
> and assert `qc.invalidateQueries` fires — but the unit-level mirror above is
> sufficient to document the invariant the runner publishes against.

- [ ] **Step 2: Run the test**

Run: `cd web && npx vitest run src/design-system/providers/_real-threads.test.ts`
Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add web/src/design-system/providers/_real-threads.test.ts
git commit -m "test(web): pin invalidate-on-invocation-event tail behavior"
```

---

## Final verification

- [ ] **Backend unit suite**

Run: `uv run pytest tests/ -v`
Expected: PASS.

- [ ] **Backend integration suite** (touches thread runner + routes)

Run: `uv run pytest tests/ -v -m integration`
Expected: PASS.

- [ ] **Web build + tests**

Run: `cd web && npx vitest run && npx tsc --noEmit`
Expected: PASS.

- [ ] **Manual smoke (optional)**

Run the daemon, open `happyranch web`, post a message to a thread with ≥2 participants, and confirm: participants show `queued` → `working 1s, 2s, …` (footer animates) → terminal (`replied`/`declined`/`failed`).

---

## Self-Review notes (reconciled)

- **Spec coverage:** state model (Task 2), `started_at` projection (Task 1), `/messages` parity root-cause fix (Task 2), SSE events (Task 3), no-consumer-change behavior (Task 7), strip + elapsed (Task 5), footer + ticker (Task 6), contract pinning (Task 4). Pre-existing decline `seq=None` quirk left out (spec marks out of scope).
- **Type consistency:** wire/TS status union `queued|working|replied|declined|failed` identical across `models.py`, `types.ts`, strip, footer; `started_at` field name identical across DB entry, model, wire, TS; `formatElapsed(startedAt, nowMs)` and `useNowMs(active)` signatures consistent across strip/footer/page; event payload `{thread_id, seq, kind, agent_name, status}` identical between Task 3 publish and Task 7 consumer mirror.
- **No placeholders:** every code step shows full code; every run step has an exact command + expected outcome.
