"""G3 producer contracts over real OrgState/bootstrap/SQLite; no epoch authority."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from runtime.daemon.org_state import OrgState
from runtime.orchestrator.executors import ExecutorResult
from tests.test_orchestrator import _setup_protocol_skills, _setup_workspaces


@pytest.fixture
def collection_org(test_settings, test_runtime, monkeypatch):
    from runtime.orchestrator.agent_def import AgentDef, render_agent_text
    test_runtime.agents_dir.mkdir(parents=True, exist_ok=True)
    (test_runtime.root / "org" / "teams.yaml").write_text("teams:\n  engineering:\n    manager: engineering_head\n    workers: [dev_agent]\n")
    for name, role in (("engineering_head", "manager"), ("dev_agent", "worker")):
        definition = AgentDef(name=name, team="engineering", role=role, executor="claude",
                              allow_rules=(), repos={}, enrolled_by=None, enrolled_at_task=None,
                              enrolled_at=None, system_prompt="worker", description="")
        (test_runtime.agents_dir / f"{name}.md").write_text(render_agent_text(definition))
    _setup_protocol_skills(test_settings)
    _setup_workspaces(test_runtime)
    org = OrgState.load(slug="test", root=test_runtime.root, settings=test_settings)
    prompts = []

    def provider(**kwargs):
        prompts.append(kwargs["full_prompt"])
        kwargs["on_started"](12345)
        return ExecutorResult(success=True, duration_seconds=0,
                              session_id=kwargs["session_id"], returncode=0)

    monkeypatch.setattr(org.orchestrator, "_launch_agent_with_scratch", provider)
    try:
        yield org, prompts
    finally:
        org.close()


def rows(org, action):
    return org.db.get_audit_logs_by_action(action)


def test_actual_org_bootstrap_reserves_independent_census(collection_org):
    """G3-P01/P12: real entry owns ordinals before any start or exposure."""
    org, prompts = collection_org
    first = org.orchestrator.create_task("root")
    second = org.orchestrator.create_task("second")
    for task in (first, second):
        result, report = org.orchestrator._run_agent(task, "dev_agent", "")
        assert result.success and report is None
    intents = rows(org, "memory_runtime_intent")
    assert len(intents) == 2, "actual _run_agent entries must persist independent intents"
    assert [row["payload"]["ordinal"] for row in intents] == [1, 2]
    assert [row["task_id"] for row in intents] == [first, second]
    assert len(prompts) == 2
    observer = org.memory_collection
    snapshot = observer.snapshot()
    assert snapshot["assigned_intents"] == 2
    assert snapshot["active_preparations"] == []
    assert observer.validate()["census_valid"] is True
    assert observer.validate()["collection_decision"] == "insufficient_instrumentation"


def seed_memory(org, *, budget=255, malformed=False, neighbor=False):
    from tests.infrastructure.test_memory_digest_exposure import FIXTURE
    root = org.root / "workspaces" / "dev_agent" / "memory"
    root.mkdir(exist_ok=True)
    text = FIXTURE.replace("id: MEM-001", "id: malformed") if malformed else FIXTURE
    (root / "MEM-001-bounded.md").write_text(text)
    if neighbor:
        (root / "MEM-002-valid.md").write_text(FIXTURE.replace("MEM-001", "MEM-002"))
    org.orchestrator._paths.org_config_path.write_text(f"memory_digest_budget: {budget}\n")
    return root


def bootstrap(org):
    task = org.orchestrator.create_task("Unrelated")
    result, report = org.orchestrator._run_agent(task, "dev_agent", "")
    assert result.success and report is None
    return task, result.session_id


@pytest.mark.parametrize("lost", ["memory_runtime_intent", "session_start", "memory_digest_impression",
                                  "memory_runtime_expectation", "memory_collection_seal"])
def test_lost_zero_read_row_cannot_reduce_live_population(collection_org, lost):
    """G3-P01/P04/P11: delete a real row, including the last seal, at zero reads."""
    org, _ = collection_org
    seed_memory(org)
    bootstrap(org)
    bootstrap(org)
    assert org.memory_collection.validate()["census_valid"] is True
    candidates = rows(org, lost)
    target = candidates[-1] if lost == "memory_collection_seal" else candidates[0]
    org.db.execute("DELETE FROM audit_log WHERE id=?", (target["id"],))
    org.db.commit()
    result = org.memory_collection.validate()
    assert result["census_valid"] is False, result
    assert org.memory_collection.snapshot()["assigned_intents"] == 2
    if lost == "session_start":
        assert result["discrepancies"]["session_start"] == 1
    if lost == "memory_digest_impression":
        assert result["discrepancies"]["impression"] == 1
    if lost == "memory_runtime_intent":
        assert result["discrepancies"]["intent"] == 1
    assert rows(org, "memory_read") == rows(org, "memory_search") == []
    assert result["thresholds_met"] is False


@pytest.mark.parametrize("corruption", ["identical_duplicate", "conflicting_duplicate", "sid", "row_task",
                                        "row_agent", "budget", "mode", "matching_corruption", "middle_ordinal"])
def test_corrupt_expectation_cannot_match_live_digest(collection_org, corruption):
    """G3-P04/P12: matching invented/corrupt streams cannot replace the live digest."""
    org, _ = collection_org
    seed_memory(org)
    bootstrap(org)
    bootstrap(org)
    expected = rows(org, "memory_runtime_expectation")[0]
    payload = dict(expected["payload"])
    if corruption.endswith("duplicate"):
        if corruption == "conflicting_duplicate":
            payload["budget"] = 172
        org.db.insert_audit_log(expected["task_id"], expected["agent"], expected["action"], payload)
    elif corruption in ("row_task", "row_agent"):
        column = "task_id" if corruption == "row_task" else "agent"
        org.db.execute(f"UPDATE audit_log SET {column}=? WHERE id=?", ("wrong", expected["id"]))
        org.db.commit()
    else:
        if corruption == "sid":
            payload["session_id"] = "provider-resume-id"
        elif corruption == "budget":
            payload["budget"] = 172
        elif corruption in ("mode", "matching_corruption"):
            payload["pointer_ids"], payload["full_body_ids"] = ["MEM-001"], []
            if corruption == "matching_corruption":
                impression = rows(org, "memory_digest_impression")[0]
                changed = {**impression["payload"], "pointer_ids": ["MEM-001"], "full_body_ids": []}
                org.db.execute("UPDATE audit_log SET payload=? WHERE id=?",
                               (json.dumps(changed), impression["id"]))
        else:
            payload["ordinal"] = 3
        org.db.execute("UPDATE audit_log SET payload=? WHERE id=?", (json.dumps(payload), expected["id"]))
        org.db.commit()
    result = org.memory_collection.validate()
    assert result["census_valid"] is False, result
    assert "expectation_count_or_digest" in result["problems"], result


@pytest.mark.parametrize(("case", "budget", "state", "reason", "text", "pointers", "bodies"), [
    ("disabled", 0, "disabled", "budget_zero", False, [], []),
    ("missing", 255, "empty", "memory_directory_absent", False, [], []),
    ("empty_directory", 255, "empty", "renderer_empty", False, [], []),
    ("below_header", 1, "empty", "renderer_empty", False, [], []),
    ("header_nudge", 172, "empty", "no_valid_rendered_ids", True, [], []),
    ("malformed", 1500, "empty", "no_valid_rendered_ids", True, [], []),
    ("neighbor", 1500, "nonempty", "rendered_ids", True, [], ["MEM-002"]),
    ("fit", 255, "nonempty", "rendered_ids", True, [], ["MEM-001"]),
    ("fallback", 172, "nonempty", "rendered_ids", True, ["MEM-001"], []),
])
def test_actual_render_freezes_distinct_expectations(collection_org, case, budget, state, reason, text, pointers, bodies):
    """G3-P02/P03: exact real render, including malformed-only text and valid neighbor."""
    org, prompts = collection_org
    if case == "missing":
        org.orchestrator._paths.org_config_path.write_text(f"memory_digest_budget: {budget}\n")
    else:
        root = seed_memory(org, budget=budget, malformed=case in ("malformed", "neighbor"), neighbor=case == "neighbor")
        if case == "empty_directory":
            (root / "MEM-001-bounded.md").unlink()
        if case == "header_nudge":
            # Two oversized pointers reach the actual header + nudge-only branch.
            for index in (1, 2):
                (root / f"MEM-00{index}-bounded.md").write_text(
                    "---\nid: invalid\nslug: short\ntitle: " + "X" * 300 +
                    "\nprovenance: experiential\nscope: agent\nlifecycle: valid\nsalience: 1\n---\nbody\n")
    task, sid = bootstrap(org)
    records = rows(org, "memory_runtime_expectation")
    assert len(records) == 1
    payload = records[0]["payload"]
    assert (payload["state"], payload["reason"], payload["rendered_text_present"]) == (state, reason, text)
    assert (payload["pointer_ids"], payload["full_body_ids"], payload["digest_ids"]) == (pointers, bodies, bodies + pointers)
    assert payload["budget"] == budget and payload["session_id"] == sid
    assert "MEM-999" not in payload["digest_ids"]
    impressions = rows(org, "memory_digest_impression")
    assert len(impressions) == (1 if state == "nonempty" else 0)
    if impressions:
        for key in ("pointer_ids", "full_body_ids", "digest_ids", "digest_count", "budget"):
            assert impressions[0]["payload"][key] == payload[key]
    assert org.memory_collection.validate()["census_valid"] is True
    assert len(prompts) == 1
    assert records[0]["task_id"] == task
    encoded = json.dumps(payload)
    assert "Keep task credit scoped" not in encoded and "Bounded directive" not in encoded


def test_observation_preserves_prompt_bytes(collection_org, monkeypatch):
    """G3-P02/P03: observer attachment cannot change any prompt bytes."""
    org, prompts = collection_org
    seed_memory(org)
    monkeypatch.setattr("runtime.orchestrator.orchestrator.render_current_time_line", lambda *args: "fixed-time")
    task = org.orchestrator.create_task("Unrelated")
    observer = org.orchestrator._memory_collection
    org.orchestrator._run_agent(task, "dev_agent", "", runtime_session_id="runtime-byte-proof")
    org.orchestrator._memory_collection = None
    try:
        org.orchestrator._run_agent(task, "dev_agent", "", runtime_session_id="runtime-byte-proof")
    finally:
        org.orchestrator._memory_collection = observer
    assert prompts[0].encode() == prompts[1].encode()
    assert len(rows(org, "memory_runtime_intent")) == 1


@pytest.mark.parametrize("fault_kind", ["raise", "omitted"])
@pytest.mark.parametrize("failed_action", ["memory_runtime_intent", "memory_runtime_expectation",
                                           "memory_runtime_terminal", "memory_collection_seal"])
def test_write_failure_is_sticky_without_changing_launch(collection_org, monkeypatch, failed_action, fault_kind):
    """G3-P01/P11: one lost insertion cannot become a healthy lower denominator."""
    org, prompts = collection_org
    insert = org.db.insert_audit_log
    failed = False

    def fault(task_id, agent, action, payload=None):
        nonlocal failed
        if action == failed_action and not failed:
            failed = True
            if fault_kind == "omitted":
                return None
            raise OSError("test-side unavailable observation storage")
        return insert(task_id, agent, action, payload)

    monkeypatch.setattr(org.db, "insert_audit_log", fault)
    bootstrap(org)
    bootstrap(org)
    snapshot = org.memory_collection.snapshot()
    assert failed and len(prompts) == 2 and snapshot["assigned_intents"] == 2
    assert snapshot["observation_error"] is not None
    assert org.memory_collection.validate()["census_valid"] is False
    assert len(rows(org, "session_start")) == 2
    assert len(rows(org, "memory_runtime_terminal")) == (1 if failed_action == "memory_runtime_terminal" else 2)
    # Failed writer never clears on a later successful call; restart is a new boot.
    from runtime.infrastructure.memory_collection import CollectionObserver
    restarted = CollectionObserver(org=org.slug, root=org.root, db=org.db)
    assert restarted.boot_id != snapshot["boot_id"]
    assert restarted.validate()["problems"] == ["zero_population", "seal_count_or_digest"]


@pytest.mark.parametrize("failure", ["before_sid", "render", "after_expectation", "scratch"])
def test_application_failure_preserves_unknown_phases_and_exception(collection_org, monkeypatch, failure):
    """G3-P09: own finally covers early preparation and later launch failures."""
    from runtime.orchestrator.orchestrator import AgentUnavailableError
    from runtime.orchestrator.task_scratch import TaskScratchError
    org, prompts = collection_org
    task = org.orchestrator.create_task("Unrelated")
    sentinel = RuntimeError("application sentinel")
    if failure == "before_sid":
        (org.root / "org" / "agents" / "dev_agent.md").unlink()
        with pytest.raises(AgentUnavailableError):
            org.orchestrator._run_agent(task, "dev_agent", "")
    elif failure == "render":
        seed_memory(org)
        def fail_render(*args, **kwargs):
            raise sentinel
        monkeypatch.setattr("runtime.infrastructure.learnings_store.MemoryStore.render_memory_digest", fail_render)
        with pytest.raises(RuntimeError) as exc:
            org.orchestrator._run_agent(task, "dev_agent", "")
        assert exc.value is sentinel
    elif failure == "after_expectation":
        def fail_launch(**kwargs):
            raise sentinel
        monkeypatch.setattr(org.orchestrator, "_launch_agent_with_scratch", fail_launch)
        with pytest.raises(RuntimeError) as exc:
            org.orchestrator._run_agent(task, "dev_agent", "")
        assert exc.value is sentinel
    else:
        def refuse(**kwargs):
            raise TaskScratchError("scratch refusal")
        monkeypatch.setattr("runtime.orchestrator.orchestrator.prepare_task_scratch", refuse)
        result, report = org.orchestrator._run_agent(task, "dev_agent", "")
        assert not result.success and report is None and result.error == "scratch refusal"
    intent = rows(org, "memory_runtime_intent")
    terminal = rows(org, "memory_runtime_terminal")
    assert len(intent) == len(terminal) == 1 and prompts == []
    payload = terminal[0]["payload"]
    assert payload["ordinal"] == 1 and payload["task_id"] == task
    assert (payload["session_id"] is None) is (failure == "before_sid")
    assert payload["expectation_known"] is (failure in ("after_expectation", "scratch"))
    assert payload["binding_known"] is (failure in ("after_expectation", "scratch"))
    assert payload["launched_callbacks"] == 0
    assert payload["outcome"] == ("returned" if failure == "scratch" else "raised")
    assert org.memory_collection.snapshot()["active_references"] == []
    assert org.memory_collection.validate()["census_valid"] is (failure not in ("before_sid", "render"))


@pytest.mark.parametrize(("started", "success", "returncode", "error"), [
    (False, False, None, "timeout"), (False, False, -15, "cancelled"),
    (True, False, 2, "nonzero"), (True, True, 0, None),
])
def test_executor_results_and_callback_occurrences_are_descriptive(collection_org, monkeypatch, started, success, returncode, error):
    """G3-P06/P09: started retries are occurrences, not additional invocations."""
    org, _ = collection_org
    results = []
    def provider(**kwargs):
        if started:
            kwargs["on_started"](100)
            kwargs["on_started"](101)  # legitimate provider-boundary retry callback seam
        result = ExecutorResult(success=success, duration_seconds=4, session_id=kwargs["session_id"],
                                returncode=returncode, error=error)
        results.append(result)
        return result
    monkeypatch.setattr(org.orchestrator, "_launch_agent_with_scratch", provider)
    task = org.orchestrator.create_task("Unrelated")
    returned, report = org.orchestrator._run_agent(task, "dev_agent", "")
    assert returned is results[0] and report is None
    assert len(rows(org, "memory_runtime_intent")) == 1
    assert len(rows(org, "memory_runtime_expectation")) == 1
    assert [row["payload"]["callback_count"] for row in rows(org, "memory_runtime_launched")] == ([1, 2] if started else [])
    terminal = rows(org, "memory_runtime_terminal")[0]["payload"]
    assert (terminal["success"], terminal["returncode"], terminal["launched_callbacks"]) == (success, returncode, 2 if started else 0)
    assert org.memory_collection.validate()["census_valid"] is True


def test_terminal_observation_fault_does_not_mask_application_exception(collection_org, monkeypatch):
    """G3-P11: terminal/seal faults cannot suppress the application's exception."""
    org, _ = collection_org
    sentinel = RuntimeError("application sentinel")
    insert = org.db.insert_audit_log
    def fault(task_id, agent, action, payload=None):
        if action in ("memory_runtime_terminal", "memory_collection_seal"):
            raise OSError("observation unavailable")
        return insert(task_id, agent, action, payload)
    def provider(**kwargs):
        raise sentinel
    monkeypatch.setattr(org.db, "insert_audit_log", fault)
    monkeypatch.setattr(org.orchestrator, "_launch_agent_with_scratch", provider)
    task = org.orchestrator.create_task("Unrelated")
    with pytest.raises(RuntimeError) as exc:
        org.orchestrator._run_agent(task, "dev_agent", "")
    assert exc.value is sentinel
    assert org.memory_collection.snapshot()["assigned_intents"] == 1
    assert org.memory_collection.snapshot()["phase_counts"]["terminal"] == {"attempted": 1, "persisted": 0}
    assert org.memory_collection.snapshot()["observation_error"] is not None


