"""Native Secret Service custody. No filesystem or substitute credential store."""
from __future__ import annotations

import base64
import hashlib
import json
import re
import threading

SCHEMA = "ai.happyranch.LinuxClient.v1"
LOGIN = "/org/freedesktop/secrets/collection/login"
LIMIT = 112 * 1024
FIELDS = ("version", "bearer", "home_id", "control_plane_id", "stable_node_id",
          "connector_id", "control_url", "ca_der", "fingerprint", "dial_ip",
          "home_endpoint", "profile_id", "hostname", "network_state")
ATTRS = ("home", "control", "node", "profile")


class StoreError(Exception):
    def __init__(self, category: str) -> None:
        super().__init__(category)


def binding(record: object) -> dict:
    return dict(zip(ATTRS, (hashlib.sha256(value.encode()).hexdigest() for value in
                (record.home_id, record.control_plane_id, record.stable_node_id, record.profile_id))))


def reference(record: object) -> str:
    return hashlib.sha256(json.dumps(binding(record), sort_keys=True).encode()).hexdigest()


def encode(record: object) -> str:
    values = {name: getattr(record, name) for name in FIELDS}
    for name in ("ca_der", "network_state"):
        values[name] = base64.b64encode(values[name]).decode("ascii")
    raw = json.dumps(values, separators=(",", ":"), ensure_ascii=True)
    if len(raw) > LIMIT:
        raise StoreError("saveFailed")
    return raw


def decode(raw: str, native: object) -> object:
    def pairs(items: list) -> dict:
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError()
            result[key] = value
        return result
    try:
        if type(raw) is not str or not 0 < len(raw.encode("utf-8")) <= LIMIT:
            raise ValueError()
        values = json.loads(raw, object_pairs_hook=pairs)
        if (type(values) is not dict or set(values) != set(FIELDS)
                or type(values["version"]) is not int or values["version"] != 1
                or any(type(values[k]) is not str or not values[k] for k in FIELDS[1:])):
            raise ValueError()
        for name in ("ca_der", "network_state"):
            values[name] = base64.b64decode(values[name], validate=True)
        record = native.SavedRecord(**values)
        if (not re.fullmatch(r"hrpin_[1-9][0-9]{0,18}_[0-9a-f]{64}", record.bearer)
                or not re.fullmatch(r"[0-9a-f]{32}", record.profile_id)
                or record.hostname != "happyranch-linux-" + record.profile_id
                or not 0 < len(record.ca_der) <= 8192
                or record.fingerprint != hashlib.sha256(record.ca_der).hexdigest()
                or any(len(values[k].encode()) > (256 if k in ("home_id", "control_plane_id", "home_endpoint") else 1024)
                       or re.search(r"[\x00-\x20\x7f-\x9f\u202a-\u202e\u2066-\u2069]", values[k])
                       for k in FIELDS[2:7] + FIELDS[8:13])):
            raise ValueError()
        native._network_state(record.network_state, record.control_plane_id, record.stable_node_id)
        return record
    except Exception:
        raise StoreError("saveFailed") from None


