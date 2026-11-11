"""S7 source observations on private case-insensitive APFS fixtures.

No HTTP server, git/network operation, host configuration or new dependency.
The subprocess/writer boundaries are isolated; product parsing, file writes and
route handlers are real. These observations do not certify a frozen Mac app.
"""
from __future__ import annotations

import asyncio
import ctypes as ct
import io
import json
import os
import platform
import tempfile
from contextlib import asynccontextmanager, nullcontext
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable
from unittest.mock import patch

Check = Callable[[str, Any, Any], None]
ROOT = Path(__file__).resolve().parents[2]


def artifact_checks(scratch: Path, check: Check) -> None:
    from fastapi import HTTPException, UploadFile
    from runtime.daemon.routes import artifacts, custom_skills
    from runtime.infrastructure.artifact_store import ArtifactStore, InvalidArtifactName

    store = ArtifactStore(scratch / "artifacts")
    store.put("Report.txt", b"original")
    error = ""
    try:
        store.put("report.txt", b"replacement")
    except InvalidArtifactName as exc:
        error = str(exc)
    check("artifact_leaf_alias", {"refused": True, "bytes": "original", "names": ["Report.txt"]},
          {"refused": error.startswith("case_collision:"), "bytes": store.read("Report.txt").decode(),
           "names": [entry.name for entry in store.list_artifacts()]})

    store.put("Reports/Keep.txt", b"keep")
    error = ""
    try:
        store.put("reports/New.txt", b"new")
    except InvalidArtifactName as exc:
        error = str(exc)
    check("artifact_folder_alias", {"refused": True, "kept": "keep", "new_exists": False},
          {"refused": error.startswith("case_collision:"), "kept": store.read("Reports/Keep.txt").decode(),
           "new_exists": (store.root / "Reports/New.txt").exists()})

    error = ""
    try:
        store.put("Reports/keep.txt", b"changed")
    except InvalidArtifactName as exc:
        error = str(exc)
    check("artifact_nested_leaf_alias", {"refused": True, "bytes": "keep"},
          {"refused": error.startswith("case_collision:"), "bytes": store.read("Reports/Keep.txt").decode()})
    store.put("Report.txt", b"updated")
    store.put("Reports/Keep.txt", b"updated nested")
    check("artifact_exact_overwrite", ["updated", "updated nested"],
          [store.read("Report.txt").decode(), store.read("Reports/Keep.txt").decode()])

    # Real route error projection; only post-success audit I/O is isolated.
    audit: list[str] = []
    logger = SimpleNamespace(log_artifact_put=lambda **_: audit.append("put"))
    org = SimpleNamespace(root=scratch, db=None)
    upload = UploadFile(filename="report.txt", file=io.BytesIO(b"route overwrite"))
    status, detail = 200, {}
    try:
        with patch.object(artifacts, "AuditLogger", return_value=logger):
            asyncio.run(artifacts.put_artifact("probe", org, upload, "report.txt", "probe"))
    except HTTPException as exc:
        status, detail = exc.status_code, exc.detail
    finally:
        asyncio.run(upload.close())
    check("artifact_route_400", {"status": 400, "code": "invalid_artifact_name", "collision": True,
                                 "bytes": "updated", "audits": []},
          {"status": status, "code": detail.get("code"),
           "collision": detail.get("message", "").startswith("case_collision:"),
           "bytes": store.read("Report.txt").decode(), "audits": audit})

    key = "custom-skills/probe/0123/SKILL.md"
    store.put(key, b"existing skill")
    status, detail = 200, {}
    try:
        custom_skills._write_artifact(org, key.removesuffix("SKILL.md") + "skill.md", "new skill")
    except HTTPException as exc:
        status, detail = exc.status_code, exc.detail
    check("custom_skill_refusal_preserves_alias", {"status": 400, "code": "invalid_artifact_name",
                                                  "refused": True, "bytes": "existing skill"},
          {"status": status, "code": detail.get("code"),
           "refused": detail.get("message", "").startswith("case_collision:"),
           "bytes": store.read(key).decode() if store.exists(key) else None})

    # Arrival after the first check, before replacement: keep the arriving key.
    mkstemp = tempfile.mkstemp

    def arriving_key(*args: Any, **kwargs: Any) -> tuple[int, str]:
        fd, path = mkstemp(*args, **kwargs)
        (store.root / "Late.txt").write_bytes(b"arrived")
        return fd, path

    error = ""
    with patch("runtime.infrastructure.artifact_store.tempfile.mkstemp", side_effect=arriving_key):
        try:
            store.put("late.txt", b"overwrite arrival")
        except InvalidArtifactName as exc:
            error = str(exc)
    check("artifact_alias_before_replace", {"refused": True, "bytes": "arrived"},
          {"refused": error.startswith("case_collision:"), "bytes": store.read("Late.txt").decode()})
    check("artifact_no_temporary_residue", [],
          [str(path.relative_to(store.root)) for path in store.root.rglob(".tmp.*")])


