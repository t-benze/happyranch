"""Org-shared artifact blob commands (put/list/get)."""
from __future__ import annotations

import argparse
import sys

import httpx
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
        agent=args.agent, task_id=args.task, thread_id=args.thread,
        purpose=args.purpose, retention=args.retention, reason=args.reason,
        expires_at=args.expires_at, operation_id=args.operation_id,
    )
    print(f"uploaded {info['name']} ({info['size_bytes']}B) generation={info['generation']} expires={info['expires_at']}")



def cmd_artifacts_list(args: argparse.Namespace) -> None:
    client = OpcClient.from_env()
    org_slug = resolve_org_slug(
        args_org=args.org, available=_shared._fetch_available_orgs(client),
    )
    body = client.list_artifacts(slug=org_slug, prefix=getattr(args, "prefix", None) or "", available_only=getattr(args,"available_only",False))
    artifacts = body["artifacts"]
    if not artifacts:
        print("no artifacts")
        return
    for a in artifacts:
        print(f"{a['name']}\t{a['size_bytes']}\t{a['modified_at']}\t{a.get('state')}\t{a.get('owner')}\t{a.get('expires_at')}")



def cmd_artifacts_get(args: argparse.Namespace) -> None:
    if args.generation is not None and args.generation < 1:
        raise SystemExit("generation must be positive")
    client = OpcClient.from_env()
    org_slug = resolve_org_slug(
        args_org=args.org, available=_shared._fetch_available_orgs(client),
    )
    try:
        data = client.get_artifact(slug=org_slug, name=args.name, generation=args.generation)
    except httpx.HTTPStatusError as exc:
        try:
            detail = exc.response.json()["detail"]
        except (ValueError,KeyError):
            raise
        if isinstance(detail,dict) and detail.get("code") in ("artifact_unavailable","artifact_operation_pending"):
            print(f"{args.name} unavailable; state: {detail.get('state','publication_pending')}; "
                  f"expired on: {detail.get('expired_on') or detail.get('expires_at')}; "
                  f"owner: {detail.get('owner')}; reason: {detail.get('reason')}; "
                  f"recoverable until: {detail.get('recoverable_until')}. "
                  "Use artifacts restore with continuing use and a bounded expiry.",file=sys.stderr)
            raise SystemExit(1) from exc
        raise
    args.output.write_bytes(data)
    print(f"saved {len(data)}B to {args.output}")



def cmd_artifacts_grant(args: argparse.Namespace) -> None:
    client = OpcClient.from_env()
    slug = resolve_org_slug(args_org=args.org, available=_shared._fetch_available_orgs(client))
    payload = {"agent":args.agent,"continuing_use":args.continuing_use,"expires_at":args.expires_at}
    for key in ("retention","reason"):
        if getattr(args,key,None) is not None:
            payload[key] = getattr(args,key)
    response = client.post(f"/api/v1/orgs/{slug}/artifacts/{args.name}/{args.artifacts_cmd}",json=payload)
    response.raise_for_status()
    body = response.json()
    print(f"{body['name']} state={body['state']} expires={body['expires_at']}")


def register(sub) -> None:
    p_artifacts = sub.add_parser("artifacts", help="Org-shared artifact blobs (put/list/get)")
    artifacts_sub = p_artifacts.add_subparsers(dest="artifacts_cmd", required=True)

    p_artifacts_put = artifacts_sub.add_parser("put", help="Upload a local file to the org's shared artifacts")
    p_artifacts_put.add_argument("local_path", type=Path, help="Local file to upload")
    p_artifacts_put.add_argument("--name", default=None, help="Override stored filename (default: local basename)")
    p_artifacts_put.add_argument("--agent", required=True, help="Agent name for audit attribution")
    p_artifacts_put.add_argument("--org", default=None, help="Org slug (or set HAPPYRANCH_ORG_SLUG; auto-inferred when only one org)")
    origin = p_artifacts_put.add_mutually_exclusive_group(required=True)
    origin.add_argument("--task", default=None)
    origin.add_argument("--thread", default=None)
    p_artifacts_put.add_argument("--purpose", required=True)
    p_artifacts_put.add_argument("--retention", choices=("default","reference"), required=True)
    p_artifacts_put.add_argument("--reason", default=None)
    p_artifacts_put.add_argument("--expires-at", default=None)
    p_artifacts_put.add_argument("--operation-id", default=None, help="Stable UUID for an exact upload retry")
    p_artifacts_put.set_defaults(func=cmd_artifacts_put)

    p_artifacts_list = artifacts_sub.add_parser("list", help="List artifact names and sizes")
    p_artifacts_list.add_argument("--prefix", default=None, help="Filter artifacts by key prefix (e.g. 'reports/')")
    p_artifacts_list.add_argument("--org", default=None, help="Org slug (or set HAPPYRANCH_ORG_SLUG; auto-inferred when only one org)")
    p_artifacts_list.add_argument("--available-only",action="store_true")
    p_artifacts_list.set_defaults(func=cmd_artifacts_list)

    p_artifacts_get = artifacts_sub.add_parser("get", help="Download an artifact by name")
    p_artifacts_get.add_argument("name", help="Artifact name to download")
    p_artifacts_get.add_argument("--output", type=Path, required=True, help="Local path to write the artifact bytes to")
    p_artifacts_get.add_argument("--org", default=None, help="Org slug (or set HAPPYRANCH_ORG_SLUG; auto-inferred when only one org)")
    p_artifacts_get.add_argument("--generation",type=int,default=None)
    p_artifacts_get.set_defaults(func=cmd_artifacts_get)


    for verb in ("renew","restore"):
        grant = artifacts_sub.add_parser(verb, help=f"{verb.title()} with a bounded retention grant")
        grant.add_argument("name")
        grant.add_argument("--agent",required=True)
        grant.add_argument("--continuing-use",required=True)
        grant.add_argument("--expires-at",required=True)
        grant.add_argument("--retention",choices=("default","reference"),default=None)
        grant.add_argument("--reason",default=None)
        grant.add_argument("--org",default=None)
        grant.set_defaults(func=cmd_artifacts_grant)
