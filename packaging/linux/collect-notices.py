"""Retain original notices beside the intermediate Linux payload's manifests."""
from __future__ import annotations

import importlib.metadata
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
import urllib.parse
import urllib.request


HEADSCALE_PATH = "libexec/headscale/headscale"


class HeadscaleRedirect(urllib.request.HTTPRedirectHandler):
    """GitHub's release CDN is the only admitted off-github.com redirect."""

    def redirect_request(self, req: urllib.request.Request, fp: object, code: int,
                         msg: str, headers: object, newurl: str) -> urllib.request.Request | None:
        url = urllib.parse.urlsplit(newurl)
        if (url.scheme != "https" or url.hostname not in
                {"github.com", "release-assets.githubusercontent.com"}
                or url.username or url.password or url.port not in (None, 443)):
            raise RuntimeError("LINUX_HEADSCALE_DOWNLOAD_FAILED: redirect refused")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def headscale_pin() -> dict:
    return json.loads(Path(__file__).with_name("headscale-pin.json").read_text())


def install_headscale(arch: str, work: Path, payload: Path) -> None:
    """Download privately, verify before copying; never execute Headscale."""
    pin = headscale_pin()
    asset = pin["assets"][arch]
    # Keep the download origin fixed even if a pin is accidentally malformed.
    expected_base = "https://github.com/juanfont/headscale/releases/download/v0.25.1/"
    if (pin["tag"] != "v0.25.1" or pin["base_url"] != expected_base
            or asset["name"] != f"headscale_0.25.1_linux_{arch}"
            or not re.fullmatch(r"[0-9a-f]{64}", asset["sha256"])):
        raise RuntimeError("LINUX_HEADSCALE_PIN_INVALID")
    pending = work / (asset["name"] + ".download")
    digest, size = hashlib.sha256(), 0
    deadline = time.monotonic() + 180
    try:
        opener = urllib.request.build_opener(HeadscaleRedirect())
        with opener.open(expected_base + asset["name"], timeout=20) as response, pending.open("xb") as out:
            while chunk := response.read1(1024 * 1024):
                size += len(chunk)
                if size > 128 * 1024 * 1024 or time.monotonic() > deadline:
                    raise RuntimeError("download limit exceeded")
                digest.update(chunk)
                out.write(chunk)
            if time.monotonic() > deadline:
                raise RuntimeError("download limit exceeded")
    except Exception:
        # urllib errors can contain signed redirect URLs. Keep diagnostics fixed.
        raise RuntimeError("LINUX_HEADSCALE_DOWNLOAD_FAILED") from None
    if digest.hexdigest() != asset["sha256"]:
        raise RuntimeError("LINUX_HEADSCALE_SHA256_MISMATCH")
    target = payload / HEADSCALE_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    pending.chmod(0o755)
    pending.replace(target)


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


def first_party_go(module: dict, source: Path) -> bool:
    """Match the Mac collector's exact repository-local health replacement."""
    replacement = module.get("Replace") or {}
    if (module.get("Path") != "happyranch/tsnethealth"
            or replacement.get("Path") != "../../shared/tsnethealth"
            or replacement.get("Version")):
        return False
    target = source / "app/shared/tsnethealth"
    return (source.is_absolute() and target.resolve() == target
            and target.is_relative_to(source.resolve())
            and all(value and Path(value).is_absolute() and Path(value).resolve() == target
                    for value in (module.get("Dir"), replacement.get("Dir"))))


def copy_go_runtime(goroot: Path, destination: Path) -> None:
    copy_notices(goroot, destination)
    # Go's own third-party vendor originals ship with the selected toolchain.
    for root in (goroot / "src/vendor", goroot / "src/cmd/vendor"):
        for path in root.rglob("*"):
            if path.is_file() and path.name.upper().startswith(("LICENSE", "COPYING", "NOTICE", "PATENTS")):
                target = destination / path.relative_to(goroot)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, target)
    if not (destination / "LICENSE").is_file():
        raise RuntimeError("Go toolchain original license missing")


