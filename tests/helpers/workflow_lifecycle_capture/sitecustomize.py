"""Source-bound U3 observation/fault windows, only in an isolated test daemon.

No result, visit, proof, event, control or wake is written by this observer.
Native owners and the public callback remain the only lifecycle writers.
"""
from __future__ import annotations

import asyncio
import functools
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import sys
import threading
import time
from types import SimpleNamespace

SOURCE_FILES = (
    "runtime/workflows/sequential_dispatch.py",
    "runtime/workflows/sequential_settlement.py",
    "runtime/orchestrator/task_pause.py",
    "runtime/orchestrator/orchestrator.py",
    "runtime/orchestrator/workspace_adapters.py",
    "runtime/orchestrator/task_scratch.py",
    "runtime/workflows/sequential_launch_failures.py",
)


def _json(path: Path) -> dict:
    return json.loads(path.read_bytes())


def _write(path: Path, value: object) -> None:
    pending = path.with_suffix(".pending")
    pending.write_text(json.dumps(value, sort_keys=True))
    pending.replace(path)


def _sanitize(raw: bytes) -> str:
    text = raw.decode("utf8", errors="replace")
    text = re.sub(r"(?i)Bearer\s+\S+", "Bearer [REDACTED]", text)
    return re.sub(r"(?i)(daemon\.token|authorization|password|api[_-]?key)\s*[:=]\s*\S+",
                  r"\1=[REDACTED]", text)


