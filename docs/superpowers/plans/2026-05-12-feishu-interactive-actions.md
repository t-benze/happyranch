# Feishu Interactive Actions Implementation Plan

**Status: REMOVED in TASK-302 (THR-022).** Web UI + threads are sole control surface. DB tables dormant.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend the existing Feishu escalation flow with (a) push notifications for terminal `FAILED` tasks and a `REVISIT` reply verb that spawns a new root task, and (b) top-level `DISPATCH` messages that create new tasks from Feishu.

**Architecture:** One new schema column (`escalation_notifications.kind`), two new config flags (`notify_on_failure`, `allow_dispatch`), and a bifurcation of the existing listener pipeline at step 3 (root_id present → reply branch; absent → dispatch branch). Reply-branch dispatch by `kind` × verb. Two new in-process helpers (`revisit_from_notification`, `dispatch_via_feishu`) extracted from existing HTTP routes so HTTP and Feishu surfaces cannot drift.

**Tech Stack:** Python 3.11+, FastAPI, Pydantic v2, SQLite (WAL), `lark-oapi>=1.6,<2`, `pytest`. Project uses `uv run pytest` and `uv run opc`.

**Spec:** `docs/superpowers/specs/2026-05-12-feishu-interactive-actions-design.md`

---

## File Structure

Modified files (in dependency order):

- `src/infrastructure/database.py` — `kind` column DDL + `mint_escalation_notification` param
- `src/infrastructure/feishu/reply_parser.py` — extend `parse_reply` with REVISIT + new `parse_top_level_message`
- `src/orchestrator/org_config.py` — `notify_on_failure`, `allow_dispatch` fields
- `src/infrastructure/feishu/notifier.py` — `send_failure`, `_build_failure_body`, `send_dispatch_confirmation`, `send_dispatch_error`
- `src/orchestrator/run_step.py` — `_maybe_spawn_auto_revisit` returns bool; `_notify_failure_if_eligible` helper called at every `_fail` call site
- `src/orchestrator/orchestrator.py` — `notify_failed` method (mirrors `notify_escalated`)
- `src/daemon/routes/tasks.py` — extract `revisit_from_notification` and `dispatch_via_feishu` in-process helpers; existing routes call them
- `src/daemon/feishu_listener.py` — bifurcate step 3; reply-branch `kind` routing; dispatch pipeline
- `src/daemon/__main__.py` — sweep calls `notify_failed(kind="daemon_restart")`
- `src/cli.py` — `opc revisit` consumes open `kind='failure'` rows with `consumed_by="cli-fallback"`

New test files:

- `tests/infrastructure/feishu/test_reply_parser_revisit.py` — REVISIT cases
- `tests/infrastructure/feishu/test_reply_parser_dispatch.py` — `parse_top_level_message`
- `tests/infrastructure/feishu/test_notifier_failure.py` — failure card + dispatch confirmations
- `tests/orchestrator/test_notify_failed_gate.py` — `_notify_failure_if_eligible` gating
- `tests/daemon/test_feishu_listener_kind_routing.py` — listener matrix
- `tests/daemon/test_feishu_listener_dispatch.py` — dispatch pipeline
- `tests/integration/test_feishu_failure_revisit_e2e.py` — failure → REVISIT round-trip
- `tests/integration/test_feishu_dispatch_e2e.py` — DISPATCH round-trip

Doc updates:

- `README.md` — Feishu config section
- `CLAUDE.md` — Feishu interactive actions section

---

## Task 1: Schema — add `kind` column to `escalation_notifications`

**Files:**
- Modify: `src/infrastructure/database.py` (CREATE TABLE block at lines 173–184, ALTER ladder at lines 198+, `mint_escalation_notification` at lines 1298–1317)
- Test: `tests/infrastructure/test_database_kind_column.py` (new)

- [ ] **Step 1: Write failing test**

Create `tests/infrastructure/test_database_kind_column.py`:

```python
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from src.infrastructure.database import Database


@pytest.fixture()
def db(tmp_path: Path) -> Database:
    return Database(tmp_path / "opc.db")


def test_mint_escalation_notification_defaults_to_escalation_kind(db: Database):
    db.mint_escalation_notification(
        feishu_message_id="om_abc",
        org_slug="acme",
        task_id="TASK-1",
        chat_id="oc_xyz",
        expires_at="2099-01-01T00:00:00+00:00",
    )
    row = db.get_escalation_notification("om_abc")
    assert row is not None
    assert row["kind"] == "escalation"


def test_mint_escalation_notification_accepts_failure_kind(db: Database):
    db.mint_escalation_notification(
        feishu_message_id="om_def",
        org_slug="acme",
        task_id="TASK-2",
        chat_id="oc_xyz",
        expires_at="2099-01-01T00:00:00+00:00",
        kind="failure",
    )
    row = db.get_escalation_notification("om_def")
    assert row is not None
    assert row["kind"] == "failure"


def test_mint_rejects_unknown_kind(db: Database):
    with pytest.raises(ValueError, match="kind"):
        db.mint_escalation_notification(
            feishu_message_id="om_x",
            org_slug="acme",
            task_id="TASK-3",
            chat_id="oc_xyz",
            expires_at="2099-01-01T00:00:00+00:00",
            kind="bogus",
        )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/infrastructure/test_database_kind_column.py -v`
Expected: FAIL — `mint_escalation_notification` does not accept `kind` argument; `get_escalation_notification` row has no `kind` key.

- [ ] **Step 3: Add `kind` column to CREATE TABLE and ALTER ladder**

Edit `src/infrastructure/database.py`. Locate the `escalation_notifications` CREATE TABLE (around lines 173–184) and add the column at the end before the closing paren:

```sql
CREATE TABLE IF NOT EXISTS escalation_notifications (
    feishu_message_id TEXT PRIMARY KEY,
    org_slug          TEXT NOT NULL,
    task_id           TEXT NOT NULL,
    chat_id           TEXT NOT NULL,
    created_at        TEXT NOT NULL,
    expires_at        TEXT NOT NULL,
    consumed_at       TEXT,
    consumed_by       TEXT,
    kind              TEXT NOT NULL DEFAULT 'escalation'
);
```

Then in the ALTER ladder (the existing try/except block around lines 198–237), add:

```python
try:
    self._conn.execute(
        "ALTER TABLE escalation_notifications ADD COLUMN kind "
        "TEXT NOT NULL DEFAULT 'escalation'"
    )
except sqlite3.OperationalError:
    pass
```

- [ ] **Step 4: Extend `mint_escalation_notification`**

Locate `mint_escalation_notification` (around lines 1298–1317) and modify its signature + body:

```python
def mint_escalation_notification(
    self,
    *,
    feishu_message_id: str,
    org_slug: str,
    task_id: str,
    chat_id: str,
    expires_at: str,
    kind: str = "escalation",
) -> None:
    if kind not in ("escalation", "failure"):
        raise ValueError(f"kind must be 'escalation' or 'failure', got {kind!r}")
    from datetime import datetime, timezone
    created_at = datetime.now(timezone.utc).isoformat()
    self._conn.execute(
        """INSERT INTO escalation_notifications
           (feishu_message_id, org_slug, task_id, chat_id,
            created_at, expires_at, kind)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (feishu_message_id, org_slug, task_id, chat_id,
         created_at, expires_at, kind),
    )
    self._conn.commit()
```

- [ ] **Step 5: Extend `get_escalation_notification` to include `kind`**

Locate `get_escalation_notification` (around lines 1320–1330) and ensure the SELECT clause returns `kind`. If the body uses `SELECT *`, no change needed — but verify by reading it and updating if it lists columns explicitly:

```python
def get_escalation_notification(self, feishu_message_id: str) -> dict | None:
    row = self._conn.execute(
        """SELECT feishu_message_id, org_slug, task_id, chat_id,
                  created_at, expires_at, consumed_at, consumed_by, kind
           FROM escalation_notifications WHERE feishu_message_id = ?""",
        (feishu_message_id,),
    ).fetchone()
    if row is None:
        return None
    return {
        "feishu_message_id": row[0],
        "org_slug": row[1],
        "task_id": row[2],
        "chat_id": row[3],
        "created_at": row[4],
        "expires_at": row[5],
        "consumed_at": row[6],
        "consumed_by": row[7],
        "kind": row[8],
    }
```

(If the existing implementation already uses `sqlite3.Row`/`row_factory` and dict-conversion, just add `kind` to the column list.)

- [ ] **Step 6: Run tests to verify pass**

Run: `uv run pytest tests/infrastructure/test_database_kind_column.py -v`
Expected: 3 PASS

Run: `uv run pytest tests/infrastructure/ -v`
Expected: All existing infrastructure tests still pass (no regressions).

- [ ] **Step 7: Commit**

```bash
git add src/infrastructure/database.py tests/infrastructure/test_database_kind_column.py
git commit -m "$(cat <<'EOF'
feat(db): add kind column to escalation_notifications

Additive schema change with default 'escalation' for back-compat.
mint_escalation_notification now accepts kind={'escalation','failure'}.
EOF
)"
```

---

## Task 2: Parser — extend `parse_reply` with REVISIT verb

**Files:**
- Modify: `src/infrastructure/feishu/reply_parser.py` (`ParseResult` at lines 14–17, `parse_reply` at lines 59–89)
- Test: `tests/infrastructure/feishu/test_reply_parser_revisit.py` (new)

- [ ] **Step 1: Write failing test**

Create `tests/infrastructure/feishu/test_reply_parser_revisit.py`:

```python
from __future__ import annotations

from src.infrastructure.feishu.reply_parser import parse_reply


def test_revisit_verb_uppercase():
    out = parse_reply("REVISIT\nadd the missing field")
    assert out is not None
    assert out.decision == "revisit"
    assert out.rationale == "add the missing field"


def test_revisit_verb_lowercase():
    out = parse_reply("revisit\nretry this")
    assert out is not None
    assert out.decision == "revisit"
    assert out.rationale == "retry this"


def test_revisit_with_multiline_body():
    out = parse_reply("REVISIT\nline one\nline two\n\nline three")
    assert out is not None
    assert out.decision == "revisit"
    assert out.rationale == "line one\nline two\n\nline three"


def test_revisit_without_body_defaults_rationale():
    out = parse_reply("REVISIT\n")
    assert out is not None
    assert out.decision == "revisit"
    # Match existing APPROVE/REJECT default-rationale behavior
    assert out.rationale == "(no rationale provided)"


def test_existing_approve_still_works():
    out = parse_reply("APPROVE\nrationale here")
    assert out is not None
    assert out.decision == "approve"
    assert out.rationale == "rationale here"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/infrastructure/feishu/test_reply_parser_revisit.py -v`
Expected: FAIL — `decision` Literal does not include `"revisit"`; `parse_reply` returns `None` for REVISIT input.

- [ ] **Step 3: Extend `ParseResult` and `parse_reply`**

Edit `src/infrastructure/feishu/reply_parser.py`. Update the dataclass:

```python
from typing import Literal


@dataclass(frozen=True)
class ParseResult:
    decision: Literal["approve", "reject", "revisit"]
    rationale: str
```

Update `parse_reply` — add a third branch in the verb detection. Locate the existing `if decision_word == "APPROVE":` / `elif decision_word == "REJECT":` chain and append:

```python
    elif decision_word == "REVISIT":
        decision = "revisit"
```

Leave the rationale-fallback ("(no rationale provided)") branch unchanged so REVISIT picks it up automatically.

- [ ] **Step 4: Run tests to verify pass**

Run: `uv run pytest tests/infrastructure/feishu/test_reply_parser_revisit.py tests/infrastructure/feishu/test_reply_parser.py -v`
Expected: All pass; existing parser tests unaffected.

- [ ] **Step 5: Commit**

```bash
git add src/infrastructure/feishu/reply_parser.py tests/infrastructure/feishu/test_reply_parser_revisit.py
git commit -m "$(cat <<'EOF'
feat(feishu): add REVISIT verb to reply parser

Extends parse_reply with a third decision value for failure-card replies.
Same multi-line rationale semantics as APPROVE/REJECT.
EOF
)"
```

---

## Task 3: Parser — add `parse_top_level_message` for DISPATCH

**Files:**
- Modify: `src/infrastructure/feishu/reply_parser.py`
- Test: `tests/infrastructure/feishu/test_reply_parser_dispatch.py` (new)

- [ ] **Step 1: Write failing test**

Create `tests/infrastructure/feishu/test_reply_parser_dispatch.py`:

```python
from __future__ import annotations

from src.infrastructure.feishu.reply_parser import (
    DispatchIntent,
    parse_top_level_message,
)


def test_dispatch_with_team_and_brief():
    out = parse_top_level_message("DISPATCH engineering\nfix the scraper")
    assert out == DispatchIntent(team="engineering", brief="fix the scraper")


def test_dispatch_without_team():
    out = parse_top_level_message("DISPATCH\nfix the scraper")
    assert out == DispatchIntent(team=None, brief="fix the scraper")


def test_dispatch_multiline_brief():
    out = parse_top_level_message("DISPATCH\nline 1\nline 2\n\nline 3")
    assert out == DispatchIntent(team=None, brief="line 1\nline 2\n\nline 3")


def test_dispatch_lowercase_verb():
    out = parse_top_level_message("dispatch engineering\nbrief")
    assert out == DispatchIntent(team="engineering", brief="brief")


def test_dispatch_empty_brief_returns_none():
    assert parse_top_level_message("DISPATCH engineering\n") is None
    assert parse_top_level_message("DISPATCH\n   \n") is None


def test_dispatch_unknown_verb_returns_none():
    assert parse_top_level_message("APPROVE\nbody") is None
    assert parse_top_level_message("hello world") is None


def test_dispatch_only_first_token_is_team():
    out = parse_top_level_message("DISPATCH engineering ignored\nbrief")
    # Per spec: team is the rest of the verb line stripped — single token expected
    # but we keep whatever the founder wrote so the daemon can error with a
    # helpful "unknown team" rather than silently truncate.
    assert out is not None
    assert out.team == "engineering ignored"
    assert out.brief == "brief"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/infrastructure/feishu/test_reply_parser_dispatch.py -v`
Expected: FAIL — `DispatchIntent` and `parse_top_level_message` are undefined.

- [ ] **Step 3: Add `DispatchIntent` and `parse_top_level_message`**

Edit `src/infrastructure/feishu/reply_parser.py`. Add near the top (after `ParseResult`):

```python
@dataclass(frozen=True)
class DispatchIntent:
    team: str | None
    brief: str
```

Add a shared helper near the bottom:

```python
def _split_verb_and_body(text: str) -> tuple[str, str, str] | None:
    """Return (verb_uppercase, verb_line_tail, body) or None if no content.

    verb_line_tail is the part of the first non-empty line after the verb
    (e.g., "engineering" for "DISPATCH engineering"). body is the remaining
    lines stripped.
    """
    if text is None:
        return None
    lines = text.splitlines()
    first_idx = next((i for i, line in enumerate(lines) if line.strip()), None)
    if first_idx is None:
        return None
    first_line = lines[first_idx].strip()
    parts = first_line.split(None, 1)
    verb = parts[0].upper()
    tail = parts[1].strip() if len(parts) > 1 else ""
    body = "\n".join(lines[first_idx + 1 :]).strip()
    return (verb, tail, body)


def parse_top_level_message(text: str) -> DispatchIntent | None:
    """Verbs: DISPATCH [<team>]. Body lines become the brief."""
    split = _split_verb_and_body(text)
    if split is None:
        return None
    verb, tail, body = split
    if verb != "DISPATCH":
        return None
    if not body.strip():
        return None
    team = tail if tail else None
    return DispatchIntent(team=team, brief=body)
```

(`parse_reply` is not refactored in this task — minimum diff. A later cleanup can share `_split_verb_and_body` if desired.)

- [ ] **Step 4: Run tests to verify pass**

Run: `uv run pytest tests/infrastructure/feishu/test_reply_parser_dispatch.py -v`
Expected: 7 PASS

- [ ] **Step 5: Commit**

```bash
git add src/infrastructure/feishu/reply_parser.py tests/infrastructure/feishu/test_reply_parser_dispatch.py
git commit -m "$(cat <<'EOF'
feat(feishu): add parse_top_level_message for DISPATCH verb

DispatchIntent(team, brief). Single new verb DISPATCH with optional team
after the verb word. Empty body returns None so listener can audit-reject.
EOF
)"
```

---

## Task 4: Config — add `notify_on_failure` and `allow_dispatch` fields

**Files:**
- Modify: `src/orchestrator/org_config.py` (`FeishuNotificationsConfig` at lines 24–31, `_parse_feishu_notifications` at lines 52–103)
- Test: `tests/orchestrator/test_org_config_feishu_flags.py` (new)

- [ ] **Step 1: Write failing test**

Create `tests/orchestrator/test_org_config_feishu_flags.py`:

```python
from __future__ import annotations

from pathlib import Path

import pytest

from src.orchestrator.org_config import OrgConfigError, load_org_config


def _write(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "config.yaml"
    p.write_text(body)
    return p


def _minimal_feishu_block(extra: str = "") -> str:
    return f"""
feishu_notifications:
  enabled: true
  provider: feishu
  region: feishu
  chat_id: oc_xyz
  app_id: cli_abc
  app_secret: shhh
{extra}
"""


def test_defaults_when_flags_absent(tmp_path: Path):
    p = _write(tmp_path, _minimal_feishu_block())
    cfg = load_org_config(p)
    assert cfg.feishu_notifications is not None
    assert cfg.feishu_notifications.notify_on_failure is False
    assert cfg.feishu_notifications.allow_dispatch is False


def test_flags_set_true(tmp_path: Path):
    p = _write(
        tmp_path,
        _minimal_feishu_block("  notify_on_failure: true\n  allow_dispatch: true\n"),
    )
    cfg = load_org_config(p)
    assert cfg.feishu_notifications.notify_on_failure is True
    assert cfg.feishu_notifications.allow_dispatch is True


def test_flag_must_be_bool(tmp_path: Path):
    p = _write(tmp_path, _minimal_feishu_block('  notify_on_failure: "yes"\n'))
    with pytest.raises(OrgConfigError, match="notify_on_failure"):
        load_org_config(p)


def test_allow_dispatch_must_be_bool(tmp_path: Path):
    p = _write(tmp_path, _minimal_feishu_block("  allow_dispatch: 1\n"))
    with pytest.raises(OrgConfigError, match="allow_dispatch"):
        load_org_config(p)
```

(If `load_org_config` is not the public function name, swap for whatever your `org_config.py` exposes. Read the file first to confirm — likely `OrgConfig.from_path` or similar.)

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/orchestrator/test_org_config_feishu_flags.py -v`
Expected: FAIL — `notify_on_failure` and `allow_dispatch` attributes do not exist on `FeishuNotificationsConfig`.

- [ ] **Step 3: Extend `FeishuNotificationsConfig` + `_parse_feishu_notifications`**

Edit `src/orchestrator/org_config.py`:

```python
@dataclass(frozen=True)
class FeishuNotificationsConfig:
    provider: str
    region: str
    chat_id: str
    app_id: str
    app_secret: str
    reply_ttl_hours: int = 72
    notify_on_failure: bool = False
    allow_dispatch: bool = False
```

In `_parse_feishu_notifications`, after the existing `reply_ttl_hours` validation, add:

```python
    notify_on_failure = block.get("notify_on_failure", False)
    if not isinstance(notify_on_failure, bool):
        raise OrgConfigError(
            f"{path}: feishu_notifications.notify_on_failure must be a boolean, "
            f"got {type(notify_on_failure).__name__}"
        )

    allow_dispatch = block.get("allow_dispatch", False)
    if not isinstance(allow_dispatch, bool):
        raise OrgConfigError(
            f"{path}: feishu_notifications.allow_dispatch must be a boolean, "
            f"got {type(allow_dispatch).__name__}"
        )
```

And in the return / constructor call at the bottom of the function, pass both fields through:

```python
    return FeishuNotificationsConfig(
        provider=provider,
        region=region,
        chat_id=chat_id,
        app_id=app_id,
        app_secret=app_secret,
        reply_ttl_hours=reply_ttl_hours,
        notify_on_failure=notify_on_failure,
        allow_dispatch=allow_dispatch,
    )
```

(Note the YAML quirk: `1` is parsed as `int`, not `bool`. The `isinstance(..., bool)` check catches that — Python's `True`/`False` are `int` subclasses, but `1` isn't a `bool` instance. The test `allow_dispatch: 1` exercises this.)

- [ ] **Step 4: Run tests to verify pass**

Run: `uv run pytest tests/orchestrator/test_org_config_feishu_flags.py tests/orchestrator/ -v`
Expected: All pass.

- [ ] **Step 5: Commit**

```bash
git add src/orchestrator/org_config.py tests/orchestrator/test_org_config_feishu_flags.py
git commit -m "$(cat <<'EOF'
feat(config): add notify_on_failure and allow_dispatch flags

Both default false. Boolean-typed. OrgConfigError on type mismatch.
EOF
)"
```

---

## Task 5: Notifier — `send_failure` + `_build_failure_body`

**Files:**
- Modify: `src/infrastructure/feishu/notifier.py` (`EscalationNotifier` constructor at lines 74–87; `notify_escalated` at lines 89–142; `_build_body_phase1` at lines 26–70)
- Test: `tests/infrastructure/feishu/test_notifier_failure.py` (new)

- [ ] **Step 1: Write failing test**

Create `tests/infrastructure/feishu/test_notifier_failure.py`:

```python
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from src.infrastructure.audit_logger import AuditLogger
from src.infrastructure.database import Database
from src.infrastructure.feishu.notifier import EscalationNotifier
from src.orchestrator.org_config import FeishuNotificationsConfig


class _FakeClient:
    def __init__(self) -> None:
        self.sent: list[dict] = []

    def send_post_message(self, *, chat_id, title, body_lines):
        self.sent.append({"chat_id": chat_id, "title": title, "body": body_lines})
        return "om_failure_msg_1"


@pytest.fixture()
def setup(tmp_path: Path):
    db = Database(tmp_path / "opc.db")
    audit = AuditLogger(db)
    client = _FakeClient()
    cfg = FeishuNotificationsConfig(
        provider="feishu", region="feishu", chat_id="oc_xyz",
        app_id="cli", app_secret="x", reply_ttl_hours=72,
    )
    notifier = EscalationNotifier(
        slug="acme", db=db, audit=audit, client=client, config=cfg,
    )
    # Insert a task so the notifier can render its brief
    from src.models import TaskRecord, TaskStatus
    db.insert_task(TaskRecord(
        id="TASK-9", brief="ferry scraper update", team="engineering",
        assigned_agent="dev_agent", status=TaskStatus.FAILED,
    ))
    return notifier, db, client


def test_send_failure_renders_card_and_mints_failure_kind(setup):
    notifier, db, client = setup
    asyncio.run(notifier.send_failure(
        task_id="TASK-9",
        agent="dev_agent",
        failure_kind="self_blocked",
        failure_note="cannot determine fare-tier mapping",
        last_summary="delegated; agent returned blocked status",
    ))
    assert len(client.sent) == 1
    sent = client.sent[0]
    assert "FAILED" in sent["title"]
    body_text = "\n".join(sent["body"]) if isinstance(sent["body"], list) else str(sent["body"])
    assert "self_blocked" in body_text
    assert "cannot determine fare-tier mapping" in body_text
    assert "REVISIT" in body_text

    row = db.get_escalation_notification("om_failure_msg_1")
    assert row is not None
    assert row["kind"] == "failure"
    assert row["task_id"] == "TASK-9"


def test_send_failure_swallows_send_exception(setup):
    notifier, db, client = setup

    def boom(**kwargs):
        raise RuntimeError("feishu down")

    client.send_post_message = boom
    # Must not raise
    asyncio.run(notifier.send_failure(
        task_id="TASK-9", agent="dev_agent",
        failure_kind="self_blocked", failure_note="x", last_summary="",
    ))
    # No notification row minted on send failure
    assert db.get_escalation_notification("om_failure_msg_1") is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/infrastructure/feishu/test_notifier_failure.py -v`
Expected: FAIL — `send_failure` method does not exist.

- [ ] **Step 3: Implement `_build_failure_body` + `send_failure`**

Edit `src/infrastructure/feishu/notifier.py`. After `_build_body_phase1`, add:

```python
def _build_failure_body(
    *, slug: str, task_id: str, agent: str, team: str,
    brief: str, last_summary: str, failure_kind: str,
    failure_note: str, failed_at: str,
) -> tuple[str, list[str]]:
    title = f"[OPC {slug}] {task_id} FAILED — review needed"
    body_lines = [
        f"Agent:        {agent}",
        f"Team:         {team}",
        f"Task:         {task_id}",
        f"Org:          {slug}",
        f"Failed at:    {failed_at}",
        f"Failure kind: {failure_kind}",
        "",
        "--- Brief ---",
        brief,
        "",
        "--- Last manager summary ---",
        last_summary or "(none)",
        "",
        "--- Failure detail ---",
        failure_note,
        "",
        "--- To revisit ---",
        "Reply in this thread with:",
        "",
        "  REVISIT",
        "  <optional note that becomes founder_note on the new root>",
        "",
        "(Or ignore this message — the task stays failed.)",
    ]
    return title, body_lines
```

After `notify_escalated` method, add `send_failure`:

```python
async def send_failure(
    self,
    *,
    task_id: str,
    agent: str,
    failure_kind: str,
    failure_note: str,
    last_summary: str = "",
) -> None:
    """Mirrors notify_escalated for FAILED tasks. Mint-after-send.
    All exceptions are swallowed and audited."""
    from datetime import datetime, timedelta, timezone
    try:
        task = self._db.get_task(task_id)
        if task is None:
            return
        failed_at = (task.completed_at or
                     datetime.now(timezone.utc).isoformat())
        title, body_lines = _build_failure_body(
            slug=self._slug,
            task_id=task_id,
            agent=agent,
            team=task.team or "",
            brief=task.brief,
            last_summary=last_summary,
            failure_kind=failure_kind,
            failure_note=failure_note,
            failed_at=failed_at,
        )
        message_id = self._client.send_post_message(
            chat_id=self._config.chat_id,
            title=title,
            body_lines=body_lines,
        )
        expires_at = (datetime.now(timezone.utc) +
                      timedelta(hours=self._config.reply_ttl_hours)).isoformat()
        self._db.mint_escalation_notification(
            feishu_message_id=message_id,
            org_slug=self._slug,
            task_id=task_id,
            chat_id=self._config.chat_id,
            expires_at=expires_at,
            kind="failure",
        )
        self._audit.log_failure_notify_sent(
            task_id=task_id,
            feishu_message_id=message_id,
            failure_kind=failure_kind,
            expires_at=expires_at,
        )
    except Exception as exc:  # noqa: BLE001
        self._audit.log_failure_notify_failed(
            task_id=task_id, failure_kind=failure_kind, error=str(exc),
        )
```

- [ ] **Step 4: Add audit logger methods**

In `src/infrastructure/audit_logger.py`, add the two `log_*` methods following the existing escalation-notify pattern. Read the existing `log_escalation_notify_sent` / `log_escalation_notify_failed` first and mirror them:

```python
def log_failure_notify_sent(
    self, *, task_id: str, feishu_message_id: str,
    failure_kind: str, expires_at: str,
) -> None:
    self._log(
        task_id=task_id,
        agent="orchestrator",
        action="failure_notify_sent",
        payload={
            "feishu_message_id": feishu_message_id,
            "failure_kind": failure_kind,
            "expires_at": expires_at,
        },
    )


def log_failure_notify_failed(
    self, *, task_id: str, failure_kind: str, error: str,
) -> None:
    self._log(
        task_id=task_id,
        agent="orchestrator",
        action="failure_notify_failed",
        payload={"failure_kind": failure_kind, "error": error},
    )
```

(Match the exact private-`_log` signature used by the existing notify-sent/failed methods. If the existing methods use a different shape, mirror that exactly.)

- [ ] **Step 5: Run tests to verify pass**

Run: `uv run pytest tests/infrastructure/feishu/test_notifier_failure.py tests/infrastructure/feishu/ -v`
Expected: All pass.

- [ ] **Step 6: Commit**

```bash
git add src/infrastructure/feishu/notifier.py src/infrastructure/audit_logger.py tests/infrastructure/feishu/test_notifier_failure.py
git commit -m "$(cat <<'EOF'
feat(feishu): EscalationNotifier.send_failure + failure card

Mirrors notify_escalated. Mints escalation_notifications row with
kind='failure'. Audits failure_notify_sent / failure_notify_failed.
EOF
)"
```

---

## Task 6: Notifier — dispatch confirmation + error cards

**Files:**
- Modify: `src/infrastructure/feishu/notifier.py`
- Test: `tests/infrastructure/feishu/test_notifier_dispatch.py` (new)

- [ ] **Step 1: Write failing test**

Create `tests/infrastructure/feishu/test_notifier_dispatch.py`:

```python
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from src.infrastructure.audit_logger import AuditLogger
from src.infrastructure.database import Database
from src.infrastructure.feishu.notifier import EscalationNotifier
from src.orchestrator.org_config import FeishuNotificationsConfig


class _FakeClient:
    def __init__(self):
        self.sent = []

    def send_post_message(self, *, chat_id, title, body_lines):
        self.sent.append({"chat_id": chat_id, "title": title, "body": body_lines})
        return "om_confirm_1"


