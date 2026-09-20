"""THR-181 Track A — pre-escalation authority evaluator seam + hook.

This module wires the release-controlled policy (``authority_policy.py``)
into the orchestrator's manager-root escalation commit point. It defines:

* the immutable per-attempt input snapshot;
* the closed output schema the evaluator must produce;
* the authenticated manager-completion self-evaluation parser, plus the
  deterministic injectable evaluator seam retained for strict tests;
* ``run_authority_hook`` — the single hook the orchestrator calls before a
  manager root's proposed escalation is committed. It returns
  ``"continue_same_root"`` (the named same-root permitted action was executed)
  or ``"escalate"`` (fail closed: the existing escalation path proceeds).

Invariants (THR-181; see ``docs/agent-guides/orchestrator-contracts.md`` —
``Active team authority policy``):

* Exactly ONE audited evaluation per eligible attempt; the DB is the
  single-evaluation and exactly-once-consumption guard (UNIQUE candidate_id
  on ``authority_evaluations``, ``created -> evaluated -> consumed`` CAS).
* The proposed escalation reason is UNTRUSTED input: only its digest is ever
  persisted; the raw reason is passed to the evaluator and never stored.
* Audit failure cannot permit continuation: every audit event and the
  outcome row are written BEFORE (or atomically WITH) the continuation; any
  audit failure fails closed to ESCALATE.
* Every hook-eligible attempt records exactly one ``authority_hook``
  audit_log outcome row (the denominator), or fails closed so that no
  continuation can occur even when recording is impossible.
* Server-owned mechanical fences are non-overridable by any policy output.
* Structured SERVER-derived facts (budget counters/ceilings, lineage, active
  work, cancellation/block/session state, adverse child review verdicts,
  zombie/partial-work evidence, org permission digest, DB schema digest) are
  captured with provenance into the evaluation snapshot; a server-PROVEN
  must-escalate fact (adverse child verdict, partial-work evidence, DB-schema
  drift vs the release-pinned schema) forces ESCALATE regardless of what the
  untrusted reason prose claims, and a protected-surface change during the
  attempt (org permission digest or live schema digest drift) fails closed
  with the matched clause — neither a misleading nor an omitted reason can
  authorize CONTINUE_SAME_ROOT.
* CONTINUE_SAME_ROOT is granted ONLY when the proposed reason is a BYTE-EXACT
  member of the release-controlled closed routine set
  (``CONTINUE_ACCEPTED_REASONS``) AND every server-derived predicate is
  clean: the server then has complete knowledge of the prose, so the grant
  never depends on keyword classification or the completeness/truthfulness
  of untrusted reason prose for any protected boundary. Any other reason —
  including a semantically similar paraphrase — is not verifiable as routine
  and fails closed to ESCALATE. The exact narrow permitted action (return the
  current root to pending + re-enqueue) is additionally server-proven safe
  across every protected category (fixed audited transaction; no schema/
  permission/auth/compatibility/destructive/external side effects; spend
  bounded by the budget fence; reversible and re-escalatable).
* The single-use continuation lifecycle envelope: every CONTINUE_SAME_ROOT commit
  atomically mints a single-use ``authority_continue_envelopes`` row
  (bound to the evaluation/candidate, the immutable causal task-result
  row, the matched policy clause, and the same-root grant;
  ``active -> consumed | violated`` exactly-once with a DB-enforced finite
  lifecycle). The daemon-mediated completion point consumes it for the next
  normally validated manager result; it is not an exact-action whitelist.
  Supersession/fresh-root replacement remains outside the same-root grant.
  Cancellation/session-failure/restart windows spend the envelope
  fail-closed so the continuation is never re-used. A fixed phrase or
  untrusted reason truthfulness is NEVER safety proof on its own — the
  lifecycle envelope remains an identity, replay, and audit fence. Continued
  turns use the manager agent's ordinary configured executor permissions.
* Thread id/origin remain structured lineage provenance and are neither an
  initial eligibility fence nor a final continuation-CAS rejection. They do
  not relax the same-root or no-revisit/no-successor rules.
* The final continuation CAS atomically re-validates the COMPLETE current
  fence set at consumption time (candidate/policy/input identity, manager
  ownership and session, exact team, root status, cancellation, block/
  active-work, revisit/successor lineage, budgets, zombie/partial-
  work, adverse child verdicts) inside the same transaction as the
  continuation + audit rows.
* Candidate identity is derived from the IMMUTABLE task-result/session
  causality (the persisted ``task_results`` row id), never from a freshly
  written orchestration-step audit id — so real restart/recovery re-entry
  cannot mint a second candidate or evaluation.
* Historical census eligibility is NEVER consulted; reachability depends only
  on a release-controlled policy for the team and a current manager-owned
  root.
"""
from __future__ import annotations

import hashlib
import json
import logging
import subprocess
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol

from pydantic import BaseModel, Field, field_validator

from runtime.infrastructure.database import _authority_claim_key
from runtime.models import (
    AuthorityDisposition,
    AuthorityDispositionCode,
    AuthorityFenceResult,
    AuthorityPolicyV2PermissionSurface,
    AuthorityPolicyV2SchemaIntegrity,
    TaskStatus,
    ManagerSelfEvaluation,
    validate_authority_digest,
    validate_authority_version,
)
from runtime.orchestrator.authority_policy import (
    ACTION_CONTINUE_SAME_ROOT,
    ACTION_ESCALATE_TO_FOUNDER,
    CLOSED_ACTIONS,
    CONTINUE_ACCEPTED_REASONS,
    PROMPT_DIGEST,
    PROMPT_ID,
    PROMPT_VERSION,
    POLICY_BY_TEAM,
    AuthorityPolicy,
    build_authority_evaluation_prompt,
)

if TYPE_CHECKING:
    from runtime.models import TaskRecord
    from runtime.orchestrator.orchestrator import Orchestrator

logger = logging.getLogger(__name__)

# Outcome vocabulary of the ``authority_hook`` audit_log record. Exactly one
# such record is written per hook-eligible attempt (the denominator).
OUTCOME_CONTINUED_SAME_ROOT = "continued_same_root"
OUTCOME_ESCALATED = "escalated"
OUTCOME_INELIGIBLE = "ineligible"
OUTCOME_CAS_LOST = "cas_lost"
OUTCOME_CANCELLED_STALE = "cancelled_stale"
OUTCOME_EVALUATOR_FAILURE = "evaluator_failure"
OUTCOME_AUDIT_FAILURE = "audit_failure"
OUTCOME_CAPTURE_FAILURE = "capture_failure"

AUDIT_ACTION_HOOK_OUTCOME = "authority_hook"
AUDIT_ACTION_CONTINUED_SAME_ROOT = "authority_continued_same_root"
AUDIT_ACTION_ENVELOPE_CONSUMED = "authority_continue_envelope_consumed"
AUDIT_ACTION_ENVELOPE_VIOLATED = "authority_continue_envelope_violated"

# Production evaluator bounds. A bounded invocation is part of the fail-closed
# contract: a hang must surface as a timeout disposition, never a stall.
DEFAULT_EVALUATOR_TIMEOUT_SECONDS = 60.0
DEFAULT_EXECUTOR_KIND = "pi"

# Minimum confidence required for a CONTINUE_SAME_ROOT verdict; anything
# below fails closed to ESCALATE (LOW_CONFIDENCE).
CONTINUE_MIN_CONFIDENCE = 0.5

# Diagnostic fail-closed disposition codes that are preserved verbatim into
# the recorded verdict (audit fidelity for uncertainty/error codes); anything
# else on a fail-closed verdict normalizes to the code of its own branch.
_DIAGNOSTIC_FAILURE_CODES = frozenset({
    AuthorityDispositionCode.TIMEOUT,
    AuthorityDispositionCode.MALFORMED_OUTPUT,
    AuthorityDispositionCode.INJECTION_GUARD,
    AuthorityDispositionCode.LOW_CONFIDENCE,
    AuthorityDispositionCode.EVALUATOR_ERROR,
    AuthorityDispositionCode.AUDIT_FAILURE,
})

# Closed uncertainty-code vocabulary (mirrored in the policy prompt).
CLOSED_UNCERTAINTY_CODES = frozenset({
    "low_confidence", "ambiguous", "missing_evidence",
    "conflicting_evidence", "novel",
})

_CREDENTIAL_MARKERS = (
    "bearer",
    "authorization",
    "api_key",
    "apikey",
    "password",
    "credential=",
    "credential:",
    "token=",
    "sk-",
    "private_key",
    "client_secret",
)

_TERMINAL_STATUSES = frozenset({
    TaskStatus.COMPLETED.value,
    TaskStatus.FAILED.value,
    TaskStatus.SUPERSEDED.value,
    TaskStatus.CANCELLED.value,
})


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# Verified-approve verdict vocabulary used by the adverse-review server fact.
_APPROVED_VERDICTS = frozenset({"APPROVE", "PASS"})


# The release-expected DB schema digest: the schema a FRESH Database() built
# from the CURRENT code creates. Any divergence of the live DB from this
# release schema is an authoritative schema/migration drift signal — the
# surface the continuation would operate on is not the reviewed release
# surface, so a schema/migration condition is in flight and the attempt must
# escalate. Computed once per process and cached.
_release_schema_digest_cache: str | None = None


def _release_schema_digest() -> str:
    """Digest of the sqlite_master DDL a fresh Database() creates with the
    current code (the release-pinned schema surface). Cached after first
    computation; never raises (returns "unavailable" on any defect, which
    fails closed as a drift signal)."""
    global _release_schema_digest_cache
    if _release_schema_digest_cache is not None:
        return _release_schema_digest_cache
    try:
        import tempfile
        from pathlib import Path as _Path
        from runtime.infrastructure.database import Database
        with tempfile.TemporaryDirectory() as td:
            fresh = Database(_Path(td) / "fresh-authority-schema.db")
            try:
                _release_schema_digest_cache = _live_schema_digest(fresh)
            finally:
                try:
                    fresh._conn.close()
                except Exception:
                    pass
    except Exception:
        _release_schema_digest_cache = "unavailable"
    return _release_schema_digest_cache


def _live_schema_digest(db) -> str:
    """Digest of the live DB's sqlite_master DDL (same canonical query the
    release digest uses, so the two are directly comparable)."""
    try:
        rows = db.execute(
            "SELECT sql FROM sqlite_master WHERE sql IS NOT NULL ORDER BY name"
        ).fetchall()
        return _sha256("\n".join(str(r[0]) for r in rows))
    except Exception:
        return "unavailable"


# ── THR-229 C3a: independent constraint-sensitive v2 schema-integrity seam ──
#
# ``_release_schema_digest`` above is the LEGACY v1 behavior and stays exactly
# as it is: it compares a live DB's raw DDL against a fresh ``Database()`` and
# treats ANY difference as a drift signal.  A historical database migrated
# forward by the current source legitimately differs from a fresh one in only
# two ordered table layouts (``threads`` / ``thread_messages``), so the raw
# digest alone cannot distinguish that accepted historical representation from
# real constraint drift.  The functions below are the accepted v2
# full-schema oracle: an INDEPENDENT, READ-ONLY, constraint-sensitive gate
# whose reference is built from fresh current source plus only the two accepted
# exact migrated table substitutions.  They produce integrity EVIDENCE only —
# never policy authority, a clause match, or a grant — and they never repair
# the candidate.

V2_SCHEMA_INTEGRITY_CONTRACT = "authority-policy-v2-schema-integrity-v1"
V2_PERMISSION_SURFACE_CONTRACT = "authority-policy-v2-permission-surface-v1"

# Exact ordered ``CREATE TABLE`` bytes the current source produces when it
# migrates the immutable historical constructor
# (``f39b4934611ca13ab7d8b7fa2d7be983a4bfb7a5``) forward.  These are the ONLY
# accepted historical substitutions; every other object must match fresh
# current source exactly.  ``threads`` and ``thread_messages`` are the only
# two tables whose ordered layout differs between fresh and migrated.
_V2_MIGRATED_TABLE_CREATE_SQL: dict[str, str] = {
    "threads": (
        "CREATE TABLE threads (\n"
        "                id TEXT PRIMARY KEY,\n"
        "                subject TEXT NOT NULL,\n"
        "                started_at TEXT NOT NULL,\n"
        "                archived_at TEXT,\n"
        "                status TEXT NOT NULL DEFAULT 'open',\n"
        "                forwarded_from_id TEXT,\n"
        "                forwarded_from_kind TEXT,\n"
        "                turn_cap INTEGER NOT NULL DEFAULT 500,\n"
        "                turns_used INTEGER NOT NULL DEFAULT 0,\n"
        "                summary TEXT,\n"
        "                transcript_path TEXT\n"
        "            , composed_by TEXT NOT NULL DEFAULT 'founder',"
        " composed_from_task_id TEXT, composed_from_dream_id TEXT,"
        " pinned_at TEXT, mention_routing_enabled INTEGER NOT NULL DEFAULT 1)"
    ),
    "thread_messages": (
        "CREATE TABLE thread_messages (\n"
        "                id INTEGER PRIMARY KEY AUTOINCREMENT,\n"
        "                thread_id TEXT NOT NULL,\n"
        "                seq INTEGER NOT NULL,\n"
        "                speaker TEXT NOT NULL,\n"
        "                kind TEXT NOT NULL,\n"
        "                body_markdown TEXT,\n"
        "                addressed_to_json TEXT,\n"
        "                decline_reason TEXT,\n"
        "                system_payload_json TEXT,\n"
        "                sent_from_task_id TEXT,\n"
        "                created_at TEXT NOT NULL, mentions_json TEXT,\n"
        "                FOREIGN KEY (thread_id) REFERENCES threads(id)\n"
        "            )"
    ),
}

_V2_INVENTORY_KINDS = ("tables", "indexes", "triggers", "views")
_V2_SCHEMA_REFERENCE_CACHE: list[dict] | None = None


@dataclass(frozen=True)
class AuthorityPolicyV2SchemaIntegrityOutcome:
    """Result of the v2 schema-integrity capture: bounded evidence on success,
    a bounded machine-readable diagnostic on fail-closed refusal.  Exactly one
    of ``evidence`` / ``diagnostic`` is set."""

    evidence: AuthorityPolicyV2SchemaIntegrity | None
    diagnostic: dict[str, object] | None


