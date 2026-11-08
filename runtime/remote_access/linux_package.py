"""Deterministic Linux composite package for the managed embedded transport."""
from __future__ import annotations

from runtime.remote_access.package_diagnostics import (
    diagnostic_error,
    diagnostic_scope,
    diagnostic_loss,
    diagnostic_propagate,
    exception_refusal,
    exception_loaded,
    loaded_observation,
    dependency_refusal_stderr,
    loaded_projection,
)

import hashlib
import io
import json
import os
import re
import secrets
from pathlib import Path, PurePosixPath
import shutil
import tarfile
import tempfile
import zipfile
import subprocess
import stat
import sys
import time
from typing import Callable, Mapping

from runtime.remote_access.systemd_unit import ConnectorUnitSpec, render_connector_unit


class PackageError(RuntimeError):
    """Stable, category-only package failure."""


PREFIX = "happyranch-linux-amd64"
UNITS = (
    "happyranch-connector.service",
    "happyranch-tsnet-sidecar.service",
    "happyranch-managed.target",
)
PAYLOAD_MODES = {
    "bin/happyranch-tsnet-sidecar": "0o755",
    "bin/happyranch-connector": "0o755",
    "share/happyranch.whl": "0o644",
    "share/dependency-inventory.json": "0o644",
    "share/sbom.cdx.json": "0o644",
    "share/THIRD_PARTY_NOTICES.md": "0o644",
    **{f"systemd/{name}": "0o644" for name in UNITS},
}
TRANSACTION_MARKER = ".happyranch-install-transaction.json"
# Bounded on-disk install transaction contract.  The marker is the sole durable
# ownership record: it is written atomically before any prior (OLD) byte is
# mutated and is removed last, after the authoritative commit and cleanup.  A
# record exists in exactly one of the phases below and records the minimum facts
# needed to classify and recover or conservatively refuse the transaction.
TRANSACTION_SCHEMA_VERSION = 2
_PAYLOAD_BACKUP_NAME = ".happyranch-backup"
_UNIT_BACKUP_NAME = ".happyranch-units-backup"
_STAGE_PREFIX = ".happyranch-stage-"
_DROPIN_SERVICE_DIR = "happyranch-tsnet-sidecar.service.d"
_DROPIN_FILE_NAME = "10-enrollment-credential.conf"
_DROPIN_BACKUP_RELATIVE = PurePosixPath(_DROPIN_SERVICE_DIR) / _DROPIN_FILE_NAME
_RESIDUE_PREFIXES = (
    _PAYLOAD_BACKUP_NAME,
    _UNIT_BACKUP_NAME,
    _STAGE_PREFIX,
    ".happyranch-install-transaction",
)
_DROPIN_BYTES = b"[Service]\nLoadCredential=enrollment.key:/etc/happyranch/enrollment.key\n"
# A record is produced only by this module: the attempt identity is a 32-char
# lowercase hex token and the stage directory name is the ``mkdtemp`` product
# of ``.happyranch-stage-<attempt>-<8 random chars>``.  Requiring that exact
# shape (rather than a bare prefix) prevents a plausible marker from
# redirecting ownership to an unrelated foreign directory.
_ATTEMPT_ID_PATTERN = re.compile(r"^[0-9a-f]{32}$")
_STAGE_SUFFIX_PATTERN = re.compile(r"^[a-z0-9_]{8}$")
# Finite, source-derived plan of parents the installer may create.  A record
# may only ever claim to own parents from this closed set, so an arbitrary
# entry can never authorize deleting a pre-existing empty directory.
_CREATED_PARENT_PLAN = (
    "opt",
    "etc",
    "etc/systemd",
    "etc/systemd/system",
)
_TRANSACTION_KEYS = frozenset({
    "schema_version", "attempt_id", "root", "phase", "payload_present",
    "units", "dropin_present", "stage", "created_parents",
    "published_units", "dropin_published", "backups",
    "new_payload", "new_units", "new_dropin",
})
_TRANSACTION_PHASES = frozenset({
    "preparing", "prepared", "payload_retained", "payload_published",
    "units_publishing", "dropin_publishing", "rolling_back", "committed",
})



MANAGED_LIFECYCLE_CONTRACT = {"lifecycle": 2, "child_health": 2, "recovery_diagnostics": 2}
_MANAGED_EXECUTABLE = "/opt/happyranch/bin/happyranch-tsnet-sidecar"


# Private operation context, never a public bypass flag. Only the fixed child
# below installs it after authenticating the inherited locked description.
from contextlib import contextmanager
from contextvars import ContextVar
import fcntl
import selectors
import signal

_MANAGED_DEADLINE: ContextVar[int | None] = ContextVar("managed_deadline", default=None)
_MANAGED_FRESH: ContextVar[bool] = ContextVar("managed_fresh", default=False)
_MANAGED_PACKAGE: ContextVar[bool] = ContextVar("managed_package", default=False)
_MANAGED_LOCK: ContextVar[int | None] = ContextVar("managed_lock", default=None)
# Private direct-package receipt custody; never serialized by public APIs.
_MANAGED_PACKAGE_DIAGNOSTIC: ContextVar[dict[str, object] | None] = ContextVar("managed_package_diagnostic", default=None)
_MANAGED_DECLARATION: ContextVar[dict[str, int] | None] = ContextVar("managed_declaration", default=None)
_CREDENTIAL_REFUSAL: ContextVar[dict | None] = ContextVar("credential_refusal", default=None)


def _managed_entry_deadline(seconds: int, *, entry_ns: int | None = None) -> int:
    now = time.monotonic_ns()
    inherited = _MANAGED_DEADLINE.get()
    limit = (now if entry_ns is None else entry_ns) + seconds * 1_000_000_000
    deadline = min(inherited if inherited is not None else limit, limit)
    carrier = os.environ.get("HAPPYRANCH_LIFECYCLE_DEADLINE_NS")
    if carrier is not None:
        with diagnostic_scope("P100001", "managed_operational_error"):
            if re.fullmatch(r"[1-9][0-9]{0,18}", carrier) is None or int(carrier) > 2**63 - 1:
                raise diagnostic_error("P000001", "managed_lifecycle_deadline", PackageError("managed_lifecycle_deadline"))
        with diagnostic_scope("P100002", "managed_operational_error"):
            deadline = min(deadline, int(carrier))
    if deadline <= now:
        raise diagnostic_error("P000002", "managed_lifecycle_deadline", PackageError("managed_lifecycle_deadline"))
    return deadline


