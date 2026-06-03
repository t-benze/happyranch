"""Per-agent learnings commands."""
from __future__ import annotations

import argparse
import sys

from cli import _shared
from cli._shared import _ok, resolve_org_slug
from cli.client.client import DaemonNotRunning, DaemonStateInconsistent, OpcClient


def cmd_learning(args: argparse.Namespace) -> None:
    """Agent callback: append a learning to the agent's learnings.md."""
    if not args.org:
        print("error: --org <slug> is required for agent callbacks", file=sys.stderr)
        sys.exit(1)
    try:
        client = OpcClient.from_env()
    except (DaemonNotRunning, DaemonStateInconsistent) as exc:
        print(f"Error: {exc}")
        sys.exit(1)
    r = client.post(
        f"/api/v1/orgs/{args.org}/agents/{args.agent}/learnings",
        json={"session_id": args.session_id, "task_id": args.task_id, "text": args.text},
    )
    if not _ok(r):
        return



def _read_yaml_payload(path: str) -> dict:
    import yaml
    with open(path) as f:
        data = yaml.safe_load(f)
    if data is None:
        return {}
    if not isinstance(data, dict):
        print(
            f"error: payload file must be a YAML mapping, got {type(data).__name__}",
            file=sys.stderr,
        )
        sys.exit(1)
    return data



def _learning_client() -> OpcClient:
    """Return an OpcClient, exiting with a friendly message if the daemon is down."""
    try:
        return OpcClient.from_env()
    except (DaemonNotRunning, DaemonStateInconsistent) as exc:
        print(f"Error: {exc}")
        sys.exit(1)



def cmd_learning_list(args: argparse.Namespace) -> None:
    client = _learning_client()
    org = resolve_org_slug(args_org=args.org, available=_shared._fetch_available_orgs(client))
    params: dict = {}
    if args.topic:
        params["topic"] = args.topic
    if args.tag:
        params["tag"] = args.tag
    if args.promoted:
        params["promoted"] = True
    elif args.not_promoted:
        params["promoted"] = False
    r = client.get(f"/api/v1/orgs/{org}/agents/{args.agent}/learnings/entries/", params=params)
    if not _ok(r):
        return
    entries = r.json().get("entries", [])
    if args.json:
        import json
        print(json.dumps(entries, indent=2))
        return
    if not entries:
        print("(no learnings)")
        return
    for e in entries:
        tags = ", ".join(e.get("tags", []))
        promo = f" ↗ {e['promoted_to']}" if e.get("promoted_to") else ""
        print(f"  {e['id']}  [{e['topic']}] {e['title']}  ({tags}){promo}")



def cmd_learning_get(args: argparse.Namespace) -> None:
    client = _learning_client()
    org = resolve_org_slug(args_org=args.org, available=_shared._fetch_available_orgs(client))
    r = client.get(f"/api/v1/orgs/{org}/agents/{args.agent}/learnings/entries/{args.id_or_slug}")
    if not _ok(r):
        return
    entry = r.json()
    if args.json:
        import json
        print(json.dumps(entry, indent=2))
        return
    print(f"# {entry['title']}\n")
    print(f"id: {entry['id']}  slug: {entry['slug']}  topic: {entry['topic']}")
    if entry.get("tags"):
        print(f"tags: {', '.join(entry['tags'])}")
    if entry.get("promoted_to"):
        print(f"promoted_to: {entry['promoted_to']}")
    print()
    print(entry["body"])



def cmd_learning_search(args: argparse.Namespace) -> None:
    client = _learning_client()
    org = resolve_org_slug(args_org=args.org, available=_shared._fetch_available_orgs(client))
    r = client.post(
        f"/api/v1/orgs/{org}/agents/{args.agent}/learnings/entries/search",
        json={"query": args.query, "limit": args.limit, "include_promoted": args.include_promoted},
    )
    if not _ok(r):
        return
    hits = r.json().get("hits", [])
    if args.json:
        import json
        print(json.dumps(hits, indent=2))
        return
    if not hits:
        print("(no matches)")
        return
    for h in hits:
        print(f"  {h['id']}  score={h['score']}  {h['title']}")
        print(f"      {h['snippet']}")



def cmd_learning_reindex(args: argparse.Namespace) -> None:
    client = _learning_client()
    org = resolve_org_slug(args_org=args.org, available=_shared._fetch_available_orgs(client))
    r = client.post(f"/api/v1/orgs/{org}/agents/{args.agent}/learnings/entries/reindex", json={})
    if not _ok(r):
        return
    print("ok: reindexed")



