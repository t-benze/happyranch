"""Finite external executable protocol; no product imports or decisions."""
from __future__ import annotations

import json
import os
import socket
import sys


def main() -> None:
    task, session, agent, org = sys.argv[1:]
    if org not in {"alpha", "beta"} or agent not in {"case_manager", "case_worker"}:
        raise RuntimeError("unexpected external invocation")
    with socket.socket(socket.AF_UNIX) as connection:
        connection.settimeout(300)
        connection.connect(os.environ["E2E_SOCKET"])
        stream = connection.makefile("rwb")
        row = dict(task=task, session=session, agent=agent, org=org, pid=os.getpid())
        stream.write((json.dumps(row) + "\n").encode())
        stream.flush()
        # The controller runs the real CLI while this actual provider stays live.
        # Only its exit is controlled. The daemon consumes the callback normally.
        if json.loads(stream.readline()) != {"action": "exit"}:
            raise RuntimeError("unexpected provider instruction")
        stream.write(b'{"exiting":true}\n')
        stream.flush()


if __name__ == "__main__":
    main()
