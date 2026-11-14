"""Read-only, current-user observation of the shipped Swift listener port."""
from __future__ import annotations

import ipaddress
import os
import selectors
import subprocess
import time

MESSAGE = "macOS Swift HomeConnector port 8443 has a non-loopback listener; stop it explicitly before enabling the managed connector"
UNAVAILABLE = "macOS Swift HomeConnector listener inventory unavailable; managed connector admission refused"


def classify_inventory(code: int, output: str, uid: int) -> str:
    """lsof -Fpun output only; never return process names, addresses or raw errors."""
    if code == 1 and not output:
        return "clear"  # lsof's documented no-match result
    if code != 0 or not output:
        return "unavailable"
    owner = None
    process = False
    listeners = 0
    process_listeners = 0
    conflict = False
    try:
        for line in output.splitlines():
            field, value = line[:1], line[1:]
            if field == "p" and value.isdecimal() and int(value) > 0:
                if process and not process_listeners:
                    return "unavailable"
                process, owner, process_listeners = True, None, 0
            elif field == "u" and process and owner is None and value.isdecimal() and int(value) == uid:
                owner = uid
            elif field == "n" and owner == uid:
                host, port = value.rsplit(":", 1)
                if port != "8443":
                    return "unavailable"
                listeners += 1
                process_listeners += 1
                if host == "*":
                    conflict = True
                else:
                    address = ipaddress.ip_address(host.removeprefix("[").removesuffix("]"))
                    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
                        address = address.ipv4_mapped
                    conflict |= not address.is_loopback
            else:
                return "unavailable"
    except ValueError:
        return "unavailable"
    return ("conflict" if conflict else "clear") if listeners and process_listeners else "unavailable"


def observe() -> dict[str, str]:
    """Bound time/output; any diagnostic, failed or incomplete inventory refuses."""
    child = None
    raw = bytearray()
    state = "unavailable"
    try:
        child = subprocess.Popen(
            ["/usr/sbin/lsof", "-nP", "-a", "-u", str(os.getuid()),
             "-iTCP:8443", "-sTCP:LISTEN", "-Fpun"],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        deadline = time.monotonic() + 5
        with selectors.DefaultSelector() as selector:
            selector.register(child.stdout, selectors.EVENT_READ, "stdout")
            selector.register(child.stderr, selectors.EVENT_READ, "stderr")
            while selector.get_map():
                if time.monotonic() >= deadline:
                    raise TimeoutError
                for key, _ in selector.select(min(.1, deadline - time.monotonic())):
                    block = os.read(key.fileobj.fileno(), 4096)
                    if not block:
                        selector.unregister(key.fileobj)
                    elif key.data == "stderr" or len(raw) + len(block) > 65536:
                        raise ValueError
                    else:
                        raw.extend(block)
        code = child.wait(timeout=max(.001, deadline - time.monotonic()))
        state = classify_inventory(code, raw.decode("utf-8"), os.getuid())
    except (OSError, ValueError, TimeoutError, subprocess.TimeoutExpired):
        pass
    finally:
        if child is not None:
            if child.poll() is None:
                child.kill()
            child.wait()
            child.stdout.close()
            child.stderr.close()
    return {"state": state, "message": MESSAGE if state == "conflict" else UNAVAILABLE if state == "unavailable" else ""}
