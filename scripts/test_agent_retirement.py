"""Focused no-launch retirement checks; see docs/local-ci.md. No pytest imports."""
from __future__ import annotations

import argparse
import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, contextmanager
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import sys
import tempfile
import traceback
from unittest.mock import patch

# Direct invocation finds this checkout, never an installed/live source tree.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx
import yaml

from runtime.config import Settings
from runtime.daemon.app import create_app
from runtime.daemon.state import DaemonState
from runtime.daemon.org_state import OrgState
from runtime.daemon import paths as daemon_paths
from runtime.models import (BlockKind, DreamRecord, DreamStatus, ScheduleKind, ScheduleRecord, TaskRecord,
                            TaskStatus, WorkHourMode, WorkHourRecord, WorkHourStatus)
from runtime.orchestrator.teams import TeamsRegistry
from runtime.orchestrator.authority_policy_store import AuthorityPolicyStore
from runtime.runtime import RuntimeDir

PREFIX = "/api/v1/orgs/alpha"
HEADERS = {"Authorization": "Bearer synthetic-founder-token"}


def files(root: Path) -> dict[str, bytes]:
    return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file()}


def rows(org: OrgState, table: str) -> list[tuple]:
    return [tuple(row) for row in org.db.execute(f"SELECT * FROM {table} ORDER BY 1").fetchall()]


def persistent(org: OrgState) -> tuple:
    return (files(org.root / "org" / "agents"), files(org.root / "workspaces"),
            (org.root / "org" / "teams.yaml").read_bytes(),
            [(name, org.teams.manager_for_team(name)) for name in org.teams.teams()],
            {table: rows(org, table) for table in ("tasks", "task_results", "schedules", "work_hours", "dreams")})


def seed_future(org: OrgState, name: str = "worker") -> None:
    now = datetime.now(timezone.utc)
    org.db.schedules.insert(ScheduleRecord(
        id="SCH-001", agent_name=name, team="retiring", kind=ScheduleKind.ONE_SHOT,
        fire_at=now + timedelta(days=1), normalized_brief="Synthetic future work",
        source_instruction="Synthetic future work"))
    org.db.work_hours.insert(WorkHourRecord(
        id="WH-001", agent_name=name, local_date=now.date().isoformat(), slot="test",
        mode=WorkHourMode.CONTINUOUS, scheduled_for=now))
    org.db.insert_dream(DreamRecord(
        id="DRM-001", agent_name=name, local_date=now.date().isoformat(),
        scheduled_for=now, window_end=now + timedelta(hours=1)))


@asynccontextmanager
async def fixture(scratch: Path, *, workers: tuple[str, ...] = ("worker",), pending: bool = False,
                  default: str = "default", task_default: str = "engineering",
                  legacy: bool = False) -> AsyncIterator[tuple[OrgState, httpx.AsyncClient]]:
    scratch.mkdir()
    home = scratch / "home"
    home.mkdir()
    with patch.dict(os.environ, {"HAPPYRANCH_DAEMON_HOME": str(home)}):
        daemon_paths.ensure_daemon_home()
        daemon_paths.token_file().write_text("synthetic-founder-token")
        runtime = RuntimeDir.init(scratch / "runtime")
        root = runtime.orgs_dir / "alpha"
        agents = root / "org" / "agents"
        agents.mkdir(parents=True)
        (root / "org" / "teams.yaml").write_text(yaml.safe_dump({
            "default_team": default, "task_default_team": task_default,
            "teams": {"default": {"manager": {"kind": "human", "principal": "founder"}, "workers": []},
                      "engineering": {"manager": "keeper", "workers": ["other"]},
                      "retiring": {"manager": {"kind": "agent", "principal": "manager"},
                                   "workers": list(workers)}}}))
        if legacy:
            team_path = root / "org" / "teams.yaml"
            candidate = yaml.safe_load(team_path.read_text())
            del candidate["default_team"], candidate["task_default_team"]
            candidate["teams"]["retiring"]["manager"] = "manager"
            team_path.write_text(yaml.safe_dump(candidate))
        (root / "org" / "config.yaml").write_text("reviewer_agents: [other]\n")
        roster = [("keeper", "engineering", "manager"), ("other", "engineering", "worker"),
                  ("manager", "retiring", "manager")]
        roster += [(name, "retiring", "worker") for name in workers]
        for name, team, role in roster:
            target = agents / "_pending" if pending and name in workers else agents
            target.mkdir(exist_ok=True)
            (target / f"{name}.md").write_text(
                f"---\nname: {name}\nteam: {team}\nrole: {role}\nexecutor: claude\n---\nSynthetic role.\n")
            workspace = root / "workspaces" / name
            (workspace / "memory").mkdir(parents=True)
            (workspace / "memory" / "lesson.md").write_text("Synthetic retained memory.\n")
            (workspace / "notes.md").write_text("Synthetic retained notes.\n")
        state = DaemonState.from_runtime(runtime, Settings())
        try:
            org = state.orgs["alpha"]
            org.workflow_authority.verify_admission_ready()
            # ASGI transport does not run lifespan: no background queues or executors.
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(state)),
                                         base_url="http://synthetic.test", headers=HEADERS) as client:
                yield org, client
        finally:
            await state.close_all()


