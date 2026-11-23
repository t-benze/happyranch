"""Canonical fixed entry and real retirement/recovery composition.

Run: uv run --frozen pytest scripts/test_home_retirement_entry.py -v --tb=short
Filesystem/NSS/native observations use the existing independent adapters.
The entry, original journals, revocation, cleanup and final receipt are real.
"""
from __future__ import annotations

from contextlib import ExitStack
from datetime import datetime, timedelta, timezone
import builtins
import importlib.util
import json
import os
from pathlib import Path
import runpy
import shutil
import subprocess
import sys
from types import SimpleNamespace

import pytest

from runtime.remote_access import home_retirement as entry
from runtime.remote_access import home_retirement_upgrade as upgrade
from runtime.remote_access import retirement_io
from runtime.remote_access import pairing
from runtime.remote_access import home_retirement_inventory as inventory
from runtime.remote_access.home_retirement import HomeRetirement
from runtime.remote_access.pairing import PairingError
from scripts.test_home_retirement import Fixture
from scripts.test_home_retirement_account import account_adapter, deletions
from scripts.test_home_retirement_coordinator import active, native_target_stop, FINAL, INVENTORY, PIN
from scripts.test_home_retirement_inventory import auxiliary_journal_setup, TRANSACTION
from scripts.test_home_retirement_service import PYTHON


