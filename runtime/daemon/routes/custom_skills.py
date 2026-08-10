"""THR-055 B2 custom-skill API: no proposal state or lifecycle coupling."""
from __future__ import annotations

import hashlib
import json
import uuid
from difflib import unified_diff
from pathlib import Path

from fastapi import APIRouter, Body, Depends, HTTPException, Header, Query, Request, status
from runtime.daemon.auth import _check_optional_token, require_token
from runtime.daemon.routes._org_dep import OrgDep
from runtime.daemon.routes.skill_lifecycle import _AGENT_PILOT_SLUG_MAP, _require_human
from runtime.skills.custom import service
from runtime.skills.eligibility import EligibilityRecipient, EligibilityRule, SkillEligibilityState, resolve_custom_skill_eligibility

router = APIRouter(prefix="/custom-skills", dependencies=[require_token()])
agent_custom_skills_router = APIRouter(prefix="/custom-skills")
_FORBIDDEN_IDENTITY = frozenset({"task_id","session_id","proposer_agent","agent","agent_name","org","org_slug","actor","eligibility","permission","permissions"})

def _error(code: str, status_code: int, detail: str | None = None):
    raise HTTPException(status_code=status_code, detail={"code": code, "detail": detail or code})

def _store_content(org, slug: str, content: str) -> str:
    from runtime.infrastructure.artifact_store import ArtifactStore
    from runtime.orchestrator._paths import OrgPaths
    digest = hashlib.sha256(content.encode()).hexdigest()
    return ArtifactStore(OrgPaths(org.root).artifacts_dir).put(f"custom-skills/{slug}/{digest}/SKILL.md", content.encode()).name

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
        SkillEligibilityState(bool(row["retired_at"]), row["validation_state"]),
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

@agent_custom_skills_router.post("/agent-create", status_code=201)
def agent_create(slug: str, session_id: str, org: OrgDep, request: Request, body: dict = Body(...)):
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
        conn = getattr(org.db, "_conn", org.db); existing = conn.execute("SELECT * FROM custom_skills WHERE org_slug=? AND slug=?", (slug, skill_slug)).fetchone()
        if existing and (existing["origin_kind"] != "agent" or existing["origin_agent"] != agent): _error("not_origin_owner", 403)
        if _AGENT_PILOT_SLUG_MAP.get(agent) != skill_slug: _error("slug_not_allowed_for_agent", 403)
        key = _store_content(org, skill_slug, skill_md); brief = org.db.get_task(task_id).brief
        digest = hashlib.sha256(brief.encode()).hexdigest() if brief else None
        conn.execute("BEGIN IMMEDIATE")
        try:
            if existing:
                parent = existing["current_version_id"]; version, digest_hash, validation = service.create_version(conn, skill_id=existing["id"], skill_md=skill_md, actor_kind="agent", actor=agent, artifact_key=key, task_id=task_id, session_id=session_id, brief_digest=digest, parent_id=parent)
                conn.execute("UPDATE custom_skills SET current_version_id=? WHERE id=?", (version, existing["id"])); skill_id = existing["id"]; service.append_event(conn, skill_id, "version_saved", agent, version, task_id=task_id, session_id=session_id)
            else:
                skill_id = f"custom:{uuid.uuid4()}"; conn.execute("INSERT INTO custom_skills (id,org_slug,slug,name,description,origin_kind,origin_agent,created_at,created_by) VALUES (?,?,?,?,?,'agent',?,?,?)", (skill_id, slug, skill_slug, body["name"], body.get("description", ""), agent, service.now(), agent))
                version, digest_hash, validation = service.create_version(conn, skill_id=skill_id, skill_md=skill_md, actor_kind="agent", actor=agent, artifact_key=key, task_id=task_id, session_id=session_id, brief_digest=digest)
                conn.execute("UPDATE custom_skills SET current_version_id=? WHERE id=?", (version, skill_id)); service.append_event(conn, skill_id, "created", agent, version, task_id=task_id, session_id=session_id)
            service.append_event(conn, skill_id, "validated", agent, version, task_id=task_id, session_id=session_id); conn.commit()
        except Exception: conn.rollback(); raise
    return {"skill_id": skill_id, "version_id": version, "content_hash": digest_hash, "validation_state": validation, "hidden_reason": "no_eligibility_policy"}

