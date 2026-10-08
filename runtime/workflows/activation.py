"""Founder-authorized exact-version activation and authentic initial task admission."""
from __future__ import annotations

import base64
import json
import sqlite3
from datetime import datetime, timezone
from typing import Annotated, Literal, Any

from pydantic import BaseModel, ConfigDict, Field, StrictInt, ValidationError

from runtime.infrastructure.task_attachment_store import TaskAttachmentStore
from runtime.infrastructure.workflow_schema import validate_workflow_schema
from runtime.models import TaskRecord
from runtime.orchestrator._paths import OrgPaths
from runtime.workflows.draft_dispatch import append_event_uncommitted, canonical_bytes, digest
from runtime.workflows.templates import WorkflowTemplatePrincipal, WorkflowTemplateStore, WorkflowTemplateVersion, _document_contract


CONTEXT_LIMIT = 1024 * 1024
Role = Literal["product-lead", "founder", "implementer", "tester"]
Action = Literal["draft-document", "submit-immutable-document", "collect-review",
                 "approve-planning-input", "return-to-author"]
Token = Annotated[str, Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._:-]+$")]
SHA256 = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
AbstractRole = Annotated[str, Field(min_length=1, max_length=63, pattern=r"^[a-z][a-z0-9-]*$")]


class WorkflowActivationError(ValueError):
    def __init__(self, code: str, *, owner: str | None = None, required_action: str | None = None) -> None:
        self.code = code
        self.owner = owner
        self.required_action = required_action
        super().__init__(code)


class ClosedRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class TemplatePin(ClosedRecord):
    identity_id: str = Field(min_length=1, max_length=256)
    version: StrictInt = Field(gt=0)
    definition_digest: SHA256


class AuthorityPin(ClosedRecord):
    namespace: str = Field(min_length=1, max_length=256)
    generation: StrictInt = Field(gt=0)
    snapshot_digest: SHA256


class RoleBinding(ClosedRecord):
    kind: Literal["agent", "human"]
    principal: Token
    team: str | None = Field(max_length=63)


class RoleMap(ClosedRecord):
    product_lead: RoleBinding = Field(alias="product-lead")
    founder: RoleBinding
    implementer: RoleBinding
    tester: RoleBinding


class Replacements(ClosedRecord):
    product_lead: list[RoleBinding] = Field(alias="product-lead", max_length=16)
    founder: list[RoleBinding] = Field(max_length=0)
    implementer: list[RoleBinding] = Field(max_length=16)
    tester: list[RoleBinding] = Field(max_length=16)


class Scope(ClosedRecord):
    brief: str = Field(min_length=1, max_length=16384)


class TaskInput(ClosedRecord):
    kind: Literal["task-attachment"]
    task_id: Token
    storage_key: str = Field(min_length=1, max_length=256)
    sha256: SHA256
    recipients: list[Role] = Field(min_length=1, max_length=4)


class ThreadInput(ClosedRecord):
    kind: Literal["thread-attachment"]
    thread_id: Token
    attachment_id: Token
    sha256: SHA256
    recipients: list[Role] = Field(min_length=1, max_length=4)


class ActivationRequest(ClosedRecord):
    operation_key: Token
    instance_id: str = Field(min_length=1, max_length=63, pattern=r"^[a-z][a-z0-9-]*$")
    expected_activation_revision: StrictInt = Field(ge=0, le=0)
    template: TemplatePin
    authority: AuthorityPin
    scope: Scope
    bindings: RoleMap
    eligible_replacements: Replacements
    allowed_actions: list[Action] = Field(min_length=1, max_length=5)
    inputs: list[Annotated[TaskInput | ThreadInput, Field(discriminator="kind")]] = Field(max_length=32)


class DocumentTaskInput(TaskInput):
    recipients: list[AbstractRole] = Field(min_length=1, max_length=4)


class DocumentThreadInput(ThreadInput):
    recipients: list[AbstractRole] = Field(min_length=1, max_length=4)


class DocumentActivationRequest(ActivationRequest):
    format: Literal["workflow-activation-request@2"]
    bindings: dict[AbstractRole, RoleBinding] = Field(min_length=2, max_length=4)
    eligible_replacements: dict[AbstractRole, Annotated[list[RoleBinding], Field(max_length=16)]] = Field(min_length=2, max_length=4)
    inputs: list[Annotated[DocumentTaskInput | DocumentThreadInput, Field(discriminator="kind")]] = Field(max_length=32)


class ActivatedBy(ClosedRecord):
    principal_kind: Literal["human"]
    principal_id: Literal["founder"]
    proof_kind: Literal["founder_bearer"]


class ActivatedTemplate(TemplatePin):
    version_id: str
    compiler_pin: str
    validator_pin: str
    source_pin: str


