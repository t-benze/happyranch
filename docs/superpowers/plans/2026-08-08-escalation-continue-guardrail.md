# Surface `continue` in the Thread Escalation Guardrail Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When a manager is woken (REPLY/BOOTSTRAP) in a thread with an unresolved escalation, tell it about the existing `resolve-escalation --decision continue` path (resume the same task in place) alongside the existing `resolves` dispatch/supersede path — but only when the predecessor is actually `continue`-eligible (block kind `"escalated"`, not `"delegated"`).

**Architecture:** Two independent, low-risk text changes. (1) `runtime/daemon/thread_runner.py::_maybe_unresolved_escalations_note` is rewritten to branch its generated note text on `_eligible_supersede_block_kind`'s return value (`"escalated"` vs `"delegated"`) instead of treating every eligible predecessor identically. (2) `cli/commands/tasks.py`'s `resolve-escalation` subparser help string drops an inaccurate "(founder only)" qualifier. No orchestrator state machine, schema, auth, or route logic changes.

**Tech Stack:** Python 3.12+, pytest (`uv run python -m pytest`), argparse (CLI), FastAPI (unrelated to this change — routes are untouched).

## Global Constraints

- Every source file starts with `from __future__ import annotations` (already present in both touched files — do not remove).
- Run tests with `uv run python -m pytest tests/<file> -v` — not `uv run pytest` (stale shebang, per project convention).
- No changes to `runtime/daemon/routes/tasks.py`, `runtime/orchestrator/run_step.py`, or any HIGH/CRITICAL symbol — this plan only touches prompt-text generation and one CLI help string.
- No changes to `engineering_manager.md` or any other org agent `.md` file.
- Preserve existing test behavior where the spec says to ("existing tests keep passing") — the one deliberate exception is `test_does_not_dup_same_task_id`, whose expected substring count legitimately changes from 2 to 3 (see Task 1, Step 1c) because the new `continue` example line adds one more literal mention of the task id. This is called out explicitly, not an accidental break.

---

### Task 1: Add `continue` option to the escalation guardrail note

**Files:**
- Modify: `runtime/daemon/thread_runner.py:177-249` (the `_maybe_unresolved_escalations_note` function; two small new helper functions are added just above it)
- Modify: `tests/test_thread_escalation_guardrail.py`

**Interfaces:**
- Consumes: `_eligible_supersede_block_kind(org, predecessor) -> str | None` from `runtime.daemon.routes.tasks`, already imported inline in the function (returns `"escalated"`, `"delegated"`, or `None` — see `runtime/daemon/routes/tasks.py:994-1015`). No signature change to this function.
- Produces: `_maybe_unresolved_escalations_note(*, messages, org_state, purpose, invoked_agent) -> str` — same signature as today; both call sites in `run_invocation` (`runtime/daemon/thread_runner.py` around lines 652 and 719) need zero changes.

- [ ] **Step 1: Write the failing/updated tests**

Open `tests/test_thread_escalation_guardrail.py`. Make these four changes:

**1a. Add a new fixture helper** (place it near `_insert_delegated_task_with_live_child`, after it):

```python
def _insert_delegated_task_with_terminal_child(db, task_id: str = "TASK-900",
                                                team: str = "engineering",
                                                agent: str = "engineering_head") -> None:
    """Insert an in_progress(delegated) task whose only child is terminal —
    supersedable via `resolves` (Gap-B safety gate passes), but NOT
    continue-eligible: `continue` requires the predecessor's own status to be
    literally ESCALATED, and this task's status is IN_PROGRESS."""
    db.insert_task(TaskRecord(
        id=task_id, brief="delegated work", team=team,
        assigned_agent=agent, status=TaskStatus.IN_PROGRESS,
        block_kind=BlockKind.DELEGATED,
    ))
    db.insert_task(TaskRecord(
        id=f"{task_id}-child", brief="child work", team=team,
        assigned_agent="dev_agent", status=TaskStatus.COMPLETED,
        parent_task_id=task_id,
    ))
```

**1b. Update the module docstring** at the top of the file (currently says the guardrail instructs the agent to include `resolves` — broaden it):

