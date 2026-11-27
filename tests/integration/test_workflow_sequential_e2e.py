"""U3: real private daemon, genuine CLI callbacks, immutable released graphs.

Four-answer case record (E1-E8, including named parametrized subcases):
1. Observe actual prompt bytes, native callback/closure, persisted transitions,
   initiator controls/notification consumption and independent daemon warnings.
2. Removing negative-outcome settlement, startup reconciliation, queue fencing,
   member publication or trusted recovery rendering breaks those assertions.
3. test_chain_e2e owns ordinary chains; schema-shell C15/C27-C33 and S4/S5 own
   provider-free boundaries. None owns these actual process/callback lifecycles.
4. No production seam: source-bound test-side fault windows and fake CLI only.

Exact-head hosted GREEN remains required; module RED is PROOF NOT RUN under
the venue budget. Static checks cannot establish a lifecycle PASS.
Populated-history lifecycle: NOT RUN.
"""
from __future__ import annotations

import base64
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import shlex
import sqlite3
import subprocess
import sys
import time
import traceback
from typing import Callable

import httpx
import pytest

pytestmark = pytest.mark.integration

REQUEST = "Synthetic U3 observation only; no repository or provider action is authorized."
SUPPLEMENT = "U3-rebrief-marker effect-receipt-1 head=" + "1" * 40 + " merge=" + "2" * 40
POLICY_BEGIN = "<!-- BEGIN HAPPYRANCH ACTIVE TEAM POLICY -->"
POLICY_END = "<!-- END HAPPYRANCH ACTIVE TEAM POLICY -->"


class _CensusBusy(Exception):
    """Only the finite polling loop may retry this exact read refusal."""


