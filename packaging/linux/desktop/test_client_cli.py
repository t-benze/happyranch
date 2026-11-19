"""Foreground CLI boundary; synthetic native session, real custody owner/fake GI."""
from __future__ import annotations

import argparse
from contextlib import redirect_stderr, redirect_stdout
import io
import json
import os
from pathlib import Path
import pty
import select
import signal
import sys
import termios
import threading
import time
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

import client_cli as cli
from test_client_store import FakeSecret, Item, RemoteError, record
from test_connection_cli import command


class Terminal:
    choices = []
    output = []
    reads = []

    def say(self, text: str) -> None:
        self.output.append(text)

    def read(self, prompt: str, **kwargs: object) -> str:
        self.reads.append((prompt, kwargs))
        return self.choices.pop(0)

    def close(self) -> None:
        pass


class Session:
    instances = []
    cleanup_error = False
    runtime_error = False
    join_cancel = False
    calls = []

    def __init__(self, prefix: Path) -> None:
        self.instances.append(self)

    def join(self, invite: object, *, save_intent: bool) -> None:
        self.calls.append(("join", save_intent))
        if self.join_cancel:
            os.kill(os.getpid(), signal.SIGTERM)
            time.sleep(0.1)

    def pair(self) -> tuple:
        self.calls.append("pair")
        return "synthetic-comparison", time.time() + 60

    def complete(self) -> bool:
        self.calls.append("complete")
        return True

    def restore(self, saved: object) -> None:
        self.calls.append(("restore", cli.custody.encode(saved)))

    def saved_record(self) -> object:
        return record()

    def open_runtime(self) -> str:
        self.calls.append("open")
        if self.runtime_error:
            raise cli.native.ClientError("accessUnavailable")
        return "http://127.0.0.1:12345/"

    def stop(self) -> None:
        self.calls.append("stop")
        if self.cleanup_error:
            raise cli.native.ClientError("cleanupIncomplete")