@pytest.fixture()
def notifier(tmp_path: Path):
    db = Database(tmp_path / "opc.db")
    audit = AuditLogger(db)
    client = _FakeClient()
    cfg = FeishuNotificationsConfig(
        provider="feishu", region="feishu", chat_id="oc_xyz",
        app_id="cli", app_secret="x", reply_ttl_hours=72,
    )
    return EscalationNotifier(
        slug="acme", db=db, audit=audit, client=client, config=cfg,
    ), client


def test_send_dispatch_confirmation_renders_card(notifier):
    n, client = notifier
    asyncio.run(n.send_dispatch_confirmation(
        task_id="TASK-21", team="engineering",
        brief="investigate the 503 thing",
    ))
    assert len(client.sent) == 1
    body = "\n".join(client.sent[0]["body"])
    assert "TASK-21" in client.sent[0]["title"]
    assert "engineering" in body
    assert "investigate the 503" in body
    assert "opc tail" in body


def test_send_dispatch_error_lists_reason(notifier):
    n, client = notifier
    asyncio.run(n.send_dispatch_error(
        reason="unknown team \"engineerin\"",
        valid_teams=["engineering", "customer-care"],
    ))
    assert len(client.sent) == 1
    body = "\n".join(client.sent[0]["body"])
    assert "unknown team" in body
    assert "engineering" in body
    assert "customer-care" in body


def test_send_dispatch_confirmation_swallows_exception(notifier):
    n, client = notifier
    client.send_post_message = lambda **kw: (_ for _ in ()).throw(RuntimeError("boom"))
    # Must not raise
    asyncio.run(n.send_dispatch_confirmation(
        task_id="TASK-21", team="engineering", brief="x",
    ))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/infrastructure/feishu/test_notifier_dispatch.py -v`
Expected: FAIL — methods do not exist.

- [ ] **Step 3: Implement methods**

Add to `src/infrastructure/feishu/notifier.py`:

```python
async def send_dispatch_confirmation(
    self, *, task_id: str, team: str | None, brief: str,
) -> None:
    """Top-level post (not threaded) confirming a Feishu dispatch.
    Best-effort; swallows + audits exceptions."""
    try:
        brief_trunc = brief if len(brief) <= 240 else brief[:240] + "…"
        title = f"[OPC {self._slug}] Task {task_id} dispatched"
        body_lines = [
            f"Team:  {team or '(auto)'}",
            f"Brief: {brief_trunc}",
            "",
            "Track with:",
            f"  opc tail --org {self._slug} {task_id}",
        ]
        self._client.send_post_message(
            chat_id=self._config.chat_id, title=title, body_lines=body_lines,
        )
    except Exception as exc:  # noqa: BLE001
        self._audit.log_dispatch_send_confirmation_failed(
            task_id=task_id, error=str(exc),
        )


async def send_dispatch_error(
    self, *, reason: str, valid_teams: list[str] | None = None,
) -> None:
    """Top-level post reporting a rejected DISPATCH. Best-effort."""
    try:
        title = f"[OPC {self._slug}] Dispatch rejected"
        body_lines = [f"Reason: {reason}"]
        if valid_teams:
            body_lines.append(f"Valid teams: {', '.join(valid_teams)}")
        self._client.send_post_message(
            chat_id=self._config.chat_id, title=title, body_lines=body_lines,
        )
    except Exception:  # noqa: BLE001
        # Error-card send failure is itself an error — log nothing extra,
        # the original audit row for dispatch_via_feishu_rejected already exists.
        pass
```

Add to `src/infrastructure/audit_logger.py`:

```python
def log_dispatch_send_confirmation_failed(
    self, *, task_id: str, error: str,
) -> None:
    self._log(
        task_id=task_id,
        agent="orchestrator",
        action="dispatch_send_confirmation_failed",
        payload={"error": error},
    )
```

- [ ] **Step 4: Run tests to verify pass**

Run: `uv run pytest tests/infrastructure/feishu/test_notifier_dispatch.py -v`
Expected: 3 PASS

- [ ] **Step 5: Commit**

```bash
git add src/infrastructure/feishu/notifier.py src/infrastructure/audit_logger.py tests/infrastructure/feishu/test_notifier_dispatch.py
git commit -m "$(cat <<'EOF'
feat(feishu): notifier dispatch confirmation + error cards

send_dispatch_confirmation posts a top-level confirmation card; truncates
briefs to 240 chars. send_dispatch_error lists valid teams when applicable.
EOF
)"
```

---

## Task 7: Orchestrator — `_maybe_spawn_auto_revisit` returns bool

**Files:**
- Modify: `src/orchestrator/run_step.py` (`_maybe_spawn_auto_revisit` at lines 656–729)
- Test: `tests/orchestrator/test_auto_revisit_return.py` (new)

- [ ] **Step 1: Write failing test**

Create `tests/orchestrator/test_auto_revisit_return.py`:

```python
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest


def _build_orch_with_task(tmp_path: Path, predecessor_root_status: str):
    """Helper builds the minimal orchestrator state needed to drive
    _maybe_spawn_auto_revisit. Returns (orch, failed_task_id, agent)."""
    from src.infrastructure.database import Database
    from src.infrastructure.audit_logger import AuditLogger
    from src.models import TaskRecord, TaskStatus

    db = Database(tmp_path / "opc.db")
    db.insert_task(TaskRecord(
        id="TASK-1", brief="x", team="engineering",
        assigned_agent="manager", status=TaskStatus(predecessor_root_status),
    ))
    audit = AuditLogger(db)
    orch = MagicMock()
    orch._db = db
    orch._audit = audit
    orch._queue = MagicMock()
    orch._slug = "acme"
    return orch, "TASK-1", "manager"


def test_returns_true_when_spawned(tmp_path: Path):
    from src.orchestrator.run_step import _maybe_spawn_auto_revisit
    orch, failed_id, agent = _build_orch_with_task(tmp_path, "failed")
    spawned = _maybe_spawn_auto_revisit(
        orch, failed_id, agent, error_context={"mode": "exception", "detail": "boom"},
    )
    assert spawned is True


def test_returns_false_when_no_chain(tmp_path: Path):
    from src.orchestrator.run_step import _maybe_spawn_auto_revisit
    from unittest.mock import MagicMock
    orch = MagicMock()
    orch._db.walk_ancestors.return_value = []  # no chain → False
    spawned = _maybe_spawn_auto_revisit(
        orch, "TASK-X", "agent", error_context={},
    )
    assert spawned is False


def test_returns_false_when_cap_hit(tmp_path: Path, monkeypatch):
    from src.orchestrator import run_step
    from src.orchestrator.run_step import _maybe_spawn_auto_revisit, _AUTO_REVISIT_CAP

    orch, failed_id, agent = _build_orch_with_task(tmp_path, "failed")

    # Stub walk_revisit_chain + audit_logs to simulate cap-hit
    fake_revisit_chain = [MagicMock(id=f"TASK-AR{i}") for i in range(_AUTO_REVISIT_CAP)]
    orch._db.walk_revisit_chain = MagicMock(return_value=fake_revisit_chain)
    orch._db.get_audit_logs = MagicMock(
        return_value=[{"action": "auto_revisit_of"}]
    )
    orch._db.walk_ancestors = MagicMock(
        return_value=[MagicMock(id="TASK-1", brief="x", team="engineering",
                                assigned_agent="manager",
                                session_timeout_seconds=None)]
    )

    spawned = _maybe_spawn_auto_revisit(
        orch, failed_id, agent, error_context={},
    )
    assert spawned is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/orchestrator/test_auto_revisit_return.py -v`
Expected: FAIL — function currently returns `None`, assertions on `is True` / `is False` fail (None is neither).

- [ ] **Step 3: Change return type**

Edit `src/orchestrator/run_step.py` `_maybe_spawn_auto_revisit` (lines 656–729). Update the signature:

```python
def _maybe_spawn_auto_revisit(
    orch: "Orchestrator",
    failed_task_id: str,
    failed_agent: str,
    error_context: dict,
) -> bool:
    """... (existing docstring) ...

    Returns True if a revisit row was inserted, False otherwise (no chain,
    cap hit, or future not-eligible cases).
    """
```

At every early `return` inside the body, replace with `return False`. At the bottom (after `queue.put_nowait(...)`), add `return True`.

Concrete edits to the existing body (showing each return change):

```python
    if not chain:
        return False  # was: return
    ...
    if auto_count >= _AUTO_REVISIT_CAP:
        return False  # was: return
    ...
    queue = getattr(orch, "_queue", None)
    if queue is not None:
        queue.put_nowait(orch._slug, new_id)
    return True  # NEW final line
```

Existing call sites in `run_step.py` at lines 105–108 and 129–132 ignore the return value and need no changes — `True`/`False` is just discarded as before.

- [ ] **Step 4: Run tests to verify pass**

Run: `uv run pytest tests/orchestrator/test_auto_revisit_return.py tests/orchestrator/ -v`
Expected: All pass; no regressions in existing tests.

- [ ] **Step 5: Commit**

```bash
git add src/orchestrator/run_step.py tests/orchestrator/test_auto_revisit_return.py
git commit -m "$(cat <<'EOF'
refactor(orch): _maybe_spawn_auto_revisit returns bool

Returns True if revisit spawned, False otherwise (no chain, cap hit).
Existing callers ignore the return value; new failure-notify hook
will use it to gate notification.
EOF
)"
```

---

## Task 8: Orchestrator — `notify_failed` method

**Files:**
- Modify: `src/orchestrator/orchestrator.py` (after `notify_escalated` at lines 108–134)
- Test: `tests/orchestrator/test_notify_failed_dispatch.py` (new)

- [ ] **Step 1: Write failing test**

Create `tests/orchestrator/test_notify_failed_dispatch.py`:

```python
from __future__ import annotations

import asyncio
from unittest.mock import MagicMock


def test_notify_failed_routes_to_notifier_send_failure():
    from src.orchestrator.orchestrator import Orchestrator
    orch = Orchestrator.__new__(Orchestrator)
    notifier = MagicMock()
    notifier.send_failure = MagicMock()
    # send_failure must be awaitable; return a completed coroutine
    async def _noop(**kw):
        return None
    notifier.send_failure.side_effect = _noop
    orch._notifier = notifier

    orch.notify_failed(
        task_id="TASK-9", agent="dev_agent",
        failure_kind="self_blocked", failure_note="x", last_summary="y",
    )
    # Give the daemon thread a moment to call send_failure
    import time
    for _ in range(20):
        if notifier.send_failure.called:
            break
        time.sleep(0.05)
    assert notifier.send_failure.called
    kwargs = notifier.send_failure.call_args.kwargs
    assert kwargs["task_id"] == "TASK-9"
    assert kwargs["failure_kind"] == "self_blocked"


def test_notify_failed_when_notifier_none_is_silent():
    from src.orchestrator.orchestrator import Orchestrator
    orch = Orchestrator.__new__(Orchestrator)
    orch._notifier = None
    # Must not raise
    orch.notify_failed(
        task_id="TASK-9", agent="x",
        failure_kind="self_blocked", failure_note="x", last_summary="",
    )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/orchestrator/test_notify_failed_dispatch.py -v`
Expected: FAIL — `notify_failed` method does not exist.

- [ ] **Step 3: Implement `notify_failed`**

Edit `src/orchestrator/orchestrator.py`. Add immediately after `notify_escalated` (after line 134):

```python
def notify_failed(
    self, *, task_id: str, agent: str, failure_kind: str,
    failure_note: str, last_summary: str = "",
) -> None:
    """Fire-and-forget failure notification. Same threading model as
    notify_escalated: detect running loop, fall back to daemon thread."""
    if self._notifier is None:
        return
    import asyncio
    import threading
    coro_factory = lambda: self._notifier.send_failure(
        task_id=task_id, agent=agent, failure_kind=failure_kind,
        failure_note=failure_note, last_summary=last_summary,
    )
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        threading.Thread(
            target=lambda: asyncio.run(coro_factory()),
            daemon=True,
        ).start()
    else:
        loop.create_task(coro_factory())
```

- [ ] **Step 4: Run tests to verify pass**

Run: `uv run pytest tests/orchestrator/test_notify_failed_dispatch.py -v`
Expected: 2 PASS

- [ ] **Step 5: Commit**

```bash
git add src/orchestrator/orchestrator.py tests/orchestrator/test_notify_failed_dispatch.py
git commit -m "$(cat <<'EOF'
feat(orch): Orchestrator.notify_failed fire-and-forget hook

Mirrors notify_escalated threading model. No-op when notifier is None.
EOF
)"
```

---

## Task 9: Run-step — wire `notify_failed` into all `_fail` call sites

**Files:**
- Modify: `src/orchestrator/run_step.py` (`_fail` at lines 533–550; call sites at lines 103, 127, 138, 187, 261; cascade-fail at line 616)
- Test: `tests/orchestrator/test_notify_failed_gate.py` (new)

- [ ] **Step 1: Write failing test**

Create `tests/orchestrator/test_notify_failed_gate.py`:

```python
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest


def _build_orch(tmp_path: Path, *, cancelled: bool = False, notify_on_failure: bool = True):
    from src.infrastructure.database import Database
    from src.infrastructure.audit_logger import AuditLogger
    from src.models import TaskRecord, TaskStatus
    from datetime import datetime, timezone

    db = Database(tmp_path / "opc.db")
    task = TaskRecord(
        id="TASK-1", brief="x", team="engineering",
        assigned_agent="dev_agent", status=TaskStatus.IN_PROGRESS,
    )
    if cancelled:
        task = task.model_copy(update={
            "cancelled_at": datetime.now(timezone.utc).isoformat(),
        })
    db.insert_task(task)

    audit = AuditLogger(db)
    orch = MagicMock()
    orch._db = db
    orch._audit = audit
    orch._slug = "acme"
    orch.notify_failed = MagicMock()

    # Config gate
    org_config = MagicMock()
    org_config.feishu_notifications = MagicMock(notify_on_failure=notify_on_failure)
    orch._org_config = org_config
    return orch


def test_notify_failed_fires_when_eligible(tmp_path: Path):
    from src.orchestrator.run_step import _notify_failure_if_eligible
    orch = _build_orch(tmp_path)
    _notify_failure_if_eligible(
        orch, "TASK-1", failure_kind="self_blocked",
        failure_note="x", auto_revisit_spawned=False, last_summary="",
    )
    assert orch.notify_failed.called
    assert orch.notify_failed.call_args.kwargs["failure_kind"] == "self_blocked"


def test_no_notify_when_auto_revisit_spawned(tmp_path: Path):
    from src.orchestrator.run_step import _notify_failure_if_eligible
    orch = _build_orch(tmp_path)
    _notify_failure_if_eligible(
        orch, "TASK-1", failure_kind="agent_exception",
        failure_note="x", auto_revisit_spawned=True, last_summary="",
    )
    assert not orch.notify_failed.called


