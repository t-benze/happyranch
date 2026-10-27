from __future__ import annotations

from collections.abc import Iterator
import hashlib
import json
import os
import signal
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

from runtime.daemon import paths
from runtime.orchestrator.executor_binary_registry import save_registry
from runtime.runtime import RuntimeDir
from tests.integration.conftest import seed_workspace


pytestmark = pytest.mark.integration


@pytest.mark.parametrize("version", [1, 2])
def test_thr299_public_read_and_scope_refusal(live_daemon: str, runtime: Path, tmp_path: Path, version: int) -> None:
    """C03/C09 only: real HTTP/CLI negative/read; no new-producer acceptance.

    Uses the retained isolated parent and unmodified real daemon fixture, never
    this file's old queue/claim-patched launcher. A naturally allocated root
    cannot qualify as the exact THR299 operator. Terminal startup/launch effects
    are settled before independent read-only residue snapshots.
    """
    from runtime.orchestrator._paths import OrgPaths
    from runtime.runtime import read_token

    base = f"http://127.0.0.1:{live_daemon}/api/v1"
    headers = {"Authorization": f"Bearer {read_token()}"}
    task_id = _submit(base, headers, "bounded THR299 public read/refusal")
    _wait_task(base, headers, task_id, "failed")
    db_path = OrgPaths(root=runtime).db_path

    def snapshot() -> dict:
        with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as db:
            db.execute("BEGIN")
            return {name: db.execute(f'SELECT * FROM "{name}" ORDER BY rowid').fetchall()
                    for name in ("tasks", "task_results", "audit_log", "manager_supersessions", "task_pause_controls")}

    before = snapshot()
    route = f"{base}/orgs/test/tasks/{task_id}/retry-verification"
    response = httpx.get(route, params={"mode": "retry", "target_agent": "dev_agent"}, headers=headers)
    assert response.status_code == 200
    assert response.json() == {"version": 1, "mode": "retry", "org": "test", "parent_id": task_id,
                               "target_agent": "dev_agent", "failed_child_id": None, "valid": True,
                               "path": [task_id], "reason": None, "advisory": True}
    missing = httpx.get(route, params={"mode": "retry", "target_agent": "dev_agent", "failed_child_id": "missing"}, headers=headers)
    assert missing.status_code == 200 and missing.json()["reason"] == "retry_not_found"
    forbidden = httpx.get(route, params={"mode": "retry", "target_agent": "dev_agent"},
                         headers={**headers, "X-HappyRanch-Task-Session": "not-an-authority"})
    assert forbidden.status_code == 422 and forbidden.json() == {"detail": {"code": "invalid_request"}}
    duplicate = httpx.get(route + "?mode=retry&mode=receipt&target_agent=dev_agent", headers=headers)
    assert duplicate.status_code == 422
    assert httpx.get(route, params={"mode": "retry", "target_agent": "dev_agent"}).status_code == 401
    body = {"version": version, "operation_key": "synthetic-negative", "existing_successor_id": task_id,
            "current_descendant_id": task_id, "thread_id": "synthetic", "operator_task_id": task_id,
            "session_id": "not-an-authority", "expected_state_digest": "0" * 64}
    write = httpx.post(route.replace("retry-verification", "reconcile-failed-continuation"), headers=headers, json=body)
    assert write.status_code == 403 and write.json() == {"detail": {"code": "operation_scope"}}
    malformed = httpx.post(route.replace("retry-verification", "reconcile-failed-continuation"), headers=headers,
                          content=json.dumps(body)[:-1] + ',"version":1}', timeout=3)
    assert malformed.status_code == 422
    preflight = httpx.get(route, headers={**headers, "X-HappyRanch-Task-Session": "not-an-authority"},
                         params={"mode": "preflight", **{k: str(v) for k, v in body.items()
                                 if k not in ("session_id", "expected_state_digest")}})
    assert preflight.status_code == 403 and preflight.json() == {"detail": {"code": "operation_scope"}}
    history = httpx.get(route, params={"mode": "receipt", "operation_key": "synthetic-negative"}, headers=headers)
    assert history.status_code == 403 and history.json() == {"detail": {"code": "operation_scope"}}
    cli = Path(os.environ["HAPPYRANCH_TEST_PARENT_MANIFEST"]).parent / "bin/happyranch"
    for child, expected in ((None, 0), ("missing", 1)):
        args = [str(cli), "verify-retry-link", task_id, "--org", "test", "--mode", "retry",
                "--target-agent", "dev_agent", "--json"]
        if child:
            args += ["--failed-child-id", child]
        process = subprocess.run(args, capture_output=True, text=True, timeout=10)
        assert process.returncode == expected, process.stderr
        assert json.loads(process.stdout)["valid"] is (child is None)
    bad_cli = subprocess.run([str(cli), "verify-retry-link", task_id, "--org", "test", "--mode", "receipt"],
                             capture_output=True, text=True, timeout=10)
    assert bad_cli.returncode == 2 and json.loads(bad_cli.stdout) == {"detail": {"code": "invalid_request"}}
    command_file = tmp_path / "negative-command.json"
    command_file.write_text(json.dumps(body))
    refusal = subprocess.run([str(cli), "reconcile-failed-continuation", task_id, "--org", "test",
                              "--from-file", str(command_file)], capture_output=True, text=True, timeout=10)
    assert refusal.returncode == 1 and json.loads(refusal.stdout) == {"detail": {"code": "operation_scope"}}
    assert snapshot() == before


