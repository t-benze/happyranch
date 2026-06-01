# Thread Close-Out Removal + Resume Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove the close-out ritual entirely, collapse `archiving`/`abandoned` into `archived`, make archive synchronous, and add a founder `/resume` endpoint so archive becomes an organisational state rather than a terminal.

**Architecture:** Six implementation phases ordered so each phase is mergeable on its own and tests stay green throughout. Phase 1 is purely additive (resume). Phases 2–5 are destructive. Phase 6 is a one-shot SQL sweep against the local runtime DBs and doc cleanup.

**Tech Stack:** Python 3.13 + FastAPI + Pydantic v2 + SQLite (per-org DBs), TypeScript + React 18 + TanStack Query v5, pytest + Vitest.

**Spec:** `docs/superpowers/specs/2026-06-01-thread-close-out-removal-and-resume-design.md`.

---

## Phase 1 — Resume (additive, low risk)

### Task 1: Resume DB method + audit writer

**Files:**
- Modify: `src/infrastructure/database.py:2267-2292` (the `set_thread_status` body)
- Modify: `src/infrastructure/audit_logger.py` after the `log_thread_archived` method (line 842 region)
- Test: `tests/test_thread_db.py` (append new test)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_thread_db.py`:

```python
def test_set_thread_status_to_open_resumes_archived_thread(tmp_path):
    db = Database(tmp_path / "happyranch.db")
    db.start_thread(thread_id="THR-100", subject="test", started_at=datetime.now(timezone.utc))
    db.set_thread_status("THR-100", status=ThreadStatus.ARCHIVED, summary="done")
    pre = db.get_thread("THR-100")
    assert pre.status is ThreadStatus.ARCHIVED
    assert pre.archived_at is not None
    pre_archived_at = pre.archived_at

    db.set_thread_status("THR-100", status=ThreadStatus.OPEN)

    post = db.get_thread("THR-100")
    assert post.status is ThreadStatus.OPEN
    # archived_at left intact as historical record
    assert post.archived_at == pre_archived_at
    assert post.summary == "done"


def test_log_thread_resumed_writes_audit_row(tmp_path):
    db = Database(tmp_path / "happyranch.db")
    AuditLogger(db).log_thread_resumed("THR-100", prior_archived_at="2026-05-30T12:00:00+00:00")
    rows = db.get_audit_logs("THR-100")
    assert any(r.action == "thread_resumed" for r in rows)
```

- [ ] **Step 2: Run to confirm failure**

```bash
uv run pytest tests/test_thread_db.py::test_set_thread_status_to_open_resumes_archived_thread tests/test_thread_db.py::test_log_thread_resumed_writes_audit_row -v
```
Expected: FAIL (`log_thread_resumed` undefined; `set_thread_status` with `OPEN` may clobber `archived_at` via the `else` branch).

- [ ] **Step 3: Implement**

In `src/infrastructure/database.py`, rewrite the `set_thread_status` body (currently lines 2267-2292):

```python
@_synchronized
def set_thread_status(
    self,
    thread_id: str,
    *,
    status: ThreadStatus,
    summary: str | None = None,
) -> None:
    now = _now().isoformat()
    if status is ThreadStatus.ARCHIVED:
        self._conn.execute(
            "UPDATE threads SET status = ?, summary = COALESCE(?, summary), "
            "archived_at = COALESCE(archived_at, ?) WHERE id = ?",
            (status.value, summary, now, thread_id),
        )
    else:
        # OPEN (resume) — leave archived_at and summary intact as history.
        self._conn.execute(
            "UPDATE threads SET status = ? WHERE id = ?",
            (status.value, thread_id),
        )
    self._conn.commit()
```

(`ARCHIVING` and `ABANDONED` branches removed in Phase 5; for now keep them or — since this task pre-dates the enum drop — leave the branches inline; the next-task tests will exercise the `ARCHIVED` branch.)

In `src/infrastructure/audit_logger.py`, after `log_thread_archived` (around line 842):

```python
def log_thread_resumed(
    self, thread_id: str, *, prior_archived_at: str | None,
) -> None:
    self.log(
        task_id=thread_id,
        action="thread_resumed",
        actor="founder",
        details={"prior_archived_at": prior_archived_at},
    )
```

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/test_thread_db.py -v
```
Expected: PASS (including the two new tests).

- [ ] **Step 5: Commit**

```bash
git add src/infrastructure/database.py src/infrastructure/audit_logger.py tests/test_thread_db.py
git commit -m "feat(threads): add resume DB primitive + audit writer"
```

---

### Task 2: `POST /threads/{id}/resume` route

**Files:**
- Modify: `src/daemon/routes/threads.py` (append a new endpoint after `abandon_thread_endpoint`, ~line 1263)
- Test: `tests/integration/test_threads_e2e.py` or new `tests/test_threads_resume_route.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_threads_resume.py`:

```python
"""POST /threads/{id}/resume — founder route, archived → open."""
from __future__ import annotations
import pytest
from fastapi.testclient import TestClient

from tests.daemon_test_utils import build_test_daemon  # uses your existing pattern

@pytest.mark.asyncio
async def test_resume_flips_archived_to_open(test_app, default_org):
    client = TestClient(test_app)
    slug = default_org.slug
    # Bootstrap a thread, send one message, archive it
    r = client.post(f"/api/v1/orgs/{slug}/threads",
                    json={"subject": "test", "recipients": ["alpha"], "body_markdown": "hi"})
    assert r.status_code == 200
    thread_id = r.json()["thread_id"]
    client.post(f"/api/v1/orgs/{slug}/threads/{thread_id}/archive", json={"summary": "done"})
    pre = client.get(f"/api/v1/orgs/{slug}/threads/{thread_id}").json()
    assert pre["status"] == "archived"

    r = client.post(f"/api/v1/orgs/{slug}/threads/{thread_id}/resume")
    assert r.status_code == 200
    assert r.json() == {"thread_id": thread_id, "status": "open"}

    post = client.get(f"/api/v1/orgs/{slug}/threads/{thread_id}").json()
    assert post["status"] == "open"
    assert post["summary"] == "done"  # preserved as history
    assert post["archived_at"] is not None  # preserved as history


@pytest.mark.asyncio
async def test_resume_idempotent_on_open(test_app, default_org):
    client = TestClient(test_app)
    slug = default_org.slug
    r = client.post(f"/api/v1/orgs/{slug}/threads",
                    json={"subject": "test", "recipients": ["alpha"], "body_markdown": "hi"})
    thread_id = r.json()["thread_id"]
    r = client.post(f"/api/v1/orgs/{slug}/threads/{thread_id}/resume")
    assert r.status_code == 200
    assert r.json() == {"thread_id": thread_id, "status": "open", "idempotent": True}


@pytest.mark.asyncio
async def test_resume_404_on_missing_thread(test_app, default_org):
    client = TestClient(test_app)
    r = client.post(f"/api/v1/orgs/{default_org.slug}/threads/THR-NEVER/resume")
    assert r.status_code == 404
```

**Note:** look at any existing thread route test (e.g. `tests/test_threads_routes.py` if present) for the exact fixture/imports — adopt the same shape; the test snippet above is illustrative.

- [ ] **Step 2: Run to confirm failure**

```bash
uv run pytest tests/test_threads_resume.py -v
```
Expected: FAIL (route returns 404 — not yet defined).

- [ ] **Step 3: Implement the route**

In `src/daemon/routes/threads.py`, after `abandon_thread_endpoint` (~line 1263) add:

```python
# ---------------------------------------------------------------------------
# POST /threads/{id}/resume — founder reopens an archived thread
# ---------------------------------------------------------------------------


@router.post("/threads/{thread_id}/resume")
async def resume_thread_endpoint(
    slug: str, thread_id: str, org: OrgDep,
) -> dict:
    t = org.db.get_thread(thread_id)
    if t is None:
        raise HTTPException(status_code=404, detail={"code": "not_found"})
    if t.status is ThreadStatus.OPEN:
        return {"thread_id": thread_id, "status": "open", "idempotent": True}

    prior_archived_at = (
        t.archived_at.isoformat() if t.archived_at else None
    )
    async with org.db_lock:
        org.db.set_thread_status(thread_id, status=ThreadStatus.OPEN)
        sys_seq = org.db.append_thread_message(
            thread_id=thread_id, speaker="founder",
            kind=ThreadMessageKind.SYSTEM,
            system_payload={"kind_tag": "resumed"},
        )
        AuditLogger(org.db).log_thread_resumed(
            thread_id, prior_archived_at=prior_archived_at,
        )

    await _publish_thread_event(
        org, slug,
        thread_id=thread_id, seq=sys_seq, speaker="founder",
        kind="system", preview="resumed", status="open",
    )

    return {"thread_id": thread_id, "status": "open"}
```

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/test_threads_resume.py -v
```
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/daemon/routes/threads.py tests/test_threads_resume.py
git commit -m "feat(threads): add POST /threads/{id}/resume — founder reopen"
```

---

### Task 3: CLI `happyranch threads resume`

**Files:**
- Modify: `src/cli.py` — add `cmd_threads_resume` near `cmd_threads_archive` (~line 2197) and register subparser near line 3079
- Test: `tests/test_cli_threads.py` (or whatever pattern exists for CLI thread tests)

- [ ] **Step 1: Add the handler**

In `src/cli.py`, after `cmd_threads_archive` (~line 2198):

```python
def cmd_threads_resume(args: argparse.Namespace) -> None:
    import json as _json
    client = OpcClient.from_env()
    slug = resolve_org_slug(
        args_org=args.org, available=_fetch_available_orgs(client),
    )
    r = client.post(f"/api/v1/orgs/{slug}/threads/{args.thread_id}/resume")
    if not _ok(r):
        return
    print(_json.dumps(r.json(), indent=2))
```

Register subparser after `p_threads_archive` (~line 3080):

```python
p_threads_resume = threads_sub.add_parser(
    "resume", help="Founder: reopen an archived thread",
)
p_threads_resume.add_argument("--org", default=None, help="Org slug")
p_threads_resume.add_argument("--thread-id", dest="thread_id", required=True)
p_threads_resume.set_defaults(func=cmd_threads_resume)
```

- [ ] **Step 2: Smoke-test against the running daemon**

```bash
happyranch --help | grep -A1 "resume"
happyranch threads resume --help
```
Expected: `resume` listed; help text shows `--thread-id`.