@router.get("/catalog")
def catalog(slug: str, org: OrgDep, filter: str | None = None):
    rows = getattr(org.db, "_conn", org.db).execute("SELECT s.*,v.content_hash,v.validation_state FROM custom_skills s JOIN custom_skill_versions v ON v.id=s.current_version_id WHERE s.org_slug=? ORDER BY s.name", (slug,)).fetchall()
    return {"skills": [dict(row) for row in rows]}

@router.post("", status_code=201)
def create_human(slug: str, body: dict = Body(...), org: OrgDep = None, _: None = Depends(_require_human)):
    skill_slug, skill_md = body.get("slug", ""), body.get("skill_md", "")
    if not skill_slug or not body.get("name") or not skill_md: _error("invalid_request", 422)
    conn = getattr(org.db, "_conn", org.db)
    if conn.execute("SELECT 1 FROM custom_skills WHERE org_slug=? AND slug=?", (slug, skill_slug)).fetchone(): _error("slug_exists", 409)
    key = _store_content(org, skill_slug, skill_md); skill_id = f"custom:{uuid.uuid4()}"; conn.execute("BEGIN IMMEDIATE")
    try:
        conn.execute("INSERT INTO custom_skills (id,org_slug,slug,name,description,origin_kind,created_at,created_by) VALUES (?,?,?,?,?,'human',?,?)", (skill_id,slug,skill_slug,body["name"],body.get("description", ""),service.now(),"founder"))
        version, content_hash, validation = service.create_version(conn, skill_id=skill_id, skill_md=skill_md, actor_kind="human", actor="founder", artifact_key=key)
        conn.execute("UPDATE custom_skills SET current_version_id=? WHERE id=?", (version,skill_id)); service.append_event(conn,skill_id,"created","founder",version); service.append_event(conn,skill_id,"validated","founder",version); conn.commit()
    except Exception: conn.rollback(); raise
    return {"skill_id":skill_id,"version_id":version,"content_hash":content_hash,"validation_state":validation,"hidden_reason":"no_eligibility_policy"}

@router.get("/{skill_id}")
def detail(skill_id: str, org: OrgDep, _: None = Depends(_require_human)):
    row = service.current(org.db, skill_id)
    if row is None: _error("not_found", 404)
    return dict(row)

@router.patch("/{skill_id}")
def patch_metadata(skill_id: str, body: dict = Body(...), org: OrgDep = None, _: None = Depends(_require_human)):
    allowed = {key: body[key] for key in ("name", "description") if key in body}
    if not allowed: _error("invalid_request", 422)
    row = service.current(org.db, skill_id)
    if row is None: _error("not_found", 404)
    conn=getattr(org.db,"_conn",org.db); conn.execute("BEGIN IMMEDIATE")
    try:
        conn.execute("UPDATE custom_skills SET " + ", ".join(f"{key}=?" for key in allowed) + " WHERE id=?", (*allowed.values(),skill_id)); service.append_event(conn,skill_id,"version_saved","founder",row["version_id"]); conn.commit()
    except Exception: conn.rollback(); raise
    return dict(service.current(org.db,skill_id))

@router.post("/{skill_id}/versions", status_code=201)
def add_version(skill_id: str, body: dict = Body(...), org: OrgDep = None, _: None = Depends(_require_human)):
    row=service.current(org.db,skill_id)
    if row is None: _error("not_found",404)
    skill_md=body.get("skill_md", "")
    if not skill_md: _error("invalid_request",422)
    key=_store_content(org,row["slug"],skill_md); conn=getattr(org.db,"_conn",org.db); conn.execute("BEGIN IMMEDIATE")
    try:
        version,content_hash,validation=service.create_version(conn,skill_id=skill_id,skill_md=skill_md,actor_kind="human",actor="founder",artifact_key=key,parent_id=row["version_id"])
        conn.execute("UPDATE custom_skills SET current_version_id=? WHERE id=?",(version,skill_id));service.append_event(conn,skill_id,"version_saved","founder",version);service.append_event(conn,skill_id,"validated","founder",version);conn.commit()
    except Exception: conn.rollback(); raise
    return {"skill_id":skill_id,"version_id":version,"content_hash":content_hash,"validation_state":validation}