def test_thr299_isolated_old_validator_bound_and_local_failure(tmp_path: Path) -> None:
    """C05/C06: isolated row fixtures, not authenticated producer evidence.

    Arbitrary synthetic root names deliberately cannot enter the new writer.
    Retains the existing founder/manual old-producer contract and checks the
    inclusive twenty-root boundary and failed-child/local-failure priority.
    """
    from runtime.infrastructure.database import Database
    from runtime.infrastructure.db.tasks import InvalidLineage, VerifiedRetry
    from runtime.models import TaskRecord

    db = Database(tmp_path / "isolated-validator.db")
    try:
        for index in range(21):
            db.insert_task(TaskRecord(id=f"synthetic-root-{index:02}", team="engineering", assigned_agent="synthetic-manager",
                                      brief="isolated fixture", status="superseded" if index < 20 else "in_progress"))
        for index in range(20):
            predecessor, successor = f"synthetic-root-{index:02}", f"synthetic-root-{index + 1:02}"
            db.insert_audit_log(predecessor, "founder", "escalation_resolved",
                                {"decision": "supersede", "resolution_path": "manual_break_glass"})
            db.insert_audit_log(predecessor, "founder", "escalation_superseded",
                                {"actor": "founder", "prior_block_kind": "escalated", "successor_root": successor})
        db.insert_task(TaskRecord(id="synthetic-failed", team="engineering", assigned_agent="synthetic-worker",
                                  brief="isolated fixture", status="failed", parent_task_id="synthetic-root-00", task_type="subtask"))
        changes = db._conn.total_changes
        with db.coherent_read_view():
            valid = db.verify_retry_link("synthetic-root-19", "synthetic-worker", "synthetic-failed")
            assert isinstance(valid, VerifiedRetry) and len(valid.path) == 20
            assert db.verify_retry_link("synthetic-root-20", "synthetic-worker", "synthetic-failed") == InvalidLineage("lineage_record_limit_20")
            assert db.verify_retry_link("synthetic-root-19", "wrong-agent", "synthetic-failed") == InvalidLineage("retry_agent_mismatch")
        assert db._conn.total_changes == changes
        db.insert_task(TaskRecord(id="synthetic-local-failed", team="engineering", assigned_agent="synthetic-worker",
                                  brief="isolated fixture", status="failed", parent_task_id="synthetic-root-19", task_type="subtask"))
        changes = db._conn.total_changes
        with db.coherent_read_view():
            assert db.verify_retry_link("synthetic-root-19", "synthetic-worker", "synthetic-failed") == InvalidLineage("retry_link_required")
            assert db.verify_retry_link("synthetic-root-19", "synthetic-worker", "synthetic-local-failed") == VerifiedRetry(("synthetic-root-19",))
        assert db._conn.total_changes == changes
    finally:
        db.close()


