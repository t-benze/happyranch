"""Shared knowledge-base commands."""
from __future__ import annotations

import argparse
import sys

from cli import _shared
from cli._shared import _fmt_ts, _ok, resolve_org_slug
from cli.client.client import OpcClient


def _read_markdown_payload(path: str) -> dict:
    """Parse `/tmp/kb-<slug>.md` into the daemon's add/update JSON body.

    The file is the full entry (frontmatter + body); the daemon stamps
    `authored_*` / `updated_*` itself.
    """
    import yaml as _yaml
    with open(path) as f:
        text = f.read()
    if not text.startswith("---"):
        raise ValueError("missing frontmatter")
    if text.count("---") < 2:
        raise ValueError("missing closing '---' fence")
    _, fm_text, body = text.split("---", 2)
    fm = _yaml.safe_load(fm_text) or {}
    required = ("slug", "title", "type", "topic")
    missing = [k for k in required if not fm.get(k)]
    if missing:
        raise ValueError(f"frontmatter missing keys: {missing}")
    return {
        "slug": fm["slug"],
        "title": fm["title"],
        "type": fm["type"],
        "topic": fm["topic"],
        "tags": list(fm.get("tags") or []),
        "source_task": fm.get("source_task"),
        "supersedes": fm.get("supersedes"),
        "body": body.lstrip("\n"),
    }



def cmd_kb_list(args: argparse.Namespace) -> None:
    client = OpcClient.from_env()
    org_slug = resolve_org_slug(
        args_org=args.org, available=_shared._fetch_available_orgs(client),
    )
    params = {}
    if args.topic:
        params["topic"] = args.topic
    if args.type:
        params["type"] = args.type
    r = client.get(f"/api/v1/orgs/{org_slug}/kb", params=params)
    if not _ok(r):
        return
    for e in r.json()["entries"]:
        print(f"{e['slug']:40s}  [{e['type']}/{e['topic']}]  {e['title']}")



def cmd_kb_get(args: argparse.Namespace) -> None:
    client = OpcClient.from_env()
    org_slug = resolve_org_slug(
        args_org=args.org, available=_shared._fetch_available_orgs(client),
    )
    r = client.get(f"/api/v1/orgs/{org_slug}/kb/{args.slug}")
    if not _ok(r):
        return
    e = r.json()
    print(f"# {e['title']}")
    print(f"(slug={e['slug']}, type={e['type']}, topic={e['topic']}, "
          f"authored_by={e['authored_by']}, updated_at={_fmt_ts(e['updated_at'])})")
    print()
    print(e["body"])



def cmd_kb_search(args: argparse.Namespace) -> None:
    client = OpcClient.from_env()
    org_slug = resolve_org_slug(
        args_org=args.org, available=_shared._fetch_available_orgs(client),
    )
    r = client.get(
        f"/api/v1/orgs/{org_slug}/kb/search",
        params={"q": args.query, "limit": args.limit},
    )
    if not _ok(r):
        return
    hits = r.json()["hits"]
    if args.json:
        import json as _json
        print(_json.dumps(hits, indent=2))
        return
    if not hits:
        print("no matches")
        return
    for h in hits:
        print(f"{h['slug']:40s}  {h['title']}")
        print(f"    {h['snippet']}")



def cmd_kb_add(args: argparse.Namespace) -> None:
    if not args.org:
        print("error: --org <slug> is required for agent callbacks", file=sys.stderr)
        sys.exit(1)
    client = OpcClient.from_env()
    org_slug = args.org
    try:
        body = _read_markdown_payload(args.from_file)
    except (OSError, ValueError) as exc:
        print(f"Error reading {args.from_file}: {exc}")
        sys.exit(1)
    body["agent"] = args.agent
    body["force_new_sibling"] = args.force_new_sibling
    r = client.post(f"/api/v1/orgs/{org_slug}/kb", json=body)
    if not _ok(r):
        return
    print(f"ok: added {r.json()['slug']}")



def cmd_kb_update(args: argparse.Namespace) -> None:
    if not args.org:
        print("error: --org <slug> is required for agent callbacks", file=sys.stderr)
        sys.exit(1)
    client = OpcClient.from_env()
    org_slug = args.org
    try:
        body = _read_markdown_payload(args.from_file)
    except (OSError, ValueError) as exc:
        print(f"Error reading {args.from_file}: {exc}")
        sys.exit(1)
    if body["slug"] != args.slug:
        print(f"Error: slug in file ({body['slug']!r}) does not match CLI arg ({args.slug!r})")
        sys.exit(1)
    body["agent"] = args.agent
    r = client.post(f"/api/v1/orgs/{org_slug}/kb/{args.slug}", json=body)
    if not _ok(r):
        return
    print(f"ok: updated {r.json()['slug']}")



