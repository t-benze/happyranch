from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from enum import StrEnum

from typing import Literal, Mapping

from pydantic import BaseModel, Field, StrictBool, StrictInt, StrictStr, field_validator, model_validator


class TaskStatus(StrEnum):
    # THR-037 Change B (Path B, stored source-of-truth): the surfaced `blocked`
    # vocabulary collapses into a model that stores what is actually true. A
    # parent waiting on its own children/jobs is IN_PROGRESS, not BLOCKED; the
    # waiting reason is preserved in the `block_kind` discriminant. See
    # docs/superpowers/specs/2026-06-27-task-status-pathB-stored-design.md.
    PENDING = "pending"
    # Two-valued, discriminated by `block_kind` (see BlockKind):
    #   block_kind IS NULL                       ⟺ a subprocess is running now.
    #   block_kind IN (delegated, blocked_on_job) ⟺ parked, no subprocess,
    #     waiting on children/jobs it manages internally.
    IN_PROGRESS = "in_progress"
    # NEW (Path B), non-terminal. A task that needs a founder decision (genuine
    # agent escalation, failure-round-bound exhaustion, or budget exhaustion).
    # Was the legacy blocked(escalated) state; `block_kind` is cleared. The
    # founder resolves it via resolve-escalation (continue → pending / supersede →
    # cancelled, cancelled_at set). NOT in any terminal predicate.
    ESCALATED = "escalated"
    COMPLETED = "completed"              # terminal
    FAILED = "failed"                   # terminal
    # NEW (Path B), terminal. A founder-initiated stop (was failed + cancelled_at
    # set). Distinct from FAILED so the audit/event trail shows a deliberate
    # cancellation, not an agent/executor failure. `cancelled_at` is still set.
    # Replays as a failure-class terminal event with outcome="cancelled" (see
    # OrgState._TERMINAL_STATUS_TO_EVENT). Joins every terminal predicate.
    CANCELLED = "cancelled"
    # Terminal. An escalated|delegated task whose follow-up work moved to a
    # human-authorized continuation (founder `revisit` / thread-dispatch) is
    # closed here instead of re-running — distinct from COMPLETED so the audit
    # trail shows it was superseded, not finished by an agent. Joins every
    # terminal predicate (TERMINAL_STATES, _TERMINAL_TASK_STATUSES,
    # _TERMINAL_STATUS_TO_EVENT). See docs/agent-guides/orchestrator-contracts.md and
    # docs/agent-guides/features-and-invariants.md (escalation).
    #
    # Phase 3 (THR-037): BLOCKED and BlockKind.ESCALATED were fully retired
    # after the transition soak. No live row carries 'blocked' after the
    # idempotent boot migration. See
    # docs/superpowers/specs/2026-06-27-task-status-pathB-stored-design.md §I.
    SUPERSEDED = "superseded"



class BlockKind(StrEnum):
    # Path B: the waiting-reason discriminant for an IN_PROGRESS task —
    # "what this task is internally waiting on (NULL = a subprocess is running
    # now)". Live domain narrowed to {DELEGATED, BLOCKED_ON_JOB}.
    DELEGATED = "delegated"
    BLOCKED_ON_JOB = "blocked_on_job"


class ReviewVerdict(StrEnum):
    APPROVE = "approve"
    REVISE = "revise"
    REJECT = "reject"


def _now() -> datetime:
    return datetime.now(timezone.utc)


class TaskRecord(BaseModel):
    id: str
    status: TaskStatus = TaskStatus.PENDING
    assigned_agent: str | None = None
    team: str = "engineering"
    # Provenance, NOT a behavior label: "subtask" iff spawned from an ongoing
    # task; "task" otherwise (founder-dispatched root). The orchestration gate
    # in run_step keys on this — see
    # docs/superpowers/specs/2026-06-03-subtask-composite-task-design.md.
    task_type: Literal["task", "subtask"] = "task"
    brief: str
    parent_task_id: str | None = None
    revisit_of_task_id: str | None = None
    dispatched_from_thread_id: str | None = None
    block_kind: BlockKind | None = None
    blocked_on_job_ids: str | None = None
    # In-flight inline delegation chain (JSON-serialized ChainState). NULL when no
    # chain is active on this parent. See docs/superpowers/specs/2026-05-30-inline-
    # delegation-chain-design.md.
    active_chain: str | None = None
    # In-flight fan-out metadata (JSON-serialized FanoutState). NULL when no
    # fan-out is active on this parent. Set atomically with child spawns;
    # cleared on successful join claim or terminal parent close.
    active_fanout: str | None = None
    note: str | None = None
    final_output_dir: str | None = None
    orchestration_step_count: int = 0
    revision_count: int = 0
    # Per-task override for the agent-session subprocess timeout (seconds).
    # NULL → fall through to org/config.yaml, then Settings default. Set by
    # `happyranch revisit --session-timeout-seconds`; inherited from parent on
    # delegate, and from predecessor root on revisit.
    session_timeout_seconds: int | None = None
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)
    completed_at: datetime | None = None
    # Founder-initiated cancellation marker. Under Path B a new cancellation
    # sets status=CANCELLED alongside this timestamp; historical rows left
    # as-is carry the old status=FAILED + cancelled_at shape, so derivations
    # that must classify cancellation (e.g. _classify_predecessor_status) read
    # `cancelled_at` presence rather than the status label for backward compat.
    cancelled_at: datetime | None = None
    last_heartbeat: datetime | None = None
    # OS pid of the executor subprocess, persisted at session start for
    # daemon-restart liveness probe (THR-079). NULL for tasks that predate
    # the column or that haven't reached _on_started yet. Internal signal —
    # not serialized to API responses.
    executor_pid: int | None = None
    # Session id of the CURRENT live subprocess, persisted at session start
    # alongside executor_pid. Used by the daemon-restart sweep (THR-090 Track A)
    # to scope orphaned-result detection to the current session only — a
    # prior-step result row carries a different session uuid and must never
    # match. NULL for tasks that predate the column or that haven't reached
    # _on_started yet. Internal signal — not serialized to API responses.
    current_session_id: str | None = None
    # Timestamp of the FIRST zombie detection for the ongoing zombie reaper
    # (THR-090 Track B). Set when a zombie predicate matches; used for
    # flag-then-cancel-on-TTL. Cleared (set to NULL) if the task recovers
    # before the TTL expires. NULL default — never been flagged. Internal
    # signal — not serialized to API responses.
    zombie_flagged_at: datetime | None = None


class TaskAttachmentRef(BaseModel):
    """Reference to a previously uploaded task attachment.

    The canonical model lives here so the orchestrator and daemon share
    the exact same shape without importing daemon route internals.
    """
    storage_key: str
    display_name: str | None = None


class ChainLeg(BaseModel):
    """One leg of an inline delegation chain. The manager declares legs 2..N in
    NextStep.then; the first leg is the existing delegate payload (agent +
    prompt + optional expect_verdict).
    """
    agent: str
    prompt: str
    expect_verdict: str | None = None
    # THR-109: per-leg task attachments. Only referenced when this leg is
    # auto-advanced and the orchestrator spawns the next child task.
    # Keys must have been pre-uploaded via `happyranch tasks attach-upload`.
    attachments: list[TaskAttachmentRef] | None = None


class FanoutChild(BaseModel):
    """One child in a fanout/parallel NextStep. Phase 1: read-only children only —
    each carries agent + prompt. ``then`` and ``expect_verdict`` are NOT allowed in
    Phase 1 and are parse-rejected (mutating fan-out is out of scope).
    """
    agent: str
    prompt: str
    # Phase 1 rejects these fields — they exist only for Phase 2+ forward compat.
    then: list[ChainLeg] = Field(default_factory=list)
    expect_verdict: str | None = None
    # THR-109: per-child task attachments. A pipeline carrier owns its
    # declared refs; its spawned first leg receives them only through
    # normal ancestor inheritance, never a duplicate link/claim.
    attachments: list[TaskAttachmentRef] | None = None
    # THR-078: mandatory lineage link when this fan-out child retries a
    # FAILED sibling assigned to the same agent.
    revisit_of_task_id: str | None = None


class ManagerSupersessionAttestation(BaseModel):
    """Manager-supplied postmortem evidence for a THR-152 supersession."""

    model_config = {"extra": "forbid", "strict": True}

    recovery_reason: StrictStr
    policy_product_intent_unchanged: StrictBool
    no_budget_or_external_commitment: StrictBool
    no_permission_or_cross_team_change: StrictBool
    no_schema_auth_security_privacy_or_data_access_change: StrictBool
    no_unresolved_founder_gate: StrictBool

    @field_validator("recovery_reason")
    @classmethod
    def _recovery_reason_is_nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("supersede.attestation.recovery_reason must be a nonblank string")
        return value

    @field_validator(
        "policy_product_intent_unchanged",
        "no_budget_or_external_commitment",
        "no_permission_or_cross_team_change",
        "no_schema_auth_security_privacy_or_data_access_change",
        "no_unresolved_founder_gate",
    )
    @classmethod
    def _declaration_must_affirm_no_known_gate(cls, value: bool) -> bool:
        if value is not True:
            raise ValueError("supersede attestation declarations must affirm no known gate")
        return value


class NextStep(BaseModel):
    """Decision returned by a task owner for what the orchestrator should do next."""
    action: Literal["delegate", "done", "escalate", "fanout", "parallel", "supersede"]
    agent: str | None = None
    prompt: str | None = None
    expect_verdict: str | None = None
    then: list[ChainLeg] = Field(default_factory=list)
    children: list[FanoutChild] = Field(default_factory=list)
    width_cap_ack: int | None = None
    # THR-078: when a fan-out owner re-delegates a failed slice, this field
    # carries the failed child's task id so the orchestrator can track
    # per-slice retry count from existing DB lineage (no schema migration).
    revisit_of_task_id: str | None = None
    # THR-109: direct delegate attachments. Keys must have been pre-uploaded
    # via `happyranch tasks attach-upload`; no ambient paths or upload-on-
    # decision route exists.
    attachments: list[TaskAttachmentRef] | None = None

    @field_validator('action')
    @classmethod
    def _normalize_parallel_alias(cls, v: str) -> str:
        """Accept ``parallel`` as an alias for ``fanout``.

        Downstream code always sees ``fanout`` after validation so no
        dispatch changes are needed."""
        if v == "parallel":
            return "fanout"
        return v
    join_summary: str | None = None
    summary: str | None = None
    reason: str | None = None
    successor_brief: str | None = None
    rationale: str | None = None
    attestation: ManagerSupersessionAttestation | None = None

    @model_validator(mode="before")
    @classmethod
    def _supersede_is_a_closed_payload(cls, value: object) -> object:
        """Keep the manager supersession decision deliberately non-extensible.

        The target and authority come only from the claimed task/session, never
        from a manager-supplied override.  Other decisions retain legacy
        permissive-extra parsing for wire compatibility.
        """
        if not isinstance(value, dict) or value.get("action") != "supersede":
            return value
        allowed = {"action", "successor_brief", "rationale", "attestation"}
        extra = set(value) - allowed
        if extra:
            raise ValueError(
                "supersede accepts only action, successor_brief, and rationale; "
                f"forbidden fields: {', '.join(sorted(extra))}"
            )
        for field_name in ("successor_brief", "rationale"):
            field_value = value.get(field_name)
            if not isinstance(field_value, str) or not field_value.strip():
                raise ValueError(f"supersede.{field_name} must be a nonblank string")
        if "attestation" not in value:
            raise ValueError("supersede.attestation is required")
        if value.get("attestation") is None:
            raise ValueError("supersede.attestation must not be null")
        return value


class LocalCiEvidence(BaseModel):
    """Evidence that a pushed PR's local CI ran successfully.

    Wire contract — every field is strict:
      - ``command`` MUST be the exact string "scripts/local_ci.sh all".
        Non-string values (including null) and any other string are rejected.
      - ``exit_code`` MUST be the exact integer 0.  Boolean (true/false) and
        string "0" are rejected by StrictInt — Pydantic v2 strict mode does
        NOT coerce them.
      - Extra keys (any third field beyond command + exit_code) are forbidden
        via model-level ``extra='forbid'``.
    """
    model_config = {"extra": "forbid"}

    command: StrictStr
    exit_code: StrictInt

    @field_validator("command")
    @classmethod
    def _command_must_be_all_target(cls, v: str) -> str:
        if v != "scripts/local_ci.sh all":
            raise ValueError(
                f"local_ci.command must be 'scripts/local_ci.sh all', got {v!r}"
            )
        return v

    @field_validator("exit_code")
    @classmethod
    def _exit_code_must_be_zero(cls, v: int) -> int:
        if v != 0:
            raise ValueError(
                f"local_ci.exit_code must be 0 for a pushed PR, got {v}"
            )
        return v


class CompletionReport(BaseModel):
    task_id: str
    agent: str
    status: str
    confidence: int = Field(ge=0, le=100)
    output_summary: str
    # Optional structured outcome for review/QA-type workers (APPROVE, PASS,
    # REQUEST_CHANGES, etc.). Free-string; per-team vocabulary lives in each
    # team's workflow KB entry. Used by inline delegation chains to gate
    # auto-advance.
    verdict: str | None = None
    # Task-owner-only: structured next-step decision. Subtask agents leave this None.
    # Separating the decision from the prose summary eliminates the
    # double-encoding trap where the manager's output_summary had to itself
    # be JSON (see TASK-071 post-mortem).
    decision: NextStep | None = None
    manager_self_evaluation: dict | None = None
    risks_flagged: list[str] = Field(default_factory=list)
    dependencies: list[str] = Field(default_factory=list)
    suggested_reviewer_focus: list[str] = Field(default_factory=list)
    output_dir: str | None = None
    waiting_on_job_ids: list[str] = Field(default_factory=list)
    # Push-PR local CI evidence. Optional for non-PR completions;
    # contractually required for any pushed-PR report.
    local_ci: LocalCiEvidence | None = None


class ManagerSelfEvaluation(BaseModel):
    """Strict manager-only S6a result. No prose-bearing field is permitted."""
    model_config = {"extra": "forbid", "strict": True}

    contract_id: StrictStr
    contract_version: StrictStr
    contract_digest: StrictStr
    root_task_id: StrictStr
    manager_session_id: StrictStr
    release_id: StrictStr
    policy_version: StrictStr
    policy_digest: StrictStr
    activation_id: StrictStr
    activation_epoch: StrictInt
    provider_id: StrictStr
    executor_kind: StrictStr
    model_id: StrictStr
    disposition: Literal["escalate", "continue_same_root"]
    clause_id: StrictStr
    action: Literal["escalate_to_founder", "continue_same_root"]
    confidence: float = Field(strict=True, ge=0.0, le=1.0)
    uncertainty_codes: list[Literal[
        "low_confidence", "ambiguous", "missing_evidence",
        "conflicting_evidence", "novel",
    ]] = Field(default_factory=list)

    @field_validator("contract_digest", "policy_digest")
    @classmethod
    def _self_evaluation_digests(cls, value: str, info):
        return validate_authority_digest(value, f"self_evaluation.{info.field_name}")


# THR-229 v2 values are deliberately separate from ``ManagerSelfEvaluation``.
# The latter is the persisted v1 wire contract and must remain byte-for-byte
# compatible until a later staged consumer selects v2.
AUTHORITY_POLICY_V2_CONTRACT_ID = "authority_policy_v2"
AUTHORITY_POLICY_V2_CONTRACT_VERSION = "v2"
AUTHORITY_POLICY_V2_WIRE_REVISION = 1
AUTHORITY_POLICY_V2_CONTRACT_PREIMAGE = {
    "algorithm": "sha256",
    "contract_id": AUTHORITY_POLICY_V2_CONTRACT_ID,
    "contract_version": AUTHORITY_POLICY_V2_CONTRACT_VERSION,
    "kind": "manager_self_evaluation",
    "wire_revision": AUTHORITY_POLICY_V2_WIRE_REVISION,
}
_AUTHORITY_POLICY_V2_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")


def authority_policy_v2_canonical_json_bytes(value: object) -> bytes:
    """Return the frozen v2 canonical JSON bytes without text normalization."""
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")


def authority_policy_v2_sha256(value: object) -> str:
    return hashlib.sha256(authority_policy_v2_canonical_json_bytes(value)).hexdigest()


def authority_policy_v2_contract_digest() -> str:
    return authority_policy_v2_sha256(AUTHORITY_POLICY_V2_CONTRACT_PREIMAGE)


def _reject_authority_policy_v2_json_constant(name: str) -> object:
    """Reject JSON ``NaN``/``Infinity``/``-Infinity`` before value validation."""
    raise ValueError(f"v2 wire payload must not contain {name}")


def decode_authority_policy_v2_json(raw: str | bytes) -> dict[str, object]:
    """Decode a v2 wire object strictly.

    R2 requires UTF-8 bytes: ``bytes`` input is decoded explicitly with strict
    UTF-8 so ``json.loads`` cannot auto-detect UTF-16/UTF-32. Duplicate members,
    non-object roots, and ``NaN``/``Infinity`` constants are rejected before any
    typed validation.
    """
    def reject_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
        decoded: dict[str, object] = {}
        for key, value in pairs:
            if key in decoded:
                raise ValueError(f"duplicate JSON member {key!r}")
            decoded[key] = value
        return decoded

    if isinstance(raw, bytes):
        try:
            text = raw.decode("utf-8", errors="strict")
        except UnicodeDecodeError as exc:
            raise ValueError("v2 wire payload must be valid UTF-8") from exc
    elif isinstance(raw, str):
        text = raw
    else:
        raise ValueError("v2 wire payload must be str or bytes")
    if "\ufeff" in text:
        raise ValueError("v2 wire payload must not contain a BOM")
    decoded = json.loads(
        text,
        object_pairs_hook=reject_duplicates,
        parse_constant=_reject_authority_policy_v2_json_constant,
    )
    if not isinstance(decoded, dict):
        raise ValueError("v2 wire payload must be a JSON object")
    return decoded


def _validate_authority_policy_v2_text(value: str, field_name: str, maximum: int) -> str:
    if not value or value.isspace() or len(value) > maximum:
        raise ValueError(f"{field_name} must be a nonblank string of at most {maximum} scalars")
    if "\x00" in value or any(0xD800 <= ord(char) <= 0xDFFF for char in value):
        raise ValueError(f"{field_name} must not contain NUL or surrogate code points")
    return value


def _validate_authority_policy_v2_digest(value: str, field_name: str) -> str:
    if not _AUTHORITY_POLICY_V2_DIGEST_RE.fullmatch(value):
        raise ValueError(f"{field_name} must be 64 lowercase hexadecimal characters")
    return value


class AuthorityPolicyV2Assessment(BaseModel):
    """One clause-free applicability assessment for a v2 editable text."""
    model_config = {"extra": "forbid", "strict": True, "frozen": True}

    applicability: Literal["applies", "does_not_apply", "uncertain"]
    confidence: StrictInt = Field(ge=0, le=100)
    uncertainty_codes: list[Literal[
        "ambiguous_scope", "missing_context", "conflicting_evidence",
        "unknown_authorization", "insufficient_confidence", "unsupported_version",
    ]] = Field(strict=True)

    @field_validator("uncertainty_codes")
    @classmethod
    def _codes_are_unique(cls, values: list[str]) -> list[str]:
        if len(values) != len(set(values)):
            raise ValueError("uncertainty_codes must not contain duplicates")
        return values


class AuthorityPolicyV2ManagerSelfEvaluation(BaseModel):
    """Strict, advisory v2 evaluation envelope; it grants no runtime authority."""
    model_config = {"extra": "forbid", "strict": True, "frozen": True}

    activation_epoch: StrictInt = Field(ge=1, le=2147483647)
    activation_id: StrictStr
    contract_digest: StrictStr
    contract_id: Literal[AUTHORITY_POLICY_V2_CONTRACT_ID]
    contract_version: Literal[AUTHORITY_POLICY_V2_CONTRACT_VERSION]
    executor_kind: StrictStr
    manager_session_id: StrictStr
    model_id: StrictStr
    policy_digest: StrictStr
    policy_version: StrictInt = Field(ge=1, le=2147483647)
    provider_id: StrictStr
    release_id: StrictStr
    root_task_id: StrictStr
    what_not_to_escalate: AuthorityPolicyV2Assessment
    what_to_escalate: AuthorityPolicyV2Assessment

    @field_validator("contract_digest", "policy_digest")
    @classmethod
    def _v2_digests_are_lower_hex(cls, value: str, info) -> str:
        return _validate_authority_policy_v2_digest(value, info.field_name)

    @field_validator("activation_id", "release_id")
    @classmethod
    def _v2_prefixed_ids_are_well_formed(cls, value: str, info) -> str:
        prefix = "APV2A-" if info.field_name == "activation_id" else "APV2-"
        if not value.startswith(prefix):
            raise ValueError(f"{info.field_name} must start with {prefix}")
        _validate_authority_policy_v2_digest(value[len(prefix):], info.field_name)
        return value

    @field_validator("executor_kind", "manager_session_id", "model_id", "provider_id", "root_task_id")
    @classmethod
    def _v2_identity_scalars_are_strict(cls, value: str, info) -> str:
        return _validate_authority_policy_v2_text(value, info.field_name, 128)

    @model_validator(mode="after")
    def _release_id_matches_policy_digest(self) -> AuthorityPolicyV2ManagerSelfEvaluation:
        if self.release_id != f"APV2-{self.policy_digest}":
            raise ValueError("release_id must contain policy_digest")
        if self.contract_digest != authority_policy_v2_contract_digest():
            raise ValueError("contract_digest does not match the v2 contract")
        return self


def authority_policy_v2_release_preimage(
    *, contract_digest: str, policy_id: str, team: str, title: str, version: int,
    what_to_escalate: str, what_not_to_escalate: str,
) -> dict[str, object]:
    return {
        "contract_digest": contract_digest, "policy_id": policy_id, "team": team,
        "title": title, "version": version,
        "what_not_to_escalate": what_not_to_escalate,
        "what_to_escalate": what_to_escalate,
    }


def authority_policy_v2_release_digest(**values: object) -> str:
    return authority_policy_v2_sha256(authority_policy_v2_release_preimage(**values))


def authority_policy_v2_activation_digest(
    *, action: str, previous_selector_id: str, release_digest: str, release_id: str,
    selector_epoch: int, team: str,
) -> str:
    return authority_policy_v2_sha256({
        "action": action, "previous_selector_id": previous_selector_id,
        "release_digest": release_digest, "release_id": release_id,
        "selector_epoch": selector_epoch, "team": team,
    })


def authority_policy_v2_candidate_claim_digest(values: Mapping[str, object]) -> str:
    """Digest the exact frozen claim-key preimage; callers own binding authentication."""
    return authority_policy_v2_sha256(dict(values))


def authority_policy_v2_session_binding_preimage(
    *, activation_epoch: int, activation_id: str, contract_digest: str,
    contract_id: str, contract_version: str, executor_kind: str, manager_agent: str,
    manager_session_id: str, model_id: str, policy_digest: str, policy_version: int,
    provider_id: str, release_id: str, root_task_id: str, selector_id: str, team: str,
) -> dict[str, object]:
    """Frozen R2 launch-binding preimage.

    This is exactly the accepted launch-binding object (the sixteen identity
    fields); the creation timestamp is deliberately outside the content
    identity so an exact repeat of the same launch tuple is byte-identical and
    therefore idempotent.
    """
    return {
        "activation_epoch": activation_epoch,
        "activation_id": activation_id,
        "contract_digest": contract_digest,
        "contract_id": contract_id,
        "contract_version": contract_version,
        "executor_kind": executor_kind,
        "manager_agent": manager_agent,
        "manager_session_id": manager_session_id,
        "model_id": model_id,
        "policy_digest": policy_digest,
        "policy_version": policy_version,
        "provider_id": provider_id,
        "release_id": release_id,
        "root_task_id": root_task_id,
        "selector_id": selector_id,
        "team": team,
    }


