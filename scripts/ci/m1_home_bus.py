"""M1's unprivileged fixed D-Bus caller; no production import or alternate path.

Use the same destination/path/interface/zero-argument signatures and interactive
flag as I5b. Keep this unique caller alive through lifecycle settlement. An
accepted admission is never reported as a successful lifecycle outcome.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import time

from gi.repository import Gio, GLib

NAME = 'ai.happyranch.RemoteAccess1'
PATH = '/ai/happyranch/RemoteAccess1'
BUDGETS = {'Enable': 480, 'ReplaceFailed': 480, 'Disable': 300, 'RetryPolicyRenewal': 120}


def main() -> None:
    assert os.getuid() >= 1000, 'M1 caller requires uid>=1000'
    assert os.environ.get('GITHUB_ACTIONS') == 'true', f'GITHUB_ACTIONS present={"GITHUB_ACTIONS" in os.environ}; equals true={os.environ.get("GITHUB_ACTIONS") == "true"}'
    assert os.environ.get('RUNNER_ENVIRONMENT') == 'github-hosted', f'RUNNER_ENVIRONMENT present={"RUNNER_ENVIRONMENT" in os.environ}; equals github-hosted={os.environ.get("RUNNER_ENVIRONMENT") == "github-hosted"}'
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('method', choices=BUDGETS)
    parser.add_argument('--interrupt', action='store_true')
    args = parser.parse_args()
    assert not args.interrupt or args.method == 'Enable', 'interruption fixture requires Enable'
    connection = Gio.DBusConnection.new_for_address_sync(
        Gio.dbus_address_get_for_bus_sync(Gio.BusType.SYSTEM, None),
        Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT | Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION,
        None, None)
    connection.set_exit_on_close(False)

    def bus(method: str, signature: str, values: tuple, reply: str) -> object:
        return connection.call_sync('org.freedesktop.DBus', '/org/freedesktop/DBus',
            'org.freedesktop.DBus', method, GLib.Variant(signature, values),
            GLib.VariantType.new(reply), Gio.DBusCallFlags.NONE, 3000, None).unpack()[0]

    observer = None
    try:
        if args.interrupt:
            observer = subprocess.Popen(['sudo', '--preserve-env=GITHUB_ACTIONS,RUNNER_ENVIRONMENT',
                '/opt/happyranch-desktop/python/bin/python3', '-I', '-B',
                str(Path(__file__).with_name('m1_home_checks.py')), 'interrupt', '0'],
                stdout=subprocess.PIPE, text=True)
            assert observer.stdout.readline().strip() == 'ARMED', 'interruption observer failed admission'
        owner = None
        for attempt in range(2):
            deadline = time.monotonic() + BUDGETS[args.method] + 10
            try:
                # Read the actual reply sender, avoiding a race with I5e's name
                # release after a quickly refused renewal. This is the CLI's
                # exact zero-input method and interactive-authorization flag.
                message = Gio.DBusMessage.new_method_call(NAME, PATH, NAME, args.method)
                message.set_flags(Gio.DBusMessageFlags.ALLOW_INTERACTIVE_AUTHORIZATION)
                reply, _ = connection.send_message_with_reply_sync(
                    message, Gio.DBusSendMessageFlags.NONE, 122000, None)
                if reply.get_message_type() == Gio.DBusMessageType.ERROR:
                    remote = reply.get_error_name() or ''
                    raise RuntimeError(remote)
                assert reply.get_signature() == 's', 'invalid public reply signature'
                raw = reply.get_body().unpack()[0]
                assert json.loads(raw) == 'accepted', 'invalid public admission'
                owner = reply.get_sender()
                break
            except (GLib.Error, RuntimeError) as error:
                # I5e permits one honestly recorded race refusal. Never retry
                # accepted/unknown work or other errors on a predicted outcome.
                remote = str(error) if isinstance(error, RuntimeError) else Gio.DBusError.get_remote_error(error) or ''
                print(json.dumps({'method': args.method, 'admission': 'refused',
                                  'category': 'unavailable' if remote.endswith('.unavailable') else 'other',
                                  'attempt': attempt + 1}), flush=True)
                if attempt or not remote.endswith('.unavailable'):
                    raise AssertionError('public lifecycle admission refused') from None
                # Wait for the refused instance to release its bus name; this
                # is not an idle-delay workaround for successful Enable.
                end = time.monotonic() + 5
                while bus('NameHasOwner', '(s)', (NAME,), '(b)') and time.monotonic() < end:
                    time.sleep(.05)
                assert not bus('NameHasOwner', '(s)', (NAME,), '(b)'), 'refused helper did not release its bus name'
        assert owner, 'public reply omitted helper identity'
        pid = bus('GetConnectionUnixProcessID', '(s)', (owner,), '(u)')
        marker = Path(os.environ['M1_BUILD']) / 'previous-helper-owner'
        start = Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()[19]
        identity = {'owner': owner, 'pid': pid, 'start': start}
        if marker.exists():
            previous = json.loads(marker.read_text())
            assert previous['owner'] != owner and (previous['pid'], previous['start']) != (pid, start), \
                'lifecycle reused a helper namespace'
        marker.write_text(json.dumps(identity))
        print(json.dumps({'method': args.method, 'admission': 'accepted', 'helper_pid': pid,
                          'helper_owner': owner, 'outcome': 'not-yet-observed'}), flush=True)
        # The connection is deliberately held. Its unique name is part of the
        # helper's live-caller revalidation during every effect.
        while bus('NameHasOwner', '(s)', (owner,), '(b)'):
            assert time.monotonic() < deadline, 'helper did not settle/release within original budget'
            time.sleep(.05)
        if observer is not None:
            remainder, _ = observer.communicate(timeout=5)
            print(remainder, end='')
            assert observer.returncode == 0, 'interruption missed the required between-step boundary'
        print(json.dumps({'method': args.method, 'helper_released': True,
                          'outcome': 'requires-independent-state-check'}), flush=True)
    finally:
        if observer is not None and observer.poll() is None:
            observer.terminate()
            observer.wait(timeout=5)
        connection.close_sync(None)


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print('M1 public caller failed:', type(error).__name__,
              str(error) if isinstance(error, AssertionError) else '')
        raise SystemExit(1) from None
