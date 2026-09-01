"""Deterministic Linux composite package for the managed embedded transport."""
from __future__ import annotations

import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import tarfile
import tempfile
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


def render_composite_units(prefix: str = "/opt/happyranch") -> dict[str, str]:
    connector = render_connector_unit(ConnectorUnitSpec(
        exec_start=(f"{prefix}/venv/bin/python", "-m", "runtime.remote_access.cli", "run", "--managed", "--config", "/etc/happyranch/connector.json"),
        user="happyranch", group="happyranch",
        daemon_token_path="/etc/happyranch/daemon.token",
    )).replace("After=network-online.target", "After=network-online.target\nBefore=happyranch-tsnet-sidecar.service\nPartOf=happyranch-managed.target").replace("WantedBy=multi-user.target", "WantedBy=happyranch-managed.target")
    sidecar = """[Unit]
Description=HappyRanch embedded tsnet sidecar
Requires=happyranch-connector.service
BindsTo=happyranch-connector.service
After=happyranch-connector.service
After=network-online.target
Wants=network-online.target
PartOf=happyranch-managed.target

[Service]
Type=notify
ExecStartPre={prefix}/venv/bin/python -m runtime.remote_access.cli diagnose --config /etc/happyranch/connector.json
ExecStart={prefix}/bin/happyranch-tsnet-sidecar --config /etc/happyranch/sidecar.json
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
RestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX
SystemCallArchitectures=native
CapabilityBoundingSet=
AmbientCapabilities=
UMask=0077
StateDirectory=happyranch-tsnet-sidecar
RuntimeDirectory=happyranch-tsnet-sidecar
LogsDirectory=happyranch-tsnet-sidecar
LoadCredential=enrollment.key:/etc/happyranch/enrollment.key

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


def build_linux_package(output: Path, sidecar: Path, wheel: Path, inventory_path: Path,
                        notices_path: Path, *, version: str) -> Path:
    try:
        inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
        notices = notices_path.read_bytes()
        modules = inventory["modules"]
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise PackageError("package_input_invalid") from exc
    _validate_evidence(inventory, notices)
    units = render_composite_units()
    connector = b"#!/bin/sh\nexec /opt/happyranch/venv/bin/python -m runtime.remote_access.cli run --managed --config /etc/happyranch/connector.json \"$@\"\n"
    files: dict[str, tuple[bytes, int]] = {
        "bin/happyranch-tsnet-sidecar": (sidecar.read_bytes(), 0o700),
        "bin/happyranch-connector": (connector, 0o700),
        f"lib/{wheel.name}": (wheel.read_bytes(), 0o600),
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
        modules = inventory["modules"]
    except (UnicodeDecodeError, KeyError, TypeError) as exc:
        raise PackageError("notice_invalid") from exc
    blocks = text.split("\n---\n")
    seen: dict[str, tuple[str, str]] = {}
    for block in blocks:
        spdx = next((line.removeprefix("SPDX: ") for line in block.splitlines() if line.startswith("SPDX: ")), None)
        digest = next((line.removeprefix("License-SHA256: ") for line in block.splitlines() if line.startswith("License-SHA256: ")), None)
        for line in block.splitlines():
            if line.startswith("- ") and "@" in line:
                coordinate = line[2:].strip()
                if coordinate in seen or not spdx or not digest:
                    raise PackageError("notice_invalid")
                seen[coordinate] = (spdx, digest)
    expected = {f"{item['module']}@{item['version']}": (item["spdx"], item["license_sha256"]) for item in modules}
    if seen != expected:
        raise PackageError("notice_inventory_mismatch")


def _read_verified(package: Path) -> tuple[dict[str, bytes], dict[str, object]]:
    files: dict[str, bytes] = {}
    with tarfile.open(package) as archive:
        for member in archive.getmembers():
            path = PurePosixPath(member.name)
            if not member.isfile() or path.is_absolute() or ".." in path.parts or path.parts[0] != PREFIX:
                raise PackageError("archive_member_invalid")
            relative = str(path.relative_to(PREFIX))
            if relative in files:
                raise PackageError("archive_duplicate_member")
            files[relative] = archive.extractfile(member).read()
    try:
        manifest = json.loads(files["manifest.json"])
        if manifest["schema_version"] != 1 or manifest["architecture"] != "linux-amd64" or not isinstance(manifest["version"], str):
            raise PackageError("manifest_invalid")
        entries = manifest["files"]
        expected_paths = set(files) - {"manifest.json"}
        declared_paths = {item["path"] for item in entries}
        if len(entries) != len(declared_paths) or declared_paths != expected_paths:
            raise PackageError("manifest_membership_mismatch")
        allowed = {"manifest.json", "share/dependency-inventory.json", "share/sbom.cdx.json", "share/THIRD_PARTY_NOTICES.md", *[f"systemd/{u}" for u in UNITS]}
        for item in entries:
            path = PurePosixPath(item["path"])
            if path.is_absolute() or ".." in path.parts or str(path) != item["path"] or not (item["path"] in allowed or item["path"].startswith("bin/") or item["path"].startswith("lib/")):
                raise PackageError("manifest_path_invalid")
            expected_mode = "0o700" if item["path"].startswith("bin/") else "0o600"
            if item["mode"] != expected_mode:
                raise PackageError("manifest_mode_invalid")
            if _sha(files[item["path"]]) != item["sha256"]:
                raise PackageError("manifest_hash_mismatch")
        inventory = json.loads(files["share/dependency-inventory.json"])
        if manifest["sidecar_dependency_count"] != len(inventory["modules"]):
            raise PackageError("manifest_count_invalid")
        sbom = json.loads(files["share/sbom.cdx.json"])
        components = {(c["name"], c["version"]) for c in sbom["components"]}
        inventory_coordinates = {(m["module"], m["version"]) for m in inventory["modules"]}
        if components != inventory_coordinates:
            raise PackageError("sbom_inventory_mismatch")
        _validate_evidence(inventory, files["share/THIRD_PARTY_NOTICES.md"])
    except PackageError:
        raise
    except (KeyError, TypeError, ValueError) as exc:
        raise PackageError("manifest_invalid") from exc
    return files, manifest


def install_linux_package(package: Path, root: Path, *, fault: Callable[[str], None] | None = None) -> dict[str, object]:
    files, manifest = _read_verified(package)
    opt = root / "opt/happyranch"
    units = root / "etc/systemd/system"
    root.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".happyranch-stage-", dir=root))
    backup = root / ".happyranch-backup"
    unit_backup = root / ".happyranch-units-backup"
    checkpoint = fault or (lambda _name: None)
    try:
        for name, raw in files.items():
            if name == "manifest.json" or name.startswith("systemd/"):
                continue
            target = staging / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(raw)
            mode = 0o700 if name.startswith("bin/") else 0o600
            target.chmod(mode)
        (staging / "manifest.json").write_bytes(files["manifest.json"])
        (staging / "manifest.json").chmod(0o600)
        units.mkdir(parents=True, exist_ok=True)
        unit_backup.mkdir(mode=0o700, exist_ok=True)
        for unit in UNITS:
            target = units / unit
            if target.exists(): shutil.copy2(target, unit_backup / unit)
        opt.parent.mkdir(parents=True, exist_ok=True)
        if opt.exists():
            if backup.exists(): shutil.rmtree(backup)
            opt.replace(backup)
        checkpoint("payload_old_retained")
        staging.replace(opt)
        checkpoint("payload_published")
        for unit in UNITS:
            target = units / unit
            target.write_bytes(files[f"systemd/{unit}"])
            target.chmod(0o600)
            checkpoint(f"unit_published:{unit}")
        shutil.rmtree(backup, ignore_errors=True)
        shutil.rmtree(unit_backup, ignore_errors=True)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        if opt.exists(): shutil.rmtree(opt)
        if backup.exists(): backup.replace(opt)
        if unit_backup.exists():
            for unit in UNITS:
                target = units / unit
                if target.exists(): target.unlink()
                saved = unit_backup / unit
                if saved.exists(): saved.replace(target)
        shutil.rmtree(unit_backup, ignore_errors=True)
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