def _check_managed_holder() -> None:
    descriptor = _MANAGED_LOCK.get()
    deadline = _MANAGED_DEADLINE.get()
    if descriptor is None or deadline is None or time.monotonic_ns() >= deadline:
        raise diagnostic_error("P000003", "managed_lifecycle_context_required", PackageError("managed_lifecycle_context_required"))
    with diagnostic_scope("P100003", "managed_operational_error"):
        observed = os.fstat(descriptor)
    with diagnostic_scope("P100004", "managed_operational_error"):
        named = Path("/var/lib/happyranch-connector/lifecycle.lock").lstat()
    if (not stat.S_ISREG(observed.st_mode) or observed.st_nlink != 1
            or observed.st_size != 0 or stat.S_IMODE(observed.st_mode) != 0o600
            or (observed.st_dev, observed.st_ino, observed.st_uid) != (named.st_dev, named.st_ino, named.st_uid)):
        raise diagnostic_error("P000004", "managed_lifecycle_context_required", PackageError("managed_lifecycle_context_required"))
    with diagnostic_scope("P100005", "managed_operational_error"):
        independent = os.open("/var/lib/happyranch-connector/lifecycle.lock", os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        try:
            with diagnostic_scope("P100006", "managed_operational_error"):
                fcntl.flock(independent, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            pass
        else:
            raise diagnostic_error("P000005", "managed_lifecycle_context_required", PackageError("managed_lifecycle_context_required"))
        with diagnostic_scope("P100007", "managed_operational_error"):
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    finally:
        with diagnostic_scope("P100008", "managed_operational_error"):
            os.close(independent)
    if time.monotonic_ns() >= deadline:
        raise diagnostic_error("P000006", "managed_lifecycle_deadline", PackageError("managed_lifecycle_deadline"))


def _require_managed_package_context() -> None:
    if not _MANAGED_PACKAGE.get():
        raise diagnostic_error("P000007", "managed_package_context_required", PackageError("managed_package_context_required"))
    _check_managed_holder()


# Private diagnostic language. Never use these projections for admission or
# installation decisions, public errors, audit data or arbitrary stderr.
_PACKAGE_NATIVE_SENTENCES = (
    "Could not load PyInstaller's embedded PKG archive from the executable (<redacted>)",
    "Could not side-load PyInstaller's PKG archive from external file (<redacted>)",
    "Failed to pre-initialize embedded python interpreter!",
)
_PACKAGE_LOSSES = ("BL", "FL", "FC", "ML", "UN", "U8", "CT", "PE", "RE", "CE", "SD", "JS", "TL", "CX")
_PACKAGE_STAGES = ("admission_child", "stopped_admission_child", "package_body")
_PACKAGE_REASONS = (
    "entry_context", "effective_uid", "operation", "incoming_image", "admission_deadline",
    "executable_open", "bootstrap_operation", "bootstrap_pair", "open_pair", "admission_current",
    "admission_child", "inhibit_policy", "inhibit_deadline", "manager_stop", "reacquire_pair",
    "stopped_admission_deadline", "stopped_current", "stopped_admission_child", "retire_policy",
    "package_body", "final_refresh", "final_current",
)
_PACKAGE_PRIVATE_REASONS = (
    "effective_uid", "deadline", "context_before", "entry_input", "admission_contract",
    "admission_history", "admission_dropin", "uninstall_prefix", "uninstall_images",
    "admission_context_after", "body_guard_before", "body_history", "body_contract",
    "body_install", "body_uninstall", "body_context_after", "body_guard_after",
    "body_response", "context_release",
)
_PACKAGE_DIAGNOSTIC_STATES = ("observed", "absent", "unavailable", "malformed", "unallowlisted", "truncated", "contradictory")
_PACKAGE_ERROR_CATEGORIES = (
    "credential_absent", "credential_unsafe_symlink", "credential_wrong_type",
    "credential_wrong_custody", "credential_staging_incompatible",
    "archive_duplicate_member", "archive_member_invalid", "archive_mode_invalid", "archive_owner_invalid",
    "install_mode_invalid", "inventory_invalid", "managed_configuration_invalid", "managed_identity_invalid",
    "managed_identity_missing", "managed_lifecycle_context_required", "managed_lifecycle_deadline",
    "managed_lifecycle_incompatible", "managed_lifecycle_operation_invalid", "managed_lifecycle_owner_required",
    "managed_lifecycle_owner_unknown", "managed_lifecycle_refused", "managed_lifecycle_response_invalid",
    "managed_loaded_contract_mismatch", "managed_manager_unavailable", "managed_package_context_required",
    "managed_transaction_pending", "managed_uninstall_incomplete", "managed_worker_refused",
    "manifest_count_invalid", "manifest_hash_mismatch", "manifest_invalid", "manifest_membership_mismatch",
    "manifest_mode_invalid", "manifest_path_invalid", "notice_invalid", "notice_inventory_mismatch",
    "package_input_invalid", "sbom_invalid", "sbom_inventory_mismatch", "service_manager_failed",
    "service_not_ready", "service_unit_invalid", "transaction_state_invalid", "wheel_invalid",
)


def _package_literal(value: object, choices: tuple[str, ...], *, nullable: bool = False) -> str | None:
    if value is None and nullable:
        return None
    if type(value) is str:
        for literal in choices:
            if value == literal:
                return literal
    raise diagnostic_error("P000008", "private_diagnostic_invalid", ValueError("private_diagnostic_invalid"))


def _package_integer(value: object, low: int, high: int) -> int | None:
    if value is None or type(value) is int and low <= value <= high:
        return value
    raise diagnostic_error("P000009", "private_diagnostic_invalid", ValueError("private_diagnostic_invalid"))


def _package_boolean(value: object) -> bool | None:
    if value is None or type(value) is bool:
        return value
    raise diagnostic_error("P000010", "private_diagnostic_invalid", ValueError("private_diagnostic_invalid"))


def _package_keys(value: object, keys: tuple[str, ...]) -> dict:
    if type(value) is not dict or set(value) != set(keys):
        raise diagnostic_error("P000011", "private_diagnostic_invalid", ValueError("private_diagnostic_invalid"))
    return value


def _package_loss_projection(value: object, order: tuple[str, ...] = _PACKAGE_LOSSES) -> list[str] | None:
    if value is None:
        return None
    if type(value) is not list or len(value) > len(order):
        raise diagnostic_error("P000012", "private_diagnostic_invalid", ValueError("private_diagnostic_invalid"))
    result = [_package_literal(code, order) for code in value]
    if result != [code for code in order if code in result]:
        raise diagnostic_error("P000013", "private_diagnostic_invalid", ValueError("private_diagnostic_invalid"))
    return result


def _package_json(raw: bytes, limit: int = 1024) -> object:
    if len(raw) > limit or any(b > 127 for b in raw):
        raise diagnostic_error("P000014", "private_diagnostic_invalid", ValueError("private_diagnostic_invalid"))
    def no_constant(_value: str) -> None:
        raise diagnostic_error("P000015", "private_diagnostic_invalid", ValueError("private_diagnostic_invalid"))
    with diagnostic_scope("P100009", "managed_operational_error"):
        value = json.loads(raw, object_pairs_hook=_reject_duplicate_keys, parse_constant=no_constant)
    pending = [(value, 0)]
    nodes = 0
    while pending:
        item, depth = pending.pop()
        nodes += 1
        if depth > 8 or nodes > 512 or type(item) not in (dict, list, str, int, bool, type(None)):
            raise diagnostic_error("P000016", "private_diagnostic_invalid", ValueError("private_diagnostic_invalid"))
        if type(item) is str and (not item.isascii() or any(ord(c) < 32 for c in item)):
            raise diagnostic_error("P000017", "private_diagnostic_invalid", ValueError("private_diagnostic_invalid"))
        if type(item) is dict:
            pending.extend((k, depth + 1) for k in item)
            pending.extend((v, depth + 1) for v in item.values())
        elif type(item) is list:
            pending.extend((v, depth + 1) for v in item)
    return value


def _package_process_projection(record: dict) -> dict[str, object]:
    process = _package_keys(record["process"], ("launch", "wait", "wait_error", "kill", "writer_close"))
    result = {
        "launch": _package_literal(process["launch"], ("not_attempted", "failed", "started", "unknown")),
        "wait": _package_literal(process["wait"], ("not_attempted", "pending", "reaped", "failed", "unknown")),
        "wait_error": _package_boolean(process["wait_error"]),
        "kill": _package_literal(process["kill"], ("not_requested", "requested", "sent", "not_found", "failed", "unknown")),
        "writer_close": _package_literal(process["writer_close"], ("ok", "error", "not_opened", "unknown")),
    }
    reaped = _package_boolean(record["reaped"])
    helper_exit = _package_integer(record["helper_exit"], -64, 255)
    reap_exit = _package_integer(record["reap_exit"], -64, 255)
    killed = _package_boolean(record["kill_requested"])
    _package_boolean(record["deadline_elapsed"])
    if (helper_exit != reap_exit or helper_exit is not None and reaped is not True
            or reap_exit is not None and reaped is not True
            or result["wait"] == "reaped" and reaped is not True
            or reaped is True and result["wait"] != "reaped"
            or result["wait"] == "unknown" and reaped is not None
            or result["wait"] in ("not_attempted", "pending", "failed") and reaped is not False
            or result["launch"] in ("not_attempted", "failed") and (reaped is not False or reap_exit is not None)
            or reaped is True and result["launch"] != "started"
            or result["wait"] in ("not_attempted", "pending") and result["wait_error"] is not None
            or result["kill"] == "not_requested" and killed is not False
            or result["kill"] == "unknown" and killed is not None
            or result["kill"] in ("requested", "sent", "not_found", "failed") and killed is not True):
        raise diagnostic_error("P000018", "private_diagnostic_invalid", ValueError("private_diagnostic_invalid"))
    return result


def _package_refusal_projection(value: object) -> dict[str, str] | None:
    if value is None:
        return None
    record = _package_keys(value, ("category", "site") + (("loaded",) if type(value) is dict and "loaded" in value else ()))
    with diagnostic_scope("P100010", "managed_operational_error"):
        from runtime.remote_access.package_diagnostic_codes import SITE_CATEGORIES
    site = record["site"]
    if type(site) is not str or SITE_CATEGORIES.get(site) != record["category"]:
        raise diagnostic_error("P000019", "private_diagnostic_invalid", ValueError("private_diagnostic_invalid"))
    result = {"category": record["category"], "site": site}
    if "loaded" in record:
        result["loaded"] = loaded_projection(record["loaded"])
    return result


def _package_loss_sites(value: object) -> list[str]:
    from runtime.remote_access.package_diagnostic_codes import SITE_CATEGORIES
    if (type(value) is not list or len(value) > 4
            or any(type(site) is not str or site not in SITE_CATEGORIES for site in value)
            or len(set(value)) != len(value)):
        raise diagnostic_error("P400001", "private_diagnostic_invalid", ValueError("private_diagnostic_invalid"))
    return list(value)


def _package_guard_projection(value: object) -> list[object]:
    # Compact positional acquisition facts; distinct from the enclosing child.
    if type(value) is not list or len(value) != 20:
        raise diagnostic_error("P400002", "private_diagnostic_invalid", ValueError("private_diagnostic_invalid"))
    return [
        _package_literal(value[0], _PACKAGE_DIAGNOSTIC_STATES),
        _package_integer(value[1], -64, 255), _package_boolean(value[2]),
        _package_boolean(value[3]), _package_boolean(value[4]),
        _package_integer(value[5], -64, 255),
        _package_literal(value[6], ("not_attempted", "failed", "started")),
        _package_literal(value[7], ("not_attempted", "pending", "failed", "reaped")),
        _package_boolean(value[8]),
        _package_literal(value[9], ("not_requested", "requested", "sent", "not_found", "failed")),
        _package_literal(value[10], ("not_opened", "unknown", "ok", "error")),
        _package_integer(value[11], 0, 4097), _package_integer(value[12], 0, 4097),
        _package_integer(value[13], 0, 4), _package_boolean(value[14]),
        _package_literal(value[15], ("eof", "limit", "error", "cancelled", "not_started", "unavailable", "unknown")),
        _package_literal(value[16], ("ok", "error", "not_opened", "unknown")),
        _package_loss_projection(value[17]), _package_loss_sites(value[18]),
        (_package_loss_sites([value[19]])[0] if value[19] is not None else None),
    ]


def _managed_package_child_projection(value: object) -> dict[str, object]:
    keys = ("reason", "diagnostic_state", "helper_exit", "deadline_elapsed", "kill_requested", "reaped", "reap_exit", "native", "process")
    extended = type(value) is dict and "refusal" in value
    boundary = type(value) is dict and "boundary" in value
    guard = type(value) is dict and "guard" in value
    child = _package_keys(value, keys + (("refusal",) if extended else ()) + (("boundary",) if boundary else ()) + (("guard",) if guard else ()))
    result = {"reason": _package_literal(child["reason"], _PACKAGE_PRIVATE_REASONS, nullable=True),
              "diagnostic_state": _package_literal(child["diagnostic_state"], _PACKAGE_DIAGNOSTIC_STATES),
              "helper_exit": _package_integer(child["helper_exit"], -64, 255),
              "deadline_elapsed": _package_boolean(child["deadline_elapsed"]),
              "kill_requested": _package_boolean(child["kill_requested"]),
              "reaped": _package_boolean(child["reaped"]),
              "reap_exit": _package_integer(child["reap_exit"], -64, 255),
              "process": _package_process_projection(child)}
    if extended:
        refusal = _package_keys(child["refusal"], ("category", "site") + (("loaded",) if type(child["refusal"]) is dict and "loaded" in child["refusal"] else ()))
        with diagnostic_scope("P100011", "managed_operational_error"):
            from runtime.remote_access.package_diagnostic_codes import SITE_CATEGORIES
        site = refusal["site"]
        if type(site) is not str or SITE_CATEGORIES.get(site) != refusal["category"]:
            raise diagnostic_error("P000020", "private_diagnostic_invalid", ValueError("private_diagnostic_invalid"))
        result["refusal"] = {"category": refusal["category"], "site": site}
        if "loaded" in refusal:
            result["refusal"]["loaded"] = loaded_projection(refusal["loaded"])
    if boundary:
        result["boundary"] = _package_refusal_projection(child["boundary"])
    if guard:
        result["guard"] = _package_guard_projection(child["guard"])
    loss_sites = type(child["native"]) is dict and "loss_sites" in child["native"]
    n = _package_keys(child["native"], ("source", "state", "messages", "eof", "bytes_seen", "frames_seen", "redacted", "truncated", "losses", "read", "close") + (("loss_sites",) if loss_sites else ()))
    if type(n["messages"]) is not list or len(n["messages"]) > 2 or type(n["redacted"]) is not bool or type(n["truncated"]) is not bool:
        raise diagnostic_error("P000021", "private_diagnostic_invalid", ValueError("private_diagnostic_invalid"))
    native = {
        "source": _package_literal(n["source"], ("nested_frozen_stderr",)),
        "state": _package_literal(n["state"], ("observed", "empty", "private_only", "unavailable", "redacted", "truncated", "error")),
        "messages": [_package_literal(m, _PACKAGE_NATIVE_SENTENCES) for m in n["messages"]],
        "eof": _package_boolean(n["eof"]),
        "bytes_seen": _package_integer(n["bytes_seen"], 0, 4224),
        "frames_seen": _package_integer(n["frames_seen"], 0, 17),
        "redacted": n["redacted"], "truncated": n["truncated"],
        "losses": _package_loss_projection(n["losses"]),
        "read": _package_literal(n["read"], ("eof", "limit", "error", "cancelled", "not_started", "unavailable", "unknown")),
        "close": _package_literal(n["close"], ("ok", "error", "not_opened", "unknown")),
    }
    if loss_sites:
        native["loss_sites"] = _package_loss_sites(n["loss_sites"])
    losses = native["losses"] or []
    has = lambda *codes: any(code in losses for code in codes)
    complete = (result["process"]["launch"] == "started" and native["eof"] is True
                and native["read"] == "eof" and native["close"] == "ok"
                and native["bytes_seen"] is not None and native["frames_seen"] is not None and native["losses"] is not None)
    truncated = has("BL", "FL", "FC", "ML")
    if has("U8", "CT", "PE", "RE", "CE", "SD", "JS", "TL", "CX"):
        state = "error"
    elif truncated:
        state = "truncated"
    elif has("UN"):
        state = "redacted"
    elif result["process"]["launch"] != "started":
        state = "unavailable"
    elif native["messages"]:
        state = "observed"
    elif result["reason"] is not None or n["state"] == "private_only" and complete and native["frames_seen"] == 1 and native["bytes_seen"] and not losses and result["helper_exit"] == 78:
        state = "private_only"
    elif complete and native["bytes_seen"] == native["frames_seen"] == 0 and native["losses"] == []:
        state = "empty"
    else:
        state = "unavailable"
    if (native["state"] != state or native["truncated"] != truncated
            or len(set(native["messages"])) != len(native["messages"])
            or native["eof"] is True and native["read"] != "eof"
            or native["read"] == "eof" and native["eof"] is not True
            or (native["bytes_seen"] or 0) > 4096 and not has("BL")
            or native["frames_seen"] == 17 and not has("FC")
            or native["messages"] and not native["frames_seen"]
            or native["read"] == "limit" and not has("BL")
            or native["read"] in ("error", "cancelled") and not has("RE")
            or native["close"] == "error" and not has("CE")
            or (native["frames_seen"] or native["messages"]) and not native["bytes_seen"]
            or (has("UN", "U8", "CT", "PE", "SD", "CX") or any(m in _PACKAGE_NATIVE_SENTENCES[:2] for m in native["messages"])) and not native["redacted"]):
        raise diagnostic_error("P000022", "private_diagnostic_invalid", ValueError("private_diagnostic_invalid"))
    expected = ("contradictory" if has("CX") else "malformed" if has("U8", "CT", "PE", "SD", "JS")
                else "unavailable" if has("RE", "CE", "TL") or not complete or result["process"]["writer_close"] != "ok"
                else "truncated" if truncated else "unallowlisted" if has("UN")
                else "observed" if result["reason"] is not None else "absent")
    if (result["diagnostic_state"] != expected
            or expected == "observed" and (result["reason"] is None or result["reaped"] is not True or result["helper_exit"] != 78 or result["reap_exit"] != 78)
            or expected == "observed" and native["frames_seen"] < len(native["messages"]) + 1
            or expected != "observed" and result["reason"] is not None):
        raise diagnostic_error("P000023", "private_diagnostic_invalid", ValueError("private_diagnostic_invalid"))
    result["native"] = native
    return result


def _empty_managed_package_diagnostic() -> dict[str, object]:
    return {"refusal": None, "boundary": None, "loss_sites": [], "reason": None, "diagnostic_state": "unavailable", "helper_exit": None,
            "deadline_elapsed": None, "kill_requested": False, "reaped": False, "reap_exit": None,
            "outcome": "unknown", "process": {"launch": "not_attempted", "wait": "not_attempted", "wait_error": None, "kill": "not_requested", "writer_close": "not_opened"},
            "transport": {"bytes_seen": None, "stdout_seen": None, "frames_seen": None, "eof": None, "read": "not_started", "close": "not_opened", "losses": None}, "stages": []}


def _managed_package_identities_projection(value: object) -> dict[str, object]:
    record = _package_keys(value, ("tar", "connector", "sidecar", "wheel", "manifest", "verified"))
    result = {}
    for key in ("tar", "connector", "sidecar", "wheel", "manifest"):
        item = record[key]
        if item is None:
            result[key] = None
        elif (type(item) is list and len(item) == 2 and type(item[0]) is int and 0 <= item[0] <= 536870912
              and type(item[1]) is str and re.fullmatch(r"[0-9a-f]{64}", item[1]) is not None):
            result[key] = [item[0], item[1]]
        else:
            raise diagnostic_error("P000024", "private_diagnostic_invalid", ValueError("private_diagnostic_invalid"))
    result["verified"] = _package_boolean(record["verified"])
    if result["verified"] is True and any(result[k] is None for k in ("tar", "connector", "sidecar", "wheel", "manifest")):
        raise diagnostic_error("P000025", "private_diagnostic_invalid", ValueError("private_diagnostic_invalid"))
    return result


def _managed_package_diagnostic_projection(value: object) -> dict[str, object]:
    # Historical records retain unknown origin; no site is backfilled.
    if type(value) is dict and "refusal" not in value:
        value = {**value, "refusal": None}
    if type(value) is dict and "boundary" not in value:
        value = {**value, "boundary": None}
    if type(value) is dict and "loss_sites" not in value:
        value = {**value, "loss_sites": []}
    d = _package_keys(value, tuple(_empty_managed_package_diagnostic()))
    result = {"reason": _package_literal(d["reason"], _PACKAGE_REASONS, nullable=True),
              "diagnostic_state": _package_literal(d["diagnostic_state"], _PACKAGE_DIAGNOSTIC_STATES),
              "helper_exit": _package_integer(d["helper_exit"], -64, 255),
              "deadline_elapsed": _package_boolean(d["deadline_elapsed"]),
              "kill_requested": _package_boolean(d["kill_requested"]),
              "reaped": _package_boolean(d["reaped"]), "reap_exit": _package_integer(d["reap_exit"], -64, 255),
              "outcome": _package_literal(d["outcome"], ("success", "refused", "deadline", "response_invalid", "unknown")),
              "process": _package_process_projection(d)}
    result["refusal"] = _package_refusal_projection(d["refusal"])
    result["boundary"] = _package_refusal_projection(d["boundary"])
    result["loss_sites"] = _package_loss_sites(d["loss_sites"])
    t = _package_keys(d["transport"], ("bytes_seen", "stdout_seen", "frames_seen", "eof", "read", "close", "losses"))
    transport = {"bytes_seen": _package_integer(t["bytes_seen"], 0, 4097),
                 "stdout_seen": _package_integer(t["stdout_seen"], 0, 4097),
                 "frames_seen": _package_integer(t["frames_seen"], 0, 4), "eof": _package_boolean(t["eof"]),
                 "read": _package_literal(t["read"], ("eof", "limit", "error", "cancelled", "not_started", "unavailable", "unknown")),
                 "close": _package_literal(t["close"], ("ok", "error", "not_opened", "unknown")),
                 "losses": _package_loss_projection(t["losses"])}
    if ((transport["bytes_seen"] or 0) + (transport["stdout_seen"] or 0) > 4097
            or transport["eof"] is True and transport["read"] != "eof"):
        raise diagnostic_error("P000026", "private_diagnostic_invalid", ValueError("private_diagnostic_invalid"))
    result["transport"] = transport
    stages = d["stages"]
    if type(stages) is not list or len(stages) > 3:
        raise diagnostic_error("P000027", "private_diagnostic_invalid", ValueError("private_diagnostic_invalid"))
    result["stages"] = []
    last = -1
    failed = False
    for triple in stages:
        if type(triple) is not list or len(triple) != 3:
            raise diagnostic_error("P000028", "private_diagnostic_invalid", ValueError("private_diagnostic_invalid"))
        stage = _package_literal(triple[0], _PACKAGE_STAGES)
        outcome = _package_literal(triple[1], ("success", "failure"))
        child = _managed_package_child_projection(triple[2])
        index = _PACKAGE_STAGES.index(stage)
        if index <= last or failed or outcome == "success" and (child["helper_exit"] != 0 or child["reaped"] is not True or child["deadline_elapsed"] is True or child["process"]["writer_close"] != "ok" or child["process"]["wait_error"] is not False):
            raise diagnostic_error("P000029", "private_diagnostic_invalid", ValueError("private_diagnostic_invalid"))
        last, failed = index, outcome == "failure"
        result["stages"].append([stage, outcome, child])
    if result["diagnostic_state"] in ("observed", "absent"):
        reason = result["reason"]
        if (transport["losses"] != [] or transport["eof"] is not True or transport["read"] != "eof"
                or transport["close"] != "ok" or transport["bytes_seen"] is None or transport["stdout_seen"] is None
                or transport["bytes_seen"] + transport["stdout_seen"] > 4096 or result["reaped"] is not True):
            raise diagnostic_error("P000030", "private_diagnostic_invalid", ValueError("private_diagnostic_invalid"))
        if result["diagnostic_state"] == "observed":
            if reason is None or result["helper_exit"] != 78:
                raise diagnostic_error("P000031", "private_diagnostic_invalid", ValueError("private_diagnostic_invalid"))
            if reason in _PACKAGE_STAGES:
                count = _PACKAGE_STAGES.index(reason) + 1
                if not failed or not result["stages"] or result["stages"][-1][0] != reason:
                    raise diagnostic_error("P000032", "private_diagnostic_invalid", ValueError("private_diagnostic_invalid"))
                frames = count
            else:
                count = (1 if reason in ("inhibit_policy", "inhibit_deadline", "manager_stop", "reacquire_pair", "stopped_admission_deadline", "stopped_current") else 2 if reason == "retire_policy" else 3 if reason in ("final_refresh", "final_current") else 0)
                if failed:
                    raise diagnostic_error("P000033", "private_diagnostic_invalid", ValueError("private_diagnostic_invalid"))
                frames = count + 1
        else:
            if reason is not None or result["helper_exit"] != 0 or failed:
                raise diagnostic_error("P000034", "private_diagnostic_invalid", ValueError("private_diagnostic_invalid"))
            count, frames = 3, 3
        frames += sum("guard" in triple[2] for triple in result["stages"])
        if (result["loss_sites"] or [v[0] for v in result["stages"]] != list(_PACKAGE_STAGES[:count])
                or transport["frames_seen"] != frames):
            raise diagnostic_error("P000035", "private_diagnostic_invalid", ValueError("private_diagnostic_invalid"))
    elif result["reason"] is not None:
        raise diagnostic_error("P000036", "private_diagnostic_invalid", ValueError("private_diagnostic_invalid"))
    return result


def _spawn_fixed_lifecycle(executable: str, operation: str, request: dict[str, object],
                           descriptor_map: Mapping[int, int], *,
                           _package_diagnostic: dict[str, object] | None = None) -> bytes:
    """Fixed executable/descriptors, bounded concurrent drain and actual reap.

    Diagnostics are a private observation of the unchanged operation. Owned
    descriptors are relinquished before once-only close; borrowed ones stay.
    """
    if (executable not in {_MANAGED_EXECUTABLE, "/proc/self/fd/5", "/proc/self/fd/6"}
            or executable == "/proc/self/fd/5" and operation != "managed-package"
            or executable == "/proc/self/fd/6" and operation != "managed-package-check"
            or executable != _MANAGED_EXECUTABLE and operation == "managed-lifecycle"
            or operation not in {"managed-package", "managed-run-check", "managed-stopped-check", "managed-package-check", "managed-lifecycle"}):
        raise diagnostic_error("P000037", "managed_lifecycle_operation_invalid", PackageError("managed_lifecycle_operation_invalid"))
    deadline = request["deadline_ns"]
    if type(deadline) is not int or deadline <= time.monotonic_ns():
        raise diagnostic_error("P000038", "managed_lifecycle_deadline", PackageError("managed_lifecycle_deadline"))
    with diagnostic_scope("P100012", "managed_operational_error"):
        raw = json.dumps(request, sort_keys=True, separators=(",", ":")).encode()
    if len(raw) > 4096 or time.monotonic_ns() >= deadline:
        raise diagnostic_error("P000039", "managed_lifecycle_refused", PackageError("managed_lifecycle_refused"))
    package_check = operation == "managed-package-check" and _package_diagnostic is not None
    diagnostic = (_package_diagnostic if _package_diagnostic is not None else {}) if operation == "managed-package" or package_check else None
    if diagnostic is not None:
        prior_sites = diagnostic.get("loss_sites", [])
        diagnostic.update(_empty_managed_package_diagnostic())
        diagnostic["loss_sites"] = prior_sites
        diagnostic["outcome"] = "refused"
    process = diagnostic["process"] if diagnostic is not None else {}
    transport = diagnostic["transport"] if diagnostic is not None else {}
    losses: set[str] = set()
    stages: list[list[object]] = []
    bare_refusal = None
    bare_boundary = None
    bare: str | None = None
    failure_seen = False
    frame = bytearray()
    frame_overflow = False
    diagnostic_bytes = diagnostic_lines = 0
    diagnostic_eof = False
    diagnostic_read = "not_started"
    diagnostic_close = "not_opened"
    result = bytearray()
    guards: dict[str, list[object]] = {}

    def loss(site: str, *codes: str, error: BaseException | None = None) -> None:
        losses.update(codes)
        from runtime.remote_access.package_diagnostics import exception_refusal
        origin = exception_refusal(error) if error is not None else None
        observed = [origin[1], site] if origin is not None and origin[1] != site else [site]
        if diagnostic is not None:
            for value in observed:
                if value not in diagnostic["loss_sites"] and len(diagnostic["loss_sites"]) < 4:
                    diagnostic["loss_sites"].append(value)

    def accept_frame(raw_frame: bytes) -> None:
        nonlocal bare, failure_seen, bare_refusal, bare_boundary
        if diagnostic_lines > 4:
            loss("P400003", "CX", "TL")
            return
        if raw_frame.startswith(b"managed_diagnostic_loss:"):
            try:
                sites = _package_loss_sites(raw_frame[24:-1].decode("ascii").split(":"))
                if not 1 <= len(sites) <= 2:
                    raise ValueError
            except (ValueError, UnicodeError) as error:
                loss("P400004", "TL", error=error)
            else:
                for site in sites:
                    loss(site, "TL")
            return
        if raw_frame.startswith(b"managed_package_guard:"):
            try:
                stage_raw, guard_raw = raw_frame[22:-1].split(b":", 1)
                stage = _package_literal(stage_raw.decode("ascii"), _PACKAGE_STAGES)
                guard = _package_guard_projection(_package_json(guard_raw, 512))
                if stage in guards or failure_seen or bare is not None:
                    loss("P400005", "CX", "TL")
                else:
                    guards[stage] = guard
            except (ValueError, UnicodeError, TypeError) as error:
                loss("P400006", "TL", error=error)
            return
        if raw_frame.startswith(b"managed_package_refusal:"):
            fields = raw_frame[:-1].split(b":")
            candidate_refusal = candidate_boundary = None
            if len(fields) in (3, 4):
                try:
                    with diagnostic_scope("P100013", "managed_operational_error"):
                        candidate_refusal = _package_refusal_projection({"category": "managed_lifecycle_refused", "site": fields[2].decode("ascii")})
                        if len(fields) == 4:
                            candidate_boundary = _package_refusal_projection({"category": "managed_lifecycle_refused", "site": fields[3].decode("ascii")})
                except (ValueError, UnicodeError) as error:
                    loss("P400008", "TL", error=error)
                    return
                raw_frame = b"managed_package_refusal:" + fields[1] + b"\n"
            with diagnostic_scope("P100014", "managed_operational_error"):
                reason = next((r for r in _PACKAGE_REASONS if raw_frame == b"managed_package_refusal:" + r.encode("ascii") + b"\n"), None)
            if reason is None:
                loss("P400009", "TL")
            elif bare is not None or failure_seen:
                loss("P400010", "CX")
            else:
                bare = reason
                bare_refusal, bare_boundary = candidate_refusal, candidate_boundary
            return
        try:
            success = raw_frame.startswith(b"managed_package_child_observation:")
            prefix = b"managed_package_child_observation:" if success else b"managed_package_child_refusal:"
            if not raw_frame.startswith(prefix):
                raise diagnostic_error("P000040", "managed_operational_error", ValueError)
            record = _package_json(raw_frame[len(prefix):-1])
            if type(record) is dict and "version" not in record and not success:
                # Literal historical H envelope. Validate its exact old grammar;
                # it supplies no new native stream or preceding success facts.
                old = _package_keys(record, ("reason", "child"))
                _package_literal(old["reason"], _PACKAGE_STAGES)
                child = _package_keys(old["child"], ("reason", "diagnostic_state", "helper_exit", "deadline_elapsed", "kill_requested", "reaped", "reap_exit"))
                state = _package_literal(child["diagnostic_state"], _PACKAGE_DIAGNOSTIC_STATES)
                for key in ("deadline_elapsed", "kill_requested", "reaped"):
                    if type(child[key]) is not bool:
                        raise diagnostic_error("P000041", "managed_operational_error", ValueError)
                for key in ("helper_exit", "reap_exit"):
                    _package_integer(child[key], -64, 255)
                if child["helper_exit"] != child["reap_exit"] or not child["reaped"] and child["helper_exit"] is not None:
                    raise diagnostic_error("P000042", "managed_operational_error", ValueError)
                if state == "observed":
                    _package_literal(child["reason"], _PACKAGE_PRIVATE_REASONS)
                    if not child["reaped"] or child["helper_exit"] != 78:
                        raise diagnostic_error("P000043", "managed_operational_error", ValueError)
                elif child["reason"] is not None:
                    raise diagnostic_error("P000044", "managed_operational_error", ValueError)
                loss("P400011", "TL")
                return
            key = "stage" if success else "reason"
            record = _package_keys(record, ("version", key, "child"))
            if type(record["version"]) is not int or record["version"] != 1:
                raise diagnostic_error("P000045", "managed_operational_error", ValueError)
            stage = _package_literal(record[key], _PACKAGE_STAGES)
            child = _managed_package_child_projection(record["child"])
            if stage in guards:
                child["guard"] = guards[stage]
            if (bare is not None or failure_seen or stages and _PACKAGE_STAGES.index(stage) <= _PACKAGE_STAGES.index(stages[-1][0])):
                loss("P400012", "CX")
                return
            if success and (child["helper_exit"] != 0 or child["reaped"] is not True or child["deadline_elapsed"] is True or child["process"]["wait_error"] is not False or child["process"]["writer_close"] != "ok"):
                loss("P400013", "CX")
                return
            stages.append([stage, "success" if success else "failure", child])
            failure_seen = not success
        except (ValueError, UnicodeError, PackageError, RecursionError, TypeError, KeyError) as error:
            loss("P400014", "TL", error=error)

    source_read = source_write = output_read = output_write = null = -1
    error_read = error_write = -1
    duplicates: list[int] = []
    writer_fds: set[int] = set()
    reader_fds: set[int] = set()
    writer_error = False

    def close_owned(descriptor: int) -> None:
        nonlocal diagnostic_close, writer_error
        is_writer = descriptor in writer_fds
        is_reader = descriptor in reader_fds
        writer_fds.discard(descriptor)
        reader_fds.discard(descriptor)
        try:
            with diagnostic_scope("P100015", "managed_operational_error"):
                os.close(descriptor)
        except OSError:
            if diagnostic is not None:
                loss("P400015", "CE")
                if is_reader:
                    diagnostic_close = "error"
                if is_writer:
                    writer_error = True
                    process["writer_close"] = "error"
            raise
        if diagnostic is not None:
            if is_reader and diagnostic_close != "error":
                diagnostic_close = "ok"
            if is_writer and not writer_fds and not writer_error:
                process["writer_close"] = "ok"

    child: int | None = None
    unwinding = False
    try:
        with diagnostic_scope("P200001", "managed_operational_error"):
            source_read, source_write = os.pipe2(os.O_CLOEXEC)
        writer_fds.add(source_write)
        process["writer_close"] = "unknown"
        with diagnostic_scope("P200002", "managed_operational_error"):
            output_read, output_write = os.pipe2(os.O_CLOEXEC)
        writer_fds.add(output_write)
        reader_fds.add(output_read)
        with diagnostic_scope("P100016", "managed_operational_error"):
            null = os.open(os.devnull, os.O_RDWR | os.O_CLOEXEC)
        if diagnostic is not None:
            with diagnostic_scope("P200003", "managed_operational_error"):
                error_read, error_write = os.pipe2(os.O_CLOEXEC)
            writer_fds.add(error_write)
            reader_fds.add(error_read)
            diagnostic_read, diagnostic_close = "unknown", "unknown"
        mapping = {0: source_read, 1: output_write,
                   2: error_write if diagnostic is not None else null, **descriptor_map}
        actions = []
        for target, source in mapping.items():
            with diagnostic_scope("P100017", "managed_operational_error"):
                duplicate = fcntl.fcntl(source, fcntl.F_DUPFD_CLOEXEC, 10)
            duplicates.append(duplicate)
            if target in (1, 2):
                writer_fds.add(duplicate)
            actions.append((os.POSIX_SPAWN_DUP2, duplicate, target))
        environment = {k: v for k, v in os.environ.items() if k != "NOTIFY_SOCKET"}
        if time.monotonic_ns() >= deadline:
            raise diagnostic_error("P000046", "managed_lifecycle_deadline", PackageError("managed_lifecycle_deadline"))
        process["launch"] = "failed"
        with diagnostic_scope("P200004", "managed_operational_error"):
            child = os.posix_spawn(executable, [executable, operation], environment, file_actions=actions)
        process.update(launch="started", wait="pending")
        while duplicates:
            close_owned(duplicates.pop())
        descriptor, output_write = output_write, -1
        close_owned(descriptor)
        if error_write >= 0:
            descriptor, error_write = error_write, -1
            close_owned(descriptor)
        with diagnostic_scope("P100018", "managed_operational_error"):
            if time.monotonic_ns() >= deadline or os.write(source_write, raw) != len(raw):
                raise diagnostic_error("P000047", "managed_lifecycle_refused", PackageError("managed_lifecycle_refused"))
        descriptor, source_write = source_write, -1
        close_owned(descriptor)
        with diagnostic_scope("P100019", "managed_operational_error"):
            os.set_blocking(output_read, False)
        with diagnostic_scope("P200005", "managed_operational_error"):
            with selectors.DefaultSelector() as selector:
                with diagnostic_scope("P200006", "managed_operational_error"):
                    selector.register(output_read, selectors.EVENT_READ)
                if diagnostic is not None:
                    with diagnostic_scope("P100020", "managed_operational_error"):
                        os.set_blocking(error_read, False)
                    with diagnostic_scope("P200007", "managed_operational_error"):
                        selector.register(error_read, selectors.EVENT_READ)
                while selector.get_map():
                    remaining = (deadline - time.monotonic_ns()) / 1_000_000_000
                    if remaining <= 0:
                        raise diagnostic_error("P000048", "managed_lifecycle_deadline", PackageError("managed_lifecycle_deadline"))
                    with diagnostic_scope("P200008", "managed_operational_error"):
                        ready = selector.select(remaining)
                    if not ready:
                        raise diagnostic_error("P000049", "managed_lifecycle_deadline", PackageError("managed_lifecycle_deadline"))
                    for key, _events in ready:
                        is_output = key.fd == output_read
                        available = 4096 - len(result) - diagnostic_bytes
                        if available < 0:
                            raise diagnostic_error("P000050", "managed_lifecycle_response_invalid", PackageError("managed_lifecycle_response_invalid"))
                        with diagnostic_scope("P100021", "managed_operational_error"):
                            piece = os.read(key.fd, available + 1 if is_output else min(256, available + 1))
                        if not piece:
                            with diagnostic_scope("P200009", "managed_operational_error"):
                                selector.unregister(key.fd)
                            if not is_output:
                                diagnostic_eof, diagnostic_read = True, "eof"
                                if frame or frame_overflow:
                                    loss("P400016", "PE", "TL")
                            continue
                        if is_output:
                            result.extend(piece)
                            if len(result) + diagnostic_bytes > 4096:
                                if diagnostic is not None:
                                    loss("P400017", "BL", "TL")
                                raise diagnostic_error("P000051", "managed_lifecycle_response_invalid", PackageError("managed_lifecycle_response_invalid"))
                        else:
                            diagnostic_bytes += len(piece)
                            if diagnostic_bytes + len(result) > 4096:
                                loss("P400018", "BL", "TL")
                                diagnostic_read = "limit"
                                frame.clear()
                                with diagnostic_scope("P200010", "managed_operational_error"):
                                    selector.unregister(error_read)
                                descriptor, error_read = error_read, -1
                                close_owned(descriptor)
                                continue
                            for byte in piece:
                                if len(frame) < 1024:
                                    frame.append(byte)
                                else:
                                    frame_overflow = True
                                if byte == 10:
                                    diagnostic_lines += 1
                                    if diagnostic_lines > 4:
                                        loss("P400019", "CX", "TL")
                                    elif frame_overflow:
                                        loss("P400020", "FL", "TL")
                                    else:
                                        accept_frame(bytes(frame))
                                    frame.clear()
                                    frame_overflow = False
        while True:
            try:
                with diagnostic_scope("P200011", "managed_operational_error"):
                    reaped, status = os.waitpid(child, os.WNOHANG)
            except BaseException:
                process.update(wait="failed", wait_error=True)
                raise
            if reaped:
                child = None
                with diagnostic_scope("P200012", "managed_operational_error"):
                    helper_exit = os.waitstatus_to_exitcode(status)
                elapsed = time.monotonic_ns() >= deadline
                process.update(wait="reaped", wait_error=helper_exit != 0)
                if diagnostic is not None:
                    diagnostic.update(helper_exit=helper_exit, deadline_elapsed=elapsed,
                                      reaped=True, reap_exit=helper_exit)
                if helper_exit != 0 or elapsed:
                    raise diagnostic_error("P000052", "managed_lifecycle_refused", PackageError("managed_lifecycle_refused"))
                if diagnostic is not None:
                    diagnostic["outcome"] = "success"
                return bytes(result)
            if time.monotonic_ns() >= deadline:
                raise diagnostic_error("P000053", "managed_lifecycle_deadline", PackageError("managed_lifecycle_deadline"))
            time.sleep(max(0, min(0.01, (deadline - time.monotonic_ns()) / 1_000_000_000)))
    except BaseException as exc:
        unwinding = True
        if diagnostic is not None:
            if isinstance(exc, PackageError) and "_initiating" not in diagnostic:
                category = next((c for c in _PACKAGE_ERROR_CATEGORIES if len(exc.args) == 1 and type(exc.args[0]) is str and exc.args[0] == c), None)
                diagnostic["_initiating"] = {"kind": "package_error", "category": category, "status": 1}
            if not diagnostic_eof and diagnostic_read != "limit":
                diagnostic_read = "cancelled" if isinstance(exc, (KeyboardInterrupt, SystemExit)) else "error"
                loss("P400021", "RE")
            if isinstance(exc, PackageError):
                diagnostic["outcome"] = {"managed_lifecycle_deadline": "deadline", "managed_lifecycle_response_invalid": "response_invalid"}.get(str(exc), "refused")
        raise
    finally:
        try:
            if child is not None:
                try:
                    process["kill"] = "requested"
                    if diagnostic is not None:
                        diagnostic["kill_requested"] = True
                    with diagnostic_scope("P100022", "managed_operational_error"):
                        os.kill(child, signal.SIGKILL)
                    process["kill"] = "sent"
                except ProcessLookupError:
                    process["kill"] = "not_found"
                except BaseException:
                    process["kill"] = "failed"
                    raise
                try:
                    with diagnostic_scope("P200013", "managed_operational_error"):
                        _pid, status = os.waitpid(child, 0)
                except BaseException:
                    process.update(wait="failed", wait_error=True)
                    raise
                process.update(wait="reaped", wait_error=True)
                if diagnostic is not None:
                    with diagnostic_scope("P200014", "managed_operational_error"):
                        diagnostic.update(reaped=True, reap_exit=os.waitstatus_to_exitcode(status))
        except BaseException:
            unwinding = True
            raise
        finally:
            remaining = [source_read, source_write, output_read, output_write,
                         error_read, error_write, null, *duplicates]
            duplicates.clear()
            close_error: OSError | None = None
            while remaining:
                descriptor = remaining.pop()
                if descriptor >= 0:
                    try:
                        close_owned(descriptor)
                    except OSError as exc:
                        if close_error is None:
                            close_error = exc
            if diagnostic is not None:
                diagnostic["deadline_elapsed"] = time.monotonic_ns() >= deadline if diagnostic["deadline_elapsed"] is None else diagnostic["deadline_elapsed"]
                if frame or frame_overflow:
                    loss("P400022", "TL")
                if not diagnostic_eof:
                    loss("P400023", "TL")
                # Reap after cancellation is still an actual helper exit.
                if diagnostic["helper_exit"] is None and diagnostic["reaped"] is True:
                    diagnostic["helper_exit"] = diagnostic["reap_exit"]
                helper_exit = diagnostic["helper_exit"]
                known_prefix = [triple[0] for triple in stages]
                expected = None
                reason = None
                if helper_exit == 0:
                    expected = [] if package_check else list(_PACKAGE_STAGES)
                    if bare is not None or failure_seen:
                        loss("P400024", "CX")
                elif helper_exit == 78:
                    if failure_seen:
                        reason = stages[-1][0]
                        expected = list(_PACKAGE_STAGES[:_PACKAGE_STAGES.index(reason) + 1])
                    elif bare is not None:
                        reason = bare
                        count = (1 if bare in ("inhibit_policy", "inhibit_deadline", "manager_stop", "reacquire_pair", "stopped_admission_deadline", "stopped_current") else 2 if bare == "retire_policy" else 3 if bare in ("final_refresh", "final_current") else 0)
                        expected = [] if package_check else list(_PACKAGE_STAGES[:count])
                        if bare in _PACKAGE_STAGES:
                            loss("P400025", "TL")
                    else:
                        loss("P400026", "TL")
                elif bare is not None or failure_seen:
                    loss("P400027", "CX")
                if expected is None or known_prefix != expected:
                    loss("P400028", "TL")
                if process.get("writer_close") == "error":
                    loss("P400029", "TL")
                if any(stage not in known_prefix for stage in guards):
                    loss("P400007", "TL")
                if diagnostic["loss_sites"]:
                    losses.add("TL")
                diagnostic.update(reason=reason if not losses else None,
                                  diagnostic_state=("observed" if reason else "absent") if not losses else "contradictory" if "CX" in losses else "unavailable",
                                  stages=stages)
                diagnostic["refusal"] = bare_refusal or next((triple[2].get("refusal") for triple in reversed(stages) if triple[1] == "failure"), None)
                diagnostic["boundary"] = bare_boundary or next((triple[2].get("boundary") for triple in reversed(stages) if triple[1] == "failure"), None)
                transport.update(bytes_seen=diagnostic_bytes, stdout_seen=len(result), frames_seen=min(diagnostic_lines, 4), eof=diagnostic_eof,
                                 read=diagnostic_read, close=diagnostic_close, losses=[code for code in _PACKAGE_LOSSES if code in losses])
                if close_error is not None and not unwinding:
                    diagnostic["outcome"] = "refused"
            if close_error is not None and not unwinding:
                raise diagnostic_error("P000054", "managed_lifecycle_refused", PackageError("managed_lifecycle_refused")) from close_error


def _managed_guard_probe(kind: str, descriptor: int, deadline: int) -> None:
    request = {"version": 2, "operation": kind, "operation_id": secrets.token_hex(16), "deadline_ns": deadline}
    executable = "/proc/self/fd/6" if kind == "package" else _MANAGED_EXECUTABLE
    mapping = {3: descriptor, 6: 6} if kind == "package" else {3: descriptor}
    guard_diagnostic = _empty_managed_package_diagnostic() if kind == "package" else None
    try:
        _spawn_fixed_lifecycle(executable, "managed-" + kind + "-check", request, mapping,
                               _package_diagnostic=guard_diagnostic)
        if time.monotonic_ns() >= deadline:
            raise diagnostic_error("P000055", "managed_lifecycle_deadline", PackageError("managed_lifecycle_deadline"))
    except Exception as error:
        if guard_diagnostic is not None:
            from runtime.remote_access.package_diagnostics import diagnostic_propagate
            diagnostic_propagate(guard_diagnostic.get("refusal"), error)
            p, t = guard_diagnostic.get("process"), guard_diagnostic.get("transport")
            if type(p) is dict and type(t) is dict:
                error._managed_package_guard = _package_guard_projection([
                    guard_diagnostic["diagnostic_state"], guard_diagnostic["helper_exit"],
                    guard_diagnostic["deadline_elapsed"], guard_diagnostic["kill_requested"],
                    guard_diagnostic["reaped"], guard_diagnostic["reap_exit"],
                    p["launch"], p["wait"], p["wait_error"], p["kill"], p["writer_close"],
                    t["bytes_seen"], t["stdout_seen"], t["frames_seen"], t["eof"],
                    t["read"], t["close"], t["losses"], guard_diagnostic["loss_sites"],
                    (guard_diagnostic["boundary"]["site"] if guard_diagnostic["boundary"] is not None else None),
                ])
        raise


@contextmanager
def managed_body_guard(kind: str, *, entry_ns: int | None = None):
    """BODY-owned fixed helper exclusion, including direct Python calls."""
    if kind not in {"run", "stopped"}:
        raise diagnostic_error("P000056", "managed_lifecycle_operation_invalid", PackageError("managed_lifecycle_operation_invalid"))
    previous = _MANAGED_LOCK.get()
    deadline = _managed_entry_deadline(60 if kind == "stopped" else 120, entry_ns=entry_ns)
    if kind == "run":
        # This observation ONLY shortens preparation/lock waiting. The Go
        # guard still proves the actual current ControlPID ancestry and the
        # independent installed/loaded reader still supplies authority.
        with diagnostic_scope("P100023", "managed_operational_error"):
            from runtime.remote_access.cli import _capture_bounded_query_output, _close_and_reap_query_process
        bound = min(deadline / 1_000_000_000, time.monotonic() + 1)
        if time.monotonic() >= bound:
            raise diagnostic_error("P000057", "managed_lifecycle_deadline", PackageError("managed_lifecycle_deadline"))
        with diagnostic_scope("P100024", "managed_operational_error"):
            child = subprocess.Popen(["/usr/bin/systemctl", "show", "happyranch-tsnet-sidecar.service",
                                      "--property=InvocationID", "--property=InactiveExitTimestampMonotonic"],
                                     stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                     pass_fds=(() if previous is None else (previous,)),
                                     env={"PATH": "/usr/bin:/bin", "LANG": "C", "LC_ALL": "C"})
        try:
            raw, overflow = _capture_bounded_query_output(child, bound)
            remaining = bound - time.monotonic()
            with diagnostic_scope("P100025", "managed_operational_error"):
                if overflow or remaining <= 0 or child.wait(timeout=remaining) != 0:
                    raise diagnostic_error("P000058", "managed_lifecycle_refused", PackageError("managed_lifecycle_refused"))
        finally:
            with diagnostic_scope("P100026", "managed_operational_error"):
                _close_and_reap_query_process(child, bound, managed=True)
        try:
            with diagnostic_scope("P100027", "managed_operational_error"):
                rows = raw.decode("ascii").split("\n")
            values = dict(row.split("=", 1) for row in rows[:-1])
        except ValueError as exc:
            raise diagnostic_error("P000059", "managed_lifecycle_refused", PackageError("managed_lifecycle_refused")) from exc
        if (len(rows) != 3 or rows[-1] or set(values) != {"InvocationID", "InactiveExitTimestampMonotonic"}
                or values["InvocationID"] != os.environ.get("INVOCATION_ID")
                or re.fullmatch(r"[0-9a-f]{32}", values["InvocationID"]) is None
                or values["InvocationID"] == "0" * 32
                or re.fullmatch(r"[1-9][0-9]{0,15}", values["InactiveExitTimestampMonotonic"]) is None):
            raise diagnostic_error("P000060", "managed_lifecycle_refused", PackageError("managed_lifecycle_refused"))
        with diagnostic_scope("P100028", "managed_operational_error"):
            activation = int(values["InactiveExitTimestampMonotonic"]) * 1000
        now = time.monotonic_ns()
        deadline = min(deadline, activation + 120_000_000_000)
        if activation > now or now >= deadline or time.monotonic() >= bound:
            raise diagnostic_error("P000061", "managed_lifecycle_deadline", PackageError("managed_lifecycle_deadline"))
    if previous is not None:
        deadline_token = _MANAGED_DEADLINE.set(deadline)
        try:
            _check_managed_holder()
            declaration = _installed_managed_contract(deadline_ns=deadline)
            declaration_token = _MANAGED_DECLARATION.set(declaration)
            _managed_guard_probe(kind, previous, deadline)
            _check_managed_holder()
            try:
                yield deadline
            finally:
                _check_managed_holder()
                _installed_managed_contract(deadline_ns=deadline)
                _managed_guard_probe(kind, previous, deadline)
                _check_managed_holder()
        finally:
            if "declaration_token" in locals():
                _MANAGED_DECLARATION.reset(declaration_token)
            _MANAGED_DEADLINE.reset(deadline_token)
        return
    with diagnostic_scope("P100029", "managed_operational_error"):
        descriptor = os.open("/var/lib/happyranch-connector/lifecycle.lock", os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    deadline_token = _MANAGED_DEADLINE.set(deadline)
    lock_token = _MANAGED_LOCK.set(descriptor)
    try:
        while True:
            try:
                with diagnostic_scope("P100030", "managed_operational_error"):
                    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic_ns() >= deadline:
                    raise diagnostic_error("P000062", "managed_lifecycle_deadline", PackageError("managed_lifecycle_deadline"))
                time.sleep(0.01)
        _check_managed_holder()
        declaration = _installed_managed_contract(deadline_ns=deadline)
        declaration_token = _MANAGED_DECLARATION.set(declaration)
        _managed_guard_probe(kind, descriptor, deadline)
        _check_managed_holder()
        try:
            yield deadline
        finally:
            # Failure paths retain the same holder, and cannot skip the final
            # deadline/current-owner check merely because the body raised.
            _check_managed_holder()
            _installed_managed_contract(deadline_ns=deadline)
            _managed_guard_probe(kind, descriptor, deadline)
            _check_managed_holder()
    finally:
        if "declaration_token" in locals():
            _MANAGED_DECLARATION.reset(declaration_token)
        # No LOCK_UN: borrowed descriptions held by reload/query descendants
        # retain exclusion until their actual completion, even on parent loss.
        _MANAGED_LOCK.reset(lock_token)
        _MANAGED_DEADLINE.reset(deadline_token)
        with diagnostic_scope("P100031", "managed_operational_error"):
            os.close(descriptor)


def _package_sealing_constants() -> tuple[int, int, int, int]:
    # CPython exports depend on build headers, including on supported 3.12-3.14.
    # Linux UAPI: asm-generic/fcntl.h base=1024; linux/fcntl.h ADD=base+9,
    # GET=base+10, SEAL/SHRINK/GROW/WRITE=1/2/4/8; linux/memfd.h flags=1/2.
    # These are Linux ABI arguments only, never an emulation of missing support.
    if sys.platform != "linux":
        raise diagnostic_error("P000063", "package_input_invalid", PackageError("package_input_invalid"))
    required = (getattr(fcntl, "F_SEAL_WRITE", 0x0008)
                | getattr(fcntl, "F_SEAL_GROW", 0x0004)
                | getattr(fcntl, "F_SEAL_SHRINK", 0x0002)
                | getattr(fcntl, "F_SEAL_SEAL", 0x0001))
    flags = getattr(os, "MFD_CLOEXEC", 0x0001) | getattr(os, "MFD_ALLOW_SEALING", 0x0002)
    return (getattr(fcntl, "F_ADD_SEALS", 1033),
            getattr(fcntl, "F_GET_SEALS", 1034), required, flags)


def _managed_package_outer(package: Path | None, operation: str, deadline: int) -> dict[str, object]:
    if os.geteuid() != 0 or operation not in {"package-install", "package-uninstall"}:
        raise diagnostic_error("P000064", "managed_package_context_required", PackageError("managed_package_context_required"))
    if operation == "package-install":
        with diagnostic_scope("P700001", "managed_operational_error", boundary=True):
            require_credential_capability(Path("/etc/happyranch/daemon.token"), expected_uid=os.geteuid())
        enrollment = Path("/etc/happyranch/enrollment.key")
        with diagnostic_scope("P700002", "managed_operational_error"):
            present = _backup_present(enrollment)
        if present:
            with diagnostic_scope("P700003", "managed_operational_error", boundary=True):
                require_credential_capability(enrollment, expected_uid=os.geteuid())
    descriptors: list[int] = []

    def sealed(name: str, raw: bytes, executable: bool = False) -> int:
        add_seals, _get_seals, required, flags = _package_sealing_constants()
        create = getattr(os, "memfd_create", None)
        if not callable(create):
            raise diagnostic_error("P000065", "package_input_invalid", PackageError("package_input_invalid"))
        try:
            descriptor = create(name, flags)
            descriptors.append(descriptor)
            view = memoryview(raw)
            while view:
                with diagnostic_scope("P100032", "managed_operational_error"):
                    count = os.write(descriptor, view)
                if count <= 0:
                    raise diagnostic_error("P000066", "package_input_invalid", PackageError("package_input_invalid"))
                view = view[count:]
            with diagnostic_scope("P100033", "managed_operational_error"):
                os.fchmod(descriptor, 0o755 if executable else 0o600)
            with diagnostic_scope("P200015", "managed_operational_error"):
                os.lseek(descriptor, 0, os.SEEK_SET)
            with diagnostic_scope("P100034", "managed_operational_error"):
                fcntl.fcntl(descriptor, add_seals, required)
        except OSError as exc:
            raise diagnostic_error("P000067", "package_input_invalid", PackageError("package_input_invalid")) from exc
        return descriptor

    unwinding = False
    try:
        if package is not None:
            # Authenticate ONE immutable incoming image before using its two
            # fixed existing executables. Go prepares all three private named
            # connector copies under original A/current proof before admission;
            # package extraction/transaction/temp/stage still follows B.
            _require_package_source(package)
            with diagnostic_scope("P100035", "managed_operational_error"):
                incoming_bytes = package.read_bytes()
            diagnostic = _MANAGED_PACKAGE_DIAGNOSTIC.get()
            identities = {key: None for key in ("tar", "connector", "sidecar", "wheel", "manifest", "verified")}
            if diagnostic is not None:
                diagnostic["identities"] = identities
                if len(incoming_bytes) <= 536870912:
                    identities["tar"] = [len(incoming_bytes), hashlib.sha256(incoming_bytes).hexdigest()]
            incoming = sealed("happyranch-incoming-package", incoming_bytes)
            del incoming_bytes
            try:
                files, _manifest = _read_verified(Path(f"/proc/self/fd/{incoming}"))
            except BaseException:
                if diagnostic is not None and sys.exc_info()[0] not in (SystemExit, KeyboardInterrupt):
                    identities["verified"] = False
                raise
            if diagnostic is not None:
                # These exact immutable members are subsequently sealed below.
                for key, member in (("connector", "bin/happyranch-connector"), ("sidecar", "bin/happyranch-tsnet-sidecar"), ("wheel", "share/happyranch.whl"), ("manifest", "manifest.json")):
                    member_bytes = files[member]
                    if len(member_bytes) <= 536870912:
                        identities[key] = [len(member_bytes), hashlib.sha256(member_bytes).hexdigest()]
                identities["verified"] = all(identities[key] is not None for key in ("tar", "connector", "sidecar", "wheel", "manifest"))
            connector = sealed("happyranch-connector", files["bin/happyranch-connector"], True)
            executable = sealed("happyranch-tsnet-sidecar", files["bin/happyranch-tsnet-sidecar"], True)
            mapping = {3: incoming, 4: connector, 5: executable}
            path = "/proc/self/fd/5"
        else:
            # Uninstall still needs the fixed incoming slots, never arbitrary
            # payload selection. These are the current installed executables.
            _installed_managed_contract(deadline_ns=deadline, _package=True)
            prefix, _units, _dropin, _empty_prior = _package_managed_history(deadline)
            if prefix is None:
                raise diagnostic_error("P000068", "managed_lifecycle_incompatible", PackageError("managed_lifecycle_incompatible"))
            incoming = sealed("happyranch-unused-package", b"unused")
            with diagnostic_scope("P100036", "managed_operational_error"):
                connector = sealed("happyranch-connector", (prefix / "bin/happyranch-connector").read_bytes(), True)
            with diagnostic_scope("P100037", "managed_operational_error"):
                executable = sealed("happyranch-tsnet-sidecar", (prefix / "bin/happyranch-tsnet-sidecar").read_bytes(), True)
            mapping = {3: incoming, 4: connector, 5: executable}
            path = "/proc/self/fd/5"
        request = {"version": 2, "operation": operation, "operation_id": secrets.token_hex(16), "deadline_ns": deadline}
        raw = _spawn_fixed_lifecycle(path, "managed-package", request, mapping,
                                     _package_diagnostic=_MANAGED_PACKAGE_DIAGNOSTIC.get())
        try:
            with diagnostic_scope("P100038", "managed_operational_error"):
                result = json.loads(raw, object_pairs_hook=_reject_duplicate_keys)
            if not isinstance(result, dict):
                raise diagnostic_error("P000069", "managed_lifecycle_response_invalid", PackageError("managed_lifecycle_response_invalid"))
        except BaseException:
            diagnostic = _MANAGED_PACKAGE_DIAGNOSTIC.get()
            if diagnostic is not None:
                diagnostic["outcome"] = "response_invalid"
            raise
        return result
    except BaseException as exc:
        unwinding = True
        diagnostic = _MANAGED_PACKAGE_DIAGNOSTIC.get()
        if diagnostic is not None and diagnostic.get("refusal") is None:
            from runtime.remote_access.package_diagnostics import exception_refusal
            refusal = exception_refusal(exc)
            if refusal is not None:
                diagnostic["refusal"] = {"category": refusal[0], "site": refusal[1]}
                loaded = exception_loaded(exc)
                if loaded is not None:
                    diagnostic["refusal"]["loaded"] = loaded
                marked = getattr(exc, "_managed_package_boundary", getattr(exc, "_managed_package_refusal", None))
                if marked is not None and marked != refusal:
                    diagnostic["boundary"] = {"category": marked[0], "site": marked[1]}
        if diagnostic is not None and isinstance(exc, PackageError) and "_initiating" not in diagnostic:
            category = next((c for c in _PACKAGE_ERROR_CATEGORIES if len(exc.args) == 1 and type(exc.args[0]) is str and exc.args[0] == c), None)
            diagnostic["_initiating"] = {"kind": "package_error", "category": category, "status": 1}
        raise
    finally:
        close_error: OSError | None = None
        while descriptors:
            # These originals belong to this caller, not the spawn helper.
            # Relinquish before close; never retry a possibly reused number.
            descriptor = descriptors.pop()
            try:
                with diagnostic_scope("P100039", "managed_operational_error"):
                    os.close(descriptor)
            except OSError as exc:
                diagnostic_loss("P400035", "managed_operational_error", exc)
                if close_error is None:
                    close_error = exc
        if close_error is not None and not unwinding:
            diagnostic = _MANAGED_PACKAGE_DIAGNOSTIC.get()
            if diagnostic is not None:
                diagnostic["outcome"] = "refused"
            raise diagnostic_error("P000070", "managed_lifecycle_refused", PackageError("managed_lifecycle_refused")) from close_error


def _nonmanaged_owner_destination(path: Path) -> Path:
    """Resolve an existing destination or a missing tail from a proven parent.

    Missing files may be created by the existing generic writer. A dangling
    link, inaccessible parent, non-directory ancestor or unresolved parent
    component is unknown ownership, never permission to stage over it.
    """
    missing: list[str] = []
    current = Path(path)
    try:
        while True:
            try:
                current.lstat()
            except FileNotFoundError:
                if current.parent == current or current.name in {"", ".", ".."}:
                    raise diagnostic_error("P600002", "managed_lifecycle_owner_unknown", PackageError("managed_lifecycle_owner_unknown"))
                missing.append(current.name)
                current = current.parent
                continue
            except (OSError, RuntimeError, ValueError) as exc:
                # Missing tails above are ordinary; only actual errors own a site.
                diagnostic_error("P600001", "managed_operational_error", exc)
                raise
            with diagnostic_scope("P600003", "managed_operational_error"):
                resolved = current.resolve(strict=True)
            with diagnostic_scope("P600004", "managed_operational_error"):
                if missing and not resolved.is_dir():
                    raise diagnostic_error("P600005", "managed_lifecycle_owner_unknown", PackageError("managed_lifecycle_owner_unknown"))
            return resolved.joinpath(*reversed(missing))
    except (OSError, RuntimeError, ValueError) as exc:
        # Keep this public wrapper and cause; the deepest marked origin wins.
        raise diagnostic_error("P600006", "managed_lifecycle_owner_unknown", PackageError("managed_lifecycle_owner_unknown")) from exc


def _nonmanaged_owner_same_path(left: Path, right: Path) -> bool:
    if left == right:
        return True
    identities = []
    for path in (left, right):
        try:
            metadata = path.stat()
        except FileNotFoundError:
            identities.append(None)
        except OSError as exc:
            diagnostic_error("P600007", "managed_operational_error", exc)
            raise diagnostic_error("P600008", "managed_lifecycle_owner_unknown", PackageError("managed_lifecycle_owner_unknown")) from exc
        else:
            identities.append((metadata.st_dev, metadata.st_ino))
    return identities[0] is not None and identities[0] == identities[1]


def require_nonmanaged_unit_owner(unit_name: str, unit_dir: Path, *,
                                  user_unit_dir: Path | None = None) -> None:
    """Fence generic destinations; default callers retain local-marker refusal.

    The generic USER manager may identify its existing canonical unit directory.
    Ordinary units there are not local package markers merely by their names.
    This context never releases fixed identities, state custody or other paths.
    """
    # State custody is independent of unit-name/marker classification. A fixed
    # local name with absent markers still cannot write into either state tree.
    _require_nonmanaged_state_owner(Path(unit_dir) / unit_name)
    directory = _nonmanaged_owner_destination(unit_dir)
    destination = _nonmanaged_owner_destination(Path(unit_dir) / unit_name)
    fixed_directory = _nonmanaged_owner_destination(Path("/etc/systemd/system"))
    fixed_units = tuple(_nonmanaged_owner_destination(fixed_directory / name) for name in UNITS)
    local_units = tuple(_nonmanaged_owner_destination(directory / name) for name in UNITS)
    fixed = any(_nonmanaged_owner_same_path(destination, path) for path in fixed_units)
    local = any(_nonmanaged_owner_same_path(destination, path) for path in local_units)
    if not (fixed or local):
        return
    # Choose markers from the actual owner directory. An alternate spelling
    # of the fixed directory must never suppress its durable lifecycle marker.
    fixed = fixed or _nonmanaged_owner_same_path(directory, fixed_directory)
    owner_directory = fixed_directory if fixed else directory
    if (not fixed and user_unit_dir is not None
            and Path(unit_dir) == directory == user_unit_dir):
        # A positively separate canonical USER namespace may contain any of
        # these ordinary unit names, including siblings installed sequentially.
        # Recheck concrete custody, including after write; system=False alone
        # cannot release markers in an arbitrary writer/fragment directory.
        _require_nonmanaged_state_owner(Path(unit_dir) / unit_name,
                                        staging=True, directory=False)
        return
    markers = (owner_directory / UNITS[1], owner_directory / UNITS[2])
    if fixed:
        markers += (Path("/var/lib/happyranch-connector/lifecycle.json"),
                    Path("/var/lib/happyranch-tsnet-sidecar/lifecycle.json"))
    for path in markers:
        marker = _nonmanaged_owner_destination(path)
        try:
            marker.lstat()
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise PackageError("managed_lifecycle_owner_unknown") from exc
        raise PackageError("managed_lifecycle_owner_required")


def _require_nonmanaged_state_owner(root: Path, *, staging: bool = False,
                                     directory: bool | None = None) -> None:
    """Exclude concrete destinations from BOTH fixed state trees.

    Only local metadata is used. Multiple links to a regular inode cannot
    establish independent custody without searching protected contents, so
    refuse them. Staging additionally requires canonical, nonlinked ordinary
    files/directories; this is exclusion, never a managed-authority grant.
    """
    destination = _nonmanaged_owner_destination(root)
    if staging and (".." in Path(root).parts
                    or destination != Path(os.path.abspath(root))):
        raise diagnostic_error("P600009", "managed_lifecycle_owner_unknown", PackageError("managed_lifecycle_owner_unknown"))
    for fixed_root in (Path("/var/lib/happyranch-connector"),
                       Path("/var/lib/happyranch-tsnet-sidecar")):
        fixed = _nonmanaged_owner_destination(fixed_root)
        # Ancestors exclude recursive ownership of a containing directory;
        # existing directory inodes also cover equivalent parent spellings.
        overlaps = any(_nonmanaged_owner_same_path(parent, fixed)
                       for parent in (destination, *destination.parents))
        overlaps = overlaps or any(_nonmanaged_owner_same_path(destination, parent)
                                   for parent in fixed.parents)
        left, right = destination, fixed
        while left.name == right.name and left.parent != left and right.parent != right:
            left, right = left.parent, right.parent
            overlaps = overlaps or _nonmanaged_owner_same_path(left, right)
        if overlaps:
            # Missing lifecycle/unit markers never release fixed state custody.
            raise diagnostic_error("P600010", "managed_lifecycle_owner_required", PackageError("managed_lifecycle_owner_required"))
    try:
        metadata = Path(root).lstat() if staging else destination.stat()
    except FileNotFoundError:
        # _nonmanaged_owner_destination already proved the existing parent and
        # refused dangling links. Only an ordinary missing tail can be created.
        return
    except OSError as exc:
        diagnostic_error("P600011", "managed_operational_error", exc)
        raise diagnostic_error("P600012", "managed_lifecycle_owner_unknown", PackageError("managed_lifecycle_owner_unknown")) from exc
    regular, is_directory = stat.S_ISREG(metadata.st_mode), stat.S_ISDIR(metadata.st_mode)
    if (not (regular or is_directory) or (regular and metadata.st_nlink != 1)
            or (directory is True and not is_directory)
            or (directory is False and not regular)):
        raise diagnostic_error("P600013", "managed_lifecycle_owner_unknown", PackageError("managed_lifecycle_owner_unknown"))


def managed_lifecycle(operation: str, *, expected_installation: str | None = None,
                      expected_revision: int | None = None, operation_id: str | None = None,
                      deadline_ns: int | None = None) -> dict[str, object]:
    """The one fixed owner seam for existing lifecycle verbs and reconcile.

    A caller may shorten an existing deadline; no retry or error unwind gets a
    new allowance. IDs are generated once here and never automatically replayed.
    """
    entry_ns = time.monotonic_ns()
    if operation not in {"start", "stop", "restart", "enable", "disable", "status", "reconcile"}:
        raise diagnostic_error("P000071", "managed_lifecycle_operation_invalid", PackageError("managed_lifecycle_operation_invalid"))
    deadline = _managed_entry_deadline(45, entry_ns=entry_ns)
    if deadline_ns is not None:
        deadline = min(deadline, deadline_ns)
    if deadline <= time.monotonic_ns():
        raise diagnostic_error("P000072", "managed_lifecycle_deadline", PackageError("managed_lifecycle_deadline"))
    request = {"version": 2, "operation": operation, "expected_installation": expected_installation,
               "expected_revision": expected_revision, "operation_id": operation_id or secrets.token_hex(16),
               "deadline_ns": deadline}
    try:
        raw = _spawn_fixed_lifecycle(_MANAGED_EXECUTABLE, "managed-lifecycle", request, {})
        with diagnostic_scope("P100040", "managed_operational_error"):
            report = json.loads(raw, object_pairs_hook=_reject_duplicate_keys)
        if (not isinstance(report, dict) or set(report) != {"version", "state", "operation_id"}
                or type(report["version"]) is not int or report["version"] != 2
                or report["operation_id"] != request["operation_id"]
                or report["state"] not in {"stopped", "boot_prepared", "starting", "ready", "retiring", "deferred", "rate_exhausted_until", "blocked", "unavailable"}):
            raise diagnostic_error("P000073", "managed_lifecycle_response_invalid", PackageError("managed_lifecycle_response_invalid"))
        if time.monotonic_ns() >= deadline:
            raise diagnostic_error("P000074", "managed_lifecycle_deadline", PackageError("managed_lifecycle_deadline"))
        return report
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        raise diagnostic_error("P000075", "managed_lifecycle_refused", PackageError("managed_lifecycle_refused")) from exc


@contextmanager
def _credential_observation(site: str, category: str):
    """Attribute only errors observed by this invocation, never ambient context."""
    try:
        yield
    except Exception as error:
        origin = (category, site)
        record = _CREDENTIAL_REFUSAL.get()
        if record is not None:
            cause = error.__cause__ or (None if error.__suppress_context__ else error.__context__)
            if cause is not None and cause is record.get("error"):
                origin = record["origin"]
            record["error"], record["origin"] = error, origin
        error._managed_package_refusal = origin
        error._managed_package_propagated_refusal = True
        raise


def _credential_classification(site: str, category: str, error: BaseException | None = None) -> str:
    """Keep the returned classification separate from its private source identity."""
    record = _CREDENTIAL_REFUSAL.get()
    if record is not None:
        # A finally-close failure can override a pending return, exactly as before.
        # Chained read/close errors retain their earliest marked actual exception.
        if error is None or error is not record.get("error"):
            record["origin"] = (category, site)
    return category


def credential_capability(
    source: Path,
    *,
    expected_uid: int | None,
    allowed_modes: tuple[int, ...] | None = (0o600,),
    require_read_only: bool = False,
) -> str:
    """Classify credential usability without exposing paths, bytes, or OS errors."""
    with _credential_observation("P700004", "managed_operational_error"):
        path = Path(source)
    try:
        current = path
        while current != current.parent:
            metadata = current.lstat()
            if stat.S_ISLNK(metadata.st_mode):
                return _credential_classification("P700010", "credential_unsafe_symlink")
            current = current.parent
    except FileNotFoundError:
        return _credential_classification("P700011", "credential_absent")
    except OSError:
        return _credential_classification("P700012", "credential_staging_incompatible")
    try:
        with _credential_observation("P700013", "credential_staging_incompatible"):
            metadata = path.lstat()
        if not stat.S_ISREG(metadata.st_mode):
            return _credential_classification("P700014", "credential_wrong_type")
        if expected_uid is not None and metadata.st_uid != expected_uid:
            return _credential_classification("P700015", "credential_wrong_custody")
        if allowed_modes is not None and stat.S_IMODE(metadata.st_mode) not in allowed_modes:
            return _credential_classification("P700016", "credential_wrong_custody")
        with _credential_observation("P700017", "credential_staging_incompatible"):
            descriptor = os.open(
                path,
                os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
            )
        try:
            with _credential_observation("P700018", "credential_staging_incompatible"):
                empty = not os.read(descriptor, 1)
            if empty:
                return _credential_classification("P700019", "credential_staging_incompatible")
        finally:
            with _credential_observation("P700020", "credential_staging_incompatible"):
                os.close(descriptor)
        if require_read_only:
            try:
                descriptor = os.open(
                    path,
                    os.O_WRONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
                )
            except OSError:
                pass
            else:
                with _credential_observation("P700022", "credential_staging_incompatible"):
                    os.close(descriptor)
                return _credential_classification("P700023", "credential_staging_incompatible")
    except OSError as error:
        return _credential_classification("P700024", "credential_staging_incompatible", error)
    return "credential_valid"


def require_credential_capability(
    source: Path,
    *,
    expected_uid: int,
    allowed_modes: tuple[int, ...] = (0o600,),
) -> None:
    record: dict = {}
    token = _CREDENTIAL_REFUSAL.set(record)
    try:
        category = credential_capability(
            source, expected_uid=expected_uid, allowed_modes=allowed_modes
        )
    finally:
        _CREDENTIAL_REFUSAL.reset(token)
    if category != "credential_valid":
        from runtime.remote_access.package_diagnostics import diagnostic_propagate
        error = diagnostic_error("P700025", "managed_operational_error", PackageError(category))
        origin = record.get("origin")
        if origin is not None:
            diagnostic_propagate({"category": origin[0], "site": origin[1]}, error)
        raise error


class CompositeServiceManager:
    """Injectable executable seam for the shipping composite systemd target."""

    def __init__(self, run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
                 systemctl: str = "systemctl") -> None:
        self._run, self._systemctl = run, systemctl

    def _call(self, *args: str) -> subprocess.CompletedProcess[str]:
        try:
            deadline = _MANAGED_DEADLINE.get()
            if deadline is None or time.monotonic_ns() >= deadline:
                raise diagnostic_error("P000076", "managed_lifecycle_deadline", PackageError("managed_lifecycle_deadline"))
            argv = ["/usr/bin/systemctl" if self._systemctl == "systemctl" else self._systemctl, *args]
            result = self._run(argv, check=True, text=True, capture_output=True,
                               timeout=max(0, (deadline - time.monotonic_ns()) / 1_000_000_000),
                               env={"PATH": "/usr/bin:/bin", "LANG": "C", "LC_ALL": "C"},
                               pass_fds=(() if _MANAGED_LOCK.get() is None else (_MANAGED_LOCK.get(),)))
            if time.monotonic_ns() >= deadline:
                raise diagnostic_error("P000077", "managed_lifecycle_deadline", PackageError("managed_lifecycle_deadline"))
            return result
        except (OSError, subprocess.SubprocessError) as exc:
            raise diagnostic_error("P000078", "service_manager_failed", PackageError("service_manager_failed")) from exc

    def start_ready(self) -> None:
        deadline = _managed_entry_deadline(45)
        token = _MANAGED_DEADLINE.set(deadline)
        try:
            report = managed_lifecycle("start")
            if report["state"] != "ready":
                raise diagnostic_error("P000079", "service_not_ready", PackageError("service_not_ready"))
            for unit in UNITS[:2]:
                result = self._call("show", unit, "--property=ActiveState", "--value")
                if result.stdout.strip() != "active":
                    raise diagnostic_error("P000080", "service_not_ready", PackageError("service_not_ready"))
            if time.monotonic_ns() >= deadline:
                raise diagnostic_error("P000081", "managed_lifecycle_deadline", PackageError("managed_lifecycle_deadline"))
        finally:
            _MANAGED_DEADLINE.reset(token)

    def stop(self) -> None:
        managed_lifecycle("stop")

    def restart_after_crash(self, unit: str) -> None:
        deadline = _managed_entry_deadline(45)
        token = _MANAGED_DEADLINE.set(deadline)
        try:
            if unit not in UNITS[:2]:
                raise diagnostic_error("P000082", "service_unit_invalid", PackageError("service_unit_invalid"))
            managed_lifecycle("restart")
            result = self._call("show", unit, "--property=ActiveState", "--value")
            if result.stdout.strip() != "active":
                raise diagnostic_error("P000083", "service_not_ready", PackageError("service_not_ready"))
            if time.monotonic_ns() >= deadline:
                raise diagnostic_error("P000084", "managed_lifecycle_deadline", PackageError("managed_lifecycle_deadline"))
        finally:
            _MANAGED_DEADLINE.reset(token)


def render_composite_units(prefix: str = "/opt/happyranch") -> dict[str, str]:
    with diagnostic_scope("P100041", "managed_operational_error"):
        connector = render_connector_unit(ConnectorUnitSpec(
            exec_start=(f"{prefix}/bin/happyranch-tsnet-sidecar", "supervise-connector",
                        f"{prefix}/bin/happyranch-connector", "run", "--managed", "--config",
                        "/etc/happyranch/connector.json"),
            user="happyranch", group="happyranch",
            daemon_token_path="/etc/happyranch/daemon.token",
        )).replace(
            "After=network-online.target",
            "After=network-online.target\nWants=happyranch-tsnet-sidecar.service\n"
            "PartOf=happyranch-managed.target happyranch-tsnet-sidecar.service\nStartLimitIntervalSec=0",
        ).replace("RestartSec=1", "RestartSec=30").replace(
            "TimeoutStopSec=10", "TimeoutStartSec=120\nTimeoutStopSec=35\nTimeoutAbortSec=35\n"
            "KillMode=mixed\nWatchdogSignal=SIGUSR2\nRestartMode=normal\nRestartPreventExitStatus="
            + " ".join(str(code) for code in range(2, 256)),
        ).replace("WantedBy=multi-user.target", "WantedBy=happyranch-managed.target")
    sidecar = """[Unit]
Description=HappyRanch embedded tsnet sidecar
After=network-online.target
Wants=network-online.target
PartOf=happyranch-managed.target happyranch-connector.service
StartLimitIntervalSec=0

[Service]
Type=notify
NotifyAccess=main
ExecStartPre=+{prefix}/bin/happyranch-connector reconcile-enrollment-retirement --source /etc/happyranch/enrollment.key --marker /var/lib/happyranch-tsnet-sidecar/credential.consumed --dropin /etc/systemd/system/happyranch-tsnet-sidecar.service.d/10-enrollment-credential.conf
ExecStartPre={prefix}/bin/happyranch-connector credential-capability --name enrollment.key --unit happyranch-tsnet-sidecar.service --consumed-marker /var/lib/happyranch-tsnet-sidecar/credential.consumed
ExecStart={prefix}/bin/happyranch-tsnet-sidecar --config /etc/happyranch/sidecar.json
ExecStartPost=+{prefix}/bin/happyranch-connector retire-enrollment-source --source /etc/happyranch/enrollment.key --marker /var/lib/happyranch-tsnet-sidecar/credential.consumed --dropin /etc/systemd/system/happyranch-tsnet-sidecar.service.d/10-enrollment-credential.conf
User=happyranch
Group=happyranch
Restart=no
WatchdogSec=30
WatchdogSignal=SIGUSR2
KillMode=mixed
TimeoutStartSec=120
TimeoutStopSec=35
TimeoutAbortSec=35
NoNewPrivileges=yes
ProtectSystem=strict
ProtectHome=yes
PrivateTmp=yes
PrivateDevices=yes
ProtectKernelTunables=yes
ProtectKernelModules=yes
ProtectControlGroups=yes
RestrictSUIDSGID=yes
RestrictRealtime=yes
LockPersonality=yes
MemoryDenyWriteExecute=yes
RestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX AF_NETLINK
SystemCallArchitectures=native
CapabilityBoundingSet=
AmbientCapabilities=
UMask=0077
StateDirectory=happyranch-tsnet-sidecar
StateDirectoryMode=0700
RuntimeDirectory=happyranch-tsnet-sidecar
LogsDirectory=happyranch-tsnet-sidecar
[Install]
WantedBy=happyranch-managed.target
""".format(prefix=prefix)
    target = """[Unit]
Description=HappyRanch managed remote access composite
Wants=happyranch-connector.service happyranch-tsnet-sidecar.service
After=happyranch-connector.service happyranch-tsnet-sidecar.service

[Install]
WantedBy=multi-user.target
"""
    # The base renderer repeats RestartSec. Composite directives have one
    # selected value, including the private state-directory mode.
    lines = connector.splitlines()
    connector = "\n".join(line for index, line in enumerate(lines)
                            if line != "RestartSec=30" or line not in lines[:index]) + "\n"
    with diagnostic_scope("P100042", "managed_operational_error"):
        connector = connector.replace("StateDirectory=happyranch-connector\n",
                                      "StateDirectory=happyranch-connector\nStateDirectoryMode=0700\n")
    return {UNITS[0]: connector, UNITS[1]: sidecar, UNITS[2]: target}


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _sbom(inventory: Mapping[str, object], version: str) -> bytes:
    modules = inventory.get("modules")
    if not isinstance(modules, list) or not modules:
        raise diagnostic_error("P300001", "inventory_invalid", PackageError("inventory_invalid"))
    components = []
    for item in modules:
        if not isinstance(item, dict):
            raise diagnostic_error("P300002", "inventory_invalid", PackageError("inventory_invalid"))
        try:
            components.append({
                "type": "library", "name": item["module"], "version": item["version"],
                "purl": f"pkg:golang/{item['module']}@{item['version']}",
                "licenses": [{"license": {"id": item["spdx"]}}],
                "properties": [
                    {"name": "happyranch:go.sum", "value": item["sum"]},
                    {"name": "happyranch:license-sha256", "value": item["license_sha256"]},
                ],
            })
        except KeyError as exc:
            raise diagnostic_error("P300003", "inventory_invalid", PackageError("inventory_invalid")) from exc
    payload = {"bomFormat": "CycloneDX", "specVersion": "1.5", "version": 1,
               "metadata": {"component": {"type": "application", "name": "happyranch-linux", "version": version}},
               "components": sorted(components, key=lambda item: (item["name"], item["version"]))}
    with diagnostic_scope("P300004", "managed_operational_error"):
        return (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode()


def build_linux_package(output: Path, sidecar: Path, connector: Path, wheel: Path,
                        inventory_path: Path, notices_path: Path, *, version: str) -> Path:
    try:
        inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
        notices = notices_path.read_bytes()
        modules = inventory["modules"]
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise PackageError("package_input_invalid") from exc
    _validate_evidence(inventory, notices)
    if not sidecar.is_file() or not connector.is_file() or not zipfile.is_zipfile(wheel):
        raise PackageError("package_input_invalid")
    with zipfile.ZipFile(wheel) as built_wheel:
        if not any(name.startswith("runtime/") and name.endswith(".py") for name in built_wheel.namelist()):
            raise PackageError("wheel_invalid")
    units = render_composite_units()
    files: dict[str, tuple[bytes, int]] = {
        "bin/happyranch-tsnet-sidecar": (sidecar.read_bytes(), 0o755),
        "bin/happyranch-connector": (connector.read_bytes(), 0o755),
        "share/happyranch.whl": (wheel.read_bytes(), 0o644),
        "share/dependency-inventory.json": (inventory_path.read_bytes(), 0o644),
        "share/sbom.cdx.json": (_sbom(inventory, version), 0o644),
        "share/THIRD_PARTY_NOTICES.md": (notices, 0o644),
    }
    files.update({f"systemd/{name}": (text.encode(), 0o644) for name, text in units.items()})
    manifest = {"schema_version": 1, "version": version, "architecture": "linux-amd64",
                "sidecar_dependency_count": len(modules),
                "managed_lifecycle_contract": dict(MANAGED_LIFECYCLE_CONTRACT),
                "files": [{"path": name, "sha256": _sha(raw), "mode": oct(mode)}
                          for name, (raw, mode) in sorted(files.items())]}
    files["manifest.json"] = ((json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode(), 0o644)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(output, "w", format=tarfile.PAX_FORMAT) as archive:
        for name, (raw, mode) in sorted(files.items()):
            info = tarfile.TarInfo(f"{PREFIX}/{name}")
            info.size, info.mode, info.mtime, info.uid, info.gid = len(raw), mode, 0, 0, 0
            info.uname = info.gname = "root"
            archive.addfile(info, io.BytesIO(raw))
    return output


def _validate_evidence(inventory: Mapping[str, object], notices: bytes) -> None:
    try:
        with diagnostic_scope("P100043", "managed_operational_error"):
            text = notices.decode("utf-8")
        if set(inventory) != {"schema_version", "artifact", "generator", "modules"} or type(inventory.get("schema_version")) is not int or inventory["schema_version"] != 1:
            raise diagnostic_error("P000085", "inventory_invalid", PackageError("inventory_invalid"))
        artifact = inventory["artifact"]
        if (not isinstance(artifact, dict) or set(artifact) != {"goos", "goarch", "cgo_enabled", "package"}
                or artifact != {"goos": "linux", "goarch": "amd64", "cgo_enabled": False,
                                "package": "happyranch/linux-tsnet-sidecar"}
                or inventory["generator"] != "tools/generate_inventory.py"):
            raise diagnostic_error("P000086", "inventory_invalid", PackageError("inventory_invalid"))
        modules = inventory["modules"]
        if not isinstance(modules, list) or not modules:
            raise diagnostic_error("P000087", "inventory_invalid", PackageError("inventory_invalid"))
    except (UnicodeDecodeError, KeyError, TypeError) as exc:
        raise diagnostic_error("P000088", "notice_invalid", PackageError("notice_invalid")) from exc
    blocks = text.split("\n---\n")
    seen: dict[str, tuple[str, str]] = {}
    for block in blocks:
        spdx = next((line.removeprefix("SPDX: ") for line in block.splitlines() if line.startswith("SPDX: ")), None)
        digest = next((line.removeprefix("License-SHA256: ") for line in block.splitlines() if line.startswith("License-SHA256: ")), None)
        lines = block.splitlines()
        try:
            fence = lines.index("```text")
            end = lines.index("```", fence + 1)
            license_text = "\n".join(lines[fence + 1:end]).rstrip() + "\n"
        except ValueError:
            license_text = ""
        with diagnostic_scope("P100044", "managed_operational_error"):
            if not digest or not license_text or _sha(license_text.encode()) != digest:
                if any(line.startswith("- ") for line in lines):
                    raise diagnostic_error("P000089", "notice_invalid", PackageError("notice_invalid"))
        for line in lines:
            if line.startswith("- ") and "@" in line:
                coordinate = line[2:].strip()
                if coordinate in seen or not spdx or not digest:
                    raise diagnostic_error("P000090", "notice_invalid", PackageError("notice_invalid"))
                seen[coordinate] = (spdx, digest)
    required = ("module", "version", "sum", "source", "spdx", "license_sha256", "relationship")
    if any(not isinstance(item, dict) or set(item) != set(required)
           or any(type(item.get(key)) is not str or not item[key] for key in required)
           or item["source"] != f"https://{item['module']}"
           or item["relationship"] != "statically-linked-linux-build-input"
           or not item["sum"].startswith("h1:")
           or len(item["license_sha256"]) != 64 for item in modules):
        raise diagnostic_error("P000091", "inventory_invalid", PackageError("inventory_invalid"))
    expected = {f"{item['module']}@{item['version']}": (item["spdx"], item["license_sha256"]) for item in modules}
    if len(expected) != len(modules):
        raise diagnostic_error("P000092", "inventory_invalid", PackageError("inventory_invalid"))
    if seen != expected:
        raise diagnostic_error("P000093", "notice_inventory_mismatch", PackageError("notice_inventory_mismatch"))


def _read_verified(package: Path) -> tuple[dict[str, bytes], dict[str, object]]:
    _require_package_source(package)
    files: dict[str, bytes] = {}
    modes: dict[str, int] = {}
    with diagnostic_scope("P100045", "managed_operational_error"):
        with tarfile.open(package) as archive:
            with diagnostic_scope("P100046", "managed_operational_error"):
                for member in archive.getmembers():
                    path = PurePosixPath(member.name)
                    if not member.isfile() or path.is_absolute() or not path.parts or ".." in path.parts or path.parts[0] != PREFIX:
                        raise diagnostic_error("P000094", "archive_member_invalid", PackageError("archive_member_invalid"))
                    relative = str(path.relative_to(PREFIX))
                    if relative in files:
                        raise diagnostic_error("P000095", "archive_duplicate_member", PackageError("archive_duplicate_member"))
                    if member.uid != 0 or member.gid != 0 or member.uname != "root" or member.gname != "root":
                        raise diagnostic_error("P000096", "archive_owner_invalid", PackageError("archive_owner_invalid"))
                    with diagnostic_scope("P100047", "managed_operational_error"):
                        files[relative] = archive.extractfile(member).read()
                    modes[relative] = member.mode & 0o7777
    try:
        with diagnostic_scope("P100048", "managed_operational_error"):
            manifest = json.loads(files["manifest.json"], object_pairs_hook=_reject_duplicate_keys)
        contract = manifest.get("managed_lifecycle_contract")
        if (not isinstance(contract, dict) or contract != MANAGED_LIFECYCLE_CONTRACT
                or any(type(value) is not int for value in contract.values())):
            raise diagnostic_error("P000097", "managed_lifecycle_incompatible", PackageError("managed_lifecycle_incompatible"))
        if type(manifest["schema_version"]) is not int or manifest["schema_version"] != 1 or manifest["architecture"] != "linux-amd64" or type(manifest["version"]) is not str or not manifest["version"]:
            raise diagnostic_error("P000098", "manifest_invalid", PackageError("manifest_invalid"))
        entries = manifest["files"]
        if not isinstance(entries, list) or type(manifest["sidecar_dependency_count"]) is not int:
            raise diagnostic_error("P000099", "manifest_invalid", PackageError("manifest_invalid"))
        expected_paths = set(files) - {"manifest.json"}
        declared_paths = {item["path"] for item in entries}
        if len(entries) != len(declared_paths) or declared_paths != expected_paths:
            raise diagnostic_error("P000100", "manifest_membership_mismatch", PackageError("manifest_membership_mismatch"))
        if declared_paths != set(PAYLOAD_MODES):
            raise diagnostic_error("P000101", "manifest_path_invalid", PackageError("manifest_path_invalid"))
        with diagnostic_scope("P100049", "managed_operational_error"):
            expected_modes = {name: int(mode, 8) for name, mode in PAYLOAD_MODES.items()}
        expected_modes["manifest.json"] = 0o644
        if any(modes[name] != mode for name, mode in expected_modes.items()):
            raise diagnostic_error("P000102", "archive_mode_invalid", PackageError("archive_mode_invalid"))
        for item in entries:
            if not isinstance(item, dict) or set(item) != {"path", "sha256", "mode"} or any(type(item[k]) is not str for k in item):
                raise diagnostic_error("P000103", "manifest_invalid", PackageError("manifest_invalid"))
            path = PurePosixPath(item["path"])
            if path.is_absolute() or ".." in path.parts or str(path) != item["path"] or item["path"] not in PAYLOAD_MODES:
                raise diagnostic_error("P000104", "manifest_path_invalid", PackageError("manifest_path_invalid"))
            if item["mode"] != PAYLOAD_MODES[item["path"]]:
                raise diagnostic_error("P000105", "manifest_mode_invalid", PackageError("manifest_mode_invalid"))
            if _sha(files[item["path"]]) != item["sha256"]:
                raise diagnostic_error("P000106", "manifest_hash_mismatch", PackageError("manifest_hash_mismatch"))
        with diagnostic_scope("P100050", "managed_operational_error"):
            inventory = json.loads(files["share/dependency-inventory.json"])
        if manifest["sidecar_dependency_count"] != len(inventory["modules"]):
            raise diagnostic_error("P000107", "manifest_count_invalid", PackageError("manifest_count_invalid"))
        with diagnostic_scope("P100051", "managed_operational_error"):
            sbom = json.loads(files["share/sbom.cdx.json"])
        if not isinstance(sbom, dict):
            raise diagnostic_error("P000108", "sbom_invalid", PackageError("sbom_invalid"))
        if (type(sbom.get("version")) is not int or sbom.get("bomFormat") != "CycloneDX"
                or sbom.get("specVersion") != "1.5" or not isinstance(sbom.get("components"), list)):
            raise diagnostic_error("P000109", "sbom_invalid", PackageError("sbom_invalid"))
        def component_tuple(c: object) -> tuple[object, ...]:
            if not isinstance(c, dict): raise diagnostic_error("P000110", "sbom_invalid", PackageError("sbom_invalid"))
            props = c.get("properties")
            licenses = c.get("licenses")
            if not isinstance(props, list) or not isinstance(licenses, list) or len(licenses) != 1:
                raise diagnostic_error("P000111", "sbom_invalid", PackageError("sbom_invalid"))
            prop_map = {p.get("name"): p.get("value") for p in props if isinstance(p, dict)}
            try: spdx = licenses[0]["license"]["id"]
            except (KeyError, TypeError): raise diagnostic_error("P000112", "sbom_invalid", PackageError("sbom_invalid"))
            return (c.get("name"), c.get("version"), c.get("purl"), spdx,
                    prop_map.get("happyranch:go.sum"), prop_map.get("happyranch:license-sha256"))
        components = {component_tuple(c) for c in sbom["components"]}
        inventory_coordinates = {(m["module"], m["version"], f"pkg:golang/{m['module']}@{m['version']}",
                                  m["spdx"], m["sum"], m["license_sha256"]) for m in inventory["modules"]}
        if len(components) != len(sbom["components"]) or components != inventory_coordinates:
            raise diagnostic_error("P000113", "sbom_inventory_mismatch", PackageError("sbom_inventory_mismatch"))
        _validate_evidence(inventory, files["share/THIRD_PARTY_NOTICES.md"])
    except PackageError:
        raise
    except (KeyError, TypeError, ValueError) as exc:
        raise diagnostic_error("P000114", "manifest_invalid", PackageError("manifest_invalid")) from exc
    return files, manifest


def _require_package_operand(path: Path, *, directory: bool | None = None,
                             link_object: bool = False) -> None:
    """Admit local package operands without granting managed authority.

    OLD tree links may be inventoried or unlinked as objects, never opened.
    All content operands require canonical parents and single-link regular
    custody. The literal filesystem root is a directory check only; its
    managed execution admission remains in the existing root-aware callers.
    """
    path = Path(path)
    if ".." in path.parts:
        raise diagnostic_error("P000115", "managed_lifecycle_owner_unknown", PackageError("managed_lifecycle_owner_unknown"))
    _assert_safe_root_ancestry(path)
    if path == Path("/"):
        if directory is False:
            raise diagnostic_error("P000116", "managed_lifecycle_owner_unknown", PackageError("managed_lifecycle_owner_unknown"))
        return
    if link_object:
        try:
            with diagnostic_scope("P100052", "managed_operational_error"):
                metadata = path.lstat()
        except FileNotFoundError:
            pass
        except OSError as exc:
            raise diagnostic_error("P000117", "managed_lifecycle_owner_unknown", PackageError("managed_lifecycle_owner_unknown")) from exc
        else:
            if stat.S_ISLNK(metadata.st_mode):
                # Check the containing namespace, not the link's target.
                _require_package_operand(path.parent, directory=True)
                return
    _require_nonmanaged_state_owner(path, staging=True, directory=directory)


def _require_package_source(package: Path) -> None:
    # The existing private handoff deliberately uses immutable anonymous
    # memfds. Admit that exact descriptor form independently of path aliases;
    # ordinary files (including protected state) cannot meet these seals.
    if (package.parent == Path("/proc/self/fd")
            and re.fullmatch(r"[0-9]+", package.name) is not None):
        try:
            with diagnostic_scope("P100053", "managed_operational_error"):
                descriptor = int(package.name)
            with diagnostic_scope("P100054", "managed_operational_error"):
                metadata = os.fstat(descriptor)
            _add_seals, get_seals, required, _flags = _package_sealing_constants()
            with diagnostic_scope("P100055", "managed_operational_error"):
                if (not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 0
                        or fcntl.fcntl(descriptor, get_seals) & required != required):
                    raise diagnostic_error("P000118", "package_input_invalid", PackageError("package_input_invalid"))
        except OSError as exc:
            raise diagnostic_error("P000119", "package_input_invalid", PackageError("package_input_invalid")) from exc
        return
    _require_package_operand(package, directory=False)


def _managed_package_root(root: Path) -> bool:
    """Admit only literal managed / or positively separate staging ownership.

    Resolution is exclusion evidence only. Never turn an alias into a managed
    invocation or permission to traverse links. Check the finite package
    destinations, including existing inode aliases, before any preparation.
    """
    if root == Path("/"):
        return True
    if ".." in root.parts:
        raise diagnostic_error("P000120", "managed_package_context_required", PackageError("managed_package_context_required"))
    _assert_safe_root_ancestry(root)
    destinations = (root, root / "opt/happyranch", root / "etc/systemd/system",
                    *(root / "etc/systemd/system" / name for name in UNITS),
                    root / "etc/systemd/system" / _DROPIN_SERVICE_DIR / _DROPIN_FILE_NAME,
                    *_transaction_paths(root), _record_temp(root))
    protected = (Path("/opt/happyranch"), Path("/opt/happyranch/bin"),
                 Path("/opt/happyranch/share"), Path("/etc/systemd/system"),
                 Path("/var/lib/happyranch-connector"), Path("/var/lib/happyranch-tsnet-sidecar"),
                 *(Path("/etc/systemd/system") / name for name in UNITS),
                 Path("/etc/systemd/system") / _DROPIN_SERVICE_DIR / _DROPIN_FILE_NAME,
                 *_transaction_paths(Path("/")), _record_temp(Path("/")))
    for selected in destinations:
        _require_package_operand(selected)
        destination = _nonmanaged_owner_destination(selected)
        if destination != Path(os.path.abspath(selected)):
            raise diagnostic_error("P000121", "managed_package_context_required", PackageError("managed_package_context_required"))
        for fixed in protected:
            # Ancestors catch directory aliases even when a fixed leaf is
            # absent; individual leaves also catch existing hard-link aliases.
            if (any(_nonmanaged_owner_same_path(parent, fixed)
                    for parent in (destination, *destination.parents))
                    or any(_nonmanaged_owner_same_path(destination, parent)
                           for parent in fixed.parents)):
                raise diagnostic_error("P000122", "managed_package_context_required", PackageError("managed_package_context_required"))
            left, right = destination, fixed
            while left.name == right.name and left.parent != left and right.parent != right:
                left, right = left.parent, right.parent
                if _nonmanaged_owner_same_path(left, right):
                    raise diagnostic_error("P000123", "managed_package_context_required", PackageError("managed_package_context_required"))
    return False


def _transaction_paths(root: Path) -> tuple[Path, Path, Path]:
    return root / _PAYLOAD_BACKUP_NAME, root / _UNIT_BACKUP_NAME, root / TRANSACTION_MARKER


def _record_temp(root: Path) -> Path:
    return root / (TRANSACTION_MARKER + ".tmp")


def _seam(guard, stage: str, operation: str, path: Path) -> None:
    """Invoke the narrow injectable filesystem seam.

    ``guard`` (when supplied) is called immediately before (``stage="before"``)
    and after (``stage="after"``) every real filesystem mutation this module
    performs, with the exact normalized operation name and target path.  A
    guard may raise to simulate a fault.  Occurrence scoping, one-shot
    disarmament, hit receipts and partial effects are the guard's
    responsibility: a ``partial`` guard performs a real truncated mutation at
    the target and then raises, exactly like a torn write.
    """
    if _MANAGED_PACKAGE.get():
        _check_managed_holder()
    if guard is not None:
        guard(stage, operation, str(path))


def _unlink(path: Path, guard, operation: str) -> None:
    _require_package_operand(path, link_object=True)
    _seam(guard, "before", operation, path)
    with diagnostic_scope("P100056", "managed_operational_error"):
        os.unlink(path)
    _seam(guard, "after", operation, path)


def _write_file(path: Path, raw: bytes, mode: int, guard, operation: str) -> None:
    _require_package_operand(path, directory=False)
    _seam(guard, "before", operation, path)
    with diagnostic_scope("P100057", "managed_operational_error"):
        descriptor = os.open(
            path,
            os.O_WRONLY | os.O_CREAT | os.O_TRUNC
            | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
            mode,
        )
    try:
        view = memoryview(raw)
        while view:
            with diagnostic_scope("P100058", "managed_operational_error"):
                written = os.write(descriptor, view)
            view = view[written:]
    finally:
        with diagnostic_scope("P100059", "managed_operational_error"):
            os.close(descriptor)
    _seam(guard, "after", operation, path)
    _require_package_operand(path, directory=False)
    _seam(guard, "before", f"{operation}:chmod", path)
    with diagnostic_scope("P100060", "managed_operational_error"):
        os.chmod(path, mode)
    _seam(guard, "after", f"{operation}:chmod", path)


def _copy_file(source: Path, destination: Path, guard, operation: str) -> None:
    _require_package_operand(source, directory=False)
    _require_package_operand(destination, directory=False)
    _seam(guard, "before", operation, destination)
    with diagnostic_scope("P100061", "managed_operational_error"):
        shutil.copy2(source, destination)
    _seam(guard, "after", operation, destination)


def _backup_inventory(path: Path) -> dict:
    _require_package_operand(path, directory=False)
    with diagnostic_scope("P100062", "managed_operational_error"):
        return {"sha256": _sha(path.read_bytes()), "mode": stat.S_IMODE(path.lstat().st_mode)}


def _backup_intact(path: Path, expected: object) -> bool:
    return _backup_intact_error(path, expected) is None


def _backup_intact_error(path: Path, expected: object) -> BaseException | None:
    _require_package_operand(path, directory=False)
    if expected is None:
        return diagnostic_error("P500001", "transaction_state_invalid", PackageError("transaction_state_invalid")) if path.exists() or path.is_symlink() else None
    if not isinstance(expected, dict) or not path.is_file() or path.is_symlink():
        return diagnostic_error("P500002", "transaction_state_invalid", PackageError("transaction_state_invalid"))
    try:
        with diagnostic_scope("P100063", "managed_operational_error"):
            metadata = path.lstat()
        with diagnostic_scope("P100064", "managed_operational_error"):
            matches = (
                stat.S_IMODE(metadata.st_mode) == expected["mode"]
                and _sha(path.read_bytes()) == expected["sha256"]
            )
            return None if matches else diagnostic_error("P500003", "transaction_state_invalid", PackageError("transaction_state_invalid"))
    except OSError as error:
        diagnostic_loss("P400030", "managed_operational_error", error)
        return error


def _backup_present(path: Path) -> bool:
    return path.exists() or path.is_symlink()


def _path_file_identity(path: Path) -> dict:
    _require_package_operand(path, directory=False)
    with diagnostic_scope("P100065", "managed_operational_error"):
        return {"sha256": _sha(path.read_bytes()), "mode": stat.S_IMODE(path.lstat().st_mode)}


def _planned_payload_inventory(files: Mapping[str, bytes], system_service: bool) -> dict:
    """Deterministic identity of the payload tree the stage will publish."""
    root_mode = 0o755 if system_service else 0o700
    entries: dict[str, dict] = {
        "bin": {"type": "dir", "mode": 0o755 if system_service else 0o700},
        "share": {"type": "dir", "mode": 0o755 if system_service else 0o700},
    }
    for name, raw in files.items():
        if name.startswith("systemd/"):
            continue
        with diagnostic_scope("P100066", "managed_operational_error"):
            mode = (0o644 if system_service else 0o600) if name == "manifest.json" else (
                int(PAYLOAD_MODES[name], 8) if system_service
                else (0o700 if name.startswith("bin/") else 0o600)
            )
        entries[name] = {"type": "file", "mode": mode, "sha256": _sha(raw)}
    return {"root_mode": root_mode, "entries": entries}


def _inventory_tree(path: Path, *, metadata_only: bool = False) -> dict:
    """Inventory without following links; preflight alone reads no contents.

    The default exact byte/mode fingerprint remains the schema2 identity.
    Metadata-only results are used solely for operand admission, never stored
    or compared as a transaction identity.
    """
    _require_package_operand(path, directory=True)
    with diagnostic_scope("P100067", "managed_operational_error"):
        root_mode = stat.S_IMODE(path.lstat().st_mode)
    entries: dict[str, dict] = {}
    stack: list[tuple[Path, str]] = [(path, "")]
    while stack:
        base, prefix = stack.pop()
        with diagnostic_scope("P100068", "managed_operational_error"):
            for entry in sorted(base.iterdir(), key=lambda item: item.name):
                _require_package_operand(entry, link_object=True)
                relative = f"{prefix}{entry.name}"
                with diagnostic_scope("P100069", "managed_operational_error"):
                    metadata = entry.lstat()
                if stat.S_ISLNK(metadata.st_mode):
                    with diagnostic_scope("P100070", "managed_operational_error"):
                        entries[relative] = {"type": "link", "target": os.readlink(entry)}
                elif stat.S_ISDIR(metadata.st_mode):
                    entries[relative] = {"type": "dir", "mode": stat.S_IMODE(metadata.st_mode)}
                    stack.append((entry, f"{relative}/"))
                elif stat.S_ISREG(metadata.st_mode):
                    with diagnostic_scope("P100071", "managed_operational_error"):
                        entries[relative] = {
                            "type": "file", "mode": stat.S_IMODE(metadata.st_mode),
                            **({"sha256": _sha(entry.read_bytes())} if not metadata_only else {}),
                        }
                else:
                    entries[relative] = {"type": "other"}
    return {"root_mode": root_mode, "entries": entries}


def _tree_matches(path: Path, inventory: object, *, absent_ok: bool = False) -> bool:
    if not isinstance(inventory, dict):
        return False
    try:
        with diagnostic_scope("P100072", "managed_operational_error"):
            metadata = path.lstat()
    except FileNotFoundError as error:
        # Only the restore consumer positively permits a missing root. An
        # interior/racing disappearance during inventory below remains a loss.
        if not absent_ok:
            diagnostic_loss("P400036", "managed_operational_error", error)
        return False
    except OSError as error:
        diagnostic_loss("P400037", "managed_operational_error", error)
        return False
    try:
        if not stat.S_ISDIR(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
            return False
        if stat.S_IMODE(metadata.st_mode) != inventory.get("root_mode"):
            return False
        return _inventory_tree(path) == inventory
    except OSError as error:
        diagnostic_loss("P400031", "managed_operational_error", error)
        return False


def _tree_is_owned_partial(path: Path, inventory: object) -> bool:
    """True when every surviving entry is an exact member of the recorded tree.

    A rollback that removes a freshly published payload can be interrupted
    after any interior unlink/rmdir.  The remainder is then a strict subset of
    the recorded NEW identity: no entry may exist outside that inventory and
    every surviving entry must match its recorded type/mode/bytes.  Foreign,
    corrupt or unexplained content therefore still refuses.
    """
    if not isinstance(inventory, dict) or set(inventory) != {"root_mode", "entries"}:
        return False
    entries = inventory.get("entries")
    if not isinstance(entries, dict):
        return False
    try:
        with diagnostic_scope("P100073", "managed_operational_error"):
            metadata = path.lstat()
    except OSError as error:
        diagnostic_loss("P400032", "managed_operational_error", error)
        return False
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        return False
    if stat.S_IMODE(metadata.st_mode) != inventory.get("root_mode"):
        return False
    try:
        present = _inventory_tree(path)["entries"]
    except OSError as error:
        diagnostic_loss("P400033", "managed_operational_error", error)
        return False
    if not set(present).issubset(entries):
        return False
    return all(entry == entries[relative] for relative, entry in present.items())


def _file_matches(path: Path, identity: object, *, absent_ok: bool = False) -> bool:
    _require_package_operand(path, directory=False)
    if not isinstance(identity, dict):
        return False
    try:
        with diagnostic_scope("P100074", "managed_operational_error"):
            metadata = path.lstat()
    except FileNotFoundError as error:
        if not absent_ok:
            diagnostic_loss("P400038", "managed_operational_error", error)
        return False
    except OSError as error:
        diagnostic_loss("P400039", "managed_operational_error", error)
        return False
    try:
        if not stat.S_ISREG(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
            return False
        with diagnostic_scope("P100075", "managed_operational_error"):
            return (
                stat.S_IMODE(metadata.st_mode) == identity.get("mode")
                and _sha(path.read_bytes()) == identity.get("sha256")
            )
    except OSError as error:
        diagnostic_loss("P400034", "managed_operational_error", error)
        return False


def _valid_file_identity(identity: object) -> bool:
    return (
        isinstance(identity, dict) and set(identity) == {"sha256", "mode"}
        and isinstance(identity.get("sha256"), str) and len(identity["sha256"]) == 64
        and type(identity.get("mode")) is int
    )


def _valid_inventory(inventory: object) -> bool:
    return _valid_inventory_error(inventory) is None


def _valid_inventory_error(inventory: object) -> BaseException | None:
    if not isinstance(inventory, dict) or set(inventory) != {"root_mode", "entries"}:
        return diagnostic_error("P500004", "transaction_state_invalid", PackageError("transaction_state_invalid"))
    if type(inventory.get("root_mode")) is not int:
        return diagnostic_error("P500005", "transaction_state_invalid", PackageError("transaction_state_invalid"))
    entries = inventory.get("entries")
    if not isinstance(entries, dict):
        return diagnostic_error("P500006", "transaction_state_invalid", PackageError("transaction_state_invalid"))
    for key, value in entries.items():
        if (not isinstance(key, str) or not key or key.startswith("/")
                or ".." in PurePosixPath(key).parts or not isinstance(value, dict)):
            return diagnostic_error("P500007", "transaction_state_invalid", PackageError("transaction_state_invalid"))
        kind = value.get("type")
        if kind == "dir":
            if set(value) != {"type", "mode"} or type(value.get("mode")) is not int:
                return diagnostic_error("P500008", "transaction_state_invalid", PackageError("transaction_state_invalid"))
        elif kind == "file":
            if (set(value) != {"type", "mode", "sha256"}
                    or type(value.get("mode")) is not int
                    or not isinstance(value.get("sha256"), str)
                    or len(value["sha256"]) != 64):
                return diagnostic_error("P500009", "transaction_state_invalid", PackageError("transaction_state_invalid"))
        elif kind == "link":
            if set(value) != {"type", "target"} or not isinstance(value.get("target"), str):
                return diagnostic_error("P500010", "transaction_state_invalid", PackageError("transaction_state_invalid"))
        elif kind == "other":
            if set(value) != {"type"}:
                return diagnostic_error("P500011", "transaction_state_invalid", PackageError("transaction_state_invalid"))
        else:
            return diagnostic_error("P500012", "transaction_state_invalid", PackageError("transaction_state_invalid"))
    return None


def _assert_safe_root_ancestry(root: Path) -> None:
    """Refuse a selected root reached through a symlinked ancestor.

    ``_assert_safe_target`` only inspects the root itself and the components
    *below* it.  A symlink anywhere above the selected root silently redirects
    every subsequent write outside the caller's chosen tree, so the whole
    ancestor chain is checked once per entry point without following links.
    Components that do not exist yet are tolerated (the leaf checks cover
    them); an existing component must be a real directory.
    """
    target = Path(os.path.abspath(root))
    current = Path(target.anchor)
    for part in target.parts[1:-1]:
        current = current / part
        try:
            with diagnostic_scope("P100076", "managed_operational_error"):
                metadata = current.lstat()
        except FileNotFoundError:
            return
        except OSError as exc:
            raise diagnostic_error("P000124", "transaction_state_invalid", PackageError("transaction_state_invalid")) from exc
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
            raise diagnostic_error("P000125", "transaction_state_invalid", PackageError("transaction_state_invalid"))


def _assert_safe_target(root: Path, path: Path, *, directory: bool | None = None) -> None:
    """Reject unsafe types/symlinks on the exact path or any ancestor below root.

    ``directory`` narrows the expected leaf type: ``True`` requires a real
    directory, ``False`` a regular file, and ``None`` accepts either while
    still refusing links, FIFOs, sockets and devices.  Type is validated here,
    before any filesystem operation, so an unexpected leaf never reaches
    ``shutil`` as an unrelated exception category.
    """
    root, path = Path(root), Path(path)
    _require_package_operand(root, directory=True)
    _require_package_operand(path, directory=directory)
    try:
        with diagnostic_scope("P100077", "managed_operational_error"):
            metadata = root.lstat()
    except OSError as exc:
        raise diagnostic_error("P000126", "transaction_state_invalid", PackageError("transaction_state_invalid")) from exc
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        raise diagnostic_error("P000127", "transaction_state_invalid", PackageError("transaction_state_invalid"))
    try:
        relative = path.relative_to(root)
    except ValueError as exc:
        raise diagnostic_error("P000128", "transaction_state_invalid", PackageError("transaction_state_invalid")) from exc
    current = root
    parts = relative.parts
    for index, part in enumerate(parts):
        current = current / part
        try:
            with diagnostic_scope("P100078", "managed_operational_error"):
                child = current.lstat()
        except FileNotFoundError:
            return
        except OSError as exc:
            raise diagnostic_error("P000129", "transaction_state_invalid", PackageError("transaction_state_invalid")) from exc
        if stat.S_ISLNK(child.st_mode):
            raise diagnostic_error("P000130", "transaction_state_invalid", PackageError("transaction_state_invalid"))
        if index != len(parts) - 1:
            if not stat.S_ISDIR(child.st_mode):
                raise diagnostic_error("P000131", "transaction_state_invalid", PackageError("transaction_state_invalid"))
        elif directory is True:
            if not stat.S_ISDIR(child.st_mode):
                raise diagnostic_error("P000132", "transaction_state_invalid", PackageError("transaction_state_invalid"))
        elif directory is False:
            if not stat.S_ISREG(child.st_mode):
                raise diagnostic_error("P000133", "transaction_state_invalid", PackageError("transaction_state_invalid"))
        elif not (stat.S_ISDIR(child.st_mode) or stat.S_ISREG(child.st_mode)):
            raise diagnostic_error("P000134", "transaction_state_invalid", PackageError("transaction_state_invalid"))


def _record_paths(root: Path, record: Mapping[str, object]) -> list[tuple[Path, bool]]:
    payload_backup, unit_backup, marker = _transaction_paths(root)
    paths: list[tuple[Path, bool]] = [
        (root, True), (marker, False), (_record_temp(root), False),
        (payload_backup, True), (unit_backup, True),
        (root / "opt", True), (root / "opt/happyranch", True),
        (root / "etc/systemd/system", True),
        (root / "etc/systemd/system" / _DROPIN_SERVICE_DIR, True),
        (root / "etc/systemd/system" / _DROPIN_SERVICE_DIR / _DROPIN_FILE_NAME, False),
    ]
    paths.extend((root / "etc/systemd/system" / unit, False) for unit in UNITS)
    paths.extend((unit_backup / unit, False) for unit in UNITS)
    paths.extend([
        (unit_backup / _DROPIN_SERVICE_DIR, True),
        (unit_backup / _DROPIN_BACKUP_RELATIVE, False),
    ])
    stage = record.get("stage")
    if stage is not None:
        paths.append((Path(str(stage)), True))
    return paths


def _assert_record_paths_safe(root: Path, record: Mapping[str, object]) -> None:
    for path, directory in _record_paths(root, record):
        _assert_safe_target(root, path, directory=directory)


def _replace(source: Path, destination: Path, guard, operation: str) -> None:
    _require_package_operand(source)
    _require_package_operand(destination)
    for path in (source, destination):
        if path.is_dir():
            _inventory_tree(path, metadata_only=True)
    _seam(guard, "before", operation, destination)
    with diagnostic_scope("P100079", "managed_operational_error"):
        os.replace(source, destination)
    _seam(guard, "after", operation, destination)


def _ensure_dir(path: Path, mode: int, guard, operation: str) -> bool:
    _require_package_operand(path, directory=True)
    if path.is_symlink():
        raise diagnostic_error("P000135", "transaction_state_invalid", PackageError("transaction_state_invalid"))
    if path.exists():
        if not path.is_dir():
            raise diagnostic_error("P000136", "transaction_state_invalid", PackageError("transaction_state_invalid"))
        return False
    _seam(guard, "before", f"{operation}:mkdir", path)
    with diagnostic_scope("P100080", "managed_operational_error"):
        path.mkdir()
    _seam(guard, "after", f"{operation}:mkdir", path)
    _seam(guard, "before", f"{operation}:chmod", path)
    with diagnostic_scope("P100081", "managed_operational_error"):
        path.chmod(mode)
    _seam(guard, "after", f"{operation}:chmod", path)
    return True


def _remove_tree(path: Path, guard, operation: str) -> None:
    """Recursively remove an owned tree using only exact, no-follow operations."""
    _require_package_operand(path, link_object=True)
    if not path.is_symlink() and path.is_dir():
        # Reuse the existing inventory walk before the first deletion, even
        # for a direct lower call. Unknown later descendants retain the tree.
        _inventory_tree(path, metadata_only=True)

    def remove(selected: Path) -> None:
        _require_package_operand(selected, link_object=True)
        if selected.is_symlink() or selected.is_file():
            _unlink(selected, guard, f"{operation}:unlink")
            return
        with diagnostic_scope("P100082", "managed_operational_error"):
            for entry in sorted(selected.iterdir()):
                remove(entry)
        _seam(guard, "before", f"{operation}:rmdir", selected)
        with diagnostic_scope("P100083", "managed_operational_error"):
            selected.rmdir()
        _seam(guard, "after", f"{operation}:rmdir", selected)

    remove(path)


def _write_record(root: Path, record: dict, guard) -> None:
    """Atomically publish the transaction record, preserving the prior record."""
    if _managed_package_root(root):
        _require_managed_package_context()
    _assert_record_paths_safe(root, record)
    marker = root / TRANSACTION_MARKER
    temporary = _record_temp(root)
    with diagnostic_scope("P100084", "managed_operational_error"):
        raw = (json.dumps(record, sort_keys=True) + "\n").encode("utf-8")
    _seam(guard, "before", "record_temp_create", temporary)
    with diagnostic_scope("P100085", "managed_operational_error"):
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_TRUNC
            | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
    try:
        _seam(guard, "after", "record_temp_create", temporary)
        _seam(guard, "before", "record_temp_write", temporary)
        view = memoryview(raw)
        while view:
            with diagnostic_scope("P100086", "managed_operational_error"):
                written = os.write(descriptor, view)
            view = view[written:]
        _seam(guard, "after", "record_temp_write", temporary)
        _seam(guard, "before", "record_temp_fsync", temporary)
        with diagnostic_scope("P100087", "managed_operational_error"):
            os.fsync(descriptor)
        _seam(guard, "after", "record_temp_fsync", temporary)
    finally:
        with diagnostic_scope("P100088", "managed_operational_error"):
            os.close(descriptor)
    _require_package_operand(temporary, directory=False)
    _seam(guard, "before", "record_temp_chmod", temporary)
    with diagnostic_scope("P100089", "managed_operational_error"):
        os.chmod(temporary, 0o600)
    _seam(guard, "after", "record_temp_chmod", temporary)
    _require_package_operand(temporary, directory=False)
    _require_package_operand(marker, directory=False)
    _seam(guard, "before", "record_replace", marker)
    with diagnostic_scope("P100090", "managed_operational_error"):
        os.replace(temporary, marker)
    _seam(guard, "after", "record_replace", marker)


def _publish(root: Path, record: Mapping[str, object], guard, **changes: object) -> dict:
    """Durably publish an updated record; the caller keeps the prior record on failure.

    The returned mapping is the last successfully *published* authority, so an
    exception handler never acts on an in-memory state whose record update did
    not reach disk.
    """
    updated = {**record, **changes}
    _write_record(root, updated, guard)
    return updated


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise diagnostic_error("P000137", "transaction_state_invalid", PackageError("transaction_state_invalid"))
        result[key] = value
    return result


def _allowed_created_parents(record: Mapping[str, object]) -> set[str]:
    """The closed, source-derived universe of parents an install may create."""
    allowed = set(_CREATED_PARENT_PLAN)
    if record.get("new_dropin") is not None:
        allowed.add(f"etc/systemd/system/{_DROPIN_SERVICE_DIR}")
    return allowed


def _backups_complete(record: Mapping[str, object]) -> bool:
    return _backups_complete_error(record) is None


def _backups_complete_error(record: Mapping[str, object]) -> BaseException | None:
    """True when recorded prior-existence matches the recorded backup inventory."""
    backups = record["backups"]
    if not isinstance(backups, dict):
        return diagnostic_error("P500013", "transaction_state_invalid", PackageError("transaction_state_invalid"))
    if (backups["payload"] is not None) != record["payload_present"]:
        return diagnostic_error("P500014", "transaction_state_invalid", PackageError("transaction_state_invalid"))
    if (backups["dropin"] is not None) != record["dropin_present"]:
        return diagnostic_error("P500015", "transaction_state_invalid", PackageError("transaction_state_invalid"))
    matches = all(
        (backups["units"][unit] is not None) == record["units"][unit] for unit in UNITS
    )
    return None if matches else diagnostic_error("P500016", "transaction_state_invalid", PackageError("transaction_state_invalid"))


def _validate_progress(record: Mapping[str, object]) -> None:
    """Reject phase/progress/prior-existence combinations that cannot occur.

    Every accepted phase is reachable only through the source publication
    sequence in ``install_linux_package``; a marker whose phase and recorded
    progress contradict that sequence (for example a ``committed`` record that
    never published any unit) is refused before any cleanup could act on it.
    """
    phase = record["phase"]
    published = record["published_units"]
    if phase == "preparing":
        empty_units = {unit: None for unit in UNITS}
        if record["backups"] != {"payload": None, "units": empty_units, "dropin": None}:
            raise diagnostic_error("P000138", "transaction_state_invalid", PackageError("transaction_state_invalid"))
        if published or record["dropin_published"]:
            raise diagnostic_error("P000139", "transaction_state_invalid", PackageError("transaction_state_invalid"))
        return
    if phase == "rolling_back":
        # A rollback may have consumed any subset of the recorded backups;
        # per-path identity classification already refuses unexplained gaps.
        return
    if (failure := _backups_complete_error(record)) is not None:
        error = diagnostic_error("P000140", "transaction_state_invalid", PackageError("transaction_state_invalid"))
        origin = exception_refusal(failure)
        if origin is not None:
            diagnostic_propagate({"category": origin[0], "site": origin[1]}, error)
        raise error
    if phase in {"prepared", "payload_retained", "payload_published"}:
        if published or record["dropin_published"]:
            raise diagnostic_error("P000141", "transaction_state_invalid", PackageError("transaction_state_invalid"))
    elif phase == "units_publishing":
        if record["dropin_published"]:
            raise diagnostic_error("P000142", "transaction_state_invalid", PackageError("transaction_state_invalid"))
    elif phase == "dropin_publishing":
        if set(published) != set(UNITS):
            raise diagnostic_error("P000143", "transaction_state_invalid", PackageError("transaction_state_invalid"))
        if record["dropin_published"] and record["new_dropin"] is None:
            raise diagnostic_error("P000144", "transaction_state_invalid", PackageError("transaction_state_invalid"))
    elif phase == "committed":
        if set(published) != set(UNITS):
            raise diagnostic_error("P000145", "transaction_state_invalid", PackageError("transaction_state_invalid"))
        if record["dropin_published"] != (record["new_dropin"] is not None):
            raise diagnostic_error("P000146", "transaction_state_invalid", PackageError("transaction_state_invalid"))


def _load_record(root: Path, marker: Path) -> dict:
    """Strictly classify an existing record; any ambiguity is refused unchanged."""
    _assert_safe_target(root, marker, directory=False)
    try:
        with diagnostic_scope("P100091", "managed_operational_error"):
            record = json.loads(
                marker.read_text(encoding="utf-8"), object_pairs_hook=_reject_duplicate_keys
            )
    except PackageError:
        raise
    except (OSError, ValueError) as exc:
        raise diagnostic_error("P000147", "transaction_state_invalid", PackageError("transaction_state_invalid")) from exc
    if not isinstance(record, dict) or set(record) != _TRANSACTION_KEYS:
        raise diagnostic_error("P000148", "transaction_state_invalid", PackageError("transaction_state_invalid"))
    if type(record["schema_version"]) is not int or record["schema_version"] != TRANSACTION_SCHEMA_VERSION:
        raise diagnostic_error("P000149", "transaction_state_invalid", PackageError("transaction_state_invalid"))
    attempt = record["attempt_id"]
    if not isinstance(attempt, str) or _ATTEMPT_ID_PATTERN.fullmatch(attempt) is None:
        raise diagnostic_error("P000150", "transaction_state_invalid", PackageError("transaction_state_invalid"))
    if record["root"] != str(root):
        raise diagnostic_error("P000151", "transaction_state_invalid", PackageError("transaction_state_invalid"))
    if not isinstance(record["phase"], str) or record["phase"] not in _TRANSACTION_PHASES:
        raise diagnostic_error("P000152", "transaction_state_invalid", PackageError("transaction_state_invalid"))
    if any(type(record[key]) is not bool for key in ("payload_present", "dropin_present", "dropin_published")):
        raise diagnostic_error("P000153", "transaction_state_invalid", PackageError("transaction_state_invalid"))
    units = record["units"]
    if not isinstance(units, dict) or set(units) != set(UNITS) or any(type(value) is not bool for value in units.values()):
        raise diagnostic_error("P000154", "transaction_state_invalid", PackageError("transaction_state_invalid"))
    created = record["created_parents"]
    if not isinstance(created, list) or any(not isinstance(item, str) for item in created):
        raise diagnostic_error("P000155", "transaction_state_invalid", PackageError("transaction_state_invalid"))
    if len(set(created)) != len(created):
        raise diagnostic_error("P000156", "transaction_state_invalid", PackageError("transaction_state_invalid"))
    if any(
        not item or item.startswith("/")
        or ".." in PurePosixPath(item).parts
        for item in created
    ):
        raise diagnostic_error("P000157", "transaction_state_invalid", PackageError("transaction_state_invalid"))
    if any(item not in _allowed_created_parents(record) for item in created):
        raise diagnostic_error("P000158", "transaction_state_invalid", PackageError("transaction_state_invalid"))
    published = record["published_units"]
    if not isinstance(published, list) or any(not isinstance(unit, str) for unit in published):
        raise diagnostic_error("P000159", "transaction_state_invalid", PackageError("transaction_state_invalid"))
    if len(set(published)) != len(published):
        raise diagnostic_error("P000160", "transaction_state_invalid", PackageError("transaction_state_invalid"))
    if any(unit not in UNITS for unit in published):
        raise diagnostic_error("P000161", "transaction_state_invalid", PackageError("transaction_state_invalid"))
    # The writer always records the concrete allocated stage before any
    # mutation.  A missing/null stage identity is an unreachable, incomplete
    # record: accepting it would let recovery discard the marker while leaving
    # the real owned stage orphaned.  The recorded string may legitimately name
    # a stage whose path has already moved (payload publication renames it into
    # place), so only the identity is validated here, never the path's presence.
    stage = record["stage"]
    if not isinstance(stage, str) or not stage:
        raise diagnostic_error("P000162", "transaction_state_invalid", PackageError("transaction_state_invalid"))
    stage_path = Path(stage)
    if stage != str(stage_path) or stage_path.parent != Path(root):
        raise diagnostic_error("P000163", "transaction_state_invalid", PackageError("transaction_state_invalid"))
    prefix = f"{_STAGE_PREFIX}{attempt}-"
    if not stage_path.name.startswith(prefix):
        raise diagnostic_error("P000164", "transaction_state_invalid", PackageError("transaction_state_invalid"))
    if _STAGE_SUFFIX_PATTERN.fullmatch(stage_path.name[len(prefix):]) is None:
        raise diagnostic_error("P000165", "transaction_state_invalid", PackageError("transaction_state_invalid"))
    if (failure := _valid_inventory_error(record["new_payload"])) is not None:
        error = diagnostic_error("P000166", "transaction_state_invalid", PackageError("transaction_state_invalid"))
        origin = exception_refusal(failure)
        if origin is not None:
            diagnostic_propagate({"category": origin[0], "site": origin[1]}, error)
        raise error
    new_units = record["new_units"]
    if not isinstance(new_units, dict) or set(new_units) != set(UNITS):
        raise diagnostic_error("P000167", "transaction_state_invalid", PackageError("transaction_state_invalid"))
    if any(not _valid_file_identity(identity) for identity in new_units.values()):
        raise diagnostic_error("P000168", "transaction_state_invalid", PackageError("transaction_state_invalid"))
    if record["new_dropin"] is not None and not _valid_file_identity(record["new_dropin"]):
        raise diagnostic_error("P000169", "transaction_state_invalid", PackageError("transaction_state_invalid"))
    backups = record["backups"]
    if not isinstance(backups, dict) or set(backups) != {"payload", "units", "dropin"}:
        raise diagnostic_error("P000170", "transaction_state_invalid", PackageError("transaction_state_invalid"))
    if backups["payload"] is not None:
        if (failure := _valid_inventory_error(backups["payload"])) is not None:
            error = diagnostic_error("P000171", "transaction_state_invalid", PackageError("transaction_state_invalid"))
            origin = exception_refusal(failure)
            if origin is not None:
                diagnostic_propagate({"category": origin[0], "site": origin[1]}, error)
            raise error
    backup_units = backups["units"]
    if not isinstance(backup_units, dict) or set(backup_units) != set(UNITS):
        raise diagnostic_error("P000172", "transaction_state_invalid", PackageError("transaction_state_invalid"))
    for entry in [*backup_units.values(), backups["dropin"]]:
        if entry is None:
            continue
        if not _valid_file_identity(entry):
            raise diagnostic_error("P000173", "transaction_state_invalid", PackageError("transaction_state_invalid"))
    _validate_progress(record)
    return record


def _planned_created_parents(root: Path, *, include_dropin_dir: bool) -> list[str]:
    candidates = [root / "opt", root / "etc", root / "etc/systemd", root / "etc/systemd/system"]
    if include_dropin_dir:
        candidates.append(root / "etc/systemd/system" / _DROPIN_SERVICE_DIR)
    return [str(path.relative_to(root)) for path in candidates if not path.exists()]


def _remove_created_parents(root: Path, record: dict, guard) -> None:
    if _managed_package_root(root):
        _require_managed_package_context()
    for relative in sorted(record["created_parents"], key=lambda item: len(PurePosixPath(item).parts), reverse=True):
        path = root / relative
        _assert_safe_target(root, path, directory=True)
        with diagnostic_scope("P100092", "managed_operational_error"):
            if path.is_dir() and not path.is_symlink() and not any(path.iterdir()):
                _seam(guard, "before", "created_parent_remove", path)
                with diagnostic_scope("P100093", "managed_operational_error"):
                    path.rmdir()
                _seam(guard, "after", "created_parent_remove", path)


def _cleanup_owned(root: Path, record: dict, guard) -> None:
    """Remove exact recorded owned residue, then the marker last."""
    managed = _managed_package_root(root)
    if managed:
        _require_managed_package_context()
        _package_managed_history(_MANAGED_DEADLINE.get())
    _assert_record_paths_safe(root, record)
    payload_backup, unit_backup, marker = _transaction_paths(root)
    trees = [payload_backup, unit_backup]
    if record.get("stage") is not None:
        trees.append(Path(record["stage"]))
    for path in trees:
        if _backup_present(path):
            _inventory_tree(path, metadata_only=True)
    for path in (payload_backup, unit_backup):
        _assert_safe_target(root, path, directory=True)
        if _backup_present(path):
            _remove_tree(path, guard, "backup_remove")
    temporary = _record_temp(root)
    _assert_safe_target(root, temporary, directory=False)
    if _backup_present(temporary):
        _unlink(temporary, guard, "record_temp_remove")
    stage = record.get("stage")
    if stage is not None:
        stage_path = Path(stage)
        _assert_safe_target(root, stage_path, directory=True)
        if _backup_present(stage_path):
            _remove_tree(stage_path, guard, "stage_remove")
    _remove_created_parents(root, record, guard)
    _assert_safe_target(root, marker, directory=False)
    if _backup_present(marker):
        _unlink(marker, guard, "marker_remove")


def _classify_payload(root: Path, record: dict) -> str:
    opt = root / "opt/happyranch"
    payload_backup, _unit_backup, _marker = _transaction_paths(root)
    old_payload = record["backups"]["payload"]
    if record["payload_present"]:
        if old_payload is None:
            raise diagnostic_error("P000174", "transaction_state_invalid", PackageError("transaction_state_invalid"))
        if _backup_present(payload_backup):
            if not _tree_matches(payload_backup, old_payload):
                raise diagnostic_error("P000175", "transaction_state_invalid", PackageError("transaction_state_invalid"))
            return "already" if _tree_matches(opt, old_payload, absent_ok=True) else "restore"
        if _tree_matches(opt, old_payload):
            return "already"
        raise diagnostic_error("P000176", "transaction_state_invalid", PackageError("transaction_state_invalid"))
    if old_payload is not None or _backup_present(payload_backup):
        raise diagnostic_error("P000177", "transaction_state_invalid", PackageError("transaction_state_invalid"))
    if opt.is_symlink():
        raise diagnostic_error("P000178", "transaction_state_invalid", PackageError("transaction_state_invalid"))
    if not opt.exists():
        return "nothing"
    if _tree_matches(opt, record["new_payload"]):
        return "remove"
    # A fresh rollback publishes ``rolling_back`` before it unlinks any member,
    # so a partial NEW tree that is a strict subset of the recorded NEW
    # inventory is genuine resumable progress rather than foreign content.
    if record["phase"] == "rolling_back" and _tree_is_owned_partial(opt, record["new_payload"]):
        return "remove"
    raise diagnostic_error("P000179", "transaction_state_invalid", PackageError("transaction_state_invalid"))


def _classify_units(root: Path, record: dict) -> dict[str, str]:
    units = root / "etc/systemd/system"
    _payload_backup, unit_backup, _marker = _transaction_paths(root)
    actions: dict[str, str] = {}
    for unit in UNITS:
        target = units / unit
        saved = unit_backup / unit
        expected = record["backups"]["units"][unit]
        if record["units"][unit]:
            if expected is None:
                raise diagnostic_error("P000180", "transaction_state_invalid", PackageError("transaction_state_invalid"))
            if _backup_present(saved):
                if (failure := _backup_intact_error(saved, expected)) is not None:
                    error = diagnostic_error("P000181", "transaction_state_invalid", PackageError("transaction_state_invalid"))
                    origin = exception_refusal(failure)
                    if origin is not None:
                        diagnostic_propagate({"category": origin[0], "site": origin[1]}, error)
                    raise error
                actions[unit] = "already" if _file_matches(target, expected, absent_ok=True) else "restore"
            elif _file_matches(target, expected):
                actions[unit] = "already"
            else:
                raise diagnostic_error("P000182", "transaction_state_invalid", PackageError("transaction_state_invalid"))
        else:
            if expected is not None:
                raise diagnostic_error("P000183", "transaction_state_invalid", PackageError("transaction_state_invalid"))
            if target.is_symlink():
                raise diagnostic_error("P000184", "transaction_state_invalid", PackageError("transaction_state_invalid"))
            if not target.exists():
                actions[unit] = "nothing"
            elif unit in record["published_units"]:
                actions[unit] = "remove"
            else:
                raise diagnostic_error("P000185", "transaction_state_invalid", PackageError("transaction_state_invalid"))
    return actions


def _classify_dropin(root: Path, record: dict) -> str:
    units = root / "etc/systemd/system"
    dropin = units / _DROPIN_SERVICE_DIR / _DROPIN_FILE_NAME
    _payload_backup, unit_backup, _marker = _transaction_paths(root)
    dropin_backup = unit_backup / _DROPIN_BACKUP_RELATIVE
    expected = record["backups"]["dropin"]
    if record["dropin_present"]:
        if expected is None:
            raise diagnostic_error("P000186", "transaction_state_invalid", PackageError("transaction_state_invalid"))
        if _backup_present(dropin_backup):
            if (failure := _backup_intact_error(dropin_backup, expected)) is not None:
                error = diagnostic_error("P000187", "transaction_state_invalid", PackageError("transaction_state_invalid"))
                origin = exception_refusal(failure)
                if origin is not None:
                    diagnostic_propagate({"category": origin[0], "site": origin[1]}, error)
                raise error
            return "already" if _file_matches(dropin, expected, absent_ok=True) else "restore"
        if _file_matches(dropin, expected):
            return "already"
        raise diagnostic_error("P000188", "transaction_state_invalid", PackageError("transaction_state_invalid"))
    if expected is not None or _backup_present(dropin_backup):
        raise diagnostic_error("P000189", "transaction_state_invalid", PackageError("transaction_state_invalid"))
    if dropin.is_symlink():
        raise diagnostic_error("P000190", "transaction_state_invalid", PackageError("transaction_state_invalid"))
    if not dropin.exists():
        return "nothing"
    if record["dropin_published"]:
        return "remove"
    raise diagnostic_error("P000191", "transaction_state_invalid", PackageError("transaction_state_invalid"))


def _assert_committed_active_new(root: Path, record: Mapping[str, object]) -> None:
    """Refuse a committed record whose active installation is not complete NEW.

    The committed phase is authoritative only when the active payload, every
    published unit and the applicable enrollment drop-in equal the exact
    identities the record intended to publish.  A contradictory record (for
    example a real ``units_publishing`` record whose phase was changed to
    ``committed`` while the final unit write never happened) must be refused
    *before* cleanup deletes the recorded OLD backups, so a mixed installation
    can never be silently promoted.  Commit validity is never inferred from
    the mere absence of backups.
    """
    opt = root / "opt/happyranch"
    if not _tree_matches(opt, record["new_payload"]):
        raise diagnostic_error("P000192", "transaction_state_invalid", PackageError("transaction_state_invalid"))
    units = root / "etc/systemd/system"
    new_units = record["new_units"]
    for unit in UNITS:
        if not _file_matches(units / unit, new_units[unit]):
            raise diagnostic_error("P000193", "transaction_state_invalid", PackageError("transaction_state_invalid"))
    dropin = units / _DROPIN_SERVICE_DIR / _DROPIN_FILE_NAME
    if record["new_dropin"] is not None:
        if not _file_matches(dropin, record["new_dropin"]):
            raise diagnostic_error("P000194", "transaction_state_invalid", PackageError("transaction_state_invalid"))
        return
    # No NEW drop-in was published: the unchanged-prior-drop-in branch must
    # still match its recorded prior identity, or be genuinely absent.
    if record["dropin_present"]:
        if not _file_matches(dropin, record["backups"]["dropin"]):
            raise diagnostic_error("P000195", "transaction_state_invalid", PackageError("transaction_state_invalid"))
    elif dropin.exists() or dropin.is_symlink():
        raise diagnostic_error("P000196", "transaction_state_invalid", PackageError("transaction_state_invalid"))


def _restore_old(root: Path, record: dict, guard) -> None:
    """Restore the last-known-good (OLD) composition or conservatively refuse.

    Every precondition (recorded backups intact, verified identity of any
    already-consumed backup, coherent payload/units/drop-in ownership) is
    classified BEFORE any mutation so that a refusal preserves all bytes and
    modes unchanged.  Restoring by atomic rename consumes a backup; the
    recorded OLD identity lets a later retry recognize the already-restored
    artifact instead of demanding the consumed backup again.
    """
    managed = _managed_package_root(root)
    if managed:
        _require_managed_package_context()
        _package_managed_history(_MANAGED_DEADLINE.get())
    opt = root / "opt/happyranch"
    units = root / "etc/systemd/system"
    dropin = units / _DROPIN_SERVICE_DIR / _DROPIN_FILE_NAME
    payload_backup, unit_backup, _marker = _transaction_paths(root)
    dropin_backup = unit_backup / _DROPIN_BACKUP_RELATIVE

    _assert_record_paths_safe(root, record)
    # Validate the actual tree that restore may remove before publishing any
    # rollback intent. The classifiers below still decide OLD/NEW ownership.
    trees = [opt, payload_backup, unit_backup]
    if record.get("stage") is not None:
        trees.append(Path(record["stage"]))
    for path in trees:
        if _backup_present(path):
            _inventory_tree(path, metadata_only=True)
    payload_action = _classify_payload(root, record)
    unit_actions = _classify_units(root, record)
    dropin_action = _classify_dropin(root, record)

    if record["phase"] != "rolling_back":
        record = _publish(root, record, guard, phase="rolling_back")

    if payload_action == "restore":
        if opt.exists() or opt.is_symlink():
            _remove_tree(opt, guard, "rollback_payload_remove")
        if managed:
            _require_managed_package_context()
        with diagnostic_scope("P100094", "managed_operational_error"):
            opt.parent.mkdir(parents=True, exist_ok=True)
        if managed:
            _require_managed_package_context()
        _replace(payload_backup, opt, guard, "rollback_payload_restore")
    elif payload_action == "remove":
        _remove_tree(opt, guard, "rollback_payload_remove")

    for unit in UNITS:
        action = unit_actions[unit]
        target = units / unit
        if action == "restore":
            if target.exists() or target.is_symlink():
                _unlink(target, guard, "rollback_unit_unlink")
            _replace(unit_backup / unit, target, guard, "rollback_unit_restore")
        elif action == "remove":
            if target.exists() or target.is_symlink():
                _unlink(target, guard, "rollback_unit_unlink")

    if dropin_action == "restore":
        if not dropin.parent.exists():
            if managed:
                _require_managed_package_context()
            with diagnostic_scope("P100095", "managed_operational_error"):
                dropin.parent.mkdir(parents=True, exist_ok=True)
            if managed:
                _require_managed_package_context()
        _replace(dropin_backup, dropin, guard, "rollback_dropin_restore")
    elif dropin_action == "remove":
        _unlink(dropin, guard, "rollback_dropin_unlink")

    _cleanup_owned(root, record, guard)


def _cleanup_pre_record(root: Path, stage: Path | None, guard) -> None:
    if _managed_package_root(root):
        _require_managed_package_context()
    temporary = _record_temp(root)
    _assert_safe_target(root, temporary, directory=False)
    if stage is not None:
        _assert_safe_target(root, Path(stage), directory=True)
        if _backup_present(Path(stage)):
            _inventory_tree(Path(stage), metadata_only=True)
    if _backup_present(temporary):
        _unlink(temporary, guard, "record_temp_remove")
    if stage is not None:
        stage_path = Path(stage)
        _assert_safe_target(root, stage_path, directory=True)
        if _backup_present(stage_path):
            _remove_tree(stage_path, guard, "stage_remove")


def _recover_interrupted(root: Path, guard=None) -> None:
    """Classify and recover an owned transaction, or refuse an ambiguous one.

    Conservative disposition: a legacy schema-v1 marker, an incomplete
    record, or unrecorded preparation residue is preserved unchanged and
    refused with the existing ``transaction_state_invalid`` category.  Genuine
    owned transactions are recovered to OLD (uncommitted) or completed NEW
    (committed).
    """
    if _managed_package_root(root):
        _require_managed_package_context()
        _installed_managed_contract(deadline_ns=_MANAGED_DEADLINE.get(), _package=True)
    _assert_safe_root_ancestry(root)
    if not root.exists() and not root.is_symlink():
        return
    if root.is_symlink() or not root.is_dir():
        raise diagnostic_error("P000197", "transaction_state_invalid", PackageError("transaction_state_invalid"))
    marker = root / TRANSACTION_MARKER
    _assert_safe_target(root, marker, directory=False)
    if not marker.exists():
        if marker.is_symlink():
            raise diagnostic_error("P000198", "transaction_state_invalid", PackageError("transaction_state_invalid"))
        with diagnostic_scope("P100096", "managed_operational_error"):
            if any(entry.name.startswith(_RESIDUE_PREFIXES) for entry in root.iterdir()):
                raise diagnostic_error("P000199", "transaction_state_invalid", PackageError("transaction_state_invalid"))
        return
    record = _load_record(root, marker)
    _assert_record_paths_safe(root, record)
    if record["phase"] == "committed":
        # ``committed`` is authoritative NEW only when the active installation
        # genuinely equals the recorded complete NEW identities; a
        # contradictory record is refused before cleanup can delete OLD.
        _assert_committed_active_new(root, record)
        _cleanup_owned(root, record, guard)
        return
    if record["phase"] == "preparing":
        # ``preparing`` never mutated OLD: recover by removing only exact
        # recorded owned residue.
        _cleanup_owned(root, record, guard)
        return
    _restore_old(root, record, guard)


def install_linux_package(
    package: Path,
    root: Path,
    *,
    system_service: bool = False,
    fault: Callable[[str], None] | None = None,
    guard=None,
) -> dict[str, object]:
    if type(system_service) is not bool:
        raise diagnostic_error("P000200", "install_mode_invalid", PackageError("install_mode_invalid"))
    deadline = _managed_entry_deadline(180)
    managed = _managed_package_root(root)
    if system_service or managed:
        if not managed or not system_service or fault is not None or guard is not None:
            raise diagnostic_error("P000201", "managed_package_context_required", PackageError("managed_package_context_required"))
        if not _MANAGED_PACKAGE.get():
            return _managed_package_outer(package, "package-install", deadline)
        _require_managed_package_context()
    if system_service:
        with diagnostic_scope("P300008", "managed_operational_error", boundary=True):
            require_credential_capability(
                root / "etc/happyranch/daemon.token", expected_uid=os.geteuid()
            )
        enrollment_source = root / "etc/happyranch/enrollment.key"
        if enrollment_source.exists() or enrollment_source.is_symlink():
            with diagnostic_scope("P300009", "managed_operational_error", boundary=True):
                require_credential_capability(
                    enrollment_source, expected_uid=os.geteuid()
                )
    files, manifest = _read_verified(package)
    if system_service:
        _installed_managed_contract(deadline_ns=deadline, _package=True)
    _recover_interrupted(root, guard)
    if system_service:
        _installed_managed_contract(deadline_ns=deadline, _package=True)
    opt = root / "opt/happyranch"
    if _backup_present(opt):
        _inventory_tree(opt, metadata_only=True)
    _require_package_operand(root, directory=True)
    with diagnostic_scope("P100097", "managed_operational_error"):
        root.mkdir(parents=True, exist_ok=True)
    units = root / "etc/systemd/system"
    dropin_dir = units / _DROPIN_SERVICE_DIR
    dropin = dropin_dir / _DROPIN_FILE_NAME
    credential_source = root / "etc/happyranch/enrollment.key"
    publishes_dropin = system_service and credential_source.is_file()
    # Ownership/type preflight: no write or delete may follow a symlink or an
    # unexpected type at any target or ancestor below the selected root.
    typed_targets: list[tuple[Path, bool]] = [
        (root, True), (opt, True), (units, True), (dropin_dir, True), (dropin, False),
        (root / TRANSACTION_MARKER, False), (_record_temp(root), False),
        (root / _PAYLOAD_BACKUP_NAME, True), (root / _UNIT_BACKUP_NAME, True),
    ]
    typed_targets.extend((units / unit, False) for unit in UNITS)
    for path, directory in typed_targets:
        _assert_safe_target(root, path, directory=directory)
    payload_present = opt.exists()
    dropin_present = dropin.exists()
    unit_present = {name: (units / name).exists() for name in UNITS}
    created_parents = _planned_created_parents(root, include_dropin_dir=publishes_dropin)
    new_payload = _planned_payload_inventory(files, system_service)
    new_units = {
        unit: {"sha256": _sha(files[f"systemd/{unit}"]), "mode": 0o644 if system_service else 0o600} for unit in UNITS
    }
    new_dropin = {"sha256": _sha(_DROPIN_BYTES), "mode": 0o644} if publishes_dropin else None
    checkpoint = fault or (lambda _name: None)
    attempt_id = secrets.token_hex(16)
    stage: Path | None = None
    try:
        _seam(guard, "before", "stage_create", root)
        with diagnostic_scope("P100098", "managed_operational_error"):
            stage = Path(tempfile.mkdtemp(prefix=f"{_STAGE_PREFIX}{attempt_id}-", dir=root))
        _seam(guard, "after", "stage_create", stage)
        _seam(guard, "before", "stage_chmod", stage)
        with diagnostic_scope("P100099", "managed_operational_error"):
            stage.chmod(0o755 if system_service else 0o700)
        _seam(guard, "after", "stage_chmod", stage)
        record = {
            "schema_version": TRANSACTION_SCHEMA_VERSION,
            "attempt_id": attempt_id,
            "root": str(root),
            "phase": "preparing",
            "payload_present": payload_present,
            "units": dict(unit_present),
            "dropin_present": dropin_present,
            "stage": str(stage),
            "created_parents": created_parents,
            "published_units": [],
            "dropin_published": False,
            "backups": {"payload": None, "units": {name: None for name in UNITS}, "dropin": None},
            "new_payload": new_payload,
            "new_units": new_units,
            "new_dropin": new_dropin,
        }
        record = _publish(root, record, guard)

        for relative in ("opt", "etc", "etc/systemd", "etc/systemd/system"):
            _ensure_dir(root / relative, 0o755, guard, "parent")
        if publishes_dropin:
            _ensure_dir(dropin_dir, 0o755, guard, "parent")

        for name, raw in files.items():
            if name == "manifest.json" or name.startswith("systemd/"):
                continue
            target = stage / name
            _ensure_dir(target.parent, 0o755 if system_service else 0o700, guard, "stage_dir")
            with diagnostic_scope("P100100", "managed_operational_error"):
                mode = int(PAYLOAD_MODES[name], 8) if system_service else (0o700 if name.startswith("bin/") else 0o600)
            _write_file(target, raw, mode, guard, f"stage_payload:{name}")
        _write_file(stage / "manifest.json", files["manifest.json"], 0o644 if system_service else 0o600, guard, "stage_manifest")
        if not _tree_matches(stage, new_payload):
            raise diagnostic_error("P000202", "transaction_state_invalid", PackageError("transaction_state_invalid"))

        unit_backup = root / _UNIT_BACKUP_NAME
        _ensure_dir(unit_backup, 0o700, guard, "unit_backup")
        inventory_units: dict[str, dict | None] = {}
        for unit in UNITS:
            target = units / unit
            if target.exists():
                destination = unit_backup / unit
                _copy_file(target, destination, guard, f"unit_backup:{unit}")
                inventory_units[unit] = _path_file_identity(destination)
            else:
                inventory_units[unit] = None
        inventory_dropin: dict | None = None
        if dropin_present:
            _ensure_dir(unit_backup / _DROPIN_SERVICE_DIR, 0o700, guard, "dropin_backup_dir")
            destination = unit_backup / _DROPIN_BACKUP_RELATIVE
            _copy_file(dropin, destination, guard, "dropin_backup")
            inventory_dropin = _path_file_identity(destination)
        # The complete OLD payload identity is recorded BEFORE the retain so a
        # later restore can validate it and recognize an already-restored tree.
        inventory_payload = _inventory_tree(opt) if payload_present else None
        record = _publish(
            root, record, guard, phase="prepared",
            backups={"payload": inventory_payload, "units": inventory_units, "dropin": inventory_dropin},
        )

        payload_backup = root / _PAYLOAD_BACKUP_NAME
        if payload_present:
            _replace(opt, payload_backup, guard, "payload_retain")
        record = _publish(root, record, guard, phase="payload_retained")
        checkpoint("payload_old_retained")

        _replace(stage, opt, guard, "payload_publish")
        record = _publish(root, record, guard, phase="payload_published")
        checkpoint("payload_published")

        record = _publish(root, record, guard, phase="units_publishing")
        for unit in UNITS:
            target = units / unit
            if not unit_present[unit] and (target.exists() or target.is_symlink()):
                raise diagnostic_error("P000203", "transaction_state_invalid", PackageError("transaction_state_invalid"))
            # Publish the intent before the mutation so a torn write is still
            # classified as an owned NEW artifact by recovery.
            record = _publish(
                root, record, guard, published_units=[*record["published_units"], unit]
            )
            _write_file(target, files[f"systemd/{unit}"], 0o644 if system_service else 0o600, guard, f"unit_publish:{unit}")
            checkpoint(f"unit_published:{unit}")

        record = _publish(root, record, guard, phase="dropin_publishing")
        if publishes_dropin:
            record = _publish(root, record, guard, dropin_published=True)
            _write_file(dropin, _DROPIN_BYTES, 0o644, guard, "dropin_publish")

        # The authoritative commit is the durable publication of the committed
        # record: a failure before it restores OLD, a failure after it retains NEW.
        record = _publish(root, record, guard, phase="committed")
        _cleanup_owned(root, record, guard)
    except Exception:
        marker = root / TRANSACTION_MARKER
        if not marker.exists() and not marker.is_symlink():
            _cleanup_pre_record(root, stage, guard)
        else:
            durable = _load_record(root, marker)
            _assert_record_paths_safe(root, durable)
            if durable["phase"] == "committed":
                _assert_committed_active_new(root, durable)
                _cleanup_owned(root, durable, guard)
            elif durable["phase"] == "preparing":
                _cleanup_owned(root, durable, guard)
            else:
                _restore_old(root, durable, guard)
        raise
    if system_service:
        _installed_managed_contract(deadline_ns=deadline, _package=True)
        _require_managed_package_context()
    return {"version": manifest["version"], "manifest_sha256": _sha(files["manifest.json"])}


def uninstall_linux_package(root: Path) -> None:
    deadline = _managed_entry_deadline(180)
    managed = _managed_package_root(root)
    if managed:
        if not _MANAGED_PACKAGE.get():
            _managed_package_outer(None, "package-uninstall", deadline)
            return
        _require_managed_package_context()
    if managed:
        _installed_managed_contract(deadline_ns=deadline, _package=True)
        _recover_interrupted(root)
        _installed_managed_contract(deadline_ns=deadline, _package=True)
    opt = root / "opt/happyranch"
    _require_package_operand(opt, directory=True)
    for unit in UNITS:
        _require_package_operand(root / "etc/systemd/system" / unit, directory=False)
    if _backup_present(opt):
        _inventory_tree(opt, metadata_only=True)
    if opt.exists():
        if managed:
            _remove_tree(opt, None, "uninstall_payload")
        else:
            with diagnostic_scope("P100101", "managed_operational_error"):
                shutil.rmtree(opt)
    for unit in UNITS:
        path = root / "etc/systemd/system" / unit
        if path.exists():
            if managed:
                _unlink(path, None, "uninstall_unit")
            else:
                with diagnostic_scope("P100102", "managed_operational_error"):
                    path.unlink()
    if managed:
        dropin = root / "etc/systemd/system" / _DROPIN_SERVICE_DIR / _DROPIN_FILE_NAME
        if dropin.exists():
            _unlink(dropin, None, "uninstall_dropin")
        _require_managed_package_context()
        if any(_backup_present(path) for path in (opt, *(root / "etc/systemd/system" / u for u in UNITS), dropin)):
            raise diagnostic_error("P000204", "managed_uninstall_incomplete", PackageError("managed_uninstall_incomplete"))


def _package_managed_history(deadline_ns: int) -> tuple[Path | None, dict[str, Path], Path | None, bool]:
    """Select complete source-owned OLD/NEW without changing schema2 recovery.

    This is root package admission only. Runtime/deferred readers never call it.
    A marker supplies exact ownership, not a bypass of the composition reader.
    """
    root = Path("/")
    active = root / "opt/happyranch"
    units = root / "etc/systemd/system"
    dropin = units / _DROPIN_SERVICE_DIR / _DROPIN_FILE_NAME
    backup, saved_units, marker = _transaction_paths(root)

    def current() -> None:
        if time.monotonic_ns() >= deadline_ns:
            raise diagnostic_error("P000205", "managed_lifecycle_deadline", PackageError("managed_lifecycle_deadline"))

    def owned(path: Path, *, private_container: bool = False) -> None:
        current()
        _assert_safe_target(root, path)
        if not _backup_present(path):
            return
        pending = [path]
        while pending:
            current()
            selected = pending.pop()
            with diagnostic_scope("P100103", "managed_operational_error"):
                metadata = selected.lstat()
            if (metadata.st_uid != 0 or metadata.st_mode & 0o022
                    or stat.S_ISREG(metadata.st_mode) and metadata.st_nlink != 1
                    or not (stat.S_ISDIR(metadata.st_mode) or stat.S_ISREG(metadata.st_mode))):
                raise diagnostic_error("P000206", "managed_lifecycle_incompatible", PackageError("managed_lifecycle_incompatible"))
            if stat.S_ISDIR(metadata.st_mode):
                if private_container and stat.S_IMODE(metadata.st_mode) != 0o700:
                    raise diagnostic_error("P000207", "managed_lifecycle_incompatible", PackageError("managed_lifecycle_incompatible"))
                with diagnostic_scope("P100104", "managed_operational_error"):
                    pending.extend(selected.iterdir())

    for parent in (Path("/opt"), Path("/etc"), Path("/etc/systemd"), units):
        _assert_safe_target(root, parent, directory=True)
        if _backup_present(parent):
            with diagnostic_scope("P100105", "managed_operational_error"):
                metadata = parent.lstat()
            if metadata.st_uid != 0 or metadata.st_mode & 0o022:
                raise diagnostic_error("P000208", "managed_lifecycle_incompatible", PackageError("managed_lifecycle_incompatible"))
    if _backup_present(dropin.parent):
        _assert_safe_target(root, dropin.parent, directory=True)
        with diagnostic_scope("P100106", "managed_operational_error"):
            if stat.S_IMODE(dropin.parent.lstat().st_mode) != 0o755 or {p.name for p in dropin.parent.iterdir()} - {_DROPIN_FILE_NAME}:
                raise diagnostic_error("P000209", "managed_lifecycle_incompatible", PackageError("managed_lifecycle_incompatible"))
    for path in (active, backup, saved_units, marker, _record_temp(root), dropin.parent):
        owned(path, private_container=path == saved_units)
    temporary = _record_temp(root)
    with diagnostic_scope("P100107", "managed_operational_error"):
        if _backup_present(temporary) and stat.S_IMODE(temporary.lstat().st_mode) != 0o600:
            raise diagnostic_error("P000210", "transaction_state_invalid", PackageError("transaction_state_invalid"))
    if not _backup_present(marker):
        with diagnostic_scope("P100108", "managed_operational_error"):
            if any(p.name.startswith(_RESIDUE_PREFIXES) for p in root.iterdir()):
                raise diagnostic_error("P000211", "managed_transaction_pending", PackageError("managed_transaction_pending"))
        if not _backup_present(active):
            if any(_backup_present(units / u) for u in UNITS) or _backup_present(dropin):
                raise diagnostic_error("P000212", "managed_lifecycle_incompatible", PackageError("managed_lifecycle_incompatible"))
            return None, {}, None, True
        for unit in UNITS:
            owned(units / unit)
        owned(dropin)
        current()
        return active, {u: units / u for u in UNITS}, dropin if _backup_present(dropin) else None, False
    with diagnostic_scope("P100109", "managed_operational_error"):
        if stat.S_IMODE(marker.lstat().st_mode) != 0o600:
            raise diagnostic_error("P000213", "transaction_state_invalid", PackageError("transaction_state_invalid"))
    record = _load_record(root, marker)
    _assert_record_paths_safe(root, record)
    stage = Path(record["stage"])
    allowed = {marker.name, _record_temp(root).name, backup.name, saved_units.name, stage.name}
    with diagnostic_scope("P100110", "managed_operational_error"):
        if any(p.name.startswith(_RESIDUE_PREFIXES) and p.name not in allowed for p in root.iterdir()):
            raise diagnostic_error("P000214", "transaction_state_invalid", PackageError("transaction_state_invalid"))
    owned(stage)
    # The exact declared NEW class is closed even when staging was interrupted
    # before its manifest was written. No arbitrary path/legacy inventory enters.
    new = record["new_payload"]
    names = {"bin", "share", "manifest.json"} | {n for n in PAYLOAD_MODES if not n.startswith("systemd/")}
    if new["root_mode"] != 0o755 or set(new["entries"]) != names:
        raise diagnostic_error("P000215", "managed_lifecycle_incompatible", PackageError("managed_lifecycle_incompatible"))
    for name, identity in new["entries"].items():
        directory = name in {"bin", "share"}
        with diagnostic_scope("P100111", "managed_operational_error"):
            mode = 0o755 if directory else 0o644 if name == "manifest.json" else int(PAYLOAD_MODES[name], 8)
        if (identity.get("type") != ("dir" if directory else "file") or identity.get("mode") != mode
                or not directory and re.fullmatch(r"[0-9a-f]{64}", identity["sha256"]) is None):
            raise diagnostic_error("P000216", "managed_lifecycle_incompatible", PackageError("managed_lifecycle_incompatible"))
    with diagnostic_scope("P100112", "managed_operational_error"):
        rendered = render_composite_units()
    with diagnostic_scope("P100113", "managed_operational_error"):
        expected_units = {u: {"mode": 0o644, "sha256": _sha(rendered[u].encode())} for u in UNITS}
    if record["new_units"] != expected_units or record["new_dropin"] not in (None, {"mode": 0o644, "sha256": _sha(_DROPIN_BYTES)}):
        raise diagnostic_error("P000217", "managed_lifecycle_incompatible", PackageError("managed_lifecycle_incompatible"))
    if _backup_present(stage):
        if record["phase"] == "preparing":
            # The marker owns this exact allocated stage before its first file
            # write. Interrupted writes/chmod can be partial; OLD is untouched.
            # Names/types/custody must still be a subset of the closed plan.
            partial = _inventory_tree(stage)
            if partial["root_mode"] != 0o755 or not set(partial["entries"]) <= set(new["entries"]):
                raise diagnostic_error("P000218", "transaction_state_invalid", PackageError("transaction_state_invalid"))
            for name, identity in partial["entries"].items():
                intended = new["entries"][name]
                if identity["type"] != intended["type"] or identity["mode"] not in {intended["mode"], 0o700 if intended["type"] == "dir" or name.startswith("bin/") else 0o600}:
                    raise diagnostic_error("P000219", "transaction_state_invalid", PackageError("transaction_state_invalid"))
        elif not _tree_is_owned_partial(stage, new):
            raise diagnostic_error("P000220", "transaction_state_invalid", PackageError("transaction_state_invalid"))
    if record["phase"] == "committed":
        _assert_committed_active_new(root, record)
        # Cleanup may already have consumed interior backup entries. Only an
        # exact surviving subset is owned, never arbitrary retained residue.
        old = record["backups"]["payload"]
        if _backup_present(backup) and (old is None or not _tree_is_owned_partial(backup, old)):
            raise diagnostic_error("P000221", "transaction_state_invalid", PackageError("transaction_state_invalid"))
        prefix = active
        selected_units = {u: units / u for u in UNITS}
        selected_dropin = dropin if _backup_present(dropin) else None
    elif record["phase"] == "preparing":
        # No OLD mutation is possible before prepared. Validate actual OLD in
        # full below; incomplete copies remain private and must be its prefix.
        if _backup_present(backup):
            raise diagnostic_error("P000222", "transaction_state_invalid", PackageError("transaction_state_invalid"))
        if _backup_present(active) != record["payload_present"] or any(
                _backup_present(units / u) != record["units"][u] for u in UNITS
        ) or _backup_present(dropin) != record["dropin_present"]:
            raise diagnostic_error("P000223", "transaction_state_invalid", PackageError("transaction_state_invalid"))
        prefix = active if record["payload_present"] else None
        selected_units = {u: units / u for u in UNITS} if prefix is not None else {}
        selected_dropin = dropin if record["dropin_present"] else None
    else:
        _classify_payload(root, record)
        _classify_units(root, record)
        _classify_dropin(root, record)
        old = record["backups"]["payload"]
        prefix = (backup if _backup_present(backup) else active) if old is not None else None
        if prefix is not None and not _tree_matches(prefix, old):
            raise diagnostic_error("P000224", "transaction_state_invalid", PackageError("transaction_state_invalid"))
        if _backup_present(active) and not (
                old is not None and _tree_matches(active, old)
                or _tree_matches(active, new)
                or record["phase"] == "rolling_back" and _tree_is_owned_partial(active, new)):
            raise diagnostic_error("P000225", "transaction_state_invalid", PackageError("transaction_state_invalid"))
        selected_units = {}
        for unit in UNITS:
            identity = record["backups"]["units"][unit]
            if identity is not None:
                selected_units[unit] = saved_units / unit if _backup_present(saved_units / unit) else units / unit
                if not _file_matches(selected_units[unit], identity):
                    raise diagnostic_error("P000226", "transaction_state_invalid", PackageError("transaction_state_invalid"))
        identity = record["backups"]["dropin"]
        selected_dropin = None if identity is None else (
            saved_units / _DROPIN_BACKUP_RELATIVE if _backup_present(saved_units / _DROPIN_BACKUP_RELATIVE) else dropin)
        if selected_dropin is not None and not _file_matches(selected_dropin, identity):
            raise diagnostic_error("P000227", "transaction_state_invalid", PackageError("transaction_state_invalid"))
    if (prefix is None and selected_units or prefix is not None and set(selected_units) != set(UNITS)):
        raise diagnostic_error("P000228", "managed_lifecycle_incompatible", PackageError("managed_lifecycle_incompatible"))
    # Validate fixed backups and in-progress active unit/drop-in writes, including
    # intent-before-truncation. Unknown files are preserved/refused before cleanup.
    if _backup_present(saved_units):
        expected_names = set(UNITS) | {_DROPIN_SERVICE_DIR}
        with diagnostic_scope("P100114", "managed_operational_error"):
            if any(p.name not in expected_names for p in saved_units.iterdir()):
                raise diagnostic_error("P000229", "transaction_state_invalid", PackageError("transaction_state_invalid"))
        subdir = saved_units / _DROPIN_SERVICE_DIR
        with diagnostic_scope("P100115", "managed_operational_error"):
            if _backup_present(subdir) and {p.name for p in subdir.iterdir()} - {_DROPIN_FILE_NAME}:
                raise diagnostic_error("P000230", "transaction_state_invalid", PackageError("transaction_state_invalid"))
    for unit in (*UNITS, _DROPIN_BACKUP_RELATIVE):
        is_dropin = unit == _DROPIN_BACKUP_RELATIVE
        target = dropin if is_dropin else units / unit
        saved = saved_units / unit
        old_identity = record["backups"]["dropin"] if is_dropin else record["backups"]["units"][unit]
        source = selected_dropin if is_dropin else selected_units.get(unit)
        for path in (target, saved):
            owned(path)
        if _backup_present(saved):
            if record["phase"] == "preparing":
                with diagnostic_scope("P100116", "managed_operational_error"):
                    if source is None or not source.read_bytes().startswith(saved.read_bytes()) or stat.S_IMODE(saved.lstat().st_mode) not in {0o600, stat.S_IMODE(source.lstat().st_mode)}:
                        raise diagnostic_error("P000231", "transaction_state_invalid", PackageError("transaction_state_invalid"))
            elif old_identity is None or not _file_matches(saved, old_identity):
                raise diagnostic_error("P000232", "transaction_state_invalid", PackageError("transaction_state_invalid"))
        if _backup_present(target) and target != source:
            with diagnostic_scope("P100117", "managed_operational_error"):
                planned = _DROPIN_BYTES if is_dropin else rendered[unit].encode()
            published = record["dropin_published"] if is_dropin else unit in record["published_units"]
            with diagnostic_scope("P100118", "managed_operational_error"):
                if not (_file_matches(target, old_identity) or published and planned.startswith(target.read_bytes())
                        and stat.S_IMODE(target.lstat().st_mode) in {0o600, 0o644}):
                    raise diagnostic_error("P000233", "transaction_state_invalid", PackageError("transaction_state_invalid"))
    current()
    return prefix, selected_units, selected_dropin, not record["payload_present"] and not any(record["units"].values()) and not record["dropin_present"]


def _systemd_show_words(value: str) -> list[str]:
    # v255/v259 bus_print_property uses shell_maybe_quote for string arrays.
    # shlex removes ONLY this display quoting, never the unit's \xHH bytes.
    import shlex
    return shlex.split(value, comments=False, posix=True)


def _requires_mount_units(value: str) -> set[str]:
    result: set[str] = set()
    for path in _systemd_show_words(value):
        if not path.startswith('/') or any(c in ('..', '.') for c in path.split('/')) or '\x00' in path:
            raise ValueError('managed_mount_path_invalid')
        components = [c for c in path.split('/') if c]
        result.add('-.mount')
        for count in range(1, len(components) + 1):
            raw = '/'.join(components[:count]).encode('utf-8', 'strict')
            escaped = ''.join('-' if c == 47 else chr(c) if
                              (65 <= c <= 90 or 97 <= c <= 122 or 48 <= c <= 57 or c in (58, 95)
                               or c == 46 and i != 0) else f'\\x{c:02x}' for i, c in enumerate(raw))
            # systemd hashes long unit names. Refuse unsupported long paths;
            # never accept an unrelated hashed/suffix-only mount as authority.
            if len(escaped) + 6 >= 256:
                raise ValueError('managed_mount_path_invalid')
            result.add(escaped + '.mount')
    return result


def _installed_managed_contract(*, deadline_ns: int | None = None, _package: bool = False) -> dict[str, int]:
    """Independent current disk AND loaded PID1 proof, with no repair.

    Intended package inputs are the reviewed wheel build (pyproject runtime/cli
    and explicit data) and generate_inventory.py coordinates/license output.
    Digests authenticate those inputs; they do not certify arbitrary inputs as
    nonsecret. This reader cannot adopt a caller-selected installation.
    """
    deadline = (deadline_ns if _package and deadline_ns is not None else
                min(deadline_ns or time.monotonic_ns() + 2_000_000_000,
                    time.monotonic_ns() + 2_000_000_000))

    def current() -> None:
        if time.monotonic_ns() >= deadline:
            raise diagnostic_error("P000234", "managed_lifecycle_deadline", PackageError("managed_lifecycle_deadline"))

    def directory(path: Path, mode: int | None = None) -> None:
        current()
        _assert_safe_target(Path("/"), path, directory=True)
        with diagnostic_scope("P100119", "managed_operational_error"):
            observed = path.lstat()
        if (not stat.S_ISDIR(observed.st_mode) or observed.st_uid != 0
                or observed.st_mode & 0o022
                or mode is not None and stat.S_IMODE(observed.st_mode) != mode):
            raise diagnostic_error("P000235", "managed_lifecycle_incompatible", PackageError("managed_lifecycle_incompatible"))

    def read(path: Path, mode: int, cap: int) -> bytes:
        current()
        _assert_safe_target(Path("/"), path, directory=False)
        with diagnostic_scope("P100120", "managed_operational_error"):
            descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
        try:
            with diagnostic_scope("P100121", "managed_operational_error"):
                before = os.fstat(descriptor)
            if (not stat.S_ISREG(before.st_mode) or before.st_uid != 0
                    or before.st_nlink != 1 or stat.S_IMODE(before.st_mode) != mode
                    or not 0 < before.st_size <= cap):
                raise diagnostic_error("P000236", "managed_lifecycle_incompatible", PackageError("managed_lifecycle_incompatible"))
            with diagnostic_scope("P100122", "managed_operational_error"):
                with os.fdopen(os.dup(descriptor), "rb") as stream:
                    with diagnostic_scope("P100123", "managed_operational_error"):
                        raw = stream.read(cap + 1)
            with diagnostic_scope("P100124", "managed_operational_error"):
                after = os.fstat(descriptor)
            with diagnostic_scope("P100125", "managed_operational_error"):
                named = path.lstat()
            stable = ("st_dev", "st_ino", "st_mode", "st_uid", "st_gid", "st_nlink", "st_size", "st_mtime_ns", "st_ctime_ns")
            if (len(raw) != before.st_size or any(getattr(before, k) != getattr(after, k) for k in stable)
                    or (named.st_dev, named.st_ino) != (after.st_dev, after.st_ino)):
                raise diagnostic_error("P000237", "managed_lifecycle_incompatible", PackageError("managed_lifecycle_incompatible"))
            current()
            return raw
        finally:
            with diagnostic_scope("P100126", "managed_operational_error"):
                os.close(descriptor)

    base: dict[str, str] = {}

    def loaded_error(error: BaseException, prop: str, expected: str | None,
                     observed: str | None, relation: str = "equal", *, context=None,
                     missing=None, unexpected=None) -> BaseException:
        refusal = exception_refusal(error)
        if refusal is not None and refusal[1] in ("P000261", "P000262"):
            dependency_refusal_stderr(refusal[1], unit, prop, expected, observed,
                                      base if context is None else context,
                                      missing=missing, unexpected=unexpected)
        if exception_loaded(error) is None:
            error._managed_package_loaded = loaded_observation(UNITS.index(unit), prop, relation,
                                                              expected, observed, base if context is None else context,
                                                              missing=missing, unexpected=unexpected)
        return error

    def properties(unit: str, names: tuple[str, ...]) -> dict[str, str]:
        values: dict[str, str] = {}
        try:
            with diagnostic_scope("P100127", "managed_operational_error"):
                from runtime.remote_access.cli import _capture_bounded_query_output, _close_and_reap_query_process
            current()
            bound = min(deadline / 1_000_000_000, time.monotonic() + 1)
            with diagnostic_scope("P100128", "managed_operational_error"):
                child = subprocess.Popen(["/usr/bin/systemctl", "show", unit, *["--property=" + n for n in names]],
                                         stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                         stderr=subprocess.DEVNULL,
                                         env={"PATH": "/usr/bin:/bin", "LANG": "C", "LC_ALL": "C"},
                                         pass_fds=(() if _MANAGED_LOCK.get() is None else (_MANAGED_LOCK.get(),)))
            initiating = None
            try:
                raw, overflow = _capture_bounded_query_output(child, bound)
                remaining = bound - time.monotonic()
                with diagnostic_scope("P100129", "managed_operational_error"):
                    if overflow:
                        raise diagnostic_error("P000238", "managed_manager_unavailable", PackageError("managed_manager_unavailable"))
                    if remaining <= 0:
                        raise diagnostic_error("P300005", "managed_manager_unavailable", PackageError("managed_manager_unavailable"))
                    query_status = child.wait(timeout=remaining)
                    if query_status == 1:
                        raise diagnostic_error("P300006", "managed_manager_unavailable", PackageError("managed_manager_unavailable"))
                    if query_status != 0:
                        raise diagnostic_error("P300007", "managed_manager_unavailable", PackageError("managed_manager_unavailable"))
            except BaseException as error:
                initiating = loaded_error(error, "Query", "complete", None, "query", context={**base, **values})
                raise
            finally:
                try:
                    with diagnostic_scope("P100130", "managed_operational_error"):
                        _close_and_reap_query_process(child, min(bound, deadline / 1_000_000_000), managed=True)
                except BaseException as error:
                    if initiating is None:
                        raise
                    diagnostic_loss("P400040", "managed_operational_error", error)
            current()
            with diagnostic_scope("P100131", "managed_operational_error"):
                text = raw.decode("utf-8")
            if not text.endswith("\n"):
                raise diagnostic_error("P000239", "managed_manager_unavailable", PackageError("managed_manager_unavailable"))
            for line in text[:-1].split("\n"):
                key, separator, value = line.partition("=")
                if (not separator or key not in names or key in values
                        or any(ord(c) < 32 or ord(c) == 127 for c in line)):
                    raise diagnostic_error("P000240", "managed_manager_unavailable", PackageError("managed_manager_unavailable"))
                values[key] = value
            if set(values) != set(names):
                raise loaded_error(diagnostic_error("P000241", "managed_manager_unavailable", PackageError("managed_manager_unavailable")),
                                   "Query", None, None, "query", context={},
                                   missing=set(names) - set(values), unexpected=set())
            return values
        except Exception as error:
            loaded_error(error, "Query", "complete", None, "query", context={**base, **values})
            raise
    try:
        if _package:
            if os.geteuid() != 0:
                raise diagnostic_error("P000242", "managed_package_context_required", PackageError("managed_package_context_required"))
            prefix, unit_sources, selected_dropin, empty_prior = _package_managed_history(deadline_ns or deadline)
        else:
            prefix = Path("/opt/happyranch")
            unit_sources = {u: Path("/etc/systemd/system") / u for u in UNITS}
            selected_dropin = None
            empty_prior = False
        current()
        fresh = prefix is None
        with diagnostic_scope("P100132", "managed_operational_error"):
            private = not fresh and _package and stat.S_IMODE((prefix / "manifest.json").lstat().st_mode) == 0o600
        evidence_mode = 0o600 if private else 0o644
        share_mode = 0o700 if private else 0o755
        if fresh:
            # Explicit package bootstrap/recovery only: loaded absence is checked
            # below and the Go owner independently checks exact empty records.
            with diagnostic_scope("P100133", "managed_operational_error"):
                rendered = render_composite_units()
            dropin_present = False
        else:
            for path in (Path("/opt"), Path("/etc"), Path("/etc/systemd"), Path("/etc/systemd/system")):
                directory(path)
            directory(prefix, 0o755)
            directory(prefix / "bin", 0o755)
            directory(prefix / "share", share_mode)
            with diagnostic_scope("P100134", "managed_operational_error"):
                if {p.name for p in prefix.iterdir()} != {"manifest.json", "bin", "share"}:
                    raise diagnostic_error("P000243", "managed_lifecycle_incompatible", PackageError("managed_lifecycle_incompatible"))
            with diagnostic_scope("P100135", "managed_operational_error"):
                if {p.name for p in (prefix / "bin").iterdir()} != {"happyranch-connector", "happyranch-tsnet-sidecar"}:
                    raise diagnostic_error("P000244", "managed_lifecycle_incompatible", PackageError("managed_lifecycle_incompatible"))
            with diagnostic_scope("P100136", "managed_operational_error"):
                if {p.name for p in (prefix / "share").iterdir()} != {PurePosixPath(p).name for p in PAYLOAD_MODES if p.startswith("share/")}:
                    raise diagnostic_error("P000245", "managed_lifecycle_incompatible", PackageError("managed_lifecycle_incompatible"))
            with diagnostic_scope("P100137", "managed_operational_error"):
                manifest = json.loads(read(prefix / "manifest.json", evidence_mode, 16384),
                                      object_pairs_hook=_reject_duplicate_keys)
            if (type(manifest) is not dict or set(manifest) != {
                    "schema_version", "version", "architecture", "sidecar_dependency_count",
                    "managed_lifecycle_contract", "files"}
                    or type(manifest["schema_version"]) is not int or manifest["schema_version"] != 1
                    or manifest["architecture"] != "linux-amd64"
                    or not isinstance(manifest["version"], str) or not manifest["version"]):
                raise diagnostic_error("P000246", "managed_lifecycle_incompatible", PackageError("managed_lifecycle_incompatible"))
            contract = manifest["managed_lifecycle_contract"]
            if (type(contract) is not dict or contract != MANAGED_LIFECYCLE_CONTRACT
                    or any(type(value) is not int for value in contract.values())):
                raise diagnostic_error("P000247", "managed_lifecycle_incompatible", PackageError("managed_lifecycle_incompatible"))
            entries = manifest["files"]
            if (type(entries) is not list or len(entries) != len(PAYLOAD_MODES)
                    or any(type(e) is not dict or set(e) != {"path", "sha256", "mode"} for e in entries)
                    or {e["path"] for e in entries} != set(PAYLOAD_MODES)):
                raise diagnostic_error("P000248", "managed_lifecycle_incompatible", PackageError("managed_lifecycle_incompatible"))
            with diagnostic_scope("P100138", "managed_operational_error"):
                rendered = render_composite_units()
            payload: dict[str, bytes] = {}
            for entry in entries:
                relative = entry["path"]
                if (entry["mode"] != ("0o600" if private and not relative.startswith("bin/") else PAYLOAD_MODES[relative])
                        or not isinstance(entry["sha256"], str)
                        or re.fullmatch(r"[0-9a-f]{64}", entry["sha256"]) is None):
                    raise diagnostic_error("P000249", "managed_lifecycle_incompatible", PackageError("managed_lifecycle_incompatible"))
                path = (unit_sources[PurePosixPath(relative).name]
                        if relative.startswith("systemd/") else prefix / relative)
                with diagnostic_scope("P100139", "managed_operational_error"):
                    raw = read(path, int(entry["mode"], 8), 256 * 1024 * 1024)
                if _sha(raw) != entry["sha256"]:
                    raise diagnostic_error("P000250", "managed_lifecycle_incompatible", PackageError("managed_lifecycle_incompatible"))
                with diagnostic_scope("P100140", "managed_operational_error"):
                    if relative.startswith("systemd/") and raw != rendered[path.name].encode():
                        raise diagnostic_error("P000251", "managed_lifecycle_incompatible", PackageError("managed_lifecycle_incompatible"))
                payload[relative] = raw
            with diagnostic_scope("P100141", "managed_operational_error"):
                inventory = json.loads(payload["share/dependency-inventory.json"], object_pairs_hook=_reject_duplicate_keys)
            _validate_evidence(inventory, payload["share/THIRD_PARTY_NOTICES.md"])
            if (type(manifest["sidecar_dependency_count"]) is not int
                    or manifest["sidecar_dependency_count"] != len(inventory["modules"])
                    or payload["share/sbom.cdx.json"] != _sbom(inventory, manifest["version"])):
                raise diagnostic_error("P000252", "managed_lifecycle_incompatible", PackageError("managed_lifecycle_incompatible"))
            with diagnostic_scope("P100142", "managed_operational_error"):
                for entry in (() if _package else Path("/").iterdir()):
                    current()
                    if entry.name.startswith(_RESIDUE_PREFIXES):
                        raise diagnostic_error("P000253", "managed_transaction_pending", PackageError("managed_transaction_pending"))
            dropin_dir = Path("/etc/systemd/system") / _DROPIN_SERVICE_DIR
            dropin_present = False
            if not _package and dropin_dir.exists():
                directory(dropin_dir, None if _package else 0o755)
                with diagnostic_scope("P100143", "managed_operational_error"):
                    dropin_names = {p.name for p in dropin_dir.iterdir()}
                if dropin_names not in (set(), {_DROPIN_FILE_NAME}):
                    raise diagnostic_error("P000254", "managed_lifecycle_incompatible", PackageError("managed_lifecycle_incompatible"))
                dropin_present = bool(dropin_names)
                if dropin_present and read(dropin_dir / _DROPIN_FILE_NAME, 0o644, 256) != _DROPIN_BYTES:
                    raise diagnostic_error("P000255", "managed_lifecycle_incompatible", PackageError("managed_lifecycle_incompatible"))
            if _package:
                dropin_present = selected_dropin is not None
                with diagnostic_scope("P100144", "managed_operational_error"):
                    if selected_dropin is not None and (stat.S_IMODE(selected_dropin.lstat().st_mode) not in {0o600, 0o644} or read(selected_dropin, stat.S_IMODE(selected_dropin.lstat().st_mode), 256) != _DROPIN_BYTES):
                        raise diagnostic_error("P000256", "managed_lifecycle_incompatible", PackageError("managed_lifecycle_incompatible"))
        dropin_dir = Path("/etc/systemd/system") / _DROPIN_SERVICE_DIR

        def dependency_mismatch(detail: PackageError, *, required: set[str] | None = None) -> PackageError:
            # Original aggregate and unit/property detail remain immutable.
            # Private bounded observations supplement them, never public errors.
            error = diagnostic_error("P000262", "managed_loaded_contract_mismatch", PackageError("managed_loaded_contract_mismatch"))
            error._managed_package_boundary = exception_refusal(detail)
            missing = unexpected = None
            if checking in edges:
                permitted = set(allowed.split())  # These are fixed/derived unit names, not show quoting.
                observed_edges = (edges[checking] if relation == "equal" else
                                  {name for name in edges[checking] if happyranch_unit(name)})
                if required is None:
                    required = permitted if relation == "equal" else set()
                # Missing explicit network edges use their own equality check;
                # unrelated housekeeping is never an unexpected-edge delta.
                missing, unexpected = required - edges[checking], observed_edges - permitted
            return loaded_error(error, checking, allowed, base[checking], relation,
                                missing=missing, unexpected=unexpected)

        def happyranch_unit(name: str) -> bool:
            # Installed names reserve this literal namespace, including self,
            # additional units and templates; peers alone are insufficient.
            return name in UNITS or name.startswith(("happyranch-", "happyranch.", "happyranch@"))

        for unit in UNITS:
            base = {}  # Never borrow another unit's paths/observations.
            # An empty unexpected directory is also a changed unit input.
            if unit != UNITS[1] and (Path("/etc/systemd/system") / (unit + ".d")).exists():
                raise diagnostic_error("P000257", "managed_lifecycle_incompatible", PackageError("managed_lifecycle_incompatible"))
            base = properties(unit, ("LoadState", "FragmentPath", "DropInPaths", "NeedDaemonReload",
                                     "Wants", "Requires", "BindsTo", "PartOf", "After", "Before", "Conflicts", "RequiresMountsFor",
                                     "DefaultDependencies", "StopWhenUnneeded", "RefuseManualStart", "RefuseManualStop"))
            if fresh or _package and (empty_prior or _MANAGED_FRESH.get()):
                if base["LoadState"] == "not-found":
                    if base["FragmentPath"] or base["DropInPaths"] or base["NeedDaemonReload"] != "no":
                        expected_absent = {"FragmentPath": "", "DropInPaths": "", "NeedDaemonReload": "no"}
                        prop = next(k for k in expected_absent if base[k] != expected_absent[k])
                        raise loaded_error(diagnostic_error("P000258", "managed_loaded_contract_mismatch", PackageError("managed_loaded_contract_mismatch")), prop, expected_absent[prop], base[prop])
                    continue
                # An owned fresh transaction can already have published units
                # observed by PID1. Any actually loaded selection must pass the
                # same complete property proof, never an absence assumption.
            if _package:
                # Actual PID1 mapping is independently closed even while fixed
                # disk units are in an owned intent-before-write transition.
                if base["DropInPaths"] not in ("", str(dropin_dir / _DROPIN_FILE_NAME)):
                    raise loaded_error(diagnostic_error("P000259", "managed_loaded_contract_mismatch", PackageError("managed_loaded_contract_mismatch")), "DropInPaths", str(dropin_dir / _DROPIN_FILE_NAME), base["DropInPaths"])
                loaded_dropin = bool(base["DropInPaths"])
            else:
                loaded_dropin = dropin_present
            if (base["LoadState"] != "loaded" or base["NeedDaemonReload"] not in (("no", "yes") if _package else ("no",))
                    or base["FragmentPath"] != str(Path("/etc/systemd/system") / unit)
                    or base["DropInPaths"] != (str(dropin_dir / _DROPIN_FILE_NAME) if unit == UNITS[1] and loaded_dropin else "")):
                expected_mapping = {"LoadState": ("loaded",), "NeedDaemonReload": ("no", "yes") if _package else ("no",),
                                    "FragmentPath": (str(Path("/etc/systemd/system") / unit),),
                                    "DropInPaths": (str(dropin_dir / _DROPIN_FILE_NAME) if unit == UNITS[1] and loaded_dropin else "",)}
                prop = next(k for k in expected_mapping if base[k] not in expected_mapping[k])
                raise loaded_error(diagnostic_error("P000260", "managed_loaded_contract_mismatch", PackageError("managed_loaded_contract_mismatch")), prop, " ".join(expected_mapping[prop]), base[prop])
            edges = {}
            for prop in ("Wants", "Requires", "BindsTo", "PartOf", "After", "Before", "Conflicts"):
                try:
                    edges[prop] = set(_systemd_show_words(base[prop]))
                except ValueError as error:
                    raise loaded_error(diagnostic_error("P800035", "managed_loaded_contract_mismatch", PackageError("managed_loaded_contract_mismatch")), prop, "quoted unit names", base[prop]) from error
            peers = set(UNITS) - {unit}
            selected = {name: edges[name] & peers for name in ("Wants", "Requires", "BindsTo", "PartOf", "After", "Before")}
            expected = ({"Wants": {UNITS[1]}, "Requires": set(), "BindsTo": set(),
                         "PartOf": {UNITS[1], UNITS[2]}, "After": set(), "Before": {UNITS[2]}}
                        if unit == UNITS[0] else
                        {"Wants": set(), "Requires": set(), "BindsTo": set(),
                         "PartOf": {UNITS[0], UNITS[2]}, "After": set(), "Before": {UNITS[2]}}
                        if unit == UNITS[1] else
                        {"Wants": {UNITS[0], UNITS[1]}, "Requires": set(), "BindsTo": set(),
                         "PartOf": set(), "After": {UNITS[0], UNITS[1]}, "Before": set()})
            if selected != expected:
                prop = next(k for k in expected if selected[k] != expected[k])
                raise loaded_error(diagnostic_error("P000261", "managed_loaded_contract_mismatch", PackageError("managed_loaded_contract_mismatch")), prop, " ".join(sorted(expected[prop])), base[prop], "peers",
                                   missing=expected[prop] - selected[prop], unexpected=selected[prop] - expected[prop])
            # Founder THR327seq115: implicit housekeeping may vary with PID1.
            # Check ALL observed names for unexpected HappyRanch relationships,
            # while keeping binding relationships exact even for unrelated units.
            # Each OWN RequiresMountsFor still admits its derived mounts only in
            # Requires/After, never as BindsTo/PartOf authorization.
            try:
                mounts = _requires_mount_units(base["RequiresMountsFor"])
            except ValueError as error:
                raise loaded_error(diagnostic_error("P800034", "managed_loaded_contract_mismatch", PackageError("managed_loaded_contract_mismatch")), "RequiresMountsFor", "absolute normalized paths", base["RequiresMountsFor"]) from error
            checking, allowed, relation = "Wants", " ".join(sorted(expected["Wants"])), "subset"
            if {name for name in edges["Wants"] if happyranch_unit(name)} - expected["Wants"]:
                raise dependency_mismatch(
                    diagnostic_error("P800001", "managed_loaded_contract_mismatch", PackageError("managed_loaded_contract_mismatch")) if unit == UNITS[0] else
                    diagnostic_error("P800002", "managed_loaded_contract_mismatch", PackageError("managed_loaded_contract_mismatch")) if unit == UNITS[1] else
                    diagnostic_error("P800003", "managed_loaded_contract_mismatch", PackageError("managed_loaded_contract_mismatch")))
            if unit != UNITS[2] and "network-online.target" not in edges["Wants"]:
                checking, allowed, relation = "Wants", " ".join(sorted(expected["Wants"] | {"network-online.target"})), "subset"
                raise dependency_mismatch(
                    diagnostic_error("P800036", "managed_loaded_contract_mismatch", PackageError("managed_loaded_contract_mismatch")) if unit == UNITS[0] else
                    diagnostic_error("P800037", "managed_loaded_contract_mismatch", PackageError("managed_loaded_contract_mismatch")),
                    required={"network-online.target"})
            checking, allowed, relation = "Requires", " ".join(sorted(mounts)), "subset"
            if {name for name in edges["Requires"] if happyranch_unit(name)} - mounts:
                raise dependency_mismatch(
                    diagnostic_error("P800004", "managed_loaded_contract_mismatch", PackageError("managed_loaded_contract_mismatch")) if unit == UNITS[0] else
                    diagnostic_error("P800005", "managed_loaded_contract_mismatch", PackageError("managed_loaded_contract_mismatch")) if unit == UNITS[1] else
                    diagnostic_error("P800006", "managed_loaded_contract_mismatch", PackageError("managed_loaded_contract_mismatch")))
            checking, allowed, relation = "BindsTo", "", "equal"
            if base["BindsTo"]:
                raise dependency_mismatch(
                    diagnostic_error("P800007", "managed_loaded_contract_mismatch", PackageError("managed_loaded_contract_mismatch")) if unit == UNITS[0] else
                    diagnostic_error("P800008", "managed_loaded_contract_mismatch", PackageError("managed_loaded_contract_mismatch")) if unit == UNITS[1] else
                    diagnostic_error("P800009", "managed_loaded_contract_mismatch", PackageError("managed_loaded_contract_mismatch")))
            checking, allowed, relation = "PartOf", " ".join(sorted(expected["PartOf"])), "equal"
            if edges["PartOf"] != expected["PartOf"]:
                raise dependency_mismatch(
                    diagnostic_error("P800010", "managed_loaded_contract_mismatch", PackageError("managed_loaded_contract_mismatch")) if unit == UNITS[0] else
                    diagnostic_error("P800011", "managed_loaded_contract_mismatch", PackageError("managed_loaded_contract_mismatch")) if unit == UNITS[1] else
                    diagnostic_error("P800012", "managed_loaded_contract_mismatch", PackageError("managed_loaded_contract_mismatch")))
            checking, allowed, relation = "After", " ".join(sorted(expected["After"] | mounts)), "subset"
            if {name for name in edges["After"] if happyranch_unit(name)} - (expected["After"] | mounts):
                raise dependency_mismatch(
                    diagnostic_error("P800013", "managed_loaded_contract_mismatch", PackageError("managed_loaded_contract_mismatch")) if unit == UNITS[0] else
                    diagnostic_error("P800014", "managed_loaded_contract_mismatch", PackageError("managed_loaded_contract_mismatch")) if unit == UNITS[1] else
                    diagnostic_error("P800015", "managed_loaded_contract_mismatch", PackageError("managed_loaded_contract_mismatch")))
            if unit != UNITS[2] and "network-online.target" not in edges["After"]:
                checking, allowed, relation = "After", " ".join(sorted(expected["After"] | mounts | {"network-online.target"})), "subset"
                raise dependency_mismatch(
                    diagnostic_error("P800038", "managed_loaded_contract_mismatch", PackageError("managed_loaded_contract_mismatch")) if unit == UNITS[0] else
                    diagnostic_error("P800039", "managed_loaded_contract_mismatch", PackageError("managed_loaded_contract_mismatch")),
                    required={"network-online.target"})
            checking, allowed, relation = "Before", " ".join(sorted(expected["Before"])), "subset"
            if {name for name in edges["Before"] if happyranch_unit(name)} - expected["Before"]:
                raise dependency_mismatch(
                    diagnostic_error("P800016", "managed_loaded_contract_mismatch", PackageError("managed_loaded_contract_mismatch")) if unit == UNITS[0] else
                    diagnostic_error("P800017", "managed_loaded_contract_mismatch", PackageError("managed_loaded_contract_mismatch")) if unit == UNITS[1] else
                    diagnostic_error("P800018", "managed_loaded_contract_mismatch", PackageError("managed_loaded_contract_mismatch")))
            checking, allowed, relation = "Conflicts", "", "subset"
            if any(happyranch_unit(name) for name in edges["Conflicts"]):
                raise dependency_mismatch(
                    diagnostic_error("P800019", "managed_loaded_contract_mismatch", PackageError("managed_loaded_contract_mismatch")) if unit == UNITS[0] else
                    diagnostic_error("P800020", "managed_loaded_contract_mismatch", PackageError("managed_loaded_contract_mismatch")) if unit == UNITS[1] else
                    diagnostic_error("P800021", "managed_loaded_contract_mismatch", PackageError("managed_loaded_contract_mismatch")))
            checking, allowed, relation = "DefaultDependencies", "yes", "equal"
            if base["DefaultDependencies"] != "yes":
                raise dependency_mismatch(
                    diagnostic_error("P800022", "managed_loaded_contract_mismatch", PackageError("managed_loaded_contract_mismatch")) if unit == UNITS[0] else
                    diagnostic_error("P800023", "managed_loaded_contract_mismatch", PackageError("managed_loaded_contract_mismatch")) if unit == UNITS[1] else
                    diagnostic_error("P800024", "managed_loaded_contract_mismatch", PackageError("managed_loaded_contract_mismatch")))
            checking, allowed, relation = "StopWhenUnneeded", "no", "equal"
            if base["StopWhenUnneeded"] != "no":
                raise dependency_mismatch(
                    diagnostic_error("P800025", "managed_loaded_contract_mismatch", PackageError("managed_loaded_contract_mismatch")) if unit == UNITS[0] else
                    diagnostic_error("P800026", "managed_loaded_contract_mismatch", PackageError("managed_loaded_contract_mismatch")) if unit == UNITS[1] else
                    diagnostic_error("P800027", "managed_loaded_contract_mismatch", PackageError("managed_loaded_contract_mismatch")))
            checking, allowed, relation = "RefuseManualStart", "no", "equal"
            if base["RefuseManualStart"] != "no":
                raise dependency_mismatch(
                    diagnostic_error("P800028", "managed_loaded_contract_mismatch", PackageError("managed_loaded_contract_mismatch")) if unit == UNITS[0] else
                    diagnostic_error("P800029", "managed_loaded_contract_mismatch", PackageError("managed_loaded_contract_mismatch")) if unit == UNITS[1] else
                    diagnostic_error("P800030", "managed_loaded_contract_mismatch", PackageError("managed_loaded_contract_mismatch")))
            checking, allowed, relation = "RefuseManualStop", "no", "equal"
            if base["RefuseManualStop"] != "no":
                raise dependency_mismatch(
                    diagnostic_error("P800031", "managed_loaded_contract_mismatch", PackageError("managed_loaded_contract_mismatch")) if unit == UNITS[0] else
                    diagnostic_error("P800032", "managed_loaded_contract_mismatch", PackageError("managed_loaded_contract_mismatch")) if unit == UNITS[1] else
                    diagnostic_error("P800033", "managed_loaded_contract_mismatch", PackageError("managed_loaded_contract_mismatch")))
            if unit == UNITS[2]:
                continue
            scalar = {
                "User": "happyranch", "Group": "happyranch", "Type": "notify", "NotifyAccess": "main",
                "Restart": "on-failure" if unit == UNITS[0] else "no", "RestartUSec": "30s" if unit == UNITS[0] else "1s",
                "RestartMode": "normal", "StartLimitIntervalUSec": "0", "KillMode": "mixed",
                "TimeoutStartUSec": "2min", "TimeoutStopUSec": "35s", "TimeoutAbortUSec": "35s",
                "WatchdogUSec": "30s", "WatchdogSignal": "12", "NoNewPrivileges": "yes",
                "ProtectSystem": "strict", "ProtectHome": "yes", "PrivateTmp": "yes", "PrivateDevices": "yes",
                "ProtectKernelTunables": "yes", "ProtectKernelModules": "yes", "ProtectControlGroups": "yes",
                "RestrictSUIDSGID": "yes", "RestrictRealtime": "yes", "LockPersonality": "yes",
                "MemoryDenyWriteExecute": "yes", "CapabilityBoundingSet": "", "AmbientCapabilities": "", "UMask": "0077",
                "StateDirectory": "happyranch-connector" if unit == UNITS[0] else "happyranch-tsnet-sidecar",
                "StateDirectoryMode": "0700", "ExecStop": "", "ExecStopPost": "",
                "DynamicUser": "no", "SupplementaryGroups": "", "ReadWritePaths": "",
                "Environment": "", "EnvironmentFiles": "", "PassEnvironment": "",
                "LoadCredentialEncrypted": "", "RootDirectory": "", "RootImage": "",
            }
            if unit == UNITS[1]:
                del scalar["RestartUSec"]
            actual_scalar = properties(unit, tuple(scalar))
            if actual_scalar != scalar:
                prop = next(k for k in scalar if actual_scalar[k] != scalar[k])
                raise loaded_error(diagnostic_error("P000263", "managed_loaded_contract_mismatch", PackageError("managed_loaded_contract_mismatch")), prop, scalar[prop], actual_scalar[prop])
            commands = properties(unit, ("ExecStartEx", "ExecStartPreEx", "ExecStartPostEx", "LoadCredential", "RestartPreventExitStatus", "RestrictAddressFamilies", "SystemCallArchitectures"))
            unit_text = rendered[unit]
            for directive in ("ExecStart", "ExecStartPre", "ExecStartPost"):
                declared_raw = [line.split("=", 1)[1] for line in unit_text.splitlines() if line.startswith(directive + "=")]
                declared = [line.lstrip("+") for line in declared_raw]
                actual = commands[directive + "Ex"]
                # Closed systemctl execution tuple; dynamic execution observations
                # never supply configuration authority. Unknown framing refuses.
                records = re.findall(r"\{ path=([^ ;]+) ; argv\[\]=([^;]+?) ; flags=([^;]*) ; start_time=\[([^\]]*)\] ; stop_time=\[([^\]]*)\] ; pid=([0-9]+) ; code=([^;]+?) ; status=([^;]+?) \}", actual)
                if (len(records) != len(declared)
                        or " ".join(match.group(0) for match in re.finditer(r"\{[^{}]*\}", actual)) != actual
                        or [r[1] for r in records] != declared
                        or any(r[0] != r[1].split(" ", 1)[0]
                               or r[2].strip() != ("fully-privileged" if declaration.startswith("+") else "")
                               for r, declaration in zip(records, declared_raw))):
                    raise loaded_error(diagnostic_error("P000264", "managed_loaded_contract_mismatch", PackageError("managed_loaded_contract_mismatch")), directive + "Ex", None, None)
            credentials = ("daemon.token:/etc/happyranch/daemon.token" if unit == UNITS[0]
                           else "enrollment.key:/etc/happyranch/enrollment.key" if loaded_dropin else "")
            prevented = " ".join(str(code) for code in range(2, 256)) if unit == UNITS[0] else ""
            if (commands["LoadCredential"] != credentials or commands["RestartPreventExitStatus"] != prevented
                    or set(commands["RestrictAddressFamilies"].split()) != {"AF_INET", "AF_INET6", "AF_UNIX", "AF_NETLINK"}
                    or commands["SystemCallArchitectures"] != "native"):
                command_expected = {"LoadCredential": credentials, "RestartPreventExitStatus": prevented,
                                    "RestrictAddressFamilies": "AF_INET AF_INET6 AF_UNIX AF_NETLINK", "SystemCallArchitectures": "native"}
                prop = next(k for k, v in command_expected.items() if (set(commands[k].split()) != set(v.split()) if k == "RestrictAddressFamilies" else commands[k] != v))
                raise loaded_error(diagnostic_error("P000265", "managed_loaded_contract_mismatch", PackageError("managed_loaded_contract_mismatch")), prop, command_expected[prop], commands[prop])
        current()
        with diagnostic_scope("P100145", "managed_operational_error"):
            return {"dropin_mode": int(PAYLOAD_MODES["systemd/" + UNITS[1]], 8), "dropin_directory_mode": 0o755}
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as exc:
        raise diagnostic_error("P000266", "managed_lifecycle_incompatible", PackageError("managed_lifecycle_incompatible")) from exc


def require_managed_worker() -> None:
    """Pipe/env opt-in alone is not a foreground execution capability."""
    try:
        descriptor = int(os.environ["HAPPYRANCH_CHILD_HEALTH_FD"])
        if (descriptor != 3 or not stat.S_ISFIFO(os.fstat(descriptor).st_mode)
                or os.environ.get("HAPPYRANCH_CHILD_HEALTH_VERSION") != "2"):
            raise PackageError("managed_worker_refused")
        result = subprocess.run([_MANAGED_EXECUTABLE, "managed-worker-check"],
                                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL, timeout=2, check=False)
        if result.returncode != 0:
            raise PackageError("managed_worker_refused")
    except (KeyError, ValueError, OSError, subprocess.TimeoutExpired) as exc:
        raise PackageError("managed_worker_refused") from exc


def managed_private_entry(operation: str) -> int:
    if operation == "eligibility":
        # These are the existing trust/config/policy readers, without the live
        # five-gate probe or a new credential/authorization algorithm.
        with diagnostic_scope("P100146", "managed_operational_error"):
            from runtime.remote_access.state_store import StateStoreError
        with diagnostic_scope("P100147", "managed_operational_error"):
            from runtime.remote_access.supervisor import (
                ConnectorConfig, ConnectorConfigError, ConnectorSupervisor, PolicyLoadError,
            )
        try:
            with diagnostic_scope("P100148", "managed_operational_error"):
                import errno
            with diagnostic_scope("P100149", "managed_operational_error"):
                deadline = int(os.environ.get("HAPPYRANCH_LIFECYCLE_DEADLINE_NS", "0"))
            if deadline <= time.monotonic_ns():
                raise diagnostic_error("P000267", "managed_lifecycle_deadline", PackageError("managed_lifecycle_deadline"))
            inherited = _MANAGED_DEADLINE.get()
            deadline = min(deadline, inherited if inherited is not None else deadline)
            token = _MANAGED_DEADLINE.set(deadline)
            def current() -> None:
                if time.monotonic_ns() >= deadline:
                    raise diagnostic_error("P000268", "managed_lifecycle_deadline", PackageError("managed_lifecycle_deadline"))
            current()
            _installed_managed_contract(deadline_ns=deadline)
            current()
            config = ConnectorConfig.from_file(Path("/etc/happyranch/connector.json"))
            current()
            if config.managed is None or config.unit_name != UNITS[0] or not config.system:
                raise diagnostic_error("P000269", "managed_configuration_invalid", PackageError("managed_configuration_invalid"))
            supervisor = ConnectorSupervisor(config=config)
            current()
            # Observe the SAME expanded targets as the unchanged store. Only
            # positive ENOENT observations permit its configured default, at
            # every call; absence says nothing about former trust history.
            state_path = Path(config.state_path).expanduser()
            anchor_path = Path(str(state_path) + ".anchor")

            def trust_metadata(path: Path) -> tuple[bool, tuple]:
                # Walk literal parents, without resolve() following an alias.
                # A missing parent proves absence without creating directories.
                if not path.is_absolute():
                    path = Path.cwd() / path
                cursor = Path(path.anchor)
                observations = []
                components = (cursor, *path.parts[1:])
                for index, component in enumerate(components):
                    current()
                    if index:
                        cursor = cursor / component
                    try:
                        with diagnostic_scope("P100150", "managed_operational_error"):
                            metadata = cursor.lstat()
                    except OSError as exc:
                        if exc.errno != errno.ENOENT:
                            raise
                        current()
                        observations.append((str(cursor), None))
                        return False, tuple(observations)
                    # Other OSErrors (including EACCES/ENOTDIR) reach the
                    # existing refusal path; neither is an absence certificate.
                    current()
                    leaf = index == len(components) - 1
                    if leaf:
                        if (not stat.S_ISREG(metadata.st_mode)
                                or metadata.st_nlink != 1
                                or stat.S_IMODE(metadata.st_mode) & 0o7077):
                            raise diagnostic_error("P000270", "managed_identity_invalid", PackageError("managed_identity_invalid"))
                    elif (not stat.S_ISDIR(metadata.st_mode)
                          or stat.S_IMODE(metadata.st_mode) & 0o022):
                        raise diagnostic_error("P000271", "managed_identity_invalid", PackageError("managed_identity_invalid"))
                    identity = (metadata.st_dev, metadata.st_ino,
                                metadata.st_mode, metadata.st_nlink,
                                metadata.st_uid, metadata.st_gid)
                    if leaf:
                        identity += (metadata.st_size, metadata.st_mtime_ns,
                                     metadata.st_ctime_ns)
                    observations.append((str(cursor), identity))
                return True, tuple(observations)

            before = (trust_metadata(state_path), trust_metadata(anchor_path))
            if before[0][0] != before[1][0]:
                raise diagnostic_error("P000272", "managed_identity_missing", PackageError("managed_identity_missing"))
            current()
            state = supervisor.state_store.load()
            current()
            if state.connector_identity != supervisor._configured_identity():
                raise diagnostic_error("P000273", "managed_identity_invalid", PackageError("managed_identity_invalid"))
            supervisor.load_policy()
            current()
            after = (trust_metadata(state_path), trust_metadata(anchor_path))
            if after != before:
                raise diagnostic_error("P000274", "managed_identity_invalid", PackageError("managed_identity_invalid"))
            # Sampled bookends refuse observed movement, not every possible
            # intervening race/rollback. No retry, save or history claim follows.
            current()
            return 0
        except (OSError, RuntimeError, ValueError, AttributeError,
                StateStoreError, ConnectorConfigError, PolicyLoadError):
            return 78
        finally:
            if "token" in locals():
                _MANAGED_DEADLINE.reset(token)
    if operation in {"package-install", "package-uninstall"}:
        # Last entered guard/call boundary only: helper internals remain opaque.
        # Never inspect or serialize an exception, operand, path or stderr body.
        reason = "effective_uid"
        from runtime.remote_access.package_diagnostics import PRIVATE_DIAGNOSTIC
        loss_token = PRIVATE_DIAGNOSTIC.set(True)
        try:
            if os.geteuid() != 0:
                raise diagnostic_error("P000275", "managed_package_context_required", PackageError("managed_package_context_required"))
            reason = "deadline"
            with diagnostic_scope("P100151", "managed_operational_error"):
                deadline = int(os.environ.get("HAPPYRANCH_LIFECYCLE_DEADLINE_NS", "0"))
            now = time.monotonic_ns()
            if not now < deadline <= now + 180_000_000_000:
                raise diagnostic_error("P000276", "managed_lifecycle_deadline", PackageError("managed_lifecycle_deadline"))
            deadline_token = _MANAGED_DEADLINE.set(deadline)
            lock_token = _MANAGED_LOCK.set(3)
            package_token = _MANAGED_PACKAGE.set(True)
            try:
                reason = "context_before"
                _require_managed_package_context()
                with diagnostic_scope("P100152", "managed_operational_error"):
                    import sys
                reason = "entry_input"
                with diagnostic_scope("P100153", "managed_operational_error"):
                    entry = sys.stdin.buffer.read(64)
                if entry == b"package-admission\n":
                    reason = "admission_contract"
                    _installed_managed_contract(deadline_ns=deadline, _package=True)
                    reason = "admission_history"
                    prefix, _units, _dropin, _empty_prior = _package_managed_history(deadline)
                    reason = "admission_dropin"
                    with diagnostic_scope("P100154", "managed_operational_error"):
                        if (operation == "package-install" and _dropin is not None
                                and stat.S_IMODE(_dropin.lstat().st_mode) != 0o644
                                and not Path("/etc/happyranch/enrollment.key").is_file()):
                            # There is no authorized new drop-in publication in this
                            # branch. Preserve private OLD; never silently widen it
                            # or call the resulting NEW normal-reader compatible.
                            raise diagnostic_error("P000277", "managed_lifecycle_incompatible", PackageError("managed_lifecycle_incompatible"))
                    if operation == "package-uninstall":
                        reason = "uninstall_prefix"
                        if prefix is None:
                            raise diagnostic_error("P000278", "managed_lifecycle_incompatible", PackageError("managed_lifecycle_incompatible"))
                        reason = "uninstall_images"
                        with diagnostic_scope("P100155", "managed_operational_error"):
                            if (_sha((prefix / "bin/happyranch-connector").read_bytes()) != _sha(Path("/proc/self/exe").read_bytes())
                                    or _sha((prefix / "bin/happyranch-tsnet-sidecar").read_bytes()) != _sha(Path("/proc/self/fd/6").read_bytes())):
                                raise diagnostic_error("P000279", "managed_lifecycle_incompatible", PackageError("managed_lifecycle_incompatible"))
                    reason = "admission_context_after"
                    _require_managed_package_context()
                    return 0
                if entry:
                    raise diagnostic_error("P000280", "managed_package_context_required", PackageError("managed_package_context_required"))
                reason = "body_guard_before"
                _managed_guard_probe("package", 3, deadline)
                reason = "body_history"
                prefix, _unit_sources, _selected_dropin, empty_prior = _package_managed_history(deadline)
                fresh_token = _MANAGED_FRESH.set(prefix is None or empty_prior)
                reason = "body_contract"
                _installed_managed_contract(deadline_ns=deadline, _package=True)
                if operation == "package-install":
                    reason = "body_install"
                    result = install_linux_package(Path("/proc/self/fd/4"), Path("/"), system_service=True)
                else:
                    reason = "body_uninstall"
                    uninstall_linux_package(Path("/"))
                    result = {"state": "retired"}
                reason = "body_context_after"
                _require_managed_package_context()
                reason = "body_guard_after"
                _managed_guard_probe("package", 3, deadline)
                reason = "body_response"
                with diagnostic_scope("P100156", "managed_operational_error"):
                    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
                return 0
            finally:
                # Keep the original failing boundary through successful resets.
                failed_reason = reason
                reason = "context_release"
                if "fresh_token" in locals():
                    _MANAGED_FRESH.reset(fresh_token)
                _MANAGED_PACKAGE.reset(package_token)
                _MANAGED_LOCK.reset(lock_token)
                _MANAGED_DEADLINE.reset(deadline_token)
                reason = failed_reason
        except (OSError, RuntimeError, ValueError) as exc:
            try:
                with diagnostic_scope("P100157", "managed_operational_error"):
                    from runtime.remote_access.package_diagnostics import exception_refusal
                refusal = exception_refusal(exc)
                from runtime.remote_access.package_diagnostics import exception_guard
                guard = exception_guard(exc)
                if guard is not None:
                    guard_raw = json.dumps(_package_guard_projection(guard), separators=(",", ":")).encode("ascii")
                    if len(guard_raw) <= 450:
                        os.write(2, b"managed_private_guard:" + guard_raw + b"\n")
                with diagnostic_scope("P100158", "managed_operational_error"):
                    suffix = b"" if refusal is None else b":" + refusal[0].encode("ascii") + b":" + refusal[1].encode("ascii")
                    marked = getattr(exc, "_managed_package_boundary", getattr(exc, "_managed_package_refusal", None))
                    if refusal is not None and marked is not None and marked != refusal:
                        suffix += b":" + marked[0].encode("ascii") + b":" + marked[1].encode("ascii")
                with diagnostic_scope("P100159", "managed_operational_error"):
                    loaded = exception_loaded(exc)
                    facts = b"" if loaded is None else b"|" + json.dumps(loaded, ensure_ascii=True, separators=(",", ":")).encode("ascii")
                    os.write(2, b"managed_private_refusal:" + reason.encode("ascii") + suffix + facts + b"\n")
            except OSError:
                pass
            return 78
        finally:
            PRIVATE_DIAGNOSTIC.reset(loss_token)
    return 78
