#!/usr/bin/env python3
"""THR298 bounded feature checks, outside pytest/retired units; never calls providers.

Run: uv run python scripts/verify_reasoning_effort.py --output <task-output-dir>
Only temporary synthetic registries and deterministic executables are used.
"""
from __future__ import annotations

import argparse
import atexit
import base64
import dataclasses
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def check_task_selection(root: Path, receipts: list[dict]) -> None:
    """Observe the shipping task prefix, stopping before executor construction."""
    import inspect
    from runtime.orchestrator import prompt_loader
    from runtime.orchestrator.agent_def import AgentDef, parse_agent_text, render_agent_text
    from runtime.orchestrator._paths import OrgPaths
    from runtime.orchestrator.orchestrator import (
        AgentTerminatedError, AgentUnavailableError, Orchestrator,
    )

    paths = OrgPaths(root=root / 'task-selection')
    paths.agents_dir.mkdir(parents=True)
    canonical = paths.agents_dir / 'worker.md'
    original = parse_agent_text(
        '---\nname: worker\nteam: engineering\nrole: worker\nexecutor: claude\n---\n\nSynthetic prompt.\n',
        expected_name='worker',
    )

    class ConstructionBoundary(Exception):
        pass

    observer = object.__new__(Orchestrator)
    observer._paths = paths
    observer._db = SimpleNamespace(get_task=lambda task_id: None)
    observed = []

    def stop_at_construction(provider: str) -> None:
        # Model/effort are not constructor parameters. Observe their actual
        # shipping frame values at this boundary, without executing setup,
        # admission, a provider or a lifecycle worker. No selection is mocked.
        frame = inspect.currentframe().f_back
        try:
            require(frame.f_code is Orchestrator._run_agent_impl.__code__, 'actual shipping task caller')
            observed.append((provider, frame.f_locals['model_name'], frame.f_locals['reasoning_effort']))
        finally:
            del frame
        raise ConstructionBoundary

    observer._build_executor = stop_at_construction
    real_load = prompt_loader.load_agent

    def invoke(
        expected: tuple[str, str | None, str | None], label: str,
        next_definition: AgentDef | None = None,
    ) -> None:
        reads = []
        old_hash = hashlib.sha256(canonical.read_bytes()).hexdigest()

        def load_then_save(read_paths: OrgPaths, name: str) -> AgentDef | None:
            definition = real_load(read_paths, name)
            reads.append(name)
            if next_definition is not None and len(reads) == 1:
                # Ordinary canonical file replacement AFTER the real snapshot
                # read. The returned definition stays the genuine old snapshot.
                replacement = canonical.with_suffix('.next')
                replacement.write_text(render_agent_text(next_definition))
                replacement.replace(canonical)
            return definition

        with patch.object(prompt_loader, 'load_agent', load_then_save):
            try:
                observer._run_agent_impl('TASK-synthetic', 'worker', 'inert prefix')
            except ConstructionBoundary:
                pass
            else:
                raise AssertionError('task must reach executor-construction boundary')
        require(observed[-1] == expected, f'{label}: observed {observed[-1]!r}, expected {expected!r}')
        require(reads == ['worker'], f'{label}: expected one canonical selection read, observed {reads!r}')
        current_hash = hashlib.sha256(canonical.read_bytes()).hexdigest()
        if next_definition is not None:
            require(old_hash != current_hash, 'controlled canonical replacement actually occurred')
            require(canonical.read_text() == render_agent_text(next_definition), 'new saved bytes remain canonical')
        receipts.append({'case': 'F-task-selection', 'kind': label, 'selected': observed[-1],
                         'selection_reads': len(reads), 'before_sha256': old_hash,
                         'after_sha256': current_hash, 'stopped_before_executor_construction': True})

    for model, effort in ((None, None), ('old-model', None), ('old-model', 'future_Tier-2')):
        canonical.write_text(render_agent_text(dataclasses.replace(original, model=model, reasoning_effort=effort)))
        invoke(('claude', model, effort), f'unset-selected:{model}:{effort}')

    for next_executor in ('claude', 'pi'):
        canonical.write_text(render_agent_text(dataclasses.replace(original, model='old-model', reasoning_effort='future_Tier-2')))
        saved_next = dataclasses.replace(original, executor=next_executor, model='new-model', reasoning_effort='low')
        invoke(('claude', 'old-model', 'future_Tier-2'), f'atomic-save:{next_executor}:old', saved_next)
        invoke((next_executor, 'new-model', 'low'), f'atomic-save:{next_executor}:next')

    canonical.unlink()
    for kind, expected_error, message in (
        ('missing', AgentUnavailableError, "agent 'worker' has no active definition"),
        ('pending-only', AgentUnavailableError, "agent 'worker' has no active definition"),
        ('terminated', AgentTerminatedError, "agent 'worker' has been terminated"),
    ):
        if kind == 'pending-only':
            paths.pending_agents_dir.mkdir(parents=True)
            (paths.pending_agents_dir / 'worker.md').write_text(render_agent_text(original))
        elif kind == 'terminated':
            (paths.pending_agents_dir / 'worker.md').unlink()
            archived = paths.agents_dir / '_terminated'
            archived.mkdir()
            (archived / 'worker.md').write_text(render_agent_text(original))
        boundary_count = len(observed)
        # The compatibility single-field callers retain the same refusals.
        for entry in ('task', 'executor-resolver', 'model-resolver'):
            try:
                if entry == 'task':
                    observer._run_agent_impl('TASK-synthetic', 'worker', 'refuse')
                elif entry == 'executor-resolver':
                    observer._resolve_executor_name('worker')
                else:
                    observer._resolve_model_name('worker')
            except expected_error as error:
                require(str(error) == message, f'{kind}/{entry}: exact refusal')
            else:
                raise AssertionError(f'{kind}/{entry}: expected {expected_error.__name__}')
            require(len(observed) == boundary_count, 'unavailable agent cannot reach construction')
            receipts.append({'case': 'F-task-refusal', 'kind': kind, 'entry': entry,
                             'error': expected_error.__name__, 'message': message,
                             'executor_construction_reached': False})


