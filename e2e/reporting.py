"""Small standard JUnit reports and literal synthetic-token sanitation."""
from __future__ import annotations

import json
import math
import os
import time
import xml.etree.ElementTree as ET
import zipfile
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path

HOUSEKEEPING = ("setup", "owned-processes", "ports", "sockets", "data")
IDENTITY = ("python", "source_sha", "event", "run_id", "run_attempt", "exercise")


def sampler_timings(observer: list[dict], durable: list[dict], window: dict) -> tuple[dict, dict]:
    """Summarize existing clocks, including partial observations after failure."""
    def intervals(values: list[float]) -> dict:
        ordered = sorted(values)
        return dict(min=min(values, default=None), max=max(values, default=None),
                    p95=ordered[math.ceil(.95 * len(ordered)) - 1] if ordered else None)

    anchors = [r["jobs_anchor"] for r in durable if "jobs_anchor" in r
               and not r["jobs_anchor"]["name"].startswith("E05:")]
    e04 = dict(sampler="E04", observer_sample_count=len(observer),
               inter_sample_gap_seconds=intervals([b["at"] - a["at"] for a, b in zip(observer, observer[1:])]),
               observer_first_monotonic_seconds=observer[0]["at"] if observer else None,
               observer_last_monotonic_seconds=observer[-1]["at"] if observer else None,
               barriers=[dict(name=a["name"], entered_monotonic_seconds=a["entered"],
                              finished_monotonic_seconds=a.get("finished")) for a in anchors])
    samples = window.get("samples", [])
    entered, end = window.get("entered"), window.get("end")
    e07 = dict(sampler="E07", frame_count=len(samples),
               start_interval_seconds=intervals([b["entered"] - a["entered"] for a, b in zip(samples, samples[1:])]),
               max_frame_duration_seconds=max((s["finished"] - s["entered"] for s in samples), default=None),
               window_entered_monotonic_seconds=entered, window_end_monotonic_seconds=end,
               first_start_offset_seconds=samples[0]["entered"] - entered if samples and entered is not None else None,
               last_start_offset_seconds=samples[-1]["entered"] - entered if samples and entered is not None else None,
               last_start_from_end_seconds=samples[-1]["entered"] - end if samples and end is not None else None,
               last_finish_from_end_seconds=samples[-1]["finished"] - end if samples and end is not None else None)
    return e04, e07


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
        self.outputs: dict[tuple[str, str], str] = {}

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

    def merge(self, path: Path, expected: list[str]) -> None:
        document = ET.parse(path).getroot()
        seen: set[tuple[str, str]] = set()
        product = []
        incoming = []
        for suite in document.findall("testsuite"):
            group = suite.attrib["name"]
            if group not in {"product", "harness"}:
                raise ValueError("Unexpected controller suite")
            properties = {p.attrib["name"]: p.attrib["value"] for p in suite.findall("./properties/property")}
            if any(properties.get(name) != str(self.properties.get(name, "UNKNOWN")) for name in IDENTITY):
                raise ValueError("Controller report identity mismatch")
            for case in suite.findall("testcase"):
                name = case.attrib["name"]
                key = group, name
                if key in seen or case.find("skipped") is not None:
                    raise ValueError("Duplicate or skipped controller case")
                seen.add(key)
                if group == "product":
                    product.append(name)
                failures = [node for node in case if node.tag in {"failure", "error"}]
                if len(failures) > 1:
                    raise ValueError("Multiple controller case outcomes")
                outputs = case.findall("system-out")
                if len(outputs) > 1 or any(node.attrib or list(node) for node in outputs):
                    raise ValueError("Malformed controller case output")
                kind = failures[0].tag if failures else None
                seconds = float(case.attrib.get("time", "0"))
                if not 0 <= seconds < float("inf"):
                    raise ValueError("Invalid controller case duration")
                incoming.append((key, dict(kind=kind, message=failures[0].attrib.get("message", "") if failures else "", seconds=seconds),
                                 case.findtext("system-out")))
        if product != expected:
            raise ValueError("Controller product names/order differ from manifest")
        for key, row, output in incoming:
            prior = self.cases.get(key)
            if not prior or prior.get("pending") or not prior["kind"]:
                self.cases[key] = row
            if output is not None:
                self.outputs[key] = output

    def record_sampler_timings(self, observer: list[dict], durable: list[dict], window: dict) -> None:
        for values in sampler_timings(observer, durable, window):
            # One representative case carries each group's lane-wide timings.
            name = next(name for suite, name in self.cases
                        if suite == "product" and name.startswith(values["sampler"] + "."))
            self.outputs["product", name] = json.dumps(values, sort_keys=True, allow_nan=False)

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
                if (suite, name) in self.outputs:
                    ET.SubElement(case, "system-out").text = scrub(self.outputs[suite, name], self.tokens)
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
