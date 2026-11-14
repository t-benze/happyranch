"""Portable C8 boundary checks; native acceptance is separately authorized."""
from __future__ import annotations

import json
import os
import plistlib
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from runtime.remote_access import launchd_coexistence as guard
from runtime.remote_access import launchd_installer as managed
from scripts.mac_native import suite_connector_managed as suite
from scripts.mac_native import test_connector_installer as fixtures
from scripts.mac_native.test_connector_launchd import DOMAIN, row


class Coexistence(unittest.TestCase):
    def test_inventory_classification(self) -> None:
        uid = os.getuid()
        for host, expected in (("127.0.0.1", "clear"), ("127.1.2.3", "clear"),
                ("[::1]", "clear"), ("[::ffff:127.0.0.1]", "clear"),
                ("100.64.0.1", "conflict"), ("[fd7a:115c:a1e0::1]", "conflict"),
                ("0.0.0.0", "conflict"), ("*", "conflict")):
            # Apple lsof 4.91 emits f by default even with -Fpun. Keep the
            # earlier no-f cases too; access/lock suffixes belong to other fields.
            for fd in ("", "f0\n", "f7\n", "f12345\n", "fcwd\n", "fjld\n",
                    "fltx\n", "fm86\n", "fmem\n", "fmmap\n", "fpd\n", "frtd\n",
                    "ftr\n", "ftxt\n", "fv86\n", "fL12\n", "fMab\n"):
                with self.subTest(host=host, fd=fd):
                    self.assertEqual(guard.classify_inventory(0, f"p123\nu{uid}\n{fd}n{host}:8443\n", uid), expected)
        for fd in ("", "-1", "+7", "7u", "7rW", " 7", "7 ", "７", "unknown", "err", "L", "Mxyz"):
            with self.subTest(malformed_fd=fd):
                self.assertEqual(guard.classify_inventory(0, f"p123\nu{uid}\nf{fd}\nn127.0.0.1:8443\n", uid), "unavailable")
        for text, expected in (
                (f"p123\nu{uid}\nf7\nn127.0.0.1:8443\nf8\nn[::1]:8443\n", "clear"),
                (f"p123\nu{uid}\nf7\nn127.0.0.1:8443\np999\nu{uid}\nf8\nn[fd7a::1]:8443\n", "conflict"),
                (f"p123\nu{uid}\nf7\n", "unavailable"),
                (f"p123\nu{uid}\nf7\nf8\nn127.0.0.1:8443\n", "unavailable"),
                (f"p123\nu{uid}\nf7\nn127.0.0.1:8443\nf8\n", "unavailable"),
                (f"p123\nu{uid}\nf7\np999\nu{uid}\nf8\nn127.0.0.1:8443\n", "unavailable"),
                (f"p123\nf7\nu{uid}\nn127.0.0.1:8443\n", "unavailable"),
                (f"p123\nu{uid}\nf7\nn127.0.0.1:8443\nn[::1]:8443\n", "unavailable"),
                (f"p123\nu{uid+1}\nf7\nn100.64.0.1:8443\n", "unavailable"),
                (f"p123\nu{uid}\nf7\nxunknown\nn127.0.0.1:8443\n", "unavailable")):
            with self.subTest(text=text):
                self.assertEqual(guard.classify_inventory(0, text, uid), expected)
        for code, text, expected in ((1, "", "clear"), (0, "", "unavailable"),
                (2, "", "unavailable"), (1, "failure", "unavailable"),
                (0, f"p123\nu{uid+1}\nn100.64.0.1:8443\n", "unavailable"),
                (0, f"p123\nu{uid}\nninvalid:8443\n", "unavailable"),
                (0, f"p123\nu{uid}\nn100.64.0.1:80\n", "unavailable"),
                (0, "p123\nn100.64.0.1:8443\n", "unavailable"),
                (0, f"p123\nu{uid}\nn127.0.0.1:8443\np999\n", "unavailable"),
                (0, f"p123\nu{uid}\nu{uid}\nn127.0.0.1:8443\n", "unavailable")):
            with self.subTest(code=code, text=text):
                self.assertEqual(guard.classify_inventory(code, text, uid), expected)

    def test_observation_bounds_and_secret_free_diagnostics(self) -> None:
        spawn = subprocess.Popen
        for payload, stderr, code, expected in (
                ("", "", 1, "clear"),
                (f"p123\nu{os.getuid()}\nn100.64.0.1:8443\n", "", 0, "conflict"),
                (f"p123\nu{os.getuid()}\nf7\nn127.0.0.1:8443\n", "", 0, "clear"),
                (f"p123\nu{os.getuid()}\nf7\nn[::1]:8443\n", "", 0, "clear"),
                (f"p123\nu{os.getuid()}\nf7\nn100.64.0.1:8443\n", "", 0, "conflict"),
                (f"p123\nu{os.getuid()}\nf7\nn[fd7a::1]:8443\n", "", 0, "conflict"),
                (f"p123\nu{os.getuid()}\nf7u\nn127.0.0.1:8443\n", "", 0, "unavailable"),
                ("", "synthetic-secret-diagnostic", 1, "unavailable"),
                ("x" * 65537, "", 0, "unavailable")):
            def child(argv, **kwargs):
                self.assertEqual(argv[:2], ["/usr/sbin/lsof", "-nP"])
                self.assertIn(str(os.getuid()), argv)
                return spawn([sys.executable, "-c", "import sys; sys.stdout.write(" + repr(payload)
                    + "); sys.stderr.write(" + repr(stderr) + "); sys.exit(" + str(code) + ")"], **kwargs)
            with self.subTest(payload=payload, expected=expected), patch.object(guard.subprocess, "Popen", child):
                value = guard.observe()
                self.assertEqual(value["state"], expected)
                self.assertNotIn("synthetic-secret", json.dumps(value))
        with patch.object(guard.subprocess, "Popen", side_effect=OSError("synthetic-secret")):
            self.assertEqual(guard.observe()["state"], "unavailable")

    def test_cli_refuses_before_native_or_file_effects(self) -> None:
        fixture = fixtures.ManagedDarwinInstaller()
        spawn = subprocess.Popen
        def child(argv, **kwargs):
            self.assertEqual(argv, ["/usr/sbin/lsof", "-nP", "-a", "-u", str(os.getuid()),
                "-iTCP:8443", "-sTCP:LISTEN", "-Fpun"])
            return spawn([sys.executable, "-c", "import sys; sys.stdout.write(" + repr(payload) + ")"], **kwargs)
        for payload, state, message in (
                (f"p123\nu{os.getuid()}\nf7\nn100.64.0.1:8443\n", "conflict", guard.MESSAGE),
                (f"p123\nu{os.getuid()}\nf7\nn[fd7a::1]:8443\n", "conflict", guard.MESSAGE),
                (f"p123\nu{os.getuid()}\nf7u\nn127.0.0.1:8443\n", "unavailable", guard.UNAVAILABLE),
                (f"p123\nu{os.getuid()+1}\nf7\nn127.0.0.1:8443\n", "unavailable", guard.UNAVAILABLE),
                (f"p123\nu{os.getuid()}\nf7\nxunknown\nn127.0.0.1:8443\n", "unavailable", guard.UNAVAILABLE)):
            for operation in ("install", "start", "restart", "enable"):
                with self.subTest(state=state, operation=operation), fixture.home() as paths:
                    if operation != "install":
                        fixture.install(paths)  # All other admission prerequisites pass.
                    args = ([operation, "--enrollment-key-file", str(paths["key"])]
                            if operation == "install" else [operation])
                    before = {p.relative_to(paths["root"]): p.read_bytes()
                              for p in paths["root"].rglob("*") if p.is_file()}
                    with patch.object(managed, "observe", guard.observe), patch.object(guard.subprocess, "Popen", child):
                        code, output, error = fixture.command(args)
                    self.assertEqual(code, 1)
                    self.assertIn(message, error)
                    self.assertEqual(output, "")
                    self.assertEqual(before, {p.relative_to(paths["root"]): p.read_bytes()
                        for p in paths["root"].rglob("*") if p.is_file()})
        for host in ("127.0.0.1", "[::1]"):
            payload = f"p123\nu{os.getuid()}\nf7\nn{host}:8443\n"
            for operation in ("start", "restart", "enable"):
                with self.subTest(host=host, operation=operation), fixture.home() as paths:
                    fixture.install(paths)
                    before = fixture.snapshot(paths)
                    rows = [row("print", DOMAIN)]
                    rows += (fixture.start_rows() if operation == "start" else
                             fixture.stop_rows() + fixture.start_rows() if operation == "restart" else
                             [row("enable", fixtures.TARGET), row("enable", fixtures.SIDE_TARGET)])
                    with patch.object(managed, "observe", guard.observe), patch.object(guard.subprocess, "Popen", child):
                        code, _, error = fixture.command([operation], rows, expected_code=0)
                    self.assertEqual((code, error), (0, ""))
                    self.assertEqual(before, fixture.snapshot(paths))

    def test_status_and_drift_report_conflict(self) -> None:
        fixture = fixtures.ManagedDarwinInstaller()
        with fixture.home() as paths:
            fixture.install(paths)
            for operation in ("status", "check-drift"):
                with self.subTest(operation=operation), patch.object(managed, "observe", return_value={
                        "state": "conflict", "message": guard.MESSAGE}):
                    code, output, error = fixture.command([operation], [row("print", DOMAIN), *fixture.status_rows()])
                    self.assertEqual((code, error), (0, ""))
                    self.assertEqual(json.loads(output)["coexistence"]["state"], "conflict")
                    self.assertFalse(json.loads(output)["ready"])

    def test_agent_environment_and_drift(self) -> None:
        fixture = fixtures.ManagedDarwinInstaller()
        for temporary in (None, "/private/tmp/synthetic-native"):
            with self.subTest(temporary=temporary), fixture.home() as paths, patch.dict(os.environ):
                os.environ.pop("TMPDIR", None)
                if temporary:
                    os.environ["TMPDIR"] = temporary
                fixture.install(paths)
                expected = {"HOME": str(paths["root"]), **({"TMPDIR": temporary} if temporary else {})}
                for path in (paths["plist"], paths["side_plist"]):
                    self.assertEqual(plistlib.loads(path.read_bytes()).get("EnvironmentVariables"), expected)
                for path in (paths["plist"], paths["side_plist"]):
                    original = path.read_bytes()
                    changed = plistlib.loads(original)
                    changed["EnvironmentVariables"]["HOME"] = "/different/synthetic-home"
                    try:
                        path.write_bytes(plistlib.dumps(changed))
                        code, _, error = fixture.command(["check-drift"])
                        self.assertEqual(code, 1)
                        self.assertIn("reinstall", error)
                    finally:
                        path.write_bytes(original)
                # Same receipt and files, changed installing process environment.
                os.environ["TMPDIR"] = "/private/tmp/different-native"
                code, _, error = fixture.command(["check-drift"])
                self.assertEqual(code, 1)
                self.assertIn("HOME/TMPDIR", error)
                self.assertIn("reinstall", error)