_AUTHORITY_POLICY_V2_POLICY_ID_RE = re.compile(r"^[a-z][a-z0-9-]{0,63}$")
# Mirrors the existing saved-policy secret-shape rejection in
# ``runtime/daemon/routes/authority_policy.py`` byte-for-byte. B1 keeps the
# rejection at the v2 control value boundary; the route keeps its own copy
# because B1 wires no route.
AUTHORITY_POLICY_SECRET_SHAPE_RE = re.compile(
    r"(?i)(?:authorization\s*:\s*bearer|bearer\s+[a-z0-9._-]{16,}|"
    r"(?:api[_-]?key|secret|password|token)\s*[:=]\s*\S{8,})"
)
AUTHORITY_POLICY_V2_TEAM = "engineering"
AUTHORITY_POLICY_V2_MAX_CANONICAL_BYTES = 65536
_AUTHORITY_POLICY_V2_MAX_TITLE = 200
_AUTHORITY_POLICY_V2_MAX_TEXT = 20000
# DESIGN R2 makes ``policy_version`` the release revision and the v2 wire team
# surface exactly ``engineering``; the broader family is selected later by the
# persisted selector, not by this value layer.


class AuthorityPolicyV2Release(BaseModel):
    """Strict paired v2 release value.

    Both editable texts are required together; there is no shape for saving or
    activating one alone. The value layer validates bytes and derives the exact
    approved preimage/digest only. Authentication of the launch binding and any
    continuation authority remain a later persisted consumer's obligation.
    """
    model_config = {"extra": "forbid", "strict": True, "frozen": True}

    contract_digest: StrictStr
    policy_id: StrictStr
    team: Literal[AUTHORITY_POLICY_V2_TEAM]
    title: StrictStr
    version: StrictInt = Field(ge=1, le=2147483647)
    what_not_to_escalate: StrictStr
    what_to_escalate: StrictStr

    @field_validator("contract_digest")
    @classmethod
    def _v2_release_contract_digest(cls, value: str, info) -> str:
        return _validate_authority_policy_v2_digest(value, info.field_name)

    @field_validator("policy_id")
    @classmethod
    def _v2_release_policy_id(cls, value: str) -> str:
        if not _AUTHORITY_POLICY_V2_POLICY_ID_RE.fullmatch(value):
            raise ValueError("policy_id must match [a-z][a-z0-9-]{0,63}")
        return value

    @field_validator("title")
    @classmethod
    def _v2_release_title(cls, value: str, info) -> str:
        return _validate_authority_policy_v2_text(value, info.field_name, _AUTHORITY_POLICY_V2_MAX_TITLE)

    @field_validator("what_to_escalate", "what_not_to_escalate")
    @classmethod
    def _v2_release_texts(cls, value: str, info) -> str:
        return _validate_authority_policy_v2_text(value, info.field_name, _AUTHORITY_POLICY_V2_MAX_TEXT)

    @model_validator(mode="after")
    def _v2_release_is_contract_bound_and_bounded(self) -> AuthorityPolicyV2Release:
        if self.contract_digest != authority_policy_v2_contract_digest():
            raise ValueError("contract_digest does not match the v2 contract")
        canonical = authority_policy_v2_canonical_json_bytes(self.preimage())
        if len(canonical) > AUTHORITY_POLICY_V2_MAX_CANONICAL_BYTES:
            raise ValueError(
                "release canonical JSON exceeds "
                f"{AUTHORITY_POLICY_V2_MAX_CANONICAL_BYTES} bytes"
            )
        return self

    def preimage(self) -> dict[str, object]:
        return authority_policy_v2_release_preimage(
            contract_digest=self.contract_digest, policy_id=self.policy_id,
            team=self.team, title=self.title, version=self.version,
            what_to_escalate=self.what_to_escalate,
            what_not_to_escalate=self.what_not_to_escalate,
        )

    @property
    def policy_digest(self) -> str:
        return authority_policy_v2_sha256(self.preimage())

    @property
    def release_id(self) -> str:
        return f"APV2-{self.policy_digest}"


_AUTHORITY_POLICY_V2_SELECTOR_ID_RE = re.compile(r"^APS-[0-9a-f]{64}$")
_AUTHORITY_POLICY_V2_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
AUTHORITY_POLICY_V2_CONTROL_ACTIONS = ("bootstrap", "activate", "reactivate_rollback")
AUTHORITY_POLICY_V2_SELECTOR_FAMILIES = ("empty", "legacy_v1", "v2")


def _validate_authority_policy_v2_selector_ref(value: str | None, field_name: str) -> str | None:
    if value is None:
        return None
    if not _AUTHORITY_POLICY_V2_SELECTOR_ID_RE.fullmatch(value):
        raise ValueError(f"{field_name} must be null or APS- followed by 64 lowercase hex")
    return value


def _validate_authority_policy_v2_request_id(value: str, field_name: str) -> str:
    if not _AUTHORITY_POLICY_V2_REQUEST_ID_RE.fullmatch(value):
        raise ValueError(f"{field_name} must match [A-Za-z0-9][A-Za-z0-9._:-]{{0,127}}")
    return value


class AuthorityPolicyV2Activation(BaseModel):
    """Immutable v2 selection receipt (APV2A content identity)."""
    model_config = {"extra": "forbid", "strict": True, "frozen": True}

    id: StrictStr
    team: StrictStr
    selector_epoch: StrictInt = Field(ge=1, le=2147483647)
    release_id: StrictStr
    release_digest: StrictStr
    previous_selector_id: StrictStr | None = None
    action: Literal["bootstrap", "activate", "reactivate_rollback"]
    request_id: StrictStr
    request_digest: StrictStr
    activation_digest: StrictStr
    created_at: StrictStr

    @field_validator("release_digest", "request_digest", "activation_digest")
    @classmethod
    def _v2_activation_digests(cls, value: str, info) -> str:
        return _validate_authority_policy_v2_digest(value, info.field_name)

    @field_validator("request_id")
    @classmethod
    def _v2_activation_request_id(cls, value: str, info) -> str:
        return _validate_authority_policy_v2_request_id(value, info.field_name)

    @field_validator("previous_selector_id")
    @classmethod
    def _v2_activation_previous_selector(cls, value: str | None, info) -> str | None:
        return _validate_authority_policy_v2_selector_ref(value, info.field_name)

    @model_validator(mode="after")
    def _v2_activation_content_ids(self) -> AuthorityPolicyV2Activation:
        if self.release_id != f"APV2-{self.release_digest}":
            raise ValueError("release_id must contain release_digest")
        expected = authority_policy_v2_activation_digest(
            action=self.action, previous_selector_id=self.previous_selector_id,
            release_digest=self.release_digest, release_id=self.release_id,
            selector_epoch=self.selector_epoch, team=self.team,
        )
        if self.activation_digest != expected:
            raise ValueError("activation_digest does not match canonical activation semantics")
        if self.id != f"APV2A-{self.activation_digest}":
            raise ValueError("id must be APV2A- followed by activation_digest")
        return self

    @classmethod
    def create(cls, **values: object) -> AuthorityPolicyV2Activation:
        """Trusted construction boundary deriving the exact content identity."""
        if "activation_digest" in values:
            raise ValueError("activation_digest is derived, never supplied")
        digest = authority_policy_v2_activation_digest(
            action=values["action"], previous_selector_id=values.get("previous_selector_id"),
            release_digest=values["release_digest"], release_id=values["release_id"],
            selector_epoch=values["selector_epoch"], team=values["team"],
        )
        return cls.model_validate({
            **values, "activation_digest": digest, "id": f"APV2A-{digest}",
        })


class AuthorityPolicySelector(BaseModel):
    """One authenticated team selector (empty epoch0, legacy_v1 epoch1 or v2)."""
    model_config = {"extra": "forbid", "strict": True, "frozen": True}

    team: StrictStr
    selector_id: StrictStr
    family: Literal["empty", "legacy_v1", "v2"]
    selector_epoch: StrictInt = Field(ge=0, le=2147483647)
    previous_selector_id: StrictStr | None = None
    legacy_activation_id: StrictStr | None = None
    v2_activation_id: StrictStr | None = None
    created_at: StrictStr

    @field_validator("selector_id", "previous_selector_id")
    @classmethod
    def _v2_selector_refs(cls, value: str | None, info) -> str | None:
        return _validate_authority_policy_v2_selector_ref(value, info.field_name)

    @model_validator(mode="after")
    def _v2_selector_three_arm_and_identity(self) -> AuthorityPolicySelector:
        if self.family == "empty":
            valid_arm = (
                self.selector_epoch == 0 and self.previous_selector_id is None
                and self.legacy_activation_id is None and self.v2_activation_id is None
            )
            expected = authority_policy_v2_initializer_selector_id(
                family="empty", legacy_activation_id=None, selector_epoch=0, team=self.team,
            )
        elif self.family == "legacy_v1":
            # Accepted R2: only the initial legacy epoch-1 selector is bound by
            # the frozen initializer preimage. Every later legacy selection is a
            # regular APS preimage with its real predecessor, exactly like v2.
            if self.legacy_activation_id is None or self.v2_activation_id is not None:
                valid_arm = False
                expected = self.selector_id
            elif self.previous_selector_id is None:
                valid_arm = self.selector_epoch == 1
                expected = authority_policy_v2_initializer_selector_id(
                    family="legacy_v1", legacy_activation_id=self.legacy_activation_id,
                    selector_epoch=1, team=self.team,
                )
            else:
                # Accepted R2: a selection away from the genuinely-empty
                # initializer is legitimate at selector epoch 1 with the empty
                # selector as its predecessor (no prior policy selection), so
                # the regular APS preimage still bounds it. Every other legacy
                # selection is at epoch >= 2 with its real predecessor.
                empty_initializer_id = authority_policy_v2_initializer_selector_id(
                    family="empty", legacy_activation_id=None, selector_epoch=0,
                    team=self.team,
                )
                valid_arm = self.selector_epoch >= 2 or (
                    self.selector_epoch == 1
                    and self.previous_selector_id == empty_initializer_id
                )
                expected = authority_policy_v2_selector_id(
                    activation_id=self.legacy_activation_id, family="legacy_v1",
                    previous_selector_id=self.previous_selector_id,
                    selector_epoch=self.selector_epoch, team=self.team,
                )
        else:
            valid_arm = (
                self.selector_epoch >= 1 and self.legacy_activation_id is None
                and self.v2_activation_id is not None
            )
            expected = authority_policy_v2_selector_id(
                activation_id=self.v2_activation_id, family="v2",
                previous_selector_id=self.previous_selector_id,
                selector_epoch=self.selector_epoch, team=self.team,
            )
        if not valid_arm:
            raise ValueError("selector family/epoch/reference arm is invalid")
        if self.selector_id != expected:
            raise ValueError("selector_id does not match the frozen selector preimage")
        return self


class AuthorityPolicyV2SessionBinding(BaseModel):
    """Immutable authenticated launch binding for one manager runtime session.

    The binding records exactly the selector/epoch, immutable release and
    activation, contract, and resolved provider/executor/model identity that a
    launch used. Its content identity is the frozen R2 launch-binding preimage;
    ``binding_id`` is ``APV2B-`` followed by that SHA-256, so an exact repeat of
    the same tuple yields the same immutable row and a changed tuple refuses.
    """
    model_config = {"extra": "forbid", "strict": True, "frozen": True}

    activation_epoch: StrictInt = Field(ge=1, le=2147483647)
    activation_id: StrictStr
    contract_digest: StrictStr
    contract_id: Literal[AUTHORITY_POLICY_V2_CONTRACT_ID]
    contract_version: Literal[AUTHORITY_POLICY_V2_CONTRACT_VERSION]
    executor_kind: StrictStr
    manager_agent: StrictStr
    manager_session_id: StrictStr
    model_id: StrictStr
    policy_digest: StrictStr
    policy_version: StrictInt = Field(ge=1, le=2147483647)
    provider_id: StrictStr
    release_id: StrictStr
    root_task_id: StrictStr
    selector_id: StrictStr
    team: Literal[AUTHORITY_POLICY_V2_TEAM]
    created_at: datetime = Field(default_factory=_now)

    @field_validator("contract_digest", "policy_digest")
    @classmethod
    def _v2_binding_digests_are_lower_hex(cls, value: str) -> str:
        if not _AUTHORITY_POLICY_V2_DIGEST_RE.fullmatch(value):
            raise ValueError("binding digest must be 64 lowercase hexadecimal characters")
        return value

    @field_validator("activation_id")
    @classmethod
    def _v2_binding_activation_id(cls, value: str) -> str:
        if not value.startswith("APV2A-"):
            raise ValueError("activation_id must start with APV2A-")
        _validate_authority_policy_v2_digest(value[len("APV2A-"):], "activation_id")
        return value

    @field_validator("release_id")
    @classmethod
    def _v2_binding_release_id(cls, value: str) -> str:
        if not value.startswith("APV2-"):
            raise ValueError("release_id must start with APV2-")
        _validate_authority_policy_v2_digest(value[len("APV2-"):], "release_id")
        return value

    @field_validator("selector_id")
    @classmethod
    def _v2_binding_selector_ref(cls, value: str) -> str:
        return _validate_authority_policy_v2_selector_ref(value, "selector_id")

    @field_validator(
        "executor_kind", "manager_agent", "manager_session_id", "model_id",
        "provider_id", "root_task_id",
    )
    @classmethod
    def _v2_binding_identity_scalars(cls, value: str) -> str:
        return _validate_authority_policy_v2_text(value, "binding identity field", 128)

    @model_validator(mode="after")
    def _v2_binding_is_contract_bound(self) -> AuthorityPolicyV2SessionBinding:
        if self.contract_digest != authority_policy_v2_contract_digest():
            raise ValueError("contract_digest does not match the v2 contract")
        if self.release_id != f"APV2-{self.policy_digest}":
            raise ValueError("release_id must contain policy_digest")
        return self

    def preimage(self) -> dict[str, object]:
        return authority_policy_v2_session_binding_preimage(
            activation_epoch=self.activation_epoch, activation_id=self.activation_id,
            contract_digest=self.contract_digest, contract_id=self.contract_id,
            contract_version=self.contract_version, executor_kind=self.executor_kind,
            manager_agent=self.manager_agent, manager_session_id=self.manager_session_id,
            model_id=self.model_id, policy_digest=self.policy_digest,
            policy_version=self.policy_version, provider_id=self.provider_id,
            release_id=self.release_id, root_task_id=self.root_task_id,
            selector_id=self.selector_id, team=self.team,
        )

    @property
    def binding_digest(self) -> str:
        return authority_policy_v2_sha256(self.preimage())

    @property
    def binding_id(self) -> str:
        return f"APV2B-{self.binding_digest}"


# THR-229 checkpoint C2: result-keyed immutable attempt journal.  The attempt
# is created by the callback admission transaction.  THR-229 checkpoints C3b
# and C3c complete the staged, unmerged definition admitted -> claimed ->
# claim_audited -> evaluated -> evaluation_audited -> consumed ->
# consumed_audited; later finalize/final/spend transitions remain subsequent C
# work.  The extension is additive to this PR's own new table (the released
# schema has no v2 attempt stage); the identity preimage is unchanged.
AUTHORITY_POLICY_V2_ATTEMPT_STAGE_ADMITTED = "admitted"
AUTHORITY_POLICY_V2_ATTEMPT_STAGE_CLAIMED = "claimed"
AUTHORITY_POLICY_V2_ATTEMPT_STAGE_CLAIM_AUDITED = "claim_audited"
AUTHORITY_POLICY_V2_ATTEMPT_STAGE_EVALUATED = "evaluated"
AUTHORITY_POLICY_V2_ATTEMPT_STAGE_EVALUATION_AUDITED = "evaluation_audited"
AUTHORITY_POLICY_V2_ATTEMPT_STAGE_CONSUMED = "consumed"
AUTHORITY_POLICY_V2_ATTEMPT_STAGE_CONSUMED_AUDITED = "consumed_audited"
AUTHORITY_POLICY_V2_ATTEMPT_STAGES = frozenset({
    AUTHORITY_POLICY_V2_ATTEMPT_STAGE_ADMITTED,
    AUTHORITY_POLICY_V2_ATTEMPT_STAGE_CLAIMED,
    AUTHORITY_POLICY_V2_ATTEMPT_STAGE_CLAIM_AUDITED,
    AUTHORITY_POLICY_V2_ATTEMPT_STAGE_EVALUATED,
    AUTHORITY_POLICY_V2_ATTEMPT_STAGE_EVALUATION_AUDITED,
    AUTHORITY_POLICY_V2_ATTEMPT_STAGE_CONSUMED,
    AUTHORITY_POLICY_V2_ATTEMPT_STAGE_CONSUMED_AUDITED,
})
# The only allowed stage transitions for this checkpoint.  Every writer checks
# the exact expected prior stage before advancing; no skipped/replayed/resumed
# transition is accepted.
AUTHORITY_POLICY_V2_ATTEMPT_STAGE_TRANSITIONS = {
    AUTHORITY_POLICY_V2_ATTEMPT_STAGE_ADMITTED:
        AUTHORITY_POLICY_V2_ATTEMPT_STAGE_CLAIMED,
    AUTHORITY_POLICY_V2_ATTEMPT_STAGE_CLAIMED:
        AUTHORITY_POLICY_V2_ATTEMPT_STAGE_CLAIM_AUDITED,
    AUTHORITY_POLICY_V2_ATTEMPT_STAGE_CLAIM_AUDITED:
        AUTHORITY_POLICY_V2_ATTEMPT_STAGE_EVALUATED,
    AUTHORITY_POLICY_V2_ATTEMPT_STAGE_EVALUATED:
        AUTHORITY_POLICY_V2_ATTEMPT_STAGE_EVALUATION_AUDITED,
    AUTHORITY_POLICY_V2_ATTEMPT_STAGE_EVALUATION_AUDITED:
        AUTHORITY_POLICY_V2_ATTEMPT_STAGE_CONSUMED,
    AUTHORITY_POLICY_V2_ATTEMPT_STAGE_CONSUMED:
        AUTHORITY_POLICY_V2_ATTEMPT_STAGE_CONSUMED_AUDITED,
}
AUTHORITY_POLICY_V2_ATTEMPT_FINALIZATION_STATES = frozenset({
    "unfinalized", "continued", "refused", "owner_lost",
})
AUTHORITY_POLICY_V2_RESULT_STAGE_ACTION = "authority_policy_v2_result_stage"
# THR-229 C3c correction: the ONE accepted manager decision action that the v2
# authority-policy evaluation/continuation path serves (the accepted root
# escalation decision).  A missing/malformed/non-escalate persisted decision
# (ordinary ``delegate``/``supersede``/``done`` etc.) can never acquire the v2
# pre-final continuation path; this is inspected as persisted decision DATA and
# is not a prose/sentinel/keyword detector.
AUTHORITY_POLICY_V2_ESCALATION_DECISION_ACTION = "escalate"

# THR-229 checkpoint C3b/C3c: the closed candidate-audit event vocabulary.
# This checkpoint writes the claim/evaluate/consume stage events; later
# final/spend stages extend this set additively under their own checkpoint.
AUTHORITY_POLICY_V2_CANDIDATE_AUDIT_EVENT_CLAIMED = "claimed"
AUTHORITY_POLICY_V2_CANDIDATE_AUDIT_EVENT_EVALUATED = "evaluated"
AUTHORITY_POLICY_V2_CANDIDATE_AUDIT_EVENT_CONSUMED = "consumed"
# THR-229 checkpoint C3d1: the terminal refusal event written on an existing
# candidate (K) when a pre-final attempt is durably refused.  It is append-only
# like the other events and carries no authority.
AUTHORITY_POLICY_V2_CANDIDATE_AUDIT_EVENT_REFUSED = "refused"
# THR-229 checkpoint C3d2: the final continuation event written on the consumed
# candidate by the ONE final continuation transaction.  It is append-only and
# carries no authority by itself: the active envelope plus the pending
# notification/dispatch pointer are the durable continuation evidence.
AUTHORITY_POLICY_V2_CANDIDATE_AUDIT_EVENT_FINAL = "final"
AUTHORITY_POLICY_V2_CANDIDATE_AUDIT_EVENTS = frozenset({
    AUTHORITY_POLICY_V2_CANDIDATE_AUDIT_EVENT_CLAIMED,
    AUTHORITY_POLICY_V2_CANDIDATE_AUDIT_EVENT_EVALUATED,
    AUTHORITY_POLICY_V2_CANDIDATE_AUDIT_EVENT_CONSUMED,
    AUTHORITY_POLICY_V2_CANDIDATE_AUDIT_EVENT_REFUSED,
    AUTHORITY_POLICY_V2_CANDIDATE_AUDIT_EVENT_FINAL,
})

# THR-229 checkpoint C3c: the forward-only candidate (K) lifecycle.  Claim
# creates K ``created``; the evaluation transaction advances it to
# ``evaluated``; the consumption transaction advances ``evaluated`` ->
# ``consumed``.  No stage may skip, repeat, or move backwards, and identity or
# frozen evidence is never mutable.
AUTHORITY_POLICY_V2_CANDIDATE_LIFECYCLE_CREATED = "created"
AUTHORITY_POLICY_V2_CANDIDATE_LIFECYCLE_EVALUATED = "evaluated"
AUTHORITY_POLICY_V2_CANDIDATE_LIFECYCLE_CONSUMED = "consumed"
AUTHORITY_POLICY_V2_CANDIDATE_LIFECYCLE_STAGES = frozenset({
    AUTHORITY_POLICY_V2_CANDIDATE_LIFECYCLE_CREATED,
    AUTHORITY_POLICY_V2_CANDIDATE_LIFECYCLE_EVALUATED,
    AUTHORITY_POLICY_V2_CANDIDATE_LIFECYCLE_CONSUMED,
})
AUTHORITY_POLICY_V2_CANDIDATE_LIFECYCLE_TRANSITIONS = {
    AUTHORITY_POLICY_V2_CANDIDATE_LIFECYCLE_CREATED:
        AUTHORITY_POLICY_V2_CANDIDATE_LIFECYCLE_EVALUATED,
    AUTHORITY_POLICY_V2_CANDIDATE_LIFECYCLE_EVALUATED:
        AUTHORITY_POLICY_V2_CANDIDATE_LIFECYCLE_CONSUMED,
}

# Closed diagnostic codes recorded on the persisted evaluation when the
# sanitized manager assessment was absent/null/malformed or failed its bound
# identity authentication.  These are honest evidence codes, never a valid
# assessment invented from a diagnostic carrier.
AUTHORITY_POLICY_V2_EVALUATION_DIAGNOSTIC_CODES = frozenset({
    "missing_assessment", "null_assessment", "malformed_output", "binding_mismatch",
})

# The closed clause-free outcome vocabulary persisted on V.  This mirrors
# ``AuthorityPolicyV2AssessmentOutcome`` in
# ``runtime/orchestrator/authority_policy.py`` (asserted equal there) without a
# circular import.
AUTHORITY_POLICY_V2_ASSESSMENT_OUTCOMES = frozenset({
    "escalate_applies", "continue_applies", "neither_apply", "uncertain", "invalid",
})

# Bounded, machine-readable stage outcome.  ``refused`` carries exactly one
# closed refusal code; this checkpoint only returns the disposition for the
# later refusal-housekeeping consumer and never mutates task/attempt state.
AUTHORITY_POLICY_V2_STAGE_OUTCOME_STATUSES = frozenset({
    "claimed", "claim_audited", "evaluated", "evaluation_audited",
    "consumed", "consumed_audited", "refused",
})
AUTHORITY_POLICY_V2_STAGE_REFUSAL_CODES = frozenset({
    "cancelled", "owner_lost", "identity_mismatch", "claim_failed",
    "claim_audit_missing", "already_claimed", "already_audited", "schema_drift",
    # C3b correction: the caller already owns a transaction; this stage refuses
    # before it would ever BEGIN/ROLLBACK so the caller's work is untouched.
    "transaction_owned",
    # C3b correction: the frozen claim-time permission-surface evidence no
    # longer authenticates (or is missing/unreadable) at the second boundary.
    "evidence_drift",
    # C3c: the four pre-final evaluation/consumption stage refusals.  Each is a
    # bounded prior-stage/duplicate classification; none is authority.
    "already_evaluated", "already_consumed", "evaluation_missing",
    "evaluation_audit_missing", "evaluation_failed", "consume_failed",
    # C3d2: the ONE final continuation transaction failed as a whole; the
    # winning owner is poisoned and only C3d1 refusal housekeeping may proceed.
    "final_commit_failed",
})

