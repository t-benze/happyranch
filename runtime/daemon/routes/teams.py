"""Founder-facing team reads and bounded empty Default provisioning."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response

from runtime.daemon.auth import _require_human, require_token
from runtime.daemon.routes._org_dep import OrgDep
from runtime.orchestrator.org_validation import OrgConsistencyError
from runtime.workflows.authority import WorkflowAuthorityError

router = APIRouter(dependencies=[require_token()])


@router.get("/teams")
def list_teams(slug: str, org: OrgDep) -> dict:
    """Return all registered teams + their managers + workers, sorted by team name."""
    return {"teams": [org.teams.team_row(name) for name in org.teams.teams()]}


@router.post("/teams/ensure-default", dependencies=[Depends(_require_human)],
             responses={202: {"description": "Default exists; authority is not ready"}})
async def ensure_default(slug: str, org: OrgDep, response: Response) -> dict:
    """Create only an absent empty founder team; never move or create agents."""
    created = False
    try:
        async with (
            org.workflow_authority.async_writer_interval(
                publisher="ensure_empty_default",
            ) as authority_change,
            org.teams_lock,
        ):
            with authority_change.validation():
                try:
                    needed = org.teams.needs_empty_default()
                except ValueError as exc:
                    raise HTTPException(status_code=409, detail={
                        "code": "default_team_conflict", "committed": False,
                        "message": str(exc),
                    }) from exc
                if needed:
                    # Read-only full snapshot precedes the mutation lease.
                    org.workflow_authority.capture_canonical_snapshot()
            if needed:
                with authority_change.canonical_change():
                    created = org.teams.ensure_empty_default()
                    try:
                        org.db.insert_audit_log(
                            task_id="config:teams", agent="founder",
                            action="team_default_ensured",
                            payload={"team": "default", "manager_kind": "human",
                                     "principal": "founder", "workers": [],
                                     "default_team": org.teams.default_team,
                                     "task_default_team": org.teams.task_default_team},
                        )
                    except Exception as exc:
                        raise HTTPException(status_code=500, detail={
                            "code": "default_team_audit_failed", "committed": True,
                            "message": "Default was saved; inspect teams and authority before retrying.",
                        }) from exc
        # Interval exit owns best-effort publication. Reacquire its process gate
        # for coherent current readback, outside every durable lease/transaction.
        async with (
            org.workflow_authority.async_writer_interval(publisher="ensure_empty_default_readback") as readback,
            org.teams_lock,
        ):
            with readback.validation():
                authority: dict
                try:
                    ready = org.workflow_authority.verify_admission_ready()
                    if org.workflow_authority.capture_snapshot() != ready.snapshot_bytes:
                        raise WorkflowAuthorityError("workflow_activation_authority_stale")
                    authority = {"ready": True, "namespace": ready.namespace,
                                 "generation": ready.generation, "snapshot_digest": ready.snapshot_digest}
                except (WorkflowAuthorityError, OrgConsistencyError, OSError, ValueError) as exc:
                    response.status_code = 202
                    authority = {"ready": False, "code": "default_team_authority_not_ready",
                                 "reason": exc.code if isinstance(exc, WorkflowAuthorityError) else str(exc),
                                 "message": "Default exists. Existing supported publication/recovery must complete; "
                                            "repeating this command does not repair authority."}
                return {"created": created, "team": org.teams.team_row("default"),
                        "default_team": org.teams.default_team,
                        "task_default_team": org.teams.task_default_team, "authority": authority}
    except (WorkflowAuthorityError, OrgConsistencyError) as exc:
        raise HTTPException(status_code=409, detail={
            "code": exc.code if isinstance(exc, WorkflowAuthorityError) else "teams_consistency_drift",
            "committed": created, "message": str(exc),
        }) from exc
    except OSError as exc:
        raise HTTPException(status_code=503, detail={
            "code": "default_team_save_failed", "committed": created,
            "message": "Registry save failed; inspect teams and authority before retrying.",
        }) from exc