async def retire(client: httpx.AsyncClient, name: str = "worker") -> httpx.Response:
    return await client.post(f"{PREFIX}/agents/{name}/retire")


async def expect_refusal(org: OrgState, client: httpx.AsyncClient, name: str, code: str, status: int = 409) -> dict:
    before = persistent(org)
    response = await retire(client, name)
    assert response.status_code == status, response.text
    assert response.json()["detail"]["code"] == code, response.text
    assert persistent(org) == before
    return response.json()["detail"]


async def worker_success(org: OrgState, client: httpx.AsyncClient) -> None:
    seed_future(org)
    org.db.insert_task(TaskRecord(id="TASK-history", assigned_agent="worker", team="retiring",
                                 brief="Synthetic history", status=TaskStatus.COMPLETED))
    before = persistent(org)
    response = await retire(client)
    assert response.status_code == 200, response.text
    assert response.json() == {"ok": True, "status": "terminated", "name": "worker",
                              "role": "worker", "team": "retiring", "team_retired": False}
    assert not (org.root / "org/agents/worker.md").exists()
    assert (org.root / "org/agents/_terminated/worker.md").read_bytes() == before[0]["worker.md"]
    assert not (org.root / "workspaces/worker").exists()
    for relative, data in before[1].items():
        target = "_terminated/" + relative if relative.startswith("worker/") else relative
        assert (org.root / "workspaces" / target).read_bytes() == data
    assert rows(org, "tasks") == before[4]["tasks"]
    assert rows(org, "task_results") == before[4]["task_results"]
    assert org.teams.manager_for_team("retiring").workers == ()
    assert org.db.schedules.get("SCH-001").status.value == "cancelled"
    assert org.db.work_hours.get("WH-001").status.value == "skipped"
    assert org.db.get_dream("DRM-001").status.value == "skipped"
    audit = org.db.execute("SELECT task_id, agent, payload FROM audit_log WHERE action='agent_managed'").fetchall()
    assert [(row["task_id"], row["agent"], json.loads(row["payload"])) for row in audit] == [
        ("founder", "founder", {"action": "terminate", "name": "worker", "source": "founder"})]
    listing = await client.get(f"{PREFIX}/agents/enrollments", params={"status": "terminated"})
    assert listing.status_code == 200 and any(row["name"] == "worker" for row in listing.json()["enrollments"])


