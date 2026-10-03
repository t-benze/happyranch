"""Real task-bootstrap -> provider child -> CLI -> route/audit transport proof.

This deliberately uses the production ``Orchestrator._run_agent`` bootstrap and
a disposable executable provider.  The provider itself is fake, but the child
process, installed ``happyranch`` CLI, loopback daemon routes, SessionTracker
registration and SQLite audit store are the shipping implementations.
"""
from __future__ import annotations

import asyncio
import json
import socket
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
import uvicorn

from runtime.daemon import paths
from runtime.daemon.app import create_app
from runtime.daemon.agent_config import write_default_agent_config
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
    for contract in ("start-task", "jobs", "make-worktree", "thread", "dream", "todos"):
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
    write_default_agent_config(workspace)
    # Codex uses AGENTS.md as its readiness marker; Claude uses its own root.
    (workspace / "AGENTS.md").write_text("# shipping fixture\n")
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

    # The provider is an actual executable child.  It ignores its provider
    # resume argv and uses only the executor-supplied environment hint.
    provider = tmp_path / executor
    provider.write_text(
            "#!" + sys.executable + "\n"
            "import os, subprocess, sys, time\n"
            "barrier = os.getenv('TASK7910_BARRIER')\n"
            "if barrier:\n"
            " open(f'{barrier}/{os.environ[\"HAPPYRANCH_RUNTIME_SESSION_ID\"]}', 'w').close()\n"
            " deadline = time.monotonic() + 5\n"
            " while len(os.listdir(barrier)) < 2 and time.monotonic() < deadline: time.sleep(.01)\n"
            " if len(os.listdir(barrier)) < 2: sys.exit(70)\n"
            "cli = os.path.join(os.path.dirname(sys.executable), 'happyranch')\n"
        "base = [cli, 'memory', 'get', '--org', 'alpha', '--agent', 'dev_agent', 'MEM-001', '--json']\n"
        "one = subprocess.run(base, text=True, capture_output=True, env=os.environ)\n"
        "two = subprocess.run([cli, 'memory', 'search', '--org', 'alpha', '--agent', 'dev_agent', 'shipping', '--json'], text=True, capture_output=True, env=os.environ)\n"
        "three = subprocess.run(base[:-2] + ['MEM-002', '--json'], text=True, capture_output=True, env=os.environ)\n"
        "print(one.stdout + two.stdout + three.stdout)\n"
        "sys.exit(one.returncode or two.returncode or three.returncode)\n"
    )
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
        session_ids = iter(("sess-runtime-shipping", "sess-overlap-root", "sess-overlap-child"))
        monkeypatch.setattr(org.orchestrator, "_build_session_id", lambda: next(session_ids))
        root_task_id = org.orchestrator.create_task("shipping memory digest root")
        if is_child:
            task_id = "TASK-SHIPPING-CHILD"
            org.db.insert_task(TaskRecord(
                id=task_id, brief="shipping memory digest child", team="engineering",
                parent_task_id=root_task_id, assigned_agent=agent,
            ))
        else:
            task_id = root_task_id
        result, _ = org.orchestrator._run_agent(task_id, agent, "provider resume=provider-resume")
        assert result.success, (result.error, result.stdout_tail, result.stderr_tail)
        rows = org.db.fetch_all_readonly(
            "SELECT action, task_id, payload FROM audit_log "
            "WHERE action IN ('memory_digest_impression', 'memory_read', 'memory_search') ORDER BY id"
        )
        payloads = [(row["action"], row["task_id"], json.loads(row["payload"])) for row in rows]
        assert payloads[0] == ("memory_digest_impression", task_id, {
            "agent": agent, "session_id": "sess-runtime-shipping", "digest_ids": ["MEM-001", "MEM-002"],
            "digest_count": 2, "budget": 1500,
        })
        reads = [payload for action, _, payload in payloads if action == "memory_read"]
        read = reads[0]
        search = next(payload for action, _, payload in payloads if action == "memory_search")
        for payload in (read, search):
            assert payload["task_id"] == task_id
            assert payload["session_id"] == "sess-runtime-shipping"
        assert read["source"] == "digest"
        assert reads[1]["source"] == "digest"
        assert org.db.get_task(task_id).parent_task_id == (root_task_id if is_child else None)
        assert "provider-resume" not in result.stdout_tail

        # The immediate bounded concurrency acceptance is exercised once with
        # Claude: two real bootstrap sessions for the same agent are held at a
        # child-process barrier, then each canonical get/search pair is audited
        # only against its own registered task/session tuple.
        if executor == "claude" and not is_child:
            barrier = tmp_path / "barrier"
            barrier.mkdir()
            monkeypatch.setenv("TASK7910_BARRIER", str(barrier))
            overlap_root = org.orchestrator.create_task("overlap root")
            overlap_child = "TASK-SHIPPING-OVERLAP-CHILD"
            org.db.insert_task(TaskRecord(
                id=overlap_child, brief="overlap child", team="engineering",
                parent_task_id=overlap_root, assigned_agent=agent,
            ))
            with ThreadPoolExecutor(max_workers=2) as pool:
                concurrent_results = list(pool.map(
                    lambda candidate: org.orchestrator._run_agent(candidate, agent, "overlap"),
                    (overlap_root, overlap_child),
                ))
            assert all(result.success for result, _ in concurrent_results)
            expected = {
                candidate: result.session_id
                for candidate, (result, _) in zip(
                    (overlap_root, overlap_child), concurrent_results, strict=True,
                )
            }
            overlap_rows = org.db.fetch_all_readonly(
                "SELECT action, payload FROM audit_log WHERE action IN ('memory_read', 'memory_search')"
            )
            observed = [(row["action"], json.loads(row["payload"])) for row in overlap_rows]
            for expected_task, expected_session in expected.items():
                own = [row for _, row in observed if row.get("task_id") == expected_task]
                assert {row["session_id"] for row in own} == {expected_session}
                reads = [row for action, row in observed if action == "memory_read" and row.get("task_id") == expected_task]
                assert {row["source"] for row in reads} == {"digest"}
    finally:
        server.should_exit = True
        thread.join(timeout=5)
        sock.close()
        asyncio.run(daemon_state.close_all())