def repo_checks(scratch: Path, check: Check) -> None:
    from fastapi import HTTPException
    from runtime.config import Settings
    from runtime.daemon.routes import agents
    from runtime.orchestrator._paths import OrgPaths
    from runtime.orchestrator.agent_def import AgentDef, AgentParseError, parse_agent_text, render_agent_text
    from runtime.orchestrator.context_builder import ContextBuilder

    root = scratch / "repo-org"
    paths = OrgPaths(root)
    paths.agents_dir.mkdir(parents=True)
    workspace = paths.workspaces_dir / "probe"
    (workspace / "repos/foo/.git").mkdir(parents=True)
    definition = AgentDef(name="probe", team="probe", role="worker", executor="claude", allow_rules=(),
                          repos={"foo": "https://example.invalid/original.git"}, enrolled_by=None,
                          enrolled_at_task=None, enrolled_at=None, system_prompt="Probe agent.\n")
    canonical = paths.agents_dir / "probe.md"
    original = render_agent_text(definition).encode()
    canonical.write_bytes(original)
    calls: list[list[str]] = []
    writer_entries: list[str] = []
    bootstrap: list[str] = []

    def git(argv: list[str], **_: Any) -> SimpleNamespace:
        calls.append(argv)
        return SimpleNamespace(returncode=0)

    @asynccontextmanager
    async def writer(*_: Any, **__: Any):
        writer_entries.append("entered")
        yield SimpleNamespace(validation=nullcontext, canonical_change=nullcontext,
                              native_disposition=SimpleNamespace(uncertain=False))

    org = SimpleNamespace(root=root, slug="probe", settings=Settings(), teams_lock=asyncio.Lock())
    body = agents.ManageRepoBody(action="add", repo_name="Foo", url="https://example.invalid/new.git")
    status, detail = 200, ""
    with patch.object(agents, "_consumer_writer_interval", writer), \
         patch("runtime.orchestrator.context_builder.subprocess.run", side_effect=git), \
         patch.object(ContextBuilder, "ensure_workspace_ready", side_effect=lambda *_a, **_k: bootstrap.append("called")):
        try:
            asyncio.run(agents.manage_repo("probe", "probe", body, org))
        except HTTPException as exc:
            status, detail = exc.status_code, str(exc.detail)
        except AgentParseError as exc:
            status, detail = "late AgentParseError", str(exc)
    check("manage_repo_early_validation", {"status": 422, "invalid_key": True, "canonical_unchanged": True,
                                          "git_calls": [], "writer_entries": [], "bootstrap": []},
          {"status": status, "invalid_key": "invalid repo key" in detail,
           "canonical_unchanged": canonical.read_bytes() == original, "git_calls": list(calls),
           "writer_entries": list(writer_entries), "bootstrap": list(bootstrap)})

    # Direct context callers must also validate before their exists/pull branch.
    calls.clear()
    builder = ContextBuilder(org.settings, paths, slug="probe")
    refused = False
    with patch("runtime.orchestrator.context_builder.subprocess.run", side_effect=git):
        try:
            builder.clone_repo(workspace, "Foo", "https://example.invalid/new.git")
        except AgentParseError:
            refused = True
    check("context_builder_early_validation", {"refused": True, "git_calls": []},
          {"refused": refused, "git_calls": list(calls)})
    calls.clear()
    with patch("runtime.orchestrator.context_builder.subprocess.run", side_effect=git):
        ok = builder.clone_repo(workspace, "foo", "https://example.invalid/original.git")
    check("context_builder_lowercase_pull", {"ok": True, "git_calls": [["git", "pull", "--ff-only"]]},
          {"ok": ok, "git_calls": list(calls)})

    # Exercise real frontmatter parsing as well as both I/O admissions above.
    for key, accepted in (("foo", True), ("web-app2", True), ("a" * 32, True), ("Foo", False),
                          ("foo_bar", False), ("../foo", False), ("", False), ("a" * 33, False), ("foo\n", False)):
        from dataclasses import replace
        text = render_agent_text(replace(definition, repos={key: "https://example.invalid/repo.git"}))
        try:
            parsed = parse_agent_text(text, expected_name="probe")
            actual = key in parsed.repos
        except AgentParseError:
            actual = False
        check(f"repo_parser_{key!r}", accepted, actual)


def case_safety(report: dict[str, Any]) -> None:
    """Run only on Darwin via the dispatcher, entirely inside its checkout scratch."""
    from scripts.mac_native.probe import Statfs64

    checks: list[dict[str, Any]] = []
    report["case_safety"] = {"checks": checks, "venue": "source APFS; NOT frozen-app acceptance"}

    def check(name: str, expected: Any, actual: Any) -> None:
        checks.append(dict(name=name, status="PASS" if expected == actual else "FAIL",
                           expected=expected, actual=actual))

    with tempfile.TemporaryDirectory(prefix="case-safety-", dir=ROOT) as folder:
        scratch = Path(folder)
        libc = ct.CDLL("/usr/lib/libSystem.B.dylib", use_errno=True)
        statfs = getattr(libc, "statfs" if platform.machine() == "arm64" else "statfs$INODE64")
        statfs.argtypes = [ct.c_char_p, ct.POINTER(Statfs64)]
        statfs.restype = ct.c_int
        value = Statfs64()
        if statfs(os.fsencode(scratch), ct.byref(value)) != 0:
            raise OSError(ct.get_errno(), "statfs failed for private case-safety fixture")
        marker = scratch / "CaseMarker"
        marker.write_bytes(b"case marker")
        alias = scratch / "casemarker"
        insensitive = alias.exists() and marker.samefile(alias)
        check("case_insensitive_apfs_fixture", {"filesystem": "apfs", "alias": True},
              {"filesystem": os.fsdecode(value.fstype), "alias": insensitive})
        if not insensitive or os.fsdecode(value.fstype) != "apfs":
            raise RuntimeError("case-safety native evidence requires case-insensitive APFS scratch")
        artifact_checks(scratch, check)
        repo_checks(scratch, check)
    check("private_scratch_removed", False, scratch.exists())
    if len(json.dumps(report).encode()) > 60000:
        raise RuntimeError("case-safety report exceeds 60000 bytes")
    if any(row["status"] != "PASS" for row in checks):
        raise AssertionError("case-safety observations failed; see named expected/actual rows")