def _v2_optional_text(value) -> object:
    return None if value is None else str(value)


def _v2_is_v2_object(name: str) -> bool:
    return str(name).startswith("authority_policy_v2_")


def _v2_is_reserved_internal_name(name: str) -> bool:
    """True only for SQLite's reserved internal ``sqlite_`` prefix.

    SQLite refuses to create a user object whose name begins with ``sqlite_``
    in ANY ASCII case ("object name reserved for internal use"), so that exact
    prefix is the internal namespace.  The comparison is case-insensitive to
    match SQLite's own reserved-name rule, and it is a literal prefix test —
    never a SQL ``LIKE`` pattern, whose ``_`` would be a single-character
    wildcard and would wrongly hide legal user objects such as
    ``sqliteXunreviewed``.
    """
    return str(name).lower().startswith("sqlite_")


def _v2_index_xinfo(conn, index_name: str) -> list[list]:
    return [
        [int(seqno), int(cid), _v2_optional_text(name), int(desc), str(coll),
         int(key)]
        for seqno, cid, name, desc, coll, key in conn.execute(
            'SELECT seqno, cid, name, "desc", coll, "key" '
            'FROM pragma_index_xinfo(?) ORDER BY seqno',
            (index_name,),
        )
    ]


def _v2_capture_inventory(conn) -> dict:
    """Complete non-internal schema inventory with ordered constraint
    semantics: full table SQL (CHECK/UNIQUE/FK expressions), ordered
    ``table_xinfo``, ``foreign_key_list``, complete ``index_xinfo`` including
    expression sentinels/collation/key flags/cid, ``index_list``
    origin/unique/partial, explicit index SQL and full trigger/view SQL.

    Only internal ``sqlite_``-prefixed objects (including ``sqlite_sequence``
    and the autoindex names) are excluded from the top-level inventory;
    autoindex constraint metadata is retained inside each table's
    ``index_list`` metadata.  The reserved prefix is matched as a literal,
    case-insensitive prefix (never a SQL ``LIKE`` pattern), so legal user
    objects that merely resemble internal names — e.g. ``sqliteXunreviewed`` —
    stay in the inventory.  ``rootpage`` and allocator/row contents are never
    read.
    """
    tables: dict[str, dict] = {}
    indexes: dict[str, dict] = {}
    triggers: dict[str, dict] = {}
    views: dict[str, dict] = {}
    rows = conn.execute(
        "SELECT type, name, tbl_name, sql FROM sqlite_master "
        "WHERE type IN ('table','index','trigger','view') "
        "ORDER BY type, name"
    ).fetchall()
    for row in rows:
        typ, name, tbl_name, sql = row[0], row[1], row[2], row[3]
        if _v2_is_reserved_internal_name(name):
            continue
        if typ == "table":
            xinfo = [
                [int(cid), str(cname), _v2_optional_text(ctype), int(notnull),
                 _v2_optional_text(dflt), int(pk), int(hidden)]
                for cid, cname, ctype, notnull, dflt, pk, hidden in conn.execute(
                    'SELECT cid, name, type, "notnull", dflt_value, pk, hidden '
                    'FROM pragma_table_xinfo(?) ORDER BY cid',
                    (name,),
                )
            ]
            fks = [
                [int(fid), int(seq), str(rtable), str(src),
                 _v2_optional_text(dst), str(on_update), str(on_delete),
                 str(match)]
                for fid, seq, rtable, src, dst, on_update, on_delete, match in
                conn.execute(
                    'SELECT id, seq, "table", "from", "to", on_update, '
                    'on_delete, match FROM pragma_foreign_key_list(?) '
                    'ORDER BY id, seq',
                    (name,),
                )
            ]
            index_meta: dict[str, dict] = {}
            for _seq, iname, unique, origin, partial in conn.execute(
                'SELECT seq, name, "unique", origin, partial '
                'FROM pragma_index_list(?)',
                (name,),
            ):
                index_meta[str(iname)] = {
                    "origin": str(origin),
                    "unique": int(unique),
                    "partial": int(partial),
                    "xinfo": _v2_index_xinfo(conn, str(iname)),
                }
            tables[str(name)] = {
                "sql": _v2_optional_text(sql),
                "xinfo": xinfo,
                "fks": fks,
                "indexes": index_meta,
            }
        elif typ == "index":
            indexes[str(name)] = {
                "sql": _v2_optional_text(sql),
                "tbl": str(tbl_name),
                "xinfo": _v2_index_xinfo(conn, str(name)),
            }
        elif typ == "trigger":
            triggers[str(name)] = {
                "sql": _v2_optional_text(sql),
                "tbl": str(tbl_name),
            }
        else:
            views[str(name)] = {
                "sql": _v2_optional_text(sql),
                "tbl": str(tbl_name),
            }
    return {
        "tables": tables,
        "indexes": indexes,
        "triggers": triggers,
        "views": views,
    }


def _v2_apply_migrated_substitutions(conn, fresh: dict) -> dict:
    """Apply ONLY the two accepted migrated table substitutions to the fresh
    reference connection, then re-capture.  SQLite itself derives the ordered
    column and index-cid consequences; no allowlist is learned from any
    candidate database."""
    explicit_index_sql = [
        meta["sql"]
        for meta in fresh["indexes"].values()
        if meta["tbl"] in _V2_MIGRATED_TABLE_CREATE_SQL and meta["sql"]
    ]
    conn.execute("PRAGMA foreign_keys=OFF")
    conn.execute("DROP TABLE IF EXISTS thread_messages")
    conn.execute("DROP TABLE IF EXISTS threads")
    for table in ("threads", "thread_messages"):
        conn.execute(_V2_MIGRATED_TABLE_CREATE_SQL[table])
    for sql in explicit_index_sql:
        conn.execute(sql)
    return _v2_capture_inventory(conn)


def _v2_build_reference_inventories() -> list[dict] | None:
    """Build the accepted reference inventories fresh from current source.

    Returns ``[fresh, migrated]`` — the two and only two accepted ordered
    representations — or ``None`` when the reference cannot be constructed
    (fail closed).  The evaluated candidate database is never consulted.
    """
    global _V2_SCHEMA_REFERENCE_CACHE
    if _V2_SCHEMA_REFERENCE_CACHE is not None:
        return _V2_SCHEMA_REFERENCE_CACHE
    try:
        import tempfile
        from pathlib import Path as _Path
        from runtime.infrastructure.database import Database

        with tempfile.TemporaryDirectory() as td:
            reference = Database(_Path(td) / "v2-schema-reference.db")
            try:
                conn = reference._conn
                fresh = _v2_capture_inventory(conn)
                migrated = _v2_apply_migrated_substitutions(conn, fresh)
            finally:
                try:
                    reference._conn.close()
                except Exception:
                    pass
        _V2_SCHEMA_REFERENCE_CACHE = [fresh, migrated]
    except Exception:
        return None
    return _V2_SCHEMA_REFERENCE_CACHE


def _v2_inventory_digest(inventory: dict) -> str:
    return _sha256(json.dumps(inventory, sort_keys=True, separators=(",", ":")))


def _v2_inventory_mismatches(reference: dict, candidate: dict) -> list[dict]:
    """Bounded structural mismatches between an accepted reference and the
    candidate.  Each diagnostic names a category, an object kind/name and
    whether the object is a v2 object; no raw schema/data/model prose."""
    out: list[dict] = []

    def _diag(code: str, kind: str, name: str) -> dict:
        return {
            "code": code,
            "kind": kind,
            "object": name,
            "v2": _v2_is_v2_object(name),
        }

    for kind in _V2_INVENTORY_KINDS:
        ref_names = reference[kind]
        cand_names = candidate[kind]
        for name in sorted(set(ref_names) - set(cand_names)):
            code = "missing_v2_object" if _v2_is_v2_object(name) else "missing_object"
            out.append(_diag(code, kind, name))
        for name in sorted(set(cand_names) - set(ref_names)):
            out.append(_diag("unexpected_object", kind, name))
    table_names = sorted(set(reference["tables"]) & set(candidate["tables"]))
    for name in table_names:
        ref = reference["tables"][name]
        cand = candidate["tables"][name]
        if ref["sql"] != cand["sql"]:
            out.append(_diag("table_sql_mismatch", "table", name))
        if ref["xinfo"] != cand["xinfo"]:
            out.append(_diag("table_column_layout_mismatch", "table", name))
        if ref["fks"] != cand["fks"]:
            out.append(_diag("table_foreign_key_mismatch", "table", name))
        for iname in sorted(set(ref["indexes"]) - set(cand["indexes"])):
            code = "missing_v2_object" if _v2_is_v2_object(iname) else "missing_object"
            out.append(_diag(code, "index", iname))
        for iname in sorted(set(cand["indexes"]) - set(ref["indexes"])):
            out.append(_diag("unexpected_object", "index", iname))
        for iname in sorted(set(ref["indexes"]) & set(cand["indexes"])):
            if ref["indexes"][iname] != cand["indexes"][iname]:
                out.append(_diag("table_index_metadata_mismatch", "index", iname))
    index_names = sorted(set(reference["indexes"]) & set(candidate["indexes"]))
    for name in index_names:
        ref = reference["indexes"][name]
        cand = candidate["indexes"][name]
        if ref["sql"] != cand["sql"]:
            out.append(_diag("index_sql_mismatch", "index", name))
        if ref["xinfo"] != cand["xinfo"]:
            out.append(_diag("index_xinfo_mismatch", "index", name))
    trigger_names = sorted(set(reference["triggers"]) & set(candidate["triggers"]))
    for name in trigger_names:
        if reference["triggers"][name]["sql"] != candidate["triggers"][name]["sql"]:
            out.append(_diag("trigger_sql_mismatch", "trigger", name))
    view_names = sorted(set(reference["views"]) & set(candidate["views"]))
    for name in view_names:
        if reference["views"][name]["sql"] != candidate["views"][name]["sql"]:
            out.append(_diag("view_sql_mismatch", "view", name))
    return out


def _v2_closest_mismatches(references: list[dict], candidate: dict) -> list[dict]:
    """Diagnose against the accepted reference that the candidate is closest
    to (fewest structural mismatches); ties keep the fresh reference first."""
    scored = [
        (len(_v2_inventory_mismatches(reference, candidate)), position, reference)
        for position, reference in enumerate(references)
    ]
    scored.sort(key=lambda item: (item[0], item[1]))
    return _v2_inventory_mismatches(scored[0][2], candidate)


def _v2_data_integrity_check(conn) -> dict | None:
    """Require ``integrity_check`` exactly ``ok`` and zero
    ``foreign_key_check`` violations; a read defect fails closed."""
    try:
        rows = conn.execute("PRAGMA integrity_check").fetchall()
    except Exception:
        return {"code": "integrity_check_unavailable", "kind": "data",
                "object": None, "v2": False}
    if [tuple(row) for row in rows] != [("ok",)]:
        return {"code": "integrity_check_failed", "kind": "data",
                "object": None, "v2": False}
    try:
        violations = conn.execute("PRAGMA foreign_key_check").fetchall()
    except Exception:
        return {"code": "foreign_key_check_unavailable", "kind": "data",
                "object": None, "v2": False}
    if violations:
        return {"code": "foreign_key_check_failed", "kind": "data",
                "object": None, "v2": False}
    return None


def capture_authority_policy_v2_schema_integrity(
    db,
) -> AuthorityPolicyV2SchemaIntegrityOutcome:
    """Read-only capture of constraint-sensitive v2 schema-integrity evidence.

    The candidate's complete non-internal inventory must match an accepted
    reference layout exactly, ``integrity_check`` must be exactly ``ok`` and
    ``foreign_key_check`` must return zero violations.  On success the returned
    outcome carries typed evidence holding the candidate's ACTUAL raw DDL
    digest.  Any unknown layout, read/query error or unavailable reference
    fails closed with a bounded machine-readable diagnostic and no evidence.
    The candidate is never repaired or mutated.

    Every candidate read and the frozen raw digest run inside ONE
    ``Database.coherent_read_view()``: the shared-connection lock is held for
    the whole capture and a single SQLite read snapshot is pinned, so a commit
    on an independent connection cannot produce evidence assembled from an old
    inventory plus a new digest.  A mutation invisible to that coherent
    snapshot is caught by the subsequent ``recheck``.
    """
    references = None
    try:
        references = _v2_build_reference_inventories()
    except Exception:
        references = None
    if not references:
        return AuthorityPolicyV2SchemaIntegrityOutcome(
            evidence=None,
            diagnostic={"code": "reference_unavailable", "kind": "reference",
                        "object": None, "v2": False},
        )
    try:
        with db.coherent_read_view() as conn:
            candidate = _v2_capture_inventory(conn)
            if not any(
                not _v2_inventory_mismatches(reference, candidate)
                for reference in references
            ):
                mismatches = _v2_closest_mismatches(references, candidate)
                diagnostic = mismatches[0] if mismatches else {
                    "code": "inventory_mismatch", "kind": "inventory",
                    "object": None, "v2": False,
                }
                return AuthorityPolicyV2SchemaIntegrityOutcome(
                    evidence=None, diagnostic=diagnostic,
                )
            data_diagnostic = _v2_data_integrity_check(conn)
            if data_diagnostic is not None:
                return AuthorityPolicyV2SchemaIntegrityOutcome(
                    evidence=None, diagnostic=data_diagnostic,
                )
            raw_digest = _live_schema_digest(db)
            if not isinstance(raw_digest, str) or raw_digest == "unavailable":
                return AuthorityPolicyV2SchemaIntegrityOutcome(
                    evidence=None,
                    diagnostic={"code": "candidate_digest_unavailable",
                                "kind": "candidate", "object": None, "v2": False},
                )
            evidence = AuthorityPolicyV2SchemaIntegrity(
                contract_version=V2_SCHEMA_INTEGRITY_CONTRACT,
                raw_digest=raw_digest,
                inventory_digest=_v2_inventory_digest(candidate),
                object_count=sum(
                    len(candidate[kind]) for kind in _V2_INVENTORY_KINDS
                ),
            )
            return AuthorityPolicyV2SchemaIntegrityOutcome(
                evidence=evidence, diagnostic=None,
            )
    except Exception:
        return AuthorityPolicyV2SchemaIntegrityOutcome(
            evidence=None,
            diagnostic={"code": "candidate_unreadable", "kind": "candidate",
                        "object": None, "v2": False},
        )


