"""Portable bundle checks using the retained manifest-notice fixture venue."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import runpy
import struct
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
FIXTURE = runpy.run_path(str(ROOT / "packaging/verify_manifest_notices.py"))
H = FIXTURE["H"]


class ConnectorBundleTests(unittest.TestCase):
    def test_signing_inventory_covers_retained_sidecar_before_outer_app(self) -> None:
        for script in ("app/mac/scripts/mac-sign.sh", "ci/jenkins/mac-signing/mac-sign.sh"):
            with self.subTest(script=script), tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                baseline = root / "baseline/HappyRanch.app/Contents/Helpers/happyranch-tsnet-sidecar"
                current = root / "work/HappyRanch.app/Contents/Helpers/happyranch-tsnet-sidecar"
                # Execute only the actual enumeration function. No signing,
                # private config, Keychain or native executable is touched.
                function = (ROOT / script).read_text().split("code_inventory() {", 1)[1].split("\n}\n", 1)[0]
                command = 'RUN=$1; INVENTORY=$2\ncode_inventory() {' + function + '\n}\ncode_inventory\n'
                inventory = ROOT / "ci/jenkins/mac-signing/code-inventory.tsv"
                for retained in (False, True):
                    for path in (current, baseline) if retained else (current,):
                        path.parent.mkdir(parents=True, exist_ok=True)
                        path.write_bytes(b"synthetic-code-inventory-member")
                    result = subprocess.run(["bash", "-c", command, "inventory", str(root), str(inventory)],
                        capture_output=True, text=True, timeout=10)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    rows = [line.split("\t") for line in result.stdout.splitlines()]
                    helper_rows = [r for r in rows if r[-1] == "Contents/Helpers/happyranch-tsnet-sidecar"]
                    self.assertEqual(len(helper_rows), int(retained))
                    self.assertEqual(rows[-1][0], "main")
                    if retained:
                        self.assertEqual(rows[-2], ["exec", "2", "ai.happyranch.HappyRanchApp.nested.11",
                                                  "Contents/Helpers/happyranch-tsnet-sidecar"])
                    else:
                        self.assertEqual(result.stdout, inventory.read_text())

    def test_sidecar_notices_and_inventory_ship_and_validate(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            source, output, app, mapping = FIXTURE["fixture"](Path(folder), retained_resources=True)
            manifest = H["collect"](source, app, output, mapping)
            rows = [r for r in manifest["components"] if r["id"] == "runtime:Go-sidecar-Darwin-arm64"]
            self.assertEqual(len(rows), 1, "the separately linked Darwin sidecar must own its notice inventory")
            originals = {Path(n["source"]).name: n for n in rows[0]["notices"]}
            self.assertEqual(set(originals), {"THIRD_PARTY_NOTICES.md", "dependency-inventory.json"})
            H["validate"](app, source, output, mapping)
            for name, notice in originals.items():
                with self.subTest(original=name):
                    raw = (ROOT / "app/linux/tsnet-sidecar/third_party/darwin-arm64" / name).read_bytes()
                    destination = app / notice["destination"]
                    self.assertEqual(destination.read_bytes(), raw)
                    self.assertEqual(notice["sha256"], hashlib.sha256(raw).hexdigest())
                    destination.write_bytes(b"changed inventory or notice")
                    with self.assertRaisesRegex(ValueError, "original/destination digest mismatch"):
                        H["validate"](app)
                    destination.write_bytes(raw)
            H["validate"](app)

    def test_bundle_requires_executable_arm64_sidecar(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            source, output, app, mapping = FIXTURE["fixture"](Path(folder), retained_resources=True)
            H["collect"](source, app, output, mapping)
            helper = app / "Contents/Helpers/happyranch-tsnet-sidecar"
            original = helper.read_bytes()
            for case in ("missing", "nonexec", "x86_64", "valid"):
                with self.subTest(case=case):
                    helper.write_bytes(original)
                    helper.chmod(0o755)
                    if case == "missing":
                        helper.unlink()
                    elif case == "nonexec":
                        helper.chmod(0o644)
                    elif case == "x86_64":
                        helper.write_bytes(struct.pack("<IIIIIIII", 0xfeedfacf, 0x1000007, 0, 2, 0, 0, 0, 0))
                    result = subprocess.run(["bash", str(ROOT / "app/mac/scripts/verify-release.sh"), "basic", "--app", str(app)],
                        capture_output=True, text=True, timeout=15)
                    self.assertEqual(result.returncode, 0 if case == "valid" else 1, result.stderr)
                    if case == "valid":
                        self.assertIn("Contents/Helpers/happyranch-tsnet-sidecar", json.loads(result.stdout)["architectures"])
                    else:
                        self.assertIn("Contents/Helpers/happyranch-tsnet-sidecar", result.stderr)


if __name__ == "__main__":
    unittest.main()
