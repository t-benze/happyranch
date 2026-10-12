#!/usr/bin/env python3
"""Prepare one frozen cleanup manifest from native receipts; never act or submit."""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import sys
from typing import Any

# Packaged source is read-only evidence, including during sibling imports.
sys.dont_write_bytecode = True

TERMINAL = {'completed', 'failed', 'cancelled', 'superseded'}
MARKERS = {
    'HAPPYRANCH SYSTEM WORKSPACE CLEANUP RUN (manual-dispatch)',
    'HAPPYRANCH SYSTEM WORKSPACE CLEANUP RUN (daemon-triggered)',
}
CAP = 40


def aware_instant(value: Any) -> dt.datetime:
    if (not isinstance(value, str) or not re.fullmatch(
            r'\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d{1,6})?(?:Z|[+-]\d\d:\d\d)', value)):
        raise ValueError('missing_malformed_or_naive_time')
    parsed = dt.datetime.fromisoformat(value.replace('Z', '+00:00'))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError('naive_time')
    return parsed.astimezone(dt.timezone.utc)


def retention_eligible(record: dict[str, Any], floor_seconds: int,
                       observed_at: dt.datetime) -> bool:
    if type(floor_seconds) is not int or floor_seconds not in (86400, 604800):
        raise ValueError('unsupported_retention_floor')
    if observed_at.tzinfo is None or observed_at.utcoffset() is None:
        raise ValueError('naive_observation')
    completed = aware_instant(record.get('completed_at'))
    return (record.get('status') in TERMINAL
            and observed_at - completed >= dt.timedelta(seconds=floor_seconds))


def same_task_snapshot(listed: dict[str, Any], fresh: dict[str, Any]) -> bool:
    return (all(isinstance(listed.get(key), str) and listed[key] == fresh.get(key)
                for key in ('task_id', 'assigned_agent', 'status'))
            and aware_instant(listed.get('completed_at'))
            == aware_instant(fresh.get('completed_at')))


def read_json(path: Path) -> Any:
    data = path.read_bytes()
    if len(data) > 16 * 1024 * 1024:
        raise ValueError('input_cap')
    return json.loads(data)


