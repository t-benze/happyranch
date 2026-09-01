"""THR-055 B2 custom-skill API: no proposal state or lifecycle coupling."""
from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from difflib import unified_diff
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Body, Depends, HTTPException, Header, Query, Request, status
from runtime.daemon.auth import _require_human, require_token
from runtime.daemon.routes._org_dep import OrgDep
from runtime.skills.custom import service
from runtime.skills.eligibility import EligibilityRecipient, EligibilityRule, SkillEligibilityState, resolve_custom_skill_eligibility

router = APIRouter(prefix="/custom-skills", dependencies=[require_token()])
agent_custom_skills_router = APIRouter(prefix="/custom-skills")
_FORBIDDEN_IDENTITY = frozenset({"task_id","session_id","proposer_agent","agent","agent_name","org","org_slug","actor","eligibility","permission","permissions"})

def _error(code: str, status_code: int, detail: str | None = None):
    raise HTTPException(status_code=status_code, detail={"code": code, "detail": detail or code})


def _purge_contract(conn) -> None:
    if conn.execute("PRAGMA foreign_keys").fetchone()[0] != 1:
        _error("schema_contract_unsupported", 422)
    columns = {row[1] for row in conn.execute("PRAGMA table_info(custom_skills)")}
    table = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='custom_skill_purge_events'"
    ).fetchone()
    if not {"purged_at", "purge_id"}.issubset(columns) or table is None:
        _error("schema_contract_unsupported", 422)


def _mutable(row) -> None:
    if row["purged_at"]:
        _error("skill_purged", 410)

def _remove_artifact_from_store(store, key: str) -> None:
    """Best-effort compensation: delete a newly-created artifact and any
    now-empty parent directories so a failed write leaves no residue."""
    try:
        if store.exists(key):
            store.delete(key)
    except Exception:
        pass
    try:
        parent = store.path_for(key).parent
        while parent != store.root and parent.exists() and not any(parent.iterdir()):
            parent.rmdir()
            parent = parent.parent
    except Exception:
        pass

def _remove_artifact(org, key: str) -> None:
    from runtime.infrastructure.artifact_store import ArtifactStore
    from runtime.orchestrator._paths import OrgPaths
    _remove_artifact_from_store(ArtifactStore(OrgPaths(org.root).artifacts_dir), key)

def _artifact_key(slug: str, content: str) -> str:
    """Deterministic content-addressed artifact key for a SKILL.md body."""
    digest = hashlib.sha256(content.encode()).hexdigest()
    return f"custom-skills/{slug}/{digest}/SKILL.md"

def _write_artifact(org, key: str, content: str) -> None:
    """Persist the content artifact. On failure, remove any partial residue."""
    from runtime.infrastructure.artifact_store import ArtifactStore
    from runtime.orchestrator._paths import OrgPaths
    store = ArtifactStore(OrgPaths(org.root).artifacts_dir)
    try:
        store.put(key, content.encode())
    except Exception:
        _remove_artifact_from_store(store, key)
        raise

