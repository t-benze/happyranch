"""Portable signing boundaries. No macOS code, keys, Apple or Jenkins access."""
from __future__ import annotations

import base64
import hashlib
import os
from pathlib import Path
import plistlib
import re
import runpy
import shutil
import struct
import subprocess
import tempfile
import unittest

from scripts.mac_native.signing_fakes import archive

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ("app/mac/scripts/mac-sign.sh", "ci/jenkins/mac-signing/mac-sign.sh")
FW = "Contents/Frameworks/Sparkle.framework"
LINKS = {"Versions/Current": "B", **{name: "Versions/Current/" + name for name in
         ("Sparkle", "Resources", "Headers", "PrivateHeaders", "Modules", "Autoupdate", "Updater.app")}}
KEY = base64.b64encode(bytes(range(32))).decode()
INVENTORY = ROOT / "ci/jenkins/mac-signing/code-inventory.tsv"


def extract_functions(source: str) -> str:
    lines = iter(source.splitlines())
    blocks = []
    for line in lines:
        if re.match(r"^[a-z_]+\(\) [\{\(]", line):
            block = [line]
            if not line.endswith(("}", ")")):
                for body in lines:
                    block.append(body)
                    if body in ("}", ")"):
                        break
            blocks.append("\n".join(block))
    return "set -euo pipefail\n" + "\n".join(blocks)


def functions(script: str) -> str:
    # Same actual-function extraction venue as test_connector_bundle.py.
    return extract_functions((ROOT / script).read_text())


