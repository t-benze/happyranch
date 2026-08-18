"""Runtime-container and org-registry commands (container-level; no --org)."""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from cli._shared import _ok
from cli.client.client import DaemonNotRunning, DaemonStateInconsistent, OpcClient


_WAIT_SECONDS = 35
_POLL_INTERVAL_SECONDS = 2
_TERMINAL_PROFILE_STATES = {"committed", "failed"}


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


def _get_custom_cli_status(
    client: OpcClient, intended_profile_name: str, *, timeout: float | None = None,
) -> dict[str, object]:
    kwargs: dict[str, object] = {
        "params": {"intended_profile_name": intended_profile_name},
    }
    if timeout is not None:
        kwargs["timeout"] = timeout
    response = client.get(
        "/api/v1/runtime/custom-cli/status",
        **kwargs,
    )
    if not _ok(response):
        raise AssertionError("_ok exits for non-successful responses")
    return response.json()


def _print_custom_cli_status(body: dict[str, object], intended_profile_name: str) -> None:
    profile_state = body.get("profile_state")
    print(f"profile_state: {profile_state or 'none'}")
    if profile_state == "committed":
        print(f"profile_name: {intended_profile_name}")
    if profile_state == "failed" and body.get("reason"):
        print(f"reason: {body['reason']}")
    print(f"wrapper_destination: {body['wrapper_destination']}")


def cmd_custom_cli_status(args: argparse.Namespace) -> None:
    """Show the direct custom-CLI connection outcome for a profile name."""
    try:
        client = OpcClient.from_env()
    except (DaemonNotRunning, DaemonStateInconsistent) as exc:
        print(f"Error: {exc}")
        sys.exit(1)

    if not args.wait:
        body = _get_custom_cli_status(client, args.intended_profile_name)
        _print_custom_cli_status(body, args.intended_profile_name)
        return

    deadline = time.monotonic() + _WAIT_SECONDS
    body = _get_custom_cli_status(
        client,
        args.intended_profile_name,
        timeout=max(0.0, deadline - time.monotonic()),
    )
    while body.get("profile_state") not in _TERMINAL_PROFILE_STATES:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        delay = min(_POLL_INTERVAL_SECONDS, remaining)
        time.sleep(delay)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        body = _get_custom_cli_status(
            client,
            args.intended_profile_name,
            timeout=remaining,
        )

    _print_custom_cli_status(body, args.intended_profile_name)
    if body.get("profile_state") not in _TERMINAL_PROFILE_STATES:
        print(
            f"still pending: no terminal outcome after {_WAIT_SECONDS} seconds",
            file=sys.stderr,
        )
        sys.exit(1)


def cmd_custom_cli_forget(args: argparse.Namespace) -> None:
    """Forget a failed direct custom-CLI connection for a profile name."""
    try:
        client = OpcClient.from_env()
    except (DaemonNotRunning, DaemonStateInconsistent) as exc:
        print(f"Error: {exc}")
        sys.exit(1)

    body = _get_custom_cli_status(client, args.profile_name)
    profile_state = body.get("profile_state")
    if profile_state != "failed":
        print(
            f"refused: profile_state is '{profile_state or 'none'}', not 'failed' — nothing to forget",
            file=sys.stderr,
        )
        sys.exit(1)
    operation_id = body.get("operation_id")
    if not isinstance(operation_id, str) or not operation_id:
        print("refused: failed profile has no operation id — nothing to forget", file=sys.stderr)
        sys.exit(1)
    response = client.post(f"/api/v1/runtime/custom-cli/{operation_id}/forget")
    if not _ok(response):
        return
    print(f"cleared failed custom-CLI connection record for {args.profile_name}")
    wrapper_messages = {
        "already_absent": "wrapper file was already absent",
        "preserved_changed": "wrapper file was preserved because it changed",
        "preserved_unsafe": "wrapper file was preserved because it could not be safely verified",
    }
    wrapper_status = response.json().get("wrapper_status")
    print(wrapper_messages.get(
        wrapper_status, "wrapper disposition was not confirmed because the server returned an unknown cleanup status",
    ))