class ActivationEligibility(ClosedRecord):
    eligible: bool
    blockers: list[str]


class ActivationReceipt(ClosedRecord):
    """Original immutable admission identity with a separate current projection.

    No private context bytes, bearer data or opaque host controls are exposed.
    Pending execution does not mean submission, review or document approval.
    """

    activation_id: str
    instance_id: str
    instance_reference: str
    activation_revision: StrictInt
    root_task_id: str
    intent_id: str
    template: ActivatedTemplate
    authority: AuthorityPin
    bindings: RoleMap
    eligible_replacements: Replacements
    allowed_actions: list[Action]
    scope_digest: SHA256
    context_digest: SHA256
    activated_by: ActivatedBy
    created_at: str
    original_request_digest: SHA256
    replayed: bool
    state: Literal["queued", "claimed", "running", "uncertain", "cancelled", "failed", "completed"]
    execution_started: bool = Field(description="A genuine bound host handle was observed; session registration alone is insufficient.")
    pending: bool
    reconciliation_required: bool
    cancellation_requested: bool
    current_eligibility: ActivationEligibility
    responsible_owner: str


class DocumentActivationReceipt(ActivationReceipt):
    format: Literal["workflow-activation-receipt@2"]
    bindings: dict[AbstractRole, RoleBinding]
    eligible_replacements: dict[AbstractRole, list[RoleBinding]]


def parse_request(value: object) -> ActivationRequest | DocumentActivationRequest:
    try:
        model = DocumentActivationRequest if isinstance(value, dict) and "format" in value else ActivationRequest
        request = model.model_validate(value)
    except ValidationError as exc:
        raise WorkflowActivationError("workflow_activation_invalid_request") from exc
    if (not request.scope.brief.strip() or len(set(request.allowed_actions)) != len(request.allowed_actions)
            or "draft-document" not in request.allowed_actions):
        raise WorkflowActivationError("workflow_activation_invalid_request")
    if isinstance(request, DocumentActivationRequest) and any(
            len(set(item.recipients)) != len(item.recipients) for item in request.inputs):
        raise WorkflowActivationError("workflow_activation_invalid_request")
    return request


def _contract_for_request(request: dict, template: WorkflowTemplateVersion) -> dict | None:
    contract = _document_contract(template)
    if (request.get("format") == "workflow-activation-request@2") != (contract is not None):
        raise WorkflowActivationError("workflow_activation_invalid_request")
    return contract


def _author_role(request: dict, template: WorkflowTemplateVersion) -> str:
    contract = _contract_for_request(request, template)
    return contract["author_role"] if contract is not None else "product-lead"


def _id(kind: str, *parts: object) -> str:
    return f"{kind}:{digest(canonical_bytes(list(parts)))}"


def _template_pin(template: WorkflowTemplateVersion) -> dict:
    return dict(identity_id=template.identity_id, version_id=template.version_id,
                version=template.version, definition_digest=template.definition_digest,
                compiler_pin=template.compiler_pin, validator_pin=template.validator_pin,
                source_pin=template.source_pin)


def _authorization(*, org_slug: str, instance_id: str, activation_id: str, task_id: str,
                   request: dict, request_digest: str, template: WorkflowTemplateVersion,
                   timestamp: str) -> dict:
    contract = _contract_for_request(request, template)
    return dict(format="workflow-authorization@2" if contract is not None else "workflow-authorization@1",
                namespace=f"org/{org_slug}/workflow-instance/{instance_id}",
                instance_id=instance_id, activation_id=activation_id, activation_revision=1,
                root_task_id=task_id, original_request=request, request_digest=request_digest,
                actor=dict(principal_kind="human", principal_id="founder", proof_kind="founder_bearer"),
                created_at=timestamp, template=_template_pin(template), authority=request["authority"])


def _binding(*, instance_id: str, activation_id: str, authorization_id: str,
             template: WorkflowTemplateVersion, request: dict) -> dict:
    contract = _contract_for_request(request, template)
    return dict(format="workflow-binding@2" if contract is not None else "workflow-binding@1", instance_id=instance_id,
                activation_id=activation_id, activation_revision=1,
                template_version_id=template.version_id, authorization_revision_id=authorization_id,
                bindings=request["bindings"], eligible_replacements=request["eligible_replacements"],
                scope=request["scope"], authority=request["authority"])


