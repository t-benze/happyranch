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
import httpx
import uvicorn

from runtime.daemon import paths
from runtime.daemon.app import create_app
from runtime.daemon.state import DaemonState
from runtime.runtime import RuntimeDir
from runtime.orchestrator.agent_def import AgentDef, render_agent_text
from runtime.orchestrator.executor_binary_registry import set_binary
from runtime.models import TaskRecord
from runtime.platform.isolation import detect_platform_isolation as _shipping_platform_detector


def _wait_for_server(server: uvicorn.Server) -> None:
    deadline = time.monotonic() + 5
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.01)
    assert server.started


@pytest.mark.parametrize("exposure", ("pointers", "fit", "fallback"))
@pytest.mark.parametrize(
    ("executor", "is_child"),
    (("claude", False), ("claude", True), ("codex", False), ("codex", True)),
)
def test_task_bootstrap_forwards_runtime_session_to_real_cli_and_audit(
    test_settings, monkeypatch, tmp_path, executor, is_child, exposure,
):
    """A task bootstrap gives its actual runtime SID to a provider child.

    The provider resume value deliberately differs from the runtime SID.  Its
    real child invokes canonical ``memory get`` and ``memory search`` without
    ``--session-id``; both requests are correlated through the existing route
    validation and audited against the bootstrap's digest impression.
    """
    # Capture the production detector at collection time, then opt out of the
    # suite's generic same-owner launch double for this shipping proof.
    monkeypatch.setattr("runtime.platform.isolation.detect_platform_isolation", _shipping_platform_detector)
    monkeypatch.setattr("runtime.orchestrator.executors.detect_platform_isolation", _shipping_platform_detector)
    monkeypatch.setenv("HAPPYRANCH_TEST_REAL_PLATFORM", "1")
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
    test_settings.executor_rate_limit_backoff_seconds = [0]
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

    if exposure != "pointers":
        # R11b/c fixture, exactly 216 chars, without timestamp/brief boosts.
        (memory / "MEM-001-shipping.md").write_text(
            "---\nid: MEM-001\nslug: bounded-directive\ntitle: Bounded directive\ntopic: memory\n"
            "provenance: directive\nscope: agent\nlifecycle: valid\nsalience: 50\n---\n"
            "Keep task credit scoped. Mention MEM-999 without exposing that item."
        )
        (memory / "MEM-002-follow-up.md").unlink()
        org.orchestrator._paths.org_config_path.write_text(
            f"memory_digest_budget: {255 if exposure == 'fit' else 172}\n"
        )
    provider_plan = {
        "search": "shipping" if exposure == "pointers" else "scoped",
        "follow_on": "MEM-002" if exposure == "pointers" else "MEM-999",
        "retry_once": executor == "codex" and not is_child and exposure == "pointers",
    }

    # Only the external provider is fake. CLI entry, imports, bootstrap,
    # launch, SessionTracker validation and SQLite writes are real.
    provider = tmp_path / executor
    provider.write_text("#!" + sys.executable + "\n" + textwrap.dedent("""\
        import json, os, subprocess, sys, time
        from pathlib import Path
        prompt = sys.stdin.read()
        plan = json.loads(Path(sys.argv[0] + '.plan.json').read_text())
        sid = os.environ['HAPPYRANCH_RUNTIME_SESSION_ID']
        evidence = Path(os.environ['HAPPYRANCH_DAEMON_HOME']) / 'observations'
        evidence.mkdir(exist_ok=True)
        barrier = evidence / 'barriers'
        retry_marker = evidence / (sid + '.retry')
        if plan.get('retry_once') and not retry_marker.exists():
            retry_marker.touch()
            print('provider rate limit 429', file=sys.stderr)
            sys.exit(1)
        if plan.get('outcome') == 'nonzero':
            print('controlled provider failure', file=sys.stderr)
            sys.exit(7)
        if plan.get('outcome') in ('timeout', 'cancel'):
            (evidence / (sid + '.failure-ready')).touch()
            time.sleep(30)
            sys.exit(0)
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
                       'imports': json.loads(probe.stdout), 'python': [sys.executable, sys.version],
                       'prompt': prompt, 'operations': []}
        commands = [('get', 'MEM-001', None), ('search', plan['search'], None), ('get', plan['follow_on'], None)]
        if not barrier.exists():
            commands += [('get', 'MEM-001', sid), ('search', plan['search'], sid),
                         ('get', 'MEM-001', ''), ('search', plan['search'], '')]
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
    Path(str(provider) + ".plan.json").write_text(json.dumps(provider_plan))
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
            if exposure != "pointers":
                # Create a real nonshown entry after render, before impression
                # emission. Metadata must retain the actual injected pass even
                # when the file-backed store changes at this existing callback.
                (memory / "MEM-999-nonshown.md").write_text(
                    "---\nid: MEM-999\nslug: nonshown\ntitle: Nonshown\ntopic: memory\n"
                    "provenance: experiential\nscope: agent\nlifecycle: valid\nsalience: 1\n---\n"
                    "A genuinely nonshown scoped memory."
                )
            with started_lock:
                started[task] = sid

        def run_task(task, resume=None, timeout=None):
            return org.orchestrator._run_agent(
                task, agent, "shipping transport", on_session_started=registered,
                resume_session_id=resume, timeout_seconds_override=timeout,
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
                    shown = ["MEM-001", "MEM-002"] if exposure == "pointers" else ["MEM-001"]
                    # Assert the phantom-ID defect first, before new metadata.
                    assert payload["digest_ids"] == shown, payload
                    assert payload == {"agent": agent, "session_id": sid,
                        "digest_ids": shown, "digest_count": len(shown),
                        "budget": 1500 if exposure == "pointers" else (255 if exposure == "fit" else 172),
                        "memory_telemetry_version": 1,
                        "pointer_ids": [] if exposure == "fit" else shown,
                        "full_body_ids": ["MEM-001"] if exposure == "fit" else []}
                    response = httpx.get(
                        f"http://127.0.0.1:{sock.getsockname()[1]}/api/v1/orgs/alpha/audit",
                        params={"task_id": task, "action": "memory_digest_impression"},
                        headers={"Authorization": f"Bearer {paths.token_file().read_text().strip()}"},
                    )
                    assert response.status_code == 200, response.text
                    entries = response.json()["entries"]
                    assert len(entries) == 1 and entries[0]["payload"] == payload
                    assert entries[0]["task_id"] == task and entries[0]["agent"] == agent
                    assert set(payload) == {"agent", "session_id", "digest_ids", "digest_count",
                        "budget", "memory_telemetry_version", "pointer_ids", "full_body_ids"}
                    starts = org.db.fetch_all_readonly(
                        "SELECT id FROM audit_log WHERE task_id=? AND action='session_start'",
                        (task,),
                    )
                    assert len(starts) == 1 and row["id"] < starts[0]["id"]
                else:
                    assert payload["task_id"] == task
                    assert payload["session_id"] == sid
                if row["action"] == "memory_search":
                    assert payload["agent"] == agent
                    assert set(payload["memory_ids"]) == {"MEM-001", provider_plan["follow_on"]}
                    assert payload["hit_count"] == 2 and payload["kb_hit_count"] == 0
                if row["action"] == "memory_read":
                    assert payload["source"] == (
                        "search" if exposure != "pointers" and payload["id"] == "MEM-999" else "digest"
                    )
            assert [payload["id"] for row, payload in own
                    if row["action"] == "memory_read"] == (
                        ["MEM-001", provider_plan["follow_on"], "MEM-001", "MEM-001"] if with_flags
                        else ["MEM-001", provider_plan["follow_on"]]
                    )
            # G3-P01/P03/P06/P08: the census tuple is produced by this actual
            # bootstrap, independently of reads/impressions/provider resume.
            phase_rows = org.db.get_audit_logs(task)
            own_phases = {phase: [entry for entry in phase_rows
                                 if entry["action"] == "memory_runtime_" + phase
                                 and entry["payload"].get("session_id") == sid]
                          for phase in ("identity", "expectation", "binding", "launched", "terminal")}
            assert all(len(own_phases[phase]) == 1 for phase in ("identity", "expectation", "binding", "terminal"))
            identity = own_phases["identity"][0]["payload"]
            actual_task = org.db.get_task(task)
            assert identity["parent_task_id"] == actual_task.parent_task_id
            assert identity["population"] == ("child" if actual_task.parent_task_id else "root")
            assert identity["executor"] == executor and identity["session_id"] == sid
            intent = [entry for entry in phase_rows if entry["action"] == "memory_runtime_intent"
                      and entry["payload"]["ordinal"] == identity["ordinal"]]
            assert len(intent) == 1
            assert intent[0]["payload"]["session_id"] is None
            assert all(entry["payload"]["ordinal"] == identity["ordinal"]
                       for entries in own_phases.values() for entry in entries)
            expectation = own_phases["expectation"][0]["payload"]
            assert expectation["state"] == "nonempty" and expectation["reason"] == "rendered_ids"
            assert expectation["rendered_text_present"] is True
            assert expectation["digest_ids"] == (["MEM-001", "MEM-002"] if exposure == "pointers" else ["MEM-001"])
            assert expectation["pointer_ids"] == ([] if exposure == "fit" else expectation["digest_ids"])
            assert expectation["full_body_ids"] == (["MEM-001"] if exposure == "fit" else [])
            assert expectation["budget"] == (1500 if exposure == "pointers" else 255 if exposure == "fit" else 172)
            callbacks = 2 if provider_plan["retry_once"] else 1
            assert [entry["payload"]["callback_count"] for entry in own_phases["launched"]] == list(range(1, callbacks + 1))
            assert own_phases["terminal"][0]["payload"]["launched_callbacks"] == callbacks
            assert own_phases["terminal"][0]["payload"]["success"] is True
            assert org.memory_collection.validate()["census_valid"] is True
            # Established report guards still withhold collection/epoch authority.
            backend = org.orchestrator._audit.compute_memory_telemetry_report()
            assert backend["decision"] == "insufficient_instrumentation"
            assert backend["observation_period"]["thresholds_met"] is False
            print(json.dumps({
                "producer_receipt": "rendered-memory-exposure", "executor": executor,
                "case": exposure, "org": "alpha", "task": task, "agent": agent,
                "runtime_session_id": sid,
                "observation": org.memory_collection.snapshot(),
                "host_sessions": daemon_state.host_session_store.snapshot(),
                "rows": [{"id": row["id"], "action": row["action"],
                          "row_scope": row["task_id"], "payload": payload}
                         for row, payload in own],
            }))
            return [row["id"] for row, _ in own[1:]]

        def provider_evidence(sid, resume, operation_count=3):
            observation = json.loads((paths.daemon_home() / "observations" / (sid + ".json")).read_text())
            assert observation["sid"] == sid
            if exposure != "pointers":
                header = "=== MEMORY-DIGEST (system) ===\nRelevant memory (pointers only — fetch bodies with `happyranch memory get <id>`):\n\n"
                block = (
                    "**Directive:** `MEM-001` — Bounded directive  (directive, salience 60)\n"
                    "Keep task credit scoped. Mention MEM-999 without exposing that item.\n\n"
                    if exposure == "fit" else
                    "- `MEM-001` — Bounded directive  (directive, salience 60)\n"
                )
                expected_digest = header + block
                assert len(expected_digest) == (255 if exposure == "fit" else 172)
                assert len(expected_digest.encode()) == (259 if exposure == "fit" else 176)
                assert expected_digest in observation["prompt"], observation["prompt"]
                assert "- `MEM-999`" not in observation["prompt"]
                assert "A genuinely nonshown scoped memory." not in observation["prompt"]
                results = json.loads(observation["operations"][1]["stdout"])
                assert {entry["id"] for entry in results["hits"]} == {"MEM-001", "MEM-999"}
                read = json.loads(observation["operations"][2]["stdout"])
                assert read["id"] == "MEM-999"
            candidate = Path(__file__).resolve().parents[1]
            assert Path(observation["cli"]).parent == Path(sys.executable).parent
            assert observation["imports"] == [str(candidate / "cli/__init__.py"),
                str(candidate / "runtime/__init__.py"), "0.1.0"]
            assert observation["python"] == [sys.executable, sys.version]
            argv = observation["argv"]
            if executor == "claude":
                assert "--resume" in argv, argv
                assert argv[argv.index("--resume") + 1] == resume
            else:
                assert argv[:3] == ["exec", "resume", resume]
            assert resume != sid
            assert [op["exit"] for op in observation["operations"]] == [0] * operation_count

        root_task_id = org.orchestrator.create_task("shipping memory digest root" if exposure == "pointers" else "Unrelated")
        if is_child:
            task_id = "TASK-SHIPPING-CHILD"
            org.db.insert_task(TaskRecord(
                id=task_id, brief="shipping memory digest child" if exposure == "pointers" else "Unrelated", team="engineering",
                parent_task_id=root_task_id, assigned_agent=agent,
            ))
        else:
            task_id = root_task_id
        resume = "provider-resume-distinct"
        result, _ = run_task(task_id, resume)
        assert result.success, (result.error, result.stdout_tail, result.stderr_tail)
        sid = started[task_id]
        assert result.session_id == sid
        persisted = org.db.get_task(task_id)
        assert (persisted.current_session_id, persisted.assigned_agent) == (sid, agent)
        audit_operations(task_id, sid, with_flags=True)
        provider_evidence(sid, resume, operation_count=7)
        report_cli = subprocess.run([
            str(Path(sys.executable).parent / "happyranch"), "memory", "report",
            "--org", "alpha", "--agent", agent, "--json",
        ], env={**os.environ, "HAPPYRANCH_RUNTIME_SESSION_ID": ""},
            capture_output=True, text=True, timeout=10)
        assert report_cli.returncode == 0, report_cli.stderr
        report_body = json.loads(report_cli.stdout)
        assert report_body["decision"] == "insufficient_instrumentation"
        assert report_body["observation_period"]["thresholds_met"] is False
        assert org.db.get_audit_logs_by_action("memory_collection_epoch_started") == []
        seal_response = httpx.get(
            f"http://127.0.0.1:{sock.getsockname()[1]}/api/v1/orgs/alpha/audit",
            params={"action": "memory_collection_seal"},
            headers={"Authorization": f"Bearer {paths.token_file().read_text().strip()}"},
        )
        assert seal_response.status_code == 200
        assert set(seal_response.json()) == {"entries", "next_cursor"}  # B1 is deferred.
        assert org.memory_collection.snapshot()["assigned_intents"] == 1
        # G3-P07/P10 negative-only: unregistered/provider/stale contexts remain
        # descriptive reads and cannot create an observer binding or intent.
        before_negative = org.db.fetch_all_readonly("SELECT max(id) AS id FROM audit_log")[0]["id"]
        for unbound in ("provider-resume-distinct", "sess-generated-unregistered", sid):
            negative = subprocess.run([
                str(Path(sys.executable).parent / "happyranch"), "memory", "get",
                "--org", "alpha", "--agent", agent, "MEM-001", "--session-id", unbound, "--json",
            ], env={**os.environ, "HAPPYRANCH_RUNTIME_SESSION_ID": ""},
                capture_output=True, text=True, timeout=10)
            assert negative.returncode == 0, negative.stderr
        negative_rows = org.db.fetch_all_readonly(
            "SELECT task_id, payload FROM audit_log WHERE id > ? AND action='memory_read' ORDER BY id",
            (before_negative,),
        )
        assert len(negative_rows) == 3
        for entry in negative_rows:
            body = json.loads(entry["payload"])
            assert entry["task_id"] == f"AGENT-{agent}"
            assert "task_id" not in body and "session_id" not in body
        assert org.memory_collection.snapshot()["assigned_intents"] == 1
        assert len(org.db.get_audit_logs_by_action("memory_runtime_binding")) == 1
        assert org.db.get_task(task_id).parent_task_id == (root_task_id if is_child else None)

        if exposure != "pointers":
            before = org.db.fetch_all_readonly("SELECT max(id) AS id FROM audit_log")[0]["id"]
            for verb, value in (("get", "MEM-999"), ("search", "scoped")):
                manual = subprocess.run([
                    str(Path(sys.executable).parent / "happyranch"), "memory", verb,
                    "--org", "alpha", "--agent", agent, value, "--json",
                ], env={**os.environ, "HAPPYRANCH_RUNTIME_SESSION_ID": ""},
                    capture_output=True, text=True, timeout=10)
                assert manual.returncode == 0, manual.stderr
            manual_rows = org.db.fetch_all_readonly(
                "SELECT action, task_id, agent, payload FROM audit_log WHERE id > ? "
                "AND action IN ('memory_read', 'memory_search') ORDER BY id", (before,),
            )
            assert [row["action"] for row in manual_rows] == ["memory_read", "memory_search"]
            for row in manual_rows:
                payload = json.loads(row["payload"])
                assert (row["task_id"], row["agent"]) == (f"AGENT-{agent}", agent)
                assert "session_id" not in payload and "task_id" not in payload
                if row["action"] == "memory_read":
                    assert payload.get("source", "explicit_or_other") == "explicit_or_other"

        # Same-agent sessions overlap with every operation explicitly ordered.
        # End and retire the root binding before the child's final read.
        if executor == "claude" and not is_child and exposure == "pointers":
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
        if executor == "claude" and not is_child and exposure == "pointers":
            # G3-P09: real provider exit/timeout and real HTTP cancellation,
            # plus actual launch-spec assembly failure before a process exists.
            for failure in ("nonzero", "timeout", "cancel", "launch_spec"):
                failed_task = org.orchestrator.create_task("owned provider failure")
                Path(str(provider) + ".plan.json").write_text(json.dumps({**provider_plan, "outcome": failure}))
                if failure == "launch_spec":
                    executor_type = type(org.orchestrator._build_executor(executor))
                    def refuse_spec(*args, **kwargs):
                        raise RuntimeError("controlled launch-spec refusal")
                    with monkeypatch.context() as fault:
                        fault.setattr(executor_type, "build_launch_spec", refuse_spec)
                        failed_result, _ = run_task(failed_task, resume)
                    assert failed_result.error == "controlled launch-spec refusal"
                elif failure == "cancel":
                    with ThreadPoolExecutor(max_workers=1) as pool:
                        future = pool.submit(run_task, failed_task, resume, 5)
                        try:
                            wait_until(lambda: failed_task in started and
                                       (paths.daemon_home() / "observations" /
                                        (started[failed_task] + ".failure-ready")).exists())
                            response = httpx.post(
                                f"http://127.0.0.1:{sock.getsockname()[1]}/api/v1/orgs/alpha/tasks/{failed_task}/cancel",
                                json={"rationale": "owned shipping cancellation"},
                                headers={"Authorization": f"Bearer {paths.token_file().read_text().strip()}"},
                            )
                            assert response.status_code == 200, response.text
                            failed_result, _ = future.result(timeout=8)
                            assert org.db.get_task(failed_task).status.value == "cancelled"
                        finally:
                            # An assertion failure still releases this owned process.
                            control = org.sessions.get_cancel_control(failed_task, agent)
                            if control is not None:
                                control()
                else:
                    failed_result, _ = run_task(failed_task, resume, 1 if failure == "timeout" else None)
                    if failure == "nonzero":
                        assert failed_result.returncode == 7
                    else:
                        assert "timed out" in (failed_result.error or "").lower()
                assert failed_result.success is False
                own_sid = started[failed_task]
                assert own_sid != sid and failed_result.session_id == own_sid
                terminal = [entry for entry in org.db.get_audit_logs(failed_task)
                            if entry["action"] == "memory_runtime_terminal"]
                assert len(terminal) == 1
                fact = terminal[0]["payload"]
                assert fact["session_id"] == own_sid and fact["task_id"] == failed_task
                assert fact["expectation_known"] is True and fact["binding_known"] is True
                assert fact["success"] is False and fact["outcome"] == "returned"
                assert fact["launched_callbacks"] == (0 if failure == "launch_spec" else 1)
                assert org.sessions.get_context_by_session(own_sid) is None
                assert org.memory_collection.validate()["census_valid"] is True
                print(json.dumps({"producer_receipt": "real-launch-failure", "case": failure,
                                  "task": failed_task, "runtime_session_id": own_sid,
                                  "returncode": failed_result.returncode, "terminal": fact}))
    finally:
        server.should_exit = True
        thread.join(timeout=5)
        sock.close()
        asyncio.run(daemon_state.close_all())
