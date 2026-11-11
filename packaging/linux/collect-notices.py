"""Retain original notices beside the intermediate Linux payload's manifests."""
from __future__ import annotations

import importlib.metadata
import json
from pathlib import Path
import shutil
import subprocess
import sys


def copy_notices(source: Path, destination: Path) -> None:
    """Retain original root notices and conventional nested license folders."""
    for path in sorted(source.iterdir()):
        if path.name.lower().startswith(("license", "licence", "copying", "notice", "copyright", "patents", "authors")):
            if path.is_symlink():
                raise RuntimeError(f"notice is a symlink: {path}")
            destination.mkdir(parents=True, exist_ok=True)
            if path.is_file():
                shutil.copy2(path, destination / path.name)
            elif path.is_dir():
                shutil.copytree(path, destination / path.name)


def main() -> None:
    source, payload = map(Path, sys.argv[1:])
    notices = payload / "licenses"
    shutil.copy2(source / "LICENSE", notices / "HappyRanch-LICENSE")
    shutil.copy2(payload / "python/lib/python3.12/LICENSE.txt", notices / "CPython-LICENSE.txt")
    shutil.copy2(source / "packaging/linux/python-build-standalone-LICENSE.txt",
                 notices / "python-build-standalone-LICENSE.txt")
    shutil.copy2(source / "packaging/linux/THIRD_PARTY_NOTICES.md", notices / "README.md")
    shutil.copy2(source / "packaging/notice-data/radix-LICENSE", notices / "Radix-LICENSE")
    shutil.copy2(source / "uv.lock", notices / "uv.lock")
    shutil.copy2(source / "web/package-lock.json", notices / "npm-package-lock.json")
    # Python wheel originals remain in their installed .dist-info locations.
    inventory = []
    for dist in importlib.metadata.distributions():
        files = [str(f) for f in (dist.files or ()) if any(
            part.lower().startswith(("license", "copying", "notice")) for part in f.parts)]
        inventory.append(dict(name=dist.metadata["Name"], version=dist.version, notices=files))
    (notices / "python-distributions.json").write_text(json.dumps(inventory, indent=2) + "\n")
    # Include build-tool originals too; this deliberately retains a superset
    # of npm inputs rather than guessing which license applies to emitted code.
    lock = json.loads((source / "web/package-lock.json").read_text())
    for location in lock["packages"]:
        if location and (source / "web" / location).is_dir():
            copy_notices(source / "web" / location, notices / "npm" / location)
    sidecar = source / "app/linux/tsnet-sidecar"
    shutil.copytree(sidecar / "third_party", notices / "sidecar")
    shutil.copy2(sidecar / "go.sum", notices / "sidecar/go.sum")
    # Collect the actual native target graph as well as the retained amd64
    # inventory, so arm64-specific modules do not inherit amd64 attribution.
    raw = subprocess.check_output(
        ["go", "list", "-mod=readonly", "-deps", "-json", "./cmd/happyranch-tsnet-sidecar"],
        cwd=sidecar, text=True)
    decoder, position, modules = json.JSONDecoder(), 0, {}
    while position < len(raw):
        if raw[position].isspace():
            position += 1
            continue
        package, position = decoder.raw_decode(raw, position)
        module = package.get("Module", {})
        if module and not module.get("Main"):
            modules[module["Path"]] = module
    for name, module in sorted(modules.items()):
        target = notices / "go" / (name + "@" + module["Version"])
        copy_notices(Path(module["Dir"]), target)
        if not target.is_dir():
            raise RuntimeError(f"Go module original notice missing: {name}")
    goroot = Path(subprocess.check_output(["go", "env", "GOROOT"], text=True).strip())
    copy_notices(goroot, notices / "go-runtime")
    # Go's own third-party vendor originals ship with the selected toolchain.
    for root in (goroot / "src/vendor", goroot / "src/cmd/vendor"):
        for path in root.rglob("*"):
            if path.is_file() and path.name.upper().startswith(("LICENSE", "COPYING", "NOTICE", "PATENTS")):
                target = notices / "go-runtime" / path.relative_to(goroot)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, target)
    (notices / "go-modules.json").write_text(json.dumps([
        {k: m[k] for k in ("Path", "Version", "Sum") if k in m}
        for _, m in sorted(modules.items())], indent=2) + "\n")


if __name__ == "__main__":
    main()