def _persist_validated_version(
    conn, *, org, slug: str, skill_id: str, skill_md: str,
    actor_kind: str, actor: str, validation: dict,
    parent_id: int | None = None, task_id: str | None = None,
    session_id: str | None = None, brief_digest: str | None = None,
    event: str = "version_saved",
) -> tuple[int, str, str, str, int | None]:
    """Append one validated version inside the caller's BEGIN IMMEDIATE block.

    Validation completed before this helper runs. The version row is inserted
    FIRST and the content artifact is written only after that insert succeeds:
    a byte-identical body (UNIQUE (skill_id, content_hash)) fails the insert
    and is rejected as 409 version_content_exists with zero residue and no
    artifact write — a concurrent duplicate can never delete a committed
    artifact. Duplicate-content translation is scoped strictly to that version
    INSERT:
    an integrity failure from ANY later stage (current-pointer update or
    either event append) is NOT a duplicate — it propagates to the generic
    handler below, which compensates only the artifact (and empty directories)
    this request wrote and lets the caller roll back, so a failed write never
    returns a false 409 with a stranded file. On any later failure the
    artifact (written by this request) is compensated and the caller rolls
    back the transaction, leaving no durable version/event/current-pointer/
    artifact residue. The append-only uniqueness invariant is preserved,
    never relaxed.

    Current-pointer selection (THR-210 PR 1, founder-approved): a VALID
    version always advances ``current_version_id``. An INVALID candidate is
    appended as immutable validation/provenance evidence but never displaces
    an existing ``current_version_id`` — the pointer is retained, so a
    malformed edit cannot darken a skill whose last valid version still
    exists. The one exception is initial creation (no prior pointer): the
    first version becomes current regardless of validity, because a NULL
    pointer is unreadable by every JOIN-based list/detail consumer and
    uneditable through the version route; the skill then darkens as
    ``current_version_invalid`` until a valid successor advances the pointer.
    The events appended are unchanged (``created``/``version_saved`` then
    ``validated``); version rows and events are never rewritten.

    Returns ``(version, content_hash, state, key, current_version_id)`` — the
    artifact key crosses the helper boundary so the CALLER keeps artifact
    compensation armed through its own ``conn.commit()`` (see
    ``_commit_compensating_artifact``), and the final element is the pointer
    after this write (equal to ``version`` for valid appends and for initial
    creation; the retained prior pointer for invalid evidence appends).
    """
    key = _artifact_key(slug, skill_md)
    artifact_written = False
    try:
        try:
            version, content_hash, state = service.create_version(
                conn, skill_id=skill_id, skill_md=skill_md, actor_kind=actor_kind,
                actor=actor, artifact_key=key, validation=validation,
                task_id=task_id, session_id=session_id, brief_digest=brief_digest,
                parent_id=parent_id,
            )
        except sqlite3.IntegrityError as exc:
            # Only the version INSERT can violate the append-only uniqueness
            # invariant, and at that point no artifact has been written by
            # this request, so there is nothing to compensate and no
            # concurrent-duplicate deletion race.
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "version_content_exists",
                    "detail": "A version with this exact content already exists for this skill",
                },
            ) from exc
        _write_artifact(org, key, skill_md)
        artifact_written = True
        current_row = conn.execute(
            "SELECT current_version_id FROM custom_skills WHERE id=?", (skill_id,)
        ).fetchone()
        existing_current = current_row["current_version_id"] if current_row else None
        if validation["ok"] or existing_current is None:
            new_current = version
        else:
            new_current = existing_current
        conn.execute(
            "UPDATE custom_skills SET current_version_id=? WHERE id=?",
            (new_current, skill_id),
        )
        service.append_event(conn, skill_id, event, actor, version, task_id=task_id, session_id=session_id)
        service.append_event(conn, skill_id, "validated", actor, version, task_id=task_id, session_id=session_id)
        return version, content_hash, state, key, new_current
    except Exception:
        if artifact_written:
            _remove_artifact(org, key)
        raise


def _commit_compensating_artifact(conn, org, key: str) -> None:
    """Commit the caller's BEGIN IMMEDIATE transaction with artifact
    compensation still armed.

    The content artifact is a filesystem write outside the SQLite transaction:
    by the time the route calls this, ``_persist_validated_version`` has
    returned successfully and the artifact this request wrote is durable even
    though the version row is still uncommitted. If ``conn.commit()`` fails,
    the caller's rollback clears every DB row but would strand the artifact —
    so this helper removes it (and any now-empty parent directories) before
    re-raising, keeping the write atomic with zero durable residue. Under
    BEGIN IMMEDIATE no other writer can commit between this request's artifact
    write and its commit, so removing the artifact cannot delete a committed
    row's content; compensation is disarmed by a successful commit.
    """
    try:
        conn.commit()
    except Exception:
        _remove_artifact(org, key)
        raise

def _recipients(org):
    from runtime.orchestrator._paths import OrgPaths
    from runtime.orchestrator.prompt_loader import load_agent
    agents = []
    for p in (org.root / "org" / "agents").glob("*.md"):
        name = p.stem
        item = load_agent(OrgPaths(root=org.root), name)
        agents.append(EligibilityRecipient(name, (getattr(item, "team", "engineering"),)))
    return agents

def _resolve(row, rules, recipient):
    return resolve_custom_skill_eligibility(
        SkillEligibilityState(bool(row["retired_at"]), row["validation_state"], bool(row["purged_at"])),
        [EligibilityRule(**dict(r)) for r in rules], recipient)

