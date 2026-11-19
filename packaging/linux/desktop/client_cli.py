"""Linux foreground client. No process may control another owner's session."""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
from pathlib import Path
import select
import signal
import sys
import termios
import threading
import time

_spec = importlib.util.spec_from_file_location("client_model", Path(__file__).with_name("client_model.py"))
model = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(model)
native, custody = model._native, model._store
ACTIONS = ("status", "accept-invite", "saved-computers", "connect-saved", "remove-saved",
           "disconnect", "cancel-attempt", "retry", "retry-save", "health")
ERRORS = frozenset(("invalidInvite", "expiredInvite", "joinUnavailable", "runtimeUnavailable",
                   "authorizationIncomplete", "resumeUnavailable", "identityMismatch", "stateChanged",
                   "keychainLocked", "keychainDenied", "saveFailed", "unavailable", "removalIncomplete",
                   "revoked", "daemonUnavailable", "accessUnavailable", "stateUnavailable"))


class Parser(argparse.ArgumentParser):
    def error(self, _message: str) -> None:
        print('{"code":"invalidGrammar","effect":"none"}', file=sys.stderr)
        raise SystemExit(2)


class Cancelled(BaseException):
    pass


class Terminal:
    """All trust material stays on the controlling terminal, even with redirects."""

    def __init__(self) -> None:
        self.stream = os.fdopen(os.open("/dev/tty", os.O_RDWR | os.O_CLOEXEC),
                                "w", encoding="utf-8", buffering=1)
        if not self.stream.isatty():
            self.stream.close()
            raise native.ClientError("unavailable")

    def say(self, text: str) -> None:
        print(text, file=self.stream, flush=True)

    def read(self, prompt: str, *, hidden: bool = False, limit: int = 32) -> str:
        fd = self.stream.fileno()
        before = termios.tcgetattr(fd)
        after = list(before)
        # Noncanonical reads avoid stdio read-ahead and enforce a byte/time bound.
        after[3] &= ~termios.ICANON
        if hidden:
            after[3] &= ~(termios.ECHO | termios.ECHONL)
        deadline = time.monotonic() + 120
        data = bytearray()
        try:
            termios.tcsetattr(fd, termios.TCSAFLUSH, after)
            self.say(prompt)  # publish readiness only after hidden input is safe
            while len(data) <= limit:
                remaining = deadline - time.monotonic()
                if remaining <= 0 or not select.select([fd], [], [], remaining)[0]:
                    raise Cancelled()
                value = os.read(fd, 1)
                if value in (b"", b"\x04"):
                    raise Cancelled()
                if value in (b"\n", b"\r"):
                    return data.decode("utf-8")
                if value in (b"\x7f", b"\x08"):
                    del data[-1:]
                else:
                    data.extend(value)
            raise native.ClientError("invalidInvite" if hidden else "unavailable")
        finally:
            termios.tcsetattr(fd, termios.TCSAFLUSH, before)
            if hidden:
                self.say("")

    def close(self) -> None:
        self.stream.close()