Find:
```python
"""Tests for the TASK-1201 thread escalation guardrail.

When a manager receives a REPLY/BOOTSTRAP invocation in a thread that carries
unresolved ``task_escalated`` system messages whose live task rows are still
supersedable, the prompt MUST name the concrete task ids and instruct the agent
to include ``resolves`` in any continuation dispatch payload.
"""
```

Replace with:
```python
"""Tests for the TASK-1201 thread escalation guardrail (extended to surface
``continue`` — docs/superpowers/specs/2026-08-08-escalation-continue-guardrail-design.md).

When a manager receives a REPLY/BOOTSTRAP invocation in a thread that carries
unresolved ``task_escalated`` system messages whose live task rows are still
supersedable, the prompt MUST name the concrete task ids and the resolution
options available for each: ``continue`` (resume the SAME task in place —
only valid when the predecessor's block kind is ``"escalated"``) and
``resolves`` (dispatch a new task naming the predecessor — valid for both
``"escalated"`` and ``"delegated"`` block kinds).
"""
```

**1c. Update the dup-id count assertion.** Find:

```python
def test_does_not_dup_same_task_id():
    """Same TASK-900 escalated twice (e.g. revisit chain) → note names it once."""
    db = FakeDB({"TASK-900": _escalated_task("TASK-900")})
    org = FakeOrgState(db=db, teams=FakeTeams({"engineering_head"}))
    msgs = [
        _system_msg(1, {"kind_tag": "task_escalated", "task_id": "TASK-900", "status": "escalated"}),
        _message(2, "founder", "ok"),
        _system_msg(3, {"kind_tag": "task_escalated", "task_id": "TASK-900", "status": "escalated"}),
    ]
    note = _maybe_unresolved_escalations_note(
        messages=msgs,
        org_state=org,
        purpose="reply",
        invoked_agent="engineering_head",
    )
    assert note.count("TASK-900") == 2  # one in intro, one in JSON field name
```

Replace the final assertion + comment with:

```python
    # Three legitimate mentions now that the note offers both resolution
    # options for a continue-eligible (ESCALATED) predecessor: the intro
    # sentence, the `continue` CLI example's --task-id, and the `resolves`
    # JSON example. Still exactly one note block for the one deduped task id.
    assert note.count("TASK-900") == 3
```

**1d. Add three new tests** at the end of the "`_maybe_unresolved_escalations_note` unit tests" section (after `test_no_note_when_no_escalation_messages`, before the `# build_thread_prompt` section divider):

```python
def test_note_offers_continue_for_escalated_predecessor():
    """A literally-ESCALATED predecessor gets both the continue CLI example
    and the resolves JSON example."""
    db = FakeDB({"TASK-900": _escalated_task("TASK-900")})
    org = FakeOrgState(db=db, teams=FakeTeams({"engineering_head"}))
    msgs = [
        _system_msg(1, {"kind_tag": "task_escalated", "task_id": "TASK-900", "status": "escalated"}),
    ]
    note = _maybe_unresolved_escalations_note(
        messages=msgs,
        org_state=org,
        purpose="reply",
        invoked_agent="engineering_head",
    )
    assert "--task-id TASK-900 --decision continue" in note
    assert "resolve-escalation" in note
    assert '{"resolves": "TASK-900"}' in note


def test_note_omits_continue_for_multiple_escalated_predecessors_labels_each():
    """Two ESCALATED predecessors — both get a continue example, each keyed
    to its own task id (never a comma-joined --task-id)."""
    db = FakeDB({
        "TASK-900": _escalated_task("TASK-900"),
        "TASK-901": _escalated_task("TASK-901"),
    })
    org = FakeOrgState(db=db, teams=FakeTeams({"engineering_head"}))
    msgs = [
        _system_msg(1, {"kind_tag": "task_escalated", "task_id": "TASK-900", "status": "escalated"}),
        _system_msg(2, {"kind_tag": "task_escalated", "task_id": "TASK-901", "status": "escalated"}),
    ]
    note = _maybe_unresolved_escalations_note(
        messages=msgs,
        org_state=org,
        purpose="reply",
        invoked_agent="engineering_head",
    )
    assert "--task-id TASK-900 --decision continue" in note
    assert "--task-id TASK-901 --decision continue" in note
    assert "--task-id TASK-900, TASK-901 --decision continue" not in note
```