def _preview(org, row, rules):
    old = service.current_rules(org.db, row["id"])
    old_rules = [dict(x) for x in old]
    new_rules = [EligibilityRule(**x) for x in rules]
    visible, hidden, unchanged = [], [], []
    for recipient in _recipients(org):
        before = resolve_custom_skill_eligibility(SkillEligibilityState(bool(row["retired_at"]), row["validation_state"]), [EligibilityRule(**x) for x in old_rules], recipient).visible
        after = resolve_custom_skill_eligibility(SkillEligibilityState(bool(row["retired_at"]), row["validation_state"]), new_rules, recipient).visible
        (visible if after and not before else hidden if before and not after else unchanged).append(recipient.agent_name)
    return {"newly_visible": visible, "newly_hidden": hidden, "unchanged": unchanged, "revision": row["version_id"]}

def create_agent_custom_skill(slug: str, session_id: str, org: OrgDep, request: Request, body: dict) -> dict:
    """Create an editable B2 custom skill from verified session provenance."""
    if "authorization" in request.headers: _error("bearer_not_accepted", 401)
    if next((key for key in _FORBIDDEN_IDENTITY if key in body), None) is not None: _error("body_identity_rejected", 403)
    context = org.sessions.get_context_by_session(session_id)
    if context is None: _error("unknown_session", 403)
    verified_org, task_id, agent = context
    if verified_org != slug: _error("cross_org_session", 403)
    lease = org.sessions._get_binding_lease(task_id, agent)
    with lease:
        if org.sessions.get_active(task_id, agent) != session_id: _error("session_not_current", 403)
        skill_slug, skill_md = body.get("slug", ""), body.get("skill_md", "")
        if not skill_slug or not body.get("name") or not skill_md: _error("invalid_request", 422)
        validation_result = service.validate_package(
            org, slug=skill_slug, name=body["name"], skill_md=skill_md,
        )
        if "slug_collision" in validation_result["reason_codes"]:
            _error("protected_slug", 409)
        # THR-210 PR 1: a document-contract validation failure is appended as
        # immutable evidence, never silently discarded — but a protected-slug
        # candidate is still hard-rejected above (policy gate, not evidence).
        conn = getattr(org.db, "_conn", org.db); existing = conn.execute("SELECT * FROM custom_skills WHERE org_slug=? AND slug=?", (slug, skill_slug)).fetchone()
        if existing and (existing["origin_kind"] != "agent" or existing["origin_agent"] != agent): _error("not_origin_owner", 403)
        if existing:
            _mutable(existing)
        task = org.db.get_task(task_id)
        if task is None: _error("task_not_found", 422)
        brief = task.brief
        digest = hashlib.sha256(brief.encode()).hexdigest() if brief else None
        conn.execute("BEGIN IMMEDIATE")
        try:
            if existing:
                skill_id = existing["id"]
                version, digest_hash, validation, key, current = _persist_validated_version(
                    conn, org=org, slug=skill_slug, skill_id=skill_id,
                    skill_md=skill_md, actor_kind="agent", actor=agent,
                    validation=validation_result, parent_id=existing["current_version_id"],
                    task_id=task_id, session_id=session_id, brief_digest=digest,
                )
            else:
                skill_id = f"custom:{uuid.uuid4()}"; conn.execute("INSERT INTO custom_skills (id,org_slug,slug,name,description,origin_kind,origin_agent,created_at,created_by) VALUES (?,?,?,?,?,'agent',?,?,?)", (skill_id, slug, skill_slug, body["name"], body.get("description", ""), agent, service.now(), agent))
                version, digest_hash, validation, key, current = _persist_validated_version(
                    conn, org=org, slug=skill_slug, skill_id=skill_id,
                    skill_md=skill_md, actor_kind="agent", actor=agent,
                    validation=validation_result, task_id=task_id,
                    session_id=session_id, brief_digest=digest, event="created",
                )
            _commit_compensating_artifact(conn, org, key)
        except Exception: conn.rollback(); raise
    version_row = conn.execute("SELECT * FROM custom_skill_versions WHERE id=?", (version,)).fetchone()
    skill_row = service.current(conn, skill_id)
    return {
        "skill": dict(skill_row),
        "version": dict(version_row),
        "hidden_reason": "no_eligibility_policy",
        "provenance": {
            "verified_org": verified_org,
            "task_id": task_id,
            "agent_name": agent,
            "session_id": session_id,
            "task_brief_digest": digest,
        },
    }


@agent_custom_skills_router.post("/agent-create", status_code=201)
def agent_create(slug: str, session_id: str, org: OrgDep, request: Request, body: dict = Body(...)):
    return create_agent_custom_skill(slug, session_id, org, request, body)