@pytest.fixture(params=[1, 2], ids=["v1-thr299", "v2-thr291"])
def isolated_session_history(request: pytest.FixtureRequest) -> Iterator[tuple]:
    """Audit-row validator fixture only: no task, live SID or producer receipt.

    The literal owner is required by the selector, but these rows are invented
    in-memory evidence for malformed-history checks, never an authority grant.
    No generic Database initializer, public writer or retired unit is invoked.
    """
    from types import SimpleNamespace
    from runtime.infrastructure.db.task_reconciliation import TaskReconciliation

    # Literal independent input: deriving the fixture from the selected producer
    # would hide a decoder that accidentally selected the old owner for v2.
    owner = {1: "TASK-10725", 2: "TASK-13286"}[request.param]

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE audit_log (id INTEGER PRIMARY KEY, timestamp TEXT, task_id TEXT, "
                 "agent TEXT, action TEXT, payload TEXT)")
    selected = {"id": 1, "timestamp": "2026-10-01T00:00:00+00:00", "task_id": owner,
                "agent": "engineering_manager", "action": "session_start",
                "payload": '{"session_id":"isolated-selected","invocation_purpose":"manager_decision",'
                           '"reasoning_effort":"high","optional":{"temperature":0.25}}'}
    conn.execute("INSERT INTO audit_log VALUES (:id, :timestamp, :task_id, :agent, :action, :payload)", selected)
    validator = TaskReconciliation(SimpleNamespace(_conn=conn), request.param)
    binding = validator.original_session("isolated-selected")
    try:
        yield conn, validator, selected, binding
    finally:
        conn.close()


@pytest.mark.parametrize("extra", [
    pytest.param({"payload": '{"invocation_purpose":"manager_decision"}'}, id="review-missing-sid"),
    pytest.param({"payload": '{"session_id":null,"invocation_purpose":"manager_decision"}'}, id="null-sid"),
    pytest.param({"payload": '{"session_id":12,"invocation_purpose":"manager_decision"}'}, id="integer-sid"),
    pytest.param({"payload": '{"session_id":"","invocation_purpose":"manager_decision"}'}, id="empty-sid"),
    pytest.param({"payload": json.dumps({"session_id": "x" * 257, "invocation_purpose": "manager_decision"})}, id="long-sid"),
    pytest.param({"payload": '{"session_id":"isolated-other"}'}, id="missing-purpose"),
    pytest.param({"payload": '{"session_id":"isolated-other","invocation_purpose":null}'}, id="null-purpose"),
    pytest.param({"payload": '{"session_id":"isolated-other","invocation_purpose":true}'}, id="boolean-purpose"),
    pytest.param({"payload": '{"session_id":"isolated-other","invocation_purpose":""}'}, id="empty-purpose"),
    pytest.param({"payload": json.dumps({"session_id": "isolated-other", "invocation_purpose": "x" * 257})}, id="long-purpose"),
    pytest.param({"payload": '{"session_id":"isolated-selected","invocation_purpose":"manager_decision"}'}, id="duplicate-selected-sid"),
    pytest.param({"agent": "isolated-wrong-agent"}, id="wrong-agent"),
    pytest.param({"id": 0}, id="nonpositive-id"),
    pytest.param({"timestamp": "invalid"}, id="malformed-timestamp"),
    pytest.param({"timestamp": "2026-10-01T00:00:00"}, id="naive-timestamp"),
    pytest.param({"timestamp": None}, id="missing-timestamp"),
    pytest.param({"payload": "{"}, id="malformed-json"),
    pytest.param({"payload": "[]"}, id="nonobject-json"),
    pytest.param({"payload": b'{"session_id":"isolated-other","invocation_purpose":"unattributed"}'}, id="blob-in-text-column"),
    pytest.param({"payload": '{"session_id":"isolated-other","session_id":"duplicate","invocation_purpose":"unattributed"}'}, id="duplicate-json-key"),
    pytest.param({"payload": '{"session_id":"isolated-other","invocation_purpose":"unattributed","optional":NaN}'}, id="nonfinite-metadata"),
])
def test_thr299_isolated_session_history_refuses_before_sid_filter(isolated_session_history: tuple, extra: dict) -> None:
    """C03/C07/C08 corrective R2: malformed relevant history refuses without writes."""
    from runtime.infrastructure.db.task_reconciliation import ReconciliationError

    conn, validator, selected, binding = isolated_session_history
    other = {**selected, "id": 2,
             "payload": '{"session_id":"isolated-other","invocation_purpose":"unattributed"}', **extra}
    conn.execute("INSERT INTO audit_log VALUES (:id, :timestamp, :task_id, :agent, :action, :payload)", other)
    before = [tuple(row) for row in conn.execute("SELECT * FROM audit_log ORDER BY id")]
    changes = conn.total_changes
    for operation in (lambda: validator.original_session("isolated-selected"),
                      lambda: validator.historical_session(binding)):
        with pytest.raises(ReconciliationError) as refused:
            operation()
        assert refused.value.code == "original_session_evidence" and refused.value.status == 409
        assert conn.total_changes == changes
        assert [tuple(row) for row in conn.execute("SELECT * FROM audit_log ORDER BY id")] == before