def test_no_notify_when_cancelled(tmp_path: Path):
    from src.orchestrator.run_step import _notify_failure_if_eligible
    orch = _build_orch(tmp_path, cancelled=True)
    _notify_failure_if_eligible(
        orch, "TASK-1", failure_kind="self_blocked",
        failure_note="x", auto_revisit_spawned=False, last_summary="",
    )
    assert not orch.notify_failed.called


def test_no_notify_when_config_disabled(tmp_path: Path):
    from src.orchestrator.run_step import _notify_failure_if_eligible
    orch = _build_orch(tmp_path, notify_on_failure=False)
    _notify_failure_if_eligible(
        orch, "TASK-1", failure_kind="self_blocked",
        failure_note="x", auto_revisit_spawned=False, last_summary="",
    )
    assert not orch.notify_failed.called


def test_no_notify_when_org_has_no_feishu_config(tmp_path: Path):
    from src.orchestrator.run_step import _notify_failure_if_eligible
    orch = _build_orch(tmp_path)
    orch._org_config.feishu_notifications = None
    _notify_failure_if_eligible(
        orch, "TASK-1", failure_kind="self_blocked",
        failure_note="x", auto_revisit_spawned=False, last_summary="",
    )
    assert not orch.notify_failed.called
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/orchestrator/test_notify_failed_gate.py -v`
Expected: FAIL — `_notify_failure_if_eligible` does not exist.

- [ ] **Step 3: Add the gate helper**

Edit `src/orchestrator/run_step.py`. Add near the other private helpers (e.g., right after `_fail`):

```python
def _notify_failure_if_eligible(
    orch: "Orchestrator",
    task_id: str,
    *,
    failure_kind: str,
    failure_note: str,
    auto_revisit_spawned: bool,
    last_summary: str = "",
) -> None:
    """Fire notify_failed if all gates open:
       1. feishu_notifications.enabled (config exists) AND notify_on_failure=true
       2. task not founder-cancelled (cancelled_at IS NULL)
       3. no auto-revisit spawned for this task
    See spec 2026-05-12-feishu-interactive-actions-design.md §5.1.
    """
    org_config = getattr(orch, "_org_config", None)
    if org_config is None or org_config.feishu_notifications is None:
        return
    if not getattr(org_config.feishu_notifications, "notify_on_failure", False):
        return
    if auto_revisit_spawned:
        return
    task = orch._db.get_task(task_id)
    if task is None or task.cancelled_at is not None:
        return
    agent = task.assigned_agent or "(unknown)"
    orch.notify_failed(
        task_id=task_id,
        agent=agent,
        failure_kind=failure_kind,
        failure_note=failure_note,
        last_summary=last_summary,
    )
```

- [ ] **Step 4: Wire the gate into each `_fail` call site**

Edit `src/orchestrator/run_step.py`. The pattern is: capture `_maybe_spawn_auto_revisit` return, then call the gate. Update each call site:

**Line ~103 (agent invocation exception):**
```python
    except Exception as exc:
        _fail(orch, task_id, note=f"agent invocation failed: {exc}")
        _enqueue_parent_if_waiting(orch, task_id)
        spawned = _maybe_spawn_auto_revisit(
            orch, task_id, agent,
            error_context={"mode": "exception", "detail": str(exc)},
        )
        _notify_failure_if_eligible(
            orch, task_id, failure_kind="agent_exception",
            failure_note=f"agent invocation failed: {exc}",
            auto_revisit_spawned=spawned,
        )
        return
```

**Line ~127 (session non-success):**
```python
    if not result.success or report is None:
        note = _session_failed_note(result, report)
        _fail(orch, task_id, note=note)
        _enqueue_parent_if_waiting(orch, task_id)
        spawned = _maybe_spawn_auto_revisit(
            orch, task_id, agent,
            error_context=_executor_failure_context(result, report),
        )
        _notify_failure_if_eligible(
            orch, task_id, failure_kind="session_failed",
            failure_note=note, auto_revisit_spawned=spawned,
        )
        return
```

**Line ~138 (self-blocked):**
```python
    if report.status == "blocked":
        note = f"self-blocked: {report.output_summary}"
        _fail(orch, task_id, note=note)
        _enqueue_parent_if_waiting(orch, task_id)
        _notify_failure_if_eligible(
            orch, task_id, failure_kind="self_blocked",
            failure_note=note, auto_revisit_spawned=False,
            last_summary=report.output_summary or "",
        )
        return
```

**Line ~187 (invalid delegate):**
```python
        err = _validate_delegate(orch, decision)
        if err is not None:
            note = f"invalid delegate: {err}"
            _fail(orch, task_id, note=note)
            _enqueue_parent_if_waiting(orch, task_id)
            _notify_failure_if_eligible(
                orch, task_id, failure_kind="invalid_delegate",
                failure_note=note, auto_revisit_spawned=False,
            )
            return
```

**Line ~261 (unknown action):**
```python
    # ---- 8. Unknown action ----
    note = f"unknown action: {decision.action}"
    _fail(orch, task_id, note=note)
    _enqueue_parent_if_waiting(orch, task_id)
    _notify_failure_if_eligible(
        orch, task_id, failure_kind="unknown_action",
        failure_note=note, auto_revisit_spawned=False,
    )
```

**Line ~616 (cascade-fail in `_enqueue_parent_if_waiting`):**
```python
    failed = [s for s in siblings if s.status == TaskStatus.FAILED]
    if failed:
        first = failed[0]
        note = f"delegated child {first.id} failed: {first.note or '(no note)'}"
        _fail(orch, parent.id, note=note)
        _enqueue_parent_if_waiting(orch, parent.id)
        _notify_failure_if_eligible(
            orch, parent.id, failure_kind="cascade_fail",
            failure_note=note, auto_revisit_spawned=False,
        )
        return
```

- [ ] **Step 5: Run tests to verify pass**

Run: `uv run pytest tests/orchestrator/test_notify_failed_gate.py tests/orchestrator/ -v`
Expected: All pass.

- [ ] **Step 6: Run integration tests**

Run: `uv run pytest tests/ -v -m integration`
Expected: All pass — orchestration loop is the regression-prone surface.

- [ ] **Step 7: Commit**

```bash
git add src/orchestrator/run_step.py tests/orchestrator/test_notify_failed_gate.py
git commit -m "$(cat <<'EOF'
feat(orch): notify_failed at every _fail call site, gated

_notify_failure_if_eligible gate enforces:
  - feishu_notifications.enabled + notify_on_failure=true
  - task.cancelled_at IS NULL
  - auto-revisit did not spawn

Wired into all six _fail call sites with appropriate failure_kind.
EOF
)"
```

---

## Task 10: Extract `revisit_from_notification` in-process helper

**Files:**
- Modify: `src/daemon/routes/tasks.py` (`revisit_task` at lines 448–~530)
- Test: `tests/daemon/test_revisit_from_notification.py` (new)

- [ ] **Step 1: Write failing test**

Create `tests/daemon/test_revisit_from_notification.py`:

```python
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest


@pytest.fixture()
def org_with_failed_task(tmp_path: Path):
    from src.infrastructure.database import Database
    from src.infrastructure.audit_logger import AuditLogger
    from src.models import TaskRecord, TaskStatus

    db = Database(tmp_path / "opc.db")
    db.insert_task(TaskRecord(
        id="TASK-1", brief="ferry scraper", team="engineering",
        assigned_agent="manager", status=TaskStatus.FAILED,
    ))
    org = MagicMock()
    org.db = db
    org.slug = "acme"
    import threading
    org.db_lock = MagicMock()
    org.db_lock.__aenter__ = MagicMock(return_value=None)
    org.db_lock.__aexit__ = MagicMock(return_value=None)
    org.orchestrator = MagicMock()
    state = MagicMock()
    state.queue = MagicMock()
    return org, state, db


@pytest.mark.asyncio
async def test_revisit_from_notification_spawns_new_root(org_with_failed_task):
    from src.daemon.routes.tasks import revisit_from_notification
    org, state, db = org_with_failed_task
    new_id = await revisit_from_notification(
        org, state,
        task_id="TASK-1",
        founder_note="add Service Class field",
        actor="feishu-reply",
    )
    assert new_id != "TASK-1"
    new_task = db.get_task(new_id)
    assert new_task is not None
    assert new_task.revisit_of_task_id == "TASK-1"
    assert new_task.brief == "ferry scraper"  # inherited
    assert new_task.team == "engineering"
    audit_rows = db.get_audit_logs(new_id)
    revisit_of = [r for r in audit_rows if r["action"] == "revisit_of"]
    assert len(revisit_of) == 1
    payload = revisit_of[0]["payload"]
    assert payload.get("actor") == "feishu-reply"
    assert payload.get("founder_note") == "add Service Class field"


@pytest.mark.asyncio
async def test_revisit_from_notification_raises_when_ineligible(org_with_failed_task):
    from fastapi import HTTPException
    from src.daemon.routes.tasks import revisit_from_notification
    from src.models import TaskStatus
    org, state, db = org_with_failed_task
    # Mutate the task to an ineligible state
    db.update_task("TASK-1", status=TaskStatus.IN_PROGRESS)
    with pytest.raises(HTTPException) as exc:
        await revisit_from_notification(
            org, state, task_id="TASK-1", founder_note="x", actor="feishu-reply",
        )
    assert exc.value.status_code == 409
    assert "cannot_revisit" in str(exc.value.detail)
```

(Add `pytest-asyncio` import compatibility if the project uses `@pytest.mark.asyncio` — check existing async tests for the convention; some projects use `asyncio_mode = "auto"` in `pyproject.toml`.)

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/daemon/test_revisit_from_notification.py -v`
Expected: FAIL — `revisit_from_notification` does not exist.

- [ ] **Step 3: Extract the helper**

Edit `src/daemon/routes/tasks.py`. Read the current `revisit_task` route body (lines 448–~530) carefully. Extract the validation + insert + audit logic into a new async helper, leaving the route as a thin wrapper:

```python
async def revisit_from_notification(
    org,
    state,
    *,
    task_id: str,
    founder_note: str | None,
    actor: str,
    session_timeout_seconds: int | None = None,
) -> str:
    """Spawn a new root task linked to the predecessor.

    Mirrors the POST /tasks/{id}/revisit HTTP handler. Reused by the
    Feishu listener so HTTP and Feishu surfaces cannot drift.

    Args:
        actor: "cli" (HTTP route) or "feishu-reply" (listener). Recorded
            on the revisit_of audit row.
        session_timeout_seconds: Override; if None, inherit from predecessor.

    Returns the new root task_id. Raises HTTPException for 404 / 409.
    """
    from src.infrastructure.audit_logger import AuditLogger
    from src.models import TaskRecord, TaskStatus

    chain = org.db.walk_ancestors(task_id, max_hops=20)
    if not chain:
        raise HTTPException(status_code=404, detail=f"task {task_id} not found")
    root = chain[-1]
    if root.status not in _REVISIT_ELIGIBLE_STATUSES:
        raise HTTPException(
            status_code=409,
            detail={"code": "cannot_revisit", "current_status": root.status.value},
        )

    new_timeout = (session_timeout_seconds
                   if session_timeout_seconds is not None
                   else root.session_timeout_seconds)
    cascade = [t.id for t in reversed(chain)]
    prior_status = root.status.value
    if root.cancelled_at is not None and root.status == TaskStatus.FAILED:
        prior_status = "failed-cancelled"

    async with org.db_lock:
        new_id = org.db.next_task_id()
        org.db.insert_task(TaskRecord(
            id=new_id, brief=root.brief, team=root.team,
            assigned_agent=root.assigned_agent,
            status=TaskStatus.PENDING,
            parent_task_id=None,
            revisit_of_task_id=root.id,
            session_timeout_seconds=new_timeout,
        ))
        AuditLogger(org.db).log_revisit_of(
            task_id=new_id,
            predecessor_root=root.id,
            flagged=task_id,
            cascade=cascade,
            prior_status=prior_status,
            founder_note=founder_note,
            actor=actor,
        )
        AuditLogger(org.db).log_revisit_spawned(
            predecessor_task_id=root.id, new_root=new_id,
        )

    if state.queue is not None:
        state.queue.put_nowait(org.slug, new_id)
    return new_id
```

Then refactor the existing `revisit_task` route to call this helper. Replace the route body with:

```python
@router.post("/{task_id}/revisit")
async def revisit_task(task_id: str, body: RevisitBody, org: OrgDep,
                      request: Request) -> dict:
    state: DaemonState = request.app.state.daemon
    new_id = await revisit_from_notification(
        org, state,
        task_id=task_id,
        founder_note=body.founder_note,
        actor="cli",
        session_timeout_seconds=body.session_timeout_seconds,
    )
    return {"new_root_task_id": new_id, "predecessor": task_id}
```

Update `log_revisit_of` in `src/infrastructure/audit_logger.py` to accept the new `actor` keyword:

```python
def log_revisit_of(
    self, *, task_id: str, predecessor_root: str, flagged: str,
    cascade: list, prior_status: str, founder_note: str | None,
    actor: str = "cli",
) -> None:
    self._log(
        task_id=task_id,
        agent="orchestrator",
        action="revisit_of",
        payload={
            "predecessor_root": predecessor_root,
            "flagged": flagged,
            "cascade": cascade,
            "prior_status": prior_status,
            "founder_note": founder_note,
            "actor": actor,
        },
    )
```

(If existing call sites in the route already construct the payload dict inline rather than going through `log_revisit_of`, follow whatever pattern is in place — the goal is `actor` lands in the audit payload.)

- [ ] **Step 4: Run tests to verify pass**

Run: `uv run pytest tests/daemon/test_revisit_from_notification.py tests/daemon/ -v`
Expected: All pass. Existing `revisit_task` HTTP-route tests should still pass since route delegates to helper.

- [ ] **Step 5: Commit**

```bash
git add src/daemon/routes/tasks.py src/infrastructure/audit_logger.py tests/daemon/test_revisit_from_notification.py
git commit -m "$(cat <<'EOF'
refactor(daemon): extract revisit_from_notification in-process helper

Mirrors resolve_escalation_in_process pattern. Adds actor field
('cli' | 'feishu-reply') to the revisit_of audit row.
Existing HTTP route delegates to helper.
EOF
)"
```

---

## Task 11: Extract `dispatch_via_feishu` in-process helper

**Files:**
- Modify: `src/daemon/routes/tasks.py` (`submit_task` at lines 37–60)
- Test: `tests/daemon/test_dispatch_via_feishu.py` (new)

- [ ] **Step 1: Write failing test**

Create `tests/daemon/test_dispatch_via_feishu.py`:

```python
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest


@pytest.fixture()
def org_with_engineering_team(tmp_path: Path):
    from src.infrastructure.database import Database
    db = Database(tmp_path / "opc.db")
    org = MagicMock()
    org.db = db
    org.slug = "acme"
    org.db_lock = MagicMock()
    org.db_lock.__aenter__ = MagicMock(return_value=None)
    org.db_lock.__aexit__ = MagicMock(return_value=None)
    teams = MagicMock()
    teams.teams.return_value = ["engineering", "customer-care"]
    teams.manager_for_team.return_value = MagicMock(name="engineering_head")
    teams.manager_for_team.return_value.name = "engineering_head"
    org.teams = teams
    state = MagicMock()
    state.queue = MagicMock()
    return org, state, db


@pytest.mark.asyncio
async def test_dispatch_via_feishu_creates_task(org_with_engineering_team):
    from src.daemon.routes.tasks import dispatch_via_feishu, DispatchIntent
    org, state, db = org_with_engineering_team
    intent = DispatchIntent(team="engineering", brief="fix the thing")
    task_id, team = await dispatch_via_feishu(
        org, state, intent=intent, sender_id="ou_x", event_id="evt_1",
    )
    assert task_id.startswith("TASK-")
    assert team == "engineering"
    task = db.get_task(task_id)
    assert task is not None
    assert task.brief == "fix the thing"


@pytest.mark.asyncio
async def test_dispatch_via_feishu_rejects_empty_brief(org_with_engineering_team):
    from src.daemon.routes.tasks import dispatch_via_feishu, DispatchError, DispatchIntent
    org, state, _ = org_with_engineering_team
    intent = DispatchIntent(team="engineering", brief="   ")
    with pytest.raises(DispatchError) as exc:
        await dispatch_via_feishu(
            org, state, intent=intent, sender_id="ou_x", event_id="evt_2",
        )
    assert exc.value.reason == "empty_brief"


@pytest.mark.asyncio
async def test_dispatch_via_feishu_rejects_unknown_team(org_with_engineering_team):
    from src.daemon.routes.tasks import dispatch_via_feishu, DispatchError, DispatchIntent
    org, state, _ = org_with_engineering_team
    intent = DispatchIntent(team="nonexistent", brief="x")
    with pytest.raises(DispatchError) as exc:
        await dispatch_via_feishu(
            org, state, intent=intent, sender_id="ou_x", event_id="evt_3",
        )
    assert exc.value.reason == "unknown_team"
    assert "engineering" in exc.value.valid_teams


@pytest.mark.asyncio
async def test_dispatch_via_feishu_falls_back_to_default_team_when_none(org_with_engineering_team):
    from src.daemon.routes.tasks import dispatch_via_feishu, DispatchIntent
    org, state, _ = org_with_engineering_team
    intent = DispatchIntent(team=None, brief="auto-team")
    # The submit_task route defaults team to "engineering" when None.
    task_id, team = await dispatch_via_feishu(
        org, state, intent=intent, sender_id="ou_x", event_id="evt_4",
    )
    assert team == "engineering"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/daemon/test_dispatch_via_feishu.py -v`
Expected: FAIL — `dispatch_via_feishu`, `DispatchError`, `DispatchIntent` re-export not yet present.

- [ ] **Step 3: Add `DispatchError` and `dispatch_via_feishu`**

Edit `src/daemon/routes/tasks.py`. Add near the top of the file (alongside other helpers):

```python
from src.infrastructure.feishu.reply_parser import DispatchIntent  # re-export


class DispatchError(Exception):
    def __init__(self, reason: str, valid_teams: list[str] | None = None):
        self.reason = reason
        self.valid_teams = valid_teams or []
        super().__init__(reason)


async def dispatch_via_feishu(
    org,
    state,
    *,
    intent: DispatchIntent,
    sender_id: str,
    event_id: str,
) -> tuple[str, str]:
    """Create a task from a Feishu DISPATCH intent. Mirrors POST /tasks.

    Returns (task_id, resolved_team).
    Raises DispatchError(reason=...) with reason in:
        empty_brief, unknown_team, dispatch_failed.
    """
    from src.infrastructure.audit_logger import AuditLogger
    from src.models import TaskRecord

    if not intent.brief or not intent.brief.strip():
        raise DispatchError("empty_brief")

    team = intent.team or "engineering"
    registry = org.teams
    valid = registry.teams() if registry is not None else []
    if registry is None or team not in valid:
        raise DispatchError("unknown_team", valid_teams=list(valid))

    try:
        manager = registry.manager_for_team(team)
        async with org.db_lock:
            task_id = org.db.next_task_id()
            org.db.insert_task(TaskRecord(
                id=task_id,
                brief=intent.brief.strip(),
                team=team,
                assigned_agent=manager.name,
            ))
            AuditLogger(org.db).log_dispatch_via_feishu_accepted(
                task_id=task_id, team=team, sender_id=sender_id,
                feishu_event_id=event_id,
            )
    except DispatchError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise DispatchError("dispatch_failed") from exc

    if state.queue is not None:
        state.queue.put_nowait(org.slug, task_id)
    return task_id, team
```

Add the audit method to `src/infrastructure/audit_logger.py`:

```python
def log_dispatch_via_feishu_accepted(
    self, *, task_id: str, team: str, sender_id: str, feishu_event_id: str,
) -> None:
    self._log(
        task_id=task_id, agent="orchestrator",
        action="dispatch_via_feishu_accepted",
        payload={"team": team, "sender_id": sender_id,
                 "feishu_event_id": feishu_event_id},
    )


def log_dispatch_via_feishu_rejected(
    self, *, reason: str, sender_id: str, feishu_event_id: str,
    task_id: str | None = None,
) -> None:
    self._log(
        task_id=task_id or "(none)", agent="orchestrator",
        action="dispatch_via_feishu_rejected",
        payload={"reason": reason, "sender_id": sender_id,
                 "feishu_event_id": feishu_event_id},
    )
```

- [ ] **Step 4: Run tests to verify pass**

Run: `uv run pytest tests/daemon/test_dispatch_via_feishu.py tests/daemon/ -v`
Expected: 4 PASS

- [ ] **Step 5: Commit**

```bash
git add src/daemon/routes/tasks.py src/infrastructure/audit_logger.py tests/daemon/test_dispatch_via_feishu.py
git commit -m "$(cat <<'EOF'
feat(daemon): dispatch_via_feishu in-process helper

DispatchError(reason in {empty_brief, unknown_team, dispatch_failed}).
Mirrors submit_task route's team-default logic. Audits accepted/rejected.
EOF
)"
```

---

## Task 12: Listener — bifurcate step 3 (reply vs dispatch vs drop)

**Files:**
- Modify: `src/daemon/feishu_listener.py` (`_handle_event_async` at lines 103–188; `FeishuEventListener` constructor at lines 36–48)
- Test: `tests/daemon/test_feishu_listener_bifurcation.py` (new)

- [ ] **Step 1: Write failing test**

Create `tests/daemon/test_feishu_listener_bifurcation.py`:

```python
from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.infrastructure.database import Database
from src.infrastructure.audit_logger import AuditLogger


def _make_event(*, root_id: str | None, text: str, sender_type: str = "user"):
    """Construct a fake Feishu im.message.receive_v1 event envelope."""
    import json
    content = json.dumps({"text": text})
    msg = SimpleNamespace(
        chat_id="oc_xyz",
        message_id="om_inbound_1",
        root_id=root_id,
        message_type="text",
        content=content,
    )
    sender = SimpleNamespace(sender_type=sender_type, sender_id=SimpleNamespace(
        open_id="ou_user_1"
    ))
    event = SimpleNamespace(
        event_id="evt_1",
        message=msg,
        sender=sender,
    )
    return event


@pytest.fixture()
def listener(tmp_path: Path):
    from src.daemon.feishu_listener import FeishuEventListener
    db = Database(tmp_path / "opc.db")
    audit = AuditLogger(db)
    loop = asyncio.new_event_loop()
    listener = FeishuEventListener(
        slug="acme", db=db, audit=audit, chat_id="oc_xyz",
        resolve_escalation=AsyncMock(),
        revisit_from_notification=AsyncMock(return_value="TASK-NEW"),
        dispatch_via_feishu=AsyncMock(return_value=("TASK-DISP", "engineering")),
        send_dispatch_confirmation=AsyncMock(),
        send_dispatch_error=AsyncMock(),
        allow_dispatch=True,
        loop=loop, app_id="x", app_secret="x", domain="feishu",
    )
    return listener, db, loop


def test_top_level_dispatch_routed(listener):
    l, db, loop = listener
    event = _make_event(root_id=None, text="DISPATCH engineering\nbrief here")
    loop.run_until_complete(l._handle_event_async(event))
    assert l._dispatch_via_feishu.called
    intent_kwarg = l._dispatch_via_feishu.call_args.kwargs["intent"]
    assert intent_kwarg.brief == "brief here"


def test_top_level_dispatch_dropped_when_disabled(tmp_path: Path):
    from src.daemon.feishu_listener import FeishuEventListener
    db = Database(tmp_path / "opc.db")
    audit = AuditLogger(db)
    loop = asyncio.new_event_loop()
    l = FeishuEventListener(
        slug="acme", db=db, audit=audit, chat_id="oc_xyz",
        resolve_escalation=AsyncMock(),
        revisit_from_notification=AsyncMock(),
        dispatch_via_feishu=AsyncMock(),
        send_dispatch_confirmation=AsyncMock(),
        send_dispatch_error=AsyncMock(),
        allow_dispatch=False,  # OFF
        loop=loop, app_id="x", app_secret="x", domain="feishu",
    )
    event = _make_event(root_id=None, text="DISPATCH engineering\nbrief")
    loop.run_until_complete(l._handle_event_async(event))
    assert not l._dispatch_via_feishu.called


def test_threaded_reply_does_not_hit_dispatch(listener):
    l, db, loop = listener
    # Mint a notification first so the reply has a row to find
    db.mint_escalation_notification(
        feishu_message_id="om_root", org_slug="acme", task_id="TASK-1",
        chat_id="oc_xyz", expires_at="2099-01-01T00:00:00+00:00",
    )
    from src.models import TaskRecord, TaskStatus, BlockKind
    db.insert_task(TaskRecord(
        id="TASK-1", brief="x", team="engineering",
        assigned_agent="m", status=TaskStatus.BLOCKED,
        block_kind=BlockKind.ESCALATED,
    ))
    event = _make_event(root_id="om_root", text="APPROVE\nok")
    loop.run_until_complete(l._handle_event_async(event))
    assert not l._dispatch_via_feishu.called
    assert l._resolve_escalation.called
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/daemon/test_feishu_listener_bifurcation.py -v`
Expected: FAIL — listener constructor does not accept the new dependencies.

- [ ] **Step 3: Extend `FeishuEventListener` constructor**

Edit `src/daemon/feishu_listener.py`. Update the constructor signature (around lines 36–48):

```python
def __init__(
    self,
    *,
    slug: str,
    db: Database,
    audit: AuditLogger,
    chat_id: str,
    resolve_escalation: ResolveFn,
    revisit_from_notification: RevisitFn,
    dispatch_via_feishu: DispatchFn,
    send_dispatch_confirmation: SendConfirmFn,
    send_dispatch_error: SendErrorFn,
    allow_dispatch: bool,
    loop: asyncio.AbstractEventLoop,
    app_id: str,
    app_secret: str,
    domain: str,
) -> None:
    self._slug = slug
    self._db = db
    self._audit = audit
    self._chat_id = chat_id
    self._resolve_escalation = resolve_escalation
    self._revisit_from_notification = revisit_from_notification
    self._dispatch_via_feishu = dispatch_via_feishu
    self._send_dispatch_confirmation = send_dispatch_confirmation
    self._send_dispatch_error = send_dispatch_error
    self._allow_dispatch = allow_dispatch
    self._loop = loop
    self._app_id = app_id
    self._app_secret = app_secret
    self._domain = domain
```

Add the type aliases near the top of the file:

```python
from typing import Awaitable, Callable

ResolveFn = Callable[..., Awaitable[str]]
RevisitFn = Callable[..., Awaitable[str]]
DispatchFn = Callable[..., Awaitable[tuple[str, str]]]
SendConfirmFn = Callable[..., Awaitable[None]]
SendErrorFn = Callable[..., Awaitable[None]]
```

- [ ] **Step 4: Bifurcate step 3 in `_handle_event_async`**

Locate `_handle_event_async` (around lines 103–188). After step 2 (chat filter), refactor step 3 to bifurcate. The new shape:

```python
async def _handle_event_async(self, data) -> None:
    msg = data.message  # adjust per actual SDK envelope
    event_id = data.event_id

    # Step 1: Dedup
    if not self._db.record_processed_event(self._slug, event_id, "pending"):
        return

    # Step 2: Chat filter
    if msg.chat_id != self._chat_id:
        self._db.update_processed_event_outcome(self._slug, event_id, "ignored", "chat_mismatch")
        return

    # Step 3: Bifurcate by root_id
    if msg.root_id is None:
        # Top-level message → dispatch branch (if enabled)
        if not self._allow_dispatch:
            self._db.update_processed_event_outcome(self._slug, event_id, "ignored", "dispatch_disabled")
            return
        await self._handle_top_level_dispatch(data, msg, event_id)
        return

    # Reply branch (existing pipeline)
    await self._handle_threaded_reply(data, msg, event_id)
```

Split the existing pipeline body into a new method `_handle_threaded_reply(self, data, msg, event_id)`:

```python
async def _handle_threaded_reply(self, data, msg, event_id: str) -> None:
    # Step 4: Sender filter (drop bot self-echoes)
    if data.sender.sender_type == "app":
        self._db.update_processed_event_outcome(self._slug, event_id, "ignored", "bot_sender")
        return

    # Step 5: Notification lookup
    row = self._db.get_escalation_notification(msg.root_id)
    if row is None or row.get("consumed_at") is not None:
        self._db.update_processed_event_outcome(self._slug, event_id, "ignored", "no_notification")
        return
    # (existing expires_at check unchanged)

    # Step 6: Parse text
    from src.infrastructure.feishu.reply_parser import (
        extract_text_from_content, parse_reply,
    )
    text = extract_text_from_content(msg.message_type, msg.content)
    parsed = parse_reply(text) if text else None
    if parsed is None:
        self._audit.log_reply_rejected(
            task_id=row["task_id"], reason="parse_failed",
            feishu_event_id=event_id,
        )
        self._db.update_processed_event_outcome(self._slug, event_id, "rejected", "parse_failed")
        return

    # Step 7 + 8: routed by (kind, decision) — see Task 13
    # (placeholder for now; Task 13 fills in)
    await self._dispatch_reply_action(row, parsed, msg, event_id)
```

Add the empty `_dispatch_reply_action` stub for now (Task 13 fills it):