@router.get("/catalog")
def catalog(
    slug: str,
    org: OrgDep,
    view: Literal["removed"] | None = Query(
        default=None,
        description="Omit for current custom skills, or use 'removed' for permanent tombstones only.",
    ),
):
    conn = getattr(org.db, "_conn", org.db)
    removed_clause = "s.purged_at IS NOT NULL" if view == "removed" else "s.purged_at IS NULL"
    rows = conn.execute(
        "SELECT s.*,v.content_hash,v.validation_state "
        "FROM custom_skills s JOIN custom_skill_versions v ON v.id=s.current_version_id "
        f"WHERE s.org_slug=? AND {removed_clause} ORDER BY s.name",
        (slug,),
    ).fetchall()
    skills = []
    for row in rows:
        skill = dict(row)
        skill["state"] = "permanently_removed" if row["purged_at"] else "retired" if row["retired_at"] else "active"
        skill["hidden_reason"] = "purged" if row["purged_at"] else "no_eligibility_policy" if not service.current_rules(conn, row["id"]) else None
        skills.append(skill)
    return {"skills": skills}

@router.post("", status_code=201)
def create_human(slug: str, body: dict = Body(...), org: OrgDep = None, _: None = Depends(_require_human)):
    skill_slug, skill_md = body.get("slug", ""), body.get("skill_md", "")
    if not skill_slug or not body.get("name") or not skill_md: _error("invalid_request", 422)
    validation_result = service.validate_package(
        org, slug=skill_slug, name=body["name"], skill_md=skill_md,
    )
    if "slug_collision" in validation_result["reason_codes"]:
        _error("protected_slug", 409)
    conn = getattr(org.db, "_conn", org.db)
    existing = conn.execute(
        "SELECT purged_at FROM custom_skills WHERE org_slug=? AND slug=?",
        (slug, skill_slug),
    ).fetchone()
    if existing:
        _error("slug_permanently_reserved" if existing["purged_at"] else "slug_exists", 409)
    skill_id = f"custom:{uuid.uuid4()}"; conn.execute("BEGIN IMMEDIATE")
    try:
        conn.execute("INSERT INTO custom_skills (id,org_slug,slug,name,description,origin_kind,created_at,created_by) VALUES (?,?,?,?,?,'human',?,?)", (skill_id,slug,skill_slug,body["name"],body.get("description", ""),service.now(),"founder"))
        version, content_hash, validation, key, current = _persist_validated_version(
            conn, org=org, slug=skill_slug, skill_id=skill_id,
            skill_md=skill_md, actor_kind="human", actor="founder",
            validation=validation_result, event="created",
        )
        _commit_compensating_artifact(conn, org, key)
    except Exception: conn.rollback(); raise
    return {"skill_id":skill_id,"version_id":version,"content_hash":content_hash,"validation_state":validation,"hidden_reason":"no_eligibility_policy","current_version_id":current}

@router.get("/{skill_id}")
def detail(skill_id: str, org: OrgDep, _: None = Depends(_require_human)):
    row = service.current(org.db, skill_id)
    if row is None: _error("not_found", 404)
    if row["purged_at"]:
        tombstone = service.purge_tombstone(org.db, skill_id)
        return {
            "id": row["id"], "skill_id": row["id"], "slug": row["slug"],
            "state": "permanently_removed", "retired_at": row["retired_at"],
            "purged_at": row["purged_at"], "purge_id": row["purge_id"],
            "physical_erasure": False, "already_purged": True,
            "actor": tombstone["actor"] if tombstone else "founder",
        }
    skill = dict(row)
    skill["hidden_reason"] = "no_eligibility_policy" if not service.current_rules(org.db, skill_id) else None
    return skill

@router.patch("/{skill_id}")
def patch_metadata(skill_id: str, body: dict = Body(...), org: OrgDep = None, _: None = Depends(_require_human)):
    allowed = {key: body[key] for key in ("name", "description") if key in body}
    if not allowed: _error("invalid_request", 422)
    row = service.current(org.db, skill_id)
    if row is None: _error("not_found", 404)
    _mutable(row)
    conn=getattr(org.db,"_conn",org.db); conn.execute("BEGIN IMMEDIATE")
    try:
        conn.execute("UPDATE custom_skills SET " + ", ".join(f"{key}=?" for key in allowed) + " WHERE id=?", (*allowed.values(),skill_id)); service.append_event(conn,skill_id,"version_saved","founder",row["version_id"]); conn.commit()
    except Exception: conn.rollback(); raise
    return dict(service.current(org.db,skill_id))

