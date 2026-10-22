#!/usr/bin/env bash
# Read-only packaging inspection; never imports runtime or executes skill helpers.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd -P)"
exec python3 "$SCRIPT_DIR/packaging_supervisor.py" run "verifier-${1:-}" python3 - "$SCRIPT_DIR/asset_evidence.py" "$@" <<'PYTHON'
import argparse
import hashlib
import json
import os
import plistlib
import re
import stat
import subprocess
import sys
import zipfile
from pathlib import Path

# Only the private helper adjacent to this selected verifier is imported.
ASSET_HELPER = sys.argv.pop(1)
notices = __import__('runpy').run_path(str(Path(ASSET_HELPER).with_name('manifest_notices.py')))

# Same 29 required resource members; exact originals at fresh main 7812caa6.
BASELINE = [{'path': 'LICENSE',
  'size': 11358,
  'sha256': 'cfc7749b96f63bd31c3c42b5c471bf756814053e847c10f3eb003417bc523d30',
  'git_mode': '100644',
  'posix_mode': '0644',
  'blob_oid': 'd645695673349e3947e8e5ae42332d0ac3164cd7',
  'prior_member': True},
 {'path': 'runtime/skills/README.md',
  'size': 997,
  'sha256': 'e70e2fa70d0a15f8ed6ed9f231ffc0fcd0bc904967bb477a9e25c57035b6748d',
  'git_mode': '100644',
  'posix_mode': '0644',
  'blob_oid': 'c54753667ef6b9d98b98bd59f7f09accbf55c6c7',
  'prior_member': True},
 {'path': 'runtime/skills/bundled/create-skill/SKILL.md',
  'size': 10114,
  'sha256': '624d00966e4fba54156d1c76f09fb780de673c30be55203c6733fcf7375050c7',
  'git_mode': '100644',
  'posix_mode': '0644',
  'blob_oid': '99c19ab116a6e131db7d756186481d31ca5ea5cc',
  'prior_member': True},
 {'path': 'runtime/skills/bundled/dream/SKILL.md',
  'size': 14118,
  'sha256': '91fd7d70f9a96a2e2802379c95df91ee001cd3f46febf67377d413ba4f6ae1fb',
  'git_mode': '100644',
  'posix_mode': '0644',
  'blob_oid': '1dbe6352f801e9b4124b4fb47d19701341e9a983',
  'prior_member': True},
 {'path': 'runtime/skills/bundled/jobs/SKILL.md',
  'size': 32002,
  'sha256': 'b12d8537d82284d0a37fefce4969077754bc27c1f4ba6ecc68efb92a399acd97',
  'git_mode': '100644',
  'posix_mode': '0644',
  'blob_oid': '8000371c8dd6f3f1b25276d88ece0affbab32a9c',
  'prior_member': True},
 {'path': 'runtime/skills/bundled/make-worktree/SKILL.md',
  'size': 7683,
  'sha256': '043f7c10ede6dc819c31948af32baa46558ad2ebf08d8d7961e5c1d581f192c4',
  'git_mode': '100644',
  'posix_mode': '0644',
  'blob_oid': 'e03aab54639a87e490a57db948526164e0c87d82',
  'prior_member': True},
 {'path': 'runtime/skills/bundled/make-worktree/worktree_guard.py',
  'size': 25604,
  'sha256': 'dd115fa2e23c2a446c7524ef3f726c69631129c1b4f65eab9f69234df13a3bab',
  'git_mode': '100644',
  'posix_mode': '0644',
  'blob_oid': 'eedfa92de82e9c304f7440c98d0b47ad0cc1306c',
  'prior_member': False},
 {'path': 'runtime/skills/bundled/manage-agent/SKILL.md',
  'size': 31293,
  'sha256': '5f1813f54321107a91cc7d3d8c29c6388830c8b4827335a9c77213929345bcd2',
  'git_mode': '100644',
  'posix_mode': '0644',
  'blob_oid': '6a29e7bd0a6b0efca64fb6270e82d9eeea17b972',
  'prior_member': True},
 {'path': 'runtime/skills/bundled/manage-repo/SKILL.md',
  'size': 3203,
  'sha256': '14a2924e76ef7c796c07122c166e51f6f2cddf905b9213eae8fdc1fd19d067a9',
  'git_mode': '100644',
  'posix_mode': '0644',
  'blob_oid': 'cd478bf73c3ebf053abfecd802a55ed5e1504ab3',
  'prior_member': True},
 {'path': 'runtime/skills/bundled/reflection/SKILL.md',
  'size': 4716,
  'sha256': '807d07fad177cd9047d839e6946344ad12db1ca67b18934911d626cbc5915946',
  'git_mode': '100644',
  'posix_mode': '0644',
  'blob_oid': 'fd5fb640513d1bec3ef06cfa083d6e4baac28cbd',
  'prior_member': True},
 {'path': 'runtime/skills/bundled/start-task/SKILL.md',
  'size': 32358,
  'sha256': '40d4fd4fa2ffe318cfbdb832cee39770c0adbc447712cc51c4f2628be1a97a01',
  'git_mode': '100644',
  'posix_mode': '0644',
  'blob_oid': '1f89b3a11c0d8835dc1a59b26e10c363e95c7dc5',
  'prior_member': True},
 {'path': 'runtime/skills/bundled/thread/SKILL.md',
  'size': 23922,
  'sha256': 'f67b78f87d25ec04f7cd92be909581a9968c89340cae0f115886403bf56d23c1',
  'git_mode': '100644',
  'posix_mode': '0644',
  'blob_oid': 'ede793ef2eb7d6a86d5a42d8d7ec5b5fa8716584',
  'prior_member': True},
 {'path': 'runtime/skills/bundled/todos/SKILL.md',
  'size': 26543,
  'sha256': 'cd262078043bbfabf74f5edd8e723a75d5658df51b4712527eaa76e6078c6945',
  'git_mode': '100644',
  'posix_mode': '0644',
  'blob_oid': '9eeaf04c8d84b78a794e448b453413704865d1b1',
  'prior_member': True},
 {'path': 'runtime/skills/bundled/workspace-cleanup/SKILL.md',
  'size': 57899,
  'sha256': '7fb6a0271328e79f68c9f139d5871b51195b06b3d97ea0a79dbd5bef2c14aa8d',
  'git_mode': '100644',
  'posix_mode': '0644',
  'blob_oid': '36661d861e303889ee66a3ee84fae15e0a139323',
  'prior_member': True},
 {'path': 'runtime/skills/bundled/workspace-cleanup/scripts/check_path_use.py',
  'size': 82540,
  'sha256': '6d39214e1b20c93d7899f6e54bcc0d4f9f657c59b76f9e6e059bf3047fbfe3d4',
  'git_mode': '100644',
  'posix_mode': '0644',
  'blob_oid': '4ba3223e72bdb7bd54ad97a305bfe9e6be5ebd7a',
  'prior_member': False},
 {'path': 'runtime/skills/bundled/workspace-cleanup/scripts/observe_cleanup_batch.py',
  'size': 43384,
  'sha256': 'b3626364938f21e06875878ea4d713d8aa3afe7c4dc8ece99928323bec905fdb',
  'git_mode': '100644',
  'posix_mode': '0644',
  'blob_oid': '8cf408fc474e45e9f44cdfb2f657c55a8ec72ac0',
  'prior_member': False},
 {'path': 'runtime/skills/bundled/workspace-cleanup/scripts/prepare_cleanup.py',
  'size': 36215,
  'sha256': '048b009d186e5acfc2ca033125f62597d6d3ac9bc60ff2cdbcbf77a09c98329c',
  'git_mode': '100644',
  'posix_mode': '0644',
  'blob_oid': '6d483e3b20b8b82dffbdc5ce39be5ec300796c8b',
  'prior_member': False},
 {'path': 'runtime/skills/bundled/workspace-cleanup/scripts/run_cleanup_batch.py',
  'size': 23349,
  'sha256': 'e524933bf62729f292faadbd3836a994a08d4cd98c09fab8b41154aeb631feac',
  'git_mode': '100644',
  'posix_mode': '0644',
  'blob_oid': '07847b5c9a52b4f42101dad44cef0330b270054e',
  'prior_member': False},
 {'path': 'runtime/skills/bundled/workspace-cleanup/scripts/run_cleanup_candidate.sh',
  'size': 58643,
  'sha256': '32808665c1a200b67af9eedee6cafa5155197dcf05fb22624c0ae919ba091a3b',
  'git_mode': '100755',
  'posix_mode': '0755',
  'blob_oid': '99f95780de48c71fb19ae0e12e7208cd11dd2952',
  'prior_member': True},
 {'path': 'runtime/skills/bundled/workspace-cleanup/scripts/worktree_safety.py',
  'size': 23134,
  'sha256': '63a2dc11e7c8c6748a1746b4f9fac2dffdf44895980234c8756c6fb5e4bea719',
  'git_mode': '100644',
  'posix_mode': '0644',
  'blob_oid': 'ead2c7e00e4e37b4f1b5aaca7c977be1361efbf1',
  'prior_member': False},
 {'path': 'runtime/skills/manage-agent/SKILL.md',
  'size': 6765,
  'sha256': '990afaf025e4ccf5412ec24a226b03c6657e17fb06dec7f3df10f6941366cd86',
  'git_mode': '100644',
  'posix_mode': '0644',
  'blob_oid': '0cde16e32c8cef07bda4f7d203ee03151a210f07',
  'prior_member': True},
 {'path': 'runtime/skills/manage-agent/skill.yaml',
  'size': 518,
  'sha256': '660f5dcd6567c4c942adfe0cee2536e7ddb23be6a271124d2ee1e94b12252b12',
  'git_mode': '100644',
  'posix_mode': '0644',
  'blob_oid': '6cfc61404a385ce0725d24f3fb6c36467b74189b',
  'prior_member': True},
 {'path': 'runtime/skills/manage-repo/SKILL.md',
  'size': 2451,
  'sha256': 'd1d50e9ce0cba365a6f369f3a4ebd5960505d7548fb5c3a299c67efbbca21c6b',
  'git_mode': '100644',
  'posix_mode': '0644',
  'blob_oid': '1ba97ab805327cd362188fb503e3bdcd28353e7d',
  'prior_member': True},
 {'path': 'runtime/skills/manage-repo/skill.yaml',
  'size': 503,
  'sha256': '0bdb6d5a067f891efaa96959eaaad6d2fa3659d9d41c5a0b0da3873290617561',
  'git_mode': '100644',
  'posix_mode': '0644',
  'blob_oid': '2123917f873e1f3f2bffcbadc12c40521f008b25',
  'prior_member': True},
 {'path': 'runtime/skills/reflection/SKILL.md',
  'size': 4716,
  'sha256': '807d07fad177cd9047d839e6946344ad12db1ca67b18934911d626cbc5915946',
  'git_mode': '100644',
  'posix_mode': '0644',
  'blob_oid': 'fd5fb640513d1bec3ef06cfa083d6e4baac28cbd',
  'prior_member': True},
 {'path': 'runtime/skills/reflection/skill.yaml',
  'size': 376,
  'sha256': '1e5da8233e72bddca513862232d950618aaba2e75da048553e4737d573a1b393',
  'git_mode': '100644',
  'posix_mode': '0644',
  'blob_oid': 'e877c075b6120ec51277283e3011e5aeddb2c82e',
  'prior_member': True},
 {'path': 'skills/README.md',
  'size': 613,
  'sha256': 'fa31cc0c372c943bad5c859102ecd3ef99144f60be2ced61e46b2d994d20b320',
  'git_mode': '100644',
  'posix_mode': '0644',
  'blob_oid': 'dd30ab57830a1b37977184c70eb1edec26b029c1',
  'prior_member': True},
 {'path': 'skills/happyranch/SKILL.md',
  'size': 22255,
  'sha256': '9f2ce3fbcc04833e6d7af60bc2e458f1382229b8774b89217cb8dd59855b97b3',
  'git_mode': '100644',
  'posix_mode': '0644',
  'blob_oid': '8419dfca4cbe7a63e0c54a81a6c9b6924a12c9c6',
  'prior_member': True},
 {'path': 'skills/happyranch/scripts/happyranch',
  'size': 727,
  'sha256': '8983ce21caab00a3de5a939c4bc6a12a075110fd7096cdf6be17ed983cd89d2b',
  'git_mode': '100755',
  'posix_mode': '0755',
  'blob_oid': '8a77db50cee8a7cb7f60b542f5c51f59c2dd8a7a',
  'prior_member': True}]
