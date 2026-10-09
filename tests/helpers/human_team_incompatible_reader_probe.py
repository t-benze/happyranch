"""Source-pinned C5 admission probe; old-source execution remains HELD.

Run only in the manager-authorized disposable venue. This invokes the selected
reader's real public admission capture, without publishing or replacing its
validator. Baseline equality is a readback assertion, not syscall/no-write proof.
The accepted external observers and positive graph cases remain separate gates.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import stat
import subprocess
import sys


def _closed_files(root: Path) -> dict[str, tuple]:
    values = {}
    for path in (root, *sorted(root.rglob("*"))):
        info = path.lstat()
        metadata = (stat.S_IMODE(info.st_mode), info.st_uid, info.st_gid)
        if path.is_symlink():
            value = ("link", os.readlink(path), *metadata)
        elif path.is_dir():
            value = ("directory", *metadata)
        elif path.is_file() and info.st_nlink == 1:
            value = ("file", hashlib.sha256(path.read_bytes()).hexdigest(), *metadata)
        else:
            raise ValueError("uninspectable_fixture_file_identity")
        values[str(path.relative_to(root))] = value
    return values


def _domain(db) -> tuple:
    with db._lock:
        conn = db._conn
        schema = tuple(tuple(row) for row in conn.execute("SELECT * FROM sqlite_master ORDER BY type,name"))
        tables = []
        for name, in conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"):
            escaped = name.replace('"', '""')
            tables.append((name, tuple(sorted(repr(tuple(row)) for row in conn.execute(f'SELECT * FROM "{escaped}"')))))
        return schema, tuple(tables)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--org", required=True)
    parser.add_argument("--operation", choices=["capture-admission"], required=True)
    parser.add_argument("--expect", choices=["admitted", "workflow_activation_authority_stale"], required=True)
    parser.add_argument("--snapshot-digest", required=True)
    args = parser.parse_args()
    sys.dont_write_bytecode = True
    if sys.version_info[:2] != (3, 14):
        raise ValueError("effective_python314_required")
    source = args.source.resolve(strict=True)
    root = args.root.resolve(strict=True)
    if (not args.source.is_absolute() or args.source.is_symlink()
            or not args.root.is_absolute() or args.root.is_symlink()
            or root.is_relative_to(source) or not (root / "happyranch.db").is_file()):
        raise ValueError("independent_owned_reader_fixture_required")
    source_sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=source, text=True).strip()
    if source_sha != args.source_sha or subprocess.check_output(["git", "status", "--porcelain"], cwd=source).strip():
        raise ValueError("clean_exact_reader_source_required")
    home_value = os.environ.get("HAPPYRANCH_DAEMON_HOME")
    if not home_value:
        raise ValueError("explicit_owned_reader_daemon_home_required")
    home = Path(home_value).resolve(strict=True)
    if home.stat().st_uid != os.getuid() or stat.S_IMODE(home.stat().st_mode) != 0o700:
        raise ValueError("private_reader_daemon_home_required")
    for path in (root, home):
        if path.stat().st_uid != os.getuid():
            raise ValueError("reader_fixture_not_owned")
    # -I excludes ambient import paths; only this selected checkout is added.
    sys.path.insert(0, str(source))
    from runtime.config import Settings
    from runtime.daemon.org_state import OrgState
    from runtime.infrastructure.database import Database
    from runtime.orchestrator._paths import OrgPaths
    from runtime.orchestrator.orchestrator import Orchestrator
    from runtime.orchestrator.teams import TeamsRegistry
    from runtime.workflows.authority import WorkflowAuthorityError
    from runtime.workflows.profile_coordinator import ProfileCoordinator

    origins = {}
    for name, module in tuple(sys.modules.items()):
        if name == "runtime" or name.startswith("runtime."):
            filename = getattr(module, "__file__", None)
            if filename is None or not Path(filename).resolve().is_relative_to(source):
                raise ValueError("reader_import_origin_mismatch")
            origins[name] = hashlib.sha256(Path(filename).read_bytes()).hexdigest()
    settings = Settings(project_root=source)
    if settings.daemon_home.resolve() != home:
        raise ValueError("effective_reader_daemon_home_mismatch")
    db = Database(root / "happyranch.db")
    try:
        paths = OrgPaths(root=root)
        teams = TeamsRegistry.load(root)
        orch = Orchestrator(db, settings, paths, args.org, teams)
        org = OrgState(slug=args.org, root=root, db=db, teams=teams, settings=settings, orchestrator=orch)
        profiles = ProfileCoordinator(daemon_home=home, orgs={args.org: org})

        async def bind_profiles() -> None:
            # Unfenced public context binds the actual coordinator without
            # canonical mutation, publication or a private cache injection.
            async with profiles.consumer_writer(org=org, publisher="reader-control", consumer="reader-control"):
                pass

        asyncio.run(bind_profiles())
        outcome = org.workflow_authority.recover()
        if outcome != "rehydrated_coherent":
            raise ValueError("genuine_ready_fixture_rehydration_required")
        ready = org.workflow_authority.verify_admission_ready()
        if hashlib.sha256(ready.snapshot_bytes).hexdigest() != args.snapshot_digest:
            raise ValueError("independently_pinned_reader_input_mismatch")
        if json.loads(ready.snapshot_bytes)["machine_global_profiles"] != []:
            raise ValueError("finite_no_profile_dependency_control_required")
        # Recovery/constructors are explicit setup. Measurement starts here.
        before_domain, before_files = _domain(db), _closed_files(root)
        try:
            capture = org.workflow_authority.capture_admission()
        except WorkflowAuthorityError as exc:
            actual = exc.code
        else:
            actual = "admitted"
            if capture.ready.snapshot_digest != args.snapshot_digest:
                raise ValueError("admission_capture_input_changed")
        after_domain, after_files = _domain(db), _closed_files(root)
        if actual != args.expect:
            raise AssertionError(f"actual admission={actual!r}; expected={args.expect!r}")
        if before_domain != after_domain or before_files != after_files:
            raise AssertionError("measured admission capture changed fixture rows/files")
        print(json.dumps(dict(reader_source_sha=source_sha, effective_python=sys.executable,
                              python_version=sys.version, imported_source_hashes=origins,
                              snapshot_digest=args.snapshot_digest, setup=outcome,
                              action=args.operation, actual=actual,
                              persisted_readback_unchanged=True,
                              syscall_no_write_proof="external observer required",
                              graph_execution="not performed by this capture probe"), sort_keys=True))
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, AssertionError, KeyError, sqlite3.DatabaseError, subprocess.SubprocessError) as exc:
        print(f"probe unavailable or assertion failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
