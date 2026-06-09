# Thread File Attachments Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add artifact-backed file attachments to thread messages for founder and agent workflows.

**Architecture:** Thread routes remain reference-only: file bytes live in the existing org artifact store, while thread messages store attachment metadata pointing at artifact names. Web and CLI provide upload-before-send convenience, then pass attachment refs to the daemon. Backend read paths, prompts, transcripts, CLI output, and web rendering all use one shared attachment shape.

**Tech Stack:** Python 3.13, FastAPI, SQLite, Pydantic v2, pytest, TypeScript, React, TanStack Query, MSW/Vitest.

---

## File Structure

- **Modify `runtime/models.py`** — add `ThreadAttachment` model and `attachments` on `ThreadMessage`.
- **Modify `runtime/infrastructure/database.py`** — add `thread_message_attachments` table, migration, append/list attachment accessors.
- **Modify `runtime/daemon/routes/threads.py`** — add attachment request model, validation helpers, message append helper, and attach support on compose/send/reply/compose-as-agent/read/tail.
- **Modify `runtime/infrastructure/audit_logger.py`** — include attachment count and artifact names in `thread_message_sent`.
- **Modify `runtime/daemon/thread_runner.py`** — render attachments into full and delta prompts.
- **Modify `runtime/infrastructure/thread_store.py`** — render attachments into archived transcripts.
- **Modify `protocol/skills/thread/SKILL.md`** — document downloading and sending attachments from thread turns.
- **Modify `cli/client/client.py`** — reuse the existing `put_artifact` helper from thread commands.
- **Modify `cli/commands/threads.py`** — add `--attach` to compose/send/reply and print attachments in `threads show`.
- **Modify `web/src/lib/api/artifacts.ts`** — create a narrow FormData upload/download helper for web artifacts.
- **Modify `web/src/lib/api/index.ts`** — export the new artifacts API.
- **Modify `web/src/lib/api/threads.ts` and `web/src/lib/api/types.ts`** — add attachment types to request/response shapes.
- **Modify `web/src/design-system/patterns/Composer.tsx` and `MessageBubble.tsx`** — render attachment controls and file picker state without owning uploads.
- **Modify `web/src/features/threads/NewThreadDialog.tsx` and `ThreadsPage.tsx`** — orchestrate upload-before-send for new threads and follow-ups.
- **Modify `web/src/design-system/providers/_mock-threads.ts`** — keep prototype data and mutations type-compatible.
- **Tests:** `tests/test_thread_db.py`, `tests/daemon/test_threads_routes.py`, `tests/test_thread_runner.py`, `tests/test_thread_store.py`, create `tests/test_cli_threads.py`, `web/src/lib/api/threads.test.ts`, `web/src/features/threads/write-path.test.tsx`, `web/src/design-system/patterns/Composer.test.tsx`.

## Task 1: Backend Attachment Model And SQLite Storage

**Files:**
- Modify: `runtime/models.py`
- Modify: `runtime/infrastructure/database.py`
- Test: `tests/test_thread_db.py`
- Test: `tests/test_database.py`

- [ ] **Step 1: Run impact analysis before editing symbols**

Run these before changing model/database code:

```bash
# GitNexus MCP calls, not shell:
impact(target="ThreadMessage", direction="upstream", repo="happyranch")
impact(target="append_thread_message", direction="upstream", repo="happyranch")
impact(target="list_thread_messages", direction="upstream", repo="happyranch")
```

Expected: impact returns the known thread route, runner, transcript, and tests as affected. If risk is HIGH or CRITICAL, report it before editing.

- [ ] **Step 2: Write failing DB round-trip tests**

Add to `tests/test_thread_db.py`:

```python
from runtime.models import ThreadAttachment


def test_thread_message_attachments_roundtrip(db) -> None:
    db.insert_thread(ThreadRecord(id="THR-001", subject="Files"))
    seq = db.append_thread_message(
        thread_id="THR-001",
        speaker="founder",
        kind=ThreadMessageKind.MESSAGE,
        body_markdown=None,
        attachments=[
            ThreadAttachment(
                artifact_name="THR-001-report.pdf",
                display_name="report.pdf",
                size_bytes=123,
                content_type="application/pdf",
                uploaded_by="founder",
            ),
            ThreadAttachment(
                artifact_name="THR-001-data.csv",
                display_name="data.csv",
                size_bytes=42,
                content_type="text/csv",
                uploaded_by="founder",
            ),
        ],
    )

    messages = db.list_thread_messages("THR-001")

    assert seq == 1
    assert len(messages) == 1
    assert messages[0].attachments == [
        ThreadAttachment(
            artifact_name="THR-001-report.pdf",
            display_name="report.pdf",
            size_bytes=123,
            content_type="application/pdf",
            uploaded_by="founder",
        ),
        ThreadAttachment(
            artifact_name="THR-001-data.csv",
            display_name="data.csv",
            size_bytes=42,
            content_type="text/csv",
            uploaded_by="founder",
        ),
    ]


def test_thread_message_attachments_default_empty(db) -> None:
    db.insert_thread(ThreadRecord(id="THR-001", subject="No files"))
    db.append_thread_message(
        thread_id="THR-001",
        speaker="founder",
        kind=ThreadMessageKind.MESSAGE,
        body_markdown="hello",
    )

    assert db.list_thread_messages("THR-001")[0].attachments == []
```

- [ ] **Step 3: Run tests and confirm failure**

Run:

```bash
uv run pytest tests/test_thread_db.py::test_thread_message_attachments_roundtrip tests/test_thread_db.py::test_thread_message_attachments_default_empty -v
```

Expected: FAIL because `ThreadAttachment` or `attachments` is not defined, or `append_thread_message` does not accept `attachments`.

- [ ] **Step 4: Add the Pydantic model**

In `runtime/models.py`, add above `class ThreadMessage(BaseModel)`:

```python
class ThreadAttachment(BaseModel):
    artifact_name: str
    display_name: str
    size_bytes: int | None = None
    content_type: str | None = None
    uploaded_by: str
```

Then update `ThreadMessage`:

```python
class ThreadMessage(BaseModel):
    id: int | None = None
    thread_id: str
    seq: int
    speaker: str
    kind: ThreadMessageKind
    body_markdown: str | None = None
    decline_reason: str | None = None
    system_payload: dict | None = None
    attachments: list[ThreadAttachment] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=_now)
```

- [ ] **Step 5: Add schema and migration**

In `runtime/infrastructure/database.py`, add `ThreadAttachment` to the imports from `runtime.models`.

Inside the schema setup near `thread_messages`, add:

```python
            CREATE TABLE IF NOT EXISTS thread_message_attachments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                thread_id TEXT NOT NULL,
                message_seq INTEGER NOT NULL,
                ordinal INTEGER NOT NULL,
                artifact_name TEXT NOT NULL,
                display_name TEXT NOT NULL,
                size_bytes INTEGER,
                content_type TEXT,
                uploaded_by TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY (thread_id) REFERENCES threads(id),
                UNIQUE(thread_id, message_seq, ordinal)
            );
            CREATE INDEX IF NOT EXISTS idx_thread_message_attachments_message
                ON thread_message_attachments(thread_id, message_seq);
```

If this file has an idempotent migration block for `CREATE TABLE IF NOT EXISTS`, add the same `CREATE TABLE IF NOT EXISTS` and `CREATE INDEX IF NOT EXISTS` statements there rather than an `ALTER TABLE`, because this is a new table.

