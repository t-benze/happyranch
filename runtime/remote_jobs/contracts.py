"""Strict, immutable remote-job protocol contracts.

S1 owns data shape, canonical serialization, hashing, and stable outcome
selection only.  Constructing one of these models does not admit or execute a
job and does not authenticate a peer.
"""

from __future__ import annotations

import base64
import hashlib
import json
import math
import re
from datetime import datetime, timezone
from enum import StrEnum
from typing import Annotated, Any, ClassVar, Generic, Iterable, Literal, TypeVar

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    ValidationInfo,
    field_validator,
    model_validator,
)

PROTOCOL_VERSION = 1

Identifier = Annotated[str, StringConstraints(pattern=r"^[A-Z][A-Z0-9_]*-[A-Za-z0-9][A-Za-z0-9._-]{0,126}$")]
Digest = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
StableName = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,126}$")]
RelativePath = Annotated[str, StringConstraints(min_length=1, max_length=1024)]
PositiveInt = Annotated[int, Field(strict=True, ge=1)]
NonNegativeInt = Annotated[int, Field(strict=True, ge=0)]


class FrozenDict(dict[str, str]):
    """JSON-object-compatible mapping that cannot be changed after admission."""

    def _immutable(self, *_args: Any, **_kwargs: Any) -> None:
        raise TypeError("admitted mappings are immutable")

    __delitem__ = _immutable
    __setitem__ = _immutable
    clear = _immutable
    pop = _immutable
    popitem = _immutable
    setdefault = _immutable
    update = _immutable


def _validate_json_value(value: Any) -> None:
    if value is None or isinstance(value, (str, bool, int)):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("canonical JSON does not permit non-finite numbers")
        raise ValueError("canonical remote-job JSON does not permit floats")
    if isinstance(value, list):
        for item in value:
            _validate_json_value(item)
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError("canonical JSON object keys must be strings")
            _validate_json_value(item)
        return
    raise ValueError(f"unsupported canonical JSON value: {type(value).__name__}")