@pytest.mark.parametrize("failure", ["constructor", "attachment"])
def test_observer_initialization_failure_keeps_actual_org_usable(collection_org, monkeypatch, failure):
    """G3-P11: failed initialization stays unavailable, preserving ordinary startup."""
    from runtime.orchestrator.orchestrator import Orchestrator
    org, _ = collection_org
    def fail(*args, **kwargs):
        raise RuntimeError("observation failure")
    target = "runtime.infrastructure.memory_collection.CollectionObserver" if failure == "constructor" else None
    if target:
        monkeypatch.setattr(target, fail)
    else:
        monkeypatch.setattr(Orchestrator, "attach_memory_collection", fail)
    try:
        reloaded = OrgState.load(slug=org.slug, root=org.root, settings=org.settings)
    except RuntimeError as exc:
        pytest.fail(f"observation failure must preserve real org startup: {exc}")
    try:
        monkeypatch.setattr(reloaded.orchestrator, "_launch_agent_with_scratch", lambda **kwargs: ExecutorResult(
            success=True, duration_seconds=0, session_id=kwargs["session_id"], returncode=0))
        bootstrap(reloaded)
        assert reloaded.memory_collection_unavailable == "observer_initialization_failed"
        if reloaded.memory_collection is not None:
            assert reloaded.memory_collection.snapshot()["observation_error"] == "observer_initialization_failed"
            assert reloaded.memory_collection.validate()["census_valid"] is False
        else:
            assert not hasattr(reloaded.orchestrator, "_memory_collection")
    finally:
        reloaded.close()


