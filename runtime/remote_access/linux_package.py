"""Deterministic Linux composite package for the managed embedded transport."""
from __future__ import annotations

import hashlib
import io
import json
import os
import secrets
from pathlib import Path, PurePosixPath
import shutil
import tarfile
import tempfile
import zipfile
import subprocess
import stat
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
    "share/happyranch.whl": "0o600",
    "share/dependency-inventory.json": "0o600",
    "share/sbom.cdx.json": "0o600",
    "share/THIRD_PARTY_NOTICES.md": "0o600",
    **{f"systemd/{name}": "0o600" for name in UNITS},
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
    "happyranch-install-transaction",
)
_TRANSACTION_KEYS = frozenset({
    "schema_version", "attempt_id", "root", "phase", "payload_present",
    "units", "dropin_present", "stage", "created_parents",
    "published_units", "dropin_published", "backups",
})
_TRANSACTION_PHASES = frozenset({
    "preparing", "prepared", "payload_retained", "payload_published",
    "units_publishing", "dropin_publishing", "rolling_back", "committed",
})


def credential_capability(
    source: Path,
    *,
    expected_uid: int | None,
    allowed_modes: tuple[int, ...] | None = (0o600,),
    require_read_only: bool = False,
) -> str:
    """Classify credential usability without exposing paths, bytes, or OS errors."""
    path = Path(source)
    try:
        current = path
        while current != current.parent:
            metadata = current.lstat()
            if stat.S_ISLNK(metadata.st_mode):
                return "credential_unsafe_symlink"
            current = current.parent
    except FileNotFoundError:
        return "credential_absent"
    except OSError:
        return "credential_staging_incompatible"
    try:
        metadata = path.lstat()
        if not stat.S_ISREG(metadata.st_mode):
            return "credential_wrong_type"
        if expected_uid is not None and metadata.st_uid != expected_uid:
            return "credential_wrong_custody"
        if allowed_modes is not None and stat.S_IMODE(metadata.st_mode) not in allowed_modes:
            return "credential_wrong_custody"
        descriptor = os.open(
            path,
            os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
        )
        try:
            if not os.read(descriptor, 1):
                return "credential_staging_incompatible"
        finally:
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
                os.close(descriptor)
                return "credential_staging_incompatible"
    except OSError:
        return "credential_staging_incompatible"
    return "credential_valid"


def require_credential_capability(
    source: Path,
    *,
    expected_uid: int,
    allowed_modes: tuple[int, ...] = (0o600,),
) -> None:
    category = credential_capability(
        source, expected_uid=expected_uid, allowed_modes=allowed_modes
    )
    if category != "credential_valid":
        raise PackageError(category)


class CompositeServiceManager:
    """Injectable executable seam for the shipping composite systemd target."""

    def __init__(self, run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
                 systemctl: str = "systemctl") -> None:
        self._run, self._systemctl = run, systemctl

    def _call(self, *args: str) -> subprocess.CompletedProcess[str]:
        try:
            return self._run([self._systemctl, *args], check=True, text=True,
                             capture_output=True)
        except (OSError, subprocess.CalledProcessError) as exc:
            raise PackageError("service_manager_failed") from exc

    def start_ready(self) -> None:
        self._call("start", "happyranch-managed.target")
        for unit in UNITS[:2]:
            result = self._call("show", unit, "--property=ActiveState", "--value")
            if result.stdout.strip() != "active":
                raise PackageError("service_not_ready")

    def stop(self) -> None:
        self._call("stop", "happyranch-managed.target")

    def restart_after_crash(self, unit: str) -> None:
        if unit not in UNITS[:2]:
            raise PackageError("service_unit_invalid")
        self._call("restart", unit)
        result = self._call("show", unit, "--property=ActiveState", "--value")
        if result.stdout.strip() != "active":
            raise PackageError("service_not_ready")


