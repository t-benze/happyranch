"""Deterministic doc/harness contract tests for the GH-709 Slice A runbook.

GH-709 Slice A is a documentation/runbook PR
(``docs/operations/offline-organization-relocation.md``). These tests pin the
**command contracts** the runbook now prescribes so they cannot silently
regress, and they exercise the sidecar-free SQLite validation semantics the
runbook relies on — all on the Linux test host with stdlib only:

1. The §4 archive recipe sets ``COPYFILE_DISABLE=1`` and the §5 member screen
   explicitly rejects injected ``._*`` AppleDouble members (defense in depth).
   This is a **documented recipe contract** — it does NOT claim a real macOS
   result; the runbook itself requires verification on a real macOS machine
   and the test asserts that caveat is present.
2. The §5/§6/§7 staged-DB validation uses URI ``immutable=1`` opens after
   proving no pre-existing candidate sidecars, creates no ``-wal``/``-shm``,
   and leaves the staged file byte-identical (no self-induced manifest drift).
3. The transfer-artifact gate matches the six exact operation filenames only —
   legitimate ``artifacts/**/*.tar.gz`` evidence bundles pass.
4. The terminal-job, multi-org ledger, readiness, and diagnostics contracts
   are present in the runbook text.

No runtime imports, no daemon, no network.
"""
from __future__ import annotations

import hashlib
import re
import sqlite3
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_RUNBOOK = _REPO_ROOT / "docs/operations" / "offline-organization-relocation.md"

# The exact name/type checks the runbook §5 step 4 prescribes (kept in lockstep
# with the fenced command in the document).
_APPLEDOUBLE_RE = re.compile(r"(^|/)\._")
_SMUGGLED_RE = re.compile(
    r"(^|/)(org-archive\.tar\.gz|manifest\.txt|archive\.sha256|"
    r"members\.txt|members-typed\.txt|staged-manifest\.txt)$"
)
_UNALLOWLISTED_RE = re.compile(
    r"happyranch\.db|(org|artifacts|kb|threads|task-attachments|jobs|dreams|"
    r"work_hours|schedules|talks|skills)(/.*)?|"
    r"workspaces(/[^/]+(/memory(/.*)?)?)?"
)
_OPERATION_ARTIFACT_NAMES = frozenset({
    "org-archive.tar.gz", "manifest.txt", "archive.sha256",
    "members.txt", "members-typed.txt", "staged-manifest.txt",
})


def _member_screen_rejection(member: str) -> str | None:
    """Return the runbook §5 step-4 rejection category for a member, or None."""
    if _APPLEDOUBLE_RE.search(member):
        return "AppleDouble ._* member"
    if _SMUGGLED_RE.search(member):
        return "smuggled transfer/inbox artifact"
    if not _UNALLOWLISTED_RE.fullmatch(member):
        return "unallowlisted member"
    return None


# ── §5 step 4: member screen (AppleDouble + exact-name smuggling) ──────────


def test_member_screen_rejects_injected_appledouble_members():
    assert _member_screen_rejection("._foo") == "AppleDouble ._* member"
    assert _member_screen_rejection("._.DS_Store") == "AppleDouble ._* member"
    assert _member_screen_rejection("org/._agents") == "AppleDouble ._* member"
    assert _member_screen_rejection("workspaces/alice/memory/._notes") == (
        "AppleDouble ._* member"
    )


def test_member_screen_allows_legitimate_artifact_tarballs():
    # GH-709 finding 3: legitimate archived evidence bundles under artifacts/
    # are NOT operation transfer artifacts and must pass every check.
    assert _member_screen_rejection("artifacts/evidence/report.tar.gz") is None
    assert _member_screen_rejection("artifacts/report.tgz") is None
    assert _member_screen_rejection("artifacts/custom-skills/s/d/SKILL.md") is None


def test_member_screen_rejects_exact_operation_artifact_names_at_any_depth():
    for name in _OPERATION_ARTIFACT_NAMES:
        assert _member_screen_rejection(name) == "smuggled transfer/inbox artifact"
        assert _member_screen_rejection(f"artifacts/{name}") == (
            "smuggled transfer/inbox artifact"
        )
    assert _member_screen_rejection(".DS_Store") == "unallowlisted member"
    assert _member_screen_rejection("jobs/JOB-1.out") is None  # classifier-consistent


# ── §6 step 2: exact transfer-artifact gate (no extension-wide *.tar.gz) ────


def test_publish_layout_gate_matches_only_operation_filenames(tmp_path):
    # Build a STAGE tree exactly like the §4 payload plus stray operation
    # artifacts, mirroring `find "$STAGE" \( -name 'org-archive.tar.gz' -o
    # -name 'manifest.txt' -o -name 'archive.sha256' -o -name 'members.txt' -o
    # -name 'members-typed.txt' -o -name 'staged-manifest.txt' \) -print`.
    (tmp_path / "happyranch.db").write_bytes(b"x")
    for d in ("org", "artifacts/evidence", "kb"):
        (tmp_path / d).mkdir(parents=True)
    (tmp_path / "org" / "teams.yaml").write_text("teams: []\n")
    # legitimate evidence bundle — must NOT be flagged
    (tmp_path / "artifacts" / "evidence" / "report.tar.gz").write_bytes(b"bundle")
    # actual operation artifacts — must be flagged
    (tmp_path / "org-archive.tar.gz").write_bytes(b"archive")
    (tmp_path / "manifest.txt").write_text("m")
    (tmp_path / "archive.sha256").write_text("h")

    flagged = sorted(
        p.name for p in tmp_path.rglob("*")
        if p.is_file() and p.name in _OPERATION_ARTIFACT_NAMES
    )
    assert flagged == ["archive.sha256", "manifest.txt", "org-archive.tar.gz"]
    assert (tmp_path / "artifacts/evidence/report.tar.gz").is_file()  # untouched