# THR-229 checkpoint C3d1: the terminal pre-final refusal lifecycle.  A refusal
# is a CLOSED diagnostic recorded on the immutable attempt journal (J) by ONE
# Database-owned BEGIN IMMEDIATE transaction.  It is never authority, mints no
# successor/envelope/notification/dispatch, and the closed code allowlist
# rejects free prose.  ``interrupted_pre_final`` is the generic pre-final
# interruption code; the specific failed-stage codes record the cause.
AUTHORITY_POLICY_V2_ATTEMPT_FINALIZATION_REFUSED = "refused"
AUTHORITY_POLICY_V2_ATTEMPT_FINALIZATION_OWNER_LOST = "owner_lost"
AUTHORITY_POLICY_V2_RESULT_STAGE_REFUSED = "refused"
# The audit action recording the server-owned durable failed-stage obligation
# that authorizes housekeeping when the winning owner token was poisoned by an
# owned-stage failure.  It is written only by the server and is never a
# caller-supplied allow boolean.  It is a durable discovery/housekeeping
# obligation, not a second authority path.
AUTHORITY_POLICY_V2_HOUSEKEEPING_OBLIGATION_ACTION = (
    "authority_policy_v2_housekeeping_obligation"
)

AUTHORITY_POLICY_V2_HOUSEKEEPING_REFUSAL_CODES = frozenset({
    "interrupted_pre_final", "claim_failed", "claim_audit_missing",
    "evaluation_failed", "evaluation_audit_missing", "consume_failed",
    "consume_audit_missing", "final_commit_failed", "identity_mismatch",
    "owner_lost", "cancelled", "decision_dispatch_interrupted",
})
# A ``housekeeping_pending`` outcome carries a bounded control classification
# (never a finalized refusal code).  The pending marker itself is the status.
AUTHORITY_POLICY_V2_HOUSEKEEPING_PENDING_CODE = "authority_v2_housekeeping_pending"
AUTHORITY_POLICY_V2_HOUSEKEEPING_PENDING_REASONS = frozenset({
    "transaction_owned", "identity_mismatch", "owner_lost", "evidence_drift",
    "schema_drift", "receipt_missing",
})
AUTHORITY_POLICY_V2_HOUSEKEEPING_OUTCOME_CODES = (
    AUTHORITY_POLICY_V2_HOUSEKEEPING_REFUSAL_CODES
    | AUTHORITY_POLICY_V2_HOUSEKEEPING_PENDING_REASONS
)
AUTHORITY_POLICY_V2_HOUSEKEEPING_OUTCOME_STATUSES = frozenset({
    "refused", "owner_lost", "already_refused", "housekeeping_pending",
})

# THR-229 checkpoint C3d2: the durable continuation envelope (E), recovery
# notification (N) and root-dispatch generation pointer (D).  These are the
# three already-authorized remaining additive tables.  E is inserted ACTIVE by
# the final continuation transaction and repeats/authenticates the complete
# pinned tuple; N is the continuation generation G (its APV2N identity) and
# carries the accepted lifecycle states; D is keyed by root and admits only one
# non-retired generation.  Later publication/admission/spend transition writers
# are separate units and are NOT implemented here; only the closed state
# vocabularies and lifecycle guards are defined.
AUTHORITY_POLICY_V2_ENVELOPE_STATES = frozenset({"active", "consumed"})
AUTHORITY_POLICY_V2_NOTIFICATION_STATES = frozenset({
    "needed", "publishing", "published", "admitted", "settled", "invalidated",
})
AUTHORITY_POLICY_V2_DISPATCH_STATES = frozenset({
    "pending", "admitted", "retired",
})
# THR-229 checkpoint C3d3c1: the RESULT-KEYED spend/decision receipt.  A
# consumed continuation envelope (E) carrying a non-null ``spending_result_id``
# IS the result-keyed spending receipt for that exact reserved-session result;
# its closed, forward-only ``decision_state`` distinguishes the durable READY
# decision receipt from the later ordinary decision-consumer lifecycle.  Only
# the atomic active -> consumed spend-to-ready writer ships in this unit; the
# consumer bookkeeping (ready -> claimed -> applied/refused) remains dark.
AUTHORITY_POLICY_V2_DECISION_STATES = frozenset({
    "ready", "claimed", "applied", "refused",
})
AUTHORITY_POLICY_V2_DECISION_FORWARD_TRANSITIONS = {
    "ready": frozenset({"claimed", "refused"}),
    "claimed": frozenset({"applied", "refused"}),
    "applied": frozenset(),
    "refused": frozenset(),
}
# Closed non-semantic audit actions written by the final continuation
# transaction and the receipt-settlement transaction.  ``af`` in the accepted
# R4 notation is the final candidate/task/hook audit set plus the closed
# ``continued`` result-stage event.
AUTHORITY_POLICY_V2_RESULT_STAGE_CONTINUED = "continued"
AUTHORITY_POLICY_V2_FINAL_TASK_AUDIT_ACTION = "authority_policy_v2_final_task"
AUTHORITY_POLICY_V2_FINAL_HOOK_AUDIT_ACTION = "authority_policy_v2_final_hook"
AUTHORITY_POLICY_V2_RECOVERY_SETTLED_ACTION = "authority_policy_v2_recovery_settled"

# Bounded outcome of the ONE final continuation transaction.  ``continued`` is
# a just-committed continuation; ``already_continued`` is a read-only exact
# replay of the committed final evidence.  ``finalization_pending`` means the
# transaction could not establish safe finalization (or failed) and the prior
# consumed residue is preserved; only refusal/settlement housekeeping may
# proceed, never a remint or re-evaluation.
AUTHORITY_POLICY_V2_FINALIZATION_OUTCOME_STATUSES = frozenset({
    "continued", "already_continued", "finalization_pending",
})
AUTHORITY_POLICY_V2_FINALIZATION_PENDING_REASONS = frozenset({
    "transaction_owned", "identity_mismatch", "owner_lost", "evidence_drift",
    "schema_drift", "already_finalized", "finalization_failed",
})

# Bounded outcome of the separate exact post-final receipt-settlement
# transaction/read.  ``settled`` just committed the exact recovery settlement
# (or verified real ordinary completion evidence); ``already_settled_exact`` is
# a read-only exact replay of an existing settlement; ``settlement_pending``
# means the evidence was unsafe or the transaction failed and the prior
# Pending/E/N/D/J + callback_accepted residue is preserved.
AUTHORITY_POLICY_V2_SETTLEMENT_OUTCOME_STATUSES = frozenset({
    "settled", "already_settled_exact", "settlement_pending",
})
AUTHORITY_POLICY_V2_SETTLEMENT_PENDING_REASONS = frozenset({
    "transaction_owned", "identity_mismatch", "owner_lost", "evidence_drift",
    "receipt_missing", "completion_evidence_missing", "settlement_failed",
})

# THR-229 checkpoint C3d3a: bounded authenticated publication-bookkeeping
# vocabulary.  These statuses/reasons are the closed outcome of the callable
# discovery/claim/acknowledge/failure/invalidate storage seams.  None of them is
# launch authority; the storage seam never performs a queue call and the real
# publication caller plus non-bypassable generation admission remain later units.
AUTHORITY_POLICY_V2_PUBLICATION_LEASE_SECONDS = 30
AUTHORITY_POLICY_V2_PUBLICATION_STATES = frozenset({
    "needed", "publishing", "published",
})
AUTHORITY_POLICY_V2_PUBLICATION_CLAIM_STATUSES = frozenset({
    "claimed", "publication_pending",
})
AUTHORITY_POLICY_V2_PUBLICATION_CLAIM_PENDING_REASONS = frozenset({
    "transaction_owned", "identity_mismatch", "evidence_drift", "owner_lost",
    "not_publishable", "lease_live", "attempt_overflow", "boot_unbound",
    "publication_failed",
})
AUTHORITY_POLICY_V2_PUBLICATION_ACK_STATUSES = frozenset({
    "published", "publish_returned", "ack_pending",
})
AUTHORITY_POLICY_V2_PUBLICATION_ACK_PENDING_REASONS = frozenset({
    "transaction_owned", "identity_mismatch", "evidence_drift", "stale_claim",
    "admission_evidence_missing", "ack_failed",
})
AUTHORITY_POLICY_V2_PUBLICATION_FAILURE_STATUSES = frozenset({
    "failure_recorded", "failure_pending",
})
AUTHORITY_POLICY_V2_PUBLICATION_FAILURE_PENDING_REASONS = frozenset({
    "transaction_owned", "identity_mismatch", "evidence_drift", "stale_claim",
    "failure_failed",
})
AUTHORITY_POLICY_V2_INVALIDATION_STATUSES = frozenset({
    "invalidated", "already_invalidated", "invalidation_pending",
})
AUTHORITY_POLICY_V2_INVALIDATION_PENDING_REASONS = frozenset({
    "transaction_owned", "identity_mismatch", "evidence_drift",
    "not_invalidatable", "invalidation_failed",
})
# Closed result-stage events appended by the publication/invalidation writers.
# They reuse the existing ``authority_policy_v2_result_stage`` action and carry
# the exact attempt identity, so each is discoverable and no new audit scope /
# table / column is introduced.
AUTHORITY_POLICY_V2_RESULT_STAGE_PUBLISH_CLAIMED = "publish_claimed"
AUTHORITY_POLICY_V2_RESULT_STAGE_PUBLISHED = "published"
AUTHORITY_POLICY_V2_RESULT_STAGE_PUBLISH_FAILED = "publish_failed"
AUTHORITY_POLICY_V2_RESULT_STAGE_PUBLISH_RETURNED = "publish_returned"
AUTHORITY_POLICY_V2_RESULT_STAGE_INVALIDATED = "invalidated"

# THR-229 checkpoint C3d3b: bounded generation-admission vocabulary.  These
# statuses/reasons are the closed outcome of the callable atomic generation
# claim and the separate admission-settlement transaction.  ``claimed`` alone
# carries the reserved ``next_session_id`` and never launches work by itself;
# generation admission is the non-bypassable fence for a pending v2 generation.
AUTHORITY_POLICY_V2_GENERATION_CLAIM_STATUSES = frozenset({
    "claimed", "generation_pending",
})
AUTHORITY_POLICY_V2_GENERATION_CLAIM_PENDING_REASONS = frozenset({
    "transaction_owned", "identity_mismatch", "evidence_drift", "owner_lost",
    "not_admissible", "stale_generation", "missing_generation",
    "already_admitted", "generation_failed",
})
AUTHORITY_POLICY_V2_ADMISSION_SETTLEMENT_STATUSES = frozenset({
    "settled", "already_settled_exact", "settlement_pending",
})
AUTHORITY_POLICY_V2_ADMISSION_SETTLEMENT_PENDING_REASONS = frozenset({
    "transaction_owned", "identity_mismatch", "evidence_drift", "owner_lost",
    "not_settleable", "settlement_failed",
})
# Closed result-stage events appended by the generation-admission writers.
# They reuse the existing ``authority_policy_v2_result_stage`` action, carry the
# exact causal identity plus the reserved session, and never mint authority.
AUTHORITY_POLICY_V2_RESULT_STAGE_GENERATION_CLAIMED = "generation_claimed"
AUTHORITY_POLICY_V2_RESULT_STAGE_NOTIFICATION_SETTLED = "notification_settled"

# THR-229 checkpoint C3d3c1: bounded outcome of the ONE atomic next-result
# spend-to-ready transaction.  ``spent`` just committed E active -> consumed
# with the exact spending result and D admitted -> retired; ``already_spent_exact``
# is a read-only exact retry of the authenticated consumed/ready receipt
# (no write, no remint); ``spend_pending`` means NO spend occurred (or the
# transaction failed) and E stayed active / D admitted with the decision
# unapplied.  None of these statuses is launch authority.
AUTHORITY_POLICY_V2_SPEND_STATUSES = frozenset({
    "spent", "already_spent_exact", "spend_pending",
})
AUTHORITY_POLICY_V2_SPEND_PENDING_REASONS = frozenset({
    "transaction_owned", "identity_mismatch", "evidence_drift", "owner_lost",
    "not_spendable", "missing_result", "receipt_conflict", "spend_failed",
})
# Closed result-stage event appended by the spend writer (the accepted ``ax``
# audit).  It reuses the existing ``authority_policy_v2_result_stage`` action and
# binds the exact causal identity PLUS the spending result, its reserved session
# and the exact normalized ``report_digest`` of that retained result, so one
# receipt is discoverable, a replay cannot authenticate a changed report, and no
# new audit scope/table/column outside this PR's unreleased additive
# representation is introduced.
AUTHORITY_POLICY_V2_RESULT_STAGE_SPENT = "spent"

# THR-229 checkpoint C3d3c2: bounded decision-dispatch vocabulary for the
# ordinary decision consumer coupled to one spent result-keyed receipt.  The
# receipt's ``decision_state`` is the durable single-use token: only a winning
# ``ready -> claimed`` transition authorizes exactly ONE ordinary consumer
# entry; a duplicate/restarted ``claimed`` (or an ``applied``/``refused``)
# receipt never does.  None of these statuses is launch authority, and no
# writer below performs a queue call, child creation or external process claim.
AUTHORITY_POLICY_V2_DECISION_CLAIM_STATUSES = frozenset({
    "claimed", "decision_pending",
})
AUTHORITY_POLICY_V2_DECISION_CLAIM_PENDING_REASONS = frozenset({
    "transaction_owned", "identity_mismatch", "evidence_drift", "owner_lost",
    "not_claimable", "already_claimed", "already_applied", "already_refused",
    "claim_failed",
})
AUTHORITY_POLICY_V2_DECISION_ACK_STATUSES = frozenset({
    "applied", "already_applied_exact", "ack_pending",
})
AUTHORITY_POLICY_V2_DECISION_ACK_PENDING_REASONS = frozenset({
    "transaction_owned", "identity_mismatch", "evidence_drift", "not_claimed",
    "already_refused", "ack_failed",
})
AUTHORITY_POLICY_V2_DECISION_REFUSAL_STATUSES = frozenset({
    "refused", "already_refused", "refusal_pending",
})
AUTHORITY_POLICY_V2_DECISION_REFUSAL_PENDING_REASONS = frozenset({
    "transaction_owned", "identity_mismatch", "evidence_drift", "not_claimable",
    "already_applied", "refusal_failed",
})
# Closed result-stage events appended by the decision-dispatch writers.  They
# reuse the existing ``authority_policy_v2_result_stage`` action, bind the exact
# result-keyed receipt identity (including the bound ``report_digest``) and the
# reserved next session, and therefore introduce no new audit scope, table or
# column.  Each is a closed diagnostic, never raw model prose.
AUTHORITY_POLICY_V2_RESULT_STAGE_DECISION_CLAIMED = "decision_claimed"
AUTHORITY_POLICY_V2_RESULT_STAGE_DECISION_APPLIED = "decision_applied"
AUTHORITY_POLICY_V2_RESULT_STAGE_DECISION_DISPATCH_INTERRUPTED = (
    "decision_dispatch_interrupted"
)


class AuthorityPolicyV2Attempt(BaseModel):
    """Immutable admitted attempt journal row for one v2 callback result.

    The unique identity is ``(root_task_id, manager_agent,
    manager_session_id, result_id)`` and ``attempt_id`` is the deterministic
    ``APV2R-`` preimage over exactly those four identities plus the team.  The
    row is bound to the admitted result, the authenticated launch binding, the
    v2 contract and the admitted family.  ``origin_boot_id`` is the owning
    daemon-process UUID and ``owner_attempt_id`` is the randomly allocated UUID
    fixed by the winning insert; neither is a credential.
    """
    model_config = {"extra": "forbid", "strict": True, "frozen": True}

    attempt_id: StrictStr
    team: Literal[AUTHORITY_POLICY_V2_TEAM]
    root_task_id: StrictStr
    manager_agent: StrictStr
    manager_session_id: StrictStr
    result_id: StrictInt = Field(ge=1, le=9223372036854775807)
    binding_id: StrictStr
    contract_id: Literal[AUTHORITY_POLICY_V2_CONTRACT_ID]
    contract_version: Literal[AUTHORITY_POLICY_V2_CONTRACT_VERSION]
    contract_digest: StrictStr
    release_id: StrictStr
    activation_id: StrictStr
    activation_epoch: StrictInt = Field(ge=1, le=2147483647)
    selector_id: StrictStr
    stage: StrictStr
    finalization_state: StrictStr
    refusal_code: StrictStr | None = None
    origin_boot_id: StrictStr
    owner_attempt_id: StrictStr
    assessment_digest: StrictStr
    created_at: datetime = Field(default_factory=_now)

    @field_validator("contract_digest", "assessment_digest")
    @classmethod
    def _v2_attempt_digests_are_lower_hex(cls, value: str, info) -> str:
        return _validate_authority_policy_v2_digest(value, info.field_name)

    @field_validator("activation_id")
    @classmethod
    def _v2_attempt_activation_id(cls, value: str) -> str:
        if not value.startswith("APV2A-"):
            raise ValueError("activation_id must start with APV2A-")
        _validate_authority_policy_v2_digest(value[len("APV2A-"):], "activation_id")
        return value

    @field_validator("release_id")
    @classmethod
    def _v2_attempt_release_id(cls, value: str) -> str:
        if not value.startswith("APV2-"):
            raise ValueError("release_id must start with APV2-")
        _validate_authority_policy_v2_digest(value[len("APV2-"):], "release_id")
        return value

    @field_validator("binding_id")
    @classmethod
    def _v2_attempt_binding_id(cls, value: str) -> str:
        if not value.startswith("APV2B-"):
            raise ValueError("binding_id must start with APV2B-")
        _validate_authority_policy_v2_digest(value[len("APV2B-"):], "binding_id")
        return value

    @field_validator("selector_id")
    @classmethod
    def _v2_attempt_selector_ref(cls, value: str) -> str:
        return _validate_authority_policy_v2_selector_ref(value, "selector_id")

    @field_validator(
        "manager_agent", "manager_session_id", "origin_boot_id", "owner_attempt_id",
        "root_task_id",
    )
    @classmethod
    def _v2_attempt_identity_scalars(cls, value: str, info) -> str:
        return _validate_authority_policy_v2_text(value, info.field_name, 128)

    @field_validator("attempt_id")
    @classmethod
    def _v2_attempt_id_shape(cls, value: str) -> str:
        if not value.startswith("APV2R-"):
            raise ValueError("attempt_id must start with APV2R-")
        _validate_authority_policy_v2_digest(value[len("APV2R-"):], "attempt_id")
        return value

    @field_validator("stage")
    @classmethod
    def _v2_attempt_stage_is_closed(cls, value: str) -> str:
        if value not in AUTHORITY_POLICY_V2_ATTEMPT_STAGES:
            raise ValueError("attempt stage is not a current admitted stage")
        return value

    @field_validator("finalization_state")
    @classmethod
    def _v2_attempt_finalization_is_closed(cls, value: str) -> str:
        if value not in AUTHORITY_POLICY_V2_ATTEMPT_FINALIZATION_STATES:
            raise ValueError("attempt finalization_state is not a closed value")
        return value

    @model_validator(mode="after")
    def _v2_attempt_identity_is_deterministic(self) -> AuthorityPolicyV2Attempt:
        expected = authority_policy_v2_attempt_id(
            manager_agent=self.manager_agent,
            manager_session_id=self.manager_session_id,
            result_id=self.result_id,
            root_task_id=self.root_task_id,
            team=self.team,
        )
        if self.attempt_id != expected:
            raise ValueError("attempt_id does not match the frozen attempt preimage")
        if self.contract_digest != authority_policy_v2_contract_digest():
            raise ValueError("contract_digest does not match the v2 contract")
        if not self.release_id.startswith("APV2-"):
            raise ValueError("release_id is not a v2 release reference")
        if self.finalization_state in (
            AUTHORITY_POLICY_V2_ATTEMPT_FINALIZATION_REFUSED,
            AUTHORITY_POLICY_V2_ATTEMPT_FINALIZATION_OWNER_LOST,
        ):
            if self.refusal_code not in AUTHORITY_POLICY_V2_HOUSEKEEPING_REFUSAL_CODES:
                raise ValueError("a finalized v2 attempt requires a closed refusal code")
        elif self.refusal_code is not None:
            raise ValueError("an unfinalized v2 attempt carries no refusal code")
        return self


# THR-229 checkpoint C3b: the durable candidate/pin/claim-audit values.

def authority_policy_v2_causal_result_digest(result_id: int) -> str:
    """Frozen R2 causal-result row-identity digest (CRD).

    CRD is only the immutable row-identity digest ``sha256({"kind":
    "task_result","result_id":CR})``; it is NEVER a hash of the result body.
    The persisted result row/task/agent/session join and the normalized body
    are authenticated separately by the claim transaction.
    """
    return authority_policy_v2_sha256({"kind": "task_result", "result_id": result_id})


def authority_policy_v2_candidate_claim_preimage(
    *, activation_id: str, activation_selector_epoch: int, causal_result_digest: str,
    causal_result_id: int, contract_digest: str, executor_kind: str,
    manager_agent: str, manager_session_id: str, model_id: str, policy_digest: str,
    policy_version: int, provider_id: str, release_id: str, root_task_id: str,
    team: str,
) -> dict[str, object]:
    """The exact frozen R2 candidate claim-key preimage.

    The object literally carries ``causal_result_digest``/``causal_result_id``;
    the candidate identity is derived from exactly this object and no caller
    may substitute an interpretation of it.
    """
    return {
        "activation_id": activation_id,
        "activation_selector_epoch": activation_selector_epoch,
        "causal_result_digest": causal_result_digest,
        "causal_result_id": causal_result_id,
        "contract_digest": contract_digest,
        "executor_kind": executor_kind,
        "manager_agent": manager_agent,
        "manager_session_id": manager_session_id,
        "model_id": model_id,
        "policy_digest": policy_digest,
        "policy_version": policy_version,
        "provider_id": provider_id,
        "release_id": release_id,
        "root_task_id": root_task_id,
        "team": team,
    }


def authority_policy_v2_candidate_identity(claim_key: str) -> str:
    """``APV2C-`` + the frozen claim-key digest (identity is derived, never supplied)."""
    _validate_authority_policy_v2_digest(claim_key, "claim_key")
    return f"APV2C-{claim_key}"


