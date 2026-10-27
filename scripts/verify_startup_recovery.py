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
import runpy
import shlex
import signal
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
from typing import Any

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
# Accepted matrix lower-boundary limitation; these are obligations, not waivers.
TASK_SOURCE_LIMITATIONS = {
    "D011": {"caller": "run_step._run_step_body -> Orchestrator._run_agent",
             "prerequisite": "original prepared TaskInvocation/current native SID"},
    "D012": {"caller": "Orchestrator._run_agent -> _run_agent_impl",
             "prerequisite": "same prepared invocation, task/agent/SID and original workspace"},
    "D013": {"caller": "Orchestrator._run_agent_impl -> _launch_agent_with_scratch",
             "prerequisite": "original prepared invocation and owned scratch/launch inputs"},
    "D014": {"caller": "Orchestrator._launch_agent_with_scratch -> _run_agent_launch_contained",
             "prerequisite": "same prepared invocation and original contained launch inputs"},
    "D086": {"caller": "Orchestrator._run_agent_launch_contained -> HostSessionSupervisor.run",
             "prerequisite": "authentic task invocation and original AdmissionRequest/cancellation owner"},
    "D087": {"caller": "HostSessionSupervisor.run -> _execute_attempt",
             "prerequisite": "original granted admission lease and host execution context"},
    "D088": {"caller": "Orchestrator._run_agent_impl no-TaskInvocation fallback -> SessionTracker.set_active",
             "prerequisite": "authentic no-TaskInvocation launch selection; installed move-aware task path refuses it"},
    "D089": {"caller": "SessionTracker.register_recovery_session has no production caller",
             "prerequisite": "authentic original bound recovery selection through THIS helper"},
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


READER_ASSERTIONS = {
    'C04': (
        'C04_reader_two_running_two_queued',
        'C04_reader_actual_admission_bounds',
        'C04_reader_fifth_busy',
        'C04_reader_fifth_no_submission_or_connection',
        'C04_reader_independent_native_bounds',
    ),
    'C08': (
        'C08_reader_queued_cancel_actual_waiter_cancelled',
        'C08_reader_close_actual_waiter_cancelled',
        'C08_reader_queued_cancel_no_native_work',
        'C08_reader_running_cancel_retains_owner',
        'C08_reader_close_then_future_then_retirement',
        'C08_reader_close_same_worker_close',
        'C08_reader_deadline_same_worker_close',
        'C08_reader_running_cancel_error_preserved',
        'C08_reader_all_selected_slots_retired',
    ),
    'C10': (
        'C10_reader_fixture-a_original_transcript_before',
        'C10_reader_fixture-b_original_transcript_before',
        'C10_reader_queue_original_deadline',
        'C10_reader_deadline_503',
        'C10_reader_queued_deadline_503',
        'C10_reader_deadline_original_retirement',
        'C10_reader_queued_deadline_original_retirement',
        'C10_reader_queued_deadline_before_open',
        'C10_reader_delayed_original_sql_forwarded',
        'C10_reader_exact_lifecycle_counters',
        'C10_reader_fixture-a_original_transcript_restored',
        'C10_reader_fixture-b_original_transcript_restored',
    ),
}


# C14 remains incomplete: these finite consumers do not cover start/setup/Mac/browser.
CONSUMER_ASSERTIONS = (
    "listener_identity", "no_reader_or_provider_overlap", "fresh_health", "fresh_ready",
    "status_process", "status_exit", "status_output", "status_parser", "status_wire",
    "web_process", "web_exit", "web_output", "web_wire",
    "fixture-a_process", "fixture-a_exit", "fixture-a_transcript", "fixture-a_wire",
    "fixture-b_process", "fixture-b_exit", "fixture-b_transcript", "fixture-b_wire",
    "setup_original_admission", "rows_files_owners_unchanged", "discovery_unchanged",
)


FAILURE_ASSERTIONS = {
    "C03": ("p0_expected_rows", "p0_immutable_records"),
    "C05": ("original_reader", "actual_denial", "owned_read_unavailable", "clean_connection",
            "callback_restored", "authority_normal_none", "sweep_native_chain", "no_ready_consumers",
            "no_provider", "listener", "health", "ready", "health_wire", "health_identity", "ready_wire", "ready_identity"),
    "C08": ("recovery_close", "reader_retirement", "reader_executor", "database_order",
            "server_socket", "observers_restored", "first_cause_retained", "durable_preserved"),
    "C13": ("same_store_before_reopen", "distinct_owners", "fresh_p0_records", "fresh_ready"),
}


# Independent ROOT step43 / TASK13326 reconstructed table, not product-derived.
# This closed source selection is checked BEFORE any fixture constructor.
NTS_SELECTED_ROWS = {
    "audit_log": 111,
    "authority_policy_active_selector": 2,
    "authority_policy_active_selector_history": 3,
    "authority_policy_v2_activations": 1,
    "authority_policy_v2_attempts": 1,
    "authority_policy_v2_control_audit": 4,
    "authority_policy_v2_releases": 1,
    "authority_policy_v2_session_bindings": 1,
    "identity_name_claims": 8,
    "identity_name_owners": 8,
    "identity_name_schema": 2,
    "jobs": 1,
    "org_settings": 10,
    "remote_runner_schema_migrations": 4,
    "session_token_usage": 3,
    "task_completion_recoveries": 1,
    "task_pause_controls": 12,
    "task_results": 5,
    "tasks": 14,
    "thread_messages": 2,
    "threads": 2,
    "workflow_adapter_versions": 2,
    "workflow_authority_pointers": 2,
    "workflow_cutover_events": 2,
    "workflow_cutover_state": 2,
    "workflow_draft_adapter_versions": 2,
    "workflow_publication_journals": 3,
    "workflow_publication_leases": 30,
    "workflow_submission_schema_versions": 2
}

NTS_ASSERTIONS = {
    "N": {"C05": ("original_binding", "completion_normal_false", "original_consumer",
                    "first_cause", "no_ready"), "C08": ("retired",)},
    "T": {"C05": ("canonical_selected", "actual_thread_context", "actual_denial",
                    "late_finally_merge", "whole_batch_suppressed", "no_new_effects",
                    "committed_prefix"), "C08": ("retired",)},
    "S": {"C08": ("live_handles", "waiter_cancelled", "stop_admission", "close_refused",
                    "loop_retirement_ack", "shutdown_order", "first_causes"),
          "C13": ("durable_handoff",)},
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
            for phase in ("recovering", "ready"):
                self.expect("C14_" + phase + "_read_consumers", "C14", phase,
                    tuple("C14_" + phase + "_" + name for name in CONSUMER_ASSERTIONS),
                    "original status/web/two-org JSON CLI/static setup admission; zero new rows; start/full setup/Mac NOT RUN")
            for case, assertions in READER_ASSERTIONS.items():
                self.expect(case + "_selected_reader_lifecycle", case, "recovering", assertions,
                            "original mounted pool, canonical two transcripts, real native Futures/SQL/close/retirement; zero new rows")
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
            for group, cases in NTS_ASSERTIONS.items():
                for case, suffixes in cases.items():
                    self.expect(case + "_selected_" + group, case, group,
                        tuple(case + "_" + group + "_" + suffix for suffix in suffixes),
                        "original binding/tail/stop ownership and positive serial closure; behavior NOT RUN until allocated")
            self.expect("C08_success_consumed_owner", "C08", "recovering",
                ("C08_success_consumed_zero_job_tail_retired",),
                "original consumed-owner zero-job tail's actual retained Thread/Future and positive original drain")
            self.expect("C03_selected_extra_epochs", "C03", "T/N/S",
                ("C03_T_initial_p0", "C03_new_epoch_cumulative"),
                "independently complete five-epoch preconstructor ledger; one seed, no reset")
            for case, suffixes in FAILURE_ASSERTIONS.items():
                self.expect(case + "_selected_failed_epoch", case, "recovery_failed",
                    tuple(case + "_failed_" + suffix for suffix in suffixes),
                    "original authority None / exceptional native Future, actual listener and positive owner closure")
            for name in ("HX10.04", "HX10.11"):
                self.expect(name + "_recovery_failed", "C05", "recovery_failed",
                    tuple(name + "_recovery_failed_" + suffix for suffix in
                          ("status", "content", "identity", "wire", "body_boundary", "effects")),
                    "exact original accepted literal bytes and independent per-request zero effects")
            for slug in ("fixture-a", "fixture-b"):
                for name in ("H153", "H156", "H157"):
                    key = name + "_failed_" + slug
                    self.expect(key, "C10", "recovery_failed",
                        tuple(key + "_" + suffix for suffix in
                              ("status", "content", "identity", "wire", "auth", "effects")),
                        "original authenticated finite handler with known transcript for THIS org")
                for boundary in ("state", "queue"):
                    key = "C05_failed_" + slug + "_" + boundary
                    self.expect(key, "C05", "recovery_failed", (key,),
                                "valid canonical TASK-090 owner, original failed admission, unchanged queue and effects")
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
                    if name in TASK_SOURCE_LIMITATIONS:
                        limitation = TASK_SOURCE_LIMITATIONS[name]
                        if phase == "recovering" or name in ("D088", "D089"):
                            reason = ("SOURCE ONLY / NOT RUN: held original epoch cannot issue a fresh "
                                "prepared invocation/native lease because TaskInvocation.prepare checks ordinary admission first"
                                if phase == "recovering" and name not in ("D088", "D089") else
                                "SOURCE ONLY / NOT RUN: " + limitation["prerequisite"])
                            self.obligations[name + "_" + phase].update(
                                actual_caller=limitation["caller"], source_disposition=reason,
                                missing_prerequisite=limitation["prerequisite"],
                                limitation="accepted matrix lower-boundary venue limit; obligation remains mandatory")
            self.details["task_uncredited_dependencies"] = [
                {"symbol": "SessionTracker._set_active_locked",
                 "caller": "Orchestrator._run_agent_impl._publish_binding under original binding lease",
                 "credit": "NONE for D088/D089; distinct actual bound-launch dependency"},
                {"symbol": "SessionTracker.publish_recovery_session",
                 "caller": "TaskInvocation._commit_local recovery publication",
                 "credit": "NONE for D088/D089; distinct actual recovery dependency"}]
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


def fixture_accounting(case):
    """Cumulative canonical INSERT plan, not measured rows or resource PASS.

    The selected 128 denominator deliberately retains ALL audit_log rows, including
    config/native-writer/outer audits; the 512 causal allowance cannot absorb
    its excess. Mutable journals/selectors are identities, not new rows on
    every UPDATE. Publication leases are counted even after their DELETE.
    """
    # Original seed_counter writers: release, activation, initial+active
    # selector history, current selector, three control audits, binding,
    # attempt, and its admission audit. Original C12 observer stays untouched.
    tables = {"authority_policy_v2_releases": 1,
              "authority_policy_v2_activations": 1,
              "authority_policy_active_selector": 1,
              "authority_policy_active_selector_history": 2,
              "authority_policy_v2_control_audit": 3,
              "authority_policy_v2_session_bindings": 1,
              "authority_policy_v2_attempts": 1, "audit_log": 1,
              "tasks": 1, "task_results": 1}
    producers = [{"owner": "seed_counter -> Database canonical v2 writers",
                  "multiplicity": 1, "tables": dict(tables)}]
    if case == "all":
        # These are accepted T/N/S/failed-prefix/fresh paths, with mandatory native
        # and outer tails. No survivor-only count, history reuse or quota reset.
        producers += [
            {"owner": "DaemonState.from_runtime -> ensure_authority_selector (fixture-b only)",
             "multiplicity": 1, "tables": {"authority_policy_active_selector": 1,
                "authority_policy_active_selector_history": 1, "authority_policy_v2_control_audit": 1}},
            {"owner": "OrgState.load -> seed_org_settings_from_config -> upsert_org_setting",
             "multiplicity": 2, "tables": {"org_settings": 5, "audit_log": 5}},
            # NativeWriterStore.save_uncommitted appends BOTH active/settled
            # even when the selected authority writer is an unchanged read.
            # Each P0: identity_names_load + from_runtime selector +
            # _build_state selector, in EACH org. Runtime/use: identity+selector
            # in EACH org again. Existing sentinels prevent config reseeding.
            {"owner": "T/N/S/failed/fresh _build_state: 5 epochs * 2 orgs * 3 intervals * active/settled",
             "multiplicity": 30, "tables": {"audit_log": 2}},
            {"owner": "runtime/use -> from_runtime: 2 orgs * 2 native_writer intervals * active/settled",
             "multiplicity": 4, "tables": {"audit_log": 2}},
            {"owner": "P5 agent_yaml_frontmatter_migration: both orgs, active/settled",
             "multiplicity": 2, "tables": {"audit_log": 2}},
            {"owner": "P5 identity_names_compatibility: both orgs, active/settled",
             "multiplicity": 2, "tables": {"audit_log": 2}},
            {"owner": "seed TASK-090 + ready_task_cases.create_task + nine TaskInvocation cases",
             "multiplicity": 1, "tables": {"tasks": 12}},
            # Each original callback owns its genuine INTEGER task_results row.
            # All three: session_start, record_started, scratch teardown,
            # session_end, _log_step_result completion_report = FIVE audits.
            {"owner": "THREE original native chains -> callback/result + five session/native/tail audits",
             "multiplicity": 3, "tables": {"task_results": 1, "audit_log": 5, "session_token_usage": 1}},
            # fixture-b's authenticated EMPTY selector resolves snapshot=None:
            # ONE legacy_static audit, not a v2 binding/attempt or selector audit.
            {"owner": "TWO fixture-b managers -> legacy_static binding + orchestration_step",
             "multiplicity": 2, "tables": {"audit_log": 2}},
            {"owner": "ONCE canonical T insert/claim/publish/admit/job writers",
             "multiplicity": 1, "tables": {"tasks": 1, "task_results": 1,
                "task_completion_recoveries": 1, "jobs": 1}},
            {"owner": "T original accepted common consumer: bound completion_report + orchestration_step",
             "multiplicity": 1, "tables": {"audit_log": 2}},
            {"owner": "C13 completed suspended disk tick -> one original summary per org",
             "multiplicity": 2, "tables": {"audit_log": 1}},
            {"owner": "fixture-a worker -> _log_verdict_if_delegated",
             "multiplicity": 1, "tables": {"audit_log": 1}},
            # At most three native root controls plus nine writer roots.
            # Only five of those nine prepare successfully in the unchanged
            # success path; keep all nine in the conservative identity bound.
            {"owner": "TaskPauseStore.ensure_uncommitted (3 native roots + 9 writer roots)",
             "multiplicity": 1, "tables": {"task_pause_controls": 12}},
            {"owner": "two canonical transcript producers", "multiplicity": 2,
             "tables": {"threads": 1, "thread_messages": 1}},
            {"owner": "initialize_complete_org_schema(I) / _install_foundation", "multiplicity": 2,
             "tables": {"workflow_adapter_versions": 1, "workflow_cutover_state": 1,
                "workflow_cutover_events": 1, "workflow_draft_adapter_versions": 1,
                "workflow_submission_schema_versions": 1}},
            {"owner": "initial authority publications + fixture-b selector publication", "multiplicity": 1,
             "tables": {"workflow_authority_pointers": 2, "workflow_publication_journals": 3}},
            # Deleted rows remain cumulative: coherent recover runs even
            # when the authority pointer is unchanged in either reopen.
            {"owner": "recover_or_publish -> recover: T/N/S/failed/fresh/runtime-use * two orgs",
             "multiplicity": 12, "tables": {"workflow_publication_leases": 1}},
            {"owner": "initial fence/publication(two orgs) + fixture-b selector fence/publication, ONCE",
             "multiplicity": 6, "tables": {"workflow_publication_leases": 1}},
            {"owner": "ProfileCoordinator empty dependency synchronization: T/N/S/failed/fresh/runtime-use * two orgs",
             "multiplicity": 12, "tables": {"workflow_publication_leases": 1}},
            {"owner": "refresh_names install (founder + three tracked agents)", "multiplicity": 2,
             "tables": {"identity_name_schema": 1, "identity_name_owners": 4, "identity_name_claims": 4}},
            {"owner": "Database native remote-schema markers", "multiplicity": 2,
             "tables": {"remote_runner_schema_migrations": 2}},
        ]
        for producer in producers[1:]:
            for table, count in producer["tables"].items():
                tables[table] = tables.get(table, 0) + producer["multiplicity"] * count
    else:
        # C12 constructs one complete-I database too; include its canonical
        # initialization rows without touching the independent counter body.
        initialized = {"workflow_adapter_versions": 1, "workflow_cutover_state": 1,
                       "workflow_cutover_events": 1, "workflow_draft_adapter_versions": 1,
                       "workflow_submission_schema_versions": 1, "remote_runner_schema_migrations": 2}
        tables.update(initialized)
        producers.append({"owner": "C12 Database/initialize_complete_org_schema(I)",
                          "multiplicity": 1, "tables": initialized})
    # Conditional maximum: TWO original terminal root reconciliation pairs.
    # Native bounded preservation can return without admission/start/finish;
    # never rely on deleting or omitting those retained tails for a fit.
    # Inert admitted done residue is not v2 refusal: authority's D2-B' branch
    # appends ZERO on both startup/reload; no candidate/generation is created.
    upper = dict(tables)
    if case == "all":
        upper["audit_log"] += 4
        producers.append({"owner": "two _root_worktree_batch admissions -> original start/finish",
                          "multiplicity": 2, "tables": {"audit_log": 2}, "conditional": True})
    history = lambda rows: sum(count for table, count in rows.items() if history_table(table))
    limits = {"orgs": 2, "roots": 16, "tasks": 64, "history_controls": 128 if case == "all" else 64,
              "causal_rows": 512, "threads": 8, "messages": 64, "tokens": 16,
              "jobs": 8, "simultaneous_harmless_children": 2, "browser_contexts": 1,
              "listeners": 2, "concurrent_requests": 8, "fixture_bytes": 512 * 1024 * 1024,
              "wall_seconds": 600, "hold_seconds": 10, "http_seconds": 2, "child_seconds": 15}
    dimensions = {"orgs": 1 if case == "C12" else 2, "roots": 1 if case == "C12" else 13,
                  "tasks": upper["tasks"], "history_controls": history(upper),
                  # Count ALL planned canonical rows conservatively here too,
                  # including controls and retired leases, without moving them
                  # out of the independent history/control denominator.
                  "causal_rows": sum(upper.values()), "threads": upper.get("threads", 0),
                  "messages": upper.get("thread_messages", 0),
                  "tokens": upper.get("thread_invocations", 0) + upper.get("session_token_usage", 0), "jobs": upper.get("jobs", 0),
                  "simultaneous_harmless_children": 0 if case == "C12" else 2,
                  "browser_contexts": 0, "listeners": 0 if case == "C12" else 2,
                  "concurrent_requests": 0 if case == "C12" else 8}
    return {"kind": "SOURCE_ONLY_NOT_RUN", "producers": producers,
            "historical_success_allocation": {"history": [74, 78], "logical": [167, 171], "leases_written": 10},
            "corrected_success_source": {"history": [74, 78], "logical": [171, 175], "leases": 14},
            "prior_failed_reopen_source": {"history": [86, 90], "logical": [187, 191], "leases": 18},
            "epochs": "T/N/S/failed/fresh, SAME stores, one preparation; positive serial listener custody",
            "additional_native_lifetimes": "3 recovery/read executors and servers, 1 S read waiter; T nonempty tail + N/final-success empty-job tails (3 total), each retained/joined",
            "observer_seconds": 3, "no_execution_allocation": "maker3/3 EXHAUSTED; QA0/3 UNUSED/NOT RELEASED",
            "created_rows_upper": upper, "selected_path_rows": tables,
            "history_controls_lower": history(tables), "dimensions": dimensions, "limits": limits,
            "native_launches_total": 0 if case == "C12" else 3,
            "native_scheduling": "none selected" if case == "C12" else "one original worker; ready_task_cases awaits original native retirement before adding one worker and dedup task; <=2 live, no total-launch cap",
            "mutable_updates": "Pause journal prepare/commit/retry(empty)/started/result_processing/close and move reservations do not insert history rows; no Pause control() call or move operation; no repository-preparation stream for repos={}",
            "zero_producers": "no memory bodies/impressions, attachments, model spend (native output still creates the separately counted forensic usage rows), job processes (one inert T row is charged), thread invocation, workflow instance, candidate/pin/evaluation/continuation, new v2 generation or host SQL receipt; HostSessionStore.publish is in memory",
            "runtime_limits": "byte/wall/hold/HTTP/child limits require actual observation; no source timing/resource PASS"}


def history_table(table):
    # Preserve the old denominator, correcting ONLY its two misspelled names.
    return table.startswith("authority_policy_v2") or table in (
        "authority_policy_active_selector_history", "authority_policy_active_selector", "audit_log")


def seed(checks):
    fit = fixture_accounting(checks.args.case)
    checks.details["fixture_fit_before_constructors"] = fit
    excess = {name: {"upper": count, "cap": fit["limits"][name],
                     "excess": count - fit["limits"][name]}
              for name, count in fit["dimensions"].items() if count > fit["limits"][name]}
    fit["excess"] = excess
    checks.save()
    if checks.args.case == "all" and fit["selected_path_rows"] != NTS_SELECTED_ROWS:
        checks.missing("C03_canonical_history_fit", "incomplete five-epoch producer inventory; constructors refused")
        return None
    if excess:
        # Independent pre-constructor admission, not an unconditional all-case
        # return. ROOT step43 accepts ONLY the complete five-epoch source plan:
        # audit_log111 + authority/selector13 =124, conditional upper128.
        # Logical241/245 includes30 cumulative publication leases.
        # Historical step41:86/90,187/191,18; earlier64/74-78/167-171 retained.
        # Future allocating cases need their own canonical producer ledger;
        # removing writers/history or moving an excess to512 is not a repair.
        checks.missing("C03_canonical_history_fit", json.dumps({
            "reason": "canonical fixture exceeds selected independent caps", "excess": excess,
            "history_controls_lower": fit["history_controls_lower"],
            "minimum_history_excess": max(0, fit["history_controls_lower"] - fit["limits"]["history_controls"]),
            "table": "audit_log", "producers": fit["producers"]}, sort_keys=True))
        return None
    from runtime.infrastructure.database import Database
    from runtime.infrastructure.workflow_schema import initialize_complete_org_schema
    from runtime.models import ThreadRecord, ThreadMessageKind
    from runtime.runtime import RuntimeDir
    runtime = RuntimeDir.init(checks.args.root / "runtime")
    for index, slug in enumerate(("fixture-a", "fixture-b")[:fit["dimensions"]["orgs"]]):
        org_root = runtime.orgs_dir / slug
        shutil.copytree(SOURCE / "e2e/template/org", org_root / "org")
        if checks.args.case == "all":
            # Effective session timeout selects org_settings BEFORE Settings.
            # Bound the SAME copied synthetic input before its canonical seed;
            # global Settings(15) alone would leave the tracked300s override.
            config_path = org_root / "org/config.yaml"
            config_text = config_path.read_text()
            if not config_text.startswith("session_timeout_seconds: 300\n"):
                raise ValueError("tracked synthetic timeout input changed; source accounting needs review")
            config_path.write_text(config_text.replace("session_timeout_seconds: 300\n",
                                                      "session_timeout_seconds: 15\n", 1))
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
                if index == 1:
                    from datetime import datetime, timedelta, timezone
                    from runtime.models import JobRecord, JobStatus, JobInterpreter
                    from runtime.daemon.jobs_runner import _INFLIGHT
                    stamp = datetime.now(timezone.utc)
                    origin, recovery = "fixture-T-origin", "fixture-T-recovery"
                    db.insert_task(TaskRecord(id="TASK-091", team="engineering", assigned_agent="case_manager",
                        status=TaskStatus.IN_PROGRESS, task_type="task", current_session_id=origin,
                        brief="Synthetic interrupted root; no real provider invocation claimed"))
                    if not db.claim_task_completion_recovery(task_id="TASK-091", agent="case_manager",
                            origin_session_id=origin, recovery_session_id=recovery,
                            provider_session_id="synthetic-T-not-native-provenance",
                            claimed_at=stamp.isoformat(), expires_at=(stamp + timedelta(seconds=120)).isoformat()):
                        raise RuntimeError("canonical T claim refused")
                    if not db.publish_task_completion_recovery_binding(task_id="TASK-091", agent="case_manager",
                            origin_session_id=origin, recovery_session_id=recovery):
                        raise RuntimeError("canonical T binding refused")
                    if not db.admit_task_completion_callback(task_id="TASK-091", agent="case_manager",
                            session_id=recovery, status="completed", confidence_score=100,
                            output_summary="Synthetic accepted recovery fixture",
                            decision_json=json.dumps({"action": "done", "summary": "Synthetic recovery complete"})):
                        raise RuntimeError("canonical T callback refused")
                    result = db.get_accepted_task_completion_recovery_result(task_id="TASK-091", agent="case_manager")
                    if result is None or type(result["id"]) is not int or len(db.get_task_results("TASK-091")) != 1:
                        raise RuntimeError("canonical T genuine INTEGER callback missing")
                    db.insert_job(JobRecord(id="JOB-001", task_id="TASK-091", agent_name="case_manager",
                        title="Inert interrupted residue", rationale="No process or native registration",
                        script_text=":", interpreter=JobInterpreter.BASH, status=JobStatus.RUNNING,
                        persistent=True, created_at=stamp.isoformat()))
                    if any(row.job_id == "JOB-001" for row in _INFLIGHT.values()):
                        raise RuntimeError("T residue unexpectedly has native ownership")
                    checks.details["T_seed"] = {"task": "TASK-091", "agent": "case_manager", "origin": origin,
                        "session": recovery, "result": dict(result), "job": "JOB-001",
                        "native_provider_provenance": False}
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
    checks.details["seed_snapshot_rows"] = totals  # surviving seed rows only; plan above is cumulative
    for table, maximum in (("tasks", 64), ("threads", 8), ("thread_messages", 64),
                           ("thread_invocations", 16), ("jobs", 8)):
        if totals.get(table, 0) > maximum:
            raise RuntimeError("original fixture cap exceeded: " + table)
    v2_rows = sum(count for table, count in totals.items()
                  if history_table(table))
    if sum(totals.values()) > 512:
        raise RuntimeError("original canonical causal fixture row cap exceeded")
    if v2_rows > fit["limits"]["history_controls"]:
        raise RuntimeError("selected v2/history fixture row cap exceeded")
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
            "SOURCE ONLY / NOT RUN (accepted matrix lower-boundary limitation): "
            + TASK_SOURCE_LIMITATIONS[name]["caller"] + "; requires "
            + TASK_SOURCE_LIMITATIONS[name]["prerequisite"]
            + "; held prepare cannot issue a fresh native owner; no surrogate credit"))


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
        checks.missing(name + "_ready_original_owner", "SOURCE ONLY / NOT RUN: "
            + TASK_SOURCE_LIMITATIONS[name]["caller"] + "; requires "
            + TASK_SOURCE_LIMITATIONS[name]["prerequisite"]
            + "; _set_active_locked/publish_recovery_session are separate uncredited dependencies")


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
        self.startup_tails, self.startup_tail_inputs = [], []

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