def test_snapshot_and_validation_do_not_write_or_restore_old_boot(collection_org):
    """G3-P11/P12: zero population, read-only durable bytes and restart isolation."""
    import hashlib
    from runtime.infrastructure.memory_collection import CollectionObserver
    org, _ = collection_org
    assert org.memory_collection.validate()["census_valid"] is False
    bootstrap(org)
    before_rows = org.db.fetch_all_readonly("SELECT * FROM audit_log ORDER BY id")
    files = [Path(str(org.db.path) + suffix) for suffix in ("", "-wal", "-shm")]
    before = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in files if path.exists()}
    for _ in range(3):
        assert org.memory_collection.snapshot()["assigned_intents"] == 1
        assert org.memory_collection.validate()["census_valid"] is True
    assert [tuple(row) for row in org.db.fetch_all_readonly("SELECT * FROM audit_log ORDER BY id")] == [tuple(row) for row in before_rows]
    assert {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in files if path.exists()} == before
    restarted = CollectionObserver(org=org.slug, root=org.root, db=org.db)
    assert restarted.snapshot()["assigned_intents"] == 0
    assert restarted.validate()["census_valid"] is False
    assert restarted.boot_id != org.memory_collection.boot_id


def test_blocked_observation_writer_allows_real_callback_progress(collection_org, monkeypatch):
    """G3-P08/P11: another real launch's started hook progresses during a blocked seal."""
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    org, _ = collection_org
    blocked, release, launched_b = Event(), Event(), Event()
    task_a = org.orchestrator.create_task("A")
    task_b = org.orchestrator.create_task("B")
    insert = org.db.insert_audit_log
    def writer(task_id, agent, action, payload=None):
        if task_id == task_a and action == "memory_collection_seal" and not blocked.is_set():
            blocked.set()
            assert release.wait(5), "fixture controller failed to release blocked writer"
        return insert(task_id, agent, action, payload)
    def provider(**kwargs):
        kwargs["on_started"](23456)
        if kwargs["task_id"] == task_b:
            launched_b.set()
        return ExecutorResult(success=True, duration_seconds=0, session_id=kwargs["session_id"], returncode=0)
    monkeypatch.setattr(org.db, "insert_audit_log", writer)
    monkeypatch.setattr(org.orchestrator, "_launch_agent_with_scratch", provider)
    with ThreadPoolExecutor(max_workers=2) as pool:
        future_a = pool.submit(org.orchestrator._run_agent, task_a, "dev_agent", "")
        try:
            assert blocked.wait(3)
            future_b = pool.submit(org.orchestrator._run_agent, task_b, "dev_agent", "")
            assert launched_b.wait(3), "observer persistence must not strand the application's started callback"
            assert future_b.result(timeout=2)[0].success
            snapshot = org.memory_collection.snapshot()
            assert snapshot["assigned_intents"] == 2 and snapshot["pending_observations"] > 0
            assert org.memory_collection.validate()["census_valid"] is False
        finally:
            release.set()
        assert future_a.result(timeout=3)[0].success
    assert org.memory_collection.validate()["census_valid"] is True
    terminals = rows(org, "memory_runtime_terminal")
    assert {row["task_id"] for row in terminals} == {task_a, task_b}
    assert len({row["payload"]["session_id"] for row in terminals}) == 2
    assert all(row["payload"]["launched_callbacks"] == 1 for row in terminals)