CATALOG = {'status': 'SOURCE_DERIVED_EXPECTATIONS_NOT_OBSERVED',
 'commands': ['BUNDLED_CLI skills catalog list --json',
              'BUNDLED_CLI skills catalog validate --json'],
 'preconditions': 'Relocated onedir, no --skills-root or --policy-path, no live policy; outside '
                  'checkout; default __file__ root must relocate.',
 'list_json': [{'id': 'hr:manage-agent',
                'name': 'Manage Agent',
                'version': '1.0.0',
                'description': 'Agent roster governance — enroll, update, or terminate agents, '
                               'manage system prompts, executors, repos, and allow-rules. '
                               'Enrollment requires founder approval.',
                'policy_class': 'high_impact_policy',
                'status': 'enabled',
                'owner': 'engineering_manager',
                'when_to_use': 'Use when managing the agent roster — enrolling a new agent, '
                               "updating an existing agent's system prompt/executor/repos, or "
                               'terminating an agent.'},
               {'id': 'hr:manage-repo',
                'name': 'Manage Repo',
                'version': '1.0.0',
                'description': 'Agent workspace repository configuration — add, remove, or update '
                               'repositories in AgentDef.repos (org/agents/<name>.md frontmatter) '
                               'and refresh executor workspace metadata.',
                'policy_class': 'high_impact_policy',
                'status': 'enabled',
                'owner': 'engineering_manager',
                'when_to_use': 'Use when managing agent workspace repositories — adding a new '
                               'repo, removing an existing repo, or updating a repo URL.'},
               {'id': 'hr:reflection',
                'name': 'Reflection',
                'version': '1.0.0',
                'description': 'Operational self-reflection workflow — recent work report, '
                               'learnings, optional KB capture, final thread reply.',
                'policy_class': 'standard_operational',
                'status': 'enabled',
                'owner': 'engineering_manager',
                'when_to_use': 'Use when asked mid-thread to reflect on your recent work.'}],
 'validate_json': {'skills_root': '<absolute relocated '
                                  'HappyRanch.app>/Contents/Resources/daemon/_internal/runtime/skills',
                   'policy_path': None,
                   'total_skills': 3,
                   'ids': ['hr:manage-agent', 'hr:manage-repo', 'hr:reflection'],
                   'warnings': [],
                   'errors': [],
                   'catalog_gate_failures': []},
 'comparison': 'Substitute only run-owned absolute app prefix; compare all keys/values and list '
               'order. No normalization that accepts a checkout root. Capture actual raw '
               'stdout/stderr and exit. Exit0 alone is insufficient.'}