- [ ] **Step 6: Extend append/list helpers**

Change `append_thread_message` signature to:

```python
    def append_thread_message(
        self,
        *,
        thread_id: str,
        speaker: str,
        kind: ThreadMessageKind,
        body_markdown: str | None = None,
        decline_reason: str | None = None,
        system_payload: dict | None = None,
        attachments: list[ThreadAttachment] | None = None,
    ) -> int:
```

After inserting into `thread_messages`, insert attachment rows before commit:

```python
        for ordinal, attachment in enumerate(attachments or []):
            self._conn.execute(
                "INSERT INTO thread_message_attachments ("
                "thread_id, message_seq, ordinal, artifact_name, display_name, "
                "size_bytes, content_type, uploaded_by, created_at"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    thread_id,
                    next_seq,
                    ordinal,
                    attachment.artifact_name,
                    attachment.display_name,
                    attachment.size_bytes,
                    attachment.content_type,
                    attachment.uploaded_by,
                    _now().isoformat(),
                ),
            )
```

Add private hydration helper near `list_thread_messages`:

```python
    def _attachments_for_messages(
        self, thread_id: str, seqs: list[int]
    ) -> dict[int, list[ThreadAttachment]]:
        if not seqs:
            return {}
        placeholders = ",".join("?" for _ in seqs)
        cursor = self._conn.execute(
            "SELECT * FROM thread_message_attachments "
            f"WHERE thread_id = ? AND message_seq IN ({placeholders}) "
            "ORDER BY message_seq, ordinal",
            (thread_id, *seqs),
        )
        out: dict[int, list[ThreadAttachment]] = {seq: [] for seq in seqs}
        for row in cursor.fetchall():
            out.setdefault(row["message_seq"], []).append(
                ThreadAttachment(
                    artifact_name=row["artifact_name"],
                    display_name=row["display_name"],
                    size_bytes=row["size_bytes"],
                    content_type=row["content_type"],
                    uploaded_by=row["uploaded_by"],
                )
            )
        return out
```

In `list_thread_messages`, fetch rows once, hydrate attachments, then build models:

```python
        rows = cursor.fetchall()
        attachments_by_seq = self._attachments_for_messages(
            thread_id, [r["seq"] for r in rows]
        )
        return [
            ThreadMessage(
                id=r["id"],
                thread_id=r["thread_id"],
                seq=r["seq"],
                speaker=r["speaker"],
                kind=ThreadMessageKind(r["kind"]),
                body_markdown=r["body_markdown"],
                decline_reason=r["decline_reason"],
                system_payload=json.loads(r["system_payload_json"]) if r["system_payload_json"] else None,
                attachments=attachments_by_seq.get(r["seq"], []),
                created_at=datetime.fromisoformat(r["created_at"]),
            )
            for r in rows
        ]
```

In `get_thread_message_by_seq`, add:

```python
        attachments = self._attachments_for_messages(thread_id, [seq]).get(seq, [])
```

and pass `attachments=attachments` into `ThreadMessage`.

- [ ] **Step 7: Run focused DB tests**

Run:

```bash
uv run pytest tests/test_thread_db.py::test_thread_message_attachments_roundtrip tests/test_thread_db.py::test_thread_message_attachments_default_empty -v
```

Expected: PASS.

- [ ] **Step 8: Run broader database tests**

Run:

```bash
uv run pytest tests/test_thread_db.py tests/test_database.py -v
```

Expected: PASS.

- [ ] **Step 9: Commit**

Run:

```bash
git add runtime/models.py runtime/infrastructure/database.py tests/test_thread_db.py
git commit -m "feat(threads): persist message attachments"
```

## Task 2: Thread Route Contract And Validation

**Files:**
- Modify: `runtime/daemon/routes/threads.py`
- Modify: `runtime/infrastructure/audit_logger.py`
- Test: `tests/daemon/test_threads_routes.py`
- Test: `tests/test_audit_logger.py`

- [ ] **Step 1: Run impact analysis before editing route symbols**

Run these GitNexus MCP calls:

```bash
impact(target="compose_thread", direction="upstream", repo="happyranch")
impact(target="compose_thread_as_agent", direction="upstream", repo="happyranch")
impact(target="reply_thread_endpoint", direction="upstream", repo="happyranch")
impact(target="_send_thread_message_inprocess", direction="upstream", repo="happyranch")
impact(target="log_thread_message_sent", direction="upstream", repo="happyranch")
```

Expected: route consumers, CLI/web API mirrors, and route tests are affected. Report HIGH or CRITICAL risk before editing.

- [ ] **Step 2: Write failing route tests**

Add tests to `tests/daemon/test_threads_routes.py`:

```python
def test_thread_send_accepts_attachment_only(client, auth_headers, org_state) -> None:
    org_state.artifact_store.put("THR-001-report.pdf", b"pdf")
    thread_id = _seed_open_thread(org_state, participants=["dev_agent"])

    r = client.post(
        f"/api/v1/orgs/alpha/threads/{thread_id}/send",
        headers=auth_headers,
        json={
            "body_markdown": "",
            "attachments": [
                {"artifact_name": "THR-001-report.pdf", "display_name": "report.pdf"}
            ],
        },
    )

    assert r.status_code == 200
    messages = org_state.db.list_thread_messages(thread_id)
    assert messages[-1].body_markdown is None
    assert messages[-1].attachments[0].artifact_name == "THR-001-report.pdf"


def test_thread_send_rejects_unknown_attachment(client, auth_headers, org_state) -> None:
    thread_id = _seed_open_thread(org_state, participants=["dev_agent"])

    r = client.post(
        f"/api/v1/orgs/alpha/threads/{thread_id}/send",
        headers=auth_headers,
        json={
            "body_markdown": "see file",
            "attachments": [{"artifact_name": "missing.pdf"}],
        },
    )

    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "artifact_not_found"


def test_thread_send_rejects_empty_without_attachments(client, auth_headers, org_state) -> None:
    thread_id = _seed_open_thread(org_state, participants=["dev_agent"])

    r = client.post(
        f"/api/v1/orgs/alpha/threads/{thread_id}/send",
        headers=auth_headers,
        json={"body_markdown": "   ", "attachments": []},
    )

    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "empty_body"


def test_thread_messages_response_includes_attachments(client, auth_headers, org_state) -> None:
    org_state.artifact_store.put("THR-001-report.pdf", b"pdf")
    thread_id = _seed_open_thread(org_state, participants=["dev_agent"])
    org_state.db.append_thread_message(
        thread_id=thread_id,
        speaker="founder",
        kind=ThreadMessageKind.MESSAGE,
        attachments=[
            ThreadAttachment(
                artifact_name="THR-001-report.pdf",
                display_name="report.pdf",
                size_bytes=3,
                content_type=None,
                uploaded_by="founder",
            )
        ],
    )

    r = client.get(
        f"/api/v1/orgs/alpha/threads/{thread_id}/messages",
        headers=auth_headers,
    )

    assert r.status_code == 200
    assert r.json()["messages"][0]["attachments"] == [
        {
            "artifact_name": "THR-001-report.pdf",
            "display_name": "report.pdf",
            "size_bytes": 3,
            "content_type": None,
            "uploaded_by": "founder",
        }
    ]
```

