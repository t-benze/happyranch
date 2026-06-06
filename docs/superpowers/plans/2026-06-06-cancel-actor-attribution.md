# Cancel Actor Attribution Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make task cancellation record the real (caller-declared) actor in the audit log and task note, instead of always recording "founder".

**Architecture:** Advisory attribution. The cancel route gains an optional `actor` field (default `"founder"`); the CLI gains `--as-agent NAME` to populate it. Founder/web callers omit it and behavior is unchanged. The daemon does not validate the claim (founder and agents share one bearer token — out of scope per the spec).

**Tech Stack:** Python 3, FastAPI, Pydantic v2, pytest, argparse. Spec: `docs/superpowers/specs/2026-06-06-cancel-actor-attribution-design.md`.

---

## File Structure

- `runtime/infrastructure/audit_logger.py` — `log_task_cancelled()` gains an `actor` param.
- `runtime/daemon/routes/tasks.py` — `CancelBody.actor` field; `cancel_task` derives actor, builds the note, passes actor to the audit logger.
- `cli/commands/tasks.py` — `cmd_cancel` + its argparse parser gain `--as-agent`.
- `runtime/orchestrator/run_step.py` — comment-only touch-up (line ~998).
- `tests/daemon/test_routes_tasks.py` — route behavior tests.
- `tests/test_cli.py` — CLI flag + body tests.
- `tests/contract/test_openapi_snapshot.py` — regenerated snapshot (new optional field).

---

## Task 1: Route + audit logger honor a caller-declared actor

**Files:**
- Modify: `runtime/infrastructure/audit_logger.py:238-246`
- Modify: `runtime/daemon/routes/tasks.py` (`CancelBody` ~522-530; `cancel_task` ~842-864)
- Test: `tests/daemon/test_routes_tasks.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/daemon/test_routes_tasks.py`:

```python
def test_cancel_records_declared_actor(
    tmp_home, app, daemon_state, org_state, auth_headers,
) -> None:
    """When the caller declares an actor, the note and audit log record it
    instead of the hardcoded 'founder'."""
    sub = TestClient(app).post(
        "/api/v1/orgs/alpha/tasks", json={"brief": "x"}, headers=auth_headers,
    )
    task_id = sub.json()["task_id"]

    r = TestClient(app).post(
        f"/api/v1/orgs/alpha/tasks/{task_id}/cancel",
        json={"rationale": "", "cascade": True, "actor": "family_manager"},
        headers=auth_headers,
    )
    assert r.status_code == 200

    assert org_state.db.get_task(task_id).note == "cancelled by family_manager"
    cancel_logs = [
        e for e in org_state.db.get_audit_logs(task_id)
        if e["action"] == "task_cancelled"
    ]
    assert len(cancel_logs) == 1
    assert cancel_logs[0]["agent"] == "family_manager"


def test_cancel_actor_with_rationale(
    tmp_home, app, daemon_state, org_state, auth_headers,
) -> None:
    sub = TestClient(app).post(
        "/api/v1/orgs/alpha/tasks", json={"brief": "x"}, headers=auth_headers,
    )
    task_id = sub.json()["task_id"]

    r = TestClient(app).post(
        f"/api/v1/orgs/alpha/tasks/{task_id}/cancel",
        json={"rationale": "superseded", "actor": "family_manager"},
        headers=auth_headers,
    )
    assert r.status_code == 200
    assert org_state.db.get_task(task_id).note == "cancelled by family_manager: superseded"


def test_cancel_defaults_to_founder(
    tmp_home, app, daemon_state, org_state, auth_headers,
) -> None:
    """No actor supplied → unchanged 'founder' strings (backward compat)."""
    sub = TestClient(app).post(
        "/api/v1/orgs/alpha/tasks", json={"brief": "x"}, headers=auth_headers,
    )
    task_id = sub.json()["task_id"]

    r = TestClient(app).post(
        f"/api/v1/orgs/alpha/tasks/{task_id}/cancel",
        json={"rationale": "", "cascade": True},
        headers=auth_headers,
    )
    assert r.status_code == 200
    assert org_state.db.get_task(task_id).note == "cancelled by founder"
    cancel_logs = [
        e for e in org_state.db.get_audit_logs(task_id)
        if e["action"] == "task_cancelled"
    ]
    assert cancel_logs[0]["agent"] == "founder"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/daemon/test_routes_tasks.py -k "cancel_records_declared_actor or cancel_actor_with_rationale or cancel_defaults_to_founder" -v`
Expected: `test_cancel_records_declared_actor` and `test_cancel_actor_with_rationale` FAIL (note/agent are `founder`); `test_cancel_defaults_to_founder` PASSES (already-correct default — confirms no regression).