@pytest.mark.parametrize("purpose", ["unattributed", "worker_execution", "workflow_document"])
def test_thr299_isolated_nonselected_purpose_and_metadata_compatible(isolated_session_history: tuple, purpose: str) -> None:
    """Valid history stays open to optional metadata and nonselected purposes."""
    from runtime.infrastructure.db.task_reconciliation import digest, text_digest

    conn, validator, selected, binding = isolated_session_history
    other = {**selected, "id": 2, "payload": json.dumps({"session_id": "isolated-other", "invocation_purpose": purpose,
              "executor": "isolated-provider", "model": None, "optional": {"numeric": 0.125, "text": "原始"}})}
    conn.execute("INSERT INTO audit_log VALUES (:id, :timestamp, :task_id, :agent, :action, :payload)", other)
    before = [tuple(row) for row in conn.execute("SELECT * FROM audit_log ORDER BY id")]
    changes = conn.total_changes
    assert validator.original_session("isolated-selected") == binding
    assert binding["session_start_row_digest"] == digest(selected)
    assert binding["session_id_sha256"] == text_digest("isolated-selected")
    assert validator.historical_session(binding) is None
    assert conn.total_changes == changes
    assert [tuple(row) for row in conn.execute("SELECT * FROM audit_log ORDER BY id")] == before


def test_thr299_isolated_selected_purpose_retains_specific_refusal(isolated_session_history: tuple) -> None:
    from runtime.infrastructure.db.task_reconciliation import ReconciliationError

    conn, validator, selected, binding = isolated_session_history
    payload = json.loads(selected["payload"])
    payload["invocation_purpose"] = "unattributed"
    conn.execute("UPDATE audit_log SET payload=? WHERE id=1", (json.dumps(payload),))
    before = [tuple(row) for row in conn.execute("SELECT * FROM audit_log ORDER BY id")]
    changes = conn.total_changes
    for operation, code, status in ((lambda: validator.original_session("isolated-selected"), "ordinary_purpose_required", 403),
                                   (lambda: validator.historical_session(binding), "original_session_evidence", 409)):
        with pytest.raises(ReconciliationError) as refused:
            operation()
        assert (refused.value.code, refused.value.status) == (code, status)
    assert conn.total_changes == changes
    assert [tuple(row) for row in conn.execute("SELECT * FROM audit_log ORDER BY id")] == before


def _write_executable(path: Path, body: str) -> None:
    path.write_text(body)
    path.chmod(0o755)


def _wait_health(base: str, timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if httpx.get(f"{base}/health", timeout=1.0).status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.1)
    raise AssertionError("owned candidate daemon did not become healthy")


def _wait_task(base: str, headers: dict[str, str], task_id: str, status: str) -> dict:
    deadline = time.monotonic() + 20
    body: dict = {}
    while time.monotonic() < deadline:
        response = httpx.get(f"{base}/orgs/test/tasks/{task_id}", headers=headers, timeout=2)
        assert response.status_code == 200, response.text
        body = response.json()
        if body["task"]["status"] == status:
            return body
        time.sleep(0.1)
    raise AssertionError((task_id, status, body))


def _request(
    base: str, headers: dict[str, str], method: str, route: str,
    body: dict | None = None, status: int = 200,
) -> dict:
    response = httpx.request(method, f"{base}{route}", headers=headers, json=body, timeout=3)
    assert response.status_code == status, response.text
    return response.json()


def _children(base: str, headers: dict[str, str], task_id: str) -> list[str]:
    return _request(base, headers, "GET", f"/orgs/test/tasks/{task_id}/recall")["children"]


def _submit(base: str, headers: dict[str, str], brief: str) -> str:
    return _request(
        base, headers, "POST", "/orgs/test/tasks",
        {"type": "general", "brief": brief},
    )["task_id"]


def _supersede(base: str, headers: dict[str, str], task_id: str, brief: str) -> str:
    result = _request(
        base, headers, "POST", f"/orgs/test/tasks/{task_id}/resolve-escalation",
        {"decision": "supersede", "rationale": "isolated proof", "brief": brief,
         "actor": "founder"},
    )
    assert result == {"ok": True, "task_id": task_id, "new_status": "superseded"}
    successor = _request(base, headers, "GET", f"/orgs/test/tasks/{task_id}")[
        "superseded_by_task_id"
    ]
    assert isinstance(successor, str) and successor
    return successor


