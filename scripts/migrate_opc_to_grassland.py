#!/usr/bin/env python3
"""One-shot live-data migration for the opc -> happyranch rename.

What it does:

1. Stops a running daemon (graceful TERM, then KILL after 5s).
2. Renames ``~/.opc/`` -> ``~/.happyranch/`` (refuses if target exists).
3. For every registered runtime, renames the ``opc.yaml`` marker
   -> ``happyranch.yaml`` and each org's ``opc.db*`` -> ``happyranch.db*``.
4. Rewrites the ``Bash(opc:*)`` allow rule in each agent workspace's
   ``.claude/settings.json`` (and the ``opc *`` key in ``opencode.json``).
5. Prints the list of ``happyranch init-agent`` commands the founder
   should run next to refresh each workspace's bootstrap doc + skills.

Run AFTER you have updated the source tree to the renamed branch::

    uv sync
    uv run python scripts/migrate_opc_to_happyranch.py [--dry-run]

Idempotent: rerunning is safe — rename steps skip when the destination
already exists, and the JSON rewrites are pure string substitutions.
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import sys
import time
from pathlib import Path

import yaml

OPC_HOME = Path.home() / ".opc"
HAPPYRANCH_HOME = Path.home() / ".happyranch"


def info(msg: str) -> None:
    print(msg)


def warn(msg: str) -> None:
    print(f"WARN: {msg}", file=sys.stderr)


def stop_daemon_if_running(home: Path, dry: bool) -> None:
    pid_file = home / "daemon.pid"
    if not pid_file.exists():
        return
    try:
        pid = int(pid_file.read_text().strip())
    except ValueError:
        return
    try:
        os.kill(pid, 0)
    except OSError:
        info(f"  stale daemon.pid (pid {pid} not alive) — leaving alone")
        return
    info(f"  stopping daemon (pid {pid})")
    if dry:
        return
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError:
        return
    for _ in range(5):
        time.sleep(1)
        try:
            os.kill(pid, 0)
        except OSError:
            return
    try:
        os.kill(pid, signal.SIGKILL)
    except OSError:
        pass


def rename_if_exists(src: Path, dst: Path, dry: bool) -> bool:
    if not src.exists():
        return False
    if dst.exists():
        warn(f"  target exists, skipping rename: {dst}")
        return False
    info(f"  rename: {src.name} -> {dst.name}  (in {src.parent})")
    if not dry:
        src.rename(dst)
    return True


def discover_runtimes(home: Path) -> list[Path]:
    runtimes: list[Path] = []
    seen: set[Path] = set()

    def add(p: Path) -> None:
        if p not in seen:
            seen.add(p)
            runtimes.append(p)

    runtimes_file = home / "runtimes.yaml"
    if runtimes_file.exists():
        try:
            data = yaml.safe_load(runtimes_file.read_text()) or {}
        except yaml.YAMLError:
            data = {}
        for entry in data.get("registered", []) or []:
            if entry:
                add(Path(entry))
        active = data.get("active")
        if active:
            add(Path(active))
    default_file = home / "default_runtime"
    if default_file.is_file():
        text = default_file.read_text().strip()
        if text:
            add(Path(text))
    return runtimes


def rewrite_settings_json(path: Path, dry: bool) -> bool:
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        warn(f"    cannot parse {path}: {exc}")
        return False
    allow = data.get("permissions", {}).get("allow", [])
    new_allow = [
        rule.replace("Bash(opc:", "Bash(happyranch:").replace("opc:*", "happyranch:*")
        for rule in allow
    ]
    if new_allow == allow:
        return False
    info(f"    rewriting allow rules: {path}")
    if not dry:
        data["permissions"]["allow"] = new_allow
        path.write_text(json.dumps(data, indent=2))
    return True


def rewrite_opencode_json(path: Path, dry: bool) -> bool:
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return False
    bash = data.get("permission", {}).get("bash", {})
    changed = False
    new_bash: dict[str, str] = {}
    for key, val in bash.items():
        new_key = key
        if key == "opc *":
            new_key = "happyranch *"
        elif key.startswith("opc "):
            new_key = "happyranch " + key[len("opc "):]
        if new_key != key:
            changed = True
        new_bash[new_key] = val
    if not changed:
        return False
    info(f"    rewriting opencode.json bash rules: {path}")
    if not dry:
        data["permission"]["bash"] = new_bash
        path.write_text(json.dumps(data, indent=2))
    return True


def migrate_runtime(rt: Path, dry: bool, reinit: list[tuple[Path, str, str]]) -> None:
    info(f"runtime: {rt}")
    if not rt.is_dir():
        warn("  not a directory, skipping")
        return
    rename_if_exists(rt / "opc.yaml", rt / "happyranch.yaml", dry)
    for bak in sorted(rt.glob("opc.db.bak-*")):
        rename_if_exists(
            bak, rt / bak.name.replace("opc.db", "happyranch.db", 1), dry
        )
    orgs_dir = rt / "orgs"
    if not orgs_dir.is_dir():
        return
    for org in sorted(p for p in orgs_dir.iterdir() if p.is_dir()):
        slug = org.name
        info(f"  org: {slug}")
        for suffix in ("", "-shm", "-wal", "-journal"):
            rename_if_exists(
                org / f"opc.db{suffix}", org / f"happyranch.db{suffix}", dry
            )
        for bak in sorted(org.glob("opc.db.bak-*")):
            rename_if_exists(
                bak, org / bak.name.replace("opc.db", "happyranch.db", 1), dry
            )
        ws_dir = org / "workspaces"
        if not ws_dir.is_dir():
            continue
        for ws in sorted(p for p in ws_dir.iterdir() if p.is_dir()):
            agent_name = ws.name
            info(f"    agent: {agent_name}")
            settings = ws / ".claude" / "settings.json"
            if settings.exists():
                rewrite_settings_json(settings, dry)
            oc = ws / "opencode.json"
            if oc.exists():
                rewrite_opencode_json(oc, dry)
            reinit.append((rt, slug, agent_name))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run", action="store_true", help="print actions without applying"
    )
    args = parser.parse_args()
    dry = args.dry_run
    tag = " (dry-run)" if dry else ""

    if not OPC_HOME.exists() and not HAPPYRANCH_HOME.exists():
        info("nothing to migrate: neither ~/.opc nor ~/.happyranch exists")
        return 0

    if OPC_HOME.exists():
        info(f"step 1: stop any running daemon under {OPC_HOME}{tag}")
        stop_daemon_if_running(OPC_HOME, dry)

        if HAPPYRANCH_HOME.exists():
            warn(f"refusing to rename: {HAPPYRANCH_HOME} already exists")
            warn("delete or back it up before re-running, or migrate manually")
            return 2

        info(f"step 2: rename {OPC_HOME} -> {HAPPYRANCH_HOME}{tag}")
        if not dry:
            OPC_HOME.rename(HAPPYRANCH_HOME)
    else:
        info("~/.opc not present — assuming already migrated to ~/.happyranch")

    home_for_walk = HAPPYRANCH_HOME if HAPPYRANCH_HOME.exists() else OPC_HOME
    runtimes = discover_runtimes(home_for_walk)
    if not runtimes:
        info("no registered runtimes — done")
        return 0

    info(f"step 3: migrate each registered runtime{tag}")
    reinit: list[tuple[Path, str, str]] = []
    for rt in runtimes:
        migrate_runtime(rt, dry, reinit)

    info("")
    info("migration complete.")
    info("next steps:")
    info("  1. uv sync                                    # regenerate uv.lock with the new package name")
    info("  2. scripts/daemon.sh start                    # start daemon under ~/.happyranch")
    info("  3. re-run init-agent for each existing agent so each workspace's")
    info("     CLAUDE.md and .claude/skills/ are rewritten to use `happyranch`:")
    info("")
    for _rt, slug, agent in reinit:
        info(f"     happyranch init-agent --org {slug} {agent}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