EXPORTS = 'tsnet_init tsnet_start tsnet_close tsnet_dial tsnet_conn_dial tsnet_conn_read tsnet_conn_write tsnet_conn_close tsnet_local_addr tsnet_last_error tsnet_free_string'.split()


NATIVE_EVIDENCE_DIR = None
NATIVE_SYMBOL_STATE = None


def native_evidence(name, value, raw=False):
    if NATIVE_EVIDENCE_DIR is None:
        return
    # Explicit filenames, no-follow writes to this invocation's diagnostics.
    if name not in ('native-failure.json', 'native-symbols.json',
                    'native-members.json', 'native-nm.txt'):
        raise ValueError('NATIVE_EVIDENCE_NAME_UNSAFE')
    root = NATIVE_EVIDENCE_DIR
    if any(p.is_symlink() for p in (root, *root.parents)):
        raise ValueError('NATIVE_EVIDENCE_PATH_UNSAFE')
    fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        if os.fstat(fd).st_uid != os.getuid():
            raise ValueError('NATIVE_EVIDENCE_NOT_OWNED')
        data = value.encode('utf-8') if raw else (json.dumps(value, indent=2) + '\n').encode('utf-8')
        cap = 262144 if name in ('native-failure.json', 'native-symbols.json') else 8 * 1024 * 1024
        if len(data) > cap:
            data = (json.dumps({'error': 'NATIVE_EVIDENCE_CAP_EXCEEDED',
                                'original_size': len(data), 'retained_source_size': 0,
                                'omitted_bytes': len(data), 'source_sha256': hashlib.sha256(data).hexdigest(),
                                'complete': False}) + '\n').encode()
        dest = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=fd)
        with os.fdopen(dest, 'wb') as stream:
            stream.write(data)
    finally:
        os.close(fd)


def fail(code, detail):
    if NATIVE_EVIDENCE_DIR is not None:
        try:
            if NATIVE_SYMBOL_STATE is not None:
                native_evidence('native-symbols.json', NATIVE_SYMBOL_STATE)
            native_evidence('native-failure.json', {'error': code, 'stage': 'native',
                            'exit': 1, 'available': False, 'approved_url': None})
        except (OSError, ValueError):
            print('NATIVE_FAILURE_EVIDENCE_WRITE_REFUSED', file=sys.stderr)
    print(json.dumps({'error': code, 'detail': detail, 'available': False,
                      'approved_url': None}), file=sys.stderr)
    raise SystemExit(1)


def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def emit(value):
    print(json.dumps(value, sort_keys=True, indent=2))


def run(*argv):
    import runpy
    custody = runpy.run_path(ASSET_HELPER)
    p = custody['SUPERVISOR'].inspection_run(argv)
    if p.returncode:
        fail('INSPECTION_TOOL_FAILURE', {'argv': argv, 'exit': p.returncode,
                                        'stdout': p.stdout, 'stderr': p.stderr})
    return p.stdout


def safe_new(path, source=None):
    # Require an existing, owned parent. Reject every symlink ancestor, including
    # broken leaf links, before any build effect. No recursive mkdir or overwrite.
    if not path.is_absolute() or '..' in path.parts:
        fail('OUTPUT_PATH_UNSAFE', str(path))
    if any(ord(c) < 32 for c in str(path)):
        fail('OUTPUT_PATH_UNSAFE', str(path))
    for part in [path, *path.parents]:
        if part.is_symlink():
            fail('OUTPUT_SYMLINK', str(part))
    if path.exists():
        fail('OUTPUT_EXISTS', str(path))
    parent = path.parent
    if not parent.is_dir() or parent.stat().st_uid != os.getuid():
        fail('OUTPUT_PARENT_NOT_OWNED', str(parent))
    if not os.access(parent, os.W_OK):
        fail('OUTPUT_PARENT_NOT_WRITABLE', str(parent))
    home = Path.home().resolve()
    resolved = path.resolve()
    if resolved in (Path('/'), home, home / 'Desktop') or home / 'Desktop' in resolved.parents:
        fail('OUTPUT_PATH_UNSAFE', str(path))
    if source and (resolved == source or source in resolved.parents or resolved in source.parents):
        fail('OUTPUT_SOURCE_OVERLAP', str(path))
    return resolved


