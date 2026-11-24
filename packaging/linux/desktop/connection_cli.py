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
           "approve-device": "ApprovePending", "reject-device": "RejectPending", "revoke-device": "RevokeSelected",
           "enable": "Enable", "turn-off": "Disable", "create-invite": "IssueInvite",
           "cancel-invite": "CancelInvite", "replace-failed": "ReplaceFailed",
           "recover-invite": "RecoverInvite", "retry-policy-renewal": "RetryPolicyRenewal"}
MUTATIONS = contract.HOST_MUTATIONS | contract.CLI_LIFECYCLE_TIMEOUTS.keys()


class ClosedError(Exception):
    def __init__(self, code: str = "unavailable", effect: str = "none", recovery: str = "nativeRecovery",
                 category: str | None = None) -> None:
        self.value = dict(code=code, effect=effect, recovery=recovery)
        if code == "hostStateUnrecognized":
            self.value['category'] = category if category in contract.HOST_STATE_CATEGORIES else 'unknown'


class Terminal:
    """A terminal sink is admitted before any secret-producing dispatch."""

    def __init__(self) -> None:
        try:
            self.stream = os.fdopen(os.open("/dev/tty", os.O_RDWR | os.O_CLOEXEC),
                                    "w", encoding="utf-8", buffering=1)
            if not self.stream.isatty():
                self.stream.close()
                raise OSError()
            self.input_fd = self.stream.fileno()
        except OSError:
            raise ClosedError(recovery="nativePermission") from None

    def say(self, text: str) -> None:
        print(text, file=self.stream, flush=True)

    def confirm_replace(self) -> bool:
        self.say("Replace failed remote access; existing pairings will be lost")
        print("Continue? [y/N] ", end="", file=self.stream, flush=True)
        if not select.select([self.input_fd], [], [], 120)[0]:
            return False
        return os.read(self.input_fd, 16) in (b"y\n", b"yes\n")

    def close(self) -> None:
        self.stream.close()


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
        self.terminal = None
        self.wait_source = None
        self.cancellable = Gio.Cancellable()
        schema = Path(__file__).with_name("native-connection-bridge.v1.json")
        if not schema.is_file():
            schema = Path(__file__).parents[3] / "protocol/native-connection-bridge.v1.json"
        self.protocol = contract.Protocol(schema)

    def request(self, method: str) -> dict:
        if self.connection is not None and self.connection.is_closed():
            raise ClosedError()  # Never move a process-local selection to a new bus.
        loop, outcome = self.GLib.MainLoop(), []
        self.mutating = method in MUTATIONS

        def finish(result: dict | None = None, code: str | dict | None = None, effect: str = "none") -> None:
            outcome.append((result, code, effect))
            loop.quit()

        contract.request_host(self, method, finish, lambda: True, lambda _snapshot: None,
                              allow_reconnect=False, cli_lifecycle=True,
                              display_invite=(lambda invite, _expiry, _deadline: self.terminal.say(invite))
                              if self.terminal is not None else None)
        if not outcome:
            loop.run()
        result, code, effect = outcome[0]
        self.mutating = False
        if code:
            category = code.get('category') if type(code) is dict else None
            code = code['code'] if type(code) is dict else code
            raise ClosedError(code, effect, "nativeRecovery" if effect == "unknown" else
                              "nativePermission" if code == "permissionDenied" else "refresh", category)
        return result

    def close(self) -> None:
        if self.wait_source is not None:
            self.GLib.source_remove(self.wait_source)
            self.wait_source = None
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
    host = terminal = None
    try:
        if method in MUTATIONS and not desktop_available():
            raise ClosedError(recovery="nativePermission")
        if method in ("IssueInvite", "ReplaceFailed"):
            terminal = Terminal()
            if method == "ReplaceFailed" and not terminal.confirm_replace():
                print(json.dumps(no_change()))
                return 0
        host = Host()
        host.terminal = terminal
        result = revoke(host) if method == "RevokeSelected" else host.request(method)
        print(json.dumps(result, ensure_ascii=True))
        return 0
    except ClosedError as error:
        if error.value["code"] == "hostStateUnrecognized":
            print("Remote access files or the release runtime owner could not be verified. "
                  "Check the local installation. Category: " + error.value['category'] + ".", file=sys.stderr)
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
        if terminal is not None:
            try:
                terminal.close()
            except Exception:
                pass


if __name__ == "__main__":
    raise SystemExit(main())
