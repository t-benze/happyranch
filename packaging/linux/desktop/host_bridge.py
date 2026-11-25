"""Process-shared native Host transport, polling and sensitive invite display."""
from __future__ import annotations

from datetime import datetime, timezone
import importlib.util
import json
import math
from pathlib import Path
import time

_spec = importlib.util.spec_from_file_location("connection_contract", Path(__file__).with_name("connection_contract.py"))
_contract = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_contract)


class InviteSheet:
    """Native-memory-only invite display; never connected to web serialization."""

    def __init__(self, owner: object, text: str, expires_at: int, deadline: float) -> None:
        self.owner, self.text, self.expires_at = owner, text, expires_at
        self.closed, self.copied, self.source = False, False, None
        Gtk, tr = owner.Gtk, owner.tr
        self.dialog = Gtk.Dialog(title=tr("Invite a device", "邀请设备"),
                                 transient_for=owner.window, modal=True, destroy_with_parent=True)
        self.dialog.set_default_size(480, 240)
        content = self.dialog.get_content_area()
        content.set_spacing(12)
        content.set_border_width(16)
        self.label = Gtk.Label(label=text)
        # GTK selection exports PRIMARY and supplies an untracked Copy action.
        # Only the explicit Copy button may export invite text.
        self.label.set_selectable(False)
        self.label.set_can_focus(False)
        self.label.set_line_wrap(True)
        self.label.set_max_width_chars(64)
        scroll = Gtk.ScrolledWindow()
        scroll.set_min_content_height(96)
        scroll.add(self.label)
        content.pack_start(scroll, True, True, 0)
        explanation = Gtk.Label(label=tr(
            "The Linux owner will also need to approve the device. Closing does not cancel this invite. "
            "Clipboard history and other apps may retain copied text.",
            "Linux 机主还需要批准此设备。关闭不会取消此邀请。剪贴板历史及其他应用可能保留复制的文本。"))
        explanation.set_line_wrap(True)
        explanation.set_max_width_chars(64)
        content.pack_start(explanation, False, False, 0)
        self.expiry = Gtk.Label()
        content.pack_start(self.expiry, False, False, 0)
        self.copy = self.dialog.add_button(tr("Copy", "复制"), Gtk.ResponseType.APPLY)
        self.dialog.add_button(tr("Close", "关闭"), Gtk.ResponseType.CLOSE)
        self.dialog.set_default_response(Gtk.ResponseType.CLOSE)
        self.dialog.connect("response", self.response)
        self.dialog.connect("destroy", lambda *_args: self.close())
        self.clipboard = Gtk.Clipboard.get_default(owner.window.get_display())
        # Construction can itself take time. Recheck immediately before showing
        # any bytes, in addition to the shared terminal-frame validator.
        self.tick()
        if time.monotonic() >= deadline or time.time() >= expires_at:
            self.close()
            raise ValueError("late native invite")
        self.dialog.show_all()
        self.source = owner.GLib.timeout_add(250, self.tick)

    def clear_text(self) -> None:
        try:
            if self.copied and self.text and self.clipboard.wait_for_text() == self.text:
                self.clipboard.clear()
        except Exception:
            pass  # Best effort; never expose clipboard or backend error text.
        self.copied = False
        self.text = ""
        self.label.set_text("")
        self.copy.set_sensitive(False)

    def tick(self) -> bool:
        if self.closed:
            return False
        remaining = max(0, math.ceil(self.expires_at - time.time()))
        if not remaining:
            if self.source is not None:
                self.owner.GLib.source_remove(self.source)
            self.source = None
            self.clear_text()
            self.expiry.set_text(self.owner.tr("Invite expired", "邀请已过期"))
            return False
        absolute = datetime.fromtimestamp(self.expires_at).astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
        self.expiry.set_text(self.owner.tr("Expires at {time} ({left} remaining)",
                                          "到期时间：{time}（剩余 {left}）").format(
            time=absolute, left=f"{remaining // 60}:{remaining % 60:02d}"))
        return True

    def response(self, _dialog: object, response: int) -> None:
        if response == self.owner.Gtk.ResponseType.APPLY:
            if self.tick() and self.text:
                try:
                    self.clipboard.set_text(self.text, -1)
                    self.copied = True
                except Exception:
                    pass
        else:
            self.close()

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        if self.source is not None:
            self.owner.GLib.source_remove(self.source)
            self.source = None
        self.clear_text()
        if self.owner.invite_sheet is self:
            self.owner.invite_sheet = None
        self.dialog.destroy()