def cmd_custom_cli_retry(args: argparse.Namespace) -> None:
    """Revalidate the immutable snapshot of one failed custom-CLI operation."""
    try:
        client = OpcClient.from_env()
    except (DaemonNotRunning, DaemonStateInconsistent) as exc:
        print(f"Error: {exc}")
        sys.exit(1)

    body = _get_custom_cli_status(client, args.profile_name)
    profile_state = body.get("profile_state")
    if profile_state != "failed":
        print(
            f"refused: profile_state is '{profile_state or 'none'}', not 'failed' — nothing to retry",
            file=sys.stderr,
        )
        sys.exit(1)
    operation_id = body.get("operation_id")
    if not isinstance(operation_id, str) or not operation_id:
        print("refused: failed profile has no operation id — nothing to retry", file=sys.stderr)
        sys.exit(1)
    response = client.post(f"/api/v1/runtime/custom-cli/{operation_id}/retry")
    if not _ok(response):
        return
    result = response.json()
    if result.get("profile_state") == "committed":
        print(f"retry validated and connected custom-CLI profile {args.profile_name}")
    else:
        print(f"retry validation failed for custom-CLI profile {args.profile_name}", file=sys.stderr)


def cmd_adapters_remove(args: argparse.Namespace) -> None:
    """Remove an adapter using a freshly fetched exact snapshot."""
    try:
        client = OpcClient.from_env()
    except (DaemonNotRunning, DaemonStateInconsistent) as exc:
        print(f"Error: {exc}")
        sys.exit(1)

    response = client.get(f"/api/v1/runtime/adapters/{args.adapter_id}")
    if not _ok(response):
        return
    entry = response.json()
    snapshot = {
        field: entry.get(field)
        for field in (
            "name",
            "executable",
            "executable_hash",
            "version",
            "capabilities",
            "contract_version",
            "workspace_adapter",
            "intended_profile_name",
            "dependency_manifest_version",
            "dependencies",
        )
    }
    print(f"removing adapter {args.adapter_id} ({entry.get('name', '')})")
    response = client.request(
        "DELETE", f"/api/v1/runtime/adapters/{args.adapter_id}", json=snapshot,
    )
    if not _ok(response):
        return
    result = response.json()
    print(f"removed adapter {result['id']} ({result['name']})")



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

    p_custom_cli = sub.add_parser("custom-cli", help="Direct custom-CLI connection commands")
    custom_cli_sub = p_custom_cli.add_subparsers(dest="custom_cli_command", required=True)
    p_custom_cli_status = custom_cli_sub.add_parser(
        "status", help="Show a direct custom-CLI connection outcome",
    )
    p_custom_cli_status.add_argument("intended_profile_name", help="Profile name used to start the connection")
    p_custom_cli_status.add_argument(
        "--wait",
        action="store_true",
        help="Poll for up to 35 seconds for a committed or failed outcome",
    )
    p_custom_cli_status.set_defaults(func=cmd_custom_cli_status)

    p_custom_cli_forget = custom_cli_sub.add_parser(
        "forget", help="Clear a failed direct custom-CLI connection record",
    )
    p_custom_cli_forget.add_argument("profile_name", help="Profile name used to start the connection")
    p_custom_cli_forget.set_defaults(func=cmd_custom_cli_forget)

    p_custom_cli_retry = custom_cli_sub.add_parser(
        "retry", help="Revalidate a failed direct custom-CLI connection",
    )
    p_custom_cli_retry.add_argument("profile_name", help="Profile name used to start the connection")
    p_custom_cli_retry.set_defaults(func=cmd_custom_cli_retry)

    p_adapters = sub.add_parser("adapters", help="Manage custom adapter entries")
    adapters_sub = p_adapters.add_subparsers(dest="adapters_command", required=True)
    p_adapters_remove = adapters_sub.add_parser(
        "remove", help="Remove a custom adapter using its current server snapshot",
    )
    p_adapters_remove.add_argument("adapter_id", help="Custom adapter identifier to remove")
    p_adapters_remove.set_defaults(func=cmd_adapters_remove)
