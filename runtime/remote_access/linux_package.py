"""Deterministic Linux composite package for the managed embedded transport."""
from __future__ import annotations

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
    descriptor = os.open(
        path,
        os.O_WRONLY | os.O_CREAT | os.O_TRUNC
        | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
        mode,
    )
    try:
        view = memoryview(raw)
        while view:
            written = os.write(descriptor, view)
            view = view[written:]
    finally:
        os.close(descriptor)
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


def _backup_present(path: Path) -> bool:
    return path.exists() or path.is_symlink()


def _path_file_identity(path: Path) -> dict:
    return {"sha256": _sha(path.read_bytes()), "mode": stat.S_IMODE(path.lstat().st_mode)}


def _planned_payload_inventory(files: Mapping[str, bytes], system_service: bool) -> dict:
    """Deterministic identity of the payload tree the stage will publish."""
    root_mode = 0o755 if system_service else 0o700
    entries: dict[str, dict] = {
        "bin": {"type": "dir", "mode": 0o755 if system_service else 0o700},
        "share": {"type": "dir", "mode": 0o700},
    }
    for name, raw in files.items():
        if name.startswith("systemd/"):
            continue
        mode = 0o600 if name == "manifest.json" else (
            int(PAYLOAD_MODES[name], 8) if system_service
            else (0o700 if name.startswith("bin/") else 0o600)
        )
        entries[name] = {"type": "file", "mode": mode, "sha256": _sha(raw)}
    return {"root_mode": root_mode, "entries": entries}


def _inventory_tree(path: Path) -> dict:
    """Recursively fingerprint a tree without following links."""
    root_mode = stat.S_IMODE(path.lstat().st_mode)
    entries: dict[str, dict] = {}
    stack: list[tuple[Path, str]] = [(path, "")]
    while stack:
        base, prefix = stack.pop()
        for entry in sorted(base.iterdir(), key=lambda item: item.name):
            relative = f"{prefix}{entry.name}"
            metadata = entry.lstat()
            if stat.S_ISLNK(metadata.st_mode):
                entries[relative] = {"type": "link", "target": os.readlink(entry)}
            elif stat.S_ISDIR(metadata.st_mode):
                entries[relative] = {"type": "dir", "mode": stat.S_IMODE(metadata.st_mode)}
                stack.append((entry, f"{relative}/"))
            elif stat.S_ISREG(metadata.st_mode):
                entries[relative] = {
                    "type": "file", "mode": stat.S_IMODE(metadata.st_mode),
                    "sha256": _sha(entry.read_bytes()),
                }
            else:
                entries[relative] = {"type": "other"}
    return {"root_mode": root_mode, "entries": entries}