(End-to-end CLI test deferred — the route is already covered by Task 2's pytest suite.)

- [ ] **Step 3: Commit**

```bash
git add src/cli.py
git commit -m "feat(cli): happyranch threads resume"
```

---

### Task 4: Web API mirror + hook + ResumeButton

**Files:**
- Modify: `web/src/lib/api/threads.ts` — add `resumeThread`
- Modify: `web/src/hooks/threads.ts` — add `useResumeThread`
- Modify: `web/src/test/openapi-coverage.test.ts` — add the new path to `INCLUDED_PATHS`
- Create: `web/src/features/threads/ResumeButton.tsx`
- Modify: `web/src/features/threads/ThreadsPage.tsx` — render `ResumeButton` when `status === 'archived'`

- [ ] **Step 1: API mirror**

In `web/src/lib/api/threads.ts`, after `archiveThread` (~line 68):

```ts
export const resumeThread = (
  slug: string, threadId: string,
): Promise<{ thread_id: string; status: 'open'; idempotent?: boolean }> =>
  apiFetch(`/api/v1/orgs/${slug}/threads/${threadId}/resume`, { method: 'POST' });
```

- [ ] **Step 2: Hook**

In `web/src/hooks/threads.ts`, mirror the `useArchiveThread` pattern:

```ts
export function useResumeThread(threadId: string) {
  const slug = useOrgSlug();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => resumeThread(slug, threadId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['thread', threadId] });
      qc.invalidateQueries({ queryKey: ['threads'] });
    },
  });
}
```

- [ ] **Step 3: OpenAPI coverage**

In `web/src/test/openapi-coverage.test.ts`, add to `INCLUDED_PATHS` near line 92:

```ts
'POST /api/v1/orgs/{slug}/threads/{thread_id}/resume',
```

- [ ] **Step 4: ResumeButton**

Create `web/src/features/threads/ResumeButton.tsx`:

```tsx
import { Button } from '@/design-system/primitives/Button';
import { useResumeThread } from '@/hooks/threads';

export function ResumeButton({ threadId }: { threadId: string }): JSX.Element {
  const resume = useResumeThread(threadId);
  return (
    <Button
      variant="secondary"
      onClick={() => resume.mutate()}
      disabled={resume.isPending}
    >
      {resume.isPending ? 'Resuming…' : 'Resume thread'}
    </Button>
  );
}
```

In `web/src/features/threads/ThreadsPage.tsx`, locate the archive/actions row in the thread detail view and add:

```tsx
{thread.status === 'archived' && <ResumeButton threadId={thread.id} />}
```

- [ ] **Step 5: Run web tests**

```bash
cd web && npm run test -- --run
```
Expected: PASS (including the openapi-coverage check).

- [ ] **Step 6: Commit**

```bash
git add web/src/lib/api/threads.ts web/src/hooks/threads.ts web/src/test/openapi-coverage.test.ts web/src/features/threads/ResumeButton.tsx web/src/features/threads/ThreadsPage.tsx
git commit -m "feat(web): resume archived threads via founder button"
```

---

## Phase 2 — Synchronous archive (collapse the transitional state)

### Task 5: `ArchiveBody.summary` optional + drop `request_close_outs`

**Files:**
- Modify: `src/daemon/routes/threads.py:1068-1070` (ArchiveBody definition) and lines 1092-1119 (handler body)
- Modify: `web/src/features/threads/ArchiveDialog.tsx` — drop empty-string guard, drop checkbox, relabel
- Test: append to `tests/test_threads_resume.py` (already touches archive)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_threads_resume.py`:

```python
@pytest.mark.asyncio
async def test_archive_with_empty_summary_succeeds(test_app, default_org):
    client = TestClient(test_app)
    slug = default_org.slug
    r = client.post(f"/api/v1/orgs/{slug}/threads",
                    json={"subject": "test", "recipients": ["alpha"], "body_markdown": "hi"})
    thread_id = r.json()["thread_id"]
    r = client.post(f"/api/v1/orgs/{slug}/threads/{thread_id}/archive", json={})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "archived"
    assert body["thread_id"] == thread_id
```

- [ ] **Step 2: Run to confirm failure**

```bash
uv run pytest tests/test_threads_resume.py::test_archive_with_empty_summary_succeeds -v
```
Expected: FAIL (422 — `summary` is required).

- [ ] **Step 3: Implement on the route**

Replace `ArchiveBody` (`src/daemon/routes/threads.py:1068-1070`):

```python
class ArchiveBody(BaseModel):
    summary: str = ""
```

- [ ] **Step 4: Implement in the web dialog**

In `web/src/features/threads/ArchiveDialog.tsx`:

- Drop the `requestCloseOuts` state, `closeOutsId`, the `<label>...checkbox` block (lines 25, 28, 33, 80-91).
- Drop the `if (!summary.trim()) { ... return; }` guard (lines 39-42).
- In the `archive.mutateAsync` call, send `{ summary: summary.trim() }` only (no `request_close_outs`).
- Update the `FormField` label to `"Founder summary (optional, will be saved to transcript)"`.
- Update the `<DialogDescription>` to `"Archive this thread. A summary will be saved to the transcript."`.

- [ ] **Step 5: Run tests**

```bash
uv run pytest tests/test_threads_resume.py -v
cd web && npm run test -- --run
```
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/daemon/routes/threads.py web/src/features/threads/ArchiveDialog.tsx tests/test_threads_resume.py
git commit -m "feat(threads): make archive summary optional + drop close-outs toggle"
```

---

### Task 6: Synchronous archive handler + drop finalizer

**Files:**
- Modify: `src/daemon/routes/threads.py` — rewrite `archive_thread_endpoint` (lines 1073-1143)
- Delete: `src/daemon/thread_archive_finalizer.py`
- Modify: `src/daemon/state.py:30-44` — drop `close_out_wait_seconds` param + spawn machinery
- Modify: `src/daemon/app.py` — drop any finalizer spawn at lifespan startup (grep for `spawn_finalizer`)
- Modify: `src/orchestrator/org_config.py` — drop `threads_close_out_wait_seconds`
- Modify: `examples/orgs/hk-macau-tourism/org/config.yaml` — drop the `threads_close_out_wait_seconds:` key if present

- [ ] **Step 1: Write the failing test**

Append to `tests/test_threads_resume.py`:

```python
@pytest.mark.asyncio
async def test_archive_is_synchronous_returns_archived_status(test_app, default_org):
    client = TestClient(test_app)
    slug = default_org.slug
    r = client.post(f"/api/v1/orgs/{slug}/threads",
                    json={"subject": "test", "recipients": ["alpha"], "body_markdown": "hi"})
    thread_id = r.json()["thread_id"]
    r = client.post(f"/api/v1/orgs/{slug}/threads/{thread_id}/archive",
                    json={"summary": "done"})
    assert r.status_code == 200  # was 202 in the old finalizer-based flow
    body = r.json()
    assert body["status"] == "archived"
    # transcript_path is populated synchronously
    assert body.get("transcript_path") is not None
    # No transitional 'archiving' state visible to a follow-up GET
    detail = client.get(f"/api/v1/orgs/{slug}/threads/{thread_id}").json()
    assert detail["status"] == "archived"
```

- [ ] **Step 2: Run to confirm failure**

```bash
uv run pytest tests/test_threads_resume.py::test_archive_is_synchronous_returns_archived_status -v
```
Expected: FAIL (returns 202 with status=archiving).

- [ ] **Step 3: Rewrite the archive handler**

Replace lines 1073-1143 of `src/daemon/routes/threads.py`:

```python
@router.post("/threads/{thread_id}/archive")
async def archive_thread_endpoint(
    slug: str, thread_id: str, body: ArchiveBody, org: OrgDep,
) -> dict:
    t = org.db.get_thread(thread_id)
    if t is None:
        raise HTTPException(status_code=404, detail={"code": "not_found"})
    if t.status is ThreadStatus.ARCHIVED:
        return {
            "thread_id": thread_id, "status": "archived",
            "transcript_path": t.transcript_path, "idempotent": True,
        }
    summary = body.summary.strip()

    async with org.db_lock:
        org.db.reap_pending_invocations(
            thread_id,
            purposes=[ThreadInvocationPurpose.REPLY, ThreadInvocationPurpose.BOOTSTRAP],
            decline_reason="archive_started",
        )
        org.db.set_thread_status(
            thread_id, status=ThreadStatus.ARCHIVED, summary=summary or None,
        )
        sys_seq = org.db.append_thread_message(
            thread_id=thread_id, speaker="founder",
            kind=ThreadMessageKind.SYSTEM,
            system_payload={"kind_tag": "archived", "summary": summary},
        )
        AuditLogger(org.db).log_thread_archived(
            thread_id,
            new_learnings_total=0,
            new_kb_slugs=[],
        )

    # Write transcript synchronously (was the finalizer's job).
    from src.infrastructure.thread_store import ThreadStore
    transcript_path = ThreadStore(org.root / "threads").write_transcript(
        thread_id=thread_id,
        subject=t.subject,
        started_at=t.started_at,
        archived_at=_now(),
        summary=summary,
        participants=[p.agent_name for p in org.db.list_thread_participants(thread_id)],
        messages=org.db.list_thread_messages(thread_id),
        new_learnings_total=0,
        new_kb_slugs=[],
    )
    async with org.db_lock:
        org.db.set_thread_transcript_path(thread_id, transcript_path)

    await _publish_thread_event(
        org, slug,
        thread_id=thread_id, seq=sys_seq, speaker="founder",
        kind="system", preview="archived", status="archived",
    )

    return {
        "thread_id": thread_id, "status": "archived",
        "transcript_path": transcript_path,
    }
```

If `set_thread_transcript_path` doesn't exist, add it next to `set_thread_status` in `src/infrastructure/database.py`:

```python
@_synchronized
def set_thread_transcript_path(self, thread_id: str, transcript_path: str) -> None:
    self._conn.execute(
        "UPDATE threads SET transcript_path = ? WHERE id = ?",
        (transcript_path, thread_id),
    )
    self._conn.commit()
```

(Verify the signature of `ThreadStore.write_transcript` and `db.list_thread_messages` before writing; adapt to whatever the existing API surface is — the snippet uses the same field names the deleted finalizer used.)

- [ ] **Step 4: Delete the finalizer module**

```bash
git rm src/daemon/thread_archive_finalizer.py
```

In `src/daemon/state.py`, remove the `close_out_wait_seconds: int = 300` parameter and the `from src.daemon.thread_archive_finalizer import finalize_thread as _fin` block (lines 30-44). Also remove the `thread_finalizers` attribute and any references to it.

In `src/orchestrator/org_config.py`, remove the `threads_close_out_wait_seconds` field from the `OrgConfig` dataclass.

In `examples/orgs/hk-macau-tourism/org/config.yaml`, remove the `threads_close_out_wait_seconds:` line if present.

- [ ] **Step 5: Run the full test suite**

```bash
uv run pytest tests/ -v -x 2>&1 | tail -30
```

Expected: PASS or — if anything else still references the finalizer (grep for `thread_archive_finalizer`, `spawn_finalizer`, `close_out_wait_seconds`, `thread_finalizers`) — fix the lingering reference. No `archiving`-related tests should remain green from before; if they do, they're testing dead behaviour and Phase 5 will delete them.

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "feat(threads): make archive synchronous, drop archive finalizer"
```

---

## Phase 3 — Remove close-out

### Task 7: Delete close-out route + body model + runner branch

**Files:**
- Modify: `src/daemon/routes/threads.py` — delete `CloseOutBody`, `CloseOutLearning`, `close_out_thread_endpoint` (lines 1152-1232)
- Modify: `src/daemon/thread_runner.py` — drop the `purpose == "close_out"` branch (lines 61-62) and remove `close_out` from the docstring/type hint at line 110, 135-136
- Delete: any `tests/test_threads_close_out*.py` (catalogue them first with `ls tests/ | grep close_out`)

- [ ] **Step 1: Catalogue existing close-out tests**

```bash
grep -rln "close_out\|close-out\|CloseOut" tests/ | sort -u
```

Record the list — every file in that list will either be deleted or have its close-out cases removed.

- [ ] **Step 2: Delete the route + body models**

In `src/daemon/routes/threads.py`, delete lines 1146-1232 inclusive (the `# Task 27 — abandon` placeholder comment, the `# Task 30 — close-out` comment block, `CloseOutLearning`, `CloseOutBody`, and `close_out_thread_endpoint`). Keep `abandon_thread_endpoint` for now — it goes in Phase 4.

In `src/daemon/thread_runner.py`:

- Delete the close-out branch (lines 61-62):
  ```python
  if purpose == "close_out":
      return "This thread is being archived; provide a close-out"
  ```
- Update the docstring at line 4 to remove `close-out` from the consumer list.
- Update the type hint at line 110: `purpose: str,  # 'reply' | 'bootstrap'`.
- Update the prose at lines 135-136 to drop close-out from the prompt text.

- [ ] **Step 3: Delete close-out test files**

For each file from Step 1 that exists *only* to exercise close-out:

```bash
git rm tests/test_threads_close_out.py  # if it exists; substitute real names from step 1
```

For files with mixed coverage, remove only the close-out cases (do not delete shared fixtures).

- [ ] **Step 4: Run the full suite**

```bash
uv run pytest tests/ -v -x 2>&1 | tail -30
```
Expected: PASS (no test failures; deleted tests no longer collected).

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "feat(threads): remove close-out route, body, and runner branch"
```

---

### Task 8: Delete close-out CLI subcommand

**Files:**
- Modify: `src/cli.py` — drop `cmd_threads_close_out` (around line 2065) and its subparser registration (lines 3040-3044)

- [ ] **Step 1: Delete the handler + subparser**

In `src/cli.py`:

- Delete the function `cmd_threads_close_out` and its body (~line 2065 through the next blank line / next `def`).
- Delete lines 3040-3044 (the subparser registration block).
- Also update the parser line 2979 — change `help="Thread operations (compose, reply, decline, dispatch, close-out)"` to `help="Thread operations (compose, reply, decline, dispatch)"`.

- [ ] **Step 2: Smoke-check**

```bash
happyranch threads --help 2>&1 | grep close-out
```
Expected: empty output.

- [ ] **Step 3: Commit**

```bash
git add src/cli.py
git commit -m "feat(cli): drop happyranch threads close-out subcommand"
```

---

### Task 9: Drop `ThreadInvocationPurpose.CLOSE_OUT` + close-out audit writer

**Files:**
- Modify: `src/models.py:189` — remove `CLOSE_OUT = "close_out"`
- Modify: `src/infrastructure/audit_logger.py` — delete `log_thread_close_out_received` (line 850)
- Grep + fix any remaining references: `grep -rn "CLOSE_OUT\|close_out\|close-out" src/`

- [ ] **Step 1: Drop the enum value**

In `src/models.py:186-190`:

```python
class ThreadInvocationPurpose(StrEnum):
    REPLY = "reply"
    BOOTSTRAP = "bootstrap"
    TASK_FOLLOWUP = "task_followup"
```

- [ ] **Step 2: Drop the audit writer**

In `src/infrastructure/audit_logger.py`, delete `def log_thread_close_out_received(...)` and its body (~lines 850-865). Leave the action string `"thread_close_out_received"` un-referenced in code; historic audit rows with that action remain readable.

- [ ] **Step 3: Sweep**

```bash
grep -rn "CLOSE_OUT\|close_out\|close-out" src/
```
Expected: only references in audit `action` strings consumed by readers (e.g. dashboard renderers that gracefully handle unknown actions) — if any non-historic write site appears, fix it.

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/ -v -x 2>&1 | tail -10
```
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "feat(threads): drop CLOSE_OUT enum + close-out audit writer"
```

---

## Phase 4 — Remove abandon (collapse into archive)

### Task 10: Delete abandon route + body model

**Files:**
- Modify: `src/daemon/routes/threads.py` — delete `AbandonBody` and `abandon_thread_endpoint` (lines 1235-1262)

- [ ] **Step 1: Delete**

Remove `class AbandonBody(...)` and `async def abandon_thread_endpoint(...)` and the `# Task 27 — POST /threads/{id}/abandon` comment block.

- [ ] **Step 2: Run tests**

```bash
uv run pytest tests/ -v -x 2>&1 | tail -10
```
Expected: PASS (existing abandon-route tests still exist but will fail — Step 3 deletes them).

- [ ] **Step 3: Delete abandon tests**

```bash
grep -rln "abandon\|ABANDONED\|AbandonBody" tests/ | sort -u
```

For each file in the list, remove the abandon cases. Delete files that exist *only* for abandon coverage.

- [ ] **Step 4: Run tests again**

```bash
uv run pytest tests/ -v -x 2>&1 | tail -10
```
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "feat(threads): drop abandon route + body model"
```

---

### Task 11: Delete abandon CLI + audit writer

**Files:**
- Modify: `src/cli.py` — drop `cmd_threads_abandon` (~line 2166) and subparser registration (lines 3070-3074)
- Modify: `src/infrastructure/audit_logger.py:842` — delete `log_thread_abandoned`

- [ ] **Step 1: Delete the CLI bits**

In `src/cli.py`:

- Delete the `cmd_threads_abandon` function.
- Delete lines 3070-3074 (the subparser registration block).

- [ ] **Step 2: Delete the audit writer**

In `src/infrastructure/audit_logger.py`, delete `def log_thread_abandoned(...)` at line 842.

- [ ] **Step 3: Run tests**

```bash
uv run pytest tests/ -v -x 2>&1 | tail -10
```
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add -A
git commit -m "feat(cli,threads): drop abandon CLI + audit writer"
```

---

### Task 12: Delete web AbandonDialog + hook + API mirror

**Files:**
- Delete: `web/src/features/threads/AbandonDialog.tsx`
- Modify: `web/src/lib/api/threads.ts` — drop `abandonThread`
- Modify: `web/src/hooks/threads.ts` — drop `useAbandonThread`
- Modify: `web/src/test/openapi-coverage.test.ts` — remove `POST /api/v1/orgs/{slug}/threads/{thread_id}/abandon` from `INCLUDED_PATHS`
- Modify: `web/src/features/threads/ThreadsPage.tsx` — drop any `<AbandonDialog>` usage / button

- [ ] **Step 1: Delete**

```bash
git rm web/src/features/threads/AbandonDialog.tsx
```

In `web/src/lib/api/threads.ts` — delete the `abandonThread` export (around line 75).

In `web/src/hooks/threads.ts` — grep for `useAbandonThread` and delete the entire hook.

In `web/src/test/openapi-coverage.test.ts` — delete the line with `threads/{thread_id}/abandon`.

In `web/src/features/threads/ThreadsPage.tsx` — grep for `AbandonDialog` / `abandon` and remove the trigger button + dialog mount.

- [ ] **Step 2: Run web tests**

```bash
cd web && npm run test -- --run
```
Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add -A
git commit -m "feat(web): drop AbandonDialog + abandon API mirror"
```

---

## Phase 5 — Drop dead state, columns, model fields

### Task 13: Drop `ThreadStatus.ARCHIVING` + archive_requested audit writer

**Files:**
- Modify: `src/models.py:165-169` — drop `ARCHIVING = "archiving"`
- Modify: `src/infrastructure/audit_logger.py:810` — delete `log_thread_archive_requested`
- Modify: `src/infrastructure/database.py:2267-2292` — remove the `ARCHIVING` branch (Task 1 left it in place defensively)

- [ ] **Step 1: Drop enum value**

In `src/models.py`:

```python
class ThreadStatus(StrEnum):
    OPEN = "open"
    ARCHIVED = "archived"
```

- [ ] **Step 2: Drop audit writer**

In `src/infrastructure/audit_logger.py`, delete `def log_thread_archive_requested(...)`.

- [ ] **Step 3: Trim `set_thread_status`**

Replace the body of `set_thread_status` in `src/infrastructure/database.py`:

```python
@_synchronized
def set_thread_status(
    self,
    thread_id: str,
    *,
    status: ThreadStatus,
    summary: str | None = None,
) -> None:
    now = _now().isoformat()
    if status is ThreadStatus.ARCHIVED:
        self._conn.execute(
            "UPDATE threads SET status = ?, summary = COALESCE(?, summary), "
            "archived_at = COALESCE(archived_at, ?) WHERE id = ?",
            (status.value, summary, now, thread_id),
        )
    else:  # OPEN
        self._conn.execute(
            "UPDATE threads SET status = ? WHERE id = ?",
            (status.value, thread_id),
        )
    self._conn.commit()
```

- [ ] **Step 4: Sweep**

```bash
grep -rn "ARCHIVING\|archiving\|archive_requested" src/
```
Expected: only historic audit-row reader paths (which gracefully handle unknown actions). No write sites.

- [ ] **Step 5: Run tests**

```bash
uv run pytest tests/ -v -x 2>&1 | tail -10
```
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "feat(threads): drop ARCHIVING state + archive_requested audit writer"
```

---

### Task 14: Drop `ThreadStatus.ABANDONED`

**Files:**
- Modify: `src/models.py` — already trimmed in Task 13. Confirm `ABANDONED` is gone.

- [ ] **Step 1: Confirm + sweep**

```bash
grep -rn "ABANDONED\|abandoned" src/
```
Expected: only historic audit-row reader paths.

- [ ] **Step 2: Run tests**

```bash
uv run pytest tests/ -v -x 2>&1 | tail -10
```
Expected: PASS.

(If Task 13 didn't already collapse `ABANDONED` out of `ThreadStatus`, do it now: the enum should read `OPEN` + `ARCHIVED` only.)

- [ ] **Step 3: Commit**

```bash
git add -A
git commit -m "feat(threads): drop ABANDONED state from ThreadStatus enum"
```

(Skip the commit if nothing changed — the work was bundled into Task 13.)

---

### Task 15: Drop dead model fields + DB methods + columns from CREATE TABLE

**Files:**
- Modify: `src/models.py:204-207` — drop `new_kb_slugs`, `new_learnings_total`, `archive_requested_at`
- Modify: `src/infrastructure/database.py`:
  - Lines 301-315 — drop the three columns from `CREATE TABLE threads`
  - Lines 521 (ALTER TABLE migration) — delete the migration call
  - Lines 1822-1862 — drop the column reads in `_thread_row_to_model` (`get_thread`) and the insert columns
  - Lines 2294-2312 — delete `finalize_thread_archived`
  - Lines 2414-2444 — delete `add_thread_kb_slug` and `add_thread_learnings_count`

- [ ] **Step 1: Drop model fields**

In `src/models.py` (the `ThreadRecord` class):

```python
class ThreadRecord(BaseModel):
    id: str
    subject: str
    status: ThreadStatus = ThreadStatus.OPEN
    started_at: datetime = Field(default_factory=_now)
    archived_at: datetime | None = None
    forwarded_from_id: str | None = None
    forwarded_from_kind: str | None = None
    turn_cap: int = 500
    turns_used: int = 0
    summary: str | None = None
    transcript_path: str | None = None
    composed_by: str = "founder"
    composed_from_task_id: str | None = None
    composed_from_talk_id: str | None = None
```

- [ ] **Step 2: Drop columns from CREATE TABLE**

In `src/infrastructure/database.py:301-315`:

```python
CREATE TABLE IF NOT EXISTS threads (
    id TEXT PRIMARY KEY,
    subject TEXT NOT NULL,
    started_at TEXT NOT NULL,
    archived_at TEXT,
    status TEXT NOT NULL DEFAULT 'open',
    forwarded_from_id TEXT,
    forwarded_from_kind TEXT,
    turn_cap INTEGER NOT NULL DEFAULT 500,
    turns_used INTEGER NOT NULL DEFAULT 0,
    summary TEXT,
    transcript_path TEXT
);
```

- [ ] **Step 3: Drop the ALTER TABLE migration**

In `src/infrastructure/database.py`, delete the `ALTER TABLE threads ADD COLUMN new_learnings_total ...` block at line 521. (If the surrounding migration function becomes empty, leave it as a no-op or delete it entirely.)

- [ ] **Step 4: Trim row reader + insert**

In `_thread_row_to_model` (around line 1862), delete the `new_kb_slugs`, `new_learnings_total`, `archive_requested_at` assignments.

In `_insert_thread` (around line 1822), drop the same columns from the INSERT statement and the parameter tuple.

- [ ] **Step 5: Delete dead DB methods**

Delete from `src/infrastructure/database.py`:

- `finalize_thread_archived` (~2294-2312)
- `add_thread_kb_slug` (~2414)
- `add_thread_learnings_count` (~2432-2444)

- [ ] **Step 6: Sweep**

```bash
grep -rn "new_kb_slugs\|new_learnings_total\|archive_requested_at\|finalize_thread_archived\|add_thread_kb_slug\|add_thread_learnings_count" src/
```
Expected: empty.

- [ ] **Step 7: Run tests**

```bash
uv run pytest tests/ -v -x 2>&1 | tail -20
```
Expected: PASS. (Fresh test DBs initialize with the trimmed schema.)

- [ ] **Step 8: Commit**

```bash
git add -A
git commit -m "feat(threads): drop close-out model fields, DB columns, and dead methods"
```

---

### Task 16: SQL sweep script + execute against local runtime DBs

**Files:**
- Create: `scripts/migrations/2026-06-01_drop_close_out_columns.sql`
- Create: `scripts/migrations/2026-06-01_run_sweep.sh` (thin wrapper for running across all per-org DBs)

- [ ] **Step 1: Write the SQL**

Create `scripts/migrations/2026-06-01_drop_close_out_columns.sql`:

```sql
-- One-shot sweep for the 2026-06-01 thread close-out removal.
-- Run once per per-org SQLite DB at <runtime>/orgs/<slug>/happyranch.db.
-- Safe to re-run (idempotent updates / deletes).

BEGIN;

-- 1. Collapse 'archiving' and 'abandoned' rows into 'archived'.
UPDATE threads SET status = 'archived',
    archived_at = COALESCE(archived_at, archive_requested_at, datetime('now'))
WHERE status IN ('archiving', 'abandoned');

-- 2. Drop historic system messages that no longer have render targets.
DELETE FROM thread_messages
WHERE kind = 'system'
  AND json_extract(system_payload_json, '$.kind_tag') = 'archive_requested';

-- 3. Drop dead close_out invocation rows (the ThreadInvocationPurpose
--    enum no longer accepts 'close_out', so loaders would fail).
DELETE FROM thread_invocations WHERE purpose = 'close_out';

-- 4. Drop the three columns. Requires SQLite 3.35+ (macOS ships 3.39+).
ALTER TABLE threads DROP COLUMN new_kb_slugs_json;
ALTER TABLE threads DROP COLUMN new_learnings_total;
ALTER TABLE threads DROP COLUMN archive_requested_at;

COMMIT;

-- Verify.
SELECT 'rows by status:' AS check_name;
SELECT status, COUNT(*) FROM threads GROUP BY status;

SELECT 'remaining archive_requested system msgs:' AS check_name;
SELECT COUNT(*) FROM thread_messages
WHERE kind = 'system'
  AND json_extract(system_payload_json, '$.kind_tag') = 'archive_requested';

SELECT 'remaining close_out invocations:' AS check_name;
SELECT COUNT(*) FROM thread_invocations WHERE purpose = 'close_out';
```

- [ ] **Step 2: Write the runner script**

Create `scripts/migrations/2026-06-01_run_sweep.sh`:

```bash
#!/usr/bin/env bash
set -euo pipefail

RUNTIME="${1:-$HOME/.local/share/happyranch-runtime}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SQL_FILE="$SCRIPT_DIR/2026-06-01_drop_close_out_columns.sql"

if [[ ! -d "$RUNTIME/orgs" ]]; then
    echo "no orgs directory at $RUNTIME/orgs" >&2
    exit 1
fi

for db in "$RUNTIME"/orgs/*/happyranch.db; do
    [[ -f "$db" ]] || continue
    echo "=== sweeping $db ==="
    sqlite3 "$db" < "$SQL_FILE"
done
```

```bash
chmod +x scripts/migrations/2026-06-01_run_sweep.sh
```

- [ ] **Step 3: Stop the daemon before running the sweep**

```bash
scripts/daemon.sh stop
```

- [ ] **Step 4: Execute the sweep**

```bash
./scripts/migrations/2026-06-01_run_sweep.sh
```

Expected output for each org: three "remaining = 0" checks plus the final status histogram. If any "remaining" count is > 0, investigate before continuing.

- [ ] **Step 5: Restart the daemon and smoke-test**

```bash
scripts/daemon.sh start
happyranch threads list --org tourism-org --status archived | head -5
```

Expected: command returns archived threads (no schema errors).

- [ ] **Step 6: Commit**

```bash
git add scripts/migrations/
git commit -m "feat(migrations): one-shot sweep for thread close-out removal"
```

---

## Phase 6 — Web cleanup, docs, snapshot

### Task 17: Strip web TS types + mocks + status union

**Files:**
- Modify: `web/src/lib/api/types.ts:139,161` — drop `new_kb_slugs`, `new_learnings_total`; narrow `ThreadStatus` union to `'open' | 'archived'`
- Modify: `web/src/mocks/threads.ts`, `web/src/design-system/providers/_mock-threads.ts` — drop dropped fields and rows with `'archiving'` / `'abandoned'` statuses
- Modify: `web/src/features/threads/ThreadsPage.tsx`:
  - Drop the `case 'archive_requested':` branch in `describeSystem` (line 474)
  - Add `case 'resumed': return 'resumed';`
  - Change the `case 'archived':` text from `'archived'` (still correct) — keep
  - Drop any status filter chips for `'archiving'` / `'abandoned'`

- [ ] **Step 1: Trim types**

In `web/src/lib/api/types.ts`:

```ts
// Around line 139 — Thread type
export interface Thread {
  id: string;
  subject: string;
  status: ThreadStatus;
  started_at: string;
  archived_at: string | null;
  // ... keep everything else, but drop new_kb_slugs + new_learnings_total
  transcript_path: string | null;
  summary: string | null;
  // ...
}

export type ThreadStatus = 'open' | 'archived';
```

(Verify the exact shape before editing — adapt to the real file.)

- [ ] **Step 2: Trim mocks**

In `web/src/mocks/threads.ts` and `web/src/design-system/providers/_mock-threads.ts`:

- Remove `new_kb_slugs`, `new_learnings_total` fields from every mock row.
- Remove any row with `status: 'archiving'` or `status: 'abandoned'`.

- [ ] **Step 3: Trim describeSystem + filters**

In `web/src/features/threads/ThreadsPage.tsx`, around line 461:

```tsx
function describeSystem(payload: Record<string, unknown> | null, slug?: string): React.ReactNode {
  if (!payload) return 'system event';
  const tag = String(payload.kind_tag ?? payload.event ?? '');
  switch (tag) {
    case 'invited':
      return `invited ${payload.agent}`;
    case 'participant_added':
      return `added ${payload.agent_name}`;
    case 'extended':
    case 'turn_cap_extended':
      return `turn cap raised to ${payload.new_cap}`;
    case 'archived':
      return 'archived';
    case 'resumed':
      return 'resumed';
    // ... task_dispatched / task_completed / task_failed branches stay
    default:
      return tag || 'system event';
  }
}
```

(Drop `case 'archive_requested'` and `case 'abandoned'`. Add `case 'resumed'`.)

Locate any status filter chips and trim to `open / archived`.

- [ ] **Step 4: Run web tests**

```bash
cd web && npm run test -- --run
```
Expected: PASS. Update any test fixtures that reference the dropped statuses or fields.

- [ ] **Step 5: Commit**

```bash
git add web/
git commit -m "feat(web): narrow ThreadStatus to open|archived, drop close-out fields"
```

---

### Task 18: Doc updates

**Files:**
- Modify: `docs/superpowers/specs/2026-05-13-threads-design.md` — add a header pointer to this new design; mark §5.10.1 (close-out), §5.13 (abandon), §5.10 phase B (archive finalizer) as superseded
- Modify: `docs/superpowers/specs/2026-05-30-thread-broadcast-only-design.md` — trim close-out invocation references
- Modify: `protocol/skills/thread/SKILL.md` — drop the close-out section; add a resume verb under founder actions
- Modify: `CLAUDE.md` (project root):
  - "Thread broadcast routing (addressing model)" section: trim any close-out invocation references
  - Status vocabulary line — collapse to `open` / `archived`

- [ ] **Step 1: Threads spec marker**

At the top of `docs/superpowers/specs/2026-05-13-threads-design.md`, after the Status block, add:

```markdown
> **Superseded in part by `2026-06-01-thread-close-out-removal-and-resume-design.md`** —
> §5.10.1 (close-out), §5.13 (abandon), and the Phase B finalizer portion of §5.10
> are no longer accurate. Archive is now synchronous; abandoned and archiving states
> have been collapsed into `archived`; close-out invocations are removed.
```

- [ ] **Step 2: Thread skill update**

In `protocol/skills/thread/SKILL.md`, remove the close-out callback section. The skill keeps reply/decline/dispatch only.

- [ ] **Step 3: CLAUDE.md updates**

Locate any wording in the "Thread broadcast routing" / "Thread task-followup" / "Thread / talk dispatch self-only" sections that mentions close-outs or the four-state status vocabulary, and update to match the new shape.

- [ ] **Step 4: Commit**

```bash
git add docs/ protocol/ CLAUDE.md
git commit -m "docs: supersede close-out sections, update CLAUDE.md status vocab"
```

---

### Task 19: Regenerate OpenAPI snapshot + full test sweep

**Files:**
- Modify: `tests/contract/openapi.json` — regenerated by the existing tooling

- [ ] **Step 1: Regenerate the snapshot**

```bash
HAPPYRANCH_REGEN_OPENAPI=1 uv run pytest tests/contract/test_openapi_snapshot.py
```

- [ ] **Step 2: Verify diff is bounded**

```bash
git diff --stat tests/contract/openapi.json
```

Expected diffs: `archive` route loses `request_close_outs` from its schema; new `resume` path appears; `abandon` and `close-out` paths disappear; `ThreadStatus` enum narrows.

- [ ] **Step 3: Run the full Python suite**

```bash
uv run pytest tests/ -v 2>&1 | tail -20
```
Expected: PASS, no skips related to threads.

- [ ] **Step 4: Run the full integration suite**

```bash
uv run pytest tests/ -v -m integration 2>&1 | tail -20
```
Expected: PASS.

- [ ] **Step 5: Run web tests + openapi coverage**

```bash
cd web && npm run test -- --run
```
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add tests/contract/openapi.json
git commit -m "test: regenerate OpenAPI snapshot for close-out removal"
```

---

## Self-Review

Spec coverage (each numbered section in the spec must map to ≥1 task):

- §1 archive synchronous → Tasks 5, 6, 19
- §2 resume → Tasks 1, 2, 3, 4
- §3 CLI changes → Tasks 3, 8, 11
- §4 DB sweep → Tasks 15, 16
- §5 code deletions → Tasks 6, 7, 8, 9, 10, 11, 13, 14, 15
- §6 web UI → Tasks 4, 5, 12, 17
- §7 tests → Tasks 1, 2, 5, 6, 19
- §8 doc updates → Task 18

No placeholders. No `TBD`. No "implement appropriate error handling" without code.

Type consistency check:
- `set_thread_status` signature stable across Task 1 (write site) and Task 13 (re-trimming).
- `ThreadStatus` enum: Task 1 still has 4 values; Tasks 13–14 collapse to `OPEN` + `ARCHIVED`.
- `ThreadInvocationPurpose`: Task 9 drops `CLOSE_OUT`.
- Audit writers used: Task 1 adds `log_thread_resumed`; Task 6 keeps `log_thread_archived`; Tasks 9/11/13 delete `log_thread_close_out_received`, `log_thread_abandoned`, `log_thread_archive_requested`.

All consistent.