def _init_agent(base: str, headers: dict[str, str], agent: str) -> None:
    with httpx.stream(
        "POST", f"{base}/orgs/test/agents/init", headers=headers,
        json={"agent": agent}, timeout=30,
    ) as stream:
        for line in stream.iter_lines():
            if not line.startswith("data: "):
                continue
            event = json.loads(line.removeprefix("data: "))
            assert event.get("phase") != "error", event
            if event.get("phase") == "all_done":
                return
    raise AssertionError(f"agent init did not complete: {agent}")


def _proc_identity(
    pid: int, daemon_home: Path | None = None,
) -> tuple[str, bytes, int, int] | None:
    proc = Path("/proc") / str(pid)
    try:
        stat = (proc / "stat").read_text().rsplit(")", 1)[1].split()
        command = (proc / "cmdline").read_bytes()
    except FileNotFoundError:
        return None
    if stat[0] == "Z":
        return None
    if daemon_home is not None:
        environment = (proc / "environ").read_bytes().split(b"\0")
        assert b"HAPPYRANCH_DAEMON_HOME=" + os.fsencode(daemon_home) in environment
    return stat[19], command, int(stat[1]), int(stat[2])


def _owned_descendants(
    root_pid: int,
) -> dict[int, tuple[str, bytes, int, int]]:
    snapshots: dict[int, tuple[str, bytes, int, int]] = {}
    pending = [root_pid]
    while pending:
        parent = pending.pop()
        current = _proc_identity(parent)
        if current is not None:
            snapshots[parent] = current
        for entry in Path("/proc").iterdir():
            if not entry.name.isdigit() or int(entry.name) in snapshots:
                continue
            try:
                stat = (entry / "stat").read_text().rsplit(")", 1)[1].split()
            except (FileNotFoundError, PermissionError):
                continue
            if int(stat[1]) == parent:
                pending.append(int(entry.name))
    return snapshots


def _stop_owned(
    process: subprocess.Popen, daemon_home: Path,
    saved: tuple[str, bytes, int, int],
) -> None:
    current_root = _proc_identity(process.pid, daemon_home)
    assert current_root is not None and current_root[:2] == saved[:2]
    owned = _owned_descendants(process.pid)
    owned.setdefault(process.pid, saved)
    for sig, deadline_seconds in ((signal.SIGTERM, 5), (signal.SIGKILL, 2)):
        for pid, identity in owned.items():
            current = _proc_identity(pid)
            if current is not None:
                assert current[:2] == identity[:2], ("PID identity changed", pid)
                os.kill(pid, sig)
        deadline = time.monotonic() + deadline_seconds
        while time.monotonic() < deadline:
            if all(_proc_identity(pid) is None for pid in owned):
                process.wait(timeout=2)
                return
            time.sleep(0.05)
    raise AssertionError(("owned candidate process did not stop", process.pid))


def _hashes(root: Path) -> dict[str, str]:
    names = (
        "runtime/infrastructure/database.py",
        "runtime/orchestrator/run_step.py",
        "runtime/daemon/routes/tasks.py",
        "runtime/daemon/routes/threads.py",
        "runtime/daemon/queue.py",
        "cli/main.py",
    )
    return {name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in names}


def _seed_org(container: Path, slug: str) -> Path:
    root = container / "orgs" / slug
    (root / "org" / "agents").mkdir(parents=True, exist_ok=True)
    (root / "workspaces").mkdir(parents=True, exist_ok=True)
    (root / "kb").mkdir(parents=True, exist_ok=True)
    (root / "org" / "teams.yaml").write_text(
        "teams:\n  engineering:\n    manager: engineering_head\n"
        "    workers: [dev_agent]\n"
    )
    return root


