"""End-to-end: two orgs each submit a task; both run to completion under
one daemon, and neither org's audit log mentions the other's task.

Style note: this file mirrors ``test_end_to_end.py`` — same ``live_daemon``
fixture, same ``_auth_headers`` helper, same ``httpx`` direct calls. The
plan (docs/superpowers/plans/2026-04-28-parallel-multi-org-runtime.md
§Task 24) sketched the flow using a ``daemon_under_test`` fixture with
``bootstrap_minimal_agent`` / ``task_status`` / ``audit_log`` helpers
that don't exist; we adapt to the project's established HTTP integration
pattern while preserving the plan's spirit — exercise daemon routes
end-to-end across two concurrent orgs.

Isolation invariant: each org has its own SQLite DB and its own
``next_task_id`` counter, so both orgs naturally produce ``TASK-001`` as
their first task. The test asserts that:

1. Both orgs reach ``completed``.
2. Org alpha's audit-log query for ``TASK-001`` returns entries tagged
   to alpha (e.g. ``session_start`` for agents in alpha) and never
   bleeds into beta — and vice versa.
"""
from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from tests.integration.conftest import seed_workspace


pytestmark = pytest.mark.integration


def _auth_headers() -> dict:
    from src.daemon import paths

    return {"Authorization": f"Bearer {paths.read_token()}"}


def _org_base(port: str, slug: str) -> str:
    return f"http://127.0.0.1:{port}/api/v1/orgs/{slug}"


def _global_base(port: str) -> str:
    return f"http://127.0.0.1:{port}/api/v1"


def _make_example_tree(tmp_path: Path) -> Path:
    """Build a minimal example tree with engineering_head + dev_agent in
    the engineering team. POST /orgs --from_example copies <tree>/org/
    verbatim into <runtime>/orgs/<slug>/org/, so this tree only needs an
    ``org/`` subdir with ``teams.yaml`` and ``agents/_pending/``."""
    tree = tmp_path / "example_org"
    org = tree / "org"
    (org / "agents" / "_pending").mkdir(parents=True)
    (org / "teams.yaml").write_text(
        "teams:\n"
        "  engineering:\n"
        "    manager: engineering_head\n"
        "    workers: [dev_agent]\n"
    )
    return tree


def _init_org(port: str, slug: str, example: Path) -> None:
    r = httpx.post(
        f"{_global_base(port)}/orgs",
        json={"slug": slug, "from_example": str(example)},
        headers=_auth_headers(),
        timeout=5.0,
    )
    assert r.status_code == 200, r.text


def _submit_task(port: str, slug: str, brief: str) -> str:
    r = httpx.post(
        f"{_org_base(port, slug)}/tasks",
        json={"type": "general", "brief": brief},
        headers=_auth_headers(),
        timeout=5.0,
    )
    assert r.status_code == 200, r.text
    return r.json()["task_id"]


def _wait_for_completed(port: str, slug: str, task_id: str, timeout: float) -> None:
    import time

    deadline = time.monotonic() + timeout
    last: dict = {}
    while time.monotonic() < deadline:
        r = httpx.get(
            f"{_org_base(port, slug)}/tasks/{task_id}",
            headers=_auth_headers(),
            timeout=5.0,
        )
        if r.status_code == 200:
            last = r.json()
            status = last["task"]["status"]
            if status in ("completed", "failed"):
                assert status == "completed", (slug, task_id, last)
                return
        time.sleep(0.2)
    raise AssertionError(
        f"task {slug}/{task_id} did not complete within {timeout}s "
        f"(last body={last})"
    )