@router.post("/{skill_id}/versions", status_code=201)
def add_version(skill_id: str, body: dict = Body(...), org: OrgDep = None, _: None = Depends(_require_human)):
    row=service.current(org.db,skill_id)
    if row is None: _error("not_found",404)
    _mutable(row)
    skill_md=body.get("skill_md", "")
    if not skill_md: _error("invalid_request",422)
    validation_result = service.validate_package(
        org, slug=row["slug"], name=row["name"], skill_md=skill_md,
    )
    if "slug_collision" in validation_result["reason_codes"]:
        _error("protected_slug", 409)
    conn=getattr(org.db,"_conn",org.db); conn.execute("BEGIN IMMEDIATE")
    try:
        version,content_hash,validation,key,current = _persist_validated_version(
            conn, org=org, slug=row["slug"], skill_id=skill_id,
            skill_md=skill_md, actor_kind="human", actor="founder",
            validation=validation_result, parent_id=row["version_id"],
        )
        _commit_compensating_artifact(conn, org, key)
    except Exception: conn.rollback(); raise
    return {"skill_id":skill_id,"version_id":version,"content_hash":content_hash,"validation_state":validation,"current_version_id":current}

@router.get("/{skill_id}/versions")
def versions(skill_id: str, org: OrgDep, _: None = Depends(_require_human)):
    row = service.current(org.db, skill_id)
    if row is None: _error("not_found", 404)
    _mutable(row)
    return {"versions": [dict(r) for r in getattr(org.db, "_conn", org.db).execute("SELECT * FROM custom_skill_versions WHERE skill_id=? ORDER BY id DESC", (skill_id,)).fetchall()]}

@router.get("/{skill_id}/versions/{a}/diff/{b}")
def version_diff(skill_id: str, a: int, b: int, org: OrgDep, _: None = Depends(_require_human)):
    conn = getattr(org.db, "_conn", org.db)
    row = service.current(conn, skill_id)
    if row is None: _error("not_found", 404)
    _mutable(row)
    versions_by_id = {
        row["id"]: row
        for row in conn.execute(
            "SELECT * FROM custom_skill_versions WHERE skill_id=? AND id IN (?, ?)",
            (skill_id, a, b),
        ).fetchall()
    }
    if a not in versions_by_id or b not in versions_by_id:
        _error("version_not_found", 404)
    before, after = versions_by_id[a], versions_by_id[b]
    return {
        "a": {key: before[key] for key in ("id", "content_hash", "created_at", "author_kind", "author_identity")},
        "b": {key: after[key] for key in ("id", "content_hash", "created_at", "author_kind", "author_identity")},
        "diff": list(unified_diff(
            before["skill_md_cache"].splitlines(),
            after["skill_md_cache"].splitlines(),
            fromfile=f"version-{a}", tofile=f"version-{b}", lineterm="",
        )),
    }

@router.post("/{skill_id}/retire")
def retire(skill_id: str, body: dict = Body(default={}), org: OrgDep = None, _: None = Depends(_require_human)):
    row=service.current(org.db,skill_id)
    if row is None: _error("not_found",404)
    _mutable(row)
    conn=getattr(org.db,"_conn",org.db); conn.execute("BEGIN IMMEDIATE")
    try: conn.execute("UPDATE custom_skills SET retired_at=?,retired_by=?,retired_reason=? WHERE id=?",(service.now(),"founder",body.get("reason",""),skill_id));service.append_event(conn,skill_id,"retired","founder",row["version_id"]);conn.commit()
    except Exception: conn.rollback();raise
    return dict(service.current(org.db,skill_id))

@router.post("/{skill_id}/restore")
def restore(skill_id: str, org: OrgDep, _: None = Depends(_require_human)):
    row=service.current(org.db,skill_id)
    if row is None: _error("not_found",404)
    _mutable(row)
    conn=getattr(org.db,"_conn",org.db);conn.execute("BEGIN IMMEDIATE")
    try: conn.execute("UPDATE custom_skills SET retired_at=NULL,retired_by=NULL,retired_reason=NULL WHERE id=?",(skill_id,));service.append_event(conn,skill_id,"restored","founder",row["version_id"]);conn.commit()
    except Exception:conn.rollback();raise
    return dict(service.current(org.db,skill_id))


