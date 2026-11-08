"""Small standard JUnit reports and literal synthetic-token sanitation."""
from __future__ import annotations

import os
import time
import xml.etree.ElementTree as ET
import zipfile
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path

HOUSEKEEPING = ("setup", "owned-processes", "ports", "sockets", "data")
IDENTITY = ("python", "source_sha", "event", "run_id", "run_attempt", "exercise")


def scrub(text: str, tokens: set[str]) -> str:
    for token in sorted(tokens, key=len, reverse=True):
        if token:
            text = text.replace(token, "[REDACTED]")
    # XML 1.0 cannot represent most control characters, including NUL.
    return "".join(c if c in "\t\n\r" or 32 <= ord(c) <= 0xD7FF
                   or 0xE000 <= ord(c) <= 0xFFFD or 0x10000 <= ord(c) <= 0x10FFFF
                   else "\ufffd" for c in text)


class Report:
    def __init__(self, properties: dict, tokens: set[str] | None = None) -> None:
        self.properties = properties
        self.tokens = tokens if tokens is not None else set()
        self.cases: dict[tuple[str, str], dict] = {}

    def expect(self, suite: str, names: list[str] | tuple[str, ...]) -> None:
        for name in names:
            self.cases.setdefault((suite, name), {"kind": "error", "message": "Case did not finish; prerequisite or setup unavailable", "seconds": 0.0, "pending": True})

    def record(self, suite: str, name: str, error: BaseException | None = None, *, seconds: float = 0.0) -> None:
        prior = self.cases.get((suite, name))
        # A later success must never erase an earlier failed assertion.
        if prior and not prior.get("pending") and prior["kind"]:
            return
        self.cases[suite, name] = {"kind": ("failure" if isinstance(error, (AssertionError, ValueError)) else "error") if error else None,
                                      "message": str(error) if error else "", "seconds": max(0.0, seconds)}

    @contextmanager
    def check(self, suite: str, name: str) -> Iterator[None]:
        started = time.monotonic()
        try:
            yield
        except BaseException as exc:
            self.record(suite, name, exc, seconds=time.monotonic() - started)
            if not isinstance(exc, Exception):
                raise
        else:
            self.record(suite, name, seconds=time.monotonic() - started)

    @property
    def ok(self) -> bool:
        return bool(self.cases) and all(row["kind"] is None for row in self.cases.values())

    def write(self, path: Path) -> None:
        root = ET.Element("testsuites")
        for suite in dict.fromkeys(key[0] for key in self.cases):
            rows = [(name, row) for (group, name), row in self.cases.items() if group == suite]
            element = ET.SubElement(root, "testsuite", name=suite, tests=str(len(rows)),
                                    failures=str(sum(row["kind"] == "failure" for _, row in rows)),
                                    errors=str(sum(row["kind"] == "error" for _, row in rows)), skipped="0",
                                    time=str(sum(row["seconds"] for _, row in rows)))
            props = ET.SubElement(element, "properties")
            for name in IDENTITY:
                ET.SubElement(props, "property", name=name, value=scrub(str(self.properties.get(name, "UNKNOWN")), self.tokens))
            for name, row in rows:
                case = ET.SubElement(element, "testcase", classname=suite, name=name, time=str(row["seconds"]))
                if row["kind"]:
                    message = scrub(row["message"], self.tokens)
                    ET.SubElement(case, row["kind"], message=message).text = message
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        ET.ElementTree(root).write(temporary, encoding="utf-8", xml_declaration=True)
        os.replace(temporary, path)

    def console(self) -> None:
        for (suite, name), row in self.cases.items():
            print(f"{'FAIL' if row['kind'] else 'PASS'} {suite}.{name}", flush=True)
        passed = sum(row["kind"] is None for row in self.cases.values())
        print(f"TOTAL {passed}/{len(self.cases)} passed; {len(self.cases) - passed} failed", flush=True)


def scrub_trace(source: Path, destination: Path, tokens: set[str], check: Callable[[], None]) -> None:
    """Keep Playwright ZIP member paths and content format; replace known literals."""
    temporary = destination.with_suffix(".tmp")
    with zipfile.ZipFile(source) as original, zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED) as target:
        for member in original.infolist():
            check()
            data = original.read(member)
            for token in sorted(tokens, key=len, reverse=True):
                if token:
                    data = data.replace(token.encode(), b"[REDACTED]")
            target.writestr(member, data)
    check()
    os.replace(temporary, destination)
