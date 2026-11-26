"""Packaged resource manifests and closed, dependency-free v1 validation."""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import stat
import time
import uuid
from typing import Any

MAX_ASSET = 64 * 1024 * 1024
CLIENT_COMMANDS = {"client.status", "client.acceptInvite", "client.cancelAttempt",
                   "client.disconnect", "client.retry", "client.health", "client.savedComputers",
                   "client.connectSaved", "client.retrySave", "client.removeSaved"}

HOST_METHODS = {"host.status": "Status", "host.nextDevice": "NextDevice",
                "host.approveDevice": "ApprovePending", "host.rejectDevice": "RejectPending",
                "host.revokeDevice": "RevokeSelected", "host.enable": "Enable",
                "host.turnOff": "Disable", "host.createInvite": "IssueInvite"}
HOST_MUTATIONS = frozenset({"ApprovePending", "RejectPending", "RevokeSelected",
                            "Enable", "Disable", "IssueInvite"})
# Only these three lifecycle methods have v1 window commands. The other four
# remain CLI opt-in only; all consumers share the same original helper budgets.
WINDOW_LIFECYCLE = frozenset({"Enable", "Disable", "IssueInvite"})
CLI_LIFECYCLE_TIMEOUTS = {"Enable": 482000, "ReplaceFailed": 482000, "Disable": 302000,
                          "IssueInvite": 122000, "CancelInvite": 122000,
                          "RecoverInvite": 122000, "RetryPolicyRenewal": 122000}
HOST_STATE_CATEGORIES = frozenset({"residue", "owner", "provenance", "custody", "inventory",
                                    "quiescence", "timeout", "status_unavailable", "unknown", "unverified", "not_applicable"})
PREPARATION_CATEGORIES = frozenset({
    "deb-layout-required", "issuer-state-uncertain", "account-shape",
    "account-receipt-uncertain", "foreign-unit", "foreign-custody", "publication-uncertain",
    "address-changed", "account-create-uncertain", "cleanup-uncertain", "materializer-uncertain",
    "custody-uncertain", "inventory", "no_address", "multiple_addresses", "dial_ip", "dial-ip",
    "occupied-port", "port-check-uncertain",
    "ready-restart-failed-turn-off", "activation-retry-later", "activation-prepared-retry", "activation-unverified",
})
HELPER_NAME = "ai.happyranch.RemoteAccess1"
HELPER_PATH = "/ai/happyranch/RemoteAccess1"
DISABLE_CATEGORIES = frozenset({'partial_cleanup', 'failed', 'uncertain'})
CLI_COMPLETION_CODES = {'ReplaceFailed': 'replaceFailedCompleted', 'CancelInvite': 'cancelInviteCompleted',
                        'RecoverInvite': 'recoverInviteCompleted', 'RetryPolicyRenewal': 'policyRenewalCompleted'}
CLI_FAILURE_CATEGORIES = frozenset({'failed', 'refused', 'partial_cleanup', 'uncertain'})


def enable_terminal_frame(raw: str) -> dict:
    value = parse_json(raw, 512)
    if (type(value) is not dict or set(value) not in (
            {"method", "correlation", "outcome"}, {"method", "correlation", "outcome", "category"})
            or value["method"] not in ("Enable", "Disable", *CLI_COMPLETION_CODES)
            or value["outcome"] not in ("failed", "completed")):
        raise ValueError("invalid terminal frame")
    correlation = value["correlation"]
    if (type(correlation) is not dict or set(correlation) != {"serial", "owner"}
            or type(correlation["serial"]) is not int or not 1 <= correlation["serial"] <= 4294967295
            or type(correlation["owner"]) is not str or len(correlation["owner"]) > 255
            or re.fullmatch(r":[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)+", correlation["owner"]) is None):
        raise ValueError("invalid terminal correlation")
    if "category" in value and (value["outcome"] != "failed"
            or type(value["category"]) is not str or value["category"] not in (
                PREPARATION_CATEGORIES if value['method'] == 'Enable' else DISABLE_CATEGORIES
                if value['method'] == 'Disable' else CLI_FAILURE_CATEGORIES)):
        raise ValueError("invalid terminal category")
    return value