def _tree_matches(path: Path, inventory: object) -> bool:
    if not isinstance(inventory, dict):
        return False
    try:
        metadata = path.lstat()
        if not stat.S_ISDIR(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
            return False
        if stat.S_IMODE(metadata.st_mode) != inventory.get("root_mode"):
            return False
        return _inventory_tree(path) == inventory
    except OSError:
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
        metadata = path.lstat()
    except OSError:
        return False
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        return False
    if stat.S_IMODE(metadata.st_mode) != inventory.get("root_mode"):
        return False
    try:
        present = _inventory_tree(path)["entries"]
    except OSError:
        return False
    if not set(present).issubset(entries):
        return False
    return all(entry == entries[relative] for relative, entry in present.items())


def _file_matches(path: Path, identity: object) -> bool:
    if not isinstance(identity, dict):
        return False
    try:
        metadata = path.lstat()
        if not stat.S_ISREG(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
            return False
        return (
            stat.S_IMODE(metadata.st_mode) == identity.get("mode")
            and _sha(path.read_bytes()) == identity.get("sha256")
        )
    except OSError:
        return False


def _valid_file_identity(identity: object) -> bool:
    return (
        isinstance(identity, dict) and set(identity) == {"sha256", "mode"}
        and isinstance(identity.get("sha256"), str) and len(identity["sha256"]) == 64
        and type(identity.get("mode")) is int
    )


def _valid_inventory(inventory: object) -> bool:
    if not isinstance(inventory, dict) or set(inventory) != {"root_mode", "entries"}:
        return False
    if type(inventory.get("root_mode")) is not int:
        return False
    entries = inventory.get("entries")
    if not isinstance(entries, dict):
        return False
    for key, value in entries.items():
        if (not isinstance(key, str) or not key or key.startswith("/")
                or ".." in PurePosixPath(key).parts or not isinstance(value, dict)):
            return False
        kind = value.get("type")
        if kind == "dir":
            if set(value) != {"type", "mode"} or type(value.get("mode")) is not int:
                return False
        elif kind == "file":
            if (set(value) != {"type", "mode", "sha256"}
                    or type(value.get("mode")) is not int
                    or not isinstance(value.get("sha256"), str)
                    or len(value["sha256"]) != 64):
                return False
        elif kind == "link":
            if set(value) != {"type", "target"} or not isinstance(value.get("target"), str):
                return False
        elif kind == "other":
            if set(value) != {"type"}:
                return False
        else:
            return False
    return True


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
            metadata = current.lstat()
        except FileNotFoundError:
            return
        except OSError as exc:
            raise PackageError("transaction_state_invalid") from exc
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
            raise PackageError("transaction_state_invalid")


def _assert_safe_target(root: Path, path: Path, *, directory: bool | None = None) -> None:
    """Reject unsafe types/symlinks on the exact path or any ancestor below root.

    ``directory`` narrows the expected leaf type: ``True`` requires a real
    directory, ``False`` a regular file, and ``None`` accepts either while
    still refusing links, FIFOs, sockets and devices.  Type is validated here,
    before any filesystem operation, so an unexpected leaf never reaches
    ``shutil`` as an unrelated exception category.
    """
    root, path = Path(root), Path(path)
    try:
        metadata = root.lstat()
    except OSError as exc:
        raise PackageError("transaction_state_invalid") from exc
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        raise PackageError("transaction_state_invalid")
    try:
        relative = path.relative_to(root)
    except ValueError as exc:
        raise PackageError("transaction_state_invalid") from exc
    current = root
    parts = relative.parts
    for index, part in enumerate(parts):
        current = current / part
        try:
            child = current.lstat()
        except FileNotFoundError:
            return
        except OSError as exc:
            raise PackageError("transaction_state_invalid") from exc
        if stat.S_ISLNK(child.st_mode):
            raise PackageError("transaction_state_invalid")
        if index != len(parts) - 1:
            if not stat.S_ISDIR(child.st_mode):
                raise PackageError("transaction_state_invalid")
        elif directory is True:
            if not stat.S_ISDIR(child.st_mode):
                raise PackageError("transaction_state_invalid")
        elif directory is False:
            if not stat.S_ISREG(child.st_mode):
                raise PackageError("transaction_state_invalid")
        elif not (stat.S_ISDIR(child.st_mode) or stat.S_ISREG(child.st_mode)):
            raise PackageError("transaction_state_invalid")


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
    stage = record.get("stage")
    if stage is not None:
        paths.append((Path(str(stage)), True))
    return paths


def _assert_record_paths_safe(root: Path, record: Mapping[str, object]) -> None:
    for path, directory in _record_paths(root, record):
        _assert_safe_target(root, path, directory=directory)


def _replace(source: Path, destination: Path, guard, operation: str) -> None:
    _seam(guard, "before", operation, destination)
    os.replace(source, destination)
    _seam(guard, "after", operation, destination)


def _ensure_dir(path: Path, mode: int, guard, operation: str) -> bool:
    if path.is_symlink():
        raise PackageError("transaction_state_invalid")
    if path.exists():
        if not path.is_dir():
            raise PackageError("transaction_state_invalid")
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
        os.O_WRONLY | os.O_CREAT | os.O_TRUNC
        | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    _seam(guard, "after", "record_temp_create", temporary)
    try:
        _seam(guard, "before", "record_temp_write", temporary)
        view = memoryview(raw)
        while view:
            written = os.write(descriptor, view)
            view = view[written:]
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
            raise PackageError("transaction_state_invalid")
        result[key] = value
    return result


def _allowed_created_parents(record: Mapping[str, object]) -> set[str]:
    """The closed, source-derived universe of parents an install may create."""
    allowed = set(_CREATED_PARENT_PLAN)
    if record.get("new_dropin") is not None:
        allowed.add(f"etc/systemd/system/{_DROPIN_SERVICE_DIR}")
    return allowed


def _backups_complete(record: Mapping[str, object]) -> bool:
    """True when recorded prior-existence matches the recorded backup inventory."""
    backups = record["backups"]
    if not isinstance(backups, dict):
        return False
    if (backups["payload"] is not None) != record["payload_present"]:
        return False
    if (backups["dropin"] is not None) != record["dropin_present"]:
        return False
    return all(
        (backups["units"][unit] is not None) == record["units"][unit] for unit in UNITS
    )


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
            raise PackageError("transaction_state_invalid")
        if published or record["dropin_published"]:
            raise PackageError("transaction_state_invalid")
        return
    if phase == "rolling_back":
        # A rollback may have consumed any subset of the recorded backups;
        # per-path identity classification already refuses unexplained gaps.
        return
    if not _backups_complete(record):
        raise PackageError("transaction_state_invalid")
    if phase in {"prepared", "payload_retained", "payload_published"}:
        if published or record["dropin_published"]:
            raise PackageError("transaction_state_invalid")
    elif phase == "units_publishing":
        if record["dropin_published"]:
            raise PackageError("transaction_state_invalid")
    elif phase == "dropin_publishing":
        if set(published) != set(UNITS):
            raise PackageError("transaction_state_invalid")
        if record["dropin_published"] and record["new_dropin"] is None:
            raise PackageError("transaction_state_invalid")
    elif phase == "committed":
        if set(published) != set(UNITS):
            raise PackageError("transaction_state_invalid")
        if record["dropin_published"] != (record["new_dropin"] is not None):
            raise PackageError("transaction_state_invalid")


def _load_record(root: Path, marker: Path) -> dict:
    """Strictly classify an existing record; any ambiguity is refused unchanged."""
    try:
        record = json.loads(
            marker.read_text(encoding="utf-8"), object_pairs_hook=_reject_duplicate_keys
        )
    except PackageError:
        raise
    except (OSError, ValueError) as exc:
        raise PackageError("transaction_state_invalid") from exc
    if not isinstance(record, dict) or set(record) != _TRANSACTION_KEYS:
        raise PackageError("transaction_state_invalid")
    if type(record["schema_version"]) is not int or record["schema_version"] != TRANSACTION_SCHEMA_VERSION:
        raise PackageError("transaction_state_invalid")
    attempt = record["attempt_id"]
    if not isinstance(attempt, str) or _ATTEMPT_ID_PATTERN.match(attempt) is None:
        raise PackageError("transaction_state_invalid")
    if record["root"] != str(root):
        raise PackageError("transaction_state_invalid")
    if not isinstance(record["phase"], str) or record["phase"] not in _TRANSACTION_PHASES:
        raise PackageError("transaction_state_invalid")
    if any(type(record[key]) is not bool for key in ("payload_present", "dropin_present", "dropin_published")):
        raise PackageError("transaction_state_invalid")
    units = record["units"]
    if not isinstance(units, dict) or set(units) != set(UNITS) or any(type(value) is not bool for value in units.values()):
        raise PackageError("transaction_state_invalid")
    created = record["created_parents"]
    if not isinstance(created, list) or any(not isinstance(item, str) for item in created):
        raise PackageError("transaction_state_invalid")
    if len(set(created)) != len(created):
        raise PackageError("transaction_state_invalid")
    if any(
        not item or item.startswith("/")
        or ".." in PurePosixPath(item).parts
        for item in created
    ):
        raise PackageError("transaction_state_invalid")
    if any(item not in _allowed_created_parents(record) for item in created):
        raise PackageError("transaction_state_invalid")
    published = record["published_units"]
    if not isinstance(published, list) or any(not isinstance(unit, str) for unit in published):
        raise PackageError("transaction_state_invalid")
    if len(set(published)) != len(published):
        raise PackageError("transaction_state_invalid")
    if any(unit not in UNITS for unit in published):
        raise PackageError("transaction_state_invalid")
    stage = record["stage"]
    if stage is not None:
        if not isinstance(stage, str) or not stage:
            raise PackageError("transaction_state_invalid")
        stage_path = Path(stage)
        if stage_path.parent != Path(root):
            raise PackageError("transaction_state_invalid")
        prefix = f"{_STAGE_PREFIX}{attempt}-"
        if not stage_path.name.startswith(prefix):
            raise PackageError("transaction_state_invalid")
        if _STAGE_SUFFIX_PATTERN.match(stage_path.name[len(prefix):]) is None:
            raise PackageError("transaction_state_invalid")
    if not _valid_inventory(record["new_payload"]):
        raise PackageError("transaction_state_invalid")
    new_units = record["new_units"]
    if not isinstance(new_units, dict) or set(new_units) != set(UNITS):
        raise PackageError("transaction_state_invalid")
    if any(not _valid_file_identity(identity) for identity in new_units.values()):
        raise PackageError("transaction_state_invalid")
    if record["new_dropin"] is not None and not _valid_file_identity(record["new_dropin"]):
        raise PackageError("transaction_state_invalid")
    backups = record["backups"]
    if not isinstance(backups, dict) or set(backups) != {"payload", "units", "dropin"}:
        raise PackageError("transaction_state_invalid")
    if backups["payload"] is not None and not _valid_inventory(backups["payload"]):
        raise PackageError("transaction_state_invalid")
    backup_units = backups["units"]
    if not isinstance(backup_units, dict) or set(backup_units) != set(UNITS):
        raise PackageError("transaction_state_invalid")
    for entry in [*backup_units.values(), backups["dropin"]]:
        if entry is None:
            continue
        if not _valid_file_identity(entry):
            raise PackageError("transaction_state_invalid")
    _validate_progress(record)
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
            raise PackageError("transaction_state_invalid")
        if _backup_present(payload_backup):
            if not _tree_matches(payload_backup, old_payload):
                raise PackageError("transaction_state_invalid")
            return "already" if _tree_matches(opt, old_payload) else "restore"
        if _tree_matches(opt, old_payload):
            return "already"
        raise PackageError("transaction_state_invalid")
    if old_payload is not None or _backup_present(payload_backup):
        raise PackageError("transaction_state_invalid")
    if opt.is_symlink():
        raise PackageError("transaction_state_invalid")
    if not opt.exists():
        return "nothing"
    if _tree_matches(opt, record["new_payload"]):
        return "remove"
    # A fresh rollback publishes ``rolling_back`` before it unlinks any member,
    # so a partial NEW tree that is a strict subset of the recorded NEW
    # inventory is genuine resumable progress rather than foreign content.
    if record["phase"] == "rolling_back" and _tree_is_owned_partial(opt, record["new_payload"]):
        return "remove"
    raise PackageError("transaction_state_invalid")


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
                raise PackageError("transaction_state_invalid")
            if _backup_present(saved):
                if not _backup_intact(saved, expected):
                    raise PackageError("transaction_state_invalid")
                actions[unit] = "already" if _file_matches(target, expected) else "restore"
            elif _file_matches(target, expected):
                actions[unit] = "already"
            else:
                raise PackageError("transaction_state_invalid")
        else:
            if expected is not None:
                raise PackageError("transaction_state_invalid")
            if target.is_symlink():
                raise PackageError("transaction_state_invalid")
            if not target.exists():
                actions[unit] = "nothing"
            elif unit in record["published_units"]:
                actions[unit] = "remove"
            else:
                raise PackageError("transaction_state_invalid")
    return actions


def _classify_dropin(root: Path, record: dict) -> str:
    units = root / "etc/systemd/system"
    dropin = units / _DROPIN_SERVICE_DIR / _DROPIN_FILE_NAME
    _payload_backup, unit_backup, _marker = _transaction_paths(root)
    dropin_backup = unit_backup / _DROPIN_BACKUP_RELATIVE
    expected = record["backups"]["dropin"]
    if record["dropin_present"]:
        if expected is None:
            raise PackageError("transaction_state_invalid")
        if _backup_present(dropin_backup):
            if not _backup_intact(dropin_backup, expected):
                raise PackageError("transaction_state_invalid")
            return "already" if _file_matches(dropin, expected) else "restore"
        if _file_matches(dropin, expected):
            return "already"
        raise PackageError("transaction_state_invalid")
    if expected is not None or _backup_present(dropin_backup):
        raise PackageError("transaction_state_invalid")
    if dropin.is_symlink():
        raise PackageError("transaction_state_invalid")
    if not dropin.exists():
        return "nothing"
    if record["dropin_published"]:
        return "remove"
    raise PackageError("transaction_state_invalid")


def _restore_old(root: Path, record: dict, guard) -> None:
    """Restore the last-known-good (OLD) composition or conservatively refuse.

    Every precondition (recorded backups intact, verified identity of any
    already-consumed backup, coherent payload/units/drop-in ownership) is
    classified BEFORE any mutation so that a refusal preserves all bytes and
    modes unchanged.  Restoring by atomic rename consumes a backup; the
    recorded OLD identity lets a later retry recognize the already-restored
    artifact instead of demanding the consumed backup again.
    """
    opt = root / "opt/happyranch"
    units = root / "etc/systemd/system"
    dropin = units / _DROPIN_SERVICE_DIR / _DROPIN_FILE_NAME
    payload_backup, unit_backup, _marker = _transaction_paths(root)
    dropin_backup = unit_backup / _DROPIN_BACKUP_RELATIVE

    payload_action = _classify_payload(root, record)
    unit_actions = _classify_units(root, record)
    dropin_action = _classify_dropin(root, record)

    if record["phase"] != "rolling_back":
        record = _publish(root, record, guard, phase="rolling_back")

    if payload_action == "restore":
        if opt.exists() or opt.is_symlink():
            _remove_tree(opt, guard, "rollback_payload_remove")
        opt.parent.mkdir(parents=True, exist_ok=True)
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
            dropin.parent.mkdir(parents=True, exist_ok=True)
        _replace(dropin_backup, dropin, guard, "rollback_dropin_restore")
    elif dropin_action == "remove":
        _unlink(dropin, guard, "rollback_dropin_unlink")

    _cleanup_owned(root, record, guard)


def _cleanup_pre_record(root: Path, stage: Path | None, guard) -> None:
    temporary = _record_temp(root)
    _assert_safe_target(root, temporary, directory=False)
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
    _assert_safe_root_ancestry(root)
    if not root.exists() and not root.is_symlink():
        return
    if root.is_symlink() or not root.is_dir():
        raise PackageError("transaction_state_invalid")
    marker = root / TRANSACTION_MARKER
    _assert_safe_target(root, marker, directory=False)
    if not marker.exists():
        if marker.is_symlink():
            raise PackageError("transaction_state_invalid")
        if any(entry.name.startswith(_RESIDUE_PREFIXES) for entry in root.iterdir()):
            raise PackageError("transaction_state_invalid")
        return
    record = _load_record(root, marker)
    _assert_record_paths_safe(root, record)
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
        unit: {"sha256": _sha(files[f"systemd/{unit}"]), "mode": 0o600} for unit in UNITS
    }
    new_dropin = {"sha256": _sha(_DROPIN_BYTES), "mode": 0o600} if publishes_dropin else None
    checkpoint = fault or (lambda _name: None)
    attempt_id = secrets.token_hex(16)
    stage: Path | None = None
    try:
        _seam(guard, "before", "stage_create", root)
        stage = Path(tempfile.mkdtemp(prefix=f"{_STAGE_PREFIX}{attempt_id}-", dir=root))
        _seam(guard, "after", "stage_create", stage)
        _seam(guard, "before", "stage_chmod", stage)
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
            _ensure_dir(target.parent, 0o755 if system_service and name.startswith("bin/") else 0o700, guard, "stage_dir")
            mode = int(PAYLOAD_MODES[name], 8) if system_service else (0o700 if name.startswith("bin/") else 0o600)
            _write_file(target, raw, mode, guard, f"stage_payload:{name}")
        _write_file(stage / "manifest.json", files["manifest.json"], 0o600, guard, "stage_manifest")
        if not _tree_matches(stage, new_payload):
            raise PackageError("transaction_state_invalid")

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
                raise PackageError("transaction_state_invalid")
            # Publish the intent before the mutation so a torn write is still
            # classified as an owned NEW artifact by recovery.
            record = _publish(
                root, record, guard, published_units=[*record["published_units"], unit]
            )
            _write_file(target, files[f"systemd/{unit}"], 0o600, guard, f"unit_publish:{unit}")
            checkpoint(f"unit_published:{unit}")

        record = _publish(root, record, guard, phase="dropin_publishing")
        if publishes_dropin:
            record = _publish(root, record, guard, dropin_published=True)
            _write_file(dropin, _DROPIN_BYTES, 0o600, guard, "dropin_publish")

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
            if durable["phase"] in {"preparing", "committed"}:
                _cleanup_owned(root, durable, guard)
            else:
                _restore_old(root, durable, guard)
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
