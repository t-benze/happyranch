from __future__ import annotations

import json
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys

import pytest

from runtime.infrastructure.database import Database


FIXTURES = Path(__file__).parent / "fixtures"
EXPECTED_OBJECTS = {
    "thread_reply_breaker_episodes",
    "thread_reply_breaker_receipts",
    "idx_thread_reply_breaker_due",
    "idx_thread_reply_breaker_probe_lease",
    "idx_thread_reply_breaker_receipts_episode",
}


def _copy_fixture(tmp_path: Path, fixture: str) -> Path:
    path = tmp_path / "compatibility.db"
    shutil.copyfile(FIXTURES / fixture, path)
    return path


def _schema_objects(path: Path) -> set[str]:
    connection = sqlite3.connect(path)
    try:
        return {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type IN ('table','index')"
            )
        }
    finally:
        connection.close()


def _legacy_rows(database: Database) -> tuple[str, str]:
    thread = database.get_thread("THR-HIST")
    messages = database.list_thread_messages("THR-HIST")
    assert thread is not None
    assert len(messages) == 1
    return thread.subject, messages[0].body_markdown


def test_shipping_initializer_forward_migrates_pinned_v0_and_reopens(tmp_path):
    path = _copy_fixture(tmp_path, "thread_breaker_v0_e197b20.db")
    assert EXPECTED_OBJECTS.isdisjoint(_schema_objects(path))

    database = Database(path)
    assert EXPECTED_OBJECTS <= _schema_objects(path)
    assert _legacy_rows(database) == ("historical-v0", "legacy-v0-row")
    database.close()

    reopened = Database(path)
    assert EXPECTED_OBJECTS <= _schema_objects(path)
    assert _legacy_rows(reopened) == ("historical-v0", "legacy-v0-row")
    reopened.close()


@pytest.mark.parametrize(
    ("fixture", "expected_failures"),
    [
        ("thread_breaker_v1_2c068bb.db", 1),
        ("thread_breaker_interrupted_2c068bb.db", 2),
    ],
)
def test_shipping_initializer_repairs_pinned_v1_stages_idempotently(
    tmp_path, fixture, expected_failures,
):
    path = _copy_fixture(tmp_path, fixture)
    database = Database(path)
    assert EXPECTED_OBJECTS <= _schema_objects(path)
    row = database._conn.execute(
        "SELECT state, consecutive_failures FROM thread_reply_breaker_episodes "
        "WHERE thread_id='THR-HIST' AND agent_name='legacy_agent'"
    ).fetchone()
    assert tuple(row) == ("closed", expected_failures)
    episode = database.get_thread_reply_breaker(
        "THR-HIST", "legacy_agent", "codex:gpt-5"
    )
    assert episode is not None
    assert episode.state == "closed"
    assert episode.consecutive_failures == expected_failures
    assert _legacy_rows(database)[0].startswith("historical-")
    database.close()

    reopened = Database(path)
    assert EXPECTED_OBJECTS <= _schema_objects(path)
    reopened.close()


def test_actual_pinned_e197b20_database_reader_keeps_real_contract(tmp_path):
    path = _copy_fixture(tmp_path, "thread_breaker_v0_e197b20.db")
    Database(path).close()

    completed = subprocess.run(
        [sys.executable, str(FIXTURES / "thread_breaker_old_reader_e197b20.py"), str(path)],
        cwd=Path(__file__).parents[1],
        check=True,
        capture_output=True,
        text=True,
    )
    result = json.loads(completed.stdout)
    assert result["thread_type"] == "ThreadRecord"
    assert result["thread"]["id"] == "THR-HIST"
    assert result["thread"]["subject"] == "historical-v0"
    assert result["message_types"] == ["ThreadMessage"]
    assert result["messages"][0]["body_markdown"] == "legacy-v0-row"
