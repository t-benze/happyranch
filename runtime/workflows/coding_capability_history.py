"""Unmounted SELECT-only retained native coding capability closure.

Caller admits the complete native/J structure and owns one coherent raw-value
SQLite read transaction, connection and lock throughout. Native envelopes,
own admissions, historical publications and complete parent/setup containment
are retained facts only. They prove no real mint order, source execution,
task/SID/result/assignment/policy/J graph or current permission. Those separate
readers must ship with each corresponding capability producer/current-use
consumer. They do not block the separate human setup producer.
"""
from __future__ import annotations

import base64
import json
import re
import sqlite3
from dataclasses import dataclass

from runtime.workflows.coding_authority_history import (
    CodingAuthorityHistoryError, SetupIssuanceHistory, _Budget,
    _SetupHistoryReader, _capture_admission, _digest, _rows, _token,
)
from runtime.workflows.coding_saved_records import (
    CodingSavedRecordError, read_saved_capability,
)

_SOURCE_PIN = 'workflow-coding-capability@1'
_ANCESTRY_LIMIT = 64
_REFUSAL = 'coding_capability_history_invalid'
_TYPES = ('text', 'text', 'integer', 'blob', 'text', 'text', 'text')


class CodingCapabilityHistoryError(ValueError):
    """Categorical refusal, without private bytes, native rows or SQL details."""


@dataclass(frozen=True)
class HistoricalCapabilityAdmission:
    """All five matched native OWN admission columns, not source authenticity.

    request_digest hashes this complete capability, not its selected grant.
    admitted_by is historical source operation attribution, not a live actor.
    """

    id: str
    namespace: str
    generation: int
    request_digest: str
    admitted_by: str


@dataclass(frozen=True)
class HistoricalCodingCapability:
    """All seven native columns, original typeof and matched own admission.

    Full authority_bytes retain the complete capture and embedded ancestry;
    no reduced persisted serialization or source/current-use grant is made.
    """

    authorization_id: str
    namespace: str
    revision: int
    authority_bytes: bytes
    authority_digest: str
    source_pin: str
    created_at: str
    storage_classes: tuple[str, ...]
    own_admission: HistoricalCapabilityAdmission


@dataclass(frozen=True)
class CodingCapabilityHistory:
    """Native source-to-selected ancestry and the actual U issuance prefix.

    Later withdrawal preserves this history without restoring activity.
    setup_history ends at U's containing issue, never at a capability row.
    """

    capabilities: tuple[HistoricalCodingCapability, ...]
    setup_history: SetupIssuanceHistory

    @property
    def selected(self) -> HistoricalCodingCapability:
        return self.capabilities[-1]


def _check(condition: bool) -> None:
    if not condition:
        raise CodingCapabilityHistoryError(_REFUSAL)


def _native_capability(conn: sqlite3.Connection, org_slug: str,
                       authorization_id: str, budget: _Budget) -> tuple:
    budget.step()
    namespace = f'org/{org_slug}/coding-capability/{authorization_id}'
    # ID, namespace and digest aliases conflict rather than substituting for
    # the selected row. The scalar subquery is native-only; no unbounded value
    # is fetched from it. Structural native admission includes ID/digest UNIQUE.
    # Every returned field gates storage class/byte length BEFORE fetch and
    # retains original typeof for refusal, including wrong-type NULL sentinels.
    rows = list(_rows(conn,
        "SELECT CASE WHEN typeof(id)='text' AND length(CAST(id AS BLOB))<=1048576 THEN id ELSE NULL END,"
        "CASE WHEN typeof(namespace)='text' AND length(CAST(namespace AS BLOB))<=1048576 THEN namespace ELSE NULL END,"
        "CASE WHEN typeof(revision)='integer' THEN revision ELSE NULL END,"
        "CASE WHEN typeof(authority_bytes)='blob' AND length(authority_bytes)<=1048576 THEN authority_bytes ELSE NULL END,"
        "CASE WHEN typeof(authority_digest)='text' AND length(CAST(authority_digest AS BLOB))<=1048576 THEN authority_digest ELSE NULL END,"
        "CASE WHEN typeof(source_pin)='text' AND length(CAST(source_pin AS BLOB))<=1048576 THEN source_pin ELSE NULL END,"
        "CASE WHEN typeof(created_at)='text' AND length(CAST(created_at AS BLOB))<=1048576 THEN created_at ELSE NULL END,"
        'typeof(id),typeof(namespace),typeof(revision),typeof(authority_bytes),'
        'typeof(authority_digest),typeof(source_pin),typeof(created_at) '
        'FROM workflow_authorization_revisions WHERE id=? OR namespace=? OR '
        'authority_digest=(SELECT authority_digest FROM workflow_authorization_revisions WHERE id=? LIMIT 1) '
        'LIMIT 2', (authorization_id, namespace, authorization_id), 1, budget))
    _check(len(rows) == 1)
    values = budget.row(rows[0], _TYPES, counted=True)
    _check(values[0] == authorization_id and values[1] == namespace
           and values[2] == 1 and values[4] == _digest(values[3])
           and values[5] == _SOURCE_PIN)
    return values


