"""Machine-local executor binary-path registration CLI — THR-085 / THR-107 seq155.

The daemon stores per-executor-kind binary paths in a machine-local registry
at ``<daemon-home>/executors.json`` so that headless daemon launches (no web UI)
can self-heal: the operator runs ``happyranch executor-binaries`` commands to
tell the daemon where each executor CLI binary lives on this host.

Distinct from ``executors.py`` (THR-052 PROFILE registration — which executor
kinds/capabilities exist, org-portable). This group writes into the
machine-local binary registry, NOT org/config.yaml.

Commands:
  register <kind> --path <ABS_PATH>  — validate-then-register a binary path.
      ``--path`` is REQUIRED and must be an absolute path.
      Omission does NOT fall back to PATH resolution (THR-107 seq155).
  list                               — list registered binary paths
  remove <kind> --expected-path <ABS_PATH>
                                     — conditionally remove a binary registration.
      Both ``kind`` and ``--expected-path`` must match the registered record exactly.
      Built-in kinds (claude, codex, opencode, pi) cannot be removed.
"""
from __future__ import annotations

import argparse
import sys

from cli.client.client import OpcClient
from runtime.orchestrator.executor_binary_registry import (
    _registry_path,
    is_test_process,
    write_target_is_default_production_registry,
)


# ---------------------------------------------------------------------------
# Test-context write guard (THR-204 issue 3 / TASK-5579)
# ---------------------------------------------------------------------------


def _assert_operator_register_context() -> None:
    """Refuse to issue a registration/removal from a TEST context when the
    daemon this CLI would talk to owns the DEFAULT production registry.

    The daemon-mediated route (``POST /api/v1/executor-binaries/register`` /
    DELETE) performs its write inside the daemon process, which never runs
    under pytest — so a repro that shells out to this CLI from a test could
    overwrite the live production registry under production executor names
    (the 2026-08-23 THR-204 issue-3 incidents).  The CLI subprocess inherits
    the test marker (``PYTEST_CURRENT_TEST``) from its pytest parent, so this
    pre-flight guard refuses BEFORE any HTTP request reaches the daemon.

    Isolation is simply setting ``HAPPYRANCH_DAEMON_HOME`` to a temporary
    daemon home; operator shells never run under pytest, so legitimate
    operator registration is unaffected.
    """
    if not is_test_process():
        return
    if not write_target_is_default_production_registry():
        return

    print(
        "error: refusing to register executor binaries into the production "
        "executor-binary registry at "
        f"{_registry_path()} from a test context. Set "
        "HAPPYRANCH_DAEMON_HOME to an isolated temporary daemon home (e.g. a "
        "pytest tmp_path) before registering executor binaries in tests.",
        file=sys.stderr,
    )
    sys.exit(1)


def cmd_executor_binaries_register(args: argparse.Namespace) -> None:
    """Register a binary path for an executor kind.

    Calls POST /api/v1/executor-binaries/register (validate-then-store).

    ``--path`` is REQUIRED and must be an absolute path (THR-107 seq155).
    Omission does NOT fall back to PATH resolution.
    """
    kind: str = args.kind

    if args.path is None:
        print(
            f"error: --path is required. "
            f"Provide an absolute path to the '{kind}' binary, e.g.:\n"
            f"  happyranch executor-binaries register {kind} --path /opt/homebrew/bin/{kind}",
            file=sys.stderr,
        )
        sys.exit(1)

    resolved = args.path
    if not resolved.startswith("/"):
        print(
            f"error: --path must be an absolute path (got {resolved!r})",
            file=sys.stderr,
        )
        sys.exit(1)

    # THR-204 issue 3 / TASK-5579: refuse pre-flight when a test context
    # would target the DEFAULT production registry through the daemon.
    _assert_operator_register_context()

    client = OpcClient.from_env()

    try:
        r = client.post(
            "/api/v1/executor-binaries/register",
            json={"kind": kind, "path": resolved},
        )
    except Exception as exc:
        print(f"error: failed to reach daemon — {exc}", file=sys.stderr)
        print("Is the daemon running?", file=sys.stderr)
        sys.exit(1)

    if r.status_code == 200:
        body = r.json()
        print(f"  + registered: {body['kind']} -> {body['path']} (valid={body['valid']})")
    elif r.status_code == 422:
        body = r.json()
        print(f"error: {body.get('detail', 'validation failed')}", file=sys.stderr)
        sys.exit(1)
    else:
        print(f"error: HTTP {r.status_code}", file=sys.stderr)
        try:
            detail = r.json()
            print(f"  {detail}", file=sys.stderr)
        except ValueError:
            print(f"  {r.text}", file=sys.stderr)
        sys.exit(1)