class AcceptanceLogic(unittest.TestCase):
    def test_finally_settles_sigtermed_and_records_last_print(self) -> None:
        for stuck in (False, True):
            with self.subTest(stuck=stuck), tempfile.TemporaryDirectory() as folder:
                root = Path(folder).resolve()
                home, tmp, interpreter = root / "home", root / "tmp", root / "venv"
                home.mkdir(); tmp.mkdir(); interpreter.mkdir()
                clock = [0.0]
                booted = {}
                settled = set()
                suite_scratch = []
                def staged(path: Path) -> None:
                    suite_scratch.append(path.parent)
                    agents = path.parent / "home/Library/LaunchAgents"
                    agents.mkdir(parents=True)
                    for label in suite.LABELS:
                        (agents / f"{label}.plist").write_text("owned synthetic plist")
                    raise RuntimeError("synthetic staging failure")
                def command(argv: list[str], env: dict[str, str], cwd: Path, deadline: float,
                            *, output_cap: int) -> tuple[int, bytes]:
                    self.assertGreater(deadline, clock[0])
                    verb, target = argv[1:3]
                    if verb == "bootout":
                        if not stuck and target.endswith(suite.LABELS[0]):
                            self.assertIn(target.rsplit("/", 1)[0] + "/" + suite.LABELS[1], settled)
                        booted[target] = clock[0]
                        return 0, b""
                    label = target.rsplit("/", 1)[-1]
                    if verb == "print" and label in suite.LABELS:
                        if target in booted:
                            self.assertTrue(all((suite_scratch[0] / f"home/Library/LaunchAgents/{name}.plist").exists()
                                                for name in suite.LABELS))
                            if stuck or clock[0] - booted[target] < 1:
                                return 0, f'{target} = {{\n state = SIGTERMed\n}}'.encode()
                            settled.add(target)
                        return 113, f'Could not find service "{label}" in domain'.encode()
                    return 0, b"domain observation"
                def sleep(seconds: float) -> None:
                    self.assertGreater(seconds, 0)
                    self.assertLessEqual(seconds, .5)
                    clock[0] += seconds
                report = {}
                with patch.dict(os.environ, HOME=str(home), TMPDIR=str(tmp)), \
                        patch.object(sys, "prefix", str(interpreter)), patch.object(suite, "_command", command), \
                        patch.object(suite.venv.EnvBuilder, "create", side_effect=staged), \
                        patch.object(suite.time, "monotonic", side_effect=lambda: clock[0]), \
                        patch.object(suite.time, "sleep", side_effect=sleep):
                    with self.assertRaisesRegex(RuntimeError, "cleanup incomplete" if stuck else "synthetic staging failure"):
                        suite.run(report)
                section = report["connector_managed"]
                self.assertEqual(section["scratch_removed"], not stuck)
                cleanup = section["cleanup"]
                for target in booted:
                    self.assertEqual(cleanup[f"{target}_absent"], not stuck)
                    self.assertIn("settle", cleanup, "cleanup did not record settlement")
                    receipt = cleanup["settle"][target]
                    self.assertGreaterEqual(receipt["elapsed_seconds"], min(30, 100 - booted[target]) if stuck else 1)
                    self.assertLessEqual(receipt["elapsed_seconds"], 30)
                    self.assertEqual(receipt["last_print"]["exit_code"], 0 if stuck else 113)
                    self.assertIn("SIGTERMed" if stuck else "Could not find service", receipt["last_print"]["excerpt"])
                self.assertLessEqual(clock[0], 100)
                if stuck:
                    self.assertEqual(clock[0], 100, "cleanup reset its original total budget")
                    retained = Path(cleanup["scratch_retained"])
                    self.assertTrue(all((retained / f"home/Library/LaunchAgents/{name}.plist").exists() for name in suite.LABELS))
                    shutil.rmtree(retained)

    def test_cleanup_uncertainty_preserves_suite_scratch(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder).resolve()
            home, tmp, interpreter = root / "home", root / "tmp", root / "venv"
            home.mkdir(); tmp.mkdir(); interpreter.mkdir()
            cleaning = False
            def command(argv, *args, **kwargs):
                nonlocal cleaning
                if argv[1] == "bootout":
                    cleaning = True
                    return 1, b"synthetic bootout failure"
                label = argv[-1].rsplit("/", 1)[-1]
                if argv[1] == "print" and label in suite.LABELS:
                    if cleaning:
                        return 0, b"service remains loaded"
                    return 113, f'Could not find service "{label}" in domain'.encode()
                return 0, b"domain observation"
            report = {}
            with patch.dict(os.environ, HOME=str(home), TMPDIR=str(tmp)), \
                    patch.object(sys, "prefix", str(interpreter)), patch.object(suite, "_command", command), \
                    patch.object(suite.venv.EnvBuilder, "create", side_effect=RuntimeError("synthetic staging failure")):
                with self.assertRaisesRegex(RuntimeError, "cleanup incomplete"):
                    suite.run(report)
            section = report["connector_managed"]
            self.assertTrue(section["owned"])
            self.assertFalse(section["scratch_removed"])
            retained = Path(section["cleanup"]["scratch_retained"])
            self.assertTrue(retained.is_dir())
            # All effects above were scripted; this is our test-owned temp tree.
            shutil.rmtree(retained)

    def test_running_uses_real_cli_status_fields(self) -> None:
        fixture = fixtures.ManagedDarwinInstaller()
        with fixture.home() as paths:
            fixture.install(paths)
            code, output, _ = fixture.command(["status"], [row("print", DOMAIN),
                *fixture.status_rows(connector=fixtures.RUNNING, side=fixtures.RUNNING.replace(fixtures.LABEL, fixtures.SIDECAR_LABEL))])
            self.assertEqual(code, 0)
            status = json.loads(output)["connector"]
            self.assertNotIn("running", status)
            self.assertTrue(suite.process_running(status))
            for field, value in (("pid", 0), ("active_state", "inactive"),
                    ("sub_state", "dead"), ("load_state", "not-found")):
                self.assertFalse(suite.process_running({**status, field: value}))

    def test_absence_requires_exact_target_and_domain_evidence(self) -> None:
        label = suite.LABELS[0]
        self.assertTrue(suite.service_absent(113, f'Could not find service "{label}" in domain', label))
        for code, text in ((0, ""), (125, "unsupported"), (113, "Could not find domain for user"),
                           (113, 'Could not find service "unrelated" in domain')):
            self.assertFalse(suite.service_absent(code, text, label))
        self.assertTrue(suite.domain_unavailable(125, "Domain does not support specified action"))
        self.assertFalse(suite.domain_unavailable(1, "permission denied"))
        self.assertFalse(suite.domain_unavailable(0, "Could not find domain for"))

    def test_not_ready_requires_sidecar_record(self) -> None:
        for state in ("running-not-ready", "starting", "pid-mismatch"):
            value = {"ready": False, "sidecar": {"ready": False, "load_state": "loaded",
                "readiness_state": state, "readiness_record_state": "valid", "readiness_reason": "starting"}}
            self.assertTrue(suite.honest_not_ready(value))
            for field, wrong in (("ready", True), ("load_state", "not-found"),
                    ("readiness_state", "record-missing"), ("readiness_record_state", "corrupt"),
                    ("readiness_reason", "healthy")):
                with self.subTest(state=state, field=field):
                    self.assertFalse(suite.honest_not_ready({**value, "sidecar": {**value["sidecar"], field: wrong}}))
        self.assertFalse(suite.honest_not_ready({}))

    def test_collision_never_installs_or_cleans_preexisting_target(self) -> None:
        for collision in ("service", "domain-error"):
            with self.subTest(collision=collision), tempfile.TemporaryDirectory() as folder:
                root = Path(folder).resolve()
                home, tmp, interpreter = root / "home", root / "tmp", root / "venv"
                home.mkdir(); tmp.mkdir(); interpreter.mkdir()
                calls = []
                def command(argv, *args, **kwargs):
                    calls.append(argv)
                    if collision == "domain-error":
                        return 1, b"permission denied"
                    return 0, b"existing service"
                report = {}
                with patch.dict(os.environ, HOME=str(home), TMPDIR=str(tmp)), \
                        patch.object(sys, "prefix", str(interpreter)), patch.object(suite, "_command", command):
                    with self.assertRaisesRegex(RuntimeError, "check failed"):
                        suite.run(report)
                self.assertTrue(report["connector_managed"]["scratch_removed"])
                self.assertFalse(report["connector_managed"]["owned"])
                self.assertTrue(report["connector_managed"]["cleanup"]["not_owned_no_service_or_plist_cleanup"])
                self.assertTrue(all(argv[:2] == ["launchctl", "print"] for argv in calls))
                self.assertEqual(list(tmp.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