class ClientCLITests(unittest.TestCase):
    def setUp(self) -> None:
        self.secret = FakeSecret()
        Session.instances, Session.calls = [], []
        Session.cleanup_error = Session.runtime_error = Session.join_cancel = False
        Terminal.choices, Terminal.output, Terminal.reads = [], [], []
        from gi.repository import Gio
        for target, name, value in ((cli.native, "ClientSession", Session), (cli, "Terminal", Terminal),
                (cli.native, "decode_invite", lambda text: NS(home_id="synthetic-home", control_plane_id="synthetic-control", home_endpoint="100.64.0.1:443")),
                (Gio.DBusError, "get_remote_error", lambda e: e.remote if isinstance(e, RemoteError) else None)):
            active = patch.object(target, name, value)
            active.start()
            self.addCleanup(active.stop)
        active = patch.dict(sys.modules, {"gi.repository.Secret": self.secret})
        active.start()
        self.addCleanup(active.stop)

    def run_cli(self, action: str, choices: list | None = None) -> tuple:
        Terminal.choices = list(choices or [])
        out = io.StringIO()
        def interrupt() -> None:
            os.kill(os.getpid(), signal.SIGINT)
        with patch.object(sys, "argv", ["client_cli.py", action]), redirect_stdout(out), \
                patch.object(cli.signal, "pause", side_effect=interrupt):
            code = cli.main()
        for secret in ("synthetic-invite-secret", "synthetic-comparison", "synthetic-home",
                       "synthetic-control", "100.64.0.1", "synthetic-private-canary", "b" * 64, "fingerprint", "127.0.0.1", "secret diagnostic canary"):
            self.assertNotIn(secret, out.getvalue())
        return code, [json.loads(line) for line in out.getvalue().splitlines()]

    def test_registration_exec_and_deferred_mac(self) -> None:
        parser = argparse.ArgumentParser()
        command.register(parser.add_subparsers())
        for action in cli.ACTIONS:
            with self.subTest(action=action), patch.object(command.sys, "platform", "linux"), \
                    patch.object(command.os, "execv", side_effect=SystemExit(0)) as execute:
                with self.assertRaises(SystemExit):
                    command.cmd_connection(parser.parse_args(["connection", "client", action]))
                self.assertEqual(execute.call_args.args, ("/usr/bin/python3", ["/usr/bin/python3", "-I",
                    str(Path(__file__).with_name("client_cli.py")), action]))
            with patch.object(command.sys, "platform", "darwin"), redirect_stdout(io.StringIO()), \
                    patch.object(command.os, "execv") as execute, self.assertRaises(SystemExit) as stopped:
                command.cmd_connection(parser.parse_args(["connection", "client", action]))
            self.assertEqual(stopped.exception.code, 1)
            execute.assert_not_called()
        for flag in ("--invite", "--device-id", "--path", "--bearer"):
            error = io.StringIO()
            with redirect_stderr(error), self.assertRaises(SystemExit) as stopped:
                parser.parse_args(["connection", "client", "accept-invite", flag, "synthetic"])
            self.assertEqual(stopped.exception.code, 2)
            self.assertEqual(error.getvalue(), '{"code":"invalidGrammar","effect":"none"}\n')
            with patch.object(sys, "argv", ["client_cli.py", "accept-invite", flag, "synthetic"]), \
                    redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as native_stopped:
                cli.main()
            self.assertEqual(native_stopped.exception.code, 2)

    def test_not_owned_actions_never_touch_other_sessions(self) -> None:
        for action in ("status", "disconnect", "cancel-attempt", "retry", "retry-save", "health"):
            with self.subTest(action=action), patch.object(cli, "Terminal") as terminal, \
                    patch.object(cli.custody.SecretStore, "list") as discover:
                code, rows = self.run_cli(action)
                self.assertEqual(code, 0 if action == "status" else 1)
                self.assertEqual((rows[0]["state"], rows[0]["effect"]), ("notOwned", "none"))
                terminal.assert_not_called()
                discover.assert_not_called()
        self.assertEqual(Session.calls, [])

    def test_hidden_invite_consent_runtime_and_signal_cleanup(self) -> None:
        code, rows = self.run_cli("accept-invite", ["synthetic-invite-secret", "connect", "save"])
        self.assertEqual(code, 0)
        self.assertEqual(rows, [dict(state="runtimeReachable", effect="confirmed", savedAccess="saved", code=None),
                                dict(state="cancelled", effect="confirmed", savedAccess="saved")])
        self.assertEqual(Terminal.reads[0][1], dict(hidden=True, limit=16384))
        self.assertIn("Target: synthetic-home", Terminal.output)
        self.assertIn("Compare on Host: synthetic-comparison", Terminal.output)
        self.assertEqual(Session.calls, [("join", True), "pair", "complete", "open", "stop"])
        self.assertEqual(len(self.secret.writes), 1)
        self.assertNotIn("synthetic-invite-secret", str(Terminal.output))
        self.assertNotIn("connected", json.dumps(rows).lower())

    def test_cancel_headless_and_invalid_invite_do_not_launch(self) -> None:
        self.assertEqual(self.run_cli("accept-invite", ["synthetic-invite-secret", "", ""]),
                         (0, [dict(state="cancelled", effect="none")]))
        with patch.object(cli.native, "decode_invite", side_effect=cli.native.ClientError("invalidInvite")):
            self.assertEqual(self.run_cli("accept-invite", ["synthetic-invite-secret"])[1][0]["code"], "invalidInvite")
        with patch.object(cli, "Terminal", side_effect=OSError("secret diagnostic canary")):
            self.assertEqual(self.run_cli("accept-invite")[0], 1)
        self.assertEqual(Session.instances, [])

    def test_saved_selection_revalidation_remove_and_closed_list(self) -> None:
        store = cli.custody.SecretStore(cli.native)
        store.save(record())
        rows = self.run_cli("saved-computers")[1]
        self.assertEqual(rows[0]["computers"], [dict(computerRef=cli.custody.reference(record()), label="Saved computer 1")])
        self.assertEqual(self.run_cli("connect-saved", ["1", "connect"])[0], 0)
        self.assertEqual(Session.calls[0], ("restore", cli.custody.encode(record())))
        self.assertEqual(Session.calls[-2:], ["open", "stop"])
        self.assertEqual(self.run_cli("remove-saved", ["1", ""])[1], [dict(state="cancelled", effect="none")])
        self.assertEqual(len(self.secret.items), 1)
        for choice in ("0", "33", "", "invalid", "１"):
            with self.subTest(choice=choice):
                self.assertEqual(self.run_cli("remove-saved", [choice])[1], [dict(state="cancelled", effect="none")])
                self.assertEqual(len(self.secret.items), 1)
        original_read = Terminal.read
        def swap(terminal: Terminal, prompt: str, **kwargs: object) -> str:
            answer = original_read(terminal, prompt, **kwargs)
            if answer == "remove":
                store.save(record("different-node"))
                self.secret.items.pop(0)
            return answer
        with patch.object(Terminal, "read", swap):
            self.assertEqual(self.run_cli("remove-saved", ["1", "remove"])[0], 1)
        self.assertEqual(len(self.secret.items), 1)
        self.assertEqual(self.run_cli("remove-saved", ["1", "remove"]), (0, [dict(state="removed", effect="confirmed")]))
        self.assertEqual(self.secret.items, [])
        self.assertEqual(self.run_cli("connect-saved", ["33"])[0], 1)
        original_delete = Item.delete_sync
        for signum in (signal.SIGINT, signal.SIGTERM):
            for outcome in ("confirmed", "failed", "pending"):
                with self.subTest(signal=signum, outcome=outcome):
                    store.save(record())
                    entered, release = threading.Event(), threading.Event()
                    workers = []
                    def interrupted_delete(item: object, cancel: object) -> bool:
                        workers.append(threading.current_thread())
                        entered.set()
                        os.kill(os.getpid(), signum)
                        if outcome == "pending":
                            release.wait(2)
                        else:
                            time.sleep(0.05)
                        removed = original_delete(item, cancel)
                        if outcome == "failed":
                            raise RuntimeError("secret diagnostic canary")
                        return removed
                    original_join = threading.Thread.join
                    def bounded_join(worker: object, timeout: float | None = None) -> None:
                        original_join(worker, 0.01 if outcome == "pending" else timeout)
                    try:
                        with patch.object(Item, "delete_sync", interrupted_delete), \
                                patch.object(threading.Thread, "join", bounded_join):
                            result = self.run_cli("remove-saved", ["1", "remove"])
                        self.assertTrue(entered.is_set())
                        expected = (0, [dict(state="removed", effect="confirmed")]) if outcome == "confirmed" else \
                            (1, [dict(code="removalIncomplete", state="unknown", effect="unknown")])
                        self.assertEqual(bool(self.secret.items), outcome == "pending")
                        self.assertEqual(result, expected)
                    finally:
                        release.set()
                        for worker in workers:
                            original_join(worker, 2)
                            self.assertFalse(worker.is_alive())

    def test_save_failures_and_incomplete_cleanup_are_truthful(self) -> None:
        for mode, expected in (("locked", "needsPermission"), ("denied", "needsPermission"),
                               ("unavailable", "notSaved"), ("bad-readback", "notSaved")):
            with self.subTest(mode=mode):
                self.secret.mode = mode
                code, rows = self.run_cli("accept-invite", ["synthetic-invite-secret", "connect", "save"])
                self.assertEqual(code, 0)
                self.assertEqual((rows[0]["state"], rows[0]["savedAccess"]), ("runtimeReachable", expected))
                self.assertIn("Access not saved.", " ".join(Terminal.output))
        self.secret.mode = "ok"
        original_create = self.secret.create
        def interrupted_save(*args: object) -> object:
            os.kill(os.getpid(), signal.SIGTERM)
            time.sleep(0.05)
            item = original_create(*args)
            Session.calls.append("write")
            return item
        with patch.object(self.secret.Item, "create_sync", interrupted_save):
            code, rows = self.run_cli("accept-invite", ["synthetic-invite-secret", "connect", "save"])
        self.assertEqual((code, rows), (0, [dict(state="cancelled", effect="confirmed", savedAccess="saved")]))
        self.assertEqual(Session.calls[-2:], ["write", "stop"])
        self.assertNotIn("open", Session.calls[-2:])
        Session.cleanup_error = True
        self.assertEqual(self.run_cli("accept-invite", ["synthetic-invite-secret", "connect", ""]),
                         (1, [dict(state="runtimeReachable", effect="confirmed", savedAccess="none", code=None),
                              dict(code="cleanupIncomplete", state="unknown", effect="unknown", savedAccess="none")]))

    def test_sigterm_during_join_and_failed_runtime_never_claim_connected(self) -> None:
        Session.join_cancel = True
        self.assertEqual(self.run_cli("accept-invite", ["synthetic-invite-secret", "connect", ""])[0], 0)
        self.assertIn("stop", Session.calls)
        self.assertNotIn("open", Session.calls)
        Session.join_cancel = False
        Session.runtime_error = True
        code, rows = self.run_cli("accept-invite", ["synthetic-invite-secret", "connect", ""])
        self.assertEqual(code, 1)
        self.assertEqual(rows, [dict(code="accessUnavailable", effect="unknown", savedAccess="none")])
        self.assertEqual(Session.calls[-1], "stop")