def cmd_executor_binaries_list(args: argparse.Namespace) -> None:
    """List all registered binary paths.

    Calls GET /api/v1/executor-binaries.
    """
    client = OpcClient.from_env()

    try:
        r = client.get("/api/v1/executor-binaries")
    except Exception as exc:
        print(f"error: failed to reach daemon — {exc}", file=sys.stderr)
        print("Is the daemon running?", file=sys.stderr)
        sys.exit(1)

    if r.status_code == 200:
        body = r.json()
        entries = body.get("entries", [])
        if not entries:
            print("no registered executor binaries")
            return
        for entry in entries:
            status = "valid" if entry.get("valid") else "stale"
            path = entry.get("path") or "(none)"
            print(f"  {entry['kind']:12s}  {path:45s}  ({status})")
    else:
        print(f"error: HTTP {r.status_code}", file=sys.stderr)
        try:
            detail = r.json()
            print(f"  {detail}", file=sys.stderr)
        except ValueError:
            print(f"  {r.text}", file=sys.stderr)
        sys.exit(1)


def cmd_executor_binaries_remove(args: argparse.Namespace) -> None:
    """Conditionally remove a binary registration by kind and exact path.

    Calls DELETE /api/v1/executor-binaries/{kind} with a JSON body
    containing ``expected_name`` (the positional kind) and
    ``expected_path`` (the required --expected-path).

    Status codes:
    - 200 → prints the removed registration.
    - 404 → reports no registration for that kind.
    - 409 → stored path differs from expected_path (race / stale data).
    - 422 → validation error (built-in kind, name mismatch, etc.).
    - Transport / unexpected → surfaced clearly on stderr.
    """
    kind: str = args.kind
    expected_path: str = args.expected_path

    if not expected_path.startswith("/"):
        print(
            f"error: --expected-path must be an absolute path (got {expected_path!r})",
            file=sys.stderr,
        )
        sys.exit(1)

    # THR-204 issue 3 / TASK-5579: refuse pre-flight when a test context
    # would target the DEFAULT production registry through the daemon.
    _assert_operator_register_context()

    client = OpcClient.from_env()

    try:
        r = client.request(
            "DELETE",
            f"/api/v1/executor-binaries/{kind}",
            json={"expected_name": kind, "expected_path": expected_path},
        )
    except Exception as exc:
        print(f"error: failed to reach daemon — {exc}", file=sys.stderr)
        print("Is the daemon running?", file=sys.stderr)
        sys.exit(1)

    if r.status_code == 200:
        body = r.json()
        removed = body.get("removed", False)
        if removed:
            print(f"  ✓ removed: {body['kind']} -> {expected_path}")
        else:
            print(f"  ⚠ unexpected: removal not confirmed for {body.get('kind', kind)}")
    elif r.status_code == 404:
        body = r.json()
        print(f"  not found: {body.get('detail', f'no registration for {kind!r}')}")
    elif r.status_code == 409:
        body = r.json()
        print(f"  conflict: {body.get('detail', 'stored path mismatch')}", file=sys.stderr)
        print("  The record may have been updated concurrently — use 'list' to refresh.", file=sys.stderr)
        sys.exit(1)
    elif r.status_code == 422:
        body = r.json()
        print(f"error: {body.get('detail', 'validation failed')}", file=sys.stderr)
        sys.exit(1)
    else:
        print(f"error: HTTP {r.status_code}", file=sys.stderr)
        try:
            detail = r.json()
            print(f"  {detail}", file=sys.stderr)
        except ValueError:
            print(f"  {r.text}", file=sys.stderr)
        sys.exit(1)


def register(sub) -> None:
    p = sub.add_parser(
        "executor-binaries",
        help="Machine-local executor binary path management",
    )
    exec_sub = p.add_subparsers(dest="executor_binaries_command", required=True)

    p_reg = exec_sub.add_parser(
        "register",
        help="Register a binary path for an executor kind (validate-then-store)",
    )
    p_reg.add_argument("kind", help="Executor kind, e.g. 'claude', 'codex', 'pi'")
    p_reg.add_argument(
        "--path",
        required=True,
        help="Absolute path to the executor binary (required)",
    )
    p_reg.set_defaults(func=cmd_executor_binaries_register)

    p_list = exec_sub.add_parser(
        "list",
        help="List registered executor binary paths",
    )
    p_list.set_defaults(func=cmd_executor_binaries_list)

    p_rm = exec_sub.add_parser(
        "remove",
        help="Conditionally remove a binary registration by kind and exact path",
    )
    p_rm.add_argument("kind", help="Executor kind to remove, e.g. 'my-custom-cli'")
    p_rm.add_argument(
        "--expected-path",
        required=True,
        help="Exact absolute path that must match the registered record exactly",
    )
    p_rm.set_defaults(func=cmd_executor_binaries_remove)