@router.get("/{skill_id}/versions")
def versions(skill_id: str, org: OrgDep, _: None = Depends(_require_human)):
    return {"versions": [dict(r) for r in getattr(org.db, "_conn", org.db).execute("SELECT * FROM custom_skill_versions WHERE skill_id=? ORDER BY id DESC", (skill_id,)).fetchall()]}

@router.get("/{skill_id}/versions/{a}/diff/{b}")
def version_diff(skill_id: str, a: int, b: int, org: OrgDep, _: None = Depends(_require_human)):
    conn = getattr(org.db, "_conn", org.db)
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
    conn=getattr(org.db,"_conn",org.db); conn.execute("BEGIN IMMEDIATE")
    try: conn.execute("UPDATE custom_skills SET retired_at=?,retired_by=?,retired_reason=? WHERE id=?",(service.now(),"founder",body.get("reason",""),skill_id));service.append_event(conn,skill_id,"retired","founder",row["version_id"]);conn.commit()
    except Exception: conn.rollback();raise
    return dict(service.current(org.db,skill_id))

@router.post("/{skill_id}/restore")
def restore(skill_id: str, org: OrgDep, _: None = Depends(_require_human)):
    row=service.current(org.db,skill_id)
    if row is None: _error("not_found",404)
    conn=getattr(org.db,"_conn",org.db);conn.execute("BEGIN IMMEDIATE")
    try: conn.execute("UPDATE custom_skills SET retired_at=NULL,retired_by=NULL,retired_reason=NULL WHERE id=?",(skill_id,));service.append_event(conn,skill_id,"restored","founder",row["version_id"]);conn.commit()
    except Exception:conn.rollback();raise
    return dict(service.current(org.db,skill_id))

@router.get("/{skill_id}/eligibility")
def get_eligibility(skill_id: str, org: OrgDep, _: None = Depends(_require_human)):
    row = service.current(org.db, skill_id)
    if row is None: _error("not_found", 404)
    return {"rules": [dict(x) for x in service.current_rules(org.db, skill_id)], "revision": row["version_id"]}

@router.post("/{skill_id}/eligibility/preview")
def preview(skill_id: str, proposed: list[dict], org: OrgDep, _: None = Depends(_require_human)):
    row = service.current(org.db, skill_id)
    if row is None: _error("not_found", 404)
    return _preview(org, row, proposed)

@router.put("/{skill_id}/eligibility")
def put_eligibility(skill_id: str, proposed: list[dict], org: OrgDep, if_match: str | None = Header(None), _: None = Depends(_require_human)):
    row = service.current(org.db, skill_id)
    if row is None: _error("not_found", 404)
    if if_match != str(row["version_id"]): _error("stale_revision", 409)
    if row["retired_at"] or row["validation_state"] != "valid": _error("version_not_eligible", 422)
    known = {r.agent_name for r in _recipients(org)}; teams = {t for r in _recipients(org) for t in r.teams}
    for rule in proposed:
        target = rule.get("scope_target")
        if rule.get("scope_type") == "agent" and target not in known or rule.get("scope_type") == "team" and target not in teams: _error("unknown_target", 422)
    impact = _preview(org, row, proposed); conn = getattr(org.db, "_conn", org.db); conn.execute("BEGIN IMMEDIATE")
    try: service.replace_rules(conn, skill_id=skill_id, actor="founder", revision=row["version_id"], rules=proposed, newly_visible=impact["newly_visible"], newly_hidden=impact["newly_hidden"]); conn.commit()
    except Exception: conn.rollback(); raise
    return impact

@router.get("/{skill_id}/eligibility/explain")
def explain(skill_id: str, agent: str, org: OrgDep, _: None = Depends(_require_human)):
    row = service.current(org.db, skill_id)
    if row is None: _error("not_found", 404)
    recipient = next((r for r in _recipients(org) if r.agent_name == agent), None)
    if recipient is None: _error("unknown_target", 422)
    result = _resolve(row, service.current_rules(org.db, skill_id), recipient)
    return {"visible": result.visible, "hidden_reason": result.reason, "winning_rule": result.winning_rule}
