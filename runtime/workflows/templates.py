"""Immutable U1B workflow-template authoring and version publication.

This service is deliberately inert: it validates one closed pure-data
``product-design`` definition and stores canonical bytes.  It does not activate
templates, create workflow instances/tasks, dispatch work, or emit effects.
"""
from __future__ import annotations

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


def _canonical_json(value: object, *, code: str) -> bytes:
    try:
        return json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeError) as exc:
        raise WorkflowTemplateError(code) from exc


def _closed_dict(value: object, keys: set[str]) -> dict:
    if not isinstance(value, dict) or set(value) != keys:
        raise WorkflowTemplateError("invalid_template_definition")
    return value


def _validate_definition(definition: object) -> bytes:
    root = _closed_dict(
        definition,
        {
            "kind", "schema_version", "description", "author", "reviewers",
            "approval", "request_changes",
        },
    )
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
    author = _closed_dict(root["author"], {"role", "kind", "artifact"})
    if author != {
        "role": "product-lead",
        "kind": "agent",
        "artifact": "immutable-prd-revision",
    }:
        raise WorkflowTemplateError("invalid_template_definition")
    reviewers = root["reviewers"]
    if reviewers != [
        {"role": "founder", "kind": "human"},
        {"role": "implementer", "kind": "agent"},
        {"role": "tester", "kind": "agent"},
    ]:
        raise WorkflowTemplateError("invalid_template_definition")
    approval = _closed_dict(root["approval"], {"mode", "revision", "required_roles"})
    if approval != {
        "mode": "all",
        "revision": "current",
        "required_roles": ["founder", "implementer", "tester"],
    }:
        raise WorkflowTemplateError("invalid_template_definition")
    request_changes = _closed_dict(root["request_changes"], {"action"})
    if request_changes != {"action": "return-to-author"}:
        raise WorkflowTemplateError("invalid_template_definition")
    return _canonical_json(root, code="invalid_template_definition")


def _component(value: object, *, code: str) -> str:
    if not isinstance(value, str) or _COMPONENT.fullmatch(value) is None:
        raise WorkflowTemplateError(code)
    return value


def _namespace(org_slug: str, team_slug: str) -> str:
    org = _component(org_slug, code="invalid_template_namespace")
    team = _component(team_slug, code="invalid_template_namespace")
    return f"org/{org}/team/{team}"


def _stable_id(kind: str, *parts: object) -> str:
    digest = hashlib.sha256(
        _canonical_json([kind, *parts], code="invalid_template_request")
    ).hexdigest()
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
        content_digest = hashlib.sha256(definition_bytes).hexdigest()
        request_digest = hashlib.sha256(
            _canonical_json(
                {
                    "namespace": namespace,
                    "template_name": name,
                    "expected_current_version": expected_current_version,
                    "definition_digest": content_digest,
                    "compiler_pin": _COMPILER_PIN,
                    "validator_pin": _VALIDATOR_PIN,
                    "source_pin": _SOURCE_PIN,
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
                        _COMPILER_PIN, _VALIDATOR_PIN, _SOURCE_PIN,
                        publisher_json, published_at,
                    ),
                )
                conn.execute(
                    "INSERT INTO workflow_template_versions VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        version_id, draft_id, namespace, name, version,
                        definition_bytes, content_digest, _COMPILER_PIN,
                        _VALIDATOR_PIN, _SOURCE_PIN, publisher_json, published_at,
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
            return tuple(
                self._read_version(
                    self._db._conn,
                    identity_id=str(row["id"]),
                    version=int(row["version"]),
                )
                for row in rows
            )

    @staticmethod
    def _read_version(
        conn: sqlite3.Connection,
        *,
        identity_id: str,
        version: int,
        expected_version_id: str | None = None,
    ) -> WorkflowTemplateVersion:
        row = conn.execute(
            "SELECT i.namespace,i.template_name,m.version,m.template_version_id,"
            "v.definition_bytes,v.definition_digest,v.compiler_pin,v.validator_pin,"
            "v.source_pin,v.published_by,v.published_at "
            "FROM workflow_template_identities i "
            "JOIN workflow_template_identity_versions m ON m.template_identity_id=i.id "
            "JOIN workflow_template_versions v ON v.id=m.template_version_id "
            "WHERE i.id=? AND m.version=?",
            (identity_id, version),
        ).fetchone()
        if row is None:
            raise WorkflowTemplateError("template_version_not_found")
        if expected_version_id is not None and row["template_version_id"] != expected_version_id:
            raise WorkflowTemplateError("template_storage_corrupt")
        definition_bytes = bytes(row["definition_bytes"])
        if hashlib.sha256(definition_bytes).hexdigest() != row["definition_digest"]:
            raise WorkflowTemplateError("template_storage_corrupt")
        try:
            publisher = json.loads(row["published_by"])
        except (TypeError, json.JSONDecodeError) as exc:
            raise WorkflowTemplateError("template_storage_corrupt") from exc
        if not isinstance(publisher, dict):
            raise WorkflowTemplateError("template_storage_corrupt")
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
            publisher={str(key): str(value) for key, value in publisher.items()},
            published_at=str(row["published_at"]),
        )