class AuthorityPolicyV2Candidate(BaseModel):
    """Durable v2 candidate (K) claimed from one immutable admitted result.

    The identity is exactly ``APV2C-`` + the R2 claim-key digest over the
    frozen claim preimage, and the row is bound to the admitted attempt, the
    authenticated launch binding, the pinned release/activation/selector and
    the resolved provider/executor/model.  Uniqueness of ``claim_key`` (the
    causal tuple) prevents a second candidate for the same exact attempt.
    """
    model_config = {"extra": "forbid", "strict": True, "frozen": True}

    candidate_id: StrictStr
    claim_key: StrictStr
    team: Literal[AUTHORITY_POLICY_V2_TEAM]
    root_task_id: StrictStr
    manager_agent: StrictStr
    manager_session_id: StrictStr
    attempt_id: StrictStr
    result_id: StrictInt = Field(ge=1, le=9223372036854775807)
    binding_id: StrictStr
    contract_id: Literal[AUTHORITY_POLICY_V2_CONTRACT_ID]
    contract_version: Literal[AUTHORITY_POLICY_V2_CONTRACT_VERSION]
    contract_digest: StrictStr
    release_id: StrictStr
    policy_version: StrictInt = Field(ge=1, le=2147483647)
    policy_digest: StrictStr
    activation_id: StrictStr
    activation_epoch: StrictInt = Field(ge=1, le=2147483647)
    selector_id: StrictStr
    provider_id: StrictStr
    executor_kind: StrictStr
    model_id: StrictStr
    causal_result_id: StrictInt = Field(ge=1, le=9223372036854775807)
    causal_result_digest: StrictStr
    origin_boot_id: StrictStr
    owner_attempt_id: StrictStr
    # C3b correction: the ACTUAL claim-time schema evidence and bounded
    # read-only permission-surface evidence are frozen onto the candidate so the
    # second boundary can recheck the ORIGINAL evidence (no recapture/rebaseline)
    # and later evaluation/consume/finalize code reads the same authenticated
    # values.  These are evidence only, never a clause input, and are
    # deliberately NOT part of the frozen R2 claim preimage.
    schema_raw_digest: StrictStr = ""
    schema_inventory_digest: StrictStr = ""
    schema_object_count: StrictInt = 0
    permission_surface_digest: StrictStr = ""
    # C3c: the forward-only candidate lifecycle.  Claim creates ``created``;
    # the evaluation transaction advances it to ``evaluated`` and consumption to
    # ``consumed``.  Only this field (and the canonical payload that mirrors it)
    # is ever mutable; identity and frozen evidence are not.
    lifecycle_stage: StrictStr = AUTHORITY_POLICY_V2_CANDIDATE_LIFECYCLE_CREATED
    created_at: datetime = Field(default_factory=_now)

    @field_validator(
        "claim_key", "contract_digest", "policy_digest", "causal_result_digest",
    )
    @classmethod
    def _v2_candidate_digests_are_lower_hex(cls, value: str, info) -> str:
        return _validate_authority_policy_v2_digest(value, info.field_name)

    @field_validator(
        "schema_raw_digest", "schema_inventory_digest", "permission_surface_digest",
    )
    @classmethod
    def _v2_candidate_evidence_digests(cls, value: str, info) -> str:
        if value == "":
            return value
        return _validate_authority_policy_v2_digest(value, info.field_name)

    @field_validator("candidate_id")
    @classmethod
    def _v2_candidate_id_shape(cls, value: str) -> str:
        if not value.startswith("APV2C-"):
            raise ValueError("candidate_id must start with APV2C-")
        _validate_authority_policy_v2_digest(value[len("APV2C-"):], "candidate_id")
        return value

    @field_validator("attempt_id")
    @classmethod
    def _v2_candidate_attempt_id_shape(cls, value: str) -> str:
        if not value.startswith("APV2R-"):
            raise ValueError("attempt_id must start with APV2R-")
        _validate_authority_policy_v2_digest(value[len("APV2R-"):], "attempt_id")
        return value

    @field_validator("binding_id")
    @classmethod
    def _v2_candidate_binding_id_shape(cls, value: str) -> str:
        if not value.startswith("APV2B-"):
            raise ValueError("binding_id must start with APV2B-")
        _validate_authority_policy_v2_digest(value[len("APV2B-"):], "binding_id")
        return value

    @field_validator("activation_id")
    @classmethod
    def _v2_candidate_activation_id_shape(cls, value: str) -> str:
        if not value.startswith("APV2A-"):
            raise ValueError("activation_id must start with APV2A-")
        _validate_authority_policy_v2_digest(value[len("APV2A-"):], "activation_id")
        return value

    @field_validator("release_id")
    @classmethod
    def _v2_candidate_release_id_shape(cls, value: str) -> str:
        if not value.startswith("APV2-"):
            raise ValueError("release_id must start with APV2-")
        _validate_authority_policy_v2_digest(value[len("APV2-"):], "release_id")
        return value

    @field_validator("selector_id")
    @classmethod
    def _v2_candidate_selector_ref(cls, value: str) -> str:
        return _validate_authority_policy_v2_selector_ref(value, "selector_id")

    @field_validator(
        "manager_agent", "manager_session_id", "model_id", "origin_boot_id",
        "owner_attempt_id", "provider_id", "root_task_id",
    )
    @classmethod
    def _v2_candidate_identity_scalars(cls, value: str, info) -> str:
        return _validate_authority_policy_v2_text(value, info.field_name, 128)

    @field_validator("lifecycle_stage")
    @classmethod
    def _v2_candidate_lifecycle_is_closed(cls, value: str) -> str:
        if value not in AUTHORITY_POLICY_V2_CANDIDATE_LIFECYCLE_STAGES:
            raise ValueError("candidate lifecycle_stage is not a closed value")
        return value

    def preimage(self) -> dict[str, object]:
        return authority_policy_v2_candidate_claim_preimage(
            activation_id=self.activation_id,
            activation_selector_epoch=self.activation_epoch,
            causal_result_digest=self.causal_result_digest,
            causal_result_id=self.causal_result_id,
            contract_digest=self.contract_digest,
            executor_kind=self.executor_kind,
            manager_agent=self.manager_agent,
            manager_session_id=self.manager_session_id,
            model_id=self.model_id,
            policy_digest=self.policy_digest,
            policy_version=self.policy_version,
            provider_id=self.provider_id,
            release_id=self.release_id,
            root_task_id=self.root_task_id,
            team=self.team,
        )

    @model_validator(mode="after")
    def _v2_candidate_identity_is_frozen(self) -> AuthorityPolicyV2Candidate:
        expected_key = authority_policy_v2_sha256(self.preimage())
        if self.claim_key != expected_key:
            raise ValueError("claim_key does not match the frozen claim preimage")
        if self.candidate_id != f"APV2C-{expected_key}":
            raise ValueError("candidate_id does not match the frozen claim preimage")
        if self.result_id != self.causal_result_id:
            raise ValueError("causal_result_id must be the admitted result id")
        if self.causal_result_digest != authority_policy_v2_causal_result_digest(
            self.result_id
        ):
            raise ValueError("causal_result_digest is not the frozen row-identity digest")
        if self.contract_digest != authority_policy_v2_contract_digest():
            raise ValueError("contract_digest does not match the v2 contract")
        if self.release_id != f"APV2-{self.policy_digest}":
            raise ValueError("release_id must contain policy_digest")
        if self.attempt_id != authority_policy_v2_attempt_id(
            manager_agent=self.manager_agent, manager_session_id=self.manager_session_id,
            result_id=self.result_id, root_task_id=self.root_task_id, team=self.team,
        ):
            raise ValueError("attempt_id does not match the frozen attempt preimage")
        return self


class AuthorityPolicyV2Pin(BaseModel):
    """Durable v2 policy pin (P); its identity equals the candidate identity."""
    model_config = {"extra": "forbid", "strict": True, "frozen": True}

    candidate_id: StrictStr
    claim_key: StrictStr
    team: Literal[AUTHORITY_POLICY_V2_TEAM]
    root_task_id: StrictStr
    manager_agent: StrictStr
    manager_session_id: StrictStr
    attempt_id: StrictStr
    result_id: StrictInt = Field(ge=1, le=9223372036854775807)
    binding_id: StrictStr
    release_id: StrictStr
    activation_id: StrictStr
    activation_epoch: StrictInt = Field(ge=1, le=2147483647)
    selector_id: StrictStr
    policy_version: StrictInt = Field(ge=1, le=2147483647)
    policy_digest: StrictStr
    contract_digest: StrictStr
    provider_id: StrictStr
    executor_kind: StrictStr
    model_id: StrictStr
    # C3b correction: identity-equal to the candidate's frozen claim-time
    # schema/permission evidence (evidence only; never a claim preimage input).
    schema_raw_digest: StrictStr = ""
    schema_inventory_digest: StrictStr = ""
    schema_object_count: StrictInt = 0
    permission_surface_digest: StrictStr = ""
    created_at: datetime = Field(default_factory=_now)

    @field_validator("candidate_id")
    @classmethod
    def _v2_pin_candidate_id_shape(cls, value: str) -> str:
        if not value.startswith("APV2C-"):
            raise ValueError("candidate_id must start with APV2C-")
        _validate_authority_policy_v2_digest(value[len("APV2C-"):], "candidate_id")
        return value

    @field_validator(
        "schema_raw_digest", "schema_inventory_digest", "permission_surface_digest",
    )
    @classmethod
    def _v2_pin_evidence_digests(cls, value: str, info) -> str:
        if value == "":
            return value
        return _validate_authority_policy_v2_digest(value, info.field_name)

    @field_validator("claim_key", "contract_digest", "policy_digest")
    @classmethod
    def _v2_pin_digests_are_lower_hex(cls, value: str, info) -> str:
        return _validate_authority_policy_v2_digest(value, info.field_name)

    @field_validator("release_id")
    @classmethod
    def _v2_pin_release_id_shape(cls, value: str) -> str:
        if not value.startswith("APV2-"):
            raise ValueError("release_id must start with APV2-")
        _validate_authority_policy_v2_digest(value[len("APV2-"):], "release_id")
        return value

    @field_validator("activation_id")
    @classmethod
    def _v2_pin_activation_id_shape(cls, value: str) -> str:
        if not value.startswith("APV2A-"):
            raise ValueError("activation_id must start with APV2A-")
        _validate_authority_policy_v2_digest(value[len("APV2A-"):], "activation_id")
        return value

    @field_validator("selector_id")
    @classmethod
    def _v2_pin_selector_ref(cls, value: str) -> str:
        return _validate_authority_policy_v2_selector_ref(value, "selector_id")

    @field_validator(
        "manager_agent", "manager_session_id", "model_id", "provider_id",
        "root_task_id",
    )
    @classmethod
    def _v2_pin_identity_scalars(cls, value: str, info) -> str:
        return _validate_authority_policy_v2_text(value, info.field_name, 128)

    @property
    def pin_id(self) -> str:
        return self.candidate_id

    @model_validator(mode="after")
    def _v2_pin_identity_matches_candidate(self) -> AuthorityPolicyV2Pin:
        if self.pin_id != f"APV2C-{self.claim_key}":
            raise ValueError("pin identity must equal the candidate claim identity")
        if self.release_id != f"APV2-{self.policy_digest}":
            raise ValueError("release_id must contain policy_digest")
        return self


class AuthorityPolicyV2Evaluation(BaseModel):
    """Immutable persisted v2 evaluation (V); identity equals the candidate ID.

    The evaluation stores the derived clause-free outcome ONCE and its bounded
    authenticated assessment/diagnostic evidence.  It is written by the
    evaluation transaction and is never recomputed by the audit, consumption or
    consumed-audit stages: those stages authenticate the canonical stored
    evidence and its joins instead.
    """
    model_config = {"extra": "forbid", "strict": True, "frozen": True}

    evaluation_id: StrictStr
    candidate_id: StrictStr
    claim_key: StrictStr
    team: Literal[AUTHORITY_POLICY_V2_TEAM]
    root_task_id: StrictStr
    manager_agent: StrictStr
    manager_session_id: StrictStr
    attempt_id: StrictStr
    result_id: StrictInt = Field(ge=1, le=9223372036854775807)
    binding_id: StrictStr
    release_id: StrictStr
    activation_id: StrictStr
    activation_epoch: StrictInt = Field(ge=1, le=2147483647)
    selector_id: StrictStr
    policy_version: StrictInt = Field(ge=1, le=2147483647)
    policy_digest: StrictStr
    contract_digest: StrictStr
    provider_id: StrictStr
    executor_kind: StrictStr
    model_id: StrictStr
    outcome: StrictStr
    assessment_digest: StrictStr
    diagnostic_code: StrictStr | None = None
    what_to_escalate_json: StrictStr | None = None
    what_not_to_escalate_json: StrictStr | None = None
    created_at: datetime = Field(default_factory=_now)

    @field_validator("claim_key", "policy_digest", "contract_digest", "assessment_digest")
    @classmethod
    def _v2_evaluation_digests_are_lower_hex(cls, value: str, info) -> str:
        return _validate_authority_policy_v2_digest(value, info.field_name)

    @field_validator("evaluation_id", "candidate_id")
    @classmethod
    def _v2_evaluation_candidate_id_shape(cls, value: str) -> str:
        if not value.startswith("APV2C-"):
            raise ValueError("evaluation id must start with APV2C-")
        _validate_authority_policy_v2_digest(value[len("APV2C-"):], "evaluation_id")
        return value

    @field_validator("attempt_id")
    @classmethod
    def _v2_evaluation_attempt_id_shape(cls, value: str) -> str:
        if not value.startswith("APV2R-"):
            raise ValueError("attempt_id must start with APV2R-")
        _validate_authority_policy_v2_digest(value[len("APV2R-"):], "attempt_id")
        return value

    @field_validator("binding_id")
    @classmethod
    def _v2_evaluation_binding_id_shape(cls, value: str) -> str:
        if not value.startswith("APV2B-"):
            raise ValueError("binding_id must start with APV2B-")
        _validate_authority_policy_v2_digest(value[len("APV2B-"):], "binding_id")
        return value

    @field_validator("release_id")
    @classmethod
    def _v2_evaluation_release_id_shape(cls, value: str) -> str:
        if not value.startswith("APV2-"):
            raise ValueError("release_id must start with APV2-")
        _validate_authority_policy_v2_digest(value[len("APV2-"):], "release_id")
        return value

    @field_validator("activation_id")
    @classmethod
    def _v2_evaluation_activation_id_shape(cls, value: str) -> str:
        if not value.startswith("APV2A-"):
            raise ValueError("activation_id must start with APV2A-")
        _validate_authority_policy_v2_digest(value[len("APV2A-"):], "activation_id")
        return value

    @field_validator("selector_id")
    @classmethod
    def _v2_evaluation_selector_ref(cls, value: str) -> str:
        return _validate_authority_policy_v2_selector_ref(value, "selector_id")

    @field_validator(
        "manager_agent", "manager_session_id", "model_id", "provider_id",
        "root_task_id",
    )
    @classmethod
    def _v2_evaluation_identity_scalars(cls, value: str, info) -> str:
        return _validate_authority_policy_v2_text(value, info.field_name, 128)

    @field_validator("outcome")
    @classmethod
    def _v2_evaluation_outcome_is_closed(cls, value: str) -> str:
        if value not in AUTHORITY_POLICY_V2_ASSESSMENT_OUTCOMES:
            raise ValueError("evaluation outcome is not a closed value")
        return value

    @field_validator("diagnostic_code")
    @classmethod
    def _v2_evaluation_diagnostic_is_closed(cls, value: str | None) -> str | None:
        if value is not None and value not in AUTHORITY_POLICY_V2_EVALUATION_DIAGNOSTIC_CODES:
            raise ValueError("evaluation diagnostic code is not a closed value")
        return value

    @field_validator("what_to_escalate_json", "what_not_to_escalate_json")
    @classmethod
    def _v2_evaluation_assessment_bytes_are_canonical(
        cls, value: str | None, info,
    ) -> str | None:
        if value is None:
            return None
        try:
            parsed = AuthorityPolicyV2Assessment.model_validate_json(value)
        except Exception as exc:
            raise ValueError("evaluation assessment evidence is not a valid assessment") from exc
        if authority_policy_v2_canonical_json_bytes(
            parsed.model_dump(mode="json")
        ).decode("utf-8") != value:
            raise ValueError("evaluation assessment evidence is not canonical")
        return value

    @model_validator(mode="after")
    def _v2_evaluation_identity_matches(self) -> AuthorityPolicyV2Evaluation:
        if self.evaluation_id != self.candidate_id:
            raise ValueError("evaluation identity must equal the candidate identity")
        if self.candidate_id != f"APV2C-{self.claim_key}":
            raise ValueError("evaluation candidate_id must match the claim_key")
        if self.release_id != f"APV2-{self.policy_digest}":
            raise ValueError("release_id must contain policy_digest")
        if (self.what_to_escalate_json is None) != (self.what_not_to_escalate_json is None):
            raise ValueError("both assessment evidence values must be present or absent")
        if self.what_to_escalate_json is None and self.diagnostic_code is None:
            raise ValueError("an evaluation without assessment evidence requires a diagnostic")
        if self.what_to_escalate_json is not None and self.diagnostic_code is not None:
            raise ValueError("a valid evaluation carries no invalid-assessment diagnostic")
        return self


class AuthorityPolicyV2CandidateAudit(BaseModel):
    """Immutable, closed candidate-stage audit event (a1 claim stage here).

    The event carries only the bounded claim identity; there is no free-form
    rationale, model transcript or credential, and the attached payload is
    frozen canonical JSON.  A real candidate FK is required.
    """
    model_config = {"extra": "forbid", "strict": True, "frozen": True}

    candidate_id: StrictStr
    team: Literal[AUTHORITY_POLICY_V2_TEAM]
    root_task_id: StrictStr
    manager_agent: StrictStr
    manager_session_id: StrictStr
    event: StrictStr
    claim_key: StrictStr
    attempt_id: StrictStr
    result_id: StrictInt = Field(ge=1, le=9223372036854775807)
    owner_attempt_id: StrictStr
    origin_boot_id: StrictStr
    created_at: datetime = Field(default_factory=_now)

    @field_validator("event")
    @classmethod
    def _v2_candidate_audit_event_is_closed(cls, value: str) -> str:
        if value not in AUTHORITY_POLICY_V2_CANDIDATE_AUDIT_EVENTS:
            raise ValueError("candidate audit event is not a current closed value")
        return value

    @field_validator("candidate_id")
    @classmethod
    def _v2_candidate_audit_candidate_id(cls, value: str) -> str:
        if not value.startswith("APV2C-"):
            raise ValueError("candidate_id must start with APV2C-")
        _validate_authority_policy_v2_digest(value[len("APV2C-"):], "candidate_id")
        return value

    @field_validator("claim_key")
    @classmethod
    def _v2_candidate_audit_claim_key(cls, value: str) -> str:
        return _validate_authority_policy_v2_digest(value, "claim_key")

    @field_validator("attempt_id")
    @classmethod
    def _v2_candidate_audit_attempt_id(cls, value: str) -> str:
        if not value.startswith("APV2R-"):
            raise ValueError("attempt_id must start with APV2R-")
        _validate_authority_policy_v2_digest(value[len("APV2R-"):], "attempt_id")
        return value

    @field_validator(
        "manager_agent", "manager_session_id", "origin_boot_id",
        "owner_attempt_id", "root_task_id",
    )
    @classmethod
    def _v2_candidate_audit_identity_scalars(cls, value: str, info) -> str:
        return _validate_authority_policy_v2_text(value, info.field_name, 128)

    @model_validator(mode="after")
    def _v2_candidate_audit_identity_matches(self) -> AuthorityPolicyV2CandidateAudit:
        if self.candidate_id != f"APV2C-{self.claim_key}":
            raise ValueError("candidate audit candidate_id must match claim_key")
        return self


class AuthorityPolicyV2StageOutcome(BaseModel):
    """Bounded outcome of one callable v2 claim/claim-audit stage.

    ``refused`` names exactly one closed refusal code and grants no authority;
    this checkpoint performs NO task/attempt/refusal mutation.  The later
    refusal-housekeeping consumer (documented in CLAUDE.md) owns any durable
    refusal/settlement.
    """
    model_config = {"extra": "forbid", "strict": True, "frozen": True}

    status: StrictStr
    refusal_code: StrictStr | None = None
    attempt_id: StrictStr | None = None
    candidate_id: StrictStr | None = None
    claim_key: StrictStr | None = None
    stage: StrictStr | None = None

    @field_validator("status")
    @classmethod
    def _v2_stage_outcome_status_is_closed(cls, value: str) -> str:
        if value not in AUTHORITY_POLICY_V2_STAGE_OUTCOME_STATUSES:
            raise ValueError("stage outcome status is not a closed value")
        return value

    @field_validator("refusal_code")
    @classmethod
    def _v2_stage_outcome_refusal_is_closed(cls, value: str | None) -> str | None:
        if value is not None and value not in AUTHORITY_POLICY_V2_STAGE_REFUSAL_CODES:
            raise ValueError("stage refusal code is not a closed value")
        return value

    @model_validator(mode="after")
    def _v2_stage_outcome_shape(self) -> AuthorityPolicyV2StageOutcome:
        if self.status == "refused":
            if self.refusal_code is None:
                raise ValueError("a refused outcome requires a refusal code")
        elif self.refusal_code is not None:
            raise ValueError("a non-refused outcome carries no refusal code")
        return self


class AuthorityPolicyV2HousekeepingTarget(BaseModel):
    """Bounded, read-only discovery record for one unfinalized attempt (J).

    Discovery authenticates the actual persisted attempt row and never assumes a
    candidate exists: a failed claim with no K is still discoverable.  The record
    is evidence only and grants no housekeeping authority by itself.
    """
    model_config = {"extra": "forbid", "strict": True, "frozen": True}

    attempt_id: StrictStr
    team: StrictStr
    root_task_id: StrictStr
    manager_agent: StrictStr
    manager_session_id: StrictStr
    result_id: StrictInt = Field(ge=1, le=9223372036854775807)
    origin_boot_id: StrictStr
    owner_attempt_id: StrictStr
    stage: StrictStr
    finalization_state: StrictStr
    candidate_id: StrictStr | None = None
    obligation_code: StrictStr | None = None

    @field_validator("stage")
    @classmethod
    def _v2_target_stage_is_closed(cls, value: str) -> str:
        if value not in AUTHORITY_POLICY_V2_ATTEMPT_STAGES:
            raise ValueError("target stage is not a current closed stage")
        return value

    @field_validator("finalization_state")
    @classmethod
    def _v2_target_finalization_is_closed(cls, value: str) -> str:
        if value not in AUTHORITY_POLICY_V2_ATTEMPT_FINALIZATION_STATES:
            raise ValueError("target finalization_state is not a closed value")
        return value

    @field_validator("obligation_code")
    @classmethod
    def _v2_target_obligation_is_closed(cls, value: str | None) -> str | None:
        if value is not None and value not in AUTHORITY_POLICY_V2_HOUSEKEEPING_REFUSAL_CODES:
            raise ValueError("target obligation code is not a closed value")
        return value


class AuthorityPolicyV2HousekeepingOutcome(BaseModel):
    """Bounded outcome of ONE callable refusal-housekeeping transaction.

    ``refused``/``owner_lost`` name a just-committed terminal refusal;
    ``already_refused`` is a read-only exact replay of an existing exact
    refusal/completion audit pair; ``housekeeping_pending`` means safe
    attribution could not be established (or the transaction failed), so the
    task/Q/J/prior rows are honestly preserved and only housekeeping may retry.
    This value is never authority and never a continuation grant.
    """
    model_config = {"extra": "forbid", "strict": True, "frozen": True}

    status: StrictStr
    attempt_id: StrictStr
    refusal_code: StrictStr | None = None
    candidate_id: StrictStr | None = None
    stage: StrictStr | None = None
    finalization_state: StrictStr | None = None
    receipt_settled: bool = False

    @field_validator("status")
    @classmethod
    def _v2_housekeeping_status_is_closed(cls, value: str) -> str:
        if value not in AUTHORITY_POLICY_V2_HOUSEKEEPING_OUTCOME_STATUSES:
            raise ValueError("housekeeping outcome status is not a closed value")
        return value

    @field_validator("refusal_code")
    @classmethod
    def _v2_housekeeping_code_is_closed(cls, value: str | None) -> str | None:
        if value is not None and value not in AUTHORITY_POLICY_V2_HOUSEKEEPING_OUTCOME_CODES:
            raise ValueError("housekeeping code is not a closed value")
        return value

    @field_validator("finalization_state")
    @classmethod
    def _v2_housekeeping_finalization_is_closed(cls, value: str | None) -> str | None:
        if value is not None and value not in AUTHORITY_POLICY_V2_ATTEMPT_FINALIZATION_STATES:
            raise ValueError("housekeeping finalization_state is not a closed value")
        return value

    @model_validator(mode="after")
    def _v2_housekeeping_shape(self) -> AuthorityPolicyV2HousekeepingOutcome:
        if self.status in ("refused", "owner_lost", "already_refused"):
            if self.refusal_code not in AUTHORITY_POLICY_V2_HOUSEKEEPING_REFUSAL_CODES:
                raise ValueError("a terminal housekeeping outcome requires a refusal code")
        elif self.refusal_code is None:
            raise ValueError("a pending housekeeping outcome requires a bounded reason")
        return self