```python
async def _dispatch_reply_action(self, row, parsed, msg, event_id) -> None:
    # Existing escalation behavior preserved for back-compat with this task
    await self._resolve_escalation(
        org=None, state=None,  # adjust to real signature
        task_id=row["task_id"], decision=parsed.decision, rationale=parsed.rationale,
    )
    self._db.consume_escalation_notification(msg.root_id, consumed_by="feishu-reply")
    self._db.update_processed_event_outcome(self._slug, event_id, "consumed", None)
```

Add the dispatch handler:

```python
async def _handle_top_level_dispatch(self, data, msg, event_id: str) -> None:
    # Step 4d: Sender filter
    if data.sender.sender_type == "app":
        self._db.update_processed_event_outcome(self._slug, event_id, "ignored", "bot_sender")
        return

    # Step 5d: Parse
    from src.infrastructure.feishu.reply_parser import (
        extract_text_from_content, parse_top_level_message,
    )
    text = extract_text_from_content(msg.message_type, msg.content)
    intent = parse_top_level_message(text) if text else None
    sender_id = getattr(data.sender.sender_id, "open_id", "") or ""

    if intent is None:
        self._audit.log_dispatch_via_feishu_rejected(
            reason="parse_failed", sender_id=sender_id, feishu_event_id=event_id,
        )
        self._db.update_processed_event_outcome(self._slug, event_id, "rejected", "parse_failed")
        return

    # Step 6d–8d filled in Task 14
    pass
```

**Wire the factory.** In `maybe_start_feishu_listener_for_org` (same file), wrap each new helper in a closure that binds `org` and `state`, exactly like the existing `_resolve_for_listener` pattern at lines 211–220. Concretely:

```python
def maybe_start_feishu_listener_for_org(org, state, loop) -> None:
    # ... existing config / idempotence checks ...
    cfg = org.org_config.feishu_notifications  # or however it's accessed today

    async def _resolve_for_listener(*, task_id, decision, rationale):
        from src.daemon.routes.tasks import resolve_escalation_in_process
        return await resolve_escalation_in_process(
            org, state, task_id=task_id, decision=decision, rationale=rationale,
        )

    async def _revisit_for_listener(*, task_id, founder_note, actor):
        from src.daemon.routes.tasks import revisit_from_notification
        return await revisit_from_notification(
            org, state, task_id=task_id,
            founder_note=founder_note, actor=actor,
        )

    async def _dispatch_for_listener(*, intent, sender_id, event_id):
        from src.daemon.routes.tasks import dispatch_via_feishu
        return await dispatch_via_feishu(
            org, state, intent=intent,
            sender_id=sender_id, event_id=event_id,
        )

    async def _send_confirm_for_listener(*, task_id, team, brief):
        return await org.notifier.send_dispatch_confirmation(
            task_id=task_id, team=team, brief=brief,
        )

    async def _send_error_for_listener(*, reason, valid_teams):
        return await org.notifier.send_dispatch_error(
            reason=reason, valid_teams=valid_teams,
        )

    listener = FeishuEventListener(
        slug=org.slug, db=org.db, audit=org.audit,
        chat_id=cfg.chat_id,
        resolve_escalation=_resolve_for_listener,
        revisit_from_notification=_revisit_for_listener,
        dispatch_via_feishu=_dispatch_for_listener,
        send_dispatch_confirmation=_send_confirm_for_listener,
        send_dispatch_error=_send_error_for_listener,
        allow_dispatch=cfg.allow_dispatch,
        loop=loop, app_id=cfg.app_id, app_secret=cfg.app_secret,
        domain=cfg.region,
    )
    # ... existing thread-start logic ...
```

(Adjust attribute paths — `org.notifier`, `org.org_config`, `org.audit` — to whatever the current `state.py` / `app.py` exposes for an org. The pattern is what matters: bind `org`+`state` once at construction.)

- [ ] **Step 5: Run tests to verify pass**

Run: `uv run pytest tests/daemon/test_feishu_listener_bifurcation.py tests/daemon/ -v`
Expected: bifurcation test passes; existing listener tests still pass (the `_dispatch_reply_action` stub preserves escalation behavior).

- [ ] **Step 6: Commit**

```bash
git add src/daemon/feishu_listener.py tests/daemon/test_feishu_listener_bifurcation.py
git commit -m "$(cat <<'EOF'
feat(feishu): bifurcate listener at step 3 by root_id

Threaded messages → existing reply pipeline. Top-level messages → new
dispatch pipeline (gated by allow_dispatch). Reply pipeline action
routing extracted to _dispatch_reply_action for Task 13 to fill in.
EOF
)"
```

---

## Task 13: Listener — reply-branch routing by `kind` × verb

**Files:**
- Modify: `src/daemon/feishu_listener.py` (`_dispatch_reply_action`)
- Test: `tests/daemon/test_feishu_listener_kind_routing.py` (new)

- [ ] **Step 1: Write failing test**

Create `tests/daemon/test_feishu_listener_kind_routing.py`:

```python
from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.infrastructure.database import Database
from src.infrastructure.audit_logger import AuditLogger
from src.daemon.feishu_listener import FeishuEventListener
from src.models import TaskRecord, TaskStatus, BlockKind


def _mk_listener(tmp_path: Path):
    db = Database(tmp_path / "opc.db")
    audit = AuditLogger(db)
    loop = asyncio.new_event_loop()
    return FeishuEventListener(
        slug="acme", db=db, audit=audit, chat_id="oc_xyz",
        resolve_escalation=AsyncMock(),
        revisit_from_notification=AsyncMock(return_value="TASK-REVISIT"),
        dispatch_via_feishu=AsyncMock(),
        send_dispatch_confirmation=AsyncMock(),
        send_dispatch_error=AsyncMock(),
        allow_dispatch=False,
        loop=loop, app_id="x", app_secret="x", domain="feishu",
    ), db, loop


def _mk_notification(db: Database, *, kind: str, task_id: str = "TASK-1"):
    db.mint_escalation_notification(
        feishu_message_id="om_root", org_slug="acme", task_id=task_id,
        chat_id="oc_xyz", expires_at="2099-01-01T00:00:00+00:00",
        kind=kind,
    )


def _mk_event(text: str, root_id: str = "om_root"):
    import json
    msg = SimpleNamespace(
        chat_id="oc_xyz", message_id="om_in", root_id=root_id,
        message_type="text",
        content=json.dumps({"text": text}),
    )
    sender = SimpleNamespace(
        sender_type="user",
        sender_id=SimpleNamespace(open_id="ou_user_1"),
    )
    return SimpleNamespace(event_id="evt_1", message=msg, sender=sender)


def _insert_escalated_task(db: Database, task_id: str = "TASK-1"):
    db.insert_task(TaskRecord(
        id=task_id, brief="x", team="engineering",
        assigned_agent="m", status=TaskStatus.BLOCKED,
        block_kind=BlockKind.ESCALATED,
    ))


def _insert_failed_task(db: Database, task_id: str = "TASK-1"):
    db.insert_task(TaskRecord(
        id=task_id, brief="x", team="engineering",
        assigned_agent="m", status=TaskStatus.FAILED,
    ))


def test_escalation_approve_routes_to_resolve(tmp_path: Path):
    (l, db, loop) = _mk_listener(tmp_path)
    _insert_escalated_task(db)
    _mk_notification(db, kind="escalation")
    loop.run_until_complete(l._handle_event_async(_mk_event("APPROVE\nok")))
    assert l._resolve_escalation.called
    assert not l._revisit_from_notification.called


def test_escalation_revisit_is_verb_mismatch(tmp_path: Path):
    (l, db, loop) = _mk_listener(tmp_path)
    _insert_escalated_task(db)
    _mk_notification(db, kind="escalation")
    loop.run_until_complete(l._handle_event_async(_mk_event("REVISIT\nplease")))
    assert not l._resolve_escalation.called
    assert not l._revisit_from_notification.called
    # Notification still unconsumed
    row = db.get_escalation_notification("om_root")
    assert row["consumed_at"] is None


def test_failure_revisit_routes_to_revisit_helper(tmp_path: Path):
    (l, db, loop) = _mk_listener(tmp_path)
    _insert_failed_task(db)
    _mk_notification(db, kind="failure")
    loop.run_until_complete(l._handle_event_async(_mk_event("REVISIT\nadd field")))
    assert l._revisit_from_notification.called
    kwargs = l._revisit_from_notification.call_args.kwargs
    assert kwargs["task_id"] == "TASK-1"
    assert kwargs["founder_note"] == "add field"
    assert kwargs["actor"] == "feishu-reply"
    row = db.get_escalation_notification("om_root")
    assert row["consumed_at"] is not None
    assert row["consumed_by"] == "feishu-reply"


def test_failure_approve_is_verb_mismatch(tmp_path: Path):
    (l, db, loop) = _mk_listener(tmp_path)
    _insert_failed_task(db)
    _mk_notification(db, kind="failure")
    loop.run_until_complete(l._handle_event_async(_mk_event("APPROVE\nyes")))
    assert not l._revisit_from_notification.called
    assert not l._resolve_escalation.called
    row = db.get_escalation_notification("om_root")
    assert row["consumed_at"] is None  # unconsumed
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/daemon/test_feishu_listener_kind_routing.py -v`
Expected: FAIL — `_dispatch_reply_action` still routes everything to `resolve_escalation` regardless of `kind`.

- [ ] **Step 3: Implement `kind` × verb routing**

Edit `src/daemon/feishu_listener.py`. Replace the `_dispatch_reply_action` stub from Task 12:

```python
async def _dispatch_reply_action(self, row, parsed, msg, event_id) -> None:
    kind = row.get("kind", "escalation")
    decision = parsed.decision
    task_id = row["task_id"]

    if kind == "escalation" and decision in ("approve", "reject"):
        try:
            await self._resolve_escalation(
                task_id=task_id, decision=decision, rationale=parsed.rationale,
            )
        except Exception:  # noqa: BLE001
            self._audit.log_reply_rejected(
                task_id=task_id, reason="handler_exception",
                feishu_event_id=event_id,
            )
            self._db.update_processed_event_outcome(
                self._slug, event_id, "rejected", "handler_exception",
            )
            return
        self._db.consume_escalation_notification(msg.root_id, consumed_by="feishu-reply")
        self._audit.log_escalation_reply_processed(
            task_id=task_id, decision=decision,
            feishu_event_id=event_id,
        )
        self._db.update_processed_event_outcome(self._slug, event_id, "consumed", None)
        return

    if kind == "failure" and decision == "revisit":
        try:
            new_id = await self._revisit_from_notification(
                task_id=task_id,
                founder_note=parsed.rationale,
                actor="feishu-reply",
            )
        except Exception as exc:  # noqa: BLE001
            reason = "cannot_revisit" if "cannot_revisit" in str(exc) else "handler_exception"
            self._audit.log_reply_rejected(
                task_id=task_id, reason=reason, feishu_event_id=event_id,
            )
            # Leave notification unconsumed (spec §6.3 step 8)
            self._db.update_processed_event_outcome(self._slug, event_id, "rejected", reason)
            return
        self._db.consume_escalation_notification(msg.root_id, consumed_by="feishu-reply")
        self._audit.log_failure_revisit_via_reply(
            predecessor_task_id=task_id, new_root=new_id,
            founder_note=parsed.rationale,
            feishu_message_id=msg.root_id, feishu_event_id=event_id,
        )
        self._db.update_processed_event_outcome(self._slug, event_id, "consumed", None)
        return

    # Verb mismatch
    self._audit.log_reply_rejected(
        task_id=task_id, reason="verb_mismatch", feishu_event_id=event_id,
    )
    # Leave notification unconsumed
    self._db.update_processed_event_outcome(self._slug, event_id, "rejected", "verb_mismatch")
```

Add the audit method `log_failure_revisit_via_reply` to `src/infrastructure/audit_logger.py`:

```python
def log_failure_revisit_via_reply(
    self, *, predecessor_task_id: str, new_root: str,
    founder_note: str | None, feishu_message_id: str, feishu_event_id: str,
) -> None:
    self._log(
        task_id=new_root, agent="orchestrator",
        action="failure_revisit_via_reply",
        payload={
            "predecessor_task_id": predecessor_task_id,
            "founder_note": founder_note,
            "feishu_message_id": feishu_message_id,
            "feishu_event_id": feishu_event_id,
        },
    )
```

(`log_reply_rejected` already accepts a `reason` field per the existing v1 spec; if not, extend it the same way.)

The `resolve_escalation` callable wired in the listener factory must be called with whatever shape it expects today — most existing call sites pass `(org, state, task_id=..., decision=..., rationale=...)`. Check the existing wrapper `_resolve_for_listener` in `maybe_start_feishu_listener_for_org` and pass-through accordingly. If your existing call shape differs, adjust the test mocks to match.

- [ ] **Step 4: Run tests to verify pass**

Run: `uv run pytest tests/daemon/test_feishu_listener_kind_routing.py tests/daemon/ -v`
Expected: 4 PASS

- [ ] **Step 5: Commit**

```bash
git add src/daemon/feishu_listener.py src/infrastructure/audit_logger.py tests/daemon/test_feishu_listener_kind_routing.py
git commit -m "$(cat <<'EOF'
feat(feishu): listener routes by notification kind x verb

escalation+APPROVE/REJECT → resolve_escalation (unchanged)
escalation+REVISIT → reply_rejected(verb_mismatch), unconsumed
failure+REVISIT → revisit_from_notification, consumed
failure+APPROVE/REJECT → reply_rejected(verb_mismatch), unconsumed
cannot_revisit failure → reply_rejected, unconsumed
EOF
)"
```

---

## Task 14: Listener — dispatch pipeline (steps 5d–8d)

**Files:**
- Modify: `src/daemon/feishu_listener.py` (`_handle_top_level_dispatch`)
- Test: `tests/daemon/test_feishu_listener_dispatch.py` (new)

- [ ] **Step 1: Write failing test**

Create `tests/daemon/test_feishu_listener_dispatch.py`:

```python
from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.infrastructure.database import Database
from src.infrastructure.audit_logger import AuditLogger
from src.daemon.feishu_listener import FeishuEventListener
from src.daemon.routes.tasks import DispatchError


def _mk_listener(tmp_path: Path, *, allow_dispatch: bool = True):
    db = Database(tmp_path / "opc.db")
    audit = AuditLogger(db)
    loop = asyncio.new_event_loop()
    return FeishuEventListener(
        slug="acme", db=db, audit=audit, chat_id="oc_xyz",
        resolve_escalation=AsyncMock(),
        revisit_from_notification=AsyncMock(),
        dispatch_via_feishu=AsyncMock(return_value=("TASK-DISP", "engineering")),
        send_dispatch_confirmation=AsyncMock(),
        send_dispatch_error=AsyncMock(),
        allow_dispatch=allow_dispatch,
        loop=loop, app_id="x", app_secret="x", domain="feishu",
    ), db, loop


def _mk_event(text: str):
    import json
    msg = SimpleNamespace(
        chat_id="oc_xyz", message_id="om_in", root_id=None,
        message_type="text", content=json.dumps({"text": text}),
    )
    sender = SimpleNamespace(
        sender_type="user",
        sender_id=SimpleNamespace(open_id="ou_user_1"),
    )
    return SimpleNamespace(event_id="evt_1", message=msg, sender=sender)


def test_dispatch_success_sends_confirmation(tmp_path: Path):
    l, db, loop = _mk_listener(tmp_path)
    loop.run_until_complete(l._handle_event_async(
        _mk_event("DISPATCH engineering\nfix the thing")
    ))
    assert l._dispatch_via_feishu.called
    assert l._send_dispatch_confirmation.called
    confirm_kwargs = l._send_dispatch_confirmation.call_args.kwargs
    assert confirm_kwargs["task_id"] == "TASK-DISP"
    assert confirm_kwargs["team"] == "engineering"


def test_dispatch_empty_brief_sends_error(tmp_path: Path):
    l, db, loop = _mk_listener(tmp_path)
    # Make dispatch_via_feishu raise empty_brief
    l._dispatch_via_feishu.side_effect = DispatchError("empty_brief")
    loop.run_until_complete(l._handle_event_async(
        _mk_event("DISPATCH engineering\nactual brief here")
    ))
    assert l._send_dispatch_error.called
    err_kwargs = l._send_dispatch_error.call_args.kwargs
    assert "empty_brief" in err_kwargs["reason"]


def test_dispatch_unknown_team_lists_valid(tmp_path: Path):
    l, db, loop = _mk_listener(tmp_path)
    l._dispatch_via_feishu.side_effect = DispatchError(
        "unknown_team", valid_teams=["engineering", "customer-care"],
    )
    loop.run_until_complete(l._handle_event_async(
        _mk_event("DISPATCH wrongteam\nbrief")
    ))
    assert l._send_dispatch_error.called
    err_kwargs = l._send_dispatch_error.call_args.kwargs
    assert "unknown_team" in err_kwargs["reason"]
    assert "engineering" in err_kwargs["valid_teams"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/daemon/test_feishu_listener_dispatch.py -v`
Expected: FAIL — dispatch pipeline not implemented; `_send_dispatch_confirmation` / `_send_dispatch_error` not called.

- [ ] **Step 3: Implement steps 5d–8d**

Edit `src/daemon/feishu_listener.py`. Replace the `_handle_top_level_dispatch` body from Task 12:

```python
async def _handle_top_level_dispatch(self, data, msg, event_id: str) -> None:
    # Step 4d: Sender filter (drop bot self-echoes)
    if data.sender.sender_type == "app":
        self._db.update_processed_event_outcome(self._slug, event_id, "ignored", "bot_sender")
        return

    # Step 5d: Parse
    from src.infrastructure.feishu.reply_parser import (
        extract_text_from_content, parse_top_level_message,
    )
    text = extract_text_from_content(msg.message_type, msg.content)
    intent = parse_top_level_message(text) if text else None
    sender_id = getattr(data.sender.sender_id, "open_id", "") or ""

    if intent is None:
        self._audit.log_dispatch_via_feishu_rejected(
            reason="parse_failed", sender_id=sender_id, feishu_event_id=event_id,
        )
        self._db.update_processed_event_outcome(self._slug, event_id, "rejected", "parse_failed")
        # No error card sent for parse failures — message wasn't recognizably a DISPATCH
        return

    # Step 6d: Dispatch
    from src.daemon.routes.tasks import DispatchError
    try:
        task_id, team = await self._dispatch_via_feishu(
            intent=intent, sender_id=sender_id, event_id=event_id,
        )
    except DispatchError as exc:
        self._audit.log_dispatch_via_feishu_rejected(
            reason=exc.reason, sender_id=sender_id, feishu_event_id=event_id,
        )
        # Step 8d (rejection path): send error card
        reason_text = exc.reason
        if exc.reason == "unknown_team" and intent.team:
            reason_text = f'unknown team "{intent.team}"'
        try:
            await self._send_dispatch_error(
                reason=reason_text, valid_teams=exc.valid_teams,
            )
        except Exception:  # noqa: BLE001
            pass
        self._db.update_processed_event_outcome(self._slug, event_id, "rejected", exc.reason)
        return

    # Step 7d: Confirmation card
    try:
        await self._send_dispatch_confirmation(
            task_id=task_id, team=team, brief=intent.brief,
        )
    except Exception:  # noqa: BLE001
        # Audited inside send_dispatch_confirmation; task is already created
        pass
    self._db.update_processed_event_outcome(self._slug, event_id, "consumed", None)
```

- [ ] **Step 4: Run tests to verify pass**

Run: `uv run pytest tests/daemon/test_feishu_listener_dispatch.py -v`
Expected: 3 PASS

- [ ] **Step 5: Commit**

```bash
git add src/daemon/feishu_listener.py tests/daemon/test_feishu_listener_dispatch.py
git commit -m "$(cat <<'EOF'
feat(feishu): listener dispatch pipeline (steps 5d-8d)

Top-level DISPATCH → dispatch_via_feishu → send_dispatch_confirmation.
DispatchError → send_dispatch_error with valid-teams list for unknown_team.
parse_failed → audit-only (no error card; message wasn't a recognizable DISPATCH).
EOF
)"
```

---

## Task 15: Daemon-restart sweep — reclassify as failure

**Files:**
- Modify: `src/daemon/__main__.py` (`_sweep_on_startup` at lines 31–74; specifically `notify_escalated` call at lines 51–54)
- Test: `tests/integration/test_daemon_restart_sweep.py` (extend if exists, else create minimal new test)

- [ ] **Step 1: Read existing sweep code**

Run: `uv run -m bash -c "sed -n '31,74p' src/daemon/__main__.py"` (or use Read tool on the file).
Confirm the `notify_escalated` call site and what data it passes (task_id, agent, reason).

- [ ] **Step 2: Write failing test**

Create `tests/integration/test_daemon_restart_sweep_failure_notify.py`:

```python
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock


def test_sweep_calls_notify_failed_not_escalated(tmp_path: Path):
    """Daemon-restart sweep should classify mid-task failures as failures,
    not escalations — APPROVE/REJECT don't make sense for FAILED tasks."""
    from src.daemon.__main__ import _sweep_on_startup
    from src.infrastructure.database import Database
    from src.models import TaskRecord, TaskStatus

    db = Database(tmp_path / "opc.db")
    db.insert_task(TaskRecord(
        id="TASK-1", brief="x", team="engineering",
        assigned_agent="m", status=TaskStatus.IN_PROGRESS,
    ))

    org = MagicMock()
    org.db = db
    org.slug = "acme"
    org.orchestrator = MagicMock()

    _sweep_on_startup([org])

    assert org.orchestrator.notify_failed.called
    assert not org.orchestrator.notify_escalated.called
    kwargs = org.orchestrator.notify_failed.call_args.kwargs
    assert kwargs["failure_kind"] == "daemon_restart"

    # Task is FAILED
    task = db.get_task("TASK-1")
    assert task.status == TaskStatus.FAILED
```

- [ ] **Step 3: Run test to verify it fails**

Run: `uv run pytest tests/integration/test_daemon_restart_sweep_failure_notify.py -v`
Expected: FAIL — sweep calls `notify_escalated`, not `notify_failed`.

- [ ] **Step 4: Update the sweep**

Edit `src/daemon/__main__.py` `_sweep_on_startup`. Replace the `notify_escalated` call (around lines 51–54) with:

```python
                        org.orchestrator.notify_failed(
                            task_id=task.id,
                            agent=task.assigned_agent or "(unknown)",
                            failure_kind="daemon_restart",
                            failure_note="daemon restarted mid-task",
                        )
```

The audit log call right above this (currently `log_escalation(...)`) should be left unchanged for now — it still records the recovery action. Note: spec §10 doesn't require changing the audit-action name for this path; only the notification routing changes.

- [ ] **Step 5: Run tests to verify pass**

Run: `uv run pytest tests/integration/test_daemon_restart_sweep_failure_notify.py tests/integration/ -v -m integration`
Expected: All pass.

- [ ] **Step 6: Commit**

```bash
git add src/daemon/__main__.py tests/integration/test_daemon_restart_sweep_failure_notify.py
git commit -m "$(cat <<'EOF'
fix(daemon): restart sweep classifies mid-task as failure, not escalation

A daemon-restarted task ends up FAILED, not BLOCKED/ESCALATED, so APPROVE
/REJECT doesn't apply. Replace notify_escalated with notify_failed(kind=
'daemon_restart'). Audit row at this call site unchanged for back-compat.
EOF
)"
```

---

## Task 16: CLI — `opc revisit` consumes open failure notifications

**Files:**
- Modify: `src/cli.py` (`cmd_revisit` at lines 1455–1534)
- Test: `tests/test_cli_revisit_consumes_failure.py` (new)

- [ ] **Step 1: Write failing test**

Create `tests/test_cli_revisit_consumes_failure.py`:

```python
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest


def test_cli_revisit_consumes_open_failure_notifications(tmp_path: Path):
    """When the founder runs `opc revisit TASK-X` from CLI after a
    Feishu failure card was sent, the open notification row should be
    marked consumed_by='cli-fallback' so the listener silently no-ops
    if the founder later replies REVISIT in-thread."""
    from src.infrastructure.database import Database
    from src.models import TaskRecord, TaskStatus

    db = Database(tmp_path / "opc.db")
    db.insert_task(TaskRecord(
        id="TASK-9", brief="x", team="engineering",
        assigned_agent="m", status=TaskStatus.FAILED,
    ))
    db.mint_escalation_notification(
        feishu_message_id="om_root", org_slug="acme", task_id="TASK-9",
        chat_id="oc_xyz", expires_at="2099-01-01T00:00:00+00:00",
        kind="failure",
    )

    # Simulate the CLI handler's consume-on-revisit step. Real CLI does an
    # HTTP POST to /revisit; this test exercises the consumer hook directly
    # to keep the test scoped.
    from src.cli import _consume_open_failure_notifications_for_task
    _consume_open_failure_notifications_for_task(db, "TASK-9")

    row = db.get_escalation_notification("om_root")
    assert row["consumed_at"] is not None
    assert row["consumed_by"] == "cli-fallback"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_cli_revisit_consumes_failure.py -v`
Expected: FAIL — `_consume_open_failure_notifications_for_task` does not exist.

- [ ] **Step 3: Add the helper + hook into `cmd_revisit`**

Edit `src/cli.py`. Add a small helper near the top (after imports):

```python
def _consume_open_failure_notifications_for_task(db, task_id: str) -> None:
    """Mark any open kind='failure' notification rows for task_id consumed
    with consumed_by='cli-fallback'. Idempotent."""
    for nrow in db.list_open_notifications_for_task(task_id):
        if nrow.get("kind") == "failure":
            db.consume_escalation_notification(
                nrow["feishu_message_id"], consumed_by="cli-fallback",
            )
```

In `cmd_revisit` (around lines 1455–1534), after a successful HTTP revisit POST returns 200, add a call to the helper. The CLI talks to the daemon over HTTP, so it doesn't hold a `Database` directly — instead, add a new daemon HTTP route or do the consumption server-side inside `revisit_from_notification`.

**Server-side approach (preferred — avoids a second round-trip):** Add the consume step inside `revisit_from_notification` for `actor="cli"` only:

In `src/daemon/routes/tasks.py` `revisit_from_notification`, after the `audit.log_revisit_spawned(...)` call:

```python
    # Consume any open failure-notification rows so a later Feishu reply no-ops.
    # (Mirrors resolve_escalation_in_process's behavior.)
    if actor == "cli":
        for nrow in org.db.list_open_notifications_for_task(task_id):
            if nrow.get("kind") == "failure":
                org.db.consume_escalation_notification(
                    nrow["feishu_message_id"], consumed_by="cli-fallback",
                )
```

Then update the test to exercise the helper end-to-end. Since the test as written above calls a module-level helper that doesn't really exist in this server-side approach, simplify by testing the server-side helper directly:

Replace the test body with:

```python
from src.daemon.routes.tasks import revisit_from_notification
from unittest.mock import MagicMock
from src.infrastructure.database import Database
from src.models import TaskRecord, TaskStatus
import asyncio


def test_revisit_with_cli_actor_consumes_failure_notification(tmp_path):
    db = Database(tmp_path / "opc.db")
    db.insert_task(TaskRecord(
        id="TASK-9", brief="x", team="engineering",
        assigned_agent="m", status=TaskStatus.FAILED,
    ))
    db.mint_escalation_notification(
        feishu_message_id="om_root", org_slug="acme", task_id="TASK-9",
        chat_id="oc_xyz", expires_at="2099-01-01T00:00:00+00:00",
        kind="failure",
    )
    org = MagicMock()
    org.db = db; org.slug = "acme"
    org.db_lock = MagicMock()
    org.db_lock.__aenter__ = MagicMock(return_value=None)
    org.db_lock.__aexit__ = MagicMock(return_value=None)
    org.orchestrator = MagicMock()
    state = MagicMock(); state.queue = MagicMock()

    asyncio.run(revisit_from_notification(
        org, state, task_id="TASK-9", founder_note="x", actor="cli",
    ))
    row = db.get_escalation_notification("om_root")
    assert row["consumed_at"] is not None
    assert row["consumed_by"] == "cli-fallback"


def test_revisit_with_feishu_actor_does_not_double_consume(tmp_path):
    """The listener consumes the row separately at step 8r. The helper
    must NOT also try to consume — would race with the listener consume."""
    db = Database(tmp_path / "opc.db")
    db.insert_task(TaskRecord(
        id="TASK-9", brief="x", team="engineering",
        assigned_agent="m", status=TaskStatus.FAILED,
    ))
    db.mint_escalation_notification(
        feishu_message_id="om_root", org_slug="acme", task_id="TASK-9",
        chat_id="oc_xyz", expires_at="2099-01-01T00:00:00+00:00",
        kind="failure",
    )
    org = MagicMock()
    org.db = db; org.slug = "acme"
    org.db_lock = MagicMock()
    org.db_lock.__aenter__ = MagicMock(return_value=None)
    org.db_lock.__aexit__ = MagicMock(return_value=None)
    org.orchestrator = MagicMock()
    state = MagicMock(); state.queue = MagicMock()

    asyncio.run(revisit_from_notification(
        org, state, task_id="TASK-9", founder_note="x", actor="feishu-reply",
    ))
    row = db.get_escalation_notification("om_root")
    assert row["consumed_at"] is None  # listener will consume separately
```

- [ ] **Step 4: Run tests to verify pass**

Run: `uv run pytest tests/test_cli_revisit_consumes_failure.py -v`
Expected: 2 PASS

- [ ] **Step 5: Commit**

