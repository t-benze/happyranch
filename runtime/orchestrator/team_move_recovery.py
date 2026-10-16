"""Explicit restricted context for an org excluded from ordinary attachment.

Construction validates only; it starts no producer/recovery/publication, creates
no memory/workspace and installs no layout. E must authenticate BEFORE invoking
this owner and close it before ordinary reattachment after durable completion.
"""
from __future__ import annotations

import base64
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

from runtime.infrastructure.database import Database
from runtime.infrastructure.team_move_store import MoveRequest, TeamMoveError, TeamMoveStore, _decode, _digest, _inspection_error
from runtime.orchestrator._paths import OrgPaths
from runtime.orchestrator.team_move import TeamMoveCoordinator
from runtime.orchestrator.team_move_canonical import check_parents, read_regular, validate_candidate
from runtime.orchestrator.team_move_operation import TeamMoveOperation
from runtime.orchestrator.teams import TeamsRegistry
from runtime.workflows.authority import WorkflowAuthorityCoordinator


@dataclass
class RestrictedMoveContext:
    slug: str
    root: Path
    settings: object
    db: Database
    teams: TeamsRegistry
    sessions: object
    orchestrator: object
    workflow_authority: WorkflowAuthorityCoordinator
    naming_readiness: str = "unavailable"

    def close(self):
        try:
            self.db.close()
        except (OSError, sqlite3.Error) as exc:
            raise _inspection_error("team_move_recovery_database_close_failed", exc) from exc


def open_recovery_context(*, root, slug, settings, profiles, host_supervisor, sessions, diagnostic_only=False, operation_key=None):
    """Use actual current host/tracker owners, never caller quiescence flags."""
    if not diagnostic_only and (host_supervisor is None or sessions is None):
        raise TeamMoveError("team_move_recovery_native_host_tracker_required")
    db = Database._open_team_move_recovery(OrgPaths(root).db_path, org_slug=slug, require_active=not diagnostic_only)
    try:
        store = TeamMoveStore(db, slug)
        row = store.read(operation_key=operation_key) if diagnostic_only else store.read()
        if row is None:
            raise TeamMoveError("team_move_recovery_operation_required")
        request = _decode(MoveRequest, row["request_bytes"])
        states = []
        for image in request.files:
            raw, identity = read_regular(check_parents(root, image.path))
            if identity.mode != image.identity.mode or identity.device != image.identity.device:
                raise TeamMoveError("team_move_recovery_canonical_identity_unknown")
            if _digest(raw) == image.before_digest and identity == image.identity:
                states.append("before")
            elif _digest(raw) == image.after_digest:
                states.append("after")
            else:
                raise TeamMoveError("team_move_recovery_canonical_bytes_unknown")
        candidate = validate_candidate(root, request.files)
        teams = TeamsRegistry.from_bytes(base64.b64decode(request.files[-1].before_base64), root=root) if all(s == "before" for s in states) else candidate
        move = TeamMoveCoordinator.for_org(store)
        move.bind_teams(lambda: teams)
        move.producers.root, move.producers.sessions = root, sessions
        authority = WorkflowAuthorityCoordinator(db=db, org_slug=slug, root=root, teams=teams)
        authority._profile_coordinator = profiles
        org = RestrictedMoveContext(slug, root, settings, db, teams, sessions,
            SimpleNamespace(_host_supervisor=host_supervisor), authority)
        from runtime.identities.schema import validate_names
        with db._lock:
            if validate_names(db._conn, org_slug=slug):
                org.naming_readiness = "ready"
        return org, TeamMoveOperation(org, profiles)
    except BaseException as primary:
        try:
            db.close()
        except BaseException as cleanup:
            primary.team_move_secondary_codes = (*getattr(primary, "team_move_secondary_codes", ()),
                TeamMoveOperation._diagnostic(cleanup).code)
            primary.add_note("restricted move context close failed: " + type(cleanup).__name__)
        raise
