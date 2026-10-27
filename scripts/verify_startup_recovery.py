#!/usr/bin/env python3
"""Finite disposable startup checks, outside automatic CI (ROOT13137).

Use only an explicitly allocated invocation. --case C12 selects the SAME
counter assertions for the production-defect/restoration pair. No pytest,
integration controller, installed runtime, provider or old fixture is used.
The JSON receipt distinguishes individual observations from complete cases.
"""
from __future__ import annotations

import argparse
import copy
from contextvars import ContextVar
import asyncio
import hashlib
import json
import os
from pathlib import Path
import re
import resource
import shlex
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import traceback
from urllib.parse import unquote, urlsplit

SOURCE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SOURCE))
MESSAGE = "Owned startup fixture transcript — 恢复期间可读"
WEB_TESTS = (
    "src/lib/startup.test.ts",
    "src/lib/threadErrors.i18n.test.ts",
    "src/test/identity-names.test.tsx",
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


ASSET_BYTES = b"Owned ROOT13137 startup gate asset\n"
LITERAL_IDS = tuple(f"HX09.{n:02d}" for n in range(1, 10)) + tuple(
    f"HX10.{n:02d}" for n in range(1, 14))
EARLY_IDS = ("H001", "H003", "H008", "H011", "H153", "H156", "H157", "HX01")
TASK_DIRECT = {
    "D001": ("runtime/daemon/runner.py", "enqueue_task"),
    **{f"D{n:03d}": ("runtime/daemon/queue.py", "TaskQueue." + symbol)
       for n, symbol in enumerate(("enqueue", "put_nowait", "enqueue_if_absent", "_consume",
                                  "_submit", "start_workers"), 2)},
    "D008": ("runtime/daemon/dispatcher.py", "Dispatcher.run_step"),
    "D009": ("runtime/daemon/dispatcher.py", "Dispatcher.task_admission_allowed"),
    **{f"D{n:03d}": ("runtime/orchestrator/orchestrator.py", "Orchestrator." + symbol)
       for n, symbol in enumerate(("run_step", "_run_agent", "_run_agent_impl",
                                  "_launch_agent_with_scratch", "_run_agent_launch_contained"), 10)},
    "D017": ("runtime/orchestrator/run_step.py", "run_step_impl"),
    "D086": ("runtime/orchestrator/host_supervisor.py", "HostSessionSupervisor.run"),
    "D087": ("runtime/orchestrator/host_supervisor.py", "HostSessionSupervisor._execute_attempt"),
    "D088": ("runtime/daemon/sessions.py", "SessionTracker.set_active"),
    "D089": ("runtime/daemon/sessions.py", "SessionTracker.register_recovery_session"),
}
TASK_PHASE_ASSERTIONS = {
    "recovering": ("original_entry", "startup_refusal", "queue_unchanged", "effects_unchanged"),
    "ready": ("original_entry", "owned_effect", "native_marker", "positive_retirement"),
}
# Independent mandatory selections, never inferred from collected PASS rows.
REMAINING = {
    "C01": "populated workflow/returned graph and real browser transcript/lock reuse",
    "C03": "genuine direct producers, final host marker and populated v2 causal controls",
    "C04": "two-org actual launch/release race and reader saturation",
    "C05": "genuine negative native outcomes, first cause and unsafe tail fixture",
    "C06": "populated authenticated chronology/cutover/returned notification",
    "C07": "original Pause/move producer redelivery and actual harmless job",
    "C08": "interrupted original native tail/read/partial preparation joins",
    "C09": "both listener orderings, abort/duplicate/wrong attachment and return binding",
    "C10": "scoped auth, inode/decoder/busy/close/cancel/budget and ready legacy limits",
    "C11": "actual retained pending key, uncertain transport and browser no-replay",
    "C12": "remaining phase/cache invalidation/decoder/cancel/negative outcome counters",
    "C13": "fresh original restart after partial durable commit/failure/notification",
    "C14": "real launcher/CLI/setup/Mac and bilingual browser observations",
    "C15": "excluded: separately operator-authorized launcher to usable API AND UI",
}


class Checks:
    def __init__(self, args):
        self.args = args
        self.started = time.monotonic()
        self.rows = []
        self.obligations = {}
        self.details = {"case": args.case,
                        "pid": os.getpid(), "python": sys.executable,
                        "python_version": sys.version, "cwd": str(Path.cwd()),
                        "root": str(args.root), "source": str(SOURCE),
                        "head": git("rev-parse", "HEAD"),
                        "tree": git("rev-parse", "HEAD^{tree}"),
                        "working_status": git("status", "--porcelain=v1"),
                        "allocation": "one actual top-level process; no reset"}
        self.expect("C12_counter_selection", "C12", "counter", (
            "C12_nonzero_independent_SQL_and_original_entries",
            "C12_history_fetch_count_matches_sql", "C12_fetch_is_not_cache_hit"),
            "canonical history and independent ORIGINAL-entry/SQL observers")
        if args.case == "all":
            for case, reason in REMAINING.items():
                self.expect(case + "_remaining_contract", case, "remaining", (), reason)
            for number in range(1, 246):
                name = f"H{number:03d}"
                for phase in ("recovering", "ready"):
                    if name in EARLY_IDS:
                        assertions = tuple(name + "_" + phase + suffix for suffix in (
                            "_status", "_content", "_identity", "_auth", "_wire"))
                    elif phase == "recovering":
                        # Retain each historical H receipt name verbatim.
                        assertions = tuple(name + suffix for suffix in (
                            "_held_status", "_held_code", "_one_observed_request", "_prebody_receive_zero", "_original_parser_spool_native_zero"))
                    else:
                        assertions = ()
                    self.expect(name + "_" + phase, "C02", phase, assertions,
                                "original per-declaration request/content/auth/handler required")
            for name in EARLY_IDS[7:]:
                for phase in ("recovering", "ready"):
                    self.expect(name + "_" + phase, "C09", phase,
                        tuple(name + "_" + phase + suffix for suffix in (
                            "_status", "_content", "_identity", "_auth", "_wire")),
                        "original readiness state/serving conjunction")
            for name in LITERAL_IDS:
                for phase in ("recovering", "ready"):
                    self.expect(name + "_" + phase, "C02", phase,
                        tuple(name + "_" + phase + suffix for suffix in (
                            "_status", "_content", "_identity", "_wire", "_body_boundary")),
                        "accepted literal inventory, real index/assets and genuine original phase required")
            for phase in ("recovering", "ready"):
                self.expect("C02_" + phase + "_effects", "C02", phase,
                    ("C02_" + phase + "_rows_files_owners_unchanged",
                     "C02_" + phase + ("_parser_spool_native_zero" if phase == "recovering" else "_spool_native_zero")),
                    "independent SQL/selected files/owners and original entry observers")
                self.expect("C02_" + phase + "_unobserved_boundaries", "C02", phase, (),
                    "all source-specific reservations/journals/native sinks require remaining D producer fixtures")
            for name in ("HX02", "HX03", "HX04"):
                for phase in ("recovering", "ready"):
                    self.expect(name + "_" + phase, "C02", phase,
                        tuple(name + "_" + phase + suffix for suffix in (
                            "_status", "_content", "_identity", "_wire", "_body_boundary")),
                        "actual static/root/SPA handler and genuine original phase")
            for name in ("HX05", "HX06", "HX07", "HX08"):
                for phase in ("recovering", "ready"):
                    self.expect(name + "_" + phase, "C02", phase,
                                tuple(name + suffix for suffix in ("_held_status", "_held_code", "_one_observed_request", "_prebody_receive_zero", "_original_parser_spool_native_zero")) if phase == "recovering" else (),
                                "original docs/OpenAPI and other method controls remain root-owned")
            for name in ("HX10.04", "HX10.11"):
                self.expect(name + "_recovery_failed", "C05", "recovery_failed", (),
                            "genuine negative-outcome/failure fixture not implemented; ready-to-failed is not proof")
            for number in range(1, 90):
                name = f"D{number:03d}"
                if name not in TASK_DIRECT:
                    self.expect(name, "C03", "direct", (),
                                "valid original producer/causal/native fixture and independent effect observer not implemented")
                    continue
                for phase, suffixes in TASK_PHASE_ASSERTIONS.items():
                    assertions = (tuple(f"{name}_{phase}_{slug}_{suffix}"
                                        for slug in ("fixture-a", "fixture-b") for suffix in suffixes)
                                  if phase == "recovering" else
                                  tuple(f"{name}_{phase}_{suffix}" for suffix in suffixes))
                    self.expect(name + "_" + phase, "C03", phase, assertions,
                        "each original entry needs its OWN valid selection/effects; a general gate or final marker cannot supply it")
                    self.obligations[name + "_" + phase]["original_owner"] = ":".join(TASK_DIRECT[name])
            self.expect("C04_task_pending_dedup", "C04", "ready", (
                "C04_task_eight_contenders_one_winner", "C04_task_original_pending_metadata",
                "C04_task_enqueue_put_nowait_not_deduplicated", "C04_task_one_launch_for_pending_owner"),
                "one genuine pending key, original queue ownership and original native retirement")
            self.expect("C04_task_global_hold", "C04", "recovering", (
                "C04_task_both_orgs_pending_before_release", "C04_task_no_dequeue_before_release"),
                "original P2 publications retained in both fixture orgs during ONE original sweep hold")
            for number in range(1, 5):
                for phase in ("recovering", "recovery_failed", "ready"):
                    self.expect(f"U{number:02d}_" + phase, "C11", phase, (),
                                "actual locale/viewport browser against genuine backend outcome not implemented")
            self.expect("C11_C14_selected_Web_keepers", "C14", "Web", ("Web_selected_component_keeper_command",),
                        "explicit --web and all current named keepers required; original13 failures remain original-head evidence")

    def expect(self, name, case, phase, assertions, prerequisite):
        self.obligations[name] = {"case": case, "phase": phase, "assertions": list(assertions),
            "missing_prerequisite": prerequisite, "owner": "ROOT TASK-13137",
            "source": str(SOURCE / "scripts/verify_startup_recovery.py"),
            "head": self.details["head"], "tree": self.details["tree"],
            "fixture": str(self.args.root), "observer": "named independent assertions only"}

    def disposition(self):
        results = {}
        for row in self.rows:
            results.setdefault(row["assertion"], []).append(row["status"])
        ledger = {}
        for name, obligation in self.obligations.items():
            statuses = [state for assertion in obligation["assertions"]
                        for state in results.get(assertion, ["NOT RUN"])]
            status = ("FAIL" if "FAIL" in statuses else "COMPLETE"
                      if statuses and all(state == "PASS" for state in statuses) else "NOT RUN")
            ledger[name] = {**obligation, "status": status,
                "missing_assertions": [a for a in obligation["assertions"] if a not in results]}
        failed = any(row["status"] == "FAIL" for row in self.rows)
        incomplete = any(row["status"] != "COMPLETE" for row in ledger.values())
        self.details["scope"] = "selected C12 counter observations only" if self.args.case == "C12" else "all mandatory selections"
        self.details["outcome"] = "FAIL" if failed else "INCOMPLETE" if incomplete else "COMPLETE_SELECTED_OBSERVATIONS"
        self.details["full_acceptance"] = False  # source delivery never grants product acceptance
        self.details["obligations"] = ledger
        self.details["cases"] = {case: {
            "status": "FAIL" if (any(r["status"] == "FAIL" for r in ledger.values() if r["case"] == case)
                or any(r["status"] == "FAIL" and r["assertion"].startswith(case + "_") for r in self.rows))
                else "INCOMPLETE" if any(r["status"] != "COMPLETE" for r in ledger.values() if r["case"] == case)
                else "COMPLETE_SELECTED_OBSERVATIONS",
            "observations": [r["assertion"] for r in self.rows if r["assertion"].startswith(case + "_")],
            "missing_obligations": [n for n, r in ledger.items() if r["case"] == case and r["status"] == "NOT RUN"],
        } for case in sorted({r["case"] for r in ledger.values()})}
        return 1 if failed else 2 if incomplete else 0

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
        self.disposition()
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
        if (not args.web_dist.is_absolute() or args.web_dist.resolve() != args.web_dist
                or not args.web_dist.is_dir() or not args.web_dist.is_relative_to(parent)):
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
    fit = {"orgs": 1 if checks.args.case == "C12" else 2, "roots": 1 if checks.args.case == "C12" else 12,
           "tasks": 1 if checks.args.case == "C12" else 13, "results": 1 if checks.args.case == "C12" else 4,
           "v2_history_and_controls": 11,
           "workflow_causal_rows": 0, "threads": 0 if checks.args.case == "C12" else 2,
           "messages": 0 if checks.args.case == "C12" else 2, "tokens": 0, "jobs": 0,
           "provider_children": 0 if checks.args.case == "C12" else 3,
           "simultaneous_harmless_children": 0 if checks.args.case == "C12" else 2,
           "listeners": 0 if checks.args.case == "C12" else 1,
           "browser_contexts": 0}
    if checks.args.case == "all":
        fit["cumulative_task_arithmetic"] = "1 counter root + 9 existing writer roots + 1 native child + 1 native root + 1 ready dedup root = 13 tasks / 12 roots"
        fit["native_children"] = "3 total; original worker + one direct start_workers positive; <=2 simultaneous, each original session timeout<=15s"
        # The nine writer cases create Pause journals/reservations, not v2
        # generations. Count their IDs even though release later retires them.
        fit["pause_control_roots"] = 12
        fit["additional_v2_generations"] = 0
        fit["causal_rows_cap"] = 512
        fit["history_controls_cap"] = 64
        fit["audit_fit"] = "UNRESOLVED: canonical bootstrap/session/callback/outer writers add audit history; no constructor authorized until its cumulative source count is reconciled"
    checks.details["fixture_fit_before_constructors"] = fit
    checks.save()
    if checks.args.case == "all":
        # Do not turn task-count arithmetic into an invented history fit.
        # Source inspection still owes the exact cumulative canonical audit
        # expansion of bootstrap + THREE native task/callback/outer tails.
        # This source-stage limitation is deliberately before ANY constructor.
        checks.missing("C03_canonical_history_fit", "reconcile cumulative canonical bootstrap/session/callback/outer audit writers against original64 cap before constructors; task/root fit alone is insufficient")
        return None
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
                from runtime.models import TaskRecord, TaskStatus
                native_task = TaskRecord(id="TASK-090", status=TaskStatus.PENDING,
                    team="engineering", assigned_agent="case_worker" if index == 0 else "case_manager",
                    parent_task_id="TASK-001" if index == 0 else None,
                    task_type="subtask" if index == 0 else "task",
                    brief="Owned harmless startup child; report only own actual fixture session")
                db.insert_task(native_task)
                checks.details.setdefault("native_task_selections", {})[slug] = {
                    "task_id": native_task.id, "agent": native_task.assigned_agent}
                # Original offline workspace bootstrap, before daemon attachment;
                # no generated permission or materialization behavior is replaced.
                from runtime.config import Settings
                from runtime.orchestrator._paths import OrgPaths
                from runtime.orchestrator.workspace_adapters import ClaudeWorkspaceAdapter
                from runtime.orchestrator.prompt_loader import load_agent
                paths = OrgPaths(root=org_root)
                adapter = ClaudeWorkspaceAdapter(Settings(project_root=SOURCE), paths, slug=slug)
                definition = load_agent(paths, native_task.assigned_agent)
                adapter.ensure_workspace_ready(paths.workspaces_dir / native_task.assigned_agent,
                    native_task.assigned_agent, definition.system_prompt)
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


def load_task_inputs(checks):
    path = checks.args.direct_inventory
    if path is None:
        return {}
    data = path.read_bytes()
    digest = "eaf4206c9c9455f8223fe81f3191859f8d09b4e9d9335f281c1d1079d42e3966"
    if len(data) > 256 * 1024 or hashlib.sha256(data).hexdigest() != digest:
        raise ValueError("accepted direct inventory identity mismatch")
    selected = {row["id"]: row for row in json.loads(data)["entries"] if row["id"] in TASK_DIRECT}
    if set(selected) != set(TASK_DIRECT):
        raise ValueError("selected nineteen direct IDs missing or duplicated")
    for name, row in selected.items():
        if (row["file"], row["symbol"]) != TASK_DIRECT[name]:
            raise ValueError("selected original task producer changed: " + name)
    checks.details["task_direct_inventory"] = {"path": str(path), "sha256": digest, "entries": selected}
    return selected


def prepare_task_executable(checks):
    """Use the existing isolated binary registry, never a production fake hook."""
    from runtime.orchestrator.executor_binary_registry import set_binary
    directory = checks.args.root / "native"
    directory.mkdir(mode=0o700)
    executable = directory / "claude"
    executable.write_text("#!/bin/sh\nexec " + shlex.quote(sys.executable) + " -I "
        + shlex.quote(str(SOURCE / "scripts/startup_task_child.py")) + " --binding "
        + shlex.quote(str(directory / "binding.json")) + ' "$@"\n')
    executable.chmod(0o700)
    set_binary("claude", str(executable))
    checks.details["task_executable"] = {"path": str(executable),
        "sha256": hashlib.sha256(executable.read_bytes()).hexdigest(),
        "helper_sha256": hashlib.sha256((SOURCE / "scripts/startup_task_child.py").read_bytes()).hexdigest()}


class TaskEntries:
    """Observe ORIGINAL frames/returns; never replace their bodies or grants."""

    def __init__(self, state):
        import importlib
        self.state, self.rows, self.dispatcher = state, [], None
        self.codes = {}
        self.running = {}
        for name, (path, symbol) in TASK_DIRECT.items():
            value = importlib.import_module(path[:-3].replace("/", "."))
            for part in symbol.split("."):
                value = getattr(value, part)
            self.codes[value.__code__] = name
        self.phase = "recovering"
        self.selected = None
        self.active = False
        self.effect_entries = {"native_spawn": 0, "invocation_prepare": 0}
        from runtime.orchestrator.task_pause import TaskInvocation
        self.effect_codes = {subprocess.Popen.__init__.__code__: "native_spawn",
                             TaskInvocation.prepare.__code__: "invocation_prepare"}

    def profile(self, frame, event, value):
        if not self.active or event not in ("call", "return"):
            return
        if event == "call" and frame.f_code in self.effect_codes:
            self.effect_entries[self.effect_codes[frame.f_code]] += 1
        name = self.codes.get(frame.f_code)
        if name is None:
            return
        if len(self.rows) >= 512:
            raise RuntimeError("task original entry observation cap")
        local = frame.f_locals
        owner = local.get("self", local.get("orch", local.get("state")))
        request = local.get("request")
        item = local.get("item")
        slug = (getattr(request, "org", None) or local.get("slug")
                or (item[0] if item is not None else None)
                or getattr(owner, "_slug", None)
                or getattr(getattr(owner, "_startup_org", None), "slug", None))
        task = (getattr(request, "logical_id", None) or local.get("task_id")
                or (item[1] if item is not None else None))
        row = {"id": name, "phase": self.phase, "event": event, "org": slug,
               "task": task, "owner": id(owner), "thread": threading.get_ident(),
               "selected": self.selected, "monotonic": time.monotonic()}
        if name in ("D001", "D002", "D003", "D004") and event == "return":
            queue = self.state.queue
            row["queue_items"] = copy.deepcopy(list(queue._queue._queue))
            row["pending"] = sorted((s, t, n) for (s, t), n in queue._pending_counts.items())
            row["reservations"] = sorted(queue._admission_reservations)
            if name == "D004":
                row["dedup_result"] = value
        if name == "D006" and event == "return" and value is not None:
            row["work"] = {"identity": id(value), "item": value.item,
                "attachment": id(value.attachment), "registered": value in self.state.queue._native_work,
                "task_created": value.task is not None}
        if name == "D005" and event == "return":
            work = local.get("work")
            row["work_retired"] = (work is not None and not work.failures
                and work not in self.state.queue._native_work and work.run is not None
                and work.run.done() and not work.run.cancelled()
                and work.retirement is not None and work.retirement.done()
                and not work.retirement.cancelled())
        if name in ("D011", "D012", "D013", "D014"):
            from runtime.orchestrator.task_pause import current_invocation
            invocation = current_invocation()
            row["invocation"] = None if invocation is None else {
                "identity": invocation.identity, "task": invocation.task.id,
                "agent": invocation.agent, "session": invocation.session_id,
                "prepared": invocation.move_reservation is not None,
                "live": invocation.store._live.get(invocation.identity) is invocation}
        if name in ("D008", "D010", "D017") and event == "return" and slug in self.state.orgs:
            org = self.state.orgs[slug]
            row["outer_live"] = sorted(org.db._task_pause_store._live)
            row["outer_reservations"] = sorted(org.orchestrator._team_moves._reservations)
        if name == "D007" and event == "call":
            # Retain the ACTUAL lifecycle-created Dispatcher for direct probes.
            self.dispatcher = local["dispatcher"]
            row["lifecycle_recovery"] = local.get("recovery") is self.state._startup_recovery
            row["registered_before"] = len(self.state.queue._worker_tasks)
        if name == "D007" and event == "return":
            row["registered_after"] = len(self.state.queue._worker_tasks)
            row["handles_live"] = all(not task.done() for task in self.state.queue._worker_tasks)
        if name == "D087" and event == "return" and value is not None:
            from dataclasses import asdict
            ctx = local["ctx"]  # SAME lease.ctx supplied by original run().
            running = ctx.running()
            row.update(context=id(ctx), outcome={
                "terminal": value.terminal_reason.value,
                "cleanup_error": value.cleanup_error, "error": value.error,
                "receipt": None if value.receipt is None else asdict(value.receipt)},
                running=None if running is None else {
                    "backend": running.backend, "token": running.token,
                    "request_id": running.request_id, "pid": running.root_pid,
                    "start_identity": running.start_identity,
                    "returncode": None if running.process is None else running.process.poll()})
            self.running[(slug, task)] = row
        if name == "D009" and event == "return":
            row["admitted"] = value
        self.rows.append(row)

    def start(self):
        if sys.getprofile() is not None or threading.getprofile() is not None:
            raise RuntimeError("existing original observer cannot be replaced")
        self.active = True
        threading.setprofile_all_threads(self.profile)

    def close(self):
        self.active = False
        threading.setprofile_all_threads(None)

    def entries(self, name, phase, *, selected=None, task=None):
        return [row for row in self.rows if row["id"] == name and row["phase"] == phase
                and row["event"] == "call" and (selected is None or row["selected"] == selected)
                and (task is None or (row["org"], row["task"]) == task)]


def task_queue_observation(state):
    queue = state.queue
    with queue._admission_lock:
        result = {"items": [(slug, task, metadata) for slug, task, metadata in queue._queue._queue],
            "pending": sorted((slug, task, n) for (slug, task), n in queue._pending_counts.items()),
            "reservations": sorted(queue._admission_reservations),
            "unfinished": queue._queue._unfinished_tasks,
            "workers": [(id(task), task.done()) for task in queue._worker_tasks],
            "native": sorted(id(work) for work in queue._native_work)}
    result["trackers"] = {}
    for slug, org in state.orgs.items():
        with org.sessions._lock:
            result["trackers"][slug] = {
                "active": sorted((task, agent, sid) for (task, agent), sid in org.sessions._active.items()),
                "pids": sorted((task, agent, sorted(pids.items())) for (task, agent), pids in org.sessions._pids.items()),
                "controls": sorted((task, agent, sorted((sid, id(control)) for sid, control in controls.items()))
                                   for (task, agent), controls in org.sessions._cancel_controls.items()),
                "recovery": sorted(org.sessions._recovery_sessions)}
    host = state.host_supervisor
    result["host"] = {"active": host.active_count(), "queued": host._admission.queue_depth(),
                      "admitted": host._admission.admitted_total(), "released": host._admission.released_total()}
    return copy.deepcopy(result)


async def held_task_cases(checks, runtime, state, entries):
    from runtime.daemon.runner import enqueue_task
    from runtime.daemon.startup_recovery import StartupUnavailable
    from runtime.orchestrator.run_step import run_step_impl
    if not checks.details.get("task_direct_inventory"):
        return  # mandatory per-ID ledger remains NOT RUN.
    dispatcher = entries.dispatcher
    if dispatcher is None or dispatcher._state is not state:
        raise RuntimeError("actual dormant task dispatcher not observed")
    queue = state.queue
    before_queue = task_queue_observation(state)
    checks.require("C04_task_both_orgs_pending_before_release", all(
        any(item[:2] == [slug, "TASK-090"] or item[:2] == (slug, "TASK-090")
            for item in before_queue["items"]) for slug in ("fixture-a", "fixture-b")))
    for slug in ("fixture-a", "fixture-b"):
        org = state.orgs[slug]
        item = next((item for item in queue._queue._queue if item[:2] == (slug, "TASK-090")), None)
        if item is None:
            raise RuntimeError("original P2 pending owner absent: " + slug)
        calls = {
            "D001": lambda: enqueue_task(state, slug, item[1], metadata=item[2]),
            "D002": lambda: queue.enqueue(slug, item[1], metadata=item[2]),
            "D003": lambda: queue.put_nowait(slug, item[1], metadata=item[2]),
            "D004": lambda: queue.enqueue_if_absent(slug, item[1], metadata=item[2]),
            "D005": lambda: queue._consume(dispatcher, item),
            "D006": lambda: queue._submit(dispatcher, item),
            "D007": lambda: queue.start_workers(dispatcher, n=1),
            "D008": lambda: dispatcher.run_step(slug, item[1], metadata=item[2], attachment=org),
            "D009": lambda: dispatcher.task_admission_allowed(slug, item[1], metadata=item[2], attachment=org),
            "D010": lambda: org.orchestrator.run_step(item[1], metadata=item[2]),
            "D017": lambda: run_step_impl(org.orchestrator, item[1], metadata=item[2]),
        }
        for name, call in calls.items():
            entries.selected = name + "/" + slug
            before = fixture_effects(runtime, state, checks.args.root)
            queue_before = task_queue_observation(state)
            effect_entries = dict(entries.effect_entries)
            refusal = None
            try:
                result = call()
                if asyncio.iscoroutine(result):
                    await result
            except StartupUnavailable as exc:
                refusal = exc.code
                trace = exc.__traceback__
                frames = []
                while trace is not None and len(frames) < 32:
                    frames.append({"file": trace.tb_frame.f_code.co_filename,
                                   "symbol": trace.tb_frame.f_code.co_qualname, "line": trace.tb_lineno})
                    trace = trace.tb_next
                checks.details.setdefault("task_held_refusals", []).append({
                    "id": name, "org": slug, "task": item[1], "metadata": copy.deepcopy(item[2]),
                    "code": exc.code, "original_frames": frames})
            finally:
                entries.selected = None
            # Different denial, invalid input, no-op or success cannot earn credit.
            prefix = name + "_recovering_" + slug
            checks.require(prefix + "_original_entry",
                len(entries.entries(name, "recovering", selected=name + "/" + slug)), 1)
            checks.require(prefix + "_startup_refusal", refusal, "daemon_recovering")
            checks.require(prefix + "_queue_unchanged", task_queue_observation(state), queue_before)
            checks.require(prefix + "_effects_unchanged",
                           (fixture_effects(runtime, state, checks.args.root), dict(entries.effect_entries)),
                           (before, effect_entries))
    checks.require("C04_task_no_dequeue_before_release", task_queue_observation(state), before_queue)
    for name in ("D011", "D012", "D013", "D014", "D086", "D087", "D088", "D089"):
        checks.missing(name + "_recovering_valid_owner", (
            "original bound TaskInvocation.prepare refuses before invocation/SID/lease creation in this epoch; "
            "no valid pre-held owner supplied, so no private context or tracker generation is invented"))


async def ready_task_cases(checks, runtime, state, entries):
    """Original queue, native child, callback, outer retirement before stop cases."""
    from runtime.daemon.runner import enqueue_task
    selected = checks.details["native_task_selections"]
    deadline = time.monotonic() + 15

    async def retired():
        while state.queue._queue._unfinished_tasks or state.queue._native_work:
            if time.monotonic() >= deadline:
                raise TimeoutError("original native queue retirement incomplete; retain handles/owners")
            await asyncio.sleep(.01)

    await retired()
    # Ordinary direct lifecycle call, distinct from the privileged original
    # held registration. The added handle stays under the original queue stop.
    registered = len(state.queue._worker_tasks)
    state.queue.start_workers(entries.dispatcher, n=1)
    checks.require("D007_ready_original_entry", bool(entries.entries("D007", "ready")))
    checks.require("D007_ready_owned_effect", len(state.queue._worker_tasks), registered + 1)
    # This new ready task uses the canonical ID/root producer, never a reused
    # terminal task. Its ID is included in the PRE-constructor cumulative fit.
    org = state.orgs["fixture-b"]
    task = org.orchestrator.create_task("Owned harmless startup child; report own actual fixture session", team="engineering")
    binding_path = checks.args.root / "native/binding.json"
    binding = json.loads(binding_path.read_bytes())
    binding["tasks"]["fixture-b/" + task] = "case_manager"
    binding_path.write_text(json.dumps(binding))
    metadata = {"trigger": "startup-fixture-owned-dedup"}
    # Eight scheduled eligible producers share one pending key. They yield to
    # the event loop together; none yields between its original reservation/put.
    async def contender():
        won = state.queue.enqueue_if_absent("fixture-b", task, metadata=metadata,
            publisher=lambda: enqueue_task(state, "fixture-b", task, metadata=metadata))
        if won:
            pending = task_queue_observation(state)
            checks.require("C04_task_original_pending_metadata", [item for item in pending["items"]
                if item[:2] == ("fixture-b", task)], [("fixture-b", task, metadata)])
            # Both original APIs deliberately keep duplicate pending tuples.
            # Before this coroutine yields, consumers cannot take that key.
            state.queue.put_nowait("fixture-b", task, metadata=metadata)
            state.queue.enqueue("fixture-b", task, metadata=metadata)
            pending = task_queue_observation(state)
            checks.require("C04_task_enqueue_put_nowait_not_deduplicated", [item for item in pending["items"]
                if item[:2] == ("fixture-b", task)], [("fixture-b", task, metadata)] * 3)
        return won
    results = await asyncio.gather(*(contender() for _ in range(8)))
    checks.require("C04_task_eight_contenders_one_winner", sorted(results), [False] * 7 + [True])
    selected = {**selected, "fixture-b-dedup": {"task_id": task, "agent": "case_manager", "org": "fixture-b"}}
    await retired()
    markers = [json.loads(path.read_bytes()) for path in sorted((checks.args.root / "native").glob("fixture-*-TASK-*.json"))]
    checks.details["task_native_markers"] = markers
    checks.details["task_original_entries"] = entries.rows
    checks.details["task_final_queue"] = task_queue_observation(state)
    for key, selection in selected.items():
        slug, task_id = selection.get("org", key), selection["task_id"]
        org = state.orgs[slug]
        actual = org.db.get_task(task_id)
        own_markers = [m for m in markers if (m["org"], m["task"]) == (slug, task_id)]
        host = entries.running.get((slug, task_id))
        receipt = None if host is None else host["outcome"]["receipt"]
        result = org.db.get_latest_task_result(task_id, selection["agent"], own_markers[0]["session"]) if len(own_markers) == 1 else None
        journal = invocation_observation(org, task_id)
        checks.details.setdefault("task_original_retirement", {})[slug + "/" + task_id] = {
            "task": actual.model_dump(mode="json"), "host": host, "journal": journal,
            "result_id": None if result is None else result["id"]}
        positive = (actual.status.value == "completed" and result is not None
            and type(result["id"]) is int and result["id"] > 0 and receipt is not None
            and receipt["quiescent"] is True and receipt["terminal_reason"] == "success"
            and host["outcome"]["error"] is None and host["outcome"]["cleanup_error"] is None
            and host["running"] is not None and host["running"]["pid"] > 0
            and host["running"]["returncode"] == 0 and len(own_markers) == 1
            and own_markers[0]["pid"] == host["running"]["pid"]
            and not journal["reservations"] and not journal["live"]
            and not org.sessions._active and not org.sessions._pids and not org.sessions._cancel_controls
            and not state.queue._native_work and state.host_supervisor.active_count() == 0)
        checks.require("C03_" + slug + "_" + task_id + "_native_owner_positive_retirement", positive)
        # Credit only each separately observed owner, not all nineteen from a
        # final marker. D088/D089 are NOT supplied by _set_active_locked.
        for name in TASK_DIRECT:
            original = entries.entries(name, "ready", task=(slug, task_id))
            if not original:
                continue
            checks.require(name + "_ready_original_entry", bool(original))
            # The frame retains the actual selected task/owner; admission has
            # its own result oracle and host execution its own genuine ctx.
            if name == "D009":
                effect = any(row.get("admitted") is True and (row["org"], row["task"]) == (slug, task_id)
                    for row in entries.rows if row["id"] == name and row["event"] == "return")
            elif name in ("D086", "D087"):
                effect = host is not None and receipt is not None
            elif name in ("D001", "D002", "D003", "D004"):
                effect = any(any(item[:2] == (slug, task_id) for item in row.get("queue_items", ()))
                    and (name != "D004" or row.get("dedup_result") is True)
                    for row in entries.rows if row["id"] == name and row["event"] == "return"
                    and (row["org"], row["task"]) == (slug, task_id))
            elif name == "D005":
                effect = any(row.get("work_retired") is True for row in entries.rows
                    if row["id"] == name and (row["org"], row["task"]) == (slug, task_id))
            elif name == "D006":
                effect = any(row.get("work", {}).get("registered") is True
                    and row["work"]["attachment"] == id(org) and row["work"]["task_created"]
                    for row in entries.rows if row["id"] == name and (row["org"], row["task"]) == (slug, task_id))
            elif name in ("D008", "D010", "D017"):
                effect = journal["journal"] is not None and any(
                    row.get("outer_live") == [] and row.get("outer_reservations") == []
                    for row in entries.rows if row["id"] == name and row["event"] == "return"
                    and (row["org"], row["task"]) == (slug, task_id))
            elif name in ("D011", "D012", "D013", "D014"):
                effect = any(row.get("invocation") is not None
                    and row["invocation"]["prepared"] and row["invocation"]["live"]
                    and row["invocation"]["task"] == task_id
                    and len(own_markers) == 1 and row["invocation"]["session"] == own_markers[0]["session"]
                    for row in original)
            else:
                effect = False
                checks.missing(name + "_ready_boundary_specific_effect", "original entry/terminal observed; boundary-specific preparation/publication/dequeue oracle still required")
            checks.require(name + "_ready_native_marker", len(own_markers), 1)
            checks.require(name + "_ready_positive_retirement", positive)
            if effect:
                checks.require(name + "_ready_owned_effect", effect)
        if key == "fixture-b-dedup":
            checks.require("C04_task_one_launch_for_pending_owner", len(own_markers), 1)
    checks.require("D007_ready_native_marker", len(markers), 3)
    checks.require("D007_ready_positive_retirement", not state.queue._native_work
        and state.queue._queue._unfinished_tasks == 0 and state.host_supervisor.active_count() == 0)
    for name in ("D088", "D089"):
        checks.missing(name + "_ready_original_owner", "bound TaskInvocation uses _set_active_locked; register_recovery_session has no production caller; do not fabricate a recovery owner")


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


def load_http_inputs(checks):
    selected = {}
    for label, path, digest in (
        ("declarations", checks.args.http_inventory, "b4f1aa2998c0746eaa8c4f5b089d345633e63bd601e839db6c0c9f65089d72a9"),
        ("literal22", checks.args.routing_inventory, "b73d9d4e2237680ff9d2743bd5b66165381ee37146ae640957f15fb5386802b0"),
    ):
        if path is None:
            selected[label] = []
            continue
        data = path.read_bytes()
        if len(data) > 256 * 1024 or hashlib.sha256(data).hexdigest() != digest:
            raise ValueError("accepted HTTP inventory identity mismatch: " + label)
        entries = json.loads(data)["entries"]
        selected[label] = entries
        checks.details.setdefault("inputs", {})[label] = {"path": str(path), "sha256": digest}
    if selected["literal22"] and tuple(e["id"] for e in selected["literal22"]) != LITERAL_IDS:
        raise ValueError("literal22 membership/order mismatch")
    for entry in selected["declarations"]:
        if not re.fullmatch(r"H\d{3}", entry["id"]):
            continue
        for phase in ("recovering", "ready"):
            checks.obligations[entry["id"] + "_" + phase]["request"] = {
                "method": entry["method"], "path": entry["path"], "owner": entry["owner"]}
        if entry["method"] not in ("GET", "HEAD"):
            checks.expect(entry["id"] + "_meaningful_input", "C02", "input", (),
                "generic {} refusal sweep is not this source-specific authorized body/session/owner or multipart fixture")
    checks.details["http_selection"] = {key: len(value) for key, value in selected.items()}
    return selected


def prepare_static(checks):
    dist = checks.args.web_dist
    if dist is None:
        checks.missing("C02_real_build", "explicit task-owned ordinary real Web dist/index/assets required; no fake index")
        return None
    index, assets = dist / "index.html", dist / "assets"
    if (index.is_symlink() or assets.is_symlink() or not index.is_file()
            or not assets.is_dir() or not any(assets.glob("*.js"))):
        raise ValueError("ordinary real Web build/index/assets unavailable")
    content = index.read_bytes()
    if len(content) > 4 * 1024 * 1024:
        raise ValueError("index observation exceeds original encoded-response cap")
    probe = assets / "startup-gate-probe.txt"
    with probe.open("xb") as handle:
        handle.write(ASSET_BYTES)
    checks.details["static"] = {"dist": str(dist), "index_sha256": hashlib.sha256(content).hexdigest(),
                                 "probe_sha256": hashlib.sha256(ASSET_BYTES).hexdigest()}
    return content


async def raw_request(port, method, target, token, case, body=b""):
    """Literal HTTP/1.1 bytes; no URL parser, percent decode or redirect follow."""
    line = method.encode("ascii") + b" " + target.encode("ascii") + b" HTTP/1.1\r\n"
    if any(c in target for c in ("\r", "\n", " ")) or not target.startswith("/"):
        raise ValueError("invalid literal origin request target")
    headers = [b"Host: 127.0.0.1:" + str(port).encode(), b"Connection: close",
               b"X-Startup-Case: " + case.encode("ascii"), b"Content-Type: application/json",
               b"Content-Length: " + str(len(body)).encode()]
    if token is not None:
        headers.append(b"Authorization: Bearer " + token.encode("ascii"))

    async def exchange():
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        try:
            writer.write(line + b"\r\n".join(headers) + b"\r\n\r\n" + body)
            await writer.drain()
            head = await reader.readuntil(b"\r\n\r\n")
            parts = head.split(b"\r\n")
            status = int(parts[0].split(b" ")[1])
            response_headers = {}
            for field in parts[1:-2]:
                key, value = field.split(b":", 1)
                response_headers[key.lower().decode("ascii")] = value.strip().decode("latin1")
            content = bytearray()
            if method != "HEAD" and status not in (204, 304):
                if response_headers.get("transfer-encoding") == "chunked":
                    for _ in range(4096):
                        size = int((await reader.readline()).split(b";", 1)[0], 16)
                        if size == 0:
                            if await reader.readline() != b"\r\n":
                                raise ValueError("unexpected chunk trailer")
                            break
                        if len(content) + size > 4 * 1024 * 1024:
                            raise ValueError("HTTP response byte budget")
                        content.extend(await reader.readexactly(size))
                        if await reader.readexactly(2) != b"\r\n":
                            raise ValueError("invalid chunk framing")
                    else:
                        raise ValueError("HTTP chunk count budget")
                elif "content-length" in response_headers:
                    size = int(response_headers["content-length"])
                    if not 0 <= size <= 4 * 1024 * 1024:
                        raise ValueError("HTTP response byte budget")
                    content.extend(await reader.readexactly(size))
                else:
                    raise ValueError("unbounded HTTP response framing")
            return {"status": status, "headers": response_headers, "body": bytes(content),
                    "request_line_hex": line.hex(), "request_body_sha256": hashlib.sha256(body).hexdigest()}
        finally:
            writer.close()
            await writer.wait_closed()
    return await asyncio.wait_for(exchange(), 2)


def fixture_effects(runtime, state, scratch):
    """Independent RO SQL + bounded concrete file/native ownership observations."""
    files, owners = {}, {}
    roots = [scratch / "temp", scratch / "native"]
    for slug, org in state.orgs.items():
        roots.extend((org.root / "workspaces", org.root / "jobs"))
        owners[slug] = {"reservations": sorted(org.orchestrator._team_moves._reservations),
                       "live": sorted(org.db._task_pause_store._live),
                       "native_users": sorted(id(x) for x in org._native_task_users)}
    deadline, size = time.monotonic() + 2, 0
    for root in roots:
        for path in sorted(root.rglob("*")):
            if len(files) >= 4096 or time.monotonic() >= deadline:
                raise ValueError("independent file observation unavailable/budget")
            if path.is_symlink():
                # The canonical workspace pair/skill links are real fixture
                # metadata. Observe literal link bytes without following them.
                files[str(path)] = {"symlink": os.readlink(path)}
            elif path.is_file():
                size += path.stat().st_size
                if size > 4 * 1024 * 1024:
                    raise ValueError("independent file byte budget")
                files[str(path)] = hashlib.sha256(path.read_bytes()).hexdigest()
            elif path.is_dir():
                files[str(path)] = "directory"
            else:
                raise ValueError("unexpected effect observation file type")
    return {"rows": database_observation(runtime), "files": files, "owners": owners}


class EntryObservation:
    """Count ORIGINAL parser/spool/launch entries, without replacing any body."""
    current = ContextVar("startup_http_observer", default=None)

    def __init__(self, app):
        from starlette.requests import Request
        from starlette.formparsers import MultiPartParser
        from runtime.orchestrator.task_pause import TaskInvocation
        self.codes = {fn.__code__: label for label, fn in (
            ("parser", Request.json), ("parser", Request._get_form),
            ("parser", MultiPartParser.parse), ("spool", tempfile.SpooledTemporaryFile.__init__),
            ("native", subprocess.Popen.__init__), ("native", asyncio.create_subprocess_exec),
            ("native", asyncio.create_subprocess_shell), ("preparation", TaskInvocation.prepare))}
        functions = [getattr(route, "endpoint", None) for route in app.routes] + list(app.exception_handlers.values())
        self.handlers = {fn.__code__: fn.__module__ + "." + fn.__qualname__ for fn in functions if hasattr(fn, "__code__")}
        self.totals = dict.fromkeys(("parser", "spool", "native", "preparation"), 0)
        self.active = False

    def profile(self, frame, event, _arg):
        if not self.active or event != "call":
            return
        row = self.current.get()
        handler = self.handlers.get(frame.f_code)
        if handler is not None and row is not None:
            entries = row.setdefault("original_handler_entries", {})
            entries[handler] = entries.get(handler, 0) + 1
        label = self.codes.get(frame.f_code)
        if label is not None:
            self.totals[label] += 1
            row = self.current.get()
            if row is not None:
                row[label + "_entries"] += 1

    def start(self):
        if sys.getprofile() is not None or threading.getprofile() is not None:
            raise RuntimeError("existing profile observer cannot be replaced")
        threading.setprofile_all_threads(self.profile)
        self.active = True

    def close(self):
        self.active = False
        threading.setprofile_all_threads(None)


def response_json(content):
    try:
        value = json.loads(content)
        return value if isinstance(value, dict) else {}
    except (ValueError, UnicodeDecodeError):
        return {}


def content_contract(name, response, runtime, token, phase):
    if "application/json" not in response["headers"].get("content-type", ""):
        return False
    body = response_json(response["body"])
    if name in ("H001", "HX01"):
        startup = body.get("startup", {})
        return (body.get("status") == ("ok" if name == "H001" else "unavailable" if phase == "recovering" else "ready")
                and startup.get("state") == ("recovering" if phase == "recovering" else "ready")
                and startup.get("serving") is True
                and startup.get("recovery_ready") is (phase == "ready")
                and startup.get("stopping") is False
                and startup.get("ready_commits") == (0 if phase == "recovering" else 1)
                and (name != "H001" or body.get("active_runtime") == str(runtime.root)))
    if name == "H003":
        return body == {"token": token}  # Boolean only: never persist credential bytes.
    if name == "H008":
        return body == {"runtime": str(runtime.root)}
    if name == "H011":
        return body == {"orgs": [{"slug": slug, "root": str(runtime.orgs_dir / slug)}
                                 for slug in ("fixture-a", "fixture-b")], "broken": []}
    if name == "H153":
        rows = body.get("threads", [])
        return (len(rows) == 1 and rows[0].get("thread_id") == "THR-001"
                and rows[0].get("subject") == "Owned recovery transcript"
                and rows[0].get("status") == "open" and rows[0].get("participants") == []
                and rows[0].get("last_speaker") == "founder"
                and body.get("totals") == {"open": 1, "archived": 0, "all": 1, "dream_origin": 0}
                and body.get("has_more") is False and body.get("next_cursor") is None)
    messages = body.get("messages", [])
    known = (len(messages) == 1 and messages[0].get("seq") == 1
             and messages[0].get("speaker") == "founder" and messages[0].get("kind") == "message"
             and messages[0].get("body_markdown") == MESSAGE and messages[0].get("attachments") == []
             and messages[0].get("responder_status") == [] and body.get("reply_delivery") == [])
    if name == "H156":
        return (known and body.get("thread_id") == "THR-001" and body.get("participants") == []
                and body.get("subject") == "Owned recovery transcript" and body.get("status") == "open")
    if name == "H157":
        return known and body.get("has_more") is False and body.get("next_since_seq") == 1
    raise ValueError("unselected early content oracle: " + name)


def wire_contract(checks, wire, name, phase, method, target, response, expected_route):
    key = name + "_" + phase
    matches = [row for row in wire if row["case"] == key]
    path, _, query = target.partition("?")
    valid = len(matches) == 1
    row = matches[0] if valid else {}
    valid = valid and (row["method"], row["raw_path"], row["path"], row["query"]) == (
        method, path, unquote(path, encoding="utf-8", errors="replace"), query)
    valid = valid and row.get("asgi_status") == response["status"]
    checks.require(key + "_wire", valid)
    identity = valid and ((expected_route is None and not row["router_candidates"])
        or any(candidate["path"] == expected_route for candidate in row.get("router_candidates", [])))
    endpoint_names = {
        "/api/v1/health": "runtime.daemon.routes.health.health",
        "/api/v1/health/ready": "runtime.daemon.routes.health.health_ready",
        "/api/v1/auth/bootstrap": "runtime.daemon.routes.auth.bootstrap",
        "/api/v1/runtime": "runtime.daemon.routes.runtime.get_runtime",
        "/api/v1/orgs": "runtime.daemon.routes.orgs.list_orgs",
        "/api/v1/orgs/{slug}/threads": "runtime.daemon.routes.threads.list_threads_endpoint",
        "/api/v1/orgs/{slug}/threads/{thread_id}": "runtime.daemon.routes.threads.get_thread_endpoint",
        "/api/v1/orgs/{slug}/threads/{thread_id}/messages": "runtime.daemon.routes.threads.list_thread_messages_endpoint",
        "/api/v1/orgs/{slug}/threads/{thread_id}/send": "runtime.daemon.routes.threads.send_thread_endpoint",
        "/": "runtime.daemon.routes.web_static.register.<locals>._spa_root",
    }
    endpoint = endpoint_names.get(expected_route)
    if endpoint is not None:
        identity = identity and any(c["endpoint"] == endpoint for c in row.get("router_candidates", []))
        if method == "GET" and name in EARLY_IDS and response["status"] in (200, 503):
            identity = identity and row.get("original_handler_entries", {}).get(endpoint, 0) > 0
    if name in ("HX04", "HX10.12") and response["status"] == 200:
        identity = identity and row.get("original_handler_entries", {}).get(
            "runtime.daemon.routes.web_static.register.<locals>._spa_or_404", 0) > 0
    checks.require(key + "_identity", identity)
    checks.details.setdefault("http_receipts", []).append({"id": name, "phase": phase,
        "head": checks.details["head"], "tree": checks.details["tree"],
        "fixture": str(checks.args.root), "method": method, "request_target": target,
        "request_line_hex": response["request_line_hex"], "request_body_sha256": response["request_body_sha256"],
        "transport_status": response["status"], "transport_headers": response["headers"],
        "transport_body_bytes": len(response["body"]),
        "transport_body_sha256": None if name == "H003" else hashlib.sha256(response["body"]).hexdigest(),
        "original_ASGI": row, "observer": "literal loopback bytes + original ASGI/router",
        "owner": "ROOT TASK-13137"})
    return row


async def early_cases(checks, runtime, port, token, wire, phase):
    targets = (
        ("H001", "/api/v1/health", "/api/v1/health"),
        ("H003", "/api/v1/auth/bootstrap", "/api/v1/auth/bootstrap"),
        ("H008", "/api/v1/runtime", "/api/v1/runtime"),
        ("H011", "/api/v1/orgs", "/api/v1/orgs"),
        ("H153", "/api/v1/orgs/fixture-a/threads?page_size=2", "/api/v1/orgs/{slug}/threads"),
        ("H156", "/api/v1/orgs/fixture-a/threads/THR-001", "/api/v1/orgs/{slug}/threads/{thread_id}"),
        ("H157", "/api/v1/orgs/fixture-a/threads/THR-001/messages?limit=10", "/api/v1/orgs/{slug}/threads/{thread_id}/messages"),
        ("HX01", "/api/v1/health/ready", "/api/v1/health/ready"),
    )
    async def observe(name, target, route):
        key = name + "_" + phase
        response = await raw_request(port, "GET", target, token, key)
        checks.require(key + "_status", response["status"], 503 if name == "HX01" and phase == "recovering" else 200)
        checks.require(key + "_content", content_contract(name, response, runtime, token, phase))
        wire_contract(checks, wire, name, phase, "GET", target, response, route)
        public = name in ("H001", "H003", "HX01")
        statuses = []
        for suffix, bearer in (("missing", None), ("invalid", "invalid-fixture-token")):
            denied = await raw_request(port, "GET", target, bearer, key + "_" + suffix)
            statuses.append(denied["status"])
            if public:
                checks.require(key + "_" + suffix + "_original_content",
                               content_contract(name, denied, runtime, token, phase))
            else:
                checks.require(key + "_" + suffix + "_original_refusal",
                    "application/json" in denied["headers"].get("content-type", "")
                    and response_json(denied["body"]) == {"detail": "missing bearer token" if bearer is None else "bad token"})
        expected = 503 if name == "HX01" and phase == "recovering" else 200 if public else 401
        checks.require(key + "_auth", statuses, [expected, expected])
    # Each task has one in-flight request, at most eight across auth substeps.
    await asyncio.gather(*(observe(*row) for row in targets))


async def literal_cases(checks, runtime, port, token, wire, phase, entries, index):
    async def observe(entry):
        name, method, target = entry["id"], entry["method"], entry["request_target"]
        key = name + "_" + phase
        if index is None and (target.startswith("/assets/") or name in ("HX09.06", "HX10.12", "HX02", "HX03", "HX04")):
            checks.missing(key + "_content", "real isolated ordinary Web build/index/assets missing")
            return
        expected = entry["during_recovery" if phase == "recovering" else "after_ready"]
        body = b"" if entry["request_body"] is None else json.dumps(entry["request_body"], separators=(",", ":")).encode()
        response = await raw_request(port, method, target, token, key, body)
        checks.require(key + "_status", response["status"], expected["status"])
        route = None
        if name in ("HX09.01", "HX09.02", "HX09.03", "HX10.03", "HX10.05", "HX10.07"):
            route = "/api/v1/orgs/{slug}/threads"
        elif name in ("HX10.04", "HX10.06"):
            route = "/api/v1/orgs/{slug}/threads/{thread_id}/send"
        elif target.startswith("/assets/"):
            route = "/assets"
        elif target == "/":
            route = "/"
        row = wire_contract(checks, wire, name, phase, method, target, response, route)
        media = response["headers"].get("content-type", "")
        payload = response["body"]
        if method == "HEAD":
            # Uvicorn suppresses the body. Only the ORIGINAL send observer can
            # observe an app-emitted JSON refusal; never parse client b"".
            emitted = row.get("head_asgi_body", "")
            content = (payload == b"" and "content-length" in response["headers"]
                       and response["headers"].get("content-length") == row.get("asgi_headers", {}).get("content-length"))
            if expected["status"] == 503:
                content = content and "application/json" in media and response_json(emitted).get("detail", {}).get("code") == "daemon_recovering"
            elif target.startswith("/assets/"):
                content = content and "text/plain" in media and int(response["headers"].get("content-length", -1)) == len(ASSET_BYTES)
            else:
                content = content and "application/json" in media and response_json(emitted).get("detail") == ("Not Found" if expected["status"] == 404 else "Method Not Allowed")
        elif expected["status"] == 503:
            detail = response_json(payload).get("detail", {})
            content = "application/json" in media and detail.get("code") == "daemon_recovering" and bool(detail.get("required_action"))
        elif expected["status"] == 307:
            location = urlsplit(response["headers"].get("location", ""))
            content = location.path + ("?" + location.query if location.query else "") == expected["location_path_query"]
        elif expected["status"] in (404, 405):
            detail = response_json(payload).get("detail")
            wanted = {"code": "not_found"} if name in ("HX10.04", "HX10.06") else "Not Found" if expected["status"] == 404 else "Method Not Allowed"
            content = "application/json" in media and detail == wanted and payload != index
        elif name in ("HX10.03", "HX10.05", "HX10.07"):
            content = "application/json" in media and content_contract("H153", response, runtime, token, phase)
        elif target.startswith("/assets/"):
            content = payload == ASSET_BYTES and "text/plain" in media
        else:
            content = payload == index and "text/html" in media
        checks.require(key + "_content", content)
        closed = phase == "recovering" and expected["status"] == 503
        checks.require(key + "_body_boundary", bool(row) and (
            row.get("dispatched") is None and all(row.get(k, -1) == 0 for k in ("receive_calls", "parser_entries", "spool_entries", "native_entries", "preparation_entries"))
            if closed else row.get("spool_entries") == 0 and row.get("native_entries") == 0))
    for offset in range(0, len(entries), 8):
        await asyncio.gather(*(observe(entry) for entry in entries[offset:offset + 8]))


class MaintenanceWaits:
    """Test-side scheduling barriers around the ORIGINAL service sleep calls."""
    names = ("direct_connect_projection_sweep_loop", "zombie_reaper_loop",
             "workspace_cleanup_scheduler_loop", "disk_sweeper_loop")

    def __init__(self):
        self.original_sleep = asyncio.sleep
        self.permits = {name: asyncio.Queue() for name in self.names}
        self.entries = dict.fromkeys(self.names, 0)

    async def sleep(self, delay, result=None):
        task = asyncio.current_task()
        name = None if task is None else task.get_name()
        if name not in self.permits:
            return await self.original_sleep(delay, result)
        self.entries[name] += 1
        await self.permits[name].get()
        return result

    async def parked(self, minimum):
        deadline = time.monotonic() + 3
        while any(self.entries[name] < minimum[name] for name in self.names):
            if time.monotonic() >= deadline:
                raise TimeoutError("original maintenance consumers did not reach their sleep barriers")
            await self.original_sleep(.01)

    async def tick(self):
        expected = {name: count + 1 for name, count in self.entries.items()}
        for permit in self.permits.values():
            permit.put_nowait(None)
        await self.parked(expected)


async def maintenance_switch_cases(checks, state, client, waits):
    """C09/C13: real four consumers, supported switch, joined stale native work."""
    from runtime.runtime import daemon_home, RuntimeDir
    from runtime.models import TaskStatus
    await waits.parked(dict.fromkeys(waits.names, 1))
    selected = {task.get_name(): task for task in state._startup_recovery._consumers
                if task.get_name() in waits.names}
    checks.require("C13_four_original_consumers", len(selected), 4)
    # End only this disposable counter task via its original native task writer;
    # its genuine callback/history is retained. No real task is touched.
    from datetime import datetime, timezone
    state.orgs["fixture-a"].db.update_task("TASK-001", status=TaskStatus.COMPLETED,
        completed_at=datetime.now(timezone.utc).isoformat())
    # The actual disk scheduler can observe a due tick, but may never delete.
    # These are isolated fixture control files, created before scheduled use.
    data = daemon_home() / "disk-sweep"
    data.mkdir(exist_ok=True)
    (data / "stop.json").write_text('{"stop":true}')
    epoch, runtime = state._startup_recovery.epoch, state.runtime
    response = await client.post("/api/v1/runtime/use", json={"path": str(runtime.root)})
    checks.require("C13_supported_runtime_switch", response.status_code, 200)
    if response.status_code != 200:
        raise AssertionError("supported switch refused; no fake attachment fallback")
    checks.require("C13_runtime_replaced_same_epoch", state.runtime is not runtime
                   and state._startup_recovery.epoch == epoch)
    await waits.tick()
    checks.require("C13_same_four_services_after_switch", all(not task.done() for task in selected.values()))
    marker = data / "last-scheduled.json"
    marker_bytes = marker.read_bytes()
    date = json.loads(marker_bytes)["scheduled_date"]
    summary = data / f"{date}.json"
    summary_bytes = summary.read_bytes()
    report = json.loads(summary_bytes)
    checks.require("C13_actual_due_tick_suspended", report["outcome"], "suspended")
    checks.require("C13_no_disk_worker_PID", report.get("worker_pid"), None)
    await waits.tick()
    checks.require("C13_same_night_marker_not_reset", marker.read_text(), marker_bytes.decode())
    checks.require("C13_same_night_no_second_summary", summary.read_text(), summary_bytes.decode())

    # Block AFTER the real pending-operation read in the original projection
    # worker. The original _sweep_once still performs all policy/effect checks.
    store = state.direct_connect_authority_store
    original = store.list_latest_operations_pending_projection
    entered, release = threading.Event(), threading.Event()
    def held_read():
        pending = original()
        entered.set()
        if not release.wait(2):
            raise TimeoutError("selected projection native wait exceeded2s")
        return pending
    before = database_observation(state.runtime)
    old_runtime = state.runtime
    store.list_latest_operations_pending_projection = held_read
    try:
        waits.permits["direct_connect_projection_sweep_loop"].put_nowait(None)
        if not await asyncio.to_thread(entered.wait, 2):
            raise AssertionError("original projection producer not entered")
        checks.require("C09_projection_native_owner_retained", bool(state._maintenance_workers))
        # Test-side attachment invalidation while selected work is waiting;
        # ordinary supported switching itself refuses an active native owner.
        state.runtime = RuntimeDir.load(old_runtime.root)
        release.set()
        target = dict(waits.entries)
        target["direct_connect_projection_sweep_loop"] += 1
        await waits.parked(target)
        checks.require("C09_old_projection_joined", not state._maintenance_workers)
        checks.require("C09_old_projection_no_org_effect", database_observation(state.runtime), before)
        checks.require("C09_projection_service_survives_selected_refusal",
                       not selected["direct_connect_projection_sweep_loop"].done())
    finally:
        release.set()
        store.list_latest_operations_pending_projection = original
        # Restore only the fixture's attachment perturbation; no gate reset.
        state.runtime = old_runtime
    await waits.tick()
    checks.require("C13_new_runtime_projection_still_available",
                   all(not task.done() for task in selected.values()))
    checks.require("C13_no_duplicate_consumers", [id(task) for task in selected.values()],
                   [id(task) for task in state._startup_recovery._consumers if task.get_name() in waits.names])
    # Error/stop are deliberately not repaired as a runtime rebind.
    checks.details["maintenance_original_handles"] = {name: id(task) for name, task in selected.items()}


def invocation_observation(org, task_id):
    """Independent SQL/native owners; no expected policy is reimplemented."""
    db, store = org.db, org.db._task_pause_store
    with db._lock:
        row = db._conn.execute("SELECT * FROM task_pause_controls WHERE root_task_id=?", (task_id,)).fetchone()
        return {"task": org.db.get_task(task_id).model_dump(mode="json"),
                "journal": None if row is None else tuple(row),
                "reservations": sorted(org.orchestrator._team_moves._reservations),
                "live": sorted(store._live)}


async def task_invocation_wait_cases(checks, state):
    """C03/C08/C09: original TaskInvocation writers under original native locks."""
    from runtime.models import TaskRecord, TaskStatus
    from runtime.orchestrator.task_pause import TaskInvocation, release_live
    from runtime.infrastructure.task_pause_controls import TaskPauseStore
    from runtime.daemon.startup_recovery import StartupUnavailable
    from types import SimpleNamespace
    import inspect
    org = state.orgs["fixture-b"]
    store, orch = org.db._task_pause_store, org.orchestrator
    # Literal fit BEFORE these additional canonical task producers: one ready
    # preparation, registry and seven wait cases <=9 tasks/Pause rows,
    # zero callbacks/v2 generations/provider children/jobs in THIS keeper;
    # include all nine IDs in seed's cumulative thirteen-task/twelve-root fit.
    checks.details["C03_fit_before_task_producers"] = {"new_tasks": 9, "pause_rows": 9,
        "max_simultaneous_reservations": 1, "new_v2_rows": 0, "providers": 0}
    def create(number):
        task = TaskRecord(id=f"TASK-{100 + number}", status=TaskStatus.PENDING,
            assigned_agent="case_worker", brief="Owned fresh-writer lock observation")
        org.db.insert_task(task)
        return TaskInvocation(orch, task, "case_worker", orch._build_session_id())
    ready = create(0)
    ready.prepare()
    observed = invocation_observation(org, ready.task.id)
    checks.require("C03_ready_actual_prepare_journal_and_reservation",
        observed["journal"] is not None and ready.identity in observed["live"]
        and ready.identity in observed["reservations"])
    release_live(ready)
    checks.require("C03_ready_no_launch_settles", ready.identity not in store._live
                   and ready.identity not in orch._team_moves._reservations)

    # Post-COMMIT registry acquisition is a separate original wait. Hold the
    # worker at that exact line, then the original DB mutex, without wrapping
    # prepare or substituting its journal/reservation effects.
    registry = create(8)
    entered, release = threading.Event(), threading.Event()
    lines, first_line = inspect.getsourcelines(TaskInvocation.prepare)
    registry_line = next(first_line + offset for offset, line in enumerate(lines)
                         if "with self.store.db._lock:" in line)
    def registry_worker():
        previous = sys.gettrace()
        if previous is not None:
            raise RuntimeError("existing registry observer cannot be replaced")
        def trace(frame, event, _arg):
            if (frame.f_code is TaskInvocation.prepare.__code__ and event == "line"
                    and frame.f_lineno == registry_line):
                entered.set()
                if not release.wait(2):
                    raise TimeoutError("original registry wait exceeded2s")
            return trace
        sys.settrace(trace)
        try:
            registry.prepare()
        except StartupUnavailable as exc:
            return exc.code
        finally:
            sys.settrace(previous)
        return "unexpected_acceptance"
    before_task = org.db.get_task(registry.task.id).model_dump(mode="json")
    handle = asyncio.create_task(asyncio.to_thread(registry_worker))
    original_refs = org._startup_admission_refs
    try:
        if not await asyncio.to_thread(entered.wait, 2):
            raise AssertionError("original postcommit registry acquisition not reached")
        with org.db._lock:
            row = org.db._conn.execute("SELECT admission_journal_json FROM task_pause_controls "
                "WHERE root_task_id=?", (registry.task.id,)).fetchone()
            checks.require("C09_registry_actual_preparation_committed",
                row is not None and json.loads(row[0])["entries"][0]["id"] == registry.identity
                and registry.identity in orch._team_moves._reservations
                and registry.identity not in store._live)
            org._startup_admission_refs = tuple(list(original_refs))
            release.set()
        checks.require("C09_registry_postcommit_refusal", await asyncio.shield(handle),
                       "startup_attachment_changed")
        observed = invocation_observation(org, registry.task.id)
        checks.require("C09_registry_no_launch_task_unchanged", observed["task"], before_task)
        checks.require("C09_registry_owed_no_launch_settlement",
            registry.identity not in observed["reservations"] and registry.identity not in observed["live"])
        with org.db._lock:
            row = org.db._conn.execute("SELECT admission_journal_json FROM task_pause_controls "
                "WHERE root_task_id=?", (registry.task.id,)).fetchone()
            checks.require("C09_registry_original_journal_settled", json.loads(row[0])["entries"], [])
    finally:
        release.set()
        await asyncio.shield(handle)
        org._startup_admission_refs = original_refs

    cases = (("prepare", "db", False), ("prepare", "root", False),
             ("prepare", "move", False), ("commit", "root", False),
             ("commit", "move", False), ("commit", "terminal", False),
             ("prepare", "root", True))
    for number, (stage, lock_name, stop) in enumerate(cases, 1):
        invocation = create(number)
        if stage == "commit":
            invocation.prepare()
        before = invocation_observation(org, invocation.task.id)
        original_refs = org._startup_admission_refs
        ctx = SimpleNamespace(_lock=threading.Lock(), _terminal_reason=None)
        lock = {"db": org.db._lock, "root": store.gate(invocation.task.id),
                "move": orch._team_moves.gate, "terminal": ctx._lock}[lock_name]
        entered = threading.Event()
        # Trace the actual original acquisition line, after every earlier
        # validator. Merely entering writer() cannot prove a later wait seam.
        body = TaskPauseStore.writer.__wrapped__
        lines, first_line = inspect.getsourcelines(body)
        selected_line = {"db": "with self.db._lock:",
            "root": "ownership.enter_context(self.gate(root_id))",
            "move": "ownership.enter_context(move.gate",
            "terminal": "with commit_guard"}[lock_name]
        wait_line = next(first_line + offset for offset, line in enumerate(lines) if selected_line in line)
        code = body.__code__
        def worker():
            previous = sys.gettrace()
            if previous is not None:
                raise RuntimeError("existing worker observer cannot be replaced")
            def trace(frame, event, _arg):
                if frame.f_code is code and event == "line" and frame.f_lineno == wait_line:
                    entered.set()
                return trace
            sys.settrace(trace)
            try:
                if stage == "prepare":
                    invocation.prepare()
                else:
                    invocation._commit_local(ctx)
            except StartupUnavailable as exc:
                return exc.code
            finally:
                sys.settrace(previous)
            return "unexpected_acceptance"
        lock.acquire()
        handle = asyncio.create_task(asyncio.to_thread(worker))
        try:
            if not await asyncio.to_thread(entered.wait, 2):
                raise AssertionError("original invocation writer was not reached")
            checks.require(f"C03_{stage}_{lock_name}_actually_waiting", not handle.done())
            if stop:
                state._startup_recovery.stop()
            else:
                org._startup_admission_refs = tuple(list(original_refs))
        finally:
            lock.release()
        try:
            code_result = await asyncio.wait_for(asyncio.shield(handle), 3)
            prefix = f"C08_{stage}_{lock_name}" if stop else f"C09_{stage}_{lock_name}"
            checks.require(prefix + "_selected_refusal", code_result,
                           "daemon_recovery_failed" if stop else "startup_attachment_changed")
            checks.require(prefix + "_before_effect_rows_and_owners",
                           invocation_observation(org, invocation.task.id), before)
        finally:
            # Join actual worker before restoring fixture attachments/settling.
            if not handle.done():
                await asyncio.shield(handle)
            org._startup_admission_refs = original_refs
            if stage == "commit":
                release_live(invocation)  # owed original no-launch cleanup after stop
    checks.missing("C03_v2_causal_positive", "canonical generation/claim matrix stays root-owned; no fabricated v2 input")


async def serving(checks, runtime):
    import httpx
    import uvicorn
    from runtime.config import Settings
    from runtime.daemon import __main__ as daemon, runtimes, paths
    from runtime.daemon.app import create_app
    from runtime.daemon.startup_recovery import StartupUnavailable
    runtimes.register(runtime.root)
    token = paths.ensure_token()
    prepare_task_executable(checks)
    state = daemon._build_state(Settings(project_root=SOURCE, session_timeout_seconds=15))
    if set(state.orgs) != {"fixture-a", "fixture-b"}:
        raise AssertionError("P0 failed to attach both original fixture orgs")
    selected = checks.details.pop("_http_inputs")
    index = prepare_static(checks)
    app = create_app(state)
    wire = []
    observation = EntryObservation(app)
    task_entries = TaskEntries(state)
    checks.details["wire"] = wire
    from starlette.routing import Match
    def identity(route):
        endpoint = getattr(route, "endpoint", None)
        return {"path": getattr(route, "path", None),
                "endpoint": None if endpoint is None else getattr(endpoint, "__module__", "") + "." + getattr(endpoint, "__qualname__", type(endpoint).__name__)}


    class ObserveReceive:
        def __init__(self, app):
            self.app = app

        async def __call__(self, scope, receive, send):
            if scope["type"] != "http":
                return await self.app(scope, receive, send)
            row = {"method": scope["method"], "raw_path": scope["raw_path"].decode("ascii"),
                   "path": scope["path"], "query": scope["query_string"].decode("ascii"),
                   "case": dict(scope["headers"]).get(b"x-startup-case", b"").decode("ascii"),
                   "receive_calls": 0, "parser_entries": 0, "spool_entries": 0,
                   "native_entries": 0, "preparation_entries": 0, "asgi_body_bytes": 0,
                   "router_candidates": []}
            for route in app.routes:
                match, _ = route.matches(scope)
                if match is not Match.NONE:
                    row["router_candidates"].append({**identity(route), "match": match.name})
            wire.append(row)
            if len(wire) > 1024:
                raise RuntimeError("wire observation cap")
            marker = observation.current.set(row)

            async def counted_receive():
                row["receive_calls"] += 1
                return await receive()

            async def counted_send(message):
                if message["type"] == "http.response.start":
                    row["asgi_status"] = message["status"]
                    row["asgi_headers"] = {k.decode("latin1"): v.decode("latin1") for k, v in message["headers"]}
                elif message["type"] == "http.response.body":
                    body = message.get("body", b"")
                    row["asgi_body_bytes"] += len(body)
                    if row["method"] == "HEAD":
                        if row["asgi_body_bytes"] > 16384:
                            raise ValueError("HEAD ASGI observation cap")
                        row["head_asgi_body"] = row.get("head_asgi_body", "") + body.decode("utf-8", errors="replace")
                await send(message)
            try:
                return await self.app(scope, counted_receive, counted_send)
            finally:
                row["dispatched"] = identity(scope["route"]) if "route" in scope else None
                observation.current.reset(marker)

    app.add_middleware(ObserveReceive)
    # Verify the accepted literal absent ID independently, before the native hold.
    for slug, root in runtime.iter_org_roots():
        conn = sqlite3.connect((root / "happyranch.db").as_uri() + "?mode=ro", uri=True, timeout=.05)
        try:
            checks.require("C02_" + slug + "_absent_literal_thread",
                conn.execute("SELECT COUNT(*) FROM threads WHERE id=?", ("THR-absent-13137",)).fetchone()[0], 0)
        finally:
            conn.close()
    original = daemon._sweep_on_startup
    entered, release = threading.Event(), threading.Event()
    wait_error = []

    def held(db, queue, slug, orchestrator=None):
        # Preserve genuine P2 pending publications in BOTH orgs. The single
        # native hold is after the original second-org sweep and under its
        # SAME writer mutex; no manual queue slots or fabricated publication.
        result = original(db, queue, slug, orchestrator)
        if slug == "fixture-b":
            with db._lock:
                checks.details["native_sweep_thread"] = threading.get_ident()
                checks.details["hold_started_monotonic"] = time.monotonic()
                entered.set()
                if not release.wait(10):
                    wait_error.append("held-window expired")
                    raise TimeoutError("owned native hold exceeded10s")
        return result

    daemon._sweep_on_startup = held
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    (checks.args.root / "native/binding.json").write_text(json.dumps({
        "root": str(checks.args.root), "port": port, "token": token,
        "tasks": {slug + "/" + row["task_id"]: row["agent"]
                  for slug, row in checks.details["native_task_selections"].items()}}))
    server = daemon._DiagnosticServer(uvicorn.Config(app, host="127.0.0.1", port=port,
        log_level="warning", timeout_graceful_shutdown=15), app)
    maintenance_waits = MaintenanceWaits()
    asyncio.sleep = maintenance_waits.sleep
    task_entries.start()
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
            await held_task_cases(checks, runtime, state, task_entries)
            task_entries.close()
            before = fixture_effects(runtime, state, checks.args.root)
            observation.start()
            await early_cases(checks, runtime, port, token, wire, "recovering")
            await literal_cases(checks, runtime, port, token, wire, "recovering", selected["literal22"], index)
            # Original static selections are separate from the22 variants.
            static_entries = [{"id": name, "method": "GET", "request_target": target, "request_body": None,
                "during_recovery": {"status": 200}, "after_ready": {"status": 200}}
                for name, target in (("HX02", "/assets/startup-gate-probe.txt"), ("HX03", "/"), ("HX04", "/threads"))]
            await literal_cases(checks, runtime, port, token, wire, "recovering", static_entries, index)
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
            # State guard ONLY. The nineteen direct obligations have their own
            # original entries, valid ownership and independent effect oracles.
            try:
                state._require_startup_ready()
            except StartupUnavailable as exc:
                checks.require("C03_direct_gate_refuses", exc.code, "daemon_recovering")
            else:
                checks.require("C03_direct_gate_refuses", False)
            if selected["declarations"]:
                entries = selected["declarations"]
                async def observe_held(entry):
                    # Real loopback request with a bounded body. This observes
                    # status/code only; the separate prebody oracle is not
                    # inferred from it and remains explicit below.
                    response = await client.request(entry["method"], request_path(entry["path"]),
                                                    content=b"{}", headers={"Content-Type": "application/json",
                                                                             "X-Startup-Case": entry["id"]})
                    checks.require(entry["id"] + "_held_status", response.status_code, 503)
                    # HEAD transport is empty; retain app-emitted code only from ORIGINAL ASGI send.
                    matching = [row for row in wire if row["case"] == entry["id"]]
                    emitted = (response_json(matching[0].get("head_asgi_body", "{}")) if entry["method"] == "HEAD" and len(matching) == 1 else response_json(response.content))
                    checks.require(entry["id"] + "_held_code",
                                   emitted.get("detail", {}).get("code"), "daemon_recovering")
                    matching = [row for row in wire if row["case"] == entry["id"]]
                    checks.require(entry["id"] + "_one_observed_request", len(matching), 1)
                    if len(matching) == 1:
                        checks.require(entry["id"] + "_prebody_receive_zero", matching[0]["receive_calls"], 0)
                        checks.require(entry["id"] + "_original_parser_spool_native_zero",
                            {k: matching[0][k] for k in ("parser_entries", "spool_entries", "native_entries", "preparation_entries")},
                            dict.fromkeys(("parser_entries", "spool_entries", "native_entries", "preparation_entries"), 0))
                    checks.details.setdefault("declaration_receipts", []).append({
                        "id": entry["id"], "phase": "recovering", "method": entry["method"],
                        "path": request_path(entry["path"]), "request_body": "generic {} prebody control only",
                        "head": checks.details["head"], "source_owner": entry["owner"],
                        "original_ASGI": matching, "transport_status": response.status_code,
                        "observer": "own case-correlated receive + independent batch SQL/files/owner observation"})
                held_entries = []
                for entry in entries:
                    if entry["early"].startswith("held:"):
                        held_entries.append(entry)

                # Eight is the ORIGINAL concurrent-request ceiling. Serial
                # batches avoid extending or replenishing the native hold.
                for offset in range(0, len(held_entries), 8):
                    await asyncio.gather(*(observe_held(entry) for entry in held_entries[offset:offset + 8]))
            checks.require("C02_held_all_original_rows_unchanged", database_observation(runtime), before["rows"])
            checks.require("C02_recovering_rows_files_owners_unchanged", fixture_effects(runtime, state, checks.args.root), before)
            checks.require("C02_recovering_parser_spool_native_zero", observation.totals,
                           dict.fromkeys(observation.totals, 0))
            observation.close()
            checks.details["hold_before_release_seconds"] = time.monotonic() - checks.details["hold_started_monotonic"]
            checks.require("C02_original_hold_not_expired", not wait_error and checks.details["hold_before_release_seconds"] < 10)
            task_entries.phase = "ready"
            task_entries.start()
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
            await ready_task_cases(checks, runtime, state, task_entries)
            task_entries.close()
            response = await client.get("/api/v1/orgs/fixture-a/threads/THR-001/messages?limit=10")
            checks.require("C01_post_ready_transcript", response.status_code == 200 and MESSAGE in response.text)
            checks.details["completed_startup"] = state._startup_recovery.snapshot()
            checks.require("C10_owned_reader_admissions_retired", state._startup_reads.snapshot()["active"], 0)
            # All ready HTTP controls precede maintenance switch/stop invalidation.
            await maintenance_waits.parked(dict.fromkeys(maintenance_waits.names, 1))
            before_ready = fixture_effects(runtime, state, checks.args.root)
            observation.totals = dict.fromkeys(observation.totals, 0)
            observation.start()
            await early_cases(checks, runtime, port, token, wire, "ready")
            await literal_cases(checks, runtime, port, token, wire, "ready", selected["literal22"], index)
            await literal_cases(checks, runtime, port, token, wire, "ready", static_entries, index)
            checks.require("C02_ready_rows_files_owners_unchanged", fixture_effects(runtime, state, checks.args.root), before_ready)
            checks.require("C02_ready_spool_native_zero",
                {k: v for k, v in observation.totals.items() if k != "parser"},
                {k: 0 for k in observation.totals if k != "parser"})
            checks.details["ready_original_entry_counts"] = dict(observation.totals)
            observation.close()
            await maintenance_switch_cases(checks, state, client, maintenance_waits)
            await task_invocation_wait_cases(checks, state)
            response = await client.get("/api/v1/health/ready")
            checks.require("C08_stop_closes_ready", response.status_code, 503)
    finally:
        if task_entries.active:
            task_entries.close()
        checks.details["task_original_entries"] = task_entries.rows
        if observation.active:
            observation.close()
        release.set()
        server.should_exit = True
        try:
            await asyncio.wait_for(asyncio.shield(handle), timeout=20)
            checks.require("C08_original_lifespan_positive_close", state._shutdown_complete)
        finally:
            daemon._sweep_on_startup = original
            asyncio.sleep = maintenance_waits.original_sleep
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
    parser.add_argument("--routing-inventory", type=Path, help="accepted literal22 JSON, explicit caller-supplied path")
    parser.add_argument("--direct-inventory", type=Path, help="accepted original89 direct records; nineteen task/host selections")
    parser.add_argument("--web", action="store_true")
    args = parser.parse_args()
    isolate(args)  # precedes EVERY product import and constructor
    checks = Checks(args)
    try:
        if args.case == "all":
            checks.details["_http_inputs"] = load_http_inputs(checks)
            load_task_inputs(checks)
        runtime = seed(checks)
        if args.case == "all" and runtime is not None:
            if args.web:
                web(checks)
            asyncio.run(serving(checks, runtime))
    except BaseException as exc:
        checks.rows.append({"assertion": "driver_completion", "status": "FAIL", "exception": type(exc).__name__,
                            "reason": str(exc)[:512]})
        traceback.print_exc()
    finally:
        checks.save()
    return checks.disposition()


if __name__ == "__main__":
    raise SystemExit(main())
