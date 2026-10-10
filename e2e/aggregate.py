"""Stable gate: missing cells/variants, skips and unknown cleanup always fail."""
from __future__ import annotations

import argparse
import copy
import json
import signal

from cancellation import validate as validate_cancellation
from pathlib import Path


MANIFEST = json.loads(Path(__file__).with_name("manifest.json").read_text())


def validate(rows: list[dict], expected: list[str], source: str, matrix_result: str) -> None:
    if matrix_result != "success":
        raise ValueError(f"matrix result is {matrix_result}")
    if len(rows) != len(expected) or sorted(r["python"] for r in rows) != sorted(expected):
        raise ValueError("missing, duplicate or unexpected matrix cell")
    for row in rows:
        if row["source_sha"] != source or row["status"] != "PASS" or row["skipped"] != 0:
            raise ValueError("source/result/skip mismatch")
        if set(row["variants"]) != set(MANIFEST["variants"]):
            raise ValueError("missing or unexpected variant")
        if any(v["status"] != "PASS" for v in row["variants"].values()):
            raise ValueError("failed or skipped variant")
        if row["cleanup"]["ok"] is not True or row["cleanup"]["data_absent"] is not True:
            raise ValueError("unsuccessful or unknown cleanup")
        if not row["cleanup"]["ports_absent"] or not all(value is True for value in row["cleanup"]["ports_absent"].values()):
            raise ValueError("missing port absence")
        if row["total_seconds"] > MANIFEST["hard_seconds"]:
            raise ValueError("hard deadline exceeded")
        required = {"dependencies", "browser-dependencies", "browser-install", "web-dependencies", "web-build", "scenarios"}
        if {p["name"] for p in row["phases"]} != required or any(p["exit"] != 0 for p in row["phases"]):
            raise ValueError("missing or failed execution phase")


def controls() -> dict:
    """Bounded validator controls only; never product-behavior evidence."""
    source = "1" * 40
    valid = [dict(python=p, source_sha=source, status="PASS", skipped=0, total_seconds=10,
                  variants={v: {"status": "PASS"} for v in MANIFEST["variants"]},
                  cleanup=dict(ok=True, data_absent=True, ports_absent={"1234": True}),
                  phases=[dict(name=n, exit=0) for n in ("dependencies", "browser-dependencies", "browser-install", "web-dependencies", "web-build", "scenarios")])
             for p in MANIFEST["pr_python"]]
    validate(valid, MANIFEST["pr_python"], source, "success")
    results = {}
    for name in ("missing-cell", "skip", "failed-variant", "missing-variant", "wrong-sha", "cleanup", "failed-job"):
        rows = copy.deepcopy(valid)
        state = "success"
        if name == "missing-cell":
            rows.pop()
        elif name == "skip":
            rows[0]["skipped"] = 1
        elif name == "failed-variant":
            rows[0]["variants"][MANIFEST["variants"][0]]["status"] = "FAIL"
        elif name == "missing-variant":
            rows[0]["variants"].pop(MANIFEST["variants"][0])
        elif name == "wrong-sha":
            rows[0]["source_sha"] = "2" * 40
        elif name == "cleanup":
            rows[0]["cleanup"]["ok"] = False
        else:
            state = "failure"
        try:
            validate(rows, MANIFEST["pr_python"], source, state)
        except ValueError as exc:
            results[name] = str(exc)
        else:
            raise AssertionError(f"gate incorrectly accepted {name}")
    validate(valid, MANIFEST["pr_python"], source, "success")
    results.update(cancellation_controls())
    return results