If there is no `_seed_open_thread` helper, create one in the test file:

```python
def _seed_open_thread(org_state, *, participants: list[str]) -> str:
    thread_id = org_state.db.next_thread_id()
    org_state.db.insert_thread(ThreadRecord(id=thread_id, subject="Files"))
    for agent in participants:
        org_state.db.add_thread_participant(thread_id, agent, added_by="founder")
    return thread_id
```

- [ ] **Step 3: Run route tests and confirm failure**

Run:

```bash
uv run pytest tests/daemon/test_threads_routes.py -k "attachment or empty_without_attachments" -v
```

Expected: FAIL because request bodies do not accept or return attachments yet.

- [ ] **Step 4: Add request models and validation helpers**

In `runtime/daemon/routes/threads.py`, import `ArtifactStore`, `ArtifactNotFound`, `InvalidArtifactName`, and `ThreadAttachment`.

Add near body models:

```python
MAX_THREAD_ATTACHMENTS = 5


class AttachmentRefBody(BaseModel):
    artifact_name: str
    display_name: str | None = None


def _validate_display_name(name: str) -> None:
    if not name or len(name) > 200:
        raise HTTPException(
            status_code=422,
            detail={"code": "invalid_attachment_display_name", "name": name},
        )
    if "/" in name or "\\" in name or any(ord(ch) < 32 for ch in name):
        raise HTTPException(
            status_code=422,
            detail={"code": "invalid_attachment_display_name", "name": name},
        )


def _attachments_preview(attachments: list[ThreadAttachment]) -> str:
    if not attachments:
        return ""
    names = ", ".join(a.display_name for a in attachments[:3])
    suffix = "" if len(attachments) <= 3 else f" +{len(attachments) - 3} more"
    return f"Attached {len(attachments)} file{'s' if len(attachments) != 1 else ''}: {names}{suffix}"


def _normalize_attachments(org, refs: list[AttachmentRefBody] | None, *, uploaded_by: str) -> list[ThreadAttachment]:
    if not refs:
        return []
    if len(refs) > MAX_THREAD_ATTACHMENTS:
        raise HTTPException(
            status_code=422,
            detail={"code": "too_many_attachments", "max": MAX_THREAD_ATTACHMENTS},
        )
    seen: set[str] = set()
    store = ArtifactStore(OrgPaths(org.root).artifacts_dir)
    out: list[ThreadAttachment] = []
    for ref in refs:
        artifact_name = ref.artifact_name.strip()
        if artifact_name in seen:
            raise HTTPException(
                status_code=422,
                detail={"code": "duplicate_attachment", "artifact_name": artifact_name},
            )
        seen.add(artifact_name)
        display_name = (ref.display_name or artifact_name).strip()
        _validate_display_name(display_name)
        try:
            path = store.path_for(artifact_name)
        except InvalidArtifactName as exc:
            raise HTTPException(
                status_code=400,
                detail={"code": "invalid_artifact_name", "name": artifact_name, "message": str(exc)},
            ) from exc
        if not path.exists():
            raise HTTPException(
                status_code=404,
                detail={"code": "artifact_not_found", "name": artifact_name},
            )
        stat = path.stat()
        out.append(
            ThreadAttachment(
                artifact_name=artifact_name,
                display_name=display_name,
                size_bytes=stat.st_size,
                content_type=None,
                uploaded_by=uploaded_by,
            )
        )
    return out


def _normalize_message_body(body_markdown: str | None, attachments: list[ThreadAttachment]) -> str | None:
    body_text = (body_markdown or "").strip()
    if not body_text and not attachments:
        raise HTTPException(status_code=422, detail={"code": "empty_body"})
    return body_text or None
```

- [ ] **Step 5: Add attachments to request bodies**

Update body models:

```python
class ComposeBody(BaseModel):
    subject: str
    recipients: list[str]
    body_markdown: str = ""
    attachments: list[AttachmentRefBody] = Field(default_factory=list)
    forwarded_from_id: str | None = None
    forwarded_from_kind: str | None = None


class ComposeAsAgentBody(BaseModel):
    composer: str
    subject: str
    recipients: list[str]
    body_markdown: str = ""
    attachments: list[AttachmentRefBody] = Field(default_factory=list)
    task_id: str | None = None
    session_id: str | None = None
    talk_id: str | None = None


class ReplyBody(BaseModel):
    thread_id: str
    invocation_token: str
    speaker: str
    body_markdown: str = ""
    attachments: list[AttachmentRefBody] = Field(default_factory=list)
    in_response_to_seq: int


class SendBody(BaseModel):
    body_markdown: str = ""
    attachments: list[AttachmentRefBody] = Field(default_factory=list)
```

Also add `Field` to the Pydantic imports.

- [ ] **Step 6: Pass attachments through write paths**

In each write path, replace body-only validation with:

```python
attachments = _normalize_attachments(org, body.attachments, uploaded_by="founder")
body_text = _normalize_message_body(body.body_markdown, attachments)
```

Use `uploaded_by=body.composer` for compose-as-agent and `uploaded_by=body.speaker` for reply.

Every `append_thread_message` call for user/agent message rows should pass `attachments=attachments`.

Use preview fallback:

```python
preview = body_text or _attachments_preview(attachments)
```

Pass `preview=preview` into `_publish_thread_event`.

- [ ] **Step 7: Update read serialization**

In `_msg_to_dict`, add:

```python
        "attachments": [
            attachment.model_dump(mode="json") for attachment in m.attachments
        ],
```

- [ ] **Step 8: Update audit payload**

Change `AuditLogger.log_thread_message_sent` signature:

```python
    def log_thread_message_sent(
        self,
        thread_id: str,
        *,
        seq: int,
        speaker: str,
        kind: str,
        attachment_names: list[str] | None = None,
    ) -> None:
        payload: dict[str, object] = {"seq": seq, "kind": kind}
        if attachment_names:
            payload["attachment_count"] = len(attachment_names)
            payload["attachment_names"] = attachment_names
        self._db.insert_audit_log(
            task_id=thread_id,
            agent=speaker,
            action="thread_message_sent",
            payload=payload,
        )
```

Update message send call sites:

```python
attachment_names=[a.artifact_name for a in attachments]
```

Keep system/decline callers passing no attachment names.

- [ ] **Step 9: Run focused tests**

Run:

```bash
uv run pytest tests/daemon/test_threads_routes.py -k "attachment or empty_without_attachments" -v
uv run pytest tests/test_audit_logger.py -k thread_message_sent -v
```

Expected: PASS.

- [ ] **Step 10: Run broader thread route tests**

Run:

```bash
uv run pytest tests/daemon/test_threads_routes.py tests/test_thread_db.py -v
```

Expected: PASS.

- [ ] **Step 11: Commit**

Run:

```bash
git add runtime/daemon/routes/threads.py runtime/infrastructure/audit_logger.py tests/daemon/test_threads_routes.py tests/test_audit_logger.py
git commit -m "feat(threads): accept artifact attachments"
```

## Task 3: Prompt, Transcript, Protocol Skill, And CLI Display

**Files:**
- Modify: `runtime/daemon/thread_runner.py`
- Modify: `runtime/infrastructure/thread_store.py`
- Modify: `protocol/skills/thread/SKILL.md`
- Modify: `cli/commands/threads.py`
- Test: `tests/test_thread_runner.py`
- Test: `tests/test_thread_store.py`
- Test: `tests/test_cli_threads.py`
- Test: `tests/test_skills.py`