def _history(conn: sqlite3.Connection, org_slug: str,
             authorization_id: str, *, budget: _Budget | None = None,
             setups: _SetupHistoryReader | None = None) -> CodingCapabilityHistory:
    # Private call-local composition; the public API creates its own budget.
    if budget is None:
        budget = _Budget()
    if setups is None:
        setups = _SetupHistoryReader(conn, org_slug, budget)
    _check(type(setups) is _SetupHistoryReader and setups.conn is conn
           and setups.org_slug == org_slug and setups.budget is budget)
    values = _native_capability(conn, org_slug, authorization_id, budget)
    # Parse the selected COMPLETE contained ancestry once. Exact native byte
    # equality below then binds each already-validated P, with no recursive
    # parser/budget reset, caller records, resolver or Boolean trust seam.
    read_saved_capability(values[3], expected_org_slug=org_slug)
    record = json.loads(values[3])
    identities: set[str] = set()
    admission_ids: set[str] = set()
    result: list[HistoricalCodingCapability] = []
    while True:
        budget.step()
        _check(len(result) < _ANCESTRY_LIMIT and record['id'] not in identities
               and values[0] == record['id'] and values[6] == record['created_at'])
        identities.add(record['id'])
        capture = record['authority']
        admission = HistoricalCapabilityAdmission(
            capture['admission_record_id'], capture['pin']['namespace'],
            capture['pin']['generation'], values[4],
            'agent:' + record['issuer']['principal'])
        _check(admission.id not in admission_ids)
        admission_ids.add(admission.id)
        # Full-P request_digest and historical source principal belong to THIS
        # mint; source_admission_id, parent H(P) and U wrapper H are distinct.
        _capture_admission(conn, dict(
            id=admission.id, namespace=admission.namespace,
            generation=admission.generation, request_digest=admission.request_digest,
            admitted_by=admission.admitted_by), capture, budget)
        result.append(HistoricalCodingCapability(*values, _TYPES, admission))
        parent_raw = base64.b64decode(capture['grant_bytes_base64'], validate=True)
        parent = json.loads(parent_raw)
        _check(record['parent_id'] == parent['id'])
        if parent['format'] == 'workflow-coding-setup-authority@1':
            _check(record['level'] == 'source')
            # U ID selects its genuine COMPLETE issuance.setup subtree, not a
            # row-ID alias or wrapper digest. Reused cache retains full prefix,
            # own admissions/publications and terminal original ISSUE Q.
            setup_history = setups.setup(parent['id'], parent_raw)
            return CodingCapabilityHistory(tuple(reversed(result)), setup_history)
        _check(parent['format'] == _SOURCE_PIN
               and parent['id'] not in identities and len(result) < _ANCESTRY_LIMIT)
        values = _native_capability(conn, org_slug, parent['id'], budget)
        _check(values[3] == parent_raw and values[4] == capture['grant_digest'])
        record = parent


def read_capability_history(conn: sqlite3.Connection, *, expected_org_slug: str,
                            authorization_id: str) -> CodingCapabilityHistory:
    """Read one retained native capability and its complete native P/U closure.

    Caller-owned coherent raw-value read snapshot and native/J structural
    admission are prerequisites; no transaction control occurs here. Fixed
    parameterized SELECTs only. One shared call budget:8192 accepted rows,
    16384 traversal steps,16MiB accepted raw TEXT/BLOB bytes; setup inventory
    cap1024 and selected capability ancestry64. Each variable native value is
    capped at1MiB before fetch; original typeof/genuine nullable owner NULL
    survive. One bounded overflow fetch refuses without partial history.
    This does not bound total SQLite/process/decoded-copy memory.

    The saved parser proves complete contained canonical P/U/Q and immutable
    source, source-versus-own admission, radius/action/refinement/null-SID/effect
    equalities. This reader additionally resolves each native P envelope/own
    admission/publication and complete U issuance prefix. Necessary timestamp
    comparisons prove no actual mint order or real source/task/session/result
    claim. Current setup pointers, source-rule/policy, native/J closure and
    acting service ownership remain separate, mandatory downstream readers.
    No current permission, writer, source verifier, mount or repair is supplied.
    """
    try:
        _check(type(conn) is sqlite3.Connection and conn.in_transaction)
        _check(type(expected_org_slug) is str
               and re.fullmatch(r'[a-z][a-z0-9-]{0,62}', expected_org_slug) is not None)
        _token(authorization_id)
        return _history(conn, expected_org_slug, authorization_id)
    except (sqlite3.Error, CodingSavedRecordError, CodingAuthorityHistoryError,
            ValueError, TypeError, KeyError, UnicodeError, RecursionError, OverflowError):
        raise CodingCapabilityHistoryError(_REFUSAL) from None