async def worker_then_manager(org: OrgState, client: httpx.AsyncClient) -> None:
    policy = AuthorityPolicyStore(org.db)
    org.workflow_authority.ensure_authority_selector(team="retiring", publisher="synthetic-retirement-fixture")
    selector, history = policy.get_authority_selector("retiring"), policy.list_selector_history("retiring")
    assert selector is not None and history
    before = org.teams.team_row("engineering"), org.teams.team_row("default")
    assert (await retire(client)).status_code == 200
    manager_file = (org.root / "org/agents/manager.md").read_bytes()
    response = await retire(client, "manager")
    assert response.status_code == 200, response.text
    assert response.json()["team_retired"] is True
    assert (org.root / "org/agents/_terminated/manager.md").read_bytes() == manager_file
    assert (org.root / "workspaces/_terminated/manager/memory/lesson.md").read_text() == "Synthetic retained memory.\n"
    assert org.teams.teams() == ["default", "engineering"]
    assert (org.teams.team_row("engineering"), org.teams.team_row("default")) == before
    fresh = TeamsRegistry.load(org.root)
    assert policy.get_authority_selector("retiring") == selector
    assert policy.list_selector_history("retiring") == history
    assert fresh.teams() == org.teams.teams()
    assert fresh.default_team == org.teams.default_team == "default"
    assert fresh.task_default_team == org.teams.task_default_team == "engineering"
    canonical = org.workflow_authority.capture_canonical_snapshot()
    ready = org.workflow_authority.verify_admission_ready()
    assert ready.snapshot_bytes == canonical
    snapshot = json.loads(canonical)
    assert [team["name"] for team in snapshot["teams"]] == ["default", "engineering"]
    assert [item["team"] for item in snapshot["active_policy_selectors"]] == ["default", "engineering"]
    assert (await retire(client, "founder")).status_code == 404
    assert org.teams.manager_for_team("default").kind == "human"


async def auth_and_unknown(org: OrgState, client: httpx.AsyncClient) -> None:
    org.sessions.set_active("TASK-auth", "manager", "sess-synthetic")
    before = persistent(org)
    for authorization in (None, "Bearer synthetic-wrong-token"):
        request = client.build_request("POST", f"{PREFIX}/agents/worker/retire",
                                       params={"task_id": "TASK-auth", "session_id": "sess-synthetic"})
        request.headers.pop("Authorization")
        if authorization is not None:
            request.headers["Authorization"] = authorization
        response = await client.send(request)
        assert response.status_code == 403, response.text
        assert response.json()["detail"]["code"] == "human_only"
        assert persistent(org) == before
    assert (await retire(client, "unknown")).status_code == 404
    assert persistent(org) == before


async def busy(org: OrgState, client: httpx.AsyncClient, name: str) -> None:
    org.db.insert_task(TaskRecord(id="TASK-busy", assigned_agent=name, team="engineering", brief="Synthetic busy"))
    detail = await expect_refusal(org, client, name, "agent_not_quiescent")
    assert {"kind": "task", "id": "TASK-busy"} in detail["conflicts"]


