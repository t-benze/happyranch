"""Org-owned online move journal (THR296 seq135, B plus private D readback).

Native org attachment owns release preparation; generic DB never installs. E supplies actual founder admission;
models confer no identity authority. General transitions still refuse COMPLETE;
only D's private native readback transaction can commit the terminal receipt.
"""
from __future__ import annotations

from runtime.infrastructure._workflow_reader import (
    _dump as _reader_dump,
    _encode as _reader_encode,
    _json_dumps as _reader_json_dumps,
    _sha256 as _reader_sha256,
)

from runtime.infrastructure._workflow_reader import (
    _add as _reader_add,
    _put as _reader_put,
    _update as _reader_update,
)

from runtime.infrastructure._workflow_reader import (
    _all_true as _reader_all_true,
    _any_true as _reader_any_true,
)

from runtime.infrastructure._workflow_reader import (
    _dict as _reader_dict,
    _set as _reader_set,
    _tuple as _reader_tuple,
)

from runtime.infrastructure._workflow_reader import (
    _all as _reader_all, _one as _reader_one, _rows as _reader_rows,
    _json as _reader_json, _json_work as _reader_json_work,
    _reserve as _reader_reserve, _work as _reader_work,
)

import base64
import hashlib
import json
import re
import sqlite3
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from enum import StrEnum
from functools import lru_cache
from typing import Annotated, Any, Iterator, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

MAX_BYTES = 2 * 1024 * 1024
MAX_ROWS = 65536
MAX_SCAN_BYTES = 32 * 1024 * 1024
MAX_REVISION = 2**63 - 1
Identifier = Annotated[str, Field(min_length=1, max_length=256, pattern=r"^[A-Za-z0-9_-]+$")]
Namespace = Annotated[str, Field(min_length=1, max_length=256, pattern=r"^(?:[A-Za-z0-9_-]+|org/[A-Za-z0-9_-]+)$")]
Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
Positive = Annotated[int, Field(ge=1, le=MAX_REVISION)]
Agent = Literal["consultant_codex", "consultant_head"]
CanonicalPath = Literal["org/agents/consultant_codex.md", "org/agents/consultant_head.md", "org/teams.yaml"]