class EnableTerminalObserver:
    """One original connection/request/incarnation; never a result store."""

    def __init__(self, host: object, complete: object, failed: object, current: object,
                 terminal: object, *, cli: bool, admission_current: object, method: str = 'Enable') -> None:
        if method != 'Enable' and not (cli and method in ('Disable', *CLI_COMPLETION_CODES)):
            raise ValueError('invalid terminal method')
        self.method = method
        self.host, self.connection = host, host.connection
        self.complete, self.failed, self.current, self.terminal = complete, failed, current, terminal
        self.cli = cli
        self.admission_current = admission_current
        self.admission_finished = False
        self.operation_id = uuid.uuid4().hex
        self.closed = self.consumed = self.admitted = False
        self.owner = self.arrival_owner = None
        self.pending = None
        self.subscription = self.owners = self.filter_id = self.timer = self.closed_handler = None
        self.started = time.monotonic()
        budget = CLI_LIFECYCLE_TIMEOUTS[method] / 1000 - 2
        self.cutoff, self.deadline = self.started + budget, self.started + budget + 2
        self.message = host.Gio.DBusMessage.new_method_call(HELPER_NAME, HELPER_PATH, HELPER_NAME, method)
        self.message.set_flags(host.Gio.DBusMessageFlags.ALLOW_INTERACTIVE_AUTHORIZATION)

    def start(self) -> None:
        host = self.host
        try:
            # Install both match and envelope receiver BEFORE the one send.
            self.subscription = self.connection.signal_subscribe(HELPER_NAME, HELPER_NAME,
                "LifecycleCompleted", HELPER_PATH, None, host.Gio.DBusSignalFlags.NONE,
                lambda *_args: None, None)
            self.owners = self.connection.signal_subscribe("org.freedesktop.DBus", "org.freedesktop.DBus",
                "NameOwnerChanged", "/org/freedesktop/DBus", HELPER_NAME,
                host.Gio.DBusSignalFlags.NONE, lambda *_args: None, None)
            self.filter_id = self.connection.add_filter(self._filter, None)
            self.closed_handler = self.connection.connect("closed", self._lost)
            self.timer = host.GLib.timeout_add(max(1, int((self.deadline - time.monotonic()) * 1000)), self._expired)
            self.connection.send_message_with_reply(self.message, host.Gio.DBusSendMessageFlags.NONE,
                122000, host.cancellable, self._admission, None)
        except Exception as error:
            self.close()
            self.failed(error, bool(self.message.get_serial()))

    def _filter(self, connection: object, message: object, incoming: bool, data: object) -> object:
        # Gio invokes filters on its message thread. Retain the outgoing message
        # itself: its serial is assigned before transmission, even before send
        # returns. All observer state changes occur on the main context.
        if not incoming or self.closed:
            return message
        gio = self.host.Gio
        if (message.get_message_type() == gio.DBusMessageType.METHOD_RETURN
                and message.get_reply_serial() == self.message.get_serial()):
            self.host.GLib.idle_add(self._reply_arrived, message)
        elif message.get_message_type() == gio.DBusMessageType.SIGNAL:
            if (message.get_path() == HELPER_PATH and message.get_interface() == HELPER_NAME
                    and message.get_member() == "LifecycleCompleted"
                    and message.get_destination() == connection.get_unique_name()
                    and message.get_signature() == "s"):
                try:
                    fds = message.get_unix_fd_list()
                    if fds is not None and fds.get_length():
                        return message
                    raw, = message.get_body().unpack()
                    frame = enable_terminal_frame(raw)
                    if frame['method'] == self.method and frame["correlation"]["serial"] == self.message.get_serial():
                        self.host.GLib.idle_add(self._arrived, message.get_sender(), frame)
                except (ValueError, TypeError, UnicodeError, AttributeError):
                    pass
            elif (message.get_sender() == "org.freedesktop.DBus"
                    and message.get_path() == "/org/freedesktop/DBus"
                    and message.get_interface() == "org.freedesktop.DBus"
                    and message.get_member() == "NameOwnerChanged" and message.get_signature() == "sss"):
                self.host.GLib.idle_add(self._owner_changed, message.get_body().unpack())
        return message

    def _reply_arrived(self, reply: object) -> bool:
        if not self.closed:
            try:
                self.arrival_owner = self._admission_owner(reply)
            except (ValueError, TypeError, UnicodeError, AttributeError):
                pass
        return False

    def _admission_owner(self, reply: object) -> str:
        fds = reply.get_unix_fd_list()
        owner = reply.get_sender()
        if (reply.get_message_type() != self.host.Gio.DBusMessageType.METHOD_RETURN
                or reply.get_reply_serial() != self.message.get_serial()
                or reply.get_destination() != self.connection.get_unique_name()
                or reply.get_signature() != "s" or fds is not None and fds.get_length()
                or type(owner) is not str or len(owner) > 255
                or re.fullmatch(r":[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)+", owner) is None
                or parse_json(reply.get_body().unpack()[0], 256) != "accepted"):
            raise ValueError("invalid Enable admission")
        return owner

    def _admission(self, connection: object, result: object, data: object) -> None:
        if self.admission_finished or not self.admission_current():
            self.close()
            return
        try:
            reply = connection.send_message_with_reply_finish(result)
            if reply.get_message_type() == self.host.Gio.DBusMessageType.ERROR:
                raise self.host.Gio.DBusError.new_for_dbus_error(reply.get_error_name(), "Enable refused")
            self.owner = self._admission_owner(reply)
            if time.monotonic() >= self.cutoff:
                raise ValueError("late Enable admission")
            self.admitted = True
            self.admission_finished = True
            if not self.cli:
                projection = dict(effect="unknown", hostDenial="unknown", hostCleanup="unknown",
                    localCleanup="notApplicable", networkCleanup="unknown", inviteState="unknown",
                    expiresAt=None, host=None, client=None)
                if not self.closed and self._current() and self.terminal is not None:
                    projection["operationId"] = self.operation_id
                self.complete(self.host.protocol.result("host.enable", projection))
            if (self.closed or self.terminal is None) and not self.cli:
                self.close()
                return
            if self.pending is not None:
                sender, frame = self.pending
                self.pending = None
                self._arrived(sender, frame)
        except Exception as error:
            self.close()
            self.failed(error, True)

    def _arrived(self, sender: str, frame: dict) -> bool:
        if self.closed or self.consumed:
            return False
        if not self._current():
            self.close()
            return False
        if time.monotonic() >= self.cutoff:
            return False
        owner = self.owner if self.admitted else self.arrival_owner
        if (frame['method'] != self.method or sender != owner or frame["correlation"]["owner"] != owner
                or frame["correlation"]["serial"] != self.message.get_serial()):
            return False
        if not self.admitted:
            if self.pending is None:
                self.pending = (sender, frame)
            return False
        if self.method == 'Disable':
            projection = (dict(code='turnOffCompleted', effect='unknown', recovery='refresh')
                if frame['outcome'] == 'completed' else dict(code='turnOffFailed',
                    category=frame.get('category', 'uncertain'), effect='unknown', recovery='nativeRecovery'))
            raw = None  # Disable CLI has no host.enableFinished event.
        elif self.method in CLI_COMPLETION_CODES:
            category = frame.get('category', 'uncertain')
            projection = (dict(code=CLI_COMPLETION_CODES[self.method], effect='unknown', recovery='refresh')
                if frame['outcome'] == 'completed' else
                dict(code='outcomeUnknown', effect='unknown', recovery='nativeRecovery') if category == 'uncertain'
                else dict(code='lifecycleFailed', category=category, effect='unknown', recovery='nativeRecovery'))
        elif frame["outcome"] == "completed":
            projection = dict(code="enableCompleted", effect="unknown", recovery="refresh")
            event = dict(version=1, operationId=self.operation_id, method="Enable", outcome="completed", result=projection)
        else:
            projection = dict(code="outcomeUnknown", effect="unknown", recovery="nativeRecovery")
            if "category" in frame:
                projection.update(code="enablePreparationFailed", category=frame["category"])
            event = dict(version=1, operationId=self.operation_id, method="Enable", outcome="failed", error=projection)
        if self.method == 'Enable':
            self.host.protocol.validate(event, self.host.protocol.events["host.enableFinished"])
            raw = json.dumps(event, separators=(",", ":"))
            parse_json(raw, 1024)
        if not self._current():
            self.close()
            return False
        if time.monotonic() >= self.cutoff:
            return False
        self.consumed = True  # Latch BEFORE callbacks/teardown.
        self.close()
        if self.cli:
            if frame["outcome"] == "completed":
                self.complete(projection)
            else:
                self.complete(None, projection, "unknown")
        else:
            self.terminal(raw)
        return False

    def _owner_changed(self, values: tuple) -> bool:
        if not self.closed and values[0] == HELPER_NAME and values[1] == (self.owner or self.arrival_owner):
            # A pre-release received candidate may await its admission callback.
            # Newly arriving traffic cannot take over that original tuple.
            if self.pending is None:
                self._lost()
            else:
                if self.filter_id is not None:
                    self.connection.remove_filter(self.filter_id)
                    self.filter_id = None
        return False

    def _lost(self, *_args: object) -> None:
        if self.closed:
            return
        notify = (self.cli or not self.admitted) and self._current() and not self.consumed
        self.close()
        if notify:
            self.admission_finished = True
            self.complete(None, "outcomeUnknown", "unknown")

    def _expired(self) -> bool:
        self.timer = None
        self._lost()
        return False

    def _current(self) -> bool:
        return getattr(self.host, "enable_observer", None) is self and self.current()

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        self.pending = None
        for handle in (self.subscription, self.owners):
            if handle is not None:
                self.connection.signal_unsubscribe(handle)
        if self.filter_id is not None:
            self.connection.remove_filter(self.filter_id)
        if self.timer is not None:
            self.host.GLib.source_remove(self.timer)
        if self.closed_handler is not None:
            self.connection.disconnect(self.closed_handler)
        self.subscription = self.owners = self.filter_id = self.timer = self.closed_handler = None


