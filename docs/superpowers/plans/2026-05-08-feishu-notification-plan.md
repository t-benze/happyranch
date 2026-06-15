# Feishu Escalation Notification — Phase 1 + Phase 2 Implementation Plan

**Status: REMOVED in TASK-302 (THR-022).** Web UI + threads are sole control surface. DB tables dormant.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When a task transitions to `BLOCKED/ESCALATED`, the daemon sends a Feishu post message to a configured 1:1 chat. The founder replies in the thread with `APPROVE` or `REJECT` + rationale; a long-lived event listener parses the reply and calls the existing `resolve_escalation` route in-process — same effect as `opc resolve-escalation`.

**Architecture:** A new `src/infrastructure/feishu/` package owns all Feishu I/O via the `lark-oapi` Python SDK. `FeishuClient` wraps the SDK's `im.v1.message.create`. `EscalationNotifier` builds a post-format body and persists a correlation row keyed by the returned `feishu_message_id`. `FeishuEventListener` runs the SDK's `WSClient` in a daemon thread; its handler bridges to the daemon's asyncio loop via `asyncio.run_coroutine_threadsafe` and parses inbound replies.

**Tech Stack:** Python 3.13, `lark-oapi>=1.6,<2` (new dep), httpx (already), pydantic v2, sqlite3, asyncio. Existing FastAPI lifespan + TaskQueue patterns.

**Spec:** `docs/superpowers/specs/2026-05-08-feishu-notification-design.md`.

---

## File Map

**Created:**
- `src/infrastructure/feishu/__init__.py` (empty)
- `src/infrastructure/feishu/client.py` — `FeishuClient.send_post_message`
- `src/infrastructure/feishu/notifier.py` — `EscalationNotifier`
- `src/infrastructure/feishu/reply_parser.py` — `parse_reply` pure function
- `src/daemon/feishu_listener.py` — `FeishuEventListener`
- `tests/infrastructure/__init__.py` (if missing)
- `tests/infrastructure/feishu/__init__.py`
- `tests/infrastructure/feishu/test_client.py`
- `tests/infrastructure/feishu/test_notifier.py`
- `tests/infrastructure/feishu/test_reply_parser.py`
- `tests/daemon/test_feishu_listener.py`
- `tests/daemon/test_org_state_notifier.py`
- `tests/orchestrator/test_run_step_notify.py`
- `tests/integration/fake_feishu.py`
- `tests/integration/test_feishu_notification_phase1.py`
- `examples/orgs/hk-macau-tourism/org/config.yaml`
- `docs/setup/feishu-notifications.md`

**Modified:**
- `pyproject.toml` — add `lark-oapi>=1.6,<2`
- `src/orchestrator/org_config.py` — `FeishuNotificationsConfig` + parser + `resolve_feishu_credentials`
- `src/infrastructure/database.py` — `escalation_notifications` + `processed_event_ids` tables + CRUD
- `src/infrastructure/audit_logger.py` — 4 new event methods
- `src/orchestrator/orchestrator.py` — `attach_notifier` + `notify_escalated`
- `src/orchestrator/run_step.py` — call `notify_escalated` after `log_escalation` (2 sites)
- `src/daemon/__main__.py` — pass `org.orchestrator` to `_sweep_on_startup` so recovery escalations notify too
- `src/daemon/org_state.py` — wire optional notifier + listener
- `src/daemon/state.py` — start/stop listeners in lifespan
- `src/daemon/app.py` — lifespan integration for listeners
- `tests/test_database.py` — extend
- `tests/test_audit_logger.py` — extend
- `tests/test_org_config.py` — extend
- `tests/test_orchestrator.py` — extend
- `tests/daemon/test_startup_recovery.py` — extend
- `CLAUDE.md` — one-line Tech Stack reference

---

# Phase 1 — Outbound

## Task 1: Add `lark-oapi` dependency

**Files:**
- Modify: `pyproject.toml`

- [ ] **Step 1: Add dependency**

In `pyproject.toml`, locate the `dependencies = [...]` array. Add `"lark-oapi>=1.6,<2",` to the list (alphabetical position is fine — match the existing style; if entries are alphabetical, insert it; otherwise append).

- [ ] **Step 2: Sync the lockfile**

```bash
uv sync
```

Expected: lark-oapi installed; no other changes. Run `uv pip show lark-oapi | head -5` to confirm.

- [ ] **Step 3: Smoke import**

```bash
uv run python -c "import lark_oapi as lark; print(lark.__name__)"
```

Expected: prints `lark_oapi`.

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml uv.lock
git commit -m "deps: add lark-oapi for Feishu integration"
```

---

## Task 2: `escalation_notifications` and `processed_event_ids` schema

**Files:**
- Modify: `src/infrastructure/database.py`
- Test: `tests/test_database.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_database.py`:

```python
def test_escalation_notifications_table_exists(tmp_path):
    db = Database(tmp_path / "opc.db")
    cur = db._conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='escalation_notifications'"
    )
    assert cur.fetchone() is not None


def test_escalation_notifications_index_exists(tmp_path):
    db = Database(tmp_path / "opc.db")
    cur = db._conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index' "
        "AND tbl_name='escalation_notifications'"
    )
    names = {row[0] for row in cur.fetchall()}
    assert "idx_escalation_notifications_task" in names