# THR-229 checkpoint C3d2: the three remaining approved additive values.
#
# The envelope (E) is inserted ACTIVE by the final continuation transaction and
# repeats/authenticates the complete pinned candidate tuple (the same immutable
# identity the candidate/pin/evaluation committed).  Its identity is exactly
# ``APV2E-`` + H({"candidate_id": C, "kind": "continue_envelope"}) and it is
# unique by candidate.  Only the forward-only active -> consumed lifecycle may
# advance (the later spend unit owns that transition); identity and the
# authenticated tuple are immutable.


class AuthorityPolicyV2ContinueEnvelope(BaseModel):
    """Durable active continuation envelope (E) for one consumed candidate."""
    model_config = {"extra": "forbid", "strict": True, "frozen": True}

    envelope_id: StrictStr
    candidate_id: StrictStr
    claim_key: StrictStr
    team: Literal[AUTHORITY_POLICY_V2_TEAM]
    root_task_id: StrictStr
    manager_agent: StrictStr
    manager_session_id: StrictStr
    attempt_id: StrictStr
    result_id: StrictInt = Field(ge=1, le=9223372036854775807)
    binding_id: StrictStr
    contract_id: Literal[AUTHORITY_POLICY_V2_CONTRACT_ID]
    contract_version: Literal[AUTHORITY_POLICY_V2_CONTRACT_VERSION]
    contract_digest: StrictStr
    release_id: StrictStr
    policy_version: StrictInt = Field(ge=1, le=2147483647)
    policy_digest: StrictStr
    activation_id: StrictStr
    activation_epoch: StrictInt = Field(ge=1, le=2147483647)
    selector_id: StrictStr
    provider_id: StrictStr
    executor_kind: StrictStr
    model_id: StrictStr
    causal_result_id: StrictInt = Field(ge=1, le=9223372036854775807)
    causal_result_digest: StrictStr
    # The persisted evaluation (V) outcome this envelope authenticates.  Only
    # the accepted continuation outcome may mint an envelope.
    evaluation_outcome: Literal["continue_applies"]
    origin_boot_id: StrictStr
    owner_attempt_id: StrictStr
    lifecycle_state: StrictStr = "active"
    spending_result_id: StrictInt | None = Field(
        default=None, ge=1, le=9223372036854775807,
    )
    # The result-keyed decision receipt state.  NULL while the envelope is
    # active; once consumed it is exactly one closed forward-only state.  This
    # unit only ever writes the durable ``ready`` receipt.
    decision_state: StrictStr | None = None
    created_at: datetime = Field(default_factory=_now)

    @field_validator(
        "claim_key", "contract_digest", "policy_digest", "causal_result_digest",
    )
    @classmethod
    def _v2_envelope_digests_are_lower_hex(cls, value: str, info) -> str:
        return _validate_authority_policy_v2_digest(value, info.field_name)

    @field_validator("envelope_id")
    @classmethod
    def _v2_envelope_id_shape(cls, value: str) -> str:
        if not value.startswith("APV2E-"):
            raise ValueError("envelope_id must start with APV2E-")
        _validate_authority_policy_v2_digest(value[len("APV2E-"):], "envelope_id")
        return value

    @field_validator("candidate_id")
    @classmethod
    def _v2_envelope_candidate_id_shape(cls, value: str) -> str:
        if not value.startswith("APV2C-"):
            raise ValueError("candidate_id must start with APV2C-")
        _validate_authority_policy_v2_digest(value[len("APV2C-"):], "candidate_id")
        return value

    @field_validator("attempt_id")
    @classmethod
    def _v2_envelope_attempt_id_shape(cls, value: str) -> str:
        if not value.startswith("APV2R-"):
            raise ValueError("attempt_id must start with APV2R-")
        _validate_authority_policy_v2_digest(value[len("APV2R-"):], "attempt_id")
        return value

    @field_validator("binding_id")
    @classmethod
    def _v2_envelope_binding_id_shape(cls, value: str) -> str:
        if not value.startswith("APV2B-"):
            raise ValueError("binding_id must start with APV2B-")
        _validate_authority_policy_v2_digest(value[len("APV2B-"):], "binding_id")
        return value

    @field_validator("release_id")
    @classmethod
    def _v2_envelope_release_id_shape(cls, value: str) -> str:
        if not value.startswith("APV2-"):
            raise ValueError("release_id must start with APV2-")
        _validate_authority_policy_v2_digest(value[len("APV2-"):], "release_id")
        return value

    @field_validator("activation_id")
    @classmethod
    def _v2_envelope_activation_id_shape(cls, value: str) -> str:
        if not value.startswith("APV2A-"):
            raise ValueError("activation_id must start with APV2A-")
        _validate_authority_policy_v2_digest(value[len("APV2A-"):], "activation_id")
        return value

    @field_validator("selector_id")
    @classmethod
    def _v2_envelope_selector_ref(cls, value: str) -> str:
        return _validate_authority_policy_v2_selector_ref(value, "selector_id")

    @field_validator(
        "manager_agent", "manager_session_id", "model_id", "origin_boot_id",
        "owner_attempt_id", "provider_id", "root_task_id",
    )
    @classmethod
    def _v2_envelope_identity_scalars(cls, value: str, info) -> str:
        return _validate_authority_policy_v2_text(value, info.field_name, 128)

    @field_validator("lifecycle_state")
    @classmethod
    def _v2_envelope_state_is_closed(cls, value: str) -> str:
        if value not in AUTHORITY_POLICY_V2_ENVELOPE_STATES:
            raise ValueError("envelope lifecycle_state is not a closed value")
        return value

    @field_validator("decision_state")
    @classmethod
    def _v2_envelope_decision_state_is_closed(cls, value: str | None) -> str | None:
        if value is not None and value not in AUTHORITY_POLICY_V2_DECISION_STATES:
            raise ValueError("envelope decision_state is not a closed value")
        return value

    @model_validator(mode="after")
    def _v2_envelope_identity_is_frozen(self) -> AuthorityPolicyV2ContinueEnvelope:
        if self.candidate_id != f"APV2C-{self.claim_key}":
            raise ValueError("envelope candidate_id must match the claim_key")
        if self.envelope_id != authority_policy_v2_envelope_id(self.candidate_id):
            raise ValueError("envelope_id does not match the frozen envelope preimage")
        if self.attempt_id != authority_policy_v2_attempt_id(
            manager_agent=self.manager_agent, manager_session_id=self.manager_session_id,
            result_id=self.result_id, root_task_id=self.root_task_id, team=self.team,
        ):
            raise ValueError("envelope attempt_id does not match the attempt preimage")
        if self.result_id != self.causal_result_id:
            raise ValueError("envelope causal_result_id must be the admitted result id")
        if self.causal_result_digest != authority_policy_v2_causal_result_digest(
            self.result_id
        ):
            raise ValueError("envelope causal_result_digest is not the row-identity digest")
        if self.release_id != f"APV2-{self.policy_digest}":
            raise ValueError("envelope release_id must contain policy_digest")
        if self.contract_digest != authority_policy_v2_contract_digest():
            raise ValueError("envelope contract_digest does not match the v2 contract")
        if (self.lifecycle_state == "consumed") != (self.spending_result_id is not None):
            raise ValueError("a consumed envelope requires exactly one spending result")
        if (self.lifecycle_state == "consumed") != (self.decision_state is not None):
            raise ValueError(
                "a consumed envelope requires exactly one decision receipt state"
            )
        return self


class AuthorityPolicyV2RecoveryNotification(BaseModel):
    """Durable continuation notification (N); its identity is generation G."""
    model_config = {"extra": "forbid", "strict": True, "frozen": True}

    notification_id: StrictStr
    envelope_id: StrictStr
    candidate_id: StrictStr
    result_id: StrictInt = Field(ge=1, le=9223372036854775807)
    root_task_id: StrictStr
    manager_agent: StrictStr
    manager_session_id: StrictStr
    selector_id: StrictStr
    state: StrictStr = "needed"
    publication_attempt: StrictInt = Field(default=0, ge=0, le=2147483647)
    publisher_boot_id: StrictStr | None = None
    lease_deadline: StrictStr | None = None
    next_session_id: StrictStr | None = None
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)

    @field_validator("notification_id")
    @classmethod
    def _v2_notification_id_shape(cls, value: str) -> str:
        if not value.startswith("APV2N-"):
            raise ValueError("notification_id must start with APV2N-")
        _validate_authority_policy_v2_digest(value[len("APV2N-"):], "notification_id")
        return value

    @field_validator("envelope_id")
    @classmethod
    def _v2_notification_envelope_id_shape(cls, value: str) -> str:
        if not value.startswith("APV2E-"):
            raise ValueError("envelope_id must start with APV2E-")
        _validate_authority_policy_v2_digest(value[len("APV2E-"):], "envelope_id")
        return value

    @field_validator("candidate_id")
    @classmethod
    def _v2_notification_candidate_id_shape(cls, value: str) -> str:
        if not value.startswith("APV2C-"):
            raise ValueError("candidate_id must start with APV2C-")
        _validate_authority_policy_v2_digest(value[len("APV2C-"):], "candidate_id")
        return value

    @field_validator("selector_id")
    @classmethod
    def _v2_notification_selector_ref(cls, value: str) -> str:
        return _validate_authority_policy_v2_selector_ref(value, "selector_id")

    @field_validator("manager_agent", "manager_session_id", "root_task_id")
    @classmethod
    def _v2_notification_identity_scalars(cls, value: str, info) -> str:
        return _validate_authority_policy_v2_text(value, info.field_name, 128)

    @field_validator("state")
    @classmethod
    def _v2_notification_state_is_closed(cls, value: str) -> str:
        if value not in AUTHORITY_POLICY_V2_NOTIFICATION_STATES:
            raise ValueError("notification state is not a closed value")
        return value

    @field_validator("publisher_boot_id", "lease_deadline", "next_session_id")
    @classmethod
    def _v2_notification_optional_scalars(cls, value: str | None, info) -> str | None:
        if value is None:
            return None
        return _validate_authority_policy_v2_text(value, info.field_name, 128)

    @model_validator(mode="after")
    def _v2_notification_identity_is_frozen(self) -> AuthorityPolicyV2RecoveryNotification:
        if self.notification_id != authority_policy_v2_notification_id(self.envelope_id):
            raise ValueError("notification_id does not match the frozen notification preimage")
        if (self.publisher_boot_id is None) != (self.lease_deadline is None):
            raise ValueError("publisher boot and lease are present or absent together")
        if self.state == "needed":
            if (
                self.publication_attempt != 0
                or self.publisher_boot_id is not None
                or self.next_session_id is not None
            ):
                raise ValueError("a needed notification carries no publication state")
        if self.state in ("admitted", "settled") and self.next_session_id is None:
            raise ValueError("an admitted/settled notification requires a reserved session")
        return self


class AuthorityPolicyV2RootDispatch(BaseModel):
    """Durable root dispatch pointer (D) keyed by root; one live generation G."""
    model_config = {"extra": "forbid", "strict": True, "frozen": True}

    root_task_id: StrictStr
    generation_id: StrictStr
    envelope_id: StrictStr
    state: StrictStr = "pending"
    expected_manager_agent: StrictStr
    expected_manager_session_id: StrictStr
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)

    @field_validator("generation_id")
    @classmethod
    def _v2_dispatch_generation_shape(cls, value: str) -> str:
        if not value.startswith("APV2N-"):
            raise ValueError("generation_id must start with APV2N-")
        _validate_authority_policy_v2_digest(value[len("APV2N-"):], "generation_id")
        return value

    @field_validator("envelope_id")
    @classmethod
    def _v2_dispatch_envelope_shape(cls, value: str) -> str:
        if not value.startswith("APV2E-"):
            raise ValueError("envelope_id must start with APV2E-")
        _validate_authority_policy_v2_digest(value[len("APV2E-"):], "envelope_id")
        return value

    @field_validator("state")
    @classmethod
    def _v2_dispatch_state_is_closed(cls, value: str) -> str:
        if value not in AUTHORITY_POLICY_V2_DISPATCH_STATES:
            raise ValueError("dispatch state is not a closed value")
        return value

    @field_validator(
        "expected_manager_agent", "expected_manager_session_id", "root_task_id",
    )
    @classmethod
    def _v2_dispatch_identity_scalars(cls, value: str, info) -> str:
        return _validate_authority_policy_v2_text(value, info.field_name, 128)


class AuthorityPolicyV2FinalizationOutcome(BaseModel):
    """Bounded outcome of the ONE final continuation transaction."""
    model_config = {"extra": "forbid", "strict": True, "frozen": True}

    status: StrictStr
    attempt_id: StrictStr
    reason: StrictStr | None = None
    candidate_id: StrictStr | None = None
    envelope_id: StrictStr | None = None
    notification_id: StrictStr | None = None
    generation_id: StrictStr | None = None
    finalization_state: StrictStr | None = None

    @field_validator("status")
    @classmethod
    def _v2_finalization_status_is_closed(cls, value: str) -> str:
        if value not in AUTHORITY_POLICY_V2_FINALIZATION_OUTCOME_STATUSES:
            raise ValueError("finalization status is not a closed value")
        return value

    @field_validator("reason")
    @classmethod
    def _v2_finalization_reason_is_closed(cls, value: str | None) -> str | None:
        if value is not None and value not in AUTHORITY_POLICY_V2_FINALIZATION_PENDING_REASONS:
            raise ValueError("finalization reason is not a closed value")
        return value

    @model_validator(mode="after")
    def _v2_finalization_shape(self) -> AuthorityPolicyV2FinalizationOutcome:
        if self.status == "finalization_pending":
            if self.reason is None:
                raise ValueError("a pending finalization requires a bounded reason")
        elif self.reason is not None:
            raise ValueError("a non-pending finalization carries no reason")
        return self


class AuthorityPolicyV2SettlementOutcome(BaseModel):
    """Bounded outcome of the separate exact post-final settlement contract."""
    model_config = {"extra": "forbid", "strict": True, "frozen": True}

    status: StrictStr
    attempt_id: StrictStr
    reason: StrictStr | None = None
    candidate_id: StrictStr | None = None
    envelope_id: StrictStr | None = None
    notification_id: StrictStr | None = None
    generation_id: StrictStr | None = None
    recovery: bool = False
    receipt_settled: bool = False

    @field_validator("status")
    @classmethod
    def _v2_settlement_status_is_closed(cls, value: str) -> str:
        if value not in AUTHORITY_POLICY_V2_SETTLEMENT_OUTCOME_STATUSES:
            raise ValueError("settlement status is not a closed value")
        return value

    @field_validator("reason")
    @classmethod
    def _v2_settlement_reason_is_closed(cls, value: str | None) -> str | None:
        if value is not None and value not in AUTHORITY_POLICY_V2_SETTLEMENT_PENDING_REASONS:
            raise ValueError("settlement reason is not a closed value")
        return value

    @model_validator(mode="after")
    def _v2_settlement_shape(self) -> AuthorityPolicyV2SettlementOutcome:
        if self.status == "settlement_pending":
            if self.reason is None:
                raise ValueError("a pending settlement requires a bounded reason")
        elif self.reason is not None:
            raise ValueError("a settled outcome carries no pending reason")
        if self.status == "already_settled_exact" and not self.receipt_settled:
            raise ValueError("already_settled_exact requires the settled receipt")
        return self


# THR-229 checkpoint C3d3a: bounded discovery record and outcomes for the
# callable authenticated publication-bookkeeping storage seams.  The discovery
# record is EVIDENCE ONLY and grants no authority; the outcomes are closed
# dispositions, never launch authority.


class AuthorityPolicyV2PublicationTarget(BaseModel):
    """Read-only discovery record for one publishable notification (N)."""
    model_config = {"extra": "forbid", "strict": True, "frozen": True}

    notification_id: StrictStr
    envelope_id: StrictStr
    candidate_id: StrictStr
    result_id: StrictInt = Field(ge=1, le=9223372036854775807)
    root_task_id: StrictStr
    manager_agent: StrictStr
    manager_session_id: StrictStr
    selector_id: StrictStr
    state: StrictStr
    publication_attempt: StrictInt = Field(default=0, ge=0, le=2147483647)
    publisher_boot_id: StrictStr | None = None
    lease_deadline: StrictStr | None = None

    @field_validator("notification_id")
    @classmethod
    def _v2_publication_target_notification_shape(cls, value: str) -> str:
        if not value.startswith("APV2N-"):
            raise ValueError("notification_id must start with APV2N-")
        _validate_authority_policy_v2_digest(value[len("APV2N-"):], "notification_id")
        return value

    @field_validator("envelope_id")
    @classmethod
    def _v2_publication_target_envelope_shape(cls, value: str) -> str:
        if not value.startswith("APV2E-"):
            raise ValueError("envelope_id must start with APV2E-")
        _validate_authority_policy_v2_digest(value[len("APV2E-"):], "envelope_id")
        return value

    @field_validator("candidate_id")
    @classmethod
    def _v2_publication_target_candidate_shape(cls, value: str) -> str:
        if not value.startswith("APV2C-"):
            raise ValueError("candidate_id must start with APV2C-")
        _validate_authority_policy_v2_digest(value[len("APV2C-"):], "candidate_id")
        return value

    @field_validator("selector_id")
    @classmethod
    def _v2_publication_target_selector_ref(cls, value: str) -> str:
        return _validate_authority_policy_v2_selector_ref(value, "selector_id")

    @field_validator("root_task_id", "manager_agent", "manager_session_id")
    @classmethod
    def _v2_publication_target_identity_scalars(cls, value: str, info) -> str:
        return _validate_authority_policy_v2_text(value, info.field_name, 128)

    @field_validator("state")
    @classmethod
    def _v2_publication_target_state_is_closed(cls, value: str) -> str:
        if value not in AUTHORITY_POLICY_V2_PUBLICATION_STATES:
            raise ValueError("publication target state is not a closed value")
        return value

    @field_validator("publisher_boot_id", "lease_deadline")
    @classmethod
    def _v2_publication_target_optional_scalars(cls, value: str | None, info) -> str | None:
        if value is None:
            return None
        return _validate_authority_policy_v2_text(value, info.field_name, 128)

    @model_validator(mode="after")
    def _v2_publication_target_lease_pair(self) -> AuthorityPolicyV2PublicationTarget:
        if (self.publisher_boot_id is None) != (self.lease_deadline is None):
            raise ValueError("publisher boot and lease are present or absent together")
        return self


class AuthorityPolicyV2PublicationClaimOutcome(BaseModel):
    """Bounded outcome of the ONE publication claim/reclaim transaction."""
    model_config = {"extra": "forbid", "strict": True, "frozen": True}

    status: StrictStr
    reason: StrictStr | None = None
    notification_id: StrictStr | None = None
    envelope_id: StrictStr | None = None
    generation_id: StrictStr | None = None
    publication_attempt: StrictInt | None = Field(default=None, ge=1, le=2147483647)
    publisher_boot_id: StrictStr | None = None
    lease_deadline: StrictStr | None = None

    @field_validator("status")
    @classmethod
    def _v2_publication_claim_status_is_closed(cls, value: str) -> str:
        if value not in AUTHORITY_POLICY_V2_PUBLICATION_CLAIM_STATUSES:
            raise ValueError("publication claim status is not a closed value")
        return value

    @field_validator("reason")
    @classmethod
    def _v2_publication_claim_reason_is_closed(cls, value: str | None) -> str | None:
        if value is not None and value not in AUTHORITY_POLICY_V2_PUBLICATION_CLAIM_PENDING_REASONS:
            raise ValueError("publication claim reason is not a closed value")
        return value

    @model_validator(mode="after")
    def _v2_publication_claim_shape(self) -> AuthorityPolicyV2PublicationClaimOutcome:
        if self.status == "publication_pending":
            if self.reason is None:
                raise ValueError("a pending publication claim requires a bounded reason")
            if self.publication_attempt is not None:
                raise ValueError("a pending publication claim carries no attempt")
            return self
        if self.reason is not None:
            raise ValueError("a claimed outcome carries no pending reason")
        if (
            self.notification_id is None or self.generation_id is None
            or self.publication_attempt is None or self.publisher_boot_id is None
            or self.lease_deadline is None
        ):
            raise ValueError("a claimed outcome requires the exact claim evidence")
        return self


class AuthorityPolicyV2PublicationAckOutcome(BaseModel):
    """Bounded outcome of the exact publication acknowledgement transaction."""
    model_config = {"extra": "forbid", "strict": True, "frozen": True}

    status: StrictStr
    reason: StrictStr | None = None
    notification_id: StrictStr | None = None
    generation_id: StrictStr | None = None
    publication_attempt: StrictInt | None = Field(default=None, ge=1, le=2147483647)
    state: StrictStr | None = None

    @field_validator("status")
    @classmethod
    def _v2_publication_ack_status_is_closed(cls, value: str) -> str:
        if value not in AUTHORITY_POLICY_V2_PUBLICATION_ACK_STATUSES:
            raise ValueError("publication acknowledgement status is not a closed value")
        return value

    @field_validator("reason")
    @classmethod
    def _v2_publication_ack_reason_is_closed(cls, value: str | None) -> str | None:
        if value is not None and value not in AUTHORITY_POLICY_V2_PUBLICATION_ACK_PENDING_REASONS:
            raise ValueError("publication acknowledgement reason is not a closed value")
        return value

    @model_validator(mode="after")
    def _v2_publication_ack_shape(self) -> AuthorityPolicyV2PublicationAckOutcome:
        if self.status == "ack_pending":
            if self.reason is None:
                raise ValueError("a pending acknowledgement requires a bounded reason")
            if self.publication_attempt is not None:
                raise ValueError("a pending acknowledgement carries no attempt")
            return self
        if self.reason is not None:
            raise ValueError("a completed acknowledgement carries no pending reason")
        if (
            self.notification_id is None or self.generation_id is None
            or self.publication_attempt is None or self.state is None
        ):
            raise ValueError("a completed acknowledgement requires the exact identity")
        return self


class AuthorityPolicyV2PublicationFailureOutcome(BaseModel):
    """Bounded outcome of the audited queue-failure bookkeeping transaction."""
    model_config = {"extra": "forbid", "strict": True, "frozen": True}

    status: StrictStr
    reason: StrictStr | None = None
    notification_id: StrictStr | None = None
    generation_id: StrictStr | None = None
    publication_attempt: StrictInt | None = Field(default=None, ge=1, le=2147483647)
    state: StrictStr | None = None

    @field_validator("status")
    @classmethod
    def _v2_publication_failure_status_is_closed(cls, value: str) -> str:
        if value not in AUTHORITY_POLICY_V2_PUBLICATION_FAILURE_STATUSES:
            raise ValueError("publication failure status is not a closed value")
        return value

    @field_validator("reason")
    @classmethod
    def _v2_publication_failure_reason_is_closed(cls, value: str | None) -> str | None:
        if value is not None and value not in AUTHORITY_POLICY_V2_PUBLICATION_FAILURE_PENDING_REASONS:
            raise ValueError("publication failure reason is not a closed value")
        return value

    @model_validator(mode="after")
    def _v2_publication_failure_shape(self) -> AuthorityPolicyV2PublicationFailureOutcome:
        if self.status == "failure_pending":
            if self.reason is None:
                raise ValueError("a pending failure record requires a bounded reason")
            if self.publication_attempt is not None:
                raise ValueError("a pending failure record carries no attempt")
            return self
        if self.reason is not None:
            raise ValueError("a recorded failure carries no pending reason")
        if (
            self.notification_id is None or self.generation_id is None
            or self.publication_attempt is None or self.state is None
        ):
            raise ValueError("a recorded failure requires the exact identity")
        return self


