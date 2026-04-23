# Talk-Path `manage-agent` Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the Engineering Head call `opc manage-agent` (enroll / update / terminate) from inside an active talk session, authenticated via `talk_id` instead of `(task_id, session_id)`. Founder approval via `opc approve-agent` stays unchanged.

**Architecture:** Extend `ManageAgentBody` with an optional `talk_id` that is mutually exclusive with `(task_id, session_id)`. Replace the hardcoded `SessionTracker` check in `POST /agents/manage` with an auth helper that branches on which pair is supplied: task path uses the existing `SessionTracker.get_active` lookup, talk path looks up the talk in the DB and verifies it is open and owned by `engineering_head`. Add `AuditLogger.log_agent_managed` so every action leaves a scoped audit trail (task path uses `task_id`; talk path uses `talk_id` — `audit_log.task_id` already doubles as a generic scope id, see comment at `src/infrastructure/audit_logger.py:173`). Skill docs get dual-payload examples and a narrow carve-out in the talk skill's prohibition list.

**Tech Stack:** Python 3.11+, FastAPI, Pydantic v2, SQLite (WAL), `uv run pytest`, FastAPI TestClient.

---

## Spec

Conversation-driven design (this file is the authoritative spec). Decisions:

- **DECIDE #1:** All three actions (enroll, update, terminate) work on the talk path.
- **DECIDE #2:** Every talk-initiated management action is recorded (a) in the audit log with `source: "talk"` and (b) in the talk transcript via an agent-side instruction in `manage-agent/SKILL.md` (the transcript file is only written at `/talk end`, so this is a skill-level obligation, not a daemon write).

---

## File plan

### New files

None.

### Modified files

- `src/daemon/routes/agents.py` — extend `ManageAgentBody` (add `talk_id`, mutually-exclusive validator), refactor auth check into a module-level helper, call audit logger on every successful action.
- `src/infrastructure/audit_logger.py` — add `log_agent_managed(scope_id, agent, action, name, source)`.
- `tests/daemon/test_routes_agents.py` — add talk-path tests + audit-log assertions.
- `protocol/skills/manage-agent/SKILL.md` — document dual payload shape + transcript-recording rule.
- `protocol/skills/talk/SKILL.md` — amend "What NOT to do" prohibition to carve out `opc manage-agent`.
- `CLAUDE.md` — update the `opc manage-agent` references to mention dual auth path.

---

## Task 1: Extend `ManageAgentBody` with `talk_id` + mutual-exclusion validator

**Files:**
- Modify: `src/daemon/routes/agents.py:61-69` (body model)
- Test: `tests/daemon/test_routes_agents.py` (append new tests)

- [ ] **Step 1: Write the failing test — accepts talk_id alone**

Append to `tests/daemon/test_routes_agents.py`:

```python
def test_manage_agent_body_accepts_talk_id_alone() -> None:
    """talk_id alone (no task_id/session_id) validates."""
    from src.daemon.routes.agents import ManageAgentBody

    body = ManageAgentBody(
        action="enroll",
        name="content_writer",
        talk_id="TALK-007",
        description="desc",
        system_prompt="prompt",
    )
    assert body.talk_id == "TALK-007"
    assert body.task_id is None
    assert body.session_id is None


def test_manage_agent_body_accepts_task_and_session() -> None:
    """(task_id + session_id) still validates."""
    from src.daemon.routes.agents import ManageAgentBody

    body = ManageAgentBody(
        action="enroll",
        name="content_writer",
        task_id="TASK-100",
        session_id="sess-eh",
        description="desc",
        system_prompt="prompt",
    )
    assert body.task_id == "TASK-100"
    assert body.talk_id is None


def test_manage_agent_body_rejects_both_paths() -> None:
    """Supplying both task/session and talk_id is a validation error."""
    import pytest
    from pydantic import ValidationError
    from src.daemon.routes.agents import ManageAgentBody

    with pytest.raises(ValidationError):
        ManageAgentBody(
            action="enroll",
            name="content_writer",
            task_id="TASK-100",
            session_id="sess-eh",
            talk_id="TALK-007",
            description="desc",
            system_prompt="prompt",
        )


def test_manage_agent_body_rejects_neither_path() -> None:
    """Supplying neither is a validation error."""
    import pytest
    from pydantic import ValidationError
    from src.daemon.routes.agents import ManageAgentBody

    with pytest.raises(ValidationError):
        ManageAgentBody(
            action="enroll",
            name="content_writer",
            description="desc",
            system_prompt="prompt",
        )


def test_manage_agent_body_rejects_partial_task_path() -> None:
    """task_id without session_id (or vice versa) is a validation error."""
    import pytest
    from pydantic import ValidationError
    from src.daemon.routes.agents import ManageAgentBody

    with pytest.raises(ValidationError):
        ManageAgentBody(
            action="enroll",
            name="content_writer",
            task_id="TASK-100",
            description="desc",
            system_prompt="prompt",
        )
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/daemon/test_routes_agents.py -k "manage_agent_body" -v`
Expected: FAIL (task_id is currently required and no talk_id field exists).

