"""Immutable U1B workflow-template authoring and version publication.

This service is deliberately inert: it validates one closed pure-data
versioned document-review definition and stores canonical bytes.  It does not activate
templates, create workflow instances/tasks, dispatch work, or emit effects.
"""
from __future__ import annotations

from runtime.infrastructure._workflow_reader import (
    _encode as _reader_encode,
    _json_dumps as _reader_json_dumps,
    _sha256 as _reader_sha256,
)

from runtime.infrastructure._workflow_reader import (
    _append as _reader_append,
    _put as _reader_put,
)

from runtime.infrastructure._workflow_reader import (
    _any_true as _reader_any_true,
)

from runtime.infrastructure._workflow_reader import (
    _dict_pairs as _reader_dict_pairs,
    _list as _reader_list,
    _set as _reader_set,
)

from runtime.infrastructure._workflow_reader import (
    _all as _reader_all, _one as _reader_one, _json as _reader_json,
    _reserve as _reader_reserve, _work as _reader_work,
)

import hashlib
import json
import re
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Literal

from runtime.infrastructure.database import Database


_COMPONENT = re.compile(r"[a-z][a-z0-9-]{0,62}")
_COMPILER_PIN = "workflow-compiler@1"
_VALIDATOR_PIN = "workflow-validator@1"
_SOURCE_PIN = "operator-input@1"


