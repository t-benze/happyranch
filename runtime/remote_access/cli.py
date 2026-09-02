"""Linux connector CLI (THR-097 phase unit 3 + Unit 3A).

Operator surface for the supervised Linux connector:

- ``run`` — the systemd ``Type=notify`` foreground loop (readiness-gated
  listener; SIGTERM/SIGINT stop the listener before exit). Requires
  ``--lab-only`` when the config carries a lab provider and ``--diy`` when
  it carries the Supported-DIY customer-owned-network provider, and fails
  closed (exit 1) when the config has no concrete provider/listener at all
  — READY=1 is never emitted without a proven bound listener.
- ``install`` / ``uninstall`` / ``start`` / ``stop`` / ``restart`` /
  ``enable`` / ``disable`` / ``status`` — systemd service lifecycle.
- ``readiness`` — evaluate the five gates; exit 0 only when ready.
- ``diagnose`` — redacted local diagnostics (never the daemon bearer, never
  pairing codes/credentials).
- ``upgrade`` / ``rollback`` — unit replacement with auto-rollback.
- **Supported-DIY ceremony (THR-097 Unit 3A):** ``pair`` issues a one-time
  pairing code (printed ONCE, never logged); ``list-devices`` shows the
  paired devices (redacted); ``revoke`` revokes one device or all
  (persisted; the connector closes live streams at its next
  reconciliation); ``remove-device`` removes a device and
  its credential; ``pairing-status`` reports truthful lifecycle states;
  ``recovery --factory-reset`` deletes BOTH snapshot+anchor files to return
  to the first-run deny-all default (explicit operator action).

No daemon, auth, schema, permission-model, or dependency change; this is the
packaging + ceremony surface only.
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import sys
from pathlib import Path

from runtime.remote_access.lab_provider import LAB_ONLY_BANNER
from runtime.remote_access.pairing import PairingError, PairingManager
from runtime.remote_access.state_store import CorruptTrustStateError, StateStoreError
from runtime.remote_access.supervisor import (
    ConnectorConfig,
    ConnectorConfigError,
    ConnectorSupervisor,
)

_DEFAULT_CONFIG = "~/.happyranch/remote_access/config.json"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="happyranch-connector",
        description="HappyRanch supervised Linux remote-access connector (THR-097 unit 3 / unit 3A)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    def add_lifecycle(name: str, help_text: str) -> argparse.ArgumentParser:
        p = sub.add_parser(name, help=help_text)
        p.add_argument("--config", default=_DEFAULT_CONFIG, help="connector config JSON path")
        return p

    run = add_lifecycle("run", "foreground readiness loop (systemd Type=notify)")
    run.add_argument("--lab-only", action="store_true", help="required when the config carries a lab provider")
    run.add_argument("--diy", action="store_true", help="required when the config carries the Supported-DIY provider")
    run.add_argument("--managed", action="store_true", help="required when the config carries managed sidecar ingress")
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
    retire = sub.add_parser("retire-enrollment-source", help=argparse.SUPPRESS)
    retire.add_argument("--source", required=True)
    retire.add_argument("--marker", required=True)

    pair = add_lifecycle("pair", "issue a one-time pairing code for a device (Supported-DIY ceremony)")
    pair.add_argument("--device", required=True, help="human-readable device name (e.g. macbook-pro)")
    add_lifecycle("list-devices", "list paired devices (redacted — never credentials/digests)")
    revoke = add_lifecycle("revoke", "revoke one device or all devices (persisted; the connector closes live streams at its next reconciliation)")
    revoke.add_argument("--device", default=None, help="device name to revoke; omit to revoke ALL")
    remove = add_lifecycle("remove-device", "remove a device and its credential entirely")
    remove.add_argument("--device", required=True, help="device name to remove")
    add_lifecycle("pairing-status", "truthful Supported-DIY pairing/revocation lifecycle status")
    recovery = add_lifecycle(
        "recovery",
        "factory-reset the local trust state (delete BOTH snapshot+anchor files -> first-run deny-all)",
    )
    recovery.add_argument("--factory-reset", action="store_true", help="confirm the destructive factory reset")
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
    if args.command == "retire-enrollment-source":
        try:
            _retire_enrollment_source(Path(args.source), Path(args.marker))
            return 0
        except OSError:
            print("error: enrollment_source_retirement_failed", file=sys.stderr)
            return 1
    try:
        config = _load_config(args.config)
    except (ConnectorConfigError, json.JSONDecodeError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    # Provider opt-in double-gating: the config must carry the provider AND
    # the operator must pass the matching flag on `run` (never silently
    # started as a product, never a provider-less READY).
    if args.command == "run":
        if config.lab is not None and config.diy is not None:
            print("error: config carries both lab and diy providers (mutually exclusive)", file=sys.stderr)
            return 1
        if config.lab is not None:
            if not args.lab_only:
                print(
                    f"error: config carries a LAB-ONLY provider; pass --lab-only\n{LAB_ONLY_BANNER}",
                    file=sys.stderr,
                )
                return 1
            print(f"{LAB_ONLY_BANNER}", file=sys.stderr)
        if config.diy is not None:
            if not args.diy:
                print(
                    "error: config carries the Supported-DIY provider; pass --diy",
                    file=sys.stderr,
                )
                return 1
        if config.managed is not None and not args.managed:
            print("error: config carries managed sidecar ingress; pass --managed", file=sys.stderr)
            return 1

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
        if args.command in {"pair", "list-devices", "revoke", "remove-device", "pairing-status", "recovery"}:
            return _run_ceremony(args, supervisor)
    except (ConnectorConfigError, OSError, RuntimeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 1


def _retire_enrollment_source(source: Path, marker: Path) -> None:
    """Retire the one-use source after READY; recover either side of rename."""
    if not source.is_absolute() or not marker.is_absolute() or source.name != "enrollment.key" or marker.name != "credential.consumed":
        raise OSError("invalid retirement path")
    retiring = source.with_name(source.name + ".retiring")
    marker_ok = marker.is_file() and not marker.is_symlink() and marker.stat().st_mode & 0o777 == 0o600
    if retiring.exists() or retiring.is_symlink():
        if retiring.is_symlink() or not retiring.is_file():
            raise OSError("invalid retirement residue")
        if marker_ok:
            retiring.unlink()
        elif not source.exists():
            retiring.replace(source)
        else:
            raise OSError("incoherent retirement residue")
        _fsync_dir(source.parent)
        if not marker_ok:
            raise OSError("enrollment not durable")
    if not marker_ok:
        raise OSError("enrollment not durable")
    if not source.exists():
        return
    st = source.lstat()
    if source.is_symlink() or not source.is_file() or st.st_mode & 0o777 != 0o600 or st.st_uid != os.geteuid():
        raise OSError("invalid enrollment source")
    source.replace(retiring)
    _fsync_dir(source.parent)
    retiring.unlink()
    _fsync_dir(source.parent)


def _fsync_dir(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _run_ceremony(args, supervisor: ConnectorSupervisor) -> int:
    """Supported-DIY pairing/revocation/recovery ceremony surface. All
    ceremony commands operate on the approved trust-state store (digests
    only) and render NO credential material."""
    try:
        if args.command == "pair":
            issued = supervisor.pairing_manager().issue_pairing_code(args.device)
            # The code is printed ONCE for the operator to relay to the away
            # client; it is never stored, logged, or repeated.
            print(f"pairing code for device '{issued.device_name}': {issued.code}")
            print(f"expires at: {issued.expires_at.isoformat()}")
            print("hand this code to the away client ONCE; it cannot be replayed.")
            return 0
        if args.command == "list-devices":
            _print_json(
                {
                    "devices": [
                        {
                            "device_id": d.device_id,
                            "authorization_epoch": d.authorization_epoch,
                            "expires_at": d.expires_at.isoformat(),
                            "revoked": d.revoked,
                            "paired": d.paired,
                        }
                        for d in supervisor.pairing_manager().list_devices()
                    ]
                }
            )
            return 0
        if args.command == "revoke":
            outcome = supervisor.pairing_manager().revoke(device_id=args.device)
            target = args.device or "ALL devices"
            # Cross-process honesty (TASK-6039 reviewer [CRITICAL] finding 2):
            # this CLI process is NOT the connector process serving the live
            # streams — it must never report stream closure it cannot prove.
            # The revocation is persisted; the connector closes any live
            # streams at its next reconciliation (bounded by poll_seconds).
            print(
                f"revoked {target}: revocation epoch {outcome.epoch} persisted; "
                f"the connector closes any live streams at its next "
                f"reconciliation"
            )
            return 0
        if args.command == "remove-device":
            supervisor.pairing_manager().remove_device(args.device)
            print(f"removed device '{args.device}' and its credential")
            return 0
        if args.command == "pairing-status":
            _print_json(supervisor.pairing_manager().pairing_status())
            return 0
        if args.command == "recovery":
            if not args.factory_reset:
                print(
                    "refusing: recovery --factory-reset deletes BOTH the trust-state "
                    "snapshot and its companion anchor (returns to the first-run "
                    "deny-all default). Pass --factory-reset to confirm.",
                    file=sys.stderr,
                )
                return 1
            return _factory_reset(supervisor)
    except (PairingError, StateStoreError, CorruptTrustStateError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 1


def _factory_reset(supervisor: ConnectorSupervisor) -> int:
    """Explicit operator factory-reset of the LOCAL trust state per the
    store's crash-consistency contract: delete BOTH files (snapshot + anchor)
    so the next load returns the fresh deny-all default. Deleting exactly one
    of the pair would be partial state (fail closed), so both must go."""
    state_path = Path(supervisor.config.state_path).expanduser()
    snapshot = state_path
    anchor = Path(str(state_path) + ".anchor")
    removed = []
    for path in (snapshot, anchor):
        try:
            if path.exists() or path.is_symlink():
                path.unlink()
                removed.append(str(path))
        except OSError as exc:
            print(f"error: cannot remove {path}: {exc}", file=sys.stderr)
            return 1
    if not removed:
        print("no trust state present (first run); nothing to reset")
        return 0
    print("factory reset complete: removed " + ", ".join(removed))
    return 0


def _run_foreground(supervisor: ConnectorSupervisor) -> int:
    def _stop(_signum, _frame) -> None:
        supervisor.shutdown()
        raise SystemExit(0)

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    return supervisor.run()


if __name__ == "__main__":
    sys.exit(main())