def source_identity(source, sha, tree):
    if not re.fullmatch('[0-9a-f]{40}', sha) or not re.fullmatch('[0-9a-f]{40}', tree):
        fail('SOURCE_IDENTITY_INVALID', [sha, tree])
    actual = run('git', '-C', str(source), 'rev-parse', 'HEAD').strip()
    actual_tree = run('git', '-C', str(source), 'rev-parse', 'HEAD^{tree}').strip()
    if actual != sha:
        fail('SOURCE_COMMIT_MISMATCH', [sha, actual])
    if actual_tree != tree:
        fail('SOURCE_TREE_MISMATCH', [tree, actual_tree])
    dirty = run('git', '-C', str(source), 'status', '--porcelain', '--untracked-files=all')
    if dirty:
        fail('SOURCE_NOT_CLEAN', dirty)


def member(row):
    path = row['path']
    return ('Contents/Resources/licenses/LICENSE' if path == 'LICENSE' else
            'Contents/Resources/daemon/_internal/' + path)


def inventory_identity(rows):
    rows.sort(key=lambda row: row['path'])
    encoded = (json.dumps(rows, ensure_ascii=False, sort_keys=True,
                          separators=(',', ':')) + '\n').encode('utf-8')
    return hashlib.sha256(encoded).hexdigest()


def inventory(app):
    rows = []
    for path in app.rglob('*'):
        info = path.lstat()
        row = {'path': path.relative_to(app).as_posix(),
               'mode': f'{stat.S_IMODE(info.st_mode):04o}'}
        if path.is_symlink():
            row.update(type='symlink', target=os.readlink(path))
            if not path.resolve().is_relative_to(app.resolve()):
                fail('APP_SYMLINK_ESCAPE', row)
        elif path.is_file():
            row.update(type='file', size=info.st_size, sha256=digest(path))
        elif path.is_dir():
            row.update(type='directory')
        else:
            fail('APP_MEMBER_TYPE', row)
        rows.append(row)
    identity = inventory_identity(rows)
    return {'definition': 'relative-path string order; UTF8 JSON path/type/mode/size/sha256/target; compact sorted keys; LF',
            'members': rows, 'inventory_sha256': identity}


def zip_inventory(archive):
    rows = []
    with zipfile.ZipFile(archive) as z:
        names = z.namelist()
        if len(names) != len(set(names)):
            fail('ARCHIVE_IDENTITY_MISMATCH', 'duplicate ZIP members')
        for entry in z.infolist():
            name = entry.filename
            if name == 'HappyRanch.app/':
                continue
            if not name.startswith('HappyRanch.app/') or '..' in Path(name).parts:
                fail('ARCHIVE_IDENTITY_MISMATCH', name)
            relative = name[len('HappyRanch.app/'):].rstrip('/')
            if not relative or Path(relative).as_posix() != relative or relative.startswith('/') or '\\' in relative:
                fail('ARCHIVE_IDENTITY_MISMATCH', name + ': noncanonical path')
            mode = entry.external_attr >> 16
            row = {'path': relative, 'mode': f'{stat.S_IMODE(mode):04o}'}
            if stat.S_ISDIR(mode):
                row['type'] = 'directory'
            elif stat.S_ISLNK(mode):
                target = z.read(entry).decode()
                row.update(type='symlink', target=target)
                if target.startswith('/'):
                    fail('ARCHIVE_IDENTITY_MISMATCH', name + ': absolute symlink')
                parts = list(Path(relative).parent.parts)
                for part in Path(target).parts:
                    if part == '..':
                        if not parts:
                            fail('ARCHIVE_IDENTITY_MISMATCH', name + ': symlink escape')
                        parts.pop()
                    elif part != '.':
                        parts.append(part)
            elif stat.S_ISREG(mode):
                row.update(type='file', size=entry.file_size, sha256=hashlib.sha256(z.read(entry)).hexdigest())
            else:
                fail('ARCHIVE_IDENTITY_MISMATCH', name + ': unknown type')
            rows.append(row)
    if len(rows) != len({r['path'] for r in rows}):
        fail('ARCHIVE_IDENTITY_MISMATCH', 'duplicate canonical paths')
    return inventory_identity(rows)


def resources(app, archive=None):
    rows, errors = [], []
    entries = None
    if archive:
        z = zipfile.ZipFile(archive)
        names = z.namelist()
        if len(names) != len(set(names)):
            fail('ARCHIVE_IDENTITY_MISMATCH', 'duplicate ZIP members')
        if any(not n.startswith('HappyRanch.app/') or '..' in Path(n).parts for n in names):
            fail('ARCHIVE_IDENTITY_MISMATCH', 'expected single HappyRanch.app root')
        entries = {i.filename: i for i in z.infolist()}
    for row in BASELINE:
        relative = member(row)
        path = app / relative
        identity = {'source': row['path'], 'app_member': relative,
                    'archive_member': 'HappyRanch.app/' + relative}
        if not path.is_file() or path.is_symlink() or not path.resolve().is_relative_to(app.resolve()):
            errors.append({'error': 'RESOURCE_MISSING', **identity})
            continue
        actual = {'size': path.stat().st_size, 'sha256': digest(path),
                  'posix_mode': f'{stat.S_IMODE(path.stat().st_mode):04o}'}
        if any(actual[k] != row[k] for k in actual):
            errors.append({'error': 'RESOURCE_MISMATCH', **identity,
                           'expected': row, 'actual': actual})
        if entries is not None:
            entry = entries.get(identity['archive_member'])
            if entry is None:
                errors.append({'error': 'RESOURCE_MISSING', **identity})
            else:
                mode = (entry.external_attr >> 16) & 0o7777
                if (mode != int(row['posix_mode'], 8) or entry.file_size != row['size']
                        or hashlib.sha256(z.read(entry)).hexdigest() != row['sha256']
                        or stat.S_IFMT(entry.external_attr >> 16) != stat.S_IFREG):
                    errors.append({'error': 'RESOURCE_MISMATCH', **identity})
        rows.append({**identity, **actual})
    if errors:
        fail('RESOURCE_VERIFICATION_FAILED', errors)
    return {'resources': rows, 'ordinary_reader_execution': 'UNEXECUTED',
            'app_root': str(app.resolve()),
            'onedir_root': str((app / 'Contents/Resources/daemon').resolve()),
            'calculated_ordinary_root': str((app / 'Contents/Resources/daemon/_internal/runtime/skills/bundled').resolve())}


def native_location(value, loader_dir, executable_dir):
    for token, base in (('@loader_path', loader_dir),
                        ('@executable_path', executable_dir)):
        if value == token or value.startswith(token + '/'):
            path = base / value[len(token):].lstrip('/')
            break
    else:
        path = Path(value)
        if not path.is_absolute():
            return None
    try:
        return path.resolve()
    except (OSError, RuntimeError):
        return None


def native_system_path(path):
    return path is not None and any(path == root or root in path.parents
                                   for root in (Path('/usr/lib'), Path('/System/Library')))


