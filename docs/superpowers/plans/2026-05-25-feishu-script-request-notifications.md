# Feishu Notifications for Script Requests — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Push a Feishu message when an agent submits a script request, support APPROVE/REJECT verbs in the founder's threaded reply, and post a follow-up with the run's terminal result.

**Architecture:** Extends the existing Feishu escalation/failure pipeline with a fourth notification `kind="script_request"`. Reuses `escalation_notifications` table, `FeishuEventListener._dispatch_reply_action`, and the `Orchestrator.notify_*` fire-and-forget bridges. Adds two new in-process helpers (`run_script_from_notification`, `reject_script_from_notification`) extracted from the existing HTTP route handlers, so the listener and the routes share validation/transition code.

**Tech Stack:** Python 3.11+, FastAPI, SQLite (per-org), pydantic v2, `lark-oapi>=1.6,<2`, pytest. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-05-25-feishu-script-request-notifications-design.md`

---

## File Map

**Modify:**
- `src/infrastructure/database.py` — `mint_escalation_notification` kind allowlist; new `get_open_notification_for_sr` helper.
- `src/infrastructure/audit_logger.py` — 6 new `log_script_*` methods.
- `src/infrastructure/feishu/notifier.py` — 2 new body builders, 2 new send methods.
- `src/orchestrator/orchestrator.py` — `notify_script_submitted`, `notify_script_run_result` fire-and-forget bridges.
- `src/daemon/routes/scripts.py` — extract `_run_script_core` / `_reject_script_core`; add `run_script_from_notification` + `reject_script_from_notification` adapters; hook `notify_script_submitted` into submit-path; hook `notify_script_run_result` into `_run_and_persist` terminal-path.
- `src/daemon/feishu_listener.py` — new `script_request` branch in `_dispatch_reply_action`; new injected callables in `maybe_start_feishu_listener_for_org`; constructor accepts two new closures.
- `docs/setup/feishu-notifications.md` — append a "Script requests" section.
- `CLAUDE.md` — bullet under the Feishu section noting `script_request` as a notification kind.

**Create:**
- `tests/infrastructure/feishu/test_notifier_scripts.py` — body builders + send methods.
- `tests/daemon/test_feishu_listener_scripts.py` — script_request dispatch branch.
- `tests/daemon/test_routes_scripts_helpers.py` — in-process helpers.
- `tests/orchestrator/test_notify_script_dispatch.py` — orchestrator bridges.
- `tests/integration/test_feishu_script_notifications_e2e.py` — full end-to-end.

---

## Task 1: DB — accept `script_request` kind + `get_open_notification_for_sr`

**Files:**
- Modify: `src/infrastructure/database.py:2170-2196` (`mint_escalation_notification`)
- Modify: `src/infrastructure/database.py` (add `get_open_notification_for_sr` adjacent to `get_escalation_notification`)
- Test: `tests/test_database.py` (extend existing escalation_notifications tests)

- [ ] **Step 1: Write failing test for new kind**

Append to `tests/test_database.py`:

```python
def test_mint_escalation_notification_accepts_script_request_kind(tmp_path):
    from datetime import datetime, timedelta, timezone
    from src.infrastructure.database import Database

    db = Database(tmp_path / "grassland.db")
    db.mint_escalation_notification(
        feishu_message_id="om_sr_1",
        org_slug="acme",
        task_id="SR-007",
        chat_id="oc_xyz",
        expires_at=datetime.now(timezone.utc) + timedelta(hours=72),
        kind="script_request",
    )
    row = db.get_escalation_notification("om_sr_1")
    assert row is not None
    assert row["kind"] == "script_request"
    assert row["task_id"] == "SR-007"


def test_get_open_notification_for_sr_returns_most_recent(tmp_path):
    from datetime import datetime, timedelta, timezone
    from src.infrastructure.database import Database

    db = Database(tmp_path / "grassland.db")
    now = datetime.now(timezone.utc)
    db.mint_escalation_notification(
        feishu_message_id="om_old", org_slug="acme", task_id="SR-007",
        chat_id="oc_xyz", expires_at=now + timedelta(hours=72),
        kind="script_request",
    )
    db.mint_escalation_notification(
        feishu_message_id="om_new", org_slug="acme", task_id="SR-007",
        chat_id="oc_xyz", expires_at=now + timedelta(hours=72),
        kind="script_request",
    )
    found = db.get_open_notification_for_sr("SR-007", kind="script_request")
    assert found is not None
    assert found["feishu_message_id"] == "om_new"


def test_get_open_notification_for_sr_returns_none_when_missing(tmp_path):
    from src.infrastructure.database import Database
    db = Database(tmp_path / "grassland.db")
    assert db.get_open_notification_for_sr("SR-999", kind="script_request") is None


def test_get_open_notification_for_sr_finds_consumed_rows(tmp_path):
    """The terminal-result follow-up needs the parent message_id even after
    the original APPROVE consumed the row."""
    from datetime import datetime, timedelta, timezone
    from src.infrastructure.database import Database

    db = Database(tmp_path / "grassland.db")
    db.mint_escalation_notification(
        feishu_message_id="om_x", org_slug="acme", task_id="SR-008",
        chat_id="oc_xyz",
        expires_at=datetime.now(timezone.utc) + timedelta(hours=72),
        kind="script_request",
    )
    db.consume_escalation_notification("om_x", consumed_by="feishu-reply")
    found = db.get_open_notification_for_sr("SR-008", kind="script_request")
    assert found is not None  # consumed rows still returned for follow-up lookups
    assert found["feishu_message_id"] == "om_x"
```

- [ ] **Step 2: Run tests, verify they fail**

```bash
uv run pytest tests/test_database.py::test_mint_escalation_notification_accepts_script_request_kind \
              tests/test_database.py::test_get_open_notification_for_sr_returns_most_recent \
              tests/test_database.py::test_get_open_notification_for_sr_returns_none_when_missing \
              tests/test_database.py::test_get_open_notification_for_sr_finds_consumed_rows -v
```

Expected: First test fails with `ValueError: kind must be 'escalation', 'failure', or 'thread_addressed'`. Other three fail with `AttributeError: 'Database' object has no attribute 'get_open_notification_for_sr'`.

- [ ] **Step 3: Update kind allowlist**

Edit `src/infrastructure/database.py` in `mint_escalation_notification` (around line 2179):

```python
if kind not in ("escalation", "failure", "thread_addressed", "script_request"):
    raise ValueError(
        f"kind must be 'escalation', 'failure', 'thread_addressed', "
        f"or 'script_request', got {kind!r}"
    )
```

- [ ] **Step 4: Add `get_open_notification_for_sr`**

Insert just after `get_escalation_notification` (around line 2210). The `@_synchronized` decorator matches the surrounding methods:

```python
@_synchronized
def get_open_notification_for_sr(
    self, sr_id: str, *, kind: str,
) -> dict | None:
    """Look up the most-recent escalation_notifications row for an SR.

    Used by the terminal-result follow-up: when a Feishu-initiated script run
    finishes, we post a threaded reply to the original push's message_id.
    Returns consumed rows too — the APPROVE reply consumes the row, but the
    parent message_id is still needed to thread the result post.
    """
    cur = self._conn.execute(
        """SELECT feishu_message_id, org_slug, task_id, chat_id,
                  created_at, expires_at, consumed_at, consumed_by, kind
           FROM escalation_notifications
           WHERE task_id = ? AND kind = ?
           ORDER BY created_at DESC LIMIT 1""",
        (sr_id, kind),
    )
    row = cur.fetchone()
    return dict(row) if row is not None else None
```

- [ ] **Step 5: Run tests, verify they pass**

```bash
uv run pytest tests/test_database.py::test_mint_escalation_notification_accepts_script_request_kind \
              tests/test_database.py::test_get_open_notification_for_sr_returns_most_recent \
              tests/test_database.py::test_get_open_notification_for_sr_returns_none_when_missing \
              tests/test_database.py::test_get_open_notification_for_sr_finds_consumed_rows -v
```

Expected: 4 passed.

- [ ] **Step 6: Commit**

```bash
git add src/infrastructure/database.py tests/test_database.py
git commit -m "$(cat <<'EOF'
feat(db): accept script_request notification kind + add get_open_notification_for_sr

Allowlist extension lets the new SR push flow mint correlation rows alongside
the existing escalation/failure/thread_addressed shapes. The new lookup helper
returns the most-recent row regardless of consumed state so the terminal-result
follow-up can find the parent message_id even after the APPROVE consumed it.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: Audit logger — 6 new `log_script_*` methods

**Files:**
- Modify: `src/infrastructure/audit_logger.py` (append after `log_script_run_failed`)
- Test: `tests/test_audit_logger.py` (extend)

- [ ] **Step 1: Write failing tests**

Append to `tests/test_audit_logger.py`:

```python
def test_log_script_notify_sent_records_payload(audit_db_pair):
    audit, db = audit_db_pair
    audit.log_script_notify_sent(
        task_id="TASK-91", sr_id="SR-019", feishu_message_id="om_abc",
    )
    rows = db.list_audit_logs(task_id="TASK-91")
    assert len(rows) == 1
    r = rows[0]
    assert r["action"] == "script_notify_sent"
    assert r["agent"] == "daemon"
    assert r["payload"]["script_request_id"] == "SR-019"
    assert r["payload"]["feishu_message_id"] == "om_abc"


def test_log_script_notify_failed_records_error(audit_db_pair):
    audit, db = audit_db_pair
    audit.log_script_notify_failed(
        task_id="TASK-91", sr_id="SR-019", error="ConnectionRefused: feishu",
    )
    rows = db.list_audit_logs(task_id="TASK-91")
    r = rows[0]
    assert r["action"] == "script_notify_failed"
    assert r["payload"]["script_request_id"] == "SR-019"
    assert r["payload"]["error"] == "ConnectionRefused: feishu"


def test_log_script_reply_processed_carries_decision_and_rationale(audit_db_pair):
    audit, db = audit_db_pair
    audit.log_script_reply_processed(
        sr_id="SR-019", task_id="TASK-91",
        decision="approve", rationale="merge-close approved",
        feishu_event_id="evt_1",
    )
    rows = db.list_audit_logs(task_id="TASK-91")
    r = rows[0]
    assert r["action"] == "script_reply_processed"
    assert r["agent"] == "founder"
    assert r["payload"]["decision"] == "approve"
    assert r["payload"]["rationale"] == "merge-close approved"
    assert r["payload"]["script_request_id"] == "SR-019"
    assert r["payload"]["feishu_event_id"] == "evt_1"


def test_log_script_reply_rejected_records_reason(audit_db_pair):
    audit, db = audit_db_pair
    audit.log_script_reply_rejected(
        sr_id="SR-019", task_id="TASK-91",
        reason="verb_mismatch", feishu_event_id="evt_1",
        text_preview="REVISIT please",
    )
    rows = db.list_audit_logs(task_id="TASK-91")
    r = rows[0]
    assert r["action"] == "script_reply_rejected"
    assert r["agent"] == "daemon"
    assert r["payload"]["reason"] == "verb_mismatch"
    assert r["payload"]["text_preview"] == "REVISIT please"


def test_log_script_run_result_notify_sent(audit_db_pair):
    audit, db = audit_db_pair
    audit.log_script_run_result_notify_sent(
        sr_id="SR-019", task_id="TASK-91",
        parent_message_id="om_root", follow_up_message_id="om_followup",
        status="completed",
    )
    rows = db.list_audit_logs(task_id="TASK-91")
    r = rows[0]
    assert r["action"] == "script_run_result_notify_sent"
    assert r["payload"]["parent_message_id"] == "om_root"
    assert r["payload"]["follow_up_message_id"] == "om_followup"
    assert r["payload"]["status"] == "completed"


def test_log_script_run_result_notify_failed(audit_db_pair):
    audit, db = audit_db_pair
    audit.log_script_run_result_notify_failed(
        sr_id="SR-019", task_id="TASK-91",
        error="Timeout", status="failed",
    )
    rows = db.list_audit_logs(task_id="TASK-91")
    r = rows[0]
    assert r["action"] == "script_run_result_notify_failed"
    assert r["payload"]["error"] == "Timeout"
    assert r["payload"]["status"] == "failed"
```

