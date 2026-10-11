"""Finite external executable protocol; no product imports or decisions."""
from __future__ import annotations

import hashlib
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path


def jobs_command(instruction: dict, binding: dict) -> dict:
    """Run the real bound CLI as a child of this genuinely admitted provider."""
    root = Path(os.environ["E2E_ROOT"])
    manifest = json.loads((root / "coord/manifest.json").read_text())
    callback = Path(manifest["callback"])
    if hashlib.sha256(callback.read_bytes()).hexdigest() != manifest["callback_sha256"]:
        raise RuntimeError("provider callback binding changed")
    action = instruction["action"]
    if action in {"jobs-submit", "jobs-block", "jobs-final"}:
        payload = dict(instruction["payload"], task_id=binding["task"], session_id=binding["session"])
        if action != "jobs-submit":
            payload["agent"] = binding["agent"]
        path = root / "coord" / (hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest() + ".json")
        path.write_text(json.dumps(payload, sort_keys=True))
        path.chmod(0o600)
        args = (["jobs", "submit"] if action == "jobs-submit" else ["report-completion"])
        args += ["--org", binding["org"], "--from-file", str(path)]
        if action == "jobs-submit":
            args += ["--json"]
    elif action == "jobs-read":
        operation = instruction["operation"]
        if operation not in {"show", "receipt", "output", "tail", "wait"}:
            raise RuntimeError("unexpected jobs read")
        args = ["jobs", "show" if operation == "receipt" else operation, instruction["job"], "--org", binding["org"]]
        if operation in {"receipt", "output"}:
            args += ["--json", "--task-id", binding["task"], "--session-id", binding["session"]]
        if operation == "wait":
            args += ["--timeout-seconds", "1"]
        if operation in {"tail", "wait"}:
            args += ["--task-id", binding["task"], "--session-id", binding["session"]]
        payload = None
    else:
        raise RuntimeError("unexpected jobs instruction")
    remaining = min(10, instruction["deadline"] - time.monotonic())
    if remaining <= 0:
        raise RuntimeError("provider common deadline exhausted")
    env = dict(os.environ, HAPPYRANCH_DAEMON_HOME=str(root / "client"))
    argv = [str(callback), *args]
    entered = time.monotonic()
    process = subprocess.Popen(argv, cwd=root, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        child_start = Path(f"/proc/{process.pid}/stat").read_text().rsplit(")", 1)[1].split()[19]
        out, err = process.communicate(timeout=remaining)
    except BaseException:
        # The unchanged outer owner census retains this CLI child on failure.
        raise
    return dict(action=action, operation=instruction.get("operation"), argv=argv, exit=process.returncode,
                stdout=out, stderr=err, payload=payload, entered=entered, finished=time.monotonic(),
                provider=binding, child_pid=process.pid, child_start=child_start,
                python=sys.executable, source_sha=manifest["revision"], callback_sha256=manifest["callback_sha256"])


def main() -> None:
    task, session, agent, org = sys.argv[1:]
    if org not in {"alpha", "beta"} or agent not in {"case_manager", "case_worker", "code_reviewer"}:
        raise RuntimeError("unexpected external invocation")
    os.setsid()
    with socket.socket(socket.AF_UNIX) as connection:
        connection.settimeout(300)
        connection.connect(os.environ["E2E_SOCKET"])
        stream = connection.makefile("rwb")
        row = dict(task=task, session=session, agent=agent, org=org, pid=os.getpid())
        stream.write((json.dumps(row) + "\n").encode())
        stream.flush()
        final = False
        while True:
            instruction = json.loads(stream.readline(131072))
            if instruction == {"action": "exit"}:
                stream.write(b'{"exiting":true}\n')
                stream.flush()
                return
            if final:
                raise RuntimeError("callback must be the provider's final CLI action")
            receipt = jobs_command(instruction, row)
            stream.write((json.dumps(receipt) + "\n").encode())
            stream.flush()
            if receipt["exit"] != 0:
                raise RuntimeError("real provider CLI failed")
            final = instruction["action"] in {"jobs-block", "jobs-final"}


if __name__ == "__main__":
    main()
