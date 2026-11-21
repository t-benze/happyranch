"""Native sensitive dialogs for the Main Connection owner."""
from __future__ import annotations

import hashlib
import importlib.util
from pathlib import Path
import threading
import time

_spec = importlib.util.spec_from_file_location("client_model", Path(__file__).with_name("client_model.py"))
_model = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_model)


class ClientUI:
    def __init__(self, app: object, parent: object, prefix: Path, tr: object) -> None:
        import gi
        gi.require_version("Gtk", "3.0")
        from gi.repository import GLib, Gtk
        self.GLib, self.Gtk = GLib, Gtk
        self.app, self.parent, self.tr = app, parent, tr
        self.model = _model.ClientModel(prefix, self)
        self.prompt = self.wait = None
        self.listener = None
        self.document = None
        self.prompt_cancel = None

    def idle(self, callback: object) -> None:
        self.GLib.idle_add(callback)

    def later(self, milliseconds: int, callback: object) -> None:
        self.GLib.timeout_add(milliseconds, callback)

    def attach_main(self, main: object, bridge_types: object) -> None:
        self.model.attach_main(main, bridge_types)

    def changed(self) -> None:
        if self.listener:
            self.listener()

    def dialog(self, title: str, text: str, accept: str, callback: object) -> object:
        Gtk = self.Gtk
        dialog = Gtk.Dialog(title=title, transient_for=self.parent, modal=True)
        dialog.set_default_size(520, -1)
        dialog.add_button(self.tr("Cancel", "取消"), Gtk.ResponseType.CANCEL)
        dialog.add_button(accept, Gtk.ResponseType.OK)
        dialog.set_default_response(Gtk.ResponseType.CANCEL)
        label = Gtk.Label(label=text)
        label.set_line_wrap(True)
        label.set_max_width_chars(64)
        label.set_margin_start(18)
        label.set_margin_end(18)
        label.set_margin_top(18)
        label.set_margin_bottom(18)
        dialog.get_content_area().add(label)
        self.prompt = dialog
        self.prompt_cancel = lambda: dialog.response(Gtk.ResponseType.CANCEL)
        def response(_dialog: object, value: int) -> None:
            if self.prompt != dialog:
                return
            self.prompt = self.prompt_cancel = None
            callback(value == Gtk.ResponseType.OK)
            dialog.destroy()
        dialog.connect("response", response)
        dialog.show_all()
        return dialog

    def paste(self, callback: object) -> None:
        entry = self.Gtk.Entry()
        entry.set_visibility(False)
        entry.set_max_length(16384)
        entry.set_placeholder_text(self.tr("Paste invite here", "在此粘贴邀请"))
        def response(yes: bool) -> None:
            text = entry.get_text()
            entry.set_text("")
            callback(text if yes and len(text.encode("utf-8")) <= 16384 else None)
        dialog = self.dialog(self.tr("Accept invite", "接受邀请"),
            self.tr("Paste an invite from the Host. It stays in this native dialog.",
                    "粘贴主机发来的邀请。邀请内容仅在此原生对话框中处理。"),
            self.tr("Review computer", "核对电脑"), response)
        dialog.get_content_area().add(entry)
        entry.show()
        entry.grab_focus()

    def target(self, invite: object, callback: object, *, fresh: bool = False) -> None:
        fields = [_model.plain(value) for value in
                  (invite.home_id, invite.control_plane_id, invite.home_endpoint)]
        fingerprint = hashlib.sha256(invite.ca_der).hexdigest()
        save = self.Gtk.CheckButton(label=self.tr(
            "Save access in this computer's login keyring for reconnecting",
            "将访问权限保存在此电脑的登录密钥环中，以便重新连接"))
        dialog = self.dialog(self.tr("Confirm computer", "确认电脑"),
            (self.tr("Saved access could not be verified. Connecting requires fresh Host approval.",
                     "无法验证已保存的访问权限。连接需要主机重新批准。") + "\n\n" if fresh else "")
            + self.tr("Connect this device to this exact Host? Verify its identity with the Host owner.",
                    "允许此设备连接到这台主机吗？请与主机所有者核对其身份。")
            + "\n\n" + "\n".join(fields) + "\nSHA-256: " + fingerprint,
            self.tr("Connect this device", "连接此设备"), lambda yes: callback(yes, save.get_active()))
        dialog.get_content_area().add(save)
        save.show()

    def recovery_target(self, invite: object, callback: object) -> None:
        fields = [_model.plain(value, 1024) for value in (invite.home_id, invite.control_plane_id,
                  invite.control_url, invite.dial_ip, invite.home_endpoint)]
        self.dialog(self.tr("Confirm updated address", "确认更新后的地址"),
            self.tr("Use saved access at this updated target? The invitation will not be redeemed. "
                    "The same device and live Host approval must still be verified before saving.",
                    "使用已保存的访问权限连接更新后的目标吗？不会兑换此邀请。保存前仍须验证同一设备及有效的主机批准。")
            + "\n\n" + "\n".join(fields) + "\nSHA-256: " + hashlib.sha256(invite.ca_der).hexdigest(),
            self.tr("Confirm updated target", "确认更新后的目标"), callback)

    def saved_target(self, record: object, remove: bool, callback: object) -> None:
        fields = [_model.plain(value) for value in
                  (record.home_id, record.control_plane_id, record.home_endpoint)]
        text = self.tr(
            "Remove saved access from this computer only? This does not revoke Host approval or remove network membership.",
            "仅从此电脑移除已保存的访问权限？此操作不会撤销主机批准或移除网络成员资格。") if remove else self.tr(
            "Reconnect to this exact Host using saved access?", "使用已保存的访问权限重新连接到这台主机？")
        self.dialog(self.tr("Saved computer", "已保存的电脑"),
            text + "\n\n" + "\n".join(fields) + "\nSHA-256: " + record.fingerprint,
            self.tr("Remove local access", "移除本地访问权限") if remove else self.tr("Connect", "连接"), callback)

    def storage_wait(self) -> None:
        dialog = self.Gtk.Dialog(title=self.tr("Stopping", "正在停止"), transient_for=self.parent)
        label = self.Gtk.Label(label=self.tr(
            "Waiting for keyring write and verification to finish safely. Saved access is retained.",
            "正在等待密钥环写入和验证安全完成。已保存的访问权限将保留。"))
        dialog.get_content_area().add(label)
        self.prompt = dialog
        dialog.show_all()

    def waiting(self, phrase: str, expiry: float, target: str) -> None:
        self.dismiss_waiting()
        dialog = self.Gtk.Dialog(title=self.tr("Waiting for Host approval", "等待主机批准"),
                                 transient_for=self.parent)
        dialog.add_button(self.tr("Cancel attempt", "取消尝试"), self.Gtk.ResponseType.CANCEL)
        label = self.Gtk.Label()
        label.set_margin_start(18)
        label.set_margin_end(18)
        label.set_margin_top(18)
        label.set_margin_bottom(18)
        dialog.get_content_area().add(label)
        self.wait = dialog
        def tick() -> bool:
            if self.wait != dialog:
                return False
            label.set_text(target + "\n\n" + phrase + "\n\n" + self.tr(
                "Compare this phrase on the Host and approve this device. Seconds remaining: ",
                "请在主机上核对短语并批准此设备。剩余秒数：") + str(max(0, int(min(
                    expiry - time.time(), self.model.deadline - time.monotonic())))))
            return True
        tick()
        self.GLib.timeout_add_seconds(1, tick)
        dialog.connect("response", lambda *_: self.model.stop())
        dialog.connect("delete-event", lambda *_: (self.model.stop(), True)[1])
        dialog.show_all()

    def dismiss_waiting(self) -> None:
        dialog, self.wait = self.wait, None
        if dialog:
            dialog.destroy()

    def dismiss(self) -> None:
        dialog, self.prompt, self.prompt_cancel = self.prompt, None, None
        if dialog:
            dialog.destroy()
        self.dismiss_waiting()

    def confirm_stop(self, target: str, callback: object) -> None:
        if self.prompt:
            # The existing target/paste sheet owns its Cancel. No second prompt.
            self.prompt.present()
            self.model.hold = False
            callback(False)
            return
        if self.wait:
            self.wait.set_sensitive(False)
        def response(yes: bool) -> None:
            if self.wait:
                self.wait.set_sensitive(True)
            callback(yes)
        self.dialog(self.tr("Stop local connection?", "停止本地连接？"),
            target + "\n\n" + self.tr(
                "This stops this device's connection. It does not revoke Host approval or remove network membership.",
                "这会停止此设备的连接，但不会撤销主机批准或移除网络成员资格。"),
            self.tr("Stop connection", "停止连接"), response)

    def error(self, code: str) -> None:
        messages = {
            "invalidInvite": ("Invite is invalid or expired.", "邀请无效或已过期。"),
            "expired": ("Approval deadline passed. Ask the Host for a new invite.", "批准期限已过。请向主机索取新邀请。"),
            "newInviteRequired": ("Approval could not be completed. Ask the Host for a new invite.", "无法完成批准。请向主机索取新邀请。"),
            "networkUnavailable": ("Connection failed. A new invite is required.", "连接失败。需要新的邀请。"),
            "runtimeUnavailable": ("The runtime interface is unavailable. Check Main and the connection before retrying.", "运行界面不可用。请检查主界面和连接后重试。"),
            "keychainLocked": ("Unlock this computer's login keyring, then retry.", "请解锁此电脑的登录密钥环，然后重试。"),
            "keychainDenied": ("Keyring access was denied. Check native permissions before retrying.", "密钥环访问被拒绝。请检查原生权限后重试。"),
            "savedUnavailable": ("Saved reconnect is unavailable. Check the connection and saved access before retrying.", "无法使用已保存的访问权限重新连接。请检查连接和保存的权限后重试。"),
            "identityChanged": ("The saved device identity changed. Ask the Host for a new invite and approval.", "已保存的设备身份发生变化。请向主机索取新邀请并重新批准。"),
        }
        message = messages.get(code, ("Local cleanup is incomplete. Reopen controls before retrying.", "本地清理未完成。请重新打开控制页后再试。"))
        dialog = self.Gtk.MessageDialog(transient_for=self.parent, message_type=self.Gtk.MessageType.WARNING,
            buttons=self.Gtk.ButtonsType.CLOSE, text=self.tr(*message))
        # An informational notice has no queued continuation or sensitive text.
        self.prompt = dialog
        def closed(*_args: object) -> None:
            if self.prompt == dialog:
                self.prompt = None
            dialog.destroy()
        dialog.connect("response", closed)
        dialog.show_all()

    def shutdown(self) -> None:
        self.model.token += 1
        sessions = [session for session in (self.model.session, self.model.active_session) if session is not None]
        self.model.session = self.model.active_session = None
        self.model.candidate_stop.set()
        self.dismiss()
        deadline = time.monotonic() + 20
        # Do not tear down the credential owner during write/readback.
        if self.model.saving:
            with self.model.worker_lock:
                writers = list(self.model.workers)
            for writer in writers:
                writer.join(max(0, deadline - time.monotonic()))
        for session in sessions:
            def stop(session: object = session) -> None:
                try:
                    session.stop()
                except Exception:
                    pass  # Process exit cannot certify cleanup; no secret exception logging.
            worker = threading.Thread(target=stop, daemon=True)
            worker.start()
            worker.join(max(0, deadline - time.monotonic()))
        with self.model.worker_lock:
            workers = list(self.model.workers)
        for worker in workers:
            worker.join(max(0, deadline - time.monotonic()))
