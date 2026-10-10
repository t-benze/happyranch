import os

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