def write_json(path: Path, value: Any) -> None:
    # Only the canonical name is consumable. Keep the one-attempt staging
    # sibling on failure (and success), so correctness never depends on unlink.
    # Check before staging; link below also refuses a concurrently occupied name.
    if path.exists() or path.is_symlink():
        raise FileExistsError(str(path))
    pending = path.with_name('.' + path.name + '.pending')
    with pending.open('x', encoding='utf-8') as handle:
        json.dump(value, handle, indent=2, sort_keys=True)
        handle.write('\n')
        handle.flush()
        os.fsync(handle.fileno())
    # Close must succeed before no-clobber publication. Nothing fallible follows.
    os.link(pending, path)


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def beneath(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def successful_receipts(value: Any) -> bool:
    return (isinstance(value, list) and bool(value) and all(
        isinstance(row, dict) and isinstance(row.get('argv'), list)
        and bool(row['argv']) and all(isinstance(arg, str) for arg in row['argv'])
        and isinstance(row.get('cwd'), str) and row['cwd'].startswith('/')
        and type(row.get('exit_code')) is int and row['exit_code'] == 0
        and isinstance(row.get('stdout'), str) and isinstance(row.get('stderr'), str)
        and row.get('error') is None for row in value))


def binding() -> dict[str, str]:
    keys = ('WORKSPACE', 'PRIMARY', 'AGENT', 'ORG', 'ACTING_TASK', 'SESSION_ID', 'CLEANUP_MARKER')
    result = {key: os.environ.get(key, '') for key in keys}
    if not all(result[key] for key in keys if key != 'PRIMARY') or result['CLEANUP_MARKER'] not in MARKERS:
        raise ValueError('scope_or_marker_unset')
    workspace, primary = Path(result['WORKSPACE']), Path(result['PRIMARY'])
    if (not workspace.is_absolute() or workspace.resolve() != workspace
            or not workspace.is_dir()
            or (result['PRIMARY'] and (primary.resolve() != primary
                or primary.parent != workspace / 'repos' or not primary.is_dir()))):
        raise ValueError('workspace_primary_boundary')
    return result


def history_input(path: Path) -> tuple[dict[str, Any], list[Path]]:
    value = read_json(path)
    if (not isinstance(value, dict) or set(value) != {'complete', 'sources', 'excluded_paths'}
            or value['complete'] is not True or not isinstance(value['sources'], list)
            or not value['sources'] or not isinstance(value['excluded_paths'], list)):
        raise ValueError('history_incomplete')
    for source in value['sources']:
        if (not isinstance(source, dict) or set(source) != {'path', 'sha256'}
                or not Path(source['path']).is_absolute()
                or digest(Path(source['path'])) != source['sha256']):
            raise ValueError('history_source_changed')
    paths = [Path(item) for item in value['excluded_paths']]
    if any(not item.is_absolute() or item.resolve() != item for item in paths):
        raise ValueError('history_path_unknown')
    return value, paths


def validate_inventory(value: Any, bound: dict[str, str]) -> None:
    if (not isinstance(value, dict) or value.get('complete') is not True
            or value.get('binding') != bound or not isinstance(value.get('candidates'), list)
            or value.get('repositories_state') not in ('present', 'absent')):
        raise ValueError('inventory_scope_incomplete')
    for group in ('repository_receipts', 'assigned_task_receipts', 'trigger_history_receipts'):
        if not successful_receipts(value.get(group)):
            raise ValueError(group + '_incomplete')
    if value['repositories_state'] == 'present' and not bound['PRIMARY']:
        raise ValueError('present_repository_without_primary')
    if value['repositories_state'] == 'absent' and value['candidates']:
        raise ValueError('absent_repositories_with_candidates')


def fresh_task(task_id: str, bound: dict[str, str], cwd: Path) -> tuple[Any, dict[str, Any]]:
    from observe_cleanup_batch import capture_command
    receipt = capture_command(['happyranch', 'recall', '--org', bound['ORG'], task_id], cwd)
    if not successful_receipts([receipt]):
        raise ValueError('fresh_recall_failed:' + json.dumps(receipt, sort_keys=True))
    return json.loads(receipt['stdout']), receipt


def current_owner(bound: dict[str, str], cwd: Path) -> dict[str, Any]:
    record, receipt = fresh_task(bound['ACTING_TASK'], bound, cwd)
    if (record.get('task_id') != bound['ACTING_TASK'] or record.get('assigned_agent') != bound['AGENT']
            or record.get('status') != 'in_progress'
            or not isinstance(record.get('brief'), str)
            or record['brief'].splitlines()[0] != bound['CLEANUP_MARKER']):
        raise ValueError('current_owner_or_marker_changed')
    # Session authority is still authenticated by the supported jobs service;
    # recall alone does not prove an ACTIVE session.
    return receipt


def prepare(inventory_path: Path, history_path: Path, output_dir: Path,
            max_candidates: int = CAP) -> dict[str, Any]:
    if type(max_candidates) is not int or not 1 <= max_candidates <= CAP:
        raise ValueError('cap_outside_1_40')
    output_dir.mkdir(parents=True, exist_ok=True)
    if any(output_dir.iterdir()):
        raise ValueError('evidence_directory_not_empty')
    report: dict[str, Any] = {'state': 'incomplete', 'errors': [], 'rows': [], 'receipts': [],
                              'manifest': [], 'frozen_selection': [], 'no_refill': True}
    try:
        bound = binding()
        inventory = read_json(inventory_path)
        write_json(output_dir / 'inventory.json', inventory)
        validate_inventory(inventory, bound)
        history, exclusions = history_input(history_path)
        report.update(binding=bound, inventory_path=str(inventory_path), inventory_sha256=digest(inventory_path),
                      inventory_snapshot_sha256=digest(output_dir / 'inventory.json'),
                      history_path=str(history_path), history_sha256=digest(history_path),
                      history_sources=history['sources'], observed_at=dt.datetime.now(dt.timezone.utc).isoformat())
        report['receipts'].append(current_owner(bound, Path(bound['WORKSPACE'])))
        observed_at = aware_instant(report['observed_at'])
        candidates = []
        seen: set[str] = set()
        uncertain = False
        for row in inventory['candidates']:
            reason = None
            if not isinstance(row, dict):
                raise ValueError('candidate_schema_unknown')
            candidate, containing = Path(row['candidate']), Path(row['containing'])
            listed = row.get('listing', {})
            task_id = containing.name
            primary = containing.parent.parent.parent
            if (not candidate.is_absolute() or candidate.resolve() != candidate
                    or containing.resolve() != containing or not task_id.startswith('TASK-')
                    or primary.parent != Path(bound['WORKSPACE']) / 'repos'
                    or containing != primary / '.claude/worktrees' / task_id
                    or primary != Path(bound['PRIMARY'])
                    or row.get('kind') not in ('worktree', 'cache')
                    or (row['kind'] == 'worktree' and candidate != containing)
                    or (row['kind'] == 'cache' and (candidate.parent != containing
                        or candidate.name not in ('.venv', 'node_modules')))
                    or str(candidate) in seen):
                raise ValueError('candidate_scope_unknown')
            seen.add(str(candidate))
            if any(beneath(candidate, old) or beneath(old, candidate)
                   or beneath(containing, old) or beneath(old, containing) for old in exclusions):
                reason = 'historical_overlap'
            elif listed.get('assigned_agent') != bound['AGENT']:
                reason = 'foreign_or_unknown_owner'
                uncertain |= not isinstance(listed.get('assigned_agent'), str)
            else:
                try:
                    if listed.get('task_id') != task_id:
                        raise ValueError('owner_task_mapping_unknown')
                    floor = 604800 if row['kind'] == 'worktree' else 86400
                    if not retention_eligible(listed, floor, observed_at):
                        reason = 'young_or_nonterminal'
                    else:
                        candidates.append((aware_instant(listed['completed_at']), str(candidate), row))
                except (ValueError, TypeError) as exc:
                    uncertain = True
                    reason = str(exc)
            report['rows'].append({'candidate': str(candidate), 'listing': listed, 'reason': reason})
        selected = sorted(candidates, key=lambda item: (item[2]['kind'] != 'worktree', item[0], item[1]))[:max_candidates]
        report['frozen_selection'] = [item[1] for item in selected]
        for _, candidate, row in selected:
            entry = next(item for item in report['rows'] if item['candidate'] == candidate)
            try:
                fresh, receipt = fresh_task(row['listing']['task_id'], bound, Path(bound['WORKSPACE']))
                report['receipts'].append(receipt)
                entry['fresh'] = fresh
                if not same_task_snapshot(row['listing'], fresh):
                    raise ValueError('task_snapshot_changed')
                if not retention_eligible(fresh, 604800 if row['kind'] == 'worktree' else 86400, observed_at):
                    raise ValueError('fresh_retention_refused')
                if (type(row.get('allocated_bytes')) is not int or row['allocated_bytes'] < 0
                        or not successful_receipts(row.get('measurement_receipts'))):
                    raise ValueError('measurement_incomplete')
                report['manifest'].append({key: row[key] for key in ('candidate', 'containing', 'kind', 'allocated_bytes')})
                entry['reason'] = 'prepared'
            except (OSError, ValueError, TypeError, KeyError) as exc:
                entry['reason'] = str(exc)
                uncertain = True
        report['state'] = ('prepared_nonempty' if report['manifest'] else
                           'incomplete' if uncertain else 'no_candidates_complete')
        if uncertain:
            report['errors'].append('candidate_uncertainty_retained_no_refill')
    except (OSError, ValueError, TypeError, KeyError, IndexError) as exc:
        report['state'] = 'incomplete'
        report['manifest'] = []
        report['errors'].append(str(exc))
    write_json(output_dir / 'manifest.json', report['manifest'])
    report['manifest_path'] = str((output_dir / 'manifest.json').resolve())
    report['manifest_sha256'] = digest(output_dir / 'manifest.json')
    write_json(output_dir / 'preparation.json', report)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    prep = commands.add_parser('prepare')
    prep.add_argument('--inventory', type=Path, required=True)
    prep.add_argument('--history-exclusions', type=Path, required=True)
    prep.add_argument('--output-dir', type=Path, required=True)
    prep.add_argument('--max-candidates', type=int, default=CAP)
    retention = commands.add_parser('retention')
    retention.add_argument('--task-json', type=Path, required=True)
    retention.add_argument('--floor-seconds', type=int, required=True)
    args = parser.parse_args(argv)
    try:
        if any(isinstance(value, Path) and not value.is_absolute() for value in vars(args).values()):
            raise ValueError('input_and_output_paths_must_be_absolute')
        if args.command == 'retention':
            return 0 if retention_eligible(read_json(args.task_json), args.floor_seconds,
                                           dt.datetime.now(dt.timezone.utc)) else 2
        result = prepare(args.inventory, args.history_exclusions, args.output_dir, args.max_candidates)
        print(json.dumps({'state': result['state'], 'selected': len(result['manifest'])}))
        return 2 if result['state'] == 'incomplete' else 0
    except (OSError, ValueError, TypeError, KeyError) as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
