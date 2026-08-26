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
5. **Executed negative command-path tests** (TASK-5642 fix-forward): the
   shipping shell fences for the transfer-artifact gate, the zero-count gate,
   and the immutable staged-DB validation are extracted from the runbook and
   actually executed with bash against adversarial fixtures — proving
   publication/start is unreachable on a forbidden artifact, on corrupt or
   FK-invalid DBs, and on every nonzero live-work category, while legitimate
   nested ``artifacts/**/*.tar.gz`` and a quiescent/paused-only published DB
   still pass.
6. The runbook no longer deletes source sidecars (observe/ledger/block only,
   no TOCTOU ownership claim) and no longer overclaims a daemon-child CLI
   probe that the shipped runtime cannot provide.

No runtime imports, no daemon, no network.
"""
from __future__ import annotations

import hashlib
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import textwrap
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

# Matches every ```bash fence in the runbook, including fences indented inside
# numbered list items (the content is dedented before use so the fences can be
# executed verbatim with bash).
_FENCE_RE = re.compile(r"^[ \t]*```bash\n(.*?)^[ \t]*```$", re.MULTILINE | re.DOTALL)


def _member_screen_rejection(member: str) -> str | None:
    """Return the runbook §5 step-4 rejection category for a member, or None."""
    if _APPLEDOUBLE_RE.search(member):
        return "AppleDouble ._* member"
    if _SMUGGLED_RE.search(member):
        return "smuggled transfer/inbox artifact"
    if not _UNALLOWLISTED_RE.fullmatch(member):
        return "unallowlisted member"
    return None


def _bash_fences() -> list[str]:
    """Return every bash fence body from the runbook, dedented.

    Fences nested inside numbered list items carry the list indentation in the
    markdown source; ``textwrap.dedent`` strips the common prefix so the fence
    is exactly the shell the founder runs (heredoc terminators land at column
    0, which bash requires for ``<<'PY'``).
    """
    return [
        textwrap.dedent(m.group(1))
        for m in _FENCE_RE.finditer(_RUNBOOK.read_text(encoding="utf-8"))
    ]


def _bash_fence_containing(marker: str) -> str:
    """Return the first ```bash fence whose body contains *marker*."""
    for block in _bash_fences():
        if marker in block:
            return block
    raise AssertionError(f"no ```bash fence containing {marker!r} in the runbook")


def _bash_fence_starting_with(first_commands: str) -> str:
    """Return the ```bash fence whose body starts with *first_commands*.

    Used where a plain substring marker would be ambiguous (a comment in one
    fence may mention the command another fence actually runs).
    """
    for block in _bash_fences():
        if block.strip().startswith(first_commands):
            return block
    raise AssertionError(f"no ```bash fence starting with {first_commands!r} in the runbook")


def _write_uv_shim(bindir: Path) -> None:
    """Write an offline ``uv`` shim mapping ``uv run python ...`` → ``python3``.

    The runbook's checked helpers run ``cd "$HR_CHECKOUT" && uv run python - …``
    so the founder's pinned environment is used. Tests execute the exact
    shipping fence without uv/network by front-loading this shim on PATH; the
    helper logic (stdlib sqlite3) is environment-independent.
    """
    bindir.mkdir(parents=True, exist_ok=True)
    shim = bindir / "uv"
    shim.write_text(
        "#!/usr/bin/env bash\n"
        'if [ "$1" = run ] && [ "$2" = python ]; then\n'
        "  shift 2\n"
        '  exec python3 "$@"\n'
        "fi\n"
        'exec python3 "$@"\n'
    )
    shim.chmod(0o755)


def _create_gate_fixture_db(db: Path) -> None:
    """Create a minimal published-DB fixture with the tables the §7 gate reads.

    Only the columns the gate queries are needed (``status`` on ``schedules``,
    ``tasks``, ``jobs``); the fixture is schema-agnostic and deterministic.
    """
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE schedules (id TEXT, status TEXT)")
    conn.execute("CREATE TABLE tasks (id TEXT, status TEXT)")
    conn.execute("CREATE TABLE jobs (id TEXT, status TEXT)")
    conn.commit()
    conn.close()


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