def cmd_kb_delete(args: argparse.Namespace) -> None:
    if not args.org:
        print("error: --org <slug> is required for agent callbacks", file=sys.stderr)
        sys.exit(1)
    client = OpcClient.from_env()
    org_slug = args.org
    # OpcClient only wraps `get`/`post`; hit `._client` directly for DELETE
    # rather than expanding the client API for a single caller. If a second
    # DELETE ships later, promote this into a proper `client.delete(...)`.
    r = client._client.delete(
        f"/api/v1/orgs/{org_slug}/kb/{args.slug}",
        params={"agent": args.agent, "confirm": args.confirm, "as_founder": args.as_founder},
    )
    if not _ok(r):
        return
    print(f"ok: deleted {args.slug}")



def cmd_kb_reindex(args: argparse.Namespace) -> None:
    client = OpcClient.from_env()
    org_slug = resolve_org_slug(
        args_org=args.org, available=_shared._fetch_available_orgs(client),
    )
    r = client.post(f"/api/v1/orgs/{org_slug}/kb/reindex")
    if not _ok(r):
        return
    print("ok: reindexed")



def register(sub) -> None:
    p_kb = sub.add_parser("kb", help="Shared knowledge base")
    kb_sub = p_kb.add_subparsers(dest="kb_command", required=True)

    p_kb_list = kb_sub.add_parser("list", help="List KB entries")
    p_kb_list.add_argument("--org", default=None, help="Org slug (or set HAPPYRANCH_ORG_SLUG; auto-inferred when only one org)")
    p_kb_list.add_argument("--topic")
    p_kb_list.add_argument("--type", help="Filter by type label (freeform)")
    p_kb_list.set_defaults(func=cmd_kb_list)

    p_kb_get = kb_sub.add_parser("get", help="Read a KB entry")
    p_kb_get.add_argument("--org", default=None, help="Org slug (or set HAPPYRANCH_ORG_SLUG; auto-inferred when only one org)")
    p_kb_get.add_argument("slug")
    p_kb_get.set_defaults(func=cmd_kb_get)

    p_kb_search = kb_sub.add_parser("search", help="Search KB entries")
    p_kb_search.add_argument("--org", default=None, help="Org slug (or set HAPPYRANCH_ORG_SLUG; auto-inferred when only one org)")
    p_kb_search.add_argument("query")
    p_kb_search.add_argument("--limit", type=int, default=20)
    p_kb_search.add_argument("--json", action="store_true")
    p_kb_search.set_defaults(func=cmd_kb_search)

    p_kb_add = kb_sub.add_parser("add", help="Add a KB entry from a markdown file")
    p_kb_add.add_argument("--org", required=True, help="Org slug (required for agent callbacks)")
    p_kb_add.add_argument("--agent", required=True)
    p_kb_add.add_argument("--from-file", required=True)
    p_kb_add.add_argument("--force-new-sibling", action="store_true")
    p_kb_add.set_defaults(func=cmd_kb_add)

    p_kb_update = kb_sub.add_parser("update", help="Update an existing KB entry")
    p_kb_update.add_argument("--org", required=True, help="Org slug (required for agent callbacks)")
    p_kb_update.add_argument("slug")
    p_kb_update.add_argument("--agent", required=True)
    p_kb_update.add_argument("--from-file", required=True)
    p_kb_update.set_defaults(func=cmd_kb_update)

    p_kb_delete = kb_sub.add_parser("delete", help="Delete a KB entry (team manager; founder may override with --as-founder)")
    p_kb_delete.add_argument("--org", required=True, help="Org slug (required for agent callbacks)")
    p_kb_delete.add_argument("slug")
    p_kb_delete.add_argument("--agent", required=True)
    p_kb_delete.add_argument("--confirm", action="store_true")
    p_kb_delete.add_argument("--as-founder", action="store_true")
    p_kb_delete.set_defaults(func=cmd_kb_delete)

    p_kb_reindex = kb_sub.add_parser("reindex", help="Regenerate _index.md")
    p_kb_reindex.add_argument("--org", default=None, help="Org slug (or set HAPPYRANCH_ORG_SLUG; auto-inferred when only one org)")
    p_kb_reindex.set_defaults(func=cmd_kb_reindex)

