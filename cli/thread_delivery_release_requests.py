"""Dormant human release custody and strict readback. Never a native writer.

Same-UID tampering is outside this cooperative storage guarantee. Failed or
changed files stay in place under the original key; nothing retries a POST.
"""
from __future__ import annotations

from contextlib import contextmanager
import json
import math
import os
from pathlib import Path
import re
import stat
from typing import Any, Iterator

from runtime.infrastructure.thread_delivery_release import (
    PreviewRequest, ReleaseCommand, RISKS, canonical, confirmation_inputs, decode,
    digest, validate_confirmation,
)

REQUEST_LIMIT = 16384
RESPONSE_LIMIT = 32768
INCOMPLETE = frozenset({"serving_owner_unavailable", "current_native_eligibility_unavailable"})


def identifier(value: str, *, key: bool = False) -> str:
    if type(value) is not str or re.fullmatch(r"[A-Za-z0-9_-]{1," + ("128" if key else "256") + "}", value) is None:
        raise ValueError("invalid_release_identity")
    return value


def hex_digest(value: Any) -> str:
    if type(value) is not str or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise ValueError("invalid_release_digest")
    return value


def _unique(pairs: list[tuple[str, Any]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate_release_member")
        result[key] = value
    return result


def _constant(_: str) -> Any:
    raise ValueError("nonfinite_release_number")


def _float(value: str) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("nonfinite_release_number")
    return number


def strict_json(raw: bytes, *, limit: int = RESPONSE_LIMIT) -> dict:
    if type(raw) is not bytes or not 0 < len(raw) <= limit:
        raise ValueError("release_byte_bound")
    value = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique,
                       parse_constant=_constant, parse_float=_float)
    if type(value) is not dict:
        raise ValueError("invalid_release_object")
    return value


def _fields(value: Any, fields: str) -> None:
    if type(value) is not dict or set(value) != set(fields.split()):
        raise ValueError("invalid_release_fields")


def _booleans(value: dict, *fields: str) -> None:
    if any(type(value[field]) is not bool for field in fields):
        raise ValueError("invalid_release_boolean")


def capability_result(value: dict) -> dict:
    _fields(value, "version serving_admission_available proof_backed_available external_adapters_available")
    if type(value["version"]) is not int or value["version"] != 1 or value["proof_backed_available"] is not False:
        raise ValueError("invalid_release_capability")
    _booleans(value, "serving_admission_available", "proof_backed_available", "external_adapters_available")
    return value


def preview_result(value: dict, selection: PreviewRequest) -> dict:
    _fields(value, "inputs preview_digest accepted_risks proof_backed_available serving_admission_available disposition")
    _booleans(value, "proof_backed_available", "serving_admission_available")
    if (value["proof_backed_available"] is not False or value["accepted_risks"] != list(RISKS)
            or value["disposition"] not in {"running_replaced", "historical_only"}):
        raise ValueError("invalid_release_preview")
    hex_digest(value["preview_digest"])
    if digest(canonical(value["inputs"])) != value["preview_digest"]:
        raise ValueError("invalid_release_preview_digest")
    command = command_from_preview(value)
    expected = selection.model_dump(mode="json")
    if any(command.model_dump(mode="json")[key] != item for key, item in expected.items()):
        raise ValueError("release_preview_selection_mismatch")
    return value


def command_from_preview(value: dict) -> ReleaseCommand:
    if type(value.get("inputs")) is not dict:
        raise ValueError("invalid_release_inputs")
    command = decode(ReleaseCommand, canonical({**value["inputs"], "confirmation": {
        "confirmed": True, "preview_digest": value["preview_digest"],
        "accepted_risks": value["accepted_risks"],
    }}).decode("utf-8"))
    validate_confirmation(command)
    integers = [command.selection.invocation_id, command.prefix.last_id, command.range.acknowledged,
                command.range.required, command.range.running_from, command.range.running_through]
    if any(item is not None and (type(item) is not int or not 0 <= item < 2**63) for item in integers):
        raise ValueError("invalid_release_integer")
    if confirmation_inputs(command) != value["inputs"]:
        raise ValueError("release_preview_normalization")
    return command


def operation_result(value: dict, key: str, command: ReleaseCommand | None = None,
                     *, complete: bool = True) -> dict:
    _fields(value, "version operation_key audit_id command_digest committed disposition original_id replacement_id "
            "replacement_token_digest current_delivery_permission current_native_eligibility serving_admission_available "
            "incomplete_reasons notification")
    if (type(value["version"]) is not int or value["version"] != 1 or value["operation_key"] != key
            or value["committed"] is not True):
        raise ValueError("invalid_release_operation")
    identifier(value["operation_key"], key=True)
    for field in ("audit_id", "original_id"):
        if type(value[field]) is not int or not 0 < value[field] < 2**63:
            raise ValueError("invalid_release_integer")
    hex_digest(value["command_digest"])
    _booleans(value, "current_delivery_permission", "current_native_eligibility", "serving_admission_available")
    reasons = value["incomplete_reasons"]
    if (type(reasons) is not list or len(reasons) > 64
            or any(type(reason) is not str or reason not in INCOMPLETE for reason in reasons)
            or len(set(reasons)) != len(reasons) or (complete and reasons)):
        raise ValueError("incomplete_release_readback")
    if value["notification"] not in {"unknown", "pending_or_skipped", "published_same_token"}:
        raise ValueError("invalid_release_notification")
    if value["disposition"] == "running_replaced":
        replacement = value["replacement_id"]
        if type(replacement) is not int or not 0 < replacement < 2**63 or replacement == value["original_id"]:
            raise ValueError("invalid_release_replacement")
        hex_digest(value["replacement_token_digest"])
    elif value["disposition"] == "historical_only":
        if value["replacement_id"] is not None or value["replacement_token_digest"] is not None:
            raise ValueError("invalid_release_historical_link")
    else:
        raise ValueError("invalid_release_disposition")
    if value["current_native_eligibility"] and (value["replacement_id"] is None
            or not value["current_delivery_permission"] or not value["serving_admission_available"]):
        raise ValueError("invalid_release_eligibility")
    if value["notification"] == "published_same_token" and value["replacement_id"] is None:
        raise ValueError("invalid_release_notification_link")
    if command is not None:
        validate_confirmation(command)
        if (command.operation_key != key or value["command_digest"] != digest(canonical(command.model_dump(mode="json")))
                or value["original_id"] != command.selection.invocation_id):
            raise ValueError("release_saved_command_mismatch")
    if len(canonical(value)) > RESPONSE_LIMIT:
        raise ValueError("release_response_bound")
    return value


class PrivateReleaseRequests:
    """Positive file+directory durability; no overwrite, unlink or recovery."""

    def __init__(self, slug: str) -> None:
        self.slug = identifier(slug)
        self.home = Path.home()
        self.root = self.home / ".happyranch" / "client-thread-delivery-releases" / slug

    @staticmethod
    def _same(left: os.stat_result, right: os.stat_result, *, file: bool = False) -> bool:
        fields = ("st_dev", "st_ino", "st_mode", "st_uid", "st_gid")
        if file:
            fields += ("st_nlink", "st_size", "st_mtime_ns", "st_ctime_ns")
        return all(getattr(left, field) == getattr(right, field) for field in fields)

    @contextmanager
    def directory(self) -> Iterator[int]:
        descriptors: list[int] = []
        entries = []
        primary = None
        try:
            parent = os.open(self.home, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            descriptors.append(parent)
            home_info = os.fstat(parent)
            if (not stat.S_ISDIR(home_info.st_mode) or home_info.st_uid != os.getuid()
                    or not self._same(home_info, os.stat(self.home, follow_symlinks=False))):
                raise ValueError("release_home_identity_unavailable")
            for index, name in enumerate((".happyranch", "client-thread-delivery-releases", self.slug)):
                try:
                    os.mkdir(name, mode=0o700, dir_fd=parent)
                except FileExistsError:
                    pass
                child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
                descriptors.append(child)
                info = os.fstat(child)
                if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid()
                        or (index > 0 and stat.S_IMODE(info.st_mode) != 0o700)
                        or not self._same(info, os.stat(name, dir_fd=parent, follow_symlinks=False))):
                    raise ValueError("release_directory_identity_unavailable")
                entries.append((parent, name, child, info))
                parent = child
            yield parent
            for parent, name, child, info in reversed(entries):
                if (not self._same(info, os.fstat(child))
                        or not self._same(info, os.stat(name, dir_fd=parent, follow_symlinks=False))):
                    raise ValueError("release_directory_changed")
                os.fsync(child)
            if (not self._same(home_info, os.fstat(descriptors[0]))
                    or not self._same(home_info, os.stat(self.home, follow_symlinks=False))):
                raise ValueError("release_home_changed")
            os.fsync(descriptors[0])
        except BaseException as exc:
            primary = exc
            raise
        finally:
            failure = None
            for descriptor in reversed(descriptors):
                try:
                    os.close(descriptor)
                except OSError as exc:
                    failure = failure or exc
            if failure is not None:
                if primary is None:
                    raise failure
                primary.add_note("release_directory_close_failed")

    def _name(self, key: str, kind: str) -> str:
        identifier(key, key=True)
        if kind not in {"selection", "preview", "apply"}:
            raise ValueError("invalid_release_saved_kind")
        return key + "." + kind + ".json"

    def _validate(self, raw: bytes, key: str, kind: str) -> None:
        value = strict_json(raw, limit=RESPONSE_LIMIT if kind == "preview" else REQUEST_LIMIT)
        if canonical(value) != raw:
            raise ValueError("release_saved_noncanonical")
        if kind == "preview":
            command = command_from_preview(value)
            selection = decode(PreviewRequest, canonical({k: value["inputs"][k]
                for k in ("version", "operation_key", "mode", "selection")}).decode("utf8"))
            preview_result(value, selection)
        else:
            command = decode(ReleaseCommand if kind == "apply" else PreviewRequest, raw.decode("utf8"))
            if kind == "apply":
                validate_confirmation(command)
        if command.operation_key != key or command.selection.org_slug != self.slug:
            raise ValueError("release_saved_identity_mismatch")

    def _read(self, parent: int, name: str, key: str, kind: str, *,
              expected: bytes | None = None, identity: tuple | None = None) -> bytes:
        limit = RESPONSE_LIMIT if kind == "preview" else REQUEST_LIMIT
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        try:
            info = os.fstat(fd)
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                    or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1
                    or not 0 < info.st_size <= limit
                    or (identity is not None and (info.st_dev, info.st_ino) != identity)
                    or not self._same(info, os.stat(name, dir_fd=parent, follow_symlinks=False), file=True)):
                raise ValueError("release_file_identity_unavailable")
            raw = bytearray()
            while len(raw) <= limit:
                chunk = os.read(fd, min(4096, limit + 1 - len(raw)))
                if not chunk:
                    break
                raw.extend(chunk)
            result = bytes(raw)
            self._validate(result, key, kind)
            if expected is not None and result != expected:
                raise ValueError("release_saved_bytes_changed")
            os.fsync(fd)
            if (not self._same(info, os.fstat(fd), file=True)
                    or not self._same(info, os.stat(name, dir_fd=parent, follow_symlinks=False), file=True)):
                raise ValueError("release_file_changed")
        finally:
            os.close(fd)  # Positive close is required even for existing files.
        return result

    def save(self, key: str, kind: str, value: dict) -> bytes:
        name, raw = self._name(key, kind), canonical(value)
        self._validate(raw, key, kind)
        with self.directory() as parent:
            try:
                fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_NONBLOCK,
                             0o600, dir_fd=parent)
            except FileExistsError:
                self._read(parent, name, key, kind, expected=raw)
            else:
                try:
                    info = os.fstat(fd)
                    if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                            or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1):
                        raise ValueError("release_new_file_unavailable")
                    # Unbuffered writes: any short write is a refused save.
                    if os.write(fd, raw) != len(raw):
                        raise ValueError("release_short_write")
                    os.fsync(fd)
                finally:
                    os.close(fd)
                self._read(parent, name, key, kind, expected=raw, identity=(info.st_dev, info.st_ino))
        return raw

    def read(self, key: str, kind: str) -> bytes:
        with self.directory() as parent:
            raw = self._read(parent, self._name(key, kind), key, kind)
        return raw