def cmd_learning_add(args: argparse.Namespace) -> None:
    client = _learning_client()
    org = resolve_org_slug(args_org=args.org, available=_shared._fetch_available_orgs(client))
    payload = _read_yaml_payload(args.from_file)
    r = client.post(
        f"/api/v1/orgs/{org}/agents/{args.agent}/learnings/entries/",
        json=payload,
    )
    if not _ok(r):
        return
    resp = r.json()
    print(f"ok: {resp['id']} -> {resp['path']}")



def cmd_learning_update(args: argparse.Namespace) -> None:
    client = _learning_client()
    org = resolve_org_slug(args_org=args.org, available=_shared._fetch_available_orgs(client))
    payload = _read_yaml_payload(args.from_file)
    r = client.request("PUT", f"/api/v1/orgs/{org}/agents/{args.agent}/learnings/entries/{args.id}", json=payload)
    if not _ok(r):
        return
    resp = r.json()
    print(f"ok: updated {resp['id']}")



def cmd_learning_promote(args: argparse.Namespace) -> None:
    client = _learning_client()
    org = resolve_org_slug(args_org=args.org, available=_shared._fetch_available_orgs(client))
    r = client.post(
        f"/api/v1/orgs/{org}/agents/{args.agent}/learnings/entries/{args.id}/promote",
        json={"kb_slug": args.kb_slug},
    )
    if not _ok(r):
        return
    resp = r.json()
    print(f"ok: {resp['id']} promoted to KB precedent `{resp['promoted_to']}`")



def register(sub) -> None:
    p_learn = sub.add_parser("learning", help="Per-agent learnings (verb-dispatched)")
    learn_sub = p_learn.add_subparsers(dest="learn_verb")

    # Agent callback: `happyranch learning --org <slug> --agent X --text "..."`
    p_learn.add_argument("--org", required=True)
    p_learn.add_argument("--agent", required=False)
    p_learn.add_argument("--text", required=False)
    p_learn.add_argument("--task-id", required=False)
    p_learn.add_argument("--session-id", required=False)
    p_learn.set_defaults(func=cmd_learning)

    # list
    pl = learn_sub.add_parser("list", help="List learnings")
    pl.add_argument("--org", required=False)
    pl.add_argument("--agent", required=True)
    pl.add_argument("--topic")
    pl.add_argument("--tag")
    pl.add_argument("--promoted", action="store_true")
    pl.add_argument("--not-promoted", action="store_true")
    pl.add_argument("--json", action="store_true")
    pl.set_defaults(func=cmd_learning_list)

    # get
    pg = learn_sub.add_parser("get", help="Get a learning by ID or slug")
    pg.add_argument("--org", required=False)
    pg.add_argument("--agent", required=True)
    pg.add_argument("id_or_slug")
    pg.add_argument("--json", action="store_true")
    pg.set_defaults(func=cmd_learning_get)

    # search
    ps = learn_sub.add_parser("search", help="Substring search over learnings")
    ps.add_argument("--org", required=False)
    ps.add_argument("--agent", required=True)
    ps.add_argument("query")
    ps.add_argument("--limit", type=int, default=20)
    ps.add_argument("--include-promoted", action="store_true")
    ps.add_argument("--json", action="store_true")
    ps.set_defaults(func=cmd_learning_search)

    # add
    pa = learn_sub.add_parser("add", help="Add a new learning (file payload)")
    pa.add_argument("--org", required=False)
    pa.add_argument("--agent", required=True)
    pa.add_argument("--from-file", required=True)
    pa.set_defaults(func=cmd_learning_add)

    # update
    pu = learn_sub.add_parser("update", help="Update an existing learning by ID")
    pu.add_argument("--org", required=False)
    pu.add_argument("--agent", required=True)
    pu.add_argument("id")
    pu.add_argument("--from-file", required=True)
    pu.set_defaults(func=cmd_learning_update)

    # promote
    pp = learn_sub.add_parser("promote", help="Promote a learning to a KB precedent")
    pp.add_argument("--org", required=False)
    pp.add_argument("--agent", required=True)
    pp.add_argument("id")
    pp.add_argument("--kb-slug", required=True)
    pp.set_defaults(func=cmd_learning_promote)

    # reindex
    pr = learn_sub.add_parser("reindex", help="Regenerate _index.md")
    pr.add_argument("--org", required=False)
    pr.add_argument("--agent", required=True)
    pr.set_defaults(func=cmd_learning_reindex)