def check_wake_schedule_selection(root: Path, receipts: list[dict]) -> None:
    """Enter shipping prefixes; stop before real materialization or execution."""
    import asyncio
    import inspect
    from runtime.daemon import dream_runner, schedule_runner, wake_runner
    from runtime.models import ScheduleKind, ScheduleStatus, WorkHourStatus
    from runtime.orchestrator import prompt_loader
    from runtime.orchestrator.agent_def import AgentDef, parse_agent_text, render_agent_text
    from runtime.orchestrator._paths import OrgPaths
    from runtime.orchestrator.org_config import OrgConfig

    # BaseException avoids the shipping materialization-failure handler. This
    # is an observation stop, not a simulated materialization/executor result.
    class MaterializationBoundary(BaseException):
        pass

    original = parse_agent_text(
        '---\nname: worker\nteam: engineering\nrole: worker\nexecutor: claude\n---\n\nSynthetic prompt.\n',
        expected_name='worker',
    )
    real_load = prompt_loader.load_agent

    for owner, module, runner, initial_status, selection_action in (
        ('wake', wake_runner, wake_runner.run_wake, WorkHourStatus.PENDING, 'work_hour_reasoning_selection'),
        ('schedule', schedule_runner, schedule_runner.run_schedule, ScheduleStatus.FIRING, 'schedule_reasoning_selection'),
    ):
        paths = OrgPaths(root=root / f'{owner}-selection')
        paths.agents_dir.mkdir(parents=True)
        canonical = paths.agents_dir / 'worker.md'
        record_id = f'{owner}-synthetic'
        id_parameter = 'work_hour_id' if owner == 'wake' else 'schedule_id'

        def invoke(
            expected: tuple[str, str | None, str | None] | None, label: str,
            next_definition: AgentDef | None = None,
        ) -> None:
            reads = []
            updates = []
            audits = []
            observed = []
            old_hash = hashlib.sha256(canonical.read_bytes()).hexdigest() if canonical.exists() else None
            record = SimpleNamespace(id=record_id, agent_name='worker', status=initial_status,
                                     kind=ScheduleKind.ONE_SHOT)
            store = SimpleNamespace(get=lambda identifier: record,
                                    update=lambda identifier, **fields: updates.append({'id': identifier, **fields}))

            def insert_audit_log(task_id: str, agent: str, action: str, payload: dict) -> None:
                audits.append({'task_id': task_id, 'agent': agent, 'action': action, 'payload': dict(payload)})

            state = SimpleNamespace(root=paths.root, slug='synthetic', db=SimpleNamespace(
                work_hours=store, schedules=store, insert_audit_log=insert_audit_log))

            def load_then_save(read_paths: OrgPaths, name: str) -> AgentDef | None:
                require(read_paths.root == paths.root, 'canonical read stays in the synthetic org')
                definition = real_load(read_paths, name)
                reads.append(name)
                if next_definition is not None and len(reads) == 1:
                    replacement = canonical.with_suffix('.next')
                    replacement.write_text(render_agent_text(next_definition))
                    replacement.replace(canonical)
                return definition

            def stop_at_materialization(*args, **kwargs) -> None:
                frame = inspect.currentframe().f_back
                try:
                    require(frame.f_code is runner.__code__, f'{owner}: actual shipping prefix caller')
                    observed.append((kwargs['provider'], frame.f_locals['model_name'],
                                     frame.f_locals['reasoning_effort']))
                finally:
                    del frame
                raise MaterializationBoundary

            with (
                patch.object(module, 'load_agent', load_then_save),
                patch.object(dream_runner, 'load_agent', load_then_save),
                patch.object(module, 'load_org_config', return_value=OrgConfig()),
                patch.object(module, 'resolve_managed_skills_index', return_value=''),
                patch.object(module, 'materialize_workspace_skills', stop_at_materialization),
            ):
                try:
                    asyncio.run(runner(org_state=state, **{id_parameter: record_id}))
                except MaterializationBoundary:
                    require(expected is not None, f'{owner}/{label}: unavailable agent reached materialization')

            if expected is None:
                require(not observed, f'{owner}/{label}: missing active definition must refuse')
                require(updates[-1]['status'] == (WorkHourStatus.FAILED if owner == 'wake' else ScheduleStatus.FAILED),
                        f'{owner}/{label}: existing one-shot failure transition')
                require(updates[-1]['error'] == 'agent_not_found', f'{owner}/{label}: exact failure reason')
                require([row['action'] for row in audits] == (
                    ['work_hour_started', 'work_hour_failed'] if owner == 'wake' else ['schedule_fired', 'schedule_failed']),
                    f'{owner}/{label}: unchanged failure audit actions')
                require(audits[-1]['payload'] == {'reason': 'agent_not_found'}, f'{owner}/{label}: exact failure audit')
            else:
                require(observed == [expected], f'{owner}/{label}: observed {observed!r}, expected {[expected]!r}')
                selections = [row for row in audits if row['action'] == selection_action]
                wanted = [] if expected[2] is None else [{
                    'task_id': record_id, 'agent': 'worker', 'action': selection_action,
                    'payload': {'executor': expected[0], 'model': expected[1], 'reasoning_effort': expected[2]},
                }]
                require(selections == wanted, f'{owner}/{label}: selection audit {selections!r}, expected {wanted!r}')
                require([row['action'] for row in audits] == (
                    ['work_hour_started'] if owner == 'wake' else ['schedule_fired']) +
                    ([selection_action] if expected[2] is not None else []), f'{owner}/{label}: prefix audit actions')
                require(len(updates) == (1 if owner == 'wake' else 0), f'{owner}/{label}: no post-boundary state update')

            require(reads == ['worker'], f'{owner}/{label}: one genuine canonical selection read, observed {reads!r}')
            current_hash = hashlib.sha256(canonical.read_bytes()).hexdigest() if canonical.exists() else None
            if next_definition is not None:
                require(old_hash != current_hash, 'ordinary atomic save actually changed canonical bytes')
                require(canonical.read_text() == render_agent_text(next_definition), 'complete next definition remains saved')
            receipts.append({'case': f'F-{owner}-selection' if expected else f'F-{owner}-refusal',
                             'kind': label, 'selected': observed[0] if observed else None,
                             'audits': audits, 'updates': [{k: str(v) for k, v in row.items()} for row in updates],
                             'selection_reads': len(reads), 'before_sha256': old_hash,
                             'after_sha256': current_hash, 'stopped_before_real_materialization': bool(observed)})

        # First case exposes the actual old-head mixed tuple without injecting
        # a production defect. Ordinary atomic saves occur after the real read.
        for next_executor in ('pi', 'claude'):
            for old_effort, new_effort in (('future_Tier-2', 'low'), (None, 'future_Tier-2'), ('future_Tier-2', None)):
                canonical.write_text(render_agent_text(dataclasses.replace(
                    original, model='old-model', reasoning_effort=old_effort)))
                saved_next = dataclasses.replace(original, executor=next_executor, model='new-model', reasoning_effort=new_effort)
                label = f'atomic-save:{next_executor}:{old_effort}:{new_effort}'
                invoke(('claude', 'old-model', old_effort), f'{label}:old', saved_next)
                invoke((next_executor, 'new-model', new_effort), f'{label}:next')

        for executor, provider in (('claude', 'claude'), ('CoDeX', 'codex'), ('unregistered-synthetic', 'claude')):
            for model, effort in ((None, None), ('saved-model', None), (None, 'future_Tier-2'), ('saved-model', 'future_Tier-2')):
                canonical.write_text(render_agent_text(dataclasses.replace(
                    original, executor=executor, model=model, reasoning_effort=effort)))
                invoke((provider, model, effort), f'configured:{executor}:{model}:{effort}')

        canonical.unlink()
        invoke(None, 'missing')
        paths.pending_agents_dir.mkdir(parents=True)
        pending = paths.pending_agents_dir / 'worker.md'
        pending.write_text(render_agent_text(original))
        invoke(None, 'pending-only')
        pending.unlink()
        archived = paths.agents_dir / '_terminated'
        archived.mkdir()
        (archived / 'worker.md').write_text(render_agent_text(original))
        invoke(None, 'terminated')


