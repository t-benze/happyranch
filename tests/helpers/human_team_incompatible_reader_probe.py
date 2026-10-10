"""Source-pinned C5 admission probe for the finite authorized disposable release.

Run only in the manager-authorized disposable venue. This invokes the selected
reader's real public admission capture without changing its measured inputs or
replacing its validator. Baseline equality is readback, not syscall/no-write
proof. The optional positive graph uses real selected-source routes as a
separate action after capture; it never runs on a negative input. External
observation and genuine old-source execution require authentic receipts.
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
    parser.add_argument("--positive-graph", action="store_true", help="Separate positive-control graph setup/admission after the measured capture; old-source execution requires the finite disposable release")
    args = parser.parse_args()
    if args.positive_graph and args.expect != "admitted":
        raise ValueError("negative_probe_cannot_write_a_graph")
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
        graph_receipt = None
        if args.positive_graph:
            # Positive graph proof is a SEPARATE action after readback-equal
            # capture. The negative never enters this setup or any writer.
            # Use the selected source's existing explicit org-only F->E script
            # on this disposable fixture; no installer/private schema seam.
            db.close()
            migrated = subprocess.run([sys.executable, str(source / "scripts/migrate_workflow_draft_schema.py"),
                "--runtime-root", str(root.parent.parent), "--org", args.org],
                cwd=source, capture_output=True, text=True, timeout=30)
            if migrated.returncode != 0:
                raise AssertionError(("positive fixture layout unavailable", migrated.stdout, migrated.stderr))
            db = Database(root / "happyranch.db")
            orch = Orchestrator(db, settings, paths, args.org, teams)
            org = OrgState(slug=args.org, root=root, db=db, teams=teams, settings=settings, orchestrator=orch)
            profiles = ProfileCoordinator(daemon_home=home, orgs={args.org: org})
            asyncio.run(bind_profiles())
            assert org.workflow_authority.recover() == "rehydrated_coherent"
            assert org.workflow_authority.capture_admission().ready.snapshot_bytes == ready.snapshot_bytes
            from fastapi.testclient import TestClient
            from runtime.daemon import paths as daemon_paths
            from runtime.daemon.app import create_app
            from runtime.daemon.state import DaemonState
            state = DaemonState(runtime=None, settings=settings, orgs={args.org: org}, profile_coordinator=profiles)
            client = TestClient(create_app(state))
            client.headers.update({"Authorization": "Bearer " + daemon_paths.ensure_token()})
            base = "/api/v1/orgs/" + args.org
            definition = {
                "kind":"product-design", "schema_version":1,
                "description":"Immutable PRD authoring and current-revision review",
                "author":{"role":"product-lead","kind":"agent","artifact":"immutable-prd-revision"},
                "reviewers":[{"role":"founder","kind":"human"},{"role":"implementer","kind":"agent"},{"role":"tester","kind":"agent"}],
                "approval":{"mode":"all","revision":"current","required_roles":["founder","implementer","tester"]},
                "request_changes":{"action":"return-to-author"},
            }
            try:
                published = client.post(base + "/workflows/templates/publish", json=dict(operation_key="reader-control-template",
                    team_slug="content", template_name="product-design", expected_current_version=0, definition=definition))
                assert published.status_code == 201, published.text
                template = published.json()
                enabled = client.post(base + "/workflows/cutover/requests", json=dict(operation_key="reader-control-enable", action="enable", expected_generation=1))
                assert enabled.status_code == 200 and enabled.json()["state"] == "enabled", enabled.text
                body = dict(operation_key="reader-control-graph",instance_id="reader-control-graph",expected_activation_revision=0,
                    template={key:template[key] for key in ("identity_id","version","definition_digest")},
                    authority=dict(namespace=ready.namespace,generation=ready.generation,snapshot_digest=ready.snapshot_digest),
                    scope=dict(brief="Reader control bounded document."),
                    bindings={"product-lead":dict(kind="agent",principal="content_manager",team="content"),
                              "founder":dict(kind="human",principal="founder",team=None),
                              "implementer":dict(kind="agent",principal="dev_agent",team="engineering"),
                              "tester":dict(kind="agent",principal="qa_engineer",team="engineering")},
                    eligible_replacements={role:[] for role in ("product-lead","founder","implementer","tester")},
                    allowed_actions=["draft-document","submit-immutable-document","collect-review","approve-planning-input","return-to-author"],inputs=[])
                admitted = client.post(base + "/workflows/activations", json=body)
                assert admitted.status_code == 201, admitted.text
                graph_receipt = admitted.json()
                with sqlite3.connect((root / "happyranch.db").resolve().as_uri() + "?mode=ro", uri=True) as reader:
                    assert reader.execute("SELECT assigned_agent,team,status FROM tasks WHERE id=?",(graph_receipt["root_task_id"],)).fetchone() == ("content_manager","content","pending")
                    raw, sha = reader.execute("SELECT context_bytes,context_digest FROM workflow_contexts WHERE id=(SELECT context_id FROM workflow_draft_dispatch_intents WHERE id=?)",(graph_receipt["intent_id"],)).fetchone()
                    assert hashlib.sha256(raw).hexdigest() == sha == graph_receipt["context_digest"]
                    assert json.loads(raw)["authority_snapshot"] == json.loads(ready.snapshot_bytes)
                    assert reader.execute("SELECT state,session_id,host_launch_started,final_result_id FROM workflow_draft_dispatch_intents WHERE id=?",(graph_receipt["intent_id"],)).fetchone() == ("queued",None,0,None)
                    assert reader.execute("SELECT COUNT(*) FROM task_results").fetchone()[0] == 0
                frozen = _domain(db)
                replay = client.post(base + "/workflows/activations", json=body)
                assert replay.status_code == 200 and replay.json()["replayed"], replay.text
                assert replay.json()["activation_id"] == graph_receipt["activation_id"]
                assert _domain(db) == frozen
            finally:
                client.close()
        # Late imports also belong to the selected source; no candidate runtime
        # can silently satisfy a pinned pre-feature control.
        for name, module in tuple(sys.modules.items()):
            if name == "runtime" or name.startswith("runtime."):
                filename = getattr(module, "__file__", None)
                assert filename is not None and Path(filename).resolve().is_relative_to(source), name
                origins[name] = hashlib.sha256(Path(filename).read_bytes()).hexdigest()
        print(json.dumps(dict(reader_source_sha=source_sha, effective_python=sys.executable,
                              python_version=sys.version,
                              python_sha256=hashlib.sha256(Path(sys.executable).read_bytes()).hexdigest(),
                              imported_source_files={name: str(Path(sys.modules[name].__file__).resolve()) for name in origins},
                              imported_source_hashes=origins,
                              snapshot_digest=args.snapshot_digest, setup=outcome,
                              action=args.operation, actual=actual,
                              persisted_readback_unchanged=True,
                              syscall_no_write_proof="external observer required",
                              graph_execution="separate positive graph admission; no executor/callback" if args.positive_graph else "not performed by this capture probe",
                              positive_graph_receipt=graph_receipt), sort_keys=True))
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, AssertionError, KeyError, sqlite3.DatabaseError, subprocess.SubprocessError) as exc:
        print(f"probe unavailable or assertion failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