- [ ] **Step 1: Run impact analysis**

Run these GitNexus MCP calls:

```bash
impact(target="_render_message", direction="upstream", repo="happyranch")
impact(target="render_transcript_body", direction="upstream", repo="happyranch")
impact(target="cmd_threads_show", direction="upstream", repo="happyranch")
```

Expected: thread runner, transcript tests, CLI tests, and web route-independent displays are affected.

- [ ] **Step 2: Write failing prompt and transcript tests**

Add to `tests/test_thread_runner.py`:

```python
def test_render_message_includes_attachments() -> None:
    msg = ThreadMessage(
        thread_id="THR-001",
        seq=1,
        speaker="founder",
        kind=ThreadMessageKind.MESSAGE,
        body_markdown="see attached",
        attachments=[
            ThreadAttachment(
                artifact_name="THR-001-report.pdf",
                display_name="report.pdf",
                size_bytes=123,
                content_type=None,
                uploaded_by="founder",
            )
        ],
    )

    rendered = _render_message(msg)

    assert "Attachments:" in rendered
    assert "- report.pdf (`artifact:THR-001-report.pdf`, 123 bytes)" in rendered
```

Add to `tests/test_thread_store.py`:

```python
def test_render_transcript_body_includes_attachments() -> None:
    msg = ThreadMessage(
        thread_id="THR-001",
        seq=1,
        speaker="founder",
        kind=ThreadMessageKind.MESSAGE,
        body_markdown=None,
        attachments=[
            ThreadAttachment(
                artifact_name="THR-001-report.pdf",
                display_name="report.pdf",
                size_bytes=123,
                content_type=None,
                uploaded_by="founder",
            )
        ],
    )

    rendered = render_transcript_body([msg])

    assert "Attachments:" in rendered
    assert "- [report.pdf](artifact:THR-001-report.pdf) (123 bytes)" in rendered
```

- [ ] **Step 3: Run tests and confirm failure**

Run:

```bash
uv run pytest tests/test_thread_runner.py::test_render_message_includes_attachments tests/test_thread_store.py::test_render_transcript_body_includes_attachments -v
```

Expected: FAIL because attachments are not rendered.

- [ ] **Step 4: Add prompt attachment rendering**

In `runtime/daemon/thread_runner.py`, add:

```python
def _render_attachments_for_prompt(m: ThreadMessage) -> str:
    if not m.attachments:
        return ""
    lines = ["Attachments:"]
    for attachment in m.attachments:
        size = (
            f", {attachment.size_bytes} bytes"
            if attachment.size_bytes is not None
            else ""
        )
        lines.append(
            f"- {attachment.display_name} "
            f"(`artifact:{attachment.artifact_name}`{size})"
        )
    return "\n".join(lines)
```

In `_render_message`, for `ThreadMessageKind.MESSAGE`, change the body assembly to include attachments:

```python
        attachments = _render_attachments_for_prompt(m)
        return "\n".join(filter(None, [head, "", body, attachments])) + "\n---"
```

- [ ] **Step 5: Add transcript attachment rendering**

In `runtime/infrastructure/thread_store.py`, add:

```python
def _render_attachments_for_transcript(m) -> list[str]:
    attachments = getattr(m, "attachments", []) or []
    if not attachments:
        return []
    lines = ["Attachments:"]
    for attachment in attachments:
        size = (
            f" ({attachment.size_bytes} bytes)"
            if attachment.size_bytes is not None
            else ""
        )
        lines.append(
            f"- [{attachment.display_name}](artifact:{attachment.artifact_name}){size}"
        )
    return lines
```

In the `kind_name == "message"` branch, after `lines.append(m.body_markdown or "")`, add:

```python
            lines.extend(_render_attachments_for_transcript(m))
```

- [ ] **Step 6: Update protocol skill**

In `protocol/skills/thread/SKILL.md`, add a section under Reply:

```markdown
### Attach files to a reply

If your reply needs a file, upload it as a shared artifact first:

   happyranch artifacts put /tmp/file.ext --agent <your name> --name <artifact-name> --org <slug>

Then include the artifact reference in your reply payload:

   "attachments": [{"artifact_name": "<artifact-name>", "display_name": "file.ext"}]

Recipients download attachments with:

    happyranch artifacts get <artifact-name> --output /tmp/file.ext --org <slug>

The terminal callback remains the normal single-line command:

    happyranch threads reply --org <slug> --thread-id <id> --from-file /tmp/thread-reply-<id>-<seq>.json

Do not use shell separators or multiline continuations.
```

- [ ] **Step 7: Update CLI show output**

In `cli/commands/threads.py`, inside `cmd_threads_show`, after printing `body_markdown`, print attachments:

```python
        for attachment in m.get("attachments", []):
            size = attachment.get("size_bytes")
            size_text = f" ({size}B)" if size is not None else ""
            print(
                f"  attachment: {attachment['display_name']} "
                f"[artifact:{attachment['artifact_name']}]{size_text}"
            )
```

- [ ] **Step 8: Run focused tests**

Run:

```bash
uv run pytest tests/test_thread_runner.py::test_render_message_includes_attachments tests/test_thread_store.py::test_render_transcript_body_includes_attachments tests/test_skills.py -k thread -v
```

Expected: PASS.

- [ ] **Step 9: Run broader thread tests**

Run:

```bash
uv run pytest tests/test_thread_runner.py tests/test_thread_store.py tests/test_thread_forward.py -v
```

Expected: PASS.

- [ ] **Step 10: Commit**

Run:

```bash
git add runtime/daemon/thread_runner.py runtime/infrastructure/thread_store.py protocol/skills/thread/SKILL.md cli/commands/threads.py tests/test_thread_runner.py tests/test_thread_store.py tests/test_cli_threads.py tests/test_skills.py
git commit -m "feat(threads): render attachments in prompts and transcripts"
```

## Task 4: CLI Upload Convenience

**Files:**
- Modify: `cli/commands/threads.py`
- Create: `tests/test_cli_threads.py`
- Test: `tests/test_cli_artifacts.py` if shared helper behavior changes

- [ ] **Step 1: Run impact analysis**

Run GitNexus MCP calls:

```bash
impact(target="cmd_threads_compose", direction="upstream", repo="happyranch")
impact(target="cmd_threads_send", direction="upstream", repo="happyranch")
impact(target="cmd_threads_reply", direction="upstream", repo="happyranch")
```

Expected: CLI parser/tests and agent callback docs are affected.

- [ ] **Step 2: Write failing CLI tests**

Create `tests/test_cli_threads.py` with:

```python
from __future__ import annotations

import argparse
import json
from pathlib import Path
from unittest.mock import Mock


def _json_response(body: dict) -> Mock:
    r = Mock()
    r.status_code = 200
    r.json.return_value = body
    return r


def test_threads_send_attach_uploads_and_merges_refs(tmp_path, monkeypatch) -> None:
    from cli.main import cmd_threads_send

    payload_path = tmp_path / "msg.json"
    payload_path.write_text('{"body_markdown":"see attached"}', encoding="utf-8")
    local = tmp_path / "report.pdf"
    local.write_bytes(b"pdf")
    fake = Mock()
    fake.put_artifact.return_value = {
        "name": "THR-001-report.pdf",
        "size_bytes": 3,
        "modified_at": "2026-06-09T00:00:00Z",
    }
    fake.post.return_value = _json_response({"thread_id": "THR-001", "seq": 2})
    monkeypatch.setattr("cli.commands.threads.OpcClient.from_env", lambda: fake)
    monkeypatch.setattr("cli.commands.threads._shared._fetch_available_orgs", lambda _client: ["alpha"])

    args = argparse.Namespace(
        org="alpha",
        thread_id="THR-001",
        from_file=payload_path,
        attach=[local],
    )

    cmd_threads_send(args)

    fake.put_artifact.assert_called_once()
    sent = fake.post.call_args.kwargs["json"]
    assert sent["body_markdown"] == "see attached"
    assert sent["attachments"] == [
        {"artifact_name": "THR-001-report.pdf", "display_name": "report.pdf"}
    ]


def test_threads_reply_attach_uses_speaker_for_upload_attribution(tmp_path, monkeypatch) -> None:
    from cli.main import cmd_threads_reply

    payload_path = tmp_path / "reply.json"
    payload_path.write_text(
        json.dumps({
            "thread_id": "THR-001",
            "invocation_token": "tok",
            "speaker": "dev_agent",
            "body_markdown": "",
            "in_response_to_seq": 1,
        }),
        encoding="utf-8",
    )
    local = tmp_path / "analysis.md"
    local.write_text("analysis", encoding="utf-8")
    fake = Mock()
    fake.put_artifact.return_value = {
        "name": "THR-001-analysis.md",
        "size_bytes": 8,
        "modified_at": "2026-06-09T00:00:00Z",
    }
    fake.post.return_value = _json_response({"thread_id": "THR-001", "seq": 2})
    monkeypatch.setattr("cli.commands.threads.OpcClient.from_env", lambda: fake)
    monkeypatch.setattr("cli.commands.threads._shared._fetch_available_orgs", lambda _client: ["alpha"])

    args = argparse.Namespace(
        org="alpha",
        thread_id="THR-001",
        from_file=payload_path,
        attach=[local],
    )

    cmd_threads_reply(args)

    assert fake.put_artifact.call_args.kwargs["agent"] == "dev_agent"
    sent = fake.post.call_args.kwargs["json"]
    assert sent["attachments"] == [
        {"artifact_name": "THR-001-analysis.md", "display_name": "analysis.md"}
    ]
def test_threads_compose_attach_uploads_with_founder_attribution(tmp_path, monkeypatch) -> None:
    from cli.main import cmd_threads_compose

    local = tmp_path / "data.csv"
    local.write_text("a,b\n", encoding="utf-8")
    fake = Mock()
    fake.put_artifact.return_value = {
        "name": "thread-draft-20260609T000000Z-data.csv",
        "size_bytes": 4,
        "modified_at": "2026-06-09T00:00:00Z",
    }
    fake.post.return_value = _json_response(
        {"thread_id": "THR-001", "started_at": "2026-06-09T00:00:00Z", "pending_replies": []}
    )
    monkeypatch.setattr("cli.commands.threads.OpcClient.from_env", lambda: fake)
    monkeypatch.setattr("cli.commands.threads._shared._fetch_available_orgs", lambda _client: ["alpha"])

    args = argparse.Namespace(
        org="alpha",
        task_id=None,
        talk_id=None,
        session_id=None,
        from_file=None,
        subject="Review data",
        recipients="dev_agent",
        body="",
        attach=[local],
    )

    cmd_threads_compose(args)

    assert fake.put_artifact.call_args.kwargs["agent"] == "founder"
    sent = fake.post.call_args.kwargs["json"]
    assert sent["attachments"] == [
        {
            "artifact_name": "thread-draft-20260609T000000Z-data.csv",
            "display_name": "data.csv",
        }
    ]
```

- [ ] **Step 3: Run tests and confirm failure**

Run:

```bash
uv run pytest tests/test_cli_threads.py -k "attach" -v
```

Expected: FAIL because parser/commands do not accept `attach`.

- [ ] **Step 4: Add CLI attachment helpers**

In `cli/commands/threads.py`, add imports:

```python
from datetime import datetime, timezone
import re
```

Add helpers near the top:

```python
_SAFE_ARTIFACT_CHARS = re.compile(r"[^A-Za-z0-9._-]+")


def _safe_artifact_basename(path: Path) -> str:
    cleaned = _SAFE_ARTIFACT_CHARS.sub("-", path.name).strip(".-")
    return cleaned or "attachment.bin"


def _artifact_name_for_attach(thread_id: str | None, path: Path) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    prefix = thread_id or f"thread-draft-{stamp}"
    return f"{prefix}-{stamp}-{_safe_artifact_basename(path)}"


def _merge_uploaded_attachments(
    *,
    client: OpcClient,
    slug: str,
    payload: dict,
    attach_paths: list[Path] | None,
    agent: str,
    thread_id: str | None,
) -> dict:
    refs = list(payload.get("attachments") or [])
    for path in attach_paths or []:
        name = _artifact_name_for_attach(thread_id, path)
        info = client.put_artifact(
            slug=slug,
            local_path=path,
            name=name,
            agent=agent,
        )
        refs.append({"artifact_name": info["name"], "display_name": path.name})
    payload["attachments"] = refs
    return payload
```

- [ ] **Step 5: Wire helpers into compose/send/reply**

In founder compose path, after building payload:

```python
    payload = _merge_uploaded_attachments(
        client=client,
        slug=slug,
        payload=payload,
        attach_paths=getattr(args, "attach", None),
        agent="founder",
        thread_id=None,
    )
```

In `cmd_threads_send`, after reading payload:

```python
    payload = _merge_uploaded_attachments(
        client=client,
        slug=slug,
        payload=payload,
        attach_paths=getattr(args, "attach", None),
        agent="founder",
        thread_id=args.thread_id,
    )
```

In `cmd_threads_reply`, after reading body and resolving `thread_id`:

```python
    body = _merge_uploaded_attachments(
        client=client,
        slug=slug,
        payload=body,
        attach_paths=getattr(args, "attach", None),
        agent=body.get("speaker", ""),
        thread_id=thread_id,
    )
```

For agent compose-as-agent path in `cmd_threads_compose`, use:

```python
        payload = _merge_uploaded_attachments(
            client=client,
            slug=slug,
            payload=payload,
            attach_paths=getattr(args, "attach", None),
            agent=payload.get("composer", ""),
            thread_id=None,
        )
```

- [ ] **Step 6: Add parser flags**

In `register(sub)`, add to compose, send, and reply parsers:

```python
    p_threads_compose.add_argument("--attach", action="append", type=Path, default=[])
    p_threads_send.add_argument("--attach", action="append", type=Path, default=[])
    p_threads_reply.add_argument("--attach", action="append", type=Path, default=[])
```

- [ ] **Step 7: Update thread skill for attach convenience**

Now that the parser accepts `--attach`, update `protocol/skills/thread/SKILL.md` in the Attach files section to add:

```markdown
Convenience upload during the terminal callback is also supported:

    happyranch threads reply --org <slug> --thread-id <id> --from-file /tmp/thread-reply-<id>-<seq>.json --attach /tmp/file.ext
```