def render_composite_units(prefix: str = "/opt/happyranch") -> dict[str, str]:
    connector = render_connector_unit(ConnectorUnitSpec(
        exec_start=(f"{prefix}/bin/happyranch-tsnet-sidecar", "supervise-connector",
                    f"{prefix}/bin/happyranch-connector", "run", "--managed", "--config",
                    "/etc/happyranch/connector.json"),
        user="happyranch", group="happyranch",
        daemon_token_path="/etc/happyranch/daemon.token",
    )).replace("After=network-online.target", "After=network-online.target\nPartOf=happyranch-managed.target").replace(
        "[Service]\n", "[Service]\nExecStartPre={prefix}/bin/happyranch-connector credential-capability --name daemon.token --unit happyranch-connector.service\n".format(prefix=prefix), 1
    ).replace("WantedBy=multi-user.target", "WantedBy=happyranch-managed.target")
    sidecar = """[Unit]
Description=HappyRanch embedded tsnet sidecar
BindsTo=happyranch-connector.service
After=network-online.target
Wants=network-online.target
PartOf=happyranch-managed.target

[Service]
Type=notify
NotifyAccess=main
ExecStartPre=+{prefix}/bin/happyranch-connector reconcile-enrollment-retirement --source /etc/happyranch/enrollment.key --marker /var/lib/happyranch-tsnet-sidecar/credential.consumed --dropin /etc/systemd/system/happyranch-tsnet-sidecar.service.d/10-enrollment-credential.conf
ExecStartPre={prefix}/bin/happyranch-connector credential-capability --name enrollment.key --unit happyranch-tsnet-sidecar.service --consumed-marker /var/lib/happyranch-tsnet-sidecar/credential.consumed
ExecStart={prefix}/bin/happyranch-tsnet-sidecar --config /etc/happyranch/sidecar.json
ExecStartPost=+{prefix}/bin/happyranch-connector retire-enrollment-source --source /etc/happyranch/enrollment.key --marker /var/lib/happyranch-tsnet-sidecar/credential.consumed --dropin /etc/systemd/system/happyranch-tsnet-sidecar.service.d/10-enrollment-credential.conf
User=happyranch
Group=happyranch
Restart=on-failure
RestartSec=1
WatchdogSec=30
TimeoutStopSec=10
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
Requires=happyranch-connector.service happyranch-tsnet-sidecar.service
After=happyranch-connector.service happyranch-tsnet-sidecar.service
StopWhenUnneeded=yes
"""
    return {UNITS[0]: connector, UNITS[1]: sidecar, UNITS[2]: target}


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _sbom(inventory: Mapping[str, object], version: str) -> bytes:
    modules = inventory.get("modules")
    if not isinstance(modules, list) or not modules:
        raise PackageError("inventory_invalid")
    components = []
    for item in modules:
        if not isinstance(item, dict):
            raise PackageError("inventory_invalid")
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
            raise PackageError("inventory_invalid") from exc
    payload = {"bomFormat": "CycloneDX", "specVersion": "1.5", "version": 1,
               "metadata": {"component": {"type": "application", "name": "happyranch-linux", "version": version}},
               "components": sorted(components, key=lambda item: (item["name"], item["version"]))}
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
        "share/happyranch.whl": (wheel.read_bytes(), 0o600),
        "share/dependency-inventory.json": (inventory_path.read_bytes(), 0o600),
        "share/sbom.cdx.json": (_sbom(inventory, version), 0o600),
        "share/THIRD_PARTY_NOTICES.md": (notices, 0o600),
    }
    files.update({f"systemd/{name}": (text.encode(), 0o600) for name, text in units.items()})
    manifest = {"schema_version": 1, "version": version, "architecture": "linux-amd64",
                "sidecar_dependency_count": len(modules),
                "files": [{"path": name, "sha256": _sha(raw), "mode": oct(mode)}
                          for name, (raw, mode) in sorted(files.items())]}
    files["manifest.json"] = ((json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode(), 0o600)
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
        text = notices.decode("utf-8")
        if set(inventory) != {"schema_version", "artifact", "generator", "modules"} or type(inventory.get("schema_version")) is not int or inventory["schema_version"] != 1:
            raise PackageError("inventory_invalid")
        artifact = inventory["artifact"]
        if (not isinstance(artifact, dict) or set(artifact) != {"goos", "goarch", "cgo_enabled", "package"}
                or artifact != {"goos": "linux", "goarch": "amd64", "cgo_enabled": False,
                                "package": "happyranch/linux-tsnet-sidecar"}
                or inventory["generator"] != "tools/generate_inventory.py"):
            raise PackageError("inventory_invalid")
        modules = inventory["modules"]
        if not isinstance(modules, list) or not modules:
            raise PackageError("inventory_invalid")
    except (UnicodeDecodeError, KeyError, TypeError) as exc:
        raise PackageError("notice_invalid") from exc
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
        if not digest or not license_text or _sha(license_text.encode()) != digest:
            if any(line.startswith("- ") for line in lines):
                raise PackageError("notice_invalid")
        for line in lines:
            if line.startswith("- ") and "@" in line:
                coordinate = line[2:].strip()
                if coordinate in seen or not spdx or not digest:
                    raise PackageError("notice_invalid")
                seen[coordinate] = (spdx, digest)
    required = ("module", "version", "sum", "source", "spdx", "license_sha256", "relationship")
    if any(not isinstance(item, dict) or set(item) != set(required)
           or any(type(item.get(key)) is not str or not item[key] for key in required)
           or item["source"] != f"https://{item['module']}"
           or item["relationship"] != "statically-linked-linux-build-input"
           or not item["sum"].startswith("h1:")
           or len(item["license_sha256"]) != 64 for item in modules):
        raise PackageError("inventory_invalid")
    expected = {f"{item['module']}@{item['version']}": (item["spdx"], item["license_sha256"]) for item in modules}
    if len(expected) != len(modules):
        raise PackageError("inventory_invalid")
    if seen != expected:
        raise PackageError("notice_inventory_mismatch")


def _read_verified(package: Path) -> tuple[dict[str, bytes], dict[str, object]]:
    files: dict[str, bytes] = {}
    modes: dict[str, int] = {}
    with tarfile.open(package) as archive:
        for member in archive.getmembers():
            path = PurePosixPath(member.name)
            if not member.isfile() or path.is_absolute() or ".." in path.parts or path.parts[0] != PREFIX:
                raise PackageError("archive_member_invalid")
            relative = str(path.relative_to(PREFIX))
            if relative in files:
                raise PackageError("archive_duplicate_member")
            if member.uid != 0 or member.gid != 0 or member.uname != "root" or member.gname != "root":
                raise PackageError("archive_owner_invalid")
            files[relative] = archive.extractfile(member).read()
            modes[relative] = member.mode & 0o7777
    try:
        manifest = json.loads(files["manifest.json"])
        if type(manifest["schema_version"]) is not int or manifest["schema_version"] != 1 or manifest["architecture"] != "linux-amd64" or type(manifest["version"]) is not str or not manifest["version"]:
            raise PackageError("manifest_invalid")
        entries = manifest["files"]
        if not isinstance(entries, list) or type(manifest["sidecar_dependency_count"]) is not int:
            raise PackageError("manifest_invalid")
        expected_paths = set(files) - {"manifest.json"}
        declared_paths = {item["path"] for item in entries}
        if len(entries) != len(declared_paths) or declared_paths != expected_paths:
            raise PackageError("manifest_membership_mismatch")
        if declared_paths != set(PAYLOAD_MODES):
            raise PackageError("manifest_path_invalid")
        expected_modes = {name: int(mode, 8) for name, mode in PAYLOAD_MODES.items()}
        expected_modes["manifest.json"] = 0o600
        if any(modes[name] != mode for name, mode in expected_modes.items()):
            raise PackageError("archive_mode_invalid")
        for item in entries:
            if not isinstance(item, dict) or set(item) != {"path", "sha256", "mode"} or any(type(item[k]) is not str for k in item):
                raise PackageError("manifest_invalid")
            path = PurePosixPath(item["path"])
            if path.is_absolute() or ".." in path.parts or str(path) != item["path"] or item["path"] not in PAYLOAD_MODES:
                raise PackageError("manifest_path_invalid")
            if item["mode"] != PAYLOAD_MODES[item["path"]]:
                raise PackageError("manifest_mode_invalid")
            if _sha(files[item["path"]]) != item["sha256"]:
                raise PackageError("manifest_hash_mismatch")
        inventory = json.loads(files["share/dependency-inventory.json"])
        if manifest["sidecar_dependency_count"] != len(inventory["modules"]):
            raise PackageError("manifest_count_invalid")
        sbom = json.loads(files["share/sbom.cdx.json"])
        if (type(sbom.get("version")) is not int or sbom.get("bomFormat") != "CycloneDX"
                or sbom.get("specVersion") != "1.5" or not isinstance(sbom.get("components"), list)):
            raise PackageError("sbom_invalid")
        def component_tuple(c: object) -> tuple[object, ...]:
            if not isinstance(c, dict): raise PackageError("sbom_invalid")
            props = c.get("properties")
            licenses = c.get("licenses")
            if not isinstance(props, list) or not isinstance(licenses, list) or len(licenses) != 1:
                raise PackageError("sbom_invalid")
            prop_map = {p.get("name"): p.get("value") for p in props if isinstance(p, dict)}
            try: spdx = licenses[0]["license"]["id"]
            except (KeyError, TypeError): raise PackageError("sbom_invalid")
            return (c.get("name"), c.get("version"), c.get("purl"), spdx,
                    prop_map.get("happyranch:go.sum"), prop_map.get("happyranch:license-sha256"))
        components = {component_tuple(c) for c in sbom["components"]}
        inventory_coordinates = {(m["module"], m["version"], f"pkg:golang/{m['module']}@{m['version']}",
                                  m["spdx"], m["sum"], m["license_sha256"]) for m in inventory["modules"]}
        if len(components) != len(sbom["components"]) or components != inventory_coordinates:
            raise PackageError("sbom_inventory_mismatch")
        _validate_evidence(inventory, files["share/THIRD_PARTY_NOTICES.md"])
    except PackageError:
        raise
    except (KeyError, TypeError, ValueError) as exc:
        raise PackageError("manifest_invalid") from exc
    return files, manifest


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
    if guard is not None:
        guard(stage, operation, str(path))


def _unlink(path: Path, guard, operation: str) -> None:
    _seam(guard, "before", operation, path)
    os.unlink(path)
    _seam(guard, "after", operation, path)


def _write_file(path: Path, raw: bytes, mode: int, guard, operation: str) -> None:
    _seam(guard, "before", operation, path)
    with open(path, "wb") as handle:
        handle.write(raw)
    _seam(guard, "after", operation, path)
    _seam(guard, "before", f"{operation}:chmod", path)
    os.chmod(path, mode)
    _seam(guard, "after", f"{operation}:chmod", path)


def _copy_file(source: Path, destination: Path, guard, operation: str) -> None:
    _seam(guard, "before", operation, destination)
    shutil.copy2(source, destination)
    _seam(guard, "after", operation, destination)


def _backup_inventory(path: Path) -> dict:
    return {"sha256": _sha(path.read_bytes()), "mode": stat.S_IMODE(path.lstat().st_mode)}


def _backup_intact(path: Path, expected: object) -> bool:
    if expected is None:
        return not (path.exists() or path.is_symlink())
    if not isinstance(expected, dict) or not path.is_file() or path.is_symlink():
        return False
    try:
        metadata = path.lstat()
        return (
            stat.S_IMODE(metadata.st_mode) == expected["mode"]
            and _sha(path.read_bytes()) == expected["sha256"]
        )
    except OSError:
        return False


def _replace(source: Path, destination: Path, guard, operation: str) -> None:
    _seam(guard, "before", operation, destination)
    os.replace(source, destination)
    _seam(guard, "after", operation, destination)


def _ensure_dir(path: Path, mode: int, guard, operation: str) -> bool:
    if path.exists():
        return False
    _seam(guard, "before", f"{operation}:mkdir", path)
    path.mkdir()
    _seam(guard, "after", f"{operation}:mkdir", path)
    _seam(guard, "before", f"{operation}:chmod", path)
    path.chmod(mode)
    _seam(guard, "after", f"{operation}:chmod", path)
    return True


def _remove_tree(path: Path, guard, operation: str) -> None:
    """Recursively remove an owned tree using only exact, no-follow operations."""
    if path.is_symlink() or path.is_file():
        _unlink(path, guard, f"{operation}:unlink")
        return
    for entry in sorted(path.iterdir()):
        _remove_tree(entry, guard, operation)
    _seam(guard, "before", f"{operation}:rmdir", path)
    path.rmdir()
    _seam(guard, "after", f"{operation}:rmdir", path)


def _write_record(root: Path, record: dict, guard) -> None:
    """Atomically publish the transaction record, preserving the prior record."""
    marker = root / TRANSACTION_MARKER
    temporary = _record_temp(root)
    raw = (json.dumps(record, sort_keys=True) + "\n").encode("utf-8")
    _seam(guard, "before", "record_temp_create", temporary)
    descriptor = os.open(
        temporary,
        os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_CLOEXEC", 0),
        0o600,
    )
    _seam(guard, "after", "record_temp_create", temporary)
    try:
        _seam(guard, "before", "record_temp_write", temporary)
        os.write(descriptor, raw)
        _seam(guard, "after", "record_temp_write", temporary)
        _seam(guard, "before", "record_temp_fsync", temporary)
        os.fsync(descriptor)
        _seam(guard, "after", "record_temp_fsync", temporary)
    finally:
        os.close(descriptor)
    _seam(guard, "before", "record_temp_chmod", temporary)
    os.chmod(temporary, 0o600)
    _seam(guard, "after", "record_temp_chmod", temporary)
    _seam(guard, "before", "record_replace", marker)
    os.replace(temporary, marker)
    _seam(guard, "after", "record_replace", marker)


def _load_record(root: Path, marker: Path) -> dict:
    """Strictly classify an existing record; any ambiguity is refused unchanged."""
    try:
        record = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise PackageError("transaction_state_invalid") from exc
    if not isinstance(record, dict) or set(record) != _TRANSACTION_KEYS:
        raise PackageError("transaction_state_invalid")
    if type(record["schema_version"]) is not int or record["schema_version"] != TRANSACTION_SCHEMA_VERSION:
        raise PackageError("transaction_state_invalid")
    if not isinstance(record["attempt_id"], str) or not record["attempt_id"]:
        raise PackageError("transaction_state_invalid")
    if record["root"] != str(root):
        raise PackageError("transaction_state_invalid")
    if record["phase"] not in _TRANSACTION_PHASES:
        raise PackageError("transaction_state_invalid")
    if any(type(record[key]) is not bool for key in ("payload_present", "dropin_present", "dropin_published")):
        raise PackageError("transaction_state_invalid")
    units = record["units"]
    if not isinstance(units, dict) or set(units) != set(UNITS) or any(type(value) is not bool for value in units.values()):
        raise PackageError("transaction_state_invalid")
    created = record["created_parents"]
    if not isinstance(created, list) or len(set(created)) != len(created):
        raise PackageError("transaction_state_invalid")
    if any(
        not isinstance(item, str) or not item or item.startswith("/")
        or ".." in PurePosixPath(item).parts
        for item in created
    ):
        raise PackageError("transaction_state_invalid")
    published = record["published_units"]
    if not isinstance(published, list) or len(set(published)) != len(published):
        raise PackageError("transaction_state_invalid")
    if any(unit not in UNITS for unit in published):
        raise PackageError("transaction_state_invalid")
    stage = record["stage"]
    if stage is not None:
        if not isinstance(stage, str) or not stage:
            raise PackageError("transaction_state_invalid")
        if Path(stage).parent != root or not Path(stage).name.startswith(_STAGE_PREFIX):
            raise PackageError("transaction_state_invalid")
    backups = record["backups"]
    if not isinstance(backups, dict) or set(backups) != {"units", "dropin"}:
        raise PackageError("transaction_state_invalid")
    backup_units = backups["units"]
    if not isinstance(backup_units, dict) or set(backup_units) != set(UNITS):
        raise PackageError("transaction_state_invalid")
    for entry in [*backup_units.values(), backups["dropin"]]:
        if entry is None:
            continue
        if (not isinstance(entry, dict) or set(entry) != {"sha256", "mode"}
                or not isinstance(entry["sha256"], str) or len(entry["sha256"]) != 64
                or type(entry["mode"]) is not int):
            raise PackageError("transaction_state_invalid")
    return record


def _planned_created_parents(root: Path, *, include_dropin_dir: bool) -> list[str]:
    candidates = [root / "opt", root / "etc", root / "etc/systemd", root / "etc/systemd/system"]
    if include_dropin_dir:
        candidates.append(root / "etc/systemd/system" / _DROPIN_SERVICE_DIR)
    return [str(path.relative_to(root)) for path in candidates if not path.exists()]


def _remove_created_parents(root: Path, record: dict, guard) -> None:
    for relative in sorted(record["created_parents"], key=lambda item: len(PurePosixPath(item).parts), reverse=True):
        path = root / relative
        if path.is_dir() and not path.is_symlink() and not any(path.iterdir()):
            _seam(guard, "before", "created_parent_remove", path)
            path.rmdir()
            _seam(guard, "after", "created_parent_remove", path)


def _cleanup_owned(root: Path, record: dict, guard) -> None:
    """Remove exact recorded owned residue, then the marker last."""
    payload_backup, unit_backup, marker = _transaction_paths(root)
    for path in (payload_backup, unit_backup):
        if path.exists() or path.is_symlink():
            _remove_tree(path, guard, "backup_remove")
    temporary = _record_temp(root)
    if temporary.exists():
        _unlink(temporary, guard, "record_temp_remove")
    stage = record.get("stage")
    if stage is not None and Path(stage).exists():
        _remove_tree(Path(stage), guard, "stage_remove")
    _remove_created_parents(root, record, guard)
    if marker.exists():
        _unlink(marker, guard, "marker_remove")


def _restore_old(root: Path, record: dict, guard) -> None:
    """Restore the last-known-good (OLD) composition or conservatively refuse.

    Every precondition (recorded backups intact, coherent payload/drop-in
    ownership) is classified BEFORE any mutation so that a refusal preserves
    all bytes and modes unchanged.
    """
    opt = root / "opt/happyranch"
    units = root / "etc/systemd/system"
    dropin = units / _DROPIN_SERVICE_DIR / _DROPIN_FILE_NAME
    payload_backup, unit_backup, _marker = _transaction_paths(root)
    dropin_backup = unit_backup / _DROPIN_BACKUP_RELATIVE

    restores_payload = False
    removes_payload = False
    if record["payload_present"]:
        if payload_backup.exists():
            restores_payload = True
        elif not opt.exists():
            raise PackageError("transaction_state_invalid")
    else:
        if payload_backup.exists():
            raise PackageError("transaction_state_invalid")
        removes_payload = opt.exists() or opt.is_symlink()

    restores_units = False
    removes_units: list[str] = []
    for unit in UNITS:
        saved = unit_backup / unit
        if record["units"][unit]:
            if not _backup_intact(saved, record["backups"]["units"][unit]):
                raise PackageError("transaction_state_invalid")
            restores_units = True
        elif unit in record["published_units"]:
            removes_units.append(unit)

    restores_dropin = False
    removes_dropin = False
    if record["dropin_present"]:
        expected = record["backups"]["dropin"]
        if expected is None or not _backup_intact(dropin_backup, expected):
            raise PackageError("transaction_state_invalid")
        restores_dropin = True
    else:
        if record["backups"]["dropin"] is not None or dropin_backup.exists() or dropin_backup.is_symlink():
            raise PackageError("transaction_state_invalid")
        removes_dropin = record["dropin_published"] and (dropin.exists() or dropin.is_symlink())

    record = dict(record)
    if record["phase"] != "rolling_back":
        record["phase"] = "rolling_back"
        _write_record(root, record, guard)

    if restores_payload:
        if opt.exists() or opt.is_symlink():
            _remove_tree(opt, guard, "rollback_payload_remove")
        opt.parent.mkdir(parents=True, exist_ok=True)
        _replace(payload_backup, opt, guard, "rollback_payload_restore")
    elif removes_payload:
        _remove_tree(opt, guard, "rollback_payload_remove")

    if restores_units:
        for unit in UNITS:
            if not record["units"][unit]:
                continue
            target = units / unit
            if target.exists() or target.is_symlink():
                _unlink(target, guard, "rollback_unit_unlink")
            _replace(unit_backup / unit, target, guard, "rollback_unit_restore")
    for unit in removes_units:
        target = units / unit
        if target.exists() or target.is_symlink():
            _unlink(target, guard, "rollback_unit_unlink")

    if restores_dropin:
        if not dropin.parent.exists():
            dropin.parent.mkdir(parents=True, exist_ok=True)
        _replace(dropin_backup, dropin, guard, "rollback_dropin_restore")
    elif removes_dropin:
        _unlink(dropin, guard, "rollback_dropin_unlink")

    _cleanup_owned(root, record, guard)


def _cleanup_pre_record(root: Path, stage: Path | None, guard) -> None:
    temporary = _record_temp(root)
    if temporary.exists():
        _unlink(temporary, guard, "record_temp_remove")
    if stage is not None and Path(stage).exists():
        _remove_tree(Path(stage), guard, "stage_remove")


def _recover_interrupted(root: Path, guard=None) -> None:
    """Classify and recover an owned transaction, or refuse an ambiguous one.

    Conservative disposition: a legacy schema-v1 marker, an incomplete
    record, or unrecorded preparation residue is preserved unchanged and
    refused with the existing ``transaction_state_invalid`` category.  Genuine
    owned transactions are recovered to OLD (uncommitted) or completed NEW
    (committed).
    """
    if not root.exists():
        return
    marker = root / TRANSACTION_MARKER
    if not marker.exists():
        if any(entry.name.startswith(_RESIDUE_PREFIXES) for entry in root.iterdir()):
            raise PackageError("transaction_state_invalid")
        return
    record = _load_record(root, marker)
    if record["phase"] in {"preparing", "committed"}:
        # ``preparing`` never mutated OLD and ``committed`` is authoritative NEW:
        # both are recovered by removing only exact recorded owned residue.
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
        raise PackageError("install_mode_invalid")
    if system_service:
        require_credential_capability(
            root / "etc/happyranch/daemon.token", expected_uid=os.geteuid()
        )
        enrollment_source = root / "etc/happyranch/enrollment.key"
        if enrollment_source.exists() or enrollment_source.is_symlink():
            require_credential_capability(
                enrollment_source, expected_uid=os.geteuid()
            )
    files, manifest = _read_verified(package)
    _recover_interrupted(root, guard)
    root.mkdir(parents=True, exist_ok=True)
    opt = root / "opt/happyranch"
    units = root / "etc/systemd/system"
    dropin_dir = units / _DROPIN_SERVICE_DIR
    dropin = dropin_dir / _DROPIN_FILE_NAME
    credential_source = root / "etc/happyranch/enrollment.key"
    publishes_dropin = system_service and credential_source.is_file()
    payload_present = opt.exists()
    dropin_present = dropin.exists()
    unit_present = {name: (units / name).exists() for name in UNITS}
    created_parents = _planned_created_parents(root, include_dropin_dir=publishes_dropin)
    checkpoint = fault or (lambda _name: None)
    record: dict | None = None
    stage: Path | None = None
    try:
        _seam(guard, "before", "stage_create", root)
        stage = Path(tempfile.mkdtemp(prefix=_STAGE_PREFIX, dir=root))
        _seam(guard, "after", "stage_create", stage)
        _seam(guard, "before", "stage_chmod", stage)
        stage.chmod(0o755 if system_service else 0o700)
        _seam(guard, "after", "stage_chmod", stage)
        record = {
            "schema_version": TRANSACTION_SCHEMA_VERSION,
            "attempt_id": secrets.token_hex(16),
            "root": str(root),
            "phase": "preparing",
            "payload_present": payload_present,
            "units": dict(unit_present),
            "dropin_present": dropin_present,
            "stage": str(stage),
            "created_parents": created_parents,
            "published_units": [],
            "dropin_published": False,
            "backups": {"units": {name: None for name in UNITS}, "dropin": None},
        }
        _write_record(root, record, guard)

        for relative in ("opt", "etc", "etc/systemd", "etc/systemd/system"):
            _ensure_dir(root / relative, 0o755, guard, "parent")
        if publishes_dropin:
            _ensure_dir(dropin_dir, 0o755, guard, "parent")

        for name, raw in files.items():
            if name == "manifest.json" or name.startswith("systemd/"):
                continue
            target = stage / name
            _ensure_dir(target.parent, 0o755 if system_service and name.startswith("bin/") else 0o700, guard, "stage_dir")
            mode = int(PAYLOAD_MODES[name], 8) if system_service else (0o700 if name.startswith("bin/") else 0o600)
            _write_file(target, raw, mode, guard, f"stage_payload:{name}")
        _write_file(stage / "manifest.json", files["manifest.json"], 0o600, guard, "stage_manifest")

        unit_backup = root / _UNIT_BACKUP_NAME
        _ensure_dir(unit_backup, 0o700, guard, "unit_backup")
        inventory_units: dict[str, dict | None] = {}
        for unit in UNITS:
            target = units / unit
            if target.exists():
                destination = unit_backup / unit
                _copy_file(target, destination, guard, f"unit_backup:{unit}")
                inventory_units[unit] = _backup_inventory(destination)
            else:
                inventory_units[unit] = None
        inventory_dropin: dict | None = None
        if dropin_present:
            _ensure_dir(unit_backup / _DROPIN_SERVICE_DIR, 0o700, guard, "dropin_backup_dir")
            destination = unit_backup / _DROPIN_BACKUP_RELATIVE
            _copy_file(dropin, destination, guard, "dropin_backup")
            inventory_dropin = _backup_inventory(destination)
        record["backups"] = {"units": inventory_units, "dropin": inventory_dropin}
        record["phase"] = "prepared"
        _write_record(root, record, guard)

        payload_backup = root / _PAYLOAD_BACKUP_NAME
        if payload_present:
            _replace(opt, payload_backup, guard, "payload_retain")
        record["phase"] = "payload_retained"
        _write_record(root, record, guard)
        checkpoint("payload_old_retained")

        _replace(stage, opt, guard, "payload_publish")
        record["phase"] = "payload_published"
        _write_record(root, record, guard)
        checkpoint("payload_published")

        record["phase"] = "units_publishing"
        _write_record(root, record, guard)
        for unit in UNITS:
            target = units / unit
            if not unit_present[unit] and (target.exists() or target.is_symlink()):
                raise PackageError("transaction_state_invalid")
            # Publish the intent before the mutation so a torn write is still
            # classified as an owned NEW artifact by recovery.
            record["published_units"] = [*record["published_units"], unit]
            _write_record(root, record, guard)
            _write_file(target, files[f"systemd/{unit}"], 0o600, guard, f"unit_publish:{unit}")
            checkpoint(f"unit_published:{unit}")

        record["phase"] = "dropin_publishing"
        _write_record(root, record, guard)
        if publishes_dropin:
            record["dropin_published"] = True
            _write_record(root, record, guard)
            _write_file(
                dropin,
                b"[Service]\nLoadCredential=enrollment.key:/etc/happyranch/enrollment.key\n",
                0o600,
                guard,
                "dropin_publish",
            )

        record["phase"] = "committed"
        _write_record(root, record, guard)
        _cleanup_owned(root, record, guard)
    except Exception:
        if record is not None and (root / TRANSACTION_MARKER).exists():
            if record["phase"] in {"preparing", "committed"}:
                _cleanup_owned(root, record, guard)
            else:
                _restore_old(root, record, guard)
        else:
            _cleanup_pre_record(root, stage, guard)
        raise
    return {"version": manifest["version"], "manifest_sha256": _sha(files["manifest.json"])}


def uninstall_linux_package(root: Path) -> None:
    opt = root / "opt/happyranch"
    if opt.exists():
        shutil.rmtree(opt)
    for unit in UNITS:
        path = root / "etc/systemd/system" / unit
        if path.exists():
            path.unlink()
