"""Working-hours commands.

Founder-facing ``status``/``list``/``show`` mirror the dream read commands. The
``spawn`` subcommand is the agent-side wake callback: a single-line
``happyranch work-hours spawn --org <slug> --work-hour-id WORKHOUR-NNN --from-file
<path>`` that forwards the validated routine briefs to the daemon, which creates
the root tasks server-side.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from cli import _shared
from cli._shared import _fmt_ts, _ok, resolve_org_slug
from cli.client.client import OpcClient


def _spawn_payload_from_file(path: str) -> dict:
    try:
        body = json.loads(Path(path).read_text())
    except (OSError, ValueError) as exc:
        print(f"Error reading {path}: {exc}", file=sys.stderr)
        sys.exit(1)
    if not isinstance(body, dict):
        print("error: work-hours spawn payload must be a JSON object", file=sys.stderr)
        sys.exit(1)
    return body


def _client_and_org(args: argparse.Namespace) -> tuple[OpcClient, str]:
    client = OpcClient.from_env()
    slug = resolve_org_slug(
        args_org=args.org,
        available=_shared._fetch_available_orgs(client),
    )
    return client, slug


def cmd_work_hours_spawn(args: argparse.Namespace) -> None:
    if not args.org:
        print("error: --org <slug> is required for agent callbacks", file=sys.stderr)
        sys.exit(1)
    client = OpcClient.from_env()
    body = _spawn_payload_from_file(args.from_file)
    r = client.post(
        f"/api/v1/orgs/{args.org}/work-hours/{args.work_hour_id}/spawn",
        json=body,
    )
    if not _ok(r):
        return
    resp = r.json()
    spawned = resp.get("spawned_task_ids", [])
    print(
        f"ok: {resp['work_hour_id']} status={resp['status']} "
        f"spawned={len(spawned)} {spawned}"
    )


def cmd_work_hours_status(args: argparse.Namespace) -> None:
    client, slug = _client_and_org(args)
    params = {}
    if args.agent:
        params["agent"] = args.agent
    r = client.get(f"/api/v1/orgs/{slug}/work-hours/status", params=params)
    if not _ok(r):
        return
    print(json.dumps(r.json(), indent=2))


def cmd_work_hours_list(args: argparse.Namespace) -> None:
    client, slug = _client_and_org(args)
    params = {"limit": args.limit}
    if args.agent:
        params["agent"] = args.agent
    r = client.get(f"/api/v1/orgs/{slug}/work-hours", params=params)
    if not _ok(r):
        return
    work_hours = r.json().get("work_hours", [])
    if args.json:
        print(json.dumps(work_hours, indent=2))
        return
    if not work_hours:
        print("(no work hours)")
        return
    for w in work_hours:
        print(
            f"{w['work_hour_id']:12s}  {w['status']:10s}  {w['agent_name']:20s}  "
            f"{w['local_date']} {w['slot']} {w['mode']:10s}  "
            f"{_fmt_ts(w.get('ended_at') or w['scheduled_for'])}  "
            f"spawned={w['spawned_task_count']}"
        )


def cmd_work_hours_show(args: argparse.Namespace) -> None:
    client, slug = _client_and_org(args)
    r = client.get(f"/api/v1/orgs/{slug}/work-hours/{args.work_hour_id}")
    if not _ok(r):
        return
    body = r.json()
    if args.json:
        print(json.dumps(body, indent=2))
        return
    print(f"# {body['work_hour_id']} - {body['agent_name']}")
    print(
        f"status={body['status']} {body['local_date']} {body['slot']} "
        f"mode={body['mode']} scheduled={_fmt_ts(body['scheduled_for'])}"
    )
    print(f"routines={body['routine_count']} spawned={body['spawned_task_count']}")
    if body.get("spawned_task_ids"):
        print("\n## Spawned root tasks\n")
        for tid in body["spawned_task_ids"]:
            print(f"- {tid}")
    if body.get("summary"):
        print("\n## Summary\n")
        print(body["summary"])
    if body.get("error"):
        print(f"\nerror: {body['error']}")


def register(sub) -> None:
    p = sub.add_parser("work-hours", help="Per-agent working-hours wakes")
    wh_sub = p.add_subparsers(dest="work_hours_command", required=True)

    p_spawn = wh_sub.add_parser("spawn", help="Agent callback: spawn a wake's routine tasks")
    p_spawn.add_argument("--org", required=True)
    p_spawn.add_argument("--work-hour-id", required=True)
    p_spawn.add_argument("--from-file", required=True)
    p_spawn.set_defaults(func=cmd_work_hours_spawn)

    p_status = wh_sub.add_parser("status", help="Show working-hours scheduler status")
    p_status.add_argument("--org", default=None)
    p_status.add_argument("--agent")
    p_status.set_defaults(func=cmd_work_hours_status)

    p_list = wh_sub.add_parser("list", help="List recent wakes")
    p_list.add_argument("--org", default=None)
    p_list.add_argument("--agent")
    p_list.add_argument("--limit", type=int, default=20)
    p_list.add_argument("--json", action="store_true")
    p_list.set_defaults(func=cmd_work_hours_list)

    p_show = wh_sub.add_parser("show", help="Show a wake")
    p_show.add_argument("--org", default=None)
    p_show.add_argument("work_hour_id")
    p_show.add_argument("--json", action="store_true")
    p_show.set_defaults(func=cmd_work_hours_show)