async def historical_blocker(org: OrgState, client: httpx.AsyncClient, kind: str,
                             state: str = "running", manager_session: bool = False) -> None:
    old = datetime(2020, 1, 1, tzinfo=timezone.utc)
    if kind == "task":
        blocked = state == "blocked"
        org.db.insert_task(TaskRecord(
            id="TASK-live", assigned_agent="worker", team="retiring", brief="Synthetic live task",
            status=TaskStatus.IN_PROGRESS if blocked else TaskStatus(state),
            block_kind=BlockKind.BLOCKED_ON_JOB if blocked else None, created_at=old))
        org.db.execute(
            "WITH RECURSIVE history(n) AS (VALUES(1) UNION ALL SELECT n+1 FROM history WHERE n<10001) "
            "INSERT INTO tasks(id, assigned_agent, team, brief, status, created_at, updated_at) "
            "SELECT 'TASK-history-' || n, 'worker', 'retiring', 'Synthetic history', 'completed', ?, ? FROM history",
            (old.replace(year=2021).isoformat(), old.replace(year=2021).isoformat()))
        live_id, table = "TASK-live", "tasks"
    else:
        seed_future(org)
        table, live_id = ("dreams", "DRM-001") if kind == "dream" else ("work_hours", "WH-001")
        if kind == "dream":
            org.db.update_dream(live_id, status=DreamStatus.RUNNING)
        else:
            org.db.work_hours.update(live_id, status=WorkHourStatus.RUNNING)
        org.db.execute(f"UPDATE {table} SET scheduled_for=? WHERE id=?", (old.isoformat(), live_id))
        columns = [row["name"] for row in org.db.execute(f"PRAGMA table_info({table})").fetchall()]
        expressions = ["'history-' || n" if c == "id" else "'completed'" if c == "status"
                       else "'2021-01-01T00:00:00+00:00'" if c == "scheduled_for"
                       else "'history-' || n" if c == "local_date" else c for c in columns]
        org.db.execute(
            "WITH RECURSIVE history(n) AS (VALUES(1) UNION ALL SELECT n+1 FROM history WHERE n<501) "
            f"INSERT INTO {table}({','.join(columns)}) SELECT {','.join(expressions)} "
            f"FROM {table} CROSS JOIN history WHERE id=?", (live_id,))
    with org.db._lock:
        org.db._conn.commit()
    # Prove that the real retained history exceeds the old presentation cap.
    assert org.db.execute(f"SELECT count(*) FROM {table} WHERE status='completed'").fetchone()[0] > (
        10000 if kind == "task" else 500)
    before = persistent(org), rows(org, "audit_log")
    if manager_session:
        org.sessions.set_active("TASK-owner", "manager", "sess-synthetic")
        response = await client.post(f"{PREFIX}/agents/manage", json={
            "action": "terminate", "name": "worker", "task_id": "TASK-owner", "session_id": "sess-synthetic"})
    else:
        response = await retire(client)
    assert response.status_code == 409, response.text
    assert response.json()["detail"]["code"] == "agent_not_quiescent", response.text
    assert {"kind": kind, "id": live_id} in response.json()["detail"]["conflicts"]
    assert (persistent(org), rows(org, "audit_log")) == before


async def lease_scope(org: OrgState, client: httpx.AsyncClient, name: str, rollback: bool) -> None:
    capture = org.workflow_authority.capture_canonical_snapshot
    observations = []
    def observed_capture() -> bytes:
        leases = org.db.execute("SELECT count(*) FROM workflow_publication_leases").fetchone()[0]
        observations.append(leases)
        return capture()
    with patch.object(org.workflow_authority, "capture_canonical_snapshot", side_effect=observed_capture):
        if rollback:
            await cleanup_rollback(org, client, name)
        else:
            response = await retire(client, name)
            assert response.status_code == 200, response.text
            assert capture() == org.workflow_authority.verify_admission_ready().snapshot_bytes
    assert observations and not any(observations), f"discovery publication lease counts: {observations}"


async def leased_revalidation(org: OrgState, client: httpx.AsyncClient) -> None:
    from runtime.workflows.authority import _SupportedWriterInterval
    canonical_change = _SupportedWriterInterval.canonical_change
    changed = False
    expected = None
    @contextmanager
    def arriving_change(interval):
        nonlocal changed, expected
        if not changed:
            changed = True
            path = org.root / "org/teams.yaml"
            candidate = yaml.safe_load(path.read_text())
            candidate["default_team"] = "engineering"
            path.write_text(yaml.safe_dump(candidate))
            org.teams.adopt_canonical(TeamsRegistry.load(org.root))
            expected = persistent(org)
        with canonical_change(interval):
            yield
    # Inject an arriving canonical change at the native lease boundary, while
    # keeping the real lease/fence, HTTP route and authority publication intact.
    with patch.object(_SupportedWriterInterval, "canonical_change", arriving_change):
        response = await retire(client)
    assert changed and response.status_code == 409, response.text
    assert response.json()["detail"]["code"] == "retirement_state_changed", response.text
    assert persistent(org) == expected
    assert org.workflow_authority.capture_canonical_snapshot() == org.workflow_authority.verify_admission_ready().snapshot_bytes


async def members(org: OrgState, client: httpx.AsyncClient) -> None:
    detail = await expect_refusal(org, client, "manager", "team_not_empty")
    assert detail["members"] == ["worker"]


async def open_task(org: OrgState, client: httpx.AsyncClient, status: TaskStatus) -> None:
    org.db.insert_task(TaskRecord(id="TASK-team", assigned_agent="other", team="retiring", brief="Synthetic team work", status=status))
    detail = await expect_refusal(org, client, "manager", "team_has_open_tasks")
    assert detail["conflicts"] == [{"kind": "task", "id": "TASK-team", "status": status.value}]


