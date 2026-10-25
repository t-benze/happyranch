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
        self.stop = threading.Event()
        self.errors: list[str] = []
        self.reaped: list[dict] = []
        self.collisions: list[dict] = []
        # Optional baseline diagnostics. No slot is armed by construction, and
        # none of this memory supplies containment, signal or wait authority.
        self.evidence_main = threading.current_thread()
        self.evidence_binding = None
        self.evidence_phase = None
        self.evidence_original_phase = None
        self.evidence_terminal = False
        self.evidence_phase_pid = None
        self.evidence_active_open = False
        self.evidence_entry = None
        self.evidence_entry_consumed = False
        self.evidence_writes_ended = False
        self.evidence_budget = dict(clocks=0, stats=0, cycles=0, tokens=0)
        self.evidence_fd_clear = True
        self.evidence_slots = {}
        for name in ("active", "precleanup"):
            self.evidence_slots[name] = dict(
                state="unattempted", reason="unreached", owner=None, entered=None,
                finished=None, deadline=None, join="unknown", launcher_a=None,
                launcher_b=None, controller_a=None, controller_b=None, targets=None)
        self.evidence_returns = dict(state="unattempted", reason="unreached",
                                     outcome="unreached", rows=None, coverage="none")
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
            # Harvest only the ORIGINAL traversal below. A failed traversal is
            # never repaired by another sample or a first-17 projection.
            evidence = self.evidence_binding
            phase = self.evidence_phase
            budget = self.evidence_budget
            who = threading.current_thread()
            selected = None
            children = []
            token_steps = 0
            traversal_complete = True
            traversal_reason = "incomplete"
            cycle = False
            entry = self.evidence_entry
            if evidence is not None and not self.evidence_writes_ended:
                if (who is self.evidence_main and entry is not None
                        and not self.evidence_entry_consumed):
                    self.evidence_entry_consumed = True  # Original entry only.
                    selected = "precleanup"
                elif (who is self.evidence_main and self.evidence_entry_consumed
                      and self.evidence_slots["precleanup"]["state"] == "claimed"):
                    self.evidence_slots["precleanup"].update(state="refused", reason="reentry")
                elif (who is self.thread and self.evidence_active_open
                      and self.evidence_slots["active"]["state"] in ("unattempted", "claimed")):
                    cycle = budget["cycles"] < 24000
                    if cycle:
                        budget["cycles"] += 1
                    elif self.evidence_slots["active"]["state"] == "unattempted":
                        self.evidence_slots["active"].update(state="refused", reason="cycle_limit", owner="watcher")
                if selected is not None:
                    self.evidence_slots[selected].update(state="claimed", reason="incomplete", owner="main")
                elif cycle and self.evidence_slots["active"]["state"] == "claimed":
                    self.evidence_slots["active"].update(state="refused", reason="reentry")
                    cycle = False
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
                    traversal_complete = False
                    continue
                try:
                    for task in Path(f"/proc/{pid}/task").iterdir():
                        for child in (task / "children").read_text().split():
                            child_pid = int(child)
                            if (evidence is not None and not self.evidence_writes_ended
                                    and pid == evidence["launcher"]["pid"] and (cycle or selected is not None)):
                                if token_steps < 18 and budget["tokens"] < 432000:
                                    token_steps += 1
                                    budget["tokens"] += 1
                                    if not 0 < child_pid < 2**64:
                                        traversal_complete = False
                                        traversal_reason = "malformed"
                                    elif child_pid not in children:
                                        children.append(child_pid)
                                    if (cycle and selected is None and self.evidence_active_open
                                            and self.evidence_slots["active"]["state"] == "unattempted"
                                            and child_pid != self.evidence_phase_pid and child_pid in self.pids):
                                        selected = "active"
                                        self.evidence_slots[selected].update(
                                            state="claimed", reason="incomplete", owner="watcher")
                                else:
                                    traversal_complete = False
                                    traversal_reason = "overflow" if token_steps == 18 else "no_budget"
                            self.add(child_pid)
                            if child_pid in self.pids:
                                pending.append(child_pid)
                except (FileNotFoundError, ProcessLookupError):
                    traversal_complete = False
                    continue
            for group in self.groups:
                directory = Path("/sys/fs/cgroup") / group.lstrip("/")
                if directory.exists():
                    for entry in [directory, *directory.rglob("*")]:
                        if entry.is_dir() and (entry / "cgroup.procs").exists():
                            for pid in (entry / "cgroup.procs").read_text().split():
                                self.add(int(pid))
            if selected is None:
                return
            slot = self.evidence_slots[selected]
            # Claim is already consumed, before any optional I/O or clock.
            reason = "incomplete"
            try:
                end = self.evidence_entry[1] if selected == "precleanup" else phase["deadline"] if phase else None
                if budget["clocks"] >= 512:
                    reason = "no_budget"
                    return
                budget["clocks"] += 1
                entered = time.monotonic()
                if type(end) not in (int, float) or not 0 <= entered < end <= 9007199254740991:
                    reason = "no_budget"
                    return
                if selected == "precleanup":
                    entered = self.evidence_entry[0]
                slot.update(entered=entered, deadline=end)
                if (evidence is not self.evidence_binding or phase is not self.evidence_phase
                        or evidence["state"] != "bound" or phase is None
                        or phase["allocation_state"] != "bound" or self.evidence_phase_pid is None):
                    reason = "unbound"
                    slot["join"] = "never_bound"
                    return
                if phase["outcome"] == "malformed":
                    slot["join"] = reason = "malformed"
                    return
                expected_owner = self.evidence_main if selected == "precleanup" else self.thread
                if who is not expected_owner or os.getpid() != evidence["launcher"]["pid"]:
                    reason = "off_owner"
                    return
                if not traversal_complete:
                    reason = traversal_reason
                    return
                if len(children) > 17:
                    reason = "overflow"
                    return
                if selected == "active" and not self.evidence_active_open:
                    reason = "unbound"
                    return
                targets = []
                for child_pid in children:
                    registered = self.pids.get(child_pid)
                    if registered is not None and not (type(registered) is str and 1 <= len(registered) <= 20
                                                        and registered[0] in "123456789" and all(c in "0123456789" for c in registered)):
                        reason = "malformed"
                        return
                    kind = ("direct_phase" if registered == phase["controller"]["start"] else "phase_number_excluded") if child_pid == self.evidence_phase_pid else "nonphase"
                    targets.append(dict(pid=child_pid, start=registered, kind=kind,
                                        registration="unregistered" if registered is None else "matched",
                                        a=None, b=None, close="not_opened",
                                        wait=dict(state="unattempted", reason="excluded" if kind != "nonphase" else "not_registered" if registered is None else "unreached",
                                                  outcome="unknown", at=None, code=None, status=None)))
                context = {}
                # This is work on the finite harvested set, not a second tree
                # traversal. Context brackets enclose EVERY target observation.
                reads = [("launcher_a", evidence["launcher"]["pid"], None, None),
                         ("controller_a", phase["controller"]["pid"], None, None)]
                for index, target in enumerate(targets):
                    reads.extend(((None, target["pid"], index, "a"), (None, target["pid"], index, "b")))
                reads.extend((("controller_b", phase["controller"]["pid"], None, None),
                              ("launcher_b", evidence["launcher"]["pid"], None, None)))
                for key, read_pid, index, side in reads:
                    if budget["clocks"] >= 512 or budget["stats"] >= 80:
                        reason = "no_budget"
                        return
                    budget["clocks"] += 1
                    at = time.monotonic()
                    if not entered <= at < end:
                        reason = "no_budget"
                        return
                    budget["stats"] += 1
                    observed = dict(outcome="unattempted", at=at, start=None, state=None, ppid=None)
                    try:
                        with open(f"/proc/{read_pid}/stat", "rb", buffering=0) as stream:
                            raw = stream.read(4097)
                    except TimeoutError:
                        raise
                    except (FileNotFoundError, ProcessLookupError):
                        observed["outcome"] = "absent"
                    except InterruptedError:
                        observed["outcome"] = "interrupted"
                    except OSError:
                        observed["outcome"] = "unreadable"
                    else:
                        observed["outcome"] = "malformed"
                        if not raw:
                            observed["outcome"] = "empty"
                        elif len(raw) > 4096:
                            observed["outcome"] = "oversized"
                        elif not raw.endswith(b"\n"):
                            observed["outcome"] = "partial"
                        elif raw.startswith(str(read_pid).encode("ascii") + b" (") and b") " in raw:
                            tail = raw.rsplit(b") ", 1)[1].split()
                            if (len(tail) >= 20 and tail[0] in (b"R", b"S", b"D", b"Z", b"T", b"t", b"W", b"X", b"x", b"K", b"P", b"I")
                                    and 1 <= len(tail[1]) <= 20 and all(48 <= c <= 57 for c in tail[1])
                                    and int(tail[1]) < 2**64
                                    and 1 <= len(tail[19]) <= 20 and 49 <= tail[19][0] <= 57
                                    and all(48 <= c <= 57 for c in tail[19]) and int(tail[19]) < 2**64):
                                observed.update(outcome="observed", start=tail[19].decode("ascii"),
                                                state=tail[0].decode("ascii"), ppid=int(tail[1]))
                    if key is not None:
                        context[key] = observed
                        if key == "launcher_a" and (observed["outcome"] != "observed" or observed["start"] != evidence["launcher"]["start"]):
                            reason = "reused" if observed["outcome"] == "observed" else "absent" if observed["outcome"] == "absent" else "malformed"
                            return
                        continue
                    target = targets[index]
                    target[side] = observed
                    wait = target["wait"]
                    matched = (observed["outcome"] == "observed" and observed["start"] == target["start"]
                               and observed["ppid"] == evidence["launcher"]["pid"])
                    if observed["outcome"] == "observed" and target["start"] is not None and observed["start"] != target["start"]:
                        target["registration"] = "reused"
                    if side == "b":
                        if wait["state"] == "complete" and not matched:
                            wait.update(state="refused", reason="reused" if target["registration"] == "reused" else "mismatch",
                                        outcome="unknown", code=None, status=None)
                        continue
                    # Exclusion is numerical and permanent, even on unbound,
                    # reused or absent phase generations. No Popen status here.
                    if target["pid"] == self.evidence_phase_pid or target["start"] is None:
                        continue
                    if not matched:
                        wait.update(state="refused", reason="reused" if target["registration"] == "reused" else "mismatch")
                        continue
                    if not self.evidence_fd_clear:
                        wait["reason"] = "close_unknown"
                        continue
                    if not all(hasattr(os, name) for name in ("pidfd_open", "waitid", "P_PIDFD", "WEXITED", "WNOHANG", "WNOWAIT", "CLD_EXITED", "CLD_KILLED", "CLD_DUMPED")):
                        wait.update(state="refused", reason="unsupported")
                        continue
                    if budget["clocks"] >= 512:
                        reason = "no_budget"
                        return
                    budget["clocks"] += 1
                    wait_at = time.monotonic()
                    if not at <= wait_at < end:
                        reason = "no_budget"
                        return
                    wait.update(state="claimed", reason="incomplete")
                    self.evidence_fd_clear = False  # Uncertain BEFORE acquisition.
                    descriptor = None
                    try:
                        try:
                            descriptor = os.pidfd_open(target["pid"], 0)
                        except TimeoutError:
                            raise
                        except OSError:
                            self.evidence_fd_clear = True  # Ordinary failed open; no descriptor.
                            wait.update(state="refused", reason="io_error")
                        else:
                            try:
                                if budget["clocks"] >= 512:
                                    reason = "no_budget"
                                    return
                                budget["clocks"] += 1
                                actual_wait_at = time.monotonic()
                                if actual_wait_at >= end:
                                    reason = "no_budget"
                                    return
                                wait["at"] = actual_wait_at
                                result = os.waitid(os.P_PIDFD, descriptor, os.WEXITED | os.WNOHANG | os.WNOWAIT)
                            except TimeoutError:
                                raise
                            except ChildProcessError:
                                wait.update(state="complete", reason="echild", outcome="ineligible")
                            except OSError:
                                wait.update(state="refused", reason="io_error")
                            else:
                                if result is None:
                                    wait.update(state="complete", reason="none", outcome="no_exit_state")
                                elif (type(result.si_pid) is int and result.si_pid == target["pid"]
                                      and type(result.si_signo) is int and result.si_signo == signal.SIGCHLD
                                      and type(result.si_status) is int and 0 <= result.si_status < 2**31
                                      and type(result.si_code) is int
                                      and result.si_code in (os.CLD_EXITED, os.CLD_KILLED, os.CLD_DUMPED)
                                      and ((result.si_code == os.CLD_EXITED and result.si_status <= 255)
                                           or (result.si_code != os.CLD_EXITED and result.si_status > 0))):
                                    codes = {os.CLD_EXITED: "CLD_EXITED", os.CLD_KILLED: "CLD_KILLED", os.CLD_DUMPED: "CLD_DUMPED"}
                                    wait.update(state="complete", reason="none", outcome="eligible_exit",
                                                code=codes[result.si_code], status=result.si_status)
                                else:
                                    wait.update(state="refused", reason="malformed")
                    finally:
                        if descriptor is not None:
                            target["close"] = "unknown"  # ONE close opportunity, no retry.
                            try:
                                os.close(descriptor)
                            except TimeoutError:
                                raise
                            except OSError:
                                wait.update(state="refused", reason="close_unknown", outcome="unknown", code=None, status=None)
                            else:
                                target["close"] = "closed"
                                self.evidence_fd_clear = True
                launcher_a, launcher_b = context["launcher_a"], context["launcher_b"]
                controller_a, controller_b = context["controller_a"], context["controller_b"]
                if (launcher_b["outcome"] != "observed" or launcher_b["start"] != evidence["launcher"]["start"]):
                    reason = "reused" if launcher_b["outcome"] == "observed" else "mismatch"
                    return
                controller_start = phase["controller"]["start"]
                if any(row["outcome"] == "observed" and row["start"] != controller_start for row in (controller_a, controller_b)):
                    slot["join"] = reason = "reused"
                    return
                live = all(row["outcome"] == "observed" and row["start"] == controller_start
                           and row["state"] not in ("Z", "X", "x") for row in (controller_a, controller_b))
                terminal = phase["outcome"] == "terminal"
                if terminal and all(row["outcome"] == "absent" for row in (controller_a, controller_b)):
                    join = "bound_terminal_absent"
                elif terminal and all(row["outcome"] == "observed" and row["state"] in ("Z", "X", "x") for row in (controller_a, controller_b)):
                    join = "bound_terminal_observed"
                elif terminal:
                    slot["join"] = reason = "mismatch"
                    return
                elif live and selected == "active":
                    join = "bound_live"
                elif live and phase["outcome"] == "interrupted":
                    join = "bound_interrupted_live"
                else:
                    join = "bound_outcome_unknown"
                if selected == "active" and (not live or not self.evidence_active_open):
                    reason = "unbound"
                    return
                if budget["clocks"] >= 512:
                    reason = "no_budget"
                    return
                budget["clocks"] += 1
                finished = time.monotonic()
                if not entered <= finished < end:
                    reason = "no_budget"
                    return
                if evidence is not self.evidence_binding or phase is not self.evidence_phase or slot["state"] != "claimed":
                    reason = "reentry"
                    return
                candidate = dict(slot, **context, targets=targets, finished=finished, join=join, state="complete", reason="none")
                import json
                # At the actual top-level attachment depth (indent=2).
                if len(json.dumps({"ownership_evidence": {"data": {selected: candidate}}}, indent=2).encode("utf-8")) > 8192:
                    reason = "overflow"
                    return
                if budget["clocks"] >= 512:
                    reason = "no_budget"
                    return
                budget["clocks"] += 1
                if time.monotonic() >= end:
                    reason = "no_budget"
                    return
                claim = slot
                # Failure cleanup must own the candidate BEFORE installation:
                # an interruption after the store must clear its positive data,
                # not merely the displaced claim. An earlier interruption leaves
                # that claim incomplete and permanently consumed.
                slot = candidate
                self.evidence_slots[selected] = candidate  # Last positive write.
                # Formatting and even the store can straddle main's closure.
                # The active window never reopens, references never revert and
                # a revoked claim never returns to claimed. Checking AFTER the
                # store therefore detects invalidation before/during it, without
                # another check/store gap. Closure after this check follows a
                # valid earlier completion. Until this gate finishes, stopped-
                # watcher-only freeze excludes all inspection of these slots.
                if (evidence is not self.evidence_binding or phase is not self.evidence_phase
                        or claim["state"] != "claimed" or self.evidence_slots[selected] is not candidate):
                    reason = "reentry"
                    return
                if selected == "active" and not self.evidence_active_open:
                    reason = "unbound"
                    return
                reason = "none"
            except TimeoutError:
                reason = "interrupted"
                raise
            except BaseException:
                reason = "interrupted"
                raise
            finally:
                if reason != "none":
                    slot.update(state="incomplete" if reason in ("interrupted", "incomplete") else "refused", reason=reason,
                                launcher_a=None, launcher_b=None, controller_a=None, controller_b=None, targets=None)

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
        annotations = self.evidence_returns
        annotate = self.evidence_binding is not None and not self.evidence_writes_ended
        if annotate:
            annotations.update(state="claimed", reason="incomplete", outcome="unknown", rows=[], coverage="none")
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
                        annotation = None
                        if annotate and annotations["state"] == "claimed":
                            if len(annotations["rows"]) == 34:
                                annotations.update(state="incomplete", reason="annotation_limit", coverage="partial")
                            elif (type(pid) is not int or not 0 < pid < 2**64
                                  or type(status) is not int or not 0 <= status < 2**32):
                                annotations.update(state="incomplete", reason="malformed", coverage="partial")
                            elif self.evidence_budget["clocks"] >= 512:
                                annotations.update(state="incomplete", reason="no_budget", coverage="partial")
                            else:
                                self.evidence_budget["clocks"] += 1
                                if time.monotonic() >= self.evidence_entry[1]:
                                    annotations.update(state="incomplete", reason="no_budget", coverage="partial")
                                else:
                                    annotation = dict(index=len(annotations["rows"]) + 1, pid=pid, wait_status=status,
                                                      exit=None, generation=None, join="unknown", slot=None, target=None)
                                    annotations["rows"].append(annotation)
                        self.reaped.append(dict(pid=pid, wait_status=status, exit=os.waitstatus_to_exitcode(status)))
                        if annotation is not None:
                            decoded = self.reaped[-1]["exit"]  # ORIGINAL decode only.
                            if type(decoded) is int and -(2**31) <= decoded < 2**31:
                                annotation["exit"] = decoded
                            else:
                                annotations.update(state="incomplete", reason="malformed", coverage="partial")
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
        if annotate and annotations["state"] == "claimed":
            # All ORIGINAL consuming loops returned without a gap. No Popen or
            # replacement launch occurs between the entry pin and these waits.
            slot = self.evidence_slots["precleanup"]
            rows = annotations["rows"]
            occurrences = {}
            for row in rows:
                occurrences[row["pid"]] = occurrences.get(row["pid"], 0) + 1
            for row in rows:
                matches = []
                if slot["state"] == "complete" and row["pid"] != self.evidence_phase_pid:
                    for index, target in enumerate(slot["targets"]):
                        if (target["pid"] == row["pid"] and target["registration"] == "matched"
                                and target["wait"]["state"] == "complete" and target["wait"]["outcome"] == "eligible_exit"
                                and target["close"] == "closed"):
                            matches.append((index, target["start"]))
                # Duplicate consumption would make the entire generation join
                # ambiguous. Numeric PID/status equality alone never qualifies.
                if len(matches) == 1 and occurrences[row["pid"]] == 1:
                    row.update(generation=matches[0][1], join="qualified", slot="precleanup", target=matches[0][0])
            annotations.update(reason="none", outcome="returned", coverage="all_reached")
            annotations["state"] = "complete"
        return dict(ok=absent and not self.errors and not self.thread.is_alive(), survivors=survivors, units=units, groups=groups,
                    errors=self.errors, identities=self.pids, reaped=self.reaped, collisions=self.collisions, watcher_stopped=not self.thread.is_alive(), seconds=time.monotonic() - started)
