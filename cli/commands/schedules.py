"""Agent Todos — founder-facing CLI for schedule management (THR-105 Phase 3).

Commands: list, show, pause, cancel, edit.

User-facing label: Todos.  Internal primitive: Schedule / SCHEDULE-NNN.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from cli import _shared
from cli._shared import _fmt_ts, _ok, resolve_org_slug
from cli.client.client import OpcClient


def _client_and_org(args: argparse.Namespace) -> tuple[OpcClient, str]:
    client = OpcClient.from_env()
    slug = resolve_org_slug(
        args_org=args.org,
        available=_shared._fetch_available_orgs(client),
    )
    return client, slug


TODOS_BASE = "/schedules"


def cmd_schedules_list(args: argparse.Namespace) -> None:
    client, slug = _client_and_org(args)
    params = {"limit": args.limit}
    if args.agent:
        params["agent"] = args.agent
    if args.status:
        params["status"] = args.status
    r = client.get(f"/api/v1/orgs/{slug}{TODOS_BASE}", params=params)
    if not _ok(r):
        return
    schedules = r.json().get("schedules", [])
    if args.json:
        print(json.dumps(schedules, indent=2))
        return
    if not schedules:
        print("(no Todos)")
        return
    print(f"{'ID':14s}  {'STATUS':12s}  {'KIND':10s}  {'AGENT':20s}  {'FIRE AT':25s}  {'BRIEF'}")
    print("-" * 120)
    for s in schedules:
        brief = s.get("normalized_brief", "")[:50]
        print(
            f"{s['schedule_id']:14s}  {s['status']:12s}  {s['kind']:10s}  "
            f"{s['agent_name']:20s}  {_fmt_ts(s['fire_at']):25s}  {brief}"
        )


def cmd_schedules_show(args: argparse.Namespace) -> None:
    client, slug = _client_and_org(args)
    r = client.get(f"/api/v1/orgs/{slug}{TODOS_BASE}/{args.schedule_id}")
    if not _ok(r):
        return
    body = r.json()
    if args.json:
        print(json.dumps(body, indent=2))
        return
    print(f"# {body['schedule_id']} — {body['agent_name']}")
    print(f"  status={body['status']}  kind={body['kind']}")
    print(f"  fire_at={_fmt_ts(body['fire_at'])}  timezone={body['timezone']}")
    if body.get("recurrence"):
        print(f"  recurrence={json.dumps(body['recurrence'])}")
    print(f"  brief: {body['normalized_brief']}")
    print(f"  instruction: {body['source_instruction']}")
    print(f"  fire_count={body['fire_count']}  active={body['active']}")
    if body.get("expires_at"):
        print(f"  expires_at={_fmt_ts(body['expires_at'])}")
    if body.get("spawned_task_ids"):
        print(f"  spawned_tasks={body['spawned_task_ids']}")
    if body.get("last_fired_at"):
        print(f"  last_fired_at={_fmt_ts(body['last_fired_at'])}")
    print(f"  created_at={_fmt_ts(body['created_at'])}  updated_at={_fmt_ts(body['updated_at'])}")


def cmd_schedules_pause(args: argparse.Namespace) -> None:
    client, slug = _client_and_org(args)
    r = client.post(f"/api/v1/orgs/{slug}{TODOS_BASE}/{args.schedule_id}/pause")
    if not _ok(r):
        return
    body = r.json()
    print(f"ok: {body['schedule_id']} paused (status={body['status']})")


def cmd_schedules_cancel(args: argparse.Namespace) -> None:
    client, slug = _client_and_org(args)
    r = client.post(f"/api/v1/orgs/{slug}{TODOS_BASE}/{args.schedule_id}/cancel")
    if not _ok(r):
        return
    body = r.json()
    print(f"ok: {body['schedule_id']} cancelled (status={body['status']})")


def _edit_payload_from_file(path: str) -> dict:
    try:
        body = json.loads(Path(path).read_text())
    except (OSError, ValueError) as exc:
        print(f"Error reading {path}: {exc}", file=sys.stderr)
        sys.exit(1)
    if not isinstance(body, dict):
        print("error: edit payload must be a JSON object", file=sys.stderr)
        sys.exit(1)
    return body


def cmd_schedules_edit(args: argparse.Namespace) -> None:
    client, slug = _client_and_org(args)
    body = _edit_payload_from_file(args.from_file)
    r = client.patch(f"/api/v1/orgs/{slug}{TODOS_BASE}/{args.schedule_id}", json=body)
    if not _ok(r):
        return
    resp = r.json()
    print(f"ok: {resp['schedule_id']} edited (status={resp['status']})")


def register(sub) -> None:
    p = sub.add_parser("todos", help="Agent Todos — list/show/pause/cancel/edit schedules")
    wh_sub = p.add_subparsers(dest="schedules_command", required=True)

    p_list = wh_sub.add_parser("list", help="List Todos")
    p_list.add_argument("--org", default=None)
    p_list.add_argument("--agent")
    p_list.add_argument("--status")
    p_list.add_argument("--limit", type=int, default=50)
    p_list.add_argument("--json", action="store_true")
    p_list.set_defaults(func=cmd_schedules_list)

    p_show = wh_sub.add_parser("show", help="Show a Todo")
    p_show.add_argument("--org", default=None)
    p_show.add_argument("schedule_id")
    p_show.add_argument("--json", action="store_true")
    p_show.set_defaults(func=cmd_schedules_show)

    p_pause = wh_sub.add_parser("pause", help="Pause a Todo")
    p_pause.add_argument("--org", default=None)
    p_pause.add_argument("schedule_id")
    p_pause.set_defaults(func=cmd_schedules_pause)

    p_cancel = wh_sub.add_parser("cancel", help="Cancel a Todo")
    p_cancel.add_argument("--org", default=None)
    p_cancel.add_argument("schedule_id")
    p_cancel.set_defaults(func=cmd_schedules_cancel)

    p_edit = wh_sub.add_parser("edit", help="Edit a Todo (fire_at, recurrence, timezone)")
    p_edit.add_argument("--org", default=None)
    p_edit.add_argument("schedule_id")
    p_edit.add_argument("--from-file", required=True)
    p_edit.set_defaults(func=cmd_schedules_edit)