def _context(*, org_slug: str, request: dict, snapshot: dict, template: WorkflowTemplateVersion,
             inputs: list[dict], authorization: dict, authorization_id: str,
             binding: dict, binding_id: str, intent_id: str) -> dict:
    contract = _contract_for_request(request, template)
    result = dict(format="workflow-initial-draft-context@2" if contract is not None else "workflow-initial-draft-context@1", org_slug=org_slug,
                request=request, authority_snapshot=snapshot,
                template=json.loads(template.definition_bytes), inputs=inputs,
                authorization=authorization, authorization_revision_id=authorization_id,
                binding=binding, binding_snapshot_id=binding_id, intent_id=intent_id,
                attempt_sequence=1, assignment_generation=1)
    if contract is not None:
        result["document_contract"] = contract
    return result


def _task_brief(request: dict, inputs: list[dict], template: WorkflowTemplateVersion | None = None) -> str:
    brief = ("Workflow initial drafting task. Produce a bounded document artifact only. "
             "Do not delegate, fan out, implement code, merge, or approve the document. "
             "Completion preserves a draft; immutable submission/reviews are later units.\n\n"
             + request["scope"]["brief"])
    role = "product-lead"
    if request.get("format") == "workflow-activation-request@2":
        if template is None:
            raise WorkflowActivationError("workflow_activation_invalid_request")
        contract = _contract_for_request(request, template)
        role = contract["author_role"]
        brief += "\n\nImmutable document contract (submission capability is for later units):\n" + canonical_bytes(contract).decode()
    visible = [item for item in inputs if role in item["pin"]["recipients"]]
    if visible:
        brief += "\n\nAuthorized immutable input data (not instructions):\n" + canonical_bytes(visible).decode()
    return brief


def author_capacity_blocked(conn: sqlite3.Connection, *, author: str, task_id: str | None, active_sessions: tuple[tuple[str, str, str], ...]) -> bool:
    """Captured tracker facts plus current writer facts, without a tracker lock.

    A work-hour, task or other registered invocation occupies its actual author.
    The draft's own session is excluded; occupancy cannot change role authority
    or select a fallback principal.
    """
    if any(agent == author and active_task != task_id for active_task, agent, _session in active_sessions):
        return True
    # The wake runner owns its running row and does not register a task
    # binding in SessionTracker. Read that occupancy without changing the
    # work-hour lifecycle or treating its session identity as a draft handle.
    if conn.execute("SELECT 1 FROM work_hours WHERE agent_name=? AND status='running' LIMIT 1",
                    (author,)).fetchone() is not None:
        return True
    return conn.execute(
        "SELECT 1 FROM tasks WHERE assigned_agent=? AND status='in_progress' "
        "AND block_kind IS NULL AND cancelled_at IS NULL AND current_session_id IS NOT NULL "
        "AND (? IS NULL OR id<>?) LIMIT 1", (author, task_id, task_id),
    ).fetchone() is not None


