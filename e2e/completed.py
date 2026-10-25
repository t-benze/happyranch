"""Closed incremental completion observations; independent case proof stays separate."""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import secrets
import stat
from contextlib import contextmanager
from pathlib import Path


VARIANTS = tuple(json.loads(Path(__file__).with_name("manifest.json").read_text())["variants"])
LIMIT = 262144
IDENTITY = ("source_sha", "source_tree", "run_id", "run_attempt", "python", "exercise")
COUNTS = ("http", "commands", "starts", "durable", "evidence_fields")


def binding(row: dict) -> dict:
    value = {key: row[key] for key in IDENTITY}
    for key in ("source_sha", "source_tree"):
        if type(value[key]) is not str or re.fullmatch(r"[0-9a-f]{40}", value[key]) is None:
            raise ValueError("completed source identity refused")
    for key in ("run_id", "run_attempt"):
        if type(value[key]) is not str or re.fullmatch(r"[1-9][0-9]{0,19}", value[key]) is None:
            raise ValueError("completed run identity refused")
    if value["python"] not in ("3.12", "3.14") or value["exercise"] not in ("baseline", "cancellation"):
        raise ValueError("completed lane identity refused")
    return value


def encode(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("ascii")


def decode(data: bytes) -> object:
    if not 0 < len(data) <= LIMIT:
        raise ValueError("completed evidence size refused")
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate completed field")
            result[key] = value
        return result
    return json.loads(data.decode("ascii"), object_pairs_hook=pairs,
                      parse_constant=lambda _: (_ for _ in ()).throw(ValueError("nonfinite completed field")))


def closed(value: object, keys: tuple[str, ...]) -> None:
    if type(value) is not dict or set(value) != set(keys):
        raise ValueError("completed field grammar refused")


def validate(value: object, expected: dict, files: dict[str, bytes]) -> dict:
    closed(value, ("schema", "identity", "cases"))
    closed(value["identity"], IDENTITY)
    if type(value["schema"]) is not int or value["schema"] != 1 or binding(value["identity"]) != binding(expected):
        raise ValueError("completed identity mismatch")
    cases = value["cases"]
    if type(cases) is not list or len(cases) > 56:
        raise ValueError("completed case bound refused")
    seen = set()
    previous_at = -1
    for sequence, row in enumerate(cases, 1):
        closed(row, ("variant", "sequence", "manifest_index", "at", "reported_status", "evidence"))
        if (type(row["variant"]) is not str or row["variant"] not in VARIANTS or row["variant"] in seen
                or type(row["sequence"]) is not int or row["sequence"] != sequence
                or type(row["manifest_index"]) is not int or row["manifest_index"] != VARIANTS.index(row["variant"])
                or row["reported_status"] != "PASS"
                or type(row["at"]) not in (float, int) or not math.isfinite(row["at"])
                or not max(0, previous_at) <= row["at"] <= 9007199254740991):
            raise ValueError("completed case identity/order refused")
        seen.add(row["variant"])
        previous_at = row["at"]
        ref = row["evidence"]
        closed(ref, ("path", "sha256", "bytes"))
        name = f"case-{sequence:02d}.json"
        if (ref["path"] != name or type(ref["bytes"]) is not int or not 0 < ref["bytes"] <= 4096
                or type(ref["sha256"]) is not str or re.fullmatch(r"[0-9a-f]{64}", ref["sha256"]) is None):
            raise ValueError("completed evidence reference refused")
        data = files.get(name)
        if type(data) is not bytes or len(data) != ref["bytes"] or hashlib.sha256(data).hexdigest() != ref["sha256"]:
            raise ValueError("completed evidence unavailable")
        observation = decode(data)
        closed(observation, ("schema", "identity", "variant", "sequence", "manifest_index", "at", "counts"))
        if (type(observation["schema"]) is not int or observation["schema"] != 1
                or observation["identity"] != value["identity"]
                or any(type(observation[key]) is not type(row[key]) or observation[key] != row[key]
                       for key in ("variant", "sequence", "manifest_index", "at"))):
            raise ValueError("completed observation identity refused")
        closed(observation["identity"], IDENTITY)
        closed(observation["counts"], COUNTS)
        if any(type(number) is not int or not 0 <= number <= 1000000 for number in observation["counts"].values()):
            raise ValueError("completed observation count refused")
    if set(files) != {f"case-{i:02d}.json" for i in range(1, len(cases) + 1)}:
        raise ValueError("unexpected completed evidence")
    # This is accepted completion metadata, not independent product proof.
    return value


def join_results(value: dict, result: dict) -> None:
    """Join accepted completion observations to complete original results.

    This is only a consistency gate; callers must retain all content oracles.
    """
    if (binding(value["identity"]) != binding(result) or result.get("completed_cases") != value
            or len(value["cases"]) != 56 or set(result["variants"]) != set(VARIANTS)
            or [row["variant"] for row in value["cases"]] != list(result["variants"])):
        raise ValueError("complete case/result join unavailable")
    for row in value["cases"]:
        original = result["variants"][row["variant"]]
        if original["status"] != "PASS" or type(original["at"]) is not type(row["at"]) or original["at"] != row["at"]:
            raise ValueError("completed original result conflict")


@contextmanager
def directory(path: Path):
    """Pin literal no-follow ancestors, including the original final directory."""
    parts = str(path).split("/")[1:]
    if not path.is_absolute() or not parts or any(part in ("", ".", "..") for part in parts):
        raise ValueError("completed directory spelling refused")
    descriptors = [os.open("/", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)]
    links = []
    def verify():
        for parent, name, fd, device, inode in links:
            named, opened = os.stat(name, dir_fd=parent, follow_symlinks=False), os.fstat(fd)
            if (not stat.S_ISDIR(named.st_mode) or (named.st_dev, named.st_ino) != (device, inode)
                    or (opened.st_dev, opened.st_ino) != (device, inode)):
                raise ValueError("completed directory changed")
        info = os.fstat(descriptors[-1])
        if info.st_uid != os.geteuid() or info.st_mode & 0o022:
            raise ValueError("completed directory custody refused")
    try:
        for part in parts:
            parent = descriptors[-1]
            fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
            descriptors.append(fd)
            info = os.fstat(fd)
            links.append((parent, part, fd, info.st_dev, info.st_ino))
        verify()
        yield descriptors[-1], verify
        verify()
    finally:
        for fd in reversed(descriptors):
            os.close(fd)


def file_info(info: os.stat_result) -> None:
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != os.geteuid() or info.st_mode & 0o022:
        raise ValueError("unsafe completed evidence file")


def read_file(fd: int, name: str) -> bytes:
    descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
    try:
        before = os.fstat(descriptor)
        file_info(before)
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            data = stream.read(LIMIT + 1)
        after = os.fstat(descriptor)
        named = os.stat(name, dir_fd=fd, follow_symlinks=False)
        fields = ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns", "st_mode", "st_uid", "st_nlink")
        if (any(getattr(before, field) != getattr(after, field) for field in fields)
                or (named.st_dev, named.st_ino) != (before.st_dev, before.st_ino)
                or not 0 < len(data) <= LIMIT):
            raise ValueError("completed evidence changed/oversized")
        file_info(named)
        return data
    finally:
        os.close(descriptor)


def publish(path: Path, name: str, data: bytes, *, previous: bytes | None = None) -> None:
    if name != "completed-cases.json" and re.fullmatch(r"case-(0[1-9]|[1-4][0-9]|5[0-6])\.json", name) is None:
        raise ValueError("completed publication path refused")
    if not 0 < len(data) <= LIMIT:
        raise ValueError("completed publication size refused")
    with directory(path) as (fd, verify):
        def current():
            try:
                return read_file(fd, name)
            except FileNotFoundError:
                return None
        existing = current()
        if existing == data:
            return  # Exact immutable copy/replay only, never a new case result.
        if existing != previous:
            raise ValueError("completed publication conflict")
        temporary = ".completed-" + secrets.token_hex(16)
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=fd)
        try:
            with os.fdopen(descriptor, "wb", closefd=False) as stream:
                if stream.write(data) != len(data):
                    raise OSError("short completed publication")
                stream.flush()
                os.fsync(descriptor)
            verify()
            if current() != previous:
                raise ValueError("completed publication moved")
            os.replace(temporary, name, src_dir_fd=fd, dst_dir_fd=fd)
            os.fsync(fd)
        finally:
            os.close(descriptor)
            try:
                os.unlink(temporary, dir_fd=fd)
            except FileNotFoundError:
                pass


def read_bundle(path: Path, expected: dict) -> tuple[dict, dict[str, bytes]]:
    with directory(path) as (fd, verify):
        data = read_file(fd, "completed-cases.json")
        value = decode(data)
        # Validate reference names/shape before using ANY of them as paths.
        closed(value, ("schema", "identity", "cases"))
        if type(value["cases"]) is not list or len(value["cases"]) > 56:
            raise ValueError("completed case bound refused")
        files = {}
        for sequence, row in enumerate(value["cases"], 1):
            closed(row, ("variant", "sequence", "manifest_index", "at", "reported_status", "evidence"))
            closed(row["evidence"], ("path", "sha256", "bytes"))
            name = f"case-{sequence:02d}.json"
            if row["evidence"]["path"] != name:
                raise ValueError("completed evidence path refused")
            files[name] = read_file(fd, name)
        validate(value, expected, files)
        verify()
        if read_file(fd, "completed-cases.json") != data:
            raise ValueError("completed ledger moved")
        return value, dict(files, **{"completed-cases.json": data})