class Requests:
    """Per-document bounded outcomes; cancellation has its own attempt record."""
    def __init__(self) -> None:
        self.mutations = {}
        self.cancel = None
        self.reads = 0
        self.mutating = False

    def admit(self, request: dict, attempt: int, finish: object) -> object:
        command, key = request["command"], request["requestId"]
        read = command in ("bridge.hello", "host.status", "client.status", "client.health", "client.savedComputers")
        body = json.dumps(request, sort_keys=True)
        record = self.mutations.get(key)
        if record is None and self.cancel and self.cancel["key"] == key:
            record = self.cancel
        if record:
            if record["body"] != body:
                finish(None, "invalidRequest")
            elif record["outcome"] is not None:
                finish(*record["outcome"])
            elif len(record["waiters"]) < 8:
                record["waiters"].append(finish)
            else:
                finish(None, "busy")
            return None
        if read:
            if self.reads >= 8:
                finish(None, "busy")
                return None
            self.reads += 1
            def done(*args: object) -> None:
                self.reads -= 1
                finish(*args)
            return done
        cancel = command == "client.cancelAttempt"
        if cancel and self.cancel and self.cancel["attempt"] == attempt:
            outcome = self.cancel["outcome"]
            finish(*(outcome if outcome is not None else (None, "busy")))
            return None
        if not cancel and (self.mutating or len(self.mutations) >= 64):
            finish(None, "busy")
            return None
        record = dict(key=key, body=body, attempt=attempt, outcome=None, waiters=[])
        if cancel:
            self.cancel = record
        else:
            self.mutations[key] = record
            self.mutating = True
        def done(*args: object) -> None:
            if record["outcome"] is not None:
                return
            record["outcome"] = args
            if not cancel:
                self.mutating = False
            finish(*args)
            for waiter in record["waiters"]:
                waiter(*args)
            record["waiters"].clear()
        return done