def recheck_authority_policy_v2_schema_integrity(
    evidence: AuthorityPolicyV2SchemaIntegrity | None,
    db,
) -> bool:
    """Deny ANY later raw-digest drift from the captured candidate.

    A ``None`` (failed/unavailable) capture can never become a successful
    recheck, and matching a *different* accepted layout after capture does not
    authorize the changed attempt because the comparison is against the exact
    raw digest frozen at capture time.
    """
    if evidence is None:
        return False
    if getattr(evidence, "contract_version", None) != V2_SCHEMA_INTEGRITY_CONTRACT:
        return False
    raw_digest = getattr(evidence, "raw_digest", None)
    if not isinstance(raw_digest, str) or len(raw_digest) != 64:
        return False
    try:
        current = _live_schema_digest(db)
    except Exception:
        return False
    if not isinstance(current, str) or current == "unavailable":
        return False
    return current == raw_digest


@dataclass(frozen=True)
class AuthorityPolicyV2PermissionSurfaceOutcome:
    """Result of the v2 permission-surface capture: bounded evidence on
    success, a bounded machine-readable diagnostic on fail-closed refusal.
    Exactly one of ``evidence`` / ``diagnostic`` is set.  There is deliberately
    no permissive default and no sentinel digest: an absent reader, a read
    defect, or a malformed value all yield ``evidence=None``."""

    evidence: AuthorityPolicyV2PermissionSurface | None
    diagnostic: dict[str, object] | None


def _v2_permission_reader(db):
    """Return the server-side permission reader bound on ``db`` or ``None``.

    The reader is the narrowly scoped server-side orchestration seam; it is
    never a caller-provided allow/deny boolean or a precomputed digest.
    """
    return getattr(db, "_v2_permission_surface_reader", None)


def capture_authority_policy_v2_permission_surface(
    db, agent: str,
) -> AuthorityPolicyV2PermissionSurfaceOutcome:
    """Read the current permission-surface digest through the bound reader.

    The reader is called as ``reader(agent)`` inside the server process.  An
    unbound reader, a raising read, or a value that is not exactly one 64-char
    lower-hex digest fails closed with a bounded diagnostic and NO evidence, so
    a later recheck can never authenticate a sentinel.
    """
    reader = _v2_permission_reader(db)
    if reader is None:
        return AuthorityPolicyV2PermissionSurfaceOutcome(
            evidence=None,
            diagnostic={"code": "permission_surface_unavailable",
                        "kind": "permission", "object": None, "v2": False},
        )
    try:
        digest = reader(agent)
    except Exception:
        return AuthorityPolicyV2PermissionSurfaceOutcome(
            evidence=None,
            diagnostic={"code": "permission_surface_unreadable",
                        "kind": "permission", "object": None, "v2": False},
        )
    if (
        not isinstance(digest, str)
        or len(digest) != 64
        or any(character not in "0123456789abcdef" for character in digest)
    ):
        return AuthorityPolicyV2PermissionSurfaceOutcome(
            evidence=None,
            diagnostic={"code": "permission_surface_malformed",
                        "kind": "permission", "object": None, "v2": False},
        )
    return AuthorityPolicyV2PermissionSurfaceOutcome(
        evidence=AuthorityPolicyV2PermissionSurface(
            contract_version=V2_PERMISSION_SURFACE_CONTRACT, digest=digest,
        ),
        diagnostic=None,
    )


def recheck_authority_policy_v2_permission_surface(
    evidence: AuthorityPolicyV2PermissionSurface | None,
    db, agent: str,
) -> bool:
    """Deny ANY later permission-surface change or unreadable read.

    The comparison is against the reader's CURRENT value through the same
    server-side seam; an unavailable reader, read defect or malformed value
    never becomes a successful recheck.
    """
    if evidence is None:
        return False
    if getattr(evidence, "contract_version", None) != V2_PERMISSION_SURFACE_CONTRACT:
        return False
    digest = getattr(evidence, "digest", None)
    if not isinstance(digest, str) or len(digest) != 64:
        return False
    current = capture_authority_policy_v2_permission_surface(db, agent)
    if current.evidence is None:
        return False
    return current.evidence.digest == digest


def _permission_digest(orch: "Orchestrator", agent: str) -> str:
    """Digest of the current org permission surface (org_config + the active
    agent definition). ``orch`` may be None in unit contexts — the digest
    then degrades to "unavailable" (a change is then treated as drift,
    failing closed)."""
    if orch is None:
        return "unavailable"
    parts: list[str] = []
    try:
        from runtime.orchestrator.org_config import load_org_config
        parts.append(load_org_config(orch._paths).model_dump_json(sort_keys=True))
    except Exception:
        parts.append("org_config:unavailable")
    try:
        from runtime.orchestrator.prompt_loader import load_agent
        ad = load_agent(orch._paths, agent)
        if ad is not None:
            parts.append(
                json.dumps(
                    {"name": ad.name, "allow_rules": sorted(ad.allow_rules)},
                    sort_keys=True,
                )
            )
        else:
            parts.append("agent_def:unavailable")
    except Exception:
        parts.append("agent_def:unavailable")
    return _sha256("\x1f".join(parts))


def _strict_permission_surface_digest(orch: "Orchestrator", agent: str) -> str:
    """Read the live org permission surface or RAISE (never a sentinel digest).

    This is the C3b claim/evidence server-side reader: unlike the legacy
    ``_permission_digest`` it never degrades a read failure into a digest of
    ``unavailable`` markers, so ``capture_authority_policy_v2_permission_surface``
    fails closed when the surface cannot be read.  Bind it as the reader via
    ``AuthorityPolicyStore.bind_v2_permission_surface_reader`` (partial on the
    concrete orchestrator) before a v2 claim.
    """
    from runtime.orchestrator.org_config import load_org_config
    from runtime.orchestrator.prompt_loader import load_agent

    import dataclasses

    org_config = load_org_config(orch._paths)
    parts = [json.dumps(dataclasses.asdict(org_config), sort_keys=True, default=str)]
    agent_def = load_agent(orch._paths, agent)
    if agent_def is None:
        raise ValueError("active agent definition unavailable")
    parts.append(
        json.dumps(
            {"name": agent_def.name, "allow_rules": sorted(agent_def.allow_rules)},
            sort_keys=True,
        )
    )
    return _sha256("\x1f".join(parts))


def _is_accepted_routine_reason(reason: str) -> bool:
    """Server predicate over the prose: the proposed reason is a BYTE-EXACT
    member of the release-controlled closed routine set. The server then has
    complete knowledge of the reason's content, so a CONTINUE grant never
    depends on keyword classification, completeness, or truthfulness of
    untrusted prose. Any other reason is not verifiable as routine and fails
    closed to ESCALATE."""
    return reason in CONTINUE_ACCEPTED_REASONS


def _during_attempt_drift_clause(
    orch: "Orchestrator",
    agent: str,
    snapshot: "AuthorityInputSnapshot",
) -> str | None:
    """Authoritative server predicate at the post-evaluation recheck: if the
    org permission surface or the live DB schema digest captured in the
    snapshot changed while the evaluator ran, a permission/sandbox/allow-rule
    or schema condition is in flight and the attempt fails closed with the
    matched clause. A read defect fails closed too (digest unknown)."""
    facts = snapshot.structured_facts
    try:
        perm = json.loads(facts.get("org_permission", "{}"))
        if perm.get("digest") and perm["digest"] != _permission_digest(orch, agent):
            return "esc-permission-sandbox-allow"
    except (TypeError, ValueError):
        return "esc-permission-sandbox-allow"
    try:
        schema = json.loads(facts.get("db_schema", "{}"))
        if schema.get("digest") and schema["digest"] != _live_schema_digest(orch._db):
            return "esc-schema-overloaded-column"
    except (TypeError, ValueError):
        return "esc-schema-overloaded-column"
    return None


def _server_fact_clause(structured_facts: dict[str, str]) -> str | None:
    """Return the policy clause id that the structured SERVER facts PROVE.

    The server derives these facts from authoritative runtime/database state
    (never from the untrusted reason prose). When a fact is present the
    attempt must escalate regardless of what the reason text claims — a
    misleading or omitted reason cannot authorize continuation. Both the
    hook (shipping seam) and the strict CI fake apply this gate.
    """
    try:
        adverse = json.loads(structured_facts.get("adverse_review", "{}"))
        if adverse.get("value"):
            return "esc-adverse-review-qa"
    except (TypeError, ValueError):
        pass
    try:
        partial = json.loads(structured_facts.get("partial_work", "{}"))
        if partial.get("value"):
            return "esc-partial-work"
    except (TypeError, ValueError):
        pass
    try:
        schema = json.loads(structured_facts.get("db_schema", "{}"))
        if schema.get("drift"):
            # The live DB schema is not the release-pinned schema: an
            # authoritative schema/migration drift signal.
            return "esc-schema-overloaded-column"
    except (TypeError, ValueError):
        pass
    return None


def _is_terminal_or_cancelled(task: "TaskRecord | None") -> bool:
    if task is None:
        return True
    if task.cancelled_at is not None:
        return True
    return task.status.value in _TERMINAL_STATUSES


# ── Immutable per-attempt input snapshot ─────────────────────────────────

class AuthorityInputSnapshot(BaseModel):
    """The immutable, server-derived input to ONE authority evaluation.

    ``reason`` is the raw proposed escalation prose — transient, passed to the
    evaluator, NEVER persisted. Every other field is server-derived and
    immutable within the attempt; the canonical serialization (with the reason
    replaced by its digest) is the snapshot digest recorded on the candidate.
    """
    model_config = {"extra": "forbid"}

    root_task_id: str
    team: str
    manager_agent: str
    manager_session_id: str
    candidate_id: str
    causal_event_id: str
    causal_event_digest: str
    causal_result_id: str | None = None
    reason: str = ""
    reason_digest: str = ""
    policy_id: str
    policy_version: str
    policy_digest: str
    prompt_id: str
    prompt_version: str
    prompt_digest: str
    model_id: str
    model_version: str
    model_digest: str
    structured_facts: dict[str, str] = Field(default_factory=dict)

    @field_validator(
        "causal_event_digest",
        "reason_digest",
        "policy_digest",
        "prompt_digest",
        "model_digest",
    )
    @classmethod
    def _digests_are_bounded_hex(cls, value, info):
        return validate_authority_digest(value, f"snapshot.{info.field_name}")

    @field_validator("policy_version", "prompt_version", "model_version")
    @classmethod
    def _versions_are_bounded(cls, value, info):
        return validate_authority_version(value, f"snapshot.{info.field_name}")

    def digest(self) -> str:
        """Deterministic snapshot digest (reason is replaced by its digest)."""
        canonical = self.model_dump(mode="json", exclude={"reason"})
        canonical["reason_digest"] = self.reason_digest
        return _sha256(
            json.dumps(canonical, sort_keys=True, ensure_ascii=False)
        )

    def claim_key(self) -> str:
        """The deterministic CAS claim tuple digest (mirrors Database)."""
        return _authority_claim_key(
            self.root_task_id,
            self.manager_session_id,
            self.causal_event_id,
            self.policy_digest,
            self.prompt_digest,
            self.model_digest,
        )

    def derived_candidate_id(self) -> str:
        return f"AUTH-CAND-{self.claim_key()}"


# ── Closed evaluator output schema ───────────────────────────────────────

class AuthorityEvaluatorOutput(BaseModel):
    """The strict, closed schema every evaluator output must satisfy.

    Extra fields are rejected; digest fields are bounded hex; disposition,
    action, and uncertainty codes are closed vocabularies. Anything that
    fails validation is a MALFORMED_OUTPUT fail-closed result — never a
    continuation.
    """
    model_config = {"extra": "forbid"}

    policy_id: str
    policy_version: str
    policy_digest: str
    team: str
    candidate_id: str
    input_digest: str
    disposition: str  # validated against closed vocabulary below
    clause_id: str | None = None
    action: str | None = None
    rationale_digest: str | None = None
    confidence: float = Field(ge=0.0, le=1.0)
    uncertainty_codes: list[str] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)

    @field_validator("policy_digest", "rationale_digest")
    @classmethod
    def _digests_are_bounded_hex(cls, value, info):
        if value is None:
            return None
        return validate_authority_digest(value, f"output.{info.field_name}")

    @field_validator("disposition")
    @classmethod
    def _disposition_is_closed(cls, value):
        if value not in (
            AuthorityDisposition.ESCALATE.value,
            AuthorityDisposition.CONTINUE_SAME_ROOT.value,
        ):
            raise ValueError(f"unknown disposition {value!r}")
        return value

    @field_validator("clause_id")
    @classmethod
    def _clause_id_is_blank_or_token(cls, value):
        if value is None:
            return None
        if not isinstance(value, str) or not value.strip():
            raise ValueError("clause_id must be non-empty")
        return value

    @field_validator("action")
    @classmethod
    def _action_is_closed(cls, value):
        if value is None:
            return None
        if value not in CLOSED_ACTIONS:
            raise ValueError(f"unknown action {value!r}")
        return value

    @field_validator("uncertainty_codes")
    @classmethod
    def _uncertainty_codes_are_closed(cls, value):
        for code in value:
            if not isinstance(code, str) or code not in CLOSED_UNCERTAINTY_CODES:
                raise ValueError(f"unknown uncertainty code {code!r}")
        return value

    @field_validator("evidence_refs")
    @classmethod
    def _evidence_refs_are_bounded(cls, value):
        if len(value) > 32:
            raise ValueError("too many evidence refs")
        for ref in value:
            if not isinstance(ref, str) or not ref.strip():
                raise ValueError("evidence refs must be non-empty strings")
            if len(ref) > 200:
                raise ValueError("evidence refs must be at most 200 chars")
        return value