If `audit_db_pair` is not an existing fixture in `tests/test_audit_logger.py`, add this fixture at the top of the test file (after imports):

```python
import pytest
from src.infrastructure.audit_logger import AuditLogger
from src.infrastructure.database import Database


@pytest.fixture()
def audit_db_pair(tmp_path):
    db = Database(tmp_path / "grassland.db")
    return AuditLogger(db), db
```

(Check first — there is already an analogous fixture or pattern in the existing test file. If a fixture by another name already exists, use that instead.)

- [ ] **Step 2: Run tests, verify they fail**

```bash
uv run pytest tests/test_audit_logger.py -k "log_script_notify or log_script_reply or log_script_run_result" -v
```

Expected: 6 failures with `AttributeError: 'AuditLogger' object has no attribute 'log_script_notify_sent'` (etc.).

- [ ] **Step 3: Add the 6 methods**

Append to `src/infrastructure/audit_logger.py` after `log_script_run_failed` (around line 867):

```python
    # --- Feishu push correlation for script requests ---

    def log_script_notify_sent(
        self, *, task_id: str, sr_id: str, feishu_message_id: str,
    ) -> None:
        self._db.insert_audit_log(
            task_id=task_id,
            agent="daemon",
            action="script_notify_sent",
            payload={
                "script_request_id": sr_id,
                "feishu_message_id": feishu_message_id,
            },
        )

    def log_script_notify_failed(
        self, *, task_id: str, sr_id: str, error: str,
    ) -> None:
        self._db.insert_audit_log(
            task_id=task_id,
            agent="daemon",
            action="script_notify_failed",
            payload={
                "script_request_id": sr_id,
                "error": error,
            },
        )

    def log_script_reply_processed(
        self,
        *,
        sr_id: str,
        task_id: str,
        decision: str,
        rationale: str,
        feishu_event_id: str | None = None,
    ) -> None:
        payload: dict = {
            "script_request_id": sr_id,
            "decision": decision,
            "rationale": rationale,
        }
        if feishu_event_id is not None:
            payload["feishu_event_id"] = feishu_event_id
        self._db.insert_audit_log(
            task_id=task_id,
            agent="founder",
            action="script_reply_processed",
            payload=payload,
        )

    def log_script_reply_rejected(
        self,
        *,
        sr_id: str,
        task_id: str,
        reason: str,
        feishu_event_id: str | None = None,
        text_preview: str | None = None,
    ) -> None:
        payload: dict = {
            "script_request_id": sr_id,
            "reason": reason,
        }
        if feishu_event_id is not None:
            payload["feishu_event_id"] = feishu_event_id
        if text_preview is not None:
            payload["text_preview"] = text_preview[:200]
        self._db.insert_audit_log(
            task_id=task_id,
            agent="daemon",
            action="script_reply_rejected",
            payload=payload,
        )

    def log_script_run_result_notify_sent(
        self,
        *,
        sr_id: str,
        task_id: str,
        parent_message_id: str,
        follow_up_message_id: str,
        status: str,
    ) -> None:
        self._db.insert_audit_log(
            task_id=task_id,
            agent="daemon",
            action="script_run_result_notify_sent",
            payload={
                "script_request_id": sr_id,
                "parent_message_id": parent_message_id,
                "follow_up_message_id": follow_up_message_id,
                "status": status,
            },
        )

    def log_script_run_result_notify_failed(
        self,
        *,
        sr_id: str,
        task_id: str,
        error: str,
        status: str,
    ) -> None:
        self._db.insert_audit_log(
            task_id=task_id,
            agent="daemon",
            action="script_run_result_notify_failed",
            payload={
                "script_request_id": sr_id,
                "error": error,
                "status": status,
            },
        )
```

- [ ] **Step 4: Run tests, verify they pass**

```bash
uv run pytest tests/test_audit_logger.py -k "log_script_notify or log_script_reply or log_script_run_result" -v
```

Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
git add src/infrastructure/audit_logger.py tests/test_audit_logger.py
git commit -m "$(cat <<'EOF'
feat(audit): add log_script_* methods for Feishu SR notifications

Six new audit actions covering the SR push lifecycle: notify_sent/failed
for the submit push, reply_processed/reply_rejected for the founder's
threaded reply, and run_result_notify_sent/failed for the terminal-result
follow-up. All dual-key task_id (originating task) + script_request_id
(SR-NNN) in the payload, matching log_script_submitted's convention.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: Notifier — body builders

**Files:**
- Modify: `src/infrastructure/feishu/notifier.py` (add two pure functions near the top)
- Create: `tests/infrastructure/feishu/test_notifier_scripts.py`

- [ ] **Step 1: Write failing tests for body builders**

Create `tests/infrastructure/feishu/test_notifier_scripts.py`:

```python
"""Body-builder tests for script-request Feishu push + result follow-up."""
from __future__ import annotations

from src.infrastructure.feishu.notifier import (
    _build_script_request_body,
    _build_script_result_body,
    _SCRIPT_PREVIEW_CAP,
    _RESULT_OUTPUT_PREVIEW_CAP,
)


def test_request_body_renders_all_fields():
    title, lines = _build_script_request_body(
        slug="acme",
        sr_id="SR-019",
        agent="engineering_head",
        task_id="TASK-91",
        title="Close PR #247",
        rationale="Need founder to close because allow_rules block gh pr close",
        script_text="set -euo pipefail\ngh pr close 247",
        interpreter="bash",
        cwd_hint="repos/web-app",
    )
    body = "\n".join(lines)
    assert "SR-019" in title
    assert "acme" in title
    assert "submitted" in title
    assert "Agent:" in body and "engineering_head" in body
    assert "Task:" in body and "TASK-91" in body
    assert "Interpreter:" in body and "bash" in body
    assert "Cwd hint:" in body and "repos/web-app" in body
    assert "Close PR #247" in body
    assert "Need founder to close" in body
    assert "gh pr close 247" in body
    # Reply grammar
    assert "APPROVE" in body
    assert "REJECT" in body
    # CLI fallback hints
    assert "grassland scripts show SR-019" in body
    assert "grassland scripts run SR-019" in body
    assert "grassland scripts reject SR-019" in body


def test_request_body_missing_cwd_hint_renders_workspace_root():
    _, lines = _build_script_request_body(
        slug="acme", sr_id="SR-019", agent="a", task_id="T",
        title="t", rationale="r", script_text="s",
        interpreter="bash", cwd_hint=None,
    )
    body = "\n".join(lines)
    assert "(workspace root)" in body


def test_request_body_truncates_long_script():
    long_script = "x" * (_SCRIPT_PREVIEW_CAP + 500)
    _, lines = _build_script_request_body(
        slug="acme", sr_id="SR-019", agent="a", task_id="T",
        title="t", rationale="r", script_text=long_script,
        interpreter="bash", cwd_hint=None,
    )
    body = "\n".join(lines)
    assert "[truncated — see grassland scripts show SR-019 for full script]" in body
    # The slice itself must not exceed the cap (footer is appended after).
    # Easiest check: the truncation marker only appears once.
    assert body.count("[truncated") == 1


def test_request_body_keeps_short_script_intact():
    short = "echo hi"
    _, lines = _build_script_request_body(
        slug="acme", sr_id="SR-019", agent="a", task_id="T",
        title="t", rationale="r", script_text=short,
        interpreter="bash", cwd_hint=None,
    )
    body = "\n".join(lines)
    assert "echo hi" in body
    assert "[truncated" not in body


def test_result_body_completed_branch():
    title, lines = _build_script_result_body(
        slug="acme", sr_id="SR-019", status="completed",
        exit_code=0, duration_ms=1400,
        stdout_head="✓ Closed pull request #247\n",
        stderr_head=None, reason=None,
    )
    body = "\n".join(lines)
    assert "SR-019" in title
    assert "completed" in title
    assert "exit 0" in title
    assert "Duration: 1.4s" in body
    assert "✓ Closed pull request #247" in body
    assert "(empty)" in body  # stderr is empty


def test_result_body_failed_branch_with_reason():
    title, lines = _build_script_result_body(
        slug="acme", sr_id="SR-019", status="failed",
        exit_code=None, duration_ms=300_000,
        stdout_head=None,
        stderr_head="Error: connection timed out",
        reason="timeout",
    )
    body = "\n".join(lines)
    assert "failed" in title
    assert "timeout" in title
    assert "Duration: 300.0s" in body
    assert "Error: connection timed out" in body


def test_result_body_truncates_long_output():
    long_out = "line\n" * 200  # ~1000 chars
    _, lines = _build_script_result_body(
        slug="acme", sr_id="SR-019", status="completed",
        exit_code=0, duration_ms=100,
        stdout_head=long_out, stderr_head=None, reason=None,
    )
    body = "\n".join(lines)
    assert f"[truncated — full output in grassland scripts output SR-019]" in body


def test_result_body_completed_unknown_exit_code():
    title, _ = _build_script_result_body(
        slug="acme", sr_id="SR-019", status="completed",
        exit_code=None, duration_ms=100,
        stdout_head=None, stderr_head=None, reason=None,
    )
    # Defensive — completed normally implies an exit_code, but if it's None,
    # render "?" rather than crash.
    assert "exit ?" in title
```

- [ ] **Step 2: Run tests, verify they fail**

```bash
uv run pytest tests/infrastructure/feishu/test_notifier_scripts.py -v
```

Expected: ImportError on the two builders and the two constants.

- [ ] **Step 3: Add body builders to notifier.py**

Edit `src/infrastructure/feishu/notifier.py`. After the existing `_HINT_PREVIEW_CAP = 200` line (around line 30), add:

```python
_SCRIPT_PREVIEW_CAP = 1500
_RESULT_OUTPUT_PREVIEW_CAP = 500
```

After `_build_failure_body` (around line 99), append:

```python
def _build_script_request_body(
    *,
    slug: str,
    sr_id: str,
    agent: str,
    task_id: str,
    title: str,
    rationale: str,
    script_text: str,
    interpreter: str,
    cwd_hint: str | None,
) -> tuple[str, list[str]]:
    """Body for the script-request submit push (msg_type=post)."""
    header = f"[Grassland {slug}] {sr_id} submitted — review needed"
    script_preview = script_text
    if len(script_preview) > _SCRIPT_PREVIEW_CAP:
        script_preview = (
            script_preview[:_SCRIPT_PREVIEW_CAP]
            + f"\n[truncated — see grassland scripts show {sr_id} for full script]"
        )
    lines = [
        f"Agent:        {agent}",
        f"Task:         {task_id}",
        f"Interpreter:  {interpreter}",
        f"Cwd hint:     {cwd_hint or '(workspace root)'}",
        f"Title:        {title}",
        "",
        "Rationale:",
        rationale,
        "",
        "Script:",
        script_preview,
        "",
        "To resolve, reply in this thread with one of:",
        "",
        "  APPROVE",
        "  <optional note>",
        "",
        "  —or—",
        "",
        "  REJECT",
        "  <reason>",
        "",
        "You can also resolve via CLI:",
        f"  grassland scripts show {sr_id}",
        f"  grassland scripts run {sr_id}",
        f"  grassland scripts reject {sr_id} --reason \"...\"",
    ]
    return header, lines


def _build_script_result_body(
    *,
    slug: str,
    sr_id: str,
    status: str,
    exit_code: int | None,
    duration_ms: int,
    stdout_head: str | None,
    stderr_head: str | None,
    reason: str | None,
) -> tuple[str, list[str]]:
    """Body for the terminal-result threaded reply."""
    if status == "completed":
        descriptor = f"completed (exit {exit_code if exit_code is not None else '?'})"
    else:
        descriptor = f"failed ({reason or 'unknown'})"
    header = f"[Grassland {slug}] {sr_id} {descriptor}"

    def _preview(s: str | None) -> list[str]:
        if not s:
            return ["(empty)"]
        s = s.rstrip("\n")
        if len(s) <= _RESULT_OUTPUT_PREVIEW_CAP:
            return s.split("\n")
        return (
            s[:_RESULT_OUTPUT_PREVIEW_CAP].split("\n")
            + [f"[truncated — full output in grassland scripts output {sr_id}]"]
        )

    duration_s = duration_ms / 1000.0
    lines = [
        f"Duration: {duration_s:.1f}s",
        "",
        "stdout:",
        *_preview(stdout_head),
        "",
        "stderr:",
        *_preview(stderr_head),
    ]
    return header, lines
```

- [ ] **Step 4: Run tests, verify they pass**

```bash
uv run pytest tests/infrastructure/feishu/test_notifier_scripts.py -v
```

Expected: 7 passed.

- [ ] **Step 5: Commit**

```bash
git add src/infrastructure/feishu/notifier.py tests/infrastructure/feishu/test_notifier_scripts.py
git commit -m "$(cat <<'EOF'
feat(feishu): add SR push + result body builders

Two pure functions assemble the msg_type=post payloads for the new
script_request notification kind. _build_script_request_body renders
the submit push with full agent/task/script context plus the APPROVE/
REJECT reply grammar. _build_script_result_body renders the terminal-
result threaded reply with capped stdout/stderr previews.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: Notifier — `send_script_request` + `send_script_run_result`

**Files:**
- Modify: `src/infrastructure/feishu/notifier.py` (append two methods to `EscalationNotifier`)
- Test: `tests/infrastructure/feishu/test_notifier_scripts.py`

- [ ] **Step 1: Write failing tests for the notifier methods**

Append to `tests/infrastructure/feishu/test_notifier_scripts.py`:

```python
import asyncio
from pathlib import Path
import pytest

from src.infrastructure.audit_logger import AuditLogger
from src.infrastructure.database import Database
from src.infrastructure.feishu.notifier import EscalationNotifier
from src.orchestrator.org_config import FeishuNotificationsConfig


class _FakeClient:
    def __init__(self) -> None:
        self.posts: list[dict] = []
        self.thread_replies: list[dict] = []

    def send_post_message(self, *, chat_id, title, body_lines):
        self.posts.append({"chat_id": chat_id, "title": title, "body": body_lines})
        return f"om_post_{len(self.posts)}"

    def send_thread_reply(self, *, parent_message_id, title, body_lines):
        self.thread_replies.append({
            "parent": parent_message_id, "title": title, "body": body_lines,
        })
        return f"om_thread_{len(self.thread_replies)}"


@pytest.fixture()
def notifier_setup(tmp_path: Path):
    db = Database(tmp_path / "grassland.db")
    audit = AuditLogger(db)
    client = _FakeClient()
    cfg = FeishuNotificationsConfig(
        provider="feishu", region="feishu", chat_id="oc_xyz",
        app_id="cli", app_secret="x", reply_ttl_hours=72,
    )
    notifier = EscalationNotifier(
        slug="acme", db=db, audit=audit, client=client, config=cfg,
    )
    return notifier, db, client


def test_send_script_request_happy_path(notifier_setup):
    notifier, db, client = notifier_setup
    asyncio.run(notifier.send_script_request(
        sr_id="SR-019", agent="engineering_head",
        task_id="TASK-91", title="Close PR #247",
        rationale="ok", script_text="echo hi",
        interpreter="bash", cwd_hint="repos/web-app",
    ))
    assert len(client.posts) == 1
    sent = client.posts[0]
    assert "SR-019" in sent["title"]
    assert sent["chat_id"] == "oc_xyz"

    row = db.get_escalation_notification("om_post_1")
    assert row is not None
    assert row["kind"] == "script_request"
    assert row["task_id"] == "SR-019"  # SR-NNN goes in task_id column

    audit_rows = db.list_audit_logs(task_id="TASK-91")
    assert any(r["action"] == "script_notify_sent" for r in audit_rows)


def test_send_script_request_swallows_send_failure(notifier_setup):
    notifier, db, client = notifier_setup

    def boom(**kwargs):
        raise RuntimeError("feishu down")

    client.send_post_message = boom
    # Must not raise
    asyncio.run(notifier.send_script_request(
        sr_id="SR-019", agent="a", task_id="TASK-91",
        title="t", rationale="r", script_text="s",
        interpreter="bash", cwd_hint=None,
    ))
    # No notification row minted on send failure
    assert db.get_open_notification_for_sr("SR-019", kind="script_request") is None
    audit_rows = db.list_audit_logs(task_id="TASK-91")
    assert any(r["action"] == "script_notify_failed" for r in audit_rows)


def test_send_script_run_result_happy_path(notifier_setup):
    notifier, db, client = notifier_setup
    asyncio.run(notifier.send_script_run_result(
        sr_id="SR-019", task_id="TASK-91",
        parent_message_id="om_root_xyz",
        status="completed", exit_code=0, duration_ms=1400,
        stdout_head="ok", stderr_head=None, reason=None,
    ))
    assert len(client.thread_replies) == 1
    reply = client.thread_replies[0]
    assert reply["parent"] == "om_root_xyz"
    assert "SR-019" in reply["title"]
    assert "completed" in reply["title"]
    audit_rows = db.list_audit_logs(task_id="TASK-91")
    assert any(r["action"] == "script_run_result_notify_sent" for r in audit_rows)


def test_send_script_run_result_swallows_send_failure(notifier_setup):
    notifier, db, client = notifier_setup

    def boom(**kwargs):
        raise RuntimeError("feishu down")

    client.send_thread_reply = boom
    asyncio.run(notifier.send_script_run_result(
        sr_id="SR-019", task_id="TASK-91",
        parent_message_id="om_root_xyz",
        status="failed", exit_code=None, duration_ms=0,
        stdout_head=None, stderr_head="x", reason="timeout",
    ))
    audit_rows = db.list_audit_logs(task_id="TASK-91")
    assert any(r["action"] == "script_run_result_notify_failed" for r in audit_rows)
```

- [ ] **Step 2: Run tests, verify they fail**

```bash
uv run pytest tests/infrastructure/feishu/test_notifier_scripts.py -k "send_script" -v
```

Expected: 4 failures with `AttributeError: 'EscalationNotifier' object has no attribute 'send_script_request'` (etc.).

- [ ] **Step 3: Add the two methods to `EscalationNotifier`**

Append to `src/infrastructure/feishu/notifier.py` inside the `EscalationNotifier` class (after `send_thread_addressed`, around line 358):

```python
    async def send_script_request(
        self,
        *,
        sr_id: str,
        agent: str,
        task_id: str,
        title: str,
        rationale: str,
        script_text: str,
        interpreter: str,
        cwd_hint: str | None,
    ) -> None:
        """Push a Feishu post to the founder when an agent submits SR-NNN.

        Mint-after-send: the correlation row is keyed by the returned
        feishu_message_id, so a send failure leaves no orphan row. All
        exceptions are swallowed and audited so submit_script's caller
        (the agent) never sees a 5xx because Feishu is down.
        """
        try:
            now = datetime.now(timezone.utc)
            header, body_lines = _build_script_request_body(
                slug=self._slug, sr_id=sr_id, agent=agent, task_id=task_id,
                title=title, rationale=rationale, script_text=script_text,
                interpreter=interpreter, cwd_hint=cwd_hint,
            )
            message_id = self._client.send_post_message(
                chat_id=self._config.chat_id,
                title=header,
                body_lines=body_lines,
            )
            expires = now + timedelta(hours=self._config.reply_ttl_hours)
            self._db.mint_escalation_notification(
                feishu_message_id=message_id,
                org_slug=self._slug,
                task_id=sr_id,            # SR-NNN in task_id column (matches thread_addressed)
                chat_id=self._config.chat_id,
                expires_at=expires,
                kind="script_request",
            )
            self._audit.log_script_notify_sent(
                task_id=task_id, sr_id=sr_id, feishu_message_id=message_id,
            )
        except Exception as exc:
            logger.exception("send_script_request failed for SR %s", sr_id)
            try:
                self._audit.log_script_notify_failed(
                    task_id=task_id, sr_id=sr_id,
                    error=f"{type(exc).__name__}: {exc}",
                )
            except Exception:
                logger.exception("audit log_script_notify_failed also failed")

    async def send_script_run_result(
        self,
        *,
        sr_id: str,
        task_id: str,
        parent_message_id: str,
        status: str,
        exit_code: int | None,
        duration_ms: int,
        stdout_head: str | None,
        stderr_head: str | None,
        reason: str | None,
    ) -> None:
        """Post a threaded reply with the run's terminal result.

        Best-effort; no DB row minted (this is a leaf — no reply expected).
        Failures are swallowed and audited.
        """
        try:
            header, body_lines = _build_script_result_body(
                slug=self._slug, sr_id=sr_id, status=status,
                exit_code=exit_code, duration_ms=duration_ms,
                stdout_head=stdout_head, stderr_head=stderr_head,
                reason=reason,
            )
            follow_up_id = self._client.send_thread_reply(
                parent_message_id=parent_message_id,
                title=header,
                body_lines=body_lines,
            )
            self._audit.log_script_run_result_notify_sent(
                sr_id=sr_id, task_id=task_id,
                parent_message_id=parent_message_id,
                follow_up_message_id=follow_up_id,
                status=status,
            )
        except Exception as exc:
            logger.exception("send_script_run_result failed for SR %s", sr_id)
            try:
                self._audit.log_script_run_result_notify_failed(
                    sr_id=sr_id, task_id=task_id,
                    error=f"{type(exc).__name__}: {exc}",
                    status=status,
                )
            except Exception:
                logger.exception("audit log_script_run_result_notify_failed also failed")
```

- [ ] **Step 4: Run tests, verify they pass**

```bash
uv run pytest tests/infrastructure/feishu/test_notifier_scripts.py -v
```

Expected: 11 passed (7 from Task 3 + 4 new).

- [ ] **Step 5: Commit**

```bash
git add src/infrastructure/feishu/notifier.py tests/infrastructure/feishu/test_notifier_scripts.py
git commit -m "$(cat <<'EOF'
feat(feishu): notifier methods for SR push + result follow-up

