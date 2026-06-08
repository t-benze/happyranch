"""System assistant setup and status commands."""
from __future__ import annotations

import argparse
import sys
from typing import Any

from cli.client.client import DaemonNotRunning, DaemonStateInconsistent, OpcClient


def _client() -> OpcClient:
    try:
        return OpcClient.from_env()
    except (DaemonNotRunning, DaemonStateInconsistent) as exc:
        print(f"Error: {exc}")
        sys.exit(1)


def _print_status(body: dict[str, Any]) -> None:
    print(f"state: {body['state']}")
    if body.get("selected_executor"):
        print(f"executor: {body['selected_executor']}")
    if body.get("workspace_path"):
        print(f"workspace: {body['workspace_path']}")
    if body.get("detail"):
        print(f"detail: {body['detail']}")


def cmd_assistant_status(args: argparse.Namespace) -> None:
    client = _client()
    r = client.get("/api/v1/assistant/status")
    if r.status_code != 200:
        print(f"Error ({r.status_code}): {r.text}")
        sys.exit(1)
    _print_status(r.json())


def _probe_passed(result: dict[str, Any]) -> bool:
    return result.get("passed") is True


def _probe_failure_reason(result: dict[str, Any]) -> str | None:
    reason = result.get("detail") or result.get("reason") or result.get("error")
    if reason:
        return str(reason)
    status = result.get("status")
    return str(status) if status else None


def _choose_executor(results: list[dict[str, Any]]) -> str:
    passing = [r for r in results if _probe_passed(r)]
    if not passing:
        print("No PTY-capable executor passed the HappyRanch probe.")
        for result in results:
            print(f"- {result.get('executor')}: {_probe_failure_reason(result) or 'failed'}")
            if result.get("hint"):
                print(f"  hint: {result['hint']}")
        sys.exit(2)
    print("PTY-capable executors:")
    for idx, result in enumerate(passing, start=1):
        executor = str(result["executor"])
        print(f"{idx}. {executor} ({result.get('command', executor)})")
    while True:
        raw = input("Select executor: ").strip()
        try:
            selected = passing[int(raw) - 1]
        except (ValueError, IndexError):
            print(f"Enter a number from 1 to {len(passing)}.")
            continue
        return str(selected["executor"])


def cmd_assistant_init(args: argparse.Namespace) -> None:
    client = _client()
    status = client.get("/api/v1/assistant/status")
    if status.status_code != 200:
        print(f"Error ({status.status_code}): {status.text}")
        sys.exit(1)
    body = status.json()
    if body["state"] == "configured" and not args.reconfigure and not args.repair:
        _print_status(body)
        return
    if args.repair and not args.reconfigure:
        r = client.post("/api/v1/assistant/repair")
        if r.status_code != 200:
            print(f"Error ({r.status_code}): {r.text}")
            sys.exit(1)
        _print_status(r.json())
        return
    probes = client.post("/api/v1/assistant/probes")
    if probes.status_code != 200:
        print(f"Error ({probes.status_code}): {probes.text}")
        sys.exit(1)
    results = probes.json()["probe_results"]
    selected = _choose_executor(results)
    configured = client.post(
        "/api/v1/assistant/configure",
        json={"selected_executor": selected, "probe_results": results},
    )
    if configured.status_code != 200:
        print(f"Error ({configured.status_code}): {configured.text}")
        sys.exit(1)
    _print_status(configured.json())


def cmd_assistant_attach(args: argparse.Namespace) -> None:
    print("assistant attach is not implemented yet; run `happyranch assistant status`.")
    sys.exit(2)


def register(
    sub: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    p = sub.add_parser("assistant", help="manage or attach to the system assistant")
    p.set_defaults(assistant_cmd="attach", func=cmd_assistant_attach)
    assistant_sub = p.add_subparsers(dest="assistant_cmd")
    assistant_sub.default = "attach"
    assistant_sub.required = False

    p_init = assistant_sub.add_parser("init", help="initialize the system assistant")
    group = p_init.add_mutually_exclusive_group()
    group.add_argument("--repair", action="store_true")
    group.add_argument("--reconfigure", action="store_true")
    p_init.set_defaults(func=cmd_assistant_init)

    p_status = assistant_sub.add_parser("status", help="show system assistant status")
    p_status.set_defaults(func=cmd_assistant_status)

    p_attach = assistant_sub.add_parser("attach", help="attach to the system assistant")
    p_attach.set_defaults(func=cmd_assistant_attach)
