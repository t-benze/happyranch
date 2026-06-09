"""Dreaming commands."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from cli import _shared
from cli._shared import _fmt_ts, _ok, resolve_org_slug
from cli.client.client import OpcClient


def _complete_payload_from_file(path: str) -> dict:
    try:
        body = json.loads(Path(path).read_text())
    except (OSError, ValueError) as exc:
        print(f"Error reading {path}: {exc}", file=sys.stderr)
        sys.exit(1)
    if not isinstance(body, dict):
        print("error: dream completion payload must be a JSON object", file=sys.stderr)
        sys.exit(1)

    expanded = dict(body)
    candidates = []
    for candidate in expanded.get("kb_candidates", []) or []:
        if not isinstance(candidate, dict):
            print("error: kb_candidates entries must be objects", file=sys.stderr)
            sys.exit(1)
        item = dict(candidate)
        body_path = item.pop("body_path", None)
        if body_path is None:
            print("error: kb_candidates[].body_path is required", file=sys.stderr)
            sys.exit(1)
        try:
            item["body_markdown"] = Path(body_path).read_text()
        except OSError as exc:
            print(f"Error reading {body_path}: {exc}", file=sys.stderr)
            sys.exit(1)
        candidates.append(item)
    expanded["kb_candidates"] = candidates
    return expanded


def _client_and_org(args: argparse.Namespace) -> tuple[OpcClient, str]:
    client = OpcClient.from_env()
    slug = resolve_org_slug(
        args_org=args.org,
        available=_shared._fetch_available_orgs(client),
    )
    return client, slug


def cmd_dreams_complete(args: argparse.Namespace) -> None:
    if not args.org:
        print("error: --org <slug> is required for agent callbacks", file=sys.stderr)
        sys.exit(1)
    client = OpcClient.from_env()
    body = _complete_payload_from_file(args.from_file)
    r = client.post(
        f"/api/v1/orgs/{args.org}/dreams/{args.dream_id}/complete",
        json=body,
    )
    if not _ok(r):
        return
    resp = r.json()
    print(f"ok: completed {resp['dream_id']} status={resp['status']}")


def cmd_dreams_status(args: argparse.Namespace) -> None:
    client, slug = _client_and_org(args)
    params = {}
    if args.agent:
        params["agent"] = args.agent
    r = client.get(f"/api/v1/orgs/{slug}/dreams/status", params=params)
    if not _ok(r):
        return
    print(json.dumps(r.json(), indent=2))


def cmd_dreams_list(args: argparse.Namespace) -> None:
    client, slug = _client_and_org(args)
    params = {"limit": args.limit}
    if args.agent:
        params["agent"] = args.agent
    r = client.get(f"/api/v1/orgs/{slug}/dreams", params=params)
    if not _ok(r):
        return
    dreams = r.json().get("dreams", [])
    if args.json:
        print(json.dumps(dreams, indent=2))
        return
    if not dreams:
        print("(no dreams)")
        return
    for d in dreams:
        print(
            f"{d['dream_id']:10s}  {d['status']:10s}  {d['agent_name']:20s}  "
            f"{_fmt_ts(d.get('ended_at') or d['scheduled_for'])}  "
            f"learnings={d['new_learnings_count']} candidates={d['kb_candidate_count']}"
        )


def cmd_dreams_show(args: argparse.Namespace) -> None:
    client, slug = _client_and_org(args)
    r = client.get(f"/api/v1/orgs/{slug}/dreams/{args.dream_id}")
    if not _ok(r):
        return
    body = r.json()
    if args.json:
        print(json.dumps(body, indent=2))
        return
    print(f"# {body['dream_id']} - {body['agent_name']}")
    print(f"status={body['status']} scheduled={_fmt_ts(body['scheduled_for'])}")
    if body.get("summary"):
        print("\n## Summary\n")
        print(body["summary"])
    if body.get("kb_candidates"):
        print("\n## KB Candidates\n")
        for c in body["kb_candidates"]:
            print(f"- {c['id']} `{c['slug']}` [{c['topic']}] {c['title']}")


def register(sub) -> None:
    p = sub.add_parser("dreams", help="Nightly private agent reflection")
    dream_sub = p.add_subparsers(dest="dream_command", required=True)

    p_complete = dream_sub.add_parser("complete", help="Agent callback: complete a dream")
    p_complete.add_argument("--org", required=True)
    p_complete.add_argument("--dream-id", required=True)
    p_complete.add_argument("--from-file", required=True)
    p_complete.set_defaults(func=cmd_dreams_complete)

    p_status = dream_sub.add_parser("status", help="Show dream scheduler status")
    p_status.add_argument("--org", default=None)
    p_status.add_argument("--agent")
    p_status.set_defaults(func=cmd_dreams_status)

    p_list = dream_sub.add_parser("list", help="List recent dreams")
    p_list.add_argument("--org", default=None)
    p_list.add_argument("--agent")
    p_list.add_argument("--limit", type=int, default=20)
    p_list.add_argument("--json", action="store_true")
    p_list.set_defaults(func=cmd_dreams_list)

    p_show = dream_sub.add_parser("show", help="Show a dream")
    p_show.add_argument("--org", default=None)
    p_show.add_argument("dream_id")
    p_show.add_argument("--json", action="store_true")
    p_show.set_defaults(func=cmd_dreams_show)