def test_processed_event_ids_table_exists(tmp_path):
    db = Database(tmp_path / "opc.db")
    cur = db._conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='processed_event_ids'"
    )
    assert cur.fetchone() is not None
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/test_database.py -v -k "escalation_notifications or processed_event_ids"
```

Expected: FAIL.

- [ ] **Step 3: Add schema in `_init_schema`**

In `src/infrastructure/database.py`, after the `session_token_usage` block (around line 172), insert:

```python
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS escalation_notifications (
                feishu_message_id TEXT PRIMARY KEY,
                org_slug          TEXT NOT NULL,
                task_id           TEXT NOT NULL,
                chat_id           TEXT NOT NULL,
                created_at        TEXT NOT NULL,
                expires_at        TEXT NOT NULL,
                consumed_at       TEXT,
                consumed_by       TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_escalation_notifications_task
                ON escalation_notifications (task_id);

            CREATE TABLE IF NOT EXISTS processed_event_ids (
                org_slug          TEXT NOT NULL,
                feishu_event_id   TEXT NOT NULL,
                processed_at      TEXT NOT NULL,
                outcome           TEXT NOT NULL,
                reason            TEXT,
                PRIMARY KEY (org_slug, feishu_event_id)
            );
        """)
```

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/test_database.py -v -k "escalation_notifications or processed_event_ids"
```

Expected: 3 PASS.

- [ ] **Step 5: Commit**

```bash
git add src/infrastructure/database.py tests/test_database.py
git commit -m "feat(db): add escalation_notifications + processed_event_ids tables"
```

---

## Task 3: DB CRUD — mint, get, consume notification; insert processed event

**Files:**
- Modify: `src/infrastructure/database.py`
- Test: `tests/test_database.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_database.py`:

```python
from datetime import datetime, timedelta, timezone


def test_mint_escalation_notification_writes_row(tmp_path):
    db = Database(tmp_path / "opc.db")
    expires = datetime.now(timezone.utc) + timedelta(hours=72)
    db.mint_escalation_notification(
        feishu_message_id="om_xyz",
        org_slug="hk-macau-tourism",
        task_id="TASK-001",
        chat_id="oc_abc",
        expires_at=expires,
    )
    row = db.get_escalation_notification("om_xyz")
    assert row is not None
    assert row["org_slug"] == "hk-macau-tourism"
    assert row["task_id"] == "TASK-001"
    assert row["chat_id"] == "oc_abc"
    assert row["consumed_at"] is None


def test_get_escalation_notification_missing_returns_none(tmp_path):
    db = Database(tmp_path / "opc.db")
    assert db.get_escalation_notification("om_missing") is None


def test_consume_escalation_notification_marks_consumed(tmp_path):
    db = Database(tmp_path / "opc.db")
    expires = datetime.now(timezone.utc) + timedelta(hours=72)
    db.mint_escalation_notification(
        feishu_message_id="om_1", org_slug="o", task_id="T1",
        chat_id="oc", expires_at=expires,
    )
    assert db.consume_escalation_notification("om_1", consumed_by="cli-fallback") is True
    row = db.get_escalation_notification("om_1")
    assert row["consumed_at"] is not None
    assert row["consumed_by"] == "cli-fallback"


def test_consume_escalation_notification_twice_returns_false(tmp_path):
    db = Database(tmp_path / "opc.db")
    expires = datetime.now(timezone.utc) + timedelta(hours=72)
    db.mint_escalation_notification(
        feishu_message_id="om_1", org_slug="o", task_id="T1",
        chat_id="oc", expires_at=expires,
    )
    assert db.consume_escalation_notification("om_1", consumed_by="feishu-reply") is True
    assert db.consume_escalation_notification("om_1", consumed_by="feishu-reply") is False


def test_record_processed_event_first_call_returns_true(tmp_path):
    db = Database(tmp_path / "opc.db")
    assert db.record_processed_event(
        org_slug="o", feishu_event_id="evt_1",
        outcome="consumed", reason=None,
    ) is True


def test_record_processed_event_duplicate_returns_false(tmp_path):
    db = Database(tmp_path / "opc.db")
    db.record_processed_event(
        org_slug="o", feishu_event_id="evt_1",
        outcome="consumed", reason=None,
    )
    assert db.record_processed_event(
        org_slug="o", feishu_event_id="evt_1",
        outcome="rejected", reason="dup",
    ) is False
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/test_database.py -v -k "escalation_notification or processed_event"
```

Expected: FAIL — methods don't exist.

- [ ] **Step 3: Implement the methods**

Add to `src/infrastructure/database.py` near the end of the `Database` class (after the existing `session_token_usage` aggregation methods):

```python
    # --- Escalation Notifications ---

    @_synchronized
    def mint_escalation_notification(
        self,
        feishu_message_id: str,
        org_slug: str,
        task_id: str,
        chat_id: str,
        expires_at: datetime,
    ) -> None:
        self._conn.execute(
            """INSERT INTO escalation_notifications
               (feishu_message_id, org_slug, task_id, chat_id,
                created_at, expires_at, consumed_at, consumed_by)
               VALUES (?, ?, ?, ?, ?, ?, NULL, NULL)""",
            (
                feishu_message_id, org_slug, task_id, chat_id,
                datetime.now(timezone.utc).isoformat(),
                expires_at.astimezone(timezone.utc).isoformat(),
            ),
        )
        self._conn.commit()

    @_synchronized
    def get_escalation_notification(self, feishu_message_id: str) -> dict | None:
        cur = self._conn.execute(
            """SELECT feishu_message_id, org_slug, task_id, chat_id,
                      created_at, expires_at, consumed_at, consumed_by
               FROM escalation_notifications WHERE feishu_message_id = ?""",
            (feishu_message_id,),
        )
        row = cur.fetchone()
        if row is None:
            return None
        return dict(row)

    @_synchronized
    def consume_escalation_notification(
        self, feishu_message_id: str, consumed_by: str,
    ) -> bool:
        cur = self._conn.execute(
            """UPDATE escalation_notifications
               SET consumed_at = ?, consumed_by = ?
               WHERE feishu_message_id = ? AND consumed_at IS NULL""",
            (datetime.now(timezone.utc).isoformat(), consumed_by, feishu_message_id),
        )
        self._conn.commit()
        return cur.rowcount == 1

    # --- Processed Event Dedup ---

    @_synchronized
    def record_processed_event(
        self,
        org_slug: str,
        feishu_event_id: str,
        outcome: str,
        reason: str | None,
    ) -> bool:
        """Insert dedup row. Returns True on first insert, False on duplicate."""
        cur = self._conn.execute(
            """INSERT OR IGNORE INTO processed_event_ids
               (org_slug, feishu_event_id, processed_at, outcome, reason)
               VALUES (?, ?, ?, ?, ?)""",
            (
                org_slug, feishu_event_id,
                datetime.now(timezone.utc).isoformat(),
                outcome, reason,
            ),
        )
        self._conn.commit()
        return cur.rowcount == 1
```

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/test_database.py -v -k "escalation_notification or processed_event"
```

Expected: 6 PASS.

- [ ] **Step 5: Commit**

```bash
git add src/infrastructure/database.py tests/test_database.py
git commit -m "feat(db): mint/get/consume escalation_notifications + dedup processed_event_ids"
```

---

## Task 4: `FeishuNotificationsConfig` parsing

**Files:**
- Modify: `src/orchestrator/org_config.py`
- Test: `tests/test_org_config.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_org_config.py`:

```python
from src.orchestrator.org_config import (
    FeishuNotificationsConfig,
    OrgConfig,
    OrgConfigError,
    load_org_config,
)
from src.orchestrator._paths import OrgPaths


def _write_config(paths: OrgPaths, body: str) -> None:
    paths.org_config_path.parent.mkdir(parents=True, exist_ok=True)
    paths.org_config_path.write_text(body)


def test_feishu_notifications_missing_block_returns_none(test_runtime):
    _write_config(test_runtime, "session_timeout_seconds: 1800\n")
    cfg = load_org_config(test_runtime)
    assert cfg.feishu_notifications is None


def test_feishu_notifications_disabled_returns_none(test_runtime):
    _write_config(test_runtime, """
feishu_notifications:
  enabled: false
  provider: feishu
  region: feishu
  chat_id: oc_xxx
""")
    cfg = load_org_config(test_runtime)
    assert cfg.feishu_notifications is None


def test_feishu_notifications_full_block_parses(test_runtime):
    _write_config(test_runtime, """
feishu_notifications:
  enabled: true
  provider: feishu
  region: feishu
  chat_id: oc_aaa111
  reply_ttl_hours: 48
""")
    cfg = load_org_config(test_runtime)
    f = cfg.feishu_notifications
    assert f is not None
    assert f.provider == "feishu"
    assert f.region == "feishu"
    assert f.chat_id == "oc_aaa111"
    assert f.reply_ttl_hours == 48


def test_feishu_notifications_default_ttl(test_runtime):
    _write_config(test_runtime, """
feishu_notifications:
  enabled: true
  provider: feishu
  region: feishu
  chat_id: oc_aaa
""")
    cfg = load_org_config(test_runtime)
    assert cfg.feishu_notifications.reply_ttl_hours == 72


def test_feishu_notifications_invalid_provider_raises(test_runtime):
    _write_config(test_runtime, """
feishu_notifications:
  enabled: true
  provider: slack
  region: feishu
  chat_id: oc_xxx
""")
    try:
        load_org_config(test_runtime)
    except OrgConfigError as exc:
        assert "provider" in str(exc)
    else:
        raise AssertionError("expected OrgConfigError")


def test_feishu_notifications_invalid_region_raises(test_runtime):
    _write_config(test_runtime, """
feishu_notifications:
  enabled: true
  provider: feishu
  region: us
  chat_id: oc_xxx
""")
    try:
        load_org_config(test_runtime)
    except OrgConfigError as exc:
        assert "region" in str(exc)
    else:
        raise AssertionError("expected OrgConfigError")


def test_feishu_notifications_missing_chat_id_raises(test_runtime):
    _write_config(test_runtime, """
feishu_notifications:
  enabled: true
  provider: feishu
  region: feishu
""")
    try:
        load_org_config(test_runtime)
    except OrgConfigError as exc:
        assert "chat_id" in str(exc)
    else:
        raise AssertionError("expected OrgConfigError")


def test_feishu_notifications_ttl_out_of_range_raises(test_runtime):
    _write_config(test_runtime, """
feishu_notifications:
  enabled: true
  provider: feishu
  region: feishu
  chat_id: oc_x
  reply_ttl_hours: 9999
""")
    try:
        load_org_config(test_runtime)
    except OrgConfigError as exc:
        assert "reply_ttl_hours" in str(exc)
    else:
        raise AssertionError("expected OrgConfigError")
```

- [ ] **Step 2: Run tests**

```bash
uv run pytest tests/test_org_config.py -v
```

Expected: 8 new tests FAIL (existing tests still pass).

- [ ] **Step 3: Replace `src/orchestrator/org_config.py`**

```python
"""Org-level configuration loaded from <runtime>/org/config.yaml.

A small, additive layer between the global Settings defaults and per-agent
overrides. The file is optional — a runtime without it inherits the global
defaults exactly as before.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

import yaml

from src.orchestrator._paths import OrgPaths


class OrgConfigError(ValueError):
    """Raised when org/config.yaml is malformed or fails validation."""


# region → SDK domain literal accepted by lark_oapi.Client.builder().domain(...)
FEISHU_REGIONS = {"feishu", "lark"}


@dataclass(frozen=True)
class FeishuNotificationsConfig:
    provider: str
    region: str
    chat_id: str
    reply_ttl_hours: int = 72


@dataclass(frozen=True)
class OrgConfig:
    session_timeout_seconds: int | None = None
    feishu_notifications: FeishuNotificationsConfig | None = None


def _validate_positive_int(
    value: object, name: str, *, min_v: int, max_v: int, path: str,
) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise OrgConfigError(f"{path}: {name} must be an integer, got {value!r}")
    if value < min_v or value > max_v:
        raise OrgConfigError(
            f"{path}: {name} must be in [{min_v}, {max_v}], got {value}"
        )
    return value


def _parse_feishu_notifications(
    block: dict, path: str,
) -> FeishuNotificationsConfig | None:
    if not block.get("enabled", False):
        return None

    provider = block.get("provider")
    if provider != "feishu":
        raise OrgConfigError(
            f"{path}: feishu_notifications.provider must be 'feishu' in v1, "
            f"got {provider!r}"
        )

    region = block.get("region")
    if region not in FEISHU_REGIONS:
        raise OrgConfigError(
            f"{path}: feishu_notifications.region must be one of "
            f"{sorted(FEISHU_REGIONS)}, got {region!r}"
        )

    chat_id = block.get("chat_id")
    if not chat_id or not isinstance(chat_id, str):
        raise OrgConfigError(
            f"{path}: feishu_notifications.chat_id is required when enabled"
        )

    ttl = _validate_positive_int(
        block.get("reply_ttl_hours", 72),
        "feishu_notifications.reply_ttl_hours",
        min_v=1, max_v=720, path=path,
    )

    return FeishuNotificationsConfig(
        provider=provider,
        region=region,
        chat_id=chat_id,
        reply_ttl_hours=ttl,
    )


def load_org_config(paths: OrgPaths) -> OrgConfig:
    """Load <runtime>/org/config.yaml. Missing file -> empty OrgConfig."""
    path = paths.org_config_path
    if not path.exists():
        return OrgConfig()

    try:
        data = yaml.safe_load(path.read_text()) or {}
    except yaml.YAMLError as exc:
        raise OrgConfigError(f"malformed YAML in {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise OrgConfigError(f"{path}: top-level must be a mapping")

    timeout = data.get("session_timeout_seconds")
    if timeout is not None:
        if not isinstance(timeout, int) or isinstance(timeout, bool) or timeout <= 0:
            raise OrgConfigError(
                f"{path}: session_timeout_seconds must be a positive integer, "
                f"got {timeout!r}"
            )

    feishu_block = data.get("feishu_notifications")
    feishu_cfg: FeishuNotificationsConfig | None = None
    if feishu_block is not None:
        if not isinstance(feishu_block, dict):
            raise OrgConfigError(
                f"{path}: feishu_notifications must be a mapping"
            )
        feishu_cfg = _parse_feishu_notifications(feishu_block, str(path))

    return OrgConfig(
        session_timeout_seconds=timeout,
        feishu_notifications=feishu_cfg,
    )


def _slug_env_suffix(slug: str) -> str:
    return slug.upper().replace("-", "_")


def resolve_feishu_credentials(slug: str) -> tuple[str | None, str | None]:
    """Look up Feishu app credentials for an org from environment.

    Per-org override takes precedence over the unsuffixed default:

        OPC_FEISHU_APP_ID__<SUFFIX>     # checked first
        OPC_FEISHU_APP_ID               # fallback
    """
    suffix = _slug_env_suffix(slug)
    app_id = os.environ.get(f"OPC_FEISHU_APP_ID__{suffix}") \
        or os.environ.get("OPC_FEISHU_APP_ID")
    app_secret = os.environ.get(f"OPC_FEISHU_APP_SECRET__{suffix}") \
        or os.environ.get("OPC_FEISHU_APP_SECRET")
    return app_id, app_secret
```

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/test_org_config.py -v
```

Expected: All PASS.

- [ ] **Step 5: Commit**

```bash
git add src/orchestrator/org_config.py tests/test_org_config.py
git commit -m "feat(org-config): parse feishu_notifications block + resolve_feishu_credentials"
```

---

## Task 5: Audit log methods

**Files:**
- Modify: `src/infrastructure/audit_logger.py`
- Test: `tests/test_audit_logger.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_audit_logger.py`:

```python
def test_log_escalation_notify_sent(tmp_path):
    db = Database(tmp_path / "opc.db")
    AuditLogger(db).log_escalation_notify_sent(
        task_id="TASK-1", feishu_message_id="om_xyz",
    )
    rows = db.get_audit_logs("TASK-1")
    assert len(rows) == 1
    assert rows[0]["action"] == "escalation_notify_sent"
    assert rows[0]["payload"]["feishu_message_id"] == "om_xyz"


def test_log_escalation_notify_failed(tmp_path):
    db = Database(tmp_path / "opc.db")
    AuditLogger(db).log_escalation_notify_failed(
        task_id="TASK-1", error="feishu send code=99991663",
    )
    rows = db.get_audit_logs("TASK-1")
    assert rows[0]["action"] == "escalation_notify_failed"
    assert rows[0]["payload"]["error"] == "feishu send code=99991663"


def test_log_escalation_reply_processed(tmp_path):
    db = Database(tmp_path / "opc.db")
    AuditLogger(db).log_escalation_reply_processed(
        task_id="TASK-1", decision="approve", rationale="ok"
    )
    rows = db.get_audit_logs("TASK-1")
    assert rows[0]["action"] == "escalation_reply_processed"
    assert rows[0]["payload"]["decision"] == "approve"
    assert rows[0]["payload"]["rationale"] == "ok"


def test_log_escalation_reply_rejected(tmp_path):
    db = Database(tmp_path / "opc.db")
    AuditLogger(db).log_escalation_reply_rejected(
        task_id="TASK-1", reason="bad_decision",
    )
    rows = db.get_audit_logs("TASK-1")
    assert rows[0]["action"] == "escalation_reply_rejected"
    assert rows[0]["payload"]["reason"] == "bad_decision"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/test_audit_logger.py -v -k escalation_
```

Expected: 4 FAIL.

- [ ] **Step 3: Add the methods**

In `src/infrastructure/audit_logger.py`, after `log_escalation_resolved`:

```python
    def log_escalation_notify_sent(
        self, task_id: str, feishu_message_id: str,
    ) -> None:
        self._db.insert_audit_log(
            task_id=task_id,
            agent="daemon",
            action="escalation_notify_sent",
            payload={"feishu_message_id": feishu_message_id},
        )

    def log_escalation_notify_failed(self, task_id: str, error: str) -> None:
        self._db.insert_audit_log(
            task_id=task_id,
            agent="daemon",
            action="escalation_notify_failed",
            payload={"error": error},
        )

    def log_escalation_reply_processed(
        self, task_id: str, decision: str, rationale: str,
    ) -> None:
        self._db.insert_audit_log(
            task_id=task_id,
            agent="founder",
            action="escalation_reply_processed",
            payload={"decision": decision, "rationale": rationale},
        )

    def log_escalation_reply_rejected(self, task_id: str, reason: str) -> None:
        self._db.insert_audit_log(
            task_id=task_id,
            agent="daemon",
            action="escalation_reply_rejected",
            payload={"reason": reason},
        )
```

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/test_audit_logger.py -v
```

Expected: All PASS.

- [ ] **Step 5: Commit**

```bash
git add src/infrastructure/audit_logger.py tests/test_audit_logger.py
git commit -m "feat(audit): escalation_notify_{sent,failed} + escalation_reply_{processed,rejected}"
```

---

## Task 6: `FeishuClient` send wrapper

**Files:**
- Create: `src/infrastructure/feishu/__init__.py` (empty)
- Create: `src/infrastructure/feishu/client.py`
- Create: `tests/infrastructure/feishu/__init__.py` (empty)
- Create: `tests/infrastructure/feishu/test_client.py`

- [ ] **Step 1: Create package directories**

```bash
mkdir -p src/infrastructure/feishu tests/infrastructure/feishu
touch src/infrastructure/feishu/__init__.py tests/infrastructure/feishu/__init__.py
```

If `tests/infrastructure/__init__.py` doesn't exist, `touch` it.

- [ ] **Step 2: Write the failing tests**

Create `tests/infrastructure/feishu/test_client.py`:

```python
"""Unit tests for FeishuClient.

Mocks the lark-oapi SDK Client object — we never make real API calls.
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from src.infrastructure.feishu.client import FeishuClient, FeishuSendError


def _ok_response(message_id: str = "om_test") -> MagicMock:
    resp = MagicMock()
    resp.success.return_value = True
    resp.data = MagicMock()
    resp.data.message_id = message_id
    return resp


def _err_response(code: int = 99991663, msg: str = "boom") -> MagicMock:
    resp = MagicMock()
    resp.success.return_value = False
    resp.code = code
    resp.msg = msg
    resp.data = None
    return resp


def test_send_post_message_calls_create_with_post_payload():
    sdk = MagicMock()
    sdk.im.v1.message.create.return_value = _ok_response("om_123")

    client = FeishuClient(sdk_client=sdk)
    msg_id = client.send_post_message(
        chat_id="oc_x", title="Subject Here",
        body_lines=["line one", "line two"],
    )
    assert msg_id == "om_123"

    args, kwargs = sdk.im.v1.message.create.call_args
    req = args[0]
    # Receive id type and recipient
    assert req.params["receive_id_type"] == "chat_id"
    body = req.body
    assert body.receive_id == "oc_x"
    assert body.msg_type == "post"
    payload = json.loads(body.content)
    assert payload["zh_cn"]["title"] == "Subject Here"
    lines = payload["zh_cn"]["content"]
    assert lines == [
        [{"tag": "text", "text": "line one"}],
        [{"tag": "text", "text": "line two"}],
    ]


def test_send_post_message_raises_on_error_response():
    sdk = MagicMock()
    sdk.im.v1.message.create.return_value = _err_response(99991663, "permission denied")
    client = FeishuClient(sdk_client=sdk)
    with pytest.raises(FeishuSendError) as ei:
        client.send_post_message(chat_id="oc_x", title="t", body_lines=["b"])
    assert ei.value.code == 99991663
    assert "permission denied" in str(ei.value)
```

- [ ] **Step 3: Run tests**

```bash
uv run pytest tests/infrastructure/feishu/test_client.py -v
```

Expected: FAIL — module doesn't exist.

- [ ] **Step 4: Implement `FeishuClient`**

Create `src/infrastructure/feishu/client.py`:

```python
"""Thin wrapper around the lark-oapi SDK for sending Feishu post messages.

Phase 1 only needs `send_post_message`. Phase 2's event listener uses the SDK
WS client directly via `FeishuEventListener`.
"""
from __future__ import annotations

import json
import logging
from typing import Protocol

logger = logging.getLogger(__name__)


class FeishuSendError(RuntimeError):
    """Raised when im.message.create returns a non-success response."""

    def __init__(self, code: int | None, msg: str) -> None:
        super().__init__(f"feishu send failed: code={code} msg={msg}")
        self.code = code
        self.msg = msg


class _SdkClient(Protocol):
    """Subset of lark_oapi.Client used by FeishuClient (for test injection)."""

    @property
    def im(self): ...


def _build_post_content(title: str, body_lines: list[str]) -> str:
    """Build the JSON content envelope for msg_type=post (zh_cn locale)."""
    payload = {
        "zh_cn": {
            "title": title,
            "content": [
                [{"tag": "text", "text": line}] for line in body_lines
            ],
        }
    }
    return json.dumps(payload, ensure_ascii=False)


class FeishuClient:
    def __init__(self, *, sdk_client: _SdkClient) -> None:
        self._sdk = sdk_client

    def send_post_message(
        self,
        *,
        chat_id: str,
        title: str,
        body_lines: list[str],
    ) -> str:
        """Send a post-format message to the given chat. Returns message_id.

        Raises FeishuSendError on any non-success response.
        """
        # Imports inside the method so the module loads even if lark-oapi isn't
        # importable yet during early bootstrap diagnostics.
        from lark_oapi.api.im.v1 import (
            CreateMessageRequest,
            CreateMessageRequestBody,
        )

        req = (
            CreateMessageRequest.builder()
            .receive_id_type("chat_id")
            .request_body(
                CreateMessageRequestBody.builder()
                .receive_id(chat_id)
                .msg_type("post")
                .content(_build_post_content(title, body_lines))
                .build()
            )
            .build()
        )
        resp = self._sdk.im.v1.message.create(req)
        if not resp.success():
            raise FeishuSendError(
                code=getattr(resp, "code", None),
                msg=getattr(resp, "msg", "") or "(no msg)",
            )
        return resp.data.message_id
```

- [ ] **Step 5: Run tests**

```bash
uv run pytest tests/infrastructure/feishu/test_client.py -v
```

Expected: 2 PASS.

- [ ] **Step 6: Commit**

```bash
git add src/infrastructure/feishu/ tests/infrastructure/feishu/__init__.py tests/infrastructure/feishu/test_client.py
git commit -m "feat(feishu): FeishuClient.send_post_message wrapper around lark-oapi"
```

---

## Task 7: `EscalationNotifier`

**Files:**
- Create: `src/infrastructure/feishu/notifier.py`
- Create: `tests/infrastructure/feishu/test_notifier.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/infrastructure/feishu/test_notifier.py`:

```python
"""Unit tests for EscalationNotifier."""
from __future__ import annotations

from dataclasses import dataclass

import pytest

from src.infrastructure.audit_logger import AuditLogger
from src.infrastructure.database import Database
from src.infrastructure.feishu.client import FeishuSendError
from src.infrastructure.feishu.notifier import EscalationNotifier
from src.orchestrator.org_config import FeishuNotificationsConfig


@dataclass
class _FakeFeishuClient:
    sent: list[dict]
    next_message_id: str = "om_fake"

    def send_post_message(self, *, chat_id, title, body_lines):
        self.sent.append({"chat_id": chat_id, "title": title, "body_lines": body_lines})
        return self.next_message_id


def _cfg(chat_id: str = "oc_x") -> FeishuNotificationsConfig:
    return FeishuNotificationsConfig(
        provider="feishu", region="feishu",
        chat_id=chat_id, reply_ttl_hours=72,
    )


def _seed_task(db: Database, task_id: str = "TASK-1") -> None:
    from src.models import TaskRecord
    db.insert_task(TaskRecord(
        id=task_id,
        team="engineering",
        brief="Add Alipay support",
    ))


@pytest.mark.asyncio
async def test_notify_escalated_sends_and_audits(tmp_path):
    db = Database(tmp_path / "opc.db")
    _seed_task(db)
    fake = _FakeFeishuClient(sent=[], next_message_id="om_42")
    notifier = EscalationNotifier(
        slug="hk-macau-tourism",
        db=db,
        audit=AuditLogger(db),
        client=fake,
        config=_cfg(chat_id="oc_abc"),
    )

    await notifier.notify_escalated(
        task_id="TASK-1",
        agent="engineering_head",
        reason="Manager requested founder authority.",
        last_summary="Two delegation rounds failed.",
    )

    assert len(fake.sent) == 1
    sent = fake.sent[0]
    assert sent["chat_id"] == "oc_abc"
    assert "TASK-1" in sent["title"]
    assert "hk-macau-tourism" in sent["title"]
    body_text = "\n".join(sent["body_lines"])
    assert "engineering_head" in body_text
    assert "Add Alipay support" in body_text
    assert "Two delegation rounds failed" in body_text
    assert "Manager requested founder authority" in body_text
    assert "APPROVE" in body_text
    assert "REJECT" in body_text
    assert "opc resolve-escalation" in body_text

    row = db.get_escalation_notification("om_42")
    assert row is not None
    assert row["task_id"] == "TASK-1"
    assert row["consumed_at"] is None

    actions = [r["action"] for r in db.get_audit_logs("TASK-1")]
    assert "escalation_notify_sent" in actions


@dataclass
class _ExplodingFeishuClient:
    def send_post_message(self, *, chat_id, title, body_lines):
        raise FeishuSendError(code=99991663, msg="permission denied")


@pytest.mark.asyncio
async def test_notify_escalated_swallows_send_failure(tmp_path):
    db = Database(tmp_path / "opc.db")
    _seed_task(db)
    notifier = EscalationNotifier(
        slug="o", db=db, audit=AuditLogger(db),
        client=_ExplodingFeishuClient(), config=_cfg(),
    )
    await notifier.notify_escalated(
        task_id="TASK-1", agent="x", reason="r", last_summary="s",
    )
    actions = [r["action"] for r in db.get_audit_logs("TASK-1")]
    assert "escalation_notify_sent" not in actions
    assert "escalation_notify_failed" in actions
    # Notification row never minted (we send first, then mint).
    assert db.get_escalation_notification("om_fake") is None


@pytest.mark.asyncio
async def test_notify_escalated_missing_task_is_no_op(tmp_path):
    db = Database(tmp_path / "opc.db")
    fake = _FakeFeishuClient(sent=[])
    notifier = EscalationNotifier(
        slug="o", db=db, audit=AuditLogger(db),
        client=fake, config=_cfg(),
    )
    await notifier.notify_escalated(
        task_id="TASK-DOES-NOT-EXIST", agent="x", reason="r",
    )
    assert fake.sent == []
```

- [ ] **Step 2: Run tests**

```bash
uv run pytest tests/infrastructure/feishu/test_notifier.py -v
```

Expected: FAIL — module doesn't exist.

- [ ] **Step 3: Implement `EscalationNotifier`**

Create `src/infrastructure/feishu/notifier.py`:

```python
"""Send the founder a Feishu message when a task escalates.

Phase 1 (this module) is outbound-only and persists a correlation row keyed
by the Feishu message_id. Phase 2's listener matches inbound replies against
those rows by `root_id`.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Protocol

from src.infrastructure.audit_logger import AuditLogger
from src.infrastructure.database import Database
from src.orchestrator.org_config import FeishuNotificationsConfig

logger = logging.getLogger(__name__)


class _Sender(Protocol):
    def send_post_message(
        self, *, chat_id: str, title: str, body_lines: list[str],
    ) -> str: ...


def _build_body_phase1(
    *,
    slug: str,
    task_id: str,
    agent: str,
    team: str,
    brief: str,
    last_summary: str,
    reason: str,
    escalated_at: datetime,
) -> tuple[str, list[str]]:
    """Return (title, body_lines) for the post-format payload."""
    title = f"[OPC {slug}] {task_id} escalated — action required"
    lines = [
        f"Agent:        {agent}",
        f"Team:         {team}",
        f"Task:         {task_id}",
        f"Org:          {slug}",
        f"Escalated at: {escalated_at:%Y-%m-%d %H:%M:%S} UTC",
        "",
        "--- Brief ---",
        brief,
        "",
        "--- Last manager summary ---",
        last_summary or "(none)",
        "",
        "--- Escalation reason ---",
        reason,
        "",
        "--- To resolve ---",
        "Reply in this thread with one of:",
        "",
        "  APPROVE",
        "  <your rationale>",
        "",
        "  —or—",
        "",
        "  REJECT",
        "  <your rationale>",
        "",
        "You can also resolve via CLI:",
        f"  opc resolve-escalation --org {slug} --task-id {task_id} \\",
        "    --decision approve|reject --rationale \"...\"",
    ]
    return title, lines


class EscalationNotifier:
    def __init__(
        self,
        *,
        slug: str,
        db: Database,
        audit: AuditLogger,
        client: _Sender,
        config: FeishuNotificationsConfig,
    ) -> None:
        self._slug = slug
        self._db = db
        self._audit = audit
        self._client = client
        self._config = config

    async def notify_escalated(
        self,
        *,
        task_id: str,
        agent: str,
        reason: str,
        last_summary: str = "",
    ) -> None:
        """Send + persist + audit. Errors are caught and audited; the
        orchestration loop never sees them."""
        try:
            task = self._db.get_task(task_id)
            if task is None:
                logger.warning("notify_escalated: task %s not found", task_id)
                return
            team = task.team or ""
            brief = task.brief or ""

            now = datetime.now(timezone.utc)
            title, body_lines = _build_body_phase1(
                slug=self._slug,
                task_id=task_id,
                agent=agent,
                team=team,
                brief=brief,
                last_summary=last_summary,
                reason=reason,
                escalated_at=now,
            )
            message_id = self._client.send_post_message(
                chat_id=self._config.chat_id,
                title=title,
                body_lines=body_lines,
            )
            expires = now + timedelta(hours=self._config.reply_ttl_hours)
            self._db.mint_escalation_notification(
                feishu_message_id=message_id,
                org_slug=self._slug,
                task_id=task_id,
                chat_id=self._config.chat_id,
                expires_at=expires,
            )
            self._audit.log_escalation_notify_sent(
                task_id=task_id, feishu_message_id=message_id,
            )
        except Exception as exc:
            logger.exception("notify_escalated failed for task %s", task_id)
            try:
                self._audit.log_escalation_notify_failed(
                    task_id=task_id, error=f"{type(exc).__name__}: {exc}",
                )
            except Exception:
                logger.exception("audit log_escalation_notify_failed also failed")
```

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/infrastructure/feishu/test_notifier.py -v
```

Expected: 3 PASS.

- [ ] **Step 5: Commit**

```bash
git add src/infrastructure/feishu/notifier.py tests/infrastructure/feishu/test_notifier.py
git commit -m "feat(feishu): EscalationNotifier sends post + mints notification row"
```

---

## Task 8: `Orchestrator.attach_notifier` + `notify_escalated`

**Files:**
- Modify: `src/orchestrator/orchestrator.py`
- Test: `tests/test_orchestrator.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_orchestrator.py`:

```python
def test_orchestrator_notifier_default_none(tmp_path, test_settings):
    from src.infrastructure.database import Database
    from src.orchestrator._paths import OrgPaths
    from src.orchestrator.orchestrator import Orchestrator
    from src.orchestrator.teams import TeamsRegistry

    root = tmp_path / "orgs" / "x"
    root.mkdir(parents=True)
    db = Database(root / "opc.db")
    orch = Orchestrator(
        db=db, settings=test_settings,
        paths=OrgPaths(root=root), slug="x",
        teams=TeamsRegistry.load(root),
    )
    assert orch._notifier is None


def test_orchestrator_notify_escalated_no_op_when_unset(tmp_path, test_settings):
    from src.infrastructure.database import Database
    from src.orchestrator._paths import OrgPaths
    from src.orchestrator.orchestrator import Orchestrator
    from src.orchestrator.teams import TeamsRegistry

    root = tmp_path / "orgs" / "x"
    root.mkdir(parents=True)
    db = Database(root / "opc.db")
    orch = Orchestrator(
        db=db, settings=test_settings,
        paths=OrgPaths(root=root), slug="x",
        teams=TeamsRegistry.load(root),
    )
    # Must not raise even with no notifier attached.
    orch.notify_escalated(task_id="TASK-X", agent="a", reason="r")


def test_orchestrator_notify_does_not_block_synchronous_caller(tmp_path, test_settings):
    """When called from a thread without an event loop, notify_escalated
    must spawn a background worker rather than blocking on asyncio.run."""
    import threading
    import time

    from src.infrastructure.database import Database
    from src.orchestrator._paths import OrgPaths
    from src.orchestrator.orchestrator import Orchestrator
    from src.orchestrator.teams import TeamsRegistry

    root = tmp_path / "orgs" / "x"
    root.mkdir(parents=True)
    db = Database(root / "opc.db")
    orch = Orchestrator(
        db=db, settings=test_settings,
        paths=OrgPaths(root=root), slug="x",
        teams=TeamsRegistry.load(root),
    )

    started = threading.Event()
    finish = threading.Event()
    finished = threading.Event()

    class _SlowNotifier:
        async def notify_escalated(self, **kwargs):
            started.set()
            finish.wait(timeout=5.0)
            finished.set()

    orch.attach_notifier(_SlowNotifier())

    t0 = time.monotonic()
    orch.notify_escalated(task_id="TASK-X", agent="a", reason="r")
    elapsed = time.monotonic() - t0
    assert elapsed < 1.0, f"notify_escalated blocked for {elapsed:.2f}s"
    assert started.wait(timeout=2.0), "background notifier never ran"
    finish.set()
    assert finished.wait(timeout=2.0)
```

- [ ] **Step 2: Run tests**

```bash
uv run pytest tests/test_orchestrator.py -v -k notif
```

Expected: 3 FAIL.

- [ ] **Step 3: Add the methods**

In `src/orchestrator/orchestrator.py`, in `Orchestrator.__init__` add `self._notifier = None` near `self._queue = None`. Then add two methods near `attach_queue`/`attach_sessions`:

```python
    def attach_notifier(self, notifier) -> None:
        """Wire a notifier (mirrors attach_queue / attach_sessions)."""
        self._notifier = notifier

    def notify_escalated(
        self, *, task_id: str, agent: str, reason: str, last_summary: str = "",
    ) -> None:
        """Schedule an out-of-band notification. Fire-and-forget — the
        orchestration loop never blocks on the network round-trip and never
        sees an exception from the notifier (the notifier swallows + audits
        its own errors)."""
        if self._notifier is None:
            return
        import asyncio
        import threading
        coro_factory = lambda: self._notifier.notify_escalated(
            task_id=task_id, agent=agent, reason=reason,
            last_summary=last_summary,
        )
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            # No running loop in this thread (typical: thread-pool worker
            # driven by run_step). Spawn a daemon thread that owns its own
            # event loop so the worker thread isn't blocked.
            threading.Thread(
                target=lambda: asyncio.run(coro_factory()),
                daemon=True,
            ).start()
        else:
            loop.create_task(coro_factory())
```

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/test_orchestrator.py -v -k notif
```

Expected: 3 PASS.

- [ ] **Step 5: Commit**

```bash
git add src/orchestrator/orchestrator.py tests/test_orchestrator.py
git commit -m "feat(orchestrator): attach_notifier + non-blocking notify_escalated"
```

---

## Task 9: Hook `notify_escalated` into `run_step`

**Files:**
- Modify: `src/orchestrator/run_step.py`
- Test: `tests/orchestrator/test_run_step_notify.py` (new)

- [ ] **Step 1: Write the failing test**

Create `tests/orchestrator/test_run_step_notify.py`:

```python
"""run_step should call orch.notify_escalated on max-steps overflow."""
from __future__ import annotations

from unittest.mock import MagicMock

from src.models import BlockKind, TaskStatus
from src.orchestrator import run_step as run_step_mod


def test_max_steps_path_calls_notify_escalated():
    seen: list[dict] = []

    class _FakeOrch:
        def __init__(self):
            self._db = MagicMock()
            self._audit = MagicMock()
            self._settings = MagicMock(max_orchestration_steps=1)
            self._notifier = object()  # truthy

        def notify_escalated(self, **kwargs):
            seen.append(kwargs)

    fake = _FakeOrch()
    task = MagicMock(
        id="TASK-1", status=TaskStatus.PENDING, block_kind=None,
        cancelled_at=None, orchestration_step_count=1,
    )
    fake._db.get_task.return_value = task
    run_step_mod.run_step_impl(fake, "TASK-1")

    fake._audit.log_escalation.assert_called_once()
    assert seen, "notify_escalated was not called"
    assert seen[0]["task_id"] == "TASK-1"
    assert seen[0]["agent"] == "orchestrator"
```

- [ ] **Step 2: Run test**

```bash
uv run pytest tests/orchestrator/test_run_step_notify.py -v
```

Expected: FAIL.

- [ ] **Step 3: Add the hook calls**

In `src/orchestrator/run_step.py`:

After the max-steps `orch._audit.log_escalation(task_id, "orchestrator", reason)` line (around line 70), add:

```python
        orch.notify_escalated(
            task_id=task_id, agent="orchestrator", reason=reason,
        )
```

After the manager-`escalate` `orch._audit.log_escalation(task_id, agent, reason)` line (around line 171), add:

```python
        orch.notify_escalated(
            task_id=task_id, agent=agent, reason=reason,
            last_summary=getattr(report, "output_summary", "") or "",
        )
```

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/orchestrator/test_run_step_notify.py -v
uv run pytest tests/ -v -m "not integration"
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/orchestrator/run_step.py tests/orchestrator/test_run_step_notify.py
git commit -m "feat(run-step): notify on escalation (max-steps + manager-escalate)"
```

---

## Task 10: Wire optional notifier into `OrgState`

**Files:**
- Modify: `src/daemon/org_state.py`
- Test: `tests/daemon/test_org_state_notifier.py` (new)

- [ ] **Step 1: Write the failing tests**

Create `tests/daemon/test_org_state_notifier.py`:

```python
from __future__ import annotations

import textwrap

import pytest

from src.daemon.org_state import OrgState


def _write_cfg(root, body: str) -> None:
    cfg = root / "org" / "config.yaml"
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text(body)


@pytest.fixture
def org_root(tmp_path):
    root = tmp_path / "orgs" / "test"
    root.mkdir(parents=True)
    (root / "org").mkdir()
    return root


def test_org_state_no_feishu_block_means_no_notifier(org_root, test_settings):
    state = OrgState.load(slug="test", root=org_root, settings=test_settings)
    assert state.notifier is None


def test_org_state_disabled_means_no_notifier(org_root, test_settings):
    _write_cfg(org_root, textwrap.dedent("""
        feishu_notifications:
          enabled: false
          provider: feishu
          region: feishu
          chat_id: oc_x
    """))
    state = OrgState.load(slug="test", root=org_root, settings=test_settings)
    assert state.notifier is None


def test_org_state_enabled_no_secrets_skips(
    org_root, test_settings, monkeypatch,
):
    monkeypatch.delenv("OPC_FEISHU_APP_ID", raising=False)
    monkeypatch.delenv("OPC_FEISHU_APP_SECRET", raising=False)
    monkeypatch.delenv("OPC_FEISHU_APP_ID__TEST", raising=False)
    monkeypatch.delenv("OPC_FEISHU_APP_SECRET__TEST", raising=False)
    _write_cfg(org_root, textwrap.dedent("""
        feishu_notifications:
          enabled: true
          provider: feishu
          region: feishu
          chat_id: oc_x
    """))
    state = OrgState.load(slug="test", root=org_root, settings=test_settings)
    assert state.notifier is None


def test_org_state_enabled_with_secrets_attaches_notifier(
    org_root, test_settings, monkeypatch,
):
    monkeypatch.setenv("OPC_FEISHU_APP_ID", "cli_x")
    monkeypatch.setenv("OPC_FEISHU_APP_SECRET", "secret_x")
    _write_cfg(org_root, textwrap.dedent("""
        feishu_notifications:
          enabled: true
          provider: feishu
          region: feishu
          chat_id: oc_x
    """))
    state = OrgState.load(slug="test", root=org_root, settings=test_settings)
    assert state.notifier is not None
```

- [ ] **Step 2: Run tests**

```bash
uv run pytest tests/daemon/test_org_state_notifier.py -v
```

Expected: FAIL — `OrgState.notifier` doesn't exist.

- [ ] **Step 3: Modify `src/daemon/org_state.py`**

Replace contents with:

```python
"""Per-org runtime state: DB, queue events, sessions, teams, locks.

One ``OrgState`` per active org under ``<runtime>/orgs/<slug>/``. Constructed
once at daemon startup or lazily on ``opc orgs init <slug>``.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from pathlib import Path

import lark_oapi as lark

from src.config import Settings
from src.daemon.event_bus import EventBus
from src.daemon.sessions import SessionTracker
from src.infrastructure.audit_logger import AuditLogger
from src.infrastructure.database import Database
from src.infrastructure.feishu.client import FeishuClient
from src.infrastructure.feishu.notifier import EscalationNotifier
from src.models import BlockKind, TaskStatus
from src.orchestrator._paths import OrgPaths
from src.orchestrator.orchestrator import Orchestrator
from src.orchestrator.org_config import (
    OrgConfig,
    load_org_config,
    resolve_feishu_credentials,
)
from src.orchestrator.teams import TeamsRegistry

logger = logging.getLogger(__name__)


_REGION_TO_DOMAIN = {
    "feishu": lark.FEISHU_DOMAIN,
    "lark": lark.LARK_DOMAIN,
}


@dataclass
class OrgState:
    slug: str
    root: Path
    db: Database
    teams: TeamsRegistry
    settings: Settings
    orchestrator: Orchestrator
    notifier: EscalationNotifier | None = None
    feishu_app_id: str | None = None     # used by the listener; stored at load time
    feishu_app_secret: str | None = None
    feishu_domain: str | None = None
    feishu_chat_id: str | None = None
    sessions: SessionTracker = field(default_factory=SessionTracker)
    db_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    kb_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    teams_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    event_bus: EventBus = field(init=False)

    _TERMINAL_STATUS_TO_EVENT = {
        TaskStatus.COMPLETED: "task_complete",
        TaskStatus.FAILED: "task_failed",
    }

    def __post_init__(self) -> None:
        def loader(task_id: str) -> list[dict]:
            history: list[dict] = [
                {"type": "audit", **log}
                for log in self.db.get_audit_logs(task_id)
            ]
            task = self.db.get_task(task_id)
            terminal = self._synthesize_terminal_event(task) if task else None
            if terminal is not None:
                history.append(terminal)
            return history
        self.event_bus = EventBus(history_loader=loader)

    def _synthesize_terminal_event(self, task) -> dict | None:
        if task.status in self._TERMINAL_STATUS_TO_EVENT:
            return {
                "type": self._TERMINAL_STATUS_TO_EVENT[task.status],
                "outcome": task.status.value,
                "synthesized": True,
            }
        if task.status == TaskStatus.BLOCKED and task.block_kind == BlockKind.ESCALATED:
            return {
                "type": "task_blocked",
                "outcome": "escalated",
                "synthesized": True,
            }
        return None

    @classmethod
    def load(cls, *, slug: str, root: Path, settings: Settings) -> "OrgState":
        paths = OrgPaths(root=root)
        db = Database(paths.db_path)
        teams = TeamsRegistry.load(root)
        orchestrator = Orchestrator(
            db=db,
            settings=settings,
            paths=paths,
            slug=slug,
            teams=teams,
        )
        feishu_attrs = _build_feishu_attrs(slug=slug, paths=paths, db=db)
        if feishu_attrs and feishu_attrs["notifier"] is not None:
            orchestrator.attach_notifier(feishu_attrs["notifier"])
        return cls(
            slug=slug,
            root=root,
            db=db,
            teams=teams,
            settings=settings,
            orchestrator=orchestrator,
            notifier=feishu_attrs["notifier"] if feishu_attrs else None,
            feishu_app_id=feishu_attrs["app_id"] if feishu_attrs else None,
            feishu_app_secret=feishu_attrs["app_secret"] if feishu_attrs else None,
            feishu_domain=feishu_attrs["domain"] if feishu_attrs else None,
            feishu_chat_id=feishu_attrs["chat_id"] if feishu_attrs else None,
        )

    def close(self) -> None:
        self.db.close()


def _build_feishu_attrs(
    *, slug: str, paths: OrgPaths, db: Database,
) -> dict | None:
    """Resolve Feishu config + credentials. Returns dict with notifier (may be
    None) and the raw app_id/app_secret/domain/chat_id needed by the listener
    in Phase 2. Returns None if no Feishu block at all."""
    cfg: OrgConfig = load_org_config(paths)
    if cfg.feishu_notifications is None:
        return None

    app_id, app_secret = resolve_feishu_credentials(slug)
    if not app_id or not app_secret:
        logger.warning(
            "feishu_notifications enabled for org '%s' but "
            "OPC_FEISHU_APP_ID / SECRET are not set; skipping for this org",
            slug,
        )
        return {
            "notifier": None,
            "app_id": None, "app_secret": None,
            "domain": None, "chat_id": None,
        }

    domain = _REGION_TO_DOMAIN[cfg.feishu_notifications.region]
    sdk_client = (
        lark.Client.builder()
        .app_id(app_id)
        .app_secret(app_secret)
        .domain(domain)
        .log_level(lark.LogLevel.WARNING)
        .build()
    )
    feishu_client = FeishuClient(sdk_client=sdk_client)
    notifier = EscalationNotifier(
        slug=slug,
        db=db,
        audit=AuditLogger(db),
        client=feishu_client,
        config=cfg.feishu_notifications,
    )
    return {
        "notifier": notifier,
        "app_id": app_id,
        "app_secret": app_secret,
        "domain": domain,
        "chat_id": cfg.feishu_notifications.chat_id,
    }
```

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/daemon/test_org_state_notifier.py -v
uv run pytest tests/ -v -m "not integration"
```

Expected: 4 new PASS; full suite green.

- [ ] **Step 5: Commit**

```bash
git add src/daemon/org_state.py tests/daemon/test_org_state_notifier.py
git commit -m "feat(daemon): wire optional Feishu notifier + capture app/chat attrs"
```

---

## Task 11: Daemon recovery escalation notifies via orchestrator

**Files:**
- Modify: `src/daemon/__main__.py`
- Test: `tests/daemon/test_startup_recovery.py`

- [ ] **Step 1: Locate the recovery escalation site**

```bash
grep -n "log_escalation" src/daemon/__main__.py
```

- [ ] **Step 2: Append the failing tests**

Append to `tests/daemon/test_startup_recovery.py`:

```python
def test_sweep_calls_notify_escalated_on_in_progress_recovery(tmp_path):
    db = _seed_org(tmp_path)
    db.insert_task(TaskRecord(id="T-RECOV", brief="x"))
    db.update_task("T-RECOV", status=TaskStatus.IN_PROGRESS)

    seen: list[dict] = []

    class _FakeOrch:
        def notify_escalated(self, **kwargs):
            seen.append(kwargs)

    _sweep_on_startup(db, TaskQueue(), "test", _FakeOrch())
    assert seen and seen[0]["task_id"] == "T-RECOV"
    assert seen[0]["agent"] == "daemon"


def test_sweep_works_without_orchestrator_arg(tmp_path):
    db = _seed_org(tmp_path)
    db.insert_task(TaskRecord(id="T-BC", brief="x"))
    db.update_task("T-BC", status=TaskStatus.IN_PROGRESS)
    _sweep_on_startup(db, TaskQueue(), "test")
    assert db.get_task("T-BC").status == TaskStatus.FAILED
```

- [ ] **Step 3: Run tests**

Expected: 2 FAIL.

- [ ] **Step 4: Edit `src/daemon/__main__.py`**

- Add `from src.orchestrator.orchestrator import Orchestrator` to imports.
- Change `_sweep_on_startup` signature to:
  ```python
  def _sweep_on_startup(
      db: Database, queue: TaskQueue, slug: str,
      orchestrator: Orchestrator | None = None,
  ) -> None:
  ```
- After `audit.log_escalation(task_id, "daemon", "daemon restarted mid-task")`:
  ```python
          if orchestrator is not None:
              orchestrator.notify_escalated(
                  task_id=task_id, agent="daemon",
                  reason="daemon restarted mid-task",
              )
  ```
- Update the call site (in `_build_state`) to: `_sweep_on_startup(org.db, state.queue, org.slug, org.orchestrator)`.

- [ ] **Step 5: Run tests**

```bash
uv run pytest tests/daemon/test_startup_recovery.py -v
```

Expected: All PASS.

- [ ] **Step 6: Commit**

```bash
git add src/daemon/__main__.py tests/daemon/test_startup_recovery.py
git commit -m "feat(daemon): notify founder on recovery-escalation at startup"
```

---

## Task 12: Sample org config + setup runbook

**Files:**
- Create: `examples/orgs/hk-macau-tourism/org/config.yaml`
- Create: `docs/setup/feishu-notifications.md`

- [ ] **Step 1: Write the example config**

Create `examples/orgs/hk-macau-tourism/org/config.yaml`:

```yaml
# Optional org-level overrides. Missing file = global defaults.
# session_timeout_seconds: 1800   # uncomment to override

# Feishu notifications.
# Disabled by default in the sample tree — flip enabled: true and provide
# OPC_FEISHU_APP_ID / OPC_FEISHU_APP_SECRET in the daemon environment to
# activate. See docs/setup/feishu-notifications.md for full setup.
#
# feishu_notifications:
#   enabled: false
#   provider: feishu
#   region: feishu                       # feishu (CN) | lark (intl)
#   chat_id: oc_xxxxxxxxxxxxxxxxxxxxxx   # 1:1 group between bot and founder
#   reply_ttl_hours: 72
```

- [ ] **Step 2: Write the setup runbook**

Create `docs/setup/feishu-notifications.md`:

```markdown
# Feishu Notification Setup

This runbook walks you through enabling Feishu push notifications for
escalations in an OPC org.

## 1. Create a self-built app

1. Log in at https://open.feishu.cn (or https://open.larksuite.com for intl).
2. **Developer Console** → **Create Custom App** → "Self-built app".
3. Note the `App ID` (starts with `cli_`) and `App Secret`.

## 2. Configure permissions

Add the following scopes:

- `im:message` — read incoming messages (required for the event listener)
- `im:message:send_as_bot` — send messages as the bot
- `im:resource` — download attachments (optional but harmless)

Click **Apply for Release** if your tenant requires admin approval; consumer
Feishu accounts can self-approve.

## 3. Enable WebSocket events

1. In the app config, **Events and Callbacks** → **Event Subscription**.
2. Select **WebSocket** mode (not HTTP callback). No public URL is needed.
3. Subscribe to event: `im.message.receive_v1`.

## 4. Add the bot to a 1:1 chat

1. In the Feishu app, search for the bot by name and add it to your contacts.
2. Send the bot any message to create a chat.
3. From the bot's chat info panel, copy the `chat_id` (looks like `oc_xxxx...`).

If the chat ID isn't directly visible, use a one-shot script to look it up:

```bash
curl -X POST https://open.feishu.cn/open-apis/im/v1/chats \
  -H "Authorization: Bearer <tenant_access_token>" \
  -H "Content-Type: application/json"
```

(See lark-oapi docs for retrieving a tenant_access_token from your app id/secret.)

## 5. Configure OPC

```bash
export OPC_FEISHU_APP_ID=cli_xxxxxxxxxxxxxxxx
export OPC_FEISHU_APP_SECRET=yyyyyyyyyyyyyyyyyyyyyyyy
```

Edit `<runtime>/orgs/<slug>/org/config.yaml`:

```yaml
feishu_notifications:
  enabled: true
  provider: feishu
  region: feishu
  chat_id: oc_aaaaaaaaaaaaaaa
  reply_ttl_hours: 72
```

Restart the daemon. On startup, look for log lines like:

```
INFO src.daemon.feishu_listener: started Feishu event listener for org=<slug>
```

## 6. Test

Trigger an escalation (e.g. via `opc revisit ...` to a stuck task) and
confirm the bot posts in your chat. Reply with `APPROVE\nlooks fine` and
confirm the task transitions to `pending`.
```

- [ ] **Step 3: Run the example-tree test**

```bash
uv run pytest tests/test_examples_org_tree.py -v
```

Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add examples/orgs/hk-macau-tourism/org/config.yaml docs/setup/feishu-notifications.md
git commit -m "docs(setup): Feishu notifications runbook + example org config"
```

---

## Task 13: Phase 1 integration test (fake Feishu HTTP server)

**Files:**
- Create: `tests/integration/fake_feishu.py`
- Create: `tests/integration/test_feishu_notification_phase1.py`

- [ ] **Step 1: Write the fake server**

Create `tests/integration/fake_feishu.py`:

```python
"""Tiny FastAPI app that mimics enough of the Feishu Open Platform to test
our outbound flow. Specifically:
- POST /open-apis/auth/v3/tenant_access_token/internal
- POST /open-apis/im/v1/messages?receive_id_type=...
"""
from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request


def make_fake_feishu() -> tuple[FastAPI, dict[str, Any]]:
    state: dict[str, Any] = {
        "token_calls": 0,
        "messages": [],
    }
    app = FastAPI()

    @app.post("/open-apis/auth/v3/tenant_access_token/internal")
    async def issue_token():
        state["token_calls"] += 1
        return {
            "code": 0,
            "msg": "ok",
            "tenant_access_token": f"tat-{state['token_calls']}",
            "expire": 7200,
        }

    @app.post("/open-apis/im/v1/messages")
    async def create_message(request: Request):
        receive_id_type = request.query_params.get("receive_id_type", "")
        body = await request.json()
        msg_id = f"om_{len(state['messages']) + 1}"
        state["messages"].append({
            "receive_id_type": receive_id_type,
            "body": body,
            "message_id": msg_id,
        })
        return {
            "code": 0,
            "msg": "success",
            "data": {
                "message_id": msg_id,
                "chat_id": body.get("receive_id"),
                "msg_type": body.get("msg_type"),
            },
        }

    return app, state
```

- [ ] **Step 2: Write the integration tests**

Create `tests/integration/test_feishu_notification_phase1.py`:

```python
"""End-to-end Phase 1: a manager-`escalate` decision in run_step triggers
a Feishu send against our fake server."""
from __future__ import annotations

import socket
import threading
import time

import pytest
import uvicorn

from tests.integration.fake_feishu import make_fake_feishu


pytestmark = pytest.mark.integration


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def _run_server(app, host, port, ready):
    @app.on_event("startup")
    async def _on_startup():
        ready.set()
    config = uvicorn.Config(app, host=host, port=port, log_level="warning")
    uvicorn.Server(config).run()


@pytest.fixture
def fake_feishu():
    port = _free_port()
    app, state = make_fake_feishu()
    ready = threading.Event()
    threading.Thread(
        target=_run_server, args=(app, "127.0.0.1", port, ready), daemon=True,
    ).start()
    assert ready.wait(timeout=5.0), "fake feishu didn't start"
    yield f"http://127.0.0.1:{port}", state


def test_escalation_via_run_step_sends_feishu_message(
    fake_feishu, tmp_path, test_settings, monkeypatch,
):
    base_url, state = fake_feishu
    # Override the SDK domain to point at our fake server.
    import lark_oapi as lark
    monkeypatch.setattr(lark, "FEISHU_DOMAIN", base_url)
    monkeypatch.setenv("OPC_FEISHU_APP_ID", "cli_test")
    monkeypatch.setenv("OPC_FEISHU_APP_SECRET", "secret_test")

    root = tmp_path / "orgs" / "test"
    root.mkdir(parents=True)
    (root / "org").mkdir()
    (root / "org" / "config.yaml").write_text("""
feishu_notifications:
  enabled: true
  provider: feishu
  region: feishu
  chat_id: oc_test
""")

    from src.daemon.org_state import OrgState
    org = OrgState.load(slug="test", root=root, settings=test_settings)
    assert org.notifier is not None

    from src.models import TaskRecord
    org.db.insert_task(TaskRecord(
        id="TASK-1", team="engineering", brief="Add Alipay support",
    ))
    org.db._conn.execute(
        "UPDATE tasks SET orchestration_step_count = ? WHERE id = ?",
        (test_settings.max_orchestration_steps, "TASK-1"),
    )
    org.db._conn.commit()

    org.orchestrator.run_step("TASK-1")
    time.sleep(0.5)  # drain fire-and-forget thread

    assert state["token_calls"] >= 1
    assert len(state["messages"]) == 1
    msg = state["messages"][0]
    assert msg["receive_id_type"] == "chat_id"
    assert msg["body"]["receive_id"] == "oc_test"
    assert msg["body"]["msg_type"] == "post"
    # Body content carries the brief and reason
    import json
    payload = json.loads(msg["body"]["content"])
    body_text = payload["zh_cn"]["title"] + " " + " ".join(
        seg["text"] for line in payload["zh_cn"]["content"] for seg in line
    )
    assert "TASK-1" in body_text
    assert "Add Alipay support" in body_text

    actions = [r["action"] for r in org.db.get_audit_logs("TASK-1")]
    assert "escalation_notify_sent" in actions

    org.close()


def test_escalation_with_feishu_disabled_is_silent(
    fake_feishu, tmp_path, test_settings, monkeypatch,
):
    base_url, state = fake_feishu
    import lark_oapi as lark
    monkeypatch.setattr(lark, "FEISHU_DOMAIN", base_url)

    root = tmp_path / "orgs" / "test"
    root.mkdir(parents=True)
    (root / "org").mkdir()
    (root / "org" / "config.yaml").write_text("session_timeout_seconds: 1800\n")

    from src.daemon.org_state import OrgState
    org = OrgState.load(slug="test", root=root, settings=test_settings)
    assert org.notifier is None

    from src.models import TaskRecord
    org.db.insert_task(TaskRecord(id="TASK-2", team="engineering", brief="b"))
    org.db._conn.execute(
        "UPDATE tasks SET orchestration_step_count = ? WHERE id = ?",
        (test_settings.max_orchestration_steps, "TASK-2"),
    )
    org.db._conn.commit()

    org.orchestrator.run_step("TASK-2")
    time.sleep(0.3)

    assert state["messages"] == []
    actions = [r["action"] for r in org.db.get_audit_logs("TASK-2")]
    assert "escalation" in actions
    assert "escalation_notify_sent" not in actions

    org.close()
```

- [ ] **Step 3: Run the integration tests**

```bash
uv run pytest tests/integration/test_feishu_notification_phase1.py -v -m integration
```

Expected: 2 PASS. If lark-oapi's SDK refuses to use a non-HTTPS domain, swap `monkeypatch.setattr(lark, "FEISHU_DOMAIN", base_url)` with overriding the SDK's HTTP base via its config — see lark-oapi `Client.builder().domain(...)` signature.

- [ ] **Step 4: Commit**

```bash
git add tests/integration/fake_feishu.py tests/integration/test_feishu_notification_phase1.py
git commit -m "test(integration): Phase 1 Feishu outbound end-to-end with fake server"
```

---

## Phase 1 Complete

At this point: tasks transition to ESCALATED → daemon sends a Feishu post message → audit log records the send → notification row tracks the Feishu `message_id`. CLI fallback (`opc resolve-escalation`) still works unchanged.

---

# Phase 2 — Inbound Reply Listener

## Task 14: `reply_parser` pure function

**Files:**
- Create: `src/infrastructure/feishu/reply_parser.py`
- Create: `tests/infrastructure/feishu/test_reply_parser.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/infrastructure/feishu/test_reply_parser.py`:

```python
"""Unit tests for reply_parser — pure functions, no I/O."""
from __future__ import annotations

import json

import pytest

from src.infrastructure.feishu.reply_parser import (
    ParseResult,
    extract_text_from_content,
    parse_reply,
)


def _text_envelope(text: str) -> str:
    return json.dumps({"text": text})


def _post_envelope(lines: list[str]) -> str:
    return json.dumps({
        "zh_cn": {
            "title": "",
            "content": [
                [{"tag": "text", "text": line}] for line in lines
            ],
        }
    })


def test_extract_from_text_message():
    out = extract_text_from_content("text", _text_envelope("hello"))
    assert out == "hello"


def test_extract_from_post_message():
    out = extract_text_from_content("post", _post_envelope(["line one", "line two"]))
    assert out == "line one\nline two"


def test_extract_from_unsupported_type_returns_none():
    assert extract_text_from_content("interactive", "{}") is None
    assert extract_text_from_content("image", "{}") is None


def test_parse_reply_approve_clean():
    result = parse_reply("APPROVE\ngo for it")
    assert result == ParseResult(decision="approve", rationale="go for it")


def test_parse_reply_reject_clean():
    result = parse_reply("REJECT\nnot now")
    assert result == ParseResult(decision="reject", rationale="not now")


def test_parse_reply_lowercase_accepted():
    result = parse_reply("approve\nok")
    assert result.decision == "approve"


def test_parse_reply_mixed_case_accepted():
    assert parse_reply("Approve\nok").decision == "approve"
    assert parse_reply("Reject\nno").decision == "reject"


def test_parse_reply_multiline_rationale():
    result = parse_reply("APPROVE\nline1\nline2\nline3")
    assert result.rationale == "line1\nline2\nline3"


def test_parse_reply_decision_only_uses_default_rationale():
    result = parse_reply("APPROVE")
    assert result.decision == "approve"
    assert result.rationale == "(no rationale provided)"


def test_parse_reply_first_word_invalid_returns_none():
    assert parse_reply("MAYBE\nnot sure") is None


def test_parse_reply_empty_returns_none():
    assert parse_reply("") is None
    assert parse_reply("   \n   ") is None


def test_parse_reply_leading_blank_lines_skipped():
    result = parse_reply("\n\nAPPROVE\nfine")
    assert result.decision == "approve"
    assert result.rationale == "fine"
```

- [ ] **Step 2: Run tests**

```bash
uv run pytest tests/infrastructure/feishu/test_reply_parser.py -v
```

Expected: FAIL — module doesn't exist.

- [ ] **Step 3: Implement `reply_parser`**

Create `src/infrastructure/feishu/reply_parser.py`:

```python
"""Pure-function helpers to extract a decision from a Feishu inbound message.

The text content of a Feishu message lives inside a JSON envelope that varies
by `msg_type`. We support `text` and `post` envelopes; everything else is
considered unsupported and yields no text.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class ParseResult:
    decision: Literal["approve", "reject"]
    rationale: str


_NO_RATIONALE = "(no rationale provided)"


def extract_text_from_content(msg_type: str, content_json: str) -> str | None:
    """Convert a Feishu message envelope to plain text. Returns None for
    unsupported msg_types (image, file, interactive, ...)."""
    try:
        envelope = json.loads(content_json)
    except (TypeError, ValueError):
        return None

    if msg_type == "text":
        text = envelope.get("text")
        return text if isinstance(text, str) else None

    if msg_type == "post":
        # Feishu post envelope: {"zh_cn": {"title": "...", "content": [[seg, ...], ...]}}
        # Pick whichever locale block exists; usually zh_cn for our org.
        for locale_block in envelope.values():
            if not isinstance(locale_block, dict):
                continue
            content = locale_block.get("content")
            if not isinstance(content, list):
                continue
            lines: list[str] = []
            for line in content:
                if not isinstance(line, list):
                    continue
                segs = []
                for seg in line:
                    if isinstance(seg, dict) and seg.get("tag") == "text":
                        segs.append(seg.get("text", ""))
                lines.append("".join(segs))
            return "\n".join(lines)
        return None

    return None


def parse_reply(text: str) -> ParseResult | None:
    """Parse the founder's reply text into a decision + rationale.

    First non-empty line must be APPROVE or REJECT (case-insensitive).
    Subsequent lines (joined with \\n) form the rationale; empty rationale
    defaults to a placeholder so the resolve_escalation route accepts it.
    """
    if not text or not text.strip():
        return None

    lines = text.split("\n")
    first_idx: int | None = None
    for idx, line in enumerate(lines):
        if line.strip():
            first_idx = idx
            break
    if first_idx is None:
        return None

    decision_word = lines[first_idx].strip().upper()
    if decision_word == "APPROVE":
        decision: Literal["approve", "reject"] = "approve"
    elif decision_word == "REJECT":
        decision = "reject"
    else:
        return None

    rationale = "\n".join(lines[first_idx + 1:]).strip()
    if not rationale:
        rationale = _NO_RATIONALE
    return ParseResult(decision=decision, rationale=rationale)
```

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/infrastructure/feishu/test_reply_parser.py -v
```

Expected: All PASS.

- [ ] **Step 5: Commit**

```bash
git add src/infrastructure/feishu/reply_parser.py tests/infrastructure/feishu/test_reply_parser.py
git commit -m "feat(feishu): reply_parser — extract decision + rationale"
```

---

## Task 15: `FeishuEventListener` skeleton + handler

**Files:**
- Create: `src/daemon/feishu_listener.py`
- Create: `tests/daemon/test_feishu_listener.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/daemon/test_feishu_listener.py`:

```python
"""Unit tests for FeishuEventListener._handle_event_async.

The handler is the only piece of the listener that has logic; the WS thread
itself is treated as I/O the SDK owns. Tests construct event payload objects
that mimic lark_oapi's P2ImMessageReceiveV1 shape and invoke the handler
directly (no real WebSocket).
"""
from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.daemon.feishu_listener import FeishuEventListener
from src.infrastructure.audit_logger import AuditLogger
from src.infrastructure.database import Database


def _event(
    *,
    event_id: str = "evt_1",
    chat_id: str = "oc_target",
    root_id: str | None = "om_target",
    sender_type: str = "user",
    msg_type: str = "text",
    content: str = '{"text": "APPROVE\\nfine"}',
    msg_id: str = "om_reply",
):
    return SimpleNamespace(
        header=SimpleNamespace(event_id=event_id),
        event=SimpleNamespace(
            sender=SimpleNamespace(sender_type=sender_type),
            message=SimpleNamespace(
                message_id=msg_id,
                chat_id=chat_id,
                root_id=root_id,
                message_type=msg_type,
                content=content,
            ),
        ),
    )


def _seed_notification(
    db: Database,
    *,
    feishu_message_id: str = "om_target",
    task_id: str = "TASK-1",
    expires_at: datetime | None = None,
) -> None:
    from src.models import TaskRecord
    db.insert_task(TaskRecord(id=task_id, team="engineering", brief="b"))
    expires = expires_at or datetime.now(timezone.utc) + timedelta(hours=72)
    db.mint_escalation_notification(
        feishu_message_id=feishu_message_id,
        org_slug="o", task_id=task_id, chat_id="oc_target",
        expires_at=expires,
    )


@pytest.fixture
def listener(tmp_path):
    db = Database(tmp_path / "opc.db")
    resolve_mock = AsyncMock()
    listener = FeishuEventListener(
        slug="o", db=db, audit=AuditLogger(db),
        chat_id="oc_target",
        resolve_escalation=resolve_mock,
        loop=asyncio.get_event_loop(),
        app_id="cli_x", app_secret="s_x", domain="https://x",
    )
    return listener, db, resolve_mock


@pytest.mark.asyncio
async def test_handler_calls_resolve_on_approve(listener):
    listener_obj, db, resolve_mock = listener
    _seed_notification(db)
    await listener_obj._handle_event_async(_event())
    resolve_mock.assert_awaited_once()
    args, kwargs = resolve_mock.await_args
    assert kwargs["task_id"] == "TASK-1"
    assert kwargs["decision"] == "approve"
    assert kwargs["rationale"] == "fine"
    # Notification consumed
    row = db.get_escalation_notification("om_target")
    assert row["consumed_at"] is not None
    # Audit
    actions = [r["action"] for r in db.get_audit_logs("TASK-1")]
    assert "escalation_reply_processed" in actions


@pytest.mark.asyncio
async def test_handler_dedups_redelivered_event(listener):
    listener_obj, db, resolve_mock = listener
    _seed_notification(db)
    await listener_obj._handle_event_async(_event(event_id="evt_dup"))
    await listener_obj._handle_event_async(_event(event_id="evt_dup"))
    assert resolve_mock.await_count == 1


@pytest.mark.asyncio
async def test_handler_drops_wrong_chat(listener):
    listener_obj, db, resolve_mock = listener
    _seed_notification(db)
    await listener_obj._handle_event_async(_event(chat_id="oc_other"))
    resolve_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_handler_drops_no_root_id(listener):
    listener_obj, db, resolve_mock = listener
    _seed_notification(db)
    await listener_obj._handle_event_async(_event(root_id=None))
    resolve_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_handler_drops_app_sender(listener):
    listener_obj, db, resolve_mock = listener
    _seed_notification(db)
    await listener_obj._handle_event_async(_event(sender_type="app"))
    resolve_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_handler_drops_unknown_root(listener):
    listener_obj, db, resolve_mock = listener
    # No notification seeded; root_id won't match anything.
    await listener_obj._handle_event_async(_event())
    resolve_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_handler_drops_consumed_notification(listener):
    listener_obj, db, resolve_mock = listener
    _seed_notification(db)
    db.consume_escalation_notification("om_target", consumed_by="cli-fallback")
    await listener_obj._handle_event_async(_event())
    resolve_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_handler_drops_expired_notification(listener):
    listener_obj, db, resolve_mock = listener
    past = datetime.now(timezone.utc) - timedelta(hours=1)
    _seed_notification(db, expires_at=past)
    await listener_obj._handle_event_async(_event())
    resolve_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_handler_drops_bad_decision(listener):
    listener_obj, db, resolve_mock = listener
    _seed_notification(db)
    await listener_obj._handle_event_async(_event(
        content='{"text": "MAYBE\\nnot sure"}',
    ))
    resolve_mock.assert_not_awaited()
    actions = [r["action"] for r in db.get_audit_logs("TASK-1")]
    assert "escalation_reply_rejected" in actions


@pytest.mark.asyncio
async def test_handler_handles_post_message(listener):
    listener_obj, db, resolve_mock = listener
    _seed_notification(db)
    post_content = json.dumps({
        "zh_cn": {
            "title": "",
            "content": [
                [{"tag": "text", "text": "APPROVE"}],
                [{"tag": "text", "text": "shipping it"}],
            ],
        }
    })
    await listener_obj._handle_event_async(_event(
        msg_type="post", content=post_content,
    ))
    resolve_mock.assert_awaited_once()
    kwargs = resolve_mock.await_args.kwargs
    assert kwargs["decision"] == "approve"
    assert kwargs["rationale"] == "shipping it"
```

- [ ] **Step 2: Run tests**

```bash
uv run pytest tests/daemon/test_feishu_listener.py -v
```

Expected: FAIL — module doesn't exist.

- [ ] **Step 3: Implement `FeishuEventListener`**

Create `src/daemon/feishu_listener.py`:

```python
"""Long-lived Feishu event listener — subscribes to im.message.receive_v1
events and routes founder replies to resolve_escalation.

Architecture:
- One listener per org with feishu_notifications enabled.
- WS connection runs in a daemon thread (the lark-oapi SDK's start() is blocking).
- Inbound events are bridged from the WS thread to the asyncio loop via
  asyncio.run_coroutine_threadsafe; actual logic runs on the daemon's loop.
"""
from __future__ import annotations

import asyncio
import logging
import threading
from datetime import datetime, timezone
from typing import Awaitable, Callable

import lark_oapi as lark

from src.infrastructure.audit_logger import AuditLogger
from src.infrastructure.database import Database
from src.infrastructure.feishu.reply_parser import (
    extract_text_from_content,
    parse_reply,
)

logger = logging.getLogger(__name__)


# Type for the resolve_escalation callable — either the route handler bound
# to the org, or a test stub. Awaits a coroutine that performs the transition.
ResolveFn = Callable[..., Awaitable[None]]


class FeishuEventListener:
    def __init__(
        self,
        *,
        slug: str,
        db: Database,
        audit: AuditLogger,
        chat_id: str,
        resolve_escalation: ResolveFn,
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
        self._loop = loop
        self._app_id = app_id
        self._app_secret = app_secret
        self._domain = domain
        self._ws_client: lark.ws.Client | None = None
        self._thread: threading.Thread | None = None

    # ---- Lifecycle ----

    def start(self) -> None:
        """Construct the WS client and start it in a daemon thread."""
        handler = (
            lark.EventDispatcherHandler.builder("", "")
            .register_p2_im_message_receive_v1(self._on_message_event)
            .build()
        )
        self._ws_client = lark.ws.Client(
            self._app_id,
            self._app_secret,
            domain=self._domain,
            event_handler=handler,
            log_level=lark.LogLevel.WARNING,
        )

        def _run():
            try:
                self._ws_client.start()
            except Exception:
                logger.exception("Feishu WS client crashed (org=%s)", self._slug)

        self._thread = threading.Thread(
            target=_run, daemon=True, name=f"feishu-ws-{self._slug}",
        )
        self._thread.start()
        logger.info("started Feishu event listener for org=%s", self._slug)

    # ---- WS thread -> asyncio bridge ----

    def _on_message_event(self, data) -> None:  # called in WS thread
        try:
            asyncio.run_coroutine_threadsafe(
                self._handle_event_async(data),
                self._loop,
            )
        except Exception:
            logger.exception("failed to schedule event for org=%s", self._slug)

    # ---- Async handler ----

    async def _handle_event_async(self, data) -> None:
        try:
            event_id = data.header.event_id
            msg = data.event.message

            # 1. Dedup — first writer wins; redelivery silently dropped.
            if not self._db.record_processed_event(
                org_slug=self._slug, feishu_event_id=event_id,
                outcome="pending", reason=None,
            ):
                return

            # 2. Chat filter
            if msg.chat_id != self._chat_id:
                self._db.record_processed_event(
                    org_slug=self._slug, feishu_event_id=f"{event_id}.outcome",
                    outcome="ignored", reason="wrong_chat",
                )
                return

            # 3. Threading filter
            if not msg.root_id:
                return

            # 4. Sender filter
            if data.event.sender.sender_type != "user":
                return

            # 5. Notification lookup
            row = self._db.get_escalation_notification(msg.root_id)
            if row is None:
                return
            if row["consumed_at"] is not None:
                return
            expires_at = datetime.fromisoformat(row["expires_at"])
            if datetime.now(timezone.utc) >= expires_at:
                return

            # 6. Parse text
            text = extract_text_from_content(msg.message_type, msg.content)
            if text is None:
                return
            parsed = parse_reply(text)
            if parsed is None:
                self._audit.log_escalation_reply_rejected(
                    task_id=row["task_id"], reason="bad_decision",
                )
                return

            # 7. Apply
            await self._resolve_escalation(
                slug=self._slug,
                task_id=row["task_id"],
                decision=parsed.decision,
                rationale=parsed.rationale,
            )
            self._db.consume_escalation_notification(
                msg.root_id, consumed_by="feishu-reply",
            )
            self._audit.log_escalation_reply_processed(
                task_id=row["task_id"],
                decision=parsed.decision,
                rationale=parsed.rationale,
            )
        except Exception:
            logger.exception("event handler error (org=%s)", self._slug)
```

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/daemon/test_feishu_listener.py -v
```

Expected: All 10 PASS.

- [ ] **Step 5: Commit**

```bash
git add src/daemon/feishu_listener.py tests/daemon/test_feishu_listener.py
git commit -m "feat(feishu): FeishuEventListener — WS thread + async event handler"
```

---

## Task 16: Wire listeners into daemon lifespan

**Files:**
- Modify: `src/daemon/app.py` (or wherever the FastAPI lifespan lives)
- Modify: `src/daemon/state.py`
- Modify: `src/daemon/org_state.py` (add `feishu_listener` field)
- Test: smoke test ensuring listeners start at lifespan startup

- [ ] **Step 1: Locate the lifespan**

```bash
grep -n "lifespan\|attach_queue\|TaskQueue\|state.queue" src/daemon/app.py src/daemon/state.py | head -20
```

- [ ] **Step 2: Add `feishu_listener` field to `OrgState`**

In `src/daemon/org_state.py` add:

```python
    feishu_listener: "FeishuEventListener | None" = None
```

(Use a forward-reference string literal to avoid an import cycle, or import at top of file.)

- [ ] **Step 3: Build the listener on lifespan startup**

In the FastAPI lifespan, after the existing per-org wiring:

```python
async def _start_feishu_listeners(state: DaemonState) -> None:
    """For each org with full Feishu config, construct and start a listener."""
    from src.daemon.feishu_listener import FeishuEventListener
    from src.daemon.routes.tasks import resolve_escalation_in_process
    loop = asyncio.get_running_loop()
    for org in state.orgs.values():
        if (
            org.feishu_app_id is None or org.feishu_app_secret is None
            or org.feishu_chat_id is None or org.feishu_domain is None
        ):
            continue
        listener = FeishuEventListener(
            slug=org.slug,
            db=org.db,
            audit=AuditLogger(org.db),
            chat_id=org.feishu_chat_id,
            resolve_escalation=lambda **kw, _org=org: resolve_escalation_in_process(_org, **kw),
            loop=loop,
            app_id=org.feishu_app_id,
            app_secret=org.feishu_app_secret,
            domain=org.feishu_domain,
        )
        listener.start()
        org.feishu_listener = listener
```

Call `_start_feishu_listeners(state)` from the FastAPI lifespan startup hook (alongside whatever already runs there). On shutdown, listeners are abandoned (daemon threads die with the process) — no explicit teardown.

- [ ] **Step 4: Extract `resolve_escalation_in_process` helper**

In `src/daemon/routes/tasks.py`, refactor the existing `resolve_escalation` route at line 305 so its core logic lives in a callable function that the listener can also use. Add this helper above the existing route handler:

```python
async def resolve_escalation_in_process(
    org: "OrgState",
    state: "DaemonState",
    *,
    task_id: str,
    decision: str,
    rationale: str,
) -> str:
    """Same DB transition / audit / queue re-enqueue as the HTTP handler at
    POST /tasks/{task_id}/resolve-escalation. Reused by the Feishu listener.

    Returns the new task status value (e.g. "pending" or "failed").
    Raises HTTPException for the same validation failures the route raises so
    the HTTP wrapper just re-raises.
    """
    from src.infrastructure.audit_logger import AuditLogger
    from src.models import BlockKind, TaskStatus
    from src.orchestrator.run_step import _enqueue_parent_if_waiting

    if not rationale.strip():
        raise HTTPException(status_code=400, detail={"code": "rationale_required"})
    if decision not in ("approve", "reject"):
        raise HTTPException(status_code=400, detail={"code": "invalid_decision"})
    task = org.db.get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail=f"task {task_id} not found")
    if task.status != TaskStatus.BLOCKED or task.block_kind != BlockKind.ESCALATED:
        raise HTTPException(
            status_code=409,
            detail={"code": "task_not_escalated", "current_status": task.status.value},
        )

    resolved_note = f"Founder {decision}d: {rationale}"
    async with org.db_lock:
        new_status = TaskStatus.PENDING if decision == "approve" else TaskStatus.FAILED
        org.db.update_task(
            task_id, status=new_status, block_kind=None, note=resolved_note,
        )
        AuditLogger(org.db).log_escalation_resolved(
            task_id=task_id, decision=decision, rationale=rationale,
        )

    if decision == "approve":
        if state.queue is not None:
            state.queue.put_nowait(org.slug, task_id)
    else:
        _enqueue_parent_if_waiting(org.orchestrator, task_id)

    return new_status.value
```

Then replace the existing `resolve_escalation` route body with a thin wrapper that calls the helper:

```python
@router.post("/tasks/{task_id}/resolve-escalation")
async def resolve_escalation(
    task_id: str, body: ResolveEscalationBody, org: OrgDep, request: Request,
) -> dict:
    state: DaemonState = request.app.state.daemon
    new_status = await resolve_escalation_in_process(
        org, state,
        task_id=task_id, decision=body.decision, rationale=body.rationale,
    )
    return {"ok": True, "task_id": task_id, "new_status": new_status}
```

For the Feishu listener's use, the lifespan code passes a wrapper that handles the case where validation fails (it should not raise into the listener — the listener should log and audit instead):

```python
async def resolve_for_listener(_org=org, _state=state, **kw):
    try:
        await resolve_escalation_in_process(_org, _state, **kw)
    except HTTPException as exc:
        logger.warning(
            "resolve_escalation_in_process rejected reply for task %s: %s",
            kw.get("task_id"), exc.detail,
        )
```

Run `uv run pytest tests/daemon/test_routes_tasks.py -v` after this refactor to ensure the existing route tests still pass — the wrapper preserves the same external behavior.

- [ ] **Step 5: Smoke test**

Add a smoke test that builds an `OrgState` with feishu enabled and verifies `_start_feishu_listeners` constructs (but doesn't actually connect) a listener. Use a mock for the WS thread start.

- [ ] **Step 6: Run full unit suite**

```bash
uv run pytest tests/ -v -m "not integration"
```

Expected: All green.

- [ ] **Step 7: Commit**

```bash
git add src/daemon/app.py src/daemon/state.py src/daemon/org_state.py src/daemon/routes/tasks.py tests/
git commit -m "feat(daemon): start FeishuEventListener per org on lifespan startup"
```

---

## Task 17: Final regression sweep + docs

- [ ] **Step 1: Full unit + integration suite**

```bash
uv run pytest tests/ -v -m ""
```

Expected: All green.

- [ ] **Step 2: Update CLAUDE.md**

Add a one-line reference under the Tech Stack > Database bullet:

```diff
- per-org under `<runtime>/orgs/<slug>/opc.db`. Per-session token usage rows live in `session_token_usage` (one per successful subprocess); see `docs/superpowers/specs/2026-05-05-token-usage-tracking-design.md`.
+ per-org under `<runtime>/orgs/<slug>/opc.db`. Per-session token usage rows live in `session_token_usage`; see `docs/superpowers/specs/2026-05-05-token-usage-tracking-design.md`. Per-escalation Feishu correlation rows live in `escalation_notifications`; see `docs/superpowers/specs/2026-05-08-feishu-notification-design.md`.
```

- [ ] **Step 3: Commit**

```bash
git add CLAUDE.md
git commit -m "docs(claude.md): reference escalation_notifications table"
```

---

## Phase 2 Complete

What ships:
- Per-org Feishu push notifications when tasks escalate (Phase 1).
- Per-org WebSocket-subscribed event listener that parses founder replies in-thread and resolves escalations in-process (Phase 2).
- Existing CLI `opc resolve-escalation` continues to work as a fallback.
- Threading model: notification's `feishu_message_id` is the correlation key; reply's `root_id` matches.

What does NOT ship (deferred):
- Interactive cards (`msg_type=interactive`) with APPROVE / REJECT buttons.
- Message read receipts / reaction-based acknowledgements.
- Per-token cleanup cron.
- Multi-channel future-proofing beyond the seam at `EscalationNotifier`.
