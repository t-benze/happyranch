#!/usr/bin/env python3
"""PR800 runner observations only; never a product lifecycle/allocator owner."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import selectors
import signal
import stat
import subprocess
import sys
import time

from n3_failure_capture import _initialize_observation_owner, _terminate_observation


def save(path: Path, value: object) -> None:
    raw = (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode("ascii")
    if len(raw) > 32768:
        raise ValueError
    with path.open("xb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    os.chmod(path, 0o644)


def observe(argv: list[str], *, seconds: int, env: dict[str, str] | None = None,
            cwd: Path | None = None, script_input: bool = False) -> tuple[dict, bytes]:
    """Bound launch/read/wait; retain actual exit separately from collection loss.

    Raw streams/argv/environment are never logged. Native diagnostics stay in
    the original closed installer receipt. The existing one-second owned group
    termination/reap is within the proposal's maximum five-second allowance.
    This observation deadline never changes a shipping operation's 180s bound.
    """
    started = time.monotonic()
    deadline = started + seconds
    record = {"observation_seconds": seconds, "reap_allowance_seconds": 1,
              "stream_cap": 32768, "launch": "not_attempted", "launch_errno": None,
              "exit": None, "waited": False, "group_settled": False,
              "loss": None, "stdout_bytes": 0, "stderr_bytes": 0,
              "elapsed_seconds": None, "preparation_timing": "unavailable"}
    process = None
    output = bytearray()
    cancelled = 0
    signals = (signal.SIGHUP, signal.SIGINT, signal.SIGTERM)
    handlers = {item: signal.getsignal(item) for item in signals}

    def cancel(signum: int, _frame: object) -> None:
        nonlocal cancelled
        cancelled = cancelled or signum

    try:
        for item in signals:
            signal.signal(item, cancel)
        if not _initialize_observation_owner(require_root=os.geteuid() == 0):
            record["loss"] = "owner_unavailable"
        elif cancelled or time.monotonic() >= deadline:
            record["loss"] = "cancelled" if cancelled else "timeout"
        else:
            try:
                process = subprocess.Popen(argv, stdin=sys.stdin if script_input else subprocess.DEVNULL,
                                           stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                           env=env, cwd=cwd, start_new_session=True)
                record["launch"] = "started"
            except OSError as exc:
                record.update(launch="failed", launch_errno=exc.errno, loss="launch_error")
        if process is not None:
            with selectors.DefaultSelector() as selector:
                for pipe in (process.stdout, process.stderr):
                    os.set_blocking(pipe.fileno(), False)
                    selector.register(pipe, selectors.EVENT_READ)
                while record["loss"] is None:
                    if cancelled or time.monotonic() >= deadline:
                        record["loss"] = "cancelled" if cancelled else "timeout"
                        break
                    if not selector.get_map():
                        try:
                            record["exit"] = process.wait(timeout=min(0.05, deadline - time.monotonic()))
                            record["waited"] = True
                            if time.monotonic() >= deadline:
                                record["loss"] = "timeout"
                            break
                        except subprocess.TimeoutExpired:
                            continue
                    for key, _ in selector.select(min(0.05, max(0, deadline - time.monotonic()))):
                        field = "stdout_bytes" if key.fileobj is process.stdout else "stderr_bytes"
                        chunk = os.read(key.fd, min(4096, 32769 - record[field]))
                        record[field] += len(chunk)
                        if record[field] > 32768:
                            record["loss"] = "truncated"
                            break
                        if key.fileobj is process.stdout:
                            output.extend(chunk)
                        if not chunk:
                            selector.unregister(key.fileobj)
    except (OSError, ValueError):
        record["loss"] = record["loss"] or "observation_error"
    finally:
        if process is not None:
            try:
                record["group_settled"] = _terminate_observation(process)
                # wait(), including after timeout/kill, supplies the actual exit.
                record["exit"] = process.returncode
                record["waited"] = process.returncode is not None
            finally:
                for pipe in (process.stdout, process.stderr):
                    try:
                        pipe.close()
                    except OSError:
                        record["loss"] = record["loss"] or "close_error"
        for item, handler in handlers.items():
            signal.signal(item, handler)
        record["elapsed_seconds"] = round(time.monotonic() - started, 6)
        record["cancelled"] = cancelled or None
    return record, bytes(output)


def successful(record: dict) -> bool:
    return (record["launch"] == "started" and record["exit"] == 0
            and record["waited"] and record["group_settled"] and record["loss"] is None
            and record["cancelled"] is None)


def regular(path: Path, limit: int) -> bytes:
    # Metadata precedes even a nonblocking content open: never probe a FIFO.
    before = path.lstat()
    if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or not 0 <= before.st_size <= limit:
        raise ValueError
    fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        info = os.fstat(fd)
        if (info.st_dev, info.st_ino) != (before.st_dev, before.st_ino):
            raise ValueError
        chunks = bytearray()
        while True:
            chunk = os.read(fd, min(65536, limit + 1 - len(chunks)))
            if not chunk:
                break
            chunks.extend(chunk)
            if len(chunks) > limit:
                raise ValueError
        after = os.fstat(fd)
        named = path.lstat()
        fields = ("st_dev", "st_ino", "st_mode", "st_uid", "st_gid", "st_nlink", "st_size", "st_mtime_ns", "st_ctime_ns")
        if len(chunks) != before.st_size or any(getattr(before, k) != getattr(after, k) or getattr(before, k) != getattr(named, k) for k in fields):
            raise ValueError
        return bytes(chunks)
    finally:
        os.close(fd)


def verify_operation(receipt: Path, operation: str) -> None:
    from runtime.remote_access.linux_package import _package_json, _managed_package_diagnostic_projection
    data = _package_json(regular(receipt, 4096), 4096)
    if (data["operation"] != operation or data["status"] != 0 or data["cancelled"] is not False
            or data["initiating"] != {"kind": "return", "category": None, "status": 0}
            or data["collection"] != {"state": "complete", "losses": [], "encoding": "primary"}):
        raise ValueError
    d = _managed_package_diagnostic_projection(data["diagnostic"])
    if (d["outcome"] != "success" or d["diagnostic_state"] != "absent" or d["helper_exit"] != 0
            or d["reap_exit"] != 0 or d["reaped"] is not True or d["deadline_elapsed"] is not False
            or d["process"]["wait_error"] is not False
            or [s[:2] for s in d["stages"]] != [[s, "success"] for s in ("admission_child", "stopped_admission_child", "package_body")]):
        raise ValueError
    # The closed original native/transport/stage projection, not wrapper0,
    # supplies the successful body/final Refresh+current implication at exact H.


def image_hash(path: Path, deadline: float) -> tuple[os.stat_result, str]:
    before = path.lstat()
    if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or not 0 <= before.st_size <= 268435456:
        raise ValueError
    fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        digest, count = hashlib.sha256(), 0
        while True:
            if time.monotonic() >= deadline:
                raise ValueError
            chunk = os.read(fd, 65536)
            if not chunk:
                break
            count += len(chunk)
            if count > before.st_size:
                raise ValueError
            digest.update(chunk)
        after, named = os.fstat(fd), path.lstat()
        fields = ("st_dev", "st_ino", "st_mode", "st_uid", "st_gid", "st_nlink", "st_size", "st_mtime_ns", "st_ctime_ns")
        if count != before.st_size or any(getattr(before, k) != getattr(after, k) or getattr(before, k) != getattr(named, k) for k in fields):
            raise ValueError
        return before, digest.hexdigest()
    finally:
        os.close(fd)


def policy_snapshot() -> dict:
    # Metadata-only refusal of LRA1 special objects precedes bounded JSON reads.
    # This is observation, never authority or a replacement lifecycle validator.
    from runtime.remote_access.linux_package import _package_json
    result = {}
    for role in ("connector", "tsnet-sidecar"):
        directory = Path("/var/lib/happyranch-" + role)
        try:
            m = directory.lstat()
        except FileNotFoundError:
            result[role] = None
            continue
        if not stat.S_ISDIR(m.st_mode) or m.st_mode & 0o022:
            raise ValueError
        doc = _package_json(regular(directory / "lifecycle.json", 16384), 16384)
        policy = doc["policy"]
        if (type(policy) is not dict or set(policy) != {"installation_id", "revision", "operation_id", "desired", "hard_block", "enabled"}
                or any(type(policy[k]) is not str or re.fullmatch(r"[0-9a-f]{32}", policy[k]) is None for k in ("installation_id", "operation_id"))
                or type(policy["revision"]) is not int or policy["revision"] <= 0
                or type(policy["enabled"]) is not bool
                or policy["desired"] not in ("run", "stopped", "retired")
                or policy["hard_block"] not in ("none", "io_failure", "invalid_state", "identity_loss", "transaction_unknown")):
            raise ValueError
        result[role] = policy
    return result


def images() -> dict:
    """Finite read-only metadata/hash snapshot. Never repairs or removes names.

    Fixed literal protected parents and regular-file checks precede content open.
    Bookends detect observed drift, not hostile-root races or unseen aliases.
    No safe in-operation boundary/timestamp reader exists in H; report that gap.
    """
    root = Path("/run/happyranch-connector-images")
    for parent in (Path("/"), Path("/run")):
        m = parent.lstat()
        if not stat.S_ISDIR(m.st_mode) or m.st_uid != 0 or m.st_mode & 0o022:
            raise ValueError
    try:
        base = root.lstat()
    except FileNotFoundError:
        return {"state": "absent", "images": [], "preparation_timing": "unavailable"}
    if not stat.S_ISDIR(base.st_mode) or base.st_uid != 0 or stat.S_IMODE(base.st_mode) != 0o700:
        raise ValueError
    deadline = time.monotonic() + 5
    entries = []
    with os.scandir(root) as stream:
        for entry in stream:
            entries.append(entry.name)
            if len(entries) > 33:
                raise ValueError
    boot = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
    result = []
    total_bytes = 0
    for name in sorted(entries):
        if time.monotonic() >= deadline:
            raise ValueError
        if name == ".allocation.lock":
            m = (root / name).lstat()
            if not stat.S_ISREG(m.st_mode) or m.st_uid != 0 or m.st_nlink != 1 or m.st_size != 0 or stat.S_IMODE(m.st_mode) != 0o600:
                raise ValueError
            continue
        if re.fullmatch(re.escape(boot) + r"\.[0-9a-f]{32}\.(admission|stopped-admission|body)", name) is None:
            raise ValueError
        directory = root / name
        d = directory.lstat()
        if not stat.S_ISDIR(d.st_mode) or d.st_uid != 0 or stat.S_IMODE(d.st_mode) not in (0o500, 0o700) or d.st_dev != base.st_dev:
            raise ValueError
        with os.scandir(directory) as stream:
            members = []
            for entry in stream:
                members.append(entry.name)
                if len(members) > 1:
                    raise ValueError
        if not members:
            result.append({"name": name, "state": "partial", "image": None})
            continue
        if members != ["image"]:
            raise ValueError
        path = directory / "image"
        m = path.lstat()
        if not stat.S_ISREG(m.st_mode) or m.st_uid != 0 or m.st_dev != base.st_dev or stat.S_IMODE(m.st_mode) not in (0o500, 0o600):
            raise ValueError
        m, digest = image_hash(path, deadline)
        total_bytes += m.st_size
        if total_bytes > 536870912:
            raise ValueError
        result.append({"name": name, "state": "ready" if stat.S_IMODE(d.st_mode) == 0o500 and stat.S_IMODE(m.st_mode) == 0o500 else "partial",
                       "image": {"device": m.st_dev, "inode": m.st_ino, "bytes": m.st_size, "sha256": digest}})
        after = directory.lstat()
        if (d.st_dev, d.st_ino, d.st_mtime_ns, d.st_ctime_ns) != (after.st_dev, after.st_ino, after.st_mtime_ns, after.st_ctime_ns):
            raise ValueError
    after_root = root.lstat()
    if (any(getattr(base, k) != getattr(after_root, k) for k in ("st_dev", "st_ino", "st_mode", "st_uid", "st_gid", "st_mtime_ns", "st_ctime_ns")) or time.monotonic() >= deadline):
        raise ValueError
    return {"state": "observed", "images": result, "preparation_timing": "unavailable"}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("help", "operation", "current", "images", "verify"))
    parser.add_argument("output", type=Path)
    parser.add_argument("operands", nargs="*")
    args = parser.parse_args()
    try:
        if args.mode == "images":
            snapshot = images()
            try:
                snapshot["policy"] = {"state": "observed", "records": policy_snapshot()}
            except (OSError, ValueError, KeyError, TypeError, RuntimeError):
                snapshot["policy"] = {"state": "unavailable", "records": None}
            save(args.output, snapshot)
            if args.operands:
                from runtime.remote_access.linux_package import _package_json
                prior = _package_json(regular(Path(args.operands[0]), 32768), 32768)
                original = prior["images"]
                if any(item not in snapshot["images"] for item in original):
                    raise ValueError
                if len(args.operands) >= 2 and args.operands[1] == "installed":
                    added = [item for item in snapshot["images"] if item not in original]
                    if (len(added) != 3 or any(item["state"] != "ready" for item in added)
                            or len({(item["image"]["device"], item["image"]["inode"]) for item in added}) != 3
                            or {item["name"].rsplit(".", 1)[1] for item in added} != {"admission", "stopped-admission", "body"}
                            or len({item["name"].rsplit(".", 1)[0] for item in added}) != 1
                            or len({(item["image"]["bytes"], item["image"]["sha256"]) for item in added}) != 1):
                        raise ValueError
                    receipt = _package_json(regular(Path(args.operands[2]), 4096), 4096)
                    if [added[0]["image"]["bytes"], added[0]["image"]["sha256"]] != receipt["identities"]["connector"]:
                        raise ValueError
            return 0
        if args.mode == "verify":
            verify_operation(Path(args.operands[0]), args.operands[1])
            save(args.output, {"operation": args.operands[1], "body_and_final_current": "observed_at_H", "preparation_timing": "unavailable"})
            return 0
        if args.mode == "help":
            from runtime.remote_access.linux_package import _read_verified
            files, _ = _read_verified(Path(args.operands[0]))
            home = Path(args.operands[1]) / "packaged-help"
            home.mkdir(mode=0o700)
            for name in ("home", "config", "data", "cache", "tmp", "cwd"):
                (home / name).mkdir(mode=0o700)
            binary = home / "happyranch-connector"
            with binary.open("xb") as stream:
                stream.write(files["bin/happyranch-connector"])
            binary.chmod(0o700)
            env = {"PATH": "/usr/bin:/bin", "LANG": "C", "LC_ALL": "C", "HOME": str(home / "home"),
                   "XDG_CONFIG_HOME": str(home / "config"), "XDG_DATA_HOME": str(home / "data"),
                   "XDG_CACHE_HOME": str(home / "cache"), "TMPDIR": str(home / "tmp")}
            record, _ = observe([str(binary), "--help"], seconds=10, env=env, cwd=home / "cwd")
            record["connector"] = [len(files["bin/happyranch-connector"]), hashlib.sha256(files["bin/happyranch-connector"]).hexdigest()]
            record["environment"] = {k: ("fresh" if k not in ("PATH", "LANG", "LC_ALL") else v) for k, v in env.items()}
        elif args.mode == "current":
            command = "from runtime.remote_access.linux_package import managed_lifecycle; import json; print(json.dumps(managed_lifecycle('status')))"
            record, raw = observe([sys.executable, "-c", command], seconds=50)
            if successful(record):
                from runtime.remote_access.linux_package import _package_json
                data = _package_json(raw, 1024)
                if (type(data) is not dict or set(data) != {"version", "state", "operation_id"}
                        or data["version"] != 2 or data["state"] != args.operands[0]
                        or re.fullmatch(r"[0-9a-f]{32}", data["operation_id"]) is None):
                    record["loss"] = "current_refused"
                else:
                    record["current"] = data
        else:
            # Original shipping call/receipt code is supplied on inherited stdin.
            record, _ = observe([sys.executable, "-", *args.operands], seconds=190, script_input=True)
        save(args.output, record)
        return 0 if successful(record) else record["exit"] if type(record["exit"]) is int and 0 < record["exit"] <= 255 else 1
    except (OSError, ValueError, KeyError, IndexError, TypeError, RuntimeError):
        print("n3-installer-only: observation refused; original receipts retained", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