class TerminalTests(unittest.TestCase):
    def test_real_terminal_hidden_bounded_read_and_restoration(self) -> None:
        master, slave = pty.openpty()
        terminal = cli.Terminal.__new__(cli.Terminal)
        terminal.stream = os.fdopen(os.dup(slave), "w", buffering=1)
        before = termios.tcgetattr(slave)
        values = []
        errors = []
        release = threading.Event()
        original_say = terminal.say
        def paused_prompt(text: str) -> None:
            original_say(text)
            if text == "Hidden:":
                release.wait(2)  # peer pastes at readiness, even if reader is descheduled
        terminal.say = paused_prompt
        def read() -> None:
            try:
                values.append(terminal.read("Hidden:", hidden=True, limit=64))
            except BaseException as error:
                errors.append(error)
        worker = threading.Thread(target=read)
        worker.start()
        try:
            output = os.read(master, 1024)
            self.assertIn(b"Hidden:", output)
            os.write(master, b"synthetic-hidden\n")
            deadline = time.monotonic() + 0.1
            while time.monotonic() < deadline and select.select([master], [], [], max(0, deadline - time.monotonic()))[0]:
                output += os.read(master, 1024)
            self.assertNotIn(b"synthetic-hidden", output)
            self.assertFalse(termios.tcgetattr(slave)[3] & (termios.ECHO | termios.ECHONL))
            release.set()
            worker.join(2)
            self.assertFalse(worker.is_alive())
            self.assertEqual(values, ["synthetic-hidden"])
            self.assertEqual(errors, [])
            self.assertEqual(termios.tcgetattr(slave), before)
            output += os.read(master, 1024)
            self.assertNotIn(b"synthetic-hidden", output)
            def cancel(_signum: int, _frame: object) -> None:
                raise cli.Cancelled()
            previous = {s: signal.signal(s, cancel) for s in (signal.SIGINT, signal.SIGTERM)}
            try:
                for mode in ("eof", "oversized", "prompt-error", signal.SIGINT, signal.SIGTERM):
                    with self.subTest(exit=mode):
                        def end_read(text: str) -> None:
                            original_say(text)
                            if text != "Hidden:":
                                return
                            self.assertFalse(termios.tcgetattr(slave)[3] & (termios.ECHO | termios.ECHONL))
                            if mode == "prompt-error":
                                raise OSError("synthetic prompt error")
                            if isinstance(mode, int):
                                os.kill(os.getpid(), mode)
                            else:
                                os.write(master, b"\x04" if mode == "eof" else b"x" * 65)
                        expected = OSError if mode == "prompt-error" else \
                            cli.native.ClientError if mode == "oversized" else cli.Cancelled
                        with patch.object(terminal, "say", end_read), self.assertRaises(expected):
                            terminal.read("Hidden:", hidden=True, limit=64)
                        self.assertEqual(termios.tcgetattr(slave), before)
            finally:
                for signum, handler in previous.items():
                    signal.signal(signum, handler)
        finally:
            release.set()
            worker.join(0.1)
            if worker.is_alive():
                os.write(master, b"\x04")
                worker.join(2)
            terminal.close()
            os.close(master)
            os.close(slave)


if __name__ == "__main__":
    unittest.main(verbosity=2)