class ReaderObservation:
    """Pass-through observations for ONLY this mounted pool's native readers."""

    def __init__(self, wire_observation: Any) -> None:
        self.wire = wire_observation
        self.current = ContextVar("startup_reader_selection", default=None)
        self.native = threading.local()
        self.lock = threading.Lock()
        self.rows = []
        self.owners = {}
        self.sql_release = {name: threading.Event() for name in ("close", "deadline")}
        self.close_release = threading.Event()
        self.pool = None
        self.original_connect = sqlite3.connect

    def event(self, name: str, boundary: str) -> None:
        with self.lock:
            if len(self.rows) >= 192:
                raise RuntimeError("selected reader observation cap")
            self.rows.append({"selection": name, "boundary": boundary,
                              "thread": threading.get_ident(), "at": time.monotonic()})

    def seen(self, name: str, boundary: str) -> list[dict[str, Any]]:
        with self.lock:
            return [dict(row) for row in self.rows
                    if row["selection"] == name and row["boundary"] == boundary]

    def start(self, pool: Any) -> None:
        self.pool = pool
        self.read_uris = {Path(org.db.db_path).absolute().as_uri() + "?mode=ro" for org in pool.state.orgs.values()}
        self.had_submit_override = "submit" in pool._executor.__dict__
        self.original_submit = pool._executor.submit
        pool._executor.submit = self.submit
        sqlite3.connect = self.connect

    def submit(self, function: Any, lease: Any, projection: Any) -> Any:
        wire = self.wire.current.get()
        name = self.current.get() or (None if wire is None else wire.get("case"))
        if name not in ("close", "deadline", "queued_cancel", "queued_deadline"):
            self.event(name or "other", "other_submit")
            return self.original_submit(function, lease, projection)
        owner = {"lease": lease, "waiter": asyncio.current_task(), "deadline": lease.budget.deadline}
        self.owners[name] = owner
        self.event(name, "submit")

        def native() -> bytes:
            self.native.selection = (name, lease)
            self.event(name, "worker_entry")
            try:
                return function(lease, projection)
            finally:
                self.event(name, "worker_exit")
                del self.native.selection

        future = self.original_submit(native)
        owner["future"] = future
        future.add_done_callback(lambda done: self.event(name, "future_cancelled" if done.cancelled() else "future_done"))
        lease.retired.add_done_callback(lambda done: self.event(name, "retired"))
        return future

    def connect(self, database: str, *args: Any, **kwargs: Any) -> Any:
        selected = getattr(self.native, "selection", None)
        if selected is None:
            if database in self.read_uris and kwargs.get("uri"):
                self.event("other", "other_open")
            return self.original_connect(database, *args, **kwargs)
        name, lease = selected
        if database != lease.references[3].absolute().as_uri() + "?mode=ro" or not kwargs.get("uri"):
            raise AssertionError("selected reader did not open its original admitted RO database")
        conn = self.original_connect(database, *args, **kwargs)
        self.event(name, "open")
        observer = self

        class Connection:
            def __getattr__(self, key: str) -> Any:
                return getattr(conn, key)

            @property
            def row_factory(self) -> Any:
                return conn.row_factory

            @row_factory.setter
            def row_factory(self, value: Any) -> None:
                conn.row_factory = value

            def execute(self, sql: str, parameters: Any = ()) -> sqlite3.Cursor:
                # Delay an ORIGINAL auxiliary SQL statement, after the canonical
                # thread/message reads. Neither its cursor nor DTO is replaced.
                if "FROM thread_invocations" in sql and name in observer.sql_release:
                    observer.event(name, "aux_sql_wait")
                    if not observer.sql_release[name].wait(3):
                        raise TimeoutError("selected original SQL barrier expired")
                    observer.event(name, "aux_sql_forward")
                result = conn.execute(sql, parameters)
                observer.event(name, "sql_forwarded")
                return result

            def close(self) -> None:
                observer.event(name, "close_enter")
                if name == "close" and not observer.close_release.wait(3):
                    raise TimeoutError("selected original close barrier expired")
                conn.close()  # Original connection, on its actual opening worker.
                observer.event(name, "close_return")

        return Connection()

    def release(self) -> None:
        for permit in self.sql_release.values():
            permit.set()
        self.close_release.set()

    def restore(self) -> None:
        sqlite3.connect = self.original_connect
        if self.had_submit_override:
            self.pool._executor.submit = self.original_submit
        else:
            del self.pool._executor.submit


