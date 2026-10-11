"""Finite E05 external plan. Only this admitted process spends its token."""
from __future__ import annotations

import hashlib
import json
import os
import re
import socket
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path


def redaction_context(root: Path, token: str | None) -> set[str]:
    """Read the retained private context; never echo unsafe bytes on failure."""
    try:
        path = root / "coord/redaction-context.json"
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(descriptor, "r") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o600:
                raise ValueError
            context = json.load(stream)
        bearer = (root / "daemon/daemon.token").read_text().strip()
        if (not isinstance(context, list) or not context or not bearer
                or not all(isinstance(s, str) and s for s in context)
                or bearer not in context or (token is not None and token not in context)):
            raise ValueError
        for secret in context:
            secret.encode("utf-8", errors="strict")
        return set(context)
    except Exception:
        raise RuntimeError("E05 private redaction context unavailable or unsafe") from None


def command(instruction: dict, binding: dict, token: str | None, files: dict) -> dict:
    root = Path(os.environ["E2E_ROOT"])
    secrets = redaction_context(root, token)
    manifest = json.loads((root / "coord/manifest.json").read_text())
    callback = Path(manifest["callback"])
    if hashlib.sha256(callback.read_bytes()).hexdigest() != manifest["callback_sha256"]:
        raise RuntimeError("E05 callback source changed")
    action = instruction["action"]
    payload = None
    if action in {"thread-reply", "thread-decline", "thread-dispatch"}:
        if not token:
            raise RuntimeError("thread authority missing")
        operation = action.removeprefix("thread-")
        supplied = instruction["payload"]
        if operation == "decline":
            allowed = {("E05 initial observed",1), ("E05 reply observed",2), ("E05 denial venue observed",1)}
            if binding["agent"] != "case_worker" or binding["purpose"] != "reply" or set(supplied) != {"reason","in_response_to_seq"} or (supplied["reason"],supplied["in_response_to_seq"]) not in allowed:
                raise RuntimeError("unexpected finite decline")
        elif operation == "reply":
            allowed = {("E05 reply\n@case_worker received",1), ("E05 task accepted",1)} if binding["purpose"] == "reply" else {("E05 task complete",4)}
            if binding["agent"] != "case_manager" or set(supplied) != {"body_markdown","in_response_to_seq"} or (supplied["body_markdown"],supplied["in_response_to_seq"]) not in allowed:
                raise RuntimeError("unexpected finite reply")
        elif binding["agent"] != "case_manager" or binding["purpose"] != "reply" or supplied != dict(brief="E05 one owned task",target_agent="case_manager",team="engineering"):
            raise RuntimeError("unexpected finite dispatch")
        payload = dict(instruction["payload"], thread_id=binding["thread"], invocation_token=token)
        payload["dispatcher" if operation == "dispatch" else "speaker"] = binding["agent"]
        key = "reply" if operation == "reply" else operation
        raw = json.dumps(payload, sort_keys=True).encode()
        if key in files:
            path = files[key]
            if path.read_bytes() != raw or operation != "reply":
                raise RuntimeError("only exact same-file consumed reply replay is admitted")
        else:
            descriptor, name = tempfile.mkstemp(prefix="e05-private-", dir=root / "coord")
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(raw)
            path = Path(name)
            files[key] = path
        args = ["threads", operation, "--org", "alpha", "--thread-id", binding["thread"], "--from-file", str(path)]
    elif action in {"thread-task-send", "thread-task-final"}:
        if token or binding["agent"] != "case_manager" or binding["org"] != "alpha":
            raise RuntimeError("requires genuine E05 manager task authority")
        payload = dict(instruction["payload"])
        expected = (dict(status="completed",confidence=90,summary="E05 task complete",decision=dict(action="done",summary="E05 task complete"))
                    if action == "thread-task-final" else dict(body_markdown="E05 forbidden post"))
        if payload != expected:
            raise RuntimeError("unexpected E05 task payload")
        if action == "thread-task-final":
            payload.update(task_id=binding["task"], session_id=binding["session"], agent=binding["agent"])
        else:
            payload["composer"] = binding["agent"]
        descriptor, name = tempfile.mkstemp(prefix="e05-private-", dir=root / "coord")
        path = Path(name)
        files[action] = path
        with os.fdopen(descriptor, "w") as stream:
            json.dump(payload, stream, sort_keys=True)
        args = (["report-completion"] if action == "thread-task-final" else
                ["threads", "send", "--thread-id", instruction["thread"], "--task-id", binding["task"], "--session-id", binding["session"]])
        args += ["--org", "alpha", "--from-file", str(path)]
    elif action == "thread-task-read":
        if not token or binding["purpose"] != "task_followup" or not re.fullmatch(r"TASK-\d+", instruction["task"]):
            raise RuntimeError("only followup may read dispatched task")
        args = ["details", "--org", "alpha", instruction["task"]]
    else:
        raise RuntimeError("unrecognized finite E05 action")
    entered = time.monotonic()
    end = instruction["execution_deadline"]
    if end <= entered:
        raise RuntimeError("E05 command deadline exhausted")
    argv = [str(callback), *args]
    process = subprocess.Popen(argv, cwd=root, env=dict(os.environ, HAPPYRANCH_DAEMON_HOME=str(root / "client")),
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    child_start = Path(f"/proc/{process.pid}/stat").read_text().rsplit(")", 1)[1].split()[19]
    timed_out = False
    try:
        out, err = process.communicate(timeout=max(0, end - time.monotonic()))
    except subprocess.TimeoutExpired as exc:
        timed_out = True
        out, err = exc.stdout or b"", exc.stderr or b""
    retained = redaction_context(root, token)
    if not secrets <= retained:
        raise RuntimeError("E05 retained redaction context lost identities")
    secrets |= retained

    def sanitize(value: object) -> object:
        if isinstance(value, str):
            for secret in sorted(secrets, key=len, reverse=True):
                value = value.replace(secret, "[REDACTED]")
            return value
        if isinstance(value, dict):
            return {sanitize(k): sanitize(v) for k, v in value.items()}
        if isinstance(value, list):
            return [sanitize(v) for v in value]
        return value

    def public_stream(raw: bytes) -> str:
        # Sanitize ALL captured bytes BEFORE the public cap. A timed-out read
        # may itself end inside a credential, so withhold that suffix too.
        safe = raw
        encoded = [s.encode() for s in sorted(secrets, key=len, reverse=True)]
        if timed_out:
            suffix = max((n for secret in encoded for n in range(1, min(len(secret), len(safe)) + 1)
                          if safe.endswith(secret[:n])), default=0)
            if suffix:
                safe = safe[:-suffix] + b"[REDACTED]"
        for secret in encoded:
            safe = safe.replace(secret, b"[REDACTED]")
        # Drop only an incomplete UTF-8 codepoint at the public byte boundary.
        return safe[:16384].decode(errors="ignore")

    # Raw byte counts describe capture, not the shorter sanitized stream.
    output_truncated = len(out) > 16384 or len(err) > 16384
    return sanitize(dict(action=action, provider=binding, argv=argv, exit=process.returncode,
                             stdout=public_stream(out), stderr=public_stream(err), output_truncated=output_truncated,
                             stdout_bytes=len(out), stderr_bytes=len(err),
                             stdout_truncated=len(out) > 16384, stderr_truncated=len(err) > 16384,
                             payload=payload, entered=entered, finished=time.monotonic(),
                             payload_sha256=hashlib.sha256(path.read_bytes()).hexdigest() if payload is not None else None,
                             child_pid=process.pid, child_start=child_start, timed_out=timed_out,
                             execution_deadline=end, python=sys.executable, source_sha=manifest["revision"],
                             callback_sha256=manifest["callback_sha256"]))


def main() -> None:
    thread, token, agent, org, purpose = sys.argv[1:]
    if not re.fullmatch(r"THR-\d+", thread) or not token or org != "alpha" or agent not in {"case_manager", "case_worker"} or purpose not in {"reply", "task_followup"}:
        raise RuntimeError("unexpected E05 thread invocation")
    os.setsid()
    binding = dict(thread=thread, token_sha256=hashlib.sha256(token.encode()).hexdigest(),
                   agent=agent, org=org, purpose=purpose, pid=os.getpid())
    files: dict[str, Path] = {}
    try:
        with socket.socket(socket.AF_UNIX) as connection:
            connection.settimeout(300)  # Outer failure fence, never an action allowance.
            connection.connect(os.environ["E2E_SOCKET"])
            stream = connection.makefile("rwb")
            stream.write((json.dumps(binding) + "\n").encode())
            stream.flush()
            actions = []
            while True:
                instruction = json.loads(stream.readline(131072))
                if instruction == {"action": "exit"}:
                    if actions not in (["thread-decline"], ["thread-reply", "thread-reply"],
                                       ["thread-dispatch", "thread-reply"], ["thread-task-read", "thread-reply"]):
                        raise RuntimeError("incomplete finite E05 turn")
                    for path in files.values():
                        path.unlink()
                    files.clear()
                    stream.write(b'{"exiting":true}\n')
                    stream.flush()
                    return
                action = instruction["action"]
                allowed = {(): {"thread-decline", "thread-reply", "thread-dispatch", "thread-task-read"},
                           ("thread-reply",): {"thread-reply"}, ("thread-dispatch",): {"thread-reply"},
                           ("thread-task-read",): {"thread-reply"}}
                if action not in allowed.get(tuple(actions), set()):
                    raise RuntimeError("unexpected E05 turn action/order")
                receipt = command(instruction, binding, token, files)
                stream.write((json.dumps(receipt) + "\n").encode())
                stream.flush()
                expected = 1 if actions == ["thread-reply"] else 0
                if receipt["exit"] != expected or receipt["timed_out"] or receipt["output_truncated"]:
                    raise RuntimeError("E05 real CLI failed")
                actions.append(action)
    finally:
        for path in files.values():
            path.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
