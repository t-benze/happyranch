"""Narrow ordinary callback omission; independent of startup/B classification."""
from __future__ import annotations

import hashlib
import json
from typing import Any

from runtime.models import (
    authority_policy_v2_attempt_id,
    authority_policy_v2_canonical_json_bytes,
    decode_authority_policy_v2_json,
)

OMISSION_KEY = "_authority_v2_admission"
_BINDING_KEYS = (
    "binding_id", "team", "contract_id", "contract_version", "contract_digest",
    "release_id", "activation_id", "activation_epoch", "selector_id",
)


def _marker(admission: dict) -> dict:
    return {"omitted": "ordinary", "assessment_digest": admission["assessment_digest"],
            **{key: admission[key] for key in _BINDING_KEYS}}


def ordinary_completion_omission(db: Any, *, task_id: str, row: dict) -> bool:
    """Called only in the ordinary callback writer's owned transaction.

    Any uncertainty keeps today's attempt. In particular, blocked status
    cannot hide an explicit escalation, malformed or missing decision. This
    predicate must never replace the broader startup inert classifier.
    """
    try:
        from runtime.orchestrator.orchestrator import (
            Orchestrator,
            completion_report_from_result_row,
        )

        if db.authority_policy_v2_completion_dispatch_context(
            root_task_id=task_id, result_row_id=None,
        ).kind != "no_v2":
            return False
        report = completion_report_from_result_row(task_id, row)
        if report.decision is None:
            return False
        return Orchestrator._parse_next_step(None, report).action in {"delegate", "done", "fanout"}
    except Exception:  # noqa: BLE001 - every classification/proof error must deny omission/replay
        return False


def omission_decision_json(decision_json: str, admission: dict) -> str:
    """Attach immutable server evidence before the result INSERT, never on retry."""
    decision = json.loads(decision_json)
    decision[OMISSION_KEY] = _marker(admission)
    return json.dumps(decision)


def authenticated_omitted_result(db: Any, *, result_id: int, task_id: str, agent: str) -> dict | None:
    """Read stored-session evidence, proving physical absence, not reader None.

    No current root lineage is consulted: later continuations cannot change
    what was committed by this callback. Errors/contradictions deny replay.
    """
    try:
        with db.coherent_read_view():
            raw = db._conn.execute("SELECT * FROM task_results WHERE id=?", (result_id,)).fetchone()
            if raw is None or (raw["task_id"], raw["agent"]) != (task_id, agent):
                return None
            row = dict(raw)
            decision = decode_authority_policy_v2_json(row["decision_json"])
            marker = decision.get(OMISSION_KEY)
            if not isinstance(marker, dict) or marker.get("omitted") != "ordinary":
                return None
            binding = db.get_authority_policy_v2_session_binding(
                root_task_id=task_id, manager_agent=agent, manager_session_id=row["session_id"],
            )
            if binding is None:
                return None
            db._authenticate_v2_session_binding_uncommitted(binding)
            carrier = decision["_manager_self_evaluation"]
            digest = hashlib.sha256(authority_policy_v2_canonical_json_bytes(carrier)).hexdigest()
            expected = _marker({**binding.model_dump(), "binding_id": binding.binding_id, "assessment_digest": digest})
            if marker != expected:
                return None
            attempt_id = authority_policy_v2_attempt_id(
                manager_agent=agent, manager_session_id=row["session_id"],
                result_id=result_id, root_task_id=task_id, team=binding.team,
            )
            if db._conn.execute(
                "SELECT 1 FROM authority_policy_v2_attempts WHERE result_id=? OR attempt_id=? "
                "OR (root_task_id=? AND manager_agent=? AND manager_session_id=?) LIMIT 1",
                (result_id, attempt_id, task_id, agent, row["session_id"]),
            ).fetchone() is not None:
                return None
            # Read the raw audit relation as well: a typed attempt reader also
            # returns None for corrupt attempts or missing/duplicate audits.
            # Malformed JSON is a read error, never proof of absence. Include
            # foreign-scoped evidence naming this result, and conservatively
            # reject malformed stage evidence on its own root.
            for audit in db._conn.execute(
                "SELECT task_id,payload FROM audit_log WHERE action='authority_policy_v2_result_stage' "
                "AND (task_id=? OR CAST(json_extract(payload,'$.result_id') AS TEXT)=? "
                "OR json_extract(payload,'$.attempt_id')=?)",
                (task_id, str(result_id), attempt_id),
            ):
                payload = decode_authority_policy_v2_json(audit["payload"])
                if (not isinstance(payload, dict)
                        or type(payload.get("result_id")) is not int
                        or str(payload["result_id"]) == str(result_id)
                        or payload.get("attempt_id") == attempt_id):
                    return None
            return row
    except Exception:  # noqa: BLE001 - every classification/proof error must deny omission/replay
        return None


def omitted_completion_replay_matches(db: Any, stored_result: dict, *, admission: dict, **payload: Any) -> bool:
    """Authenticate the marker, original binding, carrier digest and full body."""
    from runtime.infrastructure.database import completion_result_payload_matches

    try:
        with db.coherent_read_view():
            row = authenticated_omitted_result(
                db, result_id=stored_result["id"], task_id=stored_result["task_id"], agent=stored_result["agent"],
            )
            if row is None or not db._authenticate_v2_attempt_admission_uncommitted(
                task_id=row["task_id"], agent=row["agent"], session_id=row["session_id"], admission=admission,
            ):
                return False
            marker = json.loads(row["decision_json"])[OMISSION_KEY]
            if marker != _marker(admission):
                return False
            incoming = json.loads(payload["decision_json"])
            if OMISSION_KEY in incoming:
                return False  # Only the server's stored marker can establish omission.
            incoming[OMISSION_KEY] = marker
            return completion_result_payload_matches(row, **{**payload, "decision_json": json.dumps(incoming)})
    except Exception:  # noqa: BLE001 - every classification/proof error must deny omission/replay
        return False
