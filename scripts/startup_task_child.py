#!/usr/bin/env python3
"""Harmless executable for the existing finite startup verifier, never a provider.

Only the shipping executor/host launches this process. It consumes its actual
prompt, writes its own PID marker and reports its OWN fixture callback to the
original fixture service. It supplies no host receipt or recovery authority.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import sys
import time
import urllib.request


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--binding", type=Path, required=True)
    args, executor_args = parser.parse_known_args()
    if "-p" not in executor_args or executor_args[-2:] != ["--output-format", "json"]:
        raise ValueError("only the original ordinary Claude argv is selected")
    binding = json.loads(args.binding.read_bytes())
    root = Path(binding["root"])
    if (root.resolve() != root or not root.is_absolute()
            or not any(p.name == "task-tmp" and p.parent.name == ".happyranch" for p in root.parents)
            or args.binding != root / "native" / "binding.json"
            or os.environ.get("HAPPYRANCH_DAEMON_HOME") != str(root / "daemon")):
        raise ValueError("isolated original fixture binding required")
    prompt = sys.stdin.read(1024 * 1024 + 1)
    if len(prompt) > 1024 * 1024:
        raise ValueError("finite prompt cap")
    task = re.search(r"^\s*task_id: (TASK-\d+)\s*$", prompt, re.MULTILINE)
    session = re.search(r"^\s*session_id: ([a-zA-Z0-9-]+)\s*$", prompt, re.MULTILINE)
    agent = re.search(r"^You are ([a-z_]+)\. Use the start-task skill", prompt, re.MULTILINE)
    workspace = Path.cwd()
    org = workspace.parent.parent.name
    if (task is None or session is None or agent is None
            or workspace != root / "runtime" / "orgs" / org / "workspaces" / agent[1]
            or binding["tasks"].get(org + "/" + task[1]) != agent[1]):
        raise ValueError("unselected task/agent/workspace/prompt identity")
    marker = {"org": org, "task": task[1], "agent": agent[1], "session": session[1],
              "pid": os.getpid(), "ppid": os.getppid(), "monotonic": time.monotonic()}
    path = root / "native" / (org + "-" + task[1] + "-" + session[1] + ".json")
    with path.open("x") as stream:
        json.dump(marker, stream, sort_keys=True)
    # The original mounted callback owns session authentication, INTEGER result
    # admission and subsequent task settlement. This child never writes a DB.
    body = {"session_id": session[1], "agent": agent[1], "status": "completed",
            "confidence": 100, "output_summary": "Owned harmless startup child"}
    if agent[1] == "case_manager":
        body["decision"] = {"action": "done", "summary": "Owned harmless startup child"}
    request = urllib.request.Request(
        f"http://127.0.0.1:{binding['port']}/api/v1/orgs/{org}/tasks/{task[1]}/completion",
        data=json.dumps(body).encode(), headers={"Authorization": "Bearer " + binding["token"],
                                               "Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=2) as response:
        if response.status != 200 or len(response.read(16385)) > 16384:
            raise ValueError("original own-session callback not accepted")
    print(json.dumps({"type": "result", "result": "Owned harmless startup child"}))


if __name__ == "__main__":
    main()