def test_transfer_artifact_gate_fence_fails_closed_and_allows_legitimate_tarballs(tmp_path):
    # Execute the shipping §6 step-2 gate fence followed by the §6 step-3 mv
    # fence (in document order) with bash: a forbidden exact-name artifact must
    # make the gate exit nonzero so `mv` is never reached, while a legitimate
    # nested artifacts/evidence/report.tar.gz must pass and publish.
    text = _RUNBOOK.read_text(encoding="utf-8")
    gate = _bash_fence_containing("GH-709 Slice A: exact transfer-artifact gate")
    mv = _bash_fence_starting_with('set -euo pipefail\nmv "$STAGE" "$DST"')
    assert 'mv "$STAGE"' not in gate  # the gate fence itself never publishes
    assert text.index("exact transfer-artifact gate") < text.index('mv "$STAGE" "$DST"')
    script = gate + "\n" + mv + "\n"

    def run_case(stage_files: dict[str, bytes]) -> tuple[int, bool]:
        stage = tmp_path / "stage"
        dst = tmp_path / "dst"
        shutil.rmtree(stage, ignore_errors=True)
        shutil.rmtree(dst, ignore_errors=True)
        stage.mkdir()
        for rel, data in stage_files.items():
            p = stage / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(data)
        env = dict(os.environ, STAGE=str(stage), DST=str(dst))
        proc = subprocess.run(
            ["bash", "-c", script], env=env,
            capture_output=True, text=True, cwd=str(tmp_path),
        )
        return proc.returncode, dst.exists()

    # forbidden: an exact operation artifact anywhere in STAGE must stop mv
    rc, published = run_case({
        "happyranch.db": b"x",
        "org/teams.yaml": b"teams: []\n",
        "artifacts/evidence/report.tar.gz": b"legit",  # legitimate — must pass
        "org-archive.tar.gz": b"forbidden",            # exact-name — must stop
    })
    assert rc != 0, "exact-name artifact in STAGE must exit nonzero"
    assert published is False, "mv must be unreachable when a transfer artifact is present"

    # legitimate: nested artifacts/**/*.tar.gz is NOT a transfer artifact
    rc, published = run_case({
        "happyranch.db": b"x",
        "org/teams.yaml": b"teams: []\n",
        "artifacts/evidence/report.tar.gz": b"legit",
    })
    assert rc == 0, "legitimate nested artifacts/**/*.tar.gz must be allowed"
    assert published is True, "publication must proceed when no artifact is flagged"


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