def parse_json(raw: str, limit: int) -> Any:
    if type(raw) is not str or len(raw.encode("utf-8")) > limit:
        raise ValueError("invalid string size")

    def pairs(items: list[tuple[str, Any]]) -> dict:
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate key")
            result[key] = value
        return result

    def constant(_value: str) -> None:
        raise ValueError("invalid constant")

    value = json.loads(raw, object_pairs_hook=pairs, parse_constant=constant)

    def walk(item: Any, depth: int) -> None:
        if depth > 16:
            raise ValueError("JSON depth")
        if isinstance(item, str):
            item.encode("utf-8", errors="strict")
        elif isinstance(item, dict):
            for key, child in item.items():
                walk(key, depth + 1)
                walk(child, depth + 1)
        elif isinstance(item, list):
            for child in item:
                walk(child, depth + 1)
        elif isinstance(item, float):
            raise ValueError("noninteger number")

    walk(value, 0)
    return value


def literal_path(value: str) -> list[str]:
    if (type(value) is not str or len(value) > 256
            or re.fullmatch(r"[A-Za-z0-9_./-]+", value) is None
            or any(part in ("", ".", "..") for part in value.split("/"))):
        raise ValueError("nonliteral resource path")
    return value.split("/")


class Assets:
    """No-follow descriptor walks; hash again at delivery, not just startup."""

    def __init__(self, root: Path) -> None:
        self.root = root.absolute()
        if self.root.resolve(strict=True) != self.root:
            raise ValueError("aliased asset root")
        manifest = parse_json(self.read("resource-manifest.json", 1024 * 1024).decode("utf-8"),
                              1024 * 1024)
        if (type(manifest) is not dict or set(manifest) != {"version", "entry", "resources"}
                or type(manifest["version"]) is not int or manifest["version"] != 1
                or manifest["entry"] != "index.html" or type(manifest["resources"]) is not list
                or not 1 <= len(manifest["resources"]) <= 4096):
            raise ValueError("invalid resource manifest")
        self.resources = {}
        total = 0
        for item in manifest["resources"]:
            if type(item) is not dict or set(item) != {"path", "bytes", "sha256"}:
                raise ValueError("invalid resource record")
            name = item["path"]
            literal_path(name)
            if (name == "resource-manifest.json" or name in self.resources
                    or type(item["bytes"]) is not int or not 0 <= item["bytes"] <= MAX_ASSET
                    or type(item["sha256"]) is not str
                    or re.fullmatch("[0-9a-f]{64}", item["sha256"]) is None):
                raise ValueError("invalid resource record")
            total += item["bytes"]
            self.resources[name] = item
        if "index.html" not in self.resources or total > 256 * 1024 * 1024:
            raise ValueError("invalid resource budget")
        actual = set()
        entries = 0
        for parent, directories, files in os.walk(self.root, followlinks=False):
            for name in directories + files:
                entries += 1
                if entries > 8192:
                    raise ValueError("resource inventory limit")
                path = Path(parent) / name
                mode = path.lstat().st_mode
                if not (stat.S_ISREG(mode) or stat.S_ISDIR(mode)):
                    raise ValueError("nonregular resource")
                if stat.S_ISREG(mode):
                    actual.add(path.relative_to(self.root).as_posix())
        if actual != {*self.resources, "resource-manifest.json"}:
            raise ValueError("resource inventory mismatch")
        for name in self.resources:
            self.asset(name)

    def read(self, name: str, limit: int) -> bytes:
        parts = literal_path(name)
        # Re-admit the literal root too; no ambient web-dist or cwd discovery.
        if self.root.resolve(strict=True) != self.root:
            raise ValueError("aliased asset root")
        directory = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            for part in parts[:-1]:
                child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
                os.close(directory)
                directory = child
            descriptor = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                                 dir_fd=directory)
            with os.fdopen(descriptor, "rb") as stream:
                before = os.fstat(stream.fileno())
                if not stat.S_ISREG(before.st_mode) or before.st_size > limit:
                    raise ValueError("invalid resource file")
                data = stream.read(limit + 1)
                after = os.fstat(stream.fileno())
                if (len(data) != before.st_size or len(data) > limit
                        or (before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns)
                        != (after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns)):
                    raise ValueError("resource changed")
                return data
        finally:
            os.close(directory)

    def asset(self, name: str) -> bytes:
        record = self.resources.get(name)
        if record is None:
            raise ValueError("unknown resource")
        data = self.read(name, record["bytes"])
        if len(data) != record["bytes"] or hashlib.sha256(data).hexdigest() != record["sha256"]:
            raise ValueError("resource digest mismatch")
        return data