def _bytes(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def _save(path: Path, value: object) -> None:
    pending = path.with_name(path.name + ".pending-" + str(os.getpid()))
    pending.write_bytes(_bytes(value))
    pending.replace(path)


def _rows(root: Path, sql: str, args: tuple = ()) -> list[dict]:
    # Read-only private fixture DB. This module never executes lifecycle SQL writes.
    with closing(sqlite3.connect((root / "happyranch.db").as_uri() + "?mode=ro", uri=True)) as conn:
        conn.row_factory = sqlite3.Row
        return [dict(row) for row in conn.execute(sql, args)]


def _wait(read: Callable, predicate: Callable = bool, *, label: str, timeout: float = 45) -> object:
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        try:
            last = read()
        except _CensusBusy:
            # The request already used part of this same deadline. No fresh
            # deadline or retry of callbacks/non-poll assertions is granted.
            time.sleep(min(.2, max(0, deadline - time.monotonic())))
            continue
        if predicate(last):
            return last
        capture = os.environ.get("HAPPYRANCH_WORKFLOW_CAPTURE")
        if capture:
            errors = sorted(Path(_binding()["private"]).glob("*-actor-error.receipt.json"))
            assert not errors, f"{label}: actor failed: {errors[0].read_text() if errors else ''}"
        time.sleep(.1)
    raise AssertionError(f"{label}: deadline exhausted; last={last!r}")


def _binding() -> dict:
    return json.loads(Path(os.environ["HAPPYRANCH_WORKFLOW_CAPTURE"]).read_bytes())


def _receipt(private: Path, name: str, **value: object) -> None:
    _save(private / (name + ".receipt.json"), value)


def _callback(private: Path, payload: dict, *, name: str, expected: bool | None = True) -> subprocess.CompletedProcess:
    path = private / (name + ".callback.json")
    _save(path, payload)
    result = subprocess.run(["happyranch", "report-completion", "--org", "test", "--from-file", str(path)],
                            capture_output=True, timeout=10)
    # Only bounded synthetic CLI output is retained, never the callback file/env.
    _receipt(private, name, command="happyranch report-completion --org test --from-file ABS",
             task_id=payload["task_id"], session_id=payload["session_id"], exit=result.returncode,
             stdout_bytes=len(result.stdout), stderr_bytes=len(result.stderr),
             stdout_sha256=hashlib.sha256(result.stdout).hexdigest(),
             stderr_sha256=hashlib.sha256(result.stderr).hexdigest(),
             stdout=result.stdout[:2048].decode(errors="replace"),
             stderr=result.stderr[:2048].decode(errors="replace"))
    if expected is not None:
        assert (result.returncode == 0) == expected, f"{name}: callback exit={result.returncode} {(result.stdout + result.stderr)[:2048]!r}"
    return result


def _assessment(root: Path, task: str, sid: str) -> dict:
    bindings = _rows(root, "SELECT * FROM authority_policy_v2_session_bindings "
                     "WHERE root_task_id=? AND manager_session_id=?", (task, sid))
    assert len(bindings) == 1, "ordinary initiating session lost its active v2 binding"
    value = bindings[0]
    fields = ("activation_id", "contract_digest", "contract_id", "contract_version", "executor_kind",
              "model_id", "policy_digest", "policy_version", "provider_id", "release_id",
              "root_task_id", "manager_session_id", "activation_epoch")
    return dict({key: value[key] for key in fields},
        what_to_escalate=dict(applicability="does_not_apply", confidence=100, uncertainty_codes=[]),
        what_not_to_escalate=dict(applicability="applies", confidence=100, uncertainty_codes=[]))


def _counts(root: Path, task: str) -> dict:
    tree = "WITH RECURSIVE ancestors(id,parent_task_id) AS (SELECT id,parent_task_id FROM tasks WHERE id=? " \
        "UNION SELECT t.id,t.parent_task_id FROM tasks t JOIN ancestors a ON t.id=a.parent_task_id), " \
        "tree(id) AS (SELECT id FROM ancestors WHERE parent_task_id IS NULL " \
        "UNION SELECT t.id FROM tasks t JOIN tree p ON t.parent_task_id=p.id), " \
        "runs(id) AS (SELECT id FROM workflow_seq_runs WHERE source_task_id IN (SELECT id FROM tree)) "
    tables = ("tasks", "task_results", "audit_log", "authority_policy_v2_session_bindings", "authority_policy_v2_attempts",
              "workflow_seq_runs", "workflow_seq_visits", "workflow_seq_controls", "workflow_seq_outputs",
              "workflow_seq_evidence", "workflow_seq_inbox")
    scopes = dict(tasks="id IN (SELECT id FROM tree)", task_results="task_id IN (SELECT id FROM tree)",
        audit_log="task_id IN (SELECT id FROM tree)",
        authority_policy_v2_session_bindings="root_task_id IN (SELECT id FROM tree)",
        authority_policy_v2_attempts="root_task_id IN (SELECT id FROM tree)",
        workflow_seq_runs="id IN (SELECT id FROM runs)")
    return {name: _rows(root, tree + "SELECT count(*) AS n FROM " + name + " WHERE " +
        scopes.get(name, "run_id IN (SELECT id FROM runs)"), (task,))[0]["n"] for name in tables}


def _closed_counts(root: Path, private: Path, task: str, sid: str) -> dict:
    def closed() -> bool:
        # Results can precede their original native tail. Observe that tail
        # before asserting that a refused callback allocated nothing.
        ancestors = _rows(root, "WITH RECURSIVE a(id,parent_task_id) AS (SELECT id,parent_task_id FROM tasks WHERE id=? "
            "UNION SELECT t.id,t.parent_task_id FROM tasks t JOIN a ON t.id=a.parent_task_id) "
            "SELECT id FROM a WHERE parent_task_id IS NULL", (task,))
        source, = ancestors
        for visit in _rows(root, "SELECT v.id FROM workflow_seq_visits v JOIN workflow_seq_runs r ON r.id=v.run_id "
                          "WHERE r.source_task_id=? AND v.session_id IS NOT NULL AND v.session_id!=?", (source["id"], sid)):
            kinds = {r["kind"] for r in _rows(root, "SELECT kind FROM workflow_seq_evidence WHERE visit_id=?", (visit["id"],))}
            if not {"terminal", "outer"} <= kinds:
                return False
        for row in _rows(root, "SELECT session_id FROM task_results WHERE task_id=? AND session_id!=?", (source["id"], sid)):
            path = private / ("source-closure-" + row["session_id"] + ".receipt.json")
            if not path.exists() or not json.loads(path.read_bytes())["positive"]:
                return False
        return True
    _wait(closed, label="prior committed results and original native closure")
    return _counts(root, task)


def _assert_counts(root: Path, task: str, before: dict, label: str) -> dict:
    after = _counts(root, task)
    assert after == before, dict(reason=label, before=before, after=after,
                                deltas={k: after[k] - before[k] for k in before})
    return after


def _policy_diagnostics(root: Path, task: str, sid: str, variant: str) -> dict:
    """Observe stored callback evidence, independently of the admission helper."""
    result, = _rows(root, "SELECT * FROM task_results WHERE task_id=? AND session_id=?", (task, sid))
    decision = json.loads(result["decision_json"])
    assert decision["action"] == "workflow_start"
    assert "_authority_v2_admission" not in decision, "workflow_start incorrectly omitted its attempt"
    carrier = decision["_manager_self_evaluation"]
    expected = _assessment(root, task, sid)
    if variant == "omitted":
        assert carrier == {"_error_code": "missing_assessment"}
    elif variant == "mismatch":
        expected["manager_session_id"] = "foreign-synthetic-SID"
        assert carrier == {"_error_code": "binding_mismatch",
                           "payload_digest": hashlib.sha256(_bytes(expected)).hexdigest()}
    else:
        assert variant == "valid-start" and carrier == expected and "_error_code" not in carrier
    attempt, = _rows(root, "SELECT * FROM authority_policy_v2_attempts WHERE result_id=?", (result["id"],))
    binding, = _rows(root, "SELECT * FROM authority_policy_v2_session_bindings WHERE root_task_id=? "
                     "AND manager_session_id=?", (task, sid))
    assert (attempt["root_task_id"], attempt["manager_session_id"], attempt["binding_id"]) == (
        task, sid, binding["binding_id"])
    assert (attempt["stage"], attempt["finalization_state"], attempt["refusal_code"]) == (
        "admitted", "unfinalized", None)
    assert attempt["assessment_digest"] == hashlib.sha256(_bytes(carrier)).hexdigest()
    audits = _rows(root, "SELECT * FROM audit_log WHERE task_id=? AND action='authority_policy_v2_result_stage'", (task,))
    admitted = [row for row in audits if json.loads(row["payload"]).get("result_id") == result["id"]]
    audit, = admitted
    fields = ("attempt_id", "result_id", "binding_id", "contract_id", "contract_version", "contract_digest",
              "release_id", "activation_id", "activation_epoch", "selector_id", "assessment_digest",
              "owner_attempt_id", "origin_boot_id")
    assert json.loads(audit["payload"]) == dict({key: attempt[key] for key in fields},
                                              stage="admitted", finalization_state="unfinalized")
    assert audit["agent"] == attempt["manager_agent"] == result["agent"]
    return dict(task_id=task, session_id=sid, result_id=result["id"], carrier=carrier,
                attempt_id=attempt["attempt_id"], audit_id=audit["id"], diagnostic=carrier.get("_error_code"))


def _source_actor(private: Path, root: Path, case: dict, payload: dict, prompt: str) -> None:
    task, sid = payload["task_id"], payload["session_id"]
    assert POLICY_BEGIN in prompt and POLICY_END in prompt, "ordinary initiating manager policy missing"
    assessment = _assessment(root, task, sid)
    runs = _rows(root, "SELECT * FROM workflow_seq_runs ORDER BY rowid")
    if not runs:
        compose_path = private / "compose.json"
        _save(compose_path, case["compose"])
        composed = subprocess.run(["happyranch", "workflows", "sequential", "start-compose", "--org", "test",
            "--session-id", sid, "--from-file", str(compose_path), "--json"], capture_output=True, timeout=10)
        assert composed.returncode == 0, f"public compose refused {composed.stderr[:2048]!r}"
        decision = json.loads(composed.stdout)
        assert decision["action"] == "workflow_start"
        # Advisory diagnostics retain ordinary start admission. Each variant
        # has its own source task/SID; controls always use valid evidence.
        variant = case.get("policy_variant", "valid-start")
        payload.update(decision=decision)
        if variant != "omitted":
            payload["manager_self_evaluation"] = (dict(assessment, manager_session_id="foreign-synthetic-SID")
                                                   if variant == "mismatch" else assessment)
        _callback(private, payload, name=sid + "-start")
        if case.get("policy_variant"):
            observed = _policy_diagnostics(root, task, sid, variant)
            before = _closed_counts(root, private, task, sid)
            _callback(private, payload, name=sid + "-start-replay")
            after = _assert_counts(root, task, before, "exact start replay allocated state")
            assert _policy_diagnostics(root, task, sid, variant) == observed
            _receipt(private, "policy-diagnostic", **observed, variant=variant, before=before, after=after)
        return
    run = next((r for r in runs if r["state"] != "finished"), runs[0]) if case.get("cancel_queued") else runs[0]
    if case.get("cancel_queued") and run["state"] != "finished":
        action = "cancel"
    elif run["state"] == "held" and case.get("recovery"):
        controls = _rows(root, "SELECT * FROM workflow_seq_controls WHERE run_id=?", (run["id"],))
        action = case["exhaustion_action"] if controls else case["recovery"]
    elif run["state"] == "finished" and all(r["state"] == "finished" for r in runs):
        delivered = _rows(root, "SELECT notification_id FROM workflow_seq_inbox_deliveries d "
            "WHERE initiator_task_id=? AND session_id=? AND NOT EXISTS "
            "(SELECT 1 FROM workflow_seq_consumed_notifications c WHERE c.initiator_task_id=d.initiator_task_id "
            "AND c.notification_id=d.notification_id)", (task, sid))
        # Consume the actual delivered return through its real receipt before
        # a later initiating turn submits done. A return alone cannot finish it.
        decision = (dict(action="workflow_wait", run_ids=[],
                         consume_notifications=[r["notification_id"] for r in delivered]) if delivered else
                    dict(action="done", summary="Synthetic workflow returned once"))
        payload.update(decision=decision,
                       manager_self_evaluation=assessment)
        _callback(private, payload, name=sid + ("-consume" if delivered else "-done"))
        return
    else:
        payload.update(decision=dict(action="workflow_wait", run_ids=[r["id"] for r in runs if r["state"] != "finished"]),
                       manager_self_evaluation=assessment)
        _callback(private, payload, name=sid + "-wait")
        return
    command = dict(format="workflow-sequential-control@2", run_id=run["id"],
        operation_key="U3-" + sid, expected_cas=run["cas"], expected_held_event_id=run["held_event_id"],
        action=action, reason="Synthetic initiator observes prior effects; no effect repetition")
    if action == "rebrief":
        command["supplement"] = SUPPLEMENT
    if action == "reassign":
        command["target_agent_id"] = case["alternate"]
    payload.update(decision=dict(action="workflow_control", workflow=command), manager_self_evaluation=assessment)
    controls = _rows(root, "SELECT * FROM workflow_seq_controls WHERE run_id=?", (run["id"],))
    exhausted = bool(controls) and action != "cancel"
    before = _closed_counts(root, private, task, sid)
    result = _callback(private, payload, name=sid + "-control",
                       expected=None if action == "reassign" and case.get("coding") else not exhausted)
    if action == "reassign" and case.get("coding") and result.returncode:
        assert b"sequential_assignment_team_scope" in result.stdout + result.stderr, (result.stdout + result.stderr)[:2048]
        after = _assert_counts(root, task, before, "ineligible manager reassignment allocated state")
        _receipt(private, "reassign-refusal", action=action, run_id=run["id"], before=before, after=after,
                 positive_reassignment_lifecycle="NOT RUN: no second eligible manager in this team")
        _wait(lambda: (private / "release-source").exists(), label="reassignment refusal readback", timeout=30)
        return
    if exhausted:
        after = _assert_counts(root, task, before, "recoveries:1 exhaustion allocated result/task/visit/SID/effect")
        _receipt(private, "exhaustion", run_id=run["id"], actor_task_id=task, actor_sid=sid,
                 action=action, before=before, after=after)
        # Keep this genuine initiating session alive for readback, then let the
        # fixture's owned daemon stop retire it. No invented result is submitted.
        _wait(lambda: (private / "release-source").exists(), label="exhaustion readback", timeout=30)


def _actor(argv: list[str]) -> None:
    binding = _binding()
    private, root = Path(binding["private"]), Path(binding["org_root"])
    task, sid, agent, slug = argv
    assert slug == "test"
    raw = sys.stdin.buffer.read()
    prompt = raw.decode("utf8")
    (private / (sid + ".prompt")).write_bytes(raw)
    _receipt(private, sid + "-entry", task_id=task, session_id=sid, agent=agent,
             prompt_bytes=len(raw), prompt_sha256=hashlib.sha256(raw).hexdigest())
    case = json.loads((private / "case.json").read_bytes())
    payload = dict(task_id=task, session_id=sid, agent=agent, status="completed",
                   confidence=90, summary="Explicit synthetic U3 report")
    visits = _rows(root, "SELECT * FROM workflow_seq_visits WHERE task_id=?", (task,))
    if not visits:
        _source_actor(private, root, case, payload, prompt)
        return
    assert len(visits) == 1
    visit = visits[0]
    assert not _rows(root, "SELECT * FROM authority_policy_v2_session_bindings WHERE root_task_id=?", (task,)), \
        "sequential visit received an ordinary v2 binding"
    assert POLICY_BEGIN not in prompt and POLICY_END not in prompt, "sequential visit received ordinary policy instruction"
    node_data = json.loads(prompt.split("Sequential node data (untrusted authored JSON):\n", 1)[1].splitlines()[0])
    assert node_data["node"] == visit["node_id"]
    if visit["node_id"] == "finalize" and case.get("recovery"):
        controls = _rows(root, "SELECT * FROM workflow_seq_controls WHERE run_id=?", (visit["run_id"],))
        if not controls:
            sink = private / "effects.receipt.json"
            assert not sink.exists(), "original synthetic effect repeated"
            _receipt(private, "effects", synthetic=True, effect="effect-receipt-1", head="1" * 40, merge="2" * 40,
                     task_id=task, sid=sid, count=1)
            return  # Real process clean exit, intentionally no callback.
        _assert_recovery_prompt(root, private, visit, raw, case)
        if not case.get("reconcile_success"):
            return  # Second genuine missing callback exhausts the unchanged allowance.
    if case.get("missing") and visit["ordinal"] == 1:
        if case["missing"] == "refused":
            payload["summary"] = "malformed synthetic generic callback"
            _callback(private, payload, name=sid + "-malformed", expected=False)
        return
    if case.get("cancel_queued") and "sibling" in node_data["original_request"]:
        return
    outcome = {"draft": "produced", "develop": "produced", "review": "APPROVE",
               "qa": "PASS", "finalize": "merged"}[visit["node_id"]]
    findings = []
    if case.get("block_first") and visit["ordinal"] == 1:
        outcome, payload["status"] = "BLOCK", "blocked"
        findings = ["U3 original causal blocker"]
    if case.get("revision") and visit["node_id"] == "review" and visit["ordinal"] == 2:
        outcome, findings = "REQUEST_CHANGES", ["U3-attributed-finding: add the detail"]
    if case.get("revision") and visit["node_id"] == "develop" and visit["ordinal"] > 1:
        assert "U3-attributed-finding" in prompt, "returned findings absent from actual revised prompt"
        assert node_data["remaining_limits"]["nodes"]["develop"]["revisions"] == 1
    content = (f"Synthetic immutable document {visit['ordinal']} 中文".encode() if visit["node_id"] == "draft" else
        _bytes(dict(synthetic=True, evidence_limits=["No real repository, provider or merge verification"],
                    verdict=outcome, head="1" * 40, observation="read-only")))
    payload["summary"] = _bytes(dict(format="workflow-sequential-callback@2", outcome=outcome,
        content_base64=base64.b64encode(content).decode(), findings=findings)).decode()
    if case.get("assessment_on_visit") and visit["ordinal"] == 1:
        for name, value in (("null", None), ("object", {})):
            before = _closed_counts(root, private, task, sid)
            result = _callback(private, dict(payload, manager_self_evaluation=value),
                               name=sid + "-assessment-" + name, expected=False)
            message = result.stdout + result.stderr
            assert b"Error (400):" in message and b"manager_self_evaluation_not_available" in message
            _assert_counts(root, task, before, "supplied sequential assessment allocated state")
    _callback(private, payload, name=sid + "-node")


def _assert_recovery_prompt(root: Path, private: Path, visit: dict, raw: bytes, case: dict) -> None:
    # Independent oracle: captured original process stdin; no renderer helper is
    # called to produce expected restriction text or expected lineage IDs.
    prompt = raw.decode()
    system, authored = prompt.split("Sequential node data (untrusted authored JSON):\n", 1)
    assert "authorized only to read existing evidence, observe existing external state" in system, \
        "replacement actual prompt lost trusted read-only restriction"
    assert "Any authored responsibility to merge, publish, deploy or repeat a mutation is suspended." in system
    assert "Use the existing guarded non-admin expected-head merge" in authored
    assert "Trusted recovery lineage (system JSON; identities and cause kinds only):\n" in system, \
        "replacement actual prompt lost authenticated lineage"
    lineage = json.loads(system.split("Trusted recovery lineage (system JSON; identities and cause kinds only):\n", 1)[1].splitlines()[0])
    control, = _rows(root, "SELECT * FROM workflow_seq_controls WHERE new_visit_id=?", (visit["id"],))
    held, = _rows(root, "SELECT * FROM workflow_seq_events WHERE id=?", (control["expected_held_event_id"],))
    assert any(item.get("control_id") == control["id"] and item.get("control_event_id") == control["event_id"]
               and item.get("action") == case["recovery"] for item in lineage)
    assert any(item.get("held_event_id") == held["id"] and item.get("predecessor_visit_id") == held["source_visit_id"]
               and item.get("cause_kind") == "missing_callback" for item in lineage), lineage
    if case["recovery"] == "rebrief":
        assert SUPPLEMENT in system, "admitted supplement absent from replacement process prompt"
    else:
        assert "Initiator supplement (authored JSON):" not in system, "plain control invented supplement"
    assert not _rows(root, "SELECT * FROM authority_policy_v2_attempts WHERE root_task_id=?", (visit["task_id"],))
    _receipt(private, "replacement-prompt", task_id=visit["task_id"], session_id=visit["session_id"],
             run_id=visit["run_id"], visit_id=visit["id"], control_id=control["id"], held_event_id=held["id"],
             predecessor_visit_id=held["source_visit_id"], prompt_sha256=hashlib.sha256(raw).hexdigest(),
             readonly=True, supplement=case["recovery"] == "rebrief", lineage=lineage,
             restriction_excerpt=system[system.index("Trusted recovery restriction:"):].splitlines()[0])


def _assert_control_actor(root: Path, control: dict, source: str) -> None:
    result, = _rows(root, "SELECT * FROM task_results WHERE id=?", (control["actor_result_id"],))
    assert (result["task_id"], result["agent"], result["session_id"], result["status"]) == (
        source, control["actor_principal"], control["actor_session_id"], "completed")
    decision = json.loads(result["decision_json"])
    assert decision["action"] == "workflow_control"
    command = decision["workflow"]
    assert (command["run_id"], command["action"], command["expected_held_event_id"], command["expected_cas"]) == (
        control["run_id"], control["action"], control["expected_held_event_id"], control["expected_cas"])
    _assessment(root, source, control["actor_session_id"])
    attempt, = _rows(root, "SELECT * FROM authority_policy_v2_attempts WHERE result_id=?", (result["id"],))
    assert (attempt["root_task_id"], attempt["manager_session_id"]) == (source, control["actor_session_id"])
    run, = _rows(root, "SELECT * FROM workflow_seq_runs WHERE id=?", (control["run_id"],))
    assert control["actor_session_id"] != run["source_session_id"], "control reused the start turn"


class Lifecycle:
    def __init__(self, port: str, root: Path, private: Path, plan: Path) -> None:
        from runtime.daemon import paths
        from tests.integration.conftest import seed_workspace
        self.root, self.private = root, private
        self.polls, self.busy_count, self.busy_seconds = 0, 0, 0.0
        self.base = f"http://127.0.0.1:{port}/api/v1/orgs/test"
        self.headers = {"authorization": "Bearer " + paths.read_token(), "x-happyranch-surface": "cli"}
        for agent in ("engineering_head", "dev_agent", "code_reviewer", "qa_engineer", "payment_agent", "content_manager"):
            seed_workspace(root, agent)
        plan.write_text("#!/usr/bin/env bash\nset -euo pipefail\npython " + shlex.quote(str(Path(__file__).resolve())) + ' --actor "$@"\n')
        self.script = Path(__file__).resolve().parents[2] / "scripts/daemon.sh"

    def request(self, method: str, path: str, body: dict | None = None, *, status: int = 200, poll: bool = False) -> dict:
        # Settings consumes canonical JSON bytes, as the public CLI does.
        headers = dict(self.headers)
        if body is not None:
            headers["content-type"] = "application/json"
        started = time.monotonic()
        response = httpx.request(method, self.base + path, content=_bytes(body) if body is not None else None,
                                 headers=headers, timeout=10)
        if poll:
            self.polls += 1
            if response.status_code == 503 and response.json() == {"detail": "census_database_busy"}:
                self.busy_count += 1
                self.busy_seconds += time.monotonic() - started
                self.export_polls()
                raise _CensusBusy
            self.export_polls()
        assert response.status_code == status, (method, path, response.status_code, response.text[:2048])
        return response.json()

    def export_polls(self) -> None:
        _receipt(self.private, "polling", polls=self.polls, busy_count=self.busy_count,
                 busy_seconds=self.busy_seconds,
                 possible_lock_issue=self.busy_count > self.polls / 2 or self.busy_seconds > 30)

    def configure(self, *, coding: bool = False, **case: object) -> str:
        from runtime.workflows.sequential_release import generic_start_catalog
        identity = "release/happyranch/" + ("coding" if coding else "draft-review")
        compiled, = [c for c in generic_start_catalog() if c.pin.identity == identity]
        if coding:
            assert next(n for n in compiled.definition.nodes if n.id == "finalize").limits.recoveries == 1
        policy_path = "/agents/engineering_head/team-escalation-policy"
        selector = self.request("GET", policy_path)
        self.request("POST", policy_path + "/v2/releases", dict(policy_id="u3-policy", title="Synthetic U3 policy",
            create_request_id="U3-create", activation_request_id="U3-activate", action="bootstrap",
            based_on_selector_id=selector["selector_id"], expected_selector_id=selector["selector_id"],
            what_to_escalate="Uncertain authorization", what_not_to_escalate="Bounded synthetic observation",
            acknowledge_shared_credential_attribution=True), status=201)
        bindings = (dict(developer="dev_agent", reviewer="code_reviewer", qa="qa_engineer", final_manager="engineering_head")
                    if coding else dict(author="dev_agent", reviewer="code_reviewer"))
        limits = dict(max_visits=16, max_events=4096, max_recoveries=4 if coding else 2,
                      node_revisions={n.id: n.limits.revisions for n in compiled.definition.nodes})
        save = dict(format="workflow-sequential-settings-put@2", operation_key="U3-save", expected_generation=0,
            definition=compiled.pin.model_dump(mode="json"), bindings=[dict(role=k, principal=v) for k, v in bindings.items()],
            limits=limits)
        saved = self.request("PUT", "/workflows/sequential/settings/v2", save)
        assert saved["original"]["snapshot"]["admission"] == "Disabled"
        allowed = self.request("PUT", "/workflows/sequential/settings/admission", dict(format="workflow-sequential-admission@1",
            operation_key="U3-allow", expected_generation=1, definition=save["definition"], admission="Allowed"))["original"]
        member = dict(definition=identity + ("@3" if coding else "@1"),
            settings={k: allowed[k] for k in ("generation", "snapshot_digest")}, operation_key="U3-start",
            request=REQUEST, inputs=[], role_overrides=[], step_overrides=[], lower_limits=limits)
        members = [member]
        if case.get("cancel_queued"):
            members.append(dict(member, operation_key="U3-sibling", request=REQUEST + " sibling"))
        self.case = dict(case, coding=coding, compose=dict(format="workflow-sequential-start-compose@2", workflows=members))
        self.update()
        task = self.request("POST", "/tasks", dict(brief=REQUEST, team="engineering"))["task_id"]
        self.source = task
        return task

    def update(self, **changes: object) -> None:
        self.case.update(changes)
        _save(self.private / "case.json", self.case)

    def rows(self, sql: str, args: tuple = ()) -> list[dict]:
        return _rows(self.root, sql, args)

    def run(self) -> dict:
        rows = self.rows("SELECT * FROM workflow_seq_runs ORDER BY rowid")
        return rows[0] if rows else {}

    def wait_run(self, state: str) -> dict:
        return _wait(self.run, lambda r: r.get("state") == state, label="workflow state " + state)

    def mailbox(self, action: str = "cycles") -> dict:
        nonce = "observation-" + str(time.time_ns())
        _save(self.private / "observer-command.json", dict(nonce=nonce, action=action))
        path = self.private / (nonce + ".receipt.json")
        result = _wait(lambda: json.loads(path.read_bytes()) if path.exists() else None,
                       label="finite observer mailbox", timeout=20)
        assert result.get("completed"), result
        return result

    def assert_inbox(self, count: int) -> None:
        inbox = self.rows("SELECT * FROM workflow_seq_inbox WHERE run_id=?", (self.run()["id"],))
        assert len(inbox) == count and len({r["event_id"] for r in inbox}) == count
        assert all(r["initiator_task_id"] == self.source for r in inbox)

    def assert_history(self) -> None:
        from runtime.workflows.sequential_history import read_sequential_history
        with closing(sqlite3.connect((self.root / "happyranch.db").as_uri() + "?mode=ro", uri=True)) as conn:
            conn.row_factory = sqlite3.Row
            read_sequential_history(conn, expected_org_slug="test")

    def assert_return(self) -> None:
        self.wait_run("finished")
        _wait(lambda: self.request("GET", "/tasks/" + self.source, poll=True)["task"],
              lambda t: t["status"] == "completed", label="ordinary initiator finished")
        self.assert_history()
        assert len(self.rows("SELECT * FROM workflow_seq_source_wait_sets")) == 1
        assert self.rows("SELECT * FROM workflow_seq_consumed_notifications"), "initiator did not consume causal return"

    def wait_source_closure(self, *, after_result: int) -> dict:
        def read() -> dict | None:
            results = self.rows("SELECT * FROM task_results WHERE task_id=? AND id>? ORDER BY id DESC",
                                (self.source, after_result))
            if not results:
                return None
            result = results[0]
            path = self.private / ("source-closure-" + result["session_id"] + ".receipt.json")
            if not path.exists():
                return None
            closure = json.loads(path.read_bytes())
            assert closure["positive"], closure
            assert closure["source_status"] not in ("failed", "cancelled", "superseded"), closure
            assert json.loads(result["decision_json"])["action"] == "workflow_wait", result
            return closure
        return _wait(read, label="source committed wait and original native closure before second stop")

    def export_cancellation_cause(self) -> list[dict]:
        events = self.rows("SELECT id,run_id,event_bytes FROM workflow_seq_events WHERE kind='cancellation_finished'")
        cancellations = [dict(id=e["id"], run_id=e["run_id"], payload=json.loads(e["event_bytes"])) for e in events]
        source, = self.rows("SELECT id,status,current_session_id FROM tasks WHERE id=?", (self.source,))
        results = self.rows("SELECT id,session_id,status,decision_json FROM task_results WHERE task_id=?", (self.source,))
        _receipt(self.private, "restart-cause", source_status=source, cancellations=cancellations, source_results=results)
        return cancellations

    def restart(self) -> None:
        from tests.integration.conftest import _nested_daemon_env
        subprocess.run([str(self.script), "stop"], check=True, env=_nested_daemon_env(), timeout=30)
        subprocess.run([str(self.script), "start"], check=True, env=_nested_daemon_env(), timeout=15)
        from runtime.daemon import paths
        port = paths.port_file().read_text().strip()
        self.base = f"http://127.0.0.1:{port}/api/v1/orgs/test"
        _wait(lambda: httpx.get(f"http://127.0.0.1:{port}/api/v1/health", timeout=1).status_code,
              lambda s: s == 200, label="same-home restarted daemon health")


@pytest.fixture
def lifecycle(live_daemon, runtime, workflow_lifecycle_capture, fake_claude_plan_env):
    value = Lifecycle(live_daemon, runtime, workflow_lifecycle_capture, fake_claude_plan_env)
    try:
        yield value
    finally:
        value.export_polls()
        (value.private / "release-source").touch()


@pytest.mark.parametrize("variant", ["omitted", "mismatch", "valid-start"])
def test_real_policy_diagnostics(lifecycle, variant: str) -> None:
    """B3: advisory evidence preserves start admission, replay and traversal."""
    lifecycle.configure(policy_variant=variant)
    lifecycle.assert_return()
    lifecycle.assert_inbox(1)
    run = lifecycle.run()
    assert run["source_task_id"] == lifecycle.source
    observed = _policy_diagnostics(lifecycle.root, lifecycle.source, run["source_session_id"], variant)
    assert json.loads((lifecycle.private / "policy-diagnostic.receipt.json").read_bytes())["result_id"] == observed["result_id"]
    visits = lifecycle.rows("SELECT * FROM workflow_seq_visits ORDER BY ordinal")
    assert [v["node_id"] for v in visits] == ["draft", "review"]
    assert all(v["session_id"] and v["result_id"] is not None and v["state"] == "completed" for v in visits)
    assert not lifecycle.rows("SELECT * FROM workflow_seq_controls")


def test_real_visit_assessment_refusal(lifecycle) -> None:
    """U1: assessment field presence refuses HTTP400 without allocations."""
    lifecycle.configure(assessment_on_visit=True)
    lifecycle.assert_return()
    lifecycle.assert_inbox(1)
    receipts = sorted(lifecycle.private.glob("*-assessment-*.receipt.json"))
    assert len(receipts) == 2
    for path in receipts:
        receipt = json.loads(path.read_bytes())
        assert receipt["exit"] != 0
        assert "Error (400):" in receipt["stdout"] + receipt["stderr"]
        assert "manager_self_evaluation_not_available" in receipt["stdout"] + receipt["stderr"]


@pytest.mark.parametrize("revision", [False, True], ids=["E1", "E1-revision"])
def test_real_return(lifecycle, revision: bool) -> None:
    lifecycle.configure(coding=revision, revision=revision)
    lifecycle.assert_return()
    lifecycle.assert_inbox(1)
    if revision:
        visits = lifecycle.rows("SELECT * FROM workflow_seq_visits ORDER BY ordinal")
        assert [v["node_id"] for v in visits] == ["develop", "review", "develop", "review", "qa", "finalize"]
        assert json.loads(visits[2]["input_selection_bytes"]) == {"outputs": []}, "dependent selections were not retired"
        counter, = lifecycle.rows("SELECT * FROM workflow_seq_node_counters WHERE node_id='develop'")
        assert counter["revisions"] == 1


@pytest.mark.parametrize("action", ["retry", "rebrief", "reassign"], ids=["E2-retry", "E2-rebrief", "E2-reassign"])
def test_real_block_control(lifecycle, action: str) -> None:
    lifecycle.configure(block_first=True, recovery=action, alternate="payment_agent")
    lifecycle.assert_return()
    lifecycle.assert_inbox(2)
    run = lifecycle.run()
    assert run["recovery_count"] == 1 and run["visit_count"] == 3
    control, = lifecycle.rows("SELECT * FROM workflow_seq_controls")
    assert control["action"] == action and control["actor_task_id"] == lifecycle.source
    _assert_control_actor(lifecycle.root, control, lifecycle.source)
    replacement, = lifecycle.rows("SELECT * FROM workflow_seq_visits WHERE id=?", (control["new_visit_id"],))
    assert replacement["principal"] == ("payment_agent" if action == "reassign" else "dev_agent")
    original, = lifecycle.rows("SELECT * FROM task_results WHERE task_id=(SELECT task_id FROM workflow_seq_visits WHERE ordinal=1)")
    assert original["status"] == "blocked" and "U3 original causal blocker" in original["output_summary"]


@pytest.mark.parametrize("missing", ["clean", "refused"], ids=["E3-clean", "E3-refused"])
def test_real_missing_callback(lifecycle, missing: str) -> None:
    lifecycle.configure(missing=missing)
    run = lifecycle.wait_run("held")
    lifecycle.assert_inbox(1)
    assert not lifecycle.rows("SELECT * FROM task_results WHERE task_id IN (SELECT task_id FROM workflow_seq_visits)")
    assert not lifecycle.rows("SELECT * FROM workflow_seq_outputs")
    event, = lifecycle.rows("SELECT * FROM workflow_seq_events WHERE id=?", (run["held_event_id"],))
    assert json.loads(event["event_bytes"])["cause"] == "missing_callback"
    lifecycle.assert_history()
    if missing == "refused":
        receipt = _wait(lambda: list(lifecycle.private.glob("*-malformed.receipt.json")), label="refused callback receipt")
        refused = json.loads(receipt[0].read_bytes())
        assert refused["exit"] != 0
        assert "workflow_sequential_callback_invalid" in refused["stdout"] + refused["stderr"]


def test_real_original_manager_final(lifecycle) -> None:
    """E4-O: original Final has no ordinary policy but source retains v2."""
    lifecycle.configure(coding=True)
    lifecycle.assert_return()
    final, = lifecycle.rows("SELECT * FROM workflow_seq_visits WHERE node_id='finalize'")
    assert final["result_id"] is not None
    assert not lifecycle.rows("SELECT * FROM authority_policy_v2_session_bindings WHERE root_task_id=?", (final["task_id"],))


@pytest.mark.parametrize("action,exhaustion,success", [
    ("retry", "retry", False), ("rebrief", "retry", False), ("rebrief", "rebrief", False),
    ("reassign", "retry", False), ("rebrief", "retry", True),
], ids=["E4-R-retry", "E4-R-rebrief-retry-exhausted", "E4-R-rebrief-exhausted", "E4-R-reassign", "E4-R-reconciled"])
def test_real_final_recovery(lifecycle, action: str, exhaustion: str, success: bool) -> None:
    lifecycle.configure(coding=True, recovery=action, exhaustion_action=exhaustion,
                        alternate="content_manager", reconcile_success=success)
    if action == "reassign":
        refusal = lifecycle.private / "reassign-refusal.receipt.json"
        _wait(lambda: refusal.exists() or (lifecycle.private / "replacement-prompt.receipt.json").exists(),
              label="real manager reassignment disposition", timeout=60)
        if refusal.exists():
            observed = json.loads(refusal.read_bytes())
            assert observed["before"] == observed["after"]
            assert not lifecycle.rows("SELECT * FROM workflow_seq_controls")
            assert len(lifecycle.rows("SELECT * FROM workflow_seq_visits WHERE node_id='finalize'")) == 1
            lifecycle.assert_inbox(1)
            lifecycle.assert_history()
            return
    def captured_replacement() -> tuple | None:
        visits = lifecycle.rows("SELECT * FROM workflow_seq_visits WHERE node_id='finalize' ORDER BY ordinal")
        if len(visits) != 2 or visits[1]["session_id"] is None:
            return None
        path = lifecycle.private / (visits[1]["session_id"] + ".prompt")
        return (visits[1], path.read_bytes()) if path.exists() else None
    captured, raw = _wait(captured_replacement, label="replacement process stdin captured", timeout=60)
    # Assert in the pytest process, too: a renderer mutation must fail at this
    # exact byte oracle, never be misreported as a wait/fixture failure because
    # the fake process correctly refused to proceed on a broken instruction.
    _assert_recovery_prompt(lifecycle.root, lifecycle.private, captured, raw, lifecycle.case)
    prompt = lifecycle.private / "replacement-prompt.receipt.json"
    observed = json.loads(prompt.read_bytes())
    assert observed["readonly"] and observed["supplement"] == (action == "rebrief")
    run = lifecycle.run()
    controls = lifecycle.rows("SELECT * FROM workflow_seq_controls")
    assert len(controls) == 1 and controls[0]["actor_task_id"] == lifecycle.source
    _assert_control_actor(lifecycle.root, controls[0], lifecycle.source)
    original, replacement = lifecycle.rows("SELECT * FROM workflow_seq_visits WHERE node_id='finalize' ORDER BY ordinal")
    assert original["task_id"] != replacement["task_id"] and original["session_id"] != replacement["session_id"]
    assert original["run_id"] == replacement["run_id"] == run["id"] and original["result_id"] is None
    assert not lifecycle.rows("SELECT * FROM task_results WHERE task_id=?", (original["task_id"],))
    assert len(list(lifecycle.private.glob("effects.receipt.json"))) == 1
    assert json.loads((lifecycle.private / "effects.receipt.json").read_bytes())["count"] == 1
    if success:
        lifecycle.assert_return()
        lifecycle.assert_inbox(2)
    else:
        exhausted = _wait(lambda: json.loads((lifecycle.private / "exhaustion.receipt.json").read_bytes())
                          if (lifecycle.private / "exhaustion.receipt.json").exists() else None,
                          label="genuine source exhaustion refusal")
        assert exhausted["before"] == exhausted["after"]
        lifecycle.wait_run("held")
        lifecycle.assert_inbox(2)
        lifecycle.assert_history()
    assert lifecycle.run()["recovery_count"] == 1
    assert len(lifecycle.rows("SELECT * FROM workflow_seq_controls WHERE new_visit_id=?", (replacement["id"],))) == 1
    assert len(lifecycle.rows("SELECT * FROM workflow_seq_visits WHERE node_id='finalize'")) == 2


@pytest.mark.parametrize("fault", ["closed-before-advance", "before-outer"], ids=["E5", "E6"])
def test_real_restart_window(lifecycle, fault: str) -> None:
    lifecycle.configure(missing="clean", fault=fault)
    _wait(lambda: lifecycle.rows("SELECT * FROM workflow_seq_evidence WHERE kind='terminal'"), label="original host terminal")
    _wait(lambda: "\"kind\": \"" + fault + "\"" in (lifecycle.private / "observer.receipt.json").read_text(),
          label="original closure fault window")
    before = lifecycle.run()
    assert before["state"] == "running"
    lifecycle.update(fault="none")
    lifecycle.restart()
    if fault == "closed-before-advance":
        lifecycle.wait_run("held")
        lifecycle.wait_source_closure(after_result=before["source_result_id"])
        lifecycle.mailbox()
        lifecycle.restart()
        cancellations = lifecycle.export_cancellation_cause()
        assert not cancellations, {"STOP": "possible production defect: cancellation after healthy closed source",
                                   "cancellations": cancellations}
        lifecycle.assert_inbox(1)
        held = lifecycle.rows("SELECT id FROM workflow_seq_events WHERE kind='held'")
        inbox = lifecycle.rows("SELECT event_id FROM workflow_seq_inbox")
        assert len(held) == 1 and [r["event_id"] for r in inbox] == [held[0]["id"]]
        lifecycle.assert_history()
    else:
        lifecycle.mailbox()
        assert lifecycle.run()["state"] == "running"
        lifecycle.assert_inbox(0)
        lifecycle.mailbox("alarm")
        from runtime.daemon import paths
        log = paths.log_file().read_text()
        assert "sequential_stall" in log and "reason=closure_unavailable" in log
        assert not lifecycle.rows("SELECT * FROM workflow_seq_controls")


@pytest.mark.parametrize("fault,held", [
    ("P0", True), ("P1-raise", True), ("P1-return", True), ("U0", False),
    ("U1-integrity", False), ("U2-return", False), ("U2-raise", False), ("P2-unproved", False),
    ("U3", False), ("U4", False), ("U5-after-spawn", None), ("host-unavailable-return", False),
    ("P1-close", False), ("P1-release", False), ("P1-receipt", False),
], ids=lambda value: "E7-" + value if isinstance(value, str) else None)
def test_real_native_failure(lifecycle, fault: str, held: bool | None) -> None:
    lifecycle.configure(fault=fault, missing="clean")
    _wait(lambda: lifecycle.rows("SELECT * FROM audit_log WHERE action='workflow_seq_native_launch_failure'")
          or lifecycle.run().get("state") == "held",
          label="original native disposition")
    if held is None:
        # U5 may use T only when the original host/native tail supplied both
        # authentic receipts. It is never inferred from a failed spawn boolean.
        held = lifecycle.run()["state"] == "held"
        if held:
            assert {r["kind"] for r in lifecycle.rows("SELECT * FROM workflow_seq_evidence")} >= {"terminal", "outer"}
    if held:
        lifecycle.wait_run("held")
    else:
        _wait(lambda: lifecycle.rows("SELECT payload FROM audit_log WHERE action='workflow_seq_native_launch_failure' ORDER BY id"),
              lambda rows: bool(rows) and json.loads(rows[-1]["payload"])["stage"] == "unsettled",
              label="original native unsettled disposition committed")
    before = lifecycle.rows("SELECT task_id,payload FROM audit_log WHERE action='workflow_seq_native_launch_failure' ORDER BY id")
    observed = lifecycle.mailbox()
    assert len(observed["snapshots"]) == 5
    assert all(s == observed["initial"] for s in observed["snapshots"]), \
        "five discovery cycles changed original native identities, journal, reservation or failure disposition"
    initial = observed["initial"]
    assert len(initial["preparations"]) == 1, "original visit prepared more than once"
    preparation = initial["preparations"][0]
    assert len({r["prospective_session_id"] for r in initial["failure_identities"]}) == int(bool(before))
    assert all((r["invocation_id"], r["prospective_session_id"]) ==
               (preparation["invocation_id"], preparation["session_id"])
               for r in initial["failure_identities"])
    if held and fault != "U5-after-spawn":
        assert initial["journal_count"] == 0 and not initial["reservations"] and not initial["failure_owners"]
    after = lifecycle.rows("SELECT task_id,payload FROM audit_log WHERE action='workflow_seq_native_launch_failure' ORDER BY id")
    assert before == after and len(after) <= 2, "five redeliveries grew native dispositions"
    assert not lifecycle.rows("SELECT * FROM task_results WHERE task_id IN (SELECT task_id FROM workflow_seq_visits)")
    assert not list(lifecycle.private.glob("*-node.receipt.json"))
    starts = lifecycle.rows("SELECT * FROM audit_log WHERE action='session_start' AND task_id IN (SELECT task_id FROM workflow_seq_visits)")
    assert len(starts) == int(fault in ("U4", "U5-after-spawn"))
    lifecycle.assert_inbox(int(held))
    if held:
        if fault != "U5-after-spawn":
            assert json.loads(after[-1]["payload"])["stage"] == "closed"
        lifecycle.assert_history()
    else:
        assert lifecycle.run()["state"] == "running"
        assert json.loads(after[-1]["payload"])["stage"] == "unsettled"
        lifecycle.mailbox("alarm")
        from runtime.daemon import paths
        assert "sequential_stall" in paths.log_file().read_text()


def test_real_render_refusal(lifecycle) -> None:
    """E7-render: real refusal owner caps at three before any preparation."""
    lifecycle.configure(fault="render")
    _wait(lambda: lifecycle.run(), label="admitted run")
    lifecycle.mailbox()
    lifecycle.wait_run("held")
    lifecycle.mailbox()
    observations = (lifecycle.private / "observer.receipt.json").read_text()
    assert observations.count('"kind": "render-refusal"') == 3
    assert not lifecycle.rows("SELECT * FROM audit_log WHERE action='session_start' AND task_id IN (SELECT task_id FROM workflow_seq_visits)")
    assert not lifecycle.rows("SELECT * FROM workflow_seq_evidence WHERE visit_id IS NOT NULL")
    source_outer, = lifecycle.rows("SELECT * FROM workflow_seq_evidence WHERE visit_id IS NULL")
    source_body = json.loads(source_outer["evidence_bytes"])
    assert source_outer["kind"] == "outer" and source_body["format"] == "workflow-sequential-source-outer@2"
    assert source_body["task_id"] == lifecycle.source and source_body["positive"] is True
    assert not lifecycle.rows("SELECT * FROM task_pause_controls p,json_each(p.admission_journal_json,'$.entries') e "
                             "WHERE json_extract(e.value,'$.task_id') IN (SELECT task_id FROM workflow_seq_visits)")
    assert not lifecycle.rows("SELECT * FROM task_results WHERE task_id IN (SELECT task_id FROM workflow_seq_visits)")
    refusals = lifecycle.rows("SELECT payload FROM audit_log WHERE action='workflow_sequential_preparation_free_refusal' ORDER BY id")
    assert [json.loads(r["payload"])["count"] for r in refusals] == [1, 2, 3]
    assert len({json.loads(r["payload"])["session_id"] for r in refusals}) == 3
    assert '"kind": "prepare-entry"' not in observations
    lifecycle.assert_inbox(1)
    lifecycle.assert_history()


def test_real_cancel_before_launch(lifecycle) -> None:
    """E8: sibling Held wakes the genuine source while target stays pristine."""
    lifecycle.configure(cancel_queued=True, fault="cancel-barrier")
    _wait(lambda: lifecycle.run(), label="queued target")
    # The barrier is scoped to the target; the sibling's clean missing callback
    # supplies a genuine source continuation for the cancel control.
    _wait(lambda: lifecycle.rows("SELECT * FROM workflow_seq_controls WHERE action='cancel' AND run_id=?", (lifecycle.run()["id"],)),
          label="genuine initiating cancellation")
    control, = lifecycle.rows("SELECT * FROM workflow_seq_controls WHERE action='cancel' AND run_id=?", (lifecycle.run()["id"],))
    _assert_control_actor(lifecycle.root, control, lifecycle.source)
    lifecycle.update(fault="none")
    lifecycle.wait_run("finished")
    lifecycle.mailbox()
    target = lifecycle.run()
    assert target["terminal_outcome"] == "cancelled"
    visits = lifecycle.rows("SELECT * FROM workflow_seq_visits WHERE run_id=?", (target["id"],))
    assert len(visits) == 1 and visits[0]["session_id"] is None
    assert not lifecycle.rows("SELECT * FROM audit_log WHERE action='session_start' AND task_id=?", (visits[0]["task_id"],))
    receipt = json.loads(control["receipt_bytes"])
    assert receipt["format"] == "workflow-sequential-control-receipt@2"
    assert (receipt["run_id"], receipt["event_id"]) == (target["id"], control["event_id"])
    event, = lifecycle.rows("SELECT kind FROM workflow_seq_events WHERE id=?", (receipt["event_id"],))
    assert event["kind"] == "cancellation_finished"
    assert not lifecycle.rows("SELECT * FROM workflow_seq_evidence WHERE visit_id=?", (visits[0]["id"],))
    assert not lifecycle.rows("SELECT * FROM task_pause_controls p,json_each(p.admission_journal_json,'$.entries') e "
                             "WHERE json_extract(e.value,'$.task_id')=?", (visits[0]["task_id"],))
    observations = (lifecycle.private / "observer.receipt.json").read_text().splitlines()
    assert not [r for line in observations if (r := json.loads(line))["kind"] == "prepare-entry"
                and r["task_id"] == visits[0]["task_id"]]
    lifecycle.assert_inbox(0)
    _wait(lambda: lifecycle.rows("SELECT state FROM workflow_seq_runs"),
          lambda rows: len(rows) == 2 and all(r["state"] == "finished" for r in rows), label="both cancellation runs drained")
    sibling, = lifecycle.rows("SELECT id FROM workflow_seq_runs WHERE id!=?", (target["id"],))
    sibling_inbox = lifecycle.rows("SELECT event_id FROM workflow_seq_inbox WHERE run_id=?", (sibling["id"],))
    held, = lifecycle.rows("SELECT id FROM workflow_seq_events WHERE run_id=? AND kind='held'", (sibling["id"],))
    assert [r["event_id"] for r in sibling_inbox] == [held["id"]]
    lifecycle.assert_return()
    done, = [r for r in lifecycle.rows("SELECT id,decision_json FROM task_results WHERE task_id=?", (lifecycle.source,))
             if json.loads(r["decision_json"])["action"] == "done"]
    controls = lifecycle.rows("SELECT actor_result_id FROM workflow_seq_controls")
    assert len(controls) == 2 and all(r["actor_result_id"] < done["id"] for r in controls)
    lifecycle.assert_history()


if __name__ == "__main__":
    assert sys.argv[1] == "--actor"
    try:
        _actor(sys.argv[2:])
    except BaseException:
        _receipt(Path(_binding()["private"]), sys.argv[3] + "-actor-error", traceback=traceback.format_exc())
        raise