# ── §3/§5 step 9/§7: immutable staged-DB validation ─────────────────────────


def _build_wal_source(db_path: Path, rows: int) -> None:
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("CREATE TABLE t(x)")
    conn.execute("CREATE TABLE child(y)")
    conn.executemany("INSERT INTO t VALUES (?)", [(i,) for i in range(rows)])
    conn.commit()
    conn.close()


def test_immutable_staged_validation_creates_no_sidecars_and_sees_all_rows(tmp_path):
    src = tmp_path / "happyranch.db"
    _build_wal_source(src, rows=500)
    # cleanly-closed WAL source has no sidecars
    assert not (tmp_path / "happyranch.db-wal").exists()
    assert not (tmp_path / "happyranch.db-shm").exists()

    # §3 step 2 logical snapshot (WAL-aware backup — never immutable on source)
    stage = tmp_path / "stage.db"
    reader = sqlite3.connect(f"file:{src}?mode=ro", uri=True)
    dest = sqlite3.connect(stage)
    reader.backup(dest)
    dest.close()
    reader.close()
    # the staged backup is self-contained: immutable reads all 500 rows
    # (the runbook never opens the staged DB with an ordinary connection)
    imm0 = sqlite3.connect(f"file:{stage}?immutable=1", uri=True)
    assert imm0.execute("SELECT COUNT(*) FROM t").fetchone()[0] == 500
    imm0.close()

    # §3 step 3 / §5 step 9: prove no pre-existing candidate sidecars, validate
    # with immutable=1, prove none were created and the file is byte-identical.
    assert not (tmp_path / "stage.db-wal").exists()
    assert not (tmp_path / "stage.db-shm").exists()
    digest_before = hashlib.sha256(stage.read_bytes()).hexdigest()

    imm = sqlite3.connect(f"file:{stage}?immutable=1", uri=True)
    assert imm.execute("PRAGMA integrity_check;").fetchone()[0] == "ok"
    assert imm.execute("PRAGMA foreign_key_check;").fetchall() == []
    assert imm.execute("SELECT COUNT(*) FROM t").fetchone()[0] == 500
    imm.close()

    assert not (tmp_path / "stage.db-wal").exists()
    assert not (tmp_path / "stage.db-shm").exists()
    assert hashlib.sha256(stage.read_bytes()).hexdigest() == digest_before


def test_mode_ro_open_creates_sidecars_why_immutable_is_required(tmp_path):
    # Contrast: the pre-GH-709 recipes used plain and `mode=ro` (`-readonly`)
    # opens. A read-only WAL reader next to a sidecar-free WAL DB initializes
    # WAL shared memory and CREATES a `-shm` (repo-pinned property — the
    # stale-job observer records it; verified on this host). This is why the
    # runbook forbids both on the staged snapshot and mandates `immutable=1`.
    src = tmp_path / "happyranch.db"
    _build_wal_source(src, rows=1)
    assert not (tmp_path / "happyranch.db-wal").exists()
    assert not (tmp_path / "happyranch.db-shm").exists()
    ro = sqlite3.connect(f"file:{src}?mode=ro", uri=True)
    ro.execute("PRAGMA integrity_check;").fetchone()
    ro.close()
    assert (tmp_path / "happyranch.db-wal").exists() or (
        tmp_path / "happyranch.db-shm").exists()


# ── Doc command-contract assertions (runbook text) ──────────────────────────


def test_runbook_pins_the_gh709_slice_a_contracts():
    text = _RUNBOOK.read_text(encoding="utf-8")
    # 1. macOS AppleDouble: COPYFILE_DISABLE recipe + explicit macOS caveat
    assert "export COPYFILE_DISABLE=1" in text
    assert "verify the recipe on a real macOS machine" in text
    # 2. staged-DB validation through immutable=1, never replacing source backup
    assert "?immutable=1" in text
    assert "immutable must never replace the WAL-aware source backup" in text
    # 3. exact transfer-artifact gate: six names, no extension-wide *.tar.gz
    assert "-name 'org-archive.tar.gz'" in text
    assert "-name '*.tar.gz'" not in text
    # 4. terminal-job policy: retained, not rewritten, links may be unavailable
    assert "may be unavailable (empty) after relocation" in text
    assert "no `stdout_path`/`stderr_path`/`cwd_hint` value is rewritten" in text
    # 5. loader-backed inventory + per-org operation ledger
    assert "iter_org_roots" in text
    assert "operation ledger" in text
    # 6. mandatory post-publication readiness (marker verification)
    assert "readiness marker is a regular file" in text
    assert "executor-binaries list" in text
    # 7. launch/child-CLI diagnostics
    assert "happyranch doctor" in text
    assert "command -v uv" in text
    # honesty boundary: B/C/D runtime guarantees not shipped; operator-enforced
    assert "operator-enforced" in text
    assert "not shipped" in text
    # zero live tasks/jobs gate now includes jobs
    assert "jobs_pending_running" in text


def test_spec_records_the_slice_a_terminal_job_disposition():
    spec = (
        _REPO_ROOT / "docs" / "superpowers" / "specs"
        / "org-portability-reference-consumers.md"
    )
    text = spec.read_text(encoding="utf-8")
    assert "Manual Slice-A disposition (GH-709, founder decision" in text
    assert "does **not** rewrite `cwd_hint`" in text
    assert "historical stream links may be unavailable after relocation" in text