async def reader_cases(checks: Checks, state: Any, client: Any, observer: ReaderObservation) -> None:
    """Zero-row C04/C08/C10 subset inside the ONE original recovery hold."""
    from runtime.daemon.routes.threads import _project_thread_messages
    from runtime.daemon.startup_reads import StartupReadUnavailable
    pool = state._startup_reads
    baseline = await asyncio.gather(*(client.get(f"/api/v1/orgs/{slug}/threads/THR-001/messages?limit=10")
                                     for slug in ("fixture-a", "fixture-b")))
    for slug, response in zip(("fixture-a", "fixture-b"), baseline):
        checks.require("C10_reader_" + slug + "_original_transcript_before",
                       response.status_code == 200 and MESSAGE in response.text)
    before = pool.snapshot()
    attempts = []
    observer.start(pool)

    async def until(predicate: Any, deadline: float) -> None:
        while not predicate():
            if time.monotonic() >= deadline:
                raise TimeoutError("selected original reader boundary not observed")
            await asyncio.sleep(.001)

    async def controlled(name: str, slug: str) -> Any:
        marker = observer.current.set(name)
        try:
            # Actual coroutine waiter, original admission and canonical SQL/DTO.
            # A cancelled socket alone would not establish Task cancellation.
            return await pool.read(state.orgs[slug],
                lambda db, budget: _project_thread_messages(db, "THR-001", 0, 10))
        finally:
            observer.current.reset(marker)

    async def http(name: str, slug: str) -> Any:
        # ONLY the two intentional 2s-deadline observers get <=3s transport.
        return await asyncio.wait_for(client.get(f"/api/v1/orgs/{slug}/threads/THR-001/messages?limit=10",
                          headers={"X-Startup-Case": name}, timeout=3), 3)

    async def cancel(name: str) -> None:
        owner = observer.owners[name]
        owner["waiter"].cancel()
        done, pending = await asyncio.wait((owner["waiter"],), timeout=2)
        if pending:
            raise TimeoutError("actual reader waiter did not acknowledge cancellation")
        result = (await asyncio.gather(*done, return_exceptions=True))[0]
        checks.require("C08_reader_" + name + "_actual_waiter_cancelled", isinstance(result, asyncio.CancelledError))

    try:
        attempts.append(asyncio.create_task(controlled("close", "fixture-a")))
        attempts.append(asyncio.create_task(http("deadline", "fixture-b")))
        await until(lambda: all(observer.seen(name, "aux_sql_wait") for name in ("close", "deadline")), time.monotonic() + 2)
        attempts.append(asyncio.create_task(controlled("queued_cancel", "fixture-b")))
        attempts.append(asyncio.create_task(http("queued_deadline", "fixture-a")))
        await until(lambda: len(observer.owners) == 4, time.monotonic() + 2)
        owners = observer.owners
        running = ("close", "deadline")
        queued = ("queued_cancel", "queued_deadline")
        checks.require("C04_reader_two_running_two_queued", all(owners[n]["future"].running() for n in running)
            and all(not owners[n]["future"].running() and not owners[n]["future"].done() for n in queued)
            and all(observer.seen(n, "open") and observer.seen(n, "aux_sql_wait") for n in running)
            and all(not observer.seen(n, "worker_entry") and not observer.seen(n, "open") for n in queued))
        checks.require("C04_reader_actual_admission_bounds", pool.snapshot()["active"] == 2
            and pool.snapshot()["queued"] == 2 and pool.snapshot()["admitted"] - before["admitted"] == 4)
        selected_before = (len(owners), len(observer.rows), pool.snapshot())
        busy = await client.get("/api/v1/orgs/fixture-a/threads/THR-001/messages?limit=10",
                                headers={"X-Startup-Case": "fifth_busy"})
        checks.require("C04_reader_fifth_busy", (busy.status_code, busy.json().get("detail", {}).get("code")),
                       (503, "startup_reader_busy"))
        checks.require("C04_reader_fifth_no_submission_or_connection", len(owners) == selected_before[0]
            and len(observer.rows) == selected_before[1]
            and pool.snapshot()["admitted"] == selected_before[2]["admitted"]
            and pool.snapshot()["refused"] == selected_before[2]["refused"] + 1)
        await cancel("queued_cancel")
        q = owners["queued_cancel"]
        await until(lambda: q["lease"].retired.done(), time.monotonic() + 2)
        checks.require("C08_reader_queued_cancel_no_native_work", q["future"].cancelled()
            and observer.seen("queued_cancel", "future_cancelled") and observer.seen("queued_cancel", "retired")
            and not observer.seen("queued_cancel", "worker_entry") and not observer.seen("queued_cancel", "open")
            and not q["lease"].started and q["lease"].connection is None)
        observer.sql_release["close"].set()
        await until(lambda: observer.seen("close", "close_enter"), time.monotonic() + 2)
        await cancel("close")
        c = owners["close"]
        checks.require("C08_reader_running_cancel_retains_owner", c["future"].running()
            and not c["lease"].retired.done() and not c["lease"].close_proven
            and c["lease"].connection is not None and not observer.seen("close", "close_return")
            and not observer.seen("queued_deadline", "worker_entry")
            and pool.snapshot()["active"] == 2 and pool.snapshot()["queued"] == 1)
        checks.require("C10_reader_queue_original_deadline", owners["queued_deadline"]["lease"].budget.deadline
            == owners["queued_deadline"]["deadline"] and not observer.seen("queued_deadline", "worker_entry"))
        # Release both expired selections together. The queued original must
        # refuse without opening; the running original forwards its SAME SQL.
        expired = max(owners[n]["deadline"] for n in ("deadline", "queued_deadline"))
        await until(lambda: time.monotonic() > expired + .02, expired + .5)
        observer.sql_release["deadline"].set()
        deadline_results = await asyncio.gather(attempts[1], attempts[3])
        for name, response in zip(("deadline", "queued_deadline"), deadline_results):
            checks.require("C10_reader_" + name + "_503", (response.status_code, response.json().get("detail", {}).get("code")),
                           (503, "startup_reader_deadline"))
            lease = owners[name]["lease"]
            checks.require("C10_reader_" + name + "_original_retirement", lease.retired.done()
                and owners[name]["future"].done() and observer.seen(name, "retired")
                and isinstance(lease.primary_error, StartupReadUnavailable)
                and lease.primary_error.code == "startup_reader_deadline"
                and lease.budget.deadline == owners[name]["deadline"])
        checks.require("C10_reader_queued_deadline_before_open", not observer.seen("queued_deadline", "open")
            and observer.seen("queued_deadline", "worker_entry")[0]["at"] > owners["queued_deadline"]["deadline"])
        observer.close_release.set()
        await until(lambda: c["lease"].retired.done(), time.monotonic() + 2)
        checks.require("C08_reader_close_then_future_then_retirement", c["future"].done()
            and c["lease"].close_proven and c["lease"].connection is None
            and observer.seen("close", "close_return")[0]["at"] <= observer.seen("close", "future_done")[0]["at"]
            <= observer.seen("close", "retired")[0]["at"])
        for name in running:
            threads = [row["thread"] for boundary in ("open", "sql_forwarded", "close_enter", "close_return")
                       for row in observer.seen(name, boundary)]
            checks.require("C08_reader_" + name + "_same_worker_close", len(set(threads)) == 1
                and threads[0] != checks.details["request_loop_thread"] and owners[name]["lease"].close_error is None)
        checks.require("C08_reader_running_cancel_error_preserved",
            isinstance(c["future"].exception(), StartupReadUnavailable)
            and c["future"].exception().code == "startup_reader_unavailable"
            and c["lease"].primary_error is None and c["lease"].close_error is None)
        checks.require("C10_reader_delayed_original_sql_forwarded", bool(observer.seen("deadline", "aux_sql_forward"))
            and observer.seen("deadline", "aux_sql_forward")[0]["at"] > owners["deadline"]["deadline"]
            and observer.seen("deadline", "close_return")[0]["at"] > owners["deadline"]["deadline"])
        active = leases = peak_active = peak_leases = 0
        for row in observer.rows:
            active += int(row["boundary"] == "worker_entry") - int(row["boundary"] == "worker_exit")
            leases += int(row["boundary"] == "submit") - int(row["boundary"] == "retired")
            peak_active, peak_leases = max(peak_active, active), max(peak_leases, leases)
        checks.require("C04_reader_independent_native_bounds", (peak_active, peak_leases, active, leases,
            sum(row["boundary"] == "open" for row in observer.rows),
            sum(row["boundary"] == "close_return" for row in observer.rows),
            sum(row["boundary"] in ("other_submit", "other_open") for row in observer.rows)), (2, 4, 0, 0, 2, 2, 0))
        after = pool.snapshot()
        checks.require("C10_reader_exact_lifecycle_counters",
            {key: after[key] - before[key] for key in ("admitted", "started", "closed", "retired", "cancelled", "deadline", "refused")},
            {"admitted": 4, "started": 3, "closed": 3, "retired": 4, "cancelled": 2, "deadline": 2, "refused": 1})
        checks.require("C08_reader_all_selected_slots_retired", after["active"] == 0 and after["queued"] == 0)
        restored = await asyncio.gather(*(client.get(f"/api/v1/orgs/{slug}/threads/THR-001/messages?limit=10")
                                       for slug in ("fixture-a", "fixture-b")))
        for slug, response, original in zip(("fixture-a", "fixture-b"), restored, baseline):
            checks.require("C10_reader_" + slug + "_original_transcript_restored",
                           response.status_code == 200 and MESSAGE in response.text and response.json() == original.json())
    finally:
        primary = sys.exception()
        observer.release()
        try:
            for task in attempts:
                if not task.done():
                    task.cancel()
            done, pending = await asyncio.wait(attempts, timeout=2)
            if pending:
                raise TimeoutError("selected reader coroutine cleanup incomplete")
            await asyncio.gather(*done, return_exceptions=True)
            # Observe original Futures AND callback acknowledgements; no private
            # retirement, connection close or cross-thread SQLite operation.
            native = [asyncio.wrap_future(owner[key]) for owner in observer.owners.values()
                      for key in ("future",) if not owner[key].cancelled()]
            native += [asyncio.wrap_future(owner["lease"].retired) for owner in observer.owners.values()]
            await asyncio.wait_for(asyncio.gather(*native, return_exceptions=True), 3)
        except BaseException as secondary:
            checks.details["reader_cleanup_error"] = type(secondary).__name__
            if primary is None:
                raise
            primary.add_note("Selected reader cleanup also failed: " + type(secondary).__name__)
        finally:
            observer.restore()
            checks.details["reader_lifecycle"] = {"before": before, "after": pool.snapshot(),
                "events": observer.rows, "allocation": "zero new durable rows; <=5 simultaneous reads; original hold only",
                "exclusions": "full failed-close/drain/stop/restart/native-tail cases remain unimplemented"}


async def literal_cases(checks, runtime, port, token, wire, phase, entries, index):
    async def observe(entry):
        name, method, target = entry["id"], entry["method"], entry["request_target"]
        key = name + "_" + phase
        if index is None and (target.startswith("/assets/") or name in ("HX09.06", "HX10.12", "HX02", "HX03", "HX04")):
            checks.missing(key + "_content", "real isolated ordinary Web build/index/assets missing")
            return
        expected = ({"status": 503} if phase == "recovery_failed" else
                    entry["during_recovery" if phase == "recovering" else "after_ready"])
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
            content = "application/json" in media and detail.get("code") == ("daemon_recovery_failed" if phase == "recovery_failed" else "daemon_recovering")
            if phase == "recovery_failed":
                content = content and detail.get("required_action") == "New work remains paused. Send the startup error code to engineering_manager."
            else:
                content = content and bool(detail.get("required_action"))
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
        closed = phase in ("recovering", "recovery_failed") and expected["status"] == 503
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


def prepare_consumers(checks: Checks) -> dict:
    """Finite original inputs/imports, prepared before the one native hold."""
    from scripts._coding_setup_preflight import Hold
    from cli.client.startup import parse_launcher_output
    # main normally imports this SAME original exception after human preflight.
    # Fresh test-side namespace supplies only that import, never main/Coordinator
    # construction, a replacement predicate or any setup command/effect.
    setup = runpy.run_path(str(SOURCE / "scripts/setup_coding.py"),
                          run_name="startup_consumer_setup", init_globals={"Hold": Hold})
    env = dict(os.environ)
    env.update(PYTHONDONTWRITEBYTECODE="1", PYTHONPATH=str(SOURCE),
               UV_PYTHON=sys.executable, UV_PROJECT_ENVIRONMENT=sys.prefix,
               UV_NO_SYNC="1", UV_OFFLINE="1", NO_PROXY="127.0.0.1,localhost", no_proxy="127.0.0.1,localhost")
    for key in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        env.pop(key, None)
    bash, uv, curl = (shutil.which(name, path=env.get("PATH")) for name in ("bash", "uv", "curl"))
    if not all((bash, uv, curl)):
        raise ValueError("original status prerequisites unavailable; no wrapper/fallback")
    commands = {"status": [bash, str(SOURCE / "scripts/daemon.sh"), "status"],
                "web": [sys.executable, "-m", "cli.main", "web", "--no-open"],
                **{slug: [sys.executable, "-m", "cli.main", "threads", "show", "--org", slug,
                          "THR-001", "--json"] for slug in ("fixture-a", "fixture-b")}}
    checks.details["consumer_inputs"] = {
        "commands": commands, "cwd": str(SOURCE), "python": sys.executable,
        "uv": uv, "curl": curl, "environment": {k: env[k] for k in (
            "HOME", "XDG_CONFIG_HOME", "XDG_CACHE_HOME", "TMPDIR", "HAPPYRANCH_DAEMON_HOME",
            "HAPPYRANCH_RUNTIME_ROOT", "HAPPYRANCH_PROJECT_ROOT", "PYTHONPATH", "UV_PYTHON",
            "UV_PROJECT_ENVIRONMENT", "UV_NO_SYNC", "UV_OFFLINE", "PYTHONDONTWRITEBYTECODE")},
        "setup": "original run_path definitions + original Hold import from main; no main/constructor/preflight",
        "setup_sha256": hashlib.sha256((SOURCE / "scripts/setup_coding.py").read_bytes()).hexdigest(),
        "cli_sha256": hashlib.sha256((SOURCE / "cli/main.py").read_bytes()).hexdigest(),
        "status_sha256": hashlib.sha256((SOURCE / "scripts/daemon.sh").read_bytes()).hexdigest()}
    return {"commands": commands, "env": env, "setup": setup["Coordinator"].require_recovery_ready,
            "Hold": Hold, "parser": parse_launcher_output, "receipts": []}


