"""Retained no-authority adapters in the existing temp-root helper venue."""
from __future__ import annotations

import io
import json
import os
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from runtime.remote_access import home_activation_backend as backend
from runtime.remote_access.home_retirement import MESSAGE
from runtime.remote_access.installer import ManagedInstaller


@pytest.mark.parametrize('method,step', [('Enable', 'enable'), ('ReplaceFailed', 'replace')])
@pytest.mark.parametrize('state', ['clean', 'prepared', 'ready', 'pending', 'retired'])
def test_retired_activation_adapter_preserves_historical_state(tmp_path: Path, monkeypatch, method, step, state):
    native = Mock(side_effect=AssertionError('retired adapter attempted a native effect'))
    io_owner = ManagedInstaller(root=tmp_path, root_owner=(os.getuid(), os.getgid()), run=native)
    local = tmp_path / 'local-work'
    local.write_bytes(b'synthetic local work')
    if state != 'clean':
        history = tmp_path / 'var/lib/happyranch-remote-access'
        history.mkdir(parents=True)
        (history / ('home-retired.json' if state == 'retired' else 'home-activation.json')).write_text(state)
    before = {str(p): p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
    child = backend.HelperLifecycleBackend(io_owner)
    read = Mock(side_effect=AssertionError('retired adapter read historical authority'))
    monkeypatch.setattr(child.store, 'current', read)
    expected = child.capture(method, 1000)
    assert json.loads(expected) == dict(uid=1000, retirement='home-mode-retired')
    assert child.dispatch(method, 1000, step, expected) == {'outcome': 'refused', 'message': MESSAGE}
    assert not read.called and not native.called
    assert {str(p): p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()} == before


@pytest.mark.parametrize('method,step', [('Enable', 'enable'), ('ReplaceFailed', 'replace')])
@pytest.mark.parametrize('case', ['wrong-uid', 'stale', 'wrong-step', 'effect-flag'])
def test_retired_activation_adapter_keeps_closed_input(tmp_path, method, step, case):
    child = backend.HelperLifecycleBackend(ManagedInstaller(root=tmp_path,
        root_owner=(os.getuid(), os.getgid()), run=Mock(side_effect=AssertionError('unexpected native effect'))))
    expected = child.capture(method, 1000)
    with pytest.raises(backend.ActivationRefused):
        child.dispatch(method, 1001 if case == 'wrong-uid' else 1000,
            'stop' if case == 'wrong-step' else step,
            'stale' if case == 'stale' else expected, revoke_succeeded=case == 'effect-flag')


@pytest.mark.parametrize('method,step', [('Enable', 'enable'), ('ReplaceFailed', 'replace'), ('IssueInvite', 'issue'), ('CancelInvite', 'recover'), ('RecoverInvite', 'recover')])
def test_retired_activation_root_child_never_selects_account_tools(tmp_path, monkeypatch, capsys, method, step):
    native = Mock(side_effect=AssertionError('account or native command selected'))
    owner = ManagedInstaller(root=tmp_path, root_owner=(os.getuid(), os.getgid()), run=native)
    monkeypatch.setattr(backend, 'ManagedInstaller', lambda **kwargs: owner)
    monkeypatch.setattr(os, 'geteuid', lambda: 0)
    monkeypatch.setattr(sys, 'argv', ['home_activation_backend.py'])
    child = backend.HelperLifecycleBackend(owner)
    for action, expected in [('capture', None), (step, child.capture(method, 1000))]:
        request = dict(method=method, uid=1000, step=action, expected=expected,
                       deadline=time.monotonic() + 30)
        monkeypatch.setattr(sys, 'stdin', SimpleNamespace(buffer=io.BytesIO(json.dumps(request).encode())))
        with monkeypatch.context() as fault:
            from runtime.remote_access import service_manager
            fault.setattr(service_manager, '_default_run', native)
            assert backend.helper_main() == 0
            native.assert_not_called()
        output = capsys.readouterr()
        assert not output.err
        refusal = ({'result': 'refused', 'message': MESSAGE} if method == 'IssueInvite' else
            dict(outcome='refused', invite='unknown', cleanup='unknown', message=MESSAGE)
            if method in ('CancelInvite', 'RecoverInvite') else {'outcome': 'refused', 'message': MESSAGE})
        assert json.loads(output.out) == ({'snapshot': child.capture(method, 1000)} if action == 'capture' else refusal)


@pytest.mark.parametrize('method', ['IssueInvite', 'CancelInvite', 'RecoverInvite'])
@pytest.mark.parametrize('state', ['clean', 'prepared', 'ready', 'pending', 'retired'])
def test_retired_invite_adapter_preserves_historical_state(tmp_path, method, state):
    from runtime.remote_access.helper_invite_backend import HelperInviteBackend
    native = Mock(side_effect=AssertionError('unexpected native effect'))
    owner = ManagedInstaller(root=tmp_path, root_owner=(os.getuid(), os.getgid()), run=native)
    historical = tmp_path / 'old-invite'
    historical.write_text(state)
    child = HelperInviteBackend(owner)
    expected = child.capture(1000)
    assert json.loads(expected) == dict(uid=1000, retirement='home-mode-retired')
    commit = Mock(side_effect=AssertionError('retired invite requested effect authority'))
    result = child.dispatch(method, 1000, 'issue' if method == 'IssueInvite' else 'recover', expected,
        deadline=time.monotonic() + 30, before_commit=commit)
    assert result == ({'result': 'refused', 'message': MESSAGE} if method == 'IssueInvite' else
                      dict(outcome='refused', invite='unknown', cleanup='unknown', message=MESSAGE))
    assert historical.read_text() == state and not commit.called and not native.called


@pytest.mark.parametrize('method', ['IssueInvite', 'CancelInvite', 'RecoverInvite'])
@pytest.mark.parametrize('case', ['uid', 'stale', 'step', 'late', 'nonfinite'])
def test_retired_invite_adapter_keeps_closed_input(tmp_path, method, case):
    from runtime.remote_access.helper_invite_backend import HelperInviteBackend
    child = HelperInviteBackend(ManagedInstaller(root=tmp_path,
        root_owner=(os.getuid(), os.getgid()), run=Mock(side_effect=AssertionError('unexpected native effect'))))
    expected = child.capture(1000)
    with pytest.raises(ValueError, match='refused'):
        child.dispatch(method, 1001 if case == 'uid' else 1000,
            'stop' if case == 'step' else 'issue' if method == 'IssueInvite' else 'recover',
            'stale' if case == 'stale' else expected,
            deadline=float('nan') if case == 'nonfinite' else time.monotonic() + (-1 if case == 'late' else 30),
            before_commit=Mock(side_effect=AssertionError('unexpected effect authority')))
