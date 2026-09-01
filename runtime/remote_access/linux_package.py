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
from typing import Mapping


class PackageError(RuntimeError):
    """Stable, category-only package failure."""


PREFIX = "happyranch-linux-amd64"
UNITS = (
    "happyranch-connector.service",
    "happyranch-tsnet-sidecar.service",
    "happyranch-managed.target",
)


def render_composite_units(prefix: str = "/opt/happyranch") -> dict[str, str]:
    connector = """[Unit]
Description=HappyRanch managed connector
Before=happyranch-tsnet-sidecar.service
PartOf=happyranch-managed.target

[Service]
Type=notify
ExecStart={prefix}/bin/happyranch-connector
Restart=on-failure
RestartSec=1
TimeoutStopSec=10
NoNewPrivileges=yes
ProtectSystem=strict
ProtectHome=yes
PrivateTmp=yes
UMask=0077
StateDirectory=happyranch-connector
RuntimeDirectory=happyranch-connector

[Install]
WantedBy=happyranch-managed.target
""".format(prefix=prefix)
    sidecar = """[Unit]
Description=HappyRanch embedded tsnet sidecar
Requires=happyranch-connector.service
BindsTo=happyranch-connector.service
After=happyranch-connector.service
PartOf=happyranch-managed.target

[Service]
Type=notify
ExecStartPre={prefix}/bin/happyranch-connector-ready
ExecStart={prefix}/bin/happyranch-tsnet-sidecar
Restart=on-failure
RestartSec=1
TimeoutStopSec=10
NoNewPrivileges=yes
ProtectSystem=strict
ProtectHome=yes
PrivateTmp=yes
UMask=0077
StateDirectory=happyranch-tsnet-sidecar
RuntimeDirectory=happyranch-tsnet-sidecar

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
                "hashes": [{"alg": "SHA-256", "content": item["license_sha256"]}],
                "licenses": [{"license": {"id": item["spdx"]}}],
                "properties": [{"name": "happyranch:go.sum", "value": item["sum"]}],
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
    for item in modules:
        coordinate = f"{item['module']}@{item['version']}".encode()
        if coordinate not in notices:
            raise PackageError("notice_missing_module")
    units = render_composite_units()
    connector = b"#!/bin/sh\nexec python -m runtime.remote_access.cli run --managed \"$@\"\n"
    ready = b"#!/bin/sh\nexec python -m runtime.remote_access.cli diagnose --managed \"$@\"\n"
    files: dict[str, tuple[bytes, int]] = {
        "bin/happyranch-tsnet-sidecar": (sidecar.read_bytes(), 0o700),
        "bin/happyranch-connector": (connector, 0o700),
        "bin/happyranch-connector-ready": (ready, 0o700),
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


def _read_verified(package: Path) -> tuple[dict[str, bytes], dict[str, object]]:
    files: dict[str, bytes] = {}
    with tarfile.open(package) as archive:
        for member in archive.getmembers():
            path = PurePosixPath(member.name)
            if not member.isfile() or path.is_absolute() or ".." in path.parts or path.parts[0] != PREFIX:
                raise PackageError("archive_member_invalid")
            files[str(path.relative_to(PREFIX))] = archive.extractfile(member).read()
    try:
        manifest = json.loads(files["manifest.json"])
        for item in manifest["files"]:
            if _sha(files[item["path"]]) != item["sha256"]:
                raise PackageError("manifest_hash_mismatch")
    except PackageError:
        raise
    except (KeyError, TypeError, ValueError) as exc:
        raise PackageError("manifest_invalid") from exc
    return files, manifest


def install_linux_package(package: Path, root: Path) -> dict[str, object]:
    files, manifest = _read_verified(package)
    opt = root / "opt/happyranch"
    units = root / "etc/systemd/system"
    root.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".happyranch-stage-", dir=root))
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
        for unit in UNITS:
            target = units / unit
            target.write_bytes(files[f"systemd/{unit}"])
            target.chmod(0o600)
        opt.parent.mkdir(parents=True, exist_ok=True)
        if opt.exists():
            shutil.rmtree(opt)
        staging.replace(opt)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
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
