#!/usr/bin/env python3
"""THR298 bounded feature checks, outside pytest/retired units; never calls providers.

Run: uv run python scripts/verify_reasoning_effort.py --output <task-output-dir>
Only temporary synthetic registries and deterministic executables are used.
"""
from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    receipts = []
    with tempfile.TemporaryDirectory(prefix='effort-feature-') as temporary:
        root = Path(temporary)
        os.environ['HAPPYRANCH_DAEMON_HOME'] = str(root / 'daemon')
        os.environ['HAPPYRANCH_RUNTIME_ROOT'] = str(root / 'runtime')
        os.environ['HAPPYRANCH_EXECUTOR_LAUNCH_SPACING_SECONDS'] = '0'
        # Remove inherited provider credentials/config steering before product imports.
        for name in tuple(os.environ):
            if name.startswith(('ANTHROPIC_', 'OPENAI_', 'CODEX_', 'CLAUDE_', 'PI_')):
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
        for cli, levels in values.items():
            capture = root / f'{cli}.json'
            stub = root / cli
            stub.write_text(f'#!{sys.executable}\nimport sys,json\nfrom pathlib import Path\nPath({str(capture)!r}).write_text(json.dumps({{"argv":sys.argv,"stdin":sys.stdin.read()}}))\nprint("{{}}")\n')
            stub.chmod(0o700)
            set_binary(cli, str(stub))
            executor = build_executor(cli, settings, paths)
            for fallback in (False, True):
                if fallback:
                    executor._adapter = None  # Existing compatibility fallback, not a new shipping seam.
                for resume in (None, 'stub-resume'):
                    for model, effort in [(None, None), ('provider/model', None), *[('provider/model', v) for v in levels], *[(None, v) for v in levels if cli != 'opencode']]:
                        result = executor.run(workspace, 'capture only', session_id='sess-effort-stub', model=model, reasoning_effort=effort, resume_session_id=resume, timeout_seconds=5)
                        require(result.success, f'{cli}: stub process failed: {result.error}')
                        observed = json.loads(capture.read_text())
                        expected = [str(stub)]
                        if cli == 'codex':
                            expected += ['exec'] + (['resume', resume] if resume else [])
                        elif cli == 'opencode':
                            expected += ['run']
                        if model:
                            selected = model + ('#' + effort if cli == 'opencode' and effort else '')
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
                        require(observed['argv'] == expected, f'{cli}: argv mismatch: {observed["argv"]} != {expected}')
                        require(observed['stdin'].endswith('capture only'), 'prompt must travel on stdin')
                        spec = executor.build_launch_spec(workspace=workspace, prompt='capture only', model=model, reasoning_effort=effort, resume_session_id=resume)
                        require(list(spec.argv) == expected, 'contained launch must carry identical argv')
                        receipts.append({'case': 'A/B/F', 'cli': cli, 'fallback': fallback, 'resume': resume, 'model': model, 'effort': effort, 'argv': observed['argv']})
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
                    before = capture.read_bytes()
                    try:
                        executor.run(workspace, 'refuse', model=model, reasoning_effort=effort, timeout_seconds=5)
                    except ValueError:
                        pass
                    else:
                        raise AssertionError('conflicting/default model must refuse')
                    require(capture.read_bytes() == before, 'refusal before process boundary')

        options = {'version': 2, 'model_arg_template': ['--model', '{model}'], 'effort_arg_template': ['--effort', '{effort}'], 'reasoning_effort_values': ['low', 'high']}
        for opted in (False, True):
            capture = root / ('custom-v2.jsonl' if opted else 'custom-v1.jsonl')
            child_capture = root / ('child-v2.json' if opted else 'child-v1.json')
            child = root / ('child-v2' if opted else 'child-v1')
            child.write_text(f'#!{sys.executable}\nimport sys,json\nfrom pathlib import Path\nPath({str(child_capture)!r}).write_text(json.dumps(sys.argv))\n')
            child.chmod(0o700)
            adapter = root / ('adapter-v2' if opted else 'adapter-v1')
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
            entry = register_custom_adapter(str(adapter), '1.0.0', [], dependency_manifest_version=1, dependencies=[{'executable': str(child), 'sha256': compute_sha256(str(child))}], input_options=options if opted else None)
            require(get_adapter(entry.id).input_options == (options if opted else None), 'normal registration/store readback')
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
            profile = 'custom_opted' if opted else 'custom_legacy'
            get_registry().register_custom_profile(ExecutorProfile(name=profile, kind='custom', workspace_adapter_id='pi', command_adapter_id='custom-adapter:' + entry.id))
            executor = build_executor(profile, settings)
            executor.set_invocation_context(agent='worker', org='synthetic', invocation_kind='task', task_id='TASK-stub')
            for model, effort in [(None, None), (None, 'high'), ('actual-model', None), ('actual-model', 'high'), ('actual-model', 'low')] if opted else [('actual-model', None)]:
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
                receipts.append({'case': 'D', 'opted': opted, 'wire': wire, 'child_argv': expected_child})
            if opted:
                result = executor.run(workspace, 'without-input-ack', session_id='sess-no-ack', model='actual-model', reasoning_effort='high', timeout_seconds=5)
                require(not result.success and result.failure_category == 'post_launch_contract' and result.provider_launched, 'input2 missing ack must fail at shipping runtime response boundary')
                receipts.append({'case':'D-ack', 'success':result.success, 'failure_category':result.failure_category, 'provider_launched':result.provider_launched})
            # Unsupported efforts cannot spawn even through direct executor input.
            before = capture.read_bytes()
            try:
                executor.run(workspace, 'refuse', reasoning_effort='unsupported', timeout_seconds=5)
            except ValueError:
                pass
            else:
                raise AssertionError('unsupported custom effort must refuse')
            require(capture.read_bytes() == before, 'custom unsupported before process')

        legacy_text = '---\nname: worker\nteam: engineering\nrole: worker\nexecutor: claude\n---\n\nSynthetic prompt.\n'
        agent = parse_agent_text(legacy_text, expected_name='worker')
        old = {'allow_rules': [], 'description_digest': hashlib.sha256(b'').hexdigest(), 'executor': 'claude', 'model': None, 'name': 'worker', 'repos': {}, 'role': 'worker', 'status': 'active', 'system_prompt_digest': hashlib.sha256(b'Synthetic prompt.\n').hexdigest(), 'team': 'engineering'}
        frozen = _canonical_json(old)
        require(_canonical_json(_agent_projection(agent, status='active')) == frozen, 'absent projection byte equality')
        selected = dataclasses.replace(agent, reasoning_effort='high')
        reloaded = parse_agent_text(render_agent_text(selected), expected_name='worker')
        require(reloaded.reasoning_effort == 'high' and _agent_projection(reloaded, status='active')['reasoning_effort'] == 'high', 'set inclusion/reload')
        cleared = parse_agent_text(render_agent_text(dataclasses.replace(reloaded, reasoning_effort=None)), expected_name='worker')
        require(_canonical_json(_agent_projection(cleared, status='active')) == frozen, 'clear/restores old bytes')
        require(frozen == _canonical_json(old), 'retained historical bytes not modified')
        # Actual one-shot whole-definition importer, without importing retired tests.
        from runtime.daemon.agent_config import migrate_agent_yaml_to_frontmatter
        import yaml
        for label, executor_name, model_name, expected_effort in (
            ('unrelated-import', 'claude', 'new-model', 'high'),
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
        receipts.append({'case': 'E', 'absent_sha256': hashlib.sha256(frozen).hexdigest(), 'set': _agent_projection(reloaded, status='active'), 'clear_equality': True})
    (args.output / 'stub-receipts.json').write_text(json.dumps(receipts, indent=2) + '\n')
    print(f'PASS {len(receipts)} bounded process/projection receipts; no native provider/lifecycle acceptance claimed')


if __name__ == '__main__':
    main()