def canonical_json_bytes(value: Any) -> bytes:
    """Return the v1 canonical representation used for every digest."""

    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json", exclude_unset=True)
    _validate_json_value(value)
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def canonical_digest(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


class CanonicalModel(BaseModel):
    # Numeric fields opt into strict validation individually. Enum wire values
    # intentionally arrive as JSON strings and are parsed into their closed set.
    model_config = ConfigDict(extra="forbid", frozen=True)

    def canonical_bytes(self) -> bytes:
        return canonical_json_bytes(self)

    def digest(self) -> str:
        return canonical_digest(self)


def _require_validation_context(info: ValidationInfo, name: str) -> Any:
    if info.context is None or name not in info.context or info.context[name] is None:
        raise ValueError(f"{name} validation context is required")
    return info.context[name]


def _canonical_utc(value: str) -> str:
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z", value):
        raise ValueError("timestamp must be canonical RFC3339 UTC text ending in Z")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise ValueError("invalid RFC3339 timestamp") from exc
    if parsed.tzinfo != timezone.utc:
        raise ValueError("timestamp must be UTC")
    return value


def _validate_relative_path(value: str) -> str:
    if value.startswith(("/", "\\")) or "\\" in value:
        raise ValueError("path must use relative POSIX syntax")
    parts = value.split("/")
    if any(part in ("", "..") for part in parts):
        raise ValueError("path cannot be empty or traverse upward")
    if any(part == "." for part in parts[1:]):
        raise ValueError("path contains a noncanonical dot segment")
    return value


class PhaseName(StrEnum):
    PRE_RUN = "pre_run"
    WORKSPACE_OBSERVATION = "workspace_observation"
    RUN = "run"
    POST_RUN = "post_run"
    FINALIZATION = "finalization"


class PreRunMode(StrEnum):
    ALWAYS = "always"
    ONCE_PER_WORKSPACE_GENERATION = "once_per_workspace_generation"


class ObservationMethod(StrEnum):
    FULL_CONTENT_SHA256 = "full_content_sha256"


class SymlinkPolicy(StrEnum):
    REJECT = "reject"
    HASH_LINK = "hash_link"


class SpecialFilePolicy(StrEnum):
    REJECT = "reject"
    RECORD_METADATA = "record_metadata"


class RunnerRequirement(CanonicalModel):
    runner_id: Annotated[str, StringConstraints(pattern=r"^RUNNER-[A-Za-z0-9][A-Za-z0-9._-]{0,126}$")]
    runner_generation: PositiveInt
    attestation_digest: Digest
    required_capabilities: tuple[StableName, ...]
    network_policy_id: StableName

    @field_validator("required_capabilities")
    @classmethod
    def unique_sorted_capabilities(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if not value or tuple(sorted(set(value))) != value:
            raise ValueError("required_capabilities must be nonempty, unique, and sorted")
        return value


class WorkspaceRequirement(CanonicalModel):
    workspace_id: Annotated[str, StringConstraints(pattern=r"^RWS-[A-Za-z0-9][A-Za-z0-9._-]{0,126}$")]
    workspace_generation: PositiveInt
    agent_name: StableName


class PhaseSpec(CanonicalModel):
    script: Annotated[str, StringConstraints(min_length=1, max_length=1_048_576)]
    interpreter: Annotated[str, StringConstraints(min_length=1, max_length=1024)]
    cwd: RelativePath
    env: dict[Annotated[str, StringConstraints(pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")], str]
    runtime_limit_ms: PositiveInt
    stdout_limit_bytes: PositiveInt
    stderr_limit_bytes: PositiveInt

    _cwd = field_validator("cwd")(_validate_relative_path)

    @field_validator("env")
    @classmethod
    def sorted_env(cls, value: dict[str, str]) -> dict[str, str]:
        if len(value) > 256:
            raise ValueError("too many environment entries")
        return value

    @model_validator(mode="after")
    def freeze_env(self) -> "PhaseSpec":
        object.__setattr__(self, "env", FrozenDict(self.env))
        return self


class ObservedRoot(CanonicalModel):
    path: RelativePath
    method: ObservationMethod

    _path = field_validator("path")(_validate_relative_path)


class ObservationPolicy(CanonicalModel):
    version: PositiveInt
    policy_digest: Digest
    required_roots: tuple[RelativePath, ...]
    observed_roots: tuple[ObservedRoot, ...]
    excluded_paths: tuple[RelativePath, ...]
    max_entries: PositiveInt
    max_bytes: PositiveInt
    max_elapsed_ms: PositiveInt
    max_depth: PositiveInt
    symlink_policy: SymlinkPolicy
    special_file_policy: SpecialFilePolicy

    @field_validator("required_roots", "excluded_paths")
    @classmethod
    def normalized_paths(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(_validate_relative_path(item) for item in value)
        if tuple(sorted(set(normalized))) != normalized:
            raise ValueError("paths must be unique and sorted")
        return normalized

    @field_validator("observed_roots")
    @classmethod
    def normalized_observed_roots(cls, value: tuple[ObservedRoot, ...]) -> tuple[ObservedRoot, ...]:
        paths = tuple(root.path for root in value)
        if tuple(sorted(set(paths))) != paths:
            raise ValueError("observed roots must be unique and sorted")
        return value

    @model_validator(mode="after")
    def required_roots_are_observed(self) -> "ObservationPolicy":
        observed = {root.path for root in self.observed_roots}
        missing = set(self.required_roots) - observed
        if missing:
            raise ValueError(f"required root is not observed: {sorted(missing)[0]}")
        for required in self.required_roots:
            for excluded in self.excluded_paths:
                if excluded == required or excluded.startswith(required + "/"):
                    raise ValueError(f"required root has an excluded descendant: {required}")
        material = self.model_dump(mode="json", exclude={"policy_digest"})
        if canonical_digest(material) != self.policy_digest:
            raise ValueError("policy_digest does not match normalized observation policy")
        return self


class ReusePolicy(CanonicalModel):
    mode: PreRunMode
    pre_run_digest: Digest
    observation_policy: ObservationPolicy


class JobBundle(CanonicalModel):
    v: Literal[1]
    job_id: Annotated[str, StringConstraints(pattern=r"^JOB-[A-Za-z0-9][A-Za-z0-9._-]{0,126}$")]
    runner: RunnerRequirement
    workspace: WorkspaceRequirement
    pre_run: PhaseSpec | None = None
    run: PhaseSpec
    post_run: PhaseSpec | None = None
    reuse: ReusePolicy | None = None

    @model_validator(mode="after")
    def reuse_requires_pre_run(self) -> "JobBundle":
        if self.reuse is None:
            if self.pre_run is not None:
                raise ValueError("a declared pre_run phase requires a reuse policy")
            return self
        if self.pre_run is None:
            raise ValueError("reuse policy requires a declared pre_run phase")
        if self.pre_run.digest() != self.reuse.pre_run_digest:
            # The digest is admitted identity, so accept only exact self-consistency.
            raise ValueError("pre_run_digest does not match the admitted pre_run phase")
        return self


class AdmissionOffer(CanonicalModel):
    frame_type: ClassVar[str] = "ADMISSION_OFFER"
    bundle: JobBundle
    bundle_digest: Digest
    attempt_id: Annotated[str, StringConstraints(pattern=r"^RATT-[A-Za-z0-9][A-Za-z0-9._-]{0,126}$")]
    fence_token: str
    lease_generation: PositiveInt
    lease_expires_at: str

    _lease_expires = field_validator("lease_expires_at")(_canonical_utc)

    @field_validator("fence_token")
    @classmethod
    def valid_fence(cls, value: str) -> str:
        _validate_fence(value)
        return value

    @model_validator(mode="after")
    def bundle_is_exact(self) -> "AdmissionOffer":
        if self.bundle.digest() != self.bundle_digest:
            raise ValueError("bundle_digest does not match bundle")
        return self


class AdmissionAccepted(CanonicalModel):
    frame_type: ClassVar[str] = "ADMISSION_ACCEPTED"
    bundle_digest: Digest
    runner_id: Annotated[str, StringConstraints(pattern=r"^RUNNER-[A-Za-z0-9][A-Za-z0-9._-]{0,126}$")]
    runner_generation: PositiveInt
    workspace_id: Annotated[str, StringConstraints(pattern=r"^RWS-[A-Za-z0-9][A-Za-z0-9._-]{0,126}$")]
    workspace_generation: PositiveInt
    attempt_id: Annotated[str, StringConstraints(pattern=r"^RATT-[A-Za-z0-9][A-Za-z0-9._-]{0,126}$")]
    fence_token: str
    lease_generation: PositiveInt

    @field_validator("fence_token")
    @classmethod
    def valid_fence(cls, value: str) -> str:
        _validate_fence(value)
        return value


class PhaseStarted(CanonicalModel):
    frame_type: ClassVar[str] = "PHASE_STARTED"
    phase: PhaseName
    ordinal: PositiveInt
    phase_digest: Digest
    started_at: str

    _started = field_validator("started_at")(_canonical_utc)


class PhaseLogChunk(CanonicalModel):
    frame_type: ClassVar[str] = "PHASE_LOG_CHUNK"
    phase: PhaseName
    ordinal: PositiveInt
    phase_digest: Digest
    stream: Literal["stdout", "stderr"]
    offset: NonNegativeInt
    data_b64: str

    @field_validator("data_b64")
    @classmethod
    def canonical_base64(cls, value: str) -> str:
        try:
            raw = base64.b64decode(value, validate=True)
        except ValueError as exc:
            raise ValueError("invalid base64 log data") from exc
        if base64.b64encode(raw).decode("ascii") != value:
            raise ValueError("noncanonical base64 log data")
        return value


class PhaseFinished(CanonicalModel):
    frame_type: ClassVar[str] = "PHASE_FINISHED"
    phase: PhaseName
    ordinal: PositiveInt
    phase_digest: Digest
    outcome: Literal["succeeded", "failed", "timed_out", "output_capped", "cancelled", "skipped"]
    stable_reason: "StableReason | None" = None
    started_at: str | None = None
    finished_at: str
    exit_code: int | None = None
    stdout_bytes: NonNegativeInt
    stderr_bytes: NonNegativeInt
    observation_policy_digest: Digest | None = None
    receipt_digest: Digest

    _started = field_validator("started_at")(
        lambda value: None if value is None else _canonical_utc(value)
    )
    _finished = field_validator("finished_at")(_canonical_utc)

    @model_validator(mode="after")
    def legal_receipt(self, info: ValidationInfo) -> "PhaseFinished":
        _validate_phase_result(self.phase, self.outcome, self.stable_reason)
        if self.phase == PhaseName.WORKSPACE_OBSERVATION:
            policy_digest = _require_validation_context(info, "observation_policy_digest")
            if self.observation_policy_digest is None:
                raise ValueError("workspace observation receipt requires policy digest")
            if self.observation_policy_digest != policy_digest:
                raise ValueError("observation receipt policy digest does not match admission")
        elif self.observation_policy_digest is not None:
            raise ValueError("policy digest is valid only on workspace observation receipt")
        started = None if self.started_at is None else datetime.fromisoformat(self.started_at[:-1] + "+00:00")
        finished = datetime.fromisoformat(self.finished_at[:-1] + "+00:00")
        if self.outcome == "skipped":
            if self.started_at is not None or self.exit_code is not None or self.stdout_bytes or self.stderr_bytes:
                raise ValueError("skipped pre_run has no start, exit code, or output")
        else:
            if started is None:
                raise ValueError("executed phase requires started_at")
            if finished < started:
                raise ValueError("finished_at precedes started_at")
        if self.outcome == "succeeded" and self.phase in {PhaseName.PRE_RUN, PhaseName.RUN, PhaseName.POST_RUN}:
            if self.exit_code != 0:
                raise ValueError("successful script phase requires exit_code 0")
        elif self.stable_reason is not None and self.stable_reason.value.endswith("_nonzero"):
            if self.exit_code is None or self.exit_code == 0:
                raise ValueError("nonzero outcome requires a nonzero exit_code")
        elif self.exit_code is not None:
            raise ValueError("exit_code is absent unless a script exited")
        if self.phase in {PhaseName.PRE_RUN, PhaseName.RUN, PhaseName.POST_RUN}:
            phase_spec = _require_validation_context(info, "phase_spec")
            spec = PhaseSpec.model_validate(phase_spec)
            if self.stdout_bytes > spec.stdout_limit_bytes or self.stderr_bytes > spec.stderr_limit_bytes:
                raise ValueError("phase byte counters exceed admitted caps")
            if self.phase_digest != spec.digest():
                raise ValueError("phase_digest does not match admitted phase")
        material = self.model_dump(mode="json", exclude={"receipt_digest"}, exclude_unset=True)
        if canonical_digest(material) != self.receipt_digest:
            raise ValueError("receipt_digest does not match phase receipt")
        return self


class CancelRequested(CanonicalModel):
    frame_type: ClassVar[str] = "CANCEL_REQUESTED"
    requested_at: str
    reason: Literal["cancelled"] = "cancelled"

    _requested = field_validator("requested_at")(_canonical_utc)


class CancelAccepted(CanonicalModel):
    frame_type: ClassVar[str] = "CANCEL_ACCEPTED"
    accepted_at: str
    termination_started: Literal[True]

    _accepted = field_validator("accepted_at")(_canonical_utc)


class LeaseRenew(CanonicalModel):
    frame_type: ClassVar[str] = "LEASE_RENEW"
    lease_generation: PositiveInt
    requested_expires_at: str

    _expires = field_validator("requested_expires_at")(_canonical_utc)


class LeaseAck(CanonicalModel):
    frame_type: ClassVar[str] = "LEASE_ACK"
    lease_generation: PositiveInt
    lease_expires_at: str

    _expires = field_validator("lease_expires_at")(_canonical_utc)


class ReconcileRequest(CanonicalModel):
    frame_type: ClassVar[str] = "RECONCILE_REQUEST"
    runner_id: Annotated[str, StringConstraints(pattern=r"^RUNNER-[A-Za-z0-9][A-Za-z0-9._-]{0,126}$")]
    runner_generation: PositiveInt
    workspace_id: Annotated[str, StringConstraints(pattern=r"^RWS-[A-Za-z0-9][A-Za-z0-9._-]{0,126}$")]
    workspace_generation: PositiveInt
    attempt_id: Annotated[str, StringConstraints(pattern=r"^RATT-[A-Za-z0-9][A-Za-z0-9._-]{0,126}$")]
    fence_token: str
    last_accepted_frame_seq: NonNegativeInt

    @field_validator("fence_token")
    @classmethod
    def valid_fence(cls, value: str) -> str:
        _validate_fence(value)
        return value


class ReconcileResponse(CanonicalModel):
    frame_type: ClassVar[str] = "RECONCILE_RESPONSE"
    disposition: Literal["NOT_STARTED", "ACTIVE", "TERMINAL", "UNPROVABLE"]
    runner_id: Annotated[str, StringConstraints(pattern=r"^RUNNER-[A-Za-z0-9][A-Za-z0-9._-]{0,126}$")]
    runner_generation: PositiveInt
    workspace_id: Annotated[str, StringConstraints(pattern=r"^RWS-[A-Za-z0-9][A-Za-z0-9._-]{0,126}$")]
    workspace_generation: PositiveInt
    attempt_id: Annotated[str, StringConstraints(pattern=r"^RATT-[A-Za-z0-9][A-Za-z0-9._-]{0,126}$")]
    fence_token: str
    last_frame_seq: NonNegativeInt
    journal_digest: Digest | None = None
    terminal_digest: Digest | None = None

    @field_validator("fence_token")
    @classmethod
    def valid_fence(cls, value: str) -> str:
        _validate_fence(value)
        return value

    @model_validator(mode="after")
    def disposition_evidence(self) -> "ReconcileResponse":
        if self.disposition == "TERMINAL" and (self.journal_digest is None or self.terminal_digest is None):
            raise ValueError("TERMINAL requires journal_digest and terminal_digest")
        if self.disposition != "TERMINAL" and self.terminal_digest is not None:
            raise ValueError("terminal_digest is valid only for TERMINAL")
        return self


class ReceiptLink(CanonicalModel):
    phase: PhaseName
    ordinal: PositiveInt
    outcome: Literal["succeeded", "failed", "timed_out", "output_capped", "cancelled", "skipped"]
    stable_reason: "StableReason | None" = None
    receipt_digest: Digest
    observation_policy_digest: Digest | None = None

    @model_validator(mode="after")
    def legal_result(self) -> "ReceiptLink":
        _validate_phase_result(self.phase, self.outcome, self.stable_reason, terminal_link=True)
        if self.phase == PhaseName.WORKSPACE_OBSERVATION:
            if self.observation_policy_digest is None:
                raise ValueError("workspace observation link requires policy digest")
        elif self.observation_policy_digest is not None:
            raise ValueError("policy digest is valid only on workspace observation link")
        return self


class AdmittedPhase(CanonicalModel):
    """Phase digest context reconciled with bundle-derived phase identity."""

    phase: PhaseName
    ordinal: PositiveInt
    phase_digest: Digest


def _required_phase_identities(bundle: JobBundle) -> tuple[tuple[PhaseName, int], ...]:
    identities: list[tuple[PhaseName, int]] = []
    if bundle.pre_run is not None:
        identities.extend(((PhaseName.PRE_RUN, 1), (PhaseName.WORKSPACE_OBSERVATION, 1)))
    identities.append((PhaseName.RUN, 1))
    if bundle.post_run is not None:
        identities.append((PhaseName.POST_RUN, 1))
    identities.append((PhaseName.FINALIZATION, 1))
    return tuple(identities)


class TerminalProposed(CanonicalModel):
    frame_type: ClassVar[str] = "TERMINAL_PROPOSED"
    bundle_digest: Digest
    receipt_links: tuple[ReceiptLink, ...]
    finalization_receipt_link: ReceiptLink
    primary_status: Literal["completed", "failed", "rejected"]
    primary_reason: "StableReason | None"
    terminal_digest: Digest

    @model_validator(mode="after")
    def complete_consistent_terminal(self, info: ValidationInfo) -> "TerminalProposed":
        receipt_evidence = _require_validation_context(info, "canonical_receipts")
        bundle = _require_validation_context(info, "admitted_bundle")
        if not self.receipt_links:
            raise ValueError("terminal proposal omits phase receipt links")
        if self.finalization_receipt_link.phase != PhaseName.FINALIZATION:
            raise ValueError("finalization receipt link must name finalization")
        links = (*self.receipt_links, self.finalization_receipt_link)
        identities = [(link.phase, link.ordinal) for link in links]
        if len(set(identities)) != len(identities):
            raise ValueError("duplicate receipt link")
        receipts = tuple(receipt_evidence)
        receipt_digests = [receipt.receipt_digest for receipt in receipts]
        if len(set(receipt_digests)) != len(receipt_digests):
            raise ValueError("duplicate receipt digest in canonical receipts")
        if len(set(link.receipt_digest for link in links)) != len(links):
            raise ValueError("duplicate receipt digest in terminal links")
        by_identity = {(receipt.phase, receipt.ordinal): receipt for receipt in receipts}
        if len(by_identity) != len(receipts) or set(by_identity) != set(identities):
            raise ValueError("terminal links do not exactly cover canonical receipts")
        for link in links:
            receipt = by_identity[(link.phase, link.ordinal)]
            facts = (
                link.receipt_digest == receipt.receipt_digest,
                link.outcome == receipt.outcome,
                link.stable_reason == receipt.stable_reason,
                link.observation_policy_digest == receipt.observation_policy_digest,
            )
            if not all(facts):
                raise ValueError("terminal link does not match canonical receipt")
        reasons = [receipt.stable_reason for receipt in receipts if receipt.stable_reason is not None]
        expected = resolve_primary_outcome(reasons)
        if self.primary_status != expected.status or self.primary_reason != expected.reason:
            raise ValueError("terminal status/reason contradict receipt precedence")
        admitted = JobBundle.model_validate(bundle)
        if self.bundle_digest != admitted.digest():
            raise ValueError("terminal bundle_digest does not match admitted bundle")
        required_identities = _required_phase_identities(admitted)
        if len(links) != len(required_identities) or set(identities) != set(required_identities):
            raise ValueError("terminal proposal omits or adds a phase receipt")
        policy_digest = admitted.reuse.observation_policy.policy_digest if admitted.reuse else None
        observation = next((link for link in links if link.phase == PhaseName.WORKSPACE_OBSERVATION), None)
        if observation is not None and observation.observation_policy_digest != policy_digest:
            raise ValueError("observation receipt policy digest mismatch")
        material = self.model_dump(mode="json", exclude={"terminal_digest"})
        if canonical_digest(material) != self.terminal_digest:
            raise ValueError("terminal_digest does not match proposal")
        return self


class Hello(CanonicalModel):
    frame_type: ClassVar[str] = "HELLO"
    supported_versions: tuple[PositiveInt, ...]
    capabilities: tuple[StableName, ...]

    @field_validator("supported_versions", "capabilities")
    @classmethod
    def sorted_unique(cls, value: tuple[Any, ...]) -> tuple[Any, ...]:
        if not value or tuple(sorted(set(value))) != value:
            raise ValueError("values must be nonempty, unique, and sorted")
        return value


class HelloAck(CanonicalModel):
    frame_type: ClassVar[str] = "HELLO_ACK"
    selected_version: Literal[1]
    capabilities: tuple[StableName, ...]


class StableReasonPayload(CanonicalModel):
    reason: "StableReason"


class AdmissionRefused(StableReasonPayload):
    frame_type: ClassVar[str] = "ADMISSION_REFUSED"

    @model_validator(mode="after")
    def admission_reason_only(self) -> "AdmissionRefused":
        if self.reason.value not in _ADMISSION_REASONS:
            raise ValueError("ADMISSION_REFUSED requires an admission reason")
        return self


class TerminalAccepted(CanonicalModel):
    frame_type: ClassVar[str] = "TERMINAL_ACCEPTED"
    terminal_digest: Digest
    committed_at: str

    _committed = field_validator("committed_at")(_canonical_utc)


def _validate_fence(value: str) -> None:
    if not re.fullmatch(r"[A-Za-z0-9_-]{43}", value):
        raise ValueError("fence token must be canonical unpadded base64url for 256 bits")
    try:
        decoded = base64.urlsafe_b64decode(value + "=")
    except ValueError as exc:
        raise ValueError("invalid fence token") from exc
    if len(decoded) != 32 or base64.urlsafe_b64encode(decoded).decode().rstrip("=") != value:
        raise ValueError("invalid fence token")


class FrameType(StrEnum):
    HELLO = "HELLO"
    HELLO_ACK = "HELLO_ACK"
    ADMISSION_OFFER = "ADMISSION_OFFER"
    ADMISSION_ACCEPTED = "ADMISSION_ACCEPTED"
    ADMISSION_REFUSED = "ADMISSION_REFUSED"
    PHASE_STARTED = "PHASE_STARTED"
    PHASE_LOG_CHUNK = "PHASE_LOG_CHUNK"
    PHASE_FINISHED = "PHASE_FINISHED"
    LEASE_RENEW = "LEASE_RENEW"
    LEASE_ACK = "LEASE_ACK"
    CANCEL_REQUESTED = "CANCEL_REQUESTED"
    CANCEL_ACCEPTED = "CANCEL_ACCEPTED"
    RECONCILE_REQUEST = "RECONCILE_REQUEST"
    RECONCILE_RESPONSE = "RECONCILE_RESPONSE"
    TERMINAL_PROPOSED = "TERMINAL_PROPOSED"
    TERMINAL_ACCEPTED = "TERMINAL_ACCEPTED"


_ADMISSION_CONTEXT_TYPES = frozenset(FrameType) - {
    FrameType.HELLO, FrameType.HELLO_ACK, FrameType.ADMISSION_OFFER,
    FrameType.ADMISSION_REFUSED,
}


PayloadT = TypeVar("PayloadT", bound=CanonicalModel)


class RemoteFrame(CanonicalModel, Generic[PayloadT]):
    v: Literal[1]
    type: FrameType
    org_slug: Annotated[str, StringConstraints(pattern=r"^[a-z0-9][a-z0-9-]{0,62}$")]
    runner_id: Annotated[str, StringConstraints(pattern=r"^RUNNER-[A-Za-z0-9][A-Za-z0-9._-]{0,126}$")]
    runner_generation: PositiveInt
    connection_id: Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")]
    frame_seq: PositiveInt
    attempt_id: Annotated[str, StringConstraints(pattern=r"^RATT-[A-Za-z0-9][A-Za-z0-9._-]{0,126}$")]
    fence_token: str
    lease_generation: PositiveInt
    sent_at: str
    payload: PayloadT

    _sent = field_validator("sent_at")(_canonical_utc)

    @field_validator("fence_token")
    @classmethod
    def valid_fence(cls, value: str) -> str:
        _validate_fence(value)
        return value

    @model_validator(mode="after")
    def type_matches_payload(self, info: ValidationInfo) -> "RemoteFrame[PayloadT]":
        expected = getattr(self.payload, "frame_type", None)
        if expected is not None and self.type.value != expected:
            raise ValueError(f"frame type {self.type.value} does not match payload {expected}")
        payload = self.payload
        comparisons: list[tuple[str, Any, Any]] = []
        for name in ("runner_id", "runner_generation", "attempt_id", "fence_token", "lease_generation"):
            if hasattr(payload, name):
                comparisons.append((name, getattr(self, name), getattr(payload, name)))
        if isinstance(payload, AdmissionOffer):
            comparisons.extend((
                ("bundle.runner_id", self.runner_id, payload.bundle.runner.runner_id),
                ("bundle.runner_generation", self.runner_generation, payload.bundle.runner.runner_generation),
            ))
        if self.type in _ADMISSION_CONTEXT_TYPES:
            admitted = JobBundle.model_validate(
                _require_validation_context(info, "admitted_bundle")
            )
            comparisons.extend((
                ("bundle.runner_id", admitted.runner.runner_id, self.runner_id),
                ("bundle.runner_generation", admitted.runner.runner_generation, self.runner_generation),
            ))
            for name, expected_value in (
                ("runner_id", admitted.runner.runner_id),
                ("runner_generation", admitted.runner.runner_generation),
                ("workspace_id", admitted.workspace.workspace_id),
                ("workspace_generation", admitted.workspace.workspace_generation),
                ("bundle_digest", admitted.digest()),
            ):
                if hasattr(payload, name):
                    comparisons.append((f"bundle.{name}", expected_value, getattr(payload, name)))
        for name, envelope_value, payload_value in comparisons:
            if envelope_value != payload_value:
                raise ValueError(f"envelope {name} does not match payload")
        return self


_ADMISSION_REASONS = (
    "runner_required", "runner_unknown", "runner_unavailable", "runner_full",
    "runner_revoked", "runner_unhealthy", "attestation_missing", "attestation_expired",
    "attestation_mismatch", "capability_mismatch", "bundle_invalid",
    "protocol_version_unsupported", "workspace_unavailable",
)
_AUTHORITY_REASONS = (
    "identity_invalid", "identity_stale_generation", "certificate_revoked", "fence_invalid",
    "lease_expired", "replay_conflict", "execution_uncertain", "runner_disconnected",
)
_PHASE_REASONS = tuple(
    f"{phase}_{suffix}"
    for phase in ("pre_run", "run", "post_run")
    for suffix in ("spawn_failed", "nonzero", "timeout", "output_cap", "cancelled")
)
_OBSERVATION_REASONS = (
    "workspace_observation_failed", "workspace_observation_cap", "workspace_observation_mismatch",
)
_FINALIZATION_REASONS = (
    "termination_unproven", "finalization_failed", "result_persistence_failed", "capacity_release_unproven",
)
_CONTROL_REASONS = ("cancelled", "founder_rejected")


StableReason = StrEnum(
    "StableReason",
    {reason.upper(): reason for reason in (*_ADMISSION_REASONS, *_AUTHORITY_REASONS, *_PHASE_REASONS, *_OBSERVATION_REASONS, *_FINALIZATION_REASONS, *_CONTROL_REASONS)},
)
ALL_STABLE_REASONS = frozenset(item.value for item in StableReason)


class PrimaryOutcome(CanonicalModel):
    status: Literal["completed", "failed", "rejected"]
    reason: StableReason | None


_PRECEDENCE: tuple[tuple[str, ...], ...] = (
    ("fence_invalid", "identity_stale_generation", "lease_expired", "replay_conflict", "execution_uncertain", "runner_disconnected", "identity_invalid", "certificate_revoked"),
    _FINALIZATION_REASONS,
    ("cancelled", "pre_run_cancelled", "run_cancelled", "post_run_cancelled"),
    ("pre_run_timeout", "pre_run_output_cap", "workspace_observation_cap", "run_timeout", "run_output_cap", "post_run_timeout", "post_run_output_cap"),
    ("pre_run_spawn_failed", "pre_run_nonzero"),
    ("workspace_observation_failed", "workspace_observation_mismatch"),
    ("run_spawn_failed", "run_nonzero"),
    ("post_run_spawn_failed", "post_run_nonzero"),
    ("founder_rejected",),
    _ADMISSION_REASONS,
)


def resolve_primary_outcome(reasons: Iterable[StableReason | str]) -> PrimaryOutcome:
    """Choose the stable public outcome using the v1 closed precedence table."""

    values = {StableReason(reason).value for reason in reasons}
    if not values:
        return PrimaryOutcome(status="completed", reason=None)
    for tier in _PRECEDENCE:
        for candidate in tier:
            if candidate in values:
                status = "rejected" if candidate in _ADMISSION_REASONS or candidate == "founder_rejected" else "failed"
                return PrimaryOutcome(status=status, reason=StableReason(candidate))
    raise AssertionError("stable reason is missing from precedence table")


def _validate_phase_result(
    phase: PhaseName,
    outcome: str,
    reason: StableReason | None,
    *,
    terminal_link: bool = False,
) -> None:
    value = None if reason is None else reason.value
    script_phase = phase.value if phase in {PhaseName.PRE_RUN, PhaseName.RUN, PhaseName.POST_RUN} else None
    if outcome == "skipped":
        if phase != PhaseName.PRE_RUN or value is not None:
            raise ValueError("only reusable pre_run may be skipped without a reason")
        return
    if phase == PhaseName.WORKSPACE_OBSERVATION:
        expected = {
            "succeeded": {None},
            "failed": {"workspace_observation_failed", "workspace_observation_mismatch"},
            "output_capped": {"workspace_observation_cap"},
        }
    elif phase == PhaseName.FINALIZATION:
        expected = {
            "succeeded": {None},
            "failed": set(_FINALIZATION_REASONS) | (
                set(_AUTHORITY_REASONS) | set(_ADMISSION_REASONS) | {"founder_rejected"}
                if terminal_link else set()
            ),
            "cancelled": {"cancelled"} if terminal_link else set(),
        }
    else:
        expected = {
            "succeeded": {None},
            "failed": {f"{script_phase}_spawn_failed", f"{script_phase}_nonzero"},
            "timed_out": {f"{script_phase}_timeout"},
            "output_capped": {f"{script_phase}_output_cap"},
            "cancelled": {f"{script_phase}_cancelled"},
        }
    if value not in expected.get(outcome, set()):
        raise ValueError(f"{phase.value} {outcome} has an illegal stable reason")


_PAYLOAD_MODELS: dict[FrameType, type[CanonicalModel]] = {
    FrameType.HELLO: Hello,
    FrameType.HELLO_ACK: HelloAck,
    FrameType.ADMISSION_OFFER: AdmissionOffer,
    FrameType.ADMISSION_ACCEPTED: AdmissionAccepted,
    FrameType.ADMISSION_REFUSED: AdmissionRefused,
    FrameType.PHASE_STARTED: PhaseStarted,
    FrameType.PHASE_LOG_CHUNK: PhaseLogChunk,
    FrameType.PHASE_FINISHED: PhaseFinished,
    FrameType.LEASE_RENEW: LeaseRenew,
    FrameType.LEASE_ACK: LeaseAck,
    FrameType.CANCEL_REQUESTED: CancelRequested,
    FrameType.CANCEL_ACCEPTED: CancelAccepted,
    FrameType.RECONCILE_REQUEST: ReconcileRequest,
    FrameType.RECONCILE_RESPONSE: ReconcileResponse,
    FrameType.TERMINAL_PROPOSED: TerminalProposed,
    FrameType.TERMINAL_ACCEPTED: TerminalAccepted,
}


def parse_remote_frame(
    value: Any,
    *,
    admitted_bundle: JobBundle | dict[str, Any] | None = None,
    admission_offer: AdmissionOffer | dict[str, Any] | None = None,
    admitted_phases: Iterable[AdmittedPhase | dict[str, Any]] | None = None,
    canonical_receipts: Iterable[PhaseFinished | dict[str, Any] | bytes] | None = None,
) -> RemoteFrame[CanonicalModel]:
    """Parse an untrusted v1 frame into its exact typed payload and bind duplicates."""

    if not isinstance(value, dict):
        raise ValueError("remote frame must be a JSON object")
    frame_type = FrameType(value.get("type"))
    payload_model = _PAYLOAD_MODELS[frame_type]
    context: dict[str, Any] = {}
    offer = None if admission_offer is None else AdmissionOffer.model_validate(admission_offer)
    supplied_bundle = (
        None if admitted_bundle is None else JobBundle.model_validate(admitted_bundle)
    )
    if offer is not None and supplied_bundle is not None and supplied_bundle != offer.bundle:
        raise ValueError("admitted bundle does not match admission offer")
    bundle = offer.bundle if offer is not None else supplied_bundle
    phase_context_types = {
        FrameType.PHASE_STARTED, FrameType.PHASE_LOG_CHUNK,
        FrameType.PHASE_FINISHED, FrameType.TERMINAL_PROPOSED,
    }
    phases = None if admitted_phases is None else tuple(
        AdmittedPhase.model_validate(item) for item in admitted_phases
    )
    if frame_type in _ADMISSION_CONTEXT_TYPES and offer is None:
        raise ValueError(f"{frame_type.value} validation requires admission validation context")
    if frame_type in phase_context_types and phases is None:
        raise ValueError(f"{frame_type.value} validation requires admission validation context")
    if phases is not None and bundle is not None:
        identities = tuple((item.phase, item.ordinal) for item in phases)
        required_identities = _required_phase_identities(bundle)
        if len(identities) != len(required_identities) or set(identities) != set(required_identities):
            raise ValueError("admitted phase context does not match admitted bundle")
        derived_digests = {
            PhaseName.RUN: bundle.run.digest(),
        }
        if bundle.pre_run is not None and bundle.reuse is not None:
            derived_digests[PhaseName.PRE_RUN] = bundle.pre_run.digest()
            derived_digests[PhaseName.WORKSPACE_OBSERVATION] = bundle.reuse.observation_policy.digest()
        if bundle.post_run is not None:
            derived_digests[PhaseName.POST_RUN] = bundle.post_run.digest()
        if any(item.phase_digest != derived_digests[item.phase] for item in phases if item.phase in derived_digests):
            raise ValueError("admitted phase digest does not match admitted bundle")
    if bundle is not None:
        context["admitted_bundle"] = bundle
        payload = value.get("payload")
        if frame_type in {FrameType.PHASE_STARTED, FrameType.PHASE_LOG_CHUNK, FrameType.PHASE_FINISHED} and isinstance(payload, dict):
            phase = PhaseName(payload.get("phase"))
            ordinal = payload.get("ordinal")
            matches = [item for item in phases or () if item.phase == phase and item.ordinal == ordinal]
            if len(matches) != 1 or payload.get("phase_digest") != matches[0].phase_digest:
                raise ValueError("frame phase identity does not match admitted phase")
            spec = {
                PhaseName.PRE_RUN: bundle.pre_run,
                PhaseName.RUN: bundle.run,
                PhaseName.POST_RUN: bundle.post_run,
            }.get(phase)
            if spec is not None:
                context["phase_spec"] = spec
            if phase == PhaseName.WORKSPACE_OBSERVATION and bundle.reuse is not None:
                context["observation_policy_digest"] = bundle.reuse.observation_policy.policy_digest
    if offer is not None:
        for name, actual, expected in (
            ("runner_id", value.get("runner_id"), offer.bundle.runner.runner_id),
            ("runner_generation", value.get("runner_generation"), offer.bundle.runner.runner_generation),
            ("attempt_id", value.get("attempt_id"), offer.attempt_id),
            ("fence_token", value.get("fence_token"), offer.fence_token),
            ("lease_generation", value.get("lease_generation"), offer.lease_generation),
        ):
            if actual != expected:
                raise ValueError(f"frame {name} does not match admission")
    if frame_type == FrameType.TERMINAL_PROPOSED:
        if canonical_receipts is None:
            raise ValueError("TERMINAL_PROPOSED validation requires canonical receipts")
        parsed_receipts: list[PhaseFinished] = []
        for item in canonical_receipts:
            supplied_bytes = item if isinstance(item, bytes) else None
            if isinstance(item, bytes):
                try:
                    item = json.loads(item.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    raise ValueError("canonical receipt bytes are invalid JSON") from exc
            raw_receipt = (
                item.model_dump(mode="json", exclude_unset=True)
                if isinstance(item, PhaseFinished) else item
            )
            receipt_phase = PhaseName(raw_receipt.get("phase"))
            match = next(
                (phase for phase in phases or () if phase.phase == receipt_phase and phase.ordinal == raw_receipt.get("ordinal")),
                None,
            )
            if match is None or raw_receipt.get("phase_digest") != match.phase_digest:
                raise ValueError("canonical receipt does not match admitted phase")
            receipt_context: dict[str, Any] = {}
            spec = {
                PhaseName.PRE_RUN: bundle.pre_run,
                PhaseName.RUN: bundle.run,
                PhaseName.POST_RUN: bundle.post_run,
            }.get(receipt_phase)
            if spec is not None:
                receipt_context["phase_spec"] = spec
            if receipt_phase == PhaseName.WORKSPACE_OBSERVATION and bundle.reuse is not None:
                receipt_context["observation_policy_digest"] = bundle.reuse.observation_policy.policy_digest
            parsed_receipt = PhaseFinished.model_validate(raw_receipt, context=receipt_context)
            if supplied_bytes is not None and parsed_receipt.canonical_bytes() != supplied_bytes:
                raise ValueError("receipt bytes are not canonical JSON")
            parsed_receipts.append(parsed_receipt)
        context["canonical_receipts"] = tuple(parsed_receipts)
    return RemoteFrame[payload_model].model_validate(value, context=context)  # type: ignore[valid-type,return-value]