async def cleanup_rollback(org: OrgState, client: httpx.AsyncClient, name: str) -> None:
    seed_future(org, name)
    org.db.execute("CREATE TRIGGER synthetic_cleanup_fault BEFORE UPDATE ON work_hours BEGIN SELECT RAISE(ABORT, 'synthetic cleanup failure'); END")
    await expect_refusal(org, client, name, "terminate_cleanup_failed", status=500)
    assert org.workflow_authority.capture_canonical_snapshot() == org.workflow_authority.verify_admission_ready().snapshot_bytes


async def archive_collision(org: OrgState, client: httpx.AsyncClient) -> None:
    archive = org.root / "org/agents/_terminated"
    archive.mkdir()
    (archive / "worker.md").write_text("Synthetic existing archive")
    await expect_refusal(org, client, "worker", "archive_collision")


async def workspace_failure(org: OrgState, client: httpx.AsyncClient) -> None:
    with patch("runtime.daemon.routes.agents._move_dir_atomically", side_effect=OSError("synthetic move failure")):
        await expect_refusal(org, client, "worker", "workspace_archive_failed", status=500)


async def manager_compatibility(org: OrgState, client: httpx.AsyncClient) -> None:
    org.sessions.set_active("TASK-owner", "manager", "sess-synthetic")
    async def terminate(name: str) -> httpx.Response:
        return await client.post(f"{PREFIX}/agents/manage", json={"action": "terminate", "name": name,
                                 "task_id": "TASK-owner", "session_id": "sess-synthetic"})
    before = persistent(org)
    response = await terminate("manager")
    assert response.status_code == 409 and response.json()["detail"]["code"] == "manager_terminate_forbidden"
    assert persistent(org) == before
    response = await terminate("other")
    assert response.status_code == 403 and response.json()["detail"] == {
        "code": "cross_team_forbidden", "caller_team": "retiring", "agent_team": "engineering"}
    assert persistent(org) == before
    response = await terminate("worker")
    assert response.status_code == 200 and response.json() == {"ok": True, "status": "terminated"}
    assert not (org.root / "org/agents/worker.md").exists()
    assert (org.root / "org/agents/_terminated/worker.md").read_bytes() == before[0]["worker.md"]
    assert (org.root / "workspaces/_terminated/worker/notes.md").read_bytes() == before[1]["worker/notes.md"]
    audit = org.db.execute("SELECT task_id, agent, payload FROM audit_log WHERE action='agent_managed'").fetchone()
    assert (audit["task_id"], audit["agent"], json.loads(audit["payload"])) == (
        "TASK-owner", "manager", {"action": "terminate", "name": "worker", "source": "task"})


async def locked_recheck(org: OrgState, client: httpx.AsyncClient, kind: str) -> None:
    reached, release = asyncio.Event(), asyncio.Event()
    native_lock = org.teams_lock
    @asynccontextmanager
    async def delayed_lock() -> AsyncIterator[None]:
        reached.set()
        await release.wait()
        async with native_lock:
            yield
    # Test-side scheduling only: the shipping writer interval still runs in full.
    org.teams_lock = delayed_lock()
    name = "worker" if kind == "busy" else "manager"
    request = asyncio.create_task(retire(client, name))
    observed = asyncio.create_task(reached.wait())
    try:
        completed, _ = await asyncio.wait((request, observed), timeout=10, return_when=asyncio.FIRST_COMPLETED)
        assert completed, "route did not reach the lock or return within 10s"
        if request in completed:
            response = request.result()
            assert response.status_code == 409, response.text
            raise AssertionError("expected the route to validate again after acquiring the lock")
    finally:
        observed.cancel()
        await asyncio.gather(observed, return_exceptions=True)
    if kind == "busy":
        org.db.insert_task(TaskRecord(id="TASK-race", assigned_agent=name, team="engineering", brief="Synthetic race"))
        code = "agent_not_quiescent"
    elif kind == "team-task":
        org.db.insert_task(TaskRecord(id="TASK-race", assigned_agent="other", team="retiring", brief="Synthetic race"))
        code = "team_has_open_tasks"
    elif kind == "member":
        pending = org.root / "org/agents/_pending"
        pending.mkdir()
        (pending / "arriving.md").write_text("---\nname: arriving\nteam: retiring\nrole: worker\nexecutor: claude\n---\nSynthetic arriving worker.\n")
        org.teams.add_worker("retiring", "arriving")
        code = "team_not_empty"
    else:
        candidate = yaml.safe_load((org.root / "org/teams.yaml").read_text())
        candidate["task_default_team"] = "retiring"
        (org.root / "org/teams.yaml").write_text(yaml.safe_dump(candidate))
        org.teams.adopt_canonical(TeamsRegistry.load(org.root))
        code = "task_default_team_retire_forbidden"
    before = persistent(org)
    release.set()
    response = await request
    assert response.status_code == 409 and response.json()["detail"]["code"] == code, response.text
    assert persistent(org) == before


