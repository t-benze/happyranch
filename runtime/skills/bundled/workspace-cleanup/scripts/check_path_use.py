#!/usr/bin/env python3
"""Bounded, read-only current-use scanner for a workspace-cleanup candidate path.

TASK-8672 prototype (founder THR-259 seq185 approving seq183; retained seq171).
This is the ONE bounded stdlib helper intended to become
``runtime/skills/bundled/workspace-cleanup/scripts/check_path_use.py`` during the
production-integration leg. It is dependency-injectable so the complete finite
case matrix can be exercised deterministically without real /proc.

Contract (approved same-user population, seq185 name+role accident-prevention):
  * Complete means every process running as the agent's user was read, except
    confirmed exited processes and the fixed login/session daemon roles
    identified by an EXACT readable process name AND its exact bounded expected
    cgroup role:
      - ``sshd-session`` in ``session-<N>.scope`` under the user slice;
      - ``systemd`` (the per-user manager, comm ``systemd``) and ``(sd-pam)`` in
        ``user@<UID>.service/init.scope`` under the user slice;
      - ``ssh-agent`` in ``user@<UID>.service/app.slice/ssh-agent.service``;
      - ``gpg-agent`` in ``user@<UID>.service/app.slice/gpg-agent.service``;
      - ``gcr-ssh-agent`` OR its ``ssh-agent`` child alias in
        ``user@<UID>.service/app.slice/gcr-ssh-agent.service``.
    Name alone, role alone, a lookalike unit/session suffix, a generic
    ``*.service``/``*agent`` wildcard, an arbitrary cgroup or a different UID
    never qualifies. A qualifying exception is deliberately NOT inspected: an
    unreadable ``exe``/namespace does not reintroduce a veto, and this is an
    accident-prevention boundary, not executable-identity authentication.
  * Root-owned processes are outside this scan (scope exclusion only -- never
    proof of non-use and never permission to execute as root).
  * Any other unreadable same-user process makes coverage unknown -> the caller
    must skip.
  * Confirmed exit is distinct from EACCES/EPERM, PID/TID reuse, changed
    credentials, incomplete enumeration and transient unreadable references.
  * Checked per thread: cwd, root, exe, maps and private FD table.
  * Cross-mount-namespace processes require established path identity (the
    target path resolves to the same dev/ino through /proc/<pid>/root).
  * Positive non-exempt use blocks even when coverage is otherwise incomplete.
  * Enumeration is re-derived until bounded convergence; new processes are
    examined, never silently dropped.

Outcome is exactly one of ``clear_observation``, ``blocked`` or ``unknown``.
Never ``safe``.
"""

from __future__ import annotations

import argparse
import io
import json
import os
import re
import stat as stat_mod
import subprocess
import sys
import time
from dataclasses import dataclass, field

# seq185 exact name+role exception table. ``gcr-ssh-agent`` also matches when the
# kernel reports the ``ssh-agent`` child alias inside the gcr unit.
EXCEPTION_ROLES = ("sshd-session", "systemd --user", "(sd-pam)",
                   "ssh-agent", "gpg-agent", "gcr-ssh-agent")

DEFAULT_MAX_PIDS = 4096
DEFAULT_MAX_THREADS = 256
DEFAULT_MAX_FDS = 4096
DEFAULT_MAX_MAPS_BYTES = 4 * 1024 * 1024
DEFAULT_MAX_MAPS_LINES = 20000
DEFAULT_DEADLINE_SECONDS = 30.0
DEFAULT_MAX_ENUM_PASSES = 6
DEFAULT_MAX_WORKTREES = 4096


# ── typed read outcomes ───────────────────────────────────────────────────
@dataclass(frozen=True)
class Outcome:
    kind: str  # ok | denied | vanished | error | truncated
    value: object = None
    detail: str = ""


OK = "ok"
DENIED = "denied"
VANISHED = "vanished"
ERROR = "error"
TRUNCATED = "truncated"