async def consumer_cases(checks: Checks, runtime: Any, state: Any, server: Any, sock: Any,
                         client: Any, wire: list, inputs: dict, observation: EntryObservation, phase: str) -> None:
    """Only the four selected read commands, serialized on the original listener."""
    prefix = "C14_" + phase + "_"
    previous_totals = observation.totals
    observation.totals = dict.fromkeys(previous_totals, 0)
    observation.start()
    try:
        pid, address = os.getpid(), sock.getsockname()
        os.kill(pid, 0)
        checks.require(prefix + "listener_identity", server.started and not server.should_exit
            and address == ("127.0.0.1", int(client.base_url.port))
            and any(s.fileno() == sock.fileno() and s.getsockname() == address
                    for listener in server.servers for s in listener.sockets)
            and state._startup_recovery.snapshot()["serving"] is True)
        checks.require(prefix + "no_reader_or_provider_overlap", state._startup_reads.snapshot()["active"] == 0
            and all(not org._native_task_users for org in state.orgs.values()))
        before = fixture_effects(runtime, state, checks.args.root)

        def discovery() -> dict:
            # Covers actual isolated consumer homes/caches and discovery files too;
            # existing fixture_effects owns SQL/workspace/job/temp/native observations.
            files, size = {}, 0
            deadline = min(checks.started + 600, time.monotonic() + 2)
            for name in ("home", "config", "cache", "daemon"):
                for path in sorted((checks.args.root / name).rglob("*")):
                    if len(files) >= 4096 or time.monotonic() >= deadline:
                        raise ValueError("consumer discovery observation budget")
                    if path.is_symlink():
                        raise ValueError("aliased synthetic consumer metadata")
                    if path.is_file():
                        with path.open("rb") as stream:
                            raw = stream.read(4 * 1024 * 1024 - size + 1)
                        size += len(raw)
                        if size > 4 * 1024 * 1024:
                            raise ValueError("consumer discovery byte budget")
                        files[str(path)] = hashlib.sha256(raw).hexdigest()
                    elif path.is_dir():
                        files[str(path)] = "directory"
                    else:
                        raise ValueError("special synthetic consumer metadata")
            return files

        discovery_before = discovery()
        for name, command in inputs["commands"].items():
            # TWO status + SIX CLI across both phases; no retry or alternate argv.
            if any(r["name"] == name and r["phase"] == phase for r in inputs["receipts"]):
                raise ValueError("consumer command already attempted")
            remaining = 600 - (time.monotonic() - checks.started)
            if phase == "recovering":
                remaining = min(remaining, 10 - (time.monotonic() - checks.details["hold_started_monotonic"]))
            if remaining <= 0:
                raise TimeoutError("original consumer window exhausted before spawn")
            row = {"name": name, "phase": phase, "argv": command, "cwd": str(SOURCE),
                   "started_monotonic": time.monotonic(), "pid": None, "reaped": False,
                   "stdout": "", "stderr": "", "exit": None, "failure": None}
            inputs["receipts"].append(row)
            checks.details["consumer_commands"] = inputs["receipts"]
            start_wire = len(wire)
            process, pumps = None, []
            spawn = asyncio.create_task(asyncio.create_subprocess_exec(*command, cwd=SOURCE,
                env=inputs["env"], stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE, start_new_session=True))

            async def capture(stream: Any, key: str) -> None:
                raw = bytearray()
                while chunk := await stream.read(4096):
                    raw.extend(chunk)
                    row[key] = bytes(raw[:16384]).decode("utf-8", errors="replace")
                    if len(raw) > 16384:
                        raise ValueError("consumer " + key + " cap")

            try:
                async with asyncio.timeout(min(2, remaining)):
                    process = await asyncio.shield(spawn)
                    row["pid"] = process.pid
                    row["pgid"] = os.getpgid(process.pid)
                    os.kill(process.pid, 0)  # real owned start identity, never a fixture PID
                    pumps = [asyncio.create_task(capture(process.stdout, "stdout")),
                             asyncio.create_task(capture(process.stderr, "stderr"))]
                    await asyncio.gather(process.wait(), *pumps)
                    row["exit"], row["reaped"] = process.returncode, True
            except BaseException as exc:
                row["failure"] = type(exc).__name__
                # Keep the SAME spawn owner through cancellation; never lose a late
                # process handle. Terminate its actual session/group, drain/reap.
                try:
                    process = process or await asyncio.shield(spawn)
                    row["pid"] = process.pid
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    for pump in pumps:
                        if not pump.done():
                            pump.cancel()
                    await asyncio.gather(*pumps, return_exceptions=True)
                    async def discard(stream: Any) -> None:
                        while await stream.read(4096):
                            pass
                    await asyncio.wait_for(asyncio.gather(process.wait(), discard(process.stdout),
                                                          discard(process.stderr)), 2)
                    row["exit"], row["reaped"] = process.returncode, True
                except BaseException as cleanup:
                    row["cleanup_failure"] = type(cleanup).__name__
                    raise
                raise
            finally:
                row["ended_monotonic"] = time.monotonic()
                row["wall_seconds"] = row["ended_monotonic"] - row["started_monotonic"]
                row["wire"] = wire[start_wire:]
                row["end_identity"] = {"pid": None if process is None else process.pid,
                                       "returncode": None if process is None else process.returncode}
                checks.require(prefix + name + "_process", row["reaped"] and row["failure"] is None
                    and row.get("pgid") == row["pid"] and row["wall_seconds"] <= 2)
            checks.require(prefix + name + "_exit", (row["exit"], row["stderr"]), (0, ""))
            requests = row["wire"]
            expected_paths = (["/api/v1/health"] if name in ("status", "web") else
                              ["/api/v1/orgs", f"/api/v1/orgs/{name}/threads/THR-001"])
            endpoints = {"/api/v1/health": "runtime.daemon.routes.health.health",
                         "/api/v1/orgs": "runtime.daemon.routes.orgs.list_orgs",
                         f"/api/v1/orgs/{name}/threads/THR-001": "runtime.daemon.routes.threads.get_thread_endpoint"}
            checks.require(prefix + name + "_wire", [r["path"] for r in requests] == expected_paths
                and all(r["method"] == "GET" and not r["query"] and r.get("asgi_status") == 200
                        and r.get("dispatched", {}).get("endpoint") == endpoints[r["path"]]
                        and r.get("original_handler_entries", {}).get(endpoints[r["path"]], 0) > 0
                        and not r["native_entries"]
                        and not r["preparation_entries"] and (name == "status" or
                        (r["fixture_authenticated"] and r["surface"] == "cli")) for r in requests))
            if name == "status":
                expected = f"running (pid {pid}, port {address[1]})\nstartup serving=yes recovery={phase} launches={'admitted' if phase == 'ready' else 'held'}\n"
                checks.require(prefix + "status_output", row["stdout"], expected)
                parsed = inputs["parser"](row["stdout"], started=False)
                checks.require(prefix + "status_parser", parsed is not None and parsed[:2] == (pid, address[1])
                    and parsed[2].serving == "yes" and parsed[2].recovery == phase
                    and parsed[2].ready is (phase == "ready"))
                row["exit_semantics"] = "process liveness only; readiness requires original parsed observation"
            elif name == "web":
                description = ("Serving; recovery ready. New work remains subject to normal admission controls."
                    if phase == "ready" else "Recovering; threads can be read, new work is held. Observe status; do not start another daemon.")
                url = f"http://127.0.0.1:{address[1]}/"
                checks.require(prefix + "web_output", row["stdout"].splitlines(), [description,
                    "happyranch web → " + url,
                    f"remote access: ssh -L {address[1]}:127.0.0.1:{address[1]} {socket.gethostname()}",
                    "               then open " + url + " locally"])
            else:
                content = response_json(row["stdout"])
                checks.require(prefix + name + "_transcript", content_contract("H156", {
                    "headers": {"content-type": "application/json"}, "body": row["stdout"]}, runtime, "", phase)
                    and content.get("thread_id") == "THR-001"
                    and content.get("messages", [{}])[0].get("body_markdown") == MESSAGE)
        health = await client.get("/api/v1/health")
        ready = await client.get("/api/v1/health/ready")
        if len(health.content) > 16384 or len(ready.content) > 16384:
            raise ValueError("original consumer health body cap")
        body, ready_body = health.json(), ready.json()
        startup = body.get("startup", {})
        checks.require(prefix + "fresh_health", health.status_code == 200 and body.get("status") == "ok"
            and body.get("active_runtime") == str(runtime.root) and startup.get("state") == phase
            and startup.get("serving") is True and startup.get("recovery_ready") is (phase == "ready")
            and startup.get("stopping") is False and startup.get("ready_commits") == (1 if phase == "ready" else 0))
        checks.require(prefix + "fresh_ready", ready.status_code == (200 if phase == "ready" else 503)
            and all(ready_body.get("startup", {}).get(k) == startup.get(k)
                    for k in ("state", "serving", "recovery_ready", "stopping", "ready_commits")))
        # Feed original fresh listener bytes to the original static admission only.
        try:
            result = inputs["setup"](body)
        except inputs["Hold"] as exc:
            result = {"category": exc.category, "message": str(exc)}
        checks.details.setdefault("consumer_setup", {})[phase] = {
            "function": "scripts.setup_coding.Coordinator.require_recovery_ready",
            "health_body": body, "ready_status": ready.status_code, "result": result}
        checks.require(prefix + "setup_original_admission", result is None if phase == "ready" else
            isinstance(result, dict) and result["category"] == "serving_observation_unavailable"
            and "no second start/import" in result["message"] and "do not start another daemon" in result["message"])
        checks.require(prefix + "rows_files_owners_unchanged", fixture_effects(runtime, state, checks.args.root), before)
        checks.require(prefix + "discovery_unchanged", discovery(), discovery_before)
    finally:
        checks.details.setdefault("consumer_original_entry_counts", {})[phase] = dict(observation.totals)
        observation.close()
        observation.totals = previous_totals


def observe_wire(app, token, wire, observation):
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
                   "fixture_authenticated": dict(scope["headers"]).get(b"authorization") == ("Bearer " + token).encode(),
                   "surface": dict(scope["headers"]).get(b"x-happyranch-surface", b"").decode("ascii"),
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


def store_paths(runtime):
    return {slug: {"path": str(root / "happyranch.db"),
                   "device": (root / "happyranch.db").stat().st_dev,
                   "inode": (root / "happyranch.db").stat().st_ino}
            for slug, root in runtime.iter_org_roots()}


def immutable_records(runtime):
    # These known seed records must not change in either P0 or failed serving.
    selected = ("tasks", "task_results", "threads", "thread_messages",
                "authority_policy_v2_attempts", "authority_policy_v2_session_bindings",
                "authority_policy_v2_releases", "authority_policy_v2_activations")
    return {slug: {table: digest for table, digest in tables.items() if table in selected}
            for slug, tables in database_observation(runtime).items()}


def failed_counts(runtime):
    result = {}
    for slug, root in runtime.iter_org_roots():
        conn = sqlite3.connect((root / "happyranch.db").as_uri() + "?mode=ro", uri=True, timeout=.05)
        try:
            conn.execute("PRAGMA query_only=ON")
            result[slug] = {table: conn.execute('SELECT COUNT(*) FROM "' + table + '"').fetchone()[0]
                for table in ("audit_log", "org_settings", "tasks", "task_results", "threads", "thread_messages",
                              "workflow_authority_pointers", "workflow_publication_journals",
                              "workflow_publication_leases", "identity_name_schema", "identity_name_owners",
                              "identity_name_claims", "authority_policy_active_selector",
                              "authority_policy_active_selector_history", "authority_policy_v2_control_audit")}
        finally:
            conn.close()
    return result