send_script_request posts the submit notification and mints an
escalation_notifications row with kind=script_request (mint-after-send,
matching notify_escalated). send_script_run_result posts a threaded
reply with the terminal status; it's a leaf, no row minted. Both swallow
exceptions and audit failures so the orchestration path never sees an
exception from Feishu downtime.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: Extract in-process helpers — `run_script_from_notification`, `reject_script_from_notification`

**Files:**
- Modify: `src/daemon/routes/scripts.py`
- Create: `tests/daemon/test_routes_scripts_helpers.py`

**Rationale:** The listener can't call FastAPI route handlers directly. We extract the validation+transition+spawn logic into module-level async functions taking `(org, sr_id, ...)` arguments. The existing HTTP route handlers become thin adapters that pass the request body into the helper. This mirrors `resolve_escalation_in_process` and `revisit_from_notification`.

- [ ] **Step 1: Write failing tests for the new helpers**

Create `tests/daemon/test_routes_scripts_helpers.py`:

```python
"""In-process helpers extracted from scripts route handlers (Task 5).

These functions are called by the Feishu listener when the founder replies
APPROVE/REJECT — they wrap the same validation + transition logic the HTTP
routes use.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from fastapi import HTTPException

from src.daemon.routes.scripts import (
    reject_script_from_notification,
    run_script_from_notification,
)
from src.models import (
    ScriptInterpreter,
    ScriptRequestRecord,
    ScriptRequestStatus,
)


def _insert_pending_sr(org, sr_id: str = "SR-001") -> None:
    org.db.insert_script_request(ScriptRequestRecord(
        id=sr_id, task_id="TASK-1", agent_name="dev",
        title="t", rationale="r", script_text="echo hi",
        interpreter=ScriptInterpreter.BASH,
        cwd_hint=None,
        status=ScriptRequestStatus.PENDING,
        created_at="2026-05-25T00:00:00Z",
    ))


@pytest.mark.asyncio
async def test_reject_helper_transitions_to_rejected(scripts_test_org):
    org = scripts_test_org
    _insert_pending_sr(org)
    result = await reject_script_from_notification(
        org, sr_id="SR-001", reason="not a fit",
    )
    assert result.status == ScriptRequestStatus.REJECTED
    assert result.reject_reason == "not a fit"


@pytest.mark.asyncio
async def test_reject_helper_404_when_missing(scripts_test_org):
    org = scripts_test_org
    with pytest.raises(HTTPException) as exc:
        await reject_script_from_notification(
            org, sr_id="SR-999", reason="x",
        )
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_reject_helper_409_when_not_pending(scripts_test_org):
    org = scripts_test_org
    _insert_pending_sr(org)
    # Transition out of pending first.
    org.db.transition_script_to_rejected(
        "SR-001", reviewer="founder", reason="prior",
        reviewed_at="2026-05-25T00:00:00Z",
    )
    with pytest.raises(HTTPException) as exc:
        await reject_script_from_notification(
            org, sr_id="SR-001", reason="late",
        )
    assert exc.value.status_code == 409
    assert exc.value.detail["code"] == "not_pending"


@pytest.mark.asyncio
async def test_run_helper_transitions_to_running(scripts_test_org, monkeypatch):
    org = scripts_test_org
    _insert_pending_sr(org)
    # Workspace dir must exist for cwd_resolved to pass the exists() check.
    ws = org.root / "workspaces" / "dev"
    ws.mkdir(parents=True, exist_ok=True)

    # Stub the spawner so the test doesn't actually fork a subprocess.
    spawned: list[dict] = []

    async def _fake_spawn(**kw):
        spawned.append(kw)
        from src.daemon.scripts_runner import RunResult
        return RunResult(
            status="completed", exit_code=0, duration_ms=10,
            stdout_head="ok", stderr_head=None,
            stdout_bytes=2, stderr_bytes=0,
            truncated_stdout=False, truncated_stderr=False,
            reason=None,
        )

    monkeypatch.setattr("src.daemon.routes.scripts._spawn_script", _fake_spawn)

    result = await run_script_from_notification(
        org, sr_id="SR-001", actor="feishu-reply", founder_note="ok",
    )
    assert result["status"] == "running"
    assert result["id"] == "SR-001"

    # After spawn returns (the helper awaits the runner task), the SR row is terminal.
    # We can wait briefly for the background task to finalize.
    for _ in range(20):
        rec = org.db.get_script_request("SR-001")
        if rec.status != ScriptRequestStatus.RUNNING:
            break
        await asyncio.sleep(0.05)
    rec = org.db.get_script_request("SR-001")
    assert rec.status in (
        ScriptRequestStatus.COMPLETED, ScriptRequestStatus.FAILED,
    )


@pytest.mark.asyncio
async def test_run_helper_404_when_missing(scripts_test_org):
    org = scripts_test_org
    with pytest.raises(HTTPException) as exc:
        await run_script_from_notification(
            org, sr_id="SR-999", actor="feishu-reply", founder_note="ok",
        )
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_run_helper_409_when_not_pending(scripts_test_org):
    org = scripts_test_org
    _insert_pending_sr(org)
    org.db.transition_script_to_rejected(
        "SR-001", reviewer="founder", reason="x",
        reviewed_at="2026-05-25T00:00:00Z",
    )
    with pytest.raises(HTTPException) as exc:
        await run_script_from_notification(
            org, sr_id="SR-001", actor="feishu-reply", founder_note="ok",
        )
    assert exc.value.status_code == 409
```

Add an `scripts_test_org` fixture at the top of `tests/daemon/test_routes_scripts_helpers.py`. Inspect `tests/daemon/test_routes_scripts.py` first — if it already builds an in-memory `OrgState`-like object the same way, copy the fixture verbatim into this file (or import it via `tests/daemon/conftest.py`):

```python
import pytest
from src.daemon.event_bus import EventBus
from src.daemon.org_state import OrgState
from src.daemon.sessions import SessionTracker
from src.infrastructure.database import Database
from src.orchestrator.orchestrator import Orchestrator
from src.orchestrator.paths import OrgPaths


@pytest.fixture()
def scripts_test_org(tmp_path):
    """Minimal OrgState for in-process helper tests. No real daemon, no
    real orchestrator wiring — just enough for the helpers to read/write
    org.db and org.root."""
    root = tmp_path / "orgs" / "acme"
    (root / "scripts").mkdir(parents=True)
    (root / "workspaces" / "dev").mkdir(parents=True)
    db = Database(root / "grassland.db")
    paths = OrgPaths(runtime_root=tmp_path, slug="acme")
    # The helpers only touch org.db, org.root, org.event_bus, org.db_lock,
    # org.sessions, and (in run path) the scripts_runner module-level state.
    # OrgState's constructor wires the rest.
    from src.daemon.org_state import OrgState
    import asyncio
    org = OrgState(
        slug="acme",
        root=root,
        db=db,
        db_lock=asyncio.Lock(),
        sessions=SessionTracker(),
        event_bus=EventBus(),
        orchestrator=None,  # type: ignore  — helpers shouldn't dereference it directly
        notifier=None,
    )
    return org
```

**Note for the engineer:** `OrgState`'s real constructor signature may differ. Run `grep -n "@dataclass\|class OrgState\|def __init__" src/daemon/org_state.py` and adapt the fixture accordingly. The goal is the minimum viable `org` with `db`, `root`, `event_bus`, `sessions`, `db_lock`. If `OrgState` is a dataclass with required fields you can't easily synthesize, build a `SimpleNamespace` instead.

- [ ] **Step 2: Run tests, verify they fail**

```bash
uv run pytest tests/daemon/test_routes_scripts_helpers.py -v
```

Expected: ImportError on `reject_script_from_notification` / `run_script_from_notification`.

- [ ] **Step 3: Extract `reject_script_from_notification` from `reject_script`**

Edit `src/daemon/routes/scripts.py`. After the existing `RejectBody` and `reject_script` route handler block (around line 167–203), restructure as follows. Keep the existing route handler thin, delegate to a new helper:

Replace the existing `reject_script` function with this pair:

```python
async def reject_script_from_notification(
    org, *, sr_id: str, reason: str,
) -> ScriptRequestRecord:
    """In-process reject path used by the Feishu listener.

    Same validation + transition + audit as POST /scripts/{sr_id}/reject,
    minus the request-body parsing. Raises HTTPException on failure with
    the same status/detail shape the route returns.
    """
    record = org.db.get_script_request(sr_id)
    if record is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "unknown_script_request", "sr_id": sr_id},
        )

    reason_stripped = reason.strip()
    if not reason_stripped:
        raise HTTPException(status_code=422, detail={"code": "empty_reason"})
    if len(reason_stripped) > _MAX_REJECT_REASON_LEN:
        raise HTTPException(
            status_code=422,
            detail={"code": "reason_too_long", "max": _MAX_REJECT_REASON_LEN},
        )

    if record.status != ScriptRequestStatus.PENDING:
        raise HTTPException(
            status_code=409,
            detail={"code": "not_pending", "status": record.status.value},
        )

    reviewed_at = _now_iso()
    try:
        org.db.transition_script_to_rejected(
            sr_id, reviewer="founder", reason=reason_stripped,
            reviewed_at=reviewed_at,
        )
    except ValueError:
        raise HTTPException(status_code=409, detail={"code": "not_pending"})

    AuditLogger(org.db).log_script_rejected(
        task_id=record.task_id, sr_id=sr_id,
        reviewer="founder", reason=reason_stripped,
    )
    return org.db.get_script_request(sr_id)


@router.post("/scripts/{sr_id}/reject")
async def reject_script(slug: str, sr_id: str, body: RejectBody, org: OrgDep) -> dict:
    updated = await reject_script_from_notification(
        org, sr_id=sr_id, reason=body.reason,
    )
    return updated.model_dump()
```

- [ ] **Step 4: Extract `run_script_from_notification` from `run_script_route`**

Still in `src/daemon/routes/scripts.py`. Look at the existing `run_script_route` (around line 257–419). The function does: lookup → status check → timeout default → cwd resolution → cwd existence check → interpreter check → file allocation → DB transition → audit → spawn `_run_and_persist` background task → return 202 dict.

Extract everything except the `RunBody` parsing into a helper. The helper accepts `cwd_override` and `timeout_override` as keyword arguments (both default `None`):

Replace the existing `run_script_route` function with this pair. Add `run_script_from_notification` first:

```python
async def run_script_from_notification(
    org, *, sr_id: str, actor: str, founder_note: str,
) -> dict:
    """In-process run path used by the Feishu listener.

    Uses the SR's stored defaults — no cwd_override, no timeout_override.
    Returns the same 202-style dict the HTTP route returns. Raises
    HTTPException on failure with the same status/detail shape.

    `actor` ("feishu-reply" or "cli") and `founder_note` (the rationale from
    the APPROVE reply) are unused by the run itself today but kept on the
    signature so future polish (e.g., recording the approval rationale in
    the audit row) can land without changing the listener.
    """
    return await _run_script_core(
        org, sr_id=sr_id,
        cwd_override=None, timeout_override=None,
    )


async def _run_script_core(
    org, *, sr_id: str,
    cwd_override: str | None, timeout_override: int | None,
) -> dict:
    """Shared core for HTTP and in-process run paths."""
    record = org.db.get_script_request(sr_id)
    if record is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "unknown_script_request", "sr_id": sr_id},
        )

    if record.status != ScriptRequestStatus.PENDING:
        raise HTTPException(
            status_code=409,
            detail={"code": "not_pending", "status": record.status.value},
        )

    timeout = (
        timeout_override if timeout_override is not None
        else record.timeout_seconds
    )
    if timeout <= 0 or timeout > 86400:
        raise HTTPException(status_code=422, detail={"code": "invalid_timeout"})

    workspace_root = org.root / "workspaces" / record.agent_name
    try:
        cwd_resolved = _resolve_cwd(
            cwd_override=cwd_override,
            cwd_hint=record.cwd_hint,
            workspace_root=workspace_root,
        )
    except (ValueError, OSError):
        raise HTTPException(status_code=422, detail={"code": "invalid_cwd_override"})

    if not cwd_resolved.exists() or not cwd_resolved.is_dir():
        raise HTTPException(
            status_code=409,
            detail={"code": "cwd_missing", "resolved": str(cwd_resolved)},
        )

    if _interpreter_binary(record.interpreter.value) is None:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "interpreter_unavailable",
                "interpreter": record.interpreter.value,
            },
        )

    scripts_dir = org.root / "scripts"
    scripts_dir.mkdir(parents=True, exist_ok=True)
    stdout_path = scripts_dir / f"{sr_id}.out"
    stderr_path = scripts_dir / f"{sr_id}.err"
    stdout_path.write_bytes(b"")
    stderr_path.write_bytes(b"")

    now = _now_iso()
    try:
        org.db.transition_script_to_running(
            sr_id,
            reviewer="founder",
            reviewed_at=now,
            started_at=now,
            cwd_resolved=str(cwd_resolved),
            timeout_seconds=timeout,
            stdout_path=str(stdout_path),
            stderr_path=str(stderr_path),
        )
    except ValueError:
        raise HTTPException(status_code=409, detail={"code": "not_pending"})

    audit = AuditLogger(org.db)
    audit.log_script_run_started(
        task_id=record.task_id, sr_id=sr_id, reviewer="founder",
        cwd_resolved=str(cwd_resolved),
        timeout_seconds=timeout,
        interpreter=record.interpreter.value,
    )

    async def _run_and_persist() -> None:
        loop = asyncio.get_running_loop()

        def _sync_publish(evt: dict) -> None:
            asyncio.run_coroutine_threadsafe(
                org.event_bus.publish(script_topic(sr_id), evt), loop
            )

        try:
            result = await _spawn_script(
                sr_id=sr_id,
                script_text=record.script_text,
                interpreter=record.interpreter.value,
                cwd=str(cwd_resolved),
                stdout_path=str(stdout_path),
                stderr_path=str(stderr_path),
                timeout_seconds=timeout,
                publish=_sync_publish,
            )
        except FileNotFoundError:
            finished = _now_iso()
            try:
                org.db.transition_script_to_terminal(
                    sr_id, status=ScriptRequestStatus.FAILED,
                    exit_code=None, finished_at=finished, duration_ms=0,
                    stdout_head=None, stderr_head=None,
                )
            except ValueError:
                pass
            audit.log_script_run_failed(
                task_id=record.task_id, sr_id=sr_id, reason="spawn_failed",
            )
            return
        except Exception as exc:
            finished = _now_iso()
            try:
                org.db.transition_script_to_terminal(
                    sr_id, status=ScriptRequestStatus.FAILED,
                    exit_code=None, finished_at=finished, duration_ms=0,
                    stdout_head=None, stderr_head=str(exc),
                )
            except ValueError:
                pass
            audit.log_script_run_failed(
                task_id=record.task_id, sr_id=sr_id, reason="internal_error",
            )
            return

        finished = _now_iso()
        try:
            org.db.transition_script_to_terminal(
                sr_id,
                status=ScriptRequestStatus(result.status),
                exit_code=result.exit_code,
                finished_at=finished,
                duration_ms=result.duration_ms,
                stdout_head=result.stdout_head,
                stderr_head=result.stderr_head,
            )
        except ValueError:
            return

        if result.status == "completed":
            audit.log_script_run_completed(
                task_id=record.task_id, sr_id=sr_id,
                exit_code=result.exit_code or 0,
                duration_ms=result.duration_ms,
                stdout_bytes=result.stdout_bytes,
                stderr_bytes=result.stderr_bytes,
                truncated_stdout=result.truncated_stdout,
                truncated_stderr=result.truncated_stderr,
            )
        else:
            audit.log_script_run_failed(
                task_id=record.task_id, sr_id=sr_id,
                exit_code=result.exit_code,
                duration_ms=result.duration_ms,
                reason=result.reason or "unknown",
            )

    from src.daemon.scripts_runner import register_runner_task
    register_runner_task(sr_id, asyncio.create_task(_run_and_persist()))

    return {
        "id": sr_id,
        "status": "running",
        "started_at": now,
        "cwd_resolved": str(cwd_resolved),
        "timeout_seconds": timeout,
        "events_url": f"/api/v1/orgs/{org.slug}/scripts/{sr_id}/events",
    }


@router.post("/scripts/{sr_id}/run", status_code=202)
async def run_script_route(
    slug: str, sr_id: str, body: RunBody, org: OrgDep,
) -> dict:
    return await _run_script_core(
        org,
        sr_id=sr_id,
        cwd_override=body.cwd_override,
        timeout_override=body.timeout_seconds,
    )
```

**Important — the existing route used `slug` for the `events_url` field; the helper uses `org.slug`.** Verify `OrgState` exposes `slug` (it does — `src/daemon/org_state.py:46` declares `slug: str`). If not, fall back to passing `slug` as a kwarg from both call-sites.

- [ ] **Step 5: Run helper tests + existing route tests, verify they pass**

```bash
uv run pytest tests/daemon/test_routes_scripts_helpers.py tests/daemon/test_routes_scripts.py -v
```

Expected: All new helper tests pass AND the existing route tests still pass (refactor must be behavior-preserving).

- [ ] **Step 6: Commit**

```bash
git add src/daemon/routes/scripts.py tests/daemon/test_routes_scripts_helpers.py
git commit -m "$(cat <<'EOF'
refactor(scripts): extract run/reject helpers for in-process callers

The Feishu listener will call reject_script_from_notification and
run_script_from_notification when the founder replies APPROVE/REJECT in
a script-request thread. Helpers share the same validation + transition +
audit code paths the HTTP route handlers use; the routes become thin
adapters that pass request-body fields into the shared core.

The new in-process run path uses stored SR defaults (cwd_hint,
timeout_seconds) — no overrides exposed via Feishu in v1.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 6: Orchestrator — `notify_script_submitted` + `notify_script_run_result`

**Files:**
- Modify: `src/orchestrator/orchestrator.py`
- Create: `tests/orchestrator/test_notify_script_dispatch.py`

- [ ] **Step 1: Write failing tests**

Create `tests/orchestrator/test_notify_script_dispatch.py`:

```python
"""Fire-and-forget bridges for SR notifications (mirrors test_notify_failed_dispatch)."""
from __future__ import annotations

import asyncio
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest


def _make_orchestrator(tmp_path: Path):
    """Build a minimal Orchestrator without invoking the full daemon stack.

    The notify_* methods only depend on self._notifier, so we can attach a
    bare mock and exercise the dispatch logic in isolation.
    """
    from src.orchestrator.orchestrator import Orchestrator
    from src.orchestrator.paths import OrgPaths
    from src.config import Settings

    paths = OrgPaths(runtime_root=tmp_path, slug="acme")
    (paths.org_root / "org").mkdir(parents=True, exist_ok=True)
    (paths.org_root / "workspaces").mkdir(parents=True, exist_ok=True)
    (paths.org_root / "org" / "teams.yaml").write_text("teams: {}\n")
    (paths.org_root / "org" / "charter.md").write_text("# Charter\n")
    (paths.org_root / "org" / "escalation-rules.md").write_text("\n")
    settings = Settings()

    from src.infrastructure.database import Database
    db = Database(paths.org_root / "grassland.db")
    orch = Orchestrator(paths=paths, db=db, settings=settings)
    return orch


def test_notify_script_submitted_noop_when_notifier_unset(tmp_path):
    orch = _make_orchestrator(tmp_path)
    # Should not raise even though no notifier is attached.
    orch.notify_script_submitted(
        sr_id="SR-1", agent="a", task_id="TASK-1",
        title="t", rationale="r", script_text="s",
        interpreter="bash", cwd_hint=None,
    )


def test_notify_script_submitted_runs_in_thread_when_no_loop(tmp_path):
    orch = _make_orchestrator(tmp_path)
    captured: list[dict] = []
    done = threading.Event()

    class _FakeNotifier:
        async def send_script_request(self, **kw):
            captured.append(kw)
            done.set()
        # The bridge spec is duck-typed; only send_script_request matters here.

    orch.attach_notifier(_FakeNotifier())
    orch.notify_script_submitted(
        sr_id="SR-1", agent="a", task_id="TASK-1",
        title="t", rationale="r", script_text="s",
        interpreter="bash", cwd_hint="x",
    )
    # The bridge spawns a daemon thread that owns asyncio.run; wait briefly.
    assert done.wait(timeout=2.0), "send_script_request was not invoked"
    assert captured == [{
        "sr_id": "SR-1", "agent": "a", "task_id": "TASK-1",
        "title": "t", "rationale": "r", "script_text": "s",
        "interpreter": "bash", "cwd_hint": "x",
    }]


def test_notify_script_run_result_noop_when_notifier_unset(tmp_path):
    orch = _make_orchestrator(tmp_path)
    orch.notify_script_run_result(
        sr_id="SR-1", task_id="TASK-1",
        parent_message_id="om_x",
        status="completed", exit_code=0, duration_ms=100,
        stdout_head=None, stderr_head=None, reason=None,
    )


def test_notify_script_run_result_runs_in_thread_when_no_loop(tmp_path):
    orch = _make_orchestrator(tmp_path)
    captured: list[dict] = []
    done = threading.Event()

    class _FakeNotifier:
        async def send_script_run_result(self, **kw):
            captured.append(kw)
            done.set()

    orch.attach_notifier(_FakeNotifier())
    orch.notify_script_run_result(
        sr_id="SR-1", task_id="TASK-1",
        parent_message_id="om_x",
        status="completed", exit_code=0, duration_ms=100,
        stdout_head="ok", stderr_head=None, reason=None,
    )
    assert done.wait(timeout=2.0)
    assert captured[0]["parent_message_id"] == "om_x"
