"""Closed founder transport and redacted projections; no domain effects.

Public commands bind the private D image digest, never accept images or paths.
The shared bearer boundary does not establish cryptographic human presence.
"""
from __future__ import annotations

import base64
import difflib
import json
from typing import Annotated, Literal

from pydantic import Field, model_validator

from runtime.infrastructure.team_move_store import (
    Closed, Digest, ExpectedInputs, ForwardDecision, Identifier, MoveRequest, Positive, Progress,
    TeamMoveError, _bytes, _decode, _digest,
)
from runtime.orchestrator.team_move_canonical import AGENTS, NEW_CHARTER

Address = Annotated[str, Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")]


class MoveSelection(Closed):
    version: Literal[1]
    source_team: Literal["consultant"]
    target_team: Literal["default"]
    affected_agents: tuple[Address, Address]
    demote: Address
    retire_source: Literal[True]
    charter: Literal[NEW_CHARTER]
    drain_threads: bool

    @model_validator(mode="before")
    @classmethod
    def literal_types(cls, value):
        if not isinstance(value, dict) or type(value.get("version")) is not int or type(value.get("retire_source")) is not bool:
            raise TeamMoveError("team_move_invalid_selection")
        return value


class PublicMoveRequest(MoveSelection):
    expected: ExpectedInputs
    private_request_digest: Digest

    @model_validator(mode="after")
    def normalized_ids(self):
        if self.affected_agents != AGENTS or self.demote != "consultant_head":
            raise TeamMoveError("team_move_normalized_ids_required")
        return self


class ApplyMove(Closed):
    operation_key: Identifier
    request: PublicMoveRequest


class RecoverMove(Closed):
    expected_revision: Positive


def public_request(request: MoveRequest) -> PublicMoveRequest:
    return PublicMoveRequest(version=1, source_team=request.source_team,
        target_team=request.target_team, affected_agents=request.affected_agents,
        demote=request.demote, retire_source=True, charter=NEW_CHARTER,
        drain_threads=request.drain_threads, expected=request.expected,
        private_request_digest=_digest(_bytes(request)))


def selection_ids(org, selection: MoveSelection):
    """Use native current naming before normalization; former names never forward."""
    from runtime.identities.registry import classify
    resolved = []
    for address in (*selection.affected_agents, selection.demote):
        subject = classify(org, address)
        if subject is not None and subject.classification == "former":
            error = TeamMoveError("former_name")
            error.current_name = subject.current_label
            error.canonical_id = subject.canonical_id
            raise error
        if subject is None or subject.kind != "agent" or subject.lifecycle != "active":
            raise TeamMoveError("team_move_unknown_or_ineligible_identity")
        resolved.append(subject.canonical_id)
    if tuple(sorted(resolved[:2])) != AGENTS or resolved[2] != "consultant_head":
        raise TeamMoveError("team_move_scope_unsupported")


def text_diff(before: bytes, after: bytes, name: str) -> str:
    # Exact UTF8 diff including line endings, with explicit final-newline markers.
    lines = difflib.unified_diff(before.decode().splitlines(keepends=True),
        after.decode().splitlines(keepends=True), fromfile=name, tofile=name)
    return "".join(line if line.endswith("\n") else line + "\n\\ No newline at end of file\n" for line in lines)


def blocker_view(blocker):
    """No raw lifecycle attempts, session credentials or exception prose."""
    fact = blocker.get("fact", {})
    return {"kind": blocker["kind"], "owner_id": str(blocker["owner_id"]),
        "agent_id": blocker.get("agent_id", fact.get("agent_id")),
        "thread_id": blocker.get("thread_id", fact.get("thread_id")), "journal_id": blocker.get("journal_id"),
        "next_action": blocker.get("remedy", blocker.get("next_action", "Resolve the actual native owner; retain hold and show the SAME operation"))}


def preview_view(operation, preview, capability):
    from runtime.identities.registry import read_name_metadata
    request = preview.request
    labels = read_name_metadata(operation.org)
    threads = operation.move.threads.census(AGENTS)
    candidates = [{"invocation_id": fact["invocation_id"], "thread_id": fact["thread_id"],
        "agent_id": fact["agent_id"]} for fact in threads if fact["code"] == "thread_running"]
    contexts = []
    for before, after in zip(preview.before_contexts, preview.after_contexts):
        contexts.append({"agent_id": after.agent_id, "before": before.manifest(), "after": after.manifest(),
            "instruction_diff": text_diff(before.instruction_bytes, after.instruction_bytes, after.agent_id + "/AGENTS.md"),
            "settings_diff": text_diff(before.settings_bytes or b"", after.settings_bytes or b"", after.agent_id + "/.claude/settings.json")})
    with operation.org.db.coherent_read_view() as conn:
        impact = [dict(row) for row in conn.execute(
            "SELECT id,root_task_id,status FROM workflow_instances WHERE status NOT IN ('complete','cancelled') ORDER BY id")]
    return {"version": 1, "capability": capability, "request": public_request(request).model_dump(mode="json"),
        "request_digest": _digest(_bytes(public_request(request))),
        "current_names": [{"agent_id": agent, "current_name": labels[agent].current_label} for agent in AGENTS],
        "canonical_diff": [{"path": image.path, "before_digest": image.before_digest,
            "after_digest": image.after_digest, "diff": text_diff(base64.b64decode(image.before_base64),
                base64.b64decode(image.after_base64), image.path)} for image in request.files],
        "contexts": contexts, "expected_inputs": request.expected.model_dump(mode="json"),
        "role_delta": [{"agent_id": agent, "before_team": "consultant",
            "before_role": "manager" if agent == "consultant_head" else "worker",
            "after_team": "default", "after_role": "worker"} for agent in AGENTS],
        "target_authority_digest": _digest(preview.target_authority_bytes),
        "blockers": [blocker_view(b) for b in preview.blockers], "inventory_complete": True,
        "drain_candidates": candidates, "pending_thread_obligations": [
            {k: fact[k] for k in ("code", "agent_id", "thread_id", "invocation_id") if k in fact}
            for fact in threads if fact["code"] == "thread_pending_obligation"],
        "publication_impact": {"scope": "org", "workflow_instances_requiring_current_generation": impact},
        "can_apply_now": capability["apply_available"] and not preview.blockers,
        "can_drain_threads": bool(candidates), "reservation": False,
        "missing_evidence": "Online behavior, recovery, races, cleanup, launcher and live operation UNEXECUTED"}


def saved_owners(row):
    """Strict durable identities; absence stays unknown, never a live claim."""
    decision = _decode(ForwardDecision, row["decision_bytes"]) if row["decision_bytes"] is not None else None
    progress = _decode(Progress, row["progress_bytes"])
    latest = progress.recovery[-1] if progress.recovery else None
    publication = progress.publication
    return {"original_recovery_owner_id": decision.writer_invocation_id if decision else None,
        "saved_current_recovery_owner_id": latest.owner_id if latest else decision.writer_invocation_id if decision else None,
        "previous_recovery_owner_id": latest.previous_owner_id if latest else None,
        "original_publication": None if publication is None else {
            "journal_id": publication.journal_id, "writer_invocation_id": publication.writer_invocation_id,
            "generation": publication.generation}, "current_publication": None}


def error_view(error):
    """Safe native refusal facts; exception prose/proof/credentials stay private."""
    from runtime.orchestrator.team_move_operation import TeamMoveOperation
    diagnostic = TeamMoveOperation._diagnostic(error)
    remedy = ("Resolve the saved previous owner through genuine owner-finished/process identity evidence; retain hold, no TTL takeover"
        if diagnostic.code.startswith("team_move_recovery_owner_") or diagnostic.code == "team_move_recovery_process_unknown"
        else "Resolve named native owners; retain hold and SAME key/target; explicitly recover observed revision")
    return {"source": "process_local_failure_observation", "durable": False,
        "code": diagnostic.code, "owner_ids": list(diagnostic.owner_ids),
        "blockers": [blocker_view(b) for b in getattr(error, "blockers", ())],
        "secondary_codes": list(getattr(error, "team_move_secondary_codes", ())),
        "native_observation_at_failure": getattr(error, "team_move_observation", None),
        "next_action": remedy}


def operation_view(row, *, running=False, error=None, current_observation=None):
    """Keep saved diagnostics distinct from current reads and ephemeral failure."""
    phase = row["phase"]
    terminal = phase in {"complete", "refused", "abandoned"}
    diagnostic = json.loads(row["diagnostic_bytes"]) if row["diagnostic_bytes"] is not None else None
    error_code = error["code"] if error else None
    return {"version": 1, "operation_id": row["operation_id"], "operation_key": row["operation_key"],
        "request_digest": _digest(_bytes(public_request(_decode(MoveRequest, row["request_bytes"])))),
        "private_request_digest": row["request_digest"],
        "phase": phase, "state_revision": row["state_revision"],
        "committed": row["decision_bytes"] is not None, "held": row["active_slot"] == 1,
        "running": running, "recovery_required": bool(error_code) or (not terminal and not running),
        "diagnostic": diagnostic, "saved_diagnostic_scope": "bounded_code_and_up_to_64_owner_ids_not_current_census",
        "saved_owners": saved_owners(row), "current_observation": current_observation,
        "service_error": error_code, "service_diagnostic": error,
        "receipt": json.loads(row["receipt_bytes"]) if row["receipt_bytes"] is not None else None,
        "receipt_digest": row["receipt_digest"],
        "next_action": "resolve_named_owners_then_recover_same_operation_with_expected_revision" if error_code else "readback" if phase == "complete" else (
            "resolve_blockers_and_preview" if terminal else "show" if running else "resolve_named_owners_then_recover_same_operation_with_expected_revision")}
