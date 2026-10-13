#!/usr/bin/env python3
"""Disposable THR298 save/reload venue, actual app/routes; no lifecycle workers.

The caller owns a fresh --root and loopback --port, and terminates this process.
The synthetic registry contains executable stubs only. No provider launches occur.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import os
import shutil
import sys
import subprocess
import threading
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--port', type=int, required=True)
    args = parser.parse_args()
    root = args.root.resolve()
    if not root.name.startswith("TASK-10683-browser-venue-"):
        raise SystemExit("Task-owned feature venue name required")
    if root.exists():
        raise SystemExit('Fixture requires an absent task-owned root')
    root.mkdir(parents=True)
    for name, child in (('HOME', 'home'), ('XDG_CONFIG_HOME', 'config'), ('XDG_CACHE_HOME', 'cache'), ('TMPDIR', 'tmp')):
        destination = root / child
        destination.mkdir()
        os.environ[name] = str(destination)
    os.environ['HAPPYRANCH_DAEMON_HOME'] = str(root / 'daemon')
    os.environ['HAPPYRANCH_RUNTIME_ROOT'] = str(root / 'runtime')
    for name in tuple(os.environ):
        if name.startswith(('ANTHROPIC_', 'OPENAI_', 'CODEX_', 'CLAUDE_', 'PI_', 'OPENCODE_', 'GEMINI_', 'GOOGLE_API_')):
            os.environ.pop(name)
    from runtime.config import Settings
    from runtime.runtime import RuntimeDir
    from runtime.daemon.paths import ensure_token, ensure_daemon_home, port_file
    from runtime.orchestrator.executor_binary_registry import set_binary
    from runtime.orchestrator.custom_adapter_registry import register_custom_adapter, approve_adapter
    from runtime.orchestrator.runtime_executor_store import save_runtime_profile
    from runtime.daemon.state import DaemonState
    from runtime.daemon.app import create_app
    import uvicorn

    runtime = RuntimeDir.init(root / 'runtime')
    org_root = runtime.orgs_dir / 'effort-fixture'
    # Reuse the maintained inert synthetic org layout; never import/run its E2E controller.
    shutil.copytree(Path(__file__).resolve().parents[1] / 'e2e/template/org', org_root / 'org')
    (org_root / 'workspaces/case_worker').mkdir(parents=True)
    subprocess.run(['git', 'init', '--bare', str(root / 'synthetic-source.git')], check=True, capture_output=True)
    stub = root / 'capture-only'
    stub.write_text(f'#!{sys.executable}\nraise SystemExit("Fixture must never launch a provider")\n')
    stub.chmod(0o700)
    ensure_daemon_home()
    ensure_token()
    port_file().write_text(str(args.port))
    for cli in ('claude', 'codex', 'pi', 'opencode'):
        set_binary(cli, str(stub))
    adapter = root / 'legacy-adapter'
    adapter.write_text(f'''#!{sys.executable}
import sys,json
value=json.load(sys.stdin)
print(json.dumps({{'success':True,'duration_seconds':0,'session_id':value['invocation']['invocation_id'],'returncode':0,'stdout_tail':'','stderr_tail':'','adapter_metadata':{{'adapter':'legacy-adapter','adapter_version':'1.0.0','contract_version':1}}}}))
''')
    adapter.chmod(0o700)
    entry = register_custom_adapter(str(adapter), '1.0.0', [])
    approve_adapter(entry.id, executable=entry.executable, executable_hash=entry.executable_hash, version=entry.version, capabilities=entry.capabilities, contract_version=1, workspace_adapter='pi')
    save_runtime_profile('legacy_custom', {'workspace_adapter_id': 'pi', 'command_adapter_id': 'custom-adapter:' + entry.id})
    # Ordinary private registration/conformance/exact-snapshot approval before binding.
    for profile, effort_values in (('opted_custom', ['low', 'high']), ('model_only_custom', [])):
        child = root / (profile + '-child')
        child.write_text(f'#!{sys.executable}\nraise SystemExit(0)\n')
        child.chmod(0o700)
        # Filename and output identity must equal the registry's canonical ID.
        wrapper = root / (profile.replace('_', '-') + '-adapter')
        wrapper.write_text(f'''#!{sys.executable}
import sys,json,subprocess
value=json.load(sys.stdin)
argv=[{str(child)!r}]
model=value['model']
if model:argv += [p.replace('{{model}}',model['model_id']) for p in model['model_arg_template'] or []]
effort=value.get('reasoning_effort')
if effort:argv += [p.replace('{{effort}}',effort['reasoning_effort']) for p in effort['effort_arg_template']]
subprocess.run(argv,check=True)
print(json.dumps({{'success':True,'duration_seconds':0,'session_id':value['invocation']['invocation_id'],'returncode':0,'stdout_tail':'','stderr_tail':'','adapter_metadata':{{'adapter':{wrapper.name!r},'adapter_version':'1.0.0','contract_version':1,'input_contract_version':2}}}}))
''')
        wrapper.chmod(0o700)
        options = {'version': 2, 'model_arg_template': ['--model', '{model}'],
                   'effort_arg_template': ['--effort', '{effort}'] if effort_values else None,
                   'reasoning_effort_values': effort_values}
        dependencies = [{'executable': str(child), 'sha256': hashlib.sha256(child.read_bytes()).hexdigest()}]
        entry = register_custom_adapter(str(wrapper), '1.0.0', [], dependency_manifest_version=1,
                                        dependencies=dependencies, input_options=options)
        approve_adapter(entry.id, executable=entry.executable, executable_hash=entry.executable_hash,
                        version=entry.version, capabilities=entry.capabilities, contract_version=1,
                        workspace_adapter='pi', dependency_manifest_version=1,
                        dependencies=entry.dependencies, input_options=entry.input_options)
        save_runtime_profile(profile, {'workspace_adapter_id': 'pi', 'command_adapter_id': 'custom-adapter:' + entry.id})
    # Record resolved writable roots before any Founder-create request. All bootstrap
    # destinations descend from OrgPaths.workspaces_dir; skill sources are read-only.
    (root / 'isolation.json').write_text(json.dumps({
        'root': str(root), 'runtime': str(runtime.root), 'daemon': str(root / 'daemon'),
        'workspaces': str((org_root / 'workspaces').resolve()),
        'homes': {key: os.environ[key] for key in ('HOME', 'XDG_CONFIG_HOME', 'XDG_CACHE_HOME', 'TMPDIR')},
        'binary': str(stub), 'source': str(Path(__file__).resolve().parents[1]),
        'lifespan': 'off', 'agent_execution': False,
    }, indent=2) + '\n')
    settings = Settings(project_root=Path(__file__).resolve().parents[1], executor_launch_spacing_seconds=0)
    state = DaemonState.from_runtime(runtime, settings)
    try:
        # Save/reload only. Workers/schedules are deliberately absent, not claimed verified.
        app = create_app(state)
        @app.middleware('http')
        async def record_selection_request(request, call_next):
            # Scope body capture to feature writers; never record credentials/tokens.
            path = request.url.path
            selected = path.startswith('/api/v1/orgs/effort-fixture/agents') and request.method in ('PUT', 'POST')
            body = json.loads(await request.body()) if selected else None
            response = await call_next(request)
            if selected:
                with (root / 'selection-requests.jsonl').open('a') as stream:
                    stream.write(json.dumps({'method': request.method, 'path': path, 'body': body, 'status': response.status_code}) + '\n')
            return response
        server = uvicorn.Server(uvicorn.Config(app, host='127.0.0.1', port=args.port, lifespan='off', log_level='warning'))
        def stop_when_requested():
            while not (root / 'STOP').exists():
                time.sleep(0.2)
            server.should_exit = True
        threading.Thread(target=stop_when_requested, daemon=True).start()
        server.run()
    finally:
        for org in state.orgs.values():
            org.close()


if __name__ == '__main__':
    main()