# ── /proc seam ────────────────────────────────────────────────────────────
class RealProc:
    """Read-only /proc accessor. Every failure is a typed outcome, never an
    exception that silently disappears."""

    def __init__(self, proc_root: str = "/proc") -> None:
        self.root = proc_root
        self._stat_cache: dict[str, Outcome] = {}
        self._skip_ino_prefixes = ("/proc/", "/sys/", "/dev/")
        self._admit = lambda: True

    def bind_admission(self, admit) -> None:
        """Bind the scan's one shared read/iteration admission deadline."""
        self._admit = admit

    def _path(self, pid: str, rel: str) -> str:
        return os.path.join(self.root, pid, rel) if rel else os.path.join(self.root, pid)

    def list_pids(self, max_pids: int) -> Outcome:
        # F6: cap while iterating -- never fully materialize a directory before
        # applying the bound. Once the cap is reached the call returns truncated
        # and the caller must stop rather than reading further entries.
        try:
            it = os.scandir(self.root)
        except OSError as exc:  # pragma: no cover - host dependent
            return Outcome(ERROR, None, exc.__class__.__name__)
        pids: list[str] = []
        truncated = False
        try:
            with it:
                for entry in it:
                    if not self._admit():
                        return Outcome(TRUNCATED, pids, "deadline_exceeded")
                    if not entry.name.isdecimal():
                        continue
                    if len(pids) >= max_pids:
                        truncated = True
                        break
                    pids.append(entry.name)
        except OSError as exc:  # pragma: no cover - host dependent
            return Outcome(ERROR, None, exc.__class__.__name__)
        if truncated:
            return Outcome(TRUNCATED, pids, f"pids>{max_pids}")
        pids.sort(key=int)
        return Outcome(OK, pids)

    def listdir(self, pid: str, rel: str, max_entries: int) -> Outcome:
        p = self._path(pid, rel)
        try:
            it = os.scandir(p)
        except FileNotFoundError:
            return Outcome(VANISHED, None, p)
        except PermissionError:
            return Outcome(DENIED, None, p)
        except OSError as exc:
            return Outcome(ERROR, None, f"{p}:{exc.__class__.__name__}")
        names: list[str] = []
        truncated = False
        try:
            with it:
                for entry in it:
                    if not self._admit():
                        return Outcome(TRUNCATED, names, "deadline_exceeded")
                    if len(names) >= max_entries:
                        truncated = True
                        break
                    names.append(entry.name)
        except FileNotFoundError:
            return Outcome(VANISHED, None, p)
        except PermissionError:
            return Outcome(DENIED, None, p)
        except OSError as exc:
            return Outcome(ERROR, None, f"{p}:{exc.__class__.__name__}")
        if truncated:
            return Outcome(TRUNCATED, names, f"{p}>{max_entries}")
        return Outcome(OK, names)

    def read_text(self, pid: str, rel: str, max_bytes: int) -> Outcome:
        if not self._admit():
            return Outcome(TRUNCATED, None, "deadline_exceeded")
        p = self._path(pid, rel)
        try:
            with open(p, "rb") as fh:
                data = fh.read(max_bytes + 1)
        except FileNotFoundError:
            return Outcome(VANISHED, None, p)
        except PermissionError:
            return Outcome(DENIED, None, p)
        except OSError as exc:
            return Outcome(ERROR, None, f"{p}:{exc.__class__.__name__}")
        if len(data) > max_bytes:
            return Outcome(TRUNCATED, data[:max_bytes].decode("utf-8", "replace"), p)
        return Outcome(OK, data.decode("utf-8", "replace"))

    def readlink(self, pid: str, rel: str) -> Outcome:
        if not self._admit():
            return Outcome(TRUNCATED, None, "deadline_exceeded")
        p = self._path(pid, rel)
        try:
            return Outcome(OK, os.readlink(p))
        except FileNotFoundError:
            return Outcome(VANISHED, None, p)
        except PermissionError:
            return Outcome(DENIED, None, p)
        except OSError as exc:
            return Outcome(ERROR, None, f"{p}:{exc.__class__.__name__}")

    def stat_link(self, pid: str, rel: str) -> Outcome:
        if not self._admit():
            return Outcome(TRUNCATED, None, "deadline_exceeded")
        p = self._path(pid, rel)
        try:
            st = os.stat(p)
        except FileNotFoundError:
            return Outcome(VANISHED, None, p)
        except PermissionError:
            return Outcome(DENIED, None, p)
        except OSError as exc:
            return Outcome(ERROR, None, f"{p}:{exc.__class__.__name__}")
        return Outcome(OK, st, p)

    def stat_path(self, path: str) -> Outcome:
        if not self._admit():
            return Outcome(TRUNCATED, None, "deadline_exceeded")
        if path.startswith(self._skip_ino_prefixes):
            return Outcome(VANISHED, None, path)
        if path in self._stat_cache:
            return self._stat_cache[path]
        try:
            out = Outcome(OK, os.stat(path), path)
        except FileNotFoundError:
            out = Outcome(VANISHED, None, path)
        except PermissionError:
            out = Outcome(DENIED, None, path)
        except OSError as exc:
            out = Outcome(ERROR, None, f"{path}:{exc.__class__.__name__}")
        self._stat_cache[path] = out
        return out

    def stat_path_fresh(self, path: str) -> Outcome:
        """Uncached stat for the F3 observation-coherence final revalidation.

        ``stat_path`` deliberately caches within one scan, so a repeated cached
        lookup would mask a replacement that happened during collection. This
        method never consults or writes the cache.
        """
        if not self._admit():
            return Outcome(TRUNCATED, None, "deadline_exceeded")
        if path.startswith(self._skip_ino_prefixes):
            return Outcome(VANISHED, None, path)
        try:
            return Outcome(OK, os.stat(path), path)
        except FileNotFoundError:
            return Outcome(VANISHED, None, path)
        except PermissionError:
            return Outcome(DENIED, None, path)
        except OSError as exc:
            return Outcome(ERROR, None, f"{path}:{exc.__class__.__name__}")

    def stat_through_root(self, pid: str, path: str) -> Outcome:
        """Resolve ``path`` inside ``pid``'s mount namespace (identity check)."""
        if not self._admit():
            return Outcome(TRUNCATED, None, "deadline_exceeded")
        if path.startswith(self._skip_ino_prefixes):
            return Outcome(VANISHED, None, path)
        key = f"{pid}\0{path}"
        if key in self._stat_cache:
            return self._stat_cache[key]
        p = f"{self.root}/{pid}/root{path}"
        try:
            out = Outcome(OK, os.stat(p), p)
        except FileNotFoundError:
            out = Outcome(VANISHED, None, p)
        except PermissionError:
            out = Outcome(DENIED, None, p)
        except OSError as exc:
            out = Outcome(ERROR, None, f"{p}:{exc.__class__.__name__}")
        self._stat_cache[key] = out
        return out

    def host_context(self, self_pid: str) -> dict:
        """Establish whether this context enumerates the host process population.

        Necessary condition only: PID1 is the host init and /proc is a single
        unstacked (non-private) proc mount. The scanner may itself run in a
        child PID namespace while /proc still enumerates the host's initial
        population, so PID-namespace agreement is recorded as context, not a
        veto; a private sandbox proc mount (e.g. PID1=bwrap, stacked /proc)
        still fails.
        """
        comm = self.read_text("1", "comm", 64)
        pid1_comm = comm.value.strip() if comm.kind == OK else None
        pid1_pid = self.readlink("1", "ns/pid")
        self_pid_ns = self.readlink(self_pid, "ns/pid")
        pid1_mnt = self.readlink("1", "ns/mnt")
        self_mnt = self.readlink(self_pid, "ns/mnt")
        mi = self.read_text(self_pid, "mountinfo", 1 << 20)
        proc_mounts = []
        if mi.kind == OK:
            proc_mounts = [ln for ln in mi.value.splitlines() if " - proc proc " in ln]
        stacked = len(proc_mounts) != 1
        pid_ns_agree = (pid1_pid.kind == OK and self_pid_ns.kind == OK
                        and pid1_pid.value == self_pid_ns.value)
        mnt_agree = (pid1_mnt.kind == OK and self_mnt.kind == OK
                     and pid1_mnt.value == self_mnt.value)
        ok = (pid1_comm in ("systemd", "init")) and not stacked
        return {"pid1_comm": pid1_comm, "pid_ns_agree": pid_ns_agree,
                "mnt_agree": mnt_agree, "proc_mounts": len(proc_mounts),
                "stacked": stacked, "mountinfo": mi.kind,
                "pid1_ns_readable": pid1_pid.kind == OK, "ok": ok}

    def registered_worktrees(self, path: str, max_entries: int) -> Outcome:
        """Return canonical Git-registered roots containing ``path``."""
        if not self._admit():
            return Outcome(TRUNCATED, None, "deadline_exceeded")
        try:
            listed = subprocess.run(
                ["git", "-C", path, "worktree", "list", "--porcelain"],
                capture_output=True, text=True, timeout=5,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return Outcome(ERROR, None, exc.__class__.__name__)
        if listed.returncode:
            return Outcome(ERROR, None, "git_worktree_list")
        roots: list[str] = []
        for line in listed.stdout.splitlines():
            if not self._admit():
                return Outcome(TRUNCATED, roots, "deadline_exceeded")
            if not line.startswith("worktree "):
                continue
            if len(roots) >= max_entries:
                return Outcome(TRUNCATED, roots, f"worktrees>{max_entries}")
            root = os.path.realpath(line[9:])
            if path == root or path.startswith(root.rstrip("/") + "/"):
                roots.append(root)
        return Outcome(OK, roots)


class FakeProc:
    """Deterministic fault-injection seam over the same interface.

    ``spec`` is ``{pid: process-dict}``. Per-process keys: ``uid`` (4-list),
    ``comm``, ``ppid``, ``exe`` (realpath string or None), ``exe_stat`` (tuple
    (uid, mode, is_file) or None), ``cwd``, ``root``, ``maps`` (list of path
    strings), ``fds`` ({fd: link}), ``threads`` ({tid: {cwd,root,exe,maps,fds}}),
    ``ns`` ({pid,mnt,user}), ``cgroup``, ``starttime``.
    ``deny`` / ``vanish`` / ``error`` are sets of ``(pid, rel)``.
    """

    def __init__(self, spec: dict, *, deny=(), vanish=(), error=(), self_ns=None,
                 list_state: str | None = None, stat_map: dict | None = None,
                 default_stat: tuple[int, int] | None = None,
                 host_context: dict | None = None,
                 root_unverified: set | None = None,
                 registered_worktrees: list[str] | None = None) -> None:
        self.spec = spec
        self.deny = set(deny)
        self.vanish = set(vanish)
        self.error = set(error)
        self.self_ns = self_ns or {}
        self.list_state = list_state
        self.stat_map = stat_map or {}
        self.default_stat = default_stat
        self.root_unverified = set(root_unverified or ())
        self._registered_worktrees = list(registered_worktrees or ())
        self._host_context = host_context or {"ok": True, "synthetic": True,
                                              "pid1_comm": "systemd",
                                              "pid_ns_agree": True, "mnt_agree": True,
                                              "proc_mounts": 1, "stacked": False}

    def host_context(self, self_pid: str) -> dict:
        return dict(self._host_context)

    def registered_worktrees(self, path: str, max_entries: int) -> Outcome:
        roots = [os.path.realpath(root) for root in self._registered_worktrees
                 if path == os.path.realpath(root)
                 or path.startswith(os.path.realpath(root).rstrip("/") + "/")]
        if len(roots) > max_entries:
            return Outcome(TRUNCATED, roots[:max_entries])
        return Outcome(OK, roots)

    def _outcome(self, pid: str, rel: str):
        if (pid, rel) in self.deny:
            return Outcome(DENIED)
        if (pid, rel) in self.vanish:
            return Outcome(VANISHED)
        if (pid, rel) in self.error:
            return Outcome(ERROR)
        return Outcome(OK)

    def list_pids(self, max_pids: int) -> Outcome:
        if self.list_state == "truncated":
            return Outcome(TRUNCATED, sorted(self.spec, key=int)[:max_pids])
        if self.list_state in ("denied", "error"):
            return Outcome(self.list_state)
        pids = sorted(self.spec, key=int)
        if len(pids) > max_pids:
            return Outcome(TRUNCATED, pids[:max_pids])
        return Outcome(OK, pids)

    def _proc(self, pid: str) -> dict:
        return self.spec.get(pid, {})

    def listdir(self, pid: str, rel: str, max_entries: int) -> Outcome:
        if pid not in self.spec:
            return Outcome(VANISHED)
        out = self._outcome(pid, rel)
        if out.kind != OK:
            return out
        d = self._proc(pid)
        if rel == "task":
            return Outcome(OK, list(d.get("threads", {})))
        if rel == "fd":
            return Outcome(OK, list(d.get("fds", {})))
        if rel.startswith("task/") and rel.endswith("/fd"):
            tid = rel.split("/")[1]
            th = d.get("threads", {}).get(tid, {})
            return Outcome(OK, list(th.get("fds", {})))
        return Outcome(OK, [])

    def read_text(self, pid: str, rel: str, max_bytes: int) -> Outcome:
        if pid not in self.spec:
            return Outcome(VANISHED)
        out = self._outcome(pid, rel)
        if out.kind != OK:
            return out
        d = self._proc(pid)
        if rel == "status":
            uid = d.get("uid", [])
            lines = [f"Name:\t{d.get('comm', '?')}",
                     "Uid:\t" + "\t".join(str(u) for u in uid),
                     f"PPid:\t{d.get('ppid', '0')}"]
            return Outcome(OK, "\n".join(lines) + "\n")
        if rel == "comm":
            return Outcome(OK, d.get("comm", ""))
        if rel == "cgroup":
            return Outcome(OK, d.get("cgroup", ""))
        if rel == "maps":
            return Outcome(OK, "\n".join(d.get("maps", [])) + ("\n" if d.get("maps") else ""))
        if rel == "stat":
            return Outcome(OK, _stat_text(pid, d.get("starttime", 0)))
        if rel.startswith("task/"):
            parts = rel.split("/")
            if len(parts) >= 3:
                tid = parts[1]
                th = d.get("threads", {}).get(tid)
                if th is None:
                    return Outcome(VANISHED)
                if parts[2] == "maps":
                    return Outcome(OK, "\n".join(th.get("maps", []))
                                   + ("\n" if th.get("maps") else ""))
                if parts[2] == "stat":
                    return Outcome(OK, _stat_text(
                        tid, th.get("starttime", d.get("starttime", 0))))
                if parts[2] == "status":
                    uid = th.get("uid", d.get("uid", []))
                    lines = [f"Name:\t{th.get('comm', d.get('comm', '?'))}",
                             "Uid:\t" + "\t".join(str(u) for u in uid),
                             f"PPid:\t{th.get('ppid', d.get('ppid', '0'))}"]
                    return Outcome(OK, "\n".join(lines) + "\n")
        return Outcome(OK, "")

    def readlink(self, pid: str, rel: str) -> Outcome:
        if pid not in self.spec:
            return Outcome(VANISHED)
        out = self._outcome(pid, rel)
        if out.kind != OK:
            return out
        d = self._proc(pid)
        if rel in ("exe", "cwd", "root"):
            v = d.get(rel)
            return Outcome(OK, v) if v is not None else Outcome(VANISHED)
        if rel.startswith("ns/"):
            v = d.get("ns", {}).get(rel.split("/", 1)[1])
            return Outcome(OK, v) if v is not None else Outcome(DENIED)
        if rel.startswith("fd/"):
            v = d.get("fds", {}).get(rel.split("/", 1)[1])
            return Outcome(OK, v) if v is not None else Outcome(VANISHED)
        if rel.startswith("task/"):
            parts = rel.split("/")
            tid = parts[1]
            th = d.get("threads", {}).get(tid, {})
            if parts[2] in ("cwd", "root", "exe"):
                v = th.get(parts[2])
                return Outcome(OK, v) if v is not None else Outcome(VANISHED)
            if parts[2] == "fd" and len(parts) == 4:
                v = th.get("fds", {}).get(parts[3])
                return Outcome(OK, v) if v is not None else Outcome(VANISHED)
        return Outcome(VANISHED)

    def stat_link(self, pid: str, rel: str) -> Outcome:
        if pid not in self.spec:
            return Outcome(VANISHED)
        out = self._outcome(pid, rel)
        if out.kind != OK:
            return out
        d = self._proc(pid)
        if rel == "exe":
            es = d.get("exe_stat")
            if es is None:
                return Outcome(DENIED)
            uid, mode, is_file = es
            return Outcome(OK, SimpleStat(uid, mode, is_file))
        return self._outcome(pid, rel)

    def stat_path(self, path: str) -> Outcome:
        if path in self.stat_map:
            dev, ino = self.stat_map[path]
            return Outcome(OK, RealishStat(dev, ino))
        if self.default_stat is not None:
            dev, ino = self.default_stat
            return Outcome(OK, RealishStat(dev, ino))
        return Outcome(VANISHED)

    def stat_path_fresh(self, path: str) -> Outcome:
        # FakeProc has no stat cache; the fresh read is the same observation, so
        # a mutation to ``stat_map`` (target replacement) is visible here.
        return self.stat_path(path)

    def stat_through_root(self, pid: str, path: str) -> Outcome:
        if pid in self.root_unverified:
            return Outcome(DENIED, None, path)
        return self.stat_path(path)


@dataclass
class SimpleStat:
    st_uid: int
    st_mode: int
    is_file: bool


@dataclass
class RealishStat:
    st_dev: int
    st_ino: int


# ── identity evidence + exception classification ──────────────────────────
@dataclass
class IdentityEvidence:
    pid: str
    uid_quad: tuple[int, int, int, int] | None
    comm: str | None
    ppid: str | None
    exe_path: str | None
    exe_stat: object | None
    exe_verified_root_file: bool
    cgroup: str | None
    agent_uid: int
    # B1: how readable the executable actually was. One of
    # ``verified`` (root-owned non-writable executable inode),
    # ``readable_unverified`` (the executable was observed but is not a verified
    # root role binary -- contrary evidence), or ``unreadable`` (ptrace-denied /
    # absent, so no executable identity is available).
    exe_status: str = "unreadable"
    gid_quad: tuple[int, int, int, int] | None = None
    groups: tuple[int, ...] = ()
    parent_role: str | None = None
    parent_comm: str | None = None
    parent_uid: tuple[int, int, int, int] | None = None
    parent_cgroup: str | None = None
    evidence_gaps: list[str] = field(default_factory=list)

    @property
    def unit(self) -> str:
        # F4: collapse only an UNAMBIGUOUS single applicable path. Conflicting
        # systemd/unified paths yield "" so they can never grant an exception.
        return _cgroup_unit(self.cgroup)

    @property
    def cgroup_conflict(self) -> bool:
        return _cgroup_ambiguous(self.cgroup)

    @property
    def parent_unit(self) -> str:
        return _cgroup_unit(self.parent_cgroup)

    @property
    def exe_readable(self) -> bool:
        """True when the executable was actually observed (verified or not)."""
        return self.exe_status in ("verified", "readable_unverified")


def _basename(path: str | None) -> str | None:
    if not path:
        return None
    return os.path.basename(path.rstrip("/")) or None


def _exe_is_verified(exe_path: str | None, exe_stat: object | None) -> bool:
    """Root-owned, non-group/world-writable regular *executable* inode.

    An executable path alone is NOT reliable: the inode must be root-owned, not
    writable by group/other, a regular file, AND carry at least one execute bit
    (B2). A root-owned regular file without execute permission (e.g. 0o644) is
    not an executable and must never be accepted as verified executable
    evidence.
    """
    if not exe_path or exe_stat is None:
        return False
    st_uid = getattr(exe_stat, "st_uid", -1)
    st_mode = getattr(exe_stat, "st_mode", 0)
    is_file = getattr(exe_stat, "is_file", None)
    if is_file is None:
        is_file = stat_mod.S_ISREG(st_mode)
    if st_uid != 0:
        return False
    if st_mode & 0o022:
        return False
    if not (st_mode & 0o111):
        return False
    return bool(is_file)


def _cgroup_paths(cgroup_text: str | None) -> list[str]:
    """All applicable systemd/unified cgroup paths, in observed order, de-duped."""
    if not cgroup_text:
        return []
    paths: list[str] = []
    for line in cgroup_text.splitlines():
        line = line.strip()
        if not line:
            continue
        if ":" not in line:
            if line.startswith("/"):
                paths.append(line)
            continue
        parts = line.split(":", 2)
        if len(parts) != 3:
            continue
        controllers, path = parts[1], parts[2].strip()
        if controllers == "" or "name=systemd" in controllers:
            if path:
                paths.append(path)
    seen: set[str] = set()
    out: list[str] = []
    for p in paths:
        if p not in seen:
            seen.add(p)
            out.append(p)
    return out


def _cgroup_unit(cgroup_text: str | None) -> str:
    """The single unambiguous systemd cgroup path, or ``""`` when absent/ambiguous."""
    paths = _cgroup_paths(cgroup_text)
    return paths[0] if len(paths) == 1 else ""


def _cgroup_ambiguous(cgroup_text: str | None) -> bool:
    return len(_cgroup_paths(cgroup_text)) > 1


def _user_slice_prefix(uid: int) -> str:
    return f"/user.slice/user-{uid}.slice"


def _session_scope_re(uid: int):
    return re.compile(rf"^{re.escape(_user_slice_prefix(uid))}/session-\d+\.scope$")


def expected_exception_role(comm: str | None, uid: int,
                            unit: str | None) -> str | None:
    """Return the exact bounded role for an exact (comm, cgroup-unit) pair.

    Name-only, role-only, lookalike, wildcard and cross-UID matches return
    ``None``. This is deliberately a pure metadata match (seq185): it prevents
    accidental folder use by a known login/session daemon, and is not
    executable-identity authentication.
    """
    name = comm or ""
    if not unit:
        return None
    prefix = _user_slice_prefix(uid)
    if not unit.startswith(prefix + "/"):
        return None
    if name == "sshd-session" and _session_scope_re(uid).fullmatch(unit):
        return "sshd-session"
    user_service = f"{prefix}/user@{uid}.service"
    if name == "systemd" and unit == f"{user_service}/init.scope":
        return "systemd --user"
    if name == "(sd-pam)" and unit == f"{user_service}/init.scope":
        return "(sd-pam)"
    app_slice = f"{user_service}/app.slice"
    if name == "ssh-agent" and unit in (f"{app_slice}/ssh-agent.service",
                                        f"{app_slice}/gcr-ssh-agent.service"):
        return "ssh-agent"
    if name == "gpg-agent" and unit == f"{app_slice}/gpg-agent.service":
        return "gpg-agent"
    if name == "gcr-ssh-agent" and unit == f"{app_slice}/gcr-ssh-agent.service":
        return "gcr-ssh-agent"
    return None


_KNOWN_EXCEPTION_NAMES = ("sshd-session", "systemd", "(sd-pam)", "ssh-agent",
                          "gpg-agent", "gcr-ssh-agent")


def looks_exceptional(ev: IdentityEvidence) -> bool:
    """True when the observed name or cgroup unit resembles an exception.

    Used only for honest accounting of name-only / role-only mismatches; it never
    grants an exemption.
    """
    name = ev.comm or ""
    unit = ev.unit or ""
    if name in _KNOWN_EXCEPTION_NAMES:
        return True
    tail = unit.rsplit("/", 1)[-1] if unit else ""
    return tail in ("ssh-agent.service", "gpg-agent.service",
                    "gcr-ssh-agent.service", "init.scope") or "session-" in tail


def classify_exception(ev: IdentityEvidence) -> str:
    """Return the exact bounded role, or ``none`` for an ordinary member.

    Only :func:`expected_exception_role` (exact readable process name AND exact
    bounded cgroup role) can exempt a process. The executable inode, parent chain
    and any weaker claim are recorded for observation but never authenticate or
    veto a qualifying exception; a name-only/role-only lookalike is an ordinary
    member that is scanned. A conflicting applicable cgroup observation is
    ambiguous (F4) and therefore an ordinary member, never an exemption.
    """
    if ev.cgroup_conflict:
        return "none"
    return expected_exception_role(ev.comm, ev.agent_uid, ev.unit) or "none"


# ── scanner ───────────────────────────────────────────────────────────────
@dataclass
class Bounds:
    max_pids: int = DEFAULT_MAX_PIDS
    max_threads: int = DEFAULT_MAX_THREADS
    max_fds: int = DEFAULT_MAX_FDS
    max_maps_bytes: int = DEFAULT_MAX_MAPS_BYTES
    max_maps_lines: int = DEFAULT_MAX_MAPS_LINES
    deadline_seconds: float = DEFAULT_DEADLINE_SECONDS
    max_enum_passes: int = DEFAULT_MAX_ENUM_PASSES


@dataclass
class ScanResult:
    state: str
    target: str
    hits: list[dict] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    coverage: dict = field(default_factory=dict)
    exempt: list[dict] = field(default_factory=list)
    cycles: list[dict] = field(default_factory=list)

    def to_json(self) -> dict:
        return {"state": self.state, "target": self.target, "hits": self.hits,
                "reasons": sorted(set(self.reasons)), "coverage": self.coverage,
                "exempt": self.exempt, "cycles": self.cycles}


def _parse_uid(status_text: str) -> tuple[int, int, int, int] | None:
    for line in status_text.splitlines():
        if line.startswith("Uid:"):
            parts = line.split()
            if len(parts) >= 5:
                try:
                    return (int(parts[1]), int(parts[2]), int(parts[3]), int(parts[4]))
                except ValueError:
                    return None
    return None


def _parse_gid(status_text: str) -> tuple[int, int, int, int] | None:
    for line in status_text.splitlines():
        if line.startswith("Gid:"):
            parts = line.split()
            if len(parts) >= 5:
                try:
                    return (int(parts[1]), int(parts[2]), int(parts[3]), int(parts[4]))
                except ValueError:
                    return None
    return None


def _parse_groups(status_text: str) -> tuple[int, ...]:
    for line in status_text.splitlines():
        if line.startswith("Groups:"):
            out = []
            for tok in line.split(":", 1)[1].split():
                try:
                    out.append(int(tok))
                except ValueError:
                    return ()
            return tuple(out)
    return ()


def _parse_status_field(status_text: str, name: str) -> str | None:
    for line in status_text.splitlines():
        if line.startswith(name + ":"):
            return line.split(":", 1)[1].strip()
    return None


def _stat_text(pid, starttime) -> str:
    # Fields 3..22 after the comm: state, ppid, then placeholders. ``starttime``
    # is field 22 == rest[19] (there must be exactly 19 tokens before it).
    fields = ["S", "1"] + ["0"] * 17
    return f"{pid} (x) " + " ".join(fields) + f" {starttime}"


def _parse_starttime(stat_text: str) -> str | None:
    try:
        close = stat_text.rindex(")")
        rest = stat_text[close + 2:].split()
        return rest[19]  # field 22 == rest[19]
    except (ValueError, IndexError):
        return None


def _under(path: str | None, target_real: str) -> bool:
    if not path:
        return False
    p = path
    if p.endswith(" (deleted)"):
        p = p[: -len(" (deleted)")]
    if p == target_real:
        return True
    return p.startswith(target_real.rstrip("/") + "/")


def _matches_any(path: str | None, roots: dict) -> bool:
    return any(_under(path, root) for root in roots)


# F1: the ONLY cache directories the skill may remove, and therefore the only
# paths whose containing worktree must additionally be observed.
CACHE_BASENAMES = ("node_modules", ".venv")


def _path_identity(proc, pid: str, path: str, same_ns: bool):
    st = proc.stat_path(path) if same_ns else proc.stat_through_root(pid, path)
    if st.kind != OK:
        return None
    return (getattr(st.value, "st_dev", None), getattr(st.value, "st_ino", None))


@dataclass
class _Rec:
    pid: str
    uid: tuple | None = None
    gid: tuple | None = None
    groups: tuple[int, ...] = ()
    comm: str | None = None
    ppid: str | None = None
    starttime: str | None = None
    status_kind: str = OK
    stat_kind: str = OK
    role: str | None = None
    ev: IdentityEvidence | None = None
    handled: bool = False


class _DeadlineProc:
    """Admit every /proc/filesystem observation through one shared deadline.

    RealProc also binds the same admission callback inside its directory
    iterators and compound host-context read. The wrapper keeps injected test
    seams and any future accessor implementation subject to the same contract.
    """

    _OUTCOME_METHODS = {
        "list_pids", "listdir", "read_text", "readlink", "stat_link",
        "stat_path", "stat_path_fresh", "stat_through_root",
        "registered_worktrees",
    }

    def __init__(self, inner, admit) -> None:
        self._inner = inner
        self._admit = admit

    def __getattr__(self, name):
        value = getattr(self._inner, name)
        if name == "host_context":
            def host_context(*args, **kwargs):
                if not self._admit():
                    return {"ok": False, "deadline_exceeded": True}
                return value(*args, **kwargs)
            return host_context
        if name in self._OUTCOME_METHODS:
            def read(*args, **kwargs):
                if not self._admit():
                    return Outcome(TRUNCATED, None, "deadline_exceeded")
                return value(*args, **kwargs)
            return read
        return value


def scan(target: str | os.PathLike, *, proc=None, self_pid: int | None = None,
         agent_uid: int | None = None, bounds: Bounds | None = None,
         clock=time.monotonic,
         containing_worktree: str | os.PathLike | None = None) -> ScanResult:
    """Scan the approved same-user population for current use of ``target``.

    Read-only and bounded. Never returns ``safe``. Never mutates anything.

    F1: when ``target`` is a literal ``node_modules``/``.venv`` cache candidate,
    the containing registered worktree (its immediate parent, or an explicitly
    supplied path) is observed as well, so use anywhere in that worktree blocks.
    An unresolvable containing worktree is unknown, never a literal-path
    fallback.
    """
    raw_proc = proc or RealProc()
    bounds = bounds or Bounds()
    self_pid = str(self_pid if self_pid is not None else os.getpid())
    agent_uid = agent_uid if agent_uid is not None else os.getuid()
    started = clock()
    res = ScanResult(state="unknown", target=os.fspath(target))

    def expired() -> bool:
        return (clock() - started) > bounds.deadline_seconds

    def admit() -> bool:
        if not expired():
            return True
        if "deadline_exceeded" not in res.reasons:
            res.reasons.append("deadline_exceeded")
        if res.coverage:
            res.coverage["truncated"] = res.coverage.get("truncated", 0) + 1
        return False

    if hasattr(raw_proc, "bind_admission"):
        raw_proc.bind_admission(admit)
    proc = _DeadlineProc(raw_proc, admit)
    if not admit():
        return res
    target_real = os.path.realpath(os.fspath(target))
    res.target = target_real
    tstat = proc.stat_path(target_real)
    target_id = ((getattr(tstat.value, "st_dev", None), getattr(tstat.value, "st_ino", None))
                 if tstat.kind == OK else None)

    # F1/R2: roots that count as "the candidate". The literal target plus, for a
    # cache candidate, an EXPLICIT verified containing registered worktree. The
    # cache's own parent is never silently treated as the registration: an
    # omitted containing context for a cache is unknown, and a supplied context
    # that does not contain the target is unknown.
    roots: dict[str, tuple | None] = {target_real: target_id}
    containing_real: str | None = None
    containing_ok = False
    containing_raw = containing_worktree
    containing_expected: str | None = None
    containing_missing = False
    target_is_cache = os.path.basename(target_real.rstrip("/")) in CACHE_BASENAMES
    registration_expected: tuple[str, tuple[int, int]] | None = None
    if target_is_cache:
        registered = proc.registered_worktrees(target_real, DEFAULT_MAX_WORKTREES)
        candidates = list(registered.value or ()) if registered.kind == OK else []
        supplied_real = (os.path.realpath(os.fspath(containing_raw))
                         if containing_raw is not None and admit() else None)
        if (registered.kind != OK or len(candidates) != 1
                or (supplied_real is not None and supplied_real != candidates[0])):
            containing_missing = True
        else:
            containing_real = candidates[0]
            containing_expected = supplied_real or containing_real
            cstat = proc.stat_path(containing_real)
            containing_ok = cstat.kind == OK
            cid = ((getattr(cstat.value, "st_dev", None),
                    getattr(cstat.value, "st_ino", None))
                   if containing_ok else None)
            roots[containing_real] = cid
            if cid is not None:
                registration_expected = (containing_real, cid)
    elif containing_raw is not None:
        if not admit():
            containing_missing = True
        else:
            containing_real = os.path.realpath(os.fspath(containing_raw))
            containing_expected = containing_real
        if containing_real == target_real:
            # Whole-worktree registration is enforced by the shipped action-time
            # gate; the scanner needs no second observation root.
            containing_real = None
            containing_expected = target_real
        else:
            containing_missing = True

    res.coverage = {
        "agent_uid": agent_uid, "self_pid": self_pid,
        "target_dev_ino": list(target_id) if target_id else None,
        "containing_worktree": containing_real,
        "containing_worktree_dev_ino": (
            list(roots[containing_real])
            if containing_real and roots.get(containing_real) else None),
        "target_present": tstat.kind == OK,
        "containing_worktree_present": containing_ok,
        "total_pids": 0, "same_user": 0, "root": 0, "other_user": 0,
        "exempt": 0, "scanned": 0, "exited": 0, "unreadable_same_user": 0,
        # B3: an unreadable identity of UNKNOWN uid is distinct from a known
        # other-user process that is simply out of scope.
        "unreadable_unknown_uid": 0, "identity_read_errors": 0,
        # seq185: name-only / role-only lookalikes that did NOT qualify.
        "role_mismatch": 0,
        "denied": 0, "vanished": 0, "errors": 0,
        "truncated": 0, "maps_truncated": 0, "fd_truncated": 0,
        "threads_truncated": 0, "new_pids_after": 0, "reused_pids": 0,
        "mnt_ns_differs": 0, "mnt_ns_path_unverified": 0,
    }
    if tstat.kind != OK:
        res.reasons.append(f"target_unavailable:{tstat.kind}")
        res.cycles.append({"phase": "target", "kind": tstat.kind})
        return res
    if containing_missing:
        # R2: a cache candidate must resolve to exactly one Git-registered root;
        # a supplied root must agree. Missing, ambiguous or contradictory
        # context is unknown, never a dirname fallback.
        res.reasons.append("containing_worktree_unresolved")
    if containing_real is not None and not containing_ok:
        # Missing/ambiguous containing registration is unknown, not a
        # literal-path fallback.
        res.reasons.append("containing_worktree_unavailable")

    hc = proc.host_context(self_pid)
    res.coverage["host_context"] = hc
    if not hc.get("ok"):
        lost = [k for k in ("pid_ns_agree", "stacked") if not hc.get(k)]
        if hc.get("pid1_comm") not in ("systemd", "init"):
            lost.append("pid1_not_host_init")
        res.reasons.append("host_context_unestablished:" + ",".join(lost))

    self_mnt = proc.readlink(self_pid, "ns/mnt")
    self_mnt = self_mnt.value if self_mnt.kind == OK else None

    records: dict[str, _Rec] = {}
    processed: set[str] = set()

    def snapshot(pid: str) -> _Rec:
        st = proc.read_text(pid, "stat", 4096)
        status = proc.read_text(pid, "status", 8192)
        rec = _Rec(pid=pid, status_kind=status.kind, stat_kind=st.kind)
        if st.kind == OK:
            rec.starttime = _parse_starttime(st.value)
        elif st.kind == ERROR:
            res.coverage["identity_read_errors"] += 1
        if status.kind == OK:
            rec.uid = _parse_uid(status.value)
            rec.gid = _parse_gid(status.value)
            rec.groups = _parse_groups(status.value)
            rec.comm = _parse_status_field(status.value, "Name")
            rec.ppid = _parse_status_field(status.value, "PPid")
        elif status.kind == ERROR:
            res.coverage["identity_read_errors"] += 1
        records[pid] = rec
        return rec

    def parent_facts(ppid: str | None) -> tuple:
        if not ppid or ppid == "0":
            return None, None, None
        prec = records.get(ppid)
        if prec is None:
            return None, None, None
        cg = proc.read_text(ppid, "cgroup", 8192)
        return prec.comm, prec.uid, (cg.value if cg.kind == OK else None)

    def ensure_parents() -> None:
        """Snapshot parent records (e.g. a root sshd listener) so role evidence
        can use the parent chain. Bounded by the finite parent chain."""
        for _ in range(8):
            if expired():
                res.reasons.append("deadline_exceeded")
                res.coverage["truncated"] += 1
                return
            missing = sorted({r.ppid for r in list(records.values())
                              if r.ppid and r.ppid != "0" and r.ppid not in records},
                             key=int)
            if not missing:
                return
            for ppid in missing:
                processed.add(ppid)
                snapshot(ppid)

    def classify_pending() -> None:
        ensure_parents()
        for pid, rec in list(records.items()):
            if rec.handled or rec.uid is None:
                continue
            if rec.uid == (0, 0, 0, 0):
                rec.role = "root"
                rec.handled = True
            elif rec.uid != (agent_uid,) * 4:
                rec.role = ("credential_transition"
                            if (agent_uid in rec.uid or 0 in rec.uid) else "other")
                rec.handled = True
        for pid, rec in list(records.items()):
            if rec.uid is None or rec.role is not None or rec.ev is not None:
                continue
            # R3: identity gathering performs reads; admit each one against the
            # shared deadline. An unclassified record is unknown, not clean.
            if expired():
                res.reasons.append("deadline_exceeded")
                res.coverage["truncated"] += 1
                return
            if rec.uid == (agent_uid,) * 4:
                ev = _gather_identity(proc, pid, rec, agent_uid)
                ev.parent_comm, ev.parent_uid, ev.parent_cgroup = parent_facts(rec.ppid)
                rec.ev = ev
        # classify every role except (sd-pam) first, then resolve its parent
        for pid, rec in list(records.items()):
            if rec.ev is None or rec.role is not None or rec.ev.comm == "(sd-pam)":
                continue
            role = classify_exception(rec.ev)
            rec.role = role if role in EXCEPTION_ROLES else "member"
        for pid, rec in list(records.items()):
            if rec.ev is None or rec.role is not None:
                continue
            parent = records.get(rec.ppid or "")
            rec.ev.parent_role = parent.role if parent else None
            role = classify_exception(rec.ev)
            rec.role = role if role in EXCEPTION_ROLES else "member"

    # ── bounded enumeration convergence ─────────────────────────────────────
    # Each pass re-lists and ingests every process that appeared since the last
    # ingest; a pass with no unprocessed process is the observation boundary.
    stable = False
    enum_pass = -1
    for enum_pass in range(bounds.max_enum_passes):
        if expired():
            res.reasons.append("deadline_exceeded")
            res.coverage["truncated"] += 1
            break
        listed = proc.list_pids(bounds.max_pids)
        if listed.kind != OK:
            if listed.kind == TRUNCATED:
                res.coverage["truncated"] += 1
            elif listed.kind == ERROR:
                res.coverage["errors"] += 1
            res.reasons.append(f"enumeration_{listed.kind}")
            res.cycles.append({"phase": "enumerate", "kind": listed.kind,
                               "detail": listed.detail})
            break
        res.coverage["total_pids"] = max(res.coverage["total_pids"], len(listed.value))
        new_pids = [p for p in listed.value if p not in processed]
        if not new_pids:
            stable = True
            break
        if enum_pass > 0:
            # B3: processes that appeared after the first ingest (bounded churn).
            res.coverage["new_pids_after"] += len(new_pids)
        deadline_hit = False
        for pid in new_pids:
            processed.add(pid)
            if expired():
                # R3: never admit another snapshot read after expiry.
                res.reasons.append("deadline_exceeded")
                res.coverage["truncated"] += 1
                deadline_hit = True
                continue
            if pid == self_pid:
                continue
            snapshot(pid)
        classify_pending()
        res.cycles.append({"phase": "enumerate_pass", "pass": enum_pass,
                           "new": len(new_pids)})
        if deadline_hit:
            break
    res.coverage["enum_passes"] = enum_pass + 1

    # ── tally identity classes for every examined record ────────────────────
    for rec in sorted(records.values(), key=lambda r: int(r.pid)):
        if rec.uid is None:
            if rec.status_kind == VANISHED or rec.stat_kind == VANISHED:
                res.coverage["exited"] += 1
            else:
                # B3: identity itself is unreadable -> UNKNOWN uid. This is not
                # the same as a known other-user process, which is out of scope.
                kind = rec.stat_kind if rec.starttime is None else rec.status_kind
                res.reasons.append(f"identity_{kind}:{rec.pid}")
                res.coverage["unreadable_unknown_uid"] += 1
                if kind == ERROR:
                    res.coverage["errors"] += 1
            continue
        if rec.role == "member":
            res.coverage["same_user"] += 1
            if rec.ev is not None and looks_exceptional(rec.ev):
                # Name-only or role-only lookalike: no exemption, ordinary scan.
                res.coverage["role_mismatch"] += 1
        elif rec.role in EXCEPTION_ROLES:
            res.coverage["same_user"] += 1
            res.coverage["exempt"] += 1
            ev = rec.ev
            res.exempt.append({"pid": rec.pid, "role": rec.role,
                               "basis": "name_and_cgroup_role",
                               "exe": ev.exe_path if ev else None,
                               "comm": (ev.comm if ev else rec.comm),
                               "unit": ev.unit if ev else "",
                               "gaps": sorted(set(ev.evidence_gaps)) if ev else []})
        elif rec.role == "root":
            res.coverage["root"] += 1
        elif rec.role == "other":
            # Known other-user process: out of scope, not a coverage hole.
            res.coverage["other_user"] += 1
        elif rec.role == "credential_transition":
            res.reasons.append(f"credential_transition:{rec.pid}")
            res.coverage["unreadable_same_user"] += 1
        else:
            res.reasons.append(f"unclassified_identity:{rec.pid}")
            res.coverage["unreadable_unknown_uid"] += 1

    # scan non-exempt members. F2: bracket each leader observation with PID
    # starttime plus all four UID credentials; a reuse or credential change is
    # unknown. F3: revalidate the target/containing identity with uncached reads
    # after collection. F6: enforce the shared deadline at every admission and
    # before claiming success.
    for pid, rec in records.items():
        if rec.role != "member" or rec.handled:
            continue
        if expired():
            res.reasons.append("deadline_exceeded")
            res.coverage["truncated"] += 1
            break
        rec.handled = True
        cur = proc.read_text(pid, "stat", 4096)
        if cur.kind == VANISHED:
            res.coverage["exited"] += 1
            res.coverage["vanished"] += 1
            continue
        if cur.kind != OK:
            res.reasons.append(f"identity_recheck_{cur.kind}:{pid}")
            res.coverage["unreadable_same_user"] += 1
            res.coverage["denied"] += 1
            continue
        cur_start = _parse_starttime(cur.value)
        if cur_start is None or rec.starttime is None:
            # F2: a readable stat with an unparseable starttime (now or at
            # snapshot time) is an incomplete identity; None == None must not
            # look like a reuse-free match.
            res.reasons.append(f"identity_malformed_starttime:{pid}")
            res.coverage["unreadable_same_user"] += 1
            continue
        if cur_start != rec.starttime:
            res.reasons.append(f"pid_reuse:{pid}")
            res.coverage["reused_pids"] += 1
            continue
        cur_status = proc.read_text(pid, "status", 8192)
        if cur_status.kind == VANISHED:
            res.coverage["exited"] += 1
            res.coverage["vanished"] += 1
            continue
        if cur_status.kind != OK:
            res.reasons.append(f"identity_recheck_{cur_status.kind}:{pid}")
            res.coverage["unreadable_same_user"] += 1
            res.coverage["denied"] += 1
            continue
        if _parse_uid(cur_status.value) != rec.uid:
            res.reasons.append(f"credential_change:{pid}")
            res.coverage["unreadable_same_user"] += 1
            continue
        res.coverage["scanned"] += 1
        _scan_member(proc, pid, rec, roots, bounds, res, self_mnt, expired)

        # post-observation identity revalidation
        after = proc.read_text(pid, "stat", 4096)
        if after.kind != OK or _parse_starttime(after.value) != rec.starttime:
            res.reasons.append(f"identity_changed_after_scan:{pid}")
            res.coverage["unreadable_same_user"] += 1
            continue
        after_status = proc.read_text(pid, "status", 8192)
        if after_status.kind != OK:
            res.reasons.append(f"identity_recheck_after_{after_status.kind}:{pid}")
            res.coverage["unreadable_same_user"] += 1
        elif _parse_uid(after_status.value) != rec.uid:
            res.reasons.append(f"credential_change_after_scan:{pid}")
            res.coverage["unreadable_same_user"] += 1

    # F3/R3: no final revalidation read is admitted after the shared deadline.
    if expired():
        res.reasons.append("deadline_exceeded")
        res.coverage["truncated"] += 1
    else:
        # F3: the candidate AND the containing worktree must still resolve to
        # their observed identity after collection, read uncached. Replacement,
        # disappearance or changed resolution is unknown -- this is observation
        # coherence, not a fence against a later opener or an adversarial final
        # race.
        for root in list(roots):
            st = proc.stat_path_fresh(root)
            if st.kind != OK:
                res.reasons.append(f"target_identity_lost:{st.kind}:{root}")
                continue
            ident = (getattr(st.value, "st_dev", None),
                     getattr(st.value, "st_ino", None))
            expected = roots.get(root)
            if expected is not None and ident != expected:
                res.reasons.append(f"target_identity_changed:{root}")
        if registration_expected is not None:
            again = proc.registered_worktrees(target_real, DEFAULT_MAX_WORKTREES)
            roots_again = list(again.value or ()) if again.kind == OK else []
            if roots_again != [registration_expected[0]]:
                res.reasons.append("containing_registration_changed")
            else:
                rst = proc.stat_path_fresh(roots_again[0])
                rid = ((getattr(rst.value, "st_dev", None),
                        getattr(rst.value, "st_ino", None))
                       if rst.kind == OK else None)
                if rid != registration_expected[1]:
                    res.reasons.append("containing_registration_identity_changed")
        if admit():
            try:
                if os.path.realpath(os.fspath(target)) != target_real:
                    res.reasons.append("target_resolution_changed")
            except OSError:
                res.reasons.append("target_resolution_changed")
        # R2: re-resolve the LITERAL supplied containing path too -- a containing
        # alias retargeted during the member reads must not leave a clear result
        # behind an unchanged cached root inode.
        if (containing_raw is not None and containing_expected is not None
                and admit()):
            try:
                if os.path.realpath(os.fspath(containing_raw)) != containing_expected:
                    res.reasons.append("containing_resolution_changed")
            except OSError:
                res.reasons.append("containing_resolution_changed")

    # The last enumeration that contained no unprocessed process defines the
    # observation boundary. Processes that appear after that instant are later
    # openers (the disclosed snapshot residual). The loop re-listed after every
    # ingest, so anything that appeared during the window was ingested and
    # examined. Churn is unknown only when the bounded pass budget is exhausted
    # without ever reaching a boundary (unbounded churn).
    if not stable:
        res.reasons.append("enumeration_churn")

    # F6: a scan that exhausted its shared deadline never claims success.
    if not res.hits and expired():
        res.reasons.append("deadline_exceeded")

    if res.hits:
        res.state = "blocked"
    elif res.reasons:
        res.state = "unknown"
    else:
        res.state = "clear_observation"
    return res


def _gather_identity(proc, pid: str, rec: _Rec, agent_uid: int) -> IdentityEvidence:
    exe = proc.readlink(pid, "exe")
    exe_path = exe.value if exe.kind == OK else None
    exe_stat_out = proc.stat_link(pid, "exe")
    exe_stat = exe_stat_out.value if exe_stat_out.kind == OK else None
    verified = _exe_is_verified(exe_path, exe_stat)
    exe_readable = (exe.kind == OK) or (exe_stat_out.kind == OK)
    if verified:
        exe_status = "verified"
    elif exe_readable:
        # B1: the executable was observed but is not a verified root role binary
        # -> contrary evidence, never a fallback to weaker role claims.
        exe_status = "readable_unverified"
    else:
        exe_status = "unreadable"
    cg = proc.read_text(pid, "cgroup", 8192)
    cgroup = cg.value if cg.kind == OK else None
    ev = IdentityEvidence(
        pid=pid, uid_quad=rec.uid, comm=rec.comm, ppid=rec.ppid,
        exe_path=exe_path, exe_stat=exe_stat,
        exe_verified_root_file=verified, exe_status=exe_status,
        gid_quad=rec.gid, groups=rec.groups,
        cgroup=cgroup, agent_uid=agent_uid)
    if exe_status == "unreadable":
        ev.evidence_gaps.append("exe_unreadable")
    elif exe_status == "readable_unverified":
        ev.evidence_gaps.append("exe_unverified")
    if cgroup is None:
        ev.evidence_gaps.append("cgroup_unreadable")
    return ev


def _still_present(proc, pid: str, tid: str | None) -> bool | None:
    """Return true/false only for observed presence/confirmed disappearance.

    F2: ``ENOENT`` for a cwd/maps/fd reference is NOT confirmed process/thread
    exit. Only an actually absent PID/TID stat (and status for a leader) proves
    the disappearance; a still-present PID/TID with a missing child reference is
    an unknown observation, never a clean clear. Denied/error/truncated identity
    reads return ``None`` rather than masquerading as a confirmed exit.
    """
    if tid is None:
        st = proc.read_text(pid, "stat", 4096)
        if st.kind == OK:
            return True
        if st.kind != VANISHED:
            return None
        status = proc.read_text(pid, "status", 8192)
        if status.kind == OK:
            return True
        return False if status.kind == VANISHED else None
    st = proc.read_text(pid, f"task/{tid}/stat", 4096)
    if st.kind == OK:
        return True
    return False if st.kind == VANISHED else None


def _thread_bracket(proc, pid: str, tid: str):
    """Return ((starttime, uid_quad), "ok") or (None, state).

    F2: a complete TID identity requires a parseable starttime AND all four UID
    fields from a readable status. A denied/error status read, a missing ``Uid:``
    line, or an unparseable starttime is an incomplete identity -> ``unknown``,
    never ``ok`` and never a comparison against missing values. Only an actually
    absent TID ``stat`` is a confirmed disappearance (``gone``).
    """
    st = proc.read_text(pid, f"task/{tid}/stat", 4096)
    if st.kind == VANISHED:
        return None, "gone"
    if st.kind != OK:
        return None, st.kind
    starttime = _parse_starttime(st.value)
    if starttime is None:
        return None, "malformed"
    status = proc.read_text(pid, f"task/{tid}/status", 8192)
    if status.kind == VANISHED:
        return None, "gone"
    if status.kind != OK:
        return None, status.kind
    uid = _parse_uid(status.value)
    if uid is None:
        return None, "malformed"
    return (starttime, uid), "ok"


def _scan_member(proc, pid: str, rec: _Rec, roots: dict, bounds: Bounds,
                 res: ScanResult, self_mnt: str | None, expired) -> None:
    mnt = proc.readlink(pid, "ns/mnt")
    usr = proc.readlink(pid, "ns/user")
    if mnt.kind == VANISHED or usr.kind == VANISHED:
        # F2: ENOENT for a namespace link is a missing child reference, not
        # confirmed exit. Only an actually absent PID proves the process is
        # gone; a still-present PID with a missing namespace is unknown.
        present = _still_present(proc, pid, None)
        if present is not False:
            res.reasons.append(f"ns_vanished_but_present:{pid}")
            res.coverage["unreadable_same_user"] += 1
        else:
            res.coverage["exited"] += 1
            res.coverage["vanished"] += 1
        return
    if mnt.kind != OK or usr.kind != OK:
        kind = mnt.kind if mnt.kind != OK else usr.kind
        res.reasons.append(f"ns_{kind}:{pid}")
        res.coverage["unreadable_same_user"] += 1
        if kind == DENIED:
            res.coverage["denied"] += 1
        elif kind == TRUNCATED:
            res.coverage["truncated"] += 1
        else:
            res.coverage["errors"] += 1
        return
    same_ns = self_mnt is not None and mnt.value == self_mnt
    if not same_ns:
        res.coverage["mnt_ns_differs"] += 1
        # establish path identity for every observed root in that namespace
        for root, rid in roots.items():
            if rid is None:
                res.reasons.append(f"mnt_ns_target_identity_unknown:{pid}")
                res.coverage["mnt_ns_path_unverified"] += 1
            else:
                ident = _path_identity(proc, pid, root, same_ns=False)
                if ident != rid:
                    res.reasons.append(f"mnt_ns_path_unverified:{pid}")
                    res.coverage["mnt_ns_path_unverified"] += 1

    threads = proc.listdir(pid, "task", bounds.max_threads)
    if threads.kind == VANISHED:
        # F2: same rule as the namespace links -- a missing thread listing is
        # not a confirmed exit while the PID is still observable.
        present = _still_present(proc, pid, None)
        if present is not False:
            res.reasons.append(f"threads_vanished_but_present:{pid}")
            res.coverage["unreadable_same_user"] += 1
        else:
            res.coverage["exited"] += 1
            res.coverage["vanished"] += 1
        return
    if threads.kind != OK:
        res.reasons.append(f"threads_{threads.kind}:{pid}")
        res.coverage["unreadable_same_user"] += 1
        if threads.kind == TRUNCATED:
            res.coverage["threads_truncated"] += 1
            res.coverage["truncated"] += 1
        elif threads.kind == DENIED:
            res.coverage["denied"] += 1
        else:
            res.coverage["errors"] += 1
        return
    tids = threads.value or []
    if len(tids) > bounds.max_threads:
        res.reasons.append(f"threads_truncated:{pid}")
        res.coverage["threads_truncated"] += 1
        res.coverage["truncated"] += 1
        return

    def _inode_hit(value: str | None) -> bool:
        if not value or not value.startswith("/"):
            return False
        ident = _path_identity(proc, pid, value, same_ns)
        return any(rid is not None and ident == rid for rid in roots.values())

    def vanished_ref(kind: str, tid: str | None) -> None:
        label = f"{pid}{':' + tid if tid else ''}"
        present = _still_present(proc, pid, tid)
        if present is not False:
            res.reasons.append(f"{kind}_vanished_but_present:{label}")
            res.coverage["unreadable_same_user"] += 1
        else:
            res.coverage["exited"] += 1
            res.coverage["vanished"] += 1

    def check(rel: str, kind: str, tid: str | None) -> None:
        link = proc.readlink(pid, rel)
        if link.kind == VANISHED:
            if kind == "exe":
                # F2 names cwd/maps/fd explicitly. An absent exe symlink (e.g. a
                # zombie/kernel task) is a closed reference, not a coverage hole.
                res.coverage["vanished"] += 1
                return
            vanished_ref(kind, tid)
            return
        if link.kind != OK:
            res.reasons.append(f"{kind}_{link.kind}:{pid}{':' + tid if tid else ''}")
            if link.kind == DENIED:
                res.coverage["denied"] += 1
                res.coverage["unreadable_same_user"] += 1
            elif link.kind == TRUNCATED:
                res.coverage["truncated"] += 1
            else:
                res.coverage["errors"] += 1
                res.coverage["unreadable_same_user"] += 1
            return
        if _matches_any(link.value, roots):
            res.hits.append({"pid": pid, "tid": tid, "kind": kind, "evidence": link.value})
        elif _inode_hit(link.value):
            res.hits.append({"pid": pid, "tid": tid, "kind": kind + "_inode",
                             "evidence": link.value})

    def check_maps(rel: str, tid: str | None) -> None:
        mt = proc.read_text(pid, rel, bounds.max_maps_bytes)
        if mt.kind == VANISHED:
            vanished_ref("maps", tid)
            return
        if mt.kind != OK:
            res.reasons.append(f"maps_{mt.kind}:{pid}{':' + tid if tid else ''}")
            res.coverage["unreadable_same_user"] += 1
            if mt.kind == TRUNCATED:
                res.coverage["maps_truncated"] += 1
                res.coverage["truncated"] += 1
            elif mt.kind == DENIED:
                res.coverage["denied"] += 1
            else:
                res.coverage["errors"] += 1
            return
        # Stream the already byte-bounded observation and stop at the first
        # over-cap line. Never materialize every mapping before enforcing the
        # line cap.
        for index, line in enumerate(io.StringIO(mt.value)):
            # R3: every newly admitted maps-line iteration checks the shared
            # deadline; an in-flight read is never hard-preempted.
            if expired():
                res.reasons.append("deadline_exceeded")
                res.coverage["truncated"] += 1
                return
            if index >= bounds.max_maps_lines:
                res.reasons.append(f"maps_truncated:{pid}{':' + tid if tid else ''}")
                res.coverage["maps_truncated"] += 1
                res.coverage["truncated"] += 1
                return
            parts = line.split(None, 5)
            if len(parts) >= 6 and _matches_any(parts[5], roots):
                res.hits.append({"pid": pid, "tid": tid, "kind": "maps",
                                 "evidence": parts[5]})

    def check_fds(rel: str, tid: str | None) -> None:
        fds = proc.listdir(pid, rel, bounds.max_fds)
        if fds.kind == VANISHED:
            vanished_ref("fd", tid)
            return
        if fds.kind != OK:
            res.reasons.append(f"fd_list_{fds.kind}:{pid}{':' + tid if tid else ''}")
            res.coverage["unreadable_same_user"] += 1
            if fds.kind == TRUNCATED:
                res.coverage["fd_truncated"] += 1
                res.coverage["truncated"] += 1
            elif fds.kind == DENIED:
                res.coverage["denied"] += 1
            else:
                res.coverage["errors"] += 1
            return
        entries = fds.value or []
        if len(entries) > bounds.max_fds:
            res.reasons.append(f"fd_truncated:{pid}{':' + tid if tid else ''}")
            res.coverage["fd_truncated"] += 1
            res.coverage["truncated"] += 1
            return
        for fd in entries:
            # R3: a newly admitted FD read is checked against the shared
            # deadline before it happens; the in-flight read is not preempted.
            if expired():
                res.reasons.append("deadline_exceeded")
                res.coverage["truncated"] += 1
                return
            link = proc.readlink(pid, f"{rel}/{fd}")
            if link.kind == VANISHED:
                # a single fd entry closing mid-iteration is a confirmed close
                res.coverage["vanished"] += 1
                continue
            if link.kind != OK:
                res.reasons.append(f"fd_{link.kind}:{pid}{':' + tid if tid else ''}")
                if link.kind == DENIED:
                    res.coverage["denied"] += 1
                    res.coverage["unreadable_same_user"] += 1
                elif link.kind == TRUNCATED:
                    res.coverage["truncated"] += 1
                else:
                    res.coverage["errors"] += 1
                    res.coverage["unreadable_same_user"] += 1
                continue
            if _matches_any(link.value, roots):
                res.hits.append({"pid": pid, "tid": tid, "kind": "fd",
                                 "evidence": link.value})
            elif _inode_hit(link.value):
                res.hits.append({"pid": pid, "tid": tid, "kind": "fd_inode",
                                 "evidence": link.value})

    # F2/F6: process each thread in its own identity bracket, admitted against
    # the shared deadline before every bounded read/iteration.
    for tid in [None] + tids:
        if expired():
            res.reasons.append("deadline_exceeded")
            res.coverage["truncated"] += 1
            return
        t_start = t_uid = None
        if tid is not None:
            bracket, state = _thread_bracket(proc, pid, tid)
            if state == "gone":
                res.coverage["exited"] += 1
                res.coverage["vanished"] += 1
                continue
            if state != "ok":
                res.reasons.append(f"thread_identity_{state}:{pid}:{tid}")
                res.coverage["unreadable_same_user"] += 1
                continue
            t_start, t_uid = bracket
        prefix = "" if tid is None else f"task/{tid}/"
        for rel, kind in ((prefix + "cwd", "cwd"), (prefix + "root", "root"),
                          (prefix + "exe", "exe")):
            if expired():
                res.reasons.append("deadline_exceeded")
                res.coverage["truncated"] += 1
                return
            check(rel, kind, tid)
        if expired():
            res.reasons.append("deadline_exceeded")
            res.coverage["truncated"] += 1
            return
        check_maps(prefix + "maps", tid)
        if expired():
            res.reasons.append("deadline_exceeded")
            res.coverage["truncated"] += 1
            return
        check_fds(prefix + "fd", tid)
        if tid is not None:
            after, state2 = _thread_bracket(proc, pid, tid)
            if state2 == "gone":
                res.coverage["exited"] += 1
                res.coverage["vanished"] += 1
            elif state2 != "ok":
                res.reasons.append(f"thread_identity_recheck_{state2}:{pid}:{tid}")
                res.coverage["unreadable_same_user"] += 1
            else:
                a_start, a_uid = after
                if a_start != t_start:
                    res.reasons.append(f"thread_reuse:{pid}:{tid}")
                    res.coverage["reused_pids"] += 1
                elif t_uid is not None and a_uid != t_uid:
                    res.reasons.append(f"thread_credential_change:{pid}:{tid}")
                    res.coverage["unreadable_same_user"] += 1


# ── CLI ───────────────────────────────────────────────────────────────────
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="bounded current-use scanner")
    ap.add_argument("--target", required=True, help="candidate path to check")
    ap.add_argument("--containing-worktree", default=None,
                    help="registered worktree containing a cache candidate "
                         "(node_modules/.venv); derived from Git registration "
                         "when omitted")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--max-pids", type=int, default=DEFAULT_MAX_PIDS)
    ap.add_argument("--deadline-seconds", type=float, default=DEFAULT_DEADLINE_SECONDS)
    ap.add_argument("--max-enum-passes", type=int, default=DEFAULT_MAX_ENUM_PASSES)
    args = ap.parse_args(argv)
    res = scan(args.target, bounds=Bounds(max_pids=args.max_pids,
                                          deadline_seconds=args.deadline_seconds,
                                          max_enum_passes=args.max_enum_passes),
               containing_worktree=args.containing_worktree)
    out = res.to_json()
    if args.json:
        print(json.dumps(out, indent=2, sort_keys=True))
    else:
        print(f"state={out['state']} target={out['target']}")
        for r in out["reasons"]:
            print(f"  reason: {r}")
        for h in out["hits"]:
            print(f"  hit: {h}")
    return 0 if out["state"] == "clear_observation" else (3 if out["state"] == "blocked" else 2)


if __name__ == "__main__":
    sys.exit(main())