@router.post("/{skill_id}/purge")
def purge(slug: str, skill_id: str, body: dict = Body(...), org: OrgDep = None, _: None = Depends(_require_human)):
    """Synchronously commit an irreversible logical tombstone.

    The database commit is the synchronous logical-purge boundary; no
    filesystem object or retained evidence is removed. An identical or later
    retry returns the stable tombstone.
    """
    conn = getattr(org.db, "_conn", org.db)
    _purge_contract(conn)
    with service.canonical_publication_barrier(slug):
        conn.execute("BEGIN IMMEDIATE")
        try:
            row = service.current(conn, skill_id)
            if row is None or row["org_slug"] != slug:
                _error("skill_not_found", 404)
            if body.get("typed_slug") != row["slug"]:
                _error("typed_slug_mismatch", 422)
            existing = service.purge_tombstone(conn, skill_id)
            if existing is not None:
                conn.rollback()
                return {**dict(existing), "state": "permanently_removed", "already_purged": True}
            if row["retired_at"] is None:
                _error("skill_not_retired", 409)
            purge_id, purged_at = f"purge:{uuid.uuid4()}", service.now()
            # Preserve policy history while withdrawing the current policy.
            service.replace_rules(
                conn, skill_id=skill_id, actor="founder",
                revision=row["version_id"], rules=[],
                newly_visible=[], newly_hidden=[],
            )
            conn.execute(
                "INSERT INTO custom_skill_purge_events "
                "(purge_id,skill_id,org_slug,slug,actor,purged_at,physical_erasure) "
                "VALUES (?,?,?,?,?,?,0)",
                (purge_id, skill_id, slug, row["slug"], "founder", purged_at),
            )
            conn.execute(
                "UPDATE custom_skills SET purged_at=?,purge_id=? WHERE id=?",
                (purged_at, purge_id, skill_id),
            )
            conn.commit()
        except HTTPException:
            conn.rollback()
            raise
        except Exception:
            conn.rollback()
            raise
    return {
        "purge_id": purge_id, "skill_id": skill_id, "org_slug": slug,
        "slug": row["slug"], "actor": "founder", "purged_at": purged_at,
        "physical_erasure": 0, "state": "permanently_removed", "already_purged": False,
    }

@router.get("/{skill_id}/eligibility")
def get_eligibility(skill_id: str, org: OrgDep, _: None = Depends(_require_human)):
    row = service.current(org.db, skill_id)
    if row is None: _error("not_found", 404)
    _mutable(row)
    return {"rules": [dict(x) for x in service.current_rules(org.db, skill_id)], "revision": row["version_id"]}

@router.post("/{skill_id}/eligibility/preview")
def preview(skill_id: str, proposed: list[dict], org: OrgDep, _: None = Depends(_require_human)):
    row = service.current(org.db, skill_id)
    if row is None: _error("not_found", 404)
    _mutable(row)
    return _preview(org, row, proposed)

@router.put("/{skill_id}/eligibility")
def put_eligibility(skill_id: str, proposed: list[dict], org: OrgDep, if_match: str | None = Header(None), _: None = Depends(_require_human)):
    conn = getattr(org.db, "_conn", org.db); conn.execute("BEGIN IMMEDIATE")
    try:
        row = service.current(conn, skill_id)
        if row is None: _error("not_found", 404)
        _mutable(row)
        if if_match != str(row["version_id"]): _error("stale_revision", 409)
        if row["retired_at"] or row["validation_state"] != "valid": _error("version_not_eligible", 422)
        recipients = _recipients(org)
        known = {r.agent_name for r in recipients}; teams = {t for r in recipients for t in r.teams}
        for rule in proposed:
            target = rule.get("scope_target")
            if rule.get("scope_type") == "agent" and target not in known or rule.get("scope_type") == "team" and target not in teams: _error("unknown_target", 422)
        impact = _preview(org, row, proposed)
        service.replace_rules(conn, skill_id=skill_id, actor="founder", revision=row["version_id"], rules=proposed, newly_visible=impact["newly_visible"], newly_hidden=impact["newly_hidden"]); conn.commit()
    except Exception: conn.rollback(); raise
    return impact

@router.get("/{skill_id}/eligibility/explain")
def explain(skill_id: str, agent: str, org: OrgDep, _: None = Depends(_require_human)):
    row = service.current(org.db, skill_id)
    if row is None: _error("not_found", 404)
    _mutable(row)
    recipient = next((r for r in _recipients(org) if r.agent_name == agent), None)
    if recipient is None: _error("unknown_target", 422)
    result = _resolve(row, service.current_rules(org.db, skill_id), recipient)
    return {"visible": result.visible, "hidden_reason": result.reason, "winning_rule": result.winning_rule}