def expected_builtin_argv(cli, stub, model, effort, resume, settings, paths, workspace):
    """Independent full literal argv oracle; never calls the shipping mapper."""
    expected = [str(stub)]
    if cli == 'codex':
        expected += ['exec'] + (['resume', resume] if resume else [])
    elif cli == 'opencode':
        expected += ['run']
    if model:
        selected = model.partition('#')[0] + '#' + effort if cli == 'opencode' and effort else model
        expected += ['--model' if cli in ('claude', 'pi') else '-m', selected]
    if effort and cli != 'opencode':
        expected += {'claude': ['--effort', effort], 'codex': ['-c', f'model_reasoning_effort="{effort}"'], 'pi': ['--thinking', effort]}[cli]
    if cli == 'claude':
        from runtime.orchestrator.workspace_adapters import allow_rules_for_agent
        expected += ['-p', '--permission-mode', settings.permission_mode, '--allowedTools', ' '.join(allow_rules_for_agent(paths, workspace.name, cli=True)), '--output-format', 'json']
        if resume:
            expected += ['--resume', resume]
    elif cli == 'codex':
        expected += (['-c', 'sandbox_mode="workspace-write"'] if resume else ['--sandbox', 'workspace-write'])
        expected += ['-c', 'sandbox_workspace_write.network_access=true', '--skip-git-repo-check', '--json', '-']
    elif cli == 'pi':
        expected += ['-p', '--mode', 'json']
        if resume:
            expected += ['--session', resume]
    else:
        if resume:
            expected += ['-s', resume]
        expected += ['--dir', str(workspace), '--format', 'json']
    return expected


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--ui-handoff', type=Path, help='Actual UI-saved canonical byte/digest records')
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    receipts = []
    atexit.register(lambda: (args.output / 'stub-prefix-receipts.json').write_text(json.dumps({'receipts': receipts, 'status': 'prefix-only; inspect command exit for completion'}, indent=2) + '\n'))
    with tempfile.TemporaryDirectory(prefix='effort-feature-') as temporary:
        root = Path(temporary)
        os.environ['HAPPYRANCH_DAEMON_HOME'] = str(root / 'daemon')
        os.environ['HAPPYRANCH_RUNTIME_ROOT'] = str(root / 'runtime')
        os.environ['HAPPYRANCH_EXECUTOR_LAUNCH_SPACING_SECONDS'] = '0'
        for key, child in (('HOME', 'home'), ('XDG_CONFIG_HOME', 'config'), ('XDG_CACHE_HOME', 'cache')):
            (root / child).mkdir()
            os.environ[key] = str(root / child)
        # Remove inherited provider credentials/config steering before product imports.
        for name in tuple(os.environ):
            if name.startswith(('ANTHROPIC_', 'OPENAI_', 'CODEX_', 'CLAUDE_', 'PI_', 'OPENCODE_', 'GEMINI_', 'GOOGLE_API_')):
                os.environ.pop(name)
        from runtime.config import Settings
        from runtime.orchestrator.executor_registry import build_executor, get_registry, ExecutorProfile
        from runtime.orchestrator.executor_binary_registry import set_binary
        from runtime.orchestrator.adapter_store import compute_sha256, get_adapter
        from runtime.orchestrator.custom_adapter_registry import register_custom_adapter, approve_adapter
        from runtime.orchestrator.agent_def import parse_agent_text, render_agent_text
        from runtime.workflows.authority import _agent_projection, _canonical_json

        from runtime.orchestrator._paths import OrgPaths
        paths = OrgPaths(root=root / "synthetic")
        paths.agents_dir.mkdir(parents=True)
        check_task_selection(root, receipts)
        check_wake_schedule_selection(root, receipts)
        settings = Settings(executor_launch_spacing_seconds=0)
        workspace = root / 'worker'
        workspace.mkdir()
        # Independent expected transport, not built by the shipping catalog/helper.
        values = {
            'claude': ['low', 'medium', 'high', 'xhigh', 'max'],
            'codex': ['none', 'minimal', 'low', 'medium', 'high', 'xhigh', 'max', 'ultra', 'persistent'],
            'pi': ['off', 'minimal', 'low', 'medium', 'high', 'xhigh', 'max'],
            'opencode': ['none', 'minimal', 'low', 'medium', 'high', 'xhigh', 'max'],
        }
        handoff = json.loads(args.ui_handoff.read_text()) if args.ui_handoff else []
        for row in handoff:
            raw = base64.b64decode(row['bytes_base64'], validate=True)
            require(hashlib.sha256(raw).hexdigest() == row['sha256'], 'UI canonical digest')
            copied = paths.agents_dir / ('ui-' + row['sha256'] + '.md')
            copied.write_bytes(raw)
            agent = parse_agent_text(copied.read_text(), expected_name='case_worker')
            projection = _agent_projection(agent, status='active')
            require(projection.get('reasoning_effort') == row['effort'] and (('reasoning_effort' in projection) == (row['effort'] is not None)), 'actual UI bytes conditional projection set/absence')
            receipts.append({'case': 'E1-UI-canonical-projection', 'sha256': row['sha256'], 'effort': row['effort'], 'present': 'reasoning_effort' in projection})
            require((agent.executor, agent.model, agent.reasoning_effort) ==
                    (row['cli'], row['model'], row['effort']), 'UI canonical parsed tuple')
        from runtime.adapters import model_effort_args
        from runtime.orchestrator.executor_registry import validate_reasoning_selection
        bad_strings = ['', 'a' * 33, '-high', '_high', '1high', 'h.igh', 'h/igh', 'h=igh', "h'igh", 'h"igh', 'h\\igh', 'h$(x)', 'h`x`', 'h{x}', 'hi gh', ' high', 'high ', '  ', 'hi\tgh', 'hi\ngh', 'hi\rgh', 'hi\x01gh', 'é', 'hi;gh', 'hi#gh', 'hi:gh']
        for cli, levels in values.items():
            capture = root / f'{cli}.json'
            stub = root / cli
            stub.write_text(f'#!{sys.executable}\nimport sys,json\nfrom pathlib import Path\nPath({str(capture)!r}).write_text(json.dumps({{"argv":sys.argv,"stdin":sys.stdin.read()}}))\nprint("{{}}")\n')
            stub.chmod(0o700)
            set_binary(cli, str(stub))
            executor = build_executor(cli, settings, paths)
            levels = [*levels, 'future_Tier-2', 'HIGH', 'A', 'A' + 'a' * 31, 'none']
            for fallback in (False, True):
                if fallback:
                    executor._adapter = None  # Existing compatibility fallback, not a new shipping seam.
                for resume in (None, 'stub-resume'):
                    compositions = [('provider/org/model', 'future_Tier-2'), ('provider/model#future_Tier-2', 'future_Tier-2'), ('provider/model#high', None)] if cli == 'opencode' else [('provider/model:high', None), ('provider/model:high', 'high'), ('provider/model:ordinary', 'future_Tier-2'), ('provider/model:future_Tier-2', 'future_Tier-2')] if cli == 'pi' else []
                    for model, effort in [(None, None), ('provider/model', None), *[('provider/model', v) for v in levels], *[(None, v) for v in levels if cli != 'opencode'], *compositions]:
                        capture.unlink(missing_ok=True)
                        result = executor.run(workspace, 'capture only', session_id='sess-effort-stub', model=model, reasoning_effort=effort, resume_session_id=resume, timeout_seconds=5)
                        require(result.success, f'{cli}: stub process failed: {result.error}')
                        observed = json.loads(capture.read_text())
                        expected = expected_builtin_argv(cli, stub, model, effort, resume, settings, paths, workspace)
                        require(observed['argv'] == expected, f'{cli}: argv mismatch: {observed["argv"]} != {expected}')
                        require(observed['stdin'].endswith('capture only'), 'prompt must travel on stdin')
                        spec = executor.build_launch_spec(workspace=workspace, prompt='capture only', model=model, reasoning_effort=effort, resume_session_id=resume)
                        require(list(spec.argv) == expected, 'contained launch must carry identical argv')
                        receipts.append({'case': 'A/B/F', 'cli': cli, 'fallback': fallback, 'resume': resume, 'model': model, 'effort': effort, 'argv': observed['argv'], 'stdin': observed['stdin'], 'returncode': result.returncode, 'stdout': result.stdout_tail, 'stderr': result.stderr_tail})
                refused_pairs = [(None, 'future_Tier-2'), ('provider/model#other', 'future_Tier-2'), ('provider/model#future_tier-2', 'future_Tier-2'), ('provider//model', 'high'), ('provider/model#', 'high'), ('provider/model#high#high', 'high'), ('provider model/id', 'high')] if cli == 'opencode' else [('provider/model:high', 'future_Tier-2')] if cli == 'pi' else []
                for model, effort in refused_pairs:
                    for resume in (None, 'stub-resume'):
                        capture.unlink(missing_ok=True)
                        try:
                            executor.run(workspace, 'refuse', model=model, reasoning_effort=effort, resume_session_id=resume, timeout_seconds=5)
                        except ValueError as error:
                            receipts.append({'case': 'B3/B4-run-refusal', 'cli': cli, 'model': model, 'effort': effort, 'resume': resume, 'fallback': fallback, 'error': str(error)})
                        else:
                            raise AssertionError('composition must refuse before builtin process')
                        require(not capture.exists(), 'fresh capture: composition no spawn')
                for row in (r for r in handoff if r['cli'] == cli):
                    for resume in (None, 'stub-resume'):
                        capture.unlink(missing_ok=True)
                        result = executor.run(workspace, 'capture only', model=row['model'], reasoning_effort=row['effort'], resume_session_id=resume, timeout_seconds=5)
                        require(result.success and capture.exists(), 'UI byte bridge actual process')
                        observed = json.loads(capture.read_text())
                        expected = expected_builtin_argv(cli, stub, row['model'], row['effort'], resume, settings, paths, workspace)
                        require(observed['argv'] == expected, 'UI byte bridge full literal argv')
                        receipts.append({'case': 'A/B-UI-process-bridge', 'cli': cli, 'canonical_sha256': row['sha256'], 'source_case': row['source_case'], 'fallback': fallback, 'resume': resume, 'argv': observed['argv'], 'stdin': observed['stdin'], 'returncode': result.returncode, 'stdout': result.stdout_tail, 'stderr': result.stderr_tail})
                for bad in [*bad_strings, 'hi\x00gh', True, 7, 1.5, [], {}]:
                    for seam, call in (
                        ('selection-validator', lambda: validate_reasoning_selection(cli, 'provider/model', bad)),
                        ('model-effort-args', lambda: model_effort_args(cli, 'provider/model', ['-m', '{model}'], bad)),
                        ('launch-spec-construction', lambda: executor.build_launch_spec(workspace=workspace, prompt='refuse', model='provider/model', reasoning_effort=bad)),
                    ):
                        try:
                            call()
                        except ValueError as error:
                            receipts.append({'case': 'B2-direct', 'cli': cli, 'fallback': fallback, 'seam': seam, 'input': bad, 'error': str(error), 'process_evidence': False})
                        else:
                            raise AssertionError(f'{cli}/{seam}: invalid value accepted {bad!r}')
                    if isinstance(bad, str) and '\x00' not in bad:
                        capture.unlink(missing_ok=True)
                        try:
                            executor.run(workspace, 'refuse', model='provider/model', reasoning_effort=bad, timeout_seconds=5)
                        except ValueError:
                            pass
                        else:
                            raise AssertionError('invalid token run must refuse')
                        require(not capture.exists(), 'fresh capture: invalid run never spawns stub')
                        receipts.append({'case': 'B2-run-no-spawn', 'cli': cli, 'fallback': fallback, 'input': bad, 'fresh_capture_absent': True})
            # Embedded model equal / conflicting / unset/default cases at process seam.
            separator = '#' if cli == 'opencode' else ':' if cli == 'pi' else None
            if separator:
                embedded = 'provider/model' + separator + 'high'
                for effort in (None, 'high'):
                    require(executor.run(workspace, 'capture only', model=embedded, reasoning_effort=effort, timeout_seconds=5).success, 'embedded variant equal/unset')
                    observed = json.loads(capture.read_text())
                    require(embedded in observed['argv'], 'embedded string preserved')
                    receipts.append({'case': 'B-embedded', 'cli': cli, 'effort': effort, 'argv': observed['argv']})
                for model, effort in [(embedded, 'low')] + ([(None, 'high')] if cli == 'opencode' else []):
                    capture.unlink(missing_ok=True)
                    try:
                        executor.run(workspace, 'refuse', model=model, reasoning_effort=effort, timeout_seconds=5)
                    except ValueError:
                        pass
                    else:
                        raise AssertionError('conflicting/default model must refuse')
                    require(not capture.exists(), 'fresh capture: refusal before process boundary')

            if cli == 'opencode':
                for model in ('provider//model', '/model', 'provider/', 'provider model/id', 'p/m#', 'p/m#high#high', 'p/m\t', 'p/m\x00', 'p/m\x80'):
                    capture.unlink(missing_ok=True)
                    try:
                        model_effort_args(cli, model, ['-m', '{model}'], 'high')
                    except ValueError as error:
                        receipts.append({'case': 'B3-composition', 'model': model, 'error': str(error), 'seam': 'direct-helper'})
                    else:
                        raise AssertionError('ambiguous OpenCode model accepted')
                    require(model_effort_args(cli, model, ['-m', '{model}'], None) == ['-m', model], 'null effort leaves original transport')
                require(model_effort_args(cli, 'p/models/path#future_Tier-2', ['-m', '{model}'], 'future_Tier-2') == ['-m', 'p/models/path#future_Tier-2'], 'additional nonempty path segments and equal variant')
            if cli == 'pi':
                require(model_effort_args(cli, 'p/model:future_Tier-2', ['--model', '{model}'], 'high') == ['--model', 'p/model:future_Tier-2', '--thinking', 'high'], 'arbitrary safe suffix is not native Pi thinking syntax')

        options = {'version': 2, 'model_arg_template': ['--model', '{model}'], 'effort_arg_template': ['--effort', '{effort}'], 'reasoning_effort_values': ['low', 'high']}
        for kind in ('legacy', 'opted', 'model-only'):
            opted = kind != 'legacy'
            supports_effort = kind == 'opted'
            declaration = options if supports_effort else {**options, 'effort_arg_template': None, 'reasoning_effort_values': []} if opted else None
            capture = root / ('custom-' + kind + '.jsonl')
            child_capture = root / ('child-' + kind + '.json')
            child = root / ('child-' + kind)
            child.write_text(f'#!{sys.executable}\nimport sys,json\nfrom pathlib import Path\nPath({str(child_capture)!r}).write_text(json.dumps(sys.argv))\n')
            child.chmod(0o700)
            adapter = root / ('adapter-' + kind)
            adapter.write_text(f'''#!{sys.executable}
import sys,json,subprocess
from pathlib import Path
value=json.load(sys.stdin)
with Path({str(capture)!r}).open('a') as stream:stream.write(json.dumps(value)+'\\n')
argv=[{str(child)!r}]
model=value['model']
if model:argv += [p.replace('{{model}}',model['model_id']) for p in model['model_arg_template'] or []]
effort=value.get('reasoning_effort')
if effort:argv += [p.replace('{{effort}}',effort['reasoning_effort']) for p in effort['effort_arg_template']]
subprocess.run(argv,check=True)
metadata={{'adapter':{adapter.name!r},'adapter_version':'1.0.0','contract_version':1}}
if value['contract_version']==2 and 'without-input-ack' not in value['prompt']:metadata['input_contract_version']=2
print(json.dumps({{'success':True,'duration_seconds':0,'session_id':value['invocation']['invocation_id'],'returncode':0,'stdout_tail':'','stderr_tail':'','adapter_metadata':metadata}}))
''')
            adapter.chmod(0o700)
            entry = register_custom_adapter(str(adapter), '1.0.0', [], dependency_manifest_version=1, dependencies=[{'executable': str(child), 'sha256': compute_sha256(str(child))}], input_options=declaration)
            require(get_adapter(entry.id).input_options == declaration, 'normal registration/store readback')
            approval = dict(adapter_id=entry.id, executable=entry.executable, executable_hash=entry.executable_hash, version=entry.version, capabilities=entry.capabilities, contract_version=entry.contract_version, workspace_adapter=entry.workspace_adapter, dependency_manifest_version=1, dependencies=entry.dependencies, input_options=entry.input_options)
            if opted:
                mismatched = {**approval, 'input_options': None}
                try:
                    approve_adapter(**mismatched)
                except ValueError:
                    pass
                else:
                    raise AssertionError('approval must bind exact input declaration')
                require(get_adapter(entry.id).status == 'pending', 'mismatch no approval mutation')
            approve_adapter(**approval)
            profile = {'opted': 'opted_custom', 'legacy': 'legacy_custom', 'model-only': 'model_only_custom'}[kind]
            get_registry().register_custom_profile(ExecutorProfile(name=profile, kind='custom', workspace_adapter_id='pi', command_adapter_id='custom-adapter:' + entry.id))
            executor = build_executor(profile, settings)
            executor.set_invocation_context(agent='worker', org='synthetic', invocation_kind='task', task_id='TASK-stub')
            selections = [(None, None), (None, 'high'), ('actual-model', None), ('actual-model', 'high'), ('actual-model', 'low')] if supports_effort else [(None, None), ('actual-model', None)]
            ui_rows = [r for r in handoff if r['cli'] == profile]
            for model, effort in selections + [(r['model'], r['effort']) for r in ui_rows]:
                result = executor.run(workspace, 'stub only', session_id='sess-custom', model=model, reasoning_effort=effort, timeout_seconds=5)
                require(result.success, f'custom runtime: {result.error}')
                wire = json.loads(capture.read_text().splitlines()[-1])
                require(wire['contract_version'] == (2 if opted else 1), 'input version')
                if opted:
                    require(wire['model'] == ({'model_id': model, 'model_arg_template': ['--model', '{model}']} if model else None), 'actual selected model forwarding')
                    require(wire['reasoning_effort'] == ({'reasoning_effort': effort, 'effort_arg_template': ['--effort', '{effort}']} if effort else None), 'effort forwarding/default')
                else:
                    require(wire['model'] is None and 'reasoning_effort' not in wire, 'legacy wire model:null, no added effort member')
                expected_child = [str(child)] + (['--model', model] if opted and model else []) + (['--effort', effort] if effort else [])
                require(json.loads(child_capture.read_text()) == expected_child, 'actual child argv mapping')
                receipts.append({'case': 'D', 'kind': kind, 'opted': opted, 'wire': wire, 'child_argv': expected_child, 'ui_canonical_sha256': [r['sha256'] for r in ui_rows if (r['model'], r['effort']) == (model, effort)]})
            if supports_effort:
                result = executor.run(workspace, 'without-input-ack', session_id='sess-no-ack', model='actual-model', reasoning_effort='high', timeout_seconds=5)
                require(not result.success and result.failure_category == 'post_launch_contract' and result.provider_launched, 'input2 missing ack must fail at shipping runtime response boundary')
                receipts.append({'case':'D-ack', 'success':result.success, 'failure_category':result.failure_category, 'provider_launched':result.provider_launched})
            # Unsupported efforts cannot spawn even through direct executor input.
            before = capture.read_bytes()
            child_capture.unlink(missing_ok=True)
            try:
                executor.run(workspace, 'refuse', reasoning_effort='unsupported', timeout_seconds=5)
            except ValueError:
                pass
            else:
                raise AssertionError('unsupported custom effort must refuse')
            require(capture.read_bytes() == before and not child_capture.exists(), 'custom append-only capture + fresh child no-spawn')

        legacy_text = '---\nname: worker\nteam: engineering\nrole: worker\nexecutor: claude\n---\n\nSynthetic prompt.\n'
        agent = parse_agent_text(legacy_text, expected_name='worker')
        old = {'allow_rules': [], 'description_digest': hashlib.sha256(b'').hexdigest(), 'executor': 'claude', 'model': None, 'name': 'worker', 'repos': {}, 'role': 'worker', 'status': 'active', 'system_prompt_digest': hashlib.sha256(b'Synthetic prompt.\n').hexdigest(), 'team': 'engineering'}
        frozen = _canonical_json(old)
        require(_canonical_json(_agent_projection(agent, status='active')) == frozen, 'absent projection byte equality')
        selected = dataclasses.replace(agent, reasoning_effort='future_Tier-2')
        reloaded = parse_agent_text(render_agent_text(selected), expected_name='worker')
        require(reloaded.reasoning_effort == 'future_Tier-2' and _agent_projection(reloaded, status='active')['reasoning_effort'] == 'future_Tier-2', 'set inclusion/reload')
        cleared = parse_agent_text(render_agent_text(dataclasses.replace(reloaded, reasoning_effort=None)), expected_name='worker')
        require(_canonical_json(_agent_projection(cleared, status='active')) == frozen, 'clear/restores old bytes')
        require(frozen == _canonical_json(old), 'retained historical bytes not modified')
        # Actual one-shot whole-definition importer, without importing retired tests.
        from runtime.daemon.agent_config import migrate_agent_yaml_to_frontmatter
        import yaml
        for label, executor_name, model_name, expected_effort in (
            ('unrelated-import', 'claude', 'new-model', 'future_Tier-2'),
            ('cli-switch-import', 'pi', 'new-model', None),
        ):
            agent_workspace = paths.workspaces_dir / 'worker'
            agent_workspace.mkdir(parents=True, exist_ok=True)
            sentinel = agent_workspace / '.agent_yaml_consumed'
            sentinel.unlink(missing_ok=True)
            (paths.agents_dir / 'worker.md').write_text(render_agent_text(selected))
            (agent_workspace / 'agent.yaml').write_text(yaml.safe_dump({'executor':executor_name, 'model':model_name, 'repos':{'local':'/synthetic'}}))
            result = migrate_agent_yaml_to_frontmatter(paths)
            imported = parse_agent_text((paths.agents_dir / 'worker.md').read_text(), expected_name='worker')
            require(imported.reasoning_effort == expected_effort and sentinel.exists(), label + ' canonical preservation')
            receipts.append({'case':'E-import', 'kind':label, 'result':result, 'effort':expected_effort})
        for effort in (True, 7, 1.5, [], {}, '', '  '):
            text = '---\n' + yaml.safe_dump({'name': 'worker', 'team': 'engineering', 'role': 'worker', 'executor': 'claude', 'reasoning_effort': effort}) + '---\n\nSynthetic prompt.\n'
            try:
                parse_agent_text(text, expected_name='worker')
            except ValueError as error:
                receipts.append({'case': 'B2-canonical-parser', 'input': effort, 'error': str(error)})
            else:
                raise AssertionError('canonical parser type/empty must refuse')
        malformed = dataclasses.replace(agent, reasoning_effort='h=igh')
        raw = render_agent_text(malformed).encode()
        require(parse_agent_text(raw.decode(), expected_name='worker').reasoning_effort == 'h=igh', 'legacy readable malformed string is not silently normalized')
        (paths.agents_dir / 'worker.md').write_bytes(raw)
        sentinel.unlink(missing_ok=True)
        (agent_workspace / 'agent.yaml').write_text(yaml.safe_dump({'executor': 'claude', 'model': 'changed-model'}))
        outcome = migrate_agent_yaml_to_frontmatter(paths)
        require((paths.agents_dir / 'worker.md').read_bytes() == raw and not sentinel.exists(), 'importer invalid saved selection refuses before replacement')
        receipts.append({'case': 'B2-importer-refusal', 'outcome': outcome, 'canonical_before_after_sha256': hashlib.sha256(raw).hexdigest()})
        receipts.append({'case': 'E', 'absent_sha256': hashlib.sha256(frozen).hexdigest(), 'set': _agent_projection(reloaded, status='active'), 'clear_equality': True})
    (args.output / 'stub-receipts.json').write_text(json.dumps(receipts, indent=2) + '\n')
    print(f'PASS {len(receipts)} bounded process/projection receipts; no native provider/lifecycle acceptance claimed')


if __name__ == '__main__':
    main()
