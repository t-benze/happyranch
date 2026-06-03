"""Shared CLI helpers used across `cli.commands.*` (and re-exported by cli.main)."""
from __future__ import annotations

import os
import sys


def _ok(r) -> bool:
    """True if response is 2xx. On error: print a friendly message and exit(1).

    Translates the daemon's structured `{"detail": {"code": ...}}` errors into
    actionable user-facing sentences instead of dumping JSON.
    """
    if 200 <= r.status_code < 300:
        return True
    detail = {}
    try:
        body = r.json()
        if isinstance(body.get("detail"), dict):
            detail = body["detail"]
    except ValueError:
        pass
    code = detail.get("code")
    if code == "no_active_runtime":
        print("No active runtime. Run `happyranch use <runtime-path>` first (see `happyranch init`).")
    elif code == "active_tasks_in_flight":
        print(f"Cannot proceed: tasks still in flight ({detail.get('task_ids')}).")
    elif code == "unknown_session":
        print(
            f"Session not recognised by daemon for task {detail.get('task_id')} "
            f"(agent {detail.get('agent')}). The daemon may have restarted, or "
            "the task already completed.",
        )
    elif code == "session_mismatch":
        print(
            f"Session id mismatch — daemon expected {detail.get('active')} "
            f"but got {detail.get('got')}.",
        )
    else:
        print(f"Error ({r.status_code}): {r.text}")
    sys.exit(1)



def resolve_org_slug(*, args_org: str | None, available: list[str]) -> str:
    """Resolve the per-command --org per the spec §7.4 chain."""
    if args_org:
        return args_org
    env = os.environ.get("HAPPYRANCH_ORG_SLUG")
    if env:
        return env
    if len(available) == 1:
        return available[0]
    if not available:
        print(
            "error: no orgs registered yet\n"
            "create one with: happyranch orgs init <slug> [--from <example-path>]",
            file=sys.stderr,
        )
        sys.exit(1)
    print(
        "error: --org <slug> is required\navailable orgs:",
        file=sys.stderr,
    )
    for slug in sorted(available):
        print(f"  {slug}", file=sys.stderr)
    sys.exit(1)



def _fetch_available_orgs(client) -> list[str]:
    r = client.get("/api/v1/orgs")
    if r.status_code != 200:
        return []
    return [o["slug"] for o in r.json().get("orgs", [])]



def _fmt_ts(iso: str | None, *, date_only: bool = False) -> str:
    """Render a UTC ISO timestamp from the daemon in the machine's local tz.

    Storage is always UTC; display is always local. Unknown or malformed
    values render as "-" so callers don't need to pre-check.
    """
    if not iso:
        return "-"
    from datetime import datetime
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return iso
    if dt.tzinfo is not None:
        dt = dt.astimezone()
    return dt.strftime("%Y-%m-%d" if date_only else "%Y-%m-%d %H:%M:%S")