def export_case(binding: dict, destination: Path) -> None:
    """Bound total excerpts to 256KiB, whitelist files, keep original digests."""
    private = Path(binding["private"])
    files = []
    # 32 finite cases x 3KiB plus the job's 128KiB stream budget < 256KiB.
    remaining = 3072
    candidates = sorted(private.glob("*.receipt.json"), key=lambda p: (p.name != "replacement-prompt.receipt.json", p.name)) + sorted(private.glob("*.prompt"))
    log = Path(binding["home"]) / "daemon.log"
    if log.is_file() and not log.is_symlink():
        candidates.append(log)
    for path in candidates:
        if path.is_symlink() or not path.is_file():
            raise RuntimeError("U3 export refused a nonregular observation")
        raw = path.read_bytes()
        cap = min(768, remaining)
        excerpt = _sanitize(raw if len(raw) <= cap else raw[:cap // 2] + b"\n[bounded]\n" + raw[-cap // 2:])
        excerpt = excerpt.encode("utf8")[:cap].decode("utf8", errors="ignore")
        remaining -= len(excerpt.encode("utf8"))
        metadata = {}
        if path.name.endswith(".receipt.json"):
            try:
                value = json.loads(raw)
                fields = {"command", "task_id", "session_id", "agent", "exit", "stdout_bytes", "stderr_bytes",
                    "stdout_sha256", "stderr_sha256", "prompt_bytes", "prompt_sha256", "run_id", "visit_id",
                    "control_id", "held_event_id", "predecessor_visit_id", "readonly", "supplement", "lineage",
                    "restriction_excerpt", "synthetic", "effect", "head", "merge", "sid", "count", "before", "after",
                    "action", "actor_task_id", "actor_sid", "positive_reassignment_lifecycle", "initial", "snapshots", "completed"}
                metadata = {key: item for key, item in value.items() if key in fields}
            except ValueError:
                pass  # JSONL observer stream is retained only as a bounded excerpt.
        files.append(dict(name=path.name, bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest(),
                          excerpt=excerpt, bounded=len(raw) > cap, metadata=metadata))
    state = {}
    db_path = Path(binding["org_root"]) / "happyranch.db"
    if db_path.is_file() and not db_path.is_symlink():
        from contextlib import closing
        with closing(sqlite3.connect(db_path.as_uri() + "?mode=ro", uri=True)) as conn:
            conn.row_factory = sqlite3.Row
            queries = {
                "runs": "SELECT id,source_task_id,state,progress,active_visit_id,held_event_id,cas,recovery_count FROM workflow_seq_runs",
                "visits": "SELECT id,run_id,node_id,ordinal,task_id,principal,session_id,result_id,state,selected FROM workflow_seq_visits",
                "controls": "SELECT id,run_id,actor_task_id,actor_principal,actor_session_id,actor_result_id,expected_cas,expected_held_event_id,event_id,new_visit_id,action FROM workflow_seq_controls",
                "inbox": "SELECT notification_id,event_id,run_id,initiator_task_id FROM workflow_seq_inbox",
                "results": "SELECT id,task_id,agent,session_id,status FROM task_results",
                "custody": "SELECT id,run_id,visit_id,kind,evidence_digest FROM workflow_seq_evidence",
            }
            for label, sql in queries.items():
                try:
                    rows = conn.execute(sql + " LIMIT 129").fetchall()
                    if len(rows) > 128:
                        raise RuntimeError("U3 retained identity inventory exceeds finite case bounds")
                    state[label] = [dict(row) for row in rows]
                except sqlite3.OperationalError as exc:
                    state[label] = dict(unavailable=type(exc).__name__)
    _write(destination, dict(source_sha=binding["revision"], source_hashes=binding["hashes"],
        nodeid=binding["nodeid"], python=sys.version, executable=sys.executable, files=files,
        identities=state,
        meaning="synthetic lifecycle observations; no real repository/provider effect"))


def _install_capture() -> None:
    from guard import manifest
    parent = manifest()
    path = Path(os.environ["HAPPYRANCH_WORKFLOW_CAPTURE"])
    binding = _json(path)
    private = Path(binding["private"])
    source = Path(parent["source"])
    if (binding["revision"] != parent["revision"] or binding["source"] != str(source)
            or not private.is_relative_to(path.parent) or path.is_symlink()
            or not binding["nodeid"].startswith("tests/integration/test_workflow_sequential_e2e.py::")):
        raise RuntimeError("workflow observer binding mismatch")
    for name in SOURCE_FILES:
        if hashlib.sha256((source / name).read_bytes()).hexdigest() != binding["hashes"][name]:
            raise RuntimeError("workflow observer source mismatch")
    # Python callback/plan children inherit the environment. Only daemon boot
    # installs faults. Importing the helper in pytest is inert as well.
    if "runtime.daemon" not in sys.orig_argv:
        return

    from runtime.workflows import sequential_dispatch as dispatch
    from runtime.workflows import sequential_settlement as settlement
    from runtime.orchestrator import task_pause as pause
    from runtime.orchestrator.orchestrator import Orchestrator

    def case() -> dict:
        candidate = private / "case.json"
        return _json(candidate) if candidate.exists() else {}

    def record(kind: str, **value: object) -> None:
        # The caller supplies only identities/counts/categories, never env/DB.
        with (private / "observer.receipt.json").open("a") as stream:
            stream.write(json.dumps(dict(kind=kind, **value), sort_keys=True) + "\n")

    def original(owner: object, symbol: str):
        value = getattr(owner, symbol)
        if not Path(value.__code__.co_filename).resolve().is_relative_to(source):
            raise RuntimeError("workflow observer callable outside tested source")
        return value

    advance = original(dispatch.SequentialDispatcher, "_advance_generic")
    @functools.wraps(advance)
    def advance_window(self, run_id, *, visit_id):
        if case().get("fault") == "closed-before-advance":
            with self._reader() as conn:
                kinds = {r[0] for r in conn.execute(
                    "SELECT kind FROM workflow_seq_evidence WHERE visit_id=?", (visit_id,))}
            if {"terminal", "outer"} <= kinds:
                record("closed-before-advance", run_id=run_id, visit_id=visit_id)
                raise RuntimeError("U3 closed-before-advance observation window")
        return advance(self, run_id, visit_id=visit_id)
    dispatch.SequentialDispatcher._advance_generic = advance_window

    outer = original(dispatch.SequentialDispatcher, "_generic_outer_settled")
    @functools.wraps(outer)
    def outer_window(self, invocation, *args, **kwargs):
        if case().get("fault") == "before-outer":
            record("before-outer", task_id=invocation.task.id, session_id=invocation.session_id)
            raise RuntimeError("U3 original outer observation unavailable")
        return outer(self, invocation, *args, **kwargs)
    dispatch.SequentialDispatcher._generic_outer_settled = outer_window

    render = original(dispatch.SequentialDispatcher, "_render_node_instruction")
    @functools.wraps(render)
    def render_window(self, task_id, *args, **kwargs):
        fault = case().get("fault")
        if fault == "render":
            record("render-refusal", task_id=task_id)
            raise dispatch.SequentialRefusal("U3_render_refusal")
        if fault == "cancel-barrier":
            with self._reader() as conn:
                run, _, _ = self._entry(conn, task_id)
                sibling = "sibling" in json.loads(run["start_bytes"])["request"]
            if not sibling:
                record("cancel-barrier", task_id=task_id)
                deadline = time.monotonic() + 30
                while case().get("fault") == "cancel-barrier" and time.monotonic() < deadline:
                    time.sleep(.05)
                if case().get("fault") == "cancel-barrier":
                    raise RuntimeError("U3 cancel barrier deadline")
        return render(self, task_id, *args, **kwargs)
    dispatch.SequentialDispatcher._render_node_instruction = render_window

    prepare = original(pause.TaskInvocation, "_prepare")
    @functools.wraps(prepare)
    def prepare_window(self):
        fault = case().get("fault") if self.owner == "sequential" else None
        if self.owner == "sequential":
            record("prepare-entry", task_id=self.task.id, invocation_id=self.identity, session_id=self.session_id)
        if fault == "P0":
            record("P0", task_id=self.task.id, invocation_id=self.identity, session_id=self.session_id)
            raise RuntimeError("U3 before reservation")
        result = prepare(self)
        if fault == "U0":
            record("U0", task_id=self.task.id, invocation_id=self.identity, session_id=self.session_id)
            raise RuntimeError("U3 uncertain preparation return")
        return result
    pause.TaskInvocation._prepare = prepare_window

    impl = original(Orchestrator, "_run_agent_impl")
    @functools.wraps(impl)
    def impl_window(self, *args, **kwargs):
        invocation = pause.current_invocation()
        fault = case().get("fault") if invocation is not None and invocation.owner == "sequential" else None
        if fault in ("P1-raise", "P1-return", "P1-close", "P1-release", "P1-receipt"):
            record(fault, task_id=invocation.task.id, invocation_id=invocation.identity,
                   session_id=invocation.session_id)
            if fault != "P1-return":
                raise RuntimeError("U3 early execution failure")
            from runtime.orchestrator.executors import ExecutorResult
            return ExecutorResult(success=False, duration_seconds=0, session_id=invocation.session_id,
                                  error="U3 early unsuccessful return"), None
        return impl(self, *args, **kwargs)
    Orchestrator._run_agent_impl = impl_window

    def sequential_fault() -> tuple[object, str | None]:
        invocation = pause.current_invocation()
        return invocation, (case().get("fault") if invocation is not None and invocation.owner == "sequential" else None)

    import runtime.orchestrator.orchestrator as orchestrator_module
    from runtime.orchestrator import task_scratch
    from runtime.workflows import sequential_launch_failures as failures
    from runtime.orchestrator.team_move import TeamMoveCoordinator

    integrity = orchestrator_module.validate_workspace_skills_integrity
    @functools.wraps(integrity)
    def integrity_window(workspace, specs, **kwargs):
        invocation, fault = sequential_fault()
        if fault == "U1-integrity":
            # Corrupt only a synthetic canonical package, then let the real
            # validator and its real unsuccessful-return path own the failure.
            from runtime.skills.canonical_store import CanonicalSkillStore
            from runtime.platform.isolation import detect_platform_isolation
            store = CanonicalSkillStore(settings=kwargs["settings"], isolation=detect_platform_isolation())
            spec = specs[0]
            target = store.canonical_path(spec["slug"], spec["version"], spec["content_hash"]) / "SKILL.md"
            assert any(target.resolve().is_relative_to(root.resolve())
                       for root in (Path(parent["root"]), Path(binding["home"])))
            target.chmod(0o600)
            target.write_bytes(target.read_bytes() + b"\nSynthetic U3 integrity corruption\n")
            record(fault, task_id=invocation.task.id, invocation_id=invocation.identity)
        return integrity(workspace, specs, **kwargs)
    orchestrator_module.validate_workspace_skills_integrity = integrity_window

    observation = task_scratch._write_observation
    @functools.wraps(observation)
    def scratch_window(contract):
        invocation, fault = sequential_fault()
        if fault in ("U2-return", "U2-raise"):
            # Real scratch mkdir has happened; its own caller handles the
            # documented TaskScratchError return, or the raw exception form.
            record(fault, task_id=invocation.task.id, invocation_id=invocation.identity)
            if fault == "U2-return":
                raise task_scratch.TaskScratchError("U3 partial scratch observation")
            raise RuntimeError("U3 partial scratch observation")
        return observation(contract)
    task_scratch._write_observation = scratch_window

    launch = original(Orchestrator, "_launch_agent_with_scratch")
    @functools.wraps(launch)
    def precommit_window(self, **kwargs):
        invocation, fault = sequential_fault()
        if fault == "P2-unproved":
            record(fault, task_id=invocation.task.id, invocation_id=invocation.identity)
            raise RuntimeError("U3 pre-COMMIT without scratch closure certificate")
        if fault == "host-unavailable-return":
            saved = self._host_supervisor
            self._host_supervisor = None
            try:
                # Exercise the actual production unsuccessful-return branch.
                return launch(self, **kwargs)
            finally:
                self._host_supervisor = saved
        return launch(self, **kwargs)
    Orchestrator._launch_agent_with_scratch = precommit_window

    business = original(pause.TaskInvocation, "_business_uncommitted")
    @functools.wraps(business)
    def commit_fault(self, *args, **kwargs):
        if self.owner == "sequential" and case().get("fault") == "U3":
            record("U3", task_id=self.task.id, invocation_id=self.identity)
            raise RuntimeError("U3 actual launch transaction failure")
        return business(self, *args, **kwargs)
    pause.TaskInvocation._business_uncommitted = commit_fault

    commit = original(pause.TaskInvocation, "_commit_local")
    @functools.wraps(commit)
    def committed_window(self, *args, **kwargs):
        result = commit(self, *args, **kwargs)
        if self.owner == "sequential" and case().get("fault") == "U4" and result:
            record("U4", task_id=self.task.id, invocation_id=self.identity, admitted=self.admitted)
            raise RuntimeError("U3 after authentic launch COMMIT before backend")
        return result
    pause.TaskInvocation._commit_local = committed_window

    pid = original(dispatch.SequentialDispatcher, "record_pid")
    @functools.wraps(pid)
    def spawned_window(self, **kwargs):
        result = pid(self, **kwargs)
        if case().get("fault") == "U5-after-spawn":
            record("U5-after-spawn", task_id=kwargs["task_id"], session_id=kwargs["session_id"])
            raise RuntimeError("U3 after original spawn observation")
        return result
    dispatch.SequentialDispatcher.record_pid = spawned_window

    close = original(pause.TaskInvocation, "close")
    @functools.wraps(close)
    def close_fault(self, **kwargs):
        if self.owner == "sequential" and case().get("fault") == "P1-close":
            record("positive-close-refused", task_id=self.task.id, invocation_id=self.identity)
            raise RuntimeError("U3 original native close unavailable")
        return close(self, **kwargs)
    pause.TaskInvocation.close = close_fault

    release = original(TeamMoveCoordinator, "release_task")
    @functools.wraps(release)
    def release_fault(self, invocation, **kwargs):
        if (invocation.owner == "sequential" and case().get("fault") == "P1-release"
                and kwargs.get("failed") is False):
            record("positive-release-refused", task_id=invocation.task.id, invocation_id=invocation.identity)
            raise RuntimeError("U3 original native release unavailable")
        return release(self, invocation, **kwargs)
    TeamMoveCoordinator.release_task = release_fault

    append = failures._append
    @functools.wraps(append)
    def receipt_fault(conn, body):
        if case().get("fault") == "P1-receipt" and body["stage"] == "closed":
            record("positive-receipt-refused", task_id=body["task_id"], invocation_id=body["invocation_id"])
            raise RuntimeError("U3 original closed receipt transaction failure")
        return append(conn, body)
    failures._append = receipt_fault

    # A test-side mailbox requests finite production discovery/delivery cycles
    # on the daemon's own loop. It neither consumes callbacks nor writes facts.
    init = original(dispatch.SequentialDispatcher, "__init__")
    @functools.wraps(init)
    def observed_init(self, org):
        init(self, org)
        def monitor():
            seen = set()
            while True:
                command_path = private / "observer-command.json"
                try:
                    command = _json(command_path)
                except (OSError, ValueError):
                    time.sleep(.05)
                    continue
                nonce = command["nonce"]
                loop = getattr(org.orchestrator, "_main_loop", None)
                if nonce in seen or loop is None or not loop.is_running():
                    time.sleep(.05)
                    continue
                seen.add(nonce)
                async def observe():
                    snapshots = []
                    def snapshot():
                        with self._reader() as conn:
                            visit_tasks = {r[0] for r in conn.execute("SELECT task_id FROM workflow_seq_visits")}
                            journals = []
                            for row in conn.execute("SELECT admission_journal_json FROM task_pause_controls"):
                                journals.extend(e for e in json.loads(row[0])["entries"] if e["task_id"] in visit_tasks)
                            native = [tuple(r) for r in conn.execute("SELECT id,status,current_session_id,orchestration_step_count FROM tasks "
                                "WHERE id IN (SELECT task_id FROM workflow_seq_visits) ORDER BY id")]
                            dispositions = [tuple(r) for r in conn.execute("SELECT id,payload FROM audit_log "
                                "WHERE action='workflow_seq_native_launch_failure' ORDER BY id")]
                            identities = {table: [tuple(r) for r in conn.execute(sql)] for table, sql in {
                                "visits": "SELECT id,task_id,session_id,result_id FROM workflow_seq_visits ORDER BY id",
                                "starts": "SELECT id FROM audit_log WHERE action='session_start' AND task_id IN (SELECT task_id FROM workflow_seq_visits) ORDER BY id",
                                "results": "SELECT id,session_id FROM task_results WHERE task_id IN (SELECT task_id FROM workflow_seq_visits) ORDER BY id",
                                "bindings": "SELECT binding_id FROM authority_policy_v2_session_bindings WHERE root_task_id IN (SELECT task_id FROM workflow_seq_visits) ORDER BY binding_id",
                                "custody": "SELECT id,evidence_digest FROM workflow_seq_evidence ORDER BY id",
                            }.items()}
                        preparations = [value for line in (private / "observer.receipt.json").read_text().splitlines()
                            if (value := json.loads(line)).get("kind") == "prepare-entry" and value["task_id"] in visit_tasks]
                        entries = [dict(task_id=json.loads(path.read_bytes())["task_id"], session_id=path.name.split("-entry.receipt.json")[0])
                            for path in sorted(private.glob("*-entry.receipt.json"))
                            if json.loads(path.read_bytes())["task_id"] in visit_tasks]
                        reservations = {key: dict(task_id=value.task_id, possible_launch=value.possible_launch,
                            cleanup_failed=value.cleanup_failed, owner_live=value.owner_live)
                            for key, value in org.orchestrator._team_moves._reservations.items()
                            if value.task_id in visit_tasks}
                        return dict(native=native, journal_count=len(journals), journal_digest=hashlib.sha256(
                            json.dumps(journals, sort_keys=True).encode()).hexdigest(), reservations=reservations,
                            disposition_digest=hashlib.sha256(json.dumps(dispositions, sort_keys=True).encode()).hexdigest(),
                            failure_identities=[{key: json.loads(row[1])[key] for key in
                                ("invocation_id", "prospective_session_id")} for row in dispositions],
                            preparations=preparations, process_entries=entries, identities=identities,
                            failure_owners={key: value.identity for key, value in self._launch_failure_owners.items()
                                            if key in visit_tasks})
                    initial = snapshot()
                    if command["action"] == "alarm":
                        # Replace only this observer module's clock reference.
                        # No deadlines, reaper clocks or ownership are changed.
                        saved = settlement.time
                        offset = [0.0]
                        settlement.time = SimpleNamespace(monotonic=lambda: time.monotonic() + offset[0])
                        try:
                            before = org.db._conn.total_changes
                            settlement.observe_stalls(org)
                            offset[0] = 301
                            settlement.observe_stalls(org)
                            settlement.observe_stalls(org)
                            assert org.db._conn.total_changes == before, "stall observation wrote business state"
                            record("alarm", writes=org.db._conn.total_changes - before)
                        finally:
                            settlement.time = saved
                    else:
                        for cycle in range(5):
                            await settlement.sweep_sequential(org, org.orchestrator._queue)
                            with self._reader() as conn:
                                tasks = [r[0] for r in conn.execute("SELECT task_id FROM workflow_seq_visits WHERE selected=1")]
                            for task_id in tasks:
                                self.notify_queued(task_id, org.orchestrator._queue)
                            record("discovery-cycle", cycle=cycle + 1)
                            await asyncio.sleep(.25)
                            snapshots.append(snapshot())
                    _write(private / (nonce + ".receipt.json"), dict(nonce=nonce, action=command["action"], completed=True,
                                                                   initial=initial, snapshots=snapshots))
                try:
                    asyncio.run_coroutine_threadsafe(observe(), loop).result(timeout=15)
                except Exception as exc:
                    _write(private / (nonce + ".receipt.json"), dict(nonce=nonce, error=type(exc).__name__))
        threading.Thread(target=monitor, name="U3-observation", daemon=True).start()
    dispatch.SequentialDispatcher.__init__ = observed_init
    record("installed", source_sha=binding["revision"], pid=os.getpid())


if __name__ == "sitecustomize" and "PYTEST_CURRENT_TEST" in os.environ and "HAPPYRANCH_TEST_PARENT_MANIFEST" not in os.environ:
    os.write(2, b"integration test parent identity missing\n")
    os._exit(86)

if __name__ == "sitecustomize" and "HAPPYRANCH_TEST_PARENT_MANIFEST" in os.environ:
    try:
        from guard import install
        install()
        if "HAPPYRANCH_WORKFLOW_CAPTURE" in os.environ:
            _install_capture()
    except Exception:
        os.write(2, b"workflow observer identity unavailable\n")
        os._exit(86)
