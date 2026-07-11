"""Per-org runtime state: DB, queue events, sessions, teams, locks.

One ``OrgState`` per active org under ``<runtime>/orgs/<slug>/``. Constructed
once at daemon startup (via ``DaemonState.from_runtime``) or lazily on
``happyranch orgs init <slug>``. Each instance is fully self-contained — no
cross-references to other orgs.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from pathlib import Path

from runtime.config import Settings
from runtime.daemon.dream_queue import DreamQueue
from runtime.daemon.event_bus import EventBus
from runtime.daemon.wake_queue import WakeQueue
from runtime.daemon.sessions import SessionTracker
from runtime.daemon.thread_queue import ThreadQueue
from runtime.infrastructure.database import Database
from runtime.infrastructure.thread_store import ThreadStore
from runtime.models import BlockKind, TaskStatus
from runtime.orchestrator._paths import OrgPaths
from runtime.orchestrator.orchestrator import Orchestrator
from runtime.orchestrator.org_validation import validate_team_membership
from runtime.orchestrator.teams import TeamsRegistry

logger = logging.getLogger(__name__)


@dataclass
class OrgState:
    slug: str
    root: Path                        # <runtime>/orgs/<slug>
    db: Database
    teams: TeamsRegistry
    settings: Settings
    orchestrator: Orchestrator
    sessions: SessionTracker = field(default_factory=SessionTracker)
    db_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    kb_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    teams_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    thread_queue: ThreadQueue = field(default_factory=ThreadQueue)
    dream_queue: DreamQueue = field(default_factory=DreamQueue)
    wake_queue: WakeQueue = field(default_factory=WakeQueue)
    event_bus: EventBus = field(init=False)
    thread_store: ThreadStore = field(init=False)

    _TERMINAL_STATUS_TO_EVENT = {
        TaskStatus.COMPLETED: "task_complete",
        TaskStatus.FAILED: "task_failed",
        # A superseded-resolution is a non-failure terminal, so it replays as a
        # completion-class event; `_synthesize_terminal_event` carries the
        # precise label in `outcome` ("superseded").
        TaskStatus.SUPERSEDED: "task_complete",
        # Path B: cancellation is a non-success terminal, so it replays as a
        # failure-class event — mirroring how SUPERSEDED rides the
        # completion class — with the precise label in `outcome` ("cancelled").
        # Avoids inventing a new EventBus event type. (The founder-facing record
        # is the distinct `log_task_cancelled` audit row; this map only governs
        # terminal-event replay synthesis.)
        TaskStatus.CANCELLED: "task_failed",
    }

    def __post_init__(self) -> None:
        def loader(task_id: str) -> list[dict]:
            task = self.db.get_task(task_id)
            if task is not None:
                # Task Activity: gather audit logs for the root task and all
                # descendants (children, grandchildren, etc.), then merge and
                # deduplicate in chronological order (by audit-log id).
                task_ids = [task_id]
                task_ids.extend(self.db.get_descendant_task_ids(task_id))
                all_logs: list[dict] = []
                for tid in task_ids:
                    all_logs.extend(self.db.get_audit_logs(tid))
                # Deduplicate by id, sort chronologically.
                seen: set[int] = set()
                history: list[dict] = []
                for log in sorted(all_logs, key=lambda x: x["id"]):
                    if log["id"] not in seen:
                        seen.add(log["id"])
                        history.append({"type": "audit", **log})
            else:
                # Non-task topic (thread:*, job:*) or unknown task id:
                # preserve existing behavior — try audit logs for the raw
                # topic string (always empty for non-task topics).
                history = [
                    {"type": "audit", **log}
                    for log in self.db.get_audit_logs(task_id)
                ]
            terminal = self._synthesize_terminal_event(task) if task else None
            if terminal is not None:
                history.append(terminal)
            return history
        self.event_bus = EventBus(history_loader=loader)
        self.thread_store = ThreadStore(self.root / "threads")

    def _synthesize_terminal_event(self, task) -> dict | None:
        if task.status in self._TERMINAL_STATUS_TO_EVENT:
            return {
                "type": self._TERMINAL_STATUS_TO_EVENT[task.status],
                "outcome": task.status.value,
                "synthesized": True,
            }
        # Path B: escalated is a top-level non-terminal status. Late
        # subscribers still get the right synthesized event.
        if task.status == TaskStatus.ESCALATED:
            return {
                "type": "task_blocked",
                "outcome": "escalated",
                "synthesized": True,
            }
        return None

    @classmethod
    def load(cls, *, slug: str, root: Path, settings: Settings) -> "OrgState":
        paths = OrgPaths(root=root)
        db = Database(paths.db_path)
        teams = TeamsRegistry.load(root)
        # THR-052: register any custom executor profiles from the org config
        # BEFORE validating active agent files, so agents declaring custom
        # executors defined in the same org's config.yaml can pass validation.
        #
        # Error handling:
        # - ExecutorProfileCollisionError: a different org already registered
        #   the same profile name with incompatible semantics. This is a hard
        #   semantic conflict — propagate it so the operator sees it (caught
        #   by DaemonState.from_runtime for startup, propagated by add_org).
        # - OrgConfigError / ValueError: malformed config or invalid profile
        #   definition (missing argv, bad adapter, command not found). Logged
        #   but does not prevent org load — the org still functions with
        #   built-in executors. An active agent depending on an unregistered
        #   profile will then fail validation normally below.
        from runtime.orchestrator.org_config import (
            load_org_config, OrgConfigError,
        )
        from runtime.orchestrator.executor_registry import get_registry
        from runtime.orchestrator.executor_registry import (
            ExecutorProfileCollisionError,
        )
        try:
            org_config = load_org_config(paths)
            if org_config.executor_profiles:
                get_registry().register_custom_from_config(
                    org_config.executor_profiles
                )
        except ExecutorProfileCollisionError:
            raise  # hard semantic conflict — propagate
        except (OrgConfigError, ValueError) as exc:
            logger.error(
                "org %r: failed to register executor_profiles: %s", slug, exc
            )
        except Exception as exc:
            logger.error(
                "org %r: failed to register executor_profiles: %s", slug, exc
            )
        # Refuse to attach if agent files and teams.yaml disagree. Raises
        # OrgConsistencyError on drift; DaemonState.from_runtime catches
        # per-org so one broken org cannot crash daemon startup, while
        # add_org propagates so explicit founder actions fail loudly.
        validate_team_membership(paths, teams)
        orchestrator = Orchestrator(
            db=db,
            settings=settings,
            paths=paths,
            slug=slug,
            teams=teams,
        )
        return cls(
            slug=slug,
            root=root,
            db=db,
            teams=teams,
            settings=settings,
            orchestrator=orchestrator,
        )

    def close(self) -> None:
        self.db.close()