def test_supported_recovery_and_new_call_have_distinct_runtime_census(collection_org):
    """G3-P06: real recovery claim/publication is classified independently of provider resume."""
    from runtime.models import TaskStatus
    org, _ = collection_org
    org.orchestrator.attach_sessions(org.sessions)
    task, origin = bootstrap(org)
    org.db.update_task(task, status=TaskStatus.IN_PROGRESS)
    recovery_sid = "runtime-recovery-distinct"
    assert org.db.claim_task_completion_recovery(
        task_id=task, agent="dev_agent", origin_session_id=origin,
        recovery_session_id=recovery_sid, provider_session_id="provider-resume-distinct",
        claimed_at="2026-10-04T00:00:00+00:00", expires_at="2999-01-01T00:02:00+00:00",
    )
    result, _ = org.orchestrator._run_agent(
        task, "dev_agent", "", runtime_session_id=recovery_sid,
        origin_runtime_session_id=origin, resume_session_id="provider-resume-distinct", recovery=True,
    )
    assert result.success and result.session_id == recovery_sid
    identity = rows(org, "memory_runtime_identity")
    assert [row["payload"]["ordinal"] for row in identity] == [1, 2]
    assert [row["payload"]["session_id"] for row in identity] == [origin, recovery_sid]
    assert [row["payload"]["population"] for row in identity] == ["root", "recovery"]
    assert [row["payload"]["invocation_purpose"] for row in identity] == ["manager_decision", "unattributed"]
    assert "provider-resume-distinct" not in json.dumps(identity)
    assert org.sessions.is_recovery_session(task, "dev_agent", recovery_sid)
    assert org.memory_collection.validate()["census_valid"] is True
    # Another ordinary actual call receives its own ordinal and assigned SID.
    next_task, next_sid = bootstrap(org)
    assert next_task != task and next_sid not in (origin, recovery_sid)
    assert org.memory_collection.snapshot()["assigned_intents"] == 3