# ── Evaluator result + seam ──────────────────────────────────────────────

@dataclass(frozen=True)
class AuthorityEvaluationResult:
    """The typed result of one evaluator call (from the strict fake or the
    production subprocess evaluator). The hook re-validates it against the
    policy/snapshot before it may authorize anything."""
    disposition: AuthorityDisposition
    disposition_code: AuthorityDispositionCode
    response_digest: str
    clause_id: str | None = None
    action: str | None = None
    confidence: float = 0.0
    uncertainty_codes: tuple[str, ...] = ()
    evidence_refs: tuple[str, ...] = ()
    rationale_digest: str | None = None
    error: str | None = None


class AuthorityEvaluator(Protocol):
    """Deterministic injectable seam. Production wires the subprocess
    evaluator; CI wires the strict fake. A failing call returns an
    EVALUATOR_ERROR-typed result — it never raises into the hook."""

    model_id: str
    model_version: str
    model_digest: str

    def evaluate(
        self, snapshot: AuthorityInputSnapshot, *, policy: AuthorityPolicy | None = None
    ) -> AuthorityEvaluationResult:
        ...


def _fake_model_identity(kind: str) -> tuple[str, str, str]:
    model_id = f"fake/{kind}"
    model_version = "v1"
    model_digest = _sha256(f"{model_id}:{model_version}")
    return model_id, model_version, model_digest


# ── Strict fake (CI) ─────────────────────────────────────────────────────

class StrictFakeAuthorityEvaluator:
    """Deterministic, strict fake used by CI.

    * Deterministic: identical input produces byte-identical output.
    * Strict: it validates the snapshot (policy/team/digest identity) and its
      own result before returning; misconfiguration raises.
    * Classification: a built-in deterministic keyword classifier mirrors the
      policy clauses (must-escalate categories -> ESCALATE; the narrow
      routine-same-root pattern -> CONTINUE_SAME_ROOT; anything else fails
      closed to ESCALATE). Tests may pin an exact reason string to a
      disposition/clause/action with ``pinned``.
    """
    model_id, model_version, model_digest = _fake_model_identity("strict-authority-evaluator")

    def __init__(
        self,
        *,
        pinned: dict[str, tuple[str, str, str]] | None = None,
    ) -> None:
        # reason -> (disposition, clause_id, action)
        self._pinned = dict(pinned or {})

    # -- policy-mirroring classifier --------------------------------------

    @classmethod
    def classify_reason(cls, reason: str) -> tuple[str, str, str]:
        """Deterministic closed classification over the reason prose.

        Mirrors the Engineering policy: any must-escalate category marker
        matches its clause and returns ESCALATE. CONTINUE_SAME_ROOT is
        returned ONLY for a BYTE-EXACT member of the release-controlled
        closed routine set (``CONTINUE_ACCEPTED_REASONS``) — the server then
        has complete knowledge of the prose, so the grant never depends on
        keyword classification or the completeness/truthfulness of untrusted
        prose. Everything else fails closed to ESCALATE (clause
        esc-ambiguity-novelty).
        """
        lowered = reason.lower()
        markers: tuple[tuple[tuple[str, ...], str], ...] = (
            (("schema", "migration", "overloaded-column", "overloaded column",
              "column semantics", "database structural"), "esc-schema-overloaded-column"),
            (("permission", "sandbox", "allow-rule", "allow rule", "capability"),
             "esc-permission-sandbox-allow"),
            (("auth", "credential", "secret", "security", "privacy", "data access",
              "data-access"), "esc-auth-credentials-security"),
            (("v0", "v1", "compatibility", "compat"), "esc-compatibility"),
            (("spend", "budget", "cost", "quota", "billing"), "esc-spend-budget"),
            (("destructive", "irreversible", "deletion", "data loss"),
             "esc-destructive-irreversible"),
            (("external", "product", "deploy", "deployment", "release", "third-party",
              "hardware dependency", "vendor"), "esc-external-product-deploy"),
            (("review", "qa", "verdict", "request_changes", "revise", "rejected",
              "withheld approval"), "esc-adverse-review-qa"),
            (("ambiguous", "ambiguity", "novel", "unknown condition", "conflicting",
              "missing evidence", "incomplete evidence"), "esc-ambiguity-novelty"),
            (("partial", "incomplete work", "unverifiable"), "esc-partial-work"),
            (("exhausted", "budget", "ceiling", "max steps", "retry", "limit",
              "ceiling exceeded"), "esc-exhausted-limits"),
            (("cancel", "live children", "in-flight"), "esc-cancellation-live-work"),
            (("successor", "supersede", "revisit", "fresh root", "new task"),
             "esc-successor-supersede-revisit"),
        )
        for keywords, clause_id in markers:
            if any(kw in lowered for kw in keywords):
                return AuthorityDisposition.ESCALATE.value, clause_id, ACTION_ESCALATE_TO_FOUNDER
        # Narrow routine same-root pattern: ONLY a byte-exact member of the
        # release-controlled closed routine set — the server has complete
        # knowledge of the prose, so no must-escalate content can be hidden,
        # omitted, or misstated behind keywords.
        if _is_accepted_routine_reason(reason):
            return (
                AuthorityDisposition.CONTINUE_SAME_ROOT.value,
                "cont-routine-same-root",
                ACTION_CONTINUE_SAME_ROOT,
            )
        # Fail closed: an unverifiable reason is ambiguous (the server cannot
        # prove the routine condition from untrusted prose).
        return (
            AuthorityDisposition.ESCALATE.value,
            "esc-ambiguity-novelty",
            ACTION_ESCALATE_TO_FOUNDER,
        )

    # -- AuthorityEvaluator contract --------------------------------------

    def evaluate(
        self, snapshot: AuthorityInputSnapshot, *, policy: AuthorityPolicy | None = None
    ) -> AuthorityEvaluationResult:
        self._validate_snapshot(snapshot, policy=policy)
        # Server-derived facts OUTRANK reason prose (and any pinned verdict):
        # a server-proven must-escalate fact forces ESCALATE even when the
        # untrusted reason omits or misstates the condition.
        server_clause = _server_fact_clause(snapshot.structured_facts)
        if server_clause is not None:
            disposition, clause_id, action = (
                AuthorityDisposition.ESCALATE.value,
                server_clause,
                ACTION_ESCALATE_TO_FOUNDER,
            )
        elif snapshot.reason in self._pinned:
            disposition, clause_id, action = self._pinned[snapshot.reason]
            # The server gate outranks any pinned verdict: a CONTINUE for a
            # reason that is not a byte-exact release-controlled routine
            # phrase is not verifiable as routine and fails closed.
            if (
                disposition == AuthorityDisposition.CONTINUE_SAME_ROOT.value
                and not _is_accepted_routine_reason(snapshot.reason)
            ):
                disposition, clause_id, action = (
                    AuthorityDisposition.ESCALATE.value,
                    "esc-ambiguity-novelty",
                    ACTION_ESCALATE_TO_FOUNDER,
                )
        else:
            disposition, clause_id, action = self.classify_reason(snapshot.reason)
        code = (
            AuthorityDispositionCode.CONTINUE_SAME_ROOT
            if disposition == AuthorityDisposition.CONTINUE_SAME_ROOT.value
            else AuthorityDispositionCode.ESCALATE
        )
        result = AuthorityEvaluationResult(
            disposition=AuthorityDisposition(disposition),
            disposition_code=code,
            response_digest=_sha256(
                json.dumps(
                    {
                        "disposition": disposition,
                        "clause_id": clause_id,
                        "action": action,
                        "input_digest": snapshot.digest(),
                        "candidate_id": snapshot.candidate_id,
                    },
                    sort_keys=True,
                )
            ),
            clause_id=clause_id,
            action=action,
            confidence=1.0 if disposition == AuthorityDisposition.CONTINUE_SAME_ROOT.value else 1.0,
            uncertainty_codes=(),
            evidence_refs=(),
            rationale_digest=_sha256(f"strict-fake:{snapshot.reason_digest}"),
        )
        self._validate_result(snapshot, result)
        return result

    # -- strict validation ------------------------------------------------

    def _validate_snapshot(
        self, snapshot: AuthorityInputSnapshot, *, policy: AuthorityPolicy | None = None
    ) -> None:
        effective_policy = policy or POLICY_BY_TEAM.get(snapshot.team)
        if effective_policy is None:
            raise ValueError(f"no release-controlled policy for team {snapshot.team!r}")
        if (snapshot.policy_id != effective_policy.id
                or snapshot.policy_version != effective_policy.version):
            raise ValueError("snapshot policy identity does not match the release policy")
        if snapshot.policy_digest != effective_policy.digest:
            raise ValueError("snapshot policy digest does not match the release policy")
        if snapshot.prompt_digest != PROMPT_DIGEST:
            raise ValueError("snapshot prompt digest does not match the release prompt")
        if not snapshot.reason:
            raise ValueError("snapshot reason must be non-empty")

    def _validate_result(self, snapshot: AuthorityInputSnapshot, result: AuthorityEvaluationResult) -> None:
        if result.disposition == AuthorityDisposition.CONTINUE_SAME_ROOT:
            if result.clause_id != "cont-routine-same-root":
                raise ValueError("strict fake continue requires clause cont-routine-same-root")
            if result.action != ACTION_CONTINUE_SAME_ROOT:
                raise ValueError("strict fake continue requires action continue_same_root")
            if result.disposition_code != AuthorityDispositionCode.CONTINUE_SAME_ROOT:
                raise ValueError("strict fake continue requires code continue_same_root")
        else:
            if result.disposition not in (
                AuthorityDisposition.ESCALATE,
                AuthorityDisposition.EVALUATOR_ERROR,
            ):
                raise ValueError("strict fake only emits escalate/evaluator_error dispositions")
        if result.response_digest != _sha256(
            json.dumps(
                {
                    "disposition": result.disposition.value,
                    "clause_id": result.clause_id,
                    "action": result.action,
                    "input_digest": snapshot.digest(),
                    "candidate_id": snapshot.candidate_id,
                },
                sort_keys=True,
            )
        ):
            raise ValueError("strict fake response digest mismatch")


# ── Production subprocess evaluator ──────────────────────────────────────