class FailureObservation(EntryObservation):
    """Original bodies and retained handles; only the selected SQLite READ is denied."""

    def __init__(self, app, state, checks):
        super().__init__(app)
        from runtime.infrastructure.db.authority_v2_attempts import _V2StartupReads
        from runtime.orchestrator import authority, run_step
        from runtime.daemon import app as app_module
        from runtime.daemon.startup_reads import StartupReads
        from runtime.daemon.startup_recovery import StartupRecovery
        from runtime.daemon.org_state import OrgState
        from runtime.infrastructure.database import Database
        from concurrent.futures import ThreadPoolExecutor
        self.state, self.checks = state, checks
        self.reader_class, self.authority_module = _V2StartupReads, authority
        self.original_discover = _V2StartupReads.discover
        self.original_authority = authority.refuse_authority_policy_v2_pre_final_on_startup
        self.local = threading.local()
        self.events, self.denials, self.leases, self.sweeps = [], [], [], []
        self.errors, self.restoration_errors = [], []
        self.connection_closes = {}
        self.reader_handles = {}
        self.authority_returns, self.reader_checks = [], []
        self.reader = None
        self.discoveries = 0
        self.restored = False
        self.native_threads = []
        self.watched = {fn.__code__: name for name, fn in (
            ("prepare", app_module._prepare_startup_consumers),
            ("submit", StartupRecovery.submit), ("negative", run_step._StartupSweepOwner.negative),
            ("finalize", run_step._StartupSweepOwner.finalize),
            ("check", _V2StartupReads.check), ("read_worker", StartupReads._read),
            ("retire", StartupReads._retire), ("drain", StartupReads.drain),
            ("recovery_close", StartupRecovery.close), ("org_close", OrgState.close),
            ("db_close", Database.close.__wrapped__), ("executor_shutdown", ThreadPoolExecutor.shutdown))}
        self.originals = (self.original_discover, self.original_authority, sqlite3.connect, Database.__init__)
        self.codes.update({fn.__code__: "preparation" for fn in (app_module._prepare_startup_consumers,)})
        for owner, names in ((state.queue, ("enqueue", "put_nowait", "enqueue_if_absent", "_consume", "_submit", "_record_dequeue")),):
            self.codes.update({getattr(owner, name).__func__.__code__: "publication" for name in names})
        self.totals["publication"] = 0

    def event(self, name, **values):
        if len(self.events) >= 512:
            raise RuntimeError("failed epoch observation cap")
        self.events.append({"name": name, "thread": threading.get_ident(), "at": time.monotonic(), **values})

    def profile(self, frame, event, arg):
        super().profile(frame, event, arg)
        if not self.active:
            return
        if event in ("c_return", "c_exception") and getattr(arg, "__name__", None) == "close":
            conn = getattr(arg, "__self__", None)
            if isinstance(conn, sqlite3.Connection):
                if frame.f_code in self.watched and self.watched[frame.f_code] == "read_worker":
                    lease = frame.f_locals["lease"]
                    self.reader_handles[id(lease)] = {"connection": conn, "thread": threading.get_ident(),
                        "closed": event == "c_return", "at": time.monotonic()}
                self.connection_closes[id(conn)] = (threading.get_ident(), event == "c_return", time.monotonic())
        name = self.watched.get(frame.f_code)
        if name is None or event not in ("call", "return"):
            return
        owner = frame.f_locals.get("self")
        self.event(name + "_" + event, owner=id(owner), lease=id(frame.f_locals.get("lease")))
        if name == "read_worker" and event == "call":
            lease = frame.f_locals["lease"]
            if lease not in self.leases:
                self.leases.append(lease)
        if name == "check" and event == "return" and owner is self.reader:
            self.reader_checks.append((id(owner.conn), owner.conn.in_transaction, owner.unsafe is None, bool(self.errors)))
        if name == "negative" and event == "call" and owner not in self.sweeps:
            self.sweeps.append(owner)
        if name == "executor_shutdown" and event == "return":
            self.event("executor_joined", owner=id(owner), shutdown=owner._shutdown,
                       threads_alive=any(thread.is_alive() for thread in owner._threads))

    def install(self, discover):
        # The pinned plain constructor plus a complete setter inventory establishes
        # known-original None. No unsupported callback getter or trial setter.
        from runtime.infrastructure.database import Database
        source = SOURCE / "runtime/infrastructure/database.py"
        if (hashlib.sha256(source.read_bytes()).hexdigest() !=
                "618a751c120e4c36c2f00c6c42c8d9d45453f42e05785438b2b10701e7e371d6"
                or Database.__init__.__code__.co_filename != str(source)
                or Database.__init__.__qualname__ != "Database.__init__"
                or sqlite3.connect is not self.originals[2]):
            raise RuntimeError("original Database constructor/connection provenance changed")
        import ast
        setters = []
        for directory in (SOURCE / "runtime", SOURCE / "scripts"):
            for path in sorted(directory.rglob("*.py")):
                tree = ast.parse(path.read_bytes(), filename=str(path))
                for node in ast.walk(tree):
                    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "set_authorizer":
                        setters.append((path.relative_to(SOURCE).as_posix(), node.lineno))
        own_setters = [row for row in setters if row[0] == "scripts/verify_startup_recovery.py"]
        if setters != own_setters or len(own_setters) != 4:
            raise RuntimeError("unknown SQLite authorizer owner; no callback replacement")
        self.checks.details["failed_authorizer_provenance"] = {
            "constructor_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
            "setters": setters, "original_callback": "known None from plain pinned constructor and closed observer inventory"}
        if (self.original_discover.__module__ != "runtime.infrastructure.db.authority_v2_attempts"
                or self.original_discover.__qualname__ != "_V2StartupReads.discover"
                or self.original_authority.__module__ != "runtime.orchestrator.authority"
                or self.original_authority.__qualname__ != "refuse_authority_policy_v2_pre_final_on_startup"):
            raise RuntimeError("unknown discovery observer owner")
        self.reader_class.discover = discover
        self.authority_module.refuse_authority_policy_v2_pre_final_on_startup = self.authority
        self.start()

    def authority(self, db, orchestrator=None):
        if db is not self.state.orgs["fixture-a"].db:
            return self.original_authority(db, orchestrator=orchestrator)
        if getattr(self.local, "authority", None) is not None:
            raise RuntimeError("nested authority selection")
        self.local.authority = (db, orchestrator)
        try:
            value = self.original_authority(db, orchestrator=orchestrator)
            # This statement is reached only for an ORIGINAL NORMAL return.
            self.authority_returns.append(value is None)
            self.event("authority_normal_return", is_none=value is None)
            return value
        finally:
            del self.local.authority

    def selected_discover(self, reads, *args, **kwargs):
        selected = getattr(self.local, "authority", None)
        if selected is None:
            return self.original_discover(reads, *args, **kwargs)
        db, orch = selected
        gate = self.state._startup_recovery
        thread = threading.get_ident()
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            native = True
        else:
            native = False
        if (self.discoveries or not native or gate._native_thread != thread
                or orch is not self.state.orgs["fixture-a"].orchestrator
                or reads.db is not db or reads.conn is not db._conn
                or type(reads.conn) is not sqlite3.Connection or reads.caller_owned
                or sys._getframe(2).f_code is not self.original_authority.__code__):
            raise RuntimeError("selected original authority reader/owner changed")
        self.discoveries += 1
        self.reader = reads
        self.native_threads.append(thread)
        conn = reads.conn
        with db._lock:
            if conn.in_transaction or reads.unsafe is not None:
                raise RuntimeError("original selected transaction unsafe")
            self.event("discover_original", reader=id(reads), db=id(db), connection=id(conn), recovery=id(gate))
            def authorize(action, table, column, database, trigger):
                if action == sqlite3.SQLITE_READ and database == "main" and table == "authority_policy_v2_attempts":
                    if len(self.denials) < 8:
                        self.denials.append({"action": action, "table": table, "database": database,
                                             "thread": threading.get_ident(), "connection": id(conn)})
                    return sqlite3.SQLITE_DENY
                return sqlite3.SQLITE_OK
            primary = None
            installed = False
            try:
                conn.set_authorizer(authorize)
                installed = True
                return self.original_discover(reads, *args, **kwargs)
            except BaseException as exc:
                primary = exc
                self.errors.append(exc)  # Retain object/cause/traceback, rethrow unchanged.
                self.event("discover_original_error", kind=type(exc).__name__)
                raise
            finally:
                if installed:
                    try:
                        conn.set_authorizer(None)  # Restore ONLY established original None.
                        self.restored = True
                        self.event("authorizer_restored", clean=not conn.in_transaction and reads.unsafe is None)
                    except BaseException as exc:
                        self.restoration_errors.append(exc)
                        if primary is None:
                            raise
                        primary.add_note("Selected authorizer restoration failed: " + type(exc).__name__)

    def restore(self):
        if self.active:
            self.close()
        self.reader_class.discover = self.original_discover
        self.authority_module.refuse_authority_policy_v2_pre_final_on_startup = self.original_authority
        return (self.reader_class.discover is self.original_discover
                and self.authority_module.refuse_authority_policy_v2_pre_final_on_startup is self.original_authority
                and sqlite3.connect is self.originals[2]
                and sys.getprofile() is None and threading.getprofile() is None)


class SelectedObservation(FailureObservation):
    """N/T/S pass-through owners; never supply a result, acknowledgement or eligibility."""

    def __init__(self, app, state, checks, group):
        super().__init__(app, state, checks)
        from runtime.orchestrator import run_step
        from runtime.orchestrator.orchestrator import Orchestrator
        from runtime.daemon.startup_recovery import StartupRecovery
        from runtime.daemon.startup_publication import StartupPublications
        from runtime.infrastructure.database import Database
        from runtime.daemon import jobs_runner, app as app_module
        from runtime.workflows.recovery import classify_task
        self.group, self.run_step = group, run_step
        self.entered, self.release = threading.Event(), threading.Event()
        self.binding_completed = threading.Event()
        self.binding, self.transport_error = None, None
        self.completion_reads, self.failures, self.tail_handles = [], [], []
        self.completion_callers = []
        self.classifications, self.dispatch_kinds = [], []
        self.merges, self.final_batches, self.reconciles, self.cleanups, self.reports = [], [], [], [], []
        self.terminations, self.kill_calls, self.finalized = [], [], []
        self.first_effect_counts = {}
        self.original_binder = Orchestrator.bind_thread_queue_references
        self.original_wait = StartupPublications._wait_future
        self.original_handoff = Database.handoff_consumed_task_completion_recovery_parent_effect
        self.patch_owners = Orchestrator, StartupPublications, Database
        extra = (
            ("sweep_init", run_step._StartupSweepOwner.__init__),
            ("sweep_fail", run_step._StartupSweepOwner.fail),
            ("merge", run_step._StartupSweepOwner.merge),
            ("kill", run_step._kill_jobs_for_terminating_task),
            ("late_finally", run_step._await_root_worktree_batch),
            ("batch", run_step._root_worktree_batch), ("batch_impl", run_step._root_worktree_batch_impl),
            ("reconcile", run_step._root_worktree_reconcile),
            ("cleanup", run_step._await_worktree_cleanup),
            ("terminate", jobs_runner.terminate_jobs_for_task),
            ("audit", Database.insert_audit_log.__wrapped__),
            ("audit", Database.insert_audit_log_uncommitted.__wrapped__),
            ("report", Database.admit_terminal_worktree_root),
            ("report", Database.append_terminal_worktree_audit),
            ("shutdown", app_module._shutdown_lifespan),
            ("startup_drain", state._drain_startup.__func__),
            ("admission_stop", state._stop_runner_admission.__func__),
            ("classification", classify_task),
            ("dispatch_kind", Database.authority_policy_v2_completion_dispatch_context.__wrapped__),
            ("gate_fail", StartupRecovery.fail),
        )
        self.watched.update({fn.__code__: name for name, fn in extra})
        self.completed_code = next(code for code in StartupRecovery.bind_returned_references.__code__.co_consts
                                   if getattr(code, "co_name", None) == "completed")

    def profile(self, frame, event, arg):
        super().profile(frame, event, arg)
        if not self.active:
            return
        values = frame.f_locals
        owner = values.get("self")
        name = self.watched.get(frame.f_code)
        delivery = self.run_step._ROOT_WORKTREE_OWNER_EVENTS.get()
        if name == "dispatch_kind" and event == "return" and values.get("root_task_id") == "TASK-091":
            self.dispatch_kinds.append(getattr(arg, "kind", None))
        if name == "classification" and event == "return" and values.get("task_id") == "TASK-091":
            self.classifications.append(getattr(arg, "kind", None))
        if name == "sweep_init" and event == "return" and owner.orch._startup_recovery is self.state._startup_recovery:
            if owner not in self.sweeps:
                self.sweeps.append(owner)
        if name == "sweep_fail" and event == "call" and owner in self.sweeps:
            self.first_effect_counts.setdefault(id(owner), owner.counts["effects_started"])
            self.failures.append((owner, values["cause"]))
        if name == "gate_fail" and event == "call" and owner is self.state._startup_recovery:
            self.event("gate_cause", code=values["code"], cause=id(values["cause"]))
        if frame.f_code is self.completed_code and event == "return" and values.get("binding") is self.binding:
            self.event("binding_callback_return", binding=id(self.binding), error=id(self.binding.error))
            self.binding_completed.set()
        if (frame.f_code.co_name == "run" and frame.f_code.co_qualname.startswith("_StartupSweepOwner.start_tail.")
                and event == "call"):
            sweep = values["self"]
            if sweep in self.sweeps:
                thread = threading.current_thread()
                with sweep._lock:
                    future = sweep._tails.get(thread)
                if future is None:
                    raise RuntimeError("actual retained tail reservation missing")
                self.tail_handles.append((sweep, thread, future))
                self.event("tail_reserved", owner=id(sweep), tail=id(thread), future=id(future))
        if name == "kill" and event == "call" and values.get("orch") in [sweep.orch for sweep in self.sweeps]:
            self.kill_calls.append({key: values.get(key) for key in
                                   ("orch", "task_id", "recovery_owner", "recovery_job_ids", "root_transitions")})
        if name == "terminate" and event == "return" and values.get("task_id") == "TASK-091" and isinstance(arg, list):
            self.terminations.append((threading.get_ident(), list(arg)))
        if name in ("late_finally", "merge") and event == "call" and delivery is not None:
            sweep = delivery.get("startup")
            if sweep in self.sweeps and values.get("orch", sweep.orch) is sweep.orch:
                self.merges.append({"boundary": name, "sweep": sweep, "thread": threading.get_ident(),
                    "delivery": delivery, "first": sweep._first,
                    "transitions": tuple(values.get("transitions", ())),
                    "teardown_incomplete": values.get("teardown_incomplete"), "at": time.monotonic()})
        if name in ("batch", "batch_impl", "reconcile", "cleanup") and event == "call":
            orch = values.get("orch")
            # _await_worktree_cleanup receives orch in args. Count the actual
            # executor boundary as well as finalizer-origin calls after reset.
            if name == "cleanup":
                args = values.get("args", ())
                orch = args[0] if args else None
            if orch in [sweep.orch for sweep in self.sweeps]:
                row = {"name": name, "orch": orch, "delivery": delivery, "at": time.monotonic()}
                if name == "reconcile":
                    self.reconciles.append(row)
                elif name == "cleanup":
                    self.cleanups.append(row)
                elif delivery is None or delivery.get("startup") not in self.sweeps:
                    self.final_batches.append(row)
        if event == "call" and (name == "report" or name == "audit" and values.get("action", "").startswith("root_worktree")):
            self.reports.append((owner, values.get("action")))
        if name == "finalize" and event == "return" and owner in self.sweeps:
            self.finalized.append(owner)

    def selected_discover(self, reads, *args, **kwargs):
        if self.group != "S" or getattr(self.local, "authority", None) is None:
            return self.original_discover(reads, *args, **kwargs)
        db, orch = self.local.authority
        gate = self.state._startup_recovery
        sweep = gate._sweep_owner
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            native = True
        else:
            native = False
        if (self.entered.is_set() or not native or threading.get_ident() != gate._native_thread
                or reads.db is not db or reads.conn is not db._conn or reads.caller_owned
                or orch is not self.state.orgs["fixture-a"].orchestrator
                or sweep is None or self.run_step._ROOT_WORKTREE_OWNER_EVENTS.get() is not sweep.delivery
                or db._lock._is_owned() or db._conn.in_transaction or sweep._lock._is_owned()
                or gate._lock.locked() or self.state.orgs["fixture-a"].workflow_authority._publisher_lock._is_owned()):
            raise RuntimeError("S original unlocked discovery owner unavailable")
        self.event("S_discovery_held", owner=id(sweep), native=id(gate._native))
        self.entered.set()
        remaining = min(3, self.checks.started + 600 - time.monotonic())
        if remaining <= 0 or not self.release.wait(remaining):
            raise TimeoutError("S original discovery observation expired")
        self.event("S_discovery_released")
        return self.original_discover(reads, *args, **kwargs)

    def binder(self, orch, thread_queue, loop):
        value = self.original_binder(orch, thread_queue, loop)
        if self.group != "N" or orch is not self.state.orgs["fixture-b"].orchestrator:
            return value
        gate = self.state._startup_recovery
        binding = gate._binding
        if (binding is None or self.binding is not None or binding.task is not asyncio.current_task()
                or binding.task.get_coro() is not binding.coroutine or asyncio.get_running_loop() is not gate._loop
                or loop is not gate._loop or orch._thread_queue is not thread_queue or orch._main_loop is not loop
                or gate._lock.locked() or any(org.db._lock._is_owned()
                    or org.workflow_authority._publisher_lock._is_owned() for org in self.state.orgs.values())):
            raise RuntimeError("N original binding Task/coroutine/locks changed")
        self.binding = binding
        self.event("N_original_binding", binding=id(binding), task=id(binding.task), coroutine=id(binding.coroutine), loop=id(loop))
        binding.task.cancel()  # Actual Task; original bind/completed coroutines are untouched.
        return value

    def wait_future(self, publications, future):
        try:
            value = self.original_wait(publications, future)
        except BaseException as exc:
            if self.group == "N" and self.binding is not None and future is self.binding.future:
                gate = self.state._startup_recovery
                if (threading.get_ident() != gate._native_thread or gate._lock.locked()
                        or any(org.db._lock._is_owned() or org.workflow_authority._publisher_lock._is_owned()
                               for org in self.state.orgs.values())):
                    raise RuntimeError("N transport observation under wrong owner/lock") from exc
                remaining = min(3, self.checks.started + 600 - time.monotonic())
                if remaining <= 0 or not self.binding_completed.wait(remaining):
                    raise TimeoutError("N actual binding callback not observed") from exc
                self.transport_error = exc
                self.event("N_transport_exception", error=id(exc))
            raise  # Identical original exception and traceback, no manufactured completion.
        if self.binding is not None and future is self.binding.completion:
            self.completion_reads.append((value, threading.get_ident()))
            self.completion_callers.append(sys._getframe(2).f_code.co_qualname)
            self.event("N_completion_consumed", value=value, future=id(future))
        return value

    def handoff(self, db, **kwargs):
        if self.group != "T" or db is not self.state.orgs["fixture-b"].db or kwargs.get("task_id") != "TASK-091":
            return self.original_handoff(db, **kwargs)
        gate = self.state._startup_recovery
        sweep = gate._sweep_owner
        delivery = self.run_step._ROOT_WORKTREE_OWNER_EVENTS.get()
        thread = threading.current_thread()
        conn = db._conn
        seed = self.checks.details["T_seed"]
        exact = dict(task_id="TASK-091", agent=seed["agent"], recovery_session_id=seed["session"],
                     result_row_id=seed["result"]["id"], terminal_status="completed")
        if (sweep is None or delivery is not sweep.delivery or sweep.orch is not self.state.orgs["fixture-b"].orchestrator
                or thread not in sweep._tails or threading.get_ident() == sweep._body_thread
                or any(kwargs.get(key) != value for key, value in exact.items())
                or not self.terminations or self.terminations[-1] != (threading.get_ident(), [])
                or not any(row["recovery_job_ids"] == ("JOB-001",) for row in self.kill_calls)):
            raise RuntimeError("T original nonempty retained tail/context/termination not selected")
        self.event("T_parent_handoff", owner=id(sweep), delivery=id(delivery), connection=id(conn), tail=id(thread))
        with db._lock:
            if type(conn) is not sqlite3.Connection or conn.in_transaction:
                raise RuntimeError("T current-owner SELECT entered with unknown connection/transaction")
            def authorize(action, table, column, database, trigger):
                if action == sqlite3.SQLITE_READ and database == "main" and table == "task_completion_recoveries":
                    if len(self.denials) < 8:
                        self.denials.append({"thread": threading.get_ident(), "connection": id(conn),
                                            "table": table, "database": database, "action": action})
                    return sqlite3.SQLITE_DENY
                return sqlite3.SQLITE_OK
            installed, primary = False, None
            try:
                conn.set_authorizer(authorize)
                installed = True
                return self.original_handoff(db, **kwargs)
            except BaseException as exc:
                primary = exc
                self.errors.append(exc)
                self.event("T_original_select_error", error=id(exc), clean=not conn.in_transaction)
                raise
            finally:
                if installed:
                    try:
                        conn.set_authorizer(None)
                        self.restored = not conn.in_transaction
                        self.event("T_authorizer_restored", clean=self.restored)
                    except BaseException as exc:
                        self.restoration_errors.append(exc)
                        if primary is None:
                            raise
                        primary.add_note("T known-None restoration failed: " + type(exc).__name__)

    def install_selected(self):
        def discover(reads, *args, **kwargs):
            return self.selected_discover(reads, *args, **kwargs)
        # Same pinned constructor + closed FOUR setter sites (two faults/two
        # known-None restores), freshly inspected before replacing callbacks.
        super().install(discover)
        binder_owner, wait_owner, handoff_owner = self.patch_owners
        def binder(orch, thread_queue, loop):
            return self.binder(orch, thread_queue, loop)
        def wait(publications, future):
            return self.wait_future(publications, future)
        def handoff(db, **kwargs):
            return self.handoff(db, **kwargs)
        binder_owner.bind_thread_queue_references = binder
        wait_owner._wait_future = wait
        handoff_owner.handoff_consumed_task_completion_recovery_parent_effect = handoff

    def restore(self):
        binder_owner, wait_owner, handoff_owner = self.patch_owners
        binder_owner.bind_thread_queue_references = self.original_binder
        wait_owner._wait_future = self.original_wait
        handoff_owner.handoff_consumed_task_completion_recovery_parent_effect = self.original_handoff
        return (super().restore()
                and binder_owner.bind_thread_queue_references is self.original_binder
                and wait_owner._wait_future is self.original_wait
                and handoff_owner.handoff_consumed_task_completion_recovery_parent_effect is self.original_handoff)


