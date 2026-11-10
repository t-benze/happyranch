#!/usr/bin/env python3
"""Manual, stdlib-only Darwin observations; JSON stdout, human summary stderr."""

from __future__ import annotations

import argparse
import ctypes as ct
import ctypes.util
import hashlib
import json
import os
import platform
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
SUITES = ("truth",)
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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", choices=SUITES, default="truth")
    parser.add_argument("--output", type=Path, help="also save JSON at this path")
    parser.add_argument(
        "--tree-worker", choices=("child", "grandchild"), help=argparse.SUPPRESS
    )
    args = parser.parse_args()
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
            {"truth": truth}[args.suite](report)
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
    if args.output:
        args.output.write_text(encoded, encoding="utf-8")
    print(encoded, end="")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