def test_pending_render_is_unknown_and_not_an_empty_population(collection_org, monkeypatch):
    """G3-P12: a real entered preparation at cutoff is pending, never inferred empty."""
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    from runtime.infrastructure.learnings_store import MemoryStore
    org, _ = collection_org
    seed_memory(org)
    entered, release = Event(), Event()
    renderer = MemoryStore.render_memory_digest
    def held(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        return renderer(*args, **kwargs)
    monkeypatch.setattr(MemoryStore, "render_memory_digest", held)
    task = org.orchestrator.create_task("Unrelated")
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(org.orchestrator._run_agent, task, "dev_agent", "")
        try:
            assert entered.wait(3)
            snapshot = org.memory_collection.snapshot()
            assert snapshot["assigned_intents"] == 1
            assert snapshot["active_preparations"][0]["task_id"] == task
            assert snapshot["phase_counts"]["expectation"]["attempted"] == 0
            result = org.memory_collection.validate()
            assert not result["census_valid"] and "preparation_pending" in result["problems"]
            assert rows(org, "memory_runtime_expectation") == []
        finally:
            release.set()
        assert future.result(timeout=3)[0].success
    assert org.memory_collection.validate()["census_valid"] is True


def test_task_looking_claims_never_create_census_or_epoch_authority(collection_org):
    """G3-P05/P10 producer-only: claimed synthetic/task labels cannot supply authority."""
    org, _ = collection_org
    task = org.orchestrator.create_task("synthetic canary TASK-claimed")
    # Merely queued/created work is outside the actual entry population.
    assert org.memory_collection.snapshot()["assigned_intents"] == 0
    org.orchestrator._run_agent(task, "dev_agent", "claimed synthetic epoch health")
    snapshot = org.memory_collection.snapshot()
    assert snapshot["assigned_intents"] == 1
    intent = rows(org, "memory_runtime_intent")[0]
    assert (intent["task_id"], intent["payload"]["task_id"]) == (task, task)
    identity = rows(org, "memory_runtime_identity")[0]["payload"]
    assert identity["population"] == "root" and identity["invocation_purpose"] == "manager_decision"
    assert "synthetic" not in json.dumps(snapshot) and "epoch" not in json.dumps(snapshot)
    assert rows(org, "memory_collection_epoch_started") == []
    assert rows(org, "memory_collection_epoch_invalidated") == []
    report = org.orchestrator._audit.compute_memory_telemetry_report()
    assert report["decision"] == "insufficient_instrumentation"
    assert report["observation_period"]["thresholds_met"] is False
    # Real thread/dream/deferred-population positives and installed H06 are NOT EXECUTED here.


def test_isolated_process_loss_cannot_reconstruct_old_completeness(collection_org, tmp_path):
    """G3-P11: kill only an owned bootstrap process mid-render, then reopen real OrgState."""
    import subprocess
    import sys
    import textwrap
    import time
    org, _ = collection_org
    seed_memory(org)
    task = org.orchestrator.create_task("Unrelated")
    receipt = tmp_path / "entered.json"
    script = textwrap.dedent('''\
        import json, sys, threading
        from pathlib import Path
        import runtime
        from runtime.config import Settings
        from runtime.daemon.org_state import OrgState
        from runtime.infrastructure.learnings_store import MemoryStore
        org = OrgState.load(slug="test", root=Path(sys.argv[1]), settings=Settings(project_root=Path(sys.argv[2])))
        render = MemoryStore.render_memory_digest
        def held(*args, **kwargs):
            Path(sys.argv[4]).write_text(json.dumps({"snapshot": org.memory_collection.snapshot(),
                "python": [sys.executable, sys.version], "runtime": runtime.__file__}))
            threading.Event().wait(30)
            return render(*args, **kwargs)
        MemoryStore.render_memory_digest = held
        org.orchestrator._run_agent(sys.argv[3], "dev_agent", "")
    ''')
    process = subprocess.Popen([sys.executable, "-c", script, str(org.root),
                                str(org.settings.project_root), task, str(receipt)],
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        deadline = time.monotonic() + 5
        while not receipt.exists() and process.poll() is None and time.monotonic() < deadline:
            time.sleep(0.01)
        assert receipt.exists(), "actual isolated bootstrap must reach the held renderer"
        old = json.loads(receipt.read_text())
        assert old["snapshot"]["assigned_intents"] == 1
        assert len(old["snapshot"]["active_preparations"]) == 1
        assert old["python"] == [sys.executable, sys.version]
        assert old["runtime"] == str(Path(__file__).resolve().parents[1] / "runtime/__init__.py")
        process.kill()
        process.communicate(timeout=3)
        assert process.returncode < 0
    finally:
        if process.poll() is None:
            process.kill()
        process.communicate(timeout=3)
    reopened = OrgState.load(slug=org.slug, root=org.root, settings=org.settings)
    try:
        assert reopened.memory_collection.boot_id != old["snapshot"]["boot_id"]
        assert reopened.memory_collection.snapshot()["assigned_intents"] == 0
        assert reopened.memory_collection.validate()["census_valid"] is False
        assert len(rows(reopened, "memory_runtime_intent")) == 1
        assert rows(reopened, "memory_runtime_terminal") == []
        assert rows(reopened, "memory_collection_epoch_started") == []
    finally:
        reopened.close()


def test_missing_middle_intent_is_not_filled_by_matching_streams(collection_org):
    """G3-P12: an exact 1..N census rejects the missing middle of three real entries."""
    org, _ = collection_org
    seed_memory(org)
    for _ in range(3):
        bootstrap(org)
    middle = rows(org, "memory_runtime_intent")[1]
    org.db.execute("DELETE FROM audit_log WHERE id=?", (middle["id"],))
    org.db.commit()
    result = org.memory_collection.validate()
    assert not result["census_valid"] and "ordinal_set" in result["problems"]
    assert result["discrepancies"]["intent"] == 1
    assert org.memory_collection.snapshot()["assigned_intents"] == 3
    assert len(rows(org, "session_start")) == len(rows(org, "memory_digest_impression")) == 3


@pytest.mark.parametrize(("action", "field", "value"), [
    ("session_start", "session_id", "provider-resume-distinct"),
    ("session_start", "executor", "wrong-executor"),
    ("session_start", "model", "wrong-model"),
    ("session_start", "invocation_purpose", "unattributed"),
    ("memory_digest_impression", "memory_telemetry_version", 2),
    ("memory_digest_impression", "agent", "wrong-agent"),
])
def test_existing_start_and_impression_must_match_observed_runtime(collection_org, action, field, value):
    """G3-P01/P04/P07: existing streams must match the independent actual tuple/version."""
    org, _ = collection_org
    seed_memory(org)
    bootstrap(org)
    record = rows(org, action)[0]
    changed = {**record["payload"], field: value}
    org.db.execute("UPDATE audit_log SET payload=? WHERE id=?", (json.dumps(changed), record["id"]))
    org.db.commit()
    result = org.memory_collection.validate()
    assert result["census_valid"] is False, result
    assert any(problem.startswith("session_start_" if action == "session_start" else "impression_")
               for problem in result["problems"]), result


def test_started_callback_from_provider_thread_keeps_own_invocation(collection_org, monkeypatch):
    """G3-P08/P11: callbacks without the caller's ContextVar still bind their own invocation."""
    from concurrent.futures import ThreadPoolExecutor
    org, _ = collection_org
    org.orchestrator.attach_sessions(org.sessions)
    def provider(**kwargs):
        with ThreadPoolExecutor(max_workers=1) as pool:
            pool.submit(kwargs["on_started"], 98765).result(timeout=2)
        assert org.sessions.get_pid(kwargs["task_id"], "dev_agent") == 98765
        return ExecutorResult(success=True, duration_seconds=0, session_id=kwargs["session_id"], returncode=0)
    monkeypatch.setattr(org.orchestrator, "_launch_agent_with_scratch", provider)
    task, sid = bootstrap(org)
    launches = rows(org, "memory_runtime_launched")
    assert len(launches) == 1
    assert launches[0]["task_id"] == task and launches[0]["payload"]["session_id"] == sid
    assert launches[0]["payload"]["callback_count"] == 1
    assert rows(org, "memory_runtime_terminal")[0]["payload"]["launched_callbacks"] == 1
    assert org.memory_collection.validate()["census_valid"] is True