async def selected_until(checks, predicate, *, seconds=3):
    deadline = min(checks.started + 600, time.monotonic() + seconds)
    while not predicate():
        if time.monotonic() >= deadline:
            raise TimeoutError("selected original N/T/S boundary not observed")
        await asyncio.sleep(.001)


async def stopped_reader(checks, observer, state, server, handle):
    """One healthy close-only reader; native retirement and loop resumption differ."""
    from runtime.daemon.routes.threads import _project_thread_messages
    gate, pool = state._startup_recovery, state._startup_reads
    read_observer = ReaderObservation(observer)  # Separate from the original four-reader evidence.
    read_observer.sql_release["close"].set()  # R2: auxiliary SQL MUST forward before close.
    loop, loop_task = asyncio.get_running_loop(), asyncio.current_task()
    original_thread = threading.get_ident()
    waiter = None
    before = pool.snapshot()
    projection_returns = []
    def projection(db, budget):
        value = _project_thread_messages(db, "THR-001", 0, 10)
        projection_returns.append(value)
        return value
    async def read():
        token = read_observer.current.set("close")
        try:
            return await pool.read(state.orgs["fixture-a"], projection)
        finally:
            read_observer.current.reset(token)
    read_observer.start(pool)
    checks.details["S_reader_originals"] = {"loop": id(loop), "task": id(loop_task), "thread": original_thread}
    try:
        waiter = asyncio.create_task(read())
        await selected_until(checks, lambda: bool(read_observer.seen("close", "close_enter")), seconds=1)
        actual = read_observer.owners["close"]
        lease, future = actual["lease"], actual["future"]
        checks.require("C08_S_live_handles", len(projection_returns) == 1 and MESSAGE in json.dumps(projection_returns[0], ensure_ascii=False)
            and bool(read_observer.seen("close", "aux_sql_forward")) and lease.primary_error is None
            and lease.budget.refusal is None and time.monotonic() < lease.budget.deadline
            and actual["waiter"] is waiter and lease.future is future and future.running()
            and lease in pool._leases and lease.started and lease.connection is not None
            and not lease.retired.done() and gate._native is not None and not gate._native.done())
        if any(row["status"] == "FAIL" and row["assertion"] == "C08_S_live_handles" for row in checks.rows):
            raise RuntimeError("S unhealthy earlier unwind cannot earn close-only credit")
        waiter.cancel()
        outcome = (await asyncio.gather(waiter, return_exceptions=True))[0]
        checks.require("C08_S_waiter_cancelled", isinstance(outcome, asyncio.CancelledError)
            and waiter.cancelled() and future.running() and not future.cancelled()
            and lease in pool._leases and lease.connection is not None and not lease.retired.done())
        server.should_exit = True
        await selected_until(checks, lambda: gate._stop.is_set() and any(
            row["name"] == "recovery_close_call" for row in observer.events), seconds=1)
        sweep = gate._sweep_owner
        checks.require("C08_S_stop_admission", sweep is not None and sweep._first is not None
            and str(sweep._first) == "startup_sweep_stopped" and not sweep.admission_open()
            and gate.snapshot()["ready_commits"] == 0 and not gate._latch.is_set()
            and not gate._native.done() and not handle.done() and not observer.release.is_set()
            and state._runner_shutdown and gate._publications._closed
            and any(row["name"] == "admission_stop_call" for row in observer.events)
            and any(row["name"] == "startup_drain_call" for row in observer.events))
        before_close = sum(row["name"] == "db_close_call" for row in observer.events)
        close_refusal = None
        try:
            state.orgs["fixture-a"].close()
        except RuntimeError as exc:
            close_refusal = exc
        checks.require("C08_S_close_refused", close_refusal is not None
            and str(close_refusal) == "startup_native_still_owned"
            and before_close == sum(row["name"] == "db_close_call" for row in observer.events) == 0)
        # R1: _retire is native/inline. Only THIS actual loop coroutine resuming
        # from the ORIGINAL retired Future is evidence that shutdown left it live.
        read_observer.close_release.set()
        wrapped = asyncio.wrap_future(lease.retired, loop=loop)
        await asyncio.wait_for(asyncio.shield(wrapped), timeout=max(.001, min(1, checks.started + 600 - time.monotonic())))
        observer.event("S_loop_retirement_observed", loop=id(asyncio.get_running_loop()), task=id(asyncio.current_task()),
                       retired=id(lease.retired), wrapped=id(wrapped), native=id(gate._native))
        checks.require("C08_S_loop_retirement_ack", asyncio.get_running_loop() is loop and asyncio.current_task() is loop_task
            and threading.get_ident() == original_thread and loop is gate._loop and wrapped.done()
            and lease.retired.done() and future.done() and not future.cancelled()
            and lease.close_proven and lease.close_error is None and lease.connection is None and lease not in pool._leases
            and all(len(read_observer.seen("close", name)) == 1 for name in
                    ("open", "close_enter", "close_return", "worker_exit", "future_done", "retired"))
            and len({read_observer.seen("close", name)[0]["thread"] for name in
                    ("open", "close_enter", "close_return", "worker_exit")}) == 1
            and read_observer.seen("close", "close_return")[0]["at"] <= read_observer.seen("close", "worker_exit")[0]["at"]
                <= read_observer.seen("close", "future_done")[0]["at"] <= read_observer.seen("close", "retired")[0]["at"]
            and not observer.release.is_set() and not gate._native.done() and not handle.done()
            and gate._sweep_owner is sweep and not gate._closed)
        observer.event("S_read_before_recovery_release")
        observer.release.set()  # Read retired/observed FIRST; native discovery SECOND.
        checks.details["S_reader"] = {"events": read_observer.rows, "before": before,
            "after": pool.snapshot(), "lease": id(lease), "future": id(future),
            "retired": id(lease.retired), "waiter": id(waiter), "close_refusal": str(close_refusal)}
    finally:
        # Never await shutdown or a native join while withholding a release.
        read_observer.release()
        observer.release.set()
        if waiter is not None:
            if not waiter.done():
                waiter.cancel()
            await asyncio.gather(waiter, return_exceptions=True)
        read_observer.restore()


def selected_outcomes(checks, observer, gate):
    from concurrent.futures import CancelledError as NativeCancelledError
    group = observer.group
    sweeps = observer.sweeps
    common = (gate._native is not None and gate._native.done() and not gate._native.cancelled()
        and gate._native.exception() is not None and gate.snapshot()["ready_commits"] == 0
        and not gate._latch.is_set() and not gate._consumers and not gate._preparation_complete
        and observer.totals["preparation"] == observer.totals["native"] == 0)
    if group == "N":
        binding = observer.binding
        checks.require("C05_N_original_binding", binding is not None and gate._binding is binding
            and binding.task is not None and binding.task.get_coro() is binding.coroutine and binding.task.cancelled()
            and binding.orgs_bound == 2 and binding.jobs_bound
            and all(org.orchestrator._main_loop is gate._loop and org.orchestrator._thread_queue is org.thread_queue
                    for org in observer.state.orgs.values()))
        checks.require("C05_N_completion_normal_false", binding is not None and binding.completion.done()
            and not binding.completion.cancelled() and binding.completion.exception() is None
            and binding.completion.result() is False and isinstance(binding.error, asyncio.CancelledError)
            and binding.future.cancelled() and not binding.observed)
        checks.require("C05_N_original_consumer", len(observer.completion_reads) == 1 and observer.completion_reads[0][0] is False
            and observer.completion_reads[0][1] in {row["thread"] for row in observer.events if row["name"] == "N_transport_exception"}
            and observer.completion_callers == ["StartupRecovery.bind_returned_references"]
            and isinstance(observer.transport_error, NativeCancelledError) and binding is not None and observer.transport_error is not binding.error
            and gate._native.exception() is observer.transport_error and observer.binding_completed.is_set())
        checks.require("C05_N_first_cause", binding is not None and gate._first_cause is binding.error
            and gate._error_code == "startup_binding_failed" and observer.transport_error is not gate._first_cause
            and any(cause is observer.transport_error for cause in gate._secondary)
            and any(row["name"] == "gate_cause" and row["cause"] == id(binding.error) for row in observer.events))
        checks.require("C05_N_no_ready", common)
    elif group == "T":
        db = observer.state.orgs["fixture-b"].db
        seed = checks.details["T_seed"]
        task, result = db.get_task("TASK-091"), db.get_accepted_task_completion_recovery_result(task_id="TASK-091", agent="case_manager")
        recovery = dict(db._conn.execute("SELECT * FROM task_completion_recoveries WHERE task_id=?", ("TASK-091",)).fetchone())
        job = dict(db._conn.execute("SELECT * FROM jobs WHERE id=?", ("JOB-001",)).fetchone())
        selected = [sweep for sweep in sweeps if sweep.orch is observer.state.orgs["fixture-b"].orchestrator]
        sweep = selected[0] if len(selected) == 1 else None
        cause = observer.errors[0] if len(observer.errors) == 1 else None
        tails = [row for row in observer.tail_handles if row[0] is sweep]
        checks.require("C05_T_canonical_selected", type(seed["result"]["id"]) is int
            and result is not None and dict(result) == seed["result"] and len(db.get_task_results("TASK-091")) == 1
            and recovery["accepted_result_id"] == seed["result"]["id"] and recovery["state"] == "callback_consumed"
            and recovery["accepted_result_session_id"] == recovery["recovery_session_id"] == seed["session"]
            and bool(observer.classifications) and set(observer.classifications) == {"legacy"}
            and bool(observer.dispatch_kinds) and set(observer.dispatch_kinds) == {"no_v2"}
            and any(row["recovery_job_ids"] == ("JOB-001",) and row["root_transitions"] for row in observer.kill_calls))
        checks.require("C05_T_actual_thread_context", len(tails) == 1 and sweep is not None
            and tails[0][1].ident != sweep._body_thread and len(observer.terminations) == 1
            and observer.terminations[0] == (tails[0][1].ident, [])
            and any(row["name"] == "T_parent_handoff" and row["delivery"] == id(sweep.delivery)
                    and row["tail"] == id(tails[0][1]) for row in observer.events))
        checks.require("C05_T_actual_denial", isinstance(cause, sqlite3.DatabaseError) and bool(observer.denials)
            and sweep is not None and sweep._first is cause and gate._first_cause is cause
            and len(tails) == 1 and tails[0][2].exception() is cause and observer.restored and not observer.restoration_errors
            and not db._conn.in_transaction and all(row["table"] == "task_completion_recoveries"
                and row["database"] == "main" and row["connection"] == id(db._conn)
                and row["thread"] == tails[0][1].ident for row in observer.denials))
        merged = [row for row in observer.merges if row["sweep"] is sweep]
        valid_transition = lambda event: (event.before["id"] == event.after["id"] == "TASK-091"
            and event.before["status"] == "in_progress" and event.after["status"] == "completed")
        checks.require("C05_T_late_finally_merge", sweep is not None and any(
            row["boundary"] == "late_finally" and row["first"] is cause and row["teardown_incomplete"] is True
            and any(valid_transition(event) for event in row["transitions"]) for row in merged)
            and any(row["boundary"] == "merge" and row["first"] is cause for row in merged)
            and any(valid_transition(event) for event in sweep.delivery["events"]))
        checks.require("C05_T_whole_batch_suppressed", common and sweep is not None
            and sweep._sealed and sweep._boundary_complete and not sweep._batch_attempted
            and not sweep.delivery["active"] and observer.finalized.count(sweep) == 1
            and not any(row["orch"] is sweep.orch for row in observer.final_batches + observer.reconciles + observer.cleanups)
            and not any(owner is db for owner, _ in observer.reports))
        checks.require("C05_T_no_new_effects", sweep is not None and not sweep._tails and not sweep._humans
            and sweep.counts["tails_started"] == sweep.counts["tails_settled"] == 1
            and sweep.counts["effects_started"] == sweep.counts["effects_settled"] == observer.first_effect_counts.get(id(sweep)))
        audits = [dict(row) for row in db._conn.execute("SELECT action,payload FROM audit_log WHERE task_id=?", ("TASK-091",))]
        checks.require("C05_T_committed_prefix", task.status.value == "completed" and task.current_session_id == seed["session"]
            and result is not None and result["id"] == seed["result"]["id"] and recovery["state"] == "callback_consumed"
            and job["status"] == "failed" and job["reason"] == "task_ended"
            and sum(row["action"] == "completion_report" for row in audits) == 1
            and sum(row["action"] == "orchestration_step" for row in audits) == 1
            and any(row["action"] == "completion_report" and json.loads(row["payload"]).get("_result_row_id") == seed["result"]["id"] for row in audits))
        checks.details["T_committed"] = {"task": task.model_dump(mode="json"), "result": dict(result) if result else None,
                                       "recovery": recovery, "job": job, "audits": audits}
    else:
        checks.require("C08_S_first_causes", common and len(sweeps) == 1
            and str(sweeps[0]._first) == "startup_sweep_stopped"
            and gate._first_cause is gate._native.exception() is sweeps[0]._first
            and not sweeps[0]._batch_attempted and sweeps[0]._sealed and sweeps[0]._boundary_complete)


