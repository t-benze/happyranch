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

import lark_oapi as lark

from src.config import Settings
from src.daemon.event_bus import EventBus
from src.daemon.sessions import SessionTracker
from src.daemon.thread_queue import ThreadQueue
from src.infrastructure.audit_logger import AuditLogger
from src.infrastructure.database import Database
from src.infrastructure.feishu.client import FeishuClient
from src.infrastructure.feishu.notifier import EscalationNotifier
from src.infrastructure.thread_store import ThreadStore
from src.models import BlockKind, TaskStatus
from src.orchestrator._paths import OrgPaths
from src.orchestrator.orchestrator import Orchestrator
from src.orchestrator.org_config import (
    OrgConfig,
    load_org_config,
)
from src.orchestrator.org_validation import validate_team_membership
from src.orchestrator.teams import TeamsRegistry

logger = logging.getLogger(__name__)


_REGION_TO_DOMAIN = {
    "feishu": lark.FEISHU_DOMAIN,
    "lark": lark.LARK_DOMAIN,
}


@dataclass
class OrgState:
    slug: str
    root: Path                        # <runtime>/orgs/<slug>
    db: Database
    teams: TeamsRegistry
    settings: Settings
    orchestrator: Orchestrator
    notifier: EscalationNotifier | None = None
    feishu_listener: object | None = None  # FeishuEventListener at runtime; typed as object to avoid circular import
    # Captured Feishu attrs — used by Phase 2's FeishuEventListener:
    feishu_app_id: str | None = None
    feishu_app_secret: str | None = None
    feishu_domain: str | None = None
    feishu_chat_id: str | None = None
    sessions: SessionTracker = field(default_factory=SessionTracker)
    db_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    kb_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    teams_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    thread_queue: ThreadQueue = field(default_factory=ThreadQueue)
    event_bus: EventBus = field(init=False)
    thread_store: ThreadStore = field(init=False)

    _TERMINAL_STATUS_TO_EVENT = {
        TaskStatus.COMPLETED: "task_complete",
        TaskStatus.FAILED: "task_failed",
    }

    def __post_init__(self) -> None:
        def loader(task_id: str) -> list[dict]:
            history: list[dict] = [
                {"type": "audit", **log}
                for log in self.db.get_audit_logs(task_id)
            ]
            task = self.db.get_task(task_id)
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
        if task.status == TaskStatus.BLOCKED and task.block_kind == BlockKind.ESCALATED:
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
        feishu_attrs = _build_feishu_attrs(slug=slug, paths=paths, db=db)
        notifier = feishu_attrs["notifier"] if feishu_attrs else None
        if notifier is not None:
            orchestrator.attach_notifier(notifier)
        return cls(
            slug=slug,
            root=root,
            db=db,
            teams=teams,
            settings=settings,
            orchestrator=orchestrator,
            notifier=notifier,
            feishu_app_id=feishu_attrs["app_id"] if feishu_attrs else None,
            feishu_app_secret=feishu_attrs["app_secret"] if feishu_attrs else None,
            feishu_domain=feishu_attrs["domain"] if feishu_attrs else None,
            feishu_chat_id=feishu_attrs["chat_id"] if feishu_attrs else None,
        )

    def close(self) -> None:
        self.db.close()


def _build_feishu_attrs(
    *, slug: str, paths: OrgPaths, db: Database,
) -> dict | None:
    """Resolve Feishu config + credentials. Returns dict with notifier (may be
    None) and the raw app_id/app_secret/domain/chat_id needed by the listener
    in Phase 2. Returns None if no Feishu block at all."""
    cfg: OrgConfig = load_org_config(paths)
    if cfg.feishu_notifications is None:
        return None

    app_id = cfg.feishu_notifications.app_id
    app_secret = cfg.feishu_notifications.app_secret
    domain = _REGION_TO_DOMAIN[cfg.feishu_notifications.region]
    sdk_client = (
        lark.Client.builder()
        .app_id(app_id)
        .app_secret(app_secret)
        .domain(domain)
        .log_level(lark.LogLevel.WARNING)
        .build()
    )
    feishu_client = FeishuClient(sdk_client=sdk_client)
    notifier = EscalationNotifier(
        slug=slug,
        db=db,
        audit=AuditLogger(db),
        client=feishu_client,
        config=cfg.feishu_notifications,
    )
    return {
        "notifier": notifier,
        "app_id": app_id,
        "app_secret": app_secret,
        "domain": domain,
        "chat_id": cfg.feishu_notifications.chat_id,
    }
