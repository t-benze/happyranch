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
from contextlib import nullcontext
import json
import os
import select
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable

from runtime.remote_access.lab_provider import LAB_ONLY_BANNER
from runtime.remote_access.linux_package import (
    credential_capability,
    require_credential_capability,
)
from runtime.remote_access.pairing import PairingError, PairingManager
from runtime.remote_access.state_store import CorruptTrustStateError, StateStoreError
from runtime.remote_access.supervisor import (
    ConnectorConfig,
    ConnectorConfigError,
    ConnectorSupervisor,
)


def _expected_systemd_credentials_directory(unit: str) -> Path:
    """Return the only systemd staging directory trusted for ``unit``."""
    return Path("/run/credentials") / unit

_DEFAULT_CONFIG = "~/.happyranch/remote_access/config.json"

# THR-228 seq275: fresh enrollment is a stopped-service-only transition. The
# consumed-marker/drop-in mutation and the daemon reload are authorized only by
# one bounded, successful `systemctl show` observation whose explicitly parsed
# named properties affirmatively prove the sidecar unit is loaded and stopped.
_SIDECAR_UNIT = "happyranch-tsnet-sidecar.service"
_SERVICE_STATE_PROPERTIES = ("LoadState", "ActiveState", "SubState")
_SERVICE_QUERY_TIMEOUT_SECONDS = 5.0
_SERVICE_QUERY_MAX_OUTPUT_BYTES = 4096
_SERVICE_QUERY_READ_CHUNK_BYTES = 4096

# THR-228 seq275 (TASK8623): cleanup needs its own explicitly bounded kill/reap
# allowance. The 5-second observation deadline bounds reading and exit
# observation only; it is never extended to restart reading or to authorize a
# refused observation. But once it has expired, a still-live owned child must
# still be killed and then CONFIRMED reaped before the query returns, so this
# separate finite allowance is measured from whichever is later, the
# observation deadline or the start of cleanup. Confirming that an owned child
# is reaped is cleanup evidence only (TASK8644 R1): it never authorizes a
# successful observation that completed after the deadline.
_SERVICE_QUERY_REAP_GRACE_SECONDS = 5.0

# THR-228 seq275 (TASK8607 F1): the only supported record framing is LF-delimited
# ``Key=Value`` records. ``str.splitlines()`` would silently normalize these
# non-LF separators into record boundaries, so they are rejected in the raw
# observation before any split. CR/CRLF, VT (0x0b), FF (0x0c), FS (0x1c),
# GS (0x1d), RS (0x1e), NEL (U+0085), LS (U+2028) and PS (U+2029) are never
# legitimate in an affirmative ``systemctl show`` observation.
_SERVICE_QUERY_UNSUPPORTED_RECORD_SEPARATORS = (
    "\r",
    "\x0b",
    "\x0c",
    "\x1c",
    "\x1d",
    "\x1e",
    "\x85",
    "\u2028",
    "\u2029",
)


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
    retire.add_argument("--dropin")
    reconcile = sub.add_parser("reconcile-enrollment-retirement", help=argparse.SUPPRESS)
    reconcile.add_argument("--source", required=True)
    reconcile.add_argument("--marker", required=True)
    reconcile.add_argument("--dropin", required=True)
    fresh = sub.add_parser("prepare-fresh-enrollment", help=argparse.SUPPRESS)
    fresh.add_argument("--source", required=True)
    fresh.add_argument("--marker", required=True)
    fresh.add_argument("--dropin", required=True)
    capability = sub.add_parser("credential-capability", help=argparse.SUPPRESS)
    capability.add_argument("--name", choices=("daemon.token", "enrollment.key"), required=True)
    capability.add_argument(
        "--unit",
        choices=("happyranch-connector.service", "happyranch-tsnet-sidecar.service"),
        required=True,
    )
    capability.add_argument("--consumed-marker")

    lifecycle = sub.add_parser("managed-lifecycle", help="explicit stopped managed repair")
    lifecycle_sub = lifecycle.add_subparsers(dest="lifecycle_action", required=True)
    repair = lifecycle_sub.add_parser("reconcile")
    repair.add_argument("--managed", action="store_true", required=True)
    repair.add_argument("--config", required=True, choices=("/etc/happyranch/connector.json",))
    repair.add_argument("--expected-installation", required=True)
    repair.add_argument("--expected-revision", required=True, type=int)
    repair.add_argument("--operation-id", required=True)
    private = sub.add_parser("managed-private", help=argparse.SUPPRESS)
    private.add_argument("operation", choices=("eligibility", "package-install", "package-uninstall"))

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