async def selected_epoch(checks, runtime, group):
    """One of THREE extra serial epochs. Every handle must retire before return."""
    import uvicorn
    from runtime.config import Settings
    from runtime.daemon import __main__ as daemon
    from runtime.daemon.app import create_app
    baseline, identities = failed_counts(runtime), store_paths(runtime)
    unchanged = immutable_records(runtime)
    first_row = len(checks.rows)
    state = daemon._build_state(Settings(project_root=SOURCE, session_timeout_seconds=15))
    if tuple(state.orgs) != ("fixture-a", "fixture-b"):
        raise RuntimeError("selected N/T/S original org order changed")
    after_p0 = failed_counts(runtime)
    wanted = {}
    for slug in state.orgs:
        wanted[slug] = dict.fromkeys(baseline[slug], 0)
        if group == "T":
            wanted[slug].update(audit_log=11, org_settings=5, workflow_authority_pointers=1,
                workflow_publication_journals=1 if slug == "fixture-a" else 2,
                identity_name_schema=1, identity_name_owners=4, identity_name_claims=4)
            if slug == "fixture-b":
                wanted[slug].update(authority_policy_active_selector=1,
                    authority_policy_active_selector_history=1, authority_policy_v2_control_audit=1)
        else:
            wanted[slug].update(audit_log=6)
    checks.require("C03_T_initial_p0" if group == "T" else "C03_new_epoch_cumulative",
        {slug: {table: n - baseline[slug][table] for table, n in counts.items()} for slug, counts in after_p0.items()}, wanted)
    app = create_app(state)
    gate, reads = state._startup_recovery, state._startup_reads
    observer = SelectedObservation(app, state, checks, group)
    orgs, connections = dict(state.orgs), {slug: org.db._conn for slug, org in state.orgs.items()}
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    server = daemon._DiagnosticServer(uvicorn.Config(app, host="127.0.0.1", port=sock.getsockname()[1],
        log_level="warning", timeout_graceful_shutdown=15), app)
    handle, primary, shutdown_error, restoration_error = None, None, None, None
    durable, first = None, None
    restored = False
    try:
        observer.install_selected()
        handle = asyncio.create_task(server.serve(sockets=[sock]))
        if group == "S":
            await selected_until(checks, lambda: server.started and observer.entered.is_set())
            await stopped_reader(checks, observer, state, server, handle)
        await selected_until(checks, lambda: gate._coordinator is not None and gate._coordinator.done())
        await asyncio.shield(gate._coordinator)
        first = gate._first_cause
        selected_outcomes(checks, observer, gate)
        durable = database_observation(runtime)
        if group in ("N", "S"):
            checks.require("C13_S_durable_handoff" if group == "S" else "C05_N_no_ready",
                           immutable_records(runtime), unchanged)
    except BaseException as exc:
        primary = exc
    finally:
        observer.release.set()
        server.should_exit = True
        try:
            if handle is not None:
                await asyncio.wait_for(asyncio.shield(handle), timeout=20)
        except BaseException as exc:
            shutdown_error = exc
        finally:
            try:
                sock.close()
            except BaseException as exc:
                shutdown_error = shutdown_error or exc
            try:
                restored = observer.restore()
            except BaseException as exc:
                restoration_error = exc
        joined = [row for row in observer.events if row["name"] == "executor_joined"
                  and row["owner"] in (id(gate._executor), id(reads._executor))
                  and row["shutdown"] and not row["threads_alive"]]
        closed = [row for row in observer.events if row["name"] == "db_close_call"
                  and row["owner"] in {id(org.db) for org in orgs.values()}]
        bindings = gate._binding is None or (gate._binding.task is not None and gate._binding.task.done()
                    and gate._binding.completion.done() and not gate._binding.completion.cancelled())
        native_retired = (gate._native is not None and gate._native.done() and not gate._native.cancelled()
            and gate._coordinator is not None and gate._coordinator.done() and gate._closed and bindings
            and gate._sweep_owner is None and len(joined) == 2 and reads._closed and not reads._leases
            and all(not thread.is_alive() and future.done() and not future.cancelled()
                    for _, thread, future in observer.tail_handles)
            and all(sweep._boundary_complete and sweep._sealed and not sweep._tails and not sweep._humans
                    and sweep.counts["effects_started"] == sweep.counts["effects_settled"] for sweep in observer.sweeps))
        # N and final-success retain the canonical consumed owner's zero-job
        # native tail, not a deleted ledger/replayed completion.
        if group == "N":
            native_retired &= (len(observer.tail_handles) == 1 and any(
                row["recovery_job_ids"] == () for row in observer.kill_calls))
        ordered = (len(closed) == 2 and len(joined) == 2 and max(row["at"] for row in joined) <= min(row["at"] for row in closed)
            and all(observer.connection_closes.get(id(conn), (None, False))[1] for conn in connections.values()))
        safe = (native_retired and ordered and restored and not observer.restoration_errors
            and shutdown_error is None and restoration_error is None and state._shutdown_complete
            and handle is not None and handle.done() and not handle.cancelled() and handle.exception() is None
            and sock.fileno() == -1 and all(not listener.is_serving() for listener in server.servers)
            and first is not None and gate._first_cause is first)
        checks.require("C08_" + group + ("_shutdown_order" if group == "S" else "_retired"), safe)
        checks.details[group + "_epoch"] = {"epoch": gate.epoch, "recovery": id(gate), "reads": id(reads),
            "native": id(gate._native), "coordinator": id(gate._coordinator), "events": observer.events,
            "denials": observer.denials, "tails": [{"sweep": id(sweep), "thread": id(thread), "ident": thread.ident,
                "future": id(future), "alive": thread.is_alive()} for sweep, thread, future in observer.tail_handles],
            "failures": [{"sweep": id(sweep), "cause": id(cause), "kind": type(cause).__name__} for sweep, cause in observer.failures],
            "errors": {"primary": None if primary is None else type(primary).__name__,
                "shutdown": None if shutdown_error is None else type(shutdown_error).__name__,
                "restoration": None if restoration_error is None else type(restoration_error).__name__}}
        for error in (shutdown_error, restoration_error):
            if error is not None:
                if primary is None:
                    primary = error
                elif error is not primary:
                    primary.add_note("Selected original closure also failed: " + type(error).__name__)
        if primary is not None:
            raise primary.with_traceback(primary.__traceback__)
        if not safe:
            raise RuntimeError(group + " original ownership/restoration unknown; reopen refused")
    if durable is None or database_observation(runtime) != durable or store_paths(runtime) != identities:
        raise RuntimeError(group + " durable handoff/identity changed; reopen refused")
    if any(row["status"] == "FAIL" for row in checks.rows[first_row:]):
        raise RuntimeError(group + " original selected assertion failed; reopen refused")


async def failed_prefix(checks, runtime, token):
    import uvicorn
    from runtime.config import Settings
    from runtime.daemon import __main__ as daemon
    from runtime.daemon.app import create_app
    from runtime.daemon.startup_recovery import StartupUnavailable
    from runtime.infrastructure.db.authority_v2_attempts import _V2StartupEvidenceUnavailable
    from runtime.orchestrator.run_step import _StartupSweepUnavailable
    baseline, paths = failed_counts(runtime), store_paths(runtime)
    immutable = immutable_records(runtime)
    state = daemon._build_state(Settings(project_root=SOURCE, session_timeout_seconds=15))
    if tuple(state.orgs) != ("fixture-a", "fixture-b"):
        raise RuntimeError("selected first-org order changed")
    app = create_app(state)
    recovery, reads = state._startup_recovery, state._startup_reads
    orgs = dict(state.orgs)
    connections = {slug: org.db._conn for slug, org in orgs.items()}
    queues_before = task_queue_observation(state)
    after_p0 = failed_counts(runtime)
    wanted = {}
    for slug in ("fixture-a", "fixture-b"):
        wanted[slug] = {table: 0 for table in baseline[slug]}
        wanted[slug].update(audit_log=6)
    checks.require("C03_failed_p0_expected_rows", {
        slug: {table: n - baseline[slug][table] for table, n in rows.items()}
        for slug, rows in after_p0.items()}, wanted)
    checks.require("C03_failed_p0_immutable_records", immutable_records(runtime), immutable)
    wire = []
    observer = FailureObservation(app, state, checks)
    # Preserve normal method binding: no reader is replaced or constructed here.
    def discover(reads, *args, **kwargs):
        return observer.selected_discover(reads, *args, **kwargs)
    observe_wire(app, token, wire, observer)
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    server = daemon._DiagnosticServer(uvicorn.Config(app, host="127.0.0.1", port=port,
        log_level="warning", timeout_graceful_shutdown=15), app)
    handle = None
    primary, close_error = None, None
    first = None
    durable_before_stop = None
    restoration_error = None
    try:
        observer.install(discover)
        handle = asyncio.create_task(server.serve(sockets=[sock]))
        cutoff = min(checks.started + 600, time.monotonic() + 8)
        while not (server.started and recovery._coordinator is not None and recovery._coordinator.done()):
            if handle.done():
                await handle
                raise RuntimeError("failed listener ended before genuine serving")
            if time.monotonic() >= cutoff:
                raise TimeoutError("failed prefix observation deadline")
            await asyncio.sleep(.01)
        native, coordinator = recovery._native, recovery._coordinator
        await asyncio.shield(coordinator)
        snapshot = recovery.snapshot()
        first = recovery._first_cause
        checks.details["failed_epoch_before_stop"] = {"snapshot": snapshot, "epoch": recovery.epoch,
            "native": id(native), "coordinator": id(coordinator), "recovery": id(recovery), "reads": id(reads),
            "orgs": {slug: id(org) for slug, org in orgs.items()},
            "connections": {slug: id(conn) for slug, conn in connections.items()}, "port": port}
        lower = observer.errors[0] if observer.errors else None
        frames = []
        trace = None if lower is None else lower.__traceback__
        while trace is not None:
            frames.append(trace.tb_frame.f_code.co_qualname)
            trace = trace.tb_next
        checks.details["failed_lower_cause"] = {"kind": type(lower).__name__,
            "sqlite_kind": None if lower is None else type(lower.__cause__).__name__,
            "frames": frames, "first_sweep_kind": type(first).__name__,
            "same_object": lower is first, "reader_checks": observer.reader_checks}
        checks.require("C05_failed_original_reader", observer.discoveries == 1
            and observer.reader.db is orgs["fixture-a"].db and observer.reader.conn is connections["fixture-a"])
        checks.require("C05_failed_actual_denial", bool(observer.denials) and all(
            row == {"action": sqlite3.SQLITE_READ, "table": "authority_policy_v2_attempts", "database": "main",
                    "thread": observer.native_threads[0], "connection": id(connections["fixture-a"])}
            for row in observer.denials))
        checks.require("C05_failed_owned_read_unavailable", isinstance(lower, _V2StartupEvidenceUnavailable)
            and isinstance(lower.__cause__, sqlite3.DatabaseError)
            and all(name in frames for name in ("_V2StartupReads.discover", "_V2StartupReads._read", "_V2StartupReads._view")))
        checks.require("C05_failed_clean_connection", observer.reader is not None
            and observer.reader.unsafe is None and not connections["fixture-a"].in_transaction
            and len(observer.reader_checks) >= 3 and all(not tx and safe for _, tx, safe, _ in observer.reader_checks)
            and sum(post for _, _, _, post in observer.reader_checks) >= 2)
        checks.require("C05_failed_callback_restored", observer.restored and not observer.restoration_errors)
        checks.require("C05_failed_authority_normal_none", observer.authority_returns, [True])
        sweeps = observer.sweeps
        checks.require("C05_failed_sweep_native_chain", native is not None and native.done() and not native.cancelled()
            and native.exception() is first and isinstance(first, _StartupSweepUnavailable)
            and str(first) == "startup_v2_discovery_unavailable" and first is not lower
            and recovery._error_code == "startup_sweep_failed" and len(sweeps) == 1
            and sweeps[0]._first is first and sweeps[0]._boundary_complete and sweeps[0]._sealed
            and not sweeps[0]._batch_attempted
            and sum(row["name"] == "submit_call" for row in observer.events) == 1
            and any(row["name"] == "finalize_return" and row["owner"] == id(sweeps[0]) for row in observer.events))
        checks.require("C05_failed_no_ready_consumers", snapshot["state"] == "recovery_failed"
            and snapshot["ready_commits"] == 0 and not recovery._latch.is_set()
            and not recovery._preparation_complete and not recovery._consumers
            and not any(row["name"] == "prepare_call" for row in observer.events))
        checks.require("C05_failed_no_provider", observer.totals, dict.fromkeys(observer.totals, 0))
        checks.require("C05_failed_listener", snapshot["serving"] and not snapshot["recovery_ready"]
            and sock.getsockname() == ("127.0.0.1", port)
            and any(s.fileno() == sock.fileno() for listener in server.servers for s in listener.sockets))
        for name, target, status in (("health", "/api/v1/health", 200), ("ready", "/api/v1/health/ready", 503)):
            before = fixture_effects(runtime, state, checks.args.root)
            response = await raw_request(port, "GET", target, token, "C05_failed_" + name)
            row = wire_contract(checks, wire, "C05", "failed_" + name, "GET", target, response, target)
            endpoint = "runtime.daemon.routes.health." + ("health" if name == "health" else "health_ready")
            payload = response_json(response["body"])
            projection = payload.get("startup", {})
            checks.require("C05_failed_" + name, response["status"] == status
                and projection.get("state") == "recovery_failed" and projection.get("serving") is True
                and projection.get("recovery_ready") is False and projection.get("ready_commits") == 0
                and payload.get("status") == ("ok" if name == "health" else "unavailable")
                and row.get("fixture_authenticated") is True
                and row.get("original_handler_entries", {}).get(endpoint, 0) > 0
                and fixture_effects(runtime, state, checks.args.root) == before)
        for slug in ("fixture-a", "fixture-b"):
            for name, target, route in (
                ("H153", f"/api/v1/orgs/{slug}/threads?page_size=2", "/api/v1/orgs/{slug}/threads"),
                ("H156", f"/api/v1/orgs/{slug}/threads/THR-001", "/api/v1/orgs/{slug}/threads/{thread_id}"),
                ("H157", f"/api/v1/orgs/{slug}/threads/THR-001/messages?limit=10", "/api/v1/orgs/{slug}/threads/{thread_id}/messages")):
                phase = "failed_" + slug
                key = name + "_" + phase
                before = fixture_effects(runtime, state, checks.args.root)
                response = await raw_request(port, "GET", target, token, key)
                checks.require(key + "_status", response["status"], 200)
                checks.require(key + "_content", content_contract(name, response, runtime, token, phase))
                wire_contract(checks, wire, name, phase, "GET", target, response, route)
                checks.require(key + "_effects", fixture_effects(runtime, state, checks.args.root), before)
                auth = []
                for suffix, bearer in (("missing", None), ("invalid", "invalid-fixture-token")):
                    before = fixture_effects(runtime, state, checks.args.root)
                    denied = await raw_request(port, "GET", target, bearer, key + "_" + suffix)
                    auth.append(denied["status"] == 401 and response_json(denied["body"]) == {
                        "detail": "missing bearer token" if bearer is None else "bad token"}
                        and fixture_effects(runtime, state, checks.args.root) == before)
                checks.require(key + "_auth", auth, [True, True])
        # Do not consume/pop the original accepted inputs before successful epoch.
        selected = {entry["id"]: entry for entry in checks.details["_http_inputs"]["literal22"]}
        for name in ("HX10.04", "HX10.11"):
            if name not in selected:
                checks.missing(name + "_recovery_failed_content", "original accepted literal input unavailable")
                continue
            before = fixture_effects(runtime, state, checks.args.root)
            await literal_cases(checks, runtime, port, token, wire, "recovery_failed", [selected[name]], None)
            checks.require(name + "_recovery_failed_effects", fixture_effects(runtime, state, checks.args.root), before)
        for slug, org in orgs.items():
            task = org.db.get_task("TASK-090")
            if task is None or task.status.value != "pending" or task.assigned_agent != checks.details["native_task_selections"][slug]["agent"]:
                raise RuntimeError("valid original direct task owner changed")
            for boundary, call in (("state", state._require_startup_ready),
                                   ("queue", lambda slug=slug: state.queue.enqueue_if_absent(slug, task.id))):
                before = fixture_effects(runtime, state, checks.args.root)
                queue_before = task_queue_observation(state)
                code = None
                try:
                    value = call()
                    if asyncio.iscoroutine(value):
                        await value
                except StartupUnavailable as exc:
                    code = exc.code
                checks.require("C05_failed_" + slug + "_" + boundary,
                    code == "daemon_recovery_failed" and task_queue_observation(state) == queue_before
                    and fixture_effects(runtime, state, checks.args.root) == before)
        checks.require("C05_failed_no_provider", observer.totals["native"], 0)
        checks.require("C05_failed_no_ready_consumers", task_queue_observation(state), queues_before)
        durable_before_stop = database_observation(runtime)
    except BaseException as exc:
        primary = exc
    finally:
        # The original lifespan owns ALL close operations. No manual DB/worker
        # close or synthetic Future settlement can unblock a fresh epoch.
        server.should_exit = True
        try:
            if handle is not None:
                await asyncio.wait_for(asyncio.shield(handle), timeout=20)
        except BaseException as exc:
            close_error = exc
        finally:
            restored = False
            try:
                sock.close()
            except BaseException as exc:
                close_error = close_error or exc
            try:
                restored = observer.restore()
            except BaseException as exc:
                restoration_error = exc
            checks.details["failed_original_events"] = observer.events
            checks.details["failed_denials"] = observer.denials
            checks.details["failed_wire"] = wire
            checks.details["failed_errors"] = {
                "primary": None if primary is None else type(primary).__name__,
                "shutdown": None if close_error is None else type(close_error).__name__,
                "observer_restoration": None if restoration_error is None else type(restoration_error).__name__,
                "authorizer_restoration": [type(exc).__name__ for exc in observer.restoration_errors]}
        checks.require("C08_failed_observers_restored", restored and observer.restored and not observer.restoration_errors)
        checks.require("C08_failed_first_cause_retained", first is not None and recovery._first_cause is first)
        native = recovery._native
        checks.require("C08_failed_recovery_close", native is not None and native.done() and not native.cancelled()
            and recovery._coordinator is not None and recovery._coordinator.done() and recovery._closed
            and any(row["name"] == "executor_joined" and row["owner"] == id(recovery._executor)
                    and row["shutdown"] and not row["threads_alive"] for row in observer.events))
        def positively_retired(lease):
            handle = observer.reader_handles.get(id(lease))
            starts = [row for row in observer.events if row["name"] == "read_worker_call" and row["lease"] == id(lease)]
            ends = [row for row in observer.events if row["name"] == "read_worker_return" and row["lease"] == id(lease)]
            retires = [row for row in observer.events if row["name"] == "retire_return" and row["lease"] == id(lease)]
            return (lease.future is not None and lease.future.done() and lease.retired.done()
                    and lease.close_proven and lease.close_error is None and lease.connection is None
                    and lease.started and handle is not None and handle["closed"]
                    and len(starts) == len(ends) == len(retires) == 1
                    and handle["thread"] == starts[0]["thread"] == ends[0]["thread"]
                    and handle["at"] <= ends[0]["at"] <= retires[0]["at"])
        checks.require("C08_failed_reader_retirement", bool(observer.leases)
                       and all(positively_retired(lease) for lease in observer.leases))
        checks.require("C08_failed_reader_executor", reads._closed and not reads._leases
            and any(row["name"] == "executor_joined" and row["owner"] == id(reads._executor)
                    and row["shutdown"] and not row["threads_alive"] for row in observer.events))
        joined = [row["at"] for row in observer.events if row["name"] == "executor_joined"
                  and row["owner"] in (id(recovery._executor), id(reads._executor))]
        db_entries = [row["at"] for row in observer.events if row["name"] == "db_close_call"
                      and row["owner"] in {id(org.db) for org in orgs.values()}]
        checks.require("C08_failed_database_order", len(db_entries) == 2 and len(joined) >= 2
            and max(joined) <= min(db_entries)
            and all(id(conn) in observer.connection_closes and observer.connection_closes[id(conn)][1]
                    for conn in connections.values()))
        checks.require("C08_failed_server_socket", close_error is None and state._shutdown_complete
            and handle is not None and handle.done() and not handle.cancelled() and handle.exception() is None
            and sock.fileno() == -1 and all(not listener.is_serving() for listener in server.servers))
        if primary is not None:
            if close_error is not None:
                primary.add_note("Original failed epoch shutdown also failed: " + type(close_error).__name__)
            if restoration_error is not None:
                primary.add_note("Original failed epoch observer restoration also failed: " + type(restoration_error).__name__)
            raise primary.with_traceback(primary.__traceback__)
        if close_error is not None:
            if restoration_error is not None:
                close_error.add_note("Observer restoration also failed: " + type(restoration_error).__name__)
            raise close_error.with_traceback(close_error.__traceback__)
        if restoration_error is not None:
            raise restoration_error.with_traceback(restoration_error.__traceback__)
    durable = database_observation(runtime)
    checks.require("C08_failed_durable_preserved", durable_before_stop is not None and durable == durable_before_stop
        and immutable_records(runtime) == immutable and store_paths(runtime) == paths)
    # Failed assertions preserve their original receipts and prohibit reopen.
    if any(row["status"] == "FAIL" for row in checks.rows if "_failed_" in row["assertion"]):
        raise RuntimeError("failed epoch ownership/oracle incomplete; SAME-store reopen refused")
    return {"recovery": recovery, "reads": reads, "native": recovery._native, "orgs": orgs,
            "connections": connections, "paths": paths, "immutable": immutable,
            "durable": durable}


