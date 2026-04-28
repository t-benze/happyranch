from __future__ import annotations

import asyncio
import pytest
from unittest.mock import MagicMock


@pytest.mark.asyncio
async def test_enqueue_task_puts_id_on_state_queue(tmp_path):
    from src.config import Settings
    from src.daemon.runner import enqueue_task
    from src.daemon.state import DaemonState
    from src.runtime import RuntimeDir
    rt = RuntimeDir.init(tmp_path / "rt", slug="test")
    state = DaemonState.from_runtime(rt, Settings())
    enqueue_task(state, "TASK-001")
    assert state.queue._queue.get_nowait() == "TASK-001"


@pytest.mark.asyncio
async def test_enqueue_task_raises_when_idle():
    from src.config import Settings
    from src.daemon.runner import enqueue_task
    from src.daemon.state import DaemonState
    state = DaemonState.idle(Settings())
    with pytest.raises(RuntimeError):
        enqueue_task(state, "TASK-001")