class WorkflowTemplateError(ValueError):
    """Closed machine-readable template failure."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class WorkflowTemplatePrincipal:
    """Server-derived authenticated publisher plus an in-commit recheck.

    The callback must be a read-only, in-memory revalidation of the already
    authenticated server state.  Routes hold the existing session-binding and
    teams locks while the store invokes it after ``BEGIN IMMEDIATE``.
    """

    principal_kind: Literal["agent", "human"]
    principal_id: str
    proof_kind: Literal["task_session", "founder_bearer"]
    org_slug: str
    team_slug: str
    task_id: str | None = None
    session_id: str | None = None
    _revalidate: Callable[[], None] = field(
        default=lambda: None, repr=False, compare=False,
    )

    @classmethod
    def agent(
        cls,
        *,
        org_slug: str,
        agent_name: str,
        team_slug: str,
        task_id: str,
        session_id: str,
        revalidate: Callable[[], None],
    ) -> WorkflowTemplatePrincipal:
        return cls(
            principal_kind="agent",
            principal_id=agent_name,
            proof_kind="task_session",
            org_slug=org_slug,
            team_slug=team_slug,
            task_id=task_id,
            session_id=session_id,
            _revalidate=revalidate,
        )

    @classmethod
    def founder(
        cls,
        *,
        org_slug: str,
        team_slug: str,
        revalidate: Callable[[], None],
    ) -> WorkflowTemplatePrincipal:
        return cls(
            principal_kind="human",
            principal_id="founder",
            proof_kind="founder_bearer",
            org_slug=org_slug,
            team_slug=team_slug,
            _revalidate=revalidate,
        )

    @property
    def operation_principal(self) -> str:
        return f"{self.principal_kind}:{self.principal_id}"

    @property
    def provenance(self) -> dict[str, str]:
        value = {
            "principal_kind": self.principal_kind,
            "principal_id": self.principal_id,
            "proof_kind": self.proof_kind,
        }
        if self.principal_kind == "agent":
            assert self.task_id is not None and self.session_id is not None
            value["task_id"] = self.task_id
            value["session_id"] = self.session_id
        return value

    def revalidate(self) -> None:
        self._revalidate()


@dataclass(frozen=True)
class WorkflowTemplateVersion:
    identity_id: str
    version_id: str
    namespace: str
    template_name: str
    version: int
    definition_bytes: bytes
    definition_digest: str
    compiler_pin: str
    validator_pin: str
    source_pin: str
    publisher: dict[str, str]
    published_at: str


def _canonical_json(value: object, *, code: str, budget=None) -> bytes:
    _reader_reserve(budget, 0)
    try:
        _reader_reserve(budget, 112)
        return _reader_encode(_reader_json_dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False, budget=budget), "utf-8", budget=budget)
    except ((TypeError, ValueError, UnicodeError)) as exc:
        raise WorkflowTemplateError(code) from exc


def _closed_dict(value: object, keys: set[str], *, budget=None) -> dict:
    _reader_reserve(budget, 0)
    _reader_reserve(budget, 64 * len(keys))
    if not isinstance(value, dict) or _reader_set(value, budget=budget) != keys:
        raise WorkflowTemplateError("invalid_template_definition")
    return value


def _validate_legacy_definition(definition: object, *, budget=None) -> bytes:
    _reader_work(definition, budget, copies=2)
    root = _closed_dict(
        definition,
        _reader_set((_reader_reserve(budget, 176) or ("kind", "schema_version", "description", "author", "reviewers", "approval", "request_changes")), budget=budget),
    budget=budget)
    if (
        root["kind"] != "product-design"
        or type(root["schema_version"]) is not int
        or root["schema_version"] != 1
    ):
        raise WorkflowTemplateError("invalid_template_definition")
    if (
        not isinstance(root["description"], str)
        or not root["description"].strip()
        or len(root["description"]) > 2000
    ):
        raise WorkflowTemplateError("invalid_template_definition")
    author = _closed_dict(root["author"], _reader_set((_reader_reserve(budget, 112) or ("role", "kind", "artifact")), budget=budget), budget=budget)
    if author != (_reader_reserve(budget, 448) or {
        "role": "product-lead",
        "kind": "agent",
        "artifact": "immutable-prd-revision",
    }):
        raise WorkflowTemplateError("invalid_template_definition")
    reviewers = root["reviewers"]
    if reviewers != (_reader_reserve(budget, 448) or [
        (_reader_reserve(budget, 320) or {"role": "founder", "kind": "human"}),
        (_reader_reserve(budget, 320) or {"role": "implementer", "kind": "agent"}),
        (_reader_reserve(budget, 320) or {"role": "tester", "kind": "agent"}),
    ]):
        raise WorkflowTemplateError("invalid_template_definition")
    approval = _closed_dict(root["approval"], _reader_set((_reader_reserve(budget, 112) or ("mode", "revision", "required_roles")), budget=budget), budget=budget)
    if approval != (_reader_reserve(budget, 448) or {
        "mode": "all",
        "revision": "current",
        "required_roles": (_reader_reserve(budget, 448) or ["founder", "implementer", "tester"]),
    }):
        raise WorkflowTemplateError("invalid_template_definition")
    request_changes = _closed_dict(root["request_changes"], _reader_set((_reader_reserve(budget, 80) or ("action",)), budget=budget), budget=budget)
    if request_changes != (_reader_reserve(budget, 192) or {"action": "return-to-author"}):
        raise WorkflowTemplateError("invalid_template_definition")
    return _canonical_json(root, code="invalid_template_definition", budget=budget)


def _compile_document_definition(definition: object, *, budget=None) -> dict:
    """Compile a matched finite document family without principal authority."""
    _reader_work(definition, budget, copies=2)
    root = _closed_dict(definition, _reader_set((_reader_reserve(budget, 224) or ("kind", "schema_version", "description", "author", "output", "reviewers", "outcomes", "approval", "request_changes", "submission")), budget=budget), budget=budget)
    if (type(root["schema_version"]) is not int or root["schema_version"] not in (2, 3)
            or root["kind"] != "document-review"):
        raise WorkflowTemplateError("invalid_template_definition")
    for description in (_reader_reserve(budget, 96) or (root["description"], _closed_dict(
            root["output"], _reader_set((_reader_reserve(budget, 96) or ("primitive", "description")), budget=budget), budget=budget)["description"])):
        if not isinstance(description, str) or not description.strip() or len(description) > 2000:
            raise WorkflowTemplateError("invalid_template_definition")
    if root["output"]["primitive"] != "immutable-document-revision":
        raise WorkflowTemplateError("invalid_template_definition")
    author = _closed_dict(root["author"], _reader_set((_reader_reserve(budget, 96) or ("role", "kind")), budget=budget), budget=budget)
    _component(author["role"], code="invalid_template_definition", budget=budget)
    if author["kind"] != "agent":
        raise WorkflowTemplateError("invalid_template_definition")
    reviewers = root["reviewers"]
    if not isinstance(reviewers, list) or not 1 <= len(reviewers) <= 3:
        raise WorkflowTemplateError("invalid_template_definition")
    kinds = (_reader_reserve(budget, 192) or {author["role"]: "agent"})
    reviewer_roles = (_reader_reserve(budget, 64) or [])
    for value in reviewers:
        reviewer = _closed_dict(value, _reader_set((_reader_reserve(budget, 96) or ("role", "kind")), budget=budget), budget=budget)
        role = _component(reviewer["role"], code="invalid_template_definition", budget=budget)
        if role in kinds or reviewer["kind"] not in ("agent", "human"):
            raise WorkflowTemplateError("invalid_template_definition")
        _reader_put(reviewer["kind"], kinds, role, budget=budget)
        _reader_append(reviewer_roles, role, budget=budget)
    if _reader_list(kinds.values(), budget=budget).count("human") > 1:
        raise WorkflowTemplateError("invalid_template_definition")
    outcomes = root["outcomes"]
    if (not isinstance(outcomes, list) or not 1 <= len(outcomes) <= 2
            or _reader_any_true((value not in ("approved", "changes_requested") for value in outcomes), budget=budget)
            or len(_reader_set(outcomes, budget=budget)) != len(outcomes) or "approved" not in outcomes):
        raise WorkflowTemplateError("invalid_template_definition")
    approval = _closed_dict(root["approval"], _reader_set((_reader_reserve(budget, 112) or ("mode", "revision", "required_roles")), budget=budget), budget=budget)
    if (approval["mode"] != "all" or approval["revision"] != "current"
            or approval["required_roles"] != reviewer_roles):
        raise WorkflowTemplateError("invalid_template_definition")
    if "changes_requested" in outcomes:
        if _closed_dict(root["request_changes"], _reader_set((_reader_reserve(budget, 112) or ("action", "revision", "invalidate")), budget=budget), budget=budget) != (_reader_reserve(budget, 448) or {
                "action": "return-to-author", "revision": "new", "invalidate": "all-prior-receipts"}):
            raise WorkflowTemplateError("invalid_template_definition")
    elif root["request_changes"] is not None:
        raise WorkflowTemplateError("invalid_template_definition")
    version = root["schema_version"]
    submission = _closed_dict(root["submission"], _reader_set((_reader_reserve(budget, 96) or ("timing", "delivery")), budget=budget) if version == 3 else _reader_set((_reader_reserve(budget, 80) or ("timing",)), budget=budget), budget=budget)
    if version == 3 and submission["delivery"] != "current-author-handoff@1":
        raise WorkflowTemplateError("invalid_template_definition")
    if submission["timing"] not in ("on-completion", "while-active-or-completed"):
        raise WorkflowTemplateError("invalid_template_definition")
    # Store the operator's ordering. Neither a name nor a numerical publication
    # version participates in policy selection.
    return (_reader_reserve(budget, 1216) or dict(format=f"workflow-document-contract@{version}", author_role=author["role"],
                role_kinds=kinds, reviewer_roles=reviewer_roles, output=root["output"],
                outcomes=outcomes, approval=approval, request_changes=root["request_changes"],
                submission=submission))


def _compile_artifact_definition(definition: object, *, budget=None) -> dict:
    """Finite @4 data contract; kind selection never confers repository access."""
    _reader_work(definition, budget, copies=2)
    root = _closed_dict(definition, _reader_set((_reader_reserve(budget, 224) or ("kind", "schema_version", "description", "author", "output", "reviewers", "outcomes", "approval", "request_changes", "submission")), budget=budget), budget=budget)
    output = _closed_dict(root["output"], _reader_set((_reader_reserve(budget, 112) or ("primitive", "description", "accepted_kinds")), budget=budget), budget=budget)
    if (type(root["schema_version"]) is not int or root["schema_version"] != 4
            or root["kind"] != "artifact-review"
            or output["primitive"] != "immutable-artifact-revision"
            or type(output["accepted_kinds"]) is not list
            or len(output["accepted_kinds"]) != 1
            or output["accepted_kinds"][0] not in ("document", "code-change")):
        raise WorkflowTemplateError("invalid_template_definition")
    # Share finite policy validation, preserving the historical compiler and
    # serializer. The @4 output remains explicit and is never content-sniffed.
    policy = _compile_document_definition(dict(root, kind="document-review", schema_version=3,
        output=(_reader_reserve(budget, 320) or dict(primitive="immutable-document-revision", description=output["description"]))), budget=budget)
    return dict(policy, format="workflow-artifact-contract@4", output=output)


def _validate_definition(definition: object, *, budget=None) -> bytes:
    _reader_reserve(budget, 0)
    if isinstance(definition, dict) and type(definition.get("schema_version")) is int and definition["schema_version"] == 4:
        _compile_artifact_definition(definition, budget=budget)
        return _canonical_json(definition, code="invalid_template_definition", budget=budget)
    if isinstance(definition, dict) and type(definition.get("schema_version")) is int and definition["schema_version"] in (2, 3):
        _compile_document_definition(definition, budget=budget)
        return _canonical_json(definition, code="invalid_template_definition", budget=budget)
    return _validate_legacy_definition(definition, budget=budget)


def _definition_pins(definition: dict, *, budget=None) -> tuple[str, str, str]:
    _reader_reserve(budget, 0)
    _reader_reserve(budget, 256)
    if type(definition.get("schema_version")) is int and definition["schema_version"] in (2, 3, 4):
        version = definition["schema_version"]
        return (_reader_reserve(budget, 112) or (f'workflow-compiler@{version}', f'workflow-validator@{version}', f'operator-input@{version}'))
    return (_reader_reserve(budget, 112) or (_COMPILER_PIN, _VALIDATOR_PIN, _SOURCE_PIN))


def _validate_definition_pins(definition: object, *, compiler_pin: str,
                              validator_pin: str, source_pin: str, budget=None) -> bytes:
    _reader_reserve(budget, 0)
    raw = _validate_definition(definition, budget=budget)
    if (_reader_reserve(budget, 112) or (compiler_pin, validator_pin, source_pin)) != _definition_pins(definition, budget=budget):
        raise WorkflowTemplateError("invalid_template_definition")
    return raw


def _document_contract(template: WorkflowTemplateVersion, *, budget=None) -> dict | None:
    """Read the retained definition and exact family, including during replay."""
    try:
        _reader_reserve(budget, 112)
        definition = _reader_json(template.definition_bytes, budget)
        raw = _validate_definition_pins(definition, compiler_pin=template.compiler_pin,
                                        validator_pin=template.validator_pin, source_pin=template.source_pin, budget=budget)
        if raw != template.definition_bytes:
            raise WorkflowTemplateError("template_storage_corrupt")
        if _definition_pins(definition, budget=budget)[0] in ("workflow-compiler@2", "workflow-compiler@3"):
            return _compile_document_definition(definition, budget=budget)
        if template.compiler_pin == "workflow-compiler@4":
            raise WorkflowTemplateError("template_storage_corrupt")
        return None
    except ((ValueError, TypeError, UnicodeError)) as exc:
        raise WorkflowTemplateError("template_storage_corrupt") from exc


def _review_contract(template: WorkflowTemplateVersion, *, budget=None) -> dict | None:
    """Select an exact closed family; legacy documents keep their own contract."""
    if template.compiler_pin != "workflow-compiler@4":
        return _document_contract(template, budget=budget)
    try:
        _reader_reserve(budget, 112)
        definition = _reader_json(template.definition_bytes, budget)
        raw = _validate_definition_pins(definition, compiler_pin=template.compiler_pin,
            validator_pin=template.validator_pin, source_pin=template.source_pin, budget=budget)
        if raw != template.definition_bytes:
            raise ValueError("canonical bytes")
        return _compile_artifact_definition(definition, budget=budget)
    except ((ValueError, TypeError, UnicodeError)) as exc:
        raise WorkflowTemplateError("template_storage_corrupt") from exc


def _component(value: object, *, code: str, budget=None) -> str:
    _reader_work(value, budget)
    if not isinstance(value, str) or _COMPONENT.fullmatch(value) is None:
        raise WorkflowTemplateError(code)
    return value


def _namespace(org_slug: str, team_slug: str) -> str:
    org = _component(org_slug, code="invalid_template_namespace")
    team = _component(team_slug, code="invalid_template_namespace")
    return f"org/{org}/team/{team}"


def _stable_id(kind: str, *parts: object, budget=None) -> str:
    _reader_reserve(budget, 0)
    _reader_reserve(budget, 64 + 16 * (len(parts) + 1) + 128 + 4 * len(kind))
    raw = _canonical_json([kind, *parts], code="invalid_template_request", budget=budget)
    _reader_work(raw, budget)
    digest = _reader_sha256(raw, budget=budget).hexdigest()
    return f"{kind}:{digest}"


class WorkflowTemplateStore:
    """Database-serialized immutable template store over the shipped U1A tables."""

    def __init__(self, db: Database) -> None:
        self._db = db

    def publish_version(
        self,
        *,
        org_slug: str,
        principal: WorkflowTemplatePrincipal,
        operation_key: str,
        namespace: str,
        template_name: str,
        definition: object,
        expected_current_version: int,
    ) -> WorkflowTemplateVersion:
        expected_namespace = _namespace(org_slug, principal.team_slug)
        if principal.org_slug != org_slug or namespace != expected_namespace:
            raise WorkflowTemplateError("namespace_claim_rejected")
        name = _component(template_name, code="invalid_template_name")
        if not isinstance(operation_key, str) or not operation_key or len(operation_key) > 256:
            raise WorkflowTemplateError("invalid_operation_key")
        if (
            isinstance(expected_current_version, bool)
            or not isinstance(expected_current_version, int)
            or expected_current_version < 0
        ):
            raise WorkflowTemplateError("invalid_expected_current_version")
        if not principal.principal_id or not principal.operation_principal:
            raise WorkflowTemplateError("invalid_principal")
        definition_bytes = _validate_definition(definition)
        compiler_pin, validator_pin, source_pin = _definition_pins(definition)
        content_digest = hashlib.sha256(definition_bytes).hexdigest()
        request_digest = hashlib.sha256(
            _canonical_json(
                {
                    "namespace": namespace,
                    "template_name": name,
                    "expected_current_version": expected_current_version,
                    "definition_digest": content_digest,
                    "compiler_pin": compiler_pin,
                    "validator_pin": validator_pin,
                    "source_pin": source_pin,
                },
                code="invalid_template_request",
            )
        ).hexdigest()
        identity_id = _stable_id("workflow-template", namespace, name)
        publisher_json = _canonical_json(
            principal.provenance, code="invalid_principal"
        ).decode("utf-8")
        published_at = datetime.now(timezone.utc).isoformat()

        with self._db._lock:
            conn = self._db._conn
            if conn.in_transaction:
                raise WorkflowTemplateError("template_caller_transaction_not_allowed")
            conn.execute("BEGIN IMMEDIATE")
            try:
                principal.revalidate()
                if compiler_pin in ("workflow-compiler@3", "workflow-compiler@4"):
                    from runtime.infrastructure.workflow_schema import require_author_handoff_schema
                    require_author_handoff_schema(conn, expected_org_slug=org_slug)
                prior = conn.execute(
                    "SELECT request_digest,template_identity_id,result_version,template_version_id "
                    "FROM workflow_template_publish_operations "
                    "WHERE org_slug=? AND principal=? AND operation_key=?",
                    (org_slug, principal.operation_principal, operation_key),
                ).fetchone()
                if prior is not None:
                    if prior["request_digest"] != request_digest or prior["template_identity_id"] != identity_id:
                        raise WorkflowTemplateError("template_publish_operation_conflict")
                    result = self._read_version(
                        conn,
                        identity_id=identity_id,
                        version=int(prior["result_version"]),
                        expected_version_id=str(prior["template_version_id"]),
                    )
                    conn.commit()
                    return result

                identity = conn.execute(
                    "SELECT namespace,template_name,current_version,status "
                    "FROM workflow_template_identities WHERE id=?",
                    (identity_id,),
                ).fetchone()
                if identity is None:
                    if expected_current_version != 0:
                        raise WorkflowTemplateError("template_version_cas_stale")
                    conn.execute(
                        "INSERT INTO workflow_template_identities VALUES (?,?,?,?,?,?)",
                        (identity_id, namespace, name, 0, "active", published_at),
                    )
                    current = 0
                else:
                    if (
                        identity["namespace"] != namespace
                        or identity["template_name"] != name
                        or identity["status"] != "active"
                    ):
                        raise WorkflowTemplateError("template_identity_not_publishable")
                    current = int(identity["current_version"])
                if current != expected_current_version:
                    raise WorkflowTemplateError("template_version_cas_stale")
                if conn.execute(
                    "SELECT 1 FROM workflow_template_identity_versions "
                    "WHERE template_identity_id=? AND content_digest=?",
                    (identity_id, content_digest),
                ).fetchone() is not None:
                    raise WorkflowTemplateError("template_content_already_published")

                version = current + 1
                draft_id = _stable_id(
                    "workflow-template-draft", identity_id, version, request_digest,
                )
                version_id = _stable_id(
                    "workflow-template-version", identity_id, version, content_digest,
                )
                conn.execute(
                    "INSERT INTO workflow_template_drafts VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (
                        draft_id, namespace, name, definition_bytes, content_digest,
                        compiler_pin, validator_pin, source_pin,
                        publisher_json, published_at,
                    ),
                )
                conn.execute(
                    "INSERT INTO workflow_template_versions VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        version_id, draft_id, namespace, name, version,
                        definition_bytes, content_digest, compiler_pin,
                        validator_pin, source_pin, publisher_json, published_at,
                    ),
                )
                conn.execute(
                    "INSERT INTO workflow_template_identity_versions VALUES (?,?,?,?)",
                    (identity_id, version, version_id, content_digest),
                )
                updated = conn.execute(
                    "UPDATE workflow_template_identities SET current_version=? "
                    "WHERE id=? AND current_version=?",
                    (version, identity_id, current),
                )
                if updated.rowcount != 1:
                    raise WorkflowTemplateError("template_version_cas_stale")
                conn.execute(
                    "INSERT INTO workflow_template_publish_operations VALUES (?,?,?,?,?,?,?,?)",
                    (
                        org_slug, principal.operation_principal, operation_key,
                        request_digest, identity_id, expected_current_version,
                        version, version_id,
                    ),
                )
                result = self._read_version(
                    conn, identity_id=identity_id, version=version,
                    expected_version_id=version_id,
                )
                conn.commit()
                return result
            except WorkflowTemplateError:
                conn.rollback()
                raise
            except sqlite3.IntegrityError as exc:
                conn.rollback()
                raise WorkflowTemplateError("template_storage_conflict") from exc
            except Exception:
                conn.rollback()
                raise

    def get(
        self,
        *,
        org_slug: str,
        namespace: str,
        template_name: str,
        version: int,
    ) -> WorkflowTemplateVersion:
        if namespace == 'release/happyranch':
            if template_name != 'coding' or type(version) is not int or version != 1:
                raise WorkflowTemplateError('template_version_not_found')
            with self._db._lock:
                return self._read_release(self._db._conn, org_slug=org_slug)
        expected_prefix = f"org/{_component(org_slug, code='invalid_template_namespace')}/team/"
        if not isinstance(namespace, str) or not namespace.startswith(expected_prefix):
            raise WorkflowTemplateError("invalid_template_namespace")
        parts = namespace.split("/")
        if len(parts) != 4 or namespace != _namespace(org_slug, parts[3]):
            raise WorkflowTemplateError("invalid_template_namespace")
        name = _component(template_name, code="invalid_template_name")
        if isinstance(version, bool) or not isinstance(version, int) or version < 1:
            raise WorkflowTemplateError("invalid_template_version")
        identity_id = _stable_id("workflow-template", namespace, name)
        with self._db._lock:
            result = self._read_version(
                self._db._conn, identity_id=identity_id, version=version,
            )
        return result

    def list(
        self, *, org_slug: str, namespace: str,
    ) -> tuple[WorkflowTemplateVersion, ...]:
        if namespace == 'release/happyranch':
            with self._db._lock:
                item = self._read_release(self._db._conn, org_slug=org_slug, allow_absent=True)
                return () if item is None else (item,)
        prefix = f"org/{_component(org_slug, code='invalid_template_namespace')}/team/"
        if not isinstance(namespace, str) or not namespace.startswith(prefix):
            raise WorkflowTemplateError("invalid_template_namespace")
        parts = namespace.split("/")
        if len(parts) != 4 or namespace != _namespace(org_slug, parts[3]):
            raise WorkflowTemplateError("invalid_template_namespace")
        with self._db._lock:
            rows = self._db._conn.execute(
                "SELECT i.id,m.version FROM workflow_template_identities i "
                "JOIN workflow_template_identity_versions m ON m.template_identity_id=i.id "
                "WHERE i.namespace=? ORDER BY i.template_name,m.version",
                (namespace,),
            ).fetchall()
            result = tuple(
                self._read_version(
                    self._db._conn,
                    identity_id=str(row["id"]),
                    version=int(row["version"]),
                )
                for row in rows
            )
            imported = self._read_release(self._db._conn, org_slug=org_slug, allow_absent=True)
            return result + (() if imported is None else (imported,))

    @staticmethod
    def _read_release(conn: sqlite3.Connection, *, org_slug: str | None,
                      allow_absent: bool = False, budget=None) -> WorkflowTemplateVersion | None:
        from runtime.workflows._coding_registered_release import read_registered_release
        from runtime.workflows.coding_authority_history import _Budget
        if budget is not None and type(budget) is not _Budget:
            raise WorkflowTemplateError('template_storage_corrupt')
        budget = _Budget() if budget is None else budget
        owned = not conn.in_transaction
        if owned:
            conn.execute('BEGIN')
        try:
            if org_slug is None:
                # Only a low-level version lookup needs saved org discovery.
                # Normal org-scoped list/get reconstruct the complete inventory
                # directly, without parsing the same history twice.
                discovered = read_registered_release(conn, expected_org_slug=None, budget=budget)
                if discovered is None:
                    if allow_absent:
                        return None
                    raise WorkflowTemplateError('template_version_not_found')
                org_slug = discovered.org_slug
            from runtime.infrastructure.workflow_schema import _read_workflow_schema
            _, coding = _read_workflow_schema(conn, expected_org_slug=org_slug, budget=budget)
            if coding is None or coding.release is None:
                if allow_absent:
                    return None
                raise WorkflowTemplateError('template_version_not_found')
            return coding.release.template()
        except WorkflowTemplateError:
            raise
        except ValueError:
            raise WorkflowTemplateError('template_storage_corrupt') from None
        finally:
            if owned:
                conn.rollback()

    @staticmethod
    def _read_version(
        conn: sqlite3.Connection,
        *,
        identity_id: str,
        version: int,
        expected_version_id: str | None = None,
        budget=None,
    ) -> WorkflowTemplateVersion:
        row = _reader_one(conn, "SELECT i.namespace,i.template_name,m.version,m.template_version_id,"
            "v.definition_bytes,v.definition_digest,v.compiler_pin,v.validator_pin,"
            "v.source_pin,v.published_by,v.published_at "
            "FROM workflow_template_identities i "
            "JOIN workflow_template_identity_versions m ON m.template_identity_id=i.id "
            "JOIN workflow_template_versions v ON v.id=m.template_version_id "
            "WHERE i.id=? AND m.version=?", (_reader_reserve(budget, 96) or (identity_id, version)), budget=budget)
        if row is None:
            raise WorkflowTemplateError("template_version_not_found")
        if expected_version_id is not None and row["template_version_id"] != expected_version_id:
            raise WorkflowTemplateError("template_storage_corrupt")
        if row['namespace'] == 'release/happyranch':
            imported = WorkflowTemplateStore._read_release(conn, org_slug=None, budget=budget)
            if (type(version) is not int or imported.identity_id != identity_id or imported.version != version
                    or (expected_version_id is not None and imported.version_id != expected_version_id)):
                raise WorkflowTemplateError('template_storage_corrupt')
            return imported
        _reader_work(row["definition_bytes"], budget, copies=2)
        definition_bytes = bytes(row["definition_bytes"])
        if _reader_sha256(definition_bytes, budget=budget).hexdigest() != row["definition_digest"]:
            raise WorkflowTemplateError("template_storage_corrupt")
        try:
            _reader_reserve(budget, 112)
            canonical = _validate_definition_pins(_reader_json(definition_bytes, budget),
                compiler_pin=row["compiler_pin"], validator_pin=row["validator_pin"], source_pin=row["source_pin"], budget=budget)
            if canonical != definition_bytes:
                raise WorkflowTemplateError("template_storage_corrupt")
            publisher = _reader_json(row["published_by"], budget)
        except ((TypeError, ValueError, UnicodeError)) as exc:
            raise WorkflowTemplateError("template_storage_corrupt") from exc
        if not isinstance(publisher, dict):
            raise WorkflowTemplateError("template_storage_corrupt")
        _reader_work(publisher, budget)
        _reader_reserve(budget, 512)
        return WorkflowTemplateVersion(
            identity_id=identity_id,
            version_id=str(row["template_version_id"]),
            namespace=str(row["namespace"]),
            template_name=str(row["template_name"]),
            version=int(row["version"]),
            definition_bytes=definition_bytes,
            definition_digest=str(row["definition_digest"]),
            compiler_pin=str(row["compiler_pin"]),
            validator_pin=str(row["validator_pin"]),
            source_pin=str(row["source_pin"]),
            publisher=_reader_dict_pairs(((_reader_reserve(budget, 96) or (str(key),  str(value) )) for key, value in publisher.items()), budget=budget),
            published_at=str(row["published_at"]),
        )
