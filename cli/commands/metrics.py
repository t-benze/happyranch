"""Metrics commands (daemon-global operational metrics; no --org)."""
from __future__ import annotations

import argparse
import json
import sys

from cli._shared import _ok
from cli.client.client import DaemonNotRunning, DaemonStateInconsistent, OpcClient


def _client() -> OpcClient:
    try:
        return OpcClient.from_env()
    except (DaemonNotRunning, DaemonStateInconsistent) as exc:
        print(f"Error: {exc}")
        sys.exit(1)


def cmd_metrics_maintenance(args: argparse.Namespace) -> None:
    """Run the explicit, quiescent metrics maintenance operation."""
    client = _client()
    r = client.post(
        "/api/v1/metrics/maintenance",
        json={"confirm_quiescent": args.confirm_quiescent},
    )
    if not _ok(r):
        return
    body = r.json()
    before = body["before"]
    after = body["after"]
    print("metrics maintenance complete:")
    print(f"  cutoff:            {body['cutoff']}")
    print(f"  duration:          {body['duration_seconds']}s")
    print(f"  pruned rows:       {body['pruned_rows']}")
    print(f"  integrity (pre):   {body['integrity_check_before_vacuum']}")
    print(f"  integrity (post):  {body['integrity_check_after_vacuum']}")
    print(f"  checkpoint:        {json.dumps(body['checkpoint'])}")
    print(f"  rows:              {before['row_count']} -> {after['row_count']}")
    print(f"  db bytes:          {before['db_bytes']} -> {after['db_bytes']}")
    print(f"  wal bytes:         {before['wal_bytes']} -> {after['wal_bytes']}")
    print(f"  pages:             {before['page_count']} -> {after['page_count']}")
    print(f"  free-list pages:   {before['freelist_count']} -> {after['freelist_count']}")


def register(sub) -> None:
    p_metrics = sub.add_parser("metrics", help="daemon-global operational metrics")
    metrics_sub = p_metrics.add_subparsers(dest="metrics_command", required=True)

    p_maintenance = metrics_sub.add_parser(
        "maintenance",
        help="run quiescent metrics maintenance (prune + checkpoint + integrity + VACUUM)",
    )
    p_maintenance.add_argument(
        "--confirm-quiescent",
        action="store_true",
        help="explicitly confirm the daemon is quiescent (required by the daemon)",
    )
    p_maintenance.set_defaults(func=cmd_metrics_maintenance)