- [ ] **Step 3: Implement — update `ManageAgentBody`**

Replace `src/daemon/routes/agents.py:61-69` with:

```python
class ManageAgentBody(BaseModel):
    action: ManageAgentAction
    name: str
    task_id: str | None = None
    session_id: str | None = None
    talk_id: str | None = None
    description: str | None = None
    system_prompt: str | None = None
    repos: dict[str, str] | None = None
    executor: str | None = None

    @model_validator(mode="after")
    def _exactly_one_auth_path(self) -> ManageAgentBody:
        task_path = self.task_id is not None and self.session_id is not None
        partial_task = (self.task_id is not None) != (self.session_id is not None)
        talk_path = self.talk_id is not None
        if partial_task:
            raise ValueError("task_id and session_id must be supplied together")
        if task_path and talk_path:
            raise ValueError("supply either (task_id + session_id) or talk_id, not both")
        if not task_path and not talk_path:
            raise ValueError("supply either (task_id + session_id) or talk_id")
        return self
```

Add the import at the top of the file (next to other pydantic imports):

```python
from pydantic import BaseModel, model_validator
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/daemon/test_routes_agents.py -k "manage_agent_body" -v`
Expected: 5 PASS.

- [ ] **Step 5: Run the whole manage-agent test group to confirm no regression**

Run: `uv run pytest tests/daemon/test_routes_agents.py -k "manage_agent" -v`
Expected: all existing tests still PASS (task-path payloads are unchanged).

- [ ] **Step 6: Commit**

```bash
git add src/daemon/routes/agents.py tests/daemon/test_routes_agents.py
git commit -m "feat(manage-agent): accept talk_id as alternative auth path

Extends ManageAgentBody with an optional talk_id that is mutually
exclusive with (task_id, session_id). Pure schema change; route
still requires the task path — talk path is wired up in a follow-up."
```

---

## Task 2: Add auth helper that branches on task_id vs talk_id

**Files:**
- Modify: `src/daemon/routes/agents.py` (add helper above the route)
- Test: `tests/daemon/test_routes_agents.py` (append new tests)

- [ ] **Step 1: Write the failing test — talk path success**

Append to `tests/daemon/test_routes_agents.py`:

```python
def test_require_eh_auth_talk_path_success(
    tmp_home, daemon_state,
) -> None:
    """Helper returns None for an open EH talk."""
    from src.daemon.routes.agents import ManageAgentBody, _require_eh_auth
    from src.models import TalkRecord

    daemon_state.db.insert_talk(
        TalkRecord(id="TALK-042", agent_name="engineering_head"),
    )

    body = ManageAgentBody(
        action="enroll",
        name="content_writer",
        talk_id="TALK-042",
        description="desc",
        system_prompt="prompt",
    )
    _require_eh_auth(body, daemon_state)  # no raise


def test_require_eh_auth_talk_path_wrong_agent_raises_403(
    tmp_home, daemon_state,
) -> None:
    """Talk owned by another agent is rejected."""
    import pytest
    from fastapi import HTTPException
    from src.daemon.routes.agents import ManageAgentBody, _require_eh_auth
    from src.models import TalkRecord

    daemon_state.db.insert_talk(
        TalkRecord(id="TALK-050", agent_name="dev_agent"),
    )

    body = ManageAgentBody(
        action="enroll",
        name="content_writer",
        talk_id="TALK-050",
        description="desc",
        system_prompt="prompt",
    )
    with pytest.raises(HTTPException) as ex:
        _require_eh_auth(body, daemon_state)
    assert ex.value.status_code == 403


def test_require_eh_auth_talk_path_closed_talk_raises_403(
    tmp_home, daemon_state,
) -> None:
    """Closed talk is rejected."""
    import pytest
    from fastapi import HTTPException
    from src.daemon.routes.agents import ManageAgentBody, _require_eh_auth
    from src.models import TalkRecord, TalkStatus

    daemon_state.db.insert_talk(
        TalkRecord(
            id="TALK-060",
            agent_name="engineering_head",
            status=TalkStatus.CLOSED,
        ),
    )

    body = ManageAgentBody(
        action="enroll",
        name="content_writer",
        talk_id="TALK-060",
        description="desc",
        system_prompt="prompt",
    )
    with pytest.raises(HTTPException) as ex:
        _require_eh_auth(body, daemon_state)
    assert ex.value.status_code == 403


def test_require_eh_auth_talk_path_missing_talk_raises_404(
    tmp_home, daemon_state,
) -> None:
    """Unknown talk_id is 404."""
    import pytest
    from fastapi import HTTPException
    from src.daemon.routes.agents import ManageAgentBody, _require_eh_auth

    body = ManageAgentBody(
        action="enroll",
        name="content_writer",
        talk_id="TALK-999",
        description="desc",
        system_prompt="prompt",
    )
    with pytest.raises(HTTPException) as ex:
        _require_eh_auth(body, daemon_state)
    assert ex.value.status_code == 404


def test_require_eh_auth_task_path_success(
    tmp_home, daemon_state,
) -> None:
    """Helper returns None for a live EH task session."""
    from src.daemon.routes.agents import ManageAgentBody, _require_eh_auth

    daemon_state.sessions.set_active("TASK-100", "engineering_head", "sess-eh")

    body = ManageAgentBody(
        action="enroll",
        name="content_writer",
        task_id="TASK-100",
        session_id="sess-eh",
        description="desc",
        system_prompt="prompt",
    )
    _require_eh_auth(body, daemon_state)  # no raise


def test_require_eh_auth_task_path_unknown_session_raises_403(
    tmp_home, daemon_state,
) -> None:
    """Unknown (task_id, eh) pair is 403."""
    import pytest
    from fastapi import HTTPException
    from src.daemon.routes.agents import ManageAgentBody, _require_eh_auth

    body = ManageAgentBody(
        action="enroll",
        name="content_writer",
        task_id="TASK-404",
        session_id="sess-ghost",
        description="desc",
        system_prompt="prompt",
    )
    with pytest.raises(HTTPException) as ex:
        _require_eh_auth(body, daemon_state)
    assert ex.value.status_code == 403


def test_require_eh_auth_task_path_wrong_session_raises_403(
    tmp_home, daemon_state,
) -> None:
    """Mismatched session_id is 403."""
    import pytest
    from fastapi import HTTPException
    from src.daemon.routes.agents import ManageAgentBody, _require_eh_auth

    daemon_state.sessions.set_active("TASK-100", "engineering_head", "sess-real")

    body = ManageAgentBody(
        action="enroll",
        name="content_writer",
        task_id="TASK-100",
        session_id="sess-stale",
        description="desc",
        system_prompt="prompt",
    )
    with pytest.raises(HTTPException) as ex:
        _require_eh_auth(body, daemon_state)
    assert ex.value.status_code == 403
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/daemon/test_routes_agents.py -k "require_eh_auth" -v`
Expected: ImportError / AttributeError — `_require_eh_auth` does not exist.

- [ ] **Step 3: Implement — add the helper**

Insert into `src/daemon/routes/agents.py` above the `manage_agent` route (roughly after the existing `_require_active` helper at line 75-80):

```python
def _require_eh_auth(body: ManageAgentBody, state: DaemonState) -> None:
    """Validate the caller is authorized to run manage-agent as EH.

    Supports two auth paths:
      - Task path: (task_id, session_id) must map to an active
        engineering_head session in SessionTracker.
      - Talk path: talk_id must reference an open talk whose
        agent_name == 'engineering_head'.

    The pydantic validator on ManageAgentBody guarantees exactly one path
    is set, so this function only checks the path that is present.
    """
    if body.talk_id is not None:
        talk = state.db.get_talk(body.talk_id)
        if talk is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"talk {body.talk_id!r} not found",
            )
        if talk.agent_name != "engineering_head":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="manage-agent requires an engineering_head talk",
            )
        if talk.status != TalkStatus.OPEN:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"talk {body.talk_id!r} is {talk.status.value}, not open",
            )
        return

    # Task path
    expected = state.sessions.get_active(body.task_id, "engineering_head")
    if expected is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="manage-agent requires an active engineering_head session",
        )
    if expected != body.session_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="session_id does not match the active engineering_head session",
        )
```

Add imports at the top of `src/daemon/routes/agents.py` if not already present:

```python
from src.models import TalkStatus
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/daemon/test_routes_agents.py -k "require_eh_auth" -v`
Expected: 7 PASS.

- [ ] **Step 5: Commit**

```bash
git add src/daemon/routes/agents.py tests/daemon/test_routes_agents.py
git commit -m "feat(manage-agent): add _require_eh_auth helper with talk path

Extracts the EH authorization check into a dedicated helper that
branches on which auth path the ManageAgentBody carries. Not wired
into the route yet — follow-up task flips the call site."
```

---

## Task 3: Wire the auth helper into the `/agents/manage` route

**Files:**
- Modify: `src/daemon/routes/agents.py:232-243` (route auth block)
- Test: `tests/daemon/test_routes_agents.py` (append new tests)

- [ ] **Step 1: Write the failing test — talk path enroll**

Append to `tests/daemon/test_routes_agents.py`:

```python
def _seed_eh_talk(daemon_state, talk_id: str = "TALK-700") -> str:
    """Helper: insert an open EH talk and return its id."""
    from src.models import TalkRecord

    daemon_state.db.insert_talk(
        TalkRecord(id=talk_id, agent_name="engineering_head"),
    )
    return talk_id


def test_manage_agent_talk_path_enroll_creates_pending(
    tmp_home, app, daemon_state, auth_headers,
) -> None:
    talk_id = _seed_eh_talk(daemon_state)
    r = TestClient(app).post(
        "/api/v1/agents/manage",
        json={
            "action": "enroll",
            "name": "content_writer",
            "talk_id": talk_id,
            "description": "Writes destination guides",
            "system_prompt": "You are the Content Writer...",
            "executor": "codex",
        },
        headers=auth_headers,
    )
    assert r.status_code == 200
    assert r.json()["status"] == "pending"
    e = daemon_state.db.get_enrollment("content_writer")
    assert e is not None
    assert e["status"] == "pending"
    assert e["executor"] == "codex"


def test_manage_agent_talk_path_update_changes_prompt(
    tmp_home, app, daemon_state, auth_headers,
) -> None:
    talk_id = _seed_eh_talk(daemon_state, "TALK-701")
    daemon_state.db.insert_enrollment("content_writer", "desc", "old prompt")
    daemon_state.db.update_enrollment_status("content_writer", "approved")
    workspace = daemon_state.runtime.workspaces_dir / "content_writer"
    workspace.mkdir(parents=True)

    with patch("src.daemon.routes.agents.ContextBuilder") as MockCB:
        mock_ctx = MockCB.return_value
        mock_ctx.ensure_workspace_ready.return_value = None
        r = TestClient(app).post(
            "/api/v1/agents/manage",
            json={
                "action": "update",
                "name": "content_writer",
                "talk_id": talk_id,
                "system_prompt": "new prompt via talk",
            },
            headers=auth_headers,
        )
    assert r.status_code == 200
    assert daemon_state.db.get_enrollment("content_writer")["system_prompt"] == "new prompt via talk"


def test_manage_agent_talk_path_terminate_removes_workspace(
    tmp_home, app, daemon_state, auth_headers,
) -> None:
    talk_id = _seed_eh_talk(daemon_state, "TALK-702")
    daemon_state.db.insert_enrollment("content_writer", "desc", "prompt")
    daemon_state.db.update_enrollment_status("content_writer", "approved")
    workspace = daemon_state.runtime.workspaces_dir / "content_writer"
    workspace.mkdir(parents=True)
    (workspace / "CLAUDE.md").write_text("# test")

    r = TestClient(app).post(
        "/api/v1/agents/manage",
        json={
            "action": "terminate",
            "name": "content_writer",
            "talk_id": talk_id,
        },
        headers=auth_headers,
    )
    assert r.status_code == 200
    assert not workspace.exists()
    assert daemon_state.db.get_enrollment("content_writer")["status"] == "terminated"


def test_manage_agent_talk_path_non_eh_talk_returns_403(
    tmp_home, app, daemon_state, auth_headers,
) -> None:
    from src.models import TalkRecord

    daemon_state.db.insert_talk(
        TalkRecord(id="TALK-703", agent_name="dev_agent"),
    )
    r = TestClient(app).post(
        "/api/v1/agents/manage",
        json={
            "action": "enroll",
            "name": "content_writer",
            "talk_id": "TALK-703",
            "description": "desc",
            "system_prompt": "prompt",
        },
        headers=auth_headers,
    )
    assert r.status_code == 403


def test_manage_agent_talk_path_closed_talk_returns_403(
    tmp_home, app, daemon_state, auth_headers,
) -> None:
    from src.models import TalkRecord, TalkStatus

    daemon_state.db.insert_talk(
        TalkRecord(
            id="TALK-704",
            agent_name="engineering_head",
            status=TalkStatus.CLOSED,
        ),
    )
    r = TestClient(app).post(
        "/api/v1/agents/manage",
        json={
            "action": "enroll",
            "name": "content_writer",
            "talk_id": "TALK-704",
            "description": "desc",
            "system_prompt": "prompt",
        },
        headers=auth_headers,
    )
    assert r.status_code == 403


def test_manage_agent_talk_path_missing_talk_returns_404(
    tmp_home, app, daemon_state, auth_headers,
) -> None:
    r = TestClient(app).post(
        "/api/v1/agents/manage",
        json={
            "action": "enroll",
            "name": "content_writer",
            "talk_id": "TALK-DOES-NOT-EXIST",
            "description": "desc",
            "system_prompt": "prompt",
        },
        headers=auth_headers,
    )
    assert r.status_code == 404


def test_manage_agent_both_auth_paths_returns_422(
    tmp_home, app, daemon_state, auth_headers,
) -> None:
    _activate_eh_session(daemon_state)
    _seed_eh_talk(daemon_state, "TALK-705")
    r = TestClient(app).post(
        "/api/v1/agents/manage",
        json={
            "action": "enroll",
            "name": "content_writer",
            "task_id": _EH_TASK,
            "session_id": _EH_SESSION,
            "talk_id": "TALK-705",
            "description": "desc",
            "system_prompt": "prompt",
        },
        headers=auth_headers,
    )
    assert r.status_code == 422
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/daemon/test_routes_agents.py -k "talk_path" -v`
Expected: the talk-path success tests return 403 or 422 (route still uses the old hardcoded auth) and missing-talk returns 403 instead of 404.