class TeamMoveError(ValueError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class Closed(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class FounderAdmission(Closed):
    """Internal provenance from existing human admission, never request actor."""
    principal: Literal["founder"]
    boundary: Literal["_require_human"]
    org_slug: Identifier
    admission_id: Identifier
    admitted_at: str

    @model_validator(mode="after")
    def check_time(self) -> FounderAdmission:
        _time(self.admitted_at)
        return self


class PathIdentity(Closed):
    """Captured by the file owner, no symlink or caller-selected raw path."""
    device: Annotated[int, Field(ge=0, le=MAX_REVISION)]
    inode: Positive
    mode: Annotated[int, Field(ge=0, le=0o777)]


class FileImage(Closed):
    path: CanonicalPath
    identity: PathIdentity
    before_base64: Annotated[str, Field(max_length=MAX_BYTES)]
    before_digest: Digest
    after_base64: Annotated[str, Field(max_length=MAX_BYTES)]
    after_digest: Digest

    @model_validator(mode="after")
    def check_images(self) -> FileImage:
        for name in ("before", "after"):
            raw = getattr(self, name + "_base64")
            body = base64.b64decode(raw, validate=True)
            if (base64.b64encode(body).decode("ascii") != raw
                    or _digest(body) != getattr(self, name + "_digest")):
                raise TeamMoveError("team_move_image_digest")
            body.decode("utf-8", errors="strict")
        return self


class WorkspaceIdentity(Closed):
    agent_id: Agent
    # Evidence only: infrastructure never opens or writes this location.
    path: Annotated[str, Field(min_length=2, max_length=4096, pattern=r"^/")]
    identity: PathIdentity

    @model_validator(mode="after")
    def check_path(self) -> WorkspaceIdentity:
        if "\x00" in self.path or any(p in {"", ".", ".."} for p in self.path.split("/")[1:]):
            raise TeamMoveError("team_move_workspace_path")
        return self


class ExpectedInputs(Closed):
    canonical_digest: Digest
    roster_digest: Digest
    naming_digest: Digest
    profile_digest: Digest
    dependency_digest: Digest
    policy_digest: Digest
    skill_selection_digest: Digest
    permission_digest: Digest
    authority_namespace: Namespace
    authority_generation: Positive
    authority_digest: Digest
    agent_revisions: tuple[Digest, Digest]


class MoveRequest(Closed):
    version: Literal[1]
    org_slug: Identifier
    source_team: Literal["consultant"]
    target_team: Literal["default"]
    target_principal: Literal["founder"]
    affected_agents: tuple[Literal["consultant_codex"], Literal["consultant_head"]]
    demote: Literal["consultant_head"]
    retire_source: Literal[True]
    drain_threads: bool
    expected: ExpectedInputs
    files: tuple[FileImage, FileImage, FileImage]
    workspaces: tuple[WorkspaceIdentity, WorkspaceIdentity]

    @model_validator(mode="after")
    def check_closure(self) -> MoveRequest:
        if type(self.version) is not int or type(self.retire_source) is not bool:
            raise TeamMoveError("team_move_request_version")
        if tuple(f.path for f in self.files) != (
            "org/agents/consultant_codex.md", "org/agents/consultant_head.md", "org/teams.yaml"
        ) or tuple(w.agent_id for w in self.workspaces) != self.affected_agents:
            raise TeamMoveError("team_move_closure")
        if len({w.path for w in self.workspaces}) != 2:
            raise TeamMoveError("team_move_workspace_identity")
        return self


class Phase(StrEnum):
    WAITING = "waiting"
    COMMIT_DECIDED = "commit_decided"
    FENCED = "fenced"
    RESET = "reset"
    CANONICAL = "canonical"
    CONTEXT = "context"
    PUBLISH = "publish"
    REPAIR_REQUIRED = "repair_required"
    COMPLETE = "complete"
    REFUSED = "refused"
    ABANDONED = "abandoned"


TERMINAL = frozenset({Phase.COMPLETE, Phase.REFUSED, Phase.ABANDONED})
FORWARD_PHASES = (Phase.COMMIT_DECIDED, Phase.FENCED, Phase.RESET,
                  Phase.CANONICAL, Phase.CONTEXT, Phase.PUBLISH)


class ForwardDecision(Closed):
    request_digest: Digest
    revalidated_inputs_digest: Digest
    census_digest: Digest
    writer_invocation_id: Identifier
    decided_at: str

    @model_validator(mode="after")
    def check_time(self) -> ForwardDecision:
        _time(self.decided_at)
        return self


class ResetEvidence(Closed):
    rows: Annotated[int, Field(ge=0, le=MAX_REVISION)]
    audit_id: Positive | None
    post_state_digest: Digest

    @model_validator(mode="after")
    def check_audit(self) -> ResetEvidence:
        if (self.rows == 0) != (self.audit_id is None):
            raise TeamMoveError("team_move_reset_audit")
        return self


class ContextEvidence(Closed):
    manifest_digest: Digest
    selection_digest: Digest
    integrity_digest: Digest


class AgentProgress(Closed):
    agent_id: Agent
    reset: ResetEvidence | None = None
    context: ContextEvidence | None = None


class PublicationIdentity(Closed):
    writer_invocation_id: Identifier
    journal_id: Identifier
    namespace: Namespace
    generation: Positive
    digest: Digest


class RecoveryIdentity(Closed):
    previous_owner_id: Identifier
    owner_id: Identifier
    claim_id: Identifier
    previous_owner_quiescence_digest: Digest


class StagingFact(Closed):
    """Append-only evidence in the existing operation audit owner, no new DDL."""
    version: Literal[1]
    request_digest: Digest
    stage_id: Identifier
    owner_id: Identifier
    path: CanonicalPath
    parent: PathIdentity
    identity: PathIdentity | None
    state: Literal["intent", "acquired", "disposed"]
    disposition: Literal["absent", "unlinked", "replaced"] | None

    @model_validator(mode="after")
    def check_state(self):
        if (type(self.version) is not int
                or (self.state == "intent" and self.identity is not None)
                or (self.state == "acquired" and self.identity is None)
                or ((self.state == "disposed") != (self.disposition is not None))
                or (self.identity is None and self.disposition not in {None, "absent"})):
            raise TeamMoveError("team_move_staging_fact_invalid")
        return self


class Progress(Closed):
    agents: tuple[AgentProgress, AgentProgress]
    canonical_after: tuple[CanonicalPath, ...] = ()
    publication: PublicationIdentity | None = None
    recovery: Annotated[tuple[RecoveryIdentity, ...], Field(max_length=64)] = ()

    @model_validator(mode="after")
    def check_order(self) -> Progress:
        if (tuple(a.agent_id for a in self.agents) != ("consultant_codex", "consultant_head")
                or tuple(sorted(set(self.canonical_after))) != self.canonical_after):
            raise TeamMoveError("team_move_progress_closure")
        return self


class Diagnostic(Closed):
    # Codes/IDs only; never arbitrary exceptions containing secrets.
    code: Identifier
    owner_ids: Annotated[tuple[Identifier, ...], Field(max_length=64)] = ()


class TerminalReceipt(Closed):
    version: Literal[1]
    operation_id: Identifier
    request_digest: Digest
    phase: Literal["refused", "abandoned"]
    state_revision: Positive
    committed: Literal[False]
    diagnostic: Diagnostic


class CompletedReceipt(Closed):
    """Saved facts produced only by D's native completion readback owner."""
    version: Literal[1]
    operation_id: Identifier
    request_digest: Digest
    phase: Literal["complete"]
    state_revision: Positive
    committed: Literal[True]
    progress_digest: Digest
    current_inputs_digest: Digest
    settled_census_digest: Digest
    publication: PublicationIdentity


TEAM_MOVE_SCHEMA_SQL = """CREATE TABLE team_move_operations (
    operation_id TEXT NOT NULL PRIMARY KEY CHECK(length(operation_id) BETWEEN 1 AND 256),
    schema_version INTEGER NOT NULL CHECK(typeof(schema_version)='integer' AND schema_version=1),
    org_slug TEXT NOT NULL CHECK(length(org_slug) BETWEEN 1 AND 256),
    principal TEXT NOT NULL CHECK(principal='founder'),
    operation_key TEXT NOT NULL UNIQUE CHECK(length(operation_key) BETWEEN 1 AND 256),
    request_bytes BLOB NOT NULL CHECK(typeof(request_bytes)='blob' AND length(request_bytes) BETWEEN 1 AND 2097152),
    request_digest TEXT NOT NULL CHECK(length(request_digest)=64 AND request_digest NOT GLOB '*[^0-9a-f]*'),
    admission_bytes BLOB NOT NULL CHECK(typeof(admission_bytes)='blob' AND length(admission_bytes) BETWEEN 1 AND 2097152),
    phase TEXT NOT NULL CHECK(phase IN ('waiting','commit_decided','fenced','reset','canonical','context','publish','repair_required','complete','refused','abandoned')),
    state_revision INTEGER NOT NULL CHECK(typeof(state_revision)='integer' AND state_revision BETWEEN 1 AND 9223372036854775807),
    active_slot INTEGER UNIQUE CHECK(active_slot IS NULL OR (typeof(active_slot)='integer' AND active_slot=1)),
    decision_bytes BLOB CHECK(decision_bytes IS NULL OR (typeof(decision_bytes)='blob' AND length(decision_bytes) BETWEEN 1 AND 2097152)),
    progress_bytes BLOB NOT NULL CHECK(typeof(progress_bytes)='blob' AND length(progress_bytes) BETWEEN 1 AND 2097152),
    diagnostic_bytes BLOB CHECK(diagnostic_bytes IS NULL OR (typeof(diagnostic_bytes)='blob' AND length(diagnostic_bytes) BETWEEN 1 AND 16384)),
    diagnostic_digest TEXT CHECK(diagnostic_digest IS NULL OR (length(diagnostic_digest)=64 AND diagnostic_digest NOT GLOB '*[^0-9a-f]*')),
    resume_phase TEXT CHECK(resume_phase IN ('commit_decided','fenced','reset','canonical','context','publish')),
    receipt_bytes BLOB CHECK(receipt_bytes IS NULL OR (typeof(receipt_bytes)='blob' AND length(receipt_bytes) BETWEEN 1 AND 16384)),
    receipt_digest TEXT CHECK(receipt_digest IS NULL OR (length(receipt_digest)=64 AND receipt_digest NOT GLOB '*[^0-9a-f]*')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    CHECK((phase IN ('complete','refused','abandoned') AND active_slot IS NULL AND receipt_bytes IS NOT NULL AND receipt_digest IS NOT NULL)
       OR (phase NOT IN ('complete','refused','abandoned') AND active_slot=1 AND receipt_bytes IS NULL AND receipt_digest IS NULL)),
    CHECK((phase IN ('waiting','refused','abandoned') AND decision_bytes IS NULL)
       OR (phase NOT IN ('waiting','refused','abandoned') AND decision_bytes IS NOT NULL)),
    CHECK((diagnostic_bytes IS NULL AND diagnostic_digest IS NULL)
       OR (diagnostic_bytes IS NOT NULL AND diagnostic_digest IS NOT NULL)),
    CHECK((phase='repair_required' AND resume_phase IS NOT NULL AND diagnostic_bytes IS NOT NULL)
       OR (phase!='repair_required' AND resume_phase IS NULL))
)"""


def _time(value: str) -> None:
    if datetime.fromisoformat(value).tzinfo is None:
        raise TeamMoveError("team_move_naive_time")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _digest(raw: bytes, *, reader_budget=None) -> str:
    _reader_reserve(reader_budget, 0)
    return _reader_sha256(raw, budget=reader_budget).hexdigest()


def _bytes(value: Closed, *, reader_budget=None) -> bytes:
    _reader_reserve(reader_budget, 0)
    raw = _reader_encode(_reader_json_dumps(_reader_dump(value, mode="json", budget=reader_budget), sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False, budget=reader_budget), "utf-8", budget=reader_budget)
    if not 0 < len(raw) <= MAX_BYTES:
        raise TeamMoveError("team_move_byte_bound")
    return raw


def _decode(model: type[Closed], raw: bytes, *, reader_budget=None) -> Any:
    """Strict JSON + exact canonical bytes rejects duplicates and coercion."""
    _reader_work((_reader_reserve(reader_budget, 80) or (raw,)), reader_budget, copies=2)
    if type(raw) is not bytes or not 0 < len(raw) <= MAX_BYTES:
        raise TeamMoveError("team_move_byte_bound")
    _reader_json_work(raw, reader_budget)
    value = model.model_validate_json(raw, strict=True)
    if _bytes(value, reader_budget=reader_budget) != raw:
        raise TeamMoveError("team_move_noncanonical_bytes")
    return value


def _inventory(conn: sqlite3.Connection, *, reader_budget=None) -> tuple:
    _reader_reserve(reader_budget, 0)
    objects = _reader_tuple((_reader_tuple(r, budget=reader_budget) for r in _reader_rows(
        conn, "SELECT type,name,tbl_name,sql FROM sqlite_master "
        "WHERE tbl_name='team_move_operations' OR lower(name) GLOB 'team_move_*' ORDER BY name LIMIT 5"
    , budget=reader_budget)), budget=reader_budget)
    if len(objects) > 4:
        raise TeamMoveError("team_move_schema_mismatch")
    metadata = _reader_tuple((_reader_tuple((_reader_tuple(r, budget=reader_budget) for r in _reader_rows(conn, f"PRAGMA {p}(team_move_operations)", budget=reader_budget)), budget=reader_budget)
                     for p in ("table_xinfo", "foreign_key_list", "index_list")), budget=reader_budget)
    indexes = _reader_tuple(((_reader_reserve(reader_budget, 96) or (r[1], _reader_tuple((_reader_tuple(x, budget=reader_budget) for x in _reader_rows(
        conn, "SELECT * FROM pragma_index_xinfo(?) ORDER BY seqno", (_reader_reserve(reader_budget, 80) or (r[1],))
    , budget=reader_budget)), budget=reader_budget))) for r in _reader_rows(conn, "PRAGMA index_list(team_move_operations)", budget=reader_budget)), budget=reader_budget)
    return (_reader_reserve(reader_budget, 112) or (objects, metadata, indexes))


@lru_cache(maxsize=1)
def _cached_reference_inventory() -> tuple:
    conn = sqlite3.connect(":memory:")
    try:
        conn.execute(TEAM_MOVE_SCHEMA_SQL)
        return _inventory(conn)
    finally:
        conn.close()


def _reference_inventory(*, reader_budget=None):
    if reader_budget is None:
        return _cached_reference_inventory()
    # Supplied allowances acquire the independent literal reference afresh;
    # an eager cached tuple cannot stand in for charged acquisition.
    reference = sqlite3.connect(":memory:")
    try:
        reference.execute(TEAM_MOVE_SCHEMA_SQL)
        reference.execute('BEGIN')
        return _inventory(reference, reader_budget=reader_budget)
    finally:
        reference.close()


def _validate_row(row: dict, org_slug: str | None, *, reader_budget=None) -> None:
    _reader_work((_reader_reserve(reader_budget, 96) or (row,org_slug)), reader_budget, copies=2)
    request = _decode(MoveRequest, row["request_bytes"], reader_budget=reader_budget)
    admission = _decode(FounderAdmission, row["admission_bytes"], reader_budget=reader_budget)
    progress = _decode(Progress, row["progress_bytes"], reader_budget=reader_budget)
    phase = Phase(row["phase"])
    if _reader_any_true((type(row[k]) is not str or re.fullmatch(r"[A-Za-z0-9_-]{1,256}", row[k]) is None
           for k in ("operation_id", "operation_key", "org_slug")), budget=reader_budget):
        raise TeamMoveError("team_move_record_identity")
    _time(row["created_at"])
    _time(row["updated_at"])
    if (row["schema_version"] != 1 or row["principal"] != "founder"
            or row["org_slug"] != request.org_slug or row["org_slug"] != admission.org_slug
            or (org_slug is not None and org_slug != row["org_slug"])
            or _digest(row["request_bytes"], reader_budget=reader_budget) != row["request_digest"]
            or type(row["state_revision"]) is not int or not 1 <= row["state_revision"] <= MAX_REVISION
            or row["operation_id"] != _operation_id(row["org_slug"], row["operation_key"], reader_budget=reader_budget)
            or ((phase in TERMINAL) != (row["active_slot"] is None))
            or (phase not in TERMINAL and (type(row["active_slot"]) is not int or row["active_slot"] != 1))):
        raise TeamMoveError("team_move_record_mismatch")
    if row["decision_bytes"] is not None:
        decision = _decode(ForwardDecision, row["decision_bytes"], reader_budget=reader_budget)
        if (decision.request_digest != row["request_digest"]
                or decision.revalidated_inputs_digest != _digest(_bytes(request.expected, reader_budget=reader_budget), reader_budget=reader_budget)
                or phase in _reader_set((_reader_reserve(reader_budget, 112) or (Phase.WAITING, Phase.REFUSED, Phase.ABANDONED)), budget=reader_budget)):
            raise TeamMoveError("team_move_decision_mismatch")
        if progress.publication is not None and (
            progress.publication.writer_invocation_id != decision.writer_invocation_id
            or progress.publication.namespace != request.expected.authority_namespace
            or progress.publication.generation <= request.expected.authority_generation
        ):
            raise TeamMoveError("team_move_publication_owner")
        previous_owner = decision.writer_invocation_id
        claims = _reader_set(budget=reader_budget)
        for recovery in progress.recovery:
            if recovery.previous_owner_id != previous_owner or recovery.claim_id in claims:
                raise TeamMoveError("team_move_recovery_owner_chain")
            previous_owner = recovery.owner_id
            _reader_add(claims, recovery.claim_id, budget=reader_budget)
    elif phase not in _reader_set((_reader_reserve(reader_budget, 112) or (Phase.WAITING, Phase.REFUSED, Phase.ABANDONED)), budget=reader_budget) or progress != _empty_progress(reader_budget=reader_budget):
        raise TeamMoveError("team_move_missing_forward_decision")
    if phase is Phase.REPAIR_REQUIRED:
        if row["resume_phase"] not in FORWARD_PHASES or row["diagnostic_bytes"] is None:
            raise TeamMoveError("team_move_recovery_state")
    elif row["resume_phase"] is not None:
        raise TeamMoveError("team_move_recovery_state")
    if row["diagnostic_bytes"] is not None:
        _decode(Diagnostic, row["diagnostic_bytes"], reader_budget=reader_budget)
        if _digest(row["diagnostic_bytes"], reader_budget=reader_budget) != row["diagnostic_digest"]:
            raise TeamMoveError("team_move_diagnostic_digest")
    elif row["diagnostic_digest"] is not None:
        raise TeamMoveError("team_move_diagnostic_digest")
    if phase in TERMINAL:
        receipt = _decode(CompletedReceipt if phase is Phase.COMPLETE else TerminalReceipt, row["receipt_bytes"], reader_budget=reader_budget)
        if (type(receipt.version) is not int or receipt.operation_id != row["operation_id"] or receipt.request_digest != row["request_digest"]
                or receipt.state_revision != row["state_revision"] or receipt.phase != phase
                or _digest(row["receipt_bytes"], reader_budget=reader_budget) != row["receipt_digest"]
                or (phase is not Phase.COMPLETE and _bytes(receipt.diagnostic, reader_budget=reader_budget) != row["diagnostic_bytes"])):
            raise TeamMoveError("team_move_receipt_mismatch")
        if phase is Phase.COMPLETE and (receipt.progress_digest != _digest(row["progress_bytes"], reader_budget=reader_budget)
                or receipt.publication != progress.publication):
            raise TeamMoveError("team_move_completion_evidence_mismatch")
    elif row["receipt_bytes"] is not None or row["receipt_digest"] is not None:
        raise TeamMoveError("team_move_nonterminal_receipt")
    _validate_stage(phase, progress, row["resume_phase"], reader_budget=reader_budget)


def _validate_stage(phase: Phase, progress: Progress, resume: str | None, *, reader_budget=None) -> None:
    _reader_work((_reader_reserve(reader_budget, 112) or (phase,progress,resume)), reader_budget, copies=2)
    effective = Phase(resume) if phase is Phase.REPAIR_REQUIRED else (Phase.PUBLISH if phase is Phase.COMPLETE else phase)
    if progress.publication is None and effective in FORWARD_PHASES[1:]:
        raise TeamMoveError("team_move_missing_publication_identity")
    if effective in FORWARD_PHASES[3:] and _reader_any_true((a.reset is None for a in progress.agents), budget=reader_budget):
        raise TeamMoveError("team_move_missing_reset")
    if effective in FORWARD_PHASES[4:] and len(progress.canonical_after) != 3:
        raise TeamMoveError("team_move_missing_canonical")
    if effective is Phase.PUBLISH and _reader_any_true((a.context is None for a in progress.agents), budget=reader_budget):
        raise TeamMoveError("team_move_missing_context")
    if effective in _reader_set((_reader_reserve(reader_budget, 112) or (Phase.WAITING, Phase.REFUSED, Phase.ABANDONED)), budget=reader_budget) and progress != _empty_progress(reader_budget=reader_budget):
        raise TeamMoveError("team_move_precommit_mutation")
    # Progress cannot assert steps from a future stage.
    if effective in FORWARD_PHASES[:2] and _reader_any_true((a.reset or a.context for a in progress.agents), budget=reader_budget):
        raise TeamMoveError("team_move_early_reset")
    if effective in _reader_set((_reader_reserve(reader_budget, 112) or (Phase.COMMIT_DECIDED, Phase.FENCED, Phase.RESET)), budget=reader_budget) and progress.canonical_after:
        raise TeamMoveError("team_move_early_canonical")
    if effective in FORWARD_PHASES[:4] and _reader_any_true((a.context for a in progress.agents), budget=reader_budget):
        raise TeamMoveError("team_move_early_context")


def _transition_resume_phase(current: Phase, phase: Phase, resume: str | None, *, reader_budget=None) -> str | None:
    """Finite edge contract shared by committed-history reads and CAS writes.

    Unchanged replay returns before this function in the writer; it is never
    another audit transition. Recovery ownership/progress is validated separately.
    """
    _reader_work((_reader_reserve(reader_budget, 112) or (current,phase,resume)), reader_budget, copies=2)
    if current in TERMINAL:
        raise TeamMoveError("team_move_terminal")
    if phase is Phase.COMPLETE:
        if current is not Phase.PUBLISH:
            raise TeamMoveError("team_move_illegal_transition")
        return None
    if current is Phase.WAITING:
        if phase not in _reader_set((_reader_reserve(reader_budget, 112) or (Phase.COMMIT_DECIDED, Phase.REFUSED, Phase.ABANDONED)), budget=reader_budget):
            raise TeamMoveError("team_move_illegal_transition")
    elif current is Phase.REPAIR_REQUIRED:
        if phase is Phase.REPAIR_REQUIRED:
            pass
        elif phase.value == resume:
            resume = None
        else:
            raise TeamMoveError("team_move_illegal_recovery")
    elif phase is Phase.REPAIR_REQUIRED:
        resume = current.value
    elif phase != current and (
        current not in FORWARD_PHASES[:-1] or phase != FORWARD_PHASES[FORWARD_PHASES.index(current) + 1]
    ):
        raise TeamMoveError("team_move_illegal_transition")
    return resume


def validate_team_move_schema(conn: sqlite3.Connection, *, org_slug: str | None = None, reader_budget=None) -> bool:
    """Absent is compatible; partial/extra/unsupported or corrupt fails closed.

    The bounded scan refuses overflow; it never treats a truncated inventory as
    complete. Call within the DB owner's transaction or coherent read snapshot.
    """
    _reader_work((_reader_reserve(reader_budget, 80) or (org_slug,)), reader_budget, copies=2)
    try:
        _reader_reserve(reader_budget, 112)
        inventory = _inventory(conn, reader_budget=reader_budget)
        if not inventory[0]:
            return False
        if inventory != _reference_inventory(reader_budget=reader_budget):
            raise TeamMoveError("team_move_schema_mismatch")
        total = 0
        count = 0
        deadline = time.monotonic() + 2.0
        # Size scan precedes body decoding, so hostile BLOBs cannot allocate
        # unbounded Python objects. All history rows are validated, not active only.
        columns = _reader_tuple((c[1] for c in _reader_rows(conn, "SELECT * FROM pragma_table_xinfo('team_move_operations')", budget=reader_budget)), budget=reader_budget)
        sizes = " + ".join(f'coalesce(length(CAST("{c}" AS BLOB)),0)' for c in columns)
        for size, in _reader_rows(conn, f"SELECT {sizes} FROM team_move_operations", budget=reader_budget):
            count += 1
            total += size
            if count > MAX_ROWS or total > MAX_SCAN_BYTES or time.monotonic() > deadline:
                raise TeamMoveError("team_move_scan_bound")
        for row in _reader_rows(conn, "SELECT * FROM team_move_operations ORDER BY operation_id", budget=reader_budget):
            record = _reader_dict(zip(columns, row), budget=reader_budget)
            _validate_row(record, org_slug, reader_budget=reader_budget)
            total += _validate_audit_history(conn, record, budget=MAX_SCAN_BYTES - total, reader_budget=reader_budget)
            _stages, size = _staging_history(conn, record, budget=MAX_SCAN_BYTES - total, reader_budget=reader_budget)
            total += size
            if time.monotonic() > deadline:
                raise TeamMoveError("team_move_scan_deadline")
        return True
    except ((ValueError, TypeError, sqlite3.DatabaseError)) as exc:
        if isinstance(exc, TeamMoveError):
            raise
        raise TeamMoveError("team_move_unavailable") from exc


def _unique_object(pairs: list[tuple[str, Any]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise TeamMoveError("team_move_duplicate_audit_key")
        result[key] = value
    return result


def _validate_audit_history(conn: sqlite3.Connection, row: dict, *, budget: int, reader_budget=None) -> int:
    """Bind immutable request/provenance and current progress to existing audit.

    The writer validates new state structurally before its uncommitted audit;
    readers validate the complete committed pair. No synthetic history repair.
    """
    _reader_work((_reader_reserve(reader_budget, 80) or (row,)), reader_budget, copies=2)
    parameters = (_reader_reserve(reader_budget, 96) or ("config:team-move:" + row["operation_id"], MAX_ROWS + 1))
    predicate = " FROM audit_log WHERE task_id=? AND action IN ('team_move_reserved','team_move_transition') ORDER BY id LIMIT ?"
    count, total = 0, 0
    for size, in conn.execute("SELECT coalesce(length(CAST(payload AS BLOB)),0)" + predicate, parameters):
        count += 1
        total += size
        if count > MAX_ROWS or total > budget or size > MAX_BYTES:
            raise TeamMoveError("team_move_audit_bound")
    if count != row["state_revision"]:
        raise TeamMoveError("team_move_audit_history")
    audits = conn.execute("SELECT agent,action,payload" + predicate, parameters)
    last = None
    resume = None
    recovered = False
    empty_progress_digest = _digest(_bytes(_empty_progress(reader_budget=reader_budget), reader_budget=reader_budget), reader_budget=reader_budget)
    admission = _reader_json(row["admission_bytes"], budget=reader_budget)
    keys = _reader_set((_reader_reserve(reader_budget, 208) or ("operation_id", "request_digest", "phase", "state_revision", "admission", "progress_digest", "decision_digest", "diagnostic_digest", "receipt_digest")), budget=reader_budget)
    for revision, audit in enumerate(audits, 1):
        if type(audit[2]) is not str or len(_reader_encode(audit[2], budget=reader_budget)) > MAX_BYTES:
            raise TeamMoveError("team_move_audit_bound")
        payload = _reader_json(audit[2], object_pairs_hook=_unique_object, budget=reader_budget)
        if (not isinstance(payload, dict) or _reader_set(payload, budget=reader_budget) != keys or audit[0] != "founder"
                or audit[1] != ("team_move_reserved" if revision == 1 else "team_move_transition")
                or type(payload["state_revision"]) is not int or payload["state_revision"] != revision
                or payload["operation_id"] != row["operation_id"]
                or payload["request_digest"] != row["request_digest"] or payload["admission"] != admission
                or (revision == 1 and payload["phase"] != "waiting")):
            raise TeamMoveError("team_move_audit_history")
        phase = Phase(payload["phase"])
        if _reader_any_true((value is not None and (
                not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None
        ) for value in (_reader_reserve(reader_budget, 128) or (payload["progress_digest"], payload["decision_digest"],
                        payload["diagnostic_digest"], payload["receipt_digest"]))), budget=reader_budget):
            raise TeamMoveError("team_move_audit_digest")
        precommit = phase in _reader_set((_reader_reserve(reader_budget, 112) or (Phase.WAITING, Phase.REFUSED, Phase.ABANDONED)), budget=reader_budget)
        if (payload["progress_digest"] is None
                or (precommit and payload["progress_digest"] != empty_progress_digest)
                or ((payload["decision_digest"] is None) != precommit)
                or ((payload["receipt_digest"] is not None) != (phase in TERMINAL))
                or (phase in _reader_set((_reader_reserve(reader_budget, 112) or (Phase.REFUSED, Phase.ABANDONED, Phase.REPAIR_REQUIRED)), budget=reader_budget) and payload["diagnostic_digest"] is None)
                or (revision == 1 and payload["diagnostic_digest"] is not None)):
            raise TeamMoveError("team_move_audit_lifecycle")
        if last is not None:
            previous = Phase(last["phase"])
            resume = _transition_resume_phase(previous, phase, resume, reader_budget=reader_budget)
            if last["decision_digest"] is not None and payload["decision_digest"] != last["decision_digest"]:
                raise TeamMoveError("team_move_audit_decision_rewrite")
            if previous is Phase.REPAIR_REQUIRED and phase is not Phase.REPAIR_REQUIRED:
                recovered = True
            if phase is previous and _reader_all_true((payload[k] == last[k] for k in (
                "progress_digest", "decision_digest", "diagnostic_digest", "receipt_digest"
            )), budget=reader_budget):
                raise TeamMoveError("team_move_audit_unchanged_replay")
        last = payload
    if (last is None or last["phase"] != row["phase"]
            or last["progress_digest"] != _digest(row["progress_bytes"], reader_budget=reader_budget)
            or last["decision_digest"] != (_digest(row["decision_bytes"], reader_budget=reader_budget) if row["decision_bytes"] else None)
            or last["diagnostic_digest"] != row["diagnostic_digest"]
            or last["receipt_digest"] != row["receipt_digest"]
            or resume != row["resume_phase"]):
        raise TeamMoveError("team_move_audit_state")
    # The audit preserves digests, not old progress bodies. A recovery return
    # requires retained ownership evidence in the final monotonic progress;
    # _validate_row authenticates its chain shape, not native quiescence.
    if recovered and not _decode(Progress, row["progress_bytes"], reader_budget=reader_budget).recovery:
        raise TeamMoveError("team_move_recovery_owner_required")
    return total


def _staging_history(conn, row, *, budget=MAX_SCAN_BYTES, reader_budget=None):
    """Strict old/new reader: no staging rows is valid historical data.

    New completion additionally requires exact terminal staging coverage. Old
    progress/receipts/audit payloads keep their original bytes and interpretation.
    """
    _reader_work((_reader_reserve(reader_budget, 80) or (row,)), reader_budget, copies=2)
    owners = _reader_set(budget=reader_budget)
    if row["decision_bytes"] is not None:
        _reader_add(owners, _decode(ForwardDecision, row["decision_bytes"], reader_budget=reader_budget).writer_invocation_id, budget=reader_budget)
        _reader_update(owners, (r.owner_id for r in _decode(Progress, row["progress_bytes"], reader_budget=reader_budget).recovery), budget=reader_budget)
    stages, total = (_reader_reserve(reader_budget, 96) or (_reader_reserve(reader_budget, 64) or {}, 0))
    protocol = _reader_all(conn, "SELECT id,agent,payload FROM audit_log WHERE task_id=? AND action='team_move_staging_protocol' ORDER BY id LIMIT 2", (_reader_reserve(reader_budget, 80) or ("config:team-move:" + row["operation_id"],)), budget=reader_budget)
    if len(protocol) > 1:
        raise TeamMoveError("team_move_staging_protocol_ambiguous")
    if protocol:
        raw = protocol[0]["payload"]
        if type(raw) is not str or len(_reader_encode(raw, budget=reader_budget)) > 16384:
            raise TeamMoveError("team_move_staging_history_bound")
        payload = _reader_json(raw, object_pairs_hook=_unique_object, budget=reader_budget)
        if (protocol[0]["agent"] != "founder" or not isinstance(payload, dict)
                or _reader_set(payload, budget=reader_budget) != _reader_set((_reader_reserve(reader_budget, 128) or ("version", "request_digest", "owner_id", "state_revision")), budget=reader_budget)
                or type(payload["version"]) is not int or payload["version"] != 1
                or payload["request_digest"] != row["request_digest"] or payload["owner_id"] not in owners):
            raise TeamMoveError("team_move_staging_protocol_unknown")
        previous = _reader_one(conn, "SELECT payload FROM audit_log WHERE task_id=? AND action='team_move_transition' AND id<? ORDER BY id DESC LIMIT 1", (_reader_reserve(reader_budget, 96) or ("config:team-move:" + row["operation_id"], protocol[0]["id"])), budget=reader_budget)
        prior = None if previous is None else _reader_json(previous["payload"], object_pairs_hook=_unique_object, budget=reader_budget)
        if (type(payload["state_revision"]) is not int or prior is None
                or prior["phase"] != "canonical" or prior["state_revision"] != payload["state_revision"]):
            raise TeamMoveError("team_move_staging_protocol_transition_unknown")
        total += len(_reader_encode(raw, budget=reader_budget))
        if total > budget:
            raise TeamMoveError("team_move_staging_history_bound")
    rows = _reader_rows(conn, "SELECT id,agent,payload FROM audit_log WHERE task_id=? AND action='team_move_staging' ORDER BY id LIMIT ?",
        (_reader_reserve(reader_budget, 96) or ("config:team-move:" + row["operation_id"], 577)), budget=reader_budget)
    for index, audit in enumerate(rows):
        if not protocol or audit["id"] <= protocol[0]["id"]:
            raise TeamMoveError("team_move_staging_protocol_missing")
        raw = audit["payload"]
        if type(raw) is not str or len(_reader_encode(raw, budget=reader_budget)) > 16384:
            raise TeamMoveError("team_move_staging_history_bound")
        total += len(_reader_encode(raw, budget=reader_budget))
        if index >= 576 or total > budget:
            raise TeamMoveError("team_move_staging_history_bound")
        value = _reader_json(raw, object_pairs_hook=_unique_object, budget=reader_budget)
        fact = _decode(StagingFact, _reader_encode(_reader_json_dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, budget=reader_budget), budget=reader_budget), reader_budget=reader_budget)
        if audit["agent"] != "founder" or fact.request_digest != row["request_digest"] or fact.owner_id not in owners:
            raise TeamMoveError("team_move_staging_owner_unknown")
        prior = stages.get(fact.stage_id)
        if prior is None:
            if fact.state != "intent":
                raise TeamMoveError("team_move_staging_intent_missing")
        elif (prior.state == "disposed" or fact.state == "intent"
                or (prior.state == "acquired" and fact.state != "disposed")
                or (prior.state == "intent" and fact.state == "disposed" and (fact.disposition != "absent" or fact.identity is not None))
                or _reader_any_true((getattr(prior, k) != getattr(fact, k) for k in ("version", "request_digest", "stage_id", "owner_id", "path", "parent")), budget=reader_budget)
                or (prior.identity is not None and fact.identity != prior.identity)):
            raise TeamMoveError("team_move_staging_history_rewrite")
        image = next(i for i in _decode(MoveRequest, row["request_bytes"], reader_budget=reader_budget).files if i.path == fact.path)
        if fact.identity is not None and (fact.identity.mode != image.identity.mode
                or fact.identity.device != image.identity.device or fact.identity.device != fact.parent.device):
            raise TeamMoveError("team_move_staging_identity_mismatch")
        _reader_put(fact, stages, fact.stage_id, budget=reader_budget)
    if protocol:
        progress = _decode(Progress, row["progress_bytes"], reader_budget=reader_budget)
        replaced = _reader_set((f.path for f in stages.values() if f.disposition == "replaced"), budget=reader_budget)
        if not _reader_set(progress.canonical_after, budget=reader_budget) <= replaced:
            raise TeamMoveError("team_move_staging_progress_unproven")
        if row["phase"] == "complete" and (_reader_any_true((f.state != "disposed" for f in stages.values()), budget=reader_budget) or len(replaced) != 3):
            raise TeamMoveError("team_move_staging_cleanup_unproven")
    return (_reader_reserve(reader_budget, 96) or (stages, total))


@contextmanager
def _transaction(db: Any) -> Iterator[sqlite3.Connection]:
    """Existing connection owner: short DB-only unit with total rollback."""
    with db._lock:
        conn = db._conn
        if conn.in_transaction:
            raise TeamMoveError("team_move_caller_transaction")
        conn.execute("BEGIN IMMEDIATE")
        try:
            yield conn
            conn.commit()
        except BaseException as primary:
            try:
                conn.rollback()
            except BaseException as cleanup:
                primary.add_note('team move transaction rollback failed: ' + type(cleanup).__name__)
            raise


def install_team_move_schema(db: Any, *, org_slug: str, reader_budget=None) -> None:
    """Native org release owner only; never generic DB or diagnostic recovery.

    The release-reference builder also uses this literal on disposable org
    references. No migration of existing columns or relation repair is allowed.
    """
    if re.fullmatch(r"[A-Za-z0-9_-]{1,256}", org_slug) is None:
        raise TeamMoveError("team_move_org_identity")
    with _transaction(db) as conn:
        # A complete org workflow schema is the existing org-extension
        # discriminator. Generic Database/runtime-audit lack it and refuse.
        from runtime.infrastructure.workflow_schema import validate_workflow_schema
        validate_workflow_schema(conn, expected_org_slug=org_slug, budget=reader_budget)
        installed = validate_team_move_schema(conn, org_slug=org_slug, reader_budget=reader_budget)
        if installed and _reader_one(conn, "SELECT 1 FROM team_move_operations WHERE active_slot=1", budget=reader_budget):
            raise TeamMoveError("team_move_startup_recovery_required")
        if not installed:
            _reader_work(TEAM_MOVE_SCHEMA_SQL, reader_budget)
            conn.execute(TEAM_MOVE_SCHEMA_SQL)
        validate_team_move_schema(conn, org_slug=org_slug, reader_budget=reader_budget)


def _operation_id(org_slug: str, key: str, *, reader_budget=None) -> str:
    # Delimited canonical tuple, not task/session identity or logical-ID parsing.
    _reader_work((_reader_reserve(reader_budget, 96) or (org_slug,key)), reader_budget, copies=2)
    return "team_move_" + _digest(_reader_encode(_reader_json_dumps(_reader_reserve(reader_budget, 320) or [org_slug, key], separators=(",", ":"), budget=reader_budget), budget=reader_budget), reader_budget=reader_budget)


def _empty_progress(*, reader_budget=None) -> Progress:
    _reader_work((), reader_budget, copies=2)
    return Progress(agents=(_reader_reserve(reader_budget, 96) or (AgentProgress(agent_id="consultant_codex"), AgentProgress(agent_id="consultant_head"))))


def _monotonic(old: Progress, new: Progress) -> None:
    for previous, current in zip(old.agents, new.agents):
        if any(getattr(previous, k) is not None and getattr(previous, k) != getattr(current, k)
               for k in ("reset", "context")):
            raise TeamMoveError("team_move_progress_rewrite")
    if (not set(old.canonical_after) <= set(new.canonical_after)
            or new.recovery[:len(old.recovery)] != old.recovery
            or (old.publication is not None and old.publication != new.publication)):
        raise TeamMoveError("team_move_progress_rewrite")


class TeamMoveStore:
    """Private infrastructure seam, not an admission or domain mutation service."""
    def __init__(self, db: Any, org_slug: str) -> None:
        self.db, self.org_slug = db, org_slug

    def _require(self, conn: sqlite3.Connection) -> None:
        if not validate_team_move_schema(conn, org_slug=self.org_slug):
            raise TeamMoveError("team_move_schema_absent")

    def _row(self, conn: sqlite3.Connection, operation_id: str) -> dict:
        row = conn.execute("SELECT * FROM team_move_operations WHERE operation_id=?", (operation_id,)).fetchone()
        if row is None:
            raise TeamMoveError("team_move_unknown_operation")
        return dict(row)

    def read(self, *, operation_key: str | None = None, operation_id: str | None = None) -> dict | None:
        """Private read; raw definition images must not be returned by public show."""
        if operation_key is not None and operation_id is not None:
            raise TeamMoveError("team_move_ambiguous_selector")
        with self.db.coherent_read_view() as conn:
            if not validate_team_move_schema(conn, org_slug=self.org_slug):
                return None
            if operation_id is not None:
                row = conn.execute("SELECT * FROM team_move_operations WHERE operation_id=?", (operation_id,)).fetchone()
            elif operation_key is None:
                row = conn.execute("SELECT * FROM team_move_operations WHERE active_slot=1").fetchone()
            else:
                row = conn.execute("SELECT * FROM team_move_operations WHERE operation_key=?", (operation_key,)).fetchone()
            return dict(row) if row is not None else None

    def completed_request(self, request_digest: str) -> dict | None:
        """Read a prior COMPLETE for native no-write satisfied verification."""
        with self.db.coherent_read_view() as conn:
            if not validate_team_move_schema(conn, org_slug=self.org_slug):
                return None
            rows = conn.execute("SELECT * FROM team_move_operations WHERE request_digest=? AND phase='complete' ORDER BY operation_id",
                (request_digest,)).fetchall()
            return dict(rows[0]) if rows else None

    def staging(self, row):
        with self.db.coherent_read_view() as conn:
            self._require(conn)
            if conn.execute("SELECT 1 FROM audit_log WHERE task_id=? AND action='team_move_staging_protocol'",
                    ("config:team-move:" + row["operation_id"],)).fetchone() is None:
                raise TeamMoveError("team_move_legacy_staging_ownership_unknown")
            if self._row(conn, row["operation_id"]) != row:
                raise TeamMoveError("team_move_stale_staging_owner")
            return tuple(_staging_history(conn, row)[0].values())

    def record_staging(self, row, fact: StagingFact):
        with _transaction(self.db) as conn:
            self._require(conn)
            if self._row(conn, row["operation_id"]) != row or row["phase"] != "canonical":
                raise TeamMoveError("team_move_stale_staging_owner")
            progress = _decode(Progress, row["progress_bytes"])
            owner = progress.recovery[-1].owner_id if progress.recovery else _decode(ForwardDecision, row["decision_bytes"]).writer_invocation_id
            if fact.state == "intent" and fact.owner_id != owner:
                raise TeamMoveError("team_move_staging_owner_unknown")
            self.db.insert_audit_log_uncommitted(task_id="config:team-move:" + row["operation_id"], agent="founder",
                action="team_move_staging", payload=fact.model_dump(mode="json"))
            _staging_history(conn, row)

    def _audit(self, row: dict, action: str) -> None:
        self.db.insert_audit_log_uncommitted(
            task_id="config:team-move:" + row["operation_id"], agent="founder", action=action,
            payload={"operation_id": row["operation_id"], "request_digest": row["request_digest"],
                     "phase": row["phase"], "state_revision": row["state_revision"],
                     "admission": json.loads(row["admission_bytes"]),
                     "progress_digest": _digest(row["progress_bytes"]),
                     "decision_digest": _digest(row["decision_bytes"]) if row["decision_bytes"] else None,
                     "diagnostic_digest": row["diagnostic_digest"],
                     "receipt_digest": row["receipt_digest"]},
        )

    def reserve(self, *, operation_key: str, request_bytes: bytes,
                admission: FounderAdmission) -> dict:
        request = _decode(MoveRequest, request_bytes)
        if (request.org_slug != self.org_slug or admission.org_slug != self.org_slug
                or not operation_key or len(operation_key) > 256
                or any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-" for c in operation_key)):
            raise TeamMoveError("team_move_reservation_identity")
        with _transaction(self.db) as conn:
            self._require(conn)
            existing = conn.execute("SELECT * FROM team_move_operations WHERE operation_key=?", (operation_key,)).fetchone()
            if existing is not None:
                if existing["request_bytes"] != request_bytes:
                    raise TeamMoveError("team_move_changed_body")
                return dict(existing)  # No revision, audit, reset or publisher work.
            if conn.execute("SELECT 1 FROM team_move_operations WHERE active_slot=1").fetchone() is not None:
                raise TeamMoveError("team_move_active_conflict")
            operation_id = _operation_id(self.org_slug, operation_key)
            stamp = _now()
            conn.execute("""INSERT INTO team_move_operations
                (operation_id,schema_version,org_slug,principal,operation_key,request_bytes,request_digest,
                 admission_bytes,phase,state_revision,active_slot,progress_bytes,created_at,updated_at)
                VALUES (?,1,?,'founder',?,?,?,?,'waiting',1,1,?,?,?)""",
                (operation_id, self.org_slug, operation_key, request_bytes, _digest(request_bytes),
                 _bytes(admission), _bytes(_empty_progress()), stamp, stamp))
            row = self._row(conn, operation_id)
            _validate_row(row, self.org_slug)
            self._audit(row, "team_move_reserved")
            return row

    def transition(self, operation_id: str, *, expected_revision: int, phase: Phase,
                   progress: Progress | None = None, decision: ForwardDecision | None = None,
                   diagnostic: Diagnostic | None = None) -> dict:
        """CAS and persist only state; no reset/files/publication/census here.

        D must integrate genuine owner evidence validation to enable completion.
        A caller can never set COMPLETE or release a forward-committed hold.
        """
        if type(expected_revision) is not int or not 1 <= expected_revision < MAX_REVISION or not isinstance(phase, Phase):
            raise TeamMoveError("team_move_revision")
        with _transaction(self.db) as conn:
            self._require(conn)
            row = self._row(conn, operation_id)
            current = Phase(row["phase"])
            old_progress = _decode(Progress, row["progress_bytes"])
            next_progress = progress if progress is not None else old_progress
            next_decision = _bytes(decision) if decision is not None else row["decision_bytes"]
            next_diagnostic = _bytes(diagnostic) if diagnostic is not None else row["diagnostic_bytes"]
            if (current == phase and row["progress_bytes"] == _bytes(next_progress)
                    and row["decision_bytes"] == next_decision and row["diagnostic_bytes"] == next_diagnostic):
                # Lost-response replay of an unchanged state/receipt is read-only.
                if expected_revision not in {row["state_revision"], row["state_revision"] - 1}:
                    raise TeamMoveError("team_move_stale_revision")
                return row
            if current in TERMINAL or phase is Phase.COMPLETE:
                raise TeamMoveError("team_move_completion_unavailable" if phase is Phase.COMPLETE else "team_move_terminal")
            if row["state_revision"] != expected_revision:
                raise TeamMoveError("team_move_stale_revision")
            if row["decision_bytes"] is not None and next_decision != row["decision_bytes"]:
                raise TeamMoveError("team_move_decision_rewrite")
            _monotonic(old_progress, next_progress)
            resume = _transition_resume_phase(current, phase, row["resume_phase"])
            if current is Phase.REPAIR_REQUIRED and phase is not Phase.REPAIR_REQUIRED and not next_progress.recovery:
                raise TeamMoveError("team_move_recovery_owner_required")
            if phase in {Phase.REFUSED, Phase.ABANDONED}:
                if next_decision is not None or next_progress != _empty_progress() or diagnostic is None:
                    raise TeamMoveError("team_move_unsafe_release")
                receipt = TerminalReceipt(version=1, operation_id=operation_id,
                    request_digest=row["request_digest"], phase=phase.value,
                    state_revision=expected_revision + 1, committed=False, diagnostic=diagnostic)
                receipt_bytes = _bytes(receipt)
            else:
                receipt_bytes = None
            updated = dict(row, phase=phase.value, state_revision=expected_revision + 1,
                active_slot=None if phase in TERMINAL else 1, decision_bytes=next_decision,
                progress_bytes=_bytes(next_progress), diagnostic_bytes=next_diagnostic,
                diagnostic_digest=_digest(next_diagnostic) if next_diagnostic else None,
                resume_phase=resume, receipt_bytes=receipt_bytes,
                receipt_digest=_digest(receipt_bytes) if receipt_bytes else None, updated_at=_now())
            _validate_row(updated, self.org_slug)
            changed = conn.execute("""UPDATE team_move_operations SET phase=?,state_revision=?,active_slot=?,
                decision_bytes=?,progress_bytes=?,diagnostic_bytes=?,diagnostic_digest=?,resume_phase=?,receipt_bytes=?,receipt_digest=?,updated_at=?
                WHERE operation_id=? AND state_revision=?""", tuple(updated[k] for k in (
                    "phase", "state_revision", "active_slot", "decision_bytes", "progress_bytes", "diagnostic_bytes",
                    "diagnostic_digest", "resume_phase", "receipt_bytes", "receipt_digest", "updated_at")) + (operation_id, expected_revision))
            if changed.rowcount != 1:
                raise TeamMoveError("team_move_stale_revision")
            self._audit(updated, "team_move_transition")
            if current is Phase.RESET and phase is Phase.CANONICAL:
                owner = next_progress.recovery[-1].owner_id if next_progress.recovery else _decode(ForwardDecision, next_decision).writer_invocation_id
                self.db.insert_audit_log_uncommitted(task_id="config:team-move:" + operation_id, agent="founder",
                    action="team_move_staging_protocol", payload={"version": 1, "request_digest": row["request_digest"], "owner_id": owner, "state_revision": updated["state_revision"]})
            return updated


    def _complete_uncommitted(self, row: dict, receipt: CompletedReceipt) -> dict:
        """Private leaf of D's native readback transaction; no public transition.

        D owns the gate/DB transaction and revalidates its native captures before
        entering this leaf. A caller success Boolean is never accepted here.
        """
        if not self.db._lock._is_owned() or not self.db._conn.in_transaction:
            raise TeamMoveError("team_move_completion_transaction_owner")
        self._require(self.db._conn)
        current = self._row(self.db._conn, row["operation_id"])
        if current != row or current["phase"] != "publish" or receipt.state_revision != current["state_revision"] + 1:
            raise TeamMoveError("team_move_stale_completion")
        stages = _staging_history(self.db._conn, current)[0].values()
        if (any(f.state != "disposed" for f in stages)
                or {f.path for f in stages if f.disposition == "replaced"} != {f.path for f in _decode(MoveRequest, current["request_bytes"]).files}):
            raise TeamMoveError("team_move_staging_cleanup_unproven")
        raw = _bytes(receipt)
        updated = dict(current, phase="complete", state_revision=receipt.state_revision,
            active_slot=None, receipt_bytes=raw, receipt_digest=_digest(raw), updated_at=_now())
        _validate_row(updated, self.org_slug)
        changed = self.db._conn.execute(
            "UPDATE team_move_operations SET phase='complete',state_revision=?,active_slot=NULL,receipt_bytes=?,receipt_digest=?,updated_at=? WHERE operation_id=? AND state_revision=? AND phase='publish'",
            (updated["state_revision"], raw, updated["receipt_digest"], updated["updated_at"], updated["operation_id"], row["state_revision"]),
        ).rowcount
        if changed != 1:
            raise TeamMoveError("team_move_stale_completion")
        self._audit(updated, "team_move_transition")
        return updated


def inspect_team_move_database(path, *, org_slug: str, require_existing=False, reader_capabilities=None):
    """Schema owner's coherent read BEFORE Database compatibility writers.

    Reads complete optional layout/history, never installs/upgrades or repairs.
    A missing org DB preserves fresh/absent-layout startup. Unknown redirects,
    SQLite/layout/state errors refuse; no roster inference grants compatibility.
    """
    import os
    import stat
    from pathlib import Path
    from urllib.parse import quote
    path = Path(path)
    conn = None
    primary = None
    try:
        # Only the first observation may establish genuine initial absence.
        try:
            info = path.lstat()
        except FileNotFoundError as exc:
            if require_existing:
                raise TeamMoveError("team_move_startup_previously_unavailable_database_missing") from exc
            return None
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != os.getuid()
                or path.resolve(strict=True) != path):
            raise TeamMoveError("team_move_startup_database_identity_unknown")
        conn = sqlite3.connect("file:" + quote(str(path), safe="/") + "?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        after = path.lstat()
        if (info.st_dev, info.st_ino) != (after.st_dev, after.st_ino):
            raise TeamMoveError("team_move_startup_database_identity_changed")
        conn.execute("BEGIN")
        present = validate_team_move_schema(conn, org_slug=org_slug)
        # Empty installed layouts need a compatible reader too. An old-reader
        # check cannot infer absence from the lack of an active operation.
        if present and reader_capabilities is not None and "online-team-move@3" not in reader_capabilities:
            raise TeamMoveError("team_move_incompatible_reader")
        row = conn.execute("SELECT * FROM team_move_operations WHERE active_slot=1").fetchone() if present else None
        # Raced loss/redirect is unknown even for an absent optional layout.
        after = path.lstat()
        if path.resolve(strict=True) != path or (info.st_dev, info.st_ino) != (after.st_dev, after.st_ino):
            raise TeamMoveError("team_move_startup_database_identity_changed")
        return None if row is None else dict(row)
    except (OSError, sqlite3.Error) as exc:
        primary = exc
        raise _inspection_error("team_move_startup_database_unavailable", exc) from exc
    except BaseException as exc:
        primary = exc
        raise
    finally:
        if conn is not None:
            try:
                conn.close()
            except sqlite3.Error as cleanup:
                if primary is None:
                    raise _inspection_error("team_move_startup_database_close_failed", cleanup) from cleanup
                primary.add_note("team move inspection close failed: " + type(cleanup).__name__)


def inspect_team_move_org(root, *, org_slug, require_database=False, reader_capabilities=None):
    """Normalize per-entry acquisition too; Path.is_dir hides unknown errors."""
    import os
    import stat
    from pathlib import Path
    root = Path(root)
    try:
        info = root.lstat()
        if stat.S_ISLNK(info.st_mode) or root.resolve(strict=True) != root or info.st_uid != os.getuid():
            raise TeamMoveError("team_move_startup_org_identity_unknown")
        if not stat.S_ISDIR(info.st_mode):
            return False, None
        from runtime.orchestrator._paths import OrgPaths
        row = inspect_team_move_database(OrgPaths(root).db_path, org_slug=org_slug,
            require_existing=require_database, reader_capabilities=reader_capabilities)
        after = root.lstat()
        # A mode=ro SQLite WAL read can create -wal/-shm sidecars, changing
        # directory timestamps/size without replacing the directory. Compare
        # identity and access metadata, and recheck canonical path resolution.
        if (root.resolve(strict=True) != root
                or (info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_gid)
                != (after.st_dev, after.st_ino, after.st_mode, after.st_uid, after.st_gid)):
            raise TeamMoveError("team_move_startup_org_identity_changed")
        return True, row
    except OSError as exc:
        raise _inspection_error("team_move_startup_org_unavailable", exc) from exc


def _inspection_error(code, error):
    """Bounded diagnostic retains native errno/SQLite classification and cause."""
    if isinstance(error, OSError) and type(error.errno) is int:
        code += "_errno_" + str(abs(error.errno))
    elif isinstance(error, sqlite3.Error):
        name = getattr(error, "sqlite_errorname", None)
        if isinstance(name, str) and re.fullmatch(r"[A-Z_0-9]{1,80}", name):
            code += "_" + name
    return TeamMoveError(code)