```

- [ ] **Step 2: Run tests, verify they fail**

```bash
uv run pytest tests/orchestrator/test_notify_script_dispatch.py -v
```

Expected: 4 failures — `notify_script_submitted` / `notify_script_run_result` are not yet methods on Orchestrator.

- [ ] **Step 3: Add the two methods**

Edit `src/orchestrator/orchestrator.py` after `notify_failed` (around line 158):

```python
    def notify_script_submitted(
        self,
        *,
        sr_id: str,
        agent: str,
        task_id: str,
        title: str,
        rationale: str,
        script_text: str,
        interpreter: str,
        cwd_hint: str | None,
    ) -> None:
        """Fire-and-forget push notification for an agent's script request.

        Same threading model as notify_escalated / notify_failed: detect a
        running event loop and create_task on it; otherwise spawn a daemon
        thread that owns its own asyncio.run.
        """
        if self._notifier is None:
            return
        import asyncio
        import threading
        coro_factory = lambda: self._notifier.send_script_request(
            sr_id=sr_id, agent=agent, task_id=task_id,
            title=title, rationale=rationale, script_text=script_text,
            interpreter=interpreter, cwd_hint=cwd_hint,
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

    def notify_script_run_result(
        self,
        *,
        sr_id: str,
        task_id: str,
        parent_message_id: str,
        status: str,
        exit_code: int | None,
        duration_ms: int,
        stdout_head: str | None,
        stderr_head: str | None,
        reason: str | None,
    ) -> None:
        """Fire-and-forget threaded reply with the SR run's terminal result."""
        if self._notifier is None:
            return
        import asyncio
        import threading
        coro_factory = lambda: self._notifier.send_script_run_result(
            sr_id=sr_id, task_id=task_id,
            parent_message_id=parent_message_id,
            status=status, exit_code=exit_code, duration_ms=duration_ms,
            stdout_head=stdout_head, stderr_head=stderr_head, reason=reason,
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

- [ ] **Step 4: Run tests, verify they pass**

```bash
uv run pytest tests/orchestrator/test_notify_script_dispatch.py -v
```

Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add src/orchestrator/orchestrator.py tests/orchestrator/test_notify_script_dispatch.py
git commit -m "$(cat <<'EOF'
feat(orchestrator): fire-and-forget bridges for SR push + result notifications

Two new methods on Orchestrator that mirror notify_escalated/notify_failed:
detect a running event loop and create_task on it, else spawn a daemon
thread owning its own asyncio.run. Both are no-ops when no notifier is
attached so disabled-Feishu orgs see zero overhead.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 7: Wire submit-hook in `submit_script`

**Files:**
- Modify: `src/daemon/routes/scripts.py` (`submit_script` route)
- Modify: `tests/daemon/test_routes_scripts.py` (add notification-fired test)

- [ ] **Step 1: Write failing test**

Append to `tests/daemon/test_routes_scripts.py`. (Inspect that file to find the existing `submit_script` happy-path test and the fixture for the test client; reuse them.)

```python
def test_submit_script_fires_notify_when_orchestrator_attached(
    client, scripts_test_setup, monkeypatch,
):
    """submit_script's success path calls org.orchestrator.notify_script_submitted."""
    org, task_id, session_id, slug = scripts_test_setup

    calls: list[dict] = []

    def _capture(**kw):
        calls.append(kw)

    # The orchestrator on the fixture is wired into the test daemon already.
    monkeypatch.setattr(org.orchestrator, "notify_script_submitted", _capture)

    resp = client.post(
        f"/api/v1/orgs/{slug}/scripts/submit",
        json={
            "task_id": task_id,
            "session_id": session_id,
            "title": "Close PR",
            "rationale": "permission wall",
            "script": "echo hi",
            "interpreter": "bash",
            "cwd_hint": None,
        },
    )
    assert resp.status_code == 201
    sr_id = resp.json()["id"]
    assert calls == [{
        "sr_id": sr_id, "agent": "dev_agent",  # match whatever the fixture's agent is
        "task_id": task_id, "title": "Close PR",
        "rationale": "permission wall", "script_text": "echo hi",
        "interpreter": "bash", "cwd_hint": None,
    }]
```

**Note:** The agent name in the asserted dict must match whatever the existing test fixture assigns. Read the existing `scripts_test_setup` fixture in `tests/daemon/test_routes_scripts.py` to confirm. If a different fixture pattern is used (e.g., a class-scoped `TestClient` with pre-seeded data), adapt to that.

- [ ] **Step 2: Run test, verify it fails**

```bash
uv run pytest tests/daemon/test_routes_scripts.py::test_submit_script_fires_notify_when_orchestrator_attached -v
```

Expected: `assert calls == [...]` fails because the route doesn't call `notify_script_submitted` yet.

- [ ] **Step 3: Wire the hook into `submit_script`**

Edit `src/daemon/routes/scripts.py` in the `submit_script` function. After the existing `audit.log_script_submitted(...)` call (around line 145), and before the final `return` (around line 156), add:

```python
    # Fire-and-forget Feishu push (no-op when notifier is unset).
    if getattr(org, "orchestrator", None) is not None:
        org.orchestrator.notify_script_submitted(
            sr_id=sr_id, agent=agent, task_id=body.task_id,
            title=title, rationale=rationale, script_text=body.script,
            interpreter=body.interpreter, cwd_hint=cwd_hint,
        )
```

(The `getattr` guard handles tests that synthesize an `OrgState` without an orchestrator. Production `OrgState`s always have one — `src/daemon/org_state.py:51` makes the field non-optional.)

- [ ] **Step 4: Run test, verify it passes**

```bash
uv run pytest tests/daemon/test_routes_scripts.py -k "submit_script" -v
```

Expected: All `submit_script*` tests pass, including the new one.

- [ ] **Step 5: Commit**

```bash
git add src/daemon/routes/scripts.py tests/daemon/test_routes_scripts.py
git commit -m "$(cat <<'EOF'
feat(scripts): push Feishu notification when an agent submits an SR

After submit_script audits script_submitted, it now schedules a
notify_script_submitted on the org's orchestrator. Fire-and-forget — a
disabled or down Feishu does not affect the agent's submit return value.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 8: Listener — `script_request` dispatch branch

**Files:**
- Modify: `src/daemon/feishu_listener.py`
- Create: `tests/daemon/test_feishu_listener_scripts.py`

- [ ] **Step 1: Write failing tests**

Create `tests/daemon/test_feishu_listener_scripts.py`:

```python
"""Listener dispatch for kind=script_request × {APPROVE, REJECT, REVISIT}."""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.daemon.feishu_listener import FeishuEventListener
from src.infrastructure.audit_logger import AuditLogger
from src.infrastructure.database import Database


def _mk_listener(tmp_path: Path):
    db = Database(tmp_path / "grassland.db")
    audit = AuditLogger(db)
    loop = asyncio.new_event_loop()
    listener = FeishuEventListener(
        slug="acme", db=db, audit=audit, chat_id="oc_xyz",
        resolve_escalation=AsyncMock(),
        revisit_from_notification=AsyncMock(),
        dispatch_via_feishu=AsyncMock(),
        send_dispatch_confirmation=AsyncMock(),
        send_dispatch_error=AsyncMock(),
        allow_dispatch=False,
        loop=loop, app_id="x", app_secret="x", domain="feishu",
        run_script_from_notification=AsyncMock(return_value={"id": "SR-1", "status": "running"}),
        reject_script_from_notification=AsyncMock(),
    )
    return listener, db, loop


def _mint(db: Database, sr_id: str = "SR-1"):
    db.mint_escalation_notification(
        feishu_message_id="om_root", org_slug="acme", task_id=sr_id,
        chat_id="oc_xyz",
        expires_at=datetime(2099, 1, 1, tzinfo=timezone.utc),
        kind="script_request",
    )


def _mk_event(text: str):
    msg = SimpleNamespace(
        chat_id="oc_xyz", message_id="om_in", root_id="om_root",
        message_type="text", content=json.dumps({"text": text}),
    )
    sender = SimpleNamespace(
        sender_type="user",
        sender_id=SimpleNamespace(open_id="ou_user_1"),
    )
    return SimpleNamespace(
        header=SimpleNamespace(event_id="evt_1"),
        event=SimpleNamespace(message=msg, sender=sender),
    )


def test_script_request_approve_routes_to_run_helper(tmp_path):
    l, db, loop = _mk_listener(tmp_path)
    _mint(db)
    loop.run_until_complete(l._handle_event_async(_mk_event("APPROVE\nlgtm")))

    l._run_script_from_notification.assert_called_once()
    kwargs = l._run_script_from_notification.call_args.kwargs
    assert kwargs["sr_id"] == "SR-1"
    assert kwargs["actor"] == "feishu-reply"
    assert kwargs["founder_note"] == "lgtm"

    row = db.get_escalation_notification("om_root")
    assert row["consumed_at"] is not None
    assert row["consumed_by"] == "feishu-reply"


def test_script_request_reject_routes_to_reject_helper(tmp_path):
    l, db, loop = _mk_listener(tmp_path)
    _mint(db)
    loop.run_until_complete(l._handle_event_async(_mk_event("REJECT\nnot a fit")))

    l._reject_script_from_notification.assert_called_once()
    kwargs = l._reject_script_from_notification.call_args.kwargs
    assert kwargs["sr_id"] == "SR-1"
    assert kwargs["reason"] == "not a fit"

    row = db.get_escalation_notification("om_root")
    assert row["consumed_at"] is not None


def test_script_request_reject_with_empty_body_uses_fallback_reason(tmp_path):
    l, db, loop = _mk_listener(tmp_path)
    _mint(db)
    loop.run_until_complete(l._handle_event_async(_mk_event("REJECT")))

    kwargs = l._reject_script_from_notification.call_args.kwargs
    assert kwargs["reason"] == "(no rationale provided via Feishu)"


def test_script_request_revisit_is_verb_mismatch(tmp_path):
    l, db, loop = _mk_listener(tmp_path)
    _mint(db)
    loop.run_until_complete(l._handle_event_async(_mk_event("REVISIT\nplease")))

    l._run_script_from_notification.assert_not_called()
    l._reject_script_from_notification.assert_not_called()
    row = db.get_escalation_notification("om_root")
    assert row["consumed_at"] is None  # unconsumed


def test_script_request_handler_exception_unconsumes(tmp_path):
    """If the helper raises (e.g. not_pending because CLI won the race),
    the notification stays unconsumed for cli-fallback consume."""
    from fastapi import HTTPException
    l, db, loop = _mk_listener(tmp_path)
    _mint(db)
    l._run_script_from_notification = AsyncMock(
        side_effect=HTTPException(
            status_code=409, detail={"code": "not_pending", "status": "rejected"},
        ),
    )
    loop.run_until_complete(l._handle_event_async(_mk_event("APPROVE")))
    row = db.get_escalation_notification("om_root")
    assert row["consumed_at"] is None
```

- [ ] **Step 2: Run tests, verify they fail**

```bash
uv run pytest tests/daemon/test_feishu_listener_scripts.py -v
```

Expected: 5 failures — `FeishuEventListener.__init__` does not accept `run_script_from_notification` / `reject_script_from_notification`; `_dispatch_reply_action` has no `script_request` branch.

- [ ] **Step 3: Extend `FeishuEventListener.__init__` to accept two new callables**

Edit `src/daemon/feishu_listener.py`. After existing imports add the new type aliases (around the existing type alias block at line 30-37):

```python
RunScriptFn = Callable[..., Awaitable[dict]]
RejectScriptFn = Callable[..., Awaitable[object]]
```

Update `FeishuEventListener.__init__` signature (around line 40-79) to accept the two new optional callables. Add to the parameter list (before `send_parse_hint`):

```python
        run_script_from_notification: RunScriptFn | None = None,
        reject_script_from_notification: RejectScriptFn | None = None,
```

And in the body, store them on self (matching the existing pattern):

```python
        self._run_script_from_notification = run_script_from_notification
        self._reject_script_from_notification = reject_script_from_notification
```

- [ ] **Step 4: Add the script_request branch to `_dispatch_reply_action`**

Locate the existing `_dispatch_reply_action` method (around line 246). Insert a new branch between "Branch 2: failure + revisit" (ending around line 303) and "Branch 3: verb mismatch" (around line 305):

```python
        # Branch 3: script_request + approve/reject
        if kind == "script_request" and decision in ("approve", "reject"):
            if (
                self._run_script_from_notification is None
                or self._reject_script_from_notification is None
            ):
                # Listener was constructed without the helpers — should never
                # happen in production (maybe_start_feishu_listener_for_org
                # always passes them), but defend defensively.
                self._audit.log_script_reply_rejected(
                    sr_id=task_id, task_id=task_id,
                    reason="handler_exception", feishu_event_id=event_id,
                )
                _close("rejected", "handler_exception")
                return
            try:
                if decision == "approve":
                    await self._run_script_from_notification(
                        sr_id=task_id,
                        actor="feishu-reply",
                        founder_note=parsed.rationale,
                    )
                else:  # reject
                    reason = parsed.rationale or "(no rationale provided via Feishu)"
                    await self._reject_script_from_notification(
                        sr_id=task_id, reason=reason,
                    )
            except Exception as exc:  # noqa: BLE001
                reason_code = "handler_exception"
                detail = getattr(exc, "detail", None)
                if isinstance(detail, dict):
                    code = detail.get("code")
                    if code in ("not_pending", "cwd_missing", "interpreter_unavailable",
                                "invalid_cwd_override", "invalid_timeout",
                                "unknown_script_request"):
                        reason_code = code
                self._audit.log_script_reply_rejected(
                    sr_id=task_id, task_id=task_id,
                    reason=reason_code, feishu_event_id=event_id,
                )
                _close("rejected", reason_code)
                return
            self._db.consume_escalation_notification(
                msg.root_id, consumed_by="feishu-reply",
            )
            self._audit.log_script_reply_processed(
                sr_id=task_id, task_id=task_id,
                decision=decision, rationale=parsed.rationale,
                feishu_event_id=event_id,
            )
            _close("consumed", None)
            return

```

(The existing "Branch 3: verb mismatch" then catches REVISIT-on-script_request and the other invalid combinations.)

- [ ] **Step 5: Wire helpers in `maybe_start_feishu_listener_for_org`**

Still in `src/daemon/feishu_listener.py`, locate `maybe_start_feishu_listener_for_org` (around line 374). Add two new closures near the existing `_resolve_for_listener` / `_revisit_for_listener` / `_dispatch_for_listener` block (around line 405-426):

```python
    async def _run_script_for_listener(*, sr_id, actor, founder_note):
        from src.daemon.routes.scripts import run_script_from_notification
        return await run_script_from_notification(
            org, sr_id=sr_id, actor=actor, founder_note=founder_note,
        )

    async def _reject_script_for_listener(*, sr_id, reason):
        from src.daemon.routes.scripts import reject_script_from_notification
        return await reject_script_from_notification(
            org, sr_id=sr_id, reason=reason,
        )
```

Then add both to the `FeishuEventListener(...)` constructor call (around line 462-479):

```python
        run_script_from_notification=_run_script_for_listener,
        reject_script_from_notification=_reject_script_for_listener,
```

- [ ] **Step 6: Run tests, verify they pass**

```bash
uv run pytest tests/daemon/test_feishu_listener_scripts.py -v
uv run pytest tests/daemon/test_feishu_listener_kind_routing.py -v  # ensure no regression
```

Expected: 5 new tests pass; existing kind-routing tests unaffected.

- [ ] **Step 7: Commit**

```bash
git add src/daemon/feishu_listener.py tests/daemon/test_feishu_listener_scripts.py
git commit -m "$(cat <<'EOF'
feat(feishu-listener): dispatch APPROVE/REJECT for kind=script_request

Inserts a new branch in _dispatch_reply_action between the failure-revisit
branch and the verb_mismatch fallback. APPROVE invokes
run_script_from_notification with stored defaults; REJECT invokes
reject_script_from_notification with the parsed rationale (or a fallback
when the body is empty). Helper exceptions with known detail codes are
recorded by code; unknown shapes record as handler_exception. Notification
is consumed only on success — race losses leave the row for CLI fallback.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 9: Terminal-result follow-up hook

**Files:**
- Modify: `src/daemon/routes/scripts.py` (`_run_script_core._run_and_persist` terminal branches)
- Modify: `tests/daemon/test_routes_scripts_helpers.py` (extend with follow-up assertion)

- [ ] **Step 1: Write failing test**

Append to `tests/daemon/test_routes_scripts_helpers.py`:

```python
@pytest.mark.asyncio
async def test_run_terminal_calls_notify_script_run_result_when_notification_exists(
    scripts_test_org, monkeypatch,
):
    """When an SR has an open Feishu notification (kind=script_request),
    the terminal transition triggers a notify_script_run_result call."""
    from datetime import datetime, timedelta, timezone
    org = scripts_test_org
    _insert_pending_sr(org)
    (org.root / "workspaces" / "dev").mkdir(parents=True, exist_ok=True)

    # Mint a Feishu notification linking this SR.
    org.db.mint_escalation_notification(
        feishu_message_id="om_parent", org_slug="acme", task_id="SR-001",
        chat_id="oc_xyz",
        expires_at=datetime.now(timezone.utc) + timedelta(hours=72),
        kind="script_request",
    )

    # Attach a mock orchestrator capturing the follow-up call.
    captured: list[dict] = []
    class _MockOrchestrator:
        def notify_script_run_result(self, **kw):
            captured.append(kw)
    org.orchestrator = _MockOrchestrator()

    async def _fake_spawn(**kw):
        from src.daemon.scripts_runner import RunResult
        return RunResult(
            status="completed", exit_code=0, duration_ms=42,
            stdout_head="hello", stderr_head=None,
            stdout_bytes=5, stderr_bytes=0,
            truncated_stdout=False, truncated_stderr=False,
            reason=None,
        )
    monkeypatch.setattr("src.daemon.routes.scripts._spawn_script", _fake_spawn)

    await run_script_from_notification(
        org, sr_id="SR-001", actor="feishu-reply", founder_note="ok",
    )

    # Wait for the background runner task to finish.
    for _ in range(40):
        if captured:
            break
        await asyncio.sleep(0.05)

    assert len(captured) == 1
    kw = captured[0]
    assert kw["sr_id"] == "SR-001"
    assert kw["task_id"] == "TASK-1"
    assert kw["parent_message_id"] == "om_parent"
    assert kw["status"] == "completed"
    assert kw["exit_code"] == 0
    assert kw["stdout_head"] == "hello"


@pytest.mark.asyncio
async def test_run_terminal_skips_follow_up_when_no_notification(
    scripts_test_org, monkeypatch,
):
    """CLI-initiated runs (no Feishu notification minted) get no follow-up."""
    org = scripts_test_org
    _insert_pending_sr(org)
    (org.root / "workspaces" / "dev").mkdir(parents=True, exist_ok=True)

    captured: list[dict] = []
    class _MockOrchestrator:
        def notify_script_run_result(self, **kw):
            captured.append(kw)
    org.orchestrator = _MockOrchestrator()

    async def _fake_spawn(**kw):
        from src.daemon.scripts_runner import RunResult
        return RunResult(
            status="completed", exit_code=0, duration_ms=10,
            stdout_head=None, stderr_head=None,
            stdout_bytes=0, stderr_bytes=0,
            truncated_stdout=False, truncated_stderr=False,
            reason=None,
        )
    monkeypatch.setattr("src.daemon.routes.scripts._spawn_script", _fake_spawn)

    await run_script_from_notification(
        org, sr_id="SR-001", actor="feishu-reply", founder_note="ok",
    )
    # Allow background to settle.
    for _ in range(20):
        await asyncio.sleep(0.05)

    assert captured == []  # no notification minted → no follow-up
```

- [ ] **Step 2: Run tests, verify they fail**

```bash
uv run pytest tests/daemon/test_routes_scripts_helpers.py -k "terminal_calls_notify or terminal_skips_follow_up" -v
```

Expected: First test fails because the follow-up isn't fired; second probably passes by default but should still be in the suite.

- [ ] **Step 3: Hook the follow-up into `_run_and_persist`**

Edit `src/daemon/routes/scripts.py` inside `_run_script_core._run_and_persist`. After the audit log (both `log_script_run_completed` and `log_script_run_failed` branches around lines 391-407), add a single follow-up dispatch. The cleanest spot is at the bottom of `_run_and_persist`, after both audit branches:

Locate the existing pair (after Task 5 extraction):

```python
        if result.status == "completed":
            audit.log_script_run_completed(...)
        else:
            audit.log_script_run_failed(...)
```

Append immediately after (still inside `_run_and_persist`):

```python
        # Feishu follow-up — only when this SR has an associated push.
        parent = org.db.get_open_notification_for_sr(sr_id, kind="script_request")
        if parent is not None and getattr(org, "orchestrator", None) is not None:
            org.orchestrator.notify_script_run_result(
                sr_id=sr_id,
                task_id=record.task_id,
                parent_message_id=parent["feishu_message_id"],
                status=result.status,
                exit_code=result.exit_code,
                duration_ms=result.duration_ms,
                stdout_head=result.stdout_head,
                stderr_head=result.stderr_head,
                reason=result.reason,
            )
```

Also handle the two earlier-return failure branches (FileNotFoundError → `spawn_failed`, generic Exception → `internal_error`). Refactor those branches to share the follow-up logic. The simplest approach: after each `audit.log_script_run_failed(...)` call in those branches, also call the same follow-up. Concretely:

In the FileNotFoundError branch, after `audit.log_script_run_failed(task_id=..., reason="spawn_failed")`:

```python
            parent = org.db.get_open_notification_for_sr(sr_id, kind="script_request")
            if parent is not None and getattr(org, "orchestrator", None) is not None:
                org.orchestrator.notify_script_run_result(
                    sr_id=sr_id, task_id=record.task_id,
                    parent_message_id=parent["feishu_message_id"],
                    status="failed", exit_code=None, duration_ms=0,
                    stdout_head=None, stderr_head=None, reason="spawn_failed",
                )
            return
```

In the generic-Exception branch, after `audit.log_script_run_failed(... reason="internal_error")`:

```python
            parent = org.db.get_open_notification_for_sr(sr_id, kind="script_request")
            if parent is not None and getattr(org, "orchestrator", None) is not None:
                org.orchestrator.notify_script_run_result(
                    sr_id=sr_id, task_id=record.task_id,
                    parent_message_id=parent["feishu_message_id"],
                    status="failed", exit_code=None, duration_ms=0,
                    stdout_head=None, stderr_head=str(exc), reason="internal_error",
                )
            return
```

- [ ] **Step 4: Run tests, verify they pass**

```bash
uv run pytest tests/daemon/test_routes_scripts_helpers.py -v
```

Expected: Both new tests pass; all earlier helper tests still pass.

- [ ] **Step 5: Commit**

```bash
git add src/daemon/routes/scripts.py tests/daemon/test_routes_scripts_helpers.py
git commit -m "$(cat <<'EOF'
feat(scripts): post Feishu follow-up with terminal result

When a script run terminates (completed, failed, spawn_failed, or
internal_error), check for an associated kind=script_request notification
and dispatch notify_script_run_result through the orchestrator's
fire-and-forget bridge. CLI-initiated runs (no notification) skip the
follow-up silently. Failure to send the follow-up never affects the run's
own audit + transition path.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 10: Integration test — submit → APPROVE → terminal-result, end-to-end

**Files:**
- Create: `tests/integration/test_feishu_script_notifications_e2e.py`

This test uses the same fake-Feishu HTTP+WS pattern used by `tests/integration/test_daemon_restart_sweep_failure_notify.py` (or whatever the existing integration test scaffolding provides). Inspect that file's structure before writing this one — it's the template.

- [ ] **Step 1: Inspect existing integration scaffold**

```bash
ls tests/integration/ | grep -E "feishu|notify"
```

Read the top-of-file fixtures of the most-recent Feishu integration test. Identify how the fake-Feishu HTTP server is stood up, how `fake_claude.sh` is plumbed, and how the daemon is launched.

- [ ] **Step 2: Write the integration test**

Create `tests/integration/test_feishu_script_notifications_e2e.py`:

```python
"""End-to-end: agent submits SR → Feishu push → founder APPROVE → run → follow-up."""
from __future__ import annotations

import json
import pytest

# Reuse whatever fixture provides:
#   - daemon: a running daemon process bound to a tmp runtime
#   - fake_feishu: an in-process FastAPI app that mimics Feishu's send + event endpoints
#   - submit_sr_as_agent(sr_payload) -> str (returns SR id)
#   - simulate_feishu_reply(root_message_id, text) -> None
# These are the conventions in the existing failure-notify e2e test.


@pytest.mark.integration
def test_submit_sr_then_approve_via_feishu_runs_and_posts_result(
    daemon, fake_feishu, submit_sr_as_agent, simulate_feishu_reply,
):
    # 1. Agent submits SR
    sr_id = submit_sr_as_agent({
        "title": "echo hi",
        "rationale": "smoke test",
        "script": "echo hello-from-sr",
        "interpreter": "bash",
        "cwd_hint": None,
    })

    # 2. Fake Feishu received a push for kind=script_request
    pushed = fake_feishu.wait_for_post(matching={"title_contains": sr_id})
    assert pushed is not None
    assert "submitted" in pushed["title"]
    assert "echo hello-from-sr" in "\n".join(pushed["body"])

    # 3. Founder replies APPROVE in the thread
    simulate_feishu_reply(root_message_id=pushed["message_id"], text="APPROVE")

    # 4. SR transitions running → completed
    sr = daemon.poll_sr(sr_id, until_status={"completed", "failed"})
    assert sr["status"] == "completed"
    assert sr["exit_code"] == 0

    # 5. Fake Feishu received a threaded follow-up
    follow_up = fake_feishu.wait_for_thread_reply(
        parent_message_id=pushed["message_id"],
    )
    assert follow_up is not None
    assert "completed" in follow_up["title"]
    assert "hello-from-sr" in "\n".join(follow_up["body"])


@pytest.mark.integration
def test_submit_sr_then_reject_via_feishu(
    daemon, fake_feishu, submit_sr_as_agent, simulate_feishu_reply,
):
    sr_id = submit_sr_as_agent({
        "title": "noop", "rationale": "no", "script": "true",
        "interpreter": "bash", "cwd_hint": None,
    })
    pushed = fake_feishu.wait_for_post(matching={"title_contains": sr_id})
    simulate_feishu_reply(
        root_message_id=pushed["message_id"],
        text="REJECT\nnot needed",
    )
    sr = daemon.poll_sr(sr_id, until_status={"rejected"})
    assert sr["status"] == "rejected"
    assert sr["reject_reason"] == "not needed"
    # No terminal-result follow-up for reject (reject is not a "run")
    assert fake_feishu.find_thread_reply(parent_message_id=pushed["message_id"]) is None
```

**Note for the engineer:** The fixture names (`daemon`, `fake_feishu`, `submit_sr_as_agent`, `simulate_feishu_reply`) are placeholders matching the conventions in the existing failure-notify e2e test. If the existing test uses different names, adapt. If no `submit_sr_as_agent` helper exists, write one inline that uses the test client to call `POST /api/v1/orgs/{slug}/scripts/submit` with a valid `(task_id, session_id)` pair created via the existing session-tracker test helpers.

- [ ] **Step 3: Run integration tests**

```bash
uv run pytest tests/integration/test_feishu_script_notifications_e2e.py -v -m integration
```

Expected: Both tests pass. If they fail because the fake-Feishu fixture doesn't yet expose `wait_for_thread_reply` / `wait_for_post` helpers, extend the fixture in `tests/integration/conftest.py` (or wherever it lives) — these are test-infrastructure additions, not feature changes.

- [ ] **Step 4: Run the full integration suite — verify no regressions**

```bash
uv run pytest tests/ -v -m integration
```

Expected: All integration tests pass.

- [ ] **Step 5: Commit**

```bash
git add tests/integration/test_feishu_script_notifications_e2e.py
# Also stage any conftest.py edits that extended the fake-Feishu fixture.
git commit -m "$(cat <<'EOF'
test(integration): SR Feishu push + APPROVE + result follow-up e2e

Two scenarios covering the full submit-push-act-result loop:
- APPROVE: SR transitions completed, follow-up posted with stdout head
- REJECT: SR transitions rejected, no follow-up posted

Uses the same fake-Feishu HTTP+WS pattern as the existing failure-notify
e2e suite; extends the fixture with wait_for_thread_reply where needed.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 11: Docs — setup runbook + CLAUDE.md note

**Files:**
- Modify: `docs/setup/feishu-notifications.md`
- Modify: `CLAUDE.md`

- [ ] **Step 1: Append a "Script requests" section to the setup runbook**

Read the existing `docs/setup/feishu-notifications.md` and add a section at the end:

```markdown
## Script requests (SR-NNN)

When an agent submits a script request (`grassland scripts submit`), the daemon pushes a Feishu post to the configured chat. Reply in the same thread to act on it:

- **Approve and run:**
  ```
  APPROVE
  <optional note>
  ```
  The daemon runs the SR with the agent-provided defaults (`cwd_hint`, 300-second timeout). When the script terminates, the daemon posts a threaded reply with the exit code, duration, and head of stdout/stderr.

- **Reject:**
  ```
  REJECT
  <reason>
  ```
  The SR transitions to `rejected` with the rationale captured. No terminal-result follow-up is posted.

- **Override defaults:** Use the CLI or web UI — Feishu replies cannot set `cwd_override` or `timeout_seconds` in v1.

- **APPROVE shows the full script in the push body** (up to 1500 chars). Read it before replying. There is no "are you sure" prompt in Feishu — the message-body preview is the confirmation surface.

Audit events for the script-request notification flow: `script_notify_sent`, `script_notify_failed`, `script_reply_processed`, `script_reply_rejected`, `script_run_result_notify_sent`, `script_run_result_notify_failed`. Inspect via `grassland audit --org <slug> --action script_*`.
```

- [ ] **Step 2: Update CLAUDE.md**

Edit `CLAUDE.md`. Find the existing Feishu section (search for "Feishu notifications (founder push + reply-to-unblock)"). In the "**Optional features:**" block (or alongside it), add:

```markdown
- **Script requests** — agent SR submissions push a Feishu post to the founder; reply `APPROVE` to run with stored defaults, `REJECT\n<reason>` to reject. Terminal-result posted as a threaded reply. Spec: `docs/superpowers/specs/2026-05-25-feishu-script-request-notifications-design.md`. Notification kind: `script_request` in `escalation_notifications.kind`; routes are unchanged; new listener branch in `_dispatch_reply_action`.
```

- [ ] **Step 3: No tests needed for docs**

- [ ] **Step 4: Commit**

```bash
git add docs/setup/feishu-notifications.md CLAUDE.md
git commit -m "$(cat <<'EOF'
docs(feishu): script-request notification grammar + CLAUDE.md note

Setup runbook gains a "Script requests" section explaining the
APPROVE/REJECT reply grammar, the terminal-result follow-up, and the
six new audit actions. CLAUDE.md's Feishu section gets a bullet pointing
at the design spec.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 12: Final verification — full suite

- [ ] **Step 1: Run unit suite**

```bash
uv run pytest tests/ -v
```

Expected: All unit tests pass.

- [ ] **Step 2: Run integration suite**

```bash
uv run pytest tests/ -v -m integration
```

Expected: All integration tests pass.

- [ ] **Step 3: Sanity-check the daemon boots and the web UI still builds**

```bash
scripts/daemon.sh status || scripts/daemon.sh start
sleep 2
scripts/daemon.sh status
# (No web changes in this plan — skipping web build unless `web/` files were touched.)
```

Expected: Daemon up and running on its usual port.

- [ ] **Step 4: Spot-check via a manual SR submit (optional, requires Feishu test app)**

If a test org with `feishu_notifications.enabled: true` is configured:

1. Trigger an SR via `grassland scripts submit --from-file /tmp/sr.json` (impersonating the agent with a valid `(task_id, session_id)` pair).
2. Observe a Feishu push appears in the configured chat.
3. Reply `APPROVE` and confirm the SR transitions to `completed`/`failed`.
4. Observe a threaded follow-up arrives with the terminal status.

Skip if no Feishu test credentials are available locally; integration tests cover the contract.

---

## Self-Review

**Spec coverage:**

| Spec section | Implemented by |
|---|---|
| §1 Goal — push, APPROVE/REJECT, terminal follow-up | Tasks 4, 8, 9 |
| §2 Non-goals — no streaming, no overrides, no backfill | Enforced by helper signatures (no override args) + no migration |
| §3 User-facing interface — message body shapes | Tasks 3 (body builders) |
| §3.2 Edge paths (bad parse, verb mismatch, stale, race) | Task 8 (verb mismatch, handler exception) + existing listener machinery (dedup, expiry, consumed-check) |
| §4 Architecture — modules touched | All tasks |
| §4.4 In-process route helpers | Task 5 |
| §4.5 Terminal-result follow-up | Task 9 |
| §5 Data model — kind allowlist + get_open_notification_for_sr | Task 1 |
| §5.3 Audit events (6 new actions) | Task 2 |
| §6 Reply parsing — no grammar changes | Confirmed (parse_reply already accepts APPROVE/REJECT/REVISIT) |
| §7 Body builders | Task 3 |
| §8 Hook points (6 files) | Tasks 1, 2, 4, 5, 6, 7, 8, 9 |
| §9 Auth — no new surface | Confirmed by reusing existing `require_token` + chat_id filter |
| §10 Failure modes | Task 8 (handler exception → unconsumed); Task 9 (failed run → still post follow-up) |
| §11 Tests | Tasks 1, 2, 3, 4, 5, 6, 8, 9, 10 |
| §12 Rollout order | Plan task ordering matches |

**Placeholder scan:** No "TBD", "implement later", "add appropriate validation", "similar to Task N", or "etc." Each code-bearing step contains the actual code.

**Type consistency:**
- `send_script_request` / `send_script_run_result` signatures (Task 4) match the keyword args passed by `notify_script_submitted` / `notify_script_run_result` (Task 6).
- `run_script_from_notification(org, *, sr_id, actor, founder_note)` (Task 5) matches the closure in `maybe_start_feishu_listener_for_org` (Task 8) and the `_run_script_from_notification` field on the listener (Task 8).
- `reject_script_from_notification(org, *, sr_id, reason)` (Task 5) matches the closure (Task 8) and the listener field (Task 8).
- `get_open_notification_for_sr(sr_id, *, kind)` (Task 1) matches the call-site signature in Task 9.
- Audit method signatures (Task 2) match call-sites in notifier (Task 4) and listener (Task 8).
- `_SCRIPT_PREVIEW_CAP` and `_RESULT_OUTPUT_PREVIEW_CAP` (Task 3) match the test imports.