- [ ] **Step 3: Add `actor` param to the audit logger**

In `runtime/infrastructure/audit_logger.py`, replace the `log_task_cancelled` method (lines 238-246):

```python
    def log_task_cancelled(
        self, task_id: str, rationale: str, cascade: bool, actor: str = "founder",
    ) -> None:
        self._db.insert_audit_log(
            task_id=task_id,
            agent=actor,
            action="task_cancelled",
            payload={"rationale": rationale, "cascade": cascade},
        )
```

- [ ] **Step 4: Add the `actor` field to `CancelBody`**

In `runtime/daemon/routes/tasks.py`, in the `CancelBody` model (~522-530), add after the `cascade` field:

```python
    # Caller-declared actor for attribution. Advisory only — founder and agents
    # share one bearer token, so this is not validated. Omitted/blank → "founder",
    # preserving the original founder-only behavior byte-for-byte.
    actor: str | None = None
```

- [ ] **Step 5: Derive the actor and thread it through `cancel_task`**

In `runtime/daemon/routes/tasks.py`, in `cancel_task`, replace the note line (~843-844):

```python
    rationale = body.rationale.strip()
    note = f"cancelled by founder: {rationale}" if rationale else "cancelled by founder"
```

with:

```python
    rationale = body.rationale.strip()
    actor = (body.actor or "").strip() or "founder"
    note = f"cancelled by {actor}: {rationale}" if rationale else f"cancelled by {actor}"
```

Then in the same function update the audit call (~862-864) from:

```python
            audit.log_task_cancelled(
                task_id=tid, rationale=rationale, cascade=body.cascade,
            )
```

to:

```python
            audit.log_task_cancelled(
                task_id=tid, rationale=rationale, cascade=body.cascade, actor=actor,
            )
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `uv run pytest tests/daemon/test_routes_tasks.py -k "cancel_records_declared_actor or cancel_actor_with_rationale or cancel_defaults_to_founder" -v`
Expected: all 3 PASS.

- [ ] **Step 7: Commit**

```bash
git add runtime/infrastructure/audit_logger.py runtime/daemon/routes/tasks.py tests/daemon/test_routes_tasks.py
git commit -m "feat(cancel): record caller-declared actor in audit log and note"
```

---

## Task 2: CLI `--as-agent` flag

**Files:**
- Modify: `cli/commands/tasks.py` (`cmd_cancel` ~632-645; parser ~896-914)
- Test: `tests/test_cli.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_cli.py`:

```python
def test_cancel_parser_accepts_as_agent():
    from cli.main import build_parser

    args = build_parser().parse_args(
        ["cancel", "TASK-012", "--as-agent", "family_manager"]
    )
    assert args.command == "cancel"
    assert args.task_id == "TASK-012"
    assert args.as_agent == "family_manager"


def test_cancel_parser_as_agent_defaults_none():
    from cli.main import build_parser

    args = build_parser().parse_args(["cancel", "TASK-012"])
    assert args.as_agent is None


def test_cmd_cancel_includes_actor_when_set(capsys):
    from cli.main import cmd_cancel
    from unittest.mock import MagicMock, patch

    fake = MagicMock()
    fake.post.return_value.status_code = 200
    fake.post.return_value.json.return_value = {"cancelled": ["TASK-012"], "killed": []}
    with patch("cli.main.OpcClient.from_env", return_value=fake), \
         patch("cli._shared._fetch_available_orgs", return_value=["alpha"]):
        args = MagicMock(
            org=None, task_id="TASK-012", rationale="",
            no_cascade=False, as_agent="family_manager",
        )
        cmd_cancel(args)

    _, kwargs = fake.post.call_args
    assert kwargs["json"]["actor"] == "family_manager"