def packaged_preinst(tmp_path, monkeypatch, root, installed):
    """Use real package bytes; isolate only process/native/filesystem boundaries.

    The old interpreter is simulated in-process, including its isolated flag.
    This is neither installed CPython 3.12 nor dpkg/systemd acceptance.
    """
    repository = Path(__file__).resolve().parents[1]
    packaging = runpy.run_path(str(repository / 'packaging/linux/test_build_deb.py'))
    package = packaging['DebPackageTests']()
    package.work = tmp_path / 'package'
    package.work.mkdir()
    result = package.build(package.payload())
    assert result.returncode == 0, result.stderr
    deb, = (package.work / 'dist').glob('*.deb')
    control, payload = package.work / 'control', package.work / 'new-payload'
    subprocess.run(['dpkg-deb', '-e', deb, control], check=True)
    subprocess.run(['dpkg-deb', '-x', deb, payload], check=True)
    script = control / 'preinst'
    incoming = runpy.run_path(str(script))
    main = incoming['main']
    python = root / PYTHON
    original_python = python.read_bytes()
    children = []

    def capability():
        # Existing native/custody tests own installed bootstrap validation.
        # Here the old capability must actually survive until replacement.
        if python.read_bytes() != original_python:
            raise ValueError('synthetic unavailable old interpreter')

    def child(command, **options):
        assert command == ['/opt/happyranch-desktop/python/bin/python3', '-I', '-']
        assert options['env'] == {'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'LANG': 'C.UTF-8'}
        assert options['text'] is True
        capability()
        saved = {name: module for name, module in sys.modules.items()
                 if name == 'runtime' or name.startswith('runtime.')}
        loaders = list(sys.meta_path)
        real_spec, real_import = importlib.util.spec_from_loader, builtins.__import__
        loaded = []

        def spec(name, loader, **kwargs):
            if name == 'runtime':
                execute = loader.exec_module
                def module_source(module):
                    execute(module)
                    loaded.append(module.__name__)
                    if module.__name__ == 'runtime.remote_access.retirement_io':
                        io_type = module.RetirementIO
                        module.RetirementIO = lambda **kwargs: installed(io_type, **kwargs)
                    if module.__name__ == 'runtime.remote_access.home_retirement_inventory':
                        # Match the existing external-artifact adapter in the
                        # parent fixture. Fresh child modules must compare the
                        # synthetic binary against that independent trusted pin.
                        module._BINARIES = dict(inventory._BINARIES)
                    if module.__name__ == 'runtime.remote_access.home_retirement':
                        module.os = SimpleNamespace(**(vars(os) | {'geteuid': lambda: 0}))
                    assert module.__file__.startswith('<incoming-preinst:')
                loader.exec_module = module_source
            return real_spec(name, loader, **kwargs)

        # Only the child bootstrap sees the simulated interpreter capability.
        # Inline runtime modules use ordinary sys/builtins and exact source.
        child_sys = SimpleNamespace(**(vars(sys) | {
            'version_info': (3, 12), 'flags': SimpleNamespace(isolated=True)}))
        def importing(name, *args, **kwargs):
            return child_sys if name == 'sys' else real_import(name, *args, **kwargs)
        namespace = {'__name__': '__main__', '__builtins__': vars(builtins) | {'__import__': importing}}
        try:
            for name in saved:
                del sys.modules[name]
            with monkeypatch.context() as patch:
                patch.setattr(importlib.util, 'spec_from_loader', spec)
                with pytest.raises(SystemExit) as exited:
                    exec(compile(options['input'], '<preinst-child>', 'exec'), namespace)
            children.append(loaded)
            return SimpleNamespace(returncode=exited.value.code)
        finally:
            sys.meta_path[:] = loaders
            for name in tuple(sys.modules):
                if name == 'runtime' or name.startswith('runtime.'):
                    del sys.modules[name]
            sys.modules.update(saved)

    def invoke(action='upgrade'):
        with monkeypatch.context() as patch:
            patch.setitem(main.__globals__, 'os', SimpleNamespace(**(vars(os) | {'geteuid': lambda: 0})))
            patch.setitem(main.__globals__, 'trusted_python', capability)
            patch.setitem(main.__globals__, 'subprocess', SimpleNamespace(run=child))
            patch.setattr(sys, 'argv', ['preinst', action, 'old-version'])
            return main()
    return SimpleNamespace(invoke=invoke, children=children, payload=payload, python=python)


@pytest.mark.parametrize('case', ['fresh', 'drift'])
def test_packaged_preinst_bootstrap_preserves_read_only_gate(tmp_path, monkeypatch, capsys, case):
    fixture = Fixture(tmp_path / 'root')
    fixture.file(PYTHON, b'synthetic-old-interpreter', 0o755)
    if case == 'drift':
        fixture.file(FINAL, b'{"account":"absent","format":"home-retirement@1","state":"retired"}\n')
        fixture.file('etc/happyranch/invite-issuer.json')
    constructed = []
    def installed(io_type, **kwargs):
        assert kwargs == {'layout': 'deb'}
        constructed.append(True)
        return io_type(root=fixture.root, root_owner=fixture.io.root_owner, layout='deb',
            run=fixture.io._run, lookup_user=fixture.io._lookup_user, lookup_group=fixture.io._lookup_group)
    preinst = packaged_preinst(tmp_path, monkeypatch, fixture.root, installed)
    before = fixture.snapshot()
    assert preinst.invoke() == (0 if case == 'fresh' else 1)
    assert constructed == [True] and fixture.snapshot() == before
    assert 'runtime.remote_access.home_retirement_upgrade' in preinst.children[0]
    assert 'synthetic-' not in capsys.readouterr().out


def block_retired_imports(monkeypatch):
    """The incoming bundle cannot borrow unavailable provisioning owners."""
    original = builtins.__import__
    forbidden = {'installer', 'headscale_service', 'home_headscale', 'home_activation',
                 'home_activation_txn', 'home_activation_backend', 'home_admission', 'invite_issuer'}
    def retained(name, globals=None, locals=None, fromlist=(), level=0):
        if (name.removeprefix('runtime.remote_access.') in forbidden
                or name == 'runtime.remote_access' and forbidden.intersection(fromlist or ())):
            raise RuntimeError('retired production dependency unavailable')
        return original(name, globals, locals, fromlist, level)
    monkeypatch.setattr(builtins, '__import__', retained)


@pytest.mark.parametrize('case', ['fresh', 'retired', 'drift', 'malformed', 'arguments', 'nonroot'])
def test_fixed_entry_preserves_fresh_completed_and_refused_layouts(tmp_path, monkeypatch, capsys, case):
    fixture = Fixture(tmp_path / 'root')
    if case in ('retired', 'drift', 'malformed'):
        fixture.file(FINAL, b'{"account":"absent","format":"home-retirement@1","state":"retired"}\n'
            if case != 'malformed' else b'synthetic-unknown-final')
    if case == 'drift':
        fixture.file('etc/happyranch/invite-issuer.json')
    before = fixture.snapshot()
    constructed = []
    io_type = retirement_io.RetirementIO
    def installed(**kwargs):
        assert kwargs == {'layout': 'deb'}
        constructed.append(True)
        return io_type(root=fixture.root, root_owner=fixture.io.root_owner, layout='deb',
            run=fixture.io._run, lookup_user=fixture.io._lookup_user, lookup_group=fixture.io._lookup_group)
    monkeypatch.setattr(retirement_io, 'RetirementIO', installed)
    monkeypatch.setattr(os, 'geteuid', lambda: 1000 if case == 'nonroot' else 0)
    block_retired_imports(monkeypatch)
    result = entry.main(['--root=/caller-selected'] if case == 'arguments' else [])
    lines = capsys.readouterr().out.splitlines()
    assert lines[0] == entry.MESSAGE and len(lines) == 2
    status = json.loads(lines[1])
    assert result == (0 if case in ('fresh', 'retired') else 1)
    assert status['state'] == (case if case in ('fresh', 'retired') else 'migration pending')
    assert constructed == ([] if case in ('arguments', 'nonroot') else [True])
    assert fixture.snapshot() == before
    assert all(call[:3] == ['systemctl', 'show', entry.UNIT] for call in fixture.calls)


@pytest.mark.parametrize(('active', 'boundary'), [
    ('active', 'fragment'), ('inert', 'fragment'),
    ('active', 'publication'), ('inert', 'publication'),
    ('active', 'preparation'), ('active', 'ceremony'), ('active', 'first-device'),
    ('active', 'failed-close'), ('active', 'controller-stop'), ('active', 'controller-disable'),
    ('active', 'account'), ('active', 'before-publication'),
    ('inert', 'final-file-sync'), ('inert', 'final-stage-parent-sync'),
    ('inert', 'final-rename'), ('inert', 'final-renamed'),
    ('inert', 'final-parent-sync'), ('inert', 'final-return'),
    ('active', 'final-parent-sync'),
    ('active', 'preinst-fragment'), ('inert', 'preinst-publication'), ('active', 'preinst-first-device'),
], indirect=['active'])
def test_canonical_entry_completes_actual_interrupted_retirement_and_refuses_reappearance(active, monkeypatch, capsys, request, boundary, tmp_path):
    generated = boundary.startswith('preinst-')
    boundary = boundary.removeprefix('preinst-')
    inert = request.node.callspec.params['active'] == 'inert'
    auxiliary_journal_setup(active.controller)
    if inert:
        (active.root / TRANSACTION).unlink()
    else:
        def failed_close():
            raise OSError('synthetic failed original physical cleanup')
        nodes = ('first', 'second') if boundary in ('ceremony', 'first-device') else ('device',)
        token_hex, identifiers = pairing.secrets.token_hex, iter(('1' * 32, '2' * 32))
        with monkeypatch.context() as patch:
            patch.setattr(pairing, 'secrets', SimpleNamespace(token_hex=
                lambda size: next(identifiers) if size == 16 else token_hex(size)))
            grants = [active.grant(node) for node in nodes]
        devices = [device for device, _ in grants]
        streams = [active.stream(device, node, close=failed_close if boundary == 'failed-close' else None)
                   for device, node in zip(devices, nodes)]
        if boundary in ('ceremony', 'first-device'):
            active.manager.issue_invite_record('f' * 64, datetime.now(timezone.utc) + timedelta(minutes=2))
        native_target_stop(active)
    state = account_adapter(active.io, active.calls, fault='user-uncertain' if boundary == 'account' else '')
    previous_run = active.io._run
    reloads, interrupted, emitted = [], [], []
    uncertain_command = (['systemctl', boundary.removeprefix('controller-'), entry.UNIT]
                         if boundary.startswith('controller-') else None)
    def native(argv, **kwargs):
        if argv == ['systemctl', 'daemon-reload']:
            active.calls.append(argv)
            reloads.append(True)
            for props in (active.controller.properties, *active.native.values()):
                assert not (active.root / 'etc/systemd/system' / props['Id']).exists()
                props.update(LoadState='not-found', FragmentPath='', NeedDaemonReload='no',
                    ActiveState='inactive', SubState='dead', UnitFileState='')
                if 'MainPID' in props:
                    props.update(MainPID='0', ControlPID='0', ControlGroup='', User='', Group='')
            return 0, ''
        if argv[:2] == ['systemctl', 'stop'] and not inert:
            assert all(active.reopened()[0].devices[device].revoked for device in devices)
            assert active.closed == list(nodes)
        result = previous_run(argv, **kwargs)
        if not interrupted and (argv == uncertain_command or (boundary == 'account' and argv[0] == 'userdel')):
            interrupted.append(True)
            return 1, 'synthetic uncertainty after actual native/account effect'
        return result
    active.io._run = active.io.manager._run = native
    installer_type, constructed = retirement_io.RetirementIO, []
    def installed(io_type, **kwargs):
        assert kwargs == {'layout': 'deb'}
        io = io_type(root=active.root, root_owner=active.io.root_owner, layout='deb',
            run=active.io._run, lookup_user=active.io._lookup_user, lookup_group=active.io._lookup_group)
        constructed.append(True)
        return io
    def fresh_installer(**kwargs):
        return installed(installer_type, **kwargs)
    monkeypatch.setattr(retirement_io, 'RetirementIO', fresh_installer)
    preinst = packaged_preinst(tmp_path, monkeypatch, active.root, installed) if generated else None
    invoke = preinst.invoke if preinst else lambda: entry.main([])
    real_unlink, real_write, real_open = os.unlink, os.write, os.open
    real_sync, real_rename, real_retire = os.fsync, installer_type._rename_provision_entry, upgrade.retire
    final_syncs = []
    final_path = active.root / FINAL
    pending_path = final_path.with_name('.home-retirement.json.pending')
    published = boundary in ('final-renamed', 'final-parent-sync', 'final-return')
    revoke, revoke_calls = active.manager.revoke_device if not inert else None, []
    def interrupt_revoke(device, **kwargs):
        revoke_calls.append(device)
        if len(revoke_calls) == (1 if boundary == 'ceremony' else 2):
            interrupted.append(True)
            raise PairingError('authorization_expired')
        return revoke(device, **kwargs)
    def open_file(name, flags, *args, **kwargs):
        selected = (boundary == 'preparation' and name == INVENTORY.rsplit('/', 1)[1]
                    or boundary == 'before-publication' and name == '.home-retirement.json.pending')
        if selected and flags & os.O_CREAT and not interrupted:
            interrupted.append(True)
            raise OSError('synthetic interruption before original/final publication')
        return real_open(name, flags, *args, **kwargs)
    def unlink(name, *args, **kwargs):
        real_unlink(name, *args, **kwargs)
        if boundary == 'fragment' and not interrupted and name == 'happyranch-headscale.service':
            interrupted.append(True)
            raise OSError('synthetic interrupted original fragment removal')
    def write(fd, data):
        if not emitted:
            assert capsys.readouterr().out.startswith(entry.MESSAGE + '\n')
            emitted.append(True)
        if boundary == 'publication' and not interrupted and Path(os.readlink(f'/proc/self/fd/{fd}')) == (
                active.root / entry.STATE_DIRECTORY / '.home-retirement.json.pending'):
            interrupted.append(True)
            return real_write(fd, data[:11])
        return real_write(fd, data)
    def sync(fd):
        path = Path(os.readlink(f'/proc/self/fd/{fd}'))
        if path in (pending_path, final_path, final_path.parent):
            final_syncs.append(path)
        selected = (boundary == 'final-file-sync' and path == pending_path
            or boundary == 'final-stage-parent-sync' and path == final_path.parent and pending_path.exists()
            or boundary == 'final-parent-sync' and path == final_path.parent and final_path.exists())
        if selected and not interrupted:
            interrupted.append(True)
            raise OSError('synthetic interrupted final sync')
        real_sync(fd)
    def rename(parent, source, target, *, updating=False):
        selected = source == pending_path.name and target == final_path.name and not interrupted
        if selected and boundary == 'final-rename':
            interrupted.append(True)
            raise OSError('synthetic interruption before final rename')
        real_rename(parent, source, target, updating=updating)
        if selected and boundary == 'final-renamed':
            interrupted.append(True)
            raise OSError('synthetic uncertainty after actual final rename')
    def retire(io):
        result = real_retire(io)
        if boundary == 'final-return' and not interrupted:
            interrupted.append(True)
            raise OSError('synthetic lost return after actual final readback')
        return result
    # The existing lower fixture owns a fence for direct-owner tests. End that
    # test-side context so the shipping entry acquires the real directory flock.
    with ExitStack() as serving:
        if not inert:
            serving.enter_context(active.serving(monkeypatch))
        # Simulate root admission only here; real listener/store custody still
        # observes the test user's identity throughout the retirement.
        monkeypatch.setattr(entry, 'os', SimpleNamespace(**(vars(os) | {'geteuid': lambda: 0})))
        active.fence_context.__exit__(None, None, None)
        block_retired_imports(monkeypatch)
        with monkeypatch.context() as patch:
            patch.setattr(os, 'unlink', unlink)
            patch.setattr(os, 'write', write)
            patch.setattr(os, 'open', open_file)
            if boundary.startswith('final-'):
                patch.setattr(os, 'fsync', sync)
                patch.setattr(installer_type, '_rename_provision_entry', staticmethod(rename))
                patch.setattr(upgrade, 'retire', retire)
            if boundary in ('ceremony', 'first-device'):
                patch.setattr(active.manager, 'revoke_device', interrupt_revoke)
            assert invoke() == 1
        if boundary == 'failed-close':
            assert streams[0].cleanup_failed and active.reopened()[0].devices[devices[0]].revoked
            interrupted.append(True)
        assert interrupted and final_path.exists() == published
        published_inode = final_path.stat().st_ino if published else None
        # Fresh detection neither repairs the interrupted phase nor supplies
        # effect authority. Explicit recovery with incomplete original custody
        # must also preserve every remaining artifact/native/account effect.
        before, effects = active.snapshot(), active.effects()
        assert HomeRetirement(fresh_installer(layout='deb')).detect().state == ('retired' if published else 'migration pending')
        assert active.snapshot() == before and active.effects() == effects
        proof = active.root / (PIN if boundary == 'preparation' else INVENTORY)
        proof.chmod(0o644)
        damaged, effects, deleted = active.snapshot(), active.effects(), deletions(active.calls)
        assert invoke() == 1
        assert active.snapshot() == damaged and active.effects() == effects and deletions(active.calls) == deleted
        proof.chmod(0o600)
        if boundary in ('preparation', 'ceremony', 'first-device', 'failed-close'):
            saved, _ = active.reopened()
            assert sum(device.revoked for device in saved.devices.values()) == (
                1 if boundary in ('first-device', 'failed-close') else 0)
            if boundary in ('ceremony', 'first-device'):
                assert saved.invite_record is saved.pending_approval is saved.delivery is None
            assert active.effects() == [] and deletions(active.calls) == []
        if boundary == 'failed-close':
            from scripts.test_home_retirement_coordinator import NATIVE
            originals = {path: ((active.root / path).read_bytes(), (active.root / path).stat().st_ino)
                         for path in (PIN, INVENTORY, NATIVE)}
            effects, frames = active.effects(), len(active.frames)
            assert invoke() == 1
            # A retry may commit further ceremony burns/generations. It must
            # preserve denial, originals and the failed physical acknowledgement.
            assert active.reopened()[0].devices[devices[0]].revoked
            assert {path: ((active.root / path).read_bytes(), (active.root / path).stat().st_ino)
                    for path in originals} == originals
            assert active.effects() == effects == [] and deletions(active.calls) == []
            assert active.closed == ['device'] and streams[0].cleanup_failed
            assert len(active.frames) > frames  # Same original owner, never an empty replacement registry.
            serving.close()
            active.native_gone('absent')
        if not inert and boundary not in ('preparation', 'ceremony', 'first-device'):
            from runtime.remote_access.streams import StreamClosed
            with pytest.raises(StreamClosed):
                streams[0].receive()
        state['fault'] = ''
        if boundary.startswith('final-'):
            final_syncs.clear()
            with monkeypatch.context() as patch:
                patch.setattr(os, 'fsync', sync)
                assert invoke() == 0
            if published:
                assert final_path.stat().st_ino == published_inode
                assert final_syncs == [final_path, final_path.parent]
            else:
                assert pending_path in final_syncs and final_path.parent in final_syncs
        else:
            assert invoke() == 0
    produced = (active.root / FINAL).read_bytes()
    assert json.loads(produced) == {'account': 'absent', 'format': 'home-retirement@1', 'state': 'retired'}
    assert not state['user'] and not state['group']
    assert deletions(active.calls) == ['userdel', 'groupdel'] and reloads == [True]
    if uncertain_command is not None:
        assert active.calls.count(uncertain_command) == 1
    assert len(constructed) >= 4
    before, effects = active.snapshot(), active.effects()
    frames = list(getattr(active, 'frames', []))
    assert invoke() == invoke() == 0
    assert active.snapshot() == before and active.effects() == effects
    assert list(getattr(active, 'frames', [])) == frames
    assert deletions(active.calls) == ['userdel', 'groupdel'] and reloads == [True]
    if preinst:
        assert all('runtime.remote_access.home_retirement_upgrade' in names for names in preinst.children)
        # P02.d: preinst succeeded, but replacement has not begun. Aborting a
        # package attempt neither reverses denial nor replays retirement.
        child_count = len(preinst.children)
        assert preinst.invoke('abort-upgrade') == 0
        assert len(preinst.children) == child_count and active.snapshot() == before
        # P02.e: real extracted retained code is only partly copied; unavailable
        # old bootstrap refuses. These are layouts, not a simulated dpkg result.
        sources = Path('opt/happyranch-desktop/python/lib/python3.12/site-packages/runtime')
        retained = active.root / sources / 'remote_access/home_retirement.py'
        retained.parent.mkdir(parents=True, exist_ok=True)
        for directory in (retained.parent, *retained.parent.parents):
            if directory == active.root:
                break
            directory.chmod(0o755)
        shutil.copy2(preinst.payload / sources / 'remote_access/home_retirement.py', retained)
        replacing = preinst.python.with_suffix('.replacing')
        preinst.python.rename(replacing)
        partial = active.snapshot()
        assert invoke() == 1 and active.snapshot() == partial
        assert len(preinst.children) == child_count
        assert active.effects() == effects and deletions(active.calls) == ['userdel', 'groupdel']
        replacing.rename(preinst.python)
        shutil.copytree(preinst.payload / sources, active.root / sources, dirs_exist_ok=True)
        replacement = active.snapshot()
        assert invoke() == invoke() == 0 and active.snapshot() == replacement
        assert (active.root / FINAL).read_bytes() == produced
        assert active.effects() == effects and deletions(active.calls) == ['userdel', 'groupdel']
        assert list(getattr(active, 'frames', [])) == frames and reloads == [True]
    active.controller.file('etc/happyranch/invite-issuer.json', b'synthetic-foreign-reappearance')
    drift = active.snapshot()
    assert invoke() == 1
    assert active.snapshot() == drift and (active.root / FINAL).read_bytes() == produced
    assert active.effects() == effects and deletions(active.calls) == ['userdel', 'groupdel']
    output = capsys.readouterr().out
    assert 'synthetic-' not in output
