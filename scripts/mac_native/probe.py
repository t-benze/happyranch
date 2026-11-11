#!/usr/bin/env python3
"""Manual, stdlib-only Darwin observations; JSON stdout, human summary stderr."""

from __future__ import annotations

import argparse
import ctypes as ct
import ctypes.util
import errno
import hashlib
import json
import os
import platform
import re
import resource
import selectors
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
SUITES = ("truth", "teardown", "disk-sweep")
PROC_PIDTBSDINFO = 3


class RepoBsdinfoCopy(ct.Structure):
    """Byte-identical fallback: process_census._LibProc at 9ed24be93 (missing xstatus)."""

    _fields_ = [
        ("pbi_flags", ct.c_uint32),
        ("pbi_status", ct.c_uint32),
        ("pbi_pid", ct.c_int),
        ("pbi_ppid", ct.c_int),
        ("pbi_uid", ct.c_uint32),
        ("pbi_gid", ct.c_uint32),
        ("pbi_ruid", ct.c_uint32),
        ("pbi_rgid", ct.c_uint32),
        ("pbi_svuid", ct.c_uint32),
        ("pbi_svgid", ct.c_uint32),
        ("pbi_rfu", ct.c_uint32),
        ("pbi_comm", ct.c_char * 16),
        ("pbi_name", ct.c_char * 32),
        ("pbi_nfiles", ct.c_int),
        ("pbi_pgid", ct.c_int),
        ("pbi_pjobc", ct.c_int),
        ("pbi_e_tdev", ct.c_uint32),
        ("pbi_e_tpgid", ct.c_int),
        ("pbi_nice", ct.c_int),
        ("pbi_start_tvsec", ct.c_uint64),
        ("pbi_start_tvusec", ct.c_uint64),
    ]


class CorrectedBsdinfo(ct.Structure):
    """Darwin proc_bsdinfo ABI, including the word after pbi_status.

    Reference: apple-oss-distributions/xnu, bsd/sys/proc_info.h.
    Field aliases retain repo spelling; signed/unsigned positive PIDs share bytes.
    """

    _fields_ = [
        ("pbi_flags", ct.c_uint32),
        ("pbi_status", ct.c_uint32),
        ("pbi_xstatus", ct.c_uint32),
        ("pbi_pid", ct.c_uint32),
        ("pbi_ppid", ct.c_uint32),
        ("pbi_uid", ct.c_uint32),
        ("pbi_gid", ct.c_uint32),
        ("pbi_ruid", ct.c_uint32),
        ("pbi_rgid", ct.c_uint32),
        ("pbi_svuid", ct.c_uint32),
        ("pbi_svgid", ct.c_uint32),
        ("pbi_rfu", ct.c_uint32),
        ("pbi_comm", ct.c_char * 16),
        ("pbi_name", ct.c_char * 32),
        ("pbi_nfiles", ct.c_uint32),
        ("pbi_pgid", ct.c_uint32),
        ("pbi_pjobc", ct.c_uint32),
        ("pbi_e_tdev", ct.c_uint32),
        ("pbi_e_tpgid", ct.c_uint32),
        ("pbi_nice", ct.c_int32),
        ("pbi_start_tvsec", ct.c_uint64),
        ("pbi_start_tvusec", ct.c_uint64),
    ]


class Statfs64(ct.Structure):
    """64-bit Darwin statfs layout (xnu bsd/sys/mount.h; MAXPATHLEN=1024)."""

    _fields_ = [
        ("bsize", ct.c_uint32),
        ("iosize", ct.c_int32),
        ("blocks", ct.c_uint64),
        ("bfree", ct.c_uint64),
        ("bavail", ct.c_uint64),
        ("files", ct.c_uint64),
        ("ffree", ct.c_uint64),
        ("fsid", ct.c_int32 * 2),
        ("owner", ct.c_uint32),
        ("type", ct.c_uint32),
        ("flags", ct.c_uint32),
        ("subtype", ct.c_uint32),
        ("fstype", ct.c_char * 16),
        ("mountpoint", ct.c_char * 1024),
        ("source", ct.c_char * 1024),
        ("flags_ext", ct.c_uint32),
        ("reserved", ct.c_uint32 * 7),
    ]


def interrupt(signum: int, _frame: Any) -> None:
    raise InterruptedError(f"probe interrupted by signal {signum}")


def identity() -> dict[str, int]:
    return {"pid": os.getpid(), "ppid": os.getppid(), "pgid": os.getpgid(0)}


def read_ready(proc: subprocess.Popen[str]) -> dict[str, Any]:
    assert proc.stdout is not None
    with selectors.DefaultSelector() as selector:
        selector.register(proc.stdout, selectors.EVENT_READ)
        if not selector.select(10):
            raise RuntimeError("tree readiness timed out")
        line = proc.stdout.readline(16384)
    if not line.endswith("\n"):
        raise RuntimeError("missing tree readiness record")
    return json.loads(line)


