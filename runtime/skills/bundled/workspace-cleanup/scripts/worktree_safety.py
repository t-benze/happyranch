"""Shared worktree preservation and path safety; no task selection or scheduling.

Literal CLI adapters serve the manual runner and the root-terminal owner. Unknown
or capped evidence refuses. Snapshots protect against accidental same-user races,
not a hostile writer in the final pathname syscall window.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import selectors
import stat
import subprocess
import sys
import tempfile
import time

OUTPUT_CAP = 1048576
SHA = re.compile(r"[0-9a-f]{40}")


class Budget:
    def __init__(self, seconds: float):
        self.deadline = time.monotonic() + min(5.0, seconds)

    def check(self):
        if time.monotonic() >= self.deadline:
            raise TimeoutError("safety_deadline")

    def command(self, args, cwd):
        self.check()
        # The caller owns this helper's process group. Children inherit it,
        # so the root owner's TERM/KILL/reap also covers Git/gh descendants.
        process = subprocess.Popen(args, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        output = [bytearray(), bytearray()]
        selector = selectors.DefaultSelector()
        for index, stream in enumerate((process.stdout, process.stderr)):
            os.set_blocking(stream.fileno(), False)
            selector.register(stream, selectors.EVENT_READ, index)
        try:
            while selector.get_map():
                self.check()
                for key, _ in selector.select(min(0.05, max(0, self.deadline-time.monotonic()))):
                    chunk = os.read(key.fd, 65536)
                    if not chunk:
                        selector.unregister(key.fileobj)
                    else:
                        output[key.data].extend(chunk)
                        if sum(map(len, output)) > OUTPUT_CAP:
                            raise RuntimeError("subprocess_output_cap")
            code = process.wait(timeout=max(0.001, self.deadline-time.monotonic()))
            return subprocess.CompletedProcess(args, code, output[0].decode(), output[1].decode())
        finally:
            selector.close()
            if process.poll() is None:
                process.kill()
                process.wait(timeout=0.5)
            process.stdout.close()
            process.stderr.close()

    def required(self, args, cwd):
        result = self.command(args, cwd)
        if result.returncode:
            raise RuntimeError("external_evidence_unavailable")
        return result.stdout.strip()

    def json(self, args, cwd):
        return json.loads(self.required(args, cwd))


def snapshot_tree(candidate, containing, primary, workspace, destination, budget):
    candidate, containing, primary, workspace, destination = map(
        os.path.abspath, (candidate, containing, primary, workspace, destination))
    uid = os.getuid()
    cap = 200000

    def mountpoints():
        result = set()
        try:
            with open("/proc/self/mountinfo", encoding="utf-8") as handle:
                for line in handle:
                    budget.check()
                    fields = line.split()
                    if len(fields) < 5:
                        raise RuntimeError("malformed_mountinfo")
                    value = re.sub(r"\\([0-7]{3})", lambda m: chr(int(m.group(1), 8)), fields[4])
                    result.add(os.path.abspath(value))
            return result
        except FileNotFoundError:
            proc = budget.command(["mount"], primary)
            if proc.returncode:
                raise RuntimeError("mount_table_unavailable")
            for line in proc.stdout.splitlines():
                match = re.search(r" on (.+?) \(", line)
                if not match:
                    raise RuntimeError("malformed_mount_table")
                result.add(os.path.abspath(match.group(1)))
            return result

    def identity(path):
        value = os.lstat(path)
        return [value.st_dev, value.st_ino, value.st_mode, value.st_uid]

    def beneath(path, root):
        path = os.path.realpath(path)
        root = os.path.realpath(root)
        return path == root or path.startswith(root.rstrip(os.sep) + os.sep)

    def accepted_uv_interpreter(path, relative):
        bin_dir = os.path.dirname(path)
        venv = os.path.dirname(bin_dir)
        if (os.path.basename(bin_dir) != "bin"
                or os.path.basename(venv) != ".venv"
                or re.fullmatch(r"python(?:3(?:\.[0-9]+)?)?", os.path.basename(path)) is None):
            raise RuntimeError("external_or_protected_symlink")
        config = os.path.join(venv, "pyvenv.cfg")
        config_stat = os.lstat(config)
        if (not stat.S_ISREG(config_stat.st_mode) or stat.S_ISLNK(config_stat.st_mode)
                or config_stat.st_uid != uid):
            raise RuntimeError("external_or_protected_symlink")
        homes = []
        with open(config, encoding="utf-8") as handle:
            for line in handle:
                key, separator, value = line.partition("=")
                if separator and key.strip().lower() == "home":
                    homes.append(value.strip())
        if len(homes) != 1 or not homes[0] or not os.path.isabs(os.path.expanduser(homes[0])):
            raise RuntimeError("external_or_protected_symlink")
        resolved = os.path.realpath(path)
        try:
            target = os.stat(resolved)
        except OSError as exc:
            raise RuntimeError("external_or_protected_symlink") from exc
        if not stat.S_ISREG(target.st_mode):
            raise RuntimeError("external_or_protected_symlink")
        configured_store = os.environ.get("UV_PYTHON_INSTALL_DIR")
        if configured_store:
            uv_store = os.path.realpath(os.path.abspath(os.path.expanduser(configured_store)))
        else:
            data_home = os.environ.get("XDG_DATA_HOME") or os.path.join(
                os.path.expanduser("~"), ".local", "share",
            )
            uv_store = os.path.realpath(os.path.abspath(os.path.join(data_home, "uv", "python")))
        interpreter_home = os.path.realpath(os.path.expanduser(homes[0]))
        if not (beneath(resolved, uv_store) or beneath(resolved, interpreter_home)):
            raise RuntimeError("external_or_protected_symlink")
        forbidden = [candidate, containing, primary, workspace, *protected_paths]
        if any(beneath(resolved, item) for item in forbidden):
            raise RuntimeError("external_or_protected_symlink")
        return [
            relative, os.readlink(path), resolved, target.st_dev, target.st_ino,
            target.st_mode, target.st_uid, target.st_size,
        ]

    protected_paths = [
        workspace,
        os.path.join(workspace, "repos"),
        primary,
        os.path.join(primary, ".git"),
        os.path.join(workspace, "output"),
        os.path.join(workspace, ".happyranch"),
        os.path.join(workspace, ".agents"),
        os.path.join(workspace, ".claude"),
    ]
    if containing != candidate:
        protected_paths.extend([containing, os.path.join(containing, ".git")])
    for protected_path in protected_paths:
        if protected_path != candidate and protected_path.startswith(candidate.rstrip(os.sep) + os.sep):
            raise RuntimeError("protected_descendant")

    def snapshot():
        mounts = mountpoints()
        root = os.lstat(candidate)
        if not stat.S_ISDIR(root.st_mode) or stat.S_ISLNK(root.st_mode) or root.st_uid != uid:
            raise RuntimeError("invalid_root")
        root_dev = root.st_dev
        rows = []
        pending = [candidate]
        while pending:
            budget.check()
            if len(rows) >= cap:
                raise RuntimeError('entry_cap')
            path = pending.pop()
            value = os.lstat(path)
            if value.st_uid != uid:
                raise RuntimeError("non_owned_entry")
            if value.st_dev != root_dev:
                raise RuntimeError("cross_device_entry")
            if path != candidate and path in mounts:
                raise RuntimeError("nested_mount")
            rel = os.path.relpath(path, candidate)
            if rel == ".":
                rel = ""
            if stat.S_ISLNK(value.st_mode):
                resolved = os.path.realpath(path)
                if resolved != candidate and not resolved.startswith(candidate.rstrip(os.sep) + os.sep):
                    accepted = accepted_uv_interpreter(path, rel)
                else:
                    accepted = None
            else:
                accepted = None
            rows.append([
                rel, value.st_dev, value.st_ino, value.st_mode, value.st_uid,
                value.st_size, getattr(value, "st_blocks", 0), accepted,
            ])
            if len(rows) > cap:
                raise RuntimeError("entry_cap")
            if stat.S_ISDIR(value.st_mode) and not stat.S_ISLNK(value.st_mode):
                try:
                    with os.scandir(path) as entries:
                        children = []
                        for entry in entries:
                            budget.check()
                            if len(rows) + len(pending) + len(children) >= cap:
                                raise RuntimeError("entry_cap")
                            children.append(entry.path)
                except OSError as exc:
                    raise RuntimeError("unreadable_entry") from exc
                pending.extend(sorted(children, reverse=True))
        protected = {
            path: (identity(path) if os.path.lexists(path) else None)
            for path in protected_paths
        }
        return {"tree": sorted(rows), "protected": protected}

    first = snapshot()
    second = snapshot()
    if first != second:
        raise SystemExit(2)
    if os.path.exists(destination):
        with open(destination, encoding="utf-8") as handle:
            if json.load(handle) != first:
                raise SystemExit(3)
    else:
        parent = os.path.dirname(destination)
        fd, temporary = tempfile.mkstemp(prefix="tree-boundary-", dir=parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                encoded = json.dumps(first, sort_keys=True).encode()
                if len(encoded) > OUTPUT_CAP:
                    raise RuntimeError("snapshot_byte_cap")
                handle.write(encoded.decode())
            os.replace(temporary, destination)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

def verify_post_action(candidate, snapshot_path, isolated="", isolation_dir=""):
    if any(path and os.path.lexists(path) for path in (candidate, isolated, isolation_dir)):
        raise RuntimeError("action_residual")
    with open(snapshot_path, encoding="utf-8") as handle:
        protected = json.load(handle)["protected"]
    for path, expected in protected.items():
        if expected is None:
            if os.path.lexists(path):
                raise RuntimeError("protected_identity_changed")
        else:
            value = os.lstat(path)
            if [value.st_dev, value.st_ino, value.st_mode, value.st_uid] != expected:
                raise RuntimeError("protected_identity_changed")


def _oid(value):
    if not isinstance(value, str) or SHA.fullmatch(value) is None:
        raise RuntimeError("invalid_oid")
    return value


def _integer(value):
    if type(value) is not int or value < 0:
        raise RuntimeError("invalid_count")
    return value


def _equal(first, second):
    if first != second:
        raise RuntimeError("changing_evidence")
    return first


def target_contains_head(head, target, slug, containing, budget, *, force_github=False):
    _oid(head); _oid(target)
    if not force_github and budget.command(["git", "cat-file", "-e", target+"^{commit}"], containing).returncode == 0:
        return budget.command(["git", "merge-base", "--is-ancestor", head, target], containing).returncode == 0
    query = '{status: .status, ahead_by: .ahead_by, behind_by: .behind_by, total_commits: .total_commits, commit_count: (.commits | length), base_oid: .base_commit.sha, merge_base_oid: .merge_base_commit.sha, target_oid: (if .status == "identical" then .base_commit.sha else .commits[-1].sha end)}'
    args = ["gh", "api", f"repos/{slug}/compare/{head}...{target}", "--jq", query]
    value = _equal(budget.json(args, containing), budget.json(args, containing))
    if not isinstance(value, dict) or set(value) != {"status", "ahead_by", "behind_by", "total_commits", "commit_count", "base_oid", "merge_base_oid", "target_oid"}:
        raise RuntimeError("malformed_compare")
    for key in ("ahead_by", "behind_by", "total_commits", "commit_count"):
        _integer(value[key])
    for key in ("base_oid", "merge_base_oid", "target_oid"):
        _oid(value[key])
    return (value['status'] in ('identical', 'ahead') and value['behind_by'] == 0
            and value['ahead_by'] == value['total_commits'] == value['commit_count'] and value['base_oid'] == head
            and value['merge_base_oid'] == head and value['target_oid'] == target
            and ((value['status'] == 'identical' and value['ahead_by'] == 0 and head == target)
                 or (value['status'] == 'ahead' and value['ahead_by'] > 0 and head != target)))


def _owning_prs(pages, branch):
    if not isinstance(pages, list) or not pages:
        raise RuntimeError("incomplete_pr_pages")
    rows, totals, cursors, seen = [], set(), set(), set()
    for index, page in enumerate(pages):
        if not isinstance(page, dict) or set(page) != {'data'} or set(page['data']) != {'repository'}:
            raise RuntimeError("malformed_pr_page")
        repo = page['data']['repository']
        if not isinstance(repo, dict) or set(repo) != {'pullRequests'}:
            raise RuntimeError("malformed_pr_page")
        prs = repo['pullRequests']
        if not isinstance(prs, dict) or set(prs) != {'totalCount', 'nodes', 'pageInfo'}:
            raise RuntimeError("malformed_pr_page")
        totals.add(_integer(prs['totalCount']))
        nodes, info = prs['nodes'], prs['pageInfo']
        if not isinstance(nodes, list) or len(nodes) > 100 or not isinstance(info, dict) or set(info) != {'hasNextPage', 'endCursor'} or type(info['hasNextPage']) is not bool:
            raise RuntimeError("malformed_pr_page")
        cursor = info['endCursor']
        if index < len(pages)-1:
            if info['hasNextPage'] is not True or not isinstance(cursor, str) or not cursor or cursor in cursors:
                raise RuntimeError("incomplete_pr_pages")
            cursors.add(cursor)
        elif info['hasNextPage'] is not False:
            raise RuntimeError("incomplete_pr_pages")
        for row in nodes:
            if not isinstance(row, dict) or set(row) != {'number', 'state', 'mergedAt', 'headRefName', 'headRefOid'}:
                raise RuntimeError("malformed_pr_row")
            number = _integer(row['number'])
            if not number or number in seen or row['headRefName'] != branch:
                raise RuntimeError("conflicting_pr_row")
            seen.add(number); _oid(row['headRefOid'])
            if row['state'] != 'MERGED' or not isinstance(row['mergedAt'], str) or not row['mergedAt']:
                raise RuntimeError("unmerged_pr")
            rows.append(row)
    if len(totals) != 1 or len(rows) != next(iter(totals)) or len(rows) > 1:
        raise RuntimeError("incomplete_or_conflicting_prs")
    return rows


def _default_branch(slug, primary, budget):
    args = ['gh', 'api', f'repos/{slug}', '--jq', '{default_branch: .default_branch}']
    value = _equal(budget.json(args, primary), budget.json(args, primary))
    if not isinstance(value, dict) or set(value) != {'default_branch'}:
        raise RuntimeError('malformed_default')
    branch = value['default_branch']
    if not isinstance(branch, str) or not branch or len(branch) > 255 or any(ord(c) < 32 for c in branch):
        raise RuntimeError('malformed_default')
    return branch


def _remote_branch(primary, branch, budget):
    result = budget.command(['git', 'ls-remote', '--exit-code', 'origin', 'refs/heads/'+branch], primary)
    if result.returncode == 2 and not result.stdout.strip():
        return None
    rows = [line.split() for line in result.stdout.splitlines() if line.strip()]
    if result.returncode or len(rows) != 1 or len(rows[0]) != 2 or rows[0][1] != 'refs/heads/'+branch:
        raise RuntimeError('remote_branch_unknown')
    return _oid(rows[0][0])


def any_merged_pr_contains_head(head, slug, containing, primary, default, budget):
    args = ['gh', 'api', '-X', 'GET', 'search/issues', '--paginate', '-f', f'q=repo:{slug} type:pr {head}', '-f', 'per_page=100', '--jq', '{total_count: .total_count, incomplete_results: .incomplete_results, items: [.items[] | {number: .number, pull_request_url: .pull_request.url}]}']
    def read():
        totals, seen, numbers = set(), set(), []
        for line in budget.required(args, primary).splitlines():
            page = json.loads(line)
            if not isinstance(page, dict) or set(page) != {'total_count', 'incomplete_results', 'items'} or page['incomplete_results'] is not False or not isinstance(page['items'], list) or len(page['items']) > 100:
                raise RuntimeError('incomplete_search')
            totals.add(_integer(page['total_count']))
            for row in page['items']:
                if not isinstance(row, dict) or set(row) != {'number', 'pull_request_url'}:
                    raise RuntimeError('malformed_search')
                number = _integer(row['number'])
                if not number or number in seen or row['pull_request_url'] != f'https://api.github.com/repos/{slug}/pulls/{number}':
                    raise RuntimeError('conflicting_search')
                seen.add(number); numbers.append(number)
        if len(totals) != 1 or len(numbers) != next(iter(totals)):
            raise RuntimeError('incomplete_search')
        return numbers
    numbers = _equal(read(), read())
    targets = []
    for number in numbers:
        detail = ['gh', 'api', f'repos/{slug}/pulls/{number}', '--jq', '{number: .number, state: (if .merged == true then "MERGED" else (.state | ascii_upcase) end), mergedAt: .merged_at, headRefOid: .head.sha, baseRefName: .base.ref}']
        value = _equal(budget.json(detail, primary), budget.json(detail, primary))
        if not isinstance(value, dict) or set(value) != {'number', 'state', 'mergedAt', 'headRefOid', 'baseRefName'} or value['number'] != number or value['state'] not in ('OPEN', 'CLOSED', 'MERGED'):
            raise RuntimeError('malformed_search_pr')
        _oid(value['headRefOid'])
        if not isinstance(value['baseRefName'], str) or not value['baseRefName']:
            raise RuntimeError('malformed_search_pr')
        if value['state'] == 'MERGED':
            if not isinstance(value['mergedAt'], str) or not value['mergedAt']:
                raise RuntimeError('malformed_search_pr')
            if value['baseRefName'] == default:
                targets.append(value['headRefOid'])
        elif value['mergedAt'] is not None:
            raise RuntimeError('malformed_search_pr')
    # Preserve the runner's conservative ALL-discovered-targets requirement.
    return bool(targets) and all(target_contains_head(head, target, slug, containing, budget, force_github=True) for target in targets)


def git_preserved(containing, primary, task, budget):
    head = _oid(budget.required(['git', 'rev-parse', 'HEAD'], containing))
    branch = budget.required(['git', 'rev-parse', '--abbrev-ref', 'HEAD'], containing)
    if branch != 'task/'+task:
        raise RuntimeError('branch_identity')
    origin = budget.required(['git', 'remote', 'get-url', 'origin'], primary)
    match = re.fullmatch(r'(?:https://github[.]com/|git@github[.]com:)([^/]+/[^/]+?)(?:[.]git)?', origin)
    if not match:
        raise RuntimeError('origin_identity')
    slug = match.group(1); owner, name = slug.split('/')
    query = 'query($owner:String!,$name:String!,$headRefName:String!,$endCursor:String){repository(owner:$owner,name:$name){pullRequests(first:100,after:$endCursor,headRefName:$headRefName,orderBy:{field:CREATED_AT,direction:ASC}){totalCount nodes{number state mergedAt headRefName headRefOid} pageInfo{hasNextPage endCursor}}}}'
    args = ['gh', 'api', 'graphql', '--paginate', '--slurp', '-f', 'query='+query, '-F', 'owner='+owner, '-F', 'name='+name, '-F', 'headRefName='+branch]
    rows = _equal(_owning_prs(budget.json(args, primary), branch), _owning_prs(budget.json(args, primary), branch))
    # The live owning branch wins before ANY default/merged-PR fallback.
    remote = _equal(_remote_branch(primary, branch, budget), _remote_branch(primary, branch, budget))
    if remote is not None:
        if not target_contains_head(head, remote, slug, containing, budget):
            raise RuntimeError('owning_live_branch_contradiction')
        return head
    default = _default_branch(slug, primary, budget)
    default_oid = _equal(_remote_branch(primary, default, budget), _remote_branch(primary, default, budget))
    if default_oid is None:
        raise RuntimeError('default_branch_unavailable')
    if target_contains_head(head, default_oid, slug, containing, budget):
        return head
    if rows and target_contains_head(head, rows[0]['headRefOid'], slug, containing, budget):
        return head
    if not any_merged_pr_contains_head(head, slug, containing, primary, default, budget):
        raise RuntimeError('head_not_preserved')
    return head


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--timeout', type=float, default=5.0)
    sub = parser.add_subparsers(dest='operation', required=True)
    snap = sub.add_parser('snapshot')
    for key in ('candidate', 'containing', 'primary', 'workspace', 'destination'):
        snap.add_argument(key)
    post = sub.add_parser('post-action')
    for key in ('candidate', 'snapshot_path', 'isolated', 'isolation_dir'):
        post.add_argument(key)
    preservation = sub.add_parser('preserved')
    for key in ('containing', 'primary', 'task'):
        preservation.add_argument(key)
    args = parser.parse_args()
    budget = Budget(args.timeout)
    try:
        if args.operation == 'snapshot':
            snapshot_tree(args.candidate, args.containing, args.primary, args.workspace, args.destination, budget)
        elif args.operation == 'post-action':
            verify_post_action(args.candidate, args.snapshot_path, args.isolated, args.isolation_dir)
        else:
            head = git_preserved(args.containing, args.primary, args.task, budget)
            print(json.dumps({'head': head, 'preserved': True}, sort_keys=True))
        budget.check()
        return 0
    except Exception as exc:
        print(json.dumps({'refused': True, 'reason': str(exc)[:200], 'error': type(exc).__name__}, sort_keys=True))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