class Client:
    def __init__(self, prefix: Path) -> None:
        self.prefix = prefix
        self.store = custody.SecretStore(native)
        self.session = None
        self.worker = None
        self.terminal = None
        self.saving = False
        self.saved = "none"
        self.removal = None
        self.code = None

    def work(self, operation: object) -> object:
        """Keep signal handling responsive during the existing bounded native IO."""
        result, finished = [], threading.Event()

        def run() -> None:
            try:
                result.append((operation(), None))
            except Exception as error:
                result.append((None, error))
            finally:
                finished.set()

        self.worker = threading.Thread(target=run, daemon=True)
        self.worker.start()
        while not finished.wait(0.1):
            pass
        value, error = result[0]
        if error:
            raise error
        return value

    def save(self) -> None:
        self.saving = True
        def write() -> None:
            try:
                self.store.save(self.session.saved_record())
                self.saved = "saved"
            except custody.StoreError as error:
                self.code = str(error) if str(error) in ERRORS else "saveFailed"
                self.saved = "needsPermission" if self.code in ("keychainLocked", "keychainDenied") else "notSaved"
        try:
            self.work(write)
            if self.saved != "saved":
                self.terminal.say("Access not saved. This connection is session-only; reopening may need a new invite.")
        finally:
            # On cancellation the worker still owns write/readback and its outcome.
            if self.worker is None or not self.worker.is_alive():
                self.saving = False

    def show_target(self, target: object) -> None:
        for label, value in (("Target", target.home_id), ("Control", target.control_plane_id),
                             ("Host", target.home_endpoint)):
            self.terminal.say(label + ": " + model.plain(value))

    def select_saved(self, action: str) -> object | None:
        records, state, observation = self.work(self.store.list)
        if not records or len(records) > 32:
            raise native.ClientError("unavailable")
        # Same generic closed labels as ClientModel.computers; identities only on tty.
        items = list(records.values())
        for i, record in enumerate(items):
            self.terminal.say(str(i + 1) + ": " + model.plain(record.home_id))
        choice = self.terminal.read("Select 1-" + str(len(items)) + " (default: cancel):")
        if not choice.isascii() or not choice.isdecimal() or not 1 <= int(choice) <= len(items):
            return None
        record = items[int(choice) - 1]
        verb = "remove" if action == "remove-saved" else "connect"
        self.show_target(record)
        if self.terminal.read("Type " + verb + " to confirm (default: cancel):") != verb:
            return None
        # Consent binds the exact current item; a changed record never substitutes.
        return self.work(lambda: self.store.load(record))

    def run(self, action: str) -> dict:
        if action == "status":
            return dict(state="notOwned", effect="none", observation="unavailable")
        if action in ("disconnect", "cancel-attempt", "retry", "retry-save", "health"):
            return dict(code="unavailable", state="notOwned", effect="none", recovery="owningProcess")
        if action == "saved-computers":
            records, state, observation = self.work(self.store.list)
            projection = model.ClientModel(self.prefix, None)
            projection.records, projection.saved_observation = records, observation
            return dict(computers=projection.computers(), savedAccess=state, observation=observation)
        self.terminal = Terminal()  # no stdin/env/file alternative, even when headless
        if action == "accept-invite":
            text = self.terminal.read("Paste invite (hidden):", hidden=True, limit=16384)
            invite = native.decode_invite(text)
            del text
            self.show_target(invite)
            if self.terminal.read("Type connect to consent (default: cancel):") != "connect":
                return dict(state="cancelled", effect="none")
            save = self.terminal.read("Save access in the login keyring? Type save (default: session-only):") == "save"
            self.saved = "notSaved" if save else "none"
            self.session = native.ClientSession(self.prefix)
            self.work(lambda: self.session.join(invite, save_intent=save))
            phrase, expiry = self.work(self.session.pair)
            self.terminal.say("Compare on Host: " + model.plain(phrase))
            self.terminal.say("Waiting for Host approval. Ctrl-C cancels this attempt.")
            deadline = time.monotonic() + max(0, min(300, expiry - time.time()))
            while True:
                if time.time() >= expiry or time.monotonic() >= deadline:
                    raise native.ClientError("expiredInvite")
                if self.work(self.session.complete):
                    break
                time.sleep(1)
            if save:
                self.save()
        else:
            record = self.select_saved(action)
            if record is None:
                return dict(state="cancelled", effect="none")
            if action == "remove-saved":
                self.removal = "unknown"
                def remove() -> None:
                    self.store.delete(record)  # includes exact-item absence readback
                    self.removal = "confirmed"
                self.work(remove)
                return dict(state="removed", effect="confirmed")
            self.saved = "saved"
            self.session = native.ClientSession(self.prefix)
            self.work(lambda: self.session.restore(record))
        url = self.work(self.session.open_runtime)  # authenticated health, never a UI-load claim
        self.terminal.say("Runtime reachable: " + url)
        self.terminal.say("Foreground connection; Ctrl-C or SIGTERM disconnects. Loaded RELEASE UI is unverified.")
        print(json.dumps(dict(state="runtimeReachable", effect="confirmed", savedAccess=self.saved,
                              code=self.code)), flush=True)
        while True:
            # A signal pending just before the syscall must not need a second
            # signal to wake Python's handler and start bounded cleanup.
            time.sleep(0.1)

    def close(self) -> bool:
        # Existing store deadline is 15s. Settle dispatched custody IO separately.
        if (self.saving or self.removal is not None) and self.worker:
            self.worker.join(16)
            if self.worker.is_alive():
                self.saved = "unknown"
                return False
        ok = True
        if self.session:
            stopped = threading.Event()

            def stop() -> None:
                nonlocal ok
                try:
                    self.session.stop()
                except Exception:
                    ok = False
                finally:
                    stopped.set()

            threading.Thread(target=stop, daemon=True).start()
            if not stopped.wait(8):
                ok = False
        if self.worker:
            self.worker.join(1)
            ok = ok and not self.worker.is_alive()
        if self.terminal:
            self.terminal.close()
        return ok


def prefix() -> Path:
    # Same relocatable payload layout as the native window; no user path input.
    return Path(__file__).resolve().parents[3]


def main() -> int:
    parser = Parser(description=__doc__)
    parser.add_argument("action", choices=ACTIONS)
    args = parser.parse_args()
    client = Client(prefix())
    previous = {}

    def cancel(_signal: int, _frame: object) -> None:
        raise Cancelled()

    for signum in (signal.SIGINT, signal.SIGTERM):
        previous[signum] = signal.signal(signum, cancel)
    try:
        try:
            result = client.run(args.action)
        except Cancelled:
            result = dict(state="cancelled", effect="none" if client.session is None else "confirmed")
        except Exception as error:
            code = str(error) if isinstance(error, (native.ClientError, custody.StoreError)) else "unavailable"
            result = dict(code=code if code in ERRORS else "unavailable", effect="unknown" if code == "removalIncomplete" or client.session is not None else "none")
        # A second signal must not interrupt bounded cleanup or replace its truth.
        for signum in previous:
            signal.signal(signum, signal.SIG_IGN)
        try:
            clean = client.close()
        except Exception:
            clean = False
        if client.removal is not None:
            result = dict(state="removed", effect="confirmed") if client.removal == "confirmed" else \
                dict(code="removalIncomplete", state="unknown", effect="unknown")
        elif not clean:
            result = dict(code="cleanupIncomplete", state="unknown", effect="unknown")
        if client.session is not None:
            result["savedAccess"] = client.saved
        print(json.dumps(result), flush=True)
        return 1 if result.get("code") else 0
    finally:
        for signum, handler in previous.items():
            signal.signal(signum, handler)


if __name__ == "__main__":
    raise SystemExit(main())