- [ ] **Step 2: Run tests to verify the new/updated ones fail**

Run: `uv run python -m pytest tests/test_thread_escalation_guardrail.py -v`

Expected: `test_does_not_dup_same_task_id` FAILS (`assert note.count("TASK-900") == 3` — actual is 2, old implementation). `test_note_offers_continue_for_escalated_predecessor` and `test_note_omits_continue_for_multiple_escalated_predecessors_labels_each` FAIL (`assert "--task-id TASK-900 --decision continue" in note` — string not present, old implementation never mentions `continue`). All other existing tests still PASS (implementation hasn't changed yet, and their assertions don't depend on the new text).

- [ ] **Step 3: Implement the rewritten note function**

In `runtime/daemon/thread_runner.py`, find the current `_maybe_unresolved_escalations_note` function (lines 177-249, shown in full in the prior Read). Replace it — and insert two small helper functions immediately above it — with:

```python
def _continue_cli_example(task_id: str) -> str:
    return (
        f'  `happyranch resolve-escalation --task-id {task_id} '
        f'--decision continue --rationale "<summarize the founder\'s reply>"`'
    )


def _resolves_json_example(task_id: str) -> str:
    return f'  {task_id} → {{"resolves": "{task_id}"}}'


def _maybe_unresolved_escalations_note(
    *,
    messages: list[ThreadMessage],
    org_state,
    purpose: str,
    invoked_agent: str,
) -> str:
    """Guardrail: when a manager receives a REPLY/BOOTSTRAP invocation in a
    thread that carries unresolved ``task_escalated`` system messages whose live
    task rows are still supersedable, surface the concrete task ids and the
    resolution options available for each: ``continue`` (resume the SAME task
    in place — only valid when the predecessor's block kind is
    ``"escalated"``) and ``resolves`` (dispatch a new task naming the
    predecessor — valid for both ``"escalated"`` and ``"delegated"`` block
    kinds).

    Derived from thread messages + task status, never from brief prose.
    """
    if purpose not in ("reply", "bootstrap"):
        return ""
    # Only fire for thread participants who can actually close a predecessor —
    # a worker self-dispatch that names resolves is rejected 403 anyway.
    teams = getattr(org_state, "teams", None)
    if teams is None or not teams.is_team_manager(invoked_agent):
        return ""
    from runtime.daemon.routes.tasks import _eligible_supersede_block_kind

    escalated: list[tuple[str, str]] = []  # (task_id, block_kind)
    seen_ids: set[str] = set()
    for m in messages:
        if m.kind is not ThreadMessageKind.SYSTEM:
            continue
        payload = m.system_payload or {}
        if payload.get("kind_tag") != "task_escalated":
            continue
        task_id = payload.get("task_id", "")
        if not task_id or task_id in seen_ids:
            continue
        task = org_state.db.get_task(task_id)
        if task is None:
            continue
        block_kind = _eligible_supersede_block_kind(org_state, task)
        if block_kind is None:
            continue
        escalated.append((task_id, block_kind))
        seen_ids.add(task_id)
    if not escalated:
        return ""

    if len(escalated) == 1:
        tid, block_kind = escalated[0]
        if block_kind == "escalated":
            return (
                "\n## Unresolved Escalation in This Thread\n\n"
                f"Task **{tid}** escalated in this thread and is still "
                f"awaiting a founder-authorized resolution. Pick the option "
                f"that matches the founder's reply:\n\n"
                f"- If the founder's reply resolves this escalation with no "
                f"new task-shaped work needed, resume the SAME task in "
                f"place — original brief untouched, the reply is appended "
                f"as an audited note:\n"
                f"{_continue_cli_example(tid)}\n\n"
                f"- If the founder's reply requires new delegated work, "
                f"your next self-dispatched task MUST include the explicit "
                f"linkage:\n"
                f'  ```json\n'
                f'  {{"resolves": "{tid}"}}\n'
                f'  ```\n'
                f"  Omitting this field leaves the predecessor open — the "
                f"runtime cannot infer the relationship from brief prose "
                f"alone.\n\n"
            )
        # block_kind == "delegated": continue is not valid (requires literal
        # ESCALATED status) — resolves is the only option, unchanged text.
        return (
            "\n## Unresolved Escalation in This Thread\n\n"
            f"Task **{tid}** escalated in this thread and is still "
            f"awaiting a founder-authorized continuation.\n\n"
            f"If your next self-dispatched task is the continuation, you MUST "
            f"include the explicit linkage in your dispatch payload:\n"
            f'  ```json\n'
            f'  {{"resolves": "{tid}"}}\n'
            f'  ```\n'
            f"Omitting this field leaves the predecessor open — the runtime cannot "
            f"infer the relationship from brief prose alone.\n\n"
        )

    # Multiple unresolved escalations — show per-task options, each keyed to
    # its own task id and its own block-kind eligibility.
    ids_str = ", ".join(tid for tid, _ in escalated)
    per_task_lines: list[str] = []
    for tid, block_kind in escalated:
        if block_kind == "escalated":
            per_task_lines.append(
                f"**{tid}** — if the founder's reply resolves this "
                f"escalation with no new task-shaped work needed, resume "
                f"it in place:\n"
                f"{_continue_cli_example(tid)}\n"
                f"  Otherwise, if new delegated work is needed:\n"
                f"{_resolves_json_example(tid)}"
            )
        else:
            per_task_lines.append(_resolves_json_example(tid))
    per_task_block = "\n".join(per_task_lines)
    return (
        "\n## Unresolved Escalations in This Thread\n\n"
        f"The following tasks escalated in this thread and are still "
        f"awaiting a founder-authorized resolution: **{ids_str}**.\n\n"
        f"For each, pick the option that matches the founder's reply. If "
        f"your next self-dispatched task is the continuation of one of "
        f"these via `resolves`, you MUST include the explicit linkage for "
        f"the specific predecessor your continuation supersedes:\n"
        f"{per_task_block}\n\n"
        f"Omitting the `resolves` field leaves the predecessor open — the "
        f"runtime cannot infer the relationship from brief prose alone.\n\n"
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run python -m pytest tests/test_thread_escalation_guardrail.py -v`

Expected: all tests PASS, including the three touched/added in Step 1. Specifically verify:
- `test_returns_note_for_manager_with_single_unresolved_escalation` — still passes (`"TASK-900"`, `"resolves"`, `"is still awaiting a founder"` all still present in the new escalated-branch text).
- `test_returns_note_for_multiple_unresolved_escalations` — still passes (`"and are still awaiting"`, the two literal `TID → {"resolves": "TID"}` lines, and the no-comma-joined-resolves check all still hold).
- `test_does_not_dup_same_task_id` — passes with the updated `== 3` assertion.
- `test_note_offers_continue_for_escalated_predecessor` and `test_note_omits_continue_for_multiple_escalated_predecessors_labels_each` — pass.
- `test_run_invocation_injects_guardrail_for_supersedable_escalation` and `test_run_invocation_skips_guardrail_for_non_supersedable_predecessor` — still pass unchanged (covered further in Step 5 below).

- [ ] **Step 5: Extend the `run_invocation` boundary coverage**

Still in `tests/test_thread_escalation_guardrail.py`:

**5a.** Extend the existing `test_run_invocation_injects_guardrail_for_supersedable_escalation` test — after the existing three assertions, add:

```python
    assert "--decision continue" in cap._prompt
    assert "resolve-escalation" in cap._prompt
```

**5b.** Add two new boundary tests after `test_run_invocation_skips_guardrail_for_non_supersedable_predecessor`:

```python
@pytest.mark.asyncio
async def test_run_invocation_guardrail_omits_continue_for_delegated_predecessor(
    tmp_path, monkeypatch
):
    """A delegated (in_progress, all children terminal) predecessor is
    supersedable via resolves but is NOT continue-eligible — continue
    requires the predecessor's own status to be literally ESCALATED. The
    guardrail must offer resolves without ever mentioning continue."""
    from runtime.infrastructure.database import Database
    from runtime.config import Settings
    from runtime.daemon import thread_runner as runner_mod

    db = Database(tmp_path / "happyranch.db")
    db.insert_thread(ThreadRecord(id="THR-001", subject="Delegated follow-up"))
    db.add_thread_participant("THR-001", "engineering_head", added_by="founder")
    db.append_thread_message(
        thread_id="THR-001", speaker="system",
        kind=ThreadMessageKind.SYSTEM,
        system_payload={"kind_tag": "task_escalated",
                         "task_id": "TASK-900",
                         "status": "escalated"},
    )
    db.append_thread_message(
        thread_id="THR-001", speaker="founder",
        kind=ThreadMessageKind.MESSAGE, body_markdown="status?",
    )
    _insert_delegated_task_with_terminal_child(db, "TASK-900")
    inv = db.mint_thread_invocation(
        thread_id="THR-001", agent_name="engineering_head",
        triggering_seq=2, purpose=ThreadInvocationPurpose.REPLY,
    )

    ws = tmp_path / "workspaces" / "engineering_head"
    ws.mkdir(parents=True)
    (ws / "agent.yaml").write_text("executor: claude\n")

    cap = _CapturingExecutor()
    monkeypatch.setattr(
        runner_mod, "_build_executor_for_provider",
        lambda provider, settings, paths: cap,
    )

    org = _make_org_state_with_teams(db, tmp_path)
    await runner_mod.run_invocation(
        org_state=org, invocation_token=inv.invocation_token,
        settings=Settings(),
    )
    assert cap._prompt is not None, "executor was invoked"
    assert "Unresolved Escalation" in cap._prompt
    assert '"resolves"' in cap._prompt
    assert "--decision continue" not in cap._prompt


@pytest.mark.asyncio
async def test_run_invocation_guardrail_mixed_escalated_and_delegated(
    tmp_path, monkeypatch
):
    """Two unresolved escalations in one thread: TASK-900 is literally
    ESCALATED (continue-eligible) and TASK-901 is delegated with terminal
    children (resolves-only). The note must label each correctly, not
    apply continue's eligibility to both uniformly."""
    from runtime.infrastructure.database import Database
    from runtime.config import Settings
    from runtime.daemon import thread_runner as runner_mod

    db = Database(tmp_path / "happyranch.db")
    db.insert_thread(ThreadRecord(id="THR-001", subject="Mixed follow-up"))
    db.add_thread_participant("THR-001", "engineering_head", added_by="founder")
    db.append_thread_message(
        thread_id="THR-001", speaker="system",
        kind=ThreadMessageKind.SYSTEM,
        system_payload={"kind_tag": "task_escalated",
                         "task_id": "TASK-900",
                         "status": "escalated"},
    )
    db.append_thread_message(
        thread_id="THR-001", speaker="system",
        kind=ThreadMessageKind.SYSTEM,
        system_payload={"kind_tag": "task_escalated",
                         "task_id": "TASK-901",
                         "status": "escalated"},
    )
    db.append_thread_message(
        thread_id="THR-001", speaker="founder",
        kind=ThreadMessageKind.MESSAGE, body_markdown="status on both?",
    )
    _insert_escalated_task(db, "TASK-900")
    _insert_delegated_task_with_terminal_child(db, "TASK-901")
    inv = db.mint_thread_invocation(
        thread_id="THR-001", agent_name="engineering_head",
        triggering_seq=3, purpose=ThreadInvocationPurpose.REPLY,
    )

    ws = tmp_path / "workspaces" / "engineering_head"
    ws.mkdir(parents=True)
    (ws / "agent.yaml").write_text("executor: claude\n")

    cap = _CapturingExecutor()
    monkeypatch.setattr(
        runner_mod, "_build_executor_for_provider",
        lambda provider, settings, paths: cap,
    )

    org = _make_org_state_with_teams(db, tmp_path)
    await runner_mod.run_invocation(
        org_state=org, invocation_token=inv.invocation_token,
        settings=Settings(),
    )
    prompt = cap._prompt
    assert prompt is not None, "executor was invoked"
    assert "Unresolved Escalations" in prompt  # plural header
    assert "--task-id TASK-900 --decision continue" in prompt
    assert "--task-id TASK-901 --decision continue" not in prompt
    assert '{"resolves": "TASK-900"}' in prompt
    assert '{"resolves": "TASK-901"}' in prompt
```

- [ ] **Step 6: Run the full test file to verify everything passes**

Run: `uv run python -m pytest tests/test_thread_escalation_guardrail.py -v`

Expected: all tests PASS (original + updated + 5 new: `test_note_offers_continue_for_escalated_predecessor`, `test_note_omits_continue_for_multiple_escalated_predecessors_labels_each`, `test_run_invocation_guardrail_omits_continue_for_delegated_predecessor`, `test_run_invocation_guardrail_mixed_escalated_and_delegated`, plus the extended assertions in `test_run_invocation_injects_guardrail_for_supersedable_escalation`).

- [ ] **Step 7: Commit**

```bash
git add runtime/daemon/thread_runner.py tests/test_thread_escalation_guardrail.py
git commit -m "$(cat <<'EOF'
feat(threads): surface resolve-escalation continue in the escalation guardrail

The guardrail note that wakes a manager about an unresolved escalation
only ever mentioned dispatching a new task via resolves (supersede).
continue already exists (THR-080) and resumes the same task in place
with the original brief untouched, but nothing told managers about it —
an audit of THR-107 found 30 superseded root tasks averaging 117
minutes each, and zero uses of continue, across ~80 tasks. The note now
offers continue for literally-ESCALATED predecessors (continue's actual
eligibility requirement) alongside the existing resolves example.
EOF
)"
```

---

### Task 2: Fix the misleading CLI help text

**Files:**
- Modify: `cli/commands/tasks.py:1083`
- Modify: `tests/test_cli.py`

**Interfaces:**
- Consumes: `build_parser()` from `cli.main` (already imported in `tests/test_cli.py`).
- Produces: nothing consumed by other tasks — this is a standalone docstring-only change.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_cli.py` (anywhere alongside the other `test_*_subcommand` tests, e.g. after `test_init_agent_subcommand`):

