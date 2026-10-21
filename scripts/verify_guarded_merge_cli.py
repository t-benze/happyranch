#!/usr/bin/env python3
"""Explicitly allocated, isolated shipping-CLI acceptance; never merge CI.

The host parent imports stdlib only. Product imports occur solely in bwrap.
Transport responses are synthetic observations, never live review/callback proof.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
from pathlib import Path
import re
import resource
import signal
import subprocess
import sys
import time

FILES = (
    "runtime/__init__.py", "runtime/daemon/__init__.py",
    "runtime/daemon/pr_ci_merge.py", "runtime/daemon/pr_ci_waiter.py",
)
H = "a" * 40  # Synthetic fixture head, NOT the inspected source commit.
H2 = "b" * 40
COMMIT = "c" * 40
STAMP = "2001-02-03T04:05:06+05:30"
REPO = "synthetic-cli/acceptance"
PR = 4242
KEYS = {"verdict", "pr_number", "pinned_head_sha", "merged_sha", "merged_at",
        "observed_head_sha", "error_detail"}
MAX_BYTES = 131072

# Fakes return transport data only. Assertions/policy live outside the fakes.
FAKE = r'''#!/usr/bin/python3 -S
import json, os, signal, sys
from pathlib import Path
cfg = json.loads(Path('/fixture/case.json').read_text())
argv = sys.argv[1:]
name = Path(sys.argv[0]).name
mode = cfg['mode']
stdout, stderr, code, sig = '', '', 0, None
kind = 'unexpected'
if name == 'happyranch' and argv == ['recall','--org','synthetic-cli', 'SYNTHETIC-REVIEW']:
    kind = 'review'
    stdout = json.dumps(cfg.get('review', {'verdict':'APPROVE'}))
elif name == 'happyranch' and argv == ['recall','--org','synthetic-cli', 'SYNTHETIC-QA']:
    kind = 'qa'
    stdout = json.dumps({'verdict':None, 'output_summary':'Verdict: PASS — synthetic transport'})
elif name == 'gh' and argv[:2] == ['pr','merge']:
    kind = 'action'
    if mode == 'race':
        Path('/work/remote-head').write_text('b' * 40)
        stderr, code = 'synthetic action head changed to H2', 23
    elif mode == 'protection':
        stderr, code = 'synthetic protection refuses action', 24
    elif mode == 'nonzero':
        stdout, code = 'synthetic stdout command cause', 25
    elif mode in ('signal', 'lost'):
        stderr, sig = 'synthetic delivered response lost', signal.SIGTERM
        if mode == 'lost':
            Path('/work/delivered').write_text('possible effect')
    elif mode == 'query-exec':
        Path('/work/bin/gh').write_text('invalid executable\n')
    else:
        stdout = 'synthetic action accepted'
elif name == 'gh' and argv[:2] == ['pr','view']:
    fields = argv[-1]
    if fields == 'headRefOid,state,isDraft':
        kind = 'state'
        stdout = json.dumps({'headRefOid':'a'*40, 'state':'OPEN', 'isDraft':False})
    elif fields == 'mergeable,mergeStateStatus':
        kind = 'mergeable'
        stdout = json.dumps({'mergeable':'MERGEABLE','mergeStateStatus':'CLEAN'})
        if mode == 'action-exec':
            Path('/work/bin/gh').chmod(0o600)
    elif fields in ('number,url,headRefOid,state,mergeCommit,mergedAt', 'mergeCommit'):
        kind = 'confirmation'
        stdout = cfg['metadata_raw']
        if mode == 'query-nonzero':
            stderr, code = 'synthetic query cause', 26
        elif mode == 'query-signal':
            stderr, sig = 'synthetic query signal', signal.SIGTERM
if kind == 'unexpected':
    stderr, code = 'unexpected fake transport argv', 99
record = {'kind':kind, 'executable':name, 'argv':argv, 'stdout':stdout,
          'stderr':stderr, 'exit':-int(sig) if sig else code}
with open('/result/transport.jsonl', 'a') as f:
    f.write(json.dumps(record) + '\n')
    f.flush()
print(stdout, end='')
print(stderr, end='', file=sys.stderr)
sys.stdout.flush(); sys.stderr.flush()
if sig:
    os.kill(os.getpid(), sig)
sys.exit(code)
'''

# This stdlib driver observes isolation BEFORE starting any product interpreter.
DRIVER = r'''
import json, os, shutil, socket, subprocess, sys
from pathlib import Path
cfg = json.loads(Path('/fixture/case.json').read_text())
observed = {
    'cwd':os.getcwd(), 'interfaces':socket.if_nameindex(),
    'absent':{p:not Path(p).exists() for p in
              ['/home','/run','/root','/etc','/src/.git','/src/runtime/config.py']},
    'namespaces':{n:os.readlink('/proc/self/ns/'+n) for n in cfg['host_ns']},
    'env':dict(os.environ), 'python':sys.version, 'interpreter':sys.executable,
}
Path('/result/isolation.json').write_text(json.dumps(observed, indent=2))
assert observed['cwd'] == '/work'
assert observed['interfaces'] == [(1,'lo')]
assert all(observed['absent'].values())
assert all(observed['namespaces'][n] != old for n,old in cfg['host_ns'].items())
assert set(os.environ) <= {'HOME','PATH','LC_ALL'}
assert not Path('/work/.git').exists()
assert not list(Path('/work').iterdir())
Path('/work/home').mkdir()
Path('/work/bin').mkdir()
for name in ['gh','happyranch']:
    shutil.copyfile('/fixture/bin/'+name, '/work/bin/'+name)
    Path('/work/bin/'+name).chmod(0o700)
env = {'HOME':'/work/home','PATH':'/work/bin','PYTHONPATH':'/src',
       'PYTHONDONTWRITEBYTECODE':'1','LC_ALL':'C.UTF-8'}
cli = ['/usr/bin/python3','-S','-m','runtime.daemon.pr_ci_merge',
       '--org','synthetic-cli','--repo','synthetic-cli/acceptance','--pr','4242',
       '--head-sha','a'*40,'--merge-method','squash','--ci-verdict','ci_pass',
       '--review-task-id','SYNTHETIC-REVIEW','--qa-task-id','SYNTHETIC-QA']
def run(argv):
    result = subprocess.run(argv, env=env, cwd='/work', capture_output=True, timeout=2)
    return {'argv':argv,'cwd':'/work','env':env,'exit':result.returncode,
            'stdout':result.stdout.decode('utf-8', 'replace'),
            'stderr':result.stderr.decode('utf-8', 'replace')}
receipt = run(cli)
Path('/result/cli.json').write_text(json.dumps(receipt, indent=2))
Path('/result/effects.json').write_text(json.dumps({
    'head':Path('/work/remote-head').read_text() if Path('/work/remote-head').exists() else 'a'*40,
    'possible_delivery':Path('/work/delivered').exists(),
}))
if cfg['mode'] == 'lost':
    if cfg['has_observer']:
        # A separate read-only process never rewrites the original CLI receipt.
        observation = run(['/usr/bin/python3','-S','-c',
            'from runtime.daemon.pr_ci_merge import _gh_fetch_merge_result; '
            'import json; from dataclasses import asdict; '
            'print(json.dumps(asdict(_gh_fetch_merge_result('
            '"synthetic-cli/acceptance",4242,"'+ 'a'*40 +'"))))'])
    else:
        observation = {'unavailable':'pre-fix source has no read-only observer'}
    Path('/result/observation.json').write_text(json.dumps(observation, indent=2))
'''


def cases() -> list[dict]:
    """Finite table; every A5 row varies one canonical remote field."""
    canonical = {"number": PR, "url": f"https://github.com/{REPO}/pull/{PR}",
                 "headRefOid": H, "state": "MERGED",
                 "mergeCommit": {"oid": COMMIT}, "mergedAt": STAMP}
    rows = []
    for name, mode in [("A1-race", "race"), ("A2-protection", "protection"),
                       ("A3-success", "success"), ("A4-nonzero", "nonzero"),
                       ("A4-signal", "signal"), ("A4-exec", "action-exec"),
                       ("A6-loss-observe", "lost")]:
        rows.append({"name": name, "mode": mode, "metadata_raw": json.dumps(canonical)})
    for field in canonical:
        for label, value in [("missing", None), ("null", None),
                             ("type", True), ("empty", "")]:
            data = dict(canonical)
            if label == "missing":
                del data[field]
            else:
                data[field] = value
            rows.append({"name": f"A5-{field}-{label}", "mode": "metadata",
                         "metadata_raw": json.dumps(data)})
    for name, field, value in [
        ("wrong-number", "number", PR + 1), ("wrong-url", "url", "https://github.com/foreign/repo/pull/4242"),
        ("wrong-head", "headRefOid", H2), ("wrong-state", "state", "OPEN"),
        ("commit-missing-oid", "mergeCommit", {}),
        ("commit-null-oid", "mergeCommit", {"oid": None}),
        ("commit-type-oid", "mergeCommit", {"oid": True}),
        ("commit-empty-oid", "mergeCommit", {"oid": ""}),
        ("commit-short", "mergeCommit", {"oid": "c" * 39}),
        ("commit-uppercase", "mergeCommit", {"oid": "C" * 40}),
        ("commit-malformed", "mergeCommit", {"oid": "z" * 40}),
        ("time-malformed", "mergedAt", "invalid"),
        ("time-naive", "mergedAt", "2001-02-03T04:05:06"),
    ]:
        data = dict(canonical)
        data[field] = value
        rows.append({"name": "A5-" + name, "mode": "metadata", "metadata_raw": json.dumps(data)})
    for label, raw in [("json-malformed", "{"), ("json-array", "[]"), ("json-null", "null")]:
        rows.append({"name": "A5-" + label, "mode": "metadata", "metadata_raw": raw})
    for mode in ["query-nonzero", "query-signal", "query-exec"]:
        rows.append({"name": "A5-" + mode, "mode": mode, "metadata_raw": json.dumps(canonical)})
    # Distinct earlier guard/extractor observations, not claimed as pre-fix RED.
    rows.append({"name": "guard-review-conflict", "mode": "guard",
                 "metadata_raw": json.dumps(canonical),
                 "review": {"verdict": "APPROVE", "output_summary": "Verdict: BLOCK"}})
    assert len(rows) == 51
    return rows


def git(root: Path, *args: str) -> str:
    return subprocess.check_output(["/usr/bin/git", "-C", str(root), *args],
                                   timeout=3, env={"PATH": "/usr/bin:/bin"}).decode().strip()


def identity(root: Path, expected: str) -> dict:
    if git(root, "rev-parse", "HEAD") != expected:
        raise ValueError("source HEAD differs from --expected-head")
    digests = {}
    for rel in FILES:
        path = root / rel
        if any(p.is_symlink() for p in [path, *path.parents] if p != root.parent):
            raise ValueError(f"symlink in source path: {rel}")
        data = path.read_bytes()
        committed = subprocess.check_output(
            ["/usr/bin/git", "-C", str(root), "show", f"{expected}:{rel}"], timeout=3)
        if data != committed:
            raise ValueError(f"source bytes differ from commit: {rel}")
        tree = ast.parse(data, filename=rel)
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                names = [a.name for a in node.names] if isinstance(node, ast.Import) else [node.module or ""]
                for name in names:
                    if name not in {"runtime.daemon.pr_ci_waiter"} and name.split(".")[0] not in sys.stdlib_module_names:
                        raise ValueError(f"unadmitted import: {rel}: {name}")
                if isinstance(node, ast.ImportFrom) and node.level:
                    raise ValueError("relative product import is unadmitted")
            if isinstance(node, ast.Call) and (
                isinstance(node.func, ast.Name) and node.func.id == "__import__"
                or isinstance(node.func, ast.Attribute) and node.func.attr == "import_module"
            ):
                raise ValueError("dynamic product import is unadmitted")
        digests[rel] = hashlib.sha256(data).hexdigest()
    return {"head": expected, "tree": git(root, "rev-parse", "HEAD^{tree}"), "files": digests}


def check_case(case: dict, result: Path) -> list[dict]:
    cli = json.loads((result / "cli.json").read_text())
    ledger_path = result / "transport.jsonl"
    ledger = [json.loads(line) for line in ledger_path.read_text().splitlines()]
    checks = []

    def equal(label: str, actual: object, expected: object) -> None:
        checks.append({"assertion": label, "actual": actual, "expected": expected,
                       "pass": actual == expected})

    try:
        output = json.loads(cli["stdout"])
    except ValueError:
        output = {}
    equal("CLI exact JSON keys", sorted(output), sorted(KEYS))
    equal("addressed PR", output.get("pr_number"), PR)
    equal("pinned synthetic head", output.get("pinned_head_sha"), H)
    equal("CLI stderr", cli["stderr"], "")
    kinds = [r["kind"] for r in ledger]
    mode = case["mode"]
    if mode == "guard":
        equal("conflicting review fails before all other transports", kinds, ["review"])
        equal("extractor verdict", output.get("verdict"), "github_error")
        equal("extractor exit", cli["exit"], 7)
        return checks
    equal("earlier guards reached in declared order", kinds[:4], ["review", "qa", "state", "mergeable"])
    equal("synthetic review recall argv", ledger[0]["argv"],
          ["recall", "--org", "synthetic-cli", "SYNTHETIC-REVIEW"])
    equal("synthetic QA recall argv", ledger[1]["argv"],
          ["recall", "--org", "synthetic-cli", "SYNTHETIC-QA"])
    actions = [r for r in ledger if r["kind"] == "action"]
    equal("exactly one action (zero on native exec denial)", len(actions), 0 if mode == "action-exec" else 1)
    if actions:
        equal("ordinary exact pinned action argv", actions[0]["argv"],
              ["pr", "merge", str(PR), "--repo", REPO, "--squash", "--match-head-commit", H])
    expected_kinds = ["review", "qa", "state", "mergeable"]
    if mode != "action-exec":
        expected_kinds += ["action"]
    if mode in {"success", "metadata", "query-nonzero", "query-signal", "lost"}:
        # A6's query belongs to the later, separate observer.
        expected_kinds += ["confirmation"]
    equal("complete transport order/no replay", kinds, expected_kinds)
    for row in ledger:
        if row["kind"] == "confirmation":
            equal("exact addressed read-only confirmation argv", row["argv"],
                  ["pr", "view", str(PR), "--repo", REPO, "--json",
                   "number,url,headRefOid,state,mergeCommit,mergedAt"])
    success = mode == "success"
    equal("CLI exit", cli["exit"], 0 if success else 14)
    equal("CLI verdict", output.get("verdict"), "merged" if success else "merge_failed")
    equal("actual remote commit or absent failed receipt", output.get("merged_sha"), COMMIT if success else None)
    equal("actual remote time or absent failed receipt", output.get("merged_at"), STAMP if success else None)
    equal("no earlier stale-head verdict", output.get("observed_head_sha"), None)
    error = output.get("error_detail") or ""
    if success:
        equal("success has no error", output.get("error_detail"), None)
    else:
        cause = {"race": "synthetic action head changed to H2",
                 "protection": "synthetic protection refuses action",
                 "nonzero": "synthetic stdout command cause", "signal": "exit -15",
                 "lost": "exit -15", "action-exec": "Permission denied",
                 "query-exec": "Exec format error", "query-nonzero": "synthetic query cause",
                 "query-signal": "exit -15"}.get(mode, "remote confirmation failed")
        equal("original native failure cause preserved", cause in error, True)
        equal("uncertainty/no retry instruction", "do not repeat merge" in error, True)
    if mode == "race":
        equal("head changed at action", json.loads((result / "effects.json").read_text())["head"], H2)
        equal("native action rejected", actions[0]["exit"], 23)
    if mode == "lost":
        equal("possible delivery recorded separately",
              json.loads((result / "effects.json").read_text())["possible_delivery"], True)
        observation = json.loads((result / "observation.json").read_text())
        equal("later read-only observation exit", observation.get("exit"), 0)
        try:
            observed = json.loads(observation.get("stdout", ""))
        except ValueError:
            observed = None
        equal("later observation preserves actual remote values", observed,
              {"merged_sha": COMMIT, "merged_at": STAMP})
        equal("original failure receipt retained", json.loads((result / "cli.json").read_text()), cli)
    return checks


def limits() -> None:
    resource.setrlimit(resource.RLIMIT_FSIZE, (MAX_BYTES, MAX_BYTES))
    resource.setrlimit(resource.RLIMIT_CPU, (4, 4))
    resource.setrlimit(resource.RLIMIT_AS, (512 * 1024 * 1024,) * 2)
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))


def main() -> int:
    started = time.monotonic()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", required=True, type=Path)
    parser.add_argument("--expected-head", required=True)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    if not re.fullmatch(r"[0-9a-f]{40}", args.expected_head):
        parser.error("expected-head must be a full lowercase SHA1 commit")
    root, out = args.source_root, args.output_dir
    if not root.is_absolute() or root.resolve() != root or not root.is_dir():
        parser.error("source-root must be an absolute canonical existing directory")
    if not out.is_absolute() or out.parent.resolve() != out.parent or root == out or root in out.parents:
        parser.error("output-dir must be an absolute fresh owned directory outside source")
    before = identity(root, args.expected_head)
    out.mkdir(mode=0o700, parents=False, exist_ok=False)
    manifest = {"source": before, "argv": sys.argv, "cwd": os.getcwd(),
                "parent_interpreter": sys.executable, "parent_version": sys.version,
                "synthetic_head": H, "cases": [], "complete": False}
    host_ns = {n: os.readlink("/proc/self/ns/" + n) for n in ["user", "mnt", "net", "pid", "uts", "ipc", "cgroup"]}
    has_observer = "def _gh_fetch_merge_result(" in (root / FILES[2]).read_text()
    try:
        for case in cases():
            remaining = 59 - (time.monotonic() - started)
            if remaining <= 0:
                raise TimeoutError("whole-invocation deadline")
            folder = out / case["name"]
            folder.mkdir()
            fixture, result = folder / "fixture", folder / "result"
            (fixture / "bin").mkdir(parents=True)
            result.mkdir()
            case.update(host_ns=host_ns, has_observer=has_observer)
            (fixture / "case.json").write_text(json.dumps(case))
            (fixture / "driver.py").write_text(DRIVER)
            for name in ["gh", "happyranch"]:
                path = fixture / "bin" / name
                path.write_text(FAKE)
                path.chmod(0o700)
            command = ["/usr/bin/bwrap", "--unshare-all", "--new-session", "--die-with-parent",
                       "--clearenv", "--setenv", "HOME", "/work/home",
                       "--setenv", "PATH", "/fixture/bin", "--setenv", "LC_ALL", "C.UTF-8"]
            for system in ["/usr", "/lib", "/lib64"]:
                if Path(system).exists():
                    command += ["--ro-bind", system, system]
            command += ["--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp",
                        "--dir", "/work", "--dir", "/src/runtime/daemon"]
            for rel in FILES:
                command += ["--ro-bind", str(root / rel), "/src/" + rel]
            command += ["--ro-bind", str(fixture), "/fixture", "--bind", str(result), "/result",
                        "--chdir", "/work", "/usr/bin/python3", "-I", "-S", "/fixture/driver.py"]
            row = {"name": case["name"], "command": command, "checks": []}
            manifest["cases"].append(row)
            with (folder / "stdout").open("wb") as stdout, (folder / "stderr").open("wb") as stderr:
                process = subprocess.Popen(command, stdout=stdout, stderr=stderr,
                                           env={}, start_new_session=True, preexec_fn=limits)
                try:
                    row["exit"] = process.wait(timeout=min(5, remaining))
                except BaseException:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait()
                    raise
            if row["exit"] != 0:
                raise RuntimeError(f"{case['name']}: isolation/driver failure, not contract RED")
            row["checks"] = check_case(case, result)
        after = identity(root, args.expected_head)
        if after != before:
            raise ValueError("source identity changed during campaign")
        manifest["source_after"] = after
        manifest["complete"] = True
    except Exception as exc:
        manifest["setup_error"] = f"{type(exc).__name__}: {exc}"
    manifest["elapsed_seconds"] = time.monotonic() - started
    failed = [f"{row['name']}: {check['assertion']} actual={check['actual']!r} expected={check['expected']!r}"
              for row in manifest["cases"] for check in row["checks"] if not check["pass"]]
    manifest["failed_assertions"] = failed
    (out / "receipt.json").write_text(json.dumps(manifest, indent=2))
    print(json.dumps({"complete": manifest["complete"], "cases": len(manifest["cases"]),
                      "failed_assertions": len(failed), "setup_error": manifest.get("setup_error"),
                      "receipt": str(out / "receipt.json"), "elapsed_seconds": manifest["elapsed_seconds"]}))
    return 2 if not manifest["complete"] else (1 if failed else 0)


def invocation_deadline(signum: int, frame: object) -> None:
    raise TimeoutError("60s whole-invocation limit")


if __name__ == "__main__":
    signal.signal(signal.SIGALRM, invocation_deadline)
    signal.setitimer(signal.ITIMER_REAL, 60)
    try:
        code = main()
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
    sys.exit(code)