def test_two_orgs_run_tasks_concurrently_under_one_daemon(
    live_daemon,
    runtime_container: Path,
    fake_plan_env: Path,
    tmp_path: Path,
) -> None:
    """Submit a task to alpha and beta in quick succession; assert both
    complete and that each org's audit log for TASK-001 is namespaced to
    that org alone."""
    port = live_daemon

    # Plan: every fake-claude session reports completion. The script reads
    # $org_slug ($4) from PWD-derived extraction in fake_claude.sh, so the
    # same plan body works for alpha and beta.
    fake_plan_env.write_text(
        '#!/usr/bin/env bash\n'
        'set -e\n'
        'task_id=$1; session_id=$2; agent=$3; org_slug=$4\n'
        'opc report-completion --org "$org_slug" \\\n'
        '  --task-id "$task_id" --session-id "$session_id" \\\n'
        '  --agent engineering_head --status completed --confidence 90 \\\n'
        '  --summary \'{"action":"done","summary":"ok"}\'\n'
    )
    fake_plan_env.chmod(0o755)

    # Materialize alpha and beta orgs alongside the conftest-seeded
    # "test" org.
    example = _make_example_tree(tmp_path)
    _init_org(port, "alpha", example)
    _init_org(port, "beta", example)

    # Seed the engineering_head workspace inside each org's per-org root.
    alpha_root = runtime_container / "orgs" / "alpha"
    beta_root = runtime_container / "orgs" / "beta"
    seed_workspace(alpha_root, "engineering_head")
    seed_workspace(beta_root, "engineering_head")

    # Submit two tasks in rapid succession (same Python tick — the daemon
    # is async and should interleave them across its worker pool).
    tid_a = _submit_task(port, "alpha", "alpha ping")
    tid_b = _submit_task(port, "beta", "beta ping")

    # Per-org task counters are independent — both should be TASK-001.
    assert tid_a == "TASK-001", tid_a
    assert tid_b == "TASK-001", tid_b

    # Both reach completed within 30s.
    _wait_for_completed(port, "alpha", tid_a, timeout=30.0)
    _wait_for_completed(port, "beta", tid_b, timeout=30.0)

    # Audit isolation: alpha's audit for TASK-001 reports work in alpha;
    # beta's audit for TASK-001 reports work in beta. Neither query
    # surfaces the other org's session ids.
    r_a = httpx.get(
        f"{_org_base(port, 'alpha')}/audit",
        params={"task_id": tid_a},
        headers=_auth_headers(),
        timeout=5.0,
    )
    assert r_a.status_code == 200, r_a.text
    audit_a = r_a.json()["entries"]

    r_b = httpx.get(
        f"{_org_base(port, 'beta')}/audit",
        params={"task_id": tid_b},
        headers=_auth_headers(),
        timeout=5.0,
    )
    assert r_b.status_code == 200, r_b.text
    audit_b = r_b.json()["entries"]

    assert audit_a, "alpha audit empty"
    assert audit_b, "beta audit empty"

    # Sanity: each side recorded a session_start for engineering_head.
    starts_a = [
        e for e in audit_a
        if e.get("action") == "session_start" and e.get("agent") == "engineering_head"
    ]
    starts_b = [
        e for e in audit_b
        if e.get("action") == "session_start" and e.get("agent") == "engineering_head"
    ]
    assert starts_a, audit_a
    assert starts_b, audit_b

    # Cross-org isolation: alpha's session_start payload points at a
    # workspace under <container>/orgs/alpha/...; beta's points at
    # <container>/orgs/beta/.... The audit shard is per-org SQLite, so
    # neither query can leak the other org's rows even if task_ids
    # collide (and they do — both are TASK-001).
    workspaces_a = [
        e["payload"]["workspace"] for e in starts_a if e.get("payload")
    ]
    workspaces_b = [
        e["payload"]["workspace"] for e in starts_b if e.get("payload")
    ]
    assert all("/orgs/alpha/" in w for w in workspaces_a), workspaces_a
    assert all("/orgs/beta/" in w for w in workspaces_b), workspaces_b
    assert all("/orgs/beta/" not in w for w in workspaces_a), workspaces_a
    assert all("/orgs/alpha/" not in w for w in workspaces_b), workspaces_b