def tree_worker(role: str) -> None:
    """Each parent reaps its direct child. Workers never use the repo decoder."""
    if role == "grandchild":
        print(json.dumps(identity()), flush=True)
        sys.stdin.read(1)
        return
    signal.signal(signal.SIGTERM, interrupt)
    signal.signal(signal.SIGINT, interrupt)
    grandchild = None
    try:
        before = time.time()
        grandchild = subprocess.Popen(
            [
                sys.executable,
                str(Path(__file__).resolve()),
                "--tree-worker",
                "grandchild",
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            text=True,
        )
        ground = read_ready(grandchild)
        ground["spawn_window"] = [before, time.time()]
        print(json.dumps({"child": identity(), "grandchild": ground}), flush=True)
        sys.stdin.read(1)
    except InterruptedError:
        pass
    finally:
        # Let the supervisor reap before the outer owner considers escalation.
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        signal.signal(signal.SIGINT, signal.SIG_IGN)
        if grandchild is not None:
            grandchild.terminate()
            try:
                grandchild.wait(timeout=3)
            except subprocess.TimeoutExpired:
                grandchild.kill()
                grandchild.wait(timeout=3)
            if grandchild.stdin is not None:
                grandchild.stdin.close()
            if grandchild.stdout is not None:
                grandchild.stdout.close()


def repo_struct() -> tuple[type[ct.Structure], dict[str, Any]]:
    source = ROOT / "runtime/platform/process_census.py"
    provenance: dict[str, Any] = {"source": str(source)}
    if source.is_file():
        provenance["source_sha256"] = hashlib.sha256(source.read_bytes()).hexdigest()
    sys.path.insert(0, str(ROOT))
    try:
        from runtime.platform.process_census import _LibProc

        struct = _LibProc()._ProcBsdinfo
        provenance["mode"] = (
            "imported runtime.platform.process_census._LibProc._ProcBsdinfo"
        )
        return struct, provenance
    except (ImportError, OSError, AttributeError, RuntimeError) as exc:
        provenance.update(
            mode="byte-identical 9ed24be93 fallback", import_error=str(exc)
        )
        return RepoBsdinfoCopy, provenance


def command(argv: list[str], rows: int | None = None) -> dict[str, Any]:
    try:
        result = subprocess.run(
            argv, capture_output=True, text=True, timeout=10, check=False
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"argv": argv, "available": False, "error": str(exc)}
    lines = result.stdout.splitlines()
    return {
        "argv": argv,
        "exit_code": result.returncode,
        "stdout": "\n".join(lines if rows is None else lines[:rows]),
        "stderr": result.stderr,
        "total_lines": len(lines),
    }


def observe_proc(lib: Any, struct: type[ct.Structure], pid: int) -> dict[str, Any]:
    # Give the kernel room to report its full record, independently of either sizeof.
    raw = ct.create_string_buffer(4096)
    ct.set_errno(0)
    count = lib.proc_pidinfo(pid, PROC_PIDTBSDINFO, 0, raw, len(raw))
    result: dict[str, Any] = {"returned_bytes": count, "sizeof": ct.sizeof(struct)}
    if count < ct.sizeof(struct):
        result["error"] = f"short proc_pidinfo record; errno={ct.get_errno()}"
        return result
    value = struct.from_buffer_copy(raw.raw[: ct.sizeof(struct)])
    result.update(
        pid=value.pbi_pid,
        ppid=value.pbi_ppid,
        pgid=value.pbi_pgid,
        status=value.pbi_status,
        nfiles=value.pbi_nfiles,
        start_time={
            "seconds": value.pbi_start_tvsec,
            "microseconds": value.pbi_start_tvusec,
        },
    )
    return result


def absent(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return False
    except ProcessLookupError:
        return True


def proc_truth(report: dict[str, Any]) -> None:
    libpath = ctypes.util.find_library("proc") or "/usr/lib/libproc.dylib"
    try:
        lib = ct.CDLL(libpath, use_errno=True)
    except OSError as exc:
        report["process"] = {"libproc": libpath, "loaded": False, "error": str(exc)}
        return
    lib.proc_pidinfo.argtypes = [ct.c_int, ct.c_int, ct.c_uint64, ct.c_void_p, ct.c_int]
    lib.proc_pidinfo.restype = ct.c_int
    current, provenance = repo_struct()
    section: dict[str, Any] = {
        "libproc": libpath,
        "loaded": True,
        "repo_struct": provenance,
        "observations": [],
        "sizes": {"repo": ct.sizeof(current), "corrected": ct.sizeof(CorrectedBsdinfo)},
    }
    report["process"] = section
    child = None
    known: dict[str, Any] = {}
    before = time.time()
    try:
        child = subprocess.Popen(
            [sys.executable, str(Path(__file__).resolve()), "--tree-worker", "child"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )
        known = read_ready(child)
        known["child"]["spawn_window"] = [before, time.time()]
        known["parent"] = identity()
        if known["child"]["pid"] != child.pid or known["child"]["pgid"] != child.pid:
            raise RuntimeError("spawned child identity/group mismatch")
        if (
            known["grandchild"]["ppid"] != child.pid
            or known["grandchild"]["pgid"] != child.pid
        ):
            raise RuntimeError("spawned grandchild identity/group mismatch")
        for role in ("parent", "child", "grandchild"):
            ground = known[role]
            ground["os_getpgid_from_parent"] = os.getpgid(ground["pid"])
            row = {"role": role, "ground_truth": ground}
            for label, struct in (("repo", current), ("corrected", CorrectedBsdinfo)):
                decoded = observe_proc(lib, struct, ground["pid"])
                decoded["matches_ground_truth"] = {
                    key: decoded.get(key) == ground[key]
                    for key in ("pid", "ppid", "pgid")
                }
                if "spawn_window" in ground and "start_time" in decoded:
                    start = decoded["start_time"]
                    stamp = start["seconds"] + start["microseconds"] / 1_000_000
                    decoded["start_in_spawn_window"] = (
                        ground["spawn_window"][0] <= stamp <= ground["spawn_window"][1]
                    )
                row[label] = decoded
            section["observations"].append(row)
        section["ps_reference"] = command(
            [
                "/bin/ps",
                "-p",
                ",".join(
                    str(known[k]["pid"]) for k in ("parent", "child", "grandchild")
                ),
                "-o",
                "pid=,ppid=,pgid=,stat=,lstart=",
            ]
        )
    finally:
        if child is not None:
            try:
                os.killpg(child.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            escalated = False
            try:
                child.wait(timeout=8)
            except subprocess.TimeoutExpired:
                escalated = True
                os.killpg(child.pid, signal.SIGKILL)
                child.wait(timeout=3)
            if child.stdin is not None:
                child.stdin.close()
            if child.stdout is not None:
                child.stdout.close()
            pids = [child.pid]
            if "grandchild" in known:
                pids.append(known["grandchild"]["pid"])
            deadline = time.monotonic() + 3
            while not all(absent(pid) for pid in pids) and time.monotonic() < deadline:
                time.sleep(0.05)
            try:
                os.killpg(child.pid, 0)
                group_absent = False
            except ProcessLookupError:
                group_absent = True
            section["cleanup"] = {
                "child_reaped": child.returncode is not None,
                "grandchild_reaped_by_child": child.returncode == 0
                and "grandchild" in known,
                "absent_pids": {str(pid): absent(pid) for pid in pids},
                "group_absent": group_absent,
                "sigkill_escalation": escalated,
            }
            if (
                not group_absent
                or not all(section["cleanup"]["absent_pids"].values())
                or child.returncode != 0
            ):
                raise RuntimeError("probe tree cleanup could not be verified")


def mount_truth() -> dict[str, Any]:
    result: dict[str, Any] = {"sizeof_statfs64": ct.sizeof(Statfs64)}
    libc = ct.CDLL("/usr/lib/libSystem.B.dylib", use_errno=True)
    # arm64 exports the sole 64-bit layout without a suffix; Intel uses INODE64.
    suffix = "" if platform.machine() == "arm64" else "$INODE64"
    entries = ct.POINTER(Statfs64)()
    try:
        getmntinfo = getattr(libc, "getmntinfo" + suffix)
        getmntinfo.argtypes = [ct.POINTER(ct.POINTER(Statfs64)), ct.c_int]
        getmntinfo.restype = ct.c_int
        count = getmntinfo(ct.byref(entries), 2)  # MNT_NOWAIT
        if count <= 0 or count > 4096:
            raise OSError(ct.get_errno(), f"getmntinfo count={count}")
        result["getmntinfo"] = {
            "available": True,
            "symbol": "getmntinfo" + suffix,
            "count": count,
            "mounts": [
                {
                    "mountpoint": os.fsdecode(entries[i].mountpoint),
                    "source": os.fsdecode(entries[i].source),
                    "fstype": os.fsdecode(entries[i].fstype),
                }
                for i in range(count)
            ],
        }
    except (AttributeError, OSError) as exc:
        result["getmntinfo"] = {"available": False, "error": str(exc)}
    try:
        statfs = getattr(libc, "statfs" + suffix)
        statfs.argtypes = [ct.c_char_p, ct.POINTER(Statfs64)]
        statfs.restype = ct.c_int
        value = Statfs64()
        if statfs(os.fsencode(tempfile.gettempdir()), ct.byref(value)) != 0:
            raise OSError(ct.get_errno(), "statfs64 failed")
        result["statfs"] = {
            "available": True,
            "symbol": "statfs" + suffix,
            "fstype": os.fsdecode(value.fstype),
            "mountpoint": os.fsdecode(value.mountpoint),
            "files": value.files,
            "ffree": value.ffree,
        }
    except (AttributeError, OSError) as exc:
        result["statfs"] = {"available": False, "error": str(exc)}
    return result


def filesystem_truth() -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="happyranch-mac-truth-") as folder:
        first, second = Path(folder) / "Report.txt", Path(folder) / "report.txt"
        first.write_text("first\n", encoding="utf-8")
        collided = False
        try:
            with second.open("x", encoding="utf-8") as stream:
                stream.write("second\n")
        except FileExistsError:
            collided = True
        # Also demonstrate ordinary second-write semantics, only in this fresh folder.
        second.write_text("second\n", encoding="utf-8")
        return {
            "tmpdir_environment": os.environ.get("TMPDIR"),
            "directory": folder,
            "case_collision": collided,
            "case_sensitive": not collided,
            "same_file": os.path.samefile(first, second),
            "names": sorted(os.listdir(folder)),
            "first_content_after_second_write": first.read_text(encoding="utf-8"),
            "scope": "observed temporary volume; not every host volume",
        }


def truth(report: dict[str, Any]) -> None:
    soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    report["facts"] = {
        "macos": platform.mac_ver()[0],
        "arch": platform.machine(),
        "python_version": sys.version,
        "python_path": sys.executable,
        "uid": os.getuid(),
        "rlimit_nofile": {"soft": soft, "hard": hard},
    }
    proc_truth(report)
    report["filesystem"] = {
        "flags": {
            name: {"present": hasattr(os, name), "value": getattr(os, name, None)}
            for name in ("O_PATH", "O_NOFOLLOW", "O_SYMLINK")
        },
        "proc_exists": Path("/proc").exists(),
        "mountinfo_exists": Path("/proc/self/mountinfo").exists(),
        "mount": command(["/sbin/mount"], rows=12),
        "native_mounts": mount_truth(),
        "df_Pi": command(["/bin/df", "-Pi", tempfile.gettempdir()], rows=2),
        "df_i": command(["/bin/df", "-i", tempfile.gettempdir()], rows=2),
        "case": filesystem_truth(),
    }


def teardown_identity(lib: Any, pid: int) -> list[int] | None:
    """Read a native start tuple independently of the product census/decoder."""
    value = CorrectedBsdinfo()
    ct.set_errno(0)
    count = lib.proc_pidinfo(pid, PROC_PIDTBSDINFO, 0, ct.byref(value), ct.sizeof(value))
    code = ct.get_errno()
    if count != ct.sizeof(value):
        if count <= 0 and code == errno.ESRCH:
            return None
        raise OSError(code, f"rescue identity pid={pid} returned={count} expected={ct.sizeof(value)}")
    if value.pbi_pid != pid or value.pbi_start_tvsec == 0:
        raise RuntimeError(f"invalid rescue identity for pid={pid}")
    return [pid, int(value.pbi_start_tvsec), int(value.pbi_start_tvusec)]


class TeardownRescue:
    """Probe-owned identities: retirement is permanent, including uncertainty."""

    def __init__(self, lib: Any) -> None:
        self.lib = lib
        self.process: subprocess.Popen[str] | None = None
        self.workers: dict[int, dict[str, Any]] = {}
        self.report: dict[str, Any] = {
            "sigterm_pids": [], "sigkill_pids": [], "workers": self.workers,
        }

    def remember(self, ground: dict[str, Any]) -> None:
        pid = ground["pid"]
        if type(pid) is not int or pid <= 0:
            raise RuntimeError("invalid worker PID")
        launch = ground.get("launch_identity")
        row = self.workers.setdefault(pid, {"state": "active", "direct_only": True})
        if "launch_identity" in row:
            if row["launch_identity"] != launch:
                self.retire(pid, "unknown", "launch record changed")
            return  # Never replace retained launch evidence or revive a worker.
        row.update(launch_identity=launch, direct_only=False)
        if (not isinstance(launch, list) or len(launch) != 3
                or not all(type(value) is int for value in launch)
                or launch[0] != pid or launch[1] <= 0 or not 0 <= launch[2] < 1_000_000):
            self.retire(pid, "unknown", "launch identity unavailable")

    def retire(self, pid: int, state: str, reason: str) -> None:
        row = self.workers[pid]
        if row["state"] == "active":
            row.update(state=state, reason=reason[:512])

    def observe_absence(self, pid: int) -> bool:
        row = self.workers[pid]
        if row["state"] != "active":
            return row["state"] == "gone"
        try:
            if absent(pid):
                self.retire(pid, "gone", "ESRCH")
                return True
        except OSError as exc:
            self.retire(pid, "unknown", str(exc))
        return False

    def authenticated(self, pid: int) -> bool:
        row = self.workers[pid]
        if row["state"] != "active":
            return False
        # An unreaped direct child cannot have its PID recycled. Once reaped,
        # never inspect/signal a new occupant of that number, even on reuse.
        if self.process is not None and pid == self.process.pid:
            if self.process.poll() is not None:
                self.retire(pid, "gone", "direct child reaped")
                return False
            if row["direct_only"]:
                return True  # Partial readiness: retained Popen owns this child.
        if self.observe_absence(pid) or row["state"] != "active":
            return False
        try:
            current = teardown_identity(self.lib, pid)
        except Exception as exc:
            self.retire(pid, "unknown", f"{type(exc).__name__}: {exc}")
            return False
        if current is None:
            self.retire(pid, "gone", "identity read ESRCH")
            return False
        if current != row.get("launch_identity"):
            self.retire(pid, "unknown", "start identity mismatch (possible PID reuse)")
            return False
        return True

    def signal(self, pid: int, sig: int) -> None:
        # Authenticate afresh immediately before EACH destructive signal.
        if not self.authenticated(pid):
            return
        try:
            os.kill(pid, sig)
            self.report["sigterm_pids" if sig == signal.SIGTERM else "sigkill_pids"].append(pid)
        except ProcessLookupError:
            self.retire(pid, "gone", "signal ESRCH")
        except OSError as exc:
            self.retire(pid, "unknown", str(exc))


def teardown_worker(role: str, registry: Path) -> None:
    """Sleeper chain with independent identities and direct-child reaping."""
    signal.signal(signal.SIGTERM, interrupt)
    signal.signal(signal.SIGINT, interrupt)
    own: dict[str, Any] = identity()
    try:
        lib = ct.CDLL("/usr/lib/libproc.dylib", use_errno=True)
        lib.proc_pidinfo.argtypes = [ct.c_int, ct.c_int, ct.c_uint64, ct.c_void_p, ct.c_int]
        lib.proc_pidinfo.restype = ct.c_int
        own["launch_identity"] = teardown_identity(lib, own["pid"])
    except Exception as exc:
        own["launch_identity"] = None
        own["launch_identity_error"] = f"{type(exc).__name__}: {exc}"[:512]
    (registry / f"{role}.json").write_text(json.dumps(own), encoding="utf-8")
    child = None
    try:
        tree = {role: own}
        next_role = {"parent": "child", "child": "grandchild"}.get(role)
        if next_role:
            child = subprocess.Popen(
                [sys.executable, str(Path(__file__).resolve()),
                 "--teardown-worker", next_role, "--registry", str(registry)],
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL, text=True,
            )
            tree.update(read_ready(child))
        print(json.dumps(tree), flush=True)
        while True:
            time.sleep(1)
    except InterruptedError:
        pass
    finally:
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        signal.signal(signal.SIGINT, signal.SIG_IGN)
        if child is not None:
            if child.poll() is None:  # Retained unreaped direct-child ownership.
                child.terminate()
            try:
                child.wait(timeout=3)
            except subprocess.TimeoutExpired:
                if child.poll() is None:
                    child.kill()
                child.wait(timeout=3)
            if child.stdout is not None:
                child.stdout.close()


def forced_libproc_failure() -> dict[str, Any]:
    """Use a fresh interpreter; the forced failure cannot affect the real tree."""
    script = r'''
import ctypes
import ctypes.util
import json
import sys
import logging
sys.path.insert(0, sys.argv[1])
logging.disable(logging.CRITICAL)
original = ctypes.CDLL
attempts = []
def fail_proc(name, *args, **kwargs):
    if "proc" in str(name):
        attempts.append(str(name))
        raise OSError("probe-forced libproc load failure")
    return original(name, *args, **kwargs)
ctypes.CDLL = fail_proc
from runtime.platform.backend_factory import select_session_backend
import_attempts = list(attempts)
backend = select_session_backend()
from runtime.platform.passthrough_backend import PassthroughBackend
print(json.dumps({"import_succeeded": True, "import_load_attempts": import_attempts,
                  "libproc_load_attempts": attempts,
                  "selected_backend": backend.probe().backend,
                  "is_passthrough": isinstance(backend, PassthroughBackend)}))
'''
    result = subprocess.run(
        [sys.executable, "-c", script, str(ROOT)], cwd=ROOT,
        capture_output=True, text=True, timeout=30, check=False,
    )
    observation: dict[str, Any] = {
        "exit_code": result.returncode, "stdout": result.stdout,
        "stderr": result.stderr,
    }
    if result.returncode == 0:
        observation.update(json.loads(result.stdout))
    return observation


def teardown_decode(libproc: Any, tree: dict[str, Any], check: Any) -> list[dict[str, Any]]:
    """Compare the actual product layout to independently published OS identities."""
    rows = []
    for role, ground in {"self": identity(), **tree}.items():
        decoded = observe_proc(libproc._lib, libproc._ProcBsdinfo, ground["pid"])
        matches = {key: decoded.get(key) == ground[key] for key in ("pid", "ppid", "pgid")}
        row = {"role": role, "ground_truth": ground, "repo": decoded,
               "matches_ground_truth": matches}
        rows.append(row)
        check(f"P05_{role}_decode", all(matches.values()) and "error" not in decoded, row)
    return rows


def teardown_kinfo(libproc: Any, tree: dict[str, Any], check: Any) -> list[dict[str, Any]]:
    """Cross-check product sysctl identity against independent libproc bytes."""
    rows = []
    for role, ground in {"self": identity(), **tree}.items():
        pid = ground["pid"]
        native = observe_proc(libproc._lib, CorrectedBsdinfo, pid)
        fallback = libproc.kinfo_identity(pid)
        expected = {key: native.get(key) for key in ("pid", "ppid", "pgid")}
        start = native.get("start_time", {})
        expected["start_identity"] = f"{start.get('seconds')}.{start.get('microseconds')}"
        matches = {key: fallback is not None and fallback.get(key) == value
                   for key, value in expected.items()}
        row = {"role": role, "libproc": expected, "sysctl": fallback,
               "sizeof_kinfo_proc": ct.sizeof(libproc._KinfoProc), "matches": matches}
        rows.append(row)
        check(f"kinfo_{role}_identity", "error" not in native and all(matches.values()), row)
    return rows


def libproc_failure_context(failures: list[dict[str, Any]], tree: dict[str, Any]) -> None:
    """Enrich at most four failed PID reads outside the product's scan deadline.

    ps emits only one row of numeric identity fields, never arguments/names.
    A later row is diagnostic evidence, not a product ownership/absence proof.
    """
    import pwd

    known = {value["pid"] for value in tree.values()}
    contexts: dict[int, dict[str, Any]] = {}
    for failure in failures:
        pid = failure["pid"]
        failure["in_probe_tree"] = pid in known
        failure["descendant_status"] = "known_probe_member" if pid in known else "unknown"
        if pid not in contexts and len(contexts) < 4:
            context: dict[str, Any] = {"observation": "after_failed_scan", "our_uid": os.getuid()}
            result = subprocess.run(
                ["/bin/ps", "-p", str(pid), "-o", "pid=,ppid=,pgid=,uid=,ruid="],
                capture_output=True, text=True, timeout=2, check=False,
            )
            context["ps_exit_code"] = result.returncode
            fields = result.stdout.split()
            if len(fields) == 5 and all(value.isdecimal() for value in fields):
                context.update(zip(("pid", "ppid", "pgid", "uid", "ruid"), map(int, fields)))
                context["same_uid"] = context["uid"] == os.getuid()
                try:
                    context["uid_name"] = pwd.getpwuid(context["uid"]).pw_name[:64]
                except KeyError:
                    context["uid_name"] = None
            else:
                context["identity_available"] = False
            contexts[pid] = context
        failure["os_context"] = contexts.get(pid, {"unavailable": "four-PID diagnostic cap"})


def teardown_counter_cases(backend: Any, running: Any, check: Any) -> dict[str, Any]:
    """Mask counters on real rows, leaving product identity/ownership reads intact."""
    from runtime.platform.process_census import DescendantSampler

    libproc = backend._census.reader()._libproc
    original = libproc.task_info
    observations = {}
    try:
        for case, counters in (("missing", None), ("zero", {"rss_bytes": 0, "cpu_seconds": 0.0})):
            libproc.task_info = lambda pid: counters
            for name, sampler in (("backend", backend),
                                  ("descendants", DescendantSampler(census=backend._census))):
                key = f"P07_{name}_{case}"
                try:
                    sample = sampler.sample(running)
                except Exception as exc:
                    observations[key] = {"error": f"{type(exc).__name__}: {exc}"[:1024]}
                    check(key, False, observations[key])
                    continue
                observations[key] = {
                    "memory": sample.memory_peak_bytes, "cpu": sample.cpu_total_seconds,
                    "process_count": sample.process_count, "provenance": sample.provenance,
                }
                expected = None if case == "missing" else 0
                check(key, sample.memory_peak_bytes == expected
                      and sample.cpu_total_seconds == expected and sample.process_count == 3
                      and sample.provenance == ("unavailable" if case == "missing" else "sampled"),
                      observations[key])
    finally:
        libproc.task_info = original
    return observations


def teardown(report: dict[str, Any]) -> None:
    """Exercise shipping selection, launch, sample and finish on an owned tree."""
    from dataclasses import asdict

    sys.path.insert(0, str(ROOT))
    from runtime.orchestrator.host_supervisor import AdmissionRequest
    from runtime.platform.backend_factory import select_session_backend
    from runtime.platform.macos_process_group import MacOSProcessGroupBackend
    from runtime.platform.session_backend import LaunchSpec

    checks: list[dict[str, Any]] = []
    section: dict[str, Any] = {"checks": checks}
    report["teardown"] = section

    def check(name: str, passed: bool, observed: Any) -> None:
        checks.append({"name": name, "status": "PASS" if passed else "FAIL",
                       "observed": observed})

    forced = forced_libproc_failure()
    section["forced_libproc_failure"] = forced
    check("P03_import_without_libproc", forced.get("import_succeeded") is True
          and forced.get("import_load_attempts") == [], forced)
    check("P03_factory_fallback", forced.get("exit_code") == 0
          and bool(forced.get("libproc_load_attempts"))
          and forced.get("selected_backend") == "passthrough"
          and forced.get("is_passthrough") is True, forced)
    backend = select_session_backend()
    section["selected_backend"] = (backend.name if isinstance(backend, MacOSProcessGroupBackend)
                                   else backend.probe().backend)
    check("product_macos_backend_selected", isinstance(backend, MacOSProcessGroupBackend),
          section["selected_backend"])
    if not isinstance(backend, MacOSProcessGroupBackend):
        raise RuntimeError("product selection did not yield the macOS backend")

    libproc = backend._census.reader()._libproc
    rescue = TeardownRescue(libproc._lib)
    failures: list[dict[str, Any]] = []
    section["libproc_failures"] = failures
    section["diagnostic_limits"] = {"failure_records": 8, "os_context_pids": 4}
    fallback_pids: set[int] = set()
    # Numeric counts only, bounded by the existing4096-PID observation limit.
    fallback_counts = {"host_pids": 0, "reads": 0, "errno_counts": {}}
    section["sysctl_fallback"] = fallback_counts
    original_bsd_info = libproc.bsd_info
    stage = "launch"

    def observed_bsd_info(pid: int) -> dict | None:
        try:
            row = original_bsd_info(pid)
            if row is not None and row.get("identity_source") == "sysctl":
                if len(fallback_pids) >= 4096 and pid not in fallback_pids:
                    raise RuntimeError("probe fallback PID observation cap exhausted")
                fallback_pids.add(pid)
                fallback_counts["host_pids"] = len(fallback_pids)
                fallback_counts["reads"] += 1
                code = int(row["fallback_errno"])
                counts = fallback_counts["errno_counts"]
                counts[code] = counts.get(code, 0) + 1
            return row
        except Exception as exc:
            if len(failures) < 8:
                row: dict[str, Any] = {"pid": pid, "stage": stage, "error": str(exc)[:512]}
                for key, value in re.findall(r"(returned|expected|errno|errno_name)=(-?\w+)", str(exc)):
                    row.setdefault(key, value if key == "errno_name" else int(value))
                failures.append(row)
            raise  # Diagnostics never replace or swallow the checked refusal.

    libproc.bsd_info = observed_bsd_info
    # Every role publishes its identity before spawning, even if readiness fails.
    with tempfile.TemporaryDirectory(prefix="happyranch-mac-teardown-") as folder:
        registry = Path(folder)
        running = None
        pending = None
        pids: list[int] = []
        tree: dict[str, Any] = {}
        try:
            request = AdmissionRequest(
                org="native-probe", invocation_kind="task",
                logical_id=f"teardown-{os.getpid()}", executor_profile="native-probe",
            )
            pending = backend.prepare(request, None)
            running = backend.launch(pending, LaunchSpec(
                argv=(sys.executable, str(Path(__file__).resolve()),
                      "--teardown-worker", "parent", "--registry", folder),
                cwd=str(ROOT), stdin=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            ))
            if running.process is None:
                raise RuntimeError("backend did not return its direct process handle")
            rescue.process = running.process
            rescue.workers[running.root_pid] = {"state": "active", "direct_only": True}
            tree = read_ready(running.process)
            section["tree"] = tree
            pids = [tree[role]["pid"] for role in ("parent", "child", "grandchild")]
            for ground in tree.values():
                rescue.remember(ground)
            group_ok = (
                pids[0] == running.root_pid and len(set(pids)) == 3
                and tree["child"]["ppid"] == pids[0]
                and tree["grandchild"]["ppid"] == pids[1]
                and all(tree[role]["pgid"] == running.root_pid for role in tree)
                and all(os.getpgid(pid) == running.root_pid for pid in pids)
            )
            check("three_generation_session_group", group_ok, tree)
            section["P05_decode"] = teardown_decode(libproc, tree, check)
            section["kinfo_cross_check"] = teardown_kinfo(libproc, tree, check)
            stage = "sample"
            sample = None
            try:
                sample = backend.sample(running)
            except Exception as exc:
                section["sample_error"] = f"{type(exc).__name__}: {exc}"[:1024]
                check("tree_sample", False, section["sample_error"])
            # Both P07 cases still execute if the real checked sample failed;
            # they must not conceal a product census failure with synthetic rows.
            try:
                section["counter_cases"] = teardown_counter_cases(backend, running, check)
            except Exception as exc:
                section["counter_cases_error"] = f"{type(exc).__name__}: {exc}"[:1024]
                check("P07_counter_cases_completed", False, section["counter_cases_error"])
            stage = "finish"
            try:
                receipt = backend.finish(running, "cancelled", 5.0,
                                         samples=() if sample is None else (sample,))
            except Exception as exc:
                section["finish_error"] = f"{type(exc).__name__}: {exc}"[:1024]
                section["receipt"] = None
                check("product_finish", False, section["finish_error"])
                check("receipt_honesty", False, "finish refused; no quiescent receipt")
                raise
            if sample is not None:
                section["sample"] = asdict(sample)
                check("tree_sample", sample.process_count == 3
                      and sample.memory_peak_bytes is not None and sample.memory_peak_bytes > 0
                      and sample.cpu_total_seconds is not None and sample.cpu_total_seconds >= 0
                      and sample.provenance == "sampled", section["sample"])
            section["receipt"] = asdict(receipt)
            section["clean"] = (receipt.quiescent and not receipt.survivors
                                and receipt.cleanup_status != "incomplete")
            section["signal_sent"] = {
                "term": ["SIGTERM"], "kill": ["SIGTERM", "SIGKILL"],
            }.get(receipt.cleanup_status, [])
            section["signal_source"] = "receipt.cleanup_status; not independent delivery proof"
            # Observe BEFORE any probe-owned disposal can conceal a product leak.
            deadline = time.monotonic() + 3
            gone = {str(pid): rescue.observe_absence(pid) for pid in pids}
            while not all(gone.values()) and time.monotonic() < deadline:
                time.sleep(0.05)
                gone = {str(pid): rescue.observe_absence(pid) for pid in pids}
            section["post_finish_kill_zero_ESRCH"] = gone
            for pid, is_gone in gone.items():
                check(f"pid_{pid}_gone_after_product_finish", is_gone, {"ESRCH": is_gone})
            check("receipt_honesty", receipt.quiescent and section["clean"]
                  and all(gone.values()) and bool(section["signal_sent"]), section["receipt"])
        finally:
            libproc.bsd_info = original_bsd_info
            try:
                # Rescue precedes optional ps/getpwuid enrichment. Registry
                # records also cover partial readiness, never arbitrary groups.
                cleanup = rescue.report
                section["probe_cleanup"] = cleanup
                if running is not None and running.process is not None:
                    rescue.process = running.process
                    rescue.workers.setdefault(running.root_pid,
                                              {"state": "active", "direct_only": True})
                roles = {}
                registry_errors = []
                for role in ("parent", "child", "grandchild"):
                    path = registry / f"{role}.json"
                    try:
                        with path.open(encoding="utf-8") as stream:
                            ground = json.loads(stream.read(4096))
                    except FileNotFoundError:
                        continue
                    except Exception as exc:
                        registry_errors.append(f"{role}: {type(exc).__name__}: {exc}"[:512])
                        continue
                    try:
                        rescue.remember(ground)
                    except Exception as exc:
                        registry_errors.append(f"{role}: {type(exc).__name__}: {exc}"[:512])
                        continue
                    roles[role] = ground["pid"]
                # TERM gives each parent a chance to reap before escalation.
                for pid in rescue.workers:
                    rescue.signal(pid, signal.SIGTERM)
                if rescue.process is not None:
                    try:
                        rescue.process.wait(timeout=8)
                    except subprocess.TimeoutExpired:
                        pass
                leaf_first = [roles[role] for role in ("grandchild", "child", "parent")
                              if role in roles]
                leaf_first.extend(pid for pid in rescue.workers if pid not in leaf_first)
                for pid in leaf_first:
                    rescue.signal(pid, signal.SIGKILL)
                    deadline = time.monotonic() + 3
                    while (rescue.workers[pid]["state"] == "active"
                           and not rescue.observe_absence(pid) and time.monotonic() < deadline):
                        if rescue.process is not None:
                            rescue.process.poll()
                        time.sleep(0.05)
                if rescue.process is not None:
                    try:
                        rescue.process.wait(timeout=3)
                        rescue.retire(rescue.process.pid, "gone", "direct child reaped")
                    except subprocess.TimeoutExpired:
                        rescue.retire(rescue.process.pid, "unknown", "direct child wait timed out")
                    if rescue.process.stdout is not None:
                        rescue.process.stdout.close()
                elif pending is not None:
                    backend.abandon(pending)
                cleanup["absent_pids"] = {
                    str(pid): rescue.observe_absence(pid) for pid in rescue.workers
                }
                cleanup["unknown_residue"] = [
                    pid for pid, row in rescue.workers.items() if row["state"] != "gone"
                ] + registry_errors
                check("probe_cleanup_no_orphans", not cleanup["unknown_residue"], cleanup)
                if cleanup["unknown_residue"]:
                    raise RuntimeError("probe cleanup could not verify every owned worker gone")
            finally:
                try:
                    libproc_failure_context(failures, tree)
                except Exception as exc:
                    section["diagnostic_context_error"] = f"{type(exc).__name__}: {exc}"[:512]
    if any(row["status"] != "PASS" for row in checks):
        raise RuntimeError("native teardown checks failed")


def disk_sweep(report: dict[str, Any]) -> None:
    """Native preview plus private destructive fixtures; never live cleanup."""
    import socket
    import sqlite3
    import stat
    from collections.abc import Iterator
    from contextlib import closing, contextmanager
    from unittest.mock import patch

    sys.path.insert(0, str(ROOT))
    from runtime.daemon import _disk_sweep_fs as fs
    from runtime.daemon import disk_sweeper as sweeper

    checks: list[dict[str, Any]] = []
    section: dict[str, Any] = {"checks": checks}
    report["disk_sweep"] = section
    deadline = time.monotonic() + 60

    def check(name: str, passed: bool, observed: Any) -> None:
        checks.append(dict(name=name, status="PASS" if passed else "FAIL", observed=observed))

    def write(path: Path, data: bytes) -> None:
        if len(data) > 4096:
            raise RuntimeError("disk-sweep fixture write exceeds 4096 bytes")
        path.write_bytes(data)

    def seed(path: Path) -> list[str]:
        path.mkdir(parents=True, mode=0o700)
        write(path / "file", b"private disposable fixture\n")
        os.symlink("../outside", path / "symlink")
        os.link(path / "file", path / "hardlink")
        previous = Path.cwd()
        try:
            os.chdir(path)
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as endpoint:
                endpoint.bind("socket")  # avoid sockaddr_un's short path limit
        finally:
            os.chdir(previous)
        os.mkfifo(path / "fifo", 0o600)
        return ["fifo", "file", "hardlink", "socket", "symlink"]

    real_open, real_stat, real_unlink = os.open, os.stat, os.unlink

    @contextmanager
    def no_leaf_opens() -> Iterator[dict[str, int]]:
        attempts = {"leaf_opens": 0, "directory_opens": 0}

        def guarded_open(path: Any, flags: int, *args: Any, **kwargs: Any) -> int:
            # Check the actual object as well as flags, including dangling links.
            observed = real_stat(path, dir_fd=kwargs.get("dir_fd"), follow_symlinks=False)
            if not stat.S_ISDIR(observed.st_mode) or not flags & os.O_DIRECTORY:
                attempts["leaf_opens"] += 1
                raise AssertionError("product attempted a leaf open")
            attempts["directory_opens"] += 1
            return real_open(path, flags, *args, **kwargs)

        with patch.object(os, "open", guarded_open):
            yield attempts

    mounts = fs.mounts(deadline)
    check("native_mount_table", "/" in mounts and len(mounts) > 0,
          {"count": len(mounts), "root_present": "/" in mounts})
    for failure in ("unavailable", "layout", "malformed"):
        real_sizeof = ct.sizeof
        if failure == "unavailable":
            fault = patch.object(ct, "CDLL", side_effect=OSError("probe unavailable mount API"))
        elif failure == "layout":
            fault = patch.object(ct, "sizeof", side_effect=lambda value: 0 if value is fs._DarwinStatfs else real_sizeof(value))
        else:
            # Replace the bounded native table copy with unterminated fields.
            fault = patch.object(ct, "string_at", side_effect=lambda pointer, size: b"x" * size)
        reason = None
        with fault:
            try:
                fs.mounts(deadline)
            except fs.Retain as exc:
                reason = str(exc)
        check("mount_evidence_retains_" + failure, reason == "unknown_mount_evidence", reason)
    # The workspace checkout is private to this job. Resolving its temp root
    # avoids Darwin /var -> /private/var aliases in the no-follow product walk.
    with tempfile.TemporaryDirectory(prefix=".disk-sweep-", dir=ROOT) as folder:
        scratch = Path(folder).resolve(strict=True)
        section["scratch_removed"] = False
        write(scratch / "outside", b"outside symlink target preserved\n")
        outside = (scratch / "outside").read_bytes()  # fixed <=4096-byte fixture

        # Exercise discovery, SQLite ownership, manifest, age, mounts and both
        # snapshot admissions through the real product sweep_org dry-run path.
        org = scratch / "org"
        workspace = org / "workspaces/probe"
        tmp = workspace / ".happyranch/task-tmp"
        manifests = workspace / ".happyranch/task-scratch-manifests"
        manifests.mkdir(parents=True)
        eligible = tmp / "TASK-1"
        leaves = seed(eligible)
        recent = tmp / "TASK-2"
        recent.mkdir()
        write(recent / "recent", b"recent fixture\n")
        now = time.time() + 7200  # synthetic age clock; no wait or ctime forgery
        os.utime(recent / "recent", (now, now))
        for candidate in (eligible, recent):
            manifest = dict(version=1, task_id=candidate.name, required_root=str(candidate),
                            observed_root=str(candidate), root_classification="regenerable_scratch",
                            manifest_classification="durable_recovery_artifact",
                            lock_classification="durable_recovery_artifact", producers=[])
            write(manifests / (candidate.name + ".json"), json.dumps(manifest).encode())
        # Minimal read-only evidence fixture, not a runtime schema initializer.
        # A 16-page ceiling bounds every SQLite write (64KiB at 4096/page).
        with closing(sqlite3.connect(org / "happyranch.db")) as conn, conn:
            conn.execute("PRAGMA page_size=4096")
            conn.execute("PRAGMA max_page_count=16")
            conn.execute("PRAGMA journal_mode=OFF")
            conn.execute("CREATE TABLE tasks(id TEXT,parent_task_id TEXT,status TEXT,assigned_agent TEXT)")
            conn.execute("CREATE TABLE audit_log(task_id TEXT,action TEXT,payload TEXT)")
            conn.execute("CREATE TABLE jobs(task_id TEXT,status TEXT)")
            conn.executemany("INSERT INTO tasks VALUES(?,?,?,?)", [
                ("TASK-1", "TASK-3", "completed", "probe"),
                ("TASK-2", "TASK-3", "completed", "probe"),
                ("TASK-3", None, "completed", "probe"),
            ])
            conn.executemany("INSERT INTO audit_log VALUES(?,?,?)", [
                (candidate.name, "session_start", json.dumps({"workspace": str(workspace)}))
                for candidate in (eligible, recent)
            ])
        before = {p: fs.snapshot(p, deadline, scratch=True) for p in (eligible, recent)}
        preview = sweeper.sweep_org(org, deadline, mode="dry_run", now=now)
        states = {Path(c["path"]).name: (c["state"], c.get("reason")) for c in preview["candidates"]}
        check("real_preview_classification", preview["outcome"] == "complete"
              and states == {"TASK-1": ("eligible", None), "TASK-2": ("retained", "changed_within_hour")},
              states)
        check("preview_nonmutating", all(fs.trees_equal(tree, fs.snapshot(p, deadline, scratch=True), deadline)
                                         for p, tree in before.items()), {"candidates": len(before)})

        # A separate tree reaches real unlink: dry-run cannot test this branch.
        target = scratch / "mutation"
        seed(target)
        tree = fs.snapshot(target, deadline, scratch=True)
        removed: list[str] = []

        def record_unlink(name: str, *, dir_fd: int) -> None:
            real_unlink(name, dir_fd=dir_fd)
            if len(removed) >= 5:
                raise AssertionError("unexpected extra unlink")
            removed.append(name)

        with no_leaf_opens() as attempts, patch.object(os, "unlink", record_unlink):
            fs.mutate(target, tree, deadline, remove=True, scratch=True)
        check("all_leaf_types_removed_without_open", not target.exists() and sorted(removed) == leaves
              and attempts["leaf_opens"] == 0 and attempts["directory_opens"] > 0,
              dict(removed=removed, **attempts))
        check("symlink_target_preserved", (scratch / "outside").read_bytes() == outside, True)

        # The hardlink exception may relax only nlink/ctime, and only to the
        # exact expected decrement. Other sibling changes remain retention.
        for change in ("mode", "size", "mtime", "nlink"):
            target = scratch / ("hardlink-" + change)
            target.mkdir()
            write(target / "a", b"original\n")
            os.link(target / "a", target / "b")
            tree = fs.snapshot(target, deadline, scratch=True)
            removed = []

            def changed_sibling(name: str, *, dir_fd: int) -> None:
                real_unlink(name, dir_fd=dir_fd)
                removed.append(name)
                if name == "a":
                    sibling = target / "b"
                    if change == "mode":
                        os.chmod(sibling, 0o400)
                    elif change == "size":
                        write(sibling, b"longer changed content\n")
                    elif change == "mtime":
                        os.utime(sibling, ns=(tree["b"].st_atime_ns, tree["b"].st_mtime_ns + 1000000000))
                    else:
                        os.link(sibling, scratch / "extra-hardlink")

            reason = None
            with no_leaf_opens() as attempts, patch.object(os, "unlink", changed_sibling):
                try:
                    fs.mutate(target, tree, deadline, remove=True, scratch=True)
                except fs.Retain as exc:
                    reason = str(exc)
            check("changed_hardlink_retains_" + change, reason == "tree_changed_before_mutation"
                  and removed == ["a"] and (target / "b").exists() and attempts["leaf_opens"] == 0,
                  dict(reason=reason, removed=removed, **attempts))

        # Inject at the final stat boundary, after visit's first leaf check.
        # A spare inode exists before snapshot so replacement cannot reuse it.
        for change in ("swap", "mode", "size", "mtime", "nlink", "ctime"):
            target = scratch / change
            target.mkdir()
            leaf = target / "leaf"
            write(leaf, b"original\n")
            spare = scratch / (change + "-spare")
            write(spare, b"replacement\n")
            tree = fs.snapshot(target, deadline, scratch=True)
            calls = 0
            unlinks = 0

            def changed_stat(name: Any, *args: Any, **kwargs: Any) -> os.stat_result:
                nonlocal calls
                if name == "leaf" and kwargs.get("dir_fd") is not None:
                    calls += 1
                    if calls == 2:
                        if change == "swap":
                            os.replace(spare, leaf)
                        elif change == "mode":
                            os.chmod(leaf, 0o400)
                        elif change == "size":
                            write(leaf, b"longer changed content\n")
                        elif change == "mtime":
                            os.utime(leaf, ns=(tree["leaf"].st_atime_ns, tree["leaf"].st_mtime_ns + 1000000000))
                        elif change == "nlink":
                            os.link(leaf, scratch / "extra-link")
                        else:
                            os.chmod(leaf, 0o400)
                            os.chmod(leaf, stat.S_IMODE(tree["leaf"].st_mode))
                return real_stat(name, *args, **kwargs)

            def count_unlink(name: str, *, dir_fd: int) -> None:
                nonlocal unlinks
                unlinks += 1
                real_unlink(name, dir_fd=dir_fd)

            reason = None
            with no_leaf_opens() as attempts, patch.object(os, "stat", changed_stat), patch.object(os, "unlink", count_unlink):
                try:
                    fs.mutate(target, tree, deadline, remove=True, scratch=True)
                except fs.Retain as exc:
                    reason = str(exc)
            check("changed_leaf_retains_" + change, reason == "identity_changed" and calls == 2
                  and unlinks == 0 and leaf.exists() and attempts["leaf_opens"] == 0,
                  dict(reason=reason, final_stat_reached=calls == 2, unlinks=unlinks, **attempts))
    section["scratch_removed"] = not scratch.exists()
    check("private_scratch_removed", section["scratch_removed"], section["scratch_removed"])
    if any(row["status"] != "PASS" for row in checks):
        raise RuntimeError("native disk-sweep checks failed")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", choices=SUITES, default="truth")
    parser.add_argument("--output", type=Path, help="also save JSON at this path")
    parser.add_argument(
        "--tree-worker", choices=("child", "grandchild"), help=argparse.SUPPRESS
    )
    parser.add_argument(
        "--teardown-worker", choices=("parent", "child", "grandchild"),
        help=argparse.SUPPRESS,
    )
    parser.add_argument("--registry", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.teardown_worker:
        if args.registry is None:
            parser.error("--teardown-worker requires --registry")
        teardown_worker(args.teardown_worker, args.registry)
        return 0
    if args.tree_worker:
        tree_worker(args.tree_worker)
        return 0
    report: dict[str, Any] = {"platform": sys.platform, "suite": args.suite}
    code = 0
    if sys.platform != "darwin":
        report["skipped"] = True
        print("darwin-only: native truth probe skipped", file=sys.stderr)
    else:
        signal.signal(signal.SIGTERM, interrupt)
        signal.signal(signal.SIGINT, interrupt)
        try:
            {"truth": truth, "teardown": teardown, "disk-sweep": disk_sweep}[args.suite](report)
            report["completed"] = True
        except Exception as exc:  # noqa: BLE001 -- emit partial JSON for any internal probe error
            report.update(
                completed=False, internal_error=f"{type(exc).__name__}: {exc}"
            )
            code = 1
        print(f"Native {args.suite}: completed={report['completed']}", file=sys.stderr)
        print(f"Platform facts: {report.get('facts', {})}", file=sys.stderr)
        for row in report.get("process", {}).get("observations", []):
            print(
                f"{row['role']}: ground={row['ground_truth']} repo={row['repo']} corrected={row['corrected']}",
                file=sys.stderr,
            )
        if "filesystem" in report:
            print(f"Filesystem: {report['filesystem']}", file=sys.stderr)
        if "internal_error" in report:
            print(report["internal_error"], file=sys.stderr)
    encoded = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.suite == "disk-sweep" and len(encoded.encode("utf-8")) > 65536:
        raise RuntimeError("disk-sweep report exceeds 64KiB")
    if args.output:
        args.output.write_text(encoded, encoding="utf-8")
    print(encoded, end="")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