def native_archive_member(origin, archive, members):
    # Apple may insert a numeric member index. It is syntax only, not an
    # independently proven ar ordinal. Preserve and match the complete origin.
    match = re.fullmatch(re.escape(str(archive)) + r'(?:\[[0-9]+\])?\(([^()]+)\)', origin)
    return match[1] if match and match[1] in members else None


def native(app, archive, link_map):
    global NATIVE_SYMBOL_STATE
    # Initial all11 rows also survive an inspection tool failure before nm/map.
    observations = [{'export': export, 'observation': 'NOT_OBSERVED'} for export in EXPORTS]
    NATIVE_SYMBOL_STATE = {'observations': observations, 'required_exports': EXPORTS,
                           'go_archive_path': str(archive), 'link_map_path': str(link_map),
                           'available': False, 'approved_url': None}
    if sys.platform != 'darwin':
        fail('NATIVE_VENUE_UNAVAILABLE', sys.platform)
    binary = app / 'Contents/MacOS/HappyRanch'
    symbols = run('nm', '-m', str(binary))
    native_evidence('native-nm.txt', symbols, raw=True)
    text = link_map.read_text()
    objects = {}
    for object_id, origin in re.findall(r'^\[[ \t]*([0-9]+)\][ \t]+(.+)$', text, re.M):
        objects.setdefault(str(int(object_id)), []).append(origin)
    ambiguous_objects = any(len(origins) != 1 for origins in objects.values())
    linked, errors = [], []
    members = run('ar', '-t', str(archive)).splitlines()
    ambiguous_members = len(members) != len(set(members))
    for index, export in enumerate(EXPORTS):
        definitions = [line for line in symbols.splitlines() if re.search(r'\b_' + export + r'$', line)]
        symbol_rows = [line for line in text.splitlines() if re.search(r'[ \t]_' + export + r'$', line)]
        matches = re.findall(r'^\S+[ \t]+\S+[ \t]+\[[ \t]*([0-9]+)\][ \t]+_' + export + r'$', text, re.M)
        origin = [o for i in matches for o in objects.get(str(int(i)), [''])]
        association_errors = []
        if ambiguous_members:
            association_errors.append('ARCHIVE_MEMBER_AMBIGUOUS')
        if ambiguous_objects:
            association_errors.append('LINK_MAP_OBJECT_AMBIGUOUS')
        if len(symbol_rows) != 1 or len(matches) != 1 or len(origin) != 1:
            association_errors.append('LINK_MAP_SYMBOL_ASSOCIATION_AMBIGUOUS_OR_MISSING')
        if len(origin) == 1 and native_archive_member(origin[0], archive, members) is None:
            association_errors.append('LINK_MAP_ARCHIVE_MEMBER_UNPROVEN')
        real = origin if not association_errors else []
        if len(definitions) != 1 or 'weak' in definitions[0].lower() or '(undefined)' in definitions[0].lower() or ' external ' not in definitions[0] or association_errors:
            errors.append({'export': export, 'nm': definitions, 'map_origins': origin})
        linked.append({'export': export, 'nm': definitions, 'archive_objects': real})
        # Summary stays independent of nm/raw/map/member bulk budgets. Preserve
        # counts and explicit row/field loss if an anomalous symbol fans out.
        def selected(values):
            return {'rows': [v[:256] for v in values[:4]], 'original_rows': len(values),
                    'omitted_rows': max(0, len(values) - 4),
                    'omitted_characters': sum(max(0, len(v) - 256) for v in values[:4]),
                    'complete': len(values) <= 4 and all(len(v) <= 256 for v in values)}
        observations[index] = {'export': export, 'observation': 'OBSERVED',
                               'nm': selected(definitions), 'map_origins': selected(origin),
                               'archive_objects': selected(real),
                               'association_errors': association_errors,
                               'strong_member_backed': not any(e['export'] == export for e in errors)}
    native_evidence('native-symbols.json', NATIVE_SYMBOL_STATE)
    NATIVE_SYMBOL_STATE = None
    archive_rows = []
    if ambiguous_members:
        # ar -p cannot uniquely attribute duplicate names. Keep all eleven
        # observations and the raw listing, without claiming member hashes.
        native_evidence('native-members.json', {'archive_path': str(archive),
                        'archive_sha256': digest(archive), 'members': [],
                        'ar_members': members, 'member_hashes': 'UNPROVEN_DUPLICATE_NAMES',
                        'link_map_sha256': digest(link_map), 'link_objects': objects})
        fail('ARCHIVE_MEMBER_AMBIGUOUS', members)
    for name in members:
        data = subprocess.run(['ar', '-p', str(archive), name], capture_output=True, check=True).stdout
        archive_rows.append({'member': name, 'size': len(data), 'sha256': hashlib.sha256(data).hexdigest()})
    native_evidence('native-members.json', {'archive_path': str(archive),
                    'archive_sha256': digest(archive), 'members': archive_rows,
                    'link_map_sha256': digest(link_map), 'link_objects': objects})
    if errors:
        fail('TSNET_STRONG_LINK_UNPROVEN', {'symbols': errors,
                                          'raw_nm_evidence': 'native-nm.txt',
                                          'proposed_delta': 'All eleven strong archive-member definitions remain required; return linkage failure to manager.'})
    components = []
    for path in sorted(app.rglob('*')):
        if not path.is_file() or path.is_symlink():
            continue
        description = run('file', '-b', str(path))
        if 'Mach-O' not in description:
            continue
        slices = run('lipo', '-archs', str(path)).strip().split()
        loads = run('otool', '-arch', 'arm64', '-l', str(path))
        deps = run('otool', '-arch', 'arm64', '-L', str(path))
        minima = re.findall(r'^\s+(?:minos|version)\s+(\d+\.\d+(?:\.\d+)?)', loads, re.M)
        rpaths = re.findall(r'cmd LC_RPATH\s+cmdsize \d+\s+path (.+?) \(offset', loads)
        if 'arm64' not in slices or not minima:
            fail('NATIVE_COMPONENT_INCOMPATIBLE', str(path))
        exe_dir = path.parent if os.access(path, os.X_OK) and path.name in ('HappyRanch', 'happyranch', 'happyranch-daemon') else (
            app / 'Contents/Resources/daemon' if 'daemon' in path.parts else binary.parent)
        # Validate every destination, including rpaths no dependency uses.
        # Token-relative paths must stay in the bundle after symlink resolution.
        rpath_locations = []
        for rp in rpaths:
            location = native_location(rp, path.parent, exe_dir)
            if location is None or not (location.is_relative_to(app.resolve())
                    or (rp.startswith('/') and native_system_path(location))):
                fail('NATIVE_RPATH_UNRESOLVED', [str(path), rp])
            rpath_locations.append(location)
        component_deps = []
        for dep in re.findall(r'^\s+(.+?) \(compatibility version', deps, re.M):
            location = native_location(dep, path.parent, exe_dir)
            if dep.startswith('/') and native_system_path(location):
                component_deps.append({'path': dep, 'disposition': 'HOST_SYSTEM_REFERENCE'})
                continue
            candidates = ([rp / dep[len('@rpath/'):] for rp in rpath_locations]
                          if dep.startswith('@rpath/') else ([location] if location is not None else []))
            resolved = [p.resolve() for p in candidates if p.exists() and p.resolve().is_relative_to(app.resolve())]
            if not resolved:
                fail('NATIVE_DEPENDENCY_UNRESOLVED', [str(path), dep])
            component_deps.append({'path': dep, 'resolved': [str(p) for p in resolved]})
        components.append({'path': str(path.relative_to(app)), 'sha256': digest(path),
                           'slices': slices, 'minimum_load_commands': minima,
                           'rpaths': rpaths, 'dependencies': component_deps,
                           'file_raw': description, 'otool_load_raw': loads, 'otool_deps_raw': deps})
    plist = plistlib.loads((app / 'Contents/Info.plist').read_bytes())
    minimum = tuple(map(int, plist['LSMinimumSystemVersion'].split('.'))) + (0,)
    minimum = minimum[:3]
    if any((tuple(map(int, v.split('.'))) + (0,))[:3] > minimum for c in components for v in c['minimum_load_commands']):
        fail('MINIMUM_OS_MISMATCH', {'declared': plist['LSMinimumSystemVersion'], 'components': components})
    archive_arch = run('lipo', '-archs', str(archive))
    archive_load = run('otool', '-arch', 'arm64', '-l', str(archive))
    archive_minima = re.findall(r'^\s+(?:minos|version)\s+(\d+\.\d+(?:\.\d+)?)', archive_load, re.M)
    if 'arm64' not in archive_arch.split() or any((tuple(map(int, v.split('.'))) + (0,))[:3] > minimum for v in archive_minima):
        fail('GO_ARCHIVE_INCOMPATIBLE', {'architecture': archive_arch, 'minimums': archive_minima})
    return {'components': components, 'tsnet': linked, 'nm_raw': symbols,
            'go_archive_sha256': digest(archive), 'archive_members': archive_rows,
            'archive_arch_raw': archive_arch,
            'archive_load_raw': archive_load,
            'link_map_sha256': digest(link_map), 'minimum_os_execution': 'UNEXECUTED'}


