"""Closed C7 actual-launch capture and genuine CLI callbacks (test side only).

L launches are independent of the held C6 M check/apply/receipt handoff. This
helper neither admits a context nor writes a runtime result/terminal/session.
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

from tests.helpers.integration_stub_guard.guard import (
    _read_owned, context_plan, manifest, validate_callback, validate_plan, validate_stub,
)


def _one(pattern: str, outer: str, label: str) -> str:
    values = re.findall(pattern, outer, re.MULTILINE)
    if not values:
        raise ValueError('missing_' + label)
    if len(values) != 1:
        raise ValueError(('duplicate_' if len(set(values)) == 1 else 'conflicting_') + label)
    return values[0]


def _outer_identity(prompt: str, org: str) -> tuple[str, dict]:
    """Parse shipping outer sections, excluding skill examples and transcript."""
    if re.search(r'^You are (?:consultant_head|consultant_codex)\. Use ', prompt, re.MULTILINE):
        # The real task Parameters block precedes brief/skills/history. Examples
        # in any of those bodies never supply the callback's identity.
        outer = prompt.split('\n  brief:', 1)[0]
        task = _one(r'^  task_id: (TASK-\d+)[ \t]*$', outer, 'task')
        session = _one(r'^  session_id: (sess-[a-f0-9]+)[ \t]*$', outer, 'session')
        return 'task', {'task_id': task, 'session_id': session}
    if re.search(r'^(?:You are participating in|Continuing) thread THR-\d+:', prompt, re.MULTILINE):
        header, separator, rest = prompt.partition('Full message history follows.')
        if not separator:
            raise ValueError('fresh_full_thread_prompt_required')
        thread = _one(r'^(?:You are participating in|Continuing) thread (THR-\d+):', header, 'thread')
        # The callback instructions follow history and the last purpose note.
        outer = rest.rsplit('\nYou have been invoked because:\n', 1)[-1]
        token = _one(r'^Your invocation_token for this turn is: ([a-f0-9-]+)[ \t]*$', outer, 'invocation_token')
        return 'thread', {'thread_id': thread, 'invocation_token': token}
    for kind, title, action, operation, flag, boundary in (
        ('dream', '# Private Nightly Dream', 'dreams', 'complete', '--dream-id', '\nTask history:'),
        ('wake', '# Working-Hours Wake', 'work-hours', 'spawn', '--work-hour-id', '\n## Routine Tasks'),
        ('schedule', '# Schedule Fire', 'schedules', 'spawn', '--schedule-id', '\n## Normalized Brief'),
    ):
        if re.search('^' + re.escape(title) + '$', prompt, re.MULTILINE):
            outer = prompt.split(boundary, 1)[0]
            target = _one(r'^happyranch ' + re.escape(action + ' ' + operation + ' --org ' + org + ' ' + flag)
                          + r' ([A-Za-z0-9_-]+) --from-file \S+[ \t]*$', outer, kind + '_callback_id')
            if kind != 'wake':
                label = 'Dream id' if kind == 'dream' else 'Schedule'
                if _one('^' + label + r': ([A-Za-z0-9_-]+)[ \t]*$', outer, kind + '_id') != target:
                    raise ValueError('conflicting_' + kind + '_callback')
            return kind, {'context_id': target}
    raise ValueError('missing_real_outer_context')


def _rows(root: Path, query: str, args: tuple = ()) -> list[tuple]:
    with sqlite3.connect((root / 'happyranch.db').as_uri() + '?mode=ro', uri=True) as conn:
        return conn.execute(query, args).fetchall()


def _refusal_state(root: Path, task: str) -> dict:
    # Only this now-quiescent root is active. These independently read durable
    # owners expose a stale result, consumed recovery, audit or task mutation.
    return {table: _rows(root, 'SELECT * FROM ' + table + ' ORDER BY rowid')
            for table in ('task_results', 'task_completion_recoveries', 'audit_log')} | {
                'task': _rows(root, 'SELECT * FROM tasks WHERE id=?', (task,))}


def _call(command: list[str], payload: dict, path: Path) -> subprocess.CompletedProcess:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'w') as output:
        json.dump(payload, output)
    return subprocess.run(command + ['--from-file', str(path)], capture_output=True, text=True, timeout=30)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--provider', choices=('claude', 'codex'), required=True)
    parser.add_argument('--capture', type=Path, required=True)
    parser.add_argument('--stale-task-root', action='store_true')
    args = parser.parse_args()
    binding = manifest()
    workspace = Path.cwd().resolve(strict=True)
    agent = workspace.name
    if agent not in ('consultant_head', 'consultant_codex'):
        raise ValueError('only_two_actual_consultants')
    if args.provider != ('claude' if agent == 'consultant_head' else 'codex'):
        raise ValueError('original_provider_mismatch')
    parent_root = Path(binding['root'])
    if not workspace.is_relative_to(parent_root) or workspace.parent.name != 'workspaces':
        raise ValueError('original_parent_owned_workspace_required')
    root, org = workspace.parent.parent, workspace.parent.parent.name
    # Shipping _callee_env prepares the executor's cache under its workspace;
    # HOME/config retain the isolated parent's exact binding.
    expected_environment = {'HOME': parent_root / 'home', 'XDG_CONFIG_HOME': parent_root / 'config',
                            'XDG_CACHE_HOME': workspace / '.happyranch/cache/xdg'}
    stub = os.environ['HAPPYRANCH_TEST_CONTEXT_STUB']
    plan = os.environ['HAPPYRANCH_TEST_CONTEXT_PLAN']
    validate_stub(args.provider, stub, binding)
    validate_plan(plan)
    validate_callback(binding)
    if not context_plan(plan, binding):
        raise ValueError('exact_registered_context_plan_required')
    source = Path(binding['source'])
    helper = source / 'tests/helpers/human_team_context_plan.py'
    committed = subprocess.check_output(['git', '-C', str(source), 'show',
        binding['revision'] + ':tests/helpers/human_team_context_plan.py'])
    if Path(__file__).resolve() != helper or helper.read_bytes() != committed:
        raise ValueError('candidate_context_helper_source_binding_required')
    capture = args.capture
    if (not capture.is_absolute() or capture.is_symlink()
            or not capture.parent.resolve(strict=True).is_relative_to(parent_root)):
        raise ValueError('owned_fixture_capture_required')
    prompt = sys.stdin.read()
    # Retain this registered fixture invocation even if parsing/setup refuses
    # before the callback record. Never export ambient env or provider memory.
    launch = capture.parent / ('C7-outer-prompt-' + str(os.getpid()) + '.json')
    fd = os.open(launch, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'w') as output:
        json.dump({'source_sha': binding['revision'], 'provider': args.provider,
            'workspace': str(workspace), 'prompt': prompt,
            'execution_environment': {key: os.environ.get(key) for key in
                ('HOME', 'XDG_CONFIG_HOME', 'XDG_CACHE_HOME', 'PATH')},
            'meaning': 'actual registered launch input; callback not yet attempted'}, output, sort_keys=True)
    if (any(os.environ.get(key) != str(value) for key, value in expected_environment.items())
            or os.environ.get('PATH') != os.pathsep.join((str(parent_root / 'bin'), '/usr/bin', '/bin'))
            or os.environ.get('HAPPYRANCH_ORG_SLUG') != org
            or any(key in os.environ for key in ('ANTHROPIC_API_KEY', 'OPENAI_API_KEY', 'CODEX_HOME',
                                                 'CLAUDE_CONFIG_DIR', 'HAPPYRANCH_RUNTIME', 'HAPPYRANCH_DAEMON_TOKEN'))):
        raise ValueError('closed_actual_executor_environment_required')
    kind, identity = _outer_identity(prompt, org)
    runtime_hint = os.environ.get('HAPPYRANCH_RUNTIME_SESSION_ID')
    stale = None
    stage = 'complete'
    if kind == 'task':
        task, session = identity['task_id'], identity['session_id']
        current = _rows(root, 'SELECT assigned_agent,current_session_id,parent_task_id,status FROM tasks WHERE id=?', (task,))
        if len(current) != 1 or current[0][:2] != (agent, session) or runtime_hint != session or current[0][3] != 'in_progress':
            raise ValueError('actual_current_task_tuple_required')
        payload = {'task_id': task, 'session_id': session, 'agent': agent,
                   'status': 'completed', 'summary': 'C7 actual worker root complete', 'confidence': 90,
                   'decision': {'action': 'done', 'summary': 'C7 actual worker root complete'}}
        command = ['happyranch', 'report-completion', '--org', org]
        if args.stale_task_root and current[0][2] is None:
            children = _rows(root, 'SELECT id,status FROM tasks WHERE parent_task_id=?', (task,))
            if not children:
                stage = 'self-delegate'
                payload['decision'] = {'action': 'delegate', 'agent': agent, 'prompt': 'C7 genuine self-child continuation'}
            else:
                if len(children) != 1 or children[0][1] != 'completed':
                    raise ValueError('genuine_completed_self_child_required')
                prior_records = [json.loads(line) for line in capture.read_text().splitlines()]
                origins = [row for row in prior_records if row['kind'] == 'task' and row['stage'] == 'self-delegate'
                           and row['identity']['task_id'] == task and row['agent'] == agent]
                if len(origins) != 1 or origins[0]['callback_exit'] != 0:
                    raise ValueError('actual_earlier_admitted_same_task_required')
                old = origins[0]['identity']['session_id']
                if old == session:
                    raise ValueError('genuine_new_same_task_session_required')
                accepted = _rows(root, 'SELECT id,agent,session_id FROM task_results WHERE task_id=?', (task,))
                if len(accepted) != 1 or type(accepted[0][0]) is not int or accepted[0][1:] != (agent, old):
                    raise ValueError('actual_original_result_required')
                rejected_payload = dict(payload, session_id=old)
                before = _refusal_state(root, task)
                rejected = _call(command, rejected_payload, capture.parent / (session + '.stale.request.json'))
                after = _refusal_state(root, task)
                if after != before:
                    raise ValueError('stale_callback_changed_durable_owner')
                if rejected.returncode != 1 or rejected.stdout.strip() != f'Session id mismatch — daemon expected {session} but got {old}.':
                    raise ValueError('precise_real_stale_callback_refusal_required')
                stale = {'earlier': origins[0]['identity'], 'current': identity, 'exit': rejected.returncode,
                         'stdout': rejected.stdout, 'stderr': rejected.stderr,
                         'before_sha256': hashlib.sha256(json.dumps(before, sort_keys=True).encode()).hexdigest(),
                         'after_sha256': hashlib.sha256(json.dumps(after, sort_keys=True).encode()).hexdigest()}
                stage = 'current-after-stale'
    elif kind == 'thread':
        thread, token = identity['thread_id'], identity['invocation_token']
        current = _rows(root, 'SELECT thread_id,agent_name,status,triggering_seq,started_at,session_id FROM thread_invocations WHERE invocation_token=?', (token,))
        if (len(current) != 1 or current[0][:3] != (thread, agent, 'pending')
                or type(current[0][3]) is not int or current[0][3] <= 0 or not current[0][4]
                or current[0][5] != runtime_hint):
            raise ValueError('actual_current_thread_token_binding_required')
        payload = {'thread_id': thread, 'speaker': agent, 'invocation_token': token,
                   'body_markdown': 'C7 genuine current worker reply', 'in_response_to_seq': current[0][3]}
        command = ['happyranch', 'threads', 'reply', '--org', org]
    else:
        action, flag, table = {'dream': ('dreams', '--dream-id', 'dreams'),
                              'wake': ('work-hours', '--work-hour-id', 'work_hours'),
                              'schedule': ('schedules', '--schedule-id', 'schedules')}[kind]
        target = identity['context_id']
        if _rows(root, 'SELECT agent_name,status FROM ' + table + ' WHERE id=?', (target,)) != [(agent, 'firing' if kind == 'schedule' else 'running')]:
            raise ValueError('actual_running_context_owner_required')
        if kind == 'dream':
            payload = {'summary': 'C7 private worker reflection', 'learnings': [], 'kb_candidates': [],
                       'founder_thread': {'needed': False}}
        elif kind == 'wake':
            payload = {'summary': 'C7 current worker wake', 'routines': [{'brief': 'C7 own routine root'}]}
        else:
            payload = {'summary': 'C7 actual one-shot fire'}
        command = ['happyranch', action, 'complete' if kind == 'dream' else 'spawn', '--org', org, flag, target]
    files = {}
    for rel in ('AGENTS.md', 'CLAUDE.md', '.claude/settings.json', 'opencode.json'):
        path = workspace / rel
        if path.exists():
            raw = path.read_bytes()
            files[rel] = {'sha256': hashlib.sha256(raw).hexdigest(), 'text': raw.decode(),
                          'raw_link': os.readlink(path) if path.is_symlink() else None}
    skills = {}
    for provider_root in ('.agents/skills', '.claude/skills'):
        directory = workspace / provider_root
        if not directory.is_dir() or directory.is_symlink():
            raise ValueError('both_original_native_skill_roots_required')
        skills[provider_root] = {}
        for path in sorted(directory.iterdir()):
            if not path.is_symlink() or path.name.startswith('.tmp.'):
                raise ValueError('incomplete_native_skill_link')
            target = path.resolve(strict=True)
            if not target.is_relative_to(parent_root):
                raise ValueError('fixture_owned_canonical_package_required')
            members = {str(member.relative_to(target)): hashlib.sha256(member.read_bytes()).hexdigest()
                       for member in sorted(target.rglob('*')) if member.is_file()}
            skills[provider_root][path.name] = {'raw_link': os.readlink(path), 'target': str(target), 'members': members}
    stem = kind + '-' + (identity.get('session_id') or identity.get('invocation_token') or identity['context_id'])
    actual = _call(command, payload, capture.parent / (stem + '.request.json'))
    record = {'kind': kind, 'stage': stage, 'agent': agent, 'provider': args.provider, 'workspace': str(workspace),
              'source_sha': binding['revision'], 'identity': identity, 'prompt': prompt,
              'helper_sha256': hashlib.sha256(committed).hexdigest(), 'runtime_session_hint': runtime_hint,
              'prompt_sha256': hashlib.sha256(prompt.encode()).hexdigest(),
              'python_path': sys.executable, 'bound_python_path': binding['python'],
              'execution_environment': {key: os.environ.get(key) for key in
                  ('HOME', 'XDG_CONFIG_HOME', 'XDG_CACHE_HOME', 'TMPDIR', 'TMP', 'TEMP', 'PATH')},
              'native_source_sha256': {rel: hashlib.sha256((source / rel).read_bytes()).hexdigest() for rel in
                  ('runtime/orchestrator/executors.py', 'runtime/orchestrator/workspace_adapters.py',
                   'runtime/skills/system_contracts.py', 'runtime/skills/exposure.py',
                   'runtime/skills/resolver.py', 'tests/helpers/integration_stub_guard/guard.py')},
              'stub_path': stub, 'stub_sha256': binding['stubs'][args.provider]['sha256'],
              'plan_path': plan, 'plan_sha256': hashlib.sha256(_read_owned(Path(plan))).hexdigest(),
              'callback_sha256': binding['callback_sha256'],
              'stub_argv': json.loads(os.environ['HAPPYRANCH_TEST_CONTEXT_ARGV_JSON']),
              'definition_bytes': (root / 'org/agents' / (agent + '.md')).read_text(),
              'generated_files': files, 'skill_manifests': skills, 'callback': command,
              'callback_exit': actual.returncode, 'callback_stdout': actual.stdout, 'callback_stderr': actual.stderr,
              'stale_control': stale, 'provider_pid': int(os.environ['HAPPYRANCH_TEST_CONTEXT_PROVIDER_PID']),
              'pid': os.getpid(), 'process_group': os.getpgrp()}
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