class SecretStore:
    def __init__(self, native: object) -> None:
        self.native = native  # same SavedRecord type as the session owner
        self.lock = threading.Lock()

    def __repr__(self) -> str:
        return "SecretStore(<redacted>)"

    def _run(self, operation: object) -> object:
        # Serialize write/readback/delete. No user keyring unlock/create request,
        # secret-search UNLOCK flag, session collection or alternative collection.
        with self.lock:
            timer = None
            dispatched = False
            try:
                import gi
                gi.require_version("Secret", "1")
                from gi.repository import Gio, Secret
                cancel = Gio.Cancellable()
                timer = threading.Timer(15, cancel.cancel)
                timer.start()
                service = Secret.Service.get_sync(Secret.ServiceFlags.OPEN_SESSION, cancel)
                collection = Secret.Collection.for_alias_sync(service, "default", Secret.CollectionFlags.LOAD_ITEMS, cancel)
                if collection is None or collection.get_object_path() != LOGIN:
                    raise StoreError("unavailable")
                if collection.get_locked():
                    raise StoreError("keychainLocked")
                schema = Secret.Schema.new(SCHEMA, Secret.SchemaFlags.NONE,
                                           {k: Secret.SchemaAttributeType.STRING for k in ATTRS})
                dispatched = True
                return operation(Secret, collection, schema, cancel)
            except StoreError:
                raise
            except Exception as exc:
                category = "saveFailed" if dispatched else "unavailable"
                # Inspect only structured remote error names, never diagnostics.
                if 'Gio' in locals() and isinstance(exc, Exception):
                    try:
                        remote = Gio.DBusError.get_remote_error(exc)
                        if remote == "org.freedesktop.Secret.Error.IsLocked":
                            category = "keychainLocked"
                        elif remote in ("org.freedesktop.DBus.Error.AccessDenied",
                                        "org.freedesktop.Secret.Error.Denied",
                                        "org.freedesktop.Secret.Error.PromptDismissed"):
                            category = "keychainDenied"
                    except (TypeError, ValueError):
                        pass
                raise StoreError(category) from None
            finally:
                if timer:
                    timer.cancel()

    def _items(self, collection: object, cancel: object) -> list:
        from gi.repository import Secret
        # refresh() is asynchronous; a fresh collection-scoped SearchItems reply
        # is required for discovery and particularly post-delete absence.
        items = collection.search_sync(None, {}, Secret.SearchFlags.ALL, cancel)
        if len(items) > 256:
            raise StoreError("unavailable")
        return items

    def _attributes(self, item: object) -> dict | None:
        attrs = item.get_attributes()
        if attrs.get("xdg:schema") != SCHEMA:
            return None
        if (set(attrs) != {*ATTRS, "xdg:schema"}
                or any(re.fullmatch("[0-9a-f]{64}", attrs[k]) is None for k in ATTRS)):
            raise StoreError("saveFailed")
        return {k: attrs[k] for k in ATTRS}

    def _read(self, item: object, cancel: object) -> object:
        attrs = self._attributes(item)
        if item.get_locked():
            raise StoreError("keychainLocked")
        if not item.load_secret_sync(cancel) or item.get_secret() is None:
            raise StoreError("saveFailed")
        record = decode(item.get_secret().get_text(), self.native)
        if attrs != binding(record):
            raise StoreError("saveFailed")
        return record

    def list(self) -> tuple[dict, str, str]:
        def discover(_secret: object, collection: object, _schema: object, cancel: object) -> tuple:
            records, partial = {}, False
            for item in self._items(collection, cancel):
                try:
                    if self._attributes(item) is None:
                        continue
                    record = self._read(item, cancel)
                    ref = reference(record)
                    if ref in records or len(records) >= 32:
                        raise StoreError("saveFailed")
                    records[ref] = record
                except Exception:
                    partial = True  # An unreadable item never hides healthy peers.
            return records, "unknown" if partial else "saved" if records else "none", "unavailable" if partial else "known"
        try:
            return self._run(discover)
        except StoreError as exc:
            return {}, "needsPermission" if str(exc) in ("keychainLocked", "keychainDenied") else "unknown", "unavailable"

    def save(self, record: object) -> None:
        raw = encode(record)
        decode(raw, self.native)
        def write(secret: object, collection: object, schema: object, cancel: object) -> None:
            attrs = binding(record)
            # Fail before write on a full inventory; never silently evict access.
            items = self._items(collection, cancel)
            if len(items) >= 256 or sum(self._attributes(i) is not None for i in items) >= 32:
                if not any(self._attributes(i) == attrs for i in items):
                    raise StoreError("saveFailed")
            item = secret.Item.create_sync(collection, schema, attrs, "HappyRanch saved computer",
                secret.Value.new(raw, -1, "text/plain"), secret.ItemCreateFlags.REPLACE, cancel)
            if item is None or encode(self._read(item, cancel)) != raw:
                raise StoreError("saveFailed")
        try:
            self._run(write)
        except StoreError as exc:
            if str(exc) == "unavailable":
                raise
            raise StoreError(str(exc) if str(exc) in ("keychainLocked", "keychainDenied") else "saveFailed") from None

    def load(self, record: object) -> object:
        def read(_s: object, collection: object, _schema: object, cancel: object) -> object:
            matches = []
            for item in self._items(collection, cancel):
                try:
                    if self._attributes(item) == binding(record):
                        matches.append(item)
                except Exception:
                    continue  # malformed peers cannot substitute for this exact item

            if len(matches) != 1:
                raise StoreError("unavailable")
            loaded = self._read(matches[0], cancel)
            if encode(loaded) != encode(record):
                raise StoreError("stateChanged")  # native consent captured this exact record
            return loaded
        return self._run(read)

    def delete(self, record: object) -> None:
        def remove(_s: object, collection: object, _schema: object, cancel: object) -> None:
            attrs = binding(record)
            for item in self._items(collection, cancel):
                if self._attributes(item) == attrs and not item.delete_sync(cancel):
                    raise StoreError("removalIncomplete")
            if any(self._attributes(i) == attrs for i in self._items(collection, cancel)):
                raise StoreError("removalIncomplete")
        try:
            self._run(remove)
        except StoreError:
            raise StoreError("removalIncomplete") from None
