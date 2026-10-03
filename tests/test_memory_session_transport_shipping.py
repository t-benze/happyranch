"""Real task-bootstrap -> provider child -> CLI -> route/audit transport proof.

This deliberately uses the production ``Orchestrator._run_agent`` bootstrap and
a disposable executable provider.  The provider itself is fake, but the child
process, installed ``happyranch`` CLI, loopback daemon routes, SessionTracker
registration and SQLite audit store are the shipping implementations.
"""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
import socket
import sys
import threading
import time
import textwrap
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
import uvicorn

from runtime.daemon import paths
from runtime.daemon.app import create_app
from runtime.daemon.state import DaemonState
from runtime.runtime import RuntimeDir
from runtime.orchestrator.agent_def import AgentDef, render_agent_text
from runtime.orchestrator.executor_binary_registry import set_binary
from runtime.models import TaskRecord


def _wait_for_server(server: uvicorn.Server) -> None:
    deadline = time.monotonic() + 5
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.01)
    assert server.started


@pytest.mark.parametrize(
    ("executor", "is_child"),
    (("claude", False), ("claude", True), ("codex", False), ("codex", True)),
)
def test_task_bootstrap_forwards_runtime_session_to_real_cli_and_audit(
    test_settings, monkeypatch, tmp_path, executor, is_child,
):
    """A task bootstrap gives its actual runtime SID to a provider child.

    The provider resume value deliberately differs from the runtime SID.  Its
    real child invokes canonical ``memory get`` and ``memory search`` without
    ``--session-id``; both requests are correlated through the existing route
    validation and audited against the bootstrap's digest impression.
    """
    monkeypatch.setenv("HAPPYRANCH_DAEMON_HOME", str(tmp_path / "daemon"))
    monkeypatch.delenv("HAPPYRANCH_TASK_TMP_ROOT", raising=False)
    monkeypatch.delenv("HAPPYRANCH_TASK_SCRATCH_MANIFEST", raising=False)
    paths.ensure_daemon_home()
    paths.ensure_token()
    runtime = RuntimeDir.init(tmp_path / "runtime")
    for contract in ("start-task", "jobs", "make-worktree", "thread", "dream", "todos", "workspace-cleanup"):
        source = runtime.root / "skills" / "bundled" / contract
        source.mkdir(parents=True, exist_ok=True)
        (source / "SKILL.md").write_text(f"# {contract}\n")
    org_root = runtime.orgs_dir / "alpha"
    (org_root / "org").mkdir(parents=True)
    (org_root / "org" / "teams.yaml").write_text(
        "teams:\n  engineering:\n    manager: engineering_head\n    workers: [dev_agent]\n"
    )
    daemon_state = DaemonState.from_runtime(runtime, test_settings)
    org = daemon_state.orgs["alpha"]
    agent = "dev_agent"
    workspace = org.root / "workspaces" / agent
    workspace.mkdir(parents=True)
    (workspace / "task_history.md").write_text("# Task History: dev_agent\n")
    # Both profiles require the current canonical instruction pair.
    (workspace / "AGENTS.md").write_text("# shipping fixture\n")
    (workspace / "CLAUDE.md").symlink_to("AGENTS.md")
    agent_def = AgentDef(
        name=agent, team="engineering", role="worker", executor=executor,
        allow_rules=(), repos={}, enrolled_by=None, enrolled_at_task=None,
        enrolled_at=None, system_prompt="worker", description="",
    )
    agents_dir = org_root / "org" / "agents"
    agents_dir.mkdir(parents=True, exist_ok=True)
    (agents_dir / f"{agent}.md").write_text(render_agent_text(agent_def))
    memory = workspace / "memory"
    memory.mkdir()
    (memory / "MEM-001-shipping.md").write_text(
        "---\nid: MEM-001\nslug: shipping\ntitle: Shipping\ntopic: test\n"
        "provenance: experiential\nscope: agent\nlifecycle: valid\nsalience: 50\n---\n\nbody\n"
    )
    (memory / "MEM-002-follow-up.md").write_text(
        "---\nid: MEM-002\nslug: follow-up\ntitle: Follow up\ntopic: test\n"
        "provenance: experiential\nscope: agent\nlifecycle: valid\nsalience: 40\n---\n\nshipping follow up\n"
    )

    # Only the external provider is fake. CLI entry, imports, bootstrap,
    # launch, SessionTracker validation and SQLite writes are real.
    provider = tmp_path / executor
    provider.write_text("#!" + sys.executable + "\n" + textwrap.dedent("""\
        import json, os, subprocess, sys, time
        from pathlib import Path
        sid = os.environ['HAPPYRANCH_RUNTIME_SESSION_ID']
        evidence = Path(os.environ['HAPPYRANCH_DAEMON_HOME']) / 'observations'
        evidence.mkdir(exist_ok=True)
        barrier = evidence / 'barriers'
        cli = str(Path(sys.executable).parent / 'happyranch')
        def wait_for(name):
            deadline = time.monotonic() + 15
            while not (barrier / name).exists():
                if time.monotonic() >= deadline:
                    raise RuntimeError('operation barrier expired: ' + name)
                time.sleep(.01)
        probe = subprocess.run([sys.executable, '-c',
            'import cli,runtime,importlib.metadata,json; print(json.dumps([cli.__file__,runtime.__file__,importlib.metadata.version("happyranch")]))'],
            capture_output=True, text=True, check=True)
        observation = {'argv': sys.argv[1:], 'sid': sid, 'cli': cli,
                       'imports': json.loads(probe.stdout), 'operations': []}
        commands = [('get', 'MEM-001', None), ('search', 'shipping', None), ('get', 'MEM-002', None)]
        if not barrier.exists():
            commands += [('get', 'MEM-001', sid), ('search', 'shipping', sid),
                         ('get', 'MEM-001', ''), ('search', 'shipping', '')]
        for index, (verb, value, explicit) in enumerate(commands):
            if barrier.exists():
                wait_for(sid + '.' + str(index) + '.go')
            command = [cli, 'memory', verb, '--org', 'alpha',
                       '--agent', 'dev_agent', value, '--json']
            child_env = dict(os.environ)
            if explicit is not None:
                command += ['--session-id', explicit]
                if explicit:
                    child_env['HAPPYRANCH_RUNTIME_SESSION_ID'] = 'sess-poison-unregistered'
            result = subprocess.run(command, env=child_env,
                capture_output=True, text=True, timeout=10)
            observation['operations'].append({'verb': verb, 'value': value,
                'exit': result.returncode, 'stdout': result.stdout, 'stderr': result.stderr})
            (evidence / (sid + '.json')).write_text(json.dumps(observation))
            if result.returncode:
                print(result.stderr, file=sys.stderr)
                sys.exit(result.returncode)
            if barrier.exists():
                (barrier / (sid + '.' + str(index) + '.done')).touch()
        if barrier.exists():
            wait_for(sid + '.exit')
        print('provider completed')
        """))
    provider.chmod(0o755)
    set_binary(executor, str(provider))

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    paths.port_file().write_text(str(sock.getsockname()[1]))
    server = uvicorn.Server(uvicorn.Config(create_app(daemon_state), lifespan="off", log_level="error"))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]})
    thread.start()
    try:
        _wait_for_server(server)
        started = {}
        started_lock = threading.Lock()

        def registered(task, registered_agent, sid):
            assert registered_agent == agent
            assert org.sessions.get_context_by_session(sid) == ("alpha", task, agent)
            with started_lock:
                started[task] = sid

        def run_task(task, resume=None):
            return org.orchestrator._run_agent(
                task, agent, "shipping transport", on_session_started=registered,
                resume_session_id=resume,
            )

        def audit_operations(task, sid, with_flags=False):
            rows = org.db.fetch_all_readonly(
                "SELECT id, action, task_id, agent, payload FROM audit_log "
                "WHERE action IN ('memory_digest_impression', 'memory_read', 'memory_search') "
                "ORDER BY id"
            )
            own = [(row, json.loads(row["payload"])) for row in rows
                   if json.loads(row["payload"]).get("session_id") == sid]
            expected_actions = ["memory_digest_impression", "memory_read", "memory_search", "memory_read"]
            if with_flags:
                expected_actions += ["memory_read", "memory_search", "memory_read", "memory_search"]
            assert [row["action"] for row, _ in own] == expected_actions, own
            for row, payload in own:
                assert row["agent"] == agent
                assert row["task_id"] == (
                    f"AGENT-{agent}" if row["action"] == "memory_read" else task
                )
                if row["action"] == "memory_digest_impression":
                    assert payload == {"agent": agent, "session_id": sid,
                        "digest_ids": ["MEM-001", "MEM-002"], "digest_count": 2, "budget": 1500}
                else:
                    assert payload["task_id"] == task
                    assert payload["session_id"] == sid
                if row["action"] == "memory_search":
                    assert payload["agent"] == agent
                    assert set(payload["memory_ids"]) == {"MEM-001", "MEM-002"}
                    assert payload["hit_count"] == 2 and payload["kb_hit_count"] == 0
                if row["action"] == "memory_read":
                    assert payload["source"] == "digest"
            assert [payload["id"] for row, payload in own
                    if row["action"] == "memory_read"] == (
                        ["MEM-001", "MEM-002", "MEM-001", "MEM-001"] if with_flags
                        else ["MEM-001", "MEM-002"]
                    )
            return [row["id"] for row, _ in own[1:]]

        def provider_evidence(sid, resume, operation_count=3):
            observation = json.loads((paths.daemon_home() / "observations" / (sid + ".json")).read_text())
            assert observation["sid"] == sid
            candidate = Path(__file__).resolve().parents[1]
            assert Path(observation["cli"]).parent == Path(sys.executable).parent
            assert observation["imports"] == [str(candidate / "cli/__init__.py"),
                str(candidate / "runtime/__init__.py"), "0.1.0"]
            argv = observation["argv"]
            if executor == "claude":
                assert "--resume" in argv, argv
                assert argv[argv.index("--resume") + 1] == resume
            else:
                assert argv[:3] == ["exec", "resume", resume]
            assert resume != sid
            assert [op["exit"] for op in observation["operations"]] == [0] * operation_count

        root_task_id = org.orchestrator.create_task("shipping memory digest root")
        if is_child:
            task_id = "TASK-SHIPPING-CHILD"
            org.db.insert_task(TaskRecord(
                id=task_id, brief="shipping memory digest child", team="engineering",
                parent_task_id=root_task_id, assigned_agent=agent,
            ))
        else:
            task_id = root_task_id
        resume = "provider-resume-distinct"
        result, _ = run_task(task_id, resume)
        assert result.success, (result.error, result.stdout_tail, result.stderr_tail)
        sid = started[task_id]
        assert result.session_id == sid
        audit_operations(task_id, sid, with_flags=True)
        provider_evidence(sid, resume, operation_count=7)
        assert org.db.get_task(task_id).parent_task_id == (root_task_id if is_child else None)

        # Same-agent sessions overlap with every operation explicitly ordered.
        # End and retire the root binding before the child's final read.
        if executor == "claude" and not is_child:
            barrier = paths.daemon_home() / "observations" / "barriers"
            barrier.mkdir()
            overlap_root = org.orchestrator.create_task("overlap root")
            overlap_child = "TASK-SHIPPING-OVERLAP-CHILD"
            org.db.insert_task(TaskRecord(
                id=overlap_child, brief="overlap child", team="engineering",
                parent_task_id=overlap_root, assigned_agent=agent,
            ))

            def wait_until(predicate):
                deadline = time.monotonic() + 15
                while not predicate():
                    assert time.monotonic() < deadline, "controller barrier expired"
                    time.sleep(.01)

            with ThreadPoolExecutor(max_workers=2) as pool:
                futures = {task: pool.submit(run_task, task, resume)
                           for task in (overlap_root, overlap_child)}
                try:
                    wait_until(lambda: overlap_root in started and overlap_child in started)
                    root_sid, child_sid = started[overlap_root], started[overlap_child]
                    assert root_sid != child_sid
                    # No-context launch in a separate parent process with a
                    # poisoned ACTIVE SID: exercise the final platform overlay
                    # without changing this process's global environment.
                    before = org.db.fetch_all_readonly("SELECT max(id) AS id FROM audit_log")[0]["id"]
                    no_context = subprocess.run([
                        sys.executable, "-c", textwrap.dedent("""\
                            import subprocess, sys
                            from pathlib import Path
                            from runtime.orchestrator.executors import _callee_env
                            from runtime.platform.isolation import detect_platform_isolation
                            cli = str(Path(sys.executable).parent / 'happyranch')
                            for verb, value in [('get', 'MEM-001'), ('search', 'shipping')]:
                                proc = detect_platform_isolation().launch_executor(
                                    [cli, 'memory', verb, '--org', 'alpha', '--agent',
                                     'dev_agent', value, '--json'], cwd=Path(sys.argv[1]),
                                    env=_callee_env(workspace=Path(sys.argv[1])),
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                                stdout, stderr = proc.communicate(timeout=10)
                                assert proc.returncode == 0, (stdout, stderr)
                            """), str(workspace),
                    ], env={**os.environ, "HAPPYRANCH_RUNTIME_SESSION_ID": root_sid},
                        capture_output=True, text=True, timeout=20)
                    assert no_context.returncode == 0, no_context.stderr
                    manual_rows = org.db.fetch_all_readonly(
                        "SELECT action, agent, task_id, payload FROM audit_log WHERE id > ? "
                        "AND action IN ('memory_read', 'memory_search') ORDER BY id", (before,),
                    )
                    assert [row["action"] for row in manual_rows] == ["memory_read", "memory_search"]
                    for row in manual_rows:
                        payload = json.loads(row["payload"])
                        assert row["task_id"] == f"AGENT-{agent}" and row["agent"] == agent
                        assert "session_id" not in payload and "task_id" not in payload, payload
                        if row["action"] == "memory_read":
                            assert payload.get("source", "explicit_or_other") == "explicit_or_other"

                    for index in (0, 1):
                        for active_sid in (root_sid, child_sid):
                            (barrier / f"{active_sid}.{index}.go").touch()
                            wait_until(lambda: (barrier / f"{active_sid}.{index}.done").exists())
                    (barrier / f"{root_sid}.2.go").touch()
                    wait_until(lambda: (barrier / f"{root_sid}.2.done").exists())
                    (barrier / f"{root_sid}.exit").touch()
                    root_result, _ = futures[overlap_root].result(timeout=10)
                    assert root_result.success, root_result.error
                    assert org.sessions.get_context_by_session(root_sid) is None
                    assert org.sessions.get_context_by_session(child_sid) == ("alpha", overlap_child, agent)
                    (barrier / f"{child_sid}.2.go").touch()
                    wait_until(lambda: (barrier / f"{child_sid}.2.done").exists())
                    (barrier / f"{child_sid}.exit").touch()
                    child_result, _ = futures[overlap_child].result(timeout=10)
                    assert child_result.success, child_result.error
                    root_ids = audit_operations(overlap_root, root_sid)
                    child_ids = audit_operations(overlap_child, child_sid)
                    assert root_ids[0] < child_ids[0] < root_ids[1] < child_ids[1] < root_ids[2] < child_ids[2]
                    provider_evidence(root_sid, resume)
                    provider_evidence(child_sid, resume)
                finally:
                    # Release only this fixture's gates on a failure so children
                    # exit before the disposable daemon/DB is closed.
                    for active_sid in list(started.values()):
                        for index in range(3):
                            (barrier / f"{active_sid}.{index}.go").touch()
                        (barrier / f"{active_sid}.exit").touch()
    finally:
        server.should_exit = True
        thread.join(timeout=5)
        sock.close()
        asyncio.run(daemon_state.close_all())
