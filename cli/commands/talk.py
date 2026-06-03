"""Founder<->agent talk-flow commands."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from cli import _shared
from cli._shared import _fmt_ts, _ok, resolve_org_slug
from cli.client.client import OpcClient


def cmd_talk_start(args: argparse.Namespace) -> None:
    client = OpcClient.from_env()
    slug = resolve_org_slug(
        args_org=args.org, available=_shared._fetch_available_orgs(client),
    )
    r = client.post(f"/api/v1/orgs/{slug}/talks", json={"agent_name": args.agent})
    if r.status_code == 409:
        detail = r.json().get("detail", {})
        if detail.get("code") == "talk_already_open":
            print(
                f"An open talk with {args.agent} already exists: "
                f"{detail['prior_open_talk_id']} (started {_fmt_ts(detail.get('prior_started_at'))}). "
                f"Use `happyranch talk resume --talk-id {detail['prior_open_talk_id']}` "
                f"or `happyranch talk abandon --talk-id {detail['prior_open_talk_id']} --reason orphan`."
            )
            sys.exit(1)
    if not _ok(r):
        return
    body = r.json()
    print(f"{body['talk_id']} (started {_fmt_ts(body['started_at'])})")



def cmd_talk_resume(args: argparse.Namespace) -> None:
    client = OpcClient.from_env()
    slug = resolve_org_slug(
        args_org=args.org, available=_shared._fetch_available_orgs(client),
    )
    r = client.post(f"/api/v1/orgs/{slug}/talks/{args.talk_id}/resume")
    if not _ok(r):
        return
    print(f"ok: resumed {args.talk_id}")



def cmd_talk_abandon(args: argparse.Namespace) -> None:
    client = OpcClient.from_env()
    slug = resolve_org_slug(
        args_org=args.org, available=_shared._fetch_available_orgs(client),
    )
    r = client.post(
        f"/api/v1/orgs/{slug}/talks/{args.talk_id}/abandon",
        json={"reason": args.reason or "manual"},
    )
    if not _ok(r):
        return
    print(f"ok: abandoned {args.talk_id}")



def cmd_talk_end(args: argparse.Namespace) -> None:
    import json as _json
    client = OpcClient.from_env()
    slug = resolve_org_slug(
        args_org=args.org, available=_shared._fetch_available_orgs(client),
    )
    try:
        body = _json.loads(Path(args.from_file).read_text())
    except (OSError, ValueError) as exc:
        print(f"Error reading {args.from_file}: {exc}")
        sys.exit(1)
    r = client.post(f"/api/v1/orgs/{slug}/talks/{args.talk_id}/end", json=body)
    if not _ok(r):
        return
    resp = r.json()
    print(
        f"ok: closed {resp['talk_id']} — {resp['new_learnings_count']} learnings, "
        f"transcript at {resp['transcript_path']}"
    )



def cmd_talk_status(args: argparse.Namespace) -> None:
    client = OpcClient.from_env()
    slug = resolve_org_slug(
        args_org=args.org, available=_shared._fetch_available_orgs(client),
    )
    params = {"status": "open"}
    if args.agent:
        params["agent"] = args.agent
    r = client.get(f"/api/v1/orgs/{slug}/talks", params=params)
    if not _ok(r):
        return
    talks = r.json()["talks"]
    if not talks:
        print("no open talks")
        return
    for t in talks:
        print(f"{t['talk_id']}  agent={t['agent_name']}  started={_fmt_ts(t['started_at'])}")



def cmd_talk_list(args: argparse.Namespace) -> None:
    client = OpcClient.from_env()
    slug = resolve_org_slug(
        args_org=args.org, available=_shared._fetch_available_orgs(client),
    )
    params = {"limit": args.limit}
    if args.agent:
        params["agent"] = args.agent
    r = client.get(f"/api/v1/orgs/{slug}/talks", params=params)
    if not _ok(r):
        return
    for t in r.json()["talks"]:
        print(
            f"{t['talk_id']:10s}  {t['status']:10s}  {t['agent_name']:20s}  "
            f"{_fmt_ts(t.get('ended_at') or t['started_at'])}  "
            f"learnings={t['new_learnings_count']}"
        )



def cmd_talk_show(args: argparse.Namespace) -> None:
    import json as _json
    client = OpcClient.from_env()
    slug = resolve_org_slug(
        args_org=args.org, available=_shared._fetch_available_orgs(client),
    )
    r = client.get(f"/api/v1/orgs/{slug}/talks/{args.talk_id}")
    if not _ok(r):
        return
    t = r.json()
    if args.json:
        print(_json.dumps(t, indent=2))
        return
    print(f"# {t['talk_id']} — {t['agent_name']}")
    print(
        f"status={t['status']} started={_fmt_ts(t['started_at'])} ended={_fmt_ts(t.get('ended_at'))}"
    )
    print(f"topics: {t.get('topic_list')}")
    print(f"learnings: {t['new_learnings_count']}  kb_slugs: {t.get('new_kb_slugs')}")
    if t.get("summary"):
        print("\n## Summary\n")
        print(t["summary"])
    if t.get("transcript"):
        print("\n## Transcript\n")
        print(t["transcript"])



def register(sub) -> None:
    p_talk = sub.add_parser("talk", help="Founder↔agent conversation flow")
    talk_sub = p_talk.add_subparsers(dest="talk_command", required=True)

    p_talk_start = talk_sub.add_parser("start", help="Start a new talk")
    p_talk_start.add_argument("--org", default=None, help="Org slug (or set HAPPYRANCH_ORG_SLUG; auto-inferred when only one org)")
    p_talk_start.add_argument("--agent", required=True)
    p_talk_start.set_defaults(func=cmd_talk_start)

    p_talk_resume = talk_sub.add_parser("resume", help="Resume an open talk")
    p_talk_resume.add_argument("--org", default=None, help="Org slug (or set HAPPYRANCH_ORG_SLUG; auto-inferred when only one org)")
    p_talk_resume.add_argument("--talk-id", required=True)
    p_talk_resume.set_defaults(func=cmd_talk_resume)

    p_talk_abandon = talk_sub.add_parser("abandon", help="Abandon an open talk")
    p_talk_abandon.add_argument("--org", default=None, help="Org slug (or set HAPPYRANCH_ORG_SLUG; auto-inferred when only one org)")
    p_talk_abandon.add_argument("--talk-id", required=True)
    p_talk_abandon.add_argument("--reason", default="manual")
    p_talk_abandon.set_defaults(func=cmd_talk_abandon)

    p_talk_end = talk_sub.add_parser("end", help="End a talk (agent callback)")
    p_talk_end.add_argument("--org", default=None, help="Org slug (or set HAPPYRANCH_ORG_SLUG; auto-inferred when only one org)")
    p_talk_end.add_argument("--talk-id", required=True)
    p_talk_end.add_argument("--from-file", required=True)
    p_talk_end.set_defaults(func=cmd_talk_end)

    p_talk_status = talk_sub.add_parser("status", help="List open talks")
    p_talk_status.add_argument("--org", default=None, help="Org slug (or set HAPPYRANCH_ORG_SLUG; auto-inferred when only one org)")
    p_talk_status.add_argument("--agent")
    p_talk_status.set_defaults(func=cmd_talk_status)

    p_talk_list = talk_sub.add_parser("list", help="List recent talks")
    p_talk_list.add_argument("--org", default=None, help="Org slug (or set HAPPYRANCH_ORG_SLUG; auto-inferred when only one org)")
    p_talk_list.add_argument("--agent")
    p_talk_list.add_argument("--limit", type=int, default=20)
    p_talk_list.set_defaults(func=cmd_talk_list)

    p_talk_show = talk_sub.add_parser("show", help="Show a talk's metadata + transcript")
    p_talk_show.add_argument("--org", default=None, help="Org slug (or set HAPPYRANCH_ORG_SLUG; auto-inferred when only one org)")
    p_talk_show.add_argument("talk_id")
    p_talk_show.add_argument("--json", action="store_true", help="Emit raw JSON instead of human output")
    p_talk_show.set_defaults(func=cmd_talk_show)