class AuthorityPolicyV2InvalidationOutcome(BaseModel):
    """Bounded outcome of the exact generation-invalidation transaction."""
    model_config = {"extra": "forbid", "strict": True, "frozen": True}

    status: StrictStr
    reason: StrictStr | None = None
    notification_id: StrictStr | None = None
    generation_id: StrictStr | None = None
    envelope_id: StrictStr | None = None
    notification_state: StrictStr | None = None
    dispatch_state: StrictStr | None = None

    @field_validator("status")
    @classmethod
    def _v2_invalidation_status_is_closed(cls, value: str) -> str:
        if value not in AUTHORITY_POLICY_V2_INVALIDATION_STATUSES:
            raise ValueError("invalidation status is not a closed value")
        return value

    @field_validator("reason")
    @classmethod
    def _v2_invalidation_reason_is_closed(cls, value: str | None) -> str | None:
        if value is not None and value not in AUTHORITY_POLICY_V2_INVALIDATION_PENDING_REASONS:
            raise ValueError("invalidation reason is not a closed value")
        return value

    @model_validator(mode="after")
    def _v2_invalidation_shape(self) -> AuthorityPolicyV2InvalidationOutcome:
        if self.status == "invalidation_pending":
            if self.reason is None:
                raise ValueError("a pending invalidation requires a bounded reason")
            return self
        if self.reason is not None:
            raise ValueError("a completed invalidation carries no pending reason")
        if (
            self.notification_id is None or self.generation_id is None
            or self.envelope_id is None or self.notification_state is None
            or self.dispatch_state is None
        ):
            raise ValueError("a completed invalidation requires the exact identity")
        return self


class AuthorityPolicyV2GenerationClaimOutcome(BaseModel):
    """Bounded outcome of the ONE atomic generation-admission claim transaction.

    ``claimed`` carries the exact causal identity plus the reserved
    ``next_session_id`` and the winning ``orchestration_step_count``; the caller
    may then settle and launch with that exact session.  It is never by itself a
    guarantee of external launch, and a non-``claimed`` status means NO
    generation admission occurred (so no ordinary claim/launch may follow).
    """
    model_config = {"extra": "forbid", "strict": True, "frozen": True}

    status: StrictStr
    reason: StrictStr | None = None
    attempt_id: StrictStr | None = None
    notification_id: StrictStr | None = None
    envelope_id: StrictStr | None = None
    generation_id: StrictStr | None = None
    next_session_id: StrictStr | None = None
    orchestration_step_count: StrictInt | None = Field(
        default=None, ge=0, le=9223372036854775807,
    )

    @field_validator("status")
    @classmethod
    def _v2_generation_claim_status_is_closed(cls, value: str) -> str:
        if value not in AUTHORITY_POLICY_V2_GENERATION_CLAIM_STATUSES:
            raise ValueError("generation claim status is not a closed value")
        return value

    @field_validator("reason")
    @classmethod
    def _v2_generation_claim_reason_is_closed(cls, value: str | None) -> str | None:
        if value is not None and value not in AUTHORITY_POLICY_V2_GENERATION_CLAIM_PENDING_REASONS:
            raise ValueError("generation claim reason is not a closed value")
        return value

    @model_validator(mode="after")
    def _v2_generation_claim_shape(self) -> AuthorityPolicyV2GenerationClaimOutcome:
        if self.status == "generation_pending":
            if self.reason is None:
                raise ValueError("a pending generation claim requires a bounded reason")
            if self.next_session_id is not None:
                raise ValueError("a pending generation claim reserves no session")
            return self
        if self.reason is not None:
            raise ValueError("a claimed generation carries no pending reason")
        if (
            self.attempt_id is None or self.notification_id is None
            or self.envelope_id is None or self.generation_id is None
            or self.next_session_id is None or self.orchestration_step_count is None
        ):
            raise ValueError("a claimed generation requires the exact reservation")
        return self


class AuthorityPolicyV2AdmissionSettlementOutcome(BaseModel):
    """Bounded outcome of the separate admission-settlement transaction."""
    model_config = {"extra": "forbid", "strict": True, "frozen": True}

    status: StrictStr
    reason: StrictStr | None = None
    attempt_id: StrictStr | None = None
    notification_id: StrictStr | None = None
    generation_id: StrictStr | None = None
    next_session_id: StrictStr | None = None

    @field_validator("status")
    @classmethod
    def _v2_admission_settlement_status_is_closed(cls, value: str) -> str:
        if value not in AUTHORITY_POLICY_V2_ADMISSION_SETTLEMENT_STATUSES:
            raise ValueError("admission settlement status is not a closed value")
        return value

    @field_validator("reason")
    @classmethod
    def _v2_admission_settlement_reason_is_closed(cls, value: str | None) -> str | None:
        if value is not None and value not in AUTHORITY_POLICY_V2_ADMISSION_SETTLEMENT_PENDING_REASONS:
            raise ValueError("admission settlement reason is not a closed value")
        return value

    @model_validator(mode="after")
    def _v2_admission_settlement_shape(self) -> AuthorityPolicyV2AdmissionSettlementOutcome:
        if self.status == "settlement_pending":
            if self.reason is None:
                raise ValueError("a pending settlement requires a bounded reason")
            return self
        if self.reason is not None:
            raise ValueError("a completed settlement carries no pending reason")
        if (
            self.attempt_id is None or self.notification_id is None
            or self.generation_id is None or self.next_session_id is None
        ):
            raise ValueError("a completed settlement requires the exact identity")
        return self


class AuthorityPolicyV2SpendOutcome(BaseModel):
    """Bounded outcome of the ONE atomic next-result spend-to-ready transaction.

    ``spent`` just committed the receipt (E active -> consumed with the exact
    spending result + ``decision_state=ready``, D admitted -> retired); the
    caller may then let the ordinary decision consumer process the SAME result.
    ``already_spent_exact`` is a read-only exact retry of an authenticated
    consumed/ready receipt and performs NO write, remint or dispatch.
    ``spend_pending`` means no spend occurred and E stayed active / D admitted
    with the decision unapplied.  A committed outcome additionally carries
    ``report_digest``: the exact normalized report identity of the retained
    spending result bound into the durable receipt.  None of these statuses is
    launch authority.
    """
    model_config = {"extra": "forbid", "strict": True, "frozen": True}

    status: StrictStr
    reason: StrictStr | None = None
    attempt_id: StrictStr | None = None
    candidate_id: StrictStr | None = None
    notification_id: StrictStr | None = None
    envelope_id: StrictStr | None = None
    generation_id: StrictStr | None = None
    result_id: StrictInt | None = Field(default=None, ge=1, le=9223372036854775807)
    spending_result_id: StrictInt | None = Field(
        default=None, ge=1, le=9223372036854775807,
    )
    next_session_id: StrictStr | None = None
    decision_state: StrictStr | None = None
    report_digest: StrictStr | None = None

    @field_validator("status")
    @classmethod
    def _v2_spend_status_is_closed(cls, value: str) -> str:
        if value not in AUTHORITY_POLICY_V2_SPEND_STATUSES:
            raise ValueError("spend status is not a closed value")
        return value

    @field_validator("report_digest")
    @classmethod
    def _v2_spend_report_digest_is_sha256(cls, value: str | None) -> str | None:
        if value is not None:
            _validate_authority_policy_v2_digest(value, "report_digest")
        return value

    @field_validator("reason")
    @classmethod
    def _v2_spend_reason_is_closed(cls, value: str | None) -> str | None:
        if value is not None and value not in AUTHORITY_POLICY_V2_SPEND_PENDING_REASONS:
            raise ValueError("spend reason is not a closed value")
        return value

    @field_validator("decision_state")
    @classmethod
    def _v2_spend_decision_state_is_closed(cls, value: str | None) -> str | None:
        if value is not None and value not in AUTHORITY_POLICY_V2_DECISION_STATES:
            raise ValueError("spend decision_state is not a closed value")
        return value

    @model_validator(mode="after")
    def _v2_spend_shape(self) -> AuthorityPolicyV2SpendOutcome:
        if self.status == "spend_pending":
            if self.reason is None:
                raise ValueError("a pending spend requires a bounded reason")
            return self
        if self.reason is not None:
            raise ValueError("a committed spend carries no pending reason")
        if (
            self.attempt_id is None or self.candidate_id is None
            or self.notification_id is None or self.envelope_id is None
            or self.generation_id is None or self.result_id is None
            or self.spending_result_id is None or self.next_session_id is None
            or self.decision_state is None or self.report_digest is None
        ):
            raise ValueError(
                "a committed spend requires the exact receipt identity and "
                "bound report digest"
            )
        return self


class AuthorityPolicyV2DecisionClaimOutcome(BaseModel):
    """Bounded outcome of the ONE atomic decision-dispatch claim transaction.

    ``claimed`` just committed the single-use ``ready -> claimed`` CAS on the
    exact result-keyed receipt together with one closed ``decision_claimed``
    audit; ONLY that winning return authorizes exactly one ordinary consumer
    entry.  ``decision_pending`` means NO consumer authority exists: the caller
    must not run the normal decision body.  A committed outcome carries the exact
    receipt identity plus the bound ``report_digest``; none of these statuses is
    launch authority.
    """
    model_config = {"extra": "forbid", "strict": True, "frozen": True}

    status: StrictStr
    reason: StrictStr | None = None
    attempt_id: StrictStr | None = None
    candidate_id: StrictStr | None = None
    notification_id: StrictStr | None = None
    envelope_id: StrictStr | None = None
    generation_id: StrictStr | None = None
    result_id: StrictInt | None = Field(default=None, ge=1, le=9223372036854775807)
    spending_result_id: StrictInt | None = Field(
        default=None, ge=1, le=9223372036854775807,
    )
    next_session_id: StrictStr | None = None
    decision_state: StrictStr | None = None
    report_digest: StrictStr | None = None

    @field_validator("status")
    @classmethod
    def _v2_decision_claim_status_is_closed(cls, value: str) -> str:
        if value not in AUTHORITY_POLICY_V2_DECISION_CLAIM_STATUSES:
            raise ValueError("decision claim status is not a closed value")
        return value

    @field_validator("reason")
    @classmethod
    def _v2_decision_claim_reason_is_closed(cls, value: str | None) -> str | None:
        if value is not None and value not in AUTHORITY_POLICY_V2_DECISION_CLAIM_PENDING_REASONS:
            raise ValueError("decision claim reason is not a closed value")
        return value

    @field_validator("report_digest")
    @classmethod
    def _v2_decision_claim_report_digest_is_sha256(
        cls, value: str | None,
    ) -> str | None:
        if value is not None:
            _validate_authority_policy_v2_digest(value, "report_digest")
        return value

    @field_validator("decision_state")
    @classmethod
    def _v2_decision_claim_state_is_closed(cls, value: str | None) -> str | None:
        if value is not None and value not in AUTHORITY_POLICY_V2_DECISION_STATES:
            raise ValueError("decision claim state is not a closed value")
        return value

    @model_validator(mode="after")
    def _v2_decision_claim_shape(self) -> AuthorityPolicyV2DecisionClaimOutcome:
        if self.status == "decision_pending":
            if self.reason is None:
                raise ValueError("a pending decision claim requires a bounded reason")
            return self
        if self.reason is not None:
            raise ValueError("a claimed decision carries no pending reason")
        if self.decision_state != "claimed":
            raise ValueError("a claimed decision receipt must be exactly claimed")
        if (
            self.attempt_id is None or self.candidate_id is None
            or self.notification_id is None or self.envelope_id is None
            or self.generation_id is None or self.result_id is None
            or self.spending_result_id is None or self.next_session_id is None
            or self.report_digest is None
        ):
            raise ValueError(
                "a claimed decision requires the exact receipt identity and "
                "bound report digest"
            )
        return self


class AuthorityPolicyV2DecisionAckOutcome(BaseModel):
    """Bounded outcome of the separate decision acknowledgement transaction.

    ``applied`` just CASed ``claimed -> applied`` with one closed
    ``decision_applied`` audit after the winning caller returned from the real
    ordinary consumer.  ``already_applied_exact`` is a read-only replay of the
    authenticated applied receipt and NEVER authorizes a second consumer call.
    ``ack_pending`` means the receipt stayed ``claimed`` and no acknowledgement
    was written; the caller must not re-run the consumer.
    """
    model_config = {"extra": "forbid", "strict": True, "frozen": True}

    status: StrictStr
    reason: StrictStr | None = None
    attempt_id: StrictStr | None = None
    candidate_id: StrictStr | None = None
    envelope_id: StrictStr | None = None
    generation_id: StrictStr | None = None
    spending_result_id: StrictInt | None = Field(
        default=None, ge=1, le=9223372036854775807,
    )
    next_session_id: StrictStr | None = None
    decision_state: StrictStr | None = None
    report_digest: StrictStr | None = None

    @field_validator("status")
    @classmethod
    def _v2_decision_ack_status_is_closed(cls, value: str) -> str:
        if value not in AUTHORITY_POLICY_V2_DECISION_ACK_STATUSES:
            raise ValueError("decision ack status is not a closed value")
        return value

    @field_validator("reason")
    @classmethod
    def _v2_decision_ack_reason_is_closed(cls, value: str | None) -> str | None:
        if value is not None and value not in AUTHORITY_POLICY_V2_DECISION_ACK_PENDING_REASONS:
            raise ValueError("decision ack reason is not a closed value")
        return value

    @field_validator("report_digest")
    @classmethod
    def _v2_decision_ack_report_digest_is_sha256(
        cls, value: str | None,
    ) -> str | None:
        if value is not None:
            _validate_authority_policy_v2_digest(value, "report_digest")
        return value

    @field_validator("decision_state")
    @classmethod
    def _v2_decision_ack_state_is_closed(cls, value: str | None) -> str | None:
        if value is not None and value not in AUTHORITY_POLICY_V2_DECISION_STATES:
            raise ValueError("decision ack state is not a closed value")
        return value

    @model_validator(mode="after")
    def _v2_decision_ack_shape(self) -> AuthorityPolicyV2DecisionAckOutcome:
        if self.status == "ack_pending":
            if self.reason is None:
                raise ValueError("a pending decision ack requires a bounded reason")
            return self
        if self.reason is not None:
            raise ValueError("an acknowledged decision carries no pending reason")
        if self.decision_state != "applied":
            raise ValueError("an acknowledged decision receipt must be applied")
        if (
            self.envelope_id is None or self.spending_result_id is None
            or self.next_session_id is None or self.report_digest is None
        ):
            raise ValueError("an acknowledged decision requires the exact receipt")
        return self


class AuthorityPolicyV2DecisionRefusalOutcome(BaseModel):
    """Bounded outcome of the audited decision-dispatch interruption refusal.

    ``refused`` just CASed ``claimed -> refused`` on the exact result-keyed
    receipt with one closed ``decision_dispatch_interrupted`` audit; the spent
    envelope, causal attempt/journal evidence and every earlier committed
    effect are preserved byte-for-byte, the exact owned obligation is retired/
    settled only, and any replacement generation B is untouched.  A
    still-current nonterminal reserved invocation is escalated with the closed
    diagnostic; a cancelled/terminal/replaced task is preserved exactly.
    ``already_refused`` is a read-only exact replay.  ``refusal_pending`` means
    the receipt stayed ``claimed`` (or the transaction failed) and the
    discoverable claimed state is retained.
    """
    model_config = {"extra": "forbid", "strict": True, "frozen": True}

    status: StrictStr
    reason: StrictStr | None = None
    attempt_id: StrictStr | None = None
    candidate_id: StrictStr | None = None
    envelope_id: StrictStr | None = None
    generation_id: StrictStr | None = None
    spending_result_id: StrictInt | None = Field(
        default=None, ge=1, le=9223372036854775807,
    )
    next_session_id: StrictStr | None = None
    decision_state: StrictStr | None = None
    refusal_code: StrictStr | None = None

    @field_validator("status")
    @classmethod
    def _v2_decision_refusal_status_is_closed(cls, value: str) -> str:
        if value not in AUTHORITY_POLICY_V2_DECISION_REFUSAL_STATUSES:
            raise ValueError("decision refusal status is not a closed value")
        return value

    @field_validator("reason")
    @classmethod
    def _v2_decision_refusal_reason_is_closed(cls, value: str | None) -> str | None:
        if value is not None and value not in AUTHORITY_POLICY_V2_DECISION_REFUSAL_PENDING_REASONS:
            raise ValueError("decision refusal reason is not a closed value")
        return value

    @field_validator("refusal_code")
    @classmethod
    def _v2_decision_refusal_code_is_closed(cls, value: str | None) -> str | None:
        if value is not None and value != AUTHORITY_POLICY_V2_RESULT_STAGE_DECISION_DISPATCH_INTERRUPTED:
            raise ValueError("decision refusal code is not the closed interruption code")
        return value

    @field_validator("decision_state")
    @classmethod
    def _v2_decision_refusal_state_is_closed(cls, value: str | None) -> str | None:
        if value is not None and value not in AUTHORITY_POLICY_V2_DECISION_STATES:
            raise ValueError("decision refusal state is not a closed value")
        return value

    @model_validator(mode="after")
    def _v2_decision_refusal_shape(self) -> AuthorityPolicyV2DecisionRefusalOutcome:
        if self.status == "refusal_pending":
            if self.reason is None:
                raise ValueError("a pending decision refusal requires a bounded reason")
            return self
        if self.reason is not None:
            raise ValueError("a refused decision carries no pending reason")
        if self.decision_state != "refused":
            raise ValueError("a refused decision receipt must be exactly refused")
        if self.refusal_code != AUTHORITY_POLICY_V2_RESULT_STAGE_DECISION_DISPATCH_INTERRUPTED:
            raise ValueError("a refused decision requires the closed interruption code")
        if (
            self.envelope_id is None or self.spending_result_id is None
            or self.next_session_id is None
        ):
            raise ValueError("a refused decision requires the exact receipt")
        return self


AUTHORITY_POLICY_V2_COMPLETION_DISPATCH_KINDS = frozenset({
    "no_v2", "receipt", "reserved", "causal", "foreign",
})


class AuthorityPolicyV2CompletionDispatchContext(BaseModel):
    """Read-only classification of one completion against the v2 lineage.

    Produced from REAL persisted v2 rows (attempts, envelopes, notifications and
    the root dispatch pointer) -- never from reader absence or a mock's
    ``None``.  ``no_v2`` is the only kind that authorizes the unchanged ordinary
    path.  ``receipt`` names an already-spent result-keyed receipt whose closed
    ``decision_state`` is the single-use token.  ``reserved`` is the exact
    active reserved next-result R2 that the common consumer must atomically
    spend before claiming.  ``causal`` is the causal result R of a v2 attempt
    and is continuation bookkeeping only.  ``foreign`` is every unmatched,
    malformed or conflicting identity on a root with a live v2 lineage: it never
    becomes ordinary permission.

    The context carries the CAUSAL manager identity, so the winning caller
    acknowledges/refuses with exactly the identity it claimed -- never the
    reserved spending result id and never whichever task/owner happens to be
    current after the effect.
    """
    model_config = {"extra": "forbid", "strict": True, "frozen": True}

    kind: StrictStr
    manager_agent: StrictStr | None = None
    manager_session_id: StrictStr | None = None
    causal_result_id: StrictInt | None = Field(
        default=None, ge=1, le=9223372036854775807,
    )
    generation_id: StrictStr | None = None
    next_session_id: StrictStr | None = None
    spending_result_id: StrictInt | None = Field(
        default=None, ge=1, le=9223372036854775807,
    )
    decision_state: StrictStr | None = None

    @field_validator("kind")
    @classmethod
    def _v2_completion_dispatch_kind_is_closed(cls, value: str) -> str:
        if value not in AUTHORITY_POLICY_V2_COMPLETION_DISPATCH_KINDS:
            raise ValueError("completion dispatch kind is not a closed value")
        return value

    @field_validator("decision_state")
    @classmethod
    def _v2_completion_dispatch_state_is_closed(
        cls, value: str | None,
    ) -> str | None:
        if value is not None and value not in AUTHORITY_POLICY_V2_DECISION_STATES:
            raise ValueError("completion dispatch state is not a closed value")
        return value


class AuthorityPolicyV2SchemaIntegrity(BaseModel):
    """Bounded read-only v2 schema-integrity EVIDENCE (THR-229 checkpoint C3a).

    Produced by the independent constraint-sensitive reference oracle in
    ``runtime/orchestrator/authority.py``.  ``raw_digest`` is the candidate
    database's ACTUAL raw ``sqlite_master`` DDL digest captured at validation
    time, and ``inventory_digest`` is the canonical digest of the complete
    non-internal object inventory that matched an accepted reference layout.
    This value is integrity evidence only: it is NOT policy authority, NOT a
    policy-clause match, and NOT a continuation grant.  A recheck denies ANY
    later raw-digest drift; a failed or unavailable capture can never become a
    successful recheck.
    """

    model_config = {"extra": "forbid", "strict": True, "frozen": True}

    contract_version: StrictStr
    raw_digest: StrictStr
    inventory_digest: StrictStr
    object_count: StrictInt = Field(ge=0, le=100000)


class AuthorityPolicyV2PermissionSurface(BaseModel):
    """Bounded read-only v2 permission-surface evidence (THR-229 C3b correction).

    Produced by the narrowly scoped server-side permission reader bound on the
    ``Database`` — never from a caller-supplied allow/deny boolean.  ``digest``
    is the digest of the current org permission surface (org config + the
    active agent definition) read through that server-side seam.  This value is
    integrity EVIDENCE only: it grants no authority and never repairs anything.
    A recheck denies ANY later change; an unavailable/read-failure/malformed
    read can never become a successful recheck.
    """

    model_config = {"extra": "forbid", "strict": True, "frozen": True}

    contract_version: StrictStr
    digest: StrictStr


class AuthorityPolicyV2PairedControlRequest(BaseModel):
    """Strict paired create+activate request; client version/digest/ids are rejected."""
    model_config = {"extra": "forbid", "strict": True, "frozen": True}

    team: Literal[AUTHORITY_POLICY_V2_TEAM]
    policy_id: StrictStr
    title: StrictStr
    create_request_id: StrictStr
    activation_request_id: StrictStr
    based_on_selector_id: StrictStr | None
    expected_selector_id: StrictStr | None
    action: Literal["bootstrap", "activate"]
    what_to_escalate: StrictStr
    what_not_to_escalate: StrictStr

    @field_validator("policy_id")
    @classmethod
    def _v2_paired_policy_id(cls, value: str) -> str:
        if not _AUTHORITY_POLICY_V2_POLICY_ID_RE.fullmatch(value):
            raise ValueError("policy_id must match [a-z][a-z0-9-]{0,63}")
        return value

    @field_validator("title")
    @classmethod
    def _v2_paired_title(cls, value: str, info) -> str:
        return _validate_authority_policy_v2_text(value, info.field_name, _AUTHORITY_POLICY_V2_MAX_TITLE)

    @field_validator("what_to_escalate", "what_not_to_escalate")
    @classmethod
    def _v2_paired_texts(cls, value: str, info) -> str:
        return _validate_authority_policy_v2_text(value, info.field_name, _AUTHORITY_POLICY_V2_MAX_TEXT)

    @field_validator("create_request_id", "activation_request_id")
    @classmethod
    def _v2_paired_request_ids(cls, value: str, info) -> str:
        return _validate_authority_policy_v2_request_id(value, info.field_name)

    @field_validator("based_on_selector_id", "expected_selector_id")
    @classmethod
    def _v2_paired_selector_refs(cls, value: str | None, info) -> str | None:
        return _validate_authority_policy_v2_selector_ref(value, info.field_name)

    @model_validator(mode="after")
    def _v2_paired_is_bounded(self) -> AuthorityPolicyV2PairedControlRequest:
        canonical = authority_policy_v2_canonical_json_bytes(self.model_dump(mode="json"))
        if len(canonical) > AUTHORITY_POLICY_V2_MAX_CANONICAL_BYTES:
            raise ValueError(
                "control request canonical JSON exceeds "
                f"{AUTHORITY_POLICY_V2_MAX_CANONICAL_BYTES} bytes"
            )
        material = "\n".join((self.title, self.what_to_escalate, self.what_not_to_escalate))
        if AUTHORITY_POLICY_SECRET_SHAPE_RE.search(material):
            raise ValueError("saved policy text contains secret-shaped input")
        return self

    def create_request_preimage(self) -> dict[str, object]:
        return authority_policy_v2_create_request_preimage(
            based_on_selector_id=self.based_on_selector_id, kind="v2_create",
            policy_id=self.policy_id, request_id=self.create_request_id, team=self.team,
            title=self.title, what_to_escalate=self.what_to_escalate,
            what_not_to_escalate=self.what_not_to_escalate,
        )

    def activation_request_preimage(self, release_id: str) -> dict[str, object]:
        return authority_policy_v2_activation_request_preimage(
            action=self.action, expected_selector_id=self.expected_selector_id,
            kind="v2_activate", release_id=release_id,
            request_id=self.activation_request_id, team=self.team,
        )

    def create_request_digest(self) -> str:
        return authority_policy_v2_sha256(self.create_request_preimage())

    def to_release(self, *, version: int) -> AuthorityPolicyV2Release:
        return AuthorityPolicyV2Release(
            contract_digest=authority_policy_v2_contract_digest(),
            policy_id=self.policy_id, team=self.team, title=self.title, version=version,
            what_to_escalate=self.what_to_escalate,
            what_not_to_escalate=self.what_not_to_escalate,
        )