- [ ] **Step 3: Implement — swap the route's auth block for the helper**

Replace `src/daemon/routes/agents.py:232-243`:

```python
    # Only the Engineering Head may manage agents.
    expected = state.sessions.get_active(body.task_id, "engineering_head")
    if expected is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="manage-agent requires an active engineering_head session",
        )
    if expected != body.session_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="session_id does not match the active engineering_head session",
        )
```

with:

```python
    # Only the Engineering Head may manage agents (either via task session or open talk).
    _require_eh_auth(body, state)
```

- [ ] **Step 4: Run the new tests to verify they pass**

Run: `uv run pytest tests/daemon/test_routes_agents.py -k "talk_path or both_auth_paths" -v`
Expected: 7 PASS.

- [ ] **Step 5: Run the full manage-agent test group to confirm no regression**

Run: `uv run pytest tests/daemon/test_routes_agents.py -k "manage_agent" -v`
Expected: all PASS (existing task-path tests still work).

- [ ] **Step 6: Commit**

```bash
git add src/daemon/routes/agents.py tests/daemon/test_routes_agents.py
git commit -m "feat(manage-agent): accept talk_id in POST /agents/manage

Route now calls _require_eh_auth, which accepts either a live EH
task session or an open EH talk. All three actions (enroll, update,
terminate) work on both paths. Founder approval via
opc approve-agent is unchanged."
```

---

## Task 4: Add audit logging for every manage-agent action

**Files:**
- Modify: `src/infrastructure/audit_logger.py` (append new method)
- Modify: `src/daemon/routes/agents.py` (call the logger on each action)
- Test: `tests/daemon/test_routes_agents.py` (append new tests)

- [ ] **Step 1: Write the failing test — task-path enrollment logs audit entry**

Append to `tests/daemon/test_routes_agents.py` (uses the existing public `db.get_audit_logs(scope_id)` query method, which is what `tests/test_audit_logger.py` uses):

