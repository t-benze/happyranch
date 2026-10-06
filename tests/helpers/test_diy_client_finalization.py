"""Scripted I/O only: no socket, timer thread, or daemon is created."""
from __future__ import annotations

import time

import pytest

from tests.remote_access import diy_client as client


@pytest.mark.parametrize("fault", ["cancel", "join", "response", "connection", "socket", "alive", "note"])
def test_admission_finalizers_preserve_primary_and_attempt_every_resource(monkeypatch, fault):
    events = []
    class Primary(RuntimeError):
        def add_note(self, value):
            if fault == "note":
                raise OSError("private note failure")
            super().add_note(value)
    primary = Primary("private primary")

    def operation(name):
        events.append(name)
        if name == fault or (fault == "note" and name == "cancel"):
            raise OSError("private cleanup")

    class Socket:
        def settimeout(self, value):
            pass
        def close(self):
            operation("socket")

    class Response:
        def close(self):
            operation("response")

    response = Response()

    class Connection:
        sock = Socket()
        def __init__(self, *args, **kwargs):
            pass
        def connect(self):
            pass
        def request(self, *args, **kwargs):
            pass
        def getresponse(self):
            return response
        def close(self):
            operation("connection")

    class Timer:
        def __init__(self, *args):
            pass
        def start(self):
            pass
        def cancel(self):
            operation("cancel")
        def join(self, **kwargs):
            operation("join")
        def is_alive(self):
            operation("alive")
            return False

    monkeypatch.setattr(client.http.client, "HTTPConnection", Connection)
    monkeypatch.setattr(client.threading, "Timer", Timer)
    with pytest.raises(RuntimeError) as caught:
        with client._admission_response("scripted", 0, "/tail", {}):
            raise primary
    assert caught.value is primary, "cleanup replaced the primary exception"
    assert {"cancel", "join", "response", "connection", "socket", "alive"} <= set(events), events
    assert "private cleanup" not in str(getattr(primary, "__notes__", ()))


@pytest.mark.parametrize("data,kind", [(b"", "truncated"), (b"bad\n\n", "protocol_error"),
                                      (b"x" * 64, "oversized")])
def test_scripted_first_frame_refuses_invalid_input(data, kind):
    class Response:
        status = 200
        def getheader(self, *args):
            return "text/event-stream"
        def read1(self, count):
            nonlocal data
            chunk, data = data[:count], data[count:]
            return chunk
    with pytest.raises(client.AdmissionError) as caught:
        client._read_first_sse_frame(Response(), time.monotonic() + 1)
    assert caught.value.kind == kind


def test_scripted_first_frame_reads_only_admission():
    data = bytearray(b"data: hello\n\n" + b"held response")
    class Response:
        status = 200
        def getheader(self, *args):
            return "text/event-stream"
        def read1(self, count):
            chunk = bytes(data[:count])
            del data[:count]
            return chunk
    assert client._read_first_sse_frame(Response(), time.monotonic() + 1) == 13
    assert data == b"held response", "admission drained the held body"


def _diy_observation_namespace():
    """Read only the named pure/command helpers, never import the integration file.

    Its module-level address probe is intentionally excluded on the live host.
    """
    import ast
    import json
    import os
    import selectors
    import subprocess
    import sys
    from pathlib import Path
    source = Path(__file__).resolve().parents[1] / 'remote_access/test_diy_acceptance.py'
    tree = ast.parse(source.read_text())
    names = {'_run_client', '_admit_readiness_report', '_bounded_readiness_command',
             '_diy_failure_facts', '_record_diy_failure'}
    nodes = [node for node in tree.body if
             (isinstance(node, ast.FunctionDef) and node.name in names) or
             (isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and
                t.id == '_DIY_GATE_CATEGORIES' for t in node.targets))]
    namespace = dict(json=json, os=os, selectors=selectors, subprocess=subprocess,
                     sys=sys, time=time, Path=Path, CLIENT=Path('scripted-client'))
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(source), 'exec'), namespace)
    return namespace


def test_diy_failed_client_never_exports_stderr_credentials(monkeypatch):
    from types import SimpleNamespace
    namespace = _diy_observation_namespace()
    monkeypatch.setattr(namespace['subprocess'], 'run', lambda *a, **k:
        SimpleNamespace(returncode=1, stdout='', stderr='hrpair_PLANTED /private/path'))
    with pytest.raises(AssertionError) as caught:
        namespace['_run_client']('scripted', 0, ['request'])
    assert 'hrpair_PLANTED' not in str(caught.value)
    assert '/private/path' not in str(caught.value)


def _ready_report():
    return {'ready': True, 'gates': {name: {'ok': True, 'category': category} for name, category in (
        ('daemon_loopback', 'daemon_loopback_ok'), ('credential_permissions', 'credential_ok'),
        ('current_policy', 'policy_current'), ('bind_identity', 'identity_ok'), ('trust_state', 'state_ok'))}}


@pytest.mark.parametrize('fault', ['category', 'extra', 'gate_extra', 'bool', 'status', 'oversize', 'duplicate'])
def test_diy_readiness_admission_refuses_private_or_invalid_output(fault):
    import json
    namespace = _diy_observation_namespace()
    report = _ready_report()
    status = 0
    if fault == 'category':
        report['gates']['trust_state']['category'] = 'hrpair_PLANTED /private/path'
    elif fault == 'extra':
        report['credential'] = 'hrpair_PLANTED'
    elif fault == 'gate_extra':
        report['gates']['trust_state']['detail'] = '/private/path'
    elif fault == 'bool':
        report['ready'] = 1
    elif fault == 'status':
        status = 1
    data = json.dumps(report).encode()
    if fault == 'oversize':
        data += b' ' * 8193
    elif fault == 'duplicate':
        data = data.replace(b'"ready": true', b'"ready": false,"ready": true')
    with pytest.raises(ValueError):
        namespace['_admit_readiness_report'](data, status)