class Protocol:
    def __init__(self, path: Path) -> None:
        self.schema = parse_json(path.read_text(encoding="utf-8"), 128 * 1024)
        self.commands = self.schema["commands"]
        self.events = self.schema["events"]

    def validate(self, value: Any, schema: dict) -> None:
        if "$ref" in schema:
            self.validate(value, self.schema["$defs"][schema["$ref"].split("/")[-1]])
            return
        if "anyOf" in schema:
            for candidate in schema["anyOf"]:
                try:
                    self.validate(value, candidate)
                    break
                except ValueError:
                    continue
            else:
                raise ValueError("invalid alternative")
        if "const" in schema and (type(value) is not type(schema["const"]) or value != schema["const"]):
            raise ValueError("invalid constant")
        if "enum" in schema and value not in schema["enum"]:
            raise ValueError("invalid enum")
        kind = schema.get("type")
        if kind == "object":
            if type(value) is not dict or not set(schema.get("required", ())) <= set(value):
                raise ValueError("invalid object")
            properties = schema.get("properties", {})
            if schema.get("additionalProperties") is False and not set(value) <= set(properties):
                raise ValueError("extra properties")
            for key, child in value.items():
                if key in properties:
                    self.validate(child, properties[key])
        elif kind == "array":
            if type(value) is not list or len(value) > schema.get("maxItems", 256):
                raise ValueError("invalid array")
            if schema.get("uniqueItems") and len({json.dumps(v, sort_keys=True) for v in value}) != len(value):
                raise ValueError("duplicate array item")
            for child in value:
                self.validate(child, schema["items"])
        elif kind == "string":
            if (type(value) is not str or len(value) < schema.get("minLength", 0)
                    or len(value.encode("utf-8")) > schema.get("maxUtf8Bytes", 32768)):
                raise ValueError("invalid string")
            pattern = schema.get("pattern")
            if pattern and re.search(pattern, value) is None:
                raise ValueError("invalid string pattern")
        elif kind == "integer":
            if (type(value) is not int or value < schema.get("minimum", -9007199254740991)
                    or value > schema.get("maximum", 9007199254740991)):
                raise ValueError("invalid integer")
        elif kind == "null" and value is not None:
            raise ValueError("invalid null")
        for child in schema.get("allOf", ()):
            self.validate(value, child)

    def result(self, command: str, value: dict) -> dict:
        self.validate(value, self.commands[command]["result"])
        parse_json(json.dumps(value, ensure_ascii=False), 32768)
        return value

    def hello(self) -> dict:
        return self.result("bridge.hello", {
            "version": 1, "platform": "linux", "roles": ["host", "client"],
            "capabilities": [{"command": name, "availability": (
                "available" if name in CLIENT_COMMANDS | HOST_METHODS.keys() else "unavailable")}
                for name in self.commands if name != "bridge.hello"],
            "observationLimits": {"host": [],
                                  "client": ["route", "comparisonPhrase"]}})

    def host(self, raw: str) -> dict:
        value = parse_json(raw, 32768)
        if (type(value) is not dict or set(value) != {"phase", "ready", "devices", "pending", "selected"}
                or value["phase"] not in ("off", "pending", "ready", "paused", "failed")
                or type(value["ready"]) is not bool or type(value["devices"]) is not list
                or len(value["devices"]) > 32):
            raise ValueError("invalid helper snapshot")

        def device(item: Any) -> dict | None:
            if item is None:
                return None
            if (type(item) is not dict or set(item) != {"label", "fingerprint", "comparison_phrase"}
                    or any(type(v) is not str or len(v) > 576 for v in item.values())
                    or re.fullmatch("[0-9a-f]{24}", item["fingerprint"]) is None):
                raise ValueError("invalid helper device")
            # Helper text is HTML-escaped. Decode into bounded plain text only.
            import html
            def plain(text: str) -> str:
                decoded = html.unescape(text)
                encoded = decoded.encode("utf-8")
                return decoded if len(encoded) <= 256 else encoded[:253].decode("utf-8", errors="ignore") + "…"
            return {"label": plain(item["label"]),
                    "comparisonPhrase": plain(item["comparison_phrase"]) or None}

        devices = [device(item) for item in value["devices"]]
        if None in devices or value["selected"] is not None and value["selected"] not in value["devices"]:
            raise ValueError("invalid helper selection")
        return self.result("host.status", {
            "setup": value["phase"], "runtime": "available" if value["ready"] else "offline",
            "observation": "known", "devices": devices, "pending": device(value["pending"]),
            "selected": device(value["selected"]), "inviteState": "unknown", "expiresAt": None})

    def host_details(self, snapshot: dict, raw: str) -> dict:
        """Fresh display detail only; v1 keeps its original bytes/vocabulary."""
        value = parse_json(raw, 16384)
        dimensions = {'setup', 'runtime', 'invite', 'address', 'policy', 'cleanup'}
        if (type(value) is not dict or set(value) != {'version', *dimensions}
                or type(value['version']) is not int or value['version'] != 2):
            raise ValueError('invalid status detail')
        part = value['setup']
        states = {'prepared_no_receipt', 'failed_uncertain', 'partial_cleanup',
                  'runtime_unavailable', 'setup_unverified', 'renewal_failed'}
        legacy = {'ready': 'ready', 'off': 'off', 'in_progress': 'pending',
                  'failed': 'failed', 'uncertain': 'failed'}
        if (type(part) is not dict or set(part) != {'state', 'age_seconds'}
                or type(part['state']) is not str or part['state'] not in states | legacy.keys()
                or type(part['age_seconds']) is not int or not 0 <= part['age_seconds'] <= 30):
            raise ValueError('unavailable status detail')
        if part['state'] in states:
            snapshot = {**snapshot, 'setup': part['state'], 'runtime': 'offline',
                        'devices': None, 'pending': None, 'selected': None}
        elif snapshot['setup'] != legacy[part['state']]:
            raise ValueError('changed status detail')  # Never promote v1 failure to ready.
        invitation = value['invite']
        phases = {'none', 'issued', 'expired', 'minting', 'encoding', 'pairing',
                  'refused', 'uncertain', 'recovered', 'cancelled', 'consumed', 'pending_approval'}
        if (type(invitation) is not dict or set(invitation) != {
                'state', 'age_seconds', 'deadline_utc_seconds'} or type(invitation['state']) is not str):
            raise ValueError('invalid invite detail')
        state, age, expiry = (invitation[k] for k in ('state', 'age_seconds', 'deadline_utc_seconds'))
        if state == 'unknown' and age is None and expiry is None:
            return self.result('host.status', snapshot)
        if (state not in phases or type(age) is not int or not 0 <= age <= 30):
            raise ValueError('unavailable invite detail')
        active = state in ('issued', 'pending_approval')
        if (active and (type(expiry) is not int or not 0 <= expiry < 253402300800)
                or not active and expiry is not None):
            raise ValueError('invalid invite deadline')
        state = {'consumed': 'used', 'pending_approval': 'issued'}.get(state, state)
        if active and expiry <= time.time():
            state, expiry = 'expired', None
        expires = (datetime.fromtimestamp(expiry, timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
                   if expiry is not None else None)
        return self.result('host.status', {**snapshot, 'inviteState': state, 'expiresAt': expires})

    def host_unavailable(self, setup: str = "unknown") -> dict:
        return self.result("host.status", dict(setup=setup, runtime="unknown", observation="unavailable",
            devices=None, pending=None, selected=None, inviteState="unknown", expiresAt=None))

    def host_action(self, method: str, snapshot: dict) -> dict:
        # An acknowledged helper commit establishes only its documented effect.
        # Per-device revoke performs no network-membership cleanup operation.
        return dict(effect="confirmed", hostDenial="confirmed" if method in
                    ("RejectPending", "RevokeSelected") else "notApplicable",
                    hostCleanup="notApplicable", localCleanup="notApplicable",
                    networkCleanup="notApplicable",
                    inviteState="unknown", expiresAt=None, host=snapshot, client=None)


def request_host(self: object, method: str, complete: object, current: object, observe: object,
                 *, allow_reconnect: bool = True, cli_lifecycle: bool = False,
                 display_invite: object = None, terminal: object = None,
                 terminal_current: object = None) -> None:
    """Fixed helper transaction shared by the window and standalone CLI owner.

    The caller owns lifetime/admission; this owner owns the dedicated bus,
    closed projection, acknowledgement and loss semantics. Never retry mutations.
    """
    lifecycle = method in WINDOW_LIFECYCLE or cli_lifecycle and method in CLI_LIFECYCLE_TIMEOUTS
    if method not in HOST_METHODS.values() and not lifecycle:
        complete(None, "unavailable")
        return
    mutation = method in HOST_MUTATIONS or lifecycle
    acknowledged = False
    deadline = None
    status_snapshot = None

    def failed(error: Exception, dispatched: bool) -> None:
        if not current():
            return
        remote = self.Gio.DBusError.get_remote_error(error) if isinstance(error, self.GLib.Error) else None
        absent = remote in ("org.freedesktop.DBus.Error.ServiceUnknown",
                            "org.freedesktop.DBus.Error.NameHasNoOwner")
        snapshot = self.protocol.host_unavailable("off" if absent else "unknown")
        observe(snapshot)
        if acknowledged:
            complete(self.protocol.host_action(method, snapshot))
        elif not mutation and absent and method == "Status":
            complete(snapshot)
        elif method == 'Status' and remote in (
                'ai.happyranch.RemoteAccess1.Error.expired',
                'ai.happyranch.RemoteAccess1.Error.unavailable',
                'org.freedesktop.DBus.Error.NoReply'):
            # The helper's short Status timer is a read timeout, never invite
            # or policy expiry. A failed read proves no mutation outcome.
            category = ('timeout' if remote == 'ai.happyranch.RemoteAccess1.Error.expired'
                        else 'status_unavailable')
            complete(None, dict(code='hostStateUnrecognized', category=category))
        else:
            closed = {"ai.happyranch.RemoteAccess1.Error.denied": "permissionDenied",
                      "org.freedesktop.DBus.Error.AccessDenied": "permissionDenied",
                      "org.freedesktop.DBus.Error.AuthFailed": "permissionDenied",
                      "ai.happyranch.RemoteAccess1.Error.wrong_uid": "permissionDenied",
                      "ai.happyranch.RemoteAccess1.Error.changed": "stateChanged",
                      "ai.happyranch.RemoteAccess1.Error.expired": "expired",
                      "ai.happyranch.RemoteAccess1.Error.busy": "busy",
                      "ai.happyranch.RemoteAccess1.Error.invalid_request": "invalidRequest",
                      "ai.happyranch.RemoteAccess1.Error.unknown_method": "unavailable"}
            for category in ("host_state_unrecognized", "owner_unknown", "owner_ambiguous",
                             "owner_unsupported", "owner_mismatch", "release_home_invalid"):
                closed["ai.happyranch.RemoteAccess1.Error." + category] = "hostStateUnrecognized"
            code = closed.get(remote)
            prefix = "ai.happyranch.RemoteAccess1.Error.host_state_unrecognized_"
            category = remote[len(prefix):] if isinstance(remote, str) and remote.startswith(prefix) else None
            if category is not None or code == "hostStateUnrecognized":
                if category is None:
                    category = "unverified" if remote.endswith("host_state_unrecognized") else "owner"
                elif category not in HOST_STATE_CATEGORIES or category == "unknown":
                    category = "unverified"
                complete(None, dict(code="hostStateUnrecognized", category=category))
            elif code:
                complete(None, code)
            elif mutation and dispatched and not absent:
                complete(None, "outcomeUnknown", "unknown")
            else:
                complete(None, "unavailable")

    def replied(connection: object, result: object, called: str) -> None:
        nonlocal acknowledged, status_snapshot
        if not current():
            return
        try:
            raw, = connection.call_finish(result).unpack()
            if called == 'StatusV2':
                snapshot = self.protocol.host_details(status_snapshot, raw)
                observe(snapshot)
                complete(self.protocol.host_action(method, snapshot) if mutation else snapshot)
                return
            if lifecycle:
                if time.monotonic() >= deadline:
                    raise ValueError("late helper reply")
                if method == "IssueInvite":
                    frame = parse_json(raw, 6144)
                    now = time.time()
                    if (type(frame) is not dict or set(frame) != {"result", "invite", "expires_at"}
                            or frame["result"] != "complete" or type(frame["invite"]) is not str
                            or len(frame["invite"]) > 5500
                            or re.fullmatch(r"hrinvite2:[A-Za-z0-9_-]+", frame["invite"]) is None
                            or type(frame["expires_at"]) is not int
                            or not now < frame["expires_at"] <= now + 300 or display_invite is None):
                        raise ValueError("invalid helper invite")
                    projection = self.protocol.result("host.createInvite", dict(
                        effect="confirmed", hostDenial="notApplicable", hostCleanup="notApplicable",
                        localCleanup="notApplicable", networkCleanup="notApplicable", inviteState="issued",
                        expiresAt=datetime.fromtimestamp(frame["expires_at"], timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                        host=None, client=None))
                    if time.monotonic() >= deadline or time.time() >= frame["expires_at"]:
                        raise ValueError("late helper invite")
                    display_invite(frame["invite"], frame["expires_at"], deadline)
                    complete(projection)
                else:
                    # The public reply is admission-only ("accepted"), before
                    # worker outcome/residue. It proves neither success nor
                    # denial/cleanup, even if a later read happens to be ready.
                    if parse_json(raw, 32768) != "accepted":
                        raise ValueError("invalid helper admission")

                    if not cli_lifecycle:
                        # The window retains its dedicated caller connection.
                        # Acceptance proves admission only; later Status/events
                        # own every observed effect and cleanup claim.
                        command = "host.enable" if method == "Enable" else "host.turnOff"
                        complete(self.protocol.result(command, dict(
                            effect="unknown", hostDenial="unknown", hostCleanup="unknown",
                            localCleanup="notApplicable", networkCleanup="unknown",
                            inviteState="unknown", expiresAt=None, host=None, client=None)))
                        return

                    def held() -> bool:
                        self.wait_source = None
                        if current():
                            complete(None, "outcomeUnknown", "unknown")
                        return False

                    # Effects revalidate the live caller. Do not disconnect on
                    # admission: keep the bus through the original budget plus
                    # margin, without polling or dispatching another mutation.
                    self.wait_source = self.GLib.timeout_add(
                        max(1, int((deadline - time.monotonic()) * 1000)), held)
                return
            if called in HOST_MUTATIONS:
                if parse_json(raw, 32768) != "ok":
                    raise ValueError("invalid helper acknowledgement")
                acknowledged = True
                # Keep the slot until Status completes; never predict state
                # from an acknowledgement, selected label or fingerprint.
                call("Status")
                return
            snapshot = self.protocol.host(raw)
            if called == 'Status':
                status_snapshot = snapshot
                call('StatusV2')
                return
            observe(snapshot)
            complete(self.protocol.host_action(method, snapshot) if mutation else snapshot)
        except Exception as error:
            if called == 'StatusV2' and status_snapshot is not None:
                # Older peers retain their known v1 failure; never manufacture readiness.
                observe(status_snapshot)
                complete(self.protocol.host_action(method, status_snapshot) if mutation else status_snapshot)
                return
            failed(error, True)

    def call(called: str) -> None:
        nonlocal deadline
        try:
            if called == "Enable" or cli_lifecycle and called in ('Disable', *CLI_COMPLETION_CODES):
                previous = getattr(self, "enable_observer", None)
                if previous is not None:
                    previous.close()
                self.enable_observer = EnableTerminalObserver(self, complete, failed,
                    terminal_current or current, terminal, cli=cli_lifecycle, admission_current=current, method=called)
                self.enable_observer.start()
                return
            if lifecycle and deadline is None:
                deadline = time.monotonic() + CLI_LIFECYCLE_TIMEOUTS[called] / 1000
            self.connection.call("ai.happyranch.RemoteAccess1", "/ai/happyranch/RemoteAccess1",
                "ai.happyranch.RemoteAccess1", called, None, self.GLib.VariantType.new("(s)"),
                self.Gio.DBusCallFlags.ALLOW_INTERACTIVE_AUTHORIZATION if mutation and called == method
                else self.Gio.DBusCallFlags.NONE,
                CLI_LIFECYCLE_TIMEOUTS[called] if lifecycle else 122000 if called in HOST_MUTATIONS else 3000,
                self.cancellable, replied, called)
        except Exception as error:
            failed(error, False)

    def connected(_source: object, result: object, _data: object) -> None:
        try:
            connection = self.Gio.DBusConnection.new_for_address_finish(result)
            connection.set_exit_on_close(False)
            if not current():
                connection.close(None, None, None)
                return
            self.connection = connection
            call(method)
        except Exception as error:
            failed(error, False)

    try:
        if self.connection is not None and not self.connection.is_closed():
            call(method)
        elif self.connection is not None and not allow_reconnect:
            # A CLI selection belongs to its initial bus, even if Gio closes it
            # after the caller's admission check. Never dispatch on a new bus.
            complete(None, "unavailable")
        else:
            self.Gio.DBusConnection.new_for_address("unix:path=/run/dbus/system_bus_socket",
                self.Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT |
                self.Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION,
                None, self.cancellable, connected, None)
    except Exception as error:
        failed(error, False)