```bash
git add src/daemon/routes/tasks.py tests/test_cli_revisit_consumes_failure.py
git commit -m "$(cat <<'EOF'
feat(daemon): CLI revisit consumes open failure notifications

When actor='cli' (HTTP route path), revisit_from_notification also marks
any open kind='failure' notification rows for the task consumed_by=
'cli-fallback' — so a later Feishu reply silently no-ops instead of
trying to spawn a duplicate revisit.

actor='feishu-reply' leaves the row alone — listener step 8r consumes it.
EOF
)"
```

---

## Task 17: Integration test — failure → REVISIT round-trip

**Files:**
- Test: `tests/integration/test_feishu_failure_revisit_e2e.py` (new)

- [ ] **Step 1: Read existing integration test infrastructure**

Read `tests/integration/test_feishu_notification_phase1.py` and `tests/integration/fake_feishu.py` (or equivalent fixture) to understand how the existing escalation e2e test is shaped. Reuse the same daemon-spawning fixtures.

- [ ] **Step 2: Write the test**

Create `tests/integration/test_feishu_failure_revisit_e2e.py`:

```python
from __future__ import annotations

import json
from pathlib import Path

import pytest


pytestmark = pytest.mark.integration


def test_self_blocked_task_notifies_and_revisit_via_reply(daemon, fake_feishu_client, org_slug):
    """Full round-trip:
      1. Dispatch a task that produces a self-blocked completion.
      2. Daemon transitions task to FAILED via _fail (self-blocked path).
      3. notify_failed sends a Feishu post with kind='failure'.
      4. Simulate inbound REVISIT reply with a founder_note.
      5. Listener calls revisit_from_notification.
      6. New root task exists, revisit_of_task_id points to predecessor.
      7. founder_note appears in the revisit_of audit row.
    """
    # Configure org with notify_on_failure=true (test fixture builds the config).
    # Submit a task; the fake agent harness returns status=blocked.
    task_id = daemon.dispatch_task(
        org=org_slug,
        brief="self-block to test failure notify",
        team="engineering",
        force_outcome="self_blocked",  # fake-agent contract
    )
    daemon.wait_for_status(org_slug, task_id, "failed", timeout=30)

    # The fake Feishu client should have received a failure card.
    sent = fake_feishu_client.sent_for(org_slug)
    assert any("FAILED" in m["title"] for m in sent)
    failure_msg = next(m for m in sent if "FAILED" in m["title"])
    feishu_message_id = failure_msg["returned_message_id"]

    # Verify notification row minted with kind='failure'.
    row = daemon.get_notification(org_slug, feishu_message_id)
    assert row["kind"] == "failure"
    assert row["task_id"] == task_id

    # Simulate inbound REVISIT reply.
    fake_feishu_client.simulate_inbound(
        chat_id=daemon.feishu_chat_id(org_slug),
        root_id=feishu_message_id,
        text="REVISIT\nadd Service Class field",
        sender_type="user",
        sender_open_id="ou_founder",
        event_id="evt_revisit_1",
    )

    # Wait for the new root task.
    new_id = daemon.wait_for_revisit(org_slug, of_task_id=task_id, timeout=10)
    assert new_id is not None

    new_task = daemon.get_task(org_slug, new_id)
    assert new_task["revisit_of_task_id"] == task_id
    assert new_task["brief"] == "self-block to test failure notify"

    audit = daemon.audit(org_slug, new_id)
    revisit_of = next(r for r in audit if r["action"] == "revisit_of")
    payload = revisit_of["payload"]
    assert payload["founder_note"] == "add Service Class field"
    assert payload.get("actor") == "feishu-reply"

    # Notification row consumed.
    row_after = daemon.get_notification(org_slug, feishu_message_id)
    assert row_after["consumed_at"] is not None
    assert row_after["consumed_by"] == "feishu-reply"
```

(The test relies on test fixtures `daemon`, `fake_feishu_client`, `org_slug` matching the existing phase-1 integration test conventions. If those fixtures don't expose helpers like `wait_for_revisit`, `feishu_chat_id`, `audit`, etc., add them to the test harness in the same commit — read the existing fixture file first to see the established shape.)

- [ ] **Step 3: Run test to verify it passes end-to-end**

Run: `uv run pytest tests/integration/test_feishu_failure_revisit_e2e.py -v -m integration`
Expected: PASS

If it fails on a fixture detail (helper not present), extend the fixtures inline rather than reshaping the test.

- [ ] **Step 4: Commit**

```bash
git add tests/integration/test_feishu_failure_revisit_e2e.py
# Plus any fixture additions
git commit -m "$(cat <<'EOF'
test(integration): failure → REVISIT round-trip via fake Feishu

Self-blocked task → failure notify → inbound REVISIT reply → new root
task spawned with founder_note in revisit_of audit row. Notification
row consumed_by='feishu-reply'.
EOF
)"
```

---

## Task 18: Integration test — DISPATCH round-trip

**Files:**
- Test: `tests/integration/test_feishu_dispatch_e2e.py` (new)

- [ ] **Step 1: Write the test**

Create `tests/integration/test_feishu_dispatch_e2e.py`:

```python
from __future__ import annotations

import pytest


pytestmark = pytest.mark.integration


def test_top_level_dispatch_creates_task_and_confirms(daemon, fake_feishu_client, org_slug):
    """Simulate a top-level DISPATCH message in the configured chat.
    Expect: task created, confirmation card sent."""
    fake_feishu_client.simulate_inbound(
        chat_id=daemon.feishu_chat_id(org_slug),
        root_id=None,
        text="DISPATCH engineering\nfix the 503 issue on weekday mornings",
        sender_type="user",
        sender_open_id="ou_founder",
        event_id="evt_dispatch_1",
    )

    # Daemon should create a task.
    task_id = daemon.wait_for_recent_task(org_slug, timeout=10)
    assert task_id is not None
    task = daemon.get_task(org_slug, task_id)
    assert task["brief"] == "fix the 503 issue on weekday mornings"
    assert task["team"] == "engineering"

    # Confirmation card sent.
    sent = fake_feishu_client.sent_for(org_slug)
    assert any(task_id in m["title"] for m in sent)
    confirm = next(m for m in sent if task_id in m["title"])
    body_text = "\n".join(confirm["body"])
    assert "engineering" in body_text
    assert "fix the 503" in body_text


def test_dispatch_unknown_team_sends_error(daemon, fake_feishu_client, org_slug):
    fake_feishu_client.simulate_inbound(
        chat_id=daemon.feishu_chat_id(org_slug),
        root_id=None,
        text="DISPATCH nonexistent\nbrief",
        sender_type="user",
        sender_open_id="ou_founder",
        event_id="evt_dispatch_2",
    )
    sent = fake_feishu_client.sent_for(org_slug)
    # An error card with "rejected" should arrive
    assert any("rejected" in m["title"].lower() for m in sent)
    err = next(m for m in sent if "rejected" in m["title"].lower())
    body_text = "\n".join(err["body"])
    assert "unknown team" in body_text


def test_dispatch_disabled_silently_drops(daemon_with_allow_dispatch_false, fake_feishu_client, org_slug):
    fake_feishu_client.simulate_inbound(
        chat_id=daemon_with_allow_dispatch_false.feishu_chat_id(org_slug),
        root_id=None,
        text="DISPATCH engineering\nbrief",
        sender_type="user",
        sender_open_id="ou_founder",
        event_id="evt_dispatch_3",
    )
    # No task, no confirmation
    sent = fake_feishu_client.sent_for(org_slug)
    assert not any("dispatched" in m["title"].lower() for m in sent)
```

- [ ] **Step 2: Run test to verify it passes**

Run: `uv run pytest tests/integration/test_feishu_dispatch_e2e.py -v -m integration`
Expected: PASS

- [ ] **Step 3: Commit**

```bash
git add tests/integration/test_feishu_dispatch_e2e.py
git commit -m "$(cat <<'EOF'
test(integration): top-level DISPATCH round-trip

Inbound DISPATCH → task created + confirmation card.
unknown_team → error card listing valid teams.
allow_dispatch=false → silent drop.
EOF
)"
```

---

## Task 19: Docs — README + CLAUDE.md updates

**Files:**
- Modify: `README.md` (Feishu setup section)
- Modify: `CLAUDE.md` (Feishu notifications section)

- [ ] **Step 1: Read existing sections**

Read the current Feishu sections in both files (search for "Feishu" with `grep -n "Feishu" README.md CLAUDE.md`). Note exact location to insert the new content.

- [ ] **Step 2: Update README.md**

In the Feishu setup section, after the existing `reply_ttl_hours` documentation, add:

````markdown
### Failed-task notifications

Set `notify_on_failure: true` in `feishu_notifications` to receive a push card whenever a task ends in `FAILED` (and the system did not auto-revisit). Failure cards differ from escalation cards in two ways:

- The verb is `REVISIT`, not `APPROVE`/`REJECT`.
- Replying spawns a new root task linked to the failed predecessor (same as `opc revisit`).

```yaml
feishu_notifications:
  # ... (existing fields)
  notify_on_failure: true
```

Reply syntax:

```
REVISIT
<optional note that becomes founder_note on the new root>
```

If you don't reply, the task stays failed. Resolve via `opc revisit <task_id>` from the CLI any time before the notification's TTL expires.

### Dispatching new tasks from Feishu

Set `allow_dispatch: true` to enable top-level dispatch from the configured chat:

```yaml
feishu_notifications:
  # ... (existing fields)
  allow_dispatch: true
```

In the chat (NOT as a reply — start a new top-level message), send:

```
DISPATCH [team]
<brief over one or more lines>
```

Team is optional. If omitted, defaults to `engineering`; if `engineering` doesn't exist in your org, you'll get an error card listing valid teams.

The bot replies with a confirmation card containing the new task_id and an `opc tail` command to stream progress.

**Security:** the configured `chat_id` is the trust boundary — anyone with write access to that chat can dispatch and revisit.
````

- [ ] **Step 3: Update CLAUDE.md**

In the "Feishu notifications" section, rename the heading to "Feishu interactive actions" and add (after the existing inbound/outbound subsections):

````markdown
### Failure notifications + REVISIT replies

Per-org opt-in via `notify_on_failure: true` in `org/config.yaml`. Hook fires from `_notify_failure_if_eligible(orch, task_id, ...)` in `run_step.py`, called right after every `_fail()` call site. Gates: enabled, `notify_on_failure=true`, `task.cancelled_at IS NULL`, no auto-revisit spawned.

`Orchestrator.notify_failed(...)` mirrors `notify_escalated`'s loop-aware fire-and-forget pattern. `EscalationNotifier.send_failure(...)` mints an `escalation_notifications` row with `kind='failure'`.

Listener routes by `(kind, decision)`:
- `(escalation, approve|reject)` → `resolve_escalation_in_process`
- `(failure, revisit)` → `revisit_from_notification`
- mismatches → `reply_rejected (verb_mismatch)`, row unconsumed

`revisit_from_notification(org, state, *, task_id, founder_note, actor)` is the in-process helper. `actor='cli'` consumes open `kind='failure'` rows with `consumed_by='cli-fallback'`; `actor='feishu-reply'` leaves consumption to the listener at step 8r.

Daemon-restart sweep (`_sweep_on_startup`) calls `notify_failed(kind='daemon_restart')` — semantic fix from v1, where it used `notify_escalated` even though the task was set to `FAILED`.

### Top-level DISPATCH

Per-org opt-in via `allow_dispatch: true` in `org/config.yaml`. Listener step 3 bifurcates on `msg.root_id`: present → reply branch (existing); absent + `allow_dispatch=true` → `_handle_top_level_dispatch`.

`parse_top_level_message(text)` returns `DispatchIntent(team, brief)` or `None`. `dispatch_via_feishu(org, state, *, intent, sender_id, event_id)` is the in-process helper extracted from `submit_task`; raises `DispatchError(reason)` where reason ∈ `{empty_brief, unknown_team, dispatch_failed}`. On success, the listener calls `send_dispatch_confirmation`; on `DispatchError`, `send_dispatch_error` with the `valid_teams` list when applicable. Confirmation/error sends are best-effort.

Trust boundary remains `chat_id`. No per-Feishu-user authorization in v1.

Spec: `docs/superpowers/specs/2026-05-12-feishu-interactive-actions-design.md`. Plan: `docs/superpowers/plans/2026-05-12-feishu-interactive-actions.md`.
````

- [ ] **Step 4: Commit**

```bash
git add README.md CLAUDE.md
git commit -m "$(cat <<'EOF'
docs: README + CLAUDE.md for failure notifications + DISPATCH

User-facing setup in README; developer/agent docs in CLAUDE.md.
Spec + plan paths linked from CLAUDE.md.
EOF
)"
```

---

## Task 20: Full regression check

- [ ] **Step 1: Run full unit suite**

Run: `uv run pytest tests/ -v`
Expected: All pass.

- [ ] **Step 2: Run full integration suite**

Run: `uv run pytest tests/ -v -m integration`
Expected: All pass.

- [ ] **Step 3: Manual smoke test (optional, recommended before merge)**

In one terminal:

```bash
scripts/daemon.sh stop || true
scripts/daemon.sh start
```

In another terminal — verify a test org has `notify_on_failure: true` + `allow_dispatch: true` in `<runtime>/orgs/<slug>/org/config.yaml`, then:

```bash
# Force a self-blocked failure — depends on org agent config
uv run opc run --org <slug> --brief "test failure card" --team engineering
# Watch for the Feishu failure card; reply REVISIT in-thread; observe new task

# Top-level dispatch test in the Feishu chat
# Send: "DISPATCH engineering\ntest top-level dispatch"
# Observe: confirmation card + new task via `uv run opc tasks --org <slug>`
```

- [ ] **Step 4: Commit any final adjustments + push**

If smoke testing revealed any fixups, commit them with `fix(...)` messages. Then:

```bash
git push -u origin worktree-enhance-feishu
```

---

## Notes for the implementing engineer

- **Test conventions:** Match existing test file naming (`test_<unit>.py` under `tests/<area>/`). Use `pytest.mark.integration` for tests that spawn a daemon. Async tests likely use `asyncio_mode = "auto"` — confirm by reading `pyproject.toml`.
- **Pydantic v2:** All models use `from __future__ import annotations`. Don't use Pydantic v1 idioms.
- **Audit logger:** Read `src/infrastructure/audit_logger.py` before adding new methods — match the existing private `_log` signature exactly.
- **`record_processed_event` / `update_processed_event_outcome`:** These exist on `Database` per the existing listener. If method names differ slightly (e.g., `record_processed_event_pending`), adapt the tests to match the real signatures.
- **`list_open_notifications_for_task`:** This exists per `resolve_escalation_in_process`. Confirm before using; if not present, add it as a minimal helper next to `consume_escalation_notification`.
- **DON'T:** Refactor unrelated code, rename existing audit actions, or change the existing escalation reply contract. The scope is purely additive.