Update `tests/test_skills.py` to assert the thread skill documents `--attach`.

- [ ] **Step 8: Run CLI tests**

Run:

```bash
uv run pytest tests/test_cli_threads.py tests/test_cli_artifacts.py tests/test_skills.py -v
```

Expected: PASS.

- [ ] **Step 9: Commit**

Run:

```bash
git add cli/commands/threads.py protocol/skills/thread/SKILL.md tests/test_cli_threads.py tests/test_skills.py
git commit -m "feat(cli): attach files to thread messages"
```

## Task 5: Web API Types And Artifact Upload Helper

**Files:**
- Create: `web/src/lib/api/artifacts.ts`
- Modify: `web/src/lib/api/index.ts`
- Modify: `web/src/lib/api/types.ts`
- Modify: `web/src/lib/api/threads.ts`
- Test: `web/src/lib/api/threads.test.ts`
- Test: create `web/src/lib/api/artifacts.test.ts`

- [ ] **Step 1: Write failing web API tests**

Create `web/src/lib/api/artifacts.test.ts`:

```typescript
import { http, HttpResponse } from 'msw';
import { describe, expect, test } from 'vitest';
import { server } from '../../test/server';
import { uploadArtifact } from './artifacts';

const SLUG = 'alpha';

const seedToken = () => sessionStorage.setItem('happyranch.token', 'tok');

describe('artifacts api mirror', () => {
  test('uploadArtifact posts multipart data with agent and name', async () => {
    seedToken();
    let url = '';
    let auth = '';
    let form: FormData | null = null;
    server.use(
      http.post(`/api/v1/orgs/${SLUG}/artifacts`, async ({ request }) => {
        url = request.url;
        auth = request.headers.get('authorization') ?? '';
        form = await request.formData();
        return HttpResponse.json({
          name: 'THR-001-report.pdf',
          size_bytes: 3,
          modified_at: '2026-06-09T00:00:00Z',
        });
      }),
    );

    const file = new File(['pdf'], 'report.pdf', { type: 'application/pdf' });
    const result = await uploadArtifact(SLUG, {
      file,
      name: 'THR-001-report.pdf',
      agent: 'founder',
    });

    expect(result.name).toBe('THR-001-report.pdf');
    expect(url).toContain('agent=founder');
    expect(url).toContain('name=THR-001-report.pdf');
    expect(auth).toBe('Bearer tok');
    expect(form?.get('file')).toBeInstanceOf(File);
  });
});
```

Add to `web/src/lib/api/threads.test.ts`:

```typescript
test('sendThreadFollowUp can include attachments', async () => {
  seedToken();
  let received: unknown = null;
  server.use(
    http.post(`/api/v1/orgs/${SLUG}/threads/THR-001/send`, async ({ request: req }) => {
      received = await req.json();
      return HttpResponse.json({ thread_id: 'THR-001', seq: 2 });
    }),
  );

  await sendThreadFollowUp(SLUG, 'THR-001', {
    body_markdown: '',
    attachments: [{ artifact_name: 'THR-001-report.pdf', display_name: 'report.pdf' }],
  });

  expect(received).toEqual({
    body_markdown: '',
    attachments: [{ artifact_name: 'THR-001-report.pdf', display_name: 'report.pdf' }],
  });
});
```

- [ ] **Step 2: Run tests and confirm failure**

Run:

```bash
cd web && npm test -- --run src/lib/api/artifacts.test.ts src/lib/api/threads.test.ts
```

Expected: FAIL because `artifacts.ts` and attachment types do not exist.

- [ ] **Step 3: Add web artifact API helper**

Create `web/src/lib/api/artifacts.ts`:

```typescript
import { clearToken, getToken } from '../auth';
import { API_PREFIX, ApiError } from './client';

export interface ArtifactInfo {
  name: string;
  size_bytes: number;
  modified_at: string;
}

export interface UploadArtifactArgs {
  file: File;
  name?: string;
  agent: string;
}

function parseArtifactError(status: number, body: unknown): ApiError {
  let code: string | null = null;
  let detail: unknown = body;
  if (
    body &&
    typeof body === 'object' &&
    'detail' in body &&
    (body as { detail: unknown }).detail !== undefined
  ) {
    detail = (body as { detail: unknown }).detail;
    if (
      detail &&
      typeof detail === 'object' &&
      'code' in detail &&
      typeof (detail as { code?: unknown }).code === 'string'
    ) {
      code = (detail as { code: string }).code;
    }
  }
  return new ApiError(status, code, detail);
}

async function uploadWithToken(
  slug: string,
  args: UploadArtifactArgs,
  token: string,
): Promise<Response> {
  const params = new URLSearchParams({ agent: args.agent });
  if (args.name) params.set('name', args.name);
  const form = new FormData();
  form.set('file', args.file, args.name ?? args.file.name);
  return fetch(`${API_PREFIX}/orgs/${slug}/artifacts?${params.toString()}`, {
    method: 'POST',
    headers: {
      Authorization: `Bearer ${token}`,
      Accept: 'application/json',
    },
    body: form,
    credentials: 'same-origin',
  });
}

export async function uploadArtifact(
  slug: string,
  args: UploadArtifactArgs,
): Promise<ArtifactInfo> {
  let token = await getToken();
  let res = await uploadWithToken(slug, args, token);
  if (res.status === 401) {
    clearToken();
    token = await getToken();
    res = await uploadWithToken(slug, args, token);
  }
  const text = await res.text();
  const body = text ? JSON.parse(text) : null;
  if (!res.ok) throw parseArtifactError(res.status, body);
  return body as ArtifactInfo;
}

export function artifactDownloadPath(slug: string, artifactName: string): string {
  return `${API_PREFIX}/orgs/${slug}/artifacts/${encodeURIComponent(artifactName)}`;
}
```

Update `web/src/lib/api/index.ts`:

```typescript
export * as artifacts from './artifacts';
```

- [ ] **Step 4: Add attachment types to web API**

In `web/src/lib/api/types.ts`, add:

```typescript
export interface ThreadAttachment {
  artifact_name: string;
  display_name: string;
  size_bytes: number | null;
  content_type: string | null;
  uploaded_by: string;
}

export interface ThreadAttachmentRef {
  artifact_name: string;
  display_name?: string;
}
```

Update `ThreadMessage`:

```typescript
  attachments: ThreadAttachment[];
```

In `web/src/lib/api/threads.ts`, import `ThreadAttachmentRef` and add to request bodies:

```typescript
export interface ComposeThreadBody {
  subject: string;
  recipients: string[];
  body_markdown: string;
  attachments?: ThreadAttachmentRef[];
  forwarded_from_id?: string;
  forwarded_from_kind?: 'thread' | 'talk';
}
```

Change send body:

```typescript
export const sendThreadFollowUp = (
  slug: string,
  threadId: string,
  body: { body_markdown: string; attachments?: ThreadAttachmentRef[] },
): Promise<{ seq: number; thread_id: string }> =>
  request(`/orgs/${slug}/threads/${threadId}/send`, { method: 'POST', body });
```

- [ ] **Step 5: Run web API tests**

Run:

```bash
cd web && npm test -- --run src/lib/api/artifacts.test.ts src/lib/api/threads.test.ts
```

Expected: PASS.

- [ ] **Step 6: Commit**

Run:

```bash
git add web/src/lib/api/artifacts.ts web/src/lib/api/index.ts web/src/lib/api/types.ts web/src/lib/api/threads.ts web/src/lib/api/artifacts.test.ts web/src/lib/api/threads.test.ts
git commit -m "feat(web): add artifact attachment API types"
```

## Task 6: Web Composer Upload UX And Message Rendering

**Files:**
- Modify: `web/src/design-system/patterns/Composer.tsx`
- Modify: `web/src/design-system/patterns/MessageBubble.tsx`
- Modify: `web/src/features/threads/ThreadsPage.tsx`
- Modify: `web/src/features/threads/NewThreadDialog.tsx`
- Modify: `web/src/design-system/providers/_mock-threads.ts`
- Test: `web/src/features/threads/write-path.test.tsx`
- Test: `web/src/features/threads/write-path.test.tsx`
- Test: `web/src/design-system/patterns/Composer.test.tsx`

- [ ] **Step 1: Write failing write-path tests**

Add to `web/src/features/threads/write-path.test.tsx`:

```typescript
test('Composer uploads attachment before sending follow-up', async () => {
  sessionStorage.setItem('happyranch.token', 'tok');
  stubBaseHandlers();
  let uploadHit = false;
  let sent: unknown = null;
  server.use(
    http.get(`/api/v1/orgs/${SLUG}/threads/THR-001`, () =>
      HttpResponse.json({
        thread_id: 'THR-001',
        subject: 'Existing thread',
        status: 'open',
        started_at: 'now',
        archived_at: null,
        forwarded_from_id: null,
        forwarded_from_kind: null,
        turn_cap: 500,
        turns_used: 1,
        summary: null,
        transcript_path: null,
        participants: ['agent_a'],
        messages: [],
      }),
    ),
    http.get(`/api/v1/orgs/${SLUG}/threads/THR-001/messages`, () =>
      HttpResponse.json({ messages: [] }),
    ),
    http.get(`/api/v1/orgs/${SLUG}/threads/THR-001/tail`, () =>
      HttpResponse.text('', { headers: { 'content-type': 'text/event-stream' } }),
    ),
    http.post(`/api/v1/orgs/${SLUG}/artifacts`, async () => {
      uploadHit = true;
      return HttpResponse.json({
        name: 'THR-001-report.pdf',
        size_bytes: 3,
        modified_at: '2026-06-09T00:00:00Z',
      });
    }),
    http.post(`/api/v1/orgs/${SLUG}/threads/THR-001/send`, async ({ request: req }) => {
      sent = await req.json();
      return HttpResponse.json({ thread_id: 'THR-001', seq: 2 });
    }),
  );

  const user = userEvent.setup();
  renderWithProviders(<AppRoutes />, { route: `/orgs/${SLUG}/threads/THR-001` });
  const file = new File(['pdf'], 'report.pdf', { type: 'application/pdf' });

  await user.upload(await screen.findByLabelText(/Attach files/i), file);
  await user.click(screen.getByRole('button', { name: /^Send$/i }));

  await waitFor(() => expect(uploadHit).toBe(true));
  await waitFor(() =>
    expect(sent).toEqual({
      body_markdown: '',
      attachments: [{ artifact_name: 'THR-001-report.pdf', display_name: 'report.pdf' }],
    }),
  );
});


test('message bubble renders attachment download link', async () => {
  sessionStorage.setItem('happyranch.token', 'tok');
  stubBaseHandlers();
  server.use(
    http.get(`/api/v1/orgs/${SLUG}/threads/THR-001`, () =>
      HttpResponse.json({
        thread_id: 'THR-001',
        subject: 'Existing thread',
        status: 'open',
        started_at: 'now',
        archived_at: null,
        forwarded_from_id: null,
        forwarded_from_kind: null,
        turn_cap: 500,
        turns_used: 1,
        summary: null,
        transcript_path: null,
        participants: ['agent_a'],
        messages: [],
      }),
    ),
    http.get(`/api/v1/orgs/${SLUG}/threads/THR-001/messages`, () =>
      HttpResponse.json({
        messages: [
          {
            seq: 1,
            speaker: 'founder',
            kind: 'message',
            body_markdown: null,
            decline_reason: null,
            system_payload: null,
            created_at: '2026-06-09T00:00:00Z',
            responder_status: [],
            attachments: [
              {
                artifact_name: 'THR-001-report.pdf',
                display_name: 'report.pdf',
                size_bytes: 3,
                content_type: null,
                uploaded_by: 'founder',
              },
            ],
          },
        ],
      }),
    ),
    http.get(`/api/v1/orgs/${SLUG}/threads/THR-001/tail`, () =>
      HttpResponse.text('', { headers: { 'content-type': 'text/event-stream' } }),
    ),
  );

  renderWithProviders(<AppRoutes />, { route: `/orgs/${SLUG}/threads/THR-001` });

  expect(await screen.findByRole('link', { name: /report\.pdf/i })).toHaveAttribute(
    'href',
    '/api/v1/orgs/alpha/artifacts/THR-001-report.pdf',
  );
});
```

- [ ] **Step 2: Run tests and confirm failure**

Run:

```bash
cd web && npm test -- --run src/features/threads/write-path.test.tsx
```

Expected: FAIL because there is no attach input or rendering.

- [ ] **Step 3: Extend MessageBubble props and rendering**

In `web/src/design-system/patterns/MessageBubble.tsx`, use a prop-based href builder so the pattern remains pure:

```typescript
import type { ThreadAttachment } from '@/lib/api/types';
```

Add props:

```typescript
  attachments?: ThreadAttachment[];
  attachmentHref?: (artifactName: string) => string;
```

Render below markdown for non-system/non-decline:

```tsx
      {attachments && attachments.length > 0 && (
        <div className="mt-3 flex flex-wrap gap-2">
          {attachments.map((attachment) => (
            <a
              key={attachment.artifact_name}
              href={attachmentHref?.(attachment.artifact_name) ?? '#'}
              className="border-border-subtle bg-surface text-caption hover:bg-surface-hover inline-flex items-center gap-2 rounded-md border px-2 py-1"
            >
              <span>{attachment.display_name}</span>
              {attachment.size_bytes !== null && (
                <span className="text-text-muted">{attachment.size_bytes}B</span>
              )}
            </a>
          ))}
        </div>
      )}
```

- [ ] **Step 4: Extend Composer with file input**

In `web/src/design-system/patterns/Composer.tsx`, add:

```typescript
interface PendingAttachment {
  id: string;
  file: File;
}
```

Add props:

```typescript
  attachments?: PendingAttachment[];
  onAttachmentsChange?: (attachments: PendingAttachment[]) => void;
```

Add file input and allow attachment-only send:

```tsx
      <input
        aria-label="Attach files"
        type="file"
        multiple
        disabled={disabled || pending}
        onChange={(event) => {
          const files = Array.from(event.currentTarget.files ?? []).slice(0, 5);
          onAttachmentsChange?.([
            ...(attachments ?? []),
            ...files.map((file) => ({
              id: `${file.name}-${file.size}-${file.lastModified}`,
              file,
            })),
          ].slice(0, 5));
          event.currentTarget.value = '';
        }}
      />
```

Change submit guard:

```typescript
    if ((!draft.trim() && !(attachments?.length)) || disabled || pending) return;
```

Change `onSend` prop type:

```typescript
  onSend: (markdown: string, attachments: PendingAttachment[]) => unknown | Promise<unknown>;
```

Call:

```typescript
      await onSend(draft, attachments ?? []);
      onAttachmentsChange?.([]);
```

Change button disabled:

```tsx
disabled={disabled || (!draft.trim() && !(attachments?.length)) || pending}
```

- [ ] **Step 5: Add upload orchestration in ThreadsPage**

In `web/src/features/threads/ThreadsPage.tsx`, import:

```typescript
import { artifacts as artifactsApi } from '@/lib/api';
import type { ThreadAttachmentRef } from '@/lib/api/types';
```

Add state:

```typescript
const [pendingAttachments, setPendingAttachments] = useState<PendingAttachment[]>([]);
```

Add helper:

```typescript
function safeArtifactName(threadId: string, file: File): string {
  const stamp = new Date().toISOString().replace(/[-:]/g, '').replace(/\.\d{3}Z$/, 'Z');
  const basename = file.name.replace(/[^A-Za-z0-9._-]+/g, '-').replace(/^[.-]+|[.-]+$/g, '') || 'attachment.bin';
  return `${threadId}-${stamp}-${basename}`;
}
```

Change send handler:

```typescript
  const onSendFollowUp = async (markdown: string, attachments: PendingAttachment[]) => {
    if (!threadId || !slug) return;
    setComposerError(null);
    try {
      const refs: ThreadAttachmentRef[] = [];
      for (const pending of attachments) {
        const uploaded = await artifactsApi.uploadArtifact(slug, {
          file: pending.file,
          name: safeArtifactName(threadId, pending.file),
          agent: 'founder',
        });
        refs.push({ artifact_name: uploaded.name, display_name: pending.file.name });
      }
      await sendFollowUp.mutateAsync({
        body_markdown: markdown.trim(),
        attachments: refs,
      });
      setPendingAttachments([]);
    } catch (err) {
      if (err instanceof ApiError) {
        setComposerError(describeError(err.code, `HTTP ${err.status}`));
      } else {
        setComposerError(String(err));
      }
      throw err;
    }
  };
```

Pass to Composer:

```tsx
attachments={pendingAttachments}
onAttachmentsChange={setPendingAttachments}
```

Pass attachment props to MessageBubble:

```tsx
attachments={m.attachments}
attachmentHref={(artifactName) => artifactsApi.artifactDownloadPath(slug, artifactName)}
```

- [ ] **Step 6: Add upload orchestration in NewThreadDialog**

In `NewThreadDialog.tsx`, add pending attachment state and the same upload helper, using `thread-draft` prefix before the thread exists:

```typescript
const [pendingAttachments, setPendingAttachments] = useState<PendingAttachment[]>([]);
```

Before `compose.mutateAsync`, upload files:

```typescript
const refs: ThreadAttachmentRef[] = [];
for (const pending of pendingAttachments) {
  const uploaded = await artifactsApi.uploadArtifact(slug, {
    file: pending.file,
    name: safeDraftArtifactName(pending.file),
    agent: 'founder',
  });
  refs.push({ artifact_name: uploaded.name, display_name: pending.file.name });
}
```

Include `attachments: refs` in the compose body. Update the empty validation to allow attachments:

```typescript
if (!subject.trim() || !recipients.length || (!body.trim() && !pendingAttachments.length)) {
  setErrorMsg('Subject, recipients, and a body or attachment are required.');
  return;
}
```

Render a local file input in the dialog:

```tsx
          <FormField label="Attachments" htmlFor={`${idBase}-attachments`}>
            <input
              id={`${idBase}-attachments`}
              aria-label="Attach files"
              type="file"
              multiple
              disabled={compose.isPending}
              onChange={(event) => {
                const files = Array.from(event.currentTarget.files ?? []).slice(0, 5);
                setPendingAttachments([
                  ...pendingAttachments,
                  ...files.map((file) => ({
                    id: `${file.name}-${file.size}-${file.lastModified}`,
                    file,
                  })),
                ].slice(0, 5));
                event.currentTarget.value = '';
              }}
            />
          </FormField>
          {pendingAttachments.length > 0 && (
            <ul className="text-caption text-text-muted flex flex-col gap-1">
              {pendingAttachments.map((item) => (
                <li key={item.id}>{item.file.name}</li>
              ))}
            </ul>
          )}
```

- [ ] **Step 7: Update mocks**

In `web/src/design-system/providers/_mock-threads.ts`, ensure created message objects include:

```typescript
attachments: body.attachments ?? [],
```

For old mock messages, add `attachments: []`.

- [ ] **Step 8: Run focused web tests**

Run:

```bash
cd web && npm test -- --run src/features/threads/write-path.test.tsx
```

Expected: PASS.

- [ ] **Step 9: Run web build**

Run:

```bash
npm run build
```

Expected: PASS.

- [ ] **Step 10: Commit**

Run:

```bash
git add web/src/design-system/patterns/Composer.tsx web/src/design-system/patterns/MessageBubble.tsx web/src/features/threads/ThreadsPage.tsx web/src/features/threads/NewThreadDialog.tsx web/src/design-system/providers/_mock-threads.ts web/src/features/threads/write-path.test.tsx
git commit -m "feat(web): attach files in thread composer"
```

## Task 7: OpenAPI, Contract Coverage, And Full Verification

**Files:**
- Modify: `tests/contract/openapi.json`
- Modify: `tests/contract/openapi.json`

- [ ] **Step 1: Regenerate OpenAPI snapshot**

Run:

```bash
HAPPYRANCH_REGEN_OPENAPI=1 uv run pytest tests/contract/test_openapi_snapshot.py
```

Expected: PASS and `tests/contract/openapi.json` changes to include attachment fields on thread request/response models.

- [ ] **Step 2: Run OpenAPI coverage test**

Run:

```bash
cd web && npm test -- --run src/test/openapi-coverage.test.ts
```

Expected: PASS. Thread body shape changes must be mirrored in `web/src/lib/api/threads.ts`; do not weaken coverage to hide missing thread mirrors.

- [ ] **Step 3: Run full Python unit tests**

Run:

```bash
uv run pytest tests/ -v
```

Expected: PASS with integration tests deselected by default.

- [ ] **Step 4: Run web tests and build**

Run:

```bash
cd web && npm test -- --run
npm run build
```

Expected: PASS.

- [ ] **Step 5: Run GitNexus detect changes before final commit**

Run GitNexus MCP:

```bash
detect_changes(repo="happyranch", scope="all")
```

Expected: changed symbols and affected processes match thread message/storage/rendering/API/CLI/web attachment work. Investigate any unrelated process.

- [ ] **Step 6: Commit contract and cleanup changes**

Run:

```bash
git add tests/contract/openapi.json
git commit -m "chore: verify thread attachment contracts"
```

- [ ] **Step 7: Final branch status**

Run:

```bash
git status --short
git log --oneline --max-count=8
```

Expected: clean working tree and commits for each task.

## Self-Review Notes

- Spec coverage: data model, reference-only API, founder/agent write paths, web upload UX, CLI upload convenience, prompt/transcript rendering, audit, validation, and testing are each mapped to tasks above.
- No feature code should be written before the Task 1 failing tests and required GitNexus impact checks.
- The plan intentionally keeps artifact bytes in `/artifacts`; no task adds per-thread file storage or cleanup semantics.
