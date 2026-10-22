"""Finite external executable protocol; no product imports or decisions."""
from __future__ import annotations

import hashlib
import atexit
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path


def remaining(root: Path, cap: float = 300, end: float | None = None) -> float:
    bound = json.loads((root / "coord/work-end.json").read_text())
    absolute = min(bound["scenario_deadline"], bound["effective_end"],
                   end if end is not None else bound["scenario_deadline"])
    value = absolute - time.monotonic()
    if value <= 0 or cap <= 0:
        raise TimeoutError("provider absolute deadline")
    return min(cap, value)


def jobs_command(instruction: dict, binding: dict) -> dict:
    """Run the real bound CLI as a child of this genuinely admitted provider."""
    root = Path(os.environ["E2E_ROOT"])
    manifest = json.loads((root / "coord/manifest.json").read_text())
    callback = Path(manifest["callback"])
    if hashlib.sha256(callback.read_bytes()).hexdigest() != manifest["callback_sha256"]:
        raise RuntimeError("provider callback binding changed")
    action = instruction["action"]
    if "expected_exit" in instruction:
        raise RuntimeError("caller-selected jobs exits forbidden")
    if action in {"jobs-submit", "jobs-refuse-review", "jobs-block", "jobs-final"}:
        payload = dict(instruction["payload"], task_id=binding["task"], session_id=binding["session"])
        if action not in {"jobs-submit", "jobs-refuse-review"}:
            payload["agent"] = binding["agent"]
        path = root / "coord" / (hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest() + ".json")
        path.write_text(json.dumps(payload, sort_keys=True))
        path.chmod(0o600)
        args = (["jobs", "submit"] if action in {"jobs-submit", "jobs-refuse-review"} else ["report-completion"])
        args += ["--org", binding["org"], "--from-file", str(path)]
        if action in {"jobs-submit", "jobs-refuse-review"}:
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
    payload_file = None
    if payload is not None:
        raw = path.read_bytes()
        payload_file = dict(path=str(path), text=raw.decode("utf-8"), bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest())
    available = instruction["execution_deadline"] - time.monotonic()
    if available <= 0:
        raise RuntimeError("provider common deadline exhausted")
    env = dict(os.environ, HAPPYRANCH_DAEMON_HOME=str(root / "client"))
    argv = [str(callback), *args]
    entered = time.monotonic()
    process = subprocess.Popen(argv, cwd=root, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    timed_out = False
    try:
        child_stat = Path(f"/proc/{process.pid}/stat").read_text()
        child_fields = child_stat.rsplit(")", 1)[1].split()
        child_start = child_fields[19]
        # Setup/spawn time consumes the same absolute child allowance.
        out, err = process.communicate(timeout=remaining(root, end=instruction["execution_deadline"]))
    except subprocess.TimeoutExpired as exc:
        timed_out = True
        # Preserve partial streams; the existing containment/outer owner retains
        # the live child. A timeout never becomes exit0 or a successful case.
        out, err = exc.stdout or "", exc.stderr or ""
        out = out.decode(errors="replace") if isinstance(out, bytes) else out
        err = err.decode(errors="replace") if isinstance(err, bytes) else err
    if not timed_out and time.monotonic() >= instruction["execution_deadline"]:
        timed_out = True
    return dict(action=action, operation=instruction.get("operation"), argv=argv, exit=process.returncode,
                stdout=out, stderr=err, payload=payload, payload_file=payload_file, entered=entered, finished=time.monotonic(),
                timed_out=timed_out, execution_deadline=instruction["execution_deadline"],
                provider=binding, child_pid=process.pid, child_start=child_start,
                child_parent=int(child_fields[1]), child_stat=child_stat,
                python=sys.executable, source_sha=manifest["revision"], callback_sha256=manifest["callback_sha256"])


def main() -> None:
    root = Path(os.environ["E2E_ROOT"])
    task, session, agent, org = sys.argv[1:]
    if org not in {"alpha", "beta"} or agent not in {"case_manager", "case_worker", "code_reviewer"}:
        raise RuntimeError("unexpected external invocation")
    os.setsid()
    with socket.socket(socket.AF_UNIX) as connection:
        connection.settimeout(remaining(root))
        connection.connect(os.environ["E2E_SOCKET"])
        remaining(root)
        stream = connection.makefile("rwb")
        row = dict(task=task, session=session, agent=agent, org=org, pid=os.getpid())
        connection.settimeout(remaining(root))
        stream.write((json.dumps(row) + "\n").encode())
        remaining(root)
        connection.settimeout(remaining(root))
        stream.flush()
        remaining(root)
        final = False
        thread_files: dict = {}
        thread_actions: list[str] = []
        atexit.register(lambda: [path.unlink(missing_ok=True) for path in thread_files.values()])
        command_mode = None
        jobs_actions: list[str] = []
        while True:
            connection.settimeout(remaining(root))
            instruction = json.loads(stream.readline(131072))
            remaining(root)
            if instruction == {"action": "exit"}:
                if jobs_actions and jobs_actions[0] == "jobs-refuse-review" and jobs_actions != ["jobs-refuse-review", "jobs-final"]:
                    raise RuntimeError("incomplete review refusal protocol")
                for path in thread_files.values():
                    path.unlink(missing_ok=True)
                if thread_actions and thread_actions != ["thread-task-send", "thread-task-final"]:
                    raise RuntimeError("incomplete E05 task protocol")
                connection.settimeout(remaining(root))
                stream.write(b'{"exiting":true}\n')
                remaining(root)
                connection.settimeout(remaining(root))
                stream.flush()
                remaining(root)
                return
            if final:
                raise RuntimeError("callback must be the provider's final CLI action")
            selected_mode = ("e07" if instruction["action"].startswith("e07-") else
                             "thread" if instruction["action"].startswith("thread-") else "jobs")
            if command_mode is not None and selected_mode != command_mode:
                raise RuntimeError("provider command modes cannot be mixed")
            command_mode = selected_mode
            if selected_mode == "e07":
                sys.path.insert(0, str(Path(__file__).resolve().parent))
                from late_transport import provider_command
                receipt = provider_command(instruction, row, stream, connection)
                connection.settimeout(remaining(root, end=instruction.get("deadline", instruction.get("execution_deadline"))))
                stream.write((json.dumps(dict(e07_return=receipt)) + "\n").encode())
                remaining(root, end=instruction.get("deadline", instruction.get("execution_deadline")))
                connection.settimeout(remaining(root, end=instruction.get("deadline", instruction.get("execution_deadline"))))
                stream.flush()
                remaining(root, end=instruction.get("deadline", instruction.get("execution_deadline")))
                if instruction["action"] == "e07-held" or receipt["exit"] != 0 or receipt["timed_out"]:
                    raise RuntimeError("E07 held provider returned or parent failed")
                final = True
                continue
            refusal_content_ok = True
            if instruction["action"].startswith("thread-"):
                sys.path.insert(0, str(Path(__file__).resolve().parent))
                from thread_provider import command
                expected = "thread-task-send" if not thread_actions else "thread-task-final"
                if instruction["action"] != expected or len(thread_actions) >= 2:
                    raise RuntimeError("unexpected E05 task action")
                receipt = command(instruction, row, None, thread_files)
                thread_actions.append(instruction["action"])
                # The send refusal is deliberately nonterminal; the final
                # callback is still the provider's last actual CLI operation.
                expected_exit = 1 if instruction["action"] == "thread-task-send" else 0
            else:
                action = instruction["action"]
                if "expected_exit" in instruction:
                    raise RuntimeError("caller-selected exit forbidden")
                refusal = action == "jobs-refuse-review" or jobs_actions[:1] == ["jobs-refuse-review"]
                if refusal:
                    expected = "jobs-refuse-review" if not jobs_actions else "jobs-final"
                    if action != expected or len(jobs_actions) >= 2:
                        raise RuntimeError("closed review refusal sequence")
                    if action == "jobs-refuse-review" and (instruction["payload"].get("review_required") is not True or org != "alpha"):
                        raise RuntimeError("literal review refusal mode")
                    if action == "jobs-final" and instruction["payload"] != dict(status="completed", confidence=90,
                            summary="E04_REVIEW_REQUIRED_REFUSED no job submitted",
                            decision=dict(action="done", summary="E04_REVIEW_REQUIRED_REFUSED no job submitted")):
                        raise RuntimeError("literal review refusal continuation")
                jobs_actions.append(action)
                receipt = jobs_command(instruction, row)
                expected_exit = 2 if action == "jobs-refuse-review" else 0
                if action == "jobs-refuse-review":
                    expected_stderr = (f"Error reading scripts-submit file {receipt['payload_file']['path']}: "
                        "review_required_removed: obtain any required authorization in a thread before submitting; "
                        "upgrade old clients and omit review_required or set it to false. This request was not submitted.\n")
                    refusal_content_ok = receipt["stdout"] == "" and receipt["stderr"] == expected_stderr
            connection.settimeout(remaining(root, end=instruction.get("deadline", instruction.get("execution_deadline"))))
            stream.write((json.dumps(receipt) + "\n").encode())
            remaining(root, end=instruction.get("deadline", instruction.get("execution_deadline")))
            connection.settimeout(remaining(root, end=instruction.get("deadline", instruction.get("execution_deadline"))))
            stream.flush()
            remaining(root, end=instruction.get("deadline", instruction.get("execution_deadline")))
            if receipt["exit"] != expected_exit or receipt["timed_out"] or receipt.get("output_truncated", False) or not refusal_content_ok:
                for path in thread_files.values():
                    path.unlink(missing_ok=True)
                raise RuntimeError("real provider CLI failed")
            final = instruction["action"] in {"jobs-block", "jobs-final", "thread-task-final"}


if __name__ == "__main__":
    main()