class LLMSubprocessAuthorityEvaluator:
    """Legacy non-production evaluator retained as a strict compatibility seam.

    S6a production wiring never constructs this class. Semantic evidence is
    supplied by the already-running manager in its authenticated completion.
    This bounded implementation remains for adversarial parser tests and old
    explicitly injected callers only. It performs one one-shot LLM call through the
    machine-local executor registry (shared-identity posture), followed by
    strict closed-schema output parsing.

    The invocation is bounded by ``timeout_seconds``; a hang, provider
    error, empty/malformed/extra-field/unknown-value output, credential
    marker, or any policy/team/version/digest/candidate/input mismatch fails
    closed to an EVALUATOR_ERROR-typed result (the hook then escalates).
    """
    model_id = f"executor/{DEFAULT_EXECUTOR_KIND}"
    model_version = "v1"
    model_digest = _sha256(f"{model_id}:{model_version}")

    def __init__(
        self,
        *,
        executor_kind: str = DEFAULT_EXECUTOR_KIND,
        timeout_seconds: float = DEFAULT_EVALUATOR_TIMEOUT_SECONDS,
        resolve_binary=None,
        invoke=None,
    ) -> None:
        self._executor_kind = executor_kind
        self._timeout_seconds = timeout_seconds
        # Injectable boundaries for deterministic tests: binary resolution
        # and subprocess invocation default to the real registry + subprocess.
        self._resolve_binary = resolve_binary or self._default_resolve_binary
        self._invoke = invoke or self._default_invoke
        self.model_id = f"executor/{executor_kind}"
        self.model_digest = _sha256(f"{self.model_id}:{self.model_version}")

    # -- boundaries -------------------------------------------------------

    @staticmethod
    def _default_resolve_binary(executor_kind: str) -> str | None:
        from runtime.orchestrator.executor_binary_registry import get_binary
        return get_binary(executor_kind)

    @staticmethod
    def _default_invoke(argv: list[str], prompt: str, timeout_seconds: float):
        return subprocess.run(
            argv,
            input=prompt,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )

    # -- build + invoke ---------------------------------------------------

    def build_argv(self, prompt: str) -> list[str]:
        binary = self._resolve_binary(self._executor_kind)
        if not binary:
            raise RuntimeError(
                f"no registered executor binary for kind {self._executor_kind!r}"
            )
        # One-shot, stdin-fed, JSONL event stream — the same accepted
        # uncontained posture as the headless PiAdapter (THR-056 §3).
        return [binary, "-p", "--mode", "json", prompt]

    def _extract_final_text(self, stdout: str) -> str:
        """Concatenate the assistant's text deltas from pi's JSONL event
        stream (message_update events). Empty output yields empty text."""
        parts: list[str] = []
        for line in stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(event, dict) or event.get("type") != "message_update":
                continue
            ame = event.get("assistantMessageEvent")
            if not isinstance(ame, dict) or ame.get("type") != "text_delta":
                continue
            delta = ame.get("delta")
            if isinstance(delta, str) and delta:
                parts.append(delta)
        return "".join(parts)

    @staticmethod
    def _extract_json_object(text: str) -> dict | None:
        """Extract exactly one JSON object from the model text. More than one
        object, or non-JSON text, returns None (malformed)."""
        text = text.strip()
        if not text.startswith("{"):
            # Tolerate a code fence only when it wraps exactly one object.
            if "```" in text:
                start = text.find("{")
                end = text.rfind("}")
                if start != -1 and end != -1 and start < end:
                    text = text[start:end + 1]
            else:
                return None
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            return None
        if not isinstance(data, dict):
            return None
        return data

    # -- AuthorityEvaluator contract --------------------------------------

    def evaluate(
        self, snapshot: AuthorityInputSnapshot, *, policy: AuthorityPolicy | None = None
    ) -> AuthorityEvaluationResult:
        from runtime.models import AuthorityDispositionCode as _Code
        effective_policy = policy or POLICY_BY_TEAM[snapshot.team]
        prompt = build_authority_evaluation_prompt(
            policy=effective_policy,
            candidate_id=snapshot.candidate_id,
            team=snapshot.team,
            manager_agent=snapshot.manager_agent,
            manager_session_id=snapshot.manager_session_id,
            root_task_id=snapshot.root_task_id,
            causal_event_id=snapshot.causal_event_id,
            causal_event_digest=snapshot.causal_event_digest,
            reason=snapshot.reason,
            reason_digest=snapshot.reason_digest,
            input_digest=snapshot.digest(),
            structured_facts=snapshot.structured_facts,
        )
        try:
            argv = self.build_argv(prompt)
            proc = self._invoke(argv, prompt, self._timeout_seconds)
        except subprocess.TimeoutExpired as exc:
            return self._error_result(_Code.TIMEOUT, f"evaluator timeout: {exc}")
        except Exception as exc:
            return self._error_result(_Code.EVALUATOR_ERROR, f"evaluator launch failed: {exc}")
        if proc.returncode != 0:
            return self._error_result(
                _Code.EVALUATOR_ERROR,
                f"evaluator exited {proc.returncode}: {(proc.stderr or '')[-500:]}",
            )
        text = self._extract_final_text(proc.stdout or "")
        if not text.strip():
            return self._error_result(_Code.MALFORMED_OUTPUT, "evaluator produced no text output")
        data = self._extract_json_object(text)
        if data is None:
            return self._error_result(_Code.MALFORMED_OUTPUT, "evaluator output is not a single JSON object")
        response_digest = _sha256(text)
        try:
            parsed = AuthorityEvaluatorOutput.model_validate(data)
        except Exception as exc:
            return self._error_result(
                _Code.MALFORMED_OUTPUT, f"evaluator output failed closed-schema validation: {exc}"
            )
        # Echo-contract: the output must name the exact attempt inputs.
        if (
            parsed.policy_id != snapshot.policy_id
            or parsed.policy_version != snapshot.policy_version
            or parsed.policy_digest != snapshot.policy_digest
            or parsed.team != snapshot.team
            or parsed.candidate_id != snapshot.candidate_id
            or parsed.input_digest != snapshot.digest()
        ):
            return self._error_result(
                _Code.MALFORMED_OUTPUT, "evaluator output policy/team/candidate/input mismatch"
            )
        disposition = AuthorityDisposition(parsed.disposition)
        clause = effective_policy.clause_by_id(parsed.clause_id) if parsed.clause_id else None
        expected_action = (
            ACTION_CONTINUE_SAME_ROOT
            if disposition == AuthorityDisposition.CONTINUE_SAME_ROOT
            else ACTION_ESCALATE_TO_FOUNDER
        )
        if parsed.clause_id is None:
            valid_clause_action = (
                disposition == AuthorityDisposition.ESCALATE and parsed.action is None
            )
        else:
            valid_clause_action = (
                clause is not None
                and clause.action == expected_action
                and parsed.action == clause.action
            )
        if not valid_clause_action:
            return self._error_result(
                _Code.MALFORMED_OUTPUT,
                "evaluator output names an unknown or mismatched policy clause/action",
            )
        # Scan only model-controlled free text after all trusted echo and
        # closed-vocabulary validation succeeds.  In particular, clause ids
        # such as ``esc-auth-credentials-security`` are policy vocabulary,
        # not credential-bearing prose. Rationale is digest-only in this
        # schema; evidence refs are its sole free-text output field.
        free_text = "\n".join(parsed.evidence_refs).lower()
        if any(marker in free_text for marker in _CREDENTIAL_MARKERS):
            return self._error_result(
                _Code.INJECTION_GUARD, "evaluator output carried a credential-like marker"
            )
        code = (
            AuthorityDispositionCode.CONTINUE_SAME_ROOT
            if disposition == AuthorityDisposition.CONTINUE_SAME_ROOT
            else AuthorityDispositionCode.ESCALATE
        )
        if parsed.confidence < CONTINUE_MIN_CONFIDENCE:
            code = AuthorityDispositionCode.LOW_CONFIDENCE
        return AuthorityEvaluationResult(
            disposition=disposition,
            disposition_code=code,
            response_digest=response_digest,
            clause_id=parsed.clause_id,
            action=parsed.action,
            confidence=parsed.confidence,
            uncertainty_codes=tuple(parsed.uncertainty_codes),
            evidence_refs=tuple(parsed.evidence_refs),
            rationale_digest=parsed.rationale_digest,
        )

    @staticmethod
    def _error_result(code: AuthorityDispositionCode, error: str) -> AuthorityEvaluationResult:
        return AuthorityEvaluationResult(
            disposition=AuthorityDisposition.EVALUATOR_ERROR,
            disposition_code=code,
            response_digest=_sha256(f"evaluator-error:{code.value}:{error}"),
            error=error,
        )


# ── Hook ─────────────────────────────────────────────────────────────────

@dataclass
class _NormalizedVerdict:
    disposition: AuthorityDisposition
    disposition_code: AuthorityDispositionCode
    clause_id: str | None
    action: str | None
    confidence: float
    uncertainty_codes: tuple[str, ...]
    evidence_refs: tuple[str, ...]
    rationale_digest: str | None
    response_digest: str
    error: str | None = None


def _normalize_result(
    policy: AuthorityPolicy,
    candidate_id: str,
    input_digest: str,
    result: AuthorityEvaluationResult,
) -> _NormalizedVerdict:
    """Re-validate the evaluator result against the policy and snapshot.

    This is the final authority gate shared by the fake and the production
    evaluator: a CONTINUE_SAME_ROOT verdict survives ONLY when the named
    clause exists, is a continue clause, names the exact permitted action,
    is unambiguous (matching code), and is confident enough. Everything else
    fails closed to ESCALATE.
    """
    if result.disposition == AuthorityDisposition.CONTINUE_SAME_ROOT:
        if result.confidence < CONTINUE_MIN_CONFIDENCE:
            return _NormalizedVerdict(
                AuthorityDisposition.ESCALATE, AuthorityDispositionCode.LOW_CONFIDENCE,
                None, None, result.confidence, result.uncertainty_codes,
                result.evidence_refs, result.rationale_digest, result.response_digest,
                error="continue below confidence threshold",
            )
        if result.disposition_code != AuthorityDispositionCode.CONTINUE_SAME_ROOT:
            code = (
                result.disposition_code
                if result.disposition_code
                in _DIAGNOSTIC_FAILURE_CODES
                else AuthorityDispositionCode.MALFORMED_OUTPUT
            )
            return _NormalizedVerdict(
                AuthorityDisposition.ESCALATE, code,
                None, None, result.confidence, result.uncertainty_codes,
                result.evidence_refs, result.rationale_digest, result.response_digest,
                error="continue with non-continue disposition code",
            )
        clause = policy.clause_by_id(result.clause_id) if result.clause_id else None
        if clause is None or clause.action != ACTION_CONTINUE_SAME_ROOT:
            return _NormalizedVerdict(
                AuthorityDisposition.ESCALATE, AuthorityDispositionCode.MALFORMED_OUTPUT,
                None, None, result.confidence, result.uncertainty_codes,
                result.evidence_refs, result.rationale_digest, result.response_digest,
                error="continue names an unknown or non-continue policy clause",
            )
        if result.action != ACTION_CONTINUE_SAME_ROOT or result.action != clause.action:
            return _NormalizedVerdict(
                AuthorityDisposition.ESCALATE, AuthorityDispositionCode.MALFORMED_OUTPUT,
                None, None, result.confidence, result.uncertainty_codes,
                result.evidence_refs, result.rationale_digest, result.response_digest,
                error="continue names an action other than continue_same_root",
            )
        return _NormalizedVerdict(
            AuthorityDisposition.CONTINUE_SAME_ROOT, AuthorityDispositionCode.CONTINUE_SAME_ROOT,
            result.clause_id, result.action, result.confidence,
            result.uncertainty_codes, result.evidence_refs,
            result.rationale_digest, result.response_digest,
        )
    if result.disposition == AuthorityDisposition.ESCALATE:
        # Carry the matched must-escalate clause when the result names a real
        # must-escalate clause of the policy (audit completeness); anything
        # else keeps clause_id None (fail-closed escalate).
        clause = policy.clause_by_id(result.clause_id) if result.clause_id else None
        matched = (
            clause.id if clause is not None and clause.action == ACTION_ESCALATE_TO_FOUNDER
            else None
        )
        code = (
            result.disposition_code
            if result.disposition_code in _DIAGNOSTIC_FAILURE_CODES
            else AuthorityDispositionCode.ESCALATE
        )
        return _NormalizedVerdict(
            AuthorityDisposition.ESCALATE,
            code,
            matched, None, result.confidence, result.uncertainty_codes,
            result.evidence_refs, result.rationale_digest, result.response_digest,
        )
    # EVALUATOR_ERROR / NOT_APPLICABLE / anything unknown: fail closed.
    code = (
        result.disposition_code
        if result.disposition_code in _DIAGNOSTIC_FAILURE_CODES
        else AuthorityDispositionCode.EVALUATOR_ERROR
    )
    return _NormalizedVerdict(
        AuthorityDisposition.ESCALATE,
        code,
        None, None, result.confidence, result.uncertainty_codes,
        result.evidence_refs, result.rationale_digest, result.response_digest,
        error=result.error,
    )


def _record_hook_outcome(
    db,
    *,
    task_id: str,
    agent: str,
    outcome: str,
    policy: AuthorityPolicy | None = None,
    candidate_id: str | None = None,
    snapshot: AuthorityInputSnapshot | None = None,
    verdict: _NormalizedVerdict | None = None,
    fences: dict[str, AuthorityFenceResult] | None = None,
    lifecycle: str | None = None,
    error: str | None = None,
) -> int | None:
    """Best-effort append of exactly one `authority_hook` outcome row.

    Never raises: if the audit row cannot be written, the caller still fails
    closed (never continues); the escalation audit row and the candidate
    lifecycle remain the capture-failure evidence.
    """
    payload: dict = {"outcome": outcome}
    if policy is not None:
        payload["policy_id"] = policy.id
        payload["policy_version"] = policy.version
        payload["policy_digest"] = policy.digest
    if candidate_id is not None:
        payload["candidate_id"] = candidate_id
    if snapshot is not None:
        payload["input_digest"] = snapshot.digest()
        payload["reason_digest"] = snapshot.reason_digest
        payload["causal_event_id"] = snapshot.causal_event_id
        payload["causal_event_digest"] = snapshot.causal_event_digest
        payload["causal_result_id"] = snapshot.causal_result_id
        payload["prompt_id"] = snapshot.prompt_id
        payload["prompt_version"] = snapshot.prompt_version
        payload["prompt_digest"] = snapshot.prompt_digest
        payload["model_id"] = snapshot.model_id
        payload["model_version"] = snapshot.model_version
        payload["model_digest"] = snapshot.model_digest
    if verdict is not None:
        payload["disposition"] = verdict.disposition.value
        payload["disposition_code"] = verdict.disposition_code.value
        payload["clause_id"] = verdict.clause_id
        payload["action"] = verdict.action
        payload["confidence"] = verdict.confidence
        payload["uncertainty_codes"] = list(verdict.uncertainty_codes)
        payload["evidence_refs"] = list(verdict.evidence_refs)
        payload["rationale_digest"] = verdict.rationale_digest
        payload["response_digest"] = verdict.response_digest
    if fences:
        payload["fence_results"] = {
            name: (result.model_dump(mode="json") if isinstance(result, AuthorityFenceResult) else result)
            for name, result in fences.items()
        }
    if lifecycle is not None:
        payload["lifecycle"] = lifecycle
    if error is not None:
        payload["error"] = error[:500]
    try:
        return db.insert_audit_log(
            task_id=task_id, agent=agent,
            action=AUDIT_ACTION_HOOK_OUTCOME, payload=payload,
        )
    except Exception as exc:  # pragma: no cover - audit failure path
        logger.warning("authority hook outcome row failed for %s: %s", task_id, exc)
        return None


def _current_budget_ceilings(orch: "Orchestrator") -> int:
    """The current release-controlled revise-round ceiling. ``<= 0`` means
    the revise budget is
    disabled (unlimited). Re-read at every call site so the consumption-time
    recheck uses the freshest ceilings, not the evaluation-time ones."""
    org_cap = 0
    try:
        from runtime.orchestrator.org_config import load_org_config
        org_cap = load_org_config(orch._paths).max_revise_rounds
    except Exception:
        org_cap = 0
    return org_cap


def authority_policy_v2_claim_eligibility(orch: "Orchestrator") -> dict[str, int]:
    """Narrow server-owned mechanical-eligibility input for a v2 claim.

    Returns only the org-config revise-round ceiling that the claim
    transaction must enforce against the actual persisted
    ``tasks.revision_count``; the Database re-reads every task/owner/session
    fact itself and accepts no caller-supplied boolean.  This deliberately
    does NOT consult ``_server_fact_clause`` adverse-review, partial-work or
    raw-DDL must-escalate clauses: those remain v1 escalation diagnostics and
    are never a v2 claim veto, and no phrase or clause unlock is introduced.
    """
    return {"max_revise_rounds": _current_budget_ceilings(orch)}


