"""U3a sequential ownership attachment: all ports refuse or hold, without effects."""
from __future__ import annotations

from typing import Any


class SequentialDispatcher:
    """Recognize a same-org owner without admitting or producing sequential work."""

    def __init__(self, org: Any) -> None:
        self.org = org

    def queue_admission_allowed(self, task_id: str) -> bool:
        return False

    def callback_uncommitted(self, *, task_id: str, agent: str, session_id: str,
                             payload: dict, capture: Any = None, budget: Any = None) -> bool:
        return False

    def callback_replay(self, *, task_id: str, agent: str, session_id: str, payload: dict) -> bool:
        return False

    def heartbeat(self, task_id: str, identity: Any = None) -> None:
        pass

    def notify_queued(self, task_id: str, queue: Any) -> None:
        pass

    def dispatch(self, task_id: str) -> None:
        pass