class AuthorityPolicyV2ActivationControlRequest(BaseModel):
    """Strict activation request for an existing v2 release."""
    model_config = {"extra": "forbid", "strict": True, "frozen": True}

    team: Literal[AUTHORITY_POLICY_V2_TEAM]
    release_id: StrictStr
    request_id: StrictStr
    expected_selector_id: StrictStr | None
    action: Literal["bootstrap", "activate", "reactivate_rollback"]

    @field_validator("request_id")
    @classmethod
    def _v2_activate_request_id(cls, value: str, info) -> str:
        return _validate_authority_policy_v2_request_id(value, info.field_name)

    @field_validator("expected_selector_id")
    @classmethod
    def _v2_activate_selector_ref(cls, value: str | None, info) -> str | None:
        return _validate_authority_policy_v2_selector_ref(value, info.field_name)

    @field_validator("release_id")
    @classmethod
    def _v2_activate_release_id(cls, value: str) -> str:
        if not value.startswith("APV2-"):
            raise ValueError("release_id must start with APV2-")
        _validate_authority_policy_v2_digest(value[len("APV2-"):], "release_id")
        return value

    def activation_request_preimage(self) -> dict[str, object]:
        return authority_policy_v2_activation_request_preimage(
            action=self.action, expected_selector_id=self.expected_selector_id,
            kind="v2_activate", release_id=self.release_id,
            request_id=self.request_id, team=self.team,
        )

    def request_digest(self) -> str:
        return authority_policy_v2_sha256(self.activation_request_preimage())


class AuthorityPolicyV2ControlReceipt(BaseModel):
    """Deterministic receipt binding both request IDs/digests of a control write."""
    model_config = {"extra": "forbid", "strict": True, "frozen": True}

    team: Literal[AUTHORITY_POLICY_V2_TEAM]
    kind: Literal["v2_create_activate", "v2_activate"]
    create_request_id: StrictStr | None
    create_request_digest: StrictStr | None
    activation_request_id: StrictStr
    activation_request_digest: StrictStr
    release_id: StrictStr
    policy_digest: StrictStr
    release_version: StrictInt = Field(ge=1, le=2147483647)
    activation_id: StrictStr
    activation_digest: StrictStr
    selector_id: StrictStr
    selector_epoch: StrictInt = Field(ge=1, le=2147483647)
    action: Literal["bootstrap", "activate", "reactivate_rollback"]
    previous_selector_id: StrictStr | None = None
    created_at: StrictStr

    @field_validator("create_request_digest", "activation_request_digest", "policy_digest",
                     "activation_digest")
    @classmethod
    def _v2_receipt_digests(cls, value: str | None, info) -> str | None:
        if value is None:
            return None
        return _validate_authority_policy_v2_digest(value, info.field_name)

    @field_validator("selector_id", "previous_selector_id")
    @classmethod
    def _v2_receipt_selectors(cls, value: str | None, info) -> str | None:
        return _validate_authority_policy_v2_selector_ref(value, info.field_name)

    @model_validator(mode="after")
    def _v2_receipt_bound_ids(self) -> AuthorityPolicyV2ControlReceipt:
        if (self.create_request_id is None) != (self.create_request_digest is None):
            raise ValueError("paired receipt must bind both create request id and digest")
        if self.kind == "v2_create_activate" and self.create_request_id is None:
            raise ValueError("create+activate receipt requires a create request")
        if self.release_id != f"APV2-{self.policy_digest}":
            raise ValueError("release_id must contain policy_digest")
        if self.activation_id != f"APV2A-{self.activation_digest}":
            raise ValueError("activation_id must contain activation_digest")
        return self

    def canonical_json(self) -> str:
        return authority_policy_v2_canonical_json_bytes(self.model_dump(mode="json")).decode("utf-8")


_AUTHORITY_POLICY_LEGACY_ACTIVATION_ID_RE = re.compile(r"^APA-[A-Za-z0-9._:-]{1,124}$")
_AUTHORITY_POLICY_LEGACY_RELEASE_ID_RE = re.compile(r"^APR-[0-9a-f]{64}$")


def _validate_authority_policy_legacy_activation_id(value: str, field_name: str) -> str:
    # The shipping legacy activation id is ``APA-`` + sha256 hex, but the
    # persisted activation model does not force that exact suffix. Stay strict
    # and bounded while accepting every already-persisted legacy activation id.
    if not _AUTHORITY_POLICY_LEGACY_ACTIVATION_ID_RE.fullmatch(value):
        raise ValueError(f"{field_name} must be a bounded APA- legacy activation id")
    return value


def _validate_authority_policy_legacy_release_id(value: str, field_name: str) -> str:
    if not _AUTHORITY_POLICY_LEGACY_RELEASE_ID_RE.fullmatch(value):
        raise ValueError(f"{field_name} must be APR- followed by 64 lowercase hex")
    return value


def authority_policy_legacy_activation_request_preimage(
    *, action: str, expected_selector_id: str | None, kind: str, release_id: str,
    request_id: str, team: str,
) -> dict[str, object]:
    return {
        "action": action, "expected_selector_id": expected_selector_id, "kind": kind,
        "release_id": release_id, "request_id": request_id, "team": team,
    }


def authority_policy_legacy_reactivation_request_preimage(
    *, action: str, activation_id: str, expected_selector_id: str | None, kind: str,
    request_id: str, team: str,
) -> dict[str, object]:
    return {
        "action": action, "activation_id": activation_id,
        "expected_selector_id": expected_selector_id, "kind": kind,
        "request_id": request_id, "team": team,
    }


class AuthorityPolicyLegacyActivationRequest(BaseModel):
    """Strict selector-aware request to activate a NEW legacy v1 release.

    The observed selector is the CAS base; the legacy family epoch is allocated
    server-side from the sealed legacy stream, never supplied by the client.
    """
    model_config = {"extra": "forbid", "strict": True, "frozen": True}

    team: Literal[AUTHORITY_POLICY_V2_TEAM]
    release_id: StrictStr
    request_id: StrictStr
    expected_selector_id: StrictStr | None
    action: Literal["activate"] = "activate"

    @field_validator("release_id")
    @classmethod
    def _legacy_activate_release_id(cls, value: str, info) -> str:
        return _validate_authority_policy_legacy_release_id(value, info.field_name)

    @field_validator("request_id")
    @classmethod
    def _legacy_activate_request_id(cls, value: str, info) -> str:
        return _validate_authority_policy_v2_request_id(value, info.field_name)

    @field_validator("expected_selector_id")
    @classmethod
    def _legacy_activate_selector_ref(cls, value: str | None, info) -> str | None:
        return _validate_authority_policy_v2_selector_ref(value, info.field_name)

    def request_preimage(self) -> dict[str, object]:
        return authority_policy_legacy_activation_request_preimage(
            action=self.action, expected_selector_id=self.expected_selector_id,
            kind="legacy_activate", release_id=self.release_id,
            request_id=self.request_id, team=self.team,
        )

    def request_digest(self) -> str:
        return authority_policy_v2_sha256(self.request_preimage())


class AuthorityPolicyLegacyReactivationRequest(BaseModel):
    """Strict selector-aware request to re-select an already selected v1 activation.

    The target is the exact authenticated legacy activation; the original
    legacy activation row is never appended, renumbered or resealed.
    """
    model_config = {"extra": "forbid", "strict": True, "frozen": True}

    team: Literal[AUTHORITY_POLICY_V2_TEAM]
    activation_id: StrictStr
    request_id: StrictStr
    expected_selector_id: StrictStr | None
    action: Literal["reactivate_rollback"] = "reactivate_rollback"

    @field_validator("activation_id")
    @classmethod
    def _legacy_reactivate_activation_id(cls, value: str, info) -> str:
        return _validate_authority_policy_legacy_activation_id(value, info.field_name)

    @field_validator("request_id")
    @classmethod
    def _legacy_reactivate_request_id(cls, value: str, info) -> str:
        return _validate_authority_policy_v2_request_id(value, info.field_name)

    @field_validator("expected_selector_id")
    @classmethod
    def _legacy_reactivate_selector_ref(cls, value: str | None, info) -> str | None:
        return _validate_authority_policy_v2_selector_ref(value, info.field_name)

    def request_preimage(self) -> dict[str, object]:
        return authority_policy_legacy_reactivation_request_preimage(
            action=self.action, activation_id=self.activation_id,
            expected_selector_id=self.expected_selector_id,
            kind="legacy_reactivate", request_id=self.request_id, team=self.team,
        )

    def request_digest(self) -> str:
        return authority_policy_v2_sha256(self.request_preimage())


class AuthorityPolicyLegacyControlReceipt(BaseModel):
    """Deterministic receipt for a selector-aware legacy v1 selection."""
    model_config = {"extra": "forbid", "strict": True, "frozen": True}

    team: Literal[AUTHORITY_POLICY_V2_TEAM]
    kind: Literal["legacy_activate", "legacy_reactivate_rollback"]
    request_id: StrictStr
    request_digest: StrictStr
    release_id: StrictStr
    policy_digest: StrictStr
    release_version: StrictInt = Field(ge=1)
    activation_id: StrictStr
    activation_digest: StrictStr
    selector_id: StrictStr
    selector_epoch: StrictInt = Field(ge=1, le=2147483647)
    previous_selector_id: StrictStr | None = None
    action: Literal["bootstrap", "activate", "reactivate_rollback"]
    created_at: StrictStr

    @field_validator("request_digest", "policy_digest", "activation_digest")
    @classmethod
    def _legacy_receipt_digests(cls, value: str, info) -> str:
        return _validate_authority_policy_v2_digest(value, info.field_name)

    @field_validator("request_id")
    @classmethod
    def _legacy_receipt_request_id(cls, value: str, info) -> str:
        return _validate_authority_policy_v2_request_id(value, info.field_name)

    @field_validator("release_id")
    @classmethod
    def _legacy_receipt_release_id(cls, value: str, info) -> str:
        return _validate_authority_policy_legacy_release_id(value, info.field_name)

    @field_validator("activation_id")
    @classmethod
    def _legacy_receipt_activation_id(cls, value: str, info) -> str:
        return _validate_authority_policy_legacy_activation_id(value, info.field_name)

    @field_validator("selector_id", "previous_selector_id")
    @classmethod
    def _legacy_receipt_selectors(cls, value: str | None, info) -> str | None:
        return _validate_authority_policy_v2_selector_ref(value, info.field_name)

    @model_validator(mode="after")
    def _legacy_receipt_bound_ids(self) -> AuthorityPolicyLegacyControlReceipt:
        if self.release_id != f"APR-{self.policy_digest}":
            raise ValueError("release_id must contain policy_digest")
        if self.kind == "legacy_activate":
            if self.action not in ("bootstrap", "activate"):
                raise ValueError("legacy activate receipt action is invalid")
        elif self.action != "reactivate_rollback":
            raise ValueError("legacy reactivate receipt action is invalid")
        return self

    def canonical_json(self) -> str:
        return authority_policy_v2_canonical_json_bytes(self.model_dump(mode="json")).decode("utf-8")


def authority_policy_v2_create_request_preimage(
    *, based_on_selector_id: str | None, kind: str, policy_id: str,
    request_id: str, team: str, title: str, what_to_escalate: str,
    what_not_to_escalate: str,
) -> dict[str, object]:
    return {
        "based_on_selector_id": based_on_selector_id, "kind": kind,
        "policy_id": policy_id, "request_id": request_id, "team": team,
        "title": title, "what_not_to_escalate": what_not_to_escalate,
        "what_to_escalate": what_to_escalate,
    }


def authority_policy_v2_activation_request_preimage(
    *, action: str, expected_selector_id: str | None, kind: str, release_id: str,
    request_id: str, team: str,
) -> dict[str, object]:
    return {
        "action": action, "expected_selector_id": expected_selector_id, "kind": kind,
        "release_id": release_id, "request_id": request_id, "team": team,
    }


def authority_policy_v2_selector_preimage(
    *, activation_id: str, family: str, previous_selector_id: str | None,
    selector_epoch: int, team: str,
) -> dict[str, object]:
    return {
        "activation_id": activation_id, "family": family,
        "previous_selector_id": previous_selector_id,
        "selector_epoch": selector_epoch, "team": team,
    }


def authority_policy_v2_selector_id(**values: object) -> str:
    return f"APS-{authority_policy_v2_sha256(authority_policy_v2_selector_preimage(**values))}"


def authority_policy_v2_initializer_selector_preimage(
    *, family: str, legacy_activation_id: str | None, selector_epoch: int, team: str,
) -> dict[str, object]:
    """Frozen R2 initializer preimage (empty family epoch 0 or legacy_v1 epoch 1)."""
    return {
        "family": family,
        "kind": "selector_initialize",
        "legacy_activation_id": legacy_activation_id,
        "selector_epoch": selector_epoch,
        "team": team,
    }


def authority_policy_v2_initializer_selector_id(**values: object) -> str:
    return f"APS-{authority_policy_v2_sha256(authority_policy_v2_initializer_selector_preimage(**values))}"


def authority_policy_v2_causal_result_digest(result_id: int) -> str:
    """Immutable row-identity digest; never a hash of the result body."""
    return authority_policy_v2_sha256({"kind": "task_result", "result_id": result_id})


def authority_policy_v2_candidate_id(claim_key: str) -> str:
    return f"APV2C-{claim_key}"


def authority_policy_v2_envelope_id(candidate_id: str) -> str:
    return f"APV2E-{authority_policy_v2_sha256({'candidate_id': candidate_id, 'kind': 'continue_envelope'})}"


def authority_policy_v2_attempt_id(
    *, manager_agent: str, manager_session_id: str, result_id: int,
    root_task_id: str, team: str,
) -> str:
    return "APV2R-" + authority_policy_v2_sha256({
        "manager_agent": manager_agent, "manager_session_id": manager_session_id,
        "result_id": result_id, "root_task_id": root_task_id, "team": team,
    })


def authority_policy_v2_notification_id(envelope_id: str) -> str:
    return f"APV2N-{authority_policy_v2_sha256({'envelope_id': envelope_id, 'kind': 'continuation_notification'})}"


class TaskStep(BaseModel):
    agent: str
    action: str
    description: str


class StepRecord(BaseModel):
    """Record of a completed orchestration step, shown to the task owner as history."""
    step_number: int
    agent: str
    action: str
    result_summary: str
    success: bool


class DreamStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    TIMEOUT = "timeout"
    SKIPPED = "skipped"


class DreamRecord(BaseModel):
    id: str
    agent_name: str
    local_date: str
    scheduled_for: datetime
    window_end: datetime
    window_start: datetime | None = None
    started_at: datetime | None = None
    ended_at: datetime | None = None
    status: DreamStatus = DreamStatus.PENDING
    summary: str | None = None
    transcript_path: str | None = None
    new_learnings_count: int = 0
    kb_candidate_count: int = 0
    founder_thread_id: str | None = None
    session_id: str | None = None
    error: str | None = None
    created_at: datetime = Field(default_factory=_now)


class WorkHourMode(StrEnum):
    WINDOWED = "windowed"
    CONTINUOUS = "continuous"


class WorkHourStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    TIMEOUT = "timeout"
    SKIPPED = "skipped"


class WorkHourRecord(BaseModel):
    id: str
    agent_name: str
    local_date: str
    slot: str
    mode: WorkHourMode
    scheduled_for: datetime
    started_at: datetime | None = None
    ended_at: datetime | None = None
    status: WorkHourStatus = WorkHourStatus.PENDING
    routine_count: int = 0
    dropped_count: int = 0
    spawned_task_ids: list[str] = Field(default_factory=list)
    spawned_task_count: int = 0
    summary: str | None = None
    transcript_path: str | None = None
    session_id: str | None = None
    error: str | None = None
    created_at: datetime = Field(default_factory=_now)


class DreamKbCandidate(BaseModel):
    id: int | None = None
    dream_id: str
    agent_name: str
    slug: str
    title: str
    topic: str
    rationale: str
    body_markdown: str
    status: Literal["pending", "promoted", "rejected", "superseded"] = "pending"
    promoted_kb_slug: str | None = None
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)


class TokenUsage(BaseModel):
    """Per-session token usage, unified across executors.

    All fields nullable so we can write a row even when parsing partially
    succeeds (per spec §4.3). `total` deliberately excludes cache reads —
    cache hits are an effectiveness signal, not new consumption.
    """
    input_tokens: int | None = None
    output_tokens: int | None = None
    cache_read_tokens: int | None = None
    cache_creation_tokens: int | None = None
    reasoning_tokens: int | None = None
    model: str | None = None
    usage_raw_json: str | None = None

    @property
    def total(self) -> int:
        return (self.input_tokens or 0) + (self.output_tokens or 0) + (self.reasoning_tokens or 0)


class ThreadStatus(StrEnum):
    OPEN = "open"
    ARCHIVED = "archived"


class ThreadMessageKind(StrEnum):
    MESSAGE = "message"
    DECLINE = "decline"
    SYSTEM = "system"


class ThreadInvocationStatus(StrEnum):
    PENDING = "pending"
    CONSUMED = "consumed"
    DECLINED = "declined"
    TIMEOUT = "timeout"
    FAILED = "failed"


class ThreadInvocationPurpose(StrEnum):
    REPLY = "reply"
    BOOTSTRAP = "bootstrap"
    TASK_FOLLOWUP = "task_followup"


class ThreadRecord(BaseModel):
    id: str
    subject: str
    status: ThreadStatus = ThreadStatus.OPEN
    started_at: datetime = Field(default_factory=_now)
    archived_at: datetime | None = None
    forwarded_from_id: str | None = None
    forwarded_from_kind: str | None = None  # 'thread'
    turn_cap: int = 500
    turns_used: int = 0
    summary: str | None = None
    transcript_path: str | None = None
    composed_by: str = "founder"
    composed_from_task_id: str | None = None
    composed_from_dream_id: str | None = None
    last_speaker: str | None = None
    # Founder-workspace presentation state (THR-209): non-None when the thread
    # is pinned by the founder. Pinning changes display only — never identity,
    # participants, routing, unread state, lifecycle, or activity timestamps.
    pinned_at: datetime | None = None
    # Most recent message created_at (derived; NULL for threads without
    # messages). Informational on the wire (THR-209 msg 9: pinned ranking
    # uses the immutable numeric thread ID, not activity).
    last_activity_at: datetime | None = None


class ThreadParticipant(BaseModel):
    thread_id: str
    agent_name: str
    added_at: datetime = Field(default_factory=_now)
    added_by: str = "founder"


class ThreadAttachment(BaseModel):
    artifact_name: str
    display_name: str
    size_bytes: int | None = None
    content_type: str | None = None
    uploaded_by: str
    # Thread-scoped attachment id (mutually exclusive with artifact_name when non-None).
    # When set, the attachment is a thread-scoped file stored in the thread's
    # private attachment store rather than the org-shared ArtifactStore.
    thread_attachment_id: str | None = None


class ThreadScopedAttachment(BaseModel):
    """A file stored in a thread's private attachment store."""
    attachment_id: str
    thread_id: str
    display_name: str
    size_bytes: int | None = None
    content_type: str | None = None
    uploaded_by: str
    created_at: str = ""


class TaskAttachmentRecord(BaseModel):
    """A file attached to a task, stored in the private task-attachment store.

    Attachments are owned by the task they were uploaded to (owning ancestor).
    Descendant tasks resolve them via parent_task_id walk at spawn time.

    legacy_status is None for normal rows; non-None (e.g. 'duplicate_v1')
    marks rows that were preserved from a legacy pre-constraint schema where
    duplicate storage_key values were permitted. These rows are readable but
    their keys cannot be newly claimed.
    """
    id: int | None = None
    task_id: str
    ordinal: int
    storage_key: str
    display_name: str
    size_bytes: int | None = None
    content_type: str | None = None
    uploaded_by: str
    created_at: str = ""
    legacy_status: str | None = None


class ThreadMessage(BaseModel):
    id: int | None = None
    thread_id: str
    seq: int
    speaker: str
    kind: ThreadMessageKind
    body_markdown: str | None = None
    decline_reason: str | None = None
    system_payload: dict | None = None
    attachments: list[ThreadAttachment] = Field(default_factory=list)
    # Phase-2 mention routing (THR-198): canonical valid-participant mentions
    # derived server-side from body_markdown at write time (stored as
    # mentions_json). Empty list when derived with no valid mentions; NULL
    # rows (system/decline + pre-change history) read back as empty list.
    mentions: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=_now)


class ResponderStatusEntry(BaseModel):
    agent_name: str
    purpose: ThreadInvocationPurpose
    status: Literal["queued", "working", "replied", "declined", "failed"]
    responded_at: str | None
    started_at: str | None = None
    decline_reason: str | None = None
    category: Literal["declined", "no_callback", "no_callback_after_reprompt", "infra_fail"] | None = None


class ThreadInvocation(BaseModel):
    id: int | None = None
    thread_id: str
    agent_name: str
    invocation_token: str
    triggering_seq: int
    purpose: ThreadInvocationPurpose
    status: ThreadInvocationStatus = ThreadInvocationStatus.PENDING
    enqueued_at: datetime = Field(default_factory=_now)
    started_at: datetime | None = None
    consumed_at: datetime | None = None
    session_id: str | None = None
    dispatched_task_id: str | None = None
    decline_reason: str | None = None


class ThreadReplyDeliveryState(BaseModel):
    """Durable per-(thread_id, agent_name) conversational REPLY delivery state.

    GitHub #688 Phase 1 Slice A. This is the provider-neutral required-delivery
    contract for coalesced conversational reply wakes. One row per pair:
    at most one queued and one running REPLY slot; both tokens (when present)
    reference same-pair REPLY invocations.

    ``acknowledged_through_seq`` is the highest transcript sequence
    intentionally acknowledged as presented/handled; ``required_through_seq``
    is the highest conversational sequence that must still be offered. The
    queued/running tokens occupy the single unstarted/started REPLY slots.
    ``running_from_seq``/``running_through_seq`` are the immutable prompt
    receipt captured at claim time.

    ``last_terminal_reason``/``last_terminal_at`` carry the diagnostic data
    Slice B uses to project ``retry_required`` and to audit settlement.
    """
    thread_id: str
    agent_name: str
    acknowledged_through_seq: int = 0
    required_through_seq: int = 0
    queued_invocation_token: str | None = None
    running_invocation_token: str | None = None
    running_from_seq: int | None = None
    running_through_seq: int | None = None
    last_terminal_reason: str | None = None
    last_terminal_at: str | None = None
    updated_at: str = ""


