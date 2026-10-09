"""Read-only task prompt headers and prior-step context.

The run_step facade re-exports these same function and list objects.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from runtime.models import TaskStatus

if TYPE_CHECKING:
    from runtime.orchestrator.orchestrator import Orchestrator


def _list_candidate_agents(orch: "Orchestrator", calling_manager: str) -> list[str]:
    """Return the names of workers the calling manager can delegate to.

    Only includes workers on the calling manager's own team that have an
    existing workspace on disk. Returns an empty list when the calling_manager
    is not found in the registry (e.g. fallback / tests without a full layout).
    """
    caller_team = orch.teams.team_for_manager(calling_manager)
    if caller_team is None:
        return []
    team_members = set(orch.teams.manager_for_team(caller_team).workers)

    if orch._paths.workspaces_dir.exists():
        names = sorted(
            d.name for d in orch._paths.workspaces_dir.iterdir()
            if d.is_dir() and d.name in team_members
        )
    else:
        names = []
    return names

# Shared discipline tail appended to both revisit headers. Addresses the
# brief-vs-reality divergence failure mode (TALK-028, tourism-org): on a
# revisit-spawned session the literal brief is often stale, and the manager
# tends either to (a) execute the brief verbatim and stall against current
# state, or (b) improvise "the next obvious step" and get blocked by
# classifiers/workflow gates. The discipline frames the binary choice
# (execute-with-divergence-note OR escalate-with-diagnosis) and explicitly
# bans improvisation. Generic enough for any manager role.
_REVISIT_DISCIPLINE_LINES = [
    "Status-assess before acting on the brief below — it was authored before this "
    "revisit and may be stale. Inspect the predecessor (commands above) and verify "
    "ground truth for the work the brief describes. Then either: execute the real "
    "next step, noting any divergence from the brief in your output_summary; or "
    "escalate with a precise diagnosis (what the brief asked, what reality is, why "
    "the gap is unbridgeable). Do NOT improvise — half-completed work blocks the "
    "workstream.",
]

def _revisit_header_if_applicable(orch: "Orchestrator", task_id: str) -> str | None:
    """Return a revisit context header, or None.

    Trigger: the task has a `revisit_of` OR `auto_revisit_of` audit entry
    AND no `orchestration_step` audit entry. The latter is how we detect
    "first step" without timestamps — once the task owner has produced
    a decision, `log_orchestration_step` writes a row and this helper
    returns None on every subsequent call.
    """
    logs = orch._db.get_audit_logs(task_id)
    revisit_entry = next(
        (e for e in logs if e["action"] in ("revisit_of", "auto_revisit_of")),
        None,
    )
    if revisit_entry is None:
        return None
    if any(e["action"] == "orchestration_step" for e in logs):
        return None

    if revisit_entry["action"] == "auto_revisit_of":
        return _auto_revisit_header(revisit_entry["payload"])

    payload = revisit_entry["payload"]
    predecessor = payload["predecessor_root"]
    flagged = payload["flagged"]
    prior_status = payload["prior_status"]
    cascade = payload.get("cascade") or [predecessor]
    note = payload.get("founder_note")

    lines = [
        f"REVISIT CONTEXT: this root is a revisit of {predecessor} "
        f"(which ended in {prior_status}).",
        f"Founder flagged {flagged} in the predecessor lineage — "
        "start your investigation there.",
        "Cascade chain (predecessor root -> flagged): "
        + " -> ".join(cascade),
    ]
    if note:
        lines.append(f"Founder note: {note}")
    lines.append(
        f"Inspect via: `happyranch details {predecessor}`, "
        f"`happyranch audit {predecessor}`, `happyranch recall {predecessor}`."
    )
    lines.append(
        "You may reuse successful sub-tasks' artifacts (referenced by path in "
        "new child briefs); old child task rows stay frozen."
    )
    lines.extend(_REVISIT_DISCIPLINE_LINES)

    # JOB summary block — list any jobs submitted by the predecessor.
    predecessor_logs = orch._db.get_audit_logs(predecessor)
    sr_entries = [e for e in predecessor_logs if e.get("action") == "job_submitted"]
    if sr_entries:
        lines.append("")
        lines.append("This task previously submitted jobs:")
        for e in sr_entries:
            payload_e = e.get("payload") or {}
            if isinstance(payload_e, str):
                import json as _json  # noqa: PLC0415

                try:
                    payload_e = _json.loads(payload_e)
                except Exception:
                    payload_e = {}
            job_id = payload_e.get("script_request_id", "JOB-?")
            title = payload_e.get("title", "(no title)")
            sr = orch._db.get_job(job_id) if job_id != "JOB-?" else None
            status = sr.status.value if sr else "?"
            marker = ""
            if sr and sr.status.value in ("pending", "running"):
                marker = " [still pending — founder action needed]"
            lines.append(f"  - {job_id} ({status}) — {title}{marker}")
        lines.append("")
        lines.append("Read the outputs / rejection reasons before continuing:")
        for e in sr_entries:
            payload_e = e.get("payload") or {}
            if isinstance(payload_e, str):
                import json as _json  # noqa: PLC0415

                try:
                    payload_e = _json.loads(payload_e)
                except Exception:
                    payload_e = {}
            job_id = payload_e.get("script_request_id", "JOB-?")
            lines.append(f"  happyranch jobs show {job_id}")
            lines.append(f"  happyranch jobs output {job_id}")

    return "\n".join(lines) + "\n\n"

def _auto_revisit_header(payload: dict) -> str:
    """Render the first-step header for an orchestrator-triggered auto-revisit.

    Different language from the founder-revisit header: the manager needs
    to know an opaque agent failure happened (not a founder-flagged
    problem) and to consider whether the original approach is still sound
    or whether the failure mode suggests a different decomposition.
    """
    predecessor = payload["predecessor_root"]
    failed_task = payload["failed_task"]
    failed_agent = payload["failed_agent"]
    cascade = payload.get("cascade") or [failed_task]
    err = payload.get("error_context") or {}
    attempt = payload.get("attempt", 1)
    failure_kind = payload.get("failure_kind") or "session_failed"

    err_bits: list[str] = []
    mode = err.get("mode")
    if mode == "exception":
        err_bits.append(f"exception: {err.get('detail', '?')}")
    elif mode == "session_failure":
        rc = err.get("rc")
        err_bits.append(f"rc={rc if rc is not None else '?'}")
        if err.get("missing_callback"):
            err_bits.append("no completion callback")
        executor_error = err.get("executor_error")
        if executor_error:
            err_bits.append(executor_error)
        stderr_tail = err.get("stderr_tail") or ""
        stdout_tail = err.get("stdout_tail") or ""
        preview = stderr_tail or stdout_tail
        if preview:
            label = "stderr" if stderr_tail else "stdout"
            err_bits.append(f"{label}: {preview.replace(chr(10), ' ')}")
    err_summary = "; ".join(err_bits) if err_bits else "(no diagnostics)"

    lines = [
        f"AUTO-REVISIT CONTEXT (orchestrator-triggered, kind={failure_kind}, "
        f"attempt {attempt}): "
        f"this root is a revisit of {predecessor}, "
        "spawned because an agent in the predecessor lineage hit an opaque "
        "failure.",
        f"Failed task: {failed_task} (agent: {failed_agent}).",
        f"Failure: {err_summary}",
        "Cascade chain (predecessor root -> failed task): "
        + " -> ".join(cascade),
        f"Inspect via: `happyranch details {predecessor}`, "
        f"`happyranch audit {predecessor}`, `happyranch recall {predecessor}`.",
        "Re-evaluate the approach — the failure may be transient (worth "
        "the same plan with a fresh subprocess) or structural (a different "
        "decomposition is needed). Decide accordingly.",
    ]
    lines.extend(_REVISIT_DISCIPLINE_LINES)
    return "\n".join(lines) + "\n\n"

def _resolved_escalation_header_if_applicable(
    orch: "Orchestrator", task_id: str,
) -> str | None:
    """Return a 2-3 line header on the first manager step after a founder
    `resolve-escalation --continue` OR an authority-policy same-root
    continuation, otherwise None.

    Trigger: the most recent `escalation_resolved` OR
    `authority_continued_same_root` audit entry for this task has a higher
    row id than the most recent `orchestration_step` entry — i.e. the
    continuation happened AND the manager hasn't run yet. Audit `id` is
    autoincrement, so id-ordering is equivalent to chronological ordering.
    Once the manager produces its first decision after re-enqueue,
    `log_orchestration_step` writes a row with a higher id and this helper
    returns None on every subsequent call.
    """
    logs = orch._db.get_audit_logs(task_id)
    last_resolved = None
    last_authority_continue = None
    last_step = None
    for entry in logs:
        action = entry["action"]
        if action == "escalation_resolved":
            last_resolved = entry
        elif action == "authority_continued_same_root":
            last_authority_continue = entry
        elif action == "orchestration_step":
            last_step = entry
    if last_authority_continue is not None and (
        last_step is None or last_step["id"] < last_authority_continue["id"]
    ):
        # THR-181 Track A (founder lifecycle envelope): the continuation header is the
        # continued turn's same-root lifecycle notice. Fail-closed: it is
        # shown ONLY while the single-use continuation envelope is ACTIVE for
        # the root — a stale/resolved/replayed continuation without a live
        # envelope never presents as a continuation (ordinary turn instead).
        if orch._db.get_active_authority_continue_envelope(task_id) is None:
            return None
        payload = last_authority_continue["payload"] or {}
        policy_id = payload.get("policy_id", "(unknown policy)")
        policy_version = payload.get("policy_version", "?")
        clause_id = payload.get("clause_id", "(unknown clause)")
        action = payload.get("action", "(unknown action)")
        return (
            f"AUTHORITY POLICY CONTINUED SAME ROOT: policy {policy_id} "
            f"v{policy_version} matched clause {clause_id} "
            f"(permitted action: {action}).\n"
            "Your proposed escalation was not committed; continue the same "
            "root within that permitted action.\n"
            "THIS TURN USES YOUR ORDINARY CONFIGURED EXECUTOR PERMISSIONS "
            "and normal manager-decision validation. The single-use lifecycle "
            "envelope is not an exact-action whitelist. Same-root identity, "
            "cancellation, replay, CAS, budgets, protected boundaries, and "
            "terminal audit remain daemon-owned; supersession, successor, "
            "revisit, and fresh-root replacement remain outside this grant.\n\n"
        )
    if last_resolved is None:
        return None
    if last_step is not None and last_step["id"] > last_resolved["id"]:
        return None
    payload = last_resolved["payload"] or {}
    decision = payload.get("decision", "continue")
    # Cancel is terminal — no resume header should ever fire for a cancelled
    # escalation.
    if decision == "cancel":
        return None
    rationale = payload.get("rationale", "(no rationale recorded)")
    return (
        f"ESCALATION RESOLVED: founder continued your prior escalation.\n"
        f"Rationale: {rationale}\n"
        "Continue from where you parked, with this verdict in mind.\n\n"
    )

def _build_prior_steps_from_db(orch: "Orchestrator", task_id: str):
    """Reconstruct StepRecord[] for the parent task by reading subtasks'
    terminal outcomes from the DB. Only direct children of `task_id` count
    — each child is one past orchestration step. Order: creation order,
    1-indexed.

    If a chain ran since the last manager wake, a synthetic chain-summary
    entry is appended so the manager can see what happened without re-deriving
    it from raw child task records.
    """
    from runtime.models import StepRecord
    steps: list[StepRecord] = []
    for i, child_id in enumerate(orch._db.get_children(task_id), start=1):
        child = orch._db.get_task(child_id)
        if child is None:
            continue
        success = child.status == TaskStatus.COMPLETED
        report = orch._db.get_latest_completion_report(child.id)
        verdict = report.verdict if report is not None and report.verdict else "(none)"
        revisit = child.revisit_of_task_id or "(none)"
        steps.append(StepRecord(
            step_number=i,
            agent=child.assigned_agent or "unknown",
            action=f"delegate [{child.id}]: {(child.brief or '')[:100]}",
            result_summary=(
                f"task_id={child.id}; status={child.status.value}; "
                f"verdict={verdict}; revisit_of_task_id={revisit}; "
                f"reason={child.note or '(no summary)'}"
            ),
            success=success,
        ))
    # Append chain summary if a chain ran since the last manager wake.
    chain_summary = _summarize_recent_chain(orch, task_id)
    if chain_summary is not None:
        steps.append(StepRecord(
            step_number=len(steps) + 1,
            agent="orchestrator",
            action="chain summary",
            result_summary=chain_summary,
            success=True,
        ))
    return steps

def _summarize_recent_chain(orch: "Orchestrator", parent_task_id: str) -> str | None:
    """One-line summary of the most-recent chain that ran under parent_task_id.

    Returns None if no chain_auto_advance audit rows exist on the parent.
    Otherwise pairs the audit rows (which list triggering_child_id and
    spawned_child_id) with the final spawned child's terminal verdict to
    produce a human-readable line for the manager's wake context.
    """
    audit_logs = orch._db.get_audit_logs(parent_task_id)
    rows = [r for r in audit_logs if r["action"] == "chain_auto_advance"]
    if not rows:
        return None
    # Suppress the summary if a manager decision (orchestration_step) has
    # landed AFTER the most-recent chain advance — the manager has already
    # seen this chain summary in the wake where the chain ended, and the
    # current wake is for a later non-chain event. Showing it again would
    # place a stale chain summary at the end of prior_steps, misrepresenting
    # the latest event.
    max_chain_id = max(r["id"] for r in rows)
    max_step_id = max(
        (r["id"] for r in audit_logs if r["action"] == "orchestration_step"),
        default=0,
    )
    if max_step_id > max_chain_id:
        return None
    # Filter to the most-recent chain only — multiple sequential chains may
    # share the same parent across separate manager wakes, distinguished by
    # the chain_origin_step_audit_id of the orchestration_step that minted
    # each chain.
    latest_origin_id = rows[-1]["payload"]["chain_origin_step_audit_id"]
    rows = [
        r for r in rows
        if r["payload"]["chain_origin_step_audit_id"] == latest_origin_id
    ]
    triggers = [r["payload"]["triggering_child_id"] for r in rows]
    spawned = [r["payload"]["spawned_child_id"] for r in rows]
    chain_children = triggers + ([spawned[-1]] if spawned else [])
    last_child_id = chain_children[-1]
    last_report = orch._db.get_latest_completion_report(last_child_id)
    last_verdict = last_report.verdict if last_report else None
    arrow = " → ".join(chain_children)
    if last_report and last_report.status == "blocked":
        return f"Chain aborted at {last_child_id}: self-blocked"
    if last_verdict is not None:
        return f"Chain: {len(chain_children)} legs ({arrow}), final verdict {last_verdict}"
    return f"Chain: {len(chain_children)} legs ({arrow})"

def _fanout_join_header_if_applicable(
    orch: "Orchestrator", task_id: str,
) -> str | None:
    """Return the fan-out join context header on the first manager step after
    a fan-out join, otherwise None.

    Trigger: the most recent 'fanout_join' audit entry for this task has a
    higher row id than the most recent 'orchestration_step' entry — i.e. the
    fan-out children are all terminal AND the manager hasn't run yet. Once
    the manager produces its first decision after join,
    ``log_orchestration_step`` writes a row with a higher id and this helper
    returns None on every subsequent call.
    """
    logs = orch._db.get_audit_logs(task_id)
    last_join = None
    last_step = None
    for entry in logs:
        action = entry["action"]
        if action == "fanout_join":
            last_join = entry
        elif action == "orchestration_step":
            last_step = entry
    if last_join is None:
        return None
    if last_step is not None and last_step["id"] > last_join["id"]:
        return None
    payload = last_join.get("payload") or {}
    if isinstance(payload, str):
        import json as _json
        try:
            payload = _json.loads(payload)
        except Exception:
            payload = {}
    return payload.get("context_markdown")
