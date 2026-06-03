"""Runtime-container and org-registry commands (container-level; no --org)."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from cli._shared import _ok
from cli.client.client import DaemonNotRunning, DaemonStateInconsistent, OpcClient


def cmd_init(args: argparse.Namespace) -> None:
    """Create + register a multi-org runtime container with the daemon."""
    try:
        client = OpcClient.from_env()
    except (DaemonNotRunning, DaemonStateInconsistent) as exc:
        print(f"Error: {exc}")
        sys.exit(1)
    r = client.post(
        "/api/v1/runtime",
        json={"path": str(Path(args.path).expanduser())},
    )
    if r.status_code != 200:
        print(f"Error ({r.status_code}): {r.text}")
        sys.exit(1)
    print(f"runtime: {r.json()['runtime']}")



def cmd_runtime(args: argparse.Namespace) -> None:
    """Show the active runtime container."""
    try:
        client = OpcClient.from_env()
    except (DaemonNotRunning, DaemonStateInconsistent) as exc:
        print(f"Error: {exc}")
        sys.exit(1)
    r = client.get("/api/v1/runtime")
    if r.status_code != 200:
        print(f"Error ({r.status_code}): {r.text}")
        sys.exit(1)
    body = r.json()
    if body["runtime"] is None:
        print("(no active runtime)")
    else:
        print(f"runtime: {body['runtime']}")



def cmd_use(args: argparse.Namespace) -> None:
    """Switch the daemon's active runtime container."""
    try:
        client = OpcClient.from_env()
    except (DaemonNotRunning, DaemonStateInconsistent) as exc:
        print(f"Error: {exc}")
        sys.exit(1)
    r = client.post(
        "/api/v1/runtime/use",
        json={"path": str(Path(args.path).expanduser())},
    )
    if r.status_code == 409:
        print(f"Cannot switch runtime: {r.json()['detail']}")
        sys.exit(1)
    if r.status_code != 200:
        print(f"Error ({r.status_code}): {r.text}")
        sys.exit(1)
    print(f"runtime: {r.json()['runtime']}")



def cmd_orgs(args: argparse.Namespace) -> None:
    """List orgs registered with the active runtime."""
    try:
        client = OpcClient.from_env()
    except (DaemonNotRunning, DaemonStateInconsistent) as exc:
        print(f"Error: {exc}")
        sys.exit(1)
    r = client.get("/api/v1/orgs")
    if not _ok(r):
        return
    body = r.json()
    for org in body["orgs"]:
        print(f"  {org['slug']:30s}  {org['root']}")
    broken = body.get("broken") or []
    if broken:
        print("\nBroken (folder on disk, failed to attach):")
        for org in broken:
            error = org["error"].splitlines()[0]
            print(f"  {org['slug']:30s}  {error}")
            for line in org["error"].splitlines()[1:]:
                print(f"  {' ' * 30}  {line}")



def cmd_orgs_init(args: argparse.Namespace) -> None:
    """Create a new org subfolder inside the active runtime."""
    try:
        client = OpcClient.from_env()
    except (DaemonNotRunning, DaemonStateInconsistent) as exc:
        print(f"Error: {exc}")
        sys.exit(1)
    payload: dict = {"slug": args.slug}
    if args.from_path:
        payload["from_example"] = args.from_path
    r = client.post("/api/v1/orgs", json=payload)
    if not _ok(r):
        return
    print(f"created: {r.json()['slug']}")



def cmd_orgs_unload(args: argparse.Namespace) -> None:
    """Drop an org's state from the daemon's in-memory registry."""
    try:
        client = OpcClient.from_env()
    except (DaemonNotRunning, DaemonStateInconsistent) as exc:
        print(f"Error: {exc}")
        sys.exit(1)
    r = client.request("DELETE", f"/api/v1/orgs/{args.slug}")
    if not _ok(r):
        return
    print(f"unloaded: {r.json()['slug']}")



def cmd_web(args: argparse.Namespace) -> None:
    """Open the HappyRanch web UI in the default browser."""
    import webbrowser

    client = OpcClient.from_env()
    # Health check — fail loud if the daemon isn't running.
    try:
        r = client.get("/api/v1/health", timeout=5.0)
    except Exception as exc:
        print(f"error: daemon unreachable at {client.base_url} ({exc})", file=sys.stderr)
        print("hint: start the daemon with scripts/daemon.sh start", file=sys.stderr)
        sys.exit(2)
    if not _ok(r):
        print(f"error: daemon /health returned {r.status_code}", file=sys.stderr)
        sys.exit(2)
    url = client.base_url.rstrip("/") + "/"
    print(f"happyranch web → {url}")
    if args.no_open:
        from urllib.parse import urlparse
        import socket
        port = urlparse(client.base_url).port
        if port is not None:
            host = socket.gethostname()
            print(f"remote access: ssh -L {port}:127.0.0.1:{port} {host}")
            print(f"               then open http://127.0.0.1:{port}/ locally")
    else:
        webbrowser.open(url)



def register(sub) -> None:
    p_init_runtime = sub.add_parser(
        "init", help="create + register a multi-org runtime container",
    )
    p_init_runtime.add_argument("path", help="Path for the new runtime container")
    p_init_runtime.set_defaults(func=cmd_init)

    p_runtime = sub.add_parser("runtime", help="show the active runtime")
    p_runtime.set_defaults(func=cmd_runtime)

    p_use = sub.add_parser("use", help="switch the active runtime container")
    p_use.add_argument("path", help="Path of an already-registered runtime")
    p_use.set_defaults(func=cmd_use)

    p_orgs = sub.add_parser("orgs", help="manage orgs in the active runtime")
    p_orgs.set_defaults(orgs_cmd="list", func=cmd_orgs)
    orgs_sub = p_orgs.add_subparsers(dest="orgs_cmd")
    orgs_sub.required = False

    p_orgs_list = orgs_sub.add_parser("list", help="list orgs")
    p_orgs_list.set_defaults(func=cmd_orgs)

    p_orgs_init = orgs_sub.add_parser("init", help="create a new org")
    p_orgs_init.add_argument("slug")
    p_orgs_init.add_argument(
        "--from", dest="from_path", default=None,
        help="path to an examples/orgs/<name> tree to seed from",
    )
    p_orgs_init.set_defaults(func=cmd_orgs_init)

    p_orgs_unload = orgs_sub.add_parser(
        "unload", help="drop an org's state from the daemon",
    )
    p_orgs_unload.add_argument("slug")
    p_orgs_unload.set_defaults(func=cmd_orgs_unload)

    p_web = sub.add_parser("web", help="Open the HappyRanch web UI in the default browser")
    p_web.add_argument(
        "--no-open",
        action="store_true",
        help="Print the URL but don't open the browser",
    )
    p_web.set_defaults(func=cmd_web)

