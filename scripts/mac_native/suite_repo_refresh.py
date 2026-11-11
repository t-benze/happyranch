"""S5: real repository preparation and macOS group/census refresh, in scratch."""
from __future__ import annotations

import json
import os
import signal
import subprocess
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from runtime.infrastructure.database import Database
from runtime.infrastructure.memory_collection import CollectionObserver
from runtime.infrastructure.team_move_store import TeamMoveStore
from runtime.models import DreamRecord
from runtime.orchestrator.host_supervisor import build_default_host_supervisor
from runtime.orchestrator.producer_move import ProducerPreparation
from runtime.orchestrator.team_move import TeamMoveCoordinator
from runtime.orchestrator.teams import TeamManager, TeamsRegistry
from runtime.orchestrator.workspace_adapters import refresh_workspace_repos
from runtime.platform.macos_process_group import MacOSProcessGroupBackend
from runtime.platform.session_backend import Capability, CapabilityLevel


@contextmanager
def git_environment(scratch: Path) -> Iterator[None]:
    """Private HOME/config and no inherited Git overrides, for fixture AND product."""
    original = dict(os.environ)
    home = scratch / "home"
    home.mkdir()
    try:
        for key in tuple(os.environ):
            if key.startswith("GIT_"):
                del os.environ[key]
        os.environ.update(HOME=str(home), XDG_CONFIG_HOME=str(home),
                          GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull,
                          GIT_TERMINAL_PROMPT="0")
        yield
    finally:
        os.environ.clear()
        os.environ.update(original)


def git(cwd: Path, *arguments: str) -> str:
    """Only fixed local-fixture commands; output is file-backed and bounded."""
    with tempfile.TemporaryFile() as output:
        subprocess.run(["git", *arguments], cwd=cwd, stdin=subprocess.DEVNULL,
                       stdout=output, stderr=output, timeout=5, check=True)
        output.seek(0)
        raw = output.read(4097)
    if len(raw) > 4096:
        raise RuntimeError("fixture Git output exceeds 4096B")
    return raw.decode("utf-8").strip()


def stale_clone(scratch: Path) -> tuple[Path, str, str]:
    origin, writer, workspace = scratch / "origin.git", scratch / "writer", scratch / "workspace"
    (workspace / "repos").mkdir(parents=True)
    git(scratch, "init", "--bare", "--initial-branch=main", str(origin))
    git(scratch, "clone", str(origin), str(writer))
    git(writer, "config", "user.name", "Refresh Probe")
    git(writer, "config", "user.email", "refresh@example.invalid")
    (writer / "value.txt").write_text("first\n", encoding="utf-8")
    git(writer, "add", "value.txt")
    git(writer, "commit", "-m", "first")
    git(writer, "push", "origin", "main")
    clone = workspace / "repos" / "sample"
    git(scratch, "clone", str(origin), str(clone))
    old = git(clone, "rev-parse", "HEAD")
    (writer / "value.txt").write_text("second\n", encoding="utf-8")
    git(writer, "add", "value.txt")
    git(writer, "commit", "-m", "second")
    git(writer, "push", "origin", "main")
    return workspace, old, git(origin, "rev-parse", "refs/heads/main")


@contextmanager
def preparation(scratch: Path, suffix: str) -> Iterator[ProducerPreparation]:
    """Real native producer reservation, audit writers and preparation closure."""
    db = Database(scratch / f"{suffix}.db")
    owner = None
    try:
        observer = CollectionObserver(org="probe", root=scratch, db=db)
        db._memory_collection_context = SimpleNamespace(db=db, memory_collection=observer)
        move = TeamMoveCoordinator.for_org(TeamMoveStore(db, "probe"))
        teams = TeamsRegistry({"probe": TeamManager(name="probe", team="probe", workers=())})
        move.bind_teams(lambda: teams)
        now = datetime.now(UTC)
        db.insert_dream(DreamRecord(id="DREAM-001", agent_name="probe", local_date=now.date().isoformat(),
                                   scheduled_for=now, window_end=now + timedelta(minutes=5)))
        owner = move.producers.reserve("dream", "DREAM-001")
        if owner is None:
            raise RuntimeError("native preparation not admitted")
        yield owner
    finally:
        try:
            if owner is not None:
                owner.finish()
        finally:
            db.close()


def run(report: dict[str, Any]) -> None:
    checks: list[dict[str, Any]] = []
    report["repo_refresh"] = {"checks": checks}

    def check(name: str, expected: Any, actual: Any) -> None:
        checks.append({"name": name, "expected": expected, "actual": actual,
                           "status": "PASS" if expected == actual else "FAIL"})

    def expired(_signum: int, _frame: Any) -> None:
        raise TimeoutError("repo-refresh suite exceeds 120s")

    previous = signal.signal(signal.SIGALRM, expired)
    signal.setitimer(signal.ITIMER_REAL, 120)
    scratch_path = None
    try:
        with tempfile.TemporaryDirectory(prefix="repo-refresh-") as folder:
            scratch = Path(folder).resolve(strict=True)
            scratch_path = scratch
            with git_environment(scratch):
                workspace, old, target = stale_clone(scratch)
                clone = workspace / "repos" / "sample"
                check("clone_stale_before", True, old != target)
                receipts = []
                supervisor = build_default_host_supervisor(backend=MacOSProcessGroupBackend(), publisher=receipts.append)
                probe = supervisor.probe()
                check("backend", "macos-process-group", probe.backend)
                check("probe_healthy", True, probe.healthy)
                check("capability", CapabilityLevel.BEST_EFFORT.value,
                      probe.level(Capability.KILLS_TREE_BEST_EFFORT).value)
                # Negative uses the real honest passthrough, without altering the Mac probe.
                from runtime.platform.passthrough_backend import PassthroughBackend
                refused = build_default_host_supervisor(backend=PassthroughBackend())
                with preparation(scratch, "refused") as owner:
                    result = refresh_workspace_repos(workspace, preparation=owner, host_supervisor=refused)
                    check("passthrough_refused", {"sample": False}, result)
                    check("refusal_keeps_clone_stale", old, git(clone, "rev-parse", "HEAD"))
                    check("refusal_no_obligations", 0, len(owner._repository_preparations))
                with preparation(scratch, "accepted") as owner:
                    result = refresh_workspace_repos(workspace, preparation=owner, host_supervisor=supervisor)
                    check("refresh_admitted", True, bool(receipts))
                    check("refresh_result", {"sample": True}, result)
                    check("head_equals_origin", target, git(clone, "rev-parse", "HEAD"))
                    check("receipts_quiescent", True, bool(receipts) and all(r.quiescent is True for r in receipts))
                    check("no_surviving_children", 0, sum(len(r.survivors) for r in receipts))
                    check("preparation_closed", True, bool(owner._repository_preparations)
                          and all(e.closed and not e.uncertain for e in owner._repository_preparations.values()))
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)
    check("private_scratch_removed", False, scratch_path.exists() if scratch_path is not None else None)
    if len(json.dumps(report).encode("utf-8")) > 16384:
        raise RuntimeError("repo-refresh report exceeds 16KiB")
    failed = [row["name"] for row in checks if row["status"] != "PASS"]
    if failed:
        raise RuntimeError("repo-refresh failed: " + ", ".join(failed))
