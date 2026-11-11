"""Shared stdlib-only Darwin process observations, also shipped as skill data.

The S1 census binding and checked state live here exactly once. Cleanup strict
reads are separate from its historical permissive sampling methods.
"""
from __future__ import annotations

import os
import sys
import threading
import time
from contextlib import contextmanager

# Checked observations are consumed only by the process-group lifecycle owner.
# Other readers retain their historical permissive measurement semantics.
_CHECKED_ROW_LIMIT = 4096
_CHECKED_SECONDS = 1.0
_checked_read = threading.local()


class ProcessObservationUnavailable(RuntimeError):
    """An observation is incomplete; it supplies no proof of absence."""


@contextmanager
def _checked_observation():
    previous = getattr(_checked_read, "deadline", None)
    _checked_read.deadline = time.monotonic() + _CHECKED_SECONDS
    try:
        yield
    finally:
        _checked_read.deadline = previous


def _check_observation_budget(rows: int = 0) -> None:
    deadline = getattr(_checked_read, "deadline", None)
    if deadline is not None and (rows > _CHECKED_ROW_LIMIT or time.monotonic() >= deadline):
        raise ProcessObservationUnavailable("process observation budget exhausted")


class _LibProc:
    """Bounded ``ctypes`` binding to macOS libproc (no third-party dep).

    Uses ``proc_listpids`` / ``proc_pidinfo`` with the
    ``PROC_PIDTBSDINFO`` (parent/start identity) and ``PROC_PIDTASKINFO``
    (RSS/CPU) structs, plus read-only sysctl identity fallback on EPERM.
    Loaded lazily and only on macOS; raises
    :class:`LibProcUnavailable` when the host cannot provide it.
    """

    PROC_ALL_PIDS = 1
    PROC_PIDTBSDINFO = 3
    PROC_PIDTASKINFO = 4
    PROC_PIDLISTFD_SIZE = 30 * 4096

    def __init__(self) -> None:
        if not sys.platform == "darwin":
            raise LibProcUnavailable("libproc is macOS-only")
        try:
            import ctypes
            import ctypes.util

            lib_path = ctypes.util.find_library("proc") or "libproc.dylib"
            self._lib = ctypes.CDLL(lib_path, use_errno=True)
            self._ctypes = ctypes
        except (OSError, ImportError) as exc:
            raise LibProcUnavailable(f"cannot load libproc: {exc}") from exc
        self._setup_proc_pidinfo()
        self._kinfo_lock = threading.RLock()

    def _setup_proc_pidinfo(self) -> None:
        ctypes = self._ctypes

        class ProcBsdinfo(ctypes.Structure):
            _fields_ = [
                ("pbi_flags", ctypes.c_uint32),
                ("pbi_status", ctypes.c_uint32),
                ("pbi_xstatus", ctypes.c_uint32),
                ("pbi_pid", ctypes.c_int),
                ("pbi_ppid", ctypes.c_int),
                ("pbi_uid", ctypes.c_uint32),
                ("pbi_gid", ctypes.c_uint32),
                ("pbi_ruid", ctypes.c_uint32),
                ("pbi_rgid", ctypes.c_uint32),
                ("pbi_svuid", ctypes.c_uint32),
                ("pbi_svgid", ctypes.c_uint32),
                ("pbi_rfu", ctypes.c_uint32),
                ("pbi_comm", ctypes.c_char * 16),
                ("pbi_name", ctypes.c_char * 32),
                ("pbi_nfiles", ctypes.c_int),
                ("pbi_pgid", ctypes.c_int),
                ("pbi_pjobc", ctypes.c_int),
                ("pbi_e_tdev", ctypes.c_uint32),
                ("pbi_e_tpgid", ctypes.c_int),
                ("pbi_nice", ctypes.c_int),
                ("pbi_start_tvsec", ctypes.c_uint64),
                ("pbi_start_tvusec", ctypes.c_uint64),
            ]

        class ProcTaskinfo(ctypes.Structure):
            _fields_ = [
                ("pti_virtual_size", ctypes.c_uint64),
                ("pti_resident_size", ctypes.c_uint64),
                ("pti_total_user", ctypes.c_uint64),
                ("pti_total_system", ctypes.c_uint64),
                ("pti_threads_user", ctypes.c_uint64),
                ("pti_threads_system", ctypes.c_uint64),
                ("pti_policy", ctypes.c_int32),
                ("pti_faults", ctypes.c_int32),
                ("pti_pageins", ctypes.c_int32),
                ("pti_cow_faults", ctypes.c_int32),
                ("pti_messages_sent", ctypes.c_int32),
                ("pti_messages_received", ctypes.c_int32),
                ("pti_syscalls_mach", ctypes.c_int32),
                ("pti_syscalls_unix", ctypes.c_int32),
                ("pti_csw", ctypes.c_int32),
                ("pti_threadnum", ctypes.c_int32),
                ("pti_numrunning", ctypes.c_int32),
                ("pti_priority", ctypes.c_int32),
            ]

        self._ProcBsdinfo = ProcBsdinfo
        self._ProcTaskinfo = ProcTaskinfo
        lib = self._lib
        lib.proc_listpids.argtypes = [ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p, ctypes.c_int]
        lib.proc_listpids.restype = ctypes.c_int
        lib.proc_pidinfo.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_uint64, ctypes.c_void_p, ctypes.c_int]
        lib.proc_pidinfo.restype = ctypes.c_int

        # Size alone did not catch the missing pbi_xstatus word: padding kept
        # the old, shifted layout at 136 bytes too. Check a real kernel decode.
        if ctypes.sizeof(ProcBsdinfo) != 136:
            raise LibProcUnavailable("unexpected proc_bsdinfo ABI size")
        own_pid = os.getpid()
        info = ProcBsdinfo()
        count = lib.proc_pidinfo(
            own_pid, self.PROC_PIDTBSDINFO, 0, ctypes.byref(info), ctypes.sizeof(info)
        )
        if (count != ctypes.sizeof(info) or info.pbi_pid != own_pid
                or info.pbi_ppid != os.getppid()):
            raise LibProcUnavailable("proc_bsdinfo PID/PPID decode self-check failed")

    def list_pids(self) -> list[int]:
        ctypes = self._ctypes
        size = self.PROC_PIDLISTFD_SIZE
        if getattr(_checked_read, "deadline", None) is not None:
            size = min(size, (_CHECKED_ROW_LIMIT + 1) * ctypes.sizeof(ctypes.c_int))
        buf = (ctypes.c_int * (size // ctypes.sizeof(ctypes.c_int)))()
        n = self._lib.proc_listpids(self.PROC_ALL_PIDS, 0, buf, size)
        if getattr(_checked_read, "deadline", None) is not None:
            _check_observation_budget()
            if n <= 0 or n >= size or n % ctypes.sizeof(ctypes.c_int):
                raise ProcessObservationUnavailable("libproc enumeration unavailable or capped")
            _check_observation_budget(n // ctypes.sizeof(ctypes.c_int))
        if n <= 0:
            return []
        count = n // ctypes.sizeof(ctypes.c_int)
        return [int(buf[i]) for i in range(count) if buf[i] > 0]

    def bsd_info(self, pid: int) -> dict | None:
        import errno

        ctypes = self._ctypes
        info = self._ProcBsdinfo()
        ctypes.set_errno(0)
        n = self._lib.proc_pidinfo(
            pid, self.PROC_PIDTBSDINFO, 0, ctypes.byref(info), ctypes.sizeof(info)
        )
        native_errno = ctypes.get_errno()
        if n <= 0 and native_errno == errno.EPERM:
            # A permission refusal cannot establish non-membership. sysctl
            # supplies the SAME lineage identity, never invented task counters.
            try:
                fallback = self.kinfo_identity(pid)
                if fallback is not None:
                    fallback["fallback_errno"] = native_errno
                return fallback
            except ProcessObservationUnavailable as exc:
                if getattr(_checked_read, "deadline", None) is not None:
                    raise ProcessObservationUnavailable(
                        f"libproc identity observation unavailable: pid={pid} returned={n} "
                        f"expected={ctypes.sizeof(info)} errno={native_errno}; {exc}"
                    ) from exc
                return None
        if getattr(_checked_read, "deadline", None) is not None and n != ctypes.sizeof(info):
            if n <= 0 and native_errno == errno.ESRCH:
                return None
            # Numeric native evidence only; an opaque row is still unknown,
            # including EPERM. UID alone cannot exclude a descendant.
            raise ProcessObservationUnavailable(
                f"libproc identity observation unavailable: pid={pid} returned={n} "
                f"expected={ctypes.sizeof(info)} errno={native_errno} "
                f"errno_name={errno.errorcode.get(native_errno, 'UNKNOWN')}"
            )
        if n <= 0:
            return None
        return {
            "pid": int(info.pbi_pid),
            "ppid": info.pbi_ppid,
            "pgid": info.pbi_pgid,
            "start_identity": f"{info.pbi_start_tvsec}.{info.pbi_start_tvusec}",
            "comm": info.pbi_comm.decode("utf-8", "replace"),
            "state": int(info.pbi_status),
        }

    def _setup_kinfo_proc(self) -> None:
        """Darwin LP64 public ABI, pinned to Apple XNU xnu-11417.101.15.

        bsd/sys/{proc.h,sysctl.h,vm.h,_types.h}, arm/_types.h and
        sys/_types/_timeval.h define these widths/alignment on arm64/x86_64.
        Pointer fields are opaque: never dereferenced or exposed. No compiler
        or SDK is required in the frozen app. Unexpected ABIs refuse fallback.
        """
        import platform

        c = self._ctypes
        if (platform.machine() not in ("arm64", "x86_64")
                or c.sizeof(c.c_void_p) != 8 or c.sizeof(c.c_long) != 8):
            raise ProcessObservationUnavailable("unsupported kinfo_proc ABI")

        class Timeval(c.Structure):
            _fields_ = [("sec", c.c_long), ("usec", c.c_int32)]

        class ExternProc(c.Structure):
            _fields_ = [
                ("start", Timeval), ("vmspace", c.c_void_p), ("sigacts", c.c_void_p),
                ("flag", c.c_int32), ("state", c.c_int8), ("pid", c.c_int32),
                ("oppid", c.c_int32), ("dupfd", c.c_int32),
                ("user_stack", c.c_void_p), ("exit_thread", c.c_void_p),
                ("debugger", c.c_int32), ("sigwait", c.c_int32),
                ("estcpu", c.c_uint32), ("cpticks", c.c_int32), ("pctcpu", c.c_uint32),
                ("wchan", c.c_void_p), ("wmesg", c.c_void_p),
                ("swtime", c.c_uint32), ("slptime", c.c_uint32),
                ("realtimer", Timeval * 2), ("rtime", Timeval),
                ("uticks", c.c_uint64), ("sticks", c.c_uint64), ("iticks", c.c_uint64),
                ("traceflag", c.c_int32), ("tracep", c.c_void_p),
                ("siglist", c.c_int32), ("textvp", c.c_void_p), ("holdcnt", c.c_int32),
                ("sigmask", c.c_uint32), ("sigignore", c.c_uint32), ("sigcatch", c.c_uint32),
                ("priority", c.c_uint8), ("usrpri", c.c_uint8), ("nice", c.c_int8),
                ("comm", c.c_char * 17), ("pgrp", c.c_void_p), ("addr", c.c_void_p),
                ("xstat", c.c_uint16), ("acflag", c.c_uint16), ("ru", c.c_void_p),
            ]

        class Pcred(c.Structure):
            _fields_ = [
                ("lock", c.c_char * 72), ("ucred", c.c_void_p),
                ("ruid", c.c_uint32), ("svuid", c.c_uint32),
                ("rgid", c.c_uint32), ("svgid", c.c_uint32), ("refcnt", c.c_int32),
            ]

        class Ucred(c.Structure):
            _fields_ = [("ref", c.c_int32), ("uid", c.c_uint32),
                        ("ngroups", c.c_int16), ("groups", c.c_uint32 * 16)]

        class Vmspace(c.Structure):
            _fields_ = [("dummy", c.c_int32), ("dummy2", c.c_void_p),
                        ("dummy3", c.c_int32 * 5), ("dummy4", c.c_void_p * 3)]

        class Eproc(c.Structure):
            _fields_ = [
                ("paddr", c.c_void_p), ("sess", c.c_void_p),
                ("pcred", Pcred), ("ucred", Ucred), ("vm", Vmspace),
                ("ppid", c.c_int32), ("pgid", c.c_int32), ("jobc", c.c_int16),
                ("tdev", c.c_int32), ("tpgid", c.c_int32), ("tsess", c.c_void_p),
                ("wmesg", c.c_char * 8), ("xsize", c.c_int32),
                ("xrssize", c.c_int16), ("xccount", c.c_int16), ("xswrss", c.c_int16),
                ("flag", c.c_int32), ("login", c.c_char * 12), ("spare", c.c_int32 * 4),
            ]

        class KinfoProc(c.Structure):
            _fields_ = [("proc", ExternProc), ("eproc", Eproc)]

        if (c.sizeof(KinfoProc) != 648 or c.sizeof(ExternProc) != 296
                or ExternProc.pid.offset != 40 or ExternProc.state.offset != 36
                or KinfoProc.eproc.offset + Eproc.ppid.offset != 560
                or KinfoProc.eproc.offset + Eproc.pgid.offset != 564
                or KinfoProc.eproc.offset + Eproc.ucred.offset + Ucred.uid.offset != 420):
            raise ProcessObservationUnavailable("unexpected kinfo_proc layout")
        libc = c.CDLL("/usr/lib/libSystem.B.dylib", use_errno=True)
        libc.sysctl.argtypes = [c.POINTER(c.c_int), c.c_uint, c.c_void_p,
                               c.POINTER(c.c_size_t), c.c_void_p, c.c_size_t]
        libc.sysctl.restype = c.c_int
        self._KinfoProc = KinfoProc
        self._sysctl = libc.sysctl
        try:
            own_pid = os.getpid()
            own = self.kinfo_identity(own_pid)
            bsd = self._ProcBsdinfo()
            n = self._lib.proc_pidinfo(own_pid, self.PROC_PIDTBSDINFO, 0,
                                       c.byref(bsd), c.sizeof(bsd))
            if (own is None or n != c.sizeof(bsd) or own["pid"] != own_pid
                    or own["ppid"] != os.getppid() or own["pgid"] != os.getpgid(own_pid)
                    or own["start_identity"] != f"{bsd.pbi_start_tvsec}.{bsd.pbi_start_tvusec}"):
                raise ProcessObservationUnavailable("kinfo_proc identity self-check failed")
        except Exception:
            # A failed self-check must never leave a reusable unvalidated ABI.
            del self._KinfoProc
            del self._sysctl
            raise

    def kinfo_identity(self, pid: int) -> dict | None:
        """One bounded CTL_KERN/KERN_PROC/KERN_PROC_PID identity read.

        ESRCH or successful zero bytes means gone. All other failures and
        malformed responses remain unknown, even outside checked scans.
        """
        import errno

        c = self._ctypes
        with self._kinfo_lock:
            if not hasattr(self, "_KinfoProc"):
                try:
                    self._setup_kinfo_proc()
                except (OSError, AttributeError) as exc:
                    raise ProcessObservationUnavailable("kinfo_proc binding unavailable") from exc
        info = self._KinfoProc()
        size = c.c_size_t(c.sizeof(info))
        mib = (c.c_int * 4)(1, 14, 1, pid)
        c.set_errno(0)
        returned = self._sysctl(mib, 4, c.byref(info), c.byref(size), None, 0)
        native_errno = c.get_errno()
        _check_observation_budget()
        if (returned != 0 and native_errno == errno.ESRCH) or (returned == 0 and size.value == 0):
            return None
        if (returned != 0 or size.value != c.sizeof(info) or info.proc.pid != pid
                or info.proc.start.sec <= 0 or not 0 <= info.proc.start.usec < 1_000_000
                or info.eproc.ppid < 0 or info.eproc.pgid < 0 or not 1 <= info.proc.state <= 5):
            raise ProcessObservationUnavailable(
                f"sysctl identity unavailable: pid={pid} returned={returned} "
                f"size={size.value} expected={c.sizeof(info)} errno={native_errno}"
            )
        return {
            "pid": int(info.proc.pid), "ppid": int(info.eproc.ppid),
            "pgid": int(info.eproc.pgid),
            "start_identity": f"{info.proc.start.sec}.{info.proc.start.usec}",
            "state": int(info.proc.state), "uid": int(info.eproc.ucred.uid),
            "ruid": int(info.eproc.pcred.ruid), "identity_source": "sysctl",
        }

    def task_info(self, pid: int) -> dict | None:
        ctypes = self._ctypes
        info = self._ProcTaskinfo()
        n = self._lib.proc_pidinfo(
            pid, self.PROC_PIDTASKINFO, 0, ctypes.byref(info), ctypes.sizeof(info)
        )
        if n != ctypes.sizeof(info):
            return None
        return {
            "rss_bytes": int(info.pti_resident_size),
            "cpu_seconds": (info.pti_total_user + info.pti_total_system) / 1_000_000_000.0,
        }


class LibProcUnavailable(RuntimeError):
    """libproc cannot be loaded on this host (non-macOS or missing library)."""


# Cleanup-only strict reads. The S1 binding above is intentionally unchanged.
import ctypes as ct
import errno
import re
import stat
import subprocess
import uuid
from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True)
class NativeRead:
    kind: str
    value: object = None
    returned: int = 0
    expected: int = 0
    errno: int = 0


class NativeGap(RuntimeError):
    """Typed native uncertainty, including numeric syscall evidence."""

    def __init__(self, operation: str, read: NativeRead) -> None:
        self.read = read
        super().__init__(f'{operation}:{read.kind}:returned={read.returned}:'
                         f'expected={read.expected}:errno={read.errno}')


class VinfoStat(ct.Structure):
    _fields_ = [('dev', ct.c_uint32), ('mode', ct.c_uint16), ('nlink', ct.c_uint16),
                ('ino', ct.c_uint64), ('uid', ct.c_uint32), ('gid', ct.c_uint32)] + [
        (name, ct.c_int64) for name in ('atime', 'atimensec', 'mtime', 'mtimensec',
        'ctime', 'ctimensec', 'birthtime', 'birthtimensec', 'size', 'blocks')] + [
        ('blksize', ct.c_int32), ('flags', ct.c_uint32), ('gen', ct.c_uint32),
        ('rdev', ct.c_uint32), ('qspare', ct.c_int64 * 2)]


class VnodeInfo(ct.Structure):
    _fields_ = [('vi_stat', VinfoStat), ('vi_type', ct.c_int32),
                ('vi_pad', ct.c_int32), ('vi_fsid', ct.c_int32 * 2)]


class VnodePath(ct.Structure):
    _fields_ = [('info', VnodeInfo), ('path', ct.c_char * 1024)]


class FileInfo(ct.Structure):
    _fields_ = [('openflags', ct.c_uint32), ('status', ct.c_uint32),
                ('offset', ct.c_int64), ('type', ct.c_int32), ('guardflags', ct.c_uint32)]


class FdPath(ct.Structure):
    _fields_ = [('file', FileInfo), ('vnode', VnodePath)]


class CwdPaths(ct.Structure):
    _fields_ = [('cwd', VnodePath), ('root', VnodePath)]


class FdInfo(ct.Structure):
    _fields_ = [('fd', ct.c_int32), ('kind', ct.c_uint32)]


class ThreadInfo(ct.Structure):
    _fields_ = [('user', ct.c_uint64), ('system', ct.c_uint64)] + [
        (name, ct.c_int32) for name in ('cpu', 'policy', 'state', 'flags',
        'sleep', 'curpri', 'priority', 'maxpriority')] + [('name', ct.c_char * 64)]


class ThreadPath(ct.Structure):
    _fields_ = [('thread', ThreadInfo), ('vnode', VnodePath)]


class RegionInfo(ct.Structure):
    _fields_ = [(name, ct.c_uint32) for name in ('protection', 'max_protection',
        'inheritance', 'flags')] + [('offset', ct.c_uint64)] + [
        (name, ct.c_uint32) for name in ('behavior', 'wired', 'tag', 'resident',
        'shared_private', 'swapped', 'dirtied', 'refs', 'shadow', 'share',
        'private_resident', 'shared_resident', 'object', 'depth')] + [
        ('address', ct.c_uint64), ('size', ct.c_uint64)]


class RegionPath(ct.Structure):
    _fields_ = [('region', RegionInfo), ('vnode', VnodePath)]


def abi_layout() -> dict:
    """Pinned XNU xnu-11417.101.15 LP64 layout, checked before native reads."""
    return {'sizes': [ct.sizeof(t) for t in (VinfoStat, VnodeInfo, VnodePath,
        FileInfo, FdPath, CwdPaths, FdInfo, ThreadInfo, ThreadPath, RegionInfo, RegionPath)],
        'offsets': [VnodePath.path.offset, FdPath.vnode.offset, CwdPaths.root.offset,
                    ThreadPath.vnode.offset, RegionPath.vnode.offset,
                    RegionInfo.address.offset, RegionInfo.size.offset]}


def _require_abi() -> None:
    import platform
    if (platform.machine() not in ('arm64', 'x86_64') or ct.sizeof(ct.c_void_p) != 8
            or abi_layout() != {'sizes': [136, 152, 1176, 24, 1200, 2352, 8, 112, 1288, 96, 1272],
                               'offsets': [152, 24, 1176, 112, 96, 80, 88]}):
        raise ProcessObservationUnavailable('unexpected cleanup libproc ABI')


def boot_uuid(admit: Callable[[], None] = lambda: None) -> str:
    """Current kernel boot identity; no subprocess or app-local substitute."""
    admit()
    if sys.platform != 'darwin':
        raise LibProcUnavailable('Darwin boot identity is macOS-only')
    libc = ct.CDLL('/usr/lib/libSystem.B.dylib', use_errno=True)
    call = libc.sysctlbyname
    call.argtypes = [ct.c_char_p, ct.c_void_p, ct.POINTER(ct.c_size_t), ct.c_void_p, ct.c_size_t]
    call.restype = ct.c_int
    value = ct.create_string_buffer(64)
    size = ct.c_size_t(len(value))
    ct.set_errno(0)
    count = call(b'kern.bootsessionuuid', value, ct.byref(size), None, 0)
    code = ct.get_errno()
    admit()
    raw = value.raw[:size.value]
    if count != 0 or not 0 < size.value <= 64 or not raw.endswith(b'\0'):
        raise NativeGap('boot', NativeRead('error', returned=count, expected=64, errno=code))
    text = raw[:-1].decode('ascii', 'strict')
    if re.fullmatch(r'[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}', text) is None:
        raise ProcessObservationUnavailable('malformed boot UUID')
    return str(uuid.UUID(text))


class DarwinReader:
    """Cleanup reads never inherit the sampling API's missing/zero shortcuts."""

    def __init__(self, admit: Callable[[], None]) -> None:
        self.admit = admit
        self.admit()
        _require_abi()
        self.binding = _LibProc()
        self.lib = self.binding._lib
        self.lib.proc_pidfdinfo.argtypes = [ct.c_int, ct.c_int, ct.c_int, ct.c_void_p, ct.c_int]
        self.lib.proc_pidfdinfo.restype = ct.c_int
        self.lib.proc_pidpath.argtypes = [ct.c_int, ct.c_void_p, ct.c_uint32]
        self.lib.proc_pidpath.restype = ct.c_int
        self.admit()
        # Native path AND inode checks catch a plausible-size but shifted decode.
        own = os.getpid()
        cwd = self.struct(own, 9, CwdPaths).cwd
        self._self_vnode(cwd, os.stat('.'))
        fd = os.open('.', os.O_RDONLY | os.O_DIRECTORY)
        try:
            self._self_vnode(self.fd_path(own, fd).vnode, os.fstat(fd))
        finally:
            os.close(fd)
        self.admit()

    def _self_vnode(self, node: VnodePath, value: os.stat_result) -> None:
        identity = decode_vnode(node)
        path = decode_vnode_path(node)
        if identity != (value.st_dev, value.st_ino) or not os.path.samestat(os.stat(path), value):
            raise ProcessObservationUnavailable('native vnode/path self-check failed')

    def call(self, function: Callable, args: tuple, value: Any, *, variable: bool = False, allow_empty: bool = False) -> NativeRead:
        self.admit()
        size = ct.sizeof(value)
        ct.set_errno(0)
        count = function(*args, ct.byref(value), size)
        code = ct.get_errno()  # Capture before any other native/library call.
        self.admit()
        kind = 'ok'
        if count <= 0:
            kind = ('gone' if code == errno.ESRCH else 'denied'
                    if code in (errno.EPERM, errno.EACCES) else 'error')
            if count == 0 and code == 0 and variable and allow_empty:
                kind = 'ok'
        elif (variable and count >= size) or (not variable and count != size):
            kind = 'capped' if variable else 'short_read'
        return NativeRead(kind, value, count, size, code)

    def struct(self, pid: int, flavor: int, shape: type, arg: int = 0) -> Any:
        read = self.call(self.lib.proc_pidinfo, (pid, flavor, arg), shape())
        if read.kind != 'ok':
            raise NativeGap(f'pid={pid}:flavor={flavor}', read)
        return read.value

    def identity(self, pid: int) -> tuple:
        value = self.struct(pid, 3, self.binding._ProcBsdinfo)
        if (value.pbi_pid != pid or value.pbi_start_tvsec <= 0
                or not 0 <= value.pbi_start_tvusec < 1_000_000 or not 1 <= value.pbi_status <= 5):
            raise ProcessObservationUnavailable(f'pid={pid}:malformed_identity')
        return (pid, f'{value.pbi_start_tvsec}.{value.pbi_start_tvusec}',
                value.pbi_uid, value.pbi_ruid, value.pbi_svuid,
                value.pbi_gid, value.pbi_rgid, value.pbi_svgid,
                value.pbi_flags, bytes(value.pbi_comm), bytes(value.pbi_name))

    def population(self, cap: int) -> tuple[int, ...]:
        value = (ct.c_int * (cap + 1))()
        read = self.call(self.lib.proc_listpids, (1, 0), value, variable=True)
        if read.kind != 'ok' or read.returned % 4:
            raise NativeGap('population', read)
        pids = [int(value[i]) for i in range(read.returned // 4) if value[i] > 0]
        if not pids or len(pids) != len(set(pids)) or len(pids) > cap:
            raise ProcessObservationUnavailable('empty/duplicate/capped population')
        return tuple(sorted(pids))

    def table(self, pid: int, flavor: int, shape: type, cap: int, *, empty: bool = False) -> tuple:
        value = (shape * (cap + 1))()
        read = self.call(self.lib.proc_pidinfo, (pid, flavor, 0), value,
                         variable=True, allow_empty=empty)
        if read.kind != 'ok' or read.returned % ct.sizeof(shape):
            raise NativeGap(f'pid={pid}:table={flavor}', read)
        rows = tuple(value[i] for i in range(read.returned // ct.sizeof(shape)))
        if len(rows) > cap:
            raise ProcessObservationUnavailable('table capped')
        return rows

    def fd_path(self, pid: int, fd: int) -> FdPath:
        read = self.call(self.lib.proc_pidfdinfo, (pid, fd, 2), FdPath())
        if read.kind != 'ok':
            raise NativeGap(f'pid={pid}:fd={fd}', read)
        return read.value

    def executable(self, pid: int) -> str:
        value = ct.create_string_buffer(4096)
        read = self.call(self.lib.proc_pidpath, (pid,), value, variable=True)
        if read.kind != 'ok':
            raise NativeGap(f'pid={pid}:text', read)
        raw, nul, _ = value.raw.partition(b'\0')
        if not raw or not nul or not raw.startswith(b'/') or len(raw) != read.returned:
            raise ProcessObservationUnavailable(f'pid={pid}:invalid_executable_path')
        return os.fsdecode(raw)


def decode_vnode(node: VnodePath, *, optional: bool = False) -> tuple[int, int] | None:
    """Validate native identity independently of possibly unavailable path evidence."""
    if optional and not any(bytes(node)):
        return None
    value = node.info.vi_stat
    if not value.ino or not value.mode or node.info.vi_type <= 0:
        raise ProcessObservationUnavailable('incomplete vnode identity')
    return int(value.dev), int(value.ino)


def decode_vnode_path(node: VnodePath) -> str:
    raw = bytes(node)[VnodePath.path.offset:VnodePath.path.offset + 1024]
    content, nul, _ = raw.partition(b'\0')
    if not content or not nul or not content.startswith(b'/'):
        raise ProcessObservationUnavailable('incomplete vnode path')
    return os.fsdecode(content)


def parse_mount_table(text: str) -> list[dict]:
    """BSD mount evidence, explicitly attributed; never a procfs/findmnt row."""
    rows = []
    if len(text.encode()) > 1 << 20:
        raise ValueError('mount_table_cap')
    for line in text.splitlines():
        match = re.fullmatch(r'(.+) on (/.*?) \(([^()\r\n]+)\)', line)
        if not match or os.path.normpath(match[2]) != match[2] or len(rows) >= 4096:
            raise ValueError('malformed_mount_table')
        fields = match[3].split(', ')
        rows.append({'target': match[2], 'source': match[1], 'fstype': fields[0],
                     'options': match[3], 'platform': 'darwin', 'provenance': 'bsd_mount'})
    if not rows or not any(row['target'] == '/' for row in rows):
        raise ValueError('empty_or_incomplete_mount_table')
    if len({row['target'] for row in rows}) != len(rows):
        raise ValueError('ambiguous_mount_table')
    return rows


def scan_paths(roots: dict[str, tuple[int, int]], bounds: Any, *, agent_uid: int,
               clock: Callable[[], float] = time.monotonic, started: float | None = None) -> dict:
    """Bounded same-user observation; known use dominates every uncertainty.

    Null public region vnodes cannot distinguish anonymous memory from lost
    backing-vnode acquisition. Such negatives deliberately remain UNKNOWN.
    """
    started = clock() if started is None else started
    hits, reasons, cycles = [], [], []
    lanes = {key: False for key in ('cwd', 'root', 'fd', 'text', 'thread', 'region')}
    lane_seen = {key: set() for key in lanes}
    lane_failed = set()
    coverage = {'platform': 'darwin', 'agent_uid': agent_uid, 'self_pid': str(os.getpid()),
                'boot_uuid': None, 'abi_validated': False, 'population_complete': False,
                'total_pids': 0, 'same_user': 0, 'root': 0, 'other_user': 0,
                'scanned': 0, 'enum_passes': 0, 'lanes': lanes}
    identities = {}
    inventory = dict((identity, root) for root, identity in roots.items())
    mounts = set()
    snapshots = {}
    operations = 0
    max_operations = bounds.max_maps_lines + bounds.max_fds + bounds.max_pids

    def admit() -> None:
        nonlocal operations
        operations += 1
        if clock() - started >= bounds.deadline_seconds or operations > max_operations:
            raise ProcessObservationUnavailable('deadline_or_aggregate_cap')

    def gap(exc: object) -> None:
        # Bounded diagnostic output; uncertainty is latched even after the cap.
        if len(reasons) < 64:
            reasons.append(str(exc)[:240])

    def metadata(path: str) -> tuple:
        admit()
        value = os.lstat(path)
        return (value.st_dev, value.st_ino, value.st_mode, value.st_size,
                value.st_mtime_ns, value.st_ctime_ns, value.st_nlink)

    def collect_inventory() -> None:
        for root in roots:
            stack = [root]
            while stack:
                admit()
                path = stack.pop()
                sig = metadata(path)
                if (sig[0] != roots[root][0] or stat.S_ISLNK(sig[2])
                        or (path != root and path in mounts)):
                    raise ProcessObservationUnavailable('candidate_alias_or_mount')
                if len(snapshots) >= bounds.max_maps_lines:
                    raise ProcessObservationUnavailable('candidate_inventory_cap')
                snapshots[path] = sig
                inventory[(sig[0], sig[1])] = path
                if stat.S_ISDIR(sig[2]):
                    with os.scandir(path) as entries:
                        for entry in entries:
                            admit()
                            if len(stack) + len(snapshots) >= bounds.max_maps_lines:
                                raise ProcessObservationUnavailable('candidate_inventory_cap')
                            stack.append(entry.path)

    def match(pid: int, kind: str, node: VnodePath, *, optional: bool = False) -> tuple | None:
        admit()
        identity = decode_vnode(node, optional=optional)
        if identity is None:
            return None
        matched = inventory.get(identity)
        try:
            path = decode_vnode_path(node)
        except ProcessObservationUnavailable:
            # vn_getpath can fail after native identity was filled. Keep known
            # inode use, then latch the incomplete lane through the normal gap.
            if matched and len(hits) < 64:
                hits.append({'pid': str(pid), 'kind': kind, 'path': '',
                             'dev_ino': list(identity), 'candidate_path': matched})
            raise
        canonical = os.path.realpath(path)
        lexical = any(path == root or path.startswith(root.rstrip('/') + '/') for root in roots)
        beneath = any(canonical == root or canonical.startswith(root.rstrip('/') + '/') for root in roots)
        try:
            current = os.stat(path)
            agrees = (current.st_dev, current.st_ino) == identity
        except OSError:
            agrees = False
        if matched or (agrees and (lexical or beneath)):
            if len(hits) < 64:
                hits.append({'pid': str(pid), 'kind': kind, 'path': path,
                             'dev_ino': list(identity), 'candidate_path': matched})
        elif not agrees:
            gap(f'pid={pid}:{kind}:renamed_or_unresolved_vnode')
        return identity, path

    def references(reader: DarwinReader, pid: int, selected: tuple[str, ...]) -> None:
        # Each lane is independent: a denied mapping must not hide a cwd/fd hit.
        for lane in selected:
            try:
                if lane in ('cwd', 'root'):
                    value = reader.struct(pid, 9, CwdPaths)
                    match(pid, lane, getattr(value, lane), optional=lane == 'root')
                elif lane == 'fd':
                    def frontier() -> tuple:
                        rows = reader.table(pid, 1, FdInfo, bounds.max_fds, empty=True)
                        result = tuple(sorted((int(row.fd), int(row.kind)) for row in rows))
                        if any(fd < 0 for fd, _ in result) or len({fd for fd, _ in result}) != len(result):
                            raise ProcessObservationUnavailable('malformed fd frontier')
                        return result
                    before = frontier()
                    observed = []
                    for fd, kind in before:
                        admit()
                        if kind == 1:
                            observed.append((fd, match(pid, 'fd', reader.fd_path(pid, fd).vnode)))
                    if before != frontier():
                        raise ProcessObservationUnavailable('fd_churn')
                    for fd, identity in observed:
                        if identity != match(pid, 'fd', reader.fd_path(pid, fd).vnode):
                            raise ProcessObservationUnavailable('fd_reuse')
                elif lane == 'text':
                    path = reader.executable(pid)
                    admit()
                    canonical = os.path.realpath(path)
                    value = os.stat(path)
                    if ((value.st_dev, value.st_ino) in inventory or any(
                            canonical == root or canonical.startswith(root.rstrip('/') + '/') for root in roots)):
                        if len(hits) < 64:
                            hits.append({'pid': str(pid), 'kind': 'text', 'path': path,
                                         'dev_ino': [value.st_dev, value.st_ino]})
                elif lane == 'thread':
                    before = tuple(sorted(int(v) for v in reader.table(pid, 6, ct.c_uint64, bounds.max_threads)))
                    if not before or len(set(before)) != len(before) or any(v <= 0 for v in before):
                        raise ProcessObservationUnavailable('invalid thread frontier')
                    observed = []
                    for tid in before:
                        observed.append(match(pid, 'thread', reader.struct(pid, 10, ThreadPath, tid).vnode, optional=True))
                    after = tuple(sorted(int(v) for v in reader.table(pid, 6, ct.c_uint64, bounds.max_threads)))
                    if before != after:
                        raise ProcessObservationUnavailable('thread_churn')
                    for tid, identity in zip(before, observed):
                        if identity != match(pid, 'thread', reader.struct(pid, 10, ThreadPath, tid).vnode, optional=True):
                            raise ProcessObservationUnavailable('thread_cwd_changed')
                else:
                    address, count, byte_count = 0, 0, 0
                    while True:
                        admit()
                        try:
                            row = reader.struct(pid, 8, RegionPath, address)
                        except NativeGap as exc:
                            if count and exc.read.returned == 0 and exc.read.errno == errno.EINVAL:
                                break
                            raise
                        count += 1
                        byte_count += ct.sizeof(row)
                        end = int(row.region.address) + int(row.region.size)
                        if (count > bounds.max_maps_lines or byte_count > bounds.max_maps_bytes
                                or row.region.address < address or not row.region.size
                                or end <= address or end >= 1 << 64 or row.region.flags & 1):
                            raise ProcessObservationUnavailable('region_cap_or_nonadvancing_or_submap')
                        decoded = match(pid, 'region', row.vnode, optional=True)
                        if decoded is None:
                            lane_failed.add('region')
                            gap(f'pid={pid}:region:backing_vnode_unestablished')
                        address = end
                lane_seen[lane].add(pid)
            except (OSError, ValueError, ProcessObservationUnavailable, NativeGap) as exc:
                lane_failed.add(lane)
                gap(exc)

    try:
        admit()
        if (agent_uid == 0 or any(type(v) is not int or v <= 0 for v in (
                bounds.max_pids, bounds.max_threads, bounds.max_fds, bounds.max_maps_lines,
                bounds.max_maps_bytes, bounds.max_enum_passes))):
            raise ProcessObservationUnavailable('invalid scope or bounds')
        if (bounds.max_pids > 4096 or bounds.max_threads > 256 or bounds.max_fds > 4096
                or bounds.max_maps_lines > 20000 or bounds.max_maps_bytes > 4 * 1024 * 1024
                or bounds.max_enum_passes > 6 or not 0 < bounds.deadline_seconds <= 30):
            raise ProcessObservationUnavailable('cleanup bounds exceed policy caps')
        import tempfile
        # Same existing BSD mount tool as the observer, never GNU findmnt.
        with tempfile.TemporaryFile() as output:
            admit()
            process = subprocess.run(['/sbin/mount'], stdout=output, stderr=subprocess.DEVNULL,
                timeout=max(.001, bounds.deadline_seconds - (clock() - started)))
            admit()
            output.seek(0)
            raw = output.read((1 << 20) + 1)
        if process.returncode or len(raw) > 1 << 20:
            raise ProcessObservationUnavailable('mount evidence unavailable or capped')
        mounts.update(row['target'] for row in parse_mount_table(os.fsdecode(raw)))
        try:
            collect_inventory()
        except (OSError, ProcessObservationUnavailable) as exc:
            gap(exc)
        reader = DarwinReader(admit)
        coverage['abi_validated'] = True
        boot = coverage['boot_uuid'] = boot_uuid(admit)
        previous = None
        converged = False
        pending = []
        # Reserve one original census pass for final post-reference identity proof.
        for index in range(max(0, bounds.max_enum_passes - 1)):
            population = reader.population(bounds.max_pids)
            coverage['total_pids'] = len(population)
            coverage['enum_passes'] += 1
            cycle = {}
            for pid in reversed(population):
                admit()
                try:
                    identity = reader.identity(pid)
                    cycle[pid] = identity
                    if pid in identities:
                        if identities[pid] != identity:
                            gap(f'pid={pid}:reuse_exec_or_credentials_changed')
                        continue
                    identities[pid] = identity
                    uids = identity[2:5]
                    if len(set(uids)) != 1:
                        gap(f'pid={pid}:mixed_credentials')
                    elif uids == (agent_uid,) * 3:
                        coverage['same_user'] += 1
                        coverage['scanned'] += 1
                        references(reader, pid, ("cwd", "root", "fd", "text"))
                        pending.append(pid)
                    elif uids == (0, 0, 0):
                        coverage['root'] += 1
                    else:
                        coverage['other_user'] += 1
                    if reader.identity(pid) != identity:
                        gap(f'pid={pid}:identity_changed')
                except (OSError, ValueError, ProcessObservationUnavailable, NativeGap) as exc:
                    gap(exc)
            cycles.append({'phase': 'enumerate_pass', 'pass': index + 1, 'new': len(set(cycle) - set(previous or {}))})
            if previous == cycle and len(cycle) == len(population):
                converged = True
                previous = cycle
                break
            previous = cycle
        for selected in (("thread",), ("region",)):
            for pid in pending:
                admit()
                try:
                    if reader.identity(pid) != identities[pid]:
                        gap(f'pid={pid}:identity_changed_before_slow_lanes')
                        continue
                    references(reader, pid, selected)
                    if reader.identity(pid) != identities[pid]:
                        gap(f'pid={pid}:identity_changed_after_slow_lanes')
                except (OSError, ProcessObservationUnavailable, NativeGap) as exc:
                    gap(exc)
        # No negative clearance from a single region walk: public vnode absence
        # and mapping churn cannot establish the documented complete maps lane.
        lane_failed.add('region')
        gap('public_region_negative_coverage_unestablished')
        final_population = reader.population(bounds.max_pids)
        coverage['enum_passes'] += 1
        final_identities = {}
        for pid in final_population:
            admit()
            try:
                final_identities[pid] = reader.identity(pid)
            except (OSError, ProcessObservationUnavailable, NativeGap) as exc:
                gap(exc)
        coverage['population_complete'] = (converged and final_identities == previous
                                           and len(final_identities) == len(final_population))
        cycles.append({'phase': 'enumerate_pass', 'pass': coverage['enum_passes'],
                       'new': len(set(final_population) - set(previous or {}))})
        if not coverage['population_complete']:
            gap('population_nonconvergent_or_final_identity_changed')
        if boot_uuid(admit) != boot:
            gap('boot_changed')
        for path, signature in snapshots.items():
            if metadata(path) != signature:
                gap('candidate_metadata_changed')
        # Fresh complete inventory catches newly arriving names as well.
        old_snapshot = dict(snapshots)
        snapshots.clear()
        collect_inventory()
        if snapshots != old_snapshot:
            gap('candidate_inventory_changed')
        admit()
    except (OSError, ValueError, AttributeError, TypeError, OverflowError, subprocess.TimeoutExpired, ProcessObservationUnavailable, NativeGap, LibProcUnavailable) as exc:
        gap(exc)
    for lane in lanes:
        lanes[lane] = (lane not in lane_failed and coverage['scanned'] > 0
                       and len(lane_seen[lane]) == coverage['scanned'])
    return {'hits': hits, 'reasons': reasons, 'coverage': coverage, 'cycles': cycles,
            'state': 'blocked' if hits else 'unknown' if reasons or not all(lanes.values())
            or not coverage['population_complete'] else 'clear_observation'}


def validate_clear_scan(scan: dict, candidate: str, containing: str) -> None:
    """Darwin-only receipt branch; native job/authentication checks stay outside."""
    if sys.platform != 'darwin' or set(scan) != {'state', 'target', 'hits', 'reasons', 'coverage', 'exempt', 'cycles'}:
        raise ValueError('wrong_platform_or_scan_schema')
    if (scan['state'] != 'clear_observation' or scan['hits'] != [] or scan['reasons'] != []
            or scan['exempt'] != [] or scan['target'] != os.path.realpath(candidate)):
        raise ValueError('scan_not_clear')
    coverage = scan['coverage']
    keys = {'platform', 'agent_uid', 'self_pid', 'boot_uuid', 'abi_validated',
            'population_complete', 'total_pids', 'same_user', 'root', 'other_user',
            'scanned', 'enum_passes', 'lanes', 'target_dev_ino', 'containing_worktree',
            'containing_worktree_dev_ino'}
    if not isinstance(coverage, dict) or set(coverage) != keys or coverage['platform'] != 'darwin':
        raise ValueError('invalid_darwin_coverage')
    if coverage['agent_uid'] == 0 or coverage['agent_uid'] != os.getuid() or coverage['abi_validated'] is not True or coverage['population_complete'] is not True:
        raise ValueError('incomplete_darwin_scope')
    if coverage['lanes'] != dict.fromkeys(('cwd', 'root', 'fd', 'text', 'thread', 'region'), True) or any(
            type(value) is not bool for value in coverage['lanes'].values()):
        raise ValueError('incomplete_darwin_lanes')
    if coverage['boot_uuid'] != boot_uuid() or not isinstance(coverage['self_pid'], str) or not coverage['self_pid'].isdigit():
        raise ValueError('invalid_boot_or_pid')
    for key in ('agent_uid', 'total_pids', 'same_user', 'root', 'other_user', 'scanned', 'enum_passes'):
        if type(coverage[key]) is not int or coverage[key] < 0:
            raise ValueError('invalid_darwin_counter')
    if (coverage['enum_passes'] < 2 or coverage['total_pids'] < 1
            or coverage['same_user'] != coverage['scanned']
            or coverage['total_pids'] != coverage['same_user'] + coverage['root'] + coverage['other_user']):
        raise ValueError('incomplete_population_accounting')
    target = os.stat(candidate, follow_symlinks=False)
    if coverage['target_dev_ino'] != [target.st_dev, target.st_ino]:
        raise ValueError('target_identity_changed')
    if os.path.basename(candidate) in ('node_modules', '.venv'):
        value = os.stat(containing, follow_symlinks=False)
        if (coverage['containing_worktree'] != os.path.realpath(containing)
                or coverage['containing_worktree_dev_ino'] != [value.st_dev, value.st_ino]):
            raise ValueError('containing_identity_changed')
    elif coverage['containing_worktree'] is not None or coverage['containing_worktree_dev_ino'] is not None:
        raise ValueError('unexpected_containing_worktree')
    if not isinstance(scan['cycles'], list) or len(scan['cycles']) != coverage['enum_passes']:
        raise ValueError('invalid_darwin_cycles')
    for row in scan['cycles']:
        if (not isinstance(row, dict) or set(row) != {'phase', 'pass', 'new'}
                or row['phase'] != 'enumerate_pass'
                or any(type(row[k]) is not int or row[k] < 0 for k in ('pass', 'new'))):
            raise ValueError('invalid_darwin_cycle')