def _server_evidence(
    orch: "Orchestrator",
    current: "TaskRecord",
    agent: str,
    policy: "AuthorityPolicy",
    fences: dict[str, AuthorityFenceResult],
) -> tuple[dict[str, str], list[str]]:
    """Collect authoritative server/runtime facts for the evaluation snapshot.

    Every fact is a JSON-encoded ``{"value": ..., "source": ...}`` object with
    an immutable provenance digest where applicable — the reason prose can
    never establish or alter a server fact. Returns ``(facts, matched)`` where
    ``matched`` lists the policy must-escalate clause ids the server state
    PROVES (the hook forces ESCALATE for those regardless of the evaluator):
    an adverse child review verdict (esc-adverse-review-qa), partial-work/
    zombie evidence (esc-partial-work), and DB-schema drift vs the
    release-pinned schema (esc-schema-overloaded-column).
    """
    db = orch._db
    facts: dict[str, str] = {}
    matched: list[str] = []

    # Mechanical fence outcomes (same objects recorded on the candidate).
    facts["fence_results"] = json.dumps(
        {
            name: fr.model_dump(mode="json")
            for name, fr in sorted(fences.items())
        },
        sort_keys=True,
    )

    # Budget counters + current ceilings.
    org_cap = _current_budget_ceilings(orch)
    budget_exhausted = org_cap > 0 and current.revision_count >= org_cap
    facts["budget"] = json.dumps(
        {
            "value": {
                "orchestration_step_count": current.orchestration_step_count,
                "revision_count": current.revision_count,
                "max_revise_rounds": org_cap,
                "exhausted": budget_exhausted,
            },
            "source": "tasks/settings/org_config",
        },
        sort_keys=True,
    )

    # Lineage: revisit / parent / successor / thread / fresh-root.
    is_successor = _is_successor_root(db, current.id)
    is_fresh_root = (
        current.parent_task_id is None
        and current.revisit_of_task_id is None
        and not is_successor
    )
    facts["lineage"] = json.dumps(
        {
            "value": {
                "revisit_of_task_id": current.revisit_of_task_id or "",
                "parent_task_id": current.parent_task_id or "",
                "is_successor": is_successor,
                "thread_origin": bool(current.dispatched_from_thread_id),
                "is_fresh_root": is_fresh_root,
            },
            "source": "tasks/manager_supersessions",
        },
        sort_keys=True,
    )

    # Active work / block / cancellation / session state.
    facts["active_work"] = json.dumps(
        {
            "value": {
                "active_chain": current.active_chain or "",
                "active_fanout": current.active_fanout or "",
                "blocked_on_job_ids": current.blocked_on_job_ids or "",
            },
            "source": "tasks",
        },
        sort_keys=True,
    )
    facts["cancellation"] = json.dumps(
        {
            "value": {
                "cancelled": current.cancelled_at is not None,
                "status": current.status.value,
            },
            "source": "tasks",
        },
        sort_keys=True,
    )
    facts["block_state"] = json.dumps(
        {
            "value": {"block_kind": current.block_kind.value if current.block_kind else None},
            "source": "tasks",
        },
        sort_keys=True,
    )
    facts["session"] = json.dumps(
        {
            "value": {
                "current_session_id": current.current_session_id or "",
                "manager_agent": current.assigned_agent or agent,
            },
            "source": "tasks",
        },
        sort_keys=True,
    )

    # Adverse review/QA: a child whose LATEST persisted verdict is a
    # non-approve verdict is an authoritative must-escalate fact.
    adverse: list[dict[str, str]] = []
    for child_id in db.get_children(current.id):
        latest = db.execute(
            "SELECT verdict FROM task_results WHERE task_id = ? "
            "ORDER BY id DESC LIMIT 1",
            (child_id,),
        ).fetchone()
        verdict = latest["verdict"] if latest is not None else None
        if verdict and verdict not in _APPROVED_VERDICTS:
            adverse.append({"task_id": child_id, "verdict": str(verdict)})
    has_adverse = bool(adverse)
    facts["adverse_review"] = json.dumps(
        {
            "value": has_adverse,
            "children": adverse[:5],
            "source": "task_results",
        },
        sort_keys=True,
    )
    if has_adverse:
        matched.append("esc-adverse-review-qa")

    # Partial-work evidence: the daemon flagged this task's session as dead
    # mid-turn (zombie reaper) — the authoritative partial-work signal.
    zombie = current.zombie_flagged_at is not None
    facts["partial_work"] = json.dumps(
        {
            "value": zombie,
            "source": "tasks.zombie_flagged_at",
        },
        sort_keys=True,
    )
    if zombie:
        matched.append("esc-partial-work")

    # Protected-boundary digests: the org permission/allow-rule surface and
    # the current DB schema, so the evaluator sees authoritative server state.
    # The permission digest is provenance (no release baseline exists for the
    # org-local surface); the permission boundary is enforced by the
    # closed-pattern CONTINUE gate + a during-attempt change recheck (step 8b)
    # + the action-safety proof. The DB schema digest IS a gate: a live schema
    # that differs from the release-pinned schema (a fresh Database() built
    # from the current code) is an authoritative schema/migration drift signal
    # that forces esc-schema-overloaded-column regardless of reason prose.
    facts["org_permission"] = json.dumps(
        {
            "digest": _permission_digest(orch, current.assigned_agent or agent),
            "source": "org_config/agent_def",
        },
        sort_keys=True,
    )
    live_schema_digest = _live_schema_digest(db)
    schema_drift = live_schema_digest != _release_schema_digest()
    facts["db_schema"] = json.dumps(
        {
            "digest": live_schema_digest,
            "drift": schema_drift,
            "source": "sqlite_master",
        },
        sort_keys=True,
    )
    if schema_drift:
        matched.append("esc-schema-overloaded-column")

    # The release-controlled policy binding (immutable identity).
    facts["team_policy"] = json.dumps(
        {
            "policy_id": policy.id,
            "policy_version": policy.version,
            "policy_digest": policy.digest,
            "source": "authority_policy.py",
        },
        sort_keys=True,
    )
    return facts, matched


def _eligible_fences(
    orch: "Orchestrator",
    current: "TaskRecord",
    agent: str,
) -> dict[str, AuthorityFenceResult]:
    """Server-owned mechanical fences. Every failure makes the root
    ineligible (the hook records the outcome and the escalation proceeds
    through the existing path). No policy output can override these."""
    fences: dict[str, AuthorityFenceResult] = {}
    try:
        manager = orch.teams.manager_for_team(current.team).name
    except (KeyError, ValueError):
        manager = None
    fences["manager_ownership"] = AuthorityFenceResult(
        passed=bool(
            manager is not None
            and agent == manager
            and current.assigned_agent == agent
        )
    )
    fences["current_session"] = AuthorityFenceResult(
        passed=current.current_session_id is not None
    )
    fences["cancellation"] = AuthorityFenceResult(
        passed=current.cancelled_at is None
        and current.status.value not in _TERMINAL_STATUSES
    )
    fences["claimed_root"] = AuthorityFenceResult(
        passed=current.status == TaskStatus.IN_PROGRESS
        and current.block_kind is None
    )
    fences["revisit_lineage"] = AuthorityFenceResult(
        passed=current.revisit_of_task_id is None
    )
    fences["successor_lineage"] = AuthorityFenceResult(
        passed=not _is_successor_root(orch._db, current.id)
    )
    fences["active_work"] = AuthorityFenceResult(
        passed=current.active_chain is None
        and current.active_fanout is None
        and current.blocked_on_job_ids is None
    )
    org_cap = _current_budget_ceilings(orch)
    budget_ok = org_cap <= 0 or current.revision_count < org_cap
    fences["budget_exhausted"] = AuthorityFenceResult(
        passed=budget_ok,
        code="budget_exceeded" if not budget_ok else None,
    )
    return fences


def _is_successor_root(db, task_id: str) -> bool:
    try:
        row = db.execute(
            "SELECT 1 FROM manager_supersessions WHERE successor_task_id = ? LIMIT 1",
            (task_id,),
        ).fetchone()
    except Exception:
        return True  # fail closed on any read defect
    return row is not None