def test_cmd_cancel_omits_actor_when_unset(capsys):
    from cli.main import cmd_cancel
    from unittest.mock import MagicMock, patch

    fake = MagicMock()
    fake.post.return_value.status_code = 200
    fake.post.return_value.json.return_value = {"cancelled": ["TASK-012"], "killed": []}
    with patch("cli.main.OpcClient.from_env", return_value=fake), \
         patch("cli._shared._fetch_available_orgs", return_value=["alpha"]):
        args = MagicMock(
            org=None, task_id="TASK-012", rationale="",
            no_cascade=False, as_agent=None,
        )
        cmd_cancel(args)

    _, kwargs = fake.post.call_args
    assert "actor" not in kwargs["json"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_cli.py -k "cancel_parser_accepts_as_agent or cancel_parser_as_agent_defaults_none or cmd_cancel_includes_actor or cmd_cancel_omits_actor" -v`
Expected: FAIL — parser has no `--as-agent` (`args.as_agent` AttributeError / unrecognized argument), and `cmd_cancel` never sets `actor`.

- [ ] **Step 3: Add the `--as-agent` argument to the parser**

In `cli/commands/tasks.py`, in the cancel subparser block (~896-914), add before `p_cancel.set_defaults(func=cmd_cancel)`:

```python
    p_cancel.add_argument(
        "--as-agent", default=None, metavar="NAME",
        help="Attribute the cancellation to this agent instead of the founder "
             "(advisory; recorded in the audit log and task note)",
    )
```

- [ ] **Step 4: Send `actor` in the request body when set**

In `cli/commands/tasks.py`, in `cmd_cancel`, replace the POST call (~642-645):

```python
    r = client.post(
        f"/api/v1/orgs/{slug}/tasks/{args.task_id}/cancel",
        json={"rationale": args.rationale or "", "cascade": not args.no_cascade},
    )
```

with:

```python
    payload = {"rationale": args.rationale or "", "cascade": not args.no_cascade}
    if args.as_agent:
        payload["actor"] = args.as_agent
    r = client.post(
        f"/api/v1/orgs/{slug}/tasks/{args.task_id}/cancel",
        json=payload,
    )
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_cli.py -k "cancel_parser_accepts_as_agent or cancel_parser_as_agent_defaults_none or cmd_cancel_includes_actor or cmd_cancel_omits_actor" -v`
Expected: all 4 PASS.

- [ ] **Step 6: Commit**

```bash
git add cli/commands/tasks.py tests/test_cli.py
git commit -m "feat(cli): add --as-agent to task cancel for actor attribution"
```

---

## Task 3: Comment touch-up, OpenAPI snapshot, full suite, manual verify

**Files:**
- Modify: `runtime/orchestrator/run_step.py:998`
- Modify: `tests/contract/openapi.json` (regenerated)

- [ ] **Step 1: Fix the stale comment in `run_step.py`**

In `runtime/orchestrator/run_step.py`, in `_fail` (~995-998), replace:

```python
    # tries to write a "session failed (rc=-15; ...)" note. That must NOT
    # overwrite the founder's "cancelled by founder: ..." note.
```

with:

```python
    # tries to write a "session failed (rc=-15; ...)" note. That must NOT
    # overwrite the cancel route's "cancelled by <actor>: ..." note.
```

- [ ] **Step 2: Regenerate the OpenAPI snapshot**

The new optional `actor` field on `CancelBody` changes the schema.

Run: `HAPPYRANCH_REGEN_OPENAPI=1 uv run pytest tests/contract/test_openapi_snapshot.py`
Expected: PASS (snapshot rewritten). Confirm the diff to `tests/contract/openapi.json` only adds the optional `actor` property under the cancel request body — nothing else.

- [ ] **Step 3: Verify TS web contract still passes**

No new browser route (the existing cancel api function gains an optional arg only).

Run: `cd web && npx vitest run src/test/openapi-coverage.test.ts`
Expected: PASS.

- [ ] **Step 4: Run the full unit suite**

Run: `uv run pytest tests/ -v`
Expected: PASS (no regressions — pay attention to `tests/test_thread_task_followup.py` and any test asserting `"cancelled by founder"`, which must still pass via the default).

- [ ] **Step 5: Manual end-to-end check against a running daemon**

```bash
TOKEN=$(cat ~/.happyranch/daemon.token)
# Submit a throwaway task in a scratch org you control, capture its id, then:
happyranch cancel <TASK_ID> --as-agent family_manager --org <ORG>
curl -s -H "Authorization: Bearer $TOKEN" \
  http://localhost:8765/api/v1/orgs/<ORG>/tasks/<TASK_ID> | python3 -m json.tool
```
Expected: task `note` is `"cancelled by family_manager"`, and the `task_cancelled` audit entry shows `"agent": "family_manager"`.

- [ ] **Step 6: Commit**

```bash
git add runtime/orchestrator/run_step.py tests/contract/openapi.json
git commit -m "chore(cancel): refresh OpenAPI snapshot and stale actor comment"
```

---

## Self-Review Notes

- **Spec coverage:** audit logger actor (Task 1 Step 3), `CancelBody.actor` (Task 1 Step 4), note + audit wiring (Task 1 Step 5), CLI `--as-agent` (Task 2), comment fix (Task 3 Step 1), OpenAPI regen (Task 3 Step 2), backward-compat default (Task 1 `test_cancel_defaults_to_founder`). All spec sections covered.
- **Out-of-scope item** (agents actually passing `--as-agent`, which lives in the runtime container outside this repo) is intentionally NOT a task here — it is a documented follow-up in the spec.
- **Type consistency:** field name `actor` and CLI arg `--as-agent` (→ `args.as_agent`) are used consistently across route, CLI, and tests.