def _write_driver(root: Path) -> None:
    (root / "provider_driver.py").write_text(
        """from __future__ import annotations
import json, os, pathlib, subprocess, sys, time, urllib.request
from runtime.daemon import paths
task, session, slug, agent = sys.argv[1:]
root = pathlib.Path(os.environ['PROOF_ROOT'])
port = paths.port_file().read_text().strip()
base = 'http://127.0.0.1:' + port + '/api/v1/orgs/' + slug
headers = {'Authorization': 'Bearer ' + paths.read_token()}
def view():
    req = urllib.request.Request(base + '/tasks/' + task, headers=headers)
    with urllib.request.urlopen(req, timeout=2) as response:
        return json.load(response)
task_view = view()
assert task_view['task']['assigned_agent'] == agent
brief = task_view['task']['brief']
prior = [row for row in task_view['results'] if row['agent'] == agent and row['session_id']]
def complete(decision=None):
    payload = {'status':'completed','summary':'isolated proof complete'}
    if decision is not None:
        payload['decision'] = decision
    return payload
if agent == 'dev_agent':
    if brief == 'proof-fail':
        action = {'status':'blocked','summary':'intentional isolated worker failure'}
    elif brief == 'proof-pass':
        action = complete()
    else:
        raise AssertionError(('unexpected worker brief', brief))
elif agent == 'engineering_head':
    if brief == 'proof-origin':
        action = complete({'action':'escalate','reason':'isolated retry prerequisite'}) if prior else complete(
            {'action':'delegate','agent':'dev_agent','prompt':'proof-fail'})
    elif brief == 'proof-unrelated':
        action = complete({'action':'done','summary':'unrelated done'}) if prior else complete(
            {'action':'delegate','agent':'dev_agent','prompt':'proof-pass'})
    elif brief == 'proof-intermediate':
        action = complete({'action':'escalate','reason':'second isolated hop'})
    elif brief == 'proof-successor':
        context = json.loads((root / 'context.json').read_text())
        action = complete({'action':'done','summary':'proof done'}) if prior else complete({
            'action':'delegate','agent':'dev_agent','prompt':'proof-pass',
            'revisit_of_task_id':context.get('link', context['failed_child'])})
    elif brief == 'proof-hold':
        (root / 'active-binding.json').write_text(json.dumps(
            {'task_id':task,'session_id':session,'agent':agent,'org':slug}))
        deadline = time.monotonic() + 10
        while not (root / 'release').exists():
            if time.monotonic() >= deadline:
                raise TimeoutError('negative callback fixture release')
            time.sleep(.05)
        action = complete({'action':'done','summary':'negative callback finished'})
    else:
        raise AssertionError(('unexpected manager brief', brief))
else:
    raise AssertionError(('unexpected provider agent', agent))
payload = {'task_id':task,'session_id':session,'agent':agent,
           'status':action['status'],'confidence':90,'summary':action['summary']}
if 'decision' in action:
    payload['decision'] = action['decision']
target = root / (task + '-' + session + '.json')
target.write_text(json.dumps(payload))
subprocess.run(['happyranch','report-completion','--org',slug,'--from-file',str(target)],
               check=True, timeout=5)
"""
    )


def _write_launcher(root: Path) -> Path:
    launcher = root / "launcher.py"
    launcher.write_text(
        """from __future__ import annotations
import hashlib, importlib.metadata, json, os, pathlib, runpy, sys, threading, time
import cli.main, runtime
from runtime.daemon.queue import TaskQueue
from runtime.infrastructure.database import Database
expected = pathlib.Path(os.environ['EXPECTED_SOURCE']).resolve()
source = pathlib.Path(runtime.__file__).resolve().parent.parent
assert source == expected
assert pathlib.Path(cli.main.__file__).resolve() == source / 'cli/main.py'
receipt = {'python':sys.executable,'runtime':str(pathlib.Path(runtime.__file__).resolve()),
           'cli':str(pathlib.Path(cli.main.__file__).resolve()),
           'version':importlib.metadata.version('happyranch'),'source':str(source)}
root = pathlib.Path(os.environ['PROOF_ROOT'])
(root / 'import-receipt.json').write_text(json.dumps(receipt, sort_keys=True))
trace = root / 'queue-claim.jsonl'
lock = threading.Lock()
def event(kind, **fields):
    with lock, trace.open('a') as output:
        output.write(json.dumps({'kind':kind,'at_ns':time.monotonic_ns(),**fields})+'\\n')
original_enqueue = TaskQueue.enqueue
def observed_enqueue(self, slug, task_id, *, metadata=None):
    event('queue_attempt',slug=slug,task_id=task_id)
    answer = original_enqueue(self,slug,task_id,metadata=metadata)
    event('queue_inserted',slug=slug,task_id=task_id)
    return answer
TaskQueue.enqueue = observed_enqueue
original_claim = Database.try_claim_for_step
def observed_claim(self, task_id, expected_status, expected_block_kind, new_count):
    answer = original_claim(self,task_id,expected_status,expected_block_kind,new_count)
    event('claim',task_id=task_id,count=new_count,won=answer)
    return answer
Database.try_claim_for_step = observed_claim
runpy.run_module('runtime.daemon',run_name='__main__')
"""
    )
    return launcher


