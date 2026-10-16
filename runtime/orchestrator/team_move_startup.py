"""Early restricted move discovery and supported reader/switch refusal.

Unknown ownership/process/context proof stays diagnostic-only. This owner never
uses a coherent roster, elapsed lease, missing PID/tracker or source rollback
as permission to release a durable decision. Other orgs can attach normally.
"""
from __future__ import annotations

from runtime.infrastructure.team_move_store import TeamMoveError, inspect_team_move_database
from runtime.orchestrator._paths import OrgPaths

READER_CAPABILITIES = frozenset({"online-team-move@1"})


class TeamMoveStartupHold(TeamMoveError):
    def __init__(self, row):
        self.operation_id, self.phase, self.state_revision = row["operation_id"], row["phase"], row["state_revision"]
        super().__init__("team_move_startup_recovery_required")

    def __str__(self):
        return (f"{self.code}: operation {self.operation_id}, phase {self.phase}, revision {self.state_revision}; "
                "keep this org diagnostic-only and use admitted bounded same-operation recovery; "
                "canonical coherence alone is not context/reset/process proof")


def before_org_attachment(root, slug):
    """Called before naming preflight, Database(), schema or roster writers."""
    row = inspect_team_move_database(OrgPaths(root).db_path, org_slug=slug)
    if row is not None:
        # Restricted recovery may reconstruct ONLY a fully authenticated owned
        # prefix. This automatic path has no process/owner proof, so performs
        # ZERO reconstruction/writes and leaves explicit native recovery to D/E.
        raise TeamMoveStartupHold(row)


def require_runtime_reader(runtime, capabilities=READER_CAPABILITIES):
    """Inspect every org, including ones ordinary roster discovery would skip."""
    orgs = runtime.orgs_dir
    if not orgs.exists():
        return
    for entry in sorted(orgs.iterdir()):
        if entry.name in {"_pending", "_archive"}:
            continue
        if entry.is_symlink():
            if "online-team-move@1" in capabilities:
                continue  # Per-org diagnostic owner refuses the redirect.
            raise TeamMoveError("team_move_runtime_org_redirected")
        if not entry.is_dir():
            continue
        try:
            row = inspect_team_move_database(OrgPaths(entry).db_path, org_slug=entry.name)
        except TeamMoveError:
            if "online-team-move@1" in capabilities:
                continue  # Per-org diagnostic discovery below owns refusal.
            raise
        if row is not None and "online-team-move@1" not in capabilities:
            raise TeamMoveError("team_move_incompatible_reader")


def refuse_runtime_detach(runtime):
    """Switch/activation cannot orphan a durable hold in an inactive runtime."""
    if runtime is None or not runtime.orgs_dir.exists():
        return
    for entry in sorted(runtime.orgs_dir.iterdir()):
        if entry.name in {"_pending", "_archive"} or not entry.is_dir():
            continue
        row = inspect_team_move_database(OrgPaths(entry).db_path, org_slug=entry.name)
        if row is not None:
            raise TeamMoveError("team_move_runtime_detach_held")


def diagnostic_move_orgs(runtime):
    """Discover even missing/partial canonical rosters before ordinary iteration."""
    result = {}
    if not runtime.orgs_dir.exists():
        return result
    for entry in sorted(runtime.orgs_dir.iterdir()):
        if entry.name in {"_pending", "_archive"} or not entry.is_dir():
            continue
        try:
            before_org_attachment(entry, entry.name)
        except TeamMoveError as exc:
            result[entry.name] = str(exc)
    return result