class ThreadReplyBreakerEpisode(BaseModel):
    """Durable provider-failure breaker for one thread/agent/executor identity."""
    thread_id: str
    agent_name: str
    executor_key: str
    episode_id: str
    state: Literal["closed", "open", "probe"]
    consecutive_failures: int = Field(ge=0)
    opened_at: str | None = None
    cooldown_until: str | None = None
    probe_lease_id: str | None = None
    last_failure_category: str | None = None
    updated_at: str


class ThreadReplyRecoveryEntry(BaseModel):
    """One runnable token returned by the durable reply-delivery recovery pass.

    ``kind`` distinguishes a retained queued wake from a replacement queued
    wake minted for an interrupted running attempt.
    """
    thread_id: str
    agent_name: str
    invocation_token: str
    kind: Literal[
        "retained_queued", "replacement_queued", "deferred_catchup",
        "breaker_probe",
    ]


class ThreadReplyArrival(BaseModel):
    """One recipient's delivery result from a conversational arrival.

    GitHub #688 Phase 1 Slice B. ``invocation_token`` is non-None only when
    this arrival minted a NEW queued REPLY (no queued/running ownership
    existed); ``coalesced=True`` means the arrival merely raised
    ``required_through_seq`` on an existing wake. ``from_seq``/``through_seq``
    carry the delivery range the pair's single wake now covers (diagnostic).
    """
    agent_name: str
    invocation_token: str | None
    coalesced: bool
    from_seq: int
    through_seq: int


class ThreadReplyClaim(BaseModel):
    """Successful queued→running CAS result for a conversational REPLY.

    ``running_from_seq``/``running_through_seq`` are the immutable inclusive
    prompt receipt snapshotted at claim time; they never change for the life
    of the running attempt even if later arrivals raise ``required_through_seq``.
    """
    thread_id: str
    agent_name: str
    invocation_token: str
    acknowledged_through_seq: int
    required_through_seq: int
    running_from_seq: int
    running_through_seq: int


class ThreadReplySettlement(BaseModel):
    """Result of settling a conversational REPLY terminal path.

    ``follow_on_token`` is at most one newly-minted queued REPLY covering
    arrivals strictly after the immutable running range — the reply/decline
    follow-on, or the owed post-slot catch-up of a released deferral whose
    old slot never covered the released range (TASK-6057: minted on
    failed/timeout too, never a plain immediate retry).
    ``retry_required`` is the residual-obligation diagnostic: True only when
    ``required_through_seq`` still exceeds the acknowledged watermark AND no
    follow-on wake was minted to carry it (failure/timeout without an owed
    catch-up).

    TASK-5966 strict mention-led exchange: ``exchange_held`` is True when the
    pair was a held (deferred) member of an open exchange and its follow-on
    wake was therefore SUPPRESSED — the unacknowledged range is intentionally
    deferred to the exchange's single range-covering catch-up at closure, and
    ``retry_required`` stays False (this is not a retry condition).
    """
    thread_id: str
    agent_name: str
    outcome: Literal["reply", "decline", "failed", "timeout"]
    acknowledged_through_seq: int
    required_through_seq: int
    retry_required: bool
    follow_on_token: str | None
    exchange_held: bool = False


class ThreadReplyExchangeProjection(BaseModel):
    """Exchange-level wire projection for a thread (server contract,
    TASK-5966). Derived from ``thread_reply_exchange`` only — never
    fabricated. The most recent exchange row (any state) is returned so the
    UI/CLI can truthfully disclose an open exchange's bounds and deferred
    set; a thread that never opened an exchange has none.
    """
    thread_id: str
    exchange_id: int
    state: Literal["open", "released", "suppressed"]
    open_seq: int
    close_seq: int
    opened_at: str
    last_activity_at: str
    closed_at: str | None
    close_reason: str | None
    deferred_count: int


class ReplyDeliveryProjection(BaseModel):
    """Pair-level reply-delivery wire projection (server contract, Slice B).

    Derived from ``thread_reply_delivery_state``, never fabricated from
    per-message invocation rows. ``state`` truthfully distinguishes the four
    live obligations:
      * ``queued`` — one unstarted coalesced REPLY wake (token set, not started)
      * ``running`` — one claimed in-flight REPLY (immutable range)
      * ``held`` — an unacknowledged range intentionally deferred by both an
        OPEN reply exchange and this participant's matching HELD deferral row
      * ``retry_required`` — unacknowledged range with no active wake; the
        next conversational arrival mints the single covering retry
    A fully-settled pair (nothing queued/running/required) is omitted from the
    live projection; terminal history stays on the per-message responder strips.
    ``coalesced_message_count`` is the number of transcript rows the wake's
    range covers (computed in the store, not inferred by numeric subtraction).
    """
    agent_name: str
    state: Literal["queued", "running", "held", "retry_required"]
    from_seq: int
    through_seq: int
    coalesced_message_count: int
    started_at: str | None
    updated_at: str | None
    last_terminal_reason: str | None
    current_failure_category: Literal[
        "no_callback", "no_callback_after_reprompt", "infra_fail"
    ] | None = None


class JobStatus(StrEnum):
    PENDING   = "pending"
    REJECTED  = "rejected"
    RUNNING   = "running"
    COMPLETED = "completed"
    FAILED    = "failed"


class JobInterpreter(StrEnum):
    BASH    = "bash"
    SH      = "sh"
    ZSH     = "zsh"
    PYTHON3 = "python3"


class JobRecord(BaseModel):
    id:               str
    # Scope id of the submission context. Always a TASK-NNN id for task-originated
    # jobs. Keeping one column avoids plumbing a ``scope_id`` everywhere it's
    # already in use.
    task_id:          str
    agent_name:       str
    title:            str
    rationale:        str
    script_text:      str
    interpreter:      JobInterpreter
    cwd_hint:         str | None = None
    status:           JobStatus = JobStatus.PENDING
    exit_code:        int | None = None
    stdout_head:      str | None = None
    stderr_head:      str | None = None
    stdout_path:      str | None = None
    stderr_path:      str | None = None
    duration_ms:      int | None = None
    started_at:       str | None = None
    finished_at:      str | None = None
    reviewed_at:      str | None = None
    reviewed_by:      str | None = None
    reject_reason:    str | None = None
    cwd_resolved:     str | None = None
    max_runtime_seconds: int | None = None
    # Per-stream output-size cap (bytes). Either stdout OR stderr crossing
    # this triggers SIGKILL with reason="output_cap". 50 MiB default matches
    # the column default in the jobs table schema.
    max_output_bytes: int | None = 52428800
    # Founder-review gate. True → row inserted as `pending`, awaits explicit
    # /run. False (default) → auto-run inline at /submit.
    review_required:  bool = False
    # Long-running flag. True → no default runtime cap (unbounded unless an
    # explicit max_runtime_seconds is provided), killed only by /stop or the
    # task-terminal kill hook. False (default) → 300s default cap when no
    # explicit override is provided.
    persistent:       bool = False
    # Terminal-status reason — populated by the runner when status='failed'.
    # Examples: "timeout", "output_cap", "founder_stop", "agent_stop",
    # "task_ended", "spawn_failed", "internal_error", "daemon_crash".
    # NULL when status='completed' or the job hasn't reached terminal yet.
    reason:           str | None = None
    created_at:       str


class ScheduleKind(StrEnum):
    """THR-105 schedule cadence kinds."""
    ONE_SHOT = "one_shot"
    WEEKLY = "weekly"
    RECURRING = "recurring"


class ScheduleStatus(StrEnum):
    """THR-105: Schedule lifecycle states.  armed → firing → fired (one-shot terminal)
    or armed → firing → armed (weekly cycle).  paused / cancelled / expired / failed /
    timeout are terminal or suspended states."""
    ARMED = "armed"
    FIRING = "firing"
    FIRED = "fired"
    PAUSED = "paused"
    CANCELLED = "cancelled"
    EXPIRED = "expired"
    FAILED = "failed"
    TIMEOUT = "timeout"


class ScheduleRecord(BaseModel):
    """THR-105 Phase 1: a persisted, agent-owned scheduled work commitment.

    Internal primitive is ``Schedule``; user-facing label is "Todos".
    See docs/superpowers/specs/2026-07-18-agent-scheduled-work-design.md
    and docs/product/prds/2026-07-19-agent-todos.md.
    """
    id: str
    agent_name: str
    team: str = "engineering"
    kind: ScheduleKind
    fire_at: datetime
    recurrence: dict | None = None
    timezone: str = "UTC"
    normalized_brief: str
    source_instruction: str
    status: ScheduleStatus = ScheduleStatus.ARMED
    active: int = 1
    expires_at: datetime | None = None
    indefinite: int = 0
    spawned_task_ids: list[str] = Field(default_factory=list)
    last_fired_at: datetime | None = None
    fire_count: int = 0
    end_reason: str | None = None
    # Fields needed by the later runner (Phase 2+), consistent with WorkHourRecord
    session_id: str | None = None
    error: str | None = None
    transcript_path: str | None = None
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)


# ── THR-181 Track A: durable authority candidate/evaluation/audit foundation ──
#
# Slice 1 supplied the isolated, additive persistence foundation; the
# pre-escalation authority hook (runtime/orchestrator/authority.py) now runs
# one audited LLM evaluation before a manager root's proposed escalation is
# committed (Engineering policy v1), consuming these records via the
# database.py persistence API. The vocabularies below are closed StrEnums
# mirrored by SQLite CHECK constraints. Every prose-bearing field is stored
# as a *digest* (never the raw content); raw bearer/provider credentials,
# task prose, and unredacted model exchanges are never accepted or
# persisted.


class AuthorityLifecycleState(StrEnum):
    """Controlled candidate lifecycle. Mirrored by a SQLite CHECK constraint."""
    CREATED = "created"          # claimed, not yet evaluated
    EVALUATED = "evaluated"      # a single evaluation disposition recorded
    CONSUMED = "consumed"        # disposition consumed exactly once (hook CAS)


class AuthorityDisposition(StrEnum):
    """The primary controlled outcome recorded by the (later) evaluator slice."""
    CONTINUE_SAME_ROOT = "continue_same_root"
    ESCALATE = "escalate"
    NOT_APPLICABLE = "not_applicable"
    EVALUATOR_ERROR = "evaluator_error"


class AuthorityDispositionCode(StrEnum):
    """Fine-grained fail-closed reason codes. Superset of AuthorityDisposition."""
    CONTINUE_SAME_ROOT = "continue_same_root"
    ESCALATE = "escalate"
    NOT_APPLICABLE = "not_applicable"
    EVALUATOR_ERROR = "evaluator_error"
    LOW_CONFIDENCE = "low_confidence"
    TIMEOUT = "timeout"
    MALFORMED_OUTPUT = "malformed_output"
    INJECTION_GUARD = "injection_guard"
    AUDIT_FAILURE = "audit_failure"


class AuthorityRetentionClass(StrEnum):
    """How long a snapshot/response digest is retained. Mirrored by CHECK."""
    DIGEST_ONLY = "digest_only"
    SHADOW = "shadow"
    INDEFINITE = "indefinite"


class AuthorityRedactionClass(StrEnum):
    """Whether content was redacted before it was digested. Mirrored by CHECK."""
    NONE = "none"
    REDACTED = "redacted"


class AuthorityAuditEventType(StrEnum):
    """Controlled append-only authority audit event vocabulary."""
    CANDIDATE_CLAIMED = "candidate_claimed"
    CANDIDATE_CLAIM_LOST = "candidate_claim_lost"
    EVALUATION_RECORDED = "evaluation_recorded"
    CANDIDATE_CONSUMED = "candidate_consumed"


# Bounded digest/version validators. Shared by the closed Pydantic records
# below AND the ``Database`` writer boundary (database.py) so the two surfaces
# cannot drift. They reject task prose, raw model exchanges, and bearer/provider
# credentials smuggled into a field that must only ever hold a hex digest or a
# short version token — the writer refuses to persist (never silently redacts).

_HEX_DIGITS = frozenset("0123456789abcdefABCDEF")

_CREDENTIAL_MARKERS = (
    "bearer",
    "authorization",
    "api_key",
    "apikey",
    "password",
    "credential",
    "token=",
    "sk-",
    "private_key",
    "client_secret",
)


def _reject_credential_like(value: str, field: str) -> str:
    lowered = value.lower()
    for marker in _CREDENTIAL_MARKERS:
        if marker in lowered:
            raise ValueError(
                f"{field} appears to carry a credential-like token ({marker!r}); "
                "refusing to persist it"
            )
    return value


def validate_authority_digest(value: str, field: str) -> str:
    """Validate a digest field: bounded hex only.

    Rejects task prose, raw model exchanges (JSON), and bearer/provider
    credentials — none of which are valid hex — rather than silently storing
    or redacting them.
    """
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field} must be a non-empty string")
    if not (32 <= len(value) <= 128) or not set(value) <= _HEX_DIGITS:
        raise ValueError(
            f"{field} must be a bounded hex digest (32-128 hex chars); "
            "refusing to persist prose, credentials, or raw model-exchange content"
        )
    return value


def validate_authority_version(value: str, field: str) -> str:
    """Validate a version token: short, non-blank, credential-free."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-blank string")
    if len(value) > 64:
        raise ValueError(f"{field} must be at most 64 characters")
    return _reject_credential_like(value, field)


class AuthorityFenceCode(StrEnum):
    """Closed vocabulary of mechanical fence outcome codes.

    Only these codes may be recorded in an ``AuthorityFenceResult``. Unknown
    codes are rejected at the writer boundary (and by the model's closed
    validation) rather than persisted.
    """
    CANCELLED = "cancelled"
    STALE = "stale"
    BUDGET_EXCEEDED = "budget_exceeded"
    TIMEOUT = "timeout"
    INJECTION = "injection"
    MALFORMED = "malformed"


class AuthorityFenceResult(BaseModel):
    """One mechanical fence outcome: a boolean plus an optional closed code.

    Structured, never prose — the fence name is the dict key, and this model
    is the value. Extra keys and unknown codes are rejected; evaluator
    prose/attestation text must never be stored here.
    """
    model_config = {"extra": "forbid"}

    passed: StrictBool
    code: AuthorityFenceCode | None = None


class AuthorityAuditPayload(BaseModel):
    """Bounded, closed audit-event payload.

    Only digest / redaction / version / classification fields are permitted.
    Never prose, credentials, raw model exchanges, or arbitrary evaluator
    responses. Unknown keys are rejected.
    """
    model_config = {"extra": "forbid"}

    disposition: AuthorityDisposition | None = None
    disposition_code: AuthorityDispositionCode | None = None
    retention_class: AuthorityRetentionClass | None = None
    redaction_class: AuthorityRedactionClass | None = None
    digest: StrictStr | None = None
    version: StrictStr | None = None

    @field_validator("digest")
    @classmethod
    def _digest_is_bounded_hex(cls, value, info):
        if value is None:
            return None
        return validate_authority_digest(value, f"payload.{info.field_name}")

    @field_validator("version")
    @classmethod
    def _version_is_bounded(cls, value, info):
        if value is None:
            return None
        return validate_authority_version(value, f"payload.{info.field_name}")


class AuthorityCandidate(BaseModel):
    """Immutable identity of one pre-escalation authority candidate.

    ``claim_key`` is the deterministic sha256 digest of the
    root/session/causal-event/policy-prompt-model tuple — the CAS key that
    guarantees at most one durable candidate per tuple. Every digest field is
    validated as bounded hex; unknown keys are rejected.
    """
    model_config = {"extra": "forbid"}

    id: str
    claim_key: str
    root_task_id: str
    team: str
    manager_agent: str
    manager_session_id: str
    causal_event_id: str
    causal_event_digest: str
    causal_result_id: str | None = None
    policy_id: str
    policy_version: str
    policy_digest: str
    prompt_id: str
    prompt_version: str
    prompt_digest: str
    model_id: str
    model_version: str
    model_digest: str
    snapshot_digest: str
    snapshot_retention_class: AuthorityRetentionClass = AuthorityRetentionClass.DIGEST_ONLY
    snapshot_redaction_class: AuthorityRedactionClass = AuthorityRedactionClass.REDACTED
    fence_results: dict[str, AuthorityFenceResult] | None = None
    disposition: AuthorityDisposition | None = None
    lifecycle_state: AuthorityLifecycleState = AuthorityLifecycleState.CREATED
    consumed_at: datetime | None = None
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)

    @field_validator(
        "claim_key",
        "causal_event_digest",
        "policy_digest",
        "prompt_digest",
        "model_digest",
        "snapshot_digest",
    )
    @classmethod
    def _digests_are_bounded_hex(cls, value, info):
        return validate_authority_digest(value, info.field_name)

    @field_validator("policy_version", "prompt_version", "model_version")
    @classmethod
    def _versions_are_bounded(cls, value, info):
        return validate_authority_version(value, info.field_name)


class AuthorityPolicyRelease(BaseModel):
    """One immutable authored team-policy release.

    The digest covers exactly policy_id, version, team, title,
    normative_text, clauses, and continuation_phrase. Release ancestry,
    actor attribution, and creation time are receipts and are deliberately
    outside that semantic identity.
    """
    model_config = {"extra": "forbid", "frozen": True}

    id: str
    team: str
    policy_id: str
    version: int = Field(gt=0)
    title: str
    normative_text: str
    clauses_json: str
    continuation_phrase: str
    canonical_payload_json: str
    policy_digest: str
    based_on_release_id: str | None = None
    actor_kind: Literal["shared_local_operator_credential"]
    created_at: datetime = Field(default_factory=_now)

    @model_validator(mode="before")
    @classmethod
    def _derive_and_validate_semantic_identity(cls, raw):
        if not isinstance(raw, dict):
            return raw
        values = dict(raw)
        try:
            clauses = json.loads(values["clauses_json"])
        except (KeyError, TypeError, json.JSONDecodeError) as exc:
            raise ValueError("clauses_json must be canonical JSON") from exc
        if not isinstance(clauses, list):
            raise ValueError("clauses_json must be a JSON array")
        clause_keys = {"id", "category", "condition", "action"}
        seen: set[str] = set()
        for clause in clauses:
            if not isinstance(clause, dict) or set(clause) != clause_keys:
                raise ValueError("each policy clause must have the exact closed schema")
            if any(not isinstance(clause[key], str) or not clause[key].strip() for key in clause_keys):
                raise ValueError("policy clause fields must be nonblank strings")
            if clause["action"] not in {"escalate_to_founder", "continue_same_root"}:
                raise ValueError("policy clause action is outside the closed vocabulary")
            if clause["id"] in seen:
                raise ValueError("policy clause ids must be unique")
            seen.add(clause["id"])
        canonical_clauses = json.dumps(
            clauses, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        )
        if values["clauses_json"] != canonical_clauses:
            raise ValueError("clauses_json is not canonical")
        payload = {
            "policy_id": values.get("policy_id"), "version": values.get("version"),
            "team": values.get("team"), "title": values.get("title"),
            "normative_text": values.get("normative_text"), "clauses": clauses,
            "continuation_phrase": values.get("continuation_phrase"),
        }
        canonical_payload = json.dumps(
            payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        )
        digest = hashlib.sha256(canonical_payload.encode("utf-8")).hexdigest()
        expected_id = f"APR-{digest}"
        for field, expected in (
            ("canonical_payload_json", canonical_payload),
            ("policy_digest", digest), ("id", expected_id),
        ):
            supplied = values.get(field)
            if supplied is not None and supplied != expected:
                raise ValueError(f"{field} does not match canonical policy semantics")
            values[field] = expected
        return values

    @field_validator("policy_digest")
    @classmethod
    def _policy_digest_is_bounded_hex(cls, value, info):
        return validate_authority_digest(value, info.field_name)


class _AuthorityPolicyActivationDraft(BaseModel):
    """Trusted construction input; never accepted as a persisted receipt."""
    model_config = {"extra": "forbid", "frozen": True}

    id: str
    team: str
    epoch: int = Field(gt=0)
    release_id: str
    previous_activation_id: str | None = None
    expected_previous_epoch: int | None = Field(default=None, ge=0)
    action: Literal["activate", "reactivate_rollback", "bootstrap"]
    actor_kind: Literal["shared_local_operator_credential"]
    request_id: str
    request_digest: str
    created_at: datetime = Field(default_factory=_now)

    @field_validator("request_digest")
    @classmethod
    def _request_digest_is_bounded_hex(cls, value, info):
        return validate_authority_digest(value, info.field_name)


def _authority_activation_digest(values: dict[str, object]) -> str:
    """Derive a seal from an already validated closed canonical payload."""
    payload = {
        field: values[field]
        for field in (
            "id", "team", "epoch", "release_id", "previous_activation_id",
            "expected_previous_epoch", "action", "actor_kind", "request_id",
            "request_digest", "created_at",
        )
    }
    if isinstance(payload["created_at"], datetime):
        payload["created_at"] = payload["created_at"].isoformat()
    canonical = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class AuthorityPolicyActivation(_AuthorityPolicyActivationDraft):
    """Immutable receipt whose mandatory seal is never derived at ingress."""

    activation_digest: str

    @classmethod
    def create(cls, **values: object) -> AuthorityPolicyActivation:
        """Trusted fresh-activation factory; the sole seal-derivation boundary."""
        draft = _AuthorityPolicyActivationDraft.model_validate(values)
        snapshot = draft.model_dump(mode="python", round_trip=True, warnings=False)
        return cls.model_validate({
            **snapshot,
            "activation_digest": _authority_activation_digest(snapshot),
        })

    @model_validator(mode="after")
    def _validate_activation_digest(self) -> AuthorityPolicyActivation:
        snapshot = self.model_dump(
            mode="python", exclude={"activation_digest"}, round_trip=True,
            warnings=False,
        )
        if self.activation_digest != _authority_activation_digest(snapshot):
            raise ValueError("activation_digest does not match canonical activation semantics")
        return self

    @field_validator("activation_digest")
    @classmethod
    def _activation_digest_is_bounded_hex(cls, value, info):
        return validate_authority_digest(value, info.field_name)


class AuthorityCandidatePolicyPin(BaseModel):
    """Immutable one-to-one release/activation identity for a DB-policy candidate."""
    model_config = {"extra": "forbid", "frozen": True}

    candidate_id: str
    release_id: str
    activation_id: str
    activation_epoch: int = Field(gt=0)
    provider_id: str
    executor_kind: str
    created_at: datetime = Field(default_factory=_now)


class AuthorityEvaluation(BaseModel):
    """The single, immutable evaluation outcome for a candidate.

    Stores the *digest* of the evaluator response plus a controlled
    disposition/code — never the raw response text or unredacted exchange.
    """
    model_config = {"extra": "forbid"}

    id: int | None = None
    candidate_id: str
    disposition: AuthorityDisposition
    disposition_code: AuthorityDispositionCode
    response_digest: str
    response_retention_class: AuthorityRetentionClass = AuthorityRetentionClass.DIGEST_ONLY
    response_redaction_class: AuthorityRedactionClass = AuthorityRedactionClass.REDACTED
    fence_results: dict[str, AuthorityFenceResult] | None = None
    created_at: datetime = Field(default_factory=_now)

    @field_validator("response_digest")
    @classmethod
    def _response_digest_is_bounded_hex(cls, value, info):
        return validate_authority_digest(value, info.field_name)


class AuthorityAuditEvent(BaseModel):
    """One append-only authority audit event. Immutable after write."""
    model_config = {"extra": "forbid"}

    id: int | None = None
    candidate_id: str
    event_type: AuthorityAuditEventType
    payload: AuthorityAuditPayload | None = None
    created_at: datetime = Field(default_factory=_now)