def cancellation_controls() -> dict:
    """Synthetic refusal controls; these never count as actual signal evidence."""
    source = "1" * 40
    phases = [dict(name=n, exit=0) for n in ("dependencies", "browser-dependencies", "browser-install", "web-dependencies", "web-build")]
    phases.append(dict(name="scenarios", exit=None, interrupted=True))
    fixture = dict(source_sha=source, launcher_exit=1, launcher_absent=True,
                   identities_live_at_signal=True, total_seconds=10,
                   sent=dict(signal=15, at=2, pid=10, start="100"),
                   active=dict(source_sha=source, at=1, launcher=dict(pid=10, start="100"),
                               provider=dict(pid=11, pid_start="101"), browser_started=True,
                               callback=dict(status=200, response={"ok": True}, dropped=False), ports=[1234, 1235],
                               owned=dict(identities={"11": "101"}, units=["happyranch-example.service"], groups=["/example"])),
                   launcher_result=dict(source_sha=source, python="3.12", exercise="cancellation", status="FAIL", skipped=0,
                                        signals=[dict(signal=15, at=3, pid=10, start="100")], total_seconds=10, phases=phases,
                                        cleanup=dict(ok=True, data_absent=True, sockets_absent=True, watcher_stopped=True,
                                                     survivors={}, errors=[], identities={"11": "101"},
                                                     units={"happyranch-example.service": {"stdout": "LoadState=not-found"}},
                                                     groups={"/example": False}, ports_absent={"1234": True, "1235": True})))
    validate_cancellation(fixture, source, "3.12", 15)
    results = {}
    for name in ("no-delivery", "wrong-signal", "false-success", "not-active", "missing-identity", "missing-unit", "live-cgroup", "missing-port", "data-residue", "setup-failure"):
        row = copy.deepcopy(fixture)
        result = row["launcher_result"]
        if name == "no-delivery": result["signals"] = []
        elif name == "wrong-signal": result["signals"][0]["signal"] = 2
        elif name == "false-success": row["launcher_exit"] = 0
        elif name == "not-active": row["identities_live_at_signal"] = False
        elif name == "missing-identity": result["cleanup"]["identities"] = {}
        elif name == "missing-unit": result["cleanup"]["units"] = {}
        elif name == "live-cgroup": result["cleanup"]["groups"]["/example"] = True
        elif name == "missing-port": result["cleanup"]["ports_absent"] = {}
        elif name == "data-residue": result["cleanup"]["data_absent"] = False
        elif name == "setup-failure": result["phases"][0]["exit"] = 1
        try:
            validate_cancellation(row, source, "3.12", 15)
        except ValueError as exc:
            results["cancellation-" + name] = str(exc)
        else:
            raise AssertionError(f"cancellation validator accepted {name}")
    return results


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--controls", action="store_true")
    parser.add_argument("--artifacts", type=Path)
    parser.add_argument("--source")
    parser.add_argument("--event", choices=["pull_request", "push", "workflow_dispatch"])
    parser.add_argument("--matrix-result")
    parser.add_argument("--cancellation-result")
    args = parser.parse_args()
    if args.controls:
        print(json.dumps(controls(), indent=2))
        return
    if not all((args.artifacts, args.source, args.event, args.matrix_result, args.cancellation_result)):
        parser.error("artifacts, source, event, matrix-result and cancellation-result are required")
    rows = [json.loads(path.read_text()) for path in args.artifacts.glob("e2e-[0-9]*/result.json")]
    expected = MANIFEST["pr_python" if args.event == "pull_request" else "main_python"]
    validate(rows, expected, args.source, args.matrix_result)
    if args.cancellation_result != "success":
        raise ValueError("cancellation jobs did not succeed")
    cancellation = sorted(args.artifacts.glob("e2e-cancel-*/cancellation.json"))
    if len(cancellation) != 2:
        raise ValueError("missing or duplicate cancellation cell")
    seen = set()
    for path in cancellation:
        row = json.loads(path.read_text())
        signum = row["sent"]["signal"]
        if signum in seen or signum not in {int(signal.SIGTERM), int(signal.SIGINT)} or row["status"] != "PASS":
            raise ValueError("unexpected or failed cancellation cell")
        seen.add(signum)
        python = "3.12" if signum == signal.SIGTERM else "3.14"
        validate_cancellation(row, args.source, python, signum)
    print(json.dumps(dict(status="PASS", source_sha=args.source, cells=expected, cancellation=sorted(seen))))


if __name__ == "__main__":
    main()