```python
def test_manage_agent_task_path_writes_audit_entry(
    tmp_home, app, daemon_state, auth_headers,
) -> None:
    _activate_eh_session(daemon_state)
    r = TestClient(app).post(
        "/api/v1/agents/manage",
        json={
            "action": "enroll",
            "name": "content_writer",
            "task_id": _EH_TASK,
            "session_id": _EH_SESSION,
            "description": "desc",
            "system_prompt": "prompt",
        },
        headers=auth_headers,
    )
    assert r.status_code == 200

    managed = [
        log for log in daemon_state.db.get_audit_logs(_EH_TASK)
        if log["action"] == "agent_managed"
    ]
    assert len(managed) == 1
    assert managed[0]["agent"] == "engineering_head"
    assert managed[0]["payload"]["action"] == "enroll"
    assert managed[0]["payload"]["name"] == "content_writer"
    assert managed[0]["payload"]["source"] == "task"


def test_manage_agent_talk_path_writes_audit_entry_scoped_to_talk(
    tmp_home, app, daemon_state, auth_headers,
) -> None:
    talk_id = _seed_eh_talk(daemon_state, "TALK-800")
    r = TestClient(app).post(
        "/api/v1/agents/manage",
        json={
            "action": "enroll",
            "name": "content_writer",
            "talk_id": talk_id,
            "description": "desc",
            "system_prompt": "prompt",
        },
        headers=auth_headers,
    )
    assert r.status_code == 200

    managed = [
        log for log in daemon_state.db.get_audit_logs(talk_id)
        if log["action"] == "agent_managed"
    ]
    assert len(managed) == 1
    assert managed[0]["agent"] == "engineering_head"
    assert managed[0]["payload"]["action"] == "enroll"
    assert managed[0]["payload"]["name"] == "content_writer"
    assert managed[0]["payload"]["source"] == "talk"


def test_manage_agent_failed_enrollment_does_not_log(
    tmp_home, app, daemon_state, auth_headers,
) -> None:
    """A 409 duplicate enrollment must not leave an audit row."""
    _activate_eh_session(daemon_state)
    daemon_state.db.insert_enrollment("content_writer", "desc", "prompt")
    r = TestClient(app).post(
        "/api/v1/agents/manage",
        json={
            "action": "enroll",
            "name": "content_writer",
            "task_id": _EH_TASK,
            "session_id": _EH_SESSION,
            "description": "desc",
            "system_prompt": "prompt",
        },
        headers=auth_headers,
    )
    assert r.status_code == 409

    managed = [
        log for log in daemon_state.db.get_audit_logs(_EH_TASK)
        if log["action"] == "agent_managed"
    ]
    assert len(managed) == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/daemon/test_routes_agents.py -k "writes_audit or failed_enrollment" -v`
Expected: FAIL — no `agent_managed` action exists yet.

- [ ] **Step 3: Implement — add `log_agent_managed` to AuditLogger**

Append to `src/infrastructure/audit_logger.py` (place next to the talk-log methods since it shares the scope-id pattern — add a short comment pointing to the existing scope-id note at line 173):

```python
    def log_agent_managed(
        self,
        *,
        scope_id: str,
        actor: str,
        action: str,
        name: str,
        source: str,
    ) -> None:
        """Record a successful manage-agent call.

        `scope_id` populates `audit_log.task_id` (the generic scope column
        described at line 173): TASK-xxx for task-path calls, TALK-xxx for
        talk-path calls. `source` is 'task' or 'talk' for quick filtering.
        """
        self._db.insert_audit_log(
            task_id=scope_id,
            agent=actor,
            action="agent_managed",
            payload={
                "action": action,
                "name": name,
                "source": source,
            },
        )
```

- [ ] **Step 4: Wire the logger into the route**

In `src/daemon/routes/agents.py`, inside `manage_agent`, after each successful action (enroll insert, update apply, terminate apply — before the `return`), add an audit call. First, compute the scope id + source once near the top of the route (after the auth check):

```python
    scope_id = body.talk_id if body.talk_id is not None else body.task_id
    source = "talk" if body.talk_id is not None else "task"
    audit = AuditLogger(state.db)
```

Then at the three success paths, immediately before `return`:

- After `state.db.insert_enrollment(...)` in the `enroll` branch:
  ```python
  audit.log_agent_managed(
      scope_id=scope_id,
      actor="engineering_head",
      action="enroll",
      name=body.name,
      source=source,
  )
  ```
- At the end of the `update` branch (after workspace regen, before `return`): same call with `action="update"`.
- At the end of the `terminate` branch (after `shutil.rmtree`, before `return`): same call with `action="terminate"`.

Add the import at the top of `src/daemon/routes/agents.py` if missing:

```python
from src.infrastructure.audit_logger import AuditLogger
```

- [ ] **Step 5: Run the new tests to verify they pass**

Run: `uv run pytest tests/daemon/test_routes_agents.py -k "writes_audit or failed_enrollment" -v`
Expected: 3 PASS.

- [ ] **Step 6: Run the full route test file to confirm no regression**

