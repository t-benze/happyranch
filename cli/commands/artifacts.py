"""Org-shared artifact blob commands (put/list/get)."""
from __future__ import annotations

import argparse
from pathlib import Path

from cli import _shared
from cli._shared import resolve_org_slug
from cli.client.client import OpcClient


def cmd_artifacts_put(args: argparse.Namespace) -> None:
    client = OpcClient.from_env()
    org_slug = resolve_org_slug(
        args_org=args.org, available=_shared._fetch_available_orgs(client),
    )
    info = client.put_artifact(
        slug=org_slug,
        local_path=args.local_path,
        name=args.name,
        agent=args.agent,
    )
    print(f"uploaded {info['name']} ({info['size_bytes']}B)")



def cmd_artifacts_list(args: argparse.Namespace) -> None:
    client = OpcClient.from_env()
    org_slug = resolve_org_slug(
        args_org=args.org, available=_shared._fetch_available_orgs(client),
    )
    body = client.list_artifacts(slug=org_slug, prefix=getattr(args, "prefix", None) or "")
    artifacts = body["artifacts"]
    if not artifacts:
        print("no artifacts")
        return
    for a in artifacts:
        print(f"{a['name']}\t{a['size_bytes']}\t{a['modified_at']}")



def cmd_artifacts_get(args: argparse.Namespace) -> None:
    client = OpcClient.from_env()
    org_slug = resolve_org_slug(
        args_org=args.org, available=_shared._fetch_available_orgs(client),
    )
    data = client.get_artifact(slug=org_slug, name=args.name)
    args.output.write_bytes(data)
    print(f"saved {len(data)}B to {args.output}")



def register(sub) -> None:
    p_artifacts = sub.add_parser("artifacts", help="Org-shared artifact blobs (put/list/get)")
    artifacts_sub = p_artifacts.add_subparsers(dest="artifacts_cmd", required=True)

    p_artifacts_put = artifacts_sub.add_parser("put", help="Upload a local file to the org's shared artifacts")
    p_artifacts_put.add_argument("local_path", type=Path, help="Local file to upload")
    p_artifacts_put.add_argument("--name", default=None, help="Override stored filename (default: local basename)")
    p_artifacts_put.add_argument("--agent", required=True, help="Agent name for audit attribution")
    p_artifacts_put.add_argument("--org", default=None, help="Org slug (or set HAPPYRANCH_ORG_SLUG; auto-inferred when only one org)")
    p_artifacts_put.set_defaults(func=cmd_artifacts_put)

    p_artifacts_list = artifacts_sub.add_parser("list", help="List artifact names and sizes")
    p_artifacts_list.add_argument("--prefix", default=None, help="Filter artifacts by key prefix (e.g. 'reports/')")
    p_artifacts_list.add_argument("--org", default=None, help="Org slug (or set HAPPYRANCH_ORG_SLUG; auto-inferred when only one org)")
    p_artifacts_list.set_defaults(func=cmd_artifacts_list)

    p_artifacts_get = artifacts_sub.add_parser("get", help="Download an artifact by name")
    p_artifacts_get.add_argument("name", help="Artifact name to download")
    p_artifacts_get.add_argument("--output", type=Path, required=True, help="Local path to write the artifact bytes to")
    p_artifacts_get.add_argument("--org", default=None, help="Org slug (or set HAPPYRANCH_ORG_SLUG; auto-inferred when only one org)")
    p_artifacts_get.set_defaults(func=cmd_artifacts_get)

