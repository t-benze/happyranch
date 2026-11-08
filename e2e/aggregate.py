"""Health reads four ordinary JUnit reports; product checks run in each lane."""
from __future__ import annotations

import argparse
import json
import os
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path

from reporting import HOUSEKEEPING, IDENTITY
from run import daily_admission

MANIFEST = json.loads(Path(__file__).with_name("manifest.json").read_text())


def validate_report(path: Path, expected: dict, *, signal_name: str | None = None) -> tuple[int, int]:
    root = ET.parse(path).getroot()
    suites = root.findall("testsuite")
    expected_suites = {"diagnostic"} if signal_name else {"product", "harness"}
    if root.tag != "testsuites" or Counter(s.get("name") for s in suites) != Counter(expected_suites):
        raise ValueError("Missing, duplicate or unexpected suites")
    product_passed = diagnostic_passed = 0
    for suite in suites:
        properties = suite.findall("properties")
        if len(properties) != 1:
            raise ValueError("Missing or duplicate report properties")
        props = properties[0].findall("property")
        if Counter(p.get("name") for p in props) != Counter(IDENTITY):
            raise ValueError("Missing, duplicate or unexpected report identity")
        if {p.get("name"): p.get("value") for p in props} != expected:
            raise ValueError("Report Python/source/event/run/exercise mismatch")
        cases = suite.findall("testcase")
        names = [case.get("name") for case in cases]
        if not cases or len(names) != len(set(names)):
            raise ValueError("Missing or duplicate cases")
        suite_name = suite.get("name")
        if suite_name == "product":
            if Counter(names) != Counter(MANIFEST["variants"]):
                raise ValueError("Product cases differ from exact 56-name manifest")
        elif suite_name == "diagnostic":
            if set(names) != {*HOUSEKEEPING, "launcher." + signal_name}:
                raise ValueError("Missing or unexpected launcher/setup/cleanup cases")
        elif not set(HOUSEKEEPING) <= set(names):
            raise ValueError("Missing setup or cleanup cases")
        for case in cases:
            if case.get("classname") != suite_name or list(case):
                # Own reports use no testcase children on pass. In particular,
                # failure/error/skipped cannot hide behind declared zero totals.
                raise ValueError("Failed, errored, skipped or malformed case: " + str(case.get("name")))
        if suite.get("tests") != str(len(cases)) or any(suite.get(key) != "0" for key in ("failures", "errors", "skipped")):
            raise ValueError("Inconsistent JUnit totals")
        if suite_name == "product":
            product_passed += len(cases)
        if suite_name == "diagnostic":
            diagnostic_passed += 1
    return product_passed, diagnostic_passed


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifacts", type=Path, required=True)
    parser.add_argument("--source", required=True)
    parser.add_argument("--event", required=True)
    parser.add_argument("--matrix-result", required=True)
    parser.add_argument("--cancellation-result", required=True)
    args = parser.parse_args()
    errors: list[str] = []
    product = diagnostics = 0
    identity = {}
    try:
        identity = daily_admission(Path(__file__).resolve().parents[1])
        if identity["source_sha"] != args.source or identity["event"] != args.event:
            raise ValueError("Requested source/event differs from Actions checkout")
    except Exception as exc:
        errors.append(str(exc))
    if args.matrix_result != "success" or args.cancellation_result != "success":
        errors.append(f"Jobs: lanes={args.matrix_result}, diagnostics={args.cancellation_result}")
    reports = [("e2e-" + version, version, None) for version in MANIFEST["daily_python"]]
    reports += [("e2e-cancel-TERM", "3.12", "TERM"), ("e2e-cancel-INT", "3.14", "INT")]
    for artifact, version, signal_name in reports:
        try:
            expected = dict(python=version, source_sha=args.source, event=args.event,
                            run_id=identity["run_id"], run_attempt=identity["run_attempt"],
                            exercise="cancellation" if signal_name else "baseline")
            p, d = validate_report(args.artifacts / artifact / "junit.xml", expected, signal_name=signal_name)
            product += p
            diagnostics += d
            print("PASS " + artifact)
        except Exception as exc:
            errors.append(artifact + ": " + str(exc))
            print("FAIL " + artifact)
    status = "FAIL" if errors else "PASS"
    lines = [f"## Product E2E Health: {status}", "", f"Product cases: {product}/112",
             f"Launcher diagnostics: {diagnostics}/2", "Setup/cleanup: " + ("FAIL or unavailable" if errors else "PASS"),
             f"Source: `{args.source}`; event: `{args.event}`; run: `{identity.get('run_id', 'UNKNOWN')}`; attempt: `{identity.get('run_attempt', 'UNKNOWN')}`.",
             "", "JUnit uses GitHub default artifact retention. Health does not block merging."]
    lines.extend("- " + error.replace("\n", " ") for error in errors)
    print("\n".join(lines))
    if summary := os.environ.get("GITHUB_STEP_SUMMARY"):
        with Path(summary).open("a") as stream:
            stream.write("\n".join(lines) + "\n")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