def test_immutable_validation_fence_rejects_corrupt_and_fk_invalid(tmp_path):
    # Execute the shipping §3 step-3 validation fence (identical helper to §5
    # step 9) with bash against healthy / corrupt / FK-invalid staged DBs: the
    # checked helper must exit 0 only for exactly-ok + empty-FK output, and
    # nonzero on corrupt or FK-invalid output (publication unreachable).
    fence = _bash_fence_containing(
        "GH-709 Slice A: checked immutable staged-DB validation"
    )
    bindir = tmp_path / "bin"
    _write_uv_shim(bindir)

    def run_case(build_db) -> tuple[int, str, str]:
        stage = tmp_path / "stage"
        shutil.rmtree(stage, ignore_errors=True)
        stage.mkdir()
        db = stage / "happyranch.db"
        build_db(db)
        env = dict(
            os.environ,
            PATH=f"{bindir}:{os.environ.get('PATH', '')}",
            HR_CHECKOUT=str(tmp_path),
            STAGE=str(stage),
        )
        proc = subprocess.run(
            ["bash", "-c", fence], env=env,
            capture_output=True, text=True, cwd=str(tmp_path),
        )
        return proc.returncode, proc.stdout, proc.stderr

    def healthy(db: Path) -> None:
        conn = sqlite3.connect(db)
        conn.execute("CREATE TABLE parent(id INTEGER PRIMARY KEY)")
        conn.execute("CREATE TABLE child(pid INTEGER REFERENCES parent(id))")
        conn.execute("INSERT INTO parent(id) VALUES (1)")
        conn.execute("INSERT INTO child(pid) VALUES (1)")
        conn.commit()
        conn.close()

    def fk_invalid(db: Path) -> None:
        conn = sqlite3.connect(db)
        conn.execute("CREATE TABLE parent(id INTEGER PRIMARY KEY)")
        conn.execute("CREATE TABLE child(pid INTEGER REFERENCES parent(id))")
        conn.execute("INSERT INTO child(pid) VALUES (42)")  # orphan row
        conn.commit()
        conn.close()

    def truncated(db: Path) -> None:
        healthy(db)
        data = db.read_bytes()
        db.write_bytes(data[: max(1, len(data) // 3)])  # damaged pages

    def garbage(db: Path) -> None:
        db.write_bytes(b"definitely not a sqlite database" * 4)

    rc, out, err = run_case(healthy)
    assert rc == 0, (out, err)
    assert "staged DB valid" in out
    # validation must not create -wal/-shm beside the staged candidate
    assert not (tmp_path / "stage" / "happyranch.db-wal").exists()
    assert not (tmp_path / "stage" / "happyranch.db-shm").exists()

    rc, out, err = run_case(fk_invalid)
    assert rc != 0, "FK-invalid output must stop the validation"
    assert "FOREIGN_KEY_VIOLATIONS" in err

    rc, out, err = run_case(truncated)
    assert rc != 0, "corrupt output must stop the validation"
    assert "INTEGRITY_CHECK" in err

    rc, out, err = run_case(garbage)
    assert rc != 0, "non-database file must stop the validation"
    assert "CANNOT OPEN STAGED DB" in err or "INTEGRITY_CHECK ERROR" in err


# ── §7: zero-count gate (assert, not print) + start unreachable ─────────────


def test_zero_count_gate_fence_blocks_start_per_nonzero_category(tmp_path):
    # Execute the shipping §7 zero-count gate fence followed by the §7 start
    # fence (in document order) with bash. For EVERY nonzero live-work
    # category the gate must exit nonzero and `scripts/daemon.sh start` must
    # never be invoked (sentinel absent); a paused-only or fully quiescent
    # published DB must pass and reach start.
    text = _RUNBOOK.read_text(encoding="utf-8")
    gate = _bash_fence_containing("GH-709 Slice A: mandatory zero-count gate")
    start = _bash_fence_starting_with(
        "set -euo pipefail\nscripts/daemon.sh start"
    )
    assert start not in (gate,)
    # The §7 start fence must follow the zero-count gate fence in document
    # order (compare fence positions, not first textual occurrence — §7.2 and
    # the header legitimately mention `scripts/daemon.sh start` earlier).
    fences = _bash_fences()
    assert fences.index(gate) < fences.index(start)
    script = gate + "\n" + start + "\n"

    bindir = tmp_path / "bin"
    _write_uv_shim(bindir)
    sentinel = tmp_path / "start-invoked"
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    (scripts / "daemon.sh").write_text(
        "#!/usr/bin/env bash\n"
        f"touch {sentinel}\n"
        "exit 0\n"
    )
    (scripts / "daemon.sh").chmod(0o755)
    (bindir / "happyranch").write_text("#!/usr/bin/env bash\nexit 0\n")
    (bindir / "happyranch").chmod(0o755)

    def run_case(seed) -> tuple[int, bool, str]:
        dst = tmp_path / "dst-case"
        shutil.rmtree(dst, ignore_errors=True)
        dst.mkdir()
        _create_gate_fixture_db(dst / "happyranch.db")
        conn = sqlite3.connect(dst / "happyranch.db")
        seed(conn)
        conn.commit()  # persist the seeded row (close() would roll back)
        conn.close()
        if sentinel.exists():
            sentinel.unlink()
        env = dict(
            os.environ,
            PATH=f"{bindir}:{os.environ.get('PATH', '')}",
            HR_CHECKOUT=str(tmp_path),
            DST=str(dst),
        )
        proc = subprocess.run(
            ["bash", "-c", script], env=env,
            capture_output=True, text=True, cwd=str(tmp_path),
        )
        return proc.returncode, sentinel.exists(), proc.stderr

    # armed / firing schedules — runnable, must block
    rc, started, err = run_case(lambda c: c.execute(
        "INSERT INTO schedules VALUES ('s1','armed')"))
    assert rc != 0 and not started, (rc, err)
    rc, started, err = run_case(lambda c: c.execute(
        "INSERT INTO schedules VALUES ('s2','firing')"))
    assert rc != 0 and not started, (rc, err)
    # live tasks — must block
    rc, started, err = run_case(lambda c: c.execute(
        "INSERT INTO tasks VALUES ('t1','pending')"))
    assert rc != 0 and not started, (rc, err)
    rc, started, err = run_case(lambda c: c.execute(
        "INSERT INTO tasks VALUES ('t2','in_progress')"))
    assert rc != 0 and not started, (rc, err)
    # live jobs — must block
    rc, started, err = run_case(lambda c: c.execute(
        "INSERT INTO jobs VALUES ('j1','running')"))
    assert rc != 0 and not started, (rc, err)
    rc, started, err = run_case(lambda c: c.execute(
        "INSERT INTO jobs VALUES ('j2','pending')"))
    assert rc != 0 and not started, (rc, err)
    # unknown (non-terminal, non-paused) schedule status — must block
    rc, started, err = run_case(lambda c: c.execute(
        "INSERT INTO schedules VALUES ('s3','something_new')"))
    assert rc != 0 and not started, (rc, err)

    # paused schedule (suspended until explicit re-arm) — must NOT block
    rc, started, err = run_case(lambda c: c.execute(
        "INSERT INTO schedules VALUES ('s4','paused')"))
    assert rc == 0 and started, (rc, err)
    # terminal schedules — must NOT block
    rc, started, err = run_case(lambda c: c.execute(
        "INSERT INTO schedules VALUES ('s5','fired')"))
    assert rc == 0 and started, (rc, err)
    # fully quiescent published DB — start permitted
    rc, started, err = run_case(lambda c: None)
    assert rc == 0 and started, (rc, err)


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
    # 7. launch diagnostics
    assert "happyranch doctor" in text
    assert "command -v uv" in text
    # 10. Slice D: the synchronous uv launch preflight is now SHIPPED in the
    # script; the old "not shipped, run operator diagnostics" wording is gone
    assert "preflight is Slice D work that is **not shipped**" not in text
    assert "Slice D ships a" in text and "synchronous uv launch preflight" in text
    assert "scripts/daemon.sh start" in text
    # no automatic download / no alternate CLI selection
    assert "no automatic download" in text
    assert "no arbitrary PATH fallback" in text
    assert "no alternate CLI" in text
    # honesty boundary: B/C/D runtime guarantees not shipped; operator-enforced
    assert "operator-enforced" in text
    assert "not shipped" in text
    # zero live tasks/jobs gate now includes jobs (helper labels)
    assert "jobs_pending_running" in text
    # 8. gates ASSERT (fail closed), never print-and-continue
    assert "TRANSFER ARTIFACT PRESENT IN STAGE" in text
    assert "ZERO-COUNT GATE FAILED" in text
    assert "INTEGRITY_CHECK NOT OK" in text
    assert "FOREIGN_KEY_VIOLATIONS" in text
    # 9. paused-vs-runnable schedule distinction (current enums, no schema change)
    assert "schedules_not_paused_nonterminal" in text
    assert "explicit operator re-arm" in text
    # 10. daemon-child parity limitation stated accurately (no false claim)
    assert "no existing non-mutating daemon-child diagnostic seam" in text
    assert "proves the CLI the children will invoke is the" not in text


def test_runbook_removed_source_sidecar_deletion_and_toctou_claims():
    text = _RUNBOOK.read_text(encoding="utf-8")
    # Slice A must NOT clean source sidecars (destructive-cleanup boundary)
    assert 'rm -f "$SRC/happyranch.db-wal"' not in text
    assert "only ever remove files this runbook just created" not in text
    assert "removed — source clean" not in text
    # observe/ledger/block semantics present instead
    assert "OBSERVES and records only" in text
    assert "never deletes source sidecars" in text
    # the WAL-aware source backup is NOT weakened
    assert "reader.backup(writer)" in text
    assert "mode=ro" in text


def test_spec_records_the_slice_a_terminal_job_disposition():
    spec = (
        _REPO_ROOT / "docs" / "superpowers" / "specs"
        / "org-portability-reference-consumers.md"
    )
    text = spec.read_text(encoding="utf-8")
    assert "Manual Slice-A disposition (GH-709, founder decision" in text
    assert "does **not** rewrite `cwd_hint`" in text
    assert "historical stream links may be unavailable after relocation" in text
