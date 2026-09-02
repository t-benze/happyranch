"""Retired legacy executor-profile registration commands."""

from __future__ import annotations

import argparse
import sys


_GUIDANCE = (
    "legacy executor registration is retired; register and approve an adapter "
    "executable, then bind command_adapter_id='custom-adapter:<id>'"
)


def _retired(_: argparse.Namespace) -> None:
    """Reject before any network request or durable side effect."""
    print(f"error: {_GUIDANCE}", file=sys.stderr)
    raise SystemExit(1)


def cmd_executors_register(args: argparse.Namespace) -> None:
    _retired(args)


def cmd_executors_runtime_register(args: argparse.Namespace) -> None:
    _retired(args)


def register(sub) -> None:
    parser = sub.add_parser("executors", help="Executor profile management")
    commands = parser.add_subparsers(dest="executors_command", required=True)
    for name in ("register", "runtime-register"):
        retired = commands.add_parser(name, help=_GUIDANCE)
        retired.set_defaults(func=_retired)
