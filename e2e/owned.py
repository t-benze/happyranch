"""Runner-only PID/start tracking, including children outside the daemon PGID."""
from __future__ import annotations

import ctypes
import json
import os
import signal
import subprocess
import threading
import time
import traceback
from pathlib import Path


def identity(pid: int) -> str | None:
    try:
        # comm may contain spaces or parentheses; fields after the final ')' start at 3.
        return Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[19]
    except (FileNotFoundError, ProcessLookupError):
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
        self.reaped: list[dict] = []
        self.collisions: list[dict] = []
        self.restart_request = None
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
                previous = self.pids.get(pid)
                if previous is not None and previous != start:
                    self.collisions.append(dict(pid=pid, previous=previous, observed=start, at=time.monotonic()))
                    raise RuntimeError("owned PID number reused")
                self.pids[pid] = start

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
            for pid,start in list(self.pids.items()):
                observed = identity(pid)
                if observed is not None and observed != start:
                    self.add(pid)  # Preserve the collision and fail before any signal.
            provider_file = self.root / "owned.json"
            if provider_file.exists():
                import json
                for pid in json.loads(provider_file.read_text()):
                    start = identity(pid)
                    if pid not in self.providers and start is not None:
                        try:
                            self.register_provider(pid)
                        except (FileNotFoundError, ProcessLookupError):
                            # A just-exited provider may vanish between proc
                            # reads. Only proven disappearance is benign.
                            if identity(pid) == start:
                                raise
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
                            if child_pid in self.pids:
                                pending.append(child_pid)
                except (FileNotFoundError, ProcessLookupError):
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
                self.restart_close()
            except Exception as exc:
                self.errors.append(f"{exc!r}\n{traceback.format_exc()}")
                return

    def restart_close(self) -> None:
        """One R-only request, targeted adopted-child waits; never reap controller.

        The controller supplied complete live identity records before B death.
        Every signal checks this runner's cumulative ownership and pidfd identity.
        ECHILD is an observation of another parent, never a claimed wait status.
        """
        path = self.root / "coord/r-close-request.json"
        if not path.exists():
            return
        request = json.loads(path.read_text())
        destination = self.root / "coord/r-close-receipt.json"
        if self.restart_request is not None:
            if request != self.restart_request:
                raise RuntimeError("R close request changed")
            return
        self.restart_request = request
        record = dict(request=request, owner_pid=os.getpid(), owner_start=identity(os.getpid()),
                      entered=time.monotonic(), processes=[], signals=[], waits=[], samples=[], ok=False)
        def save():
            temporary = destination.with_suffix(".tmp")
            temporary.write_text(json.dumps(record))
            temporary.replace(destination)
        save()
        try:
            if set(request) != {"action", "processes", "daemon", "controller", "term_end", "end"} or request["action"] != "E07-R-only-close":
                raise RuntimeError("invalid R close request")
            rows = request["processes"]
            if not rows or len({r["pid"] for r in rows}) != len(rows):
                raise RuntimeError("ambiguous R close identities")
            if not time.monotonic() < request["term_end"] < request["end"]:
                raise TimeoutError("R closure not admitted")
            forbidden = {os.getpid(), request["controller"]["pid"], request["daemon"]["pid"]}
            for row in rows:
                pid = row["pid"]
                if pid in forbidden or self.pids.get(pid) != row["start"]:
                    raise RuntimeError("R close target not previously owned")
                observed = identity(pid)
                if observed is not None and observed != row["start"]:
                    raise RuntimeError("R close numeric PID reused")
                if observed is not None:
                    env = Path(f"/proc/{pid}/environ").read_bytes().split(b"\0")
                    # A zombie no longer has readable environment bytes; its
                    # authenticated pre-death identity is still checked above.
                    stat = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
                    if stat[0] != "Z" and f"E2E_ROOT={self.root}".encode() not in env:
                        raise RuntimeError("R target ownership marker lost")
                record["processes"].append(dict(row, observed_start=observed))
            sent = set()
            while time.monotonic() < request["end"]:
                sig = signal.SIGTERM if time.monotonic() < request["term_end"] else signal.SIGKILL
                sample = dict(at=time.monotonic(), identities=[])
                for row in rows:
                    pid, start = row["pid"], row["start"]
                    observed = identity(pid)
                    if observed is not None and observed != start:
                        raise RuntimeError("R target PID reused during closure")
                    if observed is not None:
                        stat = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
                        parent = int(stat[1])
                        if (pid, sig) not in sent:
                            self.signal(pid, sig)
                            sent.add((pid, sig))
                            record["signals"].append(dict(pid=pid, start=start, signal=int(sig), parent=parent, at=time.monotonic()))
                        try:
                            waited, status = os.waitpid(pid, os.WNOHANG)
                        except ChildProcessError:
                            record["waits"].append(dict(pid=pid, parent=parent, outcome="ECHILD", at=time.monotonic()))
                        else:
                            if waited:
                                if parent != os.getpid():
                                    raise RuntimeError("R wait parent attribution changed")
                                receipt = dict(pid=pid, start=start, parent=parent, wait_status=status,
                                               exit=os.waitstatus_to_exitcode(status), outcome="reaped", at=time.monotonic())
                                record["waits"].append(receipt)
                                self.reaped.append(receipt)
                    sample["identities"].append(dict(pid=pid, observed_start=identity(pid)))
                record["samples"].append(sample)
                save()
                if all(row["observed_start"] is None for row in sample["identities"]):
                    record.update(ok=True, finished=time.monotonic())
                    save()
                    return
                self.stop.wait(min(.02, max(0, request["end"]-time.monotonic())))
            raise TimeoutError("R targeted closure deadline")
        except Exception as exc:
            record.update(error=type(exc).__name__, finished=time.monotonic())
            save()
            raise

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
                    while True:
                        pid, status = os.waitpid(-1, os.WNOHANG)
                        if not pid:
                            break
                        self.reaped.append(dict(pid=pid, wait_status=status, exit=os.waitstatus_to_exitcode(status)))
                except ChildProcessError:
                    pass
                self.sample()
                with self.lock:
                    observed = dict(self.pids)
                if not any(identity(pid) == start for pid, start in observed.items()):
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
        return dict(ok=absent and not self.errors and not self.thread.is_alive(), survivors=survivors, units=units, groups=groups,
                    errors=self.errors, identities=self.pids, reaped=self.reaped, collisions=self.collisions, watcher_stopped=not self.thread.is_alive(), seconds=time.monotonic() - started)