Run: `uv run pytest tests/daemon/test_routes_agents.py -v`
Expected: all PASS.

- [ ] **Step 7: Run the full unit suite**

Run: `uv run pytest tests/ -v`
Expected: all PASS (integration suite excluded by default — unit tests only).

- [ ] **Step 8: Commit**

```bash
git add src/infrastructure/audit_logger.py src/daemon/routes/agents.py tests/daemon/test_routes_agents.py
git commit -m "feat(manage-agent): audit every successful management action

Adds AuditLogger.log_agent_managed so every enroll/update/terminate
leaves a trail scoped to the originating task or talk. Payload carries
source='task'|'talk' for downstream filtering. Failed actions (409,
404) do not log — only the success paths do."
```

---

## Task 5: Update `manage-agent` skill documentation

**Files:**
- Modify: `protocol/skills/manage-agent/SKILL.md`

- [ ] **Step 1: Rewrite the skill doc**

Replace the contents of `protocol/skills/manage-agent/SKILL.md` with:

```markdown
---
name: manage-agent
description: Enroll, update, or terminate an agent. Write a JSON file and call opc manage-agent --from-file to keep the invocation single-line. Enrollment requires founder approval.
---

# manage-agent

Manage the agent roster. You can **enroll** a new agent (requires founder approval), **update** an existing agent's system prompt or description, or **terminate** an agent (removes its workspace).

## Authentication paths

The daemon accepts two ways to prove you are the Engineering Head:

- **Task path** — supply `task_id` + `session_id` from your current task session. Use this while executing a task.
- **Talk path** — supply `talk_id` from an open talk you are currently in. Use this during a founder talk when the need for an enrollment/update/termination surfaces in conversation.

The two paths are **mutually exclusive** — supply one pair or the other, never both. The daemon rejects payloads that mix them (`422`).

## Usage

1. **Write a JSON file** to `/tmp/manage-agent-<unique>.json` using the Write tool.

   **Task-path enroll:**
   ```json
   {
     "action": "enroll",
     "name": "content_writer",
     "task_id": "<task_id>",
     "session_id": "<session_id>",
     "description": "Writes destination guides and travel articles",
     "system_prompt": "You are the Content Writer. Your responsibilities are...",
     "executor": "codex",
     "repos": {"web-content": "https://github.com/t-benze/web-content.git"}
   }
   ```

   **Talk-path enroll:**
   ```json
   {
     "action": "enroll",
     "name": "content_writer",
     "talk_id": "<talk_id>",
     "description": "Writes destination guides and travel articles",
     "system_prompt": "You are the Content Writer. Your responsibilities are...",
     "executor": "codex",
     "repos": {"web-content": "https://github.com/t-benze/web-content.git"}
   }
   ```

   **Update an existing agent (task path shown; talk path swaps task_id+session_id for talk_id):**
   ```json
   {
     "action": "update",
     "name": "content_writer",
     "task_id": "<task_id>",
     "session_id": "<session_id>",
     "description": "Updated description",
     "system_prompt": "Updated system prompt...",
     "executor": "claude"
   }
   ```

   **Terminate an agent (task path shown; talk path swaps task_id+session_id for talk_id):**
   ```json
   {
     "action": "terminate",
     "name": "content_writer",
     "task_id": "<task_id>",
     "session_id": "<session_id>"
   }
   ```

2. **Invoke as a single-line command:**

   ```bash
   opc manage-agent --from-file /tmp/manage-agent-<unique>.json
   ```

   The `--from-file` form is mandatory for agent sessions. In Claude sessions,
   multi-line bash commands are rejected by the `Bash(opc:*)` permission rule
   because newlines count as command separators.

## Access control

Only the **Engineering Head** may use this skill. The daemon validates the auth path you supplied:

- Task path: the `(task_id, session_id)` pair must match an active engineering_head session in the session tracker.
- Talk path: the `talk_id` must reference a talk whose `agent_name` is `engineering_head` and whose `status` is `open`.

Other agents — and closed/abandoned talks — receive a `403 Forbidden` (or `404` if the talk id is unknown).

## When called during a talk: update your transcript

If you invoke this skill from within a talk, **record the call in the `transcript_markdown` you will send at `/talk end`**. One line per action is enough, e.g.:

```
[during talk] submitted enrollment request for agent `content_writer` (pending founder approval).
```

The transcript is the only human-readable record of what happened in the conversation, and the daemon writes it at talk-end from whatever you provide. Skipping this step silently mutates the roster from the founder's point of view. The audit log (`opc audit <talk_id>`) captures the action too, but the transcript is what the founder reads back.

## What happens

- **enroll**: Creates a pending enrollment request. You may optionally specify `executor: "claude"` or `executor: "codex"`; if omitted, it defaults to `claude`. The founder must run `opc approve-agent <name>` before the agent's workspace is bootstrapped and the agent becomes available for delegation.
- **update**: Updates the agent's description, system prompt, executor, or repos in the enrollment registry. If the system prompt or executor changes, the workspace bootstrap files are regenerated. Only works on approved agents.
- **terminate**: Marks the agent as terminated and deletes its workspace directory. Only works on approved agents.

## Agent naming

Agent names must be lowercase with underscores only (e.g. `content_writer`, `seo_agent`). No spaces, hyphens, or uppercase.

## Error handling

- If `opc` returns non-zero, retry once after 1 second.
- `409` (duplicate name on enroll, non-approved agent on update/terminate) and `404` (agent not found, talk not found) are not retryable.
- `422` usually means the payload mixed task and talk auth paths, or supplied neither — fix the JSON and retry.
```

