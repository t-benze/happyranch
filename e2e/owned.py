"""Runner-only PID/start tracking, including children outside the daemon PGID."""
from __future__ import annotations

import ctypes
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
        # This launcher is the
        # sole consuming owner of adopted children; Popen owns direct children.
        self.launcher = (os.getpid(), identity(os.getpid()))
        if self.launcher[1] is None:
            raise RuntimeError("launcher generation unavailable")
        self.direct: dict[int, str | None] = {}
        self.processes: list[subprocess.Popen] = []
        self.creation_unknown = False
        self.reap_claims: dict[tuple[int, str], dict | None] = {}
        self.stop = threading.Event()
        self.errors: list[str] = []
        self.reaped: list[dict] = []
        self.collisions: list[dict] = []
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
        with self.lock:
            start = identity(pid)
            if start is not None:
                previous = self.pids.get(pid)
                if previous is not None and previous != start:
                    self.collisions.append(dict(pid=pid, previous=previous, observed=start, at=time.monotonic()))
                    raise RuntimeError("owned PID number reused")
                self.pids[pid] = start

    def popen(self, *args, **kwargs) -> subprocess.Popen:
        # The lock covers creation itself, not just registration afterward.
        # A watcher cannot discover a direct child in an unexcluded window.
        with self.lock:
            if self.creation_unknown:
                raise RuntimeError("prior direct creation ownership unavailable")
            # Sticky BEFORE Popen: an asynchronous exception anywhere before
            # complete registration leaves unknown custody, never a reap grant.
            self.creation_unknown = True
            process = subprocess.Popen(*args, **kwargs)
            self.processes.append(process)  # Retain status ownership on errors.
            self.direct[process.pid] = None  # Unknown generation excludes too.
            self.add(process.pid)
            self.direct[process.pid] = self.pids.get(process.pid)
            self.creation_unknown = False
            return process

    def reap(self) -> None:
        """Consume only registered, exited, launcher-adopted generations."""
        with self.lock:
            launcher, launcher_start = self.launcher
            if os.getpid() != launcher or identity(launcher) != launcher_start:
                raise RuntimeError("launcher generation changed")
            if self.creation_unknown:
                return
            for pid, start in list(self.pids.items()):
                if pid in self.direct or (pid, start) in self.reap_claims:
                    continue
                def eligible() -> bool:
                    try:
                        fields = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
                    except (FileNotFoundError, ProcessLookupError):
                        return False
                    return (fields[19] == start and int(fields[1]) == launcher
                            and fields[0] in {"Z", "X", "x"}
                            and identity(launcher) == launcher_start
                            and self.pids.get(pid) == start and pid not in self.direct)
                if not eligible():
                    continue
                try:
                    descriptor = os.pidfd_open(pid)
                except ProcessLookupError:
                    continue
                try:
                    if not eligible():
                        continue
                    observed = os.waitid(os.P_PIDFD, descriptor, os.WEXITED | os.WNOHANG | os.WNOWAIT)
                    if (observed is None or observed.si_pid != pid or observed.si_signo != signal.SIGCHLD
                            or observed.si_code not in (os.CLD_EXITED, os.CLD_KILLED, os.CLD_DUMPED)
                            or type(observed.si_status) is not int
                            or not 0 <= observed.si_status <= (255 if observed.si_code == os.CLD_EXITED else 127)):
                        continue
                    if not eligible():
                        continue
                    # One actual consuming call on the SAME pinned descriptor.
                    self.reap_claims[(pid, start)] = None
                    result = os.waitid(os.P_PIDFD, descriptor, os.WEXITED | os.WNOHANG)
                    if result is None:
                        raise RuntimeError("eligible child status unavailable")
                    receipt = dict(pid=pid, generation=start, launcher=launcher,
                                   launcher_generation=launcher_start, code=result.si_code,
                                   status=result.si_status, exit=(result.si_status if result.si_code == os.CLD_EXITED
                                                                else -result.si_status),
                                   owner="launcher-pidfd", absent=False)
                    self.reaped.append(receipt)
                    self.reap_claims[(pid, start)] = receipt
                    if result != observed or identity(pid) == start:
                        raise RuntimeError("consuming child status/disappearance mismatch")
                    receipt["absent"] = True
                except ChildProcessError:
                    # Daemon/foreign children are never wait authority. ECHILD
                    # is refusal, not a fabricated consumed status receipt.
                    continue
                finally:
                    os.close(descriptor)

    def register_provider(self, pid: int) -> dict:
        # A socket peer is not authority to signal arbitrary processes. New
        # generations need the private marker; an already owned generation
        # retains its independent Popen/descendant/marker proof across exec.
        first = pid
        while pid != os.getpid() and pid > 1:
            stat = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
            start = stat[19]
            prior = self.pids.get(pid)
            if prior is not None:
                if prior != start:
                    self.add(pid)  # Record the collision; never adopt its replacement.
                    raise RuntimeError("provider generation changed")
                break
            env = Path(f"/proc/{pid}/environ").read_bytes().split(b"\0")
            if identity(pid) != start:
                raise RuntimeError("provider generation changed during marker read")
            if f"E2E_ROOT={self.root}".encode() not in env:
                break
            # Store the generation whose marker was read, never a later PID user.
            self.pids[pid] = start
            pid = int(stat[1])
        if first not in self.pids:
            raise RuntimeError("unowned provider PID")
        start = self.pids[first]
        if identity(first) != start:
            raise RuntimeError("provider generation changed before cgroup read")
        cgroup = Path(f"/proc/{first}/cgroup").read_text()
        pgid = os.getpgid(first)
        if identity(first) != start:
            raise RuntimeError("provider generation changed during cgroup read")
        self.providers.add(first)
        for line in cgroup.splitlines():
            if line.startswith("0::"):
                path = line[3:]
                # Only a production-created per-session unit is ours. Never
                # act on runner/user-manager/ambient cgroups.
                leaf = path.rsplit("/", 1)[-1]
                if leaf.startswith("happyranch-") and leaf.endswith((".service", ".scope")):
                    self.units.add(leaf)
                    self.groups.add(path)
        return dict(pid=first, start=start, pgid=pgid, cgroup=cgroup)

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
            # Reap only independently registered, generation/custody-qualified children.
            self.reap()
    def observe(self) -> None:
        while not self.stop.wait(0.02):
            try:
                self.sample()
            except Exception as exc:
                self.errors.append(f"{exc!r}\n{traceback.format_exc()}")
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
                with self.lock:
                    self.reap()
                    # These objects are the exclusive consuming owners of every
                    # direct command, including interrupted setup/controller.
                    for process in self.processes:
                        process.poll()
                self.sample()
                with self.lock:
                    observed = dict(self.pids)
                if not any(identity(pid) == start for pid, start in observed.items()):
                    break
                self.stop.wait(0.05)
        self.stop.set()
        self.thread.join(timeout=2)
        if self.thread.is_alive():
            # No copy/publication of live watcher memory and no new child.
            return dict(ok=False, watcher_stopped=False, errors=["owned watcher stop unavailable"],
                        seconds=time.monotonic() - started)
        units = {}
        unit_env = {key: os.environ[key] for key in ("XDG_RUNTIME_DIR", "DBUS_SESSION_BUS_ADDRESS") if key in os.environ}
        unit_env.update(PATH="/usr/bin:/bin", LANG="C.UTF-8")
        for unit in sorted(self.units):
            process = self.popen(["/usr/bin/systemctl", "--user", "show", unit, "--property=LoadState,ActiveState"],
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=unit_env)
            try:
                stdout, stderr = process.communicate(timeout=3)
            except BaseException:
                process.kill()
                process.wait()
                raise
            units[unit] = dict(exit=process.returncode, stdout=stdout, stderr=stderr)
        survivors = {pid: start for pid, start in self.pids.items() if identity(pid) == start}
        groups = {group: (Path("/sys/fs/cgroup") / group.lstrip("/")).exists() for group in self.groups}
        absent = not survivors and not any(groups.values()) and all(
            "LoadState=not-found" in row["stdout"] for row in units.values())
        waits_complete = all(row is not None and row["absent"] for row in self.reap_claims.values())
        return dict(ok=absent and waits_complete and not self.creation_unknown and not self.errors and not self.thread.is_alive(),
                    creation_unknown=self.creation_unknown, survivors=survivors, units=units, groups=groups,
                    unconfirmed_reaps=[dict(pid=pid, generation=start) for (pid, start), row in self.reap_claims.items()
                                       if row is None or not row["absent"]],
                    errors=self.errors, identities=self.pids, reaped=self.reaped,
                    popen_returns=[dict(pid=p.pid, generation=self.direct[p.pid], exit=p.returncode) for p in self.processes],
                    collisions=self.collisions, watcher_stopped=not self.thread.is_alive(), seconds=time.monotonic() - started)
