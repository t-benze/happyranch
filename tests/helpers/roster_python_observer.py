"""Test-side C8/C9 source-frame observer for the bounded roster utility.

Authoring only until reviewed capability/venue release. This does not replace
production functions, validators, SQL or callbacks. Pair it with the accepted
syscall observer; frame evidence alone is never no-write/commit proof.
"""
from __future__ import annotations

import argparse
import dis
import hashlib
import json
import marshal
import os
from pathlib import Path
import runpy
import signal
import stat
import subprocess
import sys
import threading
import types


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--source-sha', required=True)
    parser.add_argument('--event-map', type=Path, required=True)
    parser.add_argument('--receipt', type=Path, required=True)
    parser.add_argument('--script', type=Path, required=True)
    parser.add_argument('--boundary', default='observe-only')
    parser.add_argument('arguments', nargs=argparse.REMAINDER)
    args = parser.parse_args()
    arguments = args.arguments[1:] if args.arguments[:1] == ['--'] else args.arguments
    subject = argparse.ArgumentParser(add_help=False)
    operation = subject.add_mutually_exclusive_group(required=True)
    for action in ('check', 'apply', 'recover'):
        operation.add_argument('--' + action, action='store_true')
    subject.add_argument('--runtime-root', type=Path, required=True)
    subject.add_argument('--org', required=True)
    subject.add_argument('--plan', type=Path)
    subject.add_argument('--manifest', type=Path)
    subject.add_argument('--expected-digest')
    subject.add_argument('--operation-id')
    subject.add_argument('--direction', choices=('complete', 'compensate'))
    selected = subject.parse_args(arguments)
    runtime = selected.runtime_root.resolve(strict=True)
    if not selected.runtime_root.is_absolute() or selected.runtime_root.is_symlink():
        raise ValueError('absolute_owned_fixture_runtime_required')
    protected_roots = [runtime]
    if selected.manifest is not None:
        checked = json.loads(selected.manifest.read_bytes())
        if hashlib.sha256(selected.manifest.read_bytes()).hexdigest() != selected.expected_digest:
            raise ValueError('exact_observed_manifest_digest_required')
        if (checked['kind'] != 'THR296-checked-manifest-v1'
                or checked['runtime_root'] != str(runtime) or checked['org'] != selected.org
                or checked['source_sha'] != args.source_sha):
            raise ValueError('real_checked_fixture_binding_required')
        protected_roots.append(Path(checked['canonical_store_root']).resolve(strict=True))
    source = args.source.resolve(strict=True)
    script = args.script.resolve(strict=True)
    if sys.version_info[:2] != (3, 14) or not args.source.is_absolute() or args.source.is_symlink():
        raise ValueError('pinned_python314_source_required')
    if script != source / 'scripts/migrate_human_team_roster.py' or args.script.is_symlink():
        raise ValueError('only_bounded_roster_utility_is_observable')
    if subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=source, text=True).strip() != args.source_sha:
        raise ValueError('observer_source_sha_mismatch')
    if subprocess.check_output(['git', 'status', '--porcelain'], cwd=source).strip():
        raise ValueError('clean_committed_observer_source_required')
    event_map = json.loads(args.event_map.read_bytes())
    if set(event_map) not in ({'source_sha', 'sites'}, {'source_sha', 'sites', 'cuts'}) or event_map['source_sha'] != args.source_sha:
        raise ValueError('source_bound_event_map_required')
    compiled = {}
    sites = {}
    allowed_files = {
        'scripts/migrate_human_team_roster.py',
        'runtime/infrastructure/db/sessions.py',
        'runtime/orchestrator/teams.py',
        'runtime/orchestrator/context_builder.py',
        'runtime/orchestrator/workspace_adapters.py',
        'runtime/workflows/authority.py',
        'runtime/workflows/profile_coordinator.py',
        'runtime/skills/canonical_store.py',
    }

    def code_members(code: types.CodeType) -> list[types.CodeType]:
        values = [code]
        for value in code.co_consts:
            if isinstance(value, types.CodeType):
                values.extend(code_members(value))
        return values

    for site in event_map['sites']:
        if set(site) != {'file', 'sha256', 'qualname', 'events'}:
            raise ValueError('closed_observer_site_required')
        rel = Path(site['file'])
        path = source / rel
        if (site['file'] not in allowed_files or rel.is_absolute() or '..' in rel.parts
                or not path.resolve(strict=True).is_relative_to(source)):
            raise ValueError('observer_site_outside_pinned_source')
        data = path.read_bytes()
        if hashlib.sha256(data).hexdigest() != site['sha256']:
            raise ValueError('observer_site_source_hash_mismatch')
        if not site['events'] or any(event not in ('call', 'return') for event in site['events']):
            raise ValueError('only_genuine_source_frame_events_supported')
        if path not in compiled:
            compiled[path] = code_members(compile(data, str(path), 'exec', dont_inherit=True, optimize=sys.flags.optimize))
        matches = [code for code in compiled[path] if code.co_qualname == site['qualname']]
        if len(matches) != 1:
            raise ValueError('ambiguous_or_missing_compiled_observer_site')
        key = (str(path), site['qualname'])
        if key in sites:
            raise ValueError('duplicate_observer_site')
        sites[key] = (hashlib.sha256(marshal.dumps(matches[0])).hexdigest(), site['events'])
    cuts = event_map.get('cuts', {})
    for name, cut in cuts.items():
        if (set(cut) != {'file', 'qualname', 'event', 'agent'}
                or (str(source / cut['file']), cut['qualname']) not in sites
                or cut['event'] not in ('call', 'return')
                or cut['agent'] not in (None, 'consultant_head', 'consultant_codex')):
            raise ValueError('invalid_finite_frame_cut')
    if args.boundary != 'observe-only' and args.boundary not in cuts:
        raise ValueError('frame_boundary_not_in_source_map')
    reset_sites = {
        ('runtime/infrastructure/db/sessions.py', 'SessionsMixin.reset_thread_sessions_for_agent'),
        ('runtime/infrastructure/db/sessions.py', 'SessionsMixin._reset_thread_sessions_for_agent_uncommitted'),
    }
    if not reset_sites.issubset({(site['file'], site['qualname']) for site in event_map['sites']}):
        raise ValueError('both_native_reset_entries_must_be_observed')
    if any('call' not in site['events'] for site in event_map['sites']
           if (site['file'], site['qualname']) in reset_sites):
        raise ValueError('native_reset_call_events_required_for_invocation_proof')
    directory = args.receipt.parent.resolve(strict=True)
    if (not args.receipt.is_absolute() or directory.stat().st_uid != os.getuid()
            or stat.S_IMODE(directory.stat().st_mode) != 0o700
            or directory.is_relative_to(source)
            or any(directory.is_relative_to(root) for root in protected_roots)):
        raise ValueError('external_private_observer_receipt_directory_required')
    fd = os.open(args.receipt, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    gate = threading.Lock()
    loss = []
    sequence = 0
    closing = False
    selected_fault = False

    def emit(value: dict) -> None:
        nonlocal sequence
        with gate:
            sequence += 1
            data = (json.dumps({'sequence': sequence, **value}, sort_keys=True) + '\n').encode()
            if os.write(fd, data) != len(data):
                raise OSError('observer_receipt_short_write')
            os.fsync(fd)

    def observe(frame, event, arg):
        nonlocal selected_fault
        if event == 'c_call' and not closing and arg in (sys.setprofile, threading.setprofile):
            loss.append('profile_replacement_attempt')
        key = (frame.f_code.co_filename, frame.f_code.co_qualname)
        if key not in sites or event not in sites[key][1]:
            return
        try:
            expected, _ = sites[key]
            if hashlib.sha256(marshal.dumps(frame.f_code)).hexdigest() != expected:
                loss.append('loaded_code_mismatch')
                return
            if sys.getprofile() is not observe:
                loss.append('profile_not_installed')
                return
            agent = frame.f_locals.get('agent_name')
            if agent is None:
                agent = frame.f_locals.get('agent')
            # Registry/materializer frames can contain other fixture agents;
            # retain the actual identity, without interpreting it as a reset.
            normal_return = event != 'return' or dis.opname[frame.f_code.co_code[frame.f_lasti]].startswith('RETURN')
            emit(dict(kind='frame', pid=os.getpid(), thread=threading.get_ident(),
                      file=str(Path(key[0]).relative_to(source)), qualname=key[1],
                      code_sha256=expected, event=event, agent=agent,
                      normal_return=normal_return))
            if args.boundary != 'observe-only':
                cut = cuts[args.boundary]
                if (key == (str(source / cut['file']), cut['qualname'])
                        and event == cut['event'] and normal_return
                        and (cut['agent'] is None or agent == cut['agent'])):
                    selected_fault = True
                    emit(dict(kind='selected-fault', boundary=args.boundary, pid=os.getpid(),
                              file=cut['file'], qualname=cut['qualname'], event=event,
                              agent=agent, code_sha256=expected))
                    os.kill(os.getpid(), signal.SIGKILL)
        except BaseException:
            # Preserve product execution, but never bless partial observation.
            loss.append('frame_capture_failed')

    emit(dict(kind='begin', source_sha=args.source_sha, script_sha256=hashlib.sha256(script.read_bytes()).hexdigest(),
              effective_python=sys.executable, python_version=sys.version,
              event_map_sha256=hashlib.sha256(args.event_map.read_bytes()).hexdigest()))
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(source))
    sys.argv = [str(script), *arguments]
    # Before runpy/imports, every subsequently created Python thread inherits
    # the observer. Syscalls/descendant tracking require the outer observer.
    threading.setprofile(observe)
    sys.setprofile(observe)
    target_exit = 1
    try:
        try:
            runpy.run_path(str(script), run_name='__main__')
        except SystemExit as exc:
            target_exit = exc.code if type(exc.code) is int else 0 if exc.code is None else 1
        else:
            target_exit = 0
    finally:
        closing = True
        if sys.getprofile() is not observe or threading.getprofile() is not observe:
            loss.append('profile_lost_before_terminal')
        sys.setprofile(None)
        threading.setprofile(None)
        if any(thread.is_alive() and thread is not threading.current_thread() for thread in threading.enumerate()):
            loss.append('unjoined_python_thread')
        if args.boundary != 'observe-only' and not selected_fault:
            loss.append('selected_boundary_not_reached')
        emit(dict(kind='terminal', target_exit=target_exit, complete=not loss,
                  loss=sorted(set(loss)), syscall_no_write_proof=False))
        os.close(fd)
    return target_exit if not loss else 86


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as exc:
        print(f'frame observation unavailable: {exc}', file=sys.stderr)
        raise SystemExit(86)