def _managed_enrollment_paths(source: Path, marker: Path, dropin: Path | None) -> bool:
    """Select the fixed tuple, or prove a separate tuple before body effects.

    Equivalent spellings, links, mixed tuples and unknown ownership refuse;
    resolving metadata never grants fixed-path or credential authority.
    """
    from runtime.remote_access.linux_package import (
        _nonmanaged_owner_destination, _nonmanaged_owner_same_path,
    )
    fixed = (Path("/etc/happyranch/enrollment.key"),
             Path("/var/lib/happyranch-tsnet-sidecar/credential.consumed"),
             Path("/etc/systemd/system/happyranch-tsnet-sidecar.service.d/10-enrollment-credential.conf"))
    selected = (source, marker, dropin)
    if selected == fixed:
        return True
    if any(path == owned for path in selected if path is not None for owned in fixed):
        raise OSError("enrollment_path_owner_invalid")
    try:
        for path in selected:
            if path is None:
                continue
            if not path.is_absolute() or ".." in path.parts:
                raise OSError("enrollment_path_owner_invalid")
            destination = _nonmanaged_owner_destination(path)
            if destination != path:
                raise OSError("enrollment_path_owner_invalid")
            directories = (*(owned.parent for owned in fixed), Path("/var/lib/happyranch-connector"))
            for directory in directories:
                if any(_nonmanaged_owner_same_path(parent, directory)
                       for parent in (destination, *destination.parents)):
                    raise OSError("enrollment_path_owner_invalid")
            for owned in fixed:
                # Parent identity catches existing directory aliases with an
                # absent leaf; file identity catches existing hard-link aliases.
                if (_nonmanaged_owner_same_path(destination, owned)
                        or _nonmanaged_owner_same_path(destination.parent, owned.parent)):
                    raise OSError("enrollment_path_owner_invalid")
                # A missing tail under an existing directory alias is still
                # fixed ownership. Compare only matching suffix components;
                # a shared unrelated filesystem root supplies no ownership.
                left, right = destination, owned
                while left.name == right.name and left.parent != left and right.parent != right:
                    left, right = left.parent, right.parent
                    if _nonmanaged_owner_same_path(left, right):
                        raise OSError("enrollment_path_owner_invalid")
    except (OSError, RuntimeError, ValueError):
        raise OSError("enrollment_path_owner_invalid") from None
    return False


def main(argv: list[str] | None = None) -> int:
    entry_ns = time.monotonic_ns()
    args = build_parser().parse_args(argv)
    from runtime.remote_access.linux_package import _MANAGED_DEADLINE
    seconds = 60 if args.command == "prepare-fresh-enrollment" else 120
    if args.command in {"start", "stop", "restart", "enable", "disable", "status", "managed-lifecycle"}:
        seconds = 45
    inherited = _MANAGED_DEADLINE.get()
    limit = entry_ns + seconds * 1_000_000_000
    managed_route = args.command in {"start", "stop", "restart", "enable", "disable", "status", "managed-lifecycle"}
    if args.command == "credential-capability":
        managed_route = args.unit == _SIDECAR_UNIT
    if args.command in {"prepare-fresh-enrollment", "retire-enrollment-source", "reconcile-enrollment-retirement"}:
        try:
            managed_route = _managed_enrollment_paths(
                Path(args.source), Path(args.marker), Path(args.dropin) if args.dropin else None)
        except OSError:
            if args.command == "prepare-fresh-enrollment":
                print("error: fresh_enrollment_transition_failed", file=sys.stderr)
                return 1
            return 78
    if managed_route and args.command not in {"start", "stop", "restart", "enable", "disable", "status"}:
        from runtime.remote_access.linux_package import _managed_entry_deadline
        try:
            limit = _managed_entry_deadline(seconds, entry_ns=entry_ns)
        except RuntimeError:
            print("error: managed_lifecycle_refused", file=sys.stderr)
            return 78 if args.command in {"credential-capability", "retire-enrollment-source", "reconcile-enrollment-retirement"} else 1
    token = _MANAGED_DEADLINE.set(min(inherited if inherited is not None else limit, limit) if managed_route else inherited)
    try:
        result = _main(args, entry_ns)
        fixed_body = managed_route and args.command in {
            "managed-lifecycle", "prepare-fresh-enrollment", "retire-enrollment-source",
            "reconcile-enrollment-retirement", "credential-capability",
        }
        deadline = _MANAGED_DEADLINE.get()
        if deadline is not None:
            deadline = min(deadline, getattr(args, "_managed_body_deadline", deadline))
        if result == 0 and (fixed_body or getattr(args, "_managed_routed", False)) and (
                deadline is None or time.monotonic_ns() >= deadline):
            print("error: managed_lifecycle_refused", file=sys.stderr)
            return 78 if args.command == "credential-capability" else 1
        return result
    finally:
        _MANAGED_DEADLINE.reset(token)


