"""Closed test-side C7 prompt capture and genuine context callbacks.

The exact guarded shell plan invokes this script with --provider and --capture,
receiving the actual provider prompt on stdin. Existing plan positional args
remain unchanged. Source authoring is not ten-context behavioral evidence.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import sys

from tests.helpers.integration_stub_guard.guard import manifest, validate_callback, validate_stub


def _one(pattern: str, prompt: str, label: str) -> str:
    values = re.findall(pattern, prompt, re.MULTILINE)
    if len(values) != 1:
        raise ValueError('missing_duplicate_or_conflicting_' + label)
    return values[0]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--provider', choices=('claude', 'codex'), required=True)
    parser.add_argument('--capture', type=Path, required=True)
    args = parser.parse_args()
    binding = manifest()
    workspace = Path.cwd().resolve(strict=True)
    agent = workspace.name
    if agent not in ('consultant_head', 'consultant_codex'):
        raise ValueError('only_two_actual_consultants')
    if args.provider != ('claude' if agent == 'consultant_head' else 'codex'):
        raise ValueError('original_provider_mismatch')
    if not workspace.is_relative_to(Path(binding['root'])) or workspace.parent.name != 'workspaces':
        raise ValueError('original_parent_owned_workspace_required')
    root = workspace.parent.parent
    org = root.name
    # This runs inside an admitted executor, whose unchanged environment
    # legitimately adds the org and per-invocation session hints. The parent
    # bootstrap's pre-executor environment check deliberately forbids those
    # hints and therefore cannot be reused at this boundary.
    parent_root = Path(binding['root'])
    expected_environment = {'HOME': parent_root / 'home', 'XDG_CONFIG_HOME': parent_root / 'config',
                            'XDG_CACHE_HOME': parent_root / 'cache'}
    if (any(os.environ.get(key) != str(value) for key, value in expected_environment.items())
            or os.environ.get('PATH') != os.pathsep.join((str(parent_root / 'bin'), '/usr/bin', '/bin'))
            or os.environ.get('HAPPYRANCH_ORG_SLUG') != org
            or any(key in os.environ for key in ('ANTHROPIC_API_KEY', 'OPENAI_API_KEY', 'CODEX_HOME',
                                                 'CLAUDE_CONFIG_DIR', 'HAPPYRANCH_RUNTIME', 'HAPPYRANCH_DAEMON_TOKEN'))):
        raise ValueError('closed_actual_executor_environment_required')
    validate_stub(args.provider, binding['stubs'][args.provider]['path'], binding)
    validate_callback(binding)
    source = Path(binding['source'])
    helper = source / 'tests/helpers/human_team_context_plan.py'
    committed_helper = subprocess.check_output(['git', '-C', str(source), 'show',
        binding['revision'] + ':tests/helpers/human_team_context_plan.py'])
    if Path(__file__).resolve() != helper or helper.read_bytes() != committed_helper:
        raise ValueError('candidate_context_helper_source_binding_required')
    capture = args.capture
    if (not capture.is_absolute() or capture.is_symlink()
            or not capture.parent.resolve(strict=True).is_relative_to(Path(binding['root']))):
        raise ValueError('owned_fixture_capture_required')
    prompt = sys.stdin.read()
    # Identify only the genuine outer prompt, not examples in bundled skills.
    kinds = [kind for kind, marker in (
        ('thread', r'^(?:You are participating in|Continuing) thread THR-\d+:'),
        ('dream', r'^# Private Nightly Dream$'),
        ('wake', r'^# Working-Hours Wake$'),
        ('schedule', r'^# Schedule Fire$'),
        ('task', r'^You are (?:consultant_head|consultant_codex)\. Use (?:the start-task skill|the injected task parameters)'),
    ) if re.search(marker, prompt, re.MULTILINE)]
    if len(kinds) != 1:
        raise ValueError('ambiguous_or_missing_real_context')
    kind = kinds[0]
    common = ['happyranch']
    if kind == 'task':
        task = _one(r'^\s*task_id: (TASK-\d+)\s*$', prompt, 'task')
        session = _one(r'^\s*session_id: (sess-[a-f0-9]+)\s*$', prompt, 'session')
        extra = re.findall(r'binding task=(TASK-\d+) session=(sess-[a-f0-9]+)', prompt)
        if extra and extra != [(task, session)]:
            raise ValueError('conflicting_recovery_binding')
        payload = {'task_id': task, 'session_id': session, 'agent': agent,
                   'status': 'completed', 'summary': 'C7 actual worker root complete',
                   'confidence': 90, 'decision': {'action': 'done', 'summary': 'C7 actual worker root complete'}}
        command = common + ['report-completion', '--org', org]
        identity = {'task_id': task, 'session_id': session}
    elif kind == 'thread':
        thread = _one(r'^(?:You are participating in|Continuing) thread (THR-\d+):', prompt, 'thread')
        token = _one(r'^Your invocation_token for this turn is: ([a-f0-9-]+)\s*$', prompt, 'invocation_token')
        with sqlite3.connect((root / 'happyranch.db').as_uri() + '?mode=ro', uri=True) as conn:
            current = conn.execute('SELECT thread_id,agent_name,status,triggering_seq,started_at FROM thread_invocations WHERE invocation_token=?', (token,)).fetchall()
        if (len(current) != 1 or current[0][:3] != (thread, agent, 'pending')
                or type(current[0][3]) is not int or current[0][3] <= 0 or not current[0][4]):
            raise ValueError('actual_current_thread_token_binding_required')
        payload = {'thread_id': thread, 'speaker': agent, 'invocation_token': token,
                   'body_markdown': 'C7 genuine current worker reply', 'in_response_to_seq': current[0][3]}
        command = common + ['threads', 'reply', '--org', org]
        identity = {'thread_id': thread, 'invocation_token': token}
    else:
        action, flag = {'dream': ('dreams', '--dream-id'), 'wake': ('work-hours', '--work-hour-id'),
                        'schedule': ('schedules', '--schedule-id')}[kind]
        operation = 'complete' if kind == 'dream' else 'spawn'
        target = _one(r'^happyranch ' + re.escape(action + ' ' + operation) + r' --org ' + re.escape(org)
                      + ' ' + re.escape(flag) + r' ([A-Za-z0-9_-]+) --from-file \S+\s*$', prompt, kind + '_callback_id')
        if kind == 'dream':
            if _one(r'^Dream id: ([A-Za-z0-9_-]+)\s*$', prompt, 'dream_id') != target:
                raise ValueError('conflicting_dream_callback')
            payload = {'summary': 'C7 private worker reflection', 'learnings': [], 'kb_candidates': [],
                       'founder_thread': {'needed': False}}
        elif kind == 'wake':
            payload = {'summary': 'C7 current worker wake', 'routines': [{'brief': 'C7 own routine root'}]}
        else:
            if _one(r'^Schedule: ([A-Za-z0-9_-]+)\s*$', prompt, 'schedule_id') != target:
                raise ValueError('conflicting_schedule_callback')
            payload = {'summary': 'C7 actual one-shot fire'}
        command = common + [action, operation, '--org', org, flag, target]
        identity = {'context_id': target}
    # Capture what the unchanged materializers actually exposed. No helper
    # generates a workspace, grants a role, inserts a result or settles state.
    files = {}
    for rel in ('AGENTS.md', 'CLAUDE.md', '.claude/settings.json', 'opencode.json'):
        path = workspace / rel
        if path.is_symlink():
            files[rel] = {'raw_link': os.readlink(path)}
        elif path.is_file():
            files[rel] = {'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
                          'text': path.read_text()}
    skill_links = {}
    for provider_root in ('.agents/skills', '.claude/skills'):
        directory = workspace / provider_root
        if not directory.is_dir() or directory.is_symlink():
            raise ValueError('both_original_native_skill_roots_required')
        for path in directory.iterdir():
            if not path.is_symlink() or path.name.startswith('.tmp.'):
                raise ValueError('incomplete_native_skill_link')
            skill_links[str(path.relative_to(workspace))] = os.readlink(path)
    stem = kind + '-' + (identity.get('session_id') or identity.get('invocation_token') or identity['context_id'])
    request = capture.parent / (stem + '.request.json')
    fd = os.open(request, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'w') as output:
        json.dump(payload, output)
    actual = subprocess.run(command + ['--from-file', str(request)], capture_output=True, text=True, timeout=30)
    record = {'kind': kind, 'agent': agent, 'provider': args.provider, 'workspace': str(workspace),
              'source_sha': binding['revision'], 'identity': identity, 'prompt': prompt,
              'helper_sha256': hashlib.sha256(committed_helper).hexdigest(),
              'runtime_session_hint': os.environ.get('HAPPYRANCH_RUNTIME_SESSION_ID'),
              'prompt_sha256': hashlib.sha256(prompt.encode()).hexdigest(),
              'stub_argv': json.loads(os.environ['HAPPYRANCH_TEST_CONTEXT_ARGV_JSON']),
              'definition_bytes': (root / 'org/agents' / (agent + '.md')).read_text(),
              'generated_files': files, 'skill_links': skill_links, 'callback': command,
              'callback_exit': actual.returncode, 'pid': os.getpid(), 'process_group': os.getpgrp()}
    fd = os.open(capture, os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'a') as output:
        output.write(json.dumps(record, sort_keys=True) + '\n')
        output.flush()
        os.fsync(output.fileno())
    if actual.returncode != 0:
        raise ValueError('actual_context_callback_failed:' + str(actual.returncode))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
