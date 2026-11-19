"""Fixed system-Python CLI entry; shares the native host transaction owner."""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
from pathlib import Path
import select
import sys
import time

_spec = importlib.util.spec_from_file_location("connection_contract", Path(__file__).with_name("connection_contract.py"))
contract = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(contract)
METHODS = {"status": "Status", "devices": "Status", "next-device": "NextDevice",
           "approve-device": "ApprovePending", "reject-device": "RejectPending", "revoke-device": "RevokeSelected"}


class ClosedError(Exception):
    def __init__(self, code: str = "unavailable", effect: str = "none", recovery: str = "nativeRecovery") -> None:
        self.value = dict(code=code, effect=effect, recovery=recovery)


def desktop_available() -> bool:
    if not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
        return False
    try:
        import gi
        gi.require_version("Gtk", "3.0")
        from gi.repository import Gtk
        return bool(Gtk.init_check()[0])
    except Exception:
        return False


class Host:
    def __init__(self) -> None:
        from gi.repository import Gio, GLib
        self.Gio, self.GLib = Gio, GLib
        self.connection = None
        self.mutating = False
        self.cancellable = Gio.Cancellable()
        schema = Path(__file__).with_name("native-connection-bridge.v1.json")
        if not schema.is_file():
            schema = Path(__file__).parents[3] / "protocol/native-connection-bridge.v1.json"
        self.protocol = contract.Protocol(schema)

    def request(self, method: str) -> dict:
        loop, outcome = self.GLib.MainLoop(), []
        self.mutating = method in contract.HOST_MUTATIONS

        def finish(result: dict | None = None, code: str | None = None, effect: str = "none") -> None:
            outcome.append((result, code, effect))
            loop.quit()

        contract.request_host(self, method, finish, lambda: True, lambda _snapshot: None)
        if not outcome:
            loop.run()
        result, code, effect = outcome[0]
        self.mutating = False
        if code:
            raise ClosedError(code, effect, "nativeRecovery" if effect == "unknown" else
                              "nativePermission" if code == "permissionDenied" else "refresh")
        return result

    def close(self) -> None:
        self.cancellable.cancel()
        if self.connection is not None:
            self.connection.close(None, None, None)


def revoke(host: Host) -> dict:
    if not sys.stdin.isatty() or not sys.stderr.isatty():
        raise ClosedError(recovery="nativePermission")
    snapshot = host.request("Status")
    deadline = time.monotonic() + 120
    for _ in range(32):
        if snapshot["observation"] != "known" or not snapshot["devices"]:
            raise ClosedError()
        if snapshot["selected"] is None:
            snapshot = host.request("NextDevice")
        if snapshot["selected"] is None:
            raise ClosedError("stateChanged")
        print(json.dumps(snapshot, ensure_ascii=True), file=sys.stderr)
        print("revoke / next / cancel (default: cancel): ", end="", flush=True, file=sys.stderr)
        remaining = deadline - time.monotonic()
        if remaining <= 0 or not select.select([sys.stdin], [], [], remaining)[0]:
            return no_change()
        choice = sys.stdin.readline(16).strip()
        if time.monotonic() >= deadline:
            return no_change()
        if choice == "revoke":
            return host.request("RevokeSelected")
        if choice != "next":
            return no_change()
        snapshot = host.request("NextDevice")
    return no_change()


def no_change() -> dict:
    return dict(effect="none", hostDenial="notApplicable", hostCleanup="notApplicable",
                localCleanup="notApplicable", networkCleanup="notApplicable", inviteState="unknown",
                expiresAt=None, host=None, client=None)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=METHODS)
    args = parser.parse_args()
    method = METHODS[args.action]
    host = None
    try:
        if method in contract.HOST_MUTATIONS and not desktop_available():
            raise ClosedError(recovery="nativePermission")
        host = Host()
        result = revoke(host) if method == "RevokeSelected" else host.request(method)
        print(json.dumps(result, ensure_ascii=True))
        return 0
    except ClosedError as error:
        print(json.dumps(error.value))
        return 1
    except (Exception, KeyboardInterrupt):
        # No backend diagnostic, identity or private field may escape.
        if host is not None and host.mutating:
            print('{"code":"outcomeUnknown","effect":"unknown","recovery":"nativeRecovery"}')
        else:
            print('{"code":"unavailable","effect":"none","recovery":"nativeRecovery"}')
        return 1
    finally:
        if host is not None:
            try:
                host.close()
            except Exception:
                pass  # Teardown cannot rewrite the already reported effect.


if __name__ == "__main__":
    raise SystemExit(main())