def _main(args: argparse.Namespace, entry_ns: int) -> int:
    if args.command == "managed-private":
        from runtime.remote_access.linux_package import managed_private_entry
        return managed_private_entry(args.operation)
    if args.command == "managed-lifecycle":
        from runtime.remote_access.linux_package import managed_lifecycle
        import re
        if (any(re.fullmatch(r"[0-9a-f]{32}", value) is None or value == "0" * 32
                for value in (args.expected_installation, args.operation_id))
                or not 1 <= args.expected_revision <= 2**63 - 1):
            return 2
        try:
            _print_json(managed_lifecycle("reconcile", expected_installation=args.expected_installation,
                expected_revision=args.expected_revision, operation_id=args.operation_id))
            return 0
        except RuntimeError:
            print("error: managed_lifecycle_refused", file=sys.stderr)
            return 1
    if args.command in {"retire-enrollment-source", "reconcile-enrollment-retirement", "credential-capability"}:
        from runtime.remote_access.linux_package import _MANAGED_LOCK, managed_body_guard
        fixed_helper = (args.command == "credential-capability" and args.unit == "happyranch-tsnet-sidecar.service")
        if args.command != "credential-capability":
            try:
                fixed_helper = _managed_enrollment_paths(
                    Path(args.source), Path(args.marker), Path(args.dropin) if args.dropin else None)
            except OSError:
                return 78
        if fixed_helper and _MANAGED_LOCK.get() is None:
            try:
                with managed_body_guard("run", entry_ns=entry_ns) as deadline:
                    args._managed_body_deadline = deadline
                    return _main(args, entry_ns)
            except (OSError, RuntimeError, ValueError, subprocess.SubprocessError):
                return 78
    if args.command == "retire-enrollment-source":
        try:
            _retire_enrollment_source(Path(args.source), Path(args.marker), dropin=Path(args.dropin) if args.dropin else None)
            return 0
        except (OSError, subprocess.CalledProcessError):
            print("error: enrollment_source_retirement_failed", file=sys.stderr)
            return 1
    if args.command == "reconcile-enrollment-retirement":
        try:
            _reconcile_enrollment_retirement(
                Path(args.source), Path(args.marker), dropin=Path(args.dropin)
            )
            return 0
        except (OSError, subprocess.CalledProcessError):
            print("error: enrollment_source_retirement_failed", file=sys.stderr)
            return 1
    if args.command == "prepare-fresh-enrollment":
        try:
            _prepare_fresh_enrollment(
                Path(args.source), Path(args.marker), dropin=Path(args.dropin)
            )
            return 0
        except (OSError, RuntimeError, ValueError, subprocess.SubprocessError):
            print("error: fresh_enrollment_transition_failed", file=sys.stderr)
            return 1
    if args.command == "credential-capability":
        expected_unit = {
            "daemon.token": "happyranch-connector.service",
            "enrollment.key": "happyranch-tsnet-sidecar.service",
        }[args.name]
        if args.unit != expected_unit:
            print("credential_staging_incompatible", file=sys.stderr)
            return 1
        if args.consumed_marker and (
            args.name != "enrollment.key"
            or not Path(args.consumed_marker).is_absolute()
            or Path(args.consumed_marker).name != "credential.consumed"
        ):
            print("credential_staging_incompatible", file=sys.stderr)
            return 1
        credentials_directory = os.environ.get("CREDENTIALS_DIRECTORY")
        if not credentials_directory:
            if args.consumed_marker:
                marker = Path(args.consumed_marker)
                category = credential_capability(
                    marker, expected_uid=os.geteuid(), allowed_modes=(0o600,)
                )
                if category == "credential_valid":
                    return 0
            print("credential_absent", file=sys.stderr)
            return 1
        expected_directory = _expected_systemd_credentials_directory(args.unit)
        if Path(credentials_directory) != expected_directory:
            print("credential_staging_incompatible", file=sys.stderr)
            return 1
        try:
            directory_metadata = expected_directory.lstat()
            directory_is_safe = (
                directory_metadata.st_mode & 0o170000 == 0o040000
                and not expected_directory.is_symlink()
                and not os.access(expected_directory, os.W_OK)
            )
        except OSError:
            directory_is_safe = False
        if not directory_is_safe:
            print("credential_staging_incompatible", file=sys.stderr)
            return 1
        category = credential_capability(
            Path(credentials_directory) / args.name,
            expected_uid=None,
            allowed_modes=None,
            require_read_only=True,
        )
        if category != "credential_valid":
            print(category, file=sys.stderr)
            return 1
        return 0
    try:
        config = _load_config(args.config)
    except (ConnectorConfigError, json.JSONDecodeError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    if config.managed is not None and args.command in {"start", "stop", "restart", "enable", "disable", "status", "readiness"}:
        from runtime.remote_access.linux_package import _MANAGED_DEADLINE, _managed_entry_deadline
        try:
            deadline = _managed_entry_deadline(45, entry_ns=entry_ns)
        except RuntimeError:
            print("error: managed_lifecycle_refused", file=sys.stderr)
            return 1
        _MANAGED_DEADLINE.set(deadline)
        args._managed_routed = True

    # Provider opt-in double-gating: the config must carry the provider AND
    # the operator must pass the matching flag on `run` (never silently
    # started as a product, never a provider-less READY).
    if args.command == "run":
        if config.managed is not None:
            from runtime.remote_access.linux_package import require_managed_worker
            try:
                require_managed_worker()
                # The existing credential-capability body now runs in the
                # admitted worker, never as an unaccounted connector prehelper.
                if main(["credential-capability", "--name", "daemon.token", "--unit", "happyranch-connector.service"]) != 0:
                    return 1
            except (OSError, RuntimeError):
                print("error: managed_lifecycle_refused", file=sys.stderr)
                return 1
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
            ready = report.ready
            if config.managed is not None:
                # Operator-only conjunction. The worker's five gates precede
                # composite admission and must not depend on composite READY.
                try:
                    lifecycle = supervisor.status()
                    ready = (ready and lifecycle.active_state == "ready"
                             and lifecycle.sub_state == "ready"
                             and time.monotonic_ns() < deadline)
                except (OSError, RuntimeError):
                    ready = False
            _print_json(
                {
                    "ready": ready,
                    "gates": {
                        name: {"ok": gate.ok, "category": gate.category}
                        for name, gate in report.gates.items()
                    },
                }
            )
            return 0 if ready else 1
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


def _retire_enrollment_source(
    source: Path,
    marker: Path,
    *,
    dropin: Path | None = None,
    reload_manager: Callable[[], None] | None = None,
) -> None:
    """Retire the one-use source after READY; recover either side of rename."""
    entry_ns = time.monotonic_ns()
    from runtime.remote_access.linux_package import _check_managed_holder, managed_body_guard
    managed = _managed_enrollment_paths(source, marker, dropin)
    with managed_body_guard("run", entry_ns=entry_ns) if managed else nullcontext():
        def current() -> None:
            if managed:
                _check_managed_holder()

        if (not source.is_absolute() or not marker.is_absolute() or source.name != "enrollment.key"
                or marker.name != "credential.consumed" or (dropin is not None and
                (not dropin.is_absolute() or dropin.name != "10-enrollment-credential.conf"))):
            raise OSError("invalid retirement path")
        retiring = source.with_name(source.name + ".retiring")
        current()
        marker_ok = marker.is_file() and not marker.is_symlink() and marker.stat().st_mode & 0o777 == 0o600
        current()
        retiring_pending = retiring.exists() or retiring.is_symlink()
        if retiring_pending:
            if retiring.is_symlink() or not retiring.is_file():
                raise OSError("invalid retirement residue")
            if not marker_ok:
                if not source.exists():
                    current()
                    retiring.replace(source)
                    current()
                else:
                    raise OSError("incoherent retirement residue")
                current()
                _fsync_dir(source.parent)
                current()
                raise OSError("enrollment not durable")
        if not marker_ok:
            raise OSError("enrollment not durable")
        if not source.exists():
            if dropin is not None and dropin.exists():
                current()
                dropin.unlink()
                current()
                _fsync_dir(dropin.parent)
                current()
                (reload_manager or _reload_systemd)()
                current()
            elif retiring_pending and dropin is not None:
                current()
                (reload_manager or _reload_systemd)()
                current()
            if retiring_pending:
                current()
                retiring.unlink()
                current()
                _fsync_dir(source.parent)
                current()
            return
        current()
        st = source.lstat()
        current()
        if source.is_symlink() or not source.is_file() or st.st_mode & 0o777 != 0o600 or st.st_uid != os.geteuid():
            raise OSError("invalid enrollment source")
        if dropin is not None:
            if dropin.exists():
                if dropin.is_symlink() or not dropin.is_file():
                    raise OSError("invalid credential dropin")
                current()
                dropin.unlink()
                current()
                _fsync_dir(dropin.parent)
                current()
            # An absent drop-in can be interrupted unlink, not proof of reload.
            current()
            (reload_manager or _reload_systemd)()
            current()
        if retiring_pending:
            current()
            retiring.unlink()
            current()
            _fsync_dir(source.parent)
            current()
        current()
        source.replace(retiring)
        current()
        _fsync_dir(source.parent)
        current()
        retiring.unlink()
        current()
        _fsync_dir(source.parent)
        current()


def _reconcile_enrollment_retirement(
    source: Path, marker: Path, *, dropin: Path
) -> None:
    """Re-establish reload before finishing an interrupted retirement."""
    entry_ns = time.monotonic_ns()
    from runtime.remote_access.linux_package import _check_managed_holder, managed_body_guard
    managed = _managed_enrollment_paths(source, marker, dropin)
    with managed_body_guard("run", entry_ns=entry_ns) if managed else nullcontext():
        def current() -> None:
            if managed:
                _check_managed_holder()

        retiring = source.with_name(source.name + ".retiring")
        if dropin.exists() or not (source.exists() or retiring.exists() or retiring.is_symlink()):
            return
        current()
        _retire_enrollment_source(source, marker, dropin=dropin)
        current()


def _prepare_fresh_enrollment(
    source: Path,
    marker: Path,
    *,
    dropin: Path,
    reload_manager: Callable[[], None] | None = None,
    require_service_stopped: Callable[[], None] | None = None,
) -> None:
    """Explicitly replace consumed state after an operator installs a fresh source.

    THR-228 seq275: the marker/drop-in mutation and the daemon reload are
    reached only after ``require_service_stopped`` (the bounded affirmative
    production observation by default) proves the sidecar unit is loaded and
    stopped. Every query error, timeout, unavailable/unknown/malformed or
    transitional observation raises the existing category-only failure before
    any filesystem or reload side effect.
    """
    entry_ns = time.monotonic_ns()
    from runtime.remote_access.linux_package import (
        _MANAGED_DECLARATION, _check_managed_holder, managed_body_guard,
    )
    managed = _managed_enrollment_paths(source, marker, dropin)
    with managed_body_guard("stopped", entry_ns=entry_ns) if managed else nullcontext():
        if (
            not source.is_absolute()
            or source.name != "enrollment.key"
            or not marker.is_absolute()
            or marker.name != "credential.consumed"
            or not dropin.is_absolute()
            or dropin.name != "10-enrollment-credential.conf"
        ):
            raise OSError("invalid fresh enrollment path")
        def current() -> None:
            if managed:
                _check_managed_holder()

        current()
        (require_service_stopped or _observe_sidecar_stopped)()
        current()
        require_credential_capability(source, expected_uid=os.geteuid())
        current()
        if marker.exists():
            if marker.is_symlink() or not marker.is_file() or marker.stat().st_mode & 0o777 != 0o600:
                raise OSError("invalid consumed marker")
            current()
            marker.unlink()
            current()
            _fsync_dir(marker.parent)
            current()
        new_directory = not dropin.parent.exists()
        current()
        dropin.parent.mkdir(mode=0o755, exist_ok=True)
        current()
        if managed and new_directory:
            declaration = _MANAGED_DECLARATION.get()
            if declaration is None:
                raise OSError("managed_stopped_context_invalid")
            dropin.parent.chmod(declaration["dropin_directory_mode"])
            current()
        temporary = dropin.with_name(dropin.name + ".new")
        current()
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            current()
            os.write(descriptor, b"[Service]\nLoadCredential=enrollment.key:/etc/happyranch/enrollment.key\n")
            current()
            os.fsync(descriptor)
            current()
        finally:
            os.close(descriptor)
        current()
        temporary.replace(dropin)
        current()
        if managed:
            # The managed declaration is produced by the approved package reader.
            # .new remains private; this fixed path-only final file is public.
            _check_managed_holder()
            declaration = _MANAGED_DECLARATION.get()
            if declaration is None:
                raise OSError("managed_stopped_context_invalid")
            dropin.chmod(declaration["dropin_mode"])
            current()
        current()
        _fsync_dir(dropin.parent)
        current()
        (reload_manager or _reload_systemd)()
        if managed:
            _check_managed_holder()


def _capture_bounded_query_output(
    process: subprocess.Popen[bytes], deadline: float
) -> tuple[bytes, bool]:
    """Read at most ``cap + 1`` bytes from an owned query under one deadline.

    Returns ``(retained_bytes, overflowed)``. The read never exceeds the
    retention limit and never blocks past ``deadline``; an over-cap producer is
    reported as overflow so the caller can stop and reap it. Normal kernel pipe
    buffering is allowed — the retained bytes are only an upper bound, not a
    claim about how many bytes the child produced.
    """
    if process.stdout is None:  # pragma: no cover - always created with PIPE
        raise OSError("service state unavailable")
    descriptor = process.stdout.fileno()
    retention_limit = _SERVICE_QUERY_MAX_OUTPUT_BYTES + 1
    captured = bytearray()
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise OSError("service state unavailable")
        try:
            readable, _, _ = select.select([descriptor], [], [], remaining)
        except (OSError, ValueError) as exc:
            raise OSError("service state unavailable") from exc
        if not readable:
            raise OSError("service state unavailable")
        allowance = retention_limit - len(captured)
        try:
            chunk = os.read(descriptor, min(_SERVICE_QUERY_READ_CHUNK_BYTES, allowance))
        except OSError as exc:
            raise OSError("service state unavailable") from exc
        if not chunk:
            return bytes(captured), False
        captured.extend(chunk)
        if len(captured) >= retention_limit:
            return bytes(captured), True


def _close_and_reap_query_process(process: subprocess.Popen[bytes], deadline: float, *, managed: bool = False) -> None:
    """Close the owned pipe and confirm the owned query child is reaped.

    Runs on every return path. The observation ``deadline`` continues to bound
    reading and exit observation only; it is never extended to restart reading
    or to authorize a refused observation. Cleanup gets one separate,
    explicitly bounded kill/reap allowance so that an already expired
    observation deadline can never leave the owned child unreaped: a child
    still alive after EOF, after cap overflow or after a read/exit timeout is
    killed and then awaited on that allowance. An owned child whose reaping
    cannot be confirmed within the allowance refuses category-only rather than
    being silently treated as reaped.
    """
    if process.stdout is not None:
        try:
            process.stdout.close()
        except OSError:
            pass
    if process.poll() is None:
        try:
            process.kill()
        except OSError:
            pass
    from runtime.remote_access.linux_package import _MANAGED_DEADLINE, _MANAGED_LOCK
    inherited = _MANAGED_DEADLINE.get()
    managed = managed or _MANAGED_LOCK.get() is not None
    reap_deadline = (min(deadline, inherited / 1_000_000_000 if inherited is not None else deadline) if managed
                     else max(deadline, time.monotonic()) + _SERVICE_QUERY_REAP_GRACE_SECONDS)
    try:
        process.wait(timeout=max(0, reap_deadline - time.monotonic()))
    except (subprocess.TimeoutExpired, OSError):
        pass
    if process.poll() is None:
        if managed:
            # Refused observation; retain the original lock through actual
            # child reap rather than unlocking over a running query/reload.
            process.wait()
        raise OSError("service state unavailable")


def _observe_sidecar_stopped() -> None:
    """Return only after a bounded affirmative observation that the sidecar is
    loaded and stopped (THR-228 seq275).

    Exactly one bounded ``systemctl show`` query requests the three named
    properties and requires a successful exit plus a strict, duplicate-free
    parse whose explicit state proves ``LoadState=loaded`` and
    ``ActiveState=inactive``/``SubState=dead`` — the normal loaded stopped
    state reached after the composite N3 stop. A nonzero query status, timeout
    (the child is killed and reaped), missing executable/manager/unit, unknown,
    missing/empty/duplicate/extra/malformed/oversize/contradictory record,
    unsupported non-LF record framing, or any active/transitional/failed state
    raises the existing category-only failure and never authorizes mutation.

    TASK8607 F2: the query is an owned child whose stdout is read incrementally
    under one absolute finite deadline shared by reading and exit observation.
    At most ``_SERVICE_QUERY_MAX_OUTPUT_BYTES + 1`` bytes are retained, an
    over-cap producer is stopped and reaped, and the owned child/pipe are closed
    on every path. An EOF that is followed by a child that stays alive is not
    treated as successful completion: the deadline still applies and the child
    is killed and reaped.

    TASK8623 F2: cleanup gets one separate, explicitly bounded kill/reap
    allowance, so an owned child is confirmed reaped before this returns even
    when the observation deadline has already expired. That allowance never
    restarts query reading and never authorizes a refused observation, and an
    unconfirmed reap refuses category-only instead of being silently ignored.

    TASK8644 R1 / TASK8662 R1: the one original absolute deadline also fences
    successful acceptance. A blocking exit observation that is collected after
    expiry (a resumed POSIX wait can collect an already-exited child and report
    success) and any scheduling delay before final acceptance refuse
    category-only, so the separate cleanup allowance can never authorize a late
    success. The same deadline is re-checked after the strict property
    validation succeeds and immediately before the affirmative return, so a
    scheduling pause inside the validator cannot authorize a late success
    either.
    """
    argv = [
        "systemctl",
        "show",
        "-p", "LoadState",
        "-p", "ActiveState",
        "-p", "SubState",
        _SIDECAR_UNIT,
    ]
    from runtime.remote_access.linux_package import _MANAGED_DEADLINE, _MANAGED_LOCK
    inherited = _MANAGED_DEADLINE.get()
    descriptor = _MANAGED_LOCK.get()
    deadline = min(time.monotonic() + _SERVICE_QUERY_TIMEOUT_SECONDS,
                   inherited / 1_000_000_000 if descriptor is not None and inherited is not None else float("inf"))
    if descriptor is not None:
        from runtime.remote_access.linux_package import _check_managed_holder
        _check_managed_holder()
        argv[0] = "/usr/bin/systemctl"
    if time.monotonic() >= deadline:
        raise OSError("service state unavailable")
    try:
        process = subprocess.Popen(
            argv,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            **({"env": {"PATH": "/usr/bin:/bin", "LANG": "C", "LC_ALL": "C"}} if descriptor is not None else {}),
            pass_fds=(_MANAGED_LOCK.get(),) if _MANAGED_LOCK.get() is not None else (),
        )
    except OSError as exc:
        raise OSError("service state unavailable") from exc
    raw = b""
    refusal: OSError | None = None
    try:
        raw, overflowed = _capture_bounded_query_output(process, deadline)
        if overflowed:
            refusal = OSError("service state unavailable")
        else:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                refusal = OSError("service state unavailable")
            else:
                try:
                    returncode = process.wait(timeout=remaining)
                except subprocess.TimeoutExpired:
                    refusal = OSError("service state unavailable")
                else:
                    if returncode != 0:
                        refusal = OSError("service state unavailable")
                    elif time.monotonic() >= deadline:
                        # TASK8644 R1: a resumed POSIX wait can collect an
                        # already-exited child and report success after the
                        # absolute observation deadline expired. A query that
                        # completed late is never affirmative evidence.
                        refusal = OSError("service state unavailable")
    finally:
        _close_and_reap_query_process(process, deadline)
    if refusal is not None:
        raise refusal
    if time.monotonic() >= deadline:
        # TASK8644 R1: the separate bounded kill/reap allowance may confirm an
        # owned child but must never authorize a late success. Re-check the one
        # original observation deadline before the parsed observation is
        # accepted, so a scheduling delay after observation cannot slip a
        # successful query past expiry.
        raise OSError("service state unavailable")
    _require_stopped_service_properties(raw)
    if time.monotonic() >= deadline:
        # TASK8662 R1: the strict property validation runs after the last
        # deadline check. Re-check the same one original observation deadline
        # after it succeeds and immediately before the affirmative return, so a
        # scheduling pause inside the validator cannot authorize a late success.
        raise OSError("service state unavailable")


def _require_stopped_service_properties(raw: bytes) -> None:
    """Strictly validate one named-property observation of a stopped unit.

    Raw record framing is validated before any normalization: only LF-delimited
    records are supported (with an optional single trailing LF). Every other
    ``str.splitlines()`` separator is refused outright rather than being
    normalized into a record boundary.
    """
    if len(raw) > _SERVICE_QUERY_MAX_OUTPUT_BYTES:
        raise OSError("service state unavailable")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise OSError("service state unavailable") from exc
    if any(
        separator in text for separator in _SERVICE_QUERY_UNSUPPORTED_RECORD_SEPARATORS
    ):
        raise OSError("service state unavailable")
    properties: dict[str, str] = {}
    records = text.split("\n")
    if records and records[-1] == "":
        records.pop()
    for line in records:
        key, separator, value = line.partition("=")
        if (
            not line
            or not separator
            or key not in _SERVICE_STATE_PROPERTIES
            or key in properties
            or not value
            or not value.isprintable()
            or any(character.isspace() for character in value)
        ):
            raise OSError("service state unavailable")
        properties[key] = value
    if set(properties) != set(_SERVICE_STATE_PROPERTIES):
        raise OSError("service state unavailable")
    if (
        properties["LoadState"] != "loaded"
        or properties["ActiveState"] != "inactive"
        or properties["SubState"] != "dead"
    ):
        raise OSError("service must be stopped")


def _reload_systemd() -> None:
    from runtime.remote_access.linux_package import _MANAGED_DEADLINE, _MANAGED_LOCK, _check_managed_holder
    env = {key: value for key, value in os.environ.items() if key != "NOTIFY_SOCKET"}
    descriptor, deadline = _MANAGED_LOCK.get(), _MANAGED_DEADLINE.get()
    if descriptor is None:
        subprocess.run(["systemctl", "daemon-reload"], check=True, env=env,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return
    _check_managed_holder()
    env = {"PATH": "/usr/bin:/bin", "LANG": "C", "LC_ALL": "C"}
    process = subprocess.Popen(["/usr/bin/systemctl", "daemon-reload"], env=env,
                               stdin=subprocess.DEVNULL,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                               pass_fds=(descriptor,))
    try:
        result = process.wait(timeout=max(0, (deadline - time.monotonic_ns()) / 1_000_000_000))
    except (OSError, subprocess.SubprocessError) as exc:
        raise OSError("service state unavailable") from exc
    finally:
        _close_and_reap_query_process(process, deadline / 1_000_000_000)
    _check_managed_holder()
    if result != 0:
        raise OSError("service state unavailable")


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