p = argparse.ArgumentParser(description=__doc__)
s = p.add_subparsers(dest='mode', required=True)
q = s.add_parser('preflight')
for key in ('version', 'build', 'source-sha', 'source-tree', 'arch', 'output-dir', 'source'):
    q.add_argument('--' + key, required=True)
q = s.add_parser('resources'); q.add_argument('--app', type=Path, required=True); q.add_argument('--archive', type=Path)
q = s.add_parser('inventory'); q.add_argument('--app', type=Path, required=True)
q = s.add_parser('catalog'); q.add_argument('--app', type=Path, required=True)
q.add_argument('--list-json', type=Path, required=True); q.add_argument('--validate-json', type=Path, required=True)
q = s.add_parser('trust'); q.add_argument('--app', type=Path, required=True)
q = s.add_parser('native'); q.add_argument('--app', type=Path, required=True)
q.add_argument('--go-archive', type=Path, required=True); q.add_argument('--link-map', type=Path, required=True)
q.add_argument('--evidence-dir', type=Path, help='Owned task diagnostics only; small failure/all11 summaries plus separate raw evidence')
q = s.add_parser('archive')
q.add_argument('--app', type=Path, required=True); q.add_argument('--archive', type=Path, required=True)
q.add_argument('--run-id', required=True)
q = s.add_parser('reconcile'); q.add_argument('--archive', type=Path, required=True)
q.add_argument('--receipt', type=Path, required=True)
q.add_argument('--transform-chain', type=Path, help='Read-only signed/submission/notary/staple/final relationship comparison; never public admission')
for key in ('version', 'build', 'source-sha', 'source-tree', 'arch', 'run-id'):
    q.add_argument('--' + key, required=True)
# Keep asset options closed and explicitly single-valued. Ordinary argparse
# store actions accept duplicates; these options must refuse them instead.
asset = __import__('runpy').run_path(ASSET_HELPER)
q = s.add_parser('licenses', allow_abbrev=False)
asset['add_arguments'](q, expectations=True)
freezer = __import__('runpy').run_path(str(Path(ASSET_HELPER).with_name('freezer_evidence.py')))
freezer['add_arguments'](q)
web_custody = __import__('runpy').run_path(str(Path(ASSET_HELPER).with_name('web_evidence.py')))
web_custody['add_arguments'](q)
q.add_argument('--mapping', type=Path, required=True, action=asset['Once'])
a = p.parse_args()
if a.mode == 'native':
    NATIVE_EVIDENCE_DIR = a.evidence_dir