def run_authority_hook(
    orch: "Orchestrator",
    task: "TaskRecord",
    agent: str,
    reason: str,
    result_row_id: int | None,
    manager_self_evaluation: dict | None = None,
) -> str:
    """The pre-escalation authority hook. Returns ``"continue_same_root"``
    (the named same-root permitted action was executed and audited) or
    ``"escalate"`` (fail closed — the caller proceeds through the exact
    existing escalation path). Never raises into the caller.

    ``result_row_id`` is the immutable ``task_results`` row id whose
    CompletionReport produced this escalate decision — the causal event
    identity. It is stable across restart/recovery re-entry (the boot sweep
    and zombie reaper rebuild the report from the SAME row), so a replayed
    attempt cannot mint a second candidate or evaluation. When it is absent
    no immutable causality exists: the hook fails closed with a
    ``capture_failure`` outcome and never creates a candidate.
    """
    policy = POLICY_BY_TEAM.get(task.team)
    if policy is None:
        # No release-controlled policy for this team: hook not applicable.
        return "escalate"

    db = orch._db
    active_activation = None
    active_release = None
    try:
        from runtime.orchestrator.active_authority_policy import (
            load_session_policy_binding, load_session_policy_snapshot, policy_from_release,
            SELF_EVALUATION_CONTRACT_DIGEST, SELF_EVALUATION_CONTRACT_ID,
            SELF_EVALUATION_CONTRACT_VERSION,
        )
        from runtime.orchestrator.authority_policy_store import AuthorityPolicyStore
        policy_store = AuthorityPolicyStore(db)
        launch_snapshot = load_session_policy_snapshot(
            db=db, store=policy_store, task_id=task.id,
            session_id=task.current_session_id or "", agent_name=agent,
        )
        if launch_snapshot is not None:
            active_activation = launch_snapshot.activation
            active_release = launch_snapshot.release
            policy = policy_from_release(active_release)
        session_binding = load_session_policy_binding(
            db=db, task_id=task.id, session_id=task.current_session_id or "",
            agent_name=agent,
        )
    except Exception as exc:
        _record_hook_outcome(
            db, task_id=task.id, agent=agent, outcome=OUTCOME_CAPTURE_FAILURE,
            policy=policy, error=f"active policy resolution failed: {exc}",
        )
        return "escalate"
    current = db.get_task(task.id)
    if current is None:
        return "escalate"

    # ---- 1. Eligibility + mechanical fences (recorded, fail closed) ----
    fences = _eligible_fences(orch, current, agent)
    failed = [name for name, fr in fences.items() if not fr.passed]
    if failed:
        _record_hook_outcome(
            db, task_id=task.id, agent=agent, outcome=OUTCOME_INELIGIBLE,
            policy=policy, fences=fences,
            error="fences not passed: " + ", ".join(sorted(failed)),
        )
        return "escalate"

    # ---- 1b. Immutable causal identity REQUIRED. The candidate claim tuple
    # is derived from the persisted task-result row (stable across restart),
    # NEVER from a freshly written orchestration-step audit id (which a
    # restart re-entry would re-mint and turn into a second candidate). ----
    if result_row_id is None:
        _record_hook_outcome(
            db, task_id=task.id, agent=agent, outcome=OUTCOME_CAPTURE_FAILURE,
            policy=policy, fences=fences,
            error="no immutable task-result row id for causal identity",
        )
        return "escalate"

    # ---- 2. Immutable input snapshot (candidate id is the deterministic
    # claim derivation, so it is known before the CAS INSERT). Structured
    # SERVER facts carry authoritative provenance; the reason prose can never
    # establish or alter them. ----
    reason_digest = _sha256(reason or "")
    causal_event_id = f"result:{result_row_id}"
    causal_event_digest = _sha256(f"task-result:{result_row_id}")
    evaluator = orch._authority_evaluator
    manager_session_id = current.current_session_id or ""
    if active_activation is not None:
        provider_id = str((session_binding or {}).get("provider_id", "unknown"))
        executor_kind = str((session_binding or {}).get("executor_kind", "unknown"))
        effective_model_id = str((session_binding or {}).get("model_id", "default"))
        model_id = f"manager/{provider_id}/{executor_kind}/{effective_model_id}"
        model_version = SELF_EVALUATION_CONTRACT_VERSION
        model_digest = _sha256(
            f"{model_id}:{model_version}:{SELF_EVALUATION_CONTRACT_DIGEST}"
        )
    else:
        provider_id = getattr(evaluator, "provider_id", "local") if evaluator else "none"
        executor_kind = getattr(evaluator, "_executor_kind", "none") if evaluator else "none"
        model_id = evaluator.model_id if evaluator is not None else "none"
        model_version = evaluator.model_version if evaluator is not None else "v1"
        model_digest = evaluator.model_digest if evaluator is not None else _sha256("none:v1")
    structured_facts, server_clauses = _server_evidence(
        orch, current, agent, policy, fences,
    )
    candidate_id = f"AUTH-CAND-{_authority_claim_key(
        current.id,
        manager_session_id,
        causal_event_id,
        policy.digest,
        PROMPT_DIGEST,
        model_digest,
    )}"
    snapshot = AuthorityInputSnapshot(
        root_task_id=current.id,
        team=current.team,
        manager_agent=current.assigned_agent or agent,
        manager_session_id=manager_session_id,
        candidate_id=candidate_id,
        causal_event_id=causal_event_id,
        causal_event_digest=causal_event_digest,
        causal_result_id=None,
        reason=reason or "",
        reason_digest=reason_digest,
        policy_id=policy.id,
        policy_version=policy.version,
        policy_digest=policy.digest,
        prompt_id=PROMPT_ID,
        prompt_version=PROMPT_VERSION,
        prompt_digest=PROMPT_DIGEST,
        model_id=model_id,
        model_version=model_version,
        model_digest=model_digest,
        structured_facts=structured_facts,
    )
    input_digest = snapshot.digest()

    # ---- 3. Claim the candidate (deterministic CAS) ----
    try:
        candidate_kwargs = dict(
            root_task_id=current.id,
            team=current.team,
            manager_agent=current.assigned_agent or agent,
            manager_session_id=manager_session_id,
            causal_event_id=causal_event_id,
            causal_event_digest=causal_event_digest,
            causal_result_id=None,
            policy_id=policy.id,
            policy_version=policy.version,
            policy_digest=policy.digest,
            prompt_id=PROMPT_ID,
            prompt_version=PROMPT_VERSION,
            prompt_digest=PROMPT_DIGEST,
            model_id=model_id,
            model_version=model_version,
            model_digest=model_digest,
            snapshot_digest=input_digest,
            fence_results={name: fr.model_dump(mode="json") for name, fr in fences.items()},
        )
        if active_activation is not None and active_release is not None:
            candidate, _pin = policy_store.claim_candidate_with_pin(
                release_id=active_release.id,
                activation_id=active_activation.id,
                activation_epoch=active_activation.epoch,
                provider_id=provider_id,
                executor_kind=executor_kind,
                **candidate_kwargs,
            )
            candidate_id, won = candidate.id, True
        else:
            candidate_id, won = db.claim_authority_candidate(**candidate_kwargs)
    except Exception as exc:
        _record_hook_outcome(
            db, task_id=task.id, agent=agent, outcome=OUTCOME_CAPTURE_FAILURE,
            policy=policy, snapshot=snapshot, fences=fences,
            error=f"candidate claim failed: {exc}",
        )
        return "escalate"

    def _authenticate_persisted_candidate_policy() -> None:
        if active_release is None or active_activation is None:
            return
        pin = policy_store.get_candidate_pin(candidate_id)
        if pin is None:
            raise ValueError("DB-backed authority candidate is missing its policy pin")
        expected_provider = provider_id
        expected_kind = executor_kind
        if (pin.release_id != active_release.id
                or pin.activation_id != active_activation.id
                or pin.activation_epoch != active_activation.epoch
                or pin.provider_id != expected_provider
                or pin.executor_kind != expected_kind):
            raise ValueError("authority candidate policy pin identity mismatch")
        reread_release = policy_store.get_release(pin.release_id)
        if reread_release is None or policy_from_release(reread_release) != policy:
            raise ValueError("authority candidate release snapshot mismatch")

    try:
        _authenticate_persisted_candidate_policy()
    except Exception as exc:
        _record_hook_outcome(
            db, task_id=task.id, agent=agent, outcome=OUTCOME_CAPTURE_FAILURE,
            policy=policy, snapshot=snapshot, candidate_id=candidate_id,
            fences=fences, lifecycle="created", error=f"candidate pin authentication failed: {exc}",
        )
        return "escalate"

    if not won:
        # CAS loser: the exact deterministic tuple was already claimed. Never
        # evaluate again, never continue — fail closed.
        try:
            db.record_authority_audit(
                candidate_id=candidate_id, event_type="candidate_claim_lost",
                payload={"digest": input_digest, "version": policy.version},
            )
        except Exception:
            pass
        _record_hook_outcome(
            db, task_id=task.id, agent=agent, outcome=OUTCOME_CAS_LOST,
            policy=policy, snapshot=snapshot, candidate_id=candidate_id,
            fences=fences, lifecycle="created",
        )
        return "escalate"

    # ---- 4. Claimed audit event ----
    try:
        db.record_authority_audit(
            candidate_id=candidate_id, event_type="candidate_claimed",
            payload={"digest": input_digest, "version": policy.version},
        )
    except Exception as exc:
        _record_hook_outcome(
            db, task_id=task.id, agent=agent, outcome=OUTCOME_AUDIT_FAILURE,
            policy=policy, snapshot=snapshot, candidate_id=candidate_id,
            fences=fences, lifecycle="created", error=f"claimed audit event failed: {exc}",
        )
        return "escalate"

    # ---- 5. Cancellation/staleness re-check AFTER claim ----
    current = db.get_task(task.id)
    if _is_terminal_or_cancelled(current):
        _record_hook_outcome(
            db, task_id=task.id, agent=agent, outcome=OUTCOME_CANCELLED_STALE,
            policy=policy, snapshot=snapshot, candidate_id=candidate_id,
            fences=fences, lifecycle="created",
            error="task cancelled/terminal between claim and evaluation",
        )
        return "escalate"

    # ---- 6. Evaluate (bounded by the seam; evaluator failures are typed) ----
    if active_activation is not None:
        try:
            if manager_self_evaluation is None:
                raise ValueError("manager self-evaluation is missing")
            if manager_self_evaluation.get("_error_code"):
                raise ValueError("manager self-evaluation is malformed")
            supplied = ManagerSelfEvaluation.model_validate(manager_self_evaluation)
            expected_identity = {
                "contract_id": SELF_EVALUATION_CONTRACT_ID,
                "contract_version": SELF_EVALUATION_CONTRACT_VERSION,
                "contract_digest": SELF_EVALUATION_CONTRACT_DIGEST,
                "root_task_id": current.id,
                "manager_session_id": manager_session_id,
                "release_id": active_release.id,
                "policy_version": str(active_release.version),
                "policy_digest": active_release.policy_digest,
                "activation_id": active_activation.id,
                "activation_epoch": active_activation.epoch,
                "provider_id": provider_id,
                "executor_kind": executor_kind,
                "model_id": effective_model_id,
            }
            actual = supplied.model_dump(mode="json")
            mismatched = [key for key, value in expected_identity.items()
                          if actual.get(key) != value]
            if mismatched:
                raise ValueError(
                    "manager self-evaluation binding mismatch: "
                    + ", ".join(sorted(mismatched))
                )
            disposition = AuthorityDisposition(supplied.disposition)
            result = AuthorityEvaluationResult(
                disposition=disposition,
                disposition_code=(
                    AuthorityDispositionCode.CONTINUE_SAME_ROOT
                    if disposition == AuthorityDisposition.CONTINUE_SAME_ROOT
                    else AuthorityDispositionCode.ESCALATE
                ),
                response_digest=_sha256(json.dumps(actual, sort_keys=True)),
                clause_id=supplied.clause_id,
                action=supplied.action,
                confidence=supplied.confidence,
                uncertainty_codes=tuple(supplied.uncertainty_codes),
            )
        except Exception as exc:
            result = AuthorityEvaluationResult(
                disposition=AuthorityDisposition.EVALUATOR_ERROR,
                disposition_code=AuthorityDispositionCode.MALFORMED_OUTPUT,
                response_digest=_sha256(f"manager-self-evaluation-error:{exc}"),
                error=str(exc),
            )
    elif evaluator is None:
        result = AuthorityEvaluationResult(
            disposition=AuthorityDisposition.EVALUATOR_ERROR,
            disposition_code=AuthorityDispositionCode.EVALUATOR_ERROR,
            response_digest=_sha256("no-evaluator-configured"),
            error="no authority evaluator configured",
        )
    else:
        try:
            result = (
                evaluator.evaluate(snapshot, policy=policy)
                if active_activation is not None
                else evaluator.evaluate(snapshot)
            )
        except Exception as exc:
            result = AuthorityEvaluationResult(
                disposition=AuthorityDisposition.EVALUATOR_ERROR,
                disposition_code=AuthorityDispositionCode.EVALUATOR_ERROR,
                response_digest=_sha256(f"evaluator-raised:{exc}"),
                error=f"evaluator raised: {exc}",
            )
    verdict = _normalize_result(policy, candidate_id, input_digest, result)

    try:
        _authenticate_persisted_candidate_policy()
    except Exception as exc:
        _record_hook_outcome(
            db, task_id=task.id, agent=agent, outcome=OUTCOME_CAPTURE_FAILURE,
            policy=policy, snapshot=snapshot, candidate_id=candidate_id,
            fences=fences, lifecycle="created", verdict=verdict,
            error=f"pre-evaluation-commit pin authentication failed: {exc}",
        )
        return "escalate"

    # ---- 6b. SERVER-DERIVED must-escalate gate. A server-PROVEN fact
    # (adverse child review verdict, partial-work/zombie evidence, DB-schema
    # drift vs the release-pinned schema) forces ESCALATE regardless of the
    # evaluator verdict — neither misleading nor omitted reason prose can
    # authorize CONTINUE_SAME_ROOT. Additionally, the closed-pattern gate:
    # a CONTINUE_SAME_ROOT verdict is honored ONLY when the proposed reason
    # is a byte-exact member of the release-controlled routine phrase set
    # (the server then has complete knowledge of the prose). Any other prose
    # — a paraphrase that omits, misstates, or hides a protected boundary —
    # is not verifiable as routine and fails closed to ESCALATE. Neither the
    # closed pattern nor reason truthfulness is safety proof on its own: the
    # single-use continuation ENVELOPE (minted atomically with the commit and
    # enforced at the daemon-side decision acceptance point) is the
    # mechanical fence that restricts the continued turn.
    server_clause = _server_fact_clause(snapshot.structured_facts)
    if server_clause is not None:
        diagnostic_code = (
            verdict.disposition_code
            if verdict.disposition_code in _DIAGNOSTIC_FAILURE_CODES
            else AuthorityDispositionCode.ESCALATE
        )
        diagnostic_error = verdict.error
        server_error = f"server-derived must-escalate fact: {server_clause}"
        verdict = _NormalizedVerdict(
            AuthorityDisposition.ESCALATE,
            diagnostic_code,
            server_clause,
            ACTION_ESCALATE_TO_FOUNDER,
            verdict.confidence,
            verdict.uncertainty_codes,
            verdict.evidence_refs,
            verdict.rationale_digest,
            verdict.response_digest,
            error=(
                f"{diagnostic_error}; {server_error}"
                if diagnostic_error else server_error
            ),
        )
        if server_clause not in server_clauses:
            server_clauses.append(server_clause)
    elif (
        verdict.disposition == AuthorityDisposition.CONTINUE_SAME_ROOT
        and not _is_accepted_routine_reason(snapshot.reason)
    ):
        verdict = _NormalizedVerdict(
            AuthorityDisposition.ESCALATE,
            AuthorityDispositionCode.ESCALATE,
            "esc-ambiguity-novelty",
            ACTION_ESCALATE_TO_FOUNDER,
            verdict.confidence,
            verdict.uncertainty_codes,
            verdict.evidence_refs,
            verdict.rationale_digest,
            verdict.response_digest,
            error="continue verdict for a non-accepted reason (not a byte-exact "
            "release-controlled routine phrase)",
        )
        if "esc-ambiguity-novelty" not in server_clauses:
            server_clauses.append("esc-ambiguity-novelty")

    # ---- 7. Record the single immutable evaluation (atomic created->evaluated) ----
    try:
        db.record_authority_evaluation(
            candidate_id=candidate_id,
            disposition=verdict.disposition.value,
            disposition_code=verdict.disposition_code.value,
            response_digest=verdict.response_digest,
            fence_results={name: fr.model_dump(mode="json") for name, fr in fences.items()},
        )
    except Exception as exc:
        _record_hook_outcome(
            db, task_id=task.id, agent=agent, outcome=OUTCOME_EVALUATOR_FAILURE,
            policy=policy, snapshot=snapshot, candidate_id=candidate_id,
            fences=fences, lifecycle="created", verdict=verdict,
            error=f"evaluation record failed: {exc}",
        )
        return "escalate"

    # ---- 8. EVALUATION_RECORDED audit event ----
    try:
        db.record_authority_audit(
            candidate_id=candidate_id, event_type="evaluation_recorded",
            payload={
                "disposition": verdict.disposition.value,
                "disposition_code": verdict.disposition_code.value,
                "digest": verdict.response_digest,
                "version": policy.version,
            },
        )
    except Exception as exc:
        _record_hook_outcome(
            db, task_id=task.id, agent=agent, outcome=OUTCOME_AUDIT_FAILURE,
            policy=policy, snapshot=snapshot, candidate_id=candidate_id,
            fences=fences, lifecycle="evaluated", verdict=verdict,
            error=f"evaluation_recorded audit event failed: {exc}",
        )
        return "escalate"

    # ---- 8b. FULL fence re-check AFTER evaluation. Any coupled eligibility
    # category that changed while the evaluator ran (cancellation, budget
    # exhaustion, block/active-work, lineage, manager/session/team, etc.)
    # closes the window: the evaluation is still recorded (auditable) but it
    # can never continue. The authoritative atomic re-validation happens at
    # the final continuation CAS (step 11); this is the fail-fast pre-check.
    current = db.get_task(task.id)
    fresh_fences = _eligible_fences(orch, current, agent)
    fresh_failed = [n for n, fr in fresh_fences.items() if not fr.passed]
    # Permission/schema surface drift during the attempt is an authoritative
    # server predicate: the org permission digest and the live DB schema
    # digest captured in the snapshot must still match at this point, else a
    # permission/sandbox/allow-rule or schema change landed mid-evaluation
    # and the attempt fails closed with the matched clause.
    drift_clause = _during_attempt_drift_clause(orch, agent, snapshot)
    if _is_terminal_or_cancelled(current) or fresh_failed or drift_clause is not None:
        try:
            db.consume_authority_candidate(candidate_id)
        except Exception:
            pass
        drift_verdict = verdict
        if drift_clause is not None:
            drift_verdict = _NormalizedVerdict(
                AuthorityDisposition.ESCALATE,
                AuthorityDispositionCode.ESCALATE,
                drift_clause,
                ACTION_ESCALATE_TO_FOUNDER,
                verdict.confidence,
                verdict.uncertainty_codes,
                verdict.evidence_refs,
                verdict.rationale_digest,
                verdict.response_digest,
                error=f"protected surface changed during evaluation: {drift_clause}",
            )
        _record_hook_outcome(
            db, task_id=task.id, agent=agent, outcome=OUTCOME_CANCELLED_STALE,
            policy=policy, snapshot=snapshot, candidate_id=candidate_id,
            fences=fences, lifecycle="evaluated", verdict=drift_verdict,
            error=(
                f"protected surface changed during evaluation: {drift_clause}"
                if drift_clause is not None
                else (
                    "fence changed during evaluation: "
                    + ", ".join(sorted(fresh_failed))
                    if fresh_failed else "task cancelled/terminal during evaluation"
                )
            ),
        )
        return "escalate"

    # ---- 9. Final CAS: consume exactly once (evaluated -> consumed) ----
    try:
        consumed = db.consume_authority_candidate(candidate_id)
    except Exception as exc:
        _record_hook_outcome(
            db, task_id=task.id, agent=agent, outcome=OUTCOME_AUDIT_FAILURE,
            policy=policy, snapshot=snapshot, candidate_id=candidate_id,
            fences=fences, lifecycle="evaluated", verdict=verdict,
            error=f"candidate consume failed: {exc}",
        )
        return "escalate"
    if not consumed:
        _record_hook_outcome(
            db, task_id=task.id, agent=agent, outcome=OUTCOME_CAS_LOST,
            policy=policy, snapshot=snapshot, candidate_id=candidate_id,
            fences=fences, lifecycle="evaluated", verdict=verdict,
            error="candidate not consumed (restart-incomplete or already consumed)",
        )
        return "escalate"

    # ---- 10. CANDIDATE_CONSUMED audit event ----
    try:
        db.record_authority_audit(
            candidate_id=candidate_id, event_type="candidate_consumed",
            payload={
                "disposition": verdict.disposition.value,
                "disposition_code": verdict.disposition_code.value,
            },
        )
    except Exception as exc:
        _record_hook_outcome(
            db, task_id=task.id, agent=agent, outcome=OUTCOME_AUDIT_FAILURE,
            policy=policy, snapshot=snapshot, candidate_id=candidate_id,
            fences=fences, lifecycle="consumed", verdict=verdict,
            error=f"candidate_consumed audit event failed: {exc}",
        )
        return "escalate"

    # ---- 11. Execute the verdict ----
    if verdict.disposition == AuthorityDisposition.CONTINUE_SAME_ROOT:
        try:
            _authenticate_persisted_candidate_policy()
        except Exception as exc:
            _record_hook_outcome(
                db, task_id=task.id, agent=agent, outcome=OUTCOME_CAPTURE_FAILURE,
                policy=policy, snapshot=snapshot, candidate_id=candidate_id,
                fences=fences, lifecycle="consumed", verdict=verdict,
                error=f"continuation pin authentication failed: {exc}",
            )
            return "escalate"
        clause = policy.clause_by_id(verdict.clause_id)
        note = (
            f"authority-policy continued same root: "
            f"clause {clause.id} ({clause.action})"
        )
        outcome_payload: dict = {
            "outcome": OUTCOME_CONTINUED_SAME_ROOT,
            "candidate_id": candidate_id,
            "input_digest": input_digest,
            "reason_digest": reason_digest,
            "policy_id": policy.id,
            "policy_version": policy.version,
            "policy_digest": policy.digest,
            "prompt_id": snapshot.prompt_id,
            "prompt_version": snapshot.prompt_version,
            "prompt_digest": snapshot.prompt_digest,
            "model_id": snapshot.model_id,
            "model_version": snapshot.model_version,
            "model_digest": snapshot.model_digest,
            "causal_event_id": causal_event_id,
            "causal_event_digest": causal_event_digest,
            "causal_result_id": None,
            "disposition": verdict.disposition.value,
            "disposition_code": verdict.disposition_code.value,
            "clause_id": verdict.clause_id,
            "action": verdict.action,
            "confidence": verdict.confidence,
            "uncertainty_codes": list(verdict.uncertainty_codes),
            "evidence_refs": list(verdict.evidence_refs),
            "rationale_digest": verdict.rationale_digest,
            "response_digest": verdict.response_digest,
            "fence_results": {
                name: fr.model_dump(mode="json") for name, fr in fences.items()
            },
            "lifecycle": "consumed",
        }
        continue_payload = {
            "candidate_id": candidate_id,
            "policy_id": policy.id,
            "policy_version": policy.version,
            "policy_digest": policy.digest,
            "clause_id": verdict.clause_id,
            "action": verdict.action,
            "disposition_code": verdict.disposition_code.value,
            "envelope_id": f"CONT-{candidate_id}",
        }
        # Fresh ceilings at consumption time (release-controlled; may have
        # changed while the evaluator ran) — the DB recheck compares the
        # CURRENT task counters against these. ``current`` was re-fetched at
        # step 8b; ``current.status``/``block_kind`` are the expected values
        # for the atomic CAS.
        fresh_revise_cap = _current_budget_ceilings(orch)
        try:
            committed = db.commit_authority_continue_same_root(
                task_id=task.id,
                candidate_id=candidate_id,
                expected_manager_agent=current.assigned_agent or agent,
                expected_session=current.current_session_id or "",
                expected_team=current.team,
                expected_policy_id=policy.id,
                expected_policy_version=policy.version,
                expected_policy_digest=policy.digest,
                expected_prompt_id=PROMPT_ID,
                expected_prompt_version=PROMPT_VERSION,
                expected_prompt_digest=PROMPT_DIGEST,
                expected_model_id=model_id,
                expected_model_version=model_version,
                expected_model_digest=model_digest,
                expected_input_digest=input_digest,
                expected_causal_event_id=causal_event_id,
                expected_max_revise_rounds=fresh_revise_cap,
                expected_status=current.status,
                expected_block_kind=current.block_kind,
                note=note,
                audit_agent=agent,
                authority_continue_payload=continue_payload,
                hook_outcome_payload=outcome_payload,
                envelope_clause_id=verdict.clause_id,
                envelope_action=verdict.action,
                envelope_causal_event_digest=causal_event_digest,
            )
        except Exception as exc:
            _record_hook_outcome(
                db, task_id=task.id, agent=agent, outcome=OUTCOME_AUDIT_FAILURE,
                policy=policy, snapshot=snapshot, candidate_id=candidate_id,
                fences=fences, lifecycle="consumed", verdict=verdict,
                error=f"same-root continuation commit failed: {exc}",
            )
            return "escalate"
        if not committed:
            # Cancellation/stale won the final CAS: no continuation.
            _record_hook_outcome(
                db, task_id=task.id, agent=agent, outcome=OUTCOME_CANCELLED_STALE,
                policy=policy, snapshot=snapshot, candidate_id=candidate_id,
                fences=fences, lifecycle="consumed", verdict=verdict,
                error="same-root continuation CAS lost (cancelled/stale)",
            )
            return "escalate"
        # Re-enqueue the root for its next manager decision step. Best-effort:
        # the run_step claim CAS keeps at-most-once admission if a replay lands.
        # THR-229 C3d4a: route through the common DB-aware entry so a root whose
        # durable pointer is pending(G) publishes its generation instead of
        # emitting an untagged fallback.
        queue = getattr(orch, "_queue", None)
        if queue is not None:
            enqueue_task_generation_aware(orch, queue, orch._slug, task.id)
        return "continue_same_root"

    # ESCALATE (fail-closed default): the existing escalation path proceeds.
    _record_hook_outcome(
        db, task_id=task.id, agent=agent, outcome=OUTCOME_ESCALATED,
        policy=policy, snapshot=snapshot, candidate_id=candidate_id,
        fences=fences, lifecycle="consumed", verdict=verdict,
        error=verdict.error,
    )
    return "escalate"


