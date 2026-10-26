#!/usr/bin/env python3
"""Finite disposable startup checks, outside automatic CI (ROOT13137).

Use only an explicitly allocated invocation. --case C12 selects the SAME
counter assertions for the production-defect/restoration pair. No pytest,
integration controller, installed runtime, provider or old fixture is used.
The JSON receipt distinguishes individual observations from complete cases.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
import re
import resource
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import traceback

SOURCE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SOURCE))
MESSAGE = "Owned startup fixture transcript — 恢复期间可读"
WEB_TESTS = (
    "src/lib/startup.test.ts",
    "src/design-system/providers/_real-health.test.tsx",
    "src/design-system/providers/_real-threads.test.ts",
    "src/design-system/providers/_real-threads-paging.test.ts",
    "src/design-system/layouts/AppShell/shell-i18n.test.tsx",
    "src/features/health/health.i18n.test.tsx",
    "src/features/orgs/AddOrgDialog.test.tsx",
    "src/features/threads/ThreadsPage.i18n.test.tsx",
    "src/features/threads/ThreadsPage.responsive.test.tsx",
    "src/features/threads/ThreadsPage.test.tsx",
    "src/features/threads/ThreadsPage.thr209.test.tsx",
    "src/shared/threads/NewThreadDialog.i18n.test.tsx",
    "src/test/openapi-coverage.test.ts",
)


class Checks:
    def __init__(self, args):
        self.args = args
        self.started = time.monotonic()
        self.rows = []
        self.details = {"case": args.case,
                        "pid": os.getpid(), "python": sys.executable,
                        "python_version": sys.version, "cwd": str(Path.cwd()),
                        "root": str(args.root), "source": str(SOURCE),
                        "head": git("rev-parse", "HEAD"),
                        "tree": git("rev-parse", "HEAD^{tree}"),
                        "working_status": git("status", "--porcelain=v1"),
                        "allocation": "one actual top-level process; no reset"}

    def require(self, name, actual, expected=True):
        passed = actual == expected
        self.rows.append({"assertion": name, "status": "PASS" if passed else "FAIL",
                          "actual": actual, "expected": expected})
        print(json.dumps(self.rows[-1], ensure_ascii=False), flush=True)
        # Deliberately collect both C12 assertions even after one fails.
        return passed

    def missing(self, name, reason):
        self.rows.append({"assertion": name, "status": "NOT RUN", "reason": reason})

    def save(self):
        self.details["wall_seconds"] = time.monotonic() - self.started
        self.details["rss"] = {"value": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
                               "units": "KiB" if sys.platform.startswith("linux") else "bytes",
                               "platform": sys.platform, "scope": "driver process only"}
        self.details["fixture_file_bytes"] = sum(
            p.stat().st_size for p in self.args.root.rglob("*")
            if p.is_file() and not p.is_symlink())
        if self.details["fixture_file_bytes"] > 512 * 1024 * 1024:
            self.rows.append({"assertion": "fixture_byte_cap", "status": "FAIL"})
        self.args.output.write_text(json.dumps({"details": self.details, "assertions": self.rows},
                                              indent=2, ensure_ascii=False) + "\n")


def git(*args):
    return subprocess.check_output(["git", "-C", str(SOURCE), *args], text=True).strip()


def isolate(args):
    parent = args.root
    if not parent.is_absolute() or not parent.is_dir() or parent.resolve() != parent:
        raise ValueError("a literal absolute task scratch parent is required")
    if not any(p.name == "task-tmp" and p.parent.name == ".happyranch" for p in parent.parents):
        raise ValueError("fixture must be below workspace .happyranch/task-tmp")
    # Each process creates a new exclusive child; the SAME C12 command can
    # run under the defect and after byte restoration without deleting data,
    # replenishing a fixture allowance, or overwriting the prior receipt.
    root = Path(tempfile.mkdtemp(prefix="startup-", dir=parent))
    args.root = root
    if not args.output.is_dir():
        raise ValueError("existing task output directory required")
    args.output = args.output / f"startup-{args.case}-{os.getpid()}.json"
    print("receipt", args.output, flush=True)
    for key, child in (("HOME", "home"), ("XDG_CONFIG_HOME", "config"),
                       ("XDG_CACHE_HOME", "cache"), ("TMPDIR", "temp"),
                       ("HAPPYRANCH_DAEMON_HOME", "daemon")):
        path = root / child
        path.mkdir(mode=0o700)
        os.environ[key] = str(path)
    os.environ["HAPPYRANCH_RUNTIME_ROOT"] = str(root / "runtime")
    os.environ["HAPPYRANCH_PROJECT_ROOT"] = str(SOURCE)
    (root / "daemon/config.yaml").write_text("queue_workers: 1\nexecutor_rate_limit_backoff_seconds: []\n")
    for key in tuple(os.environ):
        if key.startswith(("ANTHROPIC_", "OPENAI_", "CODEX_", "CLAUDE_", "PI_", "OPENCODE_", "GEMINI_")):
            os.environ.pop(key)
    if args.web_dist is not None:
        if not args.web_dist.is_dir() or not args.web_dist.is_relative_to(parent):
            raise ValueError("only the task-owned ordinary Web build is admitted")
        os.environ["HAPPYRANCH_WEB_DIST"] = str(args.web_dist)


def seed_counter(db):
    """Canonical immutable control, binding, INTEGER callback and attempt writers."""
    from runtime.models import (TaskRecord, TaskStatus, AuthorityPolicyV2SessionBinding,
                                AuthorityPolicyV2ManagerSelfEvaluation,
                                AUTHORITY_POLICY_V2_CONTRACT_ID, AUTHORITY_POLICY_V2_CONTRACT_VERSION,
                                authority_policy_v2_contract_digest, authority_policy_v2_canonical_json_bytes)
    selector = db.ensure_authority_selector("engineering")
    receipt = db.create_and_activate_authority_policy_v2({
        "team": "engineering", "policy_id": "startup-fixture", "title": "Disposable startup case",
        "create_request_id": "startup-create", "activation_request_id": "startup-activate",
        "based_on_selector_id": selector.selector_id, "expected_selector_id": selector.selector_id,
        "action": "bootstrap", "what_to_escalate": "Unknown external authority",
        "what_not_to_escalate": "Read-only disposable observations",
    })
    # This is the synthetic fixture's own canonical callback, never an agent callback
    # against the daemon running this task. All referenced records originate here.
    task_id, agent, sid = "TASK-001", "case_manager", "startup-fixture-session"
    db.insert_task(TaskRecord(id=task_id, status=TaskStatus.ESCALATED, assigned_agent=agent,
                              current_session_id=sid, brief="Disposable counter observation"))
    binding = db.bind_authority_policy_v2_session(AuthorityPolicyV2SessionBinding(
        team="engineering", root_task_id=task_id, manager_agent=agent, manager_session_id=sid,
        activation_epoch=receipt.selector_epoch, activation_id=receipt.activation_id,
        selector_id=receipt.selector_id, release_id=receipt.release_id,
        policy_digest=receipt.policy_digest, policy_version=receipt.release_version,
        contract_id=AUTHORITY_POLICY_V2_CONTRACT_ID,
        contract_version=AUTHORITY_POLICY_V2_CONTRACT_VERSION,
        contract_digest=authority_policy_v2_contract_digest(),
        provider_id="fixture", executor_kind="claude", model_id="fixture/no-provider"))
    fields = binding.model_dump(mode="json", exclude={"created_at", "team", "selector_id", "manager_agent"})
    assessment = AuthorityPolicyV2ManagerSelfEvaluation(**fields,
        what_to_escalate={"applicability": "does_not_apply", "confidence": 100, "uncertainty_codes": []},
        what_not_to_escalate={"applicability": "applies", "confidence": 100, "uncertainty_codes": []})
    canonical = authority_policy_v2_canonical_json_bytes(assessment.model_dump(mode="json"))
    admission = {key: getattr(binding, key) for key in (
        "team", "contract_id", "contract_version", "contract_digest", "release_id",
        "activation_id", "activation_epoch", "selector_id")}
    admission.update(binding_id=binding.binding_id, assessment_digest=hashlib.sha256(canonical).hexdigest(),
                     assessment_canonical_json=canonical.decode(), origin_boot_id=db._v2_process_boot_id)
    if not db.admit_task_completion_callback(task_id=task_id, agent=agent, session_id=sid,
            output_summary="Owned synthetic callback", confidence_score=100,
            decision_json='{"action":"done","summary":"Owned fixture"}', v2_admission=admission):
        raise AssertionError("canonical callback refused; no fabricated attempt fallback")
    return task_id


def counters(checks, db):
    """Independent SQL trace plus entry count of the ORIGINAL shipping body."""
    from runtime.infrastructure.db.authority_v2_attempts import _V2StartupReads
    observations = {"sql_fetches": 0, "history_entries": 0}
    code = _V2StartupReads._history.__code__

    def profile(frame, event, _arg):
        if event == "call" and frame.f_code is code:
            observations["history_entries"] += 1

    def sql(statement):
        if re.match(r"^SELECT \* FROM audit_log WHERE task_id\s*=.*ORDER BY id$", statement.strip()):
            observations["sql_fetches"] += 1

    if sys.getprofile() is not None:
        raise RuntimeError("existing observer present; cannot replace it")
    db._conn.set_trace_callback(sql)
    sys.setprofile(profile)
    try:
        with db._authority_policy_v2_startup_reads() as reads:
            targets = reads.discover()
            if len(targets) != 1 or targets[0][1] is None:
                raise AssertionError("canonical C12 history lacks one authenticated root anchor")
            target, anchor = targets[0]
            exact, exact_anchor = reads.exact(target)
            if exact != target or exact_anchor != anchor:
                raise AssertionError("original exact/discovery changed canonical fixture identity")
        actual = reads.statistics()
    finally:
        sys.setprofile(None)
        db._conn.set_trace_callback(None)
    checks.details["C12"] = {"observer": observations, "actual": actual}
    checks.require("C12_nonzero_independent_SQL_and_original_entries",
                   observations["sql_fetches"] > 0 and observations["history_entries"] > observations["sql_fetches"])
    checks.require("C12_history_fetch_count_matches_sql", actual["history_fetches"], observations["sql_fetches"])
    checks.require("C12_fetch_is_not_cache_hit", actual["history_hits"],
                   observations["history_entries"] - observations["sql_fetches"])


def seed(checks):
    # Literal fit is recorded BEFORE the first product constructor. Runtime
    # producers must remain inside it; a refusal is not replaced with raw SQL.
    fit = {"orgs": 1 if checks.args.case == "C12" else 2, "roots": 1,
           "tasks": 1, "results": 1, "v2_history_and_controls": 11,
           "workflow_causal_rows": 0, "threads": 0 if checks.args.case == "C12" else 2,
           "messages": 0 if checks.args.case == "C12" else 2, "tokens": 0, "jobs": 0,
           "provider_children": 0, "listeners": 0 if checks.args.case == "C12" else 1,
           "browser_contexts": 0}
    checks.details["fixture_fit_before_constructors"] = fit
    checks.save()
    from runtime.infrastructure.database import Database
    from runtime.infrastructure.workflow_schema import initialize_complete_org_schema
    from runtime.models import ThreadRecord, ThreadMessageKind
    from runtime.runtime import RuntimeDir
    runtime = RuntimeDir.init(checks.args.root / "runtime")
    for index, slug in enumerate(("fixture-a", "fixture-b")[:fit["orgs"]]):
        org_root = runtime.orgs_dir / slug
        shutil.copytree(SOURCE / "e2e/template/org", org_root / "org")
        # No Product E2E controller, provider registry/profile or existing home
        # is copied. Only tracked synthetic declarative org input is selected.
        for name in ("case_manager", "case_worker", "code_reviewer"):
            (org_root / "workspaces" / name).mkdir(parents=True)
        db = Database(org_root / "happyranch.db")
        try:
            initialize_complete_org_schema(db, expected_org_slug=slug, layout="I")
            if index == 0:
                # Native constructor owns this UUID; no foreign boot identity.
                from uuid import uuid4
                db.bind_authority_policy_v2_process_boot_id(str(uuid4()))
                seed_counter(db)
                # Match a cold startup connection, rather than retaining the
                # schema constructor's TEMP schema. Its presence deliberately
                # disables sharing in the production reader's bookend check.
                db.close()
                db = Database(org_root / "happyranch.db")
                checks.details["C12_connection"] = {
                    "databases": [tuple(row) for row in db._conn.execute("PRAGMA database_list")],
                    "in_transaction": db._conn.in_transaction,
                    "isolation_level": db._conn.isolation_level,
                    "autocommit": db._conn.autocommit,
                }
                counters(checks, db)
            if checks.args.case != "C12":
                db.insert_thread(ThreadRecord(id="THR-001", subject="Owned recovery transcript"))
                db.append_thread_message(thread_id="THR-001", speaker="founder",
                                         kind=ThreadMessageKind.MESSAGE, body_markdown=MESSAGE)
            checks.details.setdefault("fixture_counts", {})[slug] = {
                name: db._conn.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0]
                for (name,) in db._conn.execute("SELECT name FROM sqlite_schema WHERE type='table'")}
        finally:
            db.close()
    totals = {}
    for counts in checks.details["fixture_counts"].values():
        for table, count in counts.items():
            totals[table] = totals.get(table, 0) + count
    checks.details["cumulative_created_rows"] = totals
    for table, maximum in (("tasks", 64), ("threads", 8), ("thread_messages", 64),
                           ("thread_invocations", 16), ("jobs", 8)):
        if totals.get(table, 0) > maximum:
            raise RuntimeError("original fixture cap exceeded: " + table)
    v2_rows = sum(count for table, count in totals.items()
                  if table.startswith("authority_policy_v2") or table in (
                      "authority_policy_selector_history", "authority_policy_active_selectors", "audit_log"))
    if v2_rows > 64:
        raise RuntimeError("original v2/history fixture row cap exceeded")
    return runtime


def request_path(template):
    substitutions = {"slug": "fixture-a", "thread_id": "THR-001", "task_id": "TASK-001"}
    return re.sub(r"\{([^}:]+)(?::[^}]+)?\}", lambda m: substitutions.get(m[1], "fixture-absent"), template)


def database_observation(runtime):
    """Independent RO rows, without acquiring the held canonical mutex."""
    result = {}
    deadline, row_count, byte_count = time.monotonic() + 2, 0, 0
    for slug, root in runtime.iter_org_roots():
        conn = sqlite3.connect((root / "happyranch.db").as_uri() + "?mode=ro", uri=True, timeout=.05)
        try:
            conn.execute("PRAGMA query_only=ON")
            conn.execute("BEGIN")
            tables = [row[0] for row in conn.execute("SELECT name FROM sqlite_schema WHERE type='table'")]
            rows = {}
            for table in sorted(tables):
                quoted = table.replace('"', '""')
                material = []
                for row in conn.execute(f'SELECT * FROM "{quoted}"'):
                    row_count += 1
                    if row_count > 4096 or time.monotonic() >= deadline:
                        raise RuntimeError("independent fixture observation budget exhausted")
                    raw = repr(tuple(row)).encode()
                    byte_count += len(raw)
                    if byte_count > 4 * 1024 * 1024:
                        raise RuntimeError("independent fixture observation bytes exhausted")
                    material.append(raw)
                rows[table] = hashlib.sha256(b"\0".join(sorted(material))).hexdigest()
            result[slug] = rows
        finally:
            conn.close()
    return result


async def serving(checks, runtime):
    import httpx
    import uvicorn
    from runtime.config import Settings
    from runtime.daemon import __main__ as daemon, runtimes, paths
    from runtime.daemon.app import create_app
    from runtime.daemon.startup_recovery import StartupUnavailable
    runtimes.register(runtime.root)
    token = paths.ensure_token()
    state = daemon._build_state(Settings(project_root=SOURCE))
    if set(state.orgs) != {"fixture-a", "fixture-b"}:
        raise AssertionError("P0 failed to attach both original fixture orgs")
    app = create_app(state)
    wire = []

    class ObserveReceive:
        def __init__(self, app):
            self.app = app

        async def __call__(self, scope, receive, send):
            if scope["type"] != "http":
                return await self.app(scope, receive, send)
            row = {"method": scope["method"], "raw_path": scope["raw_path"].decode("ascii"),
                   "path": scope["path"], "query": scope["query_string"].decode("ascii"),
                   "case": dict(scope["headers"]).get(b"x-startup-case", b"").decode("ascii"),
                   "receive_calls": 0}
            wire.append(row)
            if len(wire) > 1024:
                raise RuntimeError("wire observation cap")

            async def counted_receive():
                row["receive_calls"] += 1
                return await receive()

            return await self.app(scope, counted_receive, send)

    app.add_middleware(ObserveReceive)
    before = database_observation(runtime)
    original = daemon._sweep_on_startup
    entered, release = threading.Event(), threading.Event()
    wait_error = []

    def held(db, queue, slug, orchestrator=None):
        if slug == "fixture-a":
            with db._lock:
                checks.details["native_sweep_thread"] = threading.get_ident()
                entered.set()
                if not release.wait(10):
                    wait_error.append("held-window expired")
                    raise TimeoutError("owned native hold exceeded10s")
        return original(db, queue, slug, orchestrator)

    daemon._sweep_on_startup = held
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    server = daemon._DiagnosticServer(uvicorn.Config(app, host="127.0.0.1", port=port,
        log_level="warning", timeout_graceful_shutdown=15), app)
    handle = asyncio.create_task(server.serve(sockets=[sock]))
    checks.details["listener"] = {"host": "127.0.0.1", "port": port}
    checks.details["request_loop_thread"] = threading.get_ident()
    try:
        deadline = time.monotonic() + 8
        while not (server.started and entered.is_set()):
            if handle.done():
                await handle
                raise AssertionError("listener ended before held recovery")
            if time.monotonic() >= deadline:
                raise TimeoutError("listener/hold not observed")
            await asyncio.sleep(.01)
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}",
                headers={"Authorization": "Bearer " + token}, timeout=2, follow_redirects=False) as client:
            ready = await client.get("/api/v1/health/ready")
            checks.require("C09_held_ready503", ready.status_code, 503)
            checks.require("C09_held_state", ready.json()["startup"]["state"], "recovering")
            checks.require("C01_recovery_off_request_loop",
                           checks.details["native_sweep_thread"] != threading.get_ident())
            for slug in ("fixture-a", "fixture-b"):
                response = await client.get(f"/api/v1/orgs/{slug}/threads/THR-001/messages?limit=10")
                checks.require(f"C01_{slug}_messages_read_while_writer_held", response.status_code, 200)
                checks.require(f"C01_{slug}_known_transcript_bytes", MESSAGE in response.text)
                negative = await client.get(f"/api/v1/orgs/{slug}/threads?limit=-1")
                checks.require(f"C10_{slug}_negative_limit_refuses", negative.status_code, 503)
                checks.require(f"C10_{slug}_negative_limit_code",
                               negative.json().get("detail", {}).get("code"), "startup_reader_unbounded_request")
                for suffix in ("?page_size=2", "/THR-001"):
                    read = await client.get(f"/api/v1/orgs/{slug}/threads" + suffix)
                    checks.require(f"C01_{slug}_original_read_{suffix}", read.status_code, 200)
                    checks.require(f"C01_{slug}_original_identity_{suffix}", "THR-001" in read.text)
                denied = await client.get(f"/api/v1/orgs/{slug}/threads?page_size=2",
                                          headers={"Authorization": "Bearer invalid-fixture-token"})
                checks.require(f"C02_{slug}_original_auth_preserved", denied.status_code, 401)
            # Original direct producer gate; no queue or host is replaced.
            try:
                state._require_startup_ready()
            except StartupUnavailable as exc:
                checks.require("C03_direct_gate_refuses", exc.code, "daemon_recovering")
            else:
                checks.require("C03_direct_gate_refuses", False)
            if checks.args.http_inventory:
                entries = json.loads(checks.args.http_inventory.read_text())["entries"]
                async def observe_held(entry):
                    # Real loopback request with a bounded body. This observes
                    # status/code only; the separate prebody oracle is not
                    # inferred from it and remains explicit below.
                    response = await client.request(entry["method"], request_path(entry["path"]),
                                                    content=b"{}", headers={"Content-Type": "application/json",
                                                                             "X-Startup-Case": entry["id"]})
                    checks.require(entry["id"] + "_held_status", response.status_code, 503)
                    checks.require(entry["id"] + "_held_code",
                                   response.json().get("detail", {}).get("code"), "daemon_recovering")
                    matching = [row for row in wire if row["case"] == entry["id"]]
                    checks.require(entry["id"] + "_one_observed_request", len(matching), 1)
                    if len(matching) == 1:
                        checks.require(entry["id"] + "_prebody_receive_zero", matching[0]["receive_calls"], 0)
                    checks.missing(entry["id"] + "_ready_outcome",
                                   "held refusal does not establish original post-ready handler")
                held_entries = []
                for entry in entries:
                    if entry["early"].startswith("held:"):
                        held_entries.append(entry)
                    else:
                        checks.missing(entry["id"], "full original-auth/content oracle not selected by this probe")
                # Eight is the ORIGINAL concurrent-request ceiling. Serial
                # batches avoid extending or replenishing the native hold.
                for offset in range(0, len(held_entries), 8):
                    await asyncio.gather(*(observe_held(entry) for entry in held_entries[offset:offset + 8]))
            checks.require("C02_held_all_original_rows_unchanged", database_observation(runtime), before)
            checks.details["wire"] = wire
            release.set()
            deadline = time.monotonic() + 15
            while True:
                response = await client.get("/api/v1/health/ready")
                status = response.json()["startup"]
                if status["state"] == "recovery_failed":
                    checks.details["failed_startup"] = state._startup_recovery.snapshot()
                    raise AssertionError("original recovery failed; retain owned first cause and no activation claim")
                if response.status_code == 200:
                    break
                if time.monotonic() >= deadline:
                    raise TimeoutError("original recovery did not become ready")
                await asyncio.sleep(.02)
            checks.require("C09_original_ready_commit", status["ready_commits"], 1)
            checks.require("C09_original_ready_serving", status["serving"] and status["recovery_ready"])
            state._require_startup_ready()
            response = await client.get("/api/v1/orgs/fixture-a/threads/THR-001/messages?limit=10")
            checks.require("C01_post_ready_transcript", response.status_code == 200 and MESSAGE in response.text)
            checks.details["completed_startup"] = state._startup_recovery.snapshot()
            checks.require("C10_owned_reader_admissions_retired", state._startup_reads.snapshot()["active"], 0)
            state._startup_recovery.stop()
            response = await client.get("/api/v1/health/ready")
            checks.require("C08_stop_closes_ready", response.status_code, 503)
    finally:
        release.set()
        server.should_exit = True
        try:
            await asyncio.wait_for(asyncio.shield(handle), timeout=20)
            checks.require("C08_original_lifespan_positive_close", state._shutdown_complete)
        finally:
            daemon._sweep_on_startup = original
            sock.close()
        if wait_error:
            raise AssertionError(wait_error[0])


def web(checks):
    command = ["npm", "--prefix", str(SOURCE / "web"), "run", "test", "--", "run",
               "--maxWorkers=1", "--minWorkers=1", *WEB_TESTS]
    checks.details["web_command"] = command
    remaining = 600 - (time.monotonic() - checks.started)
    result = subprocess.run(command, cwd=SOURCE, timeout=max(1, remaining))
    checks.require("Web_selected_component_keeper_command", result.returncode, 0)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", choices=("all", "C12"), default="all")
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--web-dist", type=Path)
    parser.add_argument("--http-inventory", type=Path)
    parser.add_argument("--web", action="store_true")
    args = parser.parse_args()
    isolate(args)  # precedes EVERY product import and constructor
    checks = Checks(args)
    try:
        if args.case == "all" and args.web:
            web(checks)
        runtime = seed(checks)
        if args.case == "all":
            asyncio.run(serving(checks, runtime))
        for number in range(1, 16):
            checks.missing(f"C{number:02d}_complete_acceptance",
                "only named assertions executed; populated workflow/coding/direct/tail/browser obligations remain separate")
        checks.missing("C15_under10s", "excluded: operator-authorized launcher/API/real-UI observation required")
    except BaseException as exc:
        checks.rows.append({"assertion": "driver_completion", "status": "FAIL", "exception": type(exc).__name__,
                            "reason": str(exc)[:512]})
        traceback.print_exc()
    finally:
        checks.save()
    return int(any(row["status"] == "FAIL" for row in checks.rows))


if __name__ == "__main__":
    raise SystemExit(main())