```python
def test_resolve_escalation_help_not_founder_only(capsys):
    """The resolve-escalation subcommand's help text must not claim it is
    founder-only — the underlying route has no such check (agents and the
    founder share one bearer token), and the escalation guardrail now
    directs managers to use --decision continue themselves."""
    parser = build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["resolve-escalation", "--help"])
    out = capsys.readouterr().out
    assert "founder only" not in out.lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/test_cli.py::test_resolve_escalation_help_not_founder_only -v`

Expected: FAIL — `assert "founder only" not in out.lower()` — the current help string is `"Resolve an escalated task (founder only)"`.

- [ ] **Step 3: Fix the help string**

In `cli/commands/tasks.py`, find (around line 1083):

```python
    p_resolve = sub.add_parser("resolve-escalation", help="Resolve an escalated task (founder only)")
```

Replace with:

```python
    p_resolve = sub.add_parser("resolve-escalation", help="Resolve an escalated task")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run python -m pytest tests/test_cli.py::test_resolve_escalation_help_not_founder_only -v`

Expected: PASS.

- [ ] **Step 5: Run the full CLI test file to check for regressions**

Run: `uv run python -m pytest tests/test_cli.py -v`

Expected: all tests PASS (this is a single-string change with no other consumers).

- [ ] **Step 6: Commit**

```bash
git add cli/commands/tasks.py tests/test_cli.py
git commit -m "$(cat <<'EOF'
fix(cli): drop inaccurate founder-only label from resolve-escalation help

The route has no founder/manager auth distinction — agents and the
founder share one bearer token. The label discouraged managers from
using --decision continue, which the escalation guardrail now points
them to directly.
EOF
)"
```

---

### Final Verification

- [ ] Run the full unit suite to confirm no unrelated regressions: `uv run python -m pytest tests/ -v`
- [ ] Re-read `docs/superpowers/specs/2026-08-08-escalation-continue-guardrail-design.md` sections 5, 6, and 8 and confirm each is implemented: §5 (note logic) → Task 1; §6 (CLI help text) → Task 2; §8 (testing) → Task 1 Steps 1/5 and Task 2 Steps 1/5.
