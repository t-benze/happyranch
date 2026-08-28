"""Linux connector CLI (THR-097 phase unit 3).

Operator surface for the supervised Linux connector:

- ``run`` — the systemd ``Type=notify`` foreground loop (readiness-gated
  listener; SIGTERM/SIGINT stop the listener before exit). Requires
  ``--lab-only`` when the config carries a lab provider, and fails closed
  (exit 1) when the config has no concrete lab provider/listener at all —
  READY=1 is never emitted without a proven bound listener.
- ``install`` / ``uninstall`` / ``start`` / ``stop`` / ``restart`` /
  ``enable`` / ``disable`` / ``status`` — systemd service lifecycle.
- ``readiness`` — evaluate the five gates; exit 0 only when ready.
- ``diagnose`` — redacted local diagnostics (never the daemon bearer).
- ``upgrade`` / ``rollback`` — unit replacement with auto-rollback.

No daemon, auth, schema, permission-model, or dependency change; this is the
packaging surface only.
"""
from __future__ import annotations

import argparse
import json
import signal
import sys
from pathlib import Path

from runtime.remote_access.lab_provider import LAB_ONLY_BANNER
from runtime.remote_access.supervisor import (
    ConnectorConfig,
    ConnectorConfigError,
    ConnectorSupervisor,
)

_DEFAULT_CONFIG = "~/.happyranch/remote_access/config.json"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="happyranch-connector",
        description="HappyRanch supervised Linux remote-access connector (THR-097 unit 3)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    def add_lifecycle(name: str, help_text: str) -> argparse.ArgumentParser:
        p = sub.add_parser(name, help=help_text)
        p.add_argument("--config", default=_DEFAULT_CONFIG, help="connector config JSON path")
        return p

    add_lifecycle("run", "foreground readiness loop (systemd Type=notify)").add_argument(
        "--lab-only", action="store_true", help="required when the config carries a lab provider"
    )
    install = add_lifecycle("install", "render + install the systemd unit")
    install.add_argument("--no-enable", action="store_true", help="do not enable on boot")
    add_lifecycle("uninstall", "stop, disable, and remove the systemd unit")
    add_lifecycle("start", "start the connector service")
    add_lifecycle("stop", "stop the connector service")
    add_lifecycle("restart", "restart the connector service")
    add_lifecycle("enable", "enable the connector service on boot")
    add_lifecycle("disable", "disable the connector service on boot")
    add_lifecycle("status", "print the connector service status")
    add_lifecycle("readiness", "evaluate the five readiness gates (exit 0 only when ready)")
    add_lifecycle("diagnose", "redacted local diagnostics")
    upgrade = add_lifecycle("upgrade", "install the new unit over a backup and restart")
    upgrade.add_argument("--no-verify", dest="verify_start", action="store_false", help="skip start verification")
    add_lifecycle("rollback", "restore the most recent unit backup and restart")
    return parser


def _load_config(path: str) -> ConnectorConfig:
    config_path = Path(path).expanduser()
    if not config_path.is_file():
        raise ConnectorConfigError(f"config file not found: {config_path}")
    return ConnectorConfig.from_file(config_path)


def _print_json(payload: dict) -> None:
    print(json.dumps(payload, indent=2, sort_keys=True, default=str))


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        config = _load_config(args.config)
    except (ConnectorConfigError, json.JSONDecodeError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    # Lab-only double-gating: the config must carry lab_only AND the operator
    # must pass --lab-only on `run` (never silently started as a product).
    if args.command == "run":
        if config.lab is not None:
            if not args.lab_only:
                print(
                    f"error: config carries a LAB-ONLY provider; pass --lab-only\n{LAB_ONLY_BANNER}",
                    file=sys.stderr,
                )
                return 1
            print(f"{LAB_ONLY_BANNER}", file=sys.stderr)

    supervisor = ConnectorSupervisor(config=config)
    try:
        if args.command == "run":
            return _run_foreground(supervisor)
        if args.command == "install":
            supervisor.install(enable=not args.no_enable)
            return 0
        if args.command == "uninstall":
            supervisor.uninstall()
            return 0
        if args.command == "start":
            supervisor.start()
            return 0
        if args.command == "stop":
            supervisor.stop()
            return 0
        if args.command == "restart":
            supervisor.restart()
            return 0
        if args.command == "enable":
            supervisor.enable()
            return 0
        if args.command == "disable":
            supervisor.disable()
            return 0
        if args.command == "status":
            _print_json(supervisor.status().__dict__)
            return 0
        if args.command == "readiness":
            report = supervisor.readiness_report()
            _print_json(
                {
                    "ready": report.ready,
                    "gates": {
                        name: {"ok": gate.ok, "category": gate.category}
                        for name, gate in report.gates.items()
                    },
                }
            )
            return 0 if report.ready else 1
        if args.command == "diagnose":
            _print_json(supervisor.diagnose())
            return 0
        if args.command == "upgrade":
            outcome = supervisor.upgrade(verify_start=args.verify_start)
            _print_json(outcome.__dict__)
            return 0 if outcome.ok else 1
        if args.command == "rollback":
            outcome = supervisor.rollback()
            _print_json(outcome.__dict__)
            return 0 if outcome.ok else 1
    except (ConnectorConfigError, OSError, RuntimeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 1


def _run_foreground(supervisor: ConnectorSupervisor) -> int:
    def _stop(_signum, _frame) -> None:
        supervisor.shutdown()
        raise SystemExit(0)

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    return supervisor.run()


if __name__ == "__main__":
    sys.exit(main())
