"""Runner-only PID/start tracking, including children outside the daemon PGID."""
from __future__ import annotations

import ctypes
import os
import signal
import subprocess
import threading
import time
from pathlib import Path


def identity(pid: int) -> str | None:
    try:
        # comm may contain spaces or parentheses; fields after the final ')' start at 3.
        return Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[19]
    except FileNotFoundError:
        return None


class Owned:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.pids: dict[int, str] = {}
        self.units: set[str] = set()
        self.groups: set[str] = set()
        self.providers: set[int] = set()
        self.lock = threading.RLock()
        self.stop = threading.Event()
        self.errors: list[str] = []
        if ctypes.CDLL(None, use_errno=True).prctl(36, 1, 0, 0, 0) != 0:
            raise RuntimeError("runner subreaper unavailable")
        descriptor = os.pidfd_open(os.getpid())
        try:
            signal.pidfd_send_signal(descriptor, 0)
        finally:
            os.close(descriptor)
        self.thread = threading.Thread(target=self.observe, daemon=True)
        self.thread.start()

    def add(self, pid: int) -> None:
        start = identity(pid)
        if start is not None:
            with self.lock:
                self.pids.setdefault(pid, start)

    def register_provider(self, pid: int) -> dict:
        # A socket peer is not authority to signal arbitrary processes. Verify
        # the private run marker on that exact PID and each admitted ancestor.
        first = pid
        while pid != os.getpid() and pid > 1:
            env = Path(f"/proc/{pid}/environ").read_bytes().split(b"\0")
            if f"E2E_ROOT={self.root}".encode() not in env:
                break
            self.add(pid)
            stat = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
            pid = int(stat[1])
        if first not in self.pids:
            raise RuntimeError("unowned provider PID")
        self.providers.add(first)
        cgroup = Path(f"/proc/{first}/cgroup").read_text()
        for line in cgroup.splitlines():
            if line.startswith("0::"):
                path = line[3:]
                # Only a production-created per-session unit is ours. Never
                # act on runner/user-manager/ambient cgroups.
                leaf = path.rsplit("/", 1)[-1]
                if leaf.startswith("happyranch-") and leaf.endswith((".service", ".scope")):
                    self.units.add(leaf)
                    self.groups.add(path)
        return dict(pid=first, start=self.pids[first], pgid=os.getpgid(first), cgroup=cgroup)

    def sample(self) -> None:
        with self.lock:
            provider_file = self.root / "owned.json"
            if provider_file.exists():
                import json
                for pid in json.loads(provider_file.read_text()):
                    if pid not in self.providers and identity(pid) is not None:
                        self.register_provider(pid)
            pending = [os.getpid(), *self.pids]
            seen: set[int] = set()
            while pending:
                pid = pending.pop()
                if pid in seen:
                    continue
                seen.add(pid)
                if pid != os.getpid() and identity(pid) != self.pids[pid]:
                    continue
                try:
                    for task in Path(f"/proc/{pid}/task").iterdir():
                        for child in (task / "children").read_text().split():
                            child_pid = int(child)
                            self.add(child_pid)
                            pending.append(child_pid)
                except FileNotFoundError:
                    continue
            for group in self.groups:
                directory = Path("/sys/fs/cgroup") / group.lstrip("/")
                if directory.exists():
                    for entry in [directory, *directory.rglob("*")]:
                        if entry.is_dir() and (entry / "cgroup.procs").exists():
                            for pid in (entry / "cgroup.procs").read_text().split():
                                self.add(int(pid))

    def observe(self) -> None:
        while not self.stop.wait(0.02):
            try:
                self.sample()
            except Exception as exc:
                self.errors.append(repr(exc))
                return

    def signal(self, pid: int, sig: int) -> None:
        if identity(pid) != self.pids.get(pid):
            return
        try:
            fd = os.pidfd_open(pid)
            try:
                if identity(pid) == self.pids[pid]:
                    signal.pidfd_send_signal(fd, sig)
            finally:
                os.close(fd)
        except ProcessLookupError:
            pass

    def cleanup(self) -> dict:
        started = time.monotonic()
        self.sample()
        for sig, seconds in ((signal.SIGTERM, 8), (signal.SIGKILL, 5)):
            for pid in list(self.pids):
                self.signal(pid, sig)
            until = time.monotonic() + seconds
            while time.monotonic() < until:
                try:
                    while os.waitpid(-1, os.WNOHANG)[0]:
                        pass
                except ChildProcessError:
                    pass
                self.sample()
                if not any(identity(pid) == start for pid, start in self.pids.items()):
                    break
                self.stop.wait(0.05)
        self.stop.set()
        self.thread.join(timeout=2)
        units = {}
        unit_env = {key: os.environ[key] for key in ("XDG_RUNTIME_DIR", "DBUS_SESSION_BUS_ADDRESS") if key in os.environ}
        unit_env.update(PATH="/usr/bin:/bin", LANG="C.UTF-8")
        for unit in sorted(self.units):
            r = subprocess.run(["/usr/bin/systemctl", "--user", "show", unit, "--property=LoadState,ActiveState"],
                               capture_output=True, text=True, timeout=3, env=unit_env)
            units[unit] = dict(exit=r.returncode, stdout=r.stdout, stderr=r.stderr)
        survivors = {pid: start for pid, start in self.pids.items() if identity(pid) == start}
        groups = {group: (Path("/sys/fs/cgroup") / group.lstrip("/")).exists() for group in self.groups}
        absent = not survivors and not any(groups.values()) and all(
            "LoadState=not-found" in row["stdout"] for row in units.values())
        return dict(ok=absent and not self.errors, survivors=survivors, units=units, groups=groups,
                    errors=self.errors, identities=self.pids, seconds=time.monotonic() - started)