def run_shell(code: str, *args: Path | str, env: dict | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(["bash", "-c", code, "signing-test", *map(str, args)],
                          text=True, capture_output=True, timeout=20, env=env)


class MacSigningTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="mac-signing-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.root.chmod(0o700)
        self.app = self.root / "HappyRanch.app"
        self.app.mkdir()
        for line in INVENTORY.read_text().splitlines():
            kind, typ, _, rel = line.split("\t")
            suffix = "/Contents/MacOS/Updater" if kind == "bundle" else "/Versions/B/Sparkle" if kind == "framework" else ""
            path = self.app / (rel + suffix)
            path.parent.mkdir(parents=True, exist_ok=True)
            thin = struct.pack("<8I", 0xfeedfacf, 0x100000c, 0, int(typ), 0, 0, 0, 0)
            fat = struct.pack(">7I", 0xcafebabe, 1, 0x100000c, 0, 4096, len(thin), 12)
            path.write_bytes(fat.ljust(4096, b"\0") + thin if kind in ("sparkle", "bundle", "framework") else thin)
            path.chmod(0o755)
        for name in ("Resources", "Headers", "PrivateHeaders", "Modules"):
            (self.app / FW / "Versions/B" / name).mkdir(exist_ok=True)
        for name, target in LINKS.items():
            (self.app / FW / name).symlink_to(target)
        self.info = dict(CFBundleIdentifier="ai.happyranch.HappyRanchApp", CFBundleExecutable="HappyRanch",
                         CFBundlePackageType="APPL", CFBundleShortVersionString="2.0.0", CFBundleVersion="2.0.1",
                         LSMinimumSystemVersion="14.0", GitCommitSHA="1" * 40, GitTreeSHA="2" * 40,
                         ReleaseArchitecture="arm64", SUPublicEDKey=KEY, SUFeedURL="https://app.happyranch.ai/mac/appcast.xml")
        self.zip = self.root / "original.zip"
        self.pin = self.root / "build.pins"
        self.refresh()

    def refresh(self) -> None:
        (self.app / "Contents/Info.plist").write_bytes(plistlib.dumps(self.info))
        archive(self.app, self.zip)
        layout = subprocess.check_output(["unzip", "-Z1", self.zip])
        self.pins = dict(VERSION="2.0.0", BUILD="2.0.1", INPUT_SIZE=str(self.zip.stat().st_size),
                         INPUT_SHA=hashlib.sha256(self.zip.read_bytes()).hexdigest(),
                         LAYOUT_SHA=hashlib.sha256(layout).hexdigest(), SOURCE="1" * 40, SOURCE_TREE="2" * 40, SUPUBLIC_ED_KEY=KEY)
        self.write_pins()

    def write_pins(self) -> None:
        self.pin.write_text("".join(f"{k}={v}\n" for k, v in self.pins.items()))
        self.pin.chmod(0o600)

    def adapted(self, script: str) -> tuple[Path, dict]:
        folder = self.root / ("jenkins" if script.startswith("ci/") else "owner")
        folder.mkdir(exist_ok=True)
        tools = folder / "tools"
        tools.mkdir(exist_ok=True)
        fake = ROOT / "scripts/mac_native/signing_fakes.py"
        # Only executable locations/PATH are adapted. Production functions,
        # dispatch, argument validation and guards are byte-for-byte unchanged.
        source = (ROOT / script).read_text()
        for tool in ("stat", "plutil", "codesign", "ditto", "uname", "id", "security", "xcrun", "otool", "sw_vers", "xcodebuild", "syspolicy_check", "spctl"):
            path = tools / tool
            path.write_text("#!" + shutil.which("python3") + "\n" + fake.read_text())
            path.chmod(0o700)
            source = source.replace("/usr/bin/" + tool, str(path)).replace("/usr/sbin/" + tool, str(path))
        source = source.replace("export PATH=/usr/bin:/bin:/usr/sbin:/sbin LC_ALL=C", f"export PATH={tools}:/usr/bin:/bin:/usr/sbin:/sbin LC_ALL=C")
        wrapper = tools / "bounded-run.pl"
        wrapper.write_text("#!" + shutil.which("python3") + "\n" + fake.read_text())
        wrapper.chmod(0o700)
        source = source.replace('/usr/bin/perl "$HERE/bounded-run.pl"', '"' + str(wrapper) + '"')
        path = folder / "mac-sign.sh"
        path.write_text(source)
        shutil.copy(INVENTORY, folder / INVENTORY.name)
        home = self.root / "home"
        (home / "Library/Keychains").mkdir(parents=True, exist_ok=True)
        (home / "Library/Keychains/login.keychain-db").write_bytes(b"not a keychain")
        config = self.root / "signing.env"
        config.write_text('HAPPYRANCH_SIGN_IDENTITY_SHA1=' + 'A' * 40 + '\nHAPPYRANCH_SIGN_IDENTITY_NAME="Developer ID Application: Fixture (ABCDEFGHIJ)"\nHAPPYRANCH_SIGN_TEAM_ID=ABCDEFGHIJ\nHAPPYRANCH_NOTARY_PROFILE=synthetic\n')
        config.chmod(0o600)
        env = {**os.environ, "HOME": str(home), "HAPPYRANCH_MAC_SIGNING_ENV": str(config),
               "SIGNING_CALLS": str(self.root / "calls.jsonl"), "SIGNING_INVENTORY": str(INVENTORY),
               "HR_STAGE_DIR": str(self.root), "ACTION": "sign"}
        return path, env

    def test_pin_accept_refuse_table(self) -> None:
        valid = dict(self.pins)
        for script in SCRIPTS:
            for version in ("0.0.0", "1.0.1", "123.456.789"):
                with self.subTest(script=script, valid=version):
                    self.pins = {**valid, "VERSION": version, "BUILD": version}
                    self.write_pins()
                    r = subprocess.run(["bash", ROOT / script, "pins", self.pin], text=True, capture_output=True, timeout=10)
                    self.assertEqual(r.returncode, 0, r.stderr)
                    self.assertEqual(r.stdout, self.pin.read_text())
            cases = [("VERSION", v) for v in ("1", "01.0.0", "1.0.0-beta", "1.2.3 ", "-1.2.3")] + [
                ("BUILD", "2"), ("BUILD", "1.02.3"), ("INPUT_SIZE", "0"), ("INPUT_SIZE", "01"),
                ("INPUT_SHA", "f" * 63), ("LAYOUT_SHA", "F" * 64), ("SOURCE", "1" * 39),
                ("SOURCE_TREE", "-" * 40), ("SUPUBLIC_ED_KEY", "A" * 44), ("SUPUBLIC_ED_KEY", KEY[:-2] + "9=")]
            for field, value in cases:
                with self.subTest(script=script, field=field, value=value):
                    self.pins = {**valid, field: value}
                    self.write_pins()
                    r = subprocess.run(["bash", ROOT / script, "pins", self.pin], text=True, capture_output=True, timeout=10)
                    self.assertNotEqual(r.returncode, 0)
                    self.assertIn("Invalid build pins", r.stderr)
            self.pins = valid
            self.write_pins()
            raw = self.pin.read_bytes()
            for bad in (raw.replace(b"\n", b"\r\n"), raw + b"BUILD=2.0.1\n", raw[:-1], raw.replace(b"SOURCE=", b"UNKNOWN="), raw.replace(b"BUILD=", b"BUILD=\0")):
                with self.subTest(script=script, malformed=repr(bad[:40])):
                    self.pin.write_bytes(bad)
                    r = subprocess.run(["bash", ROOT / script, "pins", self.pin], text=True, capture_output=True, timeout=10)
                    self.assertNotEqual(r.returncode, 0)
            self.write_pins()

    def test_archive_tree_allowlist_table(self) -> None:
        self.assertEqual(LINKS, runpy.run_path(str(ROOT / "app/mac/scripts/sparkle_bundle.py"))["LINKS"])
        for script in SCRIPTS:
            code = functions(script) + '\ncheck_members "$1" "$2"\n'
            for case in ("valid", "extra", "escape", "absolute", "link-to-regular", "regular-to-link", "xpc", "installer", "downloader"):
                with self.subTest(script=script, case=case):
                    tmp = self.root / ("copy-" + str(SCRIPTS.index(script)) + "-" + case)
                    shutil.copytree(self.app, tmp, symlinks=True)
                    link = tmp / FW / "Resources"
                    extra = tmp / "Contents/extra"
                    if case in ("escape", "absolute", "link-to-regular"):
                        link.unlink()
                        if case == "link-to-regular":
                            link.mkdir()
                        else:
                            link.symlink_to("../../../../escape" if case == "escape" else "/tmp")
                    elif case in ("extra", "regular-to-link"):
                        (extra if case == "extra" else tmp / "Contents/Info.plist").unlink(missing_ok=True)
                        (extra if case == "extra" else tmp / "Contents/Info.plist").symlink_to("Info.plist")
                    elif case in ("xpc", "installer", "downloader"):
                        extra = tmp / FW / {"xpc": "Versions/B/XPCServices/Installer.xpc", "installer": "Versions/B/Installer", "downloader": "Versions/B/Downloader"}[case]
                        extra.mkdir(parents=True)
                    # Use the same app root name that ditto keepParent emits.
                    parent = self.root / ("candidate-" + str(SCRIPTS.index(script)) + "-" + case)
                    parent.mkdir()
                    candidate = parent / "HappyRanch.app"
                    tmp.rename(candidate)
                    zip_path = self.root / "candidate.zip"
                    archive(candidate, zip_path)
                    for mode, path in (("tree", candidate), ("zip", zip_path)):
                        r = run_shell(code, mode, path)
                        self.assertEqual(r.returncode, 0 if case == "valid" else 1, r.stderr)
                        if case != "valid":
                            self.assertIn("Bundle members refused", r.stderr)
                    shutil.rmtree(parent)

    def test_plist_public_key_feed_refusal(self) -> None:
        for script in SCRIPTS:
            path, env = self.adapted(script)
            for case in ("valid", "missing", "mismatch", "noncanonical", "feed", "key-lf", "feed-lf"):
                with self.subTest(script=script, case=case):
                    data = dict(self.info)
                    if case == "missing":
                        del data["SUPublicEDKey"]
                    elif case == "mismatch":
                        data["SUPublicEDKey"] = base64.b64encode(bytes(32)).decode()
                    elif case == "noncanonical":
                        data["SUPublicEDKey"] = KEY[:-2] + "9="
                    elif case == "feed":
                        data["SUFeedURL"] = "https://example.invalid/appcast.xml"
                    elif case in ("key-lf", "feed-lf"):
                        data["SUPublicEDKey" if case == "key-lf" else "SUFeedURL"] += "\n"
                    (self.app / "Contents/Info.plist").write_bytes(plistlib.dumps(data))
                    # Extract from the tool-adapted script, preserving real guards.
                    code = extract_functions(path.read_text())
                    code += '\nBUNDLE=ai.happyranch.HappyRanchApp; load_build_pins "$1"; check_plist "$2"\n'
                    r = run_shell(code, self.pin, self.app / "Contents/Info.plist", env=env)
                    self.assertEqual(r.returncode, 0 if case == "valid" else 1, r.stderr)
                    if case != "valid":
                        self.assertRegex(r.stderr, "SUPublicEDKey|Sparkle feed")
                    self.assertFalse(Path(env["SIGNING_CALLS"]).exists(), "refusal must precede codesign")

    def test_real_sign_dispatch_inside_out_and_resumed_pins(self) -> None:
        import json
        for script in SCRIPTS:
            with self.subTest(script=script):
                path, env = self.adapted(script)
                calls = Path(env["SIGNING_CALLS"])
                calls.unlink(missing_ok=True)
                run = self.root / ("run-jenkins" if script.startswith("ci/") else "run-owner")
                r = subprocess.run(["bash", path, "sign", self.pin, self.zip, run], env=env, text=True, capture_output=True, timeout=30)
                self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
                signed = [c for c in map(json.loads, calls.read_text().splitlines()) if "--force" in c]
                targets = [c[-1].removeprefix(str(run / "work/HappyRanch.app") + "/") for c in signed]
                self.assertEqual(targets[:3], [FW + "/Versions/B/Autoupdate", FW + "/Versions/B/Updater.app", FW])
                self.assertEqual(targets[-1], str(run / "work/HappyRanch.app"))
                self.assertEqual(len(signed), 14)
                for call in signed:
                    self.assertIn("--timestamp", call)
                    self.assertNotIn("--deep", call)
                    self.assertNotIn("--entitlements", call)
                for call in [*signed[:3], signed[-1]]:
                    self.assertEqual(call[call.index("--options") + 1], "runtime")
                self.assertEqual((run / "build.pins").read_bytes(), self.pin.read_bytes())
                # A changed valid public key must fail at the retained hash before
                # submit can create its one-attempt directory or touch Apple.
                (run / "build.pins").write_text(self.pin.read_text().replace(KEY, base64.b64encode(bytes(32)).decode()))
                env["ACTION"] = "submit"
                r = subprocess.run(["bash", path, "submit", run], env=env, text=True, capture_output=True, timeout=15)
                self.assertNotEqual(r.returncode, 0)
                self.assertIn("Retained build pins changed", r.stderr)
                self.assertFalse((run / "submission").exists())

    def test_preflight_refuses_key_xpc_and_wrong_arch_before_signing(self) -> None:
        original = dict(self.info)
        for script in SCRIPTS:
            path, env = self.adapted(script)
            env["ACTION"] = "preflight"
            for case in ("missing", "mismatch", "noncanonical", "feed", "key-lf", "feed-lf", "xpc", "wrong-arch"):
                with self.subTest(script=script, case=case):
                    self.info = dict(original)
                    if case == "missing":
                        del self.info["SUPublicEDKey"]
                    elif case == "mismatch":
                        self.info["SUPublicEDKey"] = base64.b64encode(bytes(32)).decode()
                    elif case == "noncanonical":
                        self.info["SUPublicEDKey"] = KEY[:-2] + "9="
                    elif case == "feed":
                        self.info["SUFeedURL"] = "https://example.invalid/appcast.xml"
                    elif case in ("key-lf", "feed-lf"):
                        self.info["SUPublicEDKey" if case == "key-lf" else "SUFeedURL"] += "\n"
                    extra = self.app / FW / "Versions/B/XPCServices/Installer.xpc"
                    binary = self.app / FW / "Versions/B/Autoupdate"
                    saved = binary.read_bytes()
                    if case == "xpc":
                        extra.mkdir(parents=True)
                    if case == "wrong-arch":
                        binary.write_bytes(struct.pack("<8I", 0xfeedfacf, 0x1000007, 0, 2, 0, 0, 0, 0))
                    self.refresh()
                    try:
                        r = subprocess.run(["bash", path, "preflight", self.pin, self.zip], env=env, text=True, capture_output=True, timeout=15)
                        self.assertNotEqual(r.returncode, 0)
                        self.assertRegex(r.stderr, "SUPublicEDKey|SUFeedURL|Bundle members refused|Mach-O architecture/type")
                        self.assertFalse(Path(env["SIGNING_CALLS"]).exists(), "refusal must precede codesign")
                    finally:
                        binary.write_bytes(saved)
                        if case == "xpc":
                            shutil.rmtree(extra.parent)
        self.info = original
        self.refresh()

    def test_jenkins_retained_public_pin_reader(self) -> None:
        home = self.root / "home"
        root = home / "Library/Application Support/HappyRanchSigning"
        inputs = root / "inputs"
        inputs.mkdir(parents=True)
        for parent in (home, home / "Library", root.parent, root, inputs):
            parent.chmod(0o700)
        wrapper = ROOT / "ci/jenkins/mac-signing/bounded-run.pl"
        # Fixture-only ancestry adapter: the Linux workspace has group-writable
        # ancestors and /tmp is shared. Keep real regular/mode/hash/pin readers;
        # this case makes no claim about the native private-ancestry guard.
        code = ('require $ARGV[0]; no warnings "redefine"; *plain = sub { '
                'die "fixture escape" unless index($_[0],$ARGV[2]."/")==0; return $_[0]; }; '
                'my $p=selected_pins($ARGV[1]); print "$p->{BUILD} $p->{INPUT_SHA}\\n";')
        env = {**os.environ, "HOME": str(home)}
        for build in ("2.0.1", "2.0.2"):
            with self.subTest(build=build):
                self.pins["BUILD"] = build
                self.write_pins()
                shutil.copy(self.pin, root / "build.pins")
                shutil.copy(self.pin, inputs / "build.pins")
                digest = inputs / "pins.sha256"
                digest.write_text(hashlib.sha256(self.pin.read_bytes()).hexdigest() + "\n")
                digest.chmod(0o600)
                for action in ("preflight", "sign"):
                    r = subprocess.run(["perl", "-e", code, wrapper, action, self.root], env=env, text=True, capture_output=True, timeout=10)
                    self.assertEqual(r.returncode, 0, r.stderr)
                    self.assertEqual(r.stdout, build + " " + self.pins["INPUT_SHA"] + "\n")
                digest.write_text("0" * 64 + "\n")
                r = subprocess.run(["perl", "-e", code, wrapper, "sign", self.root], env=env, text=True, capture_output=True, timeout=10)
                self.assertNotEqual(r.returncode, 0)
                self.assertIn("retained input pin binding", r.stderr)

    def test_manifest_preserves_links_and_noncode(self) -> None:
        for script in SCRIPTS:
            path, env = self.adapted(script)
            code = extract_functions(path.read_text())
            code += '\nRUN=$1; INVENTORY=$2; manifest "$3"\n'
            r = run_shell(code, self.root, INVENTORY, self.app, env=env)
            self.assertEqual(r.returncode, 0, r.stderr)
            for name, target in LINKS.items():
                self.assertIn(f"L {target} ./{FW}/{name}\n", r.stdout)


if __name__ == "__main__":
    unittest.main()