- [ ] **Step 2: Commit**

```bash
git add protocol/skills/manage-agent/SKILL.md
git commit -m "docs(manage-agent): document talk-path payload + transcript rule"
```

---

## Task 6: Update `talk` skill documentation — carve out manage-agent

**Files:**
- Modify: `protocol/skills/talk/SKILL.md:122-128` ("What NOT to do" section)

- [ ] **Step 1: Edit the prohibition list**

In `protocol/skills/talk/SKILL.md`, replace the "What NOT to do" section with:

```markdown
## What NOT to do

- Don't dispatch tasks (`opc run ...`) from inside a talk — that's out of scope for v1. If something actionable comes up that requires a task, tell the founder explicitly and let them submit.
- **Exception:** `opc manage-agent` (enroll / update / terminate) is allowed during a talk via the talk-path payload (pass `talk_id` instead of `task_id`+`session_id`). See the `manage-agent` skill. Record any such call in your `transcript_markdown` so the founder has a human-readable record at talk-end.
- Don't call `opc talk end` without a summary + transcript. An empty payload is useless on recall.
- Don't write learnings you've already written — the daemon appends verbatim, so duplicates will clutter `learnings.md`.
- Don't treat KB entries as a catch-all for in-talk notes. KB is for durable, cross-agent-relevant knowledge. Everything else is a per-agent learning.
```

- [ ] **Step 2: Commit**

```bash
git add protocol/skills/talk/SKILL.md
git commit -m "docs(talk): allow opc manage-agent during a talk"
```

---

## Task 7: Update `CLAUDE.md` to reference dual auth path

**Files:**
- Modify: `CLAUDE.md` (update the `opc manage-agent` CLI line and the enrollment discussion)

- [ ] **Step 1: Locate and edit the CLI reference**

In `CLAUDE.md`, find the line under "Running the Daemon + CLI":

```
opc manage-agent --from-file /tmp/manage-agent-enroll.json  # enroll/update/terminate an agent
```

Replace with:

```
opc manage-agent --from-file /tmp/manage-agent-enroll.json  # enroll/update/terminate an agent (task-path or talk-path auth)
```

Then find the `### Agent executors` section and, just before the repos-per-agent discussion, append a short paragraph after the existing example JSON:

```markdown
Payloads can authenticate via either an active EH task session
(`task_id` + `session_id`) or an open EH talk (`talk_id`). The two paths
are mutually exclusive. See `protocol/skills/manage-agent/SKILL.md` for
the full payload shapes.
```

- [ ] **Step 2: Commit**

```bash
git add CLAUDE.md
git commit -m "docs(claude-md): note talk-path auth for opc manage-agent"
```

---

## Final verification

- [ ] **Run the full unit suite**

Run: `uv run pytest tests/ -v`
Expected: all PASS.

- [ ] **Run the integration suite (route-touching change)**

Run: `uv run pytest tests/ -v -m integration`
Expected: all PASS. Integration tests spawn a real daemon + fake executors — they are isolated from your live `~/.opc/` via the `OPC_DAEMON_HOME` env redirect.

- [ ] **Spot-check the skill doc in a fresh workspace**

Run: `uv run opc init-agent engineering_head` in a scratch runtime and confirm the regenerated `.claude/skills/manage-agent/SKILL.md` contains the "Authentication paths" section. (This also proves the skill-copy step picked up your edit.)