def test_installed_verified_supersession_retry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Installed candidate: real Codex/CLI callback through verified cross-root retry."""
    if not Path("/proc").is_dir():
        pytest.skip("assigned installed identity proof requires Linux /proc")
    candidate_python = Path(os.environ["CANDIDATE_PY"]).absolute()
    expected_source = Path(os.environ["EXPECTED_SOURCE"]).resolve()
    expected_sha = os.environ["EXPECTED_SHA"]
    assert candidate_python.is_file()
    assert subprocess.run(
        ["git", "-C", str(expected_source), "rev-parse", "HEAD"],
        check=True, capture_output=True, text=True,
    ).stdout.strip() == expected_sha
    assert subprocess.run(
        ["git", "-C", str(expected_source), "status", "--porcelain"],
        check=True, capture_output=True, text=True,
    ).stdout == ""
    before_hashes = _hashes(expected_source)

    proof = tmp_path / "proof"
    proof.mkdir()
    daemon_home = proof / ".happyranch"
    container = RuntimeDir.init(proof / "runtime").root
    test_org = _seed_org(container, "test")
    _seed_org(container, "other")
    seed_workspace(test_org, "engineering_head", executor="codex")
    seed_workspace(test_org, "dev_agent", executor="codex")

    from tests.helpers.integration_stub_guard.guard import manifest
    # The source-hashed parent stub is already provisioned beside its bound
    # Python/callback wrappers. Moving just the stub loses that closed route.
    binding = manifest()
    _write_driver(proof)
    launcher = _write_launcher(proof)
    plan = proof / "plan.sh"
    _write_executable(
        plan,
        "#!/usr/bin/env bash\nset -euo pipefail\n"
        "task_id=$1\nsession_id=$2\norg_slug=$3\nagent=${PWD##*/}\n"
        '"$CANDIDATE_PY" "$PROOF_ROOT/provider_driver.py" '
        '"$task_id" "$session_id" "$org_slug" "$agent"\n',
    )

    from tests.helpers.deterministic_plan import approve_plan
    approve_plan(plan)
    monkeypatch.setenv("HAPPYRANCH_DAEMON_HOME", str(daemon_home))
    monkeypatch.setenv("HAPPYRANCH_DAEMON_PORT", "0")
    test_manifest = proof / "stub-manifest.json"
    test_manifest.write_text(json.dumps(binding, sort_keys=True))
    test_manifest.chmod(0o600)
    monkeypatch.setenv("HAPPYRANCH_TEST_PARENT_MANIFEST", str(test_manifest))
    save_registry({p: row["path"] for p, row in binding["stubs"].items()})
    environment = os.environ.copy()
    environment.update({
        "HAPPYRANCH_DAEMON_HOME": str(daemon_home),
        "HAPPYRANCH_DAEMON_PORT": "0",
        "HAPPYRANCH_EXECUTOR_LAUNCH_SPACING_SECONDS": "0",
        "FAKE_CODEX_PLAN": str(plan),
        "CANDIDATE_PY": str(candidate_python),
        "EXPECTED_SOURCE": str(expected_source),
        "EXPECTED_SHA": expected_sha,
        "PROOF_ROOT": str(proof),
        "PATH": environment["PATH"],
    })
    environment.pop("HAPPYRANCH_TASK_TMP_ROOT", None)
    environment.pop("HAPPYRANCH_TASK_SCRATCH_MANIFEST", None)
    stdout_log = (proof / "daemon.stdout").open("wb")
    stderr_log = (proof / "daemon.stderr").open("wb")
    process = subprocess.Popen(
        [str(candidate_python), str(launcher)], cwd=proof, env=environment,
        start_new_session=True, stdout=stdout_log, stderr=stderr_log,
    )
    saved = _proc_identity(process.pid, daemon_home)
    assert saved is not None
    try:
        deadline = time.monotonic() + 10
        while not paths.port_file().exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        assert paths.port_file().exists()
        port = paths.port_file().read_text().strip()
        global_base = f"http://127.0.0.1:{port}/api/v1"
        _wait_health(global_base)
        headers = {"Authorization": f"Bearer {paths.read_token()}"}
        _request(global_base, headers, "POST", "/runtime", {"path": str(container)})
        _init_agent(global_base, headers, "engineering_head")
        _init_agent(global_base, headers, "dev_agent")
        assert subprocess.run(
            [str(candidate_python), "-m", "cli.main", "--help"], cwd=proof,
            env=environment, capture_output=True, timeout=5,
        ).returncode == 0

        positive_successors: list[str] = []
        for hops in (1, 2):
            origin = _submit(global_base, headers, "proof-origin")
            _wait_task(global_base, headers, origin, "escalated")
            failed_children = _children(global_base, headers, origin)
            assert len(failed_children) == 1
            failed = failed_children[0]
            failed_before = _wait_task(global_base, headers, failed, "failed")
            (proof / "context.json").write_text(json.dumps(
                {"origin": origin, "failed_child": failed}
            ))
            if hops == 2:
                middle = _supersede(global_base, headers, origin, "proof-intermediate")
                _wait_task(global_base, headers, middle, "escalated")
                successor = _supersede(global_base, headers, middle, "proof-successor")
            else:
                successor = _supersede(global_base, headers, origin, "proof-successor")
            positive_successors.append(successor)
            _wait_task(global_base, headers, successor, "completed")
            retried = _children(global_base, headers, successor)
            assert len(retried) == 1
            retry_view = _wait_task(global_base, headers, retried[0], "completed")
            assert retry_view["task"]["parent_task_id"] == successor
            assert retry_view["task"]["revisit_of_task_id"] == failed
            assert retry_view["task"]["assigned_agent"] == "dev_agent"
            failed_after = _request(
                global_base, headers, "GET", f"/orgs/test/tasks/{failed}"
            )
            assert failed_after["task"] == failed_before["task"]
            assert failed_after["results"] == failed_before["results"]

        unrelated = _submit(global_base, headers, "proof-unrelated")
        _wait_task(global_base, headers, unrelated, "completed")
        unrelated_child = _children(global_base, headers, unrelated)
        assert len(unrelated_child) == 1
        invalid_origin = _submit(global_base, headers, "proof-origin")
        _wait_task(global_base, headers, invalid_origin, "escalated")
        invalid_failed = _children(global_base, headers, invalid_origin)[0]
        _wait_task(global_base, headers, invalid_failed, "failed")
        (proof / "context.json").write_text(json.dumps({
            "origin": invalid_origin, "failed_child": invalid_failed,
            "link": unrelated_child[0],
        }))
        invalid_successor = _supersede(
            global_base, headers, invalid_origin, "proof-successor"
        )
        invalid_view = _wait_task(global_base, headers, invalid_successor, "completed")
        assert _children(global_base, headers, invalid_successor) == []
        feedback = [row for row in invalid_view["results"] if row["session_id"] == ""]
        assert len(feedback) == 1
        assert "retry_not_failed" in feedback[0]["output_summary"]

        release = proof / "release"
        release.unlink(missing_ok=True)
        binding_path = proof / "active-binding.json"
        binding_path.unlink(missing_ok=True)
        held = _submit(global_base, headers, "proof-hold")
        deadline = time.monotonic() + 10
        while not binding_path.exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        binding = json.loads(binding_path.read_text())
        assert binding["task_id"] == held
        wrong = {
            "session_id": "wrong-session", "agent": "engineering_head",
            "status": "completed", "confidence": 90, "output_summary": "negative",
        }
        wrong_session = _request(
            global_base, headers, "POST", f"/orgs/test/tasks/{held}/completion",
            wrong, status=409,
        )
        assert wrong_session["detail"]["code"] == "session_mismatch"
        wrong_org = _request(
            global_base, headers, "POST", f"/orgs/other/tasks/{held}/completion",
            {**wrong, "session_id": binding["session_id"]}, status=404,
        )
        assert wrong_org["detail"]["code"] == "unknown_task"
        release.write_text("release")
        _wait_task(global_base, headers, held, "completed")

        trace = [json.loads(line) for line in (proof / "queue-claim.jsonl").read_text().splitlines()]
        invalid_insertions = [
            row for row in trace
            if row["kind"] == "queue_inserted" and row["task_id"] == invalid_successor
        ]
        assert len(invalid_insertions) >= 2  # initial successor + refusal self-enqueue
        assert any(
            row["kind"] == "claim" and row["task_id"] == invalid_successor and row["won"]
            for row in trace
        )

        db_path = test_org / "happyranch.db"
        with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as connection:
            for successor in positive_successors:
                actions = [row[0] for row in connection.execute(
                    "SELECT action FROM audit_log WHERE task_id=?", (successor,)
                )]
                assert "orchestration_step" in actions
    finally:
        try:
            _stop_owned(process, daemon_home, saved)
        finally:
            stdout_log.close()
            stderr_log.close()

    assert _hashes(expected_source) == before_hashes
    assert json.loads((proof / "import-receipt.json").read_text())["source"] == str(
        expected_source
    )