def publish_authority_policy_v2_notifications(
    orch, queue, *, limit: int = 32, root_task_id: str | None = None,
) -> list[dict]:
    """Real publication entry for pending v2 continuation generations.

    Independently discovers EVERY ``needed``/``publishing``/``published``
    recovery notification whose root dispatch pointer is ``pending(G)`` and has
    no generation admission (including exact already-consumed recovery
    receipts), then for each target:

    1. claims publication through the authenticated C3d3a public store seam
       (ONE ``BEGIN IMMEDIATE``; null lease alone is never proof and a live
       lease is never stolen);
    2. ONLY after a winning claim, calls the real ``TaskQueue.put_nowait``
       OUTSIDE any DB transaction with
       ``metadata={"authority_v2_generation": G, "publication_attempt": P}``;
    3. authenticates and acknowledges the exact claim (``publishing`` ->
       ``published`` + closed audit); if the consumer already admitted G, the
       acknowledgement records only the exact ``publish_returned(P)``
       observation and never regresses state.

    ``P`` is diagnostic; only ``G`` is admission authority.  A failed claim
    performs NO queue call.  A queue exception uses the existing bounded
    audited failure path (the prior claim stays safely reclaimable).  An
    acknowledgement/audit failure leaves the exact lease/state replayable.
    This function performs no evaluation, no remint and no task mutation, and
    returns bounded per-target receipts for the caller.
    """
    db = orch._db
    receipts: list[dict] = []
    try:
        targets = db.list_authority_policy_v2_publication_targets()
    except Exception as exc:  # discovery is read-only best effort
        return [{"status": "discovery_failed", "error": type(exc).__name__}]
    if root_task_id is not None:
        # Common-entry single-target use: publication stays the SAME
        # authenticated claim -> raw put -> exact ack sequence; only the
        # discovery scope narrows.  A pending root with NO publishable
        # notification yields an EMPTY receipt list, which the caller must
        # treat as a refusal (never an ordinary fallback).
        targets = [t for t in targets if t.root_task_id == root_task_id]
    for target in targets[: max(0, limit)]:
        claim = db.claim_authority_policy_v2_notification_publication(
            root_task_id=target.root_task_id,
            manager_agent=target.manager_agent,
            manager_session_id=target.manager_session_id,
            result_id=target.result_id,
        )
        if claim.status != "claimed":
            receipts.append({
                "status": claim.status,
                "reason": claim.reason,
                "notification_id": target.notification_id,
            })
            continue
        try:
            queue.put_nowait(
                orch._slug,
                target.root_task_id,
                metadata={
                    "authority_v2_generation": claim.generation_id,
                    "publication_attempt": claim.publication_attempt,
                },
            )
        except Exception as exc:
            failure = db.record_authority_policy_v2_notification_publication_failure(
                root_task_id=target.root_task_id,
                manager_agent=target.manager_agent,
                manager_session_id=target.manager_session_id,
                result_id=target.result_id,
                publication_attempt=claim.publication_attempt,
                publisher_boot_id=claim.publisher_boot_id,
            )
            receipts.append({
                "status": "publish_failed",
                "reason": failure.reason,
                "notification_id": target.notification_id,
                "publication_attempt": claim.publication_attempt,
                "error": type(exc).__name__,
            })
            continue
        ack = db.acknowledge_authority_policy_v2_notification_publication(
            root_task_id=target.root_task_id,
            manager_agent=target.manager_agent,
            manager_session_id=target.manager_session_id,
            result_id=target.result_id,
            publication_attempt=claim.publication_attempt,
            publisher_boot_id=claim.publisher_boot_id,
        )
        receipts.append({
            "status": ack.status,
            "reason": ack.reason,
            "notification_id": target.notification_id,
            "generation_id": claim.generation_id,
            "publication_attempt": claim.publication_attempt,
        })
    return receipts


# Closed bounded outcomes of the common DB-aware enqueue entry (THR-229 C3d4a).
ENQUEUE_DISPATCH_ORDINARY = "ordinary"
ENQUEUE_DISPATCH_PUBLISHED = "published"
ENQUEUE_DISPATCH_REFUSED = "refused"
ENQUEUE_DISPATCH_NO_QUEUE = "no_queue"


def enqueue_task_generation_aware(
    orch, queue, slug: str, task_id: str, *, metadata: dict | None = None,
) -> str:
    """Common DB-aware task enqueue entry (THR-229 checkpoint C3d4a).

    Resolves the TARGET root's durable v2 generation at PRODUCTION time -- the
    target's OWN ``authority_policy_v2_root_dispatch`` pointer, never request
    metadata and never a parent's token -- and routes accordingly:

    * ``absent``   -> the unchanged ordinary enqueue (metadata preserved);
    * ``pending``  -> the EXISTING authenticated notification publisher for this
      exact root (real claim -> raw ``TaskQueue`` put OUTSIDE any transaction ->
      exact acknowledgement).  A claim that did not win, a queue failure whose
      audited failure obligation was recorded, or a pending root with no
      publishable notification all REFUSE: no ordinary untagged fallback is ever
      emitted for a pending v2 generation;
    * ``admitted`` -> refuse (the generation was already reserved/launched; an
      ordinary enqueue must not relaunch it);
    * ``retired``  -> ordinary enqueue (a spent old generation must not
      blanket-block legitimate later work);
    * ``malformed``/``unreadable`` -> refuse (never ordinary permission).

    The publisher transport stays DISTINCT from this entry (it calls
    ``queue.put_nowait`` directly), so routing a pending root through the common
    entry cannot recurse.  Publication may repeat; generation admission may not
    (the DB claim fence remains the non-bypassable backstop).  Returns one of
    the bounded ``ENQUEUE_DISPATCH_*`` status strings.
    """
    if queue is None:
        return ENQUEUE_DISPATCH_NO_QUEUE
    db = getattr(orch, "_db", None)
    if db is None or not hasattr(
        db, "classify_authority_policy_v2_root_dispatch_for_enqueue"
    ):
        # No durable classifier available: fall back to the unchanged ordinary
        # enqueue (mock/legacy test seams).  Production always carries a real
        # Database, where classification below governs.
        _ordinary_enqueue(queue, slug, task_id, metadata)
        return ENQUEUE_DISPATCH_ORDINARY
    try:
        classification = db.classify_authority_policy_v2_root_dispatch_for_enqueue(
            task_id
        )
    except Exception:
        logger.exception("enqueue %s: v2 dispatch classification failed", task_id)
        return ENQUEUE_DISPATCH_REFUSED
    kind = getattr(classification, "kind", None)
    if kind == "pending":
        # Route the EXACT pending generation through the authenticated publisher.
        # The publisher performs the claim -> raw put -> ack itself; a refusal or
        # an empty target set means NO queue call and NO ordinary fallback.
        receipts = publish_authority_policy_v2_notifications(
            orch, queue, root_task_id=task_id,
        )
        published = any(
            isinstance(r, dict) and r.get("status") in ("published", "published_exact")
            for r in receipts
        )
        return ENQUEUE_DISPATCH_PUBLISHED if published else ENQUEUE_DISPATCH_REFUSED
    if kind in ("admitted", "malformed", "unreadable"):
        logger.warning(
            "enqueue %s: v2 dispatch %s refuses ordinary enqueue", task_id, kind,
        )
        return ENQUEUE_DISPATCH_REFUSED
    # ``absent`` and ``retired`` keep the unchanged ordinary path; an unexpected
    # kind can never become ordinary permission.
    if kind != "absent" and kind != "retired":
        return ENQUEUE_DISPATCH_REFUSED
    _ordinary_enqueue(queue, slug, task_id, metadata)
    return ENQUEUE_DISPATCH_ORDINARY


def _ordinary_enqueue(queue, slug: str, task_id: str, metadata: dict | None) -> None:
    """Raw ordinary enqueue preserving the legacy call shape exactly.

    Real ``TaskQueue`` exposes both ``put_nowait`` and ``enqueue``; a few test
    doubles expose only one.  Prefer ``put_nowait`` and fall back to ``enqueue``
    so the converged producers keep working with both without changing the
    ordinary tuple/metadata shape.
    """
    put = getattr(queue, "put_nowait", None)
    if put is None:
        put = getattr(queue, "enqueue")
    if metadata is None:
        put(slug, task_id)
    else:
        put(slug, task_id, metadata=metadata)
