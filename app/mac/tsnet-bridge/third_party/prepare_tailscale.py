"""Prepare the single approved client replacement; no dependency or trust fallback."""
from __future__ import annotations

import argparse
import base64
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import zipfile

VERSION = "v1.78.0"
SUM = "h1:/UF5EituVa1QMhojqOObFumTarVTcZ14nW1KIVVwbLw="
PATCH = "tailscale-v1.78.0-peerapi.patch"
PATCH_SHA256 = "5bbd46d04b423af475df37e07eda36e1dcfc845dd5fda460e81de67f5625a211"
PEERAPI = "ipn/ipnlocal/peerapi.go"
PEERAPI_SHA256 = "32119b9f85363942cc551da3deeb8ac96944461887f1d7e16916e61290838273"
REPLACE = "./third_party/tailscale.com"
ARCHIVE = "tailscale-v1.78.0.zip"


def require(ok: bool, message: str) -> None:
    if not ok:
        raise ValueError("CLIENT_TAILSCALE_REFUSED: " + message)


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def upstream(raw: bytes) -> dict[str, bytes]:
    """Recompute Go's dirhash.HashZip, rather than trusting a cache .ziphash."""
    prefix = "tailscale.com@" + VERSION + "/"
    result, sums = {}, []
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        for entry in sorted(archive.infolist(), key=lambda e: e.filename):
            name = entry.filename
            require(name.startswith(prefix) and not entry.is_dir(), "unexpected zip member")
            relative = name.removeprefix(prefix)
            require(relative and all(p not in ("", ".", "..") for p in relative.split("/"))
                    and "\\" not in relative and "\n" not in relative
                    and relative not in result, "unsafe/duplicate zip member")
            data = archive.read(entry)
            result[relative] = data
            sums.append(digest(data) + "  " + name + "\n")
    actual = "h1:" + base64.b64encode(hashlib.sha256("".join(sums).encode()).digest()).decode()
    require(actual == SUM, "pristine module h1 mismatch")
    return result


def checked_inputs(root: Path) -> dict[str, bytes]:
    require(root.is_absolute() and root.resolve() == root, "aliased preparation root")
    require(not (root / PATCH).is_symlink() and digest((root / PATCH).read_bytes()) == PATCH_SHA256,
            "patch SHA-256 mismatch")
    require(not (root / ARCHIVE).is_symlink(), "aliased upstream archive")
    return upstream((root / ARCHIVE).read_bytes())


def verify_tree(root: Path) -> None:
    originals = checked_inputs(root)
    target = root / "tailscale.com"
    require(target.is_dir() and target.resolve() == target, "missing/aliased prepared module")
    files = set()
    for path in target.rglob("*"):
        require(not path.is_symlink(), "symlink in prepared module")
        if path.is_dir():
            continue
        name = path.relative_to(target).as_posix()
        require(name in originals, "extra prepared module file: " + name)
        expected = PEERAPI_SHA256 if name == PEERAPI else digest(originals[name])
        require(digest(path.read_bytes()) == expected, "prepared module changed: " + name)
        files.add(name)
    require(files == set(originals), "incomplete prepared module")


def association(module: dict, source: Path, main: dict) -> dict | None:
    """Exact main-module/path/hash association shared by both notice collectors."""
    replacement = module.get("Replace") or {}
    if (module.get("Path"), module.get("Version"), replacement.get("Path")) != (
            "tailscale.com", VERSION, REPLACE) or replacement.get("Version"):
        return None
    bridge = source / "app/mac/tsnet-bridge"
    target = bridge / "third_party/tailscale.com"
    require(main.get("Main") is True and main.get("Path") == "happyranch/tsnet-bridge"
            and main.get("Dir") == str(bridge), "not the client bridge main module")
    require(source.is_absolute() and source.resolve() == source
            and module.get("Dir") == str(target) and replacement.get("Dir") == str(target),
            "replacement directory mismatch")
    # Local Go replacements have no Sum. If provided, it must be the pristine pin.
    require(module.get("Sum", SUM) == SUM, "upstream sum mismatch")
    verify_tree(bridge / "third_party")
    return dict(Path="tailscale.com", Version=VERSION, Sum=SUM,
                ReplacePath=REPLACE, PatchSHA256=PATCH_SHA256)


def prepare(root: Path, go: str) -> None:
    require(not (root / ARCHIVE).is_symlink(), "aliased upstream archive")
    require(digest((root / PATCH).read_bytes()) == PATCH_SHA256, "patch SHA-256 mismatch")
    # An explicit version outside the bridge avoids consulting its missing replace.
    with tempfile.TemporaryDirectory(prefix="hr-tailscale-") as scratch:
        completed = subprocess.run([go, "mod", "download", "-json", "tailscale.com@" + VERSION],
                                   cwd=scratch, check=True, capture_output=True, text=True,
                                   env=dict(os.environ, GOWORK="off"))
        module = json.loads(completed.stdout)
        require(module.get("Path") == "tailscale.com" and module.get("Version") == VERSION
                and module.get("Sum") == SUM and not module.get("Error"), "download pin mismatch")
        raw = Path(module["Zip"]).read_bytes()
        originals = upstream(raw)
        # Verify inputs and patch before replacing the generated tree on the same
        # filesystem. Any failure stops either shipping build before compilation.
        with tempfile.TemporaryDirectory(prefix=".prepare-", dir=root) as staging:
            stage = Path(staging)
            for name, data in originals.items():
                path = stage / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
            subprocess.run(["git", "apply", "--unidiff-zero", "--check", str(root / PATCH)], cwd=stage, check=True)
            subprocess.run(["git", "apply", "--unidiff-zero", str(root / PATCH)], cwd=stage, check=True)
            require(digest((stage / PEERAPI).read_bytes()) == PEERAPI_SHA256, "applied patch mismatch")
            target = root / "tailscale.com"
            require(not target.is_symlink(), "aliased output module")
            if target.exists():
                shutil.rmtree(target)
            stage.rename(target)
    (root / ARCHIVE).write_bytes(raw)
    verify_tree(root)
    print("CLIENT_TAILSCALE_PREPARED", VERSION, SUM, "patch-sha256=" + PATCH_SHA256)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--go", default="go")
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    directory = Path(__file__).resolve().parent
    if args.verify:
        verify_tree(directory)
    else:
        prepare(directory, args.go)
