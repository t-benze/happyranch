"""Reusable complete historical schema fixture support (THR-229 checkpoint C3a).

This module owns the checked-in full historical schema fixture and the
reconstruction helper used by the targeted schema-integrity tests and the R3
shipping venue.

The fixture is derived from the FULL historical ``Database`` constructor at
the immutable revision ``f39b4934611ca13ab7d8b7fa2d7be983a4bfb7a5`` (the exact
parent of the accepted migration checkpoint).  Generation extracts only
``runtime/`` from that revision and creates one empty historical database in an
isolated subprocess whose ``runtime`` / ``models`` / ``work_hours_store`` /
``schedule_store`` imports all resolve inside that same extracted source.
Recorded provenance includes the source commit, a canonical runtime source
digest and the per-module hashes; no absolute temporary path is stored.

Reconstruction replays every captured historical table/index/trigger/view
``sqlite_master`` statement in creation order and re-inserts the constructor's
initial marker rows, then the caller opens it with the ACTUAL current
``Database`` path so the real migration runs.  This is deliberately the full
historical constructor, NOT the reduced two-table local-CI fixture.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURE_PATH = (
    REPO_ROOT / "tests" / "fixtures" / "authority_v2_historical_schema.json"
)

HISTORICAL_SOURCE_COMMIT = "f39b4934611ca13ab7d8b7fa2d7be983a4bfb7a5"
HISTORICAL_SOURCE_PARENT = "37df230c00a2e75e9f1e58e82dac0f9d5dad781b"

_FIXTURE_CACHE: dict | None = None

_GENERATOR = "tests/authority_v2_historical_schema.py"


def load_historical_fixture() -> dict:
    """Load (and cache) the checked-in historical schema fixture."""
    global _FIXTURE_CACHE
    if _FIXTURE_CACHE is None:
        with FIXTURE_PATH.open("r", encoding="utf-8") as handle:
            _FIXTURE_CACHE = json.load(handle)
    return _FIXTURE_CACHE


def historical_inventory_digest(inventory: dict) -> str:
    return hashlib.sha256(
        json.dumps(inventory, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _capture_schema_inventory(db_path: Path) -> dict:
    """Capture the generic complete schema inventory of a plain DB file."""
    from runtime.orchestrator.authority import _v2_capture_inventory

    conn = sqlite3.connect(str(db_path))
    try:
        return _v2_capture_inventory(conn)
    finally:
        conn.close()


def reconstruct_historical_database(
    db_path: Path, fixture: dict | None = None,
) -> Path:
    """Rebuild the full historical schema (creation order) + seeded marker rows.

    The caller is responsible for opening ``db_path`` with the current
    ``Database`` so the actual migration path runs.
    """
    payload = fixture if fixture is not None else load_historical_fixture()
    db_path = Path(db_path)
    if db_path.exists():
        db_path.unlink()
    conn = sqlite3.connect(str(db_path))
    try:
        for obj in payload["objects"]:
            if obj["sql"]:
                conn.execute(obj["sql"])
        for seed in payload.get("initial_rows", []):
            columns = ", ".join(f'"{name}"' for name in seed["columns"])
            placeholders = ", ".join("?" for _ in seed["columns"])
            conn.executemany(
                f'INSERT INTO "{seed["table"]}" ({columns}) VALUES ({placeholders})',
                [tuple(row) for row in seed["rows"]],
            )
        conn.commit()
    finally:
        conn.close()
    return db_path


def _current_capture_inventory_for_file(db_path: Path) -> dict:
    return _capture_schema_inventory(db_path)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _runtime_source_digest(root: Path) -> tuple[str, int]:
    entries: list[tuple[str, str]] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        if "__pycache__" in path.parts:
            continue
        entries.append((str(path.relative_to(root)), _sha256_file(path)))
    digest = hashlib.sha256(
        "\n".join(f"{rel}\x00{dig}" for rel, dig in entries).encode()
    ).hexdigest()
    return digest, len(entries)


def generate_historical_fixture(
    *,
    repo: Path,
    commit: str = HISTORICAL_SOURCE_COMMIT,
    out_path: Path = FIXTURE_PATH,
    workdir: Path | None = None,
) -> dict:
    """Regenerate the checked-in fixture from the immutable historical source.

    This is a maintainer-only, provenance-recording operation.  It extracts the
    historical ``runtime/`` and creates the empty historical database in an
    isolated subprocess; tests never need to run it.
    """
    repo = Path(repo).resolve()
    out_path = Path(out_path)
    scratch = Path(workdir).resolve() if workdir else Path(
        tempfile.mkdtemp(prefix="hr-historical-schema-")
    )
    scratch.mkdir(parents=True, exist_ok=True)
    source_root = scratch / "historical-runtime"
    if source_root.exists():
        import shutil

        shutil.rmtree(source_root)
    source_root.mkdir(parents=True)

    archive = subprocess.run(
        ["git", "-C", str(repo), "archive", commit, "runtime"],
        capture_output=True, check=True,
    )
    extract = subprocess.run(
        ["tar", "-x", "-C", str(source_root)],
        input=archive.stdout, capture_output=True, check=True,
    )
    del extract

    hist_db = scratch / "historical.db"
    if hist_db.exists():
        hist_db.unlink()
    script = (
        "import json, sys;"
        "from pathlib import Path;"
        "import runtime, runtime.models, runtime.infrastructure.work_hours_store,"
        " runtime.infrastructure.schedule_store;"
        "from runtime.infrastructure.database import Database;"
        "db = Database(Path(sys.argv[1])); db._conn.close();"
        "print(json.dumps({'runtime': runtime.__file__,"
        "'models': runtime.models.__file__,"
        "'work_hours_store': runtime.infrastructure.work_hours_store.__file__,"
        "'schedule_store': runtime.infrastructure.schedule_store.__file__}))"
    )
    env = {
        "PATH": os.environ.get("PATH", ""),
        "PYTHONPATH": str(source_root),
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONHASHSEED": "0",
    }
    completed = subprocess.run(
        [sys.executable, "-c", script, str(hist_db)],
        cwd=str(scratch), env=env, capture_output=True, text=True, check=True,
    )
    module_paths = json.loads(completed.stdout.strip().splitlines()[-1])
    for label, module_path in module_paths.items():
        if not str(Path(module_path).resolve()).startswith(str(source_root)):
            raise RuntimeError(
                f"historical {label} import did not resolve inside the extracted source"
            )

    conn = sqlite3.connect(str(hist_db))
    try:
        objects = [
            {"type": row[0], "name": row[1], "tbl_name": row[2], "sql": row[3]}
            for row in conn.execute(
                "SELECT type, name, tbl_name, sql FROM sqlite_master "
                "WHERE name NOT LIKE 'sqlite_%' ORDER BY rowid"
            )
        ]
        initial_rows = []
        for (table,) in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%' ORDER BY name"
        ):
            columns = [row[1] for row in conn.execute(f'PRAGMA table_info("{table}")')]
            rows = [list(row) for row in conn.execute(f'SELECT * FROM "{table}"')]
            if rows:
                initial_rows.append(
                    {"table": table, "columns": columns, "rows": rows}
                )
    finally:
        conn.close()

    inventory = _capture_schema_inventory(hist_db)
    source_digest, source_count = _runtime_source_digest(source_root / "runtime")
    module_hashes = {
        label: _sha256_file(Path(path))
        for label, path in module_paths.items()
    }
    payload = {
        "fixture_version": 1,
        "provenance": {
            "source_commit": commit,
            "source_parent": HISTORICAL_SOURCE_PARENT,
            "generated_by": _GENERATOR,
            "python_version": sys.version.split()[0],
            "sqlite_version": sqlite3.sqlite_version,
            "runtime_source_sha256": source_digest,
            "runtime_source_file_count": source_count,
            "module_sha256": module_hashes,
            "sanitized_paths": True,
        },
        "objects": objects,
        "initial_rows": initial_rows,
        "object_count": len(objects),
        "historical_inventory_digest": historical_inventory_digest(inventory),
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, indent=1, sort_keys=True) + "\n")
    return payload


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default=str(REPO_ROOT))
    parser.add_argument("--commit", default=HISTORICAL_SOURCE_COMMIT)
    parser.add_argument("--out", default=str(FIXTURE_PATH))
    parser.add_argument("--workdir", default=None)
    args = parser.parse_args(argv)
    payload = generate_historical_fixture(
        repo=Path(args.repo), commit=args.commit, out_path=Path(args.out),
        workdir=Path(args.workdir) if args.workdir else None,
    )
    print(
        "wrote", args.out, "objects=", payload["object_count"],
        "digest=", payload["historical_inventory_digest"],
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