@pytest.mark.parametrize('observation', ['ready', 'failed_gate', 'exited', 'command_error', 'private_output'])
def test_diy_failure_facts_export_only_closed_readiness_and_owned_state(observation):
    import json
    from types import SimpleNamespace
    namespace = _diy_observation_namespace()
    report = _ready_report()
    if observation == 'failed_gate':
        report['ready'] = False
        report['gates']['trust_state'] = {'ok': False, 'category': 'state_corrupt'}
    if observation == 'private_output':
        report['gates']['trust_state']['category'] = 'hrpair_PLANTED /private/path'
    def command(path):
        if observation == 'command_error':
            raise RuntimeError('hrpair_PLANTED /private/path')
        return json.dumps(report).encode(), 0 if report['ready'] else 1
    namespace['_bounded_readiness_command'] = command
    proc = SimpleNamespace(poll=lambda: 86 if observation == 'exited' else None)
    daemon = SimpleNamespace(_thread=SimpleNamespace(is_alive=lambda: True),
        _server=SimpleNamespace(fileno=lambda: 1), release=SimpleNamespace(is_set=lambda: False))
    facts = namespace['_diy_failure_facts']('not exported', proc, daemon)
    assert facts['connector'] == {'state': 'exited' if observation == 'exited' else 'running',
                                  'status': 86 if observation == 'exited' else None}
    assert facts['daemon'] == {'thread_alive': True, 'server_open': True, 'released': False}
    if observation in {'command_error', 'private_output'}:
        assert facts['readiness'] == 'unavailable' and 'gates' not in facts
    else:
        assert facts['ready'] is (observation != 'failed_gate')
        assert facts['gates'] == report['gates']
    encoded = json.dumps(facts)
    assert 'PLANTED' not in encoded and '/private/path' not in encoded and 'not exported' not in encoded


@pytest.mark.parametrize('fault', ['observe', 'note', 'none'])
def test_diy_failure_observation_keeps_primary_and_all_outer_finalizers(fault):
    namespace = _diy_observation_namespace()
    events = []
    class Primary(RuntimeError):
        def add_note(self, note):
            events.append('note')
            if fault == 'note':
                raise RuntimeError('PLANTED private')
            super().add_note(note)
    primary = Primary('original')
    def observe():
        events.append('observe')
        if fault == 'observe':
            raise RuntimeError('PLANTED private')
        return {'readiness': 'unavailable'}
    with pytest.raises(Primary) as caught:
        try:
            try:
                raise primary
            except BaseException as exc:
                namespace['_record_diy_failure'](exc, observe)
                raise
        finally:
            events.extend(['connector_finalized', 'daemon_finalized'])
    assert caught.value is primary
    assert events[-2:] == ['connector_finalized', 'daemon_finalized']
    assert 'observe' in events
    assert 'PLANTED' not in str(getattr(primary, '__notes__', []))
    if fault == 'none':
        assert primary.__notes__ == ['DIY_FAILURE_FACTS {"readiness":"unavailable"}']


@pytest.mark.parametrize('fault', ['normal', 'cap', 'deadline', 'kill', 'wait', 'pipe', 'selector'])
def test_diy_readiness_command_is_bounded_and_finalizes_owned_resources(monkeypatch, fault):
    from types import SimpleNamespace
    namespace = _diy_observation_namespace()
    events = []
    data = bytearray(b'x' * (8193 if fault == 'cap' else 10))
    class Pipe:
        def fileno(self):
            return 123
        def close(self):
            events.append('pipe')
            if fault == 'pipe':
                raise OSError('PLANTED private')
    class Process:
        stdout = Pipe()
        def poll(self):
            return None
        def kill(self):
            events.append('kill')
            if fault == 'kill':
                raise OSError('PLANTED private')
        def wait(self, *, timeout):
            assert 0 <= timeout <= 5
            events.append('wait')
            if fault == 'wait':
                raise OSError('PLANTED private')
            return 0
    class Selector:
        def register(self, *args):
            pass
        def select(self, *, timeout):
            assert 0 <= timeout <= 5
            return [] if fault == 'deadline' else [object()]
        def close(self):
            events.append('selector')
            if fault == 'selector':
                raise OSError('PLANTED private')
    def popen(args, **kwargs):
        assert args[1:4] == ['-m', 'runtime.remote_access.cli', 'readiness']
        assert kwargs == {'stdout': namespace['subprocess'].PIPE, 'stderr': namespace['subprocess'].DEVNULL}
        return Process()
    def read(fd, count):
        assert fd == 123 and 0 < count <= 8193
        chunk = bytes(data[:count]); del data[:count]
        return chunk
    monkeypatch.setattr(namespace['subprocess'], 'Popen', popen)
    monkeypatch.setattr(namespace['selectors'], 'DefaultSelector', Selector)
    monkeypatch.setattr(namespace['os'], 'set_blocking', lambda *a: None)
    monkeypatch.setattr(namespace['os'], 'read', read)
    if fault == 'normal':
        assert namespace['_bounded_readiness_command']('scripted') == (b'x' * 10, 0)
    else:
        with pytest.raises((ValueError, OSError)):
            namespace['_bounded_readiness_command']('scripted')
    assert {'kill', 'wait', 'pipe', 'selector'} <= set(events)
