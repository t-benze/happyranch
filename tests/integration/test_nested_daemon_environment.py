import json
import os
import subprocess
import sys

from tests.integration.conftest import _nested_daemon_env


def test_nested_daemon_environment_is_copy_with_exact_two_removals(monkeypatch):
    keys = ('HAPPYRANCH_TASK_TMP_ROOT', 'HAPPYRANCH_TASK_SCRATCH_MANIFEST')
    for key in keys:
        monkeypatch.setenv(key, '/synthetic-outer/' + key)
    for key in ('TMPDIR', 'TMP', 'TEMP'):
        monkeypatch.setenv(key, '/synthetic-preserved/' + key)
    monkeypatch.setenv('UNRELATED_JOB_CONTEXT', 'preserved')
    before = dict(os.environ)
    child = _nested_daemon_env()
    assert child == {k: v for k, v in before.items() if k not in keys}
    assert dict(os.environ) == before
    assert child is not os.environ
    result = subprocess.run(
        [sys.executable, '-c', 'import os,json; print(json.dumps(dict(os.environ)))'],
        env=child, capture_output=True, text=True, check=True,
    )
    observed = json.loads(result.stdout)
    assert observed == child
    assert dict(os.environ) == before


import pytest


@pytest.fixture
def outer_markers_before_daemon():
    return {key: os.environ.get(key) for key in (
        'HAPPYRANCH_TASK_TMP_ROOT', 'HAPPYRANCH_TASK_SCRATCH_MANIFEST',
        'TMPDIR', 'TMP', 'TEMP',
    )}


@pytest.mark.integration
def test_nested_idle_daemon_preserves_parent_markers(outer_markers_before_daemon, live_daemon_idle):
    import httpx
    response = httpx.get(f'http://127.0.0.1:{live_daemon_idle}/api/v1/health')
    assert response.status_code == 200
    assert {key: os.environ.get(key) for key in outer_markers_before_daemon} == outer_markers_before_daemon