async def serving(checks, runtime):
    import httpx
    import uvicorn
    from runtime.config import Settings
    from runtime.daemon import __main__ as daemon, runtimes, paths
    from runtime.daemon.app import create_app
    from runtime.daemon.startup_recovery import StartupUnavailable
    from runtime.orchestrator import run_step
    consumers = prepare_consumers(checks)
    runtimes.register(runtime.root)
    token = paths.ensure_token()
    prepare_task_executable(checks)
    for group in ("T", "N", "S"):
        await selected_epoch(checks, runtime, group)
    failed = await failed_prefix(checks, runtime, token)
    if database_observation(runtime) != failed["durable"]:
        raise RuntimeError("same-store durable handoff changed; fresh startup refused")
    if not checks.require("C13_failed_same_store_before_reopen", store_paths(runtime), failed["paths"]):
        raise RuntimeError("same-store file identity changed; fresh startup refused")
    state = daemon._build_state(Settings(project_root=SOURCE, session_timeout_seconds=15))
    checks.require("C13_failed_fresh_p0_records", immutable_records(runtime), failed["immutable"])
    if set(state.orgs) != {"fixture-a", "fixture-b"}:
        raise AssertionError("P0 failed to attach both original fixture orgs")
    selected = checks.details.pop("_http_inputs")
    index = prepare_static(checks)
    app = create_app(state)
    fresh = state._startup_recovery
    if not checks.require("C13_failed_distinct_owners", (
            fresh is not failed["recovery"] and fresh.epoch != failed["recovery"].epoch
            and state._startup_reads is not failed["reads"]
            and all(state.orgs[slug].db._conn is not conn for slug, conn in failed["connections"].items())
            and all(state.orgs[slug] is not org for slug, org in failed["orgs"].items()))):
        raise RuntimeError("fresh epoch owner identity reused")
    wire = []
    observation = EntryObservation(app)
    task_entries = TaskEntries(state)
    reader_observation = ReaderObservation(observation)  # reusable barriers before the sole hold
    checks.details["wire"] = wire
    observe_wire(app, token, wire, observation)
    # Verify the accepted literal absent ID independently, before the native hold.
    for slug, root in runtime.iter_org_roots():
        conn = sqlite3.connect((root / "happyranch.db").as_uri() + "?mode=ro", uri=True, timeout=.05)
        try:
            checks.require("C02_" + slug + "_absent_literal_thread",
                conn.execute("SELECT COUNT(*) FROM threads WHERE id=?", ("THR-absent-13137",)).fetchone()[0], 0)
        finally:
            conn.close()
    original = daemon._sweep_on_startup
    original_tail = run_step._StartupSweepOwner.start_tail
    def observe_tail(owner, operation):
        caller = sys._getframe(1)
        if caller.f_code is not run_step._kill_jobs_for_terminating_task.__code__:
            raise RuntimeError("successful consumed-owner tail caller changed")
        observation.startup_tail_inputs.append({key: caller.f_locals[key] for key in
                                               ("task_id", "recovery_owner", "recovery_job_ids")})
        thread = original_tail(owner, operation)
        with owner._lock:
            future = owner._tails.get(thread)
        if future is None or owner.orch._startup_recovery is not state._startup_recovery:
            raise RuntimeError("successful original consumed-owner tail lost its reservation")
        observation.startup_tails.append((owner, thread, future))
        return thread
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
    run_step._StartupSweepOwner.start_tail = observe_tail
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
    discovery_originals = {}
    try:
        deadline = time.monotonic() + 8
        while not (server.started and entered.is_set()):
            if handle.done():
                await handle
                raise AssertionError("listener ended before held recovery")
            if time.monotonic() >= deadline:
                raise TimeoutError("listener/hold not observed")
            await asyncio.sleep(.01)
        checks.require("C08_success_consumed_zero_job_tail_retired", len(observation.startup_tails) == 1
            and len(observation.startup_tail_inputs) == 1
            and observation.startup_tail_inputs[0]["task_id"] == "TASK-091"
            and observation.startup_tail_inputs[0]["recovery_job_ids"] == ()
            and observation.startup_tail_inputs[0]["recovery_owner"] == ("case_manager", checks.details["T_seed"]["session"],
                                                                       checks.details["T_seed"]["result"]["id"], "completed")
            and all(not thread.is_alive() and future.done() and not future.cancelled() and future.exception() is None
                and owner._boundary_complete and owner._sealed and not owner._tails and not owner._humans
                and owner.counts["tails_started"] == owner.counts["tails_settled"] == 1
                for owner, thread, future in observation.startup_tails))
        # Test-side discovery points ONLY to the actual already-serving owner.
        # Never call daemon main or publish a wrapper/start/epoch/authority fact.
        if not (server.started and state._startup_recovery.snapshot()["serving"]
                and sock.getsockname() == ("127.0.0.1", port)
                and any(s.fileno() == sock.fileno() and s.getsockname() == sock.getsockname()
                        for listener in server.servers for s in listener.sockets)):
            raise AssertionError("original listener identity unavailable for discovery")
        os.kill(os.getpid(), 0)
        if paths.daemon_home() != checks.args.root / "daemon":
            raise ValueError("consumer metadata escaped original isolated home")
        for path, value in ((paths.pid_file(), os.getpid()), (paths.port_file(), port)):
            if path.is_symlink() or path.exists():
                raise ValueError("unexpected original fixture discovery metadata; preserve")
            discovery_originals[path] = str(value).encode()
            with path.open("xb") as stream:
                stream.write(discovery_originals[path])
        checks.details["consumer_discovery_provenance"] = {
            "pid": os.getpid(), "port": port, "socket_fd": sock.fileno(),
            "home": str(paths.daemon_home()), "published_after_actual_serving": True,
            "source": "original _DiagnosticServer/listener + os.getpid, not production start/wrapper"}
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}",
                headers={"Authorization": "Bearer " + token}, timeout=2, follow_redirects=False) as client:
            await held_task_cases(checks, runtime, state, task_entries)
            task_entries.close()
            await consumer_cases(checks, runtime, state, server, sock, client, wire, consumers, observation, "recovering")
            before = fixture_effects(runtime, state, checks.args.root)
            observation.start()
            await early_cases(checks, runtime, port, token, wire, "recovering")
            await reader_cases(checks, state, client, reader_observation)
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
            checks.require("C13_failed_fresh_ready", status["ready_commits"] == 1
                and state._startup_recovery._native is not failed["native"]
                and store_paths(runtime) == failed["paths"]
                and {slug: {table: value for table, value in rows.items() if table not in ("tasks", "task_results")}
                     for slug, rows in immutable_records(runtime).items()} == {
                         slug: {table: value for table, value in rows.items() if table not in ("tasks", "task_results")}
                         for slug, rows in failed["immutable"].items()})
            state._require_startup_ready()
            await ready_task_cases(checks, runtime, state, task_entries)
            task_entries.close()
            response = await client.get("/api/v1/orgs/fixture-a/threads/THR-001/messages?limit=10")
            checks.require("C01_post_ready_transcript", response.status_code == 200 and MESSAGE in response.text)
            checks.details["completed_startup"] = state._startup_recovery.snapshot()
            checks.require("C10_owned_reader_admissions_retired", state._startup_reads.snapshot()["active"], 0)
            # All ready HTTP controls precede maintenance switch/stop invalidation.
            await maintenance_waits.parked(dict.fromkeys(maintenance_waits.names, 1))
            await consumer_cases(checks, runtime, state, server, sock, client, wire, consumers, observation, "ready")
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
            run_step._StartupSweepOwner.start_tail = original_tail
            asyncio.sleep = maintenance_waits.original_sleep
            sock.close()
        # Restore only our exact synthetic discovery files after positive owner
        # retirement; unknown cleanup or changed bytes retain their evidence.
        if not state._shutdown_complete or any(not r["reaped"] for r in consumers["receipts"]):
            raise RuntimeError("consumer/listener owners unretired; discovery retained")
        for path, raw in discovery_originals.items():
            if path.is_symlink() or path.read_bytes() != raw:
                raise RuntimeError("synthetic discovery changed; preserve original")
            path.unlink()
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
