"""Test-side stock macOS tool adapters; never used by the signing package."""
from __future__ import annotations

import json
import os
from pathlib import Path
import plistlib
import shutil
import stat
import subprocess
import sys
import zipfile


def archive(app: Path, target: Path) -> None:
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as z:
        for path in [app, *sorted(app.rglob("*"))]:
            rel = path.relative_to(app.parent).as_posix()
            mode = path.lstat().st_mode
            info = zipfile.ZipInfo(rel + ("/" if stat.S_ISDIR(mode) else ""))
            info.create_system = 3
            info.external_attr = mode << 16
            z.writestr(info, os.readlink(path).encode() if path.is_symlink()
                       else b"" if path.is_dir() else path.read_bytes())


def extract(source: Path, target: Path) -> None:
    with zipfile.ZipFile(source) as z:
        for info in z.infolist():
            path = target / info.filename
            path.parent.mkdir(parents=True, exist_ok=True)
            mode = info.external_attr >> 16
            if stat.S_ISLNK(mode):
                path.symlink_to(z.read(info).decode())
            elif info.is_dir():
                path.mkdir(exist_ok=True)
                path.chmod(stat.S_IMODE(mode))
            else:
                path.write_bytes(z.read(info))
                path.chmod(stat.S_IMODE(mode))


def main() -> int:
    tool, args = Path(sys.argv[0]).name, sys.argv[1:]
    if tool == "stat":
        if args[-1] == "/dev/console":
            print(os.getuid())
            return 0
        s = Path(args[-1]).stat()
        fields = {"%Lp": oct(stat.S_IMODE(s.st_mode))[2:], "%u:%l": f"{s.st_uid}:{s.st_nlink}",
                  "%u:%Lp": f"{s.st_uid}:{stat.S_IMODE(s.st_mode):o}", "%z": str(s.st_size), "%u": str(s.st_uid)}
        print(fields[args[1]])
    elif tool == "uname":
        print("Darwin" if args == ["-s"] else "arm64")
    elif tool == "id":
        print(os.getuid())
    elif tool == "plutil":
        raw = sys.stdin.buffer.read() if args[-1] == "-" else Path(args[-1]).read_bytes()
        data = json.loads(raw) if raw.startswith(b"{") else plistlib.loads(raw)
        try:
            value = data[args[1]]
        except KeyError:
            return 1
        print(str(value))
    elif tool == "security":
        print('1) ' + 'A' * 40 + ' "Developer ID Application: Fixture (ABCDEFGHIJ)"')
    elif tool == "ditto":
        if args[:2] == ["-x", "-k"]:
            extract(Path(args[2]), Path(args[3]))
        elif args[:2] == ["-c", "-k"]:
            archive(Path(args[-2]), Path(args[-1]))
        else:
            shutil.copytree(args[0], args[1], symlinks=True)
    elif tool == "codesign":
        with open(os.environ["SIGNING_CALLS"], "a") as log:
            log.write(json.dumps(args) + "\n")
        if "--force" in args:
            target = Path(args[-1])
            if target.is_dir():
                parent = target / ("Versions/B" if target.suffix == ".framework" else "Contents")
                seal = parent / "_CodeSignature/CodeResources"
                seal.parent.mkdir(exist_ok=True)
                seal.write_bytes(b"synthetic seal")
            else:
                with target.open("ab") as f:
                    f.write(b"synthetic signature")
        elif "--entitlements" not in args and "-d" in args:
            target = Path(args[-1])
            rows = [line.split("\t") for line in Path(os.environ["SIGNING_INVENTORY"]).read_text().splitlines()]
            ident = next((r[2] for r in rows if str(target).endswith(r[3])), "ai.happyranch.HappyRanchApp")
            if target.name == "happyranch-tsnet-sidecar":
                ident = "ai.happyranch.HappyRanchApp.nested.11"
            print(f"Identifier={ident}\nTeamIdentifier=ABCDEFGHIJ\nAuthority=Developer ID Application: Fixture (ABCDEFGHIJ)\nTimestamp=synthetic\nCodeDirectory flags=0x10000(runtime)", file=sys.stderr)
    elif tool == "bounded-run.pl":
        if args[0] == "--command":
            base, command = args[4], args[6:]
            # Console observation is an OS fixture too; no real consent is claimed.
            with open(base + ".out", "wb") as out, open(base + ".err", "wb") as err:
                result = subprocess.run(command, stdout=out, stderr=err, check=False)
            Path(base + ".exit").write_text(str(result.returncode) + "\n")
            return result.returncode
        if args[0] == "--save":
            Path(args[1]).write_text(args[2])
    return 0


if __name__ == "__main__":
    sys.exit(main())