class HostBridge:
    """One dedicated bus and busy slot, shared by Main and Connection."""

    def __init__(self, client: object, prefix: Path, tr: object) -> None:
        import gi
        gi.require_version("Gtk", "3.0")
        from gi.repository import Gio, GLib, Gtk
        self.Gio, self.GLib, self.Gtk = Gio, GLib, Gtk
        self.client, self.tr = client, tr
        self.protocol = _contract.Protocol(prefix / "share/happyranch/desktop/native-connection-bridge.v1.json")
        self.connection = None
        self.cancellable = Gio.Cancellable()
        self.wait_source = None
        self.busy, self.host_status_waiters = False, None
        self.subscribers = {}
        self.host_timer, self.host_snapshot, self.host_sequence = None, None, 0
        self.host_epoch = 0
        self.invite_sheet = None
        self.invite_owner = None
        self.operation_owner = None
        self.cancel_request = None
        self.enable_observer = self.enable_owner = None
        self.enable_subscribers = {}

    def subscribe_enable(self, owner: object, *, current: object, emit: object) -> None:
        if owner in self.enable_subscribers:
            return
        self.enable_subscribers[owner] = dict(current=current, emit=emit)

    def unsubscribe_enable(self, owner: object) -> None:
        self.enable_subscribers.pop(owner, None)
        self.retire_enable(owner)

    def retire_enable(self, owner: object) -> None:
        if self.enable_owner is owner:
            if self.enable_observer is not None:
                self.enable_observer.close()
            self.enable_observer = self.enable_owner = None

    def subscribe(self, owner: object, *, current: object, visible: object, emit: object, window: object) -> None:
        if owner in self.subscribers:
            return
        self.subscribers[owner] = dict(current=current, visible=visible, emit=emit, window=window)
        if self.host_timer is None:
            self.host_timer = self.GLib.timeout_add_seconds(3, self.poll_host)
        # Every new subscriber receives a complete observation, even unchanged.
        if self.host_snapshot is not None or self.busy or not visible():
            self.GLib.idle_add(self.send_host_event, self.subscribers[owner],
                self.host_snapshot or self.protocol.host_unavailable(), self.host_epoch)
        if visible() and not self.busy:
            self.host_request("Status", lambda *_: None, owner=owner, current=current, window=window)

    def unsubscribe(self, owner: object) -> None:
        self.subscribers.pop(owner, None)
        if not self.subscribers and self.host_timer is not None:
            self.GLib.source_remove(self.host_timer)
            self.host_timer = None
    def detach(self, owner: object) -> None:
        self.unsubscribe_enable(owner)
        self.unsubscribe(owner)
        if self.operation_owner is owner and self.cancel_request is not None:
            self.cancellable.cancel()
            self.cancel_request()
            if self.connection is not None:
                self.connection.close(None, None, None)
                self.connection = None
        if self.invite_owner is owner and self.invite_sheet is not None:
            self.invite_sheet.close()
            self.invite_owner = None

    def poll_host(self) -> bool:
        for owner, subscriber in tuple(self.subscribers.items()):
            if not subscriber["current"]():
                self.unsubscribe(owner)
            elif not self.busy and subscriber["visible"]():
                self.host_request("Status", lambda *_: None, owner=owner,
                    current=subscriber["current"], window=subscriber["window"])
                break
        return bool(self.subscribers)

    def observe_host(self, snapshot: dict) -> None:
        self.host_snapshot = snapshot
        for subscriber in tuple(self.subscribers.values()):
            self.GLib.idle_add(self.send_host_event, subscriber, snapshot, self.host_epoch)

    def send_host_event(self, subscriber: dict, snapshot: dict, epoch: int) -> bool:
        if (epoch != self.host_epoch
                or not any(item is subscriber for item in self.subscribers.values()) or not subscriber["current"]()):
            return False
        self.host_sequence += 1
        data = dict(version=1, sequence=self.host_sequence,
            observedAt=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), snapshot=snapshot)
        self.protocol.validate(data, self.protocol.events["host.stateChanged"])
        raw = json.dumps(data, ensure_ascii=False)
        if len(raw.encode("utf-8")) <= 32768:
            subscriber["emit"](raw)
        return False

    def host_request(self, method: str, finish: object, *, owner: object,
                     current: object, window: object, terminal_current: object = None) -> None:
        if self.busy:
            if method == "Status" and self.host_status_waiters is not None and len(self.host_status_waiters) < 8:
                self.host_status_waiters.append(finish)
            else:
                finish(None, "busy")
            return
        mutation = method in _contract.HOST_MUTATIONS
        display = window.get_display()
        if mutation and (display is None or display.is_closed()):
            finish(None, "unavailable", "none", "nativePermission")
            return
        model = self.client.model
        if mutation and (self.client.prompt or model.confirming_target or model.host_prompt_document is not None):
            finish(None, "busy")
            return
        self.busy, self.operation_owner = True, owner
        operation = object()
        self.active_operation = operation
        self.cancellable = self.Gio.Cancellable()
        if mutation:
            model.host_prompt_document = owner
        waiters = [] if method == "Status" else None
        self.host_status_waiters = waiters
        observation_epoch = self.host_epoch

        def complete(*outcome: object) -> None:
            self.busy, self.operation_owner = False, None
            self.cancel_request = None
            self.active_operation = None
            if model.host_prompt_document is owner:
                model.host_prompt_document = None
            self.host_status_waiters = None
            finish(*outcome)
            if waiters is not None:
                for waiter in waiters:
                    waiter(*outcome)
                waiters.clear()

        self.cancel_request = lambda: complete(None, "outcomeUnknown", "unknown", "nativeRecovery")

        def show_invite(text: str, expires_at: int, deadline: float) -> None:
            if not current():
                raise ValueError("retired invite document")
            self.show_invite(text, expires_at, deadline, window)

        subscriber = self.enable_subscribers.get(owner) if method == "Enable" else None
        context_current = terminal_current or current
        if method == "Enable":
            if self.enable_observer is not None:
                self.enable_observer.close()
            self.enable_owner = owner
        def terminal_live() -> bool:
            return (context_current() and self.enable_owner is owner
                    and (subscriber is None or self.enable_subscribers.get(owner) is subscriber
                         and subscriber["current"]()))
        def terminal(raw: str) -> None:
            if terminal_live():
                self.host_epoch += 1  # Reads started before terminal knowledge cannot publish fresh state.
                self.host_snapshot = self.protocol.host_unavailable()
                subscriber["emit"](raw)
        def observe(snapshot: dict) -> None:
            if observation_epoch == self.host_epoch:
                self.observe_host(snapshot)
        _contract.request_host(self, method, complete,
                               lambda: self.active_operation is operation and current(), observe,
                               display_invite=show_invite,
                               terminal=terminal if subscriber is not None else None,
                               terminal_current=terminal_live if method == "Enable" else None)

    def show_invite(self, text: str, expires_at: int, deadline: float, window: object) -> None:
        if self.invite_sheet is not None:
            self.invite_sheet.close()
        self.invite_owner = self.operation_owner
        self.window = window
        self.invite_sheet = InviteSheet(self, text, expires_at, deadline)


def shared_bridge(client: object, prefix: Path, tr: object) -> HostBridge:
    # ClientUI/ClientModel already has process lifetime and is shared by both
    # views, including when installed sibling modules are loaded afresh.
    bridge = getattr(client.model, "host_bridge", None)
    if bridge is None:
        bridge = HostBridge(client, prefix, tr)
        client.model.host_bridge = bridge
    return bridge