async def main() -> int:
    cases = [
        ("worker-preservation", {}, worker_success, ()),
        ("worker-then-manager-authority", {}, worker_then_manager, ()),
        ("founder-auth-and-unknown", {}, auth_and_unknown, ()),
        ("worker-busy", {}, busy, ("worker",)),
        ("manager-busy", {"workers": ()}, busy, ("manager",)),
        ("manager-members", {}, members, ()),
        ("manager-pending-members", {"pending": True}, members, ()),
        ("default-team", {"workers": (), "default": "retiring"}, expect_refusal, ("manager", "default_team_retire_forbidden")),
        ("task-default-team", {"workers": (), "task_default": "retiring"}, expect_refusal, ("manager", "task_default_team_retire_forbidden")),
        ("worker-cleanup-rollback", {}, cleanup_rollback, ("worker",)),
        ("manager-cleanup-rollback", {"workers": ()}, cleanup_rollback, ("manager",)),
        ("archive-collision", {}, archive_collision, ()),
        ("workspace-failure", {}, workspace_failure, ()),
        ("manager-session-compatibility", {}, manager_compatibility, ()),
    ]
    cases += [("team-task-" + status.value, {"workers": ()}, open_task, (status,))
              for status in (TaskStatus.PENDING, TaskStatus.IN_PROGRESS, TaskStatus.ESCALATED)]
    cases += [("locked-recheck-" + kind, {} if kind == "busy" else {"workers": ()}, locked_recheck, (kind,))
              for kind in ("busy", "team-task", "member", "pointer")]
    cases += [("history-task-" + state + suffix, {}, historical_blocker, ("task", state, manager_session))
              for state in ("pending", "escalated", "in_progress", "blocked")
              for suffix, manager_session in (("", False), ("-manage", True))]
    cases += [("history-" + kind, {}, historical_blocker, (kind,)) for kind in ("dream", "work_hour")]
    cases += [("lease-" + name + ("-rollback" if rollback else "-success"),
               {} if name == "worker" else {"workers": (), "legacy": True}, lease_scope, (name, rollback))
              for name in ("worker", "manager") for rollback in (False, True)]
    cases += [("lease-revalidate-arriving-team", {}, leased_revalidation, ())]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", action="append", choices=[case[0] for case in cases])
    selected = parser.parse_args().case
    if selected:
        cases = [case for case in cases if case[0] in selected]
    failures = []
    with tempfile.TemporaryDirectory(prefix="agent-retirement-") as scratch:
        for index, (name, options, check, args) in enumerate(cases):
            try:
                async with fixture(Path(scratch) / str(index), **options) as (org, client):
                    await check(org, client, *args)
            except Exception:
                failures.append(name)
                print("FAIL", name, flush=True)
                traceback.print_exc()
            else:
                print("PASS", name, flush=True)
    print(f"RESULT {len(cases) - len(failures)} passed; {len(failures)} failed; {len(cases)} cases")
    print("NOT RUN: real daemon/CLI/UI acceptance, retired suites, general integration, Product E2E")
    return int(bool(failures))


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
