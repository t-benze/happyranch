"""Private ordinary-task launch refusal evidence in the existing audit ledger.

Neither these events nor FAILED certify process quiescence. The native Pause
journal and move reservations retain their independent unknown effects.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from runtime.models import TaskStatus

_ACTION = "task_native_launch_boundary"
_LIMIT = 3


def safe_cause(stage: str, exc: BaseException | None = None, *,
               repo: str | None = None, returncode: int | None = None,
               diagnostic_category: str | None = None, diagnostic_rule: str | None = None,
               capture_complete: bool | None = None, capture_truncated: bool | None = None,
               timed_out: bool | None = None, operation: str | None = None,
               refresh_phase: str | None = None, refresh_probe: str | None = None,
               preparation_receipt: int | None = None) -> dict:
    """No exception text, source lines, command arguments or process output."""
    cause: dict[str, Any] = {"stage": stage}
    if exc is not None:
        name = type(exc).__name__
        cause["exception_class"] = name if name.isidentifier() and len(name) <= 80 else "unavailable"
        from runtime.infrastructure.task_pause_controls import PauseControlError
        if isinstance(exc, PauseControlError):
            cause["code"] = (exc.detail["code"] if exc.detail["code"] in {
                "pause_control_unavailable", "root_paused", "unknown_task", "task_terminal",
                "root_task_required", "task_not_in_progress", "control_generation_conflict",
            } else "pause_refusal")
        root = Path(__file__).resolve().parents[2]
        trace, locations = exc.__traceback__, []
        for _ in range(64):
            if trace is None:
                break
            filename = Path(trace.tb_frame.f_code.co_filename)
            location = "external"
            if filename.is_absolute() and filename.is_relative_to(root):
                relative = filename.relative_to(root).as_posix()
                if (len(relative) <= 200 and relative.split("/")[0] in {"runtime", "cli"}
                        and all(c.isascii() and (c.isalnum() or c in "_./-") for c in relative)):
                    location = relative
            locations.append(f"{location}:{trace.tb_lineno}")
            trace = trace.tb_next
        cause["locations"] = locations[-4:]
    if repo is not None:
        cause["repo"] = repo if (0 < len(repo) <= 80 and all(
            c.isascii() and (c.isalnum() or c in "_.-") for c in repo)) else "redacted"
        cause["operation"] = "pull_ff_only"
        if stage == "repository_pull" and operation == "refresh_ff_only":
            cause["operation"] = "refresh_ff_only"
            if type(refresh_phase) is str and refresh_phase in {
                "precheck", "fetch", "between_stages", "merge", "submodule_update",
            }:
                cause["refresh_phase"] = refresh_phase
    if returncode is not None:
        cause["returncode"] = returncode
    diagnostic_values = (diagnostic_category, diagnostic_rule, capture_complete,
                         capture_truncated, timed_out)
    # Historical/ordinary causes gain no required keys. Only a NEW executed
    # failed pull can carry the optional extension; attempted starts omit it.
    if (any(value is not None for value in diagnostic_values)
            and stage == "repository_pull" and repo is not None
            and (exc is not None or type(returncode) is int and returncode != 0)):
        pairs = {
            ("lock", "pull-lock-exists-v1"),
            ("network", "pull-resolve-host-v1"),
            ("auth", "pull-auth-failed-v1"),
            ("other", "pull-non-ff-v1"),
            ("other", "pull-unmatched-v1"),
            ("unknown", "pull-unknown-v1"),
        }
        valid = (type(diagnostic_category) is str and type(diagnostic_rule) is str
                 and (diagnostic_category, diagnostic_rule) in pairs
                 and all(type(value) is bool for value in
                         (capture_complete, capture_truncated, timed_out))
                 and (not timed_out or not capture_complete)
                 and (diagnostic_category == "unknown"
                      or exc is None and capture_complete and not capture_truncated and not timed_out))
        cause.update({
            "diagnostic_category": diagnostic_category if valid else "unknown",
            "diagnostic_rule": diagnostic_rule if valid else "pull-unknown-v1",
            "capture_complete": capture_complete if valid else False,
            "capture_truncated": capture_truncated if type(capture_truncated) is bool else False,
            "timed_out": timed_out if type(timed_out) is bool else False,
        })
    if stage == "repository_pull":
        if refresh_probe in {"identity_after_fetch", "source_after_fetch", "target_type", "ancestry_forward",
                "ancestry_reverse", "final_branch", "final_head"}:
            cause["refresh_probe"] = refresh_probe
        if type(preparation_receipt) is int and preparation_receipt > 0:
            cause["preparation_receipt"] = preparation_receipt
    return cause


def validate_preparation_cause(value: Any) -> dict:
    """Strict optional parity for historical and new sanitized cause forms."""
    import re
    fields = {"stage", "exception_class", "code", "locations", "repo", "operation", "refresh_phase",
        "returncode", "diagnostic_category", "diagnostic_rule", "capture_complete", "capture_truncated", "timed_out",
        "refresh_probe", "preparation_receipt"}
    if type(value) is not dict or not {"stage"} <= value.keys() <= fields:
        raise ValueError("preparation_cause_shape")
    if type(value["stage"]) is not str or re.fullmatch(r"[a-z_]{1,80}", value["stage"]) is None:
        raise ValueError("preparation_cause_stage")
    if "exception_class" in value and (type(value["exception_class"]) is not str
            or not value["exception_class"].isidentifier() or len(value["exception_class"]) > 80):
        raise ValueError("preparation_cause_exception")
    if "code" in value and value["code"] not in {"pause_control_unavailable", "root_paused", "unknown_task", "task_terminal",
            "root_task_required", "task_not_in_progress", "control_generation_conflict", "pause_refusal"}:
        raise ValueError("preparation_cause_code")
    if "locations" in value and (type(value["locations"]) is not list or len(value["locations"]) > 4 or any(
            type(v) is not str or len(v) > 220 or re.fullmatch(r"(?:external|(?:runtime|cli)/[A-Za-z0-9_./-]+):[0-9]{1,9}", v) is None
            for v in value["locations"])):
        raise ValueError("preparation_cause_locations")
    if "repo" in value and (type(value["repo"]) is not str or re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", value["repo"]) is None):
        raise ValueError("preparation_cause_repository")
    if "operation" in value and value["operation"] not in {"pull_ff_only", "refresh_ff_only"}:
        raise ValueError("preparation_cause_operation")
    if "refresh_phase" in value and value["refresh_phase"] not in {"precheck", "fetch", "between_stages", "merge", "submodule_update"}:
        raise ValueError("preparation_cause_phase")
    if "returncode" in value and type(value["returncode"]) is not int:
        raise ValueError("preparation_cause_returncode")
    diagnostics = {"diagnostic_category", "diagnostic_rule", "capture_complete", "capture_truncated", "timed_out"}
    if value.keys() & diagnostics:
        pairs = {("lock", "pull-lock-exists-v1"), ("network", "pull-resolve-host-v1"),
            ("auth", "pull-auth-failed-v1"), ("other", "pull-non-ff-v1"),
            ("other", "pull-unmatched-v1"), ("unknown", "pull-unknown-v1")}
        if (not diagnostics <= value.keys() or (value["diagnostic_category"], value["diagnostic_rule"]) not in pairs
                or any(type(value[k]) is not bool for k in ("capture_complete", "capture_truncated", "timed_out"))):
            raise ValueError("preparation_cause_diagnostics")
    if "refresh_probe" in value and value["refresh_probe"] not in {"identity_after_fetch", "source_after_fetch", "target_type",
            "ancestry_forward", "ancestry_reverse", "final_branch", "final_head"}:
        raise ValueError("preparation_cause_probe")
    if "preparation_receipt" in value and (type(value["preparation_receipt"]) is not int or value["preparation_receipt"] <= 0):
        raise ValueError("preparation_cause_receipt")
    return value


def _logical_owner(invocation) -> str:
    from runtime.orchestrator.task_pause import fingerprint
    identity = {"fingerprint": fingerprint(invocation.task), "agent": invocation.agent,
                "owner": invocation.owner,
                "generation": invocation.metadata.get("authority_v2_generation")}
    return hashlib.sha256(json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _recovery_owner_metadata_current(store, entry: dict, metadata: dict) -> bool:
    """Authenticate the closed journal projection and its optional v2 transport.

    Publication attempts belong to the real delivery, never owner_identity.
    Only that named, current notification fact may extend the exact projection.
    """
    owner = entry["owner_identity"]
    if type(metadata) is not dict:
        return False
    if "publication_attempt" not in metadata:
        return metadata == owner
    generation = owner.get("authority_v2_generation")
    attempt = metadata["publication_attempt"]
    if (set(metadata) != set(owner) | {"publication_attempt"}
            or {key: metadata[key] for key in owner} != owner
            or not isinstance(generation, str) or not generation
            or type(attempt) is not int or attempt < 1):
        return False
    notification = store.db.get_authority_policy_v2_recovery_notification(generation)
    return (notification is not None and notification.state == "settled"
            and notification.publication_attempt == attempt)


def _current(invocation, *, refusal: bool = False) -> bool:
    from runtime.orchestrator.task_pause import fingerprint, eligible
    db = invocation.orch._db
    task = db.get_task(invocation.task.id)
    if (task is None or task.cancelled_at is not None or task.brief != invocation.task.brief
            or task.parent_task_id != invocation.task.parent_task_id or task.team != invocation.task.team
            or task.task_type != invocation.task.task_type):
        return False
    if invocation.admitted:
        if (task.status != TaskStatus.IN_PROGRESS or task.block_kind is not None
                or task.assigned_agent != invocation.agent or task.current_session_id != invocation.session_id
                or task.orchestration_step_count != invocation.task.orchestration_step_count + (invocation.owner != "recovery")):
            return False
    elif invocation.owner == "recovery":
        # A genuine unclaimed recovery is in_progress on its ORIGINAL SID;
        # ordinary pending/carrier eligibility deliberately does not admit it.
        from runtime.orchestrator.task_pause import _unclaimed_recovery_current, _authenticate_deferred_v2
        store = invocation.store
        row = store.row_uncommitted(store.root_uncommitted(task.id)["id"])
        entry = invocation._entry(row)
        proof = entry["context"].get("recovery_return")
        if (fingerprint(task) != fingerprint(invocation.task)
                or task.status != TaskStatus.IN_PROGRESS or task.block_kind is not None
                or task.assigned_agent != invocation.agent or entry["owner"] != "recovery"
                or entry["session_id"] != invocation.session_id or entry["fingerprint"] != fingerprint(task)
                or not _recovery_owner_metadata_current(store, entry, invocation.metadata)
                or proof is None or proof["origin_session_id"] != task.current_session_id
                or proof["recovery_session_id"] != invocation.session_id
                or len([e for e in row["journal"]["entries"] if e["task_id"] == task.id
                        and e["owner"] != "job" and not e["context"].get("producer_settled")]) != 1
                or not _unclaimed_recovery_current(store, entry)):
            return False
        if "authority_v2_generation" in entry["owner_identity"]:
            _authenticate_deferred_v2(store, entry)
    elif fingerprint(task) != fingerprint(invocation.task) or not eligible(invocation.orch, task):
        return False
    store = getattr(db, "_task_pause_store", None)
    if store is not None:
        # A hold arriving after the failed write still wins over accounting.
        from runtime.infrastructure.task_pause_controls import PauseControlError
        from runtime.orchestrator.task_pause import DeferredRootPause, deferred_invocation, _same_deferred_owner
        from runtime.orchestrator.team_move import DeferredTeamMove
        try:
            if refusal:
                store.held_uncommitted(task.id)
                invocation.orch._team_moves.check_task_uncommitted(task)
            if getattr(invocation, "deferred_adoption_pending", False):
                current = deferred_invocation(invocation.orch, task, invocation.metadata, adopt=False)
                if current is None or not _same_deferred_owner(invocation, current):
                    return False
        except (DeferredRootPause, DeferredTeamMove):
            return False
        except PauseControlError as exc:
            if exc.detail["code"] == "root_paused":
                return False
            raise
    if task.parent_task_id is None:
        # Consume the existing native classification; never infer a generation
        # from metadata alone or change its admission/settlement semantics.
        dispatch = db.classify_authority_policy_v2_root_dispatch_for_enqueue(task.id)
        generation = invocation.metadata.get("authority_v2_generation")
        if generation is not None:
            expected = "admitted" if invocation.admitted or invocation.owner == "recovery" else "pending"
            if dispatch.kind != expected or dispatch.generation_id != generation:
                return False
        elif dispatch.kind not in {"absent", "retired"}:
            return False
    store = getattr(db, "_task_pause_store", None)
    if store is not None and any(live.task.id == task.id and live is not invocation
                                 for live in store._live.values()):
        return False
    # An authentic callback wins even before its decision has been consumed.
    result_sids = {invocation.session_id}
    if invocation.owner == "recovery":
        result_sids.add(invocation.task.current_session_id)
    return all(sid is None or db.get_latest_task_result(task.id, invocation.agent, sid) is None
               for sid in result_sids)


def record_started(invocation) -> None:
    """Only the native positive-PID callback breaks a consecutive streak."""
    db = invocation.orch._db
    with db._lock:
        if not _current(invocation):
            return
        db.insert_audit_log(invocation.task.id, invocation.agent, _ACTION,
            {"kind": "started", "logical_owner": _logical_owner(invocation),
             "attempt_id": invocation.refusal_attempt_id, "invocation_id": invocation.identity,
             "session_id": invocation.session_id, "count": 0})


def settle_refusal(invocation, exc: Exception, *, prepared: bool) -> bool:
    """Record an actual refusal; return True only for this exact FAILED owner.

    Pure admission failures can be rediscovered twice. An unclosed durable
    preparation cannot be retried by the existing unknown-owner protocol, so
    its actual refusal fails early while all cleanup evidence remains sticky.
    Call only outside a Pause writer, before release_live or any terminal tail.
    """
    from runtime.infrastructure.task_pause_controls import PauseControlError
    from runtime.orchestrator.run_step import _fail, _enqueue_parent_if_waiting, _maybe_post_thread_followup
    if (invocation.launch_observed or invocation.owner not in {"ordinary", "v2", "recovery"}
            or isinstance(exc, PauseControlError) and exc.detail["code"] == "root_paused"):
        return False
    db = invocation.orch._db
    with db._lock:
        if not _current(invocation, refusal=True):
            return False
        if db._conn.in_transaction:
            raise RuntimeError("launch_refusal_persistence_unavailable") from exc
        owner = _logical_owner(invocation)
        # Existing task-id index; at most three bounded payloads are decoded.
        rows = db._conn.execute(
            "SELECT CASE WHEN typeof(payload)='text' AND length(CAST(payload AS BLOB))<=8192 "
            "THEN payload END FROM audit_log WHERE task_id=? AND action=? ORDER BY id DESC LIMIT 3",
            (invocation.task.id, _ACTION)).fetchall()
        try:
            history = [json.loads(row[0]) for row in rows]
            if any(not isinstance(row, dict) or row.get("kind") not in {"started", "refused"}
                   or type(row.get("count")) is not int or not 0 <= row["count"] <= _LIMIT
                   or not isinstance(row.get("logical_owner"), str)
                   or not isinstance(row.get("attempt_id"), str)
                   or (row["kind"] == "refused" and (row["count"] == 0 or not isinstance(row.get("first_cause"), dict)))
                   or (row["kind"] == "started" and row["count"] != 0) for row in history):
                raise ValueError
        except (ValueError, TypeError):
            raise RuntimeError("launch_refusal_history_unavailable") from None
        prior = history[0] if history and history[0]["logical_owner"] == owner else None
        duplicate = next((row for row in history if row["logical_owner"] == owner
                          and row["attempt_id"] == invocation.refusal_attempt_id and row["kind"] == "refused"), None)
        if duplicate is not None:
            payload = duplicate
            count, first = payload["count"], payload["first_cause"]
        else:
            count = min(_LIMIT, prior["count"] + 1) if prior is not None else 1
            first = (prior["first_cause"] if prior is not None and prior["count"] else
                     invocation.preparation_cause or safe_cause("native_admission", exc))
            payload = {"kind": "refused", "logical_owner": owner,
                       "attempt_id": invocation.refusal_attempt_id, "invocation_id": invocation.identity,
                       "session_id": invocation.session_id, "count": count,
                       "first_cause": first, "refusal": safe_cause("native_admission", exc),
                       "disposition": "unclosed_preparation" if prepared else "bounded_admission"}
            db.insert_audit_log(invocation.task.id, invocation.agent, _ACTION, payload)
        if count < _LIMIT and not prepared:
            return False
        note = (f"launch_refused: {payload['disposition']}; attempts={count}; "
                f"first_stage={first['stage']}; inspect task_native_launch_boundary audit "
                "and resolve preparation/storage failure before an explicit manager retry")
    # Recheck the winner inside _fail; slow terminal effects stay outside
    # the ledger lock. Audit COMMIT by itself never proves terminal failure.
    _fail(invocation.orch, invocation.task.id, note=note, current_owner=lambda: _current(invocation, refusal=True))
    with db._lock:
        current = db.get_task(invocation.task.id)
        failed = current is not None and current.status == TaskStatus.FAILED and current.note == note
    if failed:
        _enqueue_parent_if_waiting(invocation.orch, invocation.task.id, root_auto_revisit_spawned=False)
        _maybe_post_thread_followup(invocation.orch, invocation.task.id,
                                  status=TaskStatus.FAILED, auto_revisit_spawned=False)
    return failed