try:
    if a.mode == 'preflight':
        for key in ('version', 'build'):
            if not re.fullmatch(r'(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)', getattr(a, key)):
                fail('RELEASE_' + key.upper() + '_INVALID', getattr(a, key))
        if a.arch != 'arm64':
            fail('RELEASE_ARCH_UNSUPPORTED', a.arch)
        source = Path(a.source).resolve()
        source_identity(source, a.source_sha, a.source_tree)
        output = safe_new(Path(a.output_dir), source)
        emit({'source_sha': a.source_sha, 'source_tree': a.source_tree,
              'version': a.version, 'build': a.build, 'arch': a.arch, 'output': str(output),
              'preflight': 'VALIDATED_NO_BUILD_EFFECTS'})
    elif a.mode == 'resources':
        emit(resources(a.app, a.archive))
    elif a.mode == 'inventory':
        if not a.app.is_dir() or a.app.is_symlink():
            fail('APP_MISSING', str(a.app))
        emit(inventory(a.app))
    elif a.mode == 'catalog':
        expected = dict(CATALOG['validate_json'])
        expected['skills_root'] = str((a.app.resolve() / 'Contents/Resources/daemon/_internal/runtime/skills'))
        actual_list = json.loads(a.list_json.read_text()); actual_validate = json.loads(a.validate_json.read_text())
        if actual_list != CATALOG['list_json'] or actual_validate != expected:
            fail('CATALOG_MISMATCH', {'list': actual_list, 'validate': actual_validate, 'expected_validate': expected})
        emit({'catalog': actual_list, 'validation': actual_validate})
    elif a.mode == 'native':
        emit(native(a.app, a.go_archive, a.link_map))
    elif a.mode == 'trust':
        if sys.platform != 'darwin':
            fail('NATIVE_VENUE_UNAVAILABLE', sys.platform)
        def observation(argv):
            try:
                result = subprocess.run(argv, capture_output=True, text=True, timeout=60,
                                        env={**os.environ, 'MANPAGER': 'cat', 'PAGER': 'cat'})
                return {'argv': argv, 'exit': result.returncode, 'stdout': result.stdout, 'stderr': result.stderr}
            except (OSError, subprocess.TimeoutExpired) as exc:
                return {'argv': argv, 'exit': None, 'error': str(exc), 'classification': 'INSPECTION_TOOL_FAILURE'}
        manual = observation(['man', 'spctl'])
        display = observation(['codesign', '-dvvv', str(a.app)])
        outer = observation(['codesign', '--verify', '--strict', '--verbose=4', str(a.app)])
        policy = observation(['spctl', '--assess', '--type', 'execute', '--verbose=4', str(a.app)])
        nested = []
        for path in sorted(a.app.rglob('*')):
            if path.is_file() and not path.is_symlink() and 'Mach-O' in run('file', '-b', str(path)):
                nested.append(observation(['codesign', '--verify', '--strict', '--verbose=4', str(path)]))
        diagnostic = policy.get('stdout', '') + policy.get('stderr', '')
        classification = ('POLICY_REJECTION_RECORDED' if policy.get('exit') == 3 and 'rejected' in diagnostic.lower()
                          else 'ASSESSMENT_ACCEPTANCE_RECORDED' if policy.get('exit') == 0 and 'accepted' in diagnostic.lower()
                          else 'ASSESSMENT_UNRESOLVED')
        emit({'spctl_manual': manual, 'codesign_display': display, 'outer_integrity': outer,
              'nested_integrity': nested, 'spctl_assessment': policy,
              'classification': classification, 'installed_manual_semantics_review': 'REQUIRED',
              'signature_notary_staple_acceptance': 'UNPROVEN', 'available': False, 'approved_url': None})
    elif a.mode == 'archive':
        notice_receipt = notices['validate'](a.app)
        if notices['validate_archive'](a.archive) != notice_receipt:
            fail('MANIFEST_NOTICE_ARCHIVE_MISMATCH', 'candidate and archive notice receipts differ')
        resources(a.app, a.archive)
        with zipfile.ZipFile(a.archive) as z:
            inv = inventory(a.app)
            for row in inv['members']:
                name = 'HappyRanch.app/' + row['path'] + ('/' if row['type'] == 'directory' else '')
                entry = z.getinfo(name)
                if (entry.external_attr >> 16) & 0o7777 != int(row['mode'], 8):
                    fail('ARCHIVE_IDENTITY_MISMATCH', name + ': mode')
                if row['type'] == 'file' and hashlib.sha256(z.read(entry)).hexdigest() != row['sha256']:
                    fail('ARCHIVE_IDENTITY_MISMATCH', name + ': bytes')
                if row['type'] == 'symlink' and z.read(entry).decode() != row['target']:
                    fail('ARCHIVE_IDENTITY_MISMATCH', name + ': symlink')
            expected_names = {'HappyRanch.app/' + r['path'] + ('/' if r['type'] == 'directory' else '') for r in inv['members']}
            if set(z.namelist()) - {'HappyRanch.app/'} != expected_names:
                fail('ARCHIVE_IDENTITY_MISMATCH', 'extra or absent members')
        info = plistlib.loads((a.app / 'Contents/Info.plist').read_bytes())
        emit({'role': 'unsigned-inner-zip', 'filename': a.archive.name,
              'size': a.archive.stat().st_size, 'sha256': digest(a.archive),
              'source_sha': info['GitCommitSHA'], 'source_tree': info['GitTreeSHA'],
              'version': info['CFBundleShortVersionString'], 'build': info['CFBundleVersion'],
              'arch': info['ReleaseArchitecture'], 'run_id': a.run_id,
              'app_inventory_sha256': inv['inventory_sha256'], 'manifest_notices': notice_receipt, 'available': False,
              'approved_url': None, 'signing': 'UNEXECUTED', 'ordinary_reader_execution': 'UNEXECUTED'})
    elif a.mode == 'reconcile':
        if not a.archive.is_file() or not a.receipt.is_file():
            fail('RELEASE_EVIDENCE_MISSING', [str(a.archive), str(a.receipt)])
        receipt = json.loads(a.receipt.read_text())
        notice_receipt = notices['validate_archive'](a.archive)
        if receipt.get('manifest_notices') != notice_receipt:
            fail('MANIFEST_NOTICE_RECEIPT_MISMATCH', 'archived notice inventory differs from release receipt')
        errors = []
        expected_name = f'HappyRanch-{a.version}-{a.build}-{a.arch}.zip'
        fields = {'size': (a.archive.stat().st_size, 'ARCHIVE_SIZE_MISMATCH'),
                  'app_inventory_sha256': (zip_inventory(a.archive), 'ARCHIVE_IDENTITY_MISMATCH'),
                  'sha256': (digest(a.archive), 'ARCHIVE_HASH_MISMATCH'),
                  'source_sha': (a.source_sha, 'SOURCE_COMMIT_MISMATCH'),
                  'source_tree': (a.source_tree, 'SOURCE_TREE_MISMATCH'),
                  'version': (a.version, 'RELEASE_VERSION_MISMATCH'),
                  'build': (a.build, 'RELEASE_BUILD_MISMATCH'),
                  'arch': (a.arch, 'RELEASE_ARCH_MISMATCH'),
                  'run_id': (a.run_id, 'ARCHIVE_IDENTITY_MISMATCH'),
                  'filename': (expected_name, 'ARCHIVE_IDENTITY_MISMATCH'),
                  'role': ('final-inner-zip' if a.transform_chain else 'unsigned-inner-zip', 'INNER_ARCHIVE_RECEIPT_MISMATCH')}
        if a.archive.name != expected_name:
            errors.append({'error': 'ARCHIVE_IDENTITY_MISMATCH', 'actual': a.archive.name, 'expected': expected_name})
        for field, (expected, code) in fields.items():
            if receipt.get(field) != expected:
                errors.append({'error': code, 'field': field, 'actual': receipt.get(field), 'expected': expected})
        with zipfile.ZipFile(a.archive) as z:
            info = plistlib.loads(z.read('HappyRanch.app/Contents/Info.plist'))
        for field, key in [('source_sha', 'GitCommitSHA'), ('source_tree', 'GitTreeSHA'),
                           ('version', 'CFBundleShortVersionString'), ('build', 'CFBundleVersion'), ('arch', 'ReleaseArchitecture')]:
            if info.get(key) != fields[field][0]:
                errors.append({'error': fields[field][1], 'field': 'archived_plist.' + key})
        if a.transform_chain:
            chain = json.loads(a.transform_chain.read_text())
            unsigned = chain.get('unsigned_app', {})
            signed = chain.get('signed_app', {})
            submission = chain.get('submission_zip', {})
            notary = chain.get('notary', {})
            stapled = chain.get('stapled_app', {})
            final = chain.get('final_zip', {})
            def relation(ok, code, field):
                if not ok:
                    errors.append({'error': code, 'field': field})
            for name, stage in [('unsigned_app', unsigned), ('signed_app', signed),
                                ('submission_zip', submission), ('notary', notary),
                                ('stapled_app', stapled), ('final_zip', final)]:
                relation(stage.get('source_sha') == a.source_sha and stage.get('source_tree') == a.source_tree,
                         'SIGNING_CHAIN_MISMATCH', name + '.source_identity')
                relation(stage.get('version') == a.version and stage.get('build') == a.build,
                         'SIGNING_CHAIN_MISMATCH', name + '.release_identity')
            for name, stage in [('unsigned_app', unsigned), ('signed_app', signed), ('stapled_app', stapled)]:
                relation(bool(re.fullmatch('[0-9a-f]{64}', str(stage.get('inventory_sha256', '')))),
                         'SIGNING_CHAIN_MISMATCH', name + '.inventory_sha256')
            relation(signed.get('unsigned_app_inventory_sha256') == unsigned.get('inventory_sha256')
                     and signed.get('unsigned_app_inventory_sha256') is not None,
                     'SIGNING_CHAIN_MISMATCH', 'signed_app.unsigned_app_inventory_sha256')
            relation(signed.get('inventory_sha256') != unsigned.get('inventory_sha256'),
                     'SIGNING_CHAIN_MISMATCH', 'signed_app.transformed_inventory')
            relation(stapled.get('inventory_sha256') != signed.get('inventory_sha256'),
                     'STAPLING_CHAIN_MISMATCH', 'stapled_app.transformed_inventory')
            relation(signed.get('signature_verified') is True and bool(signed.get('team_id'))
                     and bool(signed.get('certificate_fingerprint')),
                     'SIGNING_CHAIN_MISMATCH', 'signed_app.signature_metadata')
            relation(submission.get('signed_app_inventory_sha256') == signed.get('inventory_sha256')
                     and submission.get('signed_app_inventory_sha256') is not None,
                     'SIGNING_CHAIN_MISMATCH', 'submission_zip.signed_app_inventory_sha256')
            relation(bool(re.fullmatch('[0-9a-f]{64}', str(submission.get('sha256', ''))))
                     and type(submission.get('size')) is int and submission['size'] > 0,
                     'NOTARY_ARTIFACT_MISMATCH', 'submission_zip.bytes')
            relation(notary.get('status') == 'Accepted' and bool(notary.get('uuid'))
                     and bool(re.fullmatch('[0-9a-f]{64}', str(notary.get('log_sha256', ''))))
                     and notary.get('submission_sha256') == submission.get('sha256')
                     and notary.get('submission_size') == submission.get('size')
                     and notary.get('signed_app_inventory_sha256') == signed.get('inventory_sha256'),
                     'NOTARY_ARTIFACT_MISMATCH', 'notary.submission_binding')
            relation(stapled.get('signed_app_inventory_sha256') == signed.get('inventory_sha256')
                     and stapled.get('notary_uuid') == notary.get('uuid')
                     and stapled.get('validated') is True,
                     'STAPLING_CHAIN_MISMATCH', 'stapled_app.ticket_binding')
            relation(final.get('stapled_app_inventory_sha256') == stapled.get('inventory_sha256')
                     and final.get('stapled_app_inventory_sha256') is not None
                     and receipt.get('app_inventory_sha256') == stapled.get('inventory_sha256'),
                     'STAPLING_CHAIN_MISMATCH', 'final_zip.stapled_app_inventory_sha256')
            relation(final.get('sha256') == digest(a.archive) and final.get('size') == a.archive.stat().st_size
                     and final.get('filename') == a.archive.name and final.get('run_id') == a.run_id,
                     'STAPLING_CHAIN_MISMATCH', 'final_zip.actual_inner_bytes')
        if errors:
            fail('RECEIPT_RECONCILIATION_FAILED', errors)
        emit({'local_reconciliation': 'MATCHED_TRANSFORM_RELATIONS' if a.transform_chain else 'MATCHED_UNSIGNED_INNER_BYTES', 'available': False,
              'approved_url': None, 'signing_chain': 'SUPPLIED_RELATIONS_ONLY' if a.transform_chain else 'UNEXECUTED', 'public_admission_owner': 'TASK-11196'})
    elif a.mode == 'licenses':
        asset['verify_arguments'](a)
        freezer['verify_arguments'](a)
        web_custody['verify_arguments'](a)
        a.app = Path(a.app)
        mapping = json.loads(a.mapping.read_text())
        errors = []
        for row in mapping['components']:
            if row['disposition'] != 'RETAINED_TEXT':
                errors.append(row)
                continue
            if not row.get('notices'):
                errors.append({'error': 'LICENSE_MISSING', 'component': row['component'], 'reason': 'no retained texts'})
            for notice in row['notices']:
                relative = Path(notice['destination'])
                if relative.is_absolute() or '..' in relative.parts or relative.parts[:3] != ('Contents', 'Resources', 'licenses'):
                    fail('LICENSE_PATH_UNSAFE', str(relative))
                path = a.app / relative
                if not path.resolve().is_relative_to(a.app.resolve()) or path.is_symlink() or not path.is_file() or digest(path) != notice['sha256']:
                    errors.append({'error': 'LICENSE_MISSING', 'component': row['component'], 'notice': notice})
        if not mapping['components'] or errors:
            fail('MANIFEST_NOTICE_ORIGINALS_MISSING', {'components': errors, 'remediation': 'restore exact owner original texts and recollect'})
        emit(notices['validate'](a.app, Path(a.exported_source), Path(a.output_dir), mapping))

except (OSError, ValueError, KeyError, zipfile.BadZipFile, subprocess.SubprocessError) as exc:
    fail('INSPECTION_INPUT_OR_TOOL_FAILURE', str(exc))
PYTHON