def collect_headscale(source: Path, payload: Path) -> None:
    """Audit the verified binary's linked set, not the tag's larger test graph."""
    binary = payload / HEADSCALE_PATH
    pin = headscale_pin()
    # Recheck before inspecting, even when the collector is invoked separately.
    digest = hashlib.sha256(binary.read_bytes()).hexdigest()
    matches = [arch for arch, asset in pin["assets"].items() if asset["sha256"] == digest]
    if len(matches) != 1:
        raise RuntimeError("LINUX_HEADSCALE_SHA256_MISMATCH")
    arch = matches[0]
    asset = pin["assets"][arch]
    raw = subprocess.check_output(["go", "version", "-m", str(binary)], text=True)
    lines = raw.splitlines()
    version = lines[0].removeprefix(str(binary) + ": ")
    modules = []
    for line in lines[1:]:
        fields = line.split()
        if fields and fields[0] == "=>":
            raise RuntimeError("LINUX_HEADSCALE_BUILD_INFO_INVALID: replacement")
        if fields and fields[0] == "dep":
            if len(fields) != 4 or not fields[3].startswith("h1:"):
                raise RuntimeError("LINUX_HEADSCALE_BUILD_INFO_INVALID: module")
            modules.append(dict(zip(("Path", "Version", "Sum"), fields[1:])))
    if (version != pin["go_version"] or not re.fullmatch(r"go[0-9]+\.[0-9]+\.[0-9]+", version)
            or not modules or len({m["Path"] for m in modules}) != len(modules)):
        raise RuntimeError("LINUX_HEADSCALE_BUILD_INFO_INVALID")
    # Explicit versions outside a Go project use the existing private module
    # cache and checksum DB; user/private/insecure overrides are not inherited.
    env = dict(os.environ, GOENV="off", GOWORK="off", GOTOOLCHAIN="local",
               GOFLAGS="-modcacherw", GOPROXY="https://proxy.golang.org",
               GOSUMDB="sum.golang.org", GONOSUMDB="", GONOPROXY="",
               GOPRIVATE="", GOINSECURE="",
               GOPATH=str(Path(os.environ["GOMODCACHE"]).parent / "headscale-gopath"))

    def download(requested: list[dict]) -> dict[str, dict]:
        # Let Go's existing downloader parallelize the pinned module requests.
        try:
            raw = subprocess.check_output(
                ["go", "mod", "download", "-json",
                 *(m["Path"] + "@" + m["Version"] for m in requested)],
                cwd=source, env=env, text=True)
        except subprocess.CalledProcessError:
            raise RuntimeError("LINUX_HEADSCALE_MODULE_DOWNLOAD_FAILED") from None
        decoder, position, results = json.JSONDecoder(), 0, {}
        expected = {m["Path"]: m for m in requested}
        while position < len(raw):
            if raw[position].isspace():
                position += 1
                continue
            result, position = decoder.raw_decode(raw, position)
            name = result.get("Path")
            module = expected.get(name, {})
            if (result.get("Error") or name in results or not module
                    or result.get("Version") != module["Version"]
                    or not result.get("Sum") or not result.get("Dir")
                    or ("Sum" in module and result["Sum"] != module["Sum"])):
                raise RuntimeError("LINUX_HEADSCALE_MODULE_VERIFY_FAILED: " + str(name))
            results[name] = result
        if results.keys() != expected.keys():
            raise RuntimeError("LINUX_HEADSCALE_MODULE_VERIFY_FAILED: incomplete inventory")
        return results

    notices = payload / "licenses"
    shutil.copy2(source / "packaging/linux/headscale-LICENSE", notices / "headscale-LICENSE")
    modules.sort(key=lambda m: m["Path"])
    originals = download(modules)
    for module in modules:
        original = originals[module["Path"]]
        target = notices / "headscale/modules" / (module["Path"] + "@" + module["Version"])
        copy_notices(Path(original["Dir"]), target)
        if not target.is_dir():
            raise RuntimeError("LINUX_HEADSCALE_NOTICE_MISSING: " + module["Path"])
    toolchain = download([dict(Path="golang.org/toolchain",
                               Version="v0.0.1-" + version + ".linux-" + arch)])["golang.org/toolchain"]
    copy_go_runtime(Path(toolchain["Dir"]), notices / "headscale/go-runtime")
    (notices / "headscale-modules.json").write_text(json.dumps(modules, indent=2) + "\n")
    (notices / "headscale-build-info.json").write_text(json.dumps(dict(
        Tag=pin["tag"], Asset=asset["name"], SHA256=asset["sha256"], GoVersion=version,
        Toolchain={k: toolchain[k] for k in ("Path", "Version", "Sum")}), indent=2) + "\n")


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
        ["go", "list", "-buildvcs=false", "-mod=readonly", "-deps", "-json", "./cmd/happyranch-tsnet-sidecar"],
        cwd=sidecar, text=True)
    decoder, position, modules = json.JSONDecoder(), 0, {}
    while position < len(raw):
        if raw[position].isspace():
            position += 1
            continue
        package, position = decoder.raw_decode(raw, position)
        module = package.get("Module", {})
        # Both notices and go-modules.json describe third-party inputs. The
        # exact local health module is covered by HappyRanch-LICENSE instead.
        if module and not module.get("Main") and not first_party_go(module, source):
            modules[module["Path"]] = module
    for name, module in sorted(modules.items()):
        target = notices / "go" / (name + "@" + module["Version"])
        copy_notices(Path(module["Dir"]), target)
        if not target.is_dir():
            raise RuntimeError(f"Go module original notice missing: {name}")
    goroot = Path(subprocess.check_output(["go", "env", "GOROOT"], text=True).strip())
    copy_go_runtime(goroot, notices / "go-runtime")
    (notices / "go-modules.json").write_text(json.dumps([
        {k: m[k] for k in ("Path", "Version", "Sum") if k in m}
        for _, m in sorted(modules.items())], indent=2) + "\n")
    collect_headscale(source, payload)


if __name__ == "__main__":
    if len(sys.argv) == 5 and sys.argv[1] == "--install-headscale":
        install_headscale(sys.argv[2], Path(sys.argv[3]), Path(sys.argv[4]))
    else:
        main()
