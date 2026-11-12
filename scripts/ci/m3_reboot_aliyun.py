#!/usr/bin/env python3
"""Run two M3 reboot passes through Cloud Assistant; no inbound control port.

Run as an authorized persistent HappyRanch job, from a clean published checkout:
  python3 scripts/ci/m3_reboot_aliyun.py --instance ID --sha FULL_SHA --output DIR
The default Aliyun CLI profile stays on the host. Only public source and
synthetic fixtures are installed on the VM. Never run against a product host.
"""
from __future__ import annotations

import argparse
import base64
import gzip
import io
import json
from pathlib import Path
import re
import shlex
import subprocess
import time
import tarfile

BASE = "/var/lib/happyranch-m3"


class CloudAssistant:
    def __init__(self, instance: str, region: str) -> None:
        self.instance = instance
        self.region = region

    def api(self, operation: str, *args: str) -> dict:
        result = subprocess.run(
            ["aliyun", "ecs", operation, "--RegionId", self.region, *args],
            capture_output=True, text=True, timeout=45, check=False,
        )
        if result.returncode:
            # Do not echo CLI stderr/profile/request data into acceptance logs.
            raise RuntimeError(f"Aliyun {operation} failed, exit {result.returncode}")
        return json.loads(result.stdout)

    def run(self, command: str, timeout: int = 120) -> tuple[int, str]:
        invocation = self.api(
            "RunCommand", "--Type", "RunShellScript", "--InstanceId.1", self.instance,
            "--CommandContent", "exec /bin/bash -c " + shlex.quote(command), "--ContentEncoding", "PlainText", "--Timeout", str(timeout),
        )["InvokeId"]
        deadline = time.monotonic() + timeout + 90
        while time.monotonic() < deadline:
            response = self.api("DescribeInvocationResults", "--InvokeId", invocation)
            rows = response.get("Invocation", {}).get("InvocationResults", {}).get("InvocationResult", [])
            if rows:
                row = rows[0]
                if row.get("InvokeRecordStatus") in {"Finished", "Failed", "Stopped"}:
                    output = base64.b64decode(row.get("Output", "")).decode(errors="replace")
                    if row.get("InvocationStatus") != "Success":
                        return int(row.get("ExitCode") or 1), output
                    return int(row.get("ExitCode", 0)), output
            time.sleep(1)
        raise RuntimeError(f"Cloud Assistant invocation {invocation} did not settle")

    def reboot(self, before: str) -> str:
        rc, _ = self.run(
            "systemd-run --unit=happyranch-m3-reboot --on-active=5 /usr/bin/systemctl reboot", timeout=15
        )
        if rc:
            raise RuntimeError("guest reboot scheduling failed")
        deadline = time.monotonic() + 300
        while time.monotonic() < deadline:
            time.sleep(5)
            try:
                rc, output = self.run("cat /proc/sys/kernel/random/boot_id", timeout=15)
                after = output.strip()
                if rc == 0 and re.fullmatch(r"[0-9a-f-]{36}", after) and after != before:
                    return after
            except (RuntimeError, subprocess.TimeoutExpired):
                # Read-only reachability observations; never repeat the reboot.
                continue
        raise RuntimeError("VM did not return through Cloud Assistant with a changed boot_id")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--instance", required=True)
    parser.add_argument("--region", default="cn-hongkong")
    parser.add_argument("--sha", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not re.fullmatch(r"[0-9a-f]{40}", args.sha):
        parser.error("--sha must be a full immutable commit SHA")
    args.output.mkdir(parents=True, exist_ok=True)
    ca = CloudAssistant(args.instance, args.region)
    summary: dict = {"sha": args.sha, "instance": args.instance, "passes": []}

    def save(name: str, output: str) -> None:
        data = output.encode()
        if len(data) > 1_000_000:
            (args.output / (name + ".gz")).write_bytes(gzip.compress(data))
            lines = output.splitlines()
            output = "\n".join(lines[:40] + ["... full log in gzip ..."] + lines[-60:]) + "\n"
        (args.output / name).write_text(output)

    def phase(number: int, name: str, expected: int = 0) -> str:
        log = f"{BASE}/logs/{number}/{name}.log"
        command = (
            f"cd {BASE}/repo && mkdir -p {BASE}/logs/{number}; "
            f"M3_AUTHORIZED_VM=yes M3_PASS={number} "
            f"PATH={BASE}/tools/bin:/usr/local/go/bin:/usr/sbin:/usr/bin:/sbin:/bin "
            f"GOMAXPROCS=2 GOFLAGS=-p=2 bash scripts/ci/m3_reboot.sh {name} "
            f"> {log} 2>&1; rc=$?; tail -60 {log}; exit \"$rc\""
        )
        print(f"pass {number}: {name}", flush=True)
        rc, output = ca.run(command, timeout=1200 if name == "prepare" else 330)
        save(f"pass-{number}-{name}.excerpt.txt", f"exit={rc}\n{output}")
        print(f"pass {number}: {name} exit={rc}\n{output[-2500:]}", flush=True)
        if rc != expected:
            raise RuntimeError(f"pass {number} {name}: exit {rc}, expected {expected}")
        return output

    def collect(number: int) -> None:
        # Fetch ONLY explicit plain logs/projections, never state, databases or keys.
        archive = f"{BASE}/logs-{number}.tar.gz"
        rc, output = ca.run(
            f"tar -C {BASE}/logs/{number} -czf {archive} . && stat -c %s {archive}"
        )
        size = int(output.strip())
        if rc or size > 20_000_000:
            raise RuntimeError("log archive failed or exceeds 20 MB")
        chunks = []
        for offset in range(0, size, 12000):
            rc, chunk = ca.run(
                f"dd if={archive} bs=1 skip={offset} count=12000 status=none | base64 -w0"
            )
            if rc:
                raise RuntimeError("log chunk read failed")
            chunks.append(base64.b64decode(chunk))
        data = b"".join(chunks)
        if len(data) != size:
            raise RuntimeError("incomplete log transfer")
        total = 0
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as bundle:
            for member in bundle:
                if member.isdir():
                    continue
                name = member.name.removeprefix("./")
                if (not member.isfile() or not re.fullmatch(r"[a-zA-Z0-9_./-]+", name)
                        or ".." in name.split("/") or name.startswith("/")):
                    raise RuntimeError("unsafe log member")
                total += member.size
                if total > 40_000_000:
                    raise RuntimeError("uncompressed log budget exceeded")
                stream = bundle.extractfile(member)
                assert stream is not None
                save(f"pass-{number}-" + name.replace("/", "-"), stream.read().decode())
        if sum(p.stat().st_size for p in args.output.iterdir() if p.is_file()) > 45_000_000:
            raise RuntimeError("task evidence exceeds 45 MB budget")

    bootstrap = f"""set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
mkdir -p {BASE}/logs
apt-get update > {BASE}/bootstrap.log 2>&1
apt-get install -y git curl ca-certificates openssl sudo python3-venv golang-go >> {BASE}/bootstrap.log 2>&1
if [ ! -x {BASE}/tools/bin/uv ]; then
  python3 -m venv {BASE}/tools
  {BASE}/tools/bin/pip install uv >> {BASE}/bootstrap.log 2>&1
fi
if [ ! -d {BASE}/repo ]; then git clone --filter=blob:none https://github.com/t-benze/happyranch {BASE}/repo >> {BASE}/bootstrap.log 2>&1; fi
git -C {BASE}/repo fetch origin {args.sha} >> {BASE}/bootstrap.log 2>&1
test -z "$(git -C {BASE}/repo status --porcelain)"
git -C {BASE}/repo checkout --detach {args.sha} >> {BASE}/bootstrap.log 2>&1
test "$(git -C {BASE}/repo rev-parse HEAD)" = {args.sha}
echo 'Public checkout verified at {args.sha}'
"""
    rc, output = ca.run(bootstrap, timeout=600)
    save("bootstrap.excerpt.txt", f"exit={rc}\n{output}")
    if rc:
        raise RuntimeError("VM bootstrap failed; inspect /var/lib/happyranch-m3/bootstrap.log")
    try:
        for number in (1, 2):
            record: dict = {"pass": number, "verdict": "INCOMPLETE", "reboots": []}
            summary["passes"].append(record)
            try:
                phase(number, "prepare")
                # Failure proof: enabled target must fail the stopped-state check.
                proof = phase(number, "negative-stop", expected=1)
                if "target is still enabled" not in proof:
                    raise RuntimeError("negative-stop failed for an unrelated reason")
                for pre, post in (("pre-reboot", "post-reboot"), ("stop-pre-reboot", "stop-post-reboot")):
                    phase(number, pre)
                    rc, output = ca.run("cat /proc/sys/kernel/random/boot_id")
                    if rc:
                        raise RuntimeError("boot_id read failed")
                    before = output.strip()
                    after = ca.reboot(before)
                    record["reboots"].append({"phase": post, "before": before, "after": after})
                    phase(number, post)
                phase(number, "logs")
                phase(number, "purge")
                phase(number, "teardown")
                record["verdict"] = "PASS"
            except Exception as error:
                record["verdict"] = "FAIL"
                record["error"] = str(error)
                # No product workaround. Preserve diagnostics, then clean test state.
                for name in ("logs", "purge", "teardown"):
                    try:
                        phase(number, name)
                    except Exception as cleanup_error:
                        record.setdefault("cleanup_errors", []).append(str(cleanup_error))
                raise
            finally:
                collect(number)
                (args.output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    finally:
        (args.output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps(summary, indent=2), flush=True)


if __name__ == "__main__":
    main()