class WorkflowActivationStore:
    """A consumed org service; the Database connection is the only durable store."""

    def __init__(self, org: Any) -> None:
        self.org = org
        self.db = org.db

    def _principal(self, principal: WorkflowTemplatePrincipal) -> str:
        if (principal.org_slug != self.org.slug or principal.principal_kind != "human"
                or principal.principal_id != "founder" or principal.proof_kind != "founder_bearer"):
            raise WorkflowActivationError("role_binding_not_authorized")
        principal.revalidate()
        return principal.operation_principal

    def _template(self, request: ActivationRequest) -> WorkflowTemplateVersion:
        with self.db._lock:
            template = WorkflowTemplateStore._read_version(
                self.db._conn, identity_id=request.template.identity_id, version=request.template.version,
            )
        if (not template.namespace.startswith(f"org/{self.org.slug}/team/")
                or template.definition_digest != request.template.definition_digest):
            raise WorkflowActivationError("role_binding_not_authorized")
        _contract_for_request(request.model_dump(by_alias=True), template)
        return template

    def _roles(self, request: ActivationRequest, snapshot: dict,
               template: WorkflowTemplateVersion | None = None) -> dict:
        if isinstance(request, DocumentActivationRequest):
            if template is None:
                raise WorkflowActivationError("workflow_activation_invalid_request")
            frozen = request.model_dump(by_alias=True)
            contract = _contract_for_request(frozen, template)
            bindings = frozen["bindings"]
            replacements = frozen["eligible_replacements"]
            kinds = contract["role_kinds"]
            if set(bindings) != set(kinds) or set(replacements) != set(kinds):
                raise WorkflowActivationError("role_binding_not_authorized")
            capabilities = {"draft-document", "submit-immutable-document", "collect-review", "approve-planning-input"}
            if contract["request_changes"] is not None:
                capabilities.add("return-to-author")
            if not set(request.allowed_actions) <= capabilities:
                raise WorkflowActivationError("role_binding_not_authorized")
            if any(not set(item.recipients) <= set(kinds) for item in request.inputs):
                raise WorkflowActivationError("workflow_activation_invalid_request")
            agents = {item["name"]: item for item in snapshot["agents"] if item["status"] == "active"}
            teams = {item["name"]: item for item in snapshot["teams"]}
            if len({item["principal"] for item in bindings.values()}) != len(bindings):
                raise WorkflowActivationError("role_binding_not_authorized")
            for role, kind in kinds.items():
                if kind == "human":
                    if (bindings[role] != {"kind": "human", "principal": "founder", "team": None}
                            or replacements[role]):
                        raise WorkflowActivationError("role_binding_not_authorized")
                    continue
                candidates = [bindings[role], *replacements[role]]
                if len({candidate["principal"] for candidate in candidates}) != len(candidates):
                    raise WorkflowActivationError("role_binding_not_authorized")
                for candidate in candidates:
                    agent = agents.get(candidate["principal"])
                    team = teams.get(candidate["team"])
                    if (candidate["kind"] != "agent" or agent is None or team is None
                            or agent["team"] != candidate["team"]
                            or candidate["principal"] not in [team["manager"], *team["workers"]]
                            or candidate["principal"] in [bindings[key]["principal"] for key in kinds if key != role]):
                        raise WorkflowActivationError("role_binding_not_authorized")
            return bindings
        bindings = request.bindings.model_dump(by_alias=True)
        replacements = request.eligible_replacements.model_dump(by_alias=True)
        agents = {item["name"]: item for item in snapshot["agents"] if item["status"] == "active"}
        teams = {item["name"]: item for item in snapshot["teams"]}
        if bindings["founder"] != {"kind": "human", "principal": "founder", "team": None}:
            raise WorkflowActivationError("role_binding_not_authorized")
        selected = [bindings[role]["principal"] for role in ("product-lead", "implementer", "tester")]
        if len(set(selected)) != 3:
            raise WorkflowActivationError("role_binding_not_authorized")
        for role in ("product-lead", "implementer", "tester"):
            candidates = [bindings[role], *replacements[role]]
            if len({candidate["principal"] for candidate in candidates}) != len(candidates):
                raise WorkflowActivationError("role_binding_not_authorized")
            for candidate in candidates:
                agent = agents.get(candidate["principal"])
                team = teams.get(candidate["team"])
                if (candidate["kind"] != "agent" or agent is None or team is None
                        or agent["team"] != candidate["team"]
                        or candidate["principal"] not in [team["manager"], *team["workers"]]):
                    raise WorkflowActivationError("role_binding_not_authorized")
                # A replacement cannot erase contributor independence. Later
                # replacement admission remains a separate U4/U5 operation.
                other = [bindings[key]["principal"] for key in bindings if key != role]
                if candidate["principal"] in other:
                    raise WorkflowActivationError("role_binding_not_authorized")
        return bindings

    def _inputs(self, request: ActivationRequest, bindings: dict) -> list[dict]:
        result = []
        for item in request.inputs:
            pin = item.model_dump()
            if len(set(item.recipients)) != len(item.recipients):
                raise WorkflowActivationError("workflow_activation_invalid_request")
            try:
                if isinstance(item, TaskInput):
                    if self.db.get_task(item.task_id) is None:
                        raise KeyError("missing_task")
                    record = self.db.get_task_attachment(item.task_id, item.storage_key)
                    if record is None:
                        record = next((row for row in self.db.resolve_ancestor_attachments(item.task_id)
                                       if row.storage_key == item.storage_key), None)
                    if record is None:
                        raise KeyError("missing_attachment")
                    content = TaskAttachmentStore(OrgPaths(self.org.root).task_attachments_dir).read(record.storage_key)
                else:
                    from runtime.daemon.routes.threads import _attachment_store
                    if (self.db.get_thread(item.thread_id) is None
                            or self.db.get_thread_scoped_attachment(item.thread_id, item.attachment_id) is None):
                        raise KeyError("missing_attachment")
                    for role in item.recipients:
                        candidate = bindings[role]
                        if candidate["kind"] == "agent" and not self.db.is_thread_participant(item.thread_id, candidate["principal"]):
                            raise WorkflowActivationError("workflow_activation_input_unavailable", owner="founder",
                                required_action="Select inputs already visible to every selected recipient")
                    content = _attachment_store(self.org).read(item.thread_id, item.attachment_id)
            except (KeyError, ValueError, OSError) as exc:
                if isinstance(exc, WorkflowActivationError):
                    raise
                raise WorkflowActivationError("workflow_activation_input_unavailable", owner="founder",
                                              required_action="Restore the same-org source attachment and exact digest") from exc
            if digest(content) != item.sha256:
                raise WorkflowActivationError("workflow_activation_input_unavailable", owner="founder",
                                              required_action="Supply the digest of the authorized immutable source bytes")
            result.append({"pin": pin, "bytes_base64": base64.b64encode(content).decode("ascii")})
        return result

    def _replay(self, conn: sqlite3.Connection, *, actor: str, request: ActivationRequest, request_digest: str) -> dict | None:
        operation = conn.execute(
            "SELECT * FROM workflow_activation_operations WHERE org_slug=? AND principal=? AND operation_key=?",
            (self.org.slug, actor, request.operation_key),
        ).fetchone()
        if operation is None:
            return None
        receipt = self._closure(conn, operation["activation_id"], actor=actor)
        if operation["request_digest"] != request_digest:
            raise WorkflowActivationError("workflow_activation_operation_conflict")
        receipt["replayed"] = True
        return receipt

    async def activate(self, *, principal: WorkflowTemplatePrincipal, request: object) -> dict:
        actor = self._principal(principal)
        request = parse_request(request)
        raw = canonical_bytes(request.model_dump(by_alias=True))
        request_digest = digest(raw)
        # Historical receipt authorization and immutable closure precede all
        # mutable NEW-admission discovery, eligibility, cutover and CAS checks.
        with self.db._lock:
            replay = self._replay(self.db._conn, actor=actor, request=request, request_digest=request_digest)
        if replay is not None:
            return self._project(replay)
        template = self._template(request)
        capture = self.org.workflow_authority.capture_admission()
        if request.authority.model_dump() != dict(namespace=capture.ready.namespace,
                generation=capture.ready.generation, snapshot_digest=capture.ready.snapshot_digest):
            raise WorkflowActivationError("workflow_activation_authority_stale")
        bindings = self._roles(request, json.loads(capture.ready.snapshot_bytes), template)
        author_role = _author_role(request.model_dump(by_alias=True), template)
        inputs = self._inputs(request, bindings)
        # Tracker binding leases precede Database ownership in callback
        # admission. Do not invert that order with a tracker scan in the writer.
        active_sessions = tuple(self.org.sessions.iter_active())
        async with self.org.workflow_authority._async_writer_lock:
            async with self.org.db_lock:
                with self.org.workflow_authority.admission_writer(capture) as conn:
                    principal.revalidate()
                    replay = self._replay(conn, actor=actor, request=request, request_digest=request_digest)
                    if replay is not None:
                        receipt = replay
                    else:
                        cutover = conn.execute("SELECT state FROM workflow_cutover_state WHERE singleton=1").fetchone()
                        if cutover is None or cutover[0] != "enabled":
                            raise WorkflowActivationError("workflow_new_runs_disabled")
                        instance_id = _id("workflow-instance", self.org.slug, request.instance_id)
                        if conn.execute("SELECT 1 FROM workflow_instances WHERE id=?", (instance_id,)).fetchone():
                            raise WorkflowActivationError("workflow_activation_cas_stale")
                        author = bindings[author_role]["principal"]
                        if author_capacity_blocked(conn, author=author, task_id=None, active_sessions=active_sessions):
                            raise WorkflowActivationError("workflow_activation_author_pending")
                        task_id = self.db.next_task_id()
                        timestamp = datetime.now(timezone.utc).isoformat()
                        namespace = f"org/{self.org.slug}/workflow-instance/{instance_id}"
                        activation_id = _id("workflow-activation", instance_id, 1, request_digest)
                        frozen = json.loads(raw)
                        authorization = _authorization(org_slug=self.org.slug, instance_id=instance_id,
                            activation_id=activation_id, task_id=task_id, request=frozen,
                            request_digest=request_digest, template=template, timestamp=timestamp)
                        authorization_bytes = canonical_bytes(authorization)
                        authorization_id = _id("workflow-authorization", instance_id, 1, digest(authorization_bytes))
                        bound = _binding(instance_id=instance_id, activation_id=activation_id,
                            authorization_id=authorization_id, template=template, request=frozen)
                        binding_bytes = canonical_bytes(bound)
                        binding_id = _id("workflow-binding", instance_id, digest(binding_bytes))
                        admission = dict(org_slug=self.org.slug, instance_id=instance_id, activation_id=activation_id,
                            activation_revision=1, attempt_sequence=1, predecessor_intent_id=None,
                            admission_principal=actor, operation_key=request.operation_key, request_digest=request_digest)
                        intent_id = digest(canonical_bytes(admission))
                        # Only bounded canonicalization of pre-captured bytes occurs
                        # under the writer. The FINAL envelope includes the actual
                        # allocated root, admission time and immutable identities.
                        context_bytes = canonical_bytes(_context(org_slug=self.org.slug, request=frozen,
                            snapshot=json.loads(capture.ready.snapshot_bytes), template=template, inputs=inputs,
                            authorization=authorization, authorization_id=authorization_id,
                            binding=bound, binding_id=binding_id, intent_id=intent_id))
                        if len(context_bytes) > CONTEXT_LIMIT:
                            raise WorkflowActivationError("workflow_activation_context_too_large", owner="founder",
                                required_action="Reduce the canonical context to at most 1MiB")
                        context_id = _id("workflow-context", instance_id, digest(context_bytes))
                        task = TaskRecord(id=task_id, assigned_agent=bindings[author_role]["principal"],
                                          team=bindings[author_role]["team"], brief=_task_brief(frozen, inputs, template), task_type="subtask")
                        self.db._insert_task_uncommitted(task)
                        conn.execute("INSERT INTO workflow_authorization_revisions VALUES (?,?,?,?,?,?,?)",
                            (authorization_id, namespace, 1, authorization_bytes, digest(authorization_bytes),
                             template.source_pin, timestamp))
                        conn.execute("INSERT INTO workflow_active_authorizations VALUES (?,?)", (namespace, authorization_id))
                        conn.execute("INSERT INTO workflow_binding_snapshots VALUES (?,?,?,?,?,?)",
                            (binding_id, template.version_id, authorization_id, binding_bytes, digest(binding_bytes), timestamp))
                        conn.execute("INSERT INTO workflow_contexts VALUES (?,?,?,?,?,?)",
                            (context_id, binding_id, context_bytes, digest(context_bytes), "founder-activation", instance_id))
                        conn.execute("INSERT INTO workflow_instances VALUES (?,?,?,?,?,?)",
                            (instance_id, binding_id, context_id, task_id, actor, "draft"))
                        # The shipped source validator binds this column to the
                        # exact template namespace. The captured coordinator
                        # namespace stays org-scoped in authorization/binding bytes.
                        conn.execute("INSERT INTO workflow_activations VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                            (activation_id, instance_id, 1, template.identity_id, template.version_id,
                             template.namespace, capture.ready.generation, capture.ready.snapshot_digest,
                             request_digest, actor, "active", timestamp))
                        conn.execute("INSERT INTO workflow_active_activations VALUES (?,?,?)", (instance_id, activation_id, 1))
                        conn.execute("INSERT INTO workflow_activation_operations VALUES (?,?,?,?,?)",
                            (self.org.slug, actor, request.operation_key, request_digest, activation_id))
                        scope = canonical_bytes(dict(assigned_agent=task.assigned_agent, team=task.team, brief=task.brief))
                        values = dict(id=intent_id, instance_id=instance_id, activation_id=activation_id,
                            activation_revision=1, attempt_sequence=1, predecessor_intent_id=None, admission_kind="initial",
                            admission_principal=actor, operation_key=request.operation_key, request_bytes=raw,
                            request_digest=request_digest, task_id=task_id, context_id=context_id,
                            binding_snapshot_id=binding_id, assigned_principal=task.assigned_agent, assignment_generation=1,
                            authority_namespace=template.namespace, authority_generation=capture.ready.generation,
                            authority_digest=capture.ready.snapshot_digest, task_scope_bytes=scope,
                            task_scope_digest=digest(scope), effect_key=f"workflow-initial-draft:{instance_id}:1",
                            host_execution_key=f"workflow-draft-host:{intent_id}", is_current=1, state="queued",
                            cancellation_requested=0, claim_token=None, claim_owner=None, host_launch_started=0,
                            host_execution_id=None, session_id=None, final_result_id=None, recovery_owner="workflow_recovery",
                            last_error=None, created_at=timestamp, updated_at=timestamp)
                        conn.execute(f"INSERT INTO workflow_draft_dispatch_intents ({','.join(values)}) "
                                     f"VALUES ({','.join('?' for _ in values)})", tuple(values.values()))
                        append_event_uncommitted(conn, org_slug=self.org.slug, intent_id=intent_id, kind="admitted", before=None)
                        validate_workflow_schema(conn, expected_org_slug=self.org.slug)
                        receipt = self._closure(conn, activation_id, actor=actor)
        return self._project(receipt)

    def _closure(self, conn: sqlite3.Connection, activation_id: str, *, actor: str) -> dict:
        # Caller authenticated before any receipt lookup. A foreign actor/org
        # receives no private record, even if it guesses an activation ID.
        operation = conn.execute("SELECT * FROM workflow_activation_operations WHERE activation_id=? AND org_slug=? AND principal=?",
                                 (activation_id, self.org.slug, actor)).fetchone()
        if operation is None:
            raise WorkflowActivationError("workflow_activation_not_found")
        try:
            validate_workflow_schema(conn, expected_org_slug=self.org.slug)
            activation = dict(conn.execute("SELECT * FROM workflow_activations WHERE id=?", (activation_id,)).fetchone())
            instance = dict(conn.execute("SELECT * FROM workflow_instances WHERE id=?", (activation["instance_id"],)).fetchone())
            intent = dict(conn.execute("SELECT * FROM workflow_draft_dispatch_intents WHERE activation_id=? AND attempt_sequence=1", (activation_id,)).fetchone())
            binding = dict(conn.execute("SELECT * FROM workflow_binding_snapshots WHERE id=?", (intent["binding_snapshot_id"],)).fetchone())
            authorization = dict(conn.execute("SELECT * FROM workflow_authorization_revisions WHERE id=?", (binding["authorization_revision_id"],)).fetchone())
            context = dict(conn.execute("SELECT * FROM workflow_contexts WHERE id=?", (intent["context_id"],)).fetchone())
            grant = json.loads(authorization["authority_bytes"])
            request = parse_request(json.loads(intent["request_bytes"]))
            frozen = request.model_dump(by_alias=True)
            namespace = f"org/{self.org.slug}/workflow-instance/{instance['id']}"
            template = WorkflowTemplateStore._read_version(conn, identity_id=request.template.identity_id,
                                                          version=request.template.version, expected_version_id=activation["template_version_id"])
            timestamp = datetime.fromisoformat(activation["created_at"])
            if (operation["request_digest"] != intent["request_digest"] or intent["request_digest"] != activation["request_digest"]
                    or activation["activated_by"] != actor or intent["admission_principal"] != actor
                    or operation["operation_key"] != intent["operation_key"] or request.operation_key != intent["operation_key"]
                    or instance["owner_principal"] != actor or authorization["namespace"] != namespace
                    or authorization["revision"] != 1 or activation["activation_revision"] != 1
                    or authorization["source_pin"] != template.source_pin
                    or authorization["created_at"] != activation["created_at"]
                    or binding["created_at"] != activation["created_at"]
                    or intent["created_at"] != activation["created_at"]
                    or timestamp.tzinfo is None or timestamp.utcoffset().total_seconds() != 0
                    or intent["admission_kind"] != "initial" or intent["attempt_sequence"] != 1
                    or intent["assignment_generation"] != 1 or intent["predecessor_intent_id"] is not None
                    or context["source_kind"] != "founder-activation" or context["source_id"] != instance["id"]
                    or instance["id"] != _id("workflow-instance", self.org.slug, request.instance_id)
                    or activation_id != _id("workflow-activation", instance["id"], 1, operation["request_digest"])
                    or template.definition_digest != request.template.definition_digest
                    or template.identity_id != activation["template_identity_id"]
                    or template.namespace != intent["authority_namespace"]
                    or not template.namespace.startswith(f"org/{self.org.slug}/team/")
                    or request.authority.namespace != f"org/{self.org.slug}"
                    or request.authority.generation != intent["authority_generation"]
                    or request.authority.snapshot_digest != intent["authority_digest"]
                    or digest(intent["request_bytes"]) != operation["request_digest"]
                    or canonical_bytes(frozen) != intent["request_bytes"]):
                raise ValueError("closure")
            expected_grant = _authorization(org_slug=self.org.slug, instance_id=instance["id"],
                activation_id=activation_id, task_id=instance["root_task_id"], request=frozen,
                request_digest=operation["request_digest"], template=template, timestamp=activation["created_at"])
            bound = _binding(instance_id=instance["id"], activation_id=activation_id,
                authorization_id=authorization["id"], template=template, request=frozen)
            stored_context = json.loads(context["context_bytes"])
            # Compare the historical snapshot to its retained publication, not
            # to current readiness or a fresh roster/profile/source discovery.
            journal = conn.execute("SELECT snapshot_bytes FROM workflow_publication_journals "
                "WHERE namespace=? AND generation=? AND snapshot_digest=?",
                (request.authority.namespace, request.authority.generation, request.authority.snapshot_digest)).fetchall()
            snapshot_bytes = canonical_bytes(stored_context["authority_snapshot"])
            if (len(journal) != 1 or bytes(journal[0][0]) != snapshot_bytes
                    or digest(snapshot_bytes) != request.authority.snapshot_digest):
                raise ValueError("closure")
            self._roles(request, stored_context["authority_snapshot"], template)
            inputs = stored_context["inputs"]
            if not isinstance(inputs, list) or len(inputs) != len(request.inputs):
                raise ValueError("closure")
            for item, source in zip(inputs, request.inputs, strict=True):
                if (not isinstance(item, dict) or set(item) != {"pin", "bytes_base64"}
                        or item["pin"] != source.model_dump()
                        or not set(source.recipients) <= set(frozen["bindings"])
                        or len(set(source.recipients)) != len(source.recipients)):
                    raise ValueError("closure")
                content = base64.b64decode(item["bytes_base64"], validate=True)
                if (digest(content) != source.sha256
                        or base64.b64encode(content).decode("ascii") != item["bytes_base64"]):
                    raise ValueError("closure")
            expected_context = _context(org_slug=self.org.slug, request=frozen,
                snapshot=stored_context["authority_snapshot"], template=template, inputs=inputs,
                authorization=expected_grant, authorization_id=authorization["id"],
                binding=bound, binding_id=binding["id"], intent_id=intent["id"])
            author_role = _author_role(frozen, template)
            task_scope = dict(assigned_agent=frozen["bindings"][author_role]["principal"],
                team=frozen["bindings"][author_role]["team"], brief=_task_brief(frozen, inputs, template))
            if (canonical_bytes(expected_grant) != authorization["authority_bytes"]
                    or canonical_bytes(bound) != binding["binding_bytes"]
                    or canonical_bytes(expected_context) != context["context_bytes"]
                    or len(context["context_bytes"]) > CONTEXT_LIMIT
                    or canonical_bytes(task_scope) != intent["task_scope_bytes"]
                    or binding["id"] != _id("workflow-binding", instance["id"], digest(binding["binding_bytes"]))
                    or context["id"] != _id("workflow-context", instance["id"], digest(context["context_bytes"]))
                    or authorization["id"] != _id("workflow-authorization", instance["id"], 1, digest(authorization["authority_bytes"]))):
                raise ValueError("closure")
            receipt = dict(activation_id=activation_id, instance_id=instance["id"], instance_reference=request.instance_id,
                        activation_revision=activation["activation_revision"], root_task_id=instance["root_task_id"],
                        intent_id=intent["id"], template=grant["template"], authority=grant["authority"],
                        bindings=frozen["bindings"], eligible_replacements=frozen["eligible_replacements"],
                        allowed_actions=frozen["allowed_actions"], scope_digest=digest(canonical_bytes(frozen["scope"])),
                        context_digest=context["context_digest"], activated_by=grant["actor"], created_at=grant["created_at"],
                        original_request_digest=operation["request_digest"], replayed=False)
            if isinstance(request, DocumentActivationRequest):
                receipt["format"] = "workflow-activation-receipt@2"
            return receipt
        except (ValueError, TypeError, KeyError, AttributeError, sqlite3.DatabaseError) as exc:
            raise WorkflowActivationError("workflow_activation_storage_corrupt") from exc

    def _project(self, receipt: dict) -> dict:
        active_sessions = tuple(self.org.sessions.iter_active())
        with self.db._lock:
            intent = self.db._conn.execute("SELECT * FROM workflow_draft_dispatch_intents WHERE id=?", (receipt["intent_id"],)).fetchone()
            current = self.db._conn.execute("SELECT current_generation,snapshot_digest,state FROM workflow_authority_pointers WHERE namespace=?",
                                           (self.org.workflow_authority.namespace,)).fetchone()
            cutover = self.db._conn.execute("SELECT state FROM workflow_cutover_state WHERE singleton=1").fetchone()
            author_busy = author_capacity_blocked(self.db._conn, author=intent["assigned_principal"],
                task_id=intent["task_id"], active_sessions=active_sessions)
        blockers = []
        if cutover is None or cutover[0] != "enabled":
            blockers.append("workflow_new_runs_disabled")
        if (current is None or current["state"] != "ready"
                or current["current_generation"] != receipt["authority"]["generation"]
                or current["snapshot_digest"] != receipt["authority"]["snapshot_digest"]):
            blockers.append("workflow_activation_authority_stale")
        if author_busy:
            blockers.append("workflow_activation_author_pending")
        state = intent["state"]
        return {**receipt, "state": state, "execution_started": bool(intent["host_execution_id"]),
                "pending": state in {"queued", "claimed", "running", "uncertain"},
                "reconciliation_required": state == "uncertain", "cancellation_requested": bool(intent["cancellation_requested"]),
                "current_eligibility": {"eligible": not blockers and not intent["cancellation_requested"], "blockers": blockers},
                "responsible_owner": "workflow_recovery" if state == "uncertain" else intent["assigned_principal"]}

    def get(self, *, principal: WorkflowTemplatePrincipal, activation_id: str) -> dict:
        actor = self._principal(principal)
        with self.db._lock:
            receipt = self._closure(self.db._conn, activation_id, actor=actor)
        return self._project(receipt)

    def list(self, *, principal: WorkflowTemplatePrincipal) -> list[dict]:
        actor = self._principal(principal)
        with self.db._lock:
            ids = [row[0] for row in self.db._conn.execute(
                "SELECT activation_id FROM workflow_activation_operations WHERE org_slug=? AND principal=? ORDER BY activation_id",
                (self.org.slug, actor))]
        return [self.get(principal=principal, activation_id=activation_id) for activation_id in ids]
